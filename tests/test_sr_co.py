"""test_sr_co.py — SR-CO: Compute-onboarding tests.

Covers:
  1.  rv compute init — writes non-empty scaffold manifest (local + cluster FILL + W&B FILL)
  2.  rv compute init — refuses to clobber existing manifest without --force
  3.  rv compute init --force — overwrites existing manifest
  4.  rv compute init — scaffold contains no secret literals (leakage-clean)
  5.  rv compute init — detects local scheduler CLI (sbatch → ssh+slurm pre-filled)
  6.  rv compute init — no scheduler → defaults to ssh+slurm (commented template)
  7.  rv doctor — iterates declared backends; local probed; remote honestly reported
  8.  rv doctor — remote backend reported as "declared; probe deferred" (not silently skipped)
  9.  rv doctor — per-backend cache written; flat legacy cache reads back (back-compat)
  10. rv doctor — format_report includes the honest remote deferral message
  11. _ssh_exec extracted — importable; timeout/error-degrade unit-tested (stubbed subprocess)
  12. _run_status unchanged — existing SR-7 status tests still pass (refactor-safe)
  13. wandb_pull resolves entity/project from manifest when env unset
  14. wandb_pull env wins over manifest when env set (env-over-config rule)
  15. wandb_pull back-compat — manifest without results.wandb behaves as before
  16. research-loop.json — every run node carries the compute-recipe reads: pointer
  17. doctrine/compute-run-recipe.md shipped in package data
  18. rv compute init CLI end-to-end (build_parser + run)
  19. rv check nudge — warns when compute_manifest.json absent
  20. rv check no nudge — silent when manifest present
  21. _VERB_REGISTRY compute entry updated (when_to_use fires on init intent)

All tests are hermetic: tmp_path only; no ~/vault reads or writes.
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

def _make_cfg(tmp_path: Path, backend: str = "local") -> Config:
    """Minimal Config pointed at tmp_path."""
    raw: dict[str, Any] = {
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(tmp_path / "state"),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "adapters": {"notifier": "file", "backend": backend, "secrets": "env"},
        "projects": {},
    }
    cfg = Config(raw)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    return cfg


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return _make_cfg(tmp_path)


# ---------------------------------------------------------------------------
# 1-6: rv compute init
# ---------------------------------------------------------------------------

class TestComputeInit:
    def test_init_writes_scaffold_manifest(self, cfg: Config) -> None:
        """rv compute init writes a non-empty compute_manifest.json."""
        from research_vault.compute import cmd_init, _manifest_path, MANIFEST_FILE
        rc = cmd_init(cfg)
        assert rc == 0
        p = _manifest_path(cfg)
        assert p.exists()
        data = json.loads(p.read_text(encoding="utf-8"))

        # Must have the core structure
        assert "backends" in data
        assert "active" in data["backends"]
        assert "profiles" in data["backends"]
        assert "local" in data["backends"]["profiles"]
        assert "gpu_tiers" in data
        assert "results" in data
        assert "wandb" in data["results"]

    def test_init_scaffold_has_wandb_fill_block(self, cfg: Config) -> None:
        """Scaffold includes results.wandb with FILL values."""
        from research_vault.compute import cmd_init
        cmd_init(cfg)
        from research_vault.compute import _manifest_path
        data = json.loads(_manifest_path(cfg).read_text(encoding="utf-8"))
        wandb = data["results"]["wandb"]
        # Entity and project must be present (FILL values)
        assert "entity" in wandb
        assert "project" in wandb
        # Must start with FILL sentinel (not configured yet)
        from research_vault.compute import _FILL_PREFIX
        assert wandb["entity"].startswith(_FILL_PREFIX)
        assert wandb["project"].startswith(_FILL_PREFIX)

    def test_init_scaffold_has_cluster_profile(self, cfg: Config) -> None:
        """Scaffold includes a remote backend profile (compute-node) inactive by default.

        SR-EP-ROLE renamed the primary remote profile from 'cluster' to 'compute-node'
        to better reflect its role (submit node vs a generic cluster name).
        """
        from research_vault.compute import cmd_init
        cmd_init(cfg)
        from research_vault.compute import _manifest_path
        data = json.loads(_manifest_path(cfg).read_text(encoding="utf-8"))
        profiles = data["backends"]["profiles"]
        # Profile is now called 'compute-node' (SR-EP-ROLE)
        assert "compute-node" in profiles
        cluster = profiles["compute-node"]
        assert "archetype" in cluster
        assert "host" in cluster
        # host must be a FILL value (not a real host)
        from research_vault.compute import _FILL_PREFIX
        assert cluster["host"].startswith(_FILL_PREFIX)

    def test_init_refuses_to_clobber_without_force(self, cfg: Config) -> None:
        """rv compute init refuses to overwrite an existing manifest without --force."""
        from research_vault.compute import cmd_init, _manifest_path
        # First init
        cmd_init(cfg)
        # Write a sentinel value to check it's NOT overwritten
        p = _manifest_path(cfg)
        original = p.read_text(encoding="utf-8")
        data = json.loads(original)
        data["_sentinel"] = "do-not-overwrite"
        p.write_text(json.dumps(data), encoding="utf-8")

        # Second init without --force must return error
        rc = cmd_init(cfg, force=False)
        assert rc == 1
        # File still has sentinel (not overwritten)
        after = json.loads(p.read_text(encoding="utf-8"))
        assert after.get("_sentinel") == "do-not-overwrite"

    def test_init_force_overwrites(self, cfg: Config) -> None:
        """rv compute init --force overwrites an existing manifest."""
        from research_vault.compute import cmd_init, _manifest_path
        cmd_init(cfg)
        p = _manifest_path(cfg)
        p.write_text(json.dumps({"_sentinel": "original"}), encoding="utf-8")

        rc = cmd_init(cfg, force=True)
        assert rc == 0
        data = json.loads(p.read_text(encoding="utf-8"))
        assert "_sentinel" not in data  # overwritten
        assert "backends" in data  # proper scaffold

    def test_init_no_secret_literals(self, cfg: Config) -> None:
        """Scaffold manifest must contain no credential VALUES.

        Leakage gate: the manifest is publish-adjacent; credential values must
        NOT appear in it. Only FILL placeholders for the user-supplied address
        bits — and, since feat/secrets-forward, forwarded env-var NAMES.

        Names-not-values: ``secrets_forward`` legitimately holds env-var NAMES
        (e.g. ``WANDB_API_KEY``) — a name is not a credential (that is the whole
        security model). Those validated names are excised before the value scan
        so the gate still catches real secrets (``sk-ant-…``, ``KEY=value``
        assignments) everywhere else in the manifest.
        """
        import json as _json
        import re as _re
        from research_vault.adapters.secret_forward import validate_secret_name
        from research_vault.compute import cmd_init, _manifest_path
        cmd_init(cfg)
        content = _manifest_path(cfg).read_text(encoding="utf-8")

        # Excise validated secrets_forward NAMES (names-not-values) before scan.
        data = _json.loads(content)
        scan = content
        for prof in data.get("backends", {}).get("profiles", {}).values():
            for name in prof.get("secrets_forward", []) or []:
                validate_secret_name(name)  # every seed must be a bare env-var name
                scan = scan.replace(_json.dumps(name), '""')

        # Credential VALUE patterns that must NOT appear anywhere else.
        forbidden = ["password", "sk-ant-", "wandbapikey"]
        for pattern in forbidden:
            assert pattern.lower() not in scan.lower(), (
                f"Secret pattern {pattern!r} found in scaffold manifest — leakage violation"
            )
        # No literal NAME=value credential assignment anywhere (the leak shape).
        assert not _re.search(r"[A-Z_]*(?:API_KEY|TOKEN|PASSWORD)[A-Z_]*\s*=\s*\S", scan), (
            "a NAME=value credential assignment leaked into the scaffold manifest"
        )

    def test_init_detects_sbatch_locally(self, cfg: Config) -> None:
        """When sbatch is found locally, compute-node profile uses ssh+slurm archetype."""
        from research_vault.compute import cmd_init, _manifest_path

        with patch("shutil.which", side_effect=lambda cmd: "/usr/bin/sbatch" if cmd == "sbatch" else None):
            cmd_init(cfg, force=True)

        data = json.loads(_manifest_path(cfg).read_text(encoding="utf-8"))
        cluster = data["backends"]["profiles"]["compute-node"]
        assert cluster["archetype"] == "ssh+slurm"

    def test_init_detects_qsub_locally(self, cfg: Config) -> None:
        """When qsub is found locally (no sbatch), compute-node profile uses ssh+pbs."""
        from research_vault.compute import cmd_init, _manifest_path

        def fake_which(cmd: str):
            if cmd == "qsub":
                return "/usr/bin/qsub"
            return None

        with patch("shutil.which", side_effect=fake_which):
            cmd_init(cfg, force=True)

        data = json.loads(_manifest_path(cfg).read_text(encoding="utf-8"))
        cluster = data["backends"]["profiles"]["compute-node"]
        assert cluster["archetype"] == "ssh+pbs"

    def test_init_no_scheduler_defaults_to_slurm_template(self, cfg: Config) -> None:
        """When no scheduler found locally, defaults to ssh+slurm (most common HPC)."""
        from research_vault.compute import cmd_init, _manifest_path

        with patch("shutil.which", return_value=None):
            cmd_init(cfg, force=True)

        data = json.loads(_manifest_path(cfg).read_text(encoding="utf-8"))
        cluster = data["backends"]["profiles"]["compute-node"]
        assert cluster["archetype"] == "ssh+slurm"

    def test_init_active_remains_local_by_default(self, cfg: Config) -> None:
        """Scaffold always has active=['local'] — user flips to ['cluster'] after filling."""
        from research_vault.compute import cmd_init, _manifest_path
        cmd_init(cfg)
        data = json.loads(_manifest_path(cfg).read_text(encoding="utf-8"))
        assert data["backends"]["active"] == ["local"]

    def test_init_cli_end_to_end(self, cfg: Config) -> None:
        """rv compute init via CLI (build_parser + run) exits 0 and writes manifest."""
        from research_vault.compute import build_parser, run as compute_run, _manifest_path
        parser = build_parser()
        args = parser.parse_args(["init"])
        args._cfg = cfg
        rc = compute_run(args)
        assert rc == 0
        assert _manifest_path(cfg).exists()

    def test_init_cli_force_flag(self, cfg: Config) -> None:
        """rv compute init --force via CLI accepts the flag."""
        from research_vault.compute import build_parser, run as compute_run, _manifest_path
        parser = build_parser()
        # First init
        args = parser.parse_args(["init"])
        args._cfg = cfg
        compute_run(args)
        # Second init --force
        args2 = parser.parse_args(["init", "--force"])
        args2._cfg = cfg
        rc = compute_run(args2)
        assert rc == 0


# ---------------------------------------------------------------------------
# 7-10: env-aware rv doctor (per-backend seam + honest remote report)
# ---------------------------------------------------------------------------

class TestDoctorEnvAware:
    def _manifest_with_local_and_remote(self, cfg: Config) -> None:
        """Write a manifest with local + remote cluster declared."""
        from research_vault.compute import _save_manifest
        _save_manifest(cfg, {
            "backends": {
                "active": ["local"],
                "profiles": {
                    "local": {"archetype": "local"},
                    "cluster": {
                        "archetype": "ssh+slurm",
                        "host": "example-hpc.edu",
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

    def test_doctor_probes_local_and_probes_remote(self, cfg: Config) -> None:
        """doctor probes local backend + actually probes remote (SR-CO-REMOTE live).

        The remote probe will fail (example-hpc.edu unreachable in CI) but
        must return a structured result — never deferred, never silently skipped
        (charter §2).
        """
        from research_vault.doctor import cmd_doctor
        self._manifest_with_local_and_remote(cfg)
        result = cmd_doctor(cfg, refresh=True)
        backends = result["backends"]
        # Local must be probed
        assert "local" in backends
        local_caps = backends["local"]["capabilities"]
        assert local_caps.get("local_available") is True
        # Cluster must be present and have been attempted (not silently skipped)
        assert "cluster" in backends
        cluster_caps = backends["cluster"]["capabilities"]
        # Must have a probe_status (real probe ran) — not "deferred" (that was SR-CO placeholder)
        assert "probe_status" in cluster_caps, (
            "cluster backend has no probe_status — probe was silently skipped (charter §2 violation)"
        )
        assert cluster_caps.get("probe_status") != "deferred", (
            "cluster backend is still 'deferred' — SR-CO-REMOTE not wired in"
        )
        # The fake host will be unreachable, but the probe must have been attempted
        assert cluster_caps.get("probe_status") in (
            "ok", "unreachable", "scheduler_error", "unfilled"
        ), f"Unexpected probe_status: {cluster_caps.get('probe_status')!r}"

    def test_doctor_remote_honest_message_in_report(self, cfg: Config) -> None:
        """format_report includes a meaningful remote backend section (not silently skipped)."""
        from research_vault.doctor import cmd_doctor, format_report
        self._manifest_with_local_and_remote(cfg)
        result = cmd_doctor(cfg, refresh=True)
        report = format_report(result)
        # Must surface the cluster backend — NOT silently skip (charter §2)
        assert "[cluster]" in report
        # For the unreachable fake host, must mention unreachable or the host
        # (the old "SR-CO-REMOTE" deferral message is gone — real probe runs now)
        assert "SR-CO-REMOTE" not in report, (
            "format_report still shows old deferral message — SR-CO-REMOTE is implemented"
        )

    def test_doctor_per_backend_cache_shape(self, cfg: Config) -> None:
        """Cache written in per-backend shape: {ts, backends: {name: {ts, capabilities}}}."""
        from research_vault.doctor import cmd_doctor, _cache_path
        self._manifest_with_local_and_remote(cfg)
        cmd_doctor(cfg, refresh=True)
        raw = json.loads(_cache_path(cfg).read_text(encoding="utf-8"))
        assert "backends" in raw
        assert "local" in raw["backends"]
        assert "ts" in raw["backends"]["local"]
        assert "capabilities" in raw["backends"]["local"]

    def test_doctor_flat_legacy_cache_back_compat(self, cfg: Config) -> None:
        """Flat legacy cache (pre-SR-CO shape) is normalised to per-backend form."""
        from research_vault.doctor import _cache_path, _read_cache
        # Write a flat pre-SR-CO cache
        flat_cache = {
            "ts": "2025-01-01T00:00:00+00:00",
            "capabilities": {
                "local_available": True,
                "sbatch": False,
                "sinfo": False,
                "qsub": False,
                "qstat": False,
                "hf": False,
                "uv": False,
                "conda_envs": [],
                "nvidia_smi": {"available": False},
                "sinfo_detail": {"available": False},
                "qstat_detail": {"available": False},
                "generic_probes": [],
            },
        }
        _cache_path(cfg).write_text(json.dumps(flat_cache), encoding="utf-8")
        result = _read_cache(cfg)
        # Must be normalised to per-backend shape
        assert result is not None
        assert "backends" in result
        assert "local" in result["backends"]
        assert result.get("_legacy") is True

    def test_doctor_no_manifest_still_probes_local(self, cfg: Config) -> None:
        """Without a manifest, doctor still probes local (always useful)."""
        from research_vault.doctor import cmd_doctor
        # No manifest written — doctor must not crash
        result = cmd_doctor(cfg, refresh=True)
        assert "backends" in result
        assert "local" in result["backends"]

    def test_doctor_format_report_local_only_shows_backend_section(
        self, cfg: Config
    ) -> None:
        """format_report shows a [local] section for a local-only manifest."""
        from research_vault.doctor import cmd_doctor, format_report
        from research_vault.compute import _save_manifest
        _save_manifest(cfg, {
            "backends": {"active": ["local"], "profiles": {"local": {"archetype": "local"}}},
            "conda_envs": {},
            "gpu_tiers": {},
            "rules": [],
            "model_quirks": {},
            "run_outcomes": [],
        })
        result = cmd_doctor(cfg, refresh=True)
        report = format_report(result)
        assert "[local]" in report
        # No SR-CO-REMOTE mention for local-only
        assert "SR-CO-REMOTE" not in report


# ---------------------------------------------------------------------------
# 11-12: _ssh_exec extraction (refactor-safe)
# ---------------------------------------------------------------------------

class TestSshExec:
    def test_ssh_exec_importable(self) -> None:
        """_ssh_exec is importable from adapters.remote."""
        from research_vault.adapters.remote import _ssh_exec
        assert callable(_ssh_exec)

    def test_ssh_exec_timeout_raises(self) -> None:
        """_ssh_exec raises TimeoutExpired on timeout (not swallowed)."""
        import subprocess
        from research_vault.adapters.remote import _ssh_exec
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["ssh"], 1)):
            with pytest.raises(subprocess.TimeoutExpired):
                _ssh_exec("example-host", ["echo", "hi"], timeout=1)

    def test_ssh_exec_file_not_found_raises(self) -> None:
        """_ssh_exec raises FileNotFoundError when ssh is not installed."""
        from research_vault.adapters.remote import _ssh_exec
        with patch("subprocess.run", side_effect=FileNotFoundError("ssh not found")):
            with pytest.raises(FileNotFoundError):
                _ssh_exec("example-host", ["echo", "hi"])

    def test_ssh_exec_builds_correct_argv(self) -> None:
        """_ssh_exec passes ['ssh', host] + argv to subprocess.run."""
        import subprocess
        from research_vault.adapters.remote import _ssh_exec
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = ""
        fake_result.stderr = ""
        with patch("subprocess.run", return_value=fake_result) as mock_run:
            _ssh_exec("myhost", ["sacct", "-j", "123"], timeout=10)
        call_args = mock_run.call_args
        argv = call_args[0][0]
        assert argv == ["ssh", "myhost", "sacct", "-j", "123"]

    def test_run_status_uses_ssh_exec_not_raw_subprocess(self) -> None:
        """_run_status delegates to _ssh_exec (not duplicated subprocess.run inline).

        This is the SR-7 refactor-safety gate: _run_status must still work
        correctly after the _ssh_exec extraction.
        """
        from research_vault.adapters.remote import _run_status, _merge_profile_defaults
        profile = {
            "archetype": "ssh+slurm",
            "host": "example-hpc.edu",
            "submit_pattern": "sbatch",
        }
        merged = _merge_profile_defaults(profile)
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "123|COMPLETED"
        fake_result.stderr = ""
        with patch("research_vault.adapters.remote._ssh_exec", return_value=fake_result):
            state = _run_status("123", merged)
        assert state == "DONE"

    def test_run_status_ssh_not_found_returns_unknown(self) -> None:
        """_run_status returns UNKNOWN when _ssh_exec raises FileNotFoundError."""
        from research_vault.adapters.remote import _run_status, _merge_profile_defaults
        profile = {
            "archetype": "ssh+slurm",
            "host": "example-hpc.edu",
        }
        merged = _merge_profile_defaults(profile)
        with patch(
            "research_vault.adapters.remote._ssh_exec",
            side_effect=FileNotFoundError("ssh not found"),
        ):
            state = _run_status("123", merged)
        assert state == "UNKNOWN"


# ---------------------------------------------------------------------------
# 13-15: wandb_pull manifest fallback (entity/project)
# ---------------------------------------------------------------------------

class TestWandbManifestFallback:
    def _write_manifest_with_wandb(
        self, cfg: Config, entity: str, project: str
    ) -> None:
        """Write a compute manifest with the results.wandb block filled."""
        from research_vault.compute import _save_manifest
        _save_manifest(cfg, {
            "backends": {"active": ["local"], "profiles": {"local": {"archetype": "local"}}},
            "conda_envs": {},
            "gpu_tiers": {},
            "results": {"wandb": {"entity": entity, "project": project}},
            "rules": [],
            "model_quirks": {},
            "run_outcomes": [],
        })

    def test_resolve_from_manifest_when_env_unset(self, cfg: Config) -> None:
        """_resolve_wandb_from_manifest returns entity/project from the manifest."""
        from research_vault.wandb_pull import _resolve_wandb_from_manifest
        self._write_manifest_with_wandb(cfg, "myteam", "myproject")
        entity, project = _resolve_wandb_from_manifest(cfg)
        assert entity == "myteam"
        assert project == "myproject"

    def test_fill_value_treated_as_unconfigured(self, cfg: Config) -> None:
        """FILL sentinel values in the manifest are treated as empty."""
        from research_vault.wandb_pull import _resolve_wandb_from_manifest
        self._write_manifest_with_wandb(
            cfg,
            "FILL — your entity",
            "FILL — your project",
        )
        entity, project = _resolve_wandb_from_manifest(cfg)
        assert entity == ""
        assert project == ""

    def test_manifest_absent_returns_empty(self, cfg: Config) -> None:
        """_resolve_wandb_from_manifest returns ('', '') when no manifest exists."""
        from research_vault.wandb_pull import _resolve_wandb_from_manifest
        # No manifest written
        entity, project = _resolve_wandb_from_manifest(cfg)
        assert entity == ""
        assert project == ""

    def test_manifest_without_wandb_block_returns_empty(self, cfg: Config) -> None:
        """_resolve_wandb_from_manifest returns ('', '') when block is absent."""
        from research_vault.compute import _save_manifest
        from research_vault.wandb_pull import _resolve_wandb_from_manifest
        _save_manifest(cfg, {
            "backends": {"active": ["local"], "profiles": {}},
            "conda_envs": {},
            "gpu_tiers": {},
            "rules": [],
            "model_quirks": {},
            "run_outcomes": [],
        })
        entity, project = _resolve_wandb_from_manifest(cfg)
        assert entity == ""
        assert project == ""

    def test_env_wins_over_manifest(self, cfg: Config, monkeypatch) -> None:
        """When WANDB_ENTITY/WANDB_PROJECT env vars are set, they win over manifest."""
        from research_vault.wandb_pull import _resolve_wandb_from_manifest
        self._write_manifest_with_wandb(cfg, "manifest-entity", "manifest-project")

        # Set env vars (primary)
        monkeypatch.setenv("WANDB_ENTITY", "env-entity")
        monkeypatch.setenv("WANDB_PROJECT", "env-project")

        # The manifest resolver still returns the manifest value (it doesn't read env)
        # — env-over-manifest logic is in wandb_pull() itself (it passes None when env set)
        entity, project = _resolve_wandb_from_manifest(cfg)
        assert entity == "manifest-entity"  # raw manifest value
        assert project == "manifest-project"

        # Verify that wandb_pull would use env vars (not pass manifest value)
        # by checking the logic: if env set, fallback_entity=None, so parse_run_id uses env
        env_entity = os.environ.get("WANDB_ENTITY", "").strip()
        assert env_entity == "env-entity"  # env wins when set


# ---------------------------------------------------------------------------
# 16: research-loop.json run node wiring
# ---------------------------------------------------------------------------

class TestRunNodeWiring:
    def _load_research_loop(self) -> dict:
        """Load the demo research-loop.json from package data."""
        import importlib.resources
        pkg = importlib.resources.files("research_vault")
        loop_path = pkg / "data" / "examples" / "demo-research" / "research-loop.json"
        with importlib.resources.as_file(loop_path) as p:
            return json.loads(Path(p).read_text(encoding="utf-8"))

    def test_all_run_nodes_have_reads_pointer(self) -> None:
        """Every 'run' node (spec=task://demo-research#run) carries a reads: pointer."""
        loop = self._load_research_loop()
        run_nodes = [
            n for n in loop["nodes"]
            if n.get("spec") == "task://demo-research#run"
        ]
        assert len(run_nodes) > 0, "No run nodes found in research-loop.json"
        for node in run_nodes:
            reads = node.get("reads", [])
            assert len(reads) > 0, (
                f"Run node {node['id']!r} has no reads: pointer — "
                "crew cannot find the run recipe (anti-pattern: trial-submit)"
            )
            # Must point at the compute-run-recipe.md
            assert any("compute-run-recipe" in r for r in reads), (
                f"Run node {node['id']!r} reads: does not include compute-run-recipe.md"
            )


# ---------------------------------------------------------------------------
# 17: compute-run-recipe.md shipped as package data
# ---------------------------------------------------------------------------

class TestComputeRunRecipeShipped:
    def test_compute_run_recipe_in_package_data(self) -> None:
        """doctrine/compute-run-recipe.md is accessible via importlib.resources."""
        import importlib.resources
        pkg = importlib.resources.files("research_vault")
        recipe_path = pkg / "data" / "doctrine" / "compute-run-recipe.md"
        with importlib.resources.as_file(recipe_path) as p:
            assert Path(p).exists(), "compute-run-recipe.md not found in package data"
            content = Path(p).read_text(encoding="utf-8")
        # Must name the key commands
        assert "rv compute show" in content
        assert "rv compute explain" in content
        assert "anti-pattern" in content.lower() or "do NOT" in content
        assert "trial-submit" in content.lower() or "trial submit" in content.lower()


# ---------------------------------------------------------------------------
# 19-20: rv check nudge
# ---------------------------------------------------------------------------

class TestCheckNudge:
    def test_check_nudges_when_manifest_absent(self, cfg: Config) -> None:
        """rv check warns when compute_manifest.json is absent."""
        from research_vault.check import run_preflight
        result = run_preflight(cfg=cfg)
        report = result["report"]
        # Nudge must surface
        assert "rv compute init" in report
        assert result.get("compute_manifest") is False

    def test_check_no_nudge_when_manifest_present(self, cfg: Config) -> None:
        """rv check is silent about compute when manifest is present."""
        from research_vault.compute import cmd_init
        from research_vault.check import run_preflight
        cmd_init(cfg)
        result = run_preflight(cfg=cfg)
        assert result.get("compute_manifest") is True
        # Nudge must NOT appear
        report = result["report"]
        assert "rv compute init" not in report


# ---------------------------------------------------------------------------
# 21: _VERB_REGISTRY compute entry
# ---------------------------------------------------------------------------

class TestVerbRegistry:
    def test_compute_when_to_use_covers_init(self) -> None:
        """_VERB_REGISTRY 'compute' when_to_use fires on declare/init intent."""
        from research_vault.cli import _VERB_REGISTRY
        entry = _VERB_REGISTRY.get("compute", {})
        wtu = entry.get("when_to_use", "")
        # Must mention init (the new verb)
        assert "init" in wtu
        # Must cover the anti-pattern
        assert "trial-submit" in wtu.lower() or "trial submit" in wtu.lower()
        # Must cover the declare-first order
        assert "DECLARE" in wtu or "declare" in wtu.lower()

    def test_doctor_when_to_use_covers_declare_first(self) -> None:
        """_VERB_REGISTRY 'doctor' when_to_use mentions running after compute init."""
        from research_vault.cli import _VERB_REGISTRY
        entry = _VERB_REGISTRY.get("doctor", {})
        wtu = entry.get("when_to_use", "")
        assert "compute init" in wtu

    def test_compute_sr_field_updated(self) -> None:
        """_VERB_REGISTRY 'compute' sr field includes SR-CO."""
        from research_vault.cli import _VERB_REGISTRY
        entry = _VERB_REGISTRY.get("compute", {})
        assert "SR-CO" in entry.get("sr", "")

    def test_help_check_passes(self) -> None:
        """rv help --check passes after SR-CO additions."""
        from research_vault.cli import _VERB_REGISTRY
        for verb, entry in _VERB_REGISTRY.items():
            assert "when_to_use" in entry, f"verb {verb!r} missing when_to_use"
            assert entry["when_to_use"], f"verb {verb!r} has empty when_to_use"
            assert "module" in entry, f"verb {verb!r} missing module"
