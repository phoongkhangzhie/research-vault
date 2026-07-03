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
            "Exits cleanly with a friendly message if pdflatex is absent. "
            "Use --prep-only to run only the grounding-builders (no pdflatex) "
            "so drafting agents can reference \\result* macros before compile."
        ),
    )
    compile_p.add_argument(
        "ms_id",
        metavar="id",
        help="Manuscript identifier slug (e.g. 'ms-001').",
    )
    compile_p.add_argument(
        "--prep-only",
        action="store_true",
        default=False,
        dest="prep_only",
        help=(
            "Run grounding-builders only (no pdflatex render). "
            "Populates refs.bib, results.tex, and appendix-repro.tex so that "
            "drafting agents can reference \\result* macros before the full compile. "
            "Idempotent: safe to run before rv manuscript compile. "
            "Does NOT require texlive."
        ),
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
            "hallucinated prose references. "
            "Add --semantic to ALSO run the LLM-judged support-matcher tally "
            "(requires RV_JUDGE_MODEL and ANTHROPIC_API_KEY env vars). "
            "Anti-pattern: do NOT skip --semantic before submission — the "
            "structural gate alone does not verify claim support."
        ),
    )
    check_p.add_argument(
        "ms_id",
        metavar="id",
        help="Manuscript identifier slug (e.g. 'ms-001').",
    )
    check_p.add_argument(
        "--semantic",
        action="store_true",
        default=False,
        dest="semantic",
        help=(
            "ALSO run the LLM-judged support-matcher tally (SR-MS-2). "
            "Requires RV_JUDGE_MODEL and ANTHROPIC_API_KEY environment variables — "
            "fails loudly if either is absent (never a silent pass). "
            "Plain 'check' without this flag stays hermetic (structural gates only)."
        ),
    )

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch manuscript subcommands. Returns exit code."""
    from research_vault.config import load_config
    from research_vault.manuscript import cmd_new, cmd_list, cmd_compile, cmd_check, cmd_prep

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
            if getattr(args, "prep_only", False):
                # --prep-only: run grounding-builders only, no pdflatex
                result = cmd_prep(args.project, args.ms_id, config=cfg)
                print(result.get("message", ""))
                warnings = result.get("builder_warnings", [])
                for w in warnings:
                    print(f"  WARN: {w}")
                return result.get("exit_code", 1)
            result = cmd_compile(args.project, args.ms_id, config=cfg)
            print(result.get("message", ""))
            if result.get("exit_code", 1) != 0:
                log = result.get("log", "")
                if log:
                    # Print the last 1000 chars of the log on failure
                    print(f"\n--- LaTeX log (last 1000 chars) ---\n{log[-1000:]}", file=sys.stderr)
            return result.get("exit_code", 1)

        if args.manuscript_cmd == "check":
            semantic = getattr(args, "semantic", False)

            # SR-MS2-FIX: --semantic requires RV_JUDGE_MODEL + ANTHROPIC_API_KEY.
            # Fail LOUD if absent — never silently degrade to structural-only.
            if semantic:
                import os as _os
                _judge_model = _os.environ.get("RV_JUDGE_MODEL", "").strip()
                _api_key = _os.environ.get("ANTHROPIC_API_KEY", "").strip()
                if not _judge_model or not _api_key:
                    missing = []
                    if not _judge_model:
                        missing.append("RV_JUDGE_MODEL")
                    if not _api_key:
                        missing.append("ANTHROPIC_API_KEY")
                    print(
                        f"rv manuscript check --semantic: FAIL — "
                        f"env var(s) required but absent: {', '.join(missing)}. "
                        f"Set them to the Opus-tier model ID and API key before running "
                        f"the semantic gate. (Plain 'rv manuscript check' stays hermetic.)",
                        file=sys.stderr,
                    )
                    return 1

            result = cmd_check(args.project, args.ms_id, config=cfg)
            errors = result.get("errors", [])
            warnings = result.get("warnings", [])

            # If --semantic: also run the LLM-judged support-matcher tally
            if semantic and not errors:
                from research_vault.manuscript.check_gates import check_support_tally
                import os as _os2
                _judge_model2 = _os2.environ.get("RV_JUDGE_MODEL", "")
                tree_root = result.get("tree_root")
                notes_root = result.get("notes_root")
                if tree_root is not None:
                    tally = check_support_tally(
                        tree_root,
                        notes_root=notes_root,
                        judge_model=_judge_model2,
                    )
                    print(f"rv manuscript check --semantic: {tally['honest_report']}")
                    if tally.get("canary_aborted"):
                        print(
                            f"  ABORT (canary): {tally['errors'][0] if tally['errors'] else 'blind judge'}",
                            file=sys.stderr,
                        )
                        return 1
                    for e in tally.get("errors", []):
                        errors.append(f"[semantic] {e}")
                    for w in tally.get("warnings", []):
                        warnings.append(f"[semantic] {w}")

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
