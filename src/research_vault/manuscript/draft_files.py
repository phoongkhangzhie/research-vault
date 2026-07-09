# SPDX-License-Identifier: AGPL-3.0-or-later
"""manuscript/draft_files.py — RD-1: single source of truth for "which files
make up the reader-facing draft" (next-gen lit-review design §6, Wave B).

Before RD-1, ``bib.py``, ``fidelity_gates.py``, and ``check_gates.py`` each
hand-rolled a near-identical ``tree_root.rglob("*.tex")`` / ``main.tex +
sections/*.tex`` glob — three call sites that would each need updating
separately (and drift) once the render target moved to markdown. This
collapses them to one (charter §6: reuse over create).

RD-1's render target is markdown (``report.md`` + ``sections/*.md``); this
resolver keeps scanning legacy ``.tex`` too so an in-flight manuscript
mid-migration is never silently orphaned — retiring the render TARGET for
NEW manuscripts, not deleting support for one already in progress.

Stdlib only.
sr: NG-lit-review-waveB (RD-1)
"""
from __future__ import annotations

from pathlib import Path

# RD-1: markdown is the reader-path render target; .tex kept for manuscripts
# already in progress under the pre-RD-1 render target.
DRAFT_EXTENSIONS: tuple[str, ...] = (".md", ".tex")

# The root-level draft file names to look for, in the order they're checked
# (both may exist during a mid-migration manuscript; both are scanned).
_ROOT_DRAFT_NAMES: tuple[str, ...] = ("report.md", "main.tex")


def resolve_draft_files(tree_root: Path) -> list[Path]:
    """Return every file that makes up this manuscript's reader-facing draft.

    Args:
        tree_root: the manuscript folder (``manuscripts/<slug>/``).

    Returns:
        ``[report.md and/or main.tex (whichever exist)] + sections/*.md +
        sections/*.tex`` (sections sorted by filename within each extension,
        ``.md`` before ``.tex`` — deterministic, never filesystem-order-flaky).
        Empty list if none of these exist yet (a fresh, undrafted manuscript).

    sr: NG-lit-review-waveB (RD-1)
    """
    files: list[Path] = []
    for name in _ROOT_DRAFT_NAMES:
        p = tree_root / name
        if p.exists():
            files.append(p)

    sections_dir = tree_root / "sections"
    if sections_dir.exists():
        for ext in DRAFT_EXTENSIONS:
            files.extend(sorted(sections_dir.glob(f"*{ext}")))

    return files
