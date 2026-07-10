---
type: experiments
citekey: q1-main1
title: "Q1 Exp 1 main 1 — prompt-language drives cross-lingual accuracy"
stance: confirmatory
plan_role: main
preregistration: experiments/q1-plan
status: stub
results_location: results/q1-main1/scores.jsonl
results_hash: ''
results_commit: ''
results_summary: ''
---

# Q1 Exp 1 main 1 — prompt-language drives cross-lingual accuracy

**Claim arrow:** `prompt_language=target → accuracy_on_target_lang` under
`model=multilingual-LM, eval_set=XNLI-balanced`.

**stance:** confirmatory (frozen in covers: at human-go-plan)
**plan_role:** main
**preregistration:** experiments/q1-plan

---

## Protocol (stub)

Fill in at run dispatch:

- Model checkpoint: _
- Eval set split: XNLI balanced held-out, N=500 per language
- Languages: 15 (XNLI standard set)
- Baseline condition: English-prompt (freeze value filled here)
- Seed: _

## Results (filled post-run by Ada)

- results_location: results/q1-main1/scores.jsonl
- results_hash: (SHA-256, filled by `rv wandb pull` or manual)
- results_summary: (macro-averaged accuracy, filled post-run)
- results_commit: (git commit SHA at scoring time)

## Reproduction (filled post-run by the researcher)

- repro_cmd: _
- repro_env: _
- repro_seed: _
