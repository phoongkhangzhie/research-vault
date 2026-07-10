"""test_research_sweep_cmd.py — `rv research sweep` CLI verb (NG-3).

Hermetic: adapters are mocked at the sweep._fetch_cell seam; no network I/O.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault import research as research_mod
from research_vault.sources.base import PaperHit
from research_vault.sources.sweep import SweepCell

PROTOCOL_TEXT = """---
type: review-protocol
question: "Does X improve Y?"
seed_queries:
  by-method:     "transformer attention"
  by-outcome:    "translation quality"
sources: [semantic-scholar, arxiv]
---
"""


def _hit(title, source, external_ids=None) -> PaperHit:
    return PaperHit(
        title=title, year=2020, authors=["A"], external_ids=external_ids or {},
        abstract="", citation_count=5, source=source,
    )


def test_cmd_sweep_end_to_end(tmp_path: Path, monkeypatch, capsys) -> None:
    protocol_path = tmp_path / "_protocol.md"
    protocol_path.write_text(PROTOCOL_TEXT, encoding="utf-8")

    from research_vault.sources import sweep as sweep_mod

    def fake_fetch_cell(angle, query, source, *, limit, **_ignored):
        return SweepCell(
            angle=angle, query=query, source=source,
            hits=[_hit(f"Paper for {angle}", source, {"doi": f"10.1/{angle}"})],
        )

    monkeypatch.setattr(sweep_mod, "_fetch_cell", fake_fetch_cell)

    args = argparse.Namespace(
        protocol=str(protocol_path), project=None, budget=None, per_cell_limit=20,
    )
    rc = research_mod.cmd_sweep(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Width-sweep" in out
    assert "[NEW]" in out


def test_cmd_sweep_missing_protocol_fails_gracefully(tmp_path: Path, capsys) -> None:
    args = argparse.Namespace(
        protocol=str(tmp_path / "nope.md"), project=None, budget=None, per_cell_limit=20,
    )
    rc = research_mod.cmd_sweep(args)
    assert rc == 1


def test_cmd_sweep_no_angle_matrix_fails_gracefully(tmp_path: Path) -> None:
    protocol_path = tmp_path / "_protocol.md"
    protocol_path.write_text("---\ntype: review-protocol\n---\n", encoding="utf-8")
    args = argparse.Namespace(
        protocol=str(protocol_path), project=None, budget=None, per_cell_limit=20,
    )
    rc = research_mod.cmd_sweep(args)
    assert rc == 1


def test_cmd_sweep_annotates_with_project(tmp_path: Path, monkeypatch, capsys) -> None:
    protocol_path = tmp_path / "_protocol.md"
    protocol_path.write_text(PROTOCOL_TEXT, encoding="utf-8")

    project_notes_dir = tmp_path / "notes" / "my-proj"
    lit_dir = project_notes_dir / "literature"
    lit_dir.mkdir(parents=True)
    (lit_dir / "existing-paper.md").write_text(
        "---\ntype: literature\ntitle: existing\ndoi: 10.1/by-method\n---\n\n# x\n",
        encoding="utf-8",
    )

    cfg_path = tmp_path / "research_vault.toml"
    cfg_path.write_text(
        '[projects.my-proj]\n'
        f'source_dir = "{project_notes_dir}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_path))
    from research_vault.config import reset_config_cache
    reset_config_cache()

    from research_vault.sources import sweep as sweep_mod

    def fake_fetch_cell(angle, query, source, *, limit, **_ignored):
        return SweepCell(
            angle=angle, query=query, source=source,
            hits=[_hit(f"Paper for {angle}", source, {"doi": f"10.1/{angle}"})],
        )

    monkeypatch.setattr(sweep_mod, "_fetch_cell", fake_fetch_cell)

    args = argparse.Namespace(
        protocol=str(protocol_path), project="my-proj", budget=None, per_cell_limit=20,
    )
    rc = research_mod.cmd_sweep(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "[IN-CORPUS:existing-paper]" in out
    assert "[NEW]" in out
