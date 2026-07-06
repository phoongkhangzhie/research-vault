---
type: experiments
citekey: q1-main2
title: "Q1 Exp 2 main 2 — chain-of-thought prompting improves cross-lingual reasoning"
stance: confirmatory
plan_role: main
preregistration: experiments/q1-plan
status: stub
results_location: results/q1-main2/scores.jsonl
results_hash: ''
results_commit: ''
results_summary: ''
---

# Q1 Exp 2 main 2 — chain-of-thought prompting improves cross-lingual reasoning

**Claim arrow:** `prompt_style=chain-of-thought → f1_score_on_XNLI_reasoning`
under `model=multilingual-LM, eval_set=XNLI-reasoning-subset`.

**stance:** confirmatory
**plan_role:** main
**preregistration:** experiments/q1-plan

---

## Protocol (stub)

Fill in at run dispatch:

- Prompt: 3-step CoT instruction (fixed template, frozen at plan time)
- Eval subset: XNLI reasoning subset (5 inference-heavy language pairs)
- Baseline: standard single-step instruction (same 5 pairs)
- N=200 per language pair
- Seed: _

## Results (filled post-run)

- results_location: results/q1-main2/scores.jsonl
- results_hash: _
- results_summary: (macro-averaged F1, 5 pairs)
- results_commit: _
