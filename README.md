# Research Vault

![The Research Vault crew — Alfred (hub), Wren, Mason, Ada, Argus, and Iris](assets/hero-banner.png)

## An autonomous research crew you delegate to — not a tool you operate.

You hand Research Vault a research question. A hub agent — **Alfred** — plans the
work, dispatches a crew of specialists, and runs the full research loop:
literature review, experiment design, execution, analysis, synthesis. You stay in
the conversation and approve at the gates that matter. **You never touch the CLI —
Alfred does.**

The aim is to give the scientist space to **think and collaborate** with the crew —
not to worry about implementation and execution. The agents do the mechanical work;
a human stays in the loop where judgment matters; and the disciplines that make
research trustworthy are wired into the machinery so they *bite*.

**The discipline is the headline. The autonomy is the point.**

---

## The crew

One flat, vault-level crew — six named specialists, each a hat composed from a
shared charter plus a role doctrine. You talk to **Alfred**; Alfred coordinates
the rest.

**Alfred** — *Hub.* After Alfred Pennyworth, the butler who runs the household. The
single front door: he plans the work, dispatches the crew, walks the research loop,
and surfaces every decision that needs you. He is the *only* agent that touches the
control plane — and he never executes code or merges anything himself.

**Wren** — *Architect.* After Sir Christopher Wren. Owns the stack and the
architecture map; vets every new dependency and keeps the system coherent. Wren
designs — he doesn't lay stone.

**Mason** — *Engineer.* The master stonemason. Builds the code: features, tests,
CI, and the authorized merge. Wren draws the plans; Mason builds them.

**Ada** — *Researcher.* After Ada Lovelace. The science itself — literature review,
experiment design, retrieval-backed citations, analysis, and synthesis.

**Argus** — *Reviewer.* After Argus Panoptes, the hundred-eyed watchman.
Independent verification: adversarial review and the honesty gates. Read-only by
construction — and no agent reviews its own work.

**Iris** — *Designer.* After Iris, goddess of the rainbow and messenger of the
gods. Figures and the surfaces through which the work reaches the world.

Least-privilege is stamped into each hat: coordinator-class hats get no shell
(structural, not disciplinary), the reviewer is read-only, and the researcher
carries web retrieval for support-checked citations. **Nobody merges on their own
authority** — a merge executes only when an independent gate authorizes it.

---

## How you actually use it

You never drive the tooling. The loop is a conversation:

1. **You describe the work** to Alfred in natural language — a question to
   investigate, a literature space to map, an experiment to run.
2. **Alfred walks the DAG** — he plans the research loop, dispatches each node to
   the specialist whose hat fits it (Ada for the science, Mason for the harness,
   Argus for verification, Iris for figures), and threads the artifacts between
   them.
3. **You approve at the gates** — at each human-go gate Alfred pauses, hands you an
   evidence packet, and waits. Nothing clears the gate without your explicit go;
   the crew cannot approve its own work.

The `rv` command line exists — but it is **Alfred's control surface**, not a human
keyboard interface. You converse; Alfred runs the verbs. (The full verb reference
is [below](#the-crews-control-surface), for the curious.)

---

## Why it exists

Most "AI research assistant" tooling optimizes for output volume. The failure
mode of an LLM in research is not slowness — it's *confident fabrication*: an
invented citation, a metric that never traces to a run, a "passing" check that is
green-and-empty, a result banked because it looked clean. Research Vault is built
around stopping exactly that, mechanically:

- **Anti-fabrication.** Every specific — a number, a citation, a file — must
  trace to a real source. A citation needs a real retrieval, support-checked, not
  recalled from memory.
- **Every outcome is a finding.** A null result is a result. The loops are built
  to reach and record an honest null, not to fish for a positive.
- **Verify the artifact, not the signal.** "CI is green" is a claim to check
  against the artifact, never a fact to relay. A completed node is verified by its
  produced artifact's freshness, not by an agent's say-so.
- **Honest pre-registration.** The confirmatory plan is frozen (a content hash)
  *before* the run, and edits to the frozen set are caught structurally.
- **Human-only approval.** The crew cannot approve its own work. The approval gate
  is a mechanical trust boundary keyed on an interactive terminal — a dispatched
  agent has no TTY and is refused, regardless of flags.

Each discipline maps to a command and a gate. The code is the proof those
disciplines are actually runnable; they are not slogans in a CONTRIBUTING file.

---

## Where it fits

Research Vault stands on a wave of work exploring agentic research and discovery —
systems that let agents plan, run, and reason about scientific work:

- **AlphaEvolve** — [A Gemini-powered coding agent for designing advanced
  algorithms](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms)
  (DeepMind)
- **AutoResearch** — [Andrej Karpathy](https://github.com/karpathy/autoresearch)
- **The AI Scientist-v2** — [Workshop-Level Automated Scientific Discovery via
  Agentic Tree Search](https://arxiv.org/abs/2504.08066)
- **AutoResearchClaw** — [aiming-lab](https://github.com/aiming-lab/AutoResearchClaw)

Our emphasis is a deliberate choice, not a verdict on any of these: **discipline
and doctrine, with a human in the loop.** We want to give the scientist room to
*think and collaborate* with the crew — freed from the mechanical work of
retrieval, harness-building, running, and analysis, but never removed from the
judgment. The agents do the mechanical work; the disciplines (anti-fabrication,
honest pre-registration, verify-the-artifact, human-only approval) keep it
trustworthy; and the human stays where human judgment belongs — the questions, the
design, and the gates.

---

## What the crew runs — the two loops

Research Vault ships **two** research loops as DAGs. Alfred walks each one node by
node, dispatching every node to the specialist whose hat fits it. (Figure and
manuscript loops were deliberately left out — a solo researcher owns those
downstream, by hand, where taste matters more than automation.)

### Literature review (`rv review`)

A pre-registered, saturation-gated review. The protocol must be approved before
search fires (L-2 anti-fishing gate), snowball walks forward (cited-by) and
backward (references), and Phase-2 relate nodes fan out over every in-scope paper.
OKF outputs: `literature/*.md` notes, `concepts/`, `mocs/`, and typed gap notes.

```mermaid
flowchart LR
    scope[review-scope] --> HG1[["[HG] approve-protocol"]]
    HG1 --> search[review-search] --> snowball[review-snowball]
    snowball --> HG2[["[HG] coverage-gate"]]
    HG2 --> relate["relate-*\n(Phase-2 fan-out)"]
    relate --> synthesize[review-synthesize] --> critic[review-coverage-critic]
    critic --> HG3[["[HG] approve-review"]]
```

### Experiment (`rv experiment`)

A pre-registered study. The plan is critiqued and frozen before any harness is
built; each main's harness is reviewed independently before the run fires; results
gate conditional ablations; all findings are ratified before write-up.
OKF outputs: `experiments/*.md` (pre-reg), `findings/*.md`.

```mermaid
flowchart LR
    plan --> critic[plan-critic]
    critic --> HG1[["[HG] human-go-plan"]]
    HG1 --> harness["harness\n(×N mains)"] --> hr[harness-review]
    hr --> HG2[["[HG] human-go-harness"]]
    HG2 --> run --> score --> analyze
    analyze --> HG3[["[HG] human-go-conditionals"]]
    HG3 -->|if threshold| cabl["conditional\nablations"]
    HG3 --> HG4[["[HG] human-go-findings"]]
    cabl --> HG4
    HG4 -.-> methods-update
```

Both loops use the same underlying machinery: a DAG walker over typed nodes, with a
grounding manifest that binds each node to the artifacts it reads and produces. The
`[HG]` nodes are the **human-go gates** — the points where Alfred pauses and waits
for you.

---

## The crew's control surface

Everything below is what **Alfred** runs on your behalf. You don't type these — but
they are readable, so you can see exactly what the coordination is made of.

### How a loop runs (the DAG walk)

Alfred walks the DAG one dispatchable node at a time, using a **deterministic brief
emitter** so no dispatch is hand-transcribed (hand-transcription is where context
drifts):

```bash
rv dag status <run_id>              # 1. identify the next node (PENDING; reads verified)
rv dag brief  <run_id> <node_id>    # 2. emit the deterministic dispatch brief
#                                      3. dispatch that brief verbatim to the crew agent
rv dag complete <run_id> <node_id>  # 4. record SUCCEEDED/FAILED; the walker advances
rv dag tick   <run_id>              #    advance the frontier to the next gate
rv dag approve <run_id> <node_id>   #    human-go: the solo decision gate
```

The brief is a *pure function* of the node plus run state — byte-identical given
the same inputs — so what a crew agent receives is grounded in resolved absolute
paths, not a re-typed summary.

### Core capabilities

- **DAG research loops** with typed nodes, afterok/watch edges that gate on
  artifact freshness, and in-session resolution (no background pollers/daemons).
- **Deterministic crew briefs** (`rv dag brief`) — every dispatch carries a fixed
  structural preamble (role framing, anti-fabrication, the return schema) plus the
  node's spec and resolved read/write paths.
- **K-3 pre-registration freeze** (`rv plan check`, `rv plan freeze`) — a
  structural shape-lint (no empty/TBD/"fallback" diagnosis cells;
  one-component-per-ablation) *before* the human-go, then a content hash of the
  confirmatory `covers:` set that is re-verified at findings — a post-freeze edit
  to the frozen set is caught, not trusted.
- **Cross-project corroboration** — Alfred declares a genuine edge between projects
  (`rv project relate <a> <b> --kind <why>`), then `rv research corroborate` ranks
  candidate evidence across declared peers by TF-IDF, an LLM judge confirms each,
  and a human reviews. Never auto-asserted.
- **The mechanical approval trust boundary** (`rv approval`, `rv dag approve`) —
  `security = stdin.isatty()`, full stop. A signed token path exists for
  non-interactive operators, but the crew cannot self-approve by construction.
- **OKF typed notes** — 8 note types (literature, concepts, methods, experiments,
  findings, mocs, datasets, gaps). Notes are *pointers*, not embeds: a datasets
  note points to its artifact (path/URL/DOI + content hash), never contains it.

---

## Install

```bash
pip install research-vault      # a lean 28-package research toolkit
rv --help
```

The `rv` CLI and every verb run clean even with the toolkit absent (all toolkit
imports are lazy) — so `pip install research-vault --no-deps` works, and
`rv bootstrap` populates an isolated `.venv` if you need the full stack later.

The 28-package core covers the model seam (**litellm** as the unified provider
interface, plus the Anthropic SDK and a tokenizer), analysis (pandas, numpy,
pyarrow, scipy, statsmodels, datasets), eval (inspect-ai, evaluate, sacrebleu,
rouge-score), a multilingual set, integrations (**wandb** + **weave** for
experiment tracking and automatic call-trace observability, **pyzotero** +
**keyring** for Zotero citation management), and harness utilities. GPU-fragile local
inference (torch, transformers, …) is **opt-in** behind an extra — it is never
installed by default (CUDA-pinned wheels break CPU-only machines):

```bash
pip install research-vault[local]              # local GPU inference
pip install research-vault[local,serve-vllm]   # + a serving stack
```

Per-provider SDKs (openai, google-genai, …) and plotting libraries are **not**
shipped — install them directly at your discretion. `litellm` covers most API
targets without a dedicated SDK.

### Prerequisites

- **Python 3.12+**
- **An agent runtime.** Claude Code (see *Adoption* below).
- **Model API keys** — via environment variables or your system keyring.
- Run **`rv check`** to verify prerequisites and see the tier coverage matrix.

`wandb`, `pyzotero` (Zotero API client), and `keyring` are **core pip dependencies** —
shipped in the default `pip install research-vault`. `asta` (research corpus tooling) is
the **one external prerequisite** that is not a pip dep: install it per your project's
instructions. `rv check` reports full integration status including `asta`'s presence.

---

## Quick start

```bash
rv init my-vault        # scaffold an instance: config, control bus, OKF note
cd my-vault             #   dirs, doctrine, the crew, and two demo projects
rv check                # preflight: verify prerequisites
```

On **Claude Code**, `rv init` also writes a `CLAUDE.md` that boots your session
as **Alfred** (the hub) and installs the crew as subagents — so the session
becomes the coordinator you converse with, and the role hats become the specialists
it dispatches. `rv init` ships two runnable demo projects (`demo-research`,
`demo-litreview`) so the crew can walk a loop end-to-end before you point it at real
work.

A real project is its own git repo; register it with `rv project add` (or stand
up a fresh one with `rv project new`) after `rv init`.

---

## Adoption

Research Vault runs on **Claude Code**.

```bash
pip install research-vault
rv init my-vault
```

`rv init` scaffolds `CLAUDE.md` and the crew under `.claude/agents/`, rendered
with per-role tool grants and model aliases — so your Claude Code session boots
as Alfred with the role hats installed as the subagents he dispatches.
The human-only approval boundary holds mechanically: the approve-gate keys on an
interactive TTY, which dispatched crew subagents never have.

---

## Status

This is **alpha**. The architecture is complete and the loops run end-to-end. It
is not battle-tested across many adopters yet, and it is evolving. The disciplines
are enforced by code you can read.

## License

MIT. See [LICENSE](./LICENSE).

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md). One rule above the rest: **changes to a
discipline are doctrine changes** — they go through the doctrine, not around it.
</content>
