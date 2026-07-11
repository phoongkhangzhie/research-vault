# SPDX-License-Identifier: AGPL-3.0-or-later
"""manuscript/board_ledger.py: the reconciliation ledger + the
review -> recommend -> act -> reconcile handshake.


★ The mechanical heart of decision #5: the board does not merely score —
it drives a surgical, TRACKED revise in which no recommendation is
silently dropped. Every finding across the 4 judges becomes exactly one
ledger row with a lifecycle that MUST terminate:

  PENDING --(revise step)--> ADDRESSED --(round-2 reconcile)--> VERIFIED   [terminal]
                          |-> REJECTED  --(reason recorded)---> REJECTED   [terminal]
                          |-> ESCALATED --(orchestrator)-------> ESCALATED [terminal]
                          (still PENDING after revise --------------------> reconcile FAILS, HALT-class)
       ADDRESSED but round-2 verifier says still-open -----------------> UNRESOLVED

The ledger is PRE-STUBBED by the orchestrator (``build_ledger`` — an
agent with only Read/Edit cannot create files, mirrors
``review_board.run_revise``'s "the actual redrafting is an agent action
OUTSIDE this pure function" boundary). ``reconcile_round1`` is the
mechanical no-silent-drop check: a row left ``PENDING`` after the revise
step is a HALT-class defect (``LedgerReconcileError``), not a warning —
the run cannot proceed to declare-final.

★ REVISE_AGENT_BRIEF carries, VERBATIM, the two load-bearing craft rules
folded in from the HR review-critic lessons: integrate-by-scoping (never
append-as-caveat) and reject-not-force-fit
(an oversize finding escalates, it is never retyped as a paragraph-scale
block). The revise agent this brief is dispatched to is
``[Read, Edit]``-locked at the HARNESS level (no Write, no Bash) — the
tool grant makes patch-not-regenerate STRUCTURAL, not merely disciplinary
(mirrors the charter's coordinator-class tool-grant argument, applied here
to a doer-class agent's SCOPE rather than its role).

Stdlib only. Hermetic — every function here is pure data manipulation; the
actual revise/verify judgment is an external agent action this module only
tracks the bookkeeping for.
"""
from __future__ import annotations

import re
from typing import Any

from research_vault.manuscript.board_lenses import SEVERITY_ORDER

_SEVERITY_RANK_UNKNOWN = 3

PENDING = "PENDING"
ADDRESSED = "ADDRESSED"
REJECTED = "REJECTED"
ESCALATED = "ESCALATED"
VERIFIED = "VERIFIED"
UNRESOLVED = "UNRESOLVED"

_TERMINAL_STATUSES: frozenset[str] = frozenset({VERIFIED, REJECTED, ESCALATED, UNRESOLVED})


class LedgerReconcileError(RuntimeError):
    """Raised by ``reconcile_round1`` when one or more ledger rows are
    still ``PENDING`` after the revise step — the no-silent-drop guarantee
    ("a row left PENDING -> the reconcile FAILs loudly -> the run
    cannot proceed to declare-final"). A HALT-class defect, never a
    warning that could be swallowed and proceeded past.
    """


# ---------------------------------------------------------------------------
# build_ledger — merge + dedupe + severity-sort round-1 findings across axes
# ---------------------------------------------------------------------------

def _normalize_location(location: str) -> str:
    return re.sub(r"\s+", " ", str(location).strip().lower())


def build_ledger(findings_by_axis: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Merge every axis's capped findings into one PRE-STUBBED ledger.

    Dedupe rule (mirrors ``review_board``'s merge discipline): two
    axes flagging the SAME location (normalized: collapsed whitespace,
    case-insensitive) merge into ONE row — the higher-severity finding
    wins, and every contributing axis is recorded in ``axes`` (never
    silently collapsed to a single axis's provenance).

    Every row starts ``status: PENDING``, ``revise_outcome: None``,
    ``reconcile_outcome: None`` — the orchestrator pre-stubs these because
    the revise agent (Read/Edit only) cannot create the ledger file itself.
    """
    by_location: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for axis, findings in findings_by_axis.items():
        for f in findings:
            key = _normalize_location(f.get("location", ""))
            if not key:
                # No usable location -- never silently dropped; key on the
                # finding_id instead so it still gets its own row.
                key = f"__no_location__:{f.get('finding_id', id(f))}"
            if key not in by_location:
                row = dict(f)
                row["axes"] = [axis]
                by_location[key] = row
                order.append(key)
            else:
                existing = by_location[key]
                existing["axes"].append(axis)
                existing_rank = SEVERITY_ORDER.get(str(existing.get("severity", "")).lower(), _SEVERITY_RANK_UNKNOWN)
                new_rank = SEVERITY_ORDER.get(str(f.get("severity", "")).lower(), _SEVERITY_RANK_UNKNOWN)
                if new_rank < existing_rank:
                    # Higher severity wins -- keep the winning finding's
                    # text but preserve the accumulated axes list.
                    axes = existing["axes"]
                    by_location[key] = dict(f)
                    by_location[key]["axes"] = axes

    rows = [by_location[k] for k in order]
    rows.sort(key=lambda r: SEVERITY_ORDER.get(str(r.get("severity", "")).lower(), _SEVERITY_RANK_UNKNOWN))

    for row in rows:
        row["status"] = PENDING
        row["revise_outcome"] = None
        row["reconcile_outcome"] = None

    return rows


# ---------------------------------------------------------------------------
# The revise step ACTS — recording outcomes onto the pre-stubbed ledger
# ---------------------------------------------------------------------------

REVISE_AGENT_BRIEF: str = """\
You are the BOARD REVISE agent. Your tools are Read and Edit ONLY — you
have no Write, no Bash. Your only path to change the draft is an exact-
match Edit; you cannot create files (the ledger is already pre-stubbed for
you) and you cannot regenerate whole sections.

You are handed the reconciliation ledger — a bounded, located, severity-
sorted list of findings. Your job is NOT "improve the draft." For EVERY
row you must record exactly one outcome:

  ADDRESSED — you applied a surgical Edit. Keep each edit as SMALL as
    possible while resolving the finding's `issue`. An edit that replaces
    one sentence with a better sentence is fine; an edit that replaces a
    whole paragraph is probably regeneration -- reject it instead.

    ★ INTEGRATE-BY-SCOPING, NOT APPEND-AS-CAVEAT. When a finding raises
    counter-evidence, NARROW the claim's scope to where the evidence
    actually differs -- do NOT hedge it. "X holds in Europe/NA; in China,
    Y creates a different regime where X doesn't apply [N]" strengthens
    the thesis. "X, though this may resolve differently" only tells the
    reader you're no longer sure. A revise SHARPENS the thesis; it never
    dissolves it into qualifications.

  REJECTED — the finding is declined, with a recorded `reject_reason`
    (e.g. the evidence cited doesn't actually exist, or the finding
    misreads the draft). A reject is a first-class, reasoned outcome --
    never a silent drop.

  ESCALATED — the finding requires a structural move beyond a surgical
    hunk (an H2 reorder/rename, a whole-section rewrite).
    ★ REJECT-NOT-FORCE-FIT: if a finding would require rewriting a whole
    section, do NOT "fix" it by retyping a paragraph-scale block. Log it
    as ESCALATED and hand it to the orchestrator (which has Write/Edit).

Every row in the ledger MUST end this step with a non-null revise_outcome
-- a row left PENDING makes the reconcile step FAIL and blocks the run
from declaring final. There is no "skip this one" option.
"""


def apply_revise_outcome(
    ledger: list[dict[str, Any]],
    finding_id: str,
    outcome: str,
    *,
    how: str | None = None,
    edit_location: str | None = None,
    reject_reason: str | None = None,
) -> list[dict[str, Any]]:
    """Record a revise outcome onto the matching ledger row (by
    ``finding_id``). Returns the SAME list, mutated in place (and
    returned, for chaining) -- unknown ``finding_id`` raises ``KeyError``
    (never a silent no-op on a typo'd id).
    """
    if outcome not in (ADDRESSED, REJECTED, ESCALATED):
        raise ValueError(f"unknown revise outcome {outcome!r}; must be one of ADDRESSED/REJECTED/ESCALATED")
    if outcome == REJECTED and not reject_reason:
        raise ValueError("a REJECTED outcome requires a reject_reason -- a reject is a reasoned first-class outcome, never a bare drop")

    for row in ledger:
        if row.get("finding_id") == finding_id:
            row["revise_outcome"] = {
                "outcome": outcome,
                "how": how,
                "edit_location": edit_location,
                "reject_reason": reject_reason,
            }
            row["status"] = outcome  # ADDRESSED / REJECTED / ESCALATED
            return ledger
    raise KeyError(f"no ledger row with finding_id={finding_id!r}")


def reconcile_round1(ledger: list[dict[str, Any]]) -> dict[str, Any]:
    """The no-silent-drop mechanical check: every row MUST have a
    non-null ``revise_outcome`` after the revise step.

    Raises ``LedgerReconcileError`` (HALT-class) if any row is still
    ``PENDING`` — never proceeds past this silently. Returns
    ``{ok: True, addressed: [...], rejected: [...], escalated: [...]}``
    on success.
    """
    pending = [r for r in ledger if r.get("status") == PENDING]
    if pending:
        ids = [r.get("finding_id", "?") for r in pending]
        raise LedgerReconcileError(
            f"reconcile_round1 FAILED: {len(pending)} ledger row(s) left PENDING "
            f"after the revise step (finding_id(s): {ids}) — every finding must "
            "be ADDRESSED, REJECTED, or ESCALATED; a silently-dropped "
            "recommendation is a HALT-class defect, never a warning."
        )
    return {
        "ok": True,
        "addressed": [r["finding_id"] for r in ledger if r.get("status") == ADDRESSED],
        "rejected": [r["finding_id"] for r in ledger if r.get("status") == REJECTED],
        "escalated": [r["finding_id"] for r in ledger if r.get("status") == ESCALATED],
    }


# ---------------------------------------------------------------------------
# Round-2 reconcile VERIFIES — targeted per-finding check, not a re-score
# ---------------------------------------------------------------------------

def build_verification_tasks(ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One targeted verification task per ADDRESSED row ((1)) — "does
    the draft now satisfy this specific recommendation?" REJECTED/
    ESCALATED rows are already terminal and need no verification.
    """
    return [
        {
            "finding_id": row["finding_id"],
            "issue": row.get("issue", ""),
            "recommendation": row.get("recommendation", ""),
            "edit_location": (row.get("revise_outcome") or {}).get("edit_location"),
        }
        for row in ledger
        if row.get("status") == ADDRESSED
    ]


def apply_verification_result(
    ledger: list[dict[str, Any]], finding_id: str, *, verified: bool,
) -> list[dict[str, Any]]:
    """Flip an ADDRESSED row to VERIFIED or UNRESOLVED per the round-2
    targeted check. Only valid on a row currently ADDRESSED (raises
    ``ValueError`` otherwise — verifying a REJECTED/ESCALATED/PENDING row
    is a caller bug, never silently accepted).
    """
    for row in ledger:
        if row.get("finding_id") == finding_id:
            if row.get("status") != ADDRESSED:
                raise ValueError(
                    f"cannot verify finding_id={finding_id!r}: status is "
                    f"{row.get('status')!r}, not ADDRESSED"
                )
            row["status"] = VERIFIED if verified else UNRESOLVED
            row["reconcile_outcome"] = "VERIFIED" if verified else "UNRESOLVED"
            return ledger
    raise KeyError(f"no ledger row with finding_id={finding_id!r}")


def ledger_fully_terminal(ledger: list[dict[str, Any]]) -> bool:
    """True iff every row is in a terminal state (VERIFIED/REJECTED/
    ESCALATED/UNRESOLVED) — i.e. round-2 verification has been applied to
    every ADDRESSED row. A PENDING or bare-ADDRESSED (not yet verified)
    row means round-2 is not done."""
    return all(r.get("status") in _TERMINAL_STATUSES for r in ledger)


def round2_clears(floor_round2: dict[str, Any], ledger: list[dict[str, Any]]) -> dict[str, Any]:
    """Round-2 clears iff: (a) all 4 axes >= floor in the fresh
    round-2 board floor-vote, AND (b) every ADDRESSED row is VERIFIED (no
    UNRESOLVED survivor), AND (c) no regression vs round-1 (the caller
    supplies this via ``floor_round2`` having already been produced by
    ``board.run_bounded_board``'s regression-guard-aware round record).

    Returns ``{cleared: bool, unresolved: [...], reason: str}``.
    """
    unresolved = [r["finding_id"] for r in ledger if r.get("status") == UNRESOLVED]
    axes_clear = bool(floor_round2.get("cleared", False))
    cleared = axes_clear and not unresolved
    if cleared:
        reason = "round-2: all axes clear and every ADDRESSED finding VERIFIED."
    elif not axes_clear:
        reason = "round-2: one or more axes still below floor."
    else:
        reason = f"round-2: {len(unresolved)} ADDRESSED finding(s) still UNRESOLVED after verification."
    return {"cleared": cleared, "unresolved": unresolved, "reason": reason}


# ---------------------------------------------------------------------------
# Patch-not-regenerate mechanics — a single exact-match surgical edit,
# mirroring the harness Edit tool's own contract (charter §6: reuse the
# SAME semantics the real tool enforces, don't invent a looser stand-in).
# ---------------------------------------------------------------------------

class SurgicalEditError(ValueError):
    """Raised when a surgical edit's ``old_snippet`` is not found exactly
    once in the draft — mirrors the real Edit tool's own failure mode
    (ambiguous or absent match), so a hermetic test of this mechanism
    exercises the SAME constraint the harness-level tool-lock enforces."""


def apply_surgical_edit(draft_text: str, old_snippet: str, new_snippet: str) -> str:
    """Apply ONE exact-match replacement — the same contract the real
    Read/Edit-locked revise agent operates under. Raises
    ``SurgicalEditError`` if ``old_snippet`` doesn't appear exactly once
    (never silently replaces the wrong occurrence, never no-ops on a
    missing match).
    """
    count = draft_text.count(old_snippet)
    if count == 0:
        raise SurgicalEditError(f"old_snippet not found in draft: {old_snippet!r}")
    if count > 1:
        raise SurgicalEditError(
            f"old_snippet is ambiguous ({count} occurrences) in draft: {old_snippet!r}"
        )
    return draft_text.replace(old_snippet, new_snippet, 1)