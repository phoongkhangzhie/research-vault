"""test_store_freeze_stub.py — RunStore.create_stub acceptance tests (#49).

THE GAP (confirmed pre-fix): store_freeze_hash/verify_freeze_hash call
RunStore.load(run_id), which raises StoreError if no sidecar RunState exists.
The only creators of a RunState were RunStore.create(full RunState) and
RunState.init_nodes() (the full `rv dag run` walk) — there was no lightweight
"init the sidecar for freeze-only" API. A downstream consumer that runs rv
manifests on a FOREIGN engine (not `rv dag run`) had to hand-roll a stub
RunState via RunStore.create(RunState(...)) directly — a footgun, since the
stub shape (empty node_states/meta) is an implementation detail of freeze.py's
read surface, not a documented public contract.

Coverage:
  1. create_stub() on an absent run_id creates a minimal RunState (run_id +
     manifest_path, empty node_states/edge_registered_ts/meta).
  2. store_freeze_hash succeeds against a stub with NO prior `rv dag run`.
  3. Idempotent: calling create_stub() twice does not clobber; if a real run
     already exists (e.g. node_states populated by a real `rv dag run`), the
     existing RunState is preserved untouched.
  4. Freeze → verify round-trip works entirely off a stub (no dag run ever
     happened for this run_id).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _plan_note(tmp_path: Path, *, covers: str = "[q1-exp1]") -> Path:
    p = tmp_path / "q1-plan.md"
    fm = f"plan_kind: preregistration\ncitekey: q1-plan\ncovers: {covers}"
    p.write_text(f"---\n{fm}\n---\n\nbody", encoding="utf-8")
    return p


def _child_note(notes_dir: Path, child_id: str) -> Path:
    p = notes_dir / f"{child_id}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\ntype: experiments\ncitekey: {child_id}\n"
        f"stance: confirmatory\nplan_role: main\n---\n\n# {child_id}\n",
        encoding="utf-8",
    )
    return p


class TestCreateStub:
    def test_creates_minimal_run_state_when_absent(self, tmp_path):
        from research_vault.dag.store import RunStore

        store = RunStore(tmp_path / "state")
        manifest_path = tmp_path / "m.json"

        rs = store.create_stub("stub-run", manifest_path)

        assert rs.run_id == "stub-run"
        assert rs.manifest_path == str(manifest_path)
        assert rs.node_states == {}
        assert rs.edge_registered_ts == {}
        assert rs.meta == {}

        # And it's actually persisted — load() succeeds without StoreError.
        loaded = store.load("stub-run")
        assert loaded.run_id == "stub-run"
        assert loaded.manifest_path == str(manifest_path)

    def test_store_freeze_hash_succeeds_off_stub_no_prior_dag_run(self, tmp_path):
        """store_freeze_hash must succeed against a stub with NO `rv dag run` ever."""
        from research_vault.dag.store import RunStore
        from research_vault.plan.freeze import store_freeze_hash

        store = RunStore(tmp_path / "state")
        manifest_path = tmp_path / "m.json"
        manifest_path.write_text('{"nodes": []}', encoding="utf-8")

        store.create_stub("foreign-run", manifest_path)

        notes_dir = tmp_path / "notes"
        _child_note(notes_dir, "q1-exp1")
        plan_note = _plan_note(tmp_path)

        # Must not raise StoreError (the gap this task closes).
        store_freeze_hash(store, "foreign-run", plan_note, notes_root=notes_dir)

        rs = store.load("foreign-run")
        assert "plan_freeze" in rs.meta
        assert rs.meta["plan_freeze"]["covers_hash"]

    def test_idempotent_preserves_existing_run_state(self, tmp_path):
        """create_stub() must not clobber an existing (possibly real) RunState."""
        from research_vault.dag.store import RunState, RunStore

        store = RunStore(tmp_path / "state")
        manifest_path = tmp_path / "m.json"

        rs = RunState(run_id="real-run", manifest_path=str(manifest_path))
        rs.node_states["node-a"] = {"status": "succeeded"}
        rs.meta["some_other_key"] = "preserved"
        store.create(rs)

        # create_stub on an already-existing run_id must be a no-op that
        # preserves the real node_states/meta — never overwrite.
        returned = store.create_stub("real-run", manifest_path)

        assert returned.node_states == {"node-a": {"status": "succeeded"}}
        assert returned.meta == {"some_other_key": "preserved"}

        reloaded = store.load("real-run")
        assert reloaded.node_states == {"node-a": {"status": "succeeded"}}
        assert reloaded.meta == {"some_other_key": "preserved"}

    def test_create_stub_called_twice_is_a_noop(self, tmp_path):
        from research_vault.dag.store import RunStore

        store = RunStore(tmp_path / "state")
        manifest_path = tmp_path / "m.json"

        first = store.create_stub("dup-run", manifest_path)
        first.meta["marker"] = "set-after-first-stub"
        store.save(first)

        second = store.create_stub("dup-run", manifest_path)

        assert second.meta == {"marker": "set-after-first-stub"}, (
            "Second create_stub() call must not reset meta written between calls."
        )

    def test_freeze_verify_round_trip_off_stub(self, tmp_path):
        """Full freeze -> verify cycle using ONLY create_stub, never a real dag run."""
        from research_vault.dag.store import RunStore
        from research_vault.plan.freeze import store_freeze_hash, verify_freeze_hash

        store = RunStore(tmp_path / "state")
        manifest_path = tmp_path / "m.json"
        manifest_path.write_text('{"nodes": []}', encoding="utf-8")

        store.create_stub("rt-run", manifest_path)

        notes_dir = tmp_path / "notes"
        _child_note(notes_dir, "q1-exp1")
        plan_note = _plan_note(tmp_path)

        store_freeze_hash(store, "rt-run", plan_note, notes_root=notes_dir)

        ok, msg = verify_freeze_hash(store, "rt-run", plan_note, notes_root=notes_dir)
        assert ok is True, f"Expected match on unmodified plan; got: {msg}"

        # Tamper: add a new child to covers: — verify must now BLOCK.
        tampered_plan = _plan_note(tmp_path, covers="[q1-exp1, q1-exp2]")
        _child_note(notes_dir, "q1-exp2")
        ok2, msg2 = verify_freeze_hash(store, "rt-run", tampered_plan, notes_root=notes_dir)
        assert ok2 is False
        assert msg2 is not None
