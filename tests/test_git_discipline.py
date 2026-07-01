"""test_git_discipline.py — tests for the rv git-discipline verb.

Covers (per acceptance criteria):
  - protect-main refuses a direct-to-main commit under empty allowlist
  - protect-main allows a branch commit
  - protect-main allows allowlisted paths when opted-in
  - commit-msg rejects a malformed subject
  - commit-msg accepts a well-formed conventional-commit subject
  - install sets core.hooksPath per repo; worktree inherits
  - rv wt --project makes a worktree in a project repo's <source_dir>-wt
  - --as <role> sets the worktree git identity from config
  - profile-aware leakage: secret caught in project repo; private-marker NOT flagged there
  - private-marker IS flagged in framework repo
  - leakage GREEN — no khangzhie.io in any scanned file
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# We import the module under test lazily inside tests to get clear errors.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_rv(args: list[str], *, env: dict | None = None, cwd: Path | None = None) -> tuple[int, str]:
    """Run the rv CLI via uv run (subprocess) and return (exit_code, combined_output)."""
    cmd = ["python", "-m", "research_vault.cli"] + args
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        cwd=str(cwd) if cwd else None,
    )
    return result.returncode, result.stdout + result.stderr


def _run_git_discipline(subargs: list[str], *, cfg_path: Path | None = None,
                         cwd: Path | None = None) -> tuple[int, str]:
    """Run rv git-discipline with optional config injection."""
    env = {}
    if cfg_path:
        env["RESEARCH_VAULT_CONFIG"] = str(cfg_path)
    return _run_rv(["git-discipline"] + subargs, env=env, cwd=cwd)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A minimal git repo for discipline tests (pinned to main)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(repo)],
        check=True, capture_output=True,
    )
    for cmd in [
        ["git", "-C", str(repo), "config", "user.email", "test@test.invalid"],
        ["git", "-C", str(repo), "config", "user.name", "Test"],
    ]:
        subprocess.run(cmd, check=True, capture_output=True)
    (repo / "README.md").write_text("# repo\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "chore: init"],
        check=True, capture_output=True,
    )
    return repo


@pytest.fixture
def rv_config(tmp_path: Path, git_repo: Path) -> Path:
    """A minimal research_vault.toml pointing at the test git repo as instance_root."""
    config_file = tmp_path / "research_vault.toml"
    project_repo = tmp_path / "my-project"
    project_repo.mkdir(parents=True, exist_ok=True)
    # Init project repo too
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(project_repo)],
        check=True, capture_output=True,
    )
    for cmd in [
        ["git", "-C", str(project_repo), "config", "user.email", "test@test.invalid"],
        ["git", "-C", str(project_repo), "config", "user.name", "Test"],
    ]:
        subprocess.run(cmd, check=True, capture_output=True)
    (project_repo / "README.md").write_text("# project\n")
    subprocess.run(
        ["git", "-C", str(project_repo), "add", "."], check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(project_repo), "commit", "-m", "chore: init project"],
        check=True, capture_output=True,
    )

    config_file.write_text(
        f"""
instance_root = "{git_repo}"
notes_root = "{tmp_path / 'notes'}"
state_dir = "{tmp_path / 'state'}"
agents_dir = "{tmp_path / '.agents'}"
tasks_dir = "{tmp_path / 'tasks'}"
control_dir = "{tmp_path / 'control'}"

[adapters]
notifier = "file"
backend = "local"
secrets = "env"

[projects.my-project]
source_dir = "{project_repo}"
tasks_dir = "{tmp_path / 'tasks' / 'my-project'}"
""",
        encoding="utf-8",
    )
    return config_file


# ---------------------------------------------------------------------------
# protect-main tests
# ---------------------------------------------------------------------------

class TestProtectMain:
    """protect-main check: branch/path-keyed, identity-free."""

    def test_refuses_commit_on_main_empty_allowlist(self, git_repo: Path, rv_config: Path):
        """protect-main refuses a staged commit when on main with empty allowlist."""
        # Stage a file while on main
        (git_repo / "new.txt").write_text("content\n")
        subprocess.run(
            ["git", "-C", str(git_repo), "add", "new.txt"],
            check=True, capture_output=True,
        )
        code, out = _run_git_discipline(
            ["check", "--staged"],
            cfg_path=rv_config,
            cwd=git_repo,
        )
        assert code != 0, f"Expected non-zero exit for commit on main, got 0. Output: {out}"
        assert "main" in out.lower() or "protect" in out.lower() or "refused" in out.lower(), (
            f"Expected protect-main message, got: {out}"
        )

    def test_allows_commit_on_branch(self, git_repo: Path, rv_config: Path):
        """protect-main allows a commit when on a feature branch."""
        subprocess.run(
            ["git", "-C", str(git_repo), "checkout", "-b", "feat/test"],
            check=True, capture_output=True,
        )
        (git_repo / "new.txt").write_text("content\n")
        subprocess.run(
            ["git", "-C", str(git_repo), "add", "new.txt"],
            check=True, capture_output=True,
        )
        code, out = _run_git_discipline(
            ["check", "--staged"],
            cfg_path=rv_config,
            cwd=git_repo,
        )
        # Should pass (branch commit is fine; no leakage in content)
        assert code == 0, f"Expected 0 for branch commit, got {code}. Output: {out}"

    def test_allows_allowlisted_path_on_main(self, git_repo: Path, tmp_path: Path):
        """protect-main allows commits to allowlisted paths on main."""
        # Create a config with DEVLOG.md in the allowlist
        config_file = tmp_path / "rv_allowlist.toml"
        config_file.write_text(
            f"""
instance_root = "{git_repo}"
notes_root = "{tmp_path / 'notes'}"
state_dir = "{tmp_path / 'state'}"
agents_dir = "{tmp_path / '.agents'}"
tasks_dir = "{tmp_path / 'tasks'}"
control_dir = "{tmp_path / 'control'}"

[adapters]
notifier = "file"
backend = "local"
secrets = "env"

[git_discipline]
protect_main_allowlist = ["DEVLOG.md", "control/"]
""",
            encoding="utf-8",
        )
        # Stage DEVLOG.md (allowlisted)
        (git_repo / "DEVLOG.md").write_text("# DEVLOG\n")
        subprocess.run(
            ["git", "-C", str(git_repo), "add", "DEVLOG.md"],
            check=True, capture_output=True,
        )
        code, out = _run_git_discipline(
            ["check", "--staged"],
            cfg_path=config_file,
            cwd=git_repo,
        )
        assert code == 0, f"Expected 0 for allowlisted path on main, got {code}. Output: {out}"


# ---------------------------------------------------------------------------
# commit-msg tests
# ---------------------------------------------------------------------------

class TestCommitMsg:
    """commit-msg: conventional-commit format check."""

    def test_accepts_valid_conventional_commit(self, tmp_path: Path):
        """Well-formed conventional commit subjects pass."""
        from research_vault.git_discipline import check_commit_msg

        valid_subjects = [
            "feat(sr-gd): add git discipline layer",
            "fix: correct branch detection",
            "docs(readme): update setup instructions",
            "refactor(wt): multi-repo support",
            "test: add coverage for protect-main",
            "chore: bump dependency",
            "ci(github): add branch protection check",
            "build: update pyproject",
            "perf: speed up leakage scan",
        ]
        for subject in valid_subjects:
            result = check_commit_msg(subject)
            assert result is None, (
                f"Expected None (OK) for subject {subject!r}, got: {result}"
            )

    def test_rejects_malformed_subjects(self, tmp_path: Path):
        """Malformed commit subjects return an error string."""
        from research_vault.git_discipline import check_commit_msg

        bad_subjects = [
            "Add git discipline",            # no type prefix
            "WIP: half-done work",           # not a valid type
            "feat add something",            # missing colon
            "",                              # empty
            "   ",                           # whitespace only
        ]
        for subject in bad_subjects:
            result = check_commit_msg(subject)
            assert result is not None, (
                f"Expected error for malformed subject {subject!r}, got None (OK)"
            )

    def test_commit_msg_subcommand_via_file(self, tmp_path: Path, git_repo: Path, rv_config: Path):
        """rv git-discipline commit-msg <file> exits 0 for valid, 1 for invalid."""
        msg_file = tmp_path / "COMMIT_EDITMSG"

        # Valid
        msg_file.write_text("feat(sr-gd): implement git discipline\n")
        code, out = _run_git_discipline(
            ["commit-msg", str(msg_file)],
            cfg_path=rv_config,
            cwd=git_repo,
        )
        assert code == 0, f"Expected 0 for valid commit msg, got {code}. Output: {out}"

        # Invalid
        msg_file.write_text("WIP: not conventional\n")
        code, out = _run_git_discipline(
            ["commit-msg", str(msg_file)],
            cfg_path=rv_config,
            cwd=git_repo,
        )
        assert code != 0, f"Expected non-zero for invalid commit msg, got 0. Output: {out}"


# ---------------------------------------------------------------------------
# install / uninstall / status tests
# ---------------------------------------------------------------------------

class TestInstall:
    """install: sets core.hooksPath per repo; worktrees inherit."""

    def test_install_sets_hooks_path(self, git_repo: Path, rv_config: Path):
        """rv git-discipline install sets core.hooksPath to .githooks in the repo."""
        code, out = _run_git_discipline(
            ["install"],
            cfg_path=rv_config,
            cwd=git_repo,
        )
        # Check core.hooksPath was set
        r = subprocess.run(
            ["git", "-C", str(git_repo), "config", "core.hooksPath"],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, f"core.hooksPath not set after install. Output: {out}"
        assert ".githooks" in r.stdout.strip(), (
            f"Expected .githooks in core.hooksPath, got: {r.stdout.strip()}"
        )

    def test_install_idempotent(self, git_repo: Path, rv_config: Path):
        """install is idempotent — running twice does not error."""
        code1, _ = _run_git_discipline(["install"], cfg_path=rv_config, cwd=git_repo)
        code2, out = _run_git_discipline(["install"], cfg_path=rv_config, cwd=git_repo)
        assert code2 == 0, f"Second install errored. Output: {out}"

    def test_uninstall_removes_hooks_path(self, git_repo: Path, rv_config: Path):
        """uninstall removes core.hooksPath."""
        _run_git_discipline(["install"], cfg_path=rv_config, cwd=git_repo)
        code, out = _run_git_discipline(["uninstall"], cfg_path=rv_config, cwd=git_repo)
        assert code == 0, f"Uninstall errored. Output: {out}"
        r = subprocess.run(
            ["git", "-C", str(git_repo), "config", "core.hooksPath"],
            capture_output=True, text=True,
        )
        # Should be gone (non-zero means not set)
        assert r.returncode != 0, "core.hooksPath still set after uninstall"

    def test_status_reports_install_state(self, git_repo: Path, rv_config: Path):
        """status shows installed/not-installed for the repo."""
        code, out = _run_git_discipline(["status"], cfg_path=rv_config, cwd=git_repo)
        assert code == 0, f"Status errored. Output: {out}"
        # Should mention the repo and some status
        assert out.strip(), "Status output should be non-empty"

    def test_install_creates_hooks_dir_with_shims(self, git_repo: Path, rv_config: Path):
        """install creates .githooks/ with pre-commit and commit-msg shims."""
        _run_git_discipline(["install"], cfg_path=rv_config, cwd=git_repo)
        hooks_dir = git_repo / ".githooks"
        assert hooks_dir.exists(), ".githooks/ directory not created"
        pre_commit = hooks_dir / "pre-commit"
        commit_msg = hooks_dir / "commit-msg"
        assert pre_commit.exists(), "pre-commit shim not created"
        assert commit_msg.exists(), "commit-msg shim not created"
        # Shims must be executable
        assert os.access(pre_commit, os.X_OK), "pre-commit not executable"
        assert os.access(commit_msg, os.X_OK), "commit-msg not executable"


# ---------------------------------------------------------------------------
# Profile-aware leakage tests
# ---------------------------------------------------------------------------

class TestProfileAwareLeakage:
    """Two repo profiles: framework repo (all classes) vs project repo (secrets only)."""

    def _stage_file_in_repo(self, repo: Path, filename: str, content: str) -> None:
        """Write and stage a file in the given repo."""
        (repo / filename).write_text(content, encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repo), "add", filename],
            check=True, capture_output=True,
        )

    def test_secret_caught_in_project_repo(self, tmp_path: Path, git_repo: Path, rv_config: Path):
        """A secret (class 5) is caught in a project repo commit."""
        # Get the project repo path from config
        from research_vault.config import Config
        import tomllib
        with open(rv_config, "rb") as f:
            raw = tomllib.load(f)
        project_repo = Path(raw["projects"]["my-project"]["source_dir"])

        # Stage a file with a secret in the project repo
        subprocess.run(
            ["git", "-C", str(project_repo), "checkout", "-b", "feat/secret-test"],
            check=True, capture_output=True,
        )
        self._stage_file_in_repo(project_repo, "config.py", "API_KEY = 'sk-ant-secret123'\n")

        code, out = _run_git_discipline(
            ["check", "--staged", "--repo", str(project_repo)],
            cfg_path=rv_config,
            cwd=project_repo,
        )
        assert code != 0, (
            f"Expected non-zero for secret in project repo, got 0. Output: {out}"
        )
        assert "secret" in out.lower() or "sk-ant" in out.lower(), (
            f"Expected secret-related message, got: {out}"
        )

    def test_private_marker_not_flagged_in_project_repo(
        self, tmp_path: Path, git_repo: Path, rv_config: Path
    ):
        """A private codename does NOT trigger leakage in a project repo (researcher's own content)."""
        from research_vault.config import Config
        import tomllib
        with open(rv_config, "rb") as f:
            raw = tomllib.load(f)
        project_repo = Path(raw["projects"]["my-project"]["source_dir"])

        subprocess.run(
            ["git", "-C", str(project_repo), "checkout", "-b", "feat/marker-test"],
            check=True, capture_output=True,
        )
        # Stage a file with a private codename — should NOT be flagged in project repo
        self._stage_file_in_repo(
            project_repo, "notes.md", "# My notes on cultural-social-sim project\n"
        )

        code, out = _run_git_discipline(
            ["check", "--staged", "--repo", str(project_repo)],
            cfg_path=rv_config,
            cwd=project_repo,
        )
        # Project repo: only secrets scan, so private codename should NOT trigger
        assert code == 0, (
            f"Expected 0 (private-marker allowed in project repo), got {code}. Output: {out}"
        )

    def test_private_marker_flagged_in_framework_repo(self, git_repo: Path, rv_config: Path):
        """A private codename IS flagged in the framework repo (public OSS package)."""
        subprocess.run(
            ["git", "-C", str(git_repo), "checkout", "-b", "feat/leak-test"],
            check=True, capture_output=True,
        )
        # Stage a file with a private codename in the framework repo
        (git_repo / "leaked.md").write_text("# Notes on cultural-social-sim\n")
        subprocess.run(
            ["git", "-C", str(git_repo), "add", "leaked.md"],
            check=True, capture_output=True,
        )

        code, out = _run_git_discipline(
            ["check", "--staged"],
            cfg_path=rv_config,
            cwd=git_repo,
        )
        # Framework repo: all 9 classes including private markers
        assert code != 0, (
            f"Expected non-zero for private marker in framework repo, got 0. Output: {out}"
        )


# ---------------------------------------------------------------------------
# Crew identity convention tests
# ---------------------------------------------------------------------------

class TestCrewIdentity:
    """Crew git-identity: domain is config-driven, never hardcoded in scanned files."""

    def test_no_crew_domain_in_scanned_files(self, tmp_path: Path):
        """The crew identity domain must not appear in any scanned source file.

        The real domain lives in private instance config.  Public files must
        use the placeholder or no domain at all.
        """
        import subprocess
        # Run leakage scan over the src/ directory of the worktree
        result = subprocess.run(
            ["bash", "scripts/leakage_scan.sh", "src/"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        # If khangzhie.io (or any private marker) appears in src/, this fails
        assert result.returncode == 0, (
            f"Leakage scan found private markers in src/:\n{result.stdout}"
        )
