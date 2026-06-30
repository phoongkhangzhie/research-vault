"""test_control.py — tests for the control verb (coordination control files).

All hermetic (tmp_instance). No ~/vault reads or writes.
"""

import pytest
from research_vault.config import load_config
from research_vault import control as control_mod


@pytest.fixture
def cfg(tmp_instance):
    return load_config(reload=True)


def test_init_creates_control_file(cfg):
    """cmd_init creates a well-formed control file."""
    path = control_mod.cmd_init("demo-research", config=cfg)
    assert path.exists()
    content = path.read_text()
    assert "# CONTROL — demo-research" in content
    assert "## Inbox" in content
    assert "## Handshakes" in content
    assert "## Outbox" in content
    assert "## Open / blockers" in content


def test_init_with_note(cfg):
    """cmd_init embeds the note in the header."""
    path = control_mod.cmd_init(
        "demo-research", config=cfg, note="SR-1 membrane standup."
    )
    assert "SR-1 membrane standup." in path.read_text()


def test_init_raises_if_exists(cfg):
    """cmd_init raises FileExistsError on second call without --overwrite."""
    control_mod.cmd_init("demo-research", config=cfg)
    with pytest.raises(FileExistsError, match="already exists"):
        control_mod.cmd_init("demo-research", config=cfg)


def test_init_overwrite(cfg):
    """cmd_init with overwrite=True replaces the existing file."""
    control_mod.cmd_init("demo-research", config=cfg, note="original")
    path = control_mod.cmd_init(
        "demo-research", config=cfg, note="replaced", overwrite=True
    )
    assert "replaced" in path.read_text()
    assert "original" not in path.read_text()


def test_view_returns_content(cfg):
    """cmd_view returns the control file content."""
    control_mod.cmd_init("demo-research", config=cfg)
    content = control_mod.cmd_view("demo-research", config=cfg)
    assert "CONTROL" in content


def test_view_missing_raises(cfg):
    """cmd_view raises FileNotFoundError when control file doesn't exist."""
    with pytest.raises(FileNotFoundError, match="No control file"):
        control_mod.cmd_view("demo-litreview", config=cfg)


def test_check_valid_file_passes(cfg):
    """cmd_check returns empty list for a valid control file."""
    control_mod.cmd_init("demo-research", config=cfg)
    violations = control_mod.cmd_check("demo-research", config=cfg)
    assert violations == []


def test_check_missing_file_fails(cfg):
    """cmd_check returns a violation when the control file is missing."""
    violations = control_mod.cmd_check("demo-litreview", config=cfg)
    assert any("Missing control file" in v for v in violations)


def test_inbox_appends_message(cfg):
    """cmd_inbox adds a dated bullet to the Inbox section."""
    control_mod.cmd_init("demo-research", config=cfg)
    path = control_mod.cmd_inbox("demo-research", "SR-1 scaffold dispatched.", config=cfg)
    content = path.read_text()
    assert "SR-1 scaffold dispatched." in content


def test_inbox_creates_if_missing(cfg):
    """cmd_inbox creates the control file if it doesn't exist yet."""
    path = control_mod.cmd_inbox("demo-litreview", "Hello inbox.", config=cfg)
    assert path.exists()
    assert "Hello inbox." in path.read_text()


def test_unknown_project_raises(cfg):
    """cmd_init raises KeyError for an unknown project."""
    with pytest.raises(KeyError, match="Unknown project"):
        control_mod.cmd_init("ghost-project", config=cfg)


def test_cli_control_init(tmp_instance, capsys):
    """rv control init prints the created path."""
    from research_vault.cli import main
    result = main(["control", "init", "demo-research"])
    assert result == 0
    assert "Created:" in capsys.readouterr().out


def test_cli_control_check(tmp_instance, capsys):
    """rv control check exits 0 on a valid control file."""
    from research_vault.cli import main
    main(["control", "init", "demo-research"])
    result = main(["control", "check", "demo-research"])
    assert result == 0
