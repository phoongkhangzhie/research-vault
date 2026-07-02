---
type: experiments
citekey: q1-main2-cabl-Z
title: "Q1 Exp 2 conditional ablation Z — per-pair decomposition (fires if main2 f1 > 0.75)"
stance: confirmatory
plan_role: conditional_ablation
preregistration: experiments/q1-plan
supports_main: experiments/q1-main2
status: stub
trigger: "q1-main2 results_summary > 0.75"
trigger_result: ''
results_location: results/q1-main2-cabl-Z/scores.jsonl
results_hash: ''
results_commit: ''
results_summary: ''
---

# Q1 Exp 2 conditional ablation Z — per-pair decomposition

**Trigger (frozen):** `q1-main2 results_summary > 0.75`
**Component manipulated:** evaluation scope (full 5 pairs vs. per-pair single-pair runs).

**If trigger did not fire:** record as `blocked` via
`rv dag complete research-loop-q1 q1-main2-cabl-Z-run --status blocked`.

**stance:** confirmatory
**plan_role:** conditional_ablation
**preregistration:** experiments/q1-plan
**supports_main:** experiments/q1-main2

---

## Protocol (stub — filled if triggered)

- Run the CoT vs. standard-prompt comparison separately for each of the 5
  language pairs
- All other settings held fixed as in q1-main2

## Results (filled post-run if triggered)

- trigger_result: (macro F1 of q1-main2 as read at conditionals gate)
- results_location: results/q1-main2-cabl-Z/scores.jsonl
- results_hash: _
- results_summary: _
- results_commit: _
