"""test_dag_retry.py — RED/GREEN tests for SR-RETRY (§5I).

Covers:
  1.  schema — max_retries validates (default-0 absent OK; negative → ManifestError;
               over-cap → ManifestError; non-int → ManifestError; on human-go → ManifestError)
  2.  schema — retry_diagnosis_tips validates (absent OK; str OK; list-of-str OK;
               bad type → ManifestError)
  3.  store  — init_nodes initialises attempts/last_failure/failures
  4.  verbs  — N=0 (default): first failure → terminal failed (backward-compat regression)
  5.  verbs  — N=2: failure with attempts<N → resets to pending + attempts++
  6.  verbs  — augmented re-dispatch: rv dag status renders diagnose-first block
               carrying last_failure + directive on attempt k>0
  7.  verbs  — --error REQUIRED when max_retries>0 (D-RETRY-9)
  8.  verbs  — NO diagnose block on first attempt (attempts==0)
  9.  verbs  — exhaustion → terminal failed + failures[] retained + downstream afterok blocked
  10. verbs  — human-go downstream NOT in frontier until retriable branch resolves (§5I.3 check 3)
  11. verbs  — walker.py UNTOUCHED: grep asserts no retry/diagnosis import/call in walker
  12. verbs  — blocked is never retried
  13. verbs  — RETRY_DIAGNOSIS_DIRECTIVE constant is non-empty and contains root-cause language
  14. §5I.3 interaction-check 5: compute_frontier never reads last_failure/failures
"""
from __future__ import annotations

import ast
import importlib
import inspect
import json
import os
import sys
from io import StringIO
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import Config, reset_config_cache
from research_vault.dag.schema import (
    ManifestError,
    validate_manifest,
)
from research_vault.dag.store import RunState, RunStore, StoreError, VALID_STATUSES
from research_vault.dag.walker import compute_frontier, TERMINAL_STATUSES
from research_vault.dag.verbs import (
    RETRY_DIAGNOSIS_DIRECTIVE,
    cmd_complete,
    cmd_run,
    cmd_status,
)


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

def _manifest(nodes: list[dict], run_id: str = "retry-test", global_cap: int = 4) -> dict:
    return {"run_id": run_id, "name": "Retry Test DAG", "global_cap": global_cap, "nodes": nodes}


def _agent(nid: str, max_retries: int | None = None, needs: list | None = None,
           retry_diagnosis_tips: Any = None) -> dict:
    n: dict = {"id": nid, "type": "agent", "label": f"Agent {nid}",
               "spec": "fixture://spec"}
    if max_retries is not None:
        n["max_retries"] = max_retries
    if needs:
        n["needs"] = needs
    if retry_diagnosis_tips is not None:
        n["retry_diagnosis_tips"] = retry_diagnosis_tips
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


def _run_complete(run_id: str, node_id: str, status: str = "succeeded",
                  error: str | None = None, error_file: Path | None = None,
                  capsys=None):
    """Call cmd_complete with a namespace."""
    import argparse
    ns = argparse.Namespace(
        run_id=run_id,
        node_id=node_id,
        status=status,
        error=error,
        error_file=str(error_file) if error_file else None,
    )
    return cmd_complete(ns)


def _run_status(run_id: str):
    import argparse
    ns = argparse.Namespace(run_id=run_id)
    return cmd_status(ns)


def _start_run(tmp_instance: Path, manifest: dict) -> str:
    """Write manifest, rv dag run it, return run_id."""
    mpath = _write_manifest(tmp_instance, manifest)
    import argparse
    ns = argparse.Namespace(manifest=str(mpath))
    rc = cmd_run(ns)
    assert rc == 0
    return manifest["run_id"]


# ===========================================================================
# 1. Schema — max_retries validation
# ===========================================================================

class TestSchemaMaxRetries:
    def test_absent_ok(self):
        """max_retries absent → no error (default 0 behavior)."""
        m = _manifest([_agent("a")])
        validate_manifest(m)  # must not raise

    def test_zero_ok(self):
        m = _manifest([_agent("a", max_retries=0)])
        validate_manifest(m)  # must not raise

    def test_positive_ok(self):
        m = _manifest([_agent("a", max_retries=3)])
        validate_manifest(m)

    def test_cap_ok(self):
        m = _manifest([_agent("a", max_retries=10)])
        validate_manifest(m)

    def test_negative_raises(self):
        m = _manifest([_agent("a", max_retries=-1)])
        with pytest.raises(ManifestError, match="max_retries"):
            validate_manifest(m)

    def test_over_cap_raises(self):
        m = _manifest([_agent("a", max_retries=11)])
        with pytest.raises(ManifestError, match="max_retries"):
            validate_manifest(m)

    def test_non_int_raises(self):
        m = _manifest([_agent("a")])
        m["nodes"][0]["max_retries"] = "3"
        with pytest.raises(ManifestError, match="max_retries"):
            validate_manifest(m)

    def test_float_raises(self):
        m = _manifest([_agent("a")])
        m["nodes"][0]["max_retries"] = 2.5
        with pytest.raises(ManifestError, match="max_retries"):
            validate_manifest(m)

    def test_human_go_with_max_retries_raises(self):
        """max_retries on a human-go node → ManifestError (D-RETRY-1)."""
        m = _manifest([_human_go("gate")])
        m["nodes"][0]["max_retries"] = 2
        with pytest.raises(ManifestError, match="max_retries"):
            validate_manifest(m)


# ===========================================================================
# 2. Schema — retry_diagnosis_tips validation
# ===========================================================================

class TestSchemaRetryDiagnosisTips:
    def test_absent_ok(self):
        m = _manifest([_agent("a", max_retries=2)])
        validate_manifest(m)

    def test_str_ok(self):
        m = _manifest([_agent("a", max_retries=2,
                               retry_diagnosis_tips="Check W&B exit code first.")])
        validate_manifest(m)

    def test_list_of_str_ok(self):
        m = _manifest([_agent("a", max_retries=2,
                               retry_diagnosis_tips=["Check W&B.", "Check OOM."])])
        validate_manifest(m)

    def test_int_raises(self):
        m = _manifest([_agent("a", max_retries=2, retry_diagnosis_tips=42)])
        with pytest.raises(ManifestError, match="retry_diagnosis_tips"):
            validate_manifest(m)

    def test_list_with_non_str_raises(self):
        m = _manifest([_agent("a", max_retries=2,
                               retry_diagnosis_tips=["ok", 42])])
        with pytest.raises(ManifestError, match="retry_diagnosis_tips"):
            validate_manifest(m)


# ===========================================================================
# 3. Store — init_nodes initialises retry fields
# ===========================================================================

class TestStoreInitNodes:
    def test_retry_fields_present(self, tmp_instance):
        from research_vault.config import load_config
        cfg = load_config()
        store = RunStore.from_config(cfg)
        m = _manifest([_agent("a", max_retries=2)])
        mpath = _write_manifest(tmp_instance, m)
        run_state = RunState(
            run_id="retry-store-test",
            manifest_path=str(mpath),
        )
        run_state.init_nodes(m)
        ns = run_state.node_states["a"]
        assert ns["attempts"] == 0
        assert ns["last_failure"] is None
        assert ns["failures"] == []

    def test_retry_fields_present_no_max_retries(self, tmp_instance):
        """Fields present even when max_retries absent (backward-compat)."""
        from research_vault.config import load_config
        cfg = load_config()
        store = RunStore.from_config(cfg)
        m = _manifest([_agent("a")])  # no max_retries
        mpath = _write_manifest(tmp_instance, m)
        run_state = RunState(run_id="store-test-2", manifest_path=str(mpath))
        run_state.init_nodes(m)
        ns = run_state.node_states["a"]
        assert "attempts" in ns
        assert "last_failure" in ns
        assert "failures" in ns


# ===========================================================================
# 4. N=0 (default) — first failure → terminal failed (backward-compat)
# ===========================================================================

class TestN0BackwardCompat:
    def test_first_failure_terminal(self, tmp_instance, capsys):
        """N=0: first --status failed → terminal failed (unchanged behavior)."""
        m = _manifest([_agent("a")])  # no max_retries — defaults to 0
        run_id = _start_run(tmp_instance, m)
        rc = _run_complete(run_id, "a", status="failed", error="some error")
        assert rc == 0
        from research_vault.config import load_config
        store = RunStore.from_config(load_config())
        rs = store.load(run_id)
        assert rs.node_status("a") == "failed"

    def test_first_failure_terminal_no_error_ok(self, tmp_instance, capsys):
        """N=0: --error is optional (degrades gracefully when max_retries==0)."""
        m = _manifest([_agent("a")])
        run_id = _start_run(tmp_instance, m)
        rc = _run_complete(run_id, "a", status="failed")
        assert rc == 0

    def test_attempts_incremented_on_terminal(self, tmp_instance, capsys):
        """N=0: attempts becomes 1 on terminal failure."""
        m = _manifest([_agent("a")])
        run_id = _start_run(tmp_instance, m)
        _run_complete(run_id, "a", status="failed", error="oops")
        from research_vault.config import load_config
        store = RunStore.from_config(load_config())
        rs = store.load(run_id)
        assert rs.node_states["a"]["attempts"] == 1
        assert rs.node_states["a"]["last_failure"] == "oops"
        assert len(rs.node_states["a"]["failures"]) == 1


# ===========================================================================
# 5. N=2: failure with attempts<N → resets to pending + attempts++
# ===========================================================================

class TestRetryReset:
    def test_first_failure_resets_to_pending(self, tmp_instance, capsys):
        m = _manifest([_agent("a", max_retries=2)])
        run_id = _start_run(tmp_instance, m)
        rc = _run_complete(run_id, "a", status="failed", error="boom")
        assert rc == 0
        from research_vault.config import load_config
        store = RunStore.from_config(load_config())
        rs = store.load(run_id)
        assert rs.node_status("a") == "pending", "should be reset to pending, not failed"
        assert rs.node_states["a"]["attempts"] == 1
        assert rs.node_states["a"]["last_failure"] == "boom"
        assert len(rs.node_states["a"]["failures"]) == 1

    def test_second_failure_resets_to_pending(self, tmp_instance, capsys):
        m = _manifest([_agent("a", max_retries=2)])
        run_id = _start_run(tmp_instance, m)
        _run_complete(run_id, "a", status="failed", error="fail1")
        rc = _run_complete(run_id, "a", status="failed", error="fail2")
        assert rc == 0
        from research_vault.config import load_config
        store = RunStore.from_config(load_config())
        rs = store.load(run_id)
        assert rs.node_status("a") == "pending"
        assert rs.node_states["a"]["attempts"] == 2
        assert rs.node_states["a"]["last_failure"] == "fail2"
        assert len(rs.node_states["a"]["failures"]) == 2

    def test_reset_clears_completed_at(self, tmp_instance, capsys):
        """On retry-reset, completed_at is cleared."""
        m = _manifest([_agent("a", max_retries=1)])
        run_id = _start_run(tmp_instance, m)
        _run_complete(run_id, "a", status="failed", error="err")
        from research_vault.config import load_config
        store = RunStore.from_config(load_config())
        rs = store.load(run_id)
        assert rs.node_states["a"]["completed_at"] is None
        assert rs.node_states["a"]["started_at"] is None  # per 5I.5 reset

    def test_reset_retains_last_failure_and_failures(self, tmp_instance, capsys):
        """On retry-reset, last_failure and failures[] are retained."""
        m = _manifest([_agent("a", max_retries=2)])
        run_id = _start_run(tmp_instance, m)
        _run_complete(run_id, "a", status="failed", error="the error")
        from research_vault.config import load_config
        store = RunStore.from_config(load_config())
        rs = store.load(run_id)
        assert rs.node_states["a"]["last_failure"] == "the error"
        assert len(rs.node_states["a"]["failures"]) == 1

    def test_node_reappears_in_frontier_after_reset(self, tmp_instance, capsys):
        """After retry-reset, node reappears as dispatch in frontier."""
        m = _manifest([_agent("a", max_retries=2)])
        run_id = _start_run(tmp_instance, m)
        _run_complete(run_id, "a", status="failed", error="boom")
        # Check frontier via walker directly
        from research_vault.config import load_config
        store = RunStore.from_config(load_config())
        rs = store.load(run_id)
        frontier = compute_frontier(m, rs.node_states, rs.edge_registered_ts, 4)
        dispatch_ids = [f.node_id for f in frontier if f.action == "dispatch"]
        assert "a" in dispatch_ids

    def test_error_file_used_as_summary(self, tmp_instance, capsys):
        """--error-file path reads file content as failure summary."""
        m = _manifest([_agent("a", max_retries=2)])
        run_id = _start_run(tmp_instance, m)
        err_file = tmp_instance / "err.txt"
        err_file.write_text("detailed error output", encoding="utf-8")
        _run_complete(run_id, "a", status="failed", error_file=err_file)
        from research_vault.config import load_config
        store = RunStore.from_config(load_config())
        rs = store.load(run_id)
        assert "detailed error output" in rs.node_states["a"]["last_failure"]


# ===========================================================================
# 6. Augmented re-dispatch: rv dag status renders diagnose-first block
# ===========================================================================

class TestDiagnoseFirstBlock:
    def test_status_renders_diagnose_block_on_retry(self, tmp_instance, capsys):
        """rv dag status shows attempt k+1/N+1 + prior failure + directive for attempts>0."""
        m = _manifest([_agent("a", max_retries=2)])
        run_id = _start_run(tmp_instance, m)
        _run_complete(run_id, "a", status="failed", error="the-prior-failure-msg")
        capsys.readouterr()  # flush
        rc = _run_status(run_id)
        assert rc == 0
        out = capsys.readouterr().out
        assert "the-prior-failure-msg" in out
        assert "RETRY" in out or "attempt" in out.lower() or "PRIOR FAILURE" in out
        assert "DIAGNOSE" in out or "root-cause" in out.lower()

    def test_print_frontier_renders_diagnose_block(self, tmp_instance, capsys):
        """_print_frontier renders diagnose block for pending node with attempts>0."""
        from research_vault.dag.verbs import _print_frontier
        from research_vault.dag.walker import FrontierNode

        node = _agent("a", max_retries=2)
        node_state = {
            "status": "pending",
            "attempts": 1,
            "last_failure": "prior-boom",
            "failures": [{"attempt": 1, "summary": "prior-boom", "ts": 0.0}],
            "started_at": None,
            "completed_at": None,
            "error": None,
        }
        frontier = [FrontierNode(node_id="a", node=node, action="dispatch")]
        capsys.readouterr()
        _print_frontier(frontier, "test-run", node_states={"a": node_state})
        out = capsys.readouterr().out
        assert "prior-boom" in out
        assert "RETRY" in out or "attempt" in out.lower() or "DIAGNOSE" in out

    def test_no_diagnose_block_on_first_attempt(self, tmp_instance, capsys):
        """No diagnose block when attempts==0 (first attempt)."""
        from research_vault.dag.verbs import _print_frontier
        from research_vault.dag.walker import FrontierNode

        node = _agent("a", max_retries=2)
        node_state = {
            "status": "pending",
            "attempts": 0,
            "last_failure": None,
            "failures": [],
            "started_at": None,
            "completed_at": None,
            "error": None,
        }
        frontier = [FrontierNode(node_id="a", node=node, action="dispatch")]
        capsys.readouterr()
        _print_frontier(frontier, "test-run", node_states={"a": node_state})
        out = capsys.readouterr().out
        # Must NOT contain retry diagnostic language
        assert "PRIOR FAILURE" not in out
        assert "DIAGNOSE FIRST" not in out


# ===========================================================================
# 7. --error REQUIRED when max_retries>0 (D-RETRY-9)
# ===========================================================================

class TestErrorRequired:
    def test_missing_error_on_retriable_node_is_error(self, tmp_instance, capsys):
        """--status failed with no --error/--error-file on max_retries>0 → rc != 0."""
        m = _manifest([_agent("a", max_retries=2)])
        run_id = _start_run(tmp_instance, m)
        rc = _run_complete(run_id, "a", status="failed", error=None, error_file=None)
        assert rc != 0
        err = capsys.readouterr().err
        assert "error" in err.lower() or "--error" in err

    def test_error_provided_ok(self, tmp_instance, capsys):
        m = _manifest([_agent("a", max_retries=2)])
        run_id = _start_run(tmp_instance, m)
        rc = _run_complete(run_id, "a", status="failed", error="boom")
        assert rc == 0


# ===========================================================================
# 8. No diagnose block on first attempt — covered in 6 above (TestDiagnoseFirstBlock)
# ===========================================================================


# ===========================================================================
# 9. Exhaustion → terminal failed + failures[] retained + afterok blocked
# ===========================================================================

class TestExhaustion:
    def test_exhaustion_terminal_failed(self, tmp_instance, capsys):
        """N=2: three failures → terminal failed, attempts==3, failures[] len 3."""
        m = _manifest([_agent("a", max_retries=2)])
        run_id = _start_run(tmp_instance, m)
        _run_complete(run_id, "a", status="failed", error="fail1")
        _run_complete(run_id, "a", status="failed", error="fail2")
        _run_complete(run_id, "a", status="failed", error="fail3")
        from research_vault.config import load_config
        store = RunStore.from_config(load_config())
        rs = store.load(run_id)
        assert rs.node_status("a") == "failed"
        assert rs.node_states["a"]["attempts"] == 3
        assert len(rs.node_states["a"]["failures"]) == 3

    def test_exhaustion_failures_retained(self, tmp_instance, capsys):
        m = _manifest([_agent("a", max_retries=1)])
        run_id = _start_run(tmp_instance, m)
        _run_complete(run_id, "a", status="failed", error="err1")
        _run_complete(run_id, "a", status="failed", error="err2")
        from research_vault.config import load_config
        store = RunStore.from_config(load_config())
        rs = store.load(run_id)
        assert rs.node_status("a") == "failed"
        summaries = [f["summary"] for f in rs.node_states["a"]["failures"]]
        assert "err1" in summaries
        assert "err2" in summaries

    def test_downstream_afterok_blocked_on_exhaustion(self, tmp_instance, capsys):
        """On exhaustion, downstream afterok node is NOT in dispatch frontier."""
        m = _manifest([
            _agent("a", max_retries=1),
            _agent("b", needs=[_afterok("a")]),
        ])
        run_id = _start_run(tmp_instance, m)
        _run_complete(run_id, "a", status="failed", error="fail1")
        _run_complete(run_id, "a", status="failed", error="fail2")
        from research_vault.config import load_config
        store = RunStore.from_config(load_config())
        rs = store.load(run_id)
        frontier = compute_frontier(m, rs.node_states, rs.edge_registered_ts, 4)
        dispatch_ids = [f.node_id for f in frontier if f.action == "dispatch"]
        assert "b" not in dispatch_ids  # blocked by failed upstream


# ===========================================================================
# 10. human-go downstream NOT in frontier until retriable branch resolves (§5I.3 check 3)
# ===========================================================================

class TestHumanGoInvariant:
    def test_human_go_not_approvable_during_retry(self, tmp_instance, capsys):
        """human-go downstream of retriable node stays out of frontier during retry."""
        m = _manifest([
            _agent("a", max_retries=2),
            _human_go("gate", needs=[_afterok("a")]),
        ])
        run_id = _start_run(tmp_instance, m)
        # Fail once — "a" resets to pending
        _run_complete(run_id, "a", status="failed", error="flake")
        from research_vault.config import load_config
        store = RunStore.from_config(load_config())
        rs = store.load(run_id)
        # "a" is pending, gate should NOT be in frontier
        frontier = compute_frontier(m, rs.node_states, rs.edge_registered_ts, 4)
        await_ids = [f.node_id for f in frontier if f.action == "await-go"]
        dispatch_ids = [f.node_id for f in frontier if f.action == "dispatch"]
        assert "gate" not in await_ids
        assert "a" in dispatch_ids  # retry dispatch


# ===========================================================================
# 11. walker.py UNTOUCHED — grep asserts no retry/diagnosis import/call
# ===========================================================================

class TestWalkerUntouched:
    def test_walker_has_no_retry_references(self):
        """walker.py must not mention max_retries, attempts, last_failure, failures,
        or RETRY_DIAGNOSIS_DIRECTIVE."""
        walker_path = (
            Path(__file__).parent.parent
            / "src/research_vault/dag/walker.py"
        )
        src = walker_path.read_text(encoding="utf-8")
        forbidden = [
            "max_retries",
            "last_failure",
            "RETRY_DIAGNOSIS_DIRECTIVE",
            "retry_diagnosis",
        ]
        for term in forbidden:
            assert term not in src, (
                f"walker.py must not reference {term!r} — "
                f"the retry/diagnosis layer lives in the imperative path (§5I.1)"
            )

    def test_compute_frontier_pure_ignores_retry_fields(self):
        """compute_frontier source does NOT reference last_failure or attempts."""
        import research_vault.dag.walker as walker_mod
        src = inspect.getsource(walker_mod.compute_frontier)
        assert "last_failure" not in src
        assert "failures" not in src
        # Note: 'attempts' MIGHT appear as a local var name coincidentally,
        # but the semantic check is that it never reads from node_states retry fields.
        # The grep above covers walker.py globally.

    def test_walker_module_imports_unchanged(self):
        """walker module does not import from retry or verbs."""
        import research_vault.dag.walker as walker_mod
        src = inspect.getsource(walker_mod)
        assert "retry" not in src.lower()
        assert "RETRY_DIAGNOSIS_DIRECTIVE" not in src


# ===========================================================================
# 12. blocked is never retried
# ===========================================================================

class TestBlockedNotRetried:
    def test_blocked_is_not_retried(self, tmp_instance, capsys):
        """--status blocked → terminal blocked regardless of max_retries."""
        m = _manifest([_agent("a", max_retries=2)])
        run_id = _start_run(tmp_instance, m)
        rc = _run_complete(run_id, "a", status="blocked")
        assert rc == 0
        from research_vault.config import load_config
        store = RunStore.from_config(load_config())
        rs = store.load(run_id)
        assert rs.node_status("a") == "blocked"
        # attempts stays 0 (never incremented for blocked)
        assert rs.node_states["a"]["attempts"] == 0


# ===========================================================================
# 13. RETRY_DIAGNOSIS_DIRECTIVE constant — non-empty, contains root-cause language
# ===========================================================================

class TestRetryDiagnosisDirective:
    def test_directive_non_empty(self):
        assert isinstance(RETRY_DIAGNOSIS_DIRECTIVE, str)
        assert len(RETRY_DIAGNOSIS_DIRECTIVE) > 0

    def test_directive_contains_root_cause_language(self):
        low = RETRY_DIAGNOSIS_DIRECTIVE.lower()
        assert "root-cause" in low or "root cause" in low
        assert "blind" in low or "repeat" in low

    def test_directive_contains_retry_language(self):
        low = RETRY_DIAGNOSIS_DIRECTIVE.lower()
        assert "retry" in low or "attempt" in low or "failed" in low


# ===========================================================================
# 14. §5I.3 interaction-check 5: compute_frontier byte-for-byte unchanged
# 		(walker file hash compared to pre-SR-RETRY hash via git)
# ===========================================================================

class TestWalkerByteForByte:
    def test_walker_not_modified_by_sr_retry(self):
        """walker.py must have no staged/unstaged changes relative to origin/main."""
        import subprocess
        result = subprocess.run(
            ["git", "diff", "origin/main", "--", "src/research_vault/dag/walker.py"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )
        assert result.stdout.strip() == "", (
            f"walker.py must be UNCHANGED from origin/main:\n{result.stdout}"
        )
