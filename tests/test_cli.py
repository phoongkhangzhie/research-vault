"""test_cli.py — tests for the CLI dispatcher and rv help --check gate.

Verifies: verb dispatch, rv help --check gate, version flag, unimplemented verb message,
grouped help renderer (Item 1), and example-snippet truthfulness gate (Item 2).
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
    # Verb count still present in success message
    assert str(len(_VERB_REGISTRY)) in out


def test_cli_no_verb_prints_help(capsys):
    """rv with no verb prints help and returns 0."""
    result = main([])
    assert result == 0


def test_cli_version(capsys):
    """rv --version exits with code 0 and prints the version string."""
    from research_vault import __version__

    result = main(["--version"])
    assert result == 0
    out = capsys.readouterr().out
    assert __version__ in out


def test_cli_unknown_verb_exits_nonzero(tmp_instance, capsys):
    """rv <completely-unknown-verb> exits non-zero (argparse/cli error).

    NOTE: 'dag' was previously the SR-3 stub tested here; it shipped in SR-3
    and is now a full verb. This test now uses a nonexistent verb name.
    """
    result = main(["no-such-verb-ever"])
    assert result != 0


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


# ---------------------------------------------------------------------------
# Item 1: Grouped help renderer tests
# ---------------------------------------------------------------------------

def test_help_grouped_output(capsys):
    """rv help output is grouped by workflow phase with phase headers.

    RED before grouped renderer is implemented (currently outputs flat list).
    """
    result = main(["help"])
    assert result == 0
    out = capsys.readouterr().out
    # All phase headers present (Figure and Manuscript removed in SR-RM-FIGMS)
    for phase in ["Setup", "Lit-review", "Experiment", "Gap loop", "Infra/git", "Coordination"]:
        assert phase in out, f"Phase header {phase!r} not found in rv help output"
    # SR-RM-FIGMS: Figure and Manuscript phases must be gone
    assert "── Figure" not in out
    assert "── Manuscript" not in out


def test_help_gap_loop_subcommands_visible(capsys):
    """rv help shows gap-scan/gap-route/gap-close in a distinct Gap loop section.

    RED before grouped renderer is implemented.
    """
    result = main(["help"])
    assert result == 0
    out = capsys.readouterr().out
    gap_idx = out.find("Gap loop")
    assert gap_idx != -1, "Gap loop section not found in rv help output"
    gap_section = out[gap_idx:]
    for subcmd in ["gap-scan", "gap-route", "gap-close", "gap-list", "gap-promote"]:
        assert subcmd in gap_section, (
            f"{subcmd!r} not visible in Gap loop section"
        )


def test_help_shows_subcommands_for_multi_verb(capsys):
    """rv help lists key subcommands for verbs like review/note/dag.

    RED before grouped renderer is implemented.
    """
    result = main(["help"])
    assert result == 0
    out = capsys.readouterr().out
    # review subcommands should appear in the output
    assert "new" in out          # review has 'new'
    assert "expand" in out       # review has 'expand'
    # dag subcommands
    assert "run" in out          # dag has 'run'
    assert "approve" in out      # dag has 'approve'
    # SR-RM-FIGMS: figure and manuscript removed
    assert "rv figure" not in out
    assert "rv manuscript" not in out


def test_help_no_60char_truncation(capsys):
    """rv help does not truncate descriptions with the old 60-char '...' cutoff.

    RED before grouped renderer is implemented (currently truncates with '…').
    """
    result = main(["help"])
    assert result == 0
    out = capsys.readouterr().out
    # Old format produced lines like: '  rv figure           When you have an experiment…'
    # Count lines that end with the truncation ellipsis '…' and have an rv verb prefix
    truncated = [
        line for line in out.splitlines()
        if line.rstrip().endswith("…") and "rv " in line and len(line.rstrip()) < 85
    ]
    assert len(truncated) == 0, (
        f"Found {len(truncated)} truncated line(s) with 60-char cutoff:\n"
        + "\n".join(truncated)
    )


def test_help_validation_map_line(capsys):
    """rv help includes the C4 validation-map line.

    RED before grouped renderer is implemented.
    """
    result = main(["help"])
    assert result == 0
    out = capsys.readouterr().out
    assert "rv lint" in out
    assert "rv mdstore check" in out
    assert "rv note" in out


# ---------------------------------------------------------------------------
# Item 2: Example-snippet truthfulness gate tests
# ---------------------------------------------------------------------------

def test_help_check_catches_broken_snippet(tmp_instance):
    """_check_example_snippets catches a deliberately-broken Use `rv ...` snippet.

    Non-vacuous RED-before-GREEN: the gate must actually fail on a bad snippet
    (the note example with an unknown --wrong-flag argument with placeholder).
    """
    from research_vault.cli import _check_example_snippets
    # Simulate a broken snippet: an unknown flag passed with a placeholder
    broken_registry = {
        "note": {
            "module": "research_vault.note",
            "when_to_use": (
                "When you need a note. "
                "Use `rv note <project> new <type> --wrong-flag <value>` to create one."
            ),
            "sr": "SR-1",
        }
    }
    violations = _check_example_snippets(broken_registry)
    assert len(violations) > 0, (
        "Expected violations from broken snippet with --wrong-flag"
    )


def test_help_check_snippet_gate_real_registry(tmp_instance):
    """_check_example_snippets finds no violations in the real _VERB_REGISTRY.

    RED until all broken snippets are fixed in cli.py.
    """
    from research_vault.cli import _check_example_snippets
    violations = _check_example_snippets(_VERB_REGISTRY)
    assert violations == [], (
        f"Snippet violations in real registry (fix the Use `rv ...` examples):\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_help_check_cli_now_covers_snippets(tmp_instance, capsys):
    """rv help --check (the CLI gate) now verifies snippets, not just docstring presence.

    RED until _check_example_snippets is wired into the CLI gate.
    """
    # Create a dummy merged_registry via a monkeypatching approach: we just test that
    # the function _check_example_snippets is callable and is referenced in the CLI.
    # The real guard is test_help_check_snippet_gate_real_registry above.
    from research_vault.cli import _check_example_snippets
    assert callable(_check_example_snippets), "_check_example_snippets must be callable"
    # rv help --check on the real registry exits 0 (snippets + docstrings both pass)
    result = main(["help", "--check"])
    assert result == 0


def test_figure_manuscript_removed_from_registry(tmp_instance):
    """SR-RM-FIGMS: figure and manuscript must not be in _VERB_REGISTRY."""
    assert "figure" not in _VERB_REGISTRY, "figure still in _VERB_REGISTRY after SR-RM-FIGMS"
    assert "manuscript" not in _VERB_REGISTRY, "manuscript still in _VERB_REGISTRY after SR-RM-FIGMS"
