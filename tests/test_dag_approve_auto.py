"""test_dag_approve_auto.py — NG-4 §1: `rv dag approve --auto` end-to-end.

Coverage: the three autonomous gates (coverage-gate / approve-framework /
approve-manuscript) resolved by the gate-policy engine, real DAG path
(cmd_approve), not just the unit-level classify_disposition tests in
test_review_autonomy.py.

  1. coverage-gate --auto: saturated -> auto-approved, no human-presence
     check required (RV_APPROVER_TOKEN unset — proves the bypass).
  2. coverage-gate --auto: malformed stop_reason -> auto-rejected
     (HALT-DECLARE), node -> blocked, decision_note carries the reason.
  3. approve-framework --auto: empty spine -> REVISE, rc == 2, node
     REMAINS awaiting-go (no state mutation on REVISE).
  4. approve-protocol NEVER autonomizes even with --auto (falls through to
     the human-presence check, which fails closed with no token).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _cfg_file(tmp_path: Path) -> Path:
    f = tmp_path / "research_vault.toml"
    f.write_text(
        f'instance_root = "{tmp_path}"\n'
        f'notes_root = "{tmp_path / "notes"}"\n'
        f'state_dir = "{tmp_path / "state"}"\n'
        f'agents_dir = "{tmp_path / ".agents"}"\n'
        f'tasks_dir = "{tmp_path / "tasks"}"\n'
        f'control_dir = "{tmp_path / "control"}"\n'
        '[adapters]\nnotifier = "file"\nbackend = "local"\nsecrets = "env"\n'
        '[approval]\nenforce = true\n'
        'token_fingerprint = "d309a810bb5f40cef518202e46d197aa61e4dddafc5984c8c698da29ac8fd2bc"\n'
        'enforce_sig = ""\n',
        encoding="utf-8",
    )
    return f


@pytest.fixture
def run_env(tmp_path: Path, monkeypatch):
    cfg_file = _cfg_file(tmp_path)
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_file))
    # ★ Deliberately UNSET the approver token — proves --auto's autonomous
    # gates never touch check_human_presence, while approve-protocol still
    # requires it (test 4).
    monkeypatch.delenv("RV_APPROVER_TOKEN", raising=False)
    from research_vault.config import reset_config_cache
    reset_config_cache()
    yield tmp_path
    reset_config_cache()


def _saturation_note(path: Path, *, stop_reason: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nstop_reason: {stop_reason}\n---\n\nSaturation curve.\n", encoding="utf-8")


def _coverage_gate_manifest(run_id: str, saturation_path: Path) -> dict:
    return {
        "run_id": run_id,
        "name": "test review",
        "global_cap": 1,
        "nodes": [
            {
                "id": "review-snowball", "type": "agent", "spec": "task://demo#snowball",
                "produces": {"_saturation.md": str(saturation_path)}, "needs": [],
            },
            {
                "id": "coverage-gate", "type": "human-go", "label": "Gate 2",
                "needs": [{"from": "review-snowball", "edge": "afterok"}],
            },
        ],
    }


def _make_awaiting_run(tmp_path: Path, run_id: str, manifest: dict, gate_node_id: str):
    from research_vault.dag.store import RunState, RunStore

    manifest_path = tmp_path / f"{run_id}-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    store = RunStore(tmp_path / "state")
    rs = RunState(run_id=run_id, manifest_path=str(manifest_path))
    rs.init_nodes(manifest)
    for node in manifest["nodes"]:
        if node["id"] != gate_node_id:
            rs.set_node_status(node["id"], "succeeded")
    rs.set_node_status(gate_node_id, "awaiting-go")
    store.create(rs)
    return store


class TestCoverageGateAuto:
    def test_saturated_auto_approves_without_human_presence(self, run_env: Path):
        from research_vault.dag.verbs import cmd_approve

        saturation_path = run_env / "reviews" / "scope-a" / "_saturation.md"
        _saturation_note(saturation_path, stop_reason="saturated")
        manifest = _coverage_gate_manifest("auto-run-1", saturation_path)
        store = _make_awaiting_run(run_env, "auto-run-1", manifest, "coverage-gate")

        args = argparse.Namespace(run_id="auto-run-1", node_id="coverage-gate", auto=True)
        rc = cmd_approve(args)
        assert rc == 0
        rs = store.load("auto-run-1")
        assert rs.node_status("coverage-gate") == "succeeded"
        assert rs.node_states["coverage-gate"]["approved_by"] == "review.autonomy"

    def test_malformed_stop_reason_auto_rejects(self, run_env: Path, capsys):
        from research_vault.dag.verbs import cmd_approve

        saturation_path = run_env / "reviews" / "scope-b" / "_saturation.md"
        _saturation_note(saturation_path, stop_reason="backstop-3-waves")  # non-canonical
        manifest = _coverage_gate_manifest("auto-run-2", saturation_path)
        store = _make_awaiting_run(run_env, "auto-run-2", manifest, "coverage-gate")

        args = argparse.Namespace(run_id="auto-run-2", node_id="coverage-gate", auto=True)
        rc = cmd_approve(args)
        captured = capsys.readouterr()
        # HALT-DECLARE resolves via the reject path -> succeeds mechanically
        # (state write happens) but the node is BLOCKED, not succeeded.
        assert rc == 0
        rs = store.load("auto-run-2")
        assert rs.node_status("coverage-gate") == "blocked"
        assert "HALT-DECLARE" in rs.node_states["coverage-gate"].get("decision_note", "")
        assert "HALT-DECLARE" in captured.err

    def test_backstop_with_declared_residue_goes_with_residue(self, run_env: Path):
        from research_vault.dag.verbs import cmd_approve

        review_dir = run_env / "reviews" / "scope-c"
        saturation_path = review_dir / "_saturation.md"
        _saturation_note(saturation_path, stop_reason="backstop:3-waves")
        (review_dir / "_coverage-gaps.md").write_text("open frontier\n", encoding="utf-8")
        manifest = _coverage_gate_manifest("auto-run-3", saturation_path)
        store = _make_awaiting_run(run_env, "auto-run-3", manifest, "coverage-gate")

        args = argparse.Namespace(run_id="auto-run-3", node_id="coverage-gate", auto=True)
        rc = cmd_approve(args)
        assert rc == 0
        rs = store.load("auto-run-3")
        assert rs.node_status("coverage-gate") == "succeeded"


class TestFrameworkGateAuto:
    def _framework_manifest(self, run_id: str) -> dict:
        return {
            "run_id": run_id, "name": "ms", "global_cap": 1,
            "nodes": [
                {"id": "framework-propose", "type": "agent", "spec": "task://demo#fw", "needs": []},
                {
                    "id": "approve-framework", "type": "human-go",
                    "needs": [{"from": "framework-propose", "edge": "afterok"}],
                },
            ],
        }

    def test_empty_spine_revises_without_state_change(self, run_env: Path, capsys):
        from research_vault.dag.verbs import cmd_approve

        # _manuscript.md sits next to the manifest (manifest_path.parent)
        (run_env / "_manuscript.md").write_text(
            "---\ntitle: t\n---\n\nno spine_shape here\n", encoding="utf-8",
        )
        manifest = self._framework_manifest("auto-run-4")
        store = _make_awaiting_run(run_env, "auto-run-4", manifest, "approve-framework")

        args = argparse.Namespace(run_id="auto-run-4", node_id="approve-framework", auto=True)
        rc = cmd_approve(args)
        captured = capsys.readouterr()
        assert rc == 2
        assert "REVISE" in captured.err
        rs = store.load("auto-run-4")
        # No state mutation on REVISE — the node stays awaiting-go.
        assert rs.node_status("approve-framework") == "awaiting-go"


class TestApproveProtocolNeverAutonomizes:
    def test_approve_protocol_ignores_auto_and_fails_closed(self, run_env: Path, capsys):
        from research_vault.dag.verbs import cmd_approve

        protocol_path = run_env / "reviews" / "scope-d" / "_protocol.md"
        protocol_path.parent.mkdir(parents=True, exist_ok=True)
        protocol_path.write_text(
            "---\ncounter-position: a real counter-position\n---\n\nprotocol\n", encoding="utf-8",
        )
        manifest = {
            "run_id": "auto-run-5", "name": "review", "global_cap": 1,
            "nodes": [
                {
                    "id": "review-scope", "type": "agent", "spec": "task://demo#scope",
                    "produces": {"_protocol.md": str(protocol_path)}, "needs": [],
                },
                {
                    "id": "approve-protocol", "type": "human-go",
                    "needs": [{"from": "review-scope", "edge": "afterok"}],
                },
            ],
        }
        store = _make_awaiting_run(run_env, "auto-run-5", manifest, "approve-protocol")

        args = argparse.Namespace(run_id="auto-run-5", node_id="approve-protocol", auto=True)
        rc = cmd_approve(args)
        captured = capsys.readouterr()
        # approve-protocol is NEVER in _AUTONOMOUS_GATE_IDS -> falls through
        # to check_human_presence, which fails closed with no token/TTY.
        assert rc == 1
        rs = store.load("auto-run-5")
        assert rs.node_status("approve-protocol") == "awaiting-go"
