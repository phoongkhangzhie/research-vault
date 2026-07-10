"""test_sr_plan1.py — SR-PLAN-1 acceptance tests.

Coverage:
  1. plan/check.py — K-2 shape-lint (branch-presence + one-component-per-ablation)
     1a. branch-presence: empty cell / TBD / fallback → FAIL; full rows → PASS
     1b. one-component: "X and Y" / "X, Y" → FAIL; single → PASS
     1c. wrong plan_kind → PlanCheckError
     1d. file not found → PlanCheckError
  2. plan/style.py — get_plan_tips
     2a. all PLAN_TIPS_KEYS present in default
     2b. adopter override merges (known key replaced, unknown key dropped)
     2c. None config → default returned
  3. plan/freeze.py — K-3 covers: freeze-set hash
     3a. deterministic hash for same input
     3b. hash changes when covers: changes
     3c. missing child note → graceful (uses sentinel values, hash still computed)
     3d. store_freeze_hash writes to RunState.meta
     3e. verify_freeze_hash returns True when unchanged, False on mismatch
  4. dag/store.py — RunState.meta round-trips through to_dict/from_dict
  5. dag/verbs.py — K-3 hook in cmd_approve
     5a. approving non-human-go-plan node when freeze hash present + MATCH → passes
     5b. approving human-go-findings when freeze hash MISMATCH → returns 1 (blocked)
     5c. approving human-go-plan node itself → no K-3 check (sets the freeze)
  6. plan/verbs.py CLI
     6a. rv plan check → exit 0 on clean plan note
     6b. rv plan check → exit 1 on violations
     6c. rv plan tips → prints all keys
     6d. rv plan tips --key <known> → prints one key
     6e. rv plan tips --key <unknown> → exit 1
     6f. rv plan freeze + rv plan verify-freeze → round-trip
  7. CLI verb registry
     7a. "plan" in _VERB_REGISTRY with sr: "SR-PLAN-1"
     7b. rv help --check passes with plan verb present
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.plan.check import check_plan, PlanCheckError, _parse_frontmatter
from research_vault.plan.style import get_plan_tips, PLAN_TIPS_KEYS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _plan_note(
    tmp_path: Path,
    *,
    plan_kind: str = "preregistration",
    covers: str = "[q1-main1, q1-main1-abl-A]",
    body: str = "",
    filename: str = "q1-plan.md",
) -> Path:
    """Write a minimal plan master note and return its path."""
    p = tmp_path / filename
    fm = f"plan_kind: {plan_kind}\ncite key: q1-plan\ncovers: {covers}"
    p.write_text(f"---\n{fm}\n---\n\n{body}", encoding="utf-8")
    return p


def _child_note(
    notes_dir: Path,
    child_id: str,
    *,
    stance: str = "confirmatory",
    plan_role: str = "main",
) -> Path:
    """Write a child experiment note with stance + plan_role fields."""
    p = notes_dir / f"{child_id}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\ntype: experiments\ncitekey: {child_id}\n"
        f"stance: {stance}\nplan_role: {plan_role}\n---\n\n# {child_id}\n",
        encoding="utf-8",
    )
    return p


# ===========================================================================
# 1. plan/check.py — K-2 shape-lint
# ===========================================================================

class TestBranchPresence:
    """Rule (a): every diagnosis table row has a named conclusion and action."""

    def test_clean_table_passes(self, tmp_path):
        body = textwrap.dedent("""\
            ## Diagnosis

            | Outcome | Conclusion | Action |
            |---|---|---|
            | score > 0.8 | component load-bearing | proceed to write-up |
            | score unchanged | mechanism not engaged | reject claim |
            | score < 0.5 | main mis-specified | re-design experiment |
        """)
        p = _plan_note(tmp_path, body=body)
        violations = check_plan(p)
        assert violations == []

    def test_empty_cell_fails(self, tmp_path):
        body = textwrap.dedent("""\
            ## Diagnosis

            | Outcome | Conclusion | Action |
            |---|---|---|
            | score > 0.8 |  | proceed |
        """)
        p = _plan_note(tmp_path, body=body)
        violations = check_plan(p)
        assert any("empty" in v.lower() for v in violations)

    def test_tbd_cell_fails(self, tmp_path):
        body = textwrap.dedent("""\
            ## Diagnosis

            | Outcome | Conclusion | Action |
            |---|---|---|
            | score > 0.8 | load-bearing | TBD |
        """)
        p = _plan_note(tmp_path, body=body)
        violations = check_plan(p)
        assert any("TBD" in v for v in violations)

    def test_fallback_cell_fails(self, tmp_path):
        body = textwrap.dedent("""\
            ## Diagnosis

            | Outcome | Conclusion | Action |
            |---|---|---|
            | other | fallback | investigate further |
        """)
        p = _plan_note(tmp_path, body=body)
        violations = check_plan(p)
        assert any("fallback" in v.lower() for v in violations)

    def test_no_tables_passes(self, tmp_path):
        p = _plan_note(tmp_path, body="# Plan\n\nNo tables here.\n")
        violations = check_plan(p)
        assert violations == []


class TestOneComponent:
    """Rule (b): supporting ablations must not manipulate more than one component."""

    def test_single_component_passes(self, tmp_path):
        body = "## Ablation A\n\nComponent manipulated: prompt template\n"
        p = _plan_note(tmp_path, body=body)
        violations = check_plan(p)
        assert not any("Component manipulated" in v for v in violations)

    def test_and_conjunction_fails(self, tmp_path):
        body = "## Ablation A\n\nComponent manipulated: prompt template and sampling temperature\n"
        p = _plan_note(tmp_path, body=body)
        violations = check_plan(p)
        assert any("multiple components" in v.lower() for v in violations)

    def test_comma_list_fails(self, tmp_path):
        body = "## Ablation A\n\nComponent manipulated: prompt template, sampling temperature\n"
        p = _plan_note(tmp_path, body=body)
        violations = check_plan(p)
        assert any("multiple components" in v.lower() for v in violations)

    def test_plural_header_passes(self, tmp_path):
        """'Components manipulated:' header with a single item is fine."""
        body = "## Ablation B\n\nComponents manipulated: prompt template\n"
        p = _plan_note(tmp_path, body=body)
        violations = check_plan(p)
        assert not any("multiple components" in v.lower() for v in violations)


class TestPlanCheckErrors:
    """PlanCheckError raised for bad inputs."""

    def test_wrong_plan_kind_raises(self, tmp_path):
        p = _plan_note(tmp_path, plan_kind="experiment")
        with pytest.raises(PlanCheckError, match="not a preregistration"):
            check_plan(p)

    def test_file_not_found_raises(self, tmp_path):
        with pytest.raises(PlanCheckError, match="Cannot read"):
            check_plan(tmp_path / "nonexistent.md")

    def test_missing_plan_kind_raises(self, tmp_path):
        p = tmp_path / "bare.md"
        p.write_text("---\ncitekey: bare\n---\n\n# bare\n", encoding="utf-8")
        with pytest.raises(PlanCheckError, match="not a preregistration"):
            check_plan(p)


class TestParseFrontmatter:
    """Internal helper tests — frontmatter parsing contract."""

    def test_basic(self):
        text = "---\nfoo: bar\nbaz: 123\n---\n\nbody"
        fields, body = _parse_frontmatter(text)
        assert fields["foo"] == "bar"
        assert fields["baz"] == "123"
        assert body == "body"

    def test_no_frontmatter(self):
        text = "# heading\nbody"
        fields, body = _parse_frontmatter(text)
        assert fields == {}
        assert body == text

    def test_quoted_value(self):
        text = "---\ntitle: 'hello world'\n---\n"
        fields, _ = _parse_frontmatter(text)
        assert fields["title"] == "hello world"


# ===========================================================================
# 2. plan/style.py — get_plan_tips
# ===========================================================================

class TestGetPlanTips:
    def test_all_keys_present_no_config(self):
        tips = get_plan_tips(None)
        assert set(tips.keys()) == PLAN_TIPS_KEYS

    def test_all_values_non_empty(self):
        tips = get_plan_tips(None)
        for k, v in tips.items():
            assert isinstance(v, str), f"key {k!r} should be str"
            assert v.strip(), f"key {k!r} should be non-empty"

    def test_adopter_override_replaces_key(self):
        class FakeCfg:
            _raw = {"plan_style": {"main": "Custom main tip."}}
        tips = get_plan_tips(FakeCfg())
        assert tips["main"] == "Custom main tip."
        # Other keys are unchanged defaults
        default = get_plan_tips(None)
        for k in PLAN_TIPS_KEYS:
            if k != "main":
                assert tips[k] == default[k]

    def test_adopter_override_unknown_key_dropped(self):
        class FakeCfg:
            _raw = {"plan_style": {"unknown_key": "should be dropped"}}
        tips = get_plan_tips(FakeCfg())
        assert "unknown_key" not in tips
        assert set(tips.keys()) == PLAN_TIPS_KEYS

    def test_adopter_non_string_value_ignored(self):
        class FakeCfg:
            _raw = {"plan_style": {"main": 42}}  # not a str
        tips = get_plan_tips(FakeCfg())
        default = get_plan_tips(None)
        assert tips["main"] == default["main"]

    def test_none_config_returns_defaults(self):
        tips = get_plan_tips(None)
        assert "exploratory" in tips
        assert "freeze" in tips
        assert "diagnosis_table" in tips


# ===========================================================================
# 3. plan/freeze.py — K-3 covers: freeze-set hash
# ===========================================================================

class TestFreeze:
    """Tests for plan/freeze.py — must be written BEFORE the module exists."""

    def _import_freeze(self):
        from research_vault.plan import freeze as freeze_mod
        return freeze_mod

    def test_deterministic_hash(self, tmp_path):
        freeze = self._import_freeze()
        notes_dir = tmp_path / "notes" / "experiments"
        # Write plan master
        covers = "[q1-main1, q1-main1-abl-A]"
        p = _plan_note(tmp_path, covers=covers)
        # Write child notes
        _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
        _child_note(notes_dir, "q1-main1-abl-A", stance="confirmatory", plan_role="supporting_ablation")

        h1 = freeze.compute_covers_hash(p, notes_root=notes_dir)
        h2 = freeze.compute_covers_hash(p, notes_root=notes_dir)
        assert h1 == h2

    def test_hash_changes_on_covers_change(self, tmp_path):
        freeze = self._import_freeze()
        notes_dir = tmp_path / "notes" / "experiments"
        _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
        _child_note(notes_dir, "q1-main1-abl-A", stance="confirmatory", plan_role="supporting_ablation")
        _child_note(notes_dir, "q1-main2", stance="confirmatory", plan_role="main")

        p1 = _plan_note(tmp_path, covers="[q1-main1, q1-main1-abl-A]", filename="plan-a.md")
        p2 = _plan_note(tmp_path, covers="[q1-main1, q1-main1-abl-A, q1-main2]", filename="plan-b.md")

        h1 = freeze.compute_covers_hash(p1, notes_root=notes_dir)
        h2 = freeze.compute_covers_hash(p2, notes_root=notes_dir)
        assert h1 != h2

    def test_missing_child_note_graceful(self, tmp_path):
        """A missing child note should not crash — use sentinel values."""
        freeze = self._import_freeze()
        notes_dir = tmp_path / "notes" / "experiments"
        # Don't write child notes
        p = _plan_note(tmp_path, covers="[q1-main1, q1-main1-abl-A]")

        # Should not raise, should return a hex string
        h = freeze.compute_covers_hash(p, notes_root=notes_dir)
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex

    def test_hash_is_hex_string(self, tmp_path):
        freeze = self._import_freeze()
        notes_dir = tmp_path / "notes" / "experiments"
        p = _plan_note(tmp_path, covers="[q1-main1]")
        h = freeze.compute_covers_hash(p, notes_root=notes_dir)
        assert isinstance(h, str)
        # Valid hex
        int(h, 16)

    def test_store_and_verify_roundtrip(self, tmp_path):
        from research_vault.dag.store import RunState, RunStore

        freeze = self._import_freeze()
        notes_dir = tmp_path / "notes" / "experiments"
        _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
        p = _plan_note(tmp_path, covers="[q1-main1]")

        state_dir = tmp_path / "state"
        store = RunStore(state_dir)
        run_state = RunState(
            run_id="test-run",
            manifest_path=str(tmp_path / "manifest.json"),
        )
        store.create(run_state)

        # Store the freeze hash
        freeze.store_freeze_hash(store, "test-run", p, notes_root=notes_dir)

        # Reload and verify
        ok, msg = freeze.verify_freeze_hash(store, "test-run", p, notes_root=notes_dir)
        assert ok is True
        assert msg is None

    def test_verify_fails_on_mismatch(self, tmp_path):
        from research_vault.dag.store import RunState, RunStore

        freeze = self._import_freeze()
        notes_dir = tmp_path / "notes" / "experiments"
        _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
        p = _plan_note(tmp_path, covers="[q1-main1]")

        state_dir = tmp_path / "state"
        store = RunStore(state_dir)
        run_state = RunState(run_id="test-run2", manifest_path=str(tmp_path / "m.json"))
        store.create(run_state)

        freeze.store_freeze_hash(store, "test-run2", p, notes_root=notes_dir)

        # Now tamper: add a new child to covers
        p2 = _plan_note(tmp_path, covers="[q1-main1, q1-main2]", filename="plan2.md")
        _child_note(notes_dir, "q1-main2", stance="confirmatory", plan_role="main")
        # Verify against original plan_note path in meta but use tampered content
        # (Actually: verify uses the path stored in meta for the original hash, but
        #  recalculates on the plan note's current content.)
        # Mutate the stored run to tamper: store freeze with p, verify with p2
        ok, msg = freeze.verify_freeze_hash(store, "test-run2", p2, notes_root=notes_dir)
        assert ok is False
        assert msg is not None

    def test_verify_no_freeze_in_meta(self, tmp_path):
        """SR-FREEZE-FIX: no freeze stored → fail CLOSED by default (require_frozen=True).

        The old behavior was (True, None) — a fail-open bug (hole a).  The new
        correct behavior: (False, msg) with a 'not frozen' explanation.

        Callers that gate on presence themselves (e.g. rv dag approve) opt in to
        require_frozen=False to get the old no-op (True, None).
        """
        from research_vault.dag.store import RunState, RunStore

        freeze = self._import_freeze()
        notes_dir = tmp_path / "notes" / "experiments"
        p = _plan_note(tmp_path)

        state_dir = tmp_path / "state"
        store = RunStore(state_dir)
        run_state = RunState(run_id="no-freeze-run", manifest_path=str(tmp_path / "m.json"))
        store.create(run_state)

        # Default: require_frozen=True — must FAIL CLOSED on absent freeze.
        ok, msg = freeze.verify_freeze_hash(store, "no-freeze-run", p, notes_root=notes_dir)
        assert ok is False, "Absent freeze must return (False, msg) — not (True, None)"
        assert msg is not None
        assert "not frozen" in msg.lower() or "freeze" in msg.lower()

        # Explicit require_frozen=False: the no-op escape-hatch (for callers that
        # already gate on presence).
        ok2, msg2 = freeze.verify_freeze_hash(
            store, "no-freeze-run", p, notes_root=notes_dir, require_frozen=False
        )
        assert ok2 is True
        assert msg2 is None


# ===========================================================================
# 3b. plan/freeze.py — SR-PLAN-FREEZE-RETRY (#23)
#     max_retries folded into the covers:-freeze-set hash
# ===========================================================================

def _manifest_json(tmp_path: Path, nodes: list[dict], *, filename: str = "manifest.json") -> Path:
    """Write a minimal manifest JSON with the given node dicts and return its path."""
    import json as _json
    p = tmp_path / filename
    manifest = {"run_id": "test-run", "nodes": nodes}
    p.write_text(_json.dumps(manifest), encoding="utf-8")
    return p


def _node(node_id: str, max_retries: int | None = None) -> dict:
    """Build a minimal manifest node dict, optionally with max_retries."""
    n: dict = {"id": node_id, "type": "agent", "cmd": ["echo", "ok"]}
    if max_retries is not None:
        n["max_retries"] = max_retries
    return n


class TestPlanFreezeRetry:
    """SR-PLAN-FREEZE-RETRY (#23) — max_retries folded into the covers:-freeze hash.

    All tests use the NEW signature:
      compute_covers_hash(plan_note_path, notes_root=None, manifest_nodes=None)

    Back-compat rule: manifest_nodes=None → byte-identical to pre-extension code.
    """

    def _import_freeze(self):
        from research_vault.plan import freeze as freeze_mod
        return freeze_mod

    # ------------------------------------------------------------------
    # R1  back-compat: all-default manifest → hash == covers-only hash
    # ------------------------------------------------------------------

    def test_all_default_manifest_hash_equals_covers_only(self, tmp_path):
        """BYTE-IDENTICAL: all-default nodes (no max_retries) == manifest_nodes=None."""
        freeze = self._import_freeze()
        notes_dir = tmp_path / "notes"
        _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
        p = _plan_note(tmp_path, covers="[q1-main1]")

        # All nodes have no max_retries field (effective 0)
        nodes = [_node("q1-main1")]

        hash_no_manifest = freeze.compute_covers_hash(p, notes_root=notes_dir,
                                                      manifest_nodes=None)
        hash_all_default = freeze.compute_covers_hash(p, notes_root=notes_dir,
                                                      manifest_nodes=nodes)
        assert hash_no_manifest == hash_all_default, (
            "All-default manifest must yield byte-identical hash to covers-only "
            "(manifest_nodes=None) — back-compat requires no re-freeze."
        )

    def test_explicit_zero_retries_treated_as_default(self, tmp_path):
        """max_retries=0 is the default — must NOT appear in retries block, hash unchanged."""
        freeze = self._import_freeze()
        notes_dir = tmp_path / "notes"
        _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
        p = _plan_note(tmp_path, covers="[q1-main1]")

        nodes_no_field = [_node("q1-main1")]
        nodes_explicit_zero = [_node("q1-main1", max_retries=0)]

        h_none = freeze.compute_covers_hash(p, notes_root=notes_dir, manifest_nodes=None)
        h_no_field = freeze.compute_covers_hash(p, notes_root=notes_dir,
                                                manifest_nodes=nodes_no_field)
        h_zero = freeze.compute_covers_hash(p, notes_root=notes_dir,
                                            manifest_nodes=nodes_explicit_zero)
        assert h_none == h_no_field == h_zero, (
            "Explicit max_retries=0 must be treated as the default — omit from hash."
        )

    # ------------------------------------------------------------------
    # R2  a non-zero ceiling IS included — adding one post-freeze BLOCKs
    # ------------------------------------------------------------------

    def test_nonzero_retries_changes_hash(self, tmp_path):
        """A node with max_retries=2 must produce a DIFFERENT hash than default."""
        freeze = self._import_freeze()
        notes_dir = tmp_path / "notes"
        _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
        p = _plan_note(tmp_path, covers="[q1-main1]")

        h_default = freeze.compute_covers_hash(p, notes_root=notes_dir, manifest_nodes=None)
        h_retries = freeze.compute_covers_hash(p, notes_root=notes_dir,
                                               manifest_nodes=[_node("q1-main1", max_retries=2)])
        assert h_default != h_retries, (
            "A node with max_retries=2 must change the hash vs default (tamper-evident)."
        )

    # ------------------------------------------------------------------
    # R3–R6  four tamper directions (all must BLOCK via verify_freeze_hash)
    # ------------------------------------------------------------------

    def _setup_store(self, tmp_path, *, run_id: str, manifest_path: str):
        from research_vault.dag.store import RunState, RunStore
        state_dir = tmp_path / "state"
        store = RunStore(state_dir)
        run_state = RunState(run_id=run_id, manifest_path=manifest_path)
        store.create(run_state)
        return store

    def test_raise_ceiling_post_freeze_blocks(self, tmp_path):
        """Tamper R3: raise max_retries 2→8 post-freeze → verify BLOCKs."""
        freeze = self._import_freeze()
        notes_dir = tmp_path / "notes"
        _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
        p = _plan_note(tmp_path, covers="[q1-main1]")

        # Write initial manifest with max_retries=2
        m_path = _manifest_json(tmp_path, [_node("q1-main1", max_retries=2)])
        store = self._setup_store(tmp_path, run_id="run-raise", manifest_path=str(m_path))
        freeze.store_freeze_hash(store, "run-raise", p, notes_root=notes_dir)

        # Tamper: raise ceiling to 8
        import json as _json
        manifest = _json.loads(m_path.read_text())
        manifest["nodes"][0]["max_retries"] = 8
        m_path.write_text(_json.dumps(manifest))

        ok, msg = freeze.verify_freeze_hash(store, "run-raise", p, notes_root=notes_dir)
        assert ok is False, "Raising max_retries post-freeze must BLOCK."
        assert msg is not None

    def test_add_retry_post_freeze_blocks(self, tmp_path):
        """Tamper R4: add max_retries (absent→3) post-freeze → verify BLOCKs."""
        freeze = self._import_freeze()
        notes_dir = tmp_path / "notes"
        _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
        p = _plan_note(tmp_path, covers="[q1-main1]")

        # Initial manifest: no max_retries field
        m_path = _manifest_json(tmp_path, [_node("q1-main1")])
        store = self._setup_store(tmp_path, run_id="run-add", manifest_path=str(m_path))
        freeze.store_freeze_hash(store, "run-add", p, notes_root=notes_dir)

        # Tamper: add max_retries=3
        import json as _json
        manifest = _json.loads(m_path.read_text())
        manifest["nodes"][0]["max_retries"] = 3
        m_path.write_text(_json.dumps(manifest))

        ok, msg = freeze.verify_freeze_hash(store, "run-add", p, notes_root=notes_dir)
        assert ok is False, "Adding max_retries (absent→3) post-freeze must BLOCK."
        assert msg is not None

    def test_remove_retry_post_freeze_blocks(self, tmp_path):
        """Tamper R5: remove max_retries (2→absent) post-freeze → verify BLOCKs."""
        freeze = self._import_freeze()
        notes_dir = tmp_path / "notes"
        _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
        p = _plan_note(tmp_path, covers="[q1-main1]")

        # Initial manifest: max_retries=2
        m_path = _manifest_json(tmp_path, [_node("q1-main1", max_retries=2)])
        store = self._setup_store(tmp_path, run_id="run-remove", manifest_path=str(m_path))
        freeze.store_freeze_hash(store, "run-remove", p, notes_root=notes_dir)

        # Tamper: remove max_retries field
        import json as _json
        manifest = _json.loads(m_path.read_text())
        del manifest["nodes"][0]["max_retries"]
        m_path.write_text(_json.dumps(manifest))

        ok, msg = freeze.verify_freeze_hash(store, "run-remove", p, notes_root=notes_dir)
        assert ok is False, "Removing max_retries (2→absent) post-freeze must BLOCK."
        assert msg is not None

    def test_lower_retry_post_freeze_blocks(self, tmp_path):
        """Tamper R6: lower max_retries 5→2 post-freeze → verify BLOCKs."""
        freeze = self._import_freeze()
        notes_dir = tmp_path / "notes"
        _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
        p = _plan_note(tmp_path, covers="[q1-main1]")

        # Initial manifest: max_retries=5
        m_path = _manifest_json(tmp_path, [_node("q1-main1", max_retries=5)])
        store = self._setup_store(tmp_path, run_id="run-lower", manifest_path=str(m_path))
        freeze.store_freeze_hash(store, "run-lower", p, notes_root=notes_dir)

        # Tamper: lower to 2
        import json as _json
        manifest = _json.loads(m_path.read_text())
        manifest["nodes"][0]["max_retries"] = 2
        m_path.write_text(_json.dumps(manifest))

        ok, msg = freeze.verify_freeze_hash(store, "run-lower", p, notes_root=notes_dir)
        assert ok is False, "Lowering max_retries (5→2) post-freeze must BLOCK."
        assert msg is not None

    # ------------------------------------------------------------------
    # R7  retry-drift mismatch message distinguishes retry vs covers edit
    # ------------------------------------------------------------------

    def test_retry_drift_message_distinguishes_from_covers_edit(self, tmp_path):
        """Mismatch message names 'ceiling' / 'max_retries' for a retry-only drift."""
        freeze = self._import_freeze()
        notes_dir = tmp_path / "notes"
        _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
        p = _plan_note(tmp_path, covers="[q1-main1]")

        # Freeze with max_retries=2 so the covers block is unchanged post-tamper
        m_path = _manifest_json(tmp_path, [_node("q1-main1", max_retries=2)])
        store = self._setup_store(tmp_path, run_id="run-msg", manifest_path=str(m_path))
        freeze.store_freeze_hash(store, "run-msg", p, notes_root=notes_dir)

        # Tamper ONLY the retries (covers: unchanged)
        import json as _json
        manifest = _json.loads(m_path.read_text())
        manifest["nodes"][0]["max_retries"] = 8
        m_path.write_text(_json.dumps(manifest))

        ok, msg = freeze.verify_freeze_hash(store, "run-msg", p, notes_root=notes_dir)
        assert ok is False
        assert msg is not None
        # Message must name a ceiling/retry change, not just "covers: set was edited"
        msg_lower = msg.lower()
        assert "ceiling" in msg_lower or "max_retries" in msg_lower or "retry" in msg_lower, (
            f"Retry-drift mismatch message must name the ceiling change, got: {msg!r}"
        )

    # ------------------------------------------------------------------
    # R8  graceful fallback: unreadable manifest_path → covers-only hash
    # ------------------------------------------------------------------

    def test_unreadable_manifest_path_graceful_fallback(self, tmp_path):
        """Unreadable manifest_path → graceful covers-only hash (no crash)."""
        freeze = self._import_freeze()
        notes_dir = tmp_path / "notes"
        _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
        p = _plan_note(tmp_path, covers="[q1-main1]")

        # store_freeze_hash with a nonexistent manifest path
        from research_vault.dag.store import RunState, RunStore
        state_dir = tmp_path / "state"
        store = RunStore(state_dir)
        run_state = RunState(run_id="run-nograce", manifest_path=str(tmp_path / "ghost.json"))
        store.create(run_state)

        # Must not raise; should produce same hash as manifest_nodes=None
        freeze.store_freeze_hash(store, "run-nograce", p, notes_root=notes_dir)
        ok, msg = freeze.verify_freeze_hash(store, "run-nograce", p, notes_root=notes_dir)
        assert ok is True, f"Unreadable manifest must not cause mismatch: {msg}"

    # ------------------------------------------------------------------
    # R9  back-compat: existing 281–393 tests must remain green UNCHANGED
    #     (verified by running the full TestPlanFreezeHash class)
    # ------------------------------------------------------------------

    def test_round_trip_with_real_manifest(self, tmp_path):
        """Full round-trip: freeze with real manifest, verify identical manifest → pass."""
        freeze = self._import_freeze()
        notes_dir = tmp_path / "notes"
        _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
        _child_note(notes_dir, "q1-main1-abl-A", stance="confirmatory", plan_role="supporting_ablation")
        p = _plan_note(tmp_path, covers="[q1-main1, q1-main1-abl-A]")

        m_path = _manifest_json(tmp_path, [
            _node("q1-main1", max_retries=3),
            _node("q1-main1-abl-A"),
        ])
        from research_vault.dag.store import RunState, RunStore
        state_dir = tmp_path / "state"
        store = RunStore(state_dir)
        run_state = RunState(run_id="run-rt", manifest_path=str(m_path))
        store.create(run_state)

        freeze.store_freeze_hash(store, "run-rt", p, notes_root=notes_dir)
        ok, msg = freeze.verify_freeze_hash(store, "run-rt", p, notes_root=notes_dir)
        assert ok is True, f"Unchanged manifest must verify OK: {msg}"

    def test_sorted_node_order_is_deterministic(self, tmp_path):
        """Retries block is sorted by node_id — order of nodes list doesn't matter."""
        freeze = self._import_freeze()
        notes_dir = tmp_path / "notes"
        _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
        p = _plan_note(tmp_path, covers="[q1-main1]")

        nodes_ab = [_node("aaa", max_retries=2), _node("zzz", max_retries=5)]
        nodes_ba = [_node("zzz", max_retries=5), _node("aaa", max_retries=2)]

        h_ab = freeze.compute_covers_hash(p, notes_root=notes_dir, manifest_nodes=nodes_ab)
        h_ba = freeze.compute_covers_hash(p, notes_root=notes_dir, manifest_nodes=nodes_ba)
        assert h_ab == h_ba, "Retries block hash must be order-independent (sorted)."


# ===========================================================================
# 4. dag/store.py — RunState.meta round-trips
# ===========================================================================

class TestRunStateMeta:
    def test_meta_field_defaults_empty(self):
        from research_vault.dag.store import RunState
        rs = RunState(run_id="x", manifest_path="/p")
        assert rs.meta == {}

    def test_meta_round_trips_to_dict(self):
        from research_vault.dag.store import RunState
        rs = RunState(run_id="x", manifest_path="/p")
        rs.meta["plan_freeze"] = {"covers_hash": "abc123", "plan_note": "/p/plan.md"}
        d = rs.to_dict()
        assert d["meta"]["plan_freeze"]["covers_hash"] == "abc123"

    def test_meta_round_trips_from_dict(self):
        from research_vault.dag.store import RunState
        d = {
            "run_id": "x",
            "manifest_path": "/p",
            "created_at": 0.0,
            "node_states": {},
            "edge_registered_ts": {},
            "meta": {"plan_freeze": {"covers_hash": "def456"}},
        }
        rs = RunState.from_dict(d)
        assert rs.meta["plan_freeze"]["covers_hash"] == "def456"

    def test_meta_from_dict_missing_key_defaults_empty(self):
        """Older run states without 'meta' key deserialize correctly."""
        from research_vault.dag.store import RunState
        d = {
            "run_id": "x",
            "manifest_path": "/p",
            "created_at": 0.0,
            "node_states": {},
            "edge_registered_ts": {},
        }
        rs = RunState.from_dict(d)
        assert rs.meta == {}

    def test_meta_persists_through_store_save_load(self, tmp_path):
        from research_vault.dag.store import RunState, RunStore
        store = RunStore(tmp_path)
        rs = RunState(run_id="meta-test", manifest_path="/p")
        rs.meta["custom"] = {"key": "value"}
        store.create(rs)

        loaded = store.load("meta-test")
        assert loaded.meta["custom"]["key"] == "value"


# ===========================================================================
# 5. dag/verbs.py — K-3 hook in cmd_approve
# ===========================================================================

class TestK3HookInApprove:
    """K-3 freeze verify integrates into cmd_approve."""

    def _make_manifest(self, tmp_path: Path, nodes: list[dict]) -> Path:
        """Write a minimal DAG manifest JSON and return its path."""
        manifest = {
            "run_id": "k3-test",
            "name": "K-3 test loop",
            "global_cap": 4,
            "nodes": nodes,
        }
        p = tmp_path / "manifest.json"
        p.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return p

    def _init_run(self, tmp_path: Path, manifest_path: Path):
        """Initialize a run state, with plan and plan-critic pre-approved."""
        from research_vault.dag.store import RunState, RunStore

        store = RunStore(tmp_path / "state")
        manifest = json.loads(manifest_path.read_text())
        rs = RunState(run_id="k3-test", manifest_path=str(manifest_path))
        rs.init_nodes(manifest)
        # Pre-approve plan-related nodes to get to human-go-findings
        for nid in ("plan", "plan-critic", "human-go-plan"):
            rs.set_node_status(nid, "succeeded")
        rs.set_node_status("human-go-findings", "awaiting-go")
        store.create(rs)
        return store

    def test_approve_human_go_findings_no_freeze_passes(self, tmp_path):
        """No freeze hash in meta → approve proceeds normally."""
        from research_vault.dag.verbs import cmd_approve
        import os

        cfg_file = tmp_path / "research_vault.toml"
        cfg_file.write_text(
            f'instance_root = "{tmp_path}"\n'
            f'notes_root = "{tmp_path / "notes"}"\n'
            f'state_dir = "{tmp_path / "state"}"\n'
            f'agents_dir = "{tmp_path / ".agents"}"\n'
            f'tasks_dir = "{tmp_path / "tasks"}"\n'
            f'control_dir = "{tmp_path / "control"}"\n'
            '[adapters]\nnotifier = "file"\nbackend = "local"\nsecrets = "env"\n'
            # SR-APPROVE-GATE: token fingerprint for test-time token approval.
            '[approval]\nenforce = true\n'
            'token_fingerprint = "d309a810bb5f40cef518202e46d197aa61e4dddafc5984c8c698da29ac8fd2bc"\n'
            'enforce_sig = ""\n',
            encoding="utf-8",
        )
        old = os.environ.get("RESEARCH_VAULT_CONFIG")
        os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_file)

        try:
            nodes = [
                {"id": "plan", "type": "agent", "spec": "task://demo#plan", "needs": []},
                {"id": "plan-critic", "type": "agent", "spec": "task://demo#critic",
                 "needs": [{"from": "plan", "edge": "afterok"}]},
                {"id": "human-go-plan", "type": "human-go", "label": "gate",
                 "needs": [{"from": "plan-critic", "edge": "afterok"}]},
                {"id": "human-go-findings", "type": "human-go", "label": "findings gate",
                 "needs": [{"from": "human-go-plan", "edge": "afterok"}]},
            ]
            mp = self._make_manifest(tmp_path, nodes)
            self._init_run(tmp_path, mp)

            args = argparse.Namespace(run_id="k3-test", node_id="human-go-findings")
            result = cmd_approve(args)
            assert result == 0
        finally:
            if old is None:
                os.environ.pop("RESEARCH_VAULT_CONFIG", None)
            else:
                os.environ["RESEARCH_VAULT_CONFIG"] = old

    def test_approve_human_go_findings_with_matching_freeze_passes(self, tmp_path):
        """Freeze hash present and matching → approve proceeds."""
        from research_vault.dag.store import RunStore
        from research_vault.dag.verbs import cmd_approve
        from research_vault.plan import freeze as freeze_mod
        import os

        cfg_file = tmp_path / "research_vault.toml"
        cfg_file.write_text(
            f'instance_root = "{tmp_path}"\n'
            f'notes_root = "{tmp_path / "notes"}"\n'
            f'state_dir = "{tmp_path / "state"}"\n'
            f'agents_dir = "{tmp_path / ".agents"}"\n'
            f'tasks_dir = "{tmp_path / "tasks"}"\n'
            f'control_dir = "{tmp_path / "control"}"\n'
            '[adapters]\nnotifier = "file"\nbackend = "local"\nsecrets = "env"\n'
            # SR-APPROVE-GATE: token fingerprint for test-time token approval.
            '[approval]\nenforce = true\n'
            'token_fingerprint = "d309a810bb5f40cef518202e46d197aa61e4dddafc5984c8c698da29ac8fd2bc"\n'
            'enforce_sig = ""\n',
            encoding="utf-8",
        )
        old = os.environ.get("RESEARCH_VAULT_CONFIG")
        os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_file)

        try:
            notes_dir = tmp_path / "notes" / "experiments"
            _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
            plan_note = _plan_note(tmp_path, covers="[q1-main1]")

            nodes = [
                {"id": "plan", "type": "agent", "spec": "task://demo#plan", "needs": []},
                {"id": "plan-critic", "type": "agent", "spec": "task://demo#critic",
                 "needs": [{"from": "plan", "edge": "afterok"}]},
                {"id": "human-go-plan", "type": "human-go", "label": "gate",
                 "needs": [{"from": "plan-critic", "edge": "afterok"}]},
                {"id": "human-go-findings", "type": "human-go", "label": "findings gate",
                 "needs": [{"from": "human-go-plan", "edge": "afterok"}]},
            ]
            mp = self._make_manifest(tmp_path, nodes)
            store = self._init_run(tmp_path, mp)

            # Store the freeze hash
            freeze_mod.store_freeze_hash(store, "k3-test", plan_note, notes_root=notes_dir)

            args = argparse.Namespace(run_id="k3-test", node_id="human-go-findings")
            result = cmd_approve(args)
            assert result == 0
        finally:
            if old is None:
                os.environ.pop("RESEARCH_VAULT_CONFIG", None)
            else:
                os.environ["RESEARCH_VAULT_CONFIG"] = old

    def test_approve_human_go_findings_with_mismatched_freeze_blocks(self, tmp_path):
        """Freeze hash present but MISMATCH → approve returns 1 (blocked)."""
        from research_vault.dag.store import RunStore
        from research_vault.dag.verbs import cmd_approve
        from research_vault.plan import freeze as freeze_mod
        import os

        cfg_file = tmp_path / "research_vault.toml"
        cfg_file.write_text(
            f'instance_root = "{tmp_path}"\n'
            f'notes_root = "{tmp_path / "notes"}"\n'
            f'state_dir = "{tmp_path / "state"}"\n'
            f'agents_dir = "{tmp_path / ".agents"}"\n'
            f'tasks_dir = "{tmp_path / "tasks"}"\n'
            f'control_dir = "{tmp_path / "control"}"\n'
            '[adapters]\nnotifier = "file"\nbackend = "local"\nsecrets = "env"\n'
            # SR-APPROVE-GATE: token fingerprint for test-time token approval.
            '[approval]\nenforce = true\n'
            'token_fingerprint = "d309a810bb5f40cef518202e46d197aa61e4dddafc5984c8c698da29ac8fd2bc"\n'
            'enforce_sig = ""\n',
            encoding="utf-8",
        )
        old = os.environ.get("RESEARCH_VAULT_CONFIG")
        os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_file)

        try:
            notes_dir = tmp_path / "notes" / "experiments"
            _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
            plan_note = _plan_note(tmp_path, covers="[q1-main1]")

            nodes = [
                {"id": "plan", "type": "agent", "spec": "task://demo#plan", "needs": []},
                {"id": "plan-critic", "type": "agent", "spec": "task://demo#critic",
                 "needs": [{"from": "plan", "edge": "afterok"}]},
                {"id": "human-go-plan", "type": "human-go", "label": "gate",
                 "needs": [{"from": "plan-critic", "edge": "afterok"}]},
                {"id": "human-go-findings", "type": "human-go", "label": "findings gate",
                 "needs": [{"from": "human-go-plan", "edge": "afterok"}]},
            ]
            mp = self._make_manifest(tmp_path, nodes)
            store = self._init_run(tmp_path, mp)

            # Store freeze hash based on original plan
            freeze_mod.store_freeze_hash(store, "k3-test", plan_note, notes_root=notes_dir)

            # Tamper: overwrite the plan note with a different covers: set
            _child_note(notes_dir, "q1-main2", stance="confirmatory", plan_role="main")
            plan_note.write_text(
                "---\nplan_kind: preregistration\ncitekey: q1-plan\ncovers: [q1-main1, q1-main2]\n---\n\n# tampered\n",
                encoding="utf-8",
            )

            args = argparse.Namespace(run_id="k3-test", node_id="human-go-findings")
            result = cmd_approve(args)
            assert result == 1  # BLOCKED
        finally:
            if old is None:
                os.environ.pop("RESEARCH_VAULT_CONFIG", None)
            else:
                os.environ["RESEARCH_VAULT_CONFIG"] = old


# ===========================================================================
# 6. plan/verbs.py CLI
# ===========================================================================

class TestPlanVerbsCLI:
    def test_check_passes_on_clean_note(self, tmp_path, capsys):
        from research_vault.plan.verbs import run as plan_run, build_parser

        body = textwrap.dedent("""\
            ## Exp 1 Diagnosis

            | Outcome | Conclusion | Action |
            |---|---|---|
            | high | load-bearing | proceed |
            | low | not engaged | reject claim |
        """)
        p = _plan_note(tmp_path, body=body)

        parent = argparse.ArgumentParser()
        subs = parent.add_subparsers()
        build_parser(subs)
        args = parent.parse_args(["plan", "check", str(p)])
        result = plan_run(args)
        assert result == 0
        out, _ = capsys.readouterr()
        assert "OK" in out

    def test_check_fails_on_violations(self, tmp_path, capsys):
        from research_vault.plan.verbs import run as plan_run, build_parser

        body = "## D\n\n| Outcome | Conclusion | Action |\n|---|---|---|\n| score > 0.8 | | TBD |\n"
        p = _plan_note(tmp_path, body=body)

        parent = argparse.ArgumentParser()
        subs = parent.add_subparsers()
        build_parser(subs)
        args = parent.parse_args(["plan", "check", str(p)])
        result = plan_run(args)
        assert result == 1

    def test_tips_prints_all_keys(self, capsys):
        from research_vault.plan.verbs import run as plan_run, build_parser

        parent = argparse.ArgumentParser()
        subs = parent.add_subparsers()
        build_parser(subs)
        args = parent.parse_args(["plan", "tips"])
        result = plan_run(args)
        assert result == 0
        out, _ = capsys.readouterr()
        for k in PLAN_TIPS_KEYS:
            assert k in out

    def test_tips_key_filter(self, capsys):
        from research_vault.plan.verbs import run as plan_run, build_parser

        parent = argparse.ArgumentParser()
        subs = parent.add_subparsers()
        build_parser(subs)
        args = parent.parse_args(["plan", "tips", "--key", "main"])
        result = plan_run(args)
        assert result == 0
        out, _ = capsys.readouterr()
        assert "[main]" in out

    def test_tips_unknown_key_fails(self, capsys):
        from research_vault.plan.verbs import run as plan_run, build_parser

        parent = argparse.ArgumentParser()
        subs = parent.add_subparsers()
        build_parser(subs)
        args = parent.parse_args(["plan", "tips", "--key", "nonexistent_key_xyz"])
        result = plan_run(args)
        assert result == 1

    def test_freeze_subcommand_stores_hash(self, tmp_path):
        from research_vault.plan.verbs import run as plan_run, build_parser
        import os

        cfg_file = tmp_path / "research_vault.toml"
        cfg_file.write_text(
            f'instance_root = "{tmp_path}"\n'
            f'notes_root = "{tmp_path / "notes"}"\n'
            f'state_dir = "{tmp_path / "state"}"\n'
            f'agents_dir = "{tmp_path / ".agents"}"\n'
            f'tasks_dir = "{tmp_path / "tasks"}"\n'
            f'control_dir = "{tmp_path / "control"}"\n'
            '[adapters]\nnotifier = "file"\nbackend = "local"\nsecrets = "env"\n'
            # SR-APPROVE-GATE: token fingerprint for test-time token approval.
            '[approval]\nenforce = true\n'
            'token_fingerprint = "d309a810bb5f40cef518202e46d197aa61e4dddafc5984c8c698da29ac8fd2bc"\n'
            'enforce_sig = ""\n',
            encoding="utf-8",
        )
        old = os.environ.get("RESEARCH_VAULT_CONFIG")
        os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_file)

        try:
            from research_vault.dag.store import RunState, RunStore
            notes_dir = tmp_path / "notes" / "experiments"
            _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
            plan_note = _plan_note(tmp_path, covers="[q1-main1]")

            store = RunStore(tmp_path / "state")
            rs = RunState(run_id="freeze-cli-test", manifest_path="/p")
            store.create(rs)

            parent = argparse.ArgumentParser()
            subs = parent.add_subparsers()
            build_parser(subs)
            args = parent.parse_args([
                "plan", "freeze", "freeze-cli-test", str(plan_note),
                "--notes-root", str(notes_dir),
            ])
            result = plan_run(args)
            assert result == 0

            loaded = store.load("freeze-cli-test")
            assert "plan_freeze" in loaded.meta
            assert "covers_hash" in loaded.meta["plan_freeze"]
        finally:
            if old is None:
                os.environ.pop("RESEARCH_VAULT_CONFIG", None)
            else:
                os.environ["RESEARCH_VAULT_CONFIG"] = old

    def test_verify_freeze_subcommand_passes_on_match(self, tmp_path, capsys):
        from research_vault.plan.verbs import run as plan_run, build_parser
        import os

        cfg_file = tmp_path / "research_vault.toml"
        cfg_file.write_text(
            f'instance_root = "{tmp_path}"\n'
            f'notes_root = "{tmp_path / "notes"}"\n'
            f'state_dir = "{tmp_path / "state"}"\n'
            f'agents_dir = "{tmp_path / ".agents"}"\n'
            f'tasks_dir = "{tmp_path / "tasks"}"\n'
            f'control_dir = "{tmp_path / "control"}"\n'
            '[adapters]\nnotifier = "file"\nbackend = "local"\nsecrets = "env"\n'
            # SR-APPROVE-GATE: token fingerprint for test-time token approval.
            '[approval]\nenforce = true\n'
            'token_fingerprint = "d309a810bb5f40cef518202e46d197aa61e4dddafc5984c8c698da29ac8fd2bc"\n'
            'enforce_sig = ""\n',
            encoding="utf-8",
        )
        old = os.environ.get("RESEARCH_VAULT_CONFIG")
        os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_file)

        try:
            from research_vault.dag.store import RunState, RunStore
            from research_vault.plan import freeze as freeze_mod

            notes_dir = tmp_path / "notes" / "experiments"
            _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
            plan_note = _plan_note(tmp_path, covers="[q1-main1]")

            store = RunStore(tmp_path / "state")
            rs = RunState(run_id="vf-cli-test", manifest_path="/p")
            store.create(rs)
            freeze_mod.store_freeze_hash(store, "vf-cli-test", plan_note, notes_root=notes_dir)

            parent = argparse.ArgumentParser()
            subs = parent.add_subparsers()
            build_parser(subs)
            args = parent.parse_args([
                "plan", "verify-freeze", "vf-cli-test", str(plan_note),
                "--notes-root", str(notes_dir),
            ])
            result = plan_run(args)
            assert result == 0
        finally:
            if old is None:
                os.environ.pop("RESEARCH_VAULT_CONFIG", None)
            else:
                os.environ["RESEARCH_VAULT_CONFIG"] = old


# ===========================================================================
# 7. CLI verb registry
# ===========================================================================

class TestVerbRegistry:
    def test_plan_in_verb_registry(self):
        from research_vault.cli import _VERB_REGISTRY
        assert "plan" in _VERB_REGISTRY
        assert _VERB_REGISTRY["plan"]["module"]

    def test_rv_help_check_passes(self, tmp_path):
        """rv help --check must pass with 'plan' verb registered."""
        import subprocess
        import os

        cfg_file = tmp_path / "research_vault.toml"
        cfg_file.write_text(
            f'instance_root = "{tmp_path}"\n'
            f'notes_root = "{tmp_path / "notes"}"\n'
            f'state_dir = "{tmp_path / "state"}"\n'
            f'agents_dir = "{tmp_path / ".agents"}"\n'
            f'tasks_dir = "{tmp_path / "tasks"}"\n'
            f'control_dir = "{tmp_path / "control"}"\n'
            '[adapters]\nnotifier = "file"\nbackend = "local"\nsecrets = "env"\n'
            # SR-APPROVE-GATE: token fingerprint for test-time token approval.
            '[approval]\nenforce = true\n'
            'token_fingerprint = "d309a810bb5f40cef518202e46d197aa61e4dddafc5984c8c698da29ac8fd2bc"\n'
            'enforce_sig = ""\n',
            encoding="utf-8",
        )
        env = {**os.environ, "RESEARCH_VAULT_CONFIG": str(cfg_file)}
        result = subprocess.run(
            [sys.executable, "-m", "research_vault.cli", "help", "--check"],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0, f"rv help --check failed:\n{result.stdout}\n{result.stderr}"
