# The review board

The board makes adversarial critique **cheap and rigorous on demand**: point it at a real
artifact, and it returns *ranked, verified, actionable* feedback — not confident noise.

## The run

You run it on an **artifact**, not a project in the abstract: an experiment design, a
paper-draft section, a code diff, a finding, a figure. Lenses are tailored to the
project's profile.

1. **Convene** — pick the lenses (below) that fit the artifact.
2. **Fan out** — one agent per lens, each pointed at the artifact in its project context.
3. **Verify before reporting** — every candidate finding faces independent refuters;
   **majority-refute kills it.** This is the step that makes the board worth having.
4. **Dedupe & rank** — merge overlaps; order by severity × confidence.
5. **Post to the bus** — write the survivors to the project's `CONTROL.md` under **Open
   findings**, and roll them up to the cross-project board view.

## The lens library

**Always-on:** *the strongest counterargument* (steel-man the opposite of your claim).

**Research profile:** methodology & stats rigor (circularity, contamination, ceiling
violations, p-hacking, n) · reproducibility & figure provenance · construct validity (does
the measure measure the claim?) · anti-circularity (were gates set before seeing data?).

**Product profile:** correctness/bugs · security · performance · UX & copy · data integrity.

**Benchmark profile:** leakage/contamination · metric soundness · baseline fairness ·
contaminated-numbers-excluded.

## The finding schema

Each survivor, atomic:

> **#R\<id\>** · severity (blocker / strong / minor) · confidence (after verification)
> **Claim.** What's wrong, in one line.
> **Where.** File/section/run it's in.
> **Why it survives.** What the refuters tried and failed to dismiss.
> **Fix.** The concrete next action.

## The board view

A cross-project rollup tracks what critique is still unresolved across all work, with status
(open / addressed / **dismissed-with-reason**). Findings don't evaporate — they sit on
the board until you resolve them.

## Boundaries

The board exists to find where you're **wrong**, never to validate you. It reports;
**the operator** judges and decides. An unverified finding never reaches the operator — that's the contract.
