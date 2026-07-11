# SPDX-License-Identifier: AGPL-3.0-or-later
"""manuscript/board.py: the re-lensed board orchestration —
floor-on-ALL-6-axes, fanout-driven, bounded N=2/hardcap-3 unroll (
4->6 lenses — CONTENT split into DEPTH/WIDTH/SYNTH, FRAMEWORK renamed
INSTRUCT).


Distinct from ``manuscript.review_board`` (the OLDER 2x3 conference-style,
8-dim, floor-on-3-of-8 board) — this module is the NEW 6-lens cold-fanout
board (design decision #1: SIX judges, distinct lenses, one vote per
axis, EVERY axis a floor axis). It reuses ``review_board``'s bounded-unroll
SHAPE (N pre-declared round-blocks, skip-once-cleared, the regression guard,
the NOT-CLEARED payload) but drives ``gates.board_seam``'s fanout instead
of an in-process ``judge_fn`` — the fundamental architectural difference
this PR's "re-lens... driving the fanout instead" scope calls for.

``ingest_fn`` is the fanout stand-in (the injectable seam, mirrors
``review_board.run_reviewer_node``'s ``judge_fn`` injection): in production
this is "the hub fanned out fresh cold subagent-judges over the emitted
tasks and here are the returned verdicts" (an out-of-process step no rv
function can synchronously block on); in tests it is a hermetic mock
returning a fixture ``verdicts_doc``.

Stdlib only. Hermetic in tests — ``ingest_fn`` is always injectable; no
live LLM call is required to exercise this module.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from research_vault.gates.board_seam import (
    emit_board_tasks,
    ingest_board_verdicts,
)
from research_vault.manuscript.board_lenses import AXES

_DEFAULT_FLOOR_VALUE: int = 3
_DEFAULT_MAX_ROUNDS: int = 2
_MAX_ROUNDS_HARDCAP: int = 3


# ---------------------------------------------------------------------------
# Floor-not-average predicate — EVERY axis is a floor axis (decision #1)
# ---------------------------------------------------------------------------

def evaluate_board_floor(
    axis_scores: dict[str, int],
    *,
    floor_value: int = _DEFAULT_FLOOR_VALUE,
    axes: tuple[str, ...] = AXES,
) -> dict[str, Any]:
    """``cleared`` iff EVERY axis in ``axes`` scores >= ``floor_value``.

    A missing axis (not present in ``axis_scores`` at all) defaults to 0 —
    fail-closed, mirrors ``board_seam``'s own fail-closed axis-score fill.
    With one judge per axis there is no MIN-across-reviewers term (that's
    an ``ingest_board_verdicts``-level concern were per-axis redundancy
    ever added, R1 in the design doc) — the floor here is simply "all axes
    clear."
    """
    floor_results: dict[str, dict[str, Any]] = {}
    for axis in axes:
        score = axis_scores.get(axis, 0)
        floor_results[axis] = {"score": score, "floor": floor_value, "passed": score >= floor_value}
    cleared = all(fr["passed"] for fr in floor_results.values())
    return {"cleared": cleared, "floor_results": floor_results}


# ---------------------------------------------------------------------------
# One round — emit -> (fanout) -> ingest -> evaluate
# ---------------------------------------------------------------------------

def run_board_round(
    round_num: int,
    draft_text: str,
    *,
    ingest_fn: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any] | None],
    manuscript: str = "",
    contradiction_map: Any | None = None,
    heading_diff: dict[str, Any] | None = None,
    frozen_order: list[str] | None = None,
    coverage_map: Any | None = None,
    coverage_diff: dict[str, Any] | None = None,
    floor_value: int = _DEFAULT_FLOOR_VALUE,
) -> dict[str, Any]:
    """Run one fanout round: emit the 6 lens tasks + 3 canaries, obtain
    verdicts via ``ingest_fn(tasks_doc, canary_key_doc) -> verdicts_doc``,
    ingest (fail-closed + canary-verified), evaluate the floor.

    ``CanaryAbortError`` from the ingest step propagates to the caller —
    this function does NOT catch it (mirrors ``review_board``'s canary
    scaffold: an untrustworthy round must abort loudly, never be silently
    downgraded to "not cleared").
    """
    emitted = emit_board_tasks(
        draft_text,
        manuscript=manuscript,
        round=round_num,
        contradiction_map=contradiction_map,
        heading_diff=heading_diff,
        frozen_order=frozen_order,
        coverage_map=coverage_map,
        coverage_diff=coverage_diff,
        floor_value=floor_value,
    )
    verdicts_doc = ingest_fn(emitted["tasks_doc"], emitted["canary_key_doc"])
    ingest_result = ingest_board_verdicts(
        emitted["tasks_doc"], emitted["canary_key_doc"], verdicts_doc, floor_value=floor_value,
    )

    if ingest_result["halt"]:
        return {
            "round": round_num,
            "halt": True,
            "halt_reason": ingest_result["halt_reason"],
            "cleared": False,
            "floor_results": {},
            "axis_scores": {},
            "findings": {},
        }

    floor = evaluate_board_floor(ingest_result["axis_scores"], floor_value=floor_value)

    return {
        "round": round_num,
        "halt": False,
        "halt_reason": "",
        "cleared": floor["cleared"],
        "floor_results": floor["floor_results"],
        "axis_scores": ingest_result["axis_scores"],
        "findings": ingest_result["findings"],
        "missing_ids": ingest_result["missing_ids"],
        "unrecognized_ids": ingest_result["unrecognized_ids"],
    }


# ---------------------------------------------------------------------------
# Bounded N-round unroll — skip-once-cleared, regression guard, NOT-CLEARED
# ---------------------------------------------------------------------------

def run_bounded_board(
    draft_text: str,
    *,
    ingest_fn: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any] | None],
    manuscript: str = "",
    N: int = _DEFAULT_MAX_ROUNDS,
    floor_value: int = _DEFAULT_FLOOR_VALUE,
    contradiction_map: Any | None = None,
    heading_diff: dict[str, Any] | None = None,
    frozen_order: list[str] | None = None,
    coverage_map: Any | None = None,
    coverage_diff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The full bounded N-round (default 2, hardcap 3) board loop.

    Skip-once-cleared: once a round clears, no further round is run (no
    further ``emit_board_tasks``/``ingest_fn`` calls — asserted by tests
    via call-count on ``ingest_fn``).

    Regression guard: never silently accept a round that regresses an axis
    vs. the prior round — the caller (revise/round-2 driver) is
    expected to keep the better draft; this function surfaces the
    regression in the round record (``regression``), it does not itself
    revert anything (mirrors ``review_board.run_meta_review``'s split of
    concerns: detect + surface here, act elsewhere).

    Returns:
      cleared, cleared_at, rounds (per-round records), not_cleared (the
      NOT-CLEARED payload — None if cleared), halt (True iff any round
      HALTed on an incomplete fanout).
    """
    N_capped = min(N, _MAX_ROUNDS_HARDCAP)
    rounds: list[dict[str, Any]] = []
    cleared = False
    cleared_at: int | None = None
    prior_floor_results: dict[str, dict[str, Any]] | None = None
    halted = False

    for r in range(1, N_capped + 1):
        if cleared:
            break

        round_record = run_board_round(
            r, draft_text,
            ingest_fn=ingest_fn, manuscript=manuscript, floor_value=floor_value,
            contradiction_map=contradiction_map, heading_diff=heading_diff, frozen_order=frozen_order,
            coverage_map=coverage_map, coverage_diff=coverage_diff,
        )

        if round_record["halt"]:
            halted = True
            rounds.append(round_record)
            break

        regression_axes: list[str] = []
        if prior_floor_results:
            for axis, fr in round_record["floor_results"].items():
                prior_fr = prior_floor_results.get(axis)
                if prior_fr is not None and fr["score"] < prior_fr["score"]:
                    regression_axes.append(axis)
        round_record["regression"] = {"regressed": bool(regression_axes), "axes": regression_axes}
        prior_floor_results = round_record["floor_results"]

        rounds.append(round_record)

        if round_record["cleared"]:
            cleared = True
            cleared_at = r

    not_cleared_payload: dict[str, Any] | None = None
    if not cleared and not halted:
        last = rounds[-1] if rounds else {}
        failing_axes = [
            f"{axis} (score {fr['score']} < floor {fr['floor']})"
            for axis, fr in last.get("floor_results", {}).items()
            if not fr.get("passed", True)
        ]
        worst_findings: list[str] = []
        for axis, fr in last.get("floor_results", {}).items():
            if not fr.get("passed", True):
                for f in last.get("findings", {}).get(axis, [])[:3]:
                    worst_findings.append(f"[{axis}] {f.get('issue', '(no issue text)')}")
        persistent_weakness = (
            f"Board did not clear after {len(rounds)} round(s). Failing axis(es): "
            f"{', '.join(failing_axes) or 'all axes'}. Surviving finding(s): "
            f"{'; '.join(worst_findings) or 'see round floor_results above'}."
        )
        not_cleared_payload = {
            "n_rounds": len(rounds),
            "failing_dims": failing_axes,
            "persistent_weakness": persistent_weakness,
            "worst_findings": worst_findings,
        }

    return {
        "cleared": cleared,
        "cleared_at": cleared_at,
        "rounds": rounds,
        "not_cleared": not_cleared_payload,
        "halt": halted,
        "n_rounds_run": len(rounds),
    }


# ---------------------------------------------------------------------------
# The final board-result artifact — the handoff INTO
# ``approve-manuscript --auto``'s gate-policy dispatch (dag/verbs.py).
# ---------------------------------------------------------------------------
#
# The board's rounds require out-of-process cold-agent-judge fanouts
# between rounds (an external hub action no DAG node can synchronously
# block on) — so ``run_bounded_board`` is driven ONCE, out-of-band, by
# whatever orchestrates the manuscript loop's board phase, and its result
# is written here for the DAG's approve-manuscript gate to pick up.
# ``_evaluate_autonomous_gate`` treats a MISSING result file as "the board
# was never run for this manuscript" — an honest no-op that leaves the
# structural-gate-only disposition unchanged (never a fabricated GO/HALT
# for a board that was never driven), mirroring the support-matcher's own
# not_run convention for "no judge configured."

BOARD_RESULT_FILENAME = "_board-result.json"


def write_board_result(judge_board_dir: Path, result: dict[str, Any]) -> None:
    """Write ``run_bounded_board``'s return dict as the final board-result
    artifact (``judge_board_dir / "_board-result.json"``)."""
    judge_board_dir.mkdir(parents=True, exist_ok=True)
    (judge_board_dir / BOARD_RESULT_FILENAME).write_text(
        json.dumps(result, indent=2, sort_keys=False) + "\n", encoding="utf-8",
    )


def read_board_result(judge_board_dir: Path) -> dict[str, Any] | None:
    """Read the final board-result artifact; ``None`` if absent/unreadable
    (never raises — an absent board result is a legitimate "board not run
    yet" state, not a crash)."""
    path = judge_board_dir / BOARD_RESULT_FILENAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None