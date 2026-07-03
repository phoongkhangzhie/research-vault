# CONTRACT — demo-litreview

The project lens. Composed into every demo-litreview agent's hat
(`charter + role + this`). Identity is **static**; the roadmap shifts on
**milestones**; the operational *now* is **read fresh**, not baked here.

## Identity

**What it is.** A demonstration literature-review project for the Research
Vault framework. It runs the canonical lit-review loop (scope → survey →
distill → OKF-coverage-gate → synthesize → synthesis-critic) to show how
the DAG orchestrates a systematic literature review end-to-end. Not a real
review — a working example of how retrieval-backed, OKF-grounded synthesis
is produced.

**Profile.** Literature review methodology demonstration.

**Where it lives.** Inside the Research Vault instance under `examples/demo-litreview/`
(in-repo demo; not a separate git repository).

**Conventions / golden rules:**
- The lit-review loop manifest is `lit-review-loop.json` — run via `rv dag run`.
- OKF coverage is structural: the `okf-coverage-gate` human-go node blocks
  until all distill nodes succeed. A distill node cannot succeed without its
  `literature/<key>.md` OKF note (enforced by `rv dag complete`'s produces check).
- Distilled literature notes go in `notes/literature/`. Each note is a pointer,
  not a full-text embed.
- This is a demo; its outputs are illustrative, not a publishable systematic review.

**Care.** No real corpus, no real review — treat as illustrative only.

## Pointers

- **Loop manifest** — `examples/demo-litreview/lit-review-loop.json`
- **Architecture** — `architecture.md` (the instance-level layout)
- **Control bus** — `rv status --project demo-litreview` (never eyeball `control/demo-litreview.md` directly)

## Roadmap

Demo project — no milestones. Modify the loop manifest to demonstrate your
own literature review workflow.

## Your team (roster)

demo-litreview's crew: manager, engineer, researcher, designer, reviewer.
The hub is the sole spawner; you **request** convening, you don't spawn.

### By role — the lens each hat leans on

- **manager** → coordination artifacts, task cards, CONTROL bus
- **engineer** → any tooling or automation for the review process
- **researcher** → scope definition, search strategy, distillation, synthesis
- **designer** → any figures or visualizations produced from the review
- **reviewer** → gate verdicts before human-go nodes (especially coverage-gate)

## Operational state — read fresh, not baked here

For what's on the plate *right now*: `rv status --project demo-litreview`
and `rv dag status` for the active run. This contract is the *strategic*
lens; the board and CONTROL are the *operational* now.
