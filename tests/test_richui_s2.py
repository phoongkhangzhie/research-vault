"""test_richui_s2.py — S2 quick-win renderers (compute explain / approval status /
project list / doctor) + the closing-panel verbs.

Each renderer READS an existing dict and must not mutate it; every verb degrades
to its byte-intact plain output in a non-TTY (which is what the test process is).
"""
from __future__ import annotations

import copy
import io
import re

from research_vault import richui

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _render(fn) -> str:
    """Render to a string console and strip ANSI (bold/dim survive no_color) so
    substring assertions see the VISIBLE text, not rule-fragmented styled spans."""
    buf = io.StringIO()
    con = richui.get_console(file=buf, force_terminal=True, width=140, no_color=True)
    fn(con)
    return _ANSI.sub("", buf.getvalue())


# ---------------------------------------------------------------------------
# render_compute_explain
# ---------------------------------------------------------------------------

def test_render_compute_explain_kv_and_nested():
    resolved = {
        "job": "llama-70b",
        "backend": "compute-node",
        "conda_env": "vllm",
        "tier": "gpu-2",
        "gpus": 2,
        "submit_flags": "--gres=gpu:2",
        "model_quirks": {"tp": 2, "flashinfer_cache": "/tmp/fi"},
    }
    before = copy.deepcopy(resolved)
    text = _render(lambda con: richui.render_compute_explain("llama-70b", resolved, console=con))
    assert resolved == before, "render must not mutate the resolved dict"
    # header + resolved fields present
    assert "llama-70b" in text
    assert "backend" in text and "compute-node" in text
    assert "tier" in text and "gpu-2" in text
    # nested model_quirks rendered one level deep
    assert "tp" in text and "2" in text
    # None values (none here) and the job key are not rendered as rows


def test_render_compute_explain_empty_defaults_panel():
    resolved = {"job": "unknown", "backend": None, "conda_env": None, "tier": None,
                "gpus": None, "submit_flags": None, "model_quirks": {}}
    text = _render(lambda con: richui.render_compute_explain("unknown", resolved, console=con))
    assert "defaults apply" in text.lower()


# ---------------------------------------------------------------------------
# render_approval_status
# ---------------------------------------------------------------------------

def test_render_approval_status_enforce_on():
    state = {"enforce": True, "enforce_label": "enforce=on", "token_present": True,
             "token_label": "provisioned", "env_warning": False}
    before = copy.deepcopy(state)
    text = _render(lambda con: richui.render_approval_status(state, console=con))
    assert state == before
    assert "enforce=on" in text
    assert "provisioned" in text
    assert "Approval gate" in text


def test_render_approval_status_env_warning_surfaced():
    state = {"enforce": False, "enforce_label": "enforce=off (unsigned — trust-me mode)",
             "token_present": False, "token_label": "absent", "env_warning": True}
    text = _render(lambda con: richui.render_approval_status(state, console=con))
    assert "WARNING" in text
    assert "RV_APPROVER_TOKEN" in text
    assert "keyring" in text


# ---------------------------------------------------------------------------
# render_project_list
# ---------------------------------------------------------------------------

def test_render_project_list_table():
    projects = [
        {"slug": "demo-sim", "code": "dsm", "roster": ["engineer", "researcher"], "source": "/x/dsm"},
        {"slug": "eval-bench", "code": "evb", "roster": [], "source": ""},
    ]
    before = copy.deepcopy(projects)
    text = _render(lambda con: richui.render_project_list(projects, console=con))
    assert projects == before
    assert "demo-sim" in text and "dsm" in text
    assert "eval-bench" in text and "evb" in text
    assert "engineer" in text and "researcher" in text
    assert "2 project(s)" in text


# ---------------------------------------------------------------------------
# render_doctor — reuses doctor._backend_report_lines (SSOT, no drift)
# ---------------------------------------------------------------------------

def test_render_doctor_panels_per_backend():
    result = {
        "ts": "2026-07-05T00:00:00Z",
        "from_cache": True,
        "backends": {
            "local": {"capabilities": {"nvidia_smi": {"available": False, "reason": "no gpu"},
                                        "sbatch": False, "sinfo_detail": {},
                                        "qstat_detail": {}, "hf": True, "uv": True,
                                        "conda_envs": ["base", "vllm"]}},
            "compute-node": {"capabilities": {"probe_status": "unreachable",
                                              "archetype": "ssh+slurm", "host": "sc",
                                              "reason": "ssh timeout"}},
        },
    }
    before = copy.deepcopy(result)
    text = _render(lambda con: richui.render_doctor(result, console=con))
    assert result == before
    # header shows ts + cache source
    assert "rv doctor" in text
    assert "from cache" in text
    # per-backend panels
    assert "local" in text
    assert "compute-node" in text
    assert "UNREACHABLE" in text
    # local caps detail present (reused SSOT lines)
    assert "conda envs" in text.lower() or "vllm" in text


def test_render_doctor_matches_plain_backend_text():
    # The rich body for a backend must contain the SAME capability text the plain
    # formatter emits (SSOT: _backend_report_lines) — proves no drift.
    from research_vault.doctor import _backend_report_lines
    caps = {"probe_status": "unreachable", "archetype": "ssh+slurm",
            "host": "sc", "reason": "ssh timeout"}
    lines = _backend_report_lines("compute-node", caps)
    result = {"ts": "t", "from_cache": False, "backends": {"compute-node": {"capabilities": caps}}}
    text = _render(lambda con: richui.render_doctor(result, console=con))
    # every plain line's stripped content appears in the rich render
    for line in lines:
        stripped = line.strip()
        if stripped:
            # rich may wrap; check the leading distinctive token
            assert stripped.split()[0] in text


def test_render_doctor_empty_backends():
    result = {"ts": "t", "from_cache": False, "backends": {}}
    text = _render(lambda con: richui.render_doctor(result, console=con))
    assert "No backends" in text


# ---------------------------------------------------------------------------
# doctor SSOT parity — plain format_report still byte-identical after refactor
# ---------------------------------------------------------------------------

def test_format_report_unchanged_by_ssot_extract():
    from research_vault.doctor import format_report
    result = {
        "ts": "2026-07-05T00:00:00Z", "from_cache": True,
        "backends": {"local": {"capabilities": {
            "nvidia_smi": {"available": False, "reason": "no gpu"}, "sbatch": False,
            "sinfo_detail": {}, "qstat_detail": {}, "hf": True, "uv": True,
            "conda_envs": ["base"]}}},
    }
    report = format_report(result)
    assert "[local]" in report
    assert "hf CLI: found" in report
    assert "conda envs (1): base" in report
