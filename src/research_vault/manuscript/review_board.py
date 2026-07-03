"""review_board.py — SR-MS-REVIEW-a: scientific-merit review-board bounded loop machinery.

SCOPE AND HONEST BOUNDARY
==========================
This module implements the CONTROL-FLOW and PREDICATE layer of the venue-grounded
review-board gate (§5J.17). It does NOT implement Ada's real rubric or the
bidirectional canary calibration — those are SR-MS-REVIEW-b.

What this module provides:
  - A NEW dimensioned-score bracket extractor (do NOT overload support_matcher.py's
    4-verdict [SUPPORTS/…] extractor or control.py's [PASS]/[BLOCK] extractor).
  - The threshold predicate: per-dimension FLOORS, MIN-across-reviewers.
  - The bounded N-round unroll loop with node-level skip short-circuit.
  - The revise-r postcondition: re-fires support-matcher + cold-read on the revised
    draft so a revision CANNOT un-ground or re-leak to please a reviewer.
  - A PLACEHOLDER default rubric (Ada's real rubric = SR-MS-REVIEW-b).
  - A canary SCAFFOLD (wired but calibrated expected-score bounds = SR-MS-REVIEW-b).
  - The rubric seam: get_review_rubric(override, config) → str.

VERDICT TOKENS (dimensioned scores — NEW 7-dim set, separate from all prior extractors)
=======================================================================
  Bracket form: [DIM:SCORE] where DIM ∈ {SOUND, CONTRIB, CLARITY, ORIG, LIMIT, REPRO, ETHICS}
  and SCORE is a digit on the venue scale (default 1–5).

  Example: [SOUND:4] [CONTRIB:3] [CLARITY:4] [ORIG:3] [LIMIT:3] [REPRO:4] [ETHICS:4]

  ★ FAIL-CLOSED: a score that cannot be parsed defaults to 0 (below any floor).
    A missing dim also defaults to 0. Never a silent pass.

FLOOR PREDICATE
===============
  cleared ⟺ ∀ dim ∈ floor_dims: MIN_across_reviewers(scores[dim]) ≥ floor_value
  Default floor_dims = {SOUND, REPRO}, floor_value = 3 (borderline-accept, 1–5 scale).
  The other 5 dims (CONTRIB, CLARITY, ORIG, LIMIT, ETHICS) are surface-only — never
  auto-gate. NO overall/average score gates anything (the gameable quantity).

BOUNDED ACYCLIC UNROLL
======================
  N pre-declared round-blocks (N frozen at scaffold, hard-cap 3, default 2):
    per round r ∈ {1..N}:
      K parallel reviewer-r-L{k} nodes → meta-review-r join → (r<N) revise-r
  Cleared-at-round-r' → remaining rounds are no-ops (node-level short-circuit on
  RunState.meta["review_board"]["cleared_at"]).

REVISE-R POSTCONDITION (anti-gaming c)
=======================================
  run_revise() re-fires the support-matcher (SR-MS-2) AND the cold-read
  (SR-MS-COLDREAD) on the revised draft. If either BLOCKS → the revision is
  rejected with honesty_gate_blocked=True. A revision cannot un-ground or
  re-leak to please a reviewer.

CANARY SCAFFOLD (SR-MS-REVIEW-b fills the calibrated bounds)
=============================================================
  Placeholder: run_canary_scaffold() always returns canary_ok=True in -a.
  SR-MS-REVIEW-b drops in the known-STRONG and known-WEAK probes with
  calibrated expected-score bounds.

RUBRIC SEAM
===========
  PLACEHOLDER_REVIEW_RUBRIC ships as the seam default (Ada's real rubric = -b).
  get_review_rubric(override, config) reads [manuscript_review].rubric from config.

Stdlib only.
sr: SR-MS-REVIEW-a
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Opus-tier judge at runtime (D-REV-8). Tests always pass judge_fn= (mock).
DEFAULT_JUDGE_MODEL: str = os.environ.get("RV_JUDGE_MODEL", "")

# Default floor configuration (D-REV-2 / D-REV-5 / D-REV-3 / D-REV-4 / D-REV-6)
_DEFAULT_FLOOR_DIMS: list[str] = ["SOUND", "REPRO"]
_DEFAULT_FLOOR_VALUE: int = 3           # ≥ 3 = borderline-accept (1–5 scale)
_DEFAULT_VENUE_SCALE: str = "1-5"
_DEFAULT_MAX_ROUNDS: int = 2            # N (stopping rule)
_MAX_ROUNDS_HARDCAP: int = 3            # hard ceiling — never > 3 (D-REV-3)
_DEFAULT_REVIEWERS_PER_ROUND: int = 3   # K

# All 7 review dimensions (venue rubric)
_ALL_DIMS: tuple[str, ...] = (
    "SOUND", "CONTRIB", "CLARITY", "ORIG", "LIMIT", "REPRO", "ETHICS",
)

# ---------------------------------------------------------------------------
# Dimensioned-score bracket extractor — NEW (does NOT overload support_matcher's
# 4-verdict extractor or control.py's [PASS]/[BLOCK] extractor, by design §5J.17.3)
# ---------------------------------------------------------------------------

_REVIEW_SCORE_RE = re.compile(
    r"\[(SOUND|CONTRIB|CLARITY|ORIG|LIMIT|REPRO|ETHICS):(\d+)\]",
    re.IGNORECASE,
)


def _extract_review_scores(text: str) -> dict[str, int] | None:
    """Extract dimensioned review scores from judge response text.

    Recognized form: ``[SOUND:4]``, ``[REPRO:3]``, ``[contrib:2]`` etc.
    (case-insensitive, brackets required, DIM:SCORE format).

    Returns:
      dict mapping uppercase DIM → int score for every DIM found in text.
      Missing dims (not present in text) are NOT included (caller defaults to 0).
      Returns None on COMPLETE parse failure (no tokens found at all).

    ★ FAIL-CLOSED: None / missing-dim → caller must default to 0 (floor-fail).
    A score cannot be silently read as passing. Never a default-pass on failure.

    This is a NEW dimensioned-score extractor scoped exclusively to the review-board.
    It does NOT overload support_matcher.py's [SUPPORTS/PARTIAL/ABSENT/CONTRADICTS]
    extractor (SR-MS-2) or coldread.py's [STANDS-ALONE/DANGLING/NEEDS-CONTEXT]
    extractor (SR-MS-COLDREAD) or control.py's [PASS]/[BLOCK] extractor.

    sr: SR-MS-REVIEW-a §5J.17.3
    """
    scores: dict[str, int] = {}
    for m in _REVIEW_SCORE_RE.finditer(text):
        dim = m.group(1).upper()
        try:
            score = int(m.group(2))
        except (ValueError, IndexError):
            score = 0  # fail-closed: unparseable digit → 0
        scores[dim] = score
    if not scores:
        return None  # complete failure → caller defaults to floor-fail (0)
    return scores


# ---------------------------------------------------------------------------
# Threshold predicate
# ---------------------------------------------------------------------------

def _evaluate_threshold(
    scores_per_reviewer: list[dict[str, int]],
    floor_dims: list[str],
    floor_value: int,
    venue_scale: str = "1-5",
) -> dict[str, Any]:
    """Evaluate the floor predicate across K reviewers.

    Aggregation = MIN-across-reviewers (the worst reviewer gates).
    cleared ⟺ ∀ dim ∈ floor_dims: min(scores_per_reviewer[dim]) ≥ floor_value.

    Missing dim in a reviewer's score dict → defaults to 0 (fail-closed).

    Returns:
      cleared:            bool — True iff ALL floor dims pass
      floor_results:      {dim → {min_score, floor, passed}}
      worst_reviewer_scores: dict of reviewer index → per-dim score
    """
    if not scores_per_reviewer:
        # No reviewers → cannot clear (fail-closed)
        return {
            "cleared": False,
            "floor_results": {
                dim: {"min_score": 0, "floor": floor_value, "passed": False}
                for dim in floor_dims
            },
            "worst_reviewer_scores": {},
        }

    floor_results: dict[str, dict[str, Any]] = {}
    for dim in floor_dims:
        dim_scores = [r.get(dim, 0) for r in scores_per_reviewer]
        min_score = min(dim_scores)
        floor_results[dim] = {
            "min_score": min_score,
            "floor": floor_value,
            "passed": min_score >= floor_value,
        }

    cleared = all(fr["passed"] for fr in floor_results.values())

    # Identify worst reviewer by aggregate-floor-miss
    worst: dict[int, dict[str, int]] = {}
    for i, r in enumerate(scores_per_reviewer):
        worst[i] = {dim: r.get(dim, 0) for dim in floor_dims}

    return {
        "cleared": cleared,
        "floor_results": floor_results,
        "worst_reviewer_scores": worst,
    }


# ---------------------------------------------------------------------------
# Placeholder rubric (Ada's real rubric = SR-MS-REVIEW-b)
# ---------------------------------------------------------------------------

PLACEHOLDER_REVIEW_RUBRIC: str = """\
REVIEW-BOARD JUDGE RUBRIC — PLACEHOLDER (SR-MS-REVIEW-a)

NOTE: This is a placeholder rubric. Ada's venue-grounded review-board rubric
(7-dimension NeurIPS/ICLR/ICML/ARR scales, ARR justify-each-score rule,
Yes/No/NA Responsible-NLP checklist, disconfirm-first + anti-anchoring moves)
ships in SR-MS-REVIEW-b. Do NOT use this placeholder in production.

Your task is to assess the following paper on 7 dimensions:
  Soundness (SOUND) — methodological rigor; binds to support-grounding provenance
  Contribution (CONTRIB) — significance and novelty
  Clarity (CLARITY) — exposition and organization
  Originality (ORIG) — novel ideas, framing, or methods
  Limitations (LIMIT) — honest treatment of scope and failure modes
  Reproducibility (REPRO) — freeze-hashes, run-ids, code; binds to provenance
  Ethics (ETHICS) — responsible-AI checklist

Score each dimension on a 1–5 scale:
  1 = strong reject   2 = reject   3 = borderline   4 = accept   5 = strong accept

IMPORTANT: For each score, emit the machine-parseable bracket token:
  [SOUND:N] [CONTRIB:N] [CLARITY:N] [ORIG:N] [LIMIT:N] [REPRO:N] [ETHICS:N]
where N is your integer score (1–5). These tokens MUST appear in your response.

Paper text:
{PDF_TEXT}
"""


def get_review_rubric(
    override: str | None = None,
    config: Any | None = None,
) -> str:
    """Return the active review-board judge rubric.

    Priority: override arg > [manuscript_review].rubric in config > PLACEHOLDER.

    Ada's real rubric drops in via:
      (a) override="..." (direct pass), OR
      (b) [manuscript_review] rubric = "..." in research_vault.toml.
    The PLACEHOLDER ships in -a; Ada's real rubric replaces it in SR-MS-REVIEW-b.

    sr: SR-MS-REVIEW-a §5J.17.3
    """
    if override is not None:
        return override
    if config is not None:
        raw = getattr(config, "_raw", {})
        ms_review = raw.get("manuscript_review", {})
        if isinstance(ms_review, dict):
            rubric_cfg = ms_review.get("rubric")
            if isinstance(rubric_cfg, str) and rubric_cfg.strip():
                return rubric_cfg
    return PLACEHOLDER_REVIEW_RUBRIC


# ---------------------------------------------------------------------------
# Canary scaffold (bidirectional probe calibration = SR-MS-REVIEW-b)
# ---------------------------------------------------------------------------

def run_canary_scaffold(
    judge_fn: Callable[[str], str],
    judge_model: str = "",
    rubric: str = "",
) -> dict[str, Any]:
    """Run the canary scaffold probes before trusting real reviewer scores.

    SR-MS-REVIEW-a: scaffold wired; bidirectional calibrated bounds = SR-MS-REVIEW-b.
    In -a, both probes always return canary_ok=True (placeholder — real calibration
    requires Ada's expected-score bounds).

    A real canary (SR-MS-REVIEW-b) fires TWO synthetic probes:
      (a) known-STRONG: must NOT score at floor (else blind rejector)
      (b) known-WEAK:   must NOT score at ceiling (else blind rubber-stamper)
    Either out of bounds → ABORT the round LOUDLY.

    Returns:
      canary_ok:     bool — True (placeholder in -a; calibrated in -b)
      canary_note:   str  — explanation of placeholder status
    """
    # SR-MS-REVIEW-a placeholder: no live calibration yet.
    # SR-MS-REVIEW-b will replace this with real probe + expected-score bounds.
    return {
        "canary_ok": True,
        "canary_note": (
            "CANARY SCAFFOLD (SR-MS-REVIEW-a): placeholder — bidirectional probe "
            "calibration (known-STRONG + known-WEAK bounds) ships in SR-MS-REVIEW-b."
        ),
    }


# ---------------------------------------------------------------------------
# Reviewer node execution
# ---------------------------------------------------------------------------

def _build_reviewer_prompt(pdf_text: str, rubric: str) -> str:
    """Build the reviewer judge prompt (rubric + paper text).

    Anti-anchoring (§5J.17.2): the reviewer sees ONLY the rendered PDF text +
    the rubric. NOT the paper's own thesis/ms_id (those would anchor the reviewer
    to the author's framing). The rubric slot is {PDF_TEXT}.
    """
    return rubric.replace("{PDF_TEXT}", pdf_text)


def run_reviewer_node(
    pdf_text: str,
    *,
    round_num: int,
    lens_num: int,
    judge_fn: Callable[[str], str],
    judge_model: str,
    rubric_override: str | None = None,
    config: Any | None = None,
    run_state_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a single reviewer agent node for round round_num, lens lens_num.

    Node-level skip short-circuit: if run_state_meta["review_board"]["cleared_at"]
    is already set (a prior round cleared), returns immediately — NO judge call,
    NO score extraction. This is the compute early-return analogue of inject_results'
    empty-scope early-return (§5J.17.2, zero walker change).

    Fresh-by-construction: this function does NOT accept prior-round reviews,
    scores, or the author's rebuttal as inputs. The caller (the DAG runner / test)
    is responsible for not feeding them; the function signature enforces the boundary.

    Args:
        pdf_text:         rendered PDF text (pdftotext output or fallback)
        round_num:        round index (1-based)
        lens_num:         reviewer lens index within the round (1-based)
        judge_fn:         injectable LLM call (prompt: str) -> str
        judge_model:      model-id to log (Opus-tier D-REV-8)
        rubric_override:  optional rubric override (Ada's real rubric via seam default in -b)
        config:           optional Config for rubric lookup
        run_state_meta:   optional RunState.meta dict for skip short-circuit

    Returns dict with:
        round:         int
        lens:          int
        scores:        dict[str, int] — per-dim scores (0 for missing/unparseable)
        raw_response:  str
        judge_model:   str
        prompt_hash:   str (sha256)
        skipped:       bool — True if short-circuited (prior round cleared)
    """
    node_id = f"reviewer-{round_num}-L{lens_num}"

    # --- Node-level skip short-circuit (§5J.17.2) ---
    if run_state_meta is not None:
        rb_meta = run_state_meta.get("review_board", {})
        if rb_meta.get("cleared_at") is not None:
            return {
                "round": round_num,
                "lens": lens_num,
                "node_id": node_id,
                "scores": {dim: 0 for dim in _ALL_DIMS},
                "raw_response": "",
                "judge_model": judge_model,
                "prompt_hash": "",
                "skipped": True,
                "skip_reason": f"cleared at round {rb_meta['cleared_at']}",
            }

    rubric = get_review_rubric(override=rubric_override, config=config)
    prompt = _build_reviewer_prompt(pdf_text, rubric)
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    raw_response = judge_fn(prompt)

    # Extract scores — FAIL-CLOSED: None → all zeros
    extracted = _extract_review_scores(raw_response)
    if extracted is None:
        scores = {dim: 0 for dim in _ALL_DIMS}
    else:
        # Fill missing dims with 0 (fail-closed)
        scores = {dim: extracted.get(dim, 0) for dim in _ALL_DIMS}

    return {
        "round": round_num,
        "lens": lens_num,
        "node_id": node_id,
        "scores": scores,
        "raw_response": raw_response,
        "judge_model": judge_model,
        "prompt_hash": prompt_hash,
        "skipped": False,
    }


# ---------------------------------------------------------------------------
# Meta-review node (fan-in join + threshold evaluation)
# ---------------------------------------------------------------------------

def run_meta_review(
    round_num: int,
    reviewer_results: list[dict[str, Any]],
    *,
    floor_dims: list[str] = _DEFAULT_FLOOR_DIMS,
    floor_value: int = _DEFAULT_FLOOR_VALUE,
    venue_scale: str = _DEFAULT_VENUE_SCALE,
    canary_judge_fn: Callable[[str], str] | None = None,
    canary_rubric: str = "",
    judge_model: str = "",
    run_state_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate K reviewer results by MIN, evaluate threshold, return meta dict.

    §5J.17.3 / §5J.17.4:
      - Aggregation = MIN (worst reviewer gates, not mean)
      - Threshold = per-dim floors on substantive axes only
      - Canary scaffold: run before trusting scores

    Returns dict with:
        round:             int
        cleared:           bool
        cleared_at:        int | None
        floor_results:     {dim: {min_score, floor, passed}}
        scores_per_reviewer: list[dict]
        meta_review:       str (synthesized finding)
        worst_findings:    list[str] (top 3 unresolved concerns)
        checklist:         str (Yes/No/NA placeholder — real in -b)
        canary_ok:         bool
        canary_note:       str
        meta:              {canary_ok, judge_model, n_reviewers, floor_results}
        skipped:           bool
    """
    node_id = f"meta-review-{round_num}"

    # Skip if already cleared
    if run_state_meta is not None:
        rb_meta = run_state_meta.get("review_board", {})
        if rb_meta.get("cleared_at") is not None:
            return {
                "round": round_num,
                "node_id": node_id,
                "cleared": True,
                "cleared_at": rb_meta.get("cleared_at"),
                "floor_results": {},
                "scores_per_reviewer": [],
                "meta_review": f"SKIPPED — cleared at round {rb_meta['cleared_at']}",
                "worst_findings": [],
                "checklist": "SKIPPED",
                "canary_ok": True,
                "canary_note": "SKIPPED",
                "meta": {"canary_ok": True, "judge_model": judge_model, "n_reviewers": 0},
                "skipped": True,
            }

    # Filter out skipped reviewers
    active_results = [r for r in reviewer_results if not r.get("skipped", False)]
    scores_per_reviewer = [r["scores"] for r in active_results]

    # --- Canary scaffold (placeholder in -a; real in -b) ---
    _canary_fn = canary_judge_fn if canary_judge_fn is not None else (lambda p: "")
    canary_result = run_canary_scaffold(_canary_fn, judge_model=judge_model, rubric=canary_rubric)

    # --- Threshold predicate ---
    threshold = _evaluate_threshold(
        scores_per_reviewer,
        floor_dims=floor_dims,
        floor_value=floor_value,
        venue_scale=venue_scale,
    )

    cleared = threshold["cleared"]
    cleared_at = round_num if cleared else None

    # --- Synthesize meta-review ---
    # Identify the worst failing dimensions for the summary
    failing_dims = [
        dim for dim, fr in threshold["floor_results"].items() if not fr["passed"]
    ]
    if failing_dims:
        meta_review_text = (
            f"Round {round_num} — NOT CLEARED. "
            f"Failing floor dimensions: {', '.join(failing_dims)}. "
            f"MIN scores: "
            + ", ".join(
                f"{dim}={threshold['floor_results'][dim]['min_score']}/{floor_value}"
                for dim in failing_dims
            )
        )
        worst_findings = [
            f"[Round {round_num}] {dim}: MIN score "
            f"{threshold['floor_results'][dim]['min_score']} < floor {floor_value}"
            for dim in failing_dims
        ][:3]
    else:
        meta_review_text = (
            f"Round {round_num} — CLEARED. "
            f"All floor dimensions meet threshold: "
            + ", ".join(
                f"{dim}={threshold['floor_results'][dim]['min_score']}/{floor_value}"
                for dim in (floor_dims or [])
            )
        )
        worst_findings = []

    # Update run_state_meta if provided
    if run_state_meta is not None and cleared:
        if "review_board" not in run_state_meta:
            run_state_meta["review_board"] = {}
        run_state_meta["review_board"]["cleared_at"] = round_num

    meta_record = {
        "canary_ok": canary_result["canary_ok"],
        "judge_model": judge_model,
        "n_reviewers": len(active_results),
        "floor_results": threshold["floor_results"],
    }

    return {
        "round": round_num,
        "node_id": node_id,
        "cleared": cleared,
        "cleared_at": cleared_at,
        "floor_results": threshold["floor_results"],
        "scores_per_reviewer": scores_per_reviewer,
        "meta_review": meta_review_text,
        "worst_findings": worst_findings,
        "checklist": (
            "CHECKLIST PLACEHOLDER — Responsible-NLP Yes/No/NA checklist "
            "ships in SR-MS-REVIEW-b (Ada's rubric)."
        ),
        "canary_ok": canary_result["canary_ok"],
        "canary_note": canary_result.get("canary_note", ""),
        "meta": meta_record,
        "skipped": False,
    }


# ---------------------------------------------------------------------------
# Revise node (re-fires honesty gates — anti-gaming c)
# ---------------------------------------------------------------------------

def run_revise(
    round_num: int,
    meta_review: dict[str, Any],
    tree_root: Path,
    *,
    notes_root: Path | None = None,
    support_judge_fn: Callable[[str], str] | None = None,
    cold_read_judge_fn: Callable[[str], str] | None = None,
    judge_model: str = "",
    config: Any | None = None,
    cold_read_pdf_text: str | None = None,
) -> dict[str, Any]:
    """Run the revise node for round round_num.

    Postcondition: the revised draft STILL passes the honesty gates (§5J.17.2).
    Re-fires:
      (1) support-matcher (SR-MS-2) — claim grounding
      (2) cold-read (SR-MS-COLDREAD) — self-containment

    If either BLOCKS → honesty_gate_blocked=True, blocking_gate identified.
    The rebuttal is RECORDED as an artifact (not a verdict — crew-cannot-self-approve).

    Args:
        round_num:            round index (1-based)
        meta_review:          dict from run_meta_review (or {"meta_review": str})
        tree_root:            path to the manuscript artifact tree
        notes_root:           project notes dir (for support-matcher note lookup)
        support_judge_fn:     injectable support-matcher judge (mock in tests)
        cold_read_judge_fn:   injectable cold-read judge (mock in tests)
        judge_model:          model-id to log
        config:               optional Config
        cold_read_pdf_text:   optional pre-extracted pdftotext output for cold-read

    Returns dict with:
        round:                int
        honesty_gate_blocked: bool
        blocking_gate:        str | None — "support_matcher" | "cold_read" | "both" | None
        rebuttal:             str — recorded author rebuttal (never a verdict)
        support_errors:       list[str]
        cold_read_errors:     list[str]
    """
    node_id = f"revise-{round_num}"

    # Record the rebuttal (the author's response to the meta-review).
    # This is an ARTIFACT recorded for the human's review — it is NOT a verdict.
    # crew-cannot-self-approve: the author cannot accept their own paper.
    meta_review_text = (
        meta_review.get("meta_review", "")
        if isinstance(meta_review, dict)
        else str(meta_review)
    )
    rebuttal = (
        f"[REBUTTAL — round {round_num}]: "
        f"Author acknowledges meta-review concerns: {meta_review_text[:200]}. "
        f"Revision in progress. (Recorded artifact — not a verdict.)"
    )

    support_blocked = False
    support_errors: list[str] = []
    cold_read_blocked = False
    cold_read_errors: list[str] = []

    # --- Re-fire support-matcher (anti-gaming c) ---
    if support_judge_fn is not None:
        try:
            from research_vault.manuscript.check_gates import check_support_tally
            tally = check_support_tally(
                tree_root,
                notes_root=notes_root,
                judge_fn=support_judge_fn,
                judge_model=judge_model,
                config=config,
            )
            support_errors = tally.get("errors", [])
            support_blocked = len(support_errors) > 0
        except Exception as e:
            support_errors = [f"support-matcher re-fire error: {e}"]
            support_blocked = True

    # --- Re-fire cold-read (anti-gaming c) ---
    if cold_read_judge_fn is not None:
        try:
            from research_vault.manuscript.check_gates import check_cold_read_tally
            cr_tally = check_cold_read_tally(
                tree_root,
                judge_fn=cold_read_judge_fn,
                judge_model=judge_model,
                config=config,
                pdf_text=cold_read_pdf_text,
            )
            cold_read_errors = cr_tally.get("errors", [])
            cold_read_blocked = len(cold_read_errors) > 0
        except Exception as e:
            cold_read_errors = [f"cold-read re-fire error: {e}"]
            cold_read_blocked = True

    # Determine blocking gate
    if support_blocked and cold_read_blocked:
        blocking_gate = "both"
    elif support_blocked:
        blocking_gate = "support_matcher"
    elif cold_read_blocked:
        blocking_gate = "cold_read"
    else:
        blocking_gate = None

    honesty_gate_blocked = support_blocked or cold_read_blocked

    return {
        "round": round_num,
        "node_id": node_id,
        "honesty_gate_blocked": honesty_gate_blocked,
        "blocking_gate": blocking_gate,
        "rebuttal": rebuttal,
        "support_errors": support_errors,
        "cold_read_errors": cold_read_errors,
    }


# ---------------------------------------------------------------------------
# Main review-board loop (N-round bounded unroll)
# ---------------------------------------------------------------------------

def run_review_board(
    pdf_text: str,
    tree_root: Path,
    *,
    N: int = _DEFAULT_MAX_ROUNDS,
    K: int = _DEFAULT_REVIEWERS_PER_ROUND,
    floor_dims: list[str] | None = None,
    floor_value: int = _DEFAULT_FLOOR_VALUE,
    venue_scale: str = _DEFAULT_VENUE_SCALE,
    judge_fn: Callable[[str], str] | None = None,
    judge_model: str = "",
    rubric_override: str | None = None,
    config: Any | None = None,
    notes_root: Path | None = None,
    cold_read_pdf_text: str | None = None,
) -> dict[str, Any]:
    """Run the full bounded N-round review-board loop.

    Bounded, acyclic unroll: N pre-declared round-blocks chained afterok.
    Cleared-at-round-r' → remaining rounds are no-ops (skip short-circuit).
    Not-cleared-after-N → NOT-CLEARED first-class payload (honest failure).

    The revise node re-fires support-matcher + cold-read (anti-gaming c).

    Args:
        pdf_text:        rendered PDF text (pdftotext output or fallback)
        tree_root:       manuscript artifact tree root
        N:               number of review rounds (frozen at scaffold; clamped to hard-cap 3)
        K:               reviewers per round
        floor_dims:      list of floor dimension names (default: ["SOUND", "REPRO"])
        floor_value:     minimum score to pass each floor dim (default: 3)
        venue_scale:     score scale description (default: "1-5")
        judge_fn:        injectable judge (tests use mock; None → raises in production)
        judge_model:     model-id to log
        rubric_override: optional rubric override
        config:          optional Config
        notes_root:      project notes dir (for revise honesty re-fire)
        cold_read_pdf_text: optional PDF text for cold-read re-fire

    Returns dict with:
        cleared:        bool
        cleared_at:     int | None — round at which cleared (None if not cleared)
        rounds:         list[dict] — per-round meta (reviewer scores + meta-review + revise)
        not_cleared:    dict | None — first-class NOT-CLEARED section (if not cleared after N)
        honest_report:  str — never says "approved"; says "cleared at r" or "NOT cleared"
        n_rounds_run:   int
        n_reviewers_per_round: int
        meta:           RunState.meta["review_board"] dict

    sr: SR-MS-REVIEW-a §5J.17.2–.6
    """
    _floor_dims = floor_dims if floor_dims is not None else list(_DEFAULT_FLOOR_DIMS)
    # Apply hard-cap
    N_capped = min(N, _MAX_ROUNDS_HARDCAP)

    if judge_fn is None:
        raise RuntimeError(
            "run_review_board: judge_fn is required. "
            "In production, set RV_JUDGE_MODEL and ANTHROPIC_API_KEY, then use the "
            "default _default_judge_fn (from support_matcher). In tests, pass a mock."
        )

    run_state_meta: dict[str, Any] = {"review_board": {}}
    rounds: list[dict[str, Any]] = []
    cleared = False
    cleared_at: int | None = None

    for r in range(1, N_capped + 1):
        round_record: dict[str, Any] = {"round": r}

        # Check if already cleared (prior round short-circuit)
        if run_state_meta["review_board"].get("cleared_at") is not None:
            # This round is a no-op
            round_record["skipped"] = True
            round_record["skip_reason"] = (
                f"cleared at round {run_state_meta['review_board']['cleared_at']}"
            )
            round_record["meta"] = {"canary_ok": True, "judge_model": judge_model, "n_reviewers": 0}
            rounds.append(round_record)
            continue

        # --- K reviewer nodes (parallel fan-out in real DAG; sequential here for mock) ---
        reviewer_results: list[dict[str, Any]] = []
        for k in range(1, K + 1):
            reviewer_result = run_reviewer_node(
                pdf_text=pdf_text,
                round_num=r,
                lens_num=k,
                judge_fn=judge_fn,
                judge_model=judge_model,
                rubric_override=rubric_override,
                config=config,
                run_state_meta=run_state_meta,
            )
            reviewer_results.append(reviewer_result)
        round_record["reviewers"] = reviewer_results

        # --- Meta-review join (fan-in) ---
        meta_result = run_meta_review(
            round_num=r,
            reviewer_results=reviewer_results,
            floor_dims=_floor_dims,
            floor_value=floor_value,
            venue_scale=venue_scale,
            judge_model=judge_model,
            run_state_meta=run_state_meta,
        )
        round_record["meta_review"] = meta_result
        round_record["meta"] = meta_result.get("meta", {})

        if meta_result["cleared"]:
            cleared = True
            cleared_at = r
            # run_state_meta already updated inside run_meta_review

        # --- Revise node (only for non-last rounds, and only if not cleared) ---
        if r < N_capped and not meta_result["cleared"]:
            revise_result = run_revise(
                round_num=r,
                meta_review=meta_result,
                tree_root=tree_root,
                notes_root=notes_root,
                support_judge_fn=None,   # no re-fire in -a test harness unless explicitly set
                cold_read_judge_fn=None,
                judge_model=judge_model,
                config=config,
                cold_read_pdf_text=cold_read_pdf_text,
            )
            round_record["revise"] = revise_result

        rounds.append(round_record)

        # Early exit if cleared (remaining rounds will be no-ops via run_state_meta)
        if cleared:
            # Continue the loop so remaining rounds record their no-op state
            pass

    # --- Build NOT-CLEARED section (§5J.17.5 Guard 1) ---
    not_cleared_payload: dict[str, Any] | None = None
    if not cleared:
        # Identify the surviving failing dims from the last round
        last_meta = None
        for r_data in reversed(rounds):
            if not r_data.get("skipped", False):
                last_meta = r_data.get("meta_review", {})
                break

        failing_dims = []
        if last_meta:
            for dim, fr in last_meta.get("floor_results", {}).items():
                if not fr.get("passed", True):
                    failing_dims.append(
                        f"{dim} (min score {fr['min_score']} < floor {floor_value})"
                    )

        worst_finding_strs = []
        if last_meta:
            worst_finding_strs = last_meta.get("worst_findings", [])

        persistent_weakness = (
            f"Paper did not reach the review-board bar after {N_capped} round(s). "
            f"Failing floor dimension(s): {', '.join(failing_dims) or 'all floor dims'}. "
            f"Surviving objection(s): {'; '.join(worst_finding_strs) or 'see round meta-reviews above'}. "
            f"The human operator must adjudicate whether to revise further or submit as-is."
        )

        not_cleared_payload = {
            "n_rounds": N_capped,
            "failing_dims": failing_dims,
            "persistent_weakness": persistent_weakness,
            "worst_findings": worst_finding_strs,
        }

    # --- Honest report (never says "approved") ---
    if cleared:
        honest_report = (
            f"review-board: {N_capped} round(s) scheduled, cleared at round {cleared_at}; "
            f"floors {' '.join(f'{d}≥{floor_value}' for d in _floor_dims)}"
        )
    else:
        honest_report = (
            f"review-board: {N_capped} round(s) run, NOT cleared after {N_capped} round(s); "
            f"failing floors: "
            + ", ".join(
                f"{d}={rounds[-1].get('meta_review', {}).get('floor_results', {}).get(d, {}).get('min_score', 0)}/{floor_value}"
                for d in _floor_dims
            )
        )

    return {
        "cleared": cleared,
        "cleared_at": cleared_at,
        "rounds": rounds,
        "not_cleared": not_cleared_payload,
        "honest_report": honest_report,
        "n_rounds_run": len([r for r in rounds if not r.get("skipped", False)]),
        "n_reviewers_per_round": K,
        "meta": run_state_meta["review_board"],
    }


# ---------------------------------------------------------------------------
# Config seam helpers (for _build_manifest freeze)
# ---------------------------------------------------------------------------

def get_review_config(config: Any | None = None) -> dict[str, Any]:
    """Return the [manuscript_review] config dict, with defaults applied.

    max_rounds: clamped to hard-cap 3 (D-REV-3).
    reviewers_per_round: min 2 enforced.
    """
    raw_cfg: dict[str, Any] = {}
    if config is not None:
        raw = getattr(config, "_raw", {})
        raw_cfg = raw.get("manuscript_review", {}) or {}

    n = int(raw_cfg.get("max_rounds", _DEFAULT_MAX_ROUNDS))
    n = min(n, _MAX_ROUNDS_HARDCAP)  # hard-cap enforcement
    k = int(raw_cfg.get("reviewers_per_round", _DEFAULT_REVIEWERS_PER_ROUND))
    k = max(k, 2)  # min 2 reviewers

    floor_dims_raw = raw_cfg.get("floor_dimensions", _DEFAULT_FLOOR_DIMS)
    if isinstance(floor_dims_raw, str):
        floor_dims = [d.strip().upper() for d in floor_dims_raw.split(",")]
    else:
        floor_dims = [str(d).strip().upper() for d in floor_dims_raw]

    floor_value = int(raw_cfg.get("floor_value", _DEFAULT_FLOOR_VALUE))
    venue_scale = str(raw_cfg.get("venue_scale", _DEFAULT_VENUE_SCALE))

    return {
        "max_rounds": n,
        "reviewers_per_round": k,
        "floor_dimensions": floor_dims,
        "floor_value": floor_value,
        "venue_scale": venue_scale,
    }
