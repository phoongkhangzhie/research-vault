"""adapters/base.py — Adapter Protocols + local-default implementations.

When to use: import from here (or from ``research_vault.adapters``) when you
need to notify the operator, submit compute jobs, or resolve secrets. All paths
go through these adapters — never through macOS-only ``security`` calls or
hardcoded filesystem paths.

Protocols defined here:
  Notifier        — surface an event (info/warn/error) to the operator
  ComputeBackend  — submit and query async compute jobs
  SecretStore     — resolve a named secret to its plaintext value

Local-default implementations:
  FileNotifier       — stdout + inbox.jsonl + desk.md (no Telegram)
  LocalSubprocess    — run jobs as local subprocesses
  EnvSecretStore     — $ENV_VAR first, then ``keyring`` library, then fail

``load_adapters(cfg)`` returns a bound ``AdapterSet`` driven by the config's
``adapters.*`` keys. New adapters = add a new registry entry; no engine changes.

Stdlib only for core; ``keyring`` is imported lazily in EnvSecretStore so the
module stays importable on machines where keyring is not installed.
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

from ..config import Config


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------

@runtime_checkable
class Notifier(Protocol):
    """Surface an event to the operator.

    Implementations must write to at least stdout/stderr.
    Optional: write a structured record to inbox.jsonl + desk.md.
    NEVER call macOS-only ``security`` or require network.
    """

    def notify(
        self,
        message: str,
        *,
        level: str = "info",
        subject: str = "",
        tags: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Emit a notification.

        message: human-readable text
        level:   "info" | "warn" | "error" | "debug"
        subject: optional short label (slug, task id, etc.)
        tags:    optional list of string tags for routing
        payload: optional structured data (must be JSON-serializable)
        """
        ...


@runtime_checkable
class ComputeBackend(Protocol):
    """Submit and query async compute jobs.

    The local default runs a subprocess and returns immediately.
    A SLURM backend would call sbatch; a container backend would call docker/k8s.
    """

    def submit(
        self,
        cmd: list[str],
        *,
        name: str = "",
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> str:
        """Submit a command and return a job handle (string id).

        The local default returns the PID as a string.
        SLURM backend would return the job id from sbatch.
        """
        ...

    def status(self, job_handle: str) -> str:
        """Return the current status of a job.

        Returns one of: "PENDING" | "RUNNING" | "DONE" | "FAILED" | "UNKNOWN"
        """
        ...


@runtime_checkable
class SecretStore(Protocol):
    """Resolve a named secret to its plaintext value.

    Resolution order must be documented by each implementation.
    Never print or log secrets. Raise KeyError if not found.
    """

    def get(self, name: str) -> str:
        """Return the plaintext value for ``name``.

        Raises KeyError if the secret is not found.
        Never prints the secret or logs it.
        """
        ...


# ---------------------------------------------------------------------------
# Local-default: FileNotifier
# ---------------------------------------------------------------------------

class FileNotifier:
    """Notify via stdout + inbox.jsonl + desk.md.

    When to use: the "file" adapter (config ``adapters.notifier = "file"``).
    No Telegram, no macOS-only APIs. Writes structured records to
    ``<state_dir>/inbox.jsonl`` and a human-readable summary to
    ``<state_dir>/desk.md`` for hub review.

    Thread-safety: append to JSONL is effectively atomic on POSIX for small
    records (single write < PIPE_BUF). desk.md is not locked — concurrent
    writers may interleave lines, which is acceptable for asynchronous notify.
    """

    def __init__(self, state_dir: Path) -> None:
        self._state_dir = state_dir
        self._inbox = state_dir / "inbox.jsonl"
        self._desk = state_dir / "desk.md"

    def notify(
        self,
        message: str,
        *,
        level: str = "info",
        subject: str = "",
        tags: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        # Always write to stdout (or stderr for warn/error)
        prefix = f"[{level.upper()}]"
        if subject:
            prefix += f" {subject}:"
        line = f"{prefix} {message}"
        if level in ("warn", "error"):
            print(line, file=sys.stderr)
        else:
            print(line)

        # Build the structured record
        record: dict[str, Any] = {
            "ts": datetime.datetime.now(tz=datetime.timezone.utc).isoformat(),
            "level": level,
            "message": message,
        }
        if subject:
            record["subject"] = subject
        if tags:
            record["tags"] = tags
        if payload:
            record["payload"] = payload

        # Append to inbox.jsonl
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            with open(self._inbox, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass  # non-fatal — stdout already delivered

        # Append a line to desk.md (human-readable surface)
        try:
            ts_short = record["ts"][:19].replace("T", " ")
            desk_line = f"- `{ts_short}` **{level.upper()}**"
            if subject:
                desk_line += f" `{subject}`"
            desk_line += f" — {message}\n"
            with open(self._desk, "a", encoding="utf-8") as f:
                f.write(desk_line)
        except OSError:
            pass  # non-fatal


# ---------------------------------------------------------------------------
# Local-default: LocalSubprocess
# ---------------------------------------------------------------------------

class LocalSubprocess:
    """Run compute jobs as local subprocesses.

    When to use: the "local" adapter (config ``adapters.backend = "local"``).
    ``submit`` forks the command in the background using subprocess.Popen and
    returns the PID as a string. ``status`` polls the process via ``os.kill(0)``.

    For remote clusters: use a RemoteBackend adapter (slurm / pbs / ssh / generic).
    """

    def __init__(self, cfg: Any = None) -> None:
        # cfg is accepted and ignored — matches the RemoteBackend call shape
        # so load_adapters can call backend_cls(cfg) uniformly (D-SR7-5).
        # Track submitted PIDs → process objects (in-memory only, not persisted)
        self._procs: dict[str, subprocess.Popen] = {}  # type: ignore[type-arg]

    def submit(
        self,
        cmd: list[str],
        *,
        name: str = "",
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> str:
        """Submit a command as a background subprocess. Returns the PID string."""
        merged_env = dict(os.environ)
        if env:
            merged_env.update(env)
        proc = subprocess.Popen(
            cmd,
            env=merged_env,
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        pid_str = str(proc.pid)
        self._procs[pid_str] = proc
        return pid_str

    def status(self, job_handle: str) -> str:
        """Return the status of a submitted job by PID string."""
        proc = self._procs.get(job_handle)
        if proc is None:
            # Handle not in our registry — check via os.kill
            try:
                pid = int(job_handle)
                os.kill(pid, 0)  # signal 0: check existence, no actual kill
                return "RUNNING"
            except ProcessLookupError:
                return "DONE"   # process no longer exists — assume completed
            except ValueError:
                return "UNKNOWN"
            except PermissionError:
                # Process exists but we don't own it
                return "RUNNING"

        retcode = proc.poll()
        if retcode is None:
            return "RUNNING"
        if retcode == 0:
            return "DONE"
        return "FAILED"


# ---------------------------------------------------------------------------
# Local-default: EnvSecretStore
# ---------------------------------------------------------------------------

class EnvSecretStore:
    """Resolve secrets from environment variables, then ``keyring``, then fail.

    When to use: the "env" adapter (config ``adapters.secrets = "env"``).

    Resolution order:
      1. Environment variable matching the SCREAMING_SNAKE_CASE form of the
         name (e.g. "zotero-key" → $ZOTERO_KEY).
      2. The ``keyring`` library (cross-platform: macOS Keychain, SecretService
         on Linux, Windows Credential Manager) — service = "research-vault",
         username = the name as-is.
      3. Raise KeyError with a clear remediation message.

    Rationale: this replaces the vault's macOS-only ``security find-generic-password``
    calls with a cross-platform alternative. No ``security`` binary is ever called.

    Env-var form:
      Dashes and dots are converted to underscores, then upper-cased.
      "zotero-key"   → ZOTERO_KEY
      "s2-api-key"   → S2_API_KEY
      "anthropic.key" → ANTHROPIC_KEY

    ``keyring`` is imported lazily so the module stays importable on machines
    where it is not installed (a missing keyring means the env-var path is tried
    first, then a clear error is surfaced).
    """

    _SERVICE = "research-vault"

    def __init__(self) -> None:
        pass

    @staticmethod
    def _env_name(name: str) -> str:
        """Convert a secret name to its SCREAMING_SNAKE_CASE env-var form."""
        return name.replace("-", "_").replace(".", "_").upper()

    def get(self, name: str) -> str:
        """Return the plaintext value for ``name``.

        Raises KeyError if the secret is not found.
        """
        # 1. Environment variable
        env_var = self._env_name(name)
        val = os.environ.get(env_var, "").strip()
        if val:
            return val

        # 2. keyring library (cross-platform; NOT macOS security binary)
        # Honor VAULT_SKIP_KEYRING=1 so tests and CI can skip keyring reliably.
        if not os.environ.get("VAULT_SKIP_KEYRING"):
            try:
                import keyring  # type: ignore[import]
                stored = keyring.get_password(self._SERVICE, name)
                if stored:
                    return stored.strip()
            except ImportError:
                pass  # keyring not installed — fall through to error
            except Exception:
                pass  # keyring error (locked, permission denied, etc.)

        raise KeyError(
            f"Secret {name!r} not found.\n"
            f"  Fix: set ${env_var}  (env var)  or  keyring.set_password"
            f"({self._SERVICE!r}, {name!r}, '<value>')  (cross-platform keyring)\n"
            f"  Example: export {env_var}=<your-value>"
        )


# ---------------------------------------------------------------------------
# AdapterSet + factory
# ---------------------------------------------------------------------------

@dataclass
class AdapterSet:
    """A resolved, bound set of adapters for a Research Vault instance."""

    notifier: Notifier
    backend: ComputeBackend
    secrets: SecretStore


_NOTIFIER_REGISTRY: dict[str, type] = {
    "file": FileNotifier,
}

def _remote_backend_cls() -> type:
    """Lazily import RemoteBackend to keep base.py importable without ssh."""
    from .remote import RemoteBackend
    return RemoteBackend


_BACKEND_REGISTRY: dict[str, type] = {
    "local": LocalSubprocess,
    # SR-7: one RemoteBackend class registered under four archetype keys.
    # Loaded lazily so the module stays importable on any machine.
    "slurm":   None,  # populated in load_adapters via _remote_backend_cls()
    "pbs":     None,
    "ssh":     None,
    "generic": None,
}

_SECRETS_REGISTRY: dict[str, type] = {
    "env": EnvSecretStore,
}


def load_adapters(cfg: Config) -> AdapterSet:
    """Build an AdapterSet from a resolved Config.

    Adapter selection is driven by ``cfg.adapters``:
      adapters.notifier = "file"   → FileNotifier(cfg.state_dir)
      adapters.backend  = "local"  → LocalSubprocess()
      adapters.secrets  = "env"    → EnvSecretStore()

    Unknown adapter names raise ValueError with a clear message listing
    the known options.
    """
    adapter_cfg = cfg.adapters or {}

    # --- notifier ---
    notifier_name = adapter_cfg.get("notifier", "file")
    notifier_cls = _NOTIFIER_REGISTRY.get(notifier_name)
    if notifier_cls is None:
        known = ", ".join(sorted(_NOTIFIER_REGISTRY))
        raise ValueError(
            f"Unknown notifier adapter {notifier_name!r}. Known: {known}"
        )
    notifier: Notifier = notifier_cls(cfg.state_dir)  # type: ignore[call-arg]

    # --- backend ---
    backend_name = adapter_cfg.get("backend", "local")
    if backend_name not in _BACKEND_REGISTRY:
        known = ", ".join(sorted(_BACKEND_REGISTRY.keys()))
        raise ValueError(
            f"Unknown backend adapter {backend_name!r}. Known: {known}"
        )
    backend_cls = _BACKEND_REGISTRY[backend_name]
    if backend_cls is None:
        # Lazy-loaded remote backend (slurm / pbs / ssh / generic)
        backend_cls = _remote_backend_cls()
    # Pass cfg to backend_cls — LocalSubprocess ignores it; RemoteBackend uses it.
    backend: ComputeBackend = backend_cls(cfg)  # type: ignore[call-arg]

    # --- secrets ---
    secrets_name = adapter_cfg.get("secrets", "env")
    secrets_cls = _SECRETS_REGISTRY.get(secrets_name)
    if secrets_cls is None:
        known = ", ".join(sorted(_SECRETS_REGISTRY))
        raise ValueError(
            f"Unknown secrets adapter {secrets_name!r}. Known: {known}"
        )
    secrets: SecretStore = secrets_cls()  # type: ignore[call-arg]

    return AdapterSet(notifier=notifier, backend=backend, secrets=secrets)
