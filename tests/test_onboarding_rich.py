"""test_onboarding_rich.py — S3: rich structure, additive, degrades to plain.

The rich renderer reads the SAME result dict; non-TTY / NO_COLOR / RV_PLAIN /
--plain degrade to the plain report so the dict + plain-text contracts hold.
"""
from __future__ import annotations

import argparse
import io
import os
from unittest.mock import patch

import pytest


def _env_no_keys() -> dict[str, str]:
    drop = {
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "S2_API_KEY", "WANDB_API_KEY", "ZOTERO_KEY",
    }
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env["VAULT_SKIP_KEYRING"] = "1"
    return env


# ---------------------------------------------------------------------------
# Console detection
# ---------------------------------------------------------------------------

def test_no_color_forces_plain(monkeypatch):
    from research_vault.richui import should_render_rich
    monkeypatch.setenv("NO_COLOR", "1")

    class _TTY:
        def isatty(self):
            return True
    assert should_render_rich(_TTY()) is False


def test_rv_plain_forces_plain(monkeypatch):
    from research_vault.richui import should_render_rich
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("RV_PLAIN", "1")

    class _TTY:
        def isatty(self):
            return True
    assert should_render_rich(_TTY()) is False


def test_non_tty_is_plain(monkeypatch):
    from research_vault.richui import should_render_rich
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("RV_PLAIN", raising=False)

    class _Pipe:
        def isatty(self):
            return False
    assert should_render_rich(_Pipe()) is False


def test_tty_renders_rich(monkeypatch):
    from research_vault.richui import should_render_rich
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("RV_PLAIN", raising=False)

    class _TTY:
        def isatty(self):
            return True
    assert should_render_rich(_TTY()) is True


# ---------------------------------------------------------------------------
# run() falls back to plain in a non-TTY (capsys is not a TTY)
# ---------------------------------------------------------------------------

def test_check_run_prints_plain_report_when_not_tty(capsys):
    from research_vault.check import run
    with patch.dict(os.environ, _env_no_keys(), clear=True):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            rc = run(argparse.Namespace(require_observability=False, plain=False))
    out = capsys.readouterr().out
    assert rc == 0
    # Plain report markers present (rich structure would not emit these headers).
    assert "rv check" in out
    assert "Result: OK" in out


# ---------------------------------------------------------------------------
# render_check produces the structural tables (rendered to a string console)
# ---------------------------------------------------------------------------

def test_render_check_emits_tables_and_result_panel():
    from research_vault.check import run_preflight
    from research_vault.richui import render_check, get_console
    with patch.dict(os.environ, _env_no_keys(), clear=True):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            result = run_preflight()

    buf = io.StringIO()
    console = get_console(file=buf, force_terminal=True, width=140, no_color=True)
    render_check(result, console=console)
    text = buf.getvalue()

    # Integrations table columns (Capability | Unlocks | Class | Status)
    assert "Capability" in text and "Unlocks" in text and "Class" in text and "Status" in text
    # Tier matrix present
    assert "Toolkit tiers" in text
    # Result panel
    assert "Result" in text and "OK" in text
    # Every feature title rendered (no silent drop)
    for feat in result["features"]:
        # Rich may wrap; check a distinctive token of each title.
        token = feat["title"].split()[0]
        assert token in text, f"{feat['id']} title token {token!r} missing from render"


def test_render_check_fail_panel_names_culprit():
    from research_vault.check import run_preflight
    from research_vault.richui import render_check, get_console
    with patch.dict(os.environ, _env_no_keys(), clear=True):
        with patch("shutil.which", return_value=None):
            result = run_preflight()
    buf = io.StringIO()
    console = get_console(file=buf, force_terminal=True, width=140, no_color=True)
    render_check(result, console=console)
    text = buf.getvalue()
    assert "FAIL" in text
    assert "runtime" in text.lower()


def test_render_does_not_mutate_result():
    from research_vault.check import run_preflight
    from research_vault.richui import render_check, get_console
    import copy
    with patch.dict(os.environ, _env_no_keys(), clear=True):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            result = run_preflight()
    before = copy.deepcopy({k: result[k] for k in result if k != "report"})
    buf = io.StringIO()
    render_check(result, console=get_console(file=buf, force_terminal=True, width=140))
    after = {k: result[k] for k in result if k != "report"}
    assert before == after, "render_check must not mutate the result dict"
