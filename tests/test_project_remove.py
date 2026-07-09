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
    subprocess.run(
        ["git", "init", "-q", "--bare", "--initial-branch=main", str(bare)],
        check=True, capture_output=True,
    )
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


@pytest.fixture
def cs_demo_project(rv_instance: Path) -> Path:
    """A CS-convention project: `source_dir = <repo>/notes` (structural
    marker per `config.resolve_repo_root` — `source_dir`'s basename is
    exactly "notes"), root-level artifacts live at `<repo>` = `source_dir.parent`.

    `cmd_new` only stands up the flat convention (`git init` directly at
    `source_dir`), so this fixture hand-builds the CS layout and registers
    it via `cmd_add` directly — mirroring how a real adopter's existing
    CS-shaped repo gets added to the vault.
    """
    repo_root = rv_instance / "projects" / "cs-demo"
    notes_dir = repo_root / "notes"
    bare = rv_instance / "bare-cs-demo.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "--initial-branch=main", str(bare)],
        check=True, capture_output=True,
    )
    notes_dir.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q", "--initial-branch=main")
    subprocess.run(
        ["git", "-C", str(repo_root), "config", "user.email", "rv-test@example.invalid"],
        capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "-C", str(repo_root), "config", "user.name", "rv test"],
        capture_output=True, text=True,
    )
    (repo_root / "pointers.md").write_text("# pointers\n", encoding="utf-8")
    (notes_dir / ".gitkeep").write_text("", encoding="utf-8")
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "init")
    _git(repo_root, "remote", "add", "origin", str(bare))
    _git(repo_root, "push", "-q", "-u", "origin", "main")

    cmd_add("cs-demo", "csd", str(notes_dir), DEFAULT_ROSTER)
    reset_config_cache()
    load_config(reload=True)
    return rv_instance


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
# 7b. DAG-run → project matching must not prefix-false-positive on a
#     sibling slug ("foo" vs "foobar") — str.startswith on the resolved
#     path string is not path-aware.  Regression for the silent
#     cross-project mutation where a *terminal* run belonging to a
#     prefix-sibling project gets archived (shutil.move'd) as if it were
#     this project's own run.
# ---------------------------------------------------------------------------

class TestDagRunPrefixSiblingGuard:
    def test_foobar_run_not_matched_by_foo_removal(self, rv_instance: Path) -> None:
        cfg = _cfg(rv_instance)
        # Two independently-registered projects sharing a name prefix.
        foo_src = rv_instance / "projects" / "foo"
        foobar_src = rv_instance / "projects" / "foobar"
        foo_bare = rv_instance / "bare-foo.git"
        foobar_bare = rv_instance / "bare-foobar.git"
        for bare in (foo_bare, foobar_bare):
            subprocess.run(
                ["git", "init", "-q", "--bare", "--initial-branch=main", str(bare)],
                check=True, capture_output=True,
            )
        assert cmd_new("foo", "fo", str(foo_src), DEFAULT_ROSTER) == 0
        _git(foo_src, "remote", "add", "origin", str(foo_bare))
        _git(foo_src, "push", "-q", "-u", "origin", "main")

        assert cmd_new("foobar", "fb", str(foobar_src), DEFAULT_ROSTER) == 0
        _git(foobar_src, "remote", "add", "origin", str(foobar_bare))
        _git(foobar_src, "push", "-q", "-u", "origin", "main")
        reset_config_cache()
        cfg = load_config(reload=True)

        # A terminal DAG run belonging ONLY to "foobar".
        store = RunStore.from_config(cfg)
        rs = RunState(
            run_id="foobar-loop-done",
            manifest_path=str(foobar_src / "manifest.json"),
        )
        rs.node_states["n1"] = {"status": "succeeded"}
        store.create(rs)

        # Remove "foo" — must NOT touch foobar's run.
        cmd_remove("foo")

        run_file = cfg.state_dir / "dag" / "foobar-loop-done.json"
        assert run_file.exists(), (
            "foobar's terminal run must NOT be archived by removing the "
            "prefix-sibling project 'foo' (str.startswith false-positive)"
        )
        archived = list((cfg.state_dir / "dag" / "_archive").glob("foobar-loop-done*.json"))
        assert not archived, "foobar's run must not have been moved into foo's teardown archive"

    def test_genuine_child_path_still_matches(self, demo_project: Path) -> None:
        """Sanity: the fix must not regress the genuine (non-prefix) match —
        a run whose manifest is truly nested under the project's own repo
        root must still be found and archived."""
        cfg = _cfg(demo_project)
        src = demo_project / "projects" / "demo"
        store = RunStore.from_config(cfg)
        rs = RunState(
            run_id="demo-loop-nested",
            manifest_path=str(src / "sub" / "dir" / "manifest.json"),
        )
        rs.node_states["n1"] = {"status": "succeeded"}
        store.create(rs)

        cmd_remove("demo")

        run_file = cfg.state_dir / "dag" / "demo-loop-nested.json"
        assert not run_file.exists()
        archived = list((cfg.state_dir / "dag" / "_archive").glob("demo-loop-nested*.json"))
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

    def test_handoff_agents_dir_not_a_vault_todo_when_purge_agents(
        self, demo_project: Path, capsys
    ) -> None:
        """rv itself archives `.agents/<slug>/` under --purge-agents — the
        handoff must NOT also tell the vault consumer to archive it (same
        artifact, double-claimed).  It must instead be informational: state
        the actual action rv already took (and where)."""
        cmd_remove("demo", purge_agents=True, input_fn=lambda _p: "demo")
        out = capsys.readouterr().out
        handoff = out[out.index("VAULT-TEARDOWN"):]
        agents_line = next(
            line for line in handoff.splitlines() if line.strip().startswith("agents-dir:")
        )
        assert "archive" in agents_line.lower()
        # Must not read as a to-do for the vault side ("-> archive"); must
        # reflect that rv already did it.
        assert "archived by rv" in agents_line
        archive_root = Path(os.environ["RV_ARCHIVE_ROOT"])
        assert str(archive_root) in agents_line

    def test_handoff_agents_dir_left_in_place_when_no_purge(
        self, demo_project: Path, capsys
    ) -> None:
        """Without --purge-agents, `.agents/<slug>/` is untouched — the
        handoff must say so, never instruct the vault side to archive an
        artifact that rv now owns archiving."""
        agents_dir = demo_project / ".agents" / "demo"
        cmd_remove("demo")
        out = capsys.readouterr().out
        handoff = out[out.index("VAULT-TEARDOWN"):]
        agents_line = next(
            line for line in handoff.splitlines() if line.strip().startswith("agents-dir:")
        )
        assert "left in place" in agents_line
        assert str(agents_dir) in agents_line
        assert "--purge-agents" in agents_line
        assert "-> archive" not in agents_line


# ---------------------------------------------------------------------------
# 10. CS-convention layout (`source_dir = <repo>/notes`) — guard/action
#     path-helper parity.  `wt.cmd_add`/`wt.cmd_clean` create/clear worktrees
#     off `wt._resolve_repo` (= `source_dir`), so the removal-plan's guard
#     enumeration MUST use the same path — not `cfg.project_repo_root` (which
#     differs on this layout: `source_dir.parent`).  Regression for the
#     silent-data-loss BLOCK where `--purge-repo` rmtree'd a repo whose
#     nested worktree (with uncommitted work) the guard never saw.
#
#     NOTE on the message-content assertions below: `capsys.readouterr()` is
#     called immediately after `wt.cmd_add` (which itself prints "Created
#     worktree: <full wt_path>") so that buffer is drained BEFORE invoking
#     `cmd_remove` — otherwise an assertion like `str(wt_path) in out` would
#     pass vacuously off the worktree-creation echo, never actually proving
#     the *guard* named the dirty worktree.
# ---------------------------------------------------------------------------

class TestCsLayoutWorktreeGuard:
    def test_build_removal_plan_sees_the_real_cs_worktree(self, cs_demo_project: Path) -> None:
        """Definitive proof `_build_removal_plan` enumerates worktrees off
        the SAME path `wt.cmd_add`/`wt.cmd_clean` operate on. Immune to the
        coincidental top-level self-trip (a bare `notes-wt/` dir shows up as
        untracked in the repo-root scan regardless of its contents) that
        would otherwise mask this specific defect."""
        cfg = _cfg(cs_demo_project)
        from research_vault import wt as wt_mod
        from research_vault.project import _build_removal_plan
        wt_path = Path(wt_mod.cmd_add("dirty-task", cfg, project="cs-demo"))
        (wt_path / "scratch.txt").write_text("uncommitted\n")

        plan = _build_removal_plan(cfg, "cs-demo")

        assert plan["worktrees"] == [wt_path], (
            f"plan must enumerate the real worktree {wt_path}, got {plan['worktrees']}"
        )
        assert any(str(wt_path) in issue for issue in plan["guard_issues"]), (
            "guard_issues must name the specific dirty worktree, not just the "
            f"repo root; got {plan['guard_issues']}"
        )

    def test_uncommitted_worktree_refuses_default_clean(
        self, cs_demo_project: Path, capsys
    ) -> None:
        cfg = _cfg(cs_demo_project)
        from research_vault import wt as wt_mod
        wt_path = Path(wt_mod.cmd_add("dirty-task", cfg, project="cs-demo"))
        # Sanity: this worktree lives under source_dir-wt (notes-wt), NOT
        # repo_root-wt — proves the fixture actually exercises the CS shape.
        assert wt_path.parent.name == "notes-wt", wt_path
        (wt_path / "scratch.txt").write_text("uncommitted\n")
        capsys.readouterr()  # drain the "Created worktree: <wt_path>" echo

        rc = cmd_remove("cs-demo")

        assert wt_path.exists(), "dirty CS-layout worktree must NOT be cleared"
        out = capsys.readouterr().out
        # Must name the specific dirty worktree — not merely the coincidental
        # top-level "notes-wt/ is an untracked dir" self-trip (which fires
        # regardless of whether the worktree's own contents are dirty).
        assert str(wt_path) in out, out
        assert rc != 0

    def test_purge_repo_refuses_on_cs_layout_nested_worktree_with_unpushed_work(
        self, cs_demo_project: Path, capsys
    ) -> None:
        """The exact silent-data-loss repro: drive --purge-repo to the point
        of rmtree with a dirty nested (notes-wt) worktree present. Must
        REFUSE and the file must survive."""
        repo_root = cs_demo_project / "projects" / "cs-demo"
        cfg = _cfg(cs_demo_project)
        from research_vault import wt as wt_mod
        wt_path = Path(wt_mod.cmd_add("dirty-task", cfg, project="cs-demo"))
        marker = wt_path / "unpushed-work.txt"
        marker.write_text("do not lose me\n")
        capsys.readouterr()  # drain the "Created worktree: <wt_path>" echo

        rc = cmd_remove("cs-demo", purge_repo=True, input_fn=lambda _p: "y")

        out = capsys.readouterr().out
        assert str(wt_path) in out, out
        assert rc != 0
        assert repo_root.exists(), "repo must survive — guard must have refused before rmtree"
        assert marker.exists(), "uncommitted work in the nested worktree must survive"

    def test_default_worktree_clean_actually_clears_cs_layout_worktree(
        self, cs_demo_project: Path
    ) -> None:
        cfg = _cfg(cs_demo_project)
        from research_vault import wt as wt_mod
        wt_path = Path(wt_mod.cmd_add("clean-task", cfg, project="cs-demo"))
        assert wt_path.exists()

        rc = cmd_remove("cs-demo")

        assert not wt_path.exists(), "clean CS-layout worktree must be cleared by default"
        assert rc == 0


# ---------------------------------------------------------------------------
# 9. Unknown project — clean error, no crash
# ---------------------------------------------------------------------------

class TestUnknownProject:
    def test_unknown_slug_errors(self, rv_instance: Path, capsys) -> None:
        rc = cmd_remove("nonexistent")
        assert rc != 0
        err = capsys.readouterr().err
        assert "nonexistent" in err
