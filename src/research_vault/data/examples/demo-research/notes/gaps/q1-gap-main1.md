---
type: gaps
id: q1-gap-main1
gap_type: coverage_void
anchor: reviews/q1-lit-review/_search_hits
claim: "Does prompt-language causally drive cross-lingual accuracy, or is the correlation confounded by training-data language balance?"
why: "the pre-loop lit review surfaced this as a thin facet — few papers directly isolate prompt-language as the causal driver rather than an observed correlate; the Q1 pre-registration plan (experiments/q1-plan.md) targets this gap with Exp 1 (main 1)"
status: open
suggested_route: experiment
detected: 2026-07-12
---

# Gap: q1-gap-main1

**Type:** coverage_void
**Anchor:** reviews/q1-lit-review/_search_hits
**Status:** open

## Claim (verbatim)

> Does prompt-language causally drive cross-lingual accuracy, or is the
> correlation confounded by training-data language balance?

## Why it is a gap

The pre-loop literature review's width-sweep surfaced this facet as
under-covered — too few distinct papers directly isolate prompt-language as
a causal driver of cross-lingual accuracy, as opposed to an observed
correlate confounded by training-data balance. The Q1 pre-registration
plan (`experiments/q1-plan.md`) targets this gap directly: Exp 1 (main 1,
`experiments/q1-main1.md`) is pre-registered to close it.

## Provenance

This gap is grounded in the corpus's thin coverage of the causal-driver
facet — see `reviews/q1-lit-review/_search_hits.md` (fixture note; this
demo project does not ship a full lit-review scope, so no `_search_hits.md`
artifact is materialized here — the `anchor:` field records where a real
project's would live).

## Attribution

Gap types AND identification procedure: Müller-Bloch & Kranz (2015, ICIS)
six-gap framework. `coverage_void` is rv's own extension — the lit-review
loop's own facet-coverage record (thin poles), not a Müller-Bloch & Kranz
type.
