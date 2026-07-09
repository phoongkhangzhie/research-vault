"""test_dag_tool_node.py — D4 (verb-consolidation) acceptance: the "tool"
(deterministic-op) DAG node-kind.

Coverage:
  1. schema — "tool" is a valid node type; requires a non-empty 'op' field;
     rejects reads/max_retries/spec-shaped garbage same as human-go.
  2. walker — a tool node reaches the frontier as a normal "dispatch"
     candidate (not "await-go") once its needs are satisfied.
  3. verbs — `rv dag run`/`rv dag tick` AUTO-EXECUTE a ready tool node
     IN-PROCESS (no `rv dag brief`, no `rv dag complete` needed) via the
     review.autonomy op registry; a downstream agent node becomes
     dispatchable immediately after (tool -> agent chaining).
  4. verbs — `rv dag brief` refuses a tool node with a redirect message
     (tool nodes are not agent-dispatch targets).
  5. verbs — an op that raises is surfaced as a BLOCKED node (never
     silently swallowed, never silently retried).
  6. A tool -> tool chain auto-executes both without any manual step.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import reset_config_cache  # noqa: E402
from research_vault.dag.schema import ManifestError  # noqa: E402
from research_vault.dag.walker import compute_frontier  # noqa: E402
from research_vault.dag.verbs import cmd_run, cmd_tick, cmd_brief  # noqa: E402
from research_vault.review import autonomy  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_config_cache()
    yield
    reset_config_cache()


@pytest.fixture
def tmp_instance(tmp_path: Path):
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


def _argns(**kwargs):
    import argparse
    return argparse.Namespace(**kwargs)


def _tool_node(
    nid: str, op: str, args: dict | None = None, needs: list | None = None,
    produces: dict | None = None,
) -> dict:
    n: dict = {"id": nid, "type": "tool", "op": op}
    if args is not None:
        n["args"] = args
    if needs:
        n["needs"] = needs
    if produces is not None:
        n["produces"] = produces
    return n


def _agent_node(nid: str, needs: list | None = None) -> dict:
    n: dict = {"id": nid, "type": "agent", "spec": "fixture://test-spec"}
    if needs:
        n["needs"] = needs
    return n


# ---------------------------------------------------------------------------
# 1. Schema validation
# ---------------------------------------------------------------------------

class TestToolNodeSchema:
    def test_tool_node_valid(self):
        m = {"run_id": "r", "nodes": [_tool_node("t1", "coverage", {"project": "p", "scope": "s"})]}
        from research_vault.dag.schema import validate_manifest as validate
        validate(m)  # should not raise

    def test_tool_node_missing_op_rejected(self):
        from research_vault.dag.schema import validate_manifest as validate
        m = {"run_id": "r", "nodes": [{"id": "t1", "type": "tool"}]}
        with pytest.raises(ManifestError, match="op"):
            validate(m)

    def test_tool_node_empty_op_rejected(self):
        from research_vault.dag.schema import validate_manifest as validate
        m = {"run_id": "r", "nodes": [{"id": "t1", "type": "tool", "op": "   "}]}
        with pytest.raises(ManifestError):
            validate(m)

    def test_tool_node_rejects_reads(self):
        from research_vault.dag.schema import validate_manifest as validate
        m = {"run_id": "r", "nodes": [{"id": "t1", "type": "tool", "op": "coverage", "reads": ["x.md"]}]}
        with pytest.raises(ManifestError, match="reads"):
            validate(m)

    def test_tool_node_rejects_max_retries(self):
        from research_vault.dag.schema import validate_manifest as validate
        m = {"run_id": "r", "nodes": [{"id": "t1", "type": "tool", "op": "coverage", "max_retries": 2}]}
        with pytest.raises(ManifestError, match="max_retries"):
            validate(m)

    def test_tool_node_args_must_be_dict(self):
        from research_vault.dag.schema import validate_manifest as validate
        m = {"run_id": "r", "nodes": [{"id": "t1", "type": "tool", "op": "coverage", "args": "nope"}]}
        with pytest.raises(ManifestError, match="args"):
            validate(m)


# ---------------------------------------------------------------------------
# 2. Frontier
# ---------------------------------------------------------------------------

class TestToolNodeFrontier:
    def test_tool_node_is_dispatch_not_await_go(self):
        m = {"run_id": "r", "global_cap": 4, "nodes": [_tool_node("t1", "coverage")]}
        frontier = compute_frontier(m, {}, {}, 4)
        assert len(frontier) == 1
        assert frontier[0].action == "dispatch"
        assert frontier[0].node_id == "t1"


# ---------------------------------------------------------------------------
# 3/6. End-to-end auto-execution via cmd_run / cmd_tick
# ---------------------------------------------------------------------------

class TestToolNodeAutoExecution:
    def test_dag_run_auto_executes_ready_tool_node(self, tmp_instance: Path, monkeypatch, capsys):
        calls = []

        def fake_coverage(**kwargs):
            calls.append(kwargs)
            return {"counts": {"corpus": 3, "materialized": 3, "unmaterialized": 0, "orphan": 0}}

        monkeypatch.setitem(autonomy.OP_REGISTRY, "coverage", fake_coverage)

        m = {
            "run_id": "tool-run-1",
            "global_cap": 4,
            "nodes": [_tool_node("t1", "coverage", {"project": "demo", "scope": "s1"})],
        }
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")

        rc = cmd_run(_argns(manifest=str(mf)))
        assert rc == 0
        assert calls, "the tool op must have been auto-executed on dag run — no manual step"

        from research_vault.dag.store import RunStore
        from research_vault.config import load_config
        rs = RunStore.from_config(load_config()).load("tool-run-1")
        assert rs.node_status("t1") == "succeeded"
        assert "corpus" in rs.node_states["t1"]["tool_result_summary"]

    def test_tool_to_agent_chain_unblocks_agent_after_auto_exec(self, tmp_instance: Path, monkeypatch, capsys):
        """A tool node upstream of an agent node: on `dag run`, the tool
        auto-executes and the agent node becomes dispatchable in the SAME
        call — no `dag tick` needed."""
        monkeypatch.setitem(autonomy.OP_REGISTRY, "coverage", lambda **kw: {"ok": True})

        m = {
            "run_id": "tool-run-2",
            "global_cap": 4,
            "nodes": [
                _tool_node("t1", "coverage", {"project": "demo", "scope": "s1"}),
                _agent_node("a1", needs=[{"from": "t1", "edge": "afterok"}]),
            ],
        }
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")

        cmd_run(_argns(manifest=str(mf)))
        out = capsys.readouterr().out

        from research_vault.dag.store import RunStore
        from research_vault.config import load_config
        rs = RunStore.from_config(load_config()).load("tool-run-2")
        assert rs.node_status("t1") == "succeeded"
        assert "a1" in out  # a1 shows up in the printed frontier (dispatch-ready)

    def test_tool_to_tool_chain_auto_executes_both(self, tmp_instance: Path, monkeypatch):
        monkeypatch.setitem(autonomy.OP_REGISTRY, "sweep", lambda **kw: {"kept": []})
        monkeypatch.setitem(autonomy.OP_REGISTRY, "coverage", lambda **kw: {"ok": True})

        m = {
            "run_id": "tool-run-3",
            "global_cap": 4,
            "nodes": [
                _tool_node("t1", "sweep", {"protocol": "x"}),
                _tool_node("t2", "coverage", {"project": "p", "scope": "s"}, needs=[{"from": "t1", "edge": "afterok"}]),
            ],
        }
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")

        cmd_run(_argns(manifest=str(mf)))

        from research_vault.dag.store import RunStore
        from research_vault.config import load_config
        rs = RunStore.from_config(load_config()).load("tool-run-3")
        assert rs.node_status("t1") == "succeeded"
        assert rs.node_status("t2") == "succeeded"

    def test_tool_node_op_exception_blocks_not_silently_swallowed(self, tmp_instance: Path, monkeypatch):
        def boom(**kw):
            raise RuntimeError("adapter unreachable")

        monkeypatch.setitem(autonomy.OP_REGISTRY, "coverage", boom)

        m = {
            "run_id": "tool-run-4",
            "global_cap": 4,
            "nodes": [_tool_node("t1", "coverage", {"project": "p", "scope": "s"})],
        }
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")

        cmd_run(_argns(manifest=str(mf)))

        from research_vault.dag.store import RunStore
        from research_vault.config import load_config
        rs = RunStore.from_config(load_config()).load("tool-run-4")
        assert rs.node_status("t1") == "blocked"
        assert "adapter unreachable" in rs.node_states["t1"].get("tool_error", "")

    def test_unregistered_op_blocks_loudly(self, tmp_instance: Path):
        m = {
            "run_id": "tool-run-5",
            "global_cap": 4,
            "nodes": [_tool_node("t1", "definitely-not-a-real-op")],
        }
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")

        cmd_run(_argns(manifest=str(mf)))

        from research_vault.dag.store import RunStore
        from research_vault.config import load_config
        rs = RunStore.from_config(load_config()).load("tool-run-5")
        assert rs.node_status("t1") == "blocked"
        assert "unknown tool op" in rs.node_states["t1"].get("tool_error", "").lower()


# ---------------------------------------------------------------------------
# 3b. produces: enforcement (review-loop-nodekind-drift-fix §4-D)
# ---------------------------------------------------------------------------

class TestToolNodeProducesEnforcement:
    def test_op_that_writes_declared_artifact_succeeds(self, tmp_instance: Path, monkeypatch):
        artifact = tmp_instance / "_written.md"

        def fake_sweep(**kw):
            artifact.write_text("hits", encoding="utf-8")
            return str(artifact)

        monkeypatch.setitem(autonomy.OP_REGISTRY, "sweep", fake_sweep)

        m = {
            "run_id": "tool-produces-1",
            "global_cap": 4,
            "nodes": [_tool_node("t1", "sweep", {}, produces={"_written.md": str(artifact)})],
        }
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")

        cmd_run(_argns(manifest=str(mf)))

        from research_vault.dag.store import RunStore
        from research_vault.config import load_config
        rs = RunStore.from_config(load_config()).load("tool-produces-1")
        assert rs.node_status("t1") == "succeeded"

    def test_op_that_skips_declared_artifact_is_blocked(self, tmp_instance: Path, monkeypatch):
        never_written = tmp_instance / "_never_written.md"

        def fake_sweep_no_write(**kw):
            return "some in-memory result, no file written"

        monkeypatch.setitem(autonomy.OP_REGISTRY, "sweep", fake_sweep_no_write)

        m = {
            "run_id": "tool-produces-2",
            "global_cap": 4,
            "nodes": [_tool_node("t1", "sweep", {}, produces={"_never_written.md": str(never_written)})],
        }
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")

        cmd_run(_argns(manifest=str(mf)))

        from research_vault.dag.store import RunStore
        from research_vault.config import load_config
        rs = RunStore.from_config(load_config()).load("tool-produces-2")
        assert rs.node_status("t1") == "blocked"
        assert "_never_written.md" in rs.node_states["t1"].get("tool_error", "")

    def test_tool_node_without_produces_is_exempt(self, tmp_instance: Path, monkeypatch):
        """A tool node with no produces: dict (e.g. coverage/relations, an
        in-memory report) is never blocked by this gate."""
        monkeypatch.setitem(autonomy.OP_REGISTRY, "coverage", lambda **kw: {"ok": True})

        m = {
            "run_id": "tool-produces-3",
            "global_cap": 4,
            "nodes": [_tool_node("t1", "coverage", {"project": "p", "scope": "s"})],
        }
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")

        cmd_run(_argns(manifest=str(mf)))

        from research_vault.dag.store import RunStore
        from research_vault.config import load_config
        rs = RunStore.from_config(load_config()).load("tool-produces-3")
        assert rs.node_status("t1") == "succeeded"


# ---------------------------------------------------------------------------
# 4. `rv dag brief` refuses tool nodes
# ---------------------------------------------------------------------------

class TestToolNodeBriefRefusal:
    def test_brief_refuses_tool_node(self, tmp_instance: Path, monkeypatch, capsys):
        monkeypatch.setitem(autonomy.OP_REGISTRY, "definitely-not-a-real-op-2", lambda **kw: {})
        m = {
            "run_id": "tool-run-6",
            "global_cap": 4,
            # give it needs it never satisfies so it stays pending, not auto-executed
            "nodes": [
                _agent_node("gate"),
                _tool_node("t1", "definitely-not-a-real-op-2", needs=[{"from": "gate", "edge": "afterok"}]),
            ],
        }
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")
        cmd_run(_argns(manifest=str(mf)))
        capsys.readouterr()

        rc = cmd_brief(_argns(run_id="tool-run-6", node_id="t1"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "tool" in err.lower()
        assert "in-process" in err.lower() or "IN-PROCESS" in err
