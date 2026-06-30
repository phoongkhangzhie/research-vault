"""test_devlog.py — tests for the devlog verb.

All hermetic (tmp_instance). No ~/vault reads or writes.
"""

import datetime
import pytest
from research_vault.config import load_config
from research_vault import devlog as devlog_mod


@pytest.fixture
def cfg(tmp_instance):
    return load_config(reload=True)


def test_init_creates_devlog(cfg):
    """cmd_init creates a DEVLOG.md with a dated entry header."""
    path = devlog_mod.cmd_init("demo-research", config=cfg)
    assert path.exists()
    content = path.read_text()
    assert "# DEVLOG — demo-research" in content
    today = datetime.date.today().isoformat()
    assert f"## {today}" in content
    assert "### Done" in content
    assert "### Decisions" in content
    assert "### Open / next" in content


def test_init_with_note(cfg):
    """cmd_init embeds the note in the header."""
    path = devlog_mod.cmd_init("demo-research", "Bootstrap entry.", config=cfg)
    assert "Bootstrap entry." in path.read_text()


def test_init_raises_if_exists(cfg):
    """cmd_init raises FileExistsError on second call without --overwrite."""
    devlog_mod.cmd_init("demo-research", config=cfg)
    with pytest.raises(FileExistsError, match="already exists"):
        devlog_mod.cmd_init("demo-research", config=cfg)


def test_init_overwrite(cfg):
    """cmd_init with overwrite=True replaces the existing DEVLOG."""
    devlog_mod.cmd_init("demo-research", "first", config=cfg)
    path = devlog_mod.cmd_init("demo-research", "second", config=cfg, overwrite=True)
    assert "second" in path.read_text()


def test_check_ok_fresh_devlog(cfg):
    """cmd_check returns OK for a freshly created DEVLOG."""
    devlog_mod.cmd_init("demo-research", config=cfg)
    status, msg = devlog_mod.cmd_check("demo-research", config=cfg)
    assert status == "OK"


def test_check_missing_devlog(cfg):
    """cmd_check returns MISSING when no DEVLOG.md exists."""
    status, msg = devlog_mod.cmd_check("demo-litreview", config=cfg)
    assert status == "MISSING"


def test_check_stale_devlog(cfg):
    """cmd_check returns STALE when the latest entry is older than STALE_DAYS."""
    path = devlog_mod.cmd_init("demo-research", config=cfg)
    # Rewrite with an old date entry
    old_date = (datetime.date.today() - datetime.timedelta(days=20)).isoformat()
    content = path.read_text()
    today = datetime.date.today().isoformat()
    content = content.replace(f"## {today}", f"## {old_date}")
    path.write_text(content)

    status, msg = devlog_mod.cmd_check("demo-research", config=cfg)
    assert status == "STALE"
    assert "20 days" in msg


def test_append_adds_bullet(cfg):
    """cmd_append adds a bullet to the correct section."""
    devlog_mod.cmd_init("demo-research", config=cfg)
    path = devlog_mod.cmd_append(
        "demo-research", "Done", "Scaffolded SR-1.", config=cfg
    )
    content = path.read_text()
    assert "Scaffolded SR-1." in content


def test_append_creates_devlog_if_missing(cfg):
    """cmd_append creates the DEVLOG if it doesn't exist yet."""
    path = devlog_mod.cmd_append(
        "demo-litreview", "Decisions", "Chose OKF format.", config=cfg
    )
    assert path.exists()
    assert "Chose OKF format." in path.read_text()


def test_view_returns_top_lines(cfg):
    """cmd_view returns the first N lines of the DEVLOG."""
    devlog_mod.cmd_init("demo-research", config=cfg)
    content = devlog_mod.cmd_view("demo-research", config=cfg, lines=5)
    lines = content.splitlines()
    assert len(lines) <= 5
    assert "DEVLOG" in content


def test_view_missing_raises(cfg):
    """cmd_view raises FileNotFoundError when DEVLOG doesn't exist."""
    with pytest.raises(FileNotFoundError):
        devlog_mod.cmd_view("demo-litreview", config=cfg)


def test_unknown_project_raises(cfg):
    """cmd_init raises KeyError for an unknown project."""
    with pytest.raises(KeyError, match="Unknown project"):
        devlog_mod.cmd_init("ghost-project", config=cfg)


def test_cli_devlog_init(tmp_instance, capsys):
    """rv devlog <project> init prints the created path (project-first form)."""
    from research_vault.cli import main
    result = main(["devlog", "demo-research", "init"])
    assert result == 0
    assert "Created:" in capsys.readouterr().out


def test_cli_devlog_check_ok(tmp_instance, capsys):
    """rv devlog <project> check exits 0 for a fresh DEVLOG (project-first form)."""
    from research_vault.cli import main
    main(["devlog", "demo-research", "init"])
    result = main(["devlog", "demo-research", "check"])
    assert result == 0
    out = capsys.readouterr().out
    assert "OK" in out
