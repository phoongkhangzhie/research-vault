# SPDX-License-Identifier: AGPL-3.0-or-later
"""gitlib.py — shared git-utility functions for Research Vault.

This module houses low-level git primitives that are consumed by multiple
verb modules (git-health, control reconcile, etc.) so that each concern has
exactly one implementation.

Design constraint: stdlib only, no third-party deps, no network calls.

Exported helpers
----------------
squash_terminal_ids(repo, base="main")
    Return the set of id-tokens (lower-case) extracted from squash-merge
    subjects on ``base``.  A squash-merge commit carries a trailing ``(#N)``
    anchor (added automatically by GitHub's squash-and-merge button) — that
    anchor is the false-positive guard: random prose mentioning an id token
    has no trailing ``(#N)``.

    This is "Signal D" for git-health and the "Tertiary" signal for
    control-reconcile: it detects branches that were squash-merged and
    deleted (the dominant merge model for this repo).

    Shared here so both callers have exactly one squash-detection
    implementation — no divergence, no second parser to maintain.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Matches an id-token:  sr-N, sr-N-role-date, feat/sr-N, etc.
# Reused from controllib._ID_TOKEN_RE — same pattern, same purpose.
_ID_TOKEN_RE = re.compile(r"\b(sr-[a-z0-9]+(?:-[a-z0-9]+)*)\b", re.IGNORECASE)

# Trailing (#N) anchor — GitHub appends this automatically on squash-and-merge.
_PR_ANCHOR_RE = re.compile(r"\(#\d+\)\s*$")


def _git(args: list[str], repo: Path) -> str:
    """Run a git command in *repo* and return stdout (stripped).

    Returns empty string on non-zero exit (treat as no data, not an error).
    """
    r = subprocess.run(
        ["git", "-C", str(repo)] + args,
        capture_output=True, text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def squash_terminal_ids(repo: Path, base: str = "main") -> frozenset[str]:
    """Return id-tokens from squash-merged commits on *base* (Signal D / Tertiary).

    Scans non-merge commit subjects on *base* for lines ending with a ``(#N)``
    PR-number anchor.  GitHub's squash-and-merge button appends this anchor
    automatically, making it a reliable squash-merge marker without requiring
    the source branch to still exist.

    Id-tokens are extracted from the **whole commit subject** (not just the
    branch name, since the branch is typically deleted after squash-merge).

    Returns
    -------
    frozenset[str]
        Lower-cased id-tokens (e.g. ``{"sr-gd", "sr-cp"}``).

    Notes
    -----
    This is the same logic as ``LocalGitSource.get_terminal_set`` Tertiary
    signal (``status.py:177-187``) — lifted here so ``git_health.py`` can
    consume it without duplicating the squash parser.
    """
    if not repo.exists():
        return frozenset()

    # Resolve the base branch
    main_tip = _git(["rev-parse", base], repo)
    if not main_tip:
        # Try master
        main_tip = _git(["rev-parse", "master"], repo)
        if not main_tip:
            return frozenset()
        base = "master"

    squash_log = _git(["log", base, "--no-merges", "--format=%s"], repo)
    ids: set[str] = set()
    for line in squash_log.splitlines():
        if _PR_ANCHOR_RE.search(line):
            for m in _ID_TOKEN_RE.finditer(line):
                ids.add(m.group(1).lower())
    return frozenset(ids)
