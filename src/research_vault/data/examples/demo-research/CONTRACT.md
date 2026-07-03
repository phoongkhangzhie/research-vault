# CONTRACT — demo-research

The project lens. Composed into every demo-research agent's hat
(`charter + role + this`). Identity is **static**; the roadmap shifts on
**milestones**; the operational *now* is **read fresh**, not baked here.

## Identity

**What it is.** A demonstration research project for the Research Vault
framework. It runs the canonical research loop (plan → plan-critic →
pre-registration → run → score → analyze) to show how the DAG orchestrates
a quantitative research workflow end-to-end. Not a real experiment — a
working example that proves the framework is runnable, not aspirational.

**Profile.** Research methodology demonstration.

**Where it lives.** Inside the Research Vault instance under `examples/demo-research/`
(in-repo demo; not a separate git repository).

**Conventions / golden rules:**
- The research loop manifest is `research-loop.json` — run via `rv dag run`.
- Pre-registration is structural: the `run` node has an `afterok` watch on
  `note:experiments/exp-q1.md+fresh`. The experiment cannot run until the
  pre-registration note exists.
- This is a demo; its outputs are illustrative, not publishable findings.

**Care.** No real data, no real experiment — treat as illustrative only.

## Pointers

- **Loop manifest** — `examples/demo-research/research-loop.json`
- **Architecture** — `architecture.md` (the instance-level layout)
- **Control bus** — `rv status --project demo-research` (never eyeball `control/demo-research.md` directly)

## Roadmap

Demo project — no milestones. Modify the loop manifest to demonstrate your
own research workflow.

## Your team (roster)

demo-research's crew: manager, engineer, researcher, designer, reviewer.
The hub is the sole spawner; you **request** convening, you don't spawn.

### By role — the lens each hat leans on

- **manager** → coordination artifacts, task cards, CONTROL bus
- **engineer** → code, tests, CI (any analysis scripts in this demo)
- **researcher** → experiment design, pre-registration, analysis methodology
- **designer** → any figures or visualizations produced by the loop
- **reviewer** → gate verdicts before human-go nodes proceed

## Operational state — read fresh, not baked here

For what's on the plate *right now*: `rv status --project demo-research`
and `rv dag status` for the active run. This contract is the *strategic*
lens; the board and CONTROL are the *operational* now.
