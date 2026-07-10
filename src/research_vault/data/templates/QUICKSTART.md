# Research Vault — Quick Start

Welcome. This is a zero-infra AI research assistant framework.

The canonical getting-started sequence:

```bash
rv init myvault      # scaffold a new vault
cd myvault           # enter it
rv onboard           # guided setup: keys, compute, inline-approval token
rv start             # launch Claude Code as Alfred in the vault
```

`cd` must come before `rv onboard` — onboarding writes the compute manifest into
the vault, so it runs from inside (a subprocess can't cd your shell for you).
`rv onboard` is idempotent; the runtime alone is enough to start.

After a `pip install --upgrade research-vault`, run **`rv update`** to pull the
upgraded framework (doctrine, `CLAUDE.md`, the crew hats) into this vault — your
notes, projects, and local edits are preserved.

## The one hard requirement

The **agent runtime (Claude Code)** is the ONLY thing that must be present. There is
**no required API key**. With the runtime installed and zero keys, `rv check` is
GREEN (exit 0) — you can start immediately.

Everything else is a **feature** you unlock when you need it. A missing feature key is
never a failure — it is simply **locked until you add the key**.

| Feature | Unlocks | Get a key / access |
|---|---|---|
| **Provider API key(s)** | API-model experiments (any ONE provider) | `ANTHROPIC_API_KEY` → https://console.anthropic.com/settings/keys · `OPENAI_API_KEY` → https://platform.openai.com/api-keys |
| **s2** | `rv research find` retrieval | https://www.semanticscholar.org/product/api |
| **asta** | `rv research find --deep` | https://share.hsforms.com/1L4hUh20oT3mu8iXJQMV77w3ioxm |
| **wandb** | experiment observability + `rv wandb pull` | https://wandb.ai/settings |
| **zotero** | `rv cite` | https://www.zotero.org/settings/keys |
| **compute** | remote-cluster experiments | run `rv compute init` |

> **asta note:** the access request needs an **institutional email (not a personal
> gmail)** — state your institution at the form. See allenai.org/asta/resources/mcp.

Provider keys are **provider-plural** — Anthropic, OpenAI, and others. Any one unlocks
API-model experiments; you can skip them entirely if you run local models or do
lit-review only.

### Guided setup — `rv onboard`

```bash
rv onboard          # walk every feature: explain, show the form, add the key
```

`rv onboard` is **idempotent** — re-run it any time; satisfied steps are skipped. At an
interactive terminal it reads each secret with a hidden prompt (`getpass`, never echoed)
and stores it in your **system keyring** — never a plaintext `.env`. In a non-interactive
shell (or with `rv onboard --print`) it prints the exact remediation steps instead.

### Credentials — resolution order

Every key resolves in this order (highest priority first):

1. **Environment variable** — e.g. `export ANTHROPIC_API_KEY=sk-ant-…`
   (session-scoped; add to your shell profile to persist)
2. **System keyring** (service `research-vault`) — written by `rv onboard`, or manually
   `keyring set research-vault anthropic-api-key`

`rv check` reports which source resolved each key, and points to `rv onboard` for any
capability still locked.

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

## Example: a session with Alfred

After `rv init` writes `CLAUDE.md`, run `rv start` in the instance directory to launch
Claude Code as Alfred — the hub. Alfred is the operator's **single front door**: it runs the control-plane
verbs, walks the DAG, dispatches crew subagents for scoped work, and stops at every
human-go gate to ask for explicit approval. The operator converses in natural language;
everything else is coordinated through the file bus and the DAG.

Here is a representative slice of a literature review session.

---

**Operator:** Alfred, start a literature review on cross-lingual transfer in LLMs. Project
is `my-research`. Research question: "Does pre-training language distribution drive
cross-lingual transfer in decoder-only LLMs?"

**Alfred:** On it. First I'll scaffold the review loop.

```
rv review my-research new scope-xling-transfer \
  --question "Does pre-training language distribution drive cross-lingual transfer in decoder-only LLMs?"
```

Output:

```
Created: notes/my-research/reviews/scope-xling-transfer.md
Scaffolded: reviews/scope-xling-transfer/phase1-dag.json
Phase-1 DAG: review-scope → [HG:approve-protocol] → review-search → review-screen →
  review-snowball → review-curate → coverage-gate (auto-resolved)
```

Now starting the loop:

```
rv dag run reviews/scope-xling-transfer/phase1-dag.json
```

```
Run 'review-scope-xling-transfer-phase1' started.
  nodes: 7

Initial frontier:
  → DISPATCH  [review-scope] Define review scope and pre-registration protocol (researcher)
      FRESH — spec:reviews/scope-xling-transfer/_protocol.md
```

---

**Step 1 of the walk: emit the brief, dispatch the researcher.**

```
rv dag status review-scope-xling-transfer-phase1
```

```
Nodes:
  ○ review-scope         (pending)   ← frontier
  ○ approve-protocol     (pending, human-go)
  ○ review-search        (pending, tool)
  ○ review-screen        (pending)
  ○ review-snowball      (pending, tool)
  ○ review-curate        (pending)
  ○ coverage-gate        (pending, human-go)
```

```
rv dag brief review-scope-xling-transfer-phase1 review-scope
```

Alfred captures the emitted brief — a deterministic output of the node spec, run state,
and grounding pointers. Alfred does **not** hand-write a brief; `rv dag brief` is the
only path. Alfred dispatches the researcher subagent with the emitted brief verbatim.

The researcher runs `rv review`, `rv research find`, and `rv note` to author the protocol
note, then returns a `⟦RETURN⟧` with the deliverable path.

```
rv dag complete review-scope-xling-transfer-phase1 review-scope
```

```
Node 'review-scope' → SUCCEEDED.

Frontier:
  → HUMAN-GO  [approve-protocol] Review and approve the pre-registration protocol
      Waiting for: rv dag approve review-scope-xling-transfer-phase1 approve-protocol
```

---

**Step 2: human-go gate — Alfred stops here.**

**Alfred:** The researcher has filed the protocol note at
`notes/my-research/reviews/scope-xling-transfer/_protocol.md`. It covers:

- Research question and inclusion/exclusion criteria
- Seed queries and planned sources
- Saturation stopping rule

This is **Gate 1** (a cheap screen before the expensive search). I can't approve this
myself — the crew cannot self-approve. Please review the protocol note and, if it looks
right, approve the gate:

```
rv dag approve review-scope-xling-transfer-phase1 approve-protocol
```

Once you approve, `review-search` (a deterministic width-sweep tool node) fires, then
the researcher screens the hits (`review-screen`), then `review-snowball` (the
deterministic saturation walk) runs, the researcher curates the raw corpus
(`review-curate`), and I'll surface `coverage-gate` for your final review before
Phase-2 synthesis begins.

---

That is the operating pattern throughout: Alfred walks the DAG four steps at a time
(status → brief → dispatch → complete), advances automatically through agent nodes, and
**surfaces every human-go node to the operator** with the drafted artifact and the exact
`rv dag approve` command needed. The operator reads, decides, and approves; Alfred proceeds.

The same pattern applies to the experiment loop — `rv experiment my-research new q1
--question "..."` scaffolds a pre-registration plan DAG with a `human-go-plan` gate after
the plan and plan-critic nodes, and a `human-go-harness-main1` gate before any run fires.
No run executes until the human has approved both the plan and the harness.

**The disciplines in action:**

- Alfred grounds everything — the brief is emitted by `rv dag brief`, not hand-rolled.
- Crew do the scoped work; Alfred coordinates, never re-implements what a role knows.
- Irreversible steps (search, run) are gated behind explicit human approval.
- The crew cannot self-approve: human-go gates are the operator's decision, not Alfred's.

## The three canonical loops

Research Vault ships three research-loop shapes, discoverable via `rv dag templates`:

### Experiment loop — pre-registration enforced

The experiment loop enforces pre-registration: the `run` node cannot fire until
the pre-registration plan has been critiqued and frozen, and each main's harness
is reviewed independently before its own `human-go-harness-<main>` gate. Start
one with `rv experiment <project> new <id> --question '...'`.

### Lit-review loop — pre-registered, saturation-gated

The protocol (question, seed queries, inclusion/exclusion, and a required
counter-position) must be approved at `approve-protocol` before any search fires
(the L-2 anti-fishing gate). A deterministic width-sweep (`review-search`) is
screened by a thin agent judgment layer (`review-screen`); a deterministic
both-direction snowball walk (`review-snowball`) then runs to saturation and is
concept-tagged and curated (`review-curate`) into the final corpus. The
`coverage-gate` node is the Phase-2 boundary: every in-scope paper must have a
relate slot or be recorded MENTION-ONLY before `rv review <project>
expand <scope>` fans out the per-paper `relate-*` nodes. Single-human-gate
design: only `approve-protocol` is a human gate — `coverage-gate` and the
terminal `approve-review` (Gate 3, after the Phase-2 coverage-critic) both
resolve AUTONOMOUSLY through the gate-policy engine (`review/autonomy.py`);
the user receives the reviewed corpus as the system's best version, with no
"approve the result" gate. Start one with `rv review <project> new <scope>
--question '...'`.

### Manuscript loop — type-generic, gated on structural + semantic fidelity

Transforms the OKF pillar (`notes/`) into a submittable document. Each
manuscript type may declare its own Phase-1 (e.g. the `lit-review` type's
framework-selection sub-loop, gated on `approve-framework`); every type shares
a Phase-2 terminal `approve-manuscript` gate covering structural gates, fidelity
gates, and the review-revise board. Both `approve-framework` and
`approve-manuscript` resolve autonomously (single-human-gate design) — neither
is a human keypress. Start one with `rv manuscript <project> new <slug> --type
<type>`.

## Adding a real project

**Each project is its own git repository, living as a sibling of the vault.**
If your vault is at `/home/user/myvault/`, the project lives at
`/home/user/my-project/` — NOT inside the vault. This keeps each project's git
history independent and avoids nested-repo issues.

Stand up a brand-new project repo (git init + scaffold + crew) in one command.
No `--source` needed — the default is the sibling path automatically:

```bash
rv project new my-project --code mp
# creates /home/user/my-project/ as a sibling of the vault
# git-init'd as its own repo, scaffolded with OKF dirs + control bus + crew
```

Register an existing repo that's already on disk (must be outside the vault):

```bash
rv project add my-project --code mp --source /path/to/my-project-repo
```

Every project automatically gets the full default crew. Use any `rv` verb with
`my-project` as the project slug, and `rv build-agents` (vault-level, no project arg)
to regenerate the agent hat files.

## Key verbs

| Verb | When to use |
|------|-------------|
| `rv start` | Front door — launch Claude Code in your vault so the session becomes Alfred |
| `rv onboard` | Guided, idempotent setup — add the keys that unlock features |
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
- `rv dag templates` — the built-in research loops (experiment, lit-review)
- `doctrine/agent-charter.md` — the values and epistemics of the system
- `doctrine/coordination.md` — how the control plane works
- `doctrine/roles/` — the crew role docs (hub, engineer, researcher, designer, reviewer, architect)
