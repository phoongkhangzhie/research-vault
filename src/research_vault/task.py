"""task.py — project-scoped task tracker.

When to use: create, view, or list task cards for a specific project. Task cards are
markdown files with YAML-like frontmatter, stored in the project's tasks directory.
Each card carries: project, title, status, priority, assigned, why, goal, next.

All paths are resolved from Config — zero hardcoded paths, zero codenames.
Stdlib only.
"""

import argparse
import datetime
import os
import re
import sys
from pathlib import Path
from typing import Any

from .config import Config, load_config

# ---------------------------------------------------------------------------
# Card format
# ---------------------------------------------------------------------------

VALID_STATUSES = frozenset({"backlog", "ready", "in_progress", "blocked", "done"})
VALID_PRIORITIES = frozenset({"P0", "P1", "P2", "P3"})

FRONTMATTER_FIELDS = [
    "project", "title", "status", "priority", "assigned",
    "submitted", "why", "goal", "next", "updated",
]


def _today() -> str:
    return datetime.date.today().isoformat()


def _slugify(title: str) -> str:
    """Convert a title to a filename-safe slug."""
    slug = title.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:80] or "task"


def _render_frontmatter(fields: dict[str, str]) -> str:
    lines = ["---"]
    for key in FRONTMATTER_FIELDS:
        if key in fields:
            lines.append(f"{key}: {fields[key]!r}" if "\n" in fields[key]
                         else f"{key}: {fields[key]}")
    lines.append("---")
    return "\n".join(lines)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse YAML-ish frontmatter from a card. Returns (fields, body)."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_block = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    fields: dict[str, str] = {}
    for line in fm_block.splitlines():
        m = re.match(r"^(\w+):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            # Strip surrounding quotes if present
            if val.startswith(("'", '"')) and val.endswith(val[0]):
                val = val[1:-1]
            fields[key] = val
    return fields, body


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_add(project: str, title: str, *, config: Config | None = None,
            status: str = "backlog", priority: str = "P2",
            assigned: str = "", why: str = "", goal: str = "") -> Path:
    """Create a new task card for the given project.

    Returns the path to the created card file.
    """
    cfg = config or load_config()
    tasks_dir = cfg.project_tasks_dir(project)
    tasks_dir.mkdir(parents=True, exist_ok=True)

    slug = _slugify(title)
    card_path = tasks_dir / f"{slug}.md"
    if card_path.exists():
        # Make unique by appending date
        slug = f"{slug}-{_today()}"
        card_path = tasks_dir / f"{slug}.md"

    fields: dict[str, str] = {
        "project": project,
        "title": title,
        "status": status,
        "priority": priority,
        "submitted": _today(),
    }
    if assigned:
        fields["assigned"] = assigned
    if why:
        fields["why"] = why
    if goal:
        fields["goal"] = goal

    body = f"\n## Notes\n\n<!-- Add notes here -->\n"
    card_path.write_text(_render_frontmatter(fields) + "\n" + body, encoding="utf-8")
    return card_path


def cmd_list(project: str, *, config: Config | None = None,
             status_filter: str | None = None) -> list[dict[str, Any]]:
    """List task cards for the given project.

    Returns a list of dicts with {path, fields} for each card.
    Optionally filter by status.
    """
    cfg = config or load_config()
    tasks_dir = cfg.project_tasks_dir(project)
    if not tasks_dir.exists():
        return []
    cards = []
    for p in sorted(tasks_dir.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        fields, _ = _parse_frontmatter(text)
        if status_filter and fields.get("status") != status_filter:
            continue
        cards.append({"path": p, "fields": fields})
    return cards


def cmd_view(project: str, slug: str, *, config: Config | None = None) -> str:
    """Return the raw content of a task card."""
    cfg = config or load_config()
    tasks_dir = cfg.project_tasks_dir(project)
    card_path = tasks_dir / f"{slug}.md"
    if not card_path.exists():
        raise FileNotFoundError(f"Task card not found: {card_path}")
    return card_path.read_text(encoding="utf-8")


def cmd_update(project: str, slug: str, updates: dict[str, str], *,
               config: Config | None = None) -> Path:
    """Update frontmatter fields on an existing task card.

    Updates stamps 'updated' with today's date automatically.
    """
    cfg = config or load_config()
    tasks_dir = cfg.project_tasks_dir(project)
    card_path = tasks_dir / f"{slug}.md"
    if not card_path.exists():
        raise FileNotFoundError(f"Task card not found: {card_path}")

    text = card_path.read_text(encoding="utf-8")
    fields, body = _parse_frontmatter(text)
    fields.update(updates)
    fields["updated"] = _today()

    card_path.write_text(_render_frontmatter(fields) + "\n" + body, encoding="utf-8")
    return card_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_card_summary(card: dict[str, Any]) -> None:
    fields = card["fields"]
    status = fields.get("status", "?")
    priority = fields.get("priority", "?")
    title = fields.get("title", card["path"].stem)
    slug = card["path"].stem
    print(f"  [{status:<11}] {priority} — {slug}: {title}")


def build_parser(parent: argparse._SubParsersAction | None = None) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Build the argument parser for the `task` verb.

    When to use: use `rv task <project> <subcommand>` to manage project task cards.
    Creates, lists, views, and updates markdown task cards stored in the project's tasks dir.
    """
    desc = "Manage task cards for a project. Cards are markdown files with frontmatter."
    if parent is not None:
        p = parent.add_parser("task", help="Manage project task cards.", description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv task", description=desc)

    sub = p.add_subparsers(dest="task_cmd", required=True)

    # add
    add_p = sub.add_parser("add", help="Create a new task card.")
    add_p.add_argument("project", help="Project slug (must be in config registry).")
    add_p.add_argument("title", help="Task title.")
    add_p.add_argument("--status", default="backlog", choices=sorted(VALID_STATUSES))
    add_p.add_argument("--priority", default="P2", choices=sorted(VALID_PRIORITIES))
    add_p.add_argument("--assigned", default="")
    add_p.add_argument("--why", default="")
    add_p.add_argument("--goal", default="")

    # list
    list_p = sub.add_parser("list", help="List task cards.")
    list_p.add_argument("project", help="Project slug.")
    list_p.add_argument("--status", dest="status_filter", default=None,
                        help="Filter by status.")

    # view
    view_p = sub.add_parser("view", help="View a task card.")
    view_p.add_argument("project", help="Project slug.")
    view_p.add_argument("slug", help="Task slug (filename without .md).")

    # update
    upd_p = sub.add_parser("update", help="Update a task card's status or other fields.")
    upd_p.add_argument("project", help="Project slug.")
    upd_p.add_argument("slug", help="Task slug.")
    upd_p.add_argument("--status", default=None)
    upd_p.add_argument("--priority", default=None)
    upd_p.add_argument("--assigned", default=None)
    upd_p.add_argument("--next", dest="next_", default=None)

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch task subcommands. Returns exit code."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv task: config error: {e}", file=sys.stderr)
        return 1

    try:
        if args.task_cmd == "add":
            path = cmd_add(
                args.project, args.title,
                config=cfg,
                status=args.status,
                priority=args.priority,
                assigned=args.assigned,
                why=args.why,
                goal=args.goal,
            )
            print(f"Created: {path}")
            return 0

        elif args.task_cmd == "list":
            cards = cmd_list(args.project, config=cfg,
                             status_filter=args.status_filter)
            if not cards:
                print(f"No tasks found for project {args.project!r}.")
                return 0
            print(f"Tasks for {args.project!r}:")
            for card in cards:
                _print_card_summary(card)
            return 0

        elif args.task_cmd == "view":
            content = cmd_view(args.project, args.slug, config=cfg)
            print(content, end="")
            return 0

        elif args.task_cmd == "update":
            updates: dict[str, str] = {}
            if args.status:
                updates["status"] = args.status
            if args.priority:
                updates["priority"] = args.priority
            if args.assigned:
                updates["assigned"] = args.assigned
            if args.next_:
                updates["next"] = args.next_
            if not updates:
                print("rv task update: no fields to update.", file=sys.stderr)
                return 1
            path = cmd_update(args.project, args.slug, updates, config=cfg)
            print(f"Updated: {path}")
            return 0

    except KeyError as e:
        print(f"rv task: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"rv task: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"rv task: unexpected error: {e}", file=sys.stderr)
        return 1

    return 0
