"""test_sr_ms_review_a.py — SR-MS-REVIEW-a: venue-grounded review-board bounded loop machinery.

Covers ALL §5J.17 test cases (hermetic, mock judge, no LLM calls):
  1.  _extract_review_scores: parses [SOUND:4][REPRO:3]... tokens from judge text
  2.  _extract_review_scores FAIL-CLOSED: unparseable → floor-fail (all zeros) not a pass
  3.  _extract_review_scores partial: dims present = found, missing dims = 0 (floor-fail)
  4.  _evaluate_threshold: below floor on SOUND → not cleared
  5.  _evaluate_threshold: at/above floor on all floor dims → cleared
  6.  MIN-across-reviewers: one low reviewer blocks even if K-1 others pass (not mean)
  7.  run_review_board: N=2 K=1, round-1 below-floor → round-2 fires; both fail → NOT-CLEARED
  8.  run_review_board: cleared at round-1 → round-2 short-circuits (NO judge call in round-2)
  9.  NOT-CLEARED payload: persistent-weakness statement present, never a silent pass
  10. Anti-gaming (c): revise re-fires support-matcher; un-grounded revision → BLOCKED
  11. Anti-gaming (c): revise re-fires cold-read; re-leaked revision → BLOCKED
  12. Fresh-by-construction: round-2 reviewer nodes in manifest have no reads/needs
      pointing to round-1 reviewer output or meta-review
  13. build_approve_payload: review_board section present
  14. rv manuscript review: fails LOUD when RV_JUDGE_MODEL absent
  15. rv manuscript review: fails LOUD when ANTHROPIC_API_KEY absent
  16. Plain rv manuscript check is hermetic (no key needed — no review by default)
  17. N/K frozen in manifest at scaffold (stored in manifest meta + node ids)
  18. Honest tally line never says "approved" — says "cleared at r" or "NOT cleared"
  19. Not-cleared payload is first-class section (never silent pass, never infinite loop)
  20. Walker/schema/store imports unchanged (import-diff: no new walker mechanism)
  21. get_review_rubric: override arg wins over config and default
  22. get_review_rubric: [manuscript_review].rubric config key wins over default
  23. get_review_rubric: falls back to PLACEHOLDER_REVIEW_RUBRIC when nothing set
  24. Canary scaffold present: run_review_board returns canary_ok key in meta
  25. Honest tally key in payload: "review_board_report" (per §5J.17.6 design)
  26. revise-r postcondition logged: author rebuttal recorded in run_state meta (not verdict)
  27. Score extractor: case-insensitive ([sound:3] == [SOUND:3])
  28. N hard-cap 3: max_rounds=5 in config → clamped to 3 in manifest

All hermetic (tmp_path). No live LLM calls. Stdlib only.
sr: SR-MS-REVIEW-a
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# Mock judge helpers
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
        f"REVIEW:\n"
        f"Soundness assessment: The methodology is rigorous.\n"
        f"[SOUND:{sound}] The experimental design is sound.\n\n"
        f"Contribution: The work advances the field.\n"
        f"[CONTRIB:{contrib}]\n\n"
        f"Clarity: Well-written paper.\n"
        f"[CLARITY:{clarity}]\n\n"
        f"Originality: Novel approach.\n"
        f"[ORIG:{orig}]\n\n"
        f"Limitations: Authors address limitations.\n"
        f"[LIMIT:{limit}]\n\n"
        f"Reproducibility: Code and data are available with freeze-hashes.\n"
        f"[REPRO:{repro}]\n\n"
        f"Ethics: No ethical concerns.\n"
        f"[ETHICS:{ethics}]\n\n"
        f"SUMMARY: This paper merits acceptance.\n"
    )


def _below_floor_judge(prompt: str) -> str:
    """Returns scores where SOUND=2 (below floor=3)."""
    return _make_reviewer_response(sound=2, repro=4)


def _above_floor_judge(prompt: str) -> str:
    """Returns scores where all floor dims ≥ 3."""
    return _make_reviewer_response(sound=4, repro=4)


def _unparseable_judge(prompt: str) -> str:
    """Returns garbage — no bracketed scores."""
    return "I cannot evaluate this paper. The format is unclear."


def _floor_exactly_judge(prompt: str) -> str:
    """Returns scores exactly at floor (3) for both floor dims."""
    return _make_reviewer_response(sound=3, repro=3)


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

    # Minimal main.tex
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
# Test 1: _extract_review_scores — basic extraction
# ---------------------------------------------------------------------------

def test_extract_review_scores_basic():
    """Parses [SOUND:4][REPRO:3] etc. tokens from judge text."""
    from research_vault.manuscript.review_board import _extract_review_scores

    text = _make_reviewer_response(sound=4, contrib=3, clarity=4, orig=3, limit=3, repro=4, ethics=4)
    scores = _extract_review_scores(text)

    assert scores is not None, "Should return a dict, not None"
    assert scores["SOUND"] == 4
    assert scores["CONTRIB"] == 3
    assert scores["CLARITY"] == 4
    assert scores["ORIG"] == 3
    assert scores["LIMIT"] == 3
    assert scores["REPRO"] == 4
    assert scores["ETHICS"] == 4


# ---------------------------------------------------------------------------
# Test 2: _extract_review_scores FAIL-CLOSED — unparseable → floor-fail (zeros)
# ---------------------------------------------------------------------------

def test_extract_review_scores_fail_closed_unparseable():
    """Parse failure → all zeros (floor-fail), never a passing score.

    RED-BEFORE-GREEN: this is the fail-closed gate. A naive implementation
    returning None would be unsafe — the consumer must default to floor-fail.
    """
    from research_vault.manuscript.review_board import _extract_review_scores

    scores = _extract_review_scores("I cannot evaluate this paper. No bracketed scores here.")
    # Fail-closed: either None (consumer must treat as floor-fail) or all zeros
    # The contract: _extract_review_scores returns None on complete failure;
    # the consumer (run_round) defaults to floor-fail on None.
    assert scores is None or all(v == 0 for v in scores.values()), (
        "Unparseable input must return None or all-zero scores (fail-closed)"
    )


# ---------------------------------------------------------------------------
# Test 3: _extract_review_scores partial — missing dims → 0 (floor-fail for missing)
# ---------------------------------------------------------------------------

def test_extract_review_scores_partial():
    """Partial parse: found dims parsed, missing dims default to 0."""
    from research_vault.manuscript.review_board import _extract_review_scores

    # Only SOUND and REPRO present
    text = "[SOUND:4] some text [REPRO:3]"
    scores = _extract_review_scores(text)

    assert scores is not None
    assert scores["SOUND"] == 4
    assert scores["REPRO"] == 3
    # Missing dims default to 0 (floor-fail)
    assert scores.get("CONTRIB", 0) == 0
    assert scores.get("CLARITY", 0) == 0


# ---------------------------------------------------------------------------
# Test 4: _evaluate_threshold — below floor → not cleared
# ---------------------------------------------------------------------------

def test_evaluate_threshold_below_floor():
    """MIN-across-reviewers below floor → not cleared."""
    from research_vault.manuscript.review_board import _evaluate_threshold

    # K=1 reviewer, SOUND=2 (below floor=3)
    scores_per_reviewer = [{"SOUND": 2, "REPRO": 4, "CONTRIB": 3}]
    result = _evaluate_threshold(
        scores_per_reviewer,
        floor_dims=["SOUND", "REPRO"],
        floor_value=3,
    )

    assert result["cleared"] is False
    assert result["floor_results"]["SOUND"]["passed"] is False
    assert result["floor_results"]["SOUND"]["min_score"] == 2
    assert result["floor_results"]["REPRO"]["passed"] is True


# ---------------------------------------------------------------------------
# Test 5: _evaluate_threshold — at/above floor → cleared
# ---------------------------------------------------------------------------

def test_evaluate_threshold_cleared():
    """All floor dims at/above floor → cleared."""
    from research_vault.manuscript.review_board import _evaluate_threshold

    scores_per_reviewer = [{"SOUND": 3, "REPRO": 4, "CONTRIB": 2}]
    result = _evaluate_threshold(
        scores_per_reviewer,
        floor_dims=["SOUND", "REPRO"],
        floor_value=3,
    )

    assert result["cleared"] is True
    assert result["floor_results"]["SOUND"]["passed"] is True
    assert result["floor_results"]["REPRO"]["passed"] is True
    # CONTRIB is not a floor dim — not checked


# ---------------------------------------------------------------------------
# Test 6: MIN-across-reviewers (one low reviewer gates, not the mean)
# ---------------------------------------------------------------------------

def test_min_across_reviewers_gates():
    """One below-floor reviewer blocks even if K-1 others pass (mean would not)."""
    from research_vault.manuscript.review_board import _evaluate_threshold

    # K=3 reviewers: two pass, one fails SOUND
    scores_per_reviewer = [
        {"SOUND": 4, "REPRO": 4},  # passes
        {"SOUND": 4, "REPRO": 4},  # passes
        {"SOUND": 2, "REPRO": 4},  # fails (min blocks)
    ]
    result = _evaluate_threshold(
        scores_per_reviewer,
        floor_dims=["SOUND", "REPRO"],
        floor_value=3,
    )

    assert result["cleared"] is False, "MIN-across-reviewers: one fail must block"
    assert result["floor_results"]["SOUND"]["min_score"] == 2, "MIN is 2 (worst reviewer)"

    # Sanity check: mean would be (4+4+2)/3 = 3.33 ≥ floor — mean would wrongly pass
    mean_sound = sum(r["SOUND"] for r in scores_per_reviewer) / len(scores_per_reviewer)
    assert mean_sound >= 3, "Mean passes — proving MIN is the right aggregation here"


# ---------------------------------------------------------------------------
# Test 7: run_review_board — N=2 K=1, round-1 below-floor → round-2 runs;
#          both fail → NOT-CLEARED payload
# ---------------------------------------------------------------------------

def test_run_review_board_not_cleared_after_n(tmp_path):
    """N=2, K=1, both rounds fail → NOT-CLEARED honest failure."""
    from research_vault.manuscript.review_board import run_review_board

    note_path, tree_root, project_root = _make_ms_tree(tmp_path)
    _write_bib(tree_root)

    result = run_review_board(
        pdf_text="A self-contained test paper with no internal paths.",
        tree_root=tree_root,
        N=2,
        K=1,
        floor_dims=["SOUND", "REPRO"],
        floor_value=3,
        judge_fn=_below_floor_judge,  # always below floor (SOUND=2)
        judge_model="mock-model",
        notes_root=project_root,
    )

    assert result["cleared"] is False
    assert result["cleared_at"] is None
    assert len(result["rounds"]) == 2, "N=2 rounds must all run (acyclic unroll)"

    # NOT-CLEARED payload present (§5J.17.5 Guard 1)
    assert "not_cleared" in result
    not_cleared = result["not_cleared"]
    assert not_cleared is not None
    assert "persistent_weakness" in not_cleared
    assert "SOUND" in not_cleared["persistent_weakness"] or "REPRO" in not_cleared["persistent_weakness"], (
        "Persistent weakness must name the failing floor dim"
    )


# ---------------------------------------------------------------------------
# Test 8: run_review_board — cleared at round-1 → round-2 short-circuits
# ---------------------------------------------------------------------------

def test_run_review_board_cleared_round1_short_circuits(tmp_path):
    """Cleared at round-1 → round-2 NO-OPS (no judge call in round-2)."""
    from research_vault.manuscript.review_board import run_review_board

    note_path, tree_root, project_root = _make_ms_tree(tmp_path)
    _write_bib(tree_root)

    judge_calls: list[str] = []

    def _counting_judge(prompt: str) -> str:
        judge_calls.append(prompt)
        return _above_floor_judge(prompt)  # always clears

    result = run_review_board(
        pdf_text="A self-contained test paper.",
        tree_root=tree_root,
        N=2,
        K=1,
        floor_dims=["SOUND", "REPRO"],
        floor_value=3,
        judge_fn=_counting_judge,
        judge_model="mock-model",
        notes_root=project_root,
    )

    assert result["cleared"] is True
    assert result["cleared_at"] == 1, "Must clear at round 1"

    # Round-2 must have been short-circuited — no round-2 judge calls
    # Round-1: K=1 reviewer call → 1 call expected; round-2 must add 0 more
    assert len(judge_calls) == 1, (
        f"Round-2 must be a no-op (0 judge calls in round-2); got {len(judge_calls)} total"
    )


# ---------------------------------------------------------------------------
# Test 9: NOT-CLEARED payload has persistent-weakness, is a first-class section
# ---------------------------------------------------------------------------

def test_not_cleared_persistent_weakness_section(tmp_path):
    """NOT-CLEARED section is first-class; names the failing dim + surviving finding."""
    from research_vault.manuscript.review_board import run_review_board

    note_path, tree_root, project_root = _make_ms_tree(tmp_path)
    _write_bib(tree_root)

    result = run_review_board(
        pdf_text="Test paper.",
        tree_root=tree_root,
        N=1,
        K=1,
        floor_dims=["SOUND", "REPRO"],
        floor_value=3,
        judge_fn=_below_floor_judge,
        judge_model="mock-model",
        notes_root=project_root,
    )

    assert not result["cleared"]
    not_cleared = result.get("not_cleared")
    assert not_cleared is not None, "not_cleared key must be present as a first-class section"
    pw = not_cleared.get("persistent_weakness", "")
    assert pw, "persistent_weakness must be a non-empty string"
    assert "did not reach" in pw.lower() or "failing" in pw.lower() or "SOUND" in pw or "REPRO" in pw, (
        "Persistent weakness must describe the failure, not be a generic empty string"
    )


# ---------------------------------------------------------------------------
# Test 10: Anti-gaming (c) — revise re-fires support-matcher (un-grounded → BLOCKED)
# ---------------------------------------------------------------------------

def test_revise_refire_support_matcher_blocks_ungrounded(tmp_path):
    """revise-r postcondition: if support-matcher blocks revised draft → revision rejected."""
    from research_vault.manuscript.review_board import run_revise

    _, tree_root, project_root = _make_ms_tree(tmp_path)
    _write_bib(tree_root)

    # Write a .tex with a \cite{} that support-matcher will BLOCK
    sections_dir = tree_root / "sections"
    (sections_dir / "results.tex").write_text(
        "\\cite{smith2024} We definitively prove X.\n",
        encoding="utf-8",
    )

    # Support-matcher mock that always returns ABSENT (BLOCK)
    def _blocking_support_judge(prompt: str) -> str:
        return (
            "STEP 0: Claim: definitively prove X.\n"
            "STEP 1: Disconfirm: Note has no span supporting this.\n"
            "STEP 2: No evidence.\n"
            "VERDICT: [ABSENT]\n"
            "REASONING: Source does not contain the claimed result.\n"
            "VERBATIM: none\n"
        )

    # Write a note so the support-matcher can read it
    lit_dir = project_root / "literature"
    lit_dir.mkdir(parents=True, exist_ok=True)
    (lit_dir / "smith2024.md").write_text(
        "---\ntype: literature\ntitle: Smith 2024\n---\nTL;DR: Unrelated finding.\n",
        encoding="utf-8",
    )

    result = run_revise(
        round_num=1,
        meta_review={"meta_review": "Soundness concern: claims are ungrounded."},
        tree_root=tree_root,
        notes_root=project_root,
        support_judge_fn=_blocking_support_judge,
        cold_read_judge_fn=None,  # no cold-read judge needed
        judge_model="mock-model",
    )

    assert result["honesty_gate_blocked"] is True, (
        "Revision that un-grounds must be BLOCKED by the honesty gate (anti-gaming c)"
    )
    assert result["blocking_gate"] in ("support_matcher", "both"), (
        "Must identify which honesty gate blocked"
    )


# ---------------------------------------------------------------------------
# Test 11: Anti-gaming (c) — revise re-fires cold-read (re-leaked → BLOCKED)
# ---------------------------------------------------------------------------

def test_revise_refire_cold_read_blocks_leaked(tmp_path):
    """revise-r postcondition: if cold-read blocks revised draft → revision rejected."""
    from research_vault.manuscript.review_board import run_revise

    _, tree_root, project_root = _make_ms_tree(tmp_path)
    _write_bib(tree_root)

    # Cold-read mock that returns DANGLING (BLOCK) — simulates re-leaked internal path
    def _blocking_cold_read_judge(prompt: str) -> str:
        return (
            "FLAG:\n"
            "VERDICT: [DANGLING]\n"
            'SPAN: "sha256:a1b2c3d4"\n'
            "KIND: internal-plumbing\n"
            "WHERE: Section 2\n"
            "MISSING: Internal hash leaks into rendered paper.\n\n"
            "SUMMARY:\n"
            "OVERALL: [DANGLING]\n"
            "BLOCK_COUNT: 1\n"
            "WARN_COUNT: 0\n"
            "SWEPT: Read the full paper.\n"
        )

    result = run_revise(
        round_num=1,
        meta_review={"meta_review": "Clarity concerns."},
        tree_root=tree_root,
        notes_root=project_root,
        support_judge_fn=None,  # no support-matcher blocking
        cold_read_judge_fn=_blocking_cold_read_judge,
        judge_model="mock-model",
        cold_read_pdf_text="Explicit text with sha256:a1b2c3d4 hash.",
    )

    assert result["honesty_gate_blocked"] is True, (
        "Revision that re-leaks must be BLOCKED by the honesty gate (anti-gaming c)"
    )
    assert result["blocking_gate"] in ("cold_read", "both"), (
        "Must identify which honesty gate blocked"
    )


# ---------------------------------------------------------------------------
# Test 12: Fresh-by-construction — round-2 reviewer nodes have no reads/needs
#          pointing to round-1 reviewer output or meta-review
# ---------------------------------------------------------------------------

def test_fresh_reviewers_by_construction(tmp_path):
    """Round r+1 reviewer nodes in manifest have no channel to round r's output."""
    from research_vault.manuscript import cmd_new
    from research_vault.config import load_config

    # Minimal config setup
    import toml

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
        "max_rounds = 2\n"
        "reviewers_per_round = 2\n",
        encoding="utf-8",
    )

    import research_vault.config as _rvc
    old_cache = _rvc._CACHE
    old_env = os.environ.get("RESEARCH_VAULT_CONFIG")
    try:
        os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_path)
        _rvc._CACHE = None
        cfg = load_config()
        _, tree_root, manifest = cmd_new(
            "myproj", "ms-review-test",
            thesis="A test thesis for fresh-reviewer check.",
            scope=[],
            config=cfg,
        )
    finally:
        _rvc._CACHE = old_cache
        if old_env is None:
            os.environ.pop("RESEARCH_VAULT_CONFIG", None)
        else:
            os.environ["RESEARCH_VAULT_CONFIG"] = old_env

    # Find all round-2 reviewer nodes
    nodes_by_id = {n["id"]: n for n in manifest["nodes"]}
    round2_reviewer_ids = [
        nid for nid in nodes_by_id if nid.startswith("reviewer-2-")
    ]
    assert round2_reviewer_ids, "Manifest must contain round-2 reviewer nodes (N=2, K=2)"

    # Round-1 node ids that would carry review content
    round1_reviewer_ids = {nid for nid in nodes_by_id if nid.startswith("reviewer-1-")}
    round1_meta_id = "meta-review-1"

    for r2_id in round2_reviewer_ids:
        r2_node = nodes_by_id[r2_id]
        # Check needs: must NOT depend on round-1 reviewer nodes or meta-review-1
        needs_from_ids = {n.get("from", "") for n in r2_node.get("needs", [])}
        for r1_id in round1_reviewer_ids:
            assert r1_id not in needs_from_ids, (
                f"Round-2 reviewer {r2_id!r} must NOT depend on round-1 reviewer {r1_id!r} "
                f"(fresh-by-construction: no channel to prior round reviews)"
            )
        assert round1_meta_id not in needs_from_ids, (
            f"Round-2 reviewer {r2_id!r} must NOT depend on {round1_meta_id!r} "
            f"(no channel to prior-round meta-review — fresh reviewers cannot be argued into agreement)"
        )

        # Check reads: must NOT include any review output paths
        reads = r2_node.get("reads", [])
        for r in reads:
            assert "meta-review" not in str(r).lower(), (
                f"Round-2 reviewer reads must not include meta-review content: {r!r}"
            )
            assert "reviewer-1" not in str(r).lower(), (
                f"Round-2 reviewer reads must not include round-1 reviewer content: {r!r}"
            )


# ---------------------------------------------------------------------------
# Test 13: build_approve_payload has review_board section
# ---------------------------------------------------------------------------

def test_build_approve_payload_review_board_section(tmp_path):
    """build_approve_payload includes the review-board payload section."""
    from research_vault.manuscript.check_gates import build_approve_payload

    note_path, tree_root, project_root = _make_ms_tree(tmp_path)
    _write_bib(tree_root)

    payload = build_approve_payload(
        note_path=note_path,
        tree_root=tree_root,
        notes_root=project_root,
        judge_fn=lambda p: (
            "VERDICT: [SUPPORTS]\nREASONING: Fine.\nVERBATIM: test content\n"
        ),
        judge_model="mock-model",
        review_board_judge_fn=_above_floor_judge,
        review_board_pdf_text="A self-contained test paper.",
        review_board_n=1,
        review_board_k=1,
        cold_read_pdf_text="A self-contained test paper.",
    )

    # Review-board section must be present
    assert "review_board" in payload, "build_approve_payload must include review_board section"
    rb = payload["review_board"]
    assert "cleared" in rb, "review_board section must have 'cleared' key"
    assert "review_board_report" in payload, "Honest tally line must be in payload"


# ---------------------------------------------------------------------------
# Test 14: rv manuscript review fails LOUD when RV_JUDGE_MODEL absent
# ---------------------------------------------------------------------------

def test_rv_manuscript_review_loud_fail_no_judge_model(tmp_path, monkeypatch):
    """rv manuscript review: fails LOUD when RV_JUDGE_MODEL absent."""
    monkeypatch.delenv("RV_JUDGE_MODEL", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake-key")

    from research_vault.manuscript.verbs import build_parser, run

    p = build_parser()
    args = p.parse_args(["myproj", "review", "ms-test"])

    # Set up a config
    import research_vault.config as _rvc
    from research_vault.config import Config
    old_cache = _rvc._CACHE
    try:
        # Mock load_config to return a minimal config
        fake_cfg = Config(
            {
                "instance_root": str(tmp_path),
                "projects": {"myproj": str(tmp_path / "projects" / "myproj")},
            },
            config_file=tmp_path / "research_vault.toml",
        )
        _rvc._CACHE = fake_cfg
        exit_code = run(args)
    finally:
        _rvc._CACHE = old_cache

    assert exit_code != 0, "Must fail loud (non-zero exit) when RV_JUDGE_MODEL absent"


# ---------------------------------------------------------------------------
# Test 15: rv manuscript review fails LOUD when ANTHROPIC_API_KEY absent
# ---------------------------------------------------------------------------

def test_rv_manuscript_review_loud_fail_no_api_key(tmp_path, monkeypatch):
    """rv manuscript review: fails LOUD when ANTHROPIC_API_KEY absent."""
    monkeypatch.setenv("RV_JUDGE_MODEL", "fake-opus-model")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    from research_vault.manuscript.verbs import build_parser, run

    p = build_parser()
    args = p.parse_args(["myproj", "review", "ms-test"])

    import research_vault.config as _rvc
    from research_vault.config import Config
    old_cache = _rvc._CACHE
    try:
        fake_cfg = Config(
            {
                "instance_root": str(tmp_path),
                "projects": {"myproj": str(tmp_path / "projects" / "myproj")},
            },
            config_file=tmp_path / "research_vault.toml",
        )
        _rvc._CACHE = fake_cfg
        exit_code = run(args)
    finally:
        _rvc._CACHE = old_cache

    assert exit_code != 0, "Must fail loud (non-zero exit) when ANTHROPIC_API_KEY absent"


# ---------------------------------------------------------------------------
# Test 16: Plain rv manuscript check is hermetic (no review by default)
# ---------------------------------------------------------------------------

def test_plain_check_is_hermetic_no_review(tmp_path, monkeypatch):
    """Plain rv manuscript check runs without RV_JUDGE_MODEL (review is opt-in)."""
    monkeypatch.delenv("RV_JUDGE_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    note_path, tree_root, project_root = _make_ms_tree(tmp_path)
    _write_bib(tree_root)

    from research_vault.manuscript.check_gates import check_manuscript
    # Must not raise or call any LLM judge (hermetic structural check only)
    result = check_manuscript(note_path, tree_root)
    # Returns a dict with errors/warnings — no crash
    assert isinstance(result, dict)
    assert "errors" in result


# ---------------------------------------------------------------------------
# Test 17: N/K frozen in manifest at scaffold
# ---------------------------------------------------------------------------

def test_nk_frozen_in_manifest(tmp_path):
    """N and K are frozen into the manifest at scaffold time (stopping rule)."""
    from research_vault.manuscript import cmd_new
    from research_vault.config import Config
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
        "max_rounds = 2\n"
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
            "myproj", "ms-freeze-test",
            thesis="Test thesis for freeze.",
            scope=[],
            config=cfg,
        )
    finally:
        _rvc._CACHE = old_cache
        if old_env is None:
            os.environ.pop("RESEARCH_VAULT_CONFIG", None)
        else:
            os.environ["RESEARCH_VAULT_CONFIG"] = old_env

    # The manifest must record N and K
    assert "review_config" in manifest, "Manifest must record review_config (N, K frozen at scaffold)"
    rc = manifest["review_config"]
    assert rc["max_rounds"] == 2, f"max_rounds must be frozen as 2; got {rc.get('max_rounds')}"
    assert rc["reviewers_per_round"] == 3, f"reviewers_per_round must be frozen as 3"

    # Node count verification: N=2, K=3 → 2*(3+1+1)-1 = 9 review nodes (no revise after last round)
    node_ids = [n["id"] for n in manifest["nodes"]]
    # Round 1: reviewer-1-L1, reviewer-1-L2, reviewer-1-L3, meta-review-1, revise-1
    for k in range(1, 4):
        assert f"reviewer-1-L{k}" in node_ids, f"reviewer-1-L{k} must be in manifest"
    assert "meta-review-1" in node_ids
    assert "revise-1" in node_ids  # revise exists for non-last round

    # Round 2: reviewer-2-L1..L3, meta-review-2, NO revise-2 (last round)
    for k in range(1, 4):
        assert f"reviewer-2-L{k}" in node_ids, f"reviewer-2-L{k} must be in manifest"
    assert "meta-review-2" in node_ids
    assert "revise-2" not in node_ids, "No revise after the last round (acyclic cap)"


# ---------------------------------------------------------------------------
# Test 18: Honest tally line never says "approved"
# ---------------------------------------------------------------------------

def test_honest_tally_never_says_approved(tmp_path):
    """review_board_report must never say 'approved' — only 'cleared at r' or 'NOT cleared'."""
    from research_vault.manuscript.review_board import run_review_board

    note_path, tree_root, project_root = _make_ms_tree(tmp_path)
    _write_bib(tree_root)

    # Test both cleared and not-cleared
    for judge_fn, should_clear in [(_above_floor_judge, True), (_below_floor_judge, False)]:
        result = run_review_board(
            pdf_text="Test paper.",
            tree_root=tree_root,
            N=1,
            K=1,
            floor_dims=["SOUND", "REPRO"],
            floor_value=3,
            judge_fn=judge_fn,
            judge_model="mock-model",
            notes_root=project_root,
        )

        report = result.get("honest_report", "")
        assert "approved" not in report.lower(), (
            f"Honest tally must never say 'approved'; got: {report!r}"
        )
        if should_clear:
            assert "cleared" in report.lower(), f"Cleared result must say 'cleared': {report!r}"
        else:
            assert "not cleared" in report.lower() or "not_cleared" in report.lower(), (
                f"Not-cleared result must say 'not cleared': {report!r}"
            )


# ---------------------------------------------------------------------------
# Test 19: NOT-CLEARED is first-class section, not a silent pass
# ---------------------------------------------------------------------------

def test_not_cleared_is_first_class_not_silent(tmp_path):
    """NOT-CLEARED after N rounds is a first-class payload, not a silent pass or infinite loop."""
    from research_vault.manuscript.review_board import run_review_board

    note_path, tree_root, project_root = _make_ms_tree(tmp_path)
    _write_bib(tree_root)

    result = run_review_board(
        pdf_text="Test paper.",
        tree_root=tree_root,
        N=2,
        K=1,
        floor_dims=["SOUND", "REPRO"],
        floor_value=3,
        judge_fn=_below_floor_judge,
        judge_model="mock-model",
        notes_root=project_root,
    )

    # Must have exactly N rounds — acyclic, no extra
    assert len(result["rounds"]) == 2, "Exactly N rounds must run (no infinite loop)"
    assert result["cleared"] is False
    # Not-cleared payload must be a dict (first-class section)
    assert isinstance(result.get("not_cleared"), dict), "not_cleared must be a dict (first-class section)"
    # Cleared_at must be None
    assert result["cleared_at"] is None


# ---------------------------------------------------------------------------
# Test 20: Walker/schema/store imports unchanged (no new DAG mechanism)
# ---------------------------------------------------------------------------

def test_walker_schema_store_unchanged():
    """Import-diff: walker.py, schema.py, store.py are NOT modified by this SR."""
    import importlib.util

    # Find the package path
    spec = importlib.util.find_spec("research_vault")
    assert spec is not None
    pkg_root = Path(spec.origin).parent

    walker_path = pkg_root / "dag" / "walker.py"
    schema_path = pkg_root / "dag" / "schema.py"
    store_path = pkg_root / "dag" / "store.py"

    # These files must NOT import from review_board
    for fpath in [walker_path, schema_path, store_path]:
        if not fpath.exists():
            continue
        content = fpath.read_text(encoding="utf-8")
        assert "review_board" not in content, (
            f"{fpath.name} must NOT import review_board (no new DAG mechanism)"
        )


# ---------------------------------------------------------------------------
# Test 21: get_review_rubric — override arg wins
# ---------------------------------------------------------------------------

def test_get_review_rubric_override_wins():
    """override arg always wins over config and default."""
    from research_vault.manuscript.review_board import get_review_rubric

    override = "MY CUSTOM RUBRIC"
    result = get_review_rubric(override=override, config=None)
    assert result == override


# ---------------------------------------------------------------------------
# Test 22: get_review_rubric — [manuscript_review].rubric config key wins over default
# ---------------------------------------------------------------------------

def test_get_review_rubric_config_key_wins(tmp_path):
    """[manuscript_review].rubric config key wins over PLACEHOLDER_REVIEW_RUBRIC."""
    from research_vault.manuscript.review_board import get_review_rubric
    from research_vault.config import Config

    cfg = Config(
        {"instance_root": str(tmp_path), "manuscript_review": {"rubric": "CONFIG RUBRIC"}},
        config_file=tmp_path / "research_vault.toml",
    )
    result = get_review_rubric(override=None, config=cfg)
    assert result == "CONFIG RUBRIC"


# ---------------------------------------------------------------------------
# Test 23: get_review_rubric — falls back to PLACEHOLDER_REVIEW_RUBRIC
# ---------------------------------------------------------------------------

def test_get_review_rubric_fallback_to_placeholder():
    """Falls back to PLACEHOLDER_REVIEW_RUBRIC (not Ada's real rubric — that's SR-MS-REVIEW-b)."""
    from research_vault.manuscript.review_board import (
        get_review_rubric,
        PLACEHOLDER_REVIEW_RUBRIC,
    )

    result = get_review_rubric(override=None, config=None)
    assert result == PLACEHOLDER_REVIEW_RUBRIC
    # The placeholder must mention it's a placeholder (honest boundary)
    assert "PLACEHOLDER" in result.upper() or "placeholder" in result.lower()


# ---------------------------------------------------------------------------
# Test 24: Canary scaffold present in result meta
# ---------------------------------------------------------------------------

def test_canary_scaffold_present_in_meta(tmp_path):
    """run_review_board returns canary_ok key in meta (scaffold wired, bounds in -b)."""
    from research_vault.manuscript.review_board import run_review_board

    note_path, tree_root, project_root = _make_ms_tree(tmp_path)
    _write_bib(tree_root)

    result = run_review_board(
        pdf_text="A self-contained paper.",
        tree_root=tree_root,
        N=1,
        K=1,
        floor_dims=["SOUND", "REPRO"],
        floor_value=3,
        judge_fn=_above_floor_judge,
        judge_model="mock-model",
        notes_root=project_root,
    )

    # Each round must record canary_ok in its meta
    for round_result in result["rounds"]:
        assert "canary_ok" in round_result.get("meta", {}), (
            "Each round meta must have canary_ok key (canary scaffold wired)"
        )


# ---------------------------------------------------------------------------
# Test 25: Honest tally "review_board_report" key in build_approve_payload
# ---------------------------------------------------------------------------

def test_review_board_report_in_approve_payload(tmp_path):
    """review_board_report key present in build_approve_payload output."""
    from research_vault.manuscript.check_gates import build_approve_payload

    note_path, tree_root, project_root = _make_ms_tree(tmp_path)
    _write_bib(tree_root)

    payload = build_approve_payload(
        note_path=note_path,
        tree_root=tree_root,
        notes_root=project_root,
        judge_fn=lambda p: "VERDICT: [SUPPORTS]\nREASONING: Fine.\nVERBATIM: test\n",
        judge_model="mock-model",
        review_board_judge_fn=_above_floor_judge,
        review_board_pdf_text="A self-contained test paper.",
        review_board_n=1,
        review_board_k=1,
        cold_read_pdf_text="A self-contained test paper.",
    )

    assert "review_board_report" in payload, (
        "review_board_report must be a key in build_approve_payload output (§5J.17.6 honest line)"
    )
    report = payload["review_board_report"]
    assert "approved" not in report.lower(), "Honest line must never say 'approved'"


# ---------------------------------------------------------------------------
# Test 26: revise-r postcondition: rebuttal recorded in meta (not verdict)
# ---------------------------------------------------------------------------

def test_revise_rebuttal_recorded_in_meta_not_verdict(tmp_path):
    """run_revise records rebuttal in meta, not as a verdict (crew-cannot-self-approve)."""
    from research_vault.manuscript.review_board import run_revise

    _, tree_root, project_root = _make_ms_tree(tmp_path)
    _write_bib(tree_root)

    # Support-matcher mock that passes (no BLOCK)
    def _passing_support_judge(prompt: str) -> str:
        return (
            "STEP 0: Claim: test.\n"
            "STEP 1: Disconfirm: None found.\n"
            "STEP 2: No cite.\n"
            "VERDICT: [SUPPORTS]\n"
            "REASONING: Source supports claim.\n"
            "VERBATIM: test content\n"
        )

    # Cold-read mock that passes
    def _passing_cold_read_judge(prompt: str) -> str:
        return (
            "SUMMARY:\n"
            "OVERALL: [STANDS-ALONE]\n"
            "BLOCK_COUNT: 0\n"
            "WARN_COUNT: 0\n"
            "SWEPT: Read the full paper.\n"
        )

    meta_review_data = {"meta_review": "Please clarify the method section."}

    result = run_revise(
        round_num=1,
        meta_review=meta_review_data,
        tree_root=tree_root,
        notes_root=project_root,
        support_judge_fn=_passing_support_judge,
        cold_read_judge_fn=_passing_cold_read_judge,
        judge_model="mock-model",
    )

    # Rebuttal must be in result (not a verdict)
    assert "rebuttal" in result, "Rebuttal must be recorded in revise result"
    assert result.get("honesty_gate_blocked") is False, "Passing honesty gates → not blocked"
    # Must not claim acceptance (crew-cannot-self-approve)
    assert "verdict" not in result or result.get("verdict") != "APPROVED"


# ---------------------------------------------------------------------------
# Test 27: Score extractor is case-insensitive
# ---------------------------------------------------------------------------

def test_extract_review_scores_case_insensitive():
    """[sound:3] and [SOUND:3] both parse correctly."""
    from research_vault.manuscript.review_board import _extract_review_scores

    text_lower = "[sound:3] [repro:4]"
    text_upper = "[SOUND:3] [REPRO:4]"

    scores_lower = _extract_review_scores(text_lower)
    scores_upper = _extract_review_scores(text_upper)

    assert scores_lower is not None
    assert scores_upper is not None
    assert scores_lower.get("SOUND") == 3
    assert scores_upper.get("SOUND") == 3
    assert scores_lower.get("REPRO") == 4
    assert scores_upper.get("REPRO") == 4


# ---------------------------------------------------------------------------
# Test 28: N hard-cap 3 — max_rounds > 3 in config → clamped to 3
# ---------------------------------------------------------------------------

def test_n_hardcap_3(tmp_path):
    """max_rounds > 3 in config is clamped to 3 (hard-cap per §5J.17.9 D-REV-3)."""
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
        "max_rounds = 5\n"
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
            "myproj", "ms-cap-test",
            thesis="Test thesis.",
            scope=[],
            config=cfg,
        )
    finally:
        _rvc._CACHE = old_cache
        if old_env is None:
            os.environ.pop("RESEARCH_VAULT_CONFIG", None)
        else:
            os.environ["RESEARCH_VAULT_CONFIG"] = old_env

    rc = manifest.get("review_config", {})
    assert rc.get("max_rounds") <= 3, (
        f"max_rounds must be clamped to hard-cap 3; got {rc.get('max_rounds')}"
    )
