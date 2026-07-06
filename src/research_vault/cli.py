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
import os
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
            "experiments, findings, mocs, datasets) for a project. "
            "Enforces the type↔directory contract. "
            "SR-8 datasets notes are provenance metadata — they POINT to the data artifact "
            "(path/URL/DOI + content-hash), never contain the data itself. "
            "Anti-pattern: do NOT hand-copy a data path into a finding — file a "
            "datasets/ provenance note and afterok on it so data lineage is structural."
        ),
        "sr": "SR-1, SR-8",
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
            "Use `rv devlog <project> index` for a one-liner per entry; "
            "`rv devlog <project> search` to find entries by keyword. "
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
            "the control-bus banner, forgets the OKF type-dirs). "
            "Use `rv project add <name> --code <c> --source <dir>` "
            "if you only need the registry entry for an existing repo. Use `rv project "
            "list` to enumerate all registered projects. "
            "SR-XPB — cross-project edge stewardship (hub coordination act): "
            "use `rv project relate <a> <b> --kind <why>` to declare a cross-project "
            "edge (grants intentional reach for corroboration); "
            "`rv project relate <a> <b> --remove` to prune a stale edge; "
            "`rv project edges` to surface all declared edges. "
            "--kind is REQUIRED when declaring. "
            "Anti-pattern: do NOT blanket-relate all projects (forfeits narrowing); "
            "declare on genuine relatedness (shared methodology, domain, or data) only."
        ),
        "sr": "SR-2, SR-XPB",
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
            "Anti-pattern: do NOT hand-copy a bibliography — use 'rv research references' instead. "
            "SR-XPB — cross-project corroboration (gated to declared peers): "
            "use `rv research corroborate \"<claim>\" --from <project>` to search declared "
            "peer projects for corroborating evidence. --from is REQUIRED. "
            "Use `--emit <path>` to write a candidates JSON for the judge node. "
            "If no declared edges, the tool prints a discovery nudge: "
            "run `rv project relate <from> <peer> --kind <why>` first. "
            "Anti-pattern: do NOT substring-grep across all projects (ignores the declared-edge "
            "gate, no rank, no provenance anchor) — use rv research corroborate instead."
        ),
        "sr": "SR-2, SR-LR-1, SR-XPB",
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
    # --- SR-3 / SR-HUB-DAG / SR-DAG-BRIEF ---
    "dag": {
        "module": "research_vault.dag.verbs",
        "when_to_use": (
            "When you need to run, tick, complete, approve, add, insert, or brief nodes in a "
            "multi-node research-loop DAG. The human-go node is the solo decision gate: "
            "it blocks until ALL transitive upstream nodes are terminal, then `dag approve` "
            "is the exact command to run (printed by `dag status`). "
            "IMPORTANT: `rv dag approve` is a HUMAN-GO gate — crew agents cannot call it on "
            "their own behalf. A dispatched subagent has NO controlling TTY and no provisioned "
            "token — the gate will refuse and return 1 (state unchanged). The human operator "
            "runs approve at their terminal or via a provisioned token. [crew-cannot-self-approve] "
            "Walk protocol (hub): (1) `rv dag status <run_id>` — read current state; "
            "(2) `rv dag brief <run_id> <node_id>` — emit the deterministic dispatch brief "
            "for the node; (3) dispatch the EMITTED brief verbatim to the crew subagent; "
            "(4) `rv dag complete <run_id> <node_id>` — advance after the subagent returns. "
            "EMIT, DON'T HAND-ROLL: never hand-transcribe a node's spec/reads into a brief — "
            "use `rv dag brief`. The brief is a deterministic function of the node + run state. "
            "Use `rv dag templates` to discover the built-in research loops (experiment, "
            "lit-review) with their scaffolder verb, entry command, and "
            "human-go gate locations — the discovery entry before starting any new loop. "
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
            " Anti-pattern: (4) hand-writing the dispatch brief — use `rv dag brief` instead."
        ),
        "sr": "SR-3, SR-DISP, SR-SCOPE, SR-HUB-DAG, SR-DAG-BRIEF",
    },
    # --- SR-5 ---
    "start": {
        "module": "research_vault.start",
        "when_to_use": (
            "The front door — launch Claude Code in your vault so the session becomes "
            "Alfred, the hub with the crew as subagents. Run `rv start [<vault_path>]` "
            "to verify the vault (research_vault.toml + CLAUDE.md present) and the "
            "runtime (claude on PATH) before exec-replacing with claude. Forwards any "
            "extra args to claude. "
            "Anti-pattern: do NOT run bare `claude` in a random directory — `rv start` "
            "verifies the vault and the runtime first, so the session always boots "
            "correctly as Alfred."
        ),
        "sr": "SR-5",
    },
    "init": {
        "module": "research_vault.init",
        "when_to_use": (
            "When you need to scaffold a fresh Research Vault instance from templates. "
            "Run `rv init [<dir>]` to create the instance root with: config "
            "(research_vault.toml), git repo, CLAUDE.md hub-bootstrap, crew subagent "
            "hats (.claude/agents/), doctrine/, QUICKSTART.md, and the notes root (OKF "
            "type dirs). Real projects are SEPARATE repos — register them with "
            "`rv project add` after init. Refuses to overwrite an existing instance."
        ),
        "sr": "SR-5",
    },
    "update": {
        "module": "research_vault.update",
        "when_to_use": (
            "When you have upgraded the package (`pip install --upgrade research-vault`) "
            "and need to propagate the new framework into THIS vault. Run `rv update` to "
            "refresh the framework-managed files (doctrine/, CLAUDE.md, QUICKSTART.md) and "
            "RECOMPOSE the crew hats from the upgraded doctrine — USER-OWNED content "
            "(notes/, projects, control/, research_vault.toml, DEVLOG.md, architecture.md) "
            "is never touched. Use `rv update --dry-run` (or `--check`) to preview the "
            "per-file plan (NEW / CHANGED / USER-MODIFIED→backup / unchanged) without "
            "writing. A locally-modified framework file is backed up to <path>.rv-bak "
            "before the new version installs; `--skip-modified` keeps yours instead. "
            "Anti-pattern: do NOT hand-copy doctrine/ or CLAUDE.md from the package into "
            "your vault after an upgrade — `rv update` does it in place, preserves user "
            "edits (backup), and recomposes the hats (which are DERIVED, not files). "
            "Anti-pattern: do NOT run it on a dirty tree — commit/stash first so the "
            "update diff is clean (or pass --force)."
        ),
        "sr": "SR-RV-UPDATE",
    },
    "check": {
        "module": "research_vault.check",
        "when_to_use": (
            "When you need to verify prerequisites before running any research loop. "
            "The agent runtime (Claude CLI) is the ONLY hard requirement — there is no "
            "required API key. Provider keys, s2, asta, W&B, Zotero, and compute are "
            "FEATURE-REQUIRED: each shows 'locked' until you add it, never a FAIL. "
            "Also reports Toolkit Tier-1 (core) + Tier-2 (GPU/local). "
            "Run `rv check` at the start of every new session or after environment changes. "
            "Exit 0 = the runtime is present; exit 1 only if the runtime is missing. "
            "Run `rv onboard` for a guided setup of the locked features; "
            "`rv bootstrap` if Tier-1 packages are missing. "
            "Anti-pattern: do NOT treat a locked feature as a failure — a missing "
            "provider/s2/wandb/zotero key never fails the check; it just gates that one "
            "capability until you run `rv onboard`."
        ),
        "sr": "SR-5",
    },
    "onboard": {
        "module": "research_vault.onboard",
        "when_to_use": (
            "When a fresh adopter needs a guided, idempotent first-run setup — the "
            "front door that turns `rv check`'s locked features green. Walks runtime → "
            "provider key(s) → s2 → asta → wandb → zotero → compute; explains what each "
            "unlocks, shows its request-form URL, and (at a TTY) reads secrets via "
            "getpass and stores them in the system keyring under the unified registry "
            "SSOT — so `rv check` and the runtime read them back. Re-run any time: "
            "satisfied steps are skipped (state is re-derived from `rv check`, no state "
            "file). Use `rv onboard --print` to print remediation steps instead of "
            "prompting (or in a non-TTY). "
            "Anti-pattern: do NOT hand-write a plaintext `.env` of API keys — `rv "
            "onboard` stores them in the OS keyring (never echoed, never a file); the "
            "runtime + `rv check` resolve them from there. "
            "Anti-pattern: do NOT `keyring set research_vault ...` (underscore) — the "
            "unified service is `research-vault` (hyphen); `rv onboard` uses the right "
            "one automatically."
        ),
        "sr": "SR-ONBOARD",
    },
    # --- SR-PKG ---
    "bootstrap": {
        "module": "research_vault.bootstrap",
        "when_to_use": (
            "When Tier-1 toolkit packages are missing (e.g. after `pip install research-vault "
            "--no-deps`, or on a fresh machine). Creates a `.venv` in the current directory "
            "and pip-installs the research toolkit — Tier-1 (model SDKs, data, stats, eval, "
            "multilingual, utilities) as a hard requirement; Tier-2 (GPU-fragile: torch, "
            "transformers, accelerate, etc.) best-effort + tolerated. "
            "Run `rv check` after to verify the installed stack. "
            "Anti-pattern: do NOT pip-install Tier-2 on a CPU-only laptop — "
            "install it on your GPU box with `pip install research-vault[local]` instead. "
            "Anti-pattern: do NOT install into the system Python — `rv bootstrap` always "
            "uses an isolated `.venv`."
        ),
        "sr": "SR-PKG",
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
    # --- SR-6 / SR-CO ---
    "compute": {
        "module": "research_vault.compute",
        "when_to_use": (
            "When you need to declare your compute environment (rv compute init), "
            "see how to run on this environment (rv compute show), "
            "resolve env/tier/flags for a specific job/model (rv compute explain <job>), "
            "capture a cluster gotcha as a declared rule (rv compute lesson add), or "
            "record a run outcome so the manifest improves from real experience "
            "(rv compute outcome add). "
            "DECLARE → DISCOVER order: `rv compute init` (declare WHERE) → "
            "`rv doctor` (discover WHAT per backend) → `rv compute show` (verify). "
            "Anti-pattern: do NOT re-probe the cluster by trial-submit to learn what "
            "env/tier to use — rv compute show / rv doctor already declare it. "
            "Do NOT hand-edit compute_manifest.json from scratch — use rv compute init. "
            "Anti-pattern (SR-EP-ROLE): do NOT declare a data-transfer node (DTN) as a "
            "compute backend and hope the crew guesses its role — give each endpoint a "
            "when_to_use so the run node knows which endpoint to stage data on vs submit "
            "jobs on. Use a shared host_group tag to express that a compute node and a "
            "transfer node reach the same underlying cluster/filesystem."
        ),
        "sr": "SR-6, SR-CO, SR-EP-ROLE",
    },
    "doctor": {
        "module": "research_vault.doctor",
        "when_to_use": (
            "When you need to probe and cache compute environment capabilities "
            "(conda envs, SLURM/PBS scheduler, CLI tools, GPU presence) — "
            "per each DECLARED backend from compute_manifest.json. "
            "Run `rv doctor` AFTER `rv compute init` (declare first, then discover). "
            "Re-run with --refresh on env-change or failure. "
            "Anti-pattern: do NOT re-probe the cluster by trial-submit to learn what "
            "env/tier to use — rv doctor already discovers and caches it. "
            "Do NOT run rv doctor before rv compute init — doctor iterates declared "
            "backends; running it first is useless when your compute is a remote cluster. "
            "Degrades gracefully without a scheduler: reports 'not available', no traceback."
        ),
        "sr": "SR-6, SR-CO",
    },
    # --- SR-MODEL-SEAM ---
    "observability": {
        "module": "research_vault.observability_cli",
        "when_to_use": (
            "When you need to DISCOVER or TEST your model-seam observability wiring "
            "before a run — so you don't discover at teardown that you logged zero "
            "records (the P1 failure). Use `rv observability probe` for a rejects-only "
            "check of BOTH planes (Plane A traces via weave/langfuse/local-JSONL + "
            "Plane B classic W&B run for `rv wandb pull`) with NO network call or model "
            "spend — exit 1 if a run would produce zero records. Use `rv observability "
            "status` to see the configured backend, run-logging, W&B target, and the "
            "local JSONL trace path. "
            "The seam itself is reached from a harness via "
            "load_adapters(cfg).model.complete(model=..., messages=...) — never a "
            "hand-rolled anthropic/openai client (that produces ZERO records). "
            "Anti-pattern: do NOT start a long run and find out at teardown you "
            "produced zero records — `rv observability probe` first. "
            "Anti-pattern: do NOT hand-wire litellm callbacks in a harness — the "
            "ModelClient seam registers them once, automatically."
        ),
        "sr": "SR-MODEL-SEAM",
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
    # --- SR-APPROVE-GATE ---
    "approval": {
        "module": "research_vault.approval",
        "when_to_use": (
            "When you need to manage the human-presence gate at rv dag approve. "
            "Use `rv approval setup` (ONCE at a TTY) to provision a token + write the "
            "fingerprint to config — required before non-interactive approve calls work. "
            "Use `rv approval disable` to turn the gate off (signed when token is "
            "provisioned; unsigned 'trust-me mode' without one). "
            "Use `rv approval enable` to re-arm. "
            "Use `rv approval status` to see current gate state. "
            "Anti-pattern: do NOT edit research_vault.toml directly to set enforce=false — "
            "a raw toml edit is INERT when a token is provisioned (the gate verifies the "
            "HMAC enforce_sig). Only `rv approval disable` writes the valid sig. "
            "Crew agents CANNOT self-approve — by design. [crew-cannot-self-approve]"
        ),
        "sr": "SR-APPROVE-GATE",
    },
    # --- SR-HUB-DAG §B / SR-HARNESS-P2 ---
    "experiment": {
        "module": "research_vault.experiment",
        "when_to_use": (
            "When you need to start a pre-registered experiment study. "
            "Use `rv experiment <project> new <id> --question '...'` "
            "to scaffold the pre-registration plan note skeleton "
            "(`experiments/<id>-plan.md`, plan_kind: preregistration, covers: skeleton) "
            "AND emit a REGISTERED experiment DAG manifest mirroring the research-loop.json "
            "topology (plan → plan-critic → [HG:human-go-plan] → "
            "{per-main: harness→harness-review→[HG:human-go-harness-main<k>]} → "
            "{per-main: run→score→analyze (+ablations)} → [HG:human-go-conditionals-*] → "
            "[HG:human-go-findings] → methods-update). "
            "Use `--shared-harness` when all mains share the same harness implementation "
            "(emits one shared triple instead of one per main). "
            "Prints the exact next commands including the harness sub-sequence: "
            "`rv dag run <manifest>`, then plan freeze at human-go-plan, then per-main "
            "`rv plan freeze-harness <run_id> <plan-note> --scope main<k> --harness-commit <sha>` "
            "at each human-go-harness-main<k> gate. "
            "Anti-pattern: do NOT run a pre-registered study as ad-hoc crew dispatches — "
            "`rv experiment new` registers the DAG so `rv plan freeze` has a run_id to "
            "hash; hand-dispatching silently loses the pre-registration guarantee (K-3 "
            "covers:-hash + harness SHAs never get bound to a run_id)."
        ),
        "sr": "SR-HUB-DAG",
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
    # --- SR-PLAN-1 / SR-HARNESS-P2 ---
    "plan": {
        "module": "research_vault.plan.verbs",
        "when_to_use": (
            "When you need to lint a pre-registration plan note, freeze the K-3 hash, "
            "or record a reviewed harness commit SHA. "
            "Use `rv plan check <experiments/<id>-plan.md>` to run the K-2 structural "
            "shape-lint BEFORE the human-go-plan approval gate: checks branch-presence "
            "(every diagnosis table row has a named conclusion + committed action — no "
            "empty cells, no 'fallback', no 'TBD') and one-component-per-ablation "
            "(each supporting ablation manipulates exactly ONE component). "
            "Use `rv plan freeze <run-id> <plan-note>` immediately after "
            "`rv dag approve <run-id> human-go-plan` to hash the covers:-freeze-set (K-3). "
            "Use `rv plan freeze-harness <run-id> <plan-note> --scope main<k> "
            "--harness-commit <sha>` after each human-go-harness-main<k> approval to "
            "record the reviewed harness commit SHA and extend the K-3 hash. "
            "Use `rv plan tips [--key <key>]` to inspect the plan_tips seam (researcher's "
            "defaults or adopter override from [plan_style] in research_vault.toml). "
            "Anti-pattern: do NOT skip plan check before human-go-plan — the shape-lint "
            "is the rejects-only structural screen (charter §9); the plan-critic (reviewer) "
            "judges semantic completeness but cannot substitute for missing outcome rows. "
            "Anti-pattern: do NOT call freeze-harness without a prior freeze — it is "
            "FAIL-CLOSED on absent plan_freeze. "
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
            "literature review. Use `rv review <project> new <scope> --question '...'` "
            "to scaffold the Phase-1 DAG (review-scope → [HG:approve-protocol] → "
            "review-search → review-snowball → [HG:coverage-gate]) with protocol-freeze, "
            "internal saturation loop (both forward cited-by + backward references), and "
            "coverage-critic gates. "
            "The `review-scope` node MUST file a `_protocol.md` with a non-empty "
            "`counter-position` field (L-2 gate, §5L.3) — the anti-fishing structural "
            "obligation, MECHANICALLY ENFORCED at `rv dag approve <run_id> "
            "approve-protocol`: an empty/missing `counter-position` field REFUSES the "
            "approval (nonzero exit, node stays `awaiting-go`, no state mutation) — "
            "not agent-prose-only (task #33). Anti-pattern: do NOT hand-approve "
            "`approve-protocol` by editing run state directly to bypass the gate — "
            "fix `_protocol.md`'s `counter-position` field and re-run approve. "
            "`--reject` remains the explicit escape hatch to abandon/redo the protocol. "
            "After `coverage-gate` approval: run `rv review <project> expand <scope>` "
            "to emit the Phase-2 fan-out (one `relate-<key>` node per [NEW] citekey → "
            "`review-synthesize` → `review-coverage-critic` → `[HG:approve-review]`). "
            "Use `rv review <project> list` to enumerate all reviews. "
            "Use `rv review <project> tips [--key <key>]` to inspect the review_tips seam. "
            "SR-LR-2 gap-driven pass (§5L.7): use `rv review <project> gap-scan` to "
            "detect typed research gaps (knowledge_void, contradictory, evaluation_void) "
            "from the OKF corpus. "
            "Each gap note gets a suggested_route: field (literature|experiment|triage). "
            "This is a rejects-only SCREEN — it PROPOSES gaps, never auto-fires a review. "
            "SR-GAP-ROUTE (§5L.14–5L.16): use `rv review <project> gap-scope <gap-id> <scope>` "
            "(or the alias `gap-route`) to auto-author the remedy scope by error-asymmetry. "
            "--target literature (default): auto-authors a Part-1 review scope. "
            "--target experiment: auto-authors an SR-PLAN-1 pre-registration plan "
            "(research question ← claim verbatim; covers: skeleton; diagnosis-table stub). "
            "Use `rv review <project> gap-list [--status proven-open]` to list gaps; "
            "--status proven-open is the run-candidate queue. "
            "Use `rv review <project> gap-close <gap-id> --status proven-open` to stamp "
            "a proven-open gap (targeted pass saturated without closing → run-candidate). "
            "SR-GAP-CLOSE (§5L.19–5L.24): use `rv review <project> gap-close <gap-id> "
            "--by <note-ref> --status <status>` to record the bidirectional provenance "
            "edge — --by is REQUIRED for closed-supported/closed-filled (charter §2: a "
            "closed gap with no closer is un-auditable); --by is REJECTED for proven-open. "
            "--by writes both: closed_by: in the gap FM + closes: in the closing note FM. "
            "Use `rv review <project> gap-promote <gap-id> --to <ref>` to promote a "
            "proven-open gap to 'promoted' status (human-only, never auto). "
            "Use `rv review <project> gap-list --status promoted` / `--status reopened` "
            "for the new lifecycle statuses. "
            "Anti-pattern: do NOT gap-close a closed-* gap without --by — a closer-less "
            "closure is un-auditable and breaks the provenance chain. "
            "Anti-pattern: do NOT hand-write a contribution claim from a proven-open gap — "
            "run gap-promote so the claim round-trips the support-matcher. "
            "Anti-pattern: do NOT hand-collect papers without `rv review new` — the "
            "hand-run path has no `_protocol.md` freeze, no saturation curve, and no "
            "rejects-only coverage critic (the coverage gate cannot fire without the "
            "artifacts `rv review new` scaffolds). "
            "Anti-pattern: do NOT auto-fire a gap-driven pass — gap-scan is a rejects-only "
            "screen; the human authorizes each targeted pass via gap-scope (no auto-fire). "
            "Anti-pattern: do NOT hand-decide read-vs-run and hand-spin a lit pass or plan "
            "— run `rv review <project> gap-route <gap-id> <scope>`; it routes by "
            "error-asymmetry (Chalmers & Glasziou avoidable-waste) and auto-authors the scope. "
            "Anti-pattern: do NOT call `rv research` stdout and scrape it for saturation "
            "counts — import `_load_corpus_index` and `_corpus_annotation` from "
            "`research_vault.research` directly (the corpus-helper import rule, §5L.11)."
        ),
        "sr": "SR-LR-1, SR-LR-2, SR-GAP-ROUTE, SR-GAP-CLOSE",
    },
}


# ---------------------------------------------------------------------------
# Help renderer configuration (group-at-render; do NOT reorder _VERB_REGISTRY)
# ---------------------------------------------------------------------------

# Phase grouping for `rv help` display — render-time only.
# Collision-safe: groups reference verb names; do not alter the registry order.
_HELP_PHASE_MAP: list[tuple[str, list[str]]] = [
    ("Setup",        ["init", "update", "onboard", "check", "bootstrap", "project", "wt", "git-discipline", "git-health"]),
    ("Lit-review",   ["research", "cite", "review"]),
    ("Experiment",   ["experiment", "dag", "result", "plan", "wandb", "compute", "doctor"]),
    ("Gap loop",     ["__gap_loop__"]),  # review gap-* subcommands; see _GAP_LOOP_SUBCMDS
    ("Infra/git",    ["lint", "mdstore", "wait-for", "plugins", "approval", "observability"]),
    ("Coordination", ["status", "control", "task", "note", "devlog", "role", "build-agents"]),
]

# Review subcommands split for display purposes only.
_REVIEW_MAIN_SUBCMDS: list[str] = ["new", "expand", "list", "tips"]
_GAP_LOOP_SUBCMDS: list[str] = [
    "gap-scan", "gap-route", "gap-close", "gap-list", "gap-promote",
]


def _first_sentence(text: str) -> str:
    """Return the first sentence of text.

    A sentence ends at '.', '!', or '?' only when followed by whitespace or
    end-of-string — not inside file paths (.md), section refs (§5K.7), or
    parenthetical abbreviations.
    """
    import re as _re
    text = text.strip()
    m = _re.search(r"[.!?](?:\s|$)", text)
    if m:
        return text[: m.start() + 1]
    return text


def _verb_subcommands(verb_name: str, registry: dict) -> list[str]:
    """Return subcommand names for a verb by loading its parser.

    Returns an empty list if the verb has no subcommands or its parser fails to load.
    Uses argparse._SubParsersAction to discover choices — no ad-hoc parsing.
    """
    try:
        build_p, _ = _load_verb(verb_name, registry)
        if build_p is None:
            return []
        dummy = argparse.ArgumentParser()
        dummy_sub = dummy.add_subparsers()
        vp = build_p(dummy_sub)
        for action in vp._actions:
            if isinstance(action, argparse._SubParsersAction):
                return list(action.choices.keys())
    except Exception:
        pass
    return []


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

def _extract_config_arg(argv: list[str]) -> str | None:
    """Pre-scan argv for --config PATH before the full argparse parse.

    Needed because _load_instance_verbs() calls load_config() before main() can
    parse the full argument list. We scan early so we can inject the value into
    the environment before any load_config() call fires.

    Handles both ``--config PATH`` (space-separated) and ``--config=PATH`` forms.
    Returns None if --config is absent from argv.
    """
    for i, arg in enumerate(argv):
        if arg == "--config" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--config="):
            return arg[len("--config="):]
    return None


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


def _check_example_snippets(registry: dict | None = None) -> list[str]:
    """Parse-verify every ``Use `rv <verb> ...``` snippet in when_to_use strings.

    Only checks snippets that contain ``<placeholder>`` patterns — these signal a real
    usage example a hub would copy/paste. Bare navigation references without angle-bracket
    arguments (e.g. ``rv project list``) are skipped as intentional shorthand.

    Returns a list of violation strings. Empty = all clear.

    This implements charter §1 for examples: a ``Use `rv ...``` snippet is a specific
    — it must be invokable against the real parser, not just syntactically present.
    """
    import re
    import shlex as _shlex

    from_registry = registry if registry is not None else _VERB_REGISTRY

    # Verbs whose when_to_use is managed by a concurrent branch — skip to avoid collisions.
    _SKIP_VERBS: frozenset[str] = frozenset({"compute", "doctor", "plugins"})

    # Match capital-U "Use `rv <verb> ...`" patterns (the documented example convention).
    snippet_re = re.compile(r"Use `(rv [^`]+)`")
    placeholder_re = re.compile(r"<[^>]+>")

    def _normalize(snippet: str) -> str:
        """Replace placeholders with dummy values; remove optional-bracket groups."""
        # Remove [optional --flag <val>] groups first
        snippet = re.sub(r"\[[^\]]+\]", "", snippet)
        # Replace <placeholder> with a dummy value
        snippet = re.sub(r"<[^>]+>", "dummy_val", snippet)
        # Replace quoted ellipsis '...' (shown in --thesis '...' style)
        snippet = re.sub(r"'\.\.\.'?", "dummy_val", snippet)
        return snippet.strip()

    violations = []
    for verb, entry in from_registry.items():
        if verb in _SKIP_VERBS:
            continue
        text = entry.get("when_to_use", "")
        for m in snippet_re.finditer(text):
            raw = m.group(1)  # e.g. "rv note <project> new <type> --title <title>"
            # Only check snippets with <placeholder> patterns (real usage examples).
            if not placeholder_re.search(raw):
                continue

            parts = raw.split(None, 2)  # ["rv", "<verb>", "<rest>"]
            if len(parts) < 2 or parts[0] != "rv":
                continue
            snippet_verb = parts[1]
            snippet_rest = parts[2] if len(parts) > 2 else ""

            # Look up the verb's parser from _VERB_REGISTRY (always portable; not from_registry).
            entry_ref = _VERB_REGISTRY.get(snippet_verb)
            if entry_ref is None or not entry_ref.get("module"):
                continue  # verb unknown or unimplemented — skip

            build_p, _ = _load_verb(snippet_verb)
            if build_p is None:
                continue

            try:
                dummy_ap = argparse.ArgumentParser()
                dummy_sub = dummy_ap.add_subparsers()
                vp = build_p(dummy_sub)
            except Exception:
                continue

            normalized = _normalize(snippet_rest)
            try:
                rest_args = _shlex.split(normalized) if normalized else []
            except ValueError:
                continue  # shlex parse error in the snippet — flag it

            try:
                vp.parse_args(rest_args)
            except SystemExit:
                violations.append(
                    f"Snippet in {verb!r} when_to_use does not parse: "
                    f"`rv {snippet_verb} {snippet_rest}` "
                    f"(normalized: {normalized!r})"
                )

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
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help=(
            "Path to a research_vault.toml file. "
            "Overrides RESEARCH_VAULT_CONFIG env var and CWD walk-up. "
            "Errors loudly if the path does not exist."
        ),
    )
    parser.add_argument(
        "--show-instance",
        action="store_true",
        default=False,
        help=(
            "Print the resolved instance root and config file path, then exit. "
            "Useful to confirm which vault 'rv' is targeting (multi-instance guard)."
        ),
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
    raw_argv = list(argv or sys.argv[1:])

    # Pre-scan for --config PATH before _load_instance_verbs() calls load_config().
    # Precedence: --config > RESEARCH_VAULT_CONFIG > CWD walk-up.
    # We implement this by injecting --config into RESEARCH_VAULT_CONFIG (which
    # load_config() already reads first), saving and restoring any prior value.
    _early_config = _extract_config_arg(raw_argv)
    _saved_env = os.environ.get("RESEARCH_VAULT_CONFIG")
    if _early_config is not None:
        os.environ["RESEARCH_VAULT_CONFIG"] = _early_config

    try:
        # Load instance verbs from config (plugin seam: portable vs instance verbs)
        instance_verbs = _load_instance_verbs()
        parser, _, merged_registry = _build_top_parser(instance_verbs)

        # Check if the verb is registered-but-unimplemented BEFORE argparse rejects it.
        # argparse only knows implemented verbs; future-SR verbs are in _VERB_REGISTRY
        # with module=None and must be handled here for a friendly error message.
        # Strip global flags (--config PATH, --show-instance) to find the verb token.
        _stripped = list(raw_argv)
        if "--config" in _stripped:
            i = _stripped.index("--config")
            _stripped = _stripped[:i] + _stripped[i + 2:]  # remove flag + value
        _stripped = [a for a in _stripped if not a.startswith("--config=")]
        _stripped = [a for a in _stripped if a != "--show-instance"]
        if _stripped and _stripped[0] in merged_registry:
            verb = _stripped[0]
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

        # --- --show-instance global flag ---
        if getattr(args, "show_instance", False):
            try:
                cfg = load_config()
                config_src = str(cfg.config_file) if cfg.config_file else "(none — defaults)"
                print(f"instance_root: {cfg.instance_root}")
                print(f"config_file:   {config_src}")
            except Exception as e:
                print(f"rv --show-instance: config error: {e}", file=sys.stderr)
                return 1
            return 0

        if args.verb is None:
            parser.print_help()
            return 0

        # --- help verb ---
        if args.verb == "help":
            if args.check:
                docstring_violations = _check_verb_docstrings()
                snippet_violations = _check_example_snippets(merged_registry)
                all_violations = docstring_violations + snippet_violations
                if all_violations:
                    print("rv help --check: FAIL")
                    for v in all_violations:
                        print(f"  {v}")
                    return 1
                total = len(merged_registry)
                print(
                    f"rv help --check: OK — {total} verbs, "
                    "when_to_use present, all examples parse."
                )
                return 0

            # Print verb table grouped by workflow phase.
            print("Research Vault verbs:\n")
            _RULE = "─"
            _HEADER_WIDTH = 52

            # Track verbs already rendered (review appears in Lit-review AND Gap loop).
            printed: set[str] = set()

            for phase_name, phase_verbs in _HELP_PHASE_MAP:
                header = f"── {phase_name} {_RULE * max(0, _HEADER_WIDTH - len(phase_name) - 4)}"
                print(header)

                if "__gap_loop__" in phase_verbs:
                    # Gap loop: surface review gap-* subcommands explicitly.
                    print(
                        "  (rv review gap-* subcommands — detect, route, and close research gaps)"
                    )
                    for subcmd in _GAP_LOOP_SUBCMDS:
                        print(f"    rv review {subcmd}")
                    print()
                    continue

                for verb_name in phase_verbs:
                    entry = merged_registry.get(verb_name)
                    if not entry:
                        continue  # verb absent from this merged registry
                    printed.add(verb_name)

                    sr = entry.get("sr", "")
                    status = "" if entry.get("module") else f"  [{sr}]"
                    tag = " [instance]" if sr == "instance" else ""
                    first_sent = _first_sentence(entry.get("when_to_use", ""))

                    print(f"  rv {verb_name:<20} {first_sent}{status}{tag}")

                    # Subcommands: special-case review to show only main subcommands here.
                    if verb_name == "review":
                        subcmds = _REVIEW_MAIN_SUBCMDS
                    else:
                        subcmds = _verb_subcommands(verb_name, merged_registry)
                    if subcmds:
                        print(f"    {'subcommands:':<14} {' · '.join(subcmds)}")

                print()

            # Show any instance verbs not covered by the phase map.
            ungrouped = [
                v for v in merged_registry
                if v not in printed and merged_registry[v].get("sr") == "instance"
            ]
            if ungrouped:
                print(f"── Instance verbs {_RULE * max(0, _HEADER_WIDTH - 18)}")
                for verb_name in ungrouped:
                    entry = merged_registry[verb_name]
                    first_sent = _first_sentence(entry.get("when_to_use", ""))
                    print(f"  rv {verb_name:<20} {first_sent} [instance]")
                print()

            print(
                "Validation: leakage/config → rv lint · "
                "OKF links → rv mdstore check · "
                "note frontmatter → rv note <p> check"
            )
            print(
                "\nRun `rv <verb> --help` for details. "
                "`rv help --check` validates docstrings and example snippets."
            )
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
    finally:
        # Restore RESEARCH_VAULT_CONFIG to its pre-call value so that re-entrant
        # main() calls (e.g. rv init's post-init CC build) don't leak the injected
        # --config path forward into subsequent invocations.
        if _saved_env is None:
            os.environ.pop("RESEARCH_VAULT_CONFIG", None)
        else:
            os.environ["RESEARCH_VAULT_CONFIG"] = _saved_env


if __name__ == "__main__":
    sys.exit(main())
