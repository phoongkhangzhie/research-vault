"""test_manuscript_review_board.py — PR-M5: the bounded-unroll review-revise
loop MACHINERY (2 rounds x 3 conference-style reviewers, design §9).

Covers:
  - the 8-dim dimensioned-score bracket extractor (fail-closed)
  - floor-not-average predicate (MIN-across-3, not mean)
  - the 3 reviewer lenses + fresh-by-construction (no thesis/prior-round param)
  - node-level skip-once-cleared short-circuit (no judge call once cleared)
  - the canary scaffold (3 mock probes: strong/weak/annotated-bib)
  - the regression guard (never accept a round that regresses a floor axis)
  - the reframe-escalation payload (surface-not-auto, §5.1)
  - run_review_board: cleared / not-cleared-after-N / N,K frozen (hard-cap)
  - run_revise: single-sourced re-fire via check_gates.build_approve_payload
    (NOT duplicated — grep + call-graph proof)
  - the [manuscript_review] config seam (get_review_config)
  - cmd_review wiring: RV_JUDGE_MODEL/ANTHROPIC_API_KEY loud-fail guard

All hermetic — judge_fn is always injectable, no live LLM call.
sr: PR-M5
"""
from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.manuscript import review_board as rb


# ---------------------------------------------------------------------------
# Mock judge builders
# ---------------------------------------------------------------------------

def _score_response(**scores: int) -> str:
    """Build a mock judge response with the given dim scores as brackets."""
    lines = [f"[{dim}:{val}]" for dim, val in scores.items()]
    return "\n".join(lines)


_ALL_PASS = {d: 4 for d in rb._ALL_DIMS}          # comfortably clears every floor
_ALL_FAIL = {d: 1 for d in rb._ALL_DIMS}          # fails every floor


def _uniform_judge(scores: dict[str, int]):
    def _judge(prompt: str) -> str:
        return _score_response(**scores)
    return _judge


def _counting_judge(scores: dict[str, int]):
    """A judge that records every prompt it was called with (call log)."""
    calls: list[str] = []

    def _judge(prompt: str) -> str:
        calls.append(prompt)
        return _score_response(**scores)

    _judge.calls = calls  # type: ignore[attr-defined]
    return _judge


def _canary_aware_judge(review_scores: dict[str, int]):
    """A judge that correctly distinguishes the 3 canary probes from a real
    reviewer prompt (real production judges must do this; a mock used to
    exercise the canary-enabled path must too, or every such test would
    correctly ABORT — a dumb judge that can't tell strong from weak IS
    exactly what the canary is designed to catch)."""

    def _judge(prompt: str) -> str:
        if rb._CANARY_STRONG_MARKER in prompt:
            return _score_response(**_ALL_PASS)
        if rb._CANARY_WEAK_MARKER in prompt:
            return _score_response(**_ALL_FAIL)
        if rb._CANARY_ANNOTATED_BIB_MARKER in prompt:
            return _score_response(SCOPE=4, REPRO=4, CITE=4, FRAME=2, SYNTH=1, COMPARE=1, GAP=1, BIAS=2)
        return _score_response(**review_scores)

    return _judge


# ---------------------------------------------------------------------------
# Dimensioned-score bracket extractor
# ---------------------------------------------------------------------------

class TestExtractReviewScores:
    def test_extracts_all_eight_dims(self):
        text = _score_response(**_ALL_PASS)
        scores = rb._extract_review_scores(text)
        assert scores == _ALL_PASS

    def test_missing_dim_absent_from_dict(self):
        text = "[SCOPE:4]\n[CITE:3]\n"
        scores = rb._extract_review_scores(text)
        assert scores == {"SCOPE": 4, "CITE": 3}
        assert "REPRO" not in scores

    def test_complete_parse_failure_returns_none(self):
        assert rb._extract_review_scores("no scores here at all") is None

    def test_case_insensitive(self):
        scores = rb._extract_review_scores("[scope:5] [Cite:2]")
        assert scores == {"SCOPE": 5, "CITE": 2}

    def test_unparseable_digit_fails_closed_to_zero(self):
        # Regex requires \d+, so a non-digit simply won't match at all —
        # confirm the whole-string extraction still degrades safely.
        scores = rb._extract_review_scores("[SCOPE:4] [CITE:x]")
        assert scores == {"SCOPE": 4}


class TestExtractFrameEscalationFields:
    def test_parses_misfits_and_candidates(self):
        text = (
            "[FRAME:1]\n"
            "MISFITS: paper A doesn't fit, paper B is orphaned\n"
            "REFRAME_CANDIDATES: axis-based, timeline-based\n"
        )
        fields = rb._extract_frame_escalation_fields(text)
        assert fields["misfits"] == ["paper A doesn't fit", "paper B is orphaned"]
        assert fields["reframe_candidates"] == ["axis-based", "timeline-based"]

    def test_none_value_yields_empty_list(self):
        text = "MISFITS: none\nREFRAME_CANDIDATES: none\n"
        fields = rb._extract_frame_escalation_fields(text)
        assert fields == {"misfits": [], "reframe_candidates": []}

    def test_absent_lines_yield_empty_lists(self):
        fields = rb._extract_frame_escalation_fields("[FRAME:4]\nno escalation here\n")
        assert fields == {"misfits": [], "reframe_candidates": []}


# ---------------------------------------------------------------------------
# Threshold predicate — floor-not-average
# ---------------------------------------------------------------------------

class TestEvaluateThreshold:
    def test_all_clear_when_min_meets_floor(self):
        scores = [dict(_ALL_PASS), dict(_ALL_PASS), dict(_ALL_PASS)]
        result = rb._evaluate_threshold(scores, floor_dims=["SCOPE", "REPRO", "CITE"], floor_value=3)
        assert result["cleared"] is True

    def test_one_below_floor_blocks_even_if_two_pass(self):
        """MIN-across-3 gates — a mean would NOT catch this (avg of 5,5,1 = 3.67)."""
        scores = [
            {"SCOPE": 5, "REPRO": 5, "CITE": 5},
            {"SCOPE": 5, "REPRO": 5, "CITE": 5},
            {"SCOPE": 5, "REPRO": 5, "CITE": 1},  # one reviewer tanks CITE
        ]
        result = rb._evaluate_threshold(scores, floor_dims=["SCOPE", "REPRO", "CITE"], floor_value=3)
        assert result["cleared"] is False
        assert result["floor_results"]["CITE"]["min_score"] == 1
        assert result["floor_results"]["CITE"]["passed"] is False
        # The other two floor dims independently still pass — proves this is a
        # per-dim MIN, not an aggregate collapse.
        assert result["floor_results"]["SCOPE"]["passed"] is True

    def test_missing_dim_defaults_to_zero_fail_closed(self):
        scores = [{"SCOPE": 5}]  # REPRO/CITE entirely absent
        result = rb._evaluate_threshold(scores, floor_dims=["SCOPE", "REPRO", "CITE"], floor_value=3)
        assert result["cleared"] is False
        assert result["floor_results"]["REPRO"]["min_score"] == 0

    def test_no_reviewers_cannot_clear(self):
        result = rb._evaluate_threshold([], floor_dims=["SCOPE"], floor_value=3)
        assert result["cleared"] is False


# ---------------------------------------------------------------------------
# Reviewer lens assignment
# ---------------------------------------------------------------------------

class TestReviewerLensSpec:
    def test_k3_assigns_three_distinct_lenses(self):
        lenses = {rb.get_reviewer_lens_spec(k, 3) for k in (1, 2, 3)}
        assert len(lenses) == 3
        assert rb.get_reviewer_lens_spec(1, 3) == rb._LENS_COVERAGE_AUDITOR
        assert rb.get_reviewer_lens_spec(2, 3) == rb._LENS_FRAMEWORK_CRITIC
        assert rb.get_reviewer_lens_spec(3, 3) == rb._LENS_SYNTHESIS_ADVERSARY

    def test_framework_critic_carries_reframe_trigger_instructions(self):
        assert "MISFITS" in rb._LENS_FRAMEWORK_CRITIC
        assert "REFRAME_CANDIDATES" in rb._LENS_FRAMEWORK_CRITIC

    def test_cycles_when_k_not_three(self):
        # K=1 -> only lens 1 (coverage auditor)
        assert rb.get_reviewer_lens_spec(1, 1) == rb._LENS_COVERAGE_AUDITOR
        # K=6 wraps back to the same 3 lenses twice
        assert rb.get_reviewer_lens_spec(4, 6) == rb.get_reviewer_lens_spec(1, 6)


# ---------------------------------------------------------------------------
# Reviewer node — fresh-by-construction, skip short-circuit
# ---------------------------------------------------------------------------

class TestRunReviewerNode:
    def test_never_fed_prior_round_or_thesis(self):
        """The function signature enforces the anti-anchoring boundary: no
        parameter exists for a thesis, prior scores, or a rebuttal."""
        sig = inspect.signature(rb.run_reviewer_node)
        forbidden = {"thesis", "prior_scores", "prior_review", "rebuttal", "ms_thesis"}
        assert forbidden.isdisjoint(sig.parameters.keys())

    def test_skip_short_circuit_when_already_cleared(self):
        judge = _counting_judge(_ALL_PASS)
        run_state_meta = {"manuscript_review": {"cleared_at": 1}}
        result = rb.run_reviewer_node(
            "draft text", round_num=2, lens_num=1, K=3,
            judge_fn=judge, run_state_meta=run_state_meta,
        )
        assert result["skipped"] is True
        assert judge.calls == []  # NO judge call — proves the short-circuit

    def test_real_call_extracts_scores(self):
        judge = _uniform_judge(_ALL_PASS)
        result = rb.run_reviewer_node("draft text", round_num=1, lens_num=1, K=3, judge_fn=judge)
        assert result["skipped"] is False
        assert result["scores"] == _ALL_PASS

    def test_unparseable_response_defaults_all_zero(self):
        judge = lambda p: "not a score at all"
        result = rb.run_reviewer_node("draft text", round_num=1, lens_num=1, K=3, judge_fn=judge)
        assert all(v == 0 for v in result["scores"].values())

    def test_prompt_never_includes_a_thesis_marker(self):
        """Build the prompt and confirm it contains ONLY the lens + rubric +
        draft text — no extra 'thesis' slot exists to leak through."""
        captured = {}

        def _judge(prompt: str) -> str:
            captured["prompt"] = prompt
            return _score_response(**_ALL_PASS)

        rb.run_reviewer_node("MY UNIQUE DRAFT MARKER", round_num=1, lens_num=1, K=3, judge_fn=_judge)
        assert "MY UNIQUE DRAFT MARKER" in captured["prompt"]
        assert "MY SECRET AUTHOR THESIS" not in captured["prompt"]


# ---------------------------------------------------------------------------
# Canary scaffold
# ---------------------------------------------------------------------------

class TestRunCanaryScaffold:
    def test_skips_when_rubric_empty(self):
        result = rb.run_canary_scaffold(lambda p: "", rubric="")
        assert result["canary_ok"] is True
        assert "SKIPPED" in result["canary_note"]

    def test_well_calibrated_judge_passes_all_three_probes(self):
        def _judge(prompt: str) -> str:
            if rb._CANARY_STRONG_MARKER in prompt:
                return _score_response(SCOPE=5, REPRO=5, CITE=5, FRAME=4, SYNTH=4, COMPARE=4, GAP=4, BIAS=4)
            if rb._CANARY_WEAK_MARKER in prompt:
                return _score_response(SCOPE=1, REPRO=1, CITE=1, FRAME=1, SYNTH=1, COMPARE=1, GAP=1, BIAS=1)
            if rb._CANARY_ANNOTATED_BIB_MARKER in prompt:
                return _score_response(SCOPE=4, REPRO=4, CITE=4, FRAME=2, SYNTH=1, COMPARE=1, GAP=1, BIAS=2)
            return "no probe matched"

        result = rb.run_canary_scaffold(_judge, rb.DEFAULT_LIT_REVIEW_RUBRIC, floor_value=3)
        assert result["canary_ok"] is True

    def test_broken_harsh_judge_aborts_on_strong_probe(self):
        """The strong probe scored AT the floor (not above) -> blind rejector."""
        def _judge(prompt: str) -> str:
            if rb._CANARY_STRONG_MARKER in prompt:
                return _score_response(SCOPE=3, REPRO=3, CITE=3, FRAME=3, SYNTH=3, COMPARE=3, GAP=3, BIAS=3)
            return _score_response(**_ALL_FAIL)

        with pytest.raises(rb.CanaryAbortError, match="BROKEN-HARSH"):
            rb.run_canary_scaffold(_judge, rb.DEFAULT_LIT_REVIEW_RUBRIC, floor_value=3)

    def test_rubber_stamping_judge_aborts_on_weak_probe(self):
        """The weak probe scored AT the floor (not below) -> positivity bias."""
        def _judge(prompt: str) -> str:
            if rb._CANARY_STRONG_MARKER in prompt:
                return _score_response(**_ALL_PASS)
            if rb._CANARY_WEAK_MARKER in prompt:
                return _score_response(SCOPE=3, REPRO=3, CITE=3, FRAME=3, SYNTH=3, COMPARE=3, GAP=3, BIAS=3)
            return "unreached"

        with pytest.raises(rb.CanaryAbortError, match="RUBBER-STAMPING"):
            rb.run_canary_scaffold(_judge, rb.DEFAULT_LIT_REVIEW_RUBRIC, floor_value=3)

    def test_mandatory_annotated_bib_canary_aborts_when_it_would_clear_synth(self):
        """The #1 survey failure: a literal annotated bibliography scoring
        SYNTH >= floor must ABORT — the judge is blind to enumeration."""
        def _judge(prompt: str) -> str:
            if rb._CANARY_STRONG_MARKER in prompt:
                return _score_response(**_ALL_PASS)
            if rb._CANARY_WEAK_MARKER in prompt:
                return _score_response(**_ALL_FAIL)
            if rb._CANARY_ANNOTATED_BIB_MARKER in prompt:
                # positivity-biased on the annotated-bib probe: SYNTH clears
                return _score_response(SCOPE=4, REPRO=4, CITE=4, FRAME=3, SYNTH=4, COMPARE=3, GAP=3, BIAS=3)
            return "unreached"

        with pytest.raises(rb.CanaryAbortError, match="BLIND to the #1 survey failure"):
            rb.run_canary_scaffold(_judge, rb.DEFAULT_LIT_REVIEW_RUBRIC, floor_value=3)

    def test_unparseable_weak_probe_aborts(self):
        def _judge(prompt: str) -> str:
            if rb._CANARY_STRONG_MARKER in prompt:
                return _score_response(**_ALL_PASS)
            return "garbage, no brackets"

        with pytest.raises(rb.CanaryAbortError, match="UNPARSEABLE"):
            rb.run_canary_scaffold(_judge, rb.DEFAULT_LIT_REVIEW_RUBRIC, floor_value=3)


# ---------------------------------------------------------------------------
# Meta-review — floor-not-average, regression guard, escalation
# ---------------------------------------------------------------------------

class TestRunMetaReview:
    def test_cleared_when_all_floor_dims_meet_min(self):
        reviewers = [
            {"scores": dict(_ALL_PASS), "skipped": False, "escalation_fields": {"misfits": [], "reframe_candidates": []}}
            for _ in range(3)
        ]
        result = rb.run_meta_review(1, reviewers, floor_dims=["SCOPE", "REPRO", "CITE"], floor_value=3)
        assert result["cleared"] is True
        assert "reviewers approved" not in result["meta_review"].lower()
        assert "approved" not in result["meta_review"].lower()

    def test_regression_guard_flags_a_dropped_floor_dim(self):
        prior_floor = {"SCOPE": {"min_score": 4, "floor": 3, "passed": True}}
        reviewers = [
            {"scores": {"SCOPE": 2, "REPRO": 5, "CITE": 5}, "skipped": False, "escalation_fields": {"misfits": [], "reframe_candidates": []}}
            for _ in range(3)
        ]
        result = rb.run_meta_review(
            2, reviewers, floor_dims=["SCOPE", "REPRO", "CITE"], floor_value=3,
            prior_floor_results=prior_floor,
        )
        assert result["regression"]["regressed"] is True
        assert "SCOPE" in result["regression"]["dims"]
        assert "REGRESSION" in result["meta_review"]

    def test_no_regression_when_scores_hold_or_improve(self):
        prior_floor = {"SCOPE": {"min_score": 3, "floor": 3, "passed": True}}
        reviewers = [
            {"scores": {"SCOPE": 4, "REPRO": 5, "CITE": 5}, "skipped": False, "escalation_fields": {"misfits": [], "reframe_candidates": []}}
            for _ in range(3)
        ]
        result = rb.run_meta_review(
            2, reviewers, floor_dims=["SCOPE", "REPRO", "CITE"], floor_value=3,
            prior_floor_results=prior_floor,
        )
        assert result["regression"]["regressed"] is False

    def test_single_round_low_frame_does_not_yet_escalate(self):
        """PR-M8 tightening (design §5.1, "round after round"): a SINGLE
        round's weak FRAME + misfits is surfaced as a watching note, but does
        NOT build the formal escalation payload -- recurrence requires >= 2
        CONSECUTIVE rounds."""
        reviewers = [
            {
                "scores": {"SCOPE": 5, "REPRO": 5, "CITE": 5, "FRAME": 1},
                "skipped": False,
                "escalation_fields": {"misfits": ["paper A orphaned"], "reframe_candidates": ["axis-based"]},
            },
        ]
        result = rb.run_meta_review(1, reviewers, floor_dims=["SCOPE", "REPRO", "CITE"], floor_value=3)
        assert result["escalation"] is None
        assert result["frame_recurrence"]["streak_rounds"] == [1]
        assert "watching for recurrence" in result["meta_review"]

    def test_escalation_payload_built_after_two_consecutive_recurring_rounds(self):
        """The formal escalation only fires once the weak-FRAME-with-misfits
        condition has recurred in 2 CONSECUTIVE evaluated rounds -- design
        §5.1's literal "round after round" wording (PR-M8 tightening)."""
        round1_reviewers = [
            {
                "scores": {"SCOPE": 5, "REPRO": 5, "CITE": 5, "FRAME": 1},
                "skipped": False,
                "escalation_fields": {"misfits": ["paper A orphaned"], "reframe_candidates": ["axis-based"]},
            },
        ]
        round1 = rb.run_meta_review(1, round1_reviewers, floor_dims=["SCOPE", "REPRO", "CITE"], floor_value=3)
        assert round1["escalation"] is None  # not yet -- only one round so far

        round2_reviewers = [
            {
                "scores": {"SCOPE": 5, "REPRO": 5, "CITE": 5, "FRAME": 1},
                "skipped": False,
                "escalation_fields": {"misfits": ["paper B also orphaned"], "reframe_candidates": ["axis-based"]},
            },
        ]
        round2 = rb.run_meta_review(
            2, round2_reviewers, floor_dims=["SCOPE", "REPRO", "CITE"], floor_value=3,
            prior_frame_recurrence=round1["frame_recurrence"],
        )
        assert round2["escalation"] is not None
        assert round2["escalation"]["recurring_rounds"] == [1, 2]
        assert round2["escalation"]["recurring_misfits"] == ["paper A orphaned", "paper B also orphaned"]
        assert round2["escalation"]["candidate_reframes"] == ["axis-based", "axis-based"]
        # Surface-not-auto: the escalation is a PROPOSAL, not a mutation —
        # nothing in this function writes to disk or mutates ms_type/tree_root.

    def test_non_consecutive_weak_round_resets_the_streak(self):
        """A round where FRAME is fine (not "round after round") resets the
        recurrence streak -- a later weak round after a gap starts fresh."""
        weak_reviewers = [
            {
                "scores": {"SCOPE": 5, "REPRO": 5, "CITE": 5, "FRAME": 1},
                "skipped": False,
                "escalation_fields": {"misfits": ["misfit X"], "reframe_candidates": ["reframe X"]},
            },
        ]
        fine_reviewers = [
            {
                "scores": {"SCOPE": 5, "REPRO": 5, "CITE": 5, "FRAME": 4},
                "skipped": False,
                "escalation_fields": {"misfits": [], "reframe_candidates": []},
            },
        ]
        round1 = rb.run_meta_review(1, weak_reviewers, floor_dims=["SCOPE", "REPRO", "CITE"], floor_value=3)
        assert round1["frame_recurrence"]["streak_rounds"] == [1]

        round2 = rb.run_meta_review(
            2, fine_reviewers, floor_dims=["SCOPE", "REPRO", "CITE"], floor_value=3,
            prior_frame_recurrence=round1["frame_recurrence"],
        )
        assert round2["frame_recurrence"]["streak_rounds"] == []
        assert round2["escalation"] is None

        round3 = rb.run_meta_review(
            3, weak_reviewers, floor_dims=["SCOPE", "REPRO", "CITE"], floor_value=3,
            prior_frame_recurrence=round2["frame_recurrence"],
        )
        # Fresh streak of length 1 -- NOT treated as continuing round 1's streak.
        assert round3["frame_recurrence"]["streak_rounds"] == [3]
        assert round3["escalation"] is None

    def test_no_escalation_when_frame_score_is_fine(self):
        reviewers = [
            {
                "scores": {"SCOPE": 5, "REPRO": 5, "CITE": 5, "FRAME": 4},
                "skipped": False,
                "escalation_fields": {"misfits": [], "reframe_candidates": []},
            },
        ]
        result = rb.run_meta_review(1, reviewers, floor_dims=["SCOPE", "REPRO", "CITE"], floor_value=3)
        assert result["escalation"] is None

    def test_skip_short_circuit_when_already_cleared(self):
        run_state_meta = {"manuscript_review": {"cleared_at": 1}}
        result = rb.run_meta_review(2, [], run_state_meta=run_state_meta)
        assert result["skipped"] is True
        assert result["cleared"] is True


# ---------------------------------------------------------------------------
# run_revise — single-sourced re-fire via check_gates.build_approve_payload
# ---------------------------------------------------------------------------

class TestRunRevise:
    def test_calls_build_approve_payload_not_a_duplicate(self):
        """Call-graph proof (review-board.md's false-SSOT technique): confirm
        run_revise's AST LITERALLY calls build_approve_payload, and contains
        NO call to check_hermetic_bib/check_support_tally/check_cold_read_tally/
        check_equation_fidelity (a re-implementation would call one of these
        directly instead of going through the single-sourced assembler).

        AST-based (not a raw ``getsource`` substring match, which a stray
        comment could satisfy vacuously — rule 7 / lint.py's getsource-guard).
        """
        import ast
        import textwrap

        src = textwrap.dedent(inspect.getsource(rb.run_revise))
        tree = ast.parse(src)
        called_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    called_names.add(func.id)
                elif isinstance(func, ast.Attribute):
                    called_names.add(func.attr)

        assert "build_approve_payload" in called_names
        assert called_names.isdisjoint(
            {"check_hermetic_bib", "check_support_tally", "check_cold_read_tally", "check_equation_fidelity"}
        )

    def test_returns_gate_payload_and_honesty_flag(self, tmp_path):
        from research_vault.manuscript.types import get_type

        project_notes_dir = tmp_path / "notes"
        tree_root = project_notes_dir / "manuscripts" / "survey-x"
        (tree_root / "sections").mkdir(parents=True, exist_ok=True)
        (tree_root / "refs.bib").write_text("", encoding="utf-8")
        ms_type = get_type("lit-review")

        result = rb.run_revise(1, {"meta_review": "concern"}, tree_root, project_notes_dir, ms_type)
        assert "gate_payload" in result

    def test_dangling_cite_re_fire_blocks_via_real_hermetic_bib_gate(self, tmp_path):
        """A revise whose (mock) re-draft still has a dangling \\cite{} must
        BLOCK — proven through the REAL check_hermetic_bib gate (single-
        sourced via build_approve_payload), not a stubbed result."""
        from research_vault.manuscript.types import get_type

        project_notes_dir = tmp_path / "notes"
        tree_root = project_notes_dir / "manuscripts" / "survey-dangling"
        (tree_root / "sections").mkdir(parents=True, exist_ok=True)
        (tree_root / "sections" / "intro.tex").write_text(
            r"We note X \cite{nosuchpaper2099}.", encoding="utf-8",
        )
        (tree_root / "refs.bib").write_text("", encoding="utf-8")
        ms_type = get_type("lit-review")

        result = rb.run_revise(1, {"meta_review": "concern"}, tree_root, project_notes_dir, ms_type)
        assert result["honesty_gate_blocked"] is True
        assert any("hermetic-bib" in b for b in result["blocking"])
        assert "honesty_gate_blocked" in result
        assert "not a verdict" in result["rebuttal"]


# ---------------------------------------------------------------------------
# run_review_board — the full bounded 2x3 loop
# ---------------------------------------------------------------------------

class TestRunReviewBoard:
    def _tree(self, tmp_path):
        project_notes_dir = tmp_path / "notes"
        tree_root = project_notes_dir / "manuscripts" / "survey-y"
        (tree_root / "sections").mkdir(parents=True, exist_ok=True)
        (tree_root / "refs.bib").write_text("", encoding="utf-8")
        return project_notes_dir, tree_root

    def test_clears_at_round_one_round_two_is_a_true_noop(self, tmp_path):
        from research_vault.manuscript.types import get_type

        project_notes_dir, tree_root = self._tree(tmp_path)
        judge = _counting_judge(_ALL_PASS)
        ms_type = get_type("lit-review")

        result = rb.run_review_board(
            "draft text", tree_root, project_notes_dir, ms_type,
            judge_fn=judge, floor_dims=["SCOPE", "REPRO", "CITE"], floor_value=3,
        )

        assert result["cleared"] is True
        assert result["cleared_at"] == 1
        # Exactly K=3 judge calls made — round 2's 3 reviewers never fired.
        assert len(judge.calls) == 3
        assert result["rounds"][1]["skipped"] is True

    def test_below_floor_fires_revise_and_runs_round_two(self, tmp_path):
        from research_vault.manuscript.types import get_type

        project_notes_dir, tree_root = self._tree(tmp_path)
        judge = _counting_judge(_ALL_FAIL)
        ms_type = get_type("lit-review")

        result = rb.run_review_board(
            "draft text", tree_root, project_notes_dir, ms_type,
            judge_fn=judge, floor_dims=["SCOPE", "REPRO", "CITE"], floor_value=3,
        )

        assert result["cleared"] is False
        # Round 1 (3 reviewers) + round 2 (3 reviewers) = 6 real judge calls.
        assert len(judge.calls) == 6
        assert result["rounds"][0]["revise"] is not None

    def test_not_cleared_after_n_is_a_first_class_honest_payload(self, tmp_path):
        from research_vault.manuscript.types import get_type

        project_notes_dir, tree_root = self._tree(tmp_path)
        judge = _uniform_judge(_ALL_FAIL)
        ms_type = get_type("lit-review")

        result = rb.run_review_board(
            "draft text", tree_root, project_notes_dir, ms_type,
            judge_fn=judge, floor_dims=["SCOPE", "REPRO", "CITE"], floor_value=3,
        )
        assert result["cleared"] is False
        assert result["not_cleared"] is not None
        assert "persistent_weakness" in result["not_cleared"]
        assert "NOT cleared" in result["honest_report"]
        assert "approved" not in result["honest_report"].lower()

    def test_min_across_three_gates_one_bad_reviewer_blocks(self, tmp_path):
        from research_vault.manuscript.types import get_type

        project_notes_dir, tree_root = self._tree(tmp_path)
        ms_type = get_type("lit-review")
        call_count = {"n": 0}

        def _judge(prompt: str) -> str:
            call_count["n"] += 1
            # Reviewer 3 (every 3rd call within a round) tanks CITE.
            if call_count["n"] % 3 == 0:
                return _score_response(SCOPE=5, REPRO=5, CITE=1, FRAME=4, SYNTH=4, COMPARE=4, GAP=4, BIAS=4)
            return _score_response(**_ALL_PASS)

        result = rb.run_review_board(
            "draft text", tree_root, project_notes_dir, ms_type,
            judge_fn=_judge, floor_dims=["SCOPE", "REPRO", "CITE"], floor_value=3,
        )
        # A MEAN of (5,5,1)=3.67 would clear; MIN-across-3 correctly does not.
        assert result["cleared"] is False

    def test_n_and_k_are_frozen_hard_cap(self, tmp_path):
        from research_vault.manuscript.types import get_type

        project_notes_dir, tree_root = self._tree(tmp_path)
        judge = _counting_judge(_ALL_FAIL)
        ms_type = get_type("lit-review")

        result = rb.run_review_board(
            "draft text", tree_root, project_notes_dir, ms_type,
            judge_fn=judge, N=5, K=10,   # asking for more than the hard-cap
            floor_dims=["SCOPE", "REPRO", "CITE"], floor_value=3,
        )
        assert result["n_rounds_run"] == 3          # clamped to _MAX_ROUNDS_HARDCAP
        assert result["n_reviewers_per_round"] == 10  # K has no hard-cap, only a floor

    def test_round_two_reviewers_never_see_round_one_content(self, tmp_path):
        """Fresh-by-construction: no round-1 review text, rebuttal, or thesis
        leaks into a round-2 reviewer's prompt."""
        from research_vault.manuscript.types import get_type

        project_notes_dir, tree_root = self._tree(tmp_path)
        ms_type = get_type("lit-review")
        prompts: list[str] = []

        def _judge(prompt: str) -> str:
            prompts.append(prompt)
            return _score_response(**_ALL_FAIL)

        rb.run_review_board(
            "MY DRAFT MARKER", tree_root, project_notes_dir, ms_type,
            judge_fn=_judge, floor_dims=["SCOPE", "REPRO", "CITE"], floor_value=3,
        )
        # None of round 2's prompts should contain "REBUTTAL" or "Round 1"
        # meta-review text — only the lens + rubric + the (same) draft text.
        round_two_prompts = prompts[3:6]
        for p in round_two_prompts:
            assert "REBUTTAL" not in p
            assert "Round 1" not in p

    def test_reframe_writes_candidates_never_auto_reframes(self, tmp_path):
        """The escalation is surfaced but nothing mutates the manuscript
        tree, the type registry, or auto-commits a new spine. PR-M8
        tightening: the same judge fires the SAME weak-FRAME-with-misfits
        response every round, so the board never clears (floor dims held
        below floor both rounds) and BOTH of the two default rounds run --
        the recurrence needed for the escalation to actually fire."""
        from research_vault.manuscript.types import get_type

        project_notes_dir, tree_root = self._tree(tmp_path)
        ms_type = get_type("lit-review")

        def _judge(prompt: str) -> str:
            if "FRAMEWORK / TAXONOMY CRITIC" in prompt:
                return (
                    "[SCOPE:2]\n[REPRO:2]\n[CITE:2]\n[FRAME:1]\n"
                    "MISFITS: paper A doesn't fit any branch\n"
                    "REFRAME_CANDIDATES: axis-based reframe\n"
                    "[SYNTH:4]\n[COMPARE:4]\n[GAP:4]\n[BIAS:4]\n"
                )
            return _score_response(SCOPE=2, REPRO=2, CITE=2, FRAME=4, SYNTH=4, COMPARE=4, GAP=4, BIAS=4)

        before = sorted(p.name for p in tree_root.rglob("*"))
        result = rb.run_review_board(
            "draft text", tree_root, project_notes_dir, ms_type,
            judge_fn=_judge, floor_dims=["SCOPE", "REPRO", "CITE"], floor_value=3,
        )
        after = sorted(p.name for p in tree_root.rglob("*"))

        assert result["escalation"] is not None
        assert result["escalation"]["recurring_rounds"] == [1, 2]
        assert result["escalation"]["candidate_reframes"] == ["axis-based reframe", "axis-based reframe"]
        assert before == after  # nothing was written to the tree by this call

    def test_requires_judge_fn(self, tmp_path):
        from research_vault.manuscript.types import get_type

        project_notes_dir, tree_root = self._tree(tmp_path)
        ms_type = get_type("lit-review")
        with pytest.raises(RuntimeError, match="judge_fn is required"):
            rb.run_review_board("draft", tree_root, project_notes_dir, ms_type, judge_fn=None)


# ---------------------------------------------------------------------------
# [manuscript_review] config seam
# ---------------------------------------------------------------------------

class TestGetReviewConfig:
    def test_defaults(self):
        cfg = rb.get_review_config(None)
        assert cfg["max_rounds"] == 2
        assert cfg["reviewers_per_round"] == 3
        assert cfg["floor_value"] == 3
        assert set(cfg["floor_dimensions"]) == {"CITE", "SCOPE", "REPRO"}
        assert cfg["aggregation"] == "min"

    def test_max_rounds_hard_capped(self):
        class _Cfg:
            _raw = {"manuscript_review": {"max_rounds": 10}}
        cfg = rb.get_review_config(_Cfg())
        assert cfg["max_rounds"] == 3

    def test_reviewers_per_round_floored_at_two(self):
        class _Cfg:
            _raw = {"manuscript_review": {"reviewers_per_round": 1}}
        cfg = rb.get_review_config(_Cfg())
        assert cfg["reviewers_per_round"] == 2

    def test_floor_dimensions_alias_expansion(self):
        class _Cfg:
            _raw = {"manuscript_review": {"floor_dimensions": ["coverage_reproducibility"]}}
        cfg = rb.get_review_config(_Cfg())
        assert set(cfg["floor_dimensions"]) == {"SCOPE", "REPRO"}

    def test_floor_dimensions_pass_through_dim_codes(self):
        class _Cfg:
            _raw = {"manuscript_review": {"floor_dimensions": ["SCOPE", "CITE"]}}
        cfg = rb.get_review_config(_Cfg())
        assert cfg["floor_dimensions"] == ["SCOPE", "CITE"]


# ---------------------------------------------------------------------------
# cmd_review wiring — the judge guard + config-driven review
# ---------------------------------------------------------------------------

class TestCmdReviewWiring:
    def _scaffold(self, tmp_path, monkeypatch):
        from research_vault.config import load_config
        from research_vault.manuscript import cmd_new

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
        monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(config_file))
        cfg = load_config(reload=True)
        cmd_new("demo-research", "survey-z", ms_type_key="lit-review", config=cfg)
        return cfg

    def test_raises_loudly_with_no_judge_configured(self, tmp_path, monkeypatch):
        from research_vault.manuscript import cmd_review

        monkeypatch.delenv("RV_JUDGE_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        cfg = self._scaffold(tmp_path, monkeypatch)

        with pytest.raises(RuntimeError, match="no judge configured"):
            cmd_review("demo-research", "survey-z", config=cfg)

    def test_explicit_judge_fn_counts_as_configured(self, tmp_path, monkeypatch):
        from research_vault.manuscript import cmd_review

        monkeypatch.delenv("RV_JUDGE_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        cfg = self._scaffold(tmp_path, monkeypatch)

        judge = _canary_aware_judge(_ALL_PASS)
        result = cmd_review("demo-research", "survey-z", config=cfg, judge_fn=judge)
        assert result["cleared"] is True

    def test_stamps_review_meta_onto_manuscript_note(self, tmp_path, monkeypatch):
        from research_vault.manuscript import cmd_review, _manuscript_tree_root

        monkeypatch.delenv("RV_JUDGE_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        cfg = self._scaffold(tmp_path, monkeypatch)

        judge = _canary_aware_judge(_ALL_PASS)
        cmd_review("demo-research", "survey-z", config=cfg, judge_fn=judge)

        tree_root = _manuscript_tree_root("demo-research", "survey-z", cfg)
        note_text = (tree_root / "_manuscript.md").read_text(encoding="utf-8")
        assert "manuscript_review run" in note_text
        assert "cleared=True" in note_text

    def test_unregistered_manuscript_type_fails_loudly(self, tmp_path, monkeypatch):
        from research_vault.manuscript import cmd_review, _manuscript_tree_root

        cfg = self._scaffold(tmp_path, monkeypatch)
        tree_root = _manuscript_tree_root("demo-research", "survey-z", cfg)
        note_path = tree_root / "_manuscript.md"
        text = note_path.read_text(encoding="utf-8")
        note_path.write_text(text.replace("manuscript_type: lit-review", "manuscript_type: bogus"), encoding="utf-8")

        with pytest.raises(ValueError, match="unknown --type"):
            cmd_review("demo-research", "survey-z", config=cfg, judge_fn=_uniform_judge(_ALL_PASS))


# ---------------------------------------------------------------------------
# Import-diff cleanliness: this module must not touch the DAG core
# ---------------------------------------------------------------------------

class TestNoDagCoreImport:
    def test_review_board_does_not_import_walker_or_schema(self):
        src = inspect.getsource(rb)
        assert "dag.walker" not in src
        assert "dag.schema" not in src
        assert "dag.store" not in src
