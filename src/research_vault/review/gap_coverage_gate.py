# SPDX-License-Identifier: AGPL-3.0-or-later
"""review/gap_coverage_gate.py — the spine's MISSING mechanical gate:
findings cover gaps.

0.3.2 spec §2.4 / §3 PR-4: the lit-review coverage gate (EXISTING —
review.autonomy's coverage-gate branch) certifies the CORPUS; nothing
mechanically certified that the RESEARCH GAPS a corpus/plan committed to
were ever actually closed. This module is that second gate.

THE CONTRACT (rejects-only, fail-closed — mirrors check_framework_gate /
check_coverage_allocation_gate's native structural enforcement, never an
LLM judgment call):

  Every OPEN (or REOPENED) gap in ``gaps/*.md`` must be either:
    - CLOSED   — some ``findings/*.md`` note carries a within-project
                 ``ANSWERS`` typed edge (relate_check's unified grammar)
                 targeting that gap, OR
    - EXPLICITLY LEAVES-OPEN — the gap note's own frontmatter declares
      ``disposition: leaves-open`` + a non-empty ``disposition_reason:``
      (mirrors the coverage-allocation gate's ``deferred`` bucket — an
      explicit, defensible reason, never a silent escape hatch).

A gap satisfying NEITHER is an ``open_uncovered`` finding — the gate
BLOCKs (dag/verbs.py's ``gap-coverage-gate`` autonomous-gate branch turns
this into a HALT-DECLARE disposition).

GAP-CLOSED vs GAP-SPAWNED (distinguished, never conflated): a finding's
``ANSWERS`` edge closes the gap it targets; the SAME finding is free to
ALSO surface a brand-new, freshly-authored ``gaps/<new-id>.md`` (a
downstream researcher act, out of this gate's scope — this gate only
checks that EXISTING gaps are closed-or-declared-open, it has no opinion
on whether new gaps get spawned). ``closed`` and ``open_uncovered`` in the
returned payload are disjoint by construction (a gap counted once, in
exactly one bucket) — never double-counted.

Only ``open``/``reopened`` gaps are checked — the SAME actionable-status
set ``note.check_gap_anchor`` already uses (closed-supported/closed-
filled/proven-open/promoted are resolved states, out of scope here).

Stdlib only.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..note import _parse_frontmatter

_GAP_TARGET_RE = re.compile(r"^/gaps/([A-Za-z0-9][A-Za-z0-9_.\-]*)\.md$")

#: Gap statuses this gate treats as "requires coverage" — mirrors
#: note._ACTIONABLE_GAP_STATUSES (kept as its own frozenset here rather
#: than importing the private name — a public SSOT for this set does not
#: exist yet; both lists must be kept in sync by hand until one does).
_ACTIONABLE_GAP_STATUSES: frozenset[str] = frozenset({"open", "reopened"})


def _answered_gap_ids(findings_dir: Path) -> set[str]:
    """Scan every findings/*.md note's body for a within-project ANSWERS
    typed edge and return the set of gap ids it targets.

    Uses relate_check's unified typed-edge grammar (parse_typed_edges) —
    the SAME parser every other typed-edge consumer in the codebase reads
    through (charter §6, no second edge-line regex). A finding with no
    ANSWERS edge, or none at all, contributes nothing — never an error at
    this stage (a finding simply not answering a gap is normal; only an
    OPEN gap with zero answering findings anywhere is the gate's concern).
    """
    from .relate_check import parse_typed_edges

    answered: set[str] = set()
    if not findings_dir.is_dir():
        return answered

    for p in sorted(findings_dir.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        _fields, body = _parse_frontmatter(text)
        for edge in parse_typed_edges(body).edges:
            if edge["scope"] != "within-project" or edge["tag"] != "ANSWERS":
                continue
            m = _GAP_TARGET_RE.match(edge["target"])
            if m:
                answered.add(m.group(1))

    return answered


def check_gap_coverage_gate(project_notes_dir: Path) -> dict[str, Any]:
    """The gap-coverage gate's rejects-only structural check.

    Returns:
      ok:                     bool — True iff open_uncovered is empty.
      open_uncovered:         list[str] — gap ids neither ANSWERED nor
                               explicitly LEAVES-OPEN. Non-empty -> BLOCK.
      closed:                 list[str] — gap ids ANSWERED by >=1 finding.
      leaves_open:            list[str] — gap ids with a valid
                               disposition: leaves-open + reason.
      malformed_disposition:  list[str] — gap ids that declared
                               disposition: leaves-open but with NO
                               disposition_reason: (or an empty one) — a
                               stated-but-incomplete disposition is treated
                               as uncovered (never a silent escape hatch;
                               also appears in open_uncovered).

    A missing/empty gaps/ dir is a correct, vacuous PASS (no gaps declared
    -> nothing to cover) — never a fabricated BLOCK on an absent directory.
    """
    gaps_dir = project_notes_dir / "gaps"
    findings_dir = project_notes_dir / "findings"

    if not gaps_dir.is_dir():
        return {
            "ok": True, "open_uncovered": [], "closed": [],
            "leaves_open": [], "malformed_disposition": [],
        }

    answered_gap_ids = _answered_gap_ids(findings_dir)

    open_uncovered: list[str] = []
    leaves_open: list[str] = []
    closed: list[str] = []
    malformed_disposition: list[str] = []

    for p in sorted(gaps_dir.glob("*.md")):
        gap_id = p.stem
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        fields, _body = _parse_frontmatter(text)
        status = str(fields.get("status", "open")).strip().lower()
        if status not in _ACTIONABLE_GAP_STATUSES:
            continue  # closed/proven-open/promoted — resolved, out of scope

        if gap_id in answered_gap_ids:
            closed.append(gap_id)
            continue

        disposition = str(fields.get("disposition", "")).strip().lower()
        if disposition == "leaves-open":
            reason = str(fields.get("disposition_reason", "")).strip()
            if reason:
                leaves_open.append(gap_id)
                continue
            # A stated disposition with no reason is NOT a valid escape
            # hatch — surfaced in BOTH lists (malformed for visibility,
            # open_uncovered so it actually blocks).
            malformed_disposition.append(gap_id)

        open_uncovered.append(gap_id)

    return {
        "ok": not open_uncovered,
        "open_uncovered": open_uncovered,
        "closed": closed,
        "leaves_open": leaves_open,
        "malformed_disposition": malformed_disposition,
    }
