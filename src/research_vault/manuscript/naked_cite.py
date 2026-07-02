"""naked_cite.py — Assisted naked-citation resolver (SR-MS-2 §5J.13-B, part A).

HONEST BOUNDARY (baked into module docstring per spec):
  This module gives ASSISTED detection, not a hard block. The detection is
  explicitly incomplete: unusual citation formats, solo-author "(Smith 2023)",
  or ambiguous patterns may evade it — those are the human's job at the gate.
  We assist the clear cases and spotlight the rest into the human-go payload.

  We do NOT claim to catch all hallucinated references in prose. Structural
  guarantees (every \\cite{} resolves, every .bib entry has a provenance id)
  live in check_gates.py. Prose citation detection is assisted-plus-human.

WHAT WE DETECT (best-effort, explicitly incomplete):
  1. Author-year patterns:  "(Smith 2023)" / "(Smith et al., 2023)"
  2. Author-prominent:      "Smith (2023) showed" / "Smith et al. (2024) found"
  3. Numeric patterns:      "[N]" / "[1,2,3]" — best-effort (many are non-citation)

MATCHING (against the closed .bib):
  Match by surname(s) + year from the detected pattern against .bib entries.
  A match requires BOTH surname match AND year match.

RESOLUTION TIERS:
  - Unique high-confidence match → AUTO-CONVERT to \\cite{key} (safe: links only
    to an existing provenance-checked .bib entry, never fabricates) + REPORT in
    the human-go decision payload ("auto-linked: (Smith 2023) → \\cite{smith2023}").
  - Ambiguous (same surname + same year, multiple candidates) → DISAMBIGUATE via
    the support-matcher on the sentence's claim (the two SR-MS-2 features compose).
    * Exactly one candidate returns [SUPPORTS] → auto-link, reported as
      "disambiguated via support-match on the claim".
    * Multiple [SUPPORTS] or none → SURFACE + WARN (not guessed).
  - No match (not in .bib) → SURFACE + WARN (anti-hallucination win: a naked
    citation to a non-existent paper cannot be auto-linked → surfaces → human catches it).

BUILD ORDER NOTE (§5J.13-B ★):
  The naked-cite resolver calls match_support() for disambiguation. match_support()
  must be imported from support_matcher (same SR-MS-2, different module). This
  file therefore depends on support_matcher.py.

Stdlib only.
sr: SR-MS-2
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# Opus-tier model for semantic judgment (SR-MS-2 D-MS-4).
# Resolved via RV_JUDGE_MODEL env var; never pinned to a versioned ID in source.
_DEFAULT_JUDGE_MODEL: str = os.environ.get("RV_JUDGE_MODEL", "")


# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------

# Author-year in parens: "(Smith 2023)" / "(Smith et al., 2023)" /
# "(Smith & Jones, 2023)" / "(Smith, Jones, and Lee, 2023)"
# Groups: (1) author part, (2) year
_AY_PAREN_RE = re.compile(
    r"\(([A-Z][a-z]+(?:[,;]?\s+(?:et al\.?|&|and)\s+[A-Z][a-z]+)*(?:[,;]\s*)?)"
    r"\s*(?:et al\.?)?\s*,?\s*"
    r"(\d{4}(?:[a-z])?)"
    r"\)"
)

# Author-prominent: "Smith (2023)" / "Smith et al. (2023)"
# Groups: (1) surname, (2) optional "et al.", (3) year
_AP_PAREN_RE = re.compile(
    r"([A-Z][a-z]+)(?:\s+et al\.?)?\s+\((\d{4}(?:[a-z])?)\)"
)

# Numeric in brackets: [1] / [1,2] / [12] — best-effort
# Only 1–3 digits to avoid matching LaTeX \begin{...} / \cite{...}
# Explicitly incomplete: single-number refs may be section labels, equations, etc.
_NUMERIC_RE = re.compile(r"\[(\d{1,3}(?:,\s*\d{1,3})*)\]")

# Already-cited pattern (so we don't re-detect \cite{} as naked)
_CITE_CMD_RE = re.compile(r"\\cite[a-z]*\*?\s*(?:\[[^\]]*\])?\s*\{[^}]+\}")


# ---------------------------------------------------------------------------
# BibTeX index
# ---------------------------------------------------------------------------

# Matches: @TYPE{citekey,  and captures (type, citekey)
_BIB_ENTRY_RE = re.compile(r"^@(\w+)\{([^,\s]+),", re.MULTILINE)

# Matches: year = {YYYY} or year = YYYY
_BIB_YEAR_RE = re.compile(r"\byear\s*=\s*[\{\"']?(\d{4})[\}\"']?", re.IGNORECASE)

# Matches: author = {Smith, John and Jones, Alice}  or author = {Smith, J.}
_BIB_AUTHOR_RE = re.compile(r"\bauthor\s*=\s*[\{\"'](.+?)[\}\"']", re.IGNORECASE | re.DOTALL)


@dataclass
class BibEntry:
    """A parsed .bib entry for matching purposes."""
    citekey: str
    entry_type: str
    year: str
    surnames: list[str]  # first-author surname first, then co-authors


def _extract_surnames_from_author_str(author_str: str) -> list[str]:
    """Extract surname list from a BibTeX author string.

    Handles:
      "Smith, John and Jones, Alice" (last, first and ...)
      "John Smith and Alice Jones"   (first last and ...)
    Returns lowercased surnames.
    """
    surnames: list[str] = []
    parts = re.split(r"\s+and\s+", author_str, flags=re.IGNORECASE)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if "," in part:
            # last, first format
            surname = part.split(",", 1)[0].strip().lower()
        else:
            # first last format
            tokens = part.split()
            surname = tokens[-1].lower() if tokens else ""
        if surname:
            surnames.append(surname)
    return surnames


def _parse_bib_entries(refs_bib: Path) -> list[BibEntry]:
    """Parse refs.bib into a list of BibEntry for matching."""
    if not refs_bib.exists():
        return []
    try:
        text = refs_bib.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    entries: list[BibEntry] = []
    # Split into individual entry blocks
    entry_blocks: list[tuple[str, str, int]] = []  # (type, citekey, start_pos)
    for m in _BIB_ENTRY_RE.finditer(text):
        entry_blocks.append((m.group(1), m.group(2), m.end()))

    for i, (entry_type, citekey, start) in enumerate(entry_blocks):
        end = entry_blocks[i + 1][2] - len(entry_blocks[i + 1][1]) - 20 if i + 1 < len(entry_blocks) else len(text)
        block = text[start:end]

        year_m = _BIB_YEAR_RE.search(block)
        year = year_m.group(1) if year_m else ""

        author_m = _BIB_AUTHOR_RE.search(block)
        surnames: list[str] = []
        if author_m:
            surnames = _extract_surnames_from_author_str(author_m.group(1))

        entries.append(BibEntry(
            citekey=citekey,
            entry_type=entry_type,
            year=year,
            surnames=surnames,
        ))
    return entries


# ---------------------------------------------------------------------------
# NakedCandidate + NakedCiteResult
# ---------------------------------------------------------------------------

@dataclass
class NakedCandidate:
    """A detected naked-citation pattern in the text."""
    pattern_text: str   # e.g. "(Smith 2023)" or "Smith (2023)"
    surname: str        # normalized lower for matching
    year: str
    start: int          # character offset in the sentence
    end: int
    pattern_type: str   # "author-year" | "author-prominent" | "numeric"


@dataclass
class NakedCiteResult:
    """Resolution result for a single naked-citation candidate.

    Resolution tiers:
      status = "auto-linked"    — unique match, converted to \\cite{key}
      status = "disambiguated"  — support-matcher resolved an ambiguous case
      status = "warn-no-match"  — no .bib match (anti-hallucination flag)
      status = "warn-ambiguous" — multiple or no [SUPPORTS] after disambiguation
    """
    original_text: str          # the original sentence
    candidate: NakedCandidate
    status: str                 # see above
    matched_citekey: str | None  # the resolved citekey (None if warn-*)
    converted_sentence: str | None  # sentence with \\cite{} substituted (None if warn-*)
    disambiguation_note: str | None  # non-None for "disambiguated" or "warn-ambiguous"
    payload_line: str = field(init=False)  # for the human-go decision payload

    def __post_init__(self) -> None:
        if self.status == "auto-linked" and self.matched_citekey:
            self.payload_line = (
                f"auto-linked: '{self.candidate.pattern_text}' → \\cite{{{self.matched_citekey}}}"
            )
        elif self.status == "disambiguated" and self.matched_citekey:
            self.payload_line = (
                f"disambiguated via support-match: '{self.candidate.pattern_text}'"
                f" → \\cite{{{self.matched_citekey}}} ({self.disambiguation_note})"
            )
        elif self.status == "warn-no-match":
            self.payload_line = (
                f"WARN (no match): '{self.candidate.pattern_text}' — "
                f"no backing in the library; add via `rv research add <doi|arxiv>` "
                f"or remove the claim."
            )
        else:
            self.payload_line = (
                f"WARN (ambiguous): '{self.candidate.pattern_text}' — "
                f"{self.disambiguation_note or 'multiple or no support-match candidates; manual adjudication needed.'}"
            )


# ---------------------------------------------------------------------------
# Core detection
# ---------------------------------------------------------------------------

def _mask_cite_commands(text: str) -> str:
    """Replace \\cite{...} commands with spaces so patterns don't re-match them."""
    return _CITE_CMD_RE.sub(lambda m: " " * len(m.group(0)), text)


def detect_naked_citations(sentence: str) -> list[NakedCandidate]:
    """Detect candidate un-\\cite'd citation patterns in a sentence.

    Best-effort — explicitly incomplete (see module docstring honest boundary).
    Returns a list of NakedCandidate, empty if none detected.
    """
    # Mask existing \\cite commands so we don't double-detect
    masked = _mask_cite_commands(sentence)
    candidates: list[NakedCandidate] = []
    seen_spans: set[tuple[int, int]] = set()

    def _add(m: re.Match, surname: str, year: str, ptype: str) -> None:
        span = (m.start(), m.end())
        if span not in seen_spans:
            seen_spans.add(span)
            candidates.append(NakedCandidate(
                pattern_text=sentence[m.start():m.end()],  # original (unmasked)
                surname=surname.lower().strip(),
                year=year.strip(),
                start=m.start(),
                end=m.end(),
                pattern_type=ptype,
            ))

    # Author-prominent first (more specific — "Smith (2023) showed")
    for m in _AP_PAREN_RE.finditer(masked):
        surname = m.group(1)
        year = m.group(2)
        _add(m, surname, year, "author-prominent")

    # Author-year in parens (broader — "(Smith et al., 2023)")
    for m in _AY_PAREN_RE.finditer(masked):
        # Extract first surname from the author part
        author_part = m.group(1)
        first_surname = re.split(r"[,;&]|\bet\b|\band\b", author_part)[0].strip()
        year = m.group(2)
        # Skip if already captured by _AP_PAREN_RE at overlapping position
        span = (m.start(), m.end())
        if span not in seen_spans:
            _add(m, first_surname, year, "author-year")

    return candidates


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _match_candidates_to_bib(
    candidates: list[NakedCandidate],
    bib_entries: list[BibEntry],
) -> dict[int, list[BibEntry]]:
    """For each candidate, return the list of matching .bib entries.

    Matching criteria: first-author surname matches (case-insensitive prefix OK
    for short surnames) AND year matches exactly.
    """
    results: dict[int, list[BibEntry]] = {}
    for i, cand in enumerate(candidates):
        matches: list[BibEntry] = []
        for entry in bib_entries:
            if not entry.surnames:
                continue
            first_surname = entry.surnames[0]
            year = entry.year
            # Year must match exactly (or within year suffix like "2023a")
            cand_year_base = cand.year[:4]
            entry_year_base = year[:4] if year else ""
            if cand_year_base != entry_year_base:
                continue
            # Surname: prefix match (for "Smith" matching "Smithson" is a problem,
            # but exact or case-fold is safer)
            cand_surname = cand.surname
            if cand_surname == first_surname or cand_surname in first_surname:
                matches.append(entry)
        results[i] = matches
    return results


# ---------------------------------------------------------------------------
# Public resolver
# ---------------------------------------------------------------------------

def resolve_naked_citations(
    sentence: str,
    refs_bib: Path,
    *,
    notes_root: Path | None = None,
    judge_fn: Callable[[str], str] | None = None,
    judge_model: str = _DEFAULT_JUDGE_MODEL,
    rubric_override: str | None = None,
    config: Any | None = None,
) -> list[NakedCiteResult]:
    """Detect and resolve naked citations in a single sentence.

    Args:
        sentence:        one manuscript sentence (no \\cite{} commands).
        refs_bib:        path to refs.bib (the closed, provenance-checked bibliography).
        notes_root:      directory where literature/ notes live. Needed for support-matcher
                         disambiguation. When None, disambiguation is skipped → warn-ambiguous.
        judge_fn:        injectable LLM call for disambiguation (hermetic in tests).
        judge_model:     model-id for support-matcher disambiguation.
        rubric_override: optional rubric override for the support-matcher.
        config:          optional Config for rubric lookup.

    Returns:
        list of NakedCiteResult (one per detected candidate), empty if none detected.

    IMPORTANT: auto-links ONLY to existing provenance-checked .bib entries — never fabricates.
    Every conversion is reported in the result's payload_line for human confirmation.

    sr: SR-MS-2
    """
    from research_vault.manuscript.support_matcher import match_support

    candidates = detect_naked_citations(sentence)
    if not candidates:
        return []

    bib_entries = _parse_bib_entries(refs_bib)
    match_map = _match_candidates_to_bib(candidates, bib_entries)
    results: list[NakedCiteResult] = []

    for i, cand in enumerate(candidates):
        matches = match_map.get(i, [])

        if not matches:
            # No match — surface + WARN (anti-hallucination flag)
            results.append(NakedCiteResult(
                original_text=sentence,
                candidate=cand,
                status="warn-no-match",
                matched_citekey=None,
                converted_sentence=None,
                disambiguation_note=None,
            ))
            continue

        if len(matches) == 1:
            # Unique match — auto-convert (safe: only links to existing .bib entry)
            entry = matches[0]
            converted = sentence[:cand.start] + f"\\cite{{{entry.citekey}}}" + sentence[cand.end:]
            results.append(NakedCiteResult(
                original_text=sentence,
                candidate=cand,
                status="auto-linked",
                matched_citekey=entry.citekey,
                converted_sentence=converted,
                disambiguation_note=None,
            ))
            continue

        # Ambiguous (same surname + year, multiple candidates)
        # Try support-matcher disambiguation
        if notes_root is None:
            results.append(NakedCiteResult(
                original_text=sentence,
                candidate=cand,
                status="warn-ambiguous",
                matched_citekey=None,
                converted_sentence=None,
                disambiguation_note=(
                    f"Multiple .bib entries match '{cand.pattern_text}': "
                    f"{[e.citekey for e in matches]}. "
                    f"notes_root not provided — cannot run support-matcher disambiguation. "
                    f"Manual adjudication needed."
                ),
            ))
            continue

        # Run support-matcher on each candidate
        supports_verdicts: list[tuple[BibEntry, Any]] = []
        for entry in matches:
            note_path = notes_root / "literature" / f"{entry.citekey}.md"
            if not note_path.exists():
                # Try without the "literature/" sub-dir
                note_path = notes_root / f"{entry.citekey}.md"
            try:
                verdict = match_support(
                    claim=sentence,
                    citekey=entry.citekey,
                    note_path=note_path,
                    judge_fn=judge_fn,
                    judge_model=judge_model,
                    rubric_override=rubric_override,
                    config=config,
                )
            except Exception:  # noqa: BLE001
                continue
            if verdict.verdict == "SUPPORTS":
                supports_verdicts.append((entry, verdict))

        if len(supports_verdicts) == 1:
            # Exactly one [SUPPORTS] — disambiguate + auto-link, reported in payload
            entry, v = supports_verdicts[0]
            converted = sentence[:cand.start] + f"\\cite{{{entry.citekey}}}" + sentence[cand.end:]
            results.append(NakedCiteResult(
                original_text=sentence,
                candidate=cand,
                status="disambiguated",
                matched_citekey=entry.citekey,
                converted_sentence=converted,
                disambiguation_note=(
                    f"support-match [SUPPORTS] on '{v.verbatim_span or 'quote unavailable'}'"
                ),
            ))
        else:
            # Multiple [SUPPORTS] or none → genuinely ambiguous → surface + WARN
            note = (
                f"Multiple .bib entries match '{cand.pattern_text}': "
                f"{[e.citekey for e in matches]}. "
            )
            if len(supports_verdicts) == 0:
                note += "None returned [SUPPORTS] via support-match — manual adjudication needed."
            else:
                note += (
                    f"{len(supports_verdicts)} candidates returned [SUPPORTS]: "
                    f"{[e.citekey for e, _ in supports_verdicts]}. Cannot auto-resolve."
                )
            results.append(NakedCiteResult(
                original_text=sentence,
                candidate=cand,
                status="warn-ambiguous",
                matched_citekey=None,
                converted_sentence=None,
                disambiguation_note=note,
            ))

    return results
