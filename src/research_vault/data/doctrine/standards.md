# The bar — quality standards

<!-- SSOT: this file (doctrine/standards.md in the research-vault repo) is the source of
truth. It is mirrored manually to ~/vault (the live Astro site) — that mirror carries drift
risk. If you find divergence, treat this file as correct and update the mirror. A cross-repo
CI check is not feasible from the OSS repo; drift-guard is a ~/vault-side follow-up. -->

What "good" looks like, enforced two ways: **continuously** by linters and **deeply** by
review passes. Each project's control file restates these; a project's profile decides which apply.

## Every project

- **Grounding.** Every specific — a number, a name, a claim — traces to a real source. Never
  fabricate to sound concrete. This cross-cuts everything below.

## Research / notes profile

- **No weak claims.** A claim with no evidence to illustrate it — no citation, no number, no
  example, no figure — is a *weak claim*. Every assertion carries evidence, and notes are written
  to **render**: diagrams, numbers, figures, not just prose.
  → `rv lint` flags weak claims continuously; a deep review pass goes further.
- **Consistency + currency.** The literature stays internally consistent and current. Periodic
  review catches contradictions between notes, stale citations, and superseded results.

## Code profile

- **Harnesses, not just tests.** Every feature gets a harness that **tests _and_ stress-tests** —
  engineered against *silent failure*, tiered and cost-aware (cheap deterministic checks first,
  expensive semantic checks gated). A green test that can't fail loudly is theatre.
- **Harness hygiene — maintain, don't just create.** Harnesses rot. With every feature or change,
  reconcile them: **update** for new behaviour, **delete** what's dead, **translate / merge** into
  a better harness. No stale or orphaned harnesses — one test covering code that no longer exists
  is worse than none.
  → Review audits coverage gaps · stale harnesses · stress-test gaps.
- **Grep all callsites before extending a widely-used signature.** Before adding a REQUIRED
  parameter to an existing function or command, grep all callsites first — they are predictable
  and will break silently if missed. Before widening a return type (e.g. scalar → list), count
  the callers that assume the old shape: a `.strip()` call on an expected-scalar field throws
  when it receives a list. The callsite audit is a pre-condition, not an afterthought. A safe
  pattern: add the new parameter with a default first (backward-compatible), land it, then
  update callers in a follow-up.

## Test discipline (code profile)

Grounded in real regressions caught in review — the disciplines that keep a green suite honest:

- **Test against the repo's REAL merge model.** A git-merge-aware tool's acceptance test must exercise
  the merge strategy the repo actually uses. A terminal-detection tool tested only under `--no-ff` while
  the repo squash-merges exclusively ships a dead path behind a green suite.
- **Non-vacuous assertions.** A tautological assertion (`assert X or True`, `assert True`) lets a headline
  path ship unverified. Every test asserts a condition that can *fail*.
  → `rv lint` flags `or True` / `assert True` in tests (shipped). The `... in inspect.getsource(...)`
  guard-smell is also AST-detected (rule 7, shipped alongside the F811 rule) — a report-only flag, not a
  vacuity *proof* (the linter cannot know a comment happens to satisfy the string; see the source-introspection
  guard rule below for the AST-based remedy).
- **Hermetic fixtures pin their environment assumptions.** "Passes locally, fails CI" is the signature of
  an unpinned assumption. A fixture that `git init`s without pinning the initial branch inherits the
  runner's default and breaks on a different one — pin it. An environment-sensitive check declares its
  environment in the fixture.
- **Exit-code contracts are tested on the `run()` / argv path, not just the helper.** When non-zero-on-
  failure is what makes a tool composable into a hook or CI, a test must invoke the real dispatcher and
  assert the exit code — a correct-by-construction helper is not proof the contract holds end-to-end.
- **A guard must fail when the thing it guards is broken.** The sibling of the vacuous assertion is the
  *guard that looks-like-it-checks but doesn't* — green by construction, protecting nothing. Prove
  non-vacuity by **reverting the guarded code and confirming the guard goes RED** (pre-image replay).
  Two forms bite in practice, each with a remedy:
  - **Source-introspection guards must strip comments (AST), never raw-substring `inspect.getsource`.**
    `assert "X" in inspect.getsource(fn)` is satisfied by a *comment or docstring* mentioning `X`, not by
    live code — a routing guard passed after its logic was reverted to a hardcoded string, because the
    explanatory comment still named the symbol. Use `ast.get_source_segment` / walk the actual node, and
    prefer asserting the **negative** (the bad pattern is *absent* from the live code).
  - **A cross-module mirror of an SSOT consumes the SSOT directly, or carries a drift-guard test.** A
    hand-copied frozenset mirroring `note.OKF_TYPES ∪ OKF_SHARED_TYPES` guarded only by a keep-in-sync
    *comment* silently drifts when the SSOT grows — and the thing that drifts is itself a safety guard.
    Import the SSOT, or add an equality test asserting `mirror == source`. A comment is not a guard.
- **Help and docstrings must describe what the code actually does — a false help is a §1 fabrication,
  not a doc nit.** The documentation-side sibling of "a guard must fail when the thing it guards is
  broken" (above): a verb's `when_to_use`, its `--help` text, and any behaviour-claiming docstring are
  a promise to the adopter. A help string that claims a behaviour the code does not perform is a
  **grounding violation** (charter §1 — never fabricate; a false help fabricates capability aimed at the
  person deciding whether to trust the tool), not cosmetic. The motivating case: SR-MS-1b's
  `rv manuscript compile` help + the `inject_results` / `inject_appendix` docstrings claimed "builds
  `.bib` from `library.json`, injects results macros" while the builders were **orphaned** (defined,
  never called). Both #27 gates caught it — but `rv help --check` passed green, because it verifies a
  `when_to_use` is *present*, not that it is *true* (`_check_verb_docstrings`, cli.py). **Prove-it
  discipline (review-checklist item):** on any PR that touches a verb's help/docstring or the behaviour
  it describes, the reviewer reads the help *against the code path* and confirms every claimed behaviour
  is actually reached — help-vs-behaviour, not help-present.
- **A module that emits an artifact through an external TOOLCHAIN requires an exec-guarded,
  real-toolchain end-to-end test — string assertions on generated markup are not a sufficient gate.**
  When a module shells to LaTeX / `pdflatex` / `bibtex` / `chktex` (or any external renderer) to produce
  an artifact, a test that only asserts substrings in the *generated source* verifies the string-builder,
  not the artifact — the toolchain never runs, so a macro the renderer rejects (or a builder that is
  never called) ships green. Require at least one test that, **guarded on the tool being present**
  (`shutil.which` / skip-if-absent), runs the real toolchain end-to-end and asserts the artifact is
  produced. SR-MS-1b shipped two green-but-empty defects — a macro-brace bug *and* the orphaned
  `.bib` / results builders — precisely because no test ran `pdflatex`. Exec-guard so CI without the
  toolchain skips cleanly; never let the string-only assertion be the sole gate.
- **A packaging SR requires an isolated-install acceptance test.** Any SR that ships data files,
  doctrine, or resources inside the wheel MUST carry a test that (a) builds the wheel, (b) installs
  it into a fresh venv, (c) runs the command from OUTSIDE the repo tree, and (d) asserts real content
  is returned — not a skeleton or placeholder. This test must provably FAIL on the unfixed code. An
  editable / in-repo test silently passes on broken loaders: `__file__`-relative paths resolve in
  dev but not in a wheel, so the loader is dead while CI stays green. The SR-PKG silent-skeleton bug
  (doctrines shipped as empty skeletons while in-repo tests stayed green) arose exactly here. Companion
  rule: use `importlib.resources.files(pkg) / "data" / ...` with an `as_file()` context — never
  `__file__` or repo-root paths — and remove any skeleton-dir fallback that would mask a load miss
  (charter §2: a silent degradation is worse than a loud failure).
- **Consumers bind to structured contracts, never prose renders.** When a module consumes another
  module's output, it MUST read the producer's structured contract (dataclass fields, meta-dict keys,
  JSON schema) — never re-grep the human-readable prose render. A regex extractor over free-form text
  breaks on format drift and silently green-and-empties (charter §2): the input is non-empty, the parse
  returns zero results, and nothing warns. Two corollaries:
  - **Non-empty-input guard.** Any extractor that reads structured data MUST emit a loud warning when
    the input is non-empty but the parse returns zero records. Zero-from-non-empty is almost never
    correct and is the signature of a missed connection.
  - **Verbatim field names.** When binding a producer's structured contract, read the producer's
    serializer field names VERBATIM. A structured contract is only as good as both sides agreeing on the
    exact keys. Verification move: grep the producer's serializer field names against the consumer's
    reads before shipping. (SR-LR-2: the absent-row detector re-grepped the prose render and silently
    returned `[]` on all real inputs because it named fields that existed in the prose format, not in the
    upstream dataclass contract.)
- **When mutation-testing Python, run each mutation in its own process invocation — or bust `__pycache__`
  between runs.** Same-second `.pyc` mtime staleness causes `.pyc` files written by one mutant to satisfy
  the mtime check for a subsequent mutant in a batched run: the old bytecode is re-used, the mutation
  effectively does not execute, and the mutant spuriously "survives" (false pass). Run each mutation as
  a subprocess, OR delete `__pycache__` between mutations. Confirmed empirically on PR #80 (reviewer
  gate caught it during acceptance tests).
- **An extracted helper is only done when the pre-existing caller uses it.** An SR whose deliverable
  is "extract Phase-X as a reusable function" is incomplete if the pre-existing path still contains a
  parallel copy of the same logic. An extracted-but-uncalled copy is a silent charter-§6 regression CI
  stays green through — both copies are currently correct, so no test fails — but the duplication is
  live and will diverge. This is worse when the duplicated block is a safety contract. Review fit-check
  probe: confirm the pre-existing caller now CALLS the new helper; an independent copy is a failed
  extraction regardless of how clean the new function is.

## How it's enforced

- **Continuous & deterministic** — `rv lint` runs cheap structural checks at the end of sync:
  weak claims, harness coverage where code is in scope. Report-only, never blocks.
- **Deep & on demand** — a headless review pass (read-only, report mode) returns a ranked report
  you judge. Nothing is auto-applied.
- **In the control file** — each project's control record restates the bar with its project specifics.
