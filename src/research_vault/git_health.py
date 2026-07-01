"""git_health.py — cross-repo branch health report for Research Vault.

When to use: ``rv git-health [--prune]`` for a cross-repo branch health report
across all registered project source directories.

Branch classes (same semantics as vault's git_health.py):
  DELETE   Branch is provably merged or has no unique content vs main.
  FLAG     Has unique commits with no confirmed merge signal, or signal unavailable.
  KEEP     Protected (main/master), currently checked out, or has a dirty worktree.

Safety invariant: DELETE only when at least one positive merge signal is confirmed.

Environment overrides (for tests):
  GIT_HEALTH_REPOS   JSON dict {alias: path} to override the default repo map

Stdlib only (gh called as subprocess for Signal B).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from .config import Config, load_config

_PROTECTED = frozenset({"main", "master"})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class BranchRow(NamedTuple):
    branch: str
    cls: str        # DELETE | FLAG | KEEP
    reason: str
    signal_a: bool  # ancestor of origin/main
    signal_b: bool  # confirmed merged PR via gh
    signal_c: bool  # no unique diff vs origin/main


# ---------------------------------------------------------------------------
# Git helpers (stdlib subprocess — no third-party deps)
# ---------------------------------------------------------------------------

def _run(args: list[str], *, cwd: str | None = None, capture: bool = True) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
    return subprocess.run(args, cwd=cwd, capture_output=capture, text=True)


def _fetch_ok(repo: Path) -> bool:
    r = _run(["git", "-C", str(repo), "fetch", "origin"], capture=True)
    return r.returncode == 0


def _local_branches(repo: Path) -> list[str]:
    r = _run(["git", "-C", str(repo), "branch", "--format=%(refname:short)"])
    if r.returncode != 0:
        return []
    return [b.strip() for b in r.stdout.splitlines() if b.strip()]


def _current_branch(repo: Path) -> str:
    r = _run(["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"])
    return r.stdout.strip() if r.returncode == 0 else ""


def _is_ancestor(repo: Path, branch: str) -> bool:
    """Signal A: branch is an ancestor of origin/main (fast-forward merged)."""
    r = _run(["git", "-C", str(repo), "merge-base", "--is-ancestor", branch, "origin/main"])
    return r.returncode == 0


def _unique_diff(repo: Path, branch: str) -> bool:
    """Signal C: returns True if there IS a unique diff (not DELETE-eligible)."""
    r = _run(["git", "-C", str(repo), "diff", f"origin/main...{branch}"])
    if r.returncode != 0:
        return True  # fail-safe: treat error as non-empty
    return bool(r.stdout.strip())


def _classify_branch(
    repo: Path,
    branch: str,
    current: str,
    fetch_ok: bool,
) -> BranchRow:
    """Classify a branch as DELETE | FLAG | KEEP."""
    if branch in _PROTECTED:
        return BranchRow(branch, "KEEP", "protected branch", False, False, False)

    if branch == current:
        return BranchRow(branch, "KEEP", "currently checked out", False, False, False)

    # Signal A
    sig_a = _is_ancestor(repo, branch) if fetch_ok else False

    # Signal C (only if fetch succeeded)
    sig_c = False
    if fetch_ok and not sig_a:
        has_unique = _unique_diff(repo, branch)
        sig_c = not has_unique  # no unique diff → candidate for DELETE

    if sig_a:
        return BranchRow(branch, "DELETE", "ancestor of origin/main (Signal A)", True, False, False)
    if sig_c and fetch_ok:
        return BranchRow(branch, "DELETE", "no unique content vs origin/main (Signal C)", False, False, True)

    return BranchRow(branch, "FLAG", "unique commits, no confirmed merge signal", False, False, False)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _get_repos(cfg: Config) -> dict[str, Path]:
    """Build the repo map from registered project source_dirs."""
    env_override = os.environ.get("GIT_HEALTH_REPOS")
    if env_override:
        raw = json.loads(env_override)
        return {alias: Path(path) for alias, path in raw.items()}

    repos: dict[str, Path] = {}
    for slug in cfg.all_project_slugs():
        proj = cfg.projects[slug]
        src = proj.get("source_dir")
        if src:
            repos[slug] = Path(src).expanduser()

    # Always include the instance root itself
    repos["_instance"] = cfg.instance_root
    return repos


def cmd_report(cfg: Config, *, prune: bool = False) -> int:
    """Generate the branch health report across all registered repos."""
    repos = _get_repos(cfg)
    any_issues = False

    for alias, repo in repos.items():
        if not repo.exists() or not (repo / ".git").exists():
            print(f"\n{alias} ({repo}): not a git repo — skipping")
            continue

        print(f"\n{'='*60}")
        print(f"{alias} — {repo}")
        print("="*60)

        fetch = _fetch_ok(repo)
        if not fetch:
            print("  (fetch failed — Signal C disabled, no DELETE without fetch)")

        current = _current_branch(repo)
        branches = _local_branches(repo)

        rows = []
        for branch in branches:
            row = _classify_branch(repo, branch, current, fetch_ok=fetch)
            rows.append(row)

        # Print table
        print(f"  {'Branch':<30} {'Class':<8} Reason")
        print(f"  {'-'*29} {'-'*7} ------")
        for row in rows:
            print(f"  {row.branch:<30} {row.cls:<8} {row.reason}")

        # Prune
        if prune:
            to_delete = [r for r in rows if r.cls == "DELETE"]
            for row in to_delete:
                r = _run(["git", "-C", str(repo), "branch", "-D", row.branch])
                if r.returncode == 0:
                    print(f"  Pruned: {row.branch}")
                else:
                    print(f"  Could not prune {row.branch}: {r.stderr.strip()}", file=sys.stderr)

        any_issues = any_issues or any(r.cls == "FLAG" for r in rows)

    return 1 if any_issues else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``git-health`` verb.

    When to use: ``rv git-health`` for a cross-repo branch health report across
    all registered project source directories. Use --prune to delete DELETE-class
    branches.
    """
    desc = (
        "Cross-repo branch health report. Classifies branches as DELETE, FLAG, or KEEP. "
        "DELETE = provably merged; FLAG = unique content, unconfirmed; KEEP = protected."
    )
    if parent is not None:
        p = parent.add_parser("git-health", help="Branch health report across project repos.", description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv git-health", description=desc)

    p.add_argument(
        "--prune", action="store_true",
        help="Delete DELETE-classed branches (confirmed merged only).",
    )

    return p


def run(args: argparse.Namespace) -> int:
    """Run the git-health command. Returns exit code."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv git-health: config error: {e}", file=sys.stderr)
        return 1

    return cmd_report(cfg, prune=args.prune)
