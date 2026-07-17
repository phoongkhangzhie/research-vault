# SPDX-License-Identifier: AGPL-3.0-or-later
"""test_map.py — acceptance tests for `rv map <project>` (retrieval/map.py):
the mechanical, single-writer, additive, idempotent knowledge-map assembler
+ its orphan-coverage honesty gate.

Coverage:
  (a) happy path — complete map: concept index, MOC index, findings/gaps
      index, edge-type legend all present; literature NOT enumerated;
      map_complete: true.
  (b) orphan path — a concept referenced by a project note via a
      cross-bundle typed edge but organized under no project MOC ->
      a [MAP-GAP] line AND map_complete: false. Load-bearing (must go RED
      if the orphan check is ever removed/weakened).
  (c) empty/stub description -> a [MAP-GAP]-style WARN line naming the note.
  (d) idempotency — two generations from the same corpus are byte-identical.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import Config, reset_config_cache
from research_vault.retrieval.map import generate_map, write_map


@pytest.fixture(autouse=True)
def _reset_cfg():
    reset_config_cache()
    yield
    reset_config_cache()


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _cfg(tmp_path: Path) -> Config:
    proj_dir = tmp_path / "projects" / "demo-proj"
    proj_dir.mkdir(parents=True)
    raw = {
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(tmp_path / "state"),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "projects": {
            "demo-proj": {
                "code": "dp",
                "source_dir": str(proj_dir),
                "roster": ["engineer"],
            },
        },
    }
    return Config(raw)


def _concept_note(cfg: Config, slug: str, *, description: str = "A concept.") -> Path:
    p = cfg.concepts_root / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    desc_line = f'description: "{description}"\n' if description else "description:\n"
    p.write_text(
        f"---\ntype: concepts\ntitle: {slug}\n{desc_line}---\n\nBody.\n",
        encoding="utf-8",
    )
    return p


def _moc_note(cfg: Config, project: str, slug: str, *, organizes: list[str],
              description: str = "A MOC.") -> Path:
    p = cfg.project_notes_dir(project) / "mocs" / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    links = "\n".join(f"- [{c}](/concepts/{c}.md) — curated." for c in organizes)
    p.write_text(
        f'---\ntype: mocs\ntitle: {slug}\ndescription: "{description}"\n---\n\n{links}\n',
        encoding="utf-8",
    )
    return p


def _findings_note(cfg: Config, project: str, slug: str, *,
                    concept_edges: list[str] | None = None,
                    description: str = "A finding.") -> Path:
    p = cfg.project_notes_dir(project) / "findings" / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    concept_edges = concept_edges or []
    edge_lines = "\n".join(
        f"- [{c}](okf:concepts/{c}.md) — GROUNDED-IN: this finding grounds in {c}."
        for c in concept_edges
    )
    desc_line = f'description: "{description}"\n' if description else "description:\n"
    p.write_text(
        f"---\ntype: findings\ntitle: {slug}\n{desc_line}---\n\n"
        f"## Concept edges\n\n{edge_lines}\n",
        encoding="utf-8",
    )
    return p


def _gap_note(cfg: Config, project: str, slug: str, *, description: str = "A gap.") -> Path:
    p = cfg.project_notes_dir(project) / "gaps" / f"{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    desc_line = f'description: "{description}"\n' if description else "description:\n"
    p.write_text(
        f"---\ntype: gaps\ntitle: {slug}\n{desc_line}---\n\nBody.\n",
        encoding="utf-8",
    )
    return p


def _literature_note(cfg: Config, citekey: str) -> Path:
    p = cfg.literature_root / f"{citekey}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f'---\ntype: literature\ntitle: {citekey}\ndescription: "A paper."\n---\n\nBody.\n',
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# (a) happy path
# ---------------------------------------------------------------------------

def test_happy_path_complete_map(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _concept_note(cfg, "concept-a")
    _moc_note(cfg, "demo-proj", "moc1", organizes=["concept-a"])
    _findings_note(cfg, "demo-proj", "finding1", concept_edges=["concept-a"])
    _gap_note(cfg, "demo-proj", "gap1")
    _literature_note(cfg, "smith2024")

    m = generate_map(cfg, "demo-proj")

    assert m["map_complete"] is True
    assert m["orphan_concepts"] == []
    assert any(c["slug"] == "concept-a" for c in m["concept_index"])
    assert any(mo["slug"] == "moc1" for mo in m["moc_index"])
    assert any(f["slug"] == "finding1" for f in m["findings_gaps_index"])
    assert any(g["slug"] == "gap1" for g in m["findings_gaps_index"])
    assert m["edge_type_legend"]  # non-empty, sourced from _TAG_FAMILY
    assert "SUPPORTS" in m["edge_type_legend"]
    assert m["edge_type_legend"]["SUPPORTS"] == "argumentative"

    # literature is NOT enumerated in Tier-0
    rendered = m["rendered"]
    assert "smith2024" not in rendered
    assert "[MAP-GAP]" not in rendered


# ---------------------------------------------------------------------------
# (b) orphan path — load-bearing
# ---------------------------------------------------------------------------

def test_orphan_concept_flips_map_incomplete(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _concept_note(cfg, "concept-a")
    _concept_note(cfg, "concept-orphan")
    _moc_note(cfg, "demo-proj", "moc1", organizes=["concept-a"])
    # finding1 references BOTH concept-a (organized) and concept-orphan
    # (referenced, but under no project MOC).
    _findings_note(
        cfg, "demo-proj", "finding1",
        concept_edges=["concept-a", "concept-orphan"],
    )

    m = generate_map(cfg, "demo-proj")

    assert m["map_complete"] is False
    assert "concept-orphan" in m["orphan_concepts"]
    assert "concept-a" not in m["orphan_concepts"]
    assert "[MAP-GAP]" in m["rendered"]
    assert "concept-orphan" in m["rendered"]


def test_referenced_and_organized_concept_is_not_orphan(tmp_path: Path) -> None:
    """Positive control for the orphan gate's narrow end — a concept that IS
    organized under a project MOC must never be flagged."""
    cfg = _cfg(tmp_path)
    _concept_note(cfg, "concept-a")
    _moc_note(cfg, "demo-proj", "moc1", organizes=["concept-a"])
    _findings_note(cfg, "demo-proj", "finding1", concept_edges=["concept-a"])

    m = generate_map(cfg, "demo-proj")

    assert m["map_complete"] is True
    assert m["orphan_concepts"] == []


# ---------------------------------------------------------------------------
# (c) empty/stub description
# ---------------------------------------------------------------------------

def test_empty_description_surfaces_as_gap(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _concept_note(cfg, "concept-a", description="")
    _moc_note(cfg, "demo-proj", "moc1", organizes=["concept-a"])
    _findings_note(cfg, "demo-proj", "finding1", concept_edges=["concept-a"])

    m = generate_map(cfg, "demo-proj")

    assert any("concept-a.md" in w for w in m["description_gaps"])
    assert "[MAP-GAP]" in m["rendered"]
    assert "concept-a.md" in m["rendered"]


def test_present_description_does_not_surface_gap(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _concept_note(cfg, "concept-a", description="A real one-liner.")
    _moc_note(cfg, "demo-proj", "moc1", organizes=["concept-a"])

    m = generate_map(cfg, "demo-proj")

    assert m["description_gaps"] == []


# ---------------------------------------------------------------------------
# (d) idempotency
# ---------------------------------------------------------------------------

def test_idempotent_regeneration(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    _concept_note(cfg, "concept-a")
    _moc_note(cfg, "demo-proj", "moc1", organizes=["concept-a"])
    _findings_note(cfg, "demo-proj", "finding1", concept_edges=["concept-a"])
    _gap_note(cfg, "demo-proj", "gap1")

    out1 = write_map(cfg, "demo-proj")
    text1 = out1.read_text(encoding="utf-8")
    out2 = write_map(cfg, "demo-proj")
    text2 = out2.read_text(encoding="utf-8")

    assert out1 == out2
    assert text1 == text2


def test_write_map_writes_frontmatter_scalar(tmp_path: Path) -> None:
    from research_vault.note import _parse_frontmatter

    cfg = _cfg(tmp_path)
    _concept_note(cfg, "concept-a")
    _moc_note(cfg, "demo-proj", "moc1", organizes=["concept-a"])

    out = write_map(cfg, "demo-proj")
    fields, _body = _parse_frontmatter(out.read_text(encoding="utf-8"))
    assert str(fields.get("map_complete")).strip().lower() == "true"
    assert fields.get("type") == "knowledge-map"
