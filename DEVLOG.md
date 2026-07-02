## 2026-07-02 (SR-7 cleanup — post-review non-blocking findings)

### Done
- **Finding #1 (import guard):** `adapters/__init__.py` replaced eager `from .remote import RemoteBackend` with PEP 562 module-level `__getattr__`. `backend=local` no longer pulls in ssh/remote.py at import time.
- **Finding #2 (env/cwd wired):** `RemoteBackend.submit()` now applies `env=` and `cwd=` to the remote invocation. Standard archetypes (slurm/pbs/generic): cmd wrapped in `/bin/sh -c 'cd <cwd> && KEY=val <cmd>'`. ssh archetype: `sh -c '...'` wrapper inserted into the shell template string. No-op when both are None.
- **Finding #3 (type hint):** `_BACKEND_REGISTRY` annotation corrected to `dict[str, type | None]`.
- **Finding #4 (SSOT):** Extracted `_parse_sacct_state(stdout, job_id)` helper in `wait_for.py` plus module-level `_SLURM_TERMINAL` frozenset. Both the `sacct:` resolver and `_resolve_sched` degrade fallback now call the shared helper — no more duplicate line-parse loops.
- **Finding #5 (null status_cmd):** `cmd_show()` guards `status_cmd: null` profiles; renders as `status_cmd=null` instead of crashing with `TypeError`.
- **Finding #6 (dead assignment):** Removed `merged = defaults` dead line in `_merge_profile_defaults`.
- **11 new tests** in `test_sr7.py` (import guard, env/cwd × 4, parse helper × 4, null guard × 2). 982 + 11 = 993 pass; 18 pre-existing failures (`python` binary absent) unchanged.
- `rv lint`: PASS. `rv help --check`: 26 verbs OK. Leakage scan: clean.

### Decisions
- env/cwd wired via `sh -c` wrap (not scheduler-specific flags like `--chdir`) — cross-archetype and correct for both slurm and generic; no new archetype surface.

---

## 2026-07-02 (SR-LR-1 L-1 fix — rv research references backward snowball)

### Done
- **`cmd_references`** added to `src/research_vault/research.py`: backward snowball via `asta papers get --fields references.*`, extracting `raw["references"]`. Shared `_print_candidates` helper — no formatting fork.
- **`build_parser()`** updated: `references` subcommand registered with cross-reference to `cited-by` in both directions (forward ↔ backward snowball).
- **`run()`** dispatch updated: `research_cmd == "references"` routes to `cmd_references`.
- **`_VERB_REGISTRY["research"]`** in `cli.py` updated: `when_to_use` now includes backward-snowball intent phrases, anti-pattern (do NOT hand-copy a bibliography), cross-ref to `references`; `sr` updated to `"SR-2, SR-LR-1"`.
- **11 TDD tests** in `tests/test_research_references.py`: all green. 952 total passing (941 baseline + 11 new).
- `rv lint`: PASS. `rv help --check`: 26 verbs OK. Leakage scan: clean.

### Decisions
- `asta papers get --fields references.*` is the confirmed backward call (not `asta papers references` which does not exist). Returns `raw["references"]` directly.
- No `--limit` parameter on `references`: `asta papers get` returns the full reference list; limit is not accepted by that subcommand.
- `_print_candidates` is reused as-is — consistent formatting between `cited-by` and `references` by design.

### Open / next
- SR-LR-1 full lit-review loop: needs both directions wired. This PR covers backward (L-1 fix); forward (`cited-by`) was already present.
## 2026-07-02 (SR-FIG-REC — plot-type recommender, expressiveness→effectiveness)

### Done
- **`figures/recommend.py`**: static rule table (task × descriptor-shape → ranked encodings + principle strings), grounded in Cleveland–McGill (1984) accuracy ladder + Mackinlay (1986) expressiveness→effectiveness ordering.
  - `infer_view(df)` — pandas-backed descriptor inference (role/dtype/cardinality per column); role heuristic uses `card > 10 OR card/nrows > 0.5` (fraction-based to handle small-frame measures like confusion-matrix count cols)
  - `infer_task(cols)` — heuristic task inference from descriptor shape; returns (primary, alternates)
  - `detect_confusion_matrix_shape(cols, df)` — same-label-set on both axes detection
  - `recommend(cols, task=None, ...)` — ranked Suggestion list; prints "task inferred: ..." when task omitted
  - `integrity_warns(...)` — 6 WARN checks (truncated baseline, stacked segments, pie>3, rainbow colormap, diverging-on-sequential, bar-of-means); never blocks, exit 0 always
  - `colormap_class` seam: emit class (sequential/diverging/qualitative) only — no palette (Iris's job)
- **`figure.py` updated** (SR-FIG-REC integration):
  - `cmd_new` now accepts `plot_type=None` (the new default); when omitted, calls `_auto_recommend_plot_type()` which loads the results frame, infers descriptors, calls `recommend()`, prints rationale
  - `--type` supplied → honored silently (recommend-not-mandate)
  - Integrity WARNs fire via `_fire_integrity_warns()` regardless of who chose the type
  - `cmd_recommend()` — the `rv figure recommend <view-csv>` verb
  - Parser: `recommend` sub-verb + `--task` + `--why` on both `new` and `recommend`
- **`cli.py`**: `figure` verb `when_to_use` updated to include `recommend` sub-verb + encoding anti-pattern; sr: SR-FIG, SR-FIG-REC
- **Bug fix**: `infer_view` card > 10 threshold misclassified small-frame numeric columns (e.g. confusion-matrix `count` col with 7 unique values) as `role=dimension`. Fixed with fraction-based clause.

### Decisions
- `plot_type=None` is the new default in `cmd_new` (was `"line"`); the CLI `--type` default is also `None` (was `"line"`). When the [figures] extra is absent, falls back to `"line"` with a stderr message.
- Role heuristic: `card > 10 OR card/nrows > 0.5` — the fraction guard handles small frames where absolute cardinality is low but the column is clearly a continuous measure (not a group-ID code).
- D-FIGREC-1 (infer-and-surface): implemented as specified — inferred task announced, not buried.
- D-FIGREC-2 (WARN-only integrity): WARNs are advisory; no integrity check blocks.

### Open / next
- PR #25 → reviewer + Architect fit → operator merges (human-go class). NO self-merge.

## 2026-07-02 (SR-MS-1a complete — manuscript structure half)

### Done
- **Rescued and rebased prior-engineer WIP** (interrupted session): WIP-committed note.py + manuscript/ + tests, rebased onto origin/main (additive conflict: experiments repro branch + manuscript branch in note.py — resolved keeping BOTH).
- **`manuscript` as 9th OKF type** (`note.OKF_TYPES` 8→9): frontmatter template in `cmd_new`, body in `cmd_new`, `cmd_check` type-dir contract, `_check_manuscript_pdf_hash` PDF-hash provenance check.
- **`manuscript/__init__.py`**: `cmd_new` (scaffolds OKF note + `manuscripts/<id>/` tree + 16-node drafting-DAG manifest JSON) + `cmd_list`.
- **`manuscript/style.py`** (complete style seam): config-driven `SECTION_KEYS`+`SECTION_STATUS` with optional/venue-optional markers; `get_active_sections()`; 4 new Ada-authored `per_section_tips` entries (background, ethics-impacts, augmented conclusion, data-code-availability per §5J fold-in B); `manuscript_style_preamble` (7 voice/stance rules, §5J fold-in C) + `get_style_preamble(override=None)` accessor.
- **`manuscript/verbs.py`**: `build_parser` + `run` for `rv manuscript new/list`.
- **`templates/manuscript.tex`**: neutral article-class LaTeX template (§5J.8 v1 cut, one template).
- **`cli.py`**: `"manuscript"` added to `_VERB_REGISTRY` with when_to_use + anti-pattern; `"note"` when_to_use updated.
- **Tests updated**: `test_sr8.py` + `test_sr_fig.py` OKF_TYPES count 8→9.
- **36 SR-MS-1a tests**: all green. 923 total passing. Pre-existing failures (git_discipline + wt_project) unchanged.
- `rv lint`: PASS. `rv help --check`: 26 verbs OK. Leakage scan: clean.

### Decisions
- `manuscript.cmd_new` calls `scaffold_okf_dirs(project_notes_dir)` before emitting the manifest, ensuring all OKF type-dirs exist so reads: pointers resolve at run-time (§5J.2 gotcha ruling).
- `SECTION_KEYS` now lists all 16 possible section keys (13 required + 3 optional/venue-optional). The scaffolder uses `get_active_sections()` to select a subset. Default 16-node manifest = 13 required agent sections + 3 human-go gates.
- Optional sections (background, ethics-impacts, data-code-availability) are OFF by default; enabled via `--optional`/`--venue-optional` flags.
- DAG manifest reads: pointers use absolute paths (resolved at scaffold time, not run-time) to avoid project_root ambiguity.
- `data-code-availability` (VENUE-OPTIONAL) branches off `approve-thesis` in parallel to `appendix-repro`, joins at `assemble`.

### Open / next
- SR-MS-1b: .bib exporter + machine-injected results macros + exec-guarded compile + `rv manuscript compile/check` (the grounding-builders half).

## 2026-07-02 (SR-PLAN-1 complete — plan/freeze module, K-2 fix, K-3, demo upgrade)

### Done
- Rescued uncommitted plan/ module from prior engineer session-limit cutoff; WIP-committed immediately.
- **K-2 shape-lint fix (check.py):** pre-strip-before-filter was silently dropping genuinely empty cells. Fixed: only strip leading/trailing split artefacts; report truly empty cells as violations.
- **K-3 covers:-freeze-set hash (§5K.5.1):**
  - `plan/freeze.py`: `compute_covers_hash` / `store_freeze_hash` / `verify_freeze_hash` — SHA-256 of sorted (child_id, stance, plan_role) tuples.
  - `dag/store.py`: `RunState.meta: dict` generic run-state metadata; round-trips through to_dict/from_dict; backward-compatible (old state files without `meta` deserialize to `{}`).
  - `dag/verbs.py`: K-3 hook in `cmd_approve` — BLOCK on covers:-hash mismatch for any human-go node other than `human-go-plan` (the freeze gate itself).
  - `plan/verbs.py`: `rv plan freeze` + `rv plan verify-freeze` subcommands.
- **plan-critic spec** (`doctrine/plan-critic-spec.md`): exact dual of the 5K.4 plan_tips keys; structured verdict (BLOCK/WARN/PASS) with 10 sections; referenced by the plan-critic DAG node.
- **Demo manifest upgraded** (`examples/demo-research/research-loop.json`): multi-main (2 mains), each with 1 supporting ablation + 1 conditional, per-main `human-go-conditionals-mainK` gates, final `human-go-findings` afterok every per-main gate. No new DAG mechanism.
- **Demo plan notes**: plan master (`q1-plan.md`, plan_kind: preregistration, covers: 6 children) + 6 child stubs (q1-main1, q1-main1-abl-A, q1-main1-cabl-Y, q1-main2, q1-main2-abl-B, q1-main2-cabl-Z) with correct stance/plan_role/preregistration/supports_main fields.
- **test_sr5.py updated**: `_make_research_states` and tests 5-6 updated for new multi-main node names.
- **Tests**: 856 passed, 5 skipped. `rv lint`: PASS. `rv help --check`: 25 verbs OK.

### Decisions
- K-3 BLOCK in cmd_approve uses node_id convention (`!= "human-go-plan"`) to skip verification at the freeze gate itself; all other human-go nodes trigger verification if freeze is stored. Convention is documented in plan/freeze.py and verbs.py.
- K-3 import failure degrades to WARN (surface but do not deadlock a run when the plan/ module is unavailable in unusual installs).

### Open / next
- PR → reviewer + Architect fit → operator merges (human-go class). NO self-merge.

## 2026-07-02 (SR-CIF REWORK — BLOCK-1 + BLOCK-2 fixed)

### Done
- BLOCK-1 fixed: GitHubActionsSource now emits `sr-*` ids from `headRefName` (via shared `_ID_TOKEN_RE`, controllib.py:123) instead of inert `pr-<N>` tokens. `pr-<N>` never matched `_ID_TOKEN_RE` in `_check_r4`/`extract_id_tokens`, so the source's contribution was a silent no-op. Now mirrors LocalGitSource (status.py:106-108) and speaks the same join vocabulary.
- BLOCK-2 fixed: `_fetch_checks()` now calls `gh pr checks --json name,state,bucket` (real gh 2.9x JSON schema). Dropped tab-parsing (`parts[2]` was elapsed time, not required). Dropped required/optional distinction (unobtainable). Green = every non-skipping check has `bucket=="pass"`; any fail/pending/cancel → withheld. `skipping` is non-blocking.
- `_fetch_pr_info()` new helper: fetches `state,headRefName` in one `gh pr view` call; returns `(state, frozenset[sr-* ids])`. `_fetch_pr_state()` removed.
- Tests: 14 hermetic tests (was 13). All mocks use real `--json` schema. New test 10 (functional proof): green vs red PR differ in reconcile output — R4 fires for green (`sr-7` reaches `_check_r4`), not for red. Regression guard for the inert-source gap.
- Full suite: 763 passed, zero regressions. `rv lint`: PASS. `rv help --check`: OK (23 verbs). Leakage clean.
- Rebased onto origin/main (SR-FIG #21 merged).

### Decisions
- D-CIF-4 REVISED (operator-confirmed): green = all non-skipping checks `bucket=="pass"`. Required/optional distinction dropped (not exposed by `gh pr checks`).
- Follow-up filed (not built): CLI activation path — nothing in shipped CLI constructs `GitHubActionsSource` automatically. Activation is manual (`extra_sources=[...]`). A `rv reconcile --gh-pr N` flag is a separate SR.

### Open / next
- PR #20 rework awaits reviewer + Architect re-check → human-go (operator merges). NO self-merge.

## 2026-07-01 (SR-FIG plumbing build — REWORK: experiment-results primary source)

### Done
- Reworked PR #18 source swap: figures now source from `experiments/<id>` results (`results_location`/`results_hash` from SR-WB) as the PRIMARY frame. `--dataset` removed as primary; `--benchmark datasets/<id>` is the optional comparison overlay.
- Rebased `feat/sr-fig` onto `origin/main` (post SR-WB): note.py resolved additively — `experiments` results fields (SR-WB) + `figures` OKF type (SR-FIG) both present; `check.py` gains both `wandb` and `figures` optional checks.
- `figure.py`: `cmd_new` signature changed to `--experiment experiments/<id>` (required) + `--benchmark datasets/<id>` (optional). Reads experiment note's `results_location` as frame source. Provenance fields: `source_experiment`, `experiment_results_hash`, `benchmark_dataset` (optional).
- `note.py`: `_FIGURES_REQUIRED_FIELDS` updated to `{source_experiment, experiment_results_hash}`. `cmd_check` validates these fields for figures notes.
- `demo-figures.json` updated: `extract` node reads `experiments/<proj>/run-007.md` results, not a shared dataset note.
- Tests reworked test-first: `_write_experiment_note_and_results` helper; all `TestFigureNew` and `TestFigurePreview` tests now use experiment results as source; datasets-as-primary path removed.

### Decisions
- Source swap confirmed (§5E.10 item 3): experiment results are primary. `datasets/` is comparison-only.

### Open / next
- PR #18 rework needs reviewer-gate + Architect fit-check before operator merges (human-go class).

## 2026-07-01 (SR-WB build)

### Done
- Worktree: feat/sr-wb off origin/main. Crew identity set.
- Decision reversal mid-build (relayed by coordinator): switched Piece A from stdlib GraphQL/urllib to `wandb.Api()` import-guarded. The `wandb:` predicate in `wait_for.py` also updated to SDK path. Both changes confirmed in implementation before committing.
- Piece A(i) — `rv wandb pull`: new `wandb_pull.py`. `_import_wandb()` guard: if SDK absent, prints friendly prereq message, exits 1 (never raw ImportError). Auth via `EnvSecretStore.get("wandb-api-key")` → exported to env → SDK picks it up. `parse_run_id` handles 3 forms (bare-id, project/run-id, entity/project/run-id). `fetch_run` calls `wandb.Api().run(path)`, reads `.state`/`.summary`/`.commit`. `_update_frontmatter` updates flat `results_*` fields in experiment note in-place.
- Piece A(ii) — `wandb:` predicate in `wait_for.py`: new branch in `resolve_watch`. Import-guarded (SDK absent → `sdk-unavailable`, not crash). Terminal states → `ready=True`: `finished/failed/crashed/killed/preempted/preempting`; `running/pending` → `ready=False`. State string carried through so SR-RETRY can key off failure (D-WB-4). Added `wandb:` to `_KNOWN_PREFIXES` in `run()`.
- Piece B — `experiments/` results attachment: `note.py` extended. `cmd_new` for `experiments` type now includes 4 flat placeholder fields: `results_location`, `results_hash`, `results_wandb_run`, `results_commit`. `check_result_provenance()` validates: empty → OK; hash set + local file → streaming sha256 verify; URL → trust recorded hash (zero-infra). `cmd_check` extended for experiments type: calls `check_result_provenance`, propagates violations.
- `cli.py`: `wandb` verb added to `_VERB_REGISTRY` with `when_to_use`, anti-patterns, `sr: "SR-WB"`. `rv help --check` passes: 23 verbs, all with `when_to_use`.
- `check.py`: `_check_wandb()` added — probes SDK import + `WANDB_API_KEY`. Listed in Optional section (W&B features unavailable without it; does not block `all_required_ok` for non-W&B workflows). Reports install instructions when SDK absent.
- Tests: 52 new hermetic tests in `tests/test_sr_wb.py`. Classes: parse_run_id (3 forms), fetch_run (SDK mock), wandb_pull (writes artifact + fills note), wandb: predicate (finished/failed/crashed/killed/preempted → ready; running/pending → not ready; SDK absent → clean error), check_result_provenance (hash match/mismatch/empty/artifact-missing), cmd_check extension, manual/CSV fallback, check.py prereq, import-guard CLI (no traceback), module-level import sentinel (5 files).
- Full suite: 706 passed (654 baseline + 52 SR-WB). Zero regressions. `rv lint`: PASS. `rv help --check`: OK.
- Drift fix (post reviewer gate): corrected two stale strings referencing pre-reversal stdlib-GraphQL path: `wait_for.py` `_KNOWN_PREFIXES` comment → "wandb SDK, import-guarded"; `cli.py` `when_to_use` → describes SDK path + correct anti-pattern (do NOT hand-script `wandb.Api()`).
- Rebase onto origin/main (SR-7 merged): `resolve_watch` now contains `sched:` (SR-7) + `sacct:` (back-compat alias) + `wandb:` (SR-WB) all present; `_KNOWN_PREFIXES` lists all three.

### Decisions
- D-WB-1 = one PR (wandb_pull + results-attachment bundled): implemented.
- D-WB-2 = DEFER DAG surface: no `produces.result` or `result:` predicate — implemented.
- D-WB-3 = `experiments/<id>.results.json` (project-scoped): implemented.
- D-WB-4 = `wandb:` reports all terminal states as ready, carries state string: implemented.
- D-WB-5 = non-numeric SR-WB: implemented.
- Piece A reversal (coordinator-relayed decision): use `wandb.Api()` SDK instead of stdlib GraphQL. Import-guarded. Auth via EnvSecretStore then env export to SDK. No private `wandb_utils` wrapper.

### Open / next
- PR #19 needs maintainer merge (human-go class, gates cleared). The maintainer merges.
- Follow-up (logged, not built): (1) streaming-sha256 loop copied 3× — consolidate to one `stream_sha256` helper; (2) map wandb auth exceptions (401/403) to distinct `auth-error` state so a bad key fails fast instead of polling to timeout.

## 2026-07-01 (SR-7 build)

### Done
- Branch: feat/sr-7 off origin/main (which includes SR-6 + SR-8).
- Added `adapters/remote.py` — `RemoteBackend` implementing `ComputeBackend` Protocol exactly. One class, one code path, four archetype keys in `_BACKEND_REGISTRY` (slurm/pbs/ssh/generic). Manifest-driven: reads the SR-6 manifest via `compute._load_manifest(cfg)`, merges per-profile declared fields with built-in archetype defaults (_ARCHETYPE_DEFAULTS table). `submit()` composes `ssh <host> <submit_pattern> [container_wrap] -- <cmd>` for slurm/pbs/generic; shell-template mode for ssh (no scheduler). `status()` calls `_run_status()` — shared SSOT with the `sched:` resolver.
- Extended SR-6 manifest schema (D-SR7-2): per-profile fields `jobid_parse`, `status_cmd`, `status_parse`, `state_map` added with built-in defaults for slurm/pbs/ssh archetypes. SR-6 manifests lacking these fields remain valid (defaults applied at runtime).
- Updated `adapters/base.py`: `LocalSubprocess.__init__` now accepts and ignores `cfg=None` (D-SR7-5 factory-arg); `_BACKEND_REGISTRY` extended with slurm/pbs/ssh/generic keys (lazy-loaded via `_remote_backend_cls()`); `load_adapters` passes `cfg` to `backend_cls(cfg)` uniformly.
- Updated `wait_for.py`: fixed stale module docstring (removed false "SLURM check is stubbed" claim); added `sched:<backend>:<jobid>` resolver via `_resolve_sched()` — lazy-imports `_run_status` from `adapters.remote` (single SSOT, no duplicate parsers); `sacct:<jobid>` kept as fully functional back-compat alias; `sched:` added to `_KNOWN_PREFIXES`.
- Updated `compute.py`: module docstring updated (removed "all are declaration-only — execution is SR-7" since SR-7 is now implemented); `cmd_show` extended to render `jobid_parse`, `status_cmd`, `status_parse`, `state_map` when declared in a profile.
- Updated `adapters/__init__.py`: exports `RemoteBackend`.
- Updated `tests/test_adapters.py`: `test_load_adapters_unknown_backend_raises` updated to use genuinely unknown key "kubernetes" (slurm is now a valid key).
- Tests: 43 new hermetic tests in `tests/test_sr7.py` covering Protocol conformance, load_adapters routing, LocalSubprocess cfg=None, schema back-compat, submit argv (slurm/pbs/ssh/generic/container-wrap), status mapping (all Protocol states for slurm and pbs), ssh-absent graceful degrade, sched: resolver (slurm terminal/non-terminal, pbs terminal), sacct: back-compat, sched: in _KNOWN_PREFIXES, local unaffected, cmd_show rendering, stale docstring removal. Full suite: 679 passed; 18 pre-existing failures (test_git_discipline + test_wt_project, FileNotFoundError: 'python' binary absent — pre-dates this SR).
- `rv lint`: PASS. `rv help --check`: OK (22 verbs). No ~/vault edits. Leakage scan: PASS (no cluster names/aliases in code; all test fixtures use example-cluster/example-pbs/example-hpc).

### Decisions
- D-SR7-2 = YES: manifest schema extended with status/parse/state_map fields; SR-6 manifests still validate with defaults.
- D-SR7-3 = single `sched:<backend>:<jobid>` predicate; `sacct:` kept as live back-compat alias; container-wrap honored in submit for all archetypes.
- D-SR7-4 = DEFER: array jobs and native scheduler deps out of v1.
- D-SR7-5: `load_adapters` calls `backend_cls(cfg)` uniformly; `LocalSubprocess.__init__` accepts and ignores `cfg=None`.
- D-SR7-6 = four keys (slurm/pbs/ssh/generic) all bound to `RemoteBackend`. Reads naturally in config.
- No new verb added (reuse-over-create): remote backend is selected via config; submit flows through existing `ComputeBackend.submit` seam + `rv wait-for sched:`.

### Open / next
- PR awaiting human-go: reviewer-gate + Architect fit-check, then operator merges.

## 2026-07-01 (SR-8 build + amendment)

### Done
- Worktree: feat/sr-8 off origin/main. Crew identity set.
- Seam 1 — OKF type `datasets/`: added `"datasets"` to `note.OKF_TYPES` (frozenset, note.py:24). 7th canonical type. `cmd_new` for datasets notes adds `location:` and `hash:` placeholder frontmatter fields. `cmd_check` extended to verify datasets notes have non-empty `location` and `hash` fields. CLI `when_to_use` for `note` updated with datasets description and anti-pattern.
- Seam 2 — `produces: {dataset: …}`: extended schema validation (schema.py:~207) to accept `produces.dataset` as non-empty string. Extended `cmd_complete` in verbs.py to gate on the `check_dataset_provenance` function at complete-time: note exists + location non-empty + hash non-empty + (local path) sha256 matches.
- Seam 3 — Resolver `dataset:<id>`: added `dataset:` branch to `resolve_watch` (wait_for.py, mirroring `note:` pattern). URL/DOI/remote locations trust the recorded hash (zero-infra). Added `dataset:` and `note:` to `_KNOWN_PREFIXES` (wait_for.py run(), fixing pre-existing `note:` omission).
- Seam 4 (Adapter) — unchanged; `ComputeBackend.submit` present as designed.
- Amendment (operator decision 2026-07-01): (a) New config key `datasets_root` (default: notes_root/datasets, overridable). (b) datasets notes are SHARED cross-project — write/list/check/resolver all use cfg.datasets_root, not project_notes_dir. (c) _verify_local_file_hash now uses 1 MiB chunked streaming read (not full-file RAM load). (d) Rebased onto origin/main to incorporate SR-6 (cli.py and DEVLOG additive merge).
- Tests: 40 new hermetic tests in `tests/test_sr8.py`. Classes: config/datasets_root, OKF type, schema, complete-time gate, streaming hash, walker/frontier structural teeth. Full suite: 654 passed (614 SR-6 baseline + 40 SR-8); zero regressions.
- `rv lint`: PASS. `rv help --check`: OK (all verbs). No `~/vault` edits.

### Decisions
- D-SR8-1 = YES (approved 2026-07-01): `"datasets"` added as 7th canonical OKF type. Permanent widening of `note.OKF_TYPES`.
- datasets notes are SHARED (not project-scoped): operator decision resolves the check/resolver asymmetry in the resolver's direction. A dataset note filed once is visible across all projects.
- No new top-level verb introduced (reuse-over-create): SR-8 extends `rv note` and `rv dag`. Anti-pattern folded into `note` verb `when_to_use`.
- Structural teeth ride the watch/frontier path, not `produces` post-check. Tests authored accordingly per spec.
- Schema-shape validation left OPTIONAL (zero-infra, no pandas/pyarrow). Gate defaults to exists + content-hash via stdlib hashlib with streaming read.
- `note:` prefix added to `_KNOWN_PREFIXES` alongside `dataset:` — pre-existing omission corrected.

### Open / next
- PR #15 needs reviewer-gate + Architect verification before merge (crew cannot self-approve).

## 2026-07-01 (SR-6 build)

### Done
- Worktree: feat/sr-6 off origin/main.
- Added `compute.py` — compute manifest I/O (`_load_manifest`, `_save_manifest`, `_default_manifest`) + five commands: `cmd_show`, `cmd_explain`, `cmd_lesson_add`, `cmd_outcome_add`, `run`. Manifest stored at `state_dir/compute_manifest.json` (never ~/vault). Backend archetypes in manifest: local, ssh, ssh+slurm, ssh+pbs, generic, plus container as orthogonal modifier field.
- Added `doctor.py` — capability probe + DISCOVER-ONCE cache at `state_dir/doctor_cache.json`. Probes: nvidia-smi, sbatch/sinfo (SLURM detail), qsub/qstat (PBS detail), hf/uv/conda CLIs, conda env list, generic profile probe_commands. Degrades gracefully on every absent tool — no traceback, reports "not available". Second call reads cache (no re-probe). `--refresh` forces fresh probe.
- Added `plugins.py` — D-SR6-1=THIN: surfaces `_NOTIFIER_REGISTRY` / `_BACKEND_REGISTRY` / `_SECRETS_REGISTRY` static dicts + config-selected adapters. No entry-points seam (confirmed absent by grep).
- Registered three SR-6 verbs in `cli.py` `_VERB_REGISTRY`: `compute` / `doctor` / `plugins` — each with `when_to_use` folding the trial-submit anti-pattern inline + `sr: "SR-6"`.
- 38 hermetic tests in `tests/test_sr6.py` covering all seven acceptance criteria from the brief.
- Full suite: 614 passed (576 baseline + 38 new), zero regressions. `rv lint` PASS. `rv help --check` OK (22 verbs).

### Decisions
- D-SR6-1=THIN confirmed: no entry-points seam built. `rv plugins list` surfaces static registries only — honest to the merged code.
- Container as orthogonal modifier: manifest field `container` on a backend profile (not a 5th archetype row).
- JSON for manifest (not TOML): stdlib TOML is read-only (tomllib); JSON is bidirectional without extra deps.
- `rv compute explain <job>` (not `rv run --explain`) per NOTE #2: avoids collision with `rv dag run`.
- `rv doctor --refresh` forces re-probe; default reads cache — second call reads cache confirmed by test (same ts).
- Outcome capture: `rv compute outcome add --job --tier --result` appends to `run_outcomes` in manifest with ISO timestamp.

### Open / next
- PR open for reviewer-gate + Architect review, then the maintainer merges.
- SR-7 (SLURM execution) consumes this manifest's `backends.profiles[*].submit_pattern` + `gpu_tiers` + `rules`.

## 2026-07-01 (SR-NEW build)

### Done
- Worktree: feat/sr-new off origin/main (post all prior SRs merged).
- Added `scaffold_okf_dirs(base)` helper to `note.py` (SSOT for OKF types); `init.py` now calls it instead of re-listing the six types inline.
- Extended `_render_project_section` and `cmd_add` with optional `extra: dict` param for additional registry keys (refs, collection). Backward-compatible.
- Added `create_collection(name, *, key, uid) -> str` and `sync_library(coll_key, *, key, uid, refs_path) -> list` to `cite.py` (both reuse `_zotero`/`_find_collection` plumbing; sync_library is the thin mirror primitive for NEW-D2).
- Added `_PROJECT_ARCHITECTURE_TEMPLATE` (project-shaped, not instance-shaped) + `cmd_new` to `project.py` — the full 13-step register-first transactional sequence with rollback.
- `--source` made optional (default = `instance_root.parent / slug`, sibling-of-instance convention). Explicit `--source <dir>` overrides. Overwrite guard retained.
- With `--zotero`: create_collection + sync_library called (initial sync, empty for new collection, establishes mirror pattern); graceful degradation if key missing (catches SystemExit).
- Added `new` subparser to `project.build_parser` and dispatch in `run`.
- Updated `cli.py` `when_to_use` for `project` to surface `rv project new` with the anti-pattern warning.
- 44 hermetic tests in `tests/test_project_new.py`: happy path, registry, scaffold, crew, Zotero-skipped, git-discipline consent, guards (incl. default-source), rollback, discovery, CLI path (with and without --source), unit-level primitives.
- Full suite: 576 passed (532 baseline + 44 new), zero regressions. rv lint PASS. rv help --check OK (19 verbs).

### Decisions
- Compose-not-duplicate: every scaffold step calls the existing verb function (control.cmd_init, devlog.cmd_init, build_agents.cmd_build, git_discipline._install_repo) — no path re-implementation.
- Register-first: confirmed load-bearing; config cache reload mandatory after cmd_add.
- SR-STRIP confirmed landed: disclosure field gone from project.py; composed against current signature.
- NEW-D1 REVERSED (operator): --source now optional, default = instance_root.parent/slug (sibling-of-instance convention).
- NEW-D2 REFINED (operator): --zotero now triggers sync_library after collection creation (mirror pattern, yields [] for new collections — wired for future ingestion).
- Zotero step catches SystemExit (not just Exception) since _get_zotero_key() calls sys.exit on missing key.
- `--git-discipline` flag without the option prints install-offer line; with flag calls _install_repo.

### Open / next
- PR ready for Argus review + Architect fit-check (composes-not-duplicates, register-first transaction, rollback, acceptance tests all pass).
## 2026-07-01 (SR-CI build)

### Done
- Worktree: feat/sr-ci off origin/main, crew identity set.
- TOOL-D3 v1 (bare token): emitted `VERDICT: PASS` / `VERDICT: BLOCK` as the first unindented block line on `rv control return`.
- TOOL-D3 v2 (bracketed token — design change): upgraded to `VERDICT: [PASS]` / `VERDICT: [BLOCK]`. Bracket delimiter decouples the gate pattern from prose: `\[(PASS|BLOCK)\]` matches only the structured token; bare "PASS", "BLOCK", "FAIL" in narrative fields cannot false-match. `_extract_gate_verdict()` now uses `re.fullmatch(r'\[(PASS|BLOCK)\]', ...)` — rejects bare words by construction.
- 16 hermetic tests in `tests/test_sr_ci.py` (expanded from 7). Key decoupling-proof test (test 3): verdict `[PASS]` + narrative containing bare BLOCK/FAIL → header reads `VERDICT: [PASS]`; `re.findall(r'\[(PASS|BLOCK)\]', text)` returns exactly `["PASS"]`. Unit class for `_extract_gate_verdict` proves bare words return None. Full suite: 532 passed (516 baseline + 16 new), zero regressions.
- TOOL-D1 (verify-CI hard gate) explicitly NOT built — operator decision.

### Decisions
- Bracketed token `[PASS]`/`[BLOCK]` chosen over bare `PASS`/`BLOCK` after design change from coordinator: bare-word first-line approach still left narrative fields adjacent, so a fuzzy negation scan could re-trip on "BLOCK"/"FAIL" in the body. Bracketed form makes the gate pattern structurally unambiguous.
- No blank line between header and fields: `_parse_block` terminates on blank lines; blank line would orphan required fields. Bracket decoupling removes the original need for the separator.
- Non-bracket verdict values (e.g., `approve`) return None — no header, backward compat.
- Note for hub: the live operator-vault `approve.py` gate should later be updated to match `[PASS]`/`[BLOCK]` instead of fuzzy negation-scanning. Separate operator-vault change tracked by the operator.

### Open / next
- PR needs Argus review (reviewer-gate class).

## 2026-07-01 (SR-DOC build)

### Done
- Worktree: feat/sr-doc off origin/main (post-SR-SCOPE). Crew identity set.
- doctrine/standards.md: added "Test discipline (code profile)" section — four code-profile disciplines grounded in real review regressions (real merge model, non-vacuous assertions, hermetic fixture env-pinning, exit-code on run() path). Placed after harness hygiene, before enforcement.
- doctrine/review-board.md: added "Proving a check has teeth (reviewer technique)" — pre-image replay method (new test: revert + confirm fail; new scanner rule: pre-change passes planted content, new rule catches it). Added "The verdict header — gate-clean by construction" — negation-free PASS/BLOCK header schema; noted tool half is SR-CI (rv control return emits by construction).
- doctrine/coordination.md: added "Verify, don't relay" — two rules: verify CI/tool claims against the authority before recording; trace every relayed specific to source. Placed after Routing.
- Leakage scan: PASS on all three doctrine files.
- rv lint: PASS. rv help --check: OK (19 verbs).

### Decisions
- Applied DOC.2/DOC.3/DOC.4 verbatim as specified. Only authorized deviation: note in review-board verdict-header block that tool half = SR-CI.
- DEVLOG and all doctrine files de-personalized — no operator name, no private domain, no filesystem paths.

### Open / next
- PR needs Argus review. No self-merge.

## 2026-07-01 (SR-SCOPE build)

### Done
- Worktree: feat/sr-scope off origin/main, crew identity set (engineer@example.invalid pattern).
- dag/schema.py: `_validate_reads_structure` — structural teeth for `reads:` on agent nodes (pure/in-memory, ManifestError; non-empty-if-present, str or {ref,why} items). `_validate_no_reads_on_human_go` — human-go nodes must not carry reads:. Both called from `validate_manifest` inline with spec/continues checks. `manifest_warns` extended: absent `reads:` on agent node → non-fatal WARN using the SR-DISP boundary-smell idiom.
- dag/reads.py (new): `ReadsError`, `_anchor_found` (thin markdown-heading anchor search, no AST coupling), `resolve_reads_pointer` (bare file / file#anchor / control#slug / path:symbol), `resolve_reads_pointers` (manifest-level I/O resolution pass). Reuses fs-access pattern of wait_for.resolve_watch; pure validate_manifest is NOT touched.
- dag/verbs.py: `_print_frontier` extended with `reads:` suffix on DISPATCH line (`— reads: <p1>, <p2>, …`; omitted when absent). `_resolve_reads_or_warn` helper; called at `cmd_run` and `cmd_tick` after pure validate.
- cli.py: dag when_to_use extended with anti-pattern (3) — unbounded reading-scope without `reads:`.
- doctrine/coordination.md: "Bound the reading-scope" subsection — spec/reads distinction, prose inputs: ↔ machine reads: link, toolable/doctrine split table, scope-sufficiency loop, DISPATCH line format. De-personalized, no private markers.
- tests/test_dag_scope.py: 54 new hermetic tests (structural / purity / resolution / frontier / discovery). tests/test_dag_disp.py: 5 boundary-smell tests updated to filter continues-warns from reads-scope warns (additive SR-SCOPE — DISP tests were count-exact against the old single-source warns).
- 472 total tests, all passing. rv lint PASS. rv help --check: OK (19 verbs).

### Decisions
- `reads:` OPTIONAL (operator decision per spec). Absent emits WARN (non-breaking, no fixture migration, same idiom as SR-DISP boundary smells).
- Resolution pass is separate from validate_manifest (purity boundary established by SR-DISP). `resolve_reads_pointer` wraps the `resolve_watch` fs-seam pattern (Path.exists + thin anchor helper) rather than re-rolling.
- Symbol form (`path:symbol`) — file existence is hard (ManifestError), symbol presence is soft WARN (grep in source text; no AST coupling, no language toolchain dependency).
- Anchor search: markdown headings containing the anchor text (any heading level; handles `## 5B-SCOPE.` and `## 5B-SCOPE` forms). Case-sensitive.
- DISP tests updated (not broken): existing tests checking `manifest_warns` count/empty now filter to `_continues_boundary_warns` to isolate from reads-scope warns. Documented in test comments.
- `_resolve_reads_or_warn` is non-blocking (advisory); hard pointer errors print to stderr but don't abort run/tick. Structural errors from validate_manifest already abort.

### Open / next
- PR needs Argus review + Architect fit-check (reviewer-gate class). No self-merge.

## 2026-07-01 (SR-XP + SR-STRIP build)

### Done
- Worktree: feat/sr-xp off origin/main (SR-GD merged), engineer crew identity (domain in private config only — no domain literal in any tracked file content).
- SR-STRIP: excised inert `disclosure` registry field from project.py — removed `_VALID_DISCLOSURE`, `_OPTIONAL_FIELDS["disclosure"]`, `disclosure` param from `_validate_entry`/`_render_project_section`/`cmd_add`, `--disclosure` CLI arg, and `disclosure=` column from `project list` output. Updated test_project.py (removed invalid-disclosure test, field-order disclosure assertion, forward-flag assertion, all `disclosure=` kwargs) and test_new_verbs.py (removed disclosure from cfg_with_project fixture). Repo-wide grep confirms zero functional disclosure readers remain.
- SR-XP real `rv project list`: replaced stub with `cmd_list()` — enumerates slug, code, roster, source_dir; no disclosure column. Exposed as `rv project list` CLI. Tests in test_project.py cover: >=2 projects, real fields, no disclosure, empty registry.
- SR-XP cross-project OKF link resolution: extended mdstore.py with `@slug:path/to/note.md` link syntax, `resolve_cross_project_link()` (returns resolved/path/project/note/provenance/error dict), `_parse_cross_project_ref()`, and updated `_check_links()` to pass cfg and handle cross-project refs (resolve on good links, flag dangling ones). `cmd_check` now passes cfg to `_check_links`.
- SR-XP `cross_project.py`: new module with `list_projects(cfg)` (structured records) and `corroborate_across_projects(claim, cfg, from_slug, against_slugs)` — free cross-project note search returning hits with @slug:path provenance. No gate, no disclosure scoping.
- SR-XP `rv research corroborate`: added `cmd_corroborate` + `corroborate` subparser to research.py — shells to cross_project.corroborate_across_projects, supports `--from` and `--against` flags.
- test_cross_project.py: 14 new hermetic acceptance tests covering SR-XP acceptance criteria: list_projects records, cross-project link resolution (good + dangling), _check_links integration, corroborate_across_projects (finds match, provenance shape, excludes from_project, against_slugs filter, no-hits, no ~/vault access).
- Full suite: 435 tests passing (was 382 pre-SR-XP). `rv help --check`: OK. Leakage scan green.

### Decisions
- Cross-project link syntax `@slug:path` (not `@slug/path`) to distinguish from standard relative paths and avoid ambiguity with directory separators.
- `corroborate_across_projects` does keyword substring matching (case-insensitive) — sufficient for the hermetic acceptance test; a production instance can replace with semantic search via the adapter seam.
- `resolve_cross_project_link` returns a dict (not raises) so callers can accumulate errors without short-circuiting the link scan.
- SR-STRIP: no backward-compat shim needed — existing TOML files may carry `disclosure` fields (TOML ignores extra keys at load); only writes and list-output are affected.

### Open / next
- PR needs Argus review + Architect fit-check (reviewer-gate class). No self-merge.

## 2026-07-01 (SR-LINT build)

### Done
- Worktree: feat/sr-lint off origin/main (SR-5 + SR-GD merged), engineer crew identity.
- src/research_vault/lint.py: two new test-hygiene rules (SR-LINT), exported as public functions.
  - check_vacuous_assertions(files): flags `assert True` and `or True` in test files. Pattern: `\bassert\s+True\b` / `\bor\s+True\b`. Reports (file, lineno, label, line).
  - check_unpinned_git_init(files): flags `["git", "init"` without `--initial-branch` on the same line. Tight regex `"git",\s*"init"` avoids false-positives on git commit `-m "init"` messages.
  - _TESTS_DIR module-level constant (monkeypatchable) pointing to repo-root/tests.
  - _get_test_hygiene_skip_files: config-driven (lint.test_hygiene_skip_files) + hardcoded default ["test_lint_rules.py"] (self-exclusion, same pattern as leakage scan).
  - cmd_lint updated: rules 4a + 4b run after existing checks; each reports per-file findings with file:line and contributes to exit code.
- tests/test_lint_rules.py: 27 hermetic tests (TDD red→green). TestVacuousAssertions (12), TestUnpinnedGitInit (11), TestCmdLintIntegration (4). No subprocess; calls rule functions directly on temp files.
- 445 total suite passes. rv lint repo-wide: PASS (23 test files checked, zero findings).

### Decisions
- Self-exclusion of test_lint_rules.py: the test file plants `assert True`, `or True`, and `["git", "init"` in string literals (to test detection); scanning it would false-positive. Excluded by default in _get_test_hygiene_skip_files, exactly as test_leakage_scan.py is self-excluded from the leakage scan.
- Tight git-init regex (`"git",\s*"init"`) rather than broad `"git".*"init"`: the broad form matches git commit calls with `-m "init"` messages (false positives found in test_sr_cp.py). The tight form requires init to immediately follow git in the list literal.
- Vacuous-assertion rule: reports each line at most once (first matching pattern wins) to avoid double-counting a line that matches both `assert True` and `or True`.

### Open / next
- PR open, awaiting Argus review + Architect fit-check (reviewer-gate class, no self-merge).

## 2026-07-01 (SR-GD build)

### Done
- Worktree: feat/sr-gd off origin/main, engineer@example.invalid crew identity (placeholder — real domain in private instance config).
- tests/gitutil.py: promoted shared fixtures from SR-CP (tmp_git_repo, squash_merge_repo, invoke_cli). conftest.py re-exports tmp_git_repo globally.
- src/research_vault/gitlib.py: shared squash_terminal_ids() helper (Signal D / GD-D4). Single implementation consumed by git_health + control-reconcile — no duplication. (B1 fix: status.py formerly had a duplicate inline squash parser + _PR_ANCHOR_RE; now fully migrated to gitlib.)
- src/research_vault/git_health.py: Signal D added — squash-merged branches now classify DELETE (was FLAG). Imports gitlib.squash_terminal_ids. Updated docstring + when_to_use anti-patterns.
- scripts/leakage_scan.sh: --staged (git diff --cached --name-only file-list mode) + --secrets-only (class 5 only; project-repo profile) flags added.
- src/research_vault/git_discipline.py: new verb — check --staged (profile-aware protect-main + leakage + lint), commit-msg, install/uninstall/status (core.hooksPath per-repo, idempotent, cross-repo --all, prints branch-protection guidance).
- cli.py: git-discipline registered in _VERB_REGISTRY; wt + git-health when_to_use strings updated with named anti-patterns (committed-to-main / never-made-a-worktree / hand-merged-red-CI).
- src/research_vault/wt.py: --project <slug> (target project repo source_dir) + --as <role> (set git identity by construction). Fixes wt.py:75 instance-root-only hardcode.
- doctrine/git-discipline.md: portable identity-free discipline clause — leakage-scanned, no private markers.
- .githooks/pre-commit + .githooks/commit-msg: tracked POSIX sh shims.
- 29 new hermetic tests; 384 total, all passing. rv lint PASS. rv help --check: OK (17 verbs). Leakage scan green on src/ + doctrine/.

### Decisions
- Crew domain default is example.invalid in public source. The real domain lives in private instance [crew] identity_domain config only — leakage scanner catches any file-content leak.
- git-discipline check subcommand takes --repo to override cwd when called from a hook in a different repo. Profile (framework vs project) resolved by comparing resolved repo path to cfg.instance_root.
- Signal D in git_health: branch token extracted via same regex as gitlib._ID_TOKEN_RE; matched against squash_terminal_ids(repo). Computed once per repo in cmd_report.
- --staged mode in leakage_scan.sh uses xargs -I{} to scan each staged file individually (not recursive over a directory).

### Open / next
- PR needs Argus review + Architect fit-check (reviewer-gate class).

## 2026-07-01 (SR-5 build)

### Done
- Worktree: feat/sr-5 off origin/main (SR-DISP merged), engineer crew identity (real domain in private config).
- note: watch form (wait_for.py): `note:<type>/<id>[+fresh]` — resolves OKF note paths
  relative to load_config().notes_root. Portable across installations (no hardcoded paths).
- examples/demo-research/research-loop.json: 8 nodes, full named crew (researcher/Ada,
  reviewer/Argus). Pre-registration gate: run carries
  `afterok+watch: note:experiments/exp-q1.md+fresh` so run cannot fire without the
  pre-reg note. All agent nodes have spec: (SR-DISP compliant).
- examples/demo-litreview/lit-review-loop.json: 8 nodes, full named crew. OKF coverage
  gate: distill nodes have produces: {note: "literature/<key>.md"}; cmd_complete's
  _check_okf_note_type blocks success without the note; okf-coverage-gate human-go only
  approvable when all distill nodes terminal.
- Placeholder note dirs + README files in each demo project.
- src/research_vault/init.py: rv init [<dir>] — scaffolds multi-project instance from
  templates. Creates: research_vault.toml, DEVLOG.md, architecture.md, control/,
  tasks/, doctrine/ (copied from repo), examples/demo-* (copied), notes/ + OKF type dirs,
  QUICKSTART.md. Guards against overwrite. Multi-repo topology note: examples/ are
  in-repo for demo; real projects are separate repos via rv project add.
- src/research_vault/check.py: rv check — preflight: Claude CLI + ANTHROPIC_API_KEY
  (required), asta + ZOTERO_KEY (optional). run_preflight() returns structured result dict.
  Clear install instructions on failure.
- src/research_vault/templates/QUICKSTART.md — getting-started guide with both loops.
- cli.py: init and check verbs wired (18 total, rv help --check: OK).
- 22 new hermetic tests; 369 total, all passing.

### Decisions
- note: watch form chosen over absolute paths in manifests — portable across installations.
- OKF coverage gate: structural enforcement via produces check in cmd_complete (not
  separate walker check). Human-go approvable once distill nodes terminal; distill cannot
  succeed without notes → gate bites without extra machinery.
- rv init copies examples/ from repo root (not from src package) — examples/ is not
  a Python package; importlib.resources not applicable. Graceful fallback if not found.
- No rv project new capstone in this increment (deferred per spec §5B-SR5-COH).

### Open / next
- PR #7 needs reviewer (Argus) + Architect fit-check.

## 2026-07-01 (SR-DISP build)

### Done
- Worktree: feat/sr-disp off origin/main, engineer crew identity (real domain in private config).
- Schema teeth (dag/schema.py): spec REQUIRED on agent nodes (ManifestError on absence);
  continues OPTIONAL = {node, reason} with full cross-node validation (exists, type:agent,
  transitive-upstream ancestor via walker._transitive_upstream import, not-self, reason
  non-empty). Each violation a ManifestError.
- manifest_warns() non-fatal WARN: continues path crossing produces:/human-go boundary
  (forward BFS from ancestor ∩ backward ancestors of node). Surfaced by dag run/tick/status.
- Frontier mode line (verbs.py _print_frontier): DISPATCH lines carry
  "FRESH — spec:<ptr>" or "CONTINUES <node> — <reason> — spec:<ptr>".
- Doctrine clause (doctrine/coordination.md): "Dispatch: fresh + pointed by default;
  resume is the justified exception" subsection — de-personalized, leakage-scanned.
- Discovery (cli.py _VERB_REGISTRY dag when_to_use): two named anti-patterns appended.
- Fixture migration (test_dag.py _node()): spec="fixture://test-spec" default for agents.
- 34 new hermetic tests; 347 total, all passing. rv lint PASS. rv help --check: OK.

### Decisions
- Import walker._transitive_upstream into schema.py — no circular dep (walker doesn't
  import schema); spec says "reuse"; inlining would be duplication.
- manifest_warns uses forward BFS from continues.node ∩ backward _transitive_upstream(nid)
  to find "between" nodes; O(N) total, cleanest formula.
- WARN prints before "Run started" / "Tick" headers so it's visible at top of output.
- human-go nodes exempt from spec requirement (they're decision gates, not dispatch targets).

### Open / next
- PR needs reviewer (Argus) + Architect fit-check before merge (human-go gate).

## 2026-07-01 (SR-CP build)

### Done
- Built full SR-CP (control-plane records lifecycle): READ + reconcile + WRITE + CLEAN + INDEX.
- controllib.py: shared parser, SPAWN_REQUIRED/RETURN_REQUIRED constants (11+6 fields), locked_mutate
  (fcntl.flock advisory lock), atomic_write, append_to_archive (MEMORY.md-shape sidecar index).
- status.py: rv status verb (control sections + task board + DEVLOG tail + local git + DAG runs);
  SignalSource Protocol seam (LocalGitSource, TaskBoardSource, DagRunSource); NO gh/network in core.
- control.py: cmd_reconcile (R1–R4 deterministic rules), cmd_post/spawn_request/return_entry/close/
  edit/move write-face verbs; all mutating verbs under advisory lock; cmd_heal inserts banner.
- devlog.py: cmd_index and cmd_search (structured read face without loading whole file).
- cli.py: status verb + anti-pattern lines in control/devlog registry entries; rv help --check: OK.
- doctrine: coordination-state clause in agent-charter + all 7 role docs (read/write enforcement).
- 41 deterministic hermetic tests for all 8 §5B-CP acceptance criteria. 307 total, all passing.

### Decisions
- SignalSource.get_terminal_set uses merge commit message parsing (--no-ff) + branch-tip-differs-from-
  main check (fast-forward); merge-base approach failed post-merge (merge-base == branch tip after FF).
- cmd_inbox backward-compat: returns Path (not tuple) to avoid breaking existing callers.
- NOT_YET_LEXICON includes "pending" as specified; ID token regex broadened to sr-[a-z0-9]+ to match
  alpha ids like sr-x, sr-cp (spec uses sr-4 but tests use sr-x placeholders).
- Archive sidecar: .archive.md with INDEX:START/END region at top; idempotent regeneration.
- RESOLVED_THRESHOLD=5 for teeth check.

### Open / next
- PR needs reviewer (Argus) + Architect fit-check before merge (human-go).

## 2026-07-01 (SR-4 reviewer fixes — B1/F1/F2)

### Done
- B1: pushed unpushed commit 41a3a16 (institutional-affiliation identity marker class, +2 scanner lines, +2 tests)
  together with F1+F2 below.
- F1: added 2 missing leakage-gate marker classes per SR-4 spec §5 scope-IN:
  - Class 8 (real citekeys): Pandoc bracket-citation format — detects private bibliography
    references; pattern matches bracket-at-letter prefix. 3 new tests.
  - Class 9 (real projects.json entries): hub-infrastructure slug + private project-registry code
    (not covered by Class 1 codename scanner). 3 new tests.
  Scanner now 9 classes; test suite 35 leakage tests (260+6=266 total). All green.
  Doctrine/ GREEN on new classes (Argus-confirmed no residue).
- F2: `git rm --cached doctrine/drift-watch.md` + added to .gitignore. File stays on disk as a
  local-only maintenance aid. DEVLOG references are historical (no link in docs/manifest/rv help).

### Decisions
- Class 8 pattern: bracket-at-letter prefix captures ALL Pandoc citations (any inline bracket-citation is a
  private citekey — doctrine must not cite the private bibliography).
- Class 9 patterns: hub-infrastructure slug + private project-code entry
  are the two projects.json entries NOT already caught by Class 1 codename scan.
- drift-watch.md kept local: it maps `~/vault/src/content/docs/method/` paths and Astro/Starlight
  layout — private vault structure. Operator call correct: keep as local-only re-sync aid, not
  committed to the soon-to-be-public repo.

### Open / next
- PR #4 still awaiting operator go after re-review (CI must be green before go).

## 2026-06-30 (SR-4)

### Done
- SR-4: full spine landed in PR #4 (feat/sr-4-spine).
- Leakage scanner: scripts/leakage_scan.sh upgraded from skeleton to full teeth — 7 marker
  classes, 27 hermetic tests (each RED on planted marker, GREEN on scrubbed content). CI now
  calls the script (replaces inline stub). Caught drift-watch.md self-referencing cluster paths
  before commit — good proof the gate is live.
- Agent charter, 6 portable doctrine disciplines (note-conventions, standards, review-board,
  coordination, memory-management), 7 role docs (Alfred/Wren/Atlas/Mason/Argus/Iris/Ada),
  drift-watch note — 15 commits, each preceded by a clean leakage scan.
- Scrub applied uniformly: identity strings, codenames, site URL, cluster paths, vault→rv CLI,
  private design themes, versioned model IDs stripped. Abstract policy (Sonnet/Opus/Haiku) kept.
- 258 tests passing. human-go gate: awaiting reviewer + architect + operator.

### Decisions
- Committed incrementally (one doc per commit) per the SR-4 brief — if session died, committed
  docs would survive. Session did not die; all 15 commits landed.
- Alfred (hub) role doc synthesized from charter + coordination + how-it-works (no standalone
  vault source exists) — flagged in PR review focus for close read.
- "sage seat" in reviewer doc scrubbed to "coordinator seat" — private identity for a specific
  GitHub token; the concept (coordinators post via a separate seat) is preserved.

### Open / next
- PR #4 awaiting: rv-reviewer verdict + rv-architect fit-check + operator go.

## 2026-06-30 (fix-round)

### Done
- BLOCKER 1: moved `project` positional to top-level verb parser for all 4 verbs (task, note,
  control, devlog) — documented form `rv task <project> <subcommand>` now works. All CLI tests
  updated to project-first form; new test exercises the documented form (was never tested).
- BLOCKER 2: fixed `_default_config()` to use relative sub-paths ("notes", "tasks", etc.)
  instead of cwd-absolute paths — `_expand_paths()` now resolves them against instance_root,
  so a config with only `instance_root` set correctly derives all paths from it.
  New test: minimal config (instance_root only) asserts all derived paths are under instance_root.
- BLOCKER 3: replaced a bare principal name with "owner" in the control.py template
  (docstring, _render_control_file, cmd_inbox fallback) and in control/acme.md. Added
  the corresponding word-boundary marker (case-insensitive) to the CI leakage scanner
  so this class of leak can't slip through again.
- 72 tests passing (was 70), rv help --check OK, leakage grep clean.

### Decisions
- Used "owner" (not "<owner>" or "the principal") in control template — clean, short, generic.
- Relative defaults in _default_config() rather than a sentinel/None approach — simpler, and
  _expand_paths() already had the relative-path resolution logic.

### Open / next
- PR #1 (feat/sr-1-scaffold) ready for re-review by rv-reviewer + rv-architect fit-check.

## 2026-06-30

### Done
- SR-1 scaffold: standalone package skeleton, config plane, CLI dispatcher, first 4 portable verbs
  (task, note, control, devlog), `rv help --check`, CI skeleton, tests.
- Package: `research_vault` / CLI verb `rv`, uv-managed, Python 3.12, pytest hermetic suite.
- Config plane: multi-project registry SSOT in `config.py` — zero hardcoded paths, zero codenames.
- Verbs are project-scoped: `rv task <project> …`, `control/<project>.md`, etc.

### Decisions
- Config SSOT: `research_vault.toml` (TOML, instance-local) with env-override escape hatch
  (`RESEARCH_VAULT_CONFIG`). Multi-project registry mirrors the live projects.json shape.
- Verbs re-implemented fresh from behavioral spec — no byte-for-byte copy of ~/vault code.
- `rv help --check` greens only when every registered verb has a `when_to_use` docstring.
- Leakage-scanner CI job: skeleton in place (grep for private markers); teeth land at SR-4.

### Open / next
- SR-2: remaining verbs (research, cite, role, build-agents, mdstore, wt, git-health, lint,
  wait-for) + adapter Protocols (Notifier, ComputeBackend, SecretStore) + plugin seam.
- ZERO ~/vault edits confirmed: all build + test work happened inside ~/research-vault.
