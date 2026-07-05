# how to run here — compute run recipe

**Before submitting any experiment job, read the run-recipe and resolve the env/tier.**

## Model seam — litellm is the primary provider interface

Research Vault harnesses call providers through **`litellm`** — the unified provider seam
(SR-PKG). This makes cross-provider studies trivial: swap the `model=` parameter without
changing harness logic.

```python
import litellm

response = litellm.completion(
    model="gpt-4o",            # or "claude-..." / "gemini/..." / "ollama/..."
    messages=[{"role": "user", "content": "..."}],
)
```

Per-provider SDKs (`anthropic`, `openai`, `google-generativeai`, `mistralai`, `cohere`) are
installed by default as secondary dependencies — use them for provider-specific features not
exposed by litellm.

**Anti-pattern:** do NOT hard-wire `anthropic.Anthropic()` or `openai.OpenAI()` directly into
harnesses that run cross-provider comparisons — use litellm so provider-swap is a one-line
config change, not a harness rewrite.

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
