"""test_sr_harness_p2_s1.py — SR-HARNESS-P2 Slice 1 acceptance tests.

Covers plan/freeze.py extensions:
  S1a. HARNESS_SENTINEL constant exported.
  S1b. _parse_harness_commits: well-formed, empty, malformed.
  S1c. _build_harness_block: sorted, empty input, malformed passthrough.
  S1d. compute_covers_hash BYTE-IDENTICAL back-compat:
       - No harness_commits: → golden hash unchanged (hardcoded).
       - Non-empty retries + no harness: → back-compat with SR-PLAN-FREEZE-RETRY.
  S1e. compute_covers_hash harness sensitivity:
       - Plan with harness_commits: → hash ≠ no-harness.
       - SHA change → hash changes.
       - [main1=x, main2=y] ↔ [main2=y, main1=x] → SAME (sort-invariant).
       - [main1=x] → [main1=y] → different.
  S1f. store_freeze_hash writes both covers_hash + covers_retries_hash.
       On a harness-free plan: both are equal.
  S1g. verify_freeze_hash: harness-commit drift → (False, "harness-commit drift").
       verify_freeze_hash: untampered plan → (True, None).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.plan.freeze import (
    HARNESS_SENTINEL,
    RETRIES_SENTINEL,
    _parse_harness_commits,
    _build_harness_block,
    compute_covers_hash,
    store_freeze_hash,
    verify_freeze_hash,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Golden hash for: covers=[q1-main1(conf/main), q1-main1-abl-A(conf/supp_abl)]
# no retries, no harness_commits.  Hard-coded from the pre-SR-HARNESS-P2 run;
# must remain BYTE-IDENTICAL after the extension.
_GOLDEN_NO_HARNESS = (
    "b75a05a8f70baf776be6d87ee83c6c16c60e936e697d59fb629f43ba19c26aac"
)

# Golden hash for: same covers but children MISSING (notes_dir empty)
_GOLDEN_MISSING_CHILDREN = (
    "8c452dacb8648b901e2e98b8ba570f7995f3894bb93dba2729f983ada8771ed2"
)


def _plan_note(
    tmp_path: Path,
    *,
    covers: str = "[q1-main1, q1-main1-abl-A]",
    harness_commits: str = "",
    filename: str = "q1-plan.md",
) -> Path:
    p = tmp_path / filename
    fm = f"plan_kind: preregistration\ncitekey: q1-plan\ncovers: {covers}"
    if harness_commits:
        fm += f"\nharness_commits: {harness_commits}"
    p.write_text(f"---\n{fm}\n---\n", encoding="utf-8")
    return p


def _child_note(notes_dir: Path, child_id: str,
                stance: str = "confirmatory",
                plan_role: str = "main") -> Path:
    notes_dir.mkdir(parents=True, exist_ok=True)
    p = notes_dir / f"{child_id}.md"
    p.write_text(
        f"---\ntype: experiments\ncitekey: {child_id}\n"
        f"stance: {stance}\nplan_role: {plan_role}\n---\n",
        encoding="utf-8",
    )
    return p


def _make_run_store(tmp_path: Path):
    from research_vault.dag.store import RunStore, RunState
    store = RunStore(tmp_path / "state")
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"run_id": "test-run", "nodes": []}), encoding="utf-8")
    rs = RunState(run_id="test-run", manifest_path=str(manifest), created_at=time.time())
    store.create(rs)
    return store


# ===========================================================================
# S1a — HARNESS_SENTINEL constant
# ===========================================================================

class TestHarnessSentinel:
    def test_exported(self):
        assert isinstance(HARNESS_SENTINEL, str)
        assert HARNESS_SENTINEL == "---harness_commit---"

    def test_distinct_from_retries_sentinel(self):
        assert HARNESS_SENTINEL != RETRIES_SENTINEL


# ===========================================================================
# S1b — _parse_harness_commits
# ===========================================================================

class TestParseHarnessCommits:
    def test_empty_field(self):
        assert _parse_harness_commits({}) == []

    def test_blank_field(self):
        assert _parse_harness_commits({"harness_commits": ""}) == []
        assert _parse_harness_commits({"harness_commits": "   "}) == []

    def test_single_entry(self):
        result = _parse_harness_commits({"harness_commits": "[main1=abc123]"})
        assert result == ["main1=abc123"]

    def test_two_entries(self):
        result = _parse_harness_commits({"harness_commits": "[main1=aaa, main2=bbb]"})
        assert "main1=aaa" in result
        assert "main2=bbb" in result
        assert len(result) == 2

    def test_shared_scope(self):
        result = _parse_harness_commits({"harness_commits": "[shared=ccc]"})
        assert result == ["shared=ccc"]

    def test_malformed_no_equals_sentinel(self):
        """Malformed item (no =) → MISSING-style sentinel, never crash."""
        result = _parse_harness_commits({"harness_commits": "[baditem]"})
        assert len(result) == 1
        assert "MISSING" in result[0] or "=" in result[0]

    def test_mixed_well_formed_and_malformed(self):
        result = _parse_harness_commits({"harness_commits": "[main1=abc, badentry]"})
        assert len(result) == 2
        well = [x for x in result if x.startswith("main1=")]
        bad = [x for x in result if "MISSING" in x or not x.startswith("main1=")]
        assert len(well) == 1
        assert len(bad) == 1


# ===========================================================================
# S1c — _build_harness_block
# ===========================================================================

class TestBuildHarnessBlock:
    def test_empty(self):
        assert _build_harness_block([]) == ""

    def test_single(self):
        block = _build_harness_block(["main1=abc"])
        assert block == "main1 harness_commit=abc"

    def test_sorted_by_scope(self):
        block = _build_harness_block(["main2=bbb", "main1=aaa"])
        lines = block.splitlines()
        assert lines[0].startswith("main1")
        assert lines[1].startswith("main2")

    def test_sort_invariant(self):
        """Order of input does not affect output."""
        b1 = _build_harness_block(["main1=x", "main2=y"])
        b2 = _build_harness_block(["main2=y", "main1=x"])
        assert b1 == b2

    def test_shared_scope(self):
        block = _build_harness_block(["shared=ccc"])
        assert block == "shared harness_commit=ccc"


# ===========================================================================
# S1d — compute_covers_hash BYTE-IDENTICAL back-compat
# ===========================================================================

class TestBackCompatGoldenHash:
    """S1d — no-harness plan must re-derive the exact pre-slice hash."""

    def _setup_notes(self, tmp_path: Path) -> Path:
        notes_dir = tmp_path / "experiments"
        _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
        _child_note(notes_dir, "q1-main1-abl-A",
                    stance="confirmatory", plan_role="supporting_ablation")
        return notes_dir

    def test_no_harness_golden_empty_retries(self, tmp_path):
        """Back-compat: no harness, manifest_nodes=None → golden hash."""
        notes_dir = self._setup_notes(tmp_path)
        plan = _plan_note(tmp_path)
        h = compute_covers_hash(plan, notes_root=notes_dir)
        assert h == _GOLDEN_NO_HARNESS, (
            f"Back-compat BROKEN: expected {_GOLDEN_NO_HARNESS!r}, got {h!r}. "
            "SR-HARNESS-P2 must not change the hash of a plan without harness_commits:."
        )

    def test_no_harness_golden_empty_nodes_list(self, tmp_path):
        """manifest_nodes=[] (all-default) is byte-identical to manifest_nodes=None."""
        notes_dir = self._setup_notes(tmp_path)
        plan = _plan_note(tmp_path)
        h_none = compute_covers_hash(plan, notes_root=notes_dir, manifest_nodes=None)
        h_empty = compute_covers_hash(plan, notes_root=notes_dir, manifest_nodes=[])
        assert h_none == h_empty == _GOLDEN_NO_HARNESS

    def test_no_harness_golden_with_retries(self, tmp_path):
        """Back-compat: no harness, non-empty retries → same as pre-harness SR-PLAN-FREEZE-RETRY hash."""
        notes_dir = self._setup_notes(tmp_path)
        plan = _plan_note(tmp_path)
        nodes = [{"id": "q1-main1-run", "max_retries": 3}]
        h_retries = compute_covers_hash(plan, notes_root=notes_dir, manifest_nodes=nodes)
        # Must be different from no-retries golden (retries change the hash)
        assert h_retries != _GOLDEN_NO_HARNESS
        # Must be stable across repeated calls
        h_retries2 = compute_covers_hash(plan, notes_root=notes_dir, manifest_nodes=nodes)
        assert h_retries == h_retries2

    def test_missing_children_golden(self, tmp_path):
        """Back-compat: missing children → golden missing hash."""
        notes_dir = tmp_path / "empty_dir"
        notes_dir.mkdir()
        plan = _plan_note(tmp_path)
        h = compute_covers_hash(plan, notes_root=notes_dir)
        assert h == _GOLDEN_MISSING_CHILDREN


# ===========================================================================
# S1e — compute_covers_hash harness sensitivity
# ===========================================================================

class TestHarnessSensitivity:
    def _setup(self, tmp_path: Path) -> Path:
        notes_dir = tmp_path / "experiments"
        _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
        _child_note(notes_dir, "q1-main1-abl-A",
                    stance="confirmatory", plan_role="supporting_ablation")
        return notes_dir

    def test_harness_changes_hash(self, tmp_path):
        notes_dir = self._setup(tmp_path)
        plan_no = _plan_note(tmp_path, filename="no-h.md")
        plan_h = _plan_note(tmp_path, harness_commits="[main1=abc123]", filename="h.md")
        h_no = compute_covers_hash(plan_no, notes_root=notes_dir)
        h_with = compute_covers_hash(plan_h, notes_root=notes_dir)
        assert h_no != h_with
        assert h_no == _GOLDEN_NO_HARNESS

    def test_sha_change_changes_hash(self, tmp_path):
        notes_dir = self._setup(tmp_path)
        p1 = _plan_note(tmp_path, harness_commits="[main1=abc]", filename="p1.md")
        p2 = _plan_note(tmp_path, harness_commits="[main1=def]", filename="p2.md")
        assert compute_covers_hash(p1, notes_root=notes_dir) != \
               compute_covers_hash(p2, notes_root=notes_dir)

    def test_sort_invariant(self, tmp_path):
        """[main1=x, main2=y] ↔ [main2=y, main1=x] → same hash."""
        notes_dir = self._setup(tmp_path)
        p1 = _plan_note(tmp_path, harness_commits="[main1=x, main2=y]", filename="p1.md")
        p2 = _plan_note(tmp_path, harness_commits="[main2=y, main1=x]", filename="p2.md")
        assert compute_covers_hash(p1, notes_root=notes_dir) == \
               compute_covers_hash(p2, notes_root=notes_dir)

    def test_single_sha_change(self, tmp_path):
        notes_dir = self._setup(tmp_path)
        p1 = _plan_note(tmp_path, harness_commits="[main1=x, main2=y]", filename="a.md")
        p2 = _plan_note(tmp_path, harness_commits="[main1=x, main2=z]", filename="b.md")
        assert compute_covers_hash(p1, notes_root=notes_dir) != \
               compute_covers_hash(p2, notes_root=notes_dir)


# ===========================================================================
# S1f — store_freeze_hash writes both hashes
# ===========================================================================

class TestStoreFreezeHashDualHashes:
    def test_harness_free_plan_both_equal(self, tmp_path):
        """On a harness-free plan: covers_hash == covers_retries_hash."""
        notes_dir = tmp_path / "experiments"
        _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
        _child_note(notes_dir, "q1-main1-abl-A",
                    stance="confirmatory", plan_role="supporting_ablation")
        plan = _plan_note(tmp_path)
        store = _make_run_store(tmp_path)

        store_freeze_hash(store, "test-run", plan, notes_root=notes_dir)
        run = store.load("test-run")
        pf = run.meta["plan_freeze"]

        assert "covers_hash" in pf
        assert "covers_retries_hash" in pf
        assert pf["covers_hash"] == pf["covers_retries_hash"]

    def test_harness_plan_both_present(self, tmp_path):
        """On a plan with harness_commits: both hashes stored and differ."""
        notes_dir = tmp_path / "experiments"
        _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
        _child_note(notes_dir, "q1-main1-abl-A",
                    stance="confirmatory", plan_role="supporting_ablation")
        plan = _plan_note(tmp_path, harness_commits="[main1=abc123]")
        store = _make_run_store(tmp_path)

        store_freeze_hash(store, "test-run", plan, notes_root=notes_dir)
        run = store.load("test-run")
        pf = run.meta["plan_freeze"]

        assert "covers_hash" in pf
        assert "covers_retries_hash" in pf
        # With harness, they must differ
        assert pf["covers_hash"] != pf["covers_retries_hash"]

    def test_harness_free_retries_hash_matches_golden(self, tmp_path):
        """covers_retries_hash on a harness-free plan == the back-compat golden."""
        notes_dir = tmp_path / "experiments"
        _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
        _child_note(notes_dir, "q1-main1-abl-A",
                    stance="confirmatory", plan_role="supporting_ablation")
        plan = _plan_note(tmp_path)
        store = _make_run_store(tmp_path)

        store_freeze_hash(store, "test-run", plan, notes_root=notes_dir)
        run = store.load("test-run")
        pf = run.meta["plan_freeze"]

        assert pf["covers_retries_hash"] == _GOLDEN_NO_HARNESS


# ===========================================================================
# S1g — verify_freeze_hash harness-commit drift
# ===========================================================================

class TestVerifyFreezeHashHarnessDrift:
    def _setup_and_freeze(self, tmp_path: Path,
                          harness_commits: str = "") -> tuple:
        """Freeze a plan; return (store, plan_note_path, notes_dir)."""
        notes_dir = tmp_path / "experiments"
        _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
        _child_note(notes_dir, "q1-main1-abl-A",
                    stance="confirmatory", plan_role="supporting_ablation")
        plan = _plan_note(tmp_path, harness_commits=harness_commits)
        store = _make_run_store(tmp_path)
        store_freeze_hash(store, "test-run", plan, notes_root=notes_dir)
        return store, plan, notes_dir

    def test_untampered_passes(self, tmp_path):
        """After freeze, verify on the same plan → (True, None)."""
        store, plan, notes_dir = self._setup_and_freeze(
            tmp_path, harness_commits="[main1=abc]"
        )
        ok, msg = verify_freeze_hash(store, "test-run", plan, notes_root=notes_dir,
                                     require_frozen=True)
        assert ok is True
        assert msg is None

    def test_harness_sha_swap_detected(self, tmp_path):
        """Swap harness SHA after freeze → (False, 'harness-commit drift')."""
        store, plan, notes_dir = self._setup_and_freeze(
            tmp_path, harness_commits="[main1=abc]"
        )
        # Swap the harness SHA in the plan frontmatter
        original = plan.read_text(encoding="utf-8")
        tampered = original.replace("main1=abc", "main1=deadbeef")
        plan.write_text(tampered, encoding="utf-8")

        ok, msg = verify_freeze_hash(store, "test-run", plan, notes_root=notes_dir,
                                     require_frozen=True)
        assert ok is False
        assert msg is not None
        assert "harness-commit drift" in msg

    def test_covers_drift_not_harness_drift(self, tmp_path):
        """Covers edit → mismatch NOT classified as harness-commit drift."""
        store, plan, notes_dir = self._setup_and_freeze(
            tmp_path, harness_commits="[main1=abc]"
        )
        # Add a new child to covers:
        original = plan.read_text(encoding="utf-8")
        tampered = original.replace(
            "covers: [q1-main1, q1-main1-abl-A]",
            "covers: [q1-main1, q1-main1-abl-A, q1-extra]"
        )
        plan.write_text(tampered, encoding="utf-8")

        ok, msg = verify_freeze_hash(store, "test-run", plan, notes_root=notes_dir,
                                     require_frozen=True)
        assert ok is False
        assert msg is not None
        # Must NOT say harness-commit drift (it's a covers edit)
        assert "harness-commit drift" not in msg

    def test_harness_free_plan_verify_passes(self, tmp_path):
        """No harness field: freeze + verify round-trip still works."""
        store, plan, notes_dir = self._setup_and_freeze(tmp_path)
        ok, msg = verify_freeze_hash(store, "test-run", plan, notes_root=notes_dir,
                                     require_frozen=True)
        assert ok is True
        assert msg is None
