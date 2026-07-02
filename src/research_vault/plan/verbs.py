"""plan/verbs.py — rv plan subcommand dispatcher (SR-PLAN-1).

When to use: use ``rv plan check <plan-note>`` to run the K-2 shape-lint on a
pre-registration plan note before the ``human-go-plan`` approval gate.

Subcommands:
  rv plan check <plan-note-path>
      Run the structural shape-lint (K-2, §5K.5.5):
        - branch-presence: every diagnosis table row has a named conclusion +
          committed action (no empty cells, no 'fallback', no 'TBD').
        - one-component-per-ablation: 'Component manipulated:' lines must not
          list multiple components.
      Exit 0 on pass; exit 1 with violations printed on fail.
      This is a REJECTS-ONLY screen (charter §9): pass does NOT certify the plan;
      the plan-critic (Argus) judges semantic completeness.

  rv plan tips [--key <key>]
      Print the plan_tips seam content (Ada's default or adopter override).
      Use --key to print a single tip key.
      Useful for debugging adopter overrides and wiring the plan node's spec.

Stdlib only.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Build parser
# ---------------------------------------------------------------------------

def build_parser(parent: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = parent.add_parser(
        "plan",
        help="Pre-registration plan lint + plan-tips seam. "
             "Use `rv plan check <note>` before human-go-plan approval.",
    )
    sub = p.add_subparsers(dest="plan_subcommand", metavar="<subcommand>")

    # check
    check_p = sub.add_parser(
        "check",
        help="K-2 shape-lint: branch-presence + one-component-per-ablation.",
    )
    check_p.add_argument(
        "plan_note",
        metavar="<plan-note-path>",
        help="Path to the plan master note (experiments/<id>-plan.md).",
    )

    # tips
    tips_p = sub.add_parser(
        "tips",
        help="Print plan_tips seam content (Ada's defaults or adopter override).",
    )
    tips_p.add_argument(
        "--key",
        metavar="<key>",
        default=None,
        help="Print only this tip key. Omit to print all keys.",
    )

    return p


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    subcommand = getattr(args, "plan_subcommand", None)

    if subcommand == "check":
        return _run_check(args)
    elif subcommand == "tips":
        return _run_tips(args)
    else:
        print(
            "rv plan: missing subcommand. Use `rv plan check <note>` or `rv plan tips`.",
            file=sys.stderr,
        )
        return 1


def _run_check(args: argparse.Namespace) -> int:
    """Run K-2 shape-lint on the plan master note."""
    from .check import check_plan, PlanCheckError

    plan_note = Path(args.plan_note)
    try:
        violations = check_plan(plan_note)
    except PlanCheckError as e:
        print(f"rv plan check: {e}", file=sys.stderr)
        return 1

    if violations:
        print(f"rv plan check: FAIL — {len(violations)} violation(s):")
        for v in violations:
            print(f"  - {v}")
        return 1

    print(f"rv plan check: OK — {plan_note.name} passes K-2 shape-lint.")
    return 0


def _run_tips(args: argparse.Namespace) -> int:
    """Print plan_tips seam content."""
    from .style import get_plan_tips, PLAN_TIPS_KEYS

    try:
        from research_vault.config import load_config
        cfg = load_config()
    except Exception:
        cfg = None

    tips = get_plan_tips(cfg)
    key = getattr(args, "key", None)

    if key is not None:
        if key not in PLAN_TIPS_KEYS:
            print(
                f"rv plan tips: unknown key {key!r}. "
                f"Valid keys: {sorted(PLAN_TIPS_KEYS)}",
                file=sys.stderr,
            )
            return 1
        print(f"[{key}]")
        print(tips[key])
        return 0

    for k in sorted(tips):
        print(f"[{k}]")
        print(tips[k])
        print()
    return 0
