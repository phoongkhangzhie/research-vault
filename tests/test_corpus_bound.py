# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for review/corpus_bound.py — Section C (task #86): the deterministic
composite strength order + stratified largest-remainder selection that
bounds the curated corpus to ``corpus_bound`` (~100) papers.

Pins (per the build-decision text in the design doc):
  - floor-K guaranteed per pole with enough IN-papers
  - proportional largest-remainder allocation of the remainder
  - a <K pole contributes all it has, never padded
  - a protected (#59) citekey is never dropped even when below the cut
  - composite order: verdict tier (IN>UNCERTAIN) -> #poles -> sweep rank
  - rerank only breaks TRUE ties (same tier/poles/rank), and only for
    scored rows
  - total selected == corpus_bound, or fewer if the IN-pool is smaller
  - determinism: identical inputs -> identical selection, every time
"""
from __future__ import annotations

from research_vault.review import corpus_bound as cb


def _row(citekey, verdict="IN", poles=(), sweep_rank=0, rerank=None):
    return cb.CorpusRow(
        citekey=citekey,
        verdict=verdict,
        poles=frozenset(poles),
        sweep_rank=sweep_rank,
        rerank=rerank,
    )


class TestCompositeOrder:
    def test_in_beats_uncertain(self):
        rows = [
            _row("a", verdict="UNCERTAIN", sweep_rank=0),
            _row("b", verdict="IN", sweep_rank=1),
        ]
        ordered = cb.sort_by_composite(rows)
        assert [r.citekey for r in ordered] == ["b", "a"]

    def test_more_poles_beats_fewer_at_same_tier(self):
        rows = [
            _row("a", poles=["x.thesis"], sweep_rank=0),
            _row("b", poles=["x.thesis", "y.counter"], sweep_rank=1),
        ]
        ordered = cb.sort_by_composite(rows)
        assert [r.citekey for r in ordered] == ["b", "a"]

    def test_lower_sweep_rank_wins_when_tier_and_poles_tie(self):
        rows = [
            _row("a", poles=["x.thesis"], sweep_rank=5),
            _row("b", poles=["y.thesis"], sweep_rank=1),
        ]
        ordered = cb.sort_by_composite(rows)
        assert [r.citekey for r in ordered] == ["b", "a"]

    def test_full_ordering_priority_tier_over_poles_over_rank(self):
        # An IN paper with fewer poles and a worse rank still beats an
        # UNCERTAIN paper with more poles and a better rank — tier is
        # primary, never overridden by a lower-priority signal.
        rows = [
            _row("weak_in", verdict="IN", poles=[], sweep_rank=99),
            _row("strong_uncertain", verdict="UNCERTAIN", poles=["a.thesis", "b.counter"], sweep_rank=0),
        ]
        ordered = cb.sort_by_composite(rows)
        assert [r.citekey for r in ordered] == ["weak_in", "strong_uncertain"]

    def test_rerank_only_breaks_true_composite_ties(self):
        # Two rows sharing an identical (tier, poles, sweep_rank) triple —
        # an edge case, but the tiebreaker must fire when it happens.
        rows = [
            _row("low_score", poles=["x.thesis"], sweep_rank=3, rerank=0.1),
            _row("high_score", poles=["x.thesis"], sweep_rank=3, rerank=0.9),
        ]
        ordered = cb.sort_by_composite(rows)
        assert [r.citekey for r in ordered] == ["high_score", "low_score"]

    def test_rerank_never_overrides_a_real_composite_difference(self):
        # A row with a much lower rerank score but a strictly better
        # composite triple must still rank first — rerank is a tiebreaker
        # ONLY, never a primary factor.
        rows = [
            _row("better_composite", poles=["x.thesis", "y.counter"], sweep_rank=0, rerank=0.01),
            _row("worse_composite", poles=["x.thesis"], sweep_rank=0, rerank=0.99),
        ]
        ordered = cb.sort_by_composite(rows)
        assert [r.citekey for r in ordered] == ["better_composite", "worse_composite"]

    def test_scored_row_beats_unscored_row_on_a_true_tie(self):
        rows = [
            _row("unscored", poles=["x.thesis"], sweep_rank=7, rerank=None),
            _row("scored", poles=["x.thesis"], sweep_rank=7, rerank=0.2),
        ]
        ordered = cb.sort_by_composite(rows)
        assert [r.citekey for r in ordered] == ["scored", "unscored"]

    def test_deterministic_repeated_sort_identical(self):
        rows = [
            _row("c", verdict="IN", poles=["x.thesis"], sweep_rank=2),
            _row("a", verdict="IN", poles=["x.thesis", "y.counter"], sweep_rank=0),
            _row("b", verdict="UNCERTAIN", poles=[], sweep_rank=1),
        ]
        first = [r.citekey for r in cb.sort_by_composite(rows)]
        second = [r.citekey for r in cb.sort_by_composite(list(reversed(rows)))]
        assert first == second


class TestFloorGuarantee:
    def test_pole_with_enough_in_papers_gets_its_floor(self):
        rows = [_row(f"p{i}", poles=["x.thesis"], sweep_rank=i) for i in range(5)]
        result = cb.select_bounded_corpus(rows, corpus_bound=2, min_hits_per_pole=3)
        # The floor (3) exceeds the requested bound (2) — the floor still
        # wins (guaranteed), surfaced via floor_exceeded_bound.
        assert len(result.selected) == 3
        assert result.floor_exceeded_bound is True

    def test_thin_pole_below_floor_contributes_all_not_padded(self):
        rows = [_row("only_one", poles=["x.thesis"], sweep_rank=0)]
        result = cb.select_bounded_corpus(rows, corpus_bound=100, min_hits_per_pole=3)
        assert [r.citekey for r in result.selected] == ["only_one"]
        assert "x.thesis" in result.thin_poles

    def test_thin_pole_not_padded_with_fabricated_rows(self):
        rows = [_row(f"p{i}", poles=["x.thesis"], sweep_rank=i) for i in range(2)]
        result = cb.select_bounded_corpus(rows, corpus_bound=100, min_hits_per_pole=3)
        # Only the 2 real candidates for this pole — never padded to 3.
        selected_citekeys = {r.citekey for r in result.selected}
        assert selected_citekeys == {"p0", "p1"}
        assert "x.thesis" in result.thin_poles


class TestProportionalRemainder:
    def test_larger_pool_gets_larger_share_of_remainder(self):
        big_pool = [_row(f"big{i}", poles=["big.thesis"], sweep_rank=i) for i in range(20)]
        small_pool = [_row(f"small{i}", poles=["small.thesis"], sweep_rank=100 + i) for i in range(5)]
        rows = big_pool + small_pool
        result = cb.select_bounded_corpus(rows, corpus_bound=10, min_hits_per_pole=2)
        selected = {r.citekey for r in result.selected}
        big_selected = sum(1 for c in selected if c.startswith("big"))
        small_selected = sum(1 for c in selected if c.startswith("small"))
        assert len(selected) == 10
        assert big_selected > small_selected

    def test_total_selected_equals_bound_when_pool_is_large_enough(self):
        rows = [_row(f"p{i}", poles=["x.thesis"], sweep_rank=i) for i in range(50)]
        result = cb.select_bounded_corpus(rows, corpus_bound=10, min_hits_per_pole=3)
        assert len(result.selected) == 10

    def test_total_selected_is_fewer_when_pool_smaller_than_bound(self):
        rows = [_row(f"p{i}", poles=["x.thesis"], sweep_rank=i) for i in range(4)]
        result = cb.select_bounded_corpus(rows, corpus_bound=100, min_hits_per_pole=3)
        assert len(result.selected) == 4

    def test_best_composite_rows_preferred_within_a_pole_bucket(self):
        rows = [_row(f"p{i}", poles=["x.thesis"], sweep_rank=i) for i in range(10)]
        result = cb.select_bounded_corpus(rows, corpus_bound=3, min_hits_per_pole=1)
        # Rank 0/1/2 (best composite order) must be preferred over rank 9.
        selected = {r.citekey for r in result.selected}
        assert "p0" in selected
        assert "p9" not in selected


class TestProtectedStratumPin:
    def test_protected_citekey_survives_even_when_it_would_be_cut(self):
        strong = [_row(f"strong{i}", poles=["x.thesis"], sweep_rank=i) for i in range(5)]
        weak_protected = _row("protected_weak", poles=[], sweep_rank=999, verdict="UNCERTAIN")
        rows = strong + [weak_protected]
        result = cb.select_bounded_corpus(
            rows, corpus_bound=2, min_hits_per_pole=1,
            pinned_citekeys=frozenset({"protected_weak"}),
        )
        selected = {r.citekey for r in result.selected}
        assert "protected_weak" in selected

    def test_pins_count_inside_the_bound_not_on_top_of_it(self):
        rows = [_row(f"p{i}", poles=["x.thesis"], sweep_rank=i) for i in range(20)]
        result = cb.select_bounded_corpus(
            rows, corpus_bound=5, min_hits_per_pole=1,
            pinned_citekeys=frozenset({"p10"}),
        )
        assert len(result.selected) == 5
        assert "p10" in {r.citekey for r in result.selected}

    def test_pin_of_unknown_citekey_is_a_silent_no_op(self):
        rows = [_row("a", poles=["x.thesis"], sweep_rank=0)]
        result = cb.select_bounded_corpus(
            rows, corpus_bound=100, min_hits_per_pole=1,
            pinned_citekeys=frozenset({"does-not-exist"}),
        )
        assert {r.citekey for r in result.selected} == {"a"}


class TestDeterminism:
    def test_same_input_same_selection_every_call(self):
        rows = [
            _row(f"p{i}", poles=[f"pole{i % 3}.thesis"], sweep_rank=i, rerank=(0.1 * i if i % 2 else None))
            for i in range(30)
        ]
        first = cb.select_bounded_corpus(rows, corpus_bound=15, min_hits_per_pole=2)
        second = cb.select_bounded_corpus(list(reversed(rows)), corpus_bound=15, min_hits_per_pole=2)
        assert [r.citekey for r in first.selected] == [r.citekey for r in second.selected]

    def test_no_randomness_module_used(self):
        import inspect

        src = inspect.getsource(cb)
        assert "import random" not in src
        assert "random." not in src


class TestFindForwardReferencedCitekeys:
    def test_finds_citekey_referenced_in_a_concept_body(self, tmp_path):
        concepts = tmp_path / "concepts"
        concepts.mkdir()
        (concepts / "believability.md").write_text(
            "---\ntitle: Believability\n---\n\n"
            "## Related literature\n\n"
            "- [A grounding paper](/literature/smith2024.md) — SUPPORTS: reason\n",
            encoding="utf-8",
        )
        found = cb.find_forward_referenced_citekeys(concepts, {"smith2024", "jones2023"})
        assert found == {"smith2024"}

    def test_no_concepts_dir_is_an_honest_empty_set(self, tmp_path):
        found = cb.find_forward_referenced_citekeys(tmp_path / "does-not-exist", {"smith2024"})
        assert found == set()

    def test_citekey_not_referenced_anywhere_is_not_pinned(self, tmp_path):
        concepts = tmp_path / "concepts"
        concepts.mkdir()
        (concepts / "x.md").write_text("no literature links here\n", encoding="utf-8")
        found = cb.find_forward_referenced_citekeys(concepts, {"smith2024"})
        assert found == set()


class TestRowsFromCorpusMd:
    def test_parses_new_rows_with_verdict_poles_rerank(self):
        text = (
            "| Annotation | Citekey | Title | Abstract | Rerank | Poles |\n"
            "|---|---|---|---|---|---|\n"
            "| [NEW] | smith2024 | A Study | An abstract | 0.734 | x.thesis, y.counter |\n"
            "| [IN-CORPUS:old2019] | old2019 | Old paper | | | |\n"
        )
        rows = cb.rows_from_corpus_md(text, {"smith2024": "IN"})
        assert len(rows) == 1
        row = rows[0]
        assert row.citekey == "smith2024"
        assert row.verdict == "IN"
        assert row.poles == frozenset({"x.thesis", "y.counter"})
        assert row.rerank == 0.734
        assert row.sweep_rank == 0

    def test_dash_poles_and_rerank_sentinel_parse_to_none_empty(self):
        text = (
            "| Annotation | Citekey | Title | Abstract | Rerank | Poles |\n"
            "|---|---|---|---|---|---|\n"
            "| [NEW] | walk2024 | A Study | An abstract | — | — |\n"
        )
        rows = cb.rows_from_corpus_md(text, {"walk2024": "UNCERTAIN"})
        assert rows[0].rerank is None
        assert rows[0].poles == frozenset()

    def test_missing_verdict_defaults_to_empty_string(self):
        text = (
            "| Annotation | Citekey | Title | Abstract | Rerank | Poles |\n"
            "|---|---|---|---|---|---|\n"
            "| [NEW] | uncovered2024 | A Study | | | |\n"
        )
        rows = cb.rows_from_corpus_md(text, {})
        assert rows[0].verdict == ""
        assert cb._verdict_tier(rows[0].verdict) == cb._UNKNOWN_TIER


class TestApplyCorpusBound:
    def test_removes_new_rows_beyond_bound_keeps_in_corpus_rows_untouched(self, tmp_path):
        corpus_path = tmp_path / "_corpus.md"
        rows_md = "\n".join(
            f"| [NEW] | p{i} | Paper {i} | abstract | | x.thesis |" for i in range(5)
        )
        corpus_path.write_text(
            "# Corpus\n\n"
            "| Annotation | Citekey | Title | Abstract | Rerank | Poles |\n"
            "|---|---|---|---|---|---|\n"
            + rows_md + "\n"
            "| [IN-CORPUS:existing2019] | existing2019 | Existing | | | |\n",
            encoding="utf-8",
        )
        verdicts = {f"p{i}": "IN" for i in range(5)}
        result = cb.apply_corpus_bound(
            corpus_path, verdicts=verdicts, corpus_bound=2, min_hits_per_pole=1,
        )
        assert result.rows_considered == 5
        assert result.rows_removed == 3
        final_text = corpus_path.read_text(encoding="utf-8")
        assert "existing2019" in final_text
        assert "p0" in final_text
        assert "p4" not in final_text  # worst sweep_rank, cut

    def test_idempotent_on_second_run(self, tmp_path):
        corpus_path = tmp_path / "_corpus.md"
        rows_md = "\n".join(
            f"| [NEW] | p{i} | Paper {i} | abstract | | x.thesis |" for i in range(5)
        )
        corpus_path.write_text(
            "| Annotation | Citekey | Title | Abstract | Rerank | Poles |\n"
            "|---|---|---|---|---|---|\n" + rows_md + "\n",
            encoding="utf-8",
        )
        verdicts = {f"p{i}": "IN" for i in range(5)}
        cb.apply_corpus_bound(corpus_path, verdicts=verdicts, corpus_bound=2, min_hits_per_pole=1)
        text_after_first = corpus_path.read_text(encoding="utf-8")
        result2 = cb.apply_corpus_bound(corpus_path, verdicts=verdicts, corpus_bound=2, min_hits_per_pole=1)
        assert result2.rows_removed == 0
        assert corpus_path.read_text(encoding="utf-8") == text_after_first

    def test_protected_citekey_pinned_via_concepts_dir_survives(self, tmp_path):
        corpus_path = tmp_path / "_corpus.md"
        rows_md = "\n".join(
            f"| [NEW] | p{i} | Paper {i} | abstract | | x.thesis |" for i in range(5)
        )
        corpus_path.write_text(
            "| Annotation | Citekey | Title | Abstract | Rerank | Poles |\n"
            "|---|---|---|---|---|---|\n" + rows_md + "\n"
            "| [NEW] | grounding2024 | Grounding paper | abstract | | |\n",
            encoding="utf-8",
        )
        concepts = tmp_path / "concepts"
        concepts.mkdir()
        (concepts / "x.md").write_text(
            "- [g](/literature/grounding2024.md) — SUPPORTS: reason\n", encoding="utf-8",
        )
        verdicts = {f"p{i}": "IN" for i in range(5)}
        verdicts["grounding2024"] = "UNCERTAIN"
        result = cb.apply_corpus_bound(
            corpus_path, verdicts=verdicts, corpus_bound=2, min_hits_per_pole=1,
            concepts_dir=concepts,
        )
        final_text = corpus_path.read_text(encoding="utf-8")
        assert "grounding2024" in final_text
        assert result.rows_removed == 4

    def test_writes_residue_note_declaring_drops(self, tmp_path):
        corpus_path = tmp_path / "_corpus.md"
        rows_md = "\n".join(
            f"| [NEW] | p{i} | Paper {i} | abstract | | x.thesis |" for i in range(3)
        )
        corpus_path.write_text(
            "| Annotation | Citekey | Title | Abstract | Rerank | Poles |\n"
            "|---|---|---|---|---|---|\n" + rows_md + "\n",
            encoding="utf-8",
        )
        residue_path = tmp_path / "_corpus-bound-residue.md"
        verdicts = {f"p{i}": "IN" for i in range(3)}
        cb.apply_corpus_bound(
            corpus_path, verdicts=verdicts, corpus_bound=1, min_hits_per_pole=1,
            residue_path=residue_path,
        )
        residue_text = residue_path.read_text(encoding="utf-8")
        assert "p1" in residue_text or "p2" in residue_text
        assert "Dropped" in residue_text
