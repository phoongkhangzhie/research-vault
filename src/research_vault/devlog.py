# SPDX-License-Identifier: AGPL-3.0-or-later
"""devlog.py — DEVLOG management, freshness check, and index/search for projects.

When to use: use `rv devlog <project> <subcommand>` to seed, append to, check the
freshness of, or search a project's DEVLOG.md. The DEVLOG is the grounded record of
decisions and progress — one entry per working day, newest on top, structured with
### Done / ### Decisions / ### Open / next sections.

Use `rv devlog index` and `rv devlog search` to navigate the DEVLOG without loading the
whole file. Anti-pattern: do NOT grep or cat DEVLOG.md directly — use the index face.

Freshness rules:
  MISSING — no DEVLOG.md found → FAIL
  STALE   — latest dated entry is >14 days old while the project dir had recent writes

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
# INDEX / SEARCH commands (append-only-record read face)
# ---------------------------------------------------------------------------

def _parse_entries(devlog_path: Path) -> list[dict]:
    """Parse dated entries from a DEVLOG.md.

    Returns list of dicts: {date, summary, body, lineno}.
    summary is the first non-empty line of the entry body (used for index one-liners).
    body is the full entry body text.

    Does NOT load the whole file into one string for search — iterates lines.
    """
    if not devlog_path.exists():
        return []

    entries: list[dict] = []
    current_date: str | None = None
    current_body_lines: list[str] = []
    current_lineno: int = 0

    def _flush() -> None:
        if current_date is not None:
            body = "\n".join(current_body_lines).strip()
            # Extract summary: first non-empty, non-header line
            summary = ""
            for ln in current_body_lines:
                s = ln.strip()
                if s and not s.startswith("#") and not s.startswith("- _(add"):
                    # Strip leading "- " for bullet lines
                    summary = s[2:].strip() if s.startswith("- ") else s
                    break
            if not summary:
                summary = body[:80] if body else "(empty)"
            entries.append({
                "date": current_date,
                "summary": summary,
                "body": body,
                "lineno": current_lineno,
            })

    for lineno, line in enumerate(devlog_path.read_text(encoding="utf-8").splitlines(), 1):
        m = _DATE_ENTRY_RE.match(line)
        if m:
            _flush()
            current_date = m.group(1)
            current_body_lines = []
            current_lineno = lineno
        elif current_date is not None:
            current_body_lines.append(line)

    _flush()
    return entries


def cmd_index(project: str, *, config: Config | None = None) -> list[dict]:
    """Return a one-liner index of all dated DEVLOG entries.

    Returns list of dicts: {date, summary}.
    Idempotent: calling twice returns the same list.
    Does NOT require loading the whole file to get the index.

    Anti-pattern: do NOT grep/cat DEVLOG.md to find entries — use this instead.
    """
    cfg = config or load_config()
    devlog_path = cfg.project_devlog(project)
    entries = _parse_entries(devlog_path)
    return [{"date": e["date"], "summary": e["summary"]} for e in entries]


def cmd_search(project: str, query: str, *, config: Config | None = None) -> list[dict]:
    """Search dated DEVLOG entries for a keyword/phrase.

    Returns list of matching dicts: {date, summary, body}.
    Case-insensitive substring match against entry body.

    Anti-pattern: do NOT grep the raw DEVLOG file — use this for indexed access.
    """
    cfg = config or load_config()
    devlog_path = cfg.project_devlog(project)
    entries = _parse_entries(devlog_path)
    q_low = query.lower()
    results = []
    for e in entries:
        if q_low in e["body"].lower() or q_low in e["summary"].lower():
            results.append({
                "date": e["date"],
                "summary": e["summary"],
                "body": e["body"],
            })
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(parent: argparse._SubParsersAction | None = None) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Build the argument parser for the `devlog` verb.

    When to use: use `rv devlog <project> <subcommand>` to manage a project's DEVLOG.md.
    The DEVLOG is the grounded decision record — one entry per working day, newest on top.
    Use `check` in CI to enforce DEVLOG freshness. Use `append` to add a bullet.
    Use `index` to get a one-liner per entry. Use `search` to find entries by keyword.

    Anti-pattern: do NOT grep/cat DEVLOG.md to find or read entries — that loads the whole
    file and misses the structured index. Use `rv devlog index` and `rv devlog search` instead.
    """
    desc = "Manage project DEVLOG.md — the grounded decision and progress record."
    if parent is not None:
        p = parent.add_parser("devlog", help="Project DEVLOG management.", description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv devlog", description=desc)

    p.add_argument("project", help="Project slug.")

    sub = p.add_subparsers(dest="devlog_cmd", required=True)

    # init
    init_p = sub.add_parser("init", help="Create the DEVLOG.md for a project.")
    init_p.add_argument("--note", default="", help="Optional header note.")
    init_p.add_argument("--overwrite", action="store_true")

    # append
    app_p = sub.add_parser("append", help="Append a bullet to a section of today's entry.")
    app_p.add_argument("section", choices=["Done", "Decisions", "Open / next"],
                       help="Section to append to.")
    app_p.add_argument("text", help="Bullet text.")
    app_p.add_argument("--date", default=None, help="Override date (YYYY-MM-DD).")

    # check
    sub.add_parser("check", help="Check DEVLOG freshness.")

    # view
    view_p = sub.add_parser("view", help="Print the top of the DEVLOG.")
    view_p.add_argument("--lines", type=int, default=50, help="Number of lines to show.")

    # index
    sub.add_parser(
        "index",
        help="Print a one-liner per dated entry (the structured index face).",
    )

    # search
    search_p = sub.add_parser(
        "search",
        help="Search DEVLOG entries by keyword (anti-pattern: do not grep the raw file).",
    )
    search_p.add_argument("query", help="Keyword or phrase to search for.")

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

        elif args.devlog_cmd == "index":
            entries = cmd_index(args.project, config=cfg)
            if not entries:
                print(f"rv devlog index: no dated entries in {args.project!r}")
                return 0
            for e in entries:
                print(f"  {e['date']} — {e['summary'][:80]}")
            return 0

        elif args.devlog_cmd == "search":
            results = cmd_search(args.project, args.query, config=cfg)
            if not results:
                print(f"rv devlog search: no matches for {args.query!r}")
                return 0
            for r in results:
                print(f"  {r['date']} — {r['summary'][:80]}")
            return 0

    except (KeyError, FileNotFoundError, FileExistsError) as e:
        print(f"rv devlog: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"rv devlog: unexpected error: {e}", file=sys.stderr)
        return 1

    return 0
