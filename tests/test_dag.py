"""test_dag.py — hermetic tests for the SR-3 DAG implementation.

All tests run entirely in tmp_path. No ~/vault, no real cluster, no network.

Test coverage:
  1. schema — validation: unique ids, dangling refs, self-refs, cycles, types, edges
  2. walker — compute_frontier is pure (same inputs → same output)
  3. walker — afterok edge satisfaction (succeeded vs non-succeeded predecessor)
  4. walker — after / afterany / soft edge kinds
  5. walker — afterok+watch inline resolution (artifact+fresh)
  6. walker — human-go node: blocks frontier until all transitive upstream terminal
     THE INVARIANT TEST: pending grandparent → NOT approvable;
     upstream terminal → approvable
  7. walker — global_cap limits dispatch frontier
  8. store — create / load / save / list / delete (atomic)
  9. verbs — dag run: starts a run, prints frontier
 10. verbs — dag tick: re-computes frontier, resolves watches inline
 11. verbs — dag complete: advances node status, updates frontier
 12. verbs — dag approve: approves awaiting-go human-go node
 13. verbs — dag status: prints table + exact approve commands
 14. OKF typed-artifact: wrong type:dir fails vault check (produces invariant)
 15. NO POLLERS: grep-asserted — no import of pollers/drain/launchd anywhere in dag/
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Ensure src is importable (mirrors conftest.py)
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import Config, reset_config_cache
from research_vault.dag.schema import (
    ManifestError,
    validate_manifest,
    load_manifest,
    dump_manifest,
)
from research_vault.dag.walker import (
    FrontierNode,
    TERMINAL_STATUSES,
    compute_frontier,
    _transitive_upstream,
    _all_transitive_upstream_terminal,
)
from research_vault.dag.store import RunState, RunStore, StoreError, VALID_STATUSES
from research_vault.dag.verbs import _check_okf_note_type, cmd_run, cmd_tick, cmd_complete, cmd_approve, cmd_status


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_cfg():
    reset_config_cache()
    yield
    reset_config_cache()


@pytest.fixture
def tmp_cfg(tmp_path: Path) -> Config:
    """A minimal Config wired to tmp_path."""
    (tmp_path / "state").mkdir()
    (tmp_path / "notes").mkdir()
    raw = {
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(tmp_path / "state"),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "adapters": {"notifier": "file", "backend": "local", "secrets": "env"},
        "projects": {},
    }
    return Config(raw)


@pytest.fixture
def tmp_instance(tmp_path: Path) -> Path:
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

# SR-APPROVE-GATE: token fingerprint for test-time token approval.
[approval]
enforce = true
token_fingerprint = "d309a810bb5f40cef518202e46d197aa61e4dddafc5984c8c698da29ac8fd2bc"
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
# Minimal manifest builders
# ---------------------------------------------------------------------------

def _manifest(nodes: list[dict], run_id: str = "test-run", global_cap: int = 4) -> dict:
    return {"run_id": run_id, "name": "Test DAG", "global_cap": global_cap, "nodes": nodes}


def _node(
    nid: str,
    node_type: str = "agent",
    needs: list | None = None,
    produces: dict | None = None,
    spec: str | None = "fixture://test-spec",
    continues: dict | None = None,
) -> dict:
    """Build a manifest node.

    For agent nodes, spec defaults to "fixture://test-spec" (SR-DISP: spec is required).
    For human-go nodes, spec is omitted (human-go nodes are exempt from spec requirement).
    Pass spec=None explicitly to test the missing-spec error path.
    """
    n: dict = {"id": nid, "type": node_type, "label": f"Node {nid}"}
    if node_type == "agent" and spec is not None:
        n["spec"] = spec
    if needs:
        n["needs"] = needs
    if produces:
        n["produces"] = produces
    if continues is not None:
        n["continues"] = continues
    return n


def _need(from_id: str, edge: str = "afterok", watch: str | None = None) -> dict:
    d: dict = {"from": from_id, "edge": edge}
    if watch:
        d["watch"] = watch
    return d


def _state(status: str) -> dict:
    return {"status": status, "started_at": None, "completed_at": None, "error": None}


# ===========================================================================
# 1. Schema validation
# ===========================================================================

class TestSchemaValidation:
    def test_valid_simple_manifest(self):
        m = _manifest([_node("a"), _node("b", needs=[_need("a")])])
        validate_manifest(m)  # must not raise

    def test_missing_run_id(self):
        with pytest.raises(ManifestError, match="run_id"):
            validate_manifest({"nodes": [_node("a")]})

    def test_missing_nodes(self):
        with pytest.raises(ManifestError, match="nodes"):
            validate_manifest({"run_id": "x"})

    def test_empty_nodes(self):
        with pytest.raises(ManifestError, match="at least one"):
            validate_manifest({"run_id": "x", "nodes": []})

    def test_duplicate_node_ids(self):
        with pytest.raises(ManifestError, match="Duplicate"):
            validate_manifest(_manifest([_node("a"), _node("a")]))

    def test_dangling_needs_ref(self):
        with pytest.raises(ManifestError, match="not a known node id"):
            validate_manifest(_manifest([_node("a", needs=[_need("ghost")])]))

    def test_self_reference(self):
        with pytest.raises(ManifestError, match="self-reference"):
            validate_manifest(_manifest([_node("a", needs=[_need("a")])]))

    def test_cycle_two_nodes(self):
        with pytest.raises(ManifestError, match="cycle"):
            validate_manifest(_manifest([
                _node("a", needs=[_need("b")]),
                _node("b", needs=[_need("a")]),
            ]))

    def test_cycle_three_nodes(self):
        with pytest.raises(ManifestError, match="cycle"):
            validate_manifest(_manifest([
                _node("a"),
                _node("b", needs=[_need("a"), _need("c")]),
                _node("c", needs=[_need("b")]),
            ]))

    def test_unknown_node_type(self):
        n = _node("a")
        n["type"] = "robot"
        with pytest.raises(ManifestError, match="unknown type"):
            validate_manifest(_manifest([n]))

    def test_unknown_edge_kind(self):
        n = _node("b", needs=[{"from": "a", "edge": "mystery"}])
        with pytest.raises(ManifestError, match="unknown edge kind"):
            validate_manifest(_manifest([_node("a"), n]))

    def test_bad_global_cap(self):
        with pytest.raises(ManifestError, match="global_cap"):
            validate_manifest({"run_id": "x", "nodes": [_node("a")], "global_cap": 0})

    def test_produces_note_valid(self):
        n = _node("a", produces={"note": "experiments/e.md"})
        validate_manifest(_manifest([n]))  # must not raise

    def test_human_go_node_valid(self):
        m = _manifest([
            _node("work"),
            _node("gate", node_type="human-go", needs=[_need("work")]),
        ])
        validate_manifest(m)  # must not raise

    def test_load_manifest_from_file(self, tmp_path: Path):
        m = _manifest([_node("a"), _node("b", needs=[_need("a")])])
        p = tmp_path / "dag.json"
        p.write_text(json.dumps(m), encoding="utf-8")
        loaded = load_manifest(p)
        assert loaded["run_id"] == "test-run"
        assert len(loaded["nodes"]) == 2

    def test_load_manifest_invalid_json(self, tmp_path: Path):
        p = tmp_path / "bad.json"
        p.write_text("not json", encoding="utf-8")
        with pytest.raises(ManifestError, match="Invalid JSON"):
            load_manifest(p)


# ===========================================================================
# 2. Walker — purity
# ===========================================================================

class TestWalkerPurity:
    def test_same_inputs_same_output(self):
        """compute_frontier must be pure: same inputs → same output."""
        manifest = _manifest([
            _node("a"),
            _node("b", needs=[_need("a")]),
        ])
        node_states = {"a": _state("succeeded"), "b": _state("pending")}
        edge_reg_ts: dict = {}

        result1 = compute_frontier(manifest, node_states, edge_reg_ts, 4)
        result2 = compute_frontier(manifest, node_states, edge_reg_ts, 4)
        # Both calls must produce identical frontier (same ids, same actions)
        assert [(f.node_id, f.action) for f in result1] == [(f.node_id, f.action) for f in result2]

    def test_no_mutation_of_inputs(self):
        """compute_frontier must not mutate node_states or edge_registered_ts."""
        manifest = _manifest([_node("a"), _node("b", needs=[_need("a")])])
        node_states = {"a": _state("succeeded"), "b": _state("pending")}
        edge_reg_ts: dict = {}
        import copy
        ns_copy = copy.deepcopy(node_states)
        er_copy = copy.deepcopy(edge_reg_ts)

        compute_frontier(manifest, node_states, edge_reg_ts, 4)

        assert node_states == ns_copy
        assert edge_reg_ts == er_copy


# ===========================================================================
# 3. Walker — afterok edge
# ===========================================================================

class TestWalkerAfterok:
    def test_node_ready_when_predecessor_succeeded(self):
        m = _manifest([_node("a"), _node("b", needs=[_need("a")])])
        ns = {"a": _state("succeeded"), "b": _state("pending")}
        frontier = compute_frontier(m, ns, {}, 4)
        ids = [f.node_id for f in frontier]
        assert "b" in ids
        assert frontier[0].action == "dispatch"

    def test_node_not_ready_when_predecessor_pending(self):
        m = _manifest([_node("a"), _node("b", needs=[_need("a")])])
        ns = {"a": _state("pending"), "b": _state("pending")}
        frontier = compute_frontier(m, ns, {}, 4)
        ids = [f.node_id for f in frontier]
        assert "b" not in ids
        assert "a" in ids

    def test_node_not_ready_when_predecessor_failed(self):
        m = _manifest([_node("a"), _node("b", needs=[_need("a")])])
        ns = {"a": _state("failed"), "b": _state("pending")}
        frontier = compute_frontier(m, ns, {}, 4)
        ids = [f.node_id for f in frontier]
        assert "b" not in ids

    def test_node_not_ready_when_predecessor_dispatched(self):
        m = _manifest([_node("a"), _node("b", needs=[_need("a")])])
        ns = {"a": _state("dispatched"), "b": _state("pending")}
        frontier = compute_frontier(m, ns, {}, 4)
        ids = [f.node_id for f in frontier]
        assert "b" not in ids

    def test_no_deps_node_always_ready(self):
        m = _manifest([_node("a")])
        ns = {"a": _state("pending")}
        frontier = compute_frontier(m, ns, {}, 4)
        assert len(frontier) == 1
        assert frontier[0].node_id == "a"
        assert frontier[0].action == "dispatch"

    def test_two_predecessors_both_must_succeed(self):
        m = _manifest([
            _node("x"),
            _node("y"),
            _node("z", needs=[_need("x"), _need("y")]),
        ])
        # Only x succeeded
        ns = {"x": _state("succeeded"), "y": _state("pending"), "z": _state("pending")}
        frontier = compute_frontier(m, ns, {}, 4)
        ids = [f.node_id for f in frontier]
        assert "z" not in ids
        assert "y" in ids  # y is still pending — in frontier

    def test_two_predecessors_both_succeed(self):
        m = _manifest([
            _node("x"),
            _node("y"),
            _node("z", needs=[_need("x"), _need("y")]),
        ])
        ns = {"x": _state("succeeded"), "y": _state("succeeded"), "z": _state("pending")}
        frontier = compute_frontier(m, ns, {}, 4)
        ids = [f.node_id for f in frontier]
        assert "z" in ids


# ===========================================================================
# 4. Walker — after / afterany / soft edge kinds
# ===========================================================================

class TestWalkerEdgeKinds:
    def test_after_satisfied_by_any_terminal(self):
        """'after' edge is satisfied when predecessor is failed, blocked, or succeeded."""
        m = _manifest([_node("a"), _node("b", needs=[_need("a", edge="after")])])
        for terminal in ("succeeded", "failed", "blocked"):
            ns = {"a": _state(terminal), "b": _state("pending")}
            frontier = compute_frontier(m, ns, {}, 4)
            assert "b" in [f.node_id for f in frontier], f"expected b in frontier when a={terminal}"

    def test_after_not_satisfied_by_running(self):
        m = _manifest([_node("a"), _node("b", needs=[_need("a", edge="after")])])
        ns = {"a": _state("running"), "b": _state("pending")}
        frontier = compute_frontier(m, ns, {}, 4)
        assert "b" not in [f.node_id for f in frontier]

    def test_soft_always_satisfied(self):
        """'soft' edge never blocks — b is ready even when a is pending."""
        m = _manifest([_node("a"), _node("b", needs=[_need("a", edge="soft")])])
        ns = {"a": _state("pending"), "b": _state("pending")}
        frontier = compute_frontier(m, ns, {}, 4)
        ids = [f.node_id for f in frontier]
        assert "b" in ids
        assert "a" in ids  # a is also pending — both in frontier

    def test_afterany_satisfied_by_one_terminal(self):
        m = _manifest([
            _node("x"),
            _node("y"),
            _node("z", needs=[
                _need("x", edge="afterany"),
                _need("y", edge="afterany"),
            ]),
        ])
        # Only x is terminal
        ns = {"x": _state("succeeded"), "y": _state("pending"), "z": _state("pending")}
        frontier = compute_frontier(m, ns, {}, 4)
        ids = [f.node_id for f in frontier]
        assert "z" in ids

    def test_afterany_not_satisfied_when_none_terminal(self):
        m = _manifest([
            _node("x"),
            _node("y"),
            _node("z", needs=[
                _need("x", edge="afterany"),
                _need("y", edge="afterany"),
            ]),
        ])
        ns = {"x": _state("running"), "y": _state("running"), "z": _state("pending")}
        frontier = compute_frontier(m, ns, {}, 4)
        ids = [f.node_id for f in frontier]
        assert "z" not in ids


# ===========================================================================
# 5. Walker — afterok+watch inline resolution
# ===========================================================================

class TestWalkerAfterokWatch:
    def test_watch_resolved_when_artifact_fresh(self, tmp_path: Path):
        """afterok+watch edge resolves when the artifact exists and is fresh."""
        art = tmp_path / "findings" / "result.md"
        art.parent.mkdir(parents=True)
        reg_ts = time.time() - 1.0  # registered 1 second ago
        art.write_text("---\ntype: findings\ntitle: test\n---\n", encoding="utf-8")
        # mtime should be after reg_ts

        watch = f"artifact:{art}+fresh"
        m = _manifest([
            _node("producer"),
            _node("consumer", needs=[_need("producer", edge="afterok", watch=watch)]),
        ])
        ns = {"producer": _state("succeeded"), "consumer": _state("pending")}
        edge_key = "consumer:producer:0"
        edge_reg_ts = {edge_key: reg_ts}

        frontier = compute_frontier(m, ns, edge_reg_ts, 4)
        ids = [f.node_id for f in frontier]
        assert "consumer" in ids

    def test_watch_not_resolved_when_artifact_missing(self):
        """afterok+watch edge does not resolve when artifact is missing."""
        watch = "artifact:/nonexistent/path/file.md+fresh"
        m = _manifest([
            _node("producer"),
            _node("consumer", needs=[_need("producer", edge="afterok", watch=watch)]),
        ])
        ns = {"producer": _state("succeeded"), "consumer": _state("pending")}
        frontier = compute_frontier(m, ns, {}, 4)
        ids = [f.node_id for f in frontier]
        assert "consumer" not in ids

    def test_watch_not_resolved_when_artifact_stale(self, tmp_path: Path):
        """afterok+watch edge does not resolve when artifact predates registration."""
        art = tmp_path / "stale.md"
        art.write_text("---\ntype: findings\ntitle: stale\n---\n", encoding="utf-8")

        # Set reg_ts to AFTER the file was written — file is stale
        reg_ts = time.time() + 3600.0  # 1 hour in the future

        watch = f"artifact:{art}+fresh"
        m = _manifest([
            _node("producer"),
            _node("consumer", needs=[_need("producer", edge="afterok", watch=watch)]),
        ])
        ns = {"producer": _state("succeeded"), "consumer": _state("pending")}
        edge_reg_ts = {"consumer:producer:0": reg_ts}

        frontier = compute_frontier(m, ns, edge_reg_ts, 4)
        ids = [f.node_id for f in frontier]
        assert "consumer" not in ids

    def test_afterok_without_watch_needs_only_succeeded(self):
        """Plain afterok (no watch) satisfies when predecessor succeeded."""
        m = _manifest([_node("a"), _node("b", needs=[_need("a")])])
        ns = {"a": _state("succeeded"), "b": _state("pending")}
        frontier = compute_frontier(m, ns, {}, 4)
        assert "b" in [f.node_id for f in frontier]


# ===========================================================================
# 6. Walker — THE TRANSITIVE-UPSTREAM INVARIANT for human-go nodes
# ===========================================================================

class TestHumanGoInvariant:
    """
    THE INVARIANT: a human-go node must NOT be in the frontier (as await-go)
    until ALL transitive upstream nodes are terminal.

    Root cause of the prior defect: the walker checked only the DIRECT incoming
    edge, not the full transitive ancestor set. A human-go node became approvable
    while upstream nodes were still dispatched/pending.

    These tests are the proof that the fix is correct-by-construction.
    """

    def _dag_with_grandparent(self) -> dict:
        """Build: grandparent → parent → human-go gate."""
        return _manifest([
            _node("grandparent"),
            _node("parent", needs=[_need("grandparent")]),
            _node("gate", node_type="human-go", needs=[_need("parent")]),
        ])

    def test_human_go_not_in_frontier_when_grandparent_pending(self):
        """Pending grandparent → human-go gate NOT in frontier."""
        m = self._dag_with_grandparent()
        ns = {
            "grandparent": _state("pending"),
            "parent": _state("pending"),
            "gate": _state("pending"),
        }
        frontier = compute_frontier(m, ns, {}, 4)
        ids = [f.node_id for f in frontier]
        assert "gate" not in ids, "human-go node must not be approvable while grandparent is pending"

    def test_human_go_not_in_frontier_when_parent_dispatched(self):
        """Dispatched parent (grandparent succeeded) → human-go NOT in frontier."""
        m = self._dag_with_grandparent()
        ns = {
            "grandparent": _state("succeeded"),
            "parent": _state("dispatched"),
            "gate": _state("pending"),
        }
        frontier = compute_frontier(m, ns, {}, 4)
        ids = [f.node_id for f in frontier]
        assert "gate" not in ids, "human-go must not be approvable while parent is dispatched"

    def test_human_go_not_in_frontier_when_grandparent_dispatched_parent_pending(self):
        """Dispatched grandparent, pending parent → human-go NOT in frontier."""
        m = self._dag_with_grandparent()
        ns = {
            "grandparent": _state("dispatched"),
            "parent": _state("pending"),
            "gate": _state("pending"),
        }
        frontier = compute_frontier(m, ns, {}, 4)
        ids = [f.node_id for f in frontier]
        assert "gate" not in ids

    def test_human_go_in_frontier_when_all_upstream_terminal(self):
        """All transitive upstream terminal → human-go IS in frontier as await-go."""
        m = self._dag_with_grandparent()
        ns = {
            "grandparent": _state("succeeded"),
            "parent": _state("succeeded"),
            "gate": _state("pending"),
        }
        frontier = compute_frontier(m, ns, {}, 4)
        ids = [f.node_id for f in frontier]
        assert "gate" in ids, "human-go must be approvable when all upstream is terminal"
        gate_item = next(f for f in frontier if f.node_id == "gate")
        assert gate_item.action == "await-go"

    def test_human_go_in_frontier_when_upstream_failed_terminal(self):
        """Failed grandparent (terminal) + failed parent → human-go IS in frontier."""
        m = self._dag_with_grandparent()
        ns = {
            "grandparent": _state("failed"),
            "parent": _state("failed"),
            "gate": _state("pending"),
        }
        frontier = compute_frontier(m, ns, {}, 4)
        ids = [f.node_id for f in frontier]
        assert "gate" in ids, "human-go is approvable even when upstream failed (terminal = terminal)"

    def test_transitive_upstream_helper_includes_grandparent(self):
        """_transitive_upstream includes ALL ancestors, not just direct predecessors."""
        m = self._dag_with_grandparent()
        nodes_lookup = {n["id"]: n for n in m["nodes"]}
        ancestors = _transitive_upstream("gate", nodes_lookup)
        assert "parent" in ancestors
        assert "grandparent" in ancestors

    def test_all_transitive_upstream_terminal_false_when_grandparent_pending(self):
        m = self._dag_with_grandparent()
        nodes_lookup = {n["id"]: n for n in m["nodes"]}
        ns = {
            "grandparent": _state("pending"),
            "parent": _state("succeeded"),
            "gate": _state("pending"),
        }
        assert not _all_transitive_upstream_terminal("gate", nodes_lookup, ns)

    def test_all_transitive_upstream_terminal_true_when_all_done(self):
        m = self._dag_with_grandparent()
        nodes_lookup = {n["id"]: n for n in m["nodes"]}
        ns = {
            "grandparent": _state("succeeded"),
            "parent": _state("succeeded"),
            "gate": _state("pending"),
        }
        assert _all_transitive_upstream_terminal("gate", nodes_lookup, ns)

    def test_human_go_no_deps_always_in_frontier(self):
        """A human-go node with no dependencies is immediately in the frontier."""
        m = _manifest([_node("gate", node_type="human-go")])
        ns = {"gate": _state("pending")}
        frontier = compute_frontier(m, ns, {}, 4)
        assert len(frontier) == 1
        assert frontier[0].node_id == "gate"
        assert frontier[0].action == "await-go"

    def test_human_go_not_in_frontier_when_already_awaiting_go(self):
        """A human-go node already in awaiting-go is not re-added to frontier."""
        m = _manifest([
            _node("work"),
            _node("gate", node_type="human-go", needs=[_need("work")]),
        ])
        ns = {"work": _state("succeeded"), "gate": _state("awaiting-go")}
        frontier = compute_frontier(m, ns, {}, 4)
        ids = [f.node_id for f in frontier]
        assert "gate" not in ids  # awaiting-go is non-advanceable


# ===========================================================================
# 7. Walker — global_cap
# ===========================================================================

class TestWalkerGlobalCap:
    def test_cap_limits_dispatch_items(self):
        """global_cap=1 limits the frontier to at most 1 dispatch item."""
        m = _manifest([_node("a"), _node("b"), _node("c")], global_cap=1)
        ns = {"a": _state("pending"), "b": _state("pending"), "c": _state("pending")}
        frontier = compute_frontier(m, ns, {}, 1)
        dispatch = [f for f in frontier if f.action == "dispatch"]
        assert len(dispatch) <= 1

    def test_cap_accounts_for_active_nodes(self):
        """Active (dispatched) nodes count toward the cap."""
        m = _manifest([_node("a"), _node("b"), _node("c")], global_cap=2)
        # 1 already dispatched → only 1 slot remaining
        ns = {"a": _state("dispatched"), "b": _state("pending"), "c": _state("pending")}
        frontier = compute_frontier(m, ns, {}, 2)
        dispatch = [f for f in frontier if f.action == "dispatch"]
        assert len(dispatch) <= 1

    def test_human_go_does_not_count_toward_cap(self):
        """await-go items are not dispatch actions — they don't consume cap slots."""
        m = _manifest([
            _node("work"),
            _node("gate", node_type="human-go", needs=[_need("work")]),
        ], global_cap=1)
        ns = {"work": _state("succeeded"), "gate": _state("pending")}
        frontier = compute_frontier(m, ns, {}, 1)
        # gate should appear as await-go even at cap=1 (cap only limits dispatch)
        await_go = [f for f in frontier if f.action == "await-go"]
        assert len(await_go) == 1
        assert await_go[0].node_id == "gate"

    def test_zero_slots_no_dispatch(self):
        """When cap is fully consumed by active nodes, no new dispatch items."""
        m = _manifest([_node("a"), _node("b")], global_cap=1)
        ns = {"a": _state("dispatched"), "b": _state("pending")}
        frontier = compute_frontier(m, ns, {}, 1)
        dispatch = [f for f in frontier if f.action == "dispatch"]
        assert len(dispatch) == 0


# ===========================================================================
# 8. Store — CRUD
# ===========================================================================

class TestStore:
    def test_create_and_load(self, tmp_cfg: Config):
        store = RunStore.from_config(tmp_cfg)
        rs = RunState(run_id="run-1", manifest_path="/tmp/m.json")
        rs.node_states["a"] = _state("pending")
        store.create(rs)

        loaded = store.load("run-1")
        assert loaded.run_id == "run-1"
        assert loaded.node_states["a"]["status"] == "pending"

    def test_create_duplicate_raises(self, tmp_cfg: Config):
        store = RunStore.from_config(tmp_cfg)
        rs = RunState(run_id="dup", manifest_path="/tmp/m.json")
        store.create(rs)
        with pytest.raises(StoreError, match="already exists"):
            store.create(rs)

    def test_load_missing_raises(self, tmp_cfg: Config):
        store = RunStore.from_config(tmp_cfg)
        with pytest.raises(StoreError, match="not found"):
            store.load("nonexistent")

    def test_save_updates_state(self, tmp_cfg: Config):
        store = RunStore.from_config(tmp_cfg)
        rs = RunState(run_id="run-2", manifest_path="/tmp/m.json")
        rs.node_states["a"] = _state("pending")
        store.create(rs)

        rs.set_node_status("a", "succeeded")
        store.save(rs)

        loaded = store.load("run-2")
        assert loaded.node_states["a"]["status"] == "succeeded"

    def test_list_runs(self, tmp_cfg: Config):
        store = RunStore.from_config(tmp_cfg)
        store.create(RunState(run_id="alpha", manifest_path="/tmp/m.json"))
        store.create(RunState(run_id="beta", manifest_path="/tmp/m.json"))
        runs = store.list_runs()
        assert "alpha" in runs
        assert "beta" in runs

    def test_delete_run(self, tmp_cfg: Config):
        store = RunStore.from_config(tmp_cfg)
        store.create(RunState(run_id="del-me", manifest_path="/tmp/m.json"))
        store.delete("del-me")
        assert "del-me" not in store.list_runs()

    def test_delete_nonexistent_raises(self, tmp_cfg: Config):
        store = RunStore.from_config(tmp_cfg)
        with pytest.raises(StoreError, match="not found"):
            store.delete("ghost")

    def test_atomic_write_no_torn_read(self, tmp_cfg: Config):
        """After save(), the state file is complete JSON (atomic rename)."""
        store = RunStore.from_config(tmp_cfg)
        rs = RunState(run_id="atomic", manifest_path="/tmp/m.json")
        store.create(rs)
        # The file must be valid JSON
        dag_dir = tmp_cfg.state_dir / "dag"
        state_file = dag_dir / "atomic.json"
        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert data["run_id"] == "atomic"

    def test_edge_registered_ts_persists(self, tmp_cfg: Config):
        """edge_registered_ts values survive a save/load round-trip."""
        store = RunStore.from_config(tmp_cfg)
        rs = RunState(run_id="ts-run", manifest_path="/tmp/m.json")
        rs.edge_registered_ts["node-b:node-a:0"] = 12345.0
        store.create(rs)
        loaded = store.load("ts-run")
        assert loaded.edge_registered_ts["node-b:node-a:0"] == 12345.0

    def test_init_nodes_sets_edge_registered_ts(self):
        """init_nodes() populates edge_registered_ts for afterok+watch edges."""
        m = _manifest([
            _node("a"),
            _node("b", needs=[_need("a", edge="afterok", watch="artifact:/some/file.md+fresh")]),
        ])
        rs = RunState(run_id="ts-init", manifest_path="/tmp/m.json")
        before = time.time()
        rs.init_nodes(m)
        after = time.time()

        edge_key = "b:a:0"
        assert edge_key in rs.edge_registered_ts
        ts = rs.edge_registered_ts[edge_key]
        assert before <= ts <= after + 0.1  # within the window


# ===========================================================================
# 9. Verbs — dag run
# ===========================================================================

class TestVerbDagRun:
    def _manifest_file(self, tmp_path: Path, nodes: list[dict]) -> Path:
        m = _manifest(nodes, run_id="v-run")
        p = tmp_path / "manifest.json"
        p.write_text(json.dumps(m), encoding="utf-8")
        return p

    def test_run_creates_state_and_prints_frontier(self, tmp_instance: Path, capsys):
        p = self._manifest_file(tmp_instance, [_node("a"), _node("b", needs=[_need("a")])])
        args = _argns(manifest=str(p))
        rc = cmd_run(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "v-run" in out
        assert "DISPATCH" in out or "frontier" in out.lower()

    def test_run_missing_manifest(self, tmp_instance: Path, capsys):
        args = _argns(manifest="/nonexistent/manifest.json")
        rc = cmd_run(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "not found" in err

    def test_run_invalid_manifest(self, tmp_instance: Path, capsys):
        p = tmp_instance / "bad.json"
        p.write_text('{"run_id": "x"}', encoding="utf-8")  # missing nodes
        args = _argns(manifest=str(p))
        rc = cmd_run(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "manifest error" in err or "nodes" in err


# ===========================================================================
# 10. Verbs — dag tick
# ===========================================================================

class TestVerbDagTick:
    def test_tick_recomputes_frontier(self, tmp_instance: Path, capsys):
        """After completing 'a', tick should show 'b' in the frontier."""
        m = _manifest([_node("a"), _node("b", needs=[_need("a")])], run_id="tick-run")
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")

        # Start the run
        cmd_run(_argns(manifest=str(mf)))
        capsys.readouterr()

        # Complete node 'a'
        from research_vault.dag.store import RunStore
        from research_vault.config import load_config
        cfg = load_config()
        store = RunStore.from_config(cfg)
        rs = store.load("tick-run")
        rs.set_node_status("a", "succeeded")
        store.save(rs)

        # Tick
        rc = cmd_tick(_argns(run_id="tick-run"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "b" in out or "DISPATCH" in out

    def test_tick_missing_run(self, tmp_instance: Path, capsys):
        rc = cmd_tick(_argns(run_id="ghost-run"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "not found" in err


# ===========================================================================
# 11. Verbs — dag complete
# ===========================================================================

class TestVerbDagComplete:
    def test_complete_node_succeeded(self, tmp_instance: Path, capsys):
        m = _manifest([_node("a"), _node("b", needs=[_need("a")])], run_id="cmp-run")
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")
        cmd_run(_argns(manifest=str(mf)))
        capsys.readouterr()

        rc = cmd_complete(_argns(run_id="cmp-run", node_id="a", status="succeeded"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "succeeded" in out

        from research_vault.dag.store import RunStore
        from research_vault.config import load_config
        rs = RunStore.from_config(load_config()).load("cmp-run")
        assert rs.node_status("a") == "succeeded"

    def test_complete_already_terminal_no_op(self, tmp_instance: Path, capsys):
        m = _manifest([_node("a")], run_id="cmp2-run")
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")
        cmd_run(_argns(manifest=str(mf)))
        cmd_complete(_argns(run_id="cmp2-run", node_id="a", status="succeeded"))
        capsys.readouterr()

        rc = cmd_complete(_argns(run_id="cmp2-run", node_id="a", status="succeeded"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "already terminal" in out or "No change" in out

    def test_complete_unknown_node(self, tmp_instance: Path, capsys):
        m = _manifest([_node("a")], run_id="cmp3-run")
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")
        cmd_run(_argns(manifest=str(mf)))
        capsys.readouterr()

        rc = cmd_complete(_argns(run_id="cmp3-run", node_id="ghost", status="succeeded"))
        assert rc == 1


# ===========================================================================
# 12. Verbs — dag approve
# ===========================================================================

class TestVerbDagApprove:
    def _setup_human_go_run(self, tmp_instance: Path) -> str:
        run_id = "approve-run"
        m = _manifest([
            _node("work"),
            _node("gate", node_type="human-go", needs=[_need("work")]),
        ], run_id=run_id)
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")
        import argparse
        cmd_run(_argns(manifest=str(mf)))
        return run_id

    def test_approve_requires_awaiting_go_state(self, tmp_instance: Path, capsys):
        """Approving a pending human-go node (not yet awaiting-go) fails."""
        run_id = self._setup_human_go_run(tmp_instance)
        capsys.readouterr()  # drain run output

        # 'gate' is still pending (work not yet done)
        rc = cmd_approve(_argns(run_id=run_id, node_id="gate"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "awaiting-go" in err

    def test_approve_succeeds_after_upstream_terminal(self, tmp_instance: Path, capsys):
        """Approval succeeds once upstream is terminal and gate is awaiting-go."""
        run_id = self._setup_human_go_run(tmp_instance)
        capsys.readouterr()

        from research_vault.dag.store import RunStore
        from research_vault.config import load_config
        cfg = load_config()
        store = RunStore.from_config(cfg)

        # Complete 'work' → this triggers gate to become awaiting-go on next tick
        rs = store.load(run_id)
        rs.set_node_status("work", "succeeded")
        store.save(rs)

        # Tick to promote gate to awaiting-go
        cmd_tick(_argns(run_id=run_id))
        capsys.readouterr()

        # Now approve
        rc = cmd_approve(_argns(run_id=run_id, node_id="gate"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "approved" in out or "succeeded" in out

        rs2 = store.load(run_id)
        assert rs2.node_status("gate") == "succeeded"

    def test_approve_non_human_go_node_fails(self, tmp_instance: Path, capsys):
        run_id = self._setup_human_go_run(tmp_instance)
        capsys.readouterr()
        rc = cmd_approve(_argns(run_id=run_id, node_id="work"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "human-go" in err


# ===========================================================================
# 13. Verbs — dag status (prints exact approve command)
# ===========================================================================

class TestVerbDagStatus:
    def test_status_prints_approve_command_for_awaiting_go(self, tmp_instance: Path, capsys):
        """dag status must print the exact `dag approve <run_id> <node_id>` command."""
        run_id = "status-run"
        m = _manifest([
            _node("work"),
            _node("gate", node_type="human-go", needs=[_need("work")]),
        ], run_id=run_id)
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")
        cmd_run(_argns(manifest=str(mf)))

        from research_vault.dag.store import RunStore
        from research_vault.config import load_config
        rs = RunStore.from_config(load_config()).load(run_id)
        rs.set_node_status("work", "succeeded")
        RunStore.from_config(load_config()).save(rs)
        cmd_tick(_argns(run_id=run_id))
        capsys.readouterr()  # drain

        rc = cmd_status(_argns(run_id=run_id))
        assert rc == 0
        out = capsys.readouterr().out
        assert f"rv dag approve {run_id} gate" in out, (
            f"dag status must print exact approve command. Got:\n{out}"
        )

    def test_status_shows_all_node_statuses(self, tmp_instance: Path, capsys):
        run_id = "status2-run"
        m = _manifest([_node("a"), _node("b", needs=[_need("a")])], run_id=run_id)
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")
        cmd_run(_argns(manifest=str(mf)))
        capsys.readouterr()

        rc = cmd_status(_argns(run_id=run_id))
        assert rc == 0
        out = capsys.readouterr().out
        assert "a" in out
        assert "b" in out


# ===========================================================================
# 14. OKF typed-artifact: wrong type:dir fails vault check
# ===========================================================================

class TestOKFTypedArtifact:
    def test_correct_type_matches_dir(self, tmp_path: Path):
        """A note with type: experiments in experiments/ passes the check."""
        note_dir = tmp_path / "experiments"
        note_dir.mkdir()
        note = note_dir / "exp-001.md"
        note.write_text("---\ntype: experiments\ntitle: Test\n---\n", encoding="utf-8")

        issues = _check_okf_note_type(str(note), tmp_path)
        assert issues == [], f"Expected no issues, got: {issues}"

    def test_wrong_type_in_dir_fails(self, tmp_path: Path):
        """A note with type: literature placed in experiments/ fails the check.

        This is the core 'produces-typed node writes WRONG type:dir' invariant.
        """
        note_dir = tmp_path / "experiments"
        note_dir.mkdir()
        note = note_dir / "wrong-exp.md"
        note.write_text("---\ntype: literature\ntitle: Wrong\n---\n", encoding="utf-8")

        issues = _check_okf_note_type(str(note), tmp_path)
        assert len(issues) > 0, "Expected a type mismatch issue"
        assert any("type mismatch" in i or "literature" in i for i in issues)

    def test_missing_type_frontmatter_fails(self, tmp_path: Path):
        """A note without a type: field fails the check."""
        note_dir = tmp_path / "findings"
        note_dir.mkdir()
        note = note_dir / "f.md"
        note.write_text("---\ntitle: No type\n---\n", encoding="utf-8")

        issues = _check_okf_note_type(str(note), tmp_path)
        assert len(issues) > 0
        assert any("type" in i for i in issues)

    def test_missing_note_fails(self, tmp_path: Path):
        """A nonexistent note path returns an issue."""
        issues = _check_okf_note_type("experiments/nonexistent.md", tmp_path)
        assert len(issues) > 0

    def test_dag_complete_rejects_wrong_type_produces(self, tmp_instance: Path, capsys):
        """dag complete with produces node fails if the note type:dir is wrong."""
        note_dir = tmp_instance / "notes" / "experiments"
        note_dir.mkdir(parents=True, exist_ok=True)
        note = note_dir / "exp-bad.md"
        # Write the note with the WRONG type (literature instead of experiments)
        note.write_text("---\ntype: literature\ntitle: Bad type\n---\n", encoding="utf-8")

        run_id = "okf-fail-run"
        m = _manifest([
            _node("producer", produces={"note": str(note)}),
        ], run_id=run_id)
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")
        cmd_run(_argns(manifest=str(mf)))
        capsys.readouterr()

        rc = cmd_complete(_argns(run_id=run_id, node_id="producer", status="succeeded"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "OKF vault check FAILED" in err or "type mismatch" in err

    def test_dag_complete_accepts_correct_type_produces(self, tmp_instance: Path, capsys):
        """dag complete succeeds when the note type:dir is correct."""
        note_dir = tmp_instance / "notes" / "experiments"
        note_dir.mkdir(parents=True, exist_ok=True)
        note = note_dir / "exp-good.md"
        note.write_text("---\ntype: experiments\ntitle: Good\n---\n", encoding="utf-8")

        run_id = "okf-pass-run"
        m = _manifest([
            _node("producer", produces={"note": str(note)}),
        ], run_id=run_id)
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")
        cmd_run(_argns(manifest=str(mf)))
        capsys.readouterr()

        rc = cmd_complete(_argns(run_id=run_id, node_id="producer", status="succeeded"))
        assert rc == 0


# ===========================================================================
# 15. NO POLLERS — grep-asserted
# ===========================================================================

class TestNoPollers:
    """Asserts that the dag/ package never imports pollers, drain, or launchd.

    This enforces the in-session-only resolution contract (SR-3).
    """

    def _dag_src_files(self) -> list[Path]:
        dag_dir = Path(__file__).parent.parent / "src" / "research_vault" / "dag"
        return list(dag_dir.rglob("*.py"))

    def test_no_pollers_import_in_dag(self):
        """No import statement importing pollers/drain/launchd anywhere in dag/.

        The check looks for `import` or `from … import` lines referencing those
        modules — not docstring mentions (which are allowed for documentation).
        """
        import re
        # Match lines that actually import the forbidden modules
        forbidden_import_re = re.compile(
            r"^\s*(import|from)\s+\S*?(pollers|drain|launchd)\b",
            re.MULTILINE,
        )
        violations = []
        for src_file in self._dag_src_files():
            text = src_file.read_text(encoding="utf-8")
            for match in forbidden_import_re.finditer(text):
                violations.append(f"{src_file.name}: {match.group(0).strip()!r}")
        assert not violations, (
            "DAG module must NOT import pollers/drain/launchd — "
            "in-session resolution only (SR-3 contract):\n"
            + "\n".join(violations)
        )

    def test_no_background_scheduler_in_dag(self):
        """No asyncio/threading scheduler patterns that would constitute a liveness net."""
        forbidden_patterns = ("asyncio.create_task", "threading.Timer", "sched.scheduler")
        violations = []
        for src_file in self._dag_src_files():
            text = src_file.read_text(encoding="utf-8")
            for pat in forbidden_patterns:
                if pat in text:
                    violations.append(f"{src_file.name}: contains {pat!r}")
        assert not violations, (
            "DAG module must not contain background scheduler patterns:\n"
            + "\n".join(violations)
        )


# ===========================================================================
# Helper: argparse.Namespace constructor
# ===========================================================================

def _argns(**kwargs) -> "argparse.Namespace":
    import argparse
    return argparse.Namespace(**kwargs)
