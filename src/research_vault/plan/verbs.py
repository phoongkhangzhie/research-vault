# SPDX-License-Identifier: AGPL-3.0-or-later
"""plan/verbs.py — rv plan subcommand dispatcher.

When to use: use ``rv plan check <plan-note>`` to run the K-2 shape-lint on a
pre-registration plan note before the ``human-go-plan`` approval gate.
The K-2 lint is also run automatically (NON-OPTIONAL) inside ``rv plan freeze``
(promotion) — freeze is refused if any violations are present.

Subcommands:
  rv plan check <plan-note-path>
      Run the structural shape-lint (K-2, §5K.5.5):
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
      The K-2 shape-lint runs automatically first — freeze is
      BLOCKED if any violations are present (non-optional gate).
      Stores SHA-256 of (sorted child_id, stance, plan_role) tuples in
      run_state.meta["plan_freeze"]; checked at human-go-findings by ``rv dag approve``.

  rv plan freeze-harness <run-id> <plan-note-path> --scope <main<k>|shared>
                         --harness-commit <sha> [--notes-root <dir>]
      §5K.5.1: record the reviewed harness commit SHA(s) in
      the plan note's harness_commits: field and re-derive the K-3 hash to
      incorporate them.
      Run after each harness review gate (human-go-harness-main<k> or
      human-go-harness-shared) is approved and the engineer commits the harness.
      FAIL-CLOSED: requires a prior ``rv plan freeze`` (plan_freeze in meta).
      BASELINE GUARD: blocks if covers:/retries were edited since human-go-plan.
      Updates covers_hash to the harness-inclusive value; covers_retries_hash
      stays pinned at the plan-time baseline.
      A post-approval harness SHA swap is caught at human-go-findings by
      ``rv dag approve`` via verify_freeze_hash ("harness-commit drift").

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

    # freeze-harness
    fh_p = sub.add_parser(
        "freeze-harness",
        help=(
            "Record reviewed harness commit SHA in plan note "
            "and re-derive K-3 hash. Run after human-go-harness-<k> approval."
        ),
    )
    fh_p.add_argument(
        "run_id",
        metavar="<run-id>",
        help="DAG run id (from the manifest's run_id field).",
    )
    fh_p.add_argument(
        "plan_note",
        metavar="<plan-note-path>",
        help="Path to the plan master note (experiments/<id>-plan.md).",
    )
    fh_p.add_argument(
        "--scope",
        required=True,
        metavar="<main<k>|shared>",
        help=(
            "Scope of this harness entry: 'main1', 'main2', …, or 'shared'. "
            "Matches the DAG gate name 'human-go-harness-main<k>' or "
            "'human-go-harness-shared'."
        ),
    )
    fh_p.add_argument(
        "--harness-commit",
        required=True,
        metavar="<sha>",
        help="Git commit SHA of the reviewed harness (from engineer's ⟦RETURN⟧.provenance).",
    )
    fh_p.add_argument(
        "--notes-root",
        metavar="<dir>",
        default=None,
        help="Directory containing child experiment notes (default: auto from plan note's parent).",
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
    elif subcommand == "freeze-harness":
        return _run_freeze_harness(args)
    else:
        print(
            "rv plan: missing subcommand. "
            "Use `rv plan check <note>`, `rv plan tips`, "
            "`rv plan freeze <run-id> <note>`, `rv plan verify-freeze <run-id> <note>`, "
            "or `rv plan freeze-harness <run-id> <note> --scope <k> --harness-commit <sha>`.",
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

    The K-2 shape-lint (check_plan) is NON-OPTIONAL here.  Freeze
    BLOCKs if check_plan reports any violations — you cannot freeze a plan that
    fails the structural screen.
    """
    from .check import check_plan, PlanCheckError
    from .freeze import store_freeze_hash

    run_id = args.run_id
    plan_note = Path(args.plan_note)
    notes_root_arg = getattr(args, "notes_root", None)
    notes_root = Path(notes_root_arg) if notes_root_arg else None

    # --- K-2 gate (non-optional) ---
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

    # Resolve notes_root from plan-note's parent dir when not given.
    #
    # §B fix: the old default (cfg.notes_root / "experiments") was
    # wrong for projects that use a separate source_dir — the plan note and its
    # child stubs live under source_dir/experiments, NOT under notes_root/experiments.
    # Using plan_note.parent is correct: child stubs are scaffolded in the SAME
    # experiments/ dir as the plan note (rv experiment new writes them together).
    # This is also backwards-compatible: when notes_root IS notes_root/experiments,
    # plan_note.parent resolves to the same directory.
    if notes_root is None:
        notes_root = plan_note.parent

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


def _upsert_frontmatter_list_field(
    text: str,
    field: str,
    scope: str,
    value: str,
) -> str:
    """Upsert ``<scope>=<value>`` in a YAML inline list field in frontmatter.

    Behaviour:
    - Field absent     → inject ``field: [scope=value]`` before the closing ``---``.
    - Field present    → replace any existing ``scope=...`` entry with
                         ``scope=value``; add if not present.
    - All other frontmatter lines and the body are preserved BYTE-FOR-BYTE.

    The list is sorted alphabetically after upsert for determinism.

    Returns the updated text, or the original text if the frontmatter cannot
    be parsed (malformed or absent — no crash).
    """
    import re

    new_entry = f"{scope}={value}"

    def _rebuild_list(raw_list: str) -> str:
        """Parse inline list, upsert scope=value, return sorted inline list."""
        s = raw_list.strip()
        if s.startswith("[") and s.endswith("]"):
            s = s[1:-1]
        items = [item.strip() for item in s.split(",") if item.strip()]
        # Remove any existing entry for this scope
        items = [item for item in items if not item.startswith(f"{scope}=")]
        items.append(new_entry)
        items.sort()
        return "[" + ", ".join(items) + "]"

    # Try to replace an existing field line (single-line YAML inline list only)
    pattern = re.compile(
        r'^(' + re.escape(field) + r':\s*)(\[.*?\])\s*$',
        re.MULTILINE,
    )
    new_text, n = pattern.subn(
        lambda m: m.group(1) + _rebuild_list(m.group(2)),
        text,
        count=1,
    )
    if n > 0:
        return new_text

    # Field absent — inject before the closing "---" of the frontmatter block.
    if not text.startswith("---"):
        return text  # not a frontmatter note — return unchanged
    fm_end = text.find("\n---", 3)
    if fm_end == -1:
        return text  # malformed frontmatter — return unchanged
    injection = f"\n{field}: [{new_entry}]"
    return text[:fm_end] + injection + text[fm_end:]


def _run_verify_freeze(args: argparse.Namespace) -> int:
    """K-3: re-derive covers:-hash and compare to stored value (§5K.5.1).

    Fail CLOSED — exits 1 when no freeze is stored (a never-frozen
    run must NOT pass the K-3 gate silently).

    Caller-invariant — notes_root is used ONLY as an explicit
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


def _run_freeze_harness(args: argparse.Namespace) -> int:
    """Record reviewed harness commit SHA in plan note + re-hash (§5K.5.1).

    Flow:
    1. Load run; FAIL-CLOSED if plan_freeze absent (requires prior rv plan freeze).
    2. Baseline guard: recompute harness-excluded hash; compare to stored
       covers_retries_hash; BLOCK if they differ (covers:/retries edited since
       human-go-plan — the plan must not drift between freeze and freeze-harness).
    3. Upsert harness_commits[scope]=sha in the plan frontmatter (preserves all
       other frontmatter+body bytes).
    4. Re-run store_freeze_hash to update covers_hash to the harness-inclusive
       value; covers_retries_hash is naturally re-derived to the same value
       (covers/retries unchanged, harness-free path).
    5. Print the updated hash + scope.
    """
    import hashlib

    from .freeze import (
        _build_covers_canonical,
        store_freeze_hash,
        verify_freeze_hash,
    )

    run_id = args.run_id
    plan_note = Path(args.plan_note)
    scope = args.scope
    harness_sha = args.harness_commit
    notes_root_arg = getattr(args, "notes_root", None)
    notes_root = Path(notes_root_arg) if notes_root_arg else None

    # Default notes_root: plan note's parent directory (same as rv plan freeze)
    if notes_root is None:
        notes_root = plan_note.parent

    try:
        from research_vault.config import load_config
        from research_vault.dag.store import RunStore
        cfg = load_config()
        store = RunStore.from_config(cfg)
    except Exception as e:
        print(f"rv plan freeze-harness: config/store error: {e}", file=sys.stderr)
        return 1

    # --- Step 1: FAIL-CLOSED if never frozen ---
    try:
        run_state = store.load(run_id)
    except Exception as e:
        print(f"rv plan freeze-harness: cannot load run {run_id!r}: {e}", file=sys.stderr)
        return 1

    plan_freeze = run_state.meta.get("plan_freeze")
    if not plan_freeze:
        print(
            f"rv plan freeze-harness: BLOCKED — run {run_id!r} has no plan_freeze "
            f"(run `rv plan freeze {run_id} {plan_note}` first).",
            file=sys.stderr,
        )
        return 1

    stored_retries_hash = plan_freeze.get("covers_retries_hash")
    if stored_retries_hash is None:
        print(
            f"rv plan freeze-harness: BLOCKED — plan_freeze for run {run_id!r} has no "
            f"covers_retries_hash (legacy freeze format).  Re-run `rv plan freeze` "
            f"to establish the baseline before freeze-harness.",
            file=sys.stderr,
        )
        return 1

    # --- Step 2: Baseline guard — covers:/retries must not have changed ---
    # Load manifest_nodes via the freeze module's helper
    from .freeze import _load_manifest_nodes as _lmn
    manifest_nodes = _lmn(run_state.manifest_path)

    canon_no_harness = _build_covers_canonical(
        plan_note,
        notes_root=notes_root,
        manifest_nodes=manifest_nodes,
        include_harness=False,
    )
    if canon_no_harness is None:
        print(
            f"rv plan freeze-harness: cannot read plan note {plan_note} — "
            f"check path and permissions.",
            file=sys.stderr,
        )
        return 1

    current_retries_hash = hashlib.sha256(canon_no_harness.encode("utf-8")).hexdigest()
    if current_retries_hash != stored_retries_hash:
        print(
            f"rv plan freeze-harness: BLOCKED — covers:/retries edited since "
            f"human-go-plan.  Stored baseline {stored_retries_hash[:16]}… ≠ "
            f"current {current_retries_hash[:16]}….  "
            f"A covers:/retries edit after the plan gate is a pre-registration "
            f"integrity violation — issue a new pre-registration rather than "
            f"patching the harness.",
            file=sys.stderr,
        )
        return 1

    # --- Step 3: Upsert harness_commits[scope]=sha in plan frontmatter ---
    try:
        plan_text = plan_note.read_text(encoding="utf-8")
    except OSError as e:
        print(f"rv plan freeze-harness: cannot read plan note: {e}", file=sys.stderr)
        return 1

    updated_text = _upsert_frontmatter_list_field(
        plan_text, "harness_commits", scope, harness_sha
    )

    try:
        plan_note.write_text(updated_text, encoding="utf-8")
    except OSError as e:
        print(f"rv plan freeze-harness: cannot write plan note: {e}", file=sys.stderr)
        return 1

    # --- Step 4: Re-run store_freeze_hash to update covers_hash ---
    try:
        store_freeze_hash(store, run_id, plan_note, notes_root=notes_root)
    except Exception as e:
        print(f"rv plan freeze-harness: {e}", file=sys.stderr)
        return 1

    # --- Step 5: Report ---
    run_state2 = store.load(run_id)
    new_hash = run_state2.meta.get("plan_freeze", {}).get("covers_hash", "?")
    print(
        f"rv plan freeze-harness: OK — harness_commits[{scope}]={harness_sha[:12]}… "
        f"recorded in run {run_id!r}."
    )
    print(f"  updated covers_hash: {new_hash}")
    return 0
