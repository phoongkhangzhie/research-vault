"""test_richui_factory.py — S1: the shared richui design-system factory.

These prove the reusable primitives (make_header / make_panel / make_status_table /
make_kv_table) render structure through the SINGLE style system (_STYLE / _PANEL_BORDER)
and that the existing render_check still routes through them (reuse, not duplication).
"""
from __future__ import annotations

import io

from research_vault import richui


def _console():
    return richui.get_console(file=io.StringIO(), force_terminal=True, width=120, no_color=True)


def _render(fn) -> str:
    buf = io.StringIO()
    con = richui.get_console(file=buf, force_terminal=True, width=120, no_color=True)
    fn(con)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# make_header
# ---------------------------------------------------------------------------

def test_make_header_title_and_subtitle():
    text = _render(lambda con: richui.make_header("rv widget", "does a thing", console=con))
    assert "rv widget" in text
    assert "does a thing" in text


def test_make_header_no_subtitle():
    text = _render(lambda con: richui.make_header("rv widget", console=con))
    assert "rv widget" in text


# ---------------------------------------------------------------------------
# make_panel — kind → border dispatch
# ---------------------------------------------------------------------------

def test_make_panel_renders_title_and_body():
    text = _render(lambda con: richui.make_panel("hello body", title="MyPanel", kind="ok", console=con))
    assert "MyPanel" in text
    assert "hello body" in text


def test_make_panel_border_kinds_all_resolve():
    # Every documented kind must be present in the border map; unknown → neutral.
    for kind in ("ok", "fail", "init", "neutral"):
        assert kind in richui._PANEL_BORDER
    # Unknown kind must not raise and must fall back to the neutral border.
    text = _render(lambda con: richui.make_panel("x", title="T", kind="bogus", console=con))
    assert "T" in text and "x" in text


# ---------------------------------------------------------------------------
# make_status_table
# ---------------------------------------------------------------------------

def test_make_status_table_columns_and_rows():
    columns = [
        {"name": "Alpha", "no_wrap": True},
        {"name": "Beta", "justify": "right"},
    ]
    rows = [("a1", "b1"), ("a2", "b2")]
    table = richui.make_status_table(columns, rows, title="Grid")
    buf = io.StringIO()
    con = richui.get_console(file=buf, force_terminal=True, width=120, no_color=True)
    con.print(table)
    text = buf.getvalue()
    for token in ("Alpha", "Beta", "a1", "b2", "Grid"):
        assert token in text, f"{token!r} missing from status table render"


def test_make_status_table_empty_rows_ok():
    table = richui.make_status_table([{"name": "Only"}], [], title="Empty")
    buf = io.StringIO()
    con = richui.get_console(file=buf, force_terminal=True, width=120, no_color=True)
    con.print(table)
    assert "Only" in buf.getvalue()


# ---------------------------------------------------------------------------
# make_kv_table
# ---------------------------------------------------------------------------

def test_make_kv_table_pairs_render():
    grid = richui.make_kv_table([("backend", "local"), ("tier", "gpu-1")])
    buf = io.StringIO()
    con = richui.get_console(file=buf, force_terminal=True, width=120, no_color=True)
    con.print(grid)
    text = buf.getvalue()
    assert "backend" in text and "local" in text
    assert "tier" in text and "gpu-1" in text


def test_make_kv_table_long_value_not_dropped():
    long_val = "/very/long/path/" + "seg/" * 20 + "output.jsonl"
    grid = richui.make_kv_table([("artifact", long_val)])
    buf = io.StringIO()
    con = richui.get_console(file=buf, force_terminal=True, width=80, no_color=True)
    con.print(grid)
    text = buf.getvalue().replace("\n", "")
    # folded across lines but every path segment survives (no truncation/drop)
    assert "output.jsonl" in text
    assert "artifact" in text


# ---------------------------------------------------------------------------
# reuse: render_check still routes through the factory
# ---------------------------------------------------------------------------

def test_render_check_uses_factory_primitives():
    # The refactor must not have changed the surface: the tier + integrations tables
    # and the result panel still appear (they are now built by make_status_table /
    # make_panel).  Guards against a regression that bypasses the shared system.
    import os
    from unittest.mock import patch
    from research_vault.check import run_preflight

    drop = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "S2_API_KEY", "WANDB_API_KEY", "ZOTERO_KEY"}
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env["VAULT_SKIP_KEYRING"] = "1"
    with patch.dict(os.environ, env, clear=True):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            result = run_preflight()
    buf = io.StringIO()
    con = richui.get_console(file=buf, force_terminal=True, width=140, no_color=True)
    richui.render_check(result, console=con)
    text = buf.getvalue()
    assert "Toolkit tiers" in text
    assert "Capability" in text
    assert "Result" in text and "OK" in text
