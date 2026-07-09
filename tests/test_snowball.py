"""test_snowball.py — sources.snowball.run_snowball_to_saturation (Option C
§4-B, docs/superpowers/specs/2026-07-09-review-loop-nodekind-drift-fix.md).

Coverage:
  1. saturation: 2 consecutive zero-independent-new rounds -> stop_reason == "saturated"
  2. backstop: a never-saturating neighborhood hits the wave cap -> "backstop:N-waves"
  3. direction-starved flag when only one direction returns hits
  4. derivative discount: a near-duplicate restatement doesn't count as independent-new
  5. an adapter raising NotSupported degrades gracefully (no crash)
  6. write_corpus_raw / write_saturation render the expected artifacts
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.sources.base import NotSupported, PaperHit
from research_vault.sources.snowball import (
    run_snowball_to_saturation,
    write_corpus_raw,
    write_saturation,
)


def _hit(title: str, *, doi: str | None = None, abstract: str = "") -> PaperHit:
    ext = {"doi": doi} if doi else {}
    return PaperHit(
        title=title, year=2024, authors=["A. Author"], external_ids=ext,
        abstract=abstract or title, citation_count=0, source="semantic-scholar",
    )


class _ScriptedAdapter:
    """Fake SourceAdapter: scripted per-round (forward, backward) hit lists,
    keyed by round number (1-based). Missing rounds return [].

    Round tracking: every test in this file seeds a single-paper frontier
    each round (the scripted rounds each yield at most one new paper), so
    ``cited_by`` is called exactly once per round — its own call count IS
    the round number. ``references`` reads the SAME counter (cited_by is
    always invoked first for a given frontier paper by the real snowball
    loop), so this stays correct even when a subclass overrides one of the
    two methods to raise.
    """

    name = "fake"

    def __init__(self, script: dict[int, tuple[list[PaperHit], list[PaperHit]]]):
        self.script = script
        self.calls: list[tuple[str, str]] = []
        self._cited_by_calls = 0

    def search(self, query, *, limit=20):
        raise NotSupported("search not used by snowball")

    def cited_by(self, paper_id, *, limit=20):
        self._cited_by_calls += 1
        self.calls.append(("cited_by", paper_id))
        fwd, _ = self.script.get(self._cited_by_calls, ([], []))
        return fwd

    def references(self, paper_id, *, limit=20):
        self.calls.append(("references", paper_id))
        _, bwd = self.script.get(self._cited_by_calls, ([], []))
        return bwd


def test_saturation_two_consecutive_zero_rounds():
    adapter = _ScriptedAdapter({
        1: ([_hit("New Paper 1", doi="10.1/new1")], []),
        2: ([], []),
        3: ([], []),
    })
    result = run_snowball_to_saturation(["10.1/seed"], adapter=adapter, backstop_waves=3)
    assert result.stop_reason == "saturated"
    assert result.is_backstop is False
    # round 1 found something, rounds 2+3 found nothing -> 2 consecutive zero -> stop
    assert len(result.rounds) == 3
    assert result.rounds[0].new_independent == 1
    assert result.rounds[1].new_independent == 0
    assert result.rounds[2].new_independent == 0


def test_backstop_never_saturates():
    # Every round returns a genuinely new, non-derivative paper -> never
    # 2-consecutive-zero -> backstop fires at the wave cap.
    adapter = _ScriptedAdapter({
        1: ([_hit("Distinct Paper Alpha population outcome method one", doi="10.1/a1")], []),
        2: ([_hit("Distinct Paper Beta measurement design cohort two", doi="10.1/a2")], []),
        3: ([_hit("Distinct Paper Gamma protocol trial sample three", doi="10.1/a3")], []),
    })
    result = run_snowball_to_saturation(["10.1/seed"], adapter=adapter, backstop_waves=3)
    assert result.stop_reason == "backstop:3-waves"
    assert result.is_backstop is True
    assert len(result.rounds) == 3


def test_direction_starved_flag():
    adapter = _ScriptedAdapter({
        1: ([_hit("Forward Only Paper", doi="10.1/f1")], []),
        2: ([], []),
        3: ([], []),
    })
    result = run_snowball_to_saturation(["10.1/seed"], adapter=adapter, backstop_waves=3)
    assert result.rounds[0].direction_starved is True
    assert result.rounds[0].new_forward == 1
    assert result.rounds[0].new_backward == 0


def test_derivative_discount_does_not_count_as_independent_new():
    original = _hit("A Detailed Study Of Exploration Bonuses In Deep RL", doi="10.1/orig",
                     abstract="exploration bonuses deep reinforcement learning stochastic drives robust")
    restatement = _hit("A Detailed Study Of Exploration Bonuses In Deep RL (preprint)", doi="10.1/dup",
                        abstract="exploration bonuses deep reinforcement learning stochastic drives robust")
    adapter = _ScriptedAdapter({
        1: ([original, restatement], []),
        2: ([], []),
        3: ([], []),
    })
    result = run_snowball_to_saturation(["10.1/seed"], adapter=adapter, backstop_waves=3)
    # Two hits arrived, but one is a near-duplicate restatement of the
    # other -> only 1 counts as independent-new.
    assert result.rounds[0].new_independent == 1
    # Both still appear in kept (discount, never delete).
    assert len(result.kept) == 2


def test_not_supported_degrades_gracefully():
    class _NoBackwardAdapter(_ScriptedAdapter):
        def references(self, paper_id, *, limit=20):
            raise NotSupported("no reference graph")

    adapter = _NoBackwardAdapter({1: ([_hit("Fwd Only", doi="10.1/f")], []), 2: ([], []), 3: ([], [])})
    result = run_snowball_to_saturation(["10.1/seed"], adapter=adapter, backstop_waves=3)
    assert result.errors == []
    assert result.stop_reason in ("saturated", "backstop:3-waves")


def test_unexpected_exception_recorded_not_raised():
    class _BoomAdapter(_ScriptedAdapter):
        def cited_by(self, paper_id, *, limit=20):
            raise RuntimeError("adapter unreachable")

    adapter = _BoomAdapter({1: ([], []), 2: ([], []), 3: ([], [])})
    result = run_snowball_to_saturation(["10.1/seed"], adapter=adapter, backstop_waves=3)
    assert any("adapter unreachable" in e for e in result.errors)
    assert result.stop_reason  # never blank


def test_stop_reason_never_blank_and_exactly_canonical():
    adapter = _ScriptedAdapter({1: ([], []), 2: ([], []), 3: ([], [])})
    result = run_snowball_to_saturation(["10.1/seed"], adapter=adapter, backstop_waves=3)
    assert result.stop_reason == "saturated"


def test_write_corpus_raw_and_saturation(tmp_path):
    adapter = _ScriptedAdapter({
        1: ([_hit("New Paper 1", doi="10.1/new1")], []),
        2: ([], []),
        3: ([], []),
    })
    result = run_snowball_to_saturation(["10.1/seed"], adapter=adapter, backstop_waves=3)

    corpus_out = write_corpus_raw(result, tmp_path / "_corpus_raw.md", notes_index={})
    text = corpus_out.read_text()
    assert "[NEW]" in text
    assert "New Paper 1" in text
    assert f"Stop reason: {result.stop_reason}" in text

    sat_out = write_saturation(result, tmp_path / "_saturation.md")
    sat_text = sat_out.read_text()
    assert "stop_reason: saturated" in sat_text
    assert "| Round |" in sat_text
