---
type: literature
citekey: smith2024
title: A Framework for Retrieval-Augmented Reasoning
created: 2026-01-15
distilled: 2026-01-15
read_basis: full-text
year: 2024
venue: NeurIPS
authors: J. Smith, A. Lee
doi: 10.9999/example.smith2024
arxiv_id: 2401.00001
pmcid:
openalex:
pmid:
s2:
contribution_kind: method
result_reported: yes
key_equations:
  - label: eq:retrieval-score
    critical: true
repo: https://github.com/example/smith2024-rar
artifacts:
  - "checkpoint: https://example.invalid/artifacts/smith2024-ckpt"
---

## Result

The paper reports a 4.2-point accuracy gain over a dense-retrieval-only
baseline on the benchmark suite (Table 3), attributed to the interleaved
retrieval-then-reason loop rather than retrieval alone (ablation, Table 4).

## Key equations

### [eq:retrieval-score] Interleaved retrieval score  *(critical)*

$$ s(q, d) = \lambda \cdot \mathrm{sim}(q, d) + (1 - \lambda) \cdot \mathrm{rel}(q, d, r) $$

The reasoning-conditioned term $\mathrm{rel}(q, d, r)$ is what the paper's
central claim turns on (Section 3.2) — a plain dense-similarity baseline
(the $\lambda = 1$ special case) is the ablated comparison in Table 4.

## Related papers

- [jones2023](/literature/jones2023.md) — EXTENDS: extends jones2023's
  static retrieval index with the interleaved reasoning loop above.

## Concept edges

- [retrieval-augmented-reasoning](/concepts/retrieval-augmented-reasoning.md) — SUPPORTS: the paper's central mechanism is this review's organizing concept.
