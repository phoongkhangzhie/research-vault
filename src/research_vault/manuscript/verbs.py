# SPDX-License-Identifier: AGPL-3.0-or-later
"""manuscript/verbs.py — rv manuscript subcommand dispatcher (PR-M1, type-generic core).

When to use: use ``rv manuscript <project> new <slug> --type <type>`` to scaffold
a per-manuscript folder (design §0/§12: NOT an OKF taxonomy — a per-manuscript
``manuscripts/<slug>/{_manuscript.md, report.md, sections/, references.md, figures/}``
folder). ``rv manuscript <project> expand <slug>`` emits the Phase-2 draft
manifest generically from the registered type's section table. ``rv manuscript
<project> review <slug>`` drives the 2-round x 3-reviewer adversarial
review-revise board (design §9, PR-M5) — requires RV_JUDGE_MODEL +
ANTHROPIC_API_KEY, raises loudly rather than silently no-op-ing when absent.
``rv manuscript <project> list`` enumerates all manuscripts for a project.

This is the ONLY path that creates the type-registered per-manuscript folder +
Phase-1/2 DAG manifests.

Anti-pattern: do NOT hand-write markdown sections and hand-collect citations
from OKF piles — run ``rv manuscript new`` so the per-manuscript folder carries the
type-generic scaffold; the hermetic ``.bib`` (PR-M2), the fidelity gates
(PR-M3), the equation machinery (PR-M4), and the review-revise board (PR-M5)
all plug into this same folder.

Stdlib only.
sr: PR-M1
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Build parser
# ---------------------------------------------------------------------------

def build_parser(parent: "argparse._SubParsersAction | None" = None) -> argparse.ArgumentParser:
    """Build the argument parser for the ``manuscript`` verb.

    When to use: use ``rv manuscript <project> new <slug> --type <type>`` to
    scaffold a per-manuscript folder + (type-optional) Phase-1 manifest
    (PR-M1, design §1/§2).

    Anti-pattern: do NOT hand-write markdown sections and hand-type citations/numbers —
    ``rv manuscript new`` is the only path that registers the manuscript's
    TYPE, which every downstream gate (PR-M2/M3/M4/M5) keys off of.

    sr: PR-M1
    """
    desc = (
        "Type-generic manuscript loop (PR-M1). ``rv manuscript new`` is the ONLY "
        "path that creates the per-manuscript folder convention.\n"
        "Drive Phase-2 with: rv dag run manuscripts/<slug>/phase2-dag.json\n"
        "After scaffolding: rv manuscript <project> expand <slug> -> Phase-2"
    )
    if parent is not None:
        p = parent.add_parser(
            "manuscript",
            help="Type-generic manuscript loop (per-manuscript folder, DAG-driven).",
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
            "Scaffold a per-manuscript folder + (type-optional) Phase-1 manifest. "
            "manuscripts/<slug>/{_manuscript.md, report.md, sections/, references.md, figures/}."
        ),
    )
    new_p.add_argument(
        "slug",
        metavar="<slug>",
        nargs="?",
        default=None,
        help=(
            "Manuscript identifier slug (e.g. 'survey-llm-eval'). Optional "
            "when --from-review is given (adopted from it, NG-7 §2.6)."
        ),
    )
    new_p.add_argument(
        "--type",
        required=True,
        metavar="<type>",
        help=(
            "Registered ManuscriptType key (e.g. 'lit-review'). Unknown types "
            "fail loudly — no silent fallback."
        ),
    )
    new_p.add_argument(
        "--from-review",
        metavar="<scope>",
        default=None,
        help=(
            "An `rv review` scope id to adopt as the slug (NG-7 §2.6) — "
            "pre-binds the corpus by making the manuscript slug equal the "
            "review scope id, the convention every corpus-lookup keys off. "
            "An explicit <slug> that differs from this is warned, not "
            "silently overridden."
        ),
    )

    # ── expand / review — D1 HARD-REMOVED (verb consolidation) ────────────
    # expand collapsed into the autonomous Phase-1->2 draft emission
    # (fires when approve-framework GOes). review (the 2x3 board) collapsed
    # into an agent-fan-out node + the NG-4 cold-judge seam consumed at
    # approve-manuscript --auto. Both underlying functions
    # (manuscript.cmd_expand, manuscript.review_board.run_review_board)
    # remain importable.
    from ..cli_removed_verbs import add_removed_verb_stub
    add_removed_verb_stub(
        sub, "expand",
        op_or_transition="the autonomous Phase-1->2 draft emission (fires when approve-framework GOes, NG-4)",
        redirect="rv dag approve <run> approve-framework --auto (Phase-2 is emitted automatically on GO)",
    )
    add_removed_verb_stub(
        sub, "review",
        op_or_transition="the review-board agent-fan-out node + the NG-4 cold-judge seam",
        redirect=(
            "rv dag approve <run> approve-manuscript --auto (consumes the "
            "structural fidelity gates today; wiring the full 2x3 board into "
            "--auto is a flagged NG-5 follow-up — see the PR description) "
            "or manuscript.review_board.run_review_board directly"
        ),
    )

    # ── list ─────────────────────────────────────────────────────────────────
    sub.add_parser(
        "list",
        help="List manuscript folders for the project.",
    )

    # ── judge-emit (NG-4, design §1.9) ───────────────────────────────────────
    judge_emit_p = sub.add_parser(
        "judge-emit",
        help=(
            "Emit the cold-agent-judge fan-out task set(s) (design §1.9, "
            "Phase A) — writes judge/<gate>/_judge-tasks.json + "
            "_judge-canary-key.json. rv calls no LLM here; the hub fans "
            "cold subagent-judges out over the written tasks."
        ),
    )
    judge_emit_p.add_argument("slug", metavar="<slug>", help="Manuscript identifier.")
    judge_emit_p.add_argument(
        "--gate",
        choices=("support-matcher",),
        default="support-matcher",
        help=(
            "Which gate's task set to emit (support-matcher-only — the "
            "cold-read self-containment critic was removed; see DEVLOG)."
        ),
    )

    # ── judge-ingest (NG-4, design §1.9) ─────────────────────────────────────
    judge_ingest_p = sub.add_parser(
        "judge-ingest",
        help=(
            "Ingest the hub-fanned-out cold-judge verdicts (design §1.9, "
            "Phase C) from judge/<gate>/_judge-verdicts.json — id-join, "
            "canary-verify, fail-closed assembly. Diagnostic surface; "
            "`rv dag approve` re-ingests for the actual gate decision."
        ),
    )
    judge_ingest_p.add_argument("slug", metavar="<slug>", help="Manuscript identifier.")
    judge_ingest_p.add_argument(
        "--gate",
        choices=("support-matcher",),
        default="support-matcher",
        help=(
            "Which gate's verdicts to ingest (support-matcher-only — the "
            "cold-read self-containment critic was removed; see DEVLOG)."
        ),
    )

    return p


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    """Dispatch manuscript subcommands. Returns exit code."""
    # D1 (verb consolidation): expand / review are HARD-REMOVED stubs.
    if getattr(args, "_rv_removed_verb", None) is not None:
        from ..cli_removed_verbs import run_removed_verb_stub
        return run_removed_verb_stub(args)

    subcommand = getattr(args, "manuscript_cmd", None)

    if subcommand == "new":
        return _run_new(args)
    elif subcommand == "list":
        return _run_list(args)
    elif subcommand == "judge-emit":
        return _run_judge_emit(args)
    elif subcommand == "judge-ingest":
        return _run_judge_ingest(args)
    else:
        print(
            "rv manuscript: missing subcommand. "
            "Use `rv manuscript <project> new <slug> --type <type>`, "
            "`rv manuscript <project> judge-emit <slug>`, "
            "`rv manuscript <project> judge-ingest <slug>`, "
            "or `rv manuscript <project> list`. "
            "expand/review were HARD-REMOVED (D1, verb consolidation) — "
            "they now run automatically via the DAG's autonomous gates.",
            file=sys.stderr,
        )
        return 1


def _run_new(args: argparse.Namespace) -> int:
    """Scaffold a per-manuscript folder + (type-optional) Phase-1 manifest."""
    from research_vault.config import load_config
    from research_vault.manuscript import cmd_new

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv manuscript new: config error: {e}", file=sys.stderr)
        return 1

    try:
        note_path, tree_root, manifest = cmd_new(
            args.project,
            args.slug,
            ms_type_key=args.type,
            config=cfg,
            from_review=getattr(args, "from_review", None),
        )
        print(f"rv manuscript: created note: {note_path}")
        print(f"rv manuscript: folder: {tree_root}")
        if manifest is not None:
            n_nodes = len(manifest["nodes"])
            print(f"rv manuscript: Phase-1 manifest: {tree_root / 'phase1-dag.json'}")
            print(f"rv manuscript: manifest has {n_nodes} node(s) (run: {manifest['run_id']})")
            print(f"rv manuscript: start Phase-1 with: rv dag run {tree_root / 'phase1-dag.json'}")
        else:
            print(
                "rv manuscript: type has no Phase-1 (pass-through) — "
                f"run: rv manuscript {args.project} expand {args.slug}"
            )
        return 0
    except Exception as e:
        print(f"rv manuscript new: error: {e}", file=sys.stderr)
        return 1


def _run_expand(args: argparse.Namespace) -> int:
    """Emit Phase-2 manifest from the registered type's section_set."""
    from research_vault.config import load_config
    from research_vault.manuscript import cmd_expand

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv manuscript expand: config error: {e}", file=sys.stderr)
        return 1

    try:
        manifest = cmd_expand(args.project, args.slug, config=cfg)
        n_nodes = len(manifest["nodes"])
        tree_root = cfg.project_notes_dir(args.project) / "manuscripts" / args.slug
        print(f"rv manuscript expand: Phase-2 manifest: {tree_root / 'phase2-dag.json'}")
        print(f"rv manuscript expand: {n_nodes} node(s) (run: {manifest['run_id']})")
        print(f"rv manuscript expand: start Phase-2 with: rv dag run {tree_root / 'phase2-dag.json'}")
        return 0
    except FileNotFoundError as e:
        print(f"rv manuscript expand: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"rv manuscript expand: error: {e}", file=sys.stderr)
        return 1


def _run_review(args: argparse.Namespace) -> int:
    """Run the review-revise board and print an honest summary."""
    from research_vault.config import load_config
    from research_vault.manuscript import cmd_review

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv manuscript review: config error: {e}", file=sys.stderr)
        return 1

    try:
        result = cmd_review(args.project, args.slug, config=cfg)
        print(f"rv manuscript review: {result['honest_report']}")
        if result["cleared"]:
            print(
                f"rv manuscript review: cleared at round {result['cleared_at']} — "
                f"the human still makes the final approve-manuscript call."
            )
        else:
            nc = result.get("not_cleared") or {}
            print("rv manuscript review: NOT CLEARED.", file=sys.stderr)
            print(f"  {nc.get('persistent_weakness', '')}", file=sys.stderr)
        if result.get("escalation"):
            esc = result["escalation"]
            print(
                "rv manuscript review: FRAMEWORK ESCALATION (surface-not-auto): "
                f"{esc.get('note', '')}",
                file=sys.stderr,
            )
        return 0
    except RuntimeError as e:
        print(f"rv manuscript review: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"rv manuscript review: error: {e}", file=sys.stderr)
        return 1


def _run_list(args: argparse.Namespace) -> int:
    """List manuscript folders for the project."""
    from research_vault.config import load_config
    from research_vault.manuscript import cmd_list

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv manuscript list: config error: {e}", file=sys.stderr)
        return 1

    try:
        results = cmd_list(args.project, config=cfg)
        if not results:
            print(f"rv manuscript: no manuscripts for project {args.project!r}")
            return 0
        for item in results:
            print(f"  {item['slug']}: type={item['manuscript_type']}")
        return 0
    except Exception as e:
        print(f"rv manuscript list: error: {e}", file=sys.stderr)
        return 1


def _run_judge_emit(args: argparse.Namespace) -> int:
    """Emit the NG-4 cold-agent-judge fan-out task set(s) (design §1.9)."""
    from research_vault.config import load_config
    from research_vault.manuscript import cmd_judge_emit

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv manuscript judge-emit: config error: {e}", file=sys.stderr)
        return 1

    try:
        result = cmd_judge_emit(args.project, args.slug, config=cfg, gate=args.gate)
        for gate_name, r in result.items():
            n_tasks = len(r["tasks_doc"].get("tasks", []))
            n_canaries = len(r["canary_key_doc"].get("canaries", {}))
            n_batches = len(r["tasks_doc"].get("batches", []))
            print(
                f"rv manuscript judge-emit [{gate_name}]: wrote {n_tasks} task(s) "
                f"({n_canaries} canary probe(s), {n_batches} batch(es)) to "
                f"manuscripts/{args.slug}/judge/{gate_name}/_judge-tasks.json"
            )
            if n_tasks == 0:
                print(
                    f"rv manuscript judge-emit [{gate_name}]: nothing to check yet "
                    f"(no citations found / no draft text resolved) — honest no-op."
                )
        print(
            "rv manuscript judge-emit: hand the tasks file(s) to the hub for "
            "cold subagent-judge fan-out, then run "
            "`rv manuscript <project> judge-ingest <slug>` once "
            "_judge-verdicts.json lands."
        )
        return 0
    except FileNotFoundError as e:
        print(f"rv manuscript judge-emit: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"rv manuscript judge-emit: error: {e}", file=sys.stderr)
        return 1


def _run_judge_ingest(args: argparse.Namespace) -> int:
    """Ingest the hub-fanned-out cold-judge verdicts (design §1.9)."""
    from research_vault.config import load_config
    from research_vault.manuscript import cmd_judge_ingest

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv manuscript judge-ingest: config error: {e}", file=sys.stderr)
        return 1

    try:
        result = cmd_judge_ingest(args.project, args.slug, config=cfg, gate=args.gate)
        any_block = False
        for gate_name, r in result.items():
            print(f"rv manuscript judge-ingest [{gate_name}]: {r.get('honest_report', '')}")
            if r.get("canary_aborted"):
                print(
                    f"rv manuscript judge-ingest [{gate_name}]: CANARY ABORT "
                    f"(HALT-DECLARE) — {r['errors'][0] if r['errors'] else ''}",
                    file=sys.stderr,
                )
                any_block = True
            elif r.get("halt"):
                print(
                    f"rv manuscript judge-ingest [{gate_name}]: HALT-DECLARE — "
                    f"{r.get('halt_reason', '')}",
                    file=sys.stderr,
                )
                any_block = True
            else:
                for e in r.get("errors", []):
                    print(f"  BLOCK: {e}", file=sys.stderr)
                for w in r.get("warnings", []):
                    print(f"  SIGNAL: {w}", file=sys.stderr)
                if r.get("errors"):
                    any_block = True
        if any_block:
            print(
                "rv manuscript judge-ingest: this manuscript is NOT clear to "
                "approve on this gate. Re-run `rv dag approve` for the actual "
                "human-go decision.",
                file=sys.stderr,
            )
            return 1
        return 0
    except Exception as e:
        print(f"rv manuscript judge-ingest: error: {e}", file=sys.stderr)
        return 1
