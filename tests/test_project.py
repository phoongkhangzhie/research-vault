"""test_project.py — Tests for the ``rv project add`` + ``rv project list`` verbs.

Core invariants under test:
  1. Valid entry is schema-validated and written correctly.
  2. MINIMAL WRITE: the rest of the TOML file is byte-unchanged.
  3. IDEMPOTENT: duplicate name OR duplicate code raises a clear error.
  4. CANONICAL form: fixed field order in the written section (code, source_dir, roster).
  5. SR-1 forward-flag: roster and code appear in the written record.
  6. SR-XP: rv project list enumerates projects with real fields, no disclosure column.

All tests are hermetic: run in tmp_path, no real filesystem side-effects.
"""
import os
import sys
import tomllib
from pathlib import Path

import pytest

from research_vault.project import (
    _check_no_duplicate,
    _render_project_section,
    _validate_entry,
    cmd_add,
    cmd_list,
    run,
)
from research_vault.config import Config, reset_config_cache
import argparse


# ---------------------------------------------------------------------------
# Fixture: a minimal TOML config with one pre-existing project
# ---------------------------------------------------------------------------

@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    """Write a minimal research_vault.toml with one existing project."""
    config_path = tmp_path / "research_vault.toml"
    config_path.write_text(
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

[projects.existing-project]
code = "ep"
source_dir = "{tmp_path / 'existing'}"
roster = ["engineer"]
""",
        encoding="utf-8",
    )
    return config_path


# ---------------------------------------------------------------------------
# _validate_entry
# ---------------------------------------------------------------------------

def test_validate_entry_ok() -> None:
    # Should not raise
    _validate_entry("my-project", "mp", "/some/path", ["engineer"])


def test_validate_entry_invalid_name_leading_digit() -> None:
    with pytest.raises(ValueError, match="Invalid project name"):
        _validate_entry("1project", "mp", "/path", [])


def test_validate_entry_invalid_name_uppercase() -> None:
    with pytest.raises(ValueError, match="Invalid project name"):
        _validate_entry("MyProject", "mp", "/path", [])


def test_validate_entry_invalid_code() -> None:
    with pytest.raises(ValueError, match="Invalid project code"):
        _validate_entry("good-name", "BAD", "/path", [])


def test_validate_entry_empty_source_dir() -> None:
    with pytest.raises(ValueError, match="source_dir must not be empty"):
        _validate_entry("good-name", "gn", "   ", [])


def test_validate_entry_blank_roster_role() -> None:
    with pytest.raises(ValueError, match="Roster role name must not be blank"):
        _validate_entry("good-name", "gn", "/path", ["engineer", "  "])


# ---------------------------------------------------------------------------
# _check_no_duplicate
# ---------------------------------------------------------------------------

def test_check_no_duplicate_passes_when_empty() -> None:
    _check_no_duplicate({}, "new-project", "np")  # Should not raise


def test_check_no_duplicate_name_collision() -> None:
    existing = {"my-proj": {"code": "mp"}}
    with pytest.raises(ValueError, match="already exists"):
        _check_no_duplicate(existing, "my-proj", "something-else")


def test_check_no_duplicate_code_collision() -> None:
    existing = {"other-proj": {"code": "mp"}}
    with pytest.raises(ValueError, match="already in use"):
        _check_no_duplicate(existing, "new-name", "mp")


# ---------------------------------------------------------------------------
# _render_project_section — canonical form
# ---------------------------------------------------------------------------

def test_render_project_section_field_order() -> None:
    section = _render_project_section("my-proj", "mp", "/data/my-proj", ["engineer", "researcher"])
    # Section header must appear first
    assert "[projects.my-proj]" in section
    # Field order: code, source_dir, roster (no disclosure)
    idx_code = section.index("code =")
    idx_src = section.index("source_dir =")
    idx_roster = section.index("roster =")
    assert idx_code < idx_src < idx_roster, \
        "Field order must be: code → source_dir → roster"
    assert "disclosure" not in section, "disclosure must not appear in rendered section"


def test_render_project_section_has_forward_flags() -> None:
    """roster and code must be in the rendered section (SR-1 forward-flag)."""
    section = _render_project_section("proj", "p", "/path", ["engineer"])
    assert 'code = "p"' in section
    assert 'roster = ["engineer"]' in section
    assert "disclosure" not in section


def test_render_project_section_empty_roster() -> None:
    section = _render_project_section("proj", "p", "/path", [])
    assert "roster = []" in section


def test_render_project_section_toml_parseable() -> None:
    """The rendered section should be valid TOML when appended to a minimal header."""
    section = _render_project_section("my-proj", "mp", "/data/my-proj", ["engineer"])
    # Parse just the section as standalone TOML
    toml_text = section.strip()
    parsed = tomllib.loads(toml_text)
    proj = parsed["projects"]["my-proj"]
    assert proj["code"] == "mp"
    assert proj["source_dir"] == "/data/my-proj"
    assert proj["roster"] == ["engineer"]
    assert "disclosure" not in proj


# ---------------------------------------------------------------------------
# cmd_add — minimal write + idempotency
# ---------------------------------------------------------------------------

def test_cmd_add_registers_project(config_file: Path, tmp_path: Path) -> None:
    """cmd_add should append the new section and leave the original byte-unchanged."""
    original_content = config_file.read_text(encoding="utf-8")

    cmd_add(
        name="new-proj",
        code="np",
        source_dir=str(tmp_path / "new-proj"),
        roster=["engineer"],
        config_path=config_file,
    )

    after = config_file.read_text(encoding="utf-8")
    # Original bytes must be a prefix of the new content (minimal write)
    assert after.startswith(original_content), \
        "cmd_add must append only — original file bytes must be unchanged prefix"

    # The new section must appear in the file
    assert "[projects.new-proj]" in after
    assert 'code = "np"' in after
    assert 'roster = ["engineer"]' in after
    # disclosure must NOT appear in new writes
    assert "disclosure" not in after


def test_cmd_add_is_parseable_after_write(config_file: Path, tmp_path: Path) -> None:
    """The TOML file remains parseable after cmd_add."""
    cmd_add(
        name="second-proj",
        code="sp",
        source_dir=str(tmp_path / "sp"),
        roster=["researcher"],
        config_path=config_file,
    )
    import tomllib
    content = config_file.read_bytes()
    parsed = tomllib.loads(content.decode())
    projects = parsed.get("projects", {})
    assert "existing-project" in projects, "Pre-existing project must still be present"
    assert "second-proj" in projects, "Newly registered project must be present"
    sp = projects["second-proj"]
    assert sp["code"] == "sp"
    assert sp["roster"] == ["researcher"]
    assert "disclosure" not in sp


def test_cmd_add_refuses_duplicate_name(config_file: Path, tmp_path: Path) -> None:
    """Adding a project with the same name as an existing one must raise ValueError."""
    with pytest.raises(ValueError, match="already exists"):
        cmd_add(
            name="existing-project",
            code="xyz",
            source_dir=str(tmp_path / "other"),
            roster=[],
            config_path=config_file,
        )


def test_cmd_add_refuses_duplicate_code(config_file: Path, tmp_path: Path) -> None:
    """Adding a project with the same code as an existing one must raise ValueError."""
    with pytest.raises(ValueError, match="already in use"):
        cmd_add(
            name="different-name",
            code="ep",  # same as existing-project
            source_dir=str(tmp_path / "other"),
            roster=[],
            config_path=config_file,
        )


def test_cmd_add_tilde_expansion(config_file: Path, tmp_path: Path) -> None:
    """source_dir with ~ should be expanded and stored as an absolute path."""
    import os
    home = os.path.expanduser("~")
    cmd_add(
        name="home-proj",
        code="hp",
        source_dir="~/my-research",
        roster=[],
        config_path=config_file,
    )
    content = config_file.read_text(encoding="utf-8")
    # The stored path must be absolute (no ~ in the written value)
    assert "~/my-research" not in content
    assert home in content


def test_cmd_add_no_config_raises(tmp_path: Path, monkeypatch) -> None:
    """cmd_add without a config file raises FileNotFoundError."""
    monkeypatch.delenv("RESEARCH_VAULT_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError):
        cmd_add("x", "x", "/path", [])


def test_cmd_add_cli_run_success(config_file: Path, tmp_path: Path, monkeypatch) -> None:
    """The CLI run() function returns 0 on a valid add."""
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(config_file))
    reset_config_cache()

    ns = argparse.Namespace(
        project_cmd="add",
        name="cli-proj",
        code="cp",
        source_dir=str(tmp_path / "cli-proj"),
        roster=["engineer"],
    )
    # Patch _find_config_path to return our test config
    import research_vault.project as proj_mod
    monkeypatch.setattr(proj_mod, "_find_config_path", lambda: config_file)

    rc = run(ns)
    assert rc == 0


def test_cmd_add_cli_run_duplicate_returns_nonzero(config_file: Path, tmp_path: Path, monkeypatch) -> None:
    """The CLI run() function returns non-zero on a duplicate name."""
    import research_vault.project as proj_mod
    monkeypatch.setattr(proj_mod, "_find_config_path", lambda: config_file)

    ns = argparse.Namespace(
        project_cmd="add",
        name="existing-project",
        code="xyz",
        source_dir=str(tmp_path / "other"),
        roster=[],
    )
    rc = run(ns)
    assert rc != 0


# ---------------------------------------------------------------------------
# cmd_list — SR-XP: real project list (cross-project discovery substrate)
# ---------------------------------------------------------------------------

def _make_cfg_with_two_projects(tmp_path: Path) -> Config:
    """Build a Config with two seeded projects for list tests."""
    proj_a = tmp_path / "project-a"
    proj_b = tmp_path / "project-b"
    proj_a.mkdir()
    proj_b.mkdir()
    raw = {
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(tmp_path / "state"),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "adapters": {"notifier": "file", "backend": "local", "secrets": "env"},
        "projects": {
            "project-a": {
                "code": "pa",
                "source_dir": str(proj_a),
                "roster": ["engineer", "researcher"],
            },
            "project-b": {
                "code": "pb",
                "source_dir": str(proj_b),
                "roster": ["researcher"],
            },
        },
    }
    return Config(raw)


def test_project_list_enumerates_two_projects(tmp_path: Path, capsys) -> None:
    """rv project list enumerates >=2 seeded projects with real fields."""
    cfg = _make_cfg_with_two_projects(tmp_path)
    rc = cmd_list(cfg)
    assert rc == 0
    out = capsys.readouterr().out
    assert "project-a" in out
    assert "project-b" in out
    assert "pa" in out
    assert "pb" in out
    assert "2 project(s)" in out


def test_project_list_shows_code_roster_source(tmp_path: Path, capsys) -> None:
    """rv project list shows code, roster, and source_dir for each project."""
    cfg = _make_cfg_with_two_projects(tmp_path)
    rc = cmd_list(cfg)
    assert rc == 0
    out = capsys.readouterr().out
    assert "code=pa" in out
    assert "roster=[engineer, researcher]" in out
    assert str(tmp_path / "project-a") in out


def test_project_list_no_disclosure_column(tmp_path: Path, capsys) -> None:
    """rv project list must not show a disclosure column (field is stripped)."""
    cfg = _make_cfg_with_two_projects(tmp_path)
    cmd_list(cfg)
    out = capsys.readouterr().out
    assert "disclosure" not in out


def test_project_list_empty_registry(tmp_path: Path, capsys) -> None:
    """rv project list with no projects prints a helpful message and exits 0."""
    raw = {
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(tmp_path / "state"),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "adapters": {"notifier": "file", "backend": "local", "secrets": "env"},
        "projects": {},
    }
    cfg = Config(raw)
    rc = cmd_list(cfg)
    assert rc == 0
    out = capsys.readouterr().out
    assert "No projects" in out
