"""test_richui_s4_bootstrap.py — S4: rich `rv bootstrap`.

render_bootstrap reads the _run_bootstrap result dict; the plain `report` is
unchanged. Header + per-tier lines + Result panel (mirrors render_check).
"""
from __future__ import annotations

import copy
import io
import re

from research_vault import richui

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _render(result) -> str:
    buf = io.StringIO()
    con = richui.get_console(file=buf, force_terminal=True, width=160, no_color=True)
    richui.render_bootstrap(result, console=con)
    return _ANSI.sub("", buf.getvalue())


def _ok_result(**over) -> dict:
    base = {
        "tier1_ok": True, "tier2_ok": True, "serve_ok": False,
        "tier2_reason": "", "serve_reason": "",
        "tier2_attempted": True, "serve_target": None,
        "venv_dir": "/x/.venv", "report": "PLAIN REPORT",
    }
    base.update(over)
    return base


def test_render_bootstrap_all_ok():
    result = _ok_result()
    before = copy.deepcopy(result)
    text = _render(result)
    assert result == before, "render must not mutate the result dict"
    assert "rv bootstrap" in text
    assert "Tier-1" in text and "OK" in text
    assert "Tier-2" in text and "installed" in text
    assert "Result: OK" in text
    assert "/x/.venv" in text
    assert "rv check" in text


def test_render_bootstrap_tier2_skipped_with_reason():
    result = _ok_result(tier2_ok=False, tier2_reason="no CUDA toolkit found")
    text = _render(result)
    assert "WARN" in text
    assert "no CUDA toolkit found" in text
    # The [local] extra must survive rich markup (escaped) and be shell-quoted
    # (zsh treats bare [local] as a glob) — not swallowed as a markup tag.
    assert "pip install 'research-vault[local]'" in text
    # Result still OK (Tier-1 is the only hard requirement)
    assert "Result: OK" in text
    assert "skipped" in text


def test_render_bootstrap_no_tier2():
    result = _ok_result(tier2_ok=False, tier2_attempted=False)
    text = _render(result)
    assert "--no-tier2" in text
    assert "Result: OK" in text


def test_render_bootstrap_serve_installed():
    result = _ok_result(serve_ok=True, serve_target="vllm")
    text = _render(result)
    assert "Serve" in text and "vllm" in text and "installed" in text


def test_render_bootstrap_tier1_fail():
    result = _ok_result(
        tier1_ok=False, tier2_ok=False,
        report="Tier-1 install: research-vault\n  [FAIL]\n  stderr: network unreachable\n",
    )
    text = _render(result)
    assert "Tier-1" in text and "FAIL" in text
    assert "Result: FAIL" in text
    # the error tail is surfaced
    assert "network unreachable" in text


def test_bootstrap_report_key_unchanged_additive():
    # The additive keys must not disturb the plain report contract.
    result = _ok_result()
    assert result["report"] == "PLAIN REPORT"
    assert "tier2_attempted" in result and "serve_target" in result
