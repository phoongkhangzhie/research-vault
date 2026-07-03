---
type: experiments
citekey: q1-plan
title: "Q1 Pre-Registration Plan — Cross-lingual transfer study"
plan_kind: preregistration
covers: [q1-main1, q1-main1-abl-A, q1-main1-cabl-Y, q1-main2, q1-main2-abl-B, q1-main2-cabl-Z]
status: draft
---

# Q1 Pre-Registration Plan — Cross-lingual transfer study

**plan_kind:** preregistration
**covers:** q1-main1, q1-main1-abl-A, q1-main1-cabl-Y, q1-main2, q1-main2-abl-B, q1-main2-cabl-Z
**Freeze gate:** human-go-plan (K-3 covers:-hash stored on approval)

This plan covers two first-class main experiments (Exp 1 and Exp 2) and their
committed ablation + conditional sets.  All confirmatory child notes listed in
`covers:` are written as stubs before any run fires.  The confirmatory set is
frozen at `human-go-plan`; exploratory experiments may be added after the
freeze with `stance: exploratory`.

---

## Exp 1 — Main 1 (claim A): prompt-language drives cross-lingual accuracy

### Claim arrow

`prompt_language=target → accuracy_on_target_lang` under condition
`model=multilingual-LM, eval_set=XNLI-balanced`.

The exact manipulation: switch the instruction prompt from English to the
evaluation language (target-language prompting).  The specific outcome: NLI
accuracy on the balanced XNLI held-out split, macro-averaged across 15 languages.

### Pre-registered analysis

- **Estimand:** macro-averaged NLI accuracy (15 languages, XNLI balanced held-out split).
- **Test statistic:** paired t-test, English-prompt baseline vs. target-language-prompt
  condition, N=500 per language.
- **Comparison baseline:** English-prompt condition on same eval set (frozen in
  experiments/q1-main1.md `baseline_condition` field at run dispatch).
- **Units:** accuracy points (0–1 scale).
- **Decision threshold:** Δacc ≥ +0.03 (absolute) on the held-out split.
- **Noise floor:** seed variance estimated at ±0.005 from 3 pilot seeds
  (committed value; do not adjust post-hoc).

### Falsifier

A result of Δacc < +0.01 (below noise floor) on the held-out split would
refute the claim.  A result of Δacc ∈ [+0.01, +0.03) is ambiguous — see
diagnosis table below.

### Planned artifact

- Run note: `experiments/q1-main1.md`
- Results file: `results/q1-main1/scores.jsonl`
- Figure: `figures/q1-main1-accuracy-by-lang.pdf`
- SHA: to be filled at run dispatch by Ada.

### Main 1 Diagnosis Table

| Outcome range | Named conclusion | Committed action |
|---|---|---|
| Δacc ≥ +0.03 | Prompt language drives accuracy: claim A supported | Proceed to write-up; report as confirmatory main result |
| Δacc ∈ [+0.01, +0.03) | Effect present but below threshold: claim A inconclusive | Report inconclusive with honest threshold language; no overclaim |
| Δacc < +0.01 (≤ noise floor) | No detectable effect: claim A refuted | Reject claim A; report null; trigger ablation re-review |
| Δacc < 0 (reversal) | Target-language prompting hurts: mechanism misspecified | Reject claim A; report reversal; investigate in exploratory track |

---

## Exp 1 — Supporting Ablation A: isolates prompt language (removes instruction style)

**Purpose:** rule out that accuracy differences are driven by instruction
*style* (formal vs. informal) rather than language.  If removing instruction
style while keeping target language does NOT reduce the main effect, language
is the operative factor.

Component manipulated: instruction style (register variation within target-language prompts).

### Ablation A Diagnosis Table

| Outcome range | Named conclusion | Committed action |
|---|---|---|
| Effect maintained (Δacc same as main 1) | Instruction style is not the driver; language is operative | Main claim A interpretation stands; report style-null ablation |
| Effect reduced but positive (< main1 Δacc) | Style partially contributes | Report partial confound; qualify claim A; add exploratory style investigation |
| Effect eliminated (near zero) | Style is the driver, not language | Reject claim A's mechanistic interpretation; redesign as style study |

### Planned artifact

- Run note: `experiments/q1-main1-abl-A.md`
- Results file: `results/q1-main1-abl-A/scores.jsonl`
- SHA: to be filled at run dispatch.

---

## Exp 1 — Conditional Ablation Y: fires if main 1 acc > 0.80

**Trigger (frozen):** `q1-main1 results_summary > 0.80` (post-analysis
macro-averaged accuracy on the held-out split, as read from
`experiments/q1-main1.md` `results_summary` field).

**Purpose (pre-committed):** if main 1 shows unexpectedly high accuracy
(> 0.80), investigate whether the effect generalizes to low-resource languages
(bottom 5 languages by XNLI size), which may have been swept into the
macro-average.

**Trigger false → recorded as `blocked`** (pre-committed, trigger false,
deliberately not run).

Component manipulated: language subset (full 15 vs. bottom-5 low-resource).

### Conditional Y Diagnosis Table

| Outcome range | Named conclusion | Committed action |
|---|---|---|
| Low-resource acc also > 0.80 | Effect generalizes: not a high-resource artefact | Report full generalization; cite conditional as confirmatory evidence |
| Low-resource acc ∈ [0.65, 0.80) | Partial generalization | Report qualified generalization; add exploratory low-resource investigation |
| Low-resource acc < 0.65 | High accuracy is high-resource artefact | Qualify claim A: "primarily high-resource languages"; report in limitations |
| Trigger did not fire (main acc ≤ 0.80) | Conditional did not apply | Record as blocked; report "conditional not triggered" in methods |

### Planned artifact

- Run note: `experiments/q1-main1-cabl-Y.md` (stub; filled if triggered)
- Results file: `results/q1-main1-cabl-Y/scores.jsonl`
- SHA: to be filled if triggered.

---

## Exp 2 — Main 2 (claim B): chain-of-thought prompting improves cross-lingual reasoning

### Claim arrow

`prompt_style=chain-of-thought → f1_score_on_XNLI_reasoning` under condition
`model=multilingual-LM, eval_set=XNLI-reasoning-subset`.

Exact manipulation: switch standard single-step instruction to chain-of-thought
instruction (3-step reasoning chain, fixed template).  Specific outcome: macro-
averaged F1 on the XNLI reasoning subset (5 inference-heavy language pairs,
held-out split).

### Pre-registered analysis

- **Estimand:** macro-averaged F1 (5 inference-heavy language pairs, XNLI
  reasoning subset, held-out split).
- **Test statistic:** paired t-test, standard-prompt baseline vs. CoT condition,
  N=200 per language pair.
- **Comparison baseline:** standard-prompt condition (frozen at run dispatch).
- **Units:** F1 score (0–1 scale).
- **Decision threshold:** ΔF1 ≥ +0.04.
- **Noise floor:** seed variance estimated at ±0.008 from 3 pilot seeds.

### Falsifier

ΔF1 < +0.01 (below noise floor) refutes claim B.

### Planned artifact

- Run note: `experiments/q1-main2.md`
- Results file: `results/q1-main2/scores.jsonl`
- Figure: `figures/q1-main2-f1-by-pair.pdf`
- SHA: to be filled at run dispatch.

### Main 2 Diagnosis Table

| Outcome range | Named conclusion | Committed action |
|---|---|---|
| ΔF1 ≥ +0.04 | CoT drives cross-lingual reasoning: claim B supported | Proceed to write-up; report as confirmatory main result |
| ΔF1 ∈ [+0.01, +0.04) | Effect present but below threshold: claim B inconclusive | Report inconclusive; no overclaim |
| ΔF1 < +0.01 (≤ noise floor) | No detectable effect: claim B refuted | Reject claim B; report null |
| ΔF1 < 0 (reversal) | CoT hurts cross-lingual reasoning | Reject claim B; report reversal; investigate failure modes in exploratory track |

---

## Exp 2 — Supporting Ablation B: isolates chain structure (removes reasoning scaffold)

**Purpose:** rule out that F1 gains are driven by increased prompt length
rather than chain structure.  Length-matched baseline without explicit
reasoning chain.

Component manipulated: reasoning chain structure (chain-of-thought steps vs. length-matched filler).

### Ablation B Diagnosis Table

| Outcome range | Named conclusion | Committed action |
|---|---|---|
| Main 2 effect maintained with length-matched baseline | Chain structure is operative; length is not the driver | Main claim B interpretation stands |
| Main 2 effect reduced with length-matched baseline | Length partially contributes | Report partial confound; qualify claim B |
| Main 2 effect eliminated | Length is the driver, not chain structure | Reject claim B's mechanistic interpretation |

### Planned artifact

- Run note: `experiments/q1-main2-abl-B.md`
- Results file: `results/q1-main2-abl-B/scores.jsonl`
- SHA: to be filled at run dispatch.

---

## Exp 2 — Conditional Ablation Z: fires if main 2 f1 > 0.75

**Trigger (frozen):** `q1-main2 results_summary > 0.75`.

**Purpose:** if claim B shows high F1 (> 0.75), investigate whether the
improvement is driven by a single dominant language pair or is evenly
distributed.  Per-pair decomposition.

Component manipulated: evaluation scope (full 5 pairs vs. per-pair single-pair runs).

### Conditional Z Diagnosis Table

| Outcome range | Named conclusion | Committed action |
|---|---|---|
| Effect evenly distributed (all pairs ≥ 0.70) | Robust generalization: not a single-pair artefact | Report full generalization |
| Effect dominated by ≤ 2 pairs (others < 0.65) | Generalization limited: single-language-pair driver | Qualify claim B: "driven by specific language pairs" |
| Effect absent in all but one pair | Claim B is language-pair specific | Reject cross-lingual claim; restrict to that pair in write-up |
| Trigger did not fire (main F1 ≤ 0.75) | Conditional did not apply | Record as blocked |

### Planned artifact

- Run note: `experiments/q1-main2-cabl-Z.md` (stub; filled if triggered)
- Results file: `results/q1-main2-cabl-Z/scores.jsonl`
- SHA: to be filled if triggered.
