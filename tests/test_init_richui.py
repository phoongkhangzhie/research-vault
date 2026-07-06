"""test_init_richui.py — rv init verbosity + rich-rendering acceptance tests.

Acceptance criteria (fix/init-no-onboard-chain verbosity + rich pass):

1. Default output (verbose=False) contains NO ``created:`` / ``Written:`` /
   ``build-agents`` / ``git:`` file-inventory lines.
2. ``verbose=True`` restores the full file-inventory lines.
3. The ``--verbose`` flag exists in the parser.
4. The terse closing block always contains the expected next-steps content.
5. ``render_init`` emits 0 ANSI codes when NO_COLOR is set.
6. ``render_init`` emits 0 ANSI codes when RV_PLAIN is set.
7. The plain-path text contains the key phrases (``rv onboard``, ``rv start``,
   ``cd``, ``QUICKSTART.md``).
"""
from __future__ import annotations

import re
import sys

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mKJHF]|\x1b]8;[^;]*;[^\x07]*\x07")


def _has_ansi(text: str) -> bool:
    return bool(_ANSI_RE.search(text))


# ---------------------------------------------------------------------------
# 1 & 2. Terse-by-default + --verbose restores
# ---------------------------------------------------------------------------

class TestInitVerbosity:
    """Default output omits file inventory; --verbose restores it."""

    def test_default_no_created_lines(self, tmp_path, capsys):
        """Default rv init must NOT print any ``created:`` lines."""
        from research_vault.init import cmd_init_in_dir
        rc = cmd_init_in_dir(str(tmp_path / "myvault"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "created:" not in out, (
            f"Default output must not contain 'created:' lines; got:\n{out!r}"
        )

    def test_default_no_written_lines(self, tmp_path, capsys):
        """Default rv init must NOT print any ``Written:`` lines (from build-agents)."""
        from research_vault.init import cmd_init_in_dir
        rc = cmd_init_in_dir(str(tmp_path / "myvault"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "Written:" not in out, (
            f"Default output must not contain 'Written:' lines; got:\n{out!r}"
        )

    def test_default_no_build_agents_line(self, tmp_path, capsys):
        """Default rv init must NOT print the ``build-agents (claude-code): generated …`` line."""
        from research_vault.init import cmd_init_in_dir
        rc = cmd_init_in_dir(str(tmp_path / "myvault"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "build-agents" not in out, (
            f"Default output must not contain 'build-agents' lines; got:\n{out!r}"
        )

    def test_default_no_git_line(self, tmp_path, capsys):
        """Default rv init must NOT print ``git: initialised repo …`` line."""
        from research_vault.init import cmd_init_in_dir
        rc = cmd_init_in_dir(str(tmp_path / "myvault"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "git: initialised" not in out, (
            f"Default output must not contain 'git: initialised' line; got:\n{out!r}"
        )

    def test_verbose_restores_created_lines(self, tmp_path, capsys):
        """``verbose=True`` must print ``created:`` lines."""
        from research_vault.init import cmd_init_in_dir
        rc = cmd_init_in_dir(str(tmp_path / "myvault"), verbose=True)
        assert rc == 0
        out = capsys.readouterr().out
        assert "created:" in out, (
            f"verbose=True must print 'created:' lines; got:\n{out!r}"
        )

    def test_verbose_restores_git_line(self, tmp_path, capsys):
        """``verbose=True`` must print the git init success line."""
        from research_vault.init import cmd_init_in_dir
        rc = cmd_init_in_dir(str(tmp_path / "myvault"), verbose=True)
        assert rc == 0
        out = capsys.readouterr().out
        assert "git: initialised" in out, (
            f"verbose=True must print 'git: initialised' line; got:\n{out!r}"
        )

    def test_verbose_restores_build_agents_line(self, tmp_path, capsys):
        """``verbose=True`` must print the ``build-agents (claude-code): generated …`` line."""
        from research_vault.init import cmd_init_in_dir
        rc = cmd_init_in_dir(str(tmp_path / "myvault"), verbose=True)
        assert rc == 0
        out = capsys.readouterr().out
        assert "build-agents" in out, (
            f"verbose=True must print 'build-agents' line; got:\n{out!r}"
        )


# ---------------------------------------------------------------------------
# 3. --verbose flag in parser
# ---------------------------------------------------------------------------

class TestInitParserVerboseFlag:
    """The --verbose flag must exist in the init argument parser."""

    def test_verbose_flag_accepted(self):
        """``--verbose`` must be a recognised flag (no SystemExit)."""
        from research_vault.init import build_parser
        p = build_parser()
        args = p.parse_args(["--verbose"])
        assert args.verbose is True

    def test_verbose_defaults_to_false(self):
        """``verbose`` defaults to False when ``--verbose`` is not passed."""
        from research_vault.init import build_parser
        p = build_parser()
        args = p.parse_args([])
        assert args.verbose is False


# ---------------------------------------------------------------------------
# 4. Terse closing block always contains next-steps content
# ---------------------------------------------------------------------------

class TestInitTerseClosingBlock:
    """The terse default output must contain the key next-steps phrases."""

    def test_closing_has_rv_onboard(self, tmp_path, capsys):
        from research_vault.init import cmd_init_in_dir
        cmd_init_in_dir(str(tmp_path / "myvault"))
        out = capsys.readouterr().out
        assert "rv onboard" in out

    def test_closing_has_rv_start(self, tmp_path, capsys):
        from research_vault.init import cmd_init_in_dir
        cmd_init_in_dir(str(tmp_path / "myvault"))
        out = capsys.readouterr().out
        assert "rv start" in out

    def test_closing_has_cd(self, tmp_path, capsys):
        from research_vault.init import cmd_init_in_dir
        cmd_init_in_dir(str(tmp_path / "myvault"))
        out = capsys.readouterr().out
        assert "cd" in out

    def test_closing_has_quickstart(self, tmp_path, capsys):
        from research_vault.init import cmd_init_in_dir
        cmd_init_in_dir(str(tmp_path / "myvault"))
        out = capsys.readouterr().out
        assert "QUICKSTART.md" in out

    def test_closing_has_initialised_message(self, tmp_path, capsys):
        from research_vault.init import cmd_init_in_dir
        cmd_init_in_dir(str(tmp_path / "myvault"))
        out = capsys.readouterr().out
        assert "Research Vault instance initialised" in out


# ---------------------------------------------------------------------------
# 5 & 6. render_init emits 0 ANSI when NO_COLOR / RV_PLAIN
# ---------------------------------------------------------------------------

class TestRenderInitAnsiDegradation:
    """render_init must produce 0 ANSI codes under NO_COLOR and RV_PLAIN."""

    def test_no_ansi_when_no_color(self, tmp_path, monkeypatch, capsys):
        """Full cmd_init_in_dir output must contain 0 ANSI bytes when NO_COLOR is set."""
        monkeypatch.setenv("NO_COLOR", "1")
        from research_vault.init import cmd_init_in_dir
        rc = cmd_init_in_dir(str(tmp_path / "myvault"))
        assert rc == 0
        out = capsys.readouterr().out
        assert not _has_ansi(out), (
            f"0 ANSI expected when NO_COLOR=1; found ANSI in output:\n{out!r}"
        )

    def test_no_ansi_when_rv_plain(self, tmp_path, monkeypatch, capsys):
        """Full cmd_init_in_dir output must contain 0 ANSI bytes when RV_PLAIN is set."""
        monkeypatch.setenv("RV_PLAIN", "1")
        from research_vault.init import cmd_init_in_dir
        rc = cmd_init_in_dir(str(tmp_path / "vault2"))
        assert rc == 0
        out = capsys.readouterr().out
        assert not _has_ansi(out), (
            f"0 ANSI expected when RV_PLAIN=1; found ANSI in output:\n{out!r}"
        )

    def test_render_init_direct_no_ansi_no_color(self, monkeypatch, capsys):
        """``render_init`` called directly with NO_COLOR must emit 0 ANSI."""
        monkeypatch.setenv("NO_COLOR", "1")
        from research_vault.richui import render_init
        render_init({"target": "/tmp/myvault", "target_name": "myvault"})
        out = capsys.readouterr().out
        assert not _has_ansi(out), (
            f"render_init must emit 0 ANSI under NO_COLOR; got:\n{out!r}"
        )

    def test_render_init_direct_no_ansi_rv_plain(self, monkeypatch, capsys):
        """``render_init`` called directly with RV_PLAIN must emit 0 ANSI."""
        monkeypatch.setenv("RV_PLAIN", "1")
        from research_vault.richui import render_init
        render_init({"target": "/tmp/vault2", "target_name": "vault2"})
        out = capsys.readouterr().out
        assert not _has_ansi(out), (
            f"render_init must emit 0 ANSI under RV_PLAIN; got:\n{out!r}"
        )


# ---------------------------------------------------------------------------
# 7. Plain-path text key phrases (direct render_init unit test)
# ---------------------------------------------------------------------------

class TestRenderInitPlainContent:
    """render_init plain path (non-TTY) must contain the required phrases."""

    # Tests run under pytest which is non-TTY, so should_render_rich() → False.
    # No monkeypatching needed — the test runner itself is non-TTY.

    def test_plain_has_initialised(self, capsys):
        from research_vault.richui import render_init
        render_init({"target": "/abs/myvault", "target_name": "myvault"})
        out = capsys.readouterr().out
        assert "Research Vault instance initialised" in out

    def test_plain_has_crew_line(self, capsys):
        from research_vault.richui import render_init
        render_init({"target": "/abs/myvault", "target_name": "myvault"})
        out = capsys.readouterr().out
        assert ".claude/agents/" in out

    def test_plain_has_git_line(self, capsys):
        from research_vault.richui import render_init
        render_init({"target": "/abs/myvault", "target_name": "myvault"})
        out = capsys.readouterr().out
        assert "initial vault scaffold" in out

    def test_plain_has_rv_onboard(self, capsys):
        from research_vault.richui import render_init
        render_init({"target": "/abs/myvault", "target_name": "myvault"})
        out = capsys.readouterr().out
        assert "rv onboard" in out

    def test_plain_has_rv_start(self, capsys):
        from research_vault.richui import render_init
        render_init({"target": "/abs/myvault", "target_name": "myvault"})
        out = capsys.readouterr().out
        assert "rv start" in out

    def test_plain_has_cd_with_name(self, capsys):
        from research_vault.richui import render_init
        render_init({"target": "/abs/myvault", "target_name": "myvault"})
        out = capsys.readouterr().out
        assert "cd myvault" in out

    def test_plain_has_quickstart(self, capsys):
        from research_vault.richui import render_init
        render_init({"target": "/abs/myvault", "target_name": "myvault"})
        out = capsys.readouterr().out
        assert "QUICKSTART.md" in out

    def test_plain_has_rv_update(self, capsys):
        from research_vault.richui import render_init
        render_init({"target": "/abs/myvault", "target_name": "myvault"})
        out = capsys.readouterr().out
        assert "rv update" in out
