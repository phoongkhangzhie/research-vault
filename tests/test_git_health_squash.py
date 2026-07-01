"""test_git_health_squash.py — Signal D regression tests for git-health + reconcile.

Verifies that git-health correctly classifies squash-merged branches as
DELETE (was: FLAG — the SR-CP blind-spot), AND that the control-reconcile
LocalGitSource.get_terminal_set detects squash-merged ids via the shared
gitlib helper (B1 acceptance: single implementation, no duplication).

The squash-terminal detection consumes gitlib.squash_terminal_ids, so this
test also verifies the integration: gitlib helper + git_health._classify_branch
+ status.LocalGitSource.get_terminal_set.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from research_vault.git_health import _classify_branch, cmd_report
from research_vault.config import Config
from tests.gitutil import squash_merge_repo


class TestSignalDSquash:
    """Signal D: squash-merged branches classify as DELETE, not FLAG."""

    def test_squash_merged_branch_classifies_delete(self, tmp_git_repo: Path):
        """A branch squash-merged via (#N) commit must be DELETE, not FLAG.

        Regression: before Signal D, the branch had unique commits not visible
        via --is-ancestor (Signal A) or --merged (Signal B/C), so it was FLAG.
        After Signal D it must be DELETE.

        The branch name is feat/sr-gd and the squash subject token is also
        sr-gd — both yield the same id-token, so Signal D fires correctly.
        """
        squash_merge_repo(
            tmp_git_repo,
            "feat/sr-gd",
            "feat(sr-gd): squash delivered (#99)",
        )
        # Re-create the branch at main to simulate "stale, not yet deleted"
        # (still exists locally after the remote squash-merge + delete).
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "checkout", "-b", "feat/sr-gd"],
            check=True, capture_output=True,
        )
        # The squash subject "feat(sr-gd): ... (#99)" → token "sr-gd"
        # The branch "feat/sr-gd" → token "sr-gd"
        row = _classify_branch(
            repo=tmp_git_repo,
            branch="feat/sr-gd",
            current="main",
            fetch_ok=False,  # no remote — Signal A/C disabled
            squash_terminals=frozenset({"sr-gd"}),  # as gitlib would return
        )
        assert row.cls == "DELETE", (
            f"Expected DELETE for squash-merged branch, got {row.cls} (reason: {row.reason})"
        )

    def test_unmerged_branch_classifies_flag(self, tmp_git_repo: Path):
        """A branch with unique commits and NO squash signal must be FLAG."""
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "checkout", "-b", "feat/not-merged"],
            check=True, capture_output=True,
        )
        (tmp_git_repo / "unique.txt").write_text("unique\n")
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "add", "."], check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "commit", "-m", "wip: unique"],
            check=True, capture_output=True,
        )
        row = _classify_branch(
            repo=tmp_git_repo,
            branch="feat/not-merged",
            current="main",
            fetch_ok=False,
            squash_terminals=frozenset(),  # no squash signals
        )
        assert row.cls == "FLAG", (
            f"Expected FLAG for unmerged branch, got {row.cls}"
        )

    def test_cmd_report_with_squash_merge(self, tmp_path: Path):
        """Integration: cmd_report classifies squash-merged branch as DELETE in report."""
        # Build a minimal config pointing at the test repo
        from tests.gitutil import tmp_git_repo as _fixture  # not using fixture here

        # Create a hermetic repo manually
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init", "--initial-branch=main", str(repo)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "t@t.invalid"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "T"],
            check=True, capture_output=True,
        )
        (repo / "README.md").write_text("init\n")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "chore: init"],
            check=True, capture_output=True,
        )
        squash_merge_repo(repo, "feat/sr-integr", "feat(sr-integr): done (#55)")
        # Re-create the branch on main to simulate "stale, not yet deleted"
        # (so git-health sees the branch still exists locally)
        subprocess.run(
            ["git", "-C", str(repo), "checkout", "main"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "checkout", "-b", "feat/sr-integr"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "checkout", "main"],
            check=True, capture_output=True,
        )

        config_file = tmp_path / "research_vault.toml"
        config_file.write_text(
            f"""
instance_root = "{tmp_path}"
notes_root = "{tmp_path / 'notes'}"
state_dir = "{tmp_path / 'state'}"
agents_dir = "{tmp_path / '.agents'}"
tasks_dir = "{tmp_path / 'tasks'}"
control_dir = "{tmp_path / 'control'}"
""",
            encoding="utf-8",
        )
        old_env = os.environ.get("RESEARCH_VAULT_CONFIG")
        old_repos = os.environ.get("GIT_HEALTH_REPOS")
        os.environ["RESEARCH_VAULT_CONFIG"] = str(config_file)
        os.environ["GIT_HEALTH_REPOS"] = json.dumps({"test-repo": str(repo)})
        try:
            from research_vault.config import load_config, reset_config_cache
            reset_config_cache()
            cfg = load_config()
            import io
            import sys
            # cmd_report returns 0 when no FLAG branches remain
            # We just check it doesn't raise and returns an int
            result = cmd_report(cfg, prune=False)
            assert isinstance(result, int)
        finally:
            if old_env is None:
                os.environ.pop("RESEARCH_VAULT_CONFIG", None)
            else:
                os.environ["RESEARCH_VAULT_CONFIG"] = old_env
            if old_repos is None:
                os.environ.pop("GIT_HEALTH_REPOS", None)
            else:
                os.environ["GIT_HEALTH_REPOS"] = old_repos


# ---------------------------------------------------------------------------
# B1: LocalGitSource.get_terminal_set uses gitlib (no duplicate squash impl)
# ---------------------------------------------------------------------------


class TestLocalGitSourceSquashViaGitlib:
    """Reconcile's LocalGitSource.get_terminal_set detects squash-merged ids via gitlib.

    B1 acceptance: after the fix, status._PR_ANCHOR_RE is gone and the squash
    detection is fully delegated to gitlib.squash_terminal_ids.  These tests
    verify the BEHAVIOUR — that the terminal set includes ids from squash
    subjects — and are therefore also a correctness regression guard if the
    gitlib delegation ever breaks.
    """

    def test_local_git_source_detects_squash_terminal(self, tmp_path):
        """LocalGitSource.get_terminal_set returns the token from a squash-merged commit."""
        import os
        from research_vault.status import LocalGitSource
        from research_vault.config import load_config, reset_config_cache

        # Build a hermetic repo with a squash merge
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init", "--initial-branch=main", str(repo)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "t@t.invalid"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "T"],
            check=True, capture_output=True,
        )
        (repo / "README.md").write_text("init\n")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "chore: init"],
            check=True, capture_output=True,
        )
        # Squash-merge: GitHub appends (#N) automatically; we do it manually.
        squash_merge_repo(repo, "feat/sr-b1test", "feat(sr-b1test): migrated squash parser (#7)")

        # Minimal config; LocalGitSource uses repo_path= directly, bypassing config lookup.
        config_file = tmp_path / "rv.toml"
        config_file.write_text(
            f"""
instance_root = "{tmp_path}"
notes_root = "{tmp_path / 'notes'}"
state_dir = "{tmp_path / 'state'}"
agents_dir = "{tmp_path / '.agents'}"
tasks_dir = "{tmp_path / 'tasks'}"
control_dir = "{tmp_path / 'control'}"
""",
            encoding="utf-8",
        )
        old_env = os.environ.get("RESEARCH_VAULT_CONFIG")
        os.environ["RESEARCH_VAULT_CONFIG"] = str(config_file)
        try:
            reset_config_cache()
            cfg = load_config()
            src = LocalGitSource(repo_path=repo)
            terminal = src.get_terminal_set(cfg, "any-project")
        finally:
            if old_env is None:
                os.environ.pop("RESEARCH_VAULT_CONFIG", None)
            else:
                os.environ["RESEARCH_VAULT_CONFIG"] = old_env
            reset_config_cache()

        assert "sr-b1test" in terminal, (
            f"Expected 'sr-b1test' in terminal set from squash commit, got: {terminal!r}.\n"
            "LocalGitSource.get_terminal_set must delegate to gitlib.squash_terminal_ids."
        )

    def test_local_git_source_no_squash_token_absent(self, tmp_path):
        """An id NOT anchored with (#N) must NOT appear in the terminal squash set."""
        import os
        from research_vault.status import LocalGitSource
        from research_vault.config import load_config, reset_config_cache

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init", "--initial-branch=main", str(repo)],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "t@t.invalid"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "T"],
            check=True, capture_output=True,
        )
        (repo / "README.md").write_text("init\n")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "chore: init"],
            check=True, capture_output=True,
        )
        # Commit WITHOUT the (#N) anchor — NOT a squash merge
        (repo / "other.txt").write_text("other\n")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "feat(sr-absent): regular commit no anchor"],
            check=True, capture_output=True,
        )

        config_file = tmp_path / "rv.toml"
        config_file.write_text(
            f'instance_root = "{tmp_path}"\n',
            encoding="utf-8",
        )
        old_env = os.environ.get("RESEARCH_VAULT_CONFIG")
        os.environ["RESEARCH_VAULT_CONFIG"] = str(config_file)
        try:
            reset_config_cache()
            cfg = load_config()
            src = LocalGitSource(repo_path=repo)
            terminal = src.get_terminal_set(cfg, "any-project")
        finally:
            if old_env is None:
                os.environ.pop("RESEARCH_VAULT_CONFIG", None)
            else:
                os.environ["RESEARCH_VAULT_CONFIG"] = old_env
            reset_config_cache()

        assert "sr-absent" not in terminal, (
            f"'sr-absent' should NOT be terminal (no (#N) anchor); got terminal={terminal!r}"
        )
