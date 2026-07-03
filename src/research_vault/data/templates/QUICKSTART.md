# Research Vault — Quick Start

Welcome. This is a zero-infra AI research assistant framework.
Run `rv check` first to verify your prerequisites.

## Prerequisites

- **Claude CLI** — the agent runtime (`claude --version`)
- **ANTHROPIC_API_KEY** — your API key (or use keyring)
- **asta** (optional) — for literature search integration
- **Zotero + ZOTERO_KEY** (optional) — for citation management

## Two runnable example loops

This instance includes two demo projects under `examples/`:

### Research loop — pre-registration enforced

```bash
cd <instance-root>
rv dag run examples/demo-research/research-loop.json
rv dag status research-loop-q1
```

The research loop enforces pre-registration: the `run` node cannot fire until
the `experiments/exp-q1.md` note is filed. This turns a discipline into a
structural constraint.

### Lit-review loop — OKF coverage gate

```bash
rv dag run examples/demo-litreview/lit-review-loop.json
rv dag status lit-review-loop-topic
```

Every in-scope paper must have a `literature/<key>.md` note before synthesis begins.
The `okf-coverage-gate` human-go node blocks until all distill nodes succeed.

## Adding a real project

A real project is a separate git repository. Register an existing repo:

```bash
rv project add my-project --code mp --source /path/to/my-project-repo
```

Or stand up a brand-new project repo (git init + scaffold + crew) in one command:

```bash
rv project new my-project --code mp --source /path/to/new-project-dir
```

Every project automatically gets the full default crew. Use any `rv` verb with
`my-project` as the project slug, and `rv build-agents --project my-project`
to generate the agent hat files.

## Key verbs

| Verb | When to use |
|------|-------------|
| `rv check` | Verify prerequisites before starting |
| `rv dag run <manifest>` | Start a research loop |
| `rv dag tick <run-id>` | Advance the loop after a completion |
| `rv dag complete <run-id> <node>` | Mark a node done (verifies OKF notes) |
| `rv dag approve <run-id> <node>` | Approve a human-go gate |
| `rv dag status <run-id>` | See the current state of a loop |
| `rv note <project> create <type> <key> <title>` | Create an OKF note |
| `rv control <project> post <text>` | Post to the coordination bus |
| `rv task <project> create <title>` | Create a task card |

## Learn more

- `rv help` — all verbs and their discovery surfaces
- `rv <verb> --help` — details for a specific verb
- `examples/demo-research/README.md` — research loop walkthrough
- `examples/demo-litreview/README.md` — lit-review loop walkthrough
- `doctrine/agent-charter.md` — the values and epistemics of the system
- `doctrine/coordination.md` — how the control plane works
- `doctrine/roles/` — the named crew (Ada, Argus, Atlas, Mason, Iris, Alfred, Wren)
