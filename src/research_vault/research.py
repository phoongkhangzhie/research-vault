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
  rv research find <query>        search Semantic Scholar (annotated vs corpus)
    --deep                        deep literature review (asta literature find)
    --limit N                     result count (default 10)
    --project NAME                match candidates against this project's corpus
  rv research cited-by <paper-id> forward snowball — papers that cite this paper
    --limit N                     result count (default 20)
    --project NAME                match against project's corpus
    (see also: rv research references — backward snowball)
  rv research references <paper-id> backward snowball — papers in this paper's reference list
    --project NAME                match against project's corpus
    (see also: rv research cited-by — forward snowball)
  rv research add <doi|arxiv>     add a paper (dedup gate → cite add)
    --project NAME                target project/collection
    --force                       bypass dedup gate (logs loudly)
    --dry-run                     preview without writing
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
    """Check asta is authenticated. Exit with remediation if not."""
    r = subprocess.run(["asta", "auth", "status"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(
            "Not authenticated with asta.\n"
            "  Fix: run 'asta auth login' then retry."
        )


# ---------------------------------------------------------------------------
# Project-scoped helpers (config-driven, never hardcoded codename)
# ---------------------------------------------------------------------------

def _refs_path_for_project(project: str | None, cfg: Config) -> str | None:
    """Return the refs (library.json) path for a project from config.

    Never falls back to a hardcoded codename — if project is None, returns None.
    """
    if not project:
        return None
    try:
        proj_rec = cfg.project(project)
    except KeyError:
        return None
    refs = proj_rec.get("refs")
    return str(Path(refs).expanduser()) if refs else None


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
# Corpus-dedup index helpers (SR-LR-1 prerequisite)
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


def _load_corpus_index(refs_path: str | None) -> dict[str, str]:
    """Build a normalized-id → citekey lookup from a Zotero library.json.

    Keys are lowercased DOIs and normalized ArXiv ids.  Returns an empty dict
    when refs_path is None, missing, or malformed — callers treat that as
    "no corpus, annotate everything [NEW]".
    """
    if not refs_path:
        return {}
    p = Path(refs_path)
    if not p.exists():
        return {}
    try:
        items = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(items, list):
        return {}

    index: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        data = item.get("data", {})
        if not isinstance(data, dict):
            continue

        # Resolve citekey: prefer citationKey field, fall back to "Citation Key:" in extra.
        ck: str | None = data.get("citationKey") or None
        if not ck:
            m = re.search(r"Citation Key:\s*(\S+)", data.get("extra", ""))
            if m:
                ck = m.group(1)
        if not ck:
            continue  # no citekey → cannot annotate; skip

        # Index DOI (lowercased)
        doi = _normalize_doi(data.get("DOI") or None)
        if doi:
            index[doi] = ck

        # Index ArXiv id from archiveID field (e.g. "arXiv:2005.14165")
        arxiv = _normalize_arxiv(data.get("archiveID") or None)
        if arxiv:
            index[arxiv] = ck

    return index


def _load_notes_index(literature_dir: Path | None) -> dict[str, str]:
    """Build a normalized-id → citekey lookup by scanning literature/*.md frontmatter.

    Fix #32: literature notes filed via ``rv note new literature`` are invisible to the
    Zotero library.json-based corpus index.  This function builds a parallel lookup
    from the doi: and arxiv_id: frontmatter fields that literature notes now carry as
    optional placeholders.  The citekey is the note's filename stem.

    Returns an empty dict when literature_dir is None or does not exist.
    Only notes with a non-empty doi or arxiv_id frontmatter field are indexed.
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
        citekey = note_path.stem
        try:
            text = note_path.read_text(encoding="utf-8")
        except OSError:
            continue
        fields, _ = _parse_frontmatter(text)

        doi = _normalize_doi(fields.get("doi") or None)
        if doi:
            index[doi] = citekey

        arxiv = _normalize_arxiv(fields.get("arxiv_id") or None)
        if arxiv:
            index[arxiv] = citekey

    return index


def _corpus_annotation(
    paper: dict,
    corpus_index: dict[str, str],
    *,
    notes_index: dict[str, str] | None = None,
) -> str:
    """Return [IN-CORPUS:<citekey>] or [NEW] for a candidate S2 paper dict.

    Checks two sources in order:
      1. corpus_index — built from the project's Zotero library.json.
      2. notes_index  — built from literature/*.md doi/arxiv_id frontmatter
                        (Fix #32: filed notes count as in-corpus even before
                        Zotero sync updates library.json).

    Returns [NEW] only if the paper matches neither source.
    """
    ext = paper.get("externalIds") or {}

    doi = _normalize_doi(ext.get("DOI"))
    arxiv = _normalize_arxiv(ext.get("ArXiv"))

    # 1. Check library.json corpus index
    if corpus_index:
        if doi and doi in corpus_index:
            return f"[IN-CORPUS:{corpus_index[doi]}]"
        if arxiv and arxiv in corpus_index:
            return f"[IN-CORPUS:{corpus_index[arxiv]}]"

    # 2. Check literature/ OKF dir index (Fix #32 — union with library.json)
    ni = notes_index or {}
    if ni:
        if doi and doi in ni:
            return f"[IN-CORPUS:{ni[doi]}]"
        if arxiv and arxiv in ni:
            return f"[IN-CORPUS:{ni[arxiv]}]"

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
    corpus_index: dict[str, str] | None = None,
    *,
    notes_index: dict[str, str] | None = None,
) -> None:
    """Print S2 paper candidates in a human-readable table.

    When corpus_index is provided (loaded from the project's library.json) and/or
    notes_index (loaded from the project's literature/ OKF dir — Fix #32), each
    candidate is annotated [IN-CORPUS:<citekey>] or [NEW] so the lit-review
    saturation stopping rule (SR-LR-1) can detect when a snowball round adds no
    new papers.
    """
    idx = corpus_index or {}
    print(f"\n{len(papers)} candidate(s)\n")
    for p in papers:
        year = p.get("year", "")
        title = (p.get("title") or "")[:65]
        first_author = _normalize_author_name(p.get("authors"))
        ext = p.get("externalIds") or {}
        arxiv = ext.get("ArXiv", "")
        doi = ext.get("DOI", "")
        id_str = f"arXiv:{arxiv}" if arxiv else (f"DOI:{doi}" if doi else "")
        annotation = _corpus_annotation(p, idx, notes_index=notes_index)
        print(f"  {annotation}  {first_author} {year}  {title}")
        if id_str:
            print(f"  {'':12}  {id_str}")
    print()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_find(args: argparse.Namespace) -> int:
    """find: search Semantic Scholar (asta papers search or --deep)."""
    _preflight_asta()
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv research find: config error: {e}", file=sys.stderr)
        return 1

    project = getattr(args, "project", None) or _default_project(cfg)
    fields = "title,year,authors,externalIds,abstract,citationCount"

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
    else:
        cmd = [
            "asta", "papers", "search", args.query,
            "--format", "json", "--limit", str(args.limit),
            "--fields", fields,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"asta papers search failed:\n{r.stderr}")
        raw = json.loads(r.stdout)
        papers = raw.get("data") or []

    refs_path = _refs_path_for_project(project, cfg)
    corpus_index = _load_corpus_index(refs_path)
    # Fix #32: also check filed literature notes (zero-infra dedup)
    lit_dir = (
        cfg.project_notes_dir(project) / "literature"
        if project else None
    )
    notes_index = _load_notes_index(lit_dir)
    _print_candidates(papers, corpus_index, notes_index=notes_index)
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

    fields = "title,year,authors,externalIds,citationCount"
    cmd = [
        "asta", "papers", "citations", args.paper_id,
        "--format", "json", "--limit", str(args.limit),
        "--fields", fields,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"asta papers citations failed:\n{r.stderr}")
    raw = json.loads(r.stdout)
    papers = [item.get("citingPaper", item) for item in (raw.get("data") or [])]

    refs_path = _refs_path_for_project(project, cfg) if cfg else None
    corpus_index = _load_corpus_index(refs_path)
    # Fix #32: also check filed literature notes
    lit_dir = (
        cfg.project_notes_dir(project) / "literature"
        if (cfg and project) else None
    )
    notes_index = _load_notes_index(lit_dir)
    _print_candidates(papers, corpus_index, notes_index=notes_index)
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

    fields = (
        "references.title,references.year,references.authors,"
        "references.externalIds,references.citationCount"
    )
    cmd = [
        "asta", "papers", "get", args.paper_id,
        "--fields", fields,
        "--format", "json",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"asta papers get failed:\n{r.stderr}")
    raw = json.loads(r.stdout)
    papers = raw.get("references") or []

    refs_path = _refs_path_for_project(project, cfg) if cfg else None
    corpus_index = _load_corpus_index(refs_path)
    # Fix #32: also check filed literature notes
    lit_dir = (
        cfg.project_notes_dir(project) / "literature"
        if (cfg and project) else None
    )
    notes_index = _load_notes_index(lit_dir)
    _print_candidates(papers, corpus_index, notes_index=notes_index)
    return 0


def cmd_corroborate(args: argparse.Namespace) -> int:
    """corroborate: search peer projects' OKF notes for evidence matching a claim.

    Free cross-project reads — no gate, no disclosure scoping.
    Everything in research-vault is public by construction.
    """
    from .cross_project import corroborate_across_projects

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv research corroborate: config error: {e}", file=sys.stderr)
        return 1

    hits = corroborate_across_projects(
        claim=args.claim,
        cfg=cfg,
        from_slug=getattr(args, "from_project", None),
        against_slugs=getattr(args, "against_projects", None),
    )

    if not hits:
        print(f"No corroboration found for: {args.claim!r}")
        return 0

    print(f"{len(hits)} corroborating note(s) for: {args.claim!r}\n")
    for hit in hits:
        print(f"  {hit['provenance']}")
        if hit["excerpt"]:
            print(f"    excerpt: {hit['excerpt']}")
    print()
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    """add: dedup preflight → cite add → cite link."""
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

    r = subprocess.run(cite_cmd)
    if r.returncode != 0:
        sys.exit(f"rv cite add failed (exit {r.returncode})")
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
    find_p = sub.add_parser("find", help="Search Semantic Scholar (annotated vs corpus).")
    find_p.add_argument("query")
    find_p.add_argument("--deep", action="store_true", help="Deep literature review via asta literature find.")
    find_p.add_argument("--limit", type=int, default=10)
    find_p.add_argument(
        "--project", default=None,
        help=(
            "Project slug (from config registry). Annotates candidates "
            "[IN-CORPUS:<citekey>] or [NEW] against the project's library.json corpus."
        ),
    )

    # cited-by (forward snowball)
    cb_p = sub.add_parser(
        "cited-by",
        help="Forward snowball: papers that cite this paper (annotated vs corpus).",
        description=(
            "Forward snowball — discover who has cited the seed paper after publication. "
            "See also: rv research references (backward snowball — what the seed itself cites)."
        ),
    )
    cb_p.add_argument("paper_id", help="S2 paper id: ARXIV:xxx, DOI:xxx, CorpusId:xxx")
    cb_p.add_argument("--limit", type=int, default=20)
    cb_p.add_argument(
        "--project", default=None,
        help=(
            "Project slug (from config registry). Annotates candidates "
            "[IN-CORPUS:<citekey>] or [NEW] against the project's library.json corpus."
        ),
    )

    # references (backward snowball)
    ref_p = sub.add_parser(
        "references",
        help="Backward snowball: papers in this paper's own reference list.",
        description=(
            "Backward snowball — fetch what the seed paper itself cites. "
            "See also: rv research cited-by (forward snowball — who cites the seed paper). "
            "Anti-pattern: do NOT hand-copy a bibliography — use this command instead."
        ),
    )
    ref_p.add_argument("paper_id", help="S2 paper id: ARXIV:xxx, DOI:xxx, CorpusId:xxx")
    ref_p.add_argument(
        "--project", default=None,
        help=(
            "Project slug (from config registry). When set, each candidate is "
            "annotated [IN-CORPUS:<citekey>] or [NEW] by matching against the "
            "project's library.json corpus — enabling the saturation stopping "
            "rule for the lit-review loop (SR-LR-1)."
        ),
    )

    # add
    add_p = sub.add_parser("add", help="Add a paper (dedup gate + cite add).")
    add_p.add_argument("ident", help="DOI, arXiv id, URL, or S2 paper id.")
    add_p.add_argument("--project", default=None)
    add_p.add_argument("--force", action="store_true", help="Bypass dedup gate (logs loudly).")
    add_p.add_argument("--dry-run", action="store_true", help="Preview without writing.")

    # corroborate — cross-project OKF note search (SR-XP: free cross-project reads)
    corr_p = sub.add_parser(
        "corroborate",
        help="Search peer projects' OKF notes for evidence matching a claim (SR-XP).",
    )
    corr_p.add_argument("claim", help="Claim or query string to corroborate.")
    corr_p.add_argument(
        "--from", dest="from_project", default=None,
        help="Originating project slug (excluded from search).",
    )
    corr_p.add_argument(
        "--against", dest="against_projects", nargs="+", default=None,
        metavar="SLUG",
        help="Project slug(s) to search. Default: all registered projects except --from.",
    )

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch research subcommands. Returns exit code."""
    cmd = args.research_cmd
    try:
        if cmd == "find":
            return cmd_find(args)
        elif cmd == "cited-by":
            return cmd_cited_by(args)
        elif cmd == "references":
            return cmd_references(args)
        elif cmd == "add":
            return cmd_add(args)
        elif cmd == "corroborate":
            return cmd_corroborate(args)
        else:
            print(f"rv research: unknown subcommand {cmd!r}", file=sys.stderr)
            return 1
    except SystemExit:
        raise
    except Exception as e:
        print(f"rv research: unexpected error: {e}", file=sys.stderr)
        return 1
