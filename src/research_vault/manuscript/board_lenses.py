"""manuscript/board_lenses.py — PR-B2: the 4 board-axis lens specs, axis
rubrics, the uniform finding schema, and the per-axis finding caps +
sub-budgets.

Design: docs/superpowers/specs/2026-07-08-autonomous-board-design.md §1
(the 4 lenses table), §2 (the finding schema).

The 4 axes are ORTHOGONAL quality lenses on the holistic-quality floor —
distinct from the mechanical integrity floors in ``check_gates.py``
(hermetic-bib / support-matcher / coverage-gate / reader-hygiene /
heading-order), which this module reuses rather than duplicates (the
FRAMEWORK lens's ``heading_diff`` field is the mechanical
``check_heading_order`` result handed to the judge as ground truth, never
re-derived here).

Anti-anchoring is structural: ``build_lens_tasks`` accepts ONLY judge-facing
fields (draft text, the pre-committed contradiction map, the mechanical
heading diff) — there is no parameter through which an author's thesis or a
prior round's reviews could be passed in (mirrors
``review_board._build_reviewer_prompt``'s anti-anchoring discipline).

Finding schema (uniform across all 4 lenses, decision #5):
    {finding_id, severity: critical|major|minor, location, issue,
     evidence, recommendation}
No ``old_text``/``new_text`` — the judge locates + cites; the revise step
(PR-B4) words the change.

Stdlib only. Hermetic — no live LLM call anywhere in this module.
sr: PR-B2
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Axes + lenses
# ---------------------------------------------------------------------------

AXES: tuple[str, ...] = ("CONTENT", "SELFCONT", "ADVERS", "FRAMEWORK")
LENS_TO_AXIS: dict[str, str] = {
    "content": "CONTENT",
    "self-containment": "SELFCONT",
    "adversarial": "ADVERS",
    "framework": "FRAMEWORK",
}
AXIS_TO_LENS: dict[str, str] = {v: k for k, v in LENS_TO_AXIS.items()}
LENS_ORDER: tuple[str, ...] = ("content", "self-containment", "adversarial", "framework")

_DEFAULT_FLOOR_VALUE: int = 3

SEVERITY_ORDER: dict[str, int] = {"critical": 0, "major": 1, "minor": 2}
_SEVERITY_RANK_UNKNOWN = 3  # an unrecognized severity sorts LAST — never crowds out a real one


# ---------------------------------------------------------------------------
# Finding caps + sub-budgets (§88 the caps table)
# ---------------------------------------------------------------------------

FINDING_CAPS: dict[str, int] = {
    "CONTENT": 12,
    "SELFCONT": 10,
    "ADVERS": 12,
    "FRAMEWORK": 15,
}

# sub-budget key -> max count within that class. A finding's sub-budget
# class is read from its (optional) "category" field; findings with no
# recognized category fall into the axis's own default/unbudgeted bucket
# (never crowded out by a sub-budgeted class, since the default bucket has
# no cap of its own beyond the overall finding_cap).
SUB_BUDGETS: dict[str, dict[str, int]] = {
    "CONTENT": {"bloat": 2},
    "SELFCONT": {},
    "ADVERS": {},
    "FRAMEWORK": {"structural": 3},
}


def cap_and_prioritize_findings(findings: list[dict[str, Any]], axis: str) -> list[dict[str, Any]]:
    """Cap + prioritize a lens's findings per §2's discipline.

    - Sort ``critical > major > minor`` (unknown severities sort last,
      never ahead of a recognized one).
    - Apply the axis's sub-budgets FIRST (a category capped at N never
      contributes more than N entries, even if it would otherwise crowd
      out the axis's finding_cap) — a low-priority class cannot starve a
      high-priority one.
    - Truncate the merged result to the axis's overall finding_cap,
      keeping the N most load-bearing (severity-sorted) findings.

    A judge that ignores its own cap/sub-budget instructions is defended
    against here — this is the mechanical backstop, not merely a prompt
    convention.
    """
    cap = FINDING_CAPS.get(axis, len(findings))
    sub_budgets = SUB_BUDGETS.get(axis, {})

    def _sev_rank(f: dict[str, Any]) -> int:
        return SEVERITY_ORDER.get(str(f.get("severity", "")).strip().lower(), _SEVERITY_RANK_UNKNOWN)

    sorted_findings = sorted(findings, key=_sev_rank)

    if not sub_budgets:
        return sorted_findings[:cap]

    sub_budgeted: list[dict[str, Any]] = []
    unbudgeted: list[dict[str, Any]] = []
    sub_counts: dict[str, int] = {k: 0 for k in sub_budgets}
    for f in sorted_findings:
        category = str(f.get("category", "")).strip().lower()
        if category in sub_budgets:
            if sub_counts[category] < sub_budgets[category]:
                sub_budgeted.append(f)
                sub_counts[category] += 1
            # else: dropped — this category is at its sub-budget ceiling.
        else:
            unbudgeted.append(f)

    # The sub-budget ceiling (applied above, per category) already caps how
    # many sub-budgeted findings CAN compete for a cap slot; merge both
    # buckets back into one severity-sorted list and truncate to the axis
    # cap — a low-priority sub-budgeted class can never crowd out a primary
    # (unbudgeted) finding of equal-or-higher severity because it never
    # exceeds its own (smaller) sub-budget ceiling in the first place.
    combined = sorted(unbudgeted + sub_budgeted, key=_sev_rank)
    return combined[:cap]


# ---------------------------------------------------------------------------
# Axis rubrics (the {DRAFT} slot mirrors review_board's {PDF_TEXT} slot)
# ---------------------------------------------------------------------------

_FINDING_SCHEMA_INSTRUCTIONS: str = """\
Emit your verdict as a 1-5 axis score (1=fails outright, 3=the bar clears,
5=exemplary) plus a capped, prioritized list of atomic findings. Each
finding MUST use exactly this schema — no other fields:
  {"finding_id": "f-<lens>-NNNN", "severity": "critical"|"major"|"minor",
   "location": "<a locator snippet -- NOT an exact-match requirement>",
   "issue": "<one sentence>", "evidence": "<a real note-id/citekey>",
   "recommendation": "<the concrete fix -- you locate+cite, never word it>"}
Do NOT include old_text/new_text — you locate and cite the problem; a
downstream revise step words the exact change. If you find more findings
than your cap allows, return the N most load-bearing (critical > major >
minor) — returning many small findings buries the critical ones.
"""

CONTENT_RUBRIC: str = """\
You are the CONTENT judge on the autonomous review board (one of 4
independent cold-read axes). You have been handed ONLY the draft text
below — no thesis, no prior round's reviews, no other judge's output.

Judge SUBSTANCE: is the argument sound, is this REAL cross-paper synthesis
(a claim compared across >=2 papers under a stated theme) rather than an
annotated list (one paragraph per paper, no comparison)? Are claims
accurate to what a real corpus would show? Is the framework well-motivated?
WEAK: one-paragraph-per-paper enumeration; a paragraph citing exactly one
source; hand-waving where the corpus should be specific.

★ Prescriptive-specificity: every claim should carry the number/threshold/
mechanism the corpus supports, or explicitly say the corpus doesn't
quantify it — vague where the material is specific is a `major` finding.

★ Bloat/redundancy (capped sub-budget, category="bloat", max 2 findings,
never crowds out substance findings): flag padded/repetitive prose or an
executive-summary/conclusion with heavy phrase overlap.

Score CONTENT on the 1-5 ordinal (floor = 3) and justify it.

{FINDING_SCHEMA}

────────────────────────────────────────────────────────────────────────
DRAFT TEXT (this is ALL you may use)
────────────────────────────────────────────────────────────────────────
{DRAFT}
────────────────────────────────────────────────────────────────────────

Emit: [CONTENT:N] <justification>, then your findings.
"""

SELFCONT_RUBRIC: str = """\
You are the SELF-CONTAINMENT judge on the autonomous review board. You have
been handed ONLY the draft text below — no thesis, no prior reviews, no
other judge's output.

Judge the COLD READ: would this be clear to a reader with NO prior context?
Flag internal jargon, unexpanded acronyms, code/tool tokens (e.g. handles
like `CPk`/`Qk`, raw hashes), or assumed pipeline context a first-time
reader could not resolve. GOOD: every term defined on first use, no leaked
internal vocabulary. WEAK: an acronym never expanded; a claim that assumes
the reader already knows the corpus/method.

Score SELFCONT on the 1-5 ordinal (floor = 3) and justify it.

{FINDING_SCHEMA}

────────────────────────────────────────────────────────────────────────
DRAFT TEXT (this is ALL you may use)
────────────────────────────────────────────────────────────────────────
{DRAFT}
────────────────────────────────────────────────────────────────────────

Emit: [SELFCONT:N] <justification>, then your findings.
"""

ADVERSARIAL_RUBRIC: str = """\
You are the ADVERSARIAL judge on the autonomous review board. You have been
handed ONLY the draft text below — no thesis, no prior reviews, no other
judge's output. Be DEFAULT-SKEPTIC: your job is to REFUTE, not bless.

Attack the weakest/least-supported claim in the draft. Hunt for overclaims,
unsupported leaps, and ignored counter-evidence. Does the central thesis
survive attack?

{CONTRADICTION_MAP_BLOCK}
★ Integrate-by-scoping, not append-as-caveat: when you recommend a fix for
a claim that ignores counter-evidence, ask for a SCOPE-NARROWING ("X holds
in <domain A>; in <domain B>, <counter-evidence> shows a different regime")
— never a hedge ("X, though this may resolve differently"). Your
`recommendation` field must read as a scope-narrowing instruction, not a
request to soften the claim.

Score ADVERS on the 1-5 ordinal (floor = 3) and justify it.

{FINDING_SCHEMA}

────────────────────────────────────────────────────────────────────────
DRAFT TEXT (this is ALL you may use)
────────────────────────────────────────────────────────────────────────
{DRAFT}
────────────────────────────────────────────────────────────────────────

Emit: [ADVERS:N] <justification>, then your findings.
"""

FRAMEWORK_RUBRIC: str = """\
You are the FRAMEWORK-ADHERENCE judge on the autonomous review board. You
have been handed ONLY the draft text below — no thesis, no prior reviews,
no other judge's output.

Judge whether the output honored the APPROVED spine: every section present,
in the FROZEN order, no vague-recommendation gaps ("more research is
needed" with no specific pointer), no orphaned/miscategorized content.

{HEADING_DIFF_BLOCK}
The heading-diff above is MECHANICAL ground truth (a deterministic H2-order
check) — treat any reported drift as a real structural signal, not
something to second-guess; your job is to explain WHY it matters (a
deliberate merge/split vs. a real assembly drift) and to catch everything
the mechanical check cannot (vague-recommendation gaps, orphaned content,
miscategorization within a section).

★ Prescriptive-specificity applies to recommendations too: a
recommendation gap must name a specific missing pointer/citation/mechanism,
never "more research is needed" on its own (`major`, category="structural"
if it is itself a structural gap, otherwise unbudgeted).

Score FRAMEWORK on the 1-5 ordinal (floor = 3) and justify it.

{FINDING_SCHEMA}

────────────────────────────────────────────────────────────────────────
DRAFT TEXT (this is ALL you may use)
────────────────────────────────────────────────────────────────────────
{DRAFT}
────────────────────────────────────────────────────────────────────────

Emit: [FRAMEWORK:N] <justification>, then your findings.
"""

_AXIS_RUBRICS: dict[str, str] = {
    "CONTENT": CONTENT_RUBRIC,
    "SELFCONT": SELFCONT_RUBRIC,
    "ADVERS": ADVERSARIAL_RUBRIC,
    "FRAMEWORK": FRAMEWORK_RUBRIC,
}


def _render_rubric(
    axis: str,
    draft_text: str,
    *,
    contradiction_map: Any | None = None,
    heading_diff: dict[str, Any] | None = None,
) -> str:
    template = _AXIS_RUBRICS[axis]
    rendered = template.replace("{FINDING_SCHEMA}", _FINDING_SCHEMA_INSTRUCTIONS)
    rendered = rendered.replace("{DRAFT}", draft_text)
    if axis == "ADVERS":
        if contradiction_map:
            block = (
                "★ Check the draft against this PRE-COMMITTED corpus "
                "contradiction/tension map (ground truth from synthesis, not "
                "your intuition). For each high-relevance fork, did the draft "
                "engage both sides, or ignore/straw-man/flatten one? A "
                "confident claim on one side of a documented fork with no "
                f"acknowledgment of the other is a `critical` finding.\n{contradiction_map!r}\n"
            )
        else:
            block = (
                "(No pre-committed contradiction map was available for this "
                "round — fall back to intuition-only refutation; this is a "
                "SIGNAL, not a HALT.)\n"
            )
        rendered = rendered.replace("{CONTRADICTION_MAP_BLOCK}", block)
    if axis == "FRAMEWORK":
        if heading_diff is not None:
            block = f"Mechanical heading-order diff result: {heading_diff!r}\n"
        else:
            block = "(No mechanical heading-diff was supplied for this round.)\n"
        rendered = rendered.replace("{HEADING_DIFF_BLOCK}", block)
    return rendered


# ---------------------------------------------------------------------------
# Task builder — anti-anchoring: no thesis parameter exists to pass
# ---------------------------------------------------------------------------

def build_lens_tasks(
    draft_text: str,
    *,
    contradiction_map: Any | None = None,
    heading_diff: dict[str, Any] | None = None,
    frozen_order: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Build the 4 lens tasks (WITHOUT ids — the caller/emit step assigns
    ids via ``gates.judge_seam.interleave_with_canaries``).

    Each task: ``{kind, lens, axis, rubric, draft, finding_cap,
    sub_budgets, [contradiction_map], [heading_diff], [frozen_order]}``.
    Only the ADVERS task carries ``contradiction_map``; only the FRAMEWORK
    task carries ``heading_diff``/``frozen_order`` — the other lenses never
    see fields irrelevant to their axis (keeps each judge's prompt scoped
    to exactly what its rubric references).
    """
    tasks: list[dict[str, Any]] = []
    for lens in LENS_ORDER:
        axis = LENS_TO_AXIS[lens]
        rubric = _render_rubric(
            axis, draft_text, contradiction_map=contradiction_map, heading_diff=heading_diff,
        )
        task: dict[str, Any] = {
            "kind": "board",
            "lens": lens,
            "axis": axis,
            "rubric": rubric,
            "draft": draft_text,
            "finding_cap": FINDING_CAPS[axis],
            "sub_budgets": dict(SUB_BUDGETS.get(axis, {})),
        }
        if axis == "ADVERS" and contradiction_map is not None:
            task["contradiction_map"] = contradiction_map
        if axis == "FRAMEWORK":
            if heading_diff is not None:
                task["heading_diff"] = heading_diff
            if frozen_order is not None:
                task["frozen_order"] = list(frozen_order)
        tasks.append(task)
    return tasks
