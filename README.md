# Research Vault

**The discipline is the headline, not the code.** Research Vault is an open
research tool that runs the full research loop — literature review, experiment
planning, running, and synthesis — through a role-based agent crew coordinated
by DAG loops.

The disciplines that make AI trustworthy for research — anti-fabrication, *every
outcome is a finding*, verify the artifact not the signal, honest
pre-registration, human-only approval gates — are what the tool **enforces**,
mechanically, through commands and gates. The code is the proof those disciplines
are actually runnable; they are not slogans in a CONTRIBUTING file.

An adoptable AI research-assistant framework. A hub coordinates a crew of
role-specialized agents (**architect, engineer, researcher, reviewer, designer**)
through DAG-driven research loops, with the trust disciplines wired into the
tooling so they *bite* instead of being aspirational.

---

## Why it exists

Most "AI research assistant" tooling optimizes for output volume. The failure
mode of an LLM in research is not slowness — it's *confident fabrication*: an
invented citation, a metric that never traces to a run, a "passing" check that is
green-and-empty, a result banked because it looked clean. Research Vault is built
around stopping exactly that:

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

Each discipline maps to a command and a gate.

---

## The two loops

Research Vault ships **two** research loops as DAGs. (Figure and manuscript loops
were deliberately removed — a solo researcher owns those downstream, by hand,
where taste matters more than automation.)

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
grounding manifest that binds each node to the artifacts it reads and produces.

### How a loop actually runs (the DAG walk)

The hub walks the DAG one dispatchable node at a time, using a **deterministic
brief emitter** so no dispatch is hand-transcribed (hand-transcription is where
context drifts):

```bash
rv dag status <run_id>              # 1. identify the next node (PENDING; reads verified)
rv dag brief  <run_id> <node_id>    # 2. emit the deterministic dispatch brief
#                                      3. dispatch that brief verbatim to the crew agent
rv dag complete <run_id> <node_id>  # 4. record SUCCEEDED/FAILED; the walker advances
rv dag tick   <run_id>              #    advance the frontier to the next gate
rv dag approve <run_id> <node_id>   #    human-go: the solo decision gate (see below)
```

The brief is a *pure function* of the node plus run state — byte-identical given
the same inputs — so what an agent receives is grounded in resolved absolute
paths, not a re-typed summary.

---

## Core capabilities

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
- **Cross-project corroboration** — declare a genuine edge between projects
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
pip install research-vault      # a lean 27-package research toolkit
rv --help
```

The `rv` CLI and every verb run clean even with the toolkit absent (all toolkit
imports are lazy) — so `pip install research-vault --no-deps` works, and
`rv bootstrap` populates an isolated `.venv` if you need the full stack later.

The 27-package core covers the model seam (**litellm** as the unified provider
interface, plus the Anthropic SDK and a tokenizer), analysis (pandas, numpy,
pyarrow, scipy, statsmodels, datasets), eval (inspect-ai, evaluate, sacrebleu,
rouge-score), a multilingual set, integrations (**wandb** for experiment tracking,
**pyzotero** + **keyring** for Zotero citation management), and harness utilities. GPU-fragile local
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
as the hub and installs the crew as subagents — so the session becomes the
coordinator, and the role hats become the specialists it dispatches. `rv init`
ships two runnable demo projects (`demo-research`, `demo-litreview`) so you can
walk a loop end-to-end before pointing it at real work.

A real project is its own git repo; register it with `rv project add` (or stand
up a fresh one with `rv project new`) after `rv init`.

---

## The crew

One flat, vault-level crew — six hats composed from a shared charter plus a role
doctrine:

| Hat | Class | Role |
|-----|-------|------|
| **hub** | hub | Sole orchestrator — walks the DAG, dispatches, never executes or merges |
| **architect** | coordinator | Owns the stack + architecture map; no shell (structural, not disciplinary) |
| **engineer** | doer | Executes scoped changes; runs the authorized merge |
| **researcher** | doer | Methodology, retrieval-backed citations, synthesis |
| **reviewer** | doer | Read-only; verifies the work + the honesty gates |
| **designer** | doer | Figures and surfaces |

Least-privilege is stamped into each hat: coordinator-class gets no `Bash`;
the reviewer is read-only; the researcher carries web retrieval for
support-checked citations. Nobody merges on their own authority — a merge
executes only when an independent gate authorizes it.

---

## Adoption

Research Vault runs on **Claude Code**.

```bash
pip install research-vault
rv init my-vault
```

`rv init` scaffolds `CLAUDE.md` and the crew under `.claude/agents/`, rendered
with per-role tool grants and model aliases — so your Claude Code session boots
as the hub with the role hats installed as the subagents it dispatches.
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
