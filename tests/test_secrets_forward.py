"""Tests for automatic secret-forwarding to remote compute jobs (feat/secrets-forward).

Security spine (the load-bearing invariant): a forwarded secret's VALUE must
never appear on ANY argv — not the local ssh argv, not a scheduler --export,
not the remote process argv. Values travel only through process memory + an ssh
STDIN pipe + a mode-600 remote file that is sourced then immediately deleted.

The design maps directly onto the assertions below:
  (a) NO secret value in ANY argv across all submit archetypes (real scan)
  (b) the stage payload arrives via input=, not argv
  (c) a missing secret raises BEFORE any ssh/submit
  (d) the wrapper contains source + immediate-rm + trap
  (e) native_env is bypassed when secrets present
  (f) a bad secret name is rejected
  (g) leakage-scan clean (separate CI gate; smoke here)
  (h) absent secrets_forward = unchanged behavior
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import Config

# A distinctive sentinel so a scan for the value across all argv is unambiguous.
SECRET_VALUE = "SUPERSECRET-value-0xDEADBEEF-do-not-leak"
SECRET_NAME = "WANDB_API_KEY"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path: Path, backend: str = "slurm") -> Config:
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


def _write_manifest(cfg: Config, manifest: dict[str, Any]) -> None:
    from research_vault.compute import MANIFEST_FILE
    (cfg.state_dir / MANIFEST_FILE).write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )


def _manifest(archetype: str, *, host: str, submit_pattern: str,
              secrets_forward: list[str] | None = None,
              extra_profile: dict[str, Any] | None = None,
              jobid_parse: str | None = None) -> dict[str, Any]:
    prof: dict[str, Any] = {
        "archetype": archetype,
        "host": host,
        "submit_pattern": submit_pattern,
    }
    if secrets_forward is not None:
        prof["secrets_forward"] = secrets_forward
    if jobid_parse is not None:
        prof["jobid_parse"] = jobid_parse
    if extra_profile:
        prof.update(extra_profile)
    return {
        "backends": {"active": ["cluster"], "profiles": {
            "local": {"archetype": "local"},
            "cluster": prof,
        }},
        "conda_envs": {}, "gpu_tiers": {}, "rules": [],
        "model_quirks": {}, "run_outcomes": [],
    }


def _mock_run_factory(jobid_stdout: str):
    """A subprocess.run mock: returncode 0 + given stdout for every call."""
    def _run(*args, **kwargs):
        m = MagicMock()
        m.returncode = 0
        m.stdout = jobid_stdout
        m.stderr = ""
        return m
    return _run


def _all_argv_tokens(mock_run: MagicMock) -> list[str]:
    tokens: list[str] = []
    for c in mock_run.call_args_list:
        argv = c.args[0] if c.args else c.kwargs.get("args")
        assert isinstance(argv, list), f"argv not a list: {argv!r}"
        tokens.extend(str(t) for t in argv)
    return tokens


@pytest.fixture(autouse=True)
def _skip_keyring(monkeypatch):
    # Never touch the real keyring in tests; env-var is the only secret source.
    monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")


# Archetype matrix: (archetype, host, submit_pattern, jobid_stdout)
_ARCHETYPES = [
    ("ssh+slurm", "example-hpc", "sbatch --partition=gpu", "Submitted batch job 12345\n"),
    ("ssh+pbs", "example-pbs", "qsub -l nodes=1", "67890.pbs\n"),
    ("ssh", "example-ssh", "nohup {cmd} > /tmp/o 2>&1 & echo $!", "4242\n"),
]


# ---------------------------------------------------------------------------
# Pure-function unit tests (no subprocess)
# ---------------------------------------------------------------------------

class TestPureFunctions:
    def test_validate_secret_name_accepts_env_var_form(self):
        from research_vault.adapters.secret_forward import validate_secret_name
        for good in ("WANDB_API_KEY", "_X", "A1_B2", "s"):
            validate_secret_name(good)  # no raise

    def test_validate_secret_name_rejects_injection(self):
        from research_vault.adapters.secret_forward import validate_secret_name
        for bad in ("1BAD", "HAS-DASH", "HAS SPACE", "X;rm -rf", "$(evil)", "a.b", ""):
            with pytest.raises(ValueError):
                validate_secret_name(bad)

    def test_resolve_secrets_fail_closed_names_missing(self):
        from research_vault.adapters.secret_forward import resolve_secrets
        from research_vault.adapters.base import EnvSecretStore
        with pytest.raises(RuntimeError) as ei:
            resolve_secrets(["DEFINITELY_UNSET_XYZ"], EnvSecretStore())
        assert "DEFINITELY_UNSET_XYZ" in str(ei.value)

    def test_resolve_secrets_returns_ordered_values(self, monkeypatch):
        from research_vault.adapters.secret_forward import resolve_secrets
        from research_vault.adapters.base import EnvSecretStore
        monkeypatch.setenv("WANDB_API_KEY", SECRET_VALUE)
        monkeypatch.setenv("OTHER_KEY", "second")
        resolved = resolve_secrets(["WANDB_API_KEY", "OTHER_KEY"], EnvSecretStore())
        assert list(resolved) == ["WANDB_API_KEY", "OTHER_KEY"]
        assert resolved["WANDB_API_KEY"] == SECRET_VALUE

    def test_build_secret_blob_shlex_quotes_values(self):
        from research_vault.adapters.secret_forward import build_secret_blob
        blob = build_secret_blob({"K": "a b'c"})
        assert blob.startswith("export K=")
        assert blob.endswith("\n")
        # value with a space+quote must be shlex-safe (quoted)
        assert "'" in blob  # quoting applied

    def test_stage_script_contains_no_value_and_does_perms(self):
        from research_vault.adapters.secret_forward import make_plan
        plan = make_plan("$HOME/.rv-secrets", 720)
        script = plan.stage_script()
        assert "umask 077" in script
        assert "chmod 600" in script
        assert "cat >" in script
        assert "-mmin +720" in script
        assert SECRET_VALUE not in script

    def test_activation_wrapper_source_rm_trap(self):
        from research_vault.adapters.secret_forward import make_plan
        plan = make_plan("$HOME/.rv-secrets", 720)
        w = plan.activation_wrapper(cwd="/work", nonsecret_env={"E": "v"}, cmd=["python", "t.py"])
        # (d) source + immediate-rm + trap
        assert '. "$SECFILE"' in w
        assert 'rm -f "$SECFILE"' in w
        assert "trap " in w and "EXIT" in w and "INT" in w and "TERM" in w
        # cwd + nonsecret env prefix + cmd all present
        assert "cd /work" in w
        assert "E=v" in w
        assert "python t.py" in w
        assert SECRET_VALUE not in w

    def test_nonce_unique_per_plan(self):
        from research_vault.adapters.secret_forward import make_plan
        a = make_plan("$HOME/.rv-secrets", 720)
        b = make_plan("$HOME/.rv-secrets", 720)
        assert a.nonce != b.nonce
        assert a.secfile != b.secfile


# ---------------------------------------------------------------------------
# (a) THE LOAD-BEARING TEST — no secret value on ANY argv, all archetypes
# ---------------------------------------------------------------------------

class TestArgvClean:
    @pytest.mark.parametrize("archetype,host,submit_pattern,jobid_stdout", _ARCHETYPES)
    def test_no_secret_value_in_any_argv(self, tmp_path, monkeypatch,
                                         archetype, host, submit_pattern, jobid_stdout):
        from research_vault.adapters.remote import RemoteBackend
        monkeypatch.setenv(SECRET_NAME, SECRET_VALUE)
        cfg = _make_cfg(tmp_path)
        _write_manifest(cfg, _manifest(
            archetype, host=host, submit_pattern=submit_pattern,
            secrets_forward=[SECRET_NAME], jobid_parse=r"(\d+)",
        ))
        rb = RemoteBackend(cfg)
        with patch("subprocess.run", side_effect=_mock_run_factory(jobid_stdout)) as mr:
            rb.submit(["python", "train.py"], cwd="/work")
        tokens = _all_argv_tokens(mr)
        joined = " ".join(tokens)
        assert SECRET_VALUE not in joined, (
            f"SECRET LEAK: value found on argv for archetype {archetype}: {joined}"
        )
        # And never as a --export / KEY=value pair
        assert f"{SECRET_NAME}={SECRET_VALUE}" not in joined
        assert "--export" not in joined or SECRET_VALUE not in joined

    @pytest.mark.parametrize("archetype,host,submit_pattern,jobid_stdout", _ARCHETYPES)
    def test_stage_payload_via_stdin_not_argv(self, tmp_path, monkeypatch,
                                              archetype, host, submit_pattern, jobid_stdout):
        from research_vault.adapters.remote import RemoteBackend
        monkeypatch.setenv(SECRET_NAME, SECRET_VALUE)
        cfg = _make_cfg(tmp_path)
        _write_manifest(cfg, _manifest(
            archetype, host=host, submit_pattern=submit_pattern,
            secrets_forward=[SECRET_NAME], jobid_parse=r"(\d+)",
        ))
        rb = RemoteBackend(cfg)
        with patch("subprocess.run", side_effect=_mock_run_factory(jobid_stdout)) as mr:
            rb.submit(["python", "train.py"])
        # Exactly one call carried the blob via input= and that blob has the value.
        stage_calls = [c for c in mr.call_args_list if c.kwargs.get("input")]
        assert len(stage_calls) == 1, "expected exactly one STDIN-staged call"
        blob = stage_calls[0].kwargs["input"]
        assert SECRET_VALUE in blob, "blob must carry the value (via stdin)"
        # That very call's argv must NOT contain the value.
        stage_argv = stage_calls[0].args[0]
        assert SECRET_VALUE not in " ".join(str(t) for t in stage_argv)


# ---------------------------------------------------------------------------
# (c) resolve fail-closed BEFORE any ssh
# ---------------------------------------------------------------------------

class TestFailClosed:
    def test_missing_secret_raises_before_any_ssh(self, tmp_path, monkeypatch):
        from research_vault.adapters.remote import RemoteBackend
        monkeypatch.delenv(SECRET_NAME, raising=False)
        cfg = _make_cfg(tmp_path)
        _write_manifest(cfg, _manifest(
            "ssh+slurm", host="h", submit_pattern="sbatch",
            secrets_forward=[SECRET_NAME], jobid_parse=r"(\d+)",
        ))
        rb = RemoteBackend(cfg)
        with patch("subprocess.run", side_effect=_mock_run_factory("x\n")) as mr:
            with pytest.raises(RuntimeError) as ei:
                rb.submit(["python", "t.py"])
        assert SECRET_NAME in str(ei.value)
        assert mr.call_count == 0, "NO ssh call may happen before a missing secret is caught"


# ---------------------------------------------------------------------------
# (e) native_env bypassed when secrets present
# ---------------------------------------------------------------------------

class TestNativeEnvBypass:
    def test_native_env_ignored_no_export_on_argv(self, tmp_path, monkeypatch):
        from research_vault.adapters.remote import RemoteBackend
        monkeypatch.setenv(SECRET_NAME, SECRET_VALUE)
        cfg = _make_cfg(tmp_path)
        _write_manifest(cfg, _manifest(
            "ssh+slurm", host="h", submit_pattern="sbatch",
            secrets_forward=[SECRET_NAME], jobid_parse=r"(\d+)",
            extra_profile={"native_env": True},
        ))
        rb = RemoteBackend(cfg)
        with patch("subprocess.run", side_effect=_mock_run_factory("Submitted batch job 5\n")) as mr:
            rb.submit(["python", "t.py"], env={"NONSECRET": "ok"}, cwd="/w")
        tokens = _all_argv_tokens(mr)
        joined = " ".join(tokens)
        # native_env's --export must not be used for the submit when secrets present
        assert "--export" not in joined
        # the sh -c wrapper is forced
        assert any("SECFILE" in t for t in tokens)
        assert SECRET_VALUE not in joined

    def test_native_env_without_secrets_still_uses_export(self, tmp_path):
        """Regression guard: native_env path unchanged when no secrets_forward."""
        from research_vault.adapters.remote import RemoteBackend
        cfg = _make_cfg(tmp_path)
        _write_manifest(cfg, _manifest(
            "ssh+slurm", host="h", submit_pattern="sbatch",
            secrets_forward=None, jobid_parse=r"(\d+)",
            extra_profile={"native_env": True},
        ))
        rb = RemoteBackend(cfg)
        with patch("subprocess.run", side_effect=_mock_run_factory("Submitted batch job 5\n")) as mr:
            rb.submit(["python", "t.py"], env={"E": "v"})
        joined = " ".join(_all_argv_tokens(mr))
        assert "--export=E=v" in joined


# ---------------------------------------------------------------------------
# (f) bad secret name rejected at submit
# ---------------------------------------------------------------------------

class TestBadName:
    def test_bad_secret_name_rejected_before_ssh(self, tmp_path):
        from research_vault.adapters.remote import RemoteBackend
        cfg = _make_cfg(tmp_path)
        _write_manifest(cfg, _manifest(
            "ssh+slurm", host="h", submit_pattern="sbatch",
            secrets_forward=["BAD-NAME; rm -rf /"], jobid_parse=r"(\d+)",
        ))
        rb = RemoteBackend(cfg)
        with patch("subprocess.run", side_effect=_mock_run_factory("x\n")) as mr:
            with pytest.raises((ValueError, RuntimeError)):
                rb.submit(["python", "t.py"])
        assert mr.call_count == 0


# ---------------------------------------------------------------------------
# (h) absent secrets_forward = unchanged behavior
# ---------------------------------------------------------------------------

class TestBackCompat:
    def test_absent_secrets_forward_single_ssh_call(self, tmp_path):
        from research_vault.adapters.remote import RemoteBackend
        cfg = _make_cfg(tmp_path)
        _write_manifest(cfg, _manifest(
            "ssh+slurm", host="h", submit_pattern="sbatch",
            secrets_forward=None, jobid_parse=r"(\d+)",
        ))
        rb = RemoteBackend(cfg)
        with patch("subprocess.run", side_effect=_mock_run_factory("Submitted batch job 9\n")) as mr:
            handle = rb.submit(["python", "t.py"])
        assert handle == "9"
        # No staging call — exactly one subprocess.run (the submit)
        assert mr.call_count == 1
        assert all(not c.kwargs.get("input") for c in mr.call_args_list)


# ---------------------------------------------------------------------------
# Submit-failure cleanup (best-effort remote rm, never masks original error)
# ---------------------------------------------------------------------------

class TestCleanupOnFailure:
    def test_submit_failure_fires_cleanup_and_reraises(self, tmp_path, monkeypatch):
        from research_vault.adapters.remote import RemoteBackend
        monkeypatch.setenv(SECRET_NAME, SECRET_VALUE)
        cfg = _make_cfg(tmp_path)
        _write_manifest(cfg, _manifest(
            "ssh+slurm", host="h", submit_pattern="sbatch",
            secrets_forward=[SECRET_NAME], jobid_parse=r"(\d+)",
        ))
        rb = RemoteBackend(cfg)
        calls: list[Any] = []

        def _run(*args, **kwargs):
            calls.append((args, kwargs))
            m = MagicMock()
            # stage (has input=) succeeds; submit (no input) fails
            if kwargs.get("input"):
                m.returncode = 0
                m.stdout = ""
                m.stderr = ""
            else:
                m.returncode = 1
                m.stdout = ""
                m.stderr = "boom"
            return m

        with patch("subprocess.run", side_effect=_run):
            with pytest.raises(RuntimeError) as ei:
                rb.submit(["python", "t.py"])
        assert "failed" in str(ei.value).lower()
        # A best-effort cleanup rm was issued after the failure.
        rm_calls = [a for (a, k) in calls if a and "rm -f" in " ".join(str(t) for t in a[0])]
        assert rm_calls, "expected a best-effort remote rm on submit failure"


# ---------------------------------------------------------------------------
# Doctor resolvability probe — names-not-values
# ---------------------------------------------------------------------------

class TestDoctorProbe:
    def test_probe_reports_resolvable_and_missing(self, tmp_path, monkeypatch):
        from research_vault.doctor import _probe_capabilities
        monkeypatch.setenv(SECRET_NAME, SECRET_VALUE)
        monkeypatch.delenv("MISSING_KEY", raising=False)
        cfg = _make_cfg(tmp_path)
        _write_manifest(cfg, _manifest(
            "ssh+slurm", host="FILL — host", submit_pattern="sbatch",
            secrets_forward=[SECRET_NAME, "MISSING_KEY"],
        ))
        caps = _probe_capabilities(cfg)
        sf = caps["cluster"].get("secrets_forward")
        assert sf is not None, "doctor must attach a secrets_forward probe"
        by_name = {e["name"]: e for e in sf}
        assert by_name[SECRET_NAME]["resolvable"] is True
        assert by_name["MISSING_KEY"]["resolvable"] is False

    def test_probe_never_prints_value(self, tmp_path, monkeypatch):
        from research_vault.doctor import _probe_capabilities, format_report
        monkeypatch.setenv(SECRET_NAME, SECRET_VALUE)
        cfg = _make_cfg(tmp_path)
        _write_manifest(cfg, _manifest(
            "ssh+slurm", host="FILL — host", submit_pattern="sbatch",
            secrets_forward=[SECRET_NAME],
        ))
        caps = _probe_capabilities(cfg)
        # The probe result must carry names + bools only — never the value.
        assert SECRET_VALUE not in json.dumps(caps)
        report = format_report({
            "backends": {"cluster": {"capabilities": caps["cluster"]}},
            "ts": "now",
        })
        assert SECRET_NAME in report
        assert SECRET_VALUE not in report


# ---------------------------------------------------------------------------
# cmd_show renders forwards: <names> (never values)
# ---------------------------------------------------------------------------

class TestComputeShow:
    def test_show_renders_forward_names(self, tmp_path, monkeypatch, capsys):
        from research_vault.compute import cmd_show
        monkeypatch.setenv(SECRET_NAME, SECRET_VALUE)
        cfg = _make_cfg(tmp_path)
        _write_manifest(cfg, _manifest(
            "ssh+slurm", host="h", submit_pattern="sbatch",
            secrets_forward=[SECRET_NAME],
        ))
        cmd_show(cfg)
        out = capsys.readouterr().out
        assert SECRET_NAME in out
        assert "forwards" in out.lower()
        assert SECRET_VALUE not in out

    def test_scaffold_seeds_secrets_forward_hint(self):
        from research_vault.compute import _scaffold_manifest
        m = _scaffold_manifest(has_scheduler="ssh+slurm")
        prof = m["backends"]["profiles"]["compute-node"]
        # A hint/seed for secrets_forward must exist so adopters discover it.
        assert "secrets_forward" in prof
        blob = json.dumps(prof)
        assert "WANDB_API_KEY" in blob


# ---------------------------------------------------------------------------
# (g) leakage scan smoke — run the real scanner against the new module
# ---------------------------------------------------------------------------

class TestLeakageSmoke:
    def test_new_module_passes_leakage_scan(self):
        """The publish-bound secret_forward module must pass the real scanner."""
        import subprocess

        import research_vault.adapters.secret_forward as m
        repo = Path(__file__).parent.parent
        scanner = repo / "scripts" / "leakage_scan.sh"
        if not scanner.exists():
            pytest.skip("leakage_scan.sh not present")
        r = subprocess.run(
            ["bash", str(scanner), m.__file__],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, (
            f"leakage scan failed on secret_forward.py:\n{r.stdout}\n{r.stderr}"
        )
