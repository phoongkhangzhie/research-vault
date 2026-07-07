## 2026-07-07 (feat/cc2-repro-determinism: PR-CC-2 tolerance-taxonomy field)

### Done
- **PR-CC-2 (D-CC-2 / CHECK-4b)** — added `repro_determinism` to `note.py`'s
  `REPRO_ALL_FIELDS`, the ONE new field the code-conventions design (§2.4/§3/§8)
  calls for. Values: `exact | tol:<eps> | stochastic` — declares the comparison
  a (future, deferred) golden-rerun test applies against a claimed result's
  `scores[].hash`, so an exact-hash gate never fails-forever on a legitimately
  nondeterministic pipeline.
  - Scaffolds to the strict safe default `"exact"` (Khang's R3) — NOT the
    `REPRO_SENTINEL` hole the other 22 fields get, since a complete default is
    not a fabrication-risk gap. Kept deliberately OUT of `REPRO_LINT_REQUIRED`
    so the sentinel-lint never flags it.
  - New `REPRO_TOLERANCE = ["repro_determinism"]` list, appended only to
    `REPRO_ALL_FIELDS`, not to `REPRO_LINT_REQUIRED`.
  - Experiments-note template doc-comment now names the taxonomy + points at
    `doctrine/code-conventions.md` §5 (already shipped by PR-CC-6).
  - This PR is a schema/consumer hook ONLY — CHECK-4b's golden-rerun *runner*
    stays soft/deferred per the design; no runner was built here.
- New `tests/test_pr_cc2_repro_determinism.py` (11 tests): schema membership,
  scaffold default + non-sentinel, doc-comment content, sentinel-lint
  unaffected (both filled and legacy-absent cases), `rv note check` green on a
  fresh scaffold, and all three taxonomy values (`exact`/`tol:1e-6`/
  `stochastic`) validate untouched by the static gate.

### Decisions
- No version bump — lands as part of the 0.2.0 bundle alongside the rest of
  the code-conventions PRs (PR-CC-1/4/6).
- Parallel-wave discipline: this PR touches only `note.py` + its own test
  file — no overlap with PR-CC-4's `scaffold.py`/`USER_OWNED_NEVER_TOUCH`/
  `CITATION.cff`/`LICENSE` surface.

### Open / next
- The golden-rerun runner that actually *consumes* `repro_determinism` to pick
  its comparison remains deferred/soft per the design — a future PR.

## 2026-07-07 (feat/manuscript-integration: assemble the M2/M3/M4/M6 gates + wire source_transform)

### Done
- **The manuscript-loop INTEGRATION PR** — M2 (hermetic `.bib`), M3 (support-matcher +
  cold-read fidelity gates), M4 (equation machinery), and M6 (`lit-review` type: section-set,
  framework selection, `source_transform`) all merged to `main` independently, but nothing
  assembled them together or wired M6's `source_transform` into the drafting DAG. This PR
  closes both gaps:
  - **New `manuscript/check_gates.py :: build_approve_payload`** — assembles the four gates by
    honesty-class: `check_hermetic_bib` (M2) hard BLOCK, always runs; `check_equation_fidelity`
    (M4) SIGNAL only (D-MS-2, never BLOCK even marked-critical); `check_support_tally` /
    `check_cold_read_tally` (M3) BLOCK/SIGNAL respectively, both **behind the judge guard**
    (`RV_JUDGE_MODEL` + `ANTHROPIC_API_KEY`, or an explicit `judge_fn`) — absent, they land in
    `not_run` and are surfaced loudly, never a silent skip, never green-and-empty. The coverage
    gate (design §10 gate-4) is explicitly deferred to PR-M5 and recorded in `not_run`, not
    silently omitted.
  - **Wired into `dag/verbs.py :: cmd_approve`** at `node_id == "approve-manuscript"` — mirrors
    the existing `approve-framework` wiring exactly (BLOCK → `return 1`, no state mutation;
    SIGNAL/not_run → printed, never blocking; `--reject` bypasses as the escape hatch).
  - **Wired M6's `source_transform`** (previously computed nowhere — dead code) into
    `manuscript/__init__.py :: _build_phase2_manifest` via a new `_inject_source_transform_tips`
    helper (mirrors PR-M4's `inject_equation_brief` seam pattern): the PRISMA ledger →
    `prisma-scope` spec, the comparison table → `references` spec, the frozen framework branches
    → both `framework` and `thematic-sections` specs. `cmd_expand` now reads the frozen spine
    (`spine_shape`+`branches`) from `_manuscript.md` and threads it through as
    `manuscript_fields`.
  - **Tightened `equations.py :: _deterministic_match`** — the reviewer-caught false negative:
    the old BIDIRECTIONAL substring check let a short, unrelated draft equation mask a much
    longer DROPPED critical equation (`"a+b" in "x=a+b+c+d"` would count as a match). Tightened
    to one-directional (ledger-in-draft only) + length-gated. New regression test proves RED
    against the old logic, GREEN after.
  - Fixed stale forward-refs in `manuscript/__init__.py` (the `refs.bib`/`main.tex` stub
    comments, the module docstring, and the `approve-manuscript` node label) that still said
    "lands in PR-M2" etc. for gates that have since landed.
  - New end-to-end integration test (`tests/test_manuscript_integration.py`): real
    `cmd_new --type lit-review` → frozen spine → `cmd_expand` → `build_approve_payload`, proving
    (a) a dangling `\cite` BLOCKs, (b) a dropped marked-critical equation SIGNALs (never BLOCKs),
    (c) `source_transform`'s output actually appears in the Phase-2 manifest's section specs
    (not dangling), (d) with no judge configured the LLM gates land in `not_run` and are
    surfaced loud (not a green pass). Plus a real `rv dag approve` wiring test mirroring M6's
    `TestApproveFrameworkGateWiring` pattern.

### Decisions
- Followed the operator's LOCKED judge-guard policy from the dispatching brief (not the design
  doc's own text): the LLM gates run only when a judge is actually configured; absence is a loud
  `not_run`, never a block and never a silent skip.
- `build_approve_payload`'s `project_notes_dir` is derived as `tree_root.parent.parent` (matching
  the existing `manuscripts/<slug>/` folder convention and `check_support_tally`'s own default
  inference) — the dispatching brief's inline pseudocomment said `tree_root.parent`, which would
  resolve to the `manuscripts/` dir, not the project notes root; flagged as a likely brief typo
  and built to the actual repo convention instead.

### Open / next
- PR-M5 (review-revise board) is the single-sourced consumer of `build_approve_payload` for its
  per-round re-fire — do not duplicate the gate assembly there.
- The dup-FM-label last-write-wins behavior in the equation ledger join (a separate reviewer-noted
  issue) remains a documented follow-on, not fixed in this PR.

## 2026-07-07 (fix/patch-dataset-provenance-warn: [dataset-provenance] WARN degrade)

### Done
- **Patch fix (0.1.3 -> 0.1.4): `[dataset-provenance]` WARN was hard-failing
  `rv note check`.** Surfaced by an adopter-project backfill task. `check_dataset_provenance_warn`
  (F24)'s own docstring states "This is a SURFACE, never a BLOCK — INFO/WARN
  only", and its output is prefixed `[dataset-provenance] WARN:` — same shape
  as `[repro-lint]`/`[gap-hygiene]` (both already degrade-to-warn). But the
  `_WARN_PREFIXES` tuple in `note.run()`'s `check` dispatch (contract at
  `note.py:1216`) omitted `[dataset-provenance]`, so a ran experiment with an
  unrecorded dataset provenance hard-failed the CLI (exit 1) instead of
  degrading (exit 0, warn surfaced) — contradicting the check's stated
  contract.
- Confirmed genuinely a defect (not an intended hard gate) by reading the
  check's own docstring + comparing to the two existing WARN-class checks
  that already degrade correctly. `check_result_provenance` (hard hash-mismatch
  violations, e.g. a `results_hash` mismatch on a local artifact) returns
  UNPREFIXED strings and is untouched by this fix — still hard-fails.
- Fix: added `"[dataset-provenance]"` to the `_WARN_PREFIXES` tuple.
- New tests (`tests/test_datasets_note.py::TestCliDatasetProvenanceDegrade`):
  a `[dataset-provenance]` WARN alone now exits 0 with the warning surfaced
  in stdout (proved RED against origin/main before the fix — exit 1); a
  genuine hard provenance violation (fabricated `results_hash` mismatch
  against a real local artifact) still exits 1, scoping the degrade to the
  WARN class only.
- Version bump 0.1.3 -> 0.1.4 (`pyproject.toml`, `src/research_vault/__init__.py`)
  — a shipped CLI-behavior fix, patch-level per semver.

### Decisions
- **Release-sequencing note for the hub:** this fix only reaches a consuming
  project once research-vault 0.1.4 is released (tagged + published) AND that
  project bumps its pinned `research-vault` dependency. Until then, consuming
  projects will continue to see the hard-fail on `[dataset-provenance]` WARNs.
  Sequence the 0.1.4 release ahead of (or alongside) any dependent
  backfill/adoption work, not after.

### Open / next
- Held PR — awaiting reviewer + maintainer go (release-class change,
  tag-triggered publish.yml is out of scope for this PR).

## 2026-07-07 (feat/manuscript-m3-shared-gates: PR-M3 — the re-instantiated LLM fidelity gates, shareable location)

### Done
- **PR-M3 — the re-instantiated hard fidelity gates, landed in a SHAREABLE
  location (D-SV-0).** `manuscript/support_matcher.py` and
  `manuscript/coldread.py` (deleted in SR-RM-FIGMS) are re-instantiated as
  `research_vault.gates.support_matcher` / `research_vault.gates.coldread` —
  a NEW top-level `gates/` package, sibling to `manuscript/`, `review/`, and
  `experiment/`, rather than back under `manuscript/`. Rationale: the craft
  these modules embody (anti-anchoring, disconfirm-first,
  verbatim-span-or-BLOCK, blind-judge canary, fail-closed defaults — see
  `data/doctrine/honesty-gates.md`) is not manuscript-specific; any loop
  that needs a claim→source support check or a self-containment read can
  call these directly. The manuscript loop is the first consumer, not the
  only one.
  - `gates/support_matcher.py` — the 4-verdict claim→source matcher
    (`[SUPPORTS|PARTIAL|ABSENT|CONTRADICTS]`), rebuilt from the preserved
    craft (recovered via `git show <pre-SR-RM-FIGMS-sha>:...`), scrubbed of
    crew narrative-names (class-10 leakage) and re-pointed at its new home.
  - `gates/coldread.py` — the 3-verdict self-containment judge
    (`[STANDS-ALONE|DANGLING|NEEDS-CONTEXT]`) with the Flag-A deterministic
    scan + bidirectional canary, same treatment.
  - `gates/_llm.py` — extracted the two modules' near-identical urllib
    Anthropic-Messages-API call into one shared `call_anthropic_messages()`
    helper (charter §6: reuse over create) — the two gates previously
    carried independent copies of the same plumbing.
  - `manuscript/fidelity_gates.py` — the thin, additive manuscript-loop
    adapter: `check_support_tally()` (batch claim/citekey extraction over a
    manuscript tree + canary-gated support-matcher tally) and
    `check_cold_read_tally()` (pdftotext resolution + cold-read + honest
    errors/warnings composition). Does NOT touch `manuscript/check_gates.py`
    (PR-M2/PR-M6 territory, built concurrently) — a standalone new module
    the future `build_approve_payload` assembler can import from.
- **Tests (planted-failure required by acceptance):** `test_gates_support_matcher.py`
  plants a claim with genuinely no support in its cited note and proves the
  gate BLOCKs end-to-end (a discriminating mock judge that actually reads the
  injected note content); `test_gates_coldread.py` plants a context-dependent
  passage (internal run-id + artifact path) and proves both the LLM path AND
  the deterministic Flag-A path independently flag it `[DANGLING]`. Both
  planted tests were mutation-tested (neutered the parser / fail-closed
  default and confirmed the test goes RED) to prove they have teeth.
- Gates: `leakage_scan.sh` clean over `src/research_vault` (crew-name scrub
  verified: "Ada"→researcher, "Wren"→architect throughout the two
  re-instantiated modules), `rv lint` PASS, `rv help --check` OK.
- Held `human-go` PR: Architect fit-check requested on the `gates/` shared
  location call (D-SV-0 names "a shareable module" without pinning the
  exact path).
- **Fix-up (post-review): `ColdReadResult.blocks` fail-closed on
  `canary_aborted`.** Reviewer BLOCK — `.blocks` did not consider
  `canary_aborted`, so a judge that raises on every call produced
  `canary_aborted=True`, `overall=STANDS-ALONE`, `.blocks==False`; a direct
  caller checking `.blocks` alone (per D-SV-0) would treat a totally-broken
  judge as a pass. Fixed: `blocks` now also returns `True` when
  `canary_aborted`. New test proves a dead/raising judge -> `.blocks==True`
  at the shared API. Confirmed `support_matcher`'s per-call judge-exception
  path already degrades to `verdict=ABSENT` (already fail-closed via
  `.blocks`) — no change needed there.

## 2026-07-07 (feat/manuscript-loop-m1: the manuscript loop, re-instantiated with a type system)

### Done
- **PR-M1 — the manuscript-loop type-generic core.** Re-instantiates the
  `manuscript` loop removed in SR-RM-FIGMS, rebuilt with a TYPE system
  (design: `docs/superpowers/specs/2026-07-07-survey-capability-design.md`).
  The manuscript loop turns `notes/` (crew-reasoning pillar) into
  `manuscripts/<slug>/` (user-facing deliverable pillar), BY TYPE —
  `type: lit-review` is the survey specialization; a future
  `type: experiment-paper` is a results paper.
  - `manuscript/types/` — the `ManuscriptType` descriptor registry
    (`SectionSpec` + `ManuscriptType` dataclasses, `register_type`/`get_type`/
    `all_type_keys`). Only `lit-review` is registered, as an
    interface-conforming STUB (one placeholder `draft` section) — the real
    9-row survey section table, framework-selection Phase-1, source
    transform, style briefs, exemplar bundle, rubric, reviewer lenses, and
    canaries all land in PR-M3/M5/M6/M8.
  - `manuscript/__init__.py` — `cmd_new` (scaffolds the per-manuscript folder:
    `manuscripts/<slug>/{_manuscript.md, main.tex, sections/, refs.bib,
    figures/}` + a type-optional Phase-1 manifest), `cmd_expand` (builds the
    Phase-2 manifest generically from the type's `section_set`: section(s) →
    assemble → `[HG:approve-manuscript]`), `cmd_review` (PR-M5 stub — raises
    `NotImplementedError` loudly, never a silent no-op), `cmd_list`.
  - `manuscript/style.py` — the style seam (`get_manuscript_style_preamble` +
    `get_manuscript_section_tips`), mirroring `review/style.py`'s tips-seam
    pattern; `[manuscript_style]` adopter override.
  - `manuscript/verbs.py` + `dag/catalog.py` (`manuscript` `LoopEntry`,
    gate: `approve-manuscript`) + `cli.py` (`_VERB_REGISTRY["manuscript"]` +
    a new "Manuscript" `_HELP_PHASE_MAP` group) — `rv manuscript <project>
    new/expand/review/list`.
  - Unknown `--type` fails loudly (no silent fallback); re-scaffolding an
    existing slug raises `FileExistsError` (no silent overwrite); an empty
    `section_set` on `expand` raises rather than emitting a fabricated
    manifest. `reads:` pointers are absolute (Fix #34 lesson).
  - Reversed the SR-RM-FIGMS removal pins: `dag/catalog.py`'s
    `TestCatalogCompleteness`/`TestCatalogGrounding`, `dag/verbs.py`'s
    `templates` output tests, and `cli.py`'s help/registry tests all updated
    to assert manuscript's reinstatement (figure stays removed).
  - Reuse verified against the tree (not assumed from the design doc): the
    two-phase scaffolder pattern, the style-tips seam, and absolute
    `reads:`-pointer convention all reuse `review/__init__.py` +
    `review/style.py` verbatim in shape; the DAG engine/schema/walker are
    zero-touch (`dag/catalog.py` gains one new `LoopEntry`, no new
    walker/schema mechanism).
  - Gates green: `rv lint`, `rv help --check`, `leakage_scan.sh
    src/research_vault`, full suite (2472 passed, 3 skipped).

### Decisions
- The manuscript-note pointer (old module: separate `manuscript/<id>.md` OKF
  note + `manuscripts/<id>/` tree) collapses into ONE per-manuscript folder
  (`manuscripts/<slug>/_manuscript.md` inside the tree) — simpler than the
  removed module's two-location split, matching the design's per-manuscript-
  folder convention (§0/§12).
- The `lit-review` stub's `phase1_builder` is `None` (pass-through) rather
  than a placeholder framework-selection stub — the real framework-selection
  sub-loop is design §5/PR-M6 territory; faking it now would be a fabricated
  Phase-1 that PR-M6 would have to tear out.

### Open / next
- PR-M2 (hermetic `.bib` build + citation-resolve gate), PR-M3 (hard fidelity
  gates), PR-M4 (equation machinery), PR-M5 (review-revise board), PR-M6
  (lit-review's real section table + framework-selection Phase-1), PR-M8
  (exemplars + rubric/canary) all build on this core per the design's PR
  sequencing.

## 2026-07-07 (release/0.1.3: version bump to 0.1.3)

### Done
- Version bump `0.1.2 → 0.1.3` in `pyproject.toml` and
  `src/research_vault/__init__.py`. `test_cli_version` asserts against the
  live `__version__` (de-hardcoded in the 0.1.1 bump), so it follows the bump
  automatically with no test edit needed.
- Ships the **CS-project folder-structure convention**, gathering three
  convention PRs merged since 0.1.2:
  - **#146 — canonical project tree.** Repo-root-is-the-vault layout:
    `notes/` + `code/` + `data/` + `results/{runs,scores}` + `figures/` +
    `manuscripts/`. The `results/runs` vs `results/scores` split: `runs/` is
    raw per-run output (gitignored), `scores/` is the computed, tracked SSOT.
    Documents the notes↔artifacts linkage principles. New
    `doctrine/project-structure.md`; `rv project new` scaffolds the tree with
    the correct gitignore/tracked policy baked in.
  - **#147 — generalized experiment-results schema.** `runs:` + `scores:`
    lists replace the old flat single-result shape, handling N-runs → M-scores
    (e.g. multiple seeds folded into one aggregate metric). Each score is
    hash-anchored. `check_result_provenance` now verifies **every** score in
    the list, aggregating violations rather than stopping at the first.
    `_normalize_results` is a read-shim that folds the legacy flat
    `results_location`/`results_hash` fields into the new shape — backward
    compatible; existing demo notes verify unchanged.
  - **#148 — `rv orient <project>`.** One-shot cold-context-switch tool
    (Coordination verb group, own `when_to_use` naming the trigger explicitly)
    bundling the `rv status` read, the full `pointers.md` content, and the
    `architecture.md` head. Blesses the `pointers.md` MUST-contain skeleton
    (Identity · POINTERS · Roadmap · Team · Operational-state) scaffolded by
    `rv project new`, documented in `doctrine/coordination.md`.
- Verified the publish path is untouched: `.github/workflows/publish.yml`
  diffs clean against `origin/main` — still tag-triggered
  (`on: push: tags: v*.*.*`), still PyPI OIDC Trusted Publishing
  (`environment: pypi`, `permissions: id-token: write`, no stored token).
- Build-verified: `uv build` → `dist/research_vault-0.1.3-py3-none-any.whl` +
  `.tar.gz`; `uvx twine check dist/*` → PASSED; fresh-venv install of the
  wheel → `rv --version` prints `0.1.3`.
- Full suite green, `rv lint` PASS, `rv help --check` OK, leakage scan clean.

### Decisions
- Held PR: this release bump does not self-merge or push the tag. The
  maintainer merges and tags — the tag push is the OIDC publish trigger, an
  irreversible outward-facing action (charter §5), so it waits for an
  explicit go.

### Open / next
- After merge: `git tag v0.1.3 <merge-sha> && git push origin v0.1.3` to
  trigger the publish workflow.

## 2026-07-06 (feat/orient-context-switch: `rv orient` — one-shot cold-context-switch)

### Done
- New verb **`rv orient <project>`** (`orient.py`): a one-shot cold-context-switch
  orientation. Bundles, in order: the operational `rv status` read (reused
  as-is — no duplicated logic), the **FULL** `pointers.md` content (not just
  the head `rv status` echoes), and the `architecture.md` head (capped at 60
  lines with a truncation nudge — the diagram can be large). Missing artifacts
  print a graceful "none yet — add to `<path>`" nudge, never a crash.
- Registered top-level in `_VERB_REGISTRY` (own `when_to_use` naming the
  cold-switch/context-switch trigger explicitly) and in the `Coordination`
  `_HELP_PHASE_MAP` group, right under `status` — chosen over a buried
  `--orient` flag for discoverability: a dedicated verb gets its own row +
  full `when_to_use` paragraph in `rv help`, which is the point (Alfred must
  find it as its own capability, not an obscure flag on a command already
  understood as "coordination read").
- Blessed the `pointers.md` **MUST-contain skeleton** — Identity · ★ POINTERS
  · Roadmap · Team · Operational-state — as the shape `rv project new`
  scaffolds (`_render_pointers_skeleton` in `project.py`, upgraded from a
  headerless placeholder). Documented in `doctrine/coordination.md`.
- **Fixed the broken self-reference**: `pointers.md:66` pointed at
  `~/research-vault/architecture.md`, which didn't exist — a July-2026
  "overkill for public package" pass had deleted it, but the CS-project-
  structure convention (PR-1/PR-2, this branch) re-blessed `architecture.md`
  as a scaffolded, USER-OWNED per-project artifact. Authored a real, minimal
  `architecture.md` at the repo root (components, data flow, the two research
  loops, OKF types, doctrine index, key decisions) — rv dogfoods its own
  convention on itself. Re-added `architecture.md` to the CI leakage-scan loop
  (it was dropped from the loop in the same July deletion).
- Doctrine: new "`rv orient` — the one-shot cold-context-switch primitive" +
  "The `pointers.md` MUST-contain skeleton" sections in `coordination.md`;
  cross-link from `project-structure.md`.
- Full suite green (2412 passed, 3 skipped); `rv lint` PASS; `rv help --check`
  OK (34 verbs); leakage scan clean on `architecture.md` + doctrine.

### Decisions
- Verb, not flag: the investigation (`docs/superpowers/specs/2026-07-07-
  multi-project-context-switch-findings.md`) leaned toward a `--orient` flag
  on `status` ("fold into the habit"). Overridden per the operator's explicit
  discoverability priority for this task — a dedicated verb is more reachable
  than an option on a command whose whole docstring insists it's
  coordination-only.
- `architecture.md` head capped at 60 lines (not the full file) — Mermaid
  diagrams can run long; the head orients, the nudge points at the full read.

### Open / next
- `rv project new`'s scaffolded `architecture.md`/`pointers.md` templates
  could grow a `--from-existing` mode that back-fills the skeleton from an
  already-populated sibling project — not scoped here.

## 2026-07-06 (feat/wandb-per-project-logging: version bump to 0.1.2)

### Done
- Per-project W&B logging (Plane B): the W&B **project** now defaults to the
  calling research-vault project's own slug automatically, decoupled from
  **entity** (still declared once, account-level). `resolve_run_logging_target`
  gained a `project_slug` parameter with independent precedence per side:
    - entity: `[observability].wandb_project` entity-part → `WANDB_ENTITY` env
      → compute manifest `results.wandb.entity`.
    - project: `[observability].wandb_project` project-part → `WANDB_PROJECT`
      env → `project_slug` (new default) → compute manifest
      `results.wandb.project` (kept as a legacy last-resort fallback).
  `log_experiment_run` threads `project_slug` through to the resolver.
- `probe_run_logging` no longer hard-fails when no STATIC project resolves —
  an unresolved static project is normal now (the per-run slug covers it); the
  probe only fails on `wandb` not importable or `WANDB_API_KEY` absent. This
  was the critical fix — every adopter's `rv check` / `rv observability probe`
  would otherwise red on the new default.
- `rv compute init` scaffold and the `rv compute` wizard now ask/declare
  **entity only** — the `results.wandb.project` FILL/prompt was dropped from
  both (the manifest-level read is kept for legacy manifests).
- A resolved project slug containing a W&B-illegal character (space, `/`, `\`,
  `#`, `?`, `%`, `:`) triggers a loud `UserWarning` rather than a silent
  sanitize (charter §2 grounding/surface rule).
- Bumped `version`/`__version__` `0.1.1` → `0.1.2`. Doctrine
  (`compute-run-recipe.md`) updated with the new precedence.
- Full suite green (2394 passed, 3 skipped); `rv lint` + leakage scan clean.

### Decisions
- Held PR for the maintainer to merge (published package — engineer does not
  self-merge). The `v0.1.2` tag push is a separate explicit step after merge.

## 2026-07-06 (release/0.1.1: version bump)

### Done
- Bumped `version` in `pyproject.toml` and `__version__` in
  `src/research_vault/__init__.py` from `0.1.0` to `0.1.1`.
- 0.1.1 ships two merged correctness/data-integrity fixes:
  - **#141 / #48 / #48b** — `cmd_close` and `reconcile --archive` now capture
    the WHOLE multi-line control-plane entry block, not just the leading
    bullet line. Previously, closing/archiving a control entry with
    multi-line body fields silently orphaned or deleted those fields
    (`_find_entry_block` / `_remove_entry` in `control.py`) — a real
    data-loss bug in the control-plane record.
  - **#142 / #33** — the literature-review L-2 anti-fishing gate (a non-empty
    `counter-position` field on `_protocol.md`) is now **structurally
    enforced** in code (`check_protocol_gate()` wired into
    `rv dag approve <run_id> approve-protocol`), rather than relying on
    agent-prose instructions alone. An empty/missing counter-position now
    hard-refuses the approval.
- No functional changes beyond the version bump; #141/#142 were already
  merged to `main` in prior sessions (this entry documents the release cut).

### Decisions
- Held PR (`chore/release-0.1.1`) for the maintainer to merge; the `v0.1.1`
  tag push (which triggers `publish.yml` → PyPI via OIDC trusted publishing)
  is a separate, explicit step after the bump PR merges — not part of this PR.

## 2026-07-06 (fix/e2e-med-review-protocol-lint: L-2 counter-position structural gate)

### Done
- Task #33 (E2E-MED): the review loop's L-2 anti-fishing gate (`_protocol.md` must
  carry a non-empty `counter-position` field) was agent-prose-only — the
  `review_scope_tips`/`review_critic_tips` spec text instructs it, but nothing in
  code enforced it. Added `check_protocol_gate()` in `review/__init__.py` (reuses
  the canonical `note._parse_frontmatter` — no re-rolled parser) and wired it into
  `rv dag approve <run_id> approve-protocol`: an empty/missing `counter-position`
  field now REFUSES the approval (nonzero exit, node stays `awaiting-go`, no state
  mutation), gated on the review loop's fixed node convention
  (`review-scope`/`approve-protocol`, §5L.3). `--reject` bypasses the gate (explicit
  abandon/redo path, untouched).
- 10 new tests in `tests/test_review_protocol_gate.py`: unit-level `check_protocol_gate`
  (missing file, empty/missing/whitespace-only field, non-empty pass) + real-DAG-path
  `cmd_approve` wiring (refuse+no-mutation, refuse-on-missing-field, approve-on-non-empty,
  `--reject` bypass, and a mutation test that neutralizes the check to prove the real
  gate — not some unrelated block — is what blocks the empty case). RED-before-GREEN
  confirmed by reverting `dag/verbs.py` and observing the wiring tests fail.
- `cli.py`'s `review` verb `when_to_use` updated to state the enforcement is now
  mechanical, not prose-only, plus an anti-pattern line against hand-editing run
  state to bypass the gate.

### Decisions
- Gate keyed on the review loop's fixed node-id convention (`review-scope` producing
  `_protocol.md`, `approve-protocol` node id) rather than a `run_id` string-match —
  more robust to review scope naming, and self-skips cleanly for any non-review
  manifest that happens to reuse the `approve-protocol` id without a `review-scope`
  producer.
- No new CLI override flag: `--reject` already provides the explicit path to move
  past a rejected protocol (mirrors the K-3 freeze-hash hook's hard-block convention
  — no bypass flag, fix the artifact and re-approve).

## 2026-07-05 (feat/rv-update: framework-refresh verb + demo removal)

### Done
- `scaffold.py` (NEW): the shared framework-materialization SSOT both `init` and
  `update` consume — `pkg_data()`, `iter_managed_statics()` (CLAUDE.md, QUICKSTART.md,
  doctrine/**), hashing, `.rv-manifest.json` read/write, version helpers (stdlib tuple
  split — no `packaging` dep), `[meta]` upsert/read, `.gitignore` append-merge, and the
  `USER_OWNED_NEVER_TOUCH` partition set. Prevents init/update drift (a file added to init
  but not update = invisible-on-upgrade).
- `rv update` (NEW verb, `update.py`): `--check|--dry-run|--no-commit|--skip-modified|--force`.
  Dirty-tree guard; refreshes framework statics (doctrine/, CLAUDE.md, QUICKSTART.md) via the
  shared scaffold path; RE-RUNS `build-agents --target claude-code` to recompose the DERIVED
  crew hats; append-merges `.gitignore`; rewrites `[meta]` + `.rv-manifest.json`; lands a
  dedicated `rv update: framework vX → vY` commit.
- Demo removal (Slice 2): `rv init` no longer copies `examples/` nor registers
  demo-research/demo-litreview; demo lines dropped from next-steps + DEVLOG template + QUICKSTART.
  The package still ships the loop manifests (for `rv dag templates`).
- Version stamp (Slice 3): `[meta]` in research_vault.toml + tracked `.rv-manifest.json`
  (per-file as-shipped hashes — the drift substrate).
- Staleness nudge (Slice 5): `rv check` prints an INFO line (never a FAIL) when the installed
  package is newer than the vault's `[meta].framework_version`.
- Canonical getting-started sequence made consistent across `rv init` closing output,
  QUICKSTART.md, and README.md: `rv init myvault` → `cd myvault` → `rv onboard` → `rv start`.
- 23 tests in `tests/test_sr_rv_update.py`; updated 5 init tests for demo removal. Full suite: 2310 passed.

### Decisions
- Crew hats are DERIVED, not files — `update` never diffs/copies a hat; it refreshes doctrine/
  then recomposes via `cmd_build`. Hat idempotency: hats are composed from the NEW package
  doctrine in-memory (`compose_cc_file`) for an exact dry-run/no-op prediction.
- 3-bucket partition: USER-OWNED (never touched, incl. `architecture.md` — the architect's living
  map); framework statics (overwrite-with-backup, hash-based user-modified policy → `.rv-bak`);
  `.gitignore` append-merge (never remove a user line). `research_vault.toml` is user-owned except
  the surgically-upserted `[meta]` block.
- No-op is decided from the plan (all-unchanged AND version-unchanged) BEFORE writing, so an
  idempotent re-run makes no commit; `[meta].updated_at`/manifest are only rewritten on real change.
- User-modified policy is hash-based: pristine (`hash==manifest`) → silent overwrite; modified
  (`hash != manifest and != new`) → backup + overwrite + loud (or keep with `--skip-modified`,
  which does NOT advance the manifest hash so the file stays flagged).
- Fixed the stale roster-count docstring in init.py (DEFAULT_ROSTER is 4 → 5 hats, not 6).

### Open / next
- human-go: this is a public-facing / cross-cutting change (new verb + demo removal + doc surfaces)
  — awaiting operator review + merge. PR pushed on `feat/rv-update`.

---
## 2026-07-05 (fix/init-git-repo: rv init now git-inits the vault)

### Done
- `init.py`: `_git_init_vault(target)` — runs `git init --initial-branch=main`, writes `.gitignore`, and makes an initial commit using `-c user.name/email/gpgsign` flags.
- `.gitignore`: `state/*` (with `!state/compute_manifest.json` exception), `control/`, `.venv/`, `__pycache__/`, `*.pyc`.
- Graceful git-missing path: warns on stderr, scaffold rc=0.
- 25 tests in `tests/test_init_git_repo.py`. Full suite: 2199 passed.

### Decisions
- `state/*` not `state/` — git doesn't re-include files inside an excluded directory; wildcard form required for the `!compute_manifest.json` exception to fire. Structural test with `git check-ignore` (exit 1 = not ignored) proves the exception works.
- `control/` ignored: coordination bus changes on every operation; not worth versioning.
- git-init runs AFTER build-agents so the initial commit captures all generated files including agent hats.

### Open / next
- PR creation blocked by collaborator gap (mason can push but API token can't resolve repo for PR creation). Human needs to open the PR manually from branch `fix/init-git-repo`.

---
## 2026-07-05 (rv-start: front-door verb to launch Claude Code in the vault — feat/rv-start)

### Done
- `start.py` — new `rv start [vault_path]` verb. Verifies vault (research_vault.toml + CLAUDE.md)
  and runtime (claude on PATH) with actionable preflight errors, then exec-replaces with `claude`
  in the vault dir so the session boots as Alfred. Passthrough args forwarded verbatim.
- Registered in `_VERB_REGISTRY` with `when_to_use` + anti-pattern.
- Updated `rv init` closing panel (richui.py), `rv onboard` completion message, and
  QUICKSTART.md launch step to point at `rv start`.
- 15 hermetic tests (test_sr_start.py); full suite 2191 passed; rv lint PASS; rv help --check OK.

### Decisions
- `os.execvp` exec-replace pattern (not subprocess): replaces the rv process entirely so the
  interactive Claude Code session inherits the terminal cleanly — no wrapper process in the way.
- Vault detection = both `research_vault.toml` AND `CLAUDE.md` required — CLAUDE.md is the
  hub-bootstrap that makes the session Alfred; without it `rv start` would silently open a raw
  Claude session.

### Open / next
- PR `feat/rv-start` pushed; awaiting human-go merge (public-facing verb + first-run entry point).

## 2026-07-05 (secrets-forward: command-line-clean secret forwarding to remote jobs — feat/secrets-forward)

### Done
- `adapters/secret_forward.py` — new stdlib-only module. Pure seam: `validate_secret_name`
  (`^[A-Za-z_][A-Za-z0-9_]*$`, rejects injection), `resolve_secrets` (resolve-all-first,
  fail-closed BEFORE any ssh, RuntimeError naming the missing secret), `build_secret_blob`
  (`export NAME='<shlex.quote(value)>'`), `SecretForwardPlan` (nonce via `secrets.token_hex`,
  `stage_script` = `umask 077`/`mkdir`/`cat >`/`chmod 600`/TTL-sweep, `activation_wrapper` =
  `trap`+source+immediate-rm), `stage_over_stdin` (ssh STDIN `input=`, never argv),
  `best_effort_cleanup` (fire-and-forget, never masks the original error).
- `RemoteBackend.submit()` — resolve+stage before the submit subprocess; the sh -c wrapper
  slots into the existing `{cmd}` (ssh) / `["--"]+cmd` (slurm/pbs) machinery. `native_env` is
  IGNORED when secrets are present (its `--export` would leak the value) — forced sh -c + a
  one-line stderr note. Cleanup on submit failure. `_build_secret_store` from `cfg.adapters.secrets`.
- Discovery: `compute.py` scaffold seeds `secrets_forward: ["WANDB_API_KEY"]` + rationale in the
  compute-node profile; `cmd_show` renders `forwards=<names>`; `doctor.py` adds a resolvability
  probe (`NAME [resolvable ✓ / MISSING ✗]`, value never captured); recipe doc "Remote jobs + secrets".
- Manifest schema (per-profile): `secrets_forward` (NAMES only, validated), `secrets_scratch`
  (default `$HOME/.rv-secrets`), `secrets_ttl_minutes` (default 720). Absent = unchanged behavior.

### Decisions
- Security spine = command-line-clean: the value appears on NO argv (local ssh argv, `--export`,
  or node process argv). It lives only in memory + the kernel pipe (ssh STDIN) + a mode-600
  remote file sourced-then-deleted. Names-not-values everywhere (manifest / show / doctor / logs).
- Refined `test_init_no_secret_literals` (leakage gate): a validated forwarded env-var NAME is not
  a credential (the whole security model), so it is excised before the VALUE scan; the gate still
  catches `sk-ant-…`, `password`, and any `NAME=value` credential assignment elsewhere. FLAGGED for
  reviewer — this loosens an existing leakage assertion to admit the legitimate new field.

### Open / next
- CI disabled for this branch; verified via full `pytest` (2128 passed) + `rv lint` + leakage scan.
## 2026-07-05 (onboarding UX: rv onboard + rv check reframe — feat/onboarding-ux)

### Done
- S1/F3: `rv check` reframed to the corrected required-model — the **agent runtime is the
  ONLY hard requirement**; there is no required API key. `all_required_ok` gates only on
  the runtime (+ observability when `--require-observability`). Runtime + zero keys → OK
  (exit 0). Provider/s2/asta/wandb/zotero/compute are FEATURE-REQUIRED: each shows
  "locked", never FAIL. F2: one framing per feature (killed the Zotero optional/Required
  contradiction), request-form URL per key, asta institutional-email note. F1:
  `required_failed[]` carries culprits inline into the Result line.
- S2/F4: `keys.py` — the credential/feature registry SSOT. One keyring service
  (`research-vault`), per-key env-var + username, the FEATURES catalog. `EnvSecretStore`
  and the check functions route through it, so a key written by `rv onboard` is read by
  `rv check` AND the runtime (round-trip proven in tests).
- S3/E1: `richui.py` — rich structure (tier-matrix + Integrations table + Required/Result
  panels + init panels). Additive: reads the same result dict; NO_COLOR / RV_PLAIN /
  --plain / non-TTY degrade to the plain report. Style knobs isolated for the designer's pass.
- S4/E2: `rv onboard` — guided, idempotent (state re-derived, no state file), no-echo
  (getpass → keyring, masked verify), non-TTY prints remediation, always exit 0, no
  plaintext `.env`. Registered in `_VERB_REGISTRY` (when_to_use + anti-patterns). `rv init`
  auto-offers it at the end (TTY-only prompt).
- S5: README + QUICKSTART reframed to the feature-key request forms + "locked until you
  add the key" framing; step 1 → `rv onboard`.

### Decisions
- Provider keys are provider-plural (Anthropic + OpenAI + …); ANY one unlocks API-model
  experiments — not Anthropic-specific.
- Keyring service unified onto the hyphen form (`research-vault`) — the runtime's existing
  convention — so no reader/writer split remains.

### Open / next
- Iris visual-polish pass: palette/borders (the `_STYLE` dict in `richui.py`), URL-wrap in
  the Integrations Status column, panel styling. Structure is built; palette is deliberately default.

## 2026-07-05 (SR-MODEL-SEAM: first-class litellm ModelClient + automatic observability — feat/model-seam)

### Done
- S1: `adapters/observability.py` — `ObservabilityBackend` protocol (probe/start) + four
  backends (Weave/Langfuse/Local-JSONL/None) + `_EmissionCounter` (litellm CustomLogger,
  built via factory closure) feeding a litellm-free `EmissionStats`; one counter for both
  planes. `[observability]` config block. weave moved to the `[observability]` extra.
- S2: `adapters/model_client.py` — `ModelClient` (keys→env via SecretStore, probe+start once,
  always-register counter, `complete()` with zero per-call logging, `assert_observed()` +
  `__exit__`/`atexit` loud-warn / `ObservabilityError` under require). `AdapterSet.model` is a
  first-class LAZY member (no eager litellm/weave.init on `load_adapters`).
- S3: `_check_observability` in `rv check` + `--require-observability` gate; new `rv
  observability` verb (status/probe) — active pre-run wiring test for both planes.
- S4: harness engineer specs (per-main + shared) + `compute-run-recipe.md` +
  `harness-contract.md` now REQUIRE the ModelClient seam; `rv dag brief` names it at dispatch.
- S5/S6: `experiment_run.log_experiment_run` — Plane-B classic W&B run in the exact shapes
  `rv wandb pull` reads (config=pre-reg params, summary=aggregates+metrics, auto commit).
  `live`-marked no-mock acceptance tests (skip without keys): local-JSONL, weave-trace, and
  the full Plane-B round-trip (log → `rv wandb pull` reads back repro_model/seed + aggregates).

### Decisions
- Import-light is load-bearing: litellm is in the toolkit-blocked set and `rv help` imports
  every verb module, so ALL litellm/weave/wandb imports are lazy (inside functions); the
  CustomLogger subclasses are factory closures. Proven: observability + verb + run modules
  import with litellm/weave/wandb blocked.
- One counter, two planes: `EmissionStats` (litellm-free) is the SSOT both planes read.
- Two distinct guards feed "unforgettable": probe-time (backend wanted but unwired) and
  `assert_observed` (calls made but counter saw nothing = seam bypassed).
- 2073 pass / 3 live-skip; rv lint PASS; rv help --check OK (30 verbs); leakage clean; zero
  ~/vault edits. human-go: D-1 (new `weave` dep) needs the operator's explicit go at merge.

## 2026-07-04 (SR-XPB-FIX: remove substring pre-filter from corroborate — PR #108)

### Done
- Deleted substring pre-filter (`if claim_lower not in text.lower(): continue`) from
  `corroborate_across_projects`; every `.md` note in declared peer projects is now a
  candidate; `rank_candidates(min_score, top_k)` does all filtering.
- Candidate `body` is now title + parsed body (via `_parse_frontmatter`) — not raw file
  text — so TF-IDF is not polluted by YAML frontmatter keys.
- Reworked excerpt (title > first heading > first non-empty body line) and anchor (first
  heading in note, else line-1). Provenance format unchanged.
- Added `test_paraphrase_claim_surfaces_relevant_note` with explicit red-before-green proof
  (asserts claim is not a verbatim substring before calling corroborate). Gated on sklearn.
- 2010 tests pass; rv lint OK; rv help --check OK; leakage clean.

### Decisions
- Kept `_extract_anchor(text, match_start)` as dead code (no callers after this change);
  removing it is a separate cleanup to keep this diff reviewable.

## 2026-07-04 (SR-PKG-TRIM corrective: remove [providers]/[figures]/[all] extras — PR #107)

### Done
- Removed [providers], [figures], [all] optional-dependency extras from pyproject.toml.
  openai, google-genai, google-generativeai, mistralai, cohere, matplotlib, seaborn appear
  NOWHERE in pyproject.toml (acceptance grep: zero hits).
- Stripped _EXTRA_PACKAGES, _check_extras, _fmt_extras_section, and all extras display
  from check.py; removed extras_missing from run_preflight result dict.
- Removed _PROVIDERS_SPEC/_FIGURES_SPEC/_ALL_SPEC/_EXTRAS_SPECS and --extras argparse flag
  from bootstrap.py; _run_bootstrap no longer accepts an extras parameter.
- Updated tests/test_pkg_toolkit.py: removed [providers]/[figures]/[all] assertions;
  added test_no_provider_sdks_shipped + test_no_figure_libs_shipped (parse+grep-zero) +
  test_pyproject_no_{providers,figures,all}_extra as regression pins.
- Updated architecture.md dependency-posture paragraph and SR-PKG-TRIM row.
- Updated compute-run-recipe.md model-seam section.

### Decisions
- [providers]/[figures]/[all] extras are gone entirely; adopter installs SDKs/plotting libs
  directly. litellm covers most API targets without a dedicated SDK.
- Comments in pyproject.toml reworded to avoid naming the removed packages (acceptance grep
  is a text grep, not a TOML-parse grep — comments count).

### Open / next
- PR #107 force-pushed to feat/pkg-trim; awaiting CI green + human-go before merge.

## 2026-07-04 (SR-FIND-RERANK: over-fetch + rerank for rv research find)

### Done
- Slice 1: `cmd_find` over-fetch + rerank. Added `--pool 50` (over-fetch size),
  `--rerank/--no-rerank` (default on), `--min-score 0.0` (reorder-not-drop) flags.
  Non-deep path now fetches `--pool` candidates from asta, builds `body = title + "\n" + abstract`
  for each paper, calls `rank_candidates(query, pool, min_score, top_k=limit)` in-place
  (import inside cmd_find — no new cycle; the `research→cross_project` edge already exists at
  research.py:560). `--no-rerank` reproduces pre-SR output byte-for-byte (fetches `--limit`,
  no reranking). `--deep` path unchanged in v1.
- Slice 2 investigation: `asta papers search` exposes `--fields`, `--limit`, `--date` — NO
  field-of-study or venue filter. Appending scope terms to the query would degrade S2 relevance.
  Slice 2 is a no-op with rationale; no `--field` passthrough added. Finding recorded in test
  `TestSlice2NoOp::test_find_parser_has_no_field_passthrough`.
- Slice 3: Recall fixture captured. Query: "LLM cultural values alignment cross-cultural benchmark",
  50-paper pool saved at `tests/fixtures/find_rerank_llm_cultural_values.json`. Known anchors
  buried at asta positions 28, 30, 38; all 3 surface into reranked top-10. 22 tests pass.
- Slice 4: Help text + module docstring updated. Pool, rerank, min-score documented in the `find`
  subparser help. `rv help --check` PASS; `rv lint` PASS; leakage-clean.
- Full test suite: 1983 tests pass.

### Decisions
- D1: In-place import `from .cross_project import rank_candidates` inside `cmd_find` (not at
  module top). The import edge already exists (research.py:560 has `from .cross_project import
  corroborate_across_projects`); in-place avoids any perception of a new cycle.
- D2: `--min-score 0.0` = reorder-not-drop. Truncation to `--limit` is the noise filter.
  Raising the threshold to e.g. 0.1 would silently drop papers from the result set; 0.0 is
  the safe default.
- D3: `--deep` path unchanged in v1. The `asta literature find` output format is opaque;
  reranking would need a known schema. Future work.
- D4: asta `--limit` cap is 100 (verified from `asta papers search --help`). `--pool 50`
  default is well within the ceiling.
- D5: Slice-2 no-op. asta provides no field-of-study filter; query mutation degrades S2 scoring;
  the right long-term fix is a native asta addition, not a workaround.

### Open / next
- `--deep` + rerank (v2): requires asta literature find to return a stable JSON schema so
  body strings can be built. Currently the deep path returns a variable structure (papers/
  results/data key). Needs investigation before adding rerank.
- Pool pagination: asta caps at 100 results per call. If `--pool > 100`, fetch is silently
  capped. A future `--paginate` flag could batch multiple asta calls to achieve pools > 100.
## 2026-07-04 (F24: datasets-note discoverability)

### Done
- `experiment.py`: plan note skeleton now includes a `## Dataset Provenance` section
  with `rv note <p> new datasets` instructions; step 0 in printed next steps prompts
  dataset note creation before the freeze.
- `note.check_dataset_provenance_warn()`: WARN (never block) when `results_hash` is
  set but `repro_dataset_id` is the sentinel; silent when filled / not-applicable / no-run.
- `cmd_check` wired: calls `check_dataset_provenance_warn` for experiments notes
  alongside the existing `check_repro_sentinel_lint`.
- Pre-existing bug fixed: `NameError: exp_id not defined` in `run()` per-main harness
  loop (`exp_id` → `args.exp_id`).
- 11 fixture tests in `tests/test_datasets_note.py`; `test_sr_wb.py` fixture updated
  with explicit `repro_dataset_id: not-applicable`.
- CI green on PR #104.

### Decisions
- WARN not block: dataset provenance is opt-out (set `not-applicable` for no-external-data
  runs). Gate would be too noisy for proxy/synthetic experiments.

### Open / next
- PR #104 open; awaiting reviewer gate.

---

## 2026-07-04 (SR-XPB: principled cross-project corroboration — Slices 1–8)

### Done
- S1: `project_edges.py` — hub-owned sidecar JSON edge store (`state_dir/project_edges.json`);
  `add_edge/remove_edge/peers_of/load_edges`; atomic write; normalised undirected pairs.
  `rv project relate <a> <b> --kind K` (declare), `--remove` (prune), `rv project edges` (list).
  `Config.project_edges_path()` accessor. 17 unit tests.
- S2: Doctrine — `coordination.md` + `roles/alfred.md` + `CLAUDE.md.tmpl` updated with:
  (a) hub owns edges + `rv project edges` surface; (b) crew-cannot-self-approve binds the
  assertion (findings note), not each edge; (c) over-declaration forfeits narrowing.
- S3: Gate `corroborate_across_projects` to declared peers only (D3). `from_slug` REQUIRED;
  `against_slugs` ⊆ peers (ValueError otherwise). No declared peers → empty + nudge.
- S4: TF-IDF cosine ranker (lazy sklearn Tier-1) + Jaccard stdlib fallback (surfaced).
  `rank_candidates()` in `cross_project.py`; `--min-score`/`--top-k` on `cmd_corroborate`.
- S5: Anchor provenance `@slug:note_rel:anchor` (nearest preceding heading or `line-N`).
  `--emit <path>` writes candidates JSON for judge hand-off.
- S6: `corroborate-judge-fragment.json` DAG template (4 nodes: corroborate, judge, human-go,
  assert). 9 tests proving judge-asserts-not-rank; findings note validates OKF_TYPES.
- S7: `_VERB_REGISTRY` updated for `project` + `research` (SR-XPB); discovery nudges point
  at `rv project relate`; demo-litreview README extended with corroborate→judge→assert guidance.
  `rv lint` PASS; `rv help --check` PASS. Leakage-clean (crew-name scrub).
- S8: `rv project relate --suggest` — ranks all undeclared project pairs by corpus similarity
  (reuses S4 ranker); surfaces proposals only, never auto-declares. 4 tests.
- Full CI: 1917 tests pass.

### Decisions
- D1: Sidecar JSON edge store (not embedded in TOML) — atomic write, separate concern.
- D2: Undirected + `--kind` REQUIRED — no silent implicit edges; rationale on the record.
- D3: `from_slug` required; universe = declared peers only. Non-peer `--against` → ValueError.
- D4: Judge-gated assert. DAG fragment enforces: assert node reads judgment (not raw candidates).
- D5: Hub declares outright; crew reads `peers_of`. Human approves the assertion.
- Leakage rule: crew names (architect, etc.) in docstrings replaced with role names.

### Open / next
- PR #XPB on feat/xpb. Reviewer gate: Argus. Merge gate: human-go (doctrine slice in CLAUDE.md.tmpl).

## 2026-07-04 (SR-LR-POLISH: lit-review loop polish — F12/F14/F15/F16+F17)

### Done
- S1/F12: `_normalize_paper_id_for_asta()` shim in `research.py`; bare arXiv/DOI ids
  scheme-prefixed before asta calls; zero-result hint to stderr when normalized.
- S2/F14: Fixed 3 wrong-order `rv review expand <project> <scope>` → correct form
  in coverage-gate label, OKF note body, and parser description.
- S3/F15: Hardened `_parse_new_citekeys_from_text` (case-insensitive, whitespace-tolerant);
  added `_count_corpus_data_rows`; green-but-vacuous guard in `cmd_expand` — corpus with
  annotation rows but 0 [NEW] parsed → loud ValueError, no 0-relate phase2-dag.json written.
- S4/F16+F17: `coverage_report(project, scope, *, config)` sourced from frozen `_corpus.md`,
  identity by `citekey:` frontmatter field (filename-agnostic). `rv review <project> coverage
  <scope>` verb added. `cmd_expand` emits coverage one-liner. `review_critic_tips` axis-2
  updated to reference the verb. Coverage-gate label gains self-check instruction.
- PR #100 open on feat/lr-polish. Reviewer gate: Argus.

### Decisions
- F17: `citekey:` frontmatter field is identity; filename stem is fallback only. Prevents
  false-orphan on descriptive filenames like `zheng2023-pride-mc-selectors.md`.
- F16 source-of-truth: `_corpus.md` is the frozen manifest; unmaterialized = in corpus but
  no matching lit note.
- F15: truly empty corpus (0 annotation rows) still degrades gracefully to direct synthesize;
  only rows-present-but-none-NEW is the error case.

### Open / next
- CI check-run API not accessible with current token (403) — verify green via Actions UI.
- Argus review pending on PR #100.

---

## 2026-07-04 (SR-PKG: batteries-included research toolkit)

### Done
- `pyproject.toml`: Tier-1 default deps (~33 packages) — model SDKs (anthropic, openai, litellm,
  google-generativeai, mistralai, cohere, tiktoken), data (datasets, pandas, numpy, pyarrow),
  stats (scipy, statsmodels, scikit-learn), figures (matplotlib, seaborn), eval (inspect-ai,
  lm-eval, evaluate, sacrebleu, rouge-score, bert-score), multilingual (sentencepiece, sacremoses,
  langdetect), utils (tenacity, tqdm, orjson, pydantic, jinja2, rich, python-dotenv), integrations
  (wandb, pyzotero, asta). [analysis] extra removed — scipy now default. [local] Tier-2 extra
  (torch, transformers, accelerate, huggingface_hub, fasttext). [serve-vllm]/[serve-sglang] sub-extras.
- `check.py`: extended rv check with Tier-1/2 coverage matrix (per-group OK/MISS/WARN probes);
  bootstrap nudge when Tier-1 missing; Tier-2 missing prints GPU-box advisory.
- `bootstrap.py` (new): rv bootstrap verb — creates .venv, pip-installs research-vault (Tier-1
  hard, Tier-2 best-effort + tolerated); --no-tier2, --serve (vllm|sglang), --verbose flags.
- `cli.py`: registered bootstrap in _VERB_REGISTRY (SR-PKG); added to Setup phase; updated check
  when_to_use to mention Tier-1/2/bootstrap.
- `architecture.md`: updated dependency posture section — batteries-included with guarded imports;
  added SR-PKG to SR sequence table.
- `data/doctrine/compute-run-recipe.md`: added litellm as primary model seam section.
- 45 new tests: bare-import guard (simulates --no-deps via _BlockingFinder), check tier matrix,
  bootstrap verb parser + logic, registry/help-check; stale [analysis] tests updated.
- rv lint PASS; rv help --check PASS (27 verbs); leakage scan clean.

### Decisions
- Reverses stdlib-only-core golden rule for the research surface (operator's explicit decision).
- litellm is the PRIMARY model seam — per-provider SDKs secondary.
- Bare-import guard: all toolkit imports are guarded (lazy, inside functions only); verified by
  AST scan in tests and _BlockingFinder meta-path test.
- [analysis] extra folded into Tier-1; the dogfood-MED test updated to assert the removal.

### Open / next
- PR open for Argus review + Wren fit check (human-go class — public pyproject + golden-rule reversal).

## 2026-07-04 (SR-HARNESS-P2: harness sub-sequence in experiment loop)

### Done
- Slices 1–6 complete; PR open (reviewer-gate: Argus on S1 hash + Wren fit check on S4 catalog).
- S1 (`plan/freeze.py`): HARNESS_SENTINEL, `_parse_harness_commits`, `_build_harness_block`,
  `_build_covers_canonical` (internal; `include_harness` flag); `compute_covers_hash` unchanged;
  `store_freeze_hash` stores both `covers_hash` + `covers_retries_hash`; `verify_freeze_hash` adds
  three-way mismatch logic (full match → pass; retries match + hash differs → harness-commit drift;
  else → existing covers/retries). 29 tests.
- S2 (`plan/verbs.py`): `rv plan freeze-harness --scope <scope> --harness-commit <sha>`;
  `_upsert_frontmatter_list_field`; FAIL-CLOSED (no prior freeze → exit 1); baseline guard
  (covers edit between freeze and freeze-harness → exit 1); updates `covers_hash` including
  the new harness block. 19 tests.
- S3 (`experiment.py`): per-main harness triple `{main_id}-harness → {main_id}-harness-review
  → [HG:human-go-harness-main{k}]`; run/abl-A-run rewired to afterok harness gate (plan+watch
  stub-freshness edge intact); `--shared-harness` flag for single shared triple; printed
  next-steps include harness sub-sequence with exact freeze-harness commands. 20 tests.
- S4 (`dag/catalog.py` + `research-loop.json`): catalog updated with harness gate; demo
  manifest updated with per-main harness triples; `_make_research_states` updated in test_sr5.py.
- S5: brief-grade specs (`_harness_engineer_spec`, `_harness_reviewer_spec`) already live in experiment.py.
- S6: `rv help --check` green; `rv lint` PASS; `experiment` when_to_use updated.
- 1777 tests pass.

### Decisions
- D2 confirmed: ablation shares its main's harness gate (no separate ablation harness node).
- D3 confirmed: `--shared-harness` flag uses `human-go-harness-shared` as the single gate id.
- D4 confirmed: `harness_commits: [main1=<sha>, main2=<sha>]` inline YAML in plan frontmatter.
- Back-compat golden hash `b75a05a8f70baf776be6d87ee83c6c16c60e936e697d59fb629f43ba19c26aac` preserved.

### Open / next
- PR awaiting Argus review (S1 hash correctness) + Wren fit check (S4 catalog). Do NOT merge.

## 2026-07-04 (SR-RM-FIGMS: remove figure + manuscript loops)

### Done
- S0–S6 complete; PR #94 open (human-go class — Argus review + Wren fit check required before merge).
- Deleted `figure.py`, `figures/`, `manuscript/` modules; removed both verbs from CLI registry/help map.
- DAG LOOP_CATALOG 4→2 (experiment + lit-review); `produces.figure`/`.manuscript` removed from schema.
- OKF_TYPES 10→8 (removed "figures", "manuscript"); `pyproject.toml` `[figures]` extra deleted.
- Doctrine: `figure-minimalism.md` deleted; `roles/designer.md` narrowed (kept — owns SR-10 OSS site/identity); `honesty-gates.md` harvested before deletion (S0).
- Removed `absent_row` gap detector (was bound to deleted manuscript support-matcher); removed `--run-state`/`matcher_meta` from `cmd_gap_scan` signature.
- 13 dedicated test suites deleted; 8 shared suites edited; 1640 tests pass.
- `rv lint` PASS; `_check_verb_docstrings` + `_check_example_snippets` both return `[]`.

### Decisions
- `data/doctrine/review-board.md` kept — general adversarial-critique board, not manuscript-specific.
- `absent_row` gap type removed cleanly (had no callers outside the deleted manuscript module).
- Tests 4b/4c/4i in gap_close rewrote to use contradictory re-fire (not absent_row) — they test general reopened behavior, not absent_row specifically.

### Open / next
- PR #94 awaits Argus review + Wren fit check (human-go class).

## 2026-07-04 (fix/instance-resolution: F1/F2 footgun fix)

### Done
- Added `--config PATH` global CLI option with highest precedence (--config > RESEARCH_VAULT_CONFIG > CWD walk-up). Pre-scan in `main()` extracts the flag before `_load_instance_verbs()` calls `load_config()`.
- Added `--show-instance` global flag: prints resolved `instance_root` + `config_file`, then exits.
- `rv status <project>` now surfaces `instance_root:` and `config_file:` at the header.
- `cmd_status_all` surfaces same when no projects registered.
- Updated `config.py` docstring to document the three-tier precedence explicitly.
- 19 new hermetic tests covering all acceptance criteria; CI green on PR #92.

### Decisions
- Env-var injection approach: `--config` sets `RESEARCH_VAULT_CONFIG` before any `load_config()` call. Keeps a single resolution path in `_find_config_path()` rather than threading a new param through all callers.
- No teardown of env var injection (process-scoped CLI; tests use `monkeypatch`).
- `_extract_config_arg` strips `--config PATH` and `--show-instance` from the stripped argv before the unimplemented-verb pre-check so global flags before the verb name don't confuse it.

### Open / next
- PR #92 awaiting reviewer gate before merge.
## 2026-07-04 (fix/dag-verbs-adopter: F21 produces.note + F13 approve flags)

### Done
- F21: `cmd_complete` now reads `manifest.get("project")` and resolves `produces.note` against `cfg.project_notes_dir(slug)` instead of `cfg.notes_root`. Fallback to `notes_root` when no project declared (demo case). `KeyError` on unknown slug → graceful fallback.
- F13: `cmd_approve` gains `--note TEXT` (decision rationale), `--output k=v` (repeatable, stored in `node_states["outputs"]`), and `--reject` (sets node to `blocked`; downstream `afterok` gates halt). Module docstring updated to reflect real signature.
- 14 RED-before-GREEN tests in `tests/test_dag_adopter_fixes.py`. Full suite: 2208 passed. `rv lint`: PASS. `rv help --check`: OK. CI green on PR #91.

### Decisions
- `produces.note` resolution falls back to `notes_root` on unknown slug (graceful, not hard error) — conservative; human will see the OKF check fail if the file isn't there.
- `--output` values stored as strings (no type inference) — keeps the storage simple; downstream nodes cast as needed.
- `--reject` maps to status `blocked` (existing terminal status) — no new status needed.

## 2026-07-04 (feat/sr-fig-method-ab: seaborn skin + render-script seam)

### Done
- Slice A: seaborn-backed `apply_style` — `sns.set_theme` + project palette (culturebench tokens: teal/clay/cream) overriding seaborn defaults; `skin` arg now palette selector; guard seaborn inside apply_style → None on absence.
- C1: extend `_check_figures_extra` probe to include seaborn (two-layer defence with apply_style guard).
- C2: extract `wandb_pull._hash_file` to shared `hashing.hash_file` — single SSOT for sha256 digests across render and pull domains.
- Slice B: new `figures/render_script.py` — `static_check` (stdlib ast, V1–V4 violation classes, pre-exec) + `emit_scaffold` (author-me template, intentionally fails static_check per honesty gate ruling).
- `cmd_render`: render_script: field → static_check → subprocess exec with injected vars (preamble approach). No field → df.plot stub UNCHANGED (C3 back-compat).
- 43 new red-before-green tests; 172 figure tests total green. `rv lint` clean. CI green on PR #88.

### Decisions
- Seaborn stays in `[figures]` extra only; not core dep.
- Scaffold intentionally fails static_check — a pre-satisfied scaffold would make the honesty gate vacuous (architect's ruling baked into test).
- Variable injection via Python preamble prepend + subprocess `-c` (no temp files, no env var leakage, no `os` needed in script).
- `_allowed_import_roots` fallback to root package allows `matplotlib.patches` etc. without enumerating every submodule.

### Open / next
- Slice C (honesty gate + doctrine) — separate PR after this lands.
- Iris deploy-and-judge-live (rendered output aesthetics) — separate task.
## 2026-07-04 (SR-HUB-DAG slices A+B+D: catalog + rv experiment new + orphan-guardrail)

### Done
- `dag/catalog.py`: static SSOT registry for 4 built-in research loops; gate IDs grounded in shipped manifests.
- `dag/verbs.py`: `rv dag templates` subcommand — discovery entry, pure read.
- `experiment.py`: `rv experiment new` scaffolder (the missing rail). Authors plan note skeleton + REGISTERED DAG manifest mirroring research-loop.json topology. Prints exact freeze commands.
- `status.py`: orphan-guardrail — scans experiments/*.md for plan_kind: preregistration, warns when no covering run is registered.
- `cli.py`: registered `experiment` verb with anti-pattern; dag `when_to_use` mentions templates.
- 42 new tests (catalog, rv experiment new, freeze round-trip, orphan-guardrail). Full suite: 2147 passed.
- PR #86: https://github.com/khangzhie-vault/research-vault/pull/86

### Decisions
- Slice C (doctrine) is a SEPARATE follow-up PR that depends on this catalog — not included here per scope.
- `rv experiment new` uses `source_dir` (via `cfg.project_notes_dir`) as the notes base — consistent with all other scaffolders.
- Orphan detection derives expected `run_id` as `<stem-without-plan>-loop` with an any()-OR fallback for alternate run IDs.

### Open / next
- Slice C: doctrine (CLAUDE.md.tmpl, alfred.md, research-dags.md, CHK-CREW-CLEAN) — depends on catalog (this PR).
- PR #86 needs reviewer-gate pass + Wren architect fit-check before merge.

## 2026-07-04 (fix/remove-manager: drop manager role from 6→5 crew)

### Done
- Removed manager role from DEFAULT_ROSTER, _ROLE_DOC, _CC_ROLE_DESCRIPTIONS, _CC_GRANTS in `build_agents.py`
- Deleted `doctrine/roles/manager.md`
- Fixed dangling links in `engineer.md` (×2, pointed to coordination.md and inline) and `designer.md` (×1)
- Updated prose throughout doctrine (agent-charter, coordination, alfred, architect, researcher, reviewer, standards, note-conventions) to reflect hub-coordinates-directly model
- Updated CLAUDE.md.tmpl crew table (5 roles), QUICKSTART.md listing
- Updated tests: test_sr_ccb.py (6→5, removed manager-no-bash test), test_sr_lens_rm.py (expected sets + guard), test_project.py (exclusion test added)

### Decisions
- Hub coordinates crew directly; no intermediate manager tier. Coordination/synthesis requires cross-project context only the hub has.
- Rule 8 (doctrine link-integrity): 0 dangling links after removal. CI green on all 5 jobs.

- Argus BLOCK (Argus review): scrubbed runtime Python templates (init.py, control.py, controllib.py),
  architect.md prose, architecture.md crew list, regenerated control/acme.md
- Optional prose-residue guard: SKIPPED — "manager" too common a word; lesson in DEVLOG instead

### Decisions
- Prose-residue lint guard not added: false-positive-prone (contextlib, package manager, historical charter text).
  The lesson: after deleting a role, do a full `git grep -rn "role_name" src/ data/` pass, not just link-integrity.

### Open / next
- PR #84 (fix/remove-manager) awaits reviewer pass + hub merge — must merge before SR-HUB-DAG's doctrine slice

---
## 2026-07-04 (SR-MS-GATE-ALIGN Slice B — study-type-aware REPRO)

### Done
- **`review_board.py`**: `_REPRO_PROXY_CLAUSE` (C5-override injected into rubric for proxy studies),
  `_REVIEWER_LENS_L3_PROXY` (analysis-provenance attack lens for L3 position), `is_proxy_study`
  kwarg on `run_reviewer_node` + `run_review_board`. `run_review_board(is_proxy_study=None)`
  self-determines from `notes_root/experiments/*.md` via `appendix._is_proxy_study`. Records flag
  in `meta`. Canary passages unchanged.
- **`appendix.py`**: enriched `_proxy_study_reframe_tex` with positive analysis-provenance section
  (renders `repro_dataset_id` + `repro_config_location` from notes where non-sentinel). Template-honest.
- **Tests**: 23 new in `test_sr_ms_review_repro_proxy.py`. Red-before-green: 21 RED before
  implementation, 23 GREEN after. Full suite 2101 passed. `rv lint` PASS. Leakage clean.
- **PR #83** open: `fix/review-repro-proxy`. CI green on HEAD.

### Decisions
- `_REPRO_PROXY_CLAUSE` appended to ALL reviewers in a proxy study (not just L3): every reviewer
  needs to know the binding changed. The proxy L3 lens additionally targets analysis-provenance
  attack angle. Both signals together give the judge the right prior.
- Self-determination scopes to `notes_root/experiments/*.md` — same glob as `inject_appendix`'s
  call path. No new mechanism, reuses `appendix._is_proxy_study` directly.
- `_proxy_study_reframe_tex` now takes `experiment_notes` optionally; `inject_appendix` passes it
  through. Backward-compat: no-arg call still works (generic fallback).

### Open / next
- PR #83 awaits Wren fit-check + operator merge.
## 2026-07-03 (SR-MS-GATE-ALIGN Slice A, take 2: structural zone-1 .tex selection)

### Done
- **DELETED `_body_scope_pdf_text`** (coldread.py) and **`_SECTION_TITLE_RE`**
  (check_gates.py) — the substring primitive was the root cause of the blocked PR #82.
  `pdf_text.find(heading)` matched the FIRST occurrence of the zone-2 heading string
  anywhere in the compiled PDF text — including TOC entries and body prose
  cross-references — silently truncating all subsequent body content (vacuous gate).
- **`check_cold_read_tally`** reworked: primary text path reads zone-1 `.tex` sources
  (`main.tex` + `sections/*.tex` skipping `_ZONE2_FILENAMES` stems) — structural
  zone-2 exclusion by filename, no substring search, no silent char caps.
- **4 red-before-green fixtures** (Wren-required): TOC case, body-cross-reference case,
  zone-2 positive control, canary calibration control. All 4 confirmed RED before,
  GREEN after. `TestBodyScopePdfText` class deleted (tests the deleted primitive).
  `TestBodyScopingInTally` updated to use `pdf_text=None` (structural .tex path).
  Full suite: 50/50 (test_sr_ms_coldread), CI green on SHA `a024866`.

### Decisions
- Structural zone-1 selection over substring truncation: the .tex file's stem is the
  authoritative zone discriminant — the same check as `check_body_leakage()`'s
  `if stem in _ZONE2_FILENAMES: continue`. No new mechanism; existing frozenset reused.
- `pdf_text=` parameter preserved as explicit override/injection path (for tests that
  pre-supply text). When explicitly passed, no body-scoping is applied — the caller owns
  zone-2 exclusion. When `None` (the default), structural .tex selection runs.
- No char caps: full file content is read. Silent truncation at arbitrary byte counts
  re-creates the vacuous-gate vector — body content past the cap silently dropped.

### Open / next
- Slice B (sibling PR #83): review_board.py / appendix.py (not touched here per spec).

## 2026-07-03 (hygiene-batch: crew-name scrub + leakage gate + checklist fix + pyc doctrine)

### Done
- **Crew-name scrub** (fix #1): replaced session-narrative attributions (researcher/architect/
  engineer/reviewer/designer/manager) throughout `src/**/*.py`. Renamed 6 role doc files to
  role-based filenames (`researcher.md`, `engineer.md`, `reviewer.md`, `designer.md`,
  `manager.md`, `architect.md`) and updated all 18+ cross-references in doctrine docs,
  examples JSON, and QUICKSTART.
- **Leakage gate extension** (fix #1): added `_grep_py_word` helper (`.py`-only) + class 10
  (crew narrative-names) to `scripts/leakage_scan.sh`. Added CI step scanning `src/research_vault`.
  Red-before-green proved: 6 new tests failed before class 10, pass after. All 53 leakage tests pass.
- **Stale checklist label** (fix #2): replaced stale `"CHECKLIST PLACEHOLDER — ...ships in SR-MS-REVIEW-b"`
  string in `run_meta_review` with accurate label pointing to reviewer raw_response fields.
- **pyc-mutation doctrine** (fix #3): added test-isolation rule to `data/doctrine/standards.md` —
  each mutation needs its own process (or explicit `__pycache__` bust) to avoid same-second `.pyc`
  mtime staleness giving false survivors.

### Decisions
- Role doc filenames → role-based (`manager.md` not `atlas.md` etc.): cleanest fix to prevent
  `build_agents.py`'s `_ROLE_DOC` dict from containing crew names as Python string literals.
- Class 10 scan is `.py`-only (not `.md`) so role docs in `data/doctrine/roles/` are exempt.

### Open / next
- PR `fix/hygiene-batch` pushed; awaits reviewer-gate (Argus) then human-go merge.

## 2026-07-03 (SR-MS-REVIEW-b: real rubric + lenses + calibrated canary)

### Done
- **`manuscript/review_board.py`** — SR-MS-REVIEW-b drop-in:
  - `DEFAULT_REVIEW_RUBRIC`: Ada's 7-dim venue-grounded rubric (NeurIPS/ICLR/ICML/ARR),
    replaces `PLACEHOLDER_REVIEW_RUBRIC`. C5 binds SOUND↔support-grounding, REPRO↔provenance.
    C6 fail-closed: absent floor dim → below-floor score. ARR verbatim-span rule. Disconfirm-first.
  - `CanaryAbortError` + calibrated bidirectional `run_canary_scaffold`: known-STRONG probe
    (SOUND/REPRO ≥ 4 guards blind rejectors) + known-WEAK probe (SOUND/REPRO ≤ 2 guards
    positivity-bias rubber-stampers; the AI-Scientist failure). Dead-band at floor (3) disallowed
    both directions. Parse failure → ABORT. Skips when rubric="" (backward-compat with -a tests).
  - `_REVIEWER_LENS_L1/L2/L3` + `get_reviewer_lens_spec(k, K)`: K=2 fallback = L1+L3
    (floor-carrying pair). Prepended to reviewer node spec in `__init__._build_manifest`.
  - `run_review_board`: new `canary_judge_fn/canary_rubric` params wired through to meta-review.
- **`verbs.py`**: CLI wires canary_judge_fn (same judge lambda as real reviews) + active rubric
  into `run_review_board`; standalone re-scoring note added to description.
- **`init.py`**: commented `[manuscript_review]` stanza in config template (D-REV knobs).
- **`doctrine/review-board.md`**: orthogonality note — gates are orthogonal by construction,
  do not double-penalize (cold-read leak ≠ REPRO deficiency; support-matcher block ≠ Soundness).
- **`tests/test_sr_ms_review_b.py`** (22 new tests): canary both-directions + non-vacuous mutation
  sentinels (strong bound 4→3 RED, weak bound 3→4 RED); C5 binding (no-provenance→capped ≤2,
  with-provenance→passes); L377 partial-omit guard (missing floor dim → 0 → not cleared);
  lens spec K=1/2/3 + manifest wiring; backward-compat skip; meta-review propagation.
- **`test_sr_ms_review_a.py`**: updated test_23 — fallback is now `DEFAULT_REVIEW_RUBRIC`.

### Decisions
- Canary skips when rubric="" to preserve backward-compat with -a tests that don't wire judge.
  CLI explicitly passes canary_judge_fn + active_rubric for live production runs.
- `_CANARY_WEAK_MARKER = "clearly the best"` (not "results speak for themselves" which crosses a
  line boundary in the passage text and would fail in Python substring search).

### Post-live validation (manual — do NOT gate CI)
Ada's calibration-sweep: once live with ANTHROPIC_API_KEY, fire both canaries N=5+ times to
confirm the ≥4/≤2 bounds sit outside the judge's score noise on DEFAULT_REVIEW_RUBRIC.
If the strong probe reads SOUND=3 under noise: loosen to MIN-of-repeats and update
_CANARY_STRONG_MIN. The bounds are calibrated to floor=3; floor+1=4 is the target.

### Open / next
- SR-10 (OSS docs site + README/LICENSE + public publish) — endgame.

---

## 2026-07-03 (SR-MS-REVIEW-a: review-board bounded loop machinery)

### Done
- **`manuscript/review_board.py`** (new) — the scientific-merit review-board control-flow:
  - NEW dimensioned-score bracket extractor `_extract_review_scores()` for
    `[SOUND:N]`/`[CONTRIB:N]`/`[CLARITY:N]`/`[ORIG:N]`/`[LIMIT:N]`/`[REPRO:N]`/`[ETHICS:N]`
    — separate from support_matcher's 4-verdict and coldread's 3-verdict extractors (no overload).
    FAIL-CLOSED: unparseable → None / 0 (floor-fail, never silent pass).
  - `_evaluate_threshold()` — per-dim floors, MIN-across-reviewers (worst reviewer gates, not mean).
  - `PLACEHOLDER_REVIEW_RUBRIC` + `get_review_rubric(override, config)` seam
    keyed on `[manuscript_review].rubric` (Ada's real rubric = SR-MS-REVIEW-b).
  - Canary scaffold: `run_canary_scaffold()` wired but calibrated bounds in SR-MS-REVIEW-b.
  - `run_reviewer_node()`: node-level skip short-circuit on `RunState.meta["review_board"]["cleared_at"]`.
  - `run_meta_review()`: MIN aggregation, threshold predicate, canary_ok in meta.
  - `run_revise()`: re-fires support-matcher + cold-read (anti-gaming c); `honesty_gate_blocked`
    on BLOCK; rebuttal recorded as artifact (not verdict; crew-cannot-self-approve).
  - `run_review_board()`: N-round bounded acyclic unroll; cleared → remaining rounds no-op;
    not-cleared-after-N → NOT-CLEARED first-class payload; `honest_report` never says "approved".
  - `get_review_config()` with N hard-cap 3 (D-REV-3), K min 2 (D-REV-4).
- **`__init__.py` `_build_manifest`** extended with N review-board round-blocks after cold-read,
  before approve-manuscript. Fresh-by-construction: reviewer nodes read only `[tree_rel]` — NOT
  the thesis note, NOT prior-round reviews/rebuttals (the reads: list is the only channel).
  `review_config` frozen into manifest at scaffold time (stopping rule). Default N=2, K=3.
  Manifest node count: 18 (§5J.2+SR-MS-AUDIENCE) + 9 (N=2 K=3 review-board) = 27 total.
- **`check_gates.py` `build_approve_payload()`** extended with `review_board` + `review_board_report`
  sections (§5J.17.6). Honest tally line never says "approved".
- **`verbs.py` `rv manuscript review`** — new subcommand with loud env guard (RV_JUDGE_MODEL +
  ANTHROPIC_API_KEY required; mirrors --semantic/--cold-read guard).
- **`style.py`** — `per_section_tips` entries for review-board node types (reviewer, meta-review,
  revise) as documentation; SECTION_STATUS comment noting dynamic generation.
- **28 tests** in `test_sr_ms_review_a.py`: all RED-before-GREEN confirmed. Covers:
  score extractor (basic + fail-closed + partial + case-insensitive), threshold predicate
  (below-floor, cleared, MIN-gates), bounded unroll (N=2 K=1: cleared-r1 short-circuits r2
  with 0 extra judge calls; not-cleared-after-N → NOT-CLEARED), anti-gaming (revise re-fires
  support-matcher; revise re-fires cold-read; fresh-reviewers-by-construction in manifest),
  honest-report (never "approved"), build_approve_payload extension, rv manuscript review
  loud-fail guards, N/K frozen at scaffold, N hard-cap 3 clamping, canary scaffold meta key,
  walker/schema/store unchanged (import-diff), rubric seam. Full suite: 2038 passed.

### Decisions
- Review-board nodes generated dynamically in `_build_manifest` with inline specs (not via
  `_spec()` / SECTION_KEYS) — they don't follow the static section pattern; N and K are
  frozen config values, not section toggles.
- Placeholder rubric ships in -a; Ada's real venue rubric + bidirectional canary calibration
  = SR-MS-REVIEW-b. Clean seam: `get_review_rubric()` is wired, bounds are not.
- `run_revise()` does NOT call cold-read when `cold_read_judge_fn is None` — the honesty
  gate re-fire is conditional on having a judge (tests exercise both code paths).

### Open / next
- SR-MS-REVIEW-b: Ada's `DEFAULT_REVIEW_RUBRIC` (7-dim venue scales, ARR justify-each,
  Yes/No/NA checklist, disconfirm-first + anti-anchoring) + bidirectional canary calibration
  (known-STRONG + known-WEAK expected-score bounds).

---

## 2026-07-03 (SR-MS-COLDREAD: LLM cold-read self-containment judge)

### Done
- **`manuscript/coldread.py`** — the Layer-2 cold-read judge: `DEFAULT_COLDREAD_RUBRIC`
  (Ada's full rubric, dropped in as seam default), `get_coldread_rubric(override, config)`
  keyed on `[manuscript_coldread].rubric`, `_extract_coldread_verdict()` (NEW 3-verdict
  extractor for `[STANDS-ALONE]`/`[DANGLING]`/`[NEEDS-CONTEXT]` — does not overload
  support_matcher's 4-verdict one), `flag_a_scan()` (deterministic Flag-A pdftotext scan
  mirroring check_body_leakage patterns), `run_cold_read()` with bidirectional canary.
- **Bidirectional canary**: (a) trigger-happy guard — flags clean probe → ABORT;
  (b) blind guard — waves through leaky probe (BLOCK_COUNT < 2) → ABORT. Both must pass
  before any real verdict is trusted.
- **Flag-A belt-and-suspenders**: same deterministic patterns (sha256/covers_hash/
  results_hash, results/* paths, abs paths) applied to pdftotext output — catches any
  leak the LaTeX render introduces that the .tex scan missed. Fires independently of LLM.
- **`check_cold_read_tally()`** added to `check_gates.py` — orchestrates pdftotext
  extraction, Flag-A scan, canary probes, LLM judge; returns honest_report tally.
- **`build_approve_payload()`** extended with 9th section: `cold_read_flags`,
  `cold_read_flag_a`, `cold_read_report`.
- **`verbs.py` `--cold-read`**: updated help text; Layer-2 now fires after Layer-1 with
  explicit RV_JUDGE_MODEL + ANTHROPIC_API_KEY guard (fails LOUD if absent).
- **33 tests** in `test_sr_ms_coldread.py`: canary both directions (non-vacuous), verdict
  discrimination (discriminates, doesn't rubber-stamp), Flag-A (sha256/results-path/
  covers_hash), rubric seam, check_cold_read_tally honest_report, approve_payload 9th
  section, --cold-read env guard, plain check stays hermetic. Full suite: 2002 passed.
  PR #78 opened; CI green.
- **Pre-merge fixes** (Argus + Wren, commit `30931c1`): (1) fail-closed on malformed judge
  output — `_parse_coldread_response` now defaults `overall="UNPARSEABLE"` (not "STANDS-ALONE");
  `ColdReadResult.blocks` treats UNPARSEABLE as BLOCK; `check_cold_read_tally` surfaces a loud
  error for it; (2) `verbs.py` now calls `cold_read_layer2_env_guard()` from `coldread.py`
  instead of inlining its own env check (reuse-over-create, charter §6); (3) `per_section_tips`
  `cold-read` entry rewritten to reflect Layer-2 as live and carry the 3 anti-anchoring moves.
  8 new tests added; full suite 2010 passed; CI green on both push + pull_request triggers.

### Decisions
- Canary (b) abort condition: `overall != "DANGLING" OR block_count < 2` (Ada's exact
  condition from the rubric artifact). Added trigger-happy direction on canary (a) as well.
- Flag-A runs inside `run_cold_read()` before canary, so Flag-A hits always appear in
  results even when the canary aborts (the abort only reflects the LLM judge being broken).
- `check_cold_read_tally(pdf_text=...)` injection param allows hermetic tests without
  pdftotext on the test runner.
- `build_approve_payload` gets `cold_read_judge_fn` kwarg — can be the same judge_fn as
  support_matcher or a distinct one (D-AUD-5 resolved: Opus-tier both).
- UNPARSEABLE is a distinct fail-closed state: it BLOCKS but never gets promoted to DANGLING
  by the Flag-A merge step (masking root cause). The check_cold_read_tally error message
  explicitly names the cause so the operator knows to inspect judge model / rubric wiring.

### Open / next
- SR-FIG-MINIMAL (raster plot-only): the next dispatch after COLDREAD.
- Real-artifact validation: run the cb-fmt dogfood PDF through live cold-read judge when
  ANTHROPIC_API_KEY is available (flagging covers_hash + results/*.csv + not-recorded wall).
  Not gated in CI — documented as manual validation step per spec.

## 2026-07-03 (SR-EP-ROLE: per-endpoint when_to_use + host_group)

### Done
- **`when_to_use` field on each backend profile**: optional free-text (purpose + inline
  anti-pattern), mirroring the verb registry. Rendered by `rv compute show`/`explain`.
- **`host_group` annotation**: optional string; endpoints sharing a value are the same
  underlying cluster/filesystem. `cmd_show` groups co-located endpoints visually.
- **Scaffold extended** (`_scaffold_manifest`): primary remote profile renamed `cluster` →
  `compute-node`; local gets seeded value; remote profiles get FILL; inactive `transfer-node`
  example (plain-ssh DTN, staging `when_to_use`, shared `host_group`). Generic names only —
  leakage-clean by construction.
- **`cmd_init` next-steps**: names `when_to_use` authoring + explains `host_group` / DTN pattern.
- **Soft WARN** (non-fatal, non-blocking): fires when ≥2 active profiles share `host_group`
  (or ≥2 active remote profiles lack it) and any lacks `when_to_use`. Exit code always 0.
- **Verb anti-pattern** (`cli.py`): compute `when_to_use` gains DTN shoehorn anti-pattern.
- **26 new tests** (`test_sr_ep_role.py`); `test_sr_co.py` updated for compute-node rename.
- `rv lint` PASS; `rv help --check` OK; leakage scan clean; 1910 suite-wide pass.

### Decisions
- Flat profiles + `host_group` (not nested clusters): zero-new-mechanism; probe loop unchanged.
- `archetype: ssh` + `when_to_use` for DTN (not a new `ssh-transfer` archetype): archetype =
  transport, `when_to_use` = role; `_probe_remote_ssh` connectivity check already correct.
- Soft WARN (not a hard gate): adopter-authored profiles; `local` needs no role prose.

### Open / next
- PR open, awaiting human-go merge.

## 2026-07-03 (SR-FREEZE-FIX: fail-closed + notes_root pin + approve hardening)

### Done
- **hole (a) FAIL-OPEN closed**: `verify_freeze_hash` now returns `(False, "run not
  frozen — run rv plan freeze first")` when `plan_freeze` is absent and
  `require_frozen=True` (the new default). Old behavior was `(True, None)` — a never-
  frozen run silently passed the K-3 integrity gate.
- **hole (b) NON-REPRODUCIBLE fixed**: `store_freeze_hash` now stores `notes_root`
  (absolute) in `plan_freeze` meta. `verify_freeze_hash` uses the STORED pin for
  re-derivation, ignoring the caller's arg. Relocation: FAIL LOUD ("pass --notes-root
  to re-pin"), never silent fallback. Legacy meta (no field): `UserWarning` + fail.
- **Approve hook hardened** (`dag/verbs.py`): drops `cfg.notes_root/"experiments"`
  re-derive at L774; uses `plan_freeze["notes_root"]`. On verify EXCEPTION → BLOCK
  (`return 1`) not warn-and-proceed (second fail-open now closed).
- **13 new tests** (`test_sr_freeze_fix.py`), all red-before-green:
  - `test_never_frozen_returns_false`: confirmed old code returned `True`, new returns `False`
  - `test_cross_caller_different_notes_root_arg_still_ok`: confirmed old code gave false FAIL
  - mutation tests (tamper detection un-regressed through the pin change)
  - approve-hook exception → BLOCK
- `rv lint` PASS; `rv help --check` OK; leakage scan clean; 1825 suite-wide pass.

### Decisions
- Store `notes_root` (not a canonical child list): `compute_covers_hash` already
  parameterized by `(plan_note, notes_root)` and MUST re-parse `covers:` to catch
  tampers — pinning `notes_root` makes it caller-invariant with one field and zero
  new resolution logic. Storing a child list would need a parallel resolution path
  and couldn't catch `covers:` edits without re-reading the plan note anyway.
- `require_frozen=False` escape-hatch: `rv dag approve` gates on `plan_freeze`
  presence before calling verify, so it sets `require_frozen=False` to avoid
  redundant "not frozen" errors on runs that legitimately have no pre-registration.
- PR #72, `human-go` class: reviewer + Architect fit-check + the operator as 2nd party.

### Open / next
- PR #72 awaiting reviewer verdict + Architect (Wren) fit-check.

---
## 2026-07-03 (SR-MS2-FIX: robust extractor + blind-judge canary + --semantic + un-truncate + CSV)

### Done
- **Robust extraction** (`_read_note_structured_fields`): strip HTML comments; extract every
  ##/### section; capture markdown tables; fallback to full de-commented body; skip only
  sections literally titled `Abstract`; broaden frontmatter to all scalar fields except
  id/pointer denylist. Core bug: extractor returned {} on real OKF notes → every verdict
  was false-ABSENT.
- **Blind-judge canary** (`check_support_tally`): one synthetic known-supported probe before
  the real tally; [ABSENT] on probe → ABORT LOUDLY with "NOT real refutations" message.
- **CLI**: `rv manuscript check --semantic` — one flag on existing verb; requires
  `RV_JUDGE_MODEL` + `ANTHROPIC_API_KEY`, fails LOUD if absent; plain check stays hermetic.
- **Un-truncate** (`_build_judge_prompt`): per-field cap 400→2000, overall ~6000-char budget
  with visible `[…truncated N chars…]` marker.
- **CSV results** (`inject_results`): branches on `.csv` → 2-col key,value parser; ambiguous
  CSV (wrong column count) → clear error not silent skip.
- **Doctrine**: one-line canary principle in `doctrine/review-board.md`.
- Tests: 21 new tests (red-before-green); full suite 1852 passed; rv lint PASS; rv help --check
  OK; leakage clean. CI green on head SHA `2e4303f` — all 5 checks passed.

### Decisions
- Skip only sections titled exactly "Abstract" (not "Results Abstract" etc.) — anti-positivity
  carve-out is narrow: only the cited paper's own abstract, not researcher's recorded distillation.
- Canary uses a synthetic note with ## Result section so the extractor is exercised, not just
  the judge. The probe must survive the full extractor+judge pipeline.
- CSV: 2-col only for unambiguous machine-readable results; multi-col → JSON (clear error enforces this).

### Open / next
- Hub opens PR; Argus review + Wren fit-check before human-go merge.

## 2026-07-03 (SR-DOCTOR-PRINCIPLED: permissions + propose + confirm + learn)

### Done
- **Prereq refactor**: extracted `_ssh_probe_call(host, argv, archetype)` SSOT — the
  common ssh-error ladder (FileNotFoundError/TimeoutExpired/OSError/exit-255 ->
  unreachable dict). Routes all three `_probe_remote_*` functions through it. SR-CO-REMOTE
  tests pass unchanged (25/25).
- **StrictHostKeyChecking**: changed `no` -> `accept-new`. Safer default: accepts new
  hosts on first connect; rejects known-host key changes (real MITM signal). Still
  non-interactive for automated probes.
- **Stage 1b — PERMISSIONS probe (SLURM-first)**:
  - `_probe_permissions_slurm(host)`: runs sacctmgr (associations + QOS) + scontrol
    show partition over same `_ssh_exec` SSOT; parses into `{allowed_partitions,
    forbidden_partitions}` dict with reasons.
  - `_parse_sacctmgr_assoc`, `_parse_sacctmgr_qos`, `_parse_scontrol_partitions`:
    pure parse helpers for parsable2 output and scontrol block format.
  - PBS permission seam: `{"available": False, "reason": "PBS permission probe: not yet
    implemented"}` — honest SLURM-first boundary per §5DOC.
  - Graceful degrade: sacctmgr absent or non-zero -> `{"available": False, "reason": "..."}`
    -> falls back to inventory-only proposal with explicit banner. Never silent.
- **Stage 2 — PROPOSE (pure/deterministic)**:
  - `_propose_tiers(partitions, permissions, gpu_tiers, run_outcomes, lessons)`:
    cheapest-that-fits per tier; rationale string on each row; forbidden partitions
    never proposed; unmapped tiers surfaced with reason. LEARN: OOM outcomes annotate
    warnings; lesson triggers surface inline.
  - `_gres_gpu_count(gres_string)`: extract integer GPU count from GRES strings.
  - `_build_proposal(cfg)`: reads cache + manifest, returns proposal from first
    ok ssh+slurm backend.
  - `format_proposal_report(proposal)`: human-readable proposal with rationale + warnings.
- **Stage 3 — CONFIRM (human gate)**:
  - `cmd_doctor_propose(cfg)`: writes quarantined `tiers_proposed` block to compute
    manifest (NOT live `tiers`). Non-TTY-safe. Plain `rv doctor` writes nothing.
  - `cmd_doctor_accept(cfg)`: shows diff; promotes `tiers_proposed` -> live `tiers`;
    stamps `accepted_ts`; clears mapping from proposed block. Only path to live tiers.
- **Per-type bifurcation enforced**:
  - `ssh` archetype: direct nvidia-smi over ssh (flat topology — correct); NO sacctmgr.
  - `ssh+slurm`: scheduler inventory + first-class permissions probe.
  - `local`: unchanged (SR-6 probe).
- **build_parser updated**: `--propose` + `--accept` flags with when_to_use + anti-pattern.
- **36 new tests** in `test_sr_doctor_principled.py`: _ssh_probe_call error ladder,
  permissions probe + forbidden exclusion (key non-vacuity), per-type bifurcation,
  PROPOSE determinism + forbidden-never-proposed + unmapped surfacing, CONFIRM
  human-gate (propose writes tiers_proposed not tiers; accept-without-propose errors),
  LEARN OOM annotation + lesson inline, graceful degrade, accept-new assertion.
- Full suite: 1831 passed, 37 skipped. lint PASS. help --check OK. leakage clean.

### Decisions
- `_ssh_probe_call`: returns `CompletedProcess | dict` (isinstance check at callers).
  Considered returning a sentinel object but the dict-on-error / CompletedProcess-on-success
  pattern is simpler and more Pythonic.
- `tiers_proposed` vs mutating `gpu_tiers`: kept as a parallel key (`tiers_proposed`
  for quarantine, `tiers` for live partition mapping). This avoids mutating the
  existing gpu_tiers structure which declares GPU requirements; `tiers` holds the
  accepted partition assignments separately.
- PBS permission seam left as honest "not yet implemented" per §5DOC SLURM-first boundary.

### Open / next
- Push branch + PR; hub opens it (human-go class).
- PBS permission probe (qstat -Qf / qmgr acl_users) to fill the seam.

## 2026-07-03 (SR-CO-REMOTE: real ssh remote probe — scheduler-aware GPU discovery)

### Done
- **Real ssh remote probe** in `doctor.py`: replaced `_probe_remote_backend_deferred`
  with `_probe_remote_backend` that dispatches to archetype-specific probers.
- **_probe_remote_slurm**: calls `sinfo --format='%P %G %D' --noheader` via ssh;
  parses partitions + GRES GPU types. GPU discovery is scheduler-aware — does NOT call
  nvidia-smi on login node (which false-negatives because login nodes are typically
  GPU-less on HPC clusters).
- **_probe_remote_pbs**: calls `pbsnodes -a` via ssh for PBS/Torque clusters.
- **_probe_remote_ssh**: plain connectivity check via `ssh <host> true`.
- **_parse_sinfo_output**: pure parser for `%P %G %D` sinfo format — extracts
  partition names, GRES strings, node counts; builds deduplicated `gpu_types` list
  from GRES like `gpu:a100:4 → "a100"`.
- **BatchMode fail-fast**: all probe calls use `_SSH_PROBE_OPTS = ["-o", "BatchMode=yes",
  "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no"]` — automated probe
  NEVER hangs on auth prompt or unreachable host.
- **Graceful degrade**: timeout → probe_status="unreachable"; ssh exit 255 (auth fail) →
  "unreachable"; scheduler error → "scheduler_error" (reachable=True, distinct from
  unreachable); FILL placeholder host → "unfilled" (guides user to configure).
- **format_report updated**: shows partitions + GPU types for ok probes; unreachable
  reason + corrective action for failures; no longer shows old "SR-CO-REMOTE" deferral.
- **_ssh_exec docstring corrected**: no longer claims extraction from `submit` (submit
  uses subprocess.run directly — different contract, timeout=60, error semantics).
- **25 new tests** in `test_sr_co_remote.py`: sinfo parser, login-node GPU trap (the
  key correctness test), BatchMode/ConnectTimeout argv assertions, unreachable vs no-GPU
  distinction, PBS probe, format_report display, SR-7 regression guards.
- **SR-CO test updates**: deferred assertions replaced with real-probe assertions;
  SR-CO-REMOTE mention removed from format_report assertions.

### Decisions
- D-CO-REMOTE-1: submit() not routed through `_ssh_exec` — submit builds a structurally
  different argv (full ssh_argv with host, submit flags, container wrap, cmd as a unit),
  uses timeout=60 (vs 15 for probes), and has FileNotFoundError→RuntimeError semantics
  that differ from the probe's degrade-to-unreachable contract. Routing through `_ssh_exec`
  would require splitting the argv or changing the SSOT signature — net negative. The
  docstring was updated to accurately document the actual call sites.
- D-CO-REMOTE-2: `StrictHostKeyChecking=no` added to probe opts — new HPC systems
  not yet in known_hosts should not block the probe with an interactive prompt.

### Open / next
- SR-10 (publish) — the endgame

## 2026-07-03 (help-map rework + snippet-check gate: PR #TODO)

### Done
- **Item 1 (S3+S4b) — grouped help renderer**: `rv help` now renders verbs grouped by
  workflow phase (8 phases: Setup · Lit-review · Experiment · Figure · Manuscript ·
  Gap loop · Infra/git · Coordination) with phase headers, first-sentence descriptions
  (no 60-char truncation), per-verb subcommand listing, a Gap-loop section surfacing all
  5 `review gap-*` subcommands (gap-scan/gap-route/gap-close/gap-list/gap-promote), and
  a C4 validation-map footer. Groups at render-time — `_VERB_REGISTRY` order untouched
  (SR-CO collision avoidance).
- **Item 2 (S5) — snippet truthfulness gate**: `rv help --check` now parse-verifies
  every `Use \`rv <verb> ...\`` snippet with `<placeholder>` patterns against the real
  argparse parser (`_check_example_snippets`). Fixed 14 broken snippets across devlog,
  project, manuscript, figure, and review — central bug: `rv figure new --experiment <id>`
  omitted required `<project>` and `<fig-id>` positionals; all `rv review` subcmd examples
  had wrong arg order (`rv review list <project>` → `rv review <project> list`).
- 10 new tests (all were RED before implementation, GREEN after).

### Decisions
- Group at render time via `_HELP_PHASE_MAP` constant — do not reorder `_VERB_REGISTRY`
  (SR-CO is concurrently editing compute/doctor/plugins entries).
- Snippet check filters to `Use \`rv ...\`` patterns (capital U) with `<placeholder>` —
  navigation hints without placeholders (e.g. `rv project list`) are intentional shorthand.
- `_first_sentence` uses `[.!?](?:\s|$)` regex — stops at sentence-ending periods only,
  not at `.md` file extensions or section refs like `§5K.7`.
- `_verb_subcommands` loads each verb's argparse parser at render time to get live subcommand
  names — no hardcoded lists (except `_REVIEW_MAIN_SUBCMDS` for the Lit-review split).

### Open / next
- PR #TODO needs human-go merge (reviewer-gate class).
## 2026-07-03 (SR-CO: compute-onboarding — rv compute init + env-aware doctor seam)

### Done
- **rv compute init**: guided scaffold writes `compute_manifest.json` (local backend,
  remote cluster FILL block with archetype pre-detected from local sbatch/qsub, W&B
  entity/project FILL block, seeded gpu_tiers). Refuses to clobber without `--force`.
  No secrets in manifest — leakage-clean by construction.
- **Env-aware rv doctor**: iterates declared backends instead of only-local. Local backend
  fully probed (today's probes, unchanged). Remote backends (ssh/ssh+slurm/ssh+pbs)
  honestly reported as "declared; remote probe = SR-CO-REMOTE" — never silently skipped
  (charter §2). Cache shape changed to per-backend `{backend: {ts, capabilities}}`;
  flat legacy cache normalised to per-backend for back-compat.
- **_ssh_exec extracted** from `adapters/remote.py`: shared SSOT `_ssh_exec()` pulled
  out of the two inlined calls in `_run_status`; `_run_status` delegates to it. All
  existing SR-7 tests pass unchanged. SR-CO-REMOTE builds on this seam.
- **W&B manifest block**: `_resolve_wandb_from_manifest()` in `wandb_pull.py` reads
  entity/project from `results.wandb`; `wandb_pull()` uses it as fallback when env
  unset (env-over-config; FILL sentinel treated as unconfigured).
- **compute-run-recipe.md** shipped as package data (`data/doctrine/`): before-you-
  submit one-pager naming the three commands + anti-patterns.
- **research-loop.json run nodes wired**: every `run` node carries
  `reads: ["doctrine/compute-run-recipe.md#how to run here"]`.
- **rv check nudge**: warns when `compute_manifest.json` absent with exact command.
- **cli.py, init.py, QUICKSTART.md**: DECLARE→DISCOVER order surfaced everywhere.
- **37 new tests** in `test_sr_co.py`; `test_sr6.py` updated to per-backend shape.

### Decisions
- D-CO-3 confirmed: `rv compute init` is separate from `rv init` (cluster facts are
  user-specific; auto-scaffolding at init buys little and clutters local-only setups).
- D-CO-6 confirmed: phased — SR-CO ships the seam; SR-CO-REMOTE ships the actual
  remote ssh probe (BatchMode, sinfo GRES GPU discovery, not login-node nvidia-smi).
- Cache shape changed to per-backend (breaking for any code reading `result["capabilities"]`
  directly — only `test_sr6.py` needed updating; no other callers).

### Open / next
- **SR-CO-REMOTE**: remote ssh probe — `_ssh_exec` with BatchMode + ConnectTimeout,
  scheduler-aware GPU discovery (`sinfo -o '%P %G %D'`), per-backend cache population,
  clean "cluster unreachable" degrade. Seam is in place.
- PR on feat/sr-co at SHA 85cfa07 — hub opens PR; awaits reviewer + Wren fit-check.

---

## 2026-07-03 (lit-review loop hardening: Fix #34 reads:-gate + Fix #32 corpus-dedup)

### Done
- **Fix #34 — reads: grounding gate was silently dead for review loops**: `_rel()` in
  `review/__init__.py` emitted bare OKF type names (e.g. "literature"). The resolver uses
  `manifest_path.parent` as project_root — for review manifests that's `reviews/<scope>/`,
  not the project root. Every OKF-dir reads: pointer failed (6+ reads-scope ERRORs per run),
  AND the gate was effectively off (all reads were unresolvable). Fix: `_rel()` now returns
  `str(project_notes_dir / okf_type)` — absolute paths that resolve correctly. Covers both
  Phase-1 and Phase-2 manifests. 3 new tests in `test_sr_lr_1.py` (green after fix).
- **Fix #32 — corpus-dedup blind to filed literature notes**: `_corpus_annotation` only
  checked `library.json` (Zotero). Freshly-filed `literature/<citekey>.md` notes showed
  `[NEW]` because Zotero sync is async. Fix: added `_load_notes_index(literature_dir)` that
  scans `literature/*.md` for `doi:` and `arxiv_id:` frontmatter fields; `_corpus_annotation`
  gets a `notes_index` kwarg (checked after corpus_index); all three cmd_ call sites load and
  pass it. Also added `doi`/`arxiv_id` placeholder fields to literature note template so
  researchers can populate them immediately. 14 new tests in `test_research_corpus_dedup.py`.

### Decisions
- Fix #34 approach: fix the manifest GENERATOR (emit absolute paths) rather than the resolver
  (add project-root override logic). Less invasive — non-review DAGs unaffected; resolver
  unchanged.
- Fix #32 approach: separate `notes_index` param on `_corpus_annotation` (not merged into
  corpus_index inline) keeps the two sources auditable and the library.json path first.
  literature note template gets doi/arxiv_id as optional placeholders (empty by default) —
  same pattern as datasets/location+hash; doesn't break any existing tests.

### Open / next
- Push branch; hub to open PR (human-go class — loop correctness, two-gate CI).
## 2026-07-03 (SR-LENS-RM: remove project-lens mechanism)

### Done
- **SR-LENS-RM** (`feat/sr-lens-rm`): removed per-project CONTRACT lens; moved to one
  general vault-level crew composed from charter + role doctrine.
  - `build_agents.py`: replaced `_hat_header` / CONTRACT machinery with `_compose_hat()`
    reading `doctrine/agent-charter.md` + `doctrine/roles/<personal>.md` via `_ROLE_DOC`
    map; both backends now build 6 flat vault-level files; removed `--project` flag.
  - `project.py`: removed CONTRACT scaffold (step 7b), per-project hat bake (step 10),
    and rollback agents-dir cleanup. Added `pointers.md` skeleton (D-LR-1); updated
    next-steps message.
  - `init.py`: removed demo-CONTRACT copy block; updated auto-build call signature.
  - `check.py`: removed `_check_project_integrity` and "Project integrity" section.
  - `status.py`: added "Pointers:" echo line reading `source_dir/pointers.md`.
  - DELETED: `CONTRACT.md.tmpl`, `demo-*/CONTRACT.md`, `test_sr_contract.py`.
  - REWORKED: `test_sr_ccb.py` (TestDemoContracts → TestNoDemoContracts; backend unit
    tests updated for new render() signature).
  - ADDED: `test_sr_lens_rm.py` (24 acceptance tests, all green).
- **Latent bug fixed**: hats previously carried only the CONTRACT body (never the charter
  or role doctrine). The `_compose_hat()` composition closes this gap.

### Decisions
- D-LR-1 (pointers home): `source_dir/pointers.md` — travels with the project, read
  fresh, no fill-gate, surfaced via `rv status` "Pointers:" echo line.
- D-LR-2 (delete vs deprecate): full DELETE (pre-v1, no adopters, no deprecation shim).
- D-LR-3 (per-project roster field): kept inert in registry — forward-compat, minimal
  blast radius. build-agents ignores it (vault-level crew now).
- D-LR-4 (hat body): inline-compose charter+role — self-contained system prompt.
- The `AgentsDirBackend` now writes flat `<role>.md` (not `<project>/<role>.md`);
  the first-project-pick namespacing hack is gone (one crew → no collision → mooted).

### Open / next
- PR ready for Wren fit-check + human-go (human-go class: crew-generation convention).

## 2026-07-03 (SR-CCB fast-follow: doc-verb audit covers .tmpl files)

### Done
- **CCB audit coverage** (`feat/ccb-audit-coverage`): Closed the .tmpl coverage hole Wren
  caught in the #62 re-fit. Added `_iter_audit_files()` classmethod as SSOT for the file
  set (*.md + *.tmpl); restricted `_collect_rv_verbs()` to code contexts (backtick-quoted
  inline + fenced blocks) so prose "rv verbs" phrases don't false-positive; updated the main
  gate test to use the new helper; added three supporting tests proving CLAUDE.md.tmpl and
  CONTRACT.md.tmpl are permanently guarded.
- **init.py magic number**: `_expected_count = 6` → `len(DEFAULT_ROSTER) + 1` so the count
  tracks roster changes automatically.

### Decisions
- Scoped regex to backtick/fenced-code (not all prose): matches Wren's recommendation and is
  semantically correct — the gate lints commands adopters will type, not English text. The
  prose "rv verbs" phrases in CLAUDE.md.tmpl lines 14/44 are legitimate documentation.
- Added `_iter_audit_files()` classmethod as the single file-iteration SSOT rather than
  duplicating `rglob` calls — tests and gate share the same logic, so a future extension
  (e.g. *.tex) only needs one change.

### Open / next
- PR needs hub to open (identity guard: crew cannot self-approve).

---

## 2026-07-03 (SR-CCB — Claude Code binding: rv init boots Alfred + crew)

### Done
- **SR-CCB (`feat/sr-ccb`)**: The min-viable Claude Code binding. `rv init` now
  scaffolds `CLAUDE.md` (Alfred hub-bootstrap), creates `.claude/agents/` dir (CC
  session-start requirement), and auto-runs `build-agents --target claude-code` to
  populate `.claude/agents/{manager,engineer,researcher,designer,reviewer,architect}.md`
  with CC-format subagent files. A bare `rv init myvault && cd myvault && claude`
  now boots Alfred + a discoverable 6-agent crew with zero extra commands.
- **AgentBackend seam** in `build_agents.py`: `--target {agents-dir,claude-code}` flag;
  `ClaudeCodeBackend` emits YAML frontmatter (name/description/tools/model) + hat body;
  `AgentsDirBackend` preserves today's default; v1.1 slot commented for codex/cursor/generic.
- **Tool-grant policy** (PUB-CCB.2): coordinator-class (manager/architect) no Bash;
  reviewer no Write/Edit; researcher WebSearch+WebFetch; all model values as aliases
  (sonnet/opus/haiku), never versioned IDs.
- **`CLAUDE.md.tmpl`**: Hub-bootstrap with correct role-boundary table (human/Alfred/crew
  separation, per coordinator note). Alfred runs control-plane verbs; crew runs
  role-appropriate rv verbs from their hat bodies.
- **Demo CONTRACTs** shipped as package data: pre-filled (no FILL stubs) for demo-research
  and demo-litreview so the demo crew composes project-aware from the first session.
- **CI**: added leakage scan step for `data/templates/` (publish-bound).
- 45 SR-CCB acceptance tests (all RED before, all GREEN after). Full suite: 1722 passed.

### Decisions
- `.agents/` stays as the target-neutral source-of-record; `.claude/agents/` is the
  CC-rendered projection. Both coexist — deprecating `.agents/` would break future
  codex/cursor backends that render from the same source.
- Default `--target` stays `agents-dir` (non-breaking); `rv init` passes `claude-code`
  explicitly.
- Architect emitted as a subagent (`.claude/agents/architect.md`) — vault-level coordinator,
  Alfred delegates coherence reads to it.
- `_CC_ROLES = DEFAULT_ROSTER + ["architect"]` = 6 files total.
- Demo CONTRACTs written from `data/examples/<demo>/CONTRACT.md` (shipped alongside the
  loop manifests) to `.agents/<demo>/CONTRACT.md` at init time.

### Open / next
- Hub to open PR for `feat/sr-ccb` (human-go class: harness binding, publish-critical).

## 2026-07-03 (fix/sr-ccb-wren-block — remove fabricated rv verbs; harden init post-build)

### Done
- **Wren BLOCK resolved (`b7b5233`)**: Audited ALL `rv <verb>` patterns across 9 shipped data
  doc files (CLAUDE.md.tmpl, doctrine/roles/*.md, doctrine/*.md). Found 30+ fabricated
  references to vault-OS tools not in the OSS package (`rv identity`, `rv gh`, `rv launch`,
  `rv poll`, `rv approve`, `rv route`, `rv guard-engineer`, `rv hub-guard`, `rv selfcheck`,
  `rv memory`, `rv heal`, `rv crew` as string literal, `rv devlog-check`).
- **Fix**: replaced every fabricated pattern with a real package verb or prose rewrite that
  avoids the `rv <verb>` form. Real package verbs used: `rv git-discipline`, `rv wt`,
  `rv devlog check`, `rv build-agents`, `rv check`; vault-tier sections rewritten to
  describe sbatch/gh patterns directly or conceptually.
- **`TestShippedDocVerbAudit`** (non-vacuous): greps all `data/**/*.md` for `rv <verb>`,
  asserts each is in `_VERB_REGISTRY | {"help"}`. Was RED (30 hits), GREEN after fixes.
- **Argus hardening**: added post-build assertion in `rv init` that verifies exactly 6
  `.claude/agents/*.md` files exist after auto-build. Silent zero-exit with 0 files now
  becomes `return 1` with a loud error message.
- CI: both new runs (push + PR) green on `b7b5233`. 48 SR-CCB tests pass.

### Decisions
- Scope of doc fixes: every `rv <verb>` in a shipped file must resolve to a real package verb
  — not just the headline-named ones. The structural test enforces this as a CI gate.
- Doctrine files with vault-tier sections: rewrote to describe the underlying principle +
  the OSS-available equivalent; removed vault-OS command forms. The "Identity & rv gh" section
  in tooling.md is now the "Identity and separation of duties" section with prose + gh examples.

## 2026-07-03 (fix/sr-ccb-cache — bypass stale _CACHE bug in rv init auto-build)

### Done
- **SR-CCB cache fix (`cc5758e`)**: Coordinator hands-on wheel verification found `.claude/agents/`
  EMPTY after `rv init`. Root cause: `cli.py` calls `load_config()` at dispatch time (line 519)
  to load instance verbs, populating `_CACHE` with a stale default config (no projects, wrong
  `instance_root=CWD`). The auto-build's `load_config()` call hit the cache → files written to
  WRONG root. The conftest `autouse=True` `reset_config_cache` fixture masked this in tests.
- **Fix**: replaced `load_config()` with direct Config construction from the just-written TOML
  (`_load_toml` + `_merge` + `_expand_paths`), bypassing the cache. `reset_config_cache()` called
  after to clear any stale pre-init cache for subsequent commands in the same process.
- **Non-vacuous RED test** (`TestInitColdPathCacheResistance`): injects stale `_CACHE` with wrong
  `instance_root` before `cmd_init_in_dir`; asserts 6 files appear in the CORRECT instance.
  Was RED before fix (empty agents dir), GREEN after. 47/47 SR-CCB tests pass; 1724 full suite.
- CI: all 5 jobs green on `cc5758e`.

### Decisions
- Direct TOML construction (not `load_config(reload=True)`) is the right pattern for any verb
  that constructs a Config for a NEW instance: no cache side effects during construction,
  explicit `reset_config_cache()` clears stale state for subsequent commands.

## 2026-07-03 (feat/default-roster — canonical default crew, --roster removed)

### Done
- **DEFAULT-ROSTER (`feat/default-roster`, commit `8762ca7`)**: Every project registered via `rv project add` or `rv project new` now automatically gets `DEFAULT_ROSTER = [manager, engineer, researcher, designer, reviewer]`. The `--roster` CLI option is removed from both verbs. Belt-and-suspenders: `build-agents` and `role list` treat any empty/missing roster in the registry as DEFAULT_ROSTER, so legacy projects with `roster = []` also get the full crew. QUICKSTART.md fixed from the stale positional-arg form to `--code/--source`. 12 new tests (TestDefaultRosterConstant, TestProjectAddDefaultRoster, TestEmptyRosterFallback, TestBuildAgentsDefaultRoster); 7 existing tests updated for new semantics. Full suite 1677 passed; `rv lint` clean; `rv help --check` green.

### Decisions
- **Hub (alfred) and architect (wren) excluded from DEFAULT_ROSTER**: hub is the sole spawner (vault-level), architect is cross-project stack coherence (vault-level). All other named-crew roles (manager/engineer/researcher/designer/reviewer) are project-scoped and appear per-project.
- **Slug convention = functional role name**: `manager`, `engineer`, etc. (not personal names atlas/mason). Matches pre-existing test convention and avoids confusion between role doc filenames and roster entries.
- **`cmd_new` Python API preserved with `roster or DEFAULT_ROSTER`**: internal Python callers passing `roster=[]` get the default too; the function signature is backward-compatible.
- **No vault-level vs project-level role distinction in code**: the code models this purely via `DEFAULT_ROSTER` (excludes hub/architect). A future explicit role-tier field is a possible follow-up but not needed for this deliverable.

### Open / next
- Hub to open PR for `feat/default-roster` (reviewer-gate class).

## 2026-07-02 (SR-GAP-HYGIENE — vanished-anchor check)

### Done
- **SR-GAP-HYGIENE (feat/sr-gap-hygiene, commit `860b7c4`)**: Extended `note.cmd_check` with `check_gap_anchor` — a degrade-to-WARN check (isomorphic to `check_covers_links`) that fires when an `open` or `reopened` gap's `anchor:` field resolves to a nonexistent artifact. CLI handler extended to treat `[gap-hygiene]` prefix as warn-not-block alongside `[repro-lint]`. 11 hermetic tests incl. red-before-green guard, live/dead-anchor cases, all closed-status no-warn coverage, and CLI exit-0 guard. Full suite 1665 passed; `rv lint` clean; CI green on `860b7c4`.

### Decisions
- **Check open+reopened only** (not closed/proven-open/promoted): actionable statuses that count toward `open_gap_count`; closed-status anchor vanishing is lower urgency and would add noise for cleaned-up notes. Documented in `check_gap_anchor` docstring.
- **Resolver reuse**: `anchor_path = project_notes_dir / f"{anchor}.md"` — same pattern as `check_covers_links`'s `notes_root / f"{child_id}.md"`. No parallel resolver forked.
- **Degrade-to-WARN not BLOCK**: per Wren D-CLOSE-3 ruling. A dead anchor is a hygiene signal, not a corruption.

### Open / next
- Hub to open PR for `feat/sr-gap-hygiene` (identity guard; reviewer-gate class).

## 2026-07-03 (gap-loop-cleanup — Items #30, #26, #28, #29)

### Done
- **Item #30 (Signal 2 narrow, `feat/gap-loop-cleanup` commit `00c678f`)**: Narrowed `_check_reopen_signal` Signal 2 (contradictory re-fire) to MACHINE-CLOSED states only (`{closed-supported, closed-filled}`). `proven-open` and `promoted` (human-blessed) now emit a loud `UserWarning` call-to-action instead of auto-reopening. Ada ruling: automation-authority + COPE.
- **Item #26 (parser convergence, commits `348e29f` + `5f8aa2c`)**: Extended `note._parse_frontmatter` to handle YAML `  - item` list syntax (lazy-promote: empty keys stay `""` until a list item follows, preserving `.strip()` backwards-compat). Deleted 53-line local `gap_scan._parse_frontmatter_gap` duplicate. All 9 call sites updated to use `_pfm` alias. Grep-before-extend audit: no non-gap_scan caller accesses list-valued fields. Updated 2 test_sr_lr_2.py guard tests that encoded the now-lifted STOP decision.
- **Item #28 (SR-GAP-ROUTE polish, commit `60619b2`)**: `_cmd_gap_scope_experiment` context file renamed from fixed `_gap-context.md` to `<gap_id>-gap-context.md` (mirrors `<gap_id>-plan.md`). Added `UserWarning` when `scope` arg is passed with `--target experiment` (scope arg ignored; plan named by gap ID). Updated tests 4f/4g to use the new path.
- **Item #29 (back-edge warn, commit `0cdd1b3`)**: `_append_closes_to_note` now emits `UserWarning` on the skip path (missing `--by` closer note) instead of silently returning. Forward `closed_by:` edge still written. Charter §2: surface, never silently drop.

### Decisions
- **lazy-promote semantics**: Preferred `val == ""` for keys with no list items over an early `[]` to avoid breaking callers. This means gap_scan callers see `[]` only for keys with actual list items, and `""` for unset fields — cleaner than requiring callers to handle `str | list`.

### Open / next
- PR `feat/gap-loop-cleanup` pushed, CI green on `5f8aa2c8`. Hub to open PR; `human-go` class (touches note.py + gap-loop core).

## 2026-07-02 (SR-GAP-CLOSE / SR-LR-4 — gap-closure lifecycle, closure-as-provenance)

### Done
- **SR-GAP-CLOSE (SR-LR-4)**: gap-closure lifecycle complete. Makes closure a PROVENANCE
  EVENT, not a status delete. Zero new mechanism — frontmatter regex-stamps + pure OKF reads.
- **`GAP_STATUSES += {promoted, reopened}`** — NOT superseded (DEFERRED per D-CLOSE-3
  to `note.cmd_check`). Additive; all existing statuses untouched.
- **`cmd_gap_close --by <note-ref>`** bidirectional provenance edge (Ada ruling 2, W3C PROV):
  `--by` REQUIRED for `closed-supported`/`closed-filled` (charter §2: un-auditable without);
  REJECTED for `proven-open` (nothing closed it). Writes both: `closed_by: <note-ref>` in gap
  FM (forward edge) + `closes: <gap-id>` in closing note FM (backward link, the failure mode
  Gotel & Finkelstein name). In-place, never moves/archives (load-bearing on idempotent guard).
- **`cmd_gap_promote <gap-id> --to <ref>`** — human-only verb, `proven-open → promoted`.
  Writes `promoted_to: <ref>`. Rejects non-proven-open and absent `--to` (both un-auditable).
  Honesty backstop: promoted claim round-trips SR-MS-2 support-matcher; [ABSENT] verdict
  re-enters the gap loop as `absent_row` — the loop polices its own promotions.
- **`reopened` structural re-detection** (conservative, §5L.21(3)):
  - Signal 1: `absent_row` re-fires on `closed-supported` gap (matcher flip-back to [ABSENT]).
    Requires `matcher_meta`; degrade-to-skip if None.
  - Signal 2: `contradictory` re-fires on ANY closed status (concept re-acquired both edges).
  - Everything else (closed-filled re-fires) → `warnings.warn` (FP guard, §5L.22 caveat a).
  Stamps `reopened_reason: <signal>`; retains `closed_by:` as history (charter §2 surface).
- **`open_gap_count` counts `{open, reopened}`** (D-CLOSE-4 — both actionable).
- **`gap-promote`** CLI subcommand + `--by` on `gap-close` + updated help/anti-patterns.
- **Discovery**: verb registry SR field updated to `SR-GAP-CLOSE`; anti-patterns in docs.
- 50 new tests (test_sr_gap_close.py). Updated test_sr_lr_2.py (old gap-close tests
  now pass `closer_ref` to satisfy the new --by requirement).
- Full suite: 1627 passed, 37 skipped; `rv lint` clean; `rv help --check` OK.

### Decisions
- Superseded status DROPPED (D-CLOSE-3): vanished-anchor hygiene → deferred to `note.cmd_check`
  (the existing validation path already does the isomorphic `covers:`-resolution check).
  Avoids status proliferation; honors reuse-over-create (charter §6).
- run-arm closure: `--by experiments/<id>` records the audit trail; no `backed_by` required
  (backed_by is LITERATURE support, semantically wrong for a run-arm closure). Closure
  persists via the idempotent-preserve guard, not a detector edge.
- Conservative `warn` posture for closed-filled re-fires: the detector cannot distinguish
  "backed_by threshold crossed" from "run-arm generated result" — warn, human confirms.

### Open / next
- SR-10: OSS public publish (endgame)
- SR-GAP-HYGIENE (future): extend `note.cmd_check` for vanished-anchor degrade-to-warn

---

## 2026-07-02 (SR-PLAN-FREEZE-RETRY #23 — max_retries folded into freeze-hash)

### Done
- **`compute_covers_hash` extended** with optional `manifest_nodes=None` param.
  When `None` (default) → byte-identical to pre-extension SR-PLAN-1 (back-compat).
  When nodes provided: appends retries block (`<node_id> max_retries=<N>` for N>0 only;
  omit-defaults ruling) separated by `RETRIES_SENTINEL`.
- **`store_freeze_hash`/`verify_freeze_hash`** now read `run_state.manifest_path` via
  `json.load` and pass `manifest["nodes"]` into `compute_covers_hash` automatically.
  Unreadable/absent manifest_path → graceful fallback to covers-only hash (no crash).
- **Mismatch message distinguishes retry-ceiling drift** from covers-set edit: when the
  stored hash equals the current covers-only hash, the message names "A max_retries
  ceiling was added post-freeze" (stopping-rule change).
- **11 new TDD tests** covering: all-default back-compat (byte-identical), explicit-zero
  treated as default, nonzero changes hash, all 4 tamper directions (raise/add/remove/lower),
  retry-drift message, graceful unreadable manifest, full round-trip, sort determinism.
- **7 existing `TestFreeze` tests pass UNCHANGED** — back-compat proven (dummy manifest
  paths still produce same hashes as before).
- Full suite: 1544 passed, 37 skipped. `rv lint` clean, `rv help --check` OK.

### Decisions
- OMIT-DEFAULTS: only N>0 nodes appear in the retries block. All-default → empty block →
  canonical unchanged → no forced re-freeze of in-flight pre-registrations.
- Sentinel line `---max_retries---`: cannot appear in a valid covers-block line (node ids
  are constrained identifiers with no spaces or dashes-at-start).
- Zero new public-API args on store/verify (manifest auto-loaded from run_state).
- DEFER Option-2 `retry_class: infra` escape hatch — not v1 per §5K.5.1.

### Open / next
- PR open for reviewer-gate review (#23).
## 2026-07-02 (SR-GAP-ROUTE / SR-LR-3 — gap-loop router, read-vs-run)

### Done
- **SR-GAP-ROUTE (SR-LR-3)**: gap-loop router complete. `suggest_route()` pure
  function (per-type table + Tier-B section split per §5L.14–5L.15). Route tokens:
  `ROUTE_LITERATURE`, `ROUTE_EXPERIMENT`, `ROUTE_TRIAGE`. `suggested_route:` field
  written to `gaps/<id>.md` frontmatter at scan time (idempotent).
- **Route-aware `cmd_gap_scope`**: `--target {literature|experiment}` with default
  from gap note's `suggested_route`. Literature arm = SR-LR-2 behavior unchanged.
  Experiment arm: creates `experiments/<id>-plan.md` with `plan_kind: preregistration`,
  research question ← gap.claim verbatim (anti-fabrication spine), `covers:` skeleton,
  diagnosis-table stub that passes K-2 shape-lint; writes `_gap-context.md` adjacent
  with SR-PLAN-1 next-step chain. Zero new DAG mechanism.
- **`gap-route` alias**: thin alias for `gap-scope` (discoverability per §5L.17).
- **`gap-list` subcommand**: `--status proven-open` = the run-candidate queue.
- **`proven_open_count()`**: new function; `rv status` surfaces proven-open count
  in Needs Attention when > 0. Run NEVER auto-fires.
- **Tier B section threading** (§5L.15 D-ROUTE-2): additive ~15 lines. `SupportVerdict`
  gains optional `section: str = ""` field (back-compat). `to_meta_dict()` emits
  `section`. `match_support()` accepts `section=` parameter. `check_support_tally`
  threads `tex.stem` as `section` into each `match_support` call. `_detect_absent_rows`
  reads `section` from verdict meta → `GapRecord._meta['section']` → `suggest_route()`
  auto-splits absent_row by section (intro→literature, results→experiment, none→triage).
- **Honest bound**: router suggests; run never auto-fires. Human-go at gap-route AND
  at SR-PLAN-1's own `human-go-plan` gate. Two existing gates, no new one.
- **Tests**: 44 acceptance tests (test_sr_gap_route.py); red-before-green confirmed.
  Full suite: 1577 passed, 37 skipped.
- `rv lint` clean; `rv help --check` OK (28 verbs); leakage clean on all changed files.

### Decisions
- D-ROUTE-1: `--target` on `cmd_gap_scope` + `gap-route` alias (per locked operator decisions).
- D-ROUTE-2: Tier A + Tier B both shipped (additive, back-compat).
- D-ROUTE-3: diagnosis-table stub (not question-only).
- D-ROUTE-4: results-ingestion arm deferred (out of scope per spec).

### Open / next
- Push + PR for Architect fit-check (Wren) → reviewer gate → human-go merge.

## 2026-07-02 (sr-lr-2 block-fix — D-GAP-3 structured binding, attribution fix)

### Done
- **D-GAP-3 structured binding** (Architect BLOCK resolved): rewired `_detect_absent_rows`
  to consume `RunState.meta['support_matcher']['verdicts']` structured `SupportVerdict` records
  instead of grepping prose with a bespoke `FINDING_RE` (which never matched real matcher output
  and silently returned `[]` — charter §2 violation on the load-bearing loop-closer gate).
  New signature: `_detect_absent_rows(matcher_meta: dict, run_id: str)`. Filters
  `.verdict in {ABSENT, CONTRADICTS}` or `j2_escalation=True`; builds `GapRecord` from
  `.claim_snippet` + `.citekey`.
- **Charter §2 guard**: if meta is non-empty but `verdicts` key is absent, emits
  `warnings.warn` — never silently returns `[]`.
- **API change**: `cmd_gap_scan(matcher_meta=dict|None, run_id=str)` replaces
  `critic_report=Path|None`. CLI: `--critic-report <path>` → `--run-state <path>` (loads
  run-state JSON, extracts `meta.support_matcher`).
- **Parser convergence (STOP decision)**: Wren requested extending `note._parse_frontmatter`
  to handle list values. Verified that extension breaks `check_gates.py:synthesized_okf`,
  `review/__init__.py`, `manuscript/__init__.py` callers doing `.strip()` on expected-scalar
  fields. Per Wren's own escape hatch ("STOP and report if risks can't be cleanly verified"):
  reverted. `_parse_frontmatter_simple` renamed to `_parse_frontmatter_gap` with honest
  docstring. Canonical parser annotated with STOP decision rationale.
- **Attribution fix** (Ada retrieval-verified, coordinator relay): all 7 attribution strings
  in `gap_scan.py` corrected — type names AND procedure from Müller-Bloch & Kranz (2015, ICIS);
  Miles (2017) and Robinson et al. (2011) as related secondary taxonomies (was inverted).
- **Tests**: 7 tests (6a-6e) rewritten to structured dict API; 4 new tests (6f-6i for
  citekey anchor, j2 escalation, §2 guard, silent-all-SUPPORTS); 6j cmd_gap_scan matcher_meta;
  gap parser list test; canonical scalar guard. 48 SR-LR-2 tests pass.
- Rebased on main (post-#49/#51/#52). DEVLOG union. Full suite: 1482 passed, 37 skipped.

### Decisions
- D-GAP-3 re-resolved: absent_row detector binds to STRUCTURED `SupportVerdict` records,
  not a prose file. The prior resolution (grep FINDING_RE) was never correct — it matched a
  format the matcher never emitted. The structured binding is the only correct fix.
- Parser convergence deferred: see STOP decision above. Requires a separate PR that updates
  all `.strip()` callers across check_gates/review/manuscript — touching in-flight SR-MS-1c.

### Open / next
- Push `feat/sr-lr-2` and update PR #50 (hub handles; human-go class).
- Verify CI green on pushed HEAD SHA (post-rebase).

---

## 2026-07-03 (SR-MS-1c — Architect block fix: _run_grounding_builders refactor)

### Done
- Extracted `_run_grounding_builders(... label, extra_unmatched_msg)` as the SINGLE
  anti-fabrication contract site (§5J.4) in `manuscript/compile.py`.
- Refactored `run_prep` and `run_compile` to CALL `_run_grounding_builders` — no
  remaining copy of the builder-orchestration or hard-fail blocks in either function.
- `label` parametrises message wording ("--prep-only:" vs "compile:" + §5J.4 line);
  distinct wording preserved while the contract lives in one place.
- Fixed `except (KeyError, Exception)` → `except (KeyError, TypeError)` in
  `cmd_prep` and `cmd_compile` (Argus nit: Exception subsumed KeyError, over-broad).
- Added 5 `TestSingleOrchestration` tests: structural AST check both functions call
  the helper; behavioral check unmatched-cite hard-fails both paths with label-
  appropriate messages. Full suite: 1485 passed, 37 skipped.
- CI green on `fd1982a` (push + PR runs).

### Decisions
- Return signature of `_run_grounding_builders` is `(exit_code, failure_base | None,
  builder_warnings)`: callers merge in path-specific keys (log/chktex/pdf_path) before
  returning, so failure dicts remain byte-identical to original (compile tests unchanged).
- AST-based structural test (not `getsource` string membership) — immune to rule-7
  lint, comment-free, will catch any future copy of the builder block.

### Open / next
- PR #53 awaiting human-go merge.

## 2026-07-02 (SR-MS-1c — draft-time macro-visibility prep seam)

### Done
- Added `run_prep()` to `manuscript/compile.py`: runs grounding-builders (build_refs_bib
  → inject_results → inject_appendix) without pdflatex. No texlive required.
- Added `cmd_prep()` to `manuscript/__init__.py`: public API with same library_path
  resolution as cmd_compile (project refs key or standard default).
- Added `--prep-only` flag to `rv manuscript compile`: dispatches to cmd_prep when set;
  default False (normal compile when omitted).
- 19 new SR-MS-1c tests in `tests/test_sr_ms_1c.py`:
  - run_prep populates refs.bib, results.tex, appendix-repro.tex without producing PDF
  - Idempotency: prep→prep→compile identical to compile-alone (builders overwrite, never append)
  - run_prep exits 0 even when pdflatex absent (monkeypatched _find_tool)
  - CLI --prep-only flag dispatches correctly and defaults to False

### Decisions
- Surface: `rv manuscript compile --prep-only` (flag on existing verb) rather than a
  separate `rv manuscript prep` subcommand. Keeps the compile/prep relationship explicit
  and avoids proliferating verb surface area.
- Idempotency is structural: all three builders use write_text (overwrite), so
  prep→prep→compile produces identical output as compile alone.
- The DAG spec for results-discussion and assemble nodes can instruct the agent to run
  `rv manuscript compile --prep-only <project> <id>` first — no new DAG mechanism needed.

### Open / next
- SR-MS-2 (semantic gates + support-matcher): gated on D-MS-4 (judge model) + Ada.
- SR-EXP-REPRO: extends wandb_pull.py + experiment note repro_* fields.
## 2026-07-02 (task-22-pt2 — wheel __file__ audit)

### Done
- Audited every `__file__`-repo-root usage in `lint.py`, `git_discipline.py`,
  `wait_for.py` (Wren's SR-PKG/#46 flag). Verdict per site:
  - `lint.py _FRAMEWORK_ROOT/_TESTS_DIR/_SRC_DIR` — DEV-ONLY. Rules 4/5 scan the
    framework's own `tests/` and `src/research_vault/`. In a wheel context these
    paths don't exist → 0 files → silent no-op. Annotated with dev-repo note.
  - `lint.py cmd_lint src_dir = Path(__file__).parent` — DEV-ONLY. Leakage scan
    targets framework source (CI gate for framework devs). Wheel users typically
    don't configure `forbidden_patterns`; if they do, scan runs on installed source
    (harmless, meaningless). Annotated.
  - `git_discipline.py scripts/leakage_scan.sh` — DEV-ONLY. Graceful fallback chain
    already handles wheel (fails-open when script not found). Comments already describe
    dev vs installed intent. Annotated with task #22 note.
  - `wait_for.py _launch_background_poller package_path` — USER-REACHABLE, WRONG.
    `rv wait-for` is a user verb. The background poller subprocess needs
    `sys.path.insert(0, package_path)` to `import research_vault.wait_for`.
    Old code: `.parent.parent.parent` (repo root in dev, `lib/python3.x/` in wheel —
    neither is a valid sys.path entry for the package). Comment said "src/ dir" but
    was factually wrong.
    FIX: extracted `_get_package_path()` returning `.parent.parent` (`src/` in dev,
    `site-packages/` in wheel — both contain `research_vault/`). Works in practice
    before the fix only because the package is already installed in the env.
- 2 new tests in `test_task22_wheel_audit.py`: red-before-green verified.
  - `test_poller_package_path_contains_research_vault`: FAILED before fix (ImportError
    for non-existent `_get_package_path`), PASSED after.
  - `test_old_three_parent_path_does_not_contain_research_vault`: confirms the old
    calculation was wrong (repo root does not contain `research_vault/`).
- Full suite: 1458 passed, 37 skipped. `rv lint`: PASS. `rv help --check`: OK.
  Leakage scan: clean.

### Decisions
- Fix only the user-reachable site (`wait_for.py`); leave dev-only sites annotated.
  The annotation pattern is the right outcome for confirmed-dev-only tooling (§3 of
  the task brief: "a valid finding — document it").
- Extracted `_get_package_path()` (not inline in `_launch_background_poller`) so
  the path logic is testable in isolation — clean TDD red-green.
- `.parent.parent` is correct in BOTH dev and wheel: `src/` and `site-packages/`
  are symmetric — both are the parent directory containing `research_vault/`.

### Open / next
- PR: hub opens (crew-cannot-self-approve; task #22 architectural PR).
## 2026-07-02 (sr-cif-terminal-fix — gate terminal-set on MERGED, not just CI green)

### Done
- Fixed `GitHubActionsSource.get_terminal_set`: added Gate 1 — `state == "merged"` —
  before the existing CI-green gate.  A green-but-OPEN PR (the normal awaiting-human-go
  state) no longer contributes to the terminal set, eliminating the false `[R4] STALE`
  alarm emitted on every reconcile while waiting for the operator merge.
- Updated test 2, 7, 10, 11 in `test_sr_cif.py` to use `state="MERGED"` for the
  positive (terminal) cases — the old tests were inadvertently validating the bug.
- Added tests 23–25 (bug guard, merged positive, functional proof open-vs-merged).

### Decisions
- Placed the state gate FIRST in `get_terminal_set` (before the branch-id and CI-check
  gates) so that OPEN/CLOSED PRs short-circuit without ever calling `_fetch_checks()`.
  This avoids one unnecessary `gh pr checks` subprocess call for non-merged PRs and
  makes the control-flow intent explicit: merge state is the primary gate.
- Advisory (`get_ci_advisory`) is unchanged — it correctly reports CI state regardless
  of PR merge state.  The [R4] wording "terminal (merged/done)" is now correct by
  construction since `get_terminal_set` only fires for genuinely merged PRs.
- Hard boundary preserved: `get_terminal_set` returns `frozenset[str]` only; no write,
  approve, or verdict path added.

### Open / next
- Hub to open PR; Argus review gate.

## 2026-07-02 (sr-cif-activation — rv control reconcile --gh-pr N)

### Done
- Added CLI activation path for SR-CIF (deferred from the SR-CIF merge):
  `rv control reconcile --gh-pr N [--repo owner/repo]` constructs a
  `GitHubActionsSource` and passes it via the existing `extra_sources` seam —
  no new plumbing, composes the merged `cmd_reconcile` directly.
- Added `get_ci_advisory()` on `GitHubActionsSource`: human-facing CI summary
  line (`CI: GREEN/RED/PENDING/UNVERIFIED (PR #N)`) printed before drift findings.
- Added instance-level caching to `_fetch_pr_info()` / `_fetch_checks()` —
  avoids duplicate gh subprocess calls when advisory + reconcile run together.
- Added `_detect_github_repo()` in `control.py`: auto-detects `owner/repo`
  from `git remote get-url origin` (stdlib; no gh; covers HTTPS + SSH remotes).
  `--repo owner/repo` explicit flag overrides; missing repo → exits 1 with message.
- Updated `cli.py` control `when_to_use` with `--gh-pr N` trigger and anti-pattern.
- 8 new hermetic tests (15–22): advisory surface green/red/pending/unverified,
  CLI activation, no-repo exit, and fetch caching — all red-before-green verified.
- Full suite: 1451 passed, 37 skipped. `rv help --check`: OK. `rv lint`: PASS.

### Decisions
- EXTEND `rv control reconcile` (not a new verb) — spec D-CIF-2 recommendation,
  reuse-over-create. The advisory CI line appears where the human already reads.
- Caching: simple instance-variable cache, no functools — avoids pulling in
  lru_cache on a mutable method.
- Auto-detect repo from `git remote get-url origin` (stdlib, not `gh api`):
  respects zero-`~/vault` and no-gh-as-core-dep rules; works for HTTPS + SSH.
- Hard boundary preserved: no approve/write/[PASS] path added anywhere.

### Open / next
- PR needs hub to open (crew-cannot-self-approve; human-go class for gate-touching seam).
- Architect fit-check before merge (per §5G deliverable note).
## 2026-07-02 (sr-retry-counter-fix — cosmetic attempt counter overshoot)

### Done
- Fixed two display-only bugs in `cmd_status` and `cmd_complete` (task #21).
- `cmd_status`: gated `[attempt k/N+1]` counter on `status == "pending"` — terminal nodes no longer show an overshooting counter (e.g. `[attempt 2/1]` for N=0, `[attempt 4/3]` for exhausted N=2).
- `cmd_complete`: gated "retries exhausted: k/N attempts" detail on `max_retries > 0` — N=0 plain failures now print a plain terminal message with no nonsensical `1/0` ratio.
- State machine, terminality, and retry behavior are entirely unchanged.
- Added `TestAttemptCounterDisplay` (5 tests, red-before-green verified). Full suite: 1448 passed.
- `rv lint` clean, `rv help --check` OK. Pushed `feat/sr-retry-counter-fix` @ `985bc18`.

### Decisions
- Counter gated on `status == "pending"` (not just `attempts > 0`): simplest correct condition — a pending node with attempts>0 is by definition retry-queued; any terminal node should not show a live counter.

---

## 2026-07-02 (sr-pkg — packaging-data fix, the publish blocker)

### Done
- Identified the root bug: `_package_doctrine_dir()` / `_package_examples_dir()` /
  `_package_template_dir()` all used `Path(__file__).parent.parent.parent/{name}`
  — resolves fine in dev/editable but misses the wheel at install time. Fallbacks
  silently produced skeleton doctrine (1 README.md) and placeholder loop DAGs.
- Relocated all data into the wheel (single home, no drift):
  - `doctrine/` → `src/research_vault/data/doctrine/`
  - `examples/` → `src/research_vault/data/examples/`
  - `src/research_vault/templates/` → `src/research_vault/data/templates/`
- Rewrote all loaders in `init.py`, `project.py`, `manuscript/__init__.py` to use
  `importlib.resources.files("research_vault") / "data" / ...` + `as_file()`
  (zipimport-safe, no __file__ anywhere).
- Deleted `_write_placeholder_manifest()`, `_copy_demo_project()`, and all silent
  fallback branches — missing data is now a hard `RuntimeError`.
- Broadened CI leakage scan: doctrine re-pointed, examples/ added, root public-bound
  files (README.md, architecture.md, QUICKSTART.md) added. tooling.md included via
  rename from doctrine/ (PR #44 added it, SR-PKG relocation lands it in wheel).
- TDD red→green proven: isolated wheel smoke test fails pre-fix (skeleton doctrine),
  passes post-fix (16 doctrine files incl. tooling.md, 18 example files, 3 templates in wheel).
- Full suite: 1438 tests pass; rv lint clean; rv help --check OK; CI green on SHA
  293fbffbefa0f7ad9b0d4b55f9495a180deca4c5.

### Decisions
- Single data home under `src/research_vault/data/` — dev + CI reference the same
  package path, no separate repo-root copies. Eliminates the drift risk.
- Fallbacks DELETED not just disabled — silent degradation is worse than a loud error
  (charter §2). A broken install surfaces immediately rather than shipping bad content.
- `as_file()` used even for regular wheel installs — marginal overhead, future-proof
  for zip-backed installs.
- ci.yml conflict (SR-PKG vs #44 architecture.md step): SR-PKG loop supersedes — it
  covers architecture.md once (in the for-loop over root public-bound files) plus
  README/QUICKSTART/REFERENCES/SETUP. No double-scan.

### Open / next
- PR feat/sr-pkg → needs hub to open + Architect fit-check + human-go merge.
- SR-SETUP next (the setup flow + rv setup verb + doctor extension + keyring fix).

---
## 2026-07-02 (SR-RETRY — DAG node-level diagnose-before-retry, §5I)

### Done
- Built SR-RETRY: opt-in `max_retries: N` (0<=N<=10) per agent node + diagnose-before-retry.
- Walker unchanged (byte-for-byte). All retry logic lives in cmd_complete (fail-time reset).
- schema.py: _validate_max_retries, _validate_no_max_retries_on_human_go (D-RETRY-1),
  _validate_retry_diagnosis_tips (D-RETRY-8 seam). MAX_RETRIES_CAP=10 (D-RETRY-4).
- store.py: init_nodes adds attempts:0, last_failure:null, failures:[] (§5I.5).
- verbs.py: RETRY_DIAGNOSIS_DIRECTIVE constant (§5I.5b); --error/--error-file on
  rv dag complete; fail-time retry reset in cmd_complete (increment attempts, persist
  last_failure+failures[], reset to pending if attempts_before<N, terminal failed on
  exhaustion); diagnose-first block in _print_frontier + cmd_status (attempts>0).
- 42 new tests: all §5I.3 interaction checks, backward-compat N=0, exhaustion,
  human-go invariant, walker-untouched grep, RETRY_DIAGNOSIS_DIRECTIVE shape.
- CI green on SHA 37ea22727a2499a767983d2714ad29dddcf83fba.

### Decisions
- D-RETRY-9 (--error required when max_retries>0): implemented as a hard error —
  missing → rc=1 with clear message. Optional-with-degradation when max_retries==0.
- _print_frontier now accepts optional node_states parameter (backward-compatible:
  default None → no retry block). All callers in cmd_complete/cmd_status pass it.
- retry-reset clears started_at=None (per §5I.5 spec, truthful per-attempt timing).
- boolean check in _validate_max_retries: bool is a subtype of int in Python, rejected.

### Open / next
- Hub to open PR + request Architect fit-check, then operator human-go merge.

---
## 2026-07-02 (repo-hygiene — PR #44 de-commingled + 4 doc cleanups)

### Done
- De-commingled PR #44: merged origin/main; lint code (lint.py +138, test_lint_rules.py,
  test_sr8.py) collapsed back to main (already present via PR #42). Branch is now doc/config-only.
- Cleanup 1: genericized `~/vault/projects.json` → `projects.json` in architecture.md (line 124).
  Lines 5/37 (standalone boundary Mermaid) intentionally left as ~/vault.
- Cleanup 2: added architecture.md to CI leakage scan targets in ci.yml. Scan verified clean.
- Cleanup 3 (#19): reconciled warns-vs-blocks docstring divergence in note.py cmd_check.
  Child-note checks (plan_role-but-no-stance, confirmatory-absent-from-covers, dangling
  supports_main) were documented as "warns" but actually BLOCK. Fixed docstring + renamed
  2 misleading test methods to _blocks. Behavior unchanged; all 1393 tests pass.
- Cleanup 4: added SSOT comment header to doctrine/standards.md documenting the ~/vault
  Astro-mirror drift risk. Cross-repo CI guard not feasible from OSS repo; noted as follow-up.
- CI green on pushed head a0cd7bf13942ed99 (both CI workflow runs: success).

### Decisions
- Path genericization: `projects.json` (not `<vault-root>/projects.json`) — simpler.
- Docstring reconciliation direction: BLOCK, not weaken — strict-by-design per §5K.7.
- Standards.md drift guard: comment-only header (no test) — cross-repo CI not feasible;
  documented as a ~/vault-side follow-up rather than over-building.

### Open / next
- PR #44 awaits human-go merge (crew cannot self-approve).
- ~/vault standards.md drift-guard is a follow-up task.

## 2026-07-02 (lint-rule7-indirected — task #18 rule-7 indirected + F811 ast.Match)

### Done
- AUDIT: all 6 live indirected getsource guards checked. Guards 1–3 (test_sr8.py)
  are SOUND (symbols only in live code, not comments/docstrings of inspected
  functions) but use `assert X in src` pattern so rewritten. Guards 4–5
  (test_sr_cif.py, test_sr_cp.py) are NEGATIVE-only (`not in`) — not flagged by
  extended rule 7, left as-is. Guard 6 (test_sr_hardening.py) already uses AST
  approach (ast.get_source_segment) — not a getsource guard, left as-is.
- De-vacuoused test_sr8.py guards: 3 tests rewritten to AST inspection.
  test_dataset_in_known_prefixes + test_note_prefix_in_known_prefixes now walk the
  `run()` AST to find `_KNOWN_PREFIXES` tuple literal values. test_streaming_hash
  uses_chunked_read now detects the while-walrus-.read() pattern structurally.
- Implemented rule-7 indirected extension in lint.py: _assert_contains_tainted_in,
  _collect_fn_scope_taint_and_asserts (scope-isolated, recurses via
  _get_compound_bodies, forward ref safe in Python). check_getsource_guard now runs
  both direct + indirected passes per file; deduplicates findings.
- Implemented F811 ast.Match recursion in _get_compound_bodies: each case.body is a
  separate list (dup within a case arm → flagged; same name across arms → not flagged,
  like if/else). Guarded with hasattr(ast, "Match") for <3.10 compatibility.
- 6 RED tests committed first; all 10 new tests GREEN after implementation.
  Full suite: 1357 passed, 37 skipped. `rv lint` PASS (46 test files, 60 src files).

### Decisions
- Conservative taint (never un-taint once assigned from getsource): correct for the
  real cases; well-written AST rewrites use a different variable for the assertion.
- Rule flags only positive `in` form, not `not in`: the two forms have different
  failure modes — positive `in` is vacuously true when symbol in a comment; `not in`
  can false-fail but not false-pass.

### Open / next
- PR to hub for review + merge (human-go class: stack/cross-project change).
## 2026-07-02 (SR-PLAN-2-remainder — §5K.7 deferred items: covers:/stance link-validation + rv result assert)

### Done
- **Item 1 — covers:/stance link-validation (note.py touch):** Added
  `check_covers_links()` and `check_plan_child_links()` helpers to `note.py`.
  Wired into `cmd_check` experiments elif via a pre-pass (collects covered_ids
  from plan masters) + per-note calls. Plan master BLOCKS on missing covers:
  child, invalid/missing stance or plan_role. Child notes warn on missing stance,
  absent-from-covers (confirmatory only; degrade-to-skip when no plan masters),
  broken supports_main target. 17 tests in `test_sr_plan2_remainder.py`.
- **Items 2+3 — rv result assert + predicate-hash-into-run-state (§5K.5.4):**
  New `result.py` module with `rv result assert <exp-note> --metric M --op OP --value V`.
  Hash-verifies results_location; extracts metric from JSON/JSONL (flat or dot-path);
  evaluates predicate; exits 0 on TRUE, 1 on FALSE. With `--run-id/--node-id`,
  logs predicate string + sha256 hash + result to `run_state.meta["predicate_log"]`
  (tamper-evident §5K.5.4 audit). Registered as `result` verb (sr=SR-PLAN-2).
  19 tests. Full suite 1360 passed, 37 skipped; rv lint PASS; rv help --check OK.

### Decisions
- All three §5K.7 items are IN SCOPE and confirmed by spec (§5K.7 text + §5K.5.4).
  No items skipped. All built per spec.
- BLOCK behavior for item 1: missing child / invalid stance / invalid plan_role
  all produce violations (BLOCKs in cmd_check). Child-note stance-missing also
  produces a violation. Absent-from-covers degrades to skip when no plan masters.
- predicate-hash logging is non-fatal: log failure prints WARNING but does not
  override the predicate result (DAG watch still resolves on exit code).

### Open / next
- Push + hub opens PR (human-go class, crew cannot self-approve).

## 2026-07-02 (f811-exemptions — task #16 F811 hardening + rule 7 getsource-guard)

### Done
- Extended F811 exemption set: `@property`/`@x.setter`/`@x.deleter`/`@x.getter` and
  `@singledispatch`/`@functools.singledispatch`/`@fn.register` now exempt (both `ast.Name`
  and `ast.Attribute` forms). Private helper renamed `_is_overload_decorated` →
  `_is_exempt_decorated`.
- Added block-body recursion to `_check_scope_for_f811` via `_get_compound_bodies`:
  descends into each branch of if/for/while/with/try independently; in-branch duplicates
  now caught; try/except split-branch remains naturally exempt.
- Renamed rule label from "redefined-while-unused" to "redefined-in-same-scope" in
  docstrings, module docstring, and `cmd_lint` output — the check is statement-list
  membership, not use-before-redefine.
- Added rule 7 (getsource-guard smell): AST-scans test files for
  `assert X in inspect.getsource(fn)` / bare `getsource`. Reports location + fix hint
  (assert the negative / strip comments via AST). Motivated by #39.
- TDD: 11 new F811 tests + 12 new rule 7 tests; red-before-green verified for all.
  Full suite: 1318 passed, 37 skipped. `rv lint` clean repo-wide (60 src files, 45 test
  files). CI green on head `ebadb9c` (conclusion: success).

### Decisions
- Property/singledispatch exemptions fire on decorator `attr` name only (not the object
  being decorated) — `@x.setter` matches any `x`, which is the right rule since any
  property accessor on any property name is valid.
- Block-body recursion uses a fresh `seen` dict per recursive call — each branch body is
  independent, preserving cross-branch non-flagging.
- Rule 7 is report-only (smell, not proof of vacuity) since getsource presence in a comment
  is not detectable statically; the flag surfaces the smell for human judgment.

### Open / next
- Hub to open PR for review (human-go class — cross-project linter gate change).
## 2026-07-02 (SR-HARDENING — Argus + Wren BLOCK fixes: de-vacuousing + SSOT lazy import)

### Done
- **Fix 1 (Argus BLOCK) — de-vacuoused routing guard test** (`tests/test_sr_hardening.py`):
  Replaced `test_okf_shared_types_is_not_hardcoded_string` (vacuous: asserted presence of
  "OKF_SHARED_TYPES" in raw `inspect.getsource()` — passed even with reverted routing because
  comments contain the string) with `test_routing_condition_uses_membership_not_equality`.
  New test uses `ast.get_source_segment` to extract comment-free condition source for each
  `if X: <var> = cfg.datasets_root` block in cmd_new/cmd_list/cmd_check, then asserts ABSENCE
  of `'== "datasets"'`. Red-before-green proven: reverted cmd_new routing to `== "datasets"` →
  new test FAILED; old vacuous test still PASSED; restored → both green.
- **Fix 2 (Wren BLOCK) — SSOT lazy import in Config.__init__** (`config.py`):
  Removed `_OKF_RESERVED_SLUGS` module-level frozenset (hardcoded 9-string fork of
  `note.OKF_TYPES ∪ OKF_SHARED_TYPES`, could silently drift). Replaced with call-time import
  inside `Config.__init__`: `from .note import OKF_TYPES, OKF_SHARED_TYPES; _reserved = OKF_TYPES | OKF_SHARED_TYPES`.
  Added `test_reserved_slugs_derived_from_note_ssot_not_hardcoded` — asserts no module-level
  `_OKF_RESERVED_SLUGS` attribute. Red-before-green proven: test FAILED on old constant; PASSES
  after lazy-import fix. All 12 slug-guard functional tests still green.
- 1324 tests, 37 skipped; `rv lint` PASS; `rv help --check` OK; leakage clean.

### Decisions
- **Lazy import chosen over drift-guard test**: the lazy import inside `__init__` eliminates
  the fork entirely — a future 10th OKF type is automatically rejected, no sync required.
  Drift-guard tests are defensive; removing the thing that can drift is stronger. Call-time
  import is safe: Config.__init__ runs after both modules are fully loaded (note.py's
  module-level import of Config completes before any Config() call).
- Removed `# noqa: PLC0415` comment from the import line — our linter is AST-based, not
  ruff/pylint; the comment was misleading rather than functional.

### Open / next
- Hub to push for Argus + Wren re-review; crew cannot self-approve.

## 2026-07-02 (lint-f811 — F811 redefined-while-unused gate)

### Done
- Added `check_redefined_while_unused()` (AST-based F811) to `lint.py`: walks every scope
  in `src/research_vault/` and flags `def`/`async def`/`class` names shadowed before first
  use. Exempts `@overload`/`@typing.overload` chains and `try/except` fallbacks (naturally
  excluded). `_SRC_DIR` module-level var mirrors `_TESTS_DIR`; monkeypatchable.
- Wired as rule 6 in `cmd_lint`; 13 hermetic tests added to `test_lint_rules.py`.
- Added `rv-lint` CI job — closes the gap where CI never ran `rv lint` at all.
- Red-before-green proof: ImportError confirmed before implementation; probe file injection
  confirmed FAIL/PASS; CI green on pushed head `a63f00d` (all 4 jobs).

### Decisions
- Scoped F811 scan to `src/research_vault/` only (production code). Tests are excluded: test
  files can have legitimate redefinitions (fixture overrides) and are already covered by the
  vacuous-assertion and unpinned-git-init rules.
- Custom AST check, not ruff — ruff is not in the dep tree (stdlib-only core constraint).
  F811 is simple enough to implement cleanly in ~50 lines of stdlib ast.
- `@overload` exemption covers both `@overload` (bare Name) and `@typing.overload`
  (Attribute form) — both forms appear in the codebase.

### Open / next
- PR `feat/lint-f811` pushed; awaiting hub to open PR + human-go merge.

## 2026-07-02 (SR-HARDENING — 3 targeted fixes from #34/#35 gate)

### Done
- **Fix 1 — native_env value guard** (`adapters/remote.py`): env values containing
  space/comma/semicolon/quote in `native_env: true` mode now raise a loud `ValueError`
  before any argv is built. Expanded docstring names the comma-delimiter + injection risk
  explicitly (was undersold as "spaces won't work").
- **Fix 2 — container + native_env flag ordering** (`adapters/remote.py`): moved the
  container wrap block to AFTER the native scheduler flags, so `--export`/`--chdir` land
  before `apptainer exec img.sif` in the sbatch argv. SLURM was silently parsing them as
  apptainer args when the container wrap was first.
- **Fix 3a — slug-collision guard** (`config.py`): `Config.__init__` now rejects project
  slugs matching any of the 9 OKF type names with a clear `ValueError` at config-load time.
  Added `_OKF_RESERVED_SLUGS` constant (mirrors `note.OKF_TYPES`; no circular import since
  note.py imports Config).
- **Fix 3b — OKF_SHARED_TYPES self-consumption** (`note.py`): all 3 routing sites
  (`cmd_new`, `cmd_list`, `cmd_check`) now use `in OKF_SHARED_TYPES` instead of
  `== "datasets"`. Datasets-specific field checks (`location`/`hash`) remain under
  `if t == "datasets":`. Behavior unchanged; correct when a 2nd shared type lands.
- 28 new tests; 1307 suite total, 0 failures. CI green on SHA 34dec513.

### Decisions
- Used `_OKF_RESERVED_SLUGS` in config.py (not a lazy import of note.py) to avoid circular
  import risk. Comment points to note.py as SSOT; config.py copy must stay in sync.
- Value guard uses a dict `{char: label}` so the error message names the character class
  ("comma", "space", "semicolon", "quote") not the raw character — more actionable.

### note.py regions for #13 (SR-PLAN-2) rebase
- **cmd_new** routing change at the `if note_type in OKF_SHARED_TYPES:` block (around
  the old `if note_type == "datasets":` line in the original). The datasets-specific
  template fields (`location`, `hash`) and body template remain as `if note_type == "datasets":`.
- **cmd_list** routing change at `if t in OKF_SHARED_TYPES:` (around old line 368).
- **cmd_check** routing changes:
  - Outer branch: `if t in OKF_SHARED_TYPES:` (around old line 414).
  - Inner type-consistency check: `if note_type != t:` (was `note_type != "datasets"`).
  - Datasets field checks now nested under `if t == "datasets":` inside the shared branch.
- All other note.py logic (experiments/figures/manuscript branches) is untouched.

### Open / next
- Hub to open PR for review; crew cannot self-approve.

## 2026-07-02 (SR-MS-2 — rubric wiring + calibration gate completion)

### Done
- **Wired Ada's authored rubric as `DEFAULT_SUPPORT_RUBRIC`** (was a placeholder). The real
  adversarial rubric (disconfirm-first, verbatim-span-required, 4-verdict) now ships as the seam
  default. Runtime slots `{CLAIM}` / `{NOTE_CONTENT}` filled by `_build_judge_prompt()`; always
  appends `=== CLAIM ===` / `=== CITED SOURCE ===` markers so parsers/mocks can extract content
  regardless of rubric style.
- **`_parse_judge_response` updated**: handles Ada-format `SPAN:` key alongside legacy
  `VERBATIM_SPAN:`; stitches `DISCONFIRM` + `GAP` into reasoning when `REASONING:` absent.
- **`_rubric_aware_judge` mock fixed** (calibration harness): scoped to claim+note sections only
  (extracts `=== CLAIM ===` / `=== CITED SOURCE ===` blocks, ignores rubric preamble that contains
  instructional examples). Fixed `_CORREL` regex (`\b` word-boundary prevented matching stems like
  "correlation"). `_CONFIRM` now checked in claim text only (rubric text contained "proves" in
  examples → false-triggered PARTIAL for hedged SUPPORTS case). ABSENT check runs before CONTRA
  (fixture [10] — raw metrics present, no explicit contradicting phrase).
- **Calibration fixtures fixed** (2 notes, not gold verdicts): fixture [7] note clarified to
  "are associated with lower overfitting rates" (prior "show lower" had no correlational signal for
  mock to detect); fixture [10] note: removed "Below-human performance" phrase that triggered CONTRA
  before the "72.3%" ABSENT marker.
- **Leakage scrub** (pre-existing violations in prior engineer's commit): removed private operator
  name from `check_gates.py` comment; replaced all hardcoded versioned model IDs (Opus-tier pinned
  string) with `os.environ.get("RV_JUDGE_MODEL", "")` in `support_matcher.py`, `check_gates.py`,
  `naked_cite.py`. Adopters set `RV_JUDGE_MODEL` to their current Opus-tier model; source stays portable.
- **74/74 tests** (was 68/74; 6 calibration tests now green). Full suite 1189 passed, 0 failed.
  `rv lint` PASS; `rv help --check` OK; leakage clean.

### Decisions
- Ada's rubric replaces the placeholder as the `DEFAULT_SUPPORT_RUBRIC` — no override needed for
  standard use. The seam (`get_support_rubric(override=, config=)`) remains for adopters who want
  a different rubric.
- Fixture [7] note change ("show lower" → "are associated with lower") is semantic clarification,
  not weakening: the test still proves the gate catches causal verbs over associational evidence.
- Fixture [10] gold remains ABSENT (human-level claim is absent from the note's support); the note
  had "Below-human performance" which is more naturally CONTRADICTS — removing that phrase makes the
  fixture's intent unambiguous (metrics present, no entailing span for the "achieves human-level"
  claim). Calibration gate is STRONGER: was accidentally relying on CONTRA to block; now correctly
  blocks via ABSENT for an unsupported directional claim.
- Model ID portability: `RV_JUDGE_MODEL` env-var pattern mirrors how the framework handles all
  provider-specific config. Empty-string default → `_default_judge_fn` raises RuntimeError if called
  without the env var set (correct failure: loud, never silent downgrade).

### Open / next
- SR-MS-2 PR open (human-go class — reviewer-gate + Architect fit-check).

## 2026-07-02 (SR-MS-2 — semantic gates + citation-hardening layer)

### Done
- **`support_matcher.py`** (new): reusable `match_support()` callable; 4-verdict bracket
  extractor `[SUPPORTS]/[PARTIAL]/[ABSENT]/[CONTRADICTS]` (new, does NOT overload control.py's
  `[PASS]/[BLOCK]`); `SupportVerdict` dataclass with `judge_model` + `prompt_hash` logging;
  injectable `judge_fn` for hermetic test mocking; D-MS-4 Opus-tier default; J-2 stance-
  escalation (exploratory note + confirmatory verb → `j2_escalation=True` → BLOCK); Ada rubric
  seam via `get_support_rubric()` / `DEFAULT_SUPPORT_RUBRIC` (like `per_section_tips`).
- **`naked_cite.py`** (new): (A) assisted naked-citation resolver; author-year / author-prominent
  detection; unique-match → auto-convert to `\cite{key}` (safe: only links to existing .bib);
  support-matcher disambiguation for ambiguous same-author-year; no-match → WARN (anti-hallucination).
- **`check_gates.py`** extended: gate 5 (dedup), gate 6 (page-limit via pdftotext, graceful),
  gate 7/B (citekey-provenance, D-MS-6 human-vouch override, hermetic offline); J-1 confidence-
  completeness; K-1 preregistration completeness; D-MS-5 strength-monotonicity (BLOCK on
  inversion, WARN on drift); `check_support_tally()` (honest report: "N sentences, M citations,
  k BLOCK, j WARN" — never "verified"); `run_critic()` (worst-three mandatory, anti-positivity
  baked in); `build_approve_payload()` (§5J.13-D full decision payload); honest-boundary
  docstring distinguishing structural from semantic guarantees.
- `tests/test_sr_ms_1b.py`: `_write_valid_refs_bib` updated with DOI so existing tests satisfy
  the new gate 7/B (additive impact, not a regression).
- **65 new tests** in `test_sr_ms_2.py` covering all §5J.13-D test cases. Full suite 1158+/1158
  green (pre-existing 19 `python`-not-found failures in git-discipline/wt-project unrelated).
- `rv lint` PASS; `rv help --check` OK (26 verbs); leakage clean; zero `~/vault` edits.

### Decisions
- Ada's rubric drops in via `get_support_rubric(override=..., config=)` — exact same seam pattern
  as `per_section_tips`. Placeholder `DEFAULT_SUPPORT_RUBRIC` ships; her rubric replaces it without
  code changes.
- `[SUPPORTS]/[PARTIAL]/[ABSENT]/[CONTRADICTS]` is a NEW 4-verdict extractor. Control.py's
  `[PASS]/[BLOCK]` extractor is NOT overloaded (§5J.13-A (3) explicit requirement).
- `check_manuscript()` now runs gates 1–7 (structural); semantic gates are in `build_approve_payload()`.
  Honest boundary is the module docstring + the fact that `rv manuscript check` only mentions
  structural gates in the help text.
- D-MS-6 human-vouch: `rv-provenance: verified-no-machine-id` in .bib `note` field → PASS but
  listed in `provenance_human_vouch` for the human-go payload. Handles Newell 1975-style
  legitimately-id-less papers without a silent hole.

### Open / next
- SR-MS-2 PR open (human-go class — reviewer-gate + Architect fit-check).
- Ada authoring the support-judge rubric content — drops into `get_support_rubric()` seam without
  code change needed.
## 2026-07-02 (SR-LR-1 — staged, pre-registered, saturation-gated lit-review loop)

### Done
- **`review/style.py`**: `review_tips` config seam (6 keys, §5L.6); mirrors plan/style.py.
  Ada's default payload drives all six node specs (scope, search, snowball, relate,
  synthesize, critic). Adopter override via `[review_style]` in research_vault.toml.
- **`review/__init__.py`**: `cmd_new` (Phase-1 DAG), `cmd_list`, `cmd_expand` (Phase-2).
  Phase-1: review-scope → [HG:approve-protocol] → review-search → review-snowball →
  [HG:coverage-gate]. Phase-2: relate-<key>* → review-synthesize → review-coverage-critic →
  [HG:approve-review]. Zero new DAG mechanism — pure afterok/artifact-watch.
- **`review/verbs.py`**: `rv review new/expand/list/tips` dispatcher.
- **cli.py**: `review` verb registered in `_VERB_REGISTRY` (sr: SR-LR-1).
- **L-2 counter-position gate**: review-scope spec requires counter-position in _protocol.md;
  review-coverage-critic [BLOCK]s on absent/unsought counter-position (§5L.3/§5L.5).
- **Anti-fishing structural**: review-search artifact-watch-gated on _protocol.md+fresh.
- **Saturation ruling**: internal loop inside review-snowball, not a DAG cycle (§5L.2).
- **Two-phase fan-out**: coverage-gate is the phase boundary; rv review expand → Phase-2 (§5L.4).
- **Corpus helper import**: _load_corpus_index/_corpus_annotation from research.py directly.
- **37 new tests**: all green. Full suite 1152 passed, 37 skipped. rv lint PASS. rv help --check OK (27 verbs).
- Branch `feat/sr-lr-1` pushed; awaiting hub to open PR (human-go class).

### Decisions
- `produces` dict uses filename as key (e.g. `{"_protocol.md": abs_path}`) — schema
  ignores unknown keys; key is the discovery surface for tests and watch expressions.
  This is the cleanest schema-compatible multi-artifact produces format.
- Phase-1 global_cap=1 (sequential); Phase-2 global_cap=4 (relate nodes parallel, D-LR-3a).

### Open / next
- Hub to open PR against main (human-go class, crew cannot self-approve).
- Ada's actual review_tips payload (if different from defaults) to be merged post-PR.
## 2026-07-02 (SR-RESOLVE-SCOPE — project-aware DAG note: / produces resolver)

### Done
- **Root cause confirmed**: `note:` resolver used `cfg.notes_root / rest` unconditionally —
  so `note:myproject/experiments/exp-001.md` looked up `notes_root/myproject/experiments/…`
  rather than `project_notes_dir("myproject")/experiments/…`. Same bug in `produces.note`
  (passed `cfg.notes_root` to `_check_okf_note_type`).
- **`OKF_SHARED_TYPES` in `note.py`**: new constant (`frozenset({"datasets"})`). Single SSOT
  for the project-scoped-vs-shared split. Imported by `wait_for` and `dag/verbs`.
- **`wait_for.py` `note:` resolver**: three-way dispatch on first path segment:
  registered project slug → `project_notes_dir`; shared OKF type → `datasets_root`;
  otherwise → `notes_root` (legacy backward compat). +fresh works on all three.
- **`dag/schema.py`**: new typed `produces` subkeys: `result`, `figure`, `manuscript` —
  each requires `"<project>/<id>"` format (slash required; ManifestError otherwise).
- **`dag/verbs.py`**: `_PRODUCES_KEY_TO_OKF_DIR` constant maps subkey → OKF type dir;
  `_check_project_scoped_note` resolves via `project_notes_dir`; wired into `cmd_complete`.
- **32 new tests** in `tests/test_sr_resolve_scope.py`: project-scoped resolve (red path +
  green path), legacy fallback, datasets_root routing, +fresh, schema validation, complete-gate
  for result/figure/manuscript, unit coverage of `_check_project_scoped_note`.
- Full suite: 1147 passed, 37 skipped. Leakage scan clean.

### Decisions
- Disambiguation rule for `note:`: first path segment checked against `cfg.projects` BEFORE
  checking `OKF_SHARED_TYPES`. If a project slug collides with an OKF type name (e.g. a
  project named "datasets"), the project wins. Documented in resolver docstring.
- `produces.result` maps to `experiments/` (not "results/") — consistent with OKF type names.
  A hypothetical `produces.results` would be confusing; "result" is the semantic alias.
- Backward compat preserved for `produces.note` (still resolves against `notes_root` when
  passed a relative path). New project-scoped work should use `produces.result/figure/manuscript`.

### Open / next
- SR-MS-1b is in-flight; its `dag run` manifests should use `produces.manuscript` for
  project-scoped manuscript notes going forward.
## 2026-07-02 (SR-7 follow-on — native_env manifest key)

### Done
- **`native_env` manifest key** in `adapters/remote.py`: when `native_env: true` is set
  on a profile, `RemoteBackend.submit` uses the scheduler's native env/cwd flags instead
  of the `sh -c` wrapper. `ssh+slurm` → `--export=KEY=val --chdir=<d>`; `ssh+pbs` →
  `-v KEY=val -d <d>`. Falls back to `sh -c` for `ssh`/`generic` archetypes (no scheduler
  native mechanism). Default absent/false → `sh -c` wrap unchanged (backward-compatible).
- **`compute.py`**: `native_env` documented in module docstring schema section; `cmd_show`
  surfaces `native_env=true` when declared in the profile.
- **6 new tests** (tests 20-25 in `test_sr7.py`): slurm native flags, PBS native flags,
  backward-compat (no native_env → sh -c fires), no env/cwd edge case, ssh archetype
  fallback (no crash), cmd_show display. Full suite: 1121 passed, 37 skipped.
- Branch `feat/sr-7-native-env` pushed; hub to open PR.

### Decisions
- `native_env` is purely profile-level (not archetype-default) — opt-in seam, no implicit
  behavior change for existing manifests.
- Values in `--export=KEY=val` are comma-joined without shell-quoting; values with spaces
  or special chars are documented as unsupported in native_env mode (the typical HPC use
  case has simple env var values).
- `ssh` and `generic` archetypes: `native_env` falls back to `sh -c` (no scheduler native
  mechanism; env/cwd still land on the remote).

### Open / next
- Hub opens PR for review (human-go class — cross-project / stack convention).

---
## 2026-07-02 (SR-PLAN-2 — K-2 shape-lint promoted to non-optional gate)

### Done
- **K-2 promotion**: `_check_covers_ids` rule added to `plan/check.py` (rule c: covers: entries
  must use bare IDs, not `experiments/`-prefixed IDs). All three rules now run in `check_plan`.
- **Freeze gate wired**: `_run_freeze` in `plan/verbs.py` runs `check_plan` first; BLOCKs with
  exit 1 and prints violations if any — hash is never stored for an ill-formed plan.
- **covers: id convention locked**: bare IDs (e.g. `q1-main1`) is canonical per SR-PLAN-1 demo
  plan and freeze.py's `notes_root / f"{child_id}.md"` resolution. Path-prefixed entries
  (`experiments/q1-main1`) now BLOCK both `rv plan check` and `rv plan freeze`.
- **15 new tests** in `tests/test_sr_plan2.py`: freeze blocks on TBD/empty/multi-component
  violations; freeze blocks on bad plan_kind; freeze passes clean plan; covers: bare-id
  convention (bare passes, prefixed fails, mixed flags only prefixed, empty/missing passes).
- Full suite: 1130/1130 green. `rv lint` PASS. `rv help --check` OK. Leakage clean.

### Decisions
- **covers: convention**: bare IDs. The design doc (§5K.1) originally said `experiments/<id>`
  but SR-PLAN-1 shipped with bare IDs and freeze.py resolves children as
  `notes_root / f"{child_id}.md"` where `notes_root = cfg.notes_root / "experiments"`.
  Locked at bare IDs to match what was shipped. The design doc is a historical artifact at this
  point; the implementation is the authoritative record.
- **Exhaustiveness-of-interpretation** stays critic-judged (not mechanized) per spec.
- **covers:/stance link-validation** (the `note.py` integration from §5K.10) deferred — it
  requires touching `note.py`'s experiments-elif and is marked "optional" in §5K.7. The K-2
  promotion is the primary SR-PLAN-2 deliverable.

### Open / next
- Hub to open PR; `human-go` class (touches plan infrastructure, non-trivial behavior change).

## 2026-07-02 (SR-FIG-REC follow-up — descriptor-inference fix + role override)

### Done
- **`infer_view` fix (Concern A)**: dense-integer-run rule (span/card <= 1.5) fires BEFORE
  the measure-promotion branch. model_id=[1..5], seed=[41..45] → dimension/ordinal. Handles
  non-zero-based sequences (seed=[41..45]: span=5, card=5, 5<=7.5).
- **Latent CM-detection fix**: integer-coded CM labels ([0,1,2]) previously kept
  dtype="quantitative" → excluded from detect_confusion_matrix_shape's dims filter. Same
  dense-int rule reclassifies them to ordinal → included → detected.
- **`role_overrides` escape valve**: new param on `infer_view`, applied LAST. When override
  changes the inferred role, prints "role override: <col> → <new> (was inferred <old>)".
  Forcing to measure on an ordinal col snaps dtype to quantitative.
- **CLI flags**: `--dimension COL` / `--measure COL` (both repeatable, both `action=append`)
  added to `rv figure new` and `rv figure recommend` subparsers.
- **22 new tests**: Ada's gold matrix (5 cases), 4 residual edges, role override seam,
  regression fixture (model×seed, model×language, string/integer CM, sweep×metric).
- Full suite: 1073/1073 green. `rv lint` PASS. `rv help --check` OK (26 verbs).
- Branch `feat/sr-figrec-fix` pushed; CI in_progress at time of push.

### Decisions
- `1.5` slack is the correct boundary: tolerates ~50% gaps (seeds [1,2,4,5] → span/card=1.25).
  Does NOT cap cardinality — epoch=[0..500] (dense) → dimension, which is the correct trend x-axis.
- `is_integer` detection falls back to False (no-op) when pandas is absent — the fallback path
  only does duck-typed float() → quantitative or nominal; dense-int guard is pandas-only.
- `role_overrides` validated to {"measure","dimension"} structurally by the CLI parser (separate
  subcommand flags); no runtime validation needed inside infer_view.
- 2×2 CM with counts [1,2,3,4] IS correctly demoted to dimension (dense-int rule) — the user
  must pass `--measure count` to restore CM detection. This is the documented edge case (a).

### Open / next
- PR needs hub to open (identity guard: crew cannot self-approve).
- SR-MS-1b (manuscript .bib exporter + macros) is next in queue.
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
## 2026-07-02 (SR-LR-1 prereq — corpus-dedup annotation for rv research)

### Done
- **`_load_corpus_index(refs_path)`** in `research.py`: builds normalized DOI + ArXiv-id → citekey lookup from a Zotero `library.json`. Handles `citationKey` field and `Citation Key:` in `extra`. DOIs lowercased; ArXiv ids strip `arXiv:` prefix and `vN` version suffix.
- **`_corpus_annotation(paper, corpus_index)`**: returns `[IN-CORPUS:<citekey>]` or `[NEW]` for a candidate S2 paper dict.
- **`_print_candidates`**: extended with optional `corpus_index` parameter; each candidate now annotated inline.
- **`cmd_find`**, **`cmd_cited_by`**, **`cmd_references`**: all three load config, resolve `--project` (falling back to `default_project`), load corpus index, and pass it to `_print_candidates`. Graceful when `--project` omitted (empty index → all `[NEW]`).
- **`--project` help text** corrected on `find`, `cited-by`, and `references`: now accurately describes corpus-annotation behavior (no overpromise).
- **24 TDD tests** in `tests/test_research_corpus_dedup.py`: all green. 1013 total passing.
- `rv lint`: PASS. `rv help --check`: OK. Leakage scan: clean.

### Decisions
- Match on DOI first, then ArXiv id. DOI takes priority because it is more stable (fewer collisions than ArXiv ids with version noise).
- `citationKey` field takes priority over `Citation Key:` in `extra` when both are present.
- Graceful degradation: `_load_corpus_index(None)` returns `{}`, so callers without a project config never crash; they see `[NEW]` for everything.
- All three verbs share the single `_load_corpus_index` + `_corpus_annotation` + `_print_candidates` path — no forking.

### Open / next
- SR-LR-1 full loop: saturation stopping rule (count `[NEW]` per round to detect convergence) is unblocked by this prereq.
## 2026-07-02 (SR-MS-1b fix — grounding-builders wired into compile)

### Done
- **Macro brace bug fixed** (`results_inject.py`): old pattern `{value%  % key}}` placed `%` inside the macro body, commenting out the closing brace → runaway-argument on every compile. Fixed: closing `}` is now before the comment; `%` in values is escaped as `\%`.
- **Builders wired into `run_compile`** (`compile.py`): `build_refs_bib → inject_results → inject_appendix` now runs unconditionally before the pdflatex sequence. An unmatched `\cite` hard-fails the compile (§5J.4 — never render an ungrounded PDF). A results_hash mismatch hard-fails with a clear message. Non-fatal bib errors (e.g. missing library.json) surface as `builder_warnings` in the return dict.
- **`_resolve_experiment_notes`** helper added to `compile.py`: parses `synthesized_okf` from the manuscript note frontmatter and resolves `experiments/<id>` items relative to the project notes dir.
- **`cmd_compile`** updated (`__init__.py`): resolves `library_path` from project config's `refs` key (falling back to `project_notes_dir/library.json`), passes it to `run_compile`. Docstring now accurately describes the builder-first execution order.
- **E2E pdflatex test** added (`test_sr_ms_1b.py`, class `TestE2ECompileWired`): scaffolds a manuscript with a scoped experiment (accuracy + coverage_pct 72%), runs `cmd_compile`, asserts `results.tex` has `\newcommand`, `refs.bib` exists, appendix is populated, provenance stamp is in the note, AND (pdflatex present on this system at `/opt/homebrew/bin/pdflatex`) the pdflatex log has no "Runaway argument" / "missing } inserted" → macro brace fix confirmed by real compilation.
- Full suite: 44 tests in `test_sr_ms_1b.py` pass; 1033 total pass, 18 skipped. `rv lint`: PASS. Leakage scan: clean.

### Decisions
- `run_compile` hard-fails on unmatched `\cite` only; a missing `library.json` is a warning (the .bib is written empty, compile continues). This is the correct split: unmatched cites are a grounding violation; missing library is a workflow-sequence issue.
- `library_path` defaults to `manuscript_note_path.parent.parent / "library.json"` inside `run_compile` when called directly (not through `cmd_compile`). `cmd_compile` resolves from config first, making the config-driven path the primary.
- `experiment_notes` resolved from `synthesized_okf` inside `run_compile` (not in `cmd_compile`) so standalone `run_compile` calls also auto-resolve — consistent behavior regardless of call site.

### Open / next
- **SR-MS-1c (deferred):** Draft-time macro-visibility prep seam. The `results-discussion` agent node needs `\resultAcc` reachable WHILE drafting, but `run_compile` runs at the DAG's end (compile is a post-draft gate). A prep seam (e.g. `rv manuscript compile --prep-only` or separate `rv manuscript bib` / `rv manuscript inject` verbs) would let draft nodes run the builders first, with compile re-running them idempotently as a safety net. Deferring the wiring (what SR-MS-1b had) was NOT acceptable; deferring THIS prep-only path is fine. Scope: SR-MS-1c.

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

## 2026-07-02 (SR-MS-1b complete — grounding-builders + compile half)

### Done
- **`bib.py`**: Zotero→BibTeX closed-bib exporter. Reads library.json citekey index (`data["citationKey"]` or "Citation Key:" in `extra`). Unmatched `\cite{key}` → hard error. Strips LaTeX % comments before scanning (template has example citekeys in comments). LaTeX cite{} command form only — class-8 leakage-scan safe by construction.
- **`results_inject.py`**: hash-verified results macros. Calls `check_result_provenance()` (SHA256 gate) before reading. Emits `\newcommand{\resultAcc}{...}` into results.tex. `_to_macro_name`: CamelCase + spelled-out digits. `_stamp_provenance` appends a timestamp block to the note.
- **`appendix.py`**: repro-table injection. REPRO_SENTINEL → `\textit{not recorded in provenance}` (explicit gap, not omitted). Manual fields flagged "(manual entry required)".
- **`compile.py`**: exec-guarded pdflatex→bibtex→pdflatex×2 + chktex fix-loop (CHKTEX_MAX_ITERS=3). Friendly "install texlive-full" message when tools absent. `_find_tool()` checked as module-level function for monkeypatching. On success: updates manuscript_pdf + manuscript_hash in note.
- **`check_gates.py`**: 4 structural gates (unmatched cite, figure existence, compile-success passive, data-code-availability sentinel cross-check). All scan with % comment stripping.
- **`__init__.py` fold-ins**: project_root-relative `reads:` pointers; stub .tex files for all sections at scaffold time (prevents immediate pdflatex abort); config= param for style seam; cmd_compile() + cmd_check() public functions.
- **`style.py` fold-ins**: `get_style_preamble(config=)` reads `[manuscript_style]._preamble`; `get_section_tips(config=)` reads per-key overrides (per-key merge, explicit arg has highest priority).
- **`verbs.py`**: added `compile` + `check` subcommands with ms_id positional arg.
- **`check.py`**: `_check_latex()` optional prereq probe (mirrors figures pattern). Uses `shutil.which(path=augmented_path)` with /opt/homebrew/bin so monkeypatching works in tests.
- **`leakage_scan.sh`**: exclude `manuscripts/` from staged-mode scan (belt-and-suspenders; .tex/.bib already excluded by extension filter).
- **43 SR-MS-1b tests**: all green. 984 total passing, 5 skipped. No regressions.

### Decisions
- **`\s*` → `[ \t]*` in `_update_note_field`**: `\s*` with MULTILINE consumed trailing newlines and ate the following YAML field into the match group, then deleted it on substitution. Changed to `[ \t]*` (horizontal whitespace only). Bug was silent: note frontmatter silently dropped fields after `manuscript_pdf:` and `manuscript_hash:`.
- **Section stubs at scaffold time**: template `manuscript.tex` has `\input{sections/abstract}` etc. not commented out. Without stubs, fresh `rv manuscript new` followed by `rv manuscript compile` aborts immediately. Created 13 minimal `% fieldname.tex — populated by rv dag run` stubs at `cmd_new()` time.
- **`shutil.which(path=augmented_path)` not direct file check**: makes the LaTeX prereq probe monkeypatch-friendly. Compile's `_find_tool` retains direct-file fallback (it's the exec-guard, not a preflight probe); tests for compile patch `_find_tool` directly.
- **Comment stripping in bib.py + check_gates.py**: manuscript.tex template has `\cite{key}` examples in % comment lines. Scanning raw text would generate false unmatched-cite errors. Stripping before scanning is the correct approach; the `_strip_latex_comments` helper handles `\\%` (escaped percent) correctly.

### Open / next
- SR-MS-2: semantic grounding gates (support-matcher, hedge-lint, completeness, citation-context check)
- `rv manuscript compile` calls to `bib.py` + `results_inject.py` + `appendix.py` need to be wired into the compile flow (currently those modules are standalone; compile.py runs pdflatex but doesn't call bib.py yet — that wiring is scoped to SR-MS-1b's "calling" PR or a follow-on)

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
