"""test_dag_disp.py — hermetic tests for schema-by-construction dispatch discipline.

Coverage:
  1. Schema teeth — spec REQUIRED on agent nodes (ManifestError on absence)
  2. Schema teeth — continues validation (non-existent / non-agent / non-ancestor / self → ManifestError)
  3. Schema teeth — continues.reason non-empty required (ManifestError)
  4. Schema teeth — valid continues passes validation
  5. Boundary-smell WARN — continues crossing produces/human-go boundary → non-fatal WARN string
  6. Frontier mode line — FRESH + brief hint for fresh agent (never the full spec body)
  7. Frontier mode line — CONTINUES <node> — <reason> + brief hint for continues agent
  8. human-go nodes are EXEMPT from spec requirement
  9. manifest_warns — non-fatal (valid manifest still validates after warns)

All tests hermetic — no ~/vault, no real cluster, no network.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import reset_config_cache
from research_vault.dag.schema import ManifestError, validate_manifest, manifest_warns
from research_vault.dag.walker import FrontierNode, compute_frontier, _transitive_upstream
from research_vault.dag.verbs import _print_frontier


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_cfg():
    reset_config_cache()
    yield
    reset_config_cache()


# ---------------------------------------------------------------------------
# Minimal manifest builders (spec-aware)
# ---------------------------------------------------------------------------

def _agent(
    nid: str,
    *,
    spec: str | None = "task://test#spec",
    continues: dict | None = None,
    needs: list | None = None,
    produces: dict | None = None,
    label: str | None = None,
) -> dict:
    """Build an agent node with optional spec, continues, needs, produces."""
    n: dict = {"id": nid, "type": "agent", "label": label or f"Node {nid}"}
    if spec is not None:
        n["spec"] = spec
    if continues is not None:
        n["continues"] = continues
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


def _need(from_id: str, edge: str = "afterok") -> dict:
    return {"from": from_id, "edge": edge}


def _manifest(nodes: list[dict], run_id: str = "test-run") -> dict:
    return {"run_id": run_id, "nodes": nodes}


# ===========================================================================
# 1. spec REQUIRED on agent nodes
# ===========================================================================

class TestSpecRequired:
    def test_agent_with_spec_passes(self):
        m = _manifest([_agent("a", spec="task://research#lit-search")])
        validate_manifest(m)  # must not raise

    def test_agent_missing_spec_raises(self):
        """An agent node with no spec field → ManifestError (fresh-by-default enforcement)."""
        m = _manifest([_agent("a", spec=None)])
        with pytest.raises(ManifestError, match="spec"):
            validate_manifest(m)

    def test_agent_empty_spec_raises(self):
        """An agent node with spec='' (empty string) → ManifestError."""
        m = _manifest([_agent("a", spec="")])
        with pytest.raises(ManifestError, match="spec"):
            validate_manifest(m)

    def test_agent_spec_whitespace_only_raises(self):
        """An agent node with spec='   ' (whitespace only) → ManifestError."""
        m = _manifest([_agent("a", spec="   ")])
        with pytest.raises(ManifestError, match="spec"):
            validate_manifest(m)

    def test_human_go_no_spec_exempt(self):
        """human-go nodes are NOT required to have spec — they are decision gates, not dispatches."""
        m = _manifest([
            _agent("a", spec="task://test#a"),
            _human_go("gate", needs=[_need("a")]),
        ])
        validate_manifest(m)  # must not raise

    def test_multiple_agents_all_need_spec(self):
        """All agent nodes in the manifest must have spec."""
        m = _manifest([
            _agent("a", spec="task://test#a"),
            _agent("b", spec=None, needs=[_need("a")]),  # missing spec
        ])
        with pytest.raises(ManifestError, match="spec"):
            validate_manifest(m)


# ===========================================================================
# 2. continues validation (ManifestError on violations)
# ===========================================================================

class TestContinuesValidation:
    def test_continues_to_nonexistent_node_raises(self):
        """continues.node not in manifest → ManifestError."""
        m = _manifest([
            _agent("a", spec="task://test#a"),
            _agent("b", spec="task://test#b",
                   continues={"node": "ghost", "reason": "tight iteration"},
                   needs=[_need("a")]),
        ])
        with pytest.raises(ManifestError, match="continues"):
            validate_manifest(m)

    def test_continues_to_non_agent_type_raises(self):
        """continues.node is a human-go node → ManifestError (can only continue an agent thread)."""
        m = _manifest([
            _agent("a", spec="task://test#a"),
            _human_go("gate", needs=[_need("a")]),
            _agent("c", spec="task://test#c",
                   continues={"node": "gate", "reason": "tight iter"},
                   needs=[_need("gate")]),
        ])
        with pytest.raises(ManifestError, match="continues"):
            validate_manifest(m)

    def test_continues_to_non_ancestor_raises(self):
        """continues.node is not a transitive upstream ancestor → ManifestError."""
        # 'b' is a sibling of 'c', not an ancestor
        m = _manifest([
            _agent("a", spec="task://test#a"),
            _agent("b", spec="task://test#b", needs=[_need("a")]),
            _agent("c", spec="task://test#c",
                   continues={"node": "b", "reason": "tight iter"},
                   needs=[_need("a")]),  # c depends on a, not b — b is not c's ancestor
        ])
        with pytest.raises(ManifestError, match="continues"):
            validate_manifest(m)

    def test_continues_self_raises(self):
        """continues.node == self → ManifestError."""
        m = _manifest([
            _agent("a", spec="task://test#a"),
            _agent("b", spec="task://test#b",
                   continues={"node": "b", "reason": "tight iter"},
                   needs=[_need("a")]),
        ])
        with pytest.raises(ManifestError, match="continues"):
            validate_manifest(m)

    def test_continues_no_reason_raises(self):
        """continues present but reason missing → ManifestError."""
        m = _manifest([
            _agent("a", spec="task://test#a"),
            _agent("b", spec="task://test#b",
                   continues={"node": "a"},  # no 'reason'
                   needs=[_need("a")]),
        ])
        with pytest.raises(ManifestError, match="reason"):
            validate_manifest(m)

    def test_continues_empty_reason_raises(self):
        """continues.reason is empty string → ManifestError."""
        m = _manifest([
            _agent("a", spec="task://test#a"),
            _agent("b", spec="task://test#b",
                   continues={"node": "a", "reason": ""},
                   needs=[_need("a")]),
        ])
        with pytest.raises(ManifestError, match="reason"):
            validate_manifest(m)

    def test_continues_whitespace_reason_raises(self):
        """continues.reason is whitespace-only → ManifestError."""
        m = _manifest([
            _agent("a", spec="task://test#a"),
            _agent("b", spec="task://test#b",
                   continues={"node": "a", "reason": "   "},
                   needs=[_need("a")]),
        ])
        with pytest.raises(ManifestError, match="reason"):
            validate_manifest(m)

    def test_continues_node_field_wrong_type_raises(self):
        """continues.node must be a string → ManifestError."""
        m = _manifest([
            _agent("a", spec="task://test#a"),
            _agent("b", spec="task://test#b",
                   continues={"node": 42, "reason": "iter"},
                   needs=[_need("a")]),
        ])
        with pytest.raises(ManifestError, match="continues"):
            validate_manifest(m)

    def test_continues_not_a_dict_raises(self):
        """continues must be a dict (not a string/list) → ManifestError."""
        node = {"id": "b", "type": "agent", "spec": "task://test#b",
                "continues": "a",  # wrong type
                "needs": [_need("a")]}
        m = _manifest([_agent("a", spec="task://test#a"), node])
        with pytest.raises(ManifestError, match="continues"):
            validate_manifest(m)

    def test_valid_continues_ancestor_passes(self):
        """A valid continues to a direct ancestor passes validation."""
        m = _manifest([
            _agent("a", spec="task://test#a"),
            _agent("b", spec="task://test#b",
                   continues={"node": "a", "reason": "tight iterative refinement"},
                   needs=[_need("a")]),
        ])
        validate_manifest(m)  # must not raise

    def test_valid_continues_transitive_ancestor_passes(self):
        """A valid continues to a transitive (non-direct) ancestor passes."""
        m = _manifest([
            _agent("a", spec="task://test#a"),
            _agent("b", spec="task://test#b", needs=[_need("a")]),
            _agent("c", spec="task://test#c",
                   continues={"node": "a", "reason": "resuming initial arch thread"},
                   needs=[_need("b")]),
        ])
        validate_manifest(m)  # must not raise

    def test_continues_without_needs_edge_raises(self):
        """continues.node must be a transitive ancestor — if 'a' is not an ancestor of 'b'
        because b has no needs, then continues is invalid."""
        m = _manifest([
            _agent("a", spec="task://test#a"),
            _agent("b", spec="task://test#b",
                   continues={"node": "a", "reason": "iter"},
                   # no 'needs' — so 'a' is not an ancestor of 'b'
                   ),
        ])
        with pytest.raises(ManifestError, match="continues"):
            validate_manifest(m)


# ===========================================================================
# 3. Boundary-smell WARN — non-fatal
# ===========================================================================

def _continues_boundary_warns(manifest: dict) -> list[str]:
    """Filter manifest_warns to only continues-boundary-smell warns.

    The reads-scope check adds a second warn category (absent reads:) — the
    DISP tests should filter to the specific warn type they are testing.
    """
    return [w for w in manifest_warns(manifest) if "resumes across" in w]


class TestBoundarySmellWarn:
    def test_continues_crossing_produces_node_emits_warn(self):
        """continues path crosses a produces: node → WARN returned by manifest_warns (non-fatal)."""
        m = _manifest([
            _agent("a", spec="task://test#a"),
            # 'b' has produces: — a durable-artifact boundary
            _agent("b", spec="task://test#b",
                   produces={"note": "experiments/exp-001.md"},
                   needs=[_need("a")]),
            _agent("c", spec="task://test#c",
                   continues={"node": "a", "reason": "refining initial search"},
                   needs=[_need("b")]),  # path from a to c crosses b which has produces:
        ])
        # validation must succeed (non-fatal)
        validate_manifest(m)
        # but continues-boundary warns must surface the boundary crossing
        warns = _continues_boundary_warns(m)
        assert len(warns) == 1
        assert "c" in warns[0]
        assert "boundary" in warns[0].lower() or "produces" in warns[0].lower() or "prefer" in warns[0].lower()

    def test_continues_crossing_human_go_node_emits_warn(self):
        """continues path crosses a human-go node → WARN returned by manifest_warns (non-fatal)."""
        m = _manifest([
            _agent("a", spec="task://test#a"),
            _human_go("gate", needs=[_need("a")]),
            _agent("c", spec="task://test#c",
                   continues={"node": "a", "reason": "re-scoping after review"},
                   needs=[_need("gate")]),  # path from a to c crosses human-go gate
        ])
        validate_manifest(m)  # non-fatal — must not raise
        warns = _continues_boundary_warns(m)
        assert len(warns) == 1
        assert "c" in warns[0]

    def test_continues_no_boundary_crossing_no_continues_warn(self):
        """A continues with no boundary crossing → no continues-boundary WARN.

        NOTE: the reads-scope check may add absent-reads: warns for these
        nodes; this test filters to continues-smell warns only.
        """
        m = _manifest([
            _agent("a", spec="task://test#a"),
            _agent("b", spec="task://test#b",
                   continues={"node": "a", "reason": "tight iteration on same task"},
                   needs=[_need("a")]),  # direct continuation, no produces/human-go between
        ])
        validate_manifest(m)
        warns = _continues_boundary_warns(m)
        assert warns == []

    def test_boundary_warn_still_validates(self):
        """A manifest with a boundary-crossing continues is STILL valid (WARN is non-fatal)."""
        m = _manifest([
            _agent("a", spec="task://test#a"),
            _agent("b", spec="task://test#b",
                   produces={"note": "experiments/exp-001.md"},
                   needs=[_need("a")]),
            _agent("c", spec="task://test#c",
                   continues={"node": "a", "reason": "refining"},
                   needs=[_need("b")]),
        ])
        # Both must succeed: validate_manifest and manifest_warns
        validate_manifest(m)
        warns = manifest_warns(m)
        assert len(warns) >= 1  # has warns but did not raise

    def test_manifest_warns_no_continues_no_continues_warn(self):
        """A manifest with no continues nodes → no continues-boundary warns.

        NOTE: the reads-scope check may add absent-reads: warns; this test
        filters to continues-smell warns only.
        """
        m = _manifest([
            _agent("a", spec="task://test#a"),
            _agent("b", spec="task://test#b", needs=[_need("a")]),
        ])
        validate_manifest(m)
        assert _continues_boundary_warns(m) == []


# ===========================================================================
# 4. Frontier mode line
# ===========================================================================

class TestFrontierModeLine:
    def test_fresh_agent_prints_fresh_mode_and_brief_hint(self, capsys):
        """A fresh agent node (no continues) prints 'FRESH' + a `rv dag brief` hint —
        never the full spec body (DX regression: specs are often multi-KB prose)."""
        long_spec = "task://research#lit-search\n" + ("filler prose line\n" * 50)
        node = _agent("a", spec=long_spec)
        frontier = [FrontierNode(node_id="a", action="dispatch", node=node)]
        _print_frontier(frontier, run_id="test-run")
        out = capsys.readouterr().out
        assert "DISPATCH" in out
        assert "FRESH" in out
        assert "rv dag brief test-run a" in out
        assert "filler prose line" not in out

    def test_continues_agent_prints_continues_mode_and_brief_hint(self, capsys):
        """A continues agent prints 'CONTINUES <node> — <reason>' + a brief hint —
        never the full spec body."""
        long_spec = "task://research#refine\n" + ("filler prose line\n" * 50)
        node = _agent(
            "b",
            spec=long_spec,
            continues={"node": "a", "reason": "tight iteration on ranking"},
        )
        frontier = [FrontierNode(node_id="b", action="dispatch", node=node)]
        _print_frontier(frontier, run_id="test-run")
        out = capsys.readouterr().out
        assert "DISPATCH" in out
        assert "CONTINUES" in out
        assert "a" in out
        assert "tight iteration on ranking" in out
        assert "rv dag brief test-run b" in out
        assert "filler prose line" not in out

    def test_await_go_unaffected(self, capsys):
        """human-go AWAIT-GO items are unaffected by mode changes."""
        node = _human_go("gate")
        frontier = [FrontierNode(node_id="gate", action="await-go", node=node)]
        _print_frontier(frontier, run_id="test-run")
        out = capsys.readouterr().out
        assert "AWAIT-GO" in out
        assert "FRESH" not in out
        assert "CONTINUES" not in out

    def test_empty_frontier_unaffected(self, capsys):
        """Empty frontier still prints its no-items message."""
        _print_frontier([], run_id="test-run")
        out = capsys.readouterr().out
        assert "empty" in out.lower() or "frontier" in out.lower()


# ===========================================================================
# 5. Integration: full run emits WARN via dag run verbs
# ===========================================================================

class TestWarnSurfacedByVerb:
    """Test that manifest_warns is a pure function callable by callers (verbs surface it)."""

    def test_manifest_warns_pure_function_returns_list(self):
        """manifest_warns returns a list of strings for boundary violations."""
        m = _manifest([
            _agent("a", spec="task://test#a"),
            _agent("b", spec="task://test#b",
                   produces={"note": "exp/001.md"},
                   needs=[_need("a")]),
            _agent("c", spec="task://test#c",
                   continues={"node": "a", "reason": "iter"},
                   needs=[_need("b")]),
        ])
        warns = manifest_warns(m)
        assert isinstance(warns, list)
        assert all(isinstance(w, str) for w in warns)

    def test_manifest_warns_multiple_boundary_nodes(self):
        """Multiple agents with boundary-crossing continues each emit a boundary warn."""
        m = _manifest([
            _agent("a", spec="task://test#a"),
            _agent("b", spec="task://test#b",
                   produces={"note": "exp/001.md"},
                   needs=[_need("a")]),
            _agent("c", spec="task://test#c",
                   continues={"node": "a", "reason": "iter-c"},
                   needs=[_need("b")]),
            _agent("d", spec="task://test#d",
                   continues={"node": "a", "reason": "iter-d"},
                   needs=[_need("b")]),
        ])
        # Filter to continues-boundary warns only (the reads-scope check adds absent-reads warns too)
        warns = _continues_boundary_warns(m)
        # Both c and d cross the produces boundary
        assert len(warns) == 2


# ===========================================================================
# 6. Regression: existing schema validation still works
# ===========================================================================

class TestSchemaRegression:
    """Ensure existing validation is not broken by the new spec/continues checks."""

    def test_cycle_still_detected(self):
        """Cyclic manifests still raise ManifestError."""
        m = _manifest([
            _agent("a", spec="task://t#a", needs=[_need("b")]),
            _agent("b", spec="task://t#b", needs=[_need("a")]),
        ])
        with pytest.raises(ManifestError, match="cycle"):
            validate_manifest(m)

    def test_dangling_needs_ref_still_detected(self):
        """Dangling needs.from still raises ManifestError."""
        m = _manifest([
            _agent("a", spec="task://t#a", needs=[_need("ghost")]),
        ])
        with pytest.raises(ManifestError, match="ghost"):
            validate_manifest(m)

    def test_self_need_still_detected(self):
        """Self-reference in needs still raises ManifestError."""
        m = _manifest([
            _agent("a", spec="task://t#a", needs=[_need("a")]),
        ])
        with pytest.raises(ManifestError, match="self"):
            validate_manifest(m)

    def test_valid_fresh_chain_passes(self):
        """A fresh dispatch chain (no continues) with all specs passes."""
        m = _manifest([
            _agent("a", spec="task://t#a"),
            _agent("b", spec="task://t#b", needs=[_need("a")]),
            _human_go("gate", needs=[_need("b")]),
            _agent("c", spec="task://t#c", needs=[_need("gate")]),
        ])
        validate_manifest(m)

    def test_continues_spec_still_required(self):
        """A node with continues must ALSO have spec — both fields required together."""
        m = _manifest([
            _agent("a", spec="task://test#a"),
            _agent("b", spec=None,  # missing spec — even with continues, spec is required
                   continues={"node": "a", "reason": "tight iter"},
                   needs=[_need("a")]),
        ])
        with pytest.raises(ManifestError, match="spec"):
            validate_manifest(m)
