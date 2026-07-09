"""test_sweep_search_hits.py — sources.sweep.write_search_hits (Option C §4-A,
docs/superpowers/specs/2026-07-09-review-loop-nodekind-drift-fix.md).

Coverage:
  1. writes a markdown file with per-cell counts (incl. degraded/errored cells)
  2. [NEW] annotation for a hit absent from the corpus index
  3. [IN-CORPUS:<citekey>] annotation for a hit matching notes_index
  4. [DERIVATIVE-OF:*] flag surfaced for a derivative-flagged hit
  5. errors list surfaced in the output
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.sources.base import PaperHit
from research_vault.sources.dedup import DedupedHit
from research_vault.sources.sweep import SweepCell, SweepResult, write_search_hits


def _hit(title: str, *, doi: str | None = None, arxiv: str | None = None) -> PaperHit:
    ext = {}
    if doi:
        ext["doi"] = doi
    if arxiv:
        ext["arxiv"] = arxiv
    return PaperHit(
        title=title, year=2024, authors=["A. Author"], external_ids=ext,
        abstract="abstract text", citation_count=0, source="semantic-scholar",
    )


def test_write_search_hits_cell_counts_and_errors(tmp_path):
    cells = [
        SweepCell(angle="by-method", query="q1", source="semantic-scholar", hits=[_hit("Paper A", doi="10.1/a")]),
        SweepCell(angle="by-outcome", query="q2", source="arxiv", error="NotSupported: no keyword search"),
    ]
    result = SweepResult(kept=[], independent_count=0, total_hits_fetched=1, cells=cells, errors=["by-outcome/arxiv: NotSupported: no keyword search"])
    out = write_search_hits(result, tmp_path / "_search_hits.md")
    text = out.read_text()
    assert "by-method" in text and "semantic-scholar" in text
    assert "NotSupported" in text
    assert "Total hits fetched: 1" in text


def test_write_search_hits_new_annotation(tmp_path):
    hit = _hit("Brand New Paper", doi="10.1/new")
    kept = [DedupedHit(hit=hit, sources={"semantic-scholar"}, external_ids=dict(hit.external_ids))]
    result = SweepResult(kept=kept, independent_count=1, total_hits_fetched=1, cells=[], errors=[])
    out = write_search_hits(result, tmp_path / "_search_hits.md", notes_index={})
    text = out.read_text()
    assert "[NEW]" in text
    assert "Brand New Paper" in text


def test_write_search_hits_in_corpus_annotation(tmp_path):
    hit = _hit("Already Filed Paper", doi="10.1/old")
    kept = [DedupedHit(hit=hit, sources={"semantic-scholar"}, external_ids=dict(hit.external_ids))]
    result = SweepResult(kept=kept, independent_count=1, total_hits_fetched=1, cells=[], errors=[])
    out = write_search_hits(result, tmp_path / "_search_hits.md", notes_index={"10.1/old": "smith2024"})
    text = out.read_text()
    assert "[IN-CORPUS:smith2024]" in text


def test_write_search_hits_derivative_flag(tmp_path):
    hit = _hit("A Restatement Paper")
    hit.derivative_of = "identity-of-original"
    kept = [DedupedHit(hit=hit, sources={"semantic-scholar"}, external_ids={})]
    result = SweepResult(kept=kept, independent_count=0, total_hits_fetched=1, cells=[], errors=[])
    out = write_search_hits(result, tmp_path / "_search_hits.md", notes_index={})
    text = out.read_text()
    assert "[DERIVATIVE-OF:identity-of-original]" in text


def test_write_search_hits_below_floor_flag(tmp_path):
    hit = _hit("Boundary Item")
    hit.below_floor = True
    kept = [DedupedHit(hit=hit, sources={"semantic-scholar"}, external_ids={})]
    result = SweepResult(kept=kept, independent_count=1, total_hits_fetched=1, cells=[], errors=[])
    out = write_search_hits(result, tmp_path / "_search_hits.md", notes_index={})
    text = out.read_text()
    assert "[BELOW-FLOOR: needs more sources]" in text


def test_write_search_hits_carries_abstract(tmp_path):
    hit = _hit("Paper With Abstract", doi="10.1/abs")
    hit.abstract = "This paper studies a novel architecture for X."
    kept = [DedupedHit(hit=hit, sources={"semantic-scholar"}, external_ids={})]
    result = SweepResult(kept=kept, independent_count=1, total_hits_fetched=1, cells=[], errors=[])
    out = write_search_hits(result, tmp_path / "_search_hits.md", notes_index={})
    text = out.read_text()
    assert "This paper studies a novel architecture for X." in text


def test_write_search_hits_falls_back_to_tldr_when_abstract_empty(tmp_path):
    hit = _hit("Paper With Only TLDR", doi="10.1/tldr")
    hit.abstract = ""
    hit.raw = {"tldr": {"model": "tldr@v2", "text": "A short tldr summary."}}
    kept = [DedupedHit(hit=hit, sources={"semantic-scholar"}, external_ids={})]
    result = SweepResult(kept=kept, independent_count=1, total_hits_fetched=1, cells=[], errors=[])
    out = write_search_hits(result, tmp_path / "_search_hits.md", notes_index={})
    text = out.read_text()
    assert "A short tldr summary." in text


def test_write_search_hits_no_evidence_when_both_absent(tmp_path):
    hit = _hit("Paper With No Evidence", doi="10.1/none")
    hit.abstract = ""
    kept = [DedupedHit(hit=hit, sources={"semantic-scholar"}, external_ids={})]
    result = SweepResult(kept=kept, independent_count=1, total_hits_fetched=1, cells=[], errors=[])
    out = write_search_hits(result, tmp_path / "_search_hits.md", notes_index={})
    # no crash, no fabricated text — just an honestly-blank cell.
    assert out.exists()


def test_write_search_hits_venue_and_year_present(tmp_path):
    hit = _hit("Venued Paper", doi="10.1/venue")
    hit.venue = "NeurIPS"
    hit.year = 2023
    kept = [DedupedHit(hit=hit, sources={"semantic-scholar"}, external_ids={})]
    result = SweepResult(kept=kept, independent_count=1, total_hits_fetched=1, cells=[], errors=[])
    out = write_search_hits(result, tmp_path / "_search_hits.md", notes_index={})
    text = out.read_text()
    assert "NeurIPS" in text
    assert "2023" in text


def test_write_search_hits_venue_blank_when_absent(tmp_path):
    hit = _hit("Venueless Paper", doi="10.1/novenue")
    hit.venue = None
    kept = [DedupedHit(hit=hit, sources={"semantic-scholar"}, external_ids={})]
    result = SweepResult(kept=kept, independent_count=1, total_hits_fetched=1, cells=[], errors=[])
    out = write_search_hits(result, tmp_path / "_search_hits.md", notes_index={})
    # renders cleanly with no crash and no fabricated "None" string
    text = out.read_text()
    assert "None" not in text


def test_write_search_hits_below_floor_discriminates_when_mixed(tmp_path):
    below = _hit("Boundary Paper", doi="10.1/below")
    below.below_floor = True
    above = _hit("Well-Sourced Paper", doi="10.1/above")
    above.below_floor = False
    kept = [
        DedupedHit(hit=below, sources={"semantic-scholar"}, external_ids={}),
        DedupedHit(hit=above, sources={"semantic-scholar", "arxiv", "openalex"}, external_ids={}),
    ]
    result = SweepResult(kept=kept, independent_count=2, total_hits_fetched=2, cells=[], errors=[])
    out = write_search_hits(result, tmp_path / "_search_hits.md", notes_index={})
    text = out.read_text()
    # the flag differentiates: present on the boundary row, absent elsewhere
    below_line = next(line for line in text.splitlines() if "Boundary Paper" in line)
    above_line = next(line for line in text.splitlines() if "Well-Sourced Paper" in line)
    assert "[BELOW-FLOOR" in below_line
    assert "[BELOW-FLOOR" not in above_line


def test_write_search_hits_below_floor_suppressed_when_universal(tmp_path):
    hit_a = _hit("Paper A", doi="10.1/a2")
    hit_a.below_floor = True
    hit_b = _hit("Paper B", doi="10.1/b2")
    hit_b.below_floor = True
    kept = [
        DedupedHit(hit=hit_a, sources={"semantic-scholar"}, external_ids={}),
        DedupedHit(hit=hit_b, sources={"arxiv"}, external_ids={}),
    ]
    result = SweepResult(kept=kept, independent_count=2, total_hits_fetched=2, cells=[], errors=[])
    out = write_search_hits(result, tmp_path / "_search_hits.md", notes_index={})
    text = out.read_text()
    # zero signal (100% of kept rows below floor) -> the per-row flag
    # (`[BELOW-FLOOR: ...]`, distinct from the suppression note's own
    # `[BELOW-FLOOR]` mention) is gone from every row; the suppression
    # itself is surfaced (never silent).
    assert "[BELOW-FLOOR: needs more sources]" not in text
    assert "suppressed" in text.lower()


def test_write_search_hits_creates_parent_dirs(tmp_path):
    result = SweepResult(kept=[], independent_count=0, total_hits_fetched=0, cells=[], errors=[])
    out_path = tmp_path / "reviews" / "scope1" / "_search_hits.md"
    out = write_search_hits(result, out_path)
    assert out.exists()
