# SPDX-License-Identifier: AGPL-3.0-or-later
"""research.py — unified research-tooling namespace for Research Vault.

When to use: ``rv research <subcommand>`` for Semantic Scholar search/find,
corpus dedup annotation, and paper ingestion.

Key constraints (re-implemented fresh from vault's research.py as behavioral spec):
  - ``default_project`` comes from config (``cfg._raw.get("default_project")``) —
    NEVER a compiled-in codename (zero hardcoded project names in this module).
  - All path resolution goes through Config — zero hardcoded paths.
  - Auth preflight uses the SecretStore Protocol (EnvSecretStore) — no macOS binaries.
  - Stdlib only for the module itself; asta and cite are called as subprocess tools.

Commands:
  rv research find <query>        search Semantic Scholar (over-fetch + rerank)
    --deep                        deep literature review (asta literature find)
    --limit N                     result count shown (default 10; rerank truncates to this)
    --pool N                      over-fetch size before rerank (default 50)
    --rerank / --no-rerank        TF-IDF rerank candidates (default on)
    --min-score FLOAT             minimum similarity threshold (default 0.0 = reorder-not-drop)
    --project NAME                match candidates against this project's corpus
  rv research add <doi|arxiv>     add a paper (dedup gate → cite add)
    --project NAME                target project/collection
    --force                       bypass dedup gate (logs loudly)
    --dry-run                     preview without writing
  rv research citekey <project> <note-id>
                                   compute + stamp the canonical authorYearWord
                                   citekey into a filed literature note
  rv research migrate-citekeys <project> [--dry-run]
                                   one-shot: stamp canonical citekeys into every
                                   absent/non-conformant literature note

  Verb consolidation HARD-REMOVED ``sweep``/``cited-by``/``references`` —
  they collapsed into the review-loop DAG's ``sweep``/``snowball`` tool
  node-ops (invoked IN-PROCESS by ``rv dag run``, never shelled directly).
  Use ``rv review <project> new <scope> ...`` + ``rv dag run`` instead of
  either verb; see cli_removed_verbs.py for the redirect stubs.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .config import Config, load_config
from .adapters.base import EnvSecretStore
from .sources.base import AdapterFetchError
from .sources.semantic_scholar import SemanticScholarAdapter
from .sources.identifiers import write_external_ids_to_note


# ---------------------------------------------------------------------------
# Paper-id normalization (F12 — arXiv/DOI scheme-prefix shim)
# ---------------------------------------------------------------------------

# Recognised scheme prefixes (checked case-insensitively)
_ASTA_SCHEME_PREFIXES: tuple[str, ...] = (
    "ARXIV:", "DOI:", "CORPUSID:", "MAG:", "PMID:", "URL:",
)
# 40-hex S2 corpus SHA
_S2_SHA_RE: re.Pattern = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)
# Bare new-style arXiv: NNNN.NNNN[N] (optional vN version suffix)
_ARXIV_NEW_RE: re.Pattern = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")
# Bare old-style arXiv: category/NNNNNNN  (category allows dots, e.g. cs.LG, hep-ph)
_ARXIV_OLD_RE: re.Pattern = re.compile(r"^[a-zA-Z][a-zA-Z0-9._-]*/\d{7}$")
# Bare DOI — publisher prefix starts with 10. followed by >=4 digits
_DOI_BARE_RE: re.Pattern = re.compile(r"^10\.\d{4,}/")


def _normalize_paper_id_for_asta(paper_id: str) -> str:
    """Normalize a bare paper_id to the scheme-prefixed form asta expects.

    Pass through unchanged if:
      - Already has a scheme prefix (ARXIV:/DOI:/CorpusId:/MAG:/PMID:/URL:,
        case-insensitive).
      - Is a 40-hex S2 corpus SHA.
      - Does not match any known bare pattern (pass through; do not guess).

    Add prefix for:
      - Bare new-style arXiv (NNNN.NNNNNvN?) → ``ARXIV:<id>``
      - Bare old-style arXiv (category/NNNNNNN) → ``ARXIV:<id>``
      - Bare DOI (10.NNNN/...) → ``DOI:<id>``

    Why: asta requires the scheme prefix for disambiguation; bare ids are common
    user input (e.g. copy-pasting from a browser URL).  This shim is the ONLY
    place normalization happens — callers always pass the raw user input here.
    """
    if not paper_id:
        return paper_id

    # Already scheme-prefixed (case-insensitive)
    upper = paper_id.upper()
    for prefix in _ASTA_SCHEME_PREFIXES:
        if upper.startswith(prefix):
            return paper_id

    # 40-hex S2 SHA — pass through as-is
    if _S2_SHA_RE.match(paper_id):
        return paper_id

    # Bare new-style arXiv (e.g. "2005.14165" or "1706.03762v5")
    if _ARXIV_NEW_RE.match(paper_id):
        return f"ARXIV:{paper_id}"

    # Bare old-style arXiv (e.g. "cs.LG/0604056" or "hep-ph/9901001")
    if _ARXIV_OLD_RE.match(paper_id):
        return f"ARXIV:{paper_id}"

    # Bare DOI (e.g. "10.18653/v1/N19-1423")
    if _DOI_BARE_RE.match(paper_id):
        return f"DOI:{paper_id}"

    # Unknown pattern — pass through unchanged; do not guess
    return paper_id


# ---------------------------------------------------------------------------
# Auth preflight (SecretStore Protocol — no macOS binaries)
# ---------------------------------------------------------------------------

def _preflight_zotero() -> None:
    """Check the Zotero API key is available. Exit with remediation if not."""
    store = EnvSecretStore()
    try:
        store.get("zotero-key")
    except KeyError:
        sys.exit(
            "Zotero key missing — cannot reach your Zotero library.\n"
            "  Fix: export ZOTERO_KEY=<your-key>  or  "
            "keyring.set_password('research-vault', 'zotero-key', '<key>')"
        )


def _preflight_asta() -> None:
    """Check asta access is configured.

    Two checks, in order:
    1. The asta API key is present (env ASTA_MCP_KEY or keyring "asta-mcp-key").
    2. The asta CLI binary is on PATH (so subprocess calls can reach it).

    Exit with remediation if either fails.
    """
    import shutil
    from .keys import ASTA_KEY, resolve_key

    present, _source, _masked = resolve_key(ASTA_KEY)
    if not present:
        sys.exit(
            "asta access not configured — cannot use asta research commands.\n"
            f"  Fix: export {ASTA_KEY.env_var}=<your-asta-api-key>\n"
            f"  Or store via rv onboard (runs `keyring set research-vault {ASTA_KEY.keyring_username}`).\n"
            f"  Request a key at: {ASTA_KEY.request_url}\n"
            "  (institutional email required; see allenai.org/asta/resources/mcp)"
        )

    if not shutil.which("asta"):
        sys.exit(
            "asta CLI not found on PATH — install asta per your project's instructions.\n"
            "  (Having the API key is not enough; the asta CLI must also be installed.)\n"
            "  See: allenai.org/asta/resources/mcp"
        )


# ---------------------------------------------------------------------------
# Project-scoped helpers (config-driven, never hardcoded codename)
# ---------------------------------------------------------------------------

def _collection_for_project(project: str | None, cfg: Config) -> str | None:
    """Return the Zotero collection name for a project from config."""
    if not project:
        return None
    try:
        proj_rec = cfg.project(project)
    except KeyError:
        return None
    return proj_rec.get("collection")


def _default_project(cfg: Config) -> str | None:
    """Return the default_project from config — never a compiled-in codename."""
    return cfg._raw.get("default_project")


# ---------------------------------------------------------------------------
# Corpus-dedup index helpers
# ---------------------------------------------------------------------------

def _normalize_doi(doi: str | None) -> str | None:
    """Return a lowercase DOI suitable for corpus matching, or None."""
    if not doi:
        return None
    return doi.strip().lower()


def _normalize_arxiv(arxiv: str | None) -> str | None:
    """Normalize an ArXiv id: strip 'arXiv:' prefix and version suffix.

    Examples:
      "arXiv:1706.03762"  → "1706.03762"
      "1810.04805v2"      → "1810.04805"
      None                → None
    """
    if not arxiv:
        return None
    s = re.sub(r"^arxiv:", "", arxiv.strip(), flags=re.IGNORECASE)
    s = re.sub(r"v\d+$", "", s)
    return s.lower() if s else None


def _arxiv_from_url(url: str | None) -> str | None:
    """Extract a normalized arXiv id from a `url:` frontmatter field.

    rv-refs-corpus-fix: real literature notes commonly carry ONLY a `url:`
    field pointing at the arXiv abstract page (e.g.
    ``https://arxiv.org/abs/2209.06899``) — never a separate `arxiv_id:`
    field.  Mirrors the URL-mining the sibling ``vault research`` tool's
    cite.py already relies on (``_arxiv_of``) for the same real-world shape.
    """
    if not url:
        return None
    m = re.search(r"arxiv\.org/abs/(\d{4}\.\d{4,5}(?:v\d+)?)", url, re.IGNORECASE)
    if m:
        return _normalize_arxiv(m.group(1))
    return None


def _doi_from_url(url: str | None) -> str | None:
    """Extract a normalized DOI from a `https://doi.org/10.xxxx/...` `url:` field."""
    if not url:
        return None
    m = re.search(r"doi\.org/(10\.\S+)", url, re.IGNORECASE)
    if m:
        return _normalize_doi(m.group(1).rstrip(".,;)"))
    return None


def _norm_title_str(s: str | None) -> str:
    """Normalize a title for fallback matching: lowercase, alnum-only."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _first_author_family(authors_field: str | None) -> str:
    """Extract the normalized family name of the first author from a
    ``"Family, Given; Family, Given; ..."`` frontmatter `authors:` string
    (the convention used by ``rv note new literature``)."""
    if not authors_field:
        return ""
    first = authors_field.split(";", 1)[0].strip()
    family = first.split(",", 1)[0].strip()
    return _norm_title_str(family)


def _note_citekey(fields: dict[str, Any], note_path: Path) -> str:
    """Return the note's canonical citekey: the `citekey:` frontmatter field
    (the operator's Better BibTeX scheme, e.g. ``argyleOutOneMany2022``) when
    present, falling back to the filename stem for notes filed without one.

    rv-023: a large majority of real project notes have a filename slug that
    differs from the note's own BBT `citekey:` — before this fix,
    `[IN-CORPUS:<x>]` always emitted the filename stem, never the BBT key a
    researcher actually cites.
    """
    ck = (fields.get("citekey") or "").strip()
    return ck if ck else note_path.stem


def _resolve_intrinsic_fields(
    literature_root: Path | None, overlay_fields: dict[str, Any],
) -> dict[str, Any]:
    """given a project's overlay ``fields`` (already parsed), resolve
    its ``central:`` pointer against ``literature_root`` and merge in the
    CENTRAL CORE's intrinsic fields (doi/arxiv_id/citekey/etc — core wins on
    any collision). A missing pointer / absent core / ``literature_root is
    None`` degrades to the overlay fields UNCHANGED (honest no-op, never a
    fabricated id) — this keeps every caller below correct for both a
    genuinely two-layer note AND a lone monolithic fixture that happens to
    carry its own doi/arxiv_id directly (some hermetic tests do this on
    purpose; not a violation, just a degrade path)."""
    if literature_root is None:
        return overlay_fields
    from .note import _extract_central_slug, _parse_frontmatter
    central = _extract_central_slug(str(overlay_fields.get("central") or ""))
    if not central:
        return overlay_fields
    core_path = Path(literature_root) / f"{central}.md"
    if not core_path.exists():
        return overlay_fields
    try:
        core_fields, _ = _parse_frontmatter(core_path.read_text(encoding="utf-8"))
    except OSError:
        return overlay_fields
    return {**overlay_fields, **core_fields}


def _load_notes_index(
    literature_dir: Path | None, literature_root: Path | None = None,
) -> dict[str, str]:
    """Build a normalized-id → citekey lookup by scanning literature/*.md frontmatter.

    Fix #32: literature notes filed via ``rv note new literature`` are invisible to the
    Zotero library.json-based corpus index.  This function builds a parallel lookup
    from the doi: and arxiv_id: frontmatter fields that literature notes now carry as
    optional placeholders.  The citekey is the note's filename stem.

    rv-refs-corpus-fix: also mines the `url:` field for an arXiv/DOI id when the
    dedicated `doi:`/`arxiv_id:` fields are absent — real notes overwhelmingly use
    only `url:` (see _arxiv_from_url / _doi_from_url).  Declared doi:/arxiv_id:
    fields always take priority when present.

    Returns an empty dict when literature_dir is None or does not exist.
    Only notes with a non-empty doi or arxiv_id (declared or url-derived) are indexed.
    """
    if literature_dir is None:
        return {}
    lit_path = Path(literature_dir)
    if not lit_path.exists():
        return {}

    # Local import to avoid circular dep (note imports config; research imports config)
    from .note import _parse_frontmatter

    index: dict[str, str] = {}
    for note_path in sorted(lit_path.glob("*.md")):
        try:
            text = note_path.read_text(encoding="utf-8")
        except OSError:
            continue
        overlay_fields, _ = _parse_frontmatter(text)
        # doi/arxiv_id are CORE-only intrinsic fields — resolve the
        # overlay's `central:` pointer against literature_root. Degrades to
        # the overlay's own fields unchanged when literature_root is None
        # or no pointer resolves (a monolithic fixture with its own doi:).
        fields = _resolve_intrinsic_fields(literature_root, overlay_fields)
        citekey = _note_citekey(fields, note_path)
        url = fields.get("url") or None

        doi = _normalize_doi(fields.get("doi") or None) or _doi_from_url(url)
        if doi:
            index[doi] = citekey

        arxiv = _normalize_arxiv(fields.get("arxiv_id") or None) or _arxiv_from_url(url)
        if arxiv:
            index[arxiv] = citekey

    return index


# ---------------------------------------------------------------------------
#  canonical citekey computation + stamping (Zotero-free path)
# ---------------------------------------------------------------------------

def _all_note_citekeys(literature_dir: Path, exclude: Path | None = None) -> set[str]:
    """Scan ``literature/*.md`` frontmatter for already-used ``citekey:``
    values — the review loop's (Zotero-free) existing-key universe, reused
    the same way ``_load_notes_index`` reuses a frontmatter scan for
    id-based dedup.

    ``exclude`` (typically the note currently being (re)computed) is left
    out — its own current value should never block recomputing itself. The
    sentinel is never counted as a real key (it isn't "taken").
    """
    from .cite import CITEKEY_SENTINEL
    from .note import _parse_frontmatter

    lit_path = Path(literature_dir)
    if not lit_path.exists():
        return set()

    keys: set[str] = set()
    for note_path in sorted(lit_path.glob("*.md")):
        if exclude is not None and note_path.resolve() == Path(exclude).resolve():
            continue
        try:
            text = note_path.read_text(encoding="utf-8")
        except OSError:
            continue
        fields, _ = _parse_frontmatter(text)
        ck = (fields.get("citekey") or "").strip()
        if ck and ck != CITEKEY_SENTINEL:
            keys.add(ck)
    return keys


def compute_and_stamp_citekey(note_path: Path, literature_dir: Path) -> str:
    """Compute the canonical citekey for *note_path* from its OWN filed
    frontmatter (``title``/``authors``/``year``) and stamp it into
    ``citekey:``.

    This is the review-loop hook: the relate-<key> subagent calls
    ``rv research citekey <project> <id>`` once it has filled in title/
    authors/year (per the 5-move reading protocol) — the canonical
    familyShorttitleYear key is computed here rather than left to the
    agent to invent. Filename stays whatever id the note was filed under;
    only the ``citekey:`` FIELD becomes the convention.

    Fail-closed: if title or year is unresolved (blank/absent),
    NEVER guess — stamp ``cite.CITEKEY_SENTINEL`` instead, loudly. A missing
    note is a caller error (raises FileNotFoundError — there is nothing to
    read metadata from).

    Returns the computed citekey (or the sentinel).
    """
    from .cite import CITEKEY_SENTINEL, make_citekey
    from .note import _parse_frontmatter

    note_path = Path(note_path)
    if not note_path.is_file():
        raise FileNotFoundError(f"literature note not found: {note_path}")

    text = note_path.read_text(encoding="utf-8")
    fields, _ = _parse_frontmatter(text)

    title = (fields.get("title") or "").strip()
    year = (fields.get("year") or "").strip()
    family = _first_author_family(fields.get("authors"))

    if title and year:
        existing = _all_note_citekeys(literature_dir, exclude=note_path)
        citekey = make_citekey(family or None, title, year, existing)
    else:
        citekey = CITEKEY_SENTINEL

    from .sources.identifiers import stamp_note_frontmatter
    stamp_note_frontmatter(note_path, {"citekey": citekey})
    return citekey


def _title_fallback_match(title_norm: str, note_title: str) -> bool:
    """Conservative title-fallback match for `_corpus_annotation` tier 3.

    rv-refs-corpus-fix review tightening: the original prefix/either-contains
    heuristic over-matched on real, distinct papers — a reviewer reproduced
    three cases empirically: title-superset (one title a strict prefix of a
    longer, different paper's title), a series prefix ("Part I" vs "Part II"
    sharing everything up to that suffix), and two different authors sharing
    a surname with a generic shared title fragment.  The fix: require EITHER
    exact equality, OR containment gated by a length ratio
    ``min(len)/max(len) >= 0.9`` — so a short title can't be a false substring
    match against an unrelated, much longer title.  The legitimate Aher catch
    (identical titles, ratio 1.0) survives; the three over-match repros
    (ratios 0.50/0.83/0.76) are correctly rejected.
    """
    if not title_norm or not note_title:
        return False
    if title_norm == note_title:
        return True
    if title_norm in note_title or note_title in title_norm:
        shorter = min(len(title_norm), len(note_title))
        longer = max(len(title_norm), len(note_title))
        return (shorter / longer) >= 0.9
    return False


def _load_notes_title_index(
    literature_dir: Path | None, literature_root: Path | None = None,
) -> dict[str, list[tuple[str, str]]]:
    """Build a first-author-family → [(citekey, normalized_title)] fallback lookup.

    rv-refs-corpus-fix: a small fraction of real notes carry NO extractable id
    anywhere (e.g. a conference-proceedings `url:` with no DOI/arXiv pattern —
    the "Aher 2022" case).  For those, the only remaining corpus signal is the
    note's own title + first author.  This tier is deliberately year-agnostic:
    a paper's canonical S2 year commonly differs from a note's recorded venue
    year (arXiv preprint year vs. eventual conference/journal year), so gating
    on year here would just reintroduce the same under-detection this fix
    exists to close.  Conservative safety valves: (a) only titles that
    normalize to >= 20 alnum characters are indexed, so a short/generic title
    can't produce a cheap surname-collision false-positive; (b) review
    tightening — this index is SCOPED to notes with NO extractable id
    (declared doi:/arxiv_id: or url-derived) at all.  A note that already has
    an id is fully served by `_load_notes_index` (tier 2) — including it here
    too would only widen the over-match surface for id-carrying notes without
    adding any real detection power.

    Returns an empty dict when literature_dir is None or does not exist.
    """
    if literature_dir is None:
        return {}
    lit_path = Path(literature_dir)
    if not lit_path.exists():
        return {}

    from .note import _parse_frontmatter

    index: dict[str, list[tuple[str, str]]] = {}
    for note_path in sorted(lit_path.glob("*.md")):
        try:
            text = note_path.read_text(encoding="utf-8")
        except OSError:
            continue
        overlay_fields, _ = _parse_frontmatter(text)
        fields = _resolve_intrinsic_fields(literature_root, overlay_fields)
        citekey = _note_citekey(fields, note_path)
        url = fields.get("url") or None

        # Skip notes that already carry an extractable id — tier 2 handles them.
        doi = _normalize_doi(fields.get("doi") or None) or _doi_from_url(url)
        arxiv = _normalize_arxiv(fields.get("arxiv_id") or None) or _arxiv_from_url(url)
        if doi or arxiv:
            continue

        fam = _first_author_family(fields.get("authors"))
        title_norm = _norm_title_str(fields.get("title"))
        if fam and len(title_norm) >= 20:
            index.setdefault(fam, []).append((citekey, title_norm))

    return index


def _corpus_annotation(
    paper: dict,
    *,
    notes_index: dict[str, str] | None = None,
    notes_title_index: dict[str, list[tuple[str, str]]] | None = None,
) -> str:
    """Return [IN-CORPUS:<citekey>] or [NEW] for a candidate S2 paper dict.

    Checks sources in order:
      1. notes_index        — built from literature/*.md doi/arxiv_id
                               frontmatter, declared OR url-derived (Fix #32 +
                               rv-refs-corpus-fix: filed notes count as
                               in-corpus even before a note carries an id,
                               and even when the note only carries a `url:`
                               field).  Emits the note's own `citekey:`
                               frontmatter (the operator's Better BibTeX
                               scheme) when present, falling back to the
                               filename stem (rv-023).
      2. notes_title_index  — first-author-family + long-title fallback for
                               notes with NO extractable id anywhere
                               (rv-refs-corpus-fix).  Year-agnostic by design
                               (canonical S2 year vs. a note's own venue year
                               commonly differ) — see _load_notes_title_index
                               for the conservative-title-length rationale.

    rv-023: the third tier — a Zotero ``library.json`` corpus index — was
    removed as structurally dead: nothing wired a `refs =` path into real
    project config, and even when present the parser expected the raw
    Zotero-API item shape (`item["data"][...]`), never the flat CSL-JSON a
    real `library.json` actually contains.  The notes-index tier below
    already covers every real project via literature/*.md frontmatter.

    Returns [NEW] only if the paper matches none of the sources.
    """
    ext = paper.get("externalIds") or {}

    doi = _normalize_doi(ext.get("DOI"))
    arxiv = _normalize_arxiv(ext.get("ArXiv"))

    # 1. Check literature/ OKF dir index (Fix #32)
    ni = notes_index or {}
    if ni:
        if doi and doi in ni:
            return f"[IN-CORPUS:{ni[doi]}]"
        if arxiv and arxiv in ni:
            return f"[IN-CORPUS:{ni[arxiv]}]"

    # 2. Title + first-author fallback (rv-refs-corpus-fix) — only reached when
    #    no id matched above.
    nti = notes_title_index or {}
    if nti:
        fam = _normalize_author_name(paper.get("authors")).lower()
        title_norm = _norm_title_str(paper.get("title"))
        if fam and len(title_norm) >= 20:
            for ck, note_title in nti.get(fam, []):
                if _title_fallback_match(title_norm, note_title):
                    return f"[IN-CORPUS:{ck}]"

    return "[NEW]"


# ---------------------------------------------------------------------------
# S2 → cite matching adapter (reuses cite.py internals via import)
# ---------------------------------------------------------------------------

def _normalize_author_name(authors_raw: Any) -> str:
    """Extract the first author's family name from any S2 authors shape."""
    if not authors_raw:
        return ""
    if isinstance(authors_raw, str):
        first = authors_raw.split(",", 1)[0].strip()
        return first.rsplit(" ", 1)[-1] if first else ""
    if isinstance(authors_raw, list):
        if not authors_raw:
            return ""
        first = authors_raw[0]
        if isinstance(first, dict):
            name = first.get("name", "").strip()
        elif isinstance(first, str):
            name = first.strip()
        else:
            return ""
        return name.rsplit(" ", 1)[-1] if name else ""
    return ""


def _print_candidates(
    papers: list[dict],
    *,
    notes_index: dict[str, str] | None = None,
    notes_title_index: dict[str, list[tuple[str, str]]] | None = None,
) -> None:
    """Print S2 paper candidates in a human-readable table.

    When notes_index (loaded from the project's literature/ OKF dir — Fix #32,
    extended by rv-refs-corpus-fix to mine `url:`) and/or notes_title_index
    (title+author fallback — rv-refs-corpus-fix) is provided, each candidate is
    annotated [IN-CORPUS:<citekey>] or [NEW] so the lit-review citation-
    neighbor walk can detect when a hop adds no new papers.
    """
    print(f"\n{len(papers)} candidate(s)\n")
    for p in papers:
        year = p.get("year", "")
        title = (p.get("title") or "")[:65]
        first_author = _normalize_author_name(p.get("authors"))
        ext = p.get("externalIds") or {}
        arxiv = ext.get("ArXiv", "")
        doi = ext.get("DOI", "")
        id_str = f"arXiv:{arxiv}" if arxiv else (f"DOI:{doi}" if doi else "")
        annotation = _corpus_annotation(
            p, notes_index=notes_index, notes_title_index=notes_title_index,
        )
        print(f"  {annotation}  {first_author} {year}  {title}")
        if id_str:
            print(f"  {'':12}  {id_str}")
    print()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_find(args: argparse.Namespace) -> int:
    """find: search Semantic Scholar (asta papers search or --deep).

    Normal path (not --deep): over-fetches ``--pool`` candidates from asta, then
    reranks by TF-IDF relevance to the query and shows the top ``--limit`` results.
    This surfaces anchors that asta's recency/citation ordering buries past the
    first page.  Use ``--no-rerank`` to reproduce the legacy asta-order output
    (fetches exactly ``--limit`` candidates, no reranking).

    ``--deep`` path is unchanged in v1 (asta literature find; no rerank applied).
    """
    _preflight_asta()
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv research find: config error: {e}", file=sys.stderr)
        return 1

    project = getattr(args, "project", None) or _default_project(cfg)
    fields = "title,year,authors,externalIds,abstract,citationCount"

    # --rerank flag (default True); pool size for over-fetch; min_score threshold
    do_rerank = getattr(args, "rerank", True)
    pool = getattr(args, "pool", 50)
    min_score = getattr(args, "min_score", 0.0)

    if getattr(args, "deep", False):
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.close()
        r = subprocess.run(["asta", "literature", "find", args.query, "-o", tmp.name])
        if r.returncode != 0:
            sys.exit(f"asta literature find failed (exit {r.returncode})")
        try:
            data = json.load(open(tmp.name, encoding="utf-8"))
            papers = data if isinstance(data, list) else (
                data.get("papers") or data.get("results") or data.get("data") or []
            )
        except Exception as e:
            sys.exit(f"failed to parse asta literature find output: {e}")
        # --deep: no rerank in v1 (asta literature find manages its own ordering)
        do_rerank = False
    else:
        # Over-fetch: request pool candidates (or just limit when --no-rerank)
        fetch_n = pool if do_rerank else args.limit
        # NG-1: pure refactor — the S2 search subprocess call now lives in
        # SemanticScholarAdapter (research_vault.sources); PaperHit.raw carries
        # the original S2 dict so this pipeline is byte-identical downstream.
        hits = SemanticScholarAdapter().search(args.query, limit=fetch_n, fields=fields)
        papers = [h.raw for h in hits]

    if do_rerank and papers:
        # Build body for each paper: title + abstract (tolerate missing abstract)
        for p in papers:
            title = p.get("title") or ""
            abstract = p.get("abstract") or ""
            p["body"] = title + ("\n" + abstract if abstract else "")
        from .cross_project import rank_candidates  # in-place import (no new cycle)
        papers = rank_candidates(
            args.query, papers, min_score=min_score, top_k=args.limit
        )

    # Fix #32: check filed literature notes for corpus dedup (zero-infra)
    lit_dir = (
        cfg.project_notes_dir(project) / "literature"
        if project else None
    )
    notes_index = _load_notes_index(lit_dir, literature_root=(cfg.literature_root if cfg else None))
    notes_title_index = _load_notes_title_index(lit_dir, literature_root=(cfg.literature_root if cfg else None))
    _print_candidates(
        papers, notes_index=notes_index, notes_title_index=notes_title_index,
    )
    return 0


def cmd_cited_by(args: argparse.Namespace) -> int:
    """cited-by: forward snowball — papers that cite this paper.

    Use this to discover who has cited the seed paper after it was published.
    See also: rv research references (backward snowball — what the seed itself cites).
    """
    _preflight_asta()
    try:
        cfg = load_config()
    except Exception:
        cfg = None
    project = getattr(args, "project", None)
    if cfg and not project:
        project = _default_project(cfg)

    # normalize bare arXiv/DOI ids to the scheme-prefixed form asta expects
    paper_id = _normalize_paper_id_for_asta(args.paper_id)

    # NG-1: pure refactor — the S2 citations subprocess call now lives in
    # SemanticScholarAdapter; PaperHit.raw carries the original S2 dict.
    # AdapterFetchError is a catchable Exception (not sys.exit) so the
    # multi-round snowball walk can degrade gracefully on one bad seed
    # (sources/snowball.py); this single-lookup CLI still fails fast.
    try:
        hits = SemanticScholarAdapter().cited_by(paper_id, limit=args.limit)
    except AdapterFetchError as e:
        sys.exit(str(e))
    papers = [h.raw for h in hits]

    # zero-result hint when the id was normalized (bare input may still be wrong)
    if not papers and paper_id != args.paper_id:
        print(
            f"rv research cited-by: 0 results — bare id was normalized to {paper_id!r}. "
            f"Did you mean ARXIV:{args.paper_id}?  "
            f"Check the paper id format if this is unexpected.",
            file=sys.stderr,
        )

    # Fix #32: check filed literature notes for corpus dedup
    lit_dir = (
        cfg.project_notes_dir(project) / "literature"
        if (cfg and project) else None
    )
    notes_index = _load_notes_index(lit_dir, literature_root=(cfg.literature_root if cfg else None))
    notes_title_index = _load_notes_title_index(lit_dir, literature_root=(cfg.literature_root if cfg else None))
    _print_candidates(
        papers, notes_index=notes_index, notes_title_index=notes_title_index,
    )
    return 0


def cmd_references(args: argparse.Namespace) -> int:
    """references: backward snowball — papers in this paper's own reference list.

    Use this to discover what the seed paper itself cites (backward citation chase).
    See also: rv research cited-by (forward snowball — who cites the seed paper).

    Anti-pattern: do NOT hand-copy a bibliography — use this to fetch it programmatically.
    """
    _preflight_asta()
    try:
        cfg = load_config()
    except Exception:
        cfg = None
    project = getattr(args, "project", None)
    if cfg and not project:
        project = _default_project(cfg)

    # normalize bare arXiv/DOI ids to the scheme-prefixed form asta expects
    paper_id = _normalize_paper_id_for_asta(args.paper_id)

    # NG-1: pure refactor — the S2 references subprocess call now lives in
    # SemanticScholarAdapter; PaperHit.raw carries the original S2 dict.
    # AdapterFetchError is a catchable Exception (not sys.exit) so the
    # multi-round snowball walk can degrade gracefully on one bad seed
    # (sources/snowball.py); this single-lookup CLI still fails fast.
    try:
        hits = SemanticScholarAdapter().references(paper_id)
    except AdapterFetchError as e:
        sys.exit(str(e))
    papers = [h.raw for h in hits]

    # zero-result hint when the id was normalized (bare input may still be wrong)
    if not papers and paper_id != args.paper_id:
        print(
            f"rv research references: 0 results — bare id was normalized to {paper_id!r}. "
            f"Did you mean ARXIV:{args.paper_id}?  "
            f"Check the paper id format if this is unexpected.",
            file=sys.stderr,
        )

    # Fix #32: check filed literature notes for corpus dedup
    lit_dir = (
        cfg.project_notes_dir(project) / "literature"
        if (cfg and project) else None
    )
    notes_index = _load_notes_index(lit_dir, literature_root=(cfg.literature_root if cfg else None))
    notes_title_index = _load_notes_title_index(lit_dir, literature_root=(cfg.literature_root if cfg else None))
    _print_candidates(
        papers, notes_index=notes_index, notes_title_index=notes_title_index,
    )
    return 0


def cmd_corroborate(args: argparse.Namespace) -> int:
    """corroborate: search DECLARED peer projects' OKF notes for evidence matching a claim.

    Corroboration is gated to hub-declared cross-project edges.  The search
    universe is ``peers_of(from_slug)`` — NOT all registered projects.

    ``--from`` is REQUIRED.  If the originating project has no declared peers, a
    discovery nudge is printed and 0 is returned (not an error — just no declared
    reach yet).

    Results are ranked by TF-IDF cosine similarity (Jaccard fallback if sklearn
    is absent).  Use ``--emit <path>`` to write a candidates JSON for the judge node.

    Anti-pattern: do NOT use ``rv research corroborate`` across undeclared projects —
    declare an edge first via ``rv project relate <from> <peer> --kind <why>``.
    """
    import json as _json
    from .cross_project import corroborate_across_projects

    from_slug = getattr(args, "from_project", None)
    if not from_slug:
        print(
            "rv research corroborate: --from <project> is REQUIRED (D3).\n"
            "  Example: rv research corroborate \"<claim>\" --from <your-project>",
            file=sys.stderr,
        )
        return 1

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv research corroborate: config error: {e}", file=sys.stderr)
        return 1

    # Validate from_slug exists
    try:
        cfg.project(from_slug)
    except KeyError as e:
        print(f"rv research corroborate: {e}", file=sys.stderr)
        return 1

    against_slugs = getattr(args, "against_projects", None)
    min_score = getattr(args, "min_score", 0.05)
    top_k = getattr(args, "top_k", 10)
    emit_path = getattr(args, "emit", None)

    # Check declared peers before the call — print nudge if none
    from .project_edges import peers_of as _peers_of
    declared_peers = _peers_of(cfg, from_slug)
    if not declared_peers and against_slugs is None:
        print(
            f"rv research corroborate: no declared edges for {from_slug!r}.\n"
            f"  The hub can declare one:  rv project relate {from_slug} <peer> --kind <why>\n"
            f"  Then re-run:  rv research corroborate \"{args.claim}\" --from {from_slug}"
        )
        return 0

    try:
        hits = corroborate_across_projects(
            claim=args.claim,
            cfg=cfg,
            from_slug=from_slug,
            against_slugs=against_slugs,
            min_score=min_score,
            top_k=top_k,
        )
    except ValueError as e:
        print(f"rv research corroborate: {e}", file=sys.stderr)
        return 1

    if not hits:
        print(f"No corroboration found for: {args.claim!r}")
        return 0

    print(f"{len(hits)} corroborating note(s) for: {args.claim!r}\n")
    for hit in hits:
        score_str = f"  score={hit.get('score', 0):.3f}" if "score" in hit else ""
        print(f"  {hit['provenance']}{score_str}")
        if hit.get("excerpt"):
            print(f"    excerpt: {hit['excerpt']}")
    print()

    # --emit: write candidates JSON for the judge node (Slice 5)
    if emit_path:
        candidates_obj = {
            "claim": args.claim,
            "from": from_slug,
            "candidates": [
                {
                    "provenance": h["provenance"],
                    "project": h["project"],
                    "note_rel": h["note_rel"],
                    "anchor": h.get("anchor", ""),
                    "excerpt": h.get("excerpt", ""),
                    "score": round(h.get("score", 0.0), 4),
                    "ranker": h.get("ranker", "unknown"),
                }
                for h in hits
            ],
        }
        out_path = Path(emit_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(_json.dumps(candidates_obj, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"candidates written → {emit_path}")

    return 0


_ADDED_CITEKEY_RE = re.compile(r"^Added:\s*(\S+)", re.MULTILINE)


def _resolve_full_external_ids(ident: str) -> dict[str, str]:
    """Resolve the full normalized external-id set for *ident* (a doi or
    arXiv id, as ``rv research add``/``rv cite add`` accept) at identifier-
    persistence write time.

    Starts from the identifier itself (``cite._resolve_ident`` — stdlib
    regex, no network — reused rather than re-implemented), then best-effort
    enriches via ``SemanticScholarAdapter.get`` (s2 corpus id, PMID, MAG —
    whatever S2 resolved for this doi/arXiv id). The S2 lookup degrades
    gracefully (returns None) on any failure — this is optional enrichment,
    never a reason to fail the add.
    """
    from .cite import _resolve_ident

    r = _resolve_ident(ident)
    if not r:
        return {}
    kind, ident_val = r
    external_ids: dict[str, str] = {kind: ident_val}

    try:
        paper_id = _normalize_paper_id_for_asta(ident_val)
        hit = SemanticScholarAdapter().get(paper_id)
    except Exception:
        hit = None
    if hit is not None:
        # The identifier we resolved from `ident` itself is authoritative
        # (it's exactly what the user/caller supplied) — S2's enrichment
        # only FILLS IN keys we don't already have, never overrides.
        for k, v in hit.external_ids.items():
            external_ids.setdefault(k, v)

    return external_ids


def cmd_add(args: argparse.Namespace) -> int:
    """add: dedup preflight → cite add → cite link → identifier persistence.

    Identifier-persistence (write path): after a successful (non-dry-run)
    ``cite add``, resolves the full normalized external-id set for *ident*
    (doi/arxiv/pmcid/openalex/pmid/s2 — whichever are resolvable) and stamps
    the present ones into the project's literature note frontmatter, IF that
    note is already filed (``rv note new <project> literature <citekey>``).
    If the note isn't filed yet, this is a no-op with a clear pointer — the
    same "note not filed yet" contract ``fulltext.py``'s stamp already uses
    — never an error (surface, don't silently drop, but also
    don't invent note-filing as a side effect of ``add``).
    """
    _preflight_asta()
    _preflight_zotero()

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv research add: config error: {e}", file=sys.stderr)
        return 1

    project = getattr(args, "project", None) or _default_project(cfg)
    collection = _collection_for_project(project, cfg) if project else None
    force = getattr(args, "force", False)
    dry_run = getattr(args, "dry_run", False)

    # Build cite add command
    cite_cmd = ["rv", "cite", "add", args.ident]
    if dry_run:
        cite_cmd.append("--dry-run")
    if collection:
        cite_cmd += ["--collection", collection]

    r = subprocess.run(cite_cmd, capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    if r.stderr:
        sys.stderr.write(r.stderr)
    if r.returncode != 0:
        sys.exit(f"rv cite add failed (exit {r.returncode})")

    if dry_run or not project:
        return 0

    m = _ADDED_CITEKEY_RE.search(r.stdout)
    if not m:
        # `cite add` succeeded (returncode 0) but we couldn't parse the
        # citekey from its output — surface, don't silently skip persistence.
        print(
            "rv research add: could not parse citekey from `rv cite add` output — "
            "skipping identifier persistence.",
            file=sys.stderr,
        )
        return 0
    citekey = m.group(1)

    external_ids = _resolve_full_external_ids(args.ident)
    # the external-id set is intrinsic (core-only) — stamp the
    # CENTRAL CORE, not the per-project overlay.
    note_path = cfg.literature_root / f"{citekey}.md"
    if not external_ids:
        print(
            f"rv research add: no external ids resolved from {args.ident!r} — "
            "nothing to persist.",
            file=sys.stderr,
        )
    elif write_external_ids_to_note(note_path, external_ids):
        print(f"Stamped identifiers ({', '.join(sorted(external_ids))}) into {note_path}")
    elif not note_path.is_file():
        print(
            f"Note {note_path} does not exist yet — nothing was persisted. "
            f"File it with `rv note new {project} literature {citekey}` and "
            f"re-run `rv research add {args.ident}` to re-resolve and stamp "
            "the identifiers.",
        )
    return 0


_CITEKEY_MIGRATION_LEDGER_NAME = "_citekey_migration_ledger.json"


def cmd_citekey(args: argparse.Namespace) -> int:
    """rv research citekey <project> <note-id>:.

    Compute + stamp the canonical familyShorttitleYear citekey into a
    filed literature note, from that note's OWN title/authors/year
    frontmatter. The relate-<key> subagent calls this once those fields are
    filled in (per the 5-move reading protocol, review/style.py) — the
    filename may stay whatever id the note was created under; only the
    `citekey:` FIELD becomes the convention.

    Fail-closed: unresolved title/year -> the visible cite.CITEKEY_SENTINEL
    is stamped (never a guess), and this command exits 1 so the caller can't
    silently treat it as done.
    """
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv research citekey: config error: {e}", file=sys.stderr)
        return 1

    project = args.project
    try:
        cfg.project_notes_dir(project)  # validates the project slug exists
    except KeyError as e:
        print(f"rv research citekey: {e}", file=sys.stderr)
        return 1

    # citekey/title/authors/year are intrinsic (core-only) — the
    # note-id shares its slug with the central core (note._cmd_new_two_layer's
    # convention), so this resolves + stamps the CORE, not the overlay.
    note_path = cfg.literature_root / f"{args.note_id}.md"
    try:
        citekey = compute_and_stamp_citekey(note_path, cfg.literature_root)
    except FileNotFoundError as e:
        print(f"rv research citekey: {e}", file=sys.stderr)
        return 1

    from .cite import CITEKEY_SENTINEL
    if citekey == CITEKEY_SENTINEL:
        print(
            f"rv research citekey: {note_path} has no title/year yet — stamped "
            f"the {CITEKEY_SENTINEL} sentinel (NOT a guess). Fill in title/"
            f"authors/year and re-run.",
            file=sys.stderr,
        )
        return 1

    print(f"Stamped citekey: {citekey} into {note_path}")
    return 0


def migrate_citekeys(
    literature_dir: Path,
    ledger_path: Path,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """one-shot maintenance pass — stamp a canonical citekey into
    every literature note whose ``citekey:`` is absent or non-conformant.

    NEVER renames files (a rename would break ``reads:``/edge pointers into
    ``literature/<stem>.md`` elsewhere in the corpus) — only the ``citekey:``
    FIELD is rewritten. Every rewritten note gets an old->new entry appended
    to *ledger_path* (a JSON list, append-only across repeated runs) so any
    prose/citation that referenced the old key can be traced forward.

    ``dry_run=True`` computes the full plan without touching any file or the
    ledger — the caller can print it for review first.

    Within-batch collision safety: notes already conformant seed the
    existing-key set up front; each newly-computed key is added to that same
    set as it's assigned, so two notes migrated in the same run never
    collide with each other.

    Returns ``{"changed": [{"note","old","new"}...], "unresolved": [name...],
    "already_conformant": N}``.
    """
    from .cite import CITEKEY_RE, CITEKEY_SENTINEL, make_citekey
    from .note import _parse_frontmatter
    from .sources.identifiers import stamp_note_frontmatter
    import datetime

    lit_path = Path(literature_dir)
    changed: list[dict[str, str]] = []
    unresolved: list[str] = []
    already_conformant = 0

    if not lit_path.exists():
        return {"changed": changed, "unresolved": unresolved, "already_conformant": 0}

    notes = sorted(lit_path.glob("*.md"))
    parsed: list[tuple[Path, dict[str, Any]]] = []
    existing: set[str] = set()
    for note_path in notes:
        try:
            text = note_path.read_text(encoding="utf-8")
        except OSError:
            continue
        fields, _ = _parse_frontmatter(text)
        parsed.append((note_path, fields))
        ck = (fields.get("citekey") or "").strip()
        if ck and ck != CITEKEY_SENTINEL and CITEKEY_RE.match(ck):
            existing.add(ck)

    for note_path, fields in parsed:
        old_ck = (fields.get("citekey") or "").strip()
        if old_ck and old_ck != CITEKEY_SENTINEL and CITEKEY_RE.match(old_ck):
            already_conformant += 1
            continue  # already conformant — nothing to migrate

        title = (fields.get("title") or "").strip()
        year = (fields.get("year") or "").strip()
        family = _first_author_family(fields.get("authors"))

        if title and year:
            new_ck = make_citekey(family or None, title, year, existing)
            existing.add(new_ck)
        else:
            new_ck = CITEKEY_SENTINEL
            unresolved.append(note_path.name)

        changed.append({
            "note": note_path.name,
            "old": old_ck or note_path.stem,
            "new": new_ck,
        })

        if not dry_run:
            stamp_note_frontmatter(note_path, {"citekey": new_ck})

    if changed and not dry_run:
        ledger_path = Path(ledger_path)
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        entries: list[Any] = []
        if ledger_path.is_file():
            try:
                loaded = json.loads(ledger_path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    entries = loaded
            except (OSError, json.JSONDecodeError):
                entries = []
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        for c in changed:
            entries.append({**c, "migrated_at": ts})
        ledger_path.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    return {
        "changed": changed,
        "unresolved": unresolved,
        "already_conformant": already_conformant,
    }


def cmd_migrate_citekeys(args: argparse.Namespace) -> int:
    """rv research migrate-citekeys <project> [--dry-run]:.

    ``citekey:`` is intrinsic (core-only) content — this migrates
    notes in the CENTRAL STORE (``cfg.literature_root``), not a per-project
    overlay dir. Since the store is shared across every registered project,
    this is now effectively a corpus-wide operation regardless of which
    ``<project>`` is passed (kept for CLI-shape stability / the migration
    ledger's project-scoped audit trail — see ``ledger_path`` below); the
    project argument no longer narrows WHICH notes get migrated.

    This release, run this by hand only against the ONE
    project whose corpus needs migrating (no code-level project restriction
    here — this verb is general; the *rollout* is scoped by the operator
    invoking it against a single project at a time, not by this module).
    """
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv research migrate-citekeys: config error: {e}", file=sys.stderr)
        return 1

    project = args.project
    try:
        lit_dir = cfg.project_notes_dir(project) / "literature"
    except KeyError as e:
        print(f"rv research migrate-citekeys: {e}", file=sys.stderr)
        return 1

    # The migration ledger stays a PROJECT-level bookkeeping JSON (not an
    # OKF note) — unaffected by the two-layer split (see
    # review.ledger._citekey_migrated_count, which reads it from the same
    # project-scoped location).
    ledger_path = lit_dir / _CITEKEY_MIGRATION_LEDGER_NAME
    result = migrate_citekeys(cfg.literature_root, ledger_path, dry_run=args.dry_run)

    changed = result["changed"]
    unresolved = result["unresolved"]
    print(
        f"{result['already_conformant']} note(s) already conformant; "
        f"{len(changed)} note(s) {'would be ' if args.dry_run else ''}migrated; "
        f"{len(unresolved)} unresolved (missing title/year — stamped the "
        f"unresolvable sentinel, never a guess)."
    )
    for c in changed:
        print(f"  {c['note']}: {c['old']!r} -> {c['new']!r}")
    if unresolved:
        print("UNRESOLVED (needs manual title/authors/year, then re-run):", file=sys.stderr)
        for name in unresolved:
            print(f"  {name}", file=sys.stderr)
    if not args.dry_run and changed:
        print(f"Migration ledger: {ledger_path}")
    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    """sweep: NG-3 parallel width-sweep over the FROZEN _protocol.md angle
    matrix + sources.

    Reads (never widens) the angle matrix + sources frozen at
    `approve-protocol` (anti-fishing) — this command has no write path
    back to `_protocol.md`. Runs the cross-product (angle x source) fetch
    concurrently under the fetch budget, then composes: cross-source dedup
    (NG-2) -> derivative-of overlap discounting (NG-9) -> the 6-dim utility
    rank + saturation-paired floor (NG-3). Annotates the kept set vs the
    project's filed literature notes, same [NEW]/[IN-CORPUS:*] contract as
    `rv research find`.
    """
    from .sources.annotate import annotate_deduped
    from .sources.sweep import run_sweep_from_protocol

    protocol_path = Path(args.protocol)
    if not protocol_path.exists():
        print(f"rv research sweep: protocol not found: {protocol_path}", file=sys.stderr)
        return 1

    try:
        cfg = load_config()
    except Exception:
        cfg = None
    project = getattr(args, "project", None)
    if cfg and not project:
        project = _default_project(cfg)

    try:
        result = run_sweep_from_protocol(
            protocol_path,
            budget=getattr(args, "budget", None) or 65,
            per_cell_limit=getattr(args, "per_cell_limit", 20),
        )
    except ValueError as e:
        print(f"rv research sweep: {e}", file=sys.stderr)
        return 1

    lit_dir = cfg.project_notes_dir(project) / "literature" if (cfg and project) else None
    notes_index = _load_notes_index(lit_dir, literature_root=(cfg.literature_root if cfg else None))
    notes_title_index = _load_notes_title_index(lit_dir, literature_root=(cfg.literature_root if cfg else None))

    print(
        f"\nWidth-sweep: {result.total_hits_fetched} raw hit(s) fetched, "
        f"{len(result.kept)} kept after dedup+rank "
        f"({result.independent_count} independent, non-derivative)\n"
    )
    for d in result.kept:
        annotation = annotate_deduped(d, notes_index=notes_index, notes_title_index=notes_title_index)
        floor_flag = " [BELOW-FLOOR: needs more sources]" if d.hit.below_floor else ""
        deriv_flag = f" [DERIVATIVE-OF:{d.hit.derivative_of}]" if d.hit.derivative_of else ""
        srcs = ",".join(sorted(d.sources))
        print(f"  {annotation}  {d.hit.title[:70]}  (sources: {srcs}){floor_flag}{deriv_flag}")

    if result.errors:
        print(f"\n{len(result.errors)} adapter cell(s) degraded (non-fatal):", file=sys.stderr)
        for err in result.errors:
            print(f"  - {err}", file=sys.stderr)

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``research`` verb.

    When to use: use ``rv research <subcommand>`` to search Semantic Scholar,
    annotate candidates vs corpus, or add papers via the dedup gate.
    default_project is read from config — never a compiled-in codename.
    """
    desc = (
        "Unified research tooling: find → dedup → add. "
        "Shells to asta and rv cite for all S2 and Zotero operations."
    )
    if parent is not None:
        p = parent.add_parser("research", help="Research tooling (asta + Zotero).", description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv research", description=desc)

    sub = p.add_subparsers(dest="research_cmd", required=True)

    # find
    find_p = sub.add_parser(
        "find",
        help=(
            "Search Semantic Scholar (over-fetch + TF-IDF rerank for on-topic recall). "
            "Over-fetches --pool candidates from asta, reranks by TF-IDF relevance to "
            "the query, and shows the top --limit results — surfacing anchors buried by "
            "asta's recency/citation ordering. "
            "Use --no-rerank to reproduce the legacy asta-order output. "
            "--deep/WebSearch escalation still recommended for deep recall. "
            "Anti-pattern: do NOT rely on find alone for systematic lit review — it is a "
            "starting point; use `rv review <project> new <scope>` + `rv dag run` "
            "for a protocol-gated systematic review with a citation-neighbor "
            "relevance walk."
        ),
    )
    find_p.add_argument("query")
    find_p.add_argument("--deep", action="store_true", help="Deep literature review via asta literature find.")
    find_p.add_argument("--limit", type=int, default=10, help="Number of results to show (default 10).")
    find_p.add_argument(
        "--pool", type=int, default=50,
        help=(
            "Over-fetch size: number of candidates fetched from asta before reranking "
            "(default 50; asta cap is 100). Only used when --rerank is on."
        ),
    )
    find_p.add_argument(
        "--rerank", action="store_true", default=True,
        help="Rerank fetched candidates by TF-IDF relevance to the query (default on).",
    )
    find_p.add_argument(
        "--no-rerank", dest="rerank", action="store_false",
        help="Disable reranking; fetch --limit candidates in asta order (legacy output).",
    )
    find_p.add_argument(
        "--min-score", type=float, default=0.0, dest="min_score",
        help=(
            "Minimum TF-IDF similarity score to include in results (default 0.0 = "
            "reorder-not-drop; truncation to --limit is the noise filter)."
        ),
    )
    find_p.add_argument(
        "--project", default=None,
        help=(
            "Project slug (from config registry). Annotates candidates "
            "[IN-CORPUS:<citekey>] or [NEW] against the project's literature/ notes."
        ),
    )

    # cited-by / references — D1 HARD-REMOVED (verb consolidation): both
    # collapsed into the "snowball-forward"/"snowball-backward" tool node-ops
    # invoked by the review-snowball node. See cli_removed_verbs.py.
    from .cli_removed_verbs import add_removed_verb_stub
    add_removed_verb_stub(
        sub, "cited-by",
        op_or_transition="the 'snowball-forward' tool node-op (review-snowball node)",
        redirect="rv dag run <phase1-manifest> (the review-snowball node fans forward citations automatically)",
    )
    add_removed_verb_stub(
        sub, "references",
        op_or_transition="the 'snowball-backward' tool node-op (review-snowball node)",
        redirect="rv dag run <phase1-manifest> (the review-snowball node fans backward citations automatically)",
    )

    # add
    add_p = sub.add_parser("add", help="Add a paper (dedup gate + cite add).")
    add_p.add_argument("ident", help="DOI, arXiv id, URL, or S2 paper id.")
    add_p.add_argument("--project", default=None)
    add_p.add_argument("--force", action="store_true", help="Bypass dedup gate (logs loudly).")
    add_p.add_argument("--dry-run", action="store_true", help="Preview without writing.")

    # corroborate — declared-peer search + ranker (gated to declared edges)
    corr_p = sub.add_parser(
        "corroborate",
        help=(
            "Search DECLARED peer projects' OKF notes for evidence matching a claim "
            "(gated to hub-declared cross-project edges). "
            "--from is REQUIRED. "
            "Anti-pattern: do NOT search undeclared projects — "
            "use rv project relate <from> <peer> --kind <why> first."
        ),
    )
    corr_p.add_argument("claim", help="Claim or query string to corroborate.")
    corr_p.add_argument(
        "--from", dest="from_project", default=None,
        help="REQUIRED. Originating project slug (excluded from search; gates universe to declared peers).",
    )
    corr_p.add_argument(
        "--against", dest="against_projects", nargs="+", default=None,
        metavar="SLUG",
        help=(
            "Project slug(s) to search. Must be declared peers of --from. "
            "Default: all declared peers."
        ),
    )
    corr_p.add_argument(
        "--min-score", dest="min_score", type=float, default=0.05,
        help="Minimum relevance score threshold (default 0.05).",
    )
    corr_p.add_argument(
        "--top-k", dest="top_k", type=int, default=10,
        help="Maximum number of ranked candidates to return (default 10).",
    )
    corr_p.add_argument(
        "--emit", dest="emit", default=None, metavar="PATH",
        help=(
            "Write a candidates JSON to PATH for the judge node. "
            "The JSON carries claim, from, and ranked candidates with "
            "provenance + score + excerpt. Feed this to the DAG judge node via reads:."
        ),
    )

    # sweep — D1 HARD-REMOVED (verb consolidation): collapsed into the
    # "sweep" tool node-op invoked by the review-search node.
    add_removed_verb_stub(
        sub, "sweep",
        op_or_transition="the 'sweep' tool node-op (review-search node)",
        redirect="rv dag run <phase1-manifest> (the review-search node runs the width-sweep automatically)",
    )

    # fulltext — OA-first full-text enrichment (tier 1, read-time; of
    # the design doc). Delegates to fulltext.py — kept out of this already-
    # large module.
    from .fulltext import build_parser as _build_fulltext_parser
    _build_fulltext_parser(sub)

    # citekey: compute + stamp the canonical citekey into a
    # filed literature note (Zotero-free, review-loop path).
    citekey_p = sub.add_parser(
        "citekey",
        help="Compute + stamp the canonical citekey into a filed literature note.",
        description=(
            "Reads title/authors/year from the note's OWN frontmatter and stamps "
            "the canonical familyShorttitleYear citekey into `citekey:`. "
            "Unresolved metadata -> the visible CITEKEY_SENTINEL, never a guess "
            "(exit 1)."
        ),
    )
    citekey_p.add_argument("project", help="Project slug (from config registry).")
    citekey_p.add_argument(
        "note_id", help="The literature note's filename stem (literature/<note_id>.md).",
    )

    # migrate-citekeys: one-shot maintenance pass over a
    # project's literature/ notes (one project only this
    # release — the verb itself is general; only the rollout is scoped).
    migrate_p = sub.add_parser(
        "migrate-citekeys",
        help="One-shot: stamp canonical citekeys into non-conformant literature notes.",
        description=(
            "Scans a project's literature/*.md for citekey: fields absent or "
            "non-conformant to the familyShorttitleYear convention, stamps the "
            "canonical key (NEVER renames files), and records the old->new map "
            "in a migration ledger (literature/_citekey_migration_ledger.json)."
        ),
    )
    migrate_p.add_argument("project", help="Project slug (from config registry).")
    migrate_p.add_argument(
        "--dry-run", action="store_true",
        help="Preview the migration plan without writing any note or the ledger.",
    )

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch research subcommands. Returns exit code."""
    # Verb consolidation: cited-by / references / sweep are
    # HARD-REMOVED stubs — always dispatch to the redirect breadcrumb.
    if getattr(args, "_rv_removed_verb", None) is not None:
        from .cli_removed_verbs import run_removed_verb_stub
        return run_removed_verb_stub(args)

    cmd = args.research_cmd
    try:
        if cmd == "find":
            return cmd_find(args)
        elif cmd == "add":
            return cmd_add(args)
        elif cmd == "corroborate":
            return cmd_corroborate(args)
        elif cmd == "fulltext":
            from .fulltext import cmd_fulltext
            return cmd_fulltext(args)
        elif cmd == "citekey":
            return cmd_citekey(args)
        elif cmd == "migrate-citekeys":
            return cmd_migrate_citekeys(args)
        else:
            print(f"rv research: unknown subcommand {cmd!r}", file=sys.stderr)
            return 1
    except SystemExit:
        raise
    except Exception as e:
        print(f"rv research: unexpected error: {e}", file=sys.stderr)
        return 1
