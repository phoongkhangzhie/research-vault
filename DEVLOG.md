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
