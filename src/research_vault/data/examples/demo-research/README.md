# demo-research — Research Loop Example (SR-PLAN-1: multi-main + ablations + conditionals)

A runnable demonstration of the Research Vault **research loop** DAG, upgraded
in SR-PLAN-1 to the full §5K.2 multi-main + ablation + conditional shape.

## What this demonstrates

The `research-loop.json` manifest encodes the full pre-registration discipline
(§5K):

1. **Plan** (Ada/researcher) — writes the pre-registration MASTER note
   (`notes/experiments/q1-plan.md`) covering the full confirmatory set:
   2 mains + their supporting ablations + conditional ablations.
   Also creates child experiment stubs up-front.
2. **Plan critic** (Argus/reviewer) — independent critique against the
   plan-critic spec (`doctrine/plan-critic-spec.md`).
3. **Human-go-plan gate** — you approve the plan and run `rv plan freeze`
   (K-3) to hash the `covers:` set into the run state.
4. **2 Mains run in parallel**, each with its own sub-chain:
   - **Main 1:** run → score → analyze (experiments/q1-main1 + findings)
   - **Ablation A of Main 1:** run → score → analyze (unconditional, parallel)
   - **Main 2:** run → score → analyze (experiments/q1-main2 + findings)
   - **Ablation B of Main 2:** run → score → analyze (unconditional, parallel)
5. **Per-main human-go-conditionals gates** — ratify each main's frozen
   diagnosis mapping; mark un-fired conditionals as `blocked`.
6. **Conditional ablations (if triggered)**:
   - Conditional Y (main 1): fires if main-1 acc > 0.80
   - Conditional Z (main 2): fires if main-2 F1 > 0.75
7. **human-go-findings** — final gate (runs K-3 re-verify automatically via
   `rv dag approve`).

## The key structural guarantees (SR-PLAN-1 additions)

- **K-2 shape-lint:** run `rv plan check notes/experiments/q1-plan.md` before
  the human-go-plan gate.  REJECTS-ONLY structural screen: catches empty/TBD/
  fallback diagnosis cells and multi-component ablations.
- **K-3 freeze-set hash:** run `rv plan freeze research-loop-q1
  notes/experiments/q1-plan.md` immediately after approving human-go-plan.
  Hashes the `covers:` set (sorted child ids + stance + plan_role) into the
  run state.  `rv dag approve research-loop-q1 human-go-findings` re-derives
  and BLOCKS on mismatch — a post-freeze edit to the confirmatory set is caught
  structurally.
- **Conditionals are LEAVES:** per-main conditional sub-chains hang off the
  `human-go-conditionals-mainK` gates as leaves.  The final `human-go-findings`
  gate depends on the per-main conditional GATES (always terminal when approved),
  not on the conditional sub-chains directly — so un-fired conditionals do not
  block the final synthesis.
- **Un-fired conditionals → blocked:** if a trigger is false, run
  `rv dag complete research-loop-q1 <cabl-node-run> --status blocked` to
  record it as "pre-committed, trigger false, deliberately not run" — an
  honest negative, not a silent drop (charter §2).

## Running the loop

```bash
# 0. Start the loop
rv dag run examples/demo-research/research-loop.json

# 1. Complete the plan node (verifies the plan master note exists + fresh)
rv plan check notes/experiments/q1-plan.md        # K-2 lint — must pass
rv dag complete research-loop-q1 plan

# 2. Plan critic reviews
rv dag complete research-loop-q1 plan-critic

# 3. Advance to human-go-plan
rv dag tick research-loop-q1

# 4. Approve + freeze (K-3)
rv dag approve research-loop-q1 human-go-plan
rv plan freeze research-loop-q1 notes/experiments/q1-plan.md

# 5. All mains + ablations run in parallel (dispatched → each scored → analyzed)
rv dag complete research-loop-q1 q1-main1-run
rv dag complete research-loop-q1 q1-main1-score
rv dag complete research-loop-q1 q1-main1-analyze
rv dag complete research-loop-q1 q1-main1-abl-A-run
rv dag complete research-loop-q1 q1-main1-abl-A-score
rv dag complete research-loop-q1 q1-main1-abl-A-analyze
# ... similarly for main2 and abl-B

# 6. Advance to per-main conditional gates
rv dag tick research-loop-q1

# 7. Approve main 1's conditionals gate
#    (ratify diagnosis mapping; record un-fired conditionals as blocked)
rv dag approve research-loop-q1 human-go-conditionals-main1
# If conditional Y did NOT fire:
rv dag complete research-loop-q1 q1-main1-cabl-Y-run --status blocked

# 8. Similarly for main 2
rv dag approve research-loop-q1 human-go-conditionals-main2
# If conditional Z did NOT fire:
rv dag complete research-loop-q1 q1-main2-cabl-Z-run --status blocked

# 9. Final gate — K-3 re-verify runs automatically
rv dag approve research-loop-q1 human-go-findings
```

## Note types used

| Node | Produces | Role |
|------|----------|------|
| plan | `experiments/q1-plan.md` (master, plan_kind: preregistration) | plan master |
| q1-main1-run | `experiments/q1-main1.md` | stance: confirmatory, plan_role: main |
| q1-main1-abl-A-run | `experiments/q1-main1-abl-A.md` | stance: confirmatory, plan_role: supporting_ablation |
| q1-main1-cabl-Y-run | `experiments/q1-main1-cabl-Y.md` | stance: confirmatory, plan_role: conditional_ablation |
| q1-main2-run | `experiments/q1-main2.md` | stance: confirmatory, plan_role: main |
| q1-main2-abl-B-run | `experiments/q1-main2-abl-B.md` | stance: confirmatory, plan_role: supporting_ablation |
| q1-main2-cabl-Z-run | `experiments/q1-main2-cabl-Z.md` | stance: confirmatory, plan_role: conditional_ablation |

All confirmatory child notes are in the master's `covers:` freeze-set and
carry `preregistration: experiments/q1-plan` + `supports_main:` back-links.
Exploratory notes (if any) are created after the freeze with `stance: exploratory`
and are NOT in `covers:` — additive-after-freeze, no teeth triggered.
