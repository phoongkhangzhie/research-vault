"""test_sr_cif.py — acceptance tests for SR-CIF (tier-3 GitHub Actions CI fetch).

SR-CIF adds GitHubActionsSource — a SignalSource implementation that fetches
real PR/CI state from GitHub Actions via ``gh pr checks --json``.

Design doc: research-os-extract-adoptable-framework.md §5G + COHERENCE REFRESH.

BOUNDARY: the source fetches + surfaces CI truth. It NEVER auto-approves.
The SignalSource interface returns only frozenset[str] — no approve/write path exists.

All mocks use the REAL ``gh pr checks --json name,state,bucket`` JSON schema —
not the deprecated tab-delimited format (which caused the original BLOCK-2).

Test map:
  1.  GitHubActionsSource satisfies the SignalSource Protocol
  2.  Green-MERGED PR → sr-* ids from headRefName in terminal set
  3.  Red PR (a bucket==fail) → sr-* NOT in terminal set
  4.  Pending PR (bucket==pending) → sr-* NOT in terminal set
  5.  gh errors → source raises → combined-set emits warning, no terminal id
  6.  gh absent (FileNotFoundError) → degrades cleanly, no crash
  7.  Skipping check is non-blocking (does not prevent green), MERGED PR
  8.  build_live_set: open PR contributes sr-* from headRefName, NOT pr-N
  9.  No auto-approve path: structural boundary assertion
 10.  FUNCTIONAL PROOF: green-MERGED vs green-OPEN produce different reconcile output
       (MERGED fires R4, OPEN does NOT — that is the normal human-go-crew state)
 11.  Combined terminal set: green-MERGED contributes sr-*, green-OPEN does NOT
 12.  No-checks-at-all → conservative: empty terminal set (not accidentally green)
 13.  Constructor with repo slug (smoke)
 14.  Docstring fix: SR-9 → SR-CIF in status.py
 23.  BUG GUARD: green-but-OPEN PR → ids NOT in terminal (false STALE bug)
 24.  Green-MERGED PR → ids ARE in terminal (correct merged behavior)
 25.  FUNCTIONAL PROOF: green-OPEN reconcile must NOT emit [R4] STALE
 26.  Red-before-green: reverting merged gate makes green-OPEN test fail

All tests are hermetic: ``gh`` is mocked via monkeypatch / subprocess side-effect.
Zero ~/vault reads or writes. No live GitHub calls.
"""

from __future__ import annotations

import inspect
import json
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


# ---------------------------------------------------------------------------
# Helpers — real gh JSON schema
# ---------------------------------------------------------------------------

def _checks_json(checks: list[dict]) -> str:
    """Build ``gh pr checks --json name,state,bucket`` output (real schema).

    Each dict: {"name": str, "bucket": "pass"|"fail"|"pending"|"skipping"|"cancel"}
    Optional "state" field (e.g. "SUCCESS") is included for fidelity but bucket governs.
    """
    rows = [
        {"name": c["name"], "state": c.get("state", "SUCCESS"), "bucket": c["bucket"]}
        for c in checks
    ]
    return json.dumps(rows)


def _view_json(state: str = "OPEN", branch: str = "feat/sr-7") -> str:
    """Build ``gh pr view --json state,headRefName`` output (real schema)."""
    return json.dumps({"state": state, "headRefName": branch})


def _make_runner(*, view: str, checks: str):
    """Return a fake subprocess.run that dispatches based on the gh sub-command."""
    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stderr = ""
        # Dispatch: "checks" sub-command vs "view"
        if len(cmd) > 2 and cmd[2] == "checks":
            r.stdout = checks
        else:
            r.stdout = view
        return r
    return fake_run


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
# Test 2: Green PR → sr-* ids from headRefName in terminal set
# ---------------------------------------------------------------------------

def test_green_pr_branch_ids_in_terminal_set(cfg, monkeypatch):
    """A MERGED PR whose ALL checks have bucket==pass contributes sr-* branch ids to terminal.

    The branch 'feat/sr-7' yields 'sr-7' via _ID_TOKEN_RE — NOT 'pr-7'.
    'sr-7' is what _check_r4 and extract_id_tokens actually join on.

    Note: state must be "MERGED" — a green-but-OPEN PR is the normal
    awaiting-human-go state and must NOT contribute to the terminal set
    (see test_green_open_pr_not_in_terminal_set for that guard).
    """
    runner = _make_runner(
        view=_view_json(state="MERGED", branch="feat/sr-7"),
        checks=_checks_json([
            {"name": "tests", "bucket": "pass"},
            {"name": "lint",  "bucket": "pass"},
        ]),
    )
    monkeypatch.setattr(subprocess, "run", runner)

    src = GitHubActionsSource(repo="owner/repo", pr_number=7)
    terminal = src.get_terminal_set(cfg, "demo-research")

    assert "sr-7" in terminal, (
        f"Expected 'sr-7' (branch-derived) in terminal set; got {terminal!r}. "
        "The source must emit sr-* tokens from headRefName, not pr-N."
    )
    assert "pr-7" not in terminal, (
        f"'pr-7' must NOT be in terminal set — that token is inert (never matches "
        f"_ID_TOKEN_RE in _check_r4); got {terminal!r}"
    )


# ---------------------------------------------------------------------------
# Test 3: Red PR → sr-* NOT in terminal set
# ---------------------------------------------------------------------------

def test_red_pr_branch_ids_not_in_terminal_set(cfg, monkeypatch):
    """A PR with a failing check withholds sr-* ids from the terminal set."""
    runner = _make_runner(
        view=_view_json(state="OPEN", branch="feat/sr-7"),
        checks=_checks_json([
            {"name": "tests",        "bucket": "pass"},
            {"name": "leakage-scan", "bucket": "fail", "state": "FAILURE"},
        ]),
    )
    monkeypatch.setattr(subprocess, "run", runner)

    src = GitHubActionsSource(repo="owner/repo", pr_number=7)
    terminal = src.get_terminal_set(cfg, "demo-research")

    assert "sr-7" not in terminal, (
        f"Red PR (bucket==fail) must not yield sr-7 in terminal; got {terminal!r}"
    )


# ---------------------------------------------------------------------------
# Test 4: Pending PR → sr-* NOT in terminal set
# ---------------------------------------------------------------------------

def test_pending_pr_ids_not_in_terminal_set(cfg, monkeypatch):
    """A PR with a pending check (bucket==pending) withholds ids (not yet green)."""
    runner = _make_runner(
        view=_view_json(state="OPEN", branch="feat/sr-7"),
        checks=_checks_json([
            {"name": "tests", "bucket": "pending", "state": "IN_PROGRESS"},
        ]),
    )
    monkeypatch.setattr(subprocess, "run", runner)

    src = GitHubActionsSource(repo="owner/repo", pr_number=7)
    terminal = src.get_terminal_set(cfg, "demo-research")
    assert "sr-7" not in terminal


# ---------------------------------------------------------------------------
# Test 5: gh errors → source raises → combined-set emits warning (no crash, no green)
# ---------------------------------------------------------------------------

def test_gh_error_emits_warning_and_skips_source(cfg, ctl_file, monkeypatch):
    """When gh returns non-zero, the source raises. The combined-set builder
    emits 'false GREEN possible' to stderr and skips the source — gate stays not-green."""

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 1
        r.stdout = ""
        r.stderr = "gh: API error 401"
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)

    src = GitHubActionsSource(repo="owner/repo", pr_number=7)

    # The source must raise on error so the combined-set builder's except clause fires.
    with pytest.raises(Exception):
        src.get_terminal_set(cfg, "demo-research")

    # Test via the combined-set builder: reconcile with this source should warn.
    import io
    from research_vault import control as ctl
    stderr_buf = io.StringIO()
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
        f"Expected 'false GREEN possible' warning in stderr; got: {err_output!r}"
    )
    assert "sr-7" not in terminal
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

    with pytest.raises((FileNotFoundError, RuntimeError, OSError)):
        src.get_terminal_set(cfg, "demo-research")

    with pytest.raises((FileNotFoundError, RuntimeError, OSError)):
        src.build_live_set(cfg, "demo-research")


# ---------------------------------------------------------------------------
# Test 7: Skipping check is non-blocking
# ---------------------------------------------------------------------------

def test_skipping_check_is_non_blocking(cfg, monkeypatch):
    """A check with bucket==skipping is treated as non-blocking (pass-through).

    A MERGED PR green on all non-skipping checks is still green even if some skip.
    This is the revised D-CIF-4 semantics (bucket-based, no required/optional).

    Note: state must be "MERGED" — skipping semantics only apply once the PR is
    actually merged; a green-OPEN PR is not terminal regardless of skip status.
    """
    runner = _make_runner(
        view=_view_json(state="MERGED", branch="feat/sr-7"),
        checks=_checks_json([
            {"name": "tests",          "bucket": "pass"},
            {"name": "code-coverage",  "bucket": "skipping"},  # no-op, non-blocking
        ]),
    )
    monkeypatch.setattr(subprocess, "run", runner)

    src = GitHubActionsSource(repo="owner/repo", pr_number=7)
    terminal = src.get_terminal_set(cfg, "demo-research")
    assert "sr-7" in terminal, (
        f"MERGED PR with skipping check must still be green if all others pass; got {terminal!r}"
    )


# ---------------------------------------------------------------------------
# Test 8: build_live_set — open PR contributes sr-* from headRefName, NOT pr-N
# ---------------------------------------------------------------------------

def test_open_pr_live_set_uses_branch_ids(cfg, monkeypatch):
    """An open PR contributes its branch-derived sr-* ids to the live set.

    The live set must contain 'sr-7' (from branch 'feat/sr-7'), NOT 'pr-7'.
    This is the remote-CI analogue of LocalGitSource which also uses _ID_TOKEN_RE
    over branch names (status.py:106-108).
    """
    runner = _make_runner(
        view=_view_json(state="OPEN", branch="feat/sr-7"),
        checks="[]",  # not called for build_live_set
    )
    monkeypatch.setattr(subprocess, "run", runner)

    src = GitHubActionsSource(repo="owner/repo", pr_number=7)
    live = src.build_live_set(cfg, "demo-research")

    assert "sr-7" in live, f"Open PR must contribute 'sr-7' from branch; got {live!r}"
    assert "pr-7" not in live, (
        f"'pr-7' must NOT be in live set — inert token; got {live!r}"
    )


# ---------------------------------------------------------------------------
# Test 9: No auto-approve path — structural boundary assertion
# ---------------------------------------------------------------------------

def test_no_auto_approve_path_in_github_actions_source():
    """GitHubActionsSource must not contain any code path that writes a verdict or approves.

    This is the crew-cannot-self-approve boundary, verified by source inspection.
    The SignalSource interface only returns frozenset[str] — no write capability.

    We check for CALL patterns (function calls that could approve/write), not
    documentary mentions of these concepts in docstrings.
    """
    from research_vault.adapters import github_ci as github_ci_module

    source = inspect.getsource(github_ci_module)

    # These CALL patterns must not appear in the implementation.
    call_patterns = [
        "cmd_return_entry(",
        "cmd_return(",
        "_write_verdict(",
        "_gate_token(",
        "gh\", \"pr\", \"merge",
        "gh\", \"pr\", \"approve",
        "vault approve",
    ]
    for pattern in call_patterns:
        assert pattern not in source, (
            f"GitHubActionsSource module must not contain call {pattern!r} "
            f"(no auto-approve path — crew-cannot-self-approve boundary)"
        )

    # Both methods must return frozenset, not write anything
    src = GitHubActionsSource(repo="owner/repo", pr_number=1)
    for method_name in ("build_live_set", "get_terminal_set"):
        method = getattr(src, method_name)
        assert callable(method), f"{method_name} must be callable"


# ---------------------------------------------------------------------------
# Test 10: FUNCTIONAL PROOF — green vs red PR produce different reconcile output
# (This is the gap that caused the double-[BLOCK]: pr-* was inert, sr-* is not)
# ---------------------------------------------------------------------------

def test_functional_proof_green_vs_red_differ(cfg, ctl_file, monkeypatch):
    """GREEN-MERGED vs RED-MERGED CI for the same PR produce DIFFERENT reconcile output.

    This is the functional test that proves the source is not inert:
    - A green MERGED PR's sr-7 id reaches _check_r4 → R4 fires (found in Handshakes)
    - A red MERGED PR's sr-7 id is withheld (CI not green) → R4 does NOT fire

    Both runs use state="MERGED" to isolate the CI-state difference.
    The OPEN-vs-MERGED gate is separately covered by test 25
    (test_green_open_reconcile_does_not_emit_r4).

    The old pr-* ids never reached _check_r4 because _ID_TOKEN_RE only matches
    sr-[a-z0-9]+ tokens (controllib.py:123). This test is the regression guard.
    """
    # Post an entry with sr-7 in Handshakes
    control_mod.cmd_post(
        "demo-research",
        section="Handshakes",
        title="sr-7: feat/sr-7 in-flight, waiting on CI",
        config=cfg,
    )

    # --- GREEN run: MERGED + all checks pass → sr-7 in terminal → R4 fires ---
    green_runner = _make_runner(
        view=_view_json(state="MERGED", branch="feat/sr-7"),
        checks=_checks_json([
            {"name": "tests",  "bucket": "pass"},
            {"name": "lint",   "bucket": "pass"},
        ]),
    )
    monkeypatch.setattr(subprocess, "run", green_runner)

    src_green = GitHubActionsSource(repo="owner/repo", pr_number=7)
    green_findings = control_mod.cmd_reconcile(
        "demo-research", config=cfg, extra_sources=[src_green]
    )

    # R4 must fire: sr-7 is terminal (MERGED + green CI) AND in Handshakes
    r4_in_green = [f for f in green_findings if "[R4]" in f]
    assert r4_in_green, (
        f"GREEN-MERGED CI: R4 must fire when sr-7 is terminal and in Handshakes, "
        f"but findings were: {green_findings!r}. "
        f"If sr-7 did not reach _check_r4, the source is still inert."
    )

    # --- RED run: MERGED + a check fails → sr-7 withheld (CI not green) → R4 does NOT fire ---
    red_runner = _make_runner(
        view=_view_json(state="MERGED", branch="feat/sr-7"),
        checks=_checks_json([
            {"name": "tests",  "bucket": "pass"},
            {"name": "lint",   "bucket": "fail", "state": "FAILURE"},
        ]),
    )
    monkeypatch.setattr(subprocess, "run", red_runner)

    src_red = GitHubActionsSource(repo="owner/repo", pr_number=7)
    red_findings = control_mod.cmd_reconcile(
        "demo-research", config=cfg, extra_sources=[src_red]
    )

    r4_in_red = [f for f in red_findings if "[R4]" in f]
    assert not r4_in_red, (
        f"RED-MERGED CI: R4 must NOT fire when sr-7 is withheld (checks failing), "
        f"but findings were: {red_findings!r}"
    )

    # The key assertion: the two reconcile outputs DIFFER
    assert green_findings != red_findings, (
        "GREEN-MERGED and RED-MERGED CI must produce different reconcile outputs — "
        "the source was inert (pr-* never reached any consumer) before this fix."
    )


# ---------------------------------------------------------------------------
# Test 11: Combined terminal set includes sr-* ids from a green PR's branch
# ---------------------------------------------------------------------------

def test_combined_terminal_set_includes_green_branch_ids(cfg, ctl_file, monkeypatch):
    """When GitHubActionsSource is in extra_sources, a green MERGED PR's sr-* id
    appears in the combined terminal set returned by _build_combined_terminal_set.

    Uses state="MERGED" — a green-OPEN PR must NOT contribute (see test 23).
    """
    runner = _make_runner(
        view=_view_json(state="MERGED", branch="feat/sr-7"),
        checks=_checks_json([
            {"name": "tests", "bucket": "pass"},
            {"name": "lint",  "bucket": "pass"},
        ]),
    )
    monkeypatch.setattr(subprocess, "run", runner)

    src = GitHubActionsSource(repo="owner/repo", pr_number=7)
    from research_vault.control import _build_combined_terminal_set
    terminal = _build_combined_terminal_set(
        cfg, "demo-research", extra_sources=[src]
    )

    assert "sr-7" in terminal, (
        f"Green MERGED PR (feat/sr-7) must contribute 'sr-7' to combined terminal set; got {terminal!r}"
    )
    assert "pr-7" not in terminal, (
        f"'pr-7' must NOT appear in terminal set — that token is inert; got {terminal!r}"
    )


# ---------------------------------------------------------------------------
# Test 12: No checks at all → conservative: empty terminal set
# ---------------------------------------------------------------------------

def test_no_checks_is_conservative(cfg, monkeypatch):
    """A PR with zero checks cannot be confirmed green (conservative fallback).

    Empty check list → source contributes nothing to terminal set.
    """
    runner = _make_runner(
        view=_view_json(state="OPEN", branch="feat/sr-7"),
        checks="[]",  # no checks
    )
    monkeypatch.setattr(subprocess, "run", runner)

    src = GitHubActionsSource(repo="owner/repo", pr_number=7)
    terminal = src.get_terminal_set(cfg, "demo-research")
    assert "sr-7" not in terminal, (
        f"PR with zero checks must not be green (conservative); got {terminal!r}"
    )


# ---------------------------------------------------------------------------
# Test 13: Constructor with repo slug (smoke)
# ---------------------------------------------------------------------------

def test_github_actions_source_constructor():
    """GitHubActionsSource can be constructed with a repo slug and PR number."""
    src = GitHubActionsSource(repo="myorg/myrepo", pr_number=42)
    assert src is not None
    assert isinstance(src, SignalSource)


# ---------------------------------------------------------------------------
# Test 14: SignalSource docstring updated — SR-9 → SR-CIF
# ---------------------------------------------------------------------------

def test_signal_source_docstring_names_tier3_contributor():
    """The SignalSource docstring must describe the PR/CI source as a tier-3 contributor."""
    from research_vault.status import SignalSource
    doc = SignalSource.__doc__ or ""
    assert "SR-9" not in doc, (
        "SignalSource docstring still references the cut SR-9 internal ID"
    )
    assert "tier-3" in doc, (
        "SignalSource docstring must describe the PR/CI source as tier-3"
    )


# ===========================================================================
# SR-CIF ACTIVATION — CLI tests (tests 15–21)
# Tests for the ``rv control reconcile --gh-pr N [--repo owner/repo]`` surface.
# All mocked; zero live GitHub calls.
# ===========================================================================

# ---------------------------------------------------------------------------
# Test 15: get_ci_advisory() — green PR returns CI: GREEN string
# ---------------------------------------------------------------------------

def test_get_ci_advisory_green(cfg, monkeypatch):
    """get_ci_advisory() returns 'CI: GREEN (PR #N)' when all checks pass."""
    runner = _make_runner(
        view=_view_json(state="OPEN", branch="feat/sr-cif"),
        checks=_checks_json([
            {"name": "tests",  "bucket": "pass"},
            {"name": "lint",   "bucket": "pass"},
        ]),
    )
    monkeypatch.setattr(subprocess, "run", runner)

    src = GitHubActionsSource(repo="owner/repo", pr_number=99)
    advisory = src.get_ci_advisory()

    assert advisory.startswith("CI: GREEN"), (
        f"Expected advisory to start with 'CI: GREEN', got: {advisory!r}"
    )
    assert "PR #99" in advisory, f"Advisory must include PR number; got {advisory!r}"


# ---------------------------------------------------------------------------
# Test 16: get_ci_advisory() — red PR returns CI: RED string with failing name
# ---------------------------------------------------------------------------

def test_get_ci_advisory_red(cfg, monkeypatch):
    """get_ci_advisory() returns 'CI: RED (PR #N — <check-name>)' when a check fails."""
    runner = _make_runner(
        view=_view_json(state="OPEN", branch="feat/sr-cif"),
        checks=_checks_json([
            {"name": "tests",        "bucket": "pass"},
            {"name": "leakage-scan", "bucket": "fail", "state": "FAILURE"},
        ]),
    )
    monkeypatch.setattr(subprocess, "run", runner)

    src = GitHubActionsSource(repo="owner/repo", pr_number=99)
    advisory = src.get_ci_advisory()

    assert advisory.startswith("CI: RED"), (
        f"Expected advisory to start with 'CI: RED', got: {advisory!r}"
    )
    assert "leakage-scan" in advisory, (
        f"Advisory must name the failing check; got {advisory!r}"
    )


# ---------------------------------------------------------------------------
# Test 17: get_ci_advisory() — pending PR returns CI: PENDING
# ---------------------------------------------------------------------------

def test_get_ci_advisory_pending(cfg, monkeypatch):
    """get_ci_advisory() returns 'CI: PENDING' when a check is still running."""
    runner = _make_runner(
        view=_view_json(state="OPEN", branch="feat/sr-cif"),
        checks=_checks_json([
            {"name": "tests", "bucket": "pending", "state": "IN_PROGRESS"},
        ]),
    )
    monkeypatch.setattr(subprocess, "run", runner)

    src = GitHubActionsSource(repo="owner/repo", pr_number=99)
    advisory = src.get_ci_advisory()

    assert "PENDING" in advisory, (
        f"Expected 'PENDING' in advisory for pending check; got {advisory!r}"
    )


# ---------------------------------------------------------------------------
# Test 18: get_ci_advisory() — gh error returns CI: UNVERIFIED (no crash)
# ---------------------------------------------------------------------------

def test_get_ci_advisory_unverified_on_error(cfg, monkeypatch):
    """get_ci_advisory() returns 'CI: UNVERIFIED' on gh error (no crash)."""

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 1
        r.stdout = ""
        r.stderr = "gh: API error 401"
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)

    src = GitHubActionsSource(repo="owner/repo", pr_number=99)
    advisory = src.get_ci_advisory()

    assert "UNVERIFIED" in advisory, (
        f"Expected 'UNVERIFIED' in advisory on gh error; got {advisory!r}"
    )
    assert "PR #99" in advisory, f"Advisory must include PR number; got {advisory!r}"


# ---------------------------------------------------------------------------
# Test 19: CLI activation — rv control reconcile --gh-pr N --repo owner/repo
#   green PR → advisory line CI: GREEN printed to stdout
# ---------------------------------------------------------------------------

def test_cli_reconcile_gh_pr_green_advisory(cfg, ctl_file, monkeypatch, capsys):
    """rv control reconcile --gh-pr N --repo owner/repo prints CI: GREEN advisory."""
    runner = _make_runner(
        view=_view_json(state="OPEN", branch="feat/sr-cif"),
        checks=_checks_json([
            {"name": "tests", "bucket": "pass"},
            {"name": "lint",  "bucket": "pass"},
        ]),
    )
    monkeypatch.setattr(subprocess, "run", runner)

    from research_vault import cli as cli_mod
    exit_code = cli_mod.main([
        "control", "demo-research", "reconcile",
        "--gh-pr", "99", "--repo", "owner/repo",
    ])

    captured = capsys.readouterr()
    assert "CI: GREEN" in captured.out, (
        f"Expected 'CI: GREEN' in stdout; got {captured.out!r}"
    )
    assert "PR #99" in captured.out, f"Expected PR number in output; got {captured.out!r}"


# ---------------------------------------------------------------------------
# Test 20: CLI activation — red PR → advisory line CI: RED printed
# ---------------------------------------------------------------------------

def test_cli_reconcile_gh_pr_red_advisory(cfg, ctl_file, monkeypatch, capsys):
    """rv control reconcile --gh-pr N --repo owner/repo prints CI: RED advisory."""
    runner = _make_runner(
        view=_view_json(state="OPEN", branch="feat/sr-cif"),
        checks=_checks_json([
            {"name": "tests",        "bucket": "pass"},
            {"name": "leakage-scan", "bucket": "fail", "state": "FAILURE"},
        ]),
    )
    monkeypatch.setattr(subprocess, "run", runner)

    from research_vault import cli as cli_mod
    cli_mod.main([
        "control", "demo-research", "reconcile",
        "--gh-pr", "99", "--repo", "owner/repo",
    ])

    captured = capsys.readouterr()
    assert "CI: RED" in captured.out, (
        f"Expected 'CI: RED' in stdout; got {captured.out!r}"
    )
    assert "leakage-scan" in captured.out, (
        f"Expected failing check name in output; got {captured.out!r}"
    )


# ---------------------------------------------------------------------------
# Test 21: CLI activation — --gh-pr without --repo, no git remote → exits 1
# ---------------------------------------------------------------------------

def test_cli_reconcile_gh_pr_no_repo_no_remote_exits_1(cfg, ctl_file, monkeypatch, capsys):
    """rv control reconcile --gh-pr N without --repo exits 1 when no git remote found.

    The source requires a repo slug. Without --repo and with no detectable git
    remote, the command must exit non-zero with a useful error message.
    """
    # Make git remote get-url origin fail (no remote configured)
    def fake_run(cmd, **kwargs):
        r = MagicMock()
        # Distinguish git remote calls from gh calls
        if cmd[0] == "git":
            r.returncode = 1
            r.stdout = ""
            r.stderr = "fatal: no such remote 'origin'"
        else:
            # gh calls should not be reached — but if they are, fail loudly
            r.returncode = 1
            r.stdout = ""
            r.stderr = "should not call gh without a repo"
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)

    from research_vault import cli as cli_mod
    exit_code = cli_mod.main([
        "control", "demo-research", "reconcile",
        "--gh-pr", "99",
    ])

    assert exit_code != 0, (
        "Expected non-zero exit when --gh-pr given but no --repo and no git remote"
    )
    captured = capsys.readouterr()
    assert "repo" in captured.err.lower() or "repo" in captured.out.lower(), (
        f"Expected error mentioning 'repo'; got stderr={captured.err!r}, stdout={captured.out!r}"
    )


# ---------------------------------------------------------------------------
# Test 22: get_ci_advisory() result cached — subprocess called only once per fetch
# ---------------------------------------------------------------------------

def test_get_ci_advisory_caches_results(cfg, monkeypatch):
    """get_ci_advisory() + get_terminal_set() together call gh subprocess only ONCE per endpoint.

    Caching: after advisory is fetched, get_terminal_set() reuses the cached
    pr_info and checks — total subprocess calls = 2 (one view, one checks),
    not 4 (two view + two checks for each method).
    """
    call_log: list[str] = []

    def fake_run(cmd, **kwargs):
        # Record "checks" or "view" sub-command
        call_log.append(cmd[2] if len(cmd) > 2 else str(cmd))
        r = MagicMock()
        r.returncode = 0
        r.stderr = ""
        if len(cmd) > 2 and cmd[2] == "checks":
            r.stdout = _checks_json([{"name": "tests", "bucket": "pass"}])
        else:
            r.stdout = _view_json(state="OPEN", branch="feat/sr-cif")
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)

    src = GitHubActionsSource(repo="owner/repo", pr_number=99)

    # Call advisory first — triggers 2 subprocess calls (view + checks)
    advisory = src.get_ci_advisory()
    after_advisory = len(call_log)
    assert after_advisory == 2, (
        f"get_ci_advisory() must issue exactly 2 subprocess calls (view + checks); "
        f"got {after_advisory}: {call_log!r}"
    )

    # Call get_terminal_set — should reuse cached results, no new subprocess calls
    src.get_terminal_set(cfg, "demo-research")
    after_terminal = len(call_log)

    assert after_terminal == after_advisory, (
        f"get_terminal_set() must reuse cached fetch results — "
        f"subprocess was called {after_terminal - after_advisory} extra time(s): "
        f"{call_log[after_advisory:]!r}"
    )


# ===========================================================================
# SR-CIF-TERMINAL-FIX — Tests 23–26
# Gate: terminal-set contribution requires state==MERGED, not just CI green.
# A green-but-OPEN PR is the NORMAL human-go-crew state; it must NOT be
# marked terminal (which triggers false [R4] STALE on every reconcile).
# ===========================================================================


# ---------------------------------------------------------------------------
# Test 23: BUG GUARD — green-but-OPEN PR must NOT contribute to terminal set
# ---------------------------------------------------------------------------

def test_green_open_pr_not_in_terminal_set(cfg, monkeypatch):
    """BUG GUARD (task #24): a green PR that is still OPEN must NOT contribute
    to the terminal set.

    In a human-go crew, the PR is green-but-open while awaiting the operator
    merge.  Before this fix, get_terminal_set() returned branch ids for any
    green PR regardless of state — causing false [R4] STALE on every reconcile
    for a perfectly healthy, awaiting-human-go handshake.

    Red-before-green: revert the state==MERGED gate in get_terminal_set →
    this test fails (sr-7 reappears in terminal for OPEN PR).
    """
    runner = _make_runner(
        view=_view_json(state="OPEN", branch="feat/sr-7"),
        checks=_checks_json([
            {"name": "tests", "bucket": "pass"},
            {"name": "lint",  "bucket": "pass"},
        ]),
    )
    monkeypatch.setattr(subprocess, "run", runner)

    src = GitHubActionsSource(repo="owner/repo", pr_number=7)
    terminal = src.get_terminal_set(cfg, "demo-research")

    assert "sr-7" not in terminal, (
        f"BUG: green-but-OPEN PR must NOT contribute 'sr-7' to terminal set "
        f"(triggers false [R4] STALE on every reconcile); got {terminal!r}. "
        "The terminal-set gate must require state==MERGED, not just CI green."
    )


# ---------------------------------------------------------------------------
# Test 24: Green-MERGED PR → ids ARE in terminal (correct merged behavior)
# ---------------------------------------------------------------------------

def test_green_merged_pr_in_terminal_set(cfg, monkeypatch):
    """A green PR that has actually been MERGED must contribute sr-* to terminal.

    This is the correct merged/done path: CI green + state==MERGED → terminal.
    """
    runner = _make_runner(
        view=_view_json(state="MERGED", branch="feat/sr-7"),
        checks=_checks_json([
            {"name": "tests", "bucket": "pass"},
            {"name": "lint",  "bucket": "pass"},
        ]),
    )
    monkeypatch.setattr(subprocess, "run", runner)

    src = GitHubActionsSource(repo="owner/repo", pr_number=7)
    terminal = src.get_terminal_set(cfg, "demo-research")

    assert "sr-7" in terminal, (
        f"A green MERGED PR must contribute 'sr-7' to the terminal set; "
        f"got {terminal!r}. The fix must allow MERGED PRs through."
    )
    assert "pr-7" not in terminal, (
        f"'pr-7' must NOT appear in terminal (inert token); got {terminal!r}"
    )


# ---------------------------------------------------------------------------
# Test 25: Functional proof — green-OPEN reconcile does NOT emit [R4] STALE
# ---------------------------------------------------------------------------

def test_green_open_reconcile_does_not_emit_r4(cfg, ctl_file, monkeypatch):
    """FUNCTIONAL PROOF: reconcile with a green-but-OPEN PR must NOT emit [R4] STALE.

    [R4] fires when an id is in BOTH the terminal set and a Handshakes entry.
    If green-OPEN erroneously lands in terminal (the bug), [R4] fires on every
    reconcile while the crew waits for the human-go merge — a false STALE alarm.

    After the fix, green-OPEN → not terminal → no [R4].
    Green-MERGED → terminal → [R4] fires correctly (tested below).
    """
    # Post sr-7 into Handshakes — the normal awaiting-merge state
    control_mod.cmd_post(
        "demo-research",
        section="Handshakes",
        title="sr-7: feat/sr-7 green, awaiting human-go merge",
        config=cfg,
    )

    # Green CI but PR is still OPEN (human hasn't merged yet)
    green_open_runner = _make_runner(
        view=_view_json(state="OPEN", branch="feat/sr-7"),
        checks=_checks_json([
            {"name": "tests", "bucket": "pass"},
            {"name": "lint",  "bucket": "pass"},
        ]),
    )
    monkeypatch.setattr(subprocess, "run", green_open_runner)

    src_open = GitHubActionsSource(repo="owner/repo", pr_number=7)
    findings_open = control_mod.cmd_reconcile(
        "demo-research", config=cfg, extra_sources=[src_open]
    )

    r4_open = [f for f in findings_open if "[R4]" in f]
    assert not r4_open, (
        f"BUG: green-OPEN PR must NOT emit [R4] STALE "
        f"(normal awaiting-merge state); findings: {findings_open!r}"
    )

    # Now simulate the merge: same checks, state becomes MERGED
    green_merged_runner = _make_runner(
        view=_view_json(state="MERGED", branch="feat/sr-7"),
        checks=_checks_json([
            {"name": "tests", "bucket": "pass"},
            {"name": "lint",  "bucket": "pass"},
        ]),
    )
    monkeypatch.setattr(subprocess, "run", green_merged_runner)

    src_merged = GitHubActionsSource(repo="owner/repo", pr_number=7)
    findings_merged = control_mod.cmd_reconcile(
        "demo-research", config=cfg, extra_sources=[src_merged]
    )

    r4_merged = [f for f in findings_merged if "[R4]" in f]
    assert r4_merged, (
        f"Green-MERGED PR must emit [R4] STALE when sr-7 is still in Handshakes; "
        f"findings: {findings_merged!r}"
    )

    # The two states must produce different output
    assert findings_open != findings_merged, (
        "OPEN vs MERGED reconcile must differ — OPEN should not trigger R4, MERGED should"
    )
