---
name: research-pipeline
description: >
  Drive the research-vault lit-review→manuscript pipeline end to end: turn a
  research question into a clean, ledgered, canonically-keyed corpus and then a
  synthesized survey manuscript. This skill orchestrates the two built-in `rv`
  DAG loops (lit-review and manuscript) — ticking the frontier, dispatching each
  agent node with its canonical brief, fanning out cold judges for the gates,
  and surfacing the human-go approvals. Use it whenever the user wants a
  literature review, a survey/related-work section, a systematic search of a
  field, "find and synthesize the work on X", "build me a corpus on Y", or "turn
  this corpus into a survey" — even if they don't name research-vault or a DAG.
  If the task is a multi-step literature review or survey and `rv` is available,
  this is the driver; reach for it rather than hand-rolling ad-hoc searches.
---

# research-pipeline — drive the rv lit-review → manuscript loop

You are the **orchestrator**. You do not do the science in one pass — you drive a
DAG: `rv` walks a frozen graph of nodes, you dispatch a fresh subagent for each
agent node, resolve the gates, and surface the human approvals. The engine owns
the topology and the artifacts; you own dispatch, verification, and the human
handoffs. This is what makes a review *reproducible* instead of a one-shot answer.

## The two loops

| Loop | Turns | Produces |
|------|-------|----------|
| **lit-review** | a research question → a vetted corpus | `_corpus.md`, `_corpus_ledger.md`, `literature/*.md` notes, `concepts/*.md` |
| **manuscript** | a corpus → a synthesized survey | `_report.md` (source), `report.md` (rendered `[N]`+Sources), `references.bib` |

The lit-review loop can **emit** the manuscript loop automatically. At
`review-scope` the review proposes a `deliverable: review | manuscript`; the human
confirms or flips it at `approve-protocol`. On `deliverable: manuscript`, reaching
`approve-review` auto-emits and starts the manuscript tree — you do not hand-run
`rv manuscript new`. On `deliverable: review` (the default), the review is
**terminal**: the vetted corpus is itself the knowledge artifact.

Lit-review topology (frozen; do not reorder):
```
review-scope → [HG: approve-protocol] → review-search → review-screen →
review-snowball → review-relevance-screen → review-curate →
review-relevance-verify-prep → review-relevance-verify → coverage-gate →
(Phase-2, auto-emitted) relate-* → review-synthesize →
review-coverage-critic → approve-review
   → [if deliverable=manuscript] EMITS manuscript loop
```
Manuscript topology:
```
new --type <type> → (Phase-1: scope → framework-lens-<L> ×N (fan-out) →
framework-synthesize → framework-critic → approve-framework, auto-resolved) →
(Phase-2, auto-emitted) → section(s) → assemble → 6-lens board (cold fan-out) →
approve-manuscript (auto-resolved)
```
(`expand` no longer exists as a hand-run verb for either loop — Phase-2 auto-emits
when the upstream gate GOes.)

## Bootstrap

Before driving anything:

1. **Confirm `rv` is available and you are running the intended version.** A crew
   that executes a stale installed pin instead of the intended code validates the
   wrong thing. If you are validating a specific build, confirm the interpreter
   resolves that source (`python -c "import research_vault, subprocess; ..."` /
   `git rev-parse`), not a lagging `.venv`.
2. **Scaffold the run** if it doesn't exist yet:
   - lit-review: `rv review <project> new <scope> --question '...'` → writes
     `reviews/<scope>/phase1-dag.json`.
   - manuscript (standalone, corpus already exists):
     `rv manuscript <project> new <slug> --type <type>` → writes
     `manuscripts/<slug>/phase1-dag.json` for a type with a Phase-1 (`lit-review`
     does); Phase-2 (`phase2-dag.json`) auto-emits when `approve-framework` GOes.
3. **Start the DAG run:** `rv dag run <path-to>/phase1-dag.json` — validates the
   manifest, writes run-state, prints the initial frontier.

## How you drive it — the tick loop

The whole job is a loop over `rv dag tick`:

```
rv dag tick <run>      # re-walk the frontier: marks dispatchable nodes,
                       # surfaces human-go nodes, reports blocked/done
rv dag status <run>    # render the graph: frontier / running / ⏸ awaiting-go / done
```

For each node the tick surfaces, route by its kind:

- **Agent node** (search, screen, snowball, curate, synthesize, coverage-critic,
  framework-lens, draft, …): get its canonical brief with
  `rv dag brief <run> <node>` and dispatch a **fresh subagent** with that brief
  **verbatim** (see the spawn contract). When it returns, record the result:
  `rv dag complete <run> <node> --status ok|fail|changes [--output k=v]`, then
  `tick` again to advance the frontier.
- **Judge / gate node** (relevance verify, the 6-lens board, relate): these run
  as a **cold fan-out**, not an in-line call — see "Gates run as cold fan-outs".
- **Human-go node** (`approve-protocol` and the autonomous `approve-*` gates):
  surface the decision to the human — see "The gates".

**Pass the brief verbatim.** `rv dag brief` emits the node's exact `spec:` plus a
`FRESH | CONTINUES` mode line. Paraphrasing silently drops the stop-rules, exact
commands, and artifact-shape contracts the brief encodes — the drafter then guesses
its output path or skips a step. Dispatch the spec as given.

## Gates run as cold fan-outs, never a direct API judge

Every gate that renders a *verdict* — the relevance verifier, the 6-lens review
board, the relate edge-judge — runs as a **harness cold fan-out**: you emit a set
of judge tasks, spawn fresh no-context subagents over them, and ingest their
structured verdicts. Never a hand-rolled `api.anthropic.com` call and never a
warm self-judge.

```
rv review <project> judge-emit    <scope>                    # write the counter-facet judge-task set
# → spawn a fresh subagent per emitted task (cold, no thesis-anchoring)
rv review <project> judge-ingest  <scope>                     # fold the structured verdicts back
rv manuscript <project> judge-emit   <slug> [--gate support-matcher]   # support-matcher task set
rv manuscript <project> judge-ingest <slug> [--gate support-matcher]
rv manuscript <project> board-emit   <slug> [--round N]        # 6-lens board task set
```

A relied-on verdict is trustworthy only when it is **cold** (fresh subagent),
**rejects-only** (a pass never certifies — only a rejection is signal),
**fail-closed** (a missing or malformed verdict → fail; a missing verdict *set* →
HALT, never a silent proceed), and **canary-verified** (an unmarked, id-keyed
probe in the task set must come back classified correctly, proving the judge
actually read). If the canary is misclassified, the gate HALTs — a rubber-stamp is
caught, not waved through.

## The gates (what each decides)

- **`approve-protocol`** — the one true human gate. It is an *anti-fishing* lock:
  the protocol must declare a **counter-position** (the disconfirming literature to
  actively seek) before search fires, so the corpus can't be quietly shaped to a
  thesis. Surface the protocol; do not tick past it without the human.
- **relevance gate** (at screen + a cold verify before `coverage-gate`) — drops
  high-confidence off-domain papers; auto-prunes below the off-domain HALT
  threshold, HALTs above it (too much off-domain → search/curate is broken, tell
  the human). Backward-snowball runs hot on off-domain by nature — that's the
  gate's job, not a failure.
- **`coverage-gate`** (autonomous) — authorizes the expensive Phase-2 relate
  fan-out only when the corpus is accounted for. It writes `_corpus_ledger.md`,
  the provenance ledger that makes the corpus COMPLETE / CLEAN / CANONICALLY-KEYED
  auditable from one artifact.
- **framework-coverage** (at `approve-framework`) — every corpus paper must be
  allocated a place in the manuscript spine (used / clustered / deferred-with-
  reason) *before* a section is drafted. Coverage is a framework-stage contract,
  not something the review catches later.
- **the 6-lens board** (before `approve-manuscript`) — cold reviewers on distinct
  lenses (depth, coverage/width, synthesis-vs-recitation, self-containment,
  adversarial, instruction) VERIFY the synthesized manuscript. The board is a
  backstop: if it routinely catches coverage/synthesis gaps, the *framework* node
  is broken, not the board.

The autonomous `approve-*` gates resolve on the DAG's own evidence
(`rv dag approve <run> <node> [--reject] [--note]`), but they are still decisions —
report what they resolved on; never tunnel an approval a subagent refused.

## Synthesize, don't assemble

The manuscript's value is one mind re-writing the whole corpus in its own voice —
not a concatenation of independently-drafted sections. Enforce it upstream: the
framework allocates **every** paper to the spine (no silent drops — cross-check the
cited count against the ledger's accepted count), and the draft briefs demand the
prose *argue over* the paper→paper relations rather than *recite* typed edges. The
reader-facing `report.md` carries `[N]` citations + a hermetic `## Sources`; the
`[[citekey]]` source lives in `_report.md` and is what the gates and board read —
never feed the rendered `report.md` to a gate.

## Subagent spawn contract

Every dispatched node subagent MUST carry:

1. **The node's `rv dag brief` spec, verbatim** (+ its `FRESH | CONTINUES` mode).
2. **Its declared inputs and `produces:` path.** A node whose artifact a
   downstream node consumes must write to the exact declared path — an undeclared
   or guessed output path is a silent split-brain that breaks `assemble`.
3. **The standing contract:** judge on **substance** (abstract / full text), never
   id+title alone; surface rejects and uncertain items, never silently drop;
   return self-contained (the result in the message); and for anything it writes,
   ground every claim to a real source (page/citekey), never fabricate a specific.

Correcting a running node: a mid-flight message that reverses its brief is rejected
as injection — instead `rv dag complete <node> --status changes` (or re-dispatch)
and let the frontier re-issue it.

## Recovery (picking up mid-run)

Run-state lives on disk, so a compaction or a fresh session recovers cleanly:

- `rv dag status <run>` — the authoritative frontier / running / awaiting-go / done.
- The run's artifact directory shows the highest completed stage (`_corpus.md`
  exists → curate done; `_corpus_ledger.md` exists → coverage-gate ran;
  `_report.md` exists → drafting started).
- `rv dag tick <run>` re-derives the next actions from state — never re-run a
  completed node from memory; trust the frontier.

## Final integrity gate

A loop is done only when `rv dag status <run>` shows the terminal node complete AND
the human has cleared every human-go gate. Before treating a manuscript as
finished: every corpus paper is allocated (count vs ledger), the board PASSED cold,
the rendered `report.md` has zero residual `[[citekey]]` and a resolving `## Sources`,
and the methods trace to `_corpus_ledger.md` (not re-derived). A corpus is done when
`_corpus_ledger.md` reports `ledger_complete: true` at GO.

## Now begin

Determine where you are: no run yet (bootstrap — scaffold + `rv dag run`), or a run
in progress (`rv dag status` to recover the frontier). Then loop: `rv dag tick` →
for each surfaced node, route by kind (agent → brief+dispatch+complete; judge →
emit+cold-fanout+ingest; human-go → surface) → `tick` again. Keep going until the
terminal node is complete and the human has approved. Report each node's state
plainly; when a gate HALTs or a judge rejects, say so with the evidence.
