"""test_gates_coldread.py — PR-M3: gates.coldread (shared, D-SV-0).

Covers the re-instantiated honesty-gates.md principles for the self-
containment cold-read judge, now living in the SHAREABLE
``research_vault.gates`` package (not under manuscript/):

  1. _extract_coldread_verdict — the 3-verdict bracket extractor, distinct
     from support_matcher's 4-verdict set.
  2. Bidirectional canary: a trigger-happy judge (flags the clean probe)
     aborts; a blind judge (waves through the leaky probe) aborts; a
     discriminating judge proceeds.
  3. Flag-A deterministic scan: sha256/hash/artifact-path/abs-path shapes
     are caught independent of any LLM call.
  4. Fail-closed: malformed judge output (no SUMMARY block) -> UNPARSEABLE,
     which callers must treat as a BLOCK, never a pass.
  5. PLANTED FAILURE (required by PR-M3 acceptance): a context-dependent
     passage — one that leaks an internal run id / artifact path a fresh
     reader cannot resolve — is flagged [DANGLING] by the judge AND by the
     deterministic Flag-A scan (belt-and-suspenders).

All hermetic (mock judge_fn). No live LLM calls.
sr: PR-M3
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Helpers — mock judges
# ---------------------------------------------------------------------------

def _clean_response(overall: str = "STANDS-ALONE", block_count: int = 0, warn_count: int = 0) -> str:
    flags = ""
    if block_count > 0:
        for _ in range(block_count):
            flags += (
                "FLAG:\n"
                "VERDICT: [DANGLING]\n"
                'SPAN: "run covers_hash a3f9c1e28b7d46f0"\n'
                "KIND: internal-plumbing\n"
                "WHERE: Section 3\n"
                "MISSING: The covers_hash value is a DAG-internal id not resolvable from the paper.\n\n"
            )
    if warn_count > 0:
        for _ in range(warn_count):
            flags += (
                "FLAG:\n"
                "VERDICT: [NEEDS-CONTEXT]\n"
                'SPAN: "HFS"\n'
                "KIND: thin-explanation\n"
                "WHERE: Section 1\n"
                "MISSING: Acronym defined but explanation is very terse.\n\n"
            )
    return (
        f"{flags}"
        f"SUMMARY:\n"
        f"OVERALL: [{overall}]\n"
        f"BLOCK_COUNT: {block_count}\n"
        f"WARN_COUNT: {warn_count}\n"
        f"SWEPT: Read the full paper as a fresh reader.\n"
    )


def _trigger_happy_judge(prompt: str) -> str:
    return _clean_response(overall="DANGLING", block_count=1)


def _blind_judge(prompt: str) -> str:
    return _clean_response(overall="STANDS-ALONE", block_count=0)


def _discriminating_judge(prompt: str) -> str:
    import re
    m = re.search(r"────+\s*INPUT.*?────+\s*(.*?)────+\s*HARD CONSTRAINTS", prompt, re.DOTALL)
    text_section = m.group(1) if m else prompt
    leaky_patterns = [
        re.compile(r"\bcovers_hash\b"),
        re.compile(r"\bresults/\w+\.csv\b"),
        re.compile(r"\bsha256:"),
        re.compile(r"\b[0-9a-fA-F]{64}\b"),
        re.compile(r"\bnot-recorded-in-provenance\b"),
    ]
    for pat in leaky_patterns:
        if pat.search(text_section):
            return _clean_response(overall="DANGLING", block_count=2)
    return _clean_response(overall="STANDS-ALONE", block_count=0)


# ===========================================================================
# 1. _extract_coldread_verdict — the 3-verdict bracket extractor
# ===========================================================================

class TestExtractColdreadVerdict:
    def test_all_three_tokens_recognized(self):
        from research_vault.gates.coldread import _extract_coldread_verdict
        assert _extract_coldread_verdict("[STANDS-ALONE]") == "STANDS-ALONE"
        assert _extract_coldread_verdict("[DANGLING]") == "DANGLING"
        assert _extract_coldread_verdict("[NEEDS-CONTEXT]") == "NEEDS-CONTEXT"

    def test_support_matcher_tokens_do_not_match(self):
        """Cold-read's 3-verdict extractor is a DIFFERENT domain from support-matcher's 4."""
        from research_vault.gates.coldread import _extract_coldread_verdict
        assert _extract_coldread_verdict("[SUPPORTS]") is None
        assert _extract_coldread_verdict("[ABSENT]") is None


# ===========================================================================
# 2. Bidirectional canary
# ===========================================================================

class TestBidirectionalCanary:
    def test_trigger_happy_judge_aborts(self):
        from research_vault.gates.coldread import run_cold_read
        result = run_cold_read(
            "Some self-contained passage that resolves from the text.",
            judge_fn=_trigger_happy_judge, judge_model="mock-model",
        )
        assert result.canary_aborted is True
        assert "trigger" in result.abort_reason.lower()

    def test_blind_judge_aborts(self):
        from research_vault.gates.coldread import run_cold_read
        result = run_cold_read(
            "Some text to check.", judge_fn=_blind_judge, judge_model="mock-model",
        )
        assert result.canary_aborted is True
        assert "blind" in result.abort_reason.lower()

    def test_discriminating_judge_proceeds(self):
        from research_vault.gates.coldread import run_cold_read
        result = run_cold_read(
            "We evaluate holistic fidelity score (HFS), a 0-100 measure. "
            "As shown in Figure 1, the strongest model reaches an HFS of 71.4. "
            "Section 3 details the scoring procedure. "
            "References: [4] Rivera and Osei 2023.",
            judge_fn=_discriminating_judge, judge_model="mock-model",
        )
        assert result.canary_aborted is False


# ===========================================================================
# 3. Flag-A deterministic scan
# ===========================================================================

class TestFlagAScan:
    def test_sha256_prefix_caught(self):
        from research_vault.gates.coldread import flag_a_scan
        hits = flag_a_scan("The hash is sha256:abc123def456.")
        assert any("sha256" in h.lower() for h in hits)

    def test_bare_hex64_caught(self):
        from research_vault.gates.coldread import flag_a_scan
        hits = flag_a_scan("value=" + "a" * 64)
        assert len(hits) >= 1

    def test_artifact_path_caught(self):
        from research_vault.gates.coldread import flag_a_scan
        hits = flag_a_scan("see results/hfs_by_model.csv for details")
        assert any("artifact-path" in h for h in hits)

    def test_clean_text_no_hits(self):
        from research_vault.gates.coldread import flag_a_scan
        hits = flag_a_scan("This is a perfectly clean sentence about the results.")
        assert hits == []


# ===========================================================================
# 4. Fail-closed: unparseable judge output
# ===========================================================================

class TestFailClosed:
    def test_missing_summary_block_is_unparseable(self):
        from research_vault.gates.coldread import run_cold_read

        # Discriminate by CALL ORDER (canary a, then canary b, then the real
        # call) rather than by content — the rubric itself references
        # "holistic fidelity score (HFS)" as a C4 example in EVERY prompt,
        # so content-sniffing on that phrase is not a safe discriminator.
        _calls = {"n": 0}

        def _garbled_real_judge(prompt: str) -> str:
            _calls["n"] += 1
            if _calls["n"] == 1:
                return _clean_response(overall="STANDS-ALONE", block_count=0)  # canary (a)
            if _calls["n"] == 2:
                return _clean_response(overall="DANGLING", block_count=2)  # canary (b)
            return "no clear structured output here at all"  # the real call

        result = run_cold_read(
            "This is the real paper text under test.",
            judge_fn=_garbled_real_judge, judge_model="mock-model",
        )
        assert result.overall == "UNPARSEABLE"
        assert result.blocks, "UNPARSEABLE must be treated as a BLOCK, never a pass"


# ===========================================================================
# 5. PLANTED FAILURE — required PR-M3 acceptance test
# ===========================================================================

class TestPlantedContextDependentPassageIsFlagged:
    """The scenario cold-read exists for: a passage that leaks internal
    plumbing a fresh reader cannot resolve. Uses the SAME discriminating
    judge shape as the canary tests — end-to-end through run_cold_read,
    proving both the LLM path and the deterministic Flag-A path catch it."""

    def test_planted_leaky_passage_flags_dangling(self):
        from research_vault.gates.coldread import run_cold_read

        planted_passage = (
            "The full effect is reported in run covers_hash "
            "b7d46f0a3f9c1e28b7d46f0a3f9c1e28b7d46f0a3f9c1e28b7d46f0a3f9c1e2, "
            "with the scored outputs available at results/hfs_by_model.csv. "
            "As recorded in provenance, the improvement holds across all seeds; "
            "see the run for the per-seed breakdown."
        )

        result = run_cold_read(
            planted_passage, judge_fn=_discriminating_judge, judge_model="mock-model",
        )

        assert not result.canary_aborted
        assert result.blocks, "a planted context-dependent passage must BLOCK — the gate has teeth"
        assert result.overall == "DANGLING"
        # Belt-and-suspenders: the deterministic Flag-A scan independently
        # catches the same leak, regardless of what the LLM judge said.
        assert len(result.flag_a_hits) > 0

    def test_planted_leaky_passage_caught_by_flag_a_alone(self):
        """Even if the LLM judge were somehow blind, Flag-A independently blocks."""
        from research_vault.gates.coldread import flag_a_scan

        planted_passage = (
            "As recorded in provenance, see results/hfs_by_model.csv for the full table, "
            "run covers_hash b7d46f0a3f9c1e28b7d46f0a3f9c1e28b7d46f0a3f9c1e28b7d46f0a3f9c1e2."
        )
        hits = flag_a_scan(planted_passage)
        assert len(hits) >= 2  # both the covers_hash token AND the artifact path
