"""observability_cli.py — SR-MODEL-SEAM: the `rv observability` verb.

When to use: DISCOVER + TEST your model-seam observability wiring BEFORE a long run
— not discover-at-teardown that you logged nothing (the P1 failure). Two subcommands:

  rv observability status  — show the configured backend, run-logging, wandb target,
                             and the local JSONL trace path. Reads config only.
  rv observability probe   — a rejects-only wiring check for BOTH planes (Plane A
                             traces + Plane B classic run) WITHOUT any network call
                             or model spend. Exit 0 = wired; exit 1 = would produce
                             ZERO records (missing dep/key/project).

The passive safety net (the ModelClient loud-warn + `rv check --require-observability`)
still catches a broken seam at runtime; this verb is the ACTIVE pre-run check.

IMPORT-LIGHT: the backend probes import litellm/weave lazily and guard ImportError,
so this module is safe to import on the `rv help` path with the toolkit absent.

sr: SR-MODEL-SEAM
"""
from __future__ import annotations

import argparse
import sys
from typing import Any


def _load_cfg():
    from .config import load_config
    return load_config()


def _cmd_status(cfg: Any) -> int:
    """Print the resolved observability configuration. Reads config only."""
    from .adapters.observability import resolve_run_logging_target

    obs = getattr(cfg, "observability", {}) or {}
    backend_name = str(obs.get("backend", "local"))
    # No project_slug at this layer (instance-level status, not a specific run) —
    # an empty project here is NORMAL: it resolves to the run's own slug at call-time.
    enabled, entity, project = resolve_run_logging_target(cfg)
    if project:
        target = f"{entity}/{project}" if entity else project
    else:
        target = f"{entity or '(default account)'}/<per-run slug, auto>"

    print("=== rv observability status ===")
    print(f"Plane A (traces) backend: {backend_name}")
    if backend_name == "local":
        print(f"  local JSONL trace path: {cfg.state_dir / 'llm_calls.jsonl'}")
    elif backend_name == "weave":
        from .adapters.observability import _resolve_weave_project
        print(f"  weave project: {_resolve_weave_project(cfg) or '(unresolved)'}")
    elif backend_name == "langfuse":
        print("  langfuse via litellm success/failure_callback (adopter's own install)")
    elif backend_name == "none":
        print("  (Plane-A tracing disabled)")
    print(f"Plane B (rv wandb pull-able run): {'enabled' if enabled else 'disabled'}")
    if enabled:
        print(f"  W&B run target: {target}")
    print()
    print("Reach from a harness:")
    print("  from research_vault.adapters import load_adapters")
    print("  adapters = load_adapters(cfg)")
    print("  resp = adapters.model.complete(model=..., messages=...)")
    print()
    print("Test the wiring before a run:  rv observability probe")
    return 0


def _cmd_probe(cfg: Any) -> int:
    """Probe BOTH planes without any network call. Exit 1 if a plane is unwired."""
    from .adapters.observability import (
        probe_run_logging,
        resolve_observability_backend,
    )

    print("=== rv observability probe (rejects-only; no network, no spend) ===")
    all_ok = True

    # Plane A — the configured trace backend.
    try:
        backend = resolve_observability_backend(cfg)
        a_ok, a_msg = backend.probe()
    except ValueError as exc:
        a_ok, a_msg = False, str(exc)
    except Exception as exc:
        a_ok, a_msg = False, f"probe error — {exc}"
    print(f"  [{'OK' if a_ok else 'FAIL'}] {a_msg}")
    all_ok = all_ok and a_ok

    # Plane B — classic W&B run (rv wandb pull-readable).
    try:
        b_ok, b_msg = probe_run_logging(cfg)
    except Exception as exc:
        b_ok, b_msg = False, f"run-logging probe error — {exc}"
    print(f"  [{'OK' if b_ok else 'FAIL'}] {b_msg}")
    all_ok = all_ok and b_ok

    print()
    if all_ok:
        print("Result: OK — the seam would produce records. Safe to run.")
        return 0
    print(
        "Result: FAIL — a run now would produce ZERO records on a failing plane.\n"
        "  Fix the FAIL line(s) above, or set [observability].backend=none to opt out.\n"
        "  Do NOT start a long run and discover at teardown that you logged nothing."
    )
    return 1


def build_parser(
    parent: "argparse._SubParsersAction | None" = None,
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``observability`` verb.

    When to use: ``rv observability probe`` to TEST your model-seam wiring (both
    planes) before a long run, or ``rv observability status`` to see the configured
    backend + W&B target + local trace path.
    """
    desc = (
        "Discover and TEST the model-seam observability wiring before a run. "
        "Subcommands: `status` (show backend / run-logging / W&B target / JSONL path), "
        "`probe` (rejects-only check of BOTH planes — Plane A traces + Plane B classic "
        "run — with NO network call or model spend; exit 1 if a run would produce zero "
        "records). Anti-pattern: do NOT start a long run and discover at teardown that "
        "you produced zero records — run `rv observability probe` first."
    )
    if parent is not None:
        p = parent.add_parser(
            "observability",
            help="Test the model-seam observability wiring before a run (both planes).",
            description=desc,
        )
    else:
        p = argparse.ArgumentParser(prog="rv observability", description=desc)

    sub = p.add_subparsers(dest="obs_cmd", required=True)
    sub.add_parser("status", help="Show the configured observability backend + targets.")
    sub.add_parser("probe", help="Rejects-only wiring check for both planes (no network).")

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch: rv observability <status|probe>."""
    try:
        cfg = _load_cfg()
    except Exception as exc:
        print(f"rv observability: config error: {exc}", file=sys.stderr)
        return 1

    if args.obs_cmd == "status":
        return _cmd_status(cfg)
    if args.obs_cmd == "probe":
        return _cmd_probe(cfg)

    print(f"rv observability: unknown subcommand {args.obs_cmd!r}", file=sys.stderr)
    return 1
