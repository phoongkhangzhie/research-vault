"""tests/test_board_orchestration_b3.py — PR-B3 acceptance tests: the
re-lensed board orchestration (manuscript/board.py) — floor-on-ANY-axis,
skip-once-cleared, NOT-CLEARED payload.

Design: docs/superpowers/specs/2026-07-08-autonomous-board-design.md §3/§5.1,
PR-B3 acceptance criteria (a)/(b)/(c).
"""
from __future__ import annotations

import pytest

from research_vault.gates.board_seam import CanaryAbortError, emit_board_tasks
from research_vault.manuscript import board


_DRAFT = "## Introduction\n\nSome draft text.\n"


_ALL_AXES = ("DEPTH", "WIDTH", "SYNTH", "SELFCONT", "ADVERS", "INSTRUCT")


def _clean_verdicts(tasks_doc, canary_key_doc, *, scores: dict[str, int] | None = None):
    merged = {axis: 4 for axis in _ALL_AXES}
    if scores:
        merged.update(scores)
    scores = merged
    canaries = canary_key_doc["canaries"]
    verdicts = []
    for t in tasks_doc["tasks"]:
        tid = t["id"]
        if tid in canaries:
            band = canaries[tid]
            score = {"PASS-HIGH": 5, "FAIL-LOW": 1, "FAIL": 2}[band]
        else:
            score = scores[t["axis"]]
        verdicts.append({"id": tid, "axis": t["axis"], "score": score, "verdict": "PASS", "findings": []})
    return {"verdicts": verdicts}


def _make_ingest_fn(scores: dict[str, int] | None = None, call_log: list | None = None):
    def _fn(tasks_doc, canary_key_doc):
        if call_log is not None:
            call_log.append(tasks_doc.get("round"))
        return _clean_verdicts(tasks_doc, canary_key_doc, scores=scores)
    return _fn


# ---------------------------------------------------------------------------
# PR-B3 acceptance (a): floor-not-average sinks on ANY axis
# ---------------------------------------------------------------------------

class TestFloorSinksOnAnyAxis:
    @pytest.mark.parametrize("failing_axis", ["DEPTH", "WIDTH", "SYNTH", "SELFCONT", "ADVERS", "INSTRUCT"])
    def test_single_failing_axis_prevents_clear(self, failing_axis):
        scores = {failing_axis: 2}  # below floor; every other axis defaults to 4
        ingest_fn = _make_ingest_fn(scores)
        result = board.run_bounded_board(_DRAFT, ingest_fn=ingest_fn, N=1)
        assert result["cleared"] is False
        assert result["rounds"][0]["floor_results"][failing_axis]["passed"] is False

    def test_all_six_axes_passing_clears(self):
        ingest_fn = _make_ingest_fn({axis: 3 for axis in _ALL_AXES})
        result = board.run_bounded_board(_DRAFT, ingest_fn=ingest_fn, N=1)
        assert result["cleared"] is True

    def test_evaluate_board_floor_direct(self):
        r = board.evaluate_board_floor({"DEPTH": 5, "WIDTH": 5, "SYNTH": 5, "SELFCONT": 2, "ADVERS": 5, "INSTRUCT": 5})
        assert r["cleared"] is False
        assert r["floor_results"]["SELFCONT"]["passed"] is False
        assert r["floor_results"]["DEPTH"]["passed"] is True

    def test_missing_axis_defaults_to_zero_fails_closed(self):
        r = board.evaluate_board_floor({"DEPTH": 5, "WIDTH": 5, "SYNTH": 5, "SELFCONT": 5, "ADVERS": 5})  # INSTRUCT absent
        assert r["cleared"] is False
        assert r["floor_results"]["INSTRUCT"]["score"] == 0


# ---------------------------------------------------------------------------
# PR-B3 acceptance (b): a clean round-1 skips round-2 (no round-2 tasks)
# ---------------------------------------------------------------------------

class TestSkipOnceCleared:
    def test_clean_round1_never_emits_round2(self):
        call_log: list = []
        ingest_fn = _make_ingest_fn(call_log=call_log)
        result = board.run_bounded_board(_DRAFT, ingest_fn=ingest_fn, N=2)
        assert result["cleared"] is True
        assert result["cleared_at"] == 1
        assert call_log == [1]  # ingest_fn (and therefore emit) never called for round 2
        assert len(result["rounds"]) == 1

    def test_not_cleared_round1_runs_round2(self):
        call_log: list = []
        ingest_fn = _make_ingest_fn({"DEPTH": 2}, call_log=call_log)
        result = board.run_bounded_board(_DRAFT, ingest_fn=ingest_fn, N=2)
        assert result["cleared"] is False
        assert call_log == [1, 2]
        assert len(result["rounds"]) == 2

    def test_hardcap_never_exceeds_3_rounds(self):
        ingest_fn = _make_ingest_fn({"DEPTH": 2})
        result = board.run_bounded_board(_DRAFT, ingest_fn=ingest_fn, N=10)
        assert result["n_rounds_run"] == 3


# ---------------------------------------------------------------------------
# PR-B3 acceptance (c): not-cleared-after-N produces the NOT-CLEARED
# payload with persistent_weakness + surviving worst_findings.
# ---------------------------------------------------------------------------

class TestNotClearedPayload:
    def test_not_cleared_after_n_builds_payload(self):
        ingest_fn = _make_ingest_fn({"DEPTH": 2})
        result = board.run_bounded_board(_DRAFT, ingest_fn=ingest_fn, N=2)
        assert result["cleared"] is False
        nc = result["not_cleared"]
        assert nc is not None
        assert "DEPTH" in " ".join(nc["failing_dims"])
        assert nc["persistent_weakness"]
        assert nc["n_rounds"] == 2

    def test_cleared_board_has_no_not_cleared_payload(self):
        ingest_fn = _make_ingest_fn()
        result = board.run_bounded_board(_DRAFT, ingest_fn=ingest_fn, N=1)
        assert result["not_cleared"] is None

    def test_worst_findings_surface_failing_axis_issue_text(self):
        def _ingest(tasks_doc, canary_key_doc):
            verdicts_doc = _clean_verdicts(
                tasks_doc, canary_key_doc, scores={"DEPTH": 2},
            )
            canaries = canary_key_doc["canaries"]
            for v in verdicts_doc["verdicts"]:
                task = next(t for t in tasks_doc["tasks"] if t["id"] == v["id"])
                if task["axis"] == "DEPTH" and v["id"] not in canaries:
                    v["findings"] = [{
                        "finding_id": "f-depth-0001", "severity": "critical",
                        "location": "§1", "issue": "no cross-paper synthesis",
                        "evidence": "lit/foo", "recommendation": "compare A vs B",
                    }]
            return verdicts_doc

        result = board.run_bounded_board(_DRAFT, ingest_fn=_ingest, N=1)
        nc = result["not_cleared"]
        assert any("no cross-paper synthesis" in wf for wf in nc["worst_findings"])


# ---------------------------------------------------------------------------
# Regression guard + halt propagation
# ---------------------------------------------------------------------------

class TestRegressionGuardAndHalt:
    def test_regression_vs_prior_round_is_surfaced(self):
        calls = {"n": 0}

        def _ingest(tasks_doc, canary_key_doc):
            calls["n"] += 1
            # Round 1: DEPTH=2 (fail). Round 2: DEPTH drops further to 1 (regression).
            scores = {"DEPTH": 2} if calls["n"] == 1 \
                else {"DEPTH": 1}
            return _clean_verdicts(tasks_doc, canary_key_doc, scores=scores)

        result = board.run_bounded_board(_DRAFT, ingest_fn=_ingest, N=2)
        assert result["rounds"][1]["regression"]["regressed"] is True
        assert "DEPTH" in result["rounds"][1]["regression"]["axes"]

    def test_incomplete_fanout_halts_the_whole_loop(self):
        def _ingest(tasks_doc, canary_key_doc):
            return None  # never completes -> fanout_incomplete

        result = board.run_bounded_board(_DRAFT, ingest_fn=_ingest, N=2)
        assert result["halt"] is True
        assert result["cleared"] is False
        assert result["not_cleared"] is None  # halt takes priority, never a quality-shortfall payload

    def test_canary_abort_propagates_out_of_the_loop(self):
        def _ingest(tasks_doc, canary_key_doc):
            verdicts_doc = _clean_verdicts(tasks_doc, canary_key_doc)
            canaries = canary_key_doc["canaries"]
            ab_id = next(tid for tid, band in canaries.items() if band == "FAIL")
            for v in verdicts_doc["verdicts"]:
                if v["id"] == ab_id:
                    v["score"] = 5  # rubber-stamped
            return verdicts_doc

        with pytest.raises(CanaryAbortError):
            board.run_bounded_board(_DRAFT, ingest_fn=_ingest, N=2)
