"""doctor.py — `rv doctor` — principled DISCOVER → PROPOSE → CONFIRM → LEARN.

When to use: ``rv doctor`` to probe and cache which capabilities are available
in this environment — iterating each DECLARED backend from the compute manifest.
Run once after ``rv compute init`` or after environment changes. Agents query
the cache; re-run with --refresh on env-change or failure.

DECLARE → DISCOVER ordering (SR-CO): run ``rv compute init`` FIRST (declare
WHERE your compute is — local + optional remote cluster), THEN run ``rv doctor``
(discover WHAT is available, per declared backend). Declaration tells doctor
where to look; doctor cannot see a cluster you haven't declared.

Four-stage principled model (SR-DOCTOR-PRINCIPLED):
  DISCOVER — inventory (what exists) + permissions (what you're ALLOWED).
             Scheduler-clusters (ssh+slurm/ssh+pbs) get both probes.
             Single-box (local/ssh) get direct GPU probe, no permissions probe.
  PROPOSE  — pure deterministic ``inventory ∩ permissions → tier→partition``.
             Cheapest-that-fits each tier; each row with a rationale string.
             Written to ``doctor_cache.json`` under ``proposed_tiers`` (read-only suggestion).
  CONFIRM  — ``rv doctor --propose`` writes quarantined ``tiers_proposed`` block
             to the compute manifest (NOT the live ``tiers`` — never a silent write).
             ``rv doctor --accept`` promotes ``tiers_proposed`` → live ``tiers``
             only on explicit human action.
  LEARN    — recorded ``run_outcomes`` feed back into the proposal (down-rank
             or annotate pairings contradicted by OOM/FAILED evidence).

Anti-pattern: do NOT re-probe the cluster by trial-submit to learn what
env/tier to use — ``rv compute show`` / ``rv doctor`` already declare it.

Load-bearing principle (state this plainly):
  ``rv doctor`` discovers facts and proposes; the human decides; outcomes teach.
  Doctor separates what EXISTS (inventory) from what you're ALLOWED (permissions),
  derives a SUGGESTED tier→partition mapping, and STOPS — writing a proposed block
  the human reviews and accepts into the manifest. Never silently selects a partition.

The doctor cache is stored at ``<state_dir>/doctor_cache.json`` in the
per-backend shape: ``{backend_name: {ts, capabilities}}``. Back-compat:
flat legacy cache shape (written before SR-CO) is readable as local caps.
NEVER written to ~/vault — only the instance state_dir.

Graceful degradation: rv doctor NEVER raises a traceback when cluster CLIs
(sinfo, sbatch, qsub, qstat, sacctmgr, hf) are absent. It reports "not
available" for each missing tool and exits 0. sacctmgr absent → permissions
not available → falls back to inventory-only proposal with an honest banner.

Backend archetype probes (per declared backend):
  local       — always available; nvidia-smi for GPU detection (SR-6 probe)
  ssh+slurm   — ssh probe via sinfo (scheduler inventory), sacctmgr/scontrol
                (permissions probe, SLURM-first); no login-node nvidia-smi (trap)
  ssh+pbs     — ssh probe via pbsnodes (inventory); PBS permission seam (best-effort)
  ssh         — direct nvidia-smi over ssh (flat topology — correct for this arch);
                NO sacctmgr (no scheduler — permissions = ssh-reachable + GPU-visible)
  generic     — runs declared probe_commands from the manifest profile (local only)

StrictHostKeyChecking=accept-new: accepts new hosts on first connect; REJECTS
if a known host's key changes (the real MITM signal). Safer than ``no`` (which
silently accepts key changes). Still non-interactive — automated probes never hang.

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
from .compute import _load_manifest, _save_manifest

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
#   BatchMode=yes        — never prompt for password/passphrase (returns exit 255)
#   ConnectTimeout       — bail out quickly on unreachable hosts
#   StrictHostKeyChecking=accept-new — accepts new hosts on first connect BUT
#     REJECTS if a known host's key changes (the real MITM signal). Safer
#     than "no" (which silently accepts any key change). Still non-interactive
#     for automated probes — the probe never hangs waiting for user input.
_SSH_PROBE_OPTS: list[str] = [
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=accept-new",
]

# Archetypes that are remote (require an ssh connection to probe meaningfully).
_REMOTE_ARCHETYPES = frozenset({"ssh", "ssh+slurm", "ssh+pbs"})


def _ssh_probe_call(
    host: str,
    argv: list[str],
    archetype: str,
    *,
    timeout: int = 20,
) -> "subprocess.CompletedProcess[str] | dict[str, Any]":
    """Run an ssh probe call, handling the shared error ladder.

    This is the single SSOT for the ssh-error handling in all remote probers
    (prereq refactor — SR-DOCTOR-PRINCIPLED). Routes the common
    FileNotFoundError / TimeoutExpired / OSError / exit-255 → unreachable-caps
    ladder so each prober calls this once and checks the return type.

    Returns:
      ``subprocess.CompletedProcess`` on successful TCP connection (caller
        must still check ``returncode`` for scheduler-level errors).
      ``dict`` with ``probe_status="unreachable"`` on connection failure —
        the caller should return this dict directly.

    Never raises — all errors are captured and reported as unreachable.
    """
    from .adapters.remote import _ssh_exec
    try:
        result = _ssh_exec(host, argv, timeout=timeout)
    except FileNotFoundError:
        return {
            "probe_status": "unreachable",
            "reachable": False,
            "reason": "ssh binary not found — install openssh-client",
            "host": host,
            "archetype": archetype,
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
            "archetype": archetype,
        }
    except OSError as exc:
        return {
            "probe_status": "unreachable",
            "reachable": False,
            "reason": str(exc),
            "host": host,
            "archetype": archetype,
        }

    # ssh exit 255 = connection refused / auth failure (before scheduler runs)
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
            "archetype": archetype,
        }

    return result


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


# ---------------------------------------------------------------------------
# Permissions parse helpers (Stage 1b — SLURM)
# ---------------------------------------------------------------------------

def _parse_sacctmgr_assoc(stdout: str) -> list[dict[str, str]]:
    """Parse ``sacctmgr -P show assoc`` parsable2 output.

    The parsable2 format (``-P``) uses ``|`` as delimiter, first line = header.
    Returns a list of dicts mapping column names to values.
    Never raises — bad lines are skipped.
    """
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        return []
    header = [h.strip() for h in lines[0].split("|")]
    result: list[dict[str, str]] = []
    for line in lines[1:]:
        values = [v.strip() for v in line.split("|")]
        if len(values) < len(header):
            # Pad to header length
            values += [""] * (len(header) - len(values))
        result.append(dict(zip(header, values)))
    return result


def _parse_sacctmgr_qos(stdout: str) -> dict[str, dict[str, str]]:
    """Parse ``sacctmgr -P show qos`` parsable2 output.

    Returns ``{qos_name: {MaxWall, MaxTRESPU, MaxJobsPU, MaxSubmitPU, ...}}``.
    """
    rows = _parse_sacctmgr_assoc(stdout)  # same parsable2 format
    return {r.get("Name", ""): r for r in rows if r.get("Name")}


def _parse_scontrol_partitions(stdout: str) -> dict[str, dict[str, str]]:
    """Parse ``scontrol show partition`` output into a per-partition dict.

    Each partition block starts with ``PartitionName=<name>``.
    Extracts ``AllowAccounts``, ``AllowQos``, ``DenyQos``, ``MaxTime``.
    Returns ``{partition_name: {AllowAccounts, AllowQos, DenyQos, MaxTime}}``.
    """
    result: dict[str, dict[str, str]] = {}
    current: dict[str, str] = {}
    current_name: str = ""

    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            if current_name:
                result[current_name] = current
            current = {}
            current_name = ""
            continue
        # Each token is key=value; multiple tokens per line
        for token in line.split():
            if "=" not in token:
                continue
            k, _, v = token.partition("=")
            k = k.strip()
            v = v.strip()
            if k == "PartitionName":
                if current_name:
                    result[current_name] = current
                current_name = v
                current = {}
            elif k in ("AllowAccounts", "AllowQos", "DenyQos", "MaxTime", "DefaultTime"):
                current[k] = v

    # Flush last block
    if current_name:
        result[current_name] = current

    return result


def _probe_permissions_slurm(host: str) -> dict[str, Any]:
    """Probe SLURM permissions: sacctmgr (associations + QOS) + scontrol show partition.

    Rides the same ``_ssh_probe_call`` / ``_SSH_PROBE_OPTS`` SSOT as the
    inventory probe. Graceful degrade: sacctmgr absent / accounting not
    configured → ``{"available": False, "reason": "..."}`` → caller falls back
    to inventory-only proposal with an explicit banner (charter §2).

    Returns a permissions block::

        {
            "available": True,
            "associations": [...],        # per sacctmgr show assoc
            "qos": {...},                 # per sacctmgr show qos
            "partition_acls": {...},      # per scontrol show partition
            "allowed_partitions": [...],  # partitions user can submit to
            "forbidden_partitions": [...] # partitions discovered but access denied
        }

    or on failure::

        {"available": False, "reason": "<why>"}
    """
    import os
    user = os.environ.get("USER", os.environ.get("LOGNAME", ""))

    # 1. sacctmgr show assoc
    assoc_argv = _SSH_PROBE_OPTS + [
        "sacctmgr", "-P", "show", "assoc",
        f"user={user}",
        "format=Account,Partition,QOS,GrpTRES,MaxWall,MaxTRES",
    ]
    assoc_result = _ssh_probe_call(host, assoc_argv, "ssh+slurm", timeout=20)
    if isinstance(assoc_result, dict):
        # Connection failed (already caught by inventory probe, but handle defensively)
        return {"available": False, "reason": f"ssh failed: {assoc_result.get('reason', '?')}"}

    if assoc_result.returncode != 0:
        stderr_snip = (assoc_result.stderr or "")[:200]
        return {
            "available": False,
            "reason": (
                f"sacctmgr returned exit {assoc_result.returncode}: {stderr_snip} — "
                "accounting may not be configured; proposal is inventory-only"
            ),
        }

    associations = _parse_sacctmgr_assoc(assoc_result.stdout or "")

    # 2. sacctmgr show qos
    qos_argv = _SSH_PROBE_OPTS + [
        "sacctmgr", "-P", "show", "qos",
        "format=Name,MaxWall,MaxTRESPU,MaxJobsPU,MaxSubmitPU",
    ]
    qos_result = _ssh_probe_call(host, qos_argv, "ssh+slurm", timeout=20)
    qos_map: dict[str, dict[str, str]] = {}
    if not isinstance(qos_result, dict) and qos_result.returncode == 0:
        qos_map = _parse_sacctmgr_qos(qos_result.stdout or "")

    # 3. scontrol show partition
    scontrol_argv = _SSH_PROBE_OPTS + ["scontrol", "show", "partition"]
    scontrol_result = _ssh_probe_call(host, scontrol_argv, "ssh+slurm", timeout=20)
    partition_acls: dict[str, dict[str, str]] = {}
    if not isinstance(scontrol_result, dict) and scontrol_result.returncode == 0:
        partition_acls = _parse_scontrol_partitions(scontrol_result.stdout or "")

    # Build allowed / forbidden sets from associations + ACL cross-check
    # A partition is ALLOWED if the user's account/QOS appears in AllowAccounts/AllowQos
    # (or AllowAccounts=ALL). A partition not in associations is not proposed.
    assoc_partitions: set[str] = {
        a.get("Partition", "") for a in associations if a.get("Partition")
    }
    assoc_accounts: set[str] = {
        a.get("Account", "") for a in associations if a.get("Account")
    }

    allowed_partitions: list[str] = []
    forbidden_partitions: list[dict[str, str]] = []

    for part_name, acl in partition_acls.items():
        allow_accounts = acl.get("AllowAccounts", "ALL")
        if allow_accounts in ("ALL", ""):
            # Partition allows all accounts — user is allowed if they have an assoc
            if part_name in assoc_partitions or not assoc_partitions:
                allowed_partitions.append(part_name)
            else:
                forbidden_partitions.append({
                    "partition": part_name,
                    "reason": f"not in user associations (assoc partitions: {sorted(assoc_partitions)})",
                })
        else:
            # Partition restricts to specific accounts
            acl_accts = {a.strip() for a in allow_accounts.split(",")}
            if assoc_accounts & acl_accts:
                allowed_partitions.append(part_name)
            else:
                forbidden_partitions.append({
                    "partition": part_name,
                    "reason": (
                        f"AllowAccounts={allow_accounts} excludes user accounts "
                        f"{sorted(assoc_accounts) or ['(none found)']}"
                    ),
                })

    # Partitions in assoc but NOT in scontrol output — still allowed per sacctmgr
    scontrol_parts = set(partition_acls.keys())
    for p in assoc_partitions:
        if p and p not in scontrol_parts and p not in allowed_partitions:
            allowed_partitions.append(p)

    return {
        "available": True,
        "associations": associations,
        "qos": qos_map,
        "partition_acls": partition_acls,
        "allowed_partitions": sorted(set(allowed_partitions)),
        "forbidden_partitions": forbidden_partitions,
    }


# ---------------------------------------------------------------------------
# PROPOSE — Stage 2: inventory ∩ permissions → tier→partition (pure function)
# ---------------------------------------------------------------------------

def _gres_gpu_count(gpu_gres: str) -> int:
    """Extract GPU count from a GRES string like ``gpu:a100:4`` or ``gpu:2``.

    Returns 0 for ``(null)`` or unparseable GRES.
    """
    if not gpu_gres or gpu_gres == "(null)" or not gpu_gres.startswith("gpu:"):
        return 0
    parts = gpu_gres.split(":")
    # gpu:<type>:<count>  or  gpu:<count>
    try:
        return int(parts[-1])
    except (ValueError, IndexError):
        return 0


def _propose_tiers(
    partitions: list[dict[str, Any]],
    permissions: dict[str, Any] | None,
    gpu_tiers: dict[str, Any],
    run_outcomes: list[dict[str, Any]],
    lessons: list[dict[str, Any]],
) -> dict[str, Any]:
    """Pure deterministic function: inventory ∩ permissions → tier→partition proposal.

    Cheapest-that-fits: for each tier, pick the allowed partition with the
    smallest GPU count that meets the tier's GPU requirement. Never picks
    a forbidden partition. Surfaces unmapped tiers with a reason.

    LEARN integration: run_outcomes annotate contradicted pairings (OOM/FAILED).
    lesson rules surface inline when their trigger matches a proposed partition.

    Args:
      partitions: list of partition dicts from sinfo parse (inventory).
      permissions: dict from _probe_permissions_slurm, or None if unavailable.
      gpu_tiers: from compute manifest (e.g. {"tp1": {"gpus": 1, ...}}).
      run_outcomes: from compute manifest (e.g. [{"tier": "tp1", "result": "OOM", ...}]).
      lessons: from compute manifest rules (e.g. [{"trigger": "...", "fix": "..."}]).

    Returns::

        {
            "mapping": {
                "tp1": {
                    "partition": "gpu-short",
                    "rationale": "gpu-short [A100×4 GRES · ...",
                    "warnings": []   # OOM/FAILED outcomes or lesson matches
                },
                "tp2": {
                    "partition": None,
                    "rationale": "no allowed partition has ≥2 GPUs; nearest is gpu-priority (forbidden)",
                    "warnings": []
                }
            },
            "permissions_available": True,
            "inventory_only": False   # True when falling back to inventory-only
        }
    """
    # Determine allowed partitions
    permissions_available = False
    inventory_only = False
    allowed_set: set[str] | None = None  # None = no filter (inventory-only fallback)

    if permissions and permissions.get("available"):
        permissions_available = True
        allowed_set = set(permissions.get("allowed_partitions", []))
    else:
        inventory_only = True  # sacctmgr absent or unavailable

    # Build OOM index: {tier: ["OOM on <ts>", ...]}
    # Key on TIER ALONE. cmd_outcome_add (compute.py:524,537) writes
    # {job, tier, result, ts} — NO partition field. The partition to annotate
    # is the one _propose_tiers chooses for that tier (not the recorded outcome).
    # Requiring (tier, partition) would be silently inert against all real outcomes.
    oom_index: dict[str, list[str]] = {}
    for outcome in run_outcomes:
        tier_k = outcome.get("tier", "")
        res = outcome.get("result", "")
        if res in ("OOM", "FAILED") and tier_k:
            oom_index.setdefault(tier_k, [])
            ts = outcome.get("ts", "")
            oom_index[tier_k].append(f"{res} on {ts}" if ts else res)

    # Sort partitions by GPU count ascending (cheapest-first)
    def _sort_key(p: dict[str, Any]) -> int:
        return _gres_gpu_count(p.get("gpu_gres", "(null)"))

    sorted_parts = sorted(partitions, key=_sort_key)

    mapping: dict[str, Any] = {}

    for tier_name, tier_info in gpu_tiers.items():
        required_gpus: int = tier_info.get("gpus", 0)
        models: list[str] = tier_info.get("models", [])
        models_str = ", ".join(models) if models else "?"

        # Find cheapest allowed partition that meets GPU requirement
        best_partition: str | None = None
        best_gres: str = ""
        best_count: int = 0

        # Also track nearest forbidden partition for unmapped reason
        nearest_forbidden: str | None = None
        nearest_forbidden_gres: str = ""

        for p in sorted_parts:
            pname = p.get("partition", "")
            gres = p.get("gpu_gres", "(null)")
            gpu_count = _gres_gpu_count(gres)
            if gpu_count < required_gpus:
                continue
            # Meets GPU requirement — check allowed
            if allowed_set is not None and pname not in allowed_set:
                # Track nearest forbidden
                if nearest_forbidden is None:
                    nearest_forbidden = pname
                    nearest_forbidden_gres = gres
                continue
            # This partition fits and is allowed
            best_partition = pname
            best_gres = gres
            best_count = gpu_count
            break

        warnings: list[str] = []

        if best_partition is None:
            # No allowed partition fits
            if nearest_forbidden:
                reason = (
                    f"no allowed partition has ≥{required_gpus} GPU(s); "
                    f"nearest is {nearest_forbidden} [{nearest_forbidden_gres}] — forbidden"
                )
            elif required_gpus == 0:
                reason = "tier has gpus=0 — no GPU partition needed (CPU-only tier?)"
            else:
                reason = (
                    f"no partition found with ≥{required_gpus} GPU(s) in inventory"
                )
            mapping[tier_name] = {
                "partition": None,
                "rationale": reason,
                "warnings": warnings,
            }
            continue

        # Build rationale
        assoc_info = ""
        if permissions_available and permissions:
            # Find matching association for this partition
            assoc_list = permissions.get("associations", [])
            matching = [
                a for a in assoc_list
                if a.get("Partition") == best_partition or a.get("Partition") == ""
            ]
            if matching:
                a = matching[0]
                acct = a.get("Account", "")
                qos = a.get("QOS", "")
                maxwall = a.get("MaxWall", "")
                assoc_info = f" · account {acct}/qos {qos}"
                if maxwall:
                    assoc_info += f" · MaxWall {maxwall}"
            elif not assoc_info:
                assoc_info = " · (allowed per AllowAccounts/AllowQos)"

        rationale = (
            f"{best_partition} [{best_gres}×{best_count} · {models_str}{assoc_info}]"
        )

        # LEARN: check outcomes for OOM/FAILED on this tier (tier-keyed, not
        # tier+partition — cmd_outcome_add never records a partition field)
        outcome_warns = oom_index.get(tier_name, [])
        for ow in outcome_warns:
            warnings.append(
                f"⚠ {best_partition} had {ow} for {tier_name} — consider a larger partition"
            )

        # LEARN: check lesson rules for trigger matches
        for lesson in lessons:
            trigger = lesson.get("trigger", "")
            fix = lesson.get("fix", "")
            if trigger and best_partition and trigger.lower() in best_partition.lower():
                warnings.append(f"lesson: {trigger} → {fix}")

        mapping[tier_name] = {
            "partition": best_partition,
            "rationale": rationale,
            "warnings": warnings,
        }

    return {
        "mapping": mapping,
        "permissions_available": permissions_available,
        "inventory_only": inventory_only,
    }


def _probe_remote_slurm(host: str, backend_name: str) -> dict[str, Any]:
    """Probe an ssh+slurm backend: inventory (sinfo) + permissions (sacctmgr/scontrol).

    Uses BatchMode=yes + ConnectTimeout (via _ssh_probe_call) so the probe
    never hangs. GPU discovery goes through the scheduler (sinfo GRES), NOT
    login-node nvidia-smi — login nodes on HPC clusters are typically GPU-less.

    DISCOVER bifurcation (scheduler-cluster path):
      1a. Inventory: sinfo --format=%P %G %D  (what partitions/GPUs EXIST)
      1b. Permissions: sacctmgr + scontrol    (what you're ALLOWED to submit)

    Returns a capabilities dict with one of:
      probe_status="ok"              — reachable, sinfo ran; permissions sub-block present
      probe_status="scheduler_error" — reachable but sinfo returned non-zero
      probe_status="unreachable"     — ssh failed (timeout, auth, not found)
    """
    sinfo_argv = _SSH_PROBE_OPTS + ["sinfo", "--format=%P %G %D", "--noheader"]
    result = _ssh_probe_call(host, sinfo_argv, "ssh+slurm", timeout=20)

    # Connection failure — return the unreachable dict directly
    if isinstance(result, dict):
        return result

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

    caps: dict[str, Any] = {
        "probe_status": "ok",
        "reachable": True,
        "host": host,
        "archetype": "ssh+slurm",
        "partitions": parsed["partitions"],
        "gpu_types": parsed["gpu_types"],
    }

    # 1b — Permissions probe (SLURM-first; best-effort — graceful degrade on absence)
    caps["permissions"] = _probe_permissions_slurm(host)

    return caps


def _probe_remote_pbs(host: str, backend_name: str) -> dict[str, Any]:
    """Probe an ssh+pbs backend via ``pbsnodes -a``.

    Uses BatchMode=yes + ConnectTimeout (via _ssh_probe_call) so the probe
    never hangs. Returns a capabilities dict with probe_status in
    ("ok", "scheduler_error", "unreachable").

    PBS permission probe seam: qstat -Qf + qmgr are designed but not yet
    implemented. The permissions block is set to
    {"available": False, "reason": "PBS permission probe: not yet implemented"}.
    PBS clusters get inventory-only proposals until the seam is filled.
    """
    pbs_argv = _SSH_PROBE_OPTS + ["pbsnodes", "-a"]
    result = _ssh_probe_call(host, pbs_argv, "ssh+pbs", timeout=20)

    # Connection failure — return the unreachable dict directly
    if isinstance(result, dict):
        return result

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
        # PBS permission probe seam — qstat -Qf / qmgr acl_users: not yet implemented.
        # PBS clusters get inventory-only proposals. Fill this seam to enable full
        # permissions-aware proposals for PBS (SLURM-first per §5DOC boundary).
        "permissions": {
            "available": False,
            "reason": "PBS permission probe: not yet implemented",
        },
    }


def _probe_remote_ssh(host: str, backend_name: str) -> dict[str, Any]:
    """Probe a plain ssh (single-box) backend: connectivity + direct nvidia-smi.

    Uses BatchMode=yes + ConnectTimeout (via _ssh_probe_call) so the probe
    never hangs. This archetype is FLAT TOPOLOGY — the box you ssh into IS
    the compute node; direct nvidia-smi over ssh is CORRECT here (no trap).

    Correctness guard: this archetype must NOT run sacctmgr/scontrol (there is
    no scheduler on a single ssh box). Permissions = ssh-reachable + GPU-visible.
    The login-node nvidia-smi avoidance rule is slurm/pbs-specific; it must NOT
    leak to this archetype.
    """
    # Step 1: connectivity check
    check_argv = _SSH_PROBE_OPTS + ["true"]
    result = _ssh_probe_call(host, check_argv, "ssh", timeout=15)

    # Connection failure — return the unreachable dict directly
    if isinstance(result, dict):
        return result

    if result.returncode != 0:
        stderr_snip = (result.stderr or "")[:200]
        return {
            "probe_status": "unreachable",
            "reachable": False,
            "reason": f"ssh to '{host}' failed (exit {result.returncode}): {stderr_snip}",
            "host": host,
            "archetype": "ssh",
        }

    # Step 2: direct nvidia-smi over ssh (correct for flat single-box topology)
    # NOT sacctmgr — there is no scheduler; permissions = GPU-visible
    nvidia_argv = _SSH_PROBE_OPTS + [
        "nvidia-smi", "--query-gpu=name", "--format=csv,noheader"
    ]
    nvidia_result = _ssh_probe_call(host, nvidia_argv, "ssh", timeout=15)

    gpu_info: dict[str, Any] = {"available": False, "reason": "nvidia-smi probe failed"}
    if not isinstance(nvidia_result, dict) and nvidia_result.returncode == 0:
        gpus = [g.strip() for g in (nvidia_result.stdout or "").splitlines() if g.strip()]
        gpu_info = {"available": True, "count": len(gpus), "names": gpus}
    elif not isinstance(nvidia_result, dict):
        gpu_info = {
            "available": False,
            "reason": f"nvidia-smi exited {nvidia_result.returncode}",
        }

    return {
        "probe_status": "ok",
        "reachable": True,
        "host": host,
        "archetype": "ssh",
        "gpu_info": gpu_info,
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
# Secrets-forward resolvability probe (names-not-values)
# ---------------------------------------------------------------------------

def _build_doctor_secret_store(cfg: Config) -> Any:
    """Build the SecretStore selected by ``cfg.adapters.secrets`` (default env)."""
    from .adapters.base import _SECRETS_REGISTRY
    name = (getattr(cfg, "adapters", None) or {}).get("secrets", "env")
    cls = _SECRETS_REGISTRY.get(name) or _SECRETS_REGISTRY["env"]
    return cls()


def _probe_secrets_forward(
    profile: dict[str, Any],
    store: Any,
) -> list[dict[str, Any]]:
    """Probe resolvability of each forwarded secret NAME. NEVER returns the value.

    For each declared name: validate the name, then attempt ``store.get(name)``.
    Returns ``[{"name": <NAME>, "resolvable": bool, ["invalid_name": True]}]`` —
    a boolean + the name only, so the report can flag missing secrets without
    ever printing, logging, or returning the plaintext value.
    """
    from .adapters.secret_forward import validate_secret_name
    names = profile.get("secrets_forward") or []
    out: list[dict[str, Any]] = []
    for n in names:
        entry: dict[str, Any] = {"name": n}
        try:
            validate_secret_name(n)
            store.get(n)  # value intentionally discarded — never captured
            entry["resolvable"] = True
        except ValueError:
            entry["resolvable"] = False
            entry["invalid_name"] = True
        except KeyError:
            entry["resolvable"] = False
        out.append(entry)
    return out


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

    # Secrets-forward resolvability probe — attach per profile that declares it.
    # Names + a resolvable bool only; the value is never captured or returned.
    _secret_store: Any = None
    for backend_name, prof in profiles.items():
        if prof.get("secrets_forward") and backend_name in result:
            if _secret_store is None:
                _secret_store = _build_doctor_secret_store(cfg)
            result[backend_name]["secrets_forward"] = _probe_secrets_forward(
                prof, _secret_store
            )

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
        lines.extend(_backend_report_lines(backend_name, caps))
        lines.append("")

    return "\n".join(lines)


def _backend_report_lines(backend_name: str, caps: dict[str, Any]) -> list[str]:
    """Build the report lines for ONE backend's capabilities.

    The single source of truth for per-backend detail — shared by the plain
    :func:`format_report` and the rich :func:`richui.render_doctor` so the two
    surfaces never drift.  Does NOT emit the ``[backend]`` header or trailing
    blank line (the caller owns framing).
    """
    lines: list[str] = []
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

    # Secrets-forward resolvability (names + a mark only, never the value).
    sf = caps.get("secrets_forward")
    if sf:
        lines.append(f"  Secrets forward ({len(sf)}):")
        for e in sf:
            mark = "resolvable ✓" if e.get("resolvable") else "MISSING ✗"
            extra = " [invalid name]" if e.get("invalid_name") else ""
            lines.append(f"    {e.get('name', '?')}: [{mark}]{extra}")

    return lines


def _backend_status_kind(caps: dict[str, Any]) -> str:
    """Classify a backend's probe outcome into a richui panel ``kind``.

    ``ok`` (reachable / local), ``fail`` (unreachable / scheduler error), or
    ``neutral`` (unfilled / unknown).  Pure — reads only the caps dict.
    """
    probe_status = caps.get("probe_status")
    if probe_status == "ok" and caps.get("reachable"):
        return "ok"
    if probe_status in ("unreachable", "scheduler_error"):
        return "fail"
    if probe_status in ("unfilled", "unknown-archetype"):
        return "neutral"
    # Local (or generic) full-detail path — treat as ok (present + probed).
    if probe_status is None and caps.get("archetype") not in _REMOTE_ARCHETYPES:
        return "ok"
    return "neutral"


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
        # Permissions summary
        perms = caps.get("permissions", {})
        if perms.get("available"):
            allowed = perms.get("allowed_partitions", [])
            forbidden = perms.get("forbidden_partitions", [])
            lines.append(f"  Permissions: {len(allowed)} allowed partition(s), "
                         f"{len(forbidden)} forbidden")
            for ap in allowed[:6]:
                lines.append(f"    [allowed] {ap}")
            if len(allowed) > 6:
                lines.append(f"    ... ({len(allowed) - 6} more)")
            for fp in forbidden[:4]:
                pname = fp.get("partition", "?")
                reason = fp.get("reason", "?")
                lines.append(f"    [forbidden] {pname}: {reason}")
        else:
            reason = perms.get("reason", "sacctmgr not available")
            lines.append(f"  Permissions: not available — {reason}")
            lines.append(
                "  WARNING: proposal is inventory-only — verify access before submitting"
            )
    elif archetype == "ssh+pbs":
        lines.append("  PBS/Torque: reachable (pbsnodes successful)")
        raw = caps.get("raw_output", "")
        if raw:
            preview = raw[:200].replace("\n", " ")
            lines.append(f"  pbsnodes preview: {preview}")
        lines.append("  Permissions: PBS permission probe not yet implemented (inventory-only)")
    elif archetype == "ssh":
        lines.append("  Plain ssh: connectivity confirmed")
        gpu_info = caps.get("gpu_info", {})
        if gpu_info.get("available"):
            count = gpu_info.get("count", 0)
            names = ", ".join(gpu_info.get("names", [])[:3])
            lines.append(f"  GPU (nvidia-smi over ssh): {count} device(s) — {names}")
        else:
            reason = gpu_info.get("reason", "nvidia-smi not found")
            lines.append(f"  GPU (nvidia-smi over ssh): not available — {reason}")
    else:
        # Generic remote archetype — just confirm reachable
        lines.append("  Remote probe: reachable")

    return lines


# ---------------------------------------------------------------------------
# CONFIRM — Stage 3: quarantined tiers_proposed + --accept promotion
# ---------------------------------------------------------------------------

def _build_proposal(cfg: Config) -> dict[str, Any] | None:
    """Build a tier-partition proposal from the current doctor cache + manifest.

    Returns a proposal dict (from ``_propose_tiers``) or None if no suitable
    scheduler-cluster backend is found or no inventory data available.

    Reads: doctor cache (inventory + permissions), compute manifest (gpu_tiers,
    run_outcomes, rules).
    """
    cache = _read_cache(cfg)
    if cache is None:
        return None

    manifest = _load_manifest(cfg)
    gpu_tiers: dict[str, Any] = manifest.get("gpu_tiers", {})
    if not gpu_tiers:
        return None

    run_outcomes: list[dict[str, Any]] = manifest.get("run_outcomes", [])
    lessons: list[dict[str, Any]] = manifest.get("rules", [])

    # Find the first ssh+slurm backend with a successful probe
    backends = cache.get("backends", {})
    for _backend_name, entry in backends.items():
        caps = entry.get("capabilities", entry)
        if (
            caps.get("probe_status") == "ok"
            and caps.get("archetype") == "ssh+slurm"
        ):
            partitions = caps.get("partitions", [])
            permissions = caps.get("permissions")
            return _propose_tiers(
                partitions=partitions,
                permissions=permissions,
                gpu_tiers=gpu_tiers,
                run_outcomes=run_outcomes,
                lessons=lessons,
            )

    return None


def format_proposal_report(proposal: dict[str, Any]) -> str:
    """Format a proposal from ``_propose_tiers`` into a human-readable string.

    Includes rationale and warnings (OOM/lesson matches) for each tier.
    """
    lines: list[str] = ["=== rv doctor — proposed tier mapping ===", ""]

    inventory_only = proposal.get("inventory_only", False)
    if inventory_only:
        lines.append(
            "  WARNING: permissions not available (sacctmgr absent or no accounting DB)."
        )
        lines.append(
            "  Proposal is INVENTORY-ONLY — verify access before submitting."
        )
        lines.append("")

    mapping = proposal.get("mapping", {})
    if not mapping:
        lines.append("  No GPU tiers declared in compute manifest — nothing to propose.")
        lines.append("  Run `rv compute init` and declare gpu_tiers first.")
        lines.append("")
        return "\n".join(lines)

    for tier_name, row in mapping.items():
        partition = row.get("partition")
        rationale = row.get("rationale", "")
        warnings = row.get("warnings", [])
        if partition:
            lines.append(f"  {tier_name} -> {partition}")
            lines.append(f"    rationale: {rationale}")
        else:
            lines.append(f"  {tier_name} -> UNMAPPED")
            lines.append(f"    reason: {rationale}")
        for w in warnings:
            lines.append(f"    {w}")

    lines.append("")
    lines.append(
        "  Run `rv doctor --propose` to write this as a reviewable draft "
        "(tiers_proposed in the manifest)."
    )
    lines.append(
        "  Then edit as needed and run `rv doctor --accept` to make it live."
    )
    lines.append("")
    return "\n".join(lines)


def cmd_doctor_propose(cfg: Config) -> int:
    """Stage 3 -- write quarantined tiers_proposed block to the compute manifest.

    This is the ``rv doctor --propose`` action. The proposal is written under
    ``tiers_proposed`` in the compute manifest (NOT the live ``tiers``).
    Never silently touches ``tiers`` -- the live mapping requires ``--accept``.

    Non-TTY-safe: no blocking prompt.
    """
    proposal = _build_proposal(cfg)
    if proposal is None:
        print(
            "rv doctor --propose: no scheduler-cluster backend with probe data found.\n"
            "Run `rv doctor --refresh` first to discover inventory + permissions.",
            file=sys.stderr,
        )
        return 1

    ts = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
    manifest = _load_manifest(cfg)

    proposed_block: dict[str, Any] = {
        "status": "proposed",
        "source": f"rv doctor --propose {ts}",
        "mapping": proposal.get("mapping", {}),
        "inventory_only": proposal.get("inventory_only", False),
        "permissions_available": proposal.get("permissions_available", False),
    }
    manifest["tiers_proposed"] = proposed_block
    _save_manifest(cfg, manifest)

    print("Proposed tier mapping written to `tiers_proposed` in the compute manifest.")
    print("Review it, edit as needed, then run `rv doctor --accept` to make it live.")
    print("")
    print(format_proposal_report(proposal))
    return 0


def cmd_doctor_accept(cfg: Config) -> int:
    """Stage 3 -- promote tiers_proposed to live tiers in the compute manifest.

    This is the ``rv doctor --accept`` action. Shows a diff of what will change,
    then promotes ``tiers_proposed`` -> ``tiers``, stamps accepted, clears proposed.

    Non-TTY-safe: no blocking prompt. Only path by which a discovered mapping
    becomes live -- never auto-called by plain ``rv doctor``.
    """
    manifest = _load_manifest(cfg)
    proposed = manifest.get("tiers_proposed")

    if not proposed:
        print(
            "rv doctor --accept: no tiers_proposed found in the compute manifest.\n"
            "Run `rv doctor --propose` first to create a draft.",
            file=sys.stderr,
        )
        return 1

    if proposed.get("status") == "accepted":
        print("rv doctor --accept: tiers_proposed is already accepted. Nothing to do.")
        return 0

    mapping: dict[str, Any] = proposed.get("mapping", {})
    if not mapping:
        print(
            "rv doctor --accept: tiers_proposed.mapping is empty -- nothing to accept.",
            file=sys.stderr,
        )
        return 1

    existing_tiers = manifest.get("tiers", {})

    # Show diff
    print("=== rv doctor --accept: diff ===")
    print("")
    for tier_name, row in mapping.items():
        partition = row.get("partition")
        old_partition = existing_tiers.get(tier_name, {}).get("partition")
        if partition:
            if old_partition and old_partition != partition:
                print(f"  {tier_name}: {old_partition} -> {partition}  (changed)")
            elif old_partition == partition:
                print(f"  {tier_name}: {partition}  (unchanged)")
            else:
                print(f"  {tier_name}: (new) -> {partition}")
        else:
            print(f"  {tier_name}: UNMAPPED -- skipping")
    print("")

    # Promote: build the live tiers dict from the accepted mapping
    ts = datetime.datetime.now(tz=datetime.timezone.utc).isoformat()
    new_tiers: dict[str, Any] = dict(existing_tiers)
    for tier_name, row in mapping.items():
        partition = row.get("partition")
        if partition:
            new_tiers[tier_name] = {
                "partition": partition,
                "rationale": row.get("rationale", ""),
                "source": f"rv doctor --accept {ts}",
            }

    manifest["tiers"] = new_tiers

    # Stamp the proposed block as accepted and clear the mapping
    manifest["tiers_proposed"] = {
        "status": "accepted",
        "accepted_ts": ts,
        "source": proposed.get("source", ""),
    }

    _save_manifest(cfg, manifest)

    print(f"Tier mapping accepted and written to `tiers` in the compute manifest ({ts}).")
    print("Run `rv compute show` to verify the live configuration.")
    return 0


# ---------------------------------------------------------------------------
# CLI verb
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``doctor`` verb.

    When to use ``rv doctor``:
      - After ``rv compute init`` to discover what's available and what you're allowed.
      - After environment changes (new allocation, QOS change) — use ``--refresh``.
      - ``rv doctor --propose`` to draft a tier-to-partition mapping for review.
      - ``rv doctor --accept`` to promote the reviewed draft into the live config.

    Anti-pattern: do NOT run ``rv doctor --accept`` without reviewing the
    ``tiers_proposed`` block first — the proposal is a starting point, not a decision.
    Do NOT try to bypass ``--accept`` by hand-editing ``tiers_proposed`` status to
    "accepted" — use the verb so the diff is shown and the timestamp is stamped.
    """
    desc = (
        "Principled DISCOVER -> PROPOSE -> CONFIRM -> LEARN for compute environments. "
        "Run AFTER `rv compute init` (which declares WHERE your compute is). "
        "DISCOVER: probes inventory (sinfo GRES) + permissions (sacctmgr/scontrol) "
        "for ssh+slurm backends; direct nvidia-smi for ssh single-box backends. "
        "GPU discovery uses sinfo GRES (scheduler-aware, not login-node nvidia-smi). "
        "PROPOSE: prints a tentative tier-to-partition mapping (cheapest-that-fits) "
        "with rationale; never auto-applies it. "
        "CONFIRM: --propose writes a quarantined tiers_proposed block (not live tiers); "
        "--accept promotes tiers_proposed -> tiers on explicit human action. "
        "LEARN: recorded run_outcomes (rv compute outcome add) annotate the proposal. "
        "Second call reads cache (no re-probe). Use --refresh to force a fresh probe. "
        "Degrades gracefully when cluster is unreachable or sacctmgr is absent."
    )
    if parent is not None:
        p = parent.add_parser(
            "doctor",
            help=(
                "Probe compute capabilities + propose tier mapping "
                "(DISCOVER -> PROPOSE -> CONFIRM -> LEARN)."
            ),
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
    p.add_argument(
        "--propose",
        action="store_true",
        default=False,
        help=(
            "Write the proposed tier mapping to `tiers_proposed` in the compute manifest "
            "(NOT the live `tiers`). Review and edit before running --accept."
        ),
    )
    p.add_argument(
        "--accept",
        action="store_true",
        default=False,
        help=(
            "Promote `tiers_proposed` -> live `tiers` in the compute manifest. "
            "Shows a diff first. This is the only path that writes the live tier mapping. "
            "Requires a prior `rv doctor --propose` run."
        ),
    )

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch: rv doctor [--refresh] [--propose] [--accept]."""
    cfg: Config = getattr(args, "_cfg", None) or load_config()

    propose = getattr(args, "propose", False)
    accept = getattr(args, "accept", False)

    # --accept: promote tiers_proposed -> live tiers (no probe needed)
    if accept:
        return cmd_doctor_accept(cfg)

    # --propose: probe (or use cache), build proposal, write tiers_proposed
    if propose:
        # Ensure we have fresh probe data
        try:
            cmd_doctor(cfg, refresh=getattr(args, "refresh", False))
        except Exception as exc:  # pragma: no cover — safety net only
            print(f"rv doctor --propose: probe error: {exc}", file=sys.stderr)
            return 1
        return cmd_doctor_propose(cfg)

    # Default: probe + report + print proposal call-to-action (no manifest write)
    try:
        result = cmd_doctor(cfg, refresh=getattr(args, "refresh", False))
    except Exception as exc:  # pragma: no cover — safety net only
        print(f"rv doctor: unexpected error (this is a bug): {exc}", file=sys.stderr)
        return 1

    from .richui import should_render_rich, render_doctor
    if should_render_rich():
        try:
            render_doctor(result)
        except Exception:
            print(format_report(result))  # fall back to plain on any render hiccup
    else:
        print(format_report(result))

    # SR-APPROVE-GATE: print approval gate status.
    try:
        from .dag.approval import approval_status_lines
        from .adapters.base import EnvSecretStore
        approval_lines = approval_status_lines(cfg, EnvSecretStore())
        if approval_lines:
            print("=== approval gate ===")
            for line in approval_lines:
                print(line)
            print()
    except Exception:
        pass  # Non-fatal: doctor must not crash on approval import failure.

    # Print proposal if we have one (LEARN-informed)
    proposal = _build_proposal(cfg)
    if proposal is not None:
        print(format_proposal_report(proposal))

    return 0
