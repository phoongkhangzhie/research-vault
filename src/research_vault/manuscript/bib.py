"""bib.py — PR-M2: hermetic ``.bib`` build + citation-resolve gate (design §6, D-SV-A).

Re-instantiates the removed ``manuscript/bib.py`` (SR-RM-FIGMS deleted it),
adapted to the type-generic manuscript loop's D-SV-A contract:

  RESOLVED (design §6): build ``refs.bib`` **deterministically from the
  ``literature/`` note frontmatter** — hermetic, offline, NO live Zotero call
  during compile. ``cite.py`` (the Zotero API bridge) is for **populating**
  ``literature/`` notes, never for the build/compile path — this module does
  not import it, and imports no network library (see ``TestHermeticNoNetwork``
  in the test suite for the structural + behavioural proof).

The hermetic gate confirms BOTH, at build time (design §6):
  1. Every ``\\cite{key}`` in the draft's ``.tex`` files resolves to a real
     ``literature/`` note (citekey: field, F17 filename-stem fallback) — a
     dangling ``\\cite`` is flagged (non-empty errors), never silently
     dropped or fabricated.
  2. ``refs.bib`` is self-contained — every entry written to it is backed by
     a real ``literature/`` note (no fabricated stub entries; a missing note
     means the key is simply ABSENT from the closed bibliography, not guessed
     at).

Never fabricate a bibliographic field: authors/year/venue are emitted ONLY
when present in the note's frontmatter (the shipped literature scaffold today
carries just ``title``/``doi``/``arxiv_id`` — see ``note.py``'s ``cmd_new``;
``authors``/``year``/``venue`` are read if a note happens to carry them, by
hand or a future enrichment, never invented here).

Design: docs/superpowers/specs/2026-07-07-survey-capability-design.md (§6, §14 PR-M2).

Stdlib only.
sr: PR-M2
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from research_vault.note import _parse_frontmatter

# ---------------------------------------------------------------------------
# \cite{} extraction from .tex files (ported from the removed manuscript/bib.py,
# SR-MS-1b — the LaTeX-comment-stripping + multi-cite handling is unchanged
# craft, re-instantiated verbatim).
# ---------------------------------------------------------------------------

# Matches \cite{key}, \citep{key}, \citet{key}, \citealt{key}, etc.
# Also \cite[p. 1]{key}, \cite{key1,key2} (multi-cite).
_CITE_RE = re.compile(r"\\cite[a-z]*\*?\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")

# Matches the citekey out of a written .bib entry line: "@type{key,".
_BIB_ENTRY_KEY_RE = re.compile(r"^@\w+\{([^,\s]+),", re.MULTILINE)


def _strip_latex_comments(text: str) -> str:
    """Strip LaTeX line comments (% to end of line) from text.

    Handles the edge case of escaped percent signs (``\\%``) which are NOT
    comments. Simple line-by-line stripping — no full LaTeX parser needed for
    cite extraction.
    """
    lines: list[str] = []
    for line in text.split("\n"):
        stripped = line
        i = 0
        while i < len(line):
            if line[i] == "%" and (i == 0 or line[i - 1] != "\\"):
                stripped = line[:i]
                break
            i += 1
        lines.append(stripped)
    return "\n".join(lines)


def extract_cited_keys(tex_files: list[Path]) -> set[str]:
    r"""Extract all citekeys from \\cite{} commands in a list of .tex files.

    Handles:
      \\cite{key}        — simple
      \\citep{key}       — natbib-style
      \\cite{key1,key2}  — multi-cite
      \\cite[p. 1]{key}  — optional note

    Strips LaTeX line comments (``%`` to end-of-line) before scanning so that
    commented-out examples (e.g. ``% every \\cite{key} must resolve``) do not
    generate false positives.

    Returns a set of stripped citekey strings.
    """
    keys: set[str] = set()
    for tex_path in tex_files:
        if not tex_path.exists():
            continue
        try:
            text = tex_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = _strip_latex_comments(text)
        for m in _CITE_RE.finditer(text):
            for k in m.group(1).split(","):
                k = k.strip()
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
# BibTeX entry generation from literature/ frontmatter (never fabricate)
# ---------------------------------------------------------------------------

def _escape_bib(s: str) -> str:
    """Minimal BibTeX field escaping."""
    return s.replace("\\", "\\\\")


def _fields_to_bib_entry(citekey: str, fields: dict[str, Any]) -> str:
    """Convert a ``literature/`` note's frontmatter fields to a BibTeX entry.

    Grounding contract: emits a field ONLY when present in ``fields`` — never
    fabricates authors/year/venue/doi/arxiv_id that the note doesn't carry.
    ``title`` is the only field assumed present (every OKF note has one).

    Entry-type heuristic (best-effort, no invented specifics):
      - ``venue`` present -> ``@article`` (journal = venue).
      - otherwise -> ``@misc`` (with a ``note`` pointing at the arxiv_id/doi
        when present, so the entry is still traceable).
    """
    title = _escape_bib(str(fields.get("title") or "").strip())
    authors = _escape_bib(str(fields.get("authors") or "").strip())
    year = str(fields.get("year") or "").strip()
    venue = _escape_bib(str(fields.get("venue") or "").strip())
    doi = str(fields.get("doi") or "").strip()
    arxiv_id = str(fields.get("arxiv_id") or "").strip()

    entry_type = "article" if venue else "misc"

    lines = [f"@{entry_type}{{{citekey},"]
    if title:
        lines.append(f"  title = {{{title}}},")
    if authors:
        lines.append(f"  author = {{{authors}}},")
    if entry_type == "article" and venue:
        lines.append(f"  journal = {{{venue}}},")
    if year:
        lines.append(f"  year = {{{year}}},")
    if doi:
        lines.append(f"  doi = {{{doi}}},")
    if arxiv_id:
        lines.append(f"  note = {{arXiv:{arxiv_id}}},")
    lines.append("}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# build_refs_bib — the hermetic .bib build (design §6, part 1)
# ---------------------------------------------------------------------------

_HEADER = (
    "% refs.bib — hermetic build from literature/ frontmatter "
    "(rv manuscript, PR-M2).\n"
    "% Closed bibliography: only \\cite{}-referenced keys appear.\n"
    "% Do NOT hand-edit citekeys — the build is deterministic; re-run the\n"
    "% manuscript bib gate to regenerate.\n"
    "% NO live Zotero/network call is made to produce this file (D-SV-A).\n"
)


def build_refs_bib(
    project_notes_dir: Path,
    tree_root: Path,
    *,
    tex_files: list[Path] | None = None,
) -> tuple[list[str], Path]:
    r"""Build ``tree_root/refs.bib`` from ``literature/`` frontmatter.

    Hermetic (design §6, D-SV-A): reads only local files (``literature/*.md``
    frontmatter + the manuscript's ``.tex`` files) — no network, no Zotero API
    call is reachable from this path (see ``TestHermeticNoNetwork``).

    Args:
        project_notes_dir: the project's OKF notes root (``cfg.project_notes_dir``).
        tree_root: the manuscript folder (``manuscripts/<slug>/``).
        tex_files: explicit list of .tex files to scan for ``\\cite{}``. When
            None, scans all ``.tex`` files under ``tree_root`` recursively
            (mirrors the removed module's default).

    Returns:
        (errors, bib_path):
          errors: list of hard-error strings — one per \\cite{} with no
            backing ``literature/`` note (empty = every cite resolved).
          bib_path: path to the written ``refs.bib`` (``tree_root / "refs.bib"``).
    """
    bib_path = tree_root / "refs.bib"
    errors: list[str] = []

    literature_dir = project_notes_dir / "literature"
    lit_index = _load_literature_bib_index(literature_dir)

    if tex_files is None:
        tex_files = list(tree_root.rglob("*.tex"))
    cited_keys = extract_cited_keys(tex_files)

    matched: dict[str, dict[str, Any]] = {}
    for key in sorted(cited_keys):
        if key in lit_index:
            matched[key] = lit_index[key]
        else:
            errors.append(
                f"refs.bib: unmatched \\cite{{{key}}} — no literature/ note "
                f"with citekey (or filename stem) {key!r} found under "
                f"{literature_dir}. File one via `rv note <project> literature "
                f"--title ... --id {key}`, or check the citekey spelling."
            )

    body_parts = [_HEADER, ""]
    for key in sorted(matched):
        body_parts.append(_fields_to_bib_entry(key, matched[key]))
        body_parts.append("")

    bib_path.write_text("\n".join(body_parts).rstrip() + "\n" if matched else _HEADER, encoding="utf-8")
    return errors, bib_path


# ---------------------------------------------------------------------------
# check_hermetic_bib — the citation-resolve gate (design §6, part 2)
# ---------------------------------------------------------------------------

def check_hermetic_bib(
    project_notes_dir: Path,
    tree_root: Path,
    *,
    tex_files: list[Path] | None = None,
) -> dict[str, Any]:
    r"""The hermetic citation-resolve gate — BOTH predicates of D-SV-A.

    1. Every ``\\cite{}`` resolves to a real ``literature/`` note (dangling
       cite -> BLOCK — surfaced via ``ok: False`` + non-empty ``errors``).
    2. ``refs.bib`` is self-contained — rebuilt fresh from frontmatter only,
       so a stale hand-edited ``.bib`` is never trusted; every emitted entry
       traces to a real ``literature/`` note (asserted structurally: the
       written ``.bib``'s citekeys are always a subset of ``lit_index``, by
       construction of ``build_refs_bib``).

    Fail-closed: a build that produced ANY error is ``ok: False`` — never a
    silent partial pass (charter §2 — surface, never silently drop).

    Returns:
        {
          "ok": bool,             # True iff zero errors
          "errors": list[str],
          "bib_path": Path,
          "cited_keys": set[str],
          "resolved_keys": set[str],
        }
    """
    errors, bib_path = build_refs_bib(project_notes_dir, tree_root, tex_files=tex_files)

    literature_dir = project_notes_dir / "literature"
    lit_index = _load_literature_bib_index(literature_dir)

    if tex_files is None:
        tex_files = list(tree_root.rglob("*.tex"))
    cited_keys = extract_cited_keys(tex_files)
    resolved_keys = cited_keys & set(lit_index)

    return {
        "ok": not errors,
        "errors": errors,
        "bib_path": bib_path,
        "cited_keys": cited_keys,
        "resolved_keys": resolved_keys,
    }
