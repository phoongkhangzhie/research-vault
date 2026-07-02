"""review/verbs.py — rv review subcommand dispatcher (SR-LR-1, §5L).

When to use: use ``rv review new <project> <scope> --question '...'`` to start a
pre-registered, saturation-gated literature review.  ``rv review expand`` emits the
Phase-2 fan-out after the coverage-gate human-go.  ``rv review list`` enumerates
all reviews for a project.

This is the ONLY path that creates the closed protocol-freeze + saturation-curve +
coverage-critic framework.  A hand-run literature scan gets none of these gates.

Subcommands:
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

Anti-pattern: do NOT hand-collect papers without running ``rv review new`` — a
hand-collected corpus has no ``_protocol.md`` freeze, no saturation measurement,
and no rejects-only coverage critic.

Stdlib only.
sr: SR-LR-1
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
    else:
        print(
            "rv review: missing subcommand. "
            "Use `rv review <project> new <scope> --question '...'`, "
            "`rv review <project> expand <scope>`, "
            "`rv review <project> list`, or `rv review <project> tips`.",
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
