# how to run here — compute run recipe

**Before submitting any experiment job, read the run-recipe and resolve the env/tier.**

## Model seam — the provided `ModelClient` (litellm under the hood)

Research Vault harnesses call models through the **provided `ModelClient` seam**
(SR-MODEL-SEAM), reached from the `AdapterSet`. The seam wraps `litellm` (the unified
provider interface — swap `model=` without changing harness logic) AND makes observability
**automatic and unforgettable**: every call is traced (Plane A) and aggregated (Plane B)
with zero per-call logging code in the harness.

**The exact reach — copy-paste this:**

```python
from research_vault.adapters import load_adapters
from research_vault.config import load_config

cfg = load_config()
adapters = load_adapters(cfg)

resp = adapters.model.complete(
    model="gpt-4o",            # or "claude-..." / "gemini/..." / "ollama/..."
    messages=[{"role": "user", "content": "..."}],
)
```

`adapters.model` is a first-class member alongside `adapters.secrets` / `adapters.notifier`.
It resolves provider keys via the SecretStore into env, arms the configured observability
backend once, and registers the always-on emission counter — so you never hand-wire a logger.

`anthropic` is installed by default (core SDK). Per-provider SDKs for other providers
(`openai`, `google-genai`, `mistralai`, `cohere`) are **the adopter's own install** — not
shipped by research-vault. For most cross-provider research, the seam alone is sufficient.

**Anti-pattern (the P1 failure):** do NOT hand-roll `anthropic.Anthropic()`,
`openai.OpenAI()`, or a raw `litellm.completion(...)` in a harness. A hand-rolled client
**produces ZERO observability records** — the Haiku experiments logged nothing because the
harness called the model directly and wrote only local JSONL. The `ModelClient` seam is the
only supported path; a harness that instantiates its own provider client **fails review**.

### Observability — traces ≠ runs (both planes come from ONE seam)

The seam produces **two distinct** artifacts when configured — do not conflate them:

- **Plane A — traces.** Per-call request/response traces. Backend is `[observability].backend`:
  `weave` (W&B Weave — needs `pip install research-vault[observability]`), `langfuse`
  (adopter's own install), `local` (zero-infra default — one JSONL line per call at
  `<state_dir>/llm_calls.jsonl`), or `none`. **Weave traces do NOT appear in `rv wandb pull`.**
- **Plane B — runs.** A classic W&B **run** readable by `rv wandb pull` (score/aggregate
  provenance). Opt-in via `[observability].run_logging = true`; uses core `wandb` (no new dep).
  The run's `summary` carries the emission aggregates (calls, tokens, cost, latency p50/p95);
  `config` carries the pre-registered params.

**Test the wiring BEFORE a long run — don't discover at teardown that you logged nothing:**

```bash
rv observability probe     # rejects-only check of BOTH planes (no network, no spend)
rv observability status    # show backend / run-logging / W&B target / JSONL path
rv check --require-observability   # promote observability into the required preflight gate
```

If the seam is bypassed at runtime (a harness that called the model directly, or callbacks
reset), the `ModelClient` fires a **loud warn** at teardown (`assert_observed`) and raises
under `--require-observability` — the passive safety net behind the active `probe`.

## Step 1: Check the declared run-recipe

```bash
rv compute show
```

Shows: which backend is active (local / cluster), submit pattern, GPU tiers, W&B results
block, and any declared rules (cluster gotchas). This is the ground-truth recipe — not
what you remember from last session, not what you guessed.

## Step 2: Resolve env/tier/flags for this specific job

```bash
rv compute explain <model-or-job-name>
```

Returns: which backend, which conda env, which GPU tier, how many GPUs, the submit flags.
One command, no guessing.

## Step 3: Submit via the configured backend

The crew submits through the seam (the configured adapter in research_vault.toml +
the compute manifest). The backend handles the ssh + sbatch/qsub mechanics.

## Anti-patterns — NEVER do these

- **Do NOT trial-submit** to discover what partition/GPU/env to use. `rv compute show`
  and `rv compute explain` already declare it. Trial-submitting wastes queue time and
  corrupts the experiment log.
- **Do NOT hand-run** `ssh cluster sbatch --gres=gpu:1 ...` with flags you guessed.
  The manifest declares the submit pattern; the adapter sends it correctly.
- **Do NOT re-probe by running jobs** — `rv doctor` caches the cluster capabilities.

## If the recipe is wrong or incomplete

```bash
rv compute lesson add "<trigger>" "<fix>"
```

Capture the gotcha as a declared rule so the next run avoids it. The manifest improves
from real experience — lessons accumulate in `rules`, not in agent memory.

```bash
rv compute outcome add --job <name> --tier tp1 --result OOM
```

Record a run outcome (OOM/SUCCESS/FAILED) so the manifest learns from real results.

## Onboarding order (fresh instance)

```
rv compute init    # DECLARE: scaffold manifest with local + remote FILL blocks
  → edit FILL values in compute_manifest.json (host, submit_pattern, W&B entity/project)
rv doctor          # DISCOVER: probe each declared backend
rv compute show    # VERIFY: merged declared-where + discovered-what
```

`rv doctor` cannot see a cluster you have not declared. Declare first, then discover.
