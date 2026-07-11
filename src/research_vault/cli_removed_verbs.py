# SPDX-License-Identifier: AGPL-3.0-or-later
"""cli_removed_verbs.py — D1 (verb consolidation, HARD-REMOVE — the
operator resolved this deliberately): shared redirect-breadcrumb stub for
a step-verb collapsed into DAG node-execution.

Design of record: internal design note.
D1 — "Delete the 8 step-verbs from the CLI outright; they survive only as
importable functions the DAG node-execution calls... the surface goes fully
clean, no deprecation aliases." mandates a redirect breadcrumb so a
fresh Alfred doesn't keep reaching for the old verb out of habit.

This is a HARD removal, not a working alias: the stub takes any args
(``nargs=REMAINDER``) and always exits 2 with a message pointing at the
DAG node-op / control-verb equivalent. It never performs the old behavior.

Used by research.py / review/verbs.py / manuscript/verbs.py for the 8
collapsed step-verbs: sweep, cited-by, references, review-expand,
review-coverage, review-relations, manuscript-expand, manuscript-review.

Stdlib only.
"""
from __future__ import annotations

import argparse
import sys


def add_removed_verb_stub(
    sub: "argparse._SubParsersAction",  # type: ignore[type-arg]
    name: str,
    *,
    op_or_transition: str,
    redirect: str,
) -> None:
    """Register a stub subparser for a HARD-REMOVED step-verb.

    Args:
        sub:              the parent subparsers action.
        name:              the removed verb's old name (e.g. "sweep").
        op_or_transition:  what it collapsed INTO (e.g. "the 'sweep' tool
                            node-op, invoked by review-search").
        redirect:          the command Alfred should reach for instead
                            (e.g. "rv dag run <phase1-manifest>").
    """
    p = sub.add_parser(
        name,
        help=f"REMOVED — collapsed into {op_or_transition} (D1, verb consolidation).",
        description=(
            f"This verb was HARD-REMOVED (D1, verb consolidation). "
            f"It collapsed into {op_or_transition} — the DAG runner invokes "
            f"it IN-PROCESS when it executes the node; you do not choose it. "
            f"Use: {redirect}"
        ),
    )
    p.add_argument("_removed_args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    p.set_defaults(_rv_removed_verb=(name, op_or_transition, redirect))


def removed_verb_message(name: str, op_or_transition: str, redirect: str) -> str:
    return (
        f"rv {name}: REMOVED (D1, verb consolidation) — this collapsed into "
        f"{op_or_transition}. The DAG runner invokes it IN-PROCESS when it "
        f"executes the node; it is not a verb you choose by hand.\n"
        f"Use instead: {redirect}"
    )


def run_removed_verb_stub(args: argparse.Namespace) -> int:
    """Dispatch target for any registered removed-verb stub. Always exits 2
    (a distinct, greppable code — never 0, never a silent success-shaped 1)."""
    name, op_or_transition, redirect = getattr(
        args, "_rv_removed_verb", ("<unknown>", "a DAG node-op", "rv dag templates")
    )
    print(removed_verb_message(name, op_or_transition, redirect), file=sys.stderr)
    return 2
