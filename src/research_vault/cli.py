"""cli.py — the Research Vault CLI dispatcher.

When to use: this is the entry point for the `rv` command. It dispatches to verb modules
via config-driven argparse. All verbs are project-scoped and all paths resolve via Config.

Verbs (SR-1):
  rv task <project> <subcommand>    — manage project task cards
  rv note <project> <subcommand>    — manage OKF notes
  rv control <project> <subcommand> — manage the coordination control file
  rv devlog <project> <subcommand>  — manage the project DEVLOG

Verbs (SR-CP): status — structured READ face for coordination state (rv status <project>)

Verbs (SR-2): project, cite, research, role, build-agents, mdstore, wt,
  git-health, lint, wait-for

Plugin seam (instance vs portable verbs):
  Portable verbs are the built-in set above — they ship with the package and
  run on any machine without instance-specific configuration.

  Instance verbs are project-specific extensions registered in the
  ``[verbs]`` section of ``research_vault.toml``:

    [verbs.my-verb]
    module = "myproject.verbs.my_verb"
    when_to_use = "When you need to run my project-specific step."

  Instance verbs are loaded at dispatch time from the config and merged
  into the verb registry. They shadow portable verbs of the same name,
  allowing instance-level overrides. Instance verb modules must expose
  ``build_parser`` and ``run`` in the same shape as portable verbs.

Stdlib only — no imports from private vault instances or project-specific paths.
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
    # --- SR-CP ---
    "status": {
        "module": "research_vault.status",
        "when_to_use": (
            "When you need a project's coordination / dispatch / blocker state — control "
            "sections, task board, DEVLOG tail, local git, DAG runs. "
            "This IS the tooled read face. "
            "Anti-pattern: do NOT `cat`/`Read` `control/*.md` and parse by eye — it "
            "silently misses live git/DAG/task state and parses stale prose "
            "(the SR-4-mistaken-for-undispatched incident, 2026-07-01); use `rv status` instead."
        ),
        "sr": "SR-CP",
    },
    # --- SR-1 ---
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
            "When you need to initialize, validate, reconcile, or MUTATE the coordination "
            "control file for a project. READ via `rv status`. MUTATE via "
            "`rv control post/spawn-request/return/close/edit/move`. "
            "Anti-pattern: do NOT open `control/*.md` and hand-type bullets — it races "
            "other agents and can author schema-invalid entries; do NOT `cat`/`Read` the "
            "file by eye — use `rv status` or `rv control reconcile` to read current state."
        ),
        "sr": "SR-1",
    },
    "devlog": {
        "module": "research_vault.devlog",
        "when_to_use": (
            "When you need to create, append to, check the freshness of, or SEARCH a "
            "project's DEVLOG.md — the grounded decision and progress record. "
            "Use `rv devlog index` for a one-liner per entry; `rv devlog search` to find "
            "entries by keyword. "
            "Anti-pattern: do NOT grep/cat DEVLOG.md directly to find entries — that loads "
            "the whole file and misses the structured index face."
        ),
        "sr": "SR-1",
    },
    # --- SR-2 ---
    "project": {
        "module": "research_vault.project",
        "when_to_use": (
            "When you need to register a new project, list registered projects, or manage "
            "the project config registry. Use `rv project add` to register a new project "
            "into research_vault.toml."
        ),
        "sr": "SR-2",
    },
    "cite": {
        "module": "research_vault.cite",
        "when_to_use": (
            "When you need to resolve, add, or list citekeys against the project's Zotero "
            "collection. Requires ZOTERO_KEY env var (or cross-platform keyring). "
            "Routes secrets through the SecretStore Protocol — never macOS security binary."
        ),
        "sr": "SR-2",
    },
    "research": {
        "module": "research_vault.research",
        "when_to_use": (
            "When you want to run a research step (asta-backed), find papers, annotate "
            "candidates vs corpus, or add papers via the dedup gate. Requires asta + Zotero. "
            "Takes default_project from config — never a compiled-in codename."
        ),
        "sr": "SR-2",
    },
    "role": {
        "module": "research_vault.role",
        "when_to_use": (
            "When you need to list, view, or manage agent roles in the project registry. "
            "Use `rv role list` to see all registered roles."
        ),
        "sr": "SR-2",
    },
    "build-agents": {
        "module": "research_vault.build_agents",
        "when_to_use": (
            "When you need to regenerate agent hat files from the role registry and "
            "role-doc templates. Runs the build-agents pipeline for all or a specific project."
        ),
        "sr": "SR-2",
    },
    "mdstore": {
        "module": "research_vault.mdstore",
        "when_to_use": (
            "When you need to check, archive, or inspect the markdown document store. "
            "Validates OKF link integrity, freshness, and document structure."
        ),
        "sr": "SR-2",
    },
    "wt": {
        "module": "research_vault.wt",
        "when_to_use": (
            "When you need to create, list, or remove git worktrees for project task branches. "
            "Use `rv wt add <task>` to create an isolated worktree on feat/<task>. "
            "Anti-patterns this prevents: committed-to-main directly · never made a worktree · "
            "working on main instead of an isolated branch."
        ),
        "sr": "SR-2",
    },
    "git-health": {
        "module": "research_vault.git_health",
        "when_to_use": (
            "When you need a cross-repo branch health report: which branches are merged, "
            "stale, or have unique content. Use --prune to clean up DELETE-classed branches. "
            "Anti-patterns caught: committed-to-main / never-made-a-worktree / hand-merged-red-CI."
        ),
        "sr": "SR-2",
    },
    "lint": {
        "module": "research_vault.lint",
        "when_to_use": (
            "When you need to run the project linter: checks for leakage of private "
            "codenames/paths, validates config schema, and enforces the zero-hardcoded-path rule."
        ),
        "sr": "SR-2",
    },
    "wait-for": {
        "module": "research_vault.wait_for",
        "when_to_use": (
            "When you need to background-wait for an artifact to appear (file freshness, "
            "sacct terminal, pr merged) and then fire a command on resolution. The caller "
            "returns immediately — no sleep-looping. Primitive that SR-3's DAG afterok composes."
        ),
        "sr": "SR-2",
    },
    # --- SR-GD ---
    "git-discipline": {
        "module": "research_vault.git_discipline",
        "when_to_use": (
            "When you need to install, check, or manage identity-free git-discipline hooks "
            "(protect-main + leakage scan + conventional-commit format). "
            "Use `rv git-discipline install` to set core.hooksPath per repo (worktrees inherit). "
            "Anti-patterns addressed: committed-to-main directly · never made a worktree · "
            "hand-merged red CI."
        ),
        "sr": "SR-GD",
    },
    # --- SR-3 ---
    "dag": {
        "module": "research_vault.dag.verbs",
        "when_to_use": (
            "When you need to run, tick, complete, approve, add, or insert nodes in a "
            "multi-node research-loop DAG. The human-go node is the solo decision gate: "
            "it blocks until ALL transitive upstream nodes are terminal, then `dag approve` "
            "is the exact command to run (printed by `dag status`). "
            "Afterok+watch edges gate on artifact freshness (OKF type-dir checked by vault check). "
            "In-session resolution only — no background pollers. "
            "For external watches use: rv wait-for <cond> --then 'rv dag tick <run_id>' &"
            " — SR-DISP dispatch discipline: agent nodes require 'spec' (non-empty pointer to "
            "the durable brief); absence is a ManifestError. Anti-patterns: (1) an agent node "
            "dispatched with no pointed 'spec' — always ground the dispatch; "
            "(2) a 'continues' resume across a durable-artifact boundary (a produces:/human-go "
            "node between the resumed ancestor and this node) — prefer a fresh dispatch "
            "pointed at the artifact instead."
        ),
        "sr": "SR-3, SR-DISP",
    },
}


def _load_verb(name: str, registry: dict | None = None):
    """Dynamically import a verb module. Returns (build_parser, run) or (None, None)."""
    reg = registry if registry is not None else _VERB_REGISTRY
    entry = reg.get(name, {})
    module_path = entry.get("module")
    if not module_path:
        return None, None
    import importlib
    mod = importlib.import_module(module_path)
    return mod.build_parser, mod.run


# ---------------------------------------------------------------------------
# Plugin seam: instance verbs loaded from config
# ---------------------------------------------------------------------------

def _load_instance_verbs() -> dict[str, dict]:
    """Load instance-specific verb extensions from the config's [verbs] section.

    Instance verbs are registered in research_vault.toml:

      [verbs.my-verb]
      module = "myproject.verbs.my_verb"
      when_to_use = "When you need to run my project-specific step."

    Instance verbs shadow portable verbs of the same name, allowing overrides.
    Modules must expose build_parser(parent) and run(args).

    Returns an empty dict if:
      - No config file is found (zero-config mode)
      - No [verbs] section in the config
      - The [verbs] section is malformed (warning printed, falls through)
    """
    try:
        cfg = load_config()
    except Exception:
        return {}
    verbs_cfg = cfg._raw.get("verbs", {})
    if not isinstance(verbs_cfg, dict):
        return {}
    result: dict[str, dict] = {}
    for verb_name, verb_entry in verbs_cfg.items():
        if not isinstance(verb_entry, dict):
            continue
        module = verb_entry.get("module", "").strip()
        when_to_use = verb_entry.get("when_to_use", "").strip()
        if not module:
            import sys as _sys
            print(
                f"rv: instance verb {verb_name!r} has no 'module' in config [verbs] — skipping.",
                file=_sys.stderr,
            )
            continue
        result[verb_name] = {
            "module": module,
            "when_to_use": when_to_use or f"Instance verb {verb_name!r} (see config [verbs]).",
            "sr": "instance",
        }
    return result


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

def _build_top_parser(instance_verbs: dict | None = None) -> argparse.ArgumentParser:
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

    # Build the merged registry: portable verbs first, then instance verbs (may shadow)
    merged_registry = dict(_VERB_REGISTRY)
    if instance_verbs:
        merged_registry.update(instance_verbs)

    # Register implemented verbs (portable + instance)
    for verb_name, entry in merged_registry.items():
        if entry.get("module"):
            build_parser, _ = _load_verb(verb_name, merged_registry)
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

    return parser, sub, merged_registry


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    """Entry point for the `rv` CLI. Returns exit code."""
    # Load instance verbs from config (plugin seam: portable vs instance verbs)
    instance_verbs = _load_instance_verbs()
    parser, _, merged_registry = _build_top_parser(instance_verbs)

    # Check if the verb is registered-but-unimplemented BEFORE argparse rejects it.
    # argparse only knows implemented verbs; future-SR verbs are in _VERB_REGISTRY
    # with module=None and must be handled here for a friendly error message.
    raw_argv = list(argv or sys.argv[1:])
    if raw_argv and raw_argv[0] in merged_registry:
        verb = raw_argv[0]
        entry = merged_registry[verb]
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
            total = len(merged_registry)
            print(f"rv help --check: OK — {total} verbs, all have when_to_use.")
            return 0

        # Print verb table (merged: portable + instance)
        print("Research Vault verbs:\n")
        for verb_name, entry in merged_registry.items():
            sr = entry.get("sr", "")
            status = "" if entry.get("module") else f"  [{sr}]"
            tag = " [instance]" if sr == "instance" else ""
            print(f"  rv {verb_name:<16} {entry['when_to_use'][:60]}…{status}{tag}")
        print("\nRun `rv <verb> --help` for details. `rv help --check` validates docstrings.")
        return 0

    # --- dispatch to verb (merged registry: instance verbs shadow portable) ---
    _, run_fn = _load_verb(args.verb, merged_registry)
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
