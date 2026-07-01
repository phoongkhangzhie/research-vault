"""doctor.py — `rv doctor` — capability probe: DISCOVER-ONCE + CACHE.

When to use: ``rv doctor`` to probe and cache which capabilities are available
in this environment (conda envs, cluster CLIs, GPU presence, egress). Run once
after setup or after environment changes. Agents query the cache; re-run on
failure or env-change. Use ``rv doctor --refresh`` to force a fresh probe.

Anti-pattern: do NOT re-probe the cluster by trial-submit to learn what
env/tier to use — ``rv compute show`` / ``rv doctor`` already declare it.

The doctor cache is stored at ``<state_dir>/doctor_cache.json``. It is NEVER
written to ~/vault — only the instance state_dir.

Graceful degradation: rv doctor NEVER raises a traceback when cluster CLIs
(sinfo, sbatch, qsub, qstat, hf) are absent. It reports "not available" for
each missing tool and exits 0. A keyless, cluster-less adopter on backend=local
gets a fully working doctor that reports "slurm: not available".

Backend archetype probes:
  local       — always available; nvidia-smi for GPU detection
  ssh         — (basic: ssh reachability not probed here; use ssh-health check)
  ssh+slurm   — sinfo partitions + GPU types, sbatch present
  ssh+pbs     — qstat/pbsnodes present, queues
  generic     — runs declared probe_commands from the manifest profile

Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime
import json
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
            tokens = cmd_str.split()
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
# Main probe runner
# ---------------------------------------------------------------------------

def _probe_capabilities(cfg: Config) -> dict[str, Any]:
    """Run all capability probes and return the raw capability dict.

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


# ---------------------------------------------------------------------------
# Cache I/O
# ---------------------------------------------------------------------------

def _cache_path(cfg: Config) -> Path:
    return cfg.state_dir / _CACHE_FILE


def _read_cache(cfg: Config) -> dict[str, Any] | None:
    """Read and return the doctor cache, or None if absent/invalid."""
    p = _cache_path(cfg)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_cache(cfg: Config, caps: dict[str, Any]) -> None:
    """Write capabilities to the doctor cache file."""
    p = _cache_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
    entry = {"ts": ts, "capabilities": caps}
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
      ts            — ISO8601 timestamp of the probe
      capabilities  — the raw capability dict
      from_cache    — True if the result came from cache, False if freshly probed
    """
    if not refresh:
        cached = _read_cache(cfg)
        if cached is not None:
            cached["from_cache"] = True
            return cached

    caps = _probe_capabilities(cfg)
    _write_cache(cfg, caps)

    # Read back what we wrote to get the ts
    written = _read_cache(cfg)
    if written is None:
        # Fallback — shouldn't happen
        ts = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
        written = {"ts": ts, "capabilities": caps}
    written["from_cache"] = False
    return written


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------

def format_report(result: dict[str, Any]) -> str:
    """Format the doctor result as a human-readable string."""
    lines: list[str] = ["=== rv doctor — capability report ===", ""]

    from_cache = result.get("from_cache", False)
    ts = result.get("ts", "?")
    source = "from cache" if from_cache else "freshly probed"
    lines.append(f"  Timestamp: {ts}  ({source})")
    lines.append("")

    caps = result.get("capabilities", {})

    # --- local ---
    lines.append("Local backend:  always available")

    # --- GPU ---
    nv = caps.get("nvidia_smi", {})
    if nv.get("available"):
        count = nv.get("count", 0)
        names = ", ".join(nv.get("names", [])[:3])
        lines.append(f"GPU (nvidia-smi): {count} device(s) — {names}")
    else:
        reason = nv.get("reason", "not found")
        lines.append(f"GPU (nvidia-smi): not available — {reason}")

    # --- SLURM ---
    sbatch_ok = caps.get("sbatch", False)
    sinfo_detail = caps.get("sinfo_detail", {})
    if sbatch_ok and sinfo_detail.get("available"):
        n_parts = len(sinfo_detail.get("partitions", []))
        lines.append(f"SLURM (sbatch/sinfo): available — {n_parts} partition(s)")
    else:
        reason = sinfo_detail.get("reason", "sbatch/sinfo not found")
        lines.append(f"SLURM (sbatch/sinfo): not available — {reason}")

    # --- PBS/Torque ---
    qstat_detail = caps.get("qstat_detail", {})
    if qstat_detail.get("available"):
        lines.append("PBS/Torque (qstat): available")
    else:
        reason = qstat_detail.get("reason", "qstat not found")
        lines.append(f"PBS/Torque (qstat): not available — {reason}")

    # --- hf / uv ---
    hf_ok = caps.get("hf", False)
    uv_ok = caps.get("uv", False)
    lines.append(f"hf CLI: {'found' if hf_ok else 'not found'}")
    lines.append(f"uv CLI: {'found' if uv_ok else 'not found'}")

    # --- conda envs ---
    conda_envs = caps.get("conda_envs", [])
    if conda_envs:
        lines.append(f"conda envs ({len(conda_envs)}): {', '.join(conda_envs[:8])}")
    else:
        lines.append("conda envs: none found (or conda not installed)")

    # --- Generic backend probes ---
    generic = caps.get("generic_probes", [])
    if generic:
        lines.append("")
        lines.append("Generic backend probes:")
        for gp in generic:
            profile = gp.get("profile", "?")
            probes = gp.get("probes", [])
            for pr in probes:
                status = "OK" if pr.get("ok") else "FAIL"
                lines.append(f"  [{status}] {profile}: {pr.get('cmd', '?')}")

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
        "Probe and cache compute environment capabilities: "
        "conda envs, SLURM/PBS schedulers, CLI tools (hf/uv/sbatch/qsub), GPU presence. "
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
