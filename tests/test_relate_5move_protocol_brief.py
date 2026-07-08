"""test_relate_5move_protocol_brief.py — Wave 0 (Reading) PR-1/PR-2/PR-4/PR-5.

Prose-teeth tests over ``per_paper_relate_tips`` / ``review_synthesize_tips``
(review/style.py) — mirrors the existing convention in
``tests/test_pr_l1_lit_ingestion.py`` (assert the brief actually instructs
the discipline, not just that it exists).

sr: NG-lit-review-wave0
"""
from __future__ import annotations

from research_vault.review.style import get_review_tips, REVIEW_TIPS_KEYS


class TestFiveMoveProtocolBrief:
    def test_all_five_moves_named(self):
        tip = get_review_tips()["per_paper_relate_tips"]
        for marker in ("MOVE 1", "MOVE 2", "MOVE 3", "MOVE 4", "MOVE 5"):
            assert marker in tip, f"per_paper_relate_tips missing {marker}"

    def test_move1_contribution_kind_vocabulary(self):
        tip = get_review_tips()["per_paper_relate_tips"]
        for kind in ("mechanism", "theory-bound", "benchmark", "survey", "application"):
            assert kind in tip

    def test_move2_exact_arrow_craft_named(self):
        tip = get_review_tips()["per_paper_relate_tips"].lower()
        assert "exact arrow" in tip

    def test_move3_result_reported_mandatory(self):
        tip = get_review_tips()["per_paper_relate_tips"]
        assert "result_reported" in tip
        assert "## Result" in tip
        assert "magnitude" in tip.lower()
        assert "limitations" in tip.lower()

    def test_move4_paper_relations_sought_mandatory(self):
        tip = get_review_tips()["per_paper_relate_tips"]
        assert "paper_relations_sought" in tip
        assert "## Related papers" in tip
        for word in ("reciprocal", "refutational", "line-of-argument"):
            assert word in tip

    def test_move4_bracket_edge_vocabulary_present(self):
        tip = get_review_tips()["per_paper_relate_tips"]
        for tag in ("[SUPPORTS", "[CONTRADICTS", "[PARTIAL", "[EXTENDS"):
            assert tag in tip

    def test_move4_over_rigidity_guard_stated(self):
        tip = get_review_tips()["per_paper_relate_tips"].lower()
        assert "bare tag" in tip or "over-rigid" in tip

    def test_move4_names_the_relations_consume_verb(self):
        tip = get_review_tips()["per_paper_relate_tips"]
        assert "rv review" in tip and "relations" in tip

    def test_pr4_role_position_split_named(self):
        tip = get_review_tips()["per_paper_relate_tips"]
        assert "`role`" in tip
        assert "`position`" in tip
        for role in ("methodological", "empirical", "theoretical", "counter-position"):
            assert role in tip

    def test_pr4_stance_double_duty_explained(self):
        tip = get_review_tips()["per_paper_relate_tips"].lower()
        assert "double duty" in tip

    def test_move5_unchanged_concept_edges_still_present(self):
        tip = get_review_tips()["per_paper_relate_tips"]
        assert "concepts/<c>.md" in tip
        assert "[SUPPORTS] concepts" in tip


class TestSynthesizeTraversalBrief:
    def test_traverse_dont_rederive_named(self):
        tip = get_review_tips()["review_synthesize_tips"].lower()
        assert "traverse" in tip

    def test_names_the_relations_verb(self):
        tip = get_review_tips()["review_synthesize_tips"]
        assert "rv review" in tip and "relations" in tip

    def test_moc_entry_uses_role_not_stance(self):
        tip = get_review_tips()["review_synthesize_tips"]
        assert "(<role>)" in tip
        assert "(<stance>)" not in tip


class TestKeysUnaffected:
    def test_review_tips_keys_unchanged(self):
        """Wave 0 changes prose content only — the fixed key set (a breaking
        change if altered) must remain exactly as-is."""
        assert set(get_review_tips().keys()) == REVIEW_TIPS_KEYS
