"""test_gap_coverage_gate.py — the spine's second mechanical gate:
findings cover gaps (0.3.2).

Coverage:
  1. A gap ANSWERED by a finding -> closed, gate PASSes.
  2. A gap neither answered nor disposed -> open_uncovered, gate BLOCKs.
  3. A gap with disposition: leaves-open + a reason -> leaves_open, PASS.
  4. A gap with disposition: leaves-open but NO reason -> malformed,
     still counted as open_uncovered (never a silent escape hatch).
  5. closed-status / proven-open / promoted gaps are out of scope
     (never counted in any bucket).
  6. Missing gaps/ dir -> vacuous PASS.
  7. Multiple gaps mixed dispositions classify independently.
  8. An ADDRESSES edge (experiment->gap) does NOT satisfy coverage — only
     a finding's ANSWERS edge does (ADDRESSES marks "targeted", not "closed").

All tests hermetic — no ~/vault, no real cluster, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.review.gap_coverage_gate import check_gap_coverage_gate


def _write_gap(pnd: Path, gap_id: str, *, status: str = "open", extra_fm: str = "") -> Path:
    p = pnd / "gaps" / f"{gap_id}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\ntype: gaps\nid: {gap_id}\nstatus: {status}\n{extra_fm}---\n\n# Gap\n",
        encoding="utf-8",
    )
    return p


def _write_finding(pnd: Path, finding_id: str, *, body: str) -> Path:
    p = pnd / "findings" / f"{finding_id}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\ntype: findings\n---\n\n{body}\n", encoding="utf-8")
    return p


class TestGapCoverageGate:
    def test_answered_gap_is_closed(self, tmp_path):
        _write_gap(tmp_path, "q1-gap-main1")
        _write_finding(
            tmp_path, "q1-main1",
            body="## Provenance\n\n- [q1-gap-main1](/gaps/q1-gap-main1.md) — ANSWERS: main1 confirms the claim\n",
        )
        result = check_gap_coverage_gate(tmp_path)
        assert result["ok"] is True
        assert result["closed"] == ["q1-gap-main1"]
        assert result["open_uncovered"] == []

    def test_uncovered_gap_blocks(self, tmp_path):
        _write_gap(tmp_path, "q1-gap-main2")
        result = check_gap_coverage_gate(tmp_path)
        assert result["ok"] is False
        assert result["open_uncovered"] == ["q1-gap-main2"]

    def test_leaves_open_with_reason_passes(self, tmp_path):
        _write_gap(
            tmp_path, "q1-gap-future",
            extra_fm="disposition: leaves-open\ndisposition_reason: \"out of budget for this program; revisit next cycle\"\n",
        )
        result = check_gap_coverage_gate(tmp_path)
        assert result["ok"] is True
        assert result["leaves_open"] == ["q1-gap-future"]
        assert result["open_uncovered"] == []

    def test_leaves_open_without_reason_is_malformed_and_blocks(self, tmp_path):
        _write_gap(
            tmp_path, "q1-gap-bad",
            extra_fm="disposition: leaves-open\n",
        )
        result = check_gap_coverage_gate(tmp_path)
        assert result["ok"] is False
        assert "q1-gap-bad" in result["malformed_disposition"]
        assert "q1-gap-bad" in result["open_uncovered"]

    def test_closed_status_gaps_out_of_scope(self, tmp_path):
        _write_gap(tmp_path, "q1-gap-old", status="closed-supported")
        result = check_gap_coverage_gate(tmp_path)
        assert result["ok"] is True
        assert result["open_uncovered"] == []
        assert result["closed"] == []  # not even counted as "closed" here — out of scope entirely

    def test_proven_open_and_promoted_out_of_scope(self, tmp_path):
        _write_gap(tmp_path, "q1-gap-po", status="proven-open")
        _write_gap(tmp_path, "q1-gap-pr", status="promoted")
        result = check_gap_coverage_gate(tmp_path)
        assert result["ok"] is True
        assert result["open_uncovered"] == []

    def test_missing_gaps_dir_is_vacuous_pass(self, tmp_path):
        result = check_gap_coverage_gate(tmp_path)
        assert result["ok"] is True
        assert result["open_uncovered"] == []

    def test_reopened_gap_is_actionable(self, tmp_path):
        _write_gap(tmp_path, "q1-gap-reopen", status="reopened")
        result = check_gap_coverage_gate(tmp_path)
        assert result["ok"] is False
        assert result["open_uncovered"] == ["q1-gap-reopen"]

    def test_mixed_dispositions_classify_independently(self, tmp_path):
        _write_gap(tmp_path, "gap-closed")
        _write_finding(
            tmp_path, "f1",
            body="- [gap-closed](/gaps/gap-closed.md) — ANSWERS: closes it\n",
        )
        _write_gap(tmp_path, "gap-open")
        _write_gap(
            tmp_path, "gap-leaves-open",
            extra_fm="disposition: leaves-open\ndisposition_reason: \"deferred\"\n",
        )
        result = check_gap_coverage_gate(tmp_path)
        assert result["ok"] is False
        assert result["closed"] == ["gap-closed"]
        assert result["leaves_open"] == ["gap-leaves-open"]
        assert result["open_uncovered"] == ["gap-open"]

    def test_addresses_edge_alone_does_not_close_gap(self, tmp_path):
        """ADDRESSES (experiment->gap) marks a gap as TARGETED, not closed
        — only a finding's ANSWERS edge closes it. This is the
        gap-closed-vs-gap-targeted distinction the gate must honor."""
        _write_gap(tmp_path, "q1-gap-main1")
        exp_dir = tmp_path / "experiments"
        exp_dir.mkdir(parents=True)
        (exp_dir / "q1-main1.md").write_text(
            "---\ntype: experiments\n---\n\n"
            "## Provenance\n\n- [q1-gap-main1](/gaps/q1-gap-main1.md) — ADDRESSES: this experiment targets the gap\n",
            encoding="utf-8",
        )
        result = check_gap_coverage_gate(tmp_path)
        assert result["ok"] is False
        assert result["open_uncovered"] == ["q1-gap-main1"]
