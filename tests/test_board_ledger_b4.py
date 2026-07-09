"""tests/test_board_ledger_b4.py — PR-B4 acceptance tests: the
reconciliation ledger + the review->recommend->act->reconcile handshake
(manuscript/board_ledger.py).

Design: docs/superpowers/specs/2026-07-08-autonomous-board-design.md §4,
PR-B4 acceptance criteria (a)/(b)/(c)/(d).
"""
from __future__ import annotations

import pytest

from research_vault.manuscript import board_ledger as bl


def _finding(fid, severity="major", location="§1", **kw):
    d = {
        "finding_id": fid, "severity": severity, "location": location,
        "issue": "issue text", "evidence": "note-1", "recommendation": "fix it",
    }
    d.update(kw)
    return d


# ---------------------------------------------------------------------------
# build_ledger — merge/dedupe/severity-sort
# ---------------------------------------------------------------------------

class TestBuildLedger:
    def test_every_finding_becomes_a_pending_row(self):
        findings_by_axis = {
            "CONTENT": [_finding("f-content-0001")],
            "ADVERS": [_finding("f-advers-0001", severity="critical", location="§2")],
        }
        ledger = bl.build_ledger(findings_by_axis)
        assert len(ledger) == 2
        assert all(r["status"] == bl.PENDING for r in ledger)
        assert all(r["revise_outcome"] is None for r in ledger)

    def test_severity_sorted_critical_first(self):
        findings_by_axis = {
            "CONTENT": [_finding("f1", severity="minor", location="a"),
                        _finding("f2", severity="critical", location="b")],
        }
        ledger = bl.build_ledger(findings_by_axis)
        assert ledger[0]["finding_id"] == "f2"

    def test_dedupe_same_location_across_axes_higher_severity_wins(self):
        findings_by_axis = {
            "CONTENT": [_finding("f-content-0001", severity="minor", location="§3.2 para 2")],
            "ADVERS": [_finding("f-advers-0001", severity="critical", location="§3.2 Para 2")],
        }
        ledger = bl.build_ledger(findings_by_axis)
        assert len(ledger) == 1
        assert ledger[0]["severity"] == "critical"
        assert set(ledger[0]["axes"]) == {"CONTENT", "ADVERS"}


# ---------------------------------------------------------------------------
# PR-B4 acceptance (a): handshake catches a silently-dropped recommendation
# ---------------------------------------------------------------------------

class TestNoSilentDrop:
    def test_pending_survivor_fails_reconcile(self):
        ledger = bl.build_ledger({"CONTENT": [_finding("f1"), _finding("f2", location="§2")]})
        bl.apply_revise_outcome(ledger, "f1", bl.ADDRESSED, how="narrowed scope", edit_location="§1")
        # f2 never gets a revise_outcome -- silently dropped.
        with pytest.raises(bl.LedgerReconcileError):
            bl.reconcile_round1(ledger)

    def test_every_row_addressed_or_rejected_or_escalated_passes(self):
        ledger = bl.build_ledger({
            "CONTENT": [_finding("f1"), _finding("f2", location="§2"), _finding("f3", location="§3")],
        })
        bl.apply_revise_outcome(ledger, "f1", bl.ADDRESSED, how="x", edit_location="§1")
        bl.apply_revise_outcome(ledger, "f2", bl.REJECTED, reject_reason="evidence doesn't exist")
        bl.apply_revise_outcome(ledger, "f3", bl.ESCALATED)
        result = bl.reconcile_round1(ledger)
        assert result["ok"] is True
        assert result["addressed"] == ["f1"]
        assert result["rejected"] == ["f2"]
        assert result["escalated"] == ["f3"]

    def test_rejected_requires_reject_reason(self):
        ledger = bl.build_ledger({"CONTENT": [_finding("f1")]})
        with pytest.raises(ValueError):
            bl.apply_revise_outcome(ledger, "f1", bl.REJECTED)  # no reject_reason -- must fail

    def test_unknown_finding_id_raises(self):
        ledger = bl.build_ledger({"CONTENT": [_finding("f1")]})
        with pytest.raises(KeyError):
            bl.apply_revise_outcome(ledger, "does-not-exist", bl.ADDRESSED)


# ---------------------------------------------------------------------------
# PR-B4 acceptance (b): patch-not-regenerate preserves un-flagged sections
# ---------------------------------------------------------------------------

class TestPatchNotRegenerate:
    def test_surgical_edit_leaves_other_sections_byte_identical(self):
        draft = (
            "## Introduction\nThis is the intro, unflagged.\n\n"
            "## Findings\nThis claim is weak and needs scoping.\n\n"
            "## Conclusion\nThis is the conclusion, unflagged.\n"
        )
        old = "This claim is weak and needs scoping."
        new = "This claim holds in Europe/NA; in China a different regime applies [42]."
        revised = bl.apply_surgical_edit(draft, old, new)

        intro_before = draft.split("## Findings")[0]
        conclusion_before = draft.split("## Conclusion")[1]
        intro_after = revised.split("## Findings")[0]
        conclusion_after = revised.split("## Conclusion")[1]
        assert intro_before == intro_after
        assert conclusion_before == conclusion_after
        assert new in revised
        assert old not in revised

    def test_ambiguous_snippet_raises_not_silently_replaces_first(self):
        draft = "dup dup"
        with pytest.raises(bl.SurgicalEditError):
            bl.apply_surgical_edit(draft, "dup", "x")

    def test_missing_snippet_raises(self):
        draft = "hello world"
        with pytest.raises(bl.SurgicalEditError):
            bl.apply_surgical_edit(draft, "goodbye", "x")

    def test_integrate_by_scoping_verbatim_in_brief(self):
        """The brief's integrate-by-scoping rule must be present verbatim
        so a dispatching hub can inject it without re-deriving the craft."""
        assert "INTEGRATE-BY-SCOPING" in bl.REVISE_AGENT_BRIEF
        assert "NOT APPEND-AS-CAVEAT" in bl.REVISE_AGENT_BRIEF
        assert "REJECT-NOT-FORCE-FIT" in bl.REVISE_AGENT_BRIEF


# ---------------------------------------------------------------------------
# PR-B4 acceptance (c): ADDRESSED-but-STILL-OPEN -> UNRESOLVED
# ---------------------------------------------------------------------------

class TestRound2Verification:
    def test_addressed_row_verified_true_terminates_verified(self):
        ledger = bl.build_ledger({"CONTENT": [_finding("f1")]})
        bl.apply_revise_outcome(ledger, "f1", bl.ADDRESSED, how="x", edit_location="§1")
        bl.apply_verification_result(ledger, "f1", verified=True)
        row = next(r for r in ledger if r["finding_id"] == "f1")
        assert row["status"] == bl.VERIFIED

    def test_addressed_row_still_open_becomes_unresolved(self):
        ledger = bl.build_ledger({"CONTENT": [_finding("f1")]})
        bl.apply_revise_outcome(ledger, "f1", bl.ADDRESSED, how="x", edit_location="§1")
        bl.apply_verification_result(ledger, "f1", verified=False)
        row = next(r for r in ledger if r["finding_id"] == "f1")
        assert row["status"] == bl.UNRESOLVED

    def test_verification_tasks_only_cover_addressed_rows(self):
        ledger = bl.build_ledger({
            "CONTENT": [_finding("f1"), _finding("f2", location="§2"), _finding("f3", location="§3")],
        })
        bl.apply_revise_outcome(ledger, "f1", bl.ADDRESSED, how="x", edit_location="§1")
        bl.apply_revise_outcome(ledger, "f2", bl.REJECTED, reject_reason="bad evidence")
        bl.apply_revise_outcome(ledger, "f3", bl.ESCALATED)
        tasks = bl.build_verification_tasks(ledger)
        assert [t["finding_id"] for t in tasks] == ["f1"]

    def test_cannot_verify_a_non_addressed_row(self):
        ledger = bl.build_ledger({"CONTENT": [_finding("f1")]})
        with pytest.raises(ValueError):
            bl.apply_verification_result(ledger, "f1", verified=True)  # still PENDING


# ---------------------------------------------------------------------------
# PR-B4 acceptance (d): REJECTED row carries a recorded reject_reason
# ---------------------------------------------------------------------------

class TestRejectReasonRecorded:
    def test_reject_reason_is_recorded_on_the_row(self):
        ledger = bl.build_ledger({"CONTENT": [_finding("f1")]})
        bl.apply_revise_outcome(ledger, "f1", bl.REJECTED, reject_reason="the cited note doesn't exist")
        row = next(r for r in ledger if r["finding_id"] == "f1")
        assert row["revise_outcome"]["reject_reason"] == "the cited note doesn't exist"
        assert row["status"] == bl.REJECTED


# ---------------------------------------------------------------------------
# round2_clears — the composite predicate
# ---------------------------------------------------------------------------

class TestRound2Clears:
    def test_clears_when_axes_clear_and_no_unresolved(self):
        ledger = bl.build_ledger({"CONTENT": [_finding("f1")]})
        bl.apply_revise_outcome(ledger, "f1", bl.ADDRESSED, how="x", edit_location="§1")
        bl.apply_verification_result(ledger, "f1", verified=True)
        result = bl.round2_clears({"cleared": True}, ledger)
        assert result["cleared"] is True
        assert result["unresolved"] == []

    def test_does_not_clear_with_unresolved_row(self):
        ledger = bl.build_ledger({"CONTENT": [_finding("f1")]})
        bl.apply_revise_outcome(ledger, "f1", bl.ADDRESSED, how="x", edit_location="§1")
        bl.apply_verification_result(ledger, "f1", verified=False)
        result = bl.round2_clears({"cleared": True}, ledger)
        assert result["cleared"] is False
        assert "f1" in result["unresolved"]

    def test_does_not_clear_when_axes_still_failing(self):
        ledger = bl.build_ledger({"CONTENT": [_finding("f1")]})
        bl.apply_revise_outcome(ledger, "f1", bl.ADDRESSED, how="x", edit_location="§1")
        bl.apply_verification_result(ledger, "f1", verified=True)
        result = bl.round2_clears({"cleared": False}, ledger)
        assert result["cleared"] is False

    def test_ledger_fully_terminal(self):
        ledger = bl.build_ledger({"CONTENT": [_finding("f1"), _finding("f2", location="§2")]})
        bl.apply_revise_outcome(ledger, "f1", bl.ADDRESSED, how="x", edit_location="§1")
        assert bl.ledger_fully_terminal(ledger) is False  # f1 not yet verified, f2 still PENDING
        bl.apply_revise_outcome(ledger, "f2", bl.REJECTED, reject_reason="bad")
        bl.apply_verification_result(ledger, "f1", verified=True)
        assert bl.ledger_fully_terminal(ledger) is True
