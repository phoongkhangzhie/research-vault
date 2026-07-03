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

## Proving a check has teeth (reviewer technique)

A gate, scanner, or test PR must be shown to *add* teeth, not merely to be present — via **pre-image
replay**: run the check against the exact state it claims to catch.
- **New test:** revert the single file under test to its previous version (`git show <prev>:path`) and
  confirm the test now *fails* — proving it covers the real gap, not a relabel of an existing pass.
- **New scanner/gate rule:** run the **pre-change** scanner over planted violating content and confirm it
  *passes* (no teeth before) while the new rule *catches* it (teeth after). A rule that only fires on
  content the old rule already caught adds nothing.
- **Conservative-posture / FP-guard PR:** when a PR introduces a gate designed to *suppress* false
  positives (a narrowing condition, a conservative filter, a cost guard), the mutation that proves the
  gate has teeth is the **opposite** of the usual revert — **widen the signal or neuter the narrowing
  condition**, and confirm the gate's own test goes RED. The reviewer must NAME the exact mutation and
  SHOW the failure. This converts "the test asserts X is accepted" into evidence that "the test CATCHES
  not-X being passed through." Without this, a conservatively-scoped gate looks correct while protecting
  nothing against the false signals it was designed to suppress.

## The verdict header — gate-clean by construction

A reviewer verdict carries a rich narrative that may quote "FAIL" / "BLOCK" from its own pre-image-replay
proof. But the approve-gate reads only a short negation-veto window at the top — so a narrative negation
there blocks a legitimate PASS. **A verdict leads with a one-line, negation-free `PASS` / `BLOCK` header,
then a blank line, then the narrative.** The header is the machine-readable gate signal; the narrative is
for the human.

_Tool half:_ `rv control return` emits the negation-free `PASS`/`BLOCK` header by construction (SR-CI),
so a reviewer cannot accidentally author a verdict whose narrative negation trips the approve-gate.

## The board view

A cross-project rollup tracks what critique is still unresolved across all work, with status
(open / addressed / **dismissed-with-reason**). Findings don't evaporate — they sit on
the board until you resolve them.

## Boundaries

The board exists to find where you're **wrong**, never to validate you. It reports;
**the operator** judges and decides. An unverified finding never reaches the operator — that's the contract.
