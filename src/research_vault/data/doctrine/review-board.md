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

## Harness PR checklist (mandatory — mock/live isolation and experiment-scoping)

Any PR that introduces or modifies an experiment harness **must pass the checklist in
[harness-contract.md](harness-contract.md) before it can merge.** The checklist covers:

- **Mock/live resume isolation (§1)** — separate output directories, `run_mode`-keyed
  resume records, loud abort when a `--live` run encounters a mock-tagged record.
- **Experiment-scoping (§2)** — `run --exp <exp>` filters to only that experiment's arms;
  suspiciously-complete sanity check halts before over-dispatching.

These checklist items are **non-skippable**; a reviewer who sees a harness PR without the
required isolation test returns **needs-work** regardless of other findings.

---

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

## Reviewer technique — call-graph verification (false-SSOT)

For any PR that claims "function X is the SSOT used by A and B," **grep that both A and B
literally call X** before trusting the claim.  A helper that is only called from one side, but
whose docstring says "called from BOTH," is a **false SSOT** — the duplication is live and will
diverge silently.  Never infer call-graph from docstrings; verify it from the actual code.

```
# Concrete verification move
grep -n "_helper_name" src/module_a.py src/module_b.py
```

Both files must show a call-site, not just a comment or import.  If only one side calls it, the
extraction is incomplete and the PR is **needs-work** regardless of how clean the new function is.
(Grounded in PR #96: `resolve_produces_paths` docstring claimed SSOT parity with
`_check_project_scoped_note`; the AST test in `test_dag_brief.py::TestSSOT` is the mechanized guard.)

## Reviewer technique — PR-diff base (three-dot, not two-dot)

When examining what a PR changes, always diff against the **merge base**, not the current tip of
the target branch:

```bash
# Correct — three-dot: A...B diffs from the common ancestor of A and B
git diff main...head
# or
gh pr view <pr> --json files

# Wrong — two-dot: A..B diffs from the current tip of A, which may have moved
git diff main..head   # false-flags deletions when main advanced past the branch point
```

A two-dot diff against a moved `main` false-flags files that were deleted on `main` after the
branch was cut as "deleted by this PR."  This is a noisy distraction that can bury real findings.
Always use `git diff <base>...<head>` (three dots) or `gh pr view --json files`.
(Grounded in PR #94 review cycle.)

## The verdict header — gate-clean by construction

A reviewer verdict carries a rich narrative that may quote "FAIL" / "BLOCK" from its own pre-image-replay
proof. But the approve-gate reads only a short negation-veto window at the top — so a narrative negation
there blocks a legitimate PASS. **A verdict leads with a one-line, negation-free `PASS` / `BLOCK` header,
then a blank line, then the narrative.** The header is the machine-readable gate signal; the narrative is
for the human.

_Tool half:_ `rv control return` emits the negation-free `PASS`/`BLOCK` header by construction (SR-CI),
so a reviewer cannot accidentally author a verdict whose narrative negation trips the approve-gate.

## LLM-judged gates: blind-judge canary (SR-MS2-FIX)

An LLM-judged gate is probed on a **known-positive** before its verdicts are trusted — silent-[ABSENT]-when-blind is indistinguishable from a real refutation. If the probe returns [ABSENT], the gate ABORTS loudly; the BLOCKs it would have emitted are NOT real refutations and must not be treated as such. A gate that always blocks is as broken as one that always passes.

## The board view

A cross-project rollup tracks what critique is still unresolved across all work, with status
(open / addressed / **dismissed-with-reason**). Findings don't evaporate — they sit on
the board until you resolve them.

## Manuscript gates are orthogonal — do not double-penalize

The manuscript pipeline has three distinct LLM-judged gates: **support-matcher** (are individual
claims grounded in a structured note?), **cold-read** (do references resolve for a stranger?), and
the **review-board** (is the paper good enough — sound + reproducible at the floor?). These gates
are **orthogonal by construction** and each enforces a different contract:

- A raw hash or filesystem path dumped in prose is a **cold-read leak** (blocks on [DANGLING]).
  The review-board rubric (C5) deliberately declines to credit it as reproducibility apparatus —
  but also does **NOT** further penalize it on the REPRO dimension. The cold-read gate already
  blocks it; the review-board's REPRO score measures the positive presence of scholarly
  reproducibility apparatus (seeds, configs, availability statement), not the absence of leaks.
- A claim that has already been blocked by the support-matcher for lacking a grounding note is
  **not re-litigated** on Soundness. The review-board Soundness score is the methodological-rigor
  layer ON TOP of that upstream guarantee: is the design valid, are comparisons fair?

**Rule:** when assessing a manuscript's gates, never count the same deficiency in two gates.
Each gate enforces its own contract; a finding belongs to exactly one gate.

## Boundaries

The board exists to find where you're **wrong**, never to validate you. It reports;
**the operator** judges and decides. An unverified finding never reaches the operator — that's the contract.
