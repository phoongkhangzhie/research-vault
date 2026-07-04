"""plan/verbs.py — rv plan subcommand dispatcher (SR-PLAN-1/SR-PLAN-2).

When to use: use ``rv plan check <plan-note>`` to run the K-2 shape-lint on a
pre-registration plan note before the ``human-go-plan`` approval gate.
The K-2 lint is also run automatically (NON-OPTIONAL) inside ``rv plan freeze``
(SR-PLAN-2 promotion) — freeze is refused if any violations are present.

Subcommands:
  rv plan check <plan-note-path>
      Run the structural shape-lint (K-2, §5K.5.5 + SR-PLAN-2):
        - branch-presence: every diagnosis table row has a named conclusion +
          committed action (no empty cells, no 'fallback', no 'TBD').
        - one-component-per-ablation: 'Component manipulated:' lines must not
          list multiple components.
        - covers-id convention: covers: entries must be bare IDs, not
          path-prefixed (e.g. 'q1-main1', not 'experiments/q1-main1').
      Exit 0 on pass; exit 1 with violations printed on fail.
      This is a REJECTS-ONLY screen (charter §9): pass does NOT certify the plan;
      the plan-critic (reviewer) judges semantic completeness.

  rv plan tips [--key <key>]
      Print the plan_tips seam content (researcher's default or adopter override).
      Use --key to print a single tip key.
      Useful for debugging adopter overrides and wiring the plan node's spec.

  rv plan freeze <run-id> <plan-note-path> [--notes-root <dir>]
      K-3 (§5K.5.1): hash the frozen covers:-set into the DAG run state.
      Run immediately after ``rv dag approve <run-id> human-go-plan``.
      SR-PLAN-2: the K-2 shape-lint runs automatically first — freeze is
      BLOCKED if any violations are present (non-optional gate).
      Stores SHA-256 of (sorted child_id, stance, plan_role) tuples in
      run_state.meta["plan_freeze"]; checked at human-go-findings by ``rv dag approve``.

  rv plan verify-freeze <run-id> <plan-note-path> [--notes-root <dir>]
      Re-derive the covers:-hash and compare to the stored value.
      Exit 0 on match (or no freeze stored); exit 1 on MISMATCH.
      The ``rv dag approve`` command for human-go-findings runs this automatically
      when a freeze hash is present in meta.

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
        help="Print plan_tips seam content (researcher's defaults or adopter override).",
    )
    tips_p.add_argument(
        "--key",
        metavar="<key>",
        default=None,
        help="Print only this tip key. Omit to print all keys.",
    )

    # freeze (K-3)
    freeze_p = sub.add_parser(
        "freeze",
        help="K-3: hash the covers:-freeze-set into DAG run state at human-go-plan.",
    )
    freeze_p.add_argument(
        "run_id",
        metavar="<run-id>",
        help="DAG run id (from the manifest's run_id field).",
    )
    freeze_p.add_argument(
        "plan_note",
        metavar="<plan-note-path>",
        help="Path to the plan master note (experiments/<id>-plan.md).",
    )
    freeze_p.add_argument(
        "--notes-root",
        metavar="<dir>",
        default=None,
        help="Directory containing child experiment notes (default: auto from config).",
    )

    # verify-freeze (K-3 re-verify)
    vf_p = sub.add_parser(
        "verify-freeze",
        help="K-3: re-derive covers:-hash and compare to stored value.",
    )
    vf_p.add_argument(
        "run_id",
        metavar="<run-id>",
        help="DAG run id.",
    )
    vf_p.add_argument(
        "plan_note",
        metavar="<plan-note-path>",
        help="Path to the plan master note.",
    )
    vf_p.add_argument(
        "--notes-root",
        metavar="<dir>",
        default=None,
        help="Directory containing child experiment notes.",
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
    elif subcommand == "freeze":
        return _run_freeze(args)
    elif subcommand == "verify-freeze":
        return _run_verify_freeze(args)
    else:
        print(
            "rv plan: missing subcommand. "
            "Use `rv plan check <note>`, `rv plan tips`, "
            "`rv plan freeze <run-id> <note>`, or `rv plan verify-freeze <run-id> <note>`.",
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


def _run_freeze(args: argparse.Namespace) -> int:
    """K-3: hash covers:-freeze-set into the DAG run state (§5K.5.1).

    Run immediately after ``rv dag approve <run_id> human-go-plan``.

    SR-PLAN-2: the K-2 shape-lint (check_plan) is NON-OPTIONAL here.  Freeze
    BLOCKs if check_plan reports any violations — you cannot freeze a plan that
    fails the structural screen.
    """
    from .check import check_plan, PlanCheckError
    from .freeze import store_freeze_hash

    run_id = args.run_id
    plan_note = Path(args.plan_note)
    notes_root_arg = getattr(args, "notes_root", None)
    notes_root = Path(notes_root_arg) if notes_root_arg else None

    # --- K-2 gate (non-optional, SR-PLAN-2) ---
    # Run the structural shape-lint before storing the hash.  A violation here
    # means the plan is structurally incomplete; the freeze is refused until
    # the plan is fixed and rv plan freeze is re-run.
    try:
        violations = check_plan(plan_note)
    except PlanCheckError as e:
        print(f"rv plan freeze: K-2 lint error — {e}", file=sys.stderr)
        return 1

    if violations:
        print(
            f"rv plan freeze: BLOCKED — K-2 shape-lint has {len(violations)} violation(s). "
            f"Fix the plan and re-run rv plan freeze.",
            file=sys.stderr,
        )
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1

    # Resolve notes_root from config if not given
    if notes_root is None:
        try:
            from research_vault.config import load_config
            cfg = load_config()
            notes_root = cfg.notes_root / "experiments"
        except Exception:
            pass  # Fall through — freeze.py handles None (uses MISSING sentinels)

    try:
        from research_vault.config import load_config
        from research_vault.dag.store import RunStore
        cfg = load_config()
        store = RunStore.from_config(cfg)
    except Exception as e:
        print(f"rv plan freeze: config/store error: {e}", file=sys.stderr)
        return 1

    try:
        store_freeze_hash(store, run_id, plan_note, notes_root=notes_root)
    except Exception as e:
        print(f"rv plan freeze: {e}", file=sys.stderr)
        return 1

    print(
        f"rv plan freeze: OK — covers:-hash stored in run {run_id!r} meta "
        f"(plan note: {plan_note.name}, notes_root: {notes_root})."
    )
    return 0


def _run_verify_freeze(args: argparse.Namespace) -> int:
    """K-3: re-derive covers:-hash and compare to stored value (§5K.5.1).

    SR-FREEZE-FIX: fail CLOSED — exits 1 when no freeze is stored (a never-frozen
    run must NOT pass the K-3 gate silently).

    SR-FREEZE-FIX: caller-invariant — notes_root is used ONLY as an explicit
    re-pin override when the stored pin is absent (legacy meta back-compat).
    The stored pin in plan_freeze["notes_root"] takes precedence; the old
    config auto-resolve (cfg.notes_root/"experiments") has been REMOVED because
    it was the source of the non-reproducibility bug (different callers with
    different configs got different verdicts on the same untampered artifact).

    Returns 0 on hash match; 1 on mismatch, not-frozen, or error.
    """
    from .freeze import verify_freeze_hash

    run_id = args.run_id
    plan_note = Path(args.plan_note)
    notes_root_arg = getattr(args, "notes_root", None)
    # Accept --notes-root as an explicit re-pin override only (legacy back-compat).
    # Do NOT auto-resolve from config — that was the non-reproducibility bug.
    notes_root = Path(notes_root_arg) if notes_root_arg else None

    try:
        from research_vault.config import load_config
        from research_vault.dag.store import RunStore
        cfg = load_config()
        store = RunStore.from_config(cfg)
    except Exception as e:
        print(f"rv plan verify-freeze: config/store error: {e}", file=sys.stderr)
        return 1

    try:
        # require_frozen=True (default): exit 1 when no freeze stored.
        ok, msg = verify_freeze_hash(
            store, run_id, plan_note,
            notes_root=notes_root,
            require_frozen=True,
        )
    except Exception as e:
        print(f"rv plan verify-freeze: {e}", file=sys.stderr)
        return 1

    if ok:
        print(f"rv plan verify-freeze: OK — covers:-hash matches for run {run_id!r}.")
        return 0
    else:
        print(f"rv plan verify-freeze: FAIL — {msg}", file=sys.stderr)
        return 1
