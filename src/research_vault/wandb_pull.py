# SPDX-License-Identifier: AGPL-3.0-or-later
"""wandb_pull.py — rv wandb pull: fetch W&B run metrics via the wandb SDK.

When to use: ``rv wandb pull <run-id>`` when an experiment logged to W&B and you
need its final metrics, or ``rv wandb pull <run-id> --experiment <id>`` to attach
metrics (hash-verified) to an experiment note.

Auth: ``WANDB_API_KEY`` resolved via ``EnvSecretStore`` (env-var first → keyring,
cross-platform), then passed to the SDK — consistent with all other secret handling.
The SDK reads the key from env; no manual HTTP auth / GraphQL POST needed.

W&B is a **documented prerequisite** (like Claude/asta): if ``import wandb`` fails,
``rv wandb pull`` prints a clear install message and exits cleanly — never a raw
ImportError stack trace.

Anti-pattern: do NOT pip install wandb and script the SDK directly without going
through ``rv wandb pull`` — and do NOT hand-copy metrics into a finding.
Use ``rv wandb pull <run-id> --experiment <id> --project <slug>`` to attach
results→hash→run provenance to the experiment note.

Run-id grammar:
  bare-id                — entity + project from WANDB_ENTITY/WANDB_PROJECT env
  project/run-id         — entity from WANDB_ENTITY env
  entity/project/run-id  — fully qualified, no env vars needed

SR-WB + SR-EXP-REPRO.
SR-WB: No stdlib HTTP client needed — the SDK handles the REST/GraphQL transport.
SR-EXP-REPRO: fetch_run now returns dict(run.config) + run.metadata; wandb_pull
  writes a Layer-1 config artifact + populates 22 flat repro_* scalars via the
  alias table. Empty keys → sentinel "not-recorded-in-provenance" (never blank).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from .adapters.base import EnvSecretStore
from .config import Config, load_config
from .hashing import hash_file as _hash_file  # C2: shared hasher — never duplicate
from .note import REPRO_SENTINEL


# ---------------------------------------------------------------------------
# SR-EXP-REPRO: alias table + metadata map
# ---------------------------------------------------------------------------

# Alias table: maps run.config keys (in priority order within each group) to
# the promoted repro_* flat scalar. First matching key wins.
# Format: list of (repro_field, [candidate_config_keys_in_priority_order])
_REPRO_CONFIG_ALIAS_TABLE: list[tuple[str, list[str]]] = [
    ("repro_seed",               ["seed", "random_seed"]),
    ("repro_model_id",           ["model", "model_name", "pretrained"]),
    ("repro_model_revision",     ["model_revision", "revision"]),
    ("repro_decode_temperature", ["temperature"]),
    ("repro_decode_top_p",       ["top_p"]),
    ("repro_decode_max_tokens",  ["max_new_tokens", "max_tokens"]),
    ("repro_num_fewshot",        ["num_fewshot", "n_shot", "num_shots"]),
    ("repro_tokenizer",          ["tokenizer", "tokenizer_name"]),
    ("repro_eval_harness",       ["harness_version", "lm_eval_version"]),
]

# Metadata map: maps run.metadata keys to repro_* fields.
# For packages, the value may be a list; join with ";" for flat frontmatter.
_REPRO_META_MAP: list[tuple[str, list[str]]] = [
    ("repro_env_python",     ["python"]),
    ("repro_env_packages",   ["packages"]),
    ("repro_cost_gpu_hours", ["gpu_hours", "gpu_time_hours"]),
]


# ---------------------------------------------------------------------------
# SDK import guard
# ---------------------------------------------------------------------------

def _import_wandb():
    """Import the wandb SDK with a clear, friendly error if not installed.

    W&B is a documented prerequisite — this guard never lets a raw ImportError
    propagate. Callers catch the ImportError and print the install message.
    """
    try:
        import wandb  # type: ignore[import]
        return wandb
    except ImportError:
        raise ImportError(
            "W&B SDK is a prerequisite for `rv wandb`: pip install wandb\n"
            "  Or: uv add wandb\n"
            "  Get a free account at: https://wandb.ai"
        )


# ---------------------------------------------------------------------------
# Run-id grammar
# ---------------------------------------------------------------------------

def parse_run_id(
    run_id: str,
    *,
    entity: str | None = None,
    project: str | None = None,
) -> tuple[str, str, str]:
    """Parse a W&B run-id string into (entity, project, run_name).

    Accepted forms:
      bare-id               → entity from param/WANDB_ENTITY; project from param/WANDB_PROJECT
      project/run-id        → entity from param/WANDB_ENTITY
      entity/project/run-id → fully qualified, no env vars needed

    Raises ValueError with a clear message if entity or project cannot be resolved.
    """
    parts = run_id.split("/")
    if len(parts) == 1:
        ent = entity or os.environ.get("WANDB_ENTITY", "").strip()
        proj = project or os.environ.get("WANDB_PROJECT", "").strip()
        run_name = parts[0]
        if not ent:
            raise ValueError(
                f"W&B run id {run_id!r} is a bare id but WANDB_ENTITY is not set.\n"
                "  Pass entity/project/run-id, or set WANDB_ENTITY + WANDB_PROJECT."
            )
        if not proj:
            raise ValueError(
                f"W&B run id {run_id!r} is a bare id but WANDB_PROJECT is not set.\n"
                "  Pass entity/project/run-id, or set WANDB_ENTITY + WANDB_PROJECT."
            )
    elif len(parts) == 2:
        ent = entity or os.environ.get("WANDB_ENTITY", "").strip()
        proj, run_name = parts
        if not ent:
            raise ValueError(
                f"W&B run id {run_id!r} is a project/run-id but WANDB_ENTITY is not set.\n"
                "  Pass entity/project/run-id, or set WANDB_ENTITY."
            )
    elif len(parts) == 3:
        ent, proj, run_name = parts
    else:
        raise ValueError(
            f"Invalid W&B run id {run_id!r}. "
            "Expected: bare-id, project/run-id, or entity/project/run-id."
        )
    return ent, proj, run_name


# ---------------------------------------------------------------------------
# Run fetch (SDK-backed)
# ---------------------------------------------------------------------------

def fetch_run(entity: str, project: str, run_name: str, api_key: str) -> dict[str, Any]:
    """Fetch a W&B run's state, summary metrics, config, and metadata via the wandb SDK.

    Sets ``WANDB_API_KEY`` in env before constructing the API client, consistent
    with EnvSecretStore's cross-platform seam (env-first → keyring).

    Returns a dict with keys:
      name          — run name (string id)
      displayName   — human-readable name (run.display_name)
      state         — 'running'/'finished'/'failed'/'crashed'/'killed'/'preempted'/…
      commit        — git SHA of the code that produced the run (or empty string)
      summaryMetrics — dict of metric-name → value (run.summary)
      config        — dict(run.config): full hyperparameter/config snapshot (SR-EXP-REPRO)
      metadata      — dict(run.metadata): env info (python version, packages, …) (SR-EXP-REPRO)

    Raises ImportError if the wandb SDK is not installed (friendly message).
    Raises ValueError if the project or run is not found.
    Raises wandb.CommError / wandb.Error on network/auth errors.
    """
    wandb = _import_wandb()

    # Export the key so the SDK picks it up from env (consistent secret seam)
    os.environ["WANDB_API_KEY"] = api_key

    api = wandb.Api()
    path = f"{entity}/{project}/{run_name}"
    try:
        run = api.run(path)
    except Exception as exc:
        # wandb.CommError, wandb.Error, etc.
        msg = str(exc)
        if "not found" in msg.lower() or "does not exist" in msg.lower():
            raise ValueError(f"W&B run {path!r} not found (or API key lacks access).") from exc
        raise

    # SR-EXP-REPRO: capture full config + metadata for Layer-1 artifact + alias map
    run_config: dict[str, Any] = dict(run.config) if run.config else {}
    run_metadata: dict[str, Any] = dict(run.metadata) if run.metadata else {}

    return {
        "name": run.name,
        "displayName": getattr(run, "display_name", "") or "",
        "state": run.state or "unknown",
        "commit": getattr(run, "commit", "") or "",
        "summaryMetrics": dict(run.summary),
        "config": run_config,
        "metadata": run_metadata,
    }


# ---------------------------------------------------------------------------
# Frontmatter update helper
# ---------------------------------------------------------------------------

def _update_frontmatter(note_path: Path, updates: dict[str, str]) -> None:
    """Update (or add) frontmatter fields in a markdown note in-place.

    Only touches the flat frontmatter block (---...---). Existing fields not
    in updates are preserved unchanged. New fields are appended before the
    closing ---. The body is preserved verbatim.

    Matches note.py's _parse_frontmatter contract (flat ``^(\\w+):\\s*(.*)$``).
    """
    text = note_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        fm_lines = ["---"]
        for k, v in updates.items():
            fm_lines.append(f"{k}: {v}")
        fm_lines.append("---")
        note_path.write_text("\n".join(fm_lines) + "\n" + text, encoding="utf-8")
        return

    end = text.find("\n---", 3)
    if end == -1:
        return  # Malformed frontmatter — leave unchanged

    fm_block = text[3:end].strip()
    body_tail = text[end + 4:]

    lines: list[str] = []
    updated_keys: set[str] = set()
    for line in fm_block.splitlines():
        m = re.match(r"^(\w+):\s*(.*)$", line)
        if m and m.group(1) in updates:
            key = m.group(1)
            lines.append(f"{key}: {updates[key]}")
            updated_keys.add(key)
        else:
            lines.append(line)

    for k, v in updates.items():
        if k not in updated_keys:
            lines.append(f"{k}: {v}")

    new_fm = "---\n" + "\n".join(lines) + "\n---"
    note_path.write_text(new_fm + body_tail, encoding="utf-8")


# ---------------------------------------------------------------------------
# High-level wandb_pull
# ---------------------------------------------------------------------------

def _resolve_wandb_from_manifest(cfg: Config) -> tuple[str, str]:
    """Read W&B entity + project from the compute manifest (SR-CO results.wandb block).

    Returns (entity, project) strings — empty string when not set or manifest absent.
    Env vars (WANDB_ENTITY / WANDB_PROJECT) take priority over the manifest; this
    function reads raw manifest values. Callers must apply env-over-manifest.

    Values starting with "FILL" are treated as unconfigured and returned as "".
    """
    try:
        from .compute import _load_manifest, _manifest_path, _FILL_PREFIX
        if not _manifest_path(cfg).exists():
            return "", ""
        manifest = _load_manifest(cfg)
        wandb_block = manifest.get("results", {}).get("wandb", {})
        entity_raw = wandb_block.get("entity", "").strip()
        project_raw = wandb_block.get("project", "").strip()
        entity = "" if entity_raw.startswith(_FILL_PREFIX) else entity_raw
        project = "" if project_raw.startswith(_FILL_PREFIX) else project_raw
        return entity, project
    except Exception:
        return "", ""


def _resolve_repro_hw(cfg: Config) -> str:
    """Read the active backend from the SR-6 compute manifest for repro_hw.

    Returns the active backend name if the manifest file exists and specifies
    a non-empty active list, otherwise the REPRO_SENTINEL.
    Defers entirely to the manifest — never re-probes hardware. (§5J.14)
    """
    try:
        from .compute import _load_manifest, _manifest_path
        if not _manifest_path(cfg).exists():
            return REPRO_SENTINEL
        manifest = _load_manifest(cfg)
        active = manifest.get("backends", {}).get("active", [])
        if active:
            return active[0]
    except Exception:
        pass
    return REPRO_SENTINEL


def _resolve_repro_dataset(
    cfg: Config, dataset_id: str
) -> tuple[str, str]:
    """Read location and hash from an SR-8 dataset note.

    Returns (repro_dataset_id, repro_dataset_hash).  If the note is not found
    or is missing the hash field, returns (REPRO_SENTINEL, REPRO_SENTINEL).
    Never re-enters data — links the existing note only. (§5J.14)
    """
    dataset_note = cfg.datasets_root / f"{dataset_id}.md"
    if not dataset_note.exists():
        return REPRO_SENTINEL, REPRO_SENTINEL
    try:
        from .note import _parse_frontmatter
        ds_text = dataset_note.read_text(encoding="utf-8")
        ds_fields, _ = _parse_frontmatter(ds_text)
        hash_val = ds_fields.get("hash", "").strip() or REPRO_SENTINEL
        return dataset_id, hash_val
    except Exception:
        return REPRO_SENTINEL, REPRO_SENTINEL


def wandb_pull(
    run_id: str,
    *,
    experiment: str | None = None,
    project_slug: str | None = None,
    dataset_id: str | None = None,
    config: Config | None = None,
    json_out: bool = False,
) -> dict[str, Any]:
    """Fetch a W&B run's metrics and optionally attach to an experiment note.

    Args:
      run_id        — W&B run id (bare-id, project/run-id, or entity/project/run-id)
      experiment    — experiment note stem (e.g. 'exp-q1') to attach results to
      project_slug  — project slug; required when experiment is set
      dataset_id    — SR-8 dataset note stem to link (fills repro_dataset_* fields)
      config        — resolved Config (or None to auto-load)
      json_out      — unused here; callers can choose output format

    Returns a dict with run state + optionally results_location/results_hash/
    results_wandb_run/results_commit (when experiment is provided).

    Raises KeyError if WANDB_API_KEY is not set.
    Raises ImportError if the wandb SDK is not installed.
    Raises ValueError on bad run-id grammar or run-not-found.
    """
    cfg = config or load_config()
    store = EnvSecretStore()
    api_key = store.get("wandb-api-key")

    # Resolve entity/project from env (primary) → manifest (fallback, SR-CO).
    # parse_run_id applies env-over-param naturally via os.environ.get; we only
    # inject manifest values when the env var is NOT set (env wins when set).
    manifest_entity, manifest_project = _resolve_wandb_from_manifest(cfg)
    # Use manifest value only when env var is absent (env-over-config rule).
    fallback_entity = manifest_entity if not os.environ.get("WANDB_ENTITY", "").strip() else None
    fallback_project = manifest_project if not os.environ.get("WANDB_PROJECT", "").strip() else None

    entity, project, run_name = parse_run_id(
        run_id,
        entity=fallback_entity,
        project=fallback_project,
    )
    run_data = fetch_run(entity, project, run_name, api_key)

    result: dict[str, Any] = {
        "state": run_data["state"],
        "displayName": run_data["displayName"],
        "commit": run_data["commit"],
        "summaryMetrics": run_data["summaryMetrics"],
        "results_location": None,
        "results_hash": None,
        "results_wandb_run": None,
        "results_commit": None,
    }

    if experiment:
        if not project_slug:
            raise ValueError(
                "project_slug is required when experiment is set — "
                "the results artifact is project-scoped (D-WB-3)."
            )
        # D-WB-3: project-scoped path next to the experiment note
        exp_dir = cfg.project_notes_dir(project_slug) / "experiments"
        exp_dir.mkdir(parents=True, exist_ok=True)
        results_path = exp_dir / f"{experiment}.results.json"

        # Write the metrics artifact (sorted keys for deterministic hash)
        metrics_json = json.dumps(run_data["summaryMetrics"], indent=2, sort_keys=True)
        results_path.write_text(metrics_json, encoding="utf-8")

        # Compute content hash (streaming, same hasher as SR-8)
        results_hash = _hash_file(results_path)

        # SR-EXP-REPRO: Layer-1 — dump full dict(run.config) to <exp>.config.json + hash it
        run_config: dict[str, Any] = run_data.get("config", {})
        config_path = exp_dir / f"{experiment}.config.json"
        config_json = json.dumps(run_config, indent=2, sort_keys=True)
        config_path.write_text(config_json, encoding="utf-8")
        config_hash = _hash_file(config_path)

        # Assemble all repro_* updates (sentinel is the safe default — anti-fabrication)
        repro_updates: dict[str, str] = {
            "repro_config_location": str(config_path),
            "repro_config_hash": config_hash,
        }

        # SR-EXP-REPRO: Layer-2 auto scalars — apply alias table to run.config
        for repro_field, candidate_keys in _REPRO_CONFIG_ALIAS_TABLE:
            for key in candidate_keys:
                if key in run_config:
                    repro_updates[repro_field] = str(run_config[key])
                    break

        # SR-EXP-REPRO: Layer-2 auto scalars — apply metadata map to run.metadata
        run_metadata: dict[str, Any] = run_data.get("metadata", {})
        for repro_field, candidate_keys in _REPRO_META_MAP:
            for key in candidate_keys:
                if key in run_metadata:
                    val = run_metadata[key]
                    if isinstance(val, list):
                        val = "; ".join(str(v) for v in val)
                    repro_updates[repro_field] = str(val)
                    break

        # SR-EXP-REPRO: repro_hw — defer to SR-6 manifest (never re-probe)
        repro_updates["repro_hw"] = _resolve_repro_hw(cfg)

        # SR-EXP-REPRO: repro_dataset_* — link SR-8 dataset note (never re-enter)
        if dataset_id:
            ds_id, ds_hash = _resolve_repro_dataset(cfg, dataset_id)
            repro_updates["repro_dataset_id"] = ds_id
            repro_updates["repro_dataset_hash"] = ds_hash

        # Fill the experiment note's results_* + repro_* frontmatter fields
        exp_note = exp_dir / f"{experiment}.md"
        if exp_note.exists():
            _update_frontmatter(exp_note, {
                "results_location": str(results_path),
                "results_hash": results_hash,
                "results_wandb_run": run_id,
                "results_commit": run_data["commit"],
                **repro_updates,
            })

        result.update({
            "results_location": str(results_path),
            "results_hash": results_hash,
            "results_wandb_run": run_id,
            "results_commit": run_data["commit"],
        })

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``wandb`` verb.

    When to use: ``rv wandb pull <run-id>`` when an experiment logged to W&B and
    you need its final metrics or want to wait until the run finishes. Use
    ``--experiment`` to attach metrics (hash-verified) to the experiment note.
    Anti-pattern: do NOT pip install wandb and script the SDK directly —
    and do NOT hand-copy metrics into a finding — use ``rv wandb pull <run-id>
    --experiment <id> --project <slug>`` to fetch via the SDK and attach
    results→hash→run provenance to the experiment note.
    """
    desc = (
        "Fetch W&B run metrics via the wandb SDK and optionally attach them, "
        "hash-verified, to an experiment note. "
        "Auth via WANDB_API_KEY env var or keyring (run `rv check` to verify). "
        "W&B SDK must be installed: pip install wandb."
    )
    if parent is not None:
        p = parent.add_parser(
            "wandb",
            help="Fetch W&B run metrics and attach to an experiment note.",
            description=desc,
        )
    else:
        p = argparse.ArgumentParser(prog="rv wandb", description=desc)

    sub = p.add_subparsers(dest="wandb_cmd", required=True)

    pull_p = sub.add_parser(
        "pull",
        help="Fetch a W&B run's metrics. Use --experiment to attach to a note.",
    )
    pull_p.add_argument(
        "run_id",
        help=(
            "W&B run id. Forms: 'bare-id' (needs WANDB_ENTITY + WANDB_PROJECT), "
            "'project/run-id' (needs WANDB_ENTITY), or 'entity/project/run-id'."
        ),
    )
    pull_p.add_argument(
        "--experiment",
        default=None,
        metavar="EXP_ID",
        help=(
            "Experiment note stem (e.g. 'exp-q1') to attach results to. "
            "Writes experiments/<EXP_ID>.results.json and fills results_* frontmatter."
        ),
    )
    pull_p.add_argument(
        "--project",
        default=None,
        metavar="SLUG",
        help="Project slug (required when --experiment is set).",
    )
    pull_p.add_argument(
        "--dataset",
        default=None,
        metavar="DATASET_ID",
        help=(
            "SR-8 dataset note stem (e.g. 'xnli-en') to link as repro_dataset_*. "
            "Inherits the dataset note's hash — never re-enters data. (SR-EXP-REPRO)"
        ),
    )
    pull_p.add_argument(
        "--json",
        dest="json_out",
        action="store_true",
        help="Output results as JSON instead of human-readable text.",
    )

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch wandb subcommands. Returns exit code."""
    if args.wandb_cmd == "pull":
        try:
            cfg = load_config()
        except Exception as e:
            print(f"rv wandb: config error: {e}", file=sys.stderr)
            return 1

        try:
            result = wandb_pull(
                args.run_id,
                experiment=args.experiment,
                project_slug=args.project,
                dataset_id=getattr(args, "dataset", None),
                config=cfg,
                json_out=args.json_out,
            )
        except ImportError as e:
            print(f"rv wandb: W&B SDK not installed.\n  {e}", file=sys.stderr)
            return 1
        except KeyError as e:
            print(f"rv wandb: API key error: {e}", file=sys.stderr)
            return 1
        except ValueError as e:
            print(f"rv wandb: {e}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"rv wandb: unexpected error: {e}", file=sys.stderr)
            return 1

        if args.json_out:
            print(json.dumps(result, indent=2))
        else:
            print(f"Run: {args.run_id}")
            print(f"State: {result['state']}")
            if result.get("displayName"):
                print(f"Name:  {result['displayName']}")
            if result.get("commit"):
                print(f"Commit: {result['commit']}")
            print()
            metrics = result.get("summaryMetrics") or {}
            if metrics:
                print("Summary metrics:")
                for k, v in sorted(metrics.items()):
                    print(f"  {k}: {v}")
            if result.get("results_location"):
                print()
                print(f"Results artifact: {result['results_location']}")
                print(f"Results hash:     {result['results_hash']}")
                print(f"W&B run:          {result['results_wandb_run']}")
        return 0

    print(f"rv wandb: unknown subcommand {args.wandb_cmd!r}", file=sys.stderr)
    return 1
