"""check_gates.py — Structural gates for rv manuscript check.

Implements the STRUCTURAL gates only (SR-MS-2 owns the semantic ones):
  1. Unmatched-\\cite resolution — every \\cite{key} must be in refs.bib.
  2. Figure-file existence — every \\includegraphics{f} → f exists.
  3. Compile-success check — PDF exists (if manuscript_pdf is set).
  4. Data-code-availability sentinel cross-check — a "fully available"
     claim contradicted by a not-recorded-in-provenance repro row → flag.

Does NOT build:
  - Support-matcher / critic / hedge-lint / completeness-gates (→ SR-MS-2).
  - Page-limit / dedup (→ SR-MS-2).

Stdlib only.
sr: SR-MS-1b
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Gate 1: unmatched \\cite resolution
# ---------------------------------------------------------------------------

# Matches @entry{citekey, in refs.bib
_BIB_ENTRY_KEY_RE = re.compile(r"^@\w+\{([^,\s]+)", re.MULTILINE)

# Same pattern as bib.py (inline to avoid circular import)
_CITE_RE = re.compile(r"\\cite[a-z]*\*?\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")


def _collect_bib_keys(refs_bib: Path) -> set[str]:
    """Return the set of citekeys declared in refs.bib."""
    if not refs_bib.exists():
        return set()
    try:
        text = refs_bib.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return {m.group(1).strip() for m in _BIB_ENTRY_KEY_RE.finditer(text)}


def _strip_comments(text: str) -> str:
    """Strip LaTeX line comments (% to end of line, excluding \\%)."""
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


def _collect_cited_keys(tex_files: list[Path]) -> set[str]:
    r"""Collect all citekeys from \cite{} in the given .tex files (excluding comments)."""
    keys: set[str] = set()
    for tex in tex_files:
        if not tex.exists():
            continue
        try:
            text = tex.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = _strip_comments(text)
        for m in _CITE_RE.finditer(text):
            for k in m.group(1).split(","):
                k = k.strip()
                if k:
                    keys.add(k)
    return keys


def check_cite_resolution(
    tree_root: Path,
    tex_files: list[Path] | None = None,
) -> list[str]:
    r"""Check that every \cite{key} resolves against refs.bib.

    Returns a list of error strings (empty = all cites resolved).
    Each error names the unmatched citekey.
    """
    refs_bib = tree_root / "refs.bib"
    bib_keys = _collect_bib_keys(refs_bib)

    if tex_files is None:
        tex_files = list(tree_root.rglob("*.tex"))

    cited_keys = _collect_cited_keys(tex_files)
    errors: list[str] = []
    for key in sorted(cited_keys):
        if key not in bib_keys:
            errors.append(
                f"unmatched \\cite{{{key}}}: '{key}' not in refs.bib — "
                f"run `rv manuscript compile` to export the closed .bib, "
                f"or `rv cite add <doi>` if the reference is missing from library.json."
            )
    return errors


# ---------------------------------------------------------------------------
# Gate 2: figure-file existence
# ---------------------------------------------------------------------------

# Matches \includegraphics[opts]{path} and \includegraphics{path}
_INCLUDEGRAPHICS_RE = re.compile(
    r"\\includegraphics\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}"
)


def check_figure_existence(
    tree_root: Path,
    tex_files: list[Path] | None = None,
) -> list[str]:
    r"""Check that every \includegraphics{path} resolves to an existing file.

    Resolves relative to tree_root (the manuscript's artifact directory).
    Returns a list of error strings (empty = all figures exist).
    """
    if tex_files is None:
        tex_files = list(tree_root.rglob("*.tex"))

    errors: list[str] = []
    for tex in tex_files:
        if not tex.exists():
            continue
        try:
            text = tex.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = _strip_comments(text)  # skip commented-out \includegraphics examples
        for m in _INCLUDEGRAPHICS_RE.finditer(text):
            fig_path_str = m.group(1).strip()
            # Try the path relative to tree_root
            fig_path = tree_root / fig_path_str
            # Also try adding common extensions if the path has none
            candidates: list[Path] = [fig_path]
            if not fig_path.suffix:
                for ext in (".pdf", ".png", ".eps", ".jpg", ".jpeg", ".svg"):
                    candidates.append(fig_path.with_suffix(ext))
            if not any(c.exists() for c in candidates):
                errors.append(
                    f"missing figure: \\includegraphics{{{fig_path_str}}} — "
                    f"'{fig_path_str}' not found relative to {tree_root}."
                )
    return errors


# ---------------------------------------------------------------------------
# Gate 3: compile success (optional — checks PDF existence)
# ---------------------------------------------------------------------------

def check_compile_success(note_path: Path, tree_root: Path) -> list[str]:
    """Check compile success: if manuscript_pdf is set, verify the PDF exists.

    This is a passive check (does not run the compiler). If manuscript_pdf
    is unset (manuscript not yet compiled), no error is returned.
    """
    from research_vault.note import _parse_frontmatter
    if not note_path.exists():
        return [f"manuscript note not found: {note_path}"]
    text = note_path.read_text(encoding="utf-8")
    fields, _ = _parse_frontmatter(text)
    pdf_str = fields.get("manuscript_pdf", "").strip()
    if not pdf_str:
        return []  # Not yet compiled — not an error at check time
    pdf = Path(pdf_str)
    if not pdf.exists():
        return [
            f"compile check: manuscript_pdf is set to '{pdf_str}' but the file "
            f"does not exist — run `rv manuscript compile` to produce the PDF."
        ]
    return []


# ---------------------------------------------------------------------------
# Gate 4: data-code-availability sentinel cross-check
# ---------------------------------------------------------------------------

# Phrases indicating a "fully available" claim in the availability section.
_AVAILABILITY_CLAIM_RE = re.compile(
    r"\b(fully available|all .{0,20} available|publicly available|"
    r"code .{0,10} available|data .{0,10} available|open[- ]source)\b",
    re.IGNORECASE,
)

_SENTINEL = "not-recorded-in-provenance"

# Repro fields that are REQUIRED for a "fully available" claim to be credible.
_REQUIRED_FOR_AVAIL = frozenset({
    "repro_seed",
    "repro_model_id",
    "repro_eval_harness",
    "repro_dataset_id",
    "repro_dataset_hash",
    "repro_metric",
})


def check_availability_sentinel(
    tree_root: Path,
    experiment_notes: list[Path],
) -> list[str]:
    """Cross-check data-code-availability claim against repro sentinel fields.

    Structurally-checkable gate: if the data-code-availability section contains
    a "fully available" claim AND at least one required repro field is still at
    sentinel in any scoped experiment note → flag as a warning.

    This catches the specific anti-pattern: writing "all code and data available"
    when reproducibility fields haven't been filled in yet.

    Returns a list of warning/flag strings (empty = no cross-check issue).
    """
    from research_vault.note import _parse_frontmatter

    avail_section = tree_root / "sections" / "data-code-availability.tex"
    if not avail_section.exists():
        return []  # Section not present — no check needed

    try:
        avail_text = avail_section.read_text(encoding="utf-8")
    except OSError:
        return []

    if not _AVAILABILITY_CLAIM_RE.search(avail_text):
        return []  # No availability claim — no cross-check needed

    # Check experiment notes for sentinel repro fields
    flags: list[str] = []
    for exp_note in experiment_notes:
        if not exp_note.exists():
            continue
        try:
            text = exp_note.read_text(encoding="utf-8")
        except OSError:
            continue
        fields, _ = _parse_frontmatter(text)
        sentinel_fields = [
            f for f in _REQUIRED_FOR_AVAIL
            if fields.get(f, "").strip() == _SENTINEL
        ]
        if sentinel_fields:
            flags.append(
                f"availability sentinel cross-check: "
                f"data-code-availability.tex claims data/code availability but "
                f"{exp_note.name} has repro fields still at 'not-recorded-in-provenance': "
                f"{', '.join(sentinel_fields)}. "
                f"Fill these fields (via `rv wandb pull`) or qualify the availability claim."
            )

    return flags


# ---------------------------------------------------------------------------
# Main gate runner
# ---------------------------------------------------------------------------

def check_manuscript(
    note_path: Path,
    tree_root: Path,
    *,
    experiment_notes: list[Path] | None = None,
    tex_files: list[Path] | None = None,
) -> dict[str, Any]:
    """Run all structural gates for rv manuscript check.

    When to use: ``rv manuscript check <id>`` — run the structural grounding
    gates BEFORE the semantic ones (SR-MS-2). Structural gates are cheap,
    binary, and do not require an LLM.

    Args:
        note_path: path to the manuscript/<id>.md OKF note.
        tree_root: path to manuscripts/<id>/ artifact tree.
        experiment_notes: list of scoped experiments/ note paths (for the
            availability cross-check). When None, resolved from the note's
            synthesized_okf field relative to the project notes dir.
        tex_files: list of .tex files to scan. When None, rglob tree_root.

    Returns:
        dict with:
          "errors": list of hard error strings (unmatched cite, missing figure)
          "warnings": list of warning strings (availability cross-check)
          "all_ok": bool (True iff errors is empty)
    """
    from research_vault.note import _parse_frontmatter
    from research_vault.config import load_config

    errors: list[str] = []
    warnings: list[str] = []

    # ── Resolve experiment notes if not provided ───────────────────────────
    if experiment_notes is None:
        experiment_notes = []
        if note_path.exists():
            text = note_path.read_text(encoding="utf-8")
            fields, _ = _parse_frontmatter(text)
            scope_str = fields.get("synthesized_okf", "").strip()
            if scope_str:
                try:
                    cfg = load_config()
                    # Extract project from note path (heuristic: manuscript/<id>.md
                    # lives under project_notes_dir/<project>/manuscript/)
                    # Walk up to find project_notes_dir
                    for scope_item in scope_str.split(","):
                        scope_item = scope_item.strip()
                        if scope_item.startswith("experiments/"):
                            exp_name = scope_item[len("experiments/"):]
                            # Try to find the experiment note relative to the
                            # manuscript note's project dir
                            candidate = note_path.parent.parent / "experiments" / f"{exp_name}.md"
                            if candidate.exists():
                                experiment_notes.append(candidate)
                except Exception:
                    pass

    if tex_files is None:
        tex_files = list(tree_root.rglob("*.tex"))

    # ── Gate 1: unmatched \\cite ───────────────────────────────────────────
    errors.extend(check_cite_resolution(tree_root, tex_files))

    # ── Gate 2: figure-file existence ─────────────────────────────────────
    errors.extend(check_figure_existence(tree_root, tex_files))

    # ── Gate 3: compile success (passive) ─────────────────────────────────
    errors.extend(check_compile_success(note_path, tree_root))

    # ── Gate 4: data-code-availability sentinel cross-check ───────────────
    warnings.extend(check_availability_sentinel(tree_root, experiment_notes))

    return {
        "errors": errors,
        "warnings": warnings,
        "all_ok": len(errors) == 0,
    }
