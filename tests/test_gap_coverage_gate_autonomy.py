"""test_gap_coverage_gate_autonomy.py — gap-coverage-gate wired as an
AUTONOMOUS gate (0.3.2): the DAG's self-advancing runner
resolves it exactly like coverage-gate/approve-framework/approve-review,
no human keypress needed, via the same dispatch
(_evaluate_autonomous_gate / _AUTONOMOUS_GATE_IDS).

Coverage:
  1. Every open gap answered/leaves-open -> GO.
  2. An uncovered open gap -> HALT-DECLARE.
  3. A manifest with no top-level 'project' field -> HALT-DECLARE (cannot
     resolve project_notes_dir — never guesses).
  4. "gap-coverage-gate" is a member of _AUTONOMOUS_GATE_IDS (no human
     keypress needed).

All tests hermetic — no ~/vault, no real cluster, no network.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import load_config
from research_vault.dag.store import RunState
from research_vault.dag.verbs import _AUTONOMOUS_GATE_IDS, _evaluate_autonomous_gate
from research_vault.review import autonomy as _autonomy


def _write_gap(pnd: Path, gap_id: str, *, extra_fm: str = "") -> None:
    p = pnd / "gaps" / f"{gap_id}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\ntype: gaps\nstatus: open\n{extra_fm}---\n\n# Gap\n", encoding="utf-8")


def _write_finding(pnd: Path, finding_id: str, *, body: str) -> None:
    p = pnd / "findings" / f"{finding_id}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\ntype: findings\n---\n\n{body}\n", encoding="utf-8")


class TestGapCoverageGateAutonomy:
    def test_registered_as_autonomous_gate(self):
        assert "gap-coverage-gate" in _AUTONOMOUS_GATE_IDS

    def test_all_gaps_covered_resolves_go(self, tmp_instance):
        cfg = load_config()
        pnd = cfg.project_notes_dir("demo-research")
        _write_gap(pnd, "q1-gap-main1")
        _write_finding(
            pnd, "q1-main1",
            body="- [q1-gap-main1](/gaps/q1-gap-main1.md) — ANSWERS: closes it\n",
        )
        manifest = {"run_id": "research-loop-q1", "project": "demo-research", "nodes": []}
        run_state = RunState(run_id="r1", manifest_path="x.json", created_at=time.time())

        disposition = _evaluate_autonomous_gate(
            "gap-coverage-gate", {}, Path("x.json"), run_state, manifest=manifest,
        )
        assert disposition.disposition == _autonomy.GO

    def test_uncovered_gap_resolves_halt_declare(self, tmp_instance):
        cfg = load_config()
        pnd = cfg.project_notes_dir("demo-research")
        _write_gap(pnd, "q1-gap-main2")
        manifest = {"run_id": "research-loop-q1", "project": "demo-research", "nodes": []}
        run_state = RunState(run_id="r2", manifest_path="x.json", created_at=time.time())

        disposition = _evaluate_autonomous_gate(
            "gap-coverage-gate", {}, Path("x.json"), run_state, manifest=manifest,
        )
        assert disposition.disposition == _autonomy.HALT_DECLARE
        assert "q1-gap-main2" in disposition.reason

    def test_missing_project_field_halts(self, tmp_instance):
        manifest = {"run_id": "research-loop-q1", "nodes": []}  # no "project"
        run_state = RunState(run_id="r3", manifest_path="x.json", created_at=time.time())

        disposition = _evaluate_autonomous_gate(
            "gap-coverage-gate", {}, Path("x.json"), run_state, manifest=manifest,
        )
        assert disposition.disposition == _autonomy.HALT_DECLARE
        assert "project" in disposition.reason.lower()

    def test_leaves_open_gap_resolves_go(self, tmp_instance):
        cfg = load_config()
        pnd = cfg.project_notes_dir("demo-research")
        _write_gap(
            pnd, "q1-gap-future",
            extra_fm="disposition: leaves-open\ndisposition_reason: \"deferred to next cycle\"\n",
        )
        manifest = {"run_id": "research-loop-q1", "project": "demo-research", "nodes": []}
        run_state = RunState(run_id="r4", manifest_path="x.json", created_at=time.time())

        disposition = _evaluate_autonomous_gate(
            "gap-coverage-gate", {}, Path("x.json"), run_state, manifest=manifest,
        )
        assert disposition.disposition == _autonomy.GO
