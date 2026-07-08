---
type: experiments
citekey: q1-main2-abl-B
title: "Q1 Exp 2 ablation B — isolates chain structure (removes reasoning scaffold)"
stance: confirmatory
plan_role: supporting_ablation
preregistration: experiments/q1-plan
supports_main: q1-main2
status: stub
results_location: results/q1-main2-abl-B/scores.jsonl
results_hash: ''
results_commit: ''
results_summary: ''
---

# Q1 Exp 2 ablation B — isolates chain structure (removes reasoning scaffold)

**Component manipulated:** reasoning chain structure (chain-of-thought steps vs.
length-matched filler without explicit reasoning).

**Purpose:** rule out that F1 gains in main 2 are driven by prompt length
rather than chain structure.

**stance:** confirmatory
**plan_role:** supporting_ablation
**preregistration:** experiments/q1-plan
**supports_main:** q1-main2

---

## Protocol (stub)

Fill in at run dispatch:

- Baseline: length-matched prompt without CoT steps (same token count as CoT
  prompt, filled with neutral filler text)
- All other settings held fixed as in q1-main2

## Results (filled post-run)

- results_location: results/q1-main2-abl-B/scores.jsonl
- results_hash: _
- results_summary: _
- results_commit: _
