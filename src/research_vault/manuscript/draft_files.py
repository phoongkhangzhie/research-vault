# SPDX-License-Identifier: AGPL-3.0-or-later
"""manuscript/draft_files.py — RD-1: single source of truth for "which files
make up the reader-facing draft" (next-gen lit-review design §6, Wave B).

Before RD-1, ``bib.py``, ``fidelity_gates.py``, and ``check_gates.py`` each
hand-rolled a near-identical ``tree_root.rglob(...)`` glob — three call sites
that would each need updating separately (and drift) once the render target
changed. This collapses them to one (charter §6: reuse over create).

The manuscript loop's ONLY render target is markdown (``_report.md`` +
``sections/*.md``) — LaTeX (``main.tex``/``sections/*.tex``) has been removed
entirely (the operator's explicit call — see DEVLOG).

★ PR-D2 (two-artifact rename — architect ruling): ``_report.md`` is the
INTERNAL ``[[citekey]]`` SOURCE — the drafter/assemble write target, read by
every gate + the board. The underscore prefix follows rv's own leak
convention (``_LEAK_ARTIFACT_FILENAME_RE`` in ``check_gates.py`` — an
underscore-prefixed ``.md`` is always internal, never reader-facing). The
READER-FACING rendered artifact is ``report.md`` (no underscore) — the
``[N]``-numbered body + ``## Sources``, produced by
``bib.render_numbered_manuscript`` as a SEPARATE pass over ``_report.md``.
``resolve_draft_files`` resolves the SOURCE only; it must never return the
rendered ``report.md`` (see the collision guard,
``test_pr_d2_source_routing_driver.py``).

Stdlib only.
sr: NG-lit-review-waveB (RD-1); LaTeX removal: see DEVLOG; two-artifact
rename PR-D2.
"""
from __future__ import annotations

from pathlib import Path

# Markdown is the ONLY reader-path render target.
DRAFT_EXTENSIONS: tuple[str, ...] = (".md",)

# The root-level draft file name to look for — the INTERNAL `[[citekey]]`
# SOURCE (PR-D2). Never the rendered reader-facing `report.md` (no
# underscore) — see the module docstring's two-artifact contract.
_ROOT_DRAFT_NAMES: tuple[str, ...] = ("_report.md",)


def resolve_draft_files(tree_root: Path) -> list[Path]:
    """Return every file that makes up this manuscript's ``[[citekey]]``
    SOURCE draft (PR-D2: NOT the rendered reader-facing ``report.md``).

    Args:
        tree_root: the manuscript folder (``manuscripts/<slug>/``).

    Returns:
        ``[_report.md (if it exists)] + sections/*.md`` (sections sorted by
        filename, deterministic — never filesystem-order-flaky). Empty list
        if none of these exist yet (a fresh, undrafted manuscript).

    sr: NG-lit-review-waveB (RD-1); PR-D2 (source-routing rename)
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
