"""review/verbs.py — rv review subcommand dispatcher (SR-LR-1 + SR-LR-2, §5L).

When to use: use ``rv review new <project> <scope> --question '...'`` to start a
pre-registered, saturation-gated literature review.  ``rv review expand`` emits the
Phase-2 fan-out after the coverage-gate human-go.  ``rv review list`` enumerates
all reviews for a project.  ``rv review gap-scan`` detects typed research gaps from
the OKF corpus and/or a manuscript critic report (SR-LR-2 §5L.7, the loop-closer).

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
      Print the review_tips seam content (Ada's default or adopter override).

Subcommands (SR-LR-2 — the gap-driven pass):
  rv review <project> gap-scan [--threshold <n>] [--critic-report <path>]
      Detect typed research gaps from the OKF corpus (knowledge_void, contradictory,
      evaluation_void) and/or an optional manuscript critic report (absent_row).
      Writes gaps/<id>.md for each new gap. Idempotent (does NOT re-create existing
      gaps). Surfaces a COUNT only — records are never inlined into the control bus.
      Human authorizes which gaps become targeted review passes (no auto-fire).

  rv review <project> gap-scope <gap-id> <scope>
      Auto-author a Part-1 review scope from a gap record: question ← claim (verbatim);
      seed_queries ← per-type templates; snowball_seeds ← anchor citekeys.
      Emits Phase-1 manifest + ``_gap-context.md`` in reviews/<scope>/.
      This is a TARGETED invocation of the SR-LR-1 loop — no new DAG mechanism (§5L.7).

  rv review <project> gap-close <gap-id> --status <status>
      Stamp a gap's closure status. status ∈ {closed-supported, closed-filled, proven-open}.
      A ``proven-open`` gap saturated without closing → candidate research contribution.

Anti-pattern: do NOT hand-collect papers without running ``rv review new`` — a
hand-collected corpus has no ``_protocol.md`` freeze, no saturation measurement,
and no rejects-only coverage critic.

Anti-pattern: do NOT auto-fire a gap-driven review pass — ``gap-scan`` is a
SCREEN that PROPOSES work; the human authorizes each targeted pass via ``gap-scope``
(operator confirmed: no auto-fire, D-GAP-4).

Stdlib only.
sr: SR-LR-1, SR-LR-2
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
        "After coverage-gate: rv review expand <project> <scope> → Phase-2"
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
        help="Print review_tips seam content (Ada's defaults or adopter override).",
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
    gap_scan_p.add_argument(
        "--critic-report",
        metavar="<path>",
        default=None,
        help=(
            "Path to a manuscript critic report (the SR-MS-2 run_critic() output). "
            "If provided, scans for [ABSENT]/[CONTRADICTS] rows — the loop-closer "
            "gap type (absent_row, §5L.10). Omit if no critic report exists yet."
        ),
    )

    # ── gap-scope ─────────────────────────────────────────────────────────────
    gap_scope_p = sub.add_parser(
        "gap-scope",
        help=(
            "Auto-author a targeted Part-1 review scope from a gap record (§5L.7). "
            "Emits Phase-1 manifest + _gap-context.md. TARGETED invocation of SR-LR-1."
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

    # ── gap-close ─────────────────────────────────────────────────────────────
    gap_close_p = sub.add_parser(
        "gap-close",
        help=(
            "Stamp a gap's closure status (§5L.8). "
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
    elif subcommand == "gap-scope":
        return _run_gap_scope(args)
    elif subcommand == "gap-close":
        return _run_gap_close(args)
    else:
        print(
            "rv review: missing subcommand. "
            "Use `rv review <project> new <scope> --question '...'`, "
            "`rv review <project> expand <scope>`, "
            "`rv review <project> list`, `rv review <project> tips`, "
            "`rv review <project> gap-scan`, `rv review <project> gap-scope`, "
            "or `rv review <project> gap-close`.",
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
    critic_report_str = getattr(args, "critic_report", None)
    critic_report = Path(critic_report_str) if critic_report_str else None

    try:
        new_gaps = cmd_gap_scan(
            args.project,
            config=cfg,
            threshold=threshold,
            critic_report=critic_report,
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
    """Auto-author a targeted review scope from a gap record (SR-LR-2 §5L.7)."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scope

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv review gap-scope: config error: {e}", file=sys.stderr)
        return 1

    try:
        manifest = cmd_gap_scope(
            args.project,
            args.gap_id,
            args.scope,
            config=cfg,
        )
        pnd = cfg.project_notes_dir(args.project)
        review_dir = pnd / "reviews" / args.scope
        print(f"rv review gap-scope: Phase-1 manifest: {review_dir / 'phase1-dag.json'}")
        print(f"rv review gap-scope: gap context: {review_dir / '_gap-context.md'}")
        print(f"rv review gap-scope: {len(manifest['nodes'])} nodes (run: {manifest['run_id']})")
        print(
            f"rv review gap-scope: start with: rv dag run {review_dir / 'phase1-dag.json'}"
        )
        return 0
    except FileNotFoundError as e:
        print(f"rv review gap-scope: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"rv review gap-scope: error: {e}", file=sys.stderr)
        return 1


def _run_gap_close(args: argparse.Namespace) -> int:
    """Stamp a gap's closure status (SR-LR-2 §5L.8)."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_close

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv review gap-close: config error: {e}", file=sys.stderr)
        return 1

    try:
        gap_path = cmd_gap_close(
            args.project,
            args.gap_id,
            args.status,
            config=cfg,
        )
        print(f"rv review gap-close: updated {gap_path}")
        print(f"rv review gap-close: gap {args.gap_id!r} status → {args.status}")
        if args.status == "proven-open":
            print(
                "rv review gap-close: proven-open = targeted pass saturated without closing. "
                "This gap is a candidate contribution — cite it in the manuscript's "
                "contribution framing ('no prior work addresses X — that is our gap to fill')."
            )
        return 0
    except (FileNotFoundError, ValueError) as e:
        print(f"rv review gap-close: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"rv review gap-close: error: {e}", file=sys.stderr)
        return 1
