"""test_config.py — tests for the config plane SSOT.

Verifies: config loading, multi-project registry, path resolution,
defaults, env override, and error handling. All hermetic (tmp_path).
"""

import os
import pytest
from pathlib import Path
from research_vault.config import load_config, reset_config_cache, resolve_repo_root


def test_defaults_without_config_file(tmp_path, monkeypatch):
    """load_config() without a config file uses cwd-relative defaults."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RESEARCH_VAULT_CONFIG", raising=False)
    reset_config_cache()

    cfg = load_config(reload=True)
    assert cfg.instance_root == tmp_path
    assert cfg.notes_root == tmp_path / "notes"
    assert cfg.state_dir == tmp_path / "state"
    assert cfg.tasks_dir == tmp_path / "tasks"
    assert cfg.control_dir == tmp_path / "control"
    assert cfg.projects == {}


def test_env_override_loads_toml(tmp_path, monkeypatch):
    """RESEARCH_VAULT_CONFIG env var takes priority over cwd search."""
    config_file = tmp_path / "my_config.toml"
    projects_dir = tmp_path / "projects"
    config_file.write_text(
        f"""
instance_root = "{tmp_path}"
notes_root = "{tmp_path / 'notes'}"
state_dir = "{tmp_path / 'state'}"
agents_dir = "{tmp_path / '.agents'}"
tasks_dir = "{tmp_path / 'tasks'}"
control_dir = "{tmp_path / 'control'}"

[projects.my-project]
source_dir = "{projects_dir / 'my-project'}"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(config_file))
    reset_config_cache()

    cfg = load_config(reload=True)
    assert "my-project" in cfg.projects
    assert cfg.project("my-project")["source_dir"] == str(projects_dir / "my-project")


def test_multi_project_registry(tmp_instance):
    """Config loads both demo projects from the multi-project registry."""
    cfg = load_config(reload=True)
    assert "demo-research" in cfg.projects
    assert "demo-litreview" in cfg.projects
    assert len(cfg.all_project_slugs()) == 2


def test_project_unknown_raises_keyerror(tmp_instance):
    """Config.project() raises KeyError for an unknown slug."""
    cfg = load_config(reload=True)
    with pytest.raises(KeyError, match="Unknown project"):
        cfg.project("nonexistent-slug")


def test_project_tasks_dir_resolution(tmp_instance):
    """Config.project_tasks_dir() returns the configured tasks directory."""
    cfg = load_config(reload=True)
    tasks = cfg.project_tasks_dir("demo-research")
    assert "demo-research" in str(tasks)


def test_project_control_file_resolution(tmp_instance):
    """Config.project_control_file() returns control/<project>.md by default."""
    cfg = load_config(reload=True)
    control = cfg.project_control_file("demo-research")
    assert control.name == "demo-research.md"
    assert "control" in str(control)


def test_project_notes_dir_resolution(tmp_instance):
    """Config.project_notes_dir() returns the configured source_dir."""
    cfg = load_config(reload=True)
    notes = cfg.project_notes_dir("demo-research")
    assert "demo-research" in str(notes)


def test_env_missing_file_raises(tmp_path, monkeypatch):
    """RESEARCH_VAULT_CONFIG pointing to a nonexistent file raises FileNotFoundError."""
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(tmp_path / "does_not_exist.toml"))
    reset_config_cache()
    with pytest.raises(FileNotFoundError, match="does not exist"):
        load_config(reload=True)


def test_adapters_defaults(tmp_instance):
    """Adapters section is loaded from the config."""
    cfg = load_config(reload=True)
    assert cfg.adapters["notifier"] == "file"
    assert cfg.adapters["backend"] == "local"
    assert cfg.adapters["secrets"] == "env"


def test_minimal_config_derives_paths_from_instance_root(tmp_path, monkeypatch):
    """A config that sets ONLY instance_root must resolve all derived paths under it.

    This exercises the minimal/defaults path (not the all-keys-set path that conftest
    uses). The instance_root-as-SSOT guarantee: tasks_dir, control_dir, notes_root,
    and state_dir must all be children of instance_root, never of cwd().
    """
    # Use a different dir for cwd to prove paths don't anchor on cwd
    cwd_dir = tmp_path / "cwd_unrelated"
    cwd_dir.mkdir()
    monkeypatch.chdir(cwd_dir)

    instance_root = tmp_path / "my_instance"
    instance_root.mkdir()

    config_file = tmp_path / "research_vault.toml"
    config_file.write_text(
        f'instance_root = "{instance_root}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(config_file))
    reset_config_cache()

    cfg = load_config(reload=True)

    assert cfg.instance_root == instance_root
    # All derived paths must live under instance_root — not under cwd_unrelated
    assert cfg.tasks_dir.is_relative_to(instance_root), (
        f"tasks_dir {cfg.tasks_dir} is not under instance_root {instance_root}"
    )
    assert cfg.control_dir.is_relative_to(instance_root), (
        f"control_dir {cfg.control_dir} is not under instance_root {instance_root}"
    )
    assert cfg.notes_root.is_relative_to(instance_root), (
        f"notes_root {cfg.notes_root} is not under instance_root {instance_root}"
    )
    assert cfg.state_dir.is_relative_to(instance_root), (
        f"state_dir {cfg.state_dir} is not under instance_root {instance_root}"
    )


# ---------------------------------------------------------------------------
# resolve_repo_root — repo-root-is-vault (CS convention) vs flat/legacy
# ---------------------------------------------------------------------------

class TestResolveRepoRoot:
    """The csb C6 backfill surfaced: `rv orient`/`rv status` resolved
    pointers.md/architecture.md relative to source_dir even when the
    CS-project convention (doctrine/project-structure.md) places them at
    the repo root (source_dir.parent, when source_dir = <repo>/notes)."""

    def test_cs_convention_source_dir_ends_in_notes_returns_parent(self, tmp_path):
        repo = tmp_path / "my-project"
        source_dir = repo / "notes"
        assert resolve_repo_root(source_dir) == repo

    def test_flat_legacy_source_dir_is_repo_root_returns_itself(self, tmp_path):
        repo = tmp_path / "my-project"
        assert resolve_repo_root(repo) == repo

    def test_accepts_str_or_path(self, tmp_path):
        repo = tmp_path / "my-project"
        source_dir = repo / "notes"
        assert resolve_repo_root(str(source_dir)) == repo

    def test_project_repo_root_wires_through_config(self, tmp_path, monkeypatch):
        repo = tmp_path / "cs-demo"
        notes = repo / "notes"
        notes.mkdir(parents=True)
        config_file = tmp_path / "research_vault.toml"
        config_file.write_text(
            f"""
instance_root = "{tmp_path}"

[projects.cs-demo]
source_dir = "{notes}"
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(config_file))
        reset_config_cache()
        cfg = load_config(reload=True)
        assert cfg.project_repo_root("cs-demo") == repo
