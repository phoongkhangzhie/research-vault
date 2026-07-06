"""test_sr_start.py — hermetic tests for rv start.

Test coverage:
  1. Not-a-vault (no research_vault.toml + CLAUDE.md) → clear error, exit 1, no exec.
  2. claude missing from PATH → clear error, exit 1, no exec.
  3. Happy path: chdirs to vault, execs ["claude", ...].
  4. Happy path with passthrough args: forwarded to claude.
  5. rv start <path> resolves the given path; bare rv start resolves cwd.
  6. Vault missing only research_vault.toml → not-a-vault error.
  7. Vault missing only CLAUDE.md → not-a-vault error.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.start import cmd_start, _is_vault


# ---------------------------------------------------------------------------
# _is_vault unit tests
# ---------------------------------------------------------------------------

def test_is_vault_both_files_present(tmp_path: Path) -> None:
    (tmp_path / "research_vault.toml").write_text("[instance]\n")
    (tmp_path / "CLAUDE.md").write_text("# hub\n")
    assert _is_vault(tmp_path) is True


def test_is_vault_missing_toml(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("# hub\n")
    assert _is_vault(tmp_path) is False


def test_is_vault_missing_claude_md(tmp_path: Path) -> None:
    (tmp_path / "research_vault.toml").write_text("[instance]\n")
    assert _is_vault(tmp_path) is False


def test_is_vault_empty_dir(tmp_path: Path) -> None:
    assert _is_vault(tmp_path) is False


# ---------------------------------------------------------------------------
# cmd_start preflight: not-a-vault
# ---------------------------------------------------------------------------

def test_not_a_vault_exits_1(tmp_path: Path, capsys) -> None:
    """A directory without research_vault.toml + CLAUDE.md → exit 1, no exec."""
    exec_called = []

    rc = cmd_start(
        vault_path=str(tmp_path),
        passthrough_args=[],
        _which_fn=lambda _: "/usr/bin/claude",
        _chdir_fn=lambda _: None,
        _execvp_fn=lambda *a: exec_called.append(a),
    )

    assert rc == 1
    assert exec_called == [], "execvp must NOT be called on a non-vault dir"
    err = capsys.readouterr().err
    assert "research_vault.toml" in err
    assert "CLAUDE.md" in err
    assert "rv init" in err


def test_not_a_vault_missing_toml_only(tmp_path: Path, capsys) -> None:
    """Missing research_vault.toml → not-a-vault error."""
    (tmp_path / "CLAUDE.md").write_text("# hub\n")
    exec_called = []

    rc = cmd_start(
        vault_path=str(tmp_path),
        passthrough_args=[],
        _which_fn=lambda _: "/usr/bin/claude",
        _chdir_fn=lambda _: None,
        _execvp_fn=lambda *a: exec_called.append(a),
    )

    assert rc == 1
    assert exec_called == []


def test_not_a_vault_missing_claude_md_only(tmp_path: Path, capsys) -> None:
    """Missing CLAUDE.md → not-a-vault error."""
    (tmp_path / "research_vault.toml").write_text("[instance]\n")
    exec_called = []

    rc = cmd_start(
        vault_path=str(tmp_path),
        passthrough_args=[],
        _which_fn=lambda _: "/usr/bin/claude",
        _chdir_fn=lambda _: None,
        _execvp_fn=lambda *a: exec_called.append(a),
    )

    assert rc == 1
    assert exec_called == []


# ---------------------------------------------------------------------------
# cmd_start preflight: claude not on PATH
# ---------------------------------------------------------------------------

def test_claude_not_on_path_exits_1(tmp_path: Path, capsys) -> None:
    """claude missing from PATH → exit 1, clear error message, no exec."""
    (tmp_path / "research_vault.toml").write_text("[instance]\n")
    (tmp_path / "CLAUDE.md").write_text("# hub\n")

    exec_called = []
    chdir_called = []

    rc = cmd_start(
        vault_path=str(tmp_path),
        passthrough_args=[],
        _which_fn=lambda _: None,            # simulate missing claude
        _chdir_fn=lambda p: chdir_called.append(p),
        _execvp_fn=lambda *a: exec_called.append(a),
    )

    assert rc == 1
    assert exec_called == [], "execvp must NOT be called when claude is absent"
    assert chdir_called == [], "chdir must NOT be called when claude is absent"
    err = capsys.readouterr().err
    assert "claude" in err.lower()
    assert "PATH" in err or "path" in err.lower()
    assert "Install" in err or "install" in err.lower()


# ---------------------------------------------------------------------------
# cmd_start happy path
# ---------------------------------------------------------------------------

def _make_vault(tmp_path: Path) -> Path:
    (tmp_path / "research_vault.toml").write_text("[instance]\n")
    (tmp_path / "CLAUDE.md").write_text("# hub\n")
    return tmp_path


def test_happy_path_chdirs_and_execs(tmp_path: Path) -> None:
    """Happy path: chdirs to vault, execs ['claude']."""
    vault = _make_vault(tmp_path)
    chdir_calls = []
    exec_calls = []

    rc = cmd_start(
        vault_path=str(vault),
        passthrough_args=[],
        _which_fn=lambda _: "/usr/bin/claude",
        _chdir_fn=lambda p: chdir_calls.append(p),
        _execvp_fn=lambda prog, argv: exec_calls.append((prog, argv)),
    )

    # execvp normally never returns; our mock returns None.
    # cmd_start should have attempted the exec, then hit the post-exec error path.
    # The important assertions: chdir was called with the vault path, execvp was called.
    assert chdir_calls == [str(vault)]
    assert len(exec_calls) == 1
    prog, argv = exec_calls[0]
    assert prog == "claude"
    assert argv == ["claude"]


def test_happy_path_passthrough_args(tmp_path: Path) -> None:
    """Passthrough args are forwarded to claude."""
    vault = _make_vault(tmp_path)
    exec_calls = []

    cmd_start(
        vault_path=str(vault),
        passthrough_args=["--dangerously-skip-permissions"],
        _which_fn=lambda _: "/usr/bin/claude",
        _chdir_fn=lambda _: None,
        _execvp_fn=lambda prog, argv: exec_calls.append((prog, argv)),
    )

    assert len(exec_calls) == 1
    _, argv = exec_calls[0]
    assert argv == ["claude", "--dangerously-skip-permissions"]


def test_happy_path_multiple_passthrough_args(tmp_path: Path) -> None:
    """Multiple passthrough args all forwarded."""
    vault = _make_vault(tmp_path)
    exec_calls = []

    cmd_start(
        vault_path=str(vault),
        passthrough_args=["--resume", "abc123"],
        _which_fn=lambda _: "/usr/bin/claude",
        _chdir_fn=lambda _: None,
        _execvp_fn=lambda prog, argv: exec_calls.append((prog, argv)),
    )

    assert len(exec_calls) == 1
    _, argv = exec_calls[0]
    assert argv == ["claude", "--resume", "abc123"]


# ---------------------------------------------------------------------------
# Path resolution: explicit path vs cwd
# ---------------------------------------------------------------------------

def test_explicit_path_is_resolved(tmp_path: Path) -> None:
    """rv start <path> resolves the given path."""
    vault = _make_vault(tmp_path)
    chdir_calls = []

    cmd_start(
        vault_path=str(vault),
        passthrough_args=[],
        _which_fn=lambda _: "/usr/bin/claude",
        _chdir_fn=lambda p: chdir_calls.append(p),
        _execvp_fn=lambda *a: None,
    )

    assert chdir_calls == [str(vault)]


def test_no_path_uses_cwd(tmp_path: Path, monkeypatch) -> None:
    """Bare rv start resolves cwd."""
    vault = _make_vault(tmp_path)
    monkeypatch.chdir(str(vault))

    chdir_calls = []

    cmd_start(
        vault_path=None,
        passthrough_args=[],
        _which_fn=lambda _: "/usr/bin/claude",
        _chdir_fn=lambda p: chdir_calls.append(p),
        _execvp_fn=lambda *a: None,
    )

    assert len(chdir_calls) == 1
    assert chdir_calls[0] == str(vault)


# ---------------------------------------------------------------------------
# Error message content checks
# ---------------------------------------------------------------------------

def test_not_vault_error_includes_dir_name(tmp_path: Path, capsys) -> None:
    """The error for a non-vault dir must name the dir."""
    rc = cmd_start(
        vault_path=str(tmp_path),
        passthrough_args=[],
        _which_fn=lambda _: "/usr/bin/claude",
        _chdir_fn=lambda _: None,
        _execvp_fn=lambda *a: None,
    )
    assert rc == 1
    err = capsys.readouterr().err
    # The dir path or name should appear in the error.
    assert str(tmp_path) in err or tmp_path.name in err


def test_claude_missing_error_mentions_rv_start(tmp_path: Path, capsys) -> None:
    """The runtime-missing error should mention rv start so adopters know the context."""
    _make_vault(tmp_path)

    cmd_start(
        vault_path=str(tmp_path),
        passthrough_args=[],
        _which_fn=lambda _: None,
        _chdir_fn=lambda _: None,
        _execvp_fn=lambda *a: None,
    )
    err = capsys.readouterr().err
    assert "rv start" in err
