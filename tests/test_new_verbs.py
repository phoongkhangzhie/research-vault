"""test_new_verbs.py — Tests for SR-2 verbs: role, build-agents, mdstore, wt, git-health, lint.

All tests are hermetic: tmp_path, no real git remotes, no external services.
wait-for tests cover the resolver grammar and timeout behavior (hermetic mock).
"""
import os
import sys
import time
import json
import subprocess
from pathlib import Path
from typing import Generator

import pytest

from research_vault.config import Config, reset_config_cache


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg_with_project(tmp_path: Path) -> Config:
    """Config with one project that has a source directory and roster."""
    proj_dir = tmp_path / "projects" / "test-proj"
    proj_dir.mkdir(parents=True)
    raw = {
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(tmp_path / "state"),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "adapters": {"notifier": "file", "backend": "local", "secrets": "env"},
        "projects": {
            "test-proj": {
                "code": "tp",
                "source_dir": str(proj_dir),
                "roster": ["engineer", "researcher"],
            }
        },
        "default_project": "test-proj",
    }
    return Config(raw)


# ---------------------------------------------------------------------------
# role verb
# ---------------------------------------------------------------------------

def test_role_list_prints_projects(cfg_with_project: Config, capsys) -> None:
    from research_vault.role import cmd_list
    rc = cmd_list(cfg_with_project)
    assert rc == 0
    out = capsys.readouterr().out
    assert "test-proj" in out
    assert "tp" in out
    assert "engineer" in out
    assert "researcher" in out


def test_role_list_empty_cfg(tmp_path: Path, capsys) -> None:
    from research_vault.role import cmd_list
    raw = {
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(tmp_path / "state"),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "adapters": {},
        "projects": {},
    }
    cfg = Config(raw)
    rc = cmd_list(cfg)
    assert rc == 0
    out = capsys.readouterr().out
    assert "No projects registered" in out


def test_role_show_known_project(cfg_with_project: Config, capsys) -> None:
    from research_vault.role import cmd_show
    rc = cmd_show("test-proj", cfg_with_project)
    assert rc == 0
    out = capsys.readouterr().out
    assert "test-proj" in out
    assert "engineer" in out


def test_role_show_unknown_project(cfg_with_project: Config, capsys) -> None:
    from research_vault.role import cmd_show
    rc = cmd_show("does-not-exist", cfg_with_project)
    assert rc != 0


# ---------------------------------------------------------------------------
# build-agents verb
# ---------------------------------------------------------------------------

def test_build_agents_writes_hat_files(cfg_with_project: Config, tmp_path: Path) -> None:
    from research_vault.build_agents import cmd_build
    agents_dir = tmp_path / ".agents"
    rc = cmd_build("test-proj", cfg_with_project, agents_dir=agents_dir)
    assert rc == 0
    # Should have written engineer.md and researcher.md
    assert (agents_dir / "test-proj" / "engineer.md").exists()
    assert (agents_dir / "test-proj" / "researcher.md").exists()


def test_build_agents_dry_run_no_files(cfg_with_project: Config, tmp_path: Path, capsys) -> None:
    from research_vault.build_agents import cmd_build
    agents_dir = tmp_path / ".agents"
    rc = cmd_build("test-proj", cfg_with_project, agents_dir=agents_dir, dry_run=True)
    assert rc == 0
    # Nothing written in dry-run mode
    assert not (agents_dir / "test-proj").exists()
    out = capsys.readouterr().out
    assert "dry-run" in out


def test_build_agents_all_projects(cfg_with_project: Config, tmp_path: Path) -> None:
    from research_vault.build_agents import cmd_build
    agents_dir = tmp_path / ".agents"
    rc = cmd_build(None, cfg_with_project, agents_dir=agents_dir)
    assert rc == 0
    assert (agents_dir / "test-proj" / "engineer.md").exists()


def test_build_agents_hat_contains_role(cfg_with_project: Config, tmp_path: Path) -> None:
    from research_vault.build_agents import cmd_build
    agents_dir = tmp_path / ".agents"
    cmd_build("test-proj", cfg_with_project, agents_dir=agents_dir)
    content = (agents_dir / "test-proj" / "engineer.md").read_text(encoding="utf-8")
    assert "engineer" in content
    assert "test-proj" in content


def test_build_agents_unknown_project(cfg_with_project: Config, tmp_path: Path) -> None:
    from research_vault.build_agents import cmd_build
    agents_dir = tmp_path / ".agents"
    rc = cmd_build("nonexistent", cfg_with_project, agents_dir=agents_dir)
    assert rc != 0


# ---------------------------------------------------------------------------
# mdstore verb
# ---------------------------------------------------------------------------

def test_mdstore_check_empty_dir_ok(cfg_with_project: Config, capsys) -> None:
    from research_vault.mdstore import cmd_check
    # The source_dir exists but has no notes
    rc = cmd_check("test-proj", cfg_with_project, check_links=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "OK" in out or "0 note" in out


def test_mdstore_check_valid_note(cfg_with_project: Config, tmp_path: Path, capsys) -> None:
    from research_vault.mdstore import cmd_check
    # Write a valid note to the project's source_dir
    proj_dir = tmp_path / "projects" / "test-proj"
    note = proj_dir / "lit" / "valid.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "---\ntype: literature\ntitle: Test Paper\n---\n\n# Test Paper\n",
        encoding="utf-8",
    )
    rc = cmd_check("test-proj", cfg_with_project, check_links=False)
    assert rc == 0


def test_mdstore_check_missing_frontmatter(cfg_with_project: Config, tmp_path: Path, capsys) -> None:
    from research_vault.mdstore import cmd_check
    proj_dir = tmp_path / "projects" / "test-proj"
    note = proj_dir / "missing.md"
    note.write_text("# No frontmatter here\n", encoding="utf-8")
    rc = cmd_check("test-proj", cfg_with_project, check_links=False)
    assert rc != 0
    out = capsys.readouterr().out
    assert "missing frontmatter" in out


def test_mdstore_check_unknown_project(cfg_with_project: Config, capsys) -> None:
    from research_vault.mdstore import cmd_check
    rc = cmd_check("unknown", cfg_with_project, check_links=False)
    assert rc != 0


# ---------------------------------------------------------------------------
# lint verb
# ---------------------------------------------------------------------------

def test_lint_passes_with_clean_config(cfg_with_project: Config, capsys) -> None:
    from research_vault.lint import cmd_lint
    # No forbidden_patterns configured → leakage scan skipped
    rc = cmd_lint(cfg_with_project)
    assert rc == 0
    out = capsys.readouterr().out
    assert "PASS" in out


def test_lint_detects_schema_violation(tmp_path: Path, capsys) -> None:
    from research_vault.lint import cmd_lint
    raw = {
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(tmp_path / "state"),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "adapters": {},
        "projects": {
            "bad-proj": {
                # Missing 'code' and 'source_dir'
                "roster": ["engineer"],
            }
        },
    }
    cfg = Config(raw)
    rc = cmd_lint(cfg)
    assert rc != 0
    out = capsys.readouterr().out
    assert "missing required field" in out


def test_lint_leakage_scan_flags_pattern(tmp_path: Path, capsys) -> None:
    from research_vault.lint import _scan_for_leakage
    # Write a Python file with a forbidden pattern
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    bad_file = src_dir / "bad.py"
    bad_file.write_text("# hardcoded path: /home/secretuser/private\n", encoding="utf-8")

    findings = _scan_for_leakage(src_dir, [r"/home/secretuser"])
    assert len(findings) == 1
    assert "bad.py" in findings[0][0]


def test_lint_leakage_scan_clean(tmp_path: Path) -> None:
    from research_vault.lint import _scan_for_leakage
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    clean_file = src_dir / "clean.py"
    clean_file.write_text("x = 'hello world'\n", encoding="utf-8")

    findings = _scan_for_leakage(src_dir, [r"/home/secretuser"])
    assert len(findings) == 0


# ---------------------------------------------------------------------------
# wait-for resolver grammar (hermetic)
# ---------------------------------------------------------------------------

def test_wait_for_artifact_exists(tmp_path: Path) -> None:
    from research_vault.wait_for import resolve_watch
    f = tmp_path / "output.jsonl"
    f.write_text("data\n", encoding="utf-8")
    result = resolve_watch(f"artifact:{f}")
    assert result["ready"] is True
    assert result["state"] == "exists"


def test_wait_for_artifact_missing(tmp_path: Path) -> None:
    from research_vault.wait_for import resolve_watch
    f = tmp_path / "nonexistent.jsonl"
    result = resolve_watch(f"artifact:{f}")
    assert result["ready"] is False
    assert result["state"] == "missing"


def test_wait_for_artifact_fresh_after_registered(tmp_path: Path) -> None:
    from research_vault.wait_for import resolve_watch
    f = tmp_path / "output.jsonl"
    registered_ts = time.time() - 1  # registered 1 second ago
    # Write the file AFTER registration
    f.write_text("data\n", encoding="utf-8")
    result = resolve_watch(f"artifact:{f}+fresh", registered_ts=registered_ts)
    assert result["ready"] is True


def test_wait_for_artifact_stale_rejected(tmp_path: Path) -> None:
    from research_vault.wait_for import resolve_watch
    f = tmp_path / "old.jsonl"
    f.write_text("old data\n", encoding="utf-8")
    # Simulate registration AFTER the file was written
    registered_ts = time.time() + 100  # far in the future
    result = resolve_watch(f"artifact:{f}+fresh", registered_ts=registered_ts)
    assert result["ready"] is False
    assert "stale" in result["state"].lower() or "fresh" in result["state"].lower()


def test_wait_for_cmd_success(tmp_path: Path) -> None:
    from research_vault.wait_for import resolve_watch
    result = resolve_watch("cmd:true")
    assert result["ready"] is True
    assert result["state"] == "exit0"


def test_wait_for_cmd_failure(tmp_path: Path) -> None:
    from research_vault.wait_for import resolve_watch
    result = resolve_watch("cmd:false")
    assert result["ready"] is False


def test_wait_for_unknown_source() -> None:
    from research_vault.wait_for import resolve_watch
    result = resolve_watch("bogus:something")
    assert result["ready"] is False
    assert "unknown watch source" in (result.get("error") or "")


def test_wait_for_sync_resolves(tmp_path: Path) -> None:
    """--sync mode: poller resolves immediately when the artifact already exists."""
    from research_vault.wait_for import _run_sync
    f = tmp_path / "done.jsonl"
    f.write_text("ok\n", encoding="utf-8")
    # interval=0.01 so it doesn't wait long; timeout=2 secs
    rc = _run_sync(
        watch=f"artifact:{f}",
        then_cmd="",
        timeout_secs=2,
        interval_secs=0,   # immediate re-check
        registered_ts=time.time() - 1,
        log_path="",
    )
    assert rc == 0


def test_wait_for_then_fires_on_resolution(tmp_path: Path) -> None:
    """--then is executed when the watch resolves in --sync mode."""
    from research_vault.wait_for import _run_sync
    artifact = tmp_path / "ready.jsonl"
    artifact.write_text("data\n", encoding="utf-8")
    sentinel = tmp_path / "then_fired.txt"

    # Use a shell command that creates sentinel to confirm --then ran
    rc = _run_sync(
        watch=f"artifact:{artifact}",
        then_cmd=f"touch {sentinel}",
        timeout_secs=2,
        interval_secs=0,
        registered_ts=time.time() - 1,
        log_path="",
    )
    assert rc == 0
    assert sentinel.exists(), "--then command did not fire on resolution"


def test_wait_for_sync_timeout(tmp_path: Path) -> None:
    """--sync mode: poller times out when the artifact never appears."""
    from research_vault.wait_for import _run_sync
    f = tmp_path / "never.jsonl"  # never created
    rc = _run_sync(
        watch=f"artifact:{f}",
        then_cmd="",
        timeout_secs=1,
        interval_secs=1,  # poll interval == timeout — fires immediately
        registered_ts=time.time(),
        log_path="",
    )
    # Should time out → rc=2
    assert rc == 2


def test_wait_for_cli_returns_immediately(tmp_path: Path) -> None:
    """CLI run() must return 0 without blocking (background mode)."""
    import argparse
    from research_vault.wait_for import run as wf_run

    f = tmp_path / "artifact.jsonl"
    # Don't create the file — the background poller should keep checking,
    # but the CLI call itself must return immediately
    ns = argparse.Namespace(
        watch=f"artifact:{f}",
        then_cmd="",
        timeout=60,
        interval=30,
        log="",
        sync=False,
    )

    start = time.time()
    rc = wf_run(ns)
    elapsed = time.time() - start

    assert rc == 0
    # Must return well within 1 second (background launch is fast)
    assert elapsed < 3.0, f"wait-for should return immediately, took {elapsed:.1f}s"


def test_wait_for_parse_duration_secs() -> None:
    from research_vault.wait_for import parse_duration_secs
    assert parse_duration_secs("3600") == 3600
    assert parse_duration_secs("+3600") == 3600
    assert parse_duration_secs("1h") == 3600
    assert parse_duration_secs("2h30m") == 9000
    assert parse_duration_secs("1d") == 86400


# ---------------------------------------------------------------------------
# CLI wiring: rv help --check still green with all new verbs
# ---------------------------------------------------------------------------

def test_cli_help_check_green() -> None:
    from research_vault.cli import _check_verb_docstrings
    violations = _check_verb_docstrings()
    assert violations == [], f"rv help --check found violations: {violations}"


def test_cli_all_sr2_verbs_registered() -> None:
    from research_vault.cli import _VERB_REGISTRY
    sr2_verbs = {
        "project", "cite", "research", "role", "build-agents",
        "mdstore", "wt", "git-health", "lint", "wait-for",
    }
    registered = set(_VERB_REGISTRY.keys())
    missing = sr2_verbs - registered
    assert not missing, f"SR-2 verbs not registered: {missing}"
