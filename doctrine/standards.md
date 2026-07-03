# The bar — quality standards

What "good" looks like, enforced two ways: **continuously** by linters and **deeply** by
review passes. Each manager contract restates these; a project's profile decides which apply.

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

## Test discipline (code profile)

Grounded in real regressions caught in review — the disciplines that keep a green suite honest:

- **Test against the repo's REAL merge model.** A git-merge-aware tool's acceptance test must exercise
  the merge strategy the repo actually uses. A terminal-detection tool tested only under `--no-ff` while
  the repo squash-merges exclusively ships a dead path behind a green suite.
- **Non-vacuous assertions.** A tautological assertion (`assert X or True`, `assert True`) lets a headline
  path ship unverified. Every test asserts a condition that can *fail*.
  → `rv lint` flags `or True` / `assert True` in tests (shipped). **Candidate extension:** the `... in
  inspect.getsource(...)` guard-smell is AST-detectable (same machinery as the F811 rule) — a report-only
  pattern flag, not a vacuity *proof* (the linter can't know a comment happens to satisfy the string).
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

## How it's enforced

- **Continuous & deterministic** — `rv lint` runs cheap structural checks at the end of sync:
  weak claims, harness coverage where code is in scope. Report-only, never blocks.
- **Deep & on demand** — a headless review pass (read-only, report mode) returns a ranked report
  you judge. Nothing is auto-applied.
- **In the contract** — each manager's contract restates the bar with its project specifics.
