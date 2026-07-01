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
  → candidate `rv lint` / CI rule: flag `or True` / `assert True` in tests.
- **Hermetic fixtures pin their environment assumptions.** "Passes locally, fails CI" is the signature of
  an unpinned assumption. A fixture that `git init`s without pinning the initial branch inherits the
  runner's default and breaks on a different one — pin it. An environment-sensitive check declares its
  environment in the fixture.
- **Exit-code contracts are tested on the `run()` / argv path, not just the helper.** When non-zero-on-
  failure is what makes a tool composable into a hook or CI, a test must invoke the real dispatcher and
  assert the exit code — a correct-by-construction helper is not proof the contract holds end-to-end.

## How it's enforced

- **Continuous & deterministic** — `rv lint` runs cheap structural checks at the end of sync:
  weak claims, harness coverage where code is in scope. Report-only, never blocks.
- **Deep & on demand** — a headless review pass (read-only, report mode) returns a ranked report
  you judge. Nothing is auto-applied.
- **In the contract** — each manager's contract restates the bar with its project specifics.
