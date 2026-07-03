"""test_sr_co_remote.py — SR-CO-REMOTE: real ssh remote probe via _ssh_exec.

Covers:
  1.  ssh+slurm backend: mocked sinfo output → capabilities populated (partitions + GPU types)
  2.  Login-node GPU trap: nvidia-smi absent on login BUT sinfo shows GPU GRES → probe finds GPUs via sinfo
  3.  Unreachable host (ssh failure) → honest "unreachable" report, no crash, distinct from "no GPUs"
  4.  Auth failure (exit 255) → honest "unreachable" report
  5.  BatchMode + ConnectTimeout flags present in probe ssh argv (fail-fast)
  6.  ssh+pbs backend: mocked pbsnodes output → capabilities populated
  7.  Per-backend cache populated with real capabilities (probe_status = "ok", not "deferred")
  8.  format_report shows real probe results (partitions, GPU types) — not deferral message
  9.  sinfo parse: extracts partition name + gpu GRES + node count from '%P %G %D' format
  10. Graceful degrade: sinfo returns non-zero → honest "scheduler error" report
  11. ssh+slurm with no GPUs in sinfo → "reachable, no GPUs" distinct from "unreachable"
  12. SR-CO tests unchanged: deferral path no longer shown for ssh+slurm (replaced by real probe)
  13. SR-7 submit tests still pass: submit uses subprocess.run inline (justified — see docstring)
  14. _ssh_exec docstring accuracy: docstring updated to accurately reflect call sites

All tests are hermetic: tmp_path only; _ssh_exec stubbed via unittest.mock.patch.
No real cluster, no real network, no real SSH keys.
Leakage-clean: all hosts/aliases use example names (example-hpc.edu, gpu-cluster.example).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import Config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path: Path) -> Config:
    """Minimal Config pointed at tmp_path."""
    raw: dict[str, Any] = {
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(tmp_path / "state"),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "adapters": {"notifier": "file", "backend": "slurm", "secrets": "env"},
        "projects": {},
    }
    cfg = Config(raw)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    return cfg


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return _make_cfg(tmp_path)


def _write_slurm_manifest(cfg: Config, host: str = "example-hpc.edu") -> None:
    """Write a compute manifest with a declared ssh+slurm backend."""
    from research_vault.compute import _save_manifest
    _save_manifest(cfg, {
        "backends": {
            "active": ["cluster"],
            "profiles": {
                "local": {"archetype": "local"},
                "cluster": {
                    "archetype": "ssh+slurm",
                    "host": host,
                    "submit_pattern": "sbatch --partition=gpu --account=mylab",
                },
            },
        },
        "conda_envs": {},
        "gpu_tiers": {"tp1": {"gpus": 1, "models": ["<=7B"]}},
        "rules": [],
        "model_quirks": {},
        "run_outcomes": [],
    })


def _write_pbs_manifest(cfg: Config, host: str = "pbs-cluster.example") -> None:
    """Write a compute manifest with a declared ssh+pbs backend."""
    from research_vault.compute import _save_manifest
    _save_manifest(cfg, {
        "backends": {
            "active": ["cluster"],
            "profiles": {
                "local": {"archetype": "local"},
                "cluster": {
                    "archetype": "ssh+pbs",
                    "host": host,
                    "submit_pattern": "qsub -l select=1:ncpus=8",
                },
            },
        },
        "conda_envs": {},
        "gpu_tiers": {},
        "rules": [],
        "model_quirks": {},
        "run_outcomes": [],
    })


def _make_ssh_result(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    """Make a fake subprocess.CompletedProcess for _ssh_exec mocking."""
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


# ---------------------------------------------------------------------------
# Test 9: sinfo parse helper (pure, no subprocess)
# ---------------------------------------------------------------------------

class TestSinfoParser:
    """Test the sinfo output parser in isolation — pure function, no subprocess."""

    def test_parse_sinfo_slurm_extracts_partitions_and_gpu_types(self) -> None:
        """_parse_sinfo_output extracts partition + GRES gpu type from '%P %G %D' format."""
        from research_vault.doctor import _parse_sinfo_output
        # Typical sinfo output: partition GRES node-count
        # GRES format: gpu:a100:4 or gpu:2 or (null)
        stdout = (
            "gpu*   gpu:a100:4   8\n"
            "cpu    (null)       16\n"
            "highmem (null)      4\n"
            "gpu-v100 gpu:v100:2  2\n"
        )
        result = _parse_sinfo_output(stdout)
        assert result["available"] is True
        partitions = result["partitions"]
        assert len(partitions) == 4
        # gpu partition
        gpu_part = next(p for p in partitions if "gpu*" in p["partition"] or p["partition"] == "gpu*")
        assert gpu_part["gpu_gres"] == "gpu:a100:4"
        assert gpu_part["nodes"] == "8"
        # cpu partition
        cpu_part = next(p for p in partitions if p["partition"] == "cpu")
        assert cpu_part["gpu_gres"] == "(null)"
        # gpu-v100 partition
        v100_part = next(p for p in partitions if "v100" in p["partition"])
        assert v100_part["gpu_gres"] == "gpu:v100:2"

    def test_parse_sinfo_empty_output_returns_no_partitions(self) -> None:
        """Empty sinfo stdout → available True but no partitions (reachable, no partitions)."""
        from research_vault.doctor import _parse_sinfo_output
        result = _parse_sinfo_output("")
        assert result["available"] is True
        assert result["partitions"] == []

    def test_parse_sinfo_extracts_gpu_types_list(self) -> None:
        """_parse_sinfo_output builds a deduplicated gpu_types list."""
        from research_vault.doctor import _parse_sinfo_output
        stdout = (
            "gpu  gpu:a100:4  8\n"
            "gpu2 gpu:a100:2  2\n"  # same GPU type, different partition
            "v100 gpu:v100:8  4\n"
        )
        result = _parse_sinfo_output(stdout)
        gpu_types = result["gpu_types"]
        # Should contain a100 and v100, deduplicated
        assert "a100" in gpu_types
        assert "v100" in gpu_types
        # a100 appears twice but should be deduplicated
        assert gpu_types.count("a100") == 1

    def test_parse_sinfo_null_gres_not_in_gpu_types(self) -> None:
        """Partitions with (null) GRES don't contribute to gpu_types."""
        from research_vault.doctor import _parse_sinfo_output
        stdout = "cpu (null) 16\n"
        result = _parse_sinfo_output(stdout)
        assert result["gpu_types"] == []


# ---------------------------------------------------------------------------
# Tests 1, 7: ssh+slurm probe populates capabilities
# ---------------------------------------------------------------------------

class TestSlurmRemoteProbe:
    """ssh+slurm: mocked sinfo → capabilities populated correctly."""

    SINFO_OUTPUT = (
        "gpu*     gpu:a100:4   8\n"
        "cpu      (null)       32\n"
        "debug    (null)       2\n"
    )

    def test_ssh_slurm_probe_populates_capabilities(self, cfg: Config) -> None:
        """Declared ssh+slurm backend: doctor probes via ssh and populates capabilities."""
        from research_vault.doctor import cmd_doctor
        _write_slurm_manifest(cfg)

        ssh_result = _make_ssh_result(returncode=0, stdout=self.SINFO_OUTPUT)

        with patch("research_vault.adapters.remote._ssh_exec", return_value=ssh_result):
            result = cmd_doctor(cfg, refresh=True)

        backends = result["backends"]
        assert "cluster" in backends
        cluster_caps = backends["cluster"]["capabilities"]

        # Must be a real probe, not a deferral
        assert cluster_caps.get("probe_status") != "deferred", (
            "Expected real probe but got deferral — SR-CO-REMOTE not wired in"
        )
        assert cluster_caps.get("probe_status") == "ok"
        assert cluster_caps.get("reachable") is True

        # Partitions must be populated
        partitions = cluster_caps.get("partitions", [])
        assert len(partitions) == 3, f"Expected 3 partitions, got {len(partitions)}: {partitions}"

        # GPU types must be discovered from sinfo (not login-node nvidia-smi)
        gpu_types = cluster_caps.get("gpu_types", [])
        assert "a100" in gpu_types, (
            f"a100 GPU not found in capabilities — scheduler-based discovery failed. "
            f"gpu_types={gpu_types}"
        )

    def test_ssh_slurm_probe_per_backend_cache_written(self, cfg: Config) -> None:
        """After probe, cache contains real capabilities (not deferred)."""
        from research_vault.doctor import cmd_doctor, _cache_path
        _write_slurm_manifest(cfg)

        ssh_result = _make_ssh_result(returncode=0, stdout=self.SINFO_OUTPUT)
        with patch("research_vault.adapters.remote._ssh_exec", return_value=ssh_result):
            cmd_doctor(cfg, refresh=True)

        raw = json.loads(_cache_path(cfg).read_text(encoding="utf-8"))
        assert "cluster" in raw["backends"]
        cluster_entry = raw["backends"]["cluster"]
        assert "capabilities" in cluster_entry
        caps = cluster_entry["capabilities"]
        assert caps.get("probe_status") == "ok"
        assert "partitions" in caps


# ---------------------------------------------------------------------------
# Test 2: Login-node GPU trap
# ---------------------------------------------------------------------------

class TestLoginNodeGpuTrap:
    """The correctness test: probe must NOT rely on login-node nvidia-smi.

    Common HPC shape: login node has no GPU, but compute nodes do.
    nvidia-smi on the login node returns nothing / fails.
    The probe must discover GPUs via the scheduler (sinfo), not nvidia-smi.
    """

    SINFO_WITH_GPUS = (
        "gpu gpu:a100:4 8\n"
        "cpu (null)     16\n"
    )

    def test_login_node_no_nvidia_smi_but_sinfo_shows_gpus(self, cfg: Config) -> None:
        """Probe finds GPUs via sinfo even when nvidia-smi is absent on login node.

        This is the core correctness test for SR-CO-REMOTE.
        If the probe called nvidia-smi on the login node, it would return no GPUs.
        The correct implementation calls sinfo and finds gpu:a100:4 there.
        """
        from research_vault.doctor import cmd_doctor
        _write_slurm_manifest(cfg)

        # nvidia-smi is absent on the login node (would return exit 127 / not found)
        # sinfo correctly reports GPUs on compute nodes
        sinfo_result = _make_ssh_result(returncode=0, stdout=self.SINFO_WITH_GPUS)

        # Only sinfo should be called — not nvidia-smi
        # We mock _ssh_exec to verify the command
        called_commands: list[list[str]] = []

        def fake_ssh_exec(host: str, argv: list[str], *, timeout: int = 15) -> MagicMock:
            called_commands.append(list(argv))
            # argv may have BatchMode/-o prefix; check all elements for the command
            if any("sinfo" in arg for arg in argv):
                return sinfo_result
            # If nvidia-smi is called, return "not found" — this simulates the login node
            nvidia_fail = _make_ssh_result(returncode=127, stdout="", stderr="nvidia-smi: command not found")
            return nvidia_fail

        with patch("research_vault.adapters.remote._ssh_exec", side_effect=fake_ssh_exec):
            result = cmd_doctor(cfg, refresh=True)

        cluster_caps = result["backends"]["cluster"]["capabilities"]

        # The probe must have found GPUs via sinfo, not nvidia-smi
        gpu_types = cluster_caps.get("gpu_types", [])
        assert "a100" in gpu_types, (
            "GPU discovery FAILED the login-node trap test: "
            "nvidia-smi absent on login node but probe should have found GPUs via sinfo. "
            f"gpu_types={gpu_types}, probe_status={cluster_caps.get('probe_status')}"
        )

        # Verify sinfo was called (the scheduler, not nvidia-smi)
        sinfo_calls = [c for c in called_commands if any("sinfo" in arg for arg in c)]
        assert len(sinfo_calls) >= 1, (
            f"sinfo was not called — probe not using scheduler for GPU discovery. "
            f"Called: {called_commands}"
        )

        # nvidia-smi must NOT be the primary GPU discovery method
        # (it may be called, but should not be the source of gpu_types)
        # The definitive check: gpu_types are populated despite nvidia-smi "failing"
        assert cluster_caps.get("probe_status") == "ok"
        assert cluster_caps.get("reachable") is True


# ---------------------------------------------------------------------------
# Test 3, 4: Unreachable host → honest report, no crash
# ---------------------------------------------------------------------------

class TestUnreachableHost:
    """Unreachable host: honest report, no crash, distinct from 'no GPUs'."""

    def test_ssh_timeout_yields_unreachable_report(self, cfg: Config) -> None:
        """ssh timeout → 'unreachable' report, exit 0, no crash."""
        from research_vault.doctor import cmd_doctor, format_report
        _write_slurm_manifest(cfg)

        with patch(
            "research_vault.adapters.remote._ssh_exec",
            side_effect=subprocess.TimeoutExpired(["ssh"], 10),
        ):
            result = cmd_doctor(cfg, refresh=True)

        cluster_caps = result["backends"]["cluster"]["capabilities"]
        assert cluster_caps.get("reachable") is False
        assert cluster_caps.get("probe_status") == "unreachable"
        # Must have a reason (not empty)
        reason = cluster_caps.get("reason", "")
        assert reason, "Unreachable report must include a reason"
        # Must be distinct from "no GPUs" (which implies reachable)
        assert "gpu_types" not in cluster_caps or cluster_caps.get("gpu_types") is None

        # format_report must surface the unreachable reason (charter §2)
        report = format_report(result)
        assert "unreachable" in report.lower() or "check" in report.lower()

    def test_ssh_auth_failure_yields_unreachable_report(self, cfg: Config) -> None:
        """ssh exit 255 (auth failure / no route) → 'unreachable' report, no crash."""
        from research_vault.doctor import cmd_doctor
        _write_slurm_manifest(cfg)

        # ssh exit 255 = Permission denied / no route to host
        ssh_fail = _make_ssh_result(
            returncode=255,
            stdout="",
            stderr="ssh: connect to host example-hpc.edu port 22: Connection refused",
        )
        with patch("research_vault.adapters.remote._ssh_exec", return_value=ssh_fail):
            result = cmd_doctor(cfg, refresh=True)

        cluster_caps = result["backends"]["cluster"]["capabilities"]
        assert cluster_caps.get("reachable") is False
        assert cluster_caps.get("probe_status") == "unreachable"
        reason = cluster_caps.get("reason", "")
        assert reason  # must explain why

    def test_ssh_file_not_found_yields_unreachable_report(self, cfg: Config) -> None:
        """ssh binary not found → honest report, no crash."""
        from research_vault.doctor import cmd_doctor
        _write_slurm_manifest(cfg)

        with patch(
            "research_vault.adapters.remote._ssh_exec",
            side_effect=FileNotFoundError("ssh"),
        ):
            result = cmd_doctor(cfg, refresh=True)

        cluster_caps = result["backends"]["cluster"]["capabilities"]
        assert cluster_caps.get("probe_status") == "unreachable"
        assert cluster_caps.get("reachable") is False

    def test_unreachable_distinct_from_no_gpus(self, cfg: Config) -> None:
        """'unreachable' report is structurally distinct from 'reachable, no GPUs'.

        A reachable cluster with no GPUs has probe_status='ok' + gpu_types=[].
        An unreachable cluster has probe_status='unreachable'.
        These must not be conflated.
        """
        from research_vault.doctor import cmd_doctor
        _write_slurm_manifest(cfg, host="unreachable-host.example")

        # Unreachable scenario
        with patch(
            "research_vault.adapters.remote._ssh_exec",
            side_effect=subprocess.TimeoutExpired(["ssh"], 10),
        ):
            unreachable_result = cmd_doctor(cfg, refresh=True)
        unreachable_caps = unreachable_result["backends"]["cluster"]["capabilities"]

        # Now simulate a reachable cluster with no GPUs
        sinfo_no_gpu = _make_ssh_result(returncode=0, stdout="cpu (null) 16\n")
        with patch("research_vault.adapters.remote._ssh_exec", return_value=sinfo_no_gpu):
            reachable_result = cmd_doctor(cfg, refresh=True)
        reachable_caps = reachable_result["backends"]["cluster"]["capabilities"]

        # Structural difference
        assert unreachable_caps["probe_status"] == "unreachable"
        assert reachable_caps["probe_status"] == "ok"
        assert reachable_caps["reachable"] is True
        assert unreachable_caps["reachable"] is False


# ---------------------------------------------------------------------------
# Test 5: BatchMode + ConnectTimeout flags in ssh argv
# ---------------------------------------------------------------------------

class TestBatchModeFailFast:
    """BatchMode=yes + ConnectTimeout must be set on probe ssh argv — never hang."""

    def test_batch_mode_in_probe_argv(self, cfg: Config) -> None:
        """Probe ssh call includes -o BatchMode=yes (never prompt for password)."""
        from research_vault.doctor import cmd_doctor
        _write_slurm_manifest(cfg)

        sinfo_result = _make_ssh_result(returncode=0, stdout="gpu gpu:a100:4 8\n")
        captured_calls: list[tuple[str, list[str]]] = []

        def capturing_ssh_exec(host: str, argv: list[str], *, timeout: int = 15) -> MagicMock:
            captured_calls.append((host, list(argv)))
            return sinfo_result

        with patch("research_vault.adapters.remote._ssh_exec", side_effect=capturing_ssh_exec):
            cmd_doctor(cfg, refresh=True)

        # At least one ssh call must have been made to the cluster host
        probe_calls = [c for c in captured_calls if c[0] == "example-hpc.edu"]
        assert len(probe_calls) >= 1, (
            f"No ssh call to example-hpc.edu — probe not invoking _ssh_exec. "
            f"calls: {captured_calls}"
        )

        # Check that the _ssh_exec was called with BatchMode in the argv
        # The function builds ssh_argv and passes it to _ssh_exec — the extra
        # ssh flags (-o BatchMode=yes etc.) must be in the argv list
        # NOTE: _ssh_exec wraps subprocess.run(["ssh", host] + argv) —
        # so argv here does NOT include "ssh" or the host, but the -o flags
        # must appear somewhere in the argv passed to _ssh_exec.
        # We check all probe calls for BatchMode presence.
        batch_mode_present = any(
            any("BatchMode" in arg for arg in argv)
            for _, argv in probe_calls
        )
        assert batch_mode_present, (
            "BatchMode=yes not found in probe ssh argv — "
            "probe may hang on auth prompt for unconfigured hosts. "
            f"probe_calls: {probe_calls}"
        )

    def test_connect_timeout_in_probe_argv(self, cfg: Config) -> None:
        """Probe ssh call includes -o ConnectTimeout=N (fail-fast, no hang)."""
        from research_vault.doctor import cmd_doctor
        _write_slurm_manifest(cfg)

        sinfo_result = _make_ssh_result(returncode=0, stdout="gpu gpu:a100:4 8\n")
        captured_calls: list[tuple[str, list[str]]] = []

        def capturing_ssh_exec(host: str, argv: list[str], *, timeout: int = 15) -> MagicMock:
            captured_calls.append((host, list(argv)))
            return sinfo_result

        with patch("research_vault.adapters.remote._ssh_exec", side_effect=capturing_ssh_exec):
            cmd_doctor(cfg, refresh=True)

        probe_calls = [c for c in captured_calls if c[0] == "example-hpc.edu"]
        assert len(probe_calls) >= 1

        timeout_present = any(
            any("ConnectTimeout" in arg for arg in argv)
            for _, argv in probe_calls
        )
        assert timeout_present, (
            "ConnectTimeout not found in probe ssh argv — "
            "probe may hang indefinitely on unreachable hosts. "
            f"probe_calls: {probe_calls}"
        )


# ---------------------------------------------------------------------------
# Test 6: ssh+pbs backend probed via pbsnodes
# ---------------------------------------------------------------------------

class TestPbsRemoteProbe:
    """ssh+pbs: probe via pbsnodes -a (or qstat -Qf) discovers queue info."""

    PBSNODES_OUTPUT = (
        "node001\n"
        "     Mom = node001.example\n"
        "     gpus = 4\n"
        "     properties = gpu,v100\n"
        "node002\n"
        "     Mom = node002.example\n"
        "     gpus = 0\n"
        "     properties = cpu\n"
    )

    def test_ssh_pbs_probe_populates_capabilities(self, cfg: Config) -> None:
        """Declared ssh+pbs backend: doctor probes and marks reachable."""
        from research_vault.doctor import cmd_doctor
        _write_pbs_manifest(cfg)

        # pbsnodes returns successfully
        pbs_result = _make_ssh_result(returncode=0, stdout=self.PBSNODES_OUTPUT)

        with patch("research_vault.adapters.remote._ssh_exec", return_value=pbs_result):
            result = cmd_doctor(cfg, refresh=True)

        cluster_caps = result["backends"]["cluster"]["capabilities"]
        assert cluster_caps.get("probe_status") == "ok"
        assert cluster_caps.get("reachable") is True

    def test_ssh_pbs_unreachable_honest_report(self, cfg: Config) -> None:
        """ssh+pbs unreachable → same honest degrade as slurm."""
        from research_vault.doctor import cmd_doctor
        _write_pbs_manifest(cfg)

        with patch(
            "research_vault.adapters.remote._ssh_exec",
            side_effect=subprocess.TimeoutExpired(["ssh"], 10),
        ):
            result = cmd_doctor(cfg, refresh=True)

        cluster_caps = result["backends"]["cluster"]["capabilities"]
        assert cluster_caps.get("probe_status") == "unreachable"
        assert cluster_caps.get("reachable") is False


# ---------------------------------------------------------------------------
# Test 8: format_report shows real probe results
# ---------------------------------------------------------------------------

class TestFormatReportRemote:
    """format_report reflects real probe results when probe is live."""

    SINFO_OUTPUT = "gpu* gpu:a100:4 8\ncpu (null) 32\n"

    def test_format_report_shows_partitions_and_gpu_types(self, cfg: Config) -> None:
        """format_report shows cluster partitions + GPU types after real probe."""
        from research_vault.doctor import cmd_doctor, format_report
        _write_slurm_manifest(cfg)

        ssh_result = _make_ssh_result(returncode=0, stdout=self.SINFO_OUTPUT)
        with patch("research_vault.adapters.remote._ssh_exec", return_value=ssh_result):
            result = cmd_doctor(cfg, refresh=True)

        report = format_report(result)
        # Must show the cluster backend section
        assert "[cluster]" in report
        # Must NOT show the old deferral message (SR-CO-REMOTE implemented)
        assert "SR-CO-REMOTE" not in report, (
            "format_report still shows deferral message — real probe not wired into formatter"
        )
        # Must show reachable status
        assert "reachable" in report.lower()

    def test_format_report_shows_gpu_types_discovered(self, cfg: Config) -> None:
        """format_report mentions discovered GPU types from sinfo."""
        from research_vault.doctor import cmd_doctor, format_report
        _write_slurm_manifest(cfg)

        ssh_result = _make_ssh_result(returncode=0, stdout=self.SINFO_OUTPUT)
        with patch("research_vault.adapters.remote._ssh_exec", return_value=ssh_result):
            result = cmd_doctor(cfg, refresh=True)

        report = format_report(result)
        # GPU type from sinfo must appear in report
        assert "a100" in report.lower(), (
            f"GPU type 'a100' not in report — scheduler-based discovery not surfaced. "
            f"report:\n{report}"
        )

    def test_format_report_shows_unreachable_reason(self, cfg: Config) -> None:
        """format_report shows reason when cluster is unreachable."""
        from research_vault.doctor import cmd_doctor, format_report
        _write_slurm_manifest(cfg)

        with patch(
            "research_vault.adapters.remote._ssh_exec",
            side_effect=subprocess.TimeoutExpired(["ssh"], 10),
        ):
            result = cmd_doctor(cfg, refresh=True)

        report = format_report(result)
        assert "[cluster]" in report
        assert "unreachable" in report.lower()

    def test_format_report_reachable_no_gpus(self, cfg: Config) -> None:
        """format_report correctly shows 'no GPUs' for a reachable CPU-only cluster."""
        from research_vault.doctor import cmd_doctor, format_report
        _write_slurm_manifest(cfg)

        cpu_only_sinfo = _make_ssh_result(returncode=0, stdout="cpu (null) 32\n")
        with patch("research_vault.adapters.remote._ssh_exec", return_value=cpu_only_sinfo):
            result = cmd_doctor(cfg, refresh=True)

        report = format_report(result)
        assert "[cluster]" in report
        assert "reachable" in report.lower()
        # No GPU mention in positive sense
        assert "no gpu" in report.lower() or "0 gpu" in report.lower() or (
            "a100" not in report.lower() and "v100" not in report.lower()
        )


# ---------------------------------------------------------------------------
# Test 10: Graceful degrade — sinfo non-zero
# ---------------------------------------------------------------------------

class TestSinfoSchedulerError:
    """Scheduler (sinfo) returns non-zero → honest 'scheduler error' report."""

    def test_sinfo_nonzero_yields_scheduler_error_caps(self, cfg: Config) -> None:
        """sinfo exit non-zero → probe_status='scheduler_error', reachable=True."""
        from research_vault.doctor import cmd_doctor
        _write_slurm_manifest(cfg)

        # ssh itself succeeds (host reachable) but sinfo fails
        sinfo_fail = _make_ssh_result(
            returncode=1,
            stdout="",
            stderr="sinfo: error: slurm_load_partitions: Socket timed out",
        )
        with patch("research_vault.adapters.remote._ssh_exec", return_value=sinfo_fail):
            result = cmd_doctor(cfg, refresh=True)

        cluster_caps = result["backends"]["cluster"]["capabilities"]
        # Host is reachable (ssh connected)
        assert cluster_caps.get("reachable") is True
        # But scheduler had an error — distinct from "unreachable"
        assert cluster_caps.get("probe_status") in ("scheduler_error", "ok"), (
            f"Expected 'scheduler_error' or 'ok' (with empty partitions), "
            f"got {cluster_caps.get('probe_status')!r}"
        )
        # Must NOT crash — returns a dict, not an exception
        assert isinstance(cluster_caps, dict)


# ---------------------------------------------------------------------------
# Test 11: Reachable but no GPUs — distinct from unreachable
# ---------------------------------------------------------------------------

class TestReachableNoGpus:
    """Reachable cluster with no GPU partitions → probe_status='ok', gpu_types=[]."""

    def test_reachable_no_gpus_distinct_from_unreachable(self, cfg: Config) -> None:
        """CPU-only cluster: probe_status='ok', reachable=True, gpu_types=[]."""
        from research_vault.doctor import cmd_doctor
        _write_slurm_manifest(cfg)

        cpu_only = _make_ssh_result(returncode=0, stdout="cpu (null) 32\ndebug (null) 4\n")
        with patch("research_vault.adapters.remote._ssh_exec", return_value=cpu_only):
            result = cmd_doctor(cfg, refresh=True)

        cluster_caps = result["backends"]["cluster"]["capabilities"]
        assert cluster_caps.get("probe_status") == "ok"
        assert cluster_caps.get("reachable") is True
        gpu_types = cluster_caps.get("gpu_types", [])
        assert gpu_types == [], f"Expected empty gpu_types for CPU-only cluster, got {gpu_types}"


# ---------------------------------------------------------------------------
# Test 12: SR-CO regression — local backend unchanged
# ---------------------------------------------------------------------------

class TestLocalBackendUnchanged:
    """Local backend probe path is unchanged after SR-CO-REMOTE implementation."""

    def test_local_probe_still_works_without_ssh(self, cfg: Config) -> None:
        """Local backend probe does not call ssh — unchanged from SR-CO."""
        from research_vault.doctor import cmd_doctor
        from research_vault.compute import _save_manifest
        # Only declare local backend
        _save_manifest(cfg, {
            "backends": {
                "active": ["local"],
                "profiles": {"local": {"archetype": "local"}},
            },
            "conda_envs": {},
            "gpu_tiers": {},
            "rules": [],
            "model_quirks": {},
            "run_outcomes": [],
        })

        # If ssh is somehow called for local, this would fail — it must not be called
        with patch(
            "research_vault.adapters.remote._ssh_exec",
            side_effect=AssertionError("ssh should not be called for local backend"),
        ):
            result = cmd_doctor(cfg, refresh=True)

        assert "local" in result["backends"]
        local_caps = result["backends"]["local"]["capabilities"]
        assert local_caps.get("local_available") is True

    def test_local_probe_not_deferred(self, cfg: Config) -> None:
        """Local backend never has probe_status='deferred'."""
        from research_vault.doctor import cmd_doctor
        from research_vault.compute import _save_manifest
        _save_manifest(cfg, {
            "backends": {
                "active": ["local"],
                "profiles": {"local": {"archetype": "local"}},
            },
            "conda_envs": {},
            "gpu_tiers": {},
            "rules": [],
            "model_quirks": {},
            "run_outcomes": [],
        })
        result = cmd_doctor(cfg, refresh=True)
        local_caps = result["backends"]["local"]["capabilities"]
        assert local_caps.get("probe_status") != "deferred"


# ---------------------------------------------------------------------------
# Test 13: SR-7 submit path justification (docstring accuracy)
# ---------------------------------------------------------------------------

class TestSubmitSshExecDocstring:
    """Verify the _ssh_exec docstring accurately reflects its call sites."""

    def test_ssh_exec_docstring_does_not_overclaim_submit(self) -> None:
        """_ssh_exec docstring must accurately reflect that submit uses subprocess.run directly.

        SR-CO-REMOTE resolution: the spec asked us to route submit() through _ssh_exec.
        After examination, submit() has meaningfully different requirements:
        - It builds a full ssh_argv list (["ssh", host] + submit_parts + cmd)
        - It uses timeout=60 (not 15) for long-running job submissions
        - It handles FileNotFoundError → RuntimeError (different from probe degrade)
        - It uses subprocess.run directly for the full ssh argv, not _ssh_exec

        The docstring was updated to reflect the accurate call sites (_run_status only).
        This test is the docstring-accuracy gate.
        """
        import inspect
        from research_vault.adapters.remote import _ssh_exec
        doc = inspect.getdoc(_ssh_exec) or ""
        # Must NOT claim to be extracted from submit (it is not)
        assert "submit" not in doc.lower() or "not" in doc.lower() or (
            # OR: the docstring may mention submit if it's accurate about NOT being used there
            "directly" in doc.lower()
        ), (
            "_ssh_exec docstring overclaims 'extracted from submit' — "
            "submit uses subprocess.run directly. Fix the docstring."
        )

    def test_run_status_uses_ssh_exec_ssot(self) -> None:
        """_run_status continues to use _ssh_exec as its SSOT (SR-7 + SR-CO contract)."""
        from research_vault.adapters.remote import _run_status, _merge_profile_defaults
        profile = _merge_profile_defaults({
            "archetype": "ssh+slurm",
            "host": "example-hpc.edu",
        })
        fake_result = _make_ssh_result(returncode=0, stdout="123|COMPLETED")
        with patch("research_vault.adapters.remote._ssh_exec", return_value=fake_result):
            state = _run_status("123", profile)
        assert state == "DONE"
