"""test_gitlib.py — tests for the shared gitlib helpers.

Tests squash_terminal_ids using real squash-merge repos via the shared
gitutil fixtures (promoted from SR-CP).  These are the same test patterns
that SR-CP used to verify the Tertiary signal — now testing the shared
single-implementation function that both git-health and control-reconcile
import.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from research_vault.gitlib import squash_terminal_ids
from tests.gitutil import squash_merge_repo


class TestSquashTerminalIds:
    """squash_terminal_ids: Signal D — squash-merged branches via (#N) anchor."""

    def test_no_squash_commits_returns_empty(self, tmp_git_repo: Path):
        """A fresh repo with no squash commits returns empty set."""
        ids = squash_terminal_ids(tmp_git_repo)
        assert ids == frozenset()

    def test_detects_squash_merged_branch(self, tmp_git_repo: Path):
        """After squash-merging feat/sr-gd, 'sr-gd' appears in the terminal set."""
        squash_merge_repo(
            tmp_git_repo,
            "feat/sr-gd",
            "feat(sr-gd): implement git discipline (#11)",
        )
        ids = squash_terminal_ids(tmp_git_repo)
        assert "sr-gd" in ids, f"Expected 'sr-gd' in terminal ids, got: {ids}"

    def test_does_not_detect_unmerged_branch(self, tmp_git_repo: Path):
        """A branch that is created but NOT squash-merged is not in the terminal set."""
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "checkout", "-b", "feat/sr-live"],
            check=True, capture_output=True,
        )
        ids = squash_terminal_ids(tmp_git_repo)
        assert "sr-live" not in ids, f"Unexpected 'sr-live' in terminal ids: {ids}"

    def test_does_not_detect_commit_without_pr_anchor(self, tmp_git_repo: Path):
        """A commit subject without the (#N) anchor is NOT treated as squash-merged."""
        # Direct commit to main, no (#N)
        (tmp_git_repo / "direct.txt").write_text("direct\n")
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "add", "."],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "commit",
             "-m", "feat(sr-noanch): work without pr anchor"],
            check=True, capture_output=True,
        )
        ids = squash_terminal_ids(tmp_git_repo)
        assert "sr-noanch" not in ids, (
            f"Commit without (#N) should not appear in terminal ids: {ids}"
        )

    def test_multiple_squash_merges(self, tmp_git_repo: Path):
        """Multiple squash-merged branches are all detected."""
        squash_merge_repo(tmp_git_repo, "feat/sr-a", "feat(sr-a): part A (#21)")
        squash_merge_repo(tmp_git_repo, "feat/sr-b", "feat(sr-b): part B (#22)")
        ids = squash_terminal_ids(tmp_git_repo)
        assert "sr-a" in ids
        assert "sr-b" in ids

    def test_nonexistent_repo_returns_empty(self, tmp_path: Path):
        """A path that doesn't exist returns empty frozenset (no crash)."""
        ids = squash_terminal_ids(tmp_path / "nonexistent")
        assert ids == frozenset()
