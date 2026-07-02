"""adapters/remote.py — Remote ComputeBackend adapter (SR-7).

One manifest-driven adapter for ssh, slurm, pbs, and generic archetypes.
Import-guarded: this module can be imported on a box with no ssh installed;
the error only surfaces when RemoteBackend.submit / status is actually called.

When to use: set adapters.backend = "slurm" | "pbs" | "ssh" | "generic" in
research_vault.toml, then declare a remote profile in the compute manifest
(state_dir/compute_manifest.json). The manifest declares host + submit_pattern
(the submit flags); optionally jobid_parse / status_cmd / status_parse /
state_map. Built-in defaults cover slurm + pbs + ssh archetypes; generic
requires full declaration.

Framing: local orchestrates; the cluster computes. submit() sends the job to
the cluster and returns a handle immediately. The caller backgrounds a
``rv wait-for sched:<backend>:<handle>`` to watch for completion — no daemon,
no poller engine is ever started here (§R).

Anti-pattern: do NOT hand-run ``ssh cluster sbatch/qsub ...`` with flags you
guessed — set adapters.backend and submit through the seam; the manifest
declares the submit + status + parse for you.

Stdlib only for core logic; subprocess is called lazily (not at import time)
so this module stays importable on machines without ssh.
"""
from __future__ import annotations

import re
import shlex
import subprocess
from typing import Any


# ---------------------------------------------------------------------------
# Built-in per-archetype defaults
# ---------------------------------------------------------------------------
# These ship so adopters of common schedulers declare nothing new beyond
# host + submit_pattern. Override any field in the manifest profile.
# generic = fully adopter-declared (no defaults).

_ARCHETYPE_DEFAULTS: dict[str, dict[str, Any]] = {
    "ssh+slurm": {
        "submit": "sbatch",
        "jobid_parse": r"Submitted batch job (\d+)",
        # {jobid} is substituted with re.escape(job_handle) before use
        "status_cmd": "sacct -j {jobid} --format=JobID,State --noheader -P",
        "status_parse": r"^{jobid}(?:\.\w+)?\|([A-Z_]+)",
        "state_map": {
            "COMPLETED": "DONE",
            "FAILED": "FAILED",
            "CANCELLED": "FAILED",
            "TIMEOUT": "FAILED",
            "NODE_FAIL": "FAILED",
            "OUT_OF_MEMORY": "FAILED",
            "PREEMPTED": "FAILED",
            "BOOT_FAIL": "FAILED",
            "RUNNING": "RUNNING",
            "COMPLETING": "RUNNING",
            "PENDING": "PENDING",
            "REQUEUED": "PENDING",
        },
    },
    "ssh+pbs": {
        "submit": "qsub",
        "jobid_parse": r"^(\d+)",
        "status_cmd": "qstat -f {jobid}",
        "status_parse": r"job_state\s*=\s*(\w)",
        "state_map": {
            "R": "RUNNING",
            "Q": "PENDING",
            "H": "PENDING",
            "W": "PENDING",
            "T": "PENDING",
            "C": "DONE",
            "E": "DONE",
            "F": "DONE",
        },
    },
    "ssh": {
        # Background-run mode: the submit_pattern is treated as a shell template
        # where {cmd} is replaced by shlex.join(cmd). Sent as a single shell
        # argument to ssh (ssh host '<template>').
        # Default: nohup + setsid-like background run, print PID on stdout.
        "submit": "nohup {cmd} </dev/null >/dev/null 2>&1 & echo $!",
        "jobid_parse": r"(\d+)$",   # last number on the line = PID
        # status_cmd=None triggers exit-code mode (kill -0 <pid>)
        "status_cmd": None,
        "status_parse": None,
        "state_map": {
            "_exit0": "RUNNING",
            "_exit_nonzero": "DONE",
        },
    },
    # "generic": no defaults — all fields required from manifest
}

# Map config/registry key → manifest archetype names to match
# Supports both the simplified key ("slurm") and the full name ("ssh+slurm").
_BACKEND_KEY_TO_ARCHETYPE: dict[str, tuple[str, ...]] = {
    "slurm":   ("slurm", "ssh+slurm"),
    "pbs":     ("pbs", "ssh+pbs"),
    "ssh":     ("ssh",),
    "generic": ("generic",),
}


def _merge_profile_defaults(profile: dict[str, Any]) -> dict[str, Any]:
    """Merge a manifest profile with its archetype's built-in defaults.

    Profile fields WIN over defaults — adopters can override any default.
    Returns a new dict; the original profile is not mutated.
    """
    archetype = profile.get("archetype", "generic")
    defaults = dict(_ARCHETYPE_DEFAULTS.get(archetype, {}))
    merged = defaults
    merged = {**defaults, **profile}
    return merged


def _run_status(job_handle: str, profile: dict[str, Any]) -> str:
    """Run the declared status command and map output to a Protocol state.

    This is the single SSOT shared by RemoteBackend.status and the
    ``sched:`` resolver in wait_for.py. Both call this function —
    no duplicate parsers.

    Returns one of: "PENDING" | "RUNNING" | "DONE" | "FAILED" | "UNKNOWN"
    """
    merged = _merge_profile_defaults(profile)
    archetype = merged.get("archetype", "generic")
    host = merged.get("host", "")
    state_map: dict[str, str] = merged.get("state_map", {})
    status_cmd: str | None = merged.get("status_cmd")
    status_parse: str | None = merged.get("status_parse")

    if not host:
        return "UNKNOWN"

    # ── ssh (no scheduler): exit-code mode ───────────────────────────────────
    # Indicated by status_cmd=None or a state_map with _exit0 / _exit_nonzero keys.
    if status_cmd is None or "_exit0" in state_map:
        try:
            result = subprocess.run(
                ["ssh", host, "kill", "-0", str(job_handle)],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                return state_map.get("_exit0", "RUNNING")
            return state_map.get("_exit_nonzero", "DONE")
        except FileNotFoundError:
            return "UNKNOWN"
        except subprocess.TimeoutExpired:
            return "UNKNOWN"
        except Exception:
            return "UNKNOWN"

    # ── Standard stdout-parse mode ────────────────────────────────────────────
    # Interpolate {jobid} in the status command (literal placeholder only, no regex)
    cmd_str = status_cmd.replace("{jobid}", str(job_handle))

    try:
        result = subprocess.run(
            ["ssh", host] + shlex.split(cmd_str),
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        return "UNKNOWN"
    except subprocess.TimeoutExpired:
        return "UNKNOWN"
    except Exception:
        return "UNKNOWN"

    stdout = result.stdout or ""

    # Apply status_parse regex if declared
    if status_parse:
        # Support {jobid} placeholder in the parse pattern (substituted with re.escape)
        parse_pattern = status_parse.replace("{jobid}", re.escape(str(job_handle)))
        for line in stdout.splitlines():
            m = re.search(parse_pattern, line.strip())
            if m:
                raw_state = m.group(1).upper().strip()
                return state_map.get(raw_state, "UNKNOWN")
        return "UNKNOWN"

    # Fallback: search stdout for any state_map key (substring scan, case-insensitive)
    stdout_upper = stdout.upper()
    for raw, proto in state_map.items():
        if raw.startswith("_"):
            continue  # skip exit-code sentinels
        if raw in stdout_upper:
            return proto
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# RemoteBackend
# ---------------------------------------------------------------------------

class RemoteBackend:
    """Manifest-driven remote ComputeBackend adapter.

    One class, one code path — behavior differs only by the active profile's
    declared commands + parse. Registered under four keys in _BACKEND_REGISTRY:
    "slurm" / "pbs" / "ssh" / "generic".

    Import-guarded: instantiation succeeds on any machine. Errors only surface
    when submit / status is called without ssh available.
    """

    def __init__(self, cfg: Any = None) -> None:
        self._cfg = cfg
        self._manifest: dict[str, Any] | None = None

    def _load_manifest(self) -> dict[str, Any]:
        """Lazy-load the compute manifest (cached after first load)."""
        if self._manifest is None:
            if self._cfg is None:
                raise RuntimeError(
                    "RemoteBackend requires a Config to read the compute manifest. "
                    "Ensure adapters are loaded via load_adapters(cfg)."
                )
            from ..compute import _load_manifest as compute_load_manifest
            self._manifest = compute_load_manifest(self._cfg)
        return self._manifest

    def _active_profile(self, name: str = "") -> dict[str, Any]:
        """Return the merged (defaults + declared) profile for the active backend.

        Uses cmd_explain(cfg, name) when a name is given to resolve the active
        backend (e.g., for job-specific tier/gpus). Falls back to the first
        active backend in the manifest.
        """
        manifest = self._load_manifest()
        backends = manifest.get("backends", {})
        profiles = backends.get("profiles", {})
        active_list = backends.get("active", [])

        # Resolve the active profile name
        if self._cfg and name and active_list:
            try:
                from ..compute import cmd_explain
                explained = cmd_explain(self._cfg, name)
                active_name = explained.get("backend", active_list[0])
            except Exception:
                active_name = active_list[0] if active_list else "local"
        else:
            active_name = active_list[0] if active_list else "local"

        profile = profiles.get(active_name, {})
        return _merge_profile_defaults(profile)

    def submit(
        self,
        cmd: list[str],
        *,
        name: str = "",
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> str:
        """Submit cmd to the remote cluster. Returns a job id handle (string).

        The handle can be passed to ``status(handle)`` or used in a
        ``rv wait-for sched:<backend>:<handle>`` watch expression.
        """
        profile = self._active_profile(name)
        archetype = profile.get("archetype", "generic")
        host = profile.get("host", "")

        if not host:
            raise ValueError(
                "Compute profile has no 'host' declared. "
                "Add host = '<ssh-alias-or-hostname>' to the profile in "
                "state_dir/compute_manifest.json."
            )

        # Resolve interpolation variables for the submit pattern
        interp: dict[str, Any] = {"name": name}
        if self._cfg and name:
            try:
                from ..compute import cmd_explain
                explained = cmd_explain(self._cfg, name)
                interp.update(
                    tier=explained.get("tier") or "",
                    gpus=str(explained.get("gpus") or ""),
                    conda_env=explained.get("conda_env") or "",
                )
            except Exception:
                pass

        # The submit field: either declared or built-in default.
        # For backward compat, submit_pattern is read as the primary field;
        # the new "submit" field (if declared) overrides it.
        submit_declared = profile.get("submit") or profile.get("submit_pattern") or ""

        if archetype == "ssh":
            # Shell-template mode: {cmd} placeholder → shlex.join(cmd)
            cmd_str = shlex.join(cmd)
            expanded = submit_declared.format(cmd=cmd_str, **{
                k: v for k, v in interp.items()
            })
            ssh_argv = ["ssh", host, expanded]
        else:
            # Standard mode: ssh <host> <submit_parts> [container_wrap] -- <cmd>
            submit_str = submit_declared
            # Substitute known variables if present in the pattern
            for key, val in interp.items():
                if val:
                    submit_str = submit_str.replace("{" + key + "}", str(val))

            # Ensure the base submit command is present
            archetype_default_submit = _ARCHETYPE_DEFAULTS.get(archetype, {}).get("submit", "")
            if archetype_default_submit and submit_str:
                base = archetype_default_submit.split()[0]
                if not any(part.strip() == base for part in shlex.split(submit_str)):
                    # submit_pattern doesn't include the base command — prepend it
                    submit_str = archetype_default_submit + " " + submit_str

            ssh_argv = ["ssh", host] + shlex.split(submit_str)

            # Container wrap (orthogonal modifier — honored for any archetype)
            container = profile.get("container")
            if container:
                runtime = container.get("runtime", "apptainer")
                image = container.get("image", "")
                ssh_argv += [runtime, "exec", image]

            ssh_argv += ["--"] + list(cmd)

        try:
            result = subprocess.run(
                ssh_argv,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "ssh not found. RemoteBackend requires ssh for remote submission. "
                "Install openssh-client or use backend=local."
            )

        if result.returncode != 0:
            raise RuntimeError(
                f"Remote submission failed (exit {result.returncode}):\n"
                f"  cmd: {ssh_argv}\n"
                f"  stdout: {result.stdout[:300]}\n"
                f"  stderr: {result.stderr[:300]}"
            )

        # Parse job id from stdout via jobid_parse
        jobid_parse = profile.get("jobid_parse") or (
            _ARCHETYPE_DEFAULTS.get(archetype, {}).get("jobid_parse", r"(\S+)")
        )
        m = re.search(jobid_parse, result.stdout)
        if not m:
            raise RuntimeError(
                f"Could not parse job id from submit stdout.\n"
                f"  stdout: {result.stdout[:200]!r}\n"
                f"  jobid_parse pattern: {jobid_parse!r}\n"
                f"  Declare 'jobid_parse' in the compute manifest profile to fix."
            )
        return m.group(1)

    def status(self, job_handle: str) -> str:
        """Return the current status of a submitted job.

        Returns one of: "PENDING" | "RUNNING" | "DONE" | "FAILED" | "UNKNOWN"

        Gracefully returns "UNKNOWN" when ssh is unavailable or the status
        command fails — never raises.
        """
        profile = self._active_profile()
        return _run_status(job_handle, profile)
