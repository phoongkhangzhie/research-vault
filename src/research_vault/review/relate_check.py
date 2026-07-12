# SPDX-License-Identifier: AGPL-3.0-or-later
"""review/relate_check.py relate presence check (Wave 0).

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
  3. Result-with-magnitude → frontmatter `result_reported: yes|no` (
                           mandatory whitelist answer) + when `yes`, a non-empty
                           body `## Result` section.
  4. Relate to corpus → frontmatter `paper_relations_sought: yes|no` (
                           mandatory whitelist answer) + when `yes`, a canonical
                           `## Related papers` heading present + ≥1 typed edge
                           found anywhere in the body (full-body scan).
  5. Concept edges → mandatory gating unchanged this wave (deferred
                           to ride NG-6a's refresh verb), but the edge FORMAT
                           was migrated to OKF markdown links alongside paper
                           edges (Defect #70), and `parse_concept_edges` now
                           mechanically parses them (previously prose-only).

RETRIEVAL-TIER GATES EDGE STRENGTH (Defect #71)
================================================================================
A note whose `read_basis` is not exactly `"full-text"` (abstract-only,
title-only, any other value, or unstamped) cannot carry a `SUPPORTS` or
`CONTRADICTS` edge — of EITHER kind (paper→paper or paper→concept). The
paper was never read at the fidelity needed to assert or refute a claim at
that strength; the strongest permissible type at that retrieval tier is
`PARTIAL`. Fail-closed: an absent/unstamped `read_basis` is treated as
NOT full-text — never a free pass to claim full strength by omission.

The (role/position split) is checked alongside: `role` must be one of
ROLE_TYPES; `position` must be present and non-trivial.

WHY WHITELIST, NEVER BLACKLIST
=================================================================
`result_reported` / `paper_relations_sought` are agent-stamped free-ish
fields.  The presence check accepts EXACTLY `"yes"` / `"no"` (case/whitespace
tolerant) — any other spelling is a malformed answer, not a silent pass.
This is deliberate: a blacklist of "known bad" spellings cannot enumerate
every way an agent might dodge the question; a whitelist of the one/two
known-good spellings closes that hole structurally.

THE OVER-RIGIDITY GUARD ("require tag+target, keep substance in prose")
================================================================================
A paper→paper edge line MUST carry a typed tag + target citekey (mechanical)
AND a non-trivial reasoning clause (mechanical: minimum length) — but the
CONTENT of the reasoning is never judged. A bare tag with no reasoning is
rejected (too thin); the reasoning's quality is left entirely to the
subagent's judgment (never over-rigidified).

THE TAG IS AUTHORITATIVE, `(kind)` IS AN OPTIONAL MIRROR
================================================================================
The prose TYPE token (`SUPPORTS:`/`CONTRADICTS:`/`PARTIAL:`/`EXTENDS:`) is
required and derives the Noblit & Hare relation kind mechanically
(SUPPORTS→reciprocal, CONTRADICTS→refutational, PARTIAL/EXTENDS→line-of-
argument). The trailing `(kind)` suffix is an OPTIONAL human-readable mirror
— same "ledger wins, body mirrors" precedent as `key_equations`'s
`*(critical)*` tag. If a stated `(kind)` disagrees with the tag-derived
kind, the TAG WINS and the edge carries a `kind_mismatch` field so the
disagreement is surfaced, never silently resolved one way. Requiring
`(kind)` (an earlier shape) meant a valid edge that simply omitted it lost
the WHOLE edge — the single most likely malformation maximized silent
loss. Optional-and-derived closes that hole.

SURFACE MALFORMED EDGES, NEVER SILENTLY SKIP (the load-bearing fix)
================================================================================
An earlier `parse_paper_relations` used `finditer` over a strict regex and
silently dropped any non-matching line — a note with 3 edges where 1 is
typo'd would pass with that edge invisibly lost, and since
`review_synthesize_tips` instructs "traverse, don't re-derive," a lost edge
was gone for good. Now: any edge-shaped line found ANYWHERE in the body
(full-body scan, Defect #70 — not scoped to a heading) that does not parse
to a valid edge is collected into `ParsedRelations.malformed` — surfaced by
`parse_paper_relations`, `relations_report`, AND `check_relate_presence` (a
hard FAIL, matching this module's existing rejects-only-FAIL posture). A
plain `- ` bullet with no markdown link, or a link elsewhere in the note
that is neither a candidate edge target nor accompanied by a plausible
type-token attempt, is legitimate prose and is never flagged — see
`_looks_like_tag_attempt`, the false-positive-free signal that separates a
broken edge attempt from prose once scanning is no longer header-scoped
(extended for full-body scan).

DEFECT #70 — FULL-BODY SCAN, NOT HEADER-SCOPED
================================================================================
The pre-fix parser only looked inside the exact `## Related papers`
heading's slice — an agent that misspelled the heading (or a heading that
somehow split/duplicated) caused every edge under it to be silently
dropped: absent from `.edges` AND absent from `.malformed` (worse than a
malformed FAIL — a genuine SILENT loss with no signal at all).
`parse_paper_relations`/`parse_concept_edges` now scan the FULL body; the
canonical heading's presence is checked SEPARATELY and unconditionally by
`check_relate_presence` (Move 4) as its own structural requirement, so a
missing/misspelled heading is still flagged even when the full-body scan
recovers the edges.

OKF-CONFORMANT MARKDOWN-LINK EDGE FORMAT (the real #69 root cause)
================================================================================
See the "Body-section parsing" section below for the full grounding. In
short: relate agents naturally write OKF-conformant markdown links
(`[display](/literature/<citekey>.md)`); an earlier parser demanded a bare
citekey token and rejected the (correct) markdown-link form. This module
accepts+requires the markdown-link form for BOTH paper→paper and
paper→concept edges, aligning rv to OKF rather than the reverse.

RELATIONSHIP TYPE IS A PROSE TOKEN, NOT A LINK-PREFIX TAG (OKF conformance)
================================================================================
The Open Knowledge Format spec cross-links notes with plain markdown links
and states the relationship type belongs IN PROSE, not encoded as a marker
attached to the link. An earlier rv convention put the type in a bracketed
prefix ahead of the link (`- [SUPPORTS] [display](/literature/x.md) —
reason`) — non-conformant: a plain OKF reader sees `[SUPPORTS]` as a second,
unrelated link-like token, not a relationship type. The conformant form
moves the type into the prose clause after the link, as a leading token
before the reason (`- [display](/literature/x.md) — SUPPORTS: reason`) — a
plain OKF reader sees one ordinary markdown link followed by an ordinary
sentence; rv's typed traversal reads the leading `TYPE:` token mechanically.
The optional trailing `(kind)` mirror is unchanged.

Candidate detection flips accordingly (see `_scan_edge_lines`): the primary
signal is now the markdown LINK itself (a bulleted line opening with a link
to `/literature/` or `/concepts/`), not a bracket immediately after the
bullet. A line matching that link shape whose prose lacks a valid `TYPE:`
token is malformed (an edge attempt with a missing/typo'd type). A line
that opens `- [` but whose link is missing/broken — yet still carries a
plausible type-token attempt — is also surfaced as malformed, never
silently treated as ordinary prose (`_looks_like_tag_attempt` kept as this
secondary recovery).

Stdlib only.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
# mapped onto rv's existing SUPPORTS/CONTRADICTS/PARTIAL/EXTENDS prose-token
# convention. reciprocal≈SUPPORTS, refutational≈CONTRADICTS, line-of-argument
# ≈PARTIAL/EXTENDS (per the design doc).
RELATION_TYPES: frozenset[str] = frozenset({
    "reciprocal", "refutational", "line-of-argument",
})

_RELATION_TAGS: frozenset[str] = frozenset({
    "SUPPORTS", "CONTRADICTS", "PARTIAL", "EXTENDS",
})

# The type token derives the kind mechanically — SSOT for the derivation
# ("the type is authoritative"). A stated (kind) suffix
# that disagrees with this mapping is a mirror-mismatch, never a second
# source of truth.
_TAG_TO_KIND: dict[str, str] = {
    "SUPPORTS": "reciprocal",
    "CONTRADICTS": "refutational",
    "PARTIAL": "line-of-argument",
    "EXTENDS": "line-of-argument",
}

_YES_NO: frozenset[str] = frozenset({"yes", "no"})

# Minimum non-trivial length for a "substance" clause (position narrative,
# a paper->paper edge's reasoning). Guards against a placeholder one-word
# answer without pretending to judge quality.
_MIN_SUBSTANCE_CHARS = 15

# ---------------------------------------------------------------------------
# Misdiagnosis fixes (pre-publish adopter-facing fix): two flat-frontmatter
# authoring mistakes an LLM-authored note commonly hits produce a MISLEADING
# presence-check message rather than a correct one. Fail-closed is unchanged
# (both cases still reject) — only the DIAGNOSTIC improves.
# ---------------------------------------------------------------------------

# A bare YAML block-scalar marker (optionally with chomping indicator).
# `note._parse_frontmatter` reads only the marker line for a flat field —
# the indented body lines below it never fold in (no `key:` shape), so the
# field parses to this degenerate ~1-char value.
_BLOCK_SCALAR_MARKERS: frozenset[str] = frozenset({">", "|", ">-", ">+", "|-", "|+"})


def _run_together_hint(fields: dict, missing_key: str) -> str | None:
    """Detect the "two fields glued onto one physical line" authoring
    mistake: the flat-frontmatter parser reads one field per line, so when
    an agent forgets the newline between two fields (e.g. `position: "...
    narrative." contribution_kind: benchmark`), the second field's
    `key: value` is absorbed into the first field's parsed VALUE — the
    second field then reads as MISSING even though it was written, just
    mis-attached to the wrong field.

    Scans every OTHER parsed field's string value for `<missing_key>:`
    appearing inside it. Returns a hint string naming BOTH fields if found,
    else None (falls through to the normal "missing" message).
    """
    pattern = re.compile(rf"(?:^|\s){re.escape(missing_key)}:(?:\s|$)")
    for other_key, other_val in fields.items():
        if other_key == missing_key or not isinstance(other_val, str):
            continue
        if pattern.search(other_val):
            return (
                f"field '{missing_key}' looks run-together with '{other_key}' "
                "on one physical line (no newline between them) — the flat-"
                "frontmatter parser glued the second field's key+value into "
                f"the first field's value. Put each field on its own line "
                f"(e.g. '{other_key}: ...' on one line, then "
                f"'{missing_key}: ...' on the next)."
            )
    return None


def _block_scalar_hint(field_key: str, raw_val: str) -> str | None:
    """Detect a YAML block-scalar marker (`>`/`|`) on a flat field. Flat
    frontmatter requires a single-line quoted scalar; a block scalar's real
    content lives on indented lines the flat parser never folds in, so the
    field parses to a degenerate ~1-char value (just the marker) — reported
    as "too thin" otherwise, which is misleading: the content may be rich,
    just formatted as a block scalar this note format doesn't support.
    """
    stripped = raw_val.strip()
    if stripped in _BLOCK_SCALAR_MARKERS:
        return (
            f"field '{field_key}' looks like a YAML block scalar (`{stripped}`) "
            "— flat frontmatter requires a single-line quoted value; a block "
            "scalar's indented body is not read here. Put the content on one "
            f"line, e.g. '{field_key}: \"...\"'."
        )
    return None

# ---------------------------------------------------------------------------
# Body-section parsing
#
# OKF-CONFORMANT EDGE FORMAT (Defects #69/#70/#71 hardening; relationship
# type as a prose token, not a link-prefix tag)
# ================================================================================
# Google Cloud's OKF spec cross-links notes with STANDARD MARKDOWN LINKS —
# explicitly NOT wikilinks — in the absolute bundle-relative form
# `[display text](/type/<slug>.md)` (begins with `/`, resolved relative to
# the bundle root; recommended for stability). rv's bundle root for this
# resolution is the PROJECT NOTES DIR (`cfg.project_notes_dir(project)`) —
# the directory that directly contains `literature/`, `concepts/`, `mocs/`,
# etc. — so `/literature/<citekey>.md` and `/concepts/<slug>.md` resolve
# there. The OKF spec also states the relationship type belongs IN PROSE,
# not encoded onto the link — rv puts a typed `TYPE:` token (SUPPORTS/
# CONTRADICTS/PARTIAL/EXTENDS) at the start of the trailing reason clause,
# so a mechanical Noblit & Hare traversal is possible while a plain OKF
# reader still sees a valid markdown link followed by an ordinary sentence.
#
# THE REAL #69 ROOT CAUSE: relate agents naturally write markdown links —
# e.g. `- [Baltaji 2024](/literature/baltajipersonainconstancymulti2024.md)
# — SUPPORTS: reason`, which is OKF-conformant — but an earlier parser
# demanded a BARE citekey token and silently rejected the (correct)
# markdown-link form. This aligns rv's parser to OKF rather than asking
# agents to write non-conformant bare citekeys.
# ---------------------------------------------------------------------------

_RESULT_HEADING_RE = re.compile(r"^#{2,3}\s+Result\s*$", re.IGNORECASE | re.MULTILINE)
_RELATED_PAPERS_HEADING_RE = re.compile(
    r"^#{2,3}\s+Related papers\s*$", re.IGNORECASE | re.MULTILINE
)
# Canonical concept-edges heading (Defect #70 migration): was "## Verified
# concept edges" in the old bare-path brief; canonicalized to "## Concept
# edges" alongside the OKF markdown-link format.
_CONCEPT_EDGES_HEADING_RE = re.compile(
    r"^#{2,3}\s+Concept edges\s*$", re.IGNORECASE | re.MULTILINE
)

# The four rv-typed relation tags (SSOT — see also _TAG_TO_KIND below).
_KNOWN_TAGS: tuple[str, ...] = ("SUPPORTS", "CONTRADICTS", "PARTIAL", "EXTENDS")


def _looks_like_tag_attempt(word: str) -> bool:
    """True iff ``word`` is a plausible (mis-spelled) attempt at one of the
    four known relation types — NOT an unrelated word that happens to be
    followed by a colon elsewhere in a note body (e.g. ``note:``, ``e.g.:``).

    Defect #70 (full-body scan): once edge detection is no longer confined
    to the '## Related papers' / '## Concept edges' sections, the bare
    "any '- [' bullet is an edge attempt" heuristic (safe when scoped to a
    single known section) becomes too broad across an entire note body.
    Requiring near-similarity (``difflib`` ratio) to one of the four known
    types keeps the true positive (a typo'd type: 'SUPRTS', 'CONTRADCTS')
    while excluding unrelated colon-terminated prose words, which sit far
    below the similarity cutoff.
    """
    word = word.strip().upper()
    if not word or not word.isalpha():
        return False
    if word in _KNOWN_TAGS:
        return True
    return bool(difflib.get_close_matches(word, _KNOWN_TAGS, n=1, cutoff=0.6))


# The PRIMARY candidate signal (OKF conformance: relationship type moved
# out of a link-prefix tag into a prose token — see the module docstring).
# A bulleted markdown link whose target is `/literature/` or `/concepts/`
# is unambiguously an attempted edge, regardless of what follows it.
_LINK_PROBE_RE = re.compile(
    r"^-\s*\[[^\]]+\]\(/(?:literature|concepts)/[A-Za-z0-9][A-Za-z0-9_.\-]*\.md\)"
)

# A lax "is this line even bracket-bulleted" probe — the SECONDARY recovery
# signal (fork 5): a bracket-opened bullet whose link is missing/broken but
# whose content still carries a plausible type attempt must still surface
# as malformed, never silently fall through as ordinary prose. Two shapes
# feed this recovery: (a) an old-convention bracket-tag immediately after
# the bullet (`- [SUPRTS] ...`, no markdown link at all — the FIRST
# bracket's own content is checked), and (b) a broken/missing link whose
# trailing prose still carries a `TYPE:` colon token attempt.
_BRACKET_OPEN_RE = re.compile(r"^-\s*\[")
_FIRST_BRACKET_RE = re.compile(r"^-\s*\[([^\]]*)\]")

# Any colon-terminated word in the line — candidates for the secondary
# recovery's type-token similarity check.
_COLON_TOKEN_RE = re.compile(r"\b([A-Za-z]+):")

# THE OKF EDGE LINE GRAMMAR — one regex covers BOTH edge kinds (paper→paper
# and paper→concept); they are distinguished after the fact by which bundle
# directory the link targets (`literature/` vs `concepts/`). Relationship
# type is a PROSE TOKEN (OKF-conformant), not a link-prefix tag:
#   - [Baltaji 2024](/literature/baltaji2024.md) — SUPPORTS: <reason>
#   - [WEIRD default](/concepts/western-consensus-default.md) — SUPPORTS: <reason>
# The trailing `(reciprocal|refutational|line-of-argument)` mirror is
# OPTIONAL and only meaningful for
# paper→paper edges — a concept edge has no Noblit & Hare kind mapping.
_EDGE_LINE_RE = re.compile(
    r"^-\s*\[([^\]]+)\]\(/(literature|concepts)/([A-Za-z0-9][A-Za-z0-9_.\-]*)\.md\)\s*"
    r"(?:—|-)\s*(SUPPORTS|CONTRADICTS|PARTIAL|EXTENDS):\s*(.+?)\s*"
    r"(?:\((reciprocal|refutational|line-of-argument)\))?\s*$"
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


@dataclass
class ParsedRelations:
    """The result of parsing a note body's paper→paper typed edges.

    ``edges``     — successfully-parsed paper→paper typed edges, each an
                    OKF markdown-link edge with the relationship type as a
                    prose token (``[display](/literature/<citekey>.md) —
                    TYPE: reason``).
    ``malformed`` — raw text of any edge-shaped line (an unambiguously
                    attempted typed edge — a typo'd type, a missing/broken
                    link, a non-OKF bare-citekey/path, etc.) that did NOT
                    parse to a valid edge. NEVER silently dropped
                    (the load-bearing fix) — a caller
                    that ignores this list re-introduces the exact
                    silent-loss defect the fix closes. Free-form prose
                    (including unrelated bracket markers like ``[TODO]``)
                    is excluded from this list — see
                    ``_looks_like_tag_attempt``.

    Defect #70 (full-body scan): this is a FULL-BODY scan, not scoped to
    the '## Related papers' heading — a misplaced/misspelled section
    header can no longer silently drop a well-formed edge. The canonical
    heading's PRESENCE is checked separately (see
    ``check_relate_presence``'s Move 4 section) as an independent
    structural requirement.
    """

    edges: list[dict[str, Any]] = field(default_factory=list)
    malformed: list[str] = field(default_factory=list)


@dataclass
class ParsedConceptEdges:
    """The result of parsing a note body's paper→concept typed edges
    (``[display](/concepts/<slug>.md) — TYPE: reason``).

    ``edges`` — successfully-parsed paper→concept typed edges: {"tag",
    "target", "reason"}. No ``type``/``kind_mismatch`` fields — concept
    edges have no Noblit & Hare relation-kind mapping (that mapping is
    paper→paper specific).

    No separate ``malformed`` list here: an edge-shaped line that fails to
    parse as EITHER a paper edge or a concept edge is already surfaced once
    via ``ParsedRelations.malformed`` (from ``parse_paper_relations``,
    called on the same body) — duplicating it here would double-report the
    identical line under two different findings.
    """

    edges: list[dict[str, Any]] = field(default_factory=list)


def _scan_edge_lines(
    body: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Scan the FULL note body (never header-scoped — Defect #70) for
    edge-shaped lines, classifying each into paper→paper edges, paper→
    concept edges, or malformed.

    Candidate detection (OKF conformance — relationship type is a prose
    token, not a link-prefix tag; see the module docstring):

      PRIMARY  — a bulleted markdown link whose target is `/literature/`
                 or `/concepts/` (``_LINK_PROBE_RE``) is unambiguously an
                 attempted edge, regardless of what follows the link.
      SECONDARY (fork 5, "over-rigidity" recovery) — a line that opens
                 ``- [`` (a bracket-opened bullet, so plausibly an edge
                 attempt) but whose link is missing/broken still surfaces
                 as malformed if the line ALSO carries a colon-terminated
                 word that closely resembles one of the four known types
                 (``_looks_like_tag_attempt``) — never silently treated as
                 ordinary prose just because the link itself is broken.

    A line satisfying NEITHER signal is ordinary prose (e.g. a plain ``- ``
    bullet, an unrelated ``[TODO]``/``[1]`` marker, a markdown link to some
    other bundle/URL with no type-token attempt nearby) and is never
    flagged.

    Returns (paper_edges, concept_edges, malformed_lines).
    """
    paper_edges: list[dict[str, Any]] = []
    concept_edges: list[dict[str, Any]] = []
    malformed: list[str] = []

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        link_probe = _LINK_PROBE_RE.match(line)
        if link_probe is None:
            if not _BRACKET_OPEN_RE.match(line):
                continue  # not bracket-opened at all — ordinary prose
            first_bracket = _FIRST_BRACKET_RE.match(line)
            first_bracket_word = first_bracket.group(1) if first_bracket else ""
            has_tag_signal = _looks_like_tag_attempt(first_bracket_word) or any(
                _looks_like_tag_attempt(tok) for tok in _COLON_TOKEN_RE.findall(line)
            )
            if not has_tag_signal:
                continue  # bracket-opened but no plausible type attempt — ordinary prose
            malformed.append(line)  # secondary recovery: broken/missing link, real type attempt
            continue

        m = _EDGE_LINE_RE.match(line)
        if m is None:
            malformed.append(line)
            continue

        _display, kind_dir, slug, tag, reason, stated_kind = m.groups()
        reason = reason.strip()
        if kind_dir == "literature":
            derived_kind = _TAG_TO_KIND[tag]
            kind_mismatch = None
            if stated_kind is not None and stated_kind != derived_kind:
                kind_mismatch = {"stated": stated_kind, "derived": derived_kind}
            paper_edges.append({
                "tag": tag,
                "target": slug,
                "reason": reason,
                "type": derived_kind,
                "kind_mismatch": kind_mismatch,
            })
        else:  # kind_dir == "concepts"
            concept_edges.append({"tag": tag, "target": slug, "reason": reason})

    return paper_edges, concept_edges, malformed


def parse_paper_relations(body: str) -> ParsedRelations:
    """Parse paper→paper typed edges from a note body (full-body scan
    — Defect #70; see ``_scan_edge_lines``).

    Each edge dict: {"tag", "target", "reason", "type", "kind_mismatch"}.
    ``target`` is the citekey extracted from the OKF markdown link
    ``/literature/<citekey>.md``. ``type`` is ALWAYS the tag-derived kind
    (the tag wins — see module docstring); ``kind_mismatch`` is ``None``
    unless a stated ``(kind)`` suffix disagreed with the tag, in which case
    it is ``{"stated": <kind>, "derived": <kind>}`` — surfaced, never
    silently resolved.

    Returns ``ParsedRelations(edges=[], malformed=[])`` if no bracket-tag
    lines are found anywhere in the body.
    """
    edges, _concept_edges, malformed = _scan_edge_lines(body)
    return ParsedRelations(edges=edges, malformed=malformed)


def parse_concept_edges(body: str) -> ParsedConceptEdges:
    """Parse Move 5 paper→concept typed edges from a note body (full-body
    scan — Defect #70; see ``_scan_edge_lines``).

    Each edge dict: {"tag", "target", "reason"}. ``target`` is the concept
    slug extracted from the OKF markdown link ``/concepts/<slug>.md``.
    """
    _paper_edges, edges, _malformed = _scan_edge_lines(body)
    return ParsedConceptEdges(edges=edges)


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


def check_relate_presence(note_path: Path, *, text: str | None = None) -> RelatePresenceResult:
    """Rejects-only presence check for a relate-<key> literature note.

    Verifies the mandatory-question checklist was answered — NOT a rigid
    frontmatter schema; a note that answers every question in unconventional
    prose still passes. Missing/malformed answers are cheap FAILs (Move 1/3/4
    of the 5-move protocol, plus role/position split).

    Args:
        note_path: absolute path to the literature/<citekey>.md note (used
            for error messages, and to read from when ``text`` is None).
        text: the two-layer literature store splits a note into
            a central core (Move 1/3/4 intrinsic fields) + a thin per-project
            overlay (Move 4's edges land here too, by explicit fast-follow
            deferral — see note.check_two_layer_invariants) + role/position
            (Move-4's split). Callers checking a two-layer note MUST pass
            the ASSEMBLED (core+overlay merged) text here — reading
            note_path alone would only see the overlay's thin fields and
            false-FAIL on every core-only checklist item. When ``text`` is
            None, falls back to reading ``note_path`` directly (single-file
            callers / tests).

    Returns:
        RelatePresenceResult — .ok True iff no findings.
    """
    findings: list[str] = []

    if text is None:
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
            _run_together_hint(fields, "contribution_kind")
            or (
                "missing 'contribution_kind' (Move 1 — orient/classify the "
                f"contribution kind; one of {sorted(CONTRIBUTION_KINDS)})"
            )
        )
    elif contribution_kind not in CONTRIBUTION_KINDS:
        findings.append(
            _block_scalar_hint("contribution_kind", contribution_kind)
            or (
                f"'contribution_kind' has unrecognized value {contribution_kind!r} "
                f"(must be one of {sorted(CONTRIBUTION_KINDS)})"
            )
        )

    # ── role + position (split of the old overloaded 'stance') ──────
    role = _get_scalar(fields, "role").lower()
    if not role:
        findings.append(
            _run_together_hint(fields, "role")
            or f"missing 'role' (categorical tag; one of {sorted(ROLE_TYPES)})"
        )
    elif role not in ROLE_TYPES:
        findings.append(
            _block_scalar_hint("role", role)
            or f"'role' has unrecognized value {role!r} (must be one of {sorted(ROLE_TYPES)})"
        )

    position = _get_scalar(fields, "position")
    if not position:
        findings.append(
            _run_together_hint(fields, "position")
            or (
                "missing 'position' (free-form narrative; how this paper "
                "relates to the review question, in the subagent's own words)"
            )
        )
    elif len(position) < _MIN_SUBSTANCE_CHARS:
        findings.append(
            _block_scalar_hint("position", position)
            or (
                f"'position' is too thin ({len(position)} chars) to be a real "
                "narrative — a placeholder, not a considered answer"
            )
        )

    # ── Move 3: result-with-magnitude, mandatory whitelist answer ──
    result_reported = _get_scalar(fields, "result_reported").lower()
    if not result_reported:
        findings.append(
            _run_together_hint(fields, "result_reported")
            or (
                "missing 'result_reported' (Move 3 /  mandatory: 'yes' if "
                "the paper reports a quantitative result, else 'no')"
            )
        )
    elif result_reported not in _YES_NO:
        findings.append(
            _block_scalar_hint("result_reported", result_reported)
            or (
                f"'result_reported' has unrecognized value {result_reported!r} "
                "(must be exactly 'yes' or 'no' — fail-closed on any other spelling)"
            )
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

    # Parsed once — full-body scan (Defect #70), shared by Move 4 (paper
    # edges + malformed) and the Defect #71 retrieval-tier gate below (both
    # edge kinds).
    parsed_relations = parse_paper_relations(body)
    parsed_concepts = parse_concept_edges(body)

    # ── Move 4: paper→paper relations, mandatory whitelist answer ──
    relations_sought = _get_scalar(fields, "paper_relations_sought").lower()
    if not relations_sought:
        findings.append(
            _run_together_hint(fields, "paper_relations_sought")
            or (
                "missing 'paper_relations_sought' (Move 4 /  mandatory: "
                "'yes' if this paper bears on any corpus paper, else 'no' after "
                "having checked)"
            )
        )
    elif relations_sought not in _YES_NO:
        findings.append(
            _block_scalar_hint("paper_relations_sought", relations_sought)
            or (
                f"'paper_relations_sought' has unrecognized value {relations_sought!r} "
                "(must be exactly 'yes' or 'no' — fail-closed on any other spelling)"
            )
        )
    elif relations_sought == "yes":
        # Defect #70(a): the canonical heading is required as an independent
        # structural signal — even though the full-body scan below already
        # finds edge-shaped lines anywhere, downstream traversal
        # (review.relations_report / review-synthesize) expects this exact
        # heading, so a misplaced/misspelled heading is still flagged.
        if _find_section_body(body, _RELATED_PAPERS_HEADING_RE) is None:
            findings.append(
                "'paper_relations_sought: yes' but no canonical '## Related "
                "papers' heading found — the heading is required even though "
                "a full-body scan may still have found edge-shaped lines "
                "elsewhere; downstream traversal (review.relations_report / "
                "review-synthesize) expects this exact heading (Defect #70)"
            )
        if not parsed_relations.edges and not parsed_relations.malformed:
            findings.append(
                "'paper_relations_sought: yes' but no typed paper→paper edge "
                "found anywhere in the body — Move 4 requires at least one "
                "'[display](/literature/<citekey>.md) — "
                "SUPPORTS|CONTRADICTS|PARTIAL|EXTENDS: <reason>' line under a "
                "'## Related papers' heading (a trailing '(kind)' mirror is "
                "optional)"
            )

    for edge in parsed_relations.edges:
        if len(edge["reason"]) < _MIN_SUBSTANCE_CHARS:
            findings.append(
                f"paper→paper edge to {edge['target']!r} carries a bare "
                "tag with no real reasoning — a relation reduced to a "
                "tag with no substance is as thin as no relation "
                "(the over-rigidity guard, caveat)"
            )

    # The load-bearing fix: a malformed edge-shaped line
    # is a hard FAIL unconditionally — surfacing it never depends on the
    # yes/no answer (or even on that field being well-formed), because an
    # edge-shaped line is unambiguously an attempted edge regardless of
    # what the note's checklist fields claim.
    for bad_line in parsed_relations.malformed:
        findings.append(
            f"malformed edge line: {bad_line!r} — an edge-shaped line must "
            "parse to '[display](/literature/<citekey>.md) — "
            "SUPPORTS|CONTRADICTS|PARTIAL|EXTENDS: <reason>' (paper→paper) "
            "or '[display](/concepts/<slug>.md) — SUPPORTS|CONTRADICTS|"
            "PARTIAL|EXTENDS: <reason>' (paper→concept) — the relationship "
            "type is a PROSE TOKEN after the link, not a link-prefix tag "
            "(OKF conformance); paper→paper edges may optionally add a "
            "trailing '(reciprocal|refutational|line-of-argument)' mirror. "
            "Never silently dropped (charter §2)."
        )

    # Defect #70(a): canonical '## Concept edges' heading, enforced only when
    # the note actually carries concept edges (Move 5's mandatory gating —
    # whether a note MUST have concept edges at all — stays unchanged/
    # deferred this wave; this only enforces the heading NAME when the note
    # does have some, so downstream consumers can rely on it).
    if parsed_concepts.edges and _find_section_body(body, _CONCEPT_EDGES_HEADING_RE) is None:
        findings.append(
            "note has paper→concept edge(s) but no canonical '## Concept "
            "edges' heading found — the heading is required so downstream "
            "consumers can rely on its exact name (Defect #70)"
        )

    # ── Defect #71: retrieval-tier gates edge strength ─────────────────────
    # A note read at less than full-text (abstract-only/title-only/any other
    # or unstamped read_basis) must not carry a SUPPORTS/CONTRADICTS edge of
    # EITHER kind (paper→paper or paper→concept) — the paper was never read
    # at the fidelity needed to assert or refute a claim; cap the strongest
    # permissible type at PARTIAL. Fail-closed: an absent or unstamped
    # 'read_basis' is treated as NOT full-text (never a free pass to claim
    # full strength by simply omitting the field).
    read_basis = _get_scalar(fields, "read_basis").lower()
    if read_basis != "full-text":
        strong_tags = ("SUPPORTS", "CONTRADICTS")
        for edge in parsed_relations.edges:
            if edge["tag"] in strong_tags:
                findings.append(
                    f"paper→paper edge to {edge['target']!r} carries "
                    f"[{edge['tag']}] but 'read_basis' is "
                    f"{(read_basis or '(unstamped)')!r}, not 'full-text' — a "
                    "note not read at full-text fidelity cannot assert or "
                    "refute a claim at that strength; cap at [PARTIAL] "
                    "(Defect #71 retrieval-tier gate)"
                )
        for edge in parsed_concepts.edges:
            if edge["tag"] in strong_tags:
                findings.append(
                    f"paper→concept edge to {edge['target']!r} carries "
                    f"[{edge['tag']}] but 'read_basis' is "
                    f"{(read_basis or '(unstamped)')!r}, not 'full-text' — a "
                    "note not read at full-text fidelity cannot assert or "
                    "refute a claim at that strength; cap at [PARTIAL] "
                    "(Defect #71 retrieval-tier gate)"
                )

    return RelatePresenceResult(findings=findings)
