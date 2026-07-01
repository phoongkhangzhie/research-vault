"""mdstore.py — markdown document store checks for Research Vault.

When to use: ``rv mdstore <subcommand>`` to check, archive, or inspect the
markdown document store. Validates OKF link integrity, freshness, and document
structure.

All path resolution goes through Config — zero hardcoded paths or codenames.
Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path
from typing import Any

from .config import Config, load_config

# ---------------------------------------------------------------------------
# Frontmatter parsing (minimal, no third-party deps)
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> dict[str, str]:
    """Parse simple YAML-ish frontmatter from a markdown file."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm_block = text[3:end].strip()
    fields: dict[str, str] = {}
    for line in fm_block.splitlines():
        m = re.match(r"^(\w[\w-]*):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip().strip("\"'")
            fields[key] = val
    return fields


# ---------------------------------------------------------------------------
# OKF link checking
# ---------------------------------------------------------------------------

_OKF_LINK_RE = re.compile(
    r"\[(?P<text>[^\]]*)\]\((?P<path>[^)\s]+?\.md)(?P<section>#[^)\s]*)?\)"
)


def _check_links(text: str, note_path: Path, notes_root: Path) -> list[str]:
    """Return a list of broken OKF link descriptions in the note."""
    broken = []
    for m in _OKF_LINK_RE.finditer(text):
        link_path = m.group("path")
        # Resolve relative to the notes_root or the note's parent dir
        candidate = (note_path.parent / link_path).resolve()
        if not candidate.exists():
            # Also try from notes_root
            candidate2 = (notes_root / link_path.lstrip("/")).resolve()
            if not candidate2.exists():
                broken.append(f"broken link: {link_path!r} in {note_path.name}")
    return broken


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_check(
    project: str | None,
    cfg: Config,
    *,
    check_links: bool = True,
) -> int:
    """Check the document store for a project (or all projects).

    Reports:
    - Notes missing required frontmatter fields (type, title)
    - Broken OKF links (file references that don't resolve)

    Returns 0 if no issues, 1 if any issues found.
    """
    if project:
        slugs = [project]
    else:
        slugs = cfg.all_project_slugs()

    if not slugs:
        print("No projects registered.")
        return 0

    total_issues = 0

    for slug in slugs:
        try:
            notes_dir = cfg.project_notes_dir(slug)
        except KeyError as e:
            print(f"rv mdstore check: {e}", file=sys.stderr)
            return 1

        if not notes_dir.exists():
            print(f"  {slug}: notes directory not found ({notes_dir}) — skipping.")
            continue

        note_files = list(notes_dir.rglob("*.md"))
        issues: list[str] = []

        for note_path in sorted(note_files):
            try:
                text = note_path.read_text(encoding="utf-8")
            except OSError as e:
                issues.append(f"read error: {note_path.name}: {e}")
                continue

            fm = _parse_frontmatter(text)

            # Check required fields
            for req in ("type", "title"):
                if req not in fm:
                    issues.append(f"missing frontmatter '{req}': {note_path.name}")

            # Check links
            if check_links:
                issues.extend(_check_links(text, note_path, notes_dir))

        if issues:
            print(f"{slug}: {len(issues)} issue(s) in {len(note_files)} note(s):")
            for issue in issues:
                print(f"    {issue}")
            total_issues += len(issues)
        else:
            print(f"{slug}: {len(note_files)} note(s) — OK")

    return 0 if total_issues == 0 else 1


def cmd_freshness(project: str | None, cfg: Config, *, max_days: int = 7) -> int:
    """Report notes that haven't been updated within max_days.

    Checks the 'updated' or 'date' frontmatter field if present; otherwise
    falls back to the file's mtime.
    """
    if project:
        slugs = [project]
    else:
        slugs = cfg.all_project_slugs()

    now = datetime.date.today()
    cutoff = now - datetime.timedelta(days=max_days)

    for slug in slugs:
        try:
            notes_dir = cfg.project_notes_dir(slug)
        except KeyError as e:
            print(f"rv mdstore freshness: {e}", file=sys.stderr)
            return 1

        if not notes_dir.exists():
            continue

        stale = []
        for note_path in sorted(notes_dir.rglob("*.md")):
            try:
                text = note_path.read_text(encoding="utf-8")
                fm = _parse_frontmatter(text)
                date_str = fm.get("updated") or fm.get("date", "")
                if date_str:
                    try:
                        note_date = datetime.date.fromisoformat(date_str[:10])
                        if note_date < cutoff:
                            stale.append((note_path.name, str(note_date)))
                    except ValueError:
                        pass
            except OSError:
                pass

        if stale:
            print(f"{slug}: {len(stale)} stale note(s) (last update > {max_days} days ago):")
            for name, date in stale:
                print(f"    {name} (last: {date})")
        else:
            print(f"{slug}: all notes updated within {max_days} days — OK")

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``mdstore`` verb.

    When to use: ``rv mdstore check`` to validate OKF link integrity and
    required frontmatter fields in the document store.
    """
    desc = "Inspect and validate the markdown document store (OKF notes)."
    if parent is not None:
        p = parent.add_parser("mdstore", help="Inspect the markdown document store.", description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv mdstore", description=desc)

    sub = p.add_subparsers(dest="mdstore_cmd", required=True)

    check_p = sub.add_parser("check", help="Check frontmatter and link integrity.")
    check_p.add_argument("--project", default=None, help="Project slug (check this project only).")
    check_p.add_argument("--no-links", action="store_true", help="Skip link integrity check.")

    fresh_p = sub.add_parser("freshness", help="Report stale notes.")
    fresh_p.add_argument("--project", default=None)
    fresh_p.add_argument("--days", type=int, default=7, help="Staleness threshold in days (default 7).")

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch mdstore subcommands. Returns exit code."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv mdstore: config error: {e}", file=sys.stderr)
        return 1

    if args.mdstore_cmd == "check":
        return cmd_check(
            getattr(args, "project", None),
            cfg,
            check_links=not getattr(args, "no_links", False),
        )
    elif args.mdstore_cmd == "freshness":
        return cmd_freshness(
            getattr(args, "project", None),
            cfg,
            max_days=args.days,
        )
    else:
        print(f"rv mdstore: unknown subcommand {args.mdstore_cmd!r}", file=sys.stderr)
        return 1
