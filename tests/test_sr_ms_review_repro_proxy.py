"""test_sr_ms_review_repro_proxy.py — SR-MS-GATE-ALIGN Slice B: study-type-aware REPRO.

Covers:
  P1.  _REPRO_PROXY_CLAUSE is exported and contains key phrases
  P2.  _REPRO_PROXY_CLAUSE says run-provenance absence does NOT cap score
  P3.  _REVIEWER_LENS_L3_PROXY is exported and attacks analysis-provenance
  P4.  _REVIEWER_LENS_L3_PROXY is distinct from _REVIEWER_LENS_L3
  P5.  get_reviewer_lens_spec is_proxy_study=True returns proxy L3 for L3 position (K=3 k=3)
  P6.  get_reviewer_lens_spec is_proxy_study=True returns proxy L3 for L3 position (K=2 k=2)
  P7.  get_reviewer_lens_spec is_proxy_study=False leaves L3 unchanged
  P8.  get_reviewer_lens_spec is_proxy_study=True does NOT change L1 or L2 positions
  P9.  run_reviewer_node is_proxy_study=True appends _REPRO_PROXY_CLAUSE to prompt
  P10. run_reviewer_node is_proxy_study=False does NOT append proxy clause (strict no-op)
  P11. run_reviewer_node lens_spec kwarg is prepended to prompt when provided
  P12. ACCEPTANCE — proxy paper: all-empty results_location → REPRO ≥ floor(3) (not capped at 2)
  P13. ACCEPTANCE — real-run paper: is_proxy_study=False → REPRO capped at 2 on absent seeds (unchanged)
  P14. ACCEPTANCE — run_review_board(is_proxy_study=None) self-determines from notes_root (proxy)
  P15. ACCEPTANCE — run_review_board(is_proxy_study=None) self-determines from notes_root (real run)
  P16. run_review_board explicit is_proxy_study=True wins over auto-determination
  P17. run_review_board is_proxy_study=None with no notes_root defaults to False (safe)
  P18. _proxy_study_reframe_tex enriched: has analysis-provenance positive sentence
  P19. _proxy_study_reframe_tex enriched: renders dataset_id when populated in notes
  P20. _proxy_study_reframe_tex enriched: renders config_location code pointer when populated
  P21. _proxy_study_reframe_tex enriched: graceful fallback when all notes have sentinel fields
  P22. Canary tests are unchanged — _CANARY_STRONG_PASSAGE / _CANARY_WEAK_PASSAGE unmodified
  P23. run_review_board (is_proxy_study=True): meta records is_proxy_study flag

All hermetic (tmp_path). No live LLM calls. Stdlib only.
sr: SR-MS-GATE-ALIGN Slice B
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# Helpers
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
    return (
        f"[SOUND:{sound}]\n"
        f"WEAKNESS: minor\nJUSTIFY: \"The evaluation...\"\nCONF: high\n\n"
        f"[CONTRIB:{contrib}]\n"
        f"WEAKNESS: incremental\nJUSTIFY: \"Building on [4]\"\nCONF: med\n\n"
        f"[CLARITY:{clarity}]\n"
        f"WEAKNESS: dense notation\nJUSTIFY: \"h = f(g(x))\"\nCONF: high\n\n"
        f"[ORIG:{orig}]\n"
        f"WEAKNESS: prior art\nJUSTIFY: \"similar to [2]\"\nCONF: med\n\n"
        f"[LIMIT:{limit}]\n"
        f"WEAKNESS: no failure modes\nJUSTIFY: \"We do not discuss\"\nCONF: high\n\n"
        f"[REPRO:{repro}]\n"
        f"WEAKNESS: seeds absent\nJUSTIFY: \"Results averaged\"\nCONF: high\n\n"
        f"[ETHICS:{ethics}]\n"
        f"WEAKNESS: none stated\nJUSTIFY: \"We do not address\"\nCONF: med\n\n"
        f"CHECKLIST (responsible-AI, Yes/No/NA):\n"
        f"  DATA_LICENSING: NA\n  FORESEEABLE_HARM: No\n  DUAL_USE: NA\n\n"
        f"SUMMARY:\n"
        f"  FLOOR_DIMS: SOUND={sound} REPRO={repro}\n"
        f"  WORST_OBJECTION: Central claim lacks evidence.\n"
        f"  SWEPT: Adversarial sweep complete.\n"
    )


def _make_experiment_note(
    notes_root: Path,
    note_id: str,
    fields: dict[str, str],
) -> Path:
    """Write an experiment note under notes_root/experiments/."""
    exp_dir = notes_root / "experiments"
    exp_dir.mkdir(parents=True, exist_ok=True)
    note_path = exp_dir / f"{note_id}.md"
    front = "---\ntype: experiment\n"
    for k, v in fields.items():
        front += f"{k}: {v}\n"
    front += "---\n"
    note_path.write_text(front, encoding="utf-8")
    return note_path


def _make_ms_tree(tmp_path: Path, ms_id: str = "ms-proxy") -> tuple[Path, Path]:
    """Minimal manuscript tree."""
    tree_root = tmp_path / "manuscripts" / ms_id
    (tree_root / "sections").mkdir(parents=True, exist_ok=True)
    (tree_root / "main.tex").write_text(
        "\\documentclass{article}\n\\begin{document}\nTest.\n\\end{document}\n",
        encoding="utf-8",
    )
    return tree_root, tmp_path


# ---------------------------------------------------------------------------
# Prompt-recording judge (for testing what the reviewer received)
# ---------------------------------------------------------------------------

class RecordingJudge:
    """Judge that records every prompt it receives, returns a configurable response."""

    def __init__(self, response_fn=None):
        self.received_prompts: list[str] = []
        self._response_fn = response_fn or (lambda p: _make_reviewer_response(sound=4, repro=4))

    def __call__(self, prompt: str) -> str:
        self.received_prompts.append(prompt)
        return self._response_fn(prompt)


# ---------------------------------------------------------------------------
# P1: _REPRO_PROXY_CLAUSE is exported and contains key phrases
# ---------------------------------------------------------------------------

def test_repro_proxy_clause_exported_and_has_key_phrases():
    """_REPRO_PROXY_CLAUSE is exported and contains analysis-provenance binding phrases."""
    from research_vault.manuscript.review_board import _REPRO_PROXY_CLAUSE  # type: ignore[attr-defined]

    assert isinstance(_REPRO_PROXY_CLAUSE, str) and len(_REPRO_PROXY_CLAUSE) > 100, (
        "_REPRO_PROXY_CLAUSE must be a non-trivial string"
    )
    low = _REPRO_PROXY_CLAUSE.lower()
    assert "analysis" in low and "provenance" in low, (
        "_REPRO_PROXY_CLAUSE must mention analysis-provenance"
    )
    assert "re-analysis" in low or "proxy" in low or "no new" in low, (
        "_REPRO_PROXY_CLAUSE must mention re-analysis / proxy study context"
    )


# ---------------------------------------------------------------------------
# P2: _REPRO_PROXY_CLAUSE explicitly states run-provenance absence does NOT cap
# ---------------------------------------------------------------------------

def test_repro_proxy_clause_run_provenance_na():
    """_REPRO_PROXY_CLAUSE says run-provenance (seeds/configs/run-ids) is N/A and absence does NOT cap."""
    from research_vault.manuscript.review_board import _REPRO_PROXY_CLAUSE  # type: ignore[attr-defined]

    low = _REPRO_PROXY_CLAUSE.lower()
    # Must say something about run-provenance being N/A or not applicable
    assert (
        "n/a" in low or "not applicable" in low or "does not cap" in low or "does not cap" in low
        or "absence" in low
    ), "_REPRO_PROXY_CLAUSE must say run-provenance is N/A / absence does not cap"

    # Must explicitly mention seeds or configs or run-ids as N/A for proxy
    assert "seed" in low or "config" in low or "run-id" in low or "run_id" in low, (
        "_REPRO_PROXY_CLAUSE must reference seeds/configs/run-ids as the N/A provenance"
    )


# ---------------------------------------------------------------------------
# P3: _REVIEWER_LENS_L3_PROXY is exported and attacks analysis-provenance
# ---------------------------------------------------------------------------

def test_reviewer_lens_l3_proxy_exported_and_attacks_analysis():
    """_REVIEWER_LENS_L3_PROXY is exported and attacks analysis-provenance."""
    from research_vault.manuscript.review_board import _REVIEWER_LENS_L3_PROXY  # type: ignore[attr-defined]

    assert isinstance(_REVIEWER_LENS_L3_PROXY, str) and len(_REVIEWER_LENS_L3_PROXY) > 50, (
        "_REVIEWER_LENS_L3_PROXY must be a non-trivial string"
    )
    low = _REVIEWER_LENS_L3_PROXY.lower()
    assert "analysis" in low, "_REVIEWER_LENS_L3_PROXY must reference analysis-provenance"
    # Must mention the things it attacks
    assert "re-analysis" in low or "proxy" in low or "no new" in low or "aggregat" in low, (
        "_REVIEWER_LENS_L3_PROXY must identify the study type"
    )


# ---------------------------------------------------------------------------
# P4: _REVIEWER_LENS_L3_PROXY is distinct from _REVIEWER_LENS_L3
# ---------------------------------------------------------------------------

def test_reviewer_lens_l3_proxy_distinct_from_l3():
    """Proxy L3 lens is distinct from standard L3 lens."""
    from research_vault.manuscript.review_board import (
        _REVIEWER_LENS_L3_PROXY,  # type: ignore[attr-defined]
        _REVIEWER_LENS_L3,
    )

    assert _REVIEWER_LENS_L3_PROXY != _REVIEWER_LENS_L3, (
        "Proxy L3 lens must be distinct from the standard L3 lens"
    )
    # Proxy L3 should NOT say 'cap at 2' or 'seeds or number of runs' (that's standard L3)
    assert "cap" not in _REVIEWER_LENS_L3_PROXY.lower() or "does not cap" in _REVIEWER_LENS_L3_PROXY.lower(), (
        "Proxy L3 must NOT instruct judge to cap at 2 on absent run-provenance"
    )


# ---------------------------------------------------------------------------
# P5: get_reviewer_lens_spec is_proxy_study=True returns proxy L3 for K=3 k=3
# ---------------------------------------------------------------------------

def test_get_reviewer_lens_spec_proxy_k3_l3():
    """K=3 k=3 with is_proxy_study=True → proxy L3 lens."""
    from research_vault.manuscript.review_board import (
        get_reviewer_lens_spec,
        _REVIEWER_LENS_L3_PROXY,  # type: ignore[attr-defined]
        _REVIEWER_LENS_L3,
    )

    proxy_spec = get_reviewer_lens_spec(k=3, K=3, is_proxy_study=True)
    std_spec = get_reviewer_lens_spec(k=3, K=3, is_proxy_study=False)

    assert proxy_spec == _REVIEWER_LENS_L3_PROXY, (
        "K=3 k=3 with is_proxy_study=True must return _REVIEWER_LENS_L3_PROXY"
    )
    assert std_spec == _REVIEWER_LENS_L3, (
        "K=3 k=3 with is_proxy_study=False must still return standard _REVIEWER_LENS_L3"
    )


# ---------------------------------------------------------------------------
# P6: get_reviewer_lens_spec is_proxy_study=True returns proxy L3 for K=2 k=2
# ---------------------------------------------------------------------------

def test_get_reviewer_lens_spec_proxy_k2_l3():
    """K=2 k=2 with is_proxy_study=True → proxy L3 lens (floor-carrying pair)."""
    from research_vault.manuscript.review_board import (
        get_reviewer_lens_spec,
        _REVIEWER_LENS_L3_PROXY,  # type: ignore[attr-defined]
    )

    proxy_spec = get_reviewer_lens_spec(k=2, K=2, is_proxy_study=True)
    assert proxy_spec == _REVIEWER_LENS_L3_PROXY, (
        "K=2 k=2 with is_proxy_study=True must return _REVIEWER_LENS_L3_PROXY"
    )


# ---------------------------------------------------------------------------
# P7: get_reviewer_lens_spec is_proxy_study=False leaves L3 unchanged (strict no-op)
# ---------------------------------------------------------------------------

def test_get_reviewer_lens_spec_non_proxy_l3_unchanged():
    """is_proxy_study=False: all lens specs unchanged (strict no-op on non-proxy path)."""
    from research_vault.manuscript.review_board import (
        get_reviewer_lens_spec,
        _REVIEWER_LENS_L1,
        _REVIEWER_LENS_L2,
        _REVIEWER_LENS_L3,
    )

    assert get_reviewer_lens_spec(k=1, K=3, is_proxy_study=False) == _REVIEWER_LENS_L1
    assert get_reviewer_lens_spec(k=2, K=3, is_proxy_study=False) == _REVIEWER_LENS_L2
    assert get_reviewer_lens_spec(k=3, K=3, is_proxy_study=False) == _REVIEWER_LENS_L3


# ---------------------------------------------------------------------------
# P8: is_proxy_study=True does NOT change L1 or L2 positions
# ---------------------------------------------------------------------------

def test_proxy_study_does_not_change_l1_l2():
    """Proxy flag only affects L3 position — L1 and L2 are unchanged."""
    from research_vault.manuscript.review_board import (
        get_reviewer_lens_spec,
        _REVIEWER_LENS_L1,
        _REVIEWER_LENS_L2,
    )

    assert get_reviewer_lens_spec(k=1, K=3, is_proxy_study=True) == _REVIEWER_LENS_L1, (
        "L1 must NOT change for proxy studies"
    )
    assert get_reviewer_lens_spec(k=2, K=3, is_proxy_study=True) == _REVIEWER_LENS_L2, (
        "L2 must NOT change for proxy studies"
    )


# ---------------------------------------------------------------------------
# P9: run_reviewer_node is_proxy_study=True appends _REPRO_PROXY_CLAUSE to prompt
# ---------------------------------------------------------------------------

def test_run_reviewer_node_proxy_clause_in_prompt(tmp_path):
    """run_reviewer_node with is_proxy_study=True → _REPRO_PROXY_CLAUSE text appears in prompt."""
    from research_vault.manuscript.review_board import (
        run_reviewer_node,
        _REPRO_PROXY_CLAUSE,  # type: ignore[attr-defined]
    )

    judge = RecordingJudge()

    tree_root, _ = _make_ms_tree(tmp_path)
    run_reviewer_node(
        pdf_text="Test proxy paper.",
        round_num=1,
        lens_num=1,
        judge_fn=judge,
        judge_model="mock",
        is_proxy_study=True,
    )

    assert judge.received_prompts, "Judge must have been called"
    prompt = judge.received_prompts[0]

    # The proxy clause (or a distinctive substring of it) must appear in the prompt
    # Use a known unique phrase from the clause
    assert (
        "analysis-provenance" in prompt.lower()
        or "re-analysis" in prompt.lower()
        or "analysis provenance" in prompt.lower()
        or _REPRO_PROXY_CLAUSE[:40].lower() in prompt.lower()
    ), (
        "Proxy clause must appear in prompt when is_proxy_study=True. "
        f"Prompt start: {prompt[:200]!r}"
    )


# ---------------------------------------------------------------------------
# P10: run_reviewer_node is_proxy_study=False does NOT append proxy clause
# ---------------------------------------------------------------------------

def test_run_reviewer_node_no_proxy_clause_when_false(tmp_path):
    """run_reviewer_node with is_proxy_study=False → NO proxy clause in prompt (strict no-op)."""
    from research_vault.manuscript.review_board import (
        run_reviewer_node,
        _REPRO_PROXY_CLAUSE,  # type: ignore[attr-defined]
    )

    judge = RecordingJudge()
    tree_root, _ = _make_ms_tree(tmp_path)

    run_reviewer_node(
        pdf_text="Test real-run paper.",
        round_num=1,
        lens_num=1,
        judge_fn=judge,
        judge_model="mock",
        is_proxy_study=False,
    )

    assert judge.received_prompts, "Judge must have been called"
    prompt = judge.received_prompts[0]

    # The proxy clause must NOT appear in the standard prompt
    # Check for a phrase unique to the proxy clause
    assert (
        "run-provenance" not in prompt.lower()
        and "analysis-code" not in prompt.lower()
        and "re-analysis of published aggregate" not in prompt.lower()
    ), (
        "Proxy clause must NOT appear in prompt when is_proxy_study=False. "
        f"Prompt snippet: {prompt[:300]!r}"
    )


# ---------------------------------------------------------------------------
# P11: run_reviewer_node lens_spec kwarg is prepended to prompt
# ---------------------------------------------------------------------------

def test_run_reviewer_node_lens_spec_prepended(tmp_path):
    """lens_spec kwarg to run_reviewer_node is prepended to the judge prompt."""
    from research_vault.manuscript.review_board import run_reviewer_node

    judge = RecordingJudge()
    custom_lens = "UNIQUE-LENS-SPEC-MARKER-FOR-TESTING-XYZ"

    tree_root, _ = _make_ms_tree(tmp_path)
    run_reviewer_node(
        pdf_text="Test paper.",
        round_num=1,
        lens_num=1,
        judge_fn=judge,
        judge_model="mock",
        lens_spec=custom_lens,
    )

    assert judge.received_prompts, "Judge must have been called"
    prompt = judge.received_prompts[0]
    assert custom_lens in prompt, (
        "lens_spec must appear in the judge prompt. "
        f"Prompt start: {prompt[:200]!r}"
    )
    # It must be PREPENDED (appear before the rubric body)
    idx = prompt.find(custom_lens)
    assert idx < 200, (
        f"lens_spec should be prepended (near start); found at index {idx}"
    )


# ---------------------------------------------------------------------------
# P12: ACCEPTANCE — proxy paper → REPRO ≥ floor(3) (not capped at 2)
# ---------------------------------------------------------------------------

def test_acceptance_proxy_paper_repro_not_capped(tmp_path):
    """ACCEPTANCE: proxy study with analysis-provenance → REPRO ≥ floor(3), not capped at 2.

    Non-vacuous: if is_proxy_study=True does nothing (no clause injected), a judge
    that only reads the standard rubric (C5: absent run-provenance → cap at 2) will
    still score REPRO=2 → not cleared → this test RED.

    The judge here simulates a C5-compliant proxy-aware judge: when it sees the
    proxy clause (analysis-provenance binding), it scores REPRO=4 instead of capping
    at 2 for absent seeds.
    """
    from research_vault.manuscript.review_board import run_review_board, _REPRO_PROXY_CLAUSE  # type: ignore[attr-defined]

    # Proxy paper: has analysis methods described in-text, cited sources, but no seeds/runs
    proxy_paper_text = (
        "This work is a re-analysis of published benchmark results. "
        "We apply a weighted aggregation formula (Eq. 1: w_i = n_i / sum(n_j)) to "
        "the accuracy scores reported in Table 2 of Smith et al. (2023) and Lee et al. (2022). "
        "Reproducibility: The analysis formula and aggregation script are described in "
        "Section 3. The source results are drawn from publicly available benchmark tables "
        "cited as [1] and [2]. No new model calls or GPU runs were executed."
    )

    def _proxy_aware_judge(prompt: str) -> str:
        """Judge that scores REPRO=4 when it sees the proxy clause, REPRO=1 otherwise."""
        if (
            "analysis-provenance" in prompt.lower()
            or "re-analysis of published" in prompt.lower()
            or "analysis-code" in prompt.lower()
        ):
            # Proxy clause present → score on analysis-provenance (paper has it)
            return _make_reviewer_response(sound=4, repro=4)
        else:
            # Standard rubric, no seeds → cap at 2 (C5 compliant)
            return _make_reviewer_response(sound=4, repro=2)

    tree_root, notes_root = _make_ms_tree(tmp_path)

    result = run_review_board(
        pdf_text=proxy_paper_text,
        tree_root=tree_root,
        N=1,
        K=1,
        floor_dims=["SOUND", "REPRO"],
        floor_value=3,
        judge_fn=_proxy_aware_judge,
        judge_model="mock",
        notes_root=notes_root,
        is_proxy_study=True,  # explicit proxy flag
    )

    r1_scores = result["rounds"][0]["reviewers"][0]["scores"]
    assert r1_scores.get("REPRO", 0) >= 3, (
        f"ACCEPTANCE FAIL — proxy study REPRO was capped at {r1_scores.get('REPRO', 0)}; "
        f"expected ≥ 3 (analysis-provenance binding). "
        f"Likely cause: _REPRO_PROXY_CLAUSE not injected into prompt."
    )
    assert result["cleared"] is True, (
        f"Proxy study with analysis-provenance should clear: {result['honest_report']}"
    )


# ---------------------------------------------------------------------------
# P13: ACCEPTANCE — real-run paper: is_proxy_study=False → REPRO capped at 2 (unchanged)
# ---------------------------------------------------------------------------

def test_acceptance_real_run_repro_binding_unchanged(tmp_path):
    """ACCEPTANCE: real-run paper (is_proxy_study=False) → REPRO capped at 2 on absent seeds.

    This is the strict no-op test on the non-proxy path. The standard C5 binding
    (run-provenance absence → cap at 2) must be fully preserved.
    """
    from research_vault.manuscript.review_board import run_review_board

    def _c5_judge_no_seeds(prompt: str) -> str:
        """Standard judge: no seeds in paper → REPRO=1 (C5 cap)."""
        return _make_reviewer_response(sound=4, repro=1)

    tree_root, notes_root = _make_ms_tree(tmp_path)

    result = run_review_board(
        pdf_text="Our method outperforms baselines. No reproducibility statement.",
        tree_root=tree_root,
        N=1,
        K=1,
        floor_dims=["SOUND", "REPRO"],
        floor_value=3,
        judge_fn=_c5_judge_no_seeds,
        judge_model="mock",
        notes_root=notes_root,
        is_proxy_study=False,  # explicit non-proxy
    )

    r1_scores = result["rounds"][0]["reviewers"][0]["scores"]
    assert r1_scores.get("REPRO", 0) <= 2, (
        f"Real-run paper with no seeds must have REPRO ≤ 2 (C5 cap unchanged); "
        f"got REPRO={r1_scores.get('REPRO', 0)}"
    )
    assert result["cleared"] is False, (
        "Real-run paper with absent run-provenance must NOT clear (C5 binding intact)"
    )


# ---------------------------------------------------------------------------
# P14: ACCEPTANCE — self-determination: proxy notes → auto-detects proxy
# ---------------------------------------------------------------------------

def test_acceptance_self_determine_proxy(tmp_path):
    """run_review_board(is_proxy_study=None) self-determines proxy when notes have empty results_location."""
    from research_vault.manuscript.review_board import run_review_board

    # Create notes_root with proxy experiment notes (all-empty results_location)
    notes_root = tmp_path / "notes_root"
    _make_experiment_note(notes_root, "exp-a", {"results_location": ""})
    _make_experiment_note(notes_root, "exp-b", {"results_location": ""})

    judge = RecordingJudge()
    tree_root, _ = _make_ms_tree(tmp_path)

    run_review_board(
        pdf_text="Proxy paper.",
        tree_root=tree_root,
        N=1,
        K=1,
        floor_dims=["SOUND", "REPRO"],
        floor_value=3,
        judge_fn=judge,
        judge_model="mock",
        notes_root=notes_root,
        is_proxy_study=None,  # ← self-determine
    )

    assert judge.received_prompts, "Judge must have been called"
    prompt = judge.received_prompts[0]
    # The proxy clause must have been injected (self-determined as proxy)
    assert (
        "analysis-provenance" in prompt.lower()
        or "re-analysis" in prompt.lower()
        or "analysis provenance" in prompt.lower()
    ), (
        "Self-determination from proxy notes must inject the proxy clause. "
        f"Prompt snippet: {prompt[:300]!r}"
    )


# ---------------------------------------------------------------------------
# P15: ACCEPTANCE — self-determination: real-run notes → auto-detects NOT proxy
# ---------------------------------------------------------------------------

def test_acceptance_self_determine_not_proxy(tmp_path):
    """run_review_board(is_proxy_study=None) self-determines NOT proxy when notes have results_location."""
    from research_vault.manuscript.review_board import run_review_board

    # Create notes_root with a real-run experiment note
    notes_root = tmp_path / "notes_root"
    _make_experiment_note(notes_root, "exp-real", {"results_location": "/data/results.csv"})

    judge = RecordingJudge()
    tree_root, _ = _make_ms_tree(tmp_path)

    run_review_board(
        pdf_text="Real-run paper.",
        tree_root=tree_root,
        N=1,
        K=1,
        floor_dims=["SOUND", "REPRO"],
        floor_value=3,
        judge_fn=judge,
        judge_model="mock",
        notes_root=notes_root,
        is_proxy_study=None,  # ← self-determine
    )

    assert judge.received_prompts, "Judge must have been called"
    prompt = judge.received_prompts[0]
    # The proxy clause must NOT be injected (self-determined as real run)
    assert (
        "analysis-code" not in prompt.lower()
        and "re-analysis of published aggregate" not in prompt.lower()
    ), (
        "Self-determination from real-run notes must NOT inject the proxy clause. "
        f"Prompt snippet: {prompt[:300]!r}"
    )


# ---------------------------------------------------------------------------
# P16: run_review_board explicit is_proxy_study=True wins over auto-determination
# ---------------------------------------------------------------------------

def test_explicit_proxy_true_wins_over_auto(tmp_path):
    """Explicit is_proxy_study=True wins even when notes suggest a real run."""
    from research_vault.manuscript.review_board import run_review_board

    # Notes that LOOK like a real run (non-empty results_location)
    notes_root = tmp_path / "notes_root"
    _make_experiment_note(notes_root, "exp", {"results_location": "/data/results.csv"})

    judge = RecordingJudge()
    tree_root, _ = _make_ms_tree(tmp_path)

    run_review_board(
        pdf_text="Paper text.",
        tree_root=tree_root,
        N=1,
        K=1,
        judge_fn=judge,
        judge_model="mock",
        notes_root=notes_root,
        is_proxy_study=True,  # ← explicit override: MUST win
    )

    assert judge.received_prompts
    prompt = judge.received_prompts[0]
    assert (
        "analysis-provenance" in prompt.lower()
        or "re-analysis" in prompt.lower()
    ), "Explicit is_proxy_study=True must inject proxy clause regardless of notes"


# ---------------------------------------------------------------------------
# P17: run_review_board is_proxy_study=None with no notes_root defaults to False
# ---------------------------------------------------------------------------

def test_self_determine_no_notes_root_defaults_false(tmp_path):
    """is_proxy_study=None with notes_root=None → defaults to False (safe, non-proxy)."""
    from research_vault.manuscript.review_board import run_review_board

    judge = RecordingJudge()
    tree_root, _ = _make_ms_tree(tmp_path)

    run_review_board(
        pdf_text="Paper text.",
        tree_root=tree_root,
        N=1,
        K=1,
        judge_fn=judge,
        judge_model="mock",
        notes_root=None,   # ← no notes_root
        is_proxy_study=None,
    )

    assert judge.received_prompts
    prompt = judge.received_prompts[0]
    assert (
        "analysis-code" not in prompt.lower()
        and "re-analysis of published aggregate" not in prompt.lower()
    ), "No notes_root with is_proxy_study=None must default to non-proxy (no clause)"


# ---------------------------------------------------------------------------
# P18: _proxy_study_reframe_tex enriched: has analysis-provenance positive sentence
# ---------------------------------------------------------------------------

def test_proxy_study_reframe_tex_has_positive_analysis_provenance(tmp_path):
    """_proxy_study_reframe_tex (enriched) includes a positive analysis-provenance sentence."""
    from research_vault.manuscript.appendix import _proxy_study_reframe_tex

    # Call with no notes (fallback to generic)
    result = _proxy_study_reframe_tex()
    low = result.lower()

    # Must have the "no new runs" statement (existing)
    assert "no new" in low or "re-analysis" in low or "not applicable" in low, (
        "Reframe must retain the 'no new runs / seeds N/A' statement"
    )
    # NEW: must also have a positive analysis-provenance sentence
    assert (
        "method" in low or "formula" in low or "transform" in low
        or "in-text" in low or "section" in low
        or "cited" in low or "original publication" in low
    ), (
        "Enriched reframe must include a positive analysis-provenance sentence. "
        f"Got:\n{result}"
    )


# ---------------------------------------------------------------------------
# P19: _proxy_study_reframe_tex renders dataset_id when populated
# ---------------------------------------------------------------------------

def test_proxy_study_reframe_tex_renders_dataset_id(tmp_path):
    """Enriched reframe renders the dataset_id from notes when populated."""
    from research_vault.manuscript.appendix import _proxy_study_reframe_tex

    note_path = _make_experiment_note(
        tmp_path / "notes",
        "exp-proxy",
        {
            "results_location": "",
            "repro_dataset_id": "10.1234/benchmark-2023",
        },
    )

    result = _proxy_study_reframe_tex(experiment_notes=[note_path])
    # The dataset identifier should appear in the reframe (as a provenance pointer)
    assert "10.1234/benchmark-2023" in result, (
        f"Enriched reframe must render the dataset_id '10.1234/benchmark-2023' "
        f"when populated in experiment notes. Got:\n{result}"
    )


# ---------------------------------------------------------------------------
# P20: _proxy_study_reframe_tex renders config_location code pointer when populated
# ---------------------------------------------------------------------------

def test_proxy_study_reframe_tex_renders_analysis_code_pointer(tmp_path):
    """Enriched reframe renders an analysis-code pointer when repro_config_location is populated."""
    from research_vault.manuscript.appendix import _proxy_study_reframe_tex

    note_path = _make_experiment_note(
        tmp_path / "notes",
        "exp-proxy",
        {
            "results_location": "",
            "repro_config_location": "scripts/analysis.py",
        },
    )

    result = _proxy_study_reframe_tex(experiment_notes=[note_path])
    low = result.lower()
    # Must mention the analysis code or script
    assert "analysis" in low and ("script" in low or "code" in low or "analysis.py" in result), (
        f"Enriched reframe must render analysis-code pointer. Got:\n{result}"
    )


# ---------------------------------------------------------------------------
# P21: _proxy_study_reframe_tex graceful fallback when all fields are sentinel
# ---------------------------------------------------------------------------

def test_proxy_study_reframe_tex_graceful_fallback(tmp_path):
    """Enriched reframe is graceful when all notes have sentinel / empty fields."""
    from research_vault.manuscript.appendix import _proxy_study_reframe_tex
    from research_vault.note import REPRO_SENTINEL

    note_path = _make_experiment_note(
        tmp_path / "notes",
        "exp-proxy",
        {
            "results_location": "",
            "repro_dataset_id": REPRO_SENTINEL,
            "repro_config_location": REPRO_SENTINEL,
        },
    )

    # Must not raise; must return a valid LaTeX string
    result = _proxy_study_reframe_tex(experiment_notes=[note_path])
    assert isinstance(result, str) and len(result) > 50, "Must return non-trivial LaTeX"
    # Must NOT fabricate a dataset ID or code pointer
    assert REPRO_SENTINEL not in result, "Sentinel must NOT appear in reframe output"
    # Must still have a fallback positive statement
    assert "method" in result.lower() or "section" in result.lower() or "in-text" in result.lower() or "cited" in result.lower(), (
        f"Graceful fallback must include in-text method reference. Got:\n{result}"
    )


# ---------------------------------------------------------------------------
# P22: Canary tests are unchanged — passages unmodified
# ---------------------------------------------------------------------------

def test_canary_passages_unchanged():
    """_CANARY_STRONG_PASSAGE and _CANARY_WEAK_PASSAGE are unchanged (study-type-independent).

    The canary calibrates judge harshness, not study-type. Its passages must not
    be modified by this slice. Verified by checking the unique marker phrases.
    """
    from research_vault.manuscript.review_board import (
        _CANARY_STRONG_MARKER,
        _CANARY_WEAK_MARKER,
        _CANARY_STRONG_PASSAGE,
        _CANARY_WEAK_PASSAGE,
    )

    # Strong passage contains its unique marker
    assert _CANARY_STRONG_MARKER in _CANARY_STRONG_PASSAGE, (
        "Strong canary passage must still contain its marker phrase"
    )
    # Weak passage contains its unique marker
    assert _CANARY_WEAK_MARKER in _CANARY_WEAK_PASSAGE, (
        "Weak canary passage must still contain its marker phrase"
    )
    # Canary passages must NOT mention the proxy clause concepts
    combined = (_CANARY_STRONG_PASSAGE + _CANARY_WEAK_PASSAGE).lower()
    assert "analysis-provenance" not in combined, (
        "Canary passages must NOT mention analysis-provenance (study-type-independent)"
    )
    assert "is_proxy" not in combined and "proxy study" not in combined, (
        "Canary passages must NOT reference proxy study type"
    )


# ---------------------------------------------------------------------------
# P23: run_review_board meta records is_proxy_study flag
# ---------------------------------------------------------------------------

def test_run_review_board_meta_records_proxy_flag(tmp_path):
    """run_review_board records is_proxy_study determination in result meta."""
    from research_vault.manuscript.review_board import run_review_board

    judge = RecordingJudge()
    tree_root, _ = _make_ms_tree(tmp_path)

    result = run_review_board(
        pdf_text="Paper.",
        tree_root=tree_root,
        N=1,
        K=1,
        judge_fn=judge,
        judge_model="mock",
        is_proxy_study=True,
    )

    meta = result.get("meta", {})
    assert meta.get("is_proxy_study") is True, (
        f"run_review_board meta must record is_proxy_study=True. Got meta: {meta}"
    )
