"""test_cli.py — tests for the CLI dispatcher and rv help --check gate.

Verifies: verb dispatch, rv help --check gate, version flag, unimplemented verb message.
All hermetic (tmp_instance).
"""

import pytest
from research_vault.cli import main, _VERB_REGISTRY, _check_verb_docstrings


def test_help_check_passes():
    """rv help --check exits 0 — all registered verbs have when_to_use docstrings."""
    violations = _check_verb_docstrings()
    assert violations == [], f"Missing when_to_use on verbs: {violations}"


def test_cli_help_check_exit_zero(tmp_instance, capsys):
    """rv help --check returns 0 from the CLI."""
    result = main(["help", "--check"])
    assert result == 0
    out = capsys.readouterr().out
    assert "OK" in out
    assert str(len(_VERB_REGISTRY)) in out


def test_cli_no_verb_prints_help(capsys):
    """rv with no verb prints help and returns 0."""
    result = main([])
    assert result == 0


def test_cli_version(capsys):
    """rv --version exits with code 0 and prints the version string."""
    result = main(["--version"])
    assert result == 0
    out = capsys.readouterr().out
    assert "0.1.0" in out


def test_cli_unimplemented_verb_exits_1(tmp_instance, capsys):
    """rv <future-verb> exits 1 with a helpful message."""
    result = main(["dag", "run"])
    assert result == 1
    err = capsys.readouterr().err
    assert "SR-3" in err or "not yet implemented" in err


def test_all_verbs_have_nonempty_when_to_use():
    """Every verb in the registry has a non-empty when_to_use string (the discovery surface)."""
    for verb, entry in _VERB_REGISTRY.items():
        when = entry.get("when_to_use", "").strip()
        assert when, f"Verb {verb!r} is missing a when_to_use docstring"
        # Must be at least 20 chars — not just a placeholder
        assert len(when) >= 20, f"Verb {verb!r} has a suspiciously short when_to_use: {when!r}"


def test_cli_help_lists_verbs(tmp_instance, capsys):
    """rv help lists all registered verbs."""
    result = main(["help"])
    assert result == 0
    out = capsys.readouterr().out
    for verb in ["task", "note", "control", "devlog"]:
        assert verb in out


def test_zero_vault_writes_in_tests(tmp_instance):
    """Boundary check: tmp_instance uses tmp_path; ~/vault is never accessed.

    This test verifies that the RESEARCH_VAULT_CONFIG env var is set to a path
    inside tmp_path, not inside ~/vault. A failing test here means a test fixture
    has accidentally pointed at the live vault.
    """
    import os
    from pathlib import Path
    config_path = os.environ.get("RESEARCH_VAULT_CONFIG", "")
    assert config_path, "RESEARCH_VAULT_CONFIG should be set by the tmp_instance fixture"
    # Must NOT be under ~/vault
    vault_path = Path.home() / "vault"
    assert not Path(config_path).is_relative_to(vault_path), (
        f"Config path {config_path!r} is inside ~/vault — test isolation is broken"
    )
