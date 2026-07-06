"""test_gitutil.py — tests for the shared git-test utilities (tests/gitutil.py).

Verifies that the promoted fixtures work correctly as advertised:
  - tmp_git_repo pins initial branch to 'main' (not runner-dependent)
  - squash_merge_repo creates a squash-merged branch with the right subject
  - invoke_cli returns correct exit codes via the argv/dispatcher path
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.gitutil import squash_merge_repo, invoke_cli


# ---------------------------------------------------------------------------
# tmp_git_repo fixture tests
# ---------------------------------------------------------------------------

class TestTmpGitRepo:
    """Shared fixture: pinned-to-main hermetic git repo."""

    def test_initial_branch_is_main(self, tmp_git_repo: Path):
        """The fixture must use main (not runner-dependent master)."""
        r = subprocess.run(
            ["git", "-C", str(tmp_git_repo), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        assert r.stdout.strip() == "main"

    def test_has_initial_commit(self, tmp_git_repo: Path):
        """The repo has at least one commit (HEAD is resolvable)."""
        r = subprocess.run(
            ["git", "-C", str(tmp_git_repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        assert r.stdout.strip()  # non-empty SHA

    def test_user_identity_set(self, tmp_git_repo: Path):
        """User identity is set so commits work without global config."""
        email = subprocess.run(
            ["git", "-C", str(tmp_git_repo), "config", "user.email"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert email  # non-empty


# ---------------------------------------------------------------------------
# squash_merge_repo helper tests
# ---------------------------------------------------------------------------

class TestSquashMergeRepo:
    """squash_merge_repo produces the (# N) squash subject and deletes the branch."""

    def test_squash_subject_on_main(self, tmp_git_repo: Path):
        """After squash_merge_repo, main has a commit with the given subject."""
        subject = "feat(test): deliver work (#7)"
        squash_merge_repo(tmp_git_repo, "feat/test-sq", subject)

        r = subprocess.run(
            ["git", "-C", str(tmp_git_repo), "log", "--no-merges", "--format=%s", "-3"],
            capture_output=True, text=True, check=True,
        )
        subjects = r.stdout.strip().splitlines()
        assert subject in subjects, (
            f"Expected squash subject in log, got: {subjects}"
        )

    def test_branch_deleted_after_squash(self, tmp_git_repo: Path):
        """The source branch is deleted after squash-merge (as GitHub does)."""
        squash_merge_repo(tmp_git_repo, "feat/del-branch", "feat(x): work (#8)")

        r = subprocess.run(
            ["git", "-C", str(tmp_git_repo), "branch"],
            capture_output=True, text=True, check=True,
        )
        branches = r.stdout.strip().splitlines()
        branch_names = [b.strip().lstrip("* ") for b in branches]
        assert "feat/del-branch" not in branch_names, (
            f"Branch should be deleted after squash, but found: {branch_names}"
        )

    def test_main_is_current_branch(self, tmp_git_repo: Path):
        """After squash_merge_repo, HEAD is back on main."""
        squash_merge_repo(tmp_git_repo, "feat/return-main", "feat(y): work (#9)")
        r = subprocess.run(
            ["git", "-C", str(tmp_git_repo), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True,
        )
        assert r.stdout.strip() == "main"


# ---------------------------------------------------------------------------
# invoke_cli helper tests
# ---------------------------------------------------------------------------

class TestInvokeCli:
    """invoke_cli returns correct exit codes via the real argv/dispatcher path."""

    def test_help_flag_exits_0(self):
        """rv --version (or rv help) exits 0."""
        # 'rv help' exits 0 cleanly
        code = invoke_cli(["help"])
        assert code == 0

    def test_unknown_verb_exits_nonzero(self):
        """An unknown verb exits non-zero."""
        code = invoke_cli(["nonexistent-verb-xyzzy"])
        assert code != 0
