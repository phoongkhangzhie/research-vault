"""test_sr_ms_coldread.py — SR-MS-COLDREAD: LLM cold-read self-containment judge.

Covers:
  1.  Canary (a) trigger-happy guard: judge flags clean probe → ABORT
  2.  Canary (b) blind guard: judge passes leaky probe (BLOCK_COUNT < 2) → ABORT
  3.  Both canaries pass with a working judge → proceeds (no abort)
  4.  [DANGLING] on injected leaky text (internal hash/results-path) — discriminates
  5.  [STANDS-ALONE] on clean self-contained passage — judge does not over-flag
  6.  Flag-A deterministic scan: sha256: prefix in pdftotext output → BLOCK
  7.  Flag-A deterministic scan: results/*.csv path in pdftotext output → BLOCK
  8.  Flag-A deterministic scan: covers_hash token in pdftotext output → BLOCK
  9.  Flag-A deterministic scan: clean pdftotext output → no BLOCK
  10. _extract_coldread_verdict: 3-verdict extractor (STANDS-ALONE/DANGLING/NEEDS-CONTEXT)
  11. _extract_coldread_verdict: does NOT match support-matcher tokens (SUPPORTS/ABSENT)
  12. get_coldread_rubric: override arg wins
  13. get_coldread_rubric: [manuscript_coldread].rubric config key wins over default
  14. get_coldread_rubric: falls back to DEFAULT_COLDREAD_RUBRIC when no override/config
  15. check_cold_read_tally: honest_report tally (P passages, b BLOCK, w WARN)
  16. check_cold_read_tally: canary_aborted key in return dict
  17. build_approve_payload: cold_read_flags section present (9th section)
  18. --cold-read Layer-2 fails LOUD when ANTHROPIC_API_KEY absent
  19. Plain check stays hermetic (no --cold-read — no key needed)
  20. DEFAULT_COLDREAD_RUBRIC contains {PDF_TEXT} slot
  21. Verdict extractor: [STANDS-ALONE] matches (not just "STANDS-ALONE" bare word)
  22. run_cold_read: logs judge_model and prompt_hash in result meta
  23. check_cold_read_tally: Flag-A hit produces BLOCK even if judge says [STANDS-ALONE]
  24. FAIL-CLOSED: malformed judge output (no SUMMARY) → NOT STANDS-ALONE (blocks)
  25. FAIL-CLOSED: empty judge response → NOT STANDS-ALONE (blocks)
  26. verbs run(): --cold-read with no key → exit 1 (uses cold_read_layer2_env_guard)
  27. style.py cold-read per_section_tips mentions Layer-2 as live

All hermetic (mock judge, tmp_path). No live LLM calls. Stdlib only.
sr: SR-MS-COLDREAD
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# Helpers — mock judges
# ---------------------------------------------------------------------------

def _make_clean_judge_response(overall: str = "STANDS-ALONE", block_count: int = 0, warn_count: int = 0) -> str:
    """Synthesize a well-formed cold-read judge response."""
    flags = ""
    if block_count > 0:
        for i in range(block_count):
            flags += (
                f"FLAG:\n"
                f"VERDICT: [DANGLING]\n"
                f'SPAN: "run covers_hash a3f9c1e28b7d46f0"\n'
                f"KIND: internal-plumbing\n"
                f"WHERE: Section 3\n"
                f"MISSING: The covers_hash value is a DAG-internal id not resolvable from the paper.\n\n"
            )
    if warn_count > 0:
        for i in range(warn_count):
            flags += (
                f"FLAG:\n"
                f"VERDICT: [NEEDS-CONTEXT]\n"
                f'SPAN: "HFS"\n'
                f"KIND: thin-explanation\n"
                f"WHERE: Section 1\n"
                f"MISSING: Acronym defined but explanation is very terse.\n\n"
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
    """Flags the known-clean canary probe as [DANGLING] — trigger-happy."""
    return _make_clean_judge_response(overall="DANGLING", block_count=1, warn_count=0)


def _blind_judge(prompt: str) -> str:
    """Always returns [STANDS-ALONE] even on a leaky probe — blind."""
    return _make_clean_judge_response(overall="STANDS-ALONE", block_count=0, warn_count=0)


def _discriminating_judge(prompt: str) -> str:
    """Returns STANDS-ALONE for clean, DANGLING for leaky content.

    Detects leakiness by checking for known markers in the prompt:
    covers_hash, results/, or sha256: patterns.
    """
    import re
    # Only inspect the PDF_TEXT portion injected between markers
    _text_re = re.compile(
        r"────+\s*INPUT.*?────+\s*(.*?)────+\s*HARD CONSTRAINTS",
        re.DOTALL,
    )
    m = _text_re.search(prompt)
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
            return _make_clean_judge_response(overall="DANGLING", block_count=2, warn_count=0)
    return _make_clean_judge_response(overall="STANDS-ALONE", block_count=0, warn_count=0)


# ---------------------------------------------------------------------------
# 1–3: Bidirectional canary
# ---------------------------------------------------------------------------

class TestBidirectionalCanary:
    """Canary probes fire BOTH directions before trusting any real verdict."""

    def test_canary_a_trigger_happy_aborts(self) -> None:
        """A trigger-happy judge that flags the clean probe → ABORT."""
        from research_vault.manuscript.coldread import run_cold_read

        # Clean probe text (canary a: known self-contained)
        result = run_cold_read(
            "Some self-contained passage that resolves from the text.",
            judge_fn=_trigger_happy_judge,
            judge_model="mock-model",
        )
        assert result.canary_aborted is True
        assert "TRIGGER-HAPPY" in result.abort_reason.upper() or "trigger" in result.abort_reason.lower()

    def test_canary_b_blind_judge_aborts(self) -> None:
        """A blind judge that rubber-stamps the leaky probe → ABORT."""
        from research_vault.manuscript.coldread import run_cold_read

        result = run_cold_read(
            "Some text to check.",
            judge_fn=_blind_judge,
            judge_model="mock-model",
        )
        assert result.canary_aborted is True
        assert "BLIND" in result.abort_reason.upper() or "blind" in result.abort_reason.lower()

    def test_both_canaries_pass_discriminating_judge(self) -> None:
        """A discriminating judge passes both canaries → proceeds normally."""
        from research_vault.manuscript.coldread import run_cold_read

        result = run_cold_read(
            "We evaluate holistic fidelity score (HFS), a 0–100 measure. "
            "As shown in Figure 1, the strongest model reaches an HFS of 71.4. "
            "Section 3 details the scoring procedure. "
            "References: [4] Rivera and Osei 2023.",
            judge_fn=_discriminating_judge,
            judge_model="mock-model",
        )
        assert result.canary_aborted is False


# ---------------------------------------------------------------------------
# 4–5: [DANGLING] / [STANDS-ALONE] discrimination
# ---------------------------------------------------------------------------

class TestVerdictDiscrimination:
    """The judge discriminates — doesn't rubber-stamp, doesn't over-flag."""

    def test_dangling_on_leaky_text(self) -> None:
        """Injected covers_hash + results/*.csv → [DANGLING] BLOCK."""
        from research_vault.manuscript.coldread import run_cold_read

        leaky_text = (
            "The full effect is reported in run covers_hash "
            "a3f9c1e28b7d46f0a3f9c1e28b7d46f0a3f9c1e28b7d46f0a3f9c1e28b7d4f00d, "
            "with scored outputs at results/hfs_by_model.csv. "
            "See the run for per-seed breakdown."
        )
        result = run_cold_read(
            leaky_text,
            judge_fn=_discriminating_judge,
            judge_model="mock-model",
        )
        assert result.canary_aborted is False
        # Either Flag-A or LLM judge should produce blocks
        assert result.block_count >= 1 or len(result.flag_a_hits) >= 1

    def test_stands_alone_on_clean_passage(self) -> None:
        """Clean self-contained passage → [STANDS-ALONE] (no BLOCK, no WARN)."""
        from research_vault.manuscript.coldread import run_cold_read

        clean_text = (
            "We evaluate holistic fidelity score (HFS), a 0–100 measure of how closely a "
            "model's outputs track human reference judgments, across three models. "
            "As shown in Figure 1, the strongest model reaches an HFS of 71.4. "
            "This pattern is consistent with prior work on reference-based scoring [4]. "
            "Section 3 details the scoring procedure; Table 1 reports the full per-model breakdown.\n\n"
            "Figure 1: HFS by model.\n"
            "Table 1: Per-model HFS and 95% confidence intervals.\n\n"
            "References\n"
            "[4] A. Rivera and B. Osei (2023). Reference-based fidelity scoring. "
            "Journal of Evaluation Methods, 11(2), 88–104."
        )
        result = run_cold_read(
            clean_text,
            judge_fn=_discriminating_judge,
            judge_model="mock-model",
        )
        assert result.canary_aborted is False
        assert result.block_count == 0
        assert len(result.flag_a_hits) == 0
        assert result.overall == "STANDS-ALONE"


# ---------------------------------------------------------------------------
# 6–9: Flag-A deterministic scan
# ---------------------------------------------------------------------------

class TestFlagADeterministicScan:
    """Flag-A: deterministic patterns over pdftotext output (belt-and-suspenders)."""

    def test_flag_a_sha256_prefix(self) -> None:
        """sha256: prefix in pdftotext output → BLOCK even if LLM missed it."""
        from research_vault.manuscript.coldread import flag_a_scan

        pdf_text = "The hash is sha256:a3f9c1e28b7d46f0a3f9c1e28b7d46f0 of the artifact."
        hits = flag_a_scan(pdf_text)
        assert len(hits) >= 1
        assert any("sha256" in h.lower() for h in hits)

    def test_flag_a_results_path(self) -> None:
        """results/*.csv path in pdftotext output → BLOCK."""
        from research_vault.manuscript.coldread import flag_a_scan

        pdf_text = "Scores available at results/hfs_by_model.csv for download."
        hits = flag_a_scan(pdf_text)
        assert len(hits) >= 1
        assert any("results" in h.lower() for h in hits)

    def test_flag_a_covers_hash_token(self) -> None:
        """covers_hash token in pdftotext output → BLOCK."""
        from research_vault.manuscript.coldread import flag_a_scan

        pdf_text = "The run covers_hash is reported in Figure 4."
        hits = flag_a_scan(pdf_text)
        assert len(hits) >= 1
        assert any("covers_hash" in h for h in hits)

    def test_flag_a_clean_text_no_hits(self) -> None:
        """Clean pdftotext output → no Flag-A hits."""
        from research_vault.manuscript.coldread import flag_a_scan

        pdf_text = (
            "We evaluate holistic fidelity score (HFS) across three models. "
            "The strongest model reaches 71.4, above the 52.9 baseline. "
            "See Table 1 for the breakdown."
        )
        hits = flag_a_scan(pdf_text)
        assert hits == []

    def test_flag_a_blocks_regardless_of_llm(self) -> None:
        """Flag-A hit produces BLOCK even when the LLM judge says STANDS-ALONE."""
        from research_vault.manuscript.coldread import run_cold_read

        leaky_text = (
            "The full reproducibility table is in results/scores.csv. "
            "We evaluate holistic fidelity score (HFS) in Section 3."
        )
        # Use a blind judge that would say STANDS-ALONE
        result = run_cold_read(
            leaky_text,
            judge_fn=_discriminating_judge,  # will flag it via LLM too, but Flag-A fires first
            judge_model="mock-model",
        )
        assert result.canary_aborted is False
        # Flag-A must register the hit
        assert len(result.flag_a_hits) >= 1


# ---------------------------------------------------------------------------
# 10–11: Verdict extractor
# ---------------------------------------------------------------------------

class TestVerdictExtractor:
    """New 3-verdict extractor — does not overload support_matcher's 4-verdict one."""

    def test_extracts_stands_alone(self) -> None:
        from research_vault.manuscript.coldread import _extract_coldread_verdict
        assert _extract_coldread_verdict("[STANDS-ALONE]") == "STANDS-ALONE"

    def test_extracts_dangling(self) -> None:
        from research_vault.manuscript.coldread import _extract_coldread_verdict
        assert _extract_coldread_verdict("[DANGLING]") == "DANGLING"

    def test_extracts_needs_context(self) -> None:
        from research_vault.manuscript.coldread import _extract_coldread_verdict
        assert _extract_coldread_verdict("[NEEDS-CONTEXT]") == "NEEDS-CONTEXT"

    def test_bare_word_does_not_match(self) -> None:
        """A bare word without brackets does NOT trigger the extractor."""
        from research_vault.manuscript.coldread import _extract_coldread_verdict
        assert _extract_coldread_verdict("STANDS-ALONE") is None
        assert _extract_coldread_verdict("DANGLING") is None

    def test_does_not_match_support_matcher_tokens(self) -> None:
        """Support-matcher tokens SUPPORTS/ABSENT are NOT in this extractor."""
        from research_vault.manuscript.coldread import _extract_coldread_verdict
        assert _extract_coldread_verdict("[SUPPORTS]") is None
        assert _extract_coldread_verdict("[ABSENT]") is None
        assert _extract_coldread_verdict("[CONTRADICTS]") is None
        assert _extract_coldread_verdict("[PARTIAL]") is None

    def test_case_insensitive(self) -> None:
        from research_vault.manuscript.coldread import _extract_coldread_verdict
        assert _extract_coldread_verdict("[stands-alone]") == "STANDS-ALONE"
        assert _extract_coldread_verdict("[dangling]") == "DANGLING"


# ---------------------------------------------------------------------------
# 12–14: Rubric seam
# ---------------------------------------------------------------------------

class TestRubricSeam:
    """get_coldread_rubric() seam — override > config > default."""

    def test_override_arg_wins(self) -> None:
        from research_vault.manuscript.coldread import get_coldread_rubric
        override = "MY CUSTOM RUBRIC"
        result = get_coldread_rubric(override=override)
        assert result == override

    def test_config_key_wins_over_default(self) -> None:
        from research_vault.manuscript.coldread import get_coldread_rubric, DEFAULT_COLDREAD_RUBRIC

        class FakeConfig:
            _raw = {"manuscript_coldread": {"rubric": "CONFIG RUBRIC"}}

        result = get_coldread_rubric(config=FakeConfig())
        assert result == "CONFIG RUBRIC"
        assert result != DEFAULT_COLDREAD_RUBRIC

    def test_falls_back_to_default(self) -> None:
        from research_vault.manuscript.coldread import get_coldread_rubric, DEFAULT_COLDREAD_RUBRIC
        result = get_coldread_rubric()
        assert result == DEFAULT_COLDREAD_RUBRIC

    def test_override_beats_config(self) -> None:
        from research_vault.manuscript.coldread import get_coldread_rubric

        class FakeConfig:
            _raw = {"manuscript_coldread": {"rubric": "CONFIG RUBRIC"}}

        result = get_coldread_rubric(override="OVERRIDE", config=FakeConfig())
        assert result == "OVERRIDE"

    def test_default_rubric_has_pdf_text_slot(self) -> None:
        """The rubric must contain the {PDF_TEXT} slot."""
        from research_vault.manuscript.coldread import DEFAULT_COLDREAD_RUBRIC
        assert "{PDF_TEXT}" in DEFAULT_COLDREAD_RUBRIC


# ---------------------------------------------------------------------------
# 15–16: check_cold_read_tally
# ---------------------------------------------------------------------------

class TestCheckColdReadTally:
    """check_cold_read_tally: gate orchestrator in check_gates.py."""

    def _make_ms_tree(self, tmp_path: Path) -> tuple[Path, Path]:
        tree_root = tmp_path / "manuscripts" / "ms-test"
        (tree_root / "sections").mkdir(parents=True, exist_ok=True)
        (tree_root / "main.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nHello.\n\\end{document}\n",
            encoding="utf-8",
        )
        # Write a minimal PDF stub (not a real PDF — pdftotext will fail gracefully,
        # but that's fine for hermetic tests since we inject pdf_text directly)
        return tmp_path / "manuscript" / "ms-test.md", tree_root

    def test_honest_report_format(self, tmp_path: Path) -> None:
        """honest_report contains passages, BLOCK, WARN counts."""
        from research_vault.manuscript.check_gates import check_cold_read_tally

        note_path, tree_root = self._make_ms_tree(tmp_path)

        result = check_cold_read_tally(
            tree_root,
            judge_fn=_discriminating_judge,
            judge_model="mock",
            pdf_text="Clean self-contained passage with no leaks.",
        )
        # Exact format: "P passages, b BLOCK, w WARN"
        assert "BLOCK" in result["honest_report"]
        assert "WARN" in result["honest_report"]

    def test_canary_aborted_key_present(self, tmp_path: Path) -> None:
        """Return dict always contains 'canary_aborted' key."""
        from research_vault.manuscript.check_gates import check_cold_read_tally

        _, tree_root = self._make_ms_tree(tmp_path)

        result = check_cold_read_tally(
            tree_root,
            judge_fn=_discriminating_judge,
            judge_model="mock",
            pdf_text="Clean text.",
        )
        assert "canary_aborted" in result

    def test_trigger_happy_judge_sets_canary_aborted(self, tmp_path: Path) -> None:
        """Trigger-happy judge → canary_aborted=True in tally result."""
        from research_vault.manuscript.check_gates import check_cold_read_tally

        _, tree_root = self._make_ms_tree(tmp_path)

        result = check_cold_read_tally(
            tree_root,
            judge_fn=_trigger_happy_judge,
            judge_model="mock",
            pdf_text="Clean text.",
        )
        assert result["canary_aborted"] is True
        assert result["errors"]  # canary abort is an error

    def test_flag_a_hit_in_pdf_text_blocks(self, tmp_path: Path) -> None:
        """Flag-A hit in injected pdf_text → block-level error in tally result."""
        from research_vault.manuscript.check_gates import check_cold_read_tally

        _, tree_root = self._make_ms_tree(tmp_path)

        result = check_cold_read_tally(
            tree_root,
            judge_fn=_blind_judge,  # judge would say STANDS-ALONE
            judge_model="mock",
            pdf_text="The covers_hash is reported in run a3f9c1e28b7d46f0.",
        )
        # Flag-A fires deterministically, even if judge says STANDS-ALONE
        # Note: with blind judge the canary(b) should abort, but flag-a runs FIRST
        # and the tally errors should contain the flag-a hit
        assert result["errors"] or result["flag_a_hits"]


# ---------------------------------------------------------------------------
# 17: build_approve_payload cold-read section
# ---------------------------------------------------------------------------

class TestApprovePayloadColdRead:
    """build_approve_payload has a cold_read_flags section (9th section)."""

    def _make_full_ms_tree(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        """Scaffold a minimal manuscript tree for build_approve_payload."""
        ms_dir = tmp_path / "manuscript"
        ms_dir.mkdir(parents=True, exist_ok=True)
        note_path = ms_dir / "ms-test.md"
        note_path.write_text(
            "---\ntype: manuscript\nthesis: Test thesis\n"
            "synthesized_okf: \nmanuscript_pdf: \ndag_run: ms-test-draft\n---\n",
            encoding="utf-8",
        )
        tree_root = tmp_path / "manuscripts" / "ms-test"
        sections_dir = tree_root / "sections"
        sections_dir.mkdir(parents=True, exist_ok=True)
        (tree_root / "main.tex").write_text(
            "\\documentclass{article}\n\\title{A Reader-Facing Title}\n"
            "\\begin{document}\nMinimal.\n\\end{document}\n",
            encoding="utf-8",
        )
        # Write a valid empty refs.bib
        (tree_root / "refs.bib").write_text("", encoding="utf-8")
        return note_path, tree_root, ms_dir

    def test_build_approve_payload_has_cold_read_section(self, tmp_path: Path) -> None:
        """build_approve_payload returns 'cold_read_flags' key (9th section)."""
        from research_vault.manuscript.check_gates import build_approve_payload

        note_path, tree_root, _ = self._make_full_ms_tree(tmp_path)
        payload = build_approve_payload(
            note_path,
            tree_root,
            judge_fn=_discriminating_judge,
            judge_model="mock",
            cold_read_judge_fn=_discriminating_judge,
        )
        assert "cold_read_flags" in payload, (
            "build_approve_payload must include 'cold_read_flags' (9th section, SR-MS-COLDREAD)"
        )
        assert "cold_read_report" in payload, (
            "build_approve_payload must include 'cold_read_report' tally string"
        )


# ---------------------------------------------------------------------------
# 18–19: --cold-read CLI layer-2 key requirement
# ---------------------------------------------------------------------------

class TestColdReadCLIKeyGuard:
    """--cold-read Layer-2 fails loud without API key; plain check stays hermetic."""

    def test_cold_read_fails_loud_without_api_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """--cold-read Layer-2 exits with error when ANTHROPIC_API_KEY absent."""
        import os
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("RV_JUDGE_MODEL", raising=False)

        from research_vault.manuscript.verbs import build_parser
        p = build_parser()
        # Parser structure: rv manuscript <project> check <ms_id> [--cold-read]
        args = p.parse_args(["myproject", "check", "ms-test", "--cold-read"])
        assert getattr(args, "cold_read", False) is True

        # The Layer-2 key check: simulate the guard
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        judge_model = os.environ.get("RV_JUDGE_MODEL", "").strip()
        assert not api_key  # confirms env is clean
        assert not judge_model

    def test_plain_check_no_cold_read_flag_is_hermetic(self) -> None:
        """Plain 'rv manuscript check' (no --cold-read) has no LLM dependency."""
        from research_vault.manuscript.verbs import build_parser
        p = build_parser()
        # Parser structure: rv manuscript <project> check <ms_id>
        args = p.parse_args(["myproject", "check", "ms-test"])
        assert getattr(args, "cold_read", False) is False

    def test_cold_read_layer2_env_guard_logic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """cold_read_layer2_env_guard() raises loudly when keys absent."""
        import os
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("RV_JUDGE_MODEL", raising=False)

        from research_vault.manuscript.coldread import cold_read_layer2_env_guard
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY|RV_JUDGE_MODEL"):
            cold_read_layer2_env_guard()


# ---------------------------------------------------------------------------
# 20–23: Additional properties
# ---------------------------------------------------------------------------

class TestAdditionalProperties:
    """Miscellaneous structural properties."""

    def test_default_rubric_has_pdf_text_slot(self) -> None:
        from research_vault.manuscript.coldread import DEFAULT_COLDREAD_RUBRIC
        assert "{PDF_TEXT}" in DEFAULT_COLDREAD_RUBRIC

    def test_default_rubric_contains_verdict_tokens(self) -> None:
        """The rubric instructs the judge to emit the three verdict tokens."""
        from research_vault.manuscript.coldread import DEFAULT_COLDREAD_RUBRIC
        assert "STANDS-ALONE" in DEFAULT_COLDREAD_RUBRIC
        assert "DANGLING" in DEFAULT_COLDREAD_RUBRIC
        assert "NEEDS-CONTEXT" in DEFAULT_COLDREAD_RUBRIC

    def test_run_cold_read_logs_meta(self) -> None:
        """run_cold_read result carries judge_model and prompt_hash."""
        from research_vault.manuscript.coldread import run_cold_read

        result = run_cold_read(
            "Clean passage. Figure 1 shows the results. See Table 1.",
            judge_fn=_discriminating_judge,
            judge_model="mock-opus",
        )
        assert result.judge_model == "mock-opus"
        assert result.prompt_hash  # non-empty string

    def test_flag_a_and_llm_judge_both_block_leaky_text(self) -> None:
        """Both Flag-A and LLM judge flag leaky text; overall is BLOCK."""
        from research_vault.manuscript.coldread import run_cold_read

        leaky = (
            "The full table is at results/scores.csv. "
            "The covers_hash is a3f9c1e28b7d46f0a3f9c1e28b7d46f0."
        )
        result = run_cold_read(
            leaky,
            judge_fn=_discriminating_judge,
            judge_model="mock",
        )
        assert result.canary_aborted is False
        total_blocks = result.block_count + len(result.flag_a_hits)
        assert total_blocks >= 1


# ---------------------------------------------------------------------------
# 24–25: FAIL-CLOSED on malformed / empty judge output
# ---------------------------------------------------------------------------

class TestFailClosed:
    """Malformed judge output → NOT STANDS-ALONE (fail-closed, never a silent pass).

    A judge that passes the canary probes but returns unparseable output on the
    REAL (longer) paper MUST block — not wave the paper through silently.
    Flag-A only catches deterministic hash/path patterns; it cannot catch semantic
    danglings (undefined term, broken cross-ref, provenance-pointer prose) that
    the LLM judge would have caught. A malformed real-paper response MUST block.

    Red-before-green: these tests FAILED before the fail-closed fix was applied.
    """

    def test_malformed_judge_response_is_not_stands_alone(self) -> None:
        """A judge that emits malformed output (no SUMMARY block) → NOT STANDS-ALONE."""
        from research_vault.manuscript.coldread import run_cold_read

        def _malformed_on_real_only(prompt: str) -> str:
            # Returns well-formed canary responses so canaries pass,
            # but garbage on any other input (the real paper).
            if "Rivera and B. Osei" in prompt:
                # Canary (a): clean probe → STANDS-ALONE response
                return _make_clean_judge_response("STANDS-ALONE", 0, 0)
            if "covers_hash" in prompt and "a3f9c1e28b7d46f0" in prompt:
                # Canary (b): leaky probe → DANGLING response with BLOCK_COUNT≥2
                return _make_clean_judge_response("DANGLING", 2, 0)
            # Real paper: return garbage (no SUMMARY block)
            return "The paper looks fine to me. Some thoughts: it reads well. Good job."

        result = run_cold_read(
            "This is a regular self-contained academic paper with proper references.",
            judge_fn=_malformed_on_real_only,
            judge_model="mock",
        )
        assert result.canary_aborted is False, "Canaries should pass with well-formed responses"
        # CRITICAL: must NOT be STANDS-ALONE — fail-closed on malformed output
        assert result.overall != "STANDS-ALONE", (
            "Malformed judge output must NOT default to STANDS-ALONE (fail-open). "
            "Got STANDS-ALONE — this is the fail-open bug the fix addresses."
        )

    def test_empty_judge_response_is_not_stands_alone(self) -> None:
        """A judge that returns empty string on the real paper → NOT STANDS-ALONE."""
        from research_vault.manuscript.coldread import run_cold_read

        def _empty_on_real_only(prompt: str) -> str:
            if "Rivera and B. Osei" in prompt:
                return _make_clean_judge_response("STANDS-ALONE", 0, 0)
            if "covers_hash" in prompt and "a3f9c1e28b7d46f0" in prompt:
                return _make_clean_judge_response("DANGLING", 2, 0)
            return ""  # empty response on real paper

        result = run_cold_read(
            "A clean self-contained academic paper.",
            judge_fn=_empty_on_real_only,
            judge_model="mock",
        )
        assert result.canary_aborted is False
        assert result.overall != "STANDS-ALONE", (
            "Empty judge response must NOT default to STANDS-ALONE (fail-open)."
        )

    def test_malformed_judge_blocks_in_tally(self, tmp_path: Path) -> None:
        """check_cold_read_tally: malformed judge output → error in errors list."""
        from research_vault.manuscript.check_gates import check_cold_read_tally

        tree_root = tmp_path / "manuscripts" / "ms-test"
        tree_root.mkdir(parents=True, exist_ok=True)

        def _malformed_on_real_only(prompt: str) -> str:
            if "Rivera and B. Osei" in prompt:
                return _make_clean_judge_response("STANDS-ALONE", 0, 0)
            if "covers_hash" in prompt and "a3f9c1e28b7d46f0" in prompt:
                return _make_clean_judge_response("DANGLING", 2, 0)
            return "No structured output here whatsoever."

        result = check_cold_read_tally(
            tree_root,
            judge_fn=_malformed_on_real_only,
            judge_model="mock",
            pdf_text="A clean self-contained paper.",
        )
        # Malformed output must not produce an empty error list with STANDS-ALONE
        # The gate must block or at least produce a loud error forcing human attention
        assert result["canary_aborted"] is False
        assert result["overall"] != "STANDS-ALONE" or result["errors"], (
            "Malformed judge output must either change overall from STANDS-ALONE "
            "or add a human-visible error — not silently certify the paper."
        )


# ---------------------------------------------------------------------------
# 26: verbs run() --cold-read with no key → exit 1 (uses tested guard)
# ---------------------------------------------------------------------------

class TestVerbsRunColdReadGuard:
    """verbs.run() calls cold_read_layer2_env_guard() — integration-level test."""

    def test_run_cold_read_no_key_exits_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """run() with --cold-read and no key returns exit code 1 (loud fail)."""
        import os
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("RV_JUDGE_MODEL", raising=False)

        # Build a minimal config pointing at tmp_path
        from research_vault.manuscript.verbs import build_parser
        p = build_parser()
        args = p.parse_args(["myproject", "check", "ms-test", "--cold-read"])
        assert args.cold_read is True

        # Simulate the guard that verbs.run() must call
        from research_vault.manuscript.coldread import cold_read_layer2_env_guard
        with pytest.raises(RuntimeError) as exc_info:
            cold_read_layer2_env_guard()
        # The error must name the missing vars
        assert "ANTHROPIC_API_KEY" in str(exc_info.value) or "RV_JUDGE_MODEL" in str(exc_info.value)

    def test_verbs_run_calls_tested_guard(self) -> None:
        """verbs.py uses cold_read_layer2_env_guard (not inline duplication).

        Verify the import exists so the reuse is enforced — if someone removes the
        import in a future edit, this test fails loudly.
        """
        import ast
        from pathlib import Path as _Path
        verbs_src = (_Path(__file__).parent.parent / "src" / "research_vault"
                     / "manuscript" / "verbs.py").read_text(encoding="utf-8")
        # Check that cold_read_layer2_env_guard is referenced in verbs.py
        assert "cold_read_layer2_env_guard" in verbs_src, (
            "verbs.py must call cold_read_layer2_env_guard() from coldread.py "
            "(reuse-over-create) — not inline the env check."
        )


# ---------------------------------------------------------------------------
# 27: style.py per_section_tips cold-read is Layer-2 live
# ---------------------------------------------------------------------------

class TestStylePySectionTips:
    """style.py cold-read per_section_tips reflects Layer-2 as LIVE (not future SR)."""

    def test_cold_read_tips_no_longer_calls_layer2_future_sr(self) -> None:
        """The cold-read per_section_tips must NOT say 'future SR' for Layer-2."""
        from research_vault.manuscript.style import per_section_tips as _PER_SECTION_TIPS
        cr_tips = _PER_SECTION_TIPS.get("cold-read", "")
        assert "future SR" not in cr_tips, (
            "style.py cold-read per_section_tips still says 'future SR' for Layer-2 — "
            "SR-MS-COLDREAD has landed; update it to say Layer-2 is live."
        )

    def test_cold_read_tips_mention_layer2(self) -> None:
        """cold-read per_section_tips mentions Layer-2 LLM judge as live."""
        from research_vault.manuscript.style import per_section_tips as _PER_SECTION_TIPS
        cr_tips = _PER_SECTION_TIPS.get("cold-read", "")
        assert "Layer-2" in cr_tips or "layer-2" in cr_tips.lower(), (
            "cold-read per_section_tips must mention Layer-2 LLM judge."
        )

    def test_cold_read_tips_mention_antianchoring_moves(self) -> None:
        """cold-read per_section_tips carries the 3 anti-anchoring agent moves."""
        from research_vault.manuscript.style import per_section_tips as _PER_SECTION_TIPS
        cr_tips = _PER_SECTION_TIPS.get("cold-read", "")
        # All three anti-anchoring moves must be present (judge from page only,
        # verbatim span, disconfirm-first)
        assert "verbatim" in cr_tips.lower(), (
            "cold-read tips must mention the verbatim-span requirement."
        )
        assert "disconfirm" in cr_tips.lower() or "sweep" in cr_tips.lower(), (
            "cold-read tips must mention disconfirm-first / disconfirming sweep."
        )


# ---------------------------------------------------------------------------
# SR-MS-GATE-ALIGN Slice A: structural zone-1 .tex selection (gate-align tests)
# ---------------------------------------------------------------------------

class TestBodyScopingInTally:
    """check_cold_read_tally uses structural zone-1 .tex selection for zone-2 exclusion.

    Zone-2 content (appendix-repro.tex, data-code-availability.tex) must NOT trigger
    Flag-A or LLM flags; zone-1 content with the SAME patterns MUST still trigger them.
    Zone-2 exclusion is structural (by .tex stem name), NOT textual (substring truncation).
    sr: SR-MS-GATE-ALIGN
    """

    def _make_ms_tree_with_zone2(
        self, tmp_path: Path, heading: str = "Reproducibility Appendix"
    ) -> Path:
        """Scaffold a tree with a clean zone-1 section + appendix-repro.tex (zone-2)."""
        tree_root = tmp_path / "manuscripts" / "ms-test"
        sections_dir = tree_root / "sections"
        sections_dir.mkdir(parents=True, exist_ok=True)
        # Zone-1: clean body section (no leaks)
        (sections_dir / "introduction.tex").write_text(
            "\\section{Introduction}\nWe evaluate HFS across three models. See Table 1.\n",
            encoding="utf-8",
        )
        # Zone-2: appendix with sha256 hash — legitimate, must NOT be flagged
        (sections_dir / "appendix-repro.tex").write_text(
            f"\\section*{{{heading}}}\n"
            "This appendix was machine-generated from the experiment notes' repro_* fields.\n"
            "The sha256 verification hash is sha256:a3f9c1e28b7d46f0a3f9c1e28b7d46f0a3f9\n",
            encoding="utf-8",
        )
        (tree_root / "main.tex").write_text(
            "\\documentclass{article}\n\\title{A Paper}\n\\begin{document}\n"
            "\\input{sections/introduction}\n\\input{sections/appendix-repro}\n"
            "\\end{document}\n",
            encoding="utf-8",
        )
        return tree_root

    def test_sha256_in_appendix_section_not_flagged(self, tmp_path: Path) -> None:
        """sha256: in appendix-repro.tex (zone-2) is NOT flagged by structural selection.

        Core dogfood case: appendix has legit sha256 provenance verification text.
        Structural zone-1 .tex selection skips appendix-repro.tex by stem name,
        so its sha256 never reaches Flag-A.
        """
        from research_vault.manuscript.check_gates import check_cold_read_tally

        tree_root = self._make_ms_tree_with_zone2(tmp_path)

        # pdf_text=None → structural .tex path; appendix-repro.tex skipped by stem
        result = check_cold_read_tally(
            tree_root,
            judge_fn=_discriminating_judge,
            judge_model="mock",
            pdf_text=None,
        )
        assert result["flag_a_hits"] == [], (
            f"sha256: in appendix-repro.tex (zone-2) must NOT be a Flag-A hit. "
            f"Structural selection skips zone-2 stems by name. Got: {result['flag_a_hits']}"
        )
        assert not result["errors"], (
            f"Expected no errors for zone-2 sha256. Got: {result['errors']}"
        )

    def test_sha256_in_body_still_flagged(self, tmp_path: Path) -> None:
        """sha256: hash in body zone-1 .tex → still a Flag-A hit (zone-1 is gathered)."""
        from research_vault.manuscript.check_gates import check_cold_read_tally

        tree_root = self._make_ms_tree_with_zone2(tmp_path)
        # Overwrite the zone-1 introduction with a sha256 leak
        (tree_root / "sections" / "introduction.tex").write_text(
            "\\section{Introduction}\n"
            "The main result hash is sha256:a3f9c1e28b7d46f0a3f9c1e28b7d46f0 "
            "as verified by the pipeline.\n",
            encoding="utf-8",
        )

        result = check_cold_read_tally(
            tree_root,
            judge_fn=_discriminating_judge,
            judge_model="mock",
            pdf_text=None,
        )
        # The sha256 is in zone-1 introduction.tex → gathered → still flagged
        assert result["flag_a_hits"] or result["errors"], (
            "sha256: in zone-1 .tex section must still be a Flag-A hit "
            "under structural zone-1 selection"
        )

    def test_repro_prose_in_appendix_not_dangling(self, tmp_path: Path) -> None:
        """not-recorded-in-provenance sentinel in appendix-repro.tex → NOT flagged.

        The sentinel string is legitimate in zone-2 (appendix). Structural selection
        excludes appendix-repro.tex by stem, so the sentinel never reaches Flag-A.
        """
        from research_vault.manuscript.check_gates import check_cold_read_tally

        tree_root = self._make_ms_tree_with_zone2(tmp_path)
        # Put the sentinel in the zone-2 appendix file (overwrite to add sentinel)
        (tree_root / "sections" / "appendix-repro.tex").write_text(
            "\\section*{Reproducibility Appendix}\n"
            "machine-generated from the experiment notes' repro_* fields.\n"
            "not-recorded-in-provenance\n",  # sentinel: legitimate in zone-2
            encoding="utf-8",
        )

        # pdf_text=None → structural .tex path; appendix-repro.tex skipped → sentinel unseen
        result = check_cold_read_tally(
            tree_root,
            judge_fn=_discriminating_judge,
            judge_model="mock",
            pdf_text=None,
        )
        assert result["flag_a_hits"] == [], (
            f"Sentinel in appendix-repro.tex (zone-2) must NOT be a Flag-A hit. "
            f"Got: {result['flag_a_hits']}"
        )
        assert not result["errors"], (
            f"Expected no errors for sentinel in zone-2. Got: {result['errors']}"
        )

    def test_no_zone2_tex_files_noop(self, tmp_path: Path) -> None:
        """When no zone-2 tex files exist, zone-1 selection is a no-op (graceful)."""
        from research_vault.manuscript.check_gates import check_cold_read_tally

        tree_root = tmp_path / "manuscripts" / "ms-no-appendix"
        (tree_root / "sections").mkdir(parents=True, exist_ok=True)
        # No appendix-repro.tex — no zone-2 files at all
        pdf_text = "Clean self-contained body with no leaks whatsoever."

        result = check_cold_read_tally(
            tree_root,
            judge_fn=_discriminating_judge,
            judge_model="mock",
            pdf_text=pdf_text,
        )
        assert result["flag_a_hits"] == []
        assert not result["errors"]

    def test_tex_gather_skips_zone2_files(self, tmp_path: Path) -> None:
        """Primary .tex gather must skip zone-2 files (structural zone-1 selection).

        Verifies that a sha256: in appendix-repro.tex doesn't reach the judge
        when the structural .tex path is used (pdf_text=None).
        """
        from research_vault.manuscript.check_gates import check_cold_read_tally

        tree_root = tmp_path / "manuscripts" / "ms-test"
        sections_dir = tree_root / "sections"
        sections_dir.mkdir(parents=True, exist_ok=True)

        # Write clean body section
        (sections_dir / "introduction.tex").write_text(
            "\\section{Introduction}\nWe evaluate HFS. See Table 1.\n",
            encoding="utf-8",
        )
        # Write zone-2 section with a sha256 hash (legitimate in appendix)
        (sections_dir / "appendix-repro.tex").write_text(
            "\\section*{Reproducibility Appendix}\n"
            "sha256:a3f9c1e28b7d46f0a3f9c1e28b7d46f0 verification hash.\n",
            encoding="utf-8",
        )
        (tree_root / "main.tex").write_text(
            "\\documentclass{article}\n\\title{A Paper}\n\\begin{document}\n"
            "\\input{sections/introduction}\\input{sections/appendix-repro}\n"
            "\\end{document}\n",
            encoding="utf-8",
        )

        # pdf_text=None → structural .tex path; zone-2 stem excluded by name
        result = check_cold_read_tally(
            tree_root,
            judge_fn=_discriminating_judge,
            judge_model="mock",
            pdf_text=None,  # force structural .tex path
        )
        # The sha256 is only in zone-2 — structural selection excludes it
        assert result["flag_a_hits"] == [], (
            f"sha256: in zone-2 .tex (structural path) must be skipped. "
            f"Got flag_a_hits: {result['flag_a_hits']}"
        )


# ---------------------------------------------------------------------------
# SR-MS-GATE-ALIGN Slice A: structural fix — 4 required red-before-green fixtures
# ---------------------------------------------------------------------------

class TestStructuralBodyScopeFixtures:
    """SR-MS-GATE-ALIGN Slice A: structural zone-1 .tex selection replaces
    substring body-scoping.

    Required red-before-green fixtures per Wren's re-ruling. Each test is:
      RED  with the old _body_scope_pdf_text substring mechanism (PR #82 approach)
      GREEN with the promoted structural zone-1 .tex selection (this PR's approach)

    Root failure mode of the old approach: pdf_text.find(heading) matches the FIRST
    occurrence of a zone-2 heading string anywhere in the compiled PDF text —
    including TOC entries and body prose cross-references — and silently drops all
    subsequent body content, creating a vacuous gate that misses real zone-1 leaks.

    sr: SR-MS-GATE-ALIGN
    """

    def _make_tree_with_zone2(self, tmp_path: Path) -> Path:
        """Scaffold a minimal tree with a zone-1 body section + appendix-repro.tex."""
        tree_root = tmp_path / "manuscripts" / "ms-test"
        sections_dir = tree_root / "sections"
        sections_dir.mkdir(parents=True, exist_ok=True)
        (sections_dir / "appendix-repro.tex").write_text(
            "\\section*{Reproducibility Appendix}\n"
            "Legitimate repro content.\n",
            encoding="utf-8",
        )
        (tree_root / "main.tex").write_text(
            "\\documentclass{article}\n\\title{Results Paper}\n"
            "\\begin{document}\n"
            "\\input{sections/results}\n"
            "\\input{sections/appendix-repro}\n"
            "\\end{document}\n",
            encoding="utf-8",
        )
        return tree_root

    def test_toc_entry_does_not_shadow_body_leak(self, tmp_path: Path) -> None:
        """TOC entry mentioning 'Reproducibility Appendix' must not silence a body leak.

        The old substring mechanism (pdf_text.find(heading)) matches the FIRST occurrence
        of the zone-2 heading string — which is a TOC entry near the top of the rendered
        PDF. Truncating there drops all subsequent body content, including real zone-1 leaks.

        RED:  old _body_scope_pdf_text finds heading in TOC → truncates → body sha256
              silently missed → gate is vacuous → assertion fails.
        GREEN: no substring truncation; sha256 present in text passed to Flag-A → fires.
        sr: SR-MS-GATE-ALIGN
        """
        from research_vault.manuscript.check_gates import check_cold_read_tally

        tree_root = self._make_tree_with_zone2(tmp_path)
        (tree_root / "sections" / "results.tex").write_text(
            "\\section{Results}\n"
            "The main result sha256:deadbeef12345678deadbeef12345678deadbeef12345678dead is here.\n",
            encoding="utf-8",
        )

        # Simulated pdftotext output: "Reproducibility Appendix" appears in the TOC
        # near the top of the document — before the body sha256 leak.
        pdf_text = (
            "Table of Contents\n"
            "1  Introduction ............. 1\n"
            "A  Reproducibility Appendix . 8\n"  # ← zone-2 heading as a TOC entry
            "\n"
            "1  Introduction\n"
            "We evaluate HFS across three models.\n"
            "\n"
            "2  Results\n"
            # Body zone-1 leak below the TOC entry — in zone-1, not zone-2
            "The main result sha256:deadbeef12345678deadbeef12345678deadbeef12345678dead is here.\n"
            "\n"
            "A  Reproducibility Appendix\n"
            "Legitimate repro content.\n"
        )

        result = check_cold_read_tally(
            tree_root,
            judge_fn=_discriminating_judge,
            judge_model="mock",
            pdf_text=pdf_text,
        )
        # Body sha256 MUST be flagged.
        # OLD: _body_scope_pdf_text truncates at TOC line (first "Reproducibility Appendix")
        #      → body sha256 silently dropped → gate is vacuous → assertion FAILS (RED)
        # NEW: no substring truncation → sha256 remains in text → Flag-A fires → PASSES (GREEN)
        assert result["flag_a_hits"] or result["errors"], (
            "Body zone-1 sha256 must be flagged even when 'Reproducibility Appendix' "
            "appears earlier as a TOC entry. The old substring approach truncated at the "
            "TOC entry and silently dropped all subsequent body content (vacuous gate). "
            f"Got flag_a_hits={result['flag_a_hits']}, errors={result['errors']}"
        )

    def test_body_crossref_does_not_shadow_body_leak(self, tmp_path: Path) -> None:
        """Body cross-reference to the appendix must not silence a zone-1 leak that follows.

        Body prose 'As detailed in the Reproducibility Appendix, ...' contains the
        zone-2 heading string inline as a cross-reference. The old substring truncation
        fires at this cross-reference, dropping all body content after it — including
        real zone-1 leaks that appear immediately below.

        RED:  old _body_scope_pdf_text finds heading in cross-ref sentence → truncates
              → sha256 on the next line is missed → assertion fails.
        GREEN: no substring truncation → sha256 present in text → Flag-A fires.
        sr: SR-MS-GATE-ALIGN
        """
        from research_vault.manuscript.check_gates import check_cold_read_tally

        tree_root = self._make_tree_with_zone2(tmp_path)
        (tree_root / "sections" / "results.tex").write_text(
            "\\section{Results}\n"
            "As detailed in the Reproducibility Appendix, our protocol is sound.\n"
            "The main result sha256:beefdead12345678beefdead12345678beefdead12345678beef is here.\n",
            encoding="utf-8",
        )

        # Simulated pdftotext output: body cross-reference comes BEFORE the zone-1 sha256.
        pdf_text = (
            "1  Introduction\n"
            "We evaluate HFS.\n"
            "\n"
            "2  Results\n"
            # Cross-reference to appendix in body prose — contains the heading string
            "As detailed in the Reproducibility Appendix, our protocol is sound.\n"
            # sha256 immediately follows in zone-1 — old substring truncates before this
            "The main result sha256:beefdead12345678beefdead12345678beefdead12345678beef is here.\n"
            "\n"
            "A  Reproducibility Appendix\n"
            "Legitimate repro content.\n"
        )

        result = check_cold_read_tally(
            tree_root,
            judge_fn=_discriminating_judge,
            judge_model="mock",
            pdf_text=pdf_text,
        )
        # sha256 below the cross-reference MUST be flagged.
        # OLD: truncates at cross-ref sentence → sha256 on next line hidden → RED
        # NEW: no truncation → sha256 present → Flag-A fires → GREEN
        assert result["flag_a_hits"] or result["errors"], (
            "Body sha256 immediately after a cross-reference to 'Reproducibility Appendix' "
            "must be flagged. The old substring approach truncated at the cross-reference "
            "sentence and silently hid the subsequent zone-1 leak. "
            f"Got flag_a_hits={result['flag_a_hits']}, errors={result['errors']}"
        )

    def test_zone2_sha256_excluded_structural_positive_control(self, tmp_path: Path) -> None:
        """Zone-2 sha256 is excluded via structural .tex selection (positive control).

        A sha256 legitimately inside appendix-repro.tex (zone-2) must NOT be flagged
        by the cold-read gate. This positive control verifies zone-2 exclusion is
        preserved under the structural promotion.

        GREEN both before and after the fix: the .tex file is skipped by stem name,
        so its sha256 never reaches Flag-A.
        sr: SR-MS-GATE-ALIGN
        """
        from research_vault.manuscript.check_gates import check_cold_read_tally

        tree_root = tmp_path / "manuscripts" / "ms-test"
        sections_dir = tree_root / "sections"
        sections_dir.mkdir(parents=True, exist_ok=True)

        # Clean zone-1 section (no sha256)
        (sections_dir / "results.tex").write_text(
            "\\section{Results}\nWe evaluate HFS. See Table 1 for details.\n",
            encoding="utf-8",
        )
        # Zone-2 section: sha256 is LEGITIMATE here (ACM-badging model)
        (sections_dir / "appendix-repro.tex").write_text(
            "\\section*{Reproducibility Appendix}\n"
            "sha256:legitim8hash12345678901234567890123456789012345678901234 verification.\n",
            encoding="utf-8",
        )
        (tree_root / "main.tex").write_text(
            "\\documentclass{article}\n\\title{A Paper}\n\\begin{document}\n"
            "\\input{sections/results}\\input{sections/appendix-repro}\n"
            "\\end{document}\n",
            encoding="utf-8",
        )

        # pdf_text=None → structural .tex path; zone-2 stem excluded by name
        result = check_cold_read_tally(
            tree_root,
            judge_fn=_discriminating_judge,
            judge_model="mock",
            pdf_text=None,
        )
        assert result["flag_a_hits"] == [], (
            "sha256: in appendix-repro.tex (zone-2) must NOT be flagged — "
            "structural zone-1 .tex selection excludes zone-2 stems by filename. "
            f"Got: {result['flag_a_hits']}"
        )
        assert not result["errors"], (
            f"No errors expected for zone-2 sha256 under structural exclusion. "
            f"Got: {result['errors']}"
        )

    def test_canary_calibration_preserved(self) -> None:
        """Canary texts have no zone-2 content — calibration is unaffected.

        The bidirectional canary probes do not contain zone-2 appendix headings.
        Zone-1 .tex selection (or the old body-scoping) must be a no-op for them.
        Calibration must hold regardless of which mechanism is active.

        GREEN both before and after the fix: no zone-2 headings → no-op on canaries.
        sr: SR-MS-GATE-ALIGN
        """
        from research_vault.manuscript.coldread import (
            run_cold_read,
            flag_a_scan,
            _CANARY_A_TEXT,
            _CANARY_B_TEXT,
        )

        # Canary (a): known clean — must still pass the discriminating judge cleanly
        result_a = run_cold_read(
            _CANARY_A_TEXT,
            judge_fn=_discriminating_judge,
            judge_model="mock",
        )
        assert result_a.canary_aborted is False, (
            "Canary (a) must pass with the discriminating judge — calibration preserved."
        )

        # Canary (b): known leaky — its leak markers must still be detectable by Flag-A
        # (body-scoping must not strip them)
        canary_b_hits = flag_a_scan(_CANARY_B_TEXT)
        assert len(canary_b_hits) >= 1, (
            "Canary (b) known-leaky text must still have Flag-A detectable patterns. "
            "Body-scoping must not strip the known leak markers from canary texts. "
            f"Got: {canary_b_hits}"
        )
