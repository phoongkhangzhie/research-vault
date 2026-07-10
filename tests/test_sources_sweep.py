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

from research_vault.sources.base import NotSupported, PaperHit
from research_vault.sources.sweep import (
    DEFAULT_FETCH_BUDGET,
    SweepCell,
    _CELL_RETRY_ATTEMPTS,
    _fetch_cell,
    compose_sweep_result,
    detect_dark_sources,
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

    def fake_fetch_cell(angle, query, source, *, limit, **_ignored):
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

    def fake_fetch_cell(angle, query, source, *, limit, **_ignored):
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

    def fake_fetch_cell(angle, query, source, *, limit, **_ignored):
        return SweepCell(angle=angle, query=query, source=source, hits=[_fake_hit(f"{angle}", source)])

    monkeypatch.setattr(sweep_mod, "_fetch_cell", fake_fetch_cell)
    result = run_sweep_from_protocol(protocol_path, budget=50)
    assert result.total_hits_fetched == 4 * 3  # 4 angles x 3 sources


# ---------------------------------------------------------------------------
# Retry-with-backoff on a transient adapter failure (pre-publish hardening
# batch, downstream e2e-run finding: all 5 arXiv cells timed out in one run
# with zero retry).
# ---------------------------------------------------------------------------

class _FlakyAdapter:
    """Fails ``fail_times`` times with a transient exception, then succeeds."""

    name = "semantic-scholar"

    def __init__(self, fail_times: int, exc: Exception | None = None):
        self.fail_times = fail_times
        self.calls = 0
        self.exc = exc or TimeoutError("connection timed out")

    def search(self, query, *, limit=20):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        return [_fake_hit("recovered", self.name)]

    def cited_by(self, paper_id, *, limit=20):
        raise NotSupported

    def references(self, paper_id, *, limit=20):
        raise NotSupported


def test_fetch_cell_retries_transient_failure_then_succeeds(monkeypatch) -> None:
    """A cell that times out on the first attempt but succeeds on retry must
    end up with real hits and no error — never degrade on a single blip."""
    from research_vault.sources import sweep as sweep_mod

    adapter = _FlakyAdapter(fail_times=1)
    monkeypatch.setattr(sweep_mod, "get_adapter", lambda name: adapter)

    sleeps: list[float] = []
    cell = _fetch_cell("by-method", "q1", "semantic-scholar", limit=20, sleep_fn=sleeps.append)

    assert cell.error is None
    assert len(cell.hits) == 1
    assert adapter.calls == 2  # failed once, succeeded on retry
    assert len(sleeps) == 1  # exactly one backoff sleep between the two attempts


def test_fetch_cell_exhausts_retries_and_degrades(monkeypatch) -> None:
    """A cell that ALWAYS fails must still terminate (bounded retry) and
    degrade gracefully — never an infinite retry loop."""
    from research_vault.sources import sweep as sweep_mod

    adapter = _FlakyAdapter(fail_times=999)  # always fails
    monkeypatch.setattr(sweep_mod, "get_adapter", lambda name: adapter)

    sleeps: list[float] = []
    cell = _fetch_cell("by-method", "q1", "semantic-scholar", limit=20, sleep_fn=sleeps.append)

    assert cell.error is not None
    assert "TimeoutError" in cell.error
    assert cell.hits == []
    assert adapter.calls == _CELL_RETRY_ATTEMPTS  # bounded — exactly the configured cap
    assert len(sleeps) == _CELL_RETRY_ATTEMPTS - 1  # backoff sleeps between tries, none after the last


def test_fetch_cell_backoff_is_exponential(monkeypatch) -> None:
    from research_vault.sources import sweep as sweep_mod

    adapter = _FlakyAdapter(fail_times=999)
    monkeypatch.setattr(sweep_mod, "get_adapter", lambda name: adapter)

    sleeps: list[float] = []
    _fetch_cell(
        "by-method", "q1", "semantic-scholar", limit=20,
        retry_attempts=3, backoff_base=0.5, sleep_fn=sleeps.append,
    )
    assert sleeps == [0.5, 1.0]  # 0.5 * 2**0, 0.5 * 2**1


def test_fetch_cell_never_retries_not_supported(monkeypatch) -> None:
    """NotSupported is a PERMANENT signal — retrying it wastes the backoff
    budget for zero chance of success."""
    from research_vault.sources import sweep as sweep_mod

    class _NoSearchAdapter:
        name = "arxiv"

        def search(self, query, *, limit=20):
            raise NotSupported("no keyword search")

    monkeypatch.setattr(sweep_mod, "get_adapter", lambda name: _NoSearchAdapter())
    sleeps: list[float] = []
    cell = _fetch_cell("by-method", "q1", "arxiv", limit=20, sleep_fn=sleeps.append)

    assert cell.error is not None
    assert "NotSupported" in cell.error or "no keyword search" in cell.error
    assert sleeps == []  # zero retries — no backoff burned


def test_fetch_cell_never_retries_unknown_adapter_name(monkeypatch) -> None:
    """An unknown source name (protocol typo) is a permanent config error,
    not a transient blip — must fail immediately, no retry, no adapter call."""
    from research_vault.sources import sweep as sweep_mod

    def _raise_unknown(name):
        raise ValueError(f"unknown source adapter {name!r}")

    monkeypatch.setattr(sweep_mod, "get_adapter", _raise_unknown)
    sleeps: list[float] = []
    cell = _fetch_cell("by-method", "q1", "not-a-real-source", limit=20, sleep_fn=sleeps.append)

    assert cell.error is not None
    assert "unknown source adapter" in cell.error
    assert sleeps == []


# ---------------------------------------------------------------------------
# Dark-source detection (pre-publish hardening batch): a whole source going
# dark across ALL angles must never look like a genuinely-thin sweep.
# ---------------------------------------------------------------------------

class TestDetectDarkSources:
    def test_source_with_all_cells_errored_is_dark(self) -> None:
        cells = [
            SweepCell(angle="by-method", query="q1", source="arxiv", error="timeout"),
            SweepCell(angle="by-outcome", query="q2", source="arxiv", error="timeout"),
        ]
        assert detect_dark_sources(cells) == ["arxiv"]

    def test_source_with_all_cells_empty_but_no_error_is_dark(self) -> None:
        """A source can be "dark" without erroring at all — every cell just
        legitimately returned zero hits on every angle."""
        cells = [
            SweepCell(angle="by-method", query="q1", source="pubmed", hits=[]),
            SweepCell(angle="by-outcome", query="q2", source="pubmed", hits=[]),
        ]
        assert detect_dark_sources(cells) == ["pubmed"]

    def test_source_with_one_hit_on_one_angle_is_not_dark(self) -> None:
        """The narrow boundary this defect hinges on: ONE hit on ONE angle
        is enough to prove the source was genuinely reached — "legitimately
        thin", not dark, however few hits it produced overall."""
        cells = [
            SweepCell(angle="by-method", query="q1", source="arxiv", error="timeout"),
            SweepCell(angle="by-outcome", query="q2", source="arxiv", hits=[_fake_hit("one hit", "arxiv")]),
            SweepCell(angle="by-paradigm", query="q3", source="arxiv", hits=[]),
        ]
        assert detect_dark_sources(cells) == []

    def test_healthy_source_alongside_dark_source(self) -> None:
        cells = [
            SweepCell(angle="by-method", query="q1", source="arxiv", error="timeout"),
            SweepCell(angle="by-outcome", query="q2", source="arxiv", error="timeout"),
            SweepCell(angle="by-method", query="q1", source="semantic-scholar", hits=[_fake_hit("ok", "semantic-scholar")]),
        ]
        assert detect_dark_sources(cells) == ["arxiv"]

    def test_no_dark_sources_when_all_healthy(self) -> None:
        cells = [
            SweepCell(angle="by-method", query="q1", source="arxiv", hits=[_fake_hit("a", "arxiv")]),
            SweepCell(angle="by-method", query="q1", source="openalex", hits=[_fake_hit("b", "openalex")]),
        ]
        assert detect_dark_sources(cells) == []


def test_compose_sweep_result_surfaces_dark_sources() -> None:
    cells = [
        SweepCell(angle="by-method", query="q1", source="arxiv", error="timeout"),
        SweepCell(angle="by-outcome", query="q2", source="arxiv", error="timeout"),
        SweepCell(angle="by-method", query="q1", source="semantic-scholar", hits=[_fake_hit("ok", "semantic-scholar")]),
    ]
    result = compose_sweep_result(cells)
    assert result.dark_sources == ["arxiv"]


def test_compose_sweep_result_dark_sources_empty_when_healthy() -> None:
    cells = [SweepCell(angle="by-method", query="q1", source="arxiv", hits=[_fake_hit("ok", "arxiv")])]
    result = compose_sweep_result(cells)
    assert result.dark_sources == []
