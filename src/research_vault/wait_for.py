"""wait_for.py — the §R wait primitive for Research Vault.

When to use: ``rv wait-for <watch> [--then '<cmd>'] [--timeout <secs>] [--interval <secs>]``

The caller RETURNS IMMEDIATELY. A detached background shell polls the watch
expression and fires --then on resolution. This is structurally non-blocking:
it cannot sleep-loop the caller.

This is the SR-2 primitive that SR-3's DAG afterok will compose. The resolver
grammar is importable and reusable (the DAG imports resolve_watch directly).

Watch sources (resolver grammar):
  artifact:<path>+fresh    — file exists AND was written after registration
  artifact:<path>          — file exists
  sched:<backend>:<jobid>  — remote job reaches a terminal state via manifest-
                             driven status (SR-7: slurm / pbs / ssh / generic).
                             Use: submit → get handle → background
                             ``rv wait-for sched:<backend>:<handle>``
  sacct:<jobid>            — SLURM job terminal state (back-compat alias for
                             sched:slurm:<jobid>; fully live sacct resolver)
  pr:<owner/repo>#<n>      — PR state reaches MERGED
  cmd:<shell-cmd>          — shell command exits 0
  url:<url>                — HTTP HEAD returns < 400

Verify modifiers (appended with '+'):
  fresh                    — shorthand for fresh_since_registered (written after now)

Resolver grammar is importable by SR-3's DAG for afterok composition.

Stdlib only for core logic. The sched: resolver lazy-imports
``research_vault.adapters.remote`` at resolution time (not at import) so this
module stays importable on machines without ssh.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .config import Config, load_config


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------

_DURATION_RE = re.compile(
    r"^\+?(?:(?P<d>\d+)d)?(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?(?:(?P<s>\d+)s?)?$"
)


def parse_duration_secs(s: str) -> int:
    """Parse a human duration string to seconds.

    Accepts: 3600, +3600, 1h, 2h30m, 1d, +6h, etc. Bare integer = seconds.
    """
    s = s.strip()
    try:
        return int(s.lstrip("+"))
    except ValueError:
        pass
    m = _DURATION_RE.match(s)
    if not m:
        raise ValueError(f"cannot parse duration: {s!r}")
    d = int(m.group("d") or 0)
    h = int(m.group("h") or 0)
    mi = int(m.group("m") or 0)
    sec = int(m.group("s") or 0)
    total = d * 86400 + h * 3600 + mi * 60 + sec
    if total == 0:
        raise ValueError(f"zero duration: {s!r}")
    return total


# ---------------------------------------------------------------------------
# SR-8: Dataset resolver helpers (stdlib only — no data/compute library)
# ---------------------------------------------------------------------------

def _parse_dataset_note_frontmatter(text: str) -> dict[str, str]:
    """Parse YAML frontmatter from a datasets provenance note.

    Returns a dict of field → value strings (stripped). Empty dict if no frontmatter.
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm_block = text[3:end].strip()
    fields: dict[str, str] = {}
    for line in fm_block.splitlines():
        m = re.match(r"^(\w+):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val.startswith(("'", '"')) and val.endswith(val[0]):
                val = val[1:-1]
            fields[key] = val
    return fields


def _is_local_path(location: str) -> bool:
    """Return True if location looks like a local filesystem path (not URL/DOI)."""
    lower = location.lower()
    for prefix in ("http://", "https://", "ftp://", "s3://", "gs://", "doi:", "hdfs://"):
        if lower.startswith(prefix):
            return False
    return True


def _verify_local_file_hash(location: str, recorded_hash: str) -> dict:
    """Verify a local file's sha256 hash against the recorded hash.

    Returns {"ok": bool, "state": str, "error": str|None}.
    recorded_hash must be in "sha256:<hex>" format.
    If the hash is in an unknown format, we accept it (forward-compatible).

    Uses STREAMING chunked read (1 MiB chunks) so large data artifacts do not
    load into RAM — datasets are big-by-premise (reviewer finding, SR-8 amendment).
    """
    p = Path(location)
    if not p.exists():
        return {
            "ok": False,
            "state": "artifact-missing",
            "error": f"data artifact not found: {location}",
        }

    # Only verify sha256: prefixed hashes; other formats are accepted as-is
    if not recorded_hash.startswith("sha256:"):
        return {"ok": True, "state": "hash-format-unknown", "error": None}

    expected_hex = recorded_hash[len("sha256:"):]
    try:
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            while chunk := fh.read(1 << 20):  # 1 MiB chunks — streaming (datasets are big)
                h.update(chunk)
        actual_hex = h.hexdigest()
    except OSError as e:
        return {"ok": False, "state": "hash-read-error", "error": str(e)}

    if actual_hex != expected_hex:
        return {
            "ok": False,
            "state": f"hash-mismatch(expected={expected_hex[:12]}...,actual={actual_hex[:12]}...)",
            "error": None,
        }
    return {"ok": True, "state": "hash-verified", "error": None}


def check_dataset_provenance(
    dataset_note_filename: str,
    datasets_root: Path,
) -> list[str]:
    """Validate a datasets provenance note at complete-time (dag complete gate).

    Args:
      dataset_note_filename — note filename in datasets_root (e.g. "my-data.md").
                              The produces.dataset schema value is this filename.
      datasets_root         — cfg.datasets_root (the shared cross-project store).

    Returns a list of issue strings (empty = OK, gate passes).

    Checks:
      1. Provenance note exists at datasets_root / dataset_note_filename
      2. Note has non-empty `location` field
      3. Note has non-empty `hash` field
      4. If location is a local file: file exists AND sha256 matches (streaming read)

    SR-8 amendment: uses datasets_root (shared) not notes_root (project-scoped).
    """
    note_path = Path(dataset_note_filename)
    if not note_path.is_absolute():
        note_path = datasets_root / dataset_note_filename

    if not note_path.exists():
        return [f"dataset provenance note does not exist: {note_path}"]

    try:
        text = note_path.read_text(encoding="utf-8")
    except OSError as e:
        return [f"cannot read dataset provenance note {note_path}: {e}"]

    fields = _parse_dataset_note_frontmatter(text)
    issues: list[str] = []

    location = fields.get("location", "").strip()
    if not location:
        issues.append(
            f"datasets note {note_path.name!r}: missing 'location' field "
            f"(path/URL/DOI of the actual data artifact)"
        )

    recorded_hash = fields.get("hash", "").strip()
    if not recorded_hash:
        issues.append(
            f"datasets note {note_path.name!r}: missing 'hash' field "
            f"(content hash in sha256:<hex> format)"
        )

    if issues:
        return issues

    # For local file paths: verify file exists and hash matches
    if _is_local_path(location):
        check = _verify_local_file_hash(location, recorded_hash)
        if not check["ok"]:
            issues.append(
                f"datasets note {note_path.name!r}: {check['state']} — "
                f"location={location!r}, recorded hash={recorded_hash!r}"
                + (f" — {check['error']}" if check.get("error") else "")
            )

    return issues


# ---------------------------------------------------------------------------
# sched: resolver helpers (SR-7) — shared SSOT with RemoteBackend.status
# ---------------------------------------------------------------------------

# SLURM terminal states (shared by sacct: resolver and the _resolve_sched
# degrade fallback — single definition, no drift).
_SLURM_TERMINAL: frozenset[str] = frozenset({
    "COMPLETED", "FAILED", "CANCELLED", "TIMEOUT",
    "NODE_FAIL", "OUT_OF_MEMORY", "PREEMPTED", "BOOT_FAIL",
})


def _parse_sacct_state(stdout: str, job_id: str) -> dict[str, Any]:
    """Parse ``sacct --noheader -P`` output for *job_id* → resolve_watch dict.

    Shared SSOT for the ``sacct:`` resolver and the ``_resolve_sched`` slurm
    degrade path — eliminates the duplicate line-parse loop that previously
    lived in both.

    Returns the standard resolve_watch shape::

        {"ready": bool, "state": str, "artifact_path": None, "error": None}

    ``ready`` is True only when the job reached a terminal SLURM state.
    When *job_id* is not found in *stdout*, returns ``ready=False`` +
    ``state="pending"``.
    """
    for line in stdout.splitlines():
        parts = line.strip().split("|")
        if len(parts) >= 2:
            job_col = parts[0].strip()
            state = parts[1].strip().upper().split()[0]
            if job_col.split(".")[0] == str(job_id) or job_col == str(job_id):
                return {
                    "ready": state in _SLURM_TERMINAL,
                    "state": state,
                    "artifact_path": None,
                    "error": None,
                }
    return {"ready": False, "state": "pending", "artifact_path": None, "error": None}

def _resolve_sched(backend_name: str, job_id: str) -> dict[str, Any]:
    """Resolve a sched:<backend>:<jobid> watch expression.

    Lazy-imports research_vault.adapters.remote._run_status so this module
    stays importable without ssh. Falls back to the built-in sacct path for
    sched:slurm: when config cannot be loaded (zero-config back-compat).

    Returns the standard resolve_watch dict:
      {"ready": bool, "state": str, "artifact_path": None, "error": str|None}
    where ready=True iff Protocol state is "DONE" or "FAILED" (terminal).
    """
    _TERMINAL = frozenset({"DONE", "FAILED"})
    try:
        from .adapters.remote import (
            RemoteBackend,  # noqa: F401 — ensure module loads
            _run_status,
            _merge_profile_defaults,
            _BACKEND_KEY_TO_ARCHETYPE,
        )
        from .config import load_config
        from .compute import _load_manifest

        cfg = load_config()
        manifest = _load_manifest(cfg)
        backends = manifest.get("backends", {})
        profiles = backends.get("profiles", {})
        active_list = backends.get("active", [])

        # Find an active profile whose archetype matches backend_name
        target_archetypes = _BACKEND_KEY_TO_ARCHETYPE.get(
            backend_name, (backend_name,)
        )
        profile: dict[str, Any] = {}
        for pname in active_list:
            p = profiles.get(pname, {})
            arch = p.get("archetype", "")
            if arch in target_archetypes:
                profile = p
                break
        if not profile and active_list:
            # Fallback: first active profile regardless of archetype
            profile = profiles.get(active_list[0], {})

        proto_state = _run_status(job_id, profile)
        ready = proto_state in _TERMINAL
        return {
            "ready": ready,
            "state": proto_state,
            "artifact_path": None,
            "error": None,
        }

    except Exception as exc:
        # Graceful degrade: config unavailable or remote module import failed.
        # For sched:slurm: fall back to the built-in sacct path so this behaves
        # identically to sacct:<jobid> (back-compat at zero-config).
        if backend_name in ("slurm",):
            # Zero-config degrade: call sacct directly via ssh alias.
            # Uses _parse_sacct_state (shared with the sacct: resolver — single SSOT).
            alias = os.environ.get("RV_SSH_ALIAS", "sc")
            try:
                result = subprocess.run(
                    ["ssh", alias, "sacct", "-j", str(job_id),
                     "--format=JobID,State", "--noheader", "-P"],
                    capture_output=True, text=True, timeout=15,
                )
                if result.returncode != 0:
                    return {
                        "ready": False, "state": "unknown",
                        "artifact_path": None, "error": result.stderr[:200],
                    }
                return _parse_sacct_state(result.stdout, job_id)
            except FileNotFoundError:
                return {
                    "ready": False, "state": "ssh-unavailable",
                    "artifact_path": None, "error": "ssh not available for sacct",
                }
            except Exception as inner_exc:
                return {
                    "ready": False, "state": "error",
                    "artifact_path": None, "error": str(inner_exc),
                }
        return {
            "ready": False,
            "state": "error",
            "artifact_path": None,
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# Resolver grammar
# ---------------------------------------------------------------------------

def resolve_watch(watch: str, *, registered_ts: float | None = None) -> dict[str, Any]:
    """Resolve a watch expression to a status dict.

    This function is the importable resolver — SR-3's DAG afterok composes it.

    Returns:
      {
        "ready": bool,          True when the watch condition is met
        "state": str,           human-readable state
        "artifact_path": str|None,
        "error": str|None,
      }

    Watch expressions:
      artifact:<path>           — file/dir exists
      artifact:<path>+fresh     — file exists AND mtime >= registered_ts
      sacct:<jobid>             — SLURM job terminal (COMPLETED/FAILED/etc.)
                                  (reads via ssh; skip if no remote)
      pr:<owner/repo>#<n>       — PR merged (via gh)
      cmd:<shell-cmd>           — shell command exits 0
      url:<url>                 — HTTP HEAD returns < 400

    The +fresh modifier on artifact: checks mtime >= registered_ts (the
    timestamp captured at registration). This prevents false-satisfaction
    on stale pre-existing artifacts (the v4 false-satisfaction bug pattern).
    """
    watch = (watch or "").strip()

    # ── note:<type>/<id>[+fresh] — notes_root-aware OKF note watch ───────────
    # Resolves the note path relative to load_config().notes_root.
    # Use this form in DAG manifests for portable OKF note watches.
    # Example: "note:experiments/exp-q1.md+fresh"
    if watch.startswith("note:"):
        rest = watch[len("note:"):]
        check_fresh = False
        if rest.endswith("+fresh"):
            rest = rest[:-len("+fresh")]
            check_fresh = True

        try:
            from .config import load_config as _load_config
            _cfg = _load_config()
            p = _cfg.notes_root / rest
        except Exception as e:
            return {
                "ready": False,
                "state": "config-error",
                "artifact_path": rest,
                "error": f"cannot resolve notes_root for note: watch: {e}",
            }

        exists = p.exists()
        if not exists:
            return {"ready": False, "state": "missing", "artifact_path": str(p), "error": None}

        if check_fresh:
            if registered_ts is None:
                return {
                    "ready": False,
                    "state": "fresh-check-skipped",
                    "artifact_path": str(p),
                    "error": "registered_ts required for +fresh check",
                }
            mtime = p.stat().st_mtime
            fresh = mtime >= registered_ts
            return {
                "ready": fresh,
                "state": (
                    f"fresh(mtime={mtime:.0f},registered={registered_ts:.0f})"
                    if fresh else "stale"
                ),
                "artifact_path": str(p),
                "error": None,
            }

        return {"ready": True, "state": "exists", "artifact_path": str(p), "error": None}

    # ── dataset:<id> — SR-8 dataset provenance resolver ──────────────────────
    # Resolves ready only when:
    #   1. The provenance note <id>.md exists in cfg.datasets_root (shared cross-project)
    #   2. The note has a non-empty `location` field (points-to)
    #   3. The note has a non-empty `hash` field (content hash)
    #   4. If location is a local file path: the file exists AND the sha256 matches
    #   5. If location is a URL/DOI: trust the recorded hash (no remote fetch)
    #
    # SR-8 amendment: uses cfg.datasets_root (shared across projects), not
    # notes_root/datasets/ (project-scoped). This lets a dataset note filed for
    # one project be waited-on by a DAG finding in any other project.
    #
    # Anti-pattern: do NOT hand-copy a data path into a finding — file a datasets/
    # provenance note and afterok on it so lineage is structural (SR-8).
    if watch.startswith("dataset:"):
        dataset_id = watch[len("dataset:"):]
        if not dataset_id.strip():
            return {
                "ready": False,
                "state": "invalid-dataset-id",
                "artifact_path": None,
                "error": "dataset: watch requires a non-empty dataset id",
            }

        try:
            from .config import load_config as _load_config
            _cfg = _load_config()
            note_path = _cfg.datasets_root / f"{dataset_id}.md"
        except Exception as e:
            return {
                "ready": False,
                "state": "config-error",
                "artifact_path": None,
                "error": f"cannot resolve datasets_root for dataset: watch: {e}",
            }

        if not note_path.exists():
            return {
                "ready": False,
                "state": "note-missing",
                "artifact_path": str(note_path),
                "error": None,
            }

        # Parse the provenance note frontmatter
        try:
            text = note_path.read_text(encoding="utf-8")
        except OSError as e:
            return {
                "ready": False,
                "state": "note-unreadable",
                "artifact_path": str(note_path),
                "error": str(e),
            }

        fields = _parse_dataset_note_frontmatter(text)
        location = fields.get("location", "").strip()
        recorded_hash = fields.get("hash", "").strip()

        if not location:
            return {
                "ready": False,
                "state": "location-missing",
                "artifact_path": str(note_path),
                "error": "datasets note missing 'location' field",
            }
        if not recorded_hash:
            return {
                "ready": False,
                "state": "hash-missing",
                "artifact_path": str(note_path),
                "error": "datasets note missing 'hash' field",
            }

        # For local file paths: verify the file exists and hash matches.
        # For URLs (http/https/ftp) and DOIs: trust the recorded hash.
        if _is_local_path(location):
            check_result = _verify_local_file_hash(location, recorded_hash)
            if not check_result["ok"]:
                return {
                    "ready": False,
                    "state": check_result["state"],
                    "artifact_path": str(note_path),
                    "error": check_result.get("error"),
                }

        return {
            "ready": True,
            "state": f"provenance-ok(location={location!r},hash={recorded_hash[:20]}...)",
            "artifact_path": str(note_path),
            "error": None,
        }

    # ── artifact:<path>[+fresh] ───────────────────────────────────────────────
    if watch.startswith("artifact:"):
        rest = watch[len("artifact:"):]
        # Split on '+fresh' suffix
        check_fresh = False
        if rest.endswith("+fresh"):
            rest = rest[:-len("+fresh")]
            check_fresh = True

        p = Path(rest)
        exists = p.exists()
        if not exists:
            return {"ready": False, "state": "missing", "artifact_path": rest, "error": None}

        if check_fresh:
            if registered_ts is None:
                return {
                    "ready": False,
                    "state": "fresh-check-skipped",
                    "artifact_path": rest,
                    "error": "registered_ts required for +fresh check",
                }
            mtime = p.stat().st_mtime
            fresh = mtime >= registered_ts
            return {
                "ready": fresh,
                "state": f"fresh(mtime={mtime:.0f},registered={registered_ts:.0f})" if fresh else "stale",
                "artifact_path": rest,
                "error": None,
            }

        return {"ready": True, "state": "exists", "artifact_path": rest, "error": None}

    # ── sched:<backend>:<jobid> ───────────────────────────────────────────────
    # Manifest-driven scheduler resolver (SR-7).  One predicate, all archetypes.
    # Format: sched:slurm:12345 | sched:pbs:67890 | sched:ssh:99 | sched:generic:JOB-1
    # The <backend> value matches the config key (slurm/pbs/ssh/generic).
    # Shares _run_status with RemoteBackend.status — single SSOT, no duplicate parsers.
    if watch.startswith("sched:"):
        rest = watch[len("sched:"):]
        # Split on first ":" only to allow job ids that themselves contain ":"
        sep = rest.find(":")
        if sep == -1:
            return {
                "ready": False,
                "state": "error",
                "artifact_path": None,
                "error": f"sched: watch must be sched:<backend>:<jobid>, got: {watch!r}",
            }
        backend_name = rest[:sep]
        job_id = rest[sep + 1:]
        return _resolve_sched(backend_name, job_id)

    # ── sacct:<jobid> ─────────────────────────────────────────────────────────
    # Back-compat alias: equivalent to sched:slurm:<jobid>.
    # The resolver is fully live — not a stub.
    # Uses _parse_sacct_state (shared with the _resolve_sched degrade path —
    # single SSOT, no duplicate line-parse loop).
    if watch.startswith("sacct:"):
        job_id = watch[len("sacct:"):]
        alias = os.environ.get("RV_SSH_ALIAS", "sc")
        try:
            result = subprocess.run(
                ["ssh", alias, "sacct", "-j", str(job_id),
                 "--format=JobID,State", "--noheader", "-P"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode != 0:
                return {"ready": False, "state": "unknown", "artifact_path": None,
                        "error": result.stderr[:200]}
            return _parse_sacct_state(result.stdout, job_id)
        except subprocess.TimeoutExpired:
            return {"ready": False, "state": "timeout", "artifact_path": None, "error": "sacct timeout"}
        except FileNotFoundError:
            # ssh not available (e.g. local dev machine) — return pending
            return {"ready": False, "state": "ssh-unavailable", "artifact_path": None,
                    "error": "ssh not available for sacct"}
        except Exception as exc:
            return {"ready": False, "state": "error", "artifact_path": None, "error": str(exc)}

    # ── pr:<repo>#<n> ─────────────────────────────────────────────────────────
    if watch.startswith("pr:"):
        spec = watch[len("pr:"):]
        try:
            repo, _, pr_num = spec.rpartition("#")
            r = subprocess.run(
                ["gh", "pr", "view", pr_num, "--repo", repo, "--json", "state"],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode != 0:
                return {"ready": False, "state": "error", "artifact_path": None,
                        "error": r.stderr[:200]}
            import json
            data = json.loads(r.stdout)
            state = data.get("state", "UNKNOWN")
            return {"ready": state == "MERGED", "state": state,
                    "artifact_path": None, "error": None}
        except Exception as exc:
            return {"ready": False, "state": "error", "artifact_path": None, "error": str(exc)}

    # ── cmd:<command> ─────────────────────────────────────────────────────────
    if watch.startswith("cmd:"):
        cmd = watch[len("cmd:"):]
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, timeout=10)
            ready = r.returncode == 0
            return {"ready": ready, "state": "exit0" if ready else f"exit{r.returncode}",
                    "artifact_path": None, "error": None}
        except Exception as exc:
            return {"ready": False, "state": "error", "artifact_path": None, "error": str(exc)}

    # ── url:<url> ─────────────────────────────────────────────────────────────
    if watch.startswith("url:"):
        url = watch[len("url:"):]
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=10) as resp:
                ready = resp.status < 400
            return {"ready": ready, "state": f"http{resp.status}",
                    "artifact_path": None, "error": None}
        except Exception as exc:
            return {"ready": False, "state": "error", "artifact_path": None, "error": str(exc)}

    # ── wandb:<run-id> — W&B run terminal-state resolver (SR-WB) ─────────────
    # Uses the wandb SDK (import-guarded). If wandb is not installed, the
    # predicate resolves to not-ready/error — it NEVER crashes the background
    # poller with an ImportError traceback.
    #
    # Terminal states (ready=True — all wake the waiter, D-WB-4):
    #   finished / failed / crashed / killed / preempted / preempting
    # Non-terminal (ready=False): running / pending
    #
    # The state string is passed through in result["state"] so SR-RETRY can key
    # retry off failure state without re-querying.
    #
    # Auth: EnvSecretStore.get("wandb-api-key") → WANDB_API_KEY env → keyring.
    # Key exported to env so SDK picks it up via its native env-var path.
    if watch.startswith("wandb:"):
        run_id = watch[len("wandb:"):]
        if not run_id.strip():
            return {
                "ready": False,
                "state": "invalid-run-id",
                "artifact_path": None,
                "error": "wandb: watch requires a non-empty run id",
            }

        # SDK import guard — must NOT crash the background poller
        try:
            import wandb as _wandb  # type: ignore[import]
        except ImportError:
            return {
                "ready": False,
                "state": "sdk-unavailable",
                "artifact_path": None,
                "error": "wandb SDK not installed — pip install wandb",
            }

        # Auth — EnvSecretStore (env var first, then keyring, then KeyError)
        try:
            from .adapters.base import EnvSecretStore as _EnvSecretStore
            _store = _EnvSecretStore()
            api_key = _store.get("wandb-api-key")
            # Export for the SDK (its native env-var path)
            os.environ["WANDB_API_KEY"] = api_key
        except KeyError as exc:
            return {
                "ready": False,
                "state": "auth-error",
                "artifact_path": None,
                "error": str(exc),
            }
        except Exception as exc:
            return {
                "ready": False,
                "state": "auth-error",
                "artifact_path": None,
                "error": f"cannot resolve W&B API key: {exc}",
            }

        # Parse run id into (entity, project, run_name)
        try:
            from .wandb_pull import parse_run_id as _parse_run_id
            entity, project, run_name = _parse_run_id(run_id)
        except ValueError as exc:
            return {
                "ready": False,
                "state": "invalid-run-id",
                "artifact_path": None,
                "error": str(exc),
            }

        # Fetch run state via SDK
        try:
            _api = _wandb.Api()
            _run = _api.run(f"{entity}/{project}/{run_name}")
            state_str = (_run.state or "unknown").lower()
        except Exception as exc:
            return {
                "ready": False,
                "state": "error",
                "artifact_path": None,
                "error": f"W&B API error: {exc}",
            }

        _WANDB_TERMINAL = frozenset({
            "finished", "failed", "crashed", "killed", "preempted", "preempting",
        })
        ready = state_str in _WANDB_TERMINAL
        return {
            "ready": ready,
            "state": state_str,
            "artifact_path": None,
            "error": None,
        }

    # ── unknown ───────────────────────────────────────────────────────────────
    return {
        "ready": False,
        "state": "unknown-watch-source",
        "artifact_path": None,
        "error": f"unknown watch source: {watch!r}",
    }


# ---------------------------------------------------------------------------
# Background poller script
# ---------------------------------------------------------------------------

_POLLER_SCRIPT_TEMPLATE = """\
#!/usr/bin/env python3
# Auto-generated by rv wait-for — do not edit.
# Polls <watch> and fires <then_cmd> on resolution.

import os
import sys
import time
import json

watch = {watch!r}
then_cmd = {then_cmd!r}
interval_secs = {interval_secs!r}
timeout_secs = {timeout_secs!r}
registered_ts = {registered_ts!r}
log_path = {log_path!r}

# Import the resolver from research_vault
try:
    sys.path.insert(0, {package_path!r})
    from research_vault.wait_for import resolve_watch
except ImportError as e:
    print(f"[rv wait-for] cannot import resolver: {{e}}", file=sys.stderr)
    sys.exit(1)

start = time.time()

def _log(msg):
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    line = f"[{{ts}}] {{msg}}"
    print(line, flush=True)
    if log_path:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\\n")
        except OSError:
            pass

_log(f"wait-for: watching {{watch!r}}")

while True:
    elapsed = time.time() - start
    if timeout_secs and elapsed >= timeout_secs:
        _log(f"wait-for: TIMEOUT after {{elapsed:.0f}}s — watch={{watch!r}}")
        sys.exit(2)

    result = resolve_watch(watch, registered_ts=registered_ts)
    if result["ready"]:
        _log(f"wait-for: RESOLVED (state={{result['state']!r}}) — firing --then")
        if then_cmd:
            rc = os.system(then_cmd)
            _log(f"wait-for: --then exited with {{rc}}")
        else:
            _log("wait-for: no --then command configured")
        sys.exit(0)

    if result.get("error"):
        _log(f"wait-for: error polling watch: {{result['error']}}")

    _log(f"wait-for: not ready (state={{result['state']!r}}) — sleeping {{interval_secs}}s")
    time.sleep(interval_secs)
"""


def _launch_background_poller(
    watch: str,
    then_cmd: str,
    timeout_secs: int,
    interval_secs: int,
    registered_ts: float,
    log_path: str,
) -> None:
    """Write the poller script to a temp file and launch it detached."""
    import tempfile

    # Locate the package so the poller can import resolve_watch
    package_path = str(Path(__file__).parent.parent.parent)  # src/ dir

    script = _POLLER_SCRIPT_TEMPLATE.format(
        watch=watch,
        then_cmd=then_cmd,
        interval_secs=interval_secs,
        timeout_secs=timeout_secs,
        registered_ts=registered_ts,
        log_path=log_path,
        package_path=package_path,
    )

    fd, script_path = tempfile.mkstemp(suffix=".py", prefix="rv_wait_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)
        os.chmod(script_path, 0o755)
    except Exception:
        try:
            os.unlink(script_path)
        except OSError:
            pass
        raise

    # Launch detached (POSIX: double-fork pattern via nohup or setsid)
    if sys.platform == "win32":
        # Windows: use DETACHED_PROCESS
        import subprocess as sp
        DETACHED_PROCESS = 0x00000008
        sp.Popen(
            [sys.executable, script_path],
            creationflags=DETACHED_PROCESS,
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        # POSIX: fork + setsid so the poller survives the parent shell
        pid = os.fork()
        if pid == 0:
            # Child: become a session leader
            os.setsid()
            # Redirect stdout/stderr of the grandchild to the log
            pid2 = os.fork()
            if pid2 == 0:
                # Grandchild: exec the poller
                try:
                    if log_path:
                        log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
                        os.dup2(log_fd, 1)
                        os.dup2(log_fd, 2)
                        os.close(log_fd)
                    os.execv(sys.executable, [sys.executable, script_path])
                except Exception:
                    os._exit(1)
            else:
                os._exit(0)
        else:
            # Parent: wait for the intermediate child then return
            os.waitpid(pid, 0)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``wait-for`` verb.

    When to use: ``rv wait-for <watch>`` to background-poll a watch expression
    and fire --then on resolution. The caller returns immediately.
    SR-3's DAG afterok composes the resolve_watch resolver directly.
    """
    desc = (
        "Background-wait for a watch expression to resolve, then fire --then. "
        "The caller returns immediately — no sleep-looping. "
        "SR-3's DAG afterok composes the resolver grammar directly."
    )
    if parent is not None:
        p = parent.add_parser(
            "wait-for",
            help="Background-wait for a watch condition, then fire --then.",
            description=desc,
        )
    else:
        p = argparse.ArgumentParser(prog="rv wait-for", description=desc)

    p.add_argument(
        "watch",
        help=(
            "Watch expression: artifact:<path>, artifact:<path>+fresh, "
            "sacct:<jobid>, pr:<repo>#<n>, cmd:<cmd>, url:<url>"
        ),
    )
    p.add_argument(
        "--then", dest="then_cmd", default="",
        metavar="CMD",
        help="Shell command to run on resolution.",
    )
    p.add_argument(
        "--timeout", type=int, default=0,
        metavar="SECS",
        help="Timeout in seconds (0 = no timeout).",
    )
    p.add_argument(
        "--interval", type=int, default=30,
        metavar="SECS",
        help="Poll interval in seconds (default 30).",
    )
    p.add_argument(
        "--log", default="",
        metavar="PATH",
        help="Path to write the poller log (default: stdout of the background shell).",
    )
    p.add_argument(
        "--sync", action="store_true",
        help=(
            "Run the poller synchronously (blocking) instead of in the background. "
            "Useful for testing."
        ),
    )

    return p


def run(args: argparse.Namespace) -> int:
    """Run the wait-for command. Returns 0 immediately (background mode).

    The caller RETURNS IMMEDIATELY. A detached background shell polls
    the watch and fires --then on resolution.
    """
    watch = args.watch
    then_cmd = getattr(args, "then_cmd", "") or ""
    timeout_secs = getattr(args, "timeout", 0) or 0
    interval_secs = getattr(args, "interval", 30) or 30
    log_path = getattr(args, "log", "") or ""
    sync_mode = getattr(args, "sync", False)
    registered_ts = time.time()

    # Validate watch syntax only — do NOT execute cmd:/url: pre-flight here,
    # as that would cause a synchronous side-effect before the background poller.
    # Only check that the watch prefix is recognized.
    _KNOWN_PREFIXES = (
        "artifact:", "sched:", "sacct:", "pr:", "cmd:", "url:",
        "note:",      # OKF note resolver (notes_root-aware)
        "dataset:",   # SR-8: dataset provenance resolver (exists + hash + location)
        "wandb:",     # SR-WB: W&B run terminal-state resolver (wandb SDK, import-guarded)
    )
    if not any(watch.startswith(p) for p in _KNOWN_PREFIXES):
        print(f"rv wait-for: unknown watch source: {watch!r}", file=sys.stderr)
        return 1

    if sync_mode:
        # Blocking mode for tests / debugging — propagate the exit code
        return _run_sync(watch, then_cmd, timeout_secs, interval_secs, registered_ts, log_path)

    # Background mode: launch a detached poller shell and return immediately
    try:
        _launch_background_poller(
            watch=watch,
            then_cmd=then_cmd,
            timeout_secs=timeout_secs,
            interval_secs=interval_secs,
            registered_ts=registered_ts,
            log_path=log_path,
        )
    except Exception as e:
        print(f"rv wait-for: failed to launch background poller: {e}", file=sys.stderr)
        return 1

    print(f"rv wait-for: background poller launched for {watch!r}")
    if then_cmd:
        print(f"  --then: {then_cmd!r}")
    if timeout_secs:
        print(f"  timeout: {timeout_secs}s")
    print(f"  interval: {interval_secs}s")
    if log_path:
        print(f"  log: {log_path}")
    print("Returning immediately (poller runs in the background).")
    return 0


def _run_sync(
    watch: str,
    then_cmd: str,
    timeout_secs: int,
    interval_secs: int,
    registered_ts: float,
    log_path: str,
) -> int:
    """Run the poller synchronously (blocking). For tests and --sync mode."""
    start = time.time()
    while True:
        elapsed = time.time() - start
        if timeout_secs and elapsed >= timeout_secs:
            print(f"rv wait-for: TIMEOUT after {elapsed:.0f}s", file=sys.stderr)
            return 2

        result = resolve_watch(watch, registered_ts=registered_ts)
        if result["ready"]:
            print(f"rv wait-for: RESOLVED (state={result['state']!r})")
            if then_cmd:
                rc = os.system(then_cmd)
                print(f"rv wait-for: --then exited with {rc}")
            return 0

        time.sleep(interval_secs)
