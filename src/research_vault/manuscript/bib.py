# SPDX-License-Identifier: AGPL-3.0-or-later
"""bib.py: hermetic reference-list build + citation-resolve gate.

Adapted to the type-generic manuscript loop's hermetic, never-fabricate
contract, and later retired its LaTeX render target entirely (an explicit,
documented design call — see DEVLOG). The manuscript loop's citation
convention is now markdown-only: a ``[[citekey]]`` wikilink in the draft
prose, resolved against a markdown-native ``references.md`` ledger.

The hermetic gate confirms BOTH, at build time:
  1. Every ``[[citekey]]`` wikilink in the draft resolves to a real
     ``literature/`` note (citekey: field, filename-stem fallback) — a
     dangling wikilink is flagged (non-empty errors), never silently
     dropped or fabricated.
  2. ``references.md`` is self-contained — every entry written to it is
     backed by a real ``literature/`` note (no fabricated stub entries; a
     missing note means the key is simply ABSENT from the closed reference
     list, not guessed at).

Never fabricate a bibliographic field: authors/year/venue are emitted ONLY
when present in the note's frontmatter (the shipped literature scaffold today
carries just ``title``/``doi``/``arxiv_id`` — see ``note.py``'s ``cmd_new``;
``authors``/``year``/``venue`` are read if a note happens to carry them, by
hand or a future enrichment, never invented here).


Stdlib only.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from research_vault.manuscript.citation_pattern import WIKILINK_CITE_RE as _WIKILINK_CITE_RE
from research_vault.note import _extract_central_slug, _parse_frontmatter

# Matches the citekey out of a written references.md entry line:
# "- **citekey** — Title...".
_REFERENCE_ENTRY_KEY_RE = re.compile(r"^-\s+\*\*([^*]+)\*\*", re.MULTILINE)


def extract_cited_keys(draft_files: list[Path]) -> set[str]:
    """Extract all citekeys from a list of markdown draft files
    (``[[citekey]]`` wikilinks, the markdown render target).

    Returns a set of stripped citekey strings.
    """
    keys: set[str] = set()
    for draft_path in draft_files:
        if not draft_path.exists():
            continue
        try:
            text = draft_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _WIKILINK_CITE_RE.finditer(text):
            k = m.group(1).strip()
            if k:
                keys.add(k)
    return keys


# ---------------------------------------------------------------------------
# literature/ frontmatter index (the hermetic source of truth)
# ---------------------------------------------------------------------------

def _load_literature_bib_index(
    literature_dir: Path, literature_root: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Build a citekey -> frontmatter-fields index from ``literature/`` notes.

    Mirrors the citekey-identity convention (``review._index_literature_notes_by_citekey``):
    identity is the ``citekey:`` frontmatter field, filename-agnostic; falls
    back to the filename stem ONLY if the field is absent or empty. Reads
    frontmatter directly (rather than importing the review-module helper) so
    ``manuscript/bib.py`` stays a leaf module with a single dependency
    (``note.py``) — no cross-loop coupling for a 6-line convention.

    citekey/authors/year/venue/doi/arxiv_id (everything a reference
    entry renders) are intrinsic — CORE-only content. Iterating
    ``literature_dir`` (the project's overlay) still defines the ADOPTED set
    (this manuscript can only cite what its own project's corpus contains),
    but each entry's rendered fields are resolved against ``literature_root``
    (its ``central:`` pointer) when given. ``literature_root=None`` degrades
    to reading ``literature_dir`` directly (a monolithic fixture that
    happens to carry its own fields — not a violation, just a degrade path;
    some hermetic tests do this on purpose).

    Returns:
        dict mapping citekey (str) -> frontmatter fields dict. Empty dict if
        ``literature_dir`` does not exist.
    """
    if not literature_dir.exists():
        return {}

    index: dict[str, dict[str, Any]] = {}
    for note_path in sorted(literature_dir.glob("*.md")):
        try:
            text = note_path.read_text(encoding="utf-8")
        except OSError:
            continue
        overlay_fields, _body = _parse_frontmatter(text)
        fields = dict(overlay_fields)
        central = _extract_central_slug(str(overlay_fields.get("central") or ""))
        if literature_root is not None and central:
            core_path = Path(literature_root) / f"{central}.md"
            if core_path.exists():
                try:
                    core_fields, _ = _parse_frontmatter(
                        core_path.read_text(encoding="utf-8")
                    )
                    fields = {**overlay_fields, **core_fields}
                except OSError:
                    pass
        citekey = str(fields.get("citekey") or "").strip()
        if not citekey:
            citekey = note_path.stem
        index[citekey] = fields
    return index


# ---------------------------------------------------------------------------
# Markdown reference-entry generation from literature/ frontmatter (never
# fabricate)
# ---------------------------------------------------------------------------

def _fields_to_reference_entry(citekey: str, fields: dict[str, Any]) -> str:
    """Convert a ``literature/`` note's frontmatter fields to a markdown
    reference-list bullet.

    Grounding contract: emits a field ONLY when present in ``fields`` — never
    fabricates authors/year/venue/doi/arxiv_id that the note doesn't carry.
    ``title`` is the only field assumed present (every OKF note has one).

    Format: ``- **citekey** — Title. Authors (Year). Venue. doi:X / arXiv:Y``
    (each trailing segment present only when the corresponding field is set).
    """
    title = str(fields.get("title") or "").strip()
    authors = str(fields.get("authors") or "").strip()
    year = str(fields.get("year") or "").strip()
    venue = str(fields.get("venue") or "").strip()
    doi = str(fields.get("doi") or "").strip()
    arxiv_id = str(fields.get("arxiv_id") or "").strip()

    parts = [f"- **{citekey}** — {title}."]
    if authors:
        parts.append(f" {authors}")
        if year:
            parts.append(f" ({year}).")
        else:
            parts.append(".")
    elif year:
        parts.append(f" ({year}).")
    if venue:
        parts.append(f" {venue}.")
    if doi:
        parts.append(f" doi:{doi}")
    if arxiv_id:
        parts.append(f" arXiv:{arxiv_id}")
    return "".join(parts).rstrip() + "\n"


# ---------------------------------------------------------------------------
# build_references_md — the hermetic reference-list build (part 1)
# ---------------------------------------------------------------------------

_HEADER = (
    "# References\n\n"
    "<!-- references.md — hermetic build from literature/ frontmatter "
    "(rv manuscript). -->\n"
    "<!-- Closed bibliography: only [[citekey]]-referenced keys appear. -->\n"
    "<!-- Do NOT hand-edit citekeys — the build is deterministic; re-run the -->\n"
    "<!-- manuscript bib gate to regenerate. -->\n"
    "<!-- NO live Zotero/network call is made to produce this file. -->\n"
)


def build_references_md(
    project_notes_dir: Path,
    tree_root: Path,
    *,
    draft_files: list[Path] | None = None,
    literature_root: Path | None = None,
) -> tuple[list[str], Path]:
    """Build ``tree_root/references.md`` from ``literature/`` frontmatter.

    Hermetic: reads only local files (``literature/*.md``
    frontmatter + the manuscript's markdown draft files) — no network, no
    Zotero API call is reachable from this path (see ``TestHermeticNoNetwork``).

    Args:
        project_notes_dir: the project's OKF notes root (``cfg.project_notes_dir``).
        tree_root: the manuscript folder (``manuscripts/<slug>/``).
        draft_files: explicit list of markdown files to scan for
            ``[[citekey]]`` wikilinks. When None, resolves the manuscript's
            reader-facing draft via ``draft_files.resolve_draft_files``.

    Returns:
        (errors, references_path):
          errors: list of hard-error strings — one per ``[[citekey]]`` with no
            backing ``literature/`` note (empty = every citation resolved).
          references_path: path to the written ``references.md``
            (``tree_root / "references.md"``).
    """
    references_path = tree_root / "references.md"
    errors: list[str] = []

    literature_dir = project_notes_dir / "literature"
    lit_index = _load_literature_bib_index(literature_dir, literature_root)

    if draft_files is None:
        from research_vault.manuscript.draft_files import resolve_draft_files

        draft_files = resolve_draft_files(tree_root)
    cited_keys = extract_cited_keys(draft_files)

    matched: dict[str, dict[str, Any]] = {}
    for key in sorted(cited_keys):
        if key in lit_index:
            matched[key] = lit_index[key]
        else:
            errors.append(
                f"references.md: unmatched [[{key}]] — no literature/ note "
                f"with citekey (or filename stem) {key!r} found under "
                f"{literature_dir}. File one via `rv note <project> literature "
                f"--title ... --id {key}`, or check the citekey spelling."
            )

    body_parts = [_HEADER, ""]
    for key in sorted(matched):
        body_parts.append(_fields_to_reference_entry(key, matched[key]))

    references_path.write_text(
        "\n".join(body_parts).rstrip() + "\n" if matched else _HEADER,
        encoding="utf-8",
    )
    return errors, references_path


# ---------------------------------------------------------------------------
# check_citation_resolve — the citation-resolve gate (part 2)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
#  mechanical [[citekey]] -> [N] render + hermetic numbered
# "## Sources" + references.bib.
#
# The drafter keeps the stable, content-addressable [[citekey]] token in
# sections/*.md and _report.md (unchanged above) — this is a
# SEPARATE render pass that never mutates those draft files. It reads the
# same draft files + literature/ frontmatter, and writes NEW artifacts:
#   - tree_root/report.md (the reader-facing render, a separate
#     two-artifact rename from the drafted source): the READER-FACING
#     [N]-numbered body + a "## Sources" section
#     (the gold shape). No underscore prefix — this is the one file a
#     reader ever sees; `_report.md` (source) is internal (rv's own
#     `_LEAK_ARTIFACT_FILENAME_RE` convention: underscore-prefixed .md is
#     always internal). Because `resolve_draft_files` no longer returns a
#     file named `report.md`, this render output can never feed back as a
#     SOURCE on re-run — idempotency is structural, not just observed.
#   - tree_root/references.bib      — the same closed bibliography as a
#     real, parseable BibTeX file.
# This mirrors build_references_md's hermetic, never-fabricate contract
# applied to a numbered render instead of a bare markdown ledger.
# ---------------------------------------------------------------------------

# Mirrors cite.CITEKEY_SENTINEL — duplicated, not imported: bib.py's
# hermetic-no-cite.py-import invariant (see TestHermeticNoNetwork below)
# forbids pulling in the Zotero bridge module. Keep in sync by hand; this is
# the same duplication precedent as WIKILINK_CITE_RE before it was hoisted
# to citation_pattern.py — here the boundary is deliberate (this module must
# stay a leaf), not an oversight.
_CITEKEY_SENTINEL = "CITEKEY-UNRESOLVED"


def extract_cited_keys_ordered(draft_files: list[Path]) -> list[str]:
    """Like ``extract_cited_keys`` but preserves FIRST-APPEARANCE order
    instead of returning an unordered set — required for
    deterministic ``[N]`` numbering. Deduped: a repeated ``[[key]]``
    keeps its first-seen position, not a new one.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for draft_path in draft_files:
        if not draft_path.exists():
            continue
        try:
            text = draft_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _WIKILINK_CITE_RE.finditer(text):
            k = m.group(1).strip()
            if k and k not in seen:
                seen.add(k)
                ordered.append(k)
    return ordered


def _claim_snippet(text: str, start: int, end: int, width: int = 60) -> str:
    """Return a short, single-line context window around a match span, for
    naming the offending claim in a BLOCK message — never just the
    bare citekey with no context."""
    lo = max(0, start - width)
    hi = min(len(text), end + width)
    return " ".join(text[lo:hi].split())


def _resolve_citations(
    draft_files: list[Path], lit_index: dict[str, dict[str, Any]],
) -> tuple[list[str], dict[str, dict[str, Any]], list[str]]:
    """The single hermetic scan shared by the numbering + .bib build paths.

    Returns:
        ordered_keys: every VALID (non-blank, non-sentinel) citekey token
            found, first-appearance-deduped.
        matched: subset of ordered_keys with a backing ``literature/`` note —
            the only keys safe to number/emit as a Source or ``.bib`` entry.
        errors: blank/sentinel-citekey messages (naming the offending
            claim) + unmatched-key messages (valid format, no backing note) —
            both keep the key out of ``matched``.

    A blank ``""`` or the ``CITEKEY-UNRESOLVED`` sentinel is a citekey found
    in a draft that could never be resolved (or literally means "this note's
    own metadata was unresolved" — see ``research.py``/``note.py``, which
    stamp exactly this sentinel). Both are excluded from ``matched``
    unconditionally, regardless of whether some ``literature/`` note happens
    to carry that literal string as its ``citekey:`` field (a real failure
    mode: several unresolved-metadata notes can collide on the same
    sentinel) — never emitted as a ``[N]`` entry or a ``.bib`` key.
    """
    ordered_keys: list[str] = []
    seen: set[str] = set()
    matched: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    for draft_path in draft_files:
        if not draft_path.exists():
            continue
        try:
            text = draft_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in _WIKILINK_CITE_RE.finditer(text):
            key = m.group(1).strip()
            if key == "" or key == _CITEKEY_SENTINEL:
                snippet = _claim_snippet(text, m.start(), m.end())
                label = key or "<blank>"
                errors.append(
                    f"{draft_path.name}: [[{label}]] is an unresolved/blank "
                    f"citekey — claim: \"{snippet}\" — a blank or "
                    "CITEKEY-UNRESOLVED sentinel is never rendered as a "
                    "[N] entry or .bib key; resolve the real citekey "
                    "before publish."
                )
                continue
            if key in seen:
                continue
            seen.add(key)
            ordered_keys.append(key)
            if key in lit_index:
                matched[key] = lit_index[key]
            else:
                errors.append(
                    f"{draft_path.name}: unmatched [[{key}]] — no literature/ "
                    f"note with citekey (or filename stem) {key!r} found."
                )

    return ordered_keys, matched, errors


def build_citation_numbering(
    ordered_keys: list[str], matched: dict[str, dict[str, Any]],
) -> dict[str, int]:
    """Assign ``[N]`` numbers to ``ordered_keys`` in first-appearance order,
    skipping any key absent from ``matched`` (unresolved/unmatched —
    never numbered). Deterministic: same inputs -> same numbering.
    """
    numbering: dict[str, int] = {}
    n = 0
    for key in ordered_keys:
        if key not in matched:
            continue
        n += 1
        numbering[key] = n
    return numbering


def convert_wikilinks_to_numbered(text: str, numbering: dict[str, int]) -> str:
    """Mechanically replace every ``[[key]]`` present in ``numbering`` with
    ``[N]``. A key NOT in ``numbering`` (unresolved, unmatched, or the
    sentinel) is left untouched — it surfaces as residue via
    ``find_residual_wikilinks``, never silently dropped.
    """
    def _sub(m: re.Match[str]) -> str:
        key = m.group(1).strip()
        if key in numbering:
            return f"[{numbering[key]}]"
        return m.group(0)

    return _WIKILINK_CITE_RE.sub(_sub, text)


def find_residual_wikilinks(text: str) -> list[str]:
    """Return every ``[[citekey]]`` token still present in ``text`` — a
    non-empty result after ``convert_wikilinks_to_numbered`` means a
    half-converted body, never ship it."""
    return [m.group(1).strip() for m in _WIKILINK_CITE_RE.finditer(text)]


def _fields_to_sources_entry(n: int, fields: dict[str, Any]) -> str:
    """Render one gold-shape ``## Sources`` line:
    ``[N] Authors (Year). *Title*. Venue. doi:X / arXiv:Y``

    Grounding contract (mirrors ``_fields_to_reference_entry``): emits a
    field ONLY when present — never fabricates authors/year/venue/doi/
    arxiv_id the note doesn't carry.
    """
    title = str(fields.get("title") or "").strip()
    authors = str(fields.get("authors") or "").strip()
    year = str(fields.get("year") or "").strip()
    venue = str(fields.get("venue") or "").strip()
    doi = str(fields.get("doi") or "").strip()
    arxiv_id = str(fields.get("arxiv_id") or "").strip()

    lead = ""
    if authors and year:
        lead = f"{authors} ({year}). "
    elif authors:
        lead = f"{authors}. "
    elif year:
        lead = f"({year}). "

    line = f"[{n}] {lead}*{title}*."
    if venue:
        line += f" {venue}."
    if doi:
        line += f" doi:{doi}"
    if arxiv_id:
        line += f" arXiv:{arxiv_id}"
    return line.rstrip() + "\n"


def build_sources_section(
    numbering: dict[str, int], matched: dict[str, dict[str, Any]],
) -> str:
    """Build the whole ``## Sources`` block, one line per numbered key, in
    ``[N]`` order (not citekey-sorted — the reader-facing numbered-list shape,
    distinct from ``build_references_md``'s alphabetical ledger)."""
    lines = ["## Sources", ""]
    for key, n in sorted(numbering.items(), key=lambda kv: kv[1]):
        lines.append(_fields_to_sources_entry(n, matched[key]).rstrip("\n"))
    return "\n".join(lines).rstrip() + "\n"


def _bibtex_escape(value: str) -> str:
    """Defensive brace-strip — a frontmatter field is free text, never
    guaranteed to be brace-balanced; stripping braces keeps every emitted
    ``.bib`` entry structurally well-formed (never an unbalanced/broken
    entry from a stray ``{``/``}`` in a title)."""
    return value.replace("{", "").replace("}", "")


def _fields_to_bibtex_entry(citekey: str, fields: dict[str, Any]) -> str:
    """Render one real BibTeX entry from a ``literature/`` note's frontmatter
    fields — never fabricates a field absent from ``fields`` (mirrors
    ``_fields_to_reference_entry``'s grounding contract).

    Entry type: ``@article`` when a venue is present, else ``@misc`` (no
    invented venue). ``arxiv_id`` renders as ``eprint`` + ``archivePrefix =
    {arXiv}`` (standard BibTeX/natbib convention for an arXiv-only source).
    """
    title = _bibtex_escape(str(fields.get("title") or "").strip())
    authors = _bibtex_escape(str(fields.get("authors") or "").strip())
    year = _bibtex_escape(str(fields.get("year") or "").strip())
    venue = _bibtex_escape(str(fields.get("venue") or "").strip())
    doi = _bibtex_escape(str(fields.get("doi") or "").strip())
    arxiv_id = _bibtex_escape(str(fields.get("arxiv_id") or "").strip())

    entry_type = "article" if venue else "misc"
    lines = [f"@{entry_type}{{{citekey},"]
    lines.append(f"  title = {{{title}}},")
    if authors:
        lines.append(f"  author = {{{authors}}},")
    if year:
        lines.append(f"  year = {{{year}}},")
    if venue:
        lines.append(f"  journal = {{{venue}}},")
    if doi:
        lines.append(f"  doi = {{{doi}}},")
    if arxiv_id:
        lines.append(f"  eprint = {{{arxiv_id}}},")
        lines.append("  archivePrefix = {arXiv},")
    if lines[-1].endswith(","):
        lines[-1] = lines[-1][:-1]
    lines.append("}")
    return "\n".join(lines) + "\n"


_BIB_HEADER = (
    "% references.bib — hermetic BibTeX build from literature/ frontmatter\n"
    "% (rv manuscript). Do NOT hand-edit — re-run the numbered render\n"
    "% to regenerate. NO live Zotero/network call is made to produce this\n"
    "% file (mirrors references.md's hermetic, never-fabricate contract).\n"
)


def build_references_bib(
    project_notes_dir: Path,
    tree_root: Path,
    *,
    draft_files: list[Path] | None = None,
    literature_root: Path | None = None,
) -> tuple[list[str], Path]:
    """Build ``tree_root/references.bib`` from ``literature/`` frontmatter —
    the same closed bibliography as ``build_references_md`` (cited + resolved
    keys only), rendered as real, parseable BibTeX instead of a markdown
    bullet list.

    Returns:
        (errors, bib_path): errors mirrors ``_resolve_citations`` — a
        blank/sentinel or unmatched citekey is flagged and NEVER emitted as
        a ``.bib`` entry; ``bib_path`` is always written (best-effort,
        possibly header-only when nothing resolved).
    """
    references_bib_path = tree_root / "references.bib"

    literature_dir = project_notes_dir / "literature"
    lit_index = _load_literature_bib_index(literature_dir, literature_root)

    if draft_files is None:
        from research_vault.manuscript.draft_files import resolve_draft_files

        draft_files = resolve_draft_files(tree_root)

    _ordered, matched, errors = _resolve_citations(draft_files, lit_index)

    body_parts = [_BIB_HEADER, ""]
    for key in sorted(matched):
        body_parts.append(_fields_to_bibtex_entry(key, matched[key]))

    references_bib_path.write_text(
        "\n".join(body_parts).rstrip() + "\n" if matched else _BIB_HEADER,
        encoding="utf-8",
    )
    return errors, references_bib_path


def render_numbered_manuscript(
    project_notes_dir: Path,
    tree_root: Path,
    *,
    draft_files: list[Path] | None = None,
    literature_root: Path | None = None,
) -> dict[str, Any]:
    """The full render: mechanical ``[[citekey]] -> [N]`` conversion of
    the reader-facing draft + a hermetic numbered ``## Sources`` section +
    ``references.bib`` — the deliverable.

    Never mutates the drafted ``_report.md``/``sections/*.md`` (they keep
    their stable ``[[citekey]]`` tokens) — writes two NEW artifacts:
    ``tree_root/report.md`` (the reader-facing numbered body +
    Sources — no underscore, the one file a reader ever sees) and
    ``tree_root/references.bib``.

    Fail-closed: a blank/``CITEKEY-UNRESOLVED`` citekey
    or any residual ``[[citekey]]`` left after conversion is a
    hard BLOCK — ``ok: False`` with the offending claim/key named in
    ``errors``, never a silently half-converted body.

    Returns:
        {
          "ok": bool,
          "errors": list[str],
          "numbering": dict[str, int],          # citekey -> N
          "sources_md": str,                    # the "## Sources" block
          "rendered_bodies": dict[str, str],    # draft path (str) -> converted text
          "rendered_report_path": Path,          # tree_root/report.md
          "bib_path": Path,                      # tree_root/references.bib
        }
    """
    if draft_files is None:
        from research_vault.manuscript.draft_files import resolve_draft_files

        draft_files = resolve_draft_files(tree_root)

    literature_dir = project_notes_dir / "literature"
    lit_index = _load_literature_bib_index(literature_dir, literature_root)

    ordered_keys, matched, errors = _resolve_citations(draft_files, lit_index)
    numbering = build_citation_numbering(ordered_keys, matched)

    rendered_bodies: dict[str, str] = {}
    for draft_path in draft_files:
        if not draft_path.exists():
            continue
        text = draft_path.read_text(encoding="utf-8", errors="replace")
        converted = convert_wikilinks_to_numbered(text, numbering)
        for res_key in find_residual_wikilinks(converted):
            if res_key == "" or res_key == _CITEKEY_SENTINEL:
                # Already reported with a named-claim message above —
                # don't double-report the same token under a second, less
                # specific message.
                continue
            errors.append(
                f"{draft_path.name}: residual [[{res_key}]] left in the "
                "reader body after [N] conversion — a half-converted body "
                "is never shipped."
            )
        rendered_bodies[str(draft_path)] = converted

    sources_md = build_sources_section(numbering, matched)

    # reader-facing render target — no underscore (see module
    # docstring's two-artifact contract). `resolve_draft_files` never
    # returns a path named exactly "report.md", so this write can never
    # feed back into itself as a SOURCE on a subsequent render.
    rendered_report_path = tree_root / "report.md"
    joined_parts = [
        rendered_bodies[str(p)] for p in draft_files if p.exists()
    ]
    joined = "\n\n".join(joined_parts)
    full_text = (joined.rstrip() + "\n\n" + sources_md) if joined else sources_md
    rendered_report_path.write_text(full_text, encoding="utf-8")

    bib_errors, bib_path = build_references_bib(
        project_notes_dir, tree_root, draft_files=draft_files,
        literature_root=literature_root,
    )
    for e in bib_errors:
        if e not in errors:
            errors.append(e)

    return {
        "ok": not errors,
        "errors": errors,
        "numbering": numbering,
        "sources_md": sources_md,
        "rendered_bodies": rendered_bodies,
        "rendered_report_path": rendered_report_path,
        "bib_path": bib_path,
    }


def check_citation_resolve(
    project_notes_dir: Path,
    tree_root: Path,
    *,
    draft_files: list[Path] | None = None,
    literature_root: Path | None = None,
) -> dict[str, Any]:
    """The hermetic citation-resolve gate — BOTH predicates of the module
    docstring's contract.

    1. Every ``[[citekey]]`` wikilink resolves to a real ``literature/`` note
       (dangling wikilink -> BLOCK — surfaced via ``ok: False`` + non-empty
       ``errors``).
    2. ``references.md`` is self-contained — rebuilt fresh from frontmatter
       only, so a stale hand-edited reference list is never trusted; every
       emitted entry traces to a real ``literature/`` note (asserted
       structurally: the written references' citekeys are always a subset of
       ``lit_index``, by construction of ``build_references_md``).

    Fail-closed: a build that produced ANY error is ``ok: False`` — never a
    silent partial pass (surface, never silently drop).

    Returns:
        {
          "ok": bool,             # True iff zero errors
          "errors": list[str],
          "references_path": Path,
          "cited_keys": set[str],
          "resolved_keys": set[str],
        }
    """
    errors, references_path = build_references_md(
        project_notes_dir, tree_root, draft_files=draft_files,
        literature_root=literature_root,
    )

    literature_dir = project_notes_dir / "literature"
    lit_index = _load_literature_bib_index(literature_dir, literature_root)

    if draft_files is None:
        from research_vault.manuscript.draft_files import resolve_draft_files

        draft_files = resolve_draft_files(tree_root)
    cited_keys = extract_cited_keys(draft_files)
    resolved_keys = cited_keys & set(lit_index)

    return {
        "ok": not errors,
        "errors": errors,
        "references_path": references_path,
        "cited_keys": cited_keys,
        "resolved_keys": resolved_keys,
    }
