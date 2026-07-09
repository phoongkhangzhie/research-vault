"""test_project_remove.py — Tests for `rv project remove` (clean local teardown).

Hermetic: all tests run in tmp_path, never touch ~/vault or any private instance.

Acceptance checklist (spec: docs/superpowers/specs/2026-07-08-rv-project-remove.md §7):
  1. Default (no flags) is NON-destructive: registry gone, edges pruned, control
     archived, worktrees cleared (when clean) — repo, .agents/, GitHub UNTOUCHED.
  2. --dry-run prints the full plan and mutates NOTHING (registry, repo, worktrees,
     control, edges, tasks, dag runs all byte-identical after).
  3. THE unpushed-work guard is FAIL-CLOSED: uncommitted files / unpushed commits /
     un-pushed branches / stashes in the repo OR a worktree → REFUSE + enumerate
     the at-risk manifest; a clean repo+worktrees → proceeds.
  4. --force downgrades a guard REFUSE to a typed confirmation (never a silent
     bypass) — declining leaves the guarded step un-executed.
  5. --purge-repo only removes the local checkout when the guard passes (or is
     force-confirmed); default leaves the repo on disk.
  6. --purge-agents archives (never hard-deletes) `.agents/<slug>/`.
  7. Live/provisional DAG runs block dag-run archiving (REFUSE, not silent skip).
  8. The ⟦VAULT-TEARDOWN <slug>⟧ handoff is emitted with all five listed lines.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Generator

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from research_vault.config import load_config, reset_config_cache
from research_vault.project import DEFAULT_ROSTER, cmd_add, cmd_new, cmd_remove
from research_vault import project_edges
from research_vault import task as task_mod
from research_vault.dag.store import RunState, RunStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo)] + list(args), capture_output=True, text=True,
    )


@pytest.fixture
def rv_instance(tmp_path: Path, monkeypatch) -> Generator[Path, None, None]:
    """Minimal RV instance — config wired, no demo projects."""
    config_file = tmp_path / "research_vault.toml"
    config_file.write_text(
        f"""\
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
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(config_file))
    # Isolate worktree + archive roots from any real machine state.
    monkeypatch.setenv("RV_ARCHIVE_ROOT", str(tmp_path / "vault-archive"))
    reset_config_cache()
    yield tmp_path
    reset_config_cache()


@pytest.fixture
def demo_project(rv_instance: Path) -> Path:
    """A real `rv project new` project — bare origin remote configured + pushed."""
    src = rv_instance / "projects" / "demo"
    bare = rv_instance / "bare-demo.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True, capture_output=True)
    rc = cmd_new("demo", "dm", str(src), DEFAULT_ROSTER)
    assert rc == 0
    _git(src, "remote", "add", "origin", str(bare))
    _git(src, "push", "-q", "-u", "origin", "main")
    # `cmd_new` does not itself invoke `rv build agents-dir` — fabricate the
    # per-project crew-memory dir the same way a real build pass would, so
    # the .agents/<slug>/ artifact (V2 in the design's inventory) is real.
    agents_dir = rv_instance / ".agents" / "demo"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "engineer.md").write_text("# engineer hat\n", encoding="utf-8")
    reset_config_cache()
    load_config(reload=True)
    return rv_instance


def _cfg(rv_instance: Path):
    reset_config_cache()
    return load_config(reload=True)


# ---------------------------------------------------------------------------
# 1. Default (no flags) — non-destructive stand-down, clean repo
# ---------------------------------------------------------------------------

class TestDefaultNonDestructive:
    def test_deregisters(self, demo_project: Path) -> None:
        config_file = demo_project / "research_vault.toml"
        rc = cmd_remove("demo")
        parsed = tomllib.loads(config_file.read_bytes().decode())
        assert "demo" not in parsed.get("projects", {}), "registry entry must be removed"

    def test_repo_left_intact(self, demo_project: Path) -> None:
        src = demo_project / "projects" / "demo"
        cmd_remove("demo")
        assert src.exists(), "repo must NOT be removed by default"
        assert (src / ".git").exists()

    def test_agents_dir_left_intact(self, demo_project: Path) -> None:
        agents_dir = demo_project / ".agents" / "demo"
        assert agents_dir.exists(), "fixture sanity: build-agents wrote .agents/demo"
        cmd_remove("demo")
        assert agents_dir.exists(), ".agents/<slug> must NOT be touched by default"

    def test_control_archived_not_deleted(self, demo_project: Path) -> None:
        control_file = demo_project / "control" / "demo.md"
        assert control_file.exists()
        cmd_remove("demo")
        assert not control_file.exists(), "live control file must be moved out"
        archived = list((demo_project / "control" / "_archive").glob("demo*.md"))
        assert archived, "control file must be archived, not deleted"

    def test_edges_pruned(self, demo_project: Path) -> None:
        src2 = demo_project / "projects" / "peer"
        cmd_new("peer", "pr", str(src2), [])
        reset_config_cache()
        cfg = load_config(reload=True)
        project_edges.add_edge(cfg, "demo", "peer", "shares-methodology")
        assert project_edges.peers_of(cfg, "demo") == {"peer"}
        cmd_remove("demo")
        cfg2 = load_config(reload=True)
        assert project_edges.peers_of(cfg2, "peer") == set(), "edge must be pruned from the peer side too"

    def test_worktrees_cleared_when_clean(self, demo_project: Path) -> None:
        cfg = _cfg(demo_project)
        from research_vault import wt as wt_mod
        wt_path = Path(wt_mod.cmd_add("some-task", cfg, project="demo"))
        assert wt_path.exists()
        cmd_remove("demo")
        assert not wt_path.exists(), "clean worktree must be cleared by default"

    def test_github_never_touched(self, demo_project: Path, monkeypatch) -> None:
        calls = []
        real_run = subprocess.run

        def _spy(args, *a, **kw):
            if isinstance(args, list) and "gh" in args:
                calls.append(args)
            return real_run(args, *a, **kw)

        monkeypatch.setattr(subprocess, "run", _spy)
        cmd_remove("demo")
        assert not calls, f"no gh invocation should happen without --archive-github: {calls}"


# ---------------------------------------------------------------------------
# 2. --dry-run touches nothing
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_mutates_nothing(self, demo_project: Path, capsys) -> None:
        config_file = demo_project / "research_vault.toml"
        before = config_file.read_bytes()
        src = demo_project / "projects" / "demo"
        control_file = demo_project / "control" / "demo.md"
        assert control_file.exists()

        rc = cmd_remove("demo", dry_run=True)

        after = config_file.read_bytes()
        assert before == after, "config file must be byte-identical after --dry-run"
        assert src.exists()
        assert control_file.exists(), "control file must not be archived on dry-run"
        out = capsys.readouterr().out
        assert "demo" in out
        assert rc == 0

    def test_dry_run_prints_plan_for_unknown_flags(self, demo_project: Path, capsys) -> None:
        cmd_remove("demo", dry_run=True, purge_repo=True, purge_agents=True)
        out = capsys.readouterr().out
        assert "purge" in out.lower() or "repo" in out.lower()


# ---------------------------------------------------------------------------
# 3. The unpushed-work guard — fail-closed
# ---------------------------------------------------------------------------

class TestUnpushedWorkGuard:
    def test_uncommitted_file_refuses_worktree_clean(self, demo_project: Path, capsys) -> None:
        cfg = _cfg(demo_project)
        from research_vault import wt as wt_mod
        wt_path = Path(wt_mod.cmd_add("dirty-task", cfg, project="demo"))
        (wt_path / "scratch.txt").write_text("uncommitted\n")

        rc = cmd_remove("demo")

        assert wt_path.exists(), "dirty worktree must NOT be cleared"
        out = capsys.readouterr().out
        assert "uncommitted" in out.lower() or "untracked" in out.lower()
        assert rc != 0

    def test_unpushed_commit_refuses(self, demo_project: Path, capsys) -> None:
        cfg = _cfg(demo_project)
        from research_vault import wt as wt_mod
        wt_path = Path(wt_mod.cmd_add("ahead-task", cfg, project="demo"))
        (wt_path / "f.txt").write_text("x\n")
        _git(wt_path, "add", "-A")
        _git(wt_path, "commit", "-q", "-m", "unpushed work")

        rc = cmd_remove("demo")

        assert wt_path.exists(), "worktree with unpushed commits must NOT be cleared"
        out = capsys.readouterr().out
        assert "unpushed" in out.lower() or "ahead" in out.lower()
        assert rc != 0

    def test_unpushed_branch_refuses(self, demo_project: Path, capsys) -> None:
        src = demo_project / "projects" / "demo"
        _git(src, "checkout", "-q", "-b", "local-only-branch")
        _git(src, "checkout", "-q", "main")

        rc = cmd_remove("demo", purge_repo=True)

        out = capsys.readouterr().out
        assert "local-only-branch" in out
        assert rc != 0
        assert src.exists(), "purge-repo must be refused when a branch has no upstream"

    def test_stash_refuses(self, demo_project: Path, capsys) -> None:
        src = demo_project / "projects" / "demo"
        (src / "scratch.txt").write_text("stash-me\n")
        _git(src, "add", "-A")
        _git(src, "stash", "-u")

        rc = cmd_remove("demo", purge_repo=True)

        out = capsys.readouterr().out
        assert "stash" in out.lower()
        assert rc != 0
        assert src.exists()

    def test_clean_repo_and_worktree_proceeds(self, demo_project: Path) -> None:
        cfg = _cfg(demo_project)
        from research_vault import wt as wt_mod
        wt_path = Path(wt_mod.cmd_add("clean-task", cfg, project="demo"))

        rc = cmd_remove("demo")

        assert not wt_path.exists(), "clean worktree must be cleared"
        assert rc == 0


# ---------------------------------------------------------------------------
# 4. --force downgrades REFUSE to typed confirm
# ---------------------------------------------------------------------------

class TestForceTypedConfirm:
    def test_force_with_confirmation_declined_still_blocks(self, demo_project: Path) -> None:
        cfg = _cfg(demo_project)
        from research_vault import wt as wt_mod
        wt_path = Path(wt_mod.cmd_add("dirty-task", cfg, project="demo"))
        (wt_path / "scratch.txt").write_text("uncommitted\n")

        rc = cmd_remove("demo", force=True, input_fn=lambda _prompt: "no")

        assert wt_path.exists(), "declining the typed confirm must leave the worktree untouched"
        assert rc != 0

    def test_force_with_confirmation_accepted_proceeds(self, demo_project: Path) -> None:
        cfg = _cfg(demo_project)
        from research_vault import wt as wt_mod
        wt_path = Path(wt_mod.cmd_add("dirty-task", cfg, project="demo"))
        (wt_path / "scratch.txt").write_text("uncommitted\n")

        rc = cmd_remove("demo", force=True, input_fn=lambda _prompt: "demo")

        assert not wt_path.exists(), "typed slug confirm must proceed with the clear"


# ---------------------------------------------------------------------------
# 5. --purge-repo (guard-gated)
# ---------------------------------------------------------------------------

class TestPurgeRepo:
    def test_purge_repo_removes_local_checkout_when_clean(self, demo_project: Path) -> None:
        src = demo_project / "projects" / "demo"
        rc = cmd_remove("demo", purge_repo=True, input_fn=lambda _p: "y")
        assert rc == 0
        assert not src.exists(), "clean guard + confirm must remove the local checkout"

    def test_purge_repo_default_off(self, demo_project: Path) -> None:
        src = demo_project / "projects" / "demo"
        cmd_remove("demo")
        assert src.exists(), "repo must survive without --purge-repo"


# ---------------------------------------------------------------------------
# 6. --purge-agents (archive, never delete)
# ---------------------------------------------------------------------------

class TestPurgeAgents:
    def test_purge_agents_archives_not_deletes(self, demo_project: Path) -> None:
        agents_dir = demo_project / ".agents" / "demo"
        assert agents_dir.exists()
        archive_root = Path(os.environ["RV_ARCHIVE_ROOT"])

        rc = cmd_remove("demo", purge_agents=True, input_fn=lambda _p: "demo")

        assert not agents_dir.exists(), ".agents/<slug> must be moved out"
        archived = list(archive_root.glob("demo*"))
        assert archived, "must be archived under RV_ARCHIVE_ROOT, never hard-deleted"

    def test_default_leaves_agents_dir(self, demo_project: Path) -> None:
        agents_dir = demo_project / ".agents" / "demo"
        cmd_remove("demo")
        assert agents_dir.exists()


# ---------------------------------------------------------------------------
# 7. Live DAG runs — REFUSE, never silent skip
# ---------------------------------------------------------------------------

class TestDagRunGuard:
    def test_live_run_blocks_archiving(self, demo_project: Path, capsys) -> None:
        cfg = _cfg(demo_project)
        src = demo_project / "projects" / "demo"
        store = RunStore.from_config(cfg)
        rs = RunState(run_id="demo-loop-live", manifest_path=str(src / "manifest.json"))
        rs.node_states["n1"] = {"status": "running"}
        store.create(rs)

        rc = cmd_remove("demo")

        out = capsys.readouterr().out
        assert "demo-loop-live" in out
        assert "live" in out.lower() or "running" in out.lower()
        run_file = cfg.state_dir / "dag" / "demo-loop-live.json"
        assert run_file.exists(), "a live run must NOT be archived"
        assert rc != 0

    def test_terminal_run_archived(self, demo_project: Path) -> None:
        cfg = _cfg(demo_project)
        src = demo_project / "projects" / "demo"
        store = RunStore.from_config(cfg)
        rs = RunState(run_id="demo-loop-done", manifest_path=str(src / "manifest.json"))
        rs.node_states["n1"] = {"status": "succeeded"}
        store.create(rs)

        cmd_remove("demo")

        run_file = cfg.state_dir / "dag" / "demo-loop-done.json"
        assert not run_file.exists(), "a terminal run must be archived (moved), not left live"
        archived = list((cfg.state_dir / "dag" / "_archive").glob("demo-loop-done*.json"))
        assert archived


# ---------------------------------------------------------------------------
# 8. The ⟦VAULT-TEARDOWN⟧ handoff
# ---------------------------------------------------------------------------

class TestVaultTeardownHandoff:
    def test_handoff_emitted_with_all_lines(self, demo_project: Path, capsys) -> None:
        cmd_remove("demo")
        out = capsys.readouterr().out
        assert "VAULT-TEARDOWN" in out
        assert "demo" in out
        assert "projects.json" in out
        assert "agents-dir" in out
        assert "hub-clone" in out
        assert "deploy" in out or "mirror" in out
        assert "github-repo" in out
        assert "PRESERVED" in out


# ---------------------------------------------------------------------------
# 9. Unknown project — clean error, no crash
# ---------------------------------------------------------------------------

class TestUnknownProject:
    def test_unknown_slug_errors(self, rv_instance: Path, capsys) -> None:
        rc = cmd_remove("nonexistent")
        assert rc != 0
        err = capsys.readouterr().err
        assert "nonexistent" in err
