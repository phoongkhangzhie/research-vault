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
from research_vault.sources.dedup import DedupedHit, dedup_hits
from research_vault.sources.sweep import SweepCell, SweepResult, compose_sweep_result, write_search_hits


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


def test_evidence_snippet_default_cap_is_800_chars():
    """Pre-publish curation finding (v0.3.0): 280 chars was too short to
    verify the "measured human baseline" inclusion axis during the sweep/
    snowball candidate-table screen — that signal often sits deeper in the
    abstract. Raise the default cap 280 -> 800 (display-cap only; the full
    abstract is already fetched onto ``hit.abstract``, this only changes how
    much of it is written to the table)."""
    from research_vault.sources.sweep import _evidence_snippet

    long_abstract = "word " * 300  # far more than 800 chars raw
    hit = _hit("Long Abstract Paper", doi="10.1/long")
    hit.abstract = long_abstract
    snippet = _evidence_snippet(hit)
    assert len(snippet) == 800
    assert snippet.endswith("…")

    short_abstract = "A short abstract well under the cap."
    hit2 = _hit("Short Abstract Paper", doi="10.1/short")
    hit2.abstract = short_abstract
    snippet2 = _evidence_snippet(hit2)
    assert snippet2 == short_abstract  # written whole, no truncation


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


# ---------------------------------------------------------------------------
# Paper-id enrichment regression (pre-publish hardening batch, downstream
# e2e-run finding): the 4 STRONGEST accepted seeds came out with a BLANK
# Paper-id because the pid lookup read the first-seen representative hit's
# OWN external_ids instead of the merged union `dedup_hits` accumulates onto
# the `DedupedHit` wrapper. Drives the REAL `dedup_hits` producer (not a
# hand-planted DedupedHit) — the "test the real thing" convention.
# ---------------------------------------------------------------------------

def test_write_search_hits_paper_id_never_blank_when_a_duplicate_has_it(tmp_path):
    """The representative (first-seen) hit for an identity has NO doi/arxiv/
    openalex/s2 of its own (identity_key falls back to normalized TITLE for
    both hits — s2 ids never factor into identity_key's priority chain) —
    but a LATER duplicate surfacing the same paper from another source DOES
    carry an s2 id. `dedup_hits` merges that id onto the wrapper's
    `external_ids`; the rendered Paper-id column must NOT come out blank.
    This is the exact live-run shape: an S2-native hit resolves an s2 id
    that never influenced which identity the title-matched duplicates
    collapsed onto."""
    narrow_hit = PaperHit(
        title="Activation Steering For Cultural Values", year=2024, authors=["A. One"],
        external_ids={}, abstract="", citation_count=5, source="openalex",
    )
    rich_duplicate = PaperHit(
        title="Activation Steering For Cultural Values", year=2024, authors=["A. One"],
        external_ids={"s2": "abc123"}, abstract="", citation_count=5, source="semantic-scholar",
    )
    # order matters: narrow_hit is first-seen (becomes d.hit); rich_duplicate
    # only contributes external_ids via the union (dedup_hits' documented
    # "first-seen wins as representative" contract).
    cells = [
        SweepCell(angle="by-method", query="q1", source="openalex", hits=[narrow_hit]),
        SweepCell(angle="by-method", query="q1", source="semantic-scholar", hits=[rich_duplicate]),
    ]
    result = compose_sweep_result(cells)
    assert len(result.kept) == 1
    # sanity: the representative hit really is the narrow one (proves this
    # test is non-vacuous — the bug can only manifest if d.hit lacks the id)
    assert result.kept[0].hit.external_ids == {}
    assert result.kept[0].external_ids == {"s2": "abc123"}  # the merged union DOES have it

    out = write_search_hits(result, tmp_path / "_search_hits.md", notes_index={})
    text = out.read_text()
    row = next(line for line in text.splitlines() if "Activation Steering" in line)
    assert "abc123" in row, f"Paper-id column is blank despite the merged union carrying an s2 id:\n{row}"
    assert "[NO-ID" not in row


def test_write_search_hits_flags_no_id_when_truly_unresolvable(tmp_path):
    """When NEITHER the representative nor any duplicate resolved an id, the
    row must be FLAGGED — never a silently blank Paper-id cell."""
    hit = PaperHit(
        title="Untitled Preprint With No Ids", year=2024, authors=["A"],
        external_ids={}, abstract="", citation_count=0, source="openalex",
    )
    kept = [DedupedHit(hit=hit, sources={"openalex"}, external_ids={})]
    result = SweepResult(kept=kept, independent_count=1, total_hits_fetched=1, cells=[], errors=[])
    out = write_search_hits(result, tmp_path / "_search_hits.md", notes_index={})
    text = out.read_text()
    row = next(line for line in text.splitlines() if "Untitled Preprint" in line)
    assert "[NO-ID" in row


def test_write_search_hits_defaults_to_no_backfill_no_network(tmp_path):
    """Default call (no attempt_id_backfill, no backfill_adapters) must
    never attempt a lookup — hermetic, zero-behavior-change from before A3."""
    hit = PaperHit(
        title="Untitled Preprint With No Ids", year=2024, authors=["A"],
        external_ids={}, abstract="", citation_count=0, source="openalex",
    )
    kept = [DedupedHit(hit=hit, sources={"openalex"}, external_ids={})]
    result = SweepResult(kept=kept, independent_count=1, total_hits_fetched=1, cells=[], errors=[])
    out = write_search_hits(result, tmp_path / "_search_hits.md", notes_index={})
    text = out.read_text()
    assert "id_backfill_missing: 1" in text
    assert "id_backfill_resolved: 0" in text
    assert "id_backfill_unresolved: 1" in text


# ---------------------------------------------------------------------------
# A3: id-resolution must not silently drop canonical papers (spec A3,
# the Herrmann-2008 case: a 3000-cite paper [NO-ID]-dropped on messy
# search-hit metadata is an id-resolution failure, not search-vs-walk).
# ---------------------------------------------------------------------------

class _StubBackfillAdapter:
    """No-network stand-in for a SourceAdapter, injected via
    ``backfill_adapters`` — proves ``write_search_hits`` actually wires the
    backfill attempt through to ``identifiers.backfill_missing_ids``."""

    def __init__(self, hits):
        self.name = "stub"
        self._hits = hits

    def search(self, query, *, limit=20):
        return self._hits


def test_write_search_hits_backfills_herrmann_style_candidate_and_keeps_it(tmp_path):
    """A messy-metadata candidate (no id) with a resolvable title/year gets
    backfilled — the row carries the resolved id, and [NO-ID] never fires."""
    hit = PaperHit(
        title="Economic Man in Cross-Cultural Perspective", year=2008,
        authors=["J. Herrmann"], external_ids={}, abstract="",
        citation_count=3000, source="openalex",
    )
    kept = [DedupedHit(hit=hit, sources={"openalex"}, external_ids={})]
    result = SweepResult(kept=kept, independent_count=1, total_hits_fetched=1, cells=[], errors=[])

    resolver_hit = PaperHit(
        title="Economic Man in Cross-Cultural Perspective", year=2008,
        authors=["J. Herrmann"], external_ids={"doi": "10.1126/science.herrmann2008"},
        abstract="", citation_count=3000, source="openalex",
    )
    adapter = _StubBackfillAdapter([resolver_hit])

    out = write_search_hits(
        result, tmp_path / "_search_hits.md", notes_index={},
        backfill_adapters=[adapter],
    )
    text = out.read_text()
    row = next(line for line in text.splitlines() if "Economic Man" in line)
    assert "10.1126/science.herrmann2008" in row
    assert "[NO-ID" not in row
    assert "id_backfill_missing: 1" in text
    assert "id_backfill_resolved: 1" in text
    assert "id_backfill_unresolved: 0" in text
    assert "Id-resolution:" in text


def test_write_search_hits_genuinely_unresolvable_still_flagged_but_counted(tmp_path):
    """A candidate backfill genuinely can't resolve is STILL [NO-ID]-flagged
    — but the resolution-rate metric records the attempt, not a silent 0."""
    hit = PaperHit(
        title="A Truly Untraceable Preprint", year=2024, authors=["A"],
        external_ids={}, abstract="", citation_count=0, source="openalex",
    )
    kept = [DedupedHit(hit=hit, sources={"openalex"}, external_ids={})]
    result = SweepResult(kept=kept, independent_count=1, total_hits_fetched=1, cells=[], errors=[])
    adapter = _StubBackfillAdapter([])  # no match found anywhere

    out = write_search_hits(
        result, tmp_path / "_search_hits.md", notes_index={},
        backfill_adapters=[adapter],
    )
    text = out.read_text()
    row = next(line for line in text.splitlines() if "Untraceable" in line)
    assert "[NO-ID" in row
    assert "id_backfill_missing: 1" in text
    assert "id_backfill_resolved: 0" in text
    assert "id_backfill_unresolved: 1" in text


def test_write_search_hits_backfill_degrades_past_dead_adapter(tmp_path):
    """One dead adapter in the backfill chain must not abort the render —
    the next adapter in the chain still resolves the id."""
    hit = PaperHit(
        title="Economic Man in Cross-Cultural Perspective", year=2008,
        authors=["J. Herrmann"], external_ids={}, abstract="",
        citation_count=3000, source="openalex",
    )
    kept = [DedupedHit(hit=hit, sources={"openalex"}, external_ids={})]
    result = SweepResult(kept=kept, independent_count=1, total_hits_fetched=1, cells=[], errors=[])

    class _DeadAdapter:
        name = "dead"

        def search(self, query, *, limit=20):
            raise RuntimeError("adapter down")

    resolver_hit = PaperHit(
        title="Economic Man in Cross-Cultural Perspective", year=2008,
        authors=["J. Herrmann"], external_ids={"doi": "10.1126/science.herrmann2008"},
        abstract="", citation_count=3000, source="openalex",
    )
    out = write_search_hits(
        result, tmp_path / "_search_hits.md", notes_index={},
        backfill_adapters=[_DeadAdapter(), _StubBackfillAdapter([resolver_hit])],
    )
    text = out.read_text()
    row = next(line for line in text.splitlines() if "Economic Man" in line)
    assert "10.1126/science.herrmann2008" in row
    assert "[NO-ID" not in row


# ---------------------------------------------------------------------------
# Dark-source signal rendering (pre-publish hardening batch)
# ---------------------------------------------------------------------------

def test_write_search_hits_stamps_dark_sources_frontmatter(tmp_path):
    result = SweepResult(kept=[], independent_count=0, total_hits_fetched=0, cells=[], errors=[], dark_sources=["arxiv"])
    out = write_search_hits(result, tmp_path / "_search_hits.md")
    text = out.read_text()
    assert "dark_sources: arxiv" in text
    assert "SOURCE DARK" in text
    assert "arxiv" in text.split("SOURCE DARK")[1][:200]


def test_write_search_hits_dark_sources_empty_frontmatter_when_healthy(tmp_path):
    result = SweepResult(kept=[], independent_count=0, total_hits_fetched=0, cells=[], errors=[], dark_sources=[])
    out = write_search_hits(result, tmp_path / "_search_hits.md")
    text = out.read_text()
    assert "dark_sources: " in text
    assert "SOURCE DARK" not in text
