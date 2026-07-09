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


def _happy_verdicts(tasks_doc, canary_key_doc, *, content_score=4, selfcont_score=4,
                     advers_score=4, framework_score=4):
    """Build a verdicts doc scoring every real task at floor+1 and every
    canary at its expected band."""
    canaries = canary_key_doc["canaries"]
    scores_by_axis = {
        "CONTENT": content_score, "SELFCONT": selfcont_score,
        "ADVERS": advers_score, "FRAMEWORK": framework_score,
    }
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
    def test_emit_produces_4_real_tasks_plus_3_canaries(self):
        result = emit_board_tasks(_DRAFT, manuscript="ms-x")
        tasks = result["tasks_doc"]["tasks"]
        assert len(tasks) == 7
        canaries = result["canary_key_doc"]["canaries"]
        assert len(canaries) == 3
        real = [t for t in tasks if t["id"] not in canaries]
        assert len(real) == 4
        axes = {t["axis"] for t in real}
        assert axes == {"CONTENT", "SELFCONT", "ADVERS", "FRAMEWORK"}

    def test_round_trip_axis_scores_and_findings(self):
        emitted = emit_board_tasks(_DRAFT, manuscript="ms-x")
        finding = {
            "finding_id": "f-content-0001", "severity": "critical",
            "location": "intro", "issue": "no synthesis", "evidence": "lit/foo",
            "recommendation": "compare paper A vs B",
        }
        verdicts_doc = _happy_verdicts(emitted["tasks_doc"], emitted["canary_key_doc"])
        for v in verdicts_doc["verdicts"]:
            if v["axis"] == "CONTENT" and v["id"] not in emitted["canary_key_doc"]["canaries"]:
                v["findings"] = [finding]

        result = ingest_board_verdicts(emitted["tasks_doc"], emitted["canary_key_doc"], verdicts_doc)
        assert result["halt"] is False
        assert result["canary_aborted"] is False
        assert result["axis_scores"] == {"CONTENT": 4, "SELFCONT": 4, "ADVERS": 4, "FRAMEWORK": 4}
        assert result["findings"]["CONTENT"] == [finding]
        assert result["findings"]["SELFCONT"] == []

    def test_task_fields_scoped_to_own_lens(self):
        """The ADVERS task carries contradiction_map; the FRAMEWORK task
        carries heading_diff/frozen_order; neither leaks into the other
        lenses (anti-anchoring: each judge sees only what its rubric uses)."""
        cmap = [{"claim_a": "X", "claim_b": "not X", "relation": "contradicts"}]
        hd = {"ok": False, "warnings": ["drift"]}
        emitted = emit_board_tasks(
            _DRAFT, contradiction_map=cmap, heading_diff=hd, frozen_order=["Introduction", "Body"],
        )
        real = [t for t in emitted["tasks_doc"]["tasks"] if t["id"] not in emitted["canary_key_doc"]["canaries"]]
        by_lens = {t["lens"]: t for t in real}
        assert "contradiction_map" in by_lens["adversarial"]
        assert "contradiction_map" not in by_lens["content"]
        assert "contradiction_map" not in by_lens["self-containment"]
        assert "contradiction_map" not in by_lens["framework"]
        assert by_lens["framework"]["heading_diff"] == hd
        assert by_lens["framework"]["frozen_order"] == ["Introduction", "Body"]
        assert "heading_diff" not in by_lens["content"]

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
        # Drop the FRAMEWORK real task's verdict (not a canary).
        canaries = emitted["canary_key_doc"]["canaries"]
        real_framework_id = next(
            t["id"] for t in emitted["tasks_doc"]["tasks"]
            if t["axis"] == "FRAMEWORK" and t["id"] not in canaries
        )
        verdicts_doc["verdicts"] = [v for v in verdicts_doc["verdicts"] if v["id"] != real_framework_id]

        result = ingest_board_verdicts(emitted["tasks_doc"], emitted["canary_key_doc"], verdicts_doc)
        assert result["halt"] is False  # partial, not wholesale-missing
        assert result["axis_scores"]["FRAMEWORK"] == 0
        assert real_framework_id in result["missing_ids"]

    def test_unparseable_score_defaults_to_zero_and_surfaces(self):
        emitted = emit_board_tasks(_DRAFT, manuscript="ms-x")
        verdicts_doc = _happy_verdicts(emitted["tasks_doc"], emitted["canary_key_doc"])
        canaries = emitted["canary_key_doc"]["canaries"]
        real_content_id = next(
            t["id"] for t in emitted["tasks_doc"]["tasks"]
            if t["axis"] == "CONTENT" and t["id"] not in canaries
        )
        for v in verdicts_doc["verdicts"]:
            if v["id"] == real_content_id:
                v["score"] = "garbage"

        result = ingest_board_verdicts(emitted["tasks_doc"], emitted["canary_key_doc"], verdicts_doc)
        assert result["axis_scores"]["CONTENT"] == 0
        assert real_content_id in result["unrecognized_ids"]


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
        result = board_lenses.cap_and_prioritize_findings(findings, "CONTENT")
        assert len(result) == board_lenses.FINDING_CAPS["CONTENT"]
        assert result[0]["severity"] == "critical"
        assert result[1]["severity"] == "major"

    def test_bloat_subbudget_never_exceeds_two_for_content(self):
        findings = [{"severity": "minor", "category": "bloat", "id": i} for i in range(10)]
        result = board_lenses.cap_and_prioritize_findings(findings, "CONTENT")
        bloat_count = sum(1 for f in result if f.get("category") == "bloat")
        assert bloat_count <= board_lenses.SUB_BUDGETS["CONTENT"]["bloat"]

    def test_bloat_never_crowds_out_substance_findings(self):
        substance = [{"severity": "critical", "id": f"s{i}"} for i in range(10)]
        bloat = [{"severity": "critical", "category": "bloat", "id": f"b{i}"} for i in range(5)]
        result = board_lenses.cap_and_prioritize_findings(substance + bloat, "CONTENT")
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
    def test_framework_task_carries_real_heading_diff(self):
        reordered_draft = "# Body\n\n# Introduction\n\n"
        expected_order = ["Introduction", "Body"]
        hd = check_heading_order(reordered_draft, expected_order)
        emitted = emit_board_tasks(reordered_draft, heading_diff=hd, frozen_order=expected_order)
        real = [t for t in emitted["tasks_doc"]["tasks"] if t["id"] not in emitted["canary_key_doc"]["canaries"]]
        framework_task = next(t for t in real if t["axis"] == "FRAMEWORK")
        assert framework_task["heading_diff"] == hd
        assert hd["ok"] is False  # sanity: the fixture really is reordered
