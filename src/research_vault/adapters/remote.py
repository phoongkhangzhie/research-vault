# SPDX-License-Identifier: AGPL-3.0-or-later
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
import sys
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
    merged = {**defaults, **profile}
    return merged


def _ssh_exec(
    host: str,
    argv: list[str],
    *,
    timeout: int = 15,
) -> "subprocess.CompletedProcess[str]":
    """Run ``ssh <host> <argv>`` and return the CompletedProcess.

    This is the shared SSOT for ssh subprocess calls in the remote adapter.
    It does NOT swallow exceptions — callers decide what to do.

    Raises:
      FileNotFoundError — ssh binary not found in PATH
      subprocess.TimeoutExpired — the command timed out

    Call sites:
      - ``_run_status`` (SR-CO) — uses this for all status queries
      - ``doctor._probe_remote_*`` (SR-CO-REMOTE) — uses this for remote probing
        with BatchMode=yes + ConnectTimeout flags prepended to argv

    Note: ``submit()`` uses ``subprocess.run`` directly (not this function) because
    submit builds a full ssh_argv list that includes the host, submit command, env/cwd
    flags, container wrap, and the remote cmd all as a single argv — it is
    structurally different from the two-argument (host, argv) form here, and has
    a longer timeout (60s) plus a different error-handling contract
    (FileNotFoundError → RuntimeError).
    """
    return subprocess.run(
        ["ssh", host] + list(argv),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


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
            result = _ssh_exec(host, ["kill", "-0", str(job_handle)], timeout=15)
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
        result = _ssh_exec(host, shlex.split(cmd_str), timeout=15)
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

    def _build_secret_store(self) -> Any:
        """Build a SecretStore from ``cfg.adapters.secrets`` via the registry.

        Used only by the secrets_forward path. Lazily constructed per submit so
        no store is built when a profile declares no forwarded secrets.
        """
        from .base import _SECRETS_REGISTRY
        name = "env"
        if self._cfg is not None:
            name = (getattr(self._cfg, "adapters", None) or {}).get("secrets", "env")
        cls = _SECRETS_REGISTRY.get(name)
        if cls is None:
            known = ", ".join(sorted(_SECRETS_REGISTRY))
            raise RuntimeError(
                f"secrets_forward: unknown secrets adapter {name!r}. Known: {known}"
            )
        return cls()

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

        # --- Secret forwarding (command-line-clean) ---------------------------
        # A forwarded secret's VALUE must never touch any argv. When a profile
        # declares secrets_forward (a list of env-var NAMES), we:
        #   1. Resolve-all-first, fail-closed — every name → validated → looked
        #      up in the SecretStore BEFORE any ssh call (a miss aborts here, so
        #      no job is ever sent).
        #   2. Stage over ssh STDIN — build the blob in memory, deliver it with
        #      input= (never argv, never local disk) to a mode-600 remote file.
        #   3. Activate — force the sh -c wrapper below (native_env is IGNORED
        #      when secrets are present; its --export would leak the value). The
        #      wrapper sources the file, deletes it, and traps EXIT/INT/TERM.
        # Absent secrets_forward, secret_plan stays None → behavior is unchanged.
        secrets_forward = profile.get("secrets_forward") or []
        secret_plan = None
        if secrets_forward:
            from .secret_forward import (
                build_secret_blob,
                make_plan,
                resolve_secrets,
                stage_over_stdin,
            )
            # 1. Resolve-all-first, fail-closed BEFORE any ssh call.
            resolved = resolve_secrets(secrets_forward, self._build_secret_store())
            # 2. Stage over ssh STDIN (values via input=, never argv, never disk).
            secret_plan = make_plan(
                profile.get("secrets_scratch", "$HOME/.rv-secrets"),
                profile.get("secrets_ttl_minutes", 720),
            )
            blob = build_secret_blob(resolved)
            stage_over_stdin(host, secret_plan, blob)
            # Drop the plaintext refs from the local frame promptly.
            del resolved, blob

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
            # Shell-template mode: {cmd} placeholder → shell string.
            # env and cwd are wired into the shell string so they execute on
            # the remote side.  We wrap in 'sh -c ...' when either is set so
            # that nohup/background mechanics in the template still work
            # (nohup cannot directly receive a compound shell expression).
            if secret_plan is not None:
                # Secrets present: the wrapper sources the staged file and wires
                # env/cwd itself — the secret value never reaches the argv.
                wrapper = secret_plan.activation_wrapper(
                    cwd=cwd, nonsecret_env=env, cmd=cmd
                )
                cmd_str = "sh -c " + shlex.quote(wrapper)
            elif env or cwd:
                shell_parts: list[str] = []
                if cwd:
                    shell_parts.append("cd " + shlex.quote(cwd))
                env_str = " ".join(
                    f"{k}={shlex.quote(v)}" for k, v in (env or {}).items()
                )
                inner = (env_str + " " if env_str else "") + shlex.join(cmd)
                shell_parts.append(inner)
                cmd_str = "sh -c " + shlex.quote(
                    " && ".join(shell_parts) if cwd else inner
                )
            else:
                cmd_str = shlex.join(cmd)
            expanded = submit_declared.format(cmd=cmd_str, **{
                k: v for k, v in interp.items()
            })
            ssh_argv = ["ssh", host, expanded]
        else:
            # Standard mode: ssh <host> <submit_parts> [native_flags] [container_wrap] -- <cmd>
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

            # Wire env and cwd into the command sent to the cluster.
            #
            # Two modes, selected by the manifest profile's ``native_env`` key
            # (bool, default false):
            #
            #   native_env: false (default) — wrap cmd in 'sh -c' so both env
            #     exports and directory changes execute on the remote side before
            #     the workload starts (the existing sh -c approach).
            #
            #   native_env: true — use the scheduler's OWN env/cwd flags instead
            #     of a shell wrapper.  This avoids a redundant nested sh -c when
            #     the adopter's cmd already starts with a shell interpreter, or
            #     when the scheduler's native mechanism is preferable:
            #       ssh+slurm → sbatch --export=KEY=val --chdir=<d>
            #       ssh+pbs   → qsub  -v KEY=val -d <d>
            #     The native flags are injected immediately after the sbatch/qsub
            #     base command, BEFORE any container wrap — otherwise SLURM parses
            #     them as apptainer arguments and silently ignores them.
            #     Limitation: native_env only applies to ssh+slurm / ssh+pbs
            #     archetypes. For other archetypes it falls back to sh -c.
            #     Value restriction: env values must not contain spaces, commas,
            #     semicolons, or quotes. Commas corrupt SLURM's --export delimiter;
            #     spaces split the value at word-boundary; semicolons are shell
            #     separators on the remote; quotes break shlex parsing. A value
            #     containing any of these characters is REJECTED with a loud error —
            #     use native_env: false (sh -c mode) or encode the value (base64)
            #     when the value must contain such characters.
            #
            # Without env/cwd, cmd passes through unchanged in both modes.
            native_env: bool = bool(profile.get("native_env", False))
            # native_env is IGNORED when secrets are present: its --export would
            # place the secret value on the scheduler argv (visible via ps/scontrol).
            # Force the sh -c wrapper instead (built below from secret_plan).
            use_native = native_env and bool(env or cwd) and secret_plan is None
            if native_env and secret_plan is not None:
                print(
                    "note: native_env ignored for this submit — secrets_forward "
                    "requires the sh -c wrapper to keep secret values off the argv.",
                    file=sys.stderr,
                )

            if use_native:
                # Guard: reject unsafe env values BEFORE building argv.
                # Silent corruption is worse than a loud error — reject early.
                _NATIVE_ENV_UNSAFE = {" ": "space", ",": "comma", ";": "semicolon",
                                      "'": "quote", '"': "quote"}
                for k, v in (env or {}).items():
                    for char, label in _NATIVE_ENV_UNSAFE.items():
                        if char in v:
                            raise ValueError(
                                f"native_env: env value for {k!r} contains a {label} "
                                f"({char!r}), which corrupts the scheduler's --export "
                                f"delimiter or enables injection on the remote side. "
                                f"Use native_env: false (sh -c mode) or encode the "
                                f"value (e.g. base64) to pass it safely."
                            )

                if archetype == "ssh+slurm":
                    if env:
                        export_val = ",".join(f"{k}={v}" for k, v in env.items())
                        ssh_argv.append(f"--export={export_val}")
                    if cwd:
                        ssh_argv.append(f"--chdir={cwd}")
                elif archetype == "ssh+pbs":
                    if env:
                        v_val = ",".join(f"{k}={v}" for k, v in env.items())
                        ssh_argv.extend(["-v", v_val])
                    if cwd:
                        ssh_argv.extend(["-d", cwd])
                else:
                    # Unknown archetype: native_env has no defined mapping —
                    # fall back to sh -c so env/cwd still land on the remote.
                    use_native = False

            # Container wrap (orthogonal modifier — honored for any archetype).
            # Injected AFTER native scheduler flags so that --export/--chdir are
            # parsed by sbatch/qsub, not handed to the container runtime as its args.
            container = profile.get("container")
            if container:
                runtime = container.get("runtime", "apptainer")
                image = container.get("image", "")
                ssh_argv += [runtime, "exec", image]

            if secret_plan is not None:
                # Secrets present: source the staged file, delete it, then run
                # cmd — the value is sourced, never on the argv. env/cwd are
                # wired inside the wrapper (the native/std paths are bypassed).
                wrapper = secret_plan.activation_wrapper(
                    cwd=cwd, nonsecret_env=env, cmd=cmd
                )
                effective_cmd: list[str] = ["/bin/sh", "-c", wrapper]
            elif not use_native and (env or cwd):
                std_parts: list[str] = []
                if cwd:
                    std_parts.append("cd " + shlex.quote(cwd))
                std_env = " ".join(
                    f"{k}={shlex.quote(v)}" for k, v in (env or {}).items()
                )
                std_inner = (std_env + " " if std_env else "") + shlex.join(cmd)
                std_parts.append(std_inner)
                effective_cmd = [
                    "/bin/sh", "-c",
                    " && ".join(std_parts) if cwd else std_inner,
                ]
            else:
                effective_cmd = list(cmd)

            ssh_argv += ["--"] + effective_cmd

        def _cleanup_staged_secret() -> None:
            # Best-effort remote rm of the staged secret when the submit failed
            # (the wrapper's trap never fires — the job never started). Never
            # masks the original error; the TTL sweeper is the final backstop.
            if secret_plan is not None:
                from .secret_forward import best_effort_cleanup
                best_effort_cleanup(host, secret_plan)

        try:
            result = subprocess.run(
                ssh_argv,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError:
            _cleanup_staged_secret()
            raise RuntimeError(
                "ssh not found. RemoteBackend requires ssh for remote submission. "
                "Install openssh-client or use backend=local."
            )

        if result.returncode != 0:
            _cleanup_staged_secret()
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
