"""test_sr6.py — SR-6: COMPUTE-DISCOVERY layer tests.

Covers:
  1. rv compute show — prints the declared run-recipe
  2. rv doctor — probes + caches capabilities; second call reads cache (no re-probe)
  3. rv doctor — degrades gracefully without scheduler (no traceback)
  4. rv plugins list — surfaces static adapters + config-selected ones
  5. rv compute lesson add — appends a rule that rv compute show then displays
  6. rv compute explain <job> — resolves env/tier/flags from manifest
  7. Outcome-capture — appends a run result to the manifest
  8. _VERB_REGISTRY entries for all SR-6 verbs (when_to_use + sr="SR-6")
  9. rv help --check passes after SR-6 verb registration

All tests are hermetic: tmp_path only; no ~/vault reads or writes.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import Config, reset_config_cache


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_path: Path, monkeypatch) -> Config:
    """Minimal Config wired to tmp_path; state_dir exists."""
    config_file = tmp_path / "research_vault.toml"
    config_file.write_text(
        f"""
instance_root = "{tmp_path}"
notes_root = "{tmp_path / 'notes'}"
state_dir = "{tmp_path / 'state'}"
agents_dir = "{tmp_path / '.agents'}"
tasks_dir = "{tmp_path / 'tasks'}"
control_dir = "{tmp_path / 'control'}"

[adapters]
notifier = "file"
backend = "local"
secrets = "env"

[projects.demo-research]
source_dir = "{tmp_path / 'projects' / 'demo-research'}"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(config_file))
    reset_config_cache()
    from research_vault.config import load_config
    c = load_config(reload=True)
    c.state_dir.mkdir(parents=True, exist_ok=True)
    yield c
    reset_config_cache()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manifest(cfg: Config, **overrides) -> dict:
    """Write a minimal manifest to state_dir and return it."""
    from research_vault.compute import _default_manifest, _manifest_path, _save_manifest
    m = _default_manifest()
    m.update(overrides)
    _save_manifest(cfg, m)
    return m


# ---------------------------------------------------------------------------
# 1. rv compute show — prints the declared run-recipe
# ---------------------------------------------------------------------------

class TestComputeShow:
    def test_show_prints_backends(self, cfg: Config, capsys) -> None:
        """rv compute show prints active backends from the manifest."""
        from research_vault.compute import cmd_show
        _make_manifest(cfg, backends={"active": ["local"], "profiles": {
            "local": {"archetype": "local"}
        }})
        result = cmd_show(cfg)
        assert result == 0
        out = capsys.readouterr().out
        assert "local" in out

    def test_show_prints_conda_envs(self, cfg: Config, capsys) -> None:
        """rv compute show lists declared conda envs."""
        from research_vault.compute import cmd_show
        _make_manifest(cfg, conda_envs={"my-env": {"purpose": "vLLM inference"}})
        cmd_show(cfg)
        out = capsys.readouterr().out
        assert "my-env" in out
        assert "vLLM inference" in out

    def test_show_prints_gpu_tiers(self, cfg: Config, capsys) -> None:
        """rv compute show lists GPU tiers."""
        from research_vault.compute import cmd_show
        _make_manifest(cfg, gpu_tiers={"tp1": {"gpus": 1, "models": ["<=7B"]},
                                        "tp4": {"gpus": 4, "models": ["70B"]}})
        cmd_show(cfg)
        out = capsys.readouterr().out
        assert "tp1" in out
        assert "tp4" in out

    def test_show_prints_rules(self, cfg: Config, capsys) -> None:
        """rv compute show prints declared rules (captured gotchas)."""
        from research_vault.compute import cmd_show
        _make_manifest(cfg, rules=[
            {"trigger": "download >10GB", "fix": "Use sbatch, not login-node nohup"}
        ])
        cmd_show(cfg)
        out = capsys.readouterr().out
        assert "download >10GB" in out
        assert "sbatch" in out

    def test_show_no_manifest_returns_helpful_message(self, cfg: Config, capsys) -> None:
        """rv compute show with no manifest shows a helpful scaffold message."""
        from research_vault.compute import cmd_show
        # No manifest exists
        result = cmd_show(cfg)
        assert result == 0
        out = capsys.readouterr().out
        # Should show something — at minimum backends or a scaffold message
        assert len(out) > 0

    def test_show_prints_model_quirks(self, cfg: Config, capsys) -> None:
        """rv compute show prints per-model quirks."""
        from research_vault.compute import cmd_show
        _make_manifest(cfg, model_quirks={
            "llama-3-70b": {"tp": 4, "flashinfer_cache": "/scratch/flashinfer"}
        })
        cmd_show(cfg)
        out = capsys.readouterr().out
        assert "llama-3-70b" in out


# ---------------------------------------------------------------------------
# 2. rv doctor — probes + caches; second call reads cache
# ---------------------------------------------------------------------------

class TestDoctor:
    def test_doctor_runs_and_caches(self, cfg: Config) -> None:
        """First rv doctor call probes capabilities and writes cache (per-backend shape)."""
        from research_vault.doctor import cmd_doctor, _CACHE_FILE
        result = cmd_doctor(cfg, refresh=True)
        # SR-CO: result uses per-backend shape ("backends" key, not flat "capabilities")
        assert "backends" in result
        assert result.get("from_cache") is False
        # Cache file should now exist
        cache_path = cfg.state_dir / _CACHE_FILE
        assert cache_path.exists()

    def test_doctor_second_call_reads_cache(self, cfg: Config) -> None:
        """Second rv doctor call reads from cache (no re-probe)."""
        from research_vault.doctor import cmd_doctor, _CACHE_FILE
        # First call — writes cache
        r1 = cmd_doctor(cfg, refresh=True)
        ts1 = r1["ts"]

        # Second call — should read cache (same timestamp)
        r2 = cmd_doctor(cfg)
        assert r2.get("from_cache") is True
        assert r2["ts"] == ts1  # same timestamp means from cache

    def test_doctor_refresh_re_probes(self, cfg: Config) -> None:
        """rv doctor --refresh forces a new probe (overwrites cache); from_cache=False."""
        from research_vault.doctor import cmd_doctor, _CACHE_FILE
        r1 = cmd_doctor(cfg, refresh=True)
        r2 = cmd_doctor(cfg, refresh=True)
        assert r2.get("from_cache") is False

    def test_doctor_capabilities_have_expected_keys(self, cfg: Config) -> None:
        """Doctor capabilities dict has the expected probe keys (per-backend shape)."""
        from research_vault.doctor import cmd_doctor
        result = cmd_doctor(cfg, refresh=True)
        # SR-CO: per-backend shape; local caps are under backends["local"]["capabilities"]
        backends = result["backends"]
        assert "local" in backends
        caps = backends["local"]["capabilities"]
        # Always-present keys (local backend)
        assert "sbatch" in caps
        assert "sinfo" in caps
        assert "qsub" in caps
        assert "qstat" in caps
        assert "hf" in caps
        assert "uv" in caps
        assert "conda_envs" in caps

    def test_doctor_local_always_available(self, cfg: Config) -> None:
        """local backend is always available — doctor reports it under backends['local']."""
        from research_vault.doctor import cmd_doctor
        result = cmd_doctor(cfg, refresh=True)
        backends = result["backends"]
        assert "local" in backends
        caps = backends["local"]["capabilities"]
        # local archetype: always available
        assert caps.get("local_available") is True


# ---------------------------------------------------------------------------
# 3. rv doctor — degrades gracefully without scheduler
# ---------------------------------------------------------------------------

class TestDoctorDegrade:
    def test_doctor_no_scheduler_no_traceback(self, cfg: Config, capsys) -> None:
        """With no scheduler CLIs present, rv doctor completes without raising."""
        from research_vault.doctor import cmd_doctor

        def fake_which(cmd: str):
            if cmd in ("sbatch", "sinfo", "qsub", "qstat"):
                return None
            return f"/usr/bin/{cmd}"

        with patch("shutil.which", side_effect=fake_which):
            result = cmd_doctor(cfg, refresh=True)

        # No traceback — result is a dict with per-backend shape
        assert isinstance(result, dict)
        # SR-CO: local caps are under backends["local"]["capabilities"]
        backends = result["backends"]
        assert "local" in backends
        caps = backends["local"]["capabilities"]
        assert caps["sbatch"] is False
        assert caps["sinfo"] is False
        assert caps["qsub"] is False
        assert caps["qstat"] is False

    def test_doctor_reports_scheduler_not_available(self, cfg: Config, capsys) -> None:
        """cmd_doctor_report shows 'not available' for missing schedulers (no traceback)."""
        from research_vault.doctor import cmd_doctor, format_report

        def fake_which(cmd: str):
            if cmd in ("sbatch", "sinfo", "qsub", "qstat"):
                return None
            return f"/usr/bin/{cmd}"

        with patch("shutil.which", side_effect=fake_which):
            result = cmd_doctor(cfg, refresh=True)

        report = format_report(result)
        assert "not available" in report.lower() or "slurm" in report.lower()

    def test_doctor_cli_no_scheduler_exit_zero(self, cfg: Config, monkeypatch) -> None:
        """rv doctor exits 0 even when no scheduler present."""
        from research_vault.doctor import build_parser, run as doctor_run

        def fake_which(cmd: str):
            if cmd in ("sbatch", "sinfo", "qsub", "qstat"):
                return None
            return f"/usr/bin/{cmd}"

        with patch("shutil.which", side_effect=fake_which):
            parser = build_parser()
            args = parser.parse_args([])
            # Inject cfg — doctor reads config via load_config
            args._cfg = cfg
            code = doctor_run(args)
        assert code == 0


# ---------------------------------------------------------------------------
# 4. rv plugins list — surfaces static adapters + config-selected ones
# ---------------------------------------------------------------------------

class TestPluginsList:
    def test_plugins_list_shows_registries(self, cfg: Config, capsys) -> None:
        """rv plugins list shows all three static registries."""
        from research_vault.plugins import cmd_plugins_list
        result = cmd_plugins_list(cfg)
        assert "notifiers" in result
        assert "backends" in result
        assert "secrets" in result
        assert "file" in result["notifiers"]
        assert "local" in result["backends"]
        assert "env" in result["secrets"]

    def test_plugins_list_shows_active_adapters(self, cfg: Config) -> None:
        """rv plugins list shows the config-selected (active) adapters."""
        from research_vault.plugins import cmd_plugins_list
        result = cmd_plugins_list(cfg)
        assert "active" in result
        assert result["active"]["notifier"] == "file"
        assert result["active"]["backend"] == "local"
        assert result["active"]["secrets"] == "env"

    def test_plugins_list_cli_output(self, cfg: Config, capsys) -> None:
        """rv plugins list CLI prints adapter info."""
        from research_vault.plugins import build_parser, run as plugins_run
        parser = build_parser()
        args = parser.parse_args(["list"])
        args._cfg = cfg
        code = plugins_run(args)
        out = capsys.readouterr().out
        assert code == 0
        assert "file" in out
        assert "local" in out
        assert "env" in out


# ---------------------------------------------------------------------------
# 5. rv compute lesson add — appends rule; rv compute show displays it
# ---------------------------------------------------------------------------

class TestLessonAdd:
    def test_lesson_add_appends_rule(self, cfg: Config) -> None:
        """rv compute lesson add appends a rule to the manifest."""
        from research_vault.compute import cmd_lesson_add, _load_manifest
        _make_manifest(cfg)
        cmd_lesson_add(cfg, trigger="download >10GB", fix="Use sbatch not nohup")
        m = _load_manifest(cfg)
        assert any(
            r.get("trigger") == "download >10GB" for r in m.get("rules", [])
        )

    def test_lesson_add_multiple_rules(self, cfg: Config) -> None:
        """Multiple lesson adds accumulate all rules (no overwrite)."""
        from research_vault.compute import cmd_lesson_add, _load_manifest
        _make_manifest(cfg)
        cmd_lesson_add(cfg, trigger="A", fix="fix-A")
        cmd_lesson_add(cfg, trigger="B", fix="fix-B")
        m = _load_manifest(cfg)
        triggers = [r["trigger"] for r in m.get("rules", [])]
        assert "A" in triggers
        assert "B" in triggers

    def test_show_displays_added_lesson(self, cfg: Config, capsys) -> None:
        """rv compute show displays rules added via rv compute lesson add."""
        from research_vault.compute import cmd_lesson_add, cmd_show
        _make_manifest(cfg)
        cmd_lesson_add(cfg, trigger="never run downloads on login node", fix="use sbatch --wrap")
        cmd_show(cfg)
        out = capsys.readouterr().out
        assert "never run downloads on login node" in out


# ---------------------------------------------------------------------------
# 6. rv compute explain <job> — resolves env/tier/flags
# ---------------------------------------------------------------------------

class TestComputeExplain:
    def test_explain_resolves_model_quirks(self, cfg: Config, capsys) -> None:
        """rv compute explain resolves per-model quirks from the manifest."""
        from research_vault.compute import cmd_explain
        _make_manifest(cfg, model_quirks={
            "llama-3-70b": {"tp": 4, "conda_env": "my-env",
                            "flashinfer_cache": "/scratch/flashinfer"}
        })
        result = cmd_explain(cfg, job="llama-3-70b")
        assert result is not None
        assert result.get("tp") == 4
        assert result.get("conda_env") == "my-env"

    def test_explain_resolves_gpu_tier(self, cfg: Config) -> None:
        """rv compute explain resolves the GPU tier for a known model."""
        from research_vault.compute import cmd_explain
        _make_manifest(cfg,
            gpu_tiers={"tp4": {"gpus": 4, "models": ["70B"]}},
            model_quirks={"llama-3-70b": {"tp": 4, "tier": "tp4"}}
        )
        result = cmd_explain(cfg, job="llama-3-70b")
        assert result.get("tier") == "tp4"

    def test_explain_unknown_job_returns_defaults(self, cfg: Config) -> None:
        """rv compute explain with unknown job returns defaults (not an error)."""
        from research_vault.compute import cmd_explain
        _make_manifest(cfg)
        result = cmd_explain(cfg, job="unknown-model-xyz")
        assert result is not None  # graceful

    def test_explain_cli_prints_output(self, cfg: Config, capsys) -> None:
        """rv compute explain via CLI prints resolved config."""
        from research_vault.compute import build_parser, run as compute_run
        _make_manifest(cfg, model_quirks={
            "my-model": {"tp": 2, "conda_env": "test-env"}
        })
        parser = build_parser()
        args = parser.parse_args(["explain", "my-model"])
        args._cfg = cfg
        code = compute_run(args)
        out = capsys.readouterr().out
        assert code == 0
        assert "my-model" in out or "test-env" in out or "tp" in out


# ---------------------------------------------------------------------------
# 7. Outcome-capture — appends run result to manifest
# ---------------------------------------------------------------------------

class TestOutcomeCapture:
    def test_outcome_add_appends_to_manifest(self, cfg: Config) -> None:
        """rv compute outcome add appends a run outcome to the manifest."""
        from research_vault.compute import cmd_outcome_add, _load_manifest
        _make_manifest(cfg)
        cmd_outcome_add(cfg, job="llama-70b-eval", tier="tp2", result="OOM")
        m = _load_manifest(cfg)
        outcomes = m.get("run_outcomes", [])
        assert len(outcomes) == 1
        assert outcomes[0]["job"] == "llama-70b-eval"
        assert outcomes[0]["tier"] == "tp2"
        assert outcomes[0]["result"] == "OOM"

    def test_outcome_add_multiple(self, cfg: Config) -> None:
        """Multiple outcome adds accumulate (no overwrite)."""
        from research_vault.compute import cmd_outcome_add, _load_manifest
        _make_manifest(cfg)
        cmd_outcome_add(cfg, job="run-A", tier="tp1", result="SUCCESS")
        cmd_outcome_add(cfg, job="run-B", tier="tp4", result="OOM")
        m = _load_manifest(cfg)
        jobs = [o["job"] for o in m.get("run_outcomes", [])]
        assert "run-A" in jobs
        assert "run-B" in jobs

    def test_outcome_add_has_timestamp(self, cfg: Config) -> None:
        """Recorded outcomes include a timestamp."""
        from research_vault.compute import cmd_outcome_add, _load_manifest
        _make_manifest(cfg)
        cmd_outcome_add(cfg, job="test-run", tier="tp2", result="SUCCESS")
        m = _load_manifest(cfg)
        outcome = m["run_outcomes"][0]
        assert "ts" in outcome
        assert len(outcome["ts"]) > 10  # ISO timestamp


# ---------------------------------------------------------------------------
# 8. _VERB_REGISTRY entries: when_to_use + sr="SR-6" for all SR-6 verbs
# ---------------------------------------------------------------------------

class TestVerbRegistry:
    SR6_VERBS = ["compute", "doctor", "plugins"]

    def test_sr6_verbs_registered(self) -> None:
        """All SR-6 verbs are present in _VERB_REGISTRY."""
        from research_vault.cli import _VERB_REGISTRY
        for verb in self.SR6_VERBS:
            assert verb in _VERB_REGISTRY, f"Verb {verb!r} not in _VERB_REGISTRY"

    def test_sr6_verbs_have_when_to_use(self) -> None:
        """All SR-6 verbs have a non-empty when_to_use string."""
        from research_vault.cli import _VERB_REGISTRY
        for verb in self.SR6_VERBS:
            entry = _VERB_REGISTRY.get(verb, {})
            when = entry.get("when_to_use", "").strip()
            assert when, f"Verb {verb!r} has empty when_to_use"

    def test_sr6_verbs_are_implemented(self) -> None:
        """All SR-6 verbs have a module (implemented, not planned)."""
        from research_vault.cli import _VERB_REGISTRY
        for verb in self.SR6_VERBS:
            entry = _VERB_REGISTRY.get(verb, {})
            assert entry.get("module"), f"Verb {verb!r} missing module"

    def test_sr6_when_to_use_names_antipattern(self) -> None:
        """SR-6 compute verb when_to_use names the trial-submit anti-pattern."""
        from research_vault.cli import _VERB_REGISTRY
        # The compute verb's when_to_use must surface the anti-pattern inline
        compute_entry = _VERB_REGISTRY.get("compute", {})
        when = compute_entry.get("when_to_use", "")
        # Must reference the anti-pattern (trial-submit, probe, or equivalent)
        assert "trial-submit" in when.lower() or "re-probe" in when.lower() or "probe" in when.lower(), (
            f"compute when_to_use must name the trial-submit anti-pattern, got: {when!r}"
        )

    def test_sr6_verbs_have_module(self) -> None:
        """All SR-6 verb entries point to a module."""
        from research_vault.cli import _VERB_REGISTRY
        for verb in self.SR6_VERBS:
            entry = _VERB_REGISTRY.get(verb, {})
            assert entry.get("module"), f"Verb {verb!r} has no module"


# ---------------------------------------------------------------------------
# 9. rv help --check passes after SR-6 verb registration
# ---------------------------------------------------------------------------

class TestHelpCheck:
    def test_help_check_passes(self) -> None:
        """rv help --check passes (exit 0) with all SR-6 verbs registered."""
        from research_vault.cli import _check_verb_docstrings
        violations = _check_verb_docstrings()
        assert violations == [], (
            f"rv help --check found violations: {violations}"
        )


# ---------------------------------------------------------------------------
# 10. Backend archetype declarations in manifest
# ---------------------------------------------------------------------------

class TestManifestArchetypes:
    def test_default_manifest_has_local_backend(self, cfg: Config) -> None:
        """Default manifest declares local backend as active."""
        from research_vault.compute import _default_manifest
        m = _default_manifest()
        assert "local" in m["backends"].get("active", [])

    def test_manifest_supports_ssh_slurm_profile(self, cfg: Config) -> None:
        """Manifest can declare an ssh+slurm backend profile."""
        from research_vault.compute import _save_manifest, _load_manifest
        m = {
            "backends": {
                "active": ["ssh-slurm"],
                "profiles": {
                    "ssh-slurm": {
                        "archetype": "ssh+slurm",
                        "host": "cluster.example.edu",
                        "submit_pattern": "sbatch --gres=gpu:{gpus} --wrap={cmd}",
                    }
                },
            },
            "conda_envs": {},
            "gpu_tiers": {},
            "rules": [],
            "model_quirks": {},
            "run_outcomes": [],
        }
        _save_manifest(cfg, m)
        loaded = _load_manifest(cfg)
        profile = loaded["backends"]["profiles"]["ssh-slurm"]
        assert profile["archetype"] == "ssh+slurm"
        assert profile["host"] == "cluster.example.edu"

    def test_manifest_supports_generic_custom_profile(self, cfg: Config) -> None:
        """Manifest supports generic/custom profile (escape hatch for SGE/LSF/k8s)."""
        from research_vault.compute import _save_manifest, _load_manifest
        m = {
            "backends": {
                "active": ["my-k8s"],
                "profiles": {
                    "my-k8s": {
                        "archetype": "generic",
                        "submit_pattern": "kubectl apply -f {job_spec}",
                        "probe_commands": ["kubectl cluster-info"],
                    }
                },
            },
            "conda_envs": {},
            "gpu_tiers": {},
            "rules": [],
            "model_quirks": {},
            "run_outcomes": [],
        }
        _save_manifest(cfg, m)
        loaded = _load_manifest(cfg)
        profile = loaded["backends"]["profiles"]["my-k8s"]
        assert profile["archetype"] == "generic"
        assert "probe_commands" in profile

    def test_manifest_supports_container_modifier(self, cfg: Config) -> None:
        """Manifest supports container as orthogonal modifier on a backend profile."""
        from research_vault.compute import _save_manifest, _load_manifest
        m = {
            "backends": {
                "active": ["ssh-slurm"],
                "profiles": {
                    "ssh-slurm": {
                        "archetype": "ssh+slurm",
                        "host": "cluster.example.edu",
                        "submit_pattern": "sbatch --gres=gpu:{gpus}",
                        "container": {
                            "runtime": "apptainer",
                            "image": "/scratch/images/myimage.sif",
                        },
                    }
                },
            },
            "conda_envs": {},
            "gpu_tiers": {},
            "rules": [],
            "model_quirks": {},
            "run_outcomes": [],
        }
        _save_manifest(cfg, m)
        loaded = _load_manifest(cfg)
        profile = loaded["backends"]["profiles"]["ssh-slurm"]
        # Container is a modifier field on the profile
        assert profile["container"]["runtime"] == "apptainer"


# ---------------------------------------------------------------------------
# 11. Doctor: generic backend probes declared probe_commands
# ---------------------------------------------------------------------------

class TestDoctorGenericProbe:
    def test_doctor_generic_profile_runs_declared_probes(self, cfg: Config) -> None:
        """Doctor runs declared probe_commands for generic backend profiles."""
        from research_vault.compute import _save_manifest
        from research_vault.doctor import cmd_doctor

        # Write a manifest with a generic profile + declared probe_commands
        m = {
            "backends": {
                "active": ["my-custom"],
                "profiles": {
                    "my-custom": {
                        "archetype": "generic",
                        "submit_pattern": "myjob submit {cmd}",
                        "probe_commands": ["echo probe-ok"],
                    }
                },
            },
            "conda_envs": {},
            "gpu_tiers": {},
            "rules": [],
            "model_quirks": {},
            "run_outcomes": [],
        }
        _save_manifest(cfg, m)
        # Doctor should not raise and should include generic probe results
        result = cmd_doctor(cfg, refresh=True)
        assert isinstance(result, dict)
        # SR-CO: per-backend shape; generic caps under backends["my-custom"]["capabilities"]
        backends = result["backends"]
        assert "my-custom" in backends
        caps = backends["my-custom"]["capabilities"]
        assert "generic_probes" in caps
