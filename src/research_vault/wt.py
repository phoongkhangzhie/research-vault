"""wt.py — git worktree management for Research Vault.

When to use: ``rv wt <subcommand>`` to create, list, or remove git worktrees
for project task branches.

All worktrees live under a sibling directory ``<instance_root>-wt/`` (never
nested inside the instance root). The instance root is resolved from Config.

Environment overrides (for tests):
  RV_WT_HOME   path to the worktrees directory (default: <instance_root>-wt)

Stdlib only.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from .config import Config, load_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wt_home(cfg: Config) -> Path:
    """Return the worktrees home directory.

    Default: ``<instance_root>-wt`` (sibling of instance_root, never nested).
    Override: ``RV_WT_HOME`` env var.
    """
    env = os.environ.get("RV_WT_HOME")
    if env:
        return Path(env)
    return Path(str(cfg.instance_root) + "-wt")


def _run(args: list[str], *, cwd: Path | None = None, check: bool = True,
         capture: bool = False) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=capture,
        text=True,
        check=check,
    )


def _run_out(args: list[str], *, cwd: Path | None = None) -> str:
    r = _run(args, cwd=cwd, check=True, capture=True)
    return r.stdout.strip()


def _short_sha(repo: Path) -> str:
    """Return the short SHA of origin/main or main."""
    for ref in ("origin/main", "main"):
        try:
            sha = _run_out(["git", "-C", str(repo), "rev-parse", "--short", ref])
            if sha:
                return sha
        except subprocess.CalledProcessError:
            continue
    raise RuntimeError(f"cannot resolve a main SHA in {repo}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_add(task: str, cfg: Config) -> str:
    """Create a new worktree for <task> on branch feat/<task>. Returns the path."""
    repo = cfg.instance_root
    wt_home = _wt_home(cfg)
    wt_home.mkdir(parents=True, exist_ok=True)

    # Fetch to update origin refs (no-op if no remote)
    _run(["git", "-C", str(repo), "fetch", "origin"], check=False)

    sha = _short_sha(repo)
    name = f"{task}-{sha}"
    branch = f"feat/{task}"
    path = wt_home / name

    # Determine base ref
    base = "origin/main"
    try:
        _run_out(["git", "-C", str(repo), "rev-parse", base])
    except subprocess.CalledProcessError:
        base = "main"

    # Create the worktree
    _run([
        "git", "-C", str(repo), "worktree", "add",
        str(path), "-b", branch, base,
    ])
    print(f"Created worktree: {path}")
    print(f"Branch: {branch}")
    return str(path)


def cmd_list(cfg: Config) -> int:
    """List all worktrees for the instance."""
    repo = cfg.instance_root
    try:
        r = _run(["git", "-C", str(repo), "worktree", "list"], capture=True)
        print(r.stdout, end="")
    except subprocess.CalledProcessError as e:
        print(f"rv wt list: git error: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_remove(task: str, cfg: Config) -> int:
    """Remove the worktree matching <task>."""
    repo = cfg.instance_root
    wt_home = _wt_home(cfg)

    # Find a directory matching <task>-*
    candidates = list(wt_home.glob(f"{task}-*")) if wt_home.exists() else []
    if not candidates:
        # Try exact match
        exact = wt_home / task
        if exact.exists():
            candidates = [exact]

    if not candidates:
        print(f"rv wt remove: no worktree found matching {task!r} in {wt_home}", file=sys.stderr)
        return 1
    if len(candidates) > 1:
        print(f"rv wt remove: multiple worktrees match {task!r}:", file=sys.stderr)
        for c in candidates:
            print(f"    {c}", file=sys.stderr)
        print("Specify the full name.", file=sys.stderr)
        return 1

    wt_path = candidates[0]
    try:
        _run(["git", "-C", str(repo), "worktree", "remove", str(wt_path), "--force"])
        print(f"Removed worktree: {wt_path}")
    except subprocess.CalledProcessError as e:
        print(f"rv wt remove: git error: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_clean(cfg: Config) -> int:
    """Remove all non-main worktrees for the instance."""
    repo = cfg.instance_root
    wt_home = _wt_home(cfg)

    if not wt_home.exists():
        print(f"No worktrees directory found at {wt_home}")
        return 0

    removed = 0
    for wt_path in sorted(wt_home.iterdir()):
        if not wt_path.is_dir():
            continue
        try:
            _run([
                "git", "-C", str(repo), "worktree", "remove", str(wt_path), "--force"
            ])
            print(f"Removed: {wt_path}")
            removed += 1
        except subprocess.CalledProcessError:
            print(f"  Could not remove (may be main): {wt_path}")

    print(f"Cleaned {removed} worktree(s).")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``wt`` verb.

    When to use: ``rv wt add <task>`` to create an isolated worktree on
    feat/<task> off origin/main. ``rv wt remove <task>`` to remove it on merge.
    """
    desc = "Manage git worktrees for task branches. Worktrees live in <instance_root>-wt/."
    if parent is not None:
        p = parent.add_parser("wt", help="Manage git worktrees.", description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv wt", description=desc)

    sub = p.add_subparsers(dest="wt_cmd", required=True)

    # add
    add_p = sub.add_parser("add", help="Create a worktree for a task on feat/<task>.")
    add_p.add_argument("task", help="Task slug (branch will be feat/<task>).")

    # list
    sub.add_parser("list", help="List all worktrees for this instance.")

    # remove
    rm_p = sub.add_parser("remove", help="Remove the worktree matching <task>.")
    rm_p.add_argument("task", help="Task slug (matches the worktree directory prefix).")

    # clean
    sub.add_parser("clean", help="Remove all non-main worktrees.")

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch wt subcommands. Returns exit code."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv wt: config error: {e}", file=sys.stderr)
        return 1

    try:
        if args.wt_cmd == "add":
            cmd_add(args.task, cfg)
            return 0
        elif args.wt_cmd == "list":
            return cmd_list(cfg)
        elif args.wt_cmd == "remove":
            return cmd_remove(args.task, cfg)
        elif args.wt_cmd == "clean":
            return cmd_clean(cfg)
        else:
            print(f"rv wt: unknown subcommand {args.wt_cmd!r}", file=sys.stderr)
            return 1
    except RuntimeError as e:
        print(f"rv wt: {e}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"rv wt: git error: {e}", file=sys.stderr)
        return 1
