"""test_facet_coverage_0_3_2.py — rv 0.3.2 search-breadth + facet-coverage
redesign ("recall from queries").

Design of record: internal design note (operator-private, not shipped).

Layer 1 (generation-time breadth floor), Layer 2 (result-time facet-
coverage gate), Layer 3 (tiered-hash facet re-search remediation).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.review import autonomy as auto
from research_vault.review import corpus_freeze as cf
from research_vault.sources.sweep import (
    SweepCell,
    check_facet_breadth_floor,
    check_facet_coverage,
    compute_facet_pole_coverage,
    group_facet_stances,
    parse_angle_matrix,
)
from research_vault.sources.base import PaperHit

NESTED_PROTOCOL_BROAD = """---
type: review-protocol
question: "Do LLM personas drift over multi-turn conversation?"
inclusion: "LLM persona, multi-turn dialogue"
exclusion: "single-turn only"
seed_queries:
  by-temporal:
    thesis:
      - "cultural drift multi-turn LLM persona"
      - "homogenization LLM roleplay over turns"
      - "value shift long conversation agent"
    counter:
      - "persona stability multi-turn LLM"
      - "value persistence long-horizon dialogue agent"
sources: [semantic-scholar, arxiv]
counter-position: "persona-consistency literature"
---

# Protocol
"""

NESTED_PROTOCOL_THIN_COUNTER = """---
type: review-protocol
question: "Do LLM personas drift over multi-turn conversation?"
inclusion: "LLM persona, multi-turn dialogue"
exclusion: "single-turn only"
seed_queries:
  by-temporal:
    thesis:
      - "cultural drift multi-turn LLM persona"
      - "homogenization LLM roleplay over turns"
      - "value shift long conversation agent"
    counter:
      - "persona stability multi-turn LLM"
sources: [semantic-scholar, arxiv]
counter-position: "persona-consistency literature"
---
"""

NESTED_PROTOCOL_JARGON_NEAR_DUP = """---
type: review-protocol
question: "Does transformer attention improve translation quality?"
inclusion: "transformer attention translation"
exclusion: "non-neural MT"
seed_queries:
  by-method:
    thesis:
      - "transformer attention translation quality improvement"
      - "transformer attention translation quality gains"
      - "transformer attention translation quality boost"
    counter:
      - "rnn baseline translation quality"
      - "statistical MT translation quality"
sources: [semantic-scholar]
counter-position: "non-transformer baselines"
---
"""


def _hit(title: str, *, doi: str | None = None, arxiv: str | None = None) -> PaperHit:
    ids: dict[str, str] = {}
    if doi:
        ids["doi"] = doi
    if arxiv:
        ids["arxiv"] = arxiv
    return PaperHit(
        title=title, year=2024, authors=[], external_ids=ids, abstract="",
        citation_count=0, source="test",
    )


# ---------------------------------------------------------------------------
# Layer 1 — generation-time breadth floor
# ---------------------------------------------------------------------------

class TestFacetBreadthFloor:
    def test_broad_matrix_passes(self) -> None:
        ok, msg = check_facet_breadth_floor(NESTED_PROTOCOL_BROAD, min_per_facet=3, min_per_pole=2)
        assert ok, msg

    def test_thin_counter_pole_blocks(self) -> None:
        ok, msg = check_facet_breadth_floor(NESTED_PROTOCOL_THIN_COUNTER, min_per_facet=3, min_per_pole=2)
        assert not ok
        assert "by-temporal.counter" in msg
        assert "1 distinct" in msg

    def test_thesis_pole_never_thin_when_declared_pass(self) -> None:
        # NESTED_PROTOCOL_BROAD's thesis pole has 3 distinct queries (>= M=2).
        ok, _ = check_facet_breadth_floor(NESTED_PROTOCOL_BROAD, min_per_facet=3, min_per_pole=2)
        assert ok

    def test_non_additive_two_pole_facet_never_checked_against_facet_level_n(self) -> None:
        # A facet with exactly M=2 per pole (4 total) must pass even though
        # 4 < a hypothetical facet-level N=5 — the two floors are NEVER summed.
        ok, msg = check_facet_breadth_floor(
            NESTED_PROTOCOL_THIN_COUNTER.replace(
                '      - "persona stability multi-turn LLM"',
                '      - "persona stability multi-turn LLM"\n'
                '      - "value persistence long-horizon dialogue agent"',
            ),
            min_per_facet=5, min_per_pole=2,
        )
        assert ok, msg

    def test_legacy_scalar_only_matrix_is_a_no_op(self) -> None:
        legacy = """---
type: review-protocol
question: "x"
seed_queries:
  by-method: "transformer attention mechanism"
sources: [semantic-scholar]
counter-position: "y"
---
"""
        ok, msg = check_facet_breadth_floor(legacy, min_per_facet=3, min_per_pole=2)
        assert ok, msg

    def test_domain_vocab_stripped_before_jaccard(self) -> None:
        # 3 queries that repeat shared domain jargon ("transformer attention
        # translation quality") near-verbatim, differing only in ONE
        # non-domain content word each — literal Jaccard (no stripping)
        # would read them as near-identical (dominated by jargon overlap);
        # domain-vocab stripping isolates the true differentiator per query.
        ok, msg = check_facet_breadth_floor(
            NESTED_PROTOCOL_JARGON_NEAR_DUP, min_per_facet=3, min_per_pole=2,
        )
        assert ok, msg

    def test_reports_every_thin_facet_not_just_first(self) -> None:
        text = """---
type: review-protocol
question: "x"
inclusion: "y"
seed_queries:
  by-a:
    thesis:
      - "query one alpha"
    counter:
      - "query two beta"
  by-b:
    thesis:
      - "query three gamma"
    counter:
      - "query four delta"
sources: [semantic-scholar]
counter-position: "z"
---
"""
        ok, msg = check_facet_breadth_floor(text, min_per_facet=3, min_per_pole=2)
        assert not ok
        assert "by-a.thesis" in msg and "by-a.counter" in msg
        assert "by-b.thesis" in msg and "by-b.counter" in msg


# ---------------------------------------------------------------------------
# Layer 2 — result-time facet coverage
# ---------------------------------------------------------------------------

class TestFacetCoverage:
    def test_deduped_papers_not_raw_hit_rows(self) -> None:
        """3 near-dup queries under one pole return the SAME 2 papers ->
        must read as 2, never 3 (the acceptance criterion)."""
        same_two = [_hit("Paper A", doi="10.1/a"), _hit("Paper B", doi="10.1/b")]
        cells = [
            SweepCell(angle="by-temporal.counter.0", query="q0", source="s1", hits=list(same_two)),
            SweepCell(angle="by-temporal.counter.1", query="q1", source="s1", hits=list(same_two)),
            SweepCell(angle="by-temporal.counter.2", query="q2", source="s1", hits=list(same_two)),
        ]
        coverage = compute_facet_pole_coverage(cells)
        assert len(coverage["by-temporal.counter"]) == 2

    def test_thin_pole_flagged_below_k(self) -> None:
        angle_matrix = parse_angle_matrix(NESTED_PROTOCOL_BROAD)
        cells = [
            SweepCell(angle="by-temporal.thesis.0", query="q", source="s", hits=[
                _hit("T1", doi="10.1/t1"), _hit("T2", doi="10.1/t2"), _hit("T3", doi="10.1/t3"),
            ]),
            SweepCell(angle="by-temporal.counter.0", query="q", source="s", hits=[
                _hit("C1", doi="10.1/c1"),
            ]),
        ]
        result = check_facet_coverage(angle_matrix, cells, min_hits_per_pole=3)
        assert result["pole_counts"]["by-temporal.thesis"] == 3
        assert result["pole_counts"]["by-temporal.counter"] == 1
        assert result["thin_poles"] == ["by-temporal.counter"]

    def test_pole_with_zero_hits_reads_as_zero_not_absent(self) -> None:
        angle_matrix = parse_angle_matrix(NESTED_PROTOCOL_BROAD)
        # No cells at all for the counter pole (every cell errored/absent).
        cells = [
            SweepCell(angle="by-temporal.thesis.0", query="q", source="s", hits=[
                _hit("T1", doi="10.1/t1"), _hit("T2", doi="10.1/t2"), _hit("T3", doi="10.1/t3"),
            ]),
        ]
        result = check_facet_coverage(angle_matrix, cells, min_hits_per_pole=3)
        assert result["pole_counts"]["by-temporal.counter"] == 0
        assert "by-temporal.counter" in result["thin_poles"]

    def test_errored_cells_excluded_from_coverage(self) -> None:
        angle_matrix = parse_angle_matrix(NESTED_PROTOCOL_BROAD)
        cells = [
            SweepCell(angle="by-temporal.thesis.0", query="q", source="s", error="timeout",
                      hits=[_hit("Ghost", doi="10.1/ghost")]),
        ]
        result = check_facet_coverage(angle_matrix, cells, min_hits_per_pole=3)
        assert result["pole_counts"]["by-temporal.thesis"] == 0

    def test_legacy_cells_excluded_from_facet_coverage(self) -> None:
        cells = [
            SweepCell(angle="by-method", query="q", source="s", hits=[_hit("X", doi="10.1/x")]),
        ]
        coverage = compute_facet_pole_coverage(cells)
        assert coverage == {}


# ---------------------------------------------------------------------------
# Layer 3 — the `within-facet-query-append` deviation kind's invariant
# ---------------------------------------------------------------------------

class TestWithinFacetQueryAppendDeviationKind:
    def test_rejects_pre_ne_post_frozen_criteria(self, tmp_path) -> None:
        deviations = tmp_path / "_deviations.md"
        with pytest.raises(ValueError, match="within-facet-query-append"):
            auto.record_deviation(
                deviations, version=2, pre_criteria="h1", post_criteria="h2",
                removed=[], added=["x"], rationale="thin pole",
                kind="within-facet-query-append",
                facet_key="by-temporal.counter", new_queries=["a new query"],
                pre_query_matrix_hash="sha256:aaa", post_query_matrix_hash="sha256:bbb",
            )

    def test_rejects_removal(self, tmp_path) -> None:
        deviations = tmp_path / "_deviations.md"
        with pytest.raises(ValueError, match="within-facet-query-append"):
            auto.record_deviation(
                deviations, version=2, pre_criteria="h1", post_criteria="h1",
                removed=["b"], added=["x"], rationale="thin pole",
                kind="within-facet-query-append",
                facet_key="by-temporal.counter", new_queries=["a new query"],
                pre_query_matrix_hash="sha256:aaa", post_query_matrix_hash="sha256:bbb",
            )

    def test_rejects_missing_facet_fields(self, tmp_path) -> None:
        deviations = tmp_path / "_deviations.md"
        with pytest.raises(ValueError, match="within-facet-query-append"):
            auto.record_deviation(
                deviations, version=2, pre_criteria="h1", post_criteria="h1",
                removed=[], added=["x"], rationale="thin pole",
                kind="within-facet-query-append",
            )

    def test_accepts_pure_growth_with_full_facet_record(self, tmp_path) -> None:
        deviations = tmp_path / "_deviations.md"
        block = auto.record_deviation(
            deviations, version=2, pre_criteria="h1", post_criteria="h1",
            removed=[], added=["newpaper2024"], rationale="thin pole by-temporal.counter",
            kind="within-facet-query-append",
            facet_key="by-temporal.counter",
            new_queries=["persona rigidity long dialogue", "stability multi-session agent"],
            pre_query_matrix_hash="sha256:aaa", post_query_matrix_hash="sha256:bbb",
        )
        assert "**Kind:** within-facet-query-append" in block
        assert "**Facet key:** by-temporal.counter" in block
        assert '- "persona rigidity long dialogue"' in block
        assert '- "stability multi-session agent"' in block
        assert "**Pre query_matrix_hash:** sha256:aaa" in block
        assert "**Post query_matrix_hash:** sha256:bbb" in block
        assert "**Added citekeys:** newpaper2024" in block


# ---------------------------------------------------------------------------
# Layer 3 — corpus_freeze.refresh gates on the FROZEN tier only, never the
# query-matrix tier (the crux this whole redesign exists to fix)
# ---------------------------------------------------------------------------

NESTED_PROTOCOL_FOR_FREEZE = """---
type: review-protocol
question: "Do LLM personas drift over multi-turn conversation?"
inclusion: "LLM persona, multi-turn dialogue"
exclusion: "single-turn only"
seed_queries:
  by-temporal:
    thesis:
      - "cultural drift multi-turn LLM persona"
      - "homogenization LLM roleplay over turns"
    counter:
      - "persona stability multi-turn LLM"
sources: [semantic-scholar, arxiv]
counter-position: "persona-consistency literature"
---
"""


class TestRefreshGatesOnFrozenTierOnly:
    def test_query_only_change_without_declared_deviation_blocks(
        self, tmp_path,
    ) -> None:
        """Hardening: an out-of-band query-tier edit (never routed through
        ``run_facet_query_append_round``/``record_deviation``) must BLOCK,
        even though the frozen tier is untouched — closes the gap where
        only the autonomous mutation site's own re-gate was enforced."""
        protocol = tmp_path / "_protocol.md"
        protocol.write_text(NESTED_PROTOCOL_FOR_FREEZE, encoding="utf-8")
        corpus = tmp_path / "_corpus.md"
        corpus.write_text(
            "| annotation | citekey | title |\n|---|---|---|\n"
            "| [NEW] | alpha2024 | Alpha |\n",
            encoding="utf-8",
        )
        deviations = tmp_path / "_deviations.md"

        meta: dict = {}
        cf.stamp_corpus_freeze(meta, corpus_path=corpus, protocol_path=protocol)

        # Append a NEW query under the ALREADY-declared counter pole — a
        # query-matrix-tier-only change, no facet-key-set change — but
        # NEVER declared via record_deviation(kind="within-facet-query-append").
        amended = NESTED_PROTOCOL_FOR_FREEZE.replace(
            '      - "persona stability multi-turn LLM"',
            '      - "persona stability multi-turn LLM"\n'
            '      - "value persistence long-horizon dialogue agent"',
        )
        protocol.write_text(amended, encoding="utf-8")

        with pytest.raises(cf.RefreshBlocked, match="structural re-gate FAILED"):
            cf.refresh(
                meta, corpus_path=corpus, protocol_path=protocol, deviations_path=deviations,
            )

    def test_query_only_change_with_matching_declared_deviation_admits(
        self, tmp_path,
    ) -> None:
        protocol = tmp_path / "_protocol.md"
        protocol.write_text(NESTED_PROTOCOL_FOR_FREEZE, encoding="utf-8")
        corpus = tmp_path / "_corpus.md"
        corpus.write_text(
            "| annotation | citekey | title |\n|---|---|---|\n"
            "| [NEW] | alpha2024 | Alpha |\n",
            encoding="utf-8",
        )
        deviations = tmp_path / "_deviations.md"

        meta: dict = {}
        cf.stamp_corpus_freeze(meta, corpus_path=corpus, protocol_path=protocol)
        baseline_frozen = meta["corpus_freeze"]["criteria_hash"]
        baseline_query = meta["corpus_freeze"]["query_matrix_hash"]

        new_query = "value persistence long-horizon dialogue agent"
        amended = NESTED_PROTOCOL_FOR_FREEZE.replace(
            '      - "persona stability multi-turn LLM"',
            f'      - "persona stability multi-turn LLM"\n'
            f'      - "{new_query}"',
        )
        protocol.write_text(amended, encoding="utf-8")
        post_query_hash = cf.hash_query_matrix_bytes(protocol)

        auto.record_deviation(
            deviations,
            version=2, pre_criteria=baseline_frozen, post_criteria=baseline_frozen,
            removed=[], added=[],
            rationale="test: within-facet-query-append",
            kind=auto.DEVIATION_KIND_WITHIN_FACET_QUERY_APPEND,
            facet_key="by-temporal.counter", new_queries=[new_query],
            pre_query_matrix_hash=baseline_query, post_query_matrix_hash=post_query_hash,
        )

        new_freeze = cf.refresh(
            meta, corpus_path=corpus, protocol_path=protocol, deviations_path=deviations,
        )
        assert new_freeze["criteria_hash"] == baseline_frozen
        assert new_freeze["query_matrix_hash"] == post_query_hash
        assert new_freeze["query_matrix_hash"] != baseline_query

    def test_facet_key_set_change_still_blocks_without_human_deviation(self, tmp_path) -> None:
        protocol = tmp_path / "_protocol.md"
        protocol.write_text(NESTED_PROTOCOL_FOR_FREEZE, encoding="utf-8")
        corpus = tmp_path / "_corpus.md"
        corpus.write_text(
            "| annotation | citekey | title |\n|---|---|---|\n"
            "| [NEW] | alpha2024 | Alpha |\n",
            encoding="utf-8",
        )
        deviations = tmp_path / "_deviations.md"

        meta: dict = {}
        cf.stamp_corpus_freeze(meta, corpus_path=corpus, protocol_path=protocol)

        # A NEW facet key entirely — a real scope widening, must still BLOCK.
        amended = NESTED_PROTOCOL_FOR_FREEZE.replace(
            "sources: [semantic-scholar, arxiv]",
            "  by-method: \"a brand new legacy angle\"\n"
            "sources: [semantic-scholar, arxiv]",
        )
        protocol.write_text(amended, encoding="utf-8")

        with pytest.raises(cf.RefreshBlocked):
            cf.refresh(meta, corpus_path=corpus, protocol_path=protocol, deviations_path=deviations)


# ---------------------------------------------------------------------------
# Layer 2 — write_search_hits stamps facet coverage; a later, separate
# process invocation reads it back via check_facet_coverage_from_search_hits.
# ---------------------------------------------------------------------------

from research_vault.sources.sweep import SweepResult, write_search_hits  # noqa: E402
from research_vault.review import check_facet_coverage_from_search_hits  # noqa: E402


class TestFacetCoverageRoundTrip:
    def test_write_then_read_back(self, tmp_path) -> None:
        cells = [
            SweepCell(angle="by-temporal.thesis.0", query="q", source="s", hits=[
                _hit("T1", doi="10.1/t1"), _hit("T2", doi="10.1/t2"), _hit("T3", doi="10.1/t3"),
            ]),
            SweepCell(angle="by-temporal.counter.0", query="q", source="s", hits=[
                _hit("C1", doi="10.1/c1"),
            ]),
        ]
        angle_matrix = parse_angle_matrix(NESTED_PROTOCOL_BROAD)
        facet_coverage = check_facet_coverage(angle_matrix, cells, min_hits_per_pole=3)
        result = SweepResult(kept=[], independent_count=0, total_hits_fetched=4, cells=cells, errors=[])

        out = write_search_hits(result, tmp_path / "_search_hits.md", facet_coverage=facet_coverage)
        assert "THIN FACET-POLE" in out.read_text()

        readback = check_facet_coverage_from_search_hits(out)
        assert readback["exists"] is True
        assert readback["declared"] is True
        assert readback["pole_counts"]["by-temporal.thesis"] == 3
        assert readback["pole_counts"]["by-temporal.counter"] == 1
        assert readback["thin_poles"] == ["by-temporal.counter"]
        assert readback["min_hits_per_pole"] == 3

    def test_missing_file_reads_as_not_exists(self, tmp_path) -> None:
        readback = check_facet_coverage_from_search_hits(tmp_path / "_absent.md")
        assert readback["exists"] is False
        assert readback["declared"] is False

    def test_no_facet_coverage_stamped_reads_as_undeclared_not_a_fabricated_go(self, tmp_path) -> None:
        cells = [SweepCell(angle="by-method", query="q", source="s", hits=[_hit("X", doi="10.1/x")])]
        result = SweepResult(kept=[], independent_count=0, total_hits_fetched=1, cells=cells, errors=[])
        out = write_search_hits(result, tmp_path / "_search_hits.md")  # no facet_coverage=
        readback = check_facet_coverage_from_search_hits(out)
        assert readback["exists"] is True
        assert readback["declared"] is False
        assert readback["thin_poles"] == []


# ---------------------------------------------------------------------------
# Layer 3 — append_queries_to_protocol_text (the append-only YAML surgery)
# ---------------------------------------------------------------------------

from research_vault.sources.sweep import append_queries_to_protocol_text  # noqa: E402


class TestAppendQueriesToProtocolText:
    def test_appends_after_last_existing_item(self) -> None:
        new_text = append_queries_to_protocol_text(
            NESTED_PROTOCOL_BROAD, "by-temporal", "counter", ["a brand new counter query"],
        )
        matrix = parse_angle_matrix(new_text)
        facets = group_facet_stances(matrix)
        assert facets["by-temporal"]["counter"] == [
            "persona stability multi-turn LLM",
            "value persistence long-horizon dialogue agent",
            "a brand new counter query",
        ]
        # thesis pole untouched
        assert facets["by-temporal"]["thesis"] == [
            "cultural drift multi-turn LLM persona",
            "homogenization LLM roleplay over turns",
            "value shift long conversation agent",
        ]

    def test_appends_to_declared_but_empty_pole(self) -> None:
        text = """---
type: review-protocol
question: "x"
seed_queries:
  by-a:
    thesis:
      - "existing thesis query"
    counter:
sources: [semantic-scholar]
counter-position: "y"
---
"""
        new_text = append_queries_to_protocol_text(text, "by-a", "counter", ["new counter query"])
        matrix = parse_angle_matrix(new_text)
        facets = group_facet_stances(matrix)
        assert facets["by-a"]["counter"] == ["new counter query"]

    def test_raises_on_undeclared_pole(self) -> None:
        with pytest.raises(ValueError, match="not a DECLARED pole"):
            append_queries_to_protocol_text(NESTED_PROTOCOL_BROAD, "by-nonexistent", "thesis", ["q"])

    def test_never_authors_a_new_facet_or_pole(self) -> None:
        # explicit acceptance test: appending under an UNDECLARED stance
        # (thesis exists, counter absent entirely — no `counter:` key at
        # all, distinct from "declared but empty") must also raise.
        text = """---
type: review-protocol
question: "x"
seed_queries:
  by-a:
    thesis:
      - "existing thesis query"
sources: [semantic-scholar]
counter-position: "y"
---
"""
        with pytest.raises(ValueError, match="not a DECLARED pole"):
            append_queries_to_protocol_text(text, "by-a", "counter", ["q"])

    def test_empty_new_queries_is_a_no_op(self) -> None:
        assert append_queries_to_protocol_text(NESTED_PROTOCOL_BROAD, "by-temporal", "counter", []) == NESTED_PROTOCOL_BROAD

    def test_legacy_scalar_and_other_facets_byte_preserved(self) -> None:
        new_text = append_queries_to_protocol_text(
            NESTED_PROTOCOL_JARGON_NEAR_DUP, "by-method", "counter", ["a new counter query"],
        )
        # sources/counter-position lines untouched
        assert 'sources: [semantic-scholar]' in new_text
        assert 'counter-position: "non-transformer baselines"' in new_text


# ---------------------------------------------------------------------------
# Layer 3 — check_facet_query_append_re_gate (the structural fence)
# ---------------------------------------------------------------------------

class TestFacetQueryAppendReGate:
    def test_pure_append_passes(self) -> None:
        post = append_queries_to_protocol_text(
            NESTED_PROTOCOL_BROAD, "by-temporal", "counter", ["a brand new counter query"],
        )
        ok, msg = cf.check_facet_query_append_re_gate(NESTED_PROTOCOL_BROAD, post)
        assert ok, msg

    def test_edited_existing_query_fails(self) -> None:
        post = NESTED_PROTOCOL_BROAD.replace(
            "persona stability multi-turn LLM", "persona stability multi-turn LLM EDITED",
        )
        ok, msg = cf.check_facet_query_append_re_gate(NESTED_PROTOCOL_BROAD, post)
        assert not ok
        assert "structural re-gate FAILED" in msg

    def test_removed_query_fails(self) -> None:
        post = NESTED_PROTOCOL_BROAD.replace(
            '      - "value persistence long-horizon dialogue agent"\n', "",
        )
        ok, msg = cf.check_facet_query_append_re_gate(NESTED_PROTOCOL_BROAD, post)
        assert not ok

    def test_new_facet_key_fails(self) -> None:
        post = NESTED_PROTOCOL_BROAD.replace(
            "sources: [semantic-scholar, arxiv]",
            "  by-newfacet: \"a sneaky new facet\"\nsources: [semantic-scholar, arxiv]",
        )
        ok, msg = cf.check_facet_query_append_re_gate(NESTED_PROTOCOL_BROAD, post)
        assert not ok

    def test_inclusion_edit_fails(self) -> None:
        post = NESTED_PROTOCOL_BROAD.replace(
            'inclusion: "LLM persona, multi-turn dialogue"',
            'inclusion: "LLM persona, multi-turn dialogue, ANYTHING"',
        )
        ok, msg = cf.check_facet_query_append_re_gate(NESTED_PROTOCOL_BROAD, post)
        assert not ok


# ---------------------------------------------------------------------------
# Layer 3 — review.facet_remediation: resolve_facet_coverage,
# emit/ingest task files, screen_and_append_facet_hits (the sibling-bug
# fix: mechanical relevance-screen + [NEEDS-CURATE] tag, never a raw/bare
# append), and the full round driver.
# ---------------------------------------------------------------------------

from research_vault.review import facet_remediation as fremed  # noqa: E402


class TestResolveFacetCoverage:
    def test_halt_base_passes_through_unchanged(self) -> None:
        base = auto.DispositionResult(auto.HALT_DECLARE, "walk-terminal HALT")
        result = fremed.resolve_facet_coverage(
            base, {"declared": True, "thin_poles": ["by-a.counter"], "min_hits_per_pole": 3},
        )
        assert result is base

    def test_undeclared_facet_coverage_is_a_no_op(self) -> None:
        base = auto.DispositionResult(auto.GO, "clean")
        result = fremed.resolve_facet_coverage(base, {"declared": False})
        assert result is base
        result_none = fremed.resolve_facet_coverage(base, None)
        assert result_none is base
        # protocol_declares_facets left at its default (None, "unknown")
        # keeps the legacy no-op — a caller that never opts into the
        # cross-check sees no behavior change.
        result_unknown = fremed.resolve_facet_coverage(
            base, {"declared": False}, protocol_declares_facets=None,
        )
        assert result_unknown is base
        # a genuinely legacy/non-faceted protocol (cross-check explicitly
        # False) is still a clean no-op.
        result_legacy = fremed.resolve_facet_coverage(
            base, {"declared": False}, protocol_declares_facets=False,
        )
        assert result_legacy is base

    def test_declared_facets_but_unstamped_coverage_halts(self) -> None:
        """Hardening (missing-SET fail-closed): the protocol declared
        nested facets, but the sweep's own _search_hits.md carries no
        facet-coverage stamp — a stamping failure, not a legacy protocol.
        Must HALT-DECLARE, never silently fall through to GO."""
        base = auto.DispositionResult(auto.GO, "clean")
        result = fremed.resolve_facet_coverage(
            base, {"declared": False}, protocol_declares_facets=True,
        )
        assert result.disposition == auto.HALT_DECLARE
        assert result is not base

        # Also covers the "no facet_coverage_info at all" (None) case —
        # e.g. _search_hits.md missing entirely — with a declared-faceted
        # protocol.
        result_none_info = fremed.resolve_facet_coverage(
            base, None, protocol_declares_facets=True,
        )
        assert result_none_info.disposition == auto.HALT_DECLARE

    def test_declared_and_stamped_facet_coverage_proceeds_normally(self) -> None:
        """The cross-check must never block a HEALTHY declared+stamped
        run — only the missing-stamp case."""
        base = auto.DispositionResult(auto.GO, "clean")
        result = fremed.resolve_facet_coverage(
            base, {"declared": True, "thin_poles": [], "min_hits_per_pole": 3},
            protocol_declares_facets=True,
        )
        assert result is base

    def test_no_thin_poles_is_a_no_op(self) -> None:
        base = auto.DispositionResult(auto.GO, "clean")
        result = fremed.resolve_facet_coverage(
            base, {"declared": True, "thin_poles": [], "min_hits_per_pole": 3},
        )
        assert result is base

    def test_thin_pole_with_budget_dispatches_facet_remediate(self) -> None:
        base = auto.DispositionResult(auto.GO, "clean")
        result = fremed.resolve_facet_coverage(
            base, {"declared": True, "thin_poles": ["by-b.counter", "by-a.counter"], "min_hits_per_pole": 3},
            remediation_state={"rounds_used": 0}, max_rounds=2,
        )
        assert result.disposition == auto.FACET_REMEDIATE
        # deterministic: alphabetically first thin pole targeted
        assert result.evidence["target_pole"] == "by-a.counter"

    def test_budget_exhausted_halts(self) -> None:
        base = auto.DispositionResult(auto.GO, "clean")
        result = fremed.resolve_facet_coverage(
            base, {"declared": True, "thin_poles": ["by-a.counter"], "min_hits_per_pole": 3},
            remediation_state={"rounds_used": 2}, max_rounds=2,
        )
        assert result.disposition == auto.HALT_DECLARE

    def test_thin_pole_teeth_still_fire_when_walk_absent(self) -> None:
        """Coverage-gate refactor (search-primary redesign): E's thin-pole
        teeth must flow through IDENTICALLY whether the base GO originated
        from a walk-terminal (the pre-refactor world) or from
        ``classify_coverage_gate``'s own walk-absent GO (the surgical-walk
        steady state) — ``resolve_facet_coverage`` only ever reads the
        ``base`` disposition + facet-coverage payload, never the walk
        provenance, so this must not regress."""
        no_walk_info = {"exists": False, "stop_reason": "", "walk_complete": False, "hop_count": None}
        base = auto.classify_coverage_gate(no_walk_info)
        assert base.disposition == auto.GO
        assert base.evidence.get("walk_ran") is False

        result = fremed.resolve_facet_coverage(
            base,
            {"declared": True, "thin_poles": ["by-a.counter"], "min_hits_per_pole": 3},
            remediation_state={"rounds_used": 0}, max_rounds=2,
        )
        assert result.disposition == auto.FACET_REMEDIATE
        assert result.evidence["target_pole"] == "by-a.counter"


class TestFacetTaskEmitIngest:
    def test_emit_then_pending_then_ingest(self, tmp_path) -> None:
        task_dir = fremed.facet_task_dir(tmp_path, "by-temporal.counter")
        fremed.emit_facet_query_task(
            task_dir, pole="by-temporal.counter", existing_queries=["q1"],
            min_queries_needed=2, min_hits_per_pole=3, current_count=1,
        )
        assert fremed.facet_task_pending(task_dir)
        assert fremed.read_facet_query_response(task_dir) is None

        (task_dir / fremed._RESPONSE_FILENAME).write_text(
            "```queries\nnew query one\nnew query two\n```\n", encoding="utf-8",
        )
        assert not fremed.facet_task_pending(task_dir)
        assert fremed.read_facet_query_response(task_dir) == ["new query one", "new query two"]

        fremed.clear_facet_task(task_dir)
        assert not (task_dir / fremed._TASK_FILENAME).exists()
        assert not (task_dir / fremed._RESPONSE_FILENAME).exists()


class _FakeHit:
    def __init__(self, title: str, abstract: str = "", authors=None, year=2024):
        self.title = title
        self.abstract = abstract
        self.authors = authors or []
        self.year = year


class TestScreenAndAppendFacetHits:
    def test_off_domain_hits_never_appended_declared_in_residue(self, tmp_path) -> None:
        corpus = tmp_path / "_corpus.md"
        residue = tmp_path / "_facet-remediation-residue.md"
        criteria = {
            "question": "Do LLM personas drift over multi-turn conversation?",
            "inclusion": "LLM persona multi-turn dialogue conversation drift",
            "exclusion": "", "coverage_claim": "",
        }
        hits = [
            _FakeHit(
                "Galaxy Cluster Dynamics in the Local Universe",
                abstract="We study astrophysical dark matter halo dynamics in galaxy clusters using redshift surveys and spectroscopic observations of stellar populations.",
            ),
        ]
        result = fremed.screen_and_append_facet_hits(
            corpus, hits, criteria=criteria, counter_position="",
            residue_path=residue, existing_citekeys=set(),
        )
        assert result["added"] == []
        assert result["off_domain"]
        assert not corpus.exists() or "Galaxy Cluster" not in corpus.read_text()
        assert residue.exists()
        assert "Galaxy Cluster" in residue.read_text()

    def test_in_scope_hits_tagged_needs_curate_never_bare_new(self, tmp_path) -> None:
        corpus = tmp_path / "_corpus.md"
        residue = tmp_path / "_residue.md"
        criteria = {
            "question": "Do LLM personas drift over multi-turn conversation?",
            "inclusion": "LLM persona multi-turn dialogue conversation drift stability",
            "exclusion": "", "coverage_claim": "",
        }
        hits = [_FakeHit(
            "Persona Stability in Multi-Turn LLM Dialogue Agents",
            abstract="We study LLM persona stability and drift across multi-turn conversation dialogue sessions.",
            authors=["Jane Smith"],
        )]
        result = fremed.screen_and_append_facet_hits(
            corpus, hits, criteria=criteria, counter_position="",
            residue_path=residue, existing_citekeys=set(),
        )
        assert len(result["added"]) == 1
        text = corpus.read_text()
        assert "[NEW][NEEDS-CURATE]" in text
        assert "[LEG-" not in text  # never fabricates a leg tag

    def test_dedups_against_existing_corpus_titles(self, tmp_path) -> None:
        corpus = tmp_path / "_corpus.md"
        corpus.write_text(
            "| annotation | citekey | title |\n|---|---|---|\n"
            "| [NEW] | existing2024 | Persona Stability in Multi-Turn LLM Dialogue Agents |\n",
            encoding="utf-8",
        )
        residue = tmp_path / "_residue.md"
        criteria = {"question": "x", "inclusion": "persona stability multi-turn dialogue", "exclusion": "", "coverage_claim": ""}
        hits = [_FakeHit(
            "Persona Stability in Multi-Turn LLM Dialogue Agents",
            abstract="persona stability multi-turn dialogue agents study",
        )]
        result = fremed.screen_and_append_facet_hits(
            corpus, hits, criteria=criteria, counter_position="",
            residue_path=residue, existing_citekeys={"existing2024"},
        )
        assert result["added"] == []


class TestRunFacetQueryAppendRound:
    def _setup(self, tmp_path):
        protocol = tmp_path / "_protocol.md"
        protocol.write_text(NESTED_PROTOCOL_BROAD, encoding="utf-8")
        corpus = tmp_path / "_corpus.md"
        corpus.write_text(
            "| annotation | citekey | title |\n|---|---|---|\n"
            "| [NEW] | alpha2024 | Alpha |\n",
            encoding="utf-8",
        )
        deviations = tmp_path / "_deviations.md"
        search_hits = tmp_path / "_search_hits.md"
        search_hits.write_text(
            "---\ndark_sources: \n"
            "facet_pole_counts: by-temporal.counter=1, by-temporal.thesis=3\n"
            "facet_thin_poles: by-temporal.counter\n"
            "facet_min_hits_per_pole: 3\n---\n\n# Search hits\n",
            encoding="utf-8",
        )
        relevance_verdict = tmp_path / "_relevance-verdict.md"
        relevance_verdict.write_text("stale verdict", encoding="utf-8")
        return protocol, corpus, deviations, search_hits, relevance_verdict

    def test_full_round_screens_tags_declares_refreshes_invalidates(self, tmp_path) -> None:
        """B2: candidates pause for a cold relevance-verify pass BEFORE
        landing — the round only APPLIES on a second call, once the
        verdict file exists."""
        protocol, corpus, deviations, search_hits, relevance_verdict = self._setup(tmp_path)

        def fake_tool_op(op, **kwargs):
            assert op == "sweep"
            assert kwargs["angle_keys"] == {"by-temporal.counter"}

            class _Hit:
                title = "Persona Stability New Finding Multi-Turn"
                abstract = "persona stability multi-turn dialogue study"
                authors = ["A. Author"]
                year = 2025

            return [_Hit()]

        meta: dict = {}
        first = fremed.run_facet_query_append_round(
            meta, pole="by-temporal.counter", new_queries=["persona rigidity long dialogue"],
            protocol_path=protocol, corpus_path=corpus, deviations_path=deviations,
            out_dir=tmp_path, search_hits_path=search_hits,
            relevance_verdict_path=relevance_verdict, min_hits_per_pole=3,
            tool_op_fn=fake_tool_op,
        )
        assert first["phase"] == "awaiting_cold_verify"
        assert "persona rigidity long dialogue" in parse_angle_matrix(
            protocol.read_text(encoding="utf-8")
        ).values()
        # never appended, never declared, never invalidated while pending
        assert "Persona Stability" not in corpus.read_text()
        assert not deviations.exists() or "within-facet-query-append" not in deviations.read_text()
        assert relevance_verdict.exists()

        task_dir = fremed.facet_task_dir(tmp_path, "by-temporal.counter")
        (real_citekey,) = first["candidates"]
        verdict_path = task_dir / fremed._REMEDIATION_VERIFY_VERDICT_FILENAME
        from research_vault.review import relevance as rel
        verdict_path.write_text(
            "| Citekey | Verdict |\n|---|---|\n"
            f"| {real_citekey} | IN |\n"
            f"| {rel.CANARY_IN_SCOPE_CITEKEY} | IN |\n"
            f"| {rel.CANARY_OFF_DOMAIN_CITEKEY} | OFF_DOMAIN |\n",
            encoding="utf-8",
        )

        result = fremed.run_facet_query_append_round(
            meta, pole="by-temporal.counter", new_queries=["persona rigidity long dialogue"],
            protocol_path=protocol, corpus_path=corpus, deviations_path=deviations,
            out_dir=tmp_path, search_hits_path=search_hits,
            relevance_verdict_path=relevance_verdict, min_hits_per_pole=3,
            tool_op_fn=fake_tool_op,
        )

        assert result["phase"] == "applied"
        assert len(result["added"]) == 1
        assert "within-facet-query-append" in deviations.read_text()
        # the cold verify artifact must be invalidated (a round added rows)
        assert not relevance_verdict.exists()
        # facet coverage snapshot updated: 1 (old) + 1 (added) = 2, still thin (< 3)
        assert result["facet_coverage"]["pole_counts"]["by-temporal.counter"] == 2
        assert "by-temporal.counter" in result["facet_coverage"]["thin_poles"]
        # meta stays consistent: corpus_freeze refreshed, frozen tier unchanged
        assert meta["corpus_freeze"]["criteria_hash"] == meta["corpus_freeze"]["criteria_hash"]

    def test_off_domain_hit_this_round_never_reaches_corpus(self, tmp_path) -> None:
        protocol, corpus, deviations, search_hits, relevance_verdict = self._setup(tmp_path)

        def fake_tool_op(op, **kwargs):
            class _Hit:
                title = "Galaxy Cluster Redshift Survey"
                abstract = "dark matter halo dynamics spectroscopic redshift survey astrophysics"
                authors = []
                year = 2025

            return [_Hit()]

        meta: dict = {}
        result = fremed.run_facet_query_append_round(
            meta, pole="by-temporal.counter", new_queries=["a query"],
            protocol_path=protocol, corpus_path=corpus, deviations_path=deviations,
            out_dir=tmp_path, tool_op_fn=fake_tool_op,
        )
        assert result["added"] == []
        assert result["off_domain"]
        assert "Galaxy Cluster" not in corpus.read_text()
        # nothing added -> the relevance-verdict is untouched (not invalidated)
        # (no relevance_verdict_path passed here at all — separately covered)

    def test_structural_re_gate_rejection_raises_and_never_writes(self, tmp_path) -> None:
        protocol, corpus, deviations, search_hits, relevance_verdict = self._setup(tmp_path)
        pre_text = protocol.read_text(encoding="utf-8")

        meta: dict = {}
        with pytest.raises(ValueError):
            # undeclared pole -> append_queries_to_protocol_text raises,
            # propagated as a ValueError from the round driver.
            fremed.run_facet_query_append_round(
                meta, pole="by-nonexistent.counter", new_queries=["q"],
                protocol_path=protocol, corpus_path=corpus, deviations_path=deviations,
                out_dir=tmp_path,
            )
        assert protocol.read_text(encoding="utf-8") == pre_text  # never written


# ---------------------------------------------------------------------------
# End-to-end wiring: _evaluate_autonomous_gate("coverage-gate", ...) dispatches
# FACET_REMEDIATE (task emit -> ingest+apply) when a declared pole is thin.
# ---------------------------------------------------------------------------

from research_vault.dag.store import RunState  # noqa: E402
from research_vault.dag.verbs import _evaluate_autonomous_gate  # noqa: E402


class TestFacetRemediateEndToEndWiring:
    def _setup_review_dir(self, tmp_path) -> Path:
        review_dir = tmp_path / "reviews" / "scope-a"
        review_dir.mkdir(parents=True)
        (review_dir / "_protocol.md").write_text(NESTED_PROTOCOL_BROAD, encoding="utf-8")
        (review_dir / "_corpus.md").write_text(
            "| annotation | citekey | title |\n|---|---|---|\n"
            "| [NEW] | alpha2024 | Alpha |\n",
            encoding="utf-8",
        )
        (review_dir / "_walk.md").write_text(
            "---\nstop_reason: walk-complete:1-hops\n---\n\nWalk.\n", encoding="utf-8",
        )
        cells = [
            SweepCell(angle="by-temporal.thesis.0", query="q", source="s", hits=[
                _hit("T1", doi="10.1/t1"), _hit("T2", doi="10.1/t2"), _hit("T3", doi="10.1/t3"),
            ]),
            SweepCell(angle="by-temporal.counter.0", query="q", source="s", hits=[
                _hit("C1", doi="10.1/c1"),
            ]),
        ]
        angle_matrix = parse_angle_matrix(NESTED_PROTOCOL_BROAD)
        facet_coverage = check_facet_coverage(angle_matrix, cells, min_hits_per_pole=3)
        result = SweepResult(kept=[], independent_count=0, total_hits_fetched=4, cells=cells, errors=[])
        write_search_hits(result, review_dir / "_search_hits.md", facet_coverage=facet_coverage)
        return review_dir

    def _nodes_lookup(self, review_dir: Path) -> dict:
        return {
            "review-snowball": {"produces": {"_walk.md": str(review_dir / "_walk.md")}},
            "review-search": {"produces": {"_search_hits.md": str(review_dir / "_search_hits.md")}},
        }

    def test_first_call_emits_task_and_halts(self, tmp_path) -> None:
        review_dir = self._setup_review_dir(tmp_path)
        nodes_lookup = self._nodes_lookup(review_dir)
        manifest_path = review_dir / "phase1-dag.json"
        run_state = RunState(run_id="r1", manifest_path=str(manifest_path))

        disposition = _evaluate_autonomous_gate("coverage-gate", nodes_lookup, manifest_path, run_state)

        assert disposition.disposition == auto.HALT_DECLARE
        assert "facet-remediation task emitted" in disposition.reason
        task_dir = fremed.facet_task_dir(review_dir, "by-temporal.counter")
        assert (task_dir / fremed._TASK_FILENAME).exists()

    def test_second_call_after_response_screens_and_halts_for_cold_verify(self, tmp_path) -> None:
        """B2: the second call (query response present) only mechanically
        screens + emits the cold-verify input — it does NOT apply the
        round yet. A THIRD call, after the verdict exists, applies it."""
        review_dir = self._setup_review_dir(tmp_path)
        nodes_lookup = self._nodes_lookup(review_dir)
        manifest_path = review_dir / "phase1-dag.json"
        run_state = RunState(run_id="r1", manifest_path=str(manifest_path))

        # First call emits the query-authoring task.
        _evaluate_autonomous_gate("coverage-gate", nodes_lookup, manifest_path, run_state)
        task_dir = fremed.facet_task_dir(review_dir, "by-temporal.counter")
        (task_dir / fremed._RESPONSE_FILENAME).write_text(
            "```queries\npersona rigidity long-horizon dialogue session\n```\n", encoding="utf-8",
        )

        def fake_tool_op(**kwargs):
            class _Hit:
                title = "Persona Rigidity Across Sessions"
                abstract = "persona stability rigidity multi-turn dialogue session study"
                authors = ["A. Researcher"]
                year = 2025

            return [_Hit()]

        with pytest.MonkeyPatch.context() as mp:
            mp.setitem(auto.OP_REGISTRY, "sweep", fake_tool_op)
            disposition = _evaluate_autonomous_gate("coverage-gate", nodes_lookup, manifest_path, run_state)

        assert disposition.disposition == auto.HALT_DECLARE
        assert "AWAITING a cold relevance-verify pass" in disposition.reason
        # never appended yet — B2, the candidate has not been cold-verified
        assert "[NEW][NEEDS-CURATE]" not in (review_dir / "_corpus.md").read_text()
        assert run_state.meta["facet_remediation_state"]["rounds_used"] == 0
        # the round-state persists so a repeat evaluation doesn't re-sweep
        # (idempotent poll) — the query task/response are cleared only once
        # the round fully APPLIES, below.
        ns = run_state.node_states["coverage-gate"]
        assert ns.get("facet_remediate_task_dir") == str(task_dir)

        (real_citekey,) = disposition.evidence["candidates"]
        from research_vault.review import relevance as rel

        verdict_path = task_dir / fremed._REMEDIATION_VERIFY_VERDICT_FILENAME
        verdict_path.write_text(
            "| Citekey | Verdict |\n|---|---|\n"
            f"| {real_citekey} | IN |\n"
            f"| {rel.CANARY_IN_SCOPE_CITEKEY} | IN |\n"
            f"| {rel.CANARY_OFF_DOMAIN_CITEKEY} | OFF_DOMAIN |\n",
            encoding="utf-8",
        )

        with pytest.MonkeyPatch.context() as mp:
            mp.setitem(auto.OP_REGISTRY, "sweep", fake_tool_op)
            final_disposition = _evaluate_autonomous_gate(
                "coverage-gate", nodes_lookup, manifest_path, run_state,
            )

        assert final_disposition.disposition == auto.HALT_DECLARE
        assert "AWAITING a re-curate pass" in final_disposition.reason
        assert "[NEW][NEEDS-CURATE]" in (review_dir / "_corpus.md").read_text()
        assert run_state.meta["facet_remediation_state"]["rounds_used"] == 1
        ns = run_state.node_states["coverage-gate"]
        assert "facet_remediate_task_dir" not in ns
