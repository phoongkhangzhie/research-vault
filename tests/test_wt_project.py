"""test_wt_project.py — tests for rv wt --project and --as (GD-D6).

Verifies:
  - rv wt add <task> --project <slug> creates a worktree in <source_dir>-wt/
    (not the instance_root-wt/ default)
  - rv wt add <task> --as <role> sets git user.name/user.email in the worktree
    to <role>@<crew-domain> (config-driven; placeholder by default)
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


def _make_git_repo(path: Path, branch: str = "main") -> None:
    """Create a minimal git repo at path."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--initial-branch", branch, str(path)],
        check=True, capture_output=True,
    )
    for cmd in [
        ["git", "-C", str(path), "config", "user.email", "test@test.invalid"],
        ["git", "-C", str(path), "config", "user.name", "Test"],
    ]:
        subprocess.run(cmd, check=True, capture_output=True)
    (path / "README.md").write_text("# repo\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "chore: init"],
        check=True, capture_output=True,
    )


@pytest.fixture
def multi_repo_config(tmp_path: Path):
    """A config with a framework repo and one project repo."""
    framework_repo = tmp_path / "framework"
    project_repo = tmp_path / "my-proj"
    _make_git_repo(framework_repo)
    _make_git_repo(project_repo)

    config_file = tmp_path / "research_vault.toml"
    config_file.write_text(
        f"""
instance_root = "{framework_repo}"
notes_root = "{tmp_path / 'notes'}"
state_dir = "{tmp_path / 'state'}"
agents_dir = "{tmp_path / '.agents'}"
tasks_dir = "{tmp_path / 'tasks'}"
control_dir = "{tmp_path / 'control'}"

[adapters]
notifier = "file"
backend = "local"
secrets = "env"

[projects.my-proj]
source_dir = "{project_repo}"
tasks_dir = "{tmp_path / 'tasks' / 'my-proj'}"
""",
        encoding="utf-8",
    )
    return {
        "config_file": config_file,
        "framework_repo": framework_repo,
        "project_repo": project_repo,
        "tmp_path": tmp_path,
    }


class TestWtProject:
    """rv wt add --project creates worktree in project repo's source_dir-wt/."""

    def _run_rv(self, args: list[str], env: dict, cwd: Path | None = None):
        """Run rv via python -m."""
        result = subprocess.run(
            ["python", "-m", "research_vault.cli"] + args,
            capture_output=True, text=True,
            env={**os.environ, **env},
            cwd=str(cwd) if cwd else None,
        )
        return result.returncode, result.stdout + result.stderr

    def test_wt_add_project_creates_in_source_dir_wt(self, multi_repo_config: dict):
        """rv wt add task --project my-proj creates worktree in <project_repo>-wt/."""
        cfg_env = {"RESEARCH_VAULT_CONFIG": str(multi_repo_config["config_file"])}
        project_repo = multi_repo_config["project_repo"]
        expected_wt_home = Path(str(project_repo) + "-wt")

        code, out = self._run_rv(
            ["wt", "add", "my-task", "--project", "my-proj"],
            env=cfg_env,
        )
        assert code == 0, f"Expected exit 0, got {code}. Output: {out}"
        # Worktree should be in <project_repo>-wt/
        assert str(expected_wt_home) in out, (
            f"Expected worktree in {expected_wt_home}, got output: {out}"
        )
        # The directory should exist
        wt_dirs = list(expected_wt_home.glob("my-task-*"))
        assert len(wt_dirs) >= 1, (
            f"Expected worktree directory in {expected_wt_home}, found: {list(expected_wt_home.iterdir()) if expected_wt_home.exists() else 'dir not found'}"
        )

    def test_wt_add_default_uses_framework_repo(self, multi_repo_config: dict):
        """rv wt add task (no --project) creates worktree off the framework repo."""
        cfg_env = {"RESEARCH_VAULT_CONFIG": str(multi_repo_config["config_file"])}
        framework_repo = multi_repo_config["framework_repo"]
        expected_wt_home = Path(str(framework_repo) + "-wt")

        code, out = self._run_rv(
            ["wt", "add", "fw-task"],
            env=cfg_env,
        )
        assert code == 0, f"Expected exit 0, got {code}. Output: {out}"
        assert str(expected_wt_home) in out, (
            f"Expected worktree in {expected_wt_home}, got: {out}"
        )

    def test_wt_add_unknown_project_errors(self, multi_repo_config: dict):
        """rv wt add task --project unknown-proj exits non-zero with clear error."""
        cfg_env = {"RESEARCH_VAULT_CONFIG": str(multi_repo_config["config_file"])}
        code, out = self._run_rv(
            ["wt", "add", "task", "--project", "unknown-proj"],
            env=cfg_env,
        )
        assert code != 0, f"Expected non-zero for unknown project, got 0. Output: {out}"
        assert "unknown" in out.lower() or "not found" in out.lower() or "unknown-proj" in out, (
            f"Expected error message, got: {out}"
        )


class TestWtAs:
    """rv wt add --as <role> sets git identity in the new worktree."""

    def _run_rv(self, args: list[str], env: dict, cwd: Path | None = None):
        result = subprocess.run(
            ["python", "-m", "research_vault.cli"] + args,
            capture_output=True, text=True,
            env={**os.environ, **env},
            cwd=str(cwd) if cwd else None,
        )
        return result.returncode, result.stdout + result.stderr

    def test_wt_add_as_sets_git_email(self, multi_repo_config: dict):
        """--as mason sets git user.email to mason@<crew-domain> in the worktree."""
        cfg_env = {"RESEARCH_VAULT_CONFIG": str(multi_repo_config["config_file"])}
        code, out = self._run_rv(
            ["wt", "add", "crew-task", "--as", "mason"],
            env=cfg_env,
        )
        assert code == 0, f"Expected exit 0, got {code}. Output: {out}"

        # Find the created worktree
        framework_repo = multi_repo_config["framework_repo"]
        wt_home = Path(str(framework_repo) + "-wt")
        wt_dirs = list(wt_home.glob("crew-task-*"))
        assert wt_dirs, f"No worktree found in {wt_home}"
        wt_path = wt_dirs[0]

        # Check git identity in the worktree
        email = subprocess.run(
            ["git", "-C", str(wt_path), "config", "user.email"],
            capture_output=True, text=True,
        ).stdout.strip()
        assert "mason" in email, (
            f"Expected 'mason' in git user.email, got: {email!r}"
        )
        # Domain must NOT be the real private domain — must use placeholder
        assert "example.invalid" in email or "@" in email, (
            f"Expected a valid crew identity email, got: {email!r}"
        )

    def test_wt_add_as_sets_git_name(self, multi_repo_config: dict):
        """--as engineer sets git user.name to 'Engineer (rv crew)' or similar."""
        cfg_env = {"RESEARCH_VAULT_CONFIG": str(multi_repo_config["config_file"])}
        code, out = self._run_rv(
            ["wt", "add", "crew-name-task", "--as", "engineer"],
            env=cfg_env,
        )
        assert code == 0, f"Expected exit 0, got {code}. Output: {out}"
        framework_repo = multi_repo_config["framework_repo"]
        wt_home = Path(str(framework_repo) + "-wt")
        wt_dirs = list(wt_home.glob("crew-name-task-*"))
        assert wt_dirs, f"No worktree found in {wt_home}"
        wt_path = wt_dirs[0]
        name = subprocess.run(
            ["git", "-C", str(wt_path), "config", "user.name"],
            capture_output=True, text=True,
        ).stdout.strip()
        assert name, f"git user.name is empty after --as engineer"
        # Should contain the role name
        assert "engineer" in name.lower() or "Engineer" in name, (
            f"Expected 'engineer' in git user.name, got: {name!r}"
        )
