"""doctor.py — `rv doctor` — capability probe: DECLARE → DISCOVER.

When to use: ``rv doctor`` to probe and cache which capabilities are available
in this environment — iterating each DECLARED backend from the compute manifest.
Run once after ``rv compute init`` or after environment changes. Agents query
the cache; re-run with --refresh on env-change or failure.

DECLARE → DISCOVER ordering (SR-CO): run ``rv compute init`` FIRST (declare
WHERE your compute is — local + optional remote cluster), THEN run ``rv doctor``
(discover WHAT is available, per declared backend). Declaration tells doctor
where to look; doctor cannot see a cluster you haven't declared.

Anti-pattern: do NOT re-probe the cluster by trial-submit to learn what
env/tier to use — ``rv compute show`` / ``rv doctor`` already declare it.

The doctor cache is stored at ``<state_dir>/doctor_cache.json`` in the
per-backend shape: ``{backend_name: {ts, capabilities}}``. Back-compat:
flat legacy cache shape (written before SR-CO) is readable as local caps.
NEVER written to ~/vault — only the instance state_dir.

Graceful degradation: rv doctor NEVER raises a traceback when cluster CLIs
(sinfo, sbatch, qsub, qstat, hf) are absent. It reports "not available" for
each missing tool and exits 0. A keyless, cluster-less adopter on backend=local
gets a fully working doctor that reports "slurm: not available".

Backend archetype probes (per declared backend):
  local       — always available; nvidia-smi for GPU detection (today's full probe)
  ssh+slurm   — DECLARED; remote probe = SR-CO-REMOTE (honest deferral)
  ssh+pbs     — DECLARED; remote probe = SR-CO-REMOTE (honest deferral)
  ssh         — DECLARED; remote probe = SR-CO-REMOTE (honest deferral)
  generic     — runs declared probe_commands from the manifest profile (local only)

Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import Config, load_config
from .compute import _load_manifest

# ---------------------------------------------------------------------------
# Cache constants
# ---------------------------------------------------------------------------

_CACHE_FILE = "doctor_cache.json"


# ---------------------------------------------------------------------------
# Individual probes
# ---------------------------------------------------------------------------

def _probe_cli(name: str) -> bool:
    """Return True if a CLI tool is on PATH."""
    return shutil.which(name) is not None


def _probe_conda_envs() -> list[str]:
    """Return a list of conda env names visible in this environment.

    Gracefully returns [] if conda is not present or the command fails.
    """
    conda_exe = shutil.which("conda")
    if not conda_exe:
        return []
    try:
        result = subprocess.run(
            [conda_exe, "env", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        envs = data.get("envs", [])
        return [Path(e).name for e in envs]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return []


def _probe_sinfo() -> dict[str, Any]:
    """Return sinfo partition + GPU info.

    Returns {"available": False, "reason": "..."} if sinfo is absent or fails.
    Returns {"available": True, "partitions": [...]} on success.
    """
    if not _probe_cli("sinfo"):
        return {"available": False, "reason": "sinfo not found in PATH"}
    try:
        result = subprocess.run(
            ["sinfo", "--format=%P %G %l %D", "--noheader"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return {"available": False, "reason": f"sinfo exited {result.returncode}"}
        lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
        partitions = []
        for ln in lines:
            parts = ln.split()
            partitions.append({"raw": ln, "partition": parts[0] if parts else "?"})
        return {"available": True, "partitions": partitions}
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"available": False, "reason": str(exc)}


def _probe_qstat() -> dict[str, Any]:
    """Return qstat / PBS presence info.

    Returns {"available": False, "reason": "..."} if absent.
    Returns {"available": True} on success.
    """
    if not _probe_cli("qstat"):
        return {"available": False, "reason": "qstat not found in PATH"}
    try:
        result = subprocess.run(
            ["qstat", "-Q"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return {"available": True}
        return {"available": False, "reason": f"qstat -Q exited {result.returncode}"}
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"available": False, "reason": str(exc)}


def _probe_nvidia_smi() -> dict[str, Any]:
    """Return GPU presence info via nvidia-smi.

    Returns {"available": False} if nvidia-smi absent, else GPU count.
    """
    if not _probe_cli("nvidia-smi"):
        return {"available": False, "reason": "nvidia-smi not found"}
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return {"available": False, "reason": f"nvidia-smi exited {result.returncode}"}
        gpus = [g.strip() for g in result.stdout.splitlines() if g.strip()]
        return {"available": True, "count": len(gpus), "names": gpus}
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"available": False, "reason": str(exc)}


def _probe_generic(probe_commands: list[str]) -> list[dict[str, Any]]:
    """Run each declared probe_command and return results.

    Each result: {"cmd": str, "ok": bool, "exit_code": int}.
    Never raises — failures are captured as ok=False.
    """
    results: list[dict[str, Any]] = []
    for cmd_str in probe_commands:
        try:
            tokens = shlex.split(cmd_str)
            result = subprocess.run(
                tokens,
                capture_output=True,
                text=True,
                timeout=15,
            )
            results.append({"cmd": cmd_str, "ok": result.returncode == 0,
                            "exit_code": result.returncode})
        except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as exc:
            results.append({"cmd": cmd_str, "ok": False, "error": str(exc)})
    return results


# ---------------------------------------------------------------------------
# Per-backend probers
# ---------------------------------------------------------------------------

# Archetypes that are remote (require an ssh connection to probe meaningfully).
# The actual remote probe ships in SR-CO-REMOTE — here we report honestly.
_REMOTE_ARCHETYPES = frozenset({"ssh", "ssh+slurm", "ssh+pbs"})


def _probe_local_backend(cfg: Config) -> dict[str, Any]:
    """Run all local capability probes and return the raw capability dict.

    This is the full SR-6 local probe — unchanged from before SR-CO.
    Never raises — each probe degrades gracefully on failure.
    """
    caps: dict[str, Any] = {}

    # --- local: always available ---
    caps["local_available"] = True

    # --- nvidia-smi ---
    caps["nvidia_smi"] = _probe_nvidia_smi()

    # --- CLI tools ---
    for cli in ("sbatch", "sinfo", "qsub", "qstat", "hf", "uv", "conda"):
        caps[cli] = _probe_cli(cli)

    # --- conda envs ---
    caps["conda_envs"] = _probe_conda_envs()

    # --- SLURM detail (if sinfo present) ---
    if caps.get("sinfo"):
        caps["sinfo_detail"] = _probe_sinfo()
    else:
        caps["sinfo_detail"] = {"available": False, "reason": "sinfo not found in PATH"}

    # --- PBS/Torque detail (if qstat present) ---
    if caps.get("qstat"):
        caps["qstat_detail"] = _probe_qstat()
    else:
        caps["qstat_detail"] = {"available": False, "reason": "qstat not found in PATH"}

    # --- Generic backend probes (from manifest) ---
    manifest = _load_manifest(cfg)
    backends = manifest.get("backends", {})
    profiles = backends.get("profiles", {})
    generic_results: list[dict[str, Any]] = []
    for profile_name, prof in profiles.items():
        if prof.get("archetype") == "generic":
            probe_cmds = prof.get("probe_commands", [])
            if probe_cmds:
                probe_results = _probe_generic(probe_cmds)
                generic_results.append({
                    "profile": profile_name,
                    "probes": probe_results,
                })
    caps["generic_probes"] = generic_results

    return caps


def _probe_remote_backend_deferred(
    backend_name: str,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Return the honest "declared but not yet probed" caps for a remote backend.

    SR-CO ships the seam; the actual ssh probe (BatchMode + ConnectTimeout +
    scheduler-aware GPU discovery) ships in SR-CO-REMOTE.

    Charter §2 — never silently skip a declared remote backend. This function
    surfaces it explicitly so the user knows their cluster is declared but the
    remote probe has not yet run.
    """
    host = profile.get("host", "")
    archetype = profile.get("archetype", "?")
    host_display = host if host and not host.startswith("FILL") else "(not yet filled)"
    return {
        "declared": True,
        "archetype": archetype,
        "host": host_display,
        "probe_status": "deferred",
        "message": (
            f"cluster '{backend_name}' declared (host={host_display}, "
            f"archetype={archetype}); remote probe not yet implemented "
            "(SR-CO-REMOTE) — declare partitions/tiers by hand for now"
        ),
    }


# ---------------------------------------------------------------------------
# Main probe runner — env-aware (iterates declared backends)
# ---------------------------------------------------------------------------

def _probe_capabilities(cfg: Config) -> dict[str, dict[str, Any]]:
    """Probe each DECLARED backend and return per-backend capability dicts.

    Returns a mapping: ``{backend_name: caps_dict}``.

    The ``local`` backend (archetype="local") receives the full SR-6 local
    probe. Remote backends (ssh/ssh+slurm/ssh+pbs) are recognised and honestly
    reported as deferred until SR-CO-REMOTE ships the actual probe.

    Never raises — each backend degrades gracefully on failure.
    """
    manifest = _load_manifest(cfg)
    backends = manifest.get("backends", {})
    profiles = backends.get("profiles", {})

    result: dict[str, dict[str, Any]] = {}

    if not profiles:
        # No declared backends — fall back to local probe so doctor is always useful
        result["local"] = _probe_local_backend(cfg)
        return result

    for backend_name, prof in profiles.items():
        archetype = prof.get("archetype", "local")
        if archetype == "local":
            result[backend_name] = _probe_local_backend(cfg)
        elif archetype in _REMOTE_ARCHETYPES:
            result[backend_name] = _probe_remote_backend_deferred(backend_name, prof)
        elif archetype == "generic":
            # Generic: run declared probe_commands locally
            probe_cmds = prof.get("probe_commands", [])
            generic_caps: dict[str, Any] = {"archetype": "generic"}
            if probe_cmds:
                generic_caps["generic_probes"] = [
                    {"profile": backend_name, "probes": _probe_generic(probe_cmds)}
                ]
            result[backend_name] = generic_caps
        else:
            # Unknown archetype — report honestly
            result[backend_name] = {
                "archetype": archetype,
                "probe_status": "unknown-archetype",
                "message": f"archetype {archetype!r} has no probe handler",
            }

    # Ensure "local" is always present (even if not declared) so the cache
    # is always useful for local-only adopters who haven't run compute init yet.
    if "local" not in result:
        result["local"] = _probe_local_backend(cfg)

    return result


# ---------------------------------------------------------------------------
# Cache I/O — per-backend shape (SR-CO)
# ---------------------------------------------------------------------------
# Cache format (SR-CO+):
#   {
#     "ts": "<ISO8601 of last write>",
#     "backends": {
#       "local":   {"ts": "<probe ts>", "capabilities": { ... }},
#       "cluster": {"ts": "<probe ts>", "capabilities": { ... }},
#     }
#   }
#
# Back-compat with flat pre-SR-CO cache (shape: {"ts", "capabilities": {...}}):
# _read_cache normalises the flat shape into the per-backend form under "local".

def _cache_path(cfg: Config) -> Path:
    return cfg.state_dir / _CACHE_FILE


def _read_cache(cfg: Config) -> dict[str, Any] | None:
    """Read and return the doctor cache (per-backend shape), or None if absent/invalid.

    Back-compat: a flat legacy cache written before SR-CO is normalised into
    the per-backend shape under the 'local' key so callers see a uniform interface.
    """
    p = _cache_path(cfg)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    # Detect flat (pre-SR-CO) shape: has "capabilities" at top level
    if "capabilities" in raw and "backends" not in raw:
        # Normalise to per-backend form
        ts = raw.get("ts", "")
        flat_caps = raw.get("capabilities", {})
        return {
            "ts": ts,
            "backends": {
                "local": {"ts": ts, "capabilities": flat_caps},
            },
            "_legacy": True,
        }

    return raw


def _write_cache(cfg: Config, per_backend: dict[str, dict[str, Any]]) -> None:
    """Write per-backend capabilities to the doctor cache file.

    ``per_backend`` maps backend_name → capabilities dict (as returned by
    ``_probe_capabilities``). The written format is the SR-CO per-backend shape.
    """
    p = _cache_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
    backends_entry: dict[str, Any] = {}
    for name, caps in per_backend.items():
        backends_entry[name] = {"ts": ts, "capabilities": caps}
    entry = {"ts": ts, "backends": backends_entry}
    p.write_text(json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def cmd_doctor(cfg: Config, *, refresh: bool = False) -> dict[str, Any]:
    """Run or read the doctor cache.

    If refresh=False (default): read the cache if present; probe only if
    the cache is absent.

    If refresh=True: always re-probe and overwrite the cache.

    Returns a dict with keys:
      ts            — ISO8601 timestamp of the most recent write
      backends      — mapping of backend_name → {ts, capabilities}
      from_cache    — True if the result came from cache, False if freshly probed

    Back-compat: a flat legacy cache is normalised to per-backend shape
    under the 'local' key (_legacy=True flag is set for callers that care).
    """
    if not refresh:
        cached = _read_cache(cfg)
        if cached is not None:
            cached["from_cache"] = True
            return cached

    per_backend = _probe_capabilities(cfg)
    _write_cache(cfg, per_backend)

    # Read back what we wrote to get the ts
    written = _read_cache(cfg)
    if written is None:
        # Fallback — shouldn't happen
        ts = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
        written = {"ts": ts, "backends": {
            k: {"ts": ts, "capabilities": v} for k, v in per_backend.items()
        }}
    written["from_cache"] = False
    return written


# ---------------------------------------------------------------------------
# Report formatter — per-backend
# ---------------------------------------------------------------------------

def _format_local_caps(caps: dict[str, Any]) -> list[str]:
    """Format the local backend capabilities into report lines."""
    lines: list[str] = []

    lines.append("  Backend: local — always available")

    # --- GPU ---
    nv = caps.get("nvidia_smi", {})
    if nv.get("available"):
        count = nv.get("count", 0)
        names = ", ".join(nv.get("names", [])[:3])
        lines.append(f"  GPU (nvidia-smi): {count} device(s) — {names}")
    else:
        reason = nv.get("reason", "not found")
        lines.append(f"  GPU (nvidia-smi): not available — {reason}")

    # --- SLURM ---
    sbatch_ok = caps.get("sbatch", False)
    sinfo_detail = caps.get("sinfo_detail", {})
    if sbatch_ok and sinfo_detail.get("available"):
        n_parts = len(sinfo_detail.get("partitions", []))
        lines.append(f"  SLURM (sbatch/sinfo): available — {n_parts} partition(s)")
    else:
        reason = sinfo_detail.get("reason", "sbatch/sinfo not found")
        lines.append(f"  SLURM (sbatch/sinfo): not available — {reason}")

    # --- PBS/Torque ---
    qstat_detail = caps.get("qstat_detail", {})
    if qstat_detail.get("available"):
        lines.append("  PBS/Torque (qstat): available")
    else:
        reason = qstat_detail.get("reason", "qstat not found")
        lines.append(f"  PBS/Torque (qstat): not available — {reason}")

    # --- hf / uv ---
    hf_ok = caps.get("hf", False)
    uv_ok = caps.get("uv", False)
    lines.append(f"  hf CLI: {'found' if hf_ok else 'not found'}")
    lines.append(f"  uv CLI: {'found' if uv_ok else 'not found'}")

    # --- conda envs ---
    conda_envs = caps.get("conda_envs", [])
    if conda_envs:
        lines.append(f"  conda envs ({len(conda_envs)}): {', '.join(conda_envs[:8])}")
    else:
        lines.append("  conda envs: none found (or conda not installed)")

    # --- Generic backend probes ---
    generic = caps.get("generic_probes", [])
    if generic:
        lines.append("  Generic probes:")
        for gp in generic:
            profile = gp.get("profile", "?")
            probes = gp.get("probes", [])
            for pr in probes:
                status = "OK" if pr.get("ok") else "FAIL"
                lines.append(f"    [{status}] {profile}: {pr.get('cmd', '?')}")

    return lines


def format_report(result: dict[str, Any]) -> str:
    """Format the doctor result as a human-readable string.

    Handles the SR-CO per-backend shape (``backends`` key).
    Also handles the legacy flat shape (normalised to per-backend by _read_cache).
    """
    lines: list[str] = ["=== rv doctor — capability report ===", ""]

    from_cache = result.get("from_cache", False)
    ts = result.get("ts", "?")
    source = "from cache" if from_cache else "freshly probed"
    lines.append(f"  Timestamp: {ts}  ({source})")
    if result.get("_legacy"):
        lines.append("  (legacy cache format — run `rv doctor --refresh` to update)")
    lines.append("")

    backends = result.get("backends", {})

    if not backends:
        # Truly empty — should not happen in practice
        lines.append("  No backends found in cache.")
        lines.append("")
        return "\n".join(lines)

    for backend_name, backend_entry in backends.items():
        caps = backend_entry.get("capabilities", backend_entry)  # tolerate flat shape

        lines.append(f"[{backend_name}]")

        # --- Remote deferred backend ---
        if caps.get("probe_status") == "deferred":
            msg = caps.get("message", f"cluster '{backend_name}' declared; probe deferred")
            lines.append(f"  {msg}")
        elif caps.get("probe_status") == "unknown-archetype":
            msg = caps.get("message", f"unknown archetype for '{backend_name}'")
            lines.append(f"  {msg}")
        else:
            # Local (or generic) — full detail
            lines.extend(_format_local_caps(caps))

        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI verb
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``doctor`` verb."""
    desc = (
        "Probe and cache compute environment capabilities — per declared backend. "
        "Run AFTER `rv compute init` (which declares WHERE your compute is). "
        "Local backend: full probe (conda envs, GPU, SLURM/PBS CLIs, hf/uv). "
        "Remote backends: honestly reported as declared-but-not-yet-probed "
        "(SR-CO-REMOTE ships the actual ssh probe). "
        "Second call reads cache (no re-probe). Use --refresh to force a fresh probe. "
        "Degrades gracefully when cluster tools are absent — reports 'not available', "
        "never a traceback."
    )
    if parent is not None:
        p = parent.add_parser(
            "doctor",
            help="Probe + cache compute capabilities (conda, SLURM, PBS, GPU).",
            description=desc,
        )
    else:
        p = argparse.ArgumentParser(prog="rv doctor", description=desc)

    p.add_argument(
        "--refresh",
        action="store_true",
        default=False,
        help="Force a fresh probe even if the cache is present.",
    )

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch: rv doctor [--refresh]."""
    cfg: Config = getattr(args, "_cfg", None) or load_config()

    try:
        result = cmd_doctor(cfg, refresh=getattr(args, "refresh", False))
    except Exception as exc:  # pragma: no cover — safety net only
        print(f"rv doctor: unexpected error (this is a bug): {exc}", file=sys.stderr)
        return 1

    report = format_report(result)
    print(report)
    return 0
