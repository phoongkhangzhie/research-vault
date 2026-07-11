# SPDX-License-Identifier: AGPL-3.0-or-later
"""gates/canary_passages.py: the calibrated board-judge canary
passages, RELOCATED here (out of ``manuscript/review_board.py``) so the
board's cold-fanout canary bank keeps working after the direct-API judge
path is deleted.

WHY THIS MODULE EXISTS (deletion-blast-radius, "RELOCATE FIRST"):
  ``gates/board_seam.py`` (a ``gates/`` module) imported the 3 calibrated
  review-board canary passages from ``manuscript/review_board.py`` (a
  ``manuscript/`` module) — a LAYERING INVERSION (``manuscript`` imports
  ``gates``, never the reverse). This deletes ``review_board``'s direct-API
  judge default, so the passages move DOWN to ``gates/`` where both the
  board seam (``gates/``) and ``review_board`` (``manuscript/``) can import
  them in the correct dependency direction.

WHAT'S HERE:
  1. The 3 ORIGINAL calibrated review-board passages (strong / weak /
     annotated-bib) + their unique markers — verbatim from ``review_board``.
     ``review_board`` re-imports them so its own canary scaffold + the tests
     that reference ``rb._CANARY_STRONG_MARKER`` etc. keep resolving.
  2. ★ PER-AXIS canaries — a calibrated rejects-only FAIL probe for
     EACH of the 6 board axes (DEPTH / WIDTH / SYNTH / SELFCONT / ADVERS /
     INSTRUCT). In the per-axis cold fanout every lens goes to a SEPARATE
     fresh subagent, so a single-axis (SYNTH-only) canary certifies only ONE
     judge — a rubber-stamping WIDTH or DEPTH judge would sail through. Each
     axis now carries its own planted probe: a rubber-stamping judge on ANY
     axis scores its FAIL probe >= floor and trips the board's HALT.

CALIBRATION (floor_value = 3):
  - FAIL band  => a correct judge scores the probe < floor (rejects-only:
    the probe is a deliberate, unambiguous failure of THAT axis's rubric).
  - PASS-HIGH  => a correct judge scores the probe >= floor + 1.
  - FAIL-LOW   => a correct judge scores the probe <= floor - 1.

Pure data + a small spec — stdlib only, no ``manuscript`` import (keeps the
``gates/`` layer clean). The board seam renders each probe through the real
per-axis rubric (``board_lenses._render_rubric``) so the probe genuinely
exercises that axis's judge.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# The 3 ORIGINAL calibrated review-board passages (relocated verbatim from
# manuscript/review_board.py). Unique markers let a
# test mock dispatch on which passage it was handed.
# ---------------------------------------------------------------------------

_CANARY_STRONG_MARKER: str = "does not overlap at the 95% level"
_CANARY_WEAK_MARKER: str = "clearly the best survey"
_CANARY_ANNOTATED_BIB_MARKER: str = "Paper 1 studied X. Paper 2 studied Y."

# ★ CALIBRATED passages: each written to exercise ONE specific rubric
# dimension's GOOD or WEAK tell, so a correctly-calibrated judge has concrete
# textual evidence to justify every score against, not just a vibe.
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

# ★ The MANDATORY annotated-bibliography canary (D-SV-D, (c)):
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


# ---------------------------------------------------------------------------
# ★ PER-AXIS FAIL probes — one deliberate, unambiguous per-axis failure
# so EACH of the 6 cold judges is canary-verified (not just SYNTH).
# ---------------------------------------------------------------------------

# DEPTH: bare assertions, zero numbers/mechanisms/limits — DEPTH's rubric
# ("every load-bearing claim must carry its design + numbers + a limit")
# scores this well below the floor.
_CANARY_DEPTH_BARE_ASSERTION: str = """\
Our approach is substantially better than all prior work. It improves
performance considerably and works well across every setting we care about.
The method is highly effective, the gains are large, and the results are
strong. Overall the technique clearly outperforms the alternatives by a wide
margin. No specific number, threshold, dataset, or mechanism is given for any
of these claims, and no limitation is stated anywhere.
"""

# WIDTH: a draft that leans on ONE paper while the mechanical coverage diff
# (handed to the WIDTH judge as ground truth) shows a whole cluster of
# committed ``used`` papers dropped from the body — WIDTH's rubric scores a
# whole missing cluster as `critical`.
_CANARY_WIDTH_DROPPED_CLUSTER: str = """\
The field is well summarized by smith2020, which we discuss at length. Their
framework captures the essential dynamics and we build our entire narrative
around it. We do not engage the other retrieved works in the body.
"""
# The mechanical ground-truth diff the WIDTH judge is handed for the probe:
# three committed ``used`` papers (an entire retrieved cluster) never appear
# as [[citekey]] in the body above. A correct WIDTH judge FAILs this.
_CANARY_WIDTH_COVERAGE_DIFF: dict[str, Any] = {
    "used": ["smith2020", "jones2019", "lee2021", "patel2018"],
    "present": ["smith2020"],
    "missing": ["jones2019", "lee2021", "patel2018"],
}

# SELFCONT: dense unexpanded internal jargon / tool tokens / pipeline context
# a cold first-time reader cannot resolve — SELFCONT's rubric FAILs it.
_CANARY_SELFCONT_JARGON: str = """\
We push each CPk through the Qk stage and reconcile against the HFS floor;
the approve-manuscript node then consumes the _board-result.json emitted by
the batch fanout. Every DDR is keyed on its CPk handle and the RD-6 gate
blocks on a missing OKF edge. None of these acronyms, handles, or pipeline
stages is expanded or defined anywhere in the text.
"""

# ADVERS: a universal, settled-science overclaim that ignores all
# counter-evidence — the DEFAULT-SKEPTIC ADVERS judge refutes it below floor.
_CANARY_ADVERS_OVERCLAIM: str = """\
It is now universally established that X causes Y in all populations, at all
times, without exception. No study has ever found a boundary condition,
moderator, or contradicting result, so the question is completely settled and
requires no further scrutiny. Any apparent counter-example is simply an error.
"""

# INSTRUCT: recommendation gaps that name NO specific missing pointer — pure
# "more research is needed" filler — INSTRUCT's rubric FAILs this.
_CANARY_INSTRUCT_VAGUE_REC: str = """\
More research is needed in this area. Future work should explore the topic
further and additional studies would help clarify the picture. Researchers
are encouraged to investigate the open questions. We leave a fuller treatment
to future work. No specific missing citation, mechanism, dataset, or pointer
is named for any of these recommendations.
"""


# ---------------------------------------------------------------------------
# The per-axis canary spec the board seam consumes. Each entry:
#   {axis, passage, band, [coverage_diff]}
# The board seam renders `passage` through that axis's REAL rubric
# (board_lenses._render_rubric) so the probe genuinely exercises that judge.
# ---------------------------------------------------------------------------

# SYNTH keeps its 3 calibrated probes (a PASS-HIGH catches a broken-harsh
# judge; the two FAILs catch a rubber-stamp / enumeration-blind judge). Every
# OTHER axis gets one rejects-only FAIL probe.
BOARD_AXIS_CANARIES: list[dict[str, Any]] = [
    {"axis": "SYNTH", "passage": _CANARY_STRONG_PASSAGE, "band": "PASS-HIGH"},
    {"axis": "SYNTH", "passage": _CANARY_WEAK_PASSAGE, "band": "FAIL-LOW"},
    {"axis": "SYNTH", "passage": _CANARY_ANNOTATED_BIB_PASSAGE, "band": "FAIL"},
    {"axis": "DEPTH", "passage": _CANARY_DEPTH_BARE_ASSERTION, "band": "FAIL"},
    {
        "axis": "WIDTH",
        "passage": _CANARY_WIDTH_DROPPED_CLUSTER,
        "band": "FAIL",
        "coverage_diff": _CANARY_WIDTH_COVERAGE_DIFF,
    },
    {"axis": "SELFCONT", "passage": _CANARY_SELFCONT_JARGON, "band": "FAIL"},
    {"axis": "ADVERS", "passage": _CANARY_ADVERS_OVERCLAIM, "band": "FAIL"},
    {"axis": "INSTRUCT", "passage": _CANARY_INSTRUCT_VAGUE_REC, "band": "FAIL"},
]
