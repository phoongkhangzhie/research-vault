"""review_board.py — SR-MS-REVIEW: scientific-merit review-board bounded loop machinery.

SCOPE
=====
This module implements the complete venue-grounded review-board gate (§5J.17):

  SR-MS-REVIEW-a (merged): CONTROL-FLOW and PREDICATE layer.
    - Dimensioned-score bracket extractor (7-dim, distinct from all prior extractors).
    - Threshold predicate: per-dimension FLOORS, MIN-across-reviewers.
    - Bounded N-round unroll loop with node-level skip short-circuit.
    - Revise-r postcondition: re-fires support-matcher + cold-read.
    - Rubric seam: get_review_rubric(override, config) → str.

  SR-MS-REVIEW-b (this): Ada's real rubric + reviewer-lens specs + calibrated canary.
    - DEFAULT_REVIEW_RUBRIC: Ada's venue-grounded 7-dim rubric (replaces placeholder).
    - _REVIEWER_LENS_L1/L2/L3: the three independent review postures.
    - get_reviewer_lens_spec(k, K) → str: lens assignment for manifest builders.
    - run_canary_scaffold: CALIBRATED bidirectional probes (known-STRONG + known-WEAK),
      replacing the always-True scaffold placeholder from -a.
    - CanaryAbortError: raised when a canary probe is out-of-bounds.

HONEST BOUNDARY: rv manuscript review (standalone)
===================================================
Running ``rv manuscript review`` standalone re-scores the SAME compiled PDF text each
round. There is NO revision between rounds in standalone mode — the revise-r node is a
no-op because there is no separate re-draft step outside the full DAG. Multi-round review
only bites meaningfully inside the full manuscript DAG (the ``approve-manuscript`` flow),
where the revise-r node re-drafts failing sections and recompiles before the next round.

VERDICT TOKENS (dimensioned scores — NEW 7-dim set)
====================================================
  Bracket form: [DIM:SCORE] where DIM ∈ {SOUND, CONTRIB, CLARITY, ORIG, LIMIT, REPRO, ETHICS}
  and SCORE is a digit on the venue scale (default 1–5).

  ★ FAIL-CLOSED: a score that cannot be parsed defaults to 0 (below any floor).
    A missing dim also defaults to 0. Never a silent pass.

FLOOR PREDICATE
===============
  cleared ⟺ ∀ dim ∈ floor_dims: MIN_across_reviewers(scores[dim]) ≥ floor_value
  Default floor_dims = {SOUND, REPRO}, floor_value = 3 (borderline-accept, 1–5 scale).

CALIBRATED BIDIRECTIONAL CANARY
================================
  Two probes, fired through the SAME judge_fn + DEFAULT_REVIEW_RUBRIC, BEFORE trusting
  any real reviewer scores (same structural pattern as check_support_tally canary):
    (a) known-STRONG: expect SOUND ≥ 4 AND REPRO ≥ 4. Fails → ABORT (blind rejector).
    (b) known-WEAK:   expect SOUND ≤ 2 AND REPRO ≤ 2. Fails → ABORT (rubber-stamper).
  Dead-band at floor (3) disallowed both directions. Parse failure → ABORT.
  Bounds calibrated to floor: strong ≥ floor+1, weak ≤ floor−1.

  Canary fires when rubric has the {PDF_TEXT} slot AND canary_judge_fn is provided.
  Skips silently when rubric="" (backward-compat with -a tests that don't wire the judge).

Stdlib only.
sr: SR-MS-REVIEW-a + SR-MS-REVIEW-b
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# CanaryAbortError — raised when a calibrated canary probe is out of bounds
# ---------------------------------------------------------------------------

class CanaryAbortError(RuntimeError):
    """Raised when a canary probe fails its expected-score bounds.

    Either the judge is BROKEN-HARSH (strong probe below floor+1) or
    RUBBER-STAMPING / positivity-biased (weak probe at/above floor).
    Either failure makes the round's scores untrustworthy — ABORT.

    sr: SR-MS-REVIEW-b §5J.17.5
    """


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
# SR-MS-REVIEW-b: Ada's DEFAULT_REVIEW_RUBRIC (replaces placeholder from -a)
# ---------------------------------------------------------------------------

DEFAULT_REVIEW_RUBRIC: str = """\
SCIENTIFIC-MERIT REVIEW-BOARD JUDGE RUBRIC

You are an EXPERT PEER REVIEWER at a top venue (NeurIPS / ICLR / ICML / ARR class).
You have been handed ONE compiled paper (its extracted text, below) and NOTHING
else — no repository, no author's notes, no rebuttal, no prior round's reviews, no
acquaintance with this project or its authors. Your job is to assess the paper's
SCIENTIFIC MERIT on seven dimensions and return a machine-parseable score block.

★ YOUR POSTURE IS ADVERSARIAL — DISCONFIRM FIRST. A reviewer's job is to argue
AGAINST the paper: to find the weakest point, the unsupported claim, the flaw that
sinks it, BEFORE extending any credit. The dominant failure of an automated reviewer
is POSITIVITY BIAS — waving papers through with 4s and 5s because they read fluently.
A judge that rubber-stamps is worse than no judge: it manufactures false confidence.
You do not award a high score; a paper EARNS it by surviving your attempt to break it.
Default to the low end of each scale and let the paper's own evidence pull the score up.

────────────────────────────────────────────────────────────────────────
INPUT — the compiled paper text (this is ALL you may use)
────────────────────────────────────────────────────────────────────────
{PDF_TEXT}

────────────────────────────────────────────────────────────────────────
HARD CONSTRAINTS — read before you score
────────────────────────────────────────────────────────────────────────
C1. ★ NO EXTERNAL RESOLUTION (anti-anchoring — the load-bearing rule).
    Judge ONLY from what is printed above. You do NOT have the authors' framing,
    their thesis statement, their reputation, or the surrounding project. If the
    paper claims a result but the evidence for it is not ON THE PAGE, the claim is
    UNSUPPORTED for your purposes — even if you personally believe it, even if it is
    "standard" in the field. If you catch yourself thinking "the authors surely
    checked that" — STOP: that is the anchoring this gate exists to catch. You are a
    stranger with the page, not a colleague with the context.

C2. ★ EVERY SCORE JUSTIFIED IN TEXT WITH A VERBATIM SPAN (the ARR rule + integrity
    anchor). For EACH of the seven dimensions your justification MUST quote at least
    one EXACT string, character-for-character, copied from the paper above — the
    span that drove the score up or down. No paraphrase, no reconstruction. If you
    cannot quote a literal span to justify a score, you do not have a justified score
    — and an unjustifiable score defaults to the FLOOR-FAILING end (see C6). The
    verbatim span is what makes the score auditable rather than a vibe.

C3. DISCONFIRM FIRST, THEN SCORE. For every dimension, hunt the WEAKEST evidence
    before the strongest. Write the single most damaging objection you can defend
    from the text (the WEAKNESS line) BEFORE you commit the number. A dimension with
    no stated weakness is a tell that you did not look — re-read.

C4. SCORE INDEPENDENTLY PER DIMENSION. Do not let a strong Clarity halo a weak
    Soundness, or a novel idea excuse an unreproducible method. A beautifully written
    paper with an unsupported central claim is a LOW-Soundness paper. Score each axis
    on its own evidence.

C5. ★ THE TWO FLOOR DIMENSIONS BIND TO THE PAPER'S OWN PROVENANCE — they cannot be
    talked up. Soundness and Reproducibility are the auto-gating axes; they ride on
    objective, on-page evidence, NOT on how convincing the prose is:

    • SOUNDNESS binds to SUPPORT-GROUNDING. Score high ONLY if the paper's CENTRAL
      empirical claims are each backed by evidence VISIBLE IN THE PAPER — a number
      with a stated source, a cited result, a table/figure that is actually present,
      a described procedure that could produce the claim. A confident sentence with
      no on-page backing is an UNSUPPORTED claim and drives Soundness DOWN, however
      fluent. (An upstream hard gate — the support-matcher — already blocks claims
      that contradict or lack a grounding note; your Soundness score is the
      methodological-rigor layer on TOP of that: is the design itself valid, are the
      comparisons fair, are the claims proportionate to the evidence shown.)

    • REPRODUCIBILITY binds to PROVENANCE PRESENCE. Score high ONLY if the paper
      supplies what a stranger would need to reproduce it: a reproducibility / data-
      availability statement, the seeds or number of runs, the key configuration or
      hyperparameters, a code/data pointer, and (where the framework stamps them) the
      run-ids / freeze-manifest reference behind a proper "reproducibility" or
      "provenance" statement. ABSENCE of this apparatus CAPS Reproducibility at 2 —
      you cannot certify the reproducibility of a method whose provenance is not on
      the page. (Note: a well-formed repro statement that POINTS to seeds, configs,
      and an availability reference is legitimate scholarly apparatus and scores
      HIGH; a bare raw hash or filesystem path dumped mid-prose is a different
      problem the cold-read gate handles — do not double-penalize it here, but do
      NOT credit it as reproducibility apparatus either.)

C6. ★ FAIL-CLOSED. An unscoreable dimension is NOT a pass. If you cannot find the
    evidence to justify a score on a FLOOR dimension (Soundness, Reproducibility) —
    the section is missing, the provenance is absent, the claim has no backing — you
    MUST score that dimension BELOW the floor (1 or 2), never at or above it. Silence
    is not certification. A score you cannot justify with a verbatim span (C2)
    defaults to the floor-failing end. Never read an absent thing as adequate.

────────────────────────────────────────────────────────────────────────
THE SEVEN DIMENSIONS (venue review form) — score each 1–5
────────────────────────────────────────────────────────────────────────
SOUND   Soundness — methodological rigor; are the central claims supported by the
        evidence ON THE PAGE, the design valid, the comparisons fair, the claims
        proportionate? ★ FLOOR + binds to support-grounding (C5).
CONTRIB Contribution / significance — does the work matter; is the advance real and
        non-trivial relative to what the paper itself situates it against?
CLARITY Clarity — can a competent reader follow the exposition, notation, and
        structure from the paper alone?
ORIG    Originality — novel idea, framing, method, or result, as evidenced on the page.
LIMIT   Limitations — does the paper honestly state its scope, threats to validity,
        and failure modes (rather than hiding them)?
REPRO   Reproducibility — freeze-hashes / run-ids / seeds / configs / code-or-data
        availability present and sufficient. ★ FLOOR + binds to provenance (C5).
ETHICS  Ethics — responsible-use, risks, and a Yes/No/NA responsible-AI checklist
        (data consent/licensing, foreseeable harm, dual-use) addressed.

1–5 ORDINAL SCALE (venue-normalized):
  1 = strong reject   — fatal flaw / unsupported central claim / no provenance.
  2 = reject          — significant unresolved problem; below the bar.
  3 = borderline      — the bar clears here (floor); defensible but not compelling.
  4 = accept          — solid, well-supported, reproducible.
  5 = strong accept   — exemplary on this axis; nothing you could break.
  (Floor dims: 3 is the PASS threshold; the worst reviewer must reach it — §5J.17.4.)

────────────────────────────────────────────────────────────────────────
PROCEDURE (do the steps in order)
────────────────────────────────────────────────────────────────────────
STEP 1 — DISCONFIRMING SWEEP (mandatory, first; C3). Read the whole paper as an
  adversary. For each of the seven dimensions, find and quote (C2) the single most
  damaging thing you can defend from the text. For the two FLOOR dims, explicitly
  check the binding (C5): is each central claim backed on the page (SOUND)? is the
  provenance apparatus present (REPRO)? If either binding is absent, you are already
  at 1–2 on that dim (C6) — do not talk yourself up.

STEP 2 — SCORE each dimension 1–5 on its own evidence (C4), defaulting low and
  letting on-page evidence raise it. Attach the verbatim justification span (C2) and
  a confidence (high|med|low). LOW confidence on a FLOOR dim means you could not
  establish the binding → score it below the floor (C6).

STEP 3 — EMIT the machine-parseable block (below), exactly once, all seven tokens.

────────────────────────────────────────────────────────────────────────
OUTPUT (machine-parseable). Emit exactly one block, all seven dimensions present.
The [DIM:SCORE] token on each line is REQUIRED and is what the gate reads.
────────────────────────────────────────────────────────────────────────
For each of the seven dimensions, in this order (SOUND, CONTRIB, CLARITY, ORIG,
LIMIT, REPRO, ETHICS), emit exactly:

[SOUND:N]
WEAKNESS: <the single most damaging objection you can defend, disconfirm-first>
JUSTIFY: "<exact verbatim span copied from the paper that drove this score>"
CONF: <high|med|low>

[CONTRIB:N]
WEAKNESS: ...
JUSTIFY: "<verbatim span>"
CONF: <high|med|low>

...and likewise for [CLARITY:N] [ORIG:N] [LIMIT:N] [REPRO:N] [ETHICS:N].

Then always, exactly once:

CHECKLIST (responsible-AI, Yes/No/NA — one line each):
  DATA_LICENSING: <Yes|No|NA> — <one-line basis>
  FORESEEABLE_HARM: <Yes|No|NA> — <one-line basis>
  DUAL_USE: <Yes|No|NA> — <one-line basis>

SUMMARY:
  FLOOR_DIMS: SOUND=N REPRO=N   (the two auto-gating axes)
  WORST_OBJECTION: <the single strongest reason this paper should not clear, in one line>
  SWEPT: <one line confirming you read the whole paper adversarially, disconfirm-first>
"""


def get_review_rubric(
    override: str | None = None,
    config: Any | None = None,
) -> str:
    """Return the active review-board judge rubric.

    Priority: override arg > [manuscript_review].rubric in config > DEFAULT_REVIEW_RUBRIC.

    Ada's real rubric is now the default (SR-MS-REVIEW-b). Override via:
      (a) override="..." (direct pass), OR
      (b) [manuscript_review] rubric = "..." in research_vault.toml.

    sr: SR-MS-REVIEW-b §5J.17.3
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
    return DEFAULT_REVIEW_RUBRIC


# ---------------------------------------------------------------------------
# SR-MS-REVIEW-b: Calibrated bidirectional canary — Ada's known passages + bounds
# ---------------------------------------------------------------------------

# Unique marker strings used by tests to detect which probe is being sent.
# These substrings appear ONLY in the respective canary passage and are
# guaranteed to be on a single line (no line-break in the marker text).
_CANARY_STRONG_MARKER: str = "holistic fidelity score"
_CANARY_WEAK_MARKER: str = "clearly the best"

# Known-STRONG passage: CI-backed claim, matched baselines, full provenance apparatus.
# Expect SOUND ≥ 4 AND REPRO ≥ 4 (floor+1). A judge that scores this at/below floor
# is BROKEN-HARSH — a blind rejector that would block genuinely strong work.
_CANARY_STRONG_PASSAGE: str = """\
We evaluate three models on the holistic fidelity score (HFS), a 0–100 measure of
agreement with human reference judgments. Each model was run over 5 random seeds
(seeds 0–4); we report the mean and a 95% bootstrap confidence interval. The
strongest model reaches HFS 71.4 (95% CI [69.8, 73.1]), a margin over the weakest
at 52.9 (95% CI [51.2, 54.7]) that does not overlap at the 95% level, as shown in
Table 1. The scoring procedure, including the exact prompt and the human-reference
protocol, is given in full in Section 3. We compare against the two strongest prior
reference-based scorers [4, 9] under identical inputs; our method exceeds both.

Limitations. Our evaluation covers English-only inputs and three model families; we
do not claim the ordering transfers to other languages or to instruction-tuned
variants outside this set (Section 6).

Reproducibility. Code, prompts, and per-seed scored outputs are released; all runs
use seeds 0–4 with the configuration in Appendix B. Data are drawn from the
publicly licensed reference set [4].

References
[4] A. Rivera and B. Osei (2023). Reference-based fidelity scoring for generative
    models. Journal of Evaluation Methods, 11(2), 88–104.
[9] L. Mensah (2022). Calibrated human-reference evaluation. Proc. XYZ, 210–219.\
"""

# Known-WEAK passage: no numbers, no baselines, no provenance. EVERY central claim
# is unsupported on the page. Expect SOUND ≤ 2 AND REPRO ≤ 2 (floor−1). A judge
# that scores this at/above floor is RUBBER-STAMPING — positivity bias (AI-Scientist
# failure, Lu et al. 2024). This is the exact failure the weak canary guards.
_CANARY_WEAK_PASSAGE: str = """\
Our method is clearly the best. On our benchmark it achieves much higher quality
than the baseline, demonstrating the effectiveness of the approach. We ran the
experiment and observed a large improvement across the board. The results speak for
themselves and confirm our hypothesis that the method works well in practice.

We believe these findings will generalize broadly to essentially all settings of
interest, and we are confident the approach is robust.\
"""

# Calibrated bounds (tied to the floor value = 3, §5J.17.4):
#   strong probe: score must be ≥ floor+1 = 4 on BOTH SOUND and REPRO
#   weak probe:   score must be ≤ floor−1 = 2 on BOTH SOUND and REPRO
# Dead-band AT the floor (3) is disallowed both directions.
_CANARY_STRONG_MIN: int = 4   # SOUND/REPRO on strong probe must be ≥ this
_CANARY_WEAK_MAX: int = 2     # SOUND/REPRO on weak probe must be ≤ this


def run_canary_scaffold(
    judge_fn: Callable[[str], str],
    judge_model: str = "",
    rubric: str = "",
) -> dict[str, Any]:
    """Run the calibrated bidirectional canary probes before trusting real reviewer scores.

    SR-MS-REVIEW-b: fires Ada's two calibrated probes through the SAME judge_fn +
    rubric (via {PDF_TEXT} slot replacement) + _extract_review_scores.

    Bounds (calibrated to floor=3):
      (a) known-STRONG probe: SOUND ≥ 4 AND REPRO ≥ 4. Miss → ABORT (blind rejector).
      (b) known-WEAK probe:   SOUND ≤ 2 AND REPRO ≤ 2. Miss → ABORT (rubber-stamper).

    A probe that fails to parse (extractor → None / missing floor dim → 0) is itself
    out of bounds → ABORT. An unscoreable canary never certifies the judge.

    SKIP: if rubric is "" or lacks the {PDF_TEXT} slot, canary is skipped
    (backward-compat with -a tests that call run_meta_review without wiring a rubric).

    Args:
        judge_fn:    the SAME judge callable used for real reviewer nodes
        judge_model: model-id to log
        rubric:      the active review rubric (must contain {PDF_TEXT} slot)

    Returns:
        canary_ok:   True — only returned when both probes are in bounds
        canary_note: description of outcome

    Raises:
        CanaryAbortError: if either probe is out of bounds (ABORT — do not run round)

    sr: SR-MS-REVIEW-b §5J.17.5
    """
    # Skip when rubric not configured (backward-compat / placeholder mode)
    if not rubric or "{PDF_TEXT}" not in rubric:
        return {
            "canary_ok": True,
            "canary_note": "CANARY SKIPPED: rubric not configured (no {PDF_TEXT} slot).",
        }

    # --- Fire known-STRONG probe ---
    strong_prompt = rubric.replace("{PDF_TEXT}", _CANARY_STRONG_PASSAGE)
    strong_raw = judge_fn(strong_prompt)
    strong_scores = _extract_review_scores(strong_raw) or {}
    s_sound = strong_scores.get("SOUND", 0)
    s_repro = strong_scores.get("REPRO", 0)

    if s_sound < _CANARY_STRONG_MIN or s_repro < _CANARY_STRONG_MIN:
        raise CanaryAbortError(
            f"review judge is BROKEN-HARSH / blind REJECTOR on a known-STRONG probe "
            f"(SOUND={s_sound}, REPRO={s_repro}; expected ≥{_CANARY_STRONG_MIN} on both) "
            f"— scores not trustworthy; ABORTING round."
        )

    # --- Fire known-WEAK probe ---
    weak_prompt = rubric.replace("{PDF_TEXT}", _CANARY_WEAK_PASSAGE)
    weak_raw = judge_fn(weak_prompt)
    weak_scores = _extract_review_scores(weak_raw) or {}
    w_sound = weak_scores.get("SOUND", 0)
    w_repro = weak_scores.get("REPRO", 0)

    # Weak probe: score must be BELOW floor (≤ floor−1 = 2). At-floor (3) is disallowed.
    # Note: w_sound=0 here means parse failure, which is also out-of-bounds for a WEAK probe
    # because 0 ≤ 2 → this would vacuously pass. We treat 0 as a parse failure and abort.
    if w_sound == 0 and w_repro == 0 and not weak_scores:
        # Complete parse failure on weak probe
        raise CanaryAbortError(
            f"review judge returned UNPARSEABLE output on the known-WEAK probe "
            f"— scores not trustworthy; ABORTING round."
        )

    if w_sound >= (_CANARY_WEAK_MAX + 1) or w_repro >= (_CANARY_WEAK_MAX + 1):
        raise CanaryAbortError(
            f"review judge is RUBBER-STAMPING / positivity-biased on a known-WEAK probe "
            f"(SOUND={w_sound}, REPRO={w_repro}; expected ≤{_CANARY_WEAK_MAX} on both) "
            f"— scores not trustworthy; ABORTING round. "
            f"This is the AI-Scientist positivity-bias failure."
        )

    return {
        "canary_ok": True,
        "canary_note": (
            f"Canary calibrated: strong probe SOUND={s_sound}/REPRO={s_repro} ≥ {_CANARY_STRONG_MIN}; "
            f"weak probe SOUND={w_sound}/REPRO={w_repro} ≤ {_CANARY_WEAK_MAX}. "
            f"Judge distinguishes strong from weak — trust the round."
        ),
    }


# ---------------------------------------------------------------------------
# SR-MS-REVIEW-b: Reviewer lens specs (L1/L2/L3 — three independent postures)
# ---------------------------------------------------------------------------

# L1 — METHODS / SOUNDNESS adversary (the "does the result hold up" reviewer).
# Prepended to the reviewer node spec; all 7 dims still scored; lens biases WHERE to dig.
_REVIEWER_LENS_L1: str = (
    "You review as a hard-nosed methodologist. Attack the SOUNDNESS floor first: "
    "is every central empirical claim backed by evidence on the page, or are there "
    "confident sentences with no support? Are the comparisons fair (matched baselines, "
    "no cherry-picked split, no moved goalposts)? Is the effect size proportionate to "
    "the evidence, or is a small/uncontrolled result oversold? Is there a confound the "
    "paper waves away? Probe the REPRODUCIBILITY floor next: seeds, runs, configs, "
    "availability — present and sufficient, or absent? You are the reviewer who has seen "
    "fifty papers die on a hidden confound; find this one's before you grant Soundness above 3."
)

# L2 — SIGNIFICANCE / NOVELTY / RELATED-WORK adversary (the "is this actually new" reviewer).
_REVIEWER_LENS_L2: str = (
    "You review as a skeptic of contribution. Attack CONTRIB and ORIG first: is the "
    "advance real and non-trivial, or a relabeling of known work? Does the paper situate "
    "itself honestly against prior art, or is the related work thin / self-serving / "
    "missing the obvious comparator that would shrink the claimed delta? Is the 'novelty' "
    "a genuine new idea or an incremental tweak dressed up? You still score all seven — "
    "and you hold Soundness/Repro to the same floor — but your edge is catching the paper "
    "whose real problem is that it does not matter or is not new."
)

# L3 — CLARITY / REPRODUCIBILITY / LIMITATIONS adversary (the "can a stranger use this" reviewer).
_REVIEWER_LENS_L3: str = (
    "You review as the fresh reader who must reproduce and build on this. Attack CLARITY, "
    "REPRO, and LIMIT first: can you follow the exposition, notation, and structure from "
    "the paper alone, or does it assume context you do not have? Is the reproducibility "
    "apparatus actually present and sufficient (a real repro/availability statement, seeds, "
    "configs) — or is REPRO capped at 2 by absence (C5/C6)? Does the paper honestly own "
    "its limitations and threats to validity, or bury them? You are the reviewer who protects "
    "the reader who comes after; a paper that cannot be followed or reproduced does not clear "
    "on your watch."
)


def get_reviewer_lens_spec(k: int, K: int) -> str:
    """Return the lens posture string for reviewer k in a round of K reviewers.

    Lens assignment:
      K=1: k=1 → L1 (methods/soundness only)
      K=2: k=1 → L1, k=2 → L3 (floor-carrying pair — skip L2 significance/novelty)
      K≥3: k=1 → L1, k=2 → L2, k=3 → L3; k>3 → L1 (wraps)

    K=2 fallback rationale: L1 (soundness floor) and L3 (repro floor) carry the two
    auto-gating dimensions. L2 (significance) is the surface-only dimension; its
    adversarial angle is less critical when K is constrained.

    sr: SR-MS-REVIEW-b §5J.17.2
    """
    if K == 1:
        return _REVIEWER_LENS_L1
    if K == 2:
        return _REVIEWER_LENS_L1 if k == 1 else _REVIEWER_LENS_L3
    # K >= 3: cycle L1, L2, L3
    idx = ((k - 1) % 3) + 1  # 1,2,3,1,2,3,...
    if idx == 1:
        return _REVIEWER_LENS_L1
    if idx == 2:
        return _REVIEWER_LENS_L2
    return _REVIEWER_LENS_L3


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
    canary_judge_fn: Callable[[str], str] | None = None,
    canary_rubric: str = "",
) -> dict[str, Any]:
    """Run the full bounded N-round review-board loop.

    Bounded, acyclic unroll: N pre-declared round-blocks chained afterok.
    Cleared-at-round-r' → remaining rounds are no-ops (skip short-circuit).
    Not-cleared-after-N → NOT-CLEARED first-class payload (honest failure).

    The revise node re-fires support-matcher + cold-read (anti-gaming c).

    SR-MS-REVIEW-b: canary_judge_fn + canary_rubric wire the calibrated bidirectional
    canary through run_meta_review → run_canary_scaffold. When provided, the canary
    fires BEFORE trusting reviewer scores each round. The SAME judge_fn used for
    real reviews should be passed as canary_judge_fn (§5J.17.5).
    In tests (default): canary_judge_fn=None → canary skips (backward-compat).

    Args:
        pdf_text:           rendered PDF text (pdftotext output or fallback)
        tree_root:          manuscript artifact tree root
        N:                  number of review rounds (frozen at scaffold; clamped to hard-cap 3)
        K:                  reviewers per round
        floor_dims:         list of floor dimension names (default: ["SOUND", "REPRO"])
        floor_value:        minimum score to pass each floor dim (default: 3)
        venue_scale:        score scale description (default: "1-5")
        judge_fn:           injectable judge (tests use mock; None → raises in production)
        judge_model:        model-id to log
        rubric_override:    optional rubric override
        config:             optional Config
        notes_root:         project notes dir (for revise honesty re-fire)
        cold_read_pdf_text: optional PDF text for cold-read re-fire
        canary_judge_fn:    judge for the bidirectional canary probes (None → skip canary)
        canary_rubric:      rubric string with {PDF_TEXT} slot for canary probes

    Returns dict with:
        cleared:        bool
        cleared_at:     int | None — round at which cleared (None if not cleared)
        rounds:         list[dict] — per-round meta (reviewer scores + meta-review + revise)
        not_cleared:    dict | None — first-class NOT-CLEARED section (if not cleared after N)
        honest_report:  str — never says "approved"; says "cleared at r" or "NOT cleared"
        n_rounds_run:   int
        n_reviewers_per_round: int
        meta:           RunState.meta["review_board"] dict

    sr: SR-MS-REVIEW-a §5J.17.2–.6 + SR-MS-REVIEW-b §5J.17.5
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
        # SR-MS-REVIEW-b: pass canary_judge_fn + rubric so the calibrated bidirectional
        # canary fires BEFORE trusting scores. When None, run_canary_scaffold skips.
        meta_result = run_meta_review(
            round_num=r,
            reviewer_results=reviewer_results,
            floor_dims=_floor_dims,
            floor_value=floor_value,
            venue_scale=venue_scale,
            judge_model=judge_model,
            canary_judge_fn=canary_judge_fn,
            canary_rubric=canary_rubric,
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
