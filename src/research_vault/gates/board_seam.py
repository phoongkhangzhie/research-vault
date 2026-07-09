"""gates/board_seam.py — PR-B1: the 4-lens cold-agent-judge emit/ingest
fan-out contract for the autonomous review board.

Built ON ``gates.judge_seam``'s primitives (charter §6 — reuse, don't
fork): ``interleave_with_canaries``, ``fail_closed_fill`` (id-vocab
shape only — this module's own axis-score fail-closed fill lives here,
see ``_fail_closed_axis_scores``, because a board verdict carries a 1-5
score, not a fixed-vocab verdict string), ``fanout_incomplete``,
``read_json_or_none``, ``write_json``, ``make_task_id``,
``CanaryAbortError`` (re-exported, not re-defined — the board fan-out is
the SAME untrustworthy-judge failure class as the support-matcher's).

THE THREE ARTIFACTS, one per manuscript, under
``manuscripts/<slug>/judge/board/``:
  _board-tasks.json        (rv -> hub -> cold judges; PUBLIC — canaries
                             carry no marker distinguishing them from real
                             lens tasks)
  _board-canary-key.json   (rv-PRIVATE, never emitted — task_id -> expected
                             score band: PASS-HIGH / FAIL-LOW / FAIL)
  _board-verdicts.json     (cold judges/hub -> rv; one entry per task id:
                             {id, axis, score, verdict, findings: [...]})

GUARDS (undiminished vs. ``judge_seam``'s own contract — a cold
subagent-judge can hallucinate on a 1-5 score just as easily as a fixed
verdict string):
  - id<->id join is the contract, never prompt-text matching.
  - Rejects-only: floor-not-average is applied one level up (PR-B3); THIS
    module's job is fail-closed axis scoring, not the clear predicate.
  - Fail-closed: a missing/unparseable axis score defaults to 0 (FAILs its
    axis under any floor_value >= 1) — never a silent pass. An entirely
    missing/empty verdicts file with tasks emitted -> ``halt=True``
    (``fanout_incomplete``).
  - Canary-verified: the 3 calibrated review-board probes (strong/weak/
    annotated-bib) are re-emitted UNMARKED, interleaved among the 4 real
    lens tasks. Extended ``check_canaries``-equivalent
    (``_check_board_canaries``) compares the ingested axis SCORE against
    the expected BAND (PASS-HIGH: score >= floor+1; FAIL-LOW: score <=
    floor-1; FAIL: score < floor) — not exact-verdict-string equality, the
    scores are the thing being calibrated here.
  - Idempotent + resumable: task ids are assigned deterministically by
    ``interleave_with_canaries``; a partial verdicts file surfaces
    ``missing_ids`` for a targeted re-fan.

Design: docs/superpowers/specs/2026-07-08-autonomous-board-design.md §2.
Stdlib only. Hermetic in tests — no live LLM call anywhere in this module.
sr: PR-B1
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from research_vault.gates import judge_seam
from research_vault.gates.judge_seam import CanaryAbortError  # re-export, not re-defined
from research_vault.manuscript import board_lenses

TASKS_SCHEMA = "rv-board-tasks/v1"
CANARY_KEY_SCHEMA = "rv-board-canary-key/v1"
VERDICTS_SCHEMA = "rv-board-verdicts/v1"

_DEFAULT_FLOOR_VALUE: int = board_lenses._DEFAULT_FLOOR_VALUE


# ---------------------------------------------------------------------------
# The 3 calibrated canary probes (design §1: reuse review_board's, don't
# fork new passages) — all fed to the CONTENT lens (design §1: "the
# annotated-bib probe is fed to the CONTENT judge with expected CONTENT:
# FAIL"; the strong/weak probes are calibrated on the same content-quality
# signal, so they are likewise scored on CONTENT here).
# ---------------------------------------------------------------------------

def _canary_bank(floor_value: int) -> list[tuple[dict[str, Any], str]]:
    from research_vault.manuscript.review_board import (
        _CANARY_STRONG_PASSAGE,
        _CANARY_WEAK_PASSAGE,
        _CANARY_ANNOTATED_BIB_PASSAGE,
    )

    rubric = board_lenses.CONTENT_RUBRIC.replace(
        "{FINDING_SCHEMA}", board_lenses._FINDING_SCHEMA_INSTRUCTIONS,
    )

    def _task(passage: str) -> dict[str, Any]:
        return {
            "kind": "board",
            "lens": "content",
            "axis": "CONTENT",
            "rubric": rubric.replace("{DRAFT}", passage),
            "draft": passage,
            "finding_cap": board_lenses.FINDING_CAPS["CONTENT"],
            "sub_budgets": dict(board_lenses.SUB_BUDGETS["CONTENT"]),
        }

    return [
        (_task(_CANARY_STRONG_PASSAGE), "PASS-HIGH"),
        (_task(_CANARY_WEAK_PASSAGE), "FAIL-LOW"),
        (_task(_CANARY_ANNOTATED_BIB_PASSAGE), "FAIL"),
    ]


def _score_matches_band(score: int, band: str, floor_value: int) -> bool:
    if band == "PASS-HIGH":
        return score >= floor_value + 1
    if band == "FAIL-LOW":
        return score <= floor_value - 1
    if band == "FAIL":
        return score < floor_value
    return False


def _check_board_canaries(
    canary_key: dict[str, str],
    axis_score_by_id: dict[str, int | None],
    *,
    floor_value: int,
) -> None:
    """The board's own canary check — score-BAND comparison, not exact
    verdict-string equality (``judge_seam.check_canaries``'s contract, but
    the thing under test here is a 1-5 score, not a fixed-vocab string).

    Missing-counts-as-failed (fail-closed applies to canaries too, mirrors
    ``judge_seam.check_canaries`` exactly).
    """
    for tid, expected_band in canary_key.items():
        score = axis_score_by_id.get(tid)
        if score is None:
            raise CanaryAbortError(
                f"board-fanout canary {tid!r} is MISSING an axis score from "
                f"the verdicts file (expected band {expected_band!r}) — a "
                "missing canary counts as failed (fail-closed): the fan-out "
                "judge either dropped this task or the batch never "
                "completed. Cannot trust any real board verdict alongside it."
            )
        if not _score_matches_band(score, expected_band, floor_value):
            raise CanaryAbortError(
                f"board-fanout canary {tid!r} scored {score} (expected band "
                f"{expected_band!r}, floor={floor_value}) — the fan-out "
                "judge is either broken-harsh, rubber-stamping, or blind to "
                "the #1 survey failure (an annotated bibliography with no "
                "cross-paper synthesis) on this planted probe. Cannot trust "
                "any real board verdict alongside it."
            )


# ---------------------------------------------------------------------------
# emit
# ---------------------------------------------------------------------------

def emit_board_tasks(
    draft_text: str,
    *,
    manuscript: str = "",
    round: int = 1,  # noqa: A002 - matches the design's field name
    contradiction_map: Any | None = None,
    heading_diff: dict[str, Any] | None = None,
    frozen_order: list[str] | None = None,
    floor_value: int = _DEFAULT_FLOOR_VALUE,
) -> dict[str, Any]:
    """Build ``_board-tasks.json`` + the private ``_board-canary-key.json``
    (design §2). The 4 real lens tasks (content / self-containment /
    adversarial / framework) + the 3 interleaved unmarked canary probes.

    A completely empty ``draft_text`` still emits all 4 real tasks — unlike
    the support-matcher (which has an honest zero-citations no-op), the
    board's 4 lenses always have something to judge (an empty draft IS a
    finding, most sharply on CONTENT/FRAMEWORK).

    sr: PR-B1
    """
    real_tasks = board_lenses.build_lens_tasks(
        draft_text,
        contradiction_map=contradiction_map,
        heading_diff=heading_diff,
        frozen_order=frozen_order,
    )
    combined, canary_key = judge_seam.interleave_with_canaries(
        real_tasks, _canary_bank(floor_value),
    )
    tasks_doc = {
        "schema": TASKS_SCHEMA,
        "gate": "review-board",
        "manuscript": manuscript,
        "round": round,
        "judge_kind": "cold",
        "created": judge_seam.now_iso(),
        "floor_value": floor_value,
        "tasks": combined,
    }
    canary_key_doc = {"schema": CANARY_KEY_SCHEMA, "canaries": canary_key}
    return {"tasks_doc": tasks_doc, "canary_key_doc": canary_key_doc}


def emit_board_tasks_to_dir(judge_dir: Path, draft_text: str, **kwargs: Any) -> dict[str, Any]:
    """Convenience wrapper: emit + write both artifacts under ``judge_dir``
    (typically ``tree_root / "judge" / "board" / f"round-{n}"``)."""
    result = emit_board_tasks(draft_text, **kwargs)
    judge_seam.write_json(judge_dir / "_board-tasks.json", result["tasks_doc"])
    judge_seam.write_json(judge_dir / "_board-canary-key.json", result["canary_key_doc"])
    return result


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------

def _fail_closed_axis_scores(
    real_task_ids_by_axis: dict[str, str],
    verdict_by_id: dict[str, dict[str, Any]],
) -> tuple[dict[str, int], dict[str, list[dict[str, Any]]], list[str], list[str]]:
    """Fail-closed fill for the board's axis-score verdicts (parallel to
    ``judge_seam.fail_closed_fill`` but for a ``{score, findings}`` shape
    rather than a fixed-vocab verdict string).

    Returns (axis_scores, findings_by_axis, missing_ids, unrecognized_ids).
    A missing id defaults its axis score to 0 (FAILs the floor under any
    floor_value >= 1). A present-but-unparseable score (non-int, or absent
    "score" key) also defaults to 0 and is surfaced in unrecognized_ids —
    never silently coerced.
    """
    axis_scores: dict[str, int] = {}
    findings_by_axis: dict[str, list[dict[str, Any]]] = {}
    missing_ids: list[str] = []
    unrecognized_ids: list[str] = []

    for tid, axis in real_task_ids_by_axis.items():
        v = verdict_by_id.get(tid)
        if v is None:
            axis_scores[axis] = 0
            findings_by_axis[axis] = []
            missing_ids.append(tid)
            continue
        raw_score = v.get("score")
        try:
            score = int(raw_score)
        except (TypeError, ValueError):
            axis_scores[axis] = 0
            findings_by_axis[axis] = []
            unrecognized_ids.append(tid)
            continue
        axis_scores[axis] = score
        raw_findings = v.get("findings") or []
        findings_by_axis[axis] = board_lenses.cap_and_prioritize_findings(
            list(raw_findings), axis,
        )

    return axis_scores, findings_by_axis, missing_ids, unrecognized_ids


def ingest_board_verdicts(
    tasks_doc: dict[str, Any],
    canary_key_doc: dict[str, Any] | None,
    verdicts_doc: dict[str, Any] | None,
    *,
    floor_value: int = _DEFAULT_FLOOR_VALUE,
) -> dict[str, Any]:
    """Ingest ``_board-verdicts.json`` — id-join, canary check (score-band),
    fail-closed axis scoring. Returns:

      ``{axis_scores: {axis: int}, findings: {axis: [finding, ...]},
      canary_aborted: bool, halt: bool, halt_reason: str,
      missing_ids: [...], unrecognized_ids: [...]}``

    Canary check runs FIRST (before any real-task processing) — an
    untrustworthy judge invalidates everything else; ``CanaryAbortError``
    propagates to the caller (never swallowed).

    sr: PR-B1
    """
    tasks = tasks_doc.get("tasks", [])
    canaries = (canary_key_doc or {}).get("canaries", {})
    real_tasks = [t for t in tasks if t.get("id") not in canaries]
    real_task_ids_by_axis = {t["id"]: t["axis"] for t in real_tasks}

    if not real_task_ids_by_axis:
        return {
            "axis_scores": {}, "findings": {},
            "canary_aborted": False, "halt": False, "halt_reason": "",
            "missing_ids": [], "unrecognized_ids": [],
        }

    if judge_seam.fanout_incomplete(tasks_doc, verdicts_doc):
        return {
            "axis_scores": {}, "findings": {},
            "canary_aborted": False,
            "halt": True,
            "halt_reason": (
                "board-fanout HALT: _board-verdicts.json is missing or "
                "empty while real lens tasks were emitted — the holistic-"
                "quality floor was never checked (§1.8 floor-gate NOT RUN). "
                "This is NOT a pass."
            ),
            "missing_ids": [t["id"] for t in real_tasks],
            "unrecognized_ids": [],
        }

    verdict_by_id: dict[str, dict[str, Any]] = {}
    for v in (verdicts_doc or {}).get("verdicts", []):
        vid = v.get("id")
        if vid:
            verdict_by_id[vid] = v

    axis_score_by_canary_id: dict[str, int | None] = {}
    for tid in canaries:
        v = verdict_by_id.get(tid)
        if v is None:
            axis_score_by_canary_id[tid] = None
            continue
        try:
            axis_score_by_canary_id[tid] = int(v.get("score"))
        except (TypeError, ValueError):
            axis_score_by_canary_id[tid] = None

    # Canary check FIRST.
    _check_board_canaries(canaries, axis_score_by_canary_id, floor_value=floor_value)

    axis_scores, findings_by_axis, missing_ids, unrecognized_ids = _fail_closed_axis_scores(
        real_task_ids_by_axis, verdict_by_id,
    )

    return {
        "axis_scores": axis_scores,
        "findings": findings_by_axis,
        "canary_aborted": False,
        "halt": False,
        "halt_reason": "",
        "missing_ids": missing_ids,
        "unrecognized_ids": unrecognized_ids,
    }


def ingest_board_verdicts_from_dir(judge_dir: Path, *, floor_value: int = _DEFAULT_FLOOR_VALUE) -> dict[str, Any]:
    """Convenience wrapper: read all three artifacts from ``judge_dir`` and
    ingest. A missing ``_board-tasks.json`` (nothing ever emitted) is an
    honest zero-task no-op."""
    tasks_doc = judge_seam.read_json_or_none(judge_dir / "_board-tasks.json")
    if tasks_doc is None:
        tasks_doc = {"tasks": []}
    canary_key_doc = judge_seam.read_json_or_none(judge_dir / "_board-canary-key.json")
    verdicts_doc = judge_seam.read_json_or_none(judge_dir / "_board-verdicts.json")
    return ingest_board_verdicts(tasks_doc, canary_key_doc, verdicts_doc, floor_value=floor_value)
