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
