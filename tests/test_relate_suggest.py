"""test_relate_suggest.py — Slice 8 tests for rv project relate --suggest (SR-XPB).

Acceptance criteria:
  - --suggest surfaces ranked undeclared project pairs with scores + rationale hint.
  - --suggest never auto-declares (the edge store is unchanged after the call).
  - Already-declared pairs are excluded from suggestions.
  - Deterministic: same corpora → same ranking order.
  - Empty/single-project → graceful no-op.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from research_vault.config import Config, reset_config_cache
from research_vault.project_edges import add_edge, load_edges
from research_vault.project import cmd_relate_suggest


@pytest.fixture(autouse=True)
def _reset_cfg():
    reset_config_cache()
    yield
    reset_config_cache()


@pytest.fixture
def three_cfg(tmp_path: Path) -> Config:
    state = tmp_path / "state"
    state.mkdir()
    for slug in ("proj-nlp", "proj-cv", "proj-econ"):
        (tmp_path / slug).mkdir()
    raw = {
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(state),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "adapters": {"notifier": "file", "backend": "local", "secrets": "env"},
        "projects": {
            "proj-nlp": {"code": "nlp", "source_dir": str(tmp_path / "proj-nlp"), "roster": []},
            "proj-cv": {"code": "cv1", "source_dir": str(tmp_path / "proj-cv"), "roster": []},
            "proj-econ": {"code": "eco", "source_dir": str(tmp_path / "proj-econ"), "roster": []},
        },
    }
    return Config(raw)


def _plant_corpus(source_dir: Path, content: str) -> None:
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "findings.md").write_text(content, encoding="utf-8")


def test_suggest_surfaces_pairs(three_cfg: Config, tmp_path: Path, capsys, monkeypatch) -> None:
    """--suggest prints candidate pairs with similarity scores."""
    import research_vault.config as _cfg_mod
    monkeypatch.setattr(_cfg_mod, "_CACHE", three_cfg)

    # Plant similar corpora in nlp and cv; econ is unrelated
    _plant_corpus(
        tmp_path / "proj-nlp",
        "Transformer models use attention mechanisms for language understanding. "
        "BERT and GPT are large language models trained on text corpora.",
    )
    _plant_corpus(
        tmp_path / "proj-cv",
        "Vision transformers apply attention mechanisms to image patches. "
        "ViT and DeiT are large models trained on image classification tasks.",
    )
    _plant_corpus(
        tmp_path / "proj-econ",
        "GDP growth correlates with unemployment rates in OECD countries. "
        "Inflation and interest rate policy affect consumer spending.",
    )

    ret = cmd_relate_suggest(top_k=3)
    assert ret == 0
    out = capsys.readouterr().out
    # Output must include suggestions
    assert "↔" in out or "similarity" in out.lower(), f"Expected pair suggestions. Got: {out!r}"
    # Output must include declare instructions
    assert "rv project relate" in out


def test_suggest_never_declares(three_cfg: Config, tmp_path: Path, monkeypatch) -> None:
    """--suggest does not modify the edge store."""
    import research_vault.config as _cfg_mod
    monkeypatch.setattr(_cfg_mod, "_CACHE", three_cfg)

    _plant_corpus(tmp_path / "proj-nlp", "attention transformers language models")
    _plant_corpus(tmp_path / "proj-cv", "attention vision transformers image")
    _plant_corpus(tmp_path / "proj-econ", "gdp inflation monetary policy")

    cmd_relate_suggest(top_k=5)
    edges = load_edges(three_cfg)
    assert edges == [], f"--suggest must not declare any edges. Got: {edges}"


def test_suggest_excludes_already_declared(three_cfg: Config, tmp_path: Path, monkeypatch) -> None:
    """Already-declared pairs do not appear in --suggest output."""
    import research_vault.config as _cfg_mod
    monkeypatch.setattr(_cfg_mod, "_CACHE", three_cfg)

    _plant_corpus(tmp_path / "proj-nlp", "attention transformers language models bert gpt")
    _plant_corpus(tmp_path / "proj-cv", "attention vision transformers image classification")
    _plant_corpus(tmp_path / "proj-econ", "gdp inflation monetary policy unemployment")

    # Declare the nlp↔cv edge upfront
    add_edge(three_cfg, "proj-nlp", "proj-cv", "shared-attention-mechanism")

    import io, sys as _sys
    buf = io.StringIO()
    old_stdout = _sys.stdout
    _sys.stdout = buf
    try:
        cmd_relate_suggest(top_k=5)
    finally:
        _sys.stdout = old_stdout
    out = buf.getvalue()

    # The already-declared pair (nlp↔cv) should NOT appear as a suggestion pair
    # Check neither "proj-nlp ↔ proj-cv" nor "proj-cv ↔ proj-nlp" appear
    assert "proj-nlp ↔ proj-cv" not in out and "proj-cv ↔ proj-nlp" not in out, (
        f"Already-declared pair must be excluded from suggestions. Got output: {out!r}"
    )


def test_suggest_fewer_than_two_projects(tmp_path: Path, monkeypatch) -> None:
    """--suggest prints a message and returns 0 when fewer than 2 projects exist."""
    import research_vault.config as _cfg_mod
    state = tmp_path / "state"
    state.mkdir()
    (tmp_path / "proj-only").mkdir()
    raw = {
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(state),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "adapters": {"notifier": "file", "backend": "local", "secrets": "env"},
        "projects": {
            "proj-only": {"code": "po1", "source_dir": str(tmp_path / "proj-only"), "roster": []},
        },
    }
    cfg = Config(raw)
    monkeypatch.setattr(_cfg_mod, "_CACHE", cfg)
    ret = cmd_relate_suggest()
    assert ret == 0
