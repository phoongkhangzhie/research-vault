"""test_query_expansion_prompt.py — Step-B seed-query expansion prompt regression.

Coverage:
  1. review_scope_tips carries the B1/B2/B3 systematic-expansion structure
     (harvest-from-evidence, semantic-backend distinctness, rejects-only
     self-check) — this is the guidance that replaced the old single-line
     "canonical phrasing + synonyms + lexical variants + adjacent-concept
     terms" Step B, and its absence would silently regress recall discipline
     back to unguided near-synonym expansion.
  2. the Step-D band assertion and near-synonym warning both cross-reference
     the B3 self-check as the mechanism, so the three sub-steps read as one
     coherent discipline rather than three disconnected notes.
  3. get_review_tips() still renders/injects review_scope_tips cleanly (the
     seam this text flows through into the review-scope node's spec).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.review.style import _DEFAULT_REVIEW_TIPS, get_review_tips


def test_step_b_has_harvest_first_rule():
    """B1: harvest terms from real in-scope papers' titles/abstracts/keywords
    BEFORE inventing synonyms."""
    tips = _DEFAULT_REVIEW_TIPS["review_scope_tips"]
    assert "Harvest from evidence" in tips
    assert "titles, abstracts, and author keywords" in tips


def test_step_b_has_semantic_backend_distinctness_rule():
    """B2: a term earns its place only if it's distinct on a SEMANTIC
    backend, not a Boolean one — and spelling/word-form variants are
    explicitly excluded (they collapse at dedup on a semantic backend)."""
    tips = _DEFAULT_REVIEW_TIPS["review_scope_tips"]
    assert "SEMANTIC backend" in tips
    assert "NOT a Boolean database" in tips
    assert "DO NOT spend a term on spelling" in tips


def test_step_b_has_rejects_only_self_check():
    """B3: rejects-only self-check — drop near-synonyms, don't pad to a
    count; saturation is coverage, not raw term count."""
    tips = _DEFAULT_REVIEW_TIPS["review_scope_tips"]
    assert "Rejects-only self-check" in tips
    assert "do NOT pad to a count" in tips
    assert "saturation is coverage, not count" in tips


def test_step_b_cites_recall_gap_evidence():
    """The prompt grounds the under-expansion warning in a real citation
    (public arXiv id), not an invented statistic."""
    tips = _DEFAULT_REVIEW_TIPS["review_scope_tips"]
    assert "arXiv:2505.07155" in tips


def test_step_b_has_worked_example():
    """A concrete worked example (5 genuinely distinct terms vs. 4
    rewordings) anchors the abstract B1/B2/B3 rules."""
    tips = _DEFAULT_REVIEW_TIPS["review_scope_tips"]
    assert "Worked example" in tips
    assert "persona stability" in tips


def test_old_single_line_step_b_is_gone():
    """The old under-specified Step B ('~3-6 terms: canonical phrasing +
    synonyms + lexical variants + adjacent-concept terms', with no harvest
    step and no rejects-only check) must not silently survive alongside
    the new text."""
    tips = _DEFAULT_REVIEW_TIPS["review_scope_tips"]
    assert (
        "Expand EACH facet into ~3-6 terms: canonical phrasing + "
        "synonyms + lexical variants + adjacent-concept terms."
    ) not in tips


def test_step_d_band_assertion_cross_refs_b3():
    """Companion edit (a): the Step-D 40-100 band assertion names B3 as
    what feeds the post-dedup distinct-query count."""
    tips = _DEFAULT_REVIEW_TIPS["review_scope_tips"]
    assert "Step-B3 rejects-only self-check is what feeds this" in tips


def test_step_d_near_synonym_warning_cross_refs_b3():
    """Companion edit (b): the '8 rewordings of one facet' near-synonym
    warning names B3 as the mechanism that prevents it."""
    tips = _DEFAULT_REVIEW_TIPS["review_scope_tips"]
    assert "Step-B3 rejects-only self-check is the" in tips
    assert "mechanism that prevents this" in tips


def test_no_internal_references_leaked():
    """Only PUBLIC citations belong in the shipped prompt — no internal
    task/PR numbers, design-doc filenames, or crew/hub language."""
    tips = _DEFAULT_REVIEW_TIPS["review_scope_tips"]
    for marker in ("hub", "crew", "coordinator", "vault"):
        assert marker not in tips.lower(), f"leaked internal marker: {marker!r}"


def test_get_review_tips_still_renders_scope_tips():
    """The tips seam (get_review_tips) still returns a non-empty
    review_scope_tips string containing the new Step-B structure — proves
    the edit didn't break rendering/injection into the review-scope node."""
    tips = get_review_tips()
    assert isinstance(tips["review_scope_tips"], str)
    assert tips["review_scope_tips"].strip()
    assert "B1" in tips["review_scope_tips"]
    assert "B2" in tips["review_scope_tips"]
    assert "B3" in tips["review_scope_tips"]
