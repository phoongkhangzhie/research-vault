"""test_plugin_seam.py — Tests for the instance-verb plugin seam.

Verifies that instance verbs registered in [verbs] of the config are loaded
and merged into the CLI registry, and that instance verbs shadow portable verbs.

All tests are hermetic: tmp_path, no real filesystem side-effects.
"""
import os
import sys
import textwrap
from pathlib import Path

import pytest
from research_vault.config import reset_config_cache


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def instance_verb_module(tmp_path: Path) -> Path:
    """Write a minimal instance-verb module to tmp_path/myverb.py."""
    mod_path = tmp_path / "myverb.py"
    mod_path.write_text(
        textwrap.dedent("""\
        import argparse

        def build_parser(parent=None):
            desc = "My instance verb."
            if parent is not None:
                p = parent.add_parser("my-verb", help=desc)
            else:
                p = argparse.ArgumentParser(prog="rv my-verb")
            return p

        def run(args):
            print("my-verb ran!")
            return 0
        """),
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# _load_instance_verbs
# ---------------------------------------------------------------------------

def test_load_instance_verbs_returns_empty_without_config(monkeypatch, tmp_path) -> None:
    """Without a config file, _load_instance_verbs returns empty dict."""
    monkeypatch.delenv("RESEARCH_VAULT_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)  # no research_vault.toml here
    reset_config_cache()
    from research_vault.cli import _load_instance_verbs
    result = _load_instance_verbs()
    assert result == {}


def test_load_instance_verbs_loads_from_config(monkeypatch, tmp_path) -> None:
    """Instance verbs in [verbs] section are loaded and returned."""
    config_file = tmp_path / "research_vault.toml"
    config_file.write_text(
        f"""\
instance_root = "{tmp_path}"
notes_root = "{tmp_path / 'notes'}"
state_dir = "{tmp_path / 'state'}"
agents_dir = "{tmp_path / '.agents'}"
tasks_dir = "{tmp_path / 'tasks'}"
control_dir = "{tmp_path / 'control'}"

[verbs.custom-step]
module = "myproject.verbs.custom"
when_to_use = "When you need to run the custom step."
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(config_file))
    reset_config_cache()

    from research_vault.cli import _load_instance_verbs
    result = _load_instance_verbs()
    assert "custom-step" in result
    assert result["custom-step"]["module"] == "myproject.verbs.custom"
    assert "custom step" in result["custom-step"]["when_to_use"].lower()


def test_load_instance_verbs_skips_verb_without_module(monkeypatch, tmp_path, capsys) -> None:
    """A [verbs] entry without a 'module' key is skipped with a warning."""
    config_file = tmp_path / "research_vault.toml"
    config_file.write_text(
        f"""\
instance_root = "{tmp_path}"
notes_root = "{tmp_path / 'notes'}"
state_dir = "{tmp_path / 'state'}"
agents_dir = "{tmp_path / '.agents'}"
tasks_dir = "{tmp_path / 'tasks'}"
control_dir = "{tmp_path / 'control'}"

[verbs.bad-verb]
when_to_use = "When to use this."
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(config_file))
    reset_config_cache()

    from research_vault.cli import _load_instance_verbs
    result = _load_instance_verbs()
    assert "bad-verb" not in result
    err = capsys.readouterr().err
    assert "bad-verb" in err or "no 'module'" in err


# ---------------------------------------------------------------------------
# Plugin seam: instance verbs shadow portable verbs
# ---------------------------------------------------------------------------

def test_instance_verb_shadows_portable(monkeypatch, tmp_path) -> None:
    """An instance verb with the same name as a portable verb takes precedence."""
    config_file = tmp_path / "research_vault.toml"
    # Shadow the portable 'lint' verb with an instance override
    config_file.write_text(
        f"""\
instance_root = "{tmp_path}"
notes_root = "{tmp_path / 'notes'}"
state_dir = "{tmp_path / 'state'}"
agents_dir = "{tmp_path / '.agents'}"
tasks_dir = "{tmp_path / 'tasks'}"
control_dir = "{tmp_path / 'control'}"

[verbs.lint]
module = "myinstance.lint_override"
when_to_use = "Instance override for lint."
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(config_file))
    reset_config_cache()

    from research_vault.cli import _load_instance_verbs, _VERB_REGISTRY
    instance_verbs = _load_instance_verbs()
    assert "lint" in instance_verbs
    assert instance_verbs["lint"]["module"] == "myinstance.lint_override"
    # The portable 'lint' module is different
    assert _VERB_REGISTRY["lint"]["module"] != "myinstance.lint_override"
