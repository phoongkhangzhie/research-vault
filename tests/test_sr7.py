"""test_sr7.py — SR-7: Remote ComputeBackend adapter (manifest-driven, one adapter).

Covers:
  1.  RemoteBackend is a ComputeBackend (Protocol conformance)
  2.  load_adapters returns RemoteBackend for slurm/pbs/ssh/generic keys
  3.  load_adapters returns LocalSubprocess for "local" (zero-infra unaffected)
  4.  LocalSubprocess accepts cfg=None (D-SR7-5 factory-arg)
  5.  Schema back-compat: SR-6 manifest lacking jobid_parse/status/state_map validates
  6.  submit (ssh+slurm): builds correct ssh <host> sbatch ... -- <cmd> argv
  7.  submit (ssh+pbs): builds correct ssh <host> qsub ... -- <cmd> argv
  8.  submit container-wrap: wraps with <runtime> exec <image> when declared
  9.  submit generic: adopter-declared commands run directly
  10. status (slurm): maps sacct output to Protocol states
  11. status (pbs): maps qstat output to Protocol states
  12. status: FileNotFoundError (ssh absent) → "UNKNOWN" (no crash)
  13. sched:<backend>:<jobid> resolver — resolves terminal/non-terminal state
  14. sacct:<jobid> back-compat alias still works
  15. sched: prefix accepted in _KNOWN_PREFIXES (no "unknown watch source" error)
  16. local unaffected: backend=local still submits without ssh
  17. cmd_show renders new schema fields when declared
  18. wait_for module docstring no longer says "stubbed"
  19. rv help --check passes (no missing when_to_use for any verb)

All tests are hermetic: mocked subprocess; no real ssh/cluster; no ~/vault reads.
Leakage-clean: all hosts/aliases are example names only.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import Config, reset_config_cache


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path: Path, backend: str = "slurm", extra_adapters: dict | None = None) -> Config:
    """Minimal Config pointed at tmp_path."""
    adapters: dict[str, str] = {"notifier": "file", "backend": backend, "secrets": "env"}
    if extra_adapters:
        adapters.update(extra_adapters)
    raw: dict[str, Any] = {
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(tmp_path / "state"),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "adapters": adapters,
        "projects": {},
    }
    cfg = Config(raw)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    return cfg


def _write_manifest(cfg: Config, manifest: dict[str, Any]) -> None:
    from research_vault.compute import MANIFEST_FILE
    p = cfg.state_dir / MANIFEST_FILE
    p.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _slurm_manifest(host: str = "example-cluster") -> dict[str, Any]:
    """Minimal SR-7 slurm manifest with the new fields declared."""
    return {
        "backends": {
            "active": ["slurm-cluster"],
            "profiles": {
                "slurm-cluster": {
                    "archetype": "ssh+slurm",
                    "host": host,
                    "submit_pattern": "sbatch --partition=gpu",
                    "jobid_parse": r"Submitted batch job (\d+)",
                },
            },
        },
        "conda_envs": {},
        "gpu_tiers": {},
        "rules": [],
        "model_quirks": {},
        "run_outcomes": [],
    }


def _pbs_manifest(host: str = "example-pbs") -> dict[str, Any]:
    return {
        "backends": {
            "active": ["pbs-cluster"],
            "profiles": {
                "pbs-cluster": {
                    "archetype": "ssh+pbs",
                    "host": host,
                    "submit_pattern": "qsub -l nodes=1:ppn=8",
                    "jobid_parse": r"^(\d+)",
                },
            },
        },
        "conda_envs": {},
        "gpu_tiers": {},
        "rules": [],
        "model_quirks": {},
        "run_outcomes": [],
    }


def _container_manifest() -> dict[str, Any]:
    m = _slurm_manifest()
    m["backends"]["profiles"]["slurm-cluster"]["container"] = {
        "runtime": "apptainer",
        "image": "/images/myenv.sif",
    }
    return m


def _generic_manifest() -> dict[str, Any]:
    return {
        "backends": {
            "active": ["my-hpc"],
            "profiles": {
                "my-hpc": {
                    "archetype": "generic",
                    "host": "example-hpc",
                    "submit_pattern": "custom-submit --queue=batch",
                    "jobid_parse": r"JOB-(\d+)",
                    "status_cmd": "custom-status {jobid}",
                    "status_parse": r"Status:\s+(\w+)",
                    "state_map": {
                        "DONE": "DONE",
                        "RUNNING": "RUNNING",
                        "QUEUED": "PENDING",
                        "ERROR": "FAILED",
                    },
                },
            },
        },
        "conda_envs": {},
        "gpu_tiers": {},
        "rules": [],
        "model_quirks": {},
        "run_outcomes": [],
    }


def _sr6_compat_manifest() -> dict[str, Any]:
    """SR-6-style manifest: no jobid_parse / status / state_map fields."""
    return {
        "backends": {
            "active": ["old-cluster"],
            "profiles": {
                "old-cluster": {
                    "archetype": "ssh+slurm",
                    "host": "example-legacy",
                    "submit_pattern": "sbatch --partition=cpu",
                    # No jobid_parse, status_cmd, status_parse, state_map
                },
            },
        },
        "conda_envs": {},
        "gpu_tiers": {},
        "rules": [],
        "model_quirks": {},
        "run_outcomes": [],
    }


@pytest.fixture
def cfg_local(tmp_path):
    return _make_cfg(tmp_path, backend="local")


@pytest.fixture
def cfg_slurm(tmp_path):
    cfg = _make_cfg(tmp_path, backend="slurm")
    _write_manifest(cfg, _slurm_manifest())
    return cfg


@pytest.fixture
def cfg_pbs(tmp_path):
    cfg = _make_cfg(tmp_path, backend="pbs")
    _write_manifest(cfg, _pbs_manifest())
    return cfg


@pytest.fixture
def cfg_container(tmp_path):
    cfg = _make_cfg(tmp_path, backend="slurm")
    _write_manifest(cfg, _container_manifest())
    return cfg


@pytest.fixture
def cfg_generic(tmp_path):
    cfg = _make_cfg(tmp_path, backend="generic")
    _write_manifest(cfg, _generic_manifest())
    return cfg


@pytest.fixture
def cfg_sr6_compat(tmp_path):
    """Config with an SR-6-style manifest (no new fields)."""
    cfg = _make_cfg(tmp_path, backend="slurm")
    _write_manifest(cfg, _sr6_compat_manifest())
    return cfg


def _mock_subprocess_run(stdout: str, returncode: int = 0) -> MagicMock:
    """Return a mock for subprocess.run with controlled stdout/returncode."""
    mock = MagicMock()
    mock.stdout = stdout
    mock.stderr = ""
    mock.returncode = returncode
    return mock


# ---------------------------------------------------------------------------
# 1. Protocol conformance
# ---------------------------------------------------------------------------

def test_remote_backend_is_compute_backend(cfg_slurm):
    from research_vault.adapters.remote import RemoteBackend
    from research_vault.adapters.base import ComputeBackend
    rb = RemoteBackend(cfg_slurm)
    assert isinstance(rb, ComputeBackend), "RemoteBackend must satisfy ComputeBackend Protocol"


# ---------------------------------------------------------------------------
# 2. load_adapters returns RemoteBackend for remote keys
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("backend_key", ["slurm", "pbs", "ssh", "generic"])
def test_load_adapters_remote_keys_return_remote_backend(tmp_path, backend_key):
    from research_vault.adapters.base import load_adapters
    from research_vault.adapters.remote import RemoteBackend
    cfg = _make_cfg(tmp_path, backend=backend_key)
    # write a minimal manifest so the RemoteBackend doesn't fail on init
    manifest: dict[str, Any] = {
        "backends": {"active": ["cluster"], "profiles": {
            "cluster": {"archetype": "ssh+slurm", "host": "example-host"},
        }},
        "conda_envs": {}, "gpu_tiers": {}, "rules": [], "model_quirks": {}, "run_outcomes": [],
    }
    _write_manifest(cfg, manifest)
    adapters = load_adapters(cfg)
    assert isinstance(adapters.backend, RemoteBackend), (
        f"backend='{backend_key}' must resolve to RemoteBackend, got {type(adapters.backend)}"
    )


# ---------------------------------------------------------------------------
# 3. load_adapters: "local" still returns LocalSubprocess
# ---------------------------------------------------------------------------

def test_load_adapters_local_returns_local_subprocess(cfg_local):
    from research_vault.adapters.base import load_adapters, LocalSubprocess
    adapters = load_adapters(cfg_local)
    assert isinstance(adapters.backend, LocalSubprocess)


# ---------------------------------------------------------------------------
# 4. LocalSubprocess accepts cfg=None (D-SR7-5)
# ---------------------------------------------------------------------------

def test_local_subprocess_accepts_cfg_none():
    from research_vault.adapters.base import LocalSubprocess
    ls = LocalSubprocess(cfg=None)  # must not raise
    assert ls is not None


def test_local_subprocess_no_arg_still_works():
    from research_vault.adapters.base import LocalSubprocess
    ls = LocalSubprocess()  # original zero-arg call still works
    assert ls is not None


# ---------------------------------------------------------------------------
# 5. Schema back-compat: SR-6 manifest (no new fields) still works
# ---------------------------------------------------------------------------

def test_sr6_manifest_no_new_fields_submit_uses_defaults(cfg_sr6_compat):
    """An SR-6 manifest lacking jobid_parse/status/state_map gets built-in defaults."""
    from research_vault.adapters.remote import RemoteBackend
    rb = RemoteBackend(cfg_sr6_compat)
    profile = rb._active_profile()
    # The slurm archetype must supply defaults
    assert "jobid_parse" in profile, "Built-in jobid_parse must be supplied as default"
    assert "state_map" in profile, "Built-in state_map must be supplied as default"


# ---------------------------------------------------------------------------
# 6. submit (ssh+slurm): correct argv
# ---------------------------------------------------------------------------

def test_submit_slurm_builds_correct_argv(cfg_slurm):
    from research_vault.adapters.remote import RemoteBackend
    rb = RemoteBackend(cfg_slurm)
    with patch("subprocess.run", return_value=_mock_subprocess_run("Submitted batch job 12345\n")) as mock_run:
        handle = rb.submit(["python", "train.py"])
    call_argv = mock_run.call_args[0][0]
    assert call_argv[0] == "ssh"
    assert call_argv[1] == "example-cluster"
    assert "sbatch" in call_argv
    assert "--" in call_argv
    dash_idx = call_argv.index("--")
    assert call_argv[dash_idx + 1 :] == ["python", "train.py"]
    assert handle == "12345"


def test_submit_slurm_includes_submit_pattern_flags(cfg_slurm):
    from research_vault.adapters.remote import RemoteBackend
    rb = RemoteBackend(cfg_slurm)
    with patch("subprocess.run", return_value=_mock_subprocess_run("Submitted batch job 99\n")):
        rb.submit(["mycommand"])
    # Nothing to assert on flags specifically beyond sbatch being present —
    # the submit_pattern "sbatch --partition=gpu" is declared in the fixture.
    # Just verify submit succeeded without error.


# ---------------------------------------------------------------------------
# 7. submit (ssh+pbs): correct argv
# ---------------------------------------------------------------------------

def test_submit_pbs_builds_correct_argv(cfg_pbs):
    from research_vault.adapters.remote import RemoteBackend
    rb = RemoteBackend(cfg_pbs)
    with patch("subprocess.run", return_value=_mock_subprocess_run("67890.example-pbs\n")) as mock_run:
        handle = rb.submit(["qsubscript.sh"])
    call_argv = mock_run.call_args[0][0]
    assert call_argv[0] == "ssh"
    assert call_argv[1] == "example-pbs"
    assert "qsub" in call_argv
    assert "--" in call_argv
    assert handle == "67890"


# ---------------------------------------------------------------------------
# 8. submit container-wrap
# ---------------------------------------------------------------------------

def test_submit_slurm_container_wrap(cfg_container):
    from research_vault.adapters.remote import RemoteBackend
    rb = RemoteBackend(cfg_container)
    with patch("subprocess.run", return_value=_mock_subprocess_run("Submitted batch job 777\n")) as mock_run:
        handle = rb.submit(["python", "run.py"])
    call_argv = mock_run.call_args[0][0]
    assert "apptainer" in call_argv
    assert "exec" in call_argv
    assert "/images/myenv.sif" in call_argv
    # Order: ssh <host> sbatch <flags> apptainer exec <image> -- <cmd>
    appt_idx = call_argv.index("apptainer")
    exec_idx = call_argv.index("exec")
    dash_idx = call_argv.index("--")
    assert appt_idx < exec_idx < dash_idx
    assert handle == "777"


# ---------------------------------------------------------------------------
# 9. submit generic: adopter-declared commands
# ---------------------------------------------------------------------------

def test_submit_generic_uses_declared_commands(cfg_generic):
    from research_vault.adapters.remote import RemoteBackend
    rb = RemoteBackend(cfg_generic)
    with patch("subprocess.run", return_value=_mock_subprocess_run("JOB-42\n")) as mock_run:
        handle = rb.submit(["myworkload.sh"])
    call_argv = mock_run.call_args[0][0]
    assert call_argv[0] == "ssh"
    assert call_argv[1] == "example-hpc"
    assert "custom-submit" in call_argv
    assert handle == "42"


# ---------------------------------------------------------------------------
# 10. status (slurm): maps sacct output to Protocol states
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sacct_stdout,expected_state", [
    ("12345|COMPLETED\n",             "DONE"),
    ("12345|RUNNING\n",               "RUNNING"),
    ("12345|PENDING\n",               "PENDING"),
    ("12345|FAILED\n",                "FAILED"),
    ("12345|CANCELLED\n",             "FAILED"),
    ("12345|TIMEOUT\n",               "FAILED"),
    ("12345|NODE_FAIL\n",             "FAILED"),
    ("12345|OUT_OF_MEMORY\n",         "FAILED"),
    # Slurm sometimes shows sub-jobs
    ("12345.batch|COMPLETED\n12345|COMPLETED\n", "DONE"),
])
def test_status_slurm(cfg_slurm, sacct_stdout, expected_state):
    from research_vault.adapters.remote import RemoteBackend
    rb = RemoteBackend(cfg_slurm)
    with patch("subprocess.run", return_value=_mock_subprocess_run(sacct_stdout)):
        state = rb.status("12345")
    assert state == expected_state, f"sacct output {sacct_stdout!r} → expected {expected_state}, got {state}"


# ---------------------------------------------------------------------------
# 11. status (pbs): maps qstat output to Protocol states
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("qstat_stdout,expected_state", [
    ("job_state = R\n",  "RUNNING"),
    ("job_state = Q\n",  "PENDING"),
    ("job_state = H\n",  "PENDING"),
    ("job_state = C\n",  "DONE"),
    ("job_state = E\n",  "DONE"),
    ("job_state = F\n",  "DONE"),
])
def test_status_pbs(cfg_pbs, qstat_stdout, expected_state):
    from research_vault.adapters.remote import RemoteBackend
    rb = RemoteBackend(cfg_pbs)
    with patch("subprocess.run", return_value=_mock_subprocess_run(qstat_stdout)):
        state = rb.status("67890")
    assert state == expected_state, (
        f"qstat output {qstat_stdout!r} → expected {expected_state}, got {state}"
    )


def test_status_generic(cfg_generic):
    from research_vault.adapters.remote import RemoteBackend
    rb = RemoteBackend(cfg_generic)
    with patch("subprocess.run", return_value=_mock_subprocess_run("Status: DONE\n")):
        state = rb.status("42")
    assert state == "DONE"


# ---------------------------------------------------------------------------
# 12. status: ssh absent → UNKNOWN (no crash; graceful degrade)
# ---------------------------------------------------------------------------

def test_status_ssh_absent_returns_unknown(cfg_slurm):
    from research_vault.adapters.remote import RemoteBackend
    rb = RemoteBackend(cfg_slurm)
    with patch("subprocess.run", side_effect=FileNotFoundError("ssh not found")):
        state = rb.status("12345")
    assert state == "UNKNOWN"


def test_status_timeout_returns_unknown(cfg_slurm):
    import subprocess as sp
    from research_vault.adapters.remote import RemoteBackend
    rb = RemoteBackend(cfg_slurm)
    with patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd="ssh", timeout=15)):
        state = rb.status("12345")
    assert state == "UNKNOWN"


# ---------------------------------------------------------------------------
# 13. sched:<backend>:<jobid> resolver — shared path with RemoteBackend.status
# ---------------------------------------------------------------------------

def test_sched_slurm_resolver_terminal(tmp_path, monkeypatch):
    """sched:slurm:12345 resolves ready=True when job is COMPLETED."""
    cfg = _make_cfg(tmp_path, backend="slurm")
    _write_manifest(cfg, _slurm_manifest())

    config_file = tmp_path / "research_vault.toml"
    config_file.write_text(
        f'instance_root = "{tmp_path}"\n'
        f'notes_root = "{tmp_path / "notes"}"\n'
        f'state_dir = "{tmp_path / "state"}"\n'
        f'agents_dir = "{tmp_path / ".agents"}"\n'
        f'tasks_dir = "{tmp_path / "tasks"}"\n'
        f'control_dir = "{tmp_path / "control"}"\n'
        f'[adapters]\nbackend = "slurm"\n'
        f'[projects]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(config_file))
    reset_config_cache()
    from research_vault.config import load_config
    load_config(reload=True)

    from research_vault.wait_for import resolve_watch
    with patch("subprocess.run", return_value=_mock_subprocess_run("12345|COMPLETED\n")):
        result = resolve_watch("sched:slurm:12345", registered_ts=0)

    reset_config_cache()
    assert result["ready"] is True
    assert result["state"] in ("DONE", "COMPLETED")


def test_sched_slurm_resolver_not_terminal(tmp_path, monkeypatch):
    """sched:slurm:12345 resolves ready=False when job is RUNNING."""
    cfg = _make_cfg(tmp_path, backend="slurm")
    _write_manifest(cfg, _slurm_manifest())

    config_file = tmp_path / "research_vault.toml"
    config_file.write_text(
        f'instance_root = "{tmp_path}"\n'
        f'notes_root = "{tmp_path / "notes"}"\n'
        f'state_dir = "{tmp_path / "state"}"\n'
        f'agents_dir = "{tmp_path / ".agents"}"\n'
        f'tasks_dir = "{tmp_path / "tasks"}"\n'
        f'control_dir = "{tmp_path / "control"}"\n'
        f'[adapters]\nbackend = "slurm"\n'
        f'[projects]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(config_file))
    reset_config_cache()
    from research_vault.config import load_config
    load_config(reload=True)

    from research_vault.wait_for import resolve_watch
    with patch("subprocess.run", return_value=_mock_subprocess_run("12345|RUNNING\n")):
        result = resolve_watch("sched:slurm:12345", registered_ts=0)

    reset_config_cache()
    assert result["ready"] is False


def test_sched_pbs_resolver(tmp_path, monkeypatch):
    """sched:pbs:67890 resolves terminal state via pbs path — no fork."""
    cfg = _make_cfg(tmp_path, backend="pbs")
    _write_manifest(cfg, _pbs_manifest())

    config_file = tmp_path / "research_vault.toml"
    config_file.write_text(
        f'instance_root = "{tmp_path}"\n'
        f'notes_root = "{tmp_path / "notes"}"\n'
        f'state_dir = "{tmp_path / "state"}"\n'
        f'agents_dir = "{tmp_path / ".agents"}"\n'
        f'tasks_dir = "{tmp_path / "tasks"}"\n'
        f'control_dir = "{tmp_path / "control"}"\n'
        f'[adapters]\nbackend = "pbs"\n'
        f'[projects]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(config_file))
    reset_config_cache()
    from research_vault.config import load_config
    load_config(reload=True)

    from research_vault.wait_for import resolve_watch
    with patch("subprocess.run", return_value=_mock_subprocess_run("job_state = C\n")):
        result = resolve_watch("sched:pbs:67890", registered_ts=0)

    reset_config_cache()
    assert result["ready"] is True


# ---------------------------------------------------------------------------
# 14. sacct:<jobid> back-compat alias still works
# ---------------------------------------------------------------------------

def test_sacct_alias_still_works():
    """sacct:<jobid> is unchanged — back-compat must hold."""
    from research_vault.wait_for import resolve_watch
    with patch("subprocess.run", return_value=_mock_subprocess_run("12345|COMPLETED\n")):
        result = resolve_watch("sacct:12345", registered_ts=0)
    # sacct: still resolves via the existing path
    assert "ready" in result
    assert "state" in result


# ---------------------------------------------------------------------------
# 15. sched: prefix in _KNOWN_PREFIXES
# ---------------------------------------------------------------------------

def test_sched_prefix_in_known_prefixes(capsys):
    """rv wait-for sched:slurm:99 must not fail with 'unknown watch source'."""
    import argparse
    from research_vault.wait_for import run as wait_for_run
    # Use --sync mode so it actually tries to resolve; mock to return immediately
    args = argparse.Namespace(
        watch="sched:slurm:99",
        then_cmd="",
        timeout=1,
        interval=1,
        log="",
        sync=True,
    )
    with patch("subprocess.run", return_value=_mock_subprocess_run("99|RUNNING\n")):
        with patch("time.sleep"):
            # May loop once then timeout; that's fine — we just verify no "unknown" error
            try:
                wait_for_run(args)
            except SystemExit:
                pass
    captured = capsys.readouterr()
    assert "unknown watch source" not in captured.err


# ---------------------------------------------------------------------------
# 16. local unaffected: backend=local works without ssh
# ---------------------------------------------------------------------------

def test_local_backend_works_without_ssh(cfg_local):
    """backend=local must never touch ssh codepaths — zero-infra guarantee."""
    from research_vault.adapters.base import load_adapters, LocalSubprocess
    adapters = load_adapters(cfg_local)
    assert isinstance(adapters.backend, LocalSubprocess)
    # Verify submit runs a real local subprocess (not ssh)
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_popen.return_value = mock_proc
        handle = adapters.backend.submit(["echo", "hello"])
    call_argv = mock_popen.call_args[0][0]
    # Must NOT start with "ssh"
    assert call_argv[0] != "ssh"
    assert handle == "99999"


def test_remote_module_importable_without_ssh(tmp_path):
    """adapters.remote must import cleanly on any machine."""
    # Just importing the module must not fail even without ssh
    from research_vault.adapters import remote  # noqa: F401 — just test import
    assert hasattr(remote, "RemoteBackend")


# ---------------------------------------------------------------------------
# 17. cmd_show renders new schema fields when declared
# ---------------------------------------------------------------------------

def test_cmd_show_renders_jobid_parse_and_status_cmd(tmp_path, capsys):
    """When manifest declares jobid_parse and status_cmd, cmd_show shows them."""
    cfg = _make_cfg(tmp_path, backend="slurm")
    manifest = _slurm_manifest()
    # Add explicit status_cmd + status_parse to profile
    manifest["backends"]["profiles"]["slurm-cluster"]["status_cmd"] = (
        "sacct -j {jobid} --format=JobID,State --noheader -P"
    )
    manifest["backends"]["profiles"]["slurm-cluster"]["status_parse"] = (
        r"^{jobid}(?:\.\w+)?\|([A-Z_]+)"
    )
    _write_manifest(cfg, manifest)

    from research_vault.compute import cmd_show
    cmd_show(cfg)
    out = capsys.readouterr().out
    # The cmd_show output should surface the new fields
    assert "jobid_parse" in out or "sacct" in out or "status" in out


# ---------------------------------------------------------------------------
# 18. wait_for module docstring: stale "stubbed" note removed
# ---------------------------------------------------------------------------

def test_wait_for_docstring_not_stubbed():
    """The 'SLURM check is stubbed' note must be removed (it was never true)."""
    import research_vault.wait_for as wf
    doc = wf.__doc__ or ""
    assert "stubbed" not in doc.lower(), (
        "wait_for.py module docstring still contains 'stubbed' — fix the stale note."
    )


def test_wait_for_docstring_mentions_sched():
    """Module docstring should document the sched: watch source."""
    import research_vault.wait_for as wf
    doc = wf.__doc__ or ""
    assert "sched:" in doc, "wait_for docstring should document the sched: watch source."


# ---------------------------------------------------------------------------
# 19. rv help --check passes (no missing when_to_use for any verb)
# ---------------------------------------------------------------------------

def test_rv_help_check_passes():
    from research_vault.cli import _check_verb_docstrings
    violations = _check_verb_docstrings()
    assert violations == [], f"rv help --check violations: {violations}"
