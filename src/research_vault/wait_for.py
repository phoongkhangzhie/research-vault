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
  sacct:<jobid>            — SLURM job reaches a terminal state
  pr:<owner/repo>#<n>      — PR state reaches MERGED
  cmd:<shell-cmd>          — shell command exits 0
  url:<url>                — HTTP HEAD returns < 400

Verify modifiers (appended with '+'):
  fresh                    — shorthand for fresh_since_registered (written after now)

Resolver grammar is importable by SR-3's DAG for afterok composition.

Stdlib only. The resolver grammar is a subset of vault's pollers/resolver.py
re-implemented portably (no SSH cluster reads — SLURM check is stubbed for
portability; the full sacct resolver ships with the SLURM backend in a later SR).
"""
from __future__ import annotations

import argparse
import datetime
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

    # ── sacct:<jobid> ─────────────────────────────────────────────────────────
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
            terminal_states = frozenset({
                "COMPLETED", "FAILED", "CANCELLED", "TIMEOUT",
                "NODE_FAIL", "OUT_OF_MEMORY", "PREEMPTED", "BOOT_FAIL",
            })
            for line in result.stdout.splitlines():
                parts = line.strip().split("|")
                if len(parts) >= 2:
                    job_col = parts[0].strip()
                    state = parts[1].strip().upper().split()[0]
                    if job_col.split(".")[0] == str(job_id) or job_col == str(job_id):
                        terminal = state in terminal_states
                        return {
                            "ready": terminal,
                            "state": state,
                            "artifact_path": None,
                            "error": None,
                        }
            return {"ready": False, "state": "pending", "artifact_path": None, "error": None}
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
    _KNOWN_PREFIXES = ("artifact:", "sacct:", "pr:", "cmd:", "url:")
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
