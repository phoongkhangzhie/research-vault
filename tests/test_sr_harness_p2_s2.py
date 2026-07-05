"""test_sr_harness_p2_s2.py — SR-HARNESS-P2 Slice 2 acceptance tests.

Covers plan/verbs.py — rv plan freeze-harness:
  S2a. freeze-harness after freeze → exits 0, plan gains harness_commits: [main1=sha]
  S2b. second scope accumulates: --scope main2 adds without losing main1
  S2c. WITHOUT prior freeze → exits 1 (FAIL-CLOSED)
  S2d. Editing covers: between freeze and freeze-harness → BLOCK
  S2e. Upsert preserves rest of frontmatter + body byte-for-byte
  S2f. verify_freeze_hash passes on the updated plan (round-trip)
  S2g. _upsert_frontmatter_list_field: field absent / present / replace scope
  S2h. freeze-harness in plan parser (argparse round-trip)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.plan.verbs import _upsert_frontmatter_list_field, _run_freeze_harness
from research_vault.plan.freeze import store_freeze_hash, verify_freeze_hash


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_instance(tmp_path: Path) -> tuple[Path, object]:
    """Create a minimal instance; return (config_path, cfg)."""
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    cfg_path = tmp_path / "research_vault.toml"
    cfg_path.write_text(
        f'instance_root = "{tmp_path}"\n'
        f'notes_root = "{tmp_path / "notes"}"\n'
        f'state_dir = "{state_dir}"\n'
        f'agents_dir = "{tmp_path / ".agents"}"\n'
        f'tasks_dir = "{tmp_path / "tasks"}"\n'
        f'control_dir = "{tmp_path / "control"}"\n'
        '[adapters]\nnotifier = "file"\nbackend = "local"\nsecrets = "env"\n',
        encoding="utf-8",
    )
    return cfg_path


def _make_run(tmp_path: Path, run_id: str = "test-loop") -> tuple:
    """Create a run store + run state, return (store, run_id, manifest_path)."""
    from research_vault.dag.store import RunStore, RunState
    store = RunStore(tmp_path / "state")
    manifest_path = tmp_path / "test-loop.json"
    manifest_path.write_text(
        json.dumps({"run_id": run_id, "nodes": []}), encoding="utf-8"
    )
    rs = RunState(run_id=run_id, manifest_path=str(manifest_path), created_at=time.time())
    store.create(rs)
    return store, run_id, manifest_path


def _plan_note_text(
    *,
    covers: str = "[q1-main1, q1-main1-abl-A]",
    harness_commits: str = "",
    extra_body: str = "",
) -> str:
    fm = (
        f"plan_kind: preregistration\n"
        f"citekey: q1-plan\n"
        f"covers: {covers}\n"
    )
    if harness_commits:
        fm += f"harness_commits: {harness_commits}\n"
    return f"---\n{fm}---\n\n# Q1 Plan\n\nSome body text.\n{extra_body}"


def _write_plan(tmp_path: Path, text: str, filename: str = "q1-plan.md") -> Path:
    p = tmp_path / filename
    p.write_text(text, encoding="utf-8")
    return p


def _write_child(tmp_path: Path, child_id: str) -> None:
    p = tmp_path / f"{child_id}.md"
    p.write_text(
        f"---\ntype: experiments\ncitekey: {child_id}\n"
        f"stance: confirmatory\nplan_role: main\n---\n",
        encoding="utf-8",
    )


def _freeze_run(
    tmp_path: Path,
    store,
    run_id: str,
    plan_path: Path,
    notes_dir: Path,
) -> None:
    """Run rv plan freeze programmatically (calls store_freeze_hash after K-2 check)."""
    # Write dummy child notes
    notes_dir.mkdir(parents=True, exist_ok=True)
    _write_child(notes_dir, "q1-main1")
    _write_child(notes_dir, "q1-main1-abl-A")
    store_freeze_hash(store, run_id, plan_path, notes_root=notes_dir)


def _freeze_harness_args(
    run_id: str,
    plan_note: Path,
    scope: str,
    harness_commit: str,
    notes_root: Path | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        plan_subcommand="freeze-harness",
        run_id=run_id,
        plan_note=str(plan_note),
        scope=scope,
        harness_commit=harness_commit,
        notes_root=str(notes_root) if notes_root else None,
    )


# ===========================================================================
# S2g — _upsert_frontmatter_list_field (unit tests first)
# ===========================================================================

class TestUpsertFrontmatterListField:
    def test_field_absent_injects(self):
        text = "---\nplan_kind: preregistration\ncitekey: q1\n---\n\nbody"
        result = _upsert_frontmatter_list_field(text, "harness_commits", "main1", "abc")
        assert "harness_commits: [main1=abc]" in result
        # Body preserved
        assert "body" in result

    def test_field_present_scope_absent_adds(self):
        text = "---\nplan_kind: preregistration\nharness_commits: [main2=xyz]\n---\n"
        result = _upsert_frontmatter_list_field(text, "harness_commits", "main1", "abc")
        assert "main1=abc" in result
        assert "main2=xyz" in result

    def test_field_present_scope_present_replaces(self):
        text = "---\nplan_kind: preregistration\nharness_commits: [main1=old]\n---\n"
        result = _upsert_frontmatter_list_field(text, "harness_commits", "main1", "new")
        assert "main1=new" in result
        assert "main1=old" not in result

    def test_sort_invariant_on_add(self):
        text = "---\nharness_commits: [main2=y]\n---\n"
        result = _upsert_frontmatter_list_field(text, "harness_commits", "main1", "x")
        # main1 sorts before main2
        idx1 = result.index("main1=x")
        idx2 = result.index("main2=y")
        assert idx1 < idx2

    def test_other_frontmatter_preserved(self):
        text = "---\nplan_kind: preregistration\ncitekey: q1\ncovers: [a, b]\n---\n\nbody"
        result = _upsert_frontmatter_list_field(text, "harness_commits", "main1", "sha")
        assert "plan_kind: preregistration" in result
        assert "citekey: q1" in result
        assert "covers: [a, b]" in result
        assert "body" in result

    def test_body_byte_for_byte(self):
        body = "\n# Plan\n\nSome detailed text with special chars: == → ≠\n"
        text = f"---\ncitekey: q1\n---\n{body}"
        result = _upsert_frontmatter_list_field(text, "harness_commits", "main1", "sha")
        # Body must be byte-for-byte identical
        body_start = result.index("---\n", 4) + 4
        assert result[body_start:] == body

    def test_malformed_frontmatter_returns_unchanged(self):
        text = "no frontmatter here"
        result = _upsert_frontmatter_list_field(text, "harness_commits", "main1", "sha")
        assert result == text


# ===========================================================================
# S2a — freeze-harness after freeze: exits 0, plan gains harness_commits
# ===========================================================================

class TestFreezeHarnessBasic:

    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        cfg_path = _make_instance(tmp_path)
        monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_path))
        from research_vault.config import reset_config_cache
        reset_config_cache()
        yield tmp_path
        reset_config_cache()

    def test_freeze_harness_exits_0(self, env, tmp_path):
        """freeze-harness after freeze → exits 0."""
        notes_dir = tmp_path / "notes"
        store, run_id, _ = _make_run(tmp_path)
        plan = _write_plan(tmp_path, _plan_note_text())
        _freeze_run(tmp_path, store, run_id, plan, notes_dir)

        rc = _run_freeze_harness(
            _freeze_harness_args(run_id, plan, "main1", "abc123", notes_dir)
        )
        assert rc == 0

    def test_freeze_harness_adds_field(self, env, tmp_path):
        """freeze-harness adds harness_commits: [main1=sha] to plan note."""
        notes_dir = tmp_path / "notes"
        store, run_id, _ = _make_run(tmp_path)
        plan = _write_plan(tmp_path, _plan_note_text())
        _freeze_run(tmp_path, store, run_id, plan, notes_dir)

        _run_freeze_harness(
            _freeze_harness_args(run_id, plan, "main1", "abc123", notes_dir)
        )

        updated_text = plan.read_text(encoding="utf-8")
        assert "harness_commits:" in updated_text
        assert "main1=abc123" in updated_text

    def test_freeze_harness_updates_covers_hash(self, env, tmp_path):
        """covers_hash updated; covers_retries_hash stays pinned."""
        notes_dir = tmp_path / "notes"
        store, run_id, _ = _make_run(tmp_path)
        plan = _write_plan(tmp_path, _plan_note_text())
        _freeze_run(tmp_path, store, run_id, plan, notes_dir)

        rs_before = store.load(run_id)
        hash_before = rs_before.meta["plan_freeze"]["covers_hash"]
        retries_before = rs_before.meta["plan_freeze"]["covers_retries_hash"]

        _run_freeze_harness(
            _freeze_harness_args(run_id, plan, "main1", "abc123", notes_dir)
        )

        rs_after = store.load(run_id)
        hash_after = rs_after.meta["plan_freeze"]["covers_hash"]
        retries_after = rs_after.meta["plan_freeze"]["covers_retries_hash"]

        # covers_hash must change (harness block added)
        assert hash_after != hash_before
        # covers_retries_hash must stay at the same value (covers/retries unchanged)
        assert retries_after == retries_before


# ===========================================================================
# S2b — second scope accumulates
# ===========================================================================

class TestFreezeHarnessAccumulate:

    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        cfg_path = _make_instance(tmp_path)
        monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_path))
        from research_vault.config import reset_config_cache
        reset_config_cache()
        yield tmp_path
        reset_config_cache()

    def test_two_scopes_accumulated(self, env, tmp_path):
        """Two freeze-harness calls accumulate both scopes."""
        notes_dir = tmp_path / "notes"
        store, run_id, _ = _make_run(tmp_path)
        plan = _write_plan(tmp_path, _plan_note_text())
        _freeze_run(tmp_path, store, run_id, plan, notes_dir)

        _run_freeze_harness(
            _freeze_harness_args(run_id, plan, "main1", "sha1", notes_dir)
        )
        _run_freeze_harness(
            _freeze_harness_args(run_id, plan, "main2", "sha2", notes_dir)
        )

        text = plan.read_text(encoding="utf-8")
        assert "main1=sha1" in text
        assert "main2=sha2" in text

    def test_replace_scope_sha(self, env, tmp_path):
        """Second freeze-harness on same scope replaces the SHA."""
        notes_dir = tmp_path / "notes"
        store, run_id, _ = _make_run(tmp_path)
        plan = _write_plan(tmp_path, _plan_note_text())
        _freeze_run(tmp_path, store, run_id, plan, notes_dir)

        _run_freeze_harness(
            _freeze_harness_args(run_id, plan, "main1", "old_sha", notes_dir)
        )
        _run_freeze_harness(
            _freeze_harness_args(run_id, plan, "main1", "new_sha", notes_dir)
        )

        text = plan.read_text(encoding="utf-8")
        assert "main1=new_sha" in text
        assert "main1=old_sha" not in text


# ===========================================================================
# S2c — WITHOUT prior freeze → exits 1
# ===========================================================================

class TestFreezeHarnessFailClosed:

    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        cfg_path = _make_instance(tmp_path)
        monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_path))
        from research_vault.config import reset_config_cache
        reset_config_cache()
        yield tmp_path
        reset_config_cache()

    def test_no_prior_freeze_exits_1(self, env, tmp_path):
        """freeze-harness without prior freeze → exits 1 (FAIL-CLOSED)."""
        notes_dir = tmp_path / "notes"
        store, run_id, _ = _make_run(tmp_path)
        plan = _write_plan(tmp_path, _plan_note_text())
        # No _freeze_run call here

        rc = _run_freeze_harness(
            _freeze_harness_args(run_id, plan, "main1", "abc123", notes_dir)
        )
        assert rc == 1


# ===========================================================================
# S2d — covers: edited between freeze and freeze-harness → BLOCK
# ===========================================================================

class TestFreezeHarnessBaselineGuard:

    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        cfg_path = _make_instance(tmp_path)
        monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_path))
        from research_vault.config import reset_config_cache
        reset_config_cache()
        yield tmp_path
        reset_config_cache()

    def test_covers_edit_blocks(self, env, tmp_path):
        """Editing covers: between freeze and freeze-harness → BLOCK (exit 1)."""
        notes_dir = tmp_path / "notes"
        store, run_id, _ = _make_run(tmp_path)
        plan = _write_plan(tmp_path, _plan_note_text())
        _freeze_run(tmp_path, store, run_id, plan, notes_dir)

        # Now edit covers: in the plan note
        text = plan.read_text(encoding="utf-8")
        tampered = text.replace(
            "covers: [q1-main1, q1-main1-abl-A]",
            "covers: [q1-main1, q1-main1-abl-A, q1-extra]"
        )
        plan.write_text(tampered, encoding="utf-8")

        rc = _run_freeze_harness(
            _freeze_harness_args(run_id, plan, "main1", "abc123", notes_dir)
        )
        assert rc == 1


# ===========================================================================
# S2e — upsert preserves rest of frontmatter + body
# ===========================================================================

class TestFreezeHarnessPreservesBody:

    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        cfg_path = _make_instance(tmp_path)
        monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_path))
        from research_vault.config import reset_config_cache
        reset_config_cache()
        yield tmp_path
        reset_config_cache()

    def test_body_preserved(self, env, tmp_path):
        """freeze-harness does not modify the body of the plan note."""
        notes_dir = tmp_path / "notes"
        store, run_id, _ = _make_run(tmp_path)
        body_marker = "BODY_MARKER_UNIQUE_12345"
        plan = _write_plan(
            tmp_path, _plan_note_text(extra_body=f"\n{body_marker}\n")
        )
        _freeze_run(tmp_path, store, run_id, plan, notes_dir)

        _run_freeze_harness(
            _freeze_harness_args(run_id, plan, "main1", "abc123", notes_dir)
        )

        text = plan.read_text(encoding="utf-8")
        assert body_marker in text
        assert "plan_kind: preregistration" in text
        assert "citekey: q1-plan" in text


# ===========================================================================
# S2f — verify_freeze_hash passes after freeze-harness (round-trip)
# ===========================================================================

class TestFreezeHarnessRoundTrip:

    @pytest.fixture
    def env(self, tmp_path, monkeypatch):
        cfg_path = _make_instance(tmp_path)
        monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_path))
        from research_vault.config import reset_config_cache
        reset_config_cache()
        yield tmp_path
        reset_config_cache()

    def test_verify_passes_after_freeze_harness(self, env, tmp_path):
        """verify_freeze_hash passes after freeze → freeze-harness round-trip."""
        notes_dir = tmp_path / "notes"
        store, run_id, _ = _make_run(tmp_path)
        plan = _write_plan(tmp_path, _plan_note_text())
        _freeze_run(tmp_path, store, run_id, plan, notes_dir)

        rc = _run_freeze_harness(
            _freeze_harness_args(run_id, plan, "main1", "abc123", notes_dir)
        )
        assert rc == 0

        ok, msg = verify_freeze_hash(store, run_id, plan, notes_root=notes_dir,
                                     require_frozen=True)
        assert ok is True, f"verify_freeze_hash failed: {msg}"


# ===========================================================================
# S2h — freeze-harness subparser present (argparse round-trip)
# ===========================================================================

class TestFreezeHarnessParser:
    def test_freeze_harness_in_parser(self):
        from research_vault.plan.verbs import build_parser
        import argparse as ap
        parent = ap.ArgumentParser()
        subs = parent.add_subparsers(dest="verb")
        build_parser(subs)
        args = parent.parse_args([
            "plan", "freeze-harness", "my-run", "/some/plan.md",
            "--scope", "main1", "--harness-commit", "abc123"
        ])
        assert args.plan_subcommand == "freeze-harness"
        assert args.run_id == "my-run"
        assert args.scope == "main1"
        assert args.harness_commit == "abc123"

    def test_freeze_harness_requires_scope(self):
        from research_vault.plan.verbs import build_parser
        import argparse as ap
        parent = ap.ArgumentParser()
        subs = parent.add_subparsers(dest="verb")
        build_parser(subs)
        with pytest.raises(SystemExit):
            parent.parse_args([
                "plan", "freeze-harness", "my-run", "/some/plan.md",
                "--harness-commit", "abc123"  # --scope missing
            ])

    def test_freeze_harness_requires_harness_commit(self):
        from research_vault.plan.verbs import build_parser
        import argparse as ap
        parent = ap.ArgumentParser()
        subs = parent.add_subparsers(dest="verb")
        build_parser(subs)
        with pytest.raises(SystemExit):
            parent.parse_args([
                "plan", "freeze-harness", "my-run", "/some/plan.md",
                "--scope", "main1"  # --harness-commit missing
            ])
