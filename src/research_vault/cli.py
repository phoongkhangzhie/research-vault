"""cli.py — the Research Vault CLI dispatcher.

When to use: this is the entry point for the `rv` command. It dispatches to verb modules
via config-driven argparse. All verbs are project-scoped and all paths resolve via Config.

Verbs (SR-1):
  rv task <project> <subcommand>    — manage project task cards
  rv note <project> <subcommand>    — manage OKF notes
  rv control <project> <subcommand> — manage the coordination control file
  rv devlog <project> <subcommand>  — manage the project DEVLOG

Verbs added by later SRs are listed in `rv help` but not yet implemented.

Stdlib only. No import from ~/vault or any private instance.
"""

import argparse
import sys
from typing import Callable

from . import __version__
from .config import load_config

# ---------------------------------------------------------------------------
# Verb registry
# ---------------------------------------------------------------------------
# Each verb entry: (module_path, build_parser_fn, run_fn, when_to_use)
# when_to_use is the discovery surface: a short sentence surfacing the right trigger.

# We import lazily so that a bad verb module doesn't break `rv help`.
# Each entry is (module_attr, when_to_use_docstring).
# The build_parser + run functions are fetched from the module at dispatch time.

_VERB_REGISTRY: dict[str, dict] = {
    "task": {
        "module": "research_vault.task",
        "when_to_use": (
            "When you need to create, list, view, or update task cards for a project. "
            "Cards are markdown files with frontmatter stored in the project's tasks directory."
        ),
        "sr": "SR-1",
    },
    "note": {
        "module": "research_vault.note",
        "when_to_use": (
            "When you need to create or inspect OKF notes (literature, concepts, methods, "
            "experiments, findings, mocs) for a project. Enforces the type↔directory contract."
        ),
        "sr": "SR-1",
    },
    "control": {
        "module": "research_vault.control",
        "when_to_use": (
            "When you need to initialize, view, validate, or update the coordination control "
            "file for a project. The control file is the async manager-hub handshake bus."
        ),
        "sr": "SR-1",
    },
    "devlog": {
        "module": "research_vault.devlog",
        "when_to_use": (
            "When you need to create, append to, or check the freshness of a project's "
            "DEVLOG.md — the grounded decision and progress record."
        ),
        "sr": "SR-1",
    },
    # --- SR-2 (not yet implemented) ---
    "research": {
        "module": None,
        "when_to_use": (
            "When you want to run a research step (asta-backed), cite a paper, or annotate "
            "candidates. Requires asta + Zotero. Ships at SR-2."
        ),
        "sr": "SR-2 (coming)",
    },
    "cite": {
        "module": None,
        "when_to_use": (
            "When you need to resolve, add, or list citekeys against the project's Zotero "
            "collection. Requires ZOTERO_KEY. Ships at SR-2."
        ),
        "sr": "SR-2 (coming)",
    },
    "wait-for": {
        "module": None,
        "when_to_use": (
            "When you need to background-wait for an artifact to appear (file freshness, "
            "sacct terminal, url reachable) and then resume the session. Ships at SR-2."
        ),
        "sr": "SR-2 (coming)",
    },
    # --- SR-3 (not yet implemented) ---
    "dag": {
        "module": None,
        "when_to_use": (
            "When you need to run, tick, complete, or approve nodes in a multi-node "
            "research-loop DAG. The human-go node is the decision gate. Ships at SR-3."
        ),
        "sr": "SR-3 (coming)",
    },
}


def _load_verb(name: str):
    """Dynamically import a verb module. Returns (build_parser, run) or (None, None)."""
    entry = _VERB_REGISTRY.get(name, {})
    module_path = entry.get("module")
    if not module_path:
        return None, None
    import importlib
    mod = importlib.import_module(module_path)
    return mod.build_parser, mod.run


# ---------------------------------------------------------------------------
# help --check gate
# ---------------------------------------------------------------------------

def _check_verb_docstrings() -> list[str]:
    """Verify every registered verb has a when_to_use string (the discovery surface).

    Returns a list of violation strings. Empty = all clear.
    This is the doc-comment gate: `rv help --check` greens only when all verbs have
    a non-empty when_to_use, ensuring the discovery/trigger layer is maintained.
    """
    violations = []
    for verb, entry in _VERB_REGISTRY.items():
        when = entry.get("when_to_use", "").strip()
        if not when:
            violations.append(f"Verb {verb!r} has no when_to_use docstring.")
    return violations


# ---------------------------------------------------------------------------
# Top-level parser
# ---------------------------------------------------------------------------

def _build_top_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rv",
        description=(
            "Research Vault — an adoptable, zero-infra AI research-assistant framework.\n\n"
            "All verbs are project-scoped. Run `rv <verb> --help` for details.\n"
            "Use `rv help --check` to verify that all verbs have discovery docstrings."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"rv {__version__}"
    )

    sub = parser.add_subparsers(dest="verb", metavar="<verb>")

    # Register implemented verbs
    for verb_name, entry in _VERB_REGISTRY.items():
        if entry.get("module"):
            build_parser, _ = _load_verb(verb_name)
            if build_parser:
                build_parser(sub)

    # help verb (special — handles --check)
    help_p = sub.add_parser(
        "help",
        help="Show verb descriptions and discovery surfaces. Use --check to gate CI.",
    )
    help_p.add_argument(
        "--check",
        action="store_true",
        help=(
            "Exit 0 if every registered verb has a when_to_use docstring; exit 1 otherwise. "
            "Use in CI to enforce the discovery-surface contract."
        ),
    )

    return parser, sub


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """Entry point for the `rv` CLI. Returns exit code."""
    parser, _ = _build_top_parser()

    # Check if the verb is registered-but-unimplemented BEFORE argparse rejects it.
    # argparse only knows implemented verbs; future-SR verbs are in _VERB_REGISTRY
    # with module=None and must be handled here for a friendly error message.
    raw_argv = list(argv or sys.argv[1:])
    if raw_argv and raw_argv[0] in _VERB_REGISTRY:
        verb = raw_argv[0]
        entry = _VERB_REGISTRY[verb]
        if not entry.get("module"):
            sr = entry.get("sr", "a future SR")
            print(
                f"rv: verb {verb!r} is not yet implemented (ships at {sr}).",
                file=sys.stderr,
            )
            return 1

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 2

    if args.verb is None:
        parser.print_help()
        return 0

    # --- help verb ---
    if args.verb == "help":
        if args.check:
            violations = _check_verb_docstrings()
            if violations:
                print("rv help --check: FAIL")
                for v in violations:
                    print(f"  {v}")
                return 1
            print(f"rv help --check: OK — {len(_VERB_REGISTRY)} verbs, all have when_to_use.")
            return 0

        # Print verb table
        print("Research Vault verbs:\n")
        for verb_name, entry in _VERB_REGISTRY.items():
            sr = entry.get("sr", "")
            status = "" if entry.get("module") else f"  [{sr}]"
            print(f"  rv {verb_name:<16} {entry['when_to_use'][:60]}…{status}")
        print("\nRun `rv <verb> --help` for details. `rv help --check` validates docstrings.")
        return 0

    # --- dispatch to verb ---
    _, run_fn = _load_verb(args.verb)
    if run_fn is None:
        entry = _VERB_REGISTRY.get(args.verb, {})
        sr = entry.get("sr", "a future SR")
        print(
            f"rv: verb {args.verb!r} is not yet implemented (ships at {sr}).",
            file=sys.stderr,
        )
        return 1

    return run_fn(args)


if __name__ == "__main__":
    sys.exit(main())
