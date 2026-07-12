---
type: literature
citekey: jones2023
title: Static Dense Retrieval Indices at Scale
description: A pre-built dense index that scales sub-linearly in query latency up to 50M documents, but cannot incorporate reasoning-time context.
created: 2026-01-10
distilled: 2026-01-10
read_basis: full-text
year: 2023
venue: ACL
authors: R. Jones
doi: 10.9999/example.jones2023
arxiv_id:
pmcid:
openalex:
pmid:
s2:
contribution_kind: empirical
result_reported: yes
key_equations:
repo:
artifacts:
---

## Result

Establishes that a static, pre-built dense index scales sub-linearly in
query latency up to 50M documents (Figure 2), but the paper's own
discussion (Section 6) flags that a static index cannot incorporate
reasoning-time context — exactly the gap smith2024 (below) targets.

## Key equations

<!-- No pivotal equation this paper's argument turns on. -->

## Related papers

<!-- Bidirectional physical write — see the sibling edge in smith2024.md's -->
<!-- "## Related papers" section ((c)2: shipped 0.3.0 writes both sides). -->
- [smith2024](/literature/smith2024.md) — EXTENDS: smith2024 extends this
  paper's static index with an interleaved retrieval-reasoning loop.

## Concept edges

- [retrieval-augmented-reasoning](/concepts/retrieval-augmented-reasoning.md) — PARTIAL: the paper this review's organizing concept is defined against.
