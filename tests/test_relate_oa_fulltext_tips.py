"""test_relate_oa_fulltext_tips.py — OA-first full-text enrichment (tier 1),
the coupled `per_paper_relate_tips` prose edit (design §4.1/§8.7).

Prose-teeth tests: assert the brief actually instructs the new reading
contract (full text when available, abstract otherwise) and narrows the
LEAN "never fetch" constraint to artifacts only — mirrors the existing
convention in test_relate_5move_protocol_brief.py.
"""
from __future__ import annotations

from research_vault.review.style import get_review_tips


def _tip() -> str:
    return get_review_tips()["per_paper_relate_tips"]


class TestReadsContractUpdated:
    def test_reads_full_text_when_available(self):
        tip = _tip().lower()
        assert "full text" in tip
        assert "abstract" in tip  # still the honest fallback, named explicitly

    def test_names_the_fulltext_tool(self):
        tip = _tip()
        assert "rv research fulltext" in tip

    def test_old_abstract_only_reads_line_is_gone(self):
        tip = _tip().lower()
        # the pre-tier-1 line said the reads: access was "abstract + key
        # sections" — that framing must be gone now that full text is fetched.
        assert "abstract + key sections" not in tip


class TestLeanNarrowedToArtifactsOnly:
    def test_never_fetch_language_still_covers_artifacts(self):
        tip = _tip().lower()
        # repo/checkpoint/dataset stay record-what-you-see -- this must survive.
        assert "do not clone the repo" in tip or "never fetch" in tip or "never download" in tip

    def test_paper_body_is_named_the_exception(self):
        tip = _tip().lower()
        assert "exception" in tip

    def test_lean_constraint_no_longer_blankets_the_paper_itself(self):
        tip = _tip()
        # The old sentence blanket-applied "never fetch or download it" to
        # THE PAPER ITSELF with no scoping clause. It must now be explicitly
        # scoped to artifacts (repo/checkpoint/dataset) — never left as a
        # bare, unqualified "record what you see, never fetch" applied to
        # the paper.
        assert "record what you see, never fetch or download it)." not in tip
        assert "artifacts" in tip.lower()
