"""devlog.py — DEVLOG management and freshness check for projects.

When to use: use `rv devlog <project> <subcommand>` to seed, append to, or check the
freshness of a project's DEVLOG.md. The DEVLOG is the grounded record of decisions and
progress — one entry per working day, newest on top, structured with ### Done / ### Decisions
/ ### Open / next sections.

Freshness rules:
  MISSING — no DEVLOG.md found → FAIL
  STALE   — latest dated entry is >14 days old while the project dir had recent writes
  OK      — all else

All paths resolved from Config — zero hardcoded paths.
Stdlib only.
"""

import argparse
import datetime
import re
import subprocess
import sys
from pathlib import Path

from .config import Config, load_config

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STALE_DAYS = 14
_DATE_ENTRY_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2})", re.MULTILINE)


def _today() -> str:
    return datetime.date.today().isoformat()


def _seed_entry(date: str) -> str:
    return f"""## {date}

### Done
- _(add what was accomplished)_

### Decisions
- _(add key decisions made)_

### Open / next
- _(add open questions and next steps)_
"""


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init(project: str, note: str = "", *,
             config: Config | None = None, overwrite: bool = False) -> Path:
    """Create a DEVLOG.md for the given project.

    Seeds the first entry with today's date.
    Raises FileExistsError if the DEVLOG already exists and overwrite=False.
    """
    cfg = config or load_config()
    devlog_path = cfg.project_devlog(project)
    devlog_path.parent.mkdir(parents=True, exist_ok=True)

    if devlog_path.exists() and not overwrite:
        raise FileExistsError(
            f"DEVLOG already exists: {devlog_path}. Use --overwrite to reset."
        )

    header = note or f"Project {project!r} — devlog started."
    content = f"# DEVLOG — {project}\n\nNewest entry on top.\n{header}\n\n"
    content += _seed_entry(_today())

    devlog_path.write_text(content, encoding="utf-8")
    return devlog_path


def cmd_append(project: str, section: str, text: str, *,
               config: Config | None = None,
               date: str | None = None) -> Path:
    """Append a bullet to a section of today's DEVLOG entry.

    Creates a new dated entry if today's entry doesn't exist yet.
    section should be one of: Done, Decisions, Open / next
    """
    cfg = config or load_config()
    devlog_path = cfg.project_devlog(project)

    today = date or _today()
    entry_header = f"## {today}"

    if not devlog_path.exists():
        cmd_init(project, config=cfg)

    content = devlog_path.read_text(encoding="utf-8")

    if entry_header not in content:
        # Prepend a new entry for today (newest-on-top convention)
        # Find where to insert: after the file header (before the first ## YYYY entry)
        first_entry = _DATE_ENTRY_RE.search(content)
        if first_entry:
            insert_pos = first_entry.start()
            new_entry = _seed_entry(today) + "\n"
            content = content[:insert_pos] + new_entry + content[insert_pos:]
        else:
            content = content.rstrip() + "\n\n" + _seed_entry(today)

    # Find the section and append the bullet
    # Match the section header under today's entry
    section_pattern = re.compile(
        r"(### " + re.escape(section) + r"\n)(.*?)(?=\n### |\Z)",
        re.DOTALL
    )
    m = section_pattern.search(content)
    if m:
        section_body = m.group(2)
        # Replace the seed placeholder if present
        if "_(add" in section_body:
            new_body = f"- {text}\n"
        else:
            new_body = section_body.rstrip("\n") + f"\n- {text}\n"
        content = content[:m.start(2)] + new_body + content[m.end(2):]
    else:
        # Section not found in today's entry — just append at end of file
        content = content.rstrip() + f"\n\n### {section}\n- {text}\n"

    devlog_path.write_text(content, encoding="utf-8")
    return devlog_path


def cmd_check(project: str, *, config: Config | None = None) -> tuple[str, str]:
    """Check DEVLOG freshness for the given project.

    Returns (status, message) where status is: OK | STALE | MISSING
    """
    cfg = config or load_config()
    devlog_path = cfg.project_devlog(project)

    if not devlog_path.exists():
        return "MISSING", f"No DEVLOG.md found at: {devlog_path}"

    content = devlog_path.read_text(encoding="utf-8")
    dates = _DATE_ENTRY_RE.findall(content)

    if not dates:
        return "STALE", "DEVLOG.md has no dated entries (expected '## YYYY-MM-DD' headers)."

    latest_str = max(dates)
    try:
        latest_date = datetime.date.fromisoformat(latest_str)
    except ValueError:
        return "STALE", f"Could not parse latest entry date: {latest_str!r}"

    today = datetime.date.today()
    age_days = (today - latest_date).days

    if age_days > STALE_DAYS:
        return "STALE", (
            f"Latest entry is {latest_str!r} ({age_days} days ago). "
            f"DEVLOG is stale (threshold: {STALE_DAYS} days)."
        )

    return "OK", f"Latest entry: {latest_str!r} ({age_days} days ago)."


def cmd_view(project: str, *, config: Config | None = None, lines: int = 50) -> str:
    """Return the first `lines` lines of the project DEVLOG."""
    cfg = config or load_config()
    devlog_path = cfg.project_devlog(project)
    if not devlog_path.exists():
        raise FileNotFoundError(f"No DEVLOG.md at: {devlog_path}")
    content = devlog_path.read_text(encoding="utf-8")
    return "\n".join(content.splitlines()[:lines])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(parent: argparse._SubParsersAction | None = None) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Build the argument parser for the `devlog` verb.

    When to use: use `rv devlog <project> <subcommand>` to manage a project's DEVLOG.md.
    The DEVLOG is the grounded decision record — one entry per working day, newest on top.
    Use `check` in CI to enforce DEVLOG freshness. Use `append` to add a bullet to a section.
    """
    desc = "Manage project DEVLOG.md — the grounded decision and progress record."
    if parent is not None:
        p = parent.add_parser("devlog", help="Project DEVLOG management.", description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv devlog", description=desc)

    sub = p.add_subparsers(dest="devlog_cmd", required=True)

    # init
    init_p = sub.add_parser("init", help="Create the DEVLOG.md for a project.")
    init_p.add_argument("project", help="Project slug.")
    init_p.add_argument("--note", default="", help="Optional header note.")
    init_p.add_argument("--overwrite", action="store_true")

    # append
    app_p = sub.add_parser("append", help="Append a bullet to a section of today's entry.")
    app_p.add_argument("project", help="Project slug.")
    app_p.add_argument("section", choices=["Done", "Decisions", "Open / next"],
                       help="Section to append to.")
    app_p.add_argument("text", help="Bullet text.")
    app_p.add_argument("--date", default=None, help="Override date (YYYY-MM-DD).")

    # check
    check_p = sub.add_parser("check", help="Check DEVLOG freshness.")
    check_p.add_argument("project", help="Project slug.")

    # view
    view_p = sub.add_parser("view", help="Print the top of the DEVLOG.")
    view_p.add_argument("project", help="Project slug.")
    view_p.add_argument("--lines", type=int, default=50, help="Number of lines to show.")

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch devlog subcommands. Returns exit code."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv devlog: config error: {e}", file=sys.stderr)
        return 1

    try:
        if args.devlog_cmd == "init":
            path = cmd_init(args.project, args.note, config=cfg, overwrite=args.overwrite)
            print(f"Created: {path}")
            return 0

        elif args.devlog_cmd == "append":
            path = cmd_append(args.project, args.section, args.text,
                              config=cfg, date=args.date)
            print(f"Updated: {path}")
            return 0

        elif args.devlog_cmd == "check":
            status, message = cmd_check(args.project, config=cfg)
            print(f"rv devlog check: {status} — {args.project!r}: {message}")
            return 0 if status == "OK" else 1

        elif args.devlog_cmd == "view":
            print(cmd_view(args.project, config=cfg, lines=args.lines))
            return 0

    except (KeyError, FileNotFoundError, FileExistsError) as e:
        print(f"rv devlog: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"rv devlog: unexpected error: {e}", file=sys.stderr)
        return 1

    return 0
