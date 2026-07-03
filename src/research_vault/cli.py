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
            "experiments, findings, mocs, datasets, figures, manuscript) for a project. "
            "Enforces the type↔directory contract. "
            "SR-8 datasets notes are provenance metadata — they POINT to the data artifact "
            "(path/URL/DOI + content-hash), never contain the data itself. "
            "SR-MS-1a manuscript notes are LaTeX-native POINTER notes — use `rv manuscript new` "
            "for richer creation (scaffolds the DAG + artifact tree). "
            "Anti-pattern: do NOT hand-copy a data path into a finding — file a "
            "datasets/ provenance note and afterok on it so data lineage is structural."
        ),
        "sr": "SR-1, SR-8, SR-MS-1a",
    },
    "control": {
        "module": "research_vault.control",
        "when_to_use": (
            "When you need to initialize, validate, reconcile, or MUTATE the coordination "
            "control file for a project. READ via `rv status`. MUTATE via "
            "`rv control post/spawn-request/return/close/edit/move`. "
            "Tier-3: use `rv control reconcile --gh-pr N [--repo owner/repo]` to "
            "fetch GitHub Actions CI state and include it in the drift check — "
            "the gate refuses to record a pass on red/unverified CI. "
            "Anti-pattern: do NOT open `control/*.md` and hand-type bullets — it races "
            "other agents and can author schema-invalid entries; do NOT `cat`/`Read` the "
            "file by eye — use `rv status` or `rv control reconcile` to read current state. "
            "Anti-pattern: do NOT hand-type 'CI green' into a merge decision — use "
            "`rv control reconcile --gh-pr N` so the gate fetches Actions state directly."
        ),
        "sr": "SR-1, SR-CIF",
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
            "When you need to stand up a WHOLE new research project as its own repo "
            "(git init + registry + OKF dirs + control bus + DEVLOG + architecture + "
            "corpus + crew, in one command) use `rv project new <slug> --code <c> "
            "--source <dir>`. Anti-pattern: hand-creating the repo + hand-copying "
            "scaffolding + hand-editing research_vault.toml (races the registry, skips "
            "the control-bus banner, forgets the OKF type-dirs). Use `rv project add` "
            "if you only need the registry entry for an existing repo. Use `rv project "
            "list` to enumerate all registered projects."
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
            "Takes default_project from config — never a compiled-in codename. "
            "Use 'rv research cited-by <id>' for forward snowball (who cites the seed). "
            "Use 'rv research references <id>' for backward snowball (what the seed cites — "
            "the seed's own reference list). "
            "Anti-pattern: do NOT hand-copy a bibliography — use 'rv research references' instead."
        ),
        "sr": "SR-2, SR-LR-1",
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
            " — SR-SCOPE grounding: agent nodes may carry a 'reads:' field (optional, "
            "resolve-checked) to bound the agent's reading-scope — a list of file/doc-section/"
            "bus-ref pointers the agent must read. Anti-pattern: (3) an agent node dispatched "
            "with an unbounded reading-scope (no 'reads:') will re-ground by broad exploration, "
            "re-inflating the token cost fresh dispatch was meant to kill — bound it with the "
            "artifacts the agent must read."
        ),
        "sr": "SR-3, SR-DISP, SR-SCOPE",
    },
    # --- SR-5 ---
    "init": {
        "module": "research_vault.init",
        "when_to_use": (
            "When you need to scaffold a fresh Research Vault instance from templates. "
            "Run `rv init [<dir>]` to create the instance root with config, control files, "
            "task dirs, doctrine, notes root (OKF type dirs), and the two canned demo projects "
            "(demo-research + demo-litreview). A real project is a SEPARATE repo — use "
            "`rv project add` after init. Refuses to overwrite an existing instance."
        ),
        "sr": "SR-5",
    },
    "check": {
        "module": "research_vault.check",
        "when_to_use": (
            "When you need to verify prerequisites before running any research loop. "
            "Checks: Claude CLI (required), ANTHROPIC_API_KEY (required), "
            "asta (optional), Zotero/ZOTERO_KEY (optional). "
            "Run `rv check` at the start of every new session or after environment changes. "
            "Exit 0 = all required present; exit 1 = missing prerequisites."
        ),
        "sr": "SR-5",
    },
    # --- SR-WB ---
    "wandb": {
        "module": "research_vault.wandb_pull",
        "when_to_use": (
            "When an experiment logged to W&B and you need its final metrics, or when "
            "you want to wait until a run finishes and attach metrics to the experiment "
            "note. `rv wandb pull` uses the `wandb` SDK (a documented prerequisite — "
            "pip install wandb). Use `rv wandb pull <run-id> --experiment <exp-id> "
            "--project <slug>` to attach results→hash→run provenance to the experiment "
            "note. "
            "Anti-pattern: do NOT hand-script `wandb.Api()` in a one-off or hand-copy "
            "metrics into a finding — use `rv wandb pull --experiment` so results carry "
            "a content-hash + provenance chain. "
            "Anti-pattern: do NOT use `wandb:` as a wait predicate and ignore the state "
            "field — a failed/crashed run wakes the waiter with its specific state so "
            "SR-RETRY can key retry off failure."
        ),
        "sr": "SR-WB",
    },
    # --- SR-6 ---
    "compute": {
        "module": "research_vault.compute",
        "when_to_use": (
            "When you need to see how to run on this environment (rv compute show), "
            "resolve env/tier/flags for a specific job/model (rv compute explain <job>), "
            "capture a cluster gotcha as a declared rule (rv compute lesson add), or "
            "record a run outcome so the manifest improves from real experience "
            "(rv compute outcome add). "
            "Anti-pattern: do NOT re-probe the cluster by trial-submit to learn what "
            "env/tier to use — rv compute show / rv doctor already declare it. "
            "Memory is flimsy; this tooling makes it robust."
        ),
        "sr": "SR-6",
    },
    "doctor": {
        "module": "research_vault.doctor",
        "when_to_use": (
            "When you need to probe and cache compute environment capabilities "
            "(conda envs, SLURM/PBS scheduler, CLI tools, GPU presence). "
            "Run `rv doctor` once after setup or after environment changes — agents "
            "query the cache; re-run with --refresh on env-change or failure. "
            "Anti-pattern: do NOT re-probe the cluster by trial-submit to learn what "
            "env/tier to use — rv doctor already discovers and caches it. "
            "Degrades gracefully without a scheduler: reports 'not available', no traceback."
        ),
        "sr": "SR-6",
    },
    "plugins": {
        "module": "research_vault.plugins",
        "when_to_use": (
            "When you need to see which adapter plugins are registered "
            "(notifiers, compute backends, secret stores) and which are currently "
            "active (config-selected). Use `rv plugins list`. "
            "D-SR6-1=THIN: surfaces static registries only — no entry-points "
            "self-registration. "
            "Anti-pattern: do NOT re-probe the cluster by trial-submit to learn what "
            "adapters are wired — rv plugins list already shows the registry."
        ),
        "sr": "SR-6",
    },
    # --- SR-MS-1a ---
    "manuscript": {
        "module": "research_vault.manuscript.verbs",
        "when_to_use": (
            "When you need to scaffold a grounded manuscript draft from a project's "
            "verified OKF graph. Use `rv manuscript new <project> <id> --thesis '...'` "
            "to create the manuscript OKF note + manuscripts/<id>/ LaTeX tree + the "
            "16-node drafting-DAG manifest (§5J.2). Drive the loop with `rv dag run`. "
            "Anti-pattern: do NOT hand-write a .tex and hand-type citations/numbers — "
            "run `rv manuscript new --thesis` so the draft carries a closed .bib from "
            "your `literature/` notes, machine-injected results, and structural "
            "\\cite→source verification. A hand-typed number or an uncited claim is "
            "exactly the fabrication this prevents. Use `rv manuscript list` to "
            "enumerate all manuscript notes for a project."
        ),
        "sr": "SR-MS-1a",
    },
    # --- SR-FIG / SR-FIG-REC ---
    "figure": {
        "module": "research_vault.figure",
        "when_to_use": (
            "When you have an experiment note (experiments/<id>.md) with results attached "
            "(results_location/results_hash populated by `rv wandb pull`) and need a "
            "publication-quality plot with full provenance. Use `rv figure new --experiment <id>` "
            "to declare the figure spec (experiment results hash + filter recipe + style preset), "
            "`rv figure preview` to inspect the exact data frame before rendering, "
            "`rv figure render` to produce SVG+PNG images via the apply_style seam, and "
            "`rv figure recommend <view>` to get ranked plot-type suggestions grounded in the "
            "Cleveland–McGill perceptual-accuracy ladder + Mackinlay expressiveness→effectiveness. "
            "When `rv figure new` is called without `--type`, the recommender auto-picks and "
            "prints the rationale; `--type` overrides silently (recommend-not-mandate). "
            "The optional `--benchmark <id>` references a shared datasets/ note for comparison "
            "overlay only — it is never the primary source. "
            "Requires pip install research-vault[figures] for preview/render. "
            "Anti-pattern: do NOT hand-write a one-off matplotlib script and drop a PNG into "
            "a finding — declare `rv figure new` against an `experiments/` note so the figure "
            "carries experiment→results→filter→style provenance and afterok-able lineage. "
            "One-off scripts break the reproducibility chain that makes figures publishable. "
            "Anti-pattern: do NOT pick a plot type by gut feel or habit (eyeballing a chart "
            "type skips the perceptual encoding-accuracy check) — use `rv figure recommend` "
            "to get a ranked recommendation for your data's structure and task "
            "(comparison, trend, relationship, distribution, composition, lookup, deviation)."
        ),
        "sr": "SR-FIG, SR-FIG-REC",
    },
    # --- SR-PLAN-2 ---
    "result": {
        "module": "research_vault.result",
        "when_to_use": (
            "When you need to assert a numeric predicate holds against a hash-verified "
            "experiment note's results — for the conditional-ablation watch:cmd: trigger "
            "in a pre-registered DAG (§5K.7). "
            "Use: rv result assert <experiments/<id>.md> --metric M --op gt --value V. "
            "Exit 0 = predicate TRUE (conditional fires); exit 1 = FALSE or error. "
            "Optional: --run-id / --node-id logs the predicate string + SHA-256 hash + "
            "evaluated result into DAG run state meta (§5K.5.4 tamper-evident audit). "
            "Anti-pattern: do NOT hand-read results files and hard-code a threshold in "
            "a shell one-liner — use rv result assert so the predicate is hash-verified "
            "against the recorded results_hash and logged to run state for reproducibility."
        ),
        "sr": "SR-PLAN-2",
    },
    # --- SR-PLAN-1 ---
    "plan": {
        "module": "research_vault.plan.verbs",
        "when_to_use": (
            "When you need to lint a pre-registration plan note or inspect the plan_tips "
            "prompt seam. "
            "Use `rv plan check <experiments/<id>-plan.md>` to run the K-2 structural "
            "shape-lint BEFORE the human-go-plan approval gate: checks branch-presence "
            "(every diagnosis table row has a named conclusion + committed action — no "
            "empty cells, no 'fallback', no 'TBD') and one-component-per-ablation "
            "(each supporting ablation manipulates exactly ONE component). "
            "Use `rv plan tips [--key <key>]` to inspect the plan_tips seam (Ada's "
            "defaults or adopter override from [plan_style] in research_vault.toml). "
            "Anti-pattern: do NOT skip plan check before human-go-plan — the shape-lint "
            "is the rejects-only structural screen (charter §9); the plan-critic (Argus) "
            "judges semantic completeness but cannot substitute for missing outcome rows. "
            "This verb is note.py-FREE: plan fields (plan_kind/covers/plan_role/"
            "supports_main/stance) are agent-authored content, not cmd_new templates."
        ),
        "sr": "SR-PLAN-1",
    },
    # --- SR-LR-1 + SR-LR-2 ---
    "review": {
        "module": "research_vault.review.verbs",
        "when_to_use": (
            "When you need to conduct a structured, pre-registered, saturation-gated "
            "literature review. Use `rv review new <project> <scope> --question '...'` "
            "to scaffold the Phase-1 DAG (review-scope → [HG:approve-protocol] → "
            "review-search → review-snowball → [HG:coverage-gate]) with protocol-freeze, "
            "internal saturation loop (both forward cited-by + backward references), and "
            "coverage-critic gates. "
            "The `review-scope` node MUST file a `_protocol.md` with a non-empty "
            "`counter-position` field (L-2 gate, §5L.3) — the anti-fishing structural "
            "obligation. "
            "After `coverage-gate` approval: run `rv review expand <project> <scope>` "
            "to emit the Phase-2 fan-out (one `relate-<key>` node per [NEW] citekey → "
            "`review-synthesize` → `review-coverage-critic` → `[HG:approve-review]`). "
            "Use `rv review list <project>` to enumerate all reviews. "
            "Use `rv review tips [--key <key>]` to inspect the review_tips seam. "
            "SR-LR-2 gap-driven pass (§5L.7): use `rv review gap-scan <project>` to "
            "detect typed research gaps (knowledge_void, contradictory, evaluation_void, "
            "absent_row) from the OKF corpus + an optional manuscript critic report. "
            "This is a rejects-only SCREEN — it PROPOSES gaps, never auto-fires a review. "
            "Use `rv review gap-scope <project> <gap-id> <scope>` to auto-author a "
            "targeted Part-1 scope from the gap record (question ← claim verbatim; "
            "seed_queries ← per-type templates; snowball_seeds ← anchor citekeys). "
            "Use `rv review gap-close <project> <gap-id> --status <status>` to stamp "
            "closure (proven-open = gap is a candidate research contribution). "
            "Anti-pattern: do NOT hand-collect papers without `rv review new` — the "
            "hand-run path has no `_protocol.md` freeze, no saturation curve, and no "
            "rejects-only coverage critic (the coverage gate cannot fire without the "
            "artifacts `rv review new` scaffolds). "
            "Anti-pattern: do NOT auto-fire a gap-driven pass — gap-scan is a rejects-only "
            "screen; the human authorizes each targeted pass via gap-scope (no auto-fire). "
            "Anti-pattern: do NOT call `rv research` stdout and scrape it for saturation "
            "counts — import `_load_corpus_index` and `_corpus_annotation` from "
            "`research_vault.research` directly (the corpus-helper import rule, §5L.11)."
        ),
        "sr": "SR-LR-1, SR-LR-2",
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
