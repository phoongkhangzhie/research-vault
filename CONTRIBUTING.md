# Contributing to Research Vault

Thanks for your interest. Research Vault is a young project with a strong point of
view: **the discipline is the product.** Contributions are welcome, but the bar is
that a change must *keep the disciplines honest* — a feature that makes fabrication
easier, or lets a gate be waved through, will be declined no matter how convenient.

## The doctrine-first ethos

The disciplines (anti-fabrication, every-outcome-is-a-finding, verify-the-artifact,
pre-registration, human-only approval, crew-cannot-self-approve) live in
`src/research_vault/data/doctrine/`. They are shipped as package data and read by
the agents at work time.

**A change to a discipline is a doctrine change.** If your PR alters how a gate
behaves, what a role may do, or what counts as "verified," update the doctrine in
the same change and say so explicitly in the PR description. Do not route around a
discipline in code while leaving the doctrine stating the old rule — that drift is
exactly what the project exists to prevent.

## Development setup

Research Vault targets **Python 3.12+** and uses [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/phoongkhangzhie/research-vault.git
cd research-vault
uv sync                     # create the environment + install dev deps (pytest)
uv run rv --help            # confirm the CLI resolves
```

If you are working from a bare install and need the research toolkit:

```bash
rv bootstrap                # creates an isolated .venv and installs the toolkit
rv check                    # verify the tier coverage matrix
```

## The test suite

```bash
uv run pytest               # the full suite
uv run pytest -m slow       # isolated wheel build+install+run smoke tests (slower)
```

Test discipline (enforced by `rv lint`, see below — the project is strict about it):

- **No vacuous assertions.** `assert True` / `or True` are flagged — a tautology
  that always passes masks the bug it was meant to catch.
- **Stress the code, not the docstring.** Asserting against `inspect.getsource(fn)`
  is flagged — it passes even when the live code is broken, because the symbol may
  survive only in a comment.
- **Pin your branch in test git-inits.** `git init` without `--initial-branch` is
  flagged — it passes locally and fails on master-default runners.

New behavior needs a test that *and* stress-tests it. An LLM-judged gate needs a
blind-judge canary (a known-outcome probe) so a mis-calibrated judge can't silently
rubber-stamp — see `data/doctrine/honesty-gates.md`.

## The lint / leakage gates

Before opening a PR, run:

```bash
rv lint                     # leakage scan + config schema + test-hygiene + doc links
```

`rv lint` runs the **leakage gate** — the most important check for a public repo.
The package must contain **zero private markers**: no absolute home-directory paths,
no personal names, no internal codenames, no secrets. The forbidden-pattern list is
config-driven, not compiled in. A leak is a blocking failure, not a footnote.

> **Note on CI.** Automated CI/Actions may be disabled on this repo at times. When
> it is, `rv lint` + the leakage scan + `uv run pytest` are the gates you run
> **locally before every PR** — treat a red local gate exactly as you would a red
> CI: fix it first, do not build on top of it.

## Commit & PR conventions

- **Conventional commits.** Use `type: summary` (`feat:`, `fix:`, `docs:`,
  `refactor:`, `test:`, …). The git-discipline hooks (`rv git-discipline install`)
  enforce the format, protect `main`, and run a leakage scan on commit.
- **Commit as you go.** Commit incrementally as work lands — do not hold a large
  uncommitted working tree hostage to one final commit. Work is recoverable only
  once it is committed.
- **Work on a branch / worktree, never on `main`.** `rv wt add <task>` creates an
  isolated worktree on `feat/<task>`.
- **Keep the change scoped.** One concern per PR. Update the `DEVLOG.md` with the
  decisions you made, not just what you did.

## The review discipline

Research Vault distinguishes two merge classes, and **nobody merges on their own
authority**:

- **reviewer-gate** — a change is merged only after an independent reviewer verdict
  *and* a green local gate.
- **human-go** — stack-wide / cross-project / public-facing changes require a human
  operator's explicit go, in addition to review.

**Crew agents cannot self-approve.** In an agent-driven workflow, the human is the
second party — an approval an agent could not obtain from a person must never be
tunneled through another agent. This is enforced mechanically: the approval gate
keys on an interactive terminal, and a dispatched agent has none.

## Reporting issues

File issues with a minimal reproduction and the output of `rv check`. For anything
touching a discipline or a gate, describe the *honesty property* you believe is at
risk — that framing helps triage faster than a feature label.
