---
type: gaps
id: q1-gap-main2
gap_type: coverage_void
anchor: reviews/q1-lit-review/_search_hits
claim: "Does fine-tuning-data language diversity predict downstream cross-lingual F1, independent of prompt language?"
why: "the pre-loop lit review surfaced this as a thin facet — few papers isolate fine-tuning diversity as a predictor independent of prompting strategy; the Q1 pre-registration plan (experiments/q1-plan.md) targets this gap with Exp 2 (main 2)"
status: open
suggested_route: experiment
detected: 2026-07-12
---

# Gap: q1-gap-main2

**Type:** coverage_void
**Anchor:** reviews/q1-lit-review/_search_hits
**Status:** open

## Claim (verbatim)

> Does fine-tuning-data language diversity predict downstream cross-lingual
> F1, independent of prompt language?

## Why it is a gap

The pre-loop literature review's width-sweep surfaced this facet as
under-covered — too few distinct papers isolate fine-tuning-data language
diversity as an independent predictor of downstream cross-lingual F1
(as opposed to conflating it with prompting strategy). The Q1
pre-registration plan (`experiments/q1-plan.md`) targets this gap
directly: Exp 2 (main 2, `experiments/q1-main2.md`) is pre-registered to
close it.

## Provenance

This gap is grounded in the corpus's thin coverage of the fine-tuning-
diversity facet — see `reviews/q1-lit-review/_search_hits.md` (fixture
note; this demo project does not ship a full lit-review scope, so no
`_search_hits.md` artifact is materialized here — the `anchor:` field
records where a real project's would live).

## Attribution

Gap types AND identification procedure: Müller-Bloch & Kranz (2015, ICIS)
six-gap framework. `coverage_void` is rv's own extension — the lit-review
loop's own facet-coverage record (thin poles), not a Müller-Bloch & Kranz
type.
