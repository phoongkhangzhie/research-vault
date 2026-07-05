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
          DOC["doctrine/ (portable docs + the FULL named crew<br/>Alfred/Wren/Mason/Argus/Iris/Ada — the headline)"]
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
| 1 — research assistant | research, cite, note, mdstore, task, devlog, lint, wait-for, dag + the loops (experiment, review, plan, wandb, compute, doctor) + the doctrine | YES |
| 2 — file coordination | status, role, build-agents, control, crew/role system, control-file bus, notify | YES |
| 3 — advanced (opt-in) | github CI fetch (`adapters/github_ci.py`, MERGED), remote ComputeBackend / SLURM (MERGED); vcs multi-identity PR/merge | partial |

## OKF typed notes — 8 types (note.OKF_TYPES, the SSOT)
`note.OKF_TYPES` (`note.py`) is the frozen SSOT: **8 types** — `literature`, `concepts`, `methods`,
`experiments`, `findings`, `mocs`, `datasets` (SR-8), `gaps` (SR-LR-2). Notes are **pointers, not
embeds** (a datasets note *points to* its artifact, never contains it).
**Scoping** is governed by `note.OKF_SHARED_TYPES = {"datasets"}` (`note.py`) — `datasets/` is the sole
**shared** cross-project root (lives in `cfg.datasets_root`); **all other 7 types are project-scoped**
(`cfg.project_notes_dir/<type>`). This split is imported, never duplicated (consumers: `wait_for` note-resolver,
`dag/verbs` scope-check).

## The loops layer — the generative research OS (merged on top of core)
Two subpackages, each a **DAG-driven loop** composed on the SR-3 walker/store + `spec:`/`reads:` grounding
manifest with **zero new DAG mechanism** (the standing constraint). Each carries a **config seam** (Ada-authored
prompt defaults + adopter override).

| Subpackage | Verb | What it does | Config seam |
|---|---|---|---|
| `review/` | `rv review new/expand/list/gap-scan/gap-scope/gap-close` | Pre-registered, **saturation-gated lit-review DAG**: Phase-1 (review-scope → `[HG:approve-protocol]` → review-search → review-snowball → `[HG:coverage-gate]`) with `_protocol.md` freeze (non-empty `counter-position` = L-2 anti-fishing gate) + internal saturation loop (forward cited-by + backward refs); **two-phase fan-out** via `rv review expand` after the coverage human-go. **SR-LR-2 gap-driven pass**: `gap_scan.py` detects three typed gaps (knowledge_void / contradictory / evaluation_void); `gap-scan` is a **rejects-only screen** that writes `gaps/<id>.md` (8th OKF type, first-class lifecycle); `gap-scope` auto-authors a targeted Part-1 scope (question←claim, seed_queries, snowball_seeds) | `review_tips` + `style.py` |
| `plan/` | `rv plan check/tips` | Pre-registration **freeze** (`freeze.py`) + structural **shape-lint** (`check.py`): rule (a) branch-presence, rule (b) one-component-per-ablation, **rule (c) bare-id `covers:` convention (SR-PLAN-2)** — run BEFORE `human-go-plan` | `plan_tips` + `style.py` |

**Dependency posture: Batteries-included toolkit default; the framework/CLI import path stays dep-light by
guarded imports.** SR-PKG reverses the earlier stdlib-only-core golden rule for the *research surface*:
`pip install research-vault` now installs the full portable Tier-1 stack — model SDKs (`anthropic`, `openai`,
**`litellm`** as the primary unified provider seam, `google-generativeai`, `mistralai`, `cohere`, `tiktoken`),
data (`datasets`, `pandas`, `numpy`, `pyarrow`), stats (`scipy`, `statsmodels`, `scikit-learn`), figures
(`matplotlib`, `seaborn`), eval (`inspect-ai`, `evaluate`, `sacrebleu`, `rouge-score`),
multilingual (`sentencepiece`, `sacremoses`, `langdetect`), utilities (`tenacity`, `tqdm`, `orjson`, `pydantic`,
`jinja2`, `rich`, `python-dotenv`), and integrations (`wandb`, `pyzotero`). The GPU-fragile Tier-2
stack (`torch`, `transformers`, `accelerate`, `huggingface_hub`, `fasttext`, `lm-eval`, `bert-score`) is
opt-in via `[local]`; serving sub-extras `[serve-vllm]` (docs default) / `[serve-sglang]` are available.
`asta` is a documented external prerequisite for research corpus tooling — not a pip dep (may not be on PyPI).

**The `rv` CLI + every verb runs clean with toolkit absent** — all toolkit imports are guarded (lazy, only at
call sites), so `rv help`, `rv status`, `rv note`, `rv dag` and all other verbs work with `pip install
research-vault --no-deps`. This is enforced in CI via a hermetic bare-import test. Run `rv check` to see
the tier coverage matrix; run `rv bootstrap` if Tier-1 packages are missing.

Every loop obeys leakage-by-construction (no private markers in prompts/seams/DEVLOG).

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
Status verified against merged `main` (`src/research_vault/` modules + `note.OKF_TYPES`).

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
| SR-CIF | Tier-3 CI fetch (`adapters/github_ci.py`) — reworked | MERGED |
| SR-EXP-REPRO | Experiment `repro_*` provenance schema | MERGED |
| SR-PLAN-1/2 | Plan/freeze module + pre-registration + shape-lint (rule (c) bare-id `covers:`) | MERGED |
| SR-LR-1 | Lit-review loop (`review/`) — saturation-gated, two-phase fan-out | MERGED |
| SR-LR-2 | Gap-driven pass (`review/gap_scan.py`) — three typed detectors, `gaps/` 8th OKF type | MERGED |
| corpus-dedup · SR-RESOLVE-SCOPE | Corpus dedup · project-scoped-vs-shared OKF split (`OKF_SHARED_TYPES`) | MERGED |
| SR-CONTRACT → SR-LENS-RM (#64) | project-lens scaffold, then **REVERSED**: per-project lens + `_hub.lensByRole` + per-project hat-bake removed — ONE flat vault crew, hats = `charter + role`, project context read fresh | MERGED |
| SR-CCB | Claude Code binding — `rv init` writes `CLAUDE.md` + populates `.claude/agents/` via `build-agents --target claude-code`; per-role tool grants + model aliases (PUB-CCB.2) | MERGED |
| SR-RM-FIGMS | Remove figure + manuscript loops; `figures/`, `manuscript/` OKF types, `[figures]` extra, `absent_row` gap detector removed; OKF→8; honesty-gates doctrine harvested | MERGED |
| SR-DAG-BRIEF | Deterministic dispatch brief emitter (`dag/brief.py`): `BRIEF_PREAMBLE` + `build_brief`; promotes the DAG walk to a **4-step protocol** | MERGED |
| SR-HARNESS-P2 | Experiment-loop harness sub-sequence + K-3 3rd canonical block (`harness_commit` via `rv plan freeze-harness`); re-verified at `human-go-findings` | MERGED |
| SR-XPB | Cross-project reach seam: `project_edges.py` store + `cross_project.corroborate_across_projects`; TF-IDF rank NARROWS, judge CONFIRMS, human reviews | MERGED |
| SR-APPROVE-GATE | Approval trust-boundary: `check_human_presence` (isatty + token), signed-disable (`enforce_sig` HMAC), `rv approval` verb; crew-cannot-self-approve is now MECHANICAL | MERGED |
| SR-PKG | Batteries-included toolkit: Tier-1 deps (model SDKs, data, stats, eval, multilingual, utils, integrations); `[local]` + `[serve-vllm]`/`[serve-sglang]` extras; `rv check` tier matrix; `rv bootstrap`; bare-import guard; architecture + recipe docs | MERGED |
| — next → | SR-10 (OSS docs site + README/LICENSE + public publish, human-go) | — |

## DAG walk protocol (SR-DAG-BRIEF)
The deterministic brief emitter (`dag/brief.py`) promotes the walk to a **4-step protocol** — repeat for every dispatch node:

```
1. rv dag status <run_id>           → identify the next node (PENDING; reads: paths verified)
2. rv dag brief <run_id> <node_id>  → emit the deterministic dispatch brief (BRIEF_PREAMBLE + spec + context)
3. dispatch the EMITTED brief       → send verbatim to the matching crew subagent; wait for ⟦RETURN⟧
4. rv dag complete <run_id> <node_id>  → record SUCCEEDED/FAILED; walker advances the frontier
```

`build_brief(node, node_state, cfg, run_id, project_root, manifest_project) -> str` is **pure** (no I/O beyond
path-resolution helpers). Outputs are byte-identical given the same inputs. `BRIEF_PREAMBLE` is the fixed
structural layer every dispatch carries (role framing, instance boundary, anti-fabrication, `⟦RETURN⟧` schema)
— modelled on `RETRY_DIAGNOSIS_DIRECTIVE` and unremovable. The diagnose-first block fires only on retries
(`attempts > 0`), reusing `RETRY_DIAGNOSIS_DIRECTIVE` (D-RETRY-9). Context block includes resolved absolute
`reads:` paths and `produces:` output paths — no re-transcription drift.

```mermaid
flowchart LR
    S["rv dag status"] -->|"identifies next node"| B["rv dag brief\n(BRIEF_PREAMBLE\n+ spec verbatim\n+ resolved paths)"]
    B -->|"dispatch verbatim"| A["crew subagent\n(⟦RETURN⟧)"]
    A -->|"rv dag complete"| W["walker advances\nfrontier"]
    W -->|"next node"| S
```

## Harness sub-sequence (SR-HARNESS-P2)
The experiment loop inserts a **harness sub-sequence** per main BEFORE the run fires, gated by a dedicated
human-go node (`human-go-harness-main<k>`). Per main the sequence is:

```
<id>-main<k>-harness  →  <id>-main<k>-harness-review  →  [HG:human-go-harness-main<k>]
→ <id>-main<k>-run   →  …
```

`rv plan freeze-harness <run_id> <plan-note> --scope main<k> --harness-commit <sha>` writes the harness
SHA into the `harness_commits:` frontmatter field of the plan note and adds it as the **3rd canonical block**
of the K-3 covers-hash (`plan/freeze.py:HARNESS_SENTINEL`). This block is recomputed and re-verified at
`human-go-findings`, making harness-commit drift a reportable kind (`"harness-commit drift"` vs
`"covers edit"` vs `"retries edit"`). A plan note without `harness_commits:` produces the same 2-block
hash as before SR-HARNESS-P2 (fully backward-compatible).

The `harness_commits:` field uses the flat inline-list format: `harness_commits: [main1=<sha>, main2=<sha>]`.

## Cross-project reach seam (SR-XPB)
The cross-project seam (`project_edges.py` + `cross_project.py`) adds intentional reach between peer projects
without any intra-framework disclosure boundary (everything in research-vault is public by construction).

```mermaid
flowchart LR
    HUB["hub\n(rv project relate <a> <b> --kind <why>)"]
    HUB -->|"writes"| ES["project_edges.json\n(state_dir sidecar;\nnormalised undirected pairs)"]
    ES -->|"peers_of(cfg, slug)"| COR["corroborate_across_projects\n(from_slug + against ⊆ peers)"]
    COR -->|"TF-IDF cosine rank\n(Jaccard fallback)"| RANK["ranked candidates\n(score + ranker field)"]
    RANK -->|"judge CONFIRMS\nhuman reviews"| ASS["asserted cross-project finding\n(never auto-asserted)"]
```

Key design decisions (SR-XPB D1–D5):
- **D1**: sidecar JSON at `state_dir/project_edges.json`; atomic write (tmp+replace).
- **D2**: undirected (pairs normalised to sorted order); `kind` + rationale required on declare.
- **D3**: `corroborate` requires `from_slug`; `against` ⊆ declared peers (enforced at call site, not just by convention).
- **D4**: judge-gated assert — TF-IDF rank NARROWS candidates, LLM judge CONFIRMS each, human reviews. Never auto-assert.
- **D5**: hub declares edges (`rv project relate`); crew reads via `peers_of`. Blanket-relating all projects forfeits the narrowing benefit — declare on genuine relatedness.

`rv project edges` surfaces the registry; `rv project edges --project <slug>` shows edges involving one project. `rv project relate <a> <b> --remove` prunes stale edges.

## Approval trust-boundary (SR-APPROVE-GATE)
`rv dag approve` / `rv dag reject` are gated at a single chokepoint (`cmd_approve` in `dag/verbs.py` → `check_human_presence` in `dag/approval.py`). The security property: **`security = stdin.isatty()`, full stop** — a dispatched subagent has no controlling TTY and is refused regardless of flags.

```mermaid
flowchart TD
    CA["cmd_approve\n(dag/verbs.py)"] --> CHP["check_human_presence\n(dag/approval.py)"]
    CHP -->|"stdin.isatty()"| TTY["TTY path\n(one-keystroke prompt;\n--yes skips keystroke\nwhen TTY present)"]
    CHP -->|"no TTY +\nfingerprint present"| TOK["token path\n(RV_APPROVER_TOKEN\nHMAC-verified vs\nstored fingerprint)"]
    CHP -->|"no TTY +\nno fingerprint"| FAIL["FAIL CLOSED\n(state unchanged;\nfriendly nudge printed)"]
    TTY --> OK["approve / reject\n(state written)"]
    TOK --> OK

    subgraph DISABLE["enforce=false escape hatch (Slice 3)"]
      direction LR
      DIS["rv approval disable\n(presence-checked;\nwrites enforce=false\n+ enforce_sig HMAC)"]
      DIS -. "valid sig = gate off\nbad/absent sig = gate still on\nno token = trust-me mode (warns)" .-> CHP
    end
```

Key invariants:
- `--yes` is honoured **only** when a TTY is actually present — it is ignored for non-TTY callers.
- **A signed disable does NOT remove the token requirement**: when `enforce=false` + `token_fingerprint` provisioned, `check_human_presence` still resolves `RV_APPROVER_TOKEN` to verify `enforce_sig`. If the token is absent (KeyError), the disable is treated as `enforce=True` — the gate remains up. `rv approval disable` never grants tokenless approval.
- A raw toml edit (`enforce = false`, no `enforce_sig`) is **inert** when a token is provisioned.
- `rv approval setup` provisions the token + writes the fingerprint. `rv approval enable/disable/status` manage the gate; all are presence-checked.
- Doctrine: `data/doctrine/crew-cannot-self-approve.md`.

## Crew generation & the emit path (SR-LENS-RM, #64)
**One general, VAULT-LEVEL crew — not one crew per project.** Each hat is composed **`charter + role`**
only (`_compose_hat`, `build_agents.py:67`) and built **once at `rv init`**, flat. The old per-project
CONTRACT lens, the per-project hat-bake branch, the `_hub.lensByRole` selection, and the
first-project-pick namespacing hack are **all removed** (#64). Project emphasis is **not baked** — it is
**read fresh** at work time. The 6 vault roles are `_VAULT_ROLES = DEFAULT_ROSTER + ["architect"]`
(`build_agents.py:250`).

```mermaid
flowchart TB
    subgraph DOC["doctrine/ (package data)"]
      CH["agent-charter.md<br/>(universal values)"]
      RD["roles/&lt;personal&gt;.md<br/>(mason·ada·iris·argus·wren·alfred<br/>via _ROLE_DOC map)"]
    end
    COMP["_compose_hat(role)<br/>= charter + role + read-fresh footer<br/>(NO project lens · build_agents.py:67)"]
    CH --> COMP
    RD --> COMP
    COMP --> BE{{"AgentBackend seam<br/>render(role, composed_body)"}}
    BE -->|"--target agents-dir (default)"| ADS[".agents/&lt;role&gt;.md<br/>target-neutral, harness-agnostic<br/>source-of-record — FLAT, vault-level<br/>(no per-project subdir)"]
    BE -->|"--target claude-code"| CCB[".claude/agents/&lt;role&gt;.md<br/>CC-rendered projection: YAML frontmatter<br/>(tool grant + model alias) + body verbatim"]
    ADS -. "v1.1: codex / cursor / generic render<br/>from the SAME .agents/ source" .-> FUT(["(future backends)"])

    subgraph INIT["rv init — SR-CCB binding (once, at instance setup)"]
      direction TB
      I1["writes CLAUDE.md (Alfred hub-bootstrap)"]
      I2["creates .claude/agents/ (CC session-start requirement)"]
      I3["auto-runs build-agents --target claude-code"]
    end
    I3 --> CCB

    PCTX["Project context — READ FRESH, never baked:<br/>&lt;source_dir&gt;/pointers.md · rv status --project &lt;slug&gt;<br/>· architecture.md · notes / control board"]:::fresh
    CCB -. "hat reads at work time" .-> PCTX
    ADS -. "hat reads at work time" .-> PCTX

    classDef fresh fill:#eef,stroke:#66c;
```

**Two coexisting targets, one composed source.** `.agents/<role>.md` is the neutral source-of-record;
`.claude/agents/<role>.md` is the Claude-Code-rendered *projection* (both emitted by `rv build-agents`,
selected by `--target`). The `AgentBackend` seam (`render(role, composed_body) -> [(relpath, contents)]`)
is where v1.1 `codex`/`cursor`/`generic` backends slot in — same composed body, different path/format.

**CC tool-grant policy (PUB-CCB.2 — least-privilege).** The `claude-code` projection stamps YAML
frontmatter per role: **coordinator-class** (architect) gets **no `Bash`** (structural, not
disciplinary); **doer-class** (engineer, designer) gets `Bash` + role tools; **reviewer** is read-only
(`Read, Bash, Grep, Glob` — no Write/Edit); **researcher** carries `WebSearch`/`WebFetch` for
retrieval-backed citations. Model values are **aliases only** (`sonnet`/`opus`/`haiku`) — never a
versioned ID (leakage class-6); researcher + reviewer baseline **opus**.
