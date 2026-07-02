"""compute.py — `rv compute` — compute manifest: declare + discover "how to run here".

When to use: ``rv compute show`` to see how to run on this environment (backends,
conda envs, GPU tiers, rules, model quirks). ``rv compute explain <job>`` to
resolve which env/tier/flags a specific job/model would use. ``rv compute lesson
add "<trigger>" "<fix>"`` to capture a cluster gotcha as a declared rule.
``rv compute outcome add`` to record a run result (OOM/SUCCESS) so the manifest
improves from real experience.

Anti-pattern: do NOT re-probe the cluster by trial-submit to learn what env/tier
to use — ``rv compute show`` / ``rv doctor`` already declare it. Memory is
flimsy; this tooling makes it robust.

The manifest is stored at ``<state_dir>/compute_manifest.json`` — the instance's
state_dir, NOT ~/vault.  One manifest per Research Vault instance.

Backend archetypes supported in the manifest:
  local         — subprocess (zero-infra default; LocalSubprocess adapter)
  ssh           — remote host, setsid/nohup background-run (RemoteBackend, SR-7)
  ssh+slurm     — sbatch/sacct (RemoteBackend, SR-7)
  ssh+pbs       — qsub/qstat (RemoteBackend, SR-7)
  generic       — adopter-declared submit + jobid_parse + status + state_map (SR-7)
  container modifier — orthogonal ``container`` field on any profile (not a 5th row)

SR-7 extended the profile schema with per-profile execution fields:
  jobid_parse   — regex to extract job id from submit stdout
  status_cmd    — command to query job state (with {jobid} placeholder)
  status_parse  — regex to extract raw state from status_cmd stdout
  state_map     — maps raw scheduler states to Protocol states (PENDING/RUNNING/DONE/FAILED)
Built-in defaults for slurm/pbs/ssh archetypes mean adopters need not declare
these fields unless overriding the defaults. SR-6 manifests without these fields
remain valid — defaults are applied at runtime by RemoteBackend.

Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import Any

from .config import Config, load_config

# ---------------------------------------------------------------------------
# Manifest schema constants
# ---------------------------------------------------------------------------

MANIFEST_FILE = "compute_manifest.json"

_VALID_ARCHETYPES = {"local", "ssh", "ssh+slurm", "ssh+pbs", "generic"}


# ---------------------------------------------------------------------------
# Manifest I/O
# ---------------------------------------------------------------------------

def _manifest_path(cfg: Config) -> Path:
    """Return the absolute path to the compute manifest JSON."""
    return cfg.state_dir / MANIFEST_FILE


def _default_manifest() -> dict[str, Any]:
    """Return a minimal default compute manifest (zero-infra local backend)."""
    return {
        "backends": {
            "active": ["local"],
            "profiles": {
                "local": {
                    "archetype": "local",
                    # No submit_pattern — LocalSubprocess handles this.
                },
            },
        },
        "conda_envs": {},
        "gpu_tiers": {
            "tp1": {"gpus": 1, "models": ["<=7B"]},
        },
        "rules": [],
        "model_quirks": {},
        "run_outcomes": [],
    }


def _load_manifest(cfg: Config) -> dict[str, Any]:
    """Load the compute manifest from state_dir.

    Returns the default manifest if no manifest file exists.
    Never raises on missing file — returns a usable default instead.
    """
    p = _manifest_path(cfg)
    if not p.exists():
        return _default_manifest()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default_manifest()


def _save_manifest(cfg: Config, manifest: dict[str, Any]) -> None:
    """Persist the compute manifest to state_dir."""
    p = _manifest_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# cmd_show — rv compute show
# ---------------------------------------------------------------------------

def cmd_show(cfg: Config) -> int:
    """Print the declared compute environment (the run-recipe).

    Returns exit code 0 always — missing manifest degrades to a default view.
    """
    m = _load_manifest(cfg)
    manifest_exists = _manifest_path(cfg).exists()

    lines: list[str] = ["=== rv compute show — declared compute environment ===", ""]

    if not manifest_exists:
        lines.append(
            "No compute manifest found. A default (local-only) environment is shown."
        )
        lines.append(
            f"  Create: edit {cfg.state_dir / MANIFEST_FILE} or use `rv compute lesson add`."
        )
        lines.append("")

    # --- Backends ---
    backends = m.get("backends", {})
    active = backends.get("active", ["local"])
    profiles = backends.get("profiles", {})
    lines.append("Backends:")
    lines.append(f"  active: {', '.join(active) or '(none)'}")
    for name, prof in profiles.items():
        archetype = prof.get("archetype", "?")
        extra = []
        if "host" in prof:
            extra.append(f"host={prof['host']}")
        submit_val = prof.get("submit_pattern") or prof.get("submit", "")
        if submit_val:
            truncated = submit_val[:50] + "…" if len(submit_val) > 50 else submit_val
            extra.append(f"submit='{truncated}'")
        if "jobid_parse" in prof:
            jp = prof["jobid_parse"]
            extra.append(f"jobid_parse='{jp[:40]}…'" if len(jp) > 40 else f"jobid_parse='{jp}'")
        if "status_cmd" in prof:
            sc = prof["status_cmd"]
            extra.append(f"status_cmd='{sc[:40]}…'" if len(sc) > 40 else f"status_cmd='{sc}'")
        if "status_parse" in prof:
            sp = prof["status_parse"]
            extra.append(f"status_parse='{sp[:40]}…'" if len(sp) > 40 else f"status_parse='{sp}'")
        if "state_map" in prof:
            sm = prof["state_map"]
            extra.append(f"state_map({len(sm)} entries)")
        if "container" in prof:
            c = prof["container"]
            extra.append(f"container={c.get('runtime','?')}:{c.get('image','?')}")
        suffix = f"  ({', '.join(extra)})" if extra else ""
        lines.append(f"  {name}: archetype={archetype}{suffix}")
    lines.append("")

    # --- Conda envs ---
    conda_envs = m.get("conda_envs", {})
    if conda_envs:
        lines.append("Conda environments:")
        for env_name, env_info in conda_envs.items():
            purpose = env_info.get("purpose", "")
            python = env_info.get("python", "")
            parts = []
            if purpose:
                parts.append(purpose)
            if python:
                parts.append(f"python={python}")
            suffix = f"  ({', '.join(parts)})" if parts else ""
            lines.append(f"  {env_name}{suffix}")
        lines.append("")

    # --- GPU tiers ---
    gpu_tiers = m.get("gpu_tiers", {})
    if gpu_tiers:
        lines.append("GPU tiers:")
        for tier_name, tier_info in gpu_tiers.items():
            gpus = tier_info.get("gpus", "?")
            models = tier_info.get("models", [])
            models_str = ", ".join(models) if models else "?"
            lines.append(f"  {tier_name}: gpus={gpus}  models={models_str}")
        lines.append("")

    # --- Rules (declared gotchas) ---
    rules = m.get("rules", [])
    if rules:
        lines.append("Rules (declared gotchas):")
        for r in rules:
            trigger = r.get("trigger", "?")
            fix = r.get("fix", "?")
            lines.append(f"  [trigger] {trigger}")
            lines.append(f"    -> {fix}")
        lines.append("")

    # --- Model quirks ---
    model_quirks = m.get("model_quirks", {})
    if model_quirks:
        lines.append("Model quirks:")
        for model, quirks in model_quirks.items():
            parts = [f"{k}={v}" for k, v in quirks.items()]
            lines.append(f"  {model}: {', '.join(parts)}")
        lines.append("")

    # --- Run outcomes (recent) ---
    outcomes = m.get("run_outcomes", [])
    if outcomes:
        lines.append(f"Run outcomes ({len(outcomes)} recorded):")
        for o in outcomes[-5:]:  # show last 5
            job = o.get("job", "?")
            tier = o.get("tier", "?")
            result = o.get("result", "?")
            ts = o.get("ts", "")[:10]
            lines.append(f"  {ts}  {job}  tier={tier}  result={result}")
        lines.append("")

    print("\n".join(lines))
    return 0


# ---------------------------------------------------------------------------
# cmd_explain — rv compute explain <job>
# ---------------------------------------------------------------------------

def cmd_explain(cfg: Config, job: str) -> dict[str, Any] | None:
    """Resolve env/tier/flags from the manifest for a job/model.

    Returns a dict of resolved fields, or None if the manifest is empty.
    Always returns something (graceful on unknown job).
    """
    m = _load_manifest(cfg)
    model_quirks = m.get("model_quirks", {})
    gpu_tiers = m.get("gpu_tiers", {})
    conda_envs = m.get("conda_envs", {})
    backends = m.get("backends", {})
    active_backends = backends.get("active", ["local"])
    profiles = backends.get("profiles", {})

    # Start with defaults
    resolved: dict[str, Any] = {
        "job": job,
        "backend": active_backends[0] if active_backends else "local",
        "conda_env": None,
        "tier": None,
        "gpus": None,
        "submit_flags": None,
        "model_quirks": {},
    }

    # Apply model quirks if the job matches a known model
    quirks = model_quirks.get(job, {})
    resolved["model_quirks"] = quirks
    if quirks:
        # Promote known quirk fields to top-level for convenience
        for key in ("tp", "conda_env", "tier", "flashinfer_cache"):
            if key in quirks:
                resolved[key] = quirks[key]

    # Resolve tier → gpus
    tier_name = resolved.get("tier")
    if tier_name and tier_name in gpu_tiers:
        resolved["gpus"] = gpu_tiers[tier_name].get("gpus")

    # Resolve submit_flags from active backend profile
    active_name = resolved["backend"]
    profile = profiles.get(active_name, {})
    submit_pattern = profile.get("submit_pattern")
    if submit_pattern:
        resolved["submit_flags"] = submit_pattern

    return resolved


def _print_explain(job: str, resolved: dict[str, Any]) -> None:
    """Print the explain result in a human-readable format."""
    lines = [f"=== rv compute explain: {job} ===", ""]
    for key, val in resolved.items():
        if key == "job":
            continue
        if val is None:
            continue
        if isinstance(val, dict):
            if val:
                lines.append(f"  {key}:")
                for k, v in val.items():
                    lines.append(f"    {k}: {v}")
        else:
            lines.append(f"  {key}: {val}")
    if not any(v for k, v in resolved.items() if k != "job" and v is not None):
        lines.append("  (no manifest entries found for this job — defaults apply)")
    lines.append("")
    print("\n".join(lines))


# ---------------------------------------------------------------------------
# cmd_lesson_add — rv compute lesson add "<trigger>" "<fix>"
# ---------------------------------------------------------------------------

def cmd_lesson_add(cfg: Config, trigger: str, fix: str) -> int:
    """Append a rule (captured gotcha) to the compute manifest.

    The rule is stored in the manifest's ``rules`` list as:
      {"trigger": trigger, "fix": fix}

    This is the "lesson capture" path: when a run fails and the fix is found,
    record it AGAINST the environment (not in agent memory) so the next
    engineer reads it.
    """
    m = _load_manifest(cfg)
    if "rules" not in m:
        m["rules"] = []
    m["rules"].append({"trigger": trigger, "fix": fix})
    _save_manifest(cfg, m)
    print(f"[OK] Rule recorded: trigger={trigger!r} -> {fix!r}")
    return 0


# ---------------------------------------------------------------------------
# cmd_outcome_add — rv compute outcome add
# ---------------------------------------------------------------------------

def cmd_outcome_add(cfg: Config, job: str, tier: str, result: str) -> int:
    """Append a run outcome to the compute manifest.

    Outcomes are stored in ``run_outcomes`` list as:
      {"job": job, "tier": tier, "result": result, "ts": <iso8601>}

    This is the adaptive capture half: real run results (OOM, SUCCESS) are
    recorded so the manifest improves from experience. No ML inference.
    """
    m = _load_manifest(cfg)
    if "run_outcomes" not in m:
        m["run_outcomes"] = []
    ts = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
    m["run_outcomes"].append({"job": job, "tier": tier, "result": result, "ts": ts})
    _save_manifest(cfg, m)
    print(f"[OK] Outcome recorded: job={job!r} tier={tier!r} result={result!r}")
    return 0


# ---------------------------------------------------------------------------
# CLI verb
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``compute`` verb."""
    desc = (
        "Declare, discover, and cache 'how to run here'. "
        "Sub-commands: show (print run-recipe), explain <job> (resolve env/tier/flags), "
        "lesson add (capture gotcha as rule), outcome add (record run result). "
        "Anti-pattern: do NOT re-probe the cluster by trial-submit to learn what "
        "env/tier to use — rv compute show / rv doctor already declare it."
    )
    if parent is not None:
        p = parent.add_parser(
            "compute",
            help="Compute manifest: declare + discover 'how to run here' (SR-6).",
            description=desc,
        )
    else:
        p = argparse.ArgumentParser(prog="rv compute", description=desc)

    sub = p.add_subparsers(dest="compute_cmd", required=True)

    # show
    sub.add_parser(
        "show",
        help="Print the declared compute environment (backends, envs, tiers, rules).",
    )

    # explain
    exp_p = sub.add_parser(
        "explain",
        help="Resolve env/tier/flags from the manifest for a job/model.",
    )
    exp_p.add_argument("job", help="Job or model name to resolve.")

    # lesson (sub-namespace)
    lesson_p = sub.add_parser(
        "lesson",
        help="Manage compute lessons (captured gotchas as declared rules).",
    )
    lesson_sub = lesson_p.add_subparsers(dest="lesson_cmd", required=True)
    la_p = lesson_sub.add_parser("add", help="Add a lesson: trigger → fix.")
    la_p.add_argument("trigger", help="The trigger condition (e.g. 'download >10GB').")
    la_p.add_argument("fix", help="The fix/remedy (e.g. 'use sbatch not nohup').")

    # outcome (sub-namespace)
    outcome_p = sub.add_parser(
        "outcome",
        help="Record a run outcome (OOM/SUCCESS) so the manifest improves from real runs.",
    )
    outcome_sub = outcome_p.add_subparsers(dest="outcome_cmd", required=True)
    oa_p = outcome_sub.add_parser("add", help="Record a run outcome.")
    oa_p.add_argument("--job", required=True, help="Job or run name.")
    oa_p.add_argument(
        "--tier",
        required=True,
        help="GPU tier used (e.g. tp1, tp2, tp4).",
    )
    oa_p.add_argument(
        "--result",
        required=True,
        choices=["OOM", "SUCCESS", "FAILED", "TIMEOUT"],
        help="Run outcome.",
    )

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch ``rv compute`` subcommands."""
    # Support injected cfg (for tests) or load from config
    cfg: Config = getattr(args, "_cfg", None) or load_config()

    cmd = getattr(args, "compute_cmd", None)

    if cmd == "show":
        return cmd_show(cfg)

    if cmd == "explain":
        resolved = cmd_explain(cfg, args.job)
        _print_explain(args.job, resolved or {"job": args.job})
        return 0

    if cmd == "lesson":
        lesson_cmd = getattr(args, "lesson_cmd", None)
        if lesson_cmd == "add":
            return cmd_lesson_add(cfg, args.trigger, args.fix)
        print(f"rv compute lesson: unknown subcommand {lesson_cmd!r}", file=sys.stderr)
        return 1

    if cmd == "outcome":
        outcome_cmd = getattr(args, "outcome_cmd", None)
        if outcome_cmd == "add":
            return cmd_outcome_add(cfg, args.job, args.tier, args.result)
        print(f"rv compute outcome: unknown subcommand {outcome_cmd!r}", file=sys.stderr)
        return 1

    print(f"rv compute: unknown subcommand {cmd!r}", file=sys.stderr)
    return 1
