"""test_sources_sweep.py — NG-3 frozen-protocol parsing + parallel width-sweep orchestration.

★ Anti-fishing constraint under test: the sweep orchestrator only READS the
frozen angle matrix + sources from `_protocol.md` — it has no mutation path,
so there is no way for a running sweep to widen its own seeds mid-run.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.sources.base import PaperHit
from research_vault.sources.sweep import (
    DEFAULT_FETCH_BUDGET,
    SweepCell,
    compose_sweep_result,
    parse_angle_matrix,
    parse_sources,
    run_sweep_from_protocol,
    run_width_sweep,
)

PROTOCOL_TEXT = """---
type: review-protocol
question: "Does X improve Y?"
seed_queries:
  by-method:     "transformer attention mechanism"
  by-outcome:    "translation quality improvement"
  by-paradigm:   "sequence to sequence learning"
  by-population: "machine translation benchmarks"
sources: [semantic-scholar, arxiv, openalex]
coverage_claim: "all English papers 2015-2025"
---

# Protocol
"""

LEGACY_PROTOCOL_TEXT = """---
type: review-protocol
question: "Does X improve Y?"
seed_queries:
  - "transformer attention"
  - "sequence to sequence"
---
"""


# ---------------------------------------------------------------------------
# Frozen-protocol parsing
# ---------------------------------------------------------------------------

def test_parse_angle_matrix_extracts_all_four_angles() -> None:
    matrix = parse_angle_matrix(PROTOCOL_TEXT)
    assert matrix == {
        "by-method": "transformer attention mechanism",
        "by-outcome": "translation quality improvement",
        "by-paradigm": "sequence to sequence learning",
        "by-population": "machine translation benchmarks",
    }


def test_parse_angle_matrix_empty_on_legacy_flat_list() -> None:
    """The legacy flat-list `seed_queries:` shape is NOT the angle-matrix
    shape — parse_angle_matrix must return {} (never crash, never
    misinterpret a list item as a mapping key)."""
    assert parse_angle_matrix(LEGACY_PROTOCOL_TEXT) == {}


def test_parse_sources_extracts_inline_list() -> None:
    assert parse_sources(PROTOCOL_TEXT) == ["semantic-scholar", "arxiv", "openalex"]


def test_parse_sources_defaults_to_d4_set_when_absent() -> None:
    from research_vault.sources.registry import DEFAULT_SOURCES
    assert parse_sources(LEGACY_PROTOCOL_TEXT) == list(DEFAULT_SOURCES)


# ---------------------------------------------------------------------------
# Parallel width-sweep
# ---------------------------------------------------------------------------

def _fake_hit(title: str, source: str, external_ids=None) -> PaperHit:
    return PaperHit(
        title=title, year=2020, authors=["A"], external_ids=external_ids or {},
        abstract="", citation_count=1, source=source,
    )


def test_run_width_sweep_covers_full_cross_product(monkeypatch) -> None:
    from research_vault.sources import sweep as sweep_mod

    calls = []

    def fake_fetch_cell(angle, query, source, *, limit):
        calls.append((angle, source))
        return SweepCell(angle=angle, query=query, source=source, hits=[_fake_hit(f"{angle}-{source}", source)])

    monkeypatch.setattr(sweep_mod, "_fetch_cell", fake_fetch_cell)

    matrix = {"by-method": "q1", "by-outcome": "q2"}
    sources = ["semantic-scholar", "arxiv"]
    cells = run_width_sweep(matrix, sources)

    assert len(cells) == 4  # 2 angles x 2 sources
    assert set(calls) == {
        ("by-method", "semantic-scholar"), ("by-method", "arxiv"),
        ("by-outcome", "semantic-scholar"), ("by-outcome", "arxiv"),
    }


def test_run_width_sweep_degrades_gracefully_on_adapter_error(monkeypatch) -> None:
    """An adapter failure must not fail the whole sweep — the cell records the
    error and contributes zero hits; other cells still fetch normally (§10)."""
    from research_vault.sources import sweep as sweep_mod

    def fake_fetch_cell(angle, query, source, *, limit):
        if source == "arxiv":
            return SweepCell(angle=angle, query=query, source=source, error="network down")
        return SweepCell(angle=angle, query=query, source=source, hits=[_fake_hit("ok", source)])

    monkeypatch.setattr(sweep_mod, "_fetch_cell", fake_fetch_cell)
    cells = run_width_sweep({"by-method": "q1"}, ["semantic-scholar", "arxiv"])

    errored = [c for c in cells if c.error]
    ok = [c for c in cells if not c.error]
    assert len(errored) == 1 and errored[0].source == "arxiv"
    assert len(ok) == 1 and ok[0].hits


# ---------------------------------------------------------------------------
# Composition (dedup -> derivative discount -> rank+floor)
# ---------------------------------------------------------------------------

def test_compose_sweep_result_dedups_across_cells() -> None:
    same_paper_s2 = _fake_hit("Attention Is All You Need", "semantic-scholar", {"doi": "10.1/x"})
    same_paper_arxiv = _fake_hit("Attention Is All You Need", "arxiv", {"doi": "10.1/x"})
    cells = [
        SweepCell(angle="by-method", query="q1", source="semantic-scholar", hits=[same_paper_s2]),
        SweepCell(angle="by-method", query="q1", source="arxiv", hits=[same_paper_arxiv]),
    ]
    result = compose_sweep_result(cells, budget=DEFAULT_FETCH_BUDGET)
    assert result.total_hits_fetched == 2
    assert len(result.kept) == 1
    assert result.kept[0].sources == {"semantic-scholar", "arxiv"}


def test_compose_sweep_result_surfaces_cell_errors() -> None:
    cells = [SweepCell(angle="by-method", query="q1", source="arxiv", error="boom")]
    result = compose_sweep_result(cells)
    assert result.errors == ["by-method/arxiv: boom"]
    assert result.kept == []


# ---------------------------------------------------------------------------
# Anti-fishing: sweep only READS the frozen protocol, never writes it.
# ---------------------------------------------------------------------------

def test_sweep_module_has_no_protocol_write_path() -> None:
    """Anti-fishing: the protocol-READING functions (parse/fetch/compose) must
    never write anything — no mutation path lets a running sweep widen its
    own frozen seeds mid-run.

    review-loop-nodekind-drift-fix (Option C §4-A) added ``write_search_hits``,
    which legitimately writes the sweep's OWN OUTPUT artifact
    (``_search_hits.md``) — never the protocol. This test is scoped to the
    protocol-facing functions specifically (AST-inspected, not a whole-module
    substring check) so that legitimate new write stays un-flagged while the
    protocol-mutation invariant stays enforced.
    """
    import ast
    import inspect
    import textwrap
    from research_vault.sources import sweep as sweep_mod

    protocol_facing = (
        sweep_mod.parse_angle_matrix,
        sweep_mod.parse_sources,
        sweep_mod.run_width_sweep,
        sweep_mod.compose_sweep_result,
        sweep_mod.run_sweep_from_protocol,
    )
    for fn in protocol_facing:
        src = textwrap.dedent(inspect.getsource(fn))
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "write_text":
                pytest.fail(f"{fn.__name__} must never call write_text (protocol is read-only)")


NO_ANGLE_MATRIX_PROTOCOL = """---
type: review-protocol
question: "Does X improve Y?"
---
"""


def test_run_sweep_from_protocol_raises_on_no_angle_matrix(tmp_path: Path, monkeypatch) -> None:
    protocol_path = tmp_path / "_protocol.md"
    protocol_path.write_text(NO_ANGLE_MATRIX_PROTOCOL, encoding="utf-8")
    with pytest.raises(ValueError, match="no `seed_queries:` angle matrix"):
        run_sweep_from_protocol(protocol_path)


def test_run_sweep_from_protocol_end_to_end(tmp_path: Path, monkeypatch) -> None:
    from research_vault.sources import sweep as sweep_mod

    protocol_path = tmp_path / "_protocol.md"
    protocol_path.write_text(PROTOCOL_TEXT, encoding="utf-8")

    def fake_fetch_cell(angle, query, source, *, limit):
        return SweepCell(angle=angle, query=query, source=source, hits=[_fake_hit(f"{angle}", source)])

    monkeypatch.setattr(sweep_mod, "_fetch_cell", fake_fetch_cell)
    result = run_sweep_from_protocol(protocol_path, budget=50)
    assert result.total_hits_fetched == 4 * 3  # 4 angles x 3 sources
