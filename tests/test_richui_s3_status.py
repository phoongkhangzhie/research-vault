"""test_richui_s3_status.py — S3: sectioned rich `rv status`.

status_sections() is the structured SSOT the rich renderer reads; the plain
cmd_status string is unchanged (its own tests hold that).  render_status must
render every section, not mutate the dict, and escape data-bearing brackets.
"""
from __future__ import annotations

import copy
import io
import re

from research_vault import richui

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _render(sections) -> str:
    buf = io.StringIO()
    con = richui.get_console(file=buf, force_terminal=True, width=160, no_color=True)
    richui.render_status(sections, console=con)
    return _ANSI.sub("", buf.getvalue())


def _full_sections() -> dict:
    return {
        "project": "demo-research",
        "instance_root": "/x/inst",
        "config_file": "/x/inst/research_vault.toml",
        "coordination": {
            "banner_ok": True,
            "sections": [
                {"name": "Inbox", "count": 2, "items": [
                    {"text": "[SR-9] review the plan", "resolved": False},
                    {"text": "ack the handshake", "resolved": True},
                ]},
                {"name": "Handshakes", "count": 0, "items": []},
            ],
        },
        "task_board": {"total": 3, "counts": {"in_progress": 1, "done": 2},
                       "active": ["sr-9 (mason)"]},
        "devlog": {"tail": "### 2026-07-05\n- did [a thing] with brackets"},
        "git": {"branches": ["feat/sr-9 abc123", "main def456"],
                "commits": ["abc123 feat: x", "def456 chore: y"]},
        "dag": {"runs": [{"run_id": "sr9-loop", "terminal": False},
                         {"run_id": "old-loop", "terminal": True}]},
        "pointers": {"path": "/x/src/pointers.md", "lines": ["design: /x/tasks/t.md"]},
        "attention": ["Inbox has 2 item(s) — act or acknowledge"],
    }


def test_render_status_all_sections_present():
    sections = _full_sections()
    before = copy.deepcopy(sections)
    text = _render(sections)
    assert sections == before, "render_status must not mutate the dict"
    # header + every section title
    assert "rv status" in text and "demo-research" in text
    assert "Coordination State" in text
    assert "Task Board" in text
    assert "DEVLOG" in text
    assert "Local Git State" in text
    assert "DAG Runs" in text
    assert "Pointers" in text
    assert "Needs Attention" in text


def test_render_status_content_and_bracket_escape():
    text = _render(_full_sections())
    # coordination items (with a [SR-9] bracket that must survive escaping)
    assert "SR-9" in text and "review the plan" in text
    # task board counts + active
    assert "in_progress" in text and "mason" in text
    # devlog bracketed text survives
    assert "a thing" in text and "brackets" in text
    # git branches + commits
    assert "feat/sr-9" in text and "chore: y" in text
    # dag run states
    assert "sr9-loop" in text and "in-flight" in text
    assert "old-loop" in text and "terminal" in text
    # pointers + attention
    assert "design:" in text
    assert "act or acknowledge" in text


def test_render_status_nothing_flagged():
    sections = _full_sections()
    sections["attention"] = []
    text = _render(sections)
    assert "nothing flagged" in text


def test_render_status_missing_control_and_errors():
    sections = _full_sections()
    sections["coordination"] = {"missing": True, "sections": [], "banner_ok": True}
    sections["git"] = {"error": "not a git repo"}
    text = _render(sections)
    assert "No control file" in text
    assert "not a git repo" in text


def test_status_sections_reuses_attention_ssot(monkeypatch):
    # status_sections must build attention via the SAME _build_attention the plain
    # cmd_status uses — proven by patching _build_attention and seeing it in both.
    import research_vault.status as st

    monkeypatch.setattr(st, "_build_attention", lambda project, cfg: ["SENTINEL flag"])

    class _Cfg:
        instance_root = "/x"
        config_file = None
        def project_control_file(self, p): raise Exception("skip")
        def project_devlog(self, p): raise Exception("skip")
        def project(self, p): raise KeyError(p)

    sections = st.status_sections("demo", config=_Cfg())
    assert sections["attention"] == ["SENTINEL flag"]
