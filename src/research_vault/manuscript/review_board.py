"""manuscript/review_board.py — PR-M5: the bounded-unroll review-revise loop
MACHINERY (2 rounds x 3 conference-style reviewers).

Re-instantiates the removed ``manuscript/review_board.py`` craft (deleted at
SR-RM-FIGMS, ``git show 4fdb9b2^:src/research_vault/manuscript/review_board.py``
— the "SR-MS-REVIEW-a/-b" design-of-record), rebuilt against the NEW lit-review
rubric's 8-dimensioned score set (design §11.1) instead of the old 7-dim
generic venue rubric — same bounded-unroll / floor-not-average / canary /
skip-once-cleared CONTROL-FLOW, new DIMENSIONS.

Design: docs/superpowers/specs/2026-07-07-survey-capability-design.md §9-§11.
Doctrine: data/doctrine/honesty-gates.md, data/doctrine/review-board.md.

SCOPE (the operator's locked decision, carried in the PR-M5 dispatch brief):
  - **2 rounds x 3 fresh independent reviewers per round**, conference-style.
  - **Floor-not-average aggregation** across the 3 reviewers on the FLOOR axes
    (design §11.1: dims 1/2/7 -- SCOPE, REPRO, CITE). MIN-across-3, never mean.
  - **A revise step between rounds** -- redrafts failing sections (an agent
    action in the real DAG; this module records the rebuttal + RE-FIRES the
    gates), regression guard (never accept a round that regresses a floor
    axis vs r-1 -- keep the better draft).
  - **Bounded unroll (N=2, hard-cap 3)** -- N pre-declared round-blocks,
    acyclic, frozen at scaffold.
  - **Honest failure** if it can't clear after round N -- a first-class
    NOT-CLEARED payload (persistent-weakness statement), never a silent pass,
    never an infinite loop.
  - **The 3 reviewer lenses (design §11.2)**: coverage & scope auditor (dims
    1-2) -- framework/taxonomy critic (dim 3, WITH the reframe-escalation
    trigger, §5.1) -- synthesis-vs-enumeration adversary (dims 4-6, 8).
    Reviewers are disconfirm-first and NEVER receive the manuscript's thesis
    (anti-anchoring -- the same discipline as M3's honesty gates): the judge
    prompt carries ONLY the rendered draft text + the rubric + the lens.
  - **Re-fire via ``check_gates.build_approve_payload`` -- NOT duplicated.**
    ``run_revise`` calls the single-sourced assembler (hermetic-.bib,
    equation-fidelity, support-matcher, cold-read, coverage-gate) rather than
    re-implementing any of those checks here.
  - **PR-M8 (this pass): the researcher's calibrated ``DEFAULT_LIT_REVIEW_RUBRIC``**
    (design §11.1, methodology doc §A.2) replaces the PR-M5 mock rubric via
    the SAME override seam (``ms_type.rubric`` / ``[manuscript_review].rubric``)
    -- zero control-flow change, only the rubric TEXT + the canary bounds/
    passages were swapped. See ``DEFAULT_LIT_REVIEW_RUBRIC``,
    ``run_canary_scaffold``'s calibrated probes, and the multi-round
    reframe-escalation recurrence gate (below) for the specifics.

DIMENSIONED-SCORE BRACKET (NEW -- design §11.1's 8 dims; does NOT overload the
support-matcher's 4-verdict extractor, coldread's 3-verdict extractor, or
control.py's [PASS]/[BLOCK] extractor):
  [SCOPE:d] [REPRO:d] [FRAME:d] [SYNTH:d] [COMPARE:d] [GAP:d] [CITE:d] [BIAS:d]
  d is an ordinal 1-5 score (``venue_scale``, default "1-5"). A score that
  cannot be parsed, or a dim entirely missing from the response, defaults to
  0 -- FAIL-CLOSED, never a silent pass.

FLOOR AXES (design §11.1): {SCOPE, REPRO} (the coverage/search-reproducibility
axis, dims 1+2) and {CITE} (the citation-fidelity axis, dim 7). Cleared iff
MIN-across-3-reviewers(score) >= floor_value on EVERY floor dim. FRAME (dim 3)
is SURFACE (D-SV-C -- subjective + gameable, human owns the spine); SYNTH/GAP
(dims 4/6) are SIGNAL (cold-read weak-flags, no autogate); COMPARE/BIAS
(dims 5/8) are SURFACE. SURFACE/SIGNAL dims are scored + justified + shown,
**never autogate** -- only SCOPE/REPRO/CITE bind the clear predicate.

Stdlib only. Hermetic in tests -- judge_fn is always injectable; no live LLM
call is required to exercise this module.
sr: PR-M5 (mirrors the removed SR-MS-REVIEW-a/-b craft)
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# CanaryAbortError -- raised when a canary probe fails its expected-score bounds
# ---------------------------------------------------------------------------

class CanaryAbortError(RuntimeError):
    """Raised when a canary probe is out of bounds -- ABORT the round loudly.

    Either the judge is BROKEN-HARSH (a known-strong survey scored at the
    floor) or RUBBER-STAMPING / positivity-biased (a known-weak artifact
    scored at ceiling), or the judge is BLIND to the #1 survey failure (a
    literal annotated bibliography would clear). Any of the three makes the
    round's scores untrustworthy -- ABORT rather than surface fabricated
    confidence.

    sr: PR-M5 (design §11.3, D-SV-D mandatory)
    """


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Judge model resolved via RV_JUDGE_MODEL env var (D-MS-4: Opus-tier);
# never pinned to a versioned ID in source. Tests always pass judge_fn=.
DEFAULT_JUDGE_MODEL: str = os.environ.get("RV_JUDGE_MODEL", "")

# All 8 review dimensions, in the design §11.1 table's order.
_ALL_DIMS: tuple[str, ...] = (
    "SCOPE", "REPRO", "FRAME", "SYNTH", "COMPARE", "GAP", "CITE", "BIAS",
)

# The two FLOOR axes (design §11.1): citation-fidelity (CITE) and
# coverage/search-reproducibility (SCOPE + REPRO). Config seam accepts either
# the dim codes directly, or the design's literal axis names (expanded below).
_DEFAULT_FLOOR_DIMS: list[str] = ["SCOPE", "REPRO", "CITE"]
_DEFAULT_FLOOR_VALUE: int = 3            # ordinal 1-5 scale; 3 = the bar clears
_DEFAULT_VENUE_SCALE: str = "1-5"
_DEFAULT_MAX_ROUNDS: int = 2             # N -- the operator's locked decision
_MAX_ROUNDS_HARDCAP: int = 3             # never > 3, whatever config asks for
_DEFAULT_REVIEWERS_PER_ROUND: int = 3    # K -- the operator's locked decision
_MIN_REVIEWERS_PER_ROUND: int = 2

# design §9's literal axis-name aliases -> the dim codes they expand to.
_FLOOR_AXIS_ALIASES: dict[str, list[str]] = {
    "citation_fidelity": ["CITE"],
    "coverage_reproducibility": ["SCOPE", "REPRO"],
}


def _normalize_floor_dims(raw: Any) -> list[str]:
    """Expand config floor-dim entries into dim codes.

    Accepts either the design's literal axis names (``citation_fidelity``,
    ``coverage_reproducibility``) or the dim codes directly (``SCOPE`` etc,
    case-insensitive). Unknown entries pass through uppercased -- an
    adopter override with a custom dim name is honored, not silently dropped.
    """
    if isinstance(raw, str):
        items = [d.strip() for d in raw.split(",") if d.strip()]
    else:
        items = [str(d).strip() for d in raw]

    out: list[str] = []
    for item in items:
        alias = _FLOOR_AXIS_ALIASES.get(item.lower())
        if alias is not None:
            out.extend(alias)
        else:
            out.append(item.upper())
    # De-dupe, stable order.
    seen: set[str] = set()
    deduped: list[str] = []
    for d in out:
        if d not in seen:
            seen.add(d)
            deduped.append(d)
    return deduped


# ---------------------------------------------------------------------------
# Dimensioned-score bracket extractor (NEW -- 8-dim, design §11.1)
# ---------------------------------------------------------------------------

_REVIEW_SCORE_RE = re.compile(
    r"\[(SCOPE|REPRO|FRAME|SYNTH|COMPARE|GAP|CITE|BIAS):(\d+)\]",
    re.IGNORECASE,
)


def _extract_review_scores(text: str) -> dict[str, int] | None:
    """Extract dimensioned review scores from a judge response.

    Recognized form: ``[SCOPE:4]``, ``[CITE:2]``, ``[frame:3]`` (case-
    insensitive, brackets required, DIM:SCORE). Missing dims are simply
    absent from the returned dict -- the caller defaults them to 0.

    Returns ``None`` on COMPLETE parse failure (no tokens found at all).

    FAIL-CLOSED: a caller must default an absent/unparseable dim to 0 --
    never treat a missing score as a passing one.

    sr: PR-M5
    """
    scores: dict[str, int] = {}
    for m in _REVIEW_SCORE_RE.finditer(text):
        dim = m.group(1).upper()
        try:
            score = int(m.group(2))
        except (ValueError, IndexError):
            score = 0
        scores[dim] = score
    if not scores:
        return None
    return scores


def _extract_frame_escalation_fields(text: str) -> dict[str, list[str]]:
    """Extract the framework/taxonomy critic's MISFITS/REFRAME_CANDIDATES lines.

    Recognized form (one line each, comma-separated, "none" -> empty list):
      MISFITS: <item>, <item>, ...
      REFRAME_CANDIDATES: <item>, <item>, ...

    Absent lines -> empty lists (never an error; a reviewer whose response
    doesn't carry these lines simply contributes nothing to the escalation
    payload -- surfaced as an honest empty, not fabricated).

    sr: PR-M5 (design §5.1 -- the reframe-the-spine escalation)
    """

    def _parse_line(label: str) -> list[str]:
        m = re.search(rf"^{label}:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
        if not m:
            return []
        raw = m.group(1).strip()
        if raw.lower() in ("none", "n/a", ""):
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]

    return {
        "misfits": _parse_line("MISFITS"),
        "reframe_candidates": _parse_line("REFRAME_CANDIDATES"),
    }


# One-score-per-line form (what ``DEFAULT_LIT_REVIEW_RUBRIC`` instructs the
# judge to emit): ``[DIM:N] <justification text>`` -- the ARR discipline
# (methodology doc §B.1, "every score justified in text"). Distinct from
# ``_REVIEW_SCORE_RE`` above (which tolerates several brackets packed onto one
# line, e.g. test fixtures) -- this one is line-anchored so the trailing text
# after a bracket is unambiguously that dimension's justification.
_REVIEW_SCORE_LINE_RE = re.compile(
    r"^[ \t]*\[(SCOPE|REPRO|FRAME|SYNTH|COMPARE|GAP|CITE|BIAS):(\d+)\][ \t]*[-—:]?[ \t]*(.*)$",
    re.IGNORECASE | re.MULTILINE,
)


def _extract_review_scores_and_justifications(
    text: str,
) -> tuple[dict[str, int] | None, dict[str, str]]:
    """Extract dimensioned scores AND their per-line justification text.

    ARR fail-closed rule (methodology doc §B.1, design §11.1's
    justify-each-score requirement): a dim whose bracket has NO trailing
    justification text on its line is NOT trustworthy as a real reviewer
    judgment -- the caller (``run_reviewer_node``) flags that dim in
    ``missing_justifications`` and ``run_meta_review`` SURFACES it (loudly,
    up to FLOOR-dim lines in ``worst_findings``) rather than silently
    accepting an unjustified number. This is surface-not-zero: the score
    value itself is left untouched (floor thresholds are already mechanically
    backstopped elsewhere, e.g. ``build_approve_payload``); a hard re-gate on
    a line-anchored justification parser would be brittle. This mirrors the
    support-matcher's "can't quote -> ABSENT" discipline in spirit (loud
    distrust of an unjustified claim) but not in mechanism (no zeroing here).

    Returns ``(None, {})`` on complete parse failure (no scored-line tokens
    found at all) -- same contract as ``_extract_review_scores``.

    sr: PR-M8 (design §11.1, methodology doc §A.2 justify-each-score/ARR)
    """
    scores: dict[str, int] = {}
    justifications: dict[str, str] = {}
    for m in _REVIEW_SCORE_LINE_RE.finditer(text):
        dim = m.group(1).upper()
        try:
            score = int(m.group(2))
        except (ValueError, IndexError):
            score = 0
        scores[dim] = score
        justifications[dim] = m.group(3).strip()
    if not scores:
        return None, {}
    return scores, justifications


# ---------------------------------------------------------------------------
# Threshold predicate -- floor-not-average
# ---------------------------------------------------------------------------

def _evaluate_threshold(
    scores_per_reviewer: list[dict[str, int]],
    floor_dims: list[str],
    floor_value: int,
) -> dict[str, Any]:
    """Evaluate the floor predicate across K reviewers.

    Aggregation = MIN-across-reviewers (the worst reviewer gates, never the
    mean -- design §9.1: "NOT reviewers happy overall"). A missing dim in a
    reviewer's score dict defaults to 0 (fail-closed).

    cleared <=> for every dim in floor_dims: min(scores_per_reviewer[dim]) >= floor_value.

    Returns:
      cleared:       bool
      floor_results: {dim: {min_score, floor, passed}}
    """
    if not scores_per_reviewer:
        return {
            "cleared": False,
            "floor_results": {
                dim: {"min_score": 0, "floor": floor_value, "passed": False}
                for dim in floor_dims
            },
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
    return {"cleared": cleared, "floor_results": floor_results}


# ---------------------------------------------------------------------------
# DEFAULT_LIT_REVIEW_RUBRIC -- the researcher's calibrated 8-dim rubric (PR-M8)
# ---------------------------------------------------------------------------

# ★ CALIBRATED (PR-M8, design §11.1, methodology doc §A.2). Replaces PR-M5's
# ``PLACEHOLDER_REVIEW_RUBRIC`` via the SAME override seam (``ms_type.rubric``
# / ``[manuscript_review].rubric``, ``get_review_rubric`` below) -- ZERO
# control-flow change in this module, only the rubric TEXT changed. Distilled
# from five grounded appraisal instruments (AMSTAR-2, ROBIS, PRISMA-2020,
# SANRA, CSUR reviewer criteria) + Nickerson's taxonomy ending-conditions --
# see ``~/research-vault/REFERENCES.md`` for the anchors (already appended,
# PR-M7). The gate-class table matches design §11.1 exactly:
#   FLOOR   {SCOPE, REPRO, CITE}     -- provenance-bound, MIN-across-3, autogates.
#   SIGNAL  {SYNTH, GAP}             -- cold-read weak-flags feed worst-findings;
#                                       never autogate on their own.
#   SURFACE {FRAME, COMPARE, BIAS}   -- human-judgment; scored + justified +
#                                       shown, never autogate. FRAME's escalation
#                                       path (§5.1) is its own human-go route.
DEFAULT_LIT_REVIEW_RUBRIC: str = """\
You are an adversarial peer reviewer for a literature-review survey manuscript
(methodology grounded in AMSTAR-2, ROBIS, PRISMA-2020, SANRA, CSUR reviewer
criteria, and Nickerson's taxonomy method -- see REFERENCES.md). You have been
handed ONLY the compiled draft text below -- NOT the author's thesis, NOT any
prior round's reviews or rebuttal, NOT project context. Base every judgment
SOLELY on what this text shows and claims about itself.

Be DISCONFIRM-FIRST: for each dimension, actively hunt for the weakest
evidence and the strongest counterexample BEFORE crediting anything the draft
claims about its own quality. A survey that reads fluently is not thereby
rigorous -- an AI-generated "annotated bibliography" (a per-paper summary
list with no cross-paper synthesis) is the single most common failure this
review exists to catch, and it can read very smoothly.

────────────────────────────────────────────────────────────────────────
DRAFT TEXT (this is ALL you may use -- no other context exists)
────────────────────────────────────────────────────────────────────────
{PDF_TEXT}
────────────────────────────────────────────────────────────────────────

Score EACH of the eight dimensions below on an ordinal 1-5 scale
(1 = fails outright, 3 = the bar clears, 5 = exemplary). For EVERY
dimension, you MUST justify the score with a specific, located observation
from the draft text (ARR discipline: "every score justified in text" -- a
bare number with no textual justification is not a real review). Point at a
section/paragraph/claim, name the signalling question the dimension fails or
passes, and (when scoring low) state the concrete change that would fix it.

  1. SCOPE (scope & coverage completeness) -- FLOOR. GOOD: boundary stated
     and defended; no seminal/high-degree work in this area is missing;
     mention-only inclusions are justified. WEAK: an unstated scope; a known-
     central paper absent; coverage gerrymandered to fit a narrative.
  2. REPRO (search/selection reproducibility) -- FLOOR. GOOD: a reader could
     re-derive the corpus -- documented inclusion/exclusion criteria, a
     PRISMA-style ledger. WEAK: no protocol; opaque "papers we read";
     selection unauditable.
  3. FRAME (framework/taxonomy soundness) -- SURFACE. GOOD: one dominant
     framework whose axes are ~orthogonal and ~exhaustive; concise, robust,
     comprehensive, extendible, explanatory (Nickerson). WEAK: a chronological
     catalog; ad-hoc overlapping buckets; a framework built by clustering with
     no argued thesis; works that don't fit any branch (orphans).
  4. SYNTH (synthesis, not enumeration) -- SIGNAL. GOOD: theme-first
     paragraphs -- a claim compared across >=2 papers under it. WEAK: one
     paragraph per paper (an annotated bibliography); read-order structure;
     a paragraph citing exactly one source with no comparison.
  5. COMPARE (critical comparison depth) -- SURFACE. GOOD: side-by-side
     comparison on shared axes; states which approach wins where and why;
     surfaces tensions/incompatibilities. WEAK: descriptive-only; methods
     listed without evaluation; no "why they differ" argument.
  6. GAP (gap validity / entailment) -- SIGNAL. GOOD: gaps entailed by the
     framework -- an empty grid cell, an under-served stage, an unresolved
     incompatibility. WEAK: boilerplate "more research is needed"; a gap
     anchored to no specific branch of the framework.
  7. CITE (citation fidelity) -- FLOOR. GOOD: every cited claim traces to a
     source that actually SUBSTANTIATES it (support, not mere existence).
     WEAK: a citation the source doesn't support; an invented "X builds on
     Y"; misattribution. (~16.9% of quotations in the literature are
     inaccurate -- treat this dimension as adversarially as any other.)
  8. BIAS (synthesis integrity / bias) -- SURFACE. GOOD: conflicting evidence
     acknowledged; claims not overstated past what the evidence shows; what's
     missing is named. WEAK: selective citation to fit a predetermined
     narrative; overclaiming; disconfirming work ignored.

Emit EXACTLY one machine-parseable line per dimension, in this order, each
followed immediately by its justification on the SAME line:

[SCOPE:N] <justification>
[REPRO:N] <justification>
[FRAME:N] <justification>
MISFITS: <comma-separated recurring misfits the framework doesn't account for, or 'none'>
REFRAME_CANDIDATES: <comma-separated candidate encapsulating reframes, or 'none'>
[SYNTH:N] <justification>
[COMPARE:N] <justification>
[GAP:N] <justification>
[CITE:N] <justification>
[BIAS:N] <justification>

The MISFITS/REFRAME_CANDIDATES lines are for the framework/taxonomy lens
only -- if you are not reviewing under that lens, or find no incoherence,
emit 'none' for both. A reframe candidate is a PROPOSAL to surface to the
human, never a commitment you are making.
"""


def get_review_rubric(
    override: str | None = None,
    config: Any | None = None,
) -> str:
    """Return the active review-board judge rubric.

    Priority: ``override`` arg (e.g. ``ms_type.rubric``) > ``[manuscript_review]
    rubric`` in config > ``DEFAULT_LIT_REVIEW_RUBRIC`` (the researcher's
    calibrated rubric, PR-M8).

    sr: PR-M5, calibrated PR-M8
    """
    if override is not None and str(override).strip():
        return override
    if config is not None:
        raw = getattr(config, "_raw", {})
        ms_review = raw.get("manuscript_review", {})
        if isinstance(ms_review, dict):
            rubric_cfg = ms_review.get("rubric")
            if isinstance(rubric_cfg, str) and rubric_cfg.strip():
                return rubric_cfg
    return DEFAULT_LIT_REVIEW_RUBRIC


# ---------------------------------------------------------------------------
# Canary scaffold -- calibrated bounds + passages (PR-M8, design §11.3, D-SV-D)
# ---------------------------------------------------------------------------

# Unique single-line markers so tests can detect which probe fired.
_CANARY_STRONG_MARKER: str = "does not overlap at the 95% level"
_CANARY_WEAK_MARKER: str = "clearly the best survey"
_CANARY_ANNOTATED_BIB_MARKER: str = "Paper 1 studied X. Paper 2 studied Y."

# ★ CALIBRATED passages (PR-M8, design §11.3): each written to exercise ONE
# specific rubric dimension's GOOD or WEAK tell (§A.2 of the methodology
# doc), so a correctly-calibrated judge has concrete textual evidence to
# justify every score against, not just a vibe.
_CANARY_STRONG_PASSAGE: str = """\
This survey covers 214 papers retrieved via a documented PRISMA search over
three databases; the search query, inclusion/exclusion criteria, and a full
ledger of included/excluded works are given in Section 2 and Appendix A
(SCOPE/REPRO: a reader could re-derive the corpus). Every claim in Sections
3-5 is attributed to a specific cited work with a verbatim quotation or
paraphrase that substantiates it (CITE: no invented attribution). Per Table 2,
each pairwise comparison of reported effect sizes does not overlap at the 95% level of confidence, and every estimate carries its own interval rather than a single unqualified headline number. The
taxonomy (Figure 1) organizes the corpus into four coherent, mutually
exclusive axes, each populated by more than one paper and each explaining why
its members belong there, and no work is orphaned outside the taxonomy
(FRAME: Nickerson's ending-conditions hold). Section 4 compares the four
leading approaches side-by-side on the same three axes and states which wins
where and why (COMPARE), surfacing a genuine tension between two of them that
neither original paper resolves. Two of the identified gaps are traced to a
specific empty cell in the taxonomy grid (GAP), and Section 5 explicitly
flags three studies whose results conflict with the survey's own synthesis
before explaining why the majority view is still preferred (BIAS).
"""

_CANARY_WEAK_PASSAGE: str = """\
This is clearly the best survey of the field. We read a bunch of papers and
they are all pretty good. The topic is important and many people work on it.
No search protocol is given -- we just read what we found. There is no
taxonomy, just a list. Every claim is stated without a specific citation, and
more research is clearly needed in this area.
"""

# ★ The MANDATORY annotated-bibliography canary (D-SV-D, design §11.3(c)):
# a literal per-paper summary list with NO framework and NO cross-paper
# synthesis -- the #1 survey failure mode this whole capability exists to
# catch. Deliberately well-behaved on the FLOOR axes (each paper's summary IS
# individually well-sourced and the "search" is described) so the probe
# isolates the SYNTH failure specifically, rather than failing for the wrong
# reason. This probe must NOT clear on SYNTH.
_CANARY_ANNOTATED_BIB_PASSAGE: str = """\
This survey retrieved 40 papers via a documented database search (Section 2
gives the query and inclusion criteria), and every summary below cites its
specific source. Paper 1 studied X. Paper 2 studied Y. Paper 3 studied Z.
Paper 4 studied W. Paper 5 proposed a method for A. Paper 6 evaluated B on a
benchmark. Paper 7 extended C. Each of these papers is summarized above, one
paragraph per paper, in the order they were retrieved from the search. No
comparison is drawn between any two papers, no shared axis is used to
organize them, and no claim spans more than one source.
"""

# Bounds calibrated to floor_value (design §11.3):
#   strong probe: SCOPE/REPRO/CITE must be >= floor + 1
#   weak probe:   SCOPE/REPRO/CITE must be <= floor - 1
#   annotated-bib probe: SYNTH (the synthesis-vs-enumeration dim) must be
#     < floor_value -- a probe that would CLEAR (its floor dims pass) AND
#     score SYNTH >= floor_value is exactly the blind-to-enumeration failure
#     this canary exists to catch (the #1 survey failure, AI-Scientist's
#     positivity-bias analog).


def run_canary_scaffold(
    judge_fn: Callable[[str], str],
    rubric: str,
    *,
    floor_dims: list[str] | None = None,
    floor_value: int = _DEFAULT_FLOOR_VALUE,
    judge_model: str = "",
) -> dict[str, Any]:
    """Run the three canary probes before trusting real reviewer scores.

    Fires (a) known-STRONG, (b) known-WEAK, and (c) the ★ MANDATORY literal
    annotated-bibliography probe through the SAME ``judge_fn`` + ``rubric``.
    Any probe out of bounds -> ABORT LOUDLY (``CanaryAbortError``); the
    scores from a round whose canary fails are NOT real (honesty-gates.md §4
    blind-judge-canary discipline, generalized to three probes here).

    SKIP: when ``rubric`` is empty or lacks the ``{PDF_TEXT}`` slot (back-
    compat with callers that haven't wired a judge/rubric yet).

    Args:
        judge_fn:    the SAME judge callable used for real reviewer nodes.
        rubric:      the active rubric (must contain the ``{PDF_TEXT}`` slot).
        floor_dims:  the floor dim codes to bound-check (default the three
                     floor dims: SCOPE, REPRO, CITE).
        floor_value: the floor value the bounds are calibrated against.
        judge_model: model-id to log.

    Returns:
        ``{"canary_ok": True, "canary_note": str, "judge_model": str,
        "prompt_hashes": {"strong": ..., "weak": ..., "annotated_bib": ...}}``
        -- only when all three probes are in bounds. ``prompt_hashes`` are
        sha256[:16] of the exact prompt sent for each probe (audit + drift
        detection, PR-M8).

    Raises:
        CanaryAbortError: if any probe is out of bounds.

    sr: PR-M5 (design §11.3, D-SV-D mandatory); calibrated PR-M8
    """
    if not rubric or "{PDF_TEXT}" not in rubric:
        return {
            "canary_ok": True,
            "canary_note": "CANARY SKIPPED: rubric not configured (no {PDF_TEXT} slot).",
            "judge_model": judge_model,
            "prompt_hashes": {},
        }

    _floor_dims = floor_dims if floor_dims is not None else list(_DEFAULT_FLOOR_DIMS)
    strong_min = floor_value + 1
    weak_max = floor_value - 1

    def _hash(prompt: str) -> str:
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]

    # --- (a) known-STRONG probe: every floor dim must be >= floor + 1 -------
    strong_prompt = rubric.replace("{PDF_TEXT}", _CANARY_STRONG_PASSAGE)
    strong_hash = _hash(strong_prompt)
    strong_raw = judge_fn(strong_prompt)
    strong_scores = _extract_review_scores(strong_raw) or {}
    strong_fail = [d for d in _floor_dims if strong_scores.get(d, 0) < strong_min]
    if strong_fail:
        raise CanaryAbortError(
            f"review-board canary: judge is BROKEN-HARSH / blind REJECTOR on a "
            f"known-STRONG survey probe (dims below {strong_min}: "
            f"{', '.join(f'{d}={strong_scores.get(d, 0)}' for d in strong_fail)}) "
            f"-- scores not trustworthy; ABORTING round. "
            f"[judge_model={judge_model or 'unset'} prompt_hash={strong_hash}]"
        )

    # --- (b) known-WEAK probe: every floor dim must be <= floor - 1 ---------
    weak_prompt = rubric.replace("{PDF_TEXT}", _CANARY_WEAK_PASSAGE)
    weak_hash = _hash(weak_prompt)
    weak_raw = judge_fn(weak_prompt)
    weak_scores = _extract_review_scores(weak_raw)
    if weak_scores is None:
        raise CanaryAbortError(
            "review-board canary: judge returned UNPARSEABLE output on the "
            "known-WEAK probe -- scores not trustworthy; ABORTING round. "
            f"[judge_model={judge_model or 'unset'} prompt_hash={weak_hash}]"
        )
    weak_fail = [d for d in _floor_dims if weak_scores.get(d, 0) > weak_max]
    if weak_fail:
        raise CanaryAbortError(
            f"review-board canary: judge is RUBBER-STAMPING / positivity-biased on "
            f"a known-WEAK artifact probe (dims above {weak_max}: "
            f"{', '.join(f'{d}={weak_scores.get(d, 0)}' for d in weak_fail)}) "
            f"-- scores not trustworthy; ABORTING round. "
            f"[judge_model={judge_model or 'unset'} prompt_hash={weak_hash}]"
        )

    # --- (c) ★ MANDATORY annotated-bibliography probe: SYNTH must NOT clear
    ab_prompt = rubric.replace("{PDF_TEXT}", _CANARY_ANNOTATED_BIB_PASSAGE)
    ab_hash = _hash(ab_prompt)
    ab_raw = judge_fn(ab_prompt)
    ab_scores = _extract_review_scores(ab_raw)
    if ab_scores is None:
        raise CanaryAbortError(
            "review-board canary: judge returned UNPARSEABLE output on the "
            "annotated-bibliography probe -- scores not trustworthy; ABORTING round. "
            f"[judge_model={judge_model or 'unset'} prompt_hash={ab_hash}]"
        )
    ab_synth = ab_scores.get("SYNTH", 0)
    if ab_synth >= floor_value:
        raise CanaryAbortError(
            f"review-board canary: judge is BLIND to the #1 survey failure -- a "
            f"literal, per-paper annotated bibliography with NO cross-paper "
            f"synthesis scored SYNTH={ab_synth} (>= floor {floor_value}); it must "
            f"NOT clear (design §11.3(c), D-SV-D mandatory). ABORTING round. "
            f"[judge_model={judge_model or 'unset'} prompt_hash={ab_hash}]"
        )

    return {
        "canary_ok": True,
        "canary_note": (
            f"Canary calibrated: strong probe floor dims >= {strong_min}; "
            f"weak probe floor dims <= {weak_max}; annotated-bib probe SYNTH="
            f"{ab_synth} < {floor_value} (does not clear). "
            f"Judge distinguishes strong/weak/enumeration -- trust the round. "
            f"[judge_model={judge_model or 'unset'}]"
        ),
        "judge_model": judge_model,
        "prompt_hashes": {
            "strong": strong_hash,
            "weak": weak_hash,
            "annotated_bib": ab_hash,
        },
    }


# ---------------------------------------------------------------------------
# The three reviewer lenses (design §11.2)
# ---------------------------------------------------------------------------

# ★ PLACEHOLDER lens text -- PR-M8 replaces with the researcher's authored lens prose
# (design §14 PR-M8: "the 3 fresh reviewer lens specs"). The STRUCTURE (which
# dims each lens attacks, the reframe-escalation trigger on the framework
# lens) was locked at PR-M5 and is unchanged. The WORDING below is PR-M8's
# calibrated version (design §11.2, methodology doc §B.3) -- grounded in the
# same instruments as the rubric (AMSTAR-2/ROBIS for the auditor, Nickerson
# for the framework critic, SANRA/CSUR for the synthesis adversary) and
# carrying the ARR justify-each-score reminder each lens shares.
_LENS_COVERAGE_AUDITOR: str = (
    "You are the COVERAGE & SCOPE AUDITOR (design §11.2, lens 1 of 3; grounded "
    "in AMSTAR-2 items #2/#4 and ROBIS domains 1-2). Attack SCOPE and REPRO "
    "first: is seminal / high-degree work in this area missing from the "
    "corpus? Is the search/selection boundary honestly stated, or "
    "gerrymandered to exclude inconvenient work and flatter the survey's own "
    "narrative? Cross-check any inclusion/exclusion ledger the draft shows "
    "against what the draft actually cites. You do not see the corpus "
    "directly -- judge only from what the draft itself shows and claims about "
    "its own coverage. Score all eight dimensions and justify every score "
    "with a located observation, but your edge is catching a survey whose "
    "real problem is that it looked in the wrong place, or drew its boundary "
    "to flatter its own thesis."
)

_LENS_FRAMEWORK_CRITIC: str = (
    "You are the FRAMEWORK / TAXONOMY CRITIC (design §11.2, lens 2 of 3; "
    "grounded in Nickerson et al. 2013's taxonomy ending-conditions). Attack "
    "FRAME: is the framework internally consistent, mutually exclusive, and "
    "collectively exhaustive over the corpus as the draft presents it -- "
    "concise, robust, comprehensive, extendible, explanatory? Does any branch "
    "orphan works that don't fit, or force a fit that isn't there? If the "
    "SAME misfit recurs across multiple sections -- the same works don't fit "
    "any branch, or a branch has no anchoring gap -- this is a RECURRING "
    "MISFIT, not a polish issue: no amount of prose rewriting fixes an "
    "incoherent spine. When you find recurring misfits, you MUST emit both:\n"
    "  MISFITS: <comma-separated list of the specific recurring misfits>\n"
    "  REFRAME_CANDIDATES: <comma-separated list of candidate encapsulating "
    "reframes that would resolve them, or 'none' if you see none>\n"
    "This is a PROPOSAL, never a commitment -- the human owns the spine "
    "(design §5.1); the loop only escalates a reframe if this same recurring "
    "misfit pattern survives across multiple review rounds, not a single "
    "round's finding. Score all eight dimensions as usual, with a located "
    "justification for each."
)

_LENS_SYNTHESIS_ADVERSARY: str = (
    "You are the SYNTHESIS-VS-ENUMERATION ADVERSARY (design §11.2, lens 3 of "
    "3; grounded in SANRA and the CSUR 'not a core dump' reviewer criterion). "
    "Attack SYNTH, COMPARE, GAP, and BIAS: does the draft marshal claims "
    "across MULTIPLE papers under a stated theme and compare them critically, "
    "or is it a per-paper enumeration (one paragraph, one paper, no "
    "comparison)? Is every stated gap anchored to a specific branch of the "
    "framework, or does it float free of any argument? Is any claim "
    "overclaimed relative to what the cited work actually supports, or is "
    "citation selective (cherry-picked to avoid a counterexample, disconfirming "
    "work silently dropped)? A single-cite paragraph, an unanchored gap, or a "
    "loose overclaim are exactly what you exist to catch -- a literal "
    "annotated bibliography (no framework, no cross-paper synthesis) MUST "
    "score LOW on SYNTH regardless of how fluently it reads: this is the #1 "
    "survey failure mode, and you are the last line of defense against it. "
    "Justify every score with the specific paragraph/claim you're pointing at."
)

_LENS_ORDER: tuple[str, ...] = (_LENS_COVERAGE_AUDITOR, _LENS_FRAMEWORK_CRITIC, _LENS_SYNTHESIS_ADVERSARY)


def get_reviewer_lens_spec(k: int, K: int) -> str:
    """Return the lens posture string for reviewer ``k`` of a round of ``K``.

    K=3 (the locked default): k=1 -> coverage auditor, k=2 -> framework
    critic, k=3 -> synthesis adversary. K != 3 cycles through the same three
    lenses in order (an adopter override to K is honored, not rejected).

    sr: PR-M5 (design §11.2)
    """
    idx = (k - 1) % len(_LENS_ORDER)
    return _LENS_ORDER[idx]


# ---------------------------------------------------------------------------
# Reviewer node -- ANTI-ANCHORING: never fed the thesis, never fed prior rounds
# ---------------------------------------------------------------------------

def _build_reviewer_prompt(draft_text: str, rubric: str, lens_spec: str) -> str:
    """Build the reviewer judge prompt: lens + rubric + draft text ONLY.

    Anti-anchoring (mirrors M3's honesty-gate discipline): the reviewer sees
    ONLY the lens posture, the rubric, and the rendered draft text. It does
    NOT see the manuscript's thesis/framing, the project context, prior-
    round reviews, or the author's rebuttal -- the function signature
    enforces this boundary (no such parameters exist to pass).
    """
    return lens_spec + "\n\n" + rubric.replace("{PDF_TEXT}", draft_text)


def run_reviewer_node(
    draft_text: str,
    *,
    round_num: int,
    lens_num: int,
    K: int,
    judge_fn: Callable[[str], str],
    judge_model: str = "",
    rubric_override: str | None = None,
    config: Any | None = None,
    run_state_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one FRESH, independent reviewer agent for round ``round_num``.

    Node-level skip short-circuit (§5J.17.2 pattern, re-instantiated): if
    ``run_state_meta["manuscript_review"]["cleared_at"]`` is already set (a
    prior round cleared), returns immediately -- NO judge call, NO score
    extraction. Proves round-2 reviewers never fire once round 1 clears.

    Fresh-by-construction: this function accepts ONLY the draft text, the
    lens, and the rubric -- no prior-round reviews, no rebuttal, no thesis
    parameter exists for a caller to (accidentally or not) wire in.

    sr: PR-M5
    """
    node_id = f"reviewer-{round_num}-{lens_num}"

    if run_state_meta is not None:
        rb_meta = run_state_meta.get("manuscript_review", {})
        if rb_meta.get("cleared_at") is not None:
            return {
                "round": round_num,
                "lens": lens_num,
                "node_id": node_id,
                "scores": {dim: 0 for dim in _ALL_DIMS},
                "raw_response": "",
                "judge_model": judge_model,
                "prompt_hash": "",
                "justifications": {},
                "missing_justifications": [],
                "skipped": True,
                "skip_reason": f"cleared at round {rb_meta['cleared_at']}",
            }

    rubric = get_review_rubric(override=rubric_override, config=config)
    lens_spec = get_reviewer_lens_spec(lens_num, K)
    prompt = _build_reviewer_prompt(draft_text, rubric, lens_spec)
    # PR-M8: log judge_model + prompt_hash (audit + drift detection, the
    # support-matcher/coldread convention -- sha256 hex[:16] of the prompt
    # actually sent, computed BEFORE the judge call).
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    raw_response = judge_fn(prompt)

    extracted = _extract_review_scores(raw_response)
    if extracted is None:
        scores = {dim: 0 for dim in _ALL_DIMS}
    else:
        scores = {dim: extracted.get(dim, 0) for dim in _ALL_DIMS}

    # PR-M8: ARR (justify-each-score) visibility -- best-effort per-dim
    # justification lookup, SURFACED (charter §2), never silently dropped.
    # Score numbers still come from the proven ``_extract_review_scores``
    # extractor above; this is additional audit metadata, not a re-gate.
    _, justifications = _extract_review_scores_and_justifications(raw_response)
    missing_justifications = [
        dim for dim, val in scores.items() if val > 0 and not justifications.get(dim, "").strip()
    ]

    escalation_fields = (
        _extract_frame_escalation_fields(raw_response) if lens_num == 2 or K == 1 else {"misfits": [], "reframe_candidates": []}
    )

    return {
        "round": round_num,
        "lens": lens_num,
        "node_id": node_id,
        "scores": scores,
        "raw_response": raw_response,
        "judge_model": judge_model,
        "prompt_hash": prompt_hash,
        "justifications": justifications,
        "missing_justifications": missing_justifications,
        "escalation_fields": escalation_fields,
        "skipped": False,
    }


# ---------------------------------------------------------------------------
# Meta-review node -- fan-in join + floor-not-average + canary
# ---------------------------------------------------------------------------

def run_meta_review(
    round_num: int,
    reviewer_results: list[dict[str, Any]],
    *,
    floor_dims: list[str] | None = None,
    floor_value: int = _DEFAULT_FLOOR_VALUE,
    canary_judge_fn: Callable[[str], str] | None = None,
    canary_rubric: str = "",
    judge_model: str = "",
    run_state_meta: dict[str, Any] | None = None,
    prior_floor_results: dict[str, dict[str, Any]] | None = None,
    prior_frame_recurrence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate K reviewer results by MIN, evaluate the floor predicate.

    Floor-not-average (design §9.1): ``cleared`` binds ONLY the floor dims
    (SCOPE, REPRO, CITE by default) via MIN-across-reviewers -- SURFACE dims
    (FRAME, COMPARE, BIAS) and SIGNAL dims (SYNTH, GAP) are recorded and
    surfaced but NEVER gate the clear predicate.

    Regression guard (design §9.1, the researcher's pass-2 Self-Refine caveat): if
    ``prior_floor_results`` is given, flags any floor dim whose min_score
    DROPPED versus the prior round -- never silently accepted.

    Reframe escalation (design §5.1) -- ★ PR-M8 TIGHTENING: MULTI-ROUND
    RECURRENCE, not a single round's low FRAME score. Design §5.1's literal
    wording is "recurring misfit ... round after round" -- a single round's
    weak FRAME finding is surfaced as a WATCHING note (in ``meta_review`` /
    ``worst_findings``) but the formal ``escalation`` payload is only built
    once FRAME<floor with misfits/candidates present has recurred in >= 2
    CONSECUTIVE evaluated rounds. ``prior_frame_recurrence`` threads the
    running streak state (``{"streak_rounds": [...], "misfits": [...],
    "candidate_reframes": [...]}``) between rounds; the returned dict's
    ``frame_recurrence`` field is the updated state for the caller to pass
    into the next round. A non-consecutive weak round (this round's FRAME is
    fine) resets the streak -- "round after round" means consecutive, not
    merely cumulative.

    sr: PR-M5; multi-round recurrence tightening PR-M8 (design §5.1)
    """
    node_id = f"meta-review-{round_num}"
    _floor_dims = floor_dims if floor_dims is not None else list(_DEFAULT_FLOOR_DIMS)

    if run_state_meta is not None:
        rb_meta = run_state_meta.get("manuscript_review", {})
        if rb_meta.get("cleared_at") is not None:
            return {
                "round": round_num,
                "node_id": node_id,
                "cleared": True,
                "cleared_at": rb_meta.get("cleared_at"),
                "floor_results": {},
                "scores_per_reviewer": [],
                "meta_review": f"SKIPPED -- cleared at round {rb_meta['cleared_at']}",
                "worst_findings": [],
                "missing_justifications": [],
                "escalation": None,
                "frame_recurrence": prior_frame_recurrence or {"streak_rounds": [], "misfits": [], "candidate_reframes": []},
                "regression": {"regressed": False, "dims": []},
                "canary_ok": True,
                "canary_note": "SKIPPED",
                "skipped": True,
            }

    active_results = [r for r in reviewer_results if not r.get("skipped", False)]
    scores_per_reviewer = [r["scores"] for r in active_results]

    _canary_fn = canary_judge_fn if canary_judge_fn is not None else (lambda p: "")
    canary_result = run_canary_scaffold(
        _canary_fn, canary_rubric, floor_dims=_floor_dims, floor_value=floor_value, judge_model=judge_model,
    )

    threshold = _evaluate_threshold(scores_per_reviewer, floor_dims=_floor_dims, floor_value=floor_value)
    cleared = threshold["cleared"]
    cleared_at = round_num if cleared else None

    # --- Regression guard: never silently accept a round that regresses ----
    regression_dims: list[str] = []
    if prior_floor_results:
        for dim, fr in threshold["floor_results"].items():
            prior_fr = prior_floor_results.get(dim)
            if prior_fr is not None and fr["min_score"] < prior_fr["min_score"]:
                regression_dims.append(dim)
    regression = {"regressed": bool(regression_dims), "dims": regression_dims}

    failing_dims = [dim for dim, fr in threshold["floor_results"].items() if not fr["passed"]]
    if failing_dims:
        meta_review_text = (
            f"Round {round_num} -- NOT CLEARED. Failing floor dimension(s): "
            f"{', '.join(failing_dims)}. MIN scores: "
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
            f"Round {round_num} -- CLEARED. All floor dimensions meet threshold: "
            + ", ".join(
                f"{dim}={threshold['floor_results'][dim]['min_score']}/{floor_value}"
                for dim in _floor_dims
            )
        )
        worst_findings = []
    if regression["regressed"]:
        meta_review_text += (
            f" REGRESSION vs prior round on: {', '.join(regression_dims)} -- "
            f"keeping the better (prior) draft per the regression guard."
        )

    # --- PR-165 fix 1: propagate per-reviewer missing_justifications (ARR) --
    # ``run_reviewer_node`` computes ``missing_justifications`` per reviewer
    # but this function never read it -- surfaced to the machine, silently
    # dropped before the human (a green-and-empty, charter Sec 2). Aggregate
    # every reviewer's missing-justification dims into a dedicated field, and
    # additionally push FLOOR-dim misses (SCOPE/REPRO/CITE by default) into
    # ``worst_findings`` -- an unjustified FLOOR score deserves a loud line
    # right next to ``floor_results``. This is surfacing only: floor scores
    # are already mechanically backstopped by ``build_approve_payload``; a
    # hard re-gate on a line-anchored justification parser would be brittle
    # and fail-closed-wrongly (the researcher's fit-check verdict).
    missing_justification_lines: list[str] = []
    for r in active_results:
        r_node_id = r.get("node_id", "?")
        r_scores = r.get("scores", {})
        for dim in r.get("missing_justifications", []):
            line = (
                f"[Round {round_num}] {dim}={r_scores.get(dim, '?')} from "
                f"{r_node_id} carries NO justification -- score not "
                f"corroborated in text (ARR)"
            )
            missing_justification_lines.append(line)
            if dim in _floor_dims and line not in worst_findings:
                worst_findings.append(line)

    # --- Reframe escalation (design §5.1) -- ★ PR-M8 MULTI-ROUND RECURRENCE
    # (tightened from PR-M5's single-round FRAME<floor trigger). A single low
    # round is recorded as a "watching" note; the formal escalation payload
    # only builds once the SAME weak-FRAME-with-misfits condition has held in
    # >= 2 CONSECUTIVE rounds -- design §5.1's literal wording, "recurring
    # misfit ... round after round".
    escalation: dict[str, Any] | None = None
    frame_min = min((r["scores"].get("FRAME", 0) for r in active_results), default=0)
    this_round_misfits: list[str] = []
    this_round_candidates: list[str] = []
    for r in active_results:
        ef = r.get("escalation_fields") or {}
        this_round_misfits.extend(ef.get("misfits", []))
        this_round_candidates.extend(ef.get("reframe_candidates", []))
    frame_low_this_round = frame_min < floor_value and bool(this_round_misfits or this_round_candidates)

    prior_streak = list((prior_frame_recurrence or {}).get("streak_rounds", []))
    prior_misfits = list((prior_frame_recurrence or {}).get("misfits", []))
    prior_candidates = list((prior_frame_recurrence or {}).get("candidate_reframes", []))

    # Consecutive-only: a streak only extends if the prior recorded round is
    # the immediately preceding round number -- a gap (this round is not
    # round_num-1 after the last streak entry) is NOT "round after round".
    is_consecutive = bool(prior_streak) and prior_streak[-1] == round_num - 1

    if frame_low_this_round:
        if is_consecutive:
            streak_rounds = prior_streak + [round_num]
            misfits = prior_misfits + this_round_misfits
            reframe_candidates = prior_candidates + this_round_candidates
        else:
            streak_rounds = [round_num]
            misfits = list(this_round_misfits)
            reframe_candidates = list(this_round_candidates)
    else:
        # This round's FRAME is fine -- the streak resets (not recurring).
        streak_rounds, misfits, reframe_candidates = [], [], []

    frame_recurrence = {
        "streak_rounds": streak_rounds,
        "misfits": misfits,
        "candidate_reframes": reframe_candidates,
    }

    if len(streak_rounds) >= 2:
        escalation = {
            "round": round_num,
            "frame_min_score": frame_min,
            "recurring_rounds": streak_rounds,
            "recurring_misfits": misfits,
            "candidate_reframes": reframe_candidates,
            "note": (
                f"framework judged incoherent round after round -- FRAME weak "
                f"(min score {frame_min} < {floor_value}) with recurring "
                f"misfits across rounds {streak_rounds}; recurring "
                f"misfits = {misfits or '(unspecified)'}; candidate "
                f"encapsulating reframes = {reframe_candidates or '(none proposed)'}. "
                f"The machine PROPOSES only -- the human commits the new spine "
                f"via `rv manuscript new --reframe <prior>` (design §5.1)."
            ),
        }
    elif frame_low_this_round:
        # Surfaced (charter §2) but NOT yet an escalation -- a single round's
        # weak FRAME is not "round after round" recurrence.
        watching_note = (
            f"[Round {round_num}] FRAME flagged weak (min score {frame_min} < "
            f"{floor_value}); watching for recurrence before escalating "
            f"reframe-the-spine (design §5.1 requires >= 2 consecutive rounds)."
        )
        meta_review_text += " " + watching_note
        if watching_note not in worst_findings:
            worst_findings = worst_findings + [watching_note]

    if run_state_meta is not None and cleared:
        if "manuscript_review" not in run_state_meta:
            run_state_meta["manuscript_review"] = {}
        run_state_meta["manuscript_review"]["cleared_at"] = round_num

    return {
        "round": round_num,
        "node_id": node_id,
        "cleared": cleared,
        "cleared_at": cleared_at,
        "floor_results": threshold["floor_results"],
        "scores_per_reviewer": scores_per_reviewer,
        "meta_review": meta_review_text,
        "worst_findings": worst_findings,
        "missing_justifications": missing_justification_lines,
        "escalation": escalation,
        "frame_recurrence": frame_recurrence,
        "regression": regression,
        "canary_ok": canary_result["canary_ok"],
        "canary_note": canary_result.get("canary_note", ""),
        "skipped": False,
    }


# ---------------------------------------------------------------------------
# Revise node -- records a rebuttal, RE-FIRES the gates via build_approve_payload
# ---------------------------------------------------------------------------

def run_revise(
    round_num: int,
    meta_review: dict[str, Any],
    tree_root: Path,
    project_notes_dir: Path,
    ms_type: Any,
    *,
    judge_fn: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Run the revise-r node: record the rebuttal, RE-FIRE the fidelity +
    equation + coverage gates via ``check_gates.build_approve_payload``.

    ★ Single-sourced -- this function imports and calls
    ``manuscript.check_gates.build_approve_payload`` (the assembler PR-M5
    was explicitly told NOT to duplicate). The actual section redrafting is
    an agent action performed by the real DAG's ``revise-r`` node OUTSIDE
    this pure function (mirrors the removed module's own boundary: it never
    diffed draft text either) -- this function's postcondition check is
    "does the (possibly redrafted) tree still pass every gate."

    The rebuttal is an ARTIFACT recorded for the human's review -- it is
    NEVER a verdict (crew-cannot-self-approve: the author cannot accept
    their own paper).

    sr: PR-M5
    """
    node_id = f"revise-{round_num}"

    meta_review_text = (
        meta_review.get("meta_review", "") if isinstance(meta_review, dict) else str(meta_review)
    )
    rebuttal = (
        f"[REBUTTAL -- round {round_num}]: author acknowledges meta-review "
        f"concerns: {meta_review_text[:200]}. Revision in progress. "
        f"(Recorded artifact -- not a verdict.)"
    )

    from research_vault.manuscript.check_gates import build_approve_payload

    payload = build_approve_payload(tree_root, project_notes_dir, ms_type, judge_fn=judge_fn)

    return {
        "round": round_num,
        "node_id": node_id,
        "rebuttal": rebuttal,
        "gate_payload": payload,
        "honesty_gate_blocked": not payload["ok"],
        "blocking": payload["blocking"],
    }


# ---------------------------------------------------------------------------
# Main review-board loop -- N-round bounded unroll (N=2, K=3)
# ---------------------------------------------------------------------------

def run_review_board(
    draft_text: str,
    tree_root: Path,
    project_notes_dir: Path,
    ms_type: Any,
    *,
    N: int = _DEFAULT_MAX_ROUNDS,
    K: int = _DEFAULT_REVIEWERS_PER_ROUND,
    floor_dims: list[str] | None = None,
    floor_value: int = _DEFAULT_FLOOR_VALUE,
    judge_fn: Callable[[str], str] | None = None,
    judge_model: str = "",
    rubric_override: str | None = None,
    config: Any | None = None,
    canary_judge_fn: Callable[[str], str] | None = None,
    canary_rubric: str | None = None,
    revise_judge_fn: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Run the full bounded N-round (default 2x3) review-revise loop.

    Bounded, acyclic unroll: N pre-declared round-blocks. Cleared-at-round-r
    -> remaining rounds are no-ops (node-level skip short-circuit, asserted:
    no further judge calls). Not-cleared-after-N -> a first-class NOT-CLEARED
    payload (honest failure, never a silent pass, never an infinite loop).

    Args:
        draft_text:        the rendered manuscript draft text (main.tex +
                            sections/*.tex, joined -- see
                            ``check_gates._read_draft_text``).
        tree_root:          the manuscript folder (``manuscripts/<slug>/``).
        project_notes_dir:  the project's OKF notes root.
        ms_type:            the manuscript's registered ``ManuscriptType``
                            (for ``ms_type.rubric`` override + passed through
                            to the gate re-fire).
        N:                  rounds (frozen at scaffold; clamped to hard-cap 3).
        K:                  reviewers per round (min 2).
        floor_dims:         floor dim codes (default SCOPE, REPRO, CITE).
        floor_value:        minimum MIN-across-reviewers score to clear.
        judge_fn:           injectable reviewer judge (required in
                            production; mock in tests).
        judge_model:        model-id to log.
        rubric_override:    explicit rubric override (else ``ms_type.rubric``
                            then config then the placeholder).
        config:             optional Config for the rubric config-seam.
        canary_judge_fn:    judge for the 3 canary probes (None -> skip).
        canary_rubric:      rubric string with the ``{PDF_TEXT}`` slot for
                            canary probes (defaults to the resolved rubric).
        revise_judge_fn:    judge passed through to the gate re-fire's
                            support-matcher/cold-read (None -> those two
                            gates land in ``not_run``, never silently BLOCK).

    Returns dict with:
        cleared:       bool
        cleared_at:    int | None
        rounds:        list[dict] -- per-round reviewer scores + meta-review
                       + revise (gate re-fire) records
        not_cleared:   dict | None -- the first-class NOT-CLEARED payload
        escalation:    dict | None -- the LATEST reframe-escalation payload
                       seen across all rounds (surface-not-auto, §5.1)
        honest_report: str -- never says "approved"; says "cleared at r" or
                       "NOT cleared"
        meta:          RunState.meta["manuscript_review"] dict

    sr: PR-M5
    """
    if judge_fn is None:
        raise RuntimeError(
            "run_review_board: judge_fn is required. In production, set "
            "RV_JUDGE_MODEL and ANTHROPIC_API_KEY; in tests, pass a mock."
        )

    N_capped = min(N, _MAX_ROUNDS_HARDCAP)
    K_capped = max(K, _MIN_REVIEWERS_PER_ROUND)
    _floor_dims = floor_dims if floor_dims is not None else list(_DEFAULT_FLOOR_DIMS)
    _resolved_rubric = get_review_rubric(
        override=rubric_override if rubric_override is not None else getattr(ms_type, "rubric", None),
        config=config,
    )
    # canary_rubric defaults to the resolved rubric ONLY when a canary judge
    # was actually supplied -- when canary_judge_fn is None (the common case
    # for tests/callers that haven't wired a canary judge), it stays ""
    # so run_canary_scaffold's SKIP path fires (back-compat, mirrors the
    # removed module's own convention: canary is opt-in, not silently forced).
    if canary_rubric is not None:
        _canary_rubric = canary_rubric
    elif canary_judge_fn is not None:
        _canary_rubric = _resolved_rubric
    else:
        _canary_rubric = ""

    run_state_meta: dict[str, Any] = {"manuscript_review": {}}
    rounds: list[dict[str, Any]] = []
    cleared = False
    cleared_at: int | None = None
    last_escalation: dict[str, Any] | None = None
    prior_floor_results: dict[str, dict[str, Any]] | None = None
    prior_frame_recurrence: dict[str, Any] | None = None

    for r in range(1, N_capped + 1):
        round_record: dict[str, Any] = {"round": r}

        if run_state_meta["manuscript_review"].get("cleared_at") is not None:
            round_record["skipped"] = True
            round_record["skip_reason"] = (
                f"cleared at round {run_state_meta['manuscript_review']['cleared_at']}"
            )
            rounds.append(round_record)
            continue

        reviewer_results: list[dict[str, Any]] = []
        for k in range(1, K_capped + 1):
            reviewer_result = run_reviewer_node(
                draft_text,
                round_num=r,
                lens_num=k,
                K=K_capped,
                judge_fn=judge_fn,
                judge_model=judge_model,
                rubric_override=_resolved_rubric,
                config=config,
                run_state_meta=run_state_meta,
            )
            reviewer_results.append(reviewer_result)
        round_record["reviewers"] = reviewer_results

        meta_result = run_meta_review(
            round_num=r,
            reviewer_results=reviewer_results,
            floor_dims=_floor_dims,
            floor_value=floor_value,
            judge_model=judge_model,
            canary_judge_fn=canary_judge_fn,
            canary_rubric=_canary_rubric,
            run_state_meta=run_state_meta,
            prior_floor_results=prior_floor_results,
            prior_frame_recurrence=prior_frame_recurrence,
        )
        round_record["meta_review"] = meta_result
        prior_floor_results = meta_result.get("floor_results") or prior_floor_results
        prior_frame_recurrence = meta_result.get("frame_recurrence") or prior_frame_recurrence
        if meta_result.get("escalation"):
            last_escalation = meta_result["escalation"]

        if meta_result["cleared"]:
            cleared = True
            cleared_at = r

        if r < N_capped and not meta_result["cleared"]:
            revise_result = run_revise(
                round_num=r,
                meta_review=meta_result,
                tree_root=tree_root,
                project_notes_dir=project_notes_dir,
                ms_type=ms_type,
                judge_fn=revise_judge_fn,
            )
            round_record["revise"] = revise_result

        rounds.append(round_record)

    not_cleared_payload: dict[str, Any] | None = None
    if not cleared:
        last_meta = None
        for r_data in reversed(rounds):
            if not r_data.get("skipped", False):
                last_meta = r_data.get("meta_review", {})
                break

        failing_dims: list[str] = []
        worst_finding_strs: list[str] = []
        missing_justification_strs: list[str] = []
        if last_meta:
            for dim, fr in last_meta.get("floor_results", {}).items():
                if not fr.get("passed", True):
                    failing_dims.append(f"{dim} (min score {fr['min_score']} < floor {floor_value})")
            worst_finding_strs = last_meta.get("worst_findings", [])
            missing_justification_strs = last_meta.get("missing_justifications", [])

        persistent_weakness = (
            f"Manuscript did not reach the review-board bar after {N_capped} round(s). "
            f"Failing floor dimension(s): {', '.join(failing_dims) or 'all floor dims'}. "
            f"Surviving objection(s): {'; '.join(worst_finding_strs) or 'see round meta-reviews above'}. "
            f"The human operator must adjudicate whether to revise further or submit as-is."
        )

        not_cleared_payload = {
            "n_rounds": N_capped,
            "failing_dims": failing_dims,
            "persistent_weakness": persistent_weakness,
            "worst_findings": worst_finding_strs,
            "missing_justifications": missing_justification_strs,
        }

    if cleared:
        honest_report = (
            f"review-board: {N_capped} round(s) scheduled, cleared at round {cleared_at}; "
            f"floors {' '.join(f'{d}>={floor_value}' for d in _floor_dims)}"
        )
    else:
        last_round = rounds[-1] if rounds else {}
        last_meta_review = last_round.get("meta_review", {})
        honest_report = (
            f"review-board: {N_capped} round(s) run, NOT cleared after {N_capped} round(s); "
            f"failing floors: "
            + ", ".join(
                f"{d}={last_meta_review.get('floor_results', {}).get(d, {}).get('min_score', 0)}/{floor_value}"
                for d in _floor_dims
            )
        )

    return {
        "cleared": cleared,
        "cleared_at": cleared_at,
        "rounds": rounds,
        "not_cleared": not_cleared_payload,
        "escalation": last_escalation,
        "honest_report": honest_report,
        "n_rounds_run": len([r for r in rounds if not r.get("skipped", False)]),
        "n_reviewers_per_round": K_capped,
        "meta": run_state_meta["manuscript_review"],
    }


# ---------------------------------------------------------------------------
# [manuscript_review] config seam (design §9)
# ---------------------------------------------------------------------------

def get_review_config(config: Any | None = None) -> dict[str, Any]:
    """Return the ``[manuscript_review]`` config dict, with defaults applied.

    ``max_rounds``: clamped to the hard-cap (3). ``reviewers_per_round``:
    min 2 enforced. ``floor_dimensions``: expanded via ``_normalize_floor_dims``
    (accepts either the design's literal axis names or dim codes directly).

    sr: PR-M5 (design §9's config seam)
    """
    raw_cfg: dict[str, Any] = {}
    if config is not None:
        raw = getattr(config, "_raw", {})
        raw_cfg = raw.get("manuscript_review", {}) or {}

    n = int(raw_cfg.get("max_rounds", _DEFAULT_MAX_ROUNDS))
    n = min(n, _MAX_ROUNDS_HARDCAP)
    k = int(raw_cfg.get("reviewers_per_round", _DEFAULT_REVIEWERS_PER_ROUND))
    k = max(k, _MIN_REVIEWERS_PER_ROUND)

    floor_dims_raw = raw_cfg.get(
        "floor_dimensions", ["citation_fidelity", "coverage_reproducibility"]
    )
    floor_dims = _normalize_floor_dims(floor_dims_raw)

    floor_value = int(raw_cfg.get("floor_value", _DEFAULT_FLOOR_VALUE))
    venue_scale = str(raw_cfg.get("venue_scale", _DEFAULT_VENUE_SCALE))
    aggregation = str(raw_cfg.get("aggregation", "min"))

    return {
        "max_rounds": n,
        "reviewers_per_round": k,
        "floor_dimensions": floor_dims,
        "floor_value": floor_value,
        "venue_scale": venue_scale,
        "aggregation": aggregation,
    }
