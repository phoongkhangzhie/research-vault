"""experiment_run.py — SR-MODEL-SEAM S6: Plane-B classic W&B run logging.

When to use: wrap an experiment's real model-calling work so it emits a **classic
W&B run** readable by ``rv wandb pull`` — distinct from Plane-A traces (weave/JSONL).
The seam produces BOTH when configured; this module owns Plane B.

The run is logged into the EXACT shapes ``wandb_pull.py::fetch_run`` reads back:
  run.config  — the pre-registered params (model, seed, temperature, top_p,
                max_tokens, tokenizer, num_fewshot — the ``_REPRO_CONFIG_ALIAS_TABLE``
                keys, so ``repro_*`` populate on pull-back).
  run.summary — the ``_EmissionCounter`` aggregates (calls, prompt/completion/total
                tokens, total_cost_usd, latency p50/p95) merged with the experiment's
                analysis result metrics at teardown.
  run.commit  — auto-captured by ``wandb.init`` from the repo cwd.

Flow: ``wandb.init(entity, project, config=<pre-reg>)`` → run the real calls through
``adapters.model`` (the emission counter accrues) → ``run.summary.update(aggregates |
analysis_metrics)`` → ``run.finish()``. The run path ``entity/project/<run_id>`` is
surfaced via the Notifier and (optionally) written to the experiment note's
``results_wandb_run`` field so the score/analyze node can ``rv wandb pull`` it.

Reliability (charter §2): ``run_logging=true`` with an unresolvable key / project, or
a ``wandb.init`` that raises → a LOUD Notifier warn (raise under
``require_observability``). Uses core ``wandb`` — NO new dep. ``wandb`` imported lazily.

sr: SR-MODEL-SEAM
"""
from __future__ import annotations

import sys
from typing import Any, Callable

from .adapters.observability import resolve_run_logging_target


class RunLoggingError(RuntimeError):
    """Raised when a required Plane-B run-logging guarantee is violated."""


def _warn(notifier: Any, msg: str) -> None:
    if notifier is not None:
        try:
            notifier.notify(msg, level="warn", subject="observability")
            return
        except Exception:
            pass
    print(f"[WARN] observability: {msg}", file=sys.stderr)


def log_experiment_run(
    cfg: Any,
    adapters: Any,
    *,
    config_params: dict[str, Any],
    analysis_metrics: dict[str, Any] | None,
    run_fn: Callable[[Any], Any],
    run_name: str | None = None,
    experiment_note: Any = None,
) -> str:
    """Run ``run_fn`` inside a classic W&B run and log aggregates + metrics + config.

    Parameters
    ----------
    cfg:             the loaded Config (reads [observability].run_logging + target).
    adapters:        the AdapterSet — ``adapters.model`` is the seam whose emission
                     counter accrues during ``run_fn``; ``adapters.notifier`` warns.
                     ``adapters.require_observability`` promotes warns to raises.
    config_params:   pre-registered params → run.config (alias-table keys).
    analysis_metrics: result metrics merged into run.summary at teardown (or None).
    run_fn:          callable ``run_fn(model_client)`` that makes the real calls.
    run_name:        optional W&B run display name.
    experiment_note: optional Path to an experiment note — its ``results_wandb_run``
                     frontmatter field is filled with the run path.

    Returns
    -------
    ``"entity/project/<run_id>"`` on success, or ``""`` when run-logging is disabled.
    Always executes ``run_fn`` (even when logging is disabled) so the caller's real
    work still runs; only the W&B side is gated.
    """
    notifier = getattr(adapters, "notifier", None)
    require = bool(getattr(adapters, "require_observability", False))
    model_client = adapters.model  # arms Plane A + registers the emission counter

    enabled, entity, project = resolve_run_logging_target(cfg)
    if not enabled:
        # Run-logging opt-out — just execute the work, no W&B run.
        run_fn(model_client)
        return ""

    # Preconditions — surface loudly, don't silently skip (charter §2).
    if not project.strip():
        msg = (
            "run-logging (Plane B): enabled but no W&B project resolvable — set "
            "[observability].wandb_project or the compute manifest results.wandb block. "
            "No classic run will be logged."
        )
        _warn(notifier, msg)
        if require:
            raise RunLoggingError(msg)
        run_fn(model_client)
        return ""

    try:
        import wandb  # lazy — core dep
    except Exception as exc:
        msg = f"run-logging (Plane B): `wandb` not importable — {exc}. No run logged."
        _warn(notifier, msg)
        if require:
            raise RunLoggingError(msg) from exc
        run_fn(model_client)
        return ""

    # Init the classic run. run.commit is auto-captured from the repo cwd.
    try:
        run = wandb.init(
            entity=entity or None,
            project=project,
            name=run_name,
            config=dict(config_params or {}),
        )
    except Exception as exc:
        msg = f"run-logging (Plane B): wandb.init failed — {exc}. No run logged."
        _warn(notifier, msg)
        if require:
            raise RunLoggingError(msg) from exc
        run_fn(model_client)
        return ""

    run_path = ""
    try:
        # Do the real work — the emission counter accrues during these calls.
        run_fn(model_client)
        # Wait for litellm's threaded/async success callbacks to land BEFORE reading
        # the aggregates — litellm dispatches them off the calling thread, so a naive
        # read here sees calls=0/total_tokens=0 even for a healthy run (the Plane-B
        # round-trip defect: run.summary["calls"] == 0). flush() is bounded + a no-op
        # once the counter has caught up.
        model_client.flush()
        # Teardown: aggregates (Plane B run.summary shape) merged with analysis metrics.
        summary: dict[str, Any] = dict(model_client.stats.as_summary())
        if analysis_metrics:
            summary.update(analysis_metrics)
        run.summary.update(summary)
        # Compose the run path in the exact shape rv wandb pull parses.
        run_entity = getattr(run, "entity", "") or entity
        run_project = getattr(run, "project", "") or project
        run_id = getattr(run, "id", "")
        run_path = f"{run_entity}/{run_project}/{run_id}"
    finally:
        try:
            run.finish()
        except Exception:
            pass

    # Surface the run path so the score/analyze node can rv wandb pull it.
    if run_path:
        if notifier is not None:
            try:
                notifier.notify(
                    f"Plane-B W&B run logged: {run_path} — pull with "
                    f"`rv wandb pull {run_path} --experiment <id> --project <slug>`",
                    level="info",
                    subject="observability",
                    payload={"results_wandb_run": run_path},
                )
            except Exception:
                pass
        if experiment_note is not None:
            _write_run_path_to_note(experiment_note, run_path)

    # Fire the unforgettable-seam guard (calls>0 but counter==0 → loud/raise).
    model_client.assert_observed()
    return run_path


def _write_run_path_to_note(note_path: Any, run_path: str) -> None:
    """Fill the experiment note's ``results_wandb_run`` frontmatter field. Best-effort."""
    try:
        from pathlib import Path
        from .wandb_pull import _update_frontmatter
        p = Path(note_path)
        if p.exists():
            _update_frontmatter(p, {"results_wandb_run": run_path})
    except Exception:
        pass
