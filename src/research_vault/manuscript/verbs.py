"""manuscript/verbs.py — rv manuscript subcommand dispatcher (PR-M1, type-generic core).

When to use: use ``rv manuscript <project> new <slug> --type <type>`` to scaffold
a per-manuscript folder (design §0/§12: NOT an OKF taxonomy — a per-manuscript
``manuscripts/<slug>/{_manuscript.md, main.tex, sections/, refs.bib, figures/}``
folder). ``rv manuscript <project> expand <slug>`` emits the Phase-2 draft
manifest generically from the registered type's section table. ``rv manuscript
<project> review <slug>`` will drive the review-revise board once PR-M5 lands
(raises loudly today — never a silent no-op). ``rv manuscript <project> list``
enumerates all manuscripts for a project.

This is the ONLY path that creates the type-registered per-manuscript folder +
Phase-1/2 DAG manifests.

Anti-pattern: do NOT hand-write a .tex and hand-collect citations from OKF
piles — run ``rv manuscript new`` so the per-manuscript folder carries the
type-generic scaffold; the hermetic ``.bib`` (PR-M2), the fidelity gates
(PR-M3), the equation machinery (PR-M4), and the review-revise board (PR-M5)
all plug into this same folder as they land.

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

    Anti-pattern: do NOT hand-write a .tex and hand-type citations/numbers —
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
            "manuscripts/<slug>/{_manuscript.md, main.tex, sections/, refs.bib, figures/}."
        ),
    )
    new_p.add_argument(
        "slug",
        metavar="<slug>",
        help="Manuscript identifier slug (e.g. 'survey-llm-eval').",
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

    # ── expand ──────────────────────────────────────────────────────────────
    expand_p = sub.add_parser(
        "expand",
        help=(
            "Emit the Phase-2 draft manifest generically from the registered "
            "type's section_set: section(s) -> assemble -> [HG:approve-manuscript]."
        ),
    )
    expand_p.add_argument(
        "slug",
        metavar="<slug>",
        help="Manuscript identifier (same as used in rv manuscript new).",
    )

    # ── review ────────────────────────────────────────────────────────────────
    review_p = sub.add_parser(
        "review",
        help=(
            "PR-M5 stub: the review-revise board (2 rounds x 3 reviewers) is not "
            "built yet — this raises loudly rather than silently no-op-ing."
        ),
    )
    review_p.add_argument(
        "slug",
        metavar="<slug>",
        help="Manuscript identifier.",
    )

    # ── list ─────────────────────────────────────────────────────────────────
    sub.add_parser(
        "list",
        help="List manuscript folders for the project.",
    )

    return p


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    """Dispatch manuscript subcommands. Returns exit code."""
    subcommand = getattr(args, "manuscript_cmd", None)

    if subcommand == "new":
        return _run_new(args)
    elif subcommand == "expand":
        return _run_expand(args)
    elif subcommand == "review":
        return _run_review(args)
    elif subcommand == "list":
        return _run_list(args)
    else:
        print(
            "rv manuscript: missing subcommand. "
            "Use `rv manuscript <project> new <slug> --type <type>`, "
            "`rv manuscript <project> expand <slug>`, "
            "`rv manuscript <project> review <slug>`, "
            "or `rv manuscript <project> list`.",
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
    """PR-M5 stub: raises loudly."""
    from research_vault.config import load_config
    from research_vault.manuscript import cmd_review

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv manuscript review: config error: {e}", file=sys.stderr)
        return 1

    try:
        cmd_review(args.project, args.slug, config=cfg)
        return 0
    except NotImplementedError as e:
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
