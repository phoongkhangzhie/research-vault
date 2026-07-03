"""role.py — role-registry management for Research Vault.

When to use: ``rv role <subcommand>`` to list, view, or manage agent roles
registered in the project config registry.

All path resolution goes through Config — zero hardcoded paths or codenames.
Stdlib only.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .config import Config, load_config
from .project import DEFAULT_ROSTER


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_list(cfg: Config) -> int:
    """List all project slugs and their roster entries."""
    projects = cfg.projects
    if not projects:
        print("No projects registered (rv project add <name> to register one).")
        return 0
    print(f"{'Project':<24} {'Code':<12} {'Roster'}")
    print("-" * 60)
    for slug, rec in projects.items():
        code = rec.get("code", "?")
        roster = rec.get("roster", []) or DEFAULT_ROSTER
        roster_str = ", ".join(roster)
        print(f"  {slug:<22} {code:<12} {roster_str}")
    return 0


def cmd_show(slug: str, cfg: Config) -> int:
    """Show the full registry record for a project."""
    try:
        proj = cfg.project(slug)
    except KeyError as e:
        print(f"rv role show: {e}", file=sys.stderr)
        return 1
    import json
    print(json.dumps({slug: proj}, indent=2, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``role`` verb.

    When to use: ``rv role list`` or ``rv role show <project>`` to inspect
    agent roles in the project config registry.
    """
    desc = "Manage and inspect agent roles in the project registry."
    if parent is not None:
        p = parent.add_parser("role", help="Inspect agent roles in the config registry.", description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv role", description=desc)

    sub = p.add_subparsers(dest="role_cmd", required=True)

    sub.add_parser("list", help="List all projects and their rosters.")

    show_p = sub.add_parser("show", help="Show the registry record for a project.")
    show_p.add_argument("project", help="Project slug.")

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch role subcommands. Returns exit code."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv role: config error: {e}", file=sys.stderr)
        return 1

    if args.role_cmd == "list":
        return cmd_list(cfg)
    elif args.role_cmd == "show":
        return cmd_show(args.project, cfg)
    else:
        print(f"rv role: unknown subcommand {args.role_cmd!r}", file=sys.stderr)
        return 1
