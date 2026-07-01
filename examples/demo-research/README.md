# demo-research — Research Loop Example

A runnable demonstration of the Research Vault **research loop** DAG.

## What this demonstrates

The `research-loop.json` manifest encodes the core research discipline:

1. **Plan** (researcher) — write the research plan and pre-registration note
2. **Plan critic** (reviewer) — independent critique of the plan (Argus)
3. **Human-go gate** — you approve before any experiment runs
4. **Run** — execute the experiment (BLOCKED until pre-registration note is filed)
5. **Score** — score the results
6. **Analyze** — analyze and write findings note
7. **Human-go gate** — you review the findings before they are final
8. **Methods update** (soft) — update the methods note if the protocol changed

## The key structural guarantee

The `run` node has an `afterok` edge with `watch: note:experiments/exp-q1.md+fresh`.
This means **the experiment cannot run until its pre-registration note is filed**.
Pre-registration becomes a structural constraint, not a discipline someone must remember.

## Running the loop

```bash
# Start the loop (from your Research Vault instance root)
rv dag run examples/demo-research/research-loop.json

# After planning, complete the plan node (this verifies the experiments note exists)
rv dag complete research-loop-q1 plan

# After the critic reviews, complete the critic node
rv dag complete research-loop-q1 plan-critic

# Tick to advance to the human-go gate
rv dag tick research-loop-q1

# Approve the human-go gate
rv dag approve research-loop-q1 human-go-plan

# Now run, score, analyze...
```

## OKF note types used

| Node | Produces | Directory |
|------|----------|-----------|
| plan | pre-registration | `notes/experiments/` |
| analyze | findings | `notes/findings/` |
| methods-update | protocol note | `notes/methods/` |
