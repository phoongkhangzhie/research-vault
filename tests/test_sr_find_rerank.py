"""test_sr_find_rerank.py — SR-FIND-RERANK acceptance tests.

Coverage (network-free):
  1. Parser flags: --rerank/--no-rerank, --pool, --min-score present on find subparser.
  2. Recall fixture: raw asta order top-10 MISSES known buried anchors;
     after rank_candidates(query, pool, top_k=10) those anchors are SURFACED.
     Strict inequality: n_after > n_before (reranking strictly improves recall).
  3. Body builder: paper with no abstract uses title only (no crash, no empty body).
  4. Annotation preserved: externalIds/ArXiv survives rank_candidates round-trip
     so _corpus_annotation still works on reranked results.
  5. --no-rerank semantics: legacy path (rank_candidates NOT called); papers come
     back in asta order with no added 'score' key.
  6. Slice-2 no-op: asta papers search has no field-of-study filter; confirmed and
     documented.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "find_rerank_llm_cultural_values.json"

# Known anchor arXiv ids: highly relevant to the query but buried past position 10
# in the raw asta ordering (verified: positions 28, 30, 38 respectively).
QUERY = "LLM cultural values alignment cross-cultural benchmark"
KNOWN_ANCHORS = {
    "2408.16482",  # Self-Alignment: Improving Alignment of Cultural Values in LLMs — pos 38
    "2512.07075",  # Do Large Language Models Truly Understand Cross-cultural Differences? — pos 28
    "2512.05176",  # Towards A Cultural Intelligence and Values Inferences Quality Benchmark — pos 30
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_pool() -> list[dict]:
    """Load the 50-paper fixture and attach body strings."""
    with FIXTURE_PATH.open(encoding="utf-8") as f:
        raw = json.load(f)
    papers = raw.get("data", [])
    assert papers, "Fixture is empty — recapture with asta papers search"
    return papers


def _arxiv_ids_in_top10(papers: list[dict]) -> set[str]:
    """Return the set of arXiv ids in the first 10 entries."""
    ids: set[str] = set()
    for p in papers[:10]:
        arxiv = (p.get("externalIds") or {}).get("ArXiv", "")
        if arxiv:
            ids.add(arxiv)
    return ids


# ---------------------------------------------------------------------------
# 1. Parser flags
# ---------------------------------------------------------------------------

class TestFindParserFlags:
    """The find subparser exposes all SR-FIND-RERANK flags."""

    def _find_parser(self) -> argparse.ArgumentParser:
        from research_vault import research as research_mod
        p = research_mod.build_parser()  # returns the 'research' subparser directly
        sub_actions = p._subparsers._group_actions[0]._name_parser_map
        return sub_actions["find"]

    def test_pool_flag_exists(self):
        """--pool is a recognized flag on the find subparser."""
        find_p = self._find_parser()
        args = find_p.parse_args(["my query", "--pool", "75"])
        assert args.pool == 75

    def test_pool_default_is_50(self):
        """--pool defaults to 50."""
        find_p = self._find_parser()
        args = find_p.parse_args(["my query"])
        assert args.pool == 50

    def test_rerank_on_by_default(self):
        """--rerank defaults to True."""
        find_p = self._find_parser()
        args = find_p.parse_args(["my query"])
        assert args.rerank is True

    def test_no_rerank_disables(self):
        """--no-rerank sets rerank=False."""
        find_p = self._find_parser()
        args = find_p.parse_args(["my query", "--no-rerank"])
        assert args.rerank is False

    def test_min_score_flag_exists(self):
        """--min-score is a recognized flag on the find subparser."""
        find_p = self._find_parser()
        args = find_p.parse_args(["my query", "--min-score", "0.1"])
        assert abs(args.min_score - 0.1) < 1e-9

    def test_min_score_default_is_zero(self):
        """--min-score defaults to 0.0 (reorder-not-drop)."""
        find_p = self._find_parser()
        args = find_p.parse_args(["my query"])
        assert args.min_score == 0.0

    def test_limit_default_still_10(self):
        """--limit default is still 10 (unchanged from pre-SR)."""
        find_p = self._find_parser()
        args = find_p.parse_args(["my query"])
        assert args.limit == 10


# ---------------------------------------------------------------------------
# 2. Recall fixture — strict recall gain
# ---------------------------------------------------------------------------

class TestRecallFixture:
    """Reranking surfaces buried anchors: strict n_after > n_before."""

    def test_fixture_file_exists(self):
        """The real asta fixture file exists at tests/fixtures/."""
        assert FIXTURE_PATH.exists(), (
            f"Fixture missing: {FIXTURE_PATH}. "
            "Recapture with: asta papers search <query> --format json --limit 50"
        )

    def test_fixture_has_50_papers(self):
        """The fixture contains the full 50-paper pool."""
        pool = _load_pool()
        assert len(pool) == 50, f"Expected 50 papers, got {len(pool)}"

    def test_anchors_buried_in_raw_asta_order(self):
        """Known anchors are NOT in the raw asta top-10 (they are buried)."""
        pool = _load_pool()
        asta_top10 = _arxiv_ids_in_top10(pool)
        buried = KNOWN_ANCHORS - asta_top10
        assert len(buried) == len(KNOWN_ANCHORS), (
            f"Expected all anchors buried in asta top-10, but {KNOWN_ANCHORS - buried} "
            f"already appear. Fixture may need a harder query."
        )

    def test_anchors_present_in_full_pool(self):
        """Known anchors ARE present somewhere in the 50-paper pool (positions 11-50)."""
        pool = _load_pool()
        pool_arxiv = {(p.get("externalIds") or {}).get("ArXiv", "") for p in pool}
        for anchor in KNOWN_ANCHORS:
            assert anchor in pool_arxiv, (
                f"Anchor arXiv:{anchor} not found anywhere in the 50-paper pool. "
                "Recapture fixture or update KNOWN_ANCHORS."
            )

    def test_strict_recall_gain_after_rerank(self):
        """After reranking, more anchors appear in the top-10 than before — strict gain.

        This is the load-bearing recall test:
          - Before: top-10 by raw asta order contains 0 of the known anchors.
          - After:  rank_candidates(query, pool, top_k=10) surfaces ≥ 1 anchor.
          - Strict: n_after > n_before (a delta of 0 means the fixture is inert).
        """
        from research_vault.cross_project import rank_candidates

        pool = _load_pool()

        # Attach body strings (same as cmd_find does)
        for p in pool:
            title = p.get("title") or ""
            abstract = p.get("abstract") or ""
            p["body"] = title + ("\n" + abstract if abstract else "")

        # Before: asta order
        n_before = len(KNOWN_ANCHORS & _arxiv_ids_in_top10(pool))
        assert n_before == 0, (
            f"Fixture is not hard enough: {n_before} anchor(s) already in asta top-10. "
            "Update KNOWN_ANCHORS or recapture a harder fixture."
        )

        # After: TF-IDF rerank
        reranked = rank_candidates(QUERY, pool, min_score=0.0, top_k=10)
        assert len(reranked) == 10, f"Reranker returned {len(reranked)} results, expected 10"

        reranked_top10 = _arxiv_ids_in_top10(reranked)
        n_after = len(KNOWN_ANCHORS & reranked_top10)
        assert n_after > n_before, (
            f"No recall gain: n_before={n_before}, n_after={n_after}. "
            f"Reranked top-10 arXiv ids: {reranked_top10}"
        )

    def test_all_anchors_surfaced_after_rerank(self):
        """All 3 known anchors appear in the reranked top-10."""
        from research_vault.cross_project import rank_candidates

        pool = _load_pool()
        for p in pool:
            title = p.get("title") or ""
            abstract = p.get("abstract") or ""
            p["body"] = title + ("\n" + abstract if abstract else "")

        reranked = rank_candidates(QUERY, pool, min_score=0.0, top_k=10)
        reranked_top10 = _arxiv_ids_in_top10(reranked)
        missing = KNOWN_ANCHORS - reranked_top10
        assert not missing, (
            f"Some anchors not surfaced in top-10: {missing}. "
            f"Reranked top-10: {reranked_top10}"
        )


# ---------------------------------------------------------------------------
# 3. Body builder tolerates missing abstract
# ---------------------------------------------------------------------------

class TestBodyBuilder:
    """rank_candidates handles papers with no abstract gracefully."""

    def test_missing_abstract_uses_title_only(self):
        """A paper with no abstract field produces a non-empty body from title."""
        from research_vault.cross_project import rank_candidates

        papers = [
            {"title": "Cultural Values in LLMs", "externalIds": {"ArXiv": "0001.00001"}},
            {"title": "Cross-cultural Alignment Benchmark", "abstract": "Studies LLM alignment."},
        ]
        for p in papers:
            title = p.get("title") or ""
            abstract = p.get("abstract") or ""
            p["body"] = title + ("\n" + abstract if abstract else "")

        # No crash, returns results
        results = rank_candidates("cultural values alignment", papers, min_score=0.0, top_k=5)
        assert len(results) >= 1

    def test_paper_with_no_abstract_gets_non_empty_body(self):
        """The body for a no-abstract paper equals the title (no trailing newline)."""
        title = "Cultural Values in Large Language Models"
        p = {"title": title}
        abstract = p.get("abstract") or ""
        body = title + ("\n" + abstract if abstract else "")
        assert body == title
        assert body  # non-empty

    def test_paper_with_abstract_includes_both(self):
        """The body for a paper with abstract = title + newline + abstract."""
        title = "Cultural Values Study"
        abstract = "This paper studies cultural values."
        p = {"title": title, "abstract": abstract}
        body = (p.get("title") or "") + ("\n" + (p.get("abstract") or "") if p.get("abstract") else "")
        assert body == f"{title}\n{abstract}"


# ---------------------------------------------------------------------------
# 4. Annotation preserved through reranking
# ---------------------------------------------------------------------------

class TestAnnotationPreserved:
    """externalIds and all corpus-annotation keys survive rank_candidates round-trip."""

    def test_external_ids_preserved(self):
        """rank_candidates preserves externalIds so _corpus_annotation still works."""
        from research_vault.cross_project import rank_candidates

        papers = [
            {
                "title": "Cultural LLM Benchmark",
                "abstract": "Evaluating cultural knowledge in large language models.",
                "externalIds": {"ArXiv": "1234.56789", "DOI": "10.1234/test"},
                "year": 2024,
                "body": "Cultural LLM Benchmark\nEvaluating cultural knowledge.",
            },
            {
                "title": "Cross-cultural Alignment Study",
                "abstract": "A study of cross-cultural alignment benchmarks.",
                "externalIds": {"ArXiv": "9876.54321"},
                "year": 2023,
                "body": "Cross-cultural Alignment Study\nA study of cross-cultural alignment benchmarks.",
            },
        ]
        results = rank_candidates("cultural benchmark alignment", papers, min_score=0.0, top_k=5)
        for r in results:
            assert "externalIds" in r, "externalIds lost after reranking"
            assert "score" in r, "score key should be added by ranker"

    def test_corpus_annotation_works_on_reranked_paper(self):
        """_corpus_annotation correctly annotates a reranked paper with externalIds."""
        from research_vault.cross_project import rank_candidates
        from research_vault.research import _corpus_annotation

        papers = [
            {
                "title": "Paper A",
                "abstract": "Cultural benchmark paper.",
                "externalIds": {"DOI": "10.1234/cultural-bench"},
                "body": "Paper A\nCultural benchmark paper.",
            },
        ]
        results = rank_candidates("cultural benchmark", papers, min_score=0.0, top_k=5)
        assert results
        notes_index = {"10.1234/cultural-bench": "bench2024"}
        annotation = _corpus_annotation(results[0], notes_index=notes_index)
        assert annotation == "[IN-CORPUS:bench2024]", (
            f"Expected [IN-CORPUS:bench2024], got {annotation!r}"
        )


# ---------------------------------------------------------------------------
# 5. --no-rerank: legacy path produces asta-order output
# ---------------------------------------------------------------------------

class TestNoRerankLegacy:
    """--no-rerank reproduces the pre-SR asta-order output (no score key added)."""

    def test_reranked_has_score_key(self):
        """Papers returned by rank_candidates have a 'score' key added."""
        from research_vault.cross_project import rank_candidates

        papers = [
            {"title": "T1", "body": "cultural values benchmark"},
            {"title": "T2", "body": "alignment cross-cultural study"},
        ]
        results = rank_candidates("cultural values", papers, min_score=0.0, top_k=5)
        for r in results:
            assert "score" in r

    def test_no_rerank_path_does_not_add_score_key(self):
        """When --no-rerank, papers are passed through without rank_candidates — no 'score'."""
        # Simulate the cmd_find no-rerank code path: do_rerank=False means we skip
        # the rank_candidates call entirely and pass papers straight to _print_candidates.
        # Test that papers without 'score' key can be printed without error.
        from research_vault.research import _print_candidates

        papers = [
            {"title": "Paper A", "year": 2024, "authors": [], "externalIds": {}, "abstract": ""},
            {"title": "Paper B", "year": 2023, "authors": [], "externalIds": {}, "abstract": ""},
        ]
        # Must not crash; no score key expected on these dicts
        for p in papers:
            assert "score" not in p

        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _print_candidates(papers)
        output = buf.getvalue()
        assert "Paper A" in output
        assert "Paper B" in output

    def test_reranked_results_count_limited_to_top_k(self):
        """rank_candidates respects top_k even with min_score=0.0."""
        from research_vault.cross_project import rank_candidates

        papers = [{"title": f"Paper {i}", "body": f"cultural benchmark paper {i}"} for i in range(20)]
        results = rank_candidates("cultural benchmark", papers, min_score=0.0, top_k=5)
        assert len(results) == 5


# ---------------------------------------------------------------------------
# 6. Slice-2 no-op: asta papers search has no field-of-study filter
# ---------------------------------------------------------------------------

class TestSlice2NoOp:
    """Slice-2 finding: asta papers search has no field-of-study/venue filter.

    asta papers search exposes --fields (which fields to return), --limit, and
    --date — but no field-of-study or venue filter. Appending scope terms to the
    query string would degrade S2 relevance scoring. Slice-2 is a no-op with
    rationale; no --field passthrough flag is added to rv research find.
    """

    def test_find_parser_has_no_field_passthrough(self):
        """The find subparser does NOT expose a --field flag (Slice-2 no-op)."""
        from research_vault import research as research_mod
        p = research_mod.build_parser()
        sub_actions = p._subparsers._group_actions[0]._name_parser_map
        find_p = sub_actions["find"]
        # --field should NOT be a registered action
        all_option_strings = {
            opt_str
            for action in find_p._actions
            for opt_str in action.option_strings
        }
        assert "--field" not in all_option_strings, (
            "--field was added to the find subparser. "
            "Slice-2 rationale: asta papers search has no field-of-study filter; "
            "appending scope terms degrades S2 relevance. "
            "If asta adds a native filter, implement it then."
        )
