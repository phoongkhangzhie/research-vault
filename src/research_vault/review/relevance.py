# SPDX-License-Identifier: AGPL-3.0-or-later
"""review/relevance.py — the trustworthy-curation relevance gate.

Design of record: internal doctrine (does not ship). Root cause: a
downstream project's e2e validation run's Phase-2 relate fan-out refused/flagged 51 of
97 "curated" papers as off-domain (astronomy, materials physics, musicology,
literary criticism, surgical robotics, finance) — caught only by luck, AFTER
the expensive fan-out. This module is the precision floor: relevance is a
fail-closed GATE (never a 7th ranking dimension — ``sources/ranker.py``
stays unchanged and only orders relevant survivors).

Calibration (high precision, recall-preserving):
  - reject = high-confidence OFF-DOMAIN only (zero topical-vocabulary
    overlap with the frozen criteria) — an unambiguous wrong-field paper.
  - keep = anything topically plausible, INCLUDING boundary/disconfirming
    papers (the `counter-position` field is a MANDATORY protection: a named
    contrast anchor is treated as defined in-scope, never stripped).
  - keep + flag = uncertain (unfetchable / too-short abstract) — bias to
    keep; "fail-closed" here means fail TOWARD keep-and-flag, the opposite
    of a security gate. A dropped relevant paper is the worse, invisible
    error in a systematic review.

Three placements share ONE primitive (``relevance_gate``):
  1. curate-inline  — the curate agent applies it via prompt discipline
     (review/style.py's review_curate_tips) — cheap, prevention-only.
  2. snowball-screen — ``screen_corpus_raw`` (the mechanical TOOL op,
     ``review.autonomy``'s ``relevance_screen`` op) runs it deterministically
     against every ``_corpus_raw.md`` row, BEFORE review-curate ever sees the
     pool — the snowball is the actual contamination source (citation-
     promiscuous; query-scoping can't help it).
  3. final-corpus cold verifier — a COLD agent node re-applies the same
     calibration to the FINAL ``_corpus.md`` (on abstracts), producing a
     STRUCTURED per-citekey verdict, CANARY-VERIFIED (an unmarked in-scope +
     an unmarked off-domain probe must both classify correctly or the run
     aborts) — the guarantee that runs BEFORE ``coverage-gate`` authorizes
     the expensive Phase-2 fan-out.

Stdlib only.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from research_vault.note import _parse_frontmatter

# ---------------------------------------------------------------------------
# 1. The fixed 3-value vocabulary (charter §2: whitelist, never a heuristic
#    blacklist — see review.check_walk_terminal's identical rationale)
# ---------------------------------------------------------------------------

IN = "IN"
OFF_DOMAIN = "OFF_DOMAIN"
UNCERTAIN = "UNCERTAIN"
VALID_VERDICTS: frozenset[str] = frozenset({IN, OFF_DOMAIN, UNCERTAIN})

# A candidate whose combined title+abstract is shorter than this many words
# cannot be judged with high confidence either way — UNCERTAIN (keep+flag),
# never OFF_DOMAIN. This is the "fail toward keep" floor.
_MIN_WORDS_FOR_CONFIDENT_JUDGMENT = 8

# Generic English stopwords + academic-boilerplate words that carry no
# topical signal — excluded from BOTH the domain vocabulary and the
# candidate's token set so overlap is measured on content words only.
_STOPWORDS: frozenset[str] = frozenset({
    "this", "that", "these", "those", "with", "from", "into", "onto",
    "about", "which", "while", "where", "when", "what", "have", "has",
    "had", "were", "will", "would", "could", "should", "shall", "does",
    "done", "using", "used", "use", "study", "paper", "propose", "present",
    "presents", "results", "result", "based", "than", "then", "their",
    "there", "they", "them", "such", "some", "each", "both", "more",
    "most", "over", "also", "between", "across", "within", "under", "here",
    "however", "therefore", "thus", "hence", "shows", "show", "shown",
    "including", "include", "includes", "given", "novel", "approach",
    "method", "methods", "work", "paper's", "authors",
})

# B1 hardening (2026-07-12 design, "search-primary" redesign): the
# original gate admitted a candidate on a SINGLE shared len>=4 content
# token (``tokens & domain_vocab`` non-empty). That single-token OR-match
# is the documented root cause of a real 212-paper physics/chem/bio
# FACET_REMEDIATE flood (211 of 212 adds were off-domain, admitted on
# one incidental shared word like "behavior" or "field"). Requiring >=2
# DISTINCT overlapping tokens (not a ratio — a fixed count is simpler,
# threshold-stable across abstract lengths, and matches the spec's literal
# ask) closes that flood at the source while keeping the gate a cheap
# mechanical pre-screen (no NLP, no semantic judgment — that's still the
# LLM placements' job). Module constant, not a config knob: this is a
# calibration constant of the mechanical gate itself, not a per-review
# tunable (mirrors ``OFF_DOMAIN_HALT_THRESHOLD``'s own module-constant
# convention below).
_MIN_DISTINCTIVE_TOKEN_OVERLAP = 2

# Cross-domain-generic terms (B2 hardening): a term that shows up in the
# vocabulary of MANY unrelated fields, so a single shared hit is weak
# evidence of true topical overlap — e.g. a review whose counter-position
# talks about "variance collapse" or a "value survey" shares the bare
# tokens "collapse"/"survey" with an unrelated astrophysics abstract about
# quantum-state collapse or a galaxy survey. Stoplisted alongside
# ``_STOPWORDS`` so overlap is measured on the DISTINCTIVE terms of the
# frozen criteria, not on a generic-noun collision (grounded in the
# remediation corpus-bypass off-domain field-leak: 103 physics papers
# admitted on exactly this class of collision).
_CROSS_DOMAIN_GENERIC: frozenset[str] = frozenset({
    "collapse", "survey", "field", "dynamics", "network", "model",
    "simulation", "agent", "population", "sample", "observation", "search",
})


def _tokenize(text: str) -> set[str]:
    """Lowercase word tokens (len >= 4), stopwords excluded.

    Deliberately simple (regex + set, no NLP dependency) — this is a
    topical-overlap heuristic, not a semantic classifier; the LLM-driven
    placements (curate-inline, cold verifier) supply the nuance a keyword
    check cannot.
    """
    tokens = re.findall(r"[a-zA-Z][a-zA-Z\-]{3,}", text.lower())
    return {t for t in tokens if t not in _STOPWORDS and t not in _CROSS_DOMAIN_GENERIC}


def _domain_vocabulary(criteria: dict[str, Any]) -> set[str]:
    """Union of tokenized ``question``/``inclusion``/``exclusion``/
    ``coverage_claim`` — the topical fingerprint of the frozen protocol.

    Each value may be a str or list[str] (frontmatter YAML-list fields
    parse as either, note._parse_frontmatter's lazy-promote — see engineer
    memory); both are handled.
    """
    parts: list[str] = []
    for key in ("question", "inclusion", "exclusion", "coverage_claim"):
        value = criteria.get(key, "")
        if isinstance(value, list):
            value = " ".join(str(v) for v in value)
        parts.append(str(value))
    return _tokenize(" ".join(parts))


def relevance_gate(
    candidate: dict[str, Any],
    criteria: dict[str, Any],
    counter_position: str = "",
) -> str:
    """The pure decision primitive: ``IN | OFF_DOMAIN | UNCERTAIN``.

    Args:
        candidate: at minimum ``{"title": str, "abstract": str}`` (an
            ``evidence_snippet``/``tldr`` key is accepted as an ``abstract``
            fallback for callers using the sweep/snowball evidence-column
            naming).
        criteria: the frozen protocol's ``question``/``inclusion``/
            ``exclusion``/``coverage_claim`` fields (see
            ``parse_protocol_criteria``).
        counter_position: the frozen ``counter-position`` field — the
            disconfirming sub-literature that must be actively sought.
            MANDATORY protection: any candidate whose text overlaps this
            field's vocabulary is treated as a defined in-scope contrast
            anchor and is NEVER rejected, regardless of domain-vocabulary
            overlap (recall-protection test).

    Returns:
        ``OFF_DOMAIN`` only when the candidate has ENOUGH substance to
        judge (>= ``_MIN_WORDS_FOR_CONFIDENT_JUDGMENT`` words) AND shares
        FEWER than ``_MIN_DISTINCTIVE_TOKEN_OVERLAP`` (2) distinct topical
        tokens with the frozen criteria AND is not a named counter-position
        match. ``UNCERTAIN`` when there isn't enough substance to judge
        confidently, or the criteria carry no vocabulary to judge against.
        ``IN`` otherwise (topically plausible, including any
        boundary/disconfirming match, or >=2 distinctive-token overlap) —
        the permissive, recall-safe default.

        The counter-position (disconfirming-protection) branch is
        deliberately NOT subject to the >=2-token floor: it exists to
        protect a small, deliberately-named contrast anchor from being
        stripped by the domain-vocabulary reject path, and a counter-
        position field is often short/single-concept prose (e.g. one named
        phenomenon) — requiring 2+ tokens there would defeat the exact
        recall-protection the field exists for. The >=2-token floor applies
        ONLY to the domain-vocabulary accept/reject decision (the flood's
        actual root cause).
    """
    title = str(candidate.get("title", "") or "").strip()
    abstract = str(
        candidate.get("abstract")
        or candidate.get("evidence_snippet")
        or candidate.get("tldr")
        or ""
    ).strip()
    text = f"{title} {abstract}".strip()

    word_count = len(re.findall(r"\w+", text))
    if word_count < _MIN_WORDS_FOR_CONFIDENT_JUDGMENT:
        return UNCERTAIN

    tokens = _tokenize(text)

    # Disconfirming protection (mandatory): a named contrast
    # anchor is DEFINED in-scope — checked BEFORE the domain-vocabulary
    # reject path, so it can never be stripped by the relevance gate.
    cp_vocab = _tokenize(str(counter_position or ""))
    if cp_vocab and (tokens & cp_vocab):
        return IN

    domain_vocab = _domain_vocabulary(criteria)
    if not domain_vocab:
        # No criteria vocabulary to judge against — cannot confidently
        # reject anything (fail toward keep+flag, never a silent OFF_DOMAIN).
        return UNCERTAIN

    if len(tokens & domain_vocab) >= _MIN_DISTINCTIVE_TOKEN_OVERLAP:
        return IN

    return OFF_DOMAIN


# ---------------------------------------------------------------------------
# 2. Frozen-protocol criteria extraction
# ---------------------------------------------------------------------------

def parse_protocol_criteria(protocol_path: Path) -> tuple[dict[str, Any], str]:
    """Read ``_protocol.md``'s frontmatter into ``(criteria, counter_position)``.

    Returns:
        criteria: ``{"question": ..., "inclusion": ..., "exclusion": ...,
            "coverage_claim": ...}`` (missing fields default to ``""``).
        counter_position: the frozen ``counter-position`` field (``""`` if
            absent — the L-2 structural gate at ``approve-protocol``
            already refuses an empty one before search ever fires, but this
            function must not crash on a protocol that hasn't cleared that
            gate yet, e.g. in isolated unit tests).
    """
    if not protocol_path.exists():
        return {}, ""
    text = protocol_path.read_text(encoding="utf-8")
    fields, _ = _parse_frontmatter(text)

    def _s(key: str) -> str:
        value = fields.get(key, "")
        if isinstance(value, list):
            value = " ".join(str(v) for v in value)
        return str(value)

    criteria = {
        "question": _s("question"),
        "inclusion": _s("inclusion"),
        "exclusion": _s("exclusion"),
        "coverage_claim": _s("coverage_claim"),
    }
    counter_position = _s("counter-position")
    return criteria, counter_position


# ---------------------------------------------------------------------------
# 3. Snowball-screen (CORE) — the mechanical corpus_raw.md pre-filter
# ---------------------------------------------------------------------------

def parse_corpus_raw_rows(text: str) -> list[dict[str, str]]:
    """Parse a ``_corpus_raw.md``-shaped table:
    ``| Annotation | Paper-id | Title | Venue | Year | Abstract/TL;DR | Flags | Rerank |``
    (``sources.snowball.write_corpus_raw``'s exact header).

    Tolerant of extra/missing trailing columns (Venue/Year/Abstract/Flags/
    Rerank may be blank strings — the honest-blank convention already
    documented on ``write_corpus_raw``). Header/separator rows and any row
    whose column-0 isn't bracket-shaped (``[...]``) are silently skipped —
    the same narrow structural signal ``review._parse_corpus_citekeys`` uses.

    A1 (task #86): the trailing ``Rerank`` column (8th) is OPTIONAL — a
    legacy 7-column row (written before A1 shipped) parses exactly as
    before, with ``rerank`` defaulting to ``""``. Pure append, no
    positional-format break.

    C (task #86): a further OPTIONAL 9th ``Poles`` column — same tolerant-
    append discipline; a legacy 7- or 8-column row parses unchanged with
    ``poles`` defaulting to ``""``.

    Returns a list of dicts with keys: annotation, paper_id, title, venue,
    year, abstract, flags, rerank, poles (all raw strings, "" when absent).
    """
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cols = [c.strip() for c in stripped.split("|")]
        # split("|") on a line starting/ending with "|" yields empty first/
        # last elements — drop them without collapsing interior empties
        # (an interior blank column, e.g. no Venue, is a real value: "").
        if cols and cols[0] == "":
            cols = cols[1:]
        if cols and cols[-1] == "":
            cols = cols[:-1]
        if not cols or not re.match(r"^\[.*\]$", cols[0]):
            continue
        rows.append({
            "annotation": cols[0] if len(cols) > 0 else "",
            "paper_id": cols[1] if len(cols) > 1 else "",
            "title": cols[2] if len(cols) > 2 else "",
            "venue": cols[3] if len(cols) > 3 else "",
            "year": cols[4] if len(cols) > 4 else "",
            "abstract": cols[5] if len(cols) > 5 else "",
            "flags": cols[6] if len(cols) > 6 else "",
            "rerank": cols[7] if len(cols) > 7 else "",
            "poles": cols[8] if len(cols) > 8 else "",
        })
    return rows


def _render_corpus_raw_row(row: dict[str, str]) -> str:
    return (
        f"| {row.get('annotation', '')} | {row.get('paper_id', '')} | "
        f"{row.get('title', '')} | {row.get('venue', '')} | "
        f"{row.get('year', '')} | {row.get('abstract', '')} | "
        f"{row.get('flags', '')} | {row.get('rerank', '')} | "
        f"{row.get('poles', '')} |"
    )


def screen_corpus_raw(
    corpus_raw_path: Path,
    protocol_path: Path,
    out_path: Path,
) -> dict[str, Any]:
    """The ``relevance_screen`` TOOL op: the deterministic snowball-screen
    gate between ``review-snowball`` and ``review-curate`` (d,
    CORE — not deferred, because the snowball is the actual contamination
    source: citation-promiscuous, query-scoping can't help it).

    Reads every row of ``_corpus_raw.md``, applies ``relevance_gate`` against
    the frozen protocol's criteria + counter-position, and writes
    ``out_path``:
      - ``IN``/``UNCERTAIN`` rows are KEPT in the main table (an
        ``UNCERTAIN`` row gets a visible ``[RELEVANCE:UNCERTAIN]`` flag
        appended — never silently indistinguishable from a clean ``IN``
        row, so ``review-curate`` sees exactly which rows to double-check).
      - ``OFF_DOMAIN`` rows are moved to a declared
        ``## Rejected as off-domain (relevance gate)`` section — NEVER
        silently dropped (charter §2); each rejected row's title/abstract
        is preserved in full for audit.

    Returns:
        ``{"total", "in", "uncertain", "off_domain"}`` counts.
    """
    text = corpus_raw_path.read_text(encoding="utf-8") if corpus_raw_path.exists() else ""
    rows = parse_corpus_raw_rows(text)
    criteria, counter_position = parse_protocol_criteria(protocol_path)

    kept: list[dict[str, str]] = []
    rejected: list[dict[str, str]] = []
    counts = {"total": len(rows), "in": 0, "uncertain": 0, "off_domain": 0}

    for row in rows:
        candidate = {"title": row["title"], "abstract": row["abstract"]}
        verdict = relevance_gate(candidate, criteria, counter_position)
        if verdict == OFF_DOMAIN:
            counts["off_domain"] += 1
            rejected.append(row)
        elif verdict == UNCERTAIN:
            counts["uncertain"] += 1
            flagged = dict(row)
            flagged["flags"] = (row.get("flags", "") + " [RELEVANCE:UNCERTAIN]").strip()
            kept.append(flagged)
        else:
            counts["in"] += 1
            kept.append(row)

    lines: list[str] = [
        "# Corpus (raw, pre-curation) — relevance-screened\n",
        f"Relevance gate: {counts['total']} candidate(s), "
        f"{counts['in']} in-scope, {counts['uncertain']} uncertain "
        f"(kept+flagged), {counts['off_domain']} off-domain (rejected).\n",
        "| Annotation | Paper-id | Title | Venue | Year | Abstract/TL;DR | Flags | Rerank | Poles |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in kept:
        lines.append(_render_corpus_raw_row(row))
    lines.append("")

    if rejected:
        lines.append("## Rejected as off-domain (relevance gate)\n")
        lines.append(
            "The following candidates carried ZERO topical-vocabulary overlap "
            "with the frozen protocol's criteria and are not a named "
            "counter-position match — high-confidence off-domain, excluded "
            "from review-curate's input pool. Preserved here, never silently "
            "dropped.\n"
        )
        lines.append("| Paper-id | Title | Abstract/TL;DR |")
        lines.append("|---|---|---|")
        for row in rejected:
            lines.append(f"| {row.get('paper_id', '')} | {row.get('title', '')} | {row.get('abstract', '')} |")
        lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return counts


# ---------------------------------------------------------------------------
# 4. Final-corpus cold verifier — canary probes + verdict table
# ---------------------------------------------------------------------------

# Fixed, well-known citekeys for the two unmarked canary rows the prep step
# injects into the verifier's input. The title/abstract text carries no hint
# that the row is a canary (the agent judges it like any other row) — only
# THIS module (which knows the constant) can identify it in the returned
# verdict table.
CANARY_IN_SCOPE_CITEKEY = "zz-relgate-probe-a"
CANARY_OFF_DOMAIN_CITEKEY = "zz-relgate-probe-b"
_CANARY_CITEKEYS: frozenset[str] = frozenset({CANARY_IN_SCOPE_CITEKEY, CANARY_OFF_DOMAIN_CITEKEY})

# A fixed, unambiguously off-domain astronomy abstract — mirrors the real
# grounding-run contamination class (a galaxy/AGN survey) named in the design doc.
# Deliberately domain-INDEPENDENT of whatever the live review's criteria
# are: no plausible social-science/ML/behavioral review criteria would
# share vocabulary with a spectroscopic AGN survey.
_CANARY_OFF_DOMAIN_TITLE = (
    "A wide-field spectroscopic survey of active galactic nuclei"
)
_CANARY_OFF_DOMAIN_ABSTRACT = (
    "We present a spectroscopic survey of 4,000 active galactic nuclei, "
    "deriving black hole mass estimates via reverberation mapping and "
    "cross-dispersed echelle spectroscopy of quasar emission-line ratios "
    "across a wide-field galaxy sample."
)


def build_canary_rows(criteria: dict[str, Any]) -> list[dict[str, str]]:
    """Build the two unmarked canary rows for the cold verifier's input.

    The in-scope canary is CONSTRUCTED FROM the live criteria's own text
    (title/abstract literally drawn from ``question``/``inclusion``) —
    guaranteed topical overlap by construction, so it is domain-agnostic:
    it works for whatever the live review's actual topic is, not a
    hardcoded example. The off-domain canary is a fixed astronomy abstract
    (see this is the real contamination class from the
    grounding e2e run) that shares no plausible vocabulary with a social-
    science/ML/behavioral review's criteria.
    """
    question = str(criteria.get("question", "")).strip() or "the review question"
    inclusion = str(criteria.get("inclusion", "")).strip() or str(
        criteria.get("coverage_claim", "")
    ).strip()
    return [
        {
            "citekey": CANARY_IN_SCOPE_CITEKEY,
            "title": f"On {question}",
            "abstract": inclusion or f"A study directly addressing {question}.",
        },
        {
            "citekey": CANARY_OFF_DOMAIN_CITEKEY,
            "title": _CANARY_OFF_DOMAIN_TITLE,
            "abstract": _CANARY_OFF_DOMAIN_ABSTRACT,
        },
    ]


_BRACKET_TOKEN_RE = re.compile(r"\[([^\[\]]+)\]")


def corpus_row_annotation_tags(annotation: str) -> list[str]:
    """Case-normalized bracket-delimited tokens in a ``_corpus.md`` row's
    annotation cell — the ONE shared grammar for classifying that cell.

    Convergence note (remediation corpus-bypass fix): this repo had grown
    THREE independent re-implementations of "scan every ``[...]`` token in
    the annotation column" (this module's own ``_annotation_is_new``,
    ``review.__init__._corpus_row_tags``, and ``review.ledger._corpus_rows``'s
    exact-string-match, which silently diverged and undercounted a compound
    annotation like ``[LEG-1][NEW]``). Every consumer that needs to
    classify a corpus-row annotation cell calls this function (directly, or
    via a thin local re-export kept for import-cycle reasons) — never a
    fourth re-implementation.
    """
    return [t.strip().upper() for t in _BRACKET_TOKEN_RE.findall(annotation)]


def _annotation_is_new(annotation: str) -> bool:
    """Whether an ``_corpus.md`` row's annotation column marks it ``NEW``
    (to be re-verified) rather than ``IN-CORPUS`` (already vetted in a prior
    review cycle).

    Root-cause fix (relevance-verify-prep probe-only defect): a real
    ``review-curate`` run does NOT always emit the bare literal ``[NEW]``
    the original exact-match check required — it commonly emits a COMPOUND
    annotation like ``[LEG-1][NEW] {SF,silicon-sampling,CR}`` (leg tag +
    status tag + a trailing concept-tag set). An exact ``annotation == "[NEW]"``
    check silently matched ZERO rows against that real shape — the entire
    264-paper curated corpus vanished from the verify input, leaving only
    the two canary probes (the corpus_verify_input.md observed on the live
    cultural-actor-fidelity run). This scans EVERY bracket-delimited token
    in the annotation column for an exact (case-insensitive) ``NEW`` token,
    so it matches both the bare legacy form and the compound form.

    ``[IN-CORPUS...]`` rows (any bracket token starting with ``IN-CORPUS``)
    are excluded regardless of any other tag present — same design as the
    legacy check: already-vetted papers are not re-verified here.
    """
    tokens = corpus_row_annotation_tags(annotation)
    if any(t.startswith("IN-CORPUS") for t in tokens):
        return False
    return any(t == "NEW" for t in tokens)


def annotation_needs_curate(annotation: str) -> bool:
    """True iff the row's annotation cell carries a ``[NEEDS-CURATE]``
    token — a mechanically-screened-in remediation/facet-remediation
    append (``review.facet_remediation.screen_and_append_facet_hits``'s own
    tag) that a re-curate pass has NOT yet processed. Used by
    ``review.check_corpus_all_accept_tagged`` — never bypass it by adding a
    fifth ad hoc ``"NEEDS-CURATE" in annotation`` check elsewhere."""
    return "NEEDS-CURATE" in corpus_row_annotation_tags(annotation)


def parse_corpus_table_with_abstract(text: str) -> list[dict[str, str]]:
    """Parse a final ``_corpus.md`` table, tolerating an OPTIONAL trailing
    Abstract/TL;DR column (``| Annotation | Citekey | Title | Abstract |``)
    on top of the legacy 3-column shape (``| Annotation | Citekey | Title |``
    — ``review._parse_corpus_citekeys``'s exact contract). A legacy row with
    no abstract column gets ``abstract: ""`` — never a crash, never a
    fabricated abstract.

    A1 (task #86): a further OPTIONAL 5th ``Rerank`` column
    (``| Annotation | Citekey | Title | Abstract | Rerank |``) — the
    curate agent's tips (``review.style``'s ``review_curate_tips``)
    instruct it to carry the score verbatim from ``_corpus_raw.md``. A row
    with no 5th column gets ``rerank: ""`` — pure append, no positional-
    format break on either the legacy 3- or 4-column shape.

    C (task #86): a further OPTIONAL 6th ``Poles`` column
    (``| Annotation | Citekey | Title | Abstract | Rerank | Poles |``) —
    same carry-over discipline as Rerank. A row with no 6th column gets
    ``poles: ""``, pure append.

    Only rows carrying a ``NEW`` status tag are returned (see
    ``_annotation_is_new`` — tolerates both the bare ``[NEW]`` legacy shape
    and a real compound annotation like ``[LEG-1][NEW] {tags}``).
    ``[IN-CORPUS:*]``-tagged papers were already vetted in a prior review
    cycle and are not re-verified here (mirrors
    ``review._parse_new_citekeys_from_text``).
    """
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cols = [c.strip() for c in stripped.split("|")]
        if cols and cols[0] == "":
            cols = cols[1:]
        if cols and cols[-1] == "":
            cols = cols[:-1]
        if len(cols) < 2:
            continue
        annotation = cols[0]
        if not _annotation_is_new(annotation):
            continue
        citekey = cols[1] if len(cols) > 1 else ""
        title = cols[2] if len(cols) > 2 else ""
        abstract = cols[3] if len(cols) > 3 else ""
        rerank = cols[4] if len(cols) > 4 else ""
        poles = cols[5] if len(cols) > 5 else ""
        # DOI-shaped citekeys (e.g. "10.48550/arXiv.2604.19787",
        # "10.1038/s44482-026-00026-6") are common in a real curated corpus
        # — the legacy charset omitted "/" and silently dropped every DOI
        # row too (same class of bug as the annotation exact-match: a real
        # shape the strict regex never accounted for).
        if not re.match(r"^[A-Za-z0-9_:/\-\.]+$", citekey):
            continue
        rows.append({
            "citekey": citekey, "title": title, "abstract": abstract,
            "rerank": rerank, "poles": poles,
        })
    return rows


class DegenerateVerifyInputError(RuntimeError):
    """Raised by ``build_verify_input`` when ZERO real (non-canary) rows
    were parsed out of ``_corpus.md`` — a probe-only input.

    A rejects-only cold verifier fed ONLY the two canary rows still
    reports a clean canary-pass, but exercises no real curation at all
    (retro'd on the live cultural-actor-fidelity run: a 264-paper curated
    corpus produced a ``_corpus_verify_input.md`` with the two canary rows
    and nothing else). That must never look like a clean pass — this is a
    loud, HALT-shaped failure (charter §2), raised here so the tool-node
    runner (``dag/verbs.py::_auto_execute_tool_nodes``) surfaces it as a
    ``blocked`` node rather than writing an artifact that silently
    satisfies the downstream ``needs: artifact:...+fresh`` edge.
    """


def _interleave_canaries_deterministic(
    real_rows: list[dict[str, str]],
    canary_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Interleave *canary_rows* among *real_rows* at evenly-spaced,
    deterministic positions — NEVER appended after every real row.

    A canary always trailing the real rows is itself a positional tell (an
    adversarial or merely pattern-matching judge could learn "the last N
    rows are probes"). Evenly-spacing them among the real rows (same
    deterministic-spacing formula as
    ``gates.judge_seam.interleave_with_canaries``) removes that tell while
    staying reproducible given the same inputs (idempotent re-emit).
    """
    n_real = len(real_rows)
    n_canary = len(canary_rows)
    total = n_real + n_canary
    if n_canary == 0:
        return list(real_rows)
    if n_real == 0:
        return list(canary_rows)

    raw_positions = [round(total * (k + 1) / (n_canary + 1)) for k in range(n_canary)]
    used: set[int] = set()
    fixed_positions: list[int] = []
    for p in raw_positions:
        p = max(0, min(p, total))
        while p in used and p <= total:
            p += 1
        while p in used and p >= 0:
            p -= 1
        used.add(p)
        fixed_positions.append(p)
    fixed_positions.sort()

    combined = list(real_rows)
    for pos, row in zip(fixed_positions, canary_rows):
        insert_at = min(pos, len(combined))
        combined.insert(insert_at, row)
    return combined


def build_verify_input(
    corpus_path: Path,
    protocol_path: Path,
    out_path: Path,
) -> dict[str, Any]:
    """The ``relevance_verify_prep`` TOOL op: build the cold verifier's
    input artifact — every ``NEW``-tagged row of the final ``_corpus.md``
    interleaved with the two unmarked canary rows at deterministic,
    non-trailing positions (b — canary-verified).

    Raises ``DegenerateVerifyInputError`` if ZERO real rows were parsed —
    a probe-only input silently defeats the whole point of the gate (see
    that exception's docstring). This is a loud tool-op failure, not a
    written-but-empty artifact.

    Returns ``{"real_citekeys": [...], "canary_citekeys": [...],
    "real_row_count": int}`` (the canary citekeys are always the two fixed
    constants — returned here only for the audit trail, never as a secret
    the caller must thread through).
    """
    text = corpus_path.read_text(encoding="utf-8") if corpus_path.exists() else ""
    real_rows = parse_corpus_table_with_abstract(text)

    if not real_rows:
        raise DegenerateVerifyInputError(
            f"relevance_verify_prep: {corpus_path} yielded ZERO real "
            "(NEW-tagged) rows — the verify input would contain only the "
            "two canary probes. A rejects-only verifier over a probe-only "
            "input verifies the judge but checks no real curation "
            "(charter §2/§10). Halting rather than writing a probe-only "
            "artifact that would look like a clean pass downstream."
        )

    criteria, _counter_position = parse_protocol_criteria(protocol_path)
    canary_rows = build_canary_rows(criteria)

    all_rows = _interleave_canaries_deterministic(real_rows, canary_rows)
    real_row_count = len(real_rows)

    lines: list[str] = [
        "# Relevance-verify input\n",
        f"<!-- real_row_count: {real_row_count} -->\n",
        "Judge EACH row IN/OFF_DOMAIN/UNCERTAIN per the relevance-gate "
        "calibration below. Judge every row identically, on its own "
        "substance alone.\n",
        "| Citekey | Title | Abstract/TL;DR |",
        "|---|---|---|",
    ]
    for row in all_rows:
        lines.append(f"| {row['citekey']} | {row['title']} | {row['abstract']} |")
    lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")

    return {
        "real_citekeys": [r["citekey"] for r in real_rows],
        "canary_citekeys": sorted(_CANARY_CITEKEYS),
        "real_row_count": real_row_count,
    }


# DOI-shaped citekeys (e.g. "10.48550/arXiv.2604.19787") carry a "/" —
# same charset fix as parse_corpus_table_with_abstract's citekey regex.
_VERDICT_ROW_RE = re.compile(
    r"^\|\s*([A-Za-z0-9_:/\-\.]+)\s*\|\s*(IN|OFF_DOMAIN|UNCERTAIN)\s*\|",
    re.IGNORECASE,
)


def parse_relevance_verdict_table(text: str) -> tuple[dict[str, str], list[str]]:
    """Parse the cold verifier's structured ``| Citekey | Verdict |`` table.

    Fixed vocab ONLY (``IN``/``OFF_DOMAIN``/``UNCERTAIN``, case-normalized)
    — never prose-parsed (charter §2, mirrors
    ``review.check_coverage_critic_verdict``'s structured-field discipline).

    Returns:
        (verdicts, malformed) — ``verdicts`` maps citekey -> verdict for
        every well-formed row (including canary rows, if present — the
        caller strips them via ``check_relevance_verifier``). ``malformed``
        lists any table-shaped row (starts with ``|``, 2+ columns, column-0
        looks like a citekey) whose verdict column did NOT match the fixed
        vocab — surfaced, never silently dropped.
    """
    verdicts: dict[str, str] = {}
    malformed: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        m = _VERDICT_ROW_RE.match(stripped)
        if m:
            citekey, verdict = m.group(1), m.group(2).upper()
            verdicts[citekey] = verdict
            continue
        # Table-shaped but not matching the fixed vocab: only flag rows
        # that look like a genuine data row (2+ pipe-delimited columns,
        # first column citekey-shaped) — never the header/separator rows.
        cols = [c.strip() for c in stripped.split("|") if c.strip()]
        if len(cols) >= 2 and re.match(r"^[A-Za-z0-9_:/\-\.]+$", cols[0]) and cols[0].lower() not in (
            "citekey",
        ):
            if not re.match(r"^-+$", cols[0]):
                malformed.append(stripped)
    return verdicts, malformed


def check_relevance_verifier(verifier_path: Path) -> dict[str, Any]:
    """Read the cold verifier's ``_relevance-verdict.md`` into the
    structural-payload shape ``classify_relevance_verdict`` consumes.

    charter §2 (surface, never silently drop) + §10 (a canary miss is a
    contamination flag, not a footnote):
      - Missing artifact -> ``exists: False`` (a floor gate that never ran
        must never look like a pass — the caller HALTs).
      - Canary row absent OR misclassified -> ``canary_aborted: True``
        (untrustworthy judge signal — fail-closed, never auto-retry the
        same broken judge, charter §10).
      - A malformed verdict row -> excluded from ``verdicts`` and listed in
        ``malformed`` (the caller treats a malformed/missing per-paper
        verdict as KEEP+flag — recall-safe, never a silent drop).
      - Zero real (non-canary) verdict rows parsed AT ALL, while the
        artifact exists and is non-empty -> ``empty_verdict_set: True``
        (the caller HALTs rather than silent-GO on a verifier that wrote
        nothing usable).

    Returns:
        dict with keys: exists, canary_aborted, canary_detail, verdicts
        (citekey -> verdict, canary rows stripped), malformed,
        empty_verdict_set.
    """
    if not verifier_path.exists():
        return {
            "exists": False,
            "canary_aborted": False,
            "canary_detail": "verifier artifact not found",
            "verdicts": {},
            "malformed": [],
            "empty_verdict_set": True,
        }

    text = verifier_path.read_text(encoding="utf-8")
    all_verdicts, malformed = parse_relevance_verdict_table(text)

    in_scope_verdict = all_verdicts.get(CANARY_IN_SCOPE_CITEKEY)
    off_domain_verdict = all_verdicts.get(CANARY_OFF_DOMAIN_CITEKEY)

    canary_aborted = False
    canary_detail = "both canaries classified correctly"
    if in_scope_verdict != IN:
        canary_aborted = True
        canary_detail = (
            f"in-scope canary ({CANARY_IN_SCOPE_CITEKEY}) classified as "
            f"{in_scope_verdict!r}, expected 'IN'"
        )
    elif off_domain_verdict != OFF_DOMAIN:
        canary_aborted = True
        canary_detail = (
            f"off-domain canary ({CANARY_OFF_DOMAIN_CITEKEY}) classified as "
            f"{off_domain_verdict!r}, expected 'OFF_DOMAIN'"
        )

    real_verdicts = {
        ck: v for ck, v in all_verdicts.items() if ck not in _CANARY_CITEKEYS
    }
    empty_verdict_set = not real_verdicts and not all_verdicts

    return {
        "exists": True,
        "canary_aborted": canary_aborted,
        "canary_detail": canary_detail,
        "verdicts": real_verdicts,
        "malformed": malformed,
        "empty_verdict_set": empty_verdict_set,
    }


# ---------------------------------------------------------------------------
# 5. Disposition — coverage-gate reads this
# ---------------------------------------------------------------------------

# Below the threshold -> auto-prune + declare residue; at/above it -> HALT-DECLARE
# (don't silently prune a large fraction of the corpus). The threshold sits above
# the cold verifier's observed natural off-domain rate on a curated corpus (~16%)
# so a citation-promiscuous domain doesn't false-HALT, while still catching a
# genuinely-broken (~40%+) corpus.
OFF_DOMAIN_HALT_THRESHOLD: float = 0.30


def classify_relevance_verdict(
    payload: dict[str, Any],
    *,
    threshold: float = OFF_DOMAIN_HALT_THRESHOLD,
) -> Any:
    """The coverage-gate relevance disposition (c).

    Args:
        payload: ``check_relevance_verifier``'s return dict.
        threshold: off-domain fraction at/above which the disposition is
            HALT-DECLARE rather than an auto-prune (default 0.30 — headroom
            above the ~16% cold-verifier natural rate, still HALTs a broken
            ~40%+ corpus).

    Returns:
        A ``review.autonomy.DispositionResult``:
          - ``exists`` False, or ``empty_verdict_set`` True -> HALT-DECLARE
            (a floor gate that never ran, or wrote nothing usable, must
            never look like a pass — never silent-GO).
          - ``canary_aborted`` True -> HALT-DECLARE (untrustworthy judge).
          - off-domain fraction >= threshold -> HALT-DECLARE (c:
            "this is a signal that curate/search is fundamentally broken,
            not a trim" — the corpus is NOT auto-pruned in this branch).
          - 0 < fraction < threshold -> GO-WITH-RESIDUE, with
            ``evidence["off_domain_citekeys"]`` — the caller (dag/verbs.py)
            is responsible for actually pruning those citekeys from
            ``_corpus.md`` and declaring the residue note.
          - fraction == 0 -> GO (nothing to prune, no residue).
    """
    from research_vault.review.autonomy import DispositionResult, GO, GO_WITH_RESIDUE, HALT_DECLARE

    if not payload.get("exists", False):
        return DispositionResult(
            HALT_DECLARE,
            "review-relevance-verify --auto: no _relevance-verdict.md found "
            "— the cold relevance verifier never ran (or wrote no artifact); "
            "cannot self-certify the relevance floor.",
            {"not_run": True},
        )

    if payload.get("canary_aborted"):
        return DispositionResult(
            HALT_DECLARE,
            "review-relevance-verify --auto: canary-abort — "
            f"{payload.get('canary_detail', 'canary check failed')} — the "
            "cold verifier's judge signal is untrustworthy, fail-closed, "
            "never auto-retry the same broken judge.",
            {"canary_aborted": True, "canary_detail": payload.get("canary_detail", "")},
        )

    if payload.get("empty_verdict_set"):
        return DispositionResult(
            HALT_DECLARE,
            "review-relevance-verify --auto: the verifier's structured "
            "verdict table parsed to ZERO usable rows — a missing verdict "
            "SET is treated as a floor-gate failure, never a silent GO.",
            {"empty_verdict_set": True},
        )

    verdicts: dict[str, str] = payload.get("verdicts", {})
    total = len(verdicts)
    off_domain_citekeys = [ck for ck, v in verdicts.items() if v == OFF_DOMAIN]

    if total == 0:
        return DispositionResult(GO, "relevance-verify: empty corpus, nothing to check.", {})

    fraction = len(off_domain_citekeys) / total

    if fraction >= threshold:
        return DispositionResult(
            HALT_DECLARE,
            f"review-relevance-verify --auto: {len(off_domain_citekeys)}/{total} "
            f"({fraction:.0%}) of the final corpus verified OFF-DOMAIN — at or "
            f"above the {threshold:.0%} threshold. This signals curate/search "
            "is fundamentally broken, not a trim — HALT-DECLARE to the human "
            "rather than silently pruning a large fraction of the corpus.",
            {"off_domain_citekeys": sorted(off_domain_citekeys), "fraction": fraction},
        )

    if off_domain_citekeys:
        return DispositionResult(
            GO_WITH_RESIDUE,
            f"review-relevance-verify --auto: {len(off_domain_citekeys)}/{total} "
            f"({fraction:.0%}) of the final corpus verified OFF-DOMAIN — below "
            f"the {threshold:.0%} threshold; auto-pruning and declaring the "
            "residue, run proceeds.",
            {"off_domain_citekeys": sorted(off_domain_citekeys), "fraction": fraction},
        )

    return DispositionResult(GO, "review-relevance-verify: no off-domain papers found.", {})


def prune_off_domain_from_corpus(
    corpus_path: Path,
    off_domain_citekeys: list[str],
    residue_path: Path,
) -> int:
    """Remove ``off_domain_citekeys`` rows from ``_corpus.md`` and declare
    the prune in ``residue_path`` (mirrors ``_coverage-gaps.md``'s honest-
    residue convention — an autonomous corpus mutation must be declared,
    never silent, per charter §2 and the D2 deviation-transparency
    contract).

    Idempotent: a citekey already absent from ``_corpus.md`` (e.g. a repeat
    evaluation after a prior prune already ran) is simply not found —
    re-running this is always safe, never a crash, never a duplicate
    removal error.

    Returns the number of rows actually removed this call.
    """
    if not off_domain_citekeys:
        return 0
    if not corpus_path.exists():
        return 0

    off_domain_set = set(off_domain_citekeys)
    text = corpus_path.read_text(encoding="utf-8")
    kept_lines: list[str] = []
    removed: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cols = [c.strip() for c in stripped.split("|")]
            if cols and cols[0] == "":
                cols = cols[1:]
            if cols and cols[-1] == "":
                cols = cols[:-1]
            if len(cols) >= 2 and cols[1] in off_domain_set:
                removed.append(cols[1])
                continue
        kept_lines.append(line)

    if removed:
        corpus_path.write_text("\n".join(kept_lines) + "\n", encoding="utf-8")

    residue_path.parent.mkdir(parents=True, exist_ok=True)
    existing = residue_path.read_text(encoding="utf-8") if residue_path.exists() else (
        "# Relevance-gate residue\n\n"
        "Papers auto-pruned by the cold final-corpus relevance verifier "
        "(design 2026-07-10-trustworthy-curation-relevance-gate-design.md "
        ") — verified OFF-DOMAIN, below the HALT threshold, so the run "
        "proceeds with these papers removed from the corpus rather than "
        "halting for human review.\n\n"
    )
    entry = (
        f"## Prune ({len(removed)} paper(s))\n\n"
        + "\n".join(f"- {ck}" for ck in sorted(off_domain_set))
        + "\n\n"
    )
    residue_path.write_text(existing + entry, encoding="utf-8")

    return len(removed)
