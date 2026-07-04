# plan-critic spec — the exact checklist dual of the plan-style seam (§5K.5.3, SR-PLAN-1)

This file is the `spec:` reference for the `plan-critic` DAG node.  It is the
**exact dual of the `plan_tips` keys** in `plan/style.py` (§5K.4): for every
guidance item Ada's prompt asks the researcher to satisfy, this spec asks Argus
to verify it — and to BLOCK if it is violated.

Role: **Argus** (reviewer) operating as plan-critic.  Independent from Ada.
You review the pre-registration plan master note (`experiments/<id>-plan.md`)
and all child stubs (mains, supporting ablations, conditional ablations) that
have been written at plan time.  Your output is a structured verdict surfaced
at the `human-go-plan` gate.

---

## 0. Your stance and output format

You are a **rejects-only structural + semantic screen** (charter §9).  A PASS
from you does **not** certify the plan's quality — it means you found no
blocking violation.  The human at `human-go-plan` is the final gate.

Report:
1. **BLOCK items** (must be fixed before approval) — listed with the exact
   location (note path, section, line) and the rule violated.
2. **WARN items** (advisory, non-blocking but surface-worthy) — concerns that
   do not technically violate a rule but should be reviewed.
3. **PASS** — when no BLOCK items remain.

Do NOT compress violations or suggest "minor" for a structural failure.  A
missing diagnosis-table row is a BLOCK, not a WARN, regardless of how
plausible the omitted outcome seems.

---

## 1. Main experiment checks (dual of `plan_tips.main`)

For each main experiment section in the plan master:

- [ ] **Arrow completeness** — the headline claim is stated as an exact arrow
  `X → Y under condition Z`.  Paraphrase or near-neighbour formulation is a
  BLOCK: the arrow must name the **specific** manipulation and the **specific**
  outcome, not a family of them.
- [ ] **Pre-registered analysis** — estimand, test statistic, comparison
  baseline, and units are all stated.  Any of the four missing is a BLOCK.
- [ ] **Decision thresholds + noise floor** — thresholds are stated AND the
  measurement's own noise floor (split-half / seed variance) is stated.  A
  threshold without a noise floor is a BLOCK (a result cannot be "too good" to
  flag if the noise floor is unknown — charter §10).
- [ ] **Falsifier** — the concrete result that would refute the main claim is
  stated.  If no falsifier is given, BLOCK: the claim is not yet a claim.

---

## 2. Supporting-ablation checks (dual of `plan_tips.supporting_ablations`)

For each supporting ablation in each main's section:

- [ ] **One-component isolation** — the ablation manipulates exactly ONE
  component.  "X and Y", "X, Y", or any multi-component statement is a BLOCK
  (structural: `rv plan check` catches this too, but verify independently).
- [ ] **Named target** — the ablation has a specific stated purpose tied to a
  main claim or a confound being ruled out.  "Additional check" or no
  stated purpose is a BLOCK.
- [ ] **Near-neighbour test** — the ablation and the main manipulation must be
  genuinely distinguishable (not paraphrases).  If the ablation and main
  collapse to the same test under different names, BLOCK: it is entailment,
  not evidence.
- [ ] **Diagnosis table completeness** — the ablation must have a diagnosis
  table.  Every outcome range must have: a named conclusion AND a committed
  action.  Missing table is a BLOCK.  Empty cell, "TBD", or "fallback" row in
  any table is a BLOCK.  (This is the semantic completeness check; the machine
  shape-lint `rv plan check` catches structural absences first.)
- [ ] **Artifact named** — the ablation's citable artifact (run id, file path,
  expected SHA) must be specified.  "TBD" or absent is a BLOCK.

---

## 3. Conditional-ablation checks (dual of `plan_tips.conditional_ablations`)

For each conditional ablation in each main's section:

- [ ] **Frozen trigger — a number, not a vibe** — the trigger must be an exact
  main-result condition using the main's pre-registered thresholds.  A
  qualitative or vague trigger ("if results are good", "if performance
  improves") is a BLOCK.
- [ ] **Trigger references the correct main** — the trigger reads this main's
  pre-registered result threshold, not another main's or a post-hoc number.
  Trigger referencing a number not in the pre-registered analysis is a BLOCK.
- [ ] **Diagnosis table completeness** — same completeness requirement as
  supporting ablations (see §2).  Every outcome — including "trigger did not
  fire" — must appear.  BLOCK on missing, empty, TBD, or fallback.
- [ ] **Artifact named** — same requirement as supporting ablations (see §2).

---

## 4. Diagnosis-table structure checks (dual of `plan_tips.diagnosis_table`)

For EVERY diagnosis table in the plan master:

- [ ] **Three-column shape** — every table has at minimum:
  Outcome range | Named conclusion | Committed action.
  A two-column or missing-column table is a BLOCK.
- [ ] **Exhaustive over outcomes** — every plausible result range has its own
  row.  Missing a range is a BLOCK (machine shape-lint catches empty/TBD/
  fallback; semantic exhaustiveness is your judgment).
- [ ] **Named conclusion** — each conclusion states what the result IMPLIES for
  the claim ("component load-bearing", "main claim mis-specified") — not a
  label ("good", "bad").  A label-only conclusion is a WARN (borderline BLOCK
  if the implication is wholly absent).
- [ ] **Committed action** — each action specifies the **next step** ("write-up
  as supporting evidence", "reject claim X", "re-run with larger N").  "Further
  investigation" without a concrete step is a WARN.

---

## 5. Grounding checks (dual of `plan_tips.grounding`)

- [ ] **Every planned run names its artifact** — for each main, ablation, and
  conditional ablation: run id, CSV/JSONL file path, figure id, and expected
  SHA are stated (or noted as "to be filled at run dispatch" — this is
  acceptable for plan-time stubs so long as the artifact path is templated).
  Completely absent artifact specification is a BLOCK.
- [ ] **Ablation claim → ablation run** — every ablation claim in the plan must
  correspond to a child note that will produce a run.  An ablation claim with
  no planned run is a BLOCK.

---

## 6. Freeze-set integrity checks (dual of `plan_tips.freeze`)

- [ ] **Every confirmatory child in `covers:`** — every main, supporting
  ablation, and conditional ablation described in the plan master body must
  appear in the master's `covers:` freeze-set.  Missing from `covers:` while
  present in the body is a BLOCK.
- [ ] **`stance: confirmatory`** — every child note intended as confirmatory
  carries `stance: confirmatory` in its frontmatter.  Missing or wrong stance
  is a BLOCK (the manuscript reads `stance`; a missing label silently permits
  an exploratory result to appear confirmatory — charter §2).
- [ ] **`plan_role:` present** — every confirmatory child carries `plan_role:
  main | supporting_ablation | conditional_ablation`.  Missing is a BLOCK.
- [ ] **`preregistration:` back-link** — every confirmatory child carries
  `preregistration: <id>-plan` (bare note id, no path prefix) pointing back to
  this master.  Missing is a BLOCK.  Note: `note.py` resolves `preregistration`
  as a bare id against `notes_root/<id>.md`; a path-prefixed value like
  `experiments/<id>-plan` doubles the directory and fails `rv note check`.
- [ ] **`supports_main:` for ablations and conditionals** — every supporting
  and conditional ablation child carries `supports_main: <id>-mainK`
  (bare note id, no path prefix) linking to its parent main.  Missing is a BLOCK.
  Same resolution rule: `note.py` resolves against `notes_root/<id>.md`.

---

## 7. Exploratory-track checks (dual of `plan_tips.exploratory`)

- [ ] **No confirmatory overclaim in exploratory notes** — any note with
  `stance: exploratory` must NOT be listed in the master `covers:` set.
  An exploratory note in `covers:` is a BLOCK: it would be treated as
  confirmatory by the freeze-hash and the manuscript gate.
- [ ] **Exploratory notes are first-class** — exploratory notes are welcome and
  no checks gate or warn on their presence.  Do NOT flag exploratory notes as
  problems unless they violate the overclaim rule above.

---

## 8. Structural completeness (cross-cutting)

- [ ] **At least one main experiment** — a plan with no main experiment is
  incomplete.  BLOCK.
- [ ] **Each main has at least one diagnosis table** — main claims without a
  diagnosis table cannot be evaluated.  BLOCK.
- [ ] **No duplicate child ids** — the `covers:` list must not contain
  duplicate entries.  BLOCK on any duplicate.
- [ ] **`plan_kind: preregistration`** — the master note must carry
  `plan_kind: preregistration` in its frontmatter.  Missing is a BLOCK (wrong
  file was passed to plan-critic).

---

## 9. What you do NOT check (stay in your lane)

- **Computational feasibility** — you do not assess whether the experiment is
  runnable on the available hardware.  That is the researcher's call.
- **Scientific novelty or significance** — you check structural completeness,
  not whether the claim is interesting.
- **The specific choice of threshold or noise floor** — you verify it is
  stated; you do not second-guess the value.
- **Exploratory design choices** — exploratory notes are outside the freeze;
  you do not gate them (see §7).

---

## 10. Convention: the `plan-critic` DAG node

In the research-loop manifest, the `plan-critic` node references this file as
its `spec:`.  It runs after the `plan` node (afterok) and before
`human-go-plan`.  Its output must be surfaced to the human at `human-go-plan`
as a structured verdict (BLOCK / WARN / PASS).  The human does NOT approve
the plan based on the critic's PASS alone — the critic informs, the gate
decides ([[crew-cannot-self-approve]]).

---

## 11. Null-model dry-run (F22)

**What this gate catches:** an estimand that FIRES even when the effect being studied
is absent — i.e., a formula that returns a non-null result on data generated by an
effect-invariant model.  This class of defect (demonstrated by the `a_Easy − N_dep`
v1 estimand in the dogfood) is not catchable by structural completeness checks alone.
It requires a semantic check at authoring time, before plan-critic runs.

### Rule

The plan's **primary estimand formula** MUST be evaluated against a hand-specified,
effect-INVARIANT generative model and confirmed to return approximately zero (the null).

- "Effect-invariant" means: a model in which the proposed causal mechanism is
  *absent* — i.e., the grouping or manipulation variable is orthogonal to the
  outcome by construction.
- "Confirmed null" means: the estimand evaluates to a value close to zero (within
  measurement noise) on data drawn from this generative model.
- Any estimand that FIRES (returns a substantially non-null value) on the
  effect-invariant model is **disqualified at authoring, before plan-critic**.
  The plan must be revised.

### Critic check

- [ ] **Null-model result is documented in the plan** — the plan MUST include the
  result of the null-model dry-run (the generative model used, the estimand value
  obtained, and a confirmation it is ≈ 0).  Absent documentation is a BLOCK.
- [ ] **The generative model is genuinely effect-invariant** — the described model
  must be plausibly orthogonal to the effect (not a strawman).  A model that
  trivially cannot show the effect but also differs from the experimental population
  in structure is a WARN; a model that is demonstrably NOT invariant is a BLOCK.

---

## 12. Literal prompt-diff for one-component claims (F23)

**What this gate catches:** "these two arms differ in exactly one component" claims
that are not backed by a literal diff — and that in practice hide multi-component
differences.  This class of defect (demonstrated by the arm-wording leak in the dogfood
v2) is undetectable from prose descriptions of the arms.  Only a literal diff makes
it visible at authoring time.

### Rule

Any claim of the form "arms A and B differ in exactly one component" MUST be backed
by a **literal diff of the two frozen prompt templates** shown directly in the plan.
Prose description of the intended difference is insufficient.

- "Frozen prompt templates" means the actual template strings as they will be used
  at run time, committed at plan time — not paraphrases or summaries of them.
- "Shown in the plan" means the diff appears verbatim in the plan note body (not
  just in a separate file referenced by path).  The diff format is at the author's
  discretion (unified diff, side-by-side, or character-level), but it must be
  readable inline.
- "Identity by construction" is the goal: a reviewer who reads the plan can verify
  the one-component claim without running any code.

### Critic check

- [ ] **Literal diff present for every one-component claim** — for each "differs in
  exactly one component" statement in the plan (in any main, ablation, or arm
  description), a literal diff of the two templates MUST appear in the plan body.
  Missing diff is a BLOCK.
- [ ] **The diff shows exactly one differing component** — the diff must be consistent
  with the claim.  If the diff reveals two or more components changing, BLOCK: the
  "one-component" claim is false and the arm structure must be corrected before
  approval.
- [ ] **Templates are frozen (not placeholders)** — the diffed templates must be the
  actual run-time strings, not `<TODO: fill in>` or similar.  Unfilled placeholders
  in the diffed templates are a BLOCK.
