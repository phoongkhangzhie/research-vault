"""verbs.py — `rv manuscript` CLI verb module for Research Vault.

When to use: use `rv manuscript new <project> <id> --thesis '...'` to scaffold a
grounded manuscript (OKF note + LaTeX tree + drafting-DAG manifest). Use
`rv manuscript list [<project>]` to enumerate all manuscript notes.

Anti-pattern: do NOT hand-write a .tex and hand-type citations/numbers — run
`rv manuscript new --thesis` so the draft carries a closed .bib from your
`literature/` notes, machine-injected results, and structural \\cite→source
verification. A hand-typed number or an uncited claim is exactly the fabrication
this prevents.

Commands:
  rv manuscript <project> new <id> --thesis "..." [--scope <ids...>]
    → creates manuscript/<id>.md OKF note + manuscripts/<id>/ tree + DAG manifest

  rv manuscript <project> list
    → lists manuscript notes for the project

sr: SR-MS-1a
Stdlib only.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Load lazily to avoid circular imports at the module level
# (cli.py imports verbs; verbs imports manuscript)


def build_parser(
    parent: "argparse._SubParsersAction | None" = None,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Build the argument parser for the `manuscript` verb.

    When to use: use `rv manuscript new <project> <id> --thesis '...'` to scaffold
    an anti-fabrication-by-construction path from a verified OKF graph to a paper draft:
    closed .bib from filed `literature/` notes, machine-injected results macros,
    `reads:` grounding contracts, and a 16-node drafting-DAG manifest (§5J.2).

    Anti-pattern: do NOT hand-write a .tex and hand-type citations/numbers — run
    `rv manuscript new --thesis` so the draft carries a closed .bib, machine-
    injected results, and structural \\cite→source verification; a hand-typed number
    or an uncited claim is exactly the fabrication this prevents.

    sr: SR-MS-1a
    """
    desc = (
        "Scaffold and list grounded manuscript drafts for a project.\n"
        "'rv manuscript new' is the ONLY path that creates the closed-.bib + DAG framework.\n"
        "Hand-writing a .tex skips the grounding teeth (closed .bib, machine-injected results,\n"
        "structural \\cite→source verification). Use rv dag run <manifest> to drive the loop."
    )
    if parent is not None:
        p = parent.add_parser(
            "manuscript",
            help="Scaffold and list grounded manuscript drafts.",
            description=desc,
        )
    else:
        p = argparse.ArgumentParser(prog="rv manuscript", description=desc)

    p.add_argument("project", help="Project slug.")

    sub = p.add_subparsers(dest="manuscript_cmd", required=True)

    # ── new ──────────────────────────────────────────────────────────────────
    new_p = sub.add_parser(
        "new",
        help=(
            "Create a manuscript OKF note + manuscripts/<id>/ tree + drafting-DAG manifest. "
            "Scaffolds the 16-node §5J.2 drafting DAG with all section specs and reads: contracts."
        ),
    )
    new_p.add_argument(
        "ms_id",
        metavar="id",
        help="Manuscript identifier slug (e.g. 'ms-001' or 'icml-2026').",
    )
    new_p.add_argument(
        "--thesis",
        required=True,
        metavar="CLAIM",
        help="One-sentence claim the paper argues. Stored in the manuscript note's thesis: field.",
    )
    new_p.add_argument(
        "--scope",
        nargs="*",
        default=[],
        metavar="OKF-ID",
        help=(
            "OKF note ids to synthesize (e.g. findings/find-q1 experiments/exp-q1). "
            "Stored as synthesized_okf in the manuscript note."
        ),
    )
    new_p.add_argument(
        "--optional",
        action="store_true",
        default=False,
        dest="include_optional",
        help="Include OPTIONAL sections (e.g. background) in the drafting-DAG manifest.",
    )
    new_p.add_argument(
        "--venue-optional",
        action="store_true",
        default=False,
        dest="include_venue_optional",
        help=(
            "Include VENUE-OPTIONAL sections (ethics-impacts, data-code-availability) "
            "in the drafting-DAG manifest."
        ),
    )

    # ── list ─────────────────────────────────────────────────────────────────
    sub.add_parser(
        "list",
        help="List manuscript notes for the project.",
    )

    # ── compile ──────────────────────────────────────────────────────────────
    compile_p = sub.add_parser(
        "compile",
        help=(
            "Exec-guarded LaTeX compile loop: build .bib from library.json, inject "
            "results macros, run pdflatex→bibtex→pdflatex×2 + chktex fix-loop. "
            "Requires texlive-full (system package, not pip-installable). "
            "Exits cleanly with a friendly message if pdflatex is absent."
        ),
    )
    compile_p.add_argument(
        "ms_id",
        metavar="id",
        help="Manuscript identifier slug (e.g. 'ms-001').",
    )

    # ── check ─────────────────────────────────────────────────────────────────
    check_p = sub.add_parser(
        "check",
        help=(
            "Run structural grounding gates: unmatched \\cite resolution, "
            "figure-file existence, compile-success, data-code-availability "
            "sentinel cross-check. Does NOT run semantic gates (SR-MS-2). "
            "Hard-fails on any unmatched \\cite. "
            "Citation integrity is structural for \\cite+provenance and "
            "assisted-plus-human for prose — this does NOT guarantee zero "
            "hallucinated prose references."
        ),
    )
    check_p.add_argument(
        "ms_id",
        metavar="id",
        help="Manuscript identifier slug (e.g. 'ms-001').",
    )

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch manuscript subcommands. Returns exit code."""
    from research_vault.config import load_config
    from research_vault.manuscript import cmd_new, cmd_list, cmd_compile, cmd_check

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv manuscript: config error: {e}", file=sys.stderr)
        return 1

    try:
        if args.manuscript_cmd == "new":
            note_path, tree_root, manifest = cmd_new(
                args.project,
                args.ms_id,
                thesis=args.thesis,
                scope=args.scope or [],
                config=cfg,
                include_optional=getattr(args, "include_optional", False),
                include_venue_optional=getattr(args, "include_venue_optional", False),
            )
            n_nodes = len(manifest["nodes"])
            print(f"rv manuscript: created note: {note_path}")
            print(f"rv manuscript: scaffolded tree: {tree_root}")
            print(f"rv manuscript: DAG manifest: {tree_root / 'drafting-dag.json'}")
            print(f"rv manuscript: manifest has {n_nodes} nodes (run: {manifest['run_id']})")
            print(f"rv manuscript: start the loop with: rv dag run {tree_root / 'drafting-dag.json'}")
            return 0

        if args.manuscript_cmd == "list":
            results = cmd_list(args.project, config=cfg)
            if not results:
                print(f"rv manuscript: no manuscript notes for project {args.project!r}")
                return 0
            for item in results:
                fields = item["fields"]
                path = item["path"]
                thesis = fields.get("thesis", "(no thesis)")
                dag_run = fields.get("dag_run", "")
                print(f"  {path.stem}: {thesis[:80]}")
                if dag_run:
                    print(f"    dag_run: {dag_run}")
            return 0

        if args.manuscript_cmd == "compile":
            result = cmd_compile(args.project, args.ms_id, config=cfg)
            print(result.get("message", ""))
            if result.get("exit_code", 1) != 0:
                log = result.get("log", "")
                if log:
                    # Print the last 1000 chars of the log on failure
                    print(f"\n--- LaTeX log (last 1000 chars) ---\n{log[-1000:]}", file=sys.stderr)
            return result.get("exit_code", 1)

        if args.manuscript_cmd == "check":
            result = cmd_check(args.project, args.ms_id, config=cfg)
            errors = result.get("errors", [])
            warnings = result.get("warnings", [])
            if errors:
                print(f"rv manuscript check: FAIL — {len(errors)} error(s):", file=sys.stderr)
                for e in errors:
                    print(f"  ERROR: {e}", file=sys.stderr)
            if warnings:
                print(f"rv manuscript check: {len(warnings)} warning(s):")
                for w in warnings:
                    print(f"  WARN: {w}")
            if not errors and not warnings:
                print("rv manuscript check: OK — all structural gates passed.")
            elif not errors:
                print("rv manuscript check: OK — no hard errors (warnings above).")
            return 0 if not errors else 1

        print(f"rv manuscript: unknown command: {args.manuscript_cmd!r}", file=sys.stderr)
        return 1

    except Exception as e:
        print(f"rv manuscript: error: {e}", file=sys.stderr)
        return 1
