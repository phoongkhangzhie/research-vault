"""test_gates_support_matcher.py — PR-M3: gates.support_matcher (shared, D-SV-0).

Covers the re-instantiated honesty-gates.md principles for the claim->source
support-matcher, now living in the SHAREABLE ``research_vault.gates`` package
(not under manuscript/):

  1. _extract_support_verdict — the 4-verdict bracket extractor (bare words
     do not trip it; [PASS]/[BLOCK] do not match this extractor).
  2. match_support — SUPPORTS / PARTIAL / ABSENT / CONTRADICTS verdicts.
  3. .blocks / .warns properties match the honesty-gates.md contract.
  4. Verbatim-span-or-BLOCK: ABSENT verdict never carries a span.
  5. Fail-closed: judge exception -> ABSENT (never silently passes).
  6. Fail-closed: missing note -> ABSENT (cannot quote -> cannot confirm).
  7. Anti-anchoring: only structured fields reach the prompt, never the
     paper's own "Abstract" section (the note's self-description).
  8. Scope-extraction (rubric contamination): a rubric-only mention of a
     verdict keyword must not leak into the parsed verdict.
  9. J-2 stance escalation (exploratory + confirmatory-strength -> BLOCK).
  10. PLANTED FAILURE (required by PR-M3 acceptance): a claim with NO real
      support in its cited note is caught — the judge is fed the real note
      content and returns [ABSENT]/BLOCK when there is nothing to quote.

All hermetic (tmp_path, mock judge_fn). No live LLM calls.
sr: PR-M3
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _literature_note(tmp_path: Path, citekey: str, *, fields: dict | None = None) -> Path:
    lit_dir = tmp_path / "literature"
    lit_dir.mkdir(parents=True, exist_ok=True)
    ffields = {
        "type": "literature",
        "tldr": "This paper demonstrates X.",
        "findings": "Finding A: X is true.",
        "limitations": "Only tested on Y.",
    }
    if fields:
        ffields.update(fields)
    fm = "---\n" + "\n".join(f"{k}: {v}" for k, v in ffields.items()) + "\n---\n"
    path = lit_dir / f"{citekey}.md"
    path.write_text(fm, encoding="utf-8")
    return path


def _mock_judge(verdict: str = "SUPPORTS", span: str = "Finding A: X is true.", polarity: str = "positive"):
    def _fn(prompt: str) -> str:
        return (
            f"VERDICT: [{verdict}]\n"
            f"VERBATIM_SPAN: {span}\n"
            f"POLARITY: {polarity}\n"
            f"REASONING: Mock reasoning for testing.\n"
        )
    return _fn


# ===========================================================================
# 1. _extract_support_verdict — the 4-verdict bracket extractor
# ===========================================================================

class TestExtractSupportVerdict:
    def test_all_four_tokens_recognized(self):
        from research_vault.gates.support_matcher import _extract_support_verdict
        assert _extract_support_verdict("[SUPPORTS]") == "SUPPORTS"
        assert _extract_support_verdict("[PARTIAL]") == "PARTIAL"
        assert _extract_support_verdict("[ABSENT]") == "ABSENT"
        assert _extract_support_verdict("[CONTRADICTS]") == "CONTRADICTS"

    def test_case_insensitive(self):
        from research_vault.gates.support_matcher import _extract_support_verdict
        assert _extract_support_verdict("[supports]") == "SUPPORTS"

    def test_bare_word_does_not_match(self):
        """A bare unbracketed 'absent' in prose must not trip the gate."""
        from research_vault.gates.support_matcher import _extract_support_verdict
        assert _extract_support_verdict("absent") is None
        assert _extract_support_verdict("SUPPORTS") is None

    def test_pass_block_do_not_match(self):
        """The 2-verdict [PASS]/[BLOCK] tokens are a DIFFERENT extractor's domain."""
        from research_vault.gates.support_matcher import _extract_support_verdict
        assert _extract_support_verdict("[PASS]") is None
        assert _extract_support_verdict("[BLOCK]") is None


# ===========================================================================
# 2. match_support — core verdicts
# ===========================================================================

class TestMatchSupport:
    def test_supports_verdict(self, tmp_path):
        from research_vault.gates.support_matcher import match_support
        note = _literature_note(tmp_path, "smith2023")
        v = match_support(
            "We show that X is true [[smith2023]].",
            "smith2023",
            note,
            judge_fn=_mock_judge("SUPPORTS", "Finding A: X is true."),
        )
        assert v.verdict == "SUPPORTS"
        assert v.verbatim_span is not None
        assert not v.blocks
        assert not v.warns

    def test_absent_verdict_blocks(self, tmp_path):
        from research_vault.gates.support_matcher import match_support
        note = _literature_note(tmp_path, "jones2024")
        v = match_support(
            "We show Y [[jones2024]].",
            "jones2024",
            note,
            judge_fn=_mock_judge("ABSENT", "none"),
        )
        assert v.verdict == "ABSENT"
        assert v.blocks

    def test_contradicts_verdict_blocks(self, tmp_path):
        from research_vault.gates.support_matcher import match_support
        note = _literature_note(tmp_path, "doe2023")
        v = match_support(
            "X does not exist [[doe2023]].",
            "doe2023",
            note,
            judge_fn=_mock_judge("CONTRADICTS", "none", "negative"),
        )
        assert v.verdict == "CONTRADICTS"
        assert v.blocks

    def test_partial_verdict_warns(self, tmp_path):
        from research_vault.gates.support_matcher import match_support
        note = _literature_note(tmp_path, "lee2022")
        v = match_support(
            "We definitively prove X [[lee2022]].",
            "lee2022",
            note,
            judge_fn=_mock_judge("PARTIAL", "Finding A: X may be true."),
        )
        assert v.verdict == "PARTIAL"
        assert not v.blocks
        assert v.warns


# ===========================================================================
# 3. Verbatim-span-or-BLOCK (honesty-gates.md §3)
# ===========================================================================

class TestVerbatimSpanOrBlock:
    def test_absent_never_carries_a_span(self, tmp_path):
        """Even if the judge (mis-)returns a span alongside ABSENT, it is dropped."""
        from research_vault.gates.support_matcher import match_support
        note = _literature_note(tmp_path, "spanleak2024")

        def _bad_judge(prompt: str) -> str:
            return (
                "VERDICT: [ABSENT]\n"
                'VERBATIM_SPAN: "Finding A: X is true."\n'  # contradictory — should be dropped
                "POLARITY: neutral\nREASONING: n/a\n"
            )

        v = match_support("Z [[spanleak2024]].", "spanleak2024", note, judge_fn=_bad_judge)
        assert v.verdict == "ABSENT"
        assert v.verbatim_span is None


# ===========================================================================
# 4. Fail-closed defaults (honesty-gates.md §5)
# ===========================================================================

class TestFailClosed:
    def test_judge_exception_degrades_to_absent(self, tmp_path):
        """A raising judge_fn must never propagate — it degrades to ABSENT/BLOCK."""
        from research_vault.gates.support_matcher import match_support
        note = _literature_note(tmp_path, "raises2024")

        def _raising_judge(prompt: str) -> str:
            raise RuntimeError("simulated network failure")

        v = match_support("Q [[raises2024]].", "raises2024", note, judge_fn=_raising_judge)
        assert v.verdict == "ABSENT"
        assert v.blocks

    def test_missing_note_is_absent(self, tmp_path):
        """No note file -> ABSENT without even calling the judge (can't quote -> can't confirm)."""
        from research_vault.gates.support_matcher import match_support
        missing = tmp_path / "literature" / "nobody2025.md"
        v = match_support(
            "We cite nobody [[nobody2025]].",
            "nobody2025",
            missing,
            judge_fn=_mock_judge("SUPPORTS"),  # even a SUPPORTS-happy judge cannot rescue this
        )
        assert v.verdict == "ABSENT"
        assert v.blocks

    def test_unparseable_response_is_absent(self, tmp_path):
        """Judge output missing a VERDICT: line -> ABSENT (the safe default)."""
        from research_vault.gates.support_matcher import match_support
        note = _literature_note(tmp_path, "garbled2024")

        def _garbled_judge(prompt: str) -> str:
            return "I think this is probably fine, no clear verdict token here."

        v = match_support("W [[garbled2024]].", "garbled2024", note, judge_fn=_garbled_judge)
        assert v.verdict == "ABSENT"


# ===========================================================================
# 5. Anti-anchoring — the paper's own Abstract is never fed to the judge
# ===========================================================================

class TestAntiAnchoring:
    def test_abstract_section_excluded_from_structured_fields(self, tmp_path):
        from research_vault.gates.support_matcher import _read_note_structured_fields
        lit_dir = tmp_path / "literature"
        lit_dir.mkdir(parents=True)
        note = lit_dir / "abstest2024.md"
        note.write_text(
            "---\ntype: literature\n---\n"
            "## Abstract\n"
            "This paper conclusively proves the moon is made of cheese.\n\n"
            "## Result\n"
            "Observed accuracy: 62%.\n",
            encoding="utf-8",
        )
        fields = _read_note_structured_fields(note)
        joined = " ".join(fields.values()).lower()
        assert "cheese" not in joined
        assert any("62" in v for v in fields.values())

    def test_prompt_never_contains_forbidden_abstract_framing(self, tmp_path):
        """The built judge prompt must not surface the paper's own Abstract section."""
        from research_vault.gates.support_matcher import (
            _read_note_structured_fields,
            _build_judge_prompt,
            get_support_rubric,
        )
        lit_dir = tmp_path / "literature"
        lit_dir.mkdir(parents=True)
        note = lit_dir / "abstest2025.md"
        note.write_text(
            "---\ntype: literature\n---\n"
            "## Abstract\n"
            "We claim a revolutionary breakthrough (self-serving framing).\n\n"
            "## Result\n"
            "Accuracy: 71%.\n",
            encoding="utf-8",
        )
        fields = _read_note_structured_fields(note)
        prompt = _build_judge_prompt(
            "Accuracy reaches 71% [[abstest2025]].", "abstest2025", fields,
            get_support_rubric(),
        )
        assert "revolutionary breakthrough" not in prompt

    def test_oa_fulltext_enrichment_contract_unchanged(self, tmp_path):
        """OA-fulltext-enrichment (tier 1, 0.3.0): the support-matcher's
        contract does NOT change. A note whose `## Result` is now full-text-
        derived (real magnitude/conditions/limitations, not abstract-level
        vagueness) still only surfaces via the SAME structured-field path —
        an `## Abstract` section (even one present alongside a rich full-text
        body) stays excluded, and the judge still gets a quotable verbatim
        span from `## Result` alone. This is the exact "closes Moon/Kim/
        Zhang" mechanism (design §4.2): richer evidence, unchanged contract.
        """
        from research_vault.gates.support_matcher import match_support

        lit_dir = tmp_path / "literature"
        lit_dir.mkdir(parents=True)
        note = lit_dir / "richfulltext2026.md"
        note.write_text(
            "---\ntype: literature\nread_basis: full-text\n"
            "full_text_provider: arxiv-pdf\noa_status: green\n---\n"
            "## Abstract\n"
            "We present a groundbreaking new method (self-serving framing).\n\n"
            "## Result\n"
            "On the held-out benchmark, our method reaches 12.4 points higher "
            "accuracy than the baseline (78.9% vs 66.5%), averaged over five "
            "random seeds. Limitations: results were only measured on English "
            "text; generalization to other languages is untested.\n",
            encoding="utf-8",
        )
        v = match_support(
            "The method improves accuracy by 12.4 points over the baseline "
            "[[richfulltext2026]].",
            "richfulltext2026", note,
            judge_fn=lambda prompt: (
                "VERDICT: [SUPPORTS]\n"
                "VERBATIM_SPAN: our method reaches 12.4 points higher accuracy "
                "than the baseline (78.9% vs 66.5%)\n"
                "POLARITY: positive\n"
                "REASONING: quoted verbatim from the Result section.\n"
            ),
        )
        assert v.verdict == "SUPPORTS"
        assert "groundbreaking" not in (v.verbatim_span or "")

    def test_oa_provenance_fields_excluded_from_structured_fields(self, tmp_path):
        """The OA-fulltext-enrichment provenance fields (`read_basis`,
        `full_text_provider`, `oa_status`, `full_text_url`) are stamped as
        flat frontmatter (tier 1) — they are pointers/metadata about HOW the
        note was read, not substantive claim content. They must never reach
        the judge prompt as noise (kz-argus follow-up, PR #184)."""
        from research_vault.gates.support_matcher import _read_note_structured_fields
        lit_dir = tmp_path / "literature"
        lit_dir.mkdir(parents=True)
        note = lit_dir / "provfields2026.md"
        note.write_text(
            "---\ntype: literature\n"
            "read_basis: full-text\n"
            "full_text_provider: unpaywall\n"
            "oa_status: gold\n"
            "full_text_url: https://example.org/paper.pdf\n"
            "---\n"
            "## Result\n"
            "Observed accuracy: 88%.\n",
            encoding="utf-8",
        )
        fields = _read_note_structured_fields(note)
        assert "read_basis" not in fields
        assert "full_text_provider" not in fields
        assert "oa_status" not in fields
        assert "full_text_url" not in fields
        assert any("88" in v for v in fields.values())

    def test_identifier_persistence_fields_excluded_from_structured_fields(self, tmp_path):
        """The identifier-persistence external-id fields (`pmcid`, `openalex`,
        `pmid`, `s2` — sources/identifiers.py; `doi`/`arxiv_id` were already
        denylisted) are provenance/bookkeeping about the paper's identity,
        never substantive claim content the judge should weigh."""
        from research_vault.gates.support_matcher import _read_note_structured_fields
        lit_dir = tmp_path / "literature"
        lit_dir.mkdir(parents=True)
        note = lit_dir / "idfields2026.md"
        note.write_text(
            "---\ntype: literature\n"
            "doi: 10.1234/example\n"
            "arxiv_id: 2005.14165\n"
            "pmcid: PMC1234567\n"
            "openalex: W2741809807\n"
            "pmid: 31000000\n"
            "s2: 215416146\n"
            "---\n"
            "## Result\n"
            "Observed accuracy: 91%.\n",
            encoding="utf-8",
        )
        fields = _read_note_structured_fields(note)
        for key in ("doi", "arxiv_id", "pmcid", "openalex", "pmid", "s2"):
            assert key not in fields, f"{key!r} leaked into judged structured fields"
        assert any("91" in v for v in fields.values())


# ===========================================================================
# 6. Scope extraction — rubric text must not contaminate the parsed verdict
# ===========================================================================

class TestScopeExtraction:
    def test_rubric_example_tokens_do_not_leak_into_verdict(self, tmp_path):
        """The rubric's own instructional examples (e.g. quoting 'ABSENT') must
        not be mistaken for the real VERDICT: line."""
        from research_vault.gates.support_matcher import match_support
        note = _literature_note(tmp_path, "scopetest2024")

        def _judge(prompt: str) -> str:
            # Real rubric text already mentions "[ABSENT]" as an example in the
            # instructions BEFORE the real verdict — the parser must find the
            # actual VERDICT: line, not any bracketed token in the rubric.
            assert "[ABSENT]" in prompt  # sanity: rubric text does mention it
            return "VERDICT: [SUPPORTS]\nVERBATIM_SPAN: Finding A: X is true.\nREASONING: ok\n"

        v = match_support("X holds [[scopetest2024]].", "scopetest2024", note, judge_fn=_judge)
        assert v.verdict == "SUPPORTS"


# ===========================================================================
# 7. J-2 stance escalation
# ===========================================================================

class TestJ2StanceEscalation:
    def test_exploratory_confirmatory_escalates(self, tmp_path):
        note = _literature_note(tmp_path, "exp2023", fields={
            "stance": "exploratory", "tldr": "Exploratory finding X.",
        })
        from research_vault.gates.support_matcher import match_support
        v = match_support(
            "We establish that X is definitively true [[exp2023]].",
            "exp2023", note, stance="exploratory", judge_fn=_mock_judge("SUPPORTS"),
        )
        assert v.j2_escalation
        assert v.blocks


# ===========================================================================
# 8. PLANTED FAILURE — required PR-M3 acceptance test
# ===========================================================================

class TestPlantedUnsupportedClaimIsCaught:
    """The scenario the whole gate exists for: a manuscript claim that has NO
    real support in its cited source. Feeds the judge the REAL note content
    (not a hand-crafted mock verdict) via a judge that discriminates on the
    actual structured-fields text it receives — proving the gate is wired
    end-to-end, not just the dataclass plumbing."""

    def test_planted_unsupported_claim_blocks(self, tmp_path):
        from research_vault.gates.support_matcher import match_support

        # The cited note genuinely says nothing about the planted claim's subject.
        note = _literature_note(
            tmp_path, "plant2024",
            fields={
                "tldr": "This paper studies image classification on CIFAR-10.",
                "findings": "ResNet-50 reaches 94.1% top-1 accuracy on CIFAR-10.",
                "limitations": "Not evaluated on out-of-distribution data.",
            },
        )

        # A claim about an ENTIRELY unrelated subject the note never discusses.
        planted_claim = (
            "The model exhibits emergent multi-hop reasoning across 12 languages "
            "[[plant2024]]."
        )

        # A discriminating mock judge: it actually reads the injected note
        # content (not a canned string) and returns SUPPORTS only if the
        # claim's subject terms appear in the note text; else ABSENT. This is
        # the real disconfirm-first behavior the rubric requires, exercised
        # end-to-end through _build_judge_prompt's field injection.
        def _discriminating_judge(prompt: str) -> str:
            note_block = prompt.split("=== CITED SOURCE")[1] if "=== CITED SOURCE" in prompt else prompt
            if re.search(r"multi-?hop reasoning|12 languages", note_block, re.IGNORECASE):
                return "VERDICT: [SUPPORTS]\nVERBATIM_SPAN: n/a\nREASONING: found it\n"
            return (
                "VERDICT: [ABSENT]\n"
                "VERBATIM_SPAN: NONE\n"
                "REASONING: the note only discusses CIFAR-10 image classification; "
                "no mention of multi-hop reasoning or multilingual evaluation.\n"
            )

        v = match_support(planted_claim, "plant2024", note, judge_fn=_discriminating_judge)

        assert v.verdict == "ABSENT"
        assert v.blocks, "a planted unsupported claim must BLOCK — the gate has teeth"
        assert v.verbatim_span is None
