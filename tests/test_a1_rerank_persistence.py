"""test_a1_rerank_persistence.py — A1 (lit-review search-primary redesign,
task #86): persist the TF-IDF rerank score as a column through
``_search_hits.md`` -> ``_corpus_raw.md`` -> ``_corpus.md``, so it's
available downstream as a strength signal (Section C).

Coverage:
  1. PaperHit.rerank_score is an honest-blank (None) optional field.
  2. sweep._fetch_cell stamps rerank_score via TF-IDF against the cell's
     own query (the rerank that already exists at ``rv research find``
     time, now wired into the width-sweep pipeline).
  3. write_search_hits renders a "Rerank" column: a real score formatted,
     an absent score rendered as the explicit sentinel — never blank,
     never fabricated.
  4. write_corpus_raw carries rerank_score through the same column/sentinel
     convention (even though today's citation-neighbor walk never itself
     produces a scored hit — see note below — the function must not drop
     a score if one IS present on a hit it's given).
  5. review.relevance.parse_corpus_raw_rows / _render_corpus_raw_row round-
     trip the new column, and TOLERATE a legacy 7-column row (no Rerank
     column at all) — no positional-format break.
  6. review.relevance.screen_corpus_raw carries the Rerank column through
     kept rows unchanged.
  7. review.relevance.parse_corpus_table_with_abstract tolerates an
     OPTIONAL 5th Rerank column on the final _corpus.md table.
  8. review._parse_corpus_citekeys / check_corpus_all_accept_tagged still
     parse a corpus table correctly with the new trailing column present
     (both only ever read cols[0]/[1] — a pure append is safe).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.sources.base import PaperHit, RERANK_NO_SCORE, format_rerank_score
from research_vault.sources.dedup import DedupedHit
from research_vault.sources.sweep import (
    SweepCell,
    SweepResult,
    _fetch_cell,
    write_search_hits,
)
from research_vault.sources.snowball import SnowballResult, write_corpus_raw
from research_vault.review import relevance as rel
from research_vault.review import _parse_corpus_citekeys, check_corpus_all_accept_tagged


def _hit(title: str, *, doi: str | None = None, abstract: str = "", rerank_score: float | None = None) -> PaperHit:
    ext = {}
    if doi:
        ext["doi"] = doi
    return PaperHit(
        title=title, year=2024, authors=["A. Author"], external_ids=ext,
        abstract=abstract, citation_count=0, source="semantic-scholar",
        rerank_score=rerank_score,
    )


class TestPaperHitRerankField:
    def test_defaults_to_none(self):
        hit = _hit("A Paper", doi="10.1/a")
        assert hit.rerank_score is None

    def test_format_rerank_score_sentinel_and_real_value(self):
        assert format_rerank_score(None) == RERANK_NO_SCORE
        assert RERANK_NO_SCORE.strip() != ""  # never a blank cell
        assert format_rerank_score(0.7345) == "0.734" or format_rerank_score(0.7345) == "0.735"


class _FakeAdapter:
    """Minimal SourceAdapter double: search() returns fixed PaperHits."""

    name = "fake"

    def __init__(self, hits):
        self._hits = hits

    def search(self, query, *, limit=20, fields=None):
        return self._hits


class TestFetchCellStampsRerank:
    def test_rerank_score_stamped_against_cell_query(self, monkeypatch):
        # RED-before-GREEN proof: before stamping, a freshly-constructed
        # PaperHit carries no score at all.
        on_topic = _hit(
            "Cultural value alignment in large language models",
            doi="10.1/on-topic",
            abstract="We evaluate LLM cross-cultural value alignment across "
                     "diverse human populations using survey instruments.",
        )
        off_topic = _hit(
            "Spectroscopic survey of quasar emission-line ratios",
            doi="10.1/off-topic",
            abstract="A wide-field spectroscopic survey of active galactic "
                     "nuclei deriving black hole mass via reverberation mapping.",
        )
        assert on_topic.rerank_score is None
        assert off_topic.rerank_score is None

        from research_vault.sources import registry

        monkeypatch.setattr(
            registry, "get_adapter",
            lambda name: _FakeAdapter([on_topic, off_topic]),
        )
        import research_vault.sources.sweep as sweep_mod
        monkeypatch.setattr(sweep_mod, "get_adapter", lambda name: _FakeAdapter([on_topic, off_topic]))

        cell = _fetch_cell(
            "by-method", "cultural value alignment large language models",
            "semantic-scholar", limit=20,
        )
        assert cell.error is None
        assert len(cell.hits) == 2
        scored = {h.title: h.rerank_score for h in cell.hits}
        # The on-topic hit's TF-IDF cosine similarity to the query must be
        # scored, and higher than the off-topic hit's.
        assert scored[on_topic.title] is not None
        assert scored[off_topic.title] is not None
        assert scored[on_topic.title] > scored[off_topic.title]


class TestWriteSearchHitsRerankColumn:
    def test_rerank_column_present_with_real_score(self, tmp_path):
        hit = _hit("Scored Paper", doi="10.1/scored", rerank_score=0.812)
        kept = [DedupedHit(hit=hit, sources={"semantic-scholar"}, external_ids=dict(hit.external_ids))]
        result = SweepResult(kept=kept, independent_count=1, total_hits_fetched=1, cells=[], errors=[])
        out = write_search_hits(result, tmp_path / "_search_hits.md")
        text = out.read_text()
        assert "Rerank" in text
        assert "0.812" in text

    def test_rerank_column_sentinel_when_absent(self, tmp_path):
        hit = _hit("Unscored Paper", doi="10.1/unscored", rerank_score=None)
        kept = [DedupedHit(hit=hit, sources={"semantic-scholar"}, external_ids=dict(hit.external_ids))]
        result = SweepResult(kept=kept, independent_count=1, total_hits_fetched=1, cells=[], errors=[])
        out = write_search_hits(result, tmp_path / "_search_hits.md")
        text = out.read_text()
        assert RERANK_NO_SCORE in text


class TestWriteCorpusRawRerankColumn:
    def test_rerank_survives_when_present(self, tmp_path):
        hit = _hit("Scored Neighbor", doi="10.1/neighbor", rerank_score=0.501)
        kept = [DedupedHit(hit=hit, sources={"semantic-scholar"}, external_ids=dict(hit.external_ids))]
        result = SnowballResult(kept=kept, rounds=[], stop_reason="walk-complete:1-hops", seed_count=1)
        out = write_corpus_raw(result, tmp_path / "_corpus_raw.md")
        text = out.read_text()
        assert "Rerank" in text
        assert "0.501" in text

    def test_rerank_sentinel_for_snowball_discovered_hit(self, tmp_path):
        # Honest ground-truth today: a citation-neighbor-walk discovery is
        # a FRESH PaperHit fetched via cited_by/references, never a rerank
        # pass — it must render the explicit "no score" sentinel, never a
        # fabricated number.
        hit = _hit("Walk-discovered Neighbor", doi="10.1/walked")
        kept = [DedupedHit(hit=hit, sources={"semantic-scholar"}, external_ids=dict(hit.external_ids))]
        result = SnowballResult(kept=kept, rounds=[], stop_reason="walk-complete:1-hops", seed_count=1)
        out = write_corpus_raw(result, tmp_path / "_corpus_raw.md")
        text = out.read_text()
        assert RERANK_NO_SCORE in text


class TestParseCorpusRawRowsRerankRoundTrip:
    def test_parses_rerank_column_when_present(self):
        text = (
            "| Annotation | Paper-id | Title | Venue | Year | Abstract/TL;DR | Flags | Rerank |\n"
            "|---|---|---|---|---|---|---|---|\n"
            "| [NEW] | 10.1/a | A Paper | | | abstract | | 0.734 |\n"
        )
        rows = rel.parse_corpus_raw_rows(text)
        assert len(rows) == 1
        assert rows[0]["rerank"] == "0.734"

    def test_legacy_seven_column_row_still_parses_no_format_break(self):
        text = (
            "| Annotation | Paper-id | Title | Venue | Year | Abstract/TL;DR | Flags |\n"
            "|---|---|---|---|---|---|---|\n"
            "| [NEW] | 10.1/legacy | A Legacy Paper | | | abstract | |\n"
        )
        rows = rel.parse_corpus_raw_rows(text)
        assert len(rows) == 1
        assert rows[0]["paper_id"] == "10.1/legacy"
        assert rows[0]["title"] == "A Legacy Paper"
        # Absent 8th column -> honest blank, not a crash, not a fabricated score.
        assert rows[0]["rerank"] == ""

    def test_render_corpus_raw_row_round_trips_rerank(self):
        row = {
            "annotation": "[NEW]", "paper_id": "10.1/a", "title": "A Paper",
            "venue": "", "year": "", "abstract": "abstract", "flags": "",
            "rerank": "0.900",
        }
        rendered = rel._render_corpus_raw_row(row)
        parsed = rel.parse_corpus_raw_rows(
            "| Annotation | Paper-id | Title | Venue | Year | Abstract/TL;DR | Flags | Rerank |\n"
            "|---|---|---|---|---|---|---|---|\n" + rendered + "\n"
        )
        assert parsed[0]["rerank"] == "0.900"


class TestScreenCorpusRawCarriesRerank:
    def test_screen_keeps_rerank_on_in_scope_row(self, tmp_path):
        protocol = tmp_path / "_protocol.md"
        protocol.write_text(
            "---\n"
            "question: does X affect Y\n"
            "inclusion: cultural values LLM alignment\n"
            "exclusion: astronomy physics chemistry\n"
            "coverage_claim: broad\n"
            "counter-position: null hypothesis studies\n"
            "---\n",
            encoding="utf-8",
        )
        corpus_raw = tmp_path / "_corpus_raw.md"
        corpus_raw.write_text(
            "| Annotation | Paper-id | Title | Venue | Year | Abstract/TL;DR | Flags | Rerank |\n"
            "|---|---|---|---|---|---|---|---|\n"
            "| [NEW] | 10.1/llm2024 | Cross-cultural evaluation of LLM value alignment "
            "| | | We measure large language models' cultural values and social norm "
            "adherence across diverse human populations. | | 0.611 |\n",
            encoding="utf-8",
        )
        out_path = tmp_path / "_corpus_raw_screened.md"
        counts = rel.screen_corpus_raw(corpus_raw, protocol, out_path)
        assert counts["in"] == 1
        text = out_path.read_text(encoding="utf-8")
        assert "0.611" in text


class TestParseCorpusTableWithAbstractOptionalRerankColumn:
    def test_five_column_row_with_rerank_parses(self):
        text = (
            "| Annotation | Citekey | Title | Abstract | Rerank |\n|---|---|---|---|---|\n"
            "| [NEW] | smith2024 | A paper | An abstract. | 0.812 |\n"
        )
        rows = rel.parse_corpus_table_with_abstract(text)
        assert len(rows) == 1
        assert rows[0]["citekey"] == "smith2024"
        assert rows[0]["rerank"] == "0.812"

    def test_legacy_four_column_row_still_works_no_format_break(self):
        text = (
            "| Annotation | Citekey | Title | Abstract |\n|---|---|---|---|\n"
            "| [NEW] | smith2024 | A paper | An abstract. |\n"
        )
        rows = rel.parse_corpus_table_with_abstract(text)
        assert len(rows) == 1
        assert rows[0]["citekey"] == "smith2024"
        assert rows[0]["rerank"] == ""


class TestExistingCorpusReadersUnaffectedByRerankColumn:
    def test_parse_corpus_citekeys_with_rerank_column_present(self, tmp_path):
        corpus_path = tmp_path / "_corpus.md"
        corpus_path.write_text(
            "| Annotation | Citekey | Title | Abstract | Rerank |\n|---|---|---|---|---|\n"
            "| [NEW] | smith2024 | A paper | An abstract. | 0.812 |\n"
            "| [IN-CORPUS:jones2019] | jones2019 | Older paper | | — |\n",
            encoding="utf-8",
        )
        citekeys = _parse_corpus_citekeys(corpus_path)
        assert citekeys == ["smith2024", "jones2019"]

    def test_check_corpus_all_accept_tagged_with_rerank_column_present(self, tmp_path):
        corpus_path = tmp_path / "_corpus.md"
        corpus_path.write_text(
            "| Annotation | Citekey | Title | Abstract | Rerank |\n|---|---|---|---|---|\n"
            "| [NEW] | smith2024 | A paper | An abstract. | 0.812 |\n",
            encoding="utf-8",
        )
        result = check_corpus_all_accept_tagged(corpus_path)
        assert result["all_tagged"] is True
        assert result["total_rows"] == 1


class TestReviewCurateTipsInstructsRerankCarryOver:
    def test_review_curate_tips_mentions_rerank_column(self):
        from research_vault.review.style import _DEFAULT_REVIEW_TIPS

        tips = _DEFAULT_REVIEW_TIPS["review_curate_tips"]
        assert "Rerank" in tips
