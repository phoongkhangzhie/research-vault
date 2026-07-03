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
  ssh+slurm   — ssh probe via sinfo (scheduler-aware GPU discovery, BatchMode fail-fast)
  ssh+pbs     — ssh probe via pbsnodes (BatchMode fail-fast)
  ssh         — ssh probe (connectivity + uptime check, BatchMode fail-fast)
  generic     — runs declared probe_commands from the manifest profile (local only)

Remote probe design (SR-CO-REMOTE):
  GPU discovery uses the SCHEDULER (sinfo/pbsnodes), NOT login-node nvidia-smi.
  Login nodes on HPC clusters typically have no GPU — nvidia-smi would false-negative.
  BatchMode=yes + ConnectTimeout=N on every probe call ensures the probe never hangs
  on an auth prompt or unreachable host.

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
# Remote probe helpers (SR-CO-REMOTE)
# ---------------------------------------------------------------------------

# SSH options that make the probe fail-fast rather than hang:
#   BatchMode=yes   — never prompt for password/passphrase (returns exit 255)
#   ConnectTimeout  — bail out quickly on unreachable hosts
_SSH_PROBE_OPTS: list[str] = [
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=no",
]

# Archetypes that are remote (require an ssh connection to probe meaningfully).
_REMOTE_ARCHETYPES = frozenset({"ssh", "ssh+slurm", "ssh+pbs"})


def _parse_sinfo_output(stdout: str) -> dict[str, Any]:
    """Parse ``sinfo --format='%P %G %D' --noheader`` stdout.

    Returns::

        {
            "available": True,
            "partitions": [
                {"partition": "gpu*", "gpu_gres": "gpu:a100:4", "nodes": "8"},
                ...
            ],
            "gpu_types": ["a100", "v100"],   # deduplicated; empty if no GPU partitions
        }

    GPU type extraction: ``gpu:a100:4`` → ``"a100"``; ``(null)`` → skipped.
    Never raises — bad lines are silently skipped.
    """
    partitions: list[dict[str, str]] = []
    gpu_types_seen: list[str] = []

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        partition_name = parts[0]
        gpu_gres = parts[1] if len(parts) > 1 else "(null)"
        nodes = parts[2] if len(parts) > 2 else "?"
        partitions.append({
            "partition": partition_name,
            "gpu_gres": gpu_gres,
            "nodes": nodes,
        })
        # Extract GPU type from GRES string like "gpu:a100:4" or "gpu:2"
        if gpu_gres and gpu_gres != "(null)" and gpu_gres.startswith("gpu:"):
            gres_parts = gpu_gres.split(":")
            # gpu:<type>:<count>  → type is index 1 (if it's not a bare count)
            if len(gres_parts) >= 2:
                gpu_type_candidate = gres_parts[1]
                # Bare "gpu:4" has a digit at index 1 — skip (no named type)
                if gpu_type_candidate and not gpu_type_candidate.isdigit():
                    if gpu_type_candidate not in gpu_types_seen:
                        gpu_types_seen.append(gpu_type_candidate)

    return {
        "available": True,
        "partitions": partitions,
        "gpu_types": gpu_types_seen,
    }


def _probe_remote_slurm(host: str, backend_name: str) -> dict[str, Any]:
    """Probe an ssh+slurm backend via ``sinfo``.

    Uses BatchMode=yes + ConnectTimeout so the probe never hangs on an
    unconfigured host or auth prompt.  GPU discovery goes through the
    scheduler (sinfo GRES), NOT login-node nvidia-smi — login nodes on
    HPC clusters are typically GPU-less, so nvidia-smi would false-negative.

    Returns a capabilities dict with one of:
      probe_status="ok"             — reachable, sinfo ran
      probe_status="scheduler_error"— reachable but sinfo returned non-zero
      probe_status="unreachable"    — ssh failed (timeout, auth, not found)
    """
    from .adapters.remote import _ssh_exec

    sinfo_argv = _SSH_PROBE_OPTS + ["sinfo", "--format=%P %G %D", "--noheader"]
    try:
        result = _ssh_exec(host, sinfo_argv, timeout=20)
    except FileNotFoundError:
        return {
            "probe_status": "unreachable",
            "reachable": False,
            "reason": "ssh binary not found — install openssh-client",
            "host": host,
            "archetype": "ssh+slurm",
        }
    except subprocess.TimeoutExpired:
        return {
            "probe_status": "unreachable",
            "reachable": False,
            "reason": (
                f"ssh to '{host}' timed out — "
                "check your ~/.ssh/config / host alias or network connectivity"
            ),
            "host": host,
            "archetype": "ssh+slurm",
        }
    except OSError as exc:
        return {
            "probe_status": "unreachable",
            "reachable": False,
            "reason": str(exc),
            "host": host,
            "archetype": "ssh+slurm",
        }

    # ssh exit 255 = connection refused / auth failure
    if result.returncode == 255:
        stderr_snip = (result.stderr or "")[:200]
        return {
            "probe_status": "unreachable",
            "reachable": False,
            "reason": (
                f"ssh to '{host}' failed (exit 255 — likely auth or connection refused): "
                f"{stderr_snip}"
            ),
            "host": host,
            "archetype": "ssh+slurm",
        }

    if result.returncode != 0:
        stderr_snip = (result.stderr or "")[:200]
        return {
            "probe_status": "scheduler_error",
            "reachable": True,
            "reason": f"sinfo returned exit {result.returncode}: {stderr_snip}",
            "host": host,
            "archetype": "ssh+slurm",
            "partitions": [],
            "gpu_types": [],
        }

    parsed = _parse_sinfo_output(result.stdout or "")
    return {
        "probe_status": "ok",
        "reachable": True,
        "host": host,
        "archetype": "ssh+slurm",
        "partitions": parsed["partitions"],
        "gpu_types": parsed["gpu_types"],
    }


def _probe_remote_pbs(host: str, backend_name: str) -> dict[str, Any]:
    """Probe an ssh+pbs backend via ``pbsnodes -a``.

    Uses BatchMode=yes + ConnectTimeout so the probe never hangs.
    Returns a capabilities dict with probe_status in
    ("ok", "scheduler_error", "unreachable").
    """
    from .adapters.remote import _ssh_exec

    pbs_argv = _SSH_PROBE_OPTS + ["pbsnodes", "-a"]
    try:
        result = _ssh_exec(host, pbs_argv, timeout=20)
    except FileNotFoundError:
        return {
            "probe_status": "unreachable",
            "reachable": False,
            "reason": "ssh binary not found — install openssh-client",
            "host": host,
            "archetype": "ssh+pbs",
        }
    except subprocess.TimeoutExpired:
        return {
            "probe_status": "unreachable",
            "reachable": False,
            "reason": (
                f"ssh to '{host}' timed out — "
                "check your ~/.ssh/config / host alias or network connectivity"
            ),
            "host": host,
            "archetype": "ssh+pbs",
        }
    except OSError as exc:
        return {
            "probe_status": "unreachable",
            "reachable": False,
            "reason": str(exc),
            "host": host,
            "archetype": "ssh+pbs",
        }

    if result.returncode == 255:
        stderr_snip = (result.stderr or "")[:200]
        return {
            "probe_status": "unreachable",
            "reachable": False,
            "reason": (
                f"ssh to '{host}' failed (exit 255 — likely auth or connection refused): "
                f"{stderr_snip}"
            ),
            "host": host,
            "archetype": "ssh+pbs",
        }

    if result.returncode != 0:
        stderr_snip = (result.stderr or "")[:200]
        return {
            "probe_status": "scheduler_error",
            "reachable": True,
            "reason": f"pbsnodes returned exit {result.returncode}: {stderr_snip}",
            "host": host,
            "archetype": "ssh+pbs",
            "raw_output": (result.stdout or "")[:500],
        }

    return {
        "probe_status": "ok",
        "reachable": True,
        "host": host,
        "archetype": "ssh+pbs",
        "raw_output": (result.stdout or "")[:1000],
    }


def _probe_remote_ssh(host: str, backend_name: str) -> dict[str, Any]:
    """Probe a plain ssh backend (connectivity check via 'true').

    Uses BatchMode=yes + ConnectTimeout so the probe never hangs.
    """
    from .adapters.remote import _ssh_exec

    check_argv = _SSH_PROBE_OPTS + ["true"]
    try:
        result = _ssh_exec(host, check_argv, timeout=15)
    except FileNotFoundError:
        return {
            "probe_status": "unreachable",
            "reachable": False,
            "reason": "ssh binary not found — install openssh-client",
            "host": host,
            "archetype": "ssh",
        }
    except subprocess.TimeoutExpired:
        return {
            "probe_status": "unreachable",
            "reachable": False,
            "reason": (
                f"ssh to '{host}' timed out — "
                "check your ~/.ssh/config / host alias or network connectivity"
            ),
            "host": host,
            "archetype": "ssh",
        }
    except OSError as exc:
        return {
            "probe_status": "unreachable",
            "reachable": False,
            "reason": str(exc),
            "host": host,
            "archetype": "ssh",
        }

    if result.returncode != 0:
        stderr_snip = (result.stderr or "")[:200]
        return {
            "probe_status": "unreachable",
            "reachable": False,
            "reason": f"ssh to '{host}' failed (exit {result.returncode}): {stderr_snip}",
            "host": host,
            "archetype": "ssh",
        }

    return {
        "probe_status": "ok",
        "reachable": True,
        "host": host,
        "archetype": "ssh",
    }


# ---------------------------------------------------------------------------
# Per-backend probers
# ---------------------------------------------------------------------------


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


def _probe_remote_backend(
    backend_name: str,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Probe a declared remote backend via ssh.

    Dispatches to the archetype-appropriate probe:
      ssh+slurm  → _probe_remote_slurm (sinfo-based GPU discovery)
      ssh+pbs    → _probe_remote_pbs   (pbsnodes)
      ssh        → _probe_remote_ssh   (connectivity check)

    Every probe uses BatchMode=yes + ConnectTimeout so the automated probe
    NEVER hangs on an auth prompt or unreachable host.

    GPU discovery is SCHEDULER-AWARE (not login-node nvidia-smi) — HPC login
    nodes are typically GPU-less; the scheduler (sinfo GRES) accurately
    reports what compute nodes have.

    Charter §2 — graceful degrade: if the host field is a FILL placeholder,
    return an "unfilled" status so the user knows to fill in the manifest.
    Never crashes; never silently returns an empty dict.
    """
    host = profile.get("host", "")
    archetype = profile.get("archetype", "?")

    # Guard: host is still a FILL placeholder — cannot probe
    if not host or host.startswith("FILL"):
        host_display = "(not yet filled)"
        return {
            "probe_status": "unfilled",
            "reachable": False,
            "declared": True,
            "archetype": archetype,
            "host": host_display,
            "reason": (
                f"cluster '{backend_name}' host is not configured — "
                "fill in the 'host' field in state_dir/compute_manifest.json "
                "(ssh alias or hostname), then run 'rv doctor --refresh'"
            ),
        }

    if archetype == "ssh+slurm":
        return _probe_remote_slurm(host, backend_name)
    elif archetype == "ssh+pbs":
        return _probe_remote_pbs(host, backend_name)
    elif archetype == "ssh":
        return _probe_remote_ssh(host, backend_name)
    else:
        # Fallback — unknown remote archetype
        return {
            "probe_status": "unknown-archetype",
            "reachable": False,
            "archetype": archetype,
            "host": host,
            "reason": f"no probe handler for remote archetype {archetype!r}",
        }


# ---------------------------------------------------------------------------
# Main probe runner — env-aware (iterates declared backends)
# ---------------------------------------------------------------------------

def _probe_capabilities(cfg: Config) -> dict[str, dict[str, Any]]:
    """Probe each DECLARED backend and return per-backend capability dicts.

    Returns a mapping: ``{backend_name: caps_dict}``.

    The ``local`` backend (archetype="local") receives the full SR-6 local
    probe. Remote backends (ssh/ssh+slurm/ssh+pbs) are probed via ssh using
    BatchMode=yes + ConnectTimeout (fail-fast, no hang). GPU discovery is
    scheduler-aware (sinfo GRES), not login-node nvidia-smi.

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
            result[backend_name] = _probe_remote_backend(backend_name, prof)
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

        probe_status = caps.get("probe_status")

        if probe_status == "ok" and caps.get("reachable"):
            # Real remote probe succeeded — show discovered capabilities
            lines.extend(_format_remote_caps(backend_name, caps))
        elif probe_status == "unreachable":
            reason = caps.get("reason", f"cluster '{backend_name}' unreachable")
            archetype = caps.get("archetype", "?")
            host = caps.get("host", "?")
            lines.append(f"  Backend: {archetype} — UNREACHABLE")
            lines.append(f"  Host: {host}")
            lines.append(f"  Reason: {reason}")
            lines.append(
                "  Action: check ~/.ssh/config host alias, network connectivity, "
                "and ssh key auth; then run `rv doctor --refresh`"
            )
        elif probe_status == "unfilled":
            reason = caps.get("reason", f"cluster '{backend_name}' not configured")
            archetype = caps.get("archetype", "?")
            lines.append(f"  Backend: {archetype} — NOT CONFIGURED (host not filled)")
            lines.append(f"  {reason}")
        elif probe_status == "scheduler_error":
            reason = caps.get("reason", "scheduler returned an error")
            archetype = caps.get("archetype", "?")
            host = caps.get("host", "?")
            lines.append(f"  Backend: {archetype} — scheduler error (host reachable)")
            lines.append(f"  Host: {host}")
            lines.append(f"  Reason: {reason}")
        elif probe_status in ("unknown-archetype", None) and caps.get("archetype") in _REMOTE_ARCHETYPES:
            # Legacy deferred or truly unknown archetype for a remote backend
            msg = caps.get("message", f"cluster '{backend_name}': no probe result")
            lines.append(f"  {msg}")
        else:
            # Local (or generic) — full detail
            lines.extend(_format_local_caps(caps))

        lines.append("")

    return "\n".join(lines)


def _format_remote_caps(backend_name: str, caps: dict[str, Any]) -> list[str]:
    """Format real remote probe results into report lines."""
    lines: list[str] = []
    archetype = caps.get("archetype", "?")
    host = caps.get("host", "?")
    lines.append(f"  Backend: {archetype} — reachable")
    lines.append(f"  Host: {host}")

    if archetype == "ssh+slurm":
        partitions = caps.get("partitions", [])
        gpu_types = caps.get("gpu_types", [])
        lines.append(f"  Partitions ({len(partitions)}):")
        for p in partitions[:8]:  # cap display at 8
            name = p.get("partition", "?")
            gres = p.get("gpu_gres", "(null)")
            nodes = p.get("nodes", "?")
            gpu_label = f" [{gres}]" if gres != "(null)" else ""
            lines.append(f"    {name}: {nodes} node(s){gpu_label}")
        if len(partitions) > 8:
            lines.append(f"    ... ({len(partitions) - 8} more partitions)")
        if gpu_types:
            lines.append(f"  GPU types (via sinfo): {', '.join(gpu_types)}")
        else:
            lines.append("  GPU types: none found in sinfo (CPU-only cluster or no GPU GRES)")
    elif archetype == "ssh+pbs":
        lines.append("  PBS/Torque: reachable (pbsnodes successful)")
        raw = caps.get("raw_output", "")
        if raw:
            preview = raw[:200].replace("\n", " ")
            lines.append(f"  pbsnodes preview: {preview}")
    elif archetype == "ssh":
        lines.append("  Plain ssh: connectivity confirmed")
    else:
        # Generic remote archetype — just confirm reachable
        lines.append(f"  Remote probe: reachable")

    return lines


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
        "Remote backends (ssh+slurm/ssh+pbs/ssh): probed via ssh using "
        "BatchMode=yes + ConnectTimeout (fail-fast). GPU discovery uses sinfo "
        "GRES (scheduler-aware, not login-node nvidia-smi). "
        "Second call reads cache (no re-probe). Use --refresh to force a fresh probe. "
        "Degrades gracefully when cluster is unreachable — reports reason, "
        "exits 0, never a traceback."
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
