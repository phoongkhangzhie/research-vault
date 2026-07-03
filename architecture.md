# Architecture — research-vault (Research Vault)

The technical map of record. Owned by the Principal Architect; kept current with the code in the same
change. **Research Vault is a STANDALONE public OSS package** — built fresh, like any project. The live
`~/vault` is **NOT a dependency, NOT refactored, NOT imported** — that boundary is a v1 acceptance check.

## The standalone boundary

```mermaid
flowchart LR
    subgraph RV["research-vault repo (standalone, public-destined)"]
      direction TB
      subgraph PKG["src/research_vault/ (the package — ships in the wheel)"]
        direction TB
        CFG["config.py<br/>(config plane SSOT —<br/>all paths + adapter selection)"]
        CLI["cli.py<br/>(fresh Python dispatcher,<br/>config-driven argparse)"]
        CLI --> CFG
        subgraph T1["tier-1 — research assistant (zero infra)"]
          V1["research · cite · note · mdstore<br/>task · devlog · lint · wait-for"]
        end
        subgraph T2["tier-2 — file-based coordination"]
          V2["role · build-agents · control<br/>crew/role system · control-file bus"]
        end
        CLI --> V1
        CLI --> V2
        subgraph ADP["adapters/ (Protocols + local-defaults)"]
          N["Notifier<br/>(default: file inbox/outbox)"]
          B["ComputeBackend<br/>(default: local subprocess)"]
          S["SecretStore<br/>(default: keyring / env)"]
        end
        V1 --> ADP
        V2 --> ADP
        subgraph DATA["data/ (SR-PKG — loaded via importlib.resources + as_file;<br/>ships in the wheel · missing file = HARD ERROR, no silent skeleton)"]
          DOC["doctrine/ (portable docs + the FULL named crew<br/>Alfred/Wren/Atlas/Mason/Argus/Iris/Ada — the headline)"]
          TPL["templates/ (OKF + CONTRACT + QUICKSTART, placeholdered)"]
          EX["examples/ (≥2 demo projects: research + lit-review)"]
        end
        CLI -. "rv init copies out via importlib.resources" .-> DATA
      end
      GATE["CI: hermetic pytest + leakage scanner (red on private marker)"]
    end

    VAULT["~/vault (the live OS)"]:::ext
    VAULT -. "NO dependency · NO import · NO edit<br/>(v1 acceptance boundary)" .-x RV

    subgraph T3["tier-3 — advanced adapters (opt-in, MERGED — SR-CIF/SR-7)"]
      VCS["github_ci adapter:<br/>CI-fetch / PR status"]
      SLURM["remote ComputeBackend: SLURM over ssh"]
    end
    ADP -. "opt-in extras, now merged" .-> T3

    classDef ext fill:#eee,stroke:#999,stroke-dasharray:5 5;
```

**Package-data layout (SR-PKG, #22 part 1 / merged #46).** `doctrine/`, `templates/`, and `examples/`
are **not** top-level repo boxes — they live under **`src/research_vault/data/`** *inside* the package,
so they ship in the wheel. `rv init` reads them via **`importlib.resources.files("research_vault") / "data"`
+ `as_file()`** (zipimport-safe: works from a regular install AND a zipped wheel). The old `__file__`-based
skeleton fallbacks are **gone** — a missing data file is a **HARD ERROR**, not a silently-degraded skeleton
(charter §2: surface, never silently drop; `init.py:17-22`).

## Tiers
| Tier | Surface | v1? |
|---|---|---|
| 1 — research assistant | research, cite, note, mdstore, task, devlog, lint, wait-for, dag + the loops (manuscript, review, plan, figure, wandb, compute, doctor) + the doctrine | YES |
| 2 — file coordination | status, role, build-agents, control, crew/role system, control-file bus, notify | YES |
| 3 — advanced (opt-in) | github CI fetch (`adapters/github_ci.py`, MERGED), remote ComputeBackend / SLURM (MERGED); vcs multi-identity PR/merge | partial |

## OKF typed notes — 9 types (note.OKF_TYPES, the SSOT)
`note.OKF_TYPES` (`note.py:26-36`) is the frozen SSOT: **9 types** — `literature`, `concepts`, `methods`,
`experiments`, `findings`, `mocs`, `datasets` (SR-8), `figures` (SR-FIG), `manuscript` (SR-MS-1a). Notes are
**pointers, not embeds** (a figures/datasets/manuscript note *points to* its artifact, never contains it).
**Scoping** is governed by `note.OKF_SHARED_TYPES = {"datasets"}` (`note.py:43`) — `datasets/` is the sole
**shared** cross-project root (lives in `cfg.datasets_root`); **all other 8 types are project-scoped**
(`cfg.project_notes_dir/<type>`). This split is imported, never duplicated (consumers: `wait_for` note-resolver,
`dag/verbs` scope-check).

## The loops & manuscript layer — the generative research OS (merged on top of core)
Four subpackages, each a **DAG-driven loop** composed on the SR-3 walker/store + `spec:`/`reads:` grounding
manifest with **zero new DAG mechanism** (the standing constraint). Each carries a **config seam** (Ada-authored
prompt defaults + adopter override) and a `style.py` `apply_style`/style-preamble seam.

| Subpackage | Verb | What it does | Config seam |
|---|---|---|---|
| `manuscript/` | `rv manuscript new/compile/check/list` | Grounded LaTeX drafting: `support_matcher` (`[SUPPORTS]`/`[PARTIAL]`/`[ABSENT]`/`[CONTRADICTS]` verdict per `\cite`→source, verbatim-span-or-BLOCK) · `naked_cite` (uncited-claim scan) · `check_gates` · `bib` (closed `.bib` from `literature/` notes) · `results_inject` (machine-injected `\result*` macros, hash-verified `experiments/` reads) · `appendix` · guarded `compile` | `per_section_tips` + `style.py` |
| `review/` | `rv review new/expand/list` | Pre-registered, **saturation-gated lit-review DAG**: Phase-1 (review-scope → `[HG:approve-protocol]` → review-search → review-snowball → `[HG:coverage-gate]`) with `_protocol.md` freeze (non-empty `counter-position` = L-2 anti-fishing gate) + internal saturation loop (forward cited-by + backward refs); **two-phase fan-out** via `rv review expand` after the coverage human-go | `review_tips` + `style.py` |
| `plan/` | `rv plan check/tips` | Pre-registration **freeze** (`freeze.py`) + structural **shape-lint** (`check.py`): rule (a) branch-presence, rule (b) one-component-per-ablation, **rule (c) bare-id `covers:` convention (SR-PLAN-2)** — run BEFORE `human-go-plan` | `plan_tips` + `style.py` |
| `figures/` | `rv figure new/preview/render/recommend/list` | scores/datasets → publication-quality figures over pandas; `recommend` ranks plot types on the Cleveland–McGill perceptual ladder + Mackinlay expressiveness (SR-FIG-REC); provenance = `figures/` note (experiment-results-hash + filter recipe + style preset) | `apply_style(preset, skin)` seam (Iris style module + BeautifulFigures [MIT, attributed]) |

**Dependency posture:** `figures/` bears RV's first optional extra — `[figures] = matplotlib/seaborn/pandas`
(import-guarded); manuscript `compile` uses a documented **texlive prerequisite**, not a core dep. **Core stays
stdlib-only.** Every loop above obeys leakage-by-construction (no private markers in prompts/seams/DEVLOG).

## Adapter Protocols (adapters/base.py)
| Adapter | Interface | Local-default (zero infra) | Advanced adapter |
|---|---|---|---|
| Notifier | `notify(msg, severity)` (+ optional `push_brief`) | file inbox/outbox (`state/inbox.jsonl` + `desk.md`) — **the ONLY impl; NO telegram/bridge anywhere** (rescope #4) | — |
| ComputeBackend | `submit(job)->handle` · `status(handle)` | local subprocess; artifact-verify = file check | remote SLURM over ssh (`adapters/remote.py`, SR-7 — **MERGED**) |
| SecretStore | `get(name)` · `set(name)` | `keyring` lib OR `$ENV` + gitignored dotfile (cross-platform) | macOS Keychain (later) |

The wait between submit and in-session verify is a backgrounded **`wait-for <condition>`** (§R) — one
main session + background shells, no daemon/poller/registry. Subagents submit-and-return; they never
block on an external job.

## Leakage-by-construction (the public-repo guarantee)
No dependency-direction tooth (there is no instance↔framework dependency). The guarantee is: the repo is
built fresh and contains no private data, enforced by (1) config-points-outward / zero hardcoded paths +
codenames; (2) a CI leakage scanner — private markers / secrets / non-template agent-memory → RED build;
(3) placeholdered + linted templates. Acceptance: `rv init` → a valid stranger-runnable instance.

## SR sequence (build plan)
Status verified against merged `main` (`src/research_vault/` modules + `note.OKF_TYPES`). **15 SRs merged.**

| SR | What | Status |
|---|---|---|
| SR-1 | Package scaffold + config plane + dispatcher + `task`/`note`/`control`/`devlog` | MERGED |
| SR-2 | Remaining verbs + `wait-for` + adapter Protocols + local-defaults + plugin seam | MERGED |
| SR-3 | DAG core (walker/store/schema/reads) + OKF typed-artifact coupling | MERGED |
| SR-4 | Leakage gate teeth + portable doctrine + FULL named crew (the SPINE) | MERGED (human-go) |
| SR-5 | Both example loops + multi-project structure + `rv init` + preflight | MERGED |
| SR-NEW | `rv project new` capstone — stands up ONE new project as its own git repo (register-first + rollback) | MERGED |
| SR-6 | `rv compute` / `rv doctor` — compute-discovery manifest + env probing/caching | MERGED |
| SR-7 | Remote `ComputeBackend` (`adapters/remote.py`) + cleanup + `native_env` | MERGED |
| SR-8 | DATASETS as a typed OKF artifact (shared root) + data-processing seams | MERGED |
| SR-WB | `rv wandb pull` — W&B results core (server holds data, vault holds index, pull by id) | MERGED |
| SR-FIG | FIGURES — data→figure DAG capability; `[figures]` extra; `figures/` OKF type (+ SR-FIG-REC recommender + fix) | MERGED |
| SR-CIF | Tier-3 CI fetch (`adapters/github_ci.py`) — reworked | MERGED |
| SR-EXP-REPRO | Experiment `repro_*` provenance schema | MERGED |
| SR-PLAN-1/2 | Plan/freeze module + pre-registration + shape-lint (rule (c) bare-id `covers:`) | MERGED |
| SR-MS-1a/1b/2 | Manuscript layer: structure · `.bib`+results-inject+guarded compile · support-matcher + critic + hash-drift gate | MERGED |
| SR-LR-1 | Lit-review loop (`review/`) — saturation-gated, two-phase fan-out | MERGED |
| corpus-dedup · SR-RESOLVE-SCOPE · SR-CONTRACT | Corpus dedup · project-scoped-vs-shared OKF split (`OKF_SHARED_TYPES`) · CONTRACT/project-lens scaffold (`build_agents`, `_hub.lensByRole`) | MERGED |
| — next → | SR-FIG-REC polish · SR-LR-2 · SR-PLAN-2 follow-ons · SR-10 (OSS docs site + README/LICENSE + public publish, human-go) | — |

## The CONTRACT / project-lens scaffold (SR-CONTRACT)
The crew-composition layer: `build_agents.py` composes each agent hat as `charter + role + project-lens`; the
per-role lens is selected via `_hub.lensByRole` in the project registry (`projects.json`). This is what
makes one shared crew serve multiple projects with the right emphasis per hat.
