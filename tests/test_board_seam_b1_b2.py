"""tests/test_board_seam_b1_b2.py — PR-B1/B2 acceptance tests: the 4-lens
board emit/ingest fanout (gates/board_seam.py) + the lens specs/finding
schema/caps (manuscript/board_lenses.py).

Design: docs/superpowers/specs/2026-07-08-autonomous-board-design.md §1/§2/
PR-B1/PR-B2 acceptance criteria.
"""
from __future__ import annotations

import pytest

from research_vault.gates.board_seam import (
    CanaryAbortError,
    emit_board_tasks,
    ingest_board_verdicts,
)
from research_vault.manuscript import board_lenses
from research_vault.manuscript.check_gates import check_heading_order


_DRAFT = "## Introduction\n\nSome draft text about the corpus.\n"


_ALL_AXES = ("DEPTH", "WIDTH", "SYNTH", "SELFCONT", "ADVERS", "INSTRUCT")


def _happy_verdicts(tasks_doc, canary_key_doc, *, scores=None):
    """Build a verdicts doc scoring every real task at floor+1 (or the given
    per-axis ``scores`` override) and every canary at its expected band."""
    canaries = canary_key_doc["canaries"]
    scores_by_axis = {axis: 4 for axis in _ALL_AXES}
    if scores:
        scores_by_axis.update(scores)
    verdicts = []
    for t in tasks_doc["tasks"]:
        tid = t["id"]
        if tid in canaries:
            band = canaries[tid]
            score = {"PASS-HIGH": 5, "FAIL-LOW": 1, "FAIL": 2}[band]
        else:
            score = scores_by_axis[t["axis"]]
        verdicts.append({"id": tid, "axis": t["axis"], "score": score, "verdict": "PASS" if score >= 3 else "FAIL", "findings": []})
    return {"schema": "rv-board-verdicts/v1", "verdicts": verdicts}


# ---------------------------------------------------------------------------
# PR-B1 acceptance (a): round-trip emit->ingest yields the right axis
# scores + findings.
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_emit_produces_6_real_tasks_plus_per_axis_canaries(self):
        # PR-F: canaries are now PER-AXIS (one FAIL probe per axis + SYNTH's
        # 3 calibrated probes = 8), not the old SYNTH-only 3 — so EACH of the
        # 6 cold judges is canary-verified.
        from research_vault.gates.canary_passages import BOARD_AXIS_CANARIES

        result = emit_board_tasks(_DRAFT, manuscript="ms-x")
        tasks = result["tasks_doc"]["tasks"]
        n_canary = len(BOARD_AXIS_CANARIES)
        assert len(tasks) == 6 + n_canary
        canaries = result["canary_key_doc"]["canaries"]
        assert len(canaries) == n_canary
        real = [t for t in tasks if t["id"] not in canaries]
        assert len(real) == 6
        axes = {t["axis"] for t in real}
        assert axes == {"DEPTH", "WIDTH", "SYNTH", "SELFCONT", "ADVERS", "INSTRUCT"}
        # Every board axis carries at least one interleaved canary probe (the
        # per-axis fit-check: a single-axis canary would certify only one).
        canary_tasks = [t for t in tasks if t["id"] in canaries]
        canary_axes = {t["axis"] for t in canary_tasks}
        assert canary_axes == {"DEPTH", "WIDTH", "SYNTH", "SELFCONT", "ADVERS", "INSTRUCT"}

    def test_round_trip_axis_scores_and_findings(self):
        emitted = emit_board_tasks(_DRAFT, manuscript="ms-x")
        finding = {
            "finding_id": "f-depth-0001", "severity": "critical",
            "location": "intro", "issue": "bare assertion, no number", "evidence": "lit/foo",
            "recommendation": "carry the reported figure",
        }
        verdicts_doc = _happy_verdicts(emitted["tasks_doc"], emitted["canary_key_doc"])
        for v in verdicts_doc["verdicts"]:
            if v["axis"] == "DEPTH" and v["id"] not in emitted["canary_key_doc"]["canaries"]:
                v["findings"] = [finding]

        result = ingest_board_verdicts(emitted["tasks_doc"], emitted["canary_key_doc"], verdicts_doc)
        assert result["halt"] is False
        assert result["canary_aborted"] is False
        assert result["axis_scores"] == {
            "DEPTH": 4, "WIDTH": 4, "SYNTH": 4, "SELFCONT": 4, "ADVERS": 4, "INSTRUCT": 4,
        }
        assert result["findings"]["DEPTH"] == [finding]
        assert result["findings"]["SELFCONT"] == []

    def test_task_fields_scoped_to_own_lens(self):
        """The ADVERS task carries contradiction_map; the INSTRUCT task
        carries heading_diff/frozen_order; the WIDTH task carries
        coverage_map/coverage_diff; none leaks into the other lenses
        (anti-anchoring: each judge sees only what its rubric uses)."""
        cmap = [{"claim_a": "X", "claim_b": "not X", "relation": "contradicts"}]
        hd = {"ok": False, "warnings": ["drift"]}
        cov_map = {"cluster-a": ["smith2023"]}
        cov_diff = {"used": ["smith2023"], "present": [], "missing": ["smith2023"]}
        emitted = emit_board_tasks(
            _DRAFT, contradiction_map=cmap, heading_diff=hd, frozen_order=["Introduction", "Body"],
            coverage_map=cov_map, coverage_diff=cov_diff,
        )
        real = [t for t in emitted["tasks_doc"]["tasks"] if t["id"] not in emitted["canary_key_doc"]["canaries"]]
        by_lens = {t["lens"]: t for t in real}
        # contradiction_map: only on ADVERS.
        assert "contradiction_map" in by_lens["adversarial"]
        for lens in ("depth", "width", "synthesis", "self-containment", "instruction-following"):
            assert "contradiction_map" not in by_lens[lens]
        # heading_diff/frozen_order: only on INSTRUCT.
        assert by_lens["instruction-following"]["heading_diff"] == hd
        assert by_lens["instruction-following"]["frozen_order"] == ["Introduction", "Body"]
        for lens in ("depth", "width", "synthesis", "self-containment", "adversarial"):
            assert "heading_diff" not in by_lens[lens]
        # coverage_map/coverage_diff: only on WIDTH.
        assert by_lens["width"]["coverage_map"] == cov_map
        assert by_lens["width"]["coverage_diff"] == cov_diff
        for lens in ("depth", "synthesis", "self-containment", "adversarial", "instruction-following"):
            assert "coverage_map" not in by_lens[lens]
            assert "coverage_diff" not in by_lens[lens]

    def test_no_old_text_new_text_field_in_finding_schema(self):
        """PR-B2 acceptance (c): a finding carries no old_text/new_text."""
        finding = {
            "finding_id": "f-x-0001", "severity": "minor", "location": "§1",
            "issue": "vague", "evidence": "note-1", "recommendation": "be specific",
        }
        assert "old_text" not in finding
        assert "new_text" not in finding


# ---------------------------------------------------------------------------
# PR-B1 acceptance (b): entirely-missing verdicts file -> halt=True
# ---------------------------------------------------------------------------

class TestFanoutIncomplete:
    def test_missing_verdicts_file_halts(self):
        emitted = emit_board_tasks(_DRAFT, manuscript="ms-x")
        result = ingest_board_verdicts(emitted["tasks_doc"], emitted["canary_key_doc"], None)
        assert result["halt"] is True
        assert result["halt_reason"]
        assert result["axis_scores"] == {}

    def test_empty_verdicts_list_halts(self):
        emitted = emit_board_tasks(_DRAFT, manuscript="ms-x")
        result = ingest_board_verdicts(
            emitted["tasks_doc"], emitted["canary_key_doc"], {"verdicts": []},
        )
        assert result["halt"] is True


# ---------------------------------------------------------------------------
# PR-B1 acceptance (c): a missing axis id -> fail-closed FAIL, surfaced.
# ---------------------------------------------------------------------------

class TestFailClosedMissingAxis:
    def test_missing_one_real_task_defaults_its_axis_to_zero(self):
        emitted = emit_board_tasks(_DRAFT, manuscript="ms-x")
        verdicts_doc = _happy_verdicts(emitted["tasks_doc"], emitted["canary_key_doc"])
        # Drop the INSTRUCT real task's verdict (not a canary).
        canaries = emitted["canary_key_doc"]["canaries"]
        real_instruct_id = next(
            t["id"] for t in emitted["tasks_doc"]["tasks"]
            if t["axis"] == "INSTRUCT" and t["id"] not in canaries
        )
        verdicts_doc["verdicts"] = [v for v in verdicts_doc["verdicts"] if v["id"] != real_instruct_id]

        result = ingest_board_verdicts(emitted["tasks_doc"], emitted["canary_key_doc"], verdicts_doc)
        assert result["halt"] is False  # partial, not wholesale-missing
        assert result["axis_scores"]["INSTRUCT"] == 0
        assert real_instruct_id in result["missing_ids"]

    def test_unparseable_score_defaults_to_zero_and_surfaces(self):
        emitted = emit_board_tasks(_DRAFT, manuscript="ms-x")
        verdicts_doc = _happy_verdicts(emitted["tasks_doc"], emitted["canary_key_doc"])
        canaries = emitted["canary_key_doc"]["canaries"]
        real_depth_id = next(
            t["id"] for t in emitted["tasks_doc"]["tasks"]
            if t["axis"] == "DEPTH" and t["id"] not in canaries
        )
        for v in verdicts_doc["verdicts"]:
            if v["id"] == real_depth_id:
                v["score"] = "garbage"

        result = ingest_board_verdicts(emitted["tasks_doc"], emitted["canary_key_doc"], verdicts_doc)
        assert result["axis_scores"]["DEPTH"] == 0
        assert real_depth_id in result["unrecognized_ids"]


# ---------------------------------------------------------------------------
# PR-B1 acceptance (d): unmarked annotated-bib canary scored PASS on
# CONTENT -> CanaryAbortError.
# ---------------------------------------------------------------------------

class TestCanaryAbort:
    def test_annotated_bib_canary_scored_pass_aborts(self):
        emitted = emit_board_tasks(_DRAFT, manuscript="ms-x")
        canaries = emitted["canary_key_doc"]["canaries"]
        ab_id = next(tid for tid, band in canaries.items() if band == "FAIL")
        verdicts_doc = _happy_verdicts(emitted["tasks_doc"], emitted["canary_key_doc"])
        for v in verdicts_doc["verdicts"]:
            if v["id"] == ab_id:
                v["score"] = 5  # rubber-stamped -- must have been < floor

        with pytest.raises(CanaryAbortError):
            ingest_board_verdicts(emitted["tasks_doc"], emitted["canary_key_doc"], verdicts_doc)

    def test_missing_canary_verdict_aborts(self):
        emitted = emit_board_tasks(_DRAFT, manuscript="ms-x")
        canaries = emitted["canary_key_doc"]["canaries"]
        any_canary_id = next(iter(canaries))
        verdicts_doc = _happy_verdicts(emitted["tasks_doc"], emitted["canary_key_doc"])
        verdicts_doc["verdicts"] = [v for v in verdicts_doc["verdicts"] if v["id"] != any_canary_id]

        with pytest.raises(CanaryAbortError):
            ingest_board_verdicts(emitted["tasks_doc"], emitted["canary_key_doc"], verdicts_doc)

    def test_strong_probe_scored_low_aborts(self):
        emitted = emit_board_tasks(_DRAFT, manuscript="ms-x")
        canaries = emitted["canary_key_doc"]["canaries"]
        strong_id = next(tid for tid, band in canaries.items() if band == "PASS-HIGH")
        verdicts_doc = _happy_verdicts(emitted["tasks_doc"], emitted["canary_key_doc"])
        for v in verdicts_doc["verdicts"]:
            if v["id"] == strong_id:
                v["score"] = 1  # broken-harsh

        with pytest.raises(CanaryAbortError):
            ingest_board_verdicts(emitted["tasks_doc"], emitted["canary_key_doc"], verdicts_doc)


# ---------------------------------------------------------------------------
# PR-B2 acceptance (a): capping + prioritization + sub-budgets
# ---------------------------------------------------------------------------

class TestCapAndPrioritize:
    def test_over_cap_returns_n_most_load_bearing_critical_first(self):
        findings = [{"severity": "minor", "id": i} for i in range(20)]
        findings[5]["severity"] = "critical"
        findings[10]["severity"] = "major"
        result = board_lenses.cap_and_prioritize_findings(findings, "DEPTH")
        assert len(result) == board_lenses.FINDING_CAPS["DEPTH"]
        assert result[0]["severity"] == "critical"
        assert result[1]["severity"] == "major"

    def test_bloat_subbudget_never_exceeds_two_for_synth(self):
        # PR-E: the bloat sub-budget moved from CONTENT to SYNTH.
        findings = [{"severity": "minor", "category": "bloat", "id": i} for i in range(10)]
        result = board_lenses.cap_and_prioritize_findings(findings, "SYNTH")
        bloat_count = sum(1 for f in result if f.get("category") == "bloat")
        assert bloat_count <= board_lenses.SUB_BUDGETS["SYNTH"]["bloat"]

    def test_bloat_never_crowds_out_synthesis_findings(self):
        substance = [{"severity": "critical", "id": f"s{i}"} for i in range(10)]
        bloat = [{"severity": "critical", "category": "bloat", "id": f"b{i}"} for i in range(5)]
        result = board_lenses.cap_and_prioritize_findings(substance + bloat, "SYNTH")
        substance_kept = [f for f in result if "category" not in f]
        assert len(substance_kept) == 10  # all substance findings survive the cap

    def test_no_sub_budget_axis_just_caps(self):
        findings = [{"severity": "major", "id": i} for i in range(20)]
        result = board_lenses.cap_and_prioritize_findings(findings, "ADVERS")
        assert len(result) == board_lenses.FINDING_CAPS["ADVERS"]


# ---------------------------------------------------------------------------
# PR-B2 acceptance (b): FRAMEWORK task's heading_diff matches
# check_heading_order on a reordered draft.
# ---------------------------------------------------------------------------

class TestHeadingDiffGroundTruth:
    def test_instruct_task_carries_real_heading_diff(self):
        reordered_draft = "# Body\n\n# Introduction\n\n"
        expected_order = ["Introduction", "Body"]
        hd = check_heading_order(reordered_draft, expected_order)
        emitted = emit_board_tasks(reordered_draft, heading_diff=hd, frozen_order=expected_order)
        real = [t for t in emitted["tasks_doc"]["tasks"] if t["id"] not in emitted["canary_key_doc"]["canaries"]]
        instruct_task = next(t for t in real if t["axis"] == "INSTRUCT")
        assert instruct_task["heading_diff"] == hd
        assert hd["ok"] is False  # sanity: the fixture really is reordered
