# SPDX-License-Identifier: AGPL-3.0-or-later
"""review/verbs.py — rv review subcommand dispatcher (§5L).

When to use: use ``rv review new <project> <scope> --question '...'`` to start a
pre-registered, saturation-gated literature review.  ``rv review expand`` emits the
Phase-2 fan-out after the coverage-gate human-go.  ``rv review list`` enumerates
all reviews for a project.  ``rv review gap-scan`` detects typed research gaps from
the OKF corpus and/or a manuscript critic report (§5L.7, the loop-closer).
``rv review gap-scope`` (or the alias ``gap-route``) auto-authors the remedy scope:
literature (Part-1 review) OR experiment (pre-registration plan), routed by error-asymmetry.

This is the ONLY path that creates the closed protocol-freeze + saturation-curve +
coverage-critic framework.  A hand-run literature scan gets none of these gates.

Subcommands:
  rv review <project> new <scope> --question "..."
      Create a review OKF note + reviews/<scope>/ artifact dir + Phase-1 DAG manifest.
      Phase-1 shape: review-scope → [HG:approve-protocol] → review-search
          → review-snowball → coverage-gate (auto-resolved).
      ``review-scope`` MUST file a ``_protocol.md`` with a non-empty ``counter-position``
      field (L-2 gate, §5L.3) — ``review-search`` is gated on the protocol artifact.
      ``review-snowball`` runs an internal saturation loop (both forward + backward
      citation directions) and produces ``_corpus.md`` + ``_saturation.md``.

  rv review <project> expand <scope> [--corpus <path>]
      Emit Phase-2 manifest from the frozen ``_corpus.md`` after coverage-gate approval.
      One ``relate-<key>`` node per ``[NEW]`` citekey → ``review-synthesize``
      → ``review-coverage-critic`` (L-2: [BLOCK] on missing counter-position)
      → ``approve-review` (auto-resolved)`.
      Saves ``reviews/<scope>/phase2-dag.json``.

  rv review <project> list
      List all review pointer notes for the project.

  rv review <project> tips [--key <key>]
      Print the review_tips seam content (researcher's default or adopter override).

Subcommands (the gap-driven pass):
  rv review <project> gap-scan [--threshold <n>]
      Detect typed research gaps from the OKF corpus (knowledge_void, contradictory,
      evaluation_void).
      Writes gaps/<id>.md for each new gap with a suggested_route: field.
      Idempotent (does NOT re-create existing gaps).
      Surfaces a COUNT only — records are never inlined into the control bus.
      Human authorizes which gaps become targeted scopes (no auto-fire).

  rv review <project> gap-scope <gap-id> <scope> [--target {literature|experiment}]
      Auto-author a scope from a gap record: default target = gap.suggested_route.
      --target literature (default): Part-1 review scope (unchanged).
      --target experiment (new): pre-registration plan (research question
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
      --by is REQUIRED for closed-supported and closed-filled (charter §2:
      a closed gap with no closer is un-auditable). --by is REJECTED for proven-open
      (nothing closed it — that's the point). --by writes bidirectional edges:
        closed_by: <note-ref> in the gap FM + closes: <gap-id> in the closing note FM.
      Anti-pattern: do NOT gap-close a closed-* gap without --by — a closer-less closure
      is un-auditable and breaks the provenance chain (§5L.21(1)).

  rv review <project> gap-promote <gap-id> --to <ref>
      Promote a proven-open gap to 'promoted' status (human-only).
      Writes promoted_to: <ref> in the gap FM.
      Requires --to <manuscript-section/claim> (unauditable without a target).
      Anti-pattern: do NOT hand-write a contribution claim from a proven-open gap —
      run gap-promote first so the claim round-trips the support-matcher
      (the honesty backstop that polices its own promotions).

Anti-pattern: do NOT hand-collect papers without running ``rv review new`` — a
hand-collected corpus has no ``_protocol.md`` freeze, no saturation measurement,
and no rejects-only coverage critic.

Anti-pattern: do NOT auto-fire a gap-driven review pass — ``gap-scan`` is a
SCREEN that PROPOSES work; the human authorizes each targeted pass via ``gap-scope``
or ``gap-route`` (operator confirmed: no auto-fire, D-GAP-4).

Anti-pattern: do NOT hand-decide read-vs-run and hand-spin a lit pass or a plan —
run ``rv review gap-scope <project> <gap-id> <scope>``; it routes by error-asymmetry
and auto-authors the remedy scope (§5L.17 when-to-use).

Stdlib only.
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
    internal saturation loop, and coverage-critic gates (§5L.1).

    Anti-pattern: do NOT hand-collect papers without ``rv review new`` — the hand-run
    path has no protocol-freeze (anti-fishing), no saturation curve, and no rejects-only
    critic (the coverage gate cannot fire without the artifacts ``rv review new`` scaffolds).
    """
    desc = (
        "Staged, pre-registered, saturation-gated literature review loop.\n"
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
            "review-search → review-snowball → coverage-gate (auto-resolved)."
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

    # ── run (D2, verb consolidation) ───────────────────────────────────────
    # The one-call autonomous-loop kick: fuses `review new` + `dag run`.
    # Starts the run; does NOT block until done (the hub fans out agent
    # nodes, §1.9) — same non-blocking contract as `dag run`.
    run_p = sub.add_parser(
        "run",
        help=(
            "D2: fuse 'review new' + 'dag run' — the one-call autonomous-"
            "loop kick. Scaffolds the review + starts Phase-1 in one call."
        ),
        description=(
            "The single trigger Alfred reaches for to kick the whole "
            "autonomous lit-review loop (verb-consolidation D2). Equivalent "
            "to `rv review <project> new <scope> --question '...'` followed "
            "immediately by `rv dag run <phase1-manifest>` — but as one "
            "call, with the manifest path resolved internally. Non-blocking: "
            "starts the run and prints the initial frontier; it does not "
            "wait for the loop to finish."
        ),
    )
    run_p.add_argument(
        "scope",
        metavar="<scope>",
        help="Review scope identifier slug (e.g. 'scope-llm-eval', 'scope-crosslingual').",
    )
    run_p.add_argument(
        "--question",
        required=True,
        metavar="QUESTION",
        help="The review research question (same as `review new --question`).",
    )

    # ── expand — D1 HARD-REMOVED (verb consolidation) ─────────────────────
    # Collapsed into the autonomous Phase-1->2 transition: once coverage-gate
    # GOes (§1.6), the runner emits Phase-2 itself (internal call to
    # review.cmd_expand, kept importable). No verb to choose by hand.
    from ..cli_removed_verbs import add_removed_verb_stub
    add_removed_verb_stub(
        sub, "expand",
        op_or_transition="the autonomous Phase-1->2 emission (fires when coverage-gate GOes, NG-4)",
        redirect="rv dag approve <run> coverage-gate --auto (Phase-2 is emitted automatically on GO)",
    )

    # ── list ─────────────────────────────────────────────────────────────────
    sub.add_parser(
        "list",
        help="List review pointer notes for the project.",
    )

    # ── refresh (NG-6a) ─────────────────────────────────────────────────────
    refresh_p = sub.add_parser(
        "refresh",
        help=(
            "NG-6a: fail-closed re-freeze of the review's corpus_freeze "
            "baseline after an in-scope append. BLOCKS on an undeclared "
            "criteria change or an undeclared corpus delta — never "
            "launders a silent mutation into a fresh hash."
        ),
        description=(
            "Re-hash _corpus.md, re-verify the frozen _protocol.md criteria "
            "hash is unchanged (or a human 'criteria-change' deviation "
            "accounts for it), re-verify every corpus delta since the last "
            "freeze is DECLARED in _deviations.md, then bump the "
            "corpus_freeze version and re-stamp the baseline. "
            "Anti-pattern: do NOT hand-edit _corpus.md and expect the next "
            "coverage-gate pass to pick it up silently — an undeclared "
            "delta trips the D2 BLOCK; run `rv review refresh` (after "
            "declaring the delta via a deviation record) to move the "
            "baseline forward."
        ),
    )
    refresh_p.add_argument(
        "scope",
        metavar="<scope>",
        help="Review scope identifier slug (the corpus_freeze baseline to re-freeze).",
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
            "Detect typed research gaps from the OKF corpus (§5L.7). "
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
            "Auto-author a targeted scope from a gap record (§5L.7, §5L.16). "
            "--target literature: Part-1 review scope (default). "
            "--target experiment: pre-registration plan. "
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
            "Route target. 'literature': Part-1 review scope. "
            "'experiment': pre-registration plan. "
            "Omit to use the gap's suggested_route field (computed at scan time)."
        ),
    )

    # ── gap-route (alias for gap-scope — §5L.17 discoverability) ──
    gap_route_p = sub.add_parser(
        "gap-route",
        help=(
            "Thin alias for gap-scope (discoverability hook). "
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
            "List gap records for the project (§5L.16 + §5L.20). "
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
            "'promoted' = proven-open gaps promoted to manuscript candidate. "
            "'reopened' = gaps that re-entered open-routing via structural reopen signal."
        ),
    )

    # ── gap-close ─────────────────────────────────────────────────────────────
    gap_close_p = sub.add_parser(
        "gap-close",
        help=(
            "Stamp a gap's closure status with provenance edge (§5L.8 + §5L.21(1)). "
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
            "The OKF note that resolved this gap (e.g. 'literature/smith2024', "
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
            "§5L.21(2): promote a proven-open gap to 'promoted' status (human-only). "
            "proven-open → promoted; writes promoted_to: <ref> in the gap FM. "
            "Requires --to <manuscript-section/claim>. "
            "Anti-pattern: do NOT hand-write a contribution from a proven-open gap — "
            "run gap-promote so the claim round-trips the support-matcher."
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

    # ── coverage / relations — D1 HARD-REMOVED (verb consolidation) ───────
    # Both collapsed into "tool" node-ops (op="coverage" / op="relations")
    # invoked by coverage-gate/review-coverage-critic and review-synthesize
    # respectively. review.coverage_report / review.relations_report remain
    # importable (review.autonomy.OP_REGISTRY calls them directly).
    add_removed_verb_stub(
        sub, "coverage",
        op_or_transition="the 'coverage' tool node-op (coverage-gate / review-coverage-critic)",
        redirect="rv dag run <phase2-manifest> (coverage is checked automatically at the gate)",
    )
    add_removed_verb_stub(
        sub, "relations",
        op_or_transition="the 'relations' tool node-op (review-synthesize node)",
        redirect="rv dag run <phase2-manifest> (review-synthesize traverses the edges automatically)",
    )

    return p


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    """Dispatch review subcommands. Returns exit code."""
    # D1 (verb consolidation): expand / coverage / relations are
    # HARD-REMOVED stubs — always dispatch to the redirect breadcrumb.
    if getattr(args, "_rv_removed_verb", None) is not None:
        from ..cli_removed_verbs import run_removed_verb_stub
        return run_removed_verb_stub(args)

    subcommand = getattr(args, "review_cmd", None)

    if subcommand == "new":
        return _run_new(args)
    elif subcommand == "run":
        return _run_run(args)
    elif subcommand == "list":
        return _run_list(args)
    elif subcommand == "refresh":
        return _run_refresh(args)
    elif subcommand == "tips":
        return _run_tips(args)
    elif subcommand == "gap-scan":
        return _run_gap_scan(args)
    elif subcommand in ("gap-scope", "gap-route"):
        # gap-route is a thin alias for gap-scope (§5L.17 discoverability)
        return _run_gap_scope(args)
    elif subcommand == "gap-list":
        return _run_gap_list(args)
    elif subcommand == "gap-close":
        return _run_gap_close(args)
    elif subcommand == "gap-promote":
        return _run_gap_promote(args)
    else:
        print(
            "rv review: missing subcommand. "
            "Use `rv review <project> new <scope> --question '...'` (or the "
            "fused `rv review <project> run <scope> --question '...'`, D2), "
            "`rv review <project> list`, `rv review <project> refresh <scope>` (NG-6a), "
            "`rv review <project> tips`, "
            "`rv review <project> gap-scan`, `rv review <project> gap-scope [--target …]`, "
            "`rv review <project> gap-route [--target …]` (alias for gap-scope), "
            "`rv review <project> gap-list [--status …]`, "
            "`rv review <project> gap-close [--by <note-ref>]`, "
            "or `rv review <project> gap-promote --to <ref>`. "
            "expand/coverage/relations were HARD-REMOVED (D1, verb "
            "consolidation) — they now run automatically as DAG node-ops.",
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


def _run_run(args: argparse.Namespace) -> int:
    """D2 (verb consolidation): fuse `review new` + `dag run` — the one-call
    autonomous-loop kick. Starts the run; does not block until done."""
    from research_vault.config import load_config
    from research_vault.review import cmd_new
    from research_vault.dag.verbs import cmd_run as _dag_cmd_run

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv review run: config error: {e}", file=sys.stderr)
        return 1

    try:
        note_path, review_dir, manifest = cmd_new(
            args.project,
            args.scope,
            question=args.question,
            config=cfg,
        )
    except Exception as e:
        print(f"rv review run: error scaffolding review: {e}", file=sys.stderr)
        return 1

    manifest_path = review_dir / "phase1-dag.json"
    print(f"rv review run: created note: {note_path}")
    print(f"rv review run: artifact dir: {review_dir}")
    print(f"rv review run: Phase-1 manifest: {manifest_path}")
    print(f"rv review run: starting Phase-1 (run: {manifest['run_id']})...")

    import argparse as _argparse
    dag_args = _argparse.Namespace(manifest=str(manifest_path))
    return _dag_cmd_run(dag_args)


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


def _run_refresh(args: argparse.Namespace) -> int:
    """NG-6a: fail-closed re-freeze of the review's corpus_freeze baseline."""
    from research_vault.config import load_config
    from research_vault.review.corpus_freeze import RefreshBlocked, cmd_refresh
    from research_vault.dag.store import StoreError
    from research_vault.review import CorpusSchemaError

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv review refresh: config error: {e}", file=sys.stderr)
        return 1

    try:
        new_freeze = cmd_refresh(args.project, args.scope, config=cfg)
    except RefreshBlocked as e:
        print(f"rv review refresh: {e}", file=sys.stderr)
        return 1
    except CorpusSchemaError as e:
        print(f"rv review refresh: {e}", file=sys.stderr)
        return 1
    except StoreError as e:
        print(f"rv review refresh: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"rv review refresh: error: {e}", file=sys.stderr)
        return 1

    print(
        f"rv review refresh ({args.project}/{args.scope}): corpus_freeze "
        f"v{new_freeze['version']} — {len(new_freeze['corpus_citekeys'])} "
        f"citekey(s), corpus_hash={new_freeze['corpus_hash'][:23]}..., "
        f"criteria_hash={new_freeze['criteria_hash'][:23]}..."
    )
    return 0


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
    """Run the gap-scan screen (§5L.7)."""
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
    """Auto-author a targeted scope from a gap record (§5L.7 + §5L.16).

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
            # Experiment arm
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
            # Literature arm (unchanged behavior)
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
    """List gap records for the project (§5L.16 run-candidate queue)."""
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
            # Show suggested_route if available
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
    """Stamp a gap's closure status with provenance edge (§5L.8 + §5L.21(1))."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_close

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv review gap-close: config error: {e}", file=sys.stderr)
        return 1

    # --by flag (maps to closer_ref parameter)
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
        mismatch_note = ""
        if e.get("kind_mismatch"):
            mismatch_note = (
                f"  [kind-mismatch: stated {e['kind_mismatch']['stated']!r}, "
                f"tag says {e['kind_mismatch']['derived']!r} — tag wins]"
            )
        print(
            f"  [{e['tag']}] {e['source']} → {e['target']} ({e['type']}) — "
            f"{e['reason']}{mismatch_note}"
        )
    if not report["edges"]:
        print("  No paper→paper edges found yet.")

    # Architect review (the load-bearing fix): malformed edges are ALWAYS
    # surfaced, never silently absorbed into a clean-looking total.
    if report["malformed"]:
        print(f"\nMalformed ({c['malformed']}) — surfaced, never silently dropped:")
        for m in report["malformed"]:
            print(f"  [MALFORMED] {m['source']}: {m['line']!r}")

    # Recommended (architect review): dangling edges, mirrors coverage_report's
    # orphan reporting — a SIGNAL, not a hard error.
    if report["dangling"]:
        print(f"\nDangling ({c['dangling']}) — target citekey not in this project's corpus:")
        for d in report["dangling"]:
            print(f"  [DANGLING] {d['source']} → {d['target']}")

    return 0


def _run_gap_promote(args: argparse.Namespace) -> int:
    """Promote a proven-open gap to 'promoted' status (§5L.21(2), human-only)."""
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
            "rv review gap-promote: the promoted claim must round-trip the "
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
