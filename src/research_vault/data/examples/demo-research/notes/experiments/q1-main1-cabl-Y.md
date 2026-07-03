---
type: experiments
citekey: q1-main1-cabl-Y
title: "Q1 Exp 1 conditional ablation Y — low-resource language generalization (fires if main1 acc > 0.80)"
stance: confirmatory
plan_role: conditional_ablation
preregistration: experiments/q1-plan
supports_main: experiments/q1-main1
status: stub
trigger: "q1-main1 results_summary > 0.80"
trigger_result: ''
results_location: results/q1-main1-cabl-Y/scores.jsonl
results_hash: ''
results_commit: ''
results_summary: ''
---

# Q1 Exp 1 conditional ablation Y — low-resource language generalization

**Trigger (frozen):** `q1-main1 results_summary > 0.80`
**Component manipulated:** language subset (full 15 vs. bottom-5 low-resource).

**If trigger did not fire:** record this node as `blocked` via
`rv dag complete research-loop-q1 q1-main1-cabl-Y-run --status blocked`.
Report in methods: "pre-committed conditional not triggered (main acc ≤ 0.80)."

**stance:** confirmatory
**plan_role:** conditional_ablation
**preregistration:** experiments/q1-plan
**supports_main:** experiments/q1-main1

---

## Protocol (stub — filled if triggered)

- Language subset: bottom 5 by XNLI training size
- All other settings held fixed as in q1-main1

## Results (filled post-run if triggered)

- trigger_result: (macro acc of q1-main1, as read at conditionals gate)
- results_location: results/q1-main1-cabl-Y/scores.jsonl
- results_hash: _
- results_summary: _
- results_commit: _
