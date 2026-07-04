"""test_sr_ms_review_b.py — SR-MS-REVIEW-b: Ada's rubric + reviewer-lens specs + calibrated bidirectional canary.

Tests (all hermetic, mock judge, no LLM calls):
  1.  DEFAULT_REVIEW_RUBRIC is NOT the placeholder (real rubric ships in -b)
  2.  DEFAULT_REVIEW_RUBRIC emits all 7 dim tokens in its OUTPUT block
  3.  DEFAULT_REVIEW_RUBRIC contains C5 binding text (REPRO capped at 2 on absent provenance)
  4.  DEFAULT_REVIEW_RUBRIC contains the adversarial posture (disconfirm-first)
  5.  CanaryAbortError is exported from review_board
  6.  Canary ABORTS on HARSH judge (strong probe SOUND=3, REPRO=4 — SOUND below ≥4 bound)
  7.  Canary ABORTS on RUBBER-STAMP judge (weak probe SOUND=4 ≥ floor 3 — positivity bias)
  8.  Canary passes on WELL-CALIBRATED judge (strong: SOUND=4, REPRO=4; weak: SOUND=1, REPRO=1)
  9.  Strong-bound non-vacuous sentinel: SOUND=3 on strong probe → ABORT (loosen 4→3: test RED)
  10. Weak-bound non-vacuous sentinel: SOUND=3 on weak probe → ABORT (loosen 3→4: test RED)
  11. Canary parse-failure is out-of-bounds → ABORT (unscoreable canary never certifies the judge)
  12. C5 binding: NO provenance apparatus → REPRO ≤ 2 → not cleared (non-vacuous: remove REPRO → RED)
  13. C5 binding: WITH provenance apparatus → REPRO can score higher → cleared
  14. L377 partial-omit guard: judge emits [SOUND:4] only, omits [REPRO] → REPRO=0 → not cleared
      (mutation guard: .get(dim, 0) → .get(dim, 5) → cleared=True → RED)
  15. get_reviewer_lens_spec: K=3 → L1/L2/L3 for k=1/2/3
  16. get_reviewer_lens_spec: K=2 → L1 for k=1, L3 for k=2 (floor-carrying pair)
  17. get_reviewer_lens_spec: K=1 → L1 always
  18. Lens specs in manifest node spec (integration): reviewer-1-L1 spec contains L1 posture text
  19. Lens spec postures are prepended (not replacing) rubric/score requirement
  20. run_canary_scaffold skips when rubric is empty (backward-compat with -a tests)
  21. Canary abort propagates via run_meta_review → canary_ok=False in meta
  22. DEFAULT_REVIEW_RUBRIC is the seam default returned by get_review_rubric(None, None)

All hermetic (tmp_path). No live LLM calls. Stdlib only.
sr: SR-MS-REVIEW-b
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# Shared test helpers (mirror -a helpers for local use)
# ---------------------------------------------------------------------------

def _make_reviewer_response(
    sound: int = 4,
    contrib: int = 3,
    clarity: int = 4,
    orig: int = 3,
    limit: int = 3,
    repro: int = 4,
    ethics: int = 4,
) -> str:
    """Return a well-formed reviewer response with all 7 dimension scores."""
    return (
        f"[SOUND:{sound}]\n"
        f"WEAKNESS: The central claim lacks independent replication.\n"
        f"JUSTIFY: \"We evaluated on our proposed benchmark\"\n"
        f"CONF: high\n\n"
        f"[CONTRIB:{contrib}]\n"
        f"WEAKNESS: Incremental over prior work.\n"
        f"JUSTIFY: \"Building on the approach of [4]\"\n"
        f"CONF: med\n\n"
        f"[CLARITY:{clarity}]\n"
        f"WEAKNESS: Notation is dense in Section 3.\n"
        f"JUSTIFY: \"Let h = f(g(x; θ); φ)\"\n"
        f"CONF: high\n\n"
        f"[ORIG:{orig}]\n"
        f"WEAKNESS: The core idea appears in [2].\n"
        f"JUSTIFY: \"similar to the approach of\"\n"
        f"CONF: med\n\n"
        f"[LIMIT:{limit}]\n"
        f"WEAKNESS: No failure modes discussed.\n"
        f"JUSTIFY: \"We do not discuss\"\n"
        f"CONF: high\n\n"
        f"[REPRO:{repro}]\n"
        f"WEAKNESS: Seeds not reported.\n"
        f"JUSTIFY: \"Results averaged over runs\"\n"
        f"CONF: high\n\n"
        f"[ETHICS:{ethics}]\n"
        f"WEAKNESS: No responsible-use statement.\n"
        f"JUSTIFY: \"We do not address ethics\"\n"
        f"CONF: med\n\n"
        f"CHECKLIST (responsible-AI, Yes/No/NA):\n"
        f"  DATA_LICENSING: NA — No external data.\n"
        f"  FORESEEABLE_HARM: No — Evaluation only.\n"
        f"  DUAL_USE: NA — Academic tool.\n\n"
        f"SUMMARY:\n"
        f"  FLOOR_DIMS: SOUND={sound} REPRO={repro}\n"
        f"  WORST_OBJECTION: Central claim lacks independent evidence.\n"
        f"  SWEPT: Read the entire paper adversarially, disconfirm-first.\n"
    )


def _make_ms_tree(tmp_path: Path, ms_id: str = "ms-test") -> tuple[Path, Path, Path]:
    """Create a minimal manuscript tree for testing."""
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)

    ms_dir = project_root / "manuscript"
    ms_dir.mkdir(parents=True, exist_ok=True)
    note_path = ms_dir / f"{ms_id}.md"
    note_path.write_text(
        "---\ntype: manuscript\nthesis: Test thesis\nsynthesized_okf: \nmanuscript_pdf: \nresults_hash: \n---\n",
        encoding="utf-8",
    )

    tree_root = project_root / "manuscripts" / ms_id
    sections_dir = tree_root / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)

    (tree_root / "main.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nTest content.\n\\end{document}\n",
        encoding="utf-8",
    )
    return note_path, tree_root, project_root


def _write_bib(tree_root: Path, entries: str = "") -> None:
    """Write a refs.bib file."""
    (tree_root / "refs.bib").write_text(
        "% refs.bib\n" + entries,
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Canary marker helpers (detect which probe is being fired by prompt content)
# ---------------------------------------------------------------------------

def _is_strong_probe(prompt: str) -> bool:
    """True if the prompt contains Ada's known-STRONG canary passage."""
    # Unique phrase from CANARY_STRONG_PASSAGE
    return "holistic fidelity score" in prompt


def _is_weak_probe(prompt: str) -> bool:
    """True if the prompt contains Ada's known-WEAK canary passage."""
    # Unique phrase from CANARY_WEAK_PASSAGE (single-line marker — no line-break issue)
    return "clearly the best" in prompt


# ---------------------------------------------------------------------------
# Mock judges for canary tests
# ---------------------------------------------------------------------------

def _harsh_judge(prompt: str) -> str:
    """HARSH judge: scores the strong probe SOUND=3 (below ≥4 bound) → should ABORT."""
    if _is_strong_probe(prompt):
        # Strong probe: SOUND=3 < 4 → canary must ABORT
        return _make_reviewer_response(sound=3, repro=4)
    # Weak probe: scores below floor (correct for a harsh judge)
    return _make_reviewer_response(sound=1, repro=1)


def _rubber_stamp_judge(prompt: str) -> str:
    """RUBBER-STAMP judge: scores the weak probe SOUND=4 (≥ floor 3) → should ABORT."""
    if _is_strong_probe(prompt):
        # Strong probe: passes correctly
        return _make_reviewer_response(sound=4, repro=4)
    # Weak probe: SOUND=4, REPRO=4 → w_sound=4 ≥ 3 → canary must ABORT
    return _make_reviewer_response(sound=4, repro=4)


def _calibrated_judge(prompt: str) -> str:
    """WELL-CALIBRATED judge: strong→4,4; weak→1,1. Canary should pass."""
    if _is_strong_probe(prompt):
        return _make_reviewer_response(sound=4, repro=4)
    if _is_weak_probe(prompt):
        return _make_reviewer_response(sound=1, repro=1)
    # Regular paper: above floor
    return _make_reviewer_response(sound=4, repro=4)


def _borderline_harsh_judge(prompt: str) -> str:
    """Borderline harsh: SOUND=3 on strong probe (strong bound is strictly ≥4, not ≥3)."""
    if _is_strong_probe(prompt):
        return _make_reviewer_response(sound=3, repro=4)  # SOUND=3 < 4 → ABORT
    return _make_reviewer_response(sound=1, repro=1)


def _borderline_rubber_stamp_judge(prompt: str) -> str:
    """Borderline rubber-stamp: SOUND=3 on weak probe (≥ floor 3 triggers abort)."""
    if _is_strong_probe(prompt):
        return _make_reviewer_response(sound=4, repro=4)
    # Weak probe: SOUND=3 ≥ floor=3 → ABORT (dead-band at floor disallowed)
    return _make_reviewer_response(sound=3, repro=2)


def _unparseable_canary_judge(prompt: str) -> str:
    """Judge that returns garbage for canary probes (no bracket tokens)."""
    return "I cannot assess this. The methodology is unclear and I have no basis for scoring."


# ---------------------------------------------------------------------------
# Test 1: DEFAULT_REVIEW_RUBRIC is NOT the placeholder
# ---------------------------------------------------------------------------

def test_default_review_rubric_is_not_placeholder():
    """DEFAULT_REVIEW_RUBRIC in -b is Ada's real rubric, not the placeholder."""
    from research_vault.manuscript.review_board import DEFAULT_REVIEW_RUBRIC

    assert DEFAULT_REVIEW_RUBRIC is not None
    # Must NOT be the placeholder (Ada's rubric replaces it)
    assert "PLACEHOLDER" not in DEFAULT_REVIEW_RUBRIC.upper(), (
        "DEFAULT_REVIEW_RUBRIC must be Ada's real rubric in -b, not the placeholder"
    )
    # Must be a substantial rubric (not empty / trivial)
    assert len(DEFAULT_REVIEW_RUBRIC) > 500, (
        "DEFAULT_REVIEW_RUBRIC must be Ada's real rubric (>500 chars)"
    )


# ---------------------------------------------------------------------------
# Test 2: DEFAULT_REVIEW_RUBRIC emits all 7 dim tokens
# ---------------------------------------------------------------------------

def test_default_review_rubric_has_all_7_dims():
    """DEFAULT_REVIEW_RUBRIC's OUTPUT block specifies all 7 dim tokens."""
    from research_vault.manuscript.review_board import DEFAULT_REVIEW_RUBRIC

    for dim in ("SOUND", "CONTRIB", "CLARITY", "ORIG", "LIMIT", "REPRO", "ETHICS"):
        assert f"[{dim}:" in DEFAULT_REVIEW_RUBRIC or dim in DEFAULT_REVIEW_RUBRIC, (
            f"DEFAULT_REVIEW_RUBRIC must reference [{dim}:N] token"
        )


# ---------------------------------------------------------------------------
# Test 3: DEFAULT_REVIEW_RUBRIC contains C5 binding text
# ---------------------------------------------------------------------------

def test_default_review_rubric_c5_binding():
    """DEFAULT_REVIEW_RUBRIC's C5 explicitly caps REPRO at 2 on absent provenance."""
    from research_vault.manuscript.review_board import DEFAULT_REVIEW_RUBRIC

    # C5 states REPRO is capped at 2 when provenance is absent
    rubric_lower = DEFAULT_REVIEW_RUBRIC.lower()
    assert "caps reproducibility at 2" in rubric_lower or "cap" in rubric_lower and "repro" in rubric_lower, (
        "DEFAULT_REVIEW_RUBRIC must contain C5 binding: REPRO capped at 2 when provenance absent"
    )
    # Also check the floor binding text is present
    assert "provenance" in rubric_lower, (
        "DEFAULT_REVIEW_RUBRIC must reference provenance binding for REPRO"
    )


# ---------------------------------------------------------------------------
# Test 4: DEFAULT_REVIEW_RUBRIC contains adversarial disconfirm-first posture
# ---------------------------------------------------------------------------

def test_default_review_rubric_adversarial_posture():
    """DEFAULT_REVIEW_RUBRIC instructs the judge to disconfirm first."""
    from research_vault.manuscript.review_board import DEFAULT_REVIEW_RUBRIC

    rubric_lower = DEFAULT_REVIEW_RUBRIC.lower()
    assert "disconfirm" in rubric_lower or "adversarial" in rubric_lower, (
        "DEFAULT_REVIEW_RUBRIC must include the adversarial disconfirm-first posture"
    )


# ---------------------------------------------------------------------------
# Test 5: CanaryAbortError is exported
# ---------------------------------------------------------------------------

def test_canary_abort_error_exported():
    """CanaryAbortError is accessible from review_board (exported exception class)."""
    from research_vault.manuscript.review_board import CanaryAbortError

    assert issubclass(CanaryAbortError, Exception), (
        "CanaryAbortError must be an Exception subclass"
    )
    # Can be raised and caught
    with pytest.raises(CanaryAbortError):
        raise CanaryAbortError("test abort")


# ---------------------------------------------------------------------------
# Test 6: Canary ABORTS on HARSH judge (strong probe SOUND=3 < 4)
# ---------------------------------------------------------------------------

def test_canary_aborts_on_harsh_judge():
    """Harsh judge (strong probe SOUND=3) → CanaryAbortError raised."""
    from research_vault.manuscript.review_board import (
        run_canary_scaffold, CanaryAbortError, DEFAULT_REVIEW_RUBRIC,
    )

    with pytest.raises(CanaryAbortError) as exc_info:
        run_canary_scaffold(_harsh_judge, rubric=DEFAULT_REVIEW_RUBRIC)

    err_msg = str(exc_info.value)
    # Must mention the probe result and expected condition
    assert "SOUND" in err_msg or "harsh" in err_msg.lower() or "rejector" in err_msg.lower(), (
        f"CanaryAbortError must describe the failure; got: {err_msg!r}"
    )


# ---------------------------------------------------------------------------
# Test 7: Canary ABORTS on RUBBER-STAMP judge (weak probe SOUND=4 ≥ 3)
# ---------------------------------------------------------------------------

def test_canary_aborts_on_rubber_stamp_judge():
    """Rubber-stamp judge (weak probe SOUND=4) → CanaryAbortError raised (positivity bias)."""
    from research_vault.manuscript.review_board import (
        run_canary_scaffold, CanaryAbortError, DEFAULT_REVIEW_RUBRIC,
    )

    with pytest.raises(CanaryAbortError) as exc_info:
        run_canary_scaffold(_rubber_stamp_judge, rubric=DEFAULT_REVIEW_RUBRIC)

    err_msg = str(exc_info.value)
    assert "rubber" in err_msg.lower() or "stamp" in err_msg.lower() or "positivity" in err_msg.lower() or "SOUND" in err_msg, (
        f"CanaryAbortError must describe rubber-stamp failure; got: {err_msg!r}"
    )


# ---------------------------------------------------------------------------
# Test 8: Canary passes on WELL-CALIBRATED judge
# ---------------------------------------------------------------------------

def test_canary_passes_well_calibrated_judge():
    """Well-calibrated judge (strong→4,4; weak→1,1) → canary_ok=True, no exception."""
    from research_vault.manuscript.review_board import (
        run_canary_scaffold, DEFAULT_REVIEW_RUBRIC,
    )

    result = run_canary_scaffold(_calibrated_judge, rubric=DEFAULT_REVIEW_RUBRIC)

    assert result["canary_ok"] is True, (
        "Well-calibrated judge must return canary_ok=True"
    )


# ---------------------------------------------------------------------------
# Test 9: Strong-bound non-vacuous sentinel (SOUND=3 on strong probe → ABORT)
# ---------------------------------------------------------------------------

def test_canary_strong_bound_non_vacuous_sentinel():
    """Borderline harsh judge (SOUND=3 on strong probe) → ABORT.

    Non-vacuous proof: if strong bound is loosened 4→3, then s_sound=3 ≥ 3
    no longer triggers ABORT → this test goes RED.
    Confirms the bound is strictly ≥4, not ≥3.
    """
    from research_vault.manuscript.review_board import (
        run_canary_scaffold, CanaryAbortError, DEFAULT_REVIEW_RUBRIC,
    )

    # Borderline judge: SOUND=3 on strong probe (just below ≥4 bound)
    # Mutation guard: loosen bound 4→3 → s_sound=3 no longer aborts → test RED ✓
    with pytest.raises(CanaryAbortError):
        run_canary_scaffold(_borderline_harsh_judge, rubric=DEFAULT_REVIEW_RUBRIC)


# ---------------------------------------------------------------------------
# Test 10: Weak-bound non-vacuous sentinel (SOUND=3 on weak probe → ABORT)
# ---------------------------------------------------------------------------

def test_canary_weak_bound_non_vacuous_sentinel():
    """Borderline rubber-stamp (SOUND=3 on weak probe) → ABORT.

    Non-vacuous proof: if weak bound is loosened (ABORT only if ≥4 instead of ≥3),
    then w_sound=3 < 4 no longer triggers ABORT → this test goes RED.
    Confirms floor=3 is the exact abort threshold for the weak probe.
    """
    from research_vault.manuscript.review_board import (
        run_canary_scaffold, CanaryAbortError, DEFAULT_REVIEW_RUBRIC,
    )

    # SOUND=3 on weak probe: at the floor (3) → abort (dead-band disallowed)
    # Mutation guard: loosen weak bound 3→4 → SOUND=3 no longer aborts → RED ✓
    with pytest.raises(CanaryAbortError):
        run_canary_scaffold(_borderline_rubber_stamp_judge, rubric=DEFAULT_REVIEW_RUBRIC)


# ---------------------------------------------------------------------------
# Test 11: Canary parse-failure → ABORT (unscoreable canary never certifies judge)
# ---------------------------------------------------------------------------

def test_canary_parse_failure_aborts():
    """Judge returns garbage for canary probe → parse fails → ABORT.

    An unscoreable canary (no bracket tokens) is out-of-bounds and must abort,
    not silently pass. Fail-closed: missing SOUND/REPRO → treated as 0 < 4 → ABORT.
    """
    from research_vault.manuscript.review_board import (
        run_canary_scaffold, CanaryAbortError, DEFAULT_REVIEW_RUBRIC,
    )

    # Fail-closed: no bracket tokens → scores=None → SOUND=0 < 4 → ABORT
    with pytest.raises(CanaryAbortError):
        run_canary_scaffold(_unparseable_canary_judge, rubric=DEFAULT_REVIEW_RUBRIC)


# ---------------------------------------------------------------------------
# Test 12: C5 binding — no provenance → REPRO ≤ 2 → not cleared
# ---------------------------------------------------------------------------

def test_c5_no_provenance_repro_capped_not_cleared(tmp_path):
    """C5 binding: paper with no reproducibility apparatus → REPRO ≤ 2 → not cleared.

    Non-vacuous: if REPRO is removed from floor_dims → SOUND=4 ≥ 3 → cleared=True → RED.
    The C5 binding means the rubric instructs the judge to cap REPRO at ≤2 when
    provenance apparatus is absent. This mock judge simulates a C5-compliant judge.
    """
    from research_vault.manuscript.review_board import run_review_board

    def _c5_judge_no_provenance(prompt: str) -> str:
        """C5-compliant judge: no provenance in paper → REPRO capped at 1 (below floor)."""
        # This mock simulates what the real rubric requires (C5): absent provenance → REPRO ≤ 2
        return _make_reviewer_response(sound=4, repro=1)

    _, tree_root, project_root = _make_ms_tree(tmp_path)
    _write_bib(tree_root)

    result = run_review_board(
        pdf_text="Our method clearly outperforms all baselines. Results confirm the hypothesis.",
        tree_root=tree_root,
        N=1,
        K=1,
        floor_dims=["SOUND", "REPRO"],  # REPRO is a floor dim — non-vacuous: remove → RED
        floor_value=3,
        judge_fn=_c5_judge_no_provenance,
        judge_model="mock-model",
        notes_root=project_root,
    )

    assert result["cleared"] is False, (
        "No-provenance paper must NOT clear: C5 caps REPRO at ≤2, which is below floor=3. "
        "Non-vacuous: remove REPRO from floor_dims → cleared=True → this test RED."
    )

    # Verify the REPRO score was ≤ 2 (C5 cap applied by judge)
    rounds = result["rounds"]
    r1 = rounds[0]
    rv_scores = r1["reviewers"][0]["scores"]
    assert rv_scores.get("REPRO", 0) <= 2, (
        f"C5: no provenance → REPRO must be ≤ 2; got {rv_scores.get('REPRO', 0)}"
    )


# ---------------------------------------------------------------------------
# Test 13: C5 binding — with provenance → REPRO can score higher → cleared
# ---------------------------------------------------------------------------

def test_c5_with_provenance_repro_can_clear(tmp_path):
    """C5 binding: paper WITH reproducibility apparatus → REPRO ≥ 3 → can clear."""
    from research_vault.manuscript.review_board import run_review_board

    def _c5_judge_with_provenance(prompt: str) -> str:
        """C5-compliant judge: provenance present → REPRO=4 (provenance apparatus sufficient)."""
        return _make_reviewer_response(sound=4, repro=4)

    _, tree_root, project_root = _make_ms_tree(tmp_path)
    _write_bib(tree_root)

    # Paper with full provenance apparatus
    paper_with_provenance = (
        "We evaluate three models on HFS. Seeds 0-4, 95% CI reported. "
        "Reproducibility. Code, prompts, and per-seed outputs are released; "
        "all runs use seeds 0-4 with the configuration in Appendix B. "
        "Data drawn from the publicly licensed reference set [4]."
    )

    result = run_review_board(
        pdf_text=paper_with_provenance,
        tree_root=tree_root,
        N=1,
        K=1,
        floor_dims=["SOUND", "REPRO"],
        floor_value=3,
        judge_fn=_c5_judge_with_provenance,
        judge_model="mock-model",
        notes_root=project_root,
    )

    assert result["cleared"] is True, (
        "Paper with provenance apparatus must be able to clear the REPRO floor. "
        "C5 allows high REPRO scores when provenance is present."
    )

    rounds = result["rounds"]
    rv_scores = rounds[0]["reviewers"][0]["scores"]
    assert rv_scores.get("REPRO", 0) >= 3, (
        f"With provenance apparatus, REPRO must be ≥ floor 3; got {rv_scores.get('REPRO', 0)}"
    )


# ---------------------------------------------------------------------------
# Test 14: L377 partial-omit guard — judge emits SOUND only, omits REPRO → not cleared
# ---------------------------------------------------------------------------

def test_l377_partial_omit_floor_dim_fails_closed(tmp_path):
    """L377 guard: partial judge emits [SOUND:4] only, omits [REPRO] → REPRO defaults to 0 → not cleared.

    This guards the PARTIAL parse branch (L377 in run_reviewer_node):
        scores = {dim: extracted.get(dim, 0) for dim in _ALL_DIMS}

    MUTATION GUARD (must be red-before-green verified):
      Mutate: extracted.get(dim, 0) → extracted.get(dim, 5) (fail-OPEN)
        → REPRO=5 ≥ floor=3 → cleared=True → this test goes RED ✓

    Distinct from test_29 (full-parse-failure): test_29 tests the L374 branch
    (None → all zeros). This tests the L377 branch (partial parse → missing dim → 0).
    """
    from research_vault.manuscript.review_board import run_review_board

    def _partial_judge(prompt: str) -> str:
        """Returns only [SOUND:4]; deliberately omits [REPRO] (and all other dims)."""
        return (
            "[SOUND:4]\n"
            "WEAKNESS: None found on this dimension.\n"
            "JUSTIFY: \"The experimental design follows established protocols\"\n"
            "CONF: high\n"
        )

    _, tree_root, project_root = _make_ms_tree(tmp_path)
    _write_bib(tree_root)

    result = run_review_board(
        pdf_text="Test paper.",
        tree_root=tree_root,
        N=1,
        K=1,
        floor_dims=["SOUND", "REPRO"],
        floor_value=3,
        judge_fn=_partial_judge,
        judge_model="mock-model",
        notes_root=project_root,
    )

    # Gate 1: not cleared (REPRO=0 < floor=3)
    assert result["cleared"] is False, (
        "Partial judge (REPRO omitted) must fail-close (REPRO defaults to 0 < floor=3). "
        "If this test passes after mutating .get(dim, 0) → .get(dim, 5): mutation is working — "
        "restore to 0."
    )
    assert result["cleared_at"] is None

    # Gate 2: verify the score values — SOUND parsed correctly, REPRO defaults to 0
    rounds = result["rounds"]
    r1 = rounds[0]
    rv_scores = r1["reviewers"][0]["scores"]

    assert rv_scores.get("SOUND") == 4, (
        f"SOUND:4 must be parsed from partial response; got {rv_scores.get('SOUND')}"
    )
    assert rv_scores.get("REPRO") == 0, (
        f"REPRO omitted → must default to 0 (fail-closed L377 branch); got {rv_scores.get('REPRO')}"
    )

    # Gate 3: NOT-CLEARED payload present (fail-closed, not silent)
    assert isinstance(result.get("not_cleared"), dict), (
        "NOT-CLEARED must produce first-class payload when partial judge omits floor dim"
    )
    # The failing dim must be REPRO
    nc = result["not_cleared"]
    failing = nc.get("failing_dims", [])
    assert any("REPRO" in str(f) for f in failing), (
        f"REPRO must be identified as failing dim; failing_dims={failing!r}"
    )


# ---------------------------------------------------------------------------
# Test 15: get_reviewer_lens_spec — K=3 → L1/L2/L3
# ---------------------------------------------------------------------------

def test_get_reviewer_lens_spec_k3():
    """K=3: k=1→L1 (methods), k=2→L2 (significance), k=3→L3 (clarity/repro)."""
    from research_vault.manuscript.review_board import get_reviewer_lens_spec

    l1 = get_reviewer_lens_spec(k=1, K=3)
    l2 = get_reviewer_lens_spec(k=2, K=3)
    l3 = get_reviewer_lens_spec(k=3, K=3)

    # L1: methods/soundness emphasis
    l1_lower = l1.lower()
    assert "soundness" in l1_lower or "method" in l1_lower or "methodolog" in l1_lower, (
        f"L1 must emphasise methods/soundness; got: {l1[:100]!r}"
    )

    # L2: significance/novelty emphasis
    l2_lower = l2.lower()
    assert "novelty" in l2_lower or "significance" in l2_lower or "contrib" in l2_lower, (
        f"L2 must emphasise significance/novelty; got: {l2[:100]!r}"
    )

    # L3: clarity/repro/limitations emphasis
    l3_lower = l3.lower()
    assert "repro" in l3_lower or "reproducib" in l3_lower or "clarity" in l3_lower, (
        f"L3 must emphasise clarity/repro/limitations; got: {l3[:100]!r}"
    )

    # All three must be distinct
    assert l1 != l2, "L1 and L2 must be distinct lens specs"
    assert l2 != l3, "L2 and L3 must be distinct lens specs"
    assert l1 != l3, "L1 and L3 must be distinct lens specs"


# ---------------------------------------------------------------------------
# Test 16: get_reviewer_lens_spec — K=2 → L1+L3 (floor-carrying pair)
# ---------------------------------------------------------------------------

def test_get_reviewer_lens_spec_k2_floor_pair():
    """K=2 fallback → L1 for k=1, L3 for k=2 (the two floor-carrying lenses)."""
    from research_vault.manuscript.review_board import get_reviewer_lens_spec

    k2_l1 = get_reviewer_lens_spec(k=1, K=2)
    k2_l2 = get_reviewer_lens_spec(k=2, K=2)

    k3_l1 = get_reviewer_lens_spec(k=1, K=3)
    k3_l3 = get_reviewer_lens_spec(k=3, K=3)

    # K=2: k=1 should be the same as K=3 k=1 (L1)
    assert k2_l1 == k3_l1, "K=2 k=1 must be L1 (same as K=3 k=1)"
    # K=2: k=2 should be the same as K=3 k=3 (L3, not L2)
    assert k2_l2 == k3_l3, "K=2 k=2 must be L3 (floor-carrying pair — skip L2)"


# ---------------------------------------------------------------------------
# Test 17: get_reviewer_lens_spec — K=1 → L1
# ---------------------------------------------------------------------------

def test_get_reviewer_lens_spec_k1():
    """K=1: only one reviewer → always L1 (methods/soundness)."""
    from research_vault.manuscript.review_board import get_reviewer_lens_spec

    k1_spec = get_reviewer_lens_spec(k=1, K=1)
    k3_l1 = get_reviewer_lens_spec(k=1, K=3)

    assert k1_spec == k3_l1, "K=1 must use L1 (same as K=3 k=1)"


# ---------------------------------------------------------------------------
# Test 18: Lens specs in manifest node spec (integration)
# ---------------------------------------------------------------------------

def test_lens_specs_in_manifest_node_spec(tmp_path):
    """reviewer-1-L1 node spec contains L1 lens posture; reviewer-1-L3 contains L3 posture."""
    from research_vault.manuscript import cmd_new
    import research_vault.config as _rvc
    from research_vault.manuscript.review_board import get_reviewer_lens_spec

    instance_root = tmp_path / "instance"
    instance_root.mkdir()
    proj_notes = instance_root / "projects" / "myproj"
    proj_notes.mkdir(parents=True)

    cfg_path = instance_root / "research_vault.toml"
    cfg_path.write_text(
        "[projects]\n"
        "myproj = \"projects/myproj\"\n"
        "\n"
        "[manuscript_review]\n"
        "max_rounds = 1\n"
        "reviewers_per_round = 3\n",
        encoding="utf-8",
    )

    old_cache = _rvc._CACHE
    old_env = os.environ.get("RESEARCH_VAULT_CONFIG")
    try:
        os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_path)
        _rvc._CACHE = None
        from research_vault.config import load_config
        cfg = load_config()
        _, tree_root, manifest = cmd_new(
            "myproj", "ms-lens-test",
            thesis="Test thesis for lens spec check.",
            scope=[],
            config=cfg,
        )
    finally:
        _rvc._CACHE = old_cache
        if old_env is None:
            os.environ.pop("RESEARCH_VAULT_CONFIG", None)
        else:
            os.environ["RESEARCH_VAULT_CONFIG"] = old_env

    nodes_by_id = {n["id"]: n for n in manifest["nodes"]}

    # L1 posture must appear in reviewer-1-L1 spec
    l1_spec_expected = get_reviewer_lens_spec(k=1, K=3)
    l1_node = nodes_by_id.get("reviewer-1-L1")
    assert l1_node is not None, "reviewer-1-L1 must exist in manifest"
    assert l1_spec_expected[:50] in l1_node["spec"], (
        "reviewer-1-L1 spec must contain the L1 lens posture (methods/soundness)"
    )

    # L3 posture must appear in reviewer-1-L3 spec
    l3_spec_expected = get_reviewer_lens_spec(k=3, K=3)
    l3_node = nodes_by_id.get("reviewer-1-L3")
    assert l3_node is not None, "reviewer-1-L3 must exist in manifest"
    assert l3_spec_expected[:50] in l3_node["spec"], (
        "reviewer-1-L3 spec must contain the L3 lens posture (clarity/repro/limitations)"
    )


# ---------------------------------------------------------------------------
# Test 19: Lens spec postures are prepended (not replacing) the rubric instruction
# ---------------------------------------------------------------------------

def test_lens_posture_prepended_not_replacing(tmp_path):
    """Lens posture is prepended to reviewer spec; the bracket-token instruction is also present."""
    from research_vault.manuscript import cmd_new
    import research_vault.config as _rvc

    instance_root = tmp_path / "instance"
    instance_root.mkdir()
    proj_notes = instance_root / "projects" / "myproj"
    proj_notes.mkdir(parents=True)

    cfg_path = instance_root / "research_vault.toml"
    cfg_path.write_text(
        "[projects]\n"
        "myproj = \"projects/myproj\"\n"
        "\n"
        "[manuscript_review]\n"
        "max_rounds = 1\n"
        "reviewers_per_round = 2\n",
        encoding="utf-8",
    )

    old_cache = _rvc._CACHE
    old_env = os.environ.get("RESEARCH_VAULT_CONFIG")
    try:
        os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_path)
        _rvc._CACHE = None
        from research_vault.config import load_config
        cfg = load_config()
        _, _, manifest = cmd_new(
            "myproj", "ms-prepend-test",
            thesis="Test.",
            scope=[],
            config=cfg,
        )
    finally:
        _rvc._CACHE = old_cache
        if old_env is None:
            os.environ.pop("RESEARCH_VAULT_CONFIG", None)
        else:
            os.environ["RESEARCH_VAULT_CONFIG"] = old_env

    nodes_by_id = {n["id"]: n for n in manifest["nodes"]}
    l1_spec = nodes_by_id["reviewer-1-L1"]["spec"]

    # Both lens posture AND the rubric instruction must be present
    # The score instruction mentions "[SOUND:N]" or "bracket" or "rubric"
    assert "SOUND" in l1_spec or "rubric" in l1_spec.lower() or "bracket" in l1_spec.lower(), (
        "Lens spec must retain the bracket-token instruction, not just the posture"
    )


# ---------------------------------------------------------------------------
# Test 20: run_canary_scaffold skips when rubric is empty (backward compat)
# ---------------------------------------------------------------------------

def test_canary_skips_when_rubric_empty():
    """run_canary_scaffold with rubric='' → skip (backward compat with -a tests)."""
    from research_vault.manuscript.review_board import run_canary_scaffold

    # Always-scoring judge (would fail canary if it fires)
    def _always_4(prompt: str) -> str:
        return _make_reviewer_response(sound=4, repro=4)

    # Should NOT raise — empty rubric means skip
    result = run_canary_scaffold(_always_4, rubric="")
    assert result["canary_ok"] is True, "Empty rubric → canary must be skipped (backward compat)"


# ---------------------------------------------------------------------------
# Test 21: Canary abort propagates via run_meta_review
# ---------------------------------------------------------------------------

def test_canary_abort_propagates_in_meta_review(tmp_path):
    """When run_canary_scaffold raises CanaryAbortError, run_meta_review surfaces it."""
    from research_vault.manuscript.review_board import (
        run_meta_review, CanaryAbortError, DEFAULT_REVIEW_RUBRIC,
    )

    # A rubber-stamp judge that will fail the canary (weak probe returns high scores)
    reviewer_results = [
        {
            "round": 1, "lens": 1, "node_id": "reviewer-1-L1",
            "scores": {"SOUND": 4, "CONTRIB": 3, "CLARITY": 4, "ORIG": 3, "LIMIT": 3, "REPRO": 4, "ETHICS": 4},
            "raw_response": _make_reviewer_response(),
            "judge_model": "mock",
            "prompt_hash": "abc",
            "skipped": False,
        }
    ]

    with pytest.raises(CanaryAbortError):
        run_meta_review(
            round_num=1,
            reviewer_results=reviewer_results,
            floor_dims=["SOUND", "REPRO"],
            floor_value=3,
            canary_judge_fn=_rubber_stamp_judge,  # will fail canary
            canary_rubric=DEFAULT_REVIEW_RUBRIC,
            judge_model="mock",
        )


# ---------------------------------------------------------------------------
# Test 22: DEFAULT_REVIEW_RUBRIC is the seam default from get_review_rubric(None, None)
# ---------------------------------------------------------------------------

def test_default_review_rubric_is_seam_default():
    """get_review_rubric(None, None) returns DEFAULT_REVIEW_RUBRIC (Ada's rubric is now the default)."""
    from research_vault.manuscript.review_board import (
        get_review_rubric, DEFAULT_REVIEW_RUBRIC,
    )

    result = get_review_rubric(override=None, config=None)
    assert result == DEFAULT_REVIEW_RUBRIC, (
        "get_review_rubric with no override/config must return DEFAULT_REVIEW_RUBRIC. "
        "In -b, the default is Ada's real rubric, not the placeholder."
    )
    # And confirm it's NOT the old placeholder
    assert "PLACEHOLDER" not in result.upper(), (
        "DEFAULT_REVIEW_RUBRIC must not be the placeholder in -b"
    )
