# SPDX-License-Identifier: AGPL-3.0-or-later
"""bib.py — PR-M2: hermetic reference-list build + citation-resolve gate (design §6, D-SV-A).

Re-instantiates the removed ``manuscript/bib.py`` (deleted earlier),
adapted to the type-generic manuscript loop's D-SV-A contract, and later
retired its LaTeX render target entirely (the operator's explicit call —
see DEVLOG). The manuscript loop's citation convention is now
markdown-only: a ``[[citekey]]`` wikilink in the draft prose (RD-1),
resolved against a markdown-native ``references.md`` ledger.

The hermetic gate confirms BOTH, at build time (design §6):
  1. Every ``[[citekey]]`` wikilink in the draft resolves to a real
     ``literature/`` note (citekey: field, F17 filename-stem fallback) — a
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

Design: docs/superpowers/specs/2026-07-07-survey-capability-design.md (§6, §14 PR-M2).

Stdlib only.
sr: PR-M2; LaTeX removal — see DEVLOG.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from research_vault.manuscript.citation_pattern import WIKILINK_CITE_RE as _WIKILINK_CITE_RE
from research_vault.note import _parse_frontmatter

# Matches the citekey out of a written references.md entry line:
# "- **citekey** — Title...".
_REFERENCE_ENTRY_KEY_RE = re.compile(r"^-\s+\*\*([^*]+)\*\*", re.MULTILINE)


def extract_cited_keys(draft_files: list[Path]) -> set[str]:
    """Extract all citekeys from a list of markdown draft files
    (``[[citekey]]`` wikilinks, RD-1's markdown render target).

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
# literature/ frontmatter index (D-SV-A — the hermetic source of truth)
# ---------------------------------------------------------------------------

def _load_literature_bib_index(literature_dir: Path) -> dict[str, dict[str, Any]]:
    """Build a citekey -> frontmatter-fields index from ``literature/`` notes.

    Mirrors the F17 convention (``review._index_literature_notes_by_citekey``):
    identity is the ``citekey:`` frontmatter field, filename-agnostic; falls
    back to the filename stem ONLY if the field is absent or empty. Reads
    frontmatter directly (rather than importing the review-module helper) so
    ``manuscript/bib.py`` stays a leaf module with a single dependency
    (``note.py``) — no cross-loop coupling for a 6-line convention.

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
        fields, _body = _parse_frontmatter(text)
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
# build_references_md — the hermetic reference-list build (design §6, part 1)
# ---------------------------------------------------------------------------

_HEADER = (
    "# References\n\n"
    "<!-- references.md — hermetic build from literature/ frontmatter "
    "(rv manuscript, PR-M2). -->\n"
    "<!-- Closed bibliography: only [[citekey]]-referenced keys appear. -->\n"
    "<!-- Do NOT hand-edit citekeys — the build is deterministic; re-run the -->\n"
    "<!-- manuscript bib gate to regenerate. -->\n"
    "<!-- NO live Zotero/network call is made to produce this file (D-SV-A). -->\n"
)


def build_references_md(
    project_notes_dir: Path,
    tree_root: Path,
    *,
    draft_files: list[Path] | None = None,
) -> tuple[list[str], Path]:
    """Build ``tree_root/references.md`` from ``literature/`` frontmatter.

    Hermetic (design §6, D-SV-A): reads only local files (``literature/*.md``
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
    lit_index = _load_literature_bib_index(literature_dir)

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
# check_citation_resolve — the citation-resolve gate (design §6, part 2)
# ---------------------------------------------------------------------------

def check_citation_resolve(
    project_notes_dir: Path,
    tree_root: Path,
    *,
    draft_files: list[Path] | None = None,
) -> dict[str, Any]:
    """The hermetic citation-resolve gate — BOTH predicates of D-SV-A.

    1. Every ``[[citekey]]`` wikilink resolves to a real ``literature/`` note
       (dangling wikilink -> BLOCK — surfaced via ``ok: False`` + non-empty
       ``errors``).
    2. ``references.md`` is self-contained — rebuilt fresh from frontmatter
       only, so a stale hand-edited reference list is never trusted; every
       emitted entry traces to a real ``literature/`` note (asserted
       structurally: the written references' citekeys are always a subset of
       ``lit_index``, by construction of ``build_references_md``).

    Fail-closed: a build that produced ANY error is ``ok: False`` — never a
    silent partial pass (charter §2 — surface, never silently drop).

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
    )

    literature_dir = project_notes_dir / "literature"
    lit_index = _load_literature_bib_index(literature_dir)

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
