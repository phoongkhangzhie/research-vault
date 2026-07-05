# Research Vault — Quick Start

Welcome. This is a zero-infra AI research assistant framework.
Run `rv check` first to verify your prerequisites.

## Prerequisites

- **Claude CLI** — the agent runtime (`claude --version`)
- **ANTHROPIC_API_KEY** — required; see _Credentials_ below
- **asta** (optional) — enables `rv research find --deep`; plain `rv research find` works without it
- **Zotero + ZOTERO_KEY** (optional) — for citation management
- **wandb** (optional) — for experiment results (`rv wandb pull`); `pip install wandb`

### Credentials

All API keys are resolved in this order (highest priority first):

1. **Environment variable** — `export ANTHROPIC_API_KEY=sk-ant-…`  
   (session-scoped; add to your shell profile to persist)

2. **System keyring** — `keyring set research_vault ANTHROPIC_API_KEY`  
   (persists across sessions; requires the `keyring` package)

Same pattern applies to other keys: `ZOTERO_KEY` (service `research_vault`) and `WANDB_API_KEY` (service `research-vault`).

`rv check` reports which source resolved each key so you can verify the provisioning path.

## Compute onboarding — DECLARE → DISCOVER

Before running experiments, declare your compute environment in the correct order:

```bash
rv compute init          # 1. DECLARE: scaffold compute_manifest.json
#  → edit FILL values:  host, submit_pattern (for remote), W&B entity/project
rv doctor                # 2. DISCOVER: probe each declared backend
rv compute show          # 3. VERIFY: merged declared-where + discovered-what
```

`rv doctor` cannot see a cluster you have not declared. Declare first, then discover.

**W&B results:** `compute_manifest.json` stores your W&B entity/project (config, not
secrets). W&B API key stays in the system keyring (see SETUP instructions). Env vars
`WANDB_ENTITY` / `WANDB_PROJECT` always win over the manifest when set.

**Remote cluster:** fill `backends.profiles.cluster.host` (your ssh alias from
`~/.ssh/config`) and `submit_pattern` (your partition/account flags). No keys in the
manifest — SSH auth is via your `~/.ssh/config` + ssh-agent.
The actual remote probe (ssh-based capability discovery) ships in SR-CO-REMOTE.

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
`my-project` as the project slug, and `rv build-agents` (vault-level, no project arg)
to regenerate the agent hat files.

## Key verbs

| Verb | When to use |
|------|-------------|
| `rv check` | Verify prerequisites before starting |
| `rv dag run <manifest>` | Start a research loop |
| `rv dag tick <run-id>` | Advance the loop after a completion |
| `rv dag complete <run-id> <node>` | Mark a node done (verifies OKF notes) |
| `rv dag approve <run-id> <node>` | Approve a human-go gate |
| `rv dag status <run-id>` | See the current state of a loop |
| `rv note <project> new <type> <title>` | Create an OKF note (`--id <key>` for a custom slug) |
| `rv control <project> inbox <text>` | Post to the Inbox section of the coordination bus |
| `rv task <project> add <title>` | Create a task card |

## Learn more

- `rv help` — all verbs and their discovery surfaces
- `rv <verb> --help` — details for a specific verb
- `examples/demo-research/README.md` — research loop walkthrough
- `examples/demo-litreview/README.md` — lit-review loop walkthrough
- `doctrine/agent-charter.md` — the values and epistemics of the system
- `doctrine/coordination.md` — how the control plane works
- `doctrine/roles/` — the crew role docs (hub, engineer, researcher, designer, reviewer, architect)
