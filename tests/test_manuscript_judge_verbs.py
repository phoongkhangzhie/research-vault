"""tests/test_manuscript_judge_verbs.py — NG-4 CLI surface:
``rv manuscript <project> judge-emit <slug>`` /
``rv manuscript <project> judge-ingest <slug>`` — parser wiring + the
cmd_judge_emit/cmd_judge_ingest functions end to end against a scaffolded
manuscript folder.

sr: NG-4
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Parser wiring
# ---------------------------------------------------------------------------

def test_verbs_judge_emit_parses():
    from research_vault.manuscript.verbs import build_parser

    p = build_parser()
    args = p.parse_args(["demo-research", "judge-emit", "survey-x"])
    assert args.manuscript_cmd == "judge-emit"
    assert args.slug == "survey-x"
    assert args.gate == "support-matcher"


def test_verbs_judge_emit_gate_flag_only_accepts_support_matcher():
    """The cold-read gate was removed (PR #180 scope addition) — --gate is
    now support-matcher-only; any other value is rejected by argparse."""
    from research_vault.manuscript.verbs import build_parser

    p = build_parser()
    args = p.parse_args(["demo-research", "judge-emit", "survey-x", "--gate", "support-matcher"])
    assert args.gate == "support-matcher"
    with pytest.raises(SystemExit):
        p.parse_args(["demo-research", "judge-emit", "survey-x", "--gate", "cold-read"])


def test_verbs_judge_ingest_parses():
    from research_vault.manuscript.verbs import build_parser

    p = build_parser()
    args = p.parse_args(["demo-research", "judge-ingest", "survey-x"])
    assert args.manuscript_cmd == "judge-ingest"


# ---------------------------------------------------------------------------
# cmd_judge_emit / cmd_judge_ingest end to end
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_path):
    from research_vault.config import Config, _default_config, _merge, _expand_paths

    raw = {
        "instance_root": str(tmp_path),
        "projects": {
            "demo-research": {"source_dir": str(tmp_path / "projects" / "demo-research")},
        },
    }
    merged = _merge(_default_config(), raw)
    merged = _expand_paths(merged, tmp_path)
    return Config(merged, config_file=None)


def _scaffold(cfg, tmp_path):
    from research_vault.manuscript import cmd_new

    cmd_new("demo-research", "survey-x", ms_type_key="lit-review", config=cfg)


def test_judge_emit_then_ingest_roundtrip(cfg, tmp_path):
    from research_vault.manuscript import cmd_judge_emit, cmd_judge_ingest, _manuscript_tree_root
    from research_vault.gates.judge_seam import write_json, read_json_or_none

    _scaffold(cfg, tmp_path)
    tree_root = _manuscript_tree_root("demo-research", "survey-x", cfg)
    (tree_root / "sections").mkdir(parents=True, exist_ok=True)
    (tree_root / "sections" / "intro.md").write_text(
        "A claim about the topic. [[paper1]]", encoding="utf-8",
    )
    lit_dir = cfg.project_notes_dir("demo-research") / "literature"
    lit_dir.mkdir(parents=True, exist_ok=True)
    (lit_dir / "paper1.md").write_text(
        "---\ntype: literature\n---\n## Result\nThe topic shows strong results.\n",
        encoding="utf-8",
    )

    emitted = cmd_judge_emit("demo-research", "survey-x", config=cfg, gate="support-matcher")
    assert "support-matcher" in emitted
    tasks_path = tree_root / "judge" / "support-matcher" / "_judge-tasks.json"
    canary_key_path = tree_root / "judge" / "support-matcher" / "_judge-canary-key.json"
    assert tasks_path.exists()
    assert canary_key_path.exists()

    tasks_doc = read_json_or_none(tasks_path)
    canary_key_doc = read_json_or_none(canary_key_path)
    verdicts = []
    for t in tasks_doc["tasks"]:
        expected = canary_key_doc["canaries"].get(t["id"])
        verdicts.append({"id": t["id"], "verdict": expected or "SUPPORTS"})
    write_json(
        tree_root / "judge" / "support-matcher" / "_judge-verdicts.json",
        {"verdicts": verdicts},
    )

    ingested = cmd_judge_ingest("demo-research", "survey-x", config=cfg, gate="support-matcher")
    assert ingested["support-matcher"]["halt"] is False
    assert ingested["support-matcher"]["canary_aborted"] is False


def test_judge_emit_missing_manuscript_raises(cfg, tmp_path):
    from research_vault.manuscript import cmd_judge_emit

    with pytest.raises(FileNotFoundError):
        cmd_judge_emit("demo-research", "does-not-exist", config=cfg)


def test_run_judge_emit_and_ingest_cli_wrappers(cfg, tmp_path, capsys):
    from research_vault.manuscript.verbs import _run_judge_emit, _run_judge_ingest
    import argparse

    _scaffold(cfg, tmp_path)

    ns = argparse.Namespace(project="demo-research", slug="survey-x", gate="support-matcher")

    import research_vault.manuscript.verbs as _verbs_mod

    orig_load_config = None
    import research_vault.config as _cfg_mod

    def _fake_load_config():
        return cfg

    monkeypatch_target = _cfg_mod.load_config
    _cfg_mod.load_config = _fake_load_config
    try:
        rc = _run_judge_emit(ns)
        assert rc == 0
        out = capsys.readouterr().out
        assert "judge-emit" in out

        rc2 = _run_judge_ingest(ns)
        # No verdicts file written yet -> halt for gates with real tasks,
        # or a clean no-op for gates with zero tasks. Either way the
        # wrapper must not crash.
        assert rc2 in (0, 1)
    finally:
        _cfg_mod.load_config = monkeypatch_target
