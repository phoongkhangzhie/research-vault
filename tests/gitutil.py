"""gitutil.py — shared git-test fixtures for Research Vault tests.

Promoted from SR-CP (test_sr_cp.py) so that every git-merge-aware test module
reuses the same pinned, hermetic fixtures instead of re-rolling broken variants.

IMPORT: ``from tests.gitutil import tmp_git_repo, squash_merge_repo, invoke_cli``

Fixtures
--------
tmp_git_repo(tmp_path)
    A minimal, pinned-to-main git repo with one initial commit. Pytest fixture.
    Always runs ``git init --initial-branch=main`` so tests pass on CI runners
    where ``init.defaultBranch`` defaults to ``master``.

squash_merge_repo(repo, branch_name, subject)
    Helper function (not a fixture) that:
      1. Creates a branch from ``main``.
      2. Adds one commit with a trivial file change.
      3. Checks out ``main``.
      4. Does ``git merge --squash <branch>``.
      5. Commits with the supplied *subject* (caller appends ``(#N)`` to get
         a GitHub-style squash subject).
      6. Deletes the branch (as GitHub does after squash-and-merge).
    Returns the repo path (pass-through).

invoke_cli(args, *, env=None)
    Invoke the ``rv`` CLI dispatcher via ``research_vault.cli.main(args)``
    and return the integer exit code. Covers the argv/dispatcher path so
    exit-code contracts are tested end-to-end (not just the helper function).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence

import pytest


# ---------------------------------------------------------------------------
# tmp_git_repo — pinned, hermetic git repo fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """A minimal git repo pinned to branch ``main``.

    Pinned: always passes ``--initial-branch=main`` so the fixture is not
    sensitive to the CI runner's ``init.defaultBranch`` setting — the source
    of the SR-CP CI-red bug (passes locally on macOS 'main', fails on a
    GitHub runner with 'master').
    """
    repo = tmp_path / "git-repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(repo)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.invalid"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test Fixture"],
        check=True, capture_output=True,
    )
    # Initial commit so HEAD exists
    (repo / "README.md").write_text("# test repo\n")
    subprocess.run(
        ["git", "-C", str(repo), "add", "."],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "chore: init"],
        check=True, capture_output=True,
    )
    return repo


# ---------------------------------------------------------------------------
# squash_merge_repo — create a squash-merged branch (GitHub model)
# ---------------------------------------------------------------------------

def squash_merge_repo(
    repo: Path,
    branch_name: str,
    subject: str,
    *,
    filename: str = "squash.txt",
) -> Path:
    """Squash-merge a branch into main, then delete it.

    Models GitHub's squash-and-merge button:
      - Creates and checks out ``branch_name``.
      - Adds a file commit.
      - Returns to main.
      - ``git merge --squash <branch>``.
      - Commits with *subject* (caller should append ``(#N)`` for a real
        GitHub squash subject).
      - Deletes the branch (as GitHub auto-deletes merged branches).

    The repo must already have at least one commit on main (i.e. use the
    ``tmp_git_repo`` fixture as a base).

    Returns the repo path for chaining.
    """
    _git = lambda args: subprocess.run(
        ["git", "-C", str(repo)] + args,
        check=True, capture_output=True,
    )
    _git(["checkout", "-b", branch_name])
    (repo / filename).write_text(f"content for {branch_name}\n")
    _git(["add", "."])
    _git(["commit", "-m", f"wip: {branch_name} work"])
    _git(["checkout", "main"])
    _git(["merge", "--squash", branch_name])
    _git(["commit", "-m", subject])
    _git(["branch", "-D", branch_name])
    return repo


# ---------------------------------------------------------------------------
# invoke_cli — test the rv argv/dispatcher path
# ---------------------------------------------------------------------------

def invoke_cli(args: Sequence[str], *, env: dict | None = None) -> int:
    """Invoke the rv CLI dispatcher and return the integer exit code.

    Tests exit-code contracts end-to-end (argv → dispatcher → verb → exit code),
    not just the underlying helper function. This is the pattern the SR-CP
    TestReconcileExitCode suite established — generalised here for reuse.

    Args:
        args: CLI arguments (without the leading ``rv``), e.g.
              ``["control", "demo", "reconcile"]``.
        env:  Optional env-var overrides applied to the test process (note:
              this function does NOT spawn a subprocess — it calls ``main()``
              directly; ``env`` is applied via ``os.environ`` temporarily).

    Returns:
        Integer exit code from ``main()``.
    """
    import os
    from research_vault.cli import main

    if env:
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            return main(list(args))
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    else:
        return main(list(args))
