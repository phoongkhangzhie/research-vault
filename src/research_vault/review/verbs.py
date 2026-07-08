"""review/verbs.py — rv review subcommand dispatcher (SR-LR-1 + SR-LR-2 + SR-GAP-ROUTE + SR-GAP-CLOSE, §5L).

When to use: use ``rv review new <project> <scope> --question '...'`` to start a
pre-registered, saturation-gated literature review.  ``rv review expand`` emits the
Phase-2 fan-out after the coverage-gate human-go.  ``rv review list`` enumerates
all reviews for a project.  ``rv review gap-scan`` detects typed research gaps from
the OKF corpus and/or a manuscript critic report (SR-LR-2 §5L.7, the loop-closer).
``rv review gap-scope`` (or the alias ``gap-route``) auto-authors the remedy scope:
literature (SR-LR-1) OR experiment (SR-PLAN-1), routed by error-asymmetry.

This is the ONLY path that creates the closed protocol-freeze + saturation-curve +
coverage-critic framework.  A hand-run literature scan gets none of these gates.

Subcommands (SR-LR-1):
  rv review <project> new <scope> --question "..."
      Create a review OKF note + reviews/<scope>/ artifact dir + Phase-1 DAG manifest.
      Phase-1 shape: review-scope → [HG:approve-protocol] → review-search
          → review-snowball → [HG:coverage-gate].
      ``review-scope`` MUST file a ``_protocol.md`` with a non-empty ``counter-position``
      field (L-2 gate, §5L.3) — ``review-search`` is gated on the protocol artifact.
      ``review-snowball`` runs an internal saturation loop (both forward + backward
      citation directions) and produces ``_corpus.md`` + ``_saturation.md``.

  rv review <project> expand <scope> [--corpus <path>]
      Emit Phase-2 manifest from the frozen ``_corpus.md`` after coverage-gate approval.
      One ``relate-<key>`` node per ``[NEW]`` citekey → ``review-synthesize``
      → ``review-coverage-critic`` (L-2: [BLOCK] on missing counter-position)
      → ``[HG:approve-review]``.
      Saves ``reviews/<scope>/phase2-dag.json``.

  rv review <project> list
      List all review pointer notes for the project.

  rv review <project> tips [--key <key>]
      Print the review_tips seam content (researcher's default or adopter override).

Subcommands (SR-LR-2 — the gap-driven pass):
  rv review <project> gap-scan [--threshold <n>]
      Detect typed research gaps from the OKF corpus (knowledge_void, contradictory,
      evaluation_void).
      Writes gaps/<id>.md for each new gap with a suggested_route: field (SR-GAP-ROUTE).
      Idempotent (does NOT re-create existing gaps).
      Surfaces a COUNT only — records are never inlined into the control bus.
      Human authorizes which gaps become targeted scopes (no auto-fire).

  rv review <project> gap-scope <gap-id> <scope> [--target {literature|experiment}]
      Auto-author a scope from a gap record: default target = gap.suggested_route.
      --target literature (default): Part-1 review scope (SR-LR-1 loop, unchanged).
      --target experiment (new): SR-PLAN-1 pre-registration plan (research question
        ← claim verbatim; covers: skeleton; diagnosis-table stub). No new mechanism.
      Emits the scope artifact + _gap-context.md.

  rv review <project> gap-route <gap-id> <scope> [--target {literature|experiment}]
      Thin alias for gap-scope (discoverability). Same behavior.
      Anti-pattern: do NOT call gap-route and expect an auto-fire — it authors a
      scope for human review; the run requires an explicit human-go.

  rv review <project> gap-list [--status <status>]
      List gap records for the project, optionally filtered by status.
      ``--status proven-open`` = the run-candidate queue (§5L.16).
      ``--status reopened`` = gaps that re-entered open-routing via structural signal.
      ``--status promoted`` = proven-open gaps promoted to manuscript contribution candidate.

  rv review <project> gap-close <gap-id> --status <status> [--by <note-ref>]
      Stamp a gap's closure status. status ∈ {closed-supported, closed-filled, proven-open}.
      A ``proven-open`` gap saturated without closing → run-candidate contribution.
      SR-GAP-CLOSE: --by is REQUIRED for closed-supported and closed-filled (charter §2:
      a closed gap with no closer is un-auditable). --by is REJECTED for proven-open
      (nothing closed it — that's the point). --by writes bidirectional edges:
        closed_by: <note-ref> in the gap FM + closes: <gap-id> in the closing note FM.
      Anti-pattern: do NOT gap-close a closed-* gap without --by — a closer-less closure
      is un-auditable and breaks the provenance chain (SR-GAP-CLOSE §5L.21(1)).

  rv review <project> gap-promote <gap-id> --to <ref>
      SR-GAP-CLOSE: promote a proven-open gap to 'promoted' status (human-only).
      Writes promoted_to: <ref> in the gap FM.
      Requires --to <manuscript-section/claim> (unauditable without a target).
      Anti-pattern: do NOT hand-write a contribution claim from a proven-open gap —
      run gap-promote first so the claim round-trips the SR-MS-2 support-matcher
      (the honesty backstop that polices its own promotions).

Anti-pattern: do NOT hand-collect papers without running ``rv review new`` — a
hand-collected corpus has no ``_protocol.md`` freeze, no saturation measurement,
and no rejects-only coverage critic.

Anti-pattern: do NOT auto-fire a gap-driven review pass — ``gap-scan`` is a
SCREEN that PROPOSES work; the human authorizes each targeted pass via ``gap-scope``
or ``gap-route`` (operator confirmed: no auto-fire, D-GAP-4).

Anti-pattern: do NOT hand-decide read-vs-run and hand-spin a lit pass or a plan —
run ``rv review gap-scope <project> <gap-id> <scope>``; it routes by error-asymmetry
and auto-authors the remedy scope (SR-GAP-ROUTE §5L.17 when-to-use).

Stdlib only.
sr: SR-LR-1, SR-LR-2, SR-GAP-ROUTE, SR-GAP-CLOSE
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Build parser
# ---------------------------------------------------------------------------

def build_parser(parent: "argparse._SubParsersAction | None" = None) -> argparse.ArgumentParser:
    """Build the argument parser for the ``review`` verb.

    When to use: use ``rv review new <project> <scope> --question '...'`` to scaffold
    a pre-registered, saturation-gated literature review with protocol-freeze,
    internal saturation loop, and coverage-critic gates (SR-LR-1, §5L.1).

    Anti-pattern: do NOT hand-collect papers without ``rv review new`` — the hand-run
    path has no protocol-freeze (anti-fishing), no saturation curve, and no rejects-only
    critic (the coverage gate cannot fire without the artifacts ``rv review new`` scaffolds).

    sr: SR-LR-1
    """
    desc = (
        "Staged, pre-registered, saturation-gated literature review loop (SR-LR-1).\n"
        "'rv review new' is the ONLY path that creates the protocol-freeze +\n"
        "saturation-curve + coverage-critic framework.\n"
        "Drive Phase-1 with: rv dag run reviews/<scope>/phase1-dag.json\n"
        "After coverage-gate: rv review <project> expand <scope> → Phase-2"
    )
    if parent is not None:
        p = parent.add_parser(
            "review",
            help="Staged literature review loop (pre-registered, saturation-gated).",
            description=desc,
        )
    else:
        p = argparse.ArgumentParser(prog="rv review", description=desc)

    p.add_argument("project", help="Project slug.")

    sub = p.add_subparsers(dest="review_cmd", required=True)

    # ── new ──────────────────────────────────────────────────────────────────
    new_p = sub.add_parser(
        "new",
        help=(
            "Create a review OKF note + reviews/<scope>/ dir + Phase-1 DAG manifest. "
            "Scaffolds the §5L.1 DAG: review-scope → [HG:approve-protocol] → "
            "review-search → review-snowball → [HG:coverage-gate]."
        ),
    )
    new_p.add_argument(
        "scope",
        metavar="<scope>",
        help="Review scope identifier slug (e.g. 'scope-llm-eval', 'scope-crosslingual').",
    )
    new_p.add_argument(
        "--question",
        required=True,
        metavar="QUESTION",
        help=(
            "The review research question. Stored in the review note and protocol. "
            "Example: 'What are the coverage limits of LLM-based evaluation benchmarks?'"
        ),
    )

    # ── expand ──────────────────────────────────────────────────────────────
    expand_p = sub.add_parser(
        "expand",
        help=(
            "Emit Phase-2 manifest from the frozen _corpus.md (post coverage-gate). "
            "One relate-<key> node per [NEW] citekey → synthesize → critic → approve-review."
        ),
    )
    expand_p.add_argument(
        "scope",
        metavar="<scope>",
        help="Review scope identifier (same as used in rv review new).",
    )
    expand_p.add_argument(
        "--corpus",
        metavar="<path>",
        default=None,
        help=(
            "Path to _corpus.md (default: reviews/<scope>/_corpus.md). "
            "Override for testing or if the file lives in a non-standard location."
        ),
    )

    # ── list ─────────────────────────────────────────────────────────────────
    sub.add_parser(
        "list",
        help="List review pointer notes for the project.",
    )

    # ── tips ─────────────────────────────────────────────────────────────────
    tips_p = sub.add_parser(
        "tips",
        help="Print review_tips seam content (researcher's defaults or adopter override).",
    )
    tips_p.add_argument(
        "--key",
        metavar="<key>",
        default=None,
        help="Print only this tip key. Omit to print all keys.",
    )

    # ── gap-scan ──────────────────────────────────────────────────────────────
    gap_scan_p = sub.add_parser(
        "gap-scan",
        help=(
            "Detect typed research gaps from the OKF corpus (SR-LR-2, §5L.7). "
            "Rejects-only screen — PROPOSES gaps, never auto-fires a pass. "
            "Surfaces a COUNT; run `rv review gap-scope` to author a targeted review."
        ),
    )
    gap_scan_p.add_argument(
        "--threshold",
        metavar="<n>",
        type=int,
        default=1,
        help=(
            "Support-degree threshold for Knowledge Void detection (D-GAP-2). "
            "A finding with backed_by count < threshold is flagged. Default: 1."
        ),
    )
    # ── gap-scope ─────────────────────────────────────────────────────────────
    gap_scope_p = sub.add_parser(
        "gap-scope",
        help=(
            "Auto-author a targeted scope from a gap record (§5L.7, SR-GAP-ROUTE §5L.16). "
            "--target literature: Part-1 review scope (default). "
            "--target experiment: SR-PLAN-1 pre-registration plan. "
            "Default target = gap.suggested_route (computed at gap-scan time)."
        ),
    )
    gap_scope_p.add_argument(
        "gap_id",
        metavar="<gap-id>",
        help="Gap record id (stem of the gaps/<id>.md note).",
    )
    gap_scope_p.add_argument(
        "scope",
        metavar="<scope>",
        help="Scope slug for the targeted review (e.g. 'scope-gap-kv-001').",
    )
    gap_scope_p.add_argument(
        "--target",
        choices=["literature", "experiment"],
        default=None,
        metavar="{literature|experiment}",
        help=(
            "Route target. 'literature': Part-1 review scope (SR-LR-1). "
            "'experiment': SR-PLAN-1 pre-registration plan. "
            "Omit to use the gap's suggested_route field (computed at scan time)."
        ),
    )

    # ── gap-route (alias for gap-scope — SR-GAP-ROUTE §5L.17 discoverability) ──
    gap_route_p = sub.add_parser(
        "gap-route",
        help=(
            "Thin alias for gap-scope (SR-GAP-ROUTE discoverability hook). "
            "Routes a gap to the appropriate remedy scope by error-asymmetry. "
            "When to use: do NOT hand-decide read-vs-run — run gap-route and confirm."
        ),
    )
    gap_route_p.add_argument(
        "gap_id",
        metavar="<gap-id>",
        help="Gap record id (stem of the gaps/<id>.md note).",
    )
    gap_route_p.add_argument(
        "scope",
        metavar="<scope>",
        help="Scope slug (e.g. 'scope-gap-ev-001').",
    )
    gap_route_p.add_argument(
        "--target",
        choices=["literature", "experiment"],
        default=None,
        metavar="{literature|experiment}",
        help="Override route target. Default = gap.suggested_route.",
    )

    # ── gap-list ──────────────────────────────────────────────────────────────
    gap_list_p = sub.add_parser(
        "gap-list",
        help=(
            "List gap records for the project (SR-GAP-ROUTE §5L.16 + SR-GAP-CLOSE §5L.20). "
            "--status proven-open shows the run-candidate queue; "
            "--status promoted shows promoted contribution candidates; "
            "--status reopened shows gaps that re-entered open-routing."
        ),
    )
    gap_list_p.add_argument(
        "--status",
        default=None,
        choices=["open", "closed-supported", "closed-filled", "proven-open",
                 "promoted", "reopened"],
        help=(
            "Filter by gap status. 'proven-open' = the run-candidate queue "
            "(gaps whose targeted lit pass saturated without closing). "
            "'promoted' = proven-open gaps promoted to manuscript candidate (SR-GAP-CLOSE). "
            "'reopened' = gaps that re-entered open-routing via structural reopen signal."
        ),
    )

    # ── gap-close ─────────────────────────────────────────────────────────────
    gap_close_p = sub.add_parser(
        "gap-close",
        help=(
            "Stamp a gap's closure status with provenance edge (§5L.8 + SR-GAP-CLOSE §5L.21(1)). "
            "--by is REQUIRED for closed-supported/closed-filled (charter §2); "
            "REJECTED for proven-open (nothing closed it). "
            "proven-open = targeted pass saturated without closing → candidate contribution."
        ),
    )
    gap_close_p.add_argument(
        "gap_id",
        metavar="<gap-id>",
        help="Gap record id (stem of the gaps/<id>.md note).",
    )
    gap_close_p.add_argument(
        "--status",
        required=True,
        choices=["closed-supported", "closed-filled", "proven-open"],
        help=(
            "Closure status. closed-supported: matcher flipped [ABSENT]→[SUPPORTS/PARTIAL]. "
            "closed-filled: support-degree crossed threshold / MOC filled. "
            "proven-open: saturated without closing → candidate contribution."
        ),
    )
    gap_close_p.add_argument(
        "--by",
        default=None,
        metavar="<note-ref>",
        help=(
            "SR-GAP-CLOSE: the OKF note that resolved this gap (e.g. 'literature/smith2024', "
            "'experiments/exp-001'). REQUIRED for closed-supported and closed-filled "
            "(a closed gap with no closer is un-auditable — charter §2). "
            "REJECTED for proven-open (nothing closed it). "
            "Writes bidirectional edges: closed_by: in the gap FM + closes: in the closing note FM."
        ),
    )

    # ── gap-promote ────────────────────────────────────────────────────────────
    gap_promote_p = sub.add_parser(
        "gap-promote",
        help=(
            "SR-GAP-CLOSE §5L.21(2): promote a proven-open gap to 'promoted' status (human-only). "
            "proven-open → promoted; writes promoted_to: <ref> in the gap FM. "
            "Requires --to <manuscript-section/claim>. "
            "Anti-pattern: do NOT hand-write a contribution from a proven-open gap — "
            "run gap-promote so the claim round-trips the SR-MS-2 support-matcher."
        ),
    )
    gap_promote_p.add_argument(
        "gap_id",
        metavar="<gap-id>",
        help="Gap record id (stem of the gaps/<id>.md note). Must be in proven-open status.",
    )
    gap_promote_p.add_argument(
        "--to",
        required=True,
        metavar="<ref>",
        help=(
            "Manuscript section or claim reference (e.g. 'manuscript/contributions', "
            "'manuscript/future-work'). Required — a promotion without a target is "
            "un-auditable (charter §2)."
        ),
    )

    # ── coverage ──────────────────────────────────────────────────────────────
    coverage_p = sub.add_parser(
        "coverage",
        help=(
            "Deterministic corpus-coverage check keyed by citekey: field (F16+F17). "
            "Source-of-truth: the frozen _corpus.md manifest. "
            "Identity: literature note citekey: frontmatter field (NOT filename stem). "
            "Reports: materialized (lit note exists), unmaterialized (missing), "
            "orphan (materialized but absent from all mocs/). "
            "When to use: run `rv review <project> coverage <scope>` to verify "
            "corpus coverage after Phase-2 relate-<key> nodes complete and before "
            "approve-review. Also: run from the coverage-gate node to confirm state. "
            "Anti-pattern: do NOT hand-stem-match filenames to corpus citekeys — "
            "a descriptive filename like zheng2023-pride-mc-selectors.md carrying "
            "citekey: zheng2023-pride is materialized, not orphan; stem-matching "
            "gives false-orphan flags."
        ),
    )
    coverage_p.add_argument(
        "scope",
        metavar="<scope>",
        help="Review scope identifier (same as used in rv review new).",
    )

    # ── relations (Wave 0 / PR-2) ────────────────────────────────────────────
    relations_p = sub.add_parser(
        "relations",
        help=(
            "Deterministic corpus-wide paper->paper typed-edge listing (PR-2). "
            "Reads the '## Related papers' body sections the relate-<key> fan-out "
            "emits (reciprocal/refutational/line-of-argument, mapped onto "
            "[SUPPORTS]/[CONTRADICTS]/[PARTIAL]/[EXTENDS])."
        ),
        description=(
            "When to use: run `rv review <project> relations <scope>` from "
            "review-synthesize (and review-coverage-critic) to TRAVERSE the "
            "carried comparative spine instead of re-deriving it from prose. "
            "Anti-pattern: do NOT hand-re-read every literature/ note to "
            "reconstruct which papers refute/extend which — the relate-<key> "
            "fan-out already discovered and typed these relations; this "
            "command surfaces them deterministically."
        ),
    )
    relations_p.add_argument(
        "scope",
        metavar="<scope>",
        help="Review scope identifier (same as used in rv review new).",
    )

    return p


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    """Dispatch review subcommands. Returns exit code."""
    subcommand = getattr(args, "review_cmd", None)

    if subcommand == "new":
        return _run_new(args)
    elif subcommand == "expand":
        return _run_expand(args)
    elif subcommand == "list":
        return _run_list(args)
    elif subcommand == "tips":
        return _run_tips(args)
    elif subcommand == "gap-scan":
        return _run_gap_scan(args)
    elif subcommand in ("gap-scope", "gap-route"):
        # gap-route is a thin alias for gap-scope (SR-GAP-ROUTE §5L.17 discoverability)
        return _run_gap_scope(args)
    elif subcommand == "gap-list":
        return _run_gap_list(args)
    elif subcommand == "gap-close":
        return _run_gap_close(args)
    elif subcommand == "gap-promote":
        return _run_gap_promote(args)
    elif subcommand == "coverage":
        return _run_coverage(args)
    elif subcommand == "relations":
        return _run_relations(args)
    else:
        print(
            "rv review: missing subcommand. "
            "Use `rv review <project> new <scope> --question '...'`, "
            "`rv review <project> expand <scope>`, "
            "`rv review <project> coverage <scope>`, "
            "`rv review <project> relations <scope>`, "
            "`rv review <project> list`, `rv review <project> tips`, "
            "`rv review <project> gap-scan`, `rv review <project> gap-scope [--target …]`, "
            "`rv review <project> gap-route [--target …]` (alias for gap-scope), "
            "`rv review <project> gap-list [--status …]`, "
            "`rv review <project> gap-close [--by <note-ref>]`, "
            "or `rv review <project> gap-promote --to <ref>`.",
            file=sys.stderr,
        )
        return 1


def _run_new(args: argparse.Namespace) -> int:
    """Create review note + Phase-1 DAG manifest."""
    from research_vault.config import load_config
    from research_vault.review import cmd_new

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv review new: config error: {e}", file=sys.stderr)
        return 1

    try:
        note_path, review_dir, manifest = cmd_new(
            args.project,
            args.scope,
            question=args.question,
            config=cfg,
        )
        n_nodes = len(manifest["nodes"])
        print(f"rv review: created note: {note_path}")
        print(f"rv review: artifact dir: {review_dir}")
        print(f"rv review: Phase-1 manifest: {review_dir / 'phase1-dag.json'}")
        print(f"rv review: manifest has {n_nodes} nodes (run: {manifest['run_id']})")
        print(
            f"rv review: start Phase-1 with: "
            f"rv dag run {review_dir / 'phase1-dag.json'}"
        )
        return 0
    except Exception as e:
        print(f"rv review new: error: {e}", file=sys.stderr)
        return 1


def _run_expand(args: argparse.Namespace) -> int:
    """Emit Phase-2 manifest from frozen _corpus.md."""
    from research_vault.config import load_config
    from research_vault.review import cmd_expand

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv review expand: config error: {e}", file=sys.stderr)
        return 1

    corpus_path = Path(args.corpus) if getattr(args, "corpus", None) else None

    try:
        manifest = cmd_expand(
            args.project,
            args.scope,
            corpus_path=corpus_path,
            config=cfg,
        )
        n_nodes = len(manifest["nodes"])
        review_dir = cfg.project_notes_dir(args.project) / "reviews" / args.scope
        print(f"rv review expand: Phase-2 manifest: {review_dir / 'phase2-dag.json'}")
        print(f"rv review expand: {n_nodes} nodes (run: {manifest['run_id']})")
        print(
            f"rv review expand: start Phase-2 with: "
            f"rv dag run {review_dir / 'phase2-dag.json'}"
        )
        return 0
    except FileNotFoundError as e:
        print(f"rv review expand: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"rv review expand: error: {e}", file=sys.stderr)
        return 1


def _run_list(args: argparse.Namespace) -> int:
    """List review pointer notes for the project."""
    from research_vault.config import load_config
    from research_vault.review import cmd_list

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv review list: config error: {e}", file=sys.stderr)
        return 1

    try:
        results = cmd_list(args.project, config=cfg)
        if not results:
            print(f"rv review: no reviews for project {args.project!r}")
            return 0
        for item in results:
            scope = item["scope"]
            question = item["question"][:80]
            dag_run = item.get("dag_run", "")
            print(f"  {scope}: {question}")
            if dag_run:
                print(f"    dag_run: {dag_run}")
        return 0
    except Exception as e:
        print(f"rv review list: error: {e}", file=sys.stderr)
        return 1


def _run_tips(args: argparse.Namespace) -> int:
    """Print review_tips seam content."""
    from research_vault.review.style import get_review_tips, REVIEW_TIPS_KEYS

    try:
        from research_vault.config import load_config
        cfg = load_config()
    except Exception:
        cfg = None

    tips = get_review_tips(cfg)
    key = getattr(args, "key", None)

    if key is not None:
        if key not in REVIEW_TIPS_KEYS:
            print(
                f"rv review tips: unknown key {key!r}. "
                f"Valid keys: {sorted(REVIEW_TIPS_KEYS)}",
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


def _run_gap_scan(args: argparse.Namespace) -> int:
    """Run the gap-scan screen (SR-LR-2 §5L.7)."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv review gap-scan: config error: {e}", file=sys.stderr)
        return 1

    threshold = getattr(args, "threshold", 1)

    try:
        new_gaps = cmd_gap_scan(
            args.project,
            config=cfg,
            threshold=threshold,
        )
        from research_vault.review.gap_scan import open_gap_count
        total_open = open_gap_count(args.project, config=cfg)
        print(f"rv review gap-scan: {len(new_gaps)} new gap(s) detected")
        print(f"rv review gap-scan: {total_open} total open gap(s) for project {args.project!r}")
        if total_open > 0:
            print(
                f"rv review gap-scan: run `rv review gap-scope <project> <gap-id> <scope>` "
                f"to author a targeted review pass (human-go required; no auto-fire)."
            )
        return 0
    except Exception as e:
        print(f"rv review gap-scan: error: {e}", file=sys.stderr)
        return 1


def _run_gap_scope(args: argparse.Namespace) -> int:
    """Auto-author a targeted scope from a gap record (SR-LR-2 §5L.7 + SR-GAP-ROUTE §5L.16).

    gap-route is a thin alias — both call this function.
    """
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scope

    verb = getattr(args, "review_cmd", "gap-scope")
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv review {verb}: config error: {e}", file=sys.stderr)
        return 1

    target = getattr(args, "target", None)

    try:
        result = cmd_gap_scope(
            args.project,
            args.gap_id,
            args.scope,
            config=cfg,
            target=target,
        )
        # result is either a Phase-1 manifest dict (literature) or
        # {'plan_note_path': ..., 'gap_context_path': ...} (experiment)
        if "plan_note_path" in result:
            # Experiment arm (SR-GAP-ROUTE)
            plan_path = Path(result["plan_note_path"])
            context_path = Path(result["gap_context_path"])
            print(f"rv review {verb}: experiment plan: {plan_path}")
            print(f"rv review {verb}: gap context: {context_path}")
            print(f"rv review {verb}: next steps:")
            print(f"  1. Fill in the plan note (research question, covers:, diagnosis table)")
            print(f"  2. rv plan check {plan_path}")
            print(f"  3. rv dag approve <run-id> human-go-plan  (human-go gate)")
            print(f"  4. rv plan freeze <run-id> {plan_path}")
            print(f"rv review {verb}: run NEVER auto-fires — human-go required at step 3.")
        else:
            # Literature arm (SR-LR-2 behavior)
            pnd = cfg.project_notes_dir(args.project)
            review_dir = pnd / "reviews" / args.scope
            print(f"rv review {verb}: Phase-1 manifest: {review_dir / 'phase1-dag.json'}")
            print(f"rv review {verb}: gap context: {review_dir / '_gap-context.md'}")
            print(f"rv review {verb}: {len(result['nodes'])} nodes (run: {result['run_id']})")
            print(
                f"rv review {verb}: start with: rv dag run {review_dir / 'phase1-dag.json'}"
            )
        return 0
    except FileNotFoundError as e:
        print(f"rv review {verb}: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"rv review {verb}: error: {e}", file=sys.stderr)
        return 1


def _run_gap_list(args: argparse.Namespace) -> int:
    """List gap records for the project (SR-GAP-ROUTE §5L.16 run-candidate queue)."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_list

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv review gap-list: config error: {e}", file=sys.stderr)
        return 1

    status_filter = getattr(args, "status", None)

    try:
        results = cmd_gap_list(args.project, config=cfg, status_filter=status_filter)
        if not results:
            filter_label = f" with status={status_filter!r}" if status_filter else ""
            print(f"rv review gap-list: no gaps found{filter_label} for project {args.project!r}")
            return 0
        for item in results:
            route = ""
            # Show suggested_route if available (SR-GAP-ROUTE)
            gap_path_hint = ""
            print(
                f"  {item['id']}: [{item['type']}] {item['claim']}"
                f" (status: {item['status']})"
            )
        if status_filter == "proven-open":
            print(
                f"\nrv review gap-list: {len(results)} proven-open run-candidate(s). "
                f"Run `rv review gap-scope {args.project} <gap-id> <scope> --target experiment` "
                f"to author an experiment plan (human-go required)."
            )
        return 0
    except Exception as e:
        print(f"rv review gap-list: error: {e}", file=sys.stderr)
        return 1


def _run_gap_close(args: argparse.Namespace) -> int:
    """Stamp a gap's closure status with provenance edge (SR-LR-2 §5L.8 + SR-GAP-CLOSE §5L.21(1))."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_close

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv review gap-close: config error: {e}", file=sys.stderr)
        return 1

    # SR-GAP-CLOSE: --by flag (maps to closer_ref parameter)
    closer_ref = getattr(args, "by", None)

    try:
        gap_path = cmd_gap_close(
            args.project,
            args.gap_id,
            args.status,
            closer_ref=closer_ref,
            config=cfg,
        )
        print(f"rv review gap-close: updated {gap_path}")
        print(f"rv review gap-close: gap {args.gap_id!r} status → {args.status}")
        if closer_ref:
            print(
                f"rv review gap-close: closed_by: {closer_ref!r} written to gap FM "
                f"(forward edge) + closes: {args.gap_id!r} written to closing note FM "
                f"(backward link — §5L.21 ruling 2, W3C PROV)."
            )
        if args.status == "proven-open":
            print(
                "rv review gap-close: proven-open = targeted pass saturated without closing. "
                "This gap is a candidate contribution — run `rv review gap-promote "
                f"{args.project} {args.gap_id} --to <manuscript-section>` to promote it "
                "into the manuscript (human-only; the claim round-trips the support-matcher)."
            )
        return 0
    except (FileNotFoundError, ValueError, TypeError) as e:
        print(f"rv review gap-close: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"rv review gap-close: error: {e}", file=sys.stderr)
        return 1


def _run_coverage(args: argparse.Namespace) -> int:
    """Report deterministic corpus-coverage keyed by citekey: field (F16+F17)."""
    from research_vault.config import load_config
    from research_vault.review import coverage_report

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv review coverage: config error: {e}", file=sys.stderr)
        return 1

    try:
        report = coverage_report(args.project, args.scope, config=cfg)
    except Exception as e:
        print(f"rv review coverage: error: {e}", file=sys.stderr)
        return 1

    c = report["counts"]
    print(
        f"rv review coverage ({args.project}/{args.scope}): "
        f"{c['corpus']} corpus citekey(s) — "
        f"{c['materialized']} materialized, "
        f"{c['unmaterialized']} unmaterialized, "
        f"{c['orphan']} orphan"
    )

    if report["unmaterialized"]:
        print(f"\nUnmaterialized ({len(report['unmaterialized'])}):")
        for ck in report["unmaterialized"]:
            print(f"  [MISSING] {ck}")

    if report["orphan"]:
        print(f"\nOrphan ({len(report['orphan'])}):")
        for ck in report["orphan"]:
            print(f"  [ORPHAN]  {ck}")

    if report["materialized"] and not report["unmaterialized"] and not report["orphan"]:
        print("  All corpus citekeys materialized and referenced in MOCs.")

    return 0


def _run_relations(args: argparse.Namespace) -> int:
    """Report the corpus-wide paper->paper typed-edge listing (PR-2)."""
    from research_vault.config import load_config
    from research_vault.review import relations_report

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv review relations: config error: {e}", file=sys.stderr)
        return 1

    try:
        report = relations_report(args.project, args.scope, config=cfg)
    except Exception as e:
        print(f"rv review relations: error: {e}", file=sys.stderr)
        return 1

    c = report["counts"]
    print(
        f"rv review relations ({args.project}/{args.scope}): "
        f"{c['total']} paper→paper edge(s) — "
        f"{c['reciprocal']} reciprocal, {c['refutational']} refutational, "
        f"{c['line-of-argument']} line-of-argument"
    )
    for e in report["edges"]:
        print(f"  [{e['tag']}] {e['source']} → {e['target']} ({e['type']}) — {e['reason']}")
    if not report["edges"]:
        print("  No paper→paper edges found yet.")
    return 0


def _run_gap_promote(args: argparse.Namespace) -> int:
    """Promote a proven-open gap to 'promoted' status (SR-GAP-CLOSE §5L.21(2), human-only)."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_promote

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv review gap-promote: config error: {e}", file=sys.stderr)
        return 1

    to_ref = getattr(args, "to", None)

    try:
        gap_path = cmd_gap_promote(
            args.project,
            args.gap_id,
            to_ref=to_ref,
            config=cfg,
        )
        print(f"rv review gap-promote: updated {gap_path}")
        print(f"rv review gap-promote: gap {args.gap_id!r} status → promoted")
        print(f"rv review gap-promote: promoted_to: {to_ref!r}")
        print(
            "rv review gap-promote: the promoted claim must round-trip the SR-MS-2 "
            "support-matcher when cited in the manuscript. If the manuscript sentence "
            "asserting significance is unsupported, the matcher returns [ABSENT] — "
            "the honesty backstop."
        )
        return 0
    except (FileNotFoundError, ValueError, TypeError) as e:
        print(f"rv review gap-promote: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"rv review gap-promote: error: {e}", file=sys.stderr)
        return 1
