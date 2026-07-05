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

Before running experiments, declare your compute environment:

```bash
rv compute init          # 1. DECLARE: scaffold compute_manifest.json
#  → edit FILL values (see below), then:
rv doctor                # 2. DISCOVER: probe each declared backend
rv compute show          # 3. VERIFY: merged declared-where + discovered-what
```

`rv doctor` cannot see a backend you have not declared. Declare first, then discover.

### Manifest FILL values

`rv compute init` writes a guided `compute_manifest.json` with clearly-labelled `FILL`
placeholders. The key fields to fill:

| Field | Where | What to put |
|---|---|---|
| `backends.profiles.compute-node.host` | remote profile | SSH alias from `~/.ssh/config` (e.g. `mycluster-login`) |
| `backends.profiles.compute-node.submit_pattern` | remote profile | Your scheduler flags: `sbatch --partition=FILL --account=FILL --gres=gpu:{gpus} --time=FILL` |
| `backends.profiles.compute-node.host_group` | remote profile | A label shared with the transfer node if your cluster has a DTN (e.g. `mycluster`) |
| `results.wandb.entity` | W&B block | Your W&B username or team (or set `WANDB_ENTITY` env var) |
| `results.wandb.project` | W&B block | Default W&B project for this instance (or set `WANDB_PROJECT` env var) |

Credentials never go in the manifest: SSH auth uses `~/.ssh/config` + ssh-agent;
the W&B API key stays in the system keyring (`keyring set research-vault WANDB_API_KEY`).

### Backend archetypes

The manifest supports four archetypes:

| Archetype | When to use |
|---|---|
| `local` | Runs on this machine as a subprocess (zero-infra default, always present) |
| `ssh` | Plain SSH to a remote host (no scheduler — use for data-transfer nodes / DTNs) |
| `ssh+slurm` | SLURM cluster over SSH (`sbatch`/`sacct`) |
| `ssh+pbs` | PBS cluster over SSH (`qsub`/`qstat`) |

A data-transfer node (DTN) shares the same filesystem as your compute node. Declare it as
a second profile with `archetype: ssh` and the same `host_group` value as the compute node.
Use the DTN profile for large downloads and staging; use the compute-node profile for job submission.

Flip `backends.active` from `["local"]` to `["compute-node"]` once the FILL values are filled.

### Tier mapping and GPU discovery

After `rv doctor` probes your cluster, it proposes a tier → partition mapping based on
available GPU hardware:

```bash
rv doctor --propose      # writes tiers_proposed (draft, does NOT touch live tiers)
#  → review tiers_proposed in compute_manifest.json, edit if needed, then:
rv doctor --accept       # promotes tiers_proposed → live tiers (shows diff first)
```

GPU tiers (`tp1`, `tp4`, …) map model sizes to GPU counts. The manifest seeds sensible defaults;
`rv doctor --propose` refines them from what it actually finds on your cluster.

To resolve the exact env/tier/flags for a specific job or model before submitting:

```bash
rv compute explain <model-or-job-name>
```

Returns: backend, conda env, GPU tier, GPU count, and submit flags. One command, no guessing.

### How an experiment run executes

The standard experiment sequence (per main condition):

```
harness       →  harness-review  →  [HG: human-go-harness]
→  run         →  score           →  analyze
```

1. **Harness phase** (`harness` node): the crew writes and reviews the run script. Approved
   via `rv dag approve <run-id> human-go-harness-main<k>`.
2. **Run phase** (`run` node): the crew calls the configured backend (`rv`'s ComputeBackend
   adapter) — it handles `sbatch`/ssh submission with the flags from your manifest. The crew
   does **not** hand-build `sbatch` commands; it submits through the adapter.
3. **Results**: experiment outputs go to W&B (configured in the manifest). Pull them back:
   ```bash
   rv wandb pull <run-id>    # fetches the W&B run by id; stores index locally
   ```
4. **Score / analyze**: downstream nodes read from the fetched index. The `analyze` node
   files the findings note (`experiments/<id>.md`).

The harness SHA is pinned at `rv plan freeze-harness` and re-verified at the final
`human-go-findings` gate — harness-commit drift is a reportable kind and will block the gate.

### Capturing lessons and outcomes

When a run reveals a cluster gotcha, record it so future runs avoid it:

```bash
rv compute lesson add "<trigger>" "<fix>"
# e.g. rv compute lesson add "download >10GB" "use transfer-node, not compute-node"
```

Record run outcomes so the manifest learns from real results:

```bash
rv compute outcome add --job <name> --tier tp1 --result OOM
# result choices: OOM | SUCCESS | FAILED | TIMEOUT
```

Lessons accumulate in the manifest's `rules` block; outcomes in `run_outcomes`. Both
surfaces are read by `rv compute explain` and `rv doctor --propose` to improve future
tier recommendations.

### Anti-patterns

- **Do NOT trial-submit** to discover partition/GPU/env. `rv compute show` and
  `rv compute explain` already declare it.
- **Do NOT hand-run** `ssh cluster sbatch ...` with guessed flags. The adapter sends the
  right flags from the manifest.
- **Do NOT hand-edit** `compute_manifest.json` from scratch — use `rv compute init` to
  scaffold it, then fill the FILL values.
- **Do NOT re-probe by running jobs** — `rv doctor` caches the cluster capabilities;
  use `rv doctor --refresh` only when the cluster hardware actually changes.

> **Note:** model-call provider routing and observability wiring are evolving. See
> `doctrine/compute-run-recipe.md` for the current recipe on how harnesses call providers.

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
