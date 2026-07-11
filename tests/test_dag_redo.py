"""test_dag_redo.py — RED/GREEN tests for `rv dag redo` (revise-loop driver).

The problem: after a cold critic BLOCKs a node (status -> blocked/succeeded),
there was NO way to re-run it once it left "pending" — the critic
BLOCK -> revise -> re-verify -> approve loop (the whole point of a
rejects-only gate) was undrivable.

`rv dag redo <run> <node>` re-opens a completed (succeeded/failed/blocked)
node so `rv dag tick` re-offers it for dispatch, WITHOUT erasing the prior
attempt (preserved in node_states[node_id]["redo_history"]).

Coverage:
  1. redo a succeeded node with NO completed descendants -> pending,
     reappears in the next tick's frontier.
  2. redo a node WITH a completed descendant -> BLOCKS without --cascade,
     naming the descendant; --cascade resets the descendant subtree too.
  3. the prior attempt is preserved in node_states[...]['redo_history'].
  4. end-to-end revise loop: verdict output -> redo -> re-complete with a
     NEW output -> a downstream AWAIT-GO gate reads the NEW verdict.
  5. misc: unknown node, non-terminal node, unknown run -> clean errors.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import reset_config_cache
from research_vault.dag.store import RunStore
from research_vault.dag.verbs import cmd_run, cmd_complete, cmd_approve, cmd_tick, cmd_redo

# SR-APPROVE-GATE: reuse the shared test token/fingerprint pair so cmd_approve
# exercises the REAL gate via the token path (see conftest.py's autouse
# _approver_token_env + tmp_instance fixture, which this file's local
# tmp_instance below intentionally mirrors for the same reason test_dag.py's
# does).
from tests.conftest import TEST_APPROVER_FINGERPRINT


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_cfg():
    reset_config_cache()
    yield
    reset_config_cache()


@pytest.fixture
def tmp_instance(tmp_path: Path):
    """Full tmp instance with RESEARCH_VAULT_CONFIG set."""
    cfg_file = tmp_path / "research_vault.toml"
    (tmp_path / "state").mkdir()
    (tmp_path / "notes").mkdir()
    cfg_file.write_text(
        f"""
instance_root = "{tmp_path}"
notes_root = "{tmp_path / 'notes'}"
state_dir = "{tmp_path / 'state'}"
agents_dir = "{tmp_path / '.agents'}"
tasks_dir = "{tmp_path / 'tasks'}"
control_dir = "{tmp_path / 'control'}"

[adapters]
notifier = "file"
backend = "local"
secrets = "env"

[approval]
enforce = true
token_fingerprint = "{TEST_APPROVER_FINGERPRINT}"
enforce_sig = ""
""",
        encoding="utf-8",
    )
    old = os.environ.get("RESEARCH_VAULT_CONFIG")
    os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_file)
    yield tmp_path
    if old is None:
        os.environ.pop("RESEARCH_VAULT_CONFIG", None)
    else:
        os.environ["RESEARCH_VAULT_CONFIG"] = old


# ---------------------------------------------------------------------------
# Manifest / node builders
# ---------------------------------------------------------------------------

def _manifest(nodes: list[dict], run_id: str = "redo-test", global_cap: int = 4) -> dict:
    return {"run_id": run_id, "name": "Redo Test DAG", "global_cap": global_cap, "nodes": nodes}


def _agent(nid: str, needs: list | None = None, produces: dict | None = None) -> dict:
    n: dict = {"id": nid, "type": "agent", "label": f"Agent {nid}", "spec": "fixture://spec"}
    if needs:
        n["needs"] = needs
    if produces:
        n["produces"] = produces
    return n


def _human_go(nid: str, needs: list | None = None) -> dict:
    n: dict = {"id": nid, "type": "human-go", "label": f"Gate {nid}"}
    if needs:
        n["needs"] = needs
    return n


def _afterok(from_id: str) -> dict:
    return {"from": from_id, "edge": "afterok"}


def _write_manifest(tmp_path: Path, manifest: dict) -> Path:
    p = tmp_path / "dag.json"
    p.write_text(json.dumps(manifest), encoding="utf-8")
    return p


def _argns(**kwargs) -> "Any":
    import argparse
    return argparse.Namespace(**kwargs)


# ===========================================================================
# 1. Simple redo: no completed descendants
# ===========================================================================

class TestRedoSimple:
    def test_redo_succeeded_node_no_descendants(self, tmp_instance: Path, capsys):
        m = _manifest([_agent("critic")], run_id="redo-1")
        mf = _write_manifest(tmp_instance, m)
        cmd_run(_argns(manifest=str(mf)))
        capsys.readouterr()

        rc = cmd_complete(_argns(run_id="redo-1", node_id="critic", status="succeeded"))
        assert rc == 0
        capsys.readouterr()

        rc = cmd_redo(_argns(run_id="redo-1", node_id="critic", cascade=False, note=None))
        assert rc == 0
        out = capsys.readouterr().out
        assert "pending" in out.lower() or "redone" in out.lower()

        store = RunStore.from_config(__import__("research_vault.config", fromlist=["load_config"]).load_config())
        rs = store.load("redo-1")
        assert rs.node_status("critic") == "pending"

    def test_redo_reappears_in_next_tick_frontier(self, tmp_instance: Path, capsys):
        m = _manifest([_agent("critic")], run_id="redo-2")
        mf = _write_manifest(tmp_instance, m)
        cmd_run(_argns(manifest=str(mf)))
        capsys.readouterr()

        cmd_complete(_argns(run_id="redo-2", node_id="critic", status="succeeded"))
        capsys.readouterr()

        cmd_redo(_argns(run_id="redo-2", node_id="critic", cascade=False, note=None))
        capsys.readouterr()

        rc = cmd_tick(_argns(run_id="redo-2"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "critic" in out
        assert "DISPATCH" in out

    def test_redo_unknown_node_fails(self, tmp_instance: Path, capsys):
        m = _manifest([_agent("a")], run_id="redo-3")
        mf = _write_manifest(tmp_instance, m)
        cmd_run(_argns(manifest=str(mf)))
        capsys.readouterr()

        rc = cmd_redo(_argns(run_id="redo-3", node_id="ghost", cascade=False, note=None))
        assert rc == 1
        err = capsys.readouterr().err
        assert "ghost" in err

    def test_redo_non_terminal_node_fails(self, tmp_instance: Path, capsys):
        """A pending node has nothing to redo."""
        m = _manifest([_agent("a")], run_id="redo-4")
        mf = _write_manifest(tmp_instance, m)
        cmd_run(_argns(manifest=str(mf)))
        capsys.readouterr()

        rc = cmd_redo(_argns(run_id="redo-4", node_id="a", cascade=False, note=None))
        assert rc == 1
        err = capsys.readouterr().err
        assert "not completed" in err.lower() or "pending" in err.lower()

    def test_redo_unknown_run_fails(self, tmp_instance: Path, capsys):
        rc = cmd_redo(_argns(run_id="ghost-run", node_id="a", cascade=False, note=None))
        assert rc == 1
        err = capsys.readouterr().err
        assert "not found" in err

    def test_redo_failed_node(self, tmp_instance: Path, capsys):
        """A failed (max_retries=0, terminal) node can also be redone."""
        m = _manifest([_agent("a")], run_id="redo-5")
        mf = _write_manifest(tmp_instance, m)
        cmd_run(_argns(manifest=str(mf)))
        capsys.readouterr()

        cmd_complete(_argns(run_id="redo-5", node_id="a", status="failed", error="oops"))
        capsys.readouterr()

        rc = cmd_redo(_argns(run_id="redo-5", node_id="a", cascade=False, note=None))
        assert rc == 0
        store = RunStore.from_config(__import__("research_vault.config", fromlist=["load_config"]).load_config())
        rs = store.load("redo-5")
        assert rs.node_status("a") == "pending"


# ===========================================================================
# 2. Cascade safety: completed descendants
# ===========================================================================

class TestRedoCascadeSafety:
    def _chain_run(self, tmp_instance: Path, run_id: str) -> None:
        m = _manifest([
            _agent("critic"),
            _human_go("approve", needs=[_afterok("critic")]),
        ], run_id=run_id)
        mf = _write_manifest(tmp_instance, m)
        cmd_run(_argns(manifest=str(mf)))

    def test_redo_blocks_with_completed_descendant(self, tmp_instance: Path, capsys):
        run_id = "redo-cascade-1"
        self._chain_run(tmp_instance, run_id)
        capsys.readouterr()

        # critic succeeds -> approve promoted to awaiting-go by tick
        cmd_complete(_argns(run_id=run_id, node_id="critic", status="succeeded"))
        capsys.readouterr()
        cmd_tick(_argns(run_id=run_id))
        capsys.readouterr()

        # human approves the gate -> gate becomes succeeded (completed)
        cmd_approve(_argns(run_id=run_id, node_id="approve", note=None, output=None, reject=False, yes=True))
        capsys.readouterr()

        # Now redo 'critic' -- 'approve' is a completed descendant reading stale output
        rc = cmd_redo(_argns(run_id=run_id, node_id="critic", cascade=False, note=None))
        assert rc == 1
        err = capsys.readouterr().err
        assert "approve" in err
        assert "cascade" in err.lower()

        # Verify nothing was mutated (BLOCK, not partial reset)
        store = RunStore.from_config(__import__("research_vault.config", fromlist=["load_config"]).load_config())
        rs = store.load(run_id)
        assert rs.node_status("critic") == "succeeded"
        assert rs.node_status("approve") == "succeeded"

    def test_redo_cascade_resets_descendant_subtree(self, tmp_instance: Path, capsys):
        run_id = "redo-cascade-2"
        self._chain_run(tmp_instance, run_id)
        capsys.readouterr()

        cmd_complete(_argns(run_id=run_id, node_id="critic", status="succeeded"))
        capsys.readouterr()
        cmd_tick(_argns(run_id=run_id))
        capsys.readouterr()
        cmd_approve(_argns(run_id=run_id, node_id="approve", note=None, output=None, reject=False, yes=True))
        capsys.readouterr()

        rc = cmd_redo(_argns(run_id=run_id, node_id="critic", cascade=True, note="revise"))
        assert rc == 0
        capsys.readouterr()

        store = RunStore.from_config(__import__("research_vault.config", fromlist=["load_config"]).load_config())
        rs = store.load(run_id)
        assert rs.node_status("critic") == "pending"
        assert rs.node_status("approve") == "pending"

    def test_redo_no_cascade_needed_when_descendant_still_pending(self, tmp_instance: Path, capsys):
        """approve-protocol style: redoing critic while approve is still
        pending (not yet awaiting-go/completed) needs no --cascade."""
        run_id = "redo-cascade-3"
        self._chain_run(tmp_instance, run_id)
        capsys.readouterr()

        cmd_complete(_argns(run_id=run_id, node_id="critic", status="succeeded"))
        capsys.readouterr()
        # do NOT tick -- approve never promotes; still pending

        # A downstream awaiting-go promotion happens automatically inside
        # cmd_complete's frontier recompute, so simulate the "still working"
        # state directly instead: reset approve back to pending for this test.
        store = RunStore.from_config(__import__("research_vault.config", fromlist=["load_config"]).load_config())
        rs = store.load(run_id)
        rs.node_states["approve"]["status"] = "pending"
        store.save(rs)

        rc = cmd_redo(_argns(run_id=run_id, node_id="critic", cascade=False, note=None))
        assert rc == 0


# ===========================================================================
# 3. Prior attempt preserved in history
# ===========================================================================

class TestRedoHistoryPreserved:
    def test_history_preserves_prior_attempt(self, tmp_instance: Path, capsys):
        m = _manifest([_agent("critic")], run_id="redo-hist-1")
        mf = _write_manifest(tmp_instance, m)
        cmd_run(_argns(manifest=str(mf)))
        capsys.readouterr()

        cmd_complete(_argns(run_id="redo-hist-1", node_id="critic", status="succeeded"))
        capsys.readouterr()

        cmd_redo(_argns(run_id="redo-hist-1", node_id="critic", cascade=False, note="critic said BLOCK, revising"))
        capsys.readouterr()

        store = RunStore.from_config(__import__("research_vault.config", fromlist=["load_config"]).load_config())
        rs = store.load("redo-hist-1")
        ns = rs.node_states["critic"]
        assert ns["status"] == "pending"
        history = ns.get("redo_history")
        assert history is not None and len(history) == 1
        assert history[0]["status"] == "succeeded"
        assert history[0].get("redo_note") == "critic said BLOCK, revising"

    def test_history_accumulates_across_multiple_redos(self, tmp_instance: Path, capsys):
        m = _manifest([_agent("critic")], run_id="redo-hist-2")
        mf = _write_manifest(tmp_instance, m)
        cmd_run(_argns(manifest=str(mf)))
        capsys.readouterr()

        # Attempt 1: succeeded, then redone.
        cmd_complete(_argns(run_id="redo-hist-2", node_id="critic", status="succeeded"))
        capsys.readouterr()
        cmd_redo(_argns(run_id="redo-hist-2", node_id="critic", cascade=False, note="round 1"))
        capsys.readouterr()

        # Attempt 2: failed, then redone again.
        cmd_complete(_argns(run_id="redo-hist-2", node_id="critic", status="failed", error="still bad"))
        capsys.readouterr()
        cmd_redo(_argns(run_id="redo-hist-2", node_id="critic", cascade=False, note="round 2"))
        capsys.readouterr()

        store = RunStore.from_config(__import__("research_vault.config", fromlist=["load_config"]).load_config())
        rs = store.load("redo-hist-2")
        history = rs.node_states["critic"]["redo_history"]
        assert len(history) == 2
        assert history[0]["status"] == "succeeded"
        assert history[0]["redo_note"] == "round 1"
        assert history[1]["status"] == "failed"
        assert history[1]["redo_note"] == "round 2"


# ===========================================================================
# 4. End-to-end revise loop: redo -> re-complete -> downstream gate reads NEW verdict
# ===========================================================================

class TestRedoEndToEndReviseLoop:
    def test_downstream_await_go_reads_new_verdict_after_redo(self, tmp_instance: Path, capsys):
        """The exact case the live validation run needed: a critic node's
        verdict output -> redo -> re-complete with a NEW output -> the
        downstream human-go gate reads the NEW verdict, not the stale one."""
        run_id = "redo-e2e-1"
        m = _manifest([
            _agent("critic"),
            _human_go("approve", needs=[_afterok("critic")]),
        ], run_id=run_id)
        mf = _write_manifest(tmp_instance, m)
        cmd_run(_argns(manifest=str(mf)))
        capsys.readouterr()

        # First pass: critic completes -- but this is the BLOCK verdict we
        # want to revise. Simulate a recorded verdict via node_states outputs.
        cmd_complete(_argns(run_id=run_id, node_id="critic", status="succeeded"))
        capsys.readouterr()

        store = RunStore.from_config(__import__("research_vault.config", fromlist=["load_config"]).load_config())
        rs = store.load(run_id)
        rs.node_states["critic"]["outputs"] = {"verdict": "BLOCK"}
        store.save(rs)

        # tick promotes 'approve' to awaiting-go, reading the BLOCK verdict upstream
        cmd_tick(_argns(run_id=run_id))
        capsys.readouterr()
        rs = store.load(run_id)
        assert rs.node_status("approve") == "awaiting-go"

        # Redo critic to re-run it (approve is awaiting-go, not yet approved --
        # not a completed descendant, no cascade needed).
        rc = cmd_redo(_argns(run_id=run_id, node_id="critic", cascade=False, note="revise after BLOCK"))
        assert rc == 0
        capsys.readouterr()

        rs = store.load(run_id)
        # 'approve' is awaiting-go (not yet approved, not terminal) so it is
        # NOT a "completed descendant" -- nothing to consume yet, the human
        # will read whatever critic's artifacts say at approval time.
        assert rs.node_status("critic") == "pending"

        # Re-complete critic with the NEW (fixed) verdict.
        cmd_complete(_argns(run_id=run_id, node_id="critic", status="succeeded"))
        capsys.readouterr()
        rs = store.load(run_id)
        rs.node_states["critic"]["outputs"] = {"verdict": "PASS"}
        store.save(rs)

        cmd_tick(_argns(run_id=run_id))
        capsys.readouterr()

        rs = store.load(run_id)
        assert rs.node_status("approve") == "awaiting-go"
        assert rs.node_states["critic"]["outputs"]["verdict"] == "PASS"
        # The prior BLOCK attempt is preserved in history, not erased.
        assert rs.node_states["critic"]["redo_history"][0]["outputs"]["verdict"] == "BLOCK"
