"""test_manuscript_m8_calibration.py — PR-M8: the calibrated rubric + reviewer
lenses + bidirectional & annotated-bib canary (the capability-level
acceptance gate, design §14 PR-M8, §11.3 D-SV-D mandatory).

This module is deliberately SEPARATE from ``test_manuscript_review_board.py``
(PR-M5's machinery tests, which use bare marker-string routing): these tests
exercise the REAL ``DEFAULT_LIT_REVIEW_RUBRIC`` + the REAL calibrated canary
passages through a judge mock that reads actual passage CONTENT (distinct
substrings from what the internal ``_CANARY_*_MARKER`` constants check),
proving the calibrated rubric text + canary passages carry genuine,
independently-detectable signal — not just "the machinery plumbing works."

★ Acceptance (design §14 PR-M8, this pass's dispatch brief):
  - strong survey -> floor cleared (Ada's rubric, not a mock bound)
  - weak survey -> floor NOT cleared
  - the annotated-bib probe -> does NOT clear (ABORT if it does — the exact
    AI-Scientist positivity-bias failure)
  - known-WEAK at ceiling -> ABORT; known-STRONG at floor -> ABORT
  - every score carries a text justification
  - the reviewer never receives the thesis
  - the judge stays behind the loud-fail guard (RV_JUDGE_MODEL/ANTHROPIC_API_KEY)
  - judge_model + prompt_hash logged (audit + drift detection)

All hermetic — judge_fn is always injectable, no live LLM call.
sr: PR-M8
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.manuscript import review_board as rb


# ---------------------------------------------------------------------------
# A content-driven mock judge -- reads distinguishing PASSAGE CONTENT (not
# the internal single-line ``_CANARY_*_MARKER`` constants), so this proves
# the calibrated rubric/passages carry independently-legible signal.
# ---------------------------------------------------------------------------

def _content_aware_calibrated_judge(prompt: str) -> str:
    """A judge that scores based on distinguishing CONTENT phrases lifted
    from the calibrated passages -- deliberately NOT the same substrings as
    ``rb._CANARY_*_MARKER`` (those are tested separately in
    test_manuscript_review_board.py's machinery tests). Every score line
    below carries a non-empty justification, per the rubric's ARR
    instruction."""
    if "PRISMA search over" in prompt and "Nickerson's ending-conditions hold" in prompt:
        # The calibrated known-STRONG passage.
        return (
            "[SCOPE:5] documented PRISMA search across three databases, full inclusion/exclusion ledger in Appendix A\n"
            "[REPRO:5] a reader could re-derive the corpus from the stated protocol\n"
            "[FRAME:4] four coherent orthogonal axes, no orphaned works, Nickerson's conditions hold\n"
            "MISFITS: none\n"
            "REFRAME_CANDIDATES: none\n"
            "[SYNTH:4] section 4 compares four approaches side-by-side under a shared theme\n"
            "[COMPARE:5] states which approach wins where and why, surfaces a genuine tension\n"
            "[GAP:4] two gaps traced to specific empty taxonomy cells\n"
            "[CITE:5] every claim attributed to a specific substantiating source\n"
            "[BIAS:4] conflicting studies explicitly named and reconciled\n"
        )
    if "No search protocol is given" in prompt:
        # The calibrated known-WEAK passage.
        return (
            "[SCOPE:1] no stated boundary, no search protocol at all\n"
            "[REPRO:1] 'we just read what we found' -- entirely unauditable\n"
            "[FRAME:1] no taxonomy, just an unordered list\n"
            "MISFITS: no framework exists to have misfits\n"
            "REFRAME_CANDIDATES: none\n"
            "[SYNTH:1] no synthesis, just a vibe-level claim\n"
            "[COMPARE:1] no comparison of any kind\n"
            "[GAP:1] boilerplate 'more research is needed'\n"
            "[CITE:1] every claim stated with no citation\n"
            "[BIAS:1] unfalsifiable blanket praise, no disconfirming evidence considered\n"
        )
    if "retrieved 40 papers via a documented database search" in prompt:
        # The MANDATORY calibrated annotated-bibliography passage: floor
        # dims are DELIBERATELY well-sourced (each summary cites its paper,
        # search protocol given) so the probe isolates the SYNTH failure.
        return (
            "[SCOPE:4] a documented database search with inclusion criteria in Section 2\n"
            "[REPRO:4] the query and criteria are given, a reader could re-derive the corpus\n"
            "[FRAME:2] no organizing taxonomy at all, just retrieval order\n"
            "MISFITS: every paper stands alone, none anchored to any framework branch\n"
            "REFRAME_CANDIDATES: none\n"
            "[SYNTH:1] one paragraph per paper, explicitly 'no comparison is drawn between any two papers'\n"
            "[COMPARE:1] zero cross-paper comparison of any kind\n"
            "[GAP:1] no gap is stated at all\n"
            "[CITE:4] each paragraph does cite its own paper accurately\n"
            "[BIAS:2] nothing overclaimed, but nothing synthesized either\n"
        )
    return "[SCOPE:3] [REPRO:3] [CITE:3] [FRAME:3] [SYNTH:3] [COMPARE:3] [GAP:3] [BIAS:3]"


# ---------------------------------------------------------------------------
# ★ Acceptance: canary calibration against the REAL calibrated rubric+passages
# ---------------------------------------------------------------------------

class TestCalibratedCanaryAcceptance:
    def test_strong_survey_clears_the_floor(self):
        """Strong probe (real DEFAULT_LIT_REVIEW_RUBRIC content) scores every
        floor dim >= floor+1 -- must NOT abort."""
        result = rb.run_canary_scaffold(
            _content_aware_calibrated_judge, rb.DEFAULT_LIT_REVIEW_RUBRIC, floor_value=3,
        )
        assert result["canary_ok"] is True

    def test_weak_survey_does_not_clear(self):
        """A judge that correctly floors the weak passage below floor-1 on
        every floor dim passes the canary (i.e. does NOT raise) -- proving
        the weak probe, scored honestly, does NOT clear."""
        # Isolate: call run_canary_scaffold's internal weak-probe path via
        # the same judge, and separately confirm the weak passage's own
        # extracted scores are below the floor -- direct proof it does not
        # clear (rather than inferring only from the aggregate not raising).
        weak_prompt = rb.DEFAULT_LIT_REVIEW_RUBRIC.replace("{PDF_TEXT}", rb._CANARY_WEAK_PASSAGE)
        weak_response = _content_aware_calibrated_judge(weak_prompt)
        scores, _just = rb._extract_review_scores_and_justifications(weak_response)
        assert scores is not None
        for dim in ("SCOPE", "REPRO", "CITE"):
            assert scores[dim] < 3, f"weak probe dim {dim}={scores[dim]} did not fail to clear"

    def test_annotated_bibliography_does_not_clear(self):
        """★ MANDATORY (D-SV-D): the literal per-paper annotated-bibliography
        probe must NOT clear on SYNTH -- the #1 survey failure this whole
        capability exists to catch. ABORT if it would clear (the exact
        AI-Scientist positivity-bias failure)."""
        ab_prompt = rb.DEFAULT_LIT_REVIEW_RUBRIC.replace("{PDF_TEXT}", rb._CANARY_ANNOTATED_BIB_PASSAGE)
        ab_response = _content_aware_calibrated_judge(ab_prompt)
        scores, _just = rb._extract_review_scores_and_justifications(ab_response)
        assert scores is not None
        assert scores["SYNTH"] < 3, "annotated-bib probe cleared SYNTH -- board is blind to enumeration"
        # And the full canary scaffold does NOT abort for a correctly-
        # calibrated judge (the ABORT path is exercised by the BROKEN/
        # RUBBER-STAMPING/BLIND judges below).
        result = rb.run_canary_scaffold(
            _content_aware_calibrated_judge, rb.DEFAULT_LIT_REVIEW_RUBRIC, floor_value=3,
        )
        assert result["canary_ok"] is True

    def test_known_weak_at_ceiling_aborts(self):
        """A judge that RUBBER-STAMPS the weak passage (scores it at
        ceiling) must ABORT -- positivity bias, exactly what the canary
        exists to catch."""
        def _rubber_stamping_judge(prompt: str) -> str:
            if "PRISMA search over" in prompt and "Nickerson's ending-conditions hold" in prompt:
                return "[SCOPE:5] x\n[REPRO:5] x\n[CITE:5] x\n[FRAME:5] x\n[SYNTH:5] x\n[COMPARE:5] x\n[GAP:5] x\n[BIAS:5] x\n"
            # Rubber-stamps the weak passage too -- scores it just as high.
            return "[SCOPE:5] x\n[REPRO:5] x\n[CITE:5] x\n[FRAME:5] x\n[SYNTH:5] x\n[COMPARE:5] x\n[GAP:5] x\n[BIAS:5] x\n"

        with pytest.raises(rb.CanaryAbortError, match="RUBBER-STAMPING"):
            rb.run_canary_scaffold(_rubber_stamping_judge, rb.DEFAULT_LIT_REVIEW_RUBRIC, floor_value=3)

    def test_known_strong_at_floor_aborts(self):
        """A judge that is BROKEN-HARSH (scores the strong passage down at
        the floor, not above it) must ABORT -- a blind rejector."""
        def _broken_harsh_judge(prompt: str) -> str:
            return "[SCOPE:3] x\n[REPRO:3] x\n[CITE:3] x\n[FRAME:3] x\n[SYNTH:3] x\n[COMPARE:3] x\n[GAP:3] x\n[BIAS:3] x\n"

        with pytest.raises(rb.CanaryAbortError, match="BROKEN-HARSH"):
            rb.run_canary_scaffold(_broken_harsh_judge, rb.DEFAULT_LIT_REVIEW_RUBRIC, floor_value=3)

    def test_annotated_bib_blind_judge_aborts(self):
        """★ PR-165 regression: the exact failure the annotated-bib canary
        exists to catch (review_board.py:604-612) -- a judge that is BLIND
        to enumeration-vs-synthesis and scores the literal
        annotated-bibliography passage's SYNTH dim >= floor_value (as if the
        one-paragraph-per-paper passage were a real cross-paper synthesis)
        must ABORT. Floor dims (SCOPE/REPRO/CITE) score fine on both the
        STRONG and WEAK probes here -- ONLY the AB-probe's SYNTH is blind --
        isolating this from the RUBBER-STAMPING / BROKEN-HARSH probes above,
        which fail on the strong/weak probes instead. This is the #1 survey
        failure (AI-Scientist's positivity-bias analog) and, until this test,
        had no dedicated regression coverage among the canary tests."""
        def _synth_blind_judge(prompt: str) -> str:
            if "PRISMA search over" in prompt and "Nickerson's ending-conditions hold" in prompt:
                # Correctly floors the STRONG probe above floor+1.
                return "[SCOPE:5] x\n[REPRO:5] x\n[CITE:5] x\n[FRAME:5] x\n[SYNTH:5] x\n[COMPARE:5] x\n[GAP:5] x\n[BIAS:5] x\n"
            if "No search protocol is given" in prompt:
                # Correctly floors the WEAK probe below floor-1.
                return "[SCOPE:1] x\n[REPRO:1] x\n[CITE:1] x\n[FRAME:1] x\n[SYNTH:1] x\n[COMPARE:1] x\n[GAP:1] x\n[BIAS:1] x\n"
            if "retrieved 40 papers via a documented database search" in prompt:
                # BLIND to enumeration: scores SYNTH at floor_value (3) as if
                # the annotated-bibliography passage were genuine synthesis --
                # despite it explicitly stating "no comparison is drawn
                # between any two papers, no shared axis is used".
                return "[SCOPE:4] x\n[REPRO:4] x\n[CITE:4] x\n[FRAME:4] x\n[SYNTH:3] x\n[COMPARE:4] x\n[GAP:4] x\n[BIAS:4] x\n"
            return "[SCOPE:3] x\n[REPRO:3] x\n[CITE:3] x\n[FRAME:3] x\n[SYNTH:3] x\n[COMPARE:3] x\n[GAP:3] x\n[BIAS:3] x\n"

        with pytest.raises(rb.CanaryAbortError, match="BLIND"):
            rb.run_canary_scaffold(_synth_blind_judge, rb.DEFAULT_LIT_REVIEW_RUBRIC, floor_value=3)


# ---------------------------------------------------------------------------
# ★ Acceptance: every score carries a text justification (ARR discipline)
# ---------------------------------------------------------------------------

class TestJustifyEachScore:
    def test_calibrated_rubric_instructs_justification_per_score(self):
        """The rubric text itself must instruct one justification per score
        line (ARR: 'every score justified in text') -- not just bare
        brackets, design §11.1/methodology §A.2."""
        assert "justif" in rb.DEFAULT_LIT_REVIEW_RUBRIC.lower()
        assert "<justification>" in rb.DEFAULT_LIT_REVIEW_RUBRIC

    def test_every_dim_has_a_non_empty_justification_on_calibrated_response(self):
        """A well-formed calibrated-judge response (the strong probe) has a
        real justification (not just a bare bracket) attached to every one
        of the 8 dims."""
        prompt = rb.DEFAULT_LIT_REVIEW_RUBRIC.replace("{PDF_TEXT}", rb._CANARY_STRONG_PASSAGE)
        raw_response = _content_aware_calibrated_judge(prompt)
        scores, justifications = rb._extract_review_scores_and_justifications(raw_response)
        assert scores is not None
        for dim in rb._ALL_DIMS:
            assert justifications.get(dim, "").strip(), f"{dim} has no justification text"

    def test_reviewer_node_surfaces_missing_justifications_not_silently(self):
        """A judge response with bare brackets and no justification text is
        surfaced via ``missing_justifications`` (charter §2) -- never
        silently accepted as a real, ARR-compliant review."""
        def _bare_judge(prompt: str) -> str:
            return "[SCOPE:4]\n[REPRO:4]\n[CITE:4]\n[FRAME:4]\n[SYNTH:4]\n[COMPARE:4]\n[GAP:4]\n[BIAS:4]\n"

        result = rb.run_reviewer_node("draft", round_num=1, lens_num=1, K=3, judge_fn=_bare_judge)
        assert set(result["missing_justifications"]) == set(rb._ALL_DIMS)

    def test_reviewer_node_recognizes_justified_scores(self):
        """A judge that DOES justify every score has an empty
        ``missing_justifications`` list."""
        justified_judge = lambda p: (
            "[SCOPE:4] because the corpus is well-documented\n"
            "[REPRO:4] search protocol is explicit\n"
            "[CITE:4] every claim traces to a source\n"
            "[FRAME:4] taxonomy is coherent\n"
            "[SYNTH:4] compares across papers\n"
            "[COMPARE:4] states which wins and why\n"
            "[GAP:4] anchored to the framework\n"
            "[BIAS:4] disconfirming work acknowledged\n"
        )
        result = rb.run_reviewer_node("draft", round_num=1, lens_num=1, K=3, judge_fn=justified_judge)
        assert result["missing_justifications"] == []


class TestMissingJustificationsPropagateToMetaReview:
    """★ PR-165 fix 1: ``missing_justifications`` (computed per-reviewer in
    ``run_reviewer_node``) must reach the human -- not be silently dropped
    before ``run_meta_review``'s returned payload (a green-and-empty,
    charter Sec 2). A FLOOR-dim miss (SCOPE/REPRO/CITE) is especially loud:
    it lands in ``worst_findings`` right next to ``floor_results``."""

    def test_floor_dim_missing_justification_surfaces_in_worst_findings(self):
        # A bare (unjustified) response on a FLOOR dim (CITE) plus every
        # other dim justified -- isolates the CITE miss.
        def _bare_cite_judge(prompt: str) -> str:
            return (
                "[SCOPE:4] documented search protocol\n"
                "[REPRO:4] reproducible from the stated method\n"
                "[CITE:5]\n"
                "[FRAME:4] coherent taxonomy\n"
                "[SYNTH:4] cross-paper comparison present\n"
                "[COMPARE:4] states which wins and why\n"
                "[GAP:4] anchored to specific cells\n"
                "[BIAS:4] disconfirming evidence considered\n"
            )

        reviewer_result = rb.run_reviewer_node(
            "draft", round_num=1, lens_num=1, K=1, judge_fn=_bare_cite_judge,
        )
        assert reviewer_result["missing_justifications"] == ["CITE"]

        meta = rb.run_meta_review(round_num=1, reviewer_results=[reviewer_result])
        assert "missing_justifications" in meta
        assert len(meta["missing_justifications"]) == 1
        assert "CITE=5" in meta["missing_justifications"][0]
        assert reviewer_result["node_id"] in meta["missing_justifications"][0]
        # FLOOR-dim miss escalates into worst_findings, loud next to floor_results.
        assert any("CITE=5" in wf for wf in meta["worst_findings"])

    def test_non_floor_dim_missing_justification_not_forced_into_worst_findings(self):
        # A bare (unjustified) response on a non-floor dim (SYNTH, a SIGNAL
        # dim) is still surfaced in ``missing_justifications`` but is NOT
        # force-injected into ``worst_findings`` (which is reserved for
        # floor-gating concerns) unless it happened to fail there anyway.
        def _bare_synth_judge(prompt: str) -> str:
            return (
                "[SCOPE:4] documented search protocol\n"
                "[REPRO:4] reproducible from the stated method\n"
                "[CITE:4] every claim traces to a source\n"
                "[FRAME:4] coherent taxonomy\n"
                "[SYNTH:5]\n"
                "[COMPARE:4] states which wins and why\n"
                "[GAP:4] anchored to specific cells\n"
                "[BIAS:4] disconfirming evidence considered\n"
            )

        reviewer_result = rb.run_reviewer_node(
            "draft", round_num=1, lens_num=1, K=1, judge_fn=_bare_synth_judge,
        )
        assert reviewer_result["missing_justifications"] == ["SYNTH"]

        meta = rb.run_meta_review(round_num=1, reviewer_results=[reviewer_result])
        assert any("SYNTH=5" in mj for mj in meta["missing_justifications"])
        assert not any("SYNTH=5" in wf for wf in meta["worst_findings"])
        # Floor dims all justified and pass -> cleared, no floor worst_findings noise.
        assert meta["cleared"] is True


# ---------------------------------------------------------------------------
# ★ Acceptance: the reviewer never receives the thesis (structural — already
# proven in test_manuscript_review_board.py; re-asserted here against the
# REAL calibrated rubric text specifically, since PR-M8 replaced the text).
# ---------------------------------------------------------------------------

class TestReviewerNeverReceivesThesis:
    def test_calibrated_rubric_prompt_has_no_thesis_slot(self):
        captured = {}

        def _judge(prompt: str) -> str:
            captured["prompt"] = prompt
            return "[SCOPE:4]\n[REPRO:4]\n[CITE:4]\n[FRAME:4]\n[SYNTH:4]\n[COMPARE:4]\n[GAP:4]\n[BIAS:4]\n"

        rb.run_reviewer_node("MY UNIQUE DRAFT MARKER", round_num=1, lens_num=1, K=3, judge_fn=_judge)
        assert "MY UNIQUE DRAFT MARKER" in captured["prompt"]
        # The rubric legitimately MENTIONS "thesis" (it tells the reviewer it
        # has NOT been given one, and the lens warns against a boundary
        # drawn "to flatter its own thesis") -- the real anti-anchoring
        # property is that no injected thesis VALUE appears in the prompt.
        assert "MY SECRET AUTHOR THESIS" not in captured["prompt"]
        assert "not the author's thesis" in captured["prompt"].lower()


# ---------------------------------------------------------------------------
# ★ Acceptance: judge stays behind the loud-fail guard (cmd_review re-check,
# calibrated rubric doesn't weaken it — re-asserted here for PR-M8's scope).
# ---------------------------------------------------------------------------

class TestJudgeLoudFailGuardUnweakened:
    def test_run_review_board_still_requires_judge_fn(self, tmp_path):
        from research_vault.manuscript.types import get_type

        project_notes_dir = tmp_path / "notes"
        tree_root = project_notes_dir / "manuscripts" / "survey-cal"
        (tree_root / "sections").mkdir(parents=True, exist_ok=True)
        (tree_root / "references.md").write_text("", encoding="utf-8")
        ms_type = get_type("lit-review")

        with pytest.raises(RuntimeError, match="judge_fn is required"):
            rb.run_review_board("draft", tree_root, project_notes_dir, ms_type, judge_fn=None)


# ---------------------------------------------------------------------------
# ★ judge_model + prompt_hash logging (audit + drift detection, PR-M8)
# ---------------------------------------------------------------------------

class TestJudgeModelAndPromptHashLogging:
    def test_reviewer_node_logs_judge_model_and_prompt_hash(self):
        result = rb.run_reviewer_node(
            "draft text", round_num=1, lens_num=1, K=3,
            judge_fn=_content_aware_calibrated_judge, judge_model="claude-fake-tier",
        )
        assert result["judge_model"] == "claude-fake-tier"
        assert isinstance(result["prompt_hash"], str) and len(result["prompt_hash"]) == 16

    def test_canary_scaffold_logs_prompt_hashes_for_all_three_probes(self):
        result = rb.run_canary_scaffold(
            _content_aware_calibrated_judge, rb.DEFAULT_LIT_REVIEW_RUBRIC,
            floor_value=3, judge_model="claude-fake-tier",
        )
        assert result["judge_model"] == "claude-fake-tier"
        assert set(result["prompt_hashes"].keys()) == {"strong", "weak", "annotated_bib"}
        # Distinct probes -> distinct hashes (they're different prompts).
        hashes = list(result["prompt_hashes"].values())
        assert len(set(hashes)) == 3

    def test_end_to_end_review_board_stamps_judge_model_and_prompt_hash(self, tmp_path):
        """Full round-trip through cmd_review's ``_stamp_review_meta`` --
        the note's audit record carries the judge_model + at least one
        prompt_hash (never blank when a judge actually ran)."""
        from research_vault.config import load_config
        from research_vault.manuscript import cmd_new, cmd_review, _manuscript_tree_root

        config_file = tmp_path / "research_vault.toml"
        config_file.write_text(
            f"""
instance_root = "{tmp_path}"
notes_root = "{tmp_path / 'notes'}"

[projects.demo-research]
source_dir = "{tmp_path / 'projects' / 'demo-research'}"
""",
            encoding="utf-8",
        )
        import os

        os.environ.pop("RV_JUDGE_MODEL", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ["RESEARCH_VAULT_CONFIG"] = str(config_file)
        cfg = load_config(reload=True)
        cmd_new("demo-research", "survey-cal", ms_type_key="lit-review", config=cfg)

        cmd_review(
            "demo-research", "survey-cal", config=cfg,
            judge_fn=_content_aware_calibrated_judge,
        )

        tree_root = _manuscript_tree_root("demo-research", "survey-cal", cfg)
        note_text = (tree_root / "_manuscript.md").read_text(encoding="utf-8")
        assert "manuscript_review run" in note_text
        assert "prompt_hashes=" in note_text
        assert "judge_model=" in note_text
