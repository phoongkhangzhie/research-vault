"""review/relate_check.py — PR-1/PR-2/PR-4/PR-5 relate presence check (Wave 0).

Design: docs/superpowers/specs/2026-07-08-next-gen-lit-review-loop-design.md §5,
docs/superpowers/specs/2026-07-08-okf-sufficiency-and-paper-reading.md.

WHAT THIS IS
============
The 5-move principled paper-reading protocol (the researcher's design,
grounded in Cochrane/PICO extraction discipline, Noblit & Hare
meta-ethnography's reciprocal/refutational/line-of-argument relation typing,
and Webster & Watson's concept matrix — see REFERENCES.md) fixes the READING
DISCIPLINE, never the note SCHEMA (the flexible-not-rigid constraint). This module
is the **rejects-only presence check** (charter §9) that enforces the
discipline mechanically: it verifies the mandatory questions were ANSWERED,
never how well. A PASS never certifies quality; it only fails to find a
missing mandatory answer.

THE 5 MOVES → what is checked
==============================
  1. Orient/classify   → frontmatter `contribution_kind` in CONTRIBUTION_KINDS.
  2. Exact-arrow        → unchecked here (free-form `claim`/`method` fields —
                           quality of the arrow is not mechanically checkable).
  3. Result-with-magnitude → frontmatter `result_reported: yes|no` (PR-5,
                           mandatory whitelist answer) + when `yes`, a non-empty
                           body `## Result` section.
  4. Relate to corpus   → frontmatter `paper_relations_sought: yes|no` (PR-2,
                           mandatory whitelist answer) + when `yes`, a non-empty
                           body `## Related papers` section with ≥1 typed edge.
  5. Concept edges       → unchanged this wave (PR-3 deferred to ride NG-6a's
                           refresh verb — see the design doc §5, wave ordering).

PR-4 (role/position split) is checked alongside: `role` must be one of
ROLE_TYPES; `position` must be present and non-trivial.

WHY WHITELIST, NEVER BLACKLIST (engineer memory, PR #175 delta)
=================================================================
`result_reported` / `paper_relations_sought` are agent-stamped free-ish
fields.  The presence check accepts EXACTLY `"yes"` / `"no"` (case/whitespace
tolerant) — any other spelling is a malformed answer, not a silent pass.
This is deliberate: a blacklist of "known bad" spellings cannot enumerate
every way an agent might dodge the question; a whitelist of the one/two
known-good spellings closes that hole structurally.

THE OVER-RIGIDITY GUARD (PR-2's "require tag+target, keep substance in prose")
================================================================================
A paper→paper edge line MUST carry a typed tag + target citekey (mechanical)
AND a non-trivial reasoning clause (mechanical: minimum length) — but the
CONTENT of the reasoning is never judged. A bare tag with no reasoning is
rejected (too thin); the reasoning's quality is left entirely to the
subagent's judgment (never over-rigidified).

Stdlib only.
sr: NG-lit-review-wave0 (PR-1, PR-2, PR-4, PR-5)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..note import _parse_frontmatter

# ---------------------------------------------------------------------------
# Fixed vocabularies (whitelists — the flexible-not-rigid line: these are
# the CHECKLIST answers, not a frontmatter schema straitjacket)
# ---------------------------------------------------------------------------

CONTRIBUTION_KINDS: frozenset[str] = frozenset({
    "mechanism", "theory-bound", "benchmark", "survey", "application",
})

ROLE_TYPES: frozenset[str] = frozenset({
    "methodological", "empirical", "theoretical", "counter-position",
})

# Noblit & Hare's three inter-study relation types (meta-ethnography step 4),
# mapped onto rv's existing [SUPPORTS]/[CONTRADICTS]/[PARTIAL]/[EXTENDS] bracket
# convention. reciprocal≈SUPPORTS, refutational≈CONTRADICTS, line-of-argument
# ≈PARTIAL/EXTENDS (per the design doc §5, PR-2).
RELATION_TYPES: frozenset[str] = frozenset({
    "reciprocal", "refutational", "line-of-argument",
})

_RELATION_TAGS: frozenset[str] = frozenset({
    "SUPPORTS", "CONTRADICTS", "PARTIAL", "EXTENDS",
})

_YES_NO: frozenset[str] = frozenset({"yes", "no"})

# Minimum non-trivial length for a "substance" clause (position narrative,
# a paper->paper edge's reasoning). Guards against a placeholder one-word
# answer without pretending to judge quality.
_MIN_SUBSTANCE_CHARS = 15

# ---------------------------------------------------------------------------
# Body-section parsing
# ---------------------------------------------------------------------------

_RESULT_HEADING_RE = re.compile(r"^#{2,3}\s+Result\s*$", re.IGNORECASE | re.MULTILINE)
_RELATED_PAPERS_HEADING_RE = re.compile(
    r"^#{2,3}\s+Related papers\s*$", re.IGNORECASE | re.MULTILINE
)
# "- [SUPPORTS] xiong2023-stepwise — <reason> (reciprocal)"
_PAPER_EDGE_RE = re.compile(
    r"^\s*-\s*\[(SUPPORTS|CONTRADICTS|PARTIAL|EXTENDS)\]\s+"
    r"([A-Za-z0-9][A-Za-z0-9_.\-]*)\s*(?:—|-)\s*(.+?)\s*"
    r"\((reciprocal|refutational|line-of-argument)\)\s*$",
    re.MULTILINE,
)


def _find_section_body(body: str, heading_re: "re.Pattern[str]") -> str | None:
    """Return the text between a matched heading and the next heading (or EOF).

    Returns None if the heading is absent. An EMPTY string means the heading
    exists but has no content — a meaningful distinction the presence check
    relies on (heading absent = move not attempted; heading present-but-blank
    = move attempted but nothing recorded).
    """
    m = heading_re.search(body)
    if m is None:
        return None
    start = m.end()
    next_m = re.search(r"^#{1,3}\s+\S", body[start:], re.MULTILINE)
    end = start + next_m.start() if next_m else len(body)
    return body[start:end].strip()


def parse_paper_relations(body: str) -> list[dict[str, str]]:
    """Parse PR-2 paper→paper typed edges from a note body's '## Related papers'
    section.

    Distinct from the existing paper→concept edges (which target
    ``concepts/<c>.md`` and live anywhere in the body) — a paper→paper edge
    targets a bare citekey and lives inside the dedicated '## Related papers'
    section, so the two edge kinds never collide during parsing.

    Returns a list of dicts: {"tag", "target", "reason", "type"}. Empty list
    if the section is absent or contains no matching edge lines.
    """
    section = _find_section_body(body, _RELATED_PAPERS_HEADING_RE)
    if not section:
        return []
    out: list[dict[str, str]] = []
    for m in _PAPER_EDGE_RE.finditer(section):
        tag, target, reason, rel_type = m.groups()
        out.append({
            "tag": tag,
            "target": target,
            "reason": reason.strip(),
            "type": rel_type,
        })
    return out


# ---------------------------------------------------------------------------
# The presence-check result
# ---------------------------------------------------------------------------

@dataclass
class RelatePresenceResult:
    """Rejects-only presence-check result. ``ok`` is True iff `findings` is
    empty — a PASS never certifies quality, it only fails to find a missing
    mandatory answer (charter §9)."""

    findings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings


def _get_scalar(fields: dict, key: str) -> str:
    val = fields.get(key, "")
    if isinstance(val, str):
        return val.strip()
    return ""


def check_relate_presence(note_path: Path) -> RelatePresenceResult:
    """Rejects-only presence check for a relate-<key> literature note (PR-1).

    Verifies the mandatory-question checklist was answered — NOT a rigid
    frontmatter schema; a note that answers every question in unconventional
    prose still passes. Missing/malformed answers are cheap FAILs (Move 1/3/4
    of the 5-move protocol, plus PR-4's role/position split).

    Args:
        note_path: absolute path to the literature/<citekey>.md note.

    Returns:
        RelatePresenceResult — .ok True iff no findings.
    """
    findings: list[str] = []

    if not note_path.exists():
        return RelatePresenceResult(findings=[f"note does not exist: {note_path}"])

    try:
        text = note_path.read_text(encoding="utf-8")
    except OSError as e:
        return RelatePresenceResult(findings=[f"cannot read note {note_path}: {e}"])

    fields, body = _parse_frontmatter(text)

    # ── Move 1: orient/classify ────────────────────────────────────────────
    contribution_kind = _get_scalar(fields, "contribution_kind").lower()
    if not contribution_kind:
        findings.append(
            "missing 'contribution_kind' (Move 1 — orient/classify the "
            f"contribution kind; one of {sorted(CONTRIBUTION_KINDS)})"
        )
    elif contribution_kind not in CONTRIBUTION_KINDS:
        findings.append(
            f"'contribution_kind' has unrecognized value {contribution_kind!r} "
            f"(must be one of {sorted(CONTRIBUTION_KINDS)})"
        )

    # ── PR-4: role + position (split of the old overloaded 'stance') ──────
    role = _get_scalar(fields, "role").lower()
    if not role:
        findings.append(
            f"missing 'role' (PR-4 — categorical tag; one of {sorted(ROLE_TYPES)})"
        )
    elif role not in ROLE_TYPES:
        findings.append(
            f"'role' has unrecognized value {role!r} (must be one of {sorted(ROLE_TYPES)})"
        )

    position = _get_scalar(fields, "position")
    if not position:
        findings.append(
            "missing 'position' (PR-4 — free-form narrative; how this paper "
            "relates to the review question, in the subagent's own words)"
        )
    elif len(position) < _MIN_SUBSTANCE_CHARS:
        findings.append(
            f"'position' is too thin ({len(position)} chars) to be a real "
            "narrative — a placeholder, not a considered answer"
        )

    # ── Move 3 / PR-5: result-with-magnitude, mandatory whitelist answer ──
    result_reported = _get_scalar(fields, "result_reported").lower()
    if not result_reported:
        findings.append(
            "missing 'result_reported' (Move 3 / PR-5 — mandatory: 'yes' if "
            "the paper reports a quantitative result, else 'no')"
        )
    elif result_reported not in _YES_NO:
        findings.append(
            f"'result_reported' has unrecognized value {result_reported!r} "
            "(must be exactly 'yes' or 'no' — fail-closed on any other spelling)"
        )
    elif result_reported == "yes":
        result_section = _find_section_body(body, _RESULT_HEADING_RE)
        if result_section is None:
            findings.append(
                "'result_reported: yes' but no '## Result' body section — "
                "Move 3 requires the magnitude + conditions + limitations "
                "when the paper reports a quantitative result"
            )
        elif len(result_section) < _MIN_SUBSTANCE_CHARS:
            findings.append(
                "'## Result' section is present but empty/too thin — "
                "record the magnitude, conditions, and stated limitations"
            )

    # ── Move 4 / PR-2: paper→paper relations, mandatory whitelist answer ──
    relations_sought = _get_scalar(fields, "paper_relations_sought").lower()
    if not relations_sought:
        findings.append(
            "missing 'paper_relations_sought' (Move 4 / PR-2 — mandatory: "
            "'yes' if this paper bears on any corpus paper, else 'no' after "
            "having checked)"
        )
    elif relations_sought not in _YES_NO:
        findings.append(
            f"'paper_relations_sought' has unrecognized value {relations_sought!r} "
            "(must be exactly 'yes' or 'no' — fail-closed on any other spelling)"
        )
    elif relations_sought == "yes":
        edges = parse_paper_relations(body)
        if not edges:
            findings.append(
                "'paper_relations_sought: yes' but no typed paper→paper edge "
                "found in a '## Related papers' body section — Move 4 requires "
                "at least one '[SUPPORTS|CONTRADICTS|PARTIAL|EXTENDS] <citekey> "
                "— <reason> (reciprocal|refutational|line-of-argument)' line"
            )
        else:
            for edge in edges:
                if len(edge["reason"]) < _MIN_SUBSTANCE_CHARS:
                    findings.append(
                        f"paper→paper edge to {edge['target']!r} carries a bare "
                        "tag with no real reasoning — a relation reduced to a "
                        "tag with no substance is as thin as no relation "
                        "(the over-rigidity guard, §5 caveat)"
                    )

    return RelatePresenceResult(findings=findings)
