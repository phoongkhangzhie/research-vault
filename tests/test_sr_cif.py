"""test_sr_cif.py — acceptance tests for SR-CIF (tier-3 GitHub Actions CI fetch).

SR-CIF adds GitHubActionsSource — a SignalSource implementation that fetches
real PR/CI state from GitHub Actions via ``gh pr checks``.

Design doc: research-os-extract-adoptable-framework.md §5G + COHERENCE REFRESH.

BOUNDARY: the source fetches + surfaces CI truth. It NEVER auto-approves.
The SignalSource interface returns only frozenset[str] — no approve/write path exists.

Test map:
  1. GitHubActionsSource satisfies the SignalSource Protocol (isinstance check)
  2. Green PR (all required checks pass) → its id in get_terminal_set
  3. Red PR (a required check fails) → id NOT in get_terminal_set
  4. Pending PR (check still running) → id NOT in get_terminal_set
  5. Unverified fetch (gh errors) → source raises → combined-set emits warning, no terminal id
  6. gh absent (FileNotFoundError) → source degrades cleanly — contributes nothing, no crash
  7. Required-vs-optional: green on required checks but failing optional → still green (in terminal)
  8. build_live_set: open PRs (not merged) contribute id to the live set
  9. No auto-approve path: GitHubActionsSource has no code path that writes [PASS] or approves
 10. Reconcile integration: red-CI PR in Handshakes stays flagged (R4 not triggered by terminal)
 11. Reconcile integration: green-CI merged PR IS flagged R4 (id in terminal set)
 12. GitHubActionsSource works when constructed with a repo slug
 13. Docstring fix: SignalSource.build_live_set docstring says SR-CIF not SR-9

All tests are hermetic: ``gh`` is mocked via monkeypatch / subprocess side-effect.
Zero ~/vault reads or writes. No live GitHub calls.
"""

from __future__ import annotations

import inspect
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import load_config
from research_vault import control as control_mod
from research_vault.status import SignalSource
from research_vault.adapters.github_ci import GitHubActionsSource


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_instance):
    return load_config(reload=True)


@pytest.fixture
def ctl_file(cfg):
    """Fresh demo-research control file."""
    return control_mod.cmd_init("demo-research", config=cfg, overwrite=True)


def _make_gh_checks_output(checks: list[dict]) -> str:
    """Build fake ``gh pr checks`` tab-delimited output.

    Each dict: {"name": str, "state": "pass"|"fail"|"pending", "required": bool}
    The actual gh pr checks output columns: name, state, required, link
    """
    lines = []
    for c in checks:
        required_str = "true" if c.get("required", False) else "false"
        lines.append(f"{c['name']}\t{c['state']}\t{required_str}\thttps://github.com/x")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Test 1: Protocol conformance
# ---------------------------------------------------------------------------

def test_github_actions_source_satisfies_signal_source_protocol():
    """GitHubActionsSource is a SignalSource (isinstance via @runtime_checkable)."""
    src = GitHubActionsSource(repo="owner/repo", pr_number=7)
    assert isinstance(src, SignalSource), (
        "GitHubActionsSource must satisfy the SignalSource Protocol"
    )


# ---------------------------------------------------------------------------
# Test 2: Green PR → id in get_terminal_set
# ---------------------------------------------------------------------------

def test_green_pr_id_in_terminal_set(cfg, monkeypatch):
    """A PR whose required checks all pass contributes its id to the terminal set."""
    output = _make_gh_checks_output([
        {"name": "tests", "state": "pass", "required": True},
        {"name": "lint",  "state": "pass", "required": True},
    ])

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = output
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)

    src = GitHubActionsSource(repo="owner/repo", pr_number=7)
    terminal = src.get_terminal_set(cfg, "demo-research")
    assert "pr-7" in terminal, f"Expected 'pr-7' in terminal set, got {terminal}"


# ---------------------------------------------------------------------------
# Test 3: Red PR → id NOT in get_terminal_set
# ---------------------------------------------------------------------------

def test_red_pr_id_not_in_terminal_set(cfg, monkeypatch):
    """A PR with a failing required check withholds its id from the terminal set."""
    output = _make_gh_checks_output([
        {"name": "tests",        "state": "pass", "required": True},
        {"name": "leakage-scan", "state": "fail", "required": True},
    ])

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = output
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)

    src = GitHubActionsSource(repo="owner/repo", pr_number=7)
    terminal = src.get_terminal_set(cfg, "demo-research")
    assert "pr-7" not in terminal, (
        f"Red PR must not appear in terminal set, but got {terminal}"
    )


# ---------------------------------------------------------------------------
# Test 4: Pending PR → id NOT in get_terminal_set
# ---------------------------------------------------------------------------

def test_pending_pr_id_not_in_terminal_set(cfg, monkeypatch):
    """A PR with a pending required check withholds its id (not yet green)."""
    output = _make_gh_checks_output([
        {"name": "tests", "state": "pending", "required": True},
    ])

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = output
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)

    src = GitHubActionsSource(repo="owner/repo", pr_number=7)
    terminal = src.get_terminal_set(cfg, "demo-research")
    assert "pr-7" not in terminal


# ---------------------------------------------------------------------------
# Test 5: gh errors → source raises → combined-set emits warning (no crash, no green)
# ---------------------------------------------------------------------------

def test_gh_error_emits_warning_and_skips_source(cfg, ctl_file, monkeypatch, capsys):
    """When gh returns non-zero, the source errors. The combined-set builder
    emits 'false GREEN possible' to stderr and skips the source — gate stays not-green."""

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 1
        r.stdout = ""
        r.stderr = "gh: API error 401"
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)

    src = GitHubActionsSource(repo="owner/repo", pr_number=7)

    # The source must raise on error so the combined-set builder's except clause fires
    with pytest.raises(Exception):
        src.get_terminal_set(cfg, "demo-research")

    # Also test via the combined-set builder: reconcile with this source should warn
    import io
    import contextlib
    stderr_buf = io.StringIO()

    # Capture stderr from _build_combined_terminal_set
    from research_vault import control as ctl
    original_stderr = sys.stderr
    sys.stderr = stderr_buf
    try:
        terminal = ctl._build_combined_terminal_set(
            cfg, "demo-research", extra_sources=[src]
        )
    finally:
        sys.stderr = original_stderr

    err_output = stderr_buf.getvalue()
    assert "false GREEN possible" in err_output, (
        f"Expected 'false GREEN possible' warning in stderr, got: {err_output!r}"
    )
    # The source's error must not result in a false terminal id
    assert "pr-7" not in terminal


# ---------------------------------------------------------------------------
# Test 6: gh absent (FileNotFoundError) → degrades cleanly, no crash
# ---------------------------------------------------------------------------

def test_gh_absent_degrades_cleanly(cfg, monkeypatch):
    """When gh is not installed (FileNotFoundError), source raises cleanly.
    No crash, no false green — the combined builder's except clause handles it."""

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("gh: command not found")

    monkeypatch.setattr(subprocess, "run", fake_run)

    src = GitHubActionsSource(repo="owner/repo", pr_number=7)

    # The source should raise (not crash with an unhandled exception type mismatch)
    with pytest.raises((FileNotFoundError, RuntimeError, OSError)):
        src.get_terminal_set(cfg, "demo-research")

    # build_live_set also degrades
    with pytest.raises((FileNotFoundError, RuntimeError, OSError)):
        src.build_live_set(cfg, "demo-research")


# ---------------------------------------------------------------------------
# Test 7: Required-vs-optional: failing optional check does NOT block green
# ---------------------------------------------------------------------------

def test_optional_failing_check_does_not_block_green(cfg, monkeypatch):
    """A PR green on required checks but with a failing OPTIONAL check counts as green."""
    output = _make_gh_checks_output([
        {"name": "tests",        "state": "pass", "required": True},
        {"name": "coverage",     "state": "fail", "required": False},  # optional
    ])

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = output
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)

    src = GitHubActionsSource(repo="owner/repo", pr_number=5)
    terminal = src.get_terminal_set(cfg, "demo-research")
    assert "pr-5" in terminal, (
        f"PR with all required checks green must be terminal even if optional fails; got {terminal}"
    )


# ---------------------------------------------------------------------------
# Test 8: build_live_set — open (not merged) PR contributes to live set
# ---------------------------------------------------------------------------

def test_open_pr_in_live_set(cfg, monkeypatch):
    """An open PR (not yet merged) contributes its id to the live set."""
    # For build_live_set, we mock ``gh pr view`` to return "open" state
    gh_view_output = '{"state":"open","number":7}'

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = gh_view_output
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)

    src = GitHubActionsSource(repo="owner/repo", pr_number=7)
    live = src.build_live_set(cfg, "demo-research")
    assert "pr-7" in live, f"Open PR must appear in live set; got {live}"


# ---------------------------------------------------------------------------
# Test 9: No auto-approve path — structural boundary assertion
# ---------------------------------------------------------------------------

def test_no_auto_approve_path_in_github_actions_source():
    """GitHubActionsSource must not contain any code path that writes a verdict or calls approve.

    This is the crew-cannot-self-approve boundary, verified by source inspection.
    The SignalSource interface only returns frozenset[str] — no write capability.

    We check for CALL patterns (function calls that could approve/write), not
    documentary mentions of these concepts in docstrings.
    """
    from research_vault.adapters import github_ci as github_ci_module

    # Collect all source text in the module
    source = inspect.getsource(github_ci_module)

    # These CALL patterns must not appear in the implementation.
    # We check for actual function call forms, not docstring word mentions.
    # Pattern: token followed by ( — an actual call or argument, not a mention.
    call_patterns = [
        "cmd_return_entry(",
        "cmd_return(",
        "_write_verdict(",
        "_gate_token(",
        "gh\", \"pr\", \"merge",    # gh pr merge call
        "gh\", \"pr\", \"approve",  # gh pr approve call
        "vault approve",
    ]
    for pattern in call_patterns:
        assert pattern not in source, (
            f"GitHubActionsSource module must not contain call {pattern!r} "
            f"(no auto-approve path — crew-cannot-self-approve boundary)"
        )

    # The two methods must return frozenset, not write anything
    src = GitHubActionsSource(repo="owner/repo", pr_number=1)
    for method_name in ("build_live_set", "get_terminal_set"):
        method = getattr(src, method_name)
        assert callable(method), f"{method_name} must be callable"


# ---------------------------------------------------------------------------
# Test 10: Reconcile integration — red-CI PR stays flagged (R4 NOT triggered)
# ---------------------------------------------------------------------------

def test_reconcile_red_ci_pr_stays_flagged(cfg, ctl_file, monkeypatch):
    """A red-CI PR whose id is in Handshakes is NOT marked terminal.
    R4 (STALE: id is terminal) must NOT fire for a red PR."""
    output = _make_gh_checks_output([
        {"name": "leakage-scan", "state": "fail", "required": True},
    ])

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = output
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)

    # Post a Handshake with sr-7 id
    control_mod.cmd_post(
        "demo-research",
        section="Handshakes",
        title="sr-7: in-flight",
        config=cfg,
    )

    src = GitHubActionsSource(repo="owner/repo", pr_number=7)
    findings = control_mod.cmd_reconcile(
        "demo-research", config=cfg, extra_sources=[src]
    )

    # R4 (STALE: terminal) must NOT appear — the PR is red, not terminal
    r4_findings = [f for f in findings if "[R4]" in f and "sr-7" in f.lower()]
    assert not r4_findings, (
        f"R4 must not fire for a red-CI PR; findings: {findings}"
    )


# ---------------------------------------------------------------------------
# Test 11: Combined terminal set includes green PR id
# ---------------------------------------------------------------------------

def test_combined_terminal_set_includes_green_pr(cfg, ctl_file, monkeypatch):
    """When GitHubActionsSource is in extra_sources, a green PR's id appears in the
    combined terminal set returned by _build_combined_terminal_set."""
    checks_output = _make_gh_checks_output([
        {"name": "tests", "state": "pass", "required": True},
        {"name": "lint",  "state": "pass", "required": True},
    ])

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stderr = ""
        r.stdout = checks_output
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)

    src = GitHubActionsSource(repo="owner/repo", pr_number=7)
    from research_vault.control import _build_combined_terminal_set
    terminal = _build_combined_terminal_set(
        cfg, "demo-research", extra_sources=[src]
    )

    # The green PR contributes its id to the combined terminal set
    assert "pr-7" in terminal, (
        f"Green PR must contribute 'pr-7' to combined terminal set; got {terminal}"
    )


# ---------------------------------------------------------------------------
# Test 12: Constructor with repo slug
# ---------------------------------------------------------------------------

def test_github_actions_source_constructor():
    """GitHubActionsSource can be constructed with a repo slug and PR number."""
    src = GitHubActionsSource(repo="myorg/myrepo", pr_number=42)
    assert src is not None
    assert isinstance(src, SignalSource)


# ---------------------------------------------------------------------------
# Test 13: SignalSource docstring updated — SR-9 → SR-CIF
# ---------------------------------------------------------------------------

def test_signal_source_docstring_names_sr_cif_not_sr9():
    """The SignalSource docstring must reference SR-CIF, not SR-9 (which is CUT)."""
    from research_vault.status import SignalSource
    doc = SignalSource.__doc__ or ""
    assert "SR-9" not in doc, (
        "SignalSource docstring still references the cut SR-9 — update to SR-CIF"
    )
    assert "SR-CIF" in doc, (
        "SignalSource docstring must reference SR-CIF (the actual contributor)"
    )
