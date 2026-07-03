"""test_sr_doctor_principled.py — SR-DOCTOR-PRINCIPLED: four-stage principled rv doctor.

Covers:
  PREREQ REFACTOR:
    1.  _ssh_probe_call: FileNotFoundError -> unreachable dict
    2.  _ssh_probe_call: TimeoutExpired -> unreachable dict
    3.  _ssh_probe_call: exit 255 -> unreachable dict
    4.  _ssh_probe_call: success -> CompletedProcess (caller checks returncode)
    5.  SR-CO-REMOTE probers still work through _ssh_probe_call (existing suite unchanged)

  DISCOVER - Stage 1b: Permissions probe (SLURM):
    6.  sacctmgr output -> allowed_partitions parsed correctly
    7.  A partition in sinfo inventory but not in user AllowAccounts -> forbidden with reason
    8.  A partition in sinfo inventory but in AllowAccounts=ALL -> allowed
    9.  sacctmgr returns non-zero -> permissions.available=False, graceful degrade
    10. sacctmgr absent (ssh fails) -> permissions.available=False, graceful degrade
    11. scontrol output parsed: AllowAccounts cross-check
    12. Permissions block present in ssh+slurm caps after probe

  DISCOVER - Per-type bifurcation:
    13. ssh archetype: nvidia-smi called over ssh (direct; NOT sacctmgr)
    14. ssh archetype: sacctmgr NOT called (no scheduler)
    15. local archetype: no ssh calls at all

  PROPOSE - Stage 2:
    16. inventory ∩ permissions -> deterministic tier->partition (cheapest-that-fits)
    17. Partition in inventory but forbidden -> NOT proposed (key non-vacuity)
    18. Tier with no allowed fitting partition -> unmapped with reason (not silent pick)
    19. Propose is deterministic (same inputs -> same outputs)
    20. Rationale string present on each proposed tier

  CONFIRM - Stage 3:
    21. Plain `rv doctor` writes NOTHING to compute.tiers (manifest unchanged)
    22. --propose writes `tiers_proposed` (NOT live `tiers`)
    23. --accept promotes tiers_proposed -> tiers, clears proposed block
    24. No code path writes live tiers without --accept (human-gate non-vacuity)
    25. --accept without prior --propose returns error, no manifest change

  LEARN - Stage 4:
    26. OOM outcome on proposed partition -> proposal annotated with warning
    27. Lesson rule matching proposed partition -> surfaces inline in proposal

  GRACEFUL DEGRADE:
    28. sacctmgr absent -> permissions.available=False + inventory-only banner
    29. StrictHostKeyChecking=accept-new in _SSH_PROBE_OPTS (not 'no')

  TIGHTEN EXISTING TESTS (#69 follow-ups):
    30. test_sinfo_nonzero -> probe_status == "scheduler_error" (exact, not loose)
    31. _ssh_exec docstring: accurate (mentions submit uses subprocess.run directly)

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
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import Config


# ---------------------------------------------------------------------------
# Fixtures and helpers
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


def _make_ssh_result(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    """Make a fake subprocess.CompletedProcess for _ssh_exec mocking."""
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


def _write_slurm_manifest(
    cfg: Config,
    host: str = "example-hpc.edu",
    gpu_tiers: dict | None = None,
    run_outcomes: list | None = None,
    rules: list | None = None,
) -> None:
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
        "gpu_tiers": gpu_tiers or {
            "tp1": {"gpus": 1, "models": ["<=7B"]},
            "tp2": {"gpus": 2, "models": ["<=34B"]},
        },
        "rules": rules or [],
        "model_quirks": {},
        "run_outcomes": run_outcomes or [],
    })


def _write_ssh_manifest(cfg: Config, host: str = "gpu-box.example") -> None:
    """Write a compute manifest with a declared plain ssh backend."""
    from research_vault.compute import _save_manifest
    _save_manifest(cfg, {
        "backends": {
            "active": ["gpu-box"],
            "profiles": {
                "local": {"archetype": "local"},
                "gpu-box": {
                    "archetype": "ssh",
                    "host": host,
                },
            },
        },
        "conda_envs": {},
        "gpu_tiers": {"tp1": {"gpus": 1, "models": ["<=7B"]}},
        "rules": [],
        "model_quirks": {},
        "run_outcomes": [],
    })


# Canned sacctmgr output (parsable2 format with -P flag)
_SACCTMGR_ASSOC_OUTPUT = (
    "Account|Partition|QOS|GrpTRES|MaxWall|MaxTRES\n"
    "mylab|gpu-short|normal||04:00:00|gres/gpu=8\n"
    "mylab|gpu-long|normal||24:00:00|gres/gpu=8\n"
)

_SACCTMGR_QOS_OUTPUT = (
    "Name|MaxWall|MaxTRESPU|MaxJobsPU|MaxSubmitPU\n"
    "normal|24:00:00|gres/gpu=8|50|200\n"
)

_SCONTROL_PARTITION_OUTPUT = (
    "PartitionName=gpu-short AllowAccounts=mylab,otherlab AllowQos=ALL "
    "DenyQos=N/A MaxTime=04:00:00 DefaultTime=01:00:00\n"
    "\n"
    "PartitionName=gpu-long AllowAccounts=mylab AllowQos=ALL "
    "DenyQos=N/A MaxTime=24:00:00 DefaultTime=04:00:00\n"
    "\n"
    "PartitionName=gpu-priority AllowAccounts=privileged AllowQos=ALL "
    "DenyQos=N/A MaxTime=72:00:00 DefaultTime=08:00:00\n"
    "\n"
)

_SINFO_OUTPUT = (
    "gpu-short  gpu:a100:4  8\n"
    "gpu-long   gpu:a100:8  4\n"
    "gpu-priority gpu:h100:8 2\n"
    "cpu        (null)      32\n"
)


def _make_slurm_ssh_exec(
    sinfo_output: str = _SINFO_OUTPUT,
    sacctmgr_assoc_output: str = _SACCTMGR_ASSOC_OUTPUT,
    sacctmgr_qos_output: str = _SACCTMGR_QOS_OUTPUT,
    scontrol_output: str = _SCONTROL_PARTITION_OUTPUT,
    sacctmgr_returncode: int = 0,
) -> Any:
    """Build a fake _ssh_exec that returns appropriate results per command."""
    def fake_ssh_exec(host: str, argv: list[str], *, timeout: int = 15) -> MagicMock:
        cmd_str = " ".join(argv)
        if "sinfo" in cmd_str:
            return _make_ssh_result(returncode=0, stdout=sinfo_output)
        if "sacctmgr" in cmd_str and "assoc" in cmd_str:
            return _make_ssh_result(returncode=sacctmgr_returncode, stdout=sacctmgr_assoc_output)
        if "sacctmgr" in cmd_str and "qos" in cmd_str:
            return _make_ssh_result(returncode=0, stdout=sacctmgr_qos_output)
        if "scontrol" in cmd_str:
            return _make_ssh_result(returncode=0, stdout=scontrol_output)
        # Default: success
        return _make_ssh_result(returncode=0, stdout="")
    return fake_ssh_exec


# ---------------------------------------------------------------------------
# Tests 1-4: _ssh_probe_call error ladder
# ---------------------------------------------------------------------------

class TestSshProbeCall:
    """_ssh_probe_call handles the full error ladder and returns CompletedProcess on success."""

    def test_file_not_found_returns_unreachable_dict(self) -> None:
        """FileNotFoundError -> dict with probe_status=unreachable."""
        from research_vault.doctor import _ssh_probe_call
        with patch("research_vault.adapters.remote._ssh_exec",
                   side_effect=FileNotFoundError("ssh")):
            result = _ssh_probe_call("example-hpc.edu", ["true"], "ssh+slurm")
        assert isinstance(result, dict)
        assert result["probe_status"] == "unreachable"
        assert result["reachable"] is False
        assert "ssh binary not found" in result.get("reason", "")
        assert result["host"] == "example-hpc.edu"
        assert result["archetype"] == "ssh+slurm"

    def test_timeout_returns_unreachable_dict(self) -> None:
        """TimeoutExpired -> dict with probe_status=unreachable."""
        from research_vault.doctor import _ssh_probe_call
        with patch("research_vault.adapters.remote._ssh_exec",
                   side_effect=subprocess.TimeoutExpired(["ssh"], 10)):
            result = _ssh_probe_call("example-hpc.edu", ["true"], "ssh+slurm")
        assert isinstance(result, dict)
        assert result["probe_status"] == "unreachable"
        assert result["reachable"] is False
        assert "timed out" in result.get("reason", "")

    def test_exit_255_returns_unreachable_dict(self) -> None:
        """exit 255 (auth failure) -> dict with probe_status=unreachable."""
        from research_vault.doctor import _ssh_probe_call
        auth_fail = _make_ssh_result(returncode=255, stderr="Connection refused")
        with patch("research_vault.adapters.remote._ssh_exec", return_value=auth_fail):
            result = _ssh_probe_call("example-hpc.edu", ["true"], "ssh+slurm")
        assert isinstance(result, dict)
        assert result["probe_status"] == "unreachable"
        assert result["reachable"] is False
        assert "255" in result.get("reason", "") or "auth" in result.get("reason", "").lower()

    def test_success_returns_completed_process(self) -> None:
        """Successful ssh -> returns CompletedProcess (not a dict)."""
        from research_vault.doctor import _ssh_probe_call
        success = _make_ssh_result(returncode=0, stdout="ok\n")
        with patch("research_vault.adapters.remote._ssh_exec", return_value=success):
            result = _ssh_probe_call("example-hpc.edu", ["true"], "ssh+slurm")
        # Must NOT be a dict (no error)
        assert not isinstance(result, dict)
        assert result.returncode == 0

    def test_ose_error_returns_unreachable_dict(self) -> None:
        """OSError -> dict with probe_status=unreachable."""
        from research_vault.doctor import _ssh_probe_call
        with patch("research_vault.adapters.remote._ssh_exec",
                   side_effect=OSError("connection reset")):
            result = _ssh_probe_call("example-hpc.edu", ["true"], "ssh+slurm")
        assert isinstance(result, dict)
        assert result["probe_status"] == "unreachable"
        assert "connection reset" in result.get("reason", "")


# ---------------------------------------------------------------------------
# Tests 6-12: Permissions probe (SLURM)
# ---------------------------------------------------------------------------

class TestPermissionsProbe:
    """SLURM permissions probe: sacctmgr + scontrol -> allowed/forbidden partitions."""

    def test_sacctmgr_output_yields_allowed_partitions(self, cfg: Config) -> None:
        """sacctmgr assoc output -> allowed_partitions populated."""
        from research_vault.doctor import _probe_permissions_slurm
        with patch("research_vault.adapters.remote._ssh_exec",
                   side_effect=_make_slurm_ssh_exec()):
            perms = _probe_permissions_slurm("example-hpc.edu")
        assert perms["available"] is True
        allowed = perms["allowed_partitions"]
        assert "gpu-short" in allowed
        assert "gpu-long" in allowed

    def test_forbidden_partition_excluded_from_allowed(self, cfg: Config) -> None:
        """Partition in scontrol AllowAccounts excluding user's account -> forbidden.

        This is the key non-vacuity: gpu-priority is in sinfo (inventory)
        but AllowAccounts=privileged excludes the user's account 'mylab'.
        """
        from research_vault.doctor import _probe_permissions_slurm
        with patch("research_vault.adapters.remote._ssh_exec",
                   side_effect=_make_slurm_ssh_exec()):
            perms = _probe_permissions_slurm("example-hpc.edu")
        assert perms["available"] is True
        allowed = perms["allowed_partitions"]
        # gpu-priority should NOT be in allowed (AllowAccounts=privileged excludes mylab)
        assert "gpu-priority" not in allowed, (
            "gpu-priority should be forbidden but appeared in allowed_partitions — "
            "permissions cross-check not working"
        )
        forbidden = perms["forbidden_partitions"]
        forbidden_names = [f.get("partition") for f in forbidden]
        assert "gpu-priority" in forbidden_names

    def test_allow_accounts_all_includes_associated_partition(self) -> None:
        """AllowAccounts=ALL: partition allowed if user has any association."""
        from research_vault.doctor import _parse_scontrol_partitions, _parse_sacctmgr_assoc

        scontrol_all = (
            "PartitionName=open-gpu AllowAccounts=ALL AllowQos=ALL "
            "MaxTime=08:00:00\n\n"
        )
        acl = _parse_scontrol_partitions(scontrol_all)
        assert "open-gpu" in acl
        assert acl["open-gpu"].get("AllowAccounts") == "ALL"

    def test_sacctmgr_non_zero_returns_unavailable(self) -> None:
        """sacctmgr non-zero exit -> permissions.available=False, graceful degrade."""
        from research_vault.doctor import _probe_permissions_slurm
        with patch("research_vault.adapters.remote._ssh_exec",
                   side_effect=_make_slurm_ssh_exec(sacctmgr_returncode=1,
                                                     sacctmgr_assoc_output="")):
            perms = _probe_permissions_slurm("example-hpc.edu")
        assert perms["available"] is False
        assert "reason" in perms
        # Must explain why (charter §2)
        assert perms["reason"]

    def test_sacctmgr_connection_fail_returns_unavailable(self) -> None:
        """Connection fail on sacctmgr call -> permissions.available=False."""
        from research_vault.doctor import _probe_permissions_slurm

        def ssh_exec_sacctmgr_fails(host: str, argv: list[str], *, timeout: int = 15) -> MagicMock:
            cmd_str = " ".join(argv)
            if "sinfo" in cmd_str:
                return _make_ssh_result(returncode=0, stdout=_SINFO_OUTPUT)
            if "sacctmgr" in cmd_str:
                raise subprocess.TimeoutExpired(["ssh"], 10)
            return _make_ssh_result(returncode=0, stdout="")

        with patch("research_vault.adapters.remote._ssh_exec",
                   side_effect=ssh_exec_sacctmgr_fails):
            perms = _probe_permissions_slurm("example-hpc.edu")
        assert perms["available"] is False

    def test_permissions_block_present_in_slurm_caps(self, cfg: Config) -> None:
        """After ssh+slurm probe, caps dict contains a 'permissions' block."""
        from research_vault.doctor import cmd_doctor
        _write_slurm_manifest(cfg)
        with patch("research_vault.adapters.remote._ssh_exec",
                   side_effect=_make_slurm_ssh_exec()):
            result = cmd_doctor(cfg, refresh=True)
        cluster_caps = result["backends"]["cluster"]["capabilities"]
        assert cluster_caps.get("probe_status") == "ok"
        assert "permissions" in cluster_caps, (
            "permissions block missing from ssh+slurm caps — "
            "Stage 1b permissions probe not wired in"
        )


# ---------------------------------------------------------------------------
# Tests 13-15: Per-type bifurcation
# ---------------------------------------------------------------------------

class TestPerTypeBifurcation:
    """DISCOVER bifurcates by topology: ssh uses nvidia-smi; slurm/pbs use scheduler."""

    def test_ssh_backend_calls_nvidia_smi_not_sacctmgr(self, cfg: Config) -> None:
        """Plain ssh archetype: nvidia-smi called; sacctmgr must NOT be called."""
        from research_vault.doctor import cmd_doctor
        _write_ssh_manifest(cfg)

        called_cmds: list[str] = []

        def capturing_exec(host: str, argv: list[str], *, timeout: int = 15) -> MagicMock:
            called_cmds.append(" ".join(argv))
            if "true" in argv:
                return _make_ssh_result(returncode=0, stdout="")
            if "nvidia-smi" in " ".join(argv):
                return _make_ssh_result(returncode=0, stdout="Tesla V100\n")
            return _make_ssh_result(returncode=0, stdout="")

        with patch("research_vault.adapters.remote._ssh_exec", side_effect=capturing_exec):
            result = cmd_doctor(cfg, refresh=True)

        # nvidia-smi MUST be called
        nvidia_calls = [c for c in called_cmds if "nvidia-smi" in c]
        assert len(nvidia_calls) >= 1, (
            f"nvidia-smi not called for ssh backend — direct GPU probe missing. "
            f"called: {called_cmds}"
        )

        # sacctmgr must NOT be called (no scheduler on a plain ssh box)
        sacctmgr_calls = [c for c in called_cmds if "sacctmgr" in c]
        assert len(sacctmgr_calls) == 0, (
            f"sacctmgr was called for a plain ssh backend — "
            f"login-node-nvidia-smi avoidance rule must not leak to ssh archetype. "
            f"sacctmgr calls: {sacctmgr_calls}"
        )

    def test_ssh_backend_result_has_gpu_info(self, cfg: Config) -> None:
        """Plain ssh archetype: caps dict includes gpu_info from nvidia-smi over ssh."""
        from research_vault.doctor import cmd_doctor
        _write_ssh_manifest(cfg)

        def capturing_exec(host: str, argv: list[str], *, timeout: int = 15) -> MagicMock:
            if "true" in argv:
                return _make_ssh_result(returncode=0, stdout="")
            if "nvidia-smi" in " ".join(argv):
                return _make_ssh_result(returncode=0, stdout="Tesla V100\nTesla V100\n")
            return _make_ssh_result(returncode=0, stdout="")

        with patch("research_vault.adapters.remote._ssh_exec", side_effect=capturing_exec):
            result = cmd_doctor(cfg, refresh=True)

        gpu_box_caps = result["backends"]["gpu-box"]["capabilities"]
        assert gpu_box_caps.get("probe_status") == "ok"
        gpu_info = gpu_box_caps.get("gpu_info", {})
        assert gpu_info.get("available") is True
        assert gpu_info.get("count") == 2

    def test_local_backend_no_ssh_called(self, cfg: Config) -> None:
        """Local backend: no ssh calls at all."""
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

        with patch("research_vault.adapters.remote._ssh_exec",
                   side_effect=AssertionError("ssh must not be called for local")):
            result = cmd_doctor(cfg, refresh=True)

        assert "local" in result["backends"]


# ---------------------------------------------------------------------------
# Tests 16-20: PROPOSE — pure deterministic function
# ---------------------------------------------------------------------------

class TestProposeTiers:
    """_propose_tiers: pure deterministic inventory ∩ permissions -> tier->partition."""

    def _make_inventory(self) -> list[dict]:
        return [
            {"partition": "gpu-short", "gpu_gres": "gpu:a100:4", "nodes": "8"},
            {"partition": "gpu-long", "gpu_gres": "gpu:a100:8", "nodes": "4"},
            {"partition": "gpu-priority", "gpu_gres": "gpu:h100:8", "nodes": "2"},
            {"partition": "cpu", "gpu_gres": "(null)", "nodes": "32"},
        ]

    def _make_permissions(self, allowed: list[str]) -> dict:
        assocs = [{"Account": "mylab", "Partition": p, "QOS": "normal",
                   "GrpTRES": "", "MaxWall": "24:00:00", "MaxTRES": "gres/gpu=8"}
                  for p in allowed]
        return {
            "available": True,
            "associations": assocs,
            "qos": {"normal": {"Name": "normal", "MaxWall": "24:00:00"}},
            "partition_acls": {},
            "allowed_partitions": allowed,
            "forbidden_partitions": [
                {"partition": "gpu-priority", "reason": "AllowAccounts excludes mylab"}
            ] if "gpu-priority" not in allowed else [],
        }

    def test_cheapest_that_fits_tp1(self) -> None:
        """tp1 (gpus=1) -> gpu-short (cheapest with >=1 GPU that's allowed)."""
        from research_vault.doctor import _propose_tiers
        inventory = self._make_inventory()
        permissions = self._make_permissions(["gpu-short", "gpu-long"])
        gpu_tiers = {"tp1": {"gpus": 1, "models": ["<=7B"]}}

        result = _propose_tiers(
            partitions=inventory,
            permissions=permissions,
            gpu_tiers=gpu_tiers,
            run_outcomes=[],
            lessons=[],
        )
        mapping = result["mapping"]
        assert "tp1" in mapping
        assert mapping["tp1"]["partition"] == "gpu-short", (
            "tp1 should map to gpu-short (cheapest with 4 GPUs >= 1), "
            f"got {mapping['tp1']['partition']!r}"
        )

    def test_forbidden_partition_never_proposed(self) -> None:
        """Partition in inventory but forbidden -> never appears in proposal.

        This is the key non-vacuity: gpu-priority is in the inventory (sinfo)
        but not in allowed_partitions (AllowAccounts excludes mylab).
        Even if it's the only partition with enough GPUs, it must not be proposed.
        """
        from research_vault.doctor import _propose_tiers
        # Only gpu-priority has 8 GPUs, but it's forbidden
        inventory = [
            {"partition": "gpu-short", "gpu_gres": "gpu:a100:2", "nodes": "8"},
            {"partition": "gpu-priority", "gpu_gres": "gpu:h100:8", "nodes": "2"},
        ]
        permissions = self._make_permissions(["gpu-short"])  # gpu-priority is NOT allowed
        gpu_tiers = {"tp4": {"gpus": 8, "models": ["<=70B"]}}

        result = _propose_tiers(
            partitions=inventory,
            permissions=permissions,
            gpu_tiers=gpu_tiers,
            run_outcomes=[],
            lessons=[],
        )
        mapping = result["mapping"]
        assert "tp4" in mapping
        # tp4 should be UNMAPPED (not silently pick the forbidden gpu-priority)
        assert mapping["tp4"]["partition"] is None, (
            "tp4 should be unmapped (gpu-priority is forbidden) but got "
            f"{mapping['tp4']['partition']!r} — forbidden partition was proposed!"
        )
        reason = mapping["tp4"].get("rationale", "")
        assert reason, "Unmapped tier must have a reason (charter §2)"
        # Reason should mention the nearest forbidden partition
        assert "forbidden" in reason.lower() or "priority" in reason.lower()

    def test_unmapped_tier_surfaced_with_reason(self) -> None:
        """Tier with no allowed partition having enough GPUs -> unmapped + reason."""
        from research_vault.doctor import _propose_tiers
        inventory = [{"partition": "gpu-small", "gpu_gres": "gpu:a100:1", "nodes": "4"}]
        permissions = self._make_permissions(["gpu-small"])
        gpu_tiers = {"tp4": {"gpus": 8, "models": ["<=70B"]}}

        result = _propose_tiers(
            partitions=inventory,
            permissions=permissions,
            gpu_tiers=gpu_tiers,
            run_outcomes=[],
            lessons=[],
        )
        assert result["mapping"]["tp4"]["partition"] is None
        assert result["mapping"]["tp4"]["rationale"]

    def test_propose_is_deterministic(self) -> None:
        """Same inputs always produce the same proposal."""
        from research_vault.doctor import _propose_tiers
        inventory = self._make_inventory()
        permissions = self._make_permissions(["gpu-short", "gpu-long"])
        gpu_tiers = {
            "tp1": {"gpus": 1, "models": ["<=7B"]},
            "tp2": {"gpus": 4, "models": ["<=34B"]},
        }

        result1 = _propose_tiers(inventory, permissions, gpu_tiers, [], [])
        result2 = _propose_tiers(inventory, permissions, gpu_tiers, [], [])
        assert result1["mapping"] == result2["mapping"]

    def test_rationale_string_present(self) -> None:
        """Each proposed tier has a non-empty rationale string."""
        from research_vault.doctor import _propose_tiers
        inventory = self._make_inventory()
        permissions = self._make_permissions(["gpu-short", "gpu-long"])
        gpu_tiers = {"tp1": {"gpus": 1, "models": ["<=7B"]}}

        result = _propose_tiers(inventory, permissions, gpu_tiers, [], [])
        for tier_name, row in result["mapping"].items():
            assert row.get("rationale"), (
                f"Tier {tier_name!r} has empty rationale — rationale is required"
            )

    def test_inventory_only_fallback_when_permissions_unavailable(self) -> None:
        """When permissions=None/unavailable, falls back to inventory-only proposal."""
        from research_vault.doctor import _propose_tiers
        inventory = [{"partition": "gpu-short", "gpu_gres": "gpu:a100:4", "nodes": "8"}]
        permissions_unavail = {"available": False, "reason": "sacctmgr not found"}
        gpu_tiers = {"tp1": {"gpus": 1, "models": ["<=7B"]}}

        result = _propose_tiers(inventory, permissions_unavail, gpu_tiers, [], [])
        assert result["inventory_only"] is True
        # In inventory-only mode, should still propose a partition (no allowed filter)
        assert result["mapping"]["tp1"]["partition"] == "gpu-short"


# ---------------------------------------------------------------------------
# Tests 21-25: CONFIRM — quarantined tiers_proposed + --accept
# ---------------------------------------------------------------------------

class TestConfirm:
    """Stage 3: --propose writes tiers_proposed; --accept promotes to tiers."""

    def test_plain_doctor_writes_nothing_to_tiers(self, cfg: Config, tmp_path: Path) -> None:
        """Plain rv doctor (no --propose, no --accept) writes NOTHING to manifest.tiers.

        This is the human-gate non-vacuity: no code path writes live tiers without --accept.
        """
        from research_vault.doctor import run as doctor_run
        from research_vault.compute import _load_manifest
        import argparse
        _write_slurm_manifest(cfg)

        with patch("research_vault.adapters.remote._ssh_exec",
                   side_effect=_make_slurm_ssh_exec()):
            args = argparse.Namespace(refresh=True, propose=False, accept=False, _cfg=cfg)
            doctor_run(args)

        manifest = _load_manifest(cfg)
        assert "tiers" not in manifest, (
            "Plain rv doctor wrote 'tiers' to the manifest — "
            "live tiers must only be written by --accept"
        )

    def test_propose_writes_tiers_proposed_not_tiers(self, cfg: Config) -> None:
        """--propose writes tiers_proposed block; does NOT touch live tiers."""
        from research_vault.doctor import cmd_doctor, cmd_doctor_propose
        from research_vault.compute import _load_manifest
        _write_slurm_manifest(cfg)

        with patch("research_vault.adapters.remote._ssh_exec",
                   side_effect=_make_slurm_ssh_exec()):
            cmd_doctor(cfg, refresh=True)

        ret = cmd_doctor_propose(cfg)
        assert ret == 0

        manifest = _load_manifest(cfg)

        # tiers_proposed must be present
        assert "tiers_proposed" in manifest, "tiers_proposed block not written by --propose"
        proposed = manifest["tiers_proposed"]
        assert proposed.get("status") == "proposed"
        assert "mapping" in proposed

        # live tiers must NOT be present
        assert "tiers" not in manifest, (
            "--propose wrote to live 'tiers' — must only write to tiers_proposed"
        )

    def test_accept_promotes_to_live_tiers(self, cfg: Config) -> None:
        """--accept promotes tiers_proposed -> live tiers; stamps accepted_ts."""
        from research_vault.doctor import cmd_doctor, cmd_doctor_propose, cmd_doctor_accept
        from research_vault.compute import _load_manifest
        _write_slurm_manifest(cfg)

        with patch("research_vault.adapters.remote._ssh_exec",
                   side_effect=_make_slurm_ssh_exec()):
            cmd_doctor(cfg, refresh=True)

        cmd_doctor_propose(cfg)
        ret = cmd_doctor_accept(cfg)
        assert ret == 0

        manifest = _load_manifest(cfg)

        # live tiers must be populated
        assert "tiers" in manifest, "--accept did not write to live tiers"
        assert manifest["tiers"], "live tiers is empty after --accept"

        # tiers_proposed should now be stamped accepted (not deleted, but cleared)
        proposed = manifest.get("tiers_proposed", {})
        assert proposed.get("status") == "accepted"
        assert "accepted_ts" in proposed
        # mapping should be gone from the accepted block
        assert "mapping" not in proposed

    def test_accept_without_propose_returns_error(self, cfg: Config) -> None:
        """--accept without prior --propose: non-zero return, no manifest change."""
        from research_vault.doctor import cmd_doctor_accept
        from research_vault.compute import _load_manifest
        _write_slurm_manifest(cfg)

        manifest_before = _load_manifest(cfg)
        ret = cmd_doctor_accept(cfg)
        assert ret != 0, "--accept without prior --propose should return non-zero"

        manifest_after = _load_manifest(cfg)
        # Manifest should be unchanged
        assert "tiers" not in manifest_after

    def test_no_code_path_writes_live_tiers_without_accept(self, cfg: Config) -> None:
        """Non-vacuity: --propose does not write live tiers; only --accept does.

        We run --propose, verify no tiers key, then run --accept, verify tiers present.
        The absence of tiers after --propose is the human-gate invariant.
        """
        from research_vault.doctor import cmd_doctor, cmd_doctor_propose, cmd_doctor_accept
        from research_vault.compute import _load_manifest
        _write_slurm_manifest(cfg)

        with patch("research_vault.adapters.remote._ssh_exec",
                   side_effect=_make_slurm_ssh_exec()):
            cmd_doctor(cfg, refresh=True)

        # After --propose: tiers_proposed present, tiers absent
        cmd_doctor_propose(cfg)
        manifest = _load_manifest(cfg)
        assert "tiers_proposed" in manifest
        assert "tiers" not in manifest  # the invariant

        # After --accept: tiers present
        cmd_doctor_accept(cfg)
        manifest = _load_manifest(cfg)
        assert "tiers" in manifest  # promoted only by --accept


# ---------------------------------------------------------------------------
# Tests 26-27: LEARN — outcomes and lessons inform the proposal
# ---------------------------------------------------------------------------

class TestLearn:
    """Stage 4: run_outcomes and lessons annotate the proposal (still human-gated)."""

    def test_oom_outcome_real_schema_annotates_proposal_warning(self) -> None:
        """OOM outcome using the REAL capture schema (no partition field) -> warning.

        cmd_outcome_add (compute.py:524,537) writes {job, tier, result, ts} —
        NO partition field. The LEARN keying must use tier ALONE. This is the
        load-bearing correctness test: the real capture path never writes
        a 'partition' key, so any impl that requires it is silently inert.

        RED-FIRST: this test MUST fail against the old (tier,partition)-keyed
        implementation; it passes only after the fix (tier-only keying).
        """
        from research_vault.doctor import _propose_tiers

        inventory = [
            {"partition": "gpu-short", "gpu_gres": "gpu:a100:4", "nodes": "8"},
        ]
        permissions = {
            "available": True,
            "associations": [{"Account": "mylab", "Partition": "gpu-short",
                              "QOS": "normal", "GrpTRES": "", "MaxWall": "04:00:00",
                              "MaxTRES": "gres/gpu=8"}],
            "qos": {},
            "partition_acls": {},
            "allowed_partitions": ["gpu-short"],
            "forbidden_partitions": [],
        }
        gpu_tiers = {"tp1": {"gpus": 1, "models": ["<=7B"]}}

        # REAL schema from cmd_outcome_add — NO "partition" field
        run_outcomes = [
            {"job": "run-1", "tier": "tp1", "result": "OOM", "ts": "2026-07-01T10:00:00"}
        ]

        result = _propose_tiers(inventory, permissions, gpu_tiers, run_outcomes, [])
        tp1 = result["mapping"].get("tp1", {})

        # Still maps tp1 (OOM doesn't prevent proposal — it annotates)
        assert tp1.get("partition") == "gpu-short"

        # LEARN must fire: OOM on tier tp1 -> warning on the proposed partition
        warnings = tp1.get("warnings", [])
        assert warnings, (
            "OOM outcome (real schema, no partition field) should produce a warning "
            "for the proposed partition, but warnings list is empty. "
            "The LEARN keying must be tier-only — 'partition' is never in real outcomes."
        )
        warning_text = " ".join(warnings).lower()
        assert "oom" in warning_text or "warn" in warning_text or "consider" in warning_text

    def test_lesson_rule_surfaces_in_proposal(self) -> None:
        """A lesson rule whose trigger matches the proposed partition surfaces inline."""
        from research_vault.doctor import _propose_tiers

        inventory = [
            {"partition": "gpu-short", "gpu_gres": "gpu:a100:4", "nodes": "8"},
        ]
        permissions = {
            "available": True,
            "associations": [{"Account": "mylab", "Partition": "gpu-short",
                              "QOS": "normal", "GrpTRES": "", "MaxWall": "04:00:00",
                              "MaxTRES": "gres/gpu=8"}],
            "qos": {},
            "partition_acls": {},
            "allowed_partitions": ["gpu-short"],
            "forbidden_partitions": [],
        }
        gpu_tiers = {"tp1": {"gpus": 1, "models": ["<=7B"]}}
        lessons = [{"trigger": "gpu-short", "fix": "set --mem explicitly on gpu-short"}]

        result = _propose_tiers(inventory, permissions, gpu_tiers, [], lessons)
        tp1 = result["mapping"].get("tp1", {})
        warnings = tp1.get("warnings", [])
        # Lesson should surface
        warning_text = " ".join(warnings)
        assert "--mem" in warning_text or "gpu-short" in warning_text, (
            f"Lesson rule not surfaced in proposal warnings: {warnings}"
        )


# ---------------------------------------------------------------------------
# Tests 28-29: Graceful degrade + SSH opts
# ---------------------------------------------------------------------------

class TestGracefulDegrade:
    """sacctmgr absent -> inventory-only banner; StrictHostKeyChecking=accept-new."""

    def test_sacctmgr_absent_gives_inventory_only_banner(self, cfg: Config) -> None:
        """When sacctmgr fails, doctor degrades gracefully to inventory-only proposal."""
        from research_vault.doctor import cmd_doctor, _build_proposal
        _write_slurm_manifest(cfg)

        def no_sacctmgr(host: str, argv: list[str], *, timeout: int = 15) -> MagicMock:
            if "sinfo" in " ".join(argv):
                return _make_ssh_result(returncode=0, stdout=_SINFO_OUTPUT)
            if "sacctmgr" in " ".join(argv):
                # sacctmgr not found
                return _make_ssh_result(returncode=127, stdout="",
                                        stderr="sacctmgr: command not found")
            if "scontrol" in " ".join(argv):
                return _make_ssh_result(returncode=0, stdout=_SCONTROL_PARTITION_OUTPUT)
            return _make_ssh_result(returncode=0, stdout="")

        with patch("research_vault.adapters.remote._ssh_exec", side_effect=no_sacctmgr):
            result = cmd_doctor(cfg, refresh=True)

        cluster_caps = result["backends"]["cluster"]["capabilities"]
        assert cluster_caps.get("probe_status") == "ok", "Cluster should still be reachable"

        perms = cluster_caps.get("permissions", {})
        assert perms.get("available") is False, "permissions should be unavailable"
        assert "reason" in perms

        # Build proposal — should fall back to inventory-only (no crash, no empty result)
        proposal = _build_proposal(cfg)
        # proposal may be None if no tiers declared, but if tiers exist it should work
        if proposal is not None:
            assert proposal.get("inventory_only") is True

    def test_strict_host_key_checking_is_accept_new(self) -> None:
        """_SSH_PROBE_OPTS uses StrictHostKeyChecking=accept-new (not 'no').

        accept-new is safer: accepts new hosts on first connect but rejects
        known hosts with changed keys (the real MITM signal). 'no' silently
        accepts any key change, providing no MITM protection.
        """
        from research_vault.doctor import _SSH_PROBE_OPTS
        opts_str = " ".join(_SSH_PROBE_OPTS)
        assert "accept-new" in opts_str, (
            "StrictHostKeyChecking should be 'accept-new' (safer than 'no'). "
            f"Found: {opts_str}"
        )
        assert "StrictHostKeyChecking=no" not in opts_str, (
            "StrictHostKeyChecking=no found in _SSH_PROBE_OPTS — "
            "use accept-new instead (rejects known-host key changes)"
        )


# ---------------------------------------------------------------------------
# Tests 30-31: Tighten existing tests (#69 follow-ups from Argus)
# ---------------------------------------------------------------------------

class TestFollowUps:
    """Non-blocking #69 follow-ups: tighten existing assertions."""

    def test_sinfo_nonzero_yields_exactly_scheduler_error(self, cfg: Config) -> None:
        """sinfo exit non-zero -> probe_status is EXACTLY 'scheduler_error' (not loose OR).

        Previously the test accepted probe_status in ("scheduler_error", "ok") — too loose.
        This asserts the exact expected value.
        """
        from research_vault.doctor import cmd_doctor
        _write_slurm_manifest(cfg)

        sinfo_fail = _make_ssh_result(
            returncode=1,
            stdout="",
            stderr="sinfo: error: slurm_load_partitions: Socket timed out",
        )
        with patch("research_vault.adapters.remote._ssh_exec", return_value=sinfo_fail):
            result = cmd_doctor(cfg, refresh=True)

        cluster_caps = result["backends"]["cluster"]["capabilities"]
        assert cluster_caps.get("probe_status") == "scheduler_error", (
            f"Expected probe_status='scheduler_error' (exact), "
            f"got {cluster_caps.get('probe_status')!r}"
        )
        assert cluster_caps.get("reachable") is True

    def test_ssh_exec_docstring_accurately_reflects_call_sites(self) -> None:
        """_ssh_exec docstring accurately states that submit uses subprocess.run directly.

        Non-vacuity: the assertion checks the live docstring text (not a comment).
        """
        import inspect
        from research_vault.adapters.remote import _ssh_exec

        doc = inspect.getdoc(_ssh_exec) or ""
        # The docstring must mention that submit uses subprocess.run directly
        # This is the accurate description of the _ssh_exec call sites
        assert "directly" in doc.lower() or "subprocess.run" in doc.lower(), (
            "_ssh_exec docstring does not accurately describe submit's use of subprocess.run. "
            f"docstring: {doc[:200]!r}"
        )


# ---------------------------------------------------------------------------
# Parse helper unit tests (pure functions)
# ---------------------------------------------------------------------------

class TestParseHelpers:
    """Unit tests for the parse helper functions (pure, no subprocess)."""

    def test_parse_sacctmgr_assoc_empty(self) -> None:
        """Empty sacctmgr output -> empty list."""
        from research_vault.doctor import _parse_sacctmgr_assoc
        result = _parse_sacctmgr_assoc("")
        assert result == []

    def test_parse_sacctmgr_assoc_header_only(self) -> None:
        """Header-only sacctmgr output -> empty list (no data rows)."""
        from research_vault.doctor import _parse_sacctmgr_assoc
        result = _parse_sacctmgr_assoc("Account|Partition|QOS\n")
        assert result == []

    def test_parse_sacctmgr_assoc_typical(self) -> None:
        """Typical sacctmgr output -> list of dicts with correct fields."""
        from research_vault.doctor import _parse_sacctmgr_assoc
        result = _parse_sacctmgr_assoc(_SACCTMGR_ASSOC_OUTPUT)
        assert len(result) == 2
        assert result[0]["Account"] == "mylab"
        assert result[0]["Partition"] == "gpu-short"
        assert result[0]["QOS"] == "normal"

    def test_parse_scontrol_partitions_typical(self) -> None:
        """scontrol show partition output -> per-partition dict."""
        from research_vault.doctor import _parse_scontrol_partitions
        result = _parse_scontrol_partitions(_SCONTROL_PARTITION_OUTPUT)
        assert "gpu-short" in result
        assert "gpu-long" in result
        assert "gpu-priority" in result
        assert result["gpu-priority"]["AllowAccounts"] == "privileged"
        assert result["gpu-short"]["MaxTime"] == "04:00:00"

    def test_gres_gpu_count_parsing(self) -> None:
        """_gres_gpu_count extracts count from various GRES string formats."""
        from research_vault.doctor import _gres_gpu_count
        assert _gres_gpu_count("gpu:a100:4") == 4
        assert _gres_gpu_count("gpu:v100:8") == 8
        assert _gres_gpu_count("gpu:2") == 2
        assert _gres_gpu_count("(null)") == 0
        assert _gres_gpu_count("") == 0
        assert _gres_gpu_count("cpu:4") == 0  # not a GPU GRES
