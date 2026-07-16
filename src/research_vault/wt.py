# SPDX-License-Identifier: AGPL-3.0-or-later
"""wt.py — git worktree management for Research Vault.

When to use: ``rv wt <subcommand>`` to create, list, or remove git worktrees
for project task branches.  Anti-patterns this prevents: committed-to-main
directly · never made a worktree · working on main instead of an isolated
branch.

Multi-repo support (GD-D6):
  By default, worktrees are created for the framework repo (instance_root).
  Use ``rv wt add <task> --project <slug>`` to create a worktree in a
  project repo's ``<source_dir>-wt/`` directory.

Crew identity (``--as <role>``, GD-D6):
  Passing ``--as <role>`` sets ``git config user.email`` in the new worktree
  to ``<role>@<crew-domain>`` (config key: ``crew.identity_domain``; default
  placeholder: ``example.invalid``).  The worktree identity is set by
  construction, killing the "forgot to activate the role" bug.

Environment overrides (for tests):
  RV_WT_HOME   path to the worktrees directory (default: <repo>-wt)

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

def _wt_home_for(repo: Path, cfg: Config | None = None) -> Path:
    """Return the worktrees home directory for *repo*.

    Default: ``<repo>-wt`` (sibling, never nested inside repo).
    Override: ``RV_WT_HOME`` env var (test isolation; takes priority).
    """
    env = os.environ.get("RV_WT_HOME")
    if env:
        return Path(env)
    return Path(str(repo) + "-wt")


def _resolve_repo(cfg: Config, project: str | None) -> Path:
    """Return the target repo path.

    If *project* is given, resolves the project's ``source_dir``.
    Otherwise returns the instance root (framework repo).
    """
    if not project:
        return cfg.instance_root
    try:
        proj = cfg.project(project)
    except KeyError as e:
        raise RuntimeError(str(e)) from e
    src = proj.get("source_dir")
    if not src:
        raise RuntimeError(
            f"Project {project!r} has no source_dir in config. "
            f"Add source_dir to [projects.{project}]."
        )
    return Path(src).expanduser().resolve()


def _crew_domain(cfg: Config) -> str:
    """Return the crew identity domain (config-driven; never hardcoded).

    Config key: ``[crew] identity_domain``.
    Default: ``example.invalid`` (safe placeholder for the public repo).

    LEAKAGE RULE: the real domain lives ONLY in private instance config.
    The default ``example.invalid`` must appear in all public file content.
    """
    crew_cfg = cfg._raw.get("crew", {})
    return crew_cfg.get("identity_domain", "example.invalid")


def _set_worktree_identity(wt_path: Path, role: str, cfg: Config) -> None:
    """Set git user.email and user.name in the worktree for the given role.

    Format: user.email = <role>@<crew-domain>
            user.name  = <Role title-case> (rv crew)

    This is the by-construction identity fix: no "activate-step to forget".
    """
    domain = _crew_domain(cfg)
    email = f"{role}@{domain}"
    name = f"{role.title()} (rv crew)"

    subprocess.run(
        ["git", "-C", str(wt_path), "config", "user.email", email],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(wt_path), "config", "user.name", name],
        check=True, capture_output=True,
    )


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

def cmd_add(
    task: str,
    cfg: Config,
    *,
    project: str | None = None,
    role: str | None = None,
) -> str:
    """Create a new worktree for <task> on branch feat/<task>. Returns the path.

    Parameters
    ----------
    project:
        If given, use the project repo's source_dir instead of instance_root.
        Worktree home is ``<source_dir>-wt``.
    role:
        If given, set git user.email/user.name in the new worktree to the
        crew identity for this role (kills the "forgot to activate" bug).
    """
    repo = _resolve_repo(cfg, project)
    wt_home = _wt_home_for(repo, cfg)
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

    # Set crew identity by construction (--as <role>)
    if role:
        _set_worktree_identity(path, role, cfg)
        domain = _crew_domain(cfg)
        print(f"Worktree identity: {role}@{domain}")

    print(f"Created worktree: {path}")
    print(f"Branch: {branch}")
    if project:
        print(f"Project repo: {repo}")
    return str(path)


def cmd_list(cfg: Config, *, project: str | None = None) -> int:
    """List all worktrees for the framework repo or a project repo."""
    repo = _resolve_repo(cfg, project)
    try:
        r = _run(["git", "-C", str(repo), "worktree", "list"], capture=True)
        print(r.stdout, end="")
    except subprocess.CalledProcessError as e:
        print(f"rv wt list: git error: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_remove(task: str, cfg: Config, *, project: str | None = None) -> int:
    """Remove the worktree matching <task>."""
    repo = _resolve_repo(cfg, project)
    wt_home = _wt_home_for(repo, cfg)

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


def cmd_clean(cfg: Config, *, project: str | None = None) -> int:
    """Remove all non-main worktrees for the repo."""
    repo = _resolve_repo(cfg, project)
    wt_home = _wt_home_for(repo, cfg)

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
    Use ``--project <slug>`` for a project repo's source_dir.
    Use ``--as <role>`` to set the worktree's git identity by construction.
    Anti-patterns this prevents: committed-to-main / never-made-a-worktree.
    """
    desc = (
        "Manage git worktrees for task branches. "
        "Default: framework repo (instance_root). "
        "Use --project to target a registered project repo. "
        "Use --as <role> to set the crew git identity in the worktree."
    )
    if parent is not None:
        p = parent.add_parser("wt", help="Manage git worktrees.", description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv wt", description=desc)

    sub = p.add_subparsers(dest="wt_cmd", required=True)

    # add
    add_p = sub.add_parser("add", help="Create a worktree for a task on feat/<task>.")
    add_p.add_argument("task", help="Task slug (branch will be feat/<task>).")
    add_p.add_argument(
        "--project", default=None, metavar="SLUG",
        help="Target a registered project repo's source_dir (default: framework repo).",
    )
    add_p.add_argument(
        "--as", dest="role", default=None, metavar="ROLE",
        help=(
            "Set git user.email/name in the new worktree to the crew identity for ROLE "
            "(format: <role>@<crew.identity_domain>). Kills the 'forgot to activate' bug."
        ),
    )

    # list
    list_p = sub.add_parser("list", help="List all worktrees for this instance (or --project repo).")
    list_p.add_argument(
        "--project", default=None, metavar="SLUG",
        help="Target a registered project repo.",
    )

    # remove
    rm_p = sub.add_parser("remove", help="Remove the worktree matching <task>.")
    rm_p.add_argument("task", help="Task slug (matches the worktree directory prefix).")
    rm_p.add_argument(
        "--project", default=None, metavar="SLUG",
        help="Target a registered project repo.",
    )

    # clean
    clean_p = sub.add_parser("clean", help="Remove all non-main worktrees.")
    clean_p.add_argument(
        "--project", default=None, metavar="SLUG",
        help="Target a registered project repo.",
    )

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch wt subcommands. Returns exit code."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv wt: config error: {e}", file=sys.stderr)
        return 1

    project = getattr(args, "project", None)

    try:
        if args.wt_cmd == "add":
            cmd_add(args.task, cfg, project=project, role=getattr(args, "role", None))
            return 0
        elif args.wt_cmd == "list":
            return cmd_list(cfg, project=project)
        elif args.wt_cmd == "remove":
            return cmd_remove(args.task, cfg, project=project)
        elif args.wt_cmd == "clean":
            return cmd_clean(cfg, project=project)
        else:
            print(f"rv wt: unknown subcommand {args.wt_cmd!r}", file=sys.stderr)
            return 1
    except RuntimeError as e:
        print(f"rv wt: {e}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"rv wt: git error: {e}", file=sys.stderr)
        return 1
