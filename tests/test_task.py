"""test_task.py — tests for the task verb.

All tests are hermetic (tmp_instance fixture). No ~/vault reads or writes.
"""

import pytest
from pathlib import Path
from research_vault.config import load_config
from research_vault import task as task_mod


@pytest.fixture
def cfg(tmp_instance):
    return load_config(reload=True)


# ---------------------------------------------------------------------------
# cmd_add
# ---------------------------------------------------------------------------

def test_add_creates_card(cfg):
    """cmd_add creates a markdown card in the project's tasks directory."""
    path = task_mod.cmd_add("demo-research", "My first task", config=cfg)
    assert path.exists()
    assert path.suffix == ".md"
    content = path.read_text()
    assert "title: My first task" in content
    assert "project: demo-research" in content
    assert "status: backlog" in content


def test_add_custom_status_priority(cfg):
    """cmd_add respects status and priority overrides."""
    path = task_mod.cmd_add(
        "demo-research", "Urgent task", config=cfg,
        status="ready", priority="P0"
    )
    content = path.read_text()
    assert "status: ready" in content
    assert "priority: P0" in content


def test_add_unknown_project_raises(cfg):
    """cmd_add raises KeyError for an unregistered project."""
    with pytest.raises(KeyError, match="Unknown project"):
        task_mod.cmd_add("nonexistent-project", "task title", config=cfg)


def test_add_slug_from_title(cfg):
    """cmd_add slugifies the title for the filename."""
    path = task_mod.cmd_add("demo-research", "Add the OKF layer", config=cfg)
    assert "add-the-okf-layer" in path.name


def test_add_why_and_goal(cfg):
    """cmd_add stores why/goal in the frontmatter."""
    path = task_mod.cmd_add(
        "demo-research", "Task with context", config=cfg,
        why="Because it matters.", goal="Ship it."
    )
    content = path.read_text()
    assert "why: Because it matters." in content
    assert "goal: Ship it." in content


# ---------------------------------------------------------------------------
# cmd_list
# ---------------------------------------------------------------------------

def test_list_empty(cfg):
    """cmd_list returns empty list when no tasks exist."""
    cards = task_mod.cmd_list("demo-litreview", config=cfg)
    assert cards == []


def test_list_returns_created_cards(cfg):
    """cmd_list returns all cards for the project."""
    task_mod.cmd_add("demo-research", "Task A", config=cfg)
    task_mod.cmd_add("demo-research", "Task B", config=cfg, status="ready")
    cards = task_mod.cmd_list("demo-research", config=cfg)
    assert len(cards) == 2


def test_list_status_filter(cfg):
    """cmd_list filters by status when requested."""
    task_mod.cmd_add("demo-research", "Backlog task", config=cfg, status="backlog")
    task_mod.cmd_add("demo-research", "Ready task", config=cfg, status="ready")
    cards = task_mod.cmd_list("demo-research", config=cfg, status_filter="ready")
    assert len(cards) == 1
    assert cards[0]["fields"]["status"] == "ready"


# ---------------------------------------------------------------------------
# cmd_view
# ---------------------------------------------------------------------------

def test_view_existing_card(cfg):
    """cmd_view returns the card content."""
    task_mod.cmd_add("demo-research", "Viewable task", config=cfg)
    content = task_mod.cmd_view("demo-research", "viewable-task", config=cfg)
    assert "Viewable task" in content


def test_view_missing_card_raises(cfg):
    """cmd_view raises FileNotFoundError for a nonexistent slug."""
    with pytest.raises(FileNotFoundError):
        task_mod.cmd_view("demo-research", "no-such-task", config=cfg)


# ---------------------------------------------------------------------------
# cmd_update
# ---------------------------------------------------------------------------

def test_update_status(cfg):
    """cmd_update changes the status field."""
    task_mod.cmd_add("demo-research", "Updatable task", config=cfg)
    path = task_mod.cmd_update(
        "demo-research", "updatable-task",
        {"status": "done"}, config=cfg
    )
    content = path.read_text()
    assert "status: done" in content
    assert "updated:" in content


def test_update_nonexistent_raises(cfg):
    """cmd_update raises FileNotFoundError for a missing slug."""
    with pytest.raises(FileNotFoundError):
        task_mod.cmd_update("demo-research", "ghost-task", {"status": "done"}, config=cfg)


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def test_cli_task_add(tmp_instance, capsys):
    """rv task add creates a card and prints the path."""
    from research_vault.cli import main
    result = main(["task", "add", "demo-research", "CLI-created task"])
    assert result == 0
    out = capsys.readouterr().out
    assert "Created:" in out


def test_cli_task_list(tmp_instance, capsys):
    """rv task list shows created cards."""
    from research_vault.cli import main
    from research_vault.config import load_config
    cfg = load_config(reload=True)
    task_mod.cmd_add("demo-research", "Listed task", config=cfg)

    result = main(["task", "list", "demo-research"])
    assert result == 0
    out = capsys.readouterr().out
    assert "listed-task" in out.lower() or "Listed task" in out
