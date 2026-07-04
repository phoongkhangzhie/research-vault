"""test_instance_resolution.py — tests for F1/F2: --config flag + instance visibility.

Verifies:
  F1 — --config PATH targets that instance regardless of CWD
  F1 — RESEARCH_VAULT_CONFIG env does the same
  F1 — precedence: --config > RESEARCH_VAULT_CONFIG > CWD walk-up
  F1 — error when --config PATH does not exist (fail-loud guard)
  F2 — resolved instance_root is surfaced in rv status output
  F2 — --show-instance prints instance root and config file
  back-compat — CWD walk-up still works when neither is set

All tests are hermetic (tmp_path). No ~/vault access.
"""

import os
import pytest
from pathlib import Path

from research_vault.cli import main, _extract_config_arg
from research_vault.config import load_config, reset_config_cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_instance(root: Path, slug: str = "test-proj") -> Path:
    """Write a minimal research_vault.toml at root, return its path."""
    toml = root / "research_vault.toml"
    proj_dir = root / "projects" / slug
    proj_dir.mkdir(parents=True, exist_ok=True)
    toml.write_text(
        f"""
instance_root = "{root}"
notes_root = "{root / 'notes'}"
state_dir = "{root / 'state'}"
agents_dir = "{root / '.agents'}"
tasks_dir = "{root / 'tasks'}"
control_dir = "{root / 'control'}"

[projects.{slug}]
source_dir = "{proj_dir}"
""",
        encoding="utf-8",
    )
    return toml


# ---------------------------------------------------------------------------
# F1 — _extract_config_arg helper
# ---------------------------------------------------------------------------

class TestExtractConfigArg:
    def test_space_separated(self):
        """--config /path/to/file.toml (space-separated form)."""
        result = _extract_config_arg(["--config", "/path/to/file.toml", "status"])
        assert result == "/path/to/file.toml"

    def test_equals_form(self):
        """--config=/path/to/file.toml (equals form)."""
        result = _extract_config_arg(["--config=/path/to/file.toml", "status"])
        assert result == "/path/to/file.toml"

    def test_absent_returns_none(self):
        """Returns None when --config is not in argv."""
        result = _extract_config_arg(["status", "my-proj"])
        assert result is None

    def test_empty_argv(self):
        """Returns None for empty argv."""
        result = _extract_config_arg([])
        assert result is None

    def test_config_at_end_of_argv_no_value(self):
        """--config at the end with no following value returns None (safe, not crash)."""
        result = _extract_config_arg(["status", "--config"])
        assert result is None


# ---------------------------------------------------------------------------
# F1 — --config PATH CLI option targets that instance regardless of CWD
# ---------------------------------------------------------------------------

class TestConfigFlagTargetsInstance:
    def test_config_flag_overrides_cwd(self, tmp_path, monkeypatch, capsys):
        """--config /other/research_vault.toml targets that instance, not cwd's."""
        instance_a = tmp_path / "instance_a"
        instance_b = tmp_path / "instance_b"
        instance_a.mkdir()
        instance_b.mkdir()

        _write_instance(instance_a, slug="proj-a")
        toml_b = _write_instance(instance_b, slug="proj-b")

        # CWD is inside instance_a; --config points to instance_b
        monkeypatch.chdir(instance_a)
        monkeypatch.delenv("RESEARCH_VAULT_CONFIG", raising=False)
        reset_config_cache()

        rc = main(["--config", str(toml_b), "--show-instance"])
        assert rc == 0
        out = capsys.readouterr().out
        assert str(instance_b) in out, (
            f"Expected instance_b root in output, got: {out!r}"
        )
        assert str(instance_a) not in out, (
            f"instance_a should NOT appear in output, got: {out!r}"
        )

    def test_config_flag_show_instance_prints_config_file(self, tmp_path, monkeypatch, capsys):
        """--show-instance prints both instance_root and config_file."""
        instance = tmp_path / "my_instance"
        instance.mkdir()
        toml = _write_instance(instance)

        monkeypatch.delenv("RESEARCH_VAULT_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)  # not inside instance
        reset_config_cache()

        rc = main(["--config", str(toml), "--show-instance"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "instance_root:" in out
        assert "config_file:" in out
        assert str(instance) in out
        assert str(toml) in out

    def test_config_flag_errors_on_nonexistent_path(self, tmp_path, monkeypatch, capsys):
        """--config /nonexistent.toml errors loudly with non-zero exit."""
        monkeypatch.delenv("RESEARCH_VAULT_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        reset_config_cache()

        rc = main(["--config", str(tmp_path / "does_not_exist.toml"), "--show-instance"])
        assert rc != 0
        err = capsys.readouterr().err
        assert "does_not_exist.toml" in err or "does not exist" in err.lower()


# ---------------------------------------------------------------------------
# F1 — RESEARCH_VAULT_CONFIG env var targets that instance
# ---------------------------------------------------------------------------

class TestEnvVarTargetsInstance:
    def test_env_var_overrides_cwd(self, tmp_path, monkeypatch, capsys):
        """RESEARCH_VAULT_CONFIG env var targets the specified instance."""
        instance_a = tmp_path / "instance_a"
        instance_b = tmp_path / "instance_b"
        instance_a.mkdir()
        instance_b.mkdir()

        _write_instance(instance_a, slug="proj-a")
        toml_b = _write_instance(instance_b, slug="proj-b")

        # CWD is inside instance_a; env var points to instance_b
        monkeypatch.chdir(instance_a)
        monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(toml_b))
        reset_config_cache()

        cfg = load_config(reload=True)
        assert cfg.instance_root == instance_b, (
            f"Expected instance_b, got {cfg.instance_root}"
        )
        assert "proj-b" in cfg.projects

    def test_env_var_errors_on_nonexistent(self, tmp_path, monkeypatch):
        """RESEARCH_VAULT_CONFIG pointing to absent file raises FileNotFoundError."""
        monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(tmp_path / "absent.toml"))
        reset_config_cache()
        with pytest.raises(FileNotFoundError, match="absent.toml"):
            load_config(reload=True)


# ---------------------------------------------------------------------------
# F1 — precedence: --config > RESEARCH_VAULT_CONFIG > CWD walk-up
# ---------------------------------------------------------------------------

class TestPrecedenceOrder:
    def test_config_flag_beats_env_var(self, tmp_path, monkeypatch, capsys):
        """--config wins over RESEARCH_VAULT_CONFIG when both are set."""
        instance_env = tmp_path / "instance_env"
        instance_flag = tmp_path / "instance_flag"
        instance_env.mkdir()
        instance_flag.mkdir()

        toml_env = _write_instance(instance_env, slug="proj-env")
        toml_flag = _write_instance(instance_flag, slug="proj-flag")

        # Both set: flag wins
        monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(toml_env))
        monkeypatch.chdir(tmp_path)
        reset_config_cache()

        rc = main(["--config", str(toml_flag), "--show-instance"])
        assert rc == 0
        out = capsys.readouterr().out
        assert str(instance_flag) in out, (
            f"Expected instance_flag root in output, got: {out!r}"
        )
        assert str(instance_env) not in out, (
            f"instance_env should NOT appear in output, got: {out!r}"
        )

    def test_env_var_beats_cwd_walkup(self, tmp_path, monkeypatch):
        """RESEARCH_VAULT_CONFIG wins over CWD walk-up when env is set."""
        cwd_instance = tmp_path / "cwd_instance"
        env_instance = tmp_path / "env_instance"
        cwd_instance.mkdir()
        env_instance.mkdir()

        _write_instance(cwd_instance, slug="proj-cwd")
        toml_env = _write_instance(env_instance, slug="proj-env")

        monkeypatch.chdir(cwd_instance)
        monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(toml_env))
        reset_config_cache()

        cfg = load_config(reload=True)
        assert cfg.instance_root == env_instance
        assert "proj-env" in cfg.projects

    def test_cwd_walkup_works_when_neither_set(self, tmp_path, monkeypatch):
        """CWD walk-up finds research_vault.toml when no flag or env var is set."""
        instance = tmp_path / "my_vault"
        instance.mkdir()
        _write_instance(instance, slug="found-proj")

        # CWD is inside the instance; neither flag nor env var set
        monkeypatch.chdir(instance)
        monkeypatch.delenv("RESEARCH_VAULT_CONFIG", raising=False)
        reset_config_cache()

        cfg = load_config(reload=True)
        assert cfg.instance_root == instance
        assert "found-proj" in cfg.projects

    def test_cwd_walkup_from_subdir(self, tmp_path, monkeypatch):
        """CWD walk-up finds research_vault.toml in a parent directory."""
        instance = tmp_path / "vault_parent"
        subdir = instance / "deep" / "subdir"
        instance.mkdir()
        subdir.mkdir(parents=True)
        _write_instance(instance, slug="parent-proj")

        monkeypatch.chdir(subdir)
        monkeypatch.delenv("RESEARCH_VAULT_CONFIG", raising=False)
        reset_config_cache()

        cfg = load_config(reload=True)
        assert cfg.instance_root == instance
        assert "parent-proj" in cfg.projects


# ---------------------------------------------------------------------------
# F2 — resolved instance_root surfaced in rv status
# ---------------------------------------------------------------------------

class TestInstanceRootSurfacedInStatus:
    def test_status_shows_instance_root(self, tmp_path, monkeypatch, capsys):
        """rv status <project> prints instance_root so the operator knows which vault."""
        instance = tmp_path / "status_instance"
        instance.mkdir()
        (instance / "projects" / "demo").mkdir(parents=True)
        toml = _write_instance(instance, slug="demo")

        monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(toml))
        reset_config_cache()

        rc = main(["status", "demo"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "instance_root:" in out
        assert str(instance) in out

    def test_status_shows_config_file(self, tmp_path, monkeypatch, capsys):
        """rv status <project> prints the config_file path."""
        instance = tmp_path / "status_instance2"
        instance.mkdir()
        (instance / "projects" / "demo2").mkdir(parents=True)
        toml = _write_instance(instance, slug="demo2")

        monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(toml))
        reset_config_cache()

        rc = main(["status", "demo2"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "config_file:" in out
        assert str(toml) in out


# ---------------------------------------------------------------------------
# Back-compat: existing callers not broken
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    def test_no_flag_no_env_uses_cwd(self, tmp_path, monkeypatch):
        """load_config() without flag or env still does CWD walk-up (back-compat)."""
        instance = tmp_path / "compat_vault"
        instance.mkdir()
        _write_instance(instance, slug="compat-proj")

        monkeypatch.chdir(instance)
        monkeypatch.delenv("RESEARCH_VAULT_CONFIG", raising=False)
        reset_config_cache()

        cfg = load_config(reload=True)
        assert "compat-proj" in cfg.projects

    def test_show_instance_flag_without_config_uses_cwd(self, tmp_path, monkeypatch, capsys):
        """--show-instance alone (no --config, no env) resolves via CWD walk-up."""
        instance = tmp_path / "cwd_vault"
        instance.mkdir()
        _write_instance(instance, slug="cwd-proj")

        monkeypatch.chdir(instance)
        monkeypatch.delenv("RESEARCH_VAULT_CONFIG", raising=False)
        reset_config_cache()

        rc = main(["--show-instance"])
        assert rc == 0
        out = capsys.readouterr().out
        assert str(instance) in out

    def test_version_flag_unaffected(self, capsys):
        """--version still works normally (regression guard)."""
        rc = main(["--version"])
        assert rc == 0
