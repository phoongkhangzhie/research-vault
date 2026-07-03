"""test_sr_freeze_fix.py — SR-FREEZE-FIX acceptance tests.

Two confirmed holes in plan/freeze.py:
  (a) FAIL-OPEN:  verify_freeze_hash returns (True, None) when plan_freeze absent
                  → "OK matches" on a never-frozen run.
  (b) NON-REPRODUCIBLE: store_freeze_hash stores {covers_hash, plan_note, frozen_at}
                  with NO notes_root → verify re-resolves from caller's --notes-root
                  → same untampered plan gives different verdicts per caller.

Coverage (RED-before-GREEN on all safety tests):
  1. Fail-closed: never-frozen run → (False, "not frozen") NOT (True, None)  [hole a]
  2. CLI fail-closed: rv plan verify-freeze on never-frozen run → exit 1         [hole a]
  3. notes_root stored in freeze meta (abs path)                                  [hole b fix]
  4. Reproducible: freeze under notes_root A; verify with no --notes-root arg
     → SAME OK (uses stored pin, ignores caller)                                  [hole b]
  5. Relocation loud: stored notes_root deleted → FAIL LOUD, never silent OK      [hole b]
  6. Legacy meta (no notes_root field): WARN + exit 1 without explicit --notes-root [back-compat]
  7. Tamper no-regression (stance mutated post-freeze → still BLOCKS)             [regression guard]
  8. Approve hook: verify EXCEPTION → BLOCK (return 1), not warn-and-proceed      [approve hardening]
  9. Approve hook: stored notes_root used (not config re-derive)                  [approve hardening]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
import warnings
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _plan_note(
    tmp_path: Path,
    *,
    plan_kind: str = "preregistration",
    covers: str = "[q1-exp1]",
    body: str = "",
    filename: str = "q1-plan.md",
) -> Path:
    p = tmp_path / filename
    fm = f"plan_kind: {plan_kind}\ncitekey: q1-plan\ncovers: {covers}"
    p.write_text(f"---\n{fm}\n---\n\n{body}", encoding="utf-8")
    return p


def _child_note(
    notes_dir: Path,
    child_id: str,
    *,
    stance: str = "confirmatory",
    plan_role: str = "main",
) -> Path:
    p = notes_dir / f"{child_id}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\ntype: experiments\ncitekey: {child_id}\n"
        f"stance: {stance}\nplan_role: {plan_role}\n---\n\n# {child_id}\n",
        encoding="utf-8",
    )
    return p


def _make_run_store(tmp_path: Path):
    from research_vault.dag.store import RunState, RunStore
    store = RunStore(tmp_path / "state")
    rs = RunState(run_id="fix-test", manifest_path=str(tmp_path / "m.json"))
    store.create(rs)
    return store


def _cfg_file(tmp_path: Path, notes_root: Path | None = None) -> Path:
    """Write a minimal research_vault.toml and return its path."""
    nr = notes_root or (tmp_path / "notes")
    f = tmp_path / "research_vault.toml"
    f.write_text(
        f'instance_root = "{tmp_path}"\n'
        f'notes_root = "{nr}"\n'
        f'state_dir = "{tmp_path / "state"}"\n'
        f'agents_dir = "{tmp_path / ".agents"}"\n'
        f'tasks_dir = "{tmp_path / "tasks"}"\n'
        f'control_dir = "{tmp_path / "control"}"\n'
        '[adapters]\nnotifier = "file"\nbackend = "local"\nsecrets = "env"\n',
        encoding="utf-8",
    )
    return f


# ---------------------------------------------------------------------------
# 1. Fail-closed (hole a) — never-frozen run → (False, msg), NOT (True, None)
# ---------------------------------------------------------------------------

class TestFailClosed:
    def test_never_frozen_returns_false(self, tmp_path):
        """verify_freeze_hash on a run with NO plan_freeze meta → (False, msg).

        RED: Old code returns (True, None) — the fail-open bug.
        """
        from research_vault.plan.freeze import verify_freeze_hash
        from research_vault.dag.store import RunState, RunStore

        store = RunStore(tmp_path / "state")
        rs = RunState(run_id="no-freeze", manifest_path=str(tmp_path / "m.json"))
        store.create(rs)

        plan_note = _plan_note(tmp_path)

        ok, msg = verify_freeze_hash(store, "no-freeze", plan_note)

        assert ok is False, (
            "verify_freeze_hash must return (False, msg) for a never-frozen run — "
            "not (True, None). The fail-open bug allowed a never-frozen run to "
            "pass the K-3 integrity gate silently."
        )
        assert msg is not None
        assert "not frozen" in msg.lower() or "freeze" in msg.lower(), (
            f"Error message should mention 'frozen' or 'freeze'; got: {msg!r}"
        )

    def test_require_frozen_false_is_noop(self, tmp_path):
        """Callers that opt into require_frozen=False still get (True, None) on absent freeze.

        This is the escape-hatch for callers (like rv dag approve gate, which gates
        on presence itself) that legitimately need the old no-op behaviour.
        """
        from research_vault.plan.freeze import verify_freeze_hash
        from research_vault.dag.store import RunState, RunStore

        store = RunStore(tmp_path / "state")
        rs = RunState(run_id="no-freeze-noop", manifest_path=str(tmp_path / "m.json"))
        store.create(rs)

        plan_note = _plan_note(tmp_path)

        ok, msg = verify_freeze_hash(store, "no-freeze-noop", plan_note, require_frozen=False)

        assert ok is True
        assert msg is None


# ---------------------------------------------------------------------------
# 2. CLI fail-closed — rv plan verify-freeze on never-frozen run → exit 1
# ---------------------------------------------------------------------------

class TestCLIFailClosed:
    def test_cli_verify_freeze_never_frozen_exits_1(self, tmp_path, capsys):
        """rv plan verify-freeze on a never-frozen run → exit 1 with 'not frozen' message.

        RED: Old CLI returned exit 0 (the fail-open propagated to the CLI).
        """
        from research_vault.plan.verbs import run as plan_run, build_parser
        from research_vault.dag.store import RunState, RunStore

        cfg_file = _cfg_file(tmp_path)
        old = os.environ.get("RESEARCH_VAULT_CONFIG")
        os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_file)

        try:
            store = RunStore(tmp_path / "state")
            rs = RunState(run_id="cli-no-freeze", manifest_path=str(tmp_path / "m.json"))
            store.create(rs)

            plan_note = _plan_note(tmp_path)

            parent = argparse.ArgumentParser()
            subs = parent.add_subparsers()
            build_parser(subs)
            args = parent.parse_args([
                "plan", "verify-freeze", "cli-no-freeze", str(plan_note),
            ])
            result = plan_run(args)

            assert result == 1, (
                "rv plan verify-freeze on a never-frozen run must exit 1 — "
                "not 0 (the old fail-open). The gate must refuse to pass a run "
                "that was never pre-registered."
            )
            _, err = capsys.readouterr()
            assert "not frozen" in err.lower() or "freeze" in err.lower() or "not frozen" in (err + capsys.readouterr()[1]).lower(), (
                f"Stderr should mention 'frozen'/'freeze'; got: {err!r}"
            )
        finally:
            if old is None:
                os.environ.pop("RESEARCH_VAULT_CONFIG", None)
            else:
                os.environ["RESEARCH_VAULT_CONFIG"] = old


# ---------------------------------------------------------------------------
# 3. notes_root stored in freeze meta (absolute)
# ---------------------------------------------------------------------------

class TestNotesRootStored:
    def test_store_freeze_hash_writes_notes_root(self, tmp_path):
        """store_freeze_hash must store notes_root (absolute) in plan_freeze meta.

        RED: Old code stored only {covers_hash, plan_note, frozen_at} — no notes_root.
        """
        from research_vault.plan.freeze import store_freeze_hash

        notes_dir = tmp_path / "notes" / "experiments"
        _child_note(notes_dir, "q1-exp1")
        plan_note = _plan_note(tmp_path)
        store = _make_run_store(tmp_path)

        store_freeze_hash(store, "fix-test", plan_note, notes_root=notes_dir)

        loaded = store.load("fix-test")
        plan_freeze = loaded.meta.get("plan_freeze")
        assert plan_freeze is not None
        assert "notes_root" in plan_freeze, (
            "store_freeze_hash must store 'notes_root' in plan_freeze meta. "
            "Without it, verify re-derives from the caller's config → non-reproducible."
        )
        stored_nr = plan_freeze["notes_root"]
        # Must be absolute
        assert Path(stored_nr).is_absolute(), (
            f"Stored notes_root must be an absolute path, got: {stored_nr!r}"
        )
        # Must match the resolved absolute path of what we passed
        assert Path(stored_nr) == notes_dir.resolve()


# ---------------------------------------------------------------------------
# 4. Reproducible across callers (hole b)
# ---------------------------------------------------------------------------

class TestReproducible:
    def test_cross_caller_notes_root_invariant(self, tmp_path):
        """Freeze under notes_root A; verify WITHOUT passing notes_root → still OK.

        The stored pin makes verify caller-invariant. Without the fix, a caller
        without --notes-root would auto-resolve from config (which may differ from
        freeze-time) and return a false FAIL or false OK.

        RED: Old code re-derives notes_root from the caller's arg — if absent,
        falls back to config, which may be a different directory.
        """
        from research_vault.plan.freeze import store_freeze_hash, verify_freeze_hash
        from research_vault.dag.store import RunState, RunStore

        # Use a notes_root that is NOT the config default (tmp_path/notes/experiments)
        notes_dir = tmp_path / "custom_notes_root" / "experiments"
        _child_note(notes_dir, "q1-exp1")
        plan_note = _plan_note(tmp_path)

        store = RunStore(tmp_path / "state")
        rs = RunState(run_id="repro-test", manifest_path=str(tmp_path / "m.json"))
        store.create(rs)

        # Freeze under the custom notes_root
        store_freeze_hash(store, "repro-test", plan_note, notes_root=notes_dir)

        # Verify WITHOUT passing notes_root — should use the stored pin
        ok, msg = verify_freeze_hash(store, "repro-test", plan_note)
        # notes_root=None: verify should read the stored notes_root

        assert ok is True, (
            "verify_freeze_hash must return OK when called without notes_root if "
            "the stored pin resolves correctly — caller-invariant. "
            f"Got ok={ok!r}, msg={msg!r}"
        )
        assert msg is None

    def test_cross_caller_different_notes_root_arg_still_ok(self, tmp_path):
        """Freeze under notes_root A; verify with a DIFFERENT notes_root B arg → still OK.

        verify_freeze_hash must use the STORED notes_root, ignoring the caller's arg
        (unless it's explicitly an override/re-pin, which the current spec marks as LOUD).
        """
        from research_vault.plan.freeze import store_freeze_hash, verify_freeze_hash
        from research_vault.dag.store import RunState, RunStore

        notes_dir_a = tmp_path / "notes_a" / "experiments"
        _child_note(notes_dir_a, "q1-exp1")
        plan_note = _plan_note(tmp_path)

        # notes_dir_b exists but has no notes → re-deriving from here would give
        # MISSING sentinels → a DIFFERENT hash → false FAIL on untampered plan
        notes_dir_b = tmp_path / "notes_b" / "experiments"
        notes_dir_b.mkdir(parents=True, exist_ok=True)

        store = RunStore(tmp_path / "state")
        rs = RunState(run_id="cross-caller", manifest_path=str(tmp_path / "m.json"))
        store.create(rs)

        store_freeze_hash(store, "cross-caller", plan_note, notes_root=notes_dir_a)

        # Caller passes notes_root_b — old bug: would re-derive from B → false FAIL
        ok, msg = verify_freeze_hash(
            store, "cross-caller", plan_note, notes_root=notes_dir_b
        )

        assert ok is True, (
            "verify_freeze_hash must use the STORED notes_root (A), not the caller's "
            f"notes_root arg (B). Got ok={ok!r}, msg={msg!r}"
        )


# ---------------------------------------------------------------------------
# 5. Relocation loud
# ---------------------------------------------------------------------------

class TestRelocationLoud:
    def test_stored_notes_root_missing_fails_loud(self, tmp_path):
        """Stored notes_root no longer exists → FAIL LOUD, never silent OK.

        The spec: 'frozen notes_root <path> not found — pass --notes-root to re-pin'
        Never silently fall back to config.
        """
        from research_vault.plan.freeze import store_freeze_hash, verify_freeze_hash
        from research_vault.dag.store import RunState, RunStore
        import shutil

        notes_dir = tmp_path / "notes" / "experiments"
        _child_note(notes_dir, "q1-exp1")
        plan_note = _plan_note(tmp_path)

        store = RunStore(tmp_path / "state")
        rs = RunState(run_id="relocate-test", manifest_path=str(tmp_path / "m.json"))
        store.create(rs)

        store_freeze_hash(store, "relocate-test", plan_note, notes_root=notes_dir)

        # Simulate relocation by deleting the notes_root
        shutil.rmtree(str(notes_dir))

        ok, msg = verify_freeze_hash(store, "relocate-test", plan_note)

        assert ok is False, (
            "verify_freeze_hash must FAIL when the stored notes_root no longer exists — "
            "never silently pass or fall back to config. Got ok=True."
        )
        assert msg is not None
        assert "re-pin" in msg.lower() or "notes-root" in msg.lower() or "not found" in msg.lower(), (
            f"Error message should instruct to re-pin; got: {msg!r}"
        )


# ---------------------------------------------------------------------------
# 6. Legacy meta back-compat
# ---------------------------------------------------------------------------

class TestLegacyMeta:
    def test_legacy_meta_without_notes_root_warns_and_fails(self, tmp_path):
        """A plan_freeze without 'notes_root' (pre-fix legacy) → WARN + require explicit --notes-root.

        Spec: 'treat as legacy pin unknown — WARN + require explicit --notes-root
        rather than guessing.'
        """
        from research_vault.plan.freeze import verify_freeze_hash
        from research_vault.dag.store import RunState, RunStore

        store = RunStore(tmp_path / "state")
        rs = RunState(run_id="legacy-test", manifest_path=str(tmp_path / "m.json"))
        # Inject a legacy plan_freeze WITHOUT notes_root field
        rs.meta["plan_freeze"] = {
            "covers_hash": "aabbcc" * 10 + "aabb",  # 64 hex chars (fake)
            "plan_note": str(tmp_path / "q1-plan.md"),
            "frozen_at": 1700000000.0,
            # No 'notes_root' key — this is the legacy format
        }
        store.create(rs)

        plan_note = _plan_note(tmp_path)

        # Without explicit notes_root: should WARN and fail (or fail with a clear msg)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ok, msg = verify_freeze_hash(store, "legacy-test", plan_note)

        # Must NOT silently pass
        assert ok is False, (
            "Legacy plan_freeze without notes_root should not silently pass — "
            "the stored hash is unverifiable without knowing where the notes live."
        )
        # Must either warn or include the info in the error message
        warned = any(
            "notes_root" in str(w.message).lower() or
            "legacy" in str(w.message).lower() or
            "re-pin" in str(w.message).lower()
            for w in caught
        )
        msg_explains = msg is not None and (
            "notes_root" in msg.lower() or
            "re-pin" in msg.lower() or
            "--notes-root" in msg.lower() or
            "legacy" in msg.lower()
        )
        assert warned or msg_explains, (
            f"Legacy meta: must emit a warning or a message explaining the need for "
            f"--notes-root. Got ok={ok!r}, msg={msg!r}, warnings={[str(w.message) for w in caught]}"
        )

    def test_legacy_meta_with_explicit_notes_root_still_works(self, tmp_path):
        """Legacy meta + explicit --notes-root: verify should proceed using the arg.

        This is the re-pin path for relocated/legacy freezes.
        """
        from research_vault.plan.freeze import store_freeze_hash, verify_freeze_hash
        from research_vault.dag.store import RunState, RunStore

        notes_dir = tmp_path / "notes" / "experiments"
        _child_note(notes_dir, "q1-exp1")
        plan_note = _plan_note(tmp_path)

        # Build a real freeze, then strip notes_root to simulate legacy format
        store = RunStore(tmp_path / "state")
        rs = RunState(run_id="legacy-pin", manifest_path=str(tmp_path / "m.json"))
        store.create(rs)
        store_freeze_hash(store, "legacy-pin", plan_note, notes_root=notes_dir)

        # Strip the notes_root field to simulate legacy
        loaded = store.load("legacy-pin")
        del loaded.meta["plan_freeze"]["notes_root"]
        store.save(loaded)

        # Now verify WITH explicit notes_root (the re-pin path)
        ok, msg = verify_freeze_hash(
            store, "legacy-pin", plan_note, notes_root=notes_dir
        )

        assert ok is True, (
            "Legacy meta + explicit --notes-root should succeed (re-pin path). "
            f"Got ok={ok!r}, msg={msg!r}"
        )


# ---------------------------------------------------------------------------
# 7. No regression on tamper (mutation test)
# ---------------------------------------------------------------------------

class TestTamperNoRegression:
    def test_tamper_stance_still_blocks(self, tmp_path):
        """Post-freeze stance mutation still causes verify to FAIL.

        Regression guard: the notes_root pin must not break tamper detection.
        """
        from research_vault.plan.freeze import store_freeze_hash, verify_freeze_hash
        from research_vault.dag.store import RunState, RunStore

        notes_dir = tmp_path / "notes" / "experiments"
        _child_note(notes_dir, "q1-exp1", stance="confirmatory", plan_role="main")
        plan_note = _plan_note(tmp_path, covers="[q1-exp1]")

        store = RunStore(tmp_path / "state")
        rs = RunState(run_id="tamper-stance", manifest_path=str(tmp_path / "m.json"))
        store.create(rs)

        store_freeze_hash(store, "tamper-stance", plan_note, notes_root=notes_dir)

        # Tamper: change stance after freeze
        (notes_dir / "q1-exp1.md").write_text(
            "---\ntype: experiments\ncitekey: q1-exp1\n"
            "stance: exploratory\nplan_role: main\n---\n\n# q1-exp1\n",
            encoding="utf-8",
        )

        ok, msg = verify_freeze_hash(store, "tamper-stance", plan_note)

        assert ok is False, (
            "A post-freeze stance mutation must still BLOCK verify. "
            "The notes_root pin must not break tamper detection."
        )
        assert msg is not None

    def test_tamper_covers_set_still_blocks(self, tmp_path):
        """Adding a child to covers: after freeze still causes verify to FAIL."""
        from research_vault.plan.freeze import store_freeze_hash, verify_freeze_hash
        from research_vault.dag.store import RunState, RunStore

        notes_dir = tmp_path / "notes" / "experiments"
        _child_note(notes_dir, "q1-exp1")
        plan_note = _plan_note(tmp_path, covers="[q1-exp1]")

        store = RunStore(tmp_path / "state")
        rs = RunState(run_id="tamper-covers", manifest_path=str(tmp_path / "m.json"))
        store.create(rs)

        store_freeze_hash(store, "tamper-covers", plan_note, notes_root=notes_dir)

        # Tamper: add a second child to covers:
        _child_note(notes_dir, "q1-exp2")
        plan_note.write_text(
            "---\nplan_kind: preregistration\ncitekey: q1-plan\n"
            "covers: [q1-exp1, q1-exp2]\n---\n\n# tampered\n",
            encoding="utf-8",
        )

        ok, msg = verify_freeze_hash(store, "tamper-covers", plan_note)

        assert ok is False, (
            "Expanding covers: after freeze must still BLOCK verify."
        )


# ---------------------------------------------------------------------------
# 8. Approve hook: exception → BLOCK (not warn-and-proceed)
# ---------------------------------------------------------------------------

class TestApproveHookException:
    """dag/verbs.py cmd_approve K-3 hook: on verify EXCEPTION → return 1."""

    def _setup_run_with_freeze(self, tmp_path: Path):
        """Create a full run with a freeze hash stored; return (store, manifest_path, plan_note)."""
        from research_vault.dag.store import RunState, RunStore
        from research_vault.plan.freeze import store_freeze_hash

        notes_dir = tmp_path / "notes" / "experiments"
        _child_note(notes_dir, "q1-exp1")
        plan_note = _plan_note(tmp_path, covers="[q1-exp1]")

        nodes = [
            {"id": "plan", "type": "agent", "spec": "task://demo#plan", "needs": []},
            {"id": "plan-critic", "type": "agent", "spec": "task://demo#critic",
             "needs": [{"from": "plan", "edge": "afterok"}]},
            {"id": "human-go-plan", "type": "human-go", "label": "gate",
             "needs": [{"from": "plan-critic", "edge": "afterok"}]},
            {"id": "human-go-findings", "type": "human-go", "label": "findings",
             "needs": [{"from": "human-go-plan", "edge": "afterok"}]},
        ]
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps({"run_id": "exc-test", "name": "test", "global_cap": 4, "nodes": nodes}))

        store = RunStore(tmp_path / "state")
        rs = RunState(run_id="exc-test", manifest_path=str(manifest_path))
        rs.init_nodes(json.loads(manifest_path.read_text()))
        for nid in ("plan", "plan-critic", "human-go-plan"):
            rs.set_node_status(nid, "succeeded")
        rs.set_node_status("human-go-findings", "awaiting-go")
        store.create(rs)

        store_freeze_hash(store, "exc-test", plan_note, notes_root=notes_dir)
        return store, manifest_path, plan_note, notes_dir

    def test_approve_hook_blocks_on_verify_exception(self, tmp_path, monkeypatch):
        """K-3 hook: verify EXCEPTION → return 1 (BLOCK), not warn-and-proceed.

        RED: Old code caught exceptions and continued with a warning (a second fail-open).
        """
        from research_vault.dag import verbs as dag_verbs

        cfg_file = _cfg_file(tmp_path)
        old_env = os.environ.get("RESEARCH_VAULT_CONFIG")
        os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_file)

        try:
            store, manifest_path, plan_note, notes_dir = self._setup_run_with_freeze(tmp_path)

            # Monkeypatch verify_freeze_hash to raise an exception
            def _raise_exc(*args, **kwargs):
                raise RuntimeError("Simulated integrity-check failure")

            monkeypatch.setattr(
                "research_vault.plan.freeze.verify_freeze_hash",
                _raise_exc,
            )

            args = argparse.Namespace(run_id="exc-test", node_id="human-go-findings")
            result = dag_verbs.cmd_approve(args)

            assert result == 1, (
                "rv dag approve must return 1 (BLOCK) when verify_freeze_hash raises — "
                "not 0 (warn-and-proceed). An integrity gate must fail-closed on "
                "inability-to-verify. Old code: 'warn ... (proceeding)'."
            )
        finally:
            if old_env is None:
                os.environ.pop("RESEARCH_VAULT_CONFIG", None)
            else:
                os.environ["RESEARCH_VAULT_CONFIG"] = old_env


# ---------------------------------------------------------------------------
# 9. Approve hook: uses stored notes_root (not config re-derive)
# ---------------------------------------------------------------------------

class TestApproveHookStoredNotesRoot:
    """dag/verbs.py cmd_approve uses plan_freeze['notes_root'], not cfg re-derive."""

    def test_approve_uses_stored_notes_root_not_config(self, tmp_path):
        """Approve hook must use the stored notes_root, not cfg.notes_root/experiments.

        Setup: freeze under notes_root_A (not the config default notes/experiments).
        The config notes_root points to notes_root_B (different dir).
        After freeze, verify via rv dag approve — must pass (using stored A),
        not fail (which would happen if it re-derives from B).

        RED: Old code had `notes_root = cfg.notes_root / "experiments"` at L774
        which re-derives from the caller's config, ignoring the stored pin.
        """
        from research_vault.dag import verbs as dag_verbs
        from research_vault.dag.store import RunState, RunStore
        from research_vault.plan.freeze import store_freeze_hash

        # notes_root_A: where we actually freeze (has the child note)
        notes_dir_a = tmp_path / "notes_a" / "experiments"
        _child_note(notes_dir_a, "q1-exp1")
        plan_note = _plan_note(tmp_path, covers="[q1-exp1]")

        # Config points to notes_root_B: different dir, no notes there
        notes_dir_b = tmp_path / "notes_b" / "experiments"
        notes_dir_b.mkdir(parents=True, exist_ok=True)

        cfg_file = _cfg_file(tmp_path, notes_root=tmp_path / "notes_b")
        old_env = os.environ.get("RESEARCH_VAULT_CONFIG")
        os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_file)

        try:
            nodes = [
                {"id": "plan", "type": "agent", "spec": "task://demo#plan", "needs": []},
                {"id": "plan-critic", "type": "agent", "spec": "task://demo#critic",
                 "needs": [{"from": "plan", "edge": "afterok"}]},
                {"id": "human-go-plan", "type": "human-go", "label": "gate",
                 "needs": [{"from": "plan-critic", "edge": "afterok"}]},
                {"id": "human-go-findings", "type": "human-go", "label": "findings",
                 "needs": [{"from": "human-go-plan", "edge": "afterok"}]},
            ]
            manifest_path = tmp_path / "manifest.json"
            manifest_path.write_text(json.dumps({"run_id": "stored-nr", "name": "t", "global_cap": 4, "nodes": nodes}))

            store = RunStore(tmp_path / "state")
            rs = RunState(run_id="stored-nr", manifest_path=str(manifest_path))
            rs.init_nodes(json.loads(manifest_path.read_text()))
            for nid in ("plan", "plan-critic", "human-go-plan"):
                rs.set_node_status(nid, "succeeded")
            rs.set_node_status("human-go-findings", "awaiting-go")
            store.create(rs)

            # Freeze under notes_dir_a (NOT notes_dir_b which is the config default)
            store_freeze_hash(store, "stored-nr", plan_note, notes_root=notes_dir_a)

            args = argparse.Namespace(run_id="stored-nr", node_id="human-go-findings")
            result = dag_verbs.cmd_approve(args)

            assert result == 0, (
                "rv dag approve must pass when using the stored notes_root (A), even "
                "though config.notes_root points to a different directory (B). "
                f"Got result={result!r} — old bug re-derives from B → false FAIL."
            )
        finally:
            if old_env is None:
                os.environ.pop("RESEARCH_VAULT_CONFIG", None)
            else:
                os.environ["RESEARCH_VAULT_CONFIG"] = old_env
