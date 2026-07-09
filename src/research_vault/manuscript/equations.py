# SPDX-License-Identifier: AGPL-3.0-or-later
"""manuscript/equations.py — the don't-drop-the-math machinery (PR-M4).

Three coordinated pieces (design §7, type-generic — applies to any
``ManuscriptType`` via ``ms_type.equation_sources``, not just ``lit-review``):

  (c) ``extract_equation_ledger`` — a DETERMINISTIC extractor mining the
      type's ``equation_sources`` notes for pivotal equations, producing an
      **equation ledger**. Two joined data sources on a ``literature/`` note
      (PR-L1 §7.5, the upstream coupling this module CONSUMES):
        - the body's ``## Key equations`` section — one ``### [label] Title``
          block per equation, body LaTeX verbatim underneath;
        - the frontmatter ``key_equations:`` criticality ledger (a D8
          mapping-list of ``{label, critical}``) — joined to the body block
          BY LABEL (label-uniqueness + exact-match; the L1 review catch).
      A non-``literature`` source (e.g. ``concepts/``) has no criticality
      ledger to join — its display-math blocks (``$$…$$``, ``\\[…\\]``,
      ``\\begin{equation}…\\end{equation}``) are mined generically and marked
      ``critical=None`` (unmarked — §7b's LLM-judge-criticality-fallback
      territory; M4 ships the deterministic half, ``check_equation_fidelity``
      below degrades an unmarked equation to SIGNAL rather than guessing).
      A note with no equations anywhere is a silent, correct no-op (never an
      error) — most notes have none.

  (a) ``inject_equation_brief`` — appends a REQUIRE-block-LaTeX rule + the
      ledger's equations (verbatim, injected — never re-typed by the writer)
      to the style-seam tip of every section whose ``source_atoms`` overlap
      the type's ``equation_sources``. Mirrors the removed
      ``results_inject.py`` discipline: numbers/equations are DATA the writer
      is handed, never typed from memory.

  (b) ``check_equation_fidelity`` — the equation-fidelity gate, re-run every
      draft/revise round: for each ledger equation, confirm a form of it made
      it into the draft. Deterministic normalized-LaTeX match FIRST; an
      optional ``judge_fn`` fallback for a re-typeset-but-equivalent form
      (``honesty-gates.md`` judge discipline: fail-closed on judge error/
      absence — an unconfirmed equation is treated as dropped, never as
      "probably fine").

  ★ D-MS-2 (the operator's explicit call, overriding this design doc's own REC of
  BLOCK for marked-critical): the gate is **SIGNAL, NOT BLOCK** — for BOTH a
  marked-critical AND an unmarked absent equation. Some papers have no
  equations at all; a drop must never fail the build. A dropped
  marked-critical equation is flagged for the human review loop (worst-
  findings triage), not autogated. ``check_equation_fidelity`` findings never
  raise and never carry a build-failing class — only ``severity`` varies
  (``"critical"`` vs ``"unmarked"``) so the review loop can prioritize.

Design: docs/superpowers/specs/2026-07-07-survey-capability-design.md §7, §7.5.
Recovers the removed ``results_inject.py``'s "machine-injected, never typed"
shape (git history: 4fdb9b2^:src/research_vault/manuscript/results_inject.py)
and extends it to equations.

Stdlib only.
sr: PR-M4
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from research_vault.note import _parse_frontmatter

# ---------------------------------------------------------------------------
# Body-section extraction (literature/ structured path — PR-L1 §7.5 shape)
# ---------------------------------------------------------------------------

# The exact shape PR-L1 authored and tested (tests/test_pr_l1_lit_ingestion.py):
# a "## Key equations" body section containing "### [label] Title" headers,
# each followed by verbatim LaTeX up to the next header (or end of section).
_EQ_SECTION_RE = re.compile(
    r"^##\s+Key equations\s*$(.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL
)
_EQ_HEADER_RE = re.compile(r"^###\s+\[([^\]]+)\]\s*(.*?)\s*$", re.MULTILINE)
_INLINE_CRITICAL_MARKER_RE = re.compile(r"\*\(critical\)\*")

# Generic display-math scan (non-literature sources, e.g. concepts/) — design
# §7c: "$$…$$", "\begin{equation}…\end{equation}" (incl. the starred form),
# "\[…\]".
_DISPLAY_MATH_RE = re.compile(
    r"\$\$(?P<dollar>.*?)\$\$"
    r"|\\begin\{equation\*?\}(?P<env>.*?)\\end\{equation\*?\}"
    r"|\\\[(?P<bracket>.*?)\\\]",
    re.DOTALL,
)


def _extract_key_equations_section(body: str) -> str | None:
    """Return the ``## Key equations`` section text, or ``None`` if absent."""
    m = _EQ_SECTION_RE.search(body)
    return m.group(1) if m else None


def _split_labeled_equation_blocks(section_text: str) -> list[dict[str, Any]]:
    """Split a ``## Key equations`` section into labeled blocks.

    Returns a list of ``{"label", "title", "latex", "inline_critical_marker"}``
    dicts, one per ``### [label] Title`` header, in document order. The
    inline ``*(critical)*`` annotation (human-readable documentation in the
    body) is stripped from the title and recorded separately — it is NOT the
    authority for criticality (the frontmatter ``key_equations:`` ledger is,
    per design §7b: "join each block to its FM ``critical:`` flag"); a
    mismatch between the two is surfaced by the caller, never silently
    resolved by trusting the body marker.
    """
    headers = list(_EQ_HEADER_RE.finditer(section_text))
    blocks: list[dict[str, Any]] = []
    for i, h in enumerate(headers):
        label = h.group(1).strip()
        title_raw = h.group(2)
        inline_critical = bool(_INLINE_CRITICAL_MARKER_RE.search(title_raw))
        title = _INLINE_CRITICAL_MARKER_RE.sub("", title_raw).strip()
        start = h.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(section_text)
        latex = section_text[start:end].strip()
        blocks.append({
            "label": label,
            "title": title,
            "latex": latex,
            "inline_critical_marker": inline_critical,
        })
    return blocks


def _extract_literature_equations(
    note_path: Path, fields: dict[str, Any], body: str
) -> list[dict[str, Any]]:
    """Extract the structured (label + criticality-joined) ledger for a
    ``literature/`` note. Empty list if the note has no ``## Key equations``
    section — a silent, correct no-op (most papers have no pivotal
    equations; charter §2 distinguishes this from a missing-data error).

    ★ The join is BY LABEL, exact-match (the L1 review catch) — label uniqueness
    within a note is assumed (the relate agent's contract, PR-L1). A
    frontmatter entry whose label has no matching body block (or vice versa)
    is simply not joined — the entry contributes no ledger row for that
    label (never a crash; the frontmatter ledger and the body section are
    independently optional/absent-tolerant per PR-L1).
    """
    section_text = _extract_key_equations_section(body)
    if section_text is None:
        return []

    blocks = _split_labeled_equation_blocks(section_text)
    if not blocks:
        return []

    fm_ledger = fields.get("key_equations", "")
    criticality_by_label: dict[str, bool] = {}
    if isinstance(fm_ledger, list):
        for entry in fm_ledger:
            if not isinstance(entry, dict):
                continue
            label = entry.get("label", "").strip()
            if not label:
                continue
            criticality_by_label[label] = str(entry.get("critical", "")).strip().lower() == "true"

    entries: list[dict[str, Any]] = []
    for block in blocks:
        label = block["label"]
        critical = criticality_by_label.get(label)  # None = unmarked (no FM entry for this label)
        entries.append({
            "note": str(note_path),
            "label": label,
            "title": block["title"],
            "latex": block["latex"],
            "critical": critical,
        })
    return entries


def _extract_generic_display_math(note_path: Path, body: str) -> list[dict[str, Any]]:
    """Generic display-math scan for a non-``literature`` source note (e.g.
    ``concepts/``) — no criticality ledger to join, so every equation is
    ``critical=None`` (unmarked; the fidelity gate treats this as a SIGNAL-
    class candidate, never a BLOCK, and never guesses criticality)."""
    entries: list[dict[str, Any]] = []
    stem = note_path.stem
    for i, m in enumerate(_DISPLAY_MATH_RE.finditer(body)):
        latex = (m.group("dollar") or m.group("env") or m.group("bracket") or "").strip()
        if not latex:
            continue
        entries.append({
            "note": str(note_path),
            "label": f"{stem}-eq{i + 1}",
            "title": "",
            "latex": latex,
            "critical": None,
        })
    return entries


# ---------------------------------------------------------------------------
# (c) The extractor — public entry point
# ---------------------------------------------------------------------------

def extract_equation_ledger(
    project_notes_dir: Path,
    equation_sources: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Mine ``equation_sources`` notes for pivotal equations -> a ledger.

    When to use: called by the manuscript loop's Phase-2 scaffolder
    (``manuscript/__init__.py``) to build the ledger injected into the
    writer's context (``inject_equation_brief``) and later re-checked by
    ``check_equation_fidelity``. ``equation_sources`` is a
    ``ManuscriptType``'s declared tuple of OKF type names (e.g.
    ``("concepts", "literature")`` for ``lit-review``) — type-generic, no
    hardcoded source list here.

    Deterministic, zero-hallucination: every entry traces to a note path +
    verbatim body LaTeX. A source directory that doesn't exist yet (a fresh
    project) contributes zero entries — not an error.

    Args:
        project_notes_dir: the project's notes root (``cfg.project_notes_dir``).
        equation_sources: OKF type names to mine (e.g. from
            ``ms_type.equation_sources``).

    Returns:
        A list of ``{"note", "label", "title", "latex", "critical"}`` dicts,
        in (source-dir, filename, in-body-order) order. Empty list = no
        pivotal equations anywhere in the mined sources — a paper (or corpus)
        with no equations is a silent, correct no-op.

    sr: PR-M4
    """
    ledger: list[dict[str, Any]] = []
    for okf_type in equation_sources:
        source_dir = project_notes_dir / okf_type
        if not source_dir.exists():
            continue
        for note_path in sorted(source_dir.glob("*.md")):
            text = note_path.read_text(encoding="utf-8")
            fields, body = _parse_frontmatter(text)
            if fields.get("type") == "literature":
                ledger.extend(_extract_literature_equations(note_path, fields, body))
            else:
                ledger.extend(_extract_generic_display_math(note_path, body))
    return ledger


# ---------------------------------------------------------------------------
# (a) Writer-brief injection — the ledger REQUIRES pivotal equations
# ---------------------------------------------------------------------------

def build_equation_ledger_brief_block(ledger: list[dict[str, Any]]) -> str:
    """Render the ledger as an injectable brief block, or ``""`` if empty.

    Empty ledger -> empty string is the no-equations no-op contract: a
    caller that appends ``""`` to a tip changes nothing (no error, no
    dangling instruction about equations that don't exist).

    sr: PR-M4
    """
    if not ledger:
        return ""

    lines = [
        "★ REQUIRE — pivotal equations from your source notes (design §7):",
        "The following equations are DATA extracted from your source notes — "
        "they are injected here VERBATIM. Where your argument turns on one of "
        "these, reproduce it as BLOCK LaTeX (\\begin{equation}...\\end{equation} "
        "or $$...$$), never as prose paraphrase. Do NOT re-type or re-derive "
        "them from memory — copy the LaTeX below exactly.",
        "",
    ]
    for entry in ledger:
        marker = "critical" if entry.get("critical") else (
            "unmarked" if entry.get("critical") is None else "non-critical"
        )
        title = entry.get("title") or entry.get("label", "")
        lines.append(f"[{entry.get('label', '?')}] {title}  ({marker}, source: {entry.get('note', '?')})")
        lines.append(entry.get("latex", ""))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def inject_equation_brief(
    tips: dict[str, str],
    ledger: list[dict[str, Any]],
    section_set: Any,
    equation_sources: tuple[str, ...],
) -> dict[str, str]:
    """Append the ledger's REQUIRE block to every section brief that reads
    from one of ``equation_sources``.

    When to use: called by the manuscript loop's Phase-2 scaffolder right
    after ``get_manuscript_section_tips`` builds the per-section tips dict
    (``manuscript/__init__.py``'s ``_build_phase2_manifest``) — a thin,
    additive wiring step (design §7a: "wire this into the type's writer
    brief / style seam").

    A paper (or corpus) with no equations -> ``ledger`` is empty ->
    ``build_equation_ledger_brief_block`` returns ``""`` -> every tip is
    appended with an empty string, i.e. unchanged. No-op, no error.

    Args:
        tips: the section-key -> tip-string dict (mutated copy returned;
            the input dict is not mutated in place).
        ledger: the equation ledger from ``extract_equation_ledger``.
        section_set: the ``ManuscriptType.section_set`` (a tuple of
            ``SectionSpec``-shaped objects with ``.name``, ``.brief_key``,
            ``.source_atoms``).
        equation_sources: the type's declared equation-source OKF types.

    Returns:
        A NEW dict (copy of ``tips``) with the ledger block appended to the
        relevant sections' tips.

    sr: PR-M4
    """
    block = build_equation_ledger_brief_block(ledger)
    result = dict(tips)
    if not block:
        return result

    sources = set(equation_sources)
    for section in section_set:
        if not sources.intersection(section.source_atoms):
            continue
        key = section.brief_key or section.name
        existing = result.get(key, "")
        result[key] = (existing.rstrip() + "\n\n" + block) if existing else block
    return result


# ---------------------------------------------------------------------------
# (b) The equation-fidelity gate
# ---------------------------------------------------------------------------

def _normalize_latex(latex: str) -> str:
    """Normalize a LaTeX equation string for structural comparison.

    Strips display-math delimiters (``$$``, ``\\[``/``\\]``,
    ``\\begin{equation}``/``\\end{equation}``) and ALL whitespace — a
    deterministic, delimiter-and-formatting-agnostic normalization (not a
    semantic one; re-typeset-but-equivalent forms that differ structurally
    fall through to the ``judge_fn`` fallback).
    """
    s = re.sub(r"\\begin\{equation\*?\}|\\end\{equation\*?\}", "", latex)
    s = s.replace("$$", "").replace("\\[", "").replace("\\]", "")
    s = re.sub(r"\s+", "", s)
    return s


def _extract_draft_equations(draft_text: str) -> list[str]:
    """Return every normalized display-math block in ``draft_text``."""
    out: list[str] = []
    for m in _DISPLAY_MATH_RE.finditer(draft_text):
        raw = m.group("dollar") or m.group("env") or m.group("bracket") or ""
        norm = _normalize_latex(raw)
        if norm:
            out.append(norm)
    return out


def _deterministic_match(norm_ledger_eq: str, draft_equations: list[str]) -> bool:
    """True if ``norm_ledger_eq`` matches a normalized draft equation.

    ★ Integration-PR tightening (reviewer-caught false negative): the ORIGINAL
    bidirectional substring check (``norm_ledger_eq in draft_eq OR draft_eq in
    norm_ledger_eq``) let a short, UNRELATED draft equation mask a longer
    DROPPED ledger equation whenever the short fragment happened to occur as
    a substring of the (missing) long one — e.g. a draft equation ``a+b``
    would satisfy a dropped ``x=a+b+c+d`` because ``"a+b" in "x=a+b+c+d"``.
    That is exactly the silent-drop failure mode this gate exists to catch.

    Tightened to ONE direction — the ledger equation may be found contained
    WITHIN a draft equation (tolerant of the draft carrying a little extra
    wrapping/padding the normalize step didn't strip), never the reverse —
    and LENGTH-GATED so a short ledger equation can't spuriously match deep
    inside an unrelated, much-longer draft equation by coincidence.
    """
    if not norm_ledger_eq:
        return False
    for draft_eq in draft_equations:
        if norm_ledger_eq == draft_eq:
            return True
        if norm_ledger_eq in draft_eq and len(draft_eq) <= len(norm_ledger_eq) * 1.2 + 10:
            return True
    return False


def check_equation_fidelity(
    ledger: list[dict[str, Any]],
    draft_text: str,
    *,
    judge_fn: Callable[[dict[str, Any], str], bool] | None = None,
) -> list[dict[str, Any]]:
    """The equation-fidelity gate — SIGNAL, never BLOCK (★ D-MS-2, the
    operator's explicit call).

    For each ledger equation: deterministic normalized-LaTeX match against
    the draft's display-math blocks FIRST; if no match and ``judge_fn`` is
    supplied, fall back to it for a re-typeset-but-equivalent form
    (``honesty-gates.md`` §5 fail-closed discipline — a judge exception, or
    no ``judge_fn`` at all, means "absent", never "probably fine").

    ★ Class: EVERY finding here is a SIGNAL (surfaced to the human review
    loop for triage), regardless of ``critical``. This design doc's own §7b
    text recommends BLOCK for marked-critical — the operator's explicit override
    (D-MS-2) is SIGNAL-only throughout: some manuscripts genuinely have no
    equations, and a drop must never fail the build. ``severity`` still
    distinguishes ``"critical"`` (marked ``critical: true``) from
    ``"unmarked"`` (no criticality data — old note, or a non-literature
    source with no ledger to join) from ``"non-critical"`` (explicitly
    marked ``critical: false``, still worth surfacing since design §7c calls
    an unmarked-absent display equation a SIGNAL too) so the review loop can
    prioritize triage without the gate itself ever hard-failing.

    Args:
        ledger: from ``extract_equation_ledger``.
        draft_text: the assembled draft (e.g. the joined ``sections/*.tex``
            or ``main.tex`` content) to check equations against.
        judge_fn: optional ``(ledger_entry, draft_text) -> bool`` callable —
            ``True`` means "this equation IS represented in the draft
            (re-typeset)". Any exception is treated as ``False`` (fail-
            closed — charter §2/honesty-gates.md §5).

    Returns:
        A list of finding dicts (empty = every ledger equation was found —
        including the trivial case of an empty ledger, i.e. no equations to
        check, which is always ``[]``, never an error). Each finding:
        ``{"note", "label", "title", "critical", "severity", "class",
        "message"}`` — ``"class"`` is always the literal string ``"SIGNAL"``.

    sr: PR-M4
    """
    if not ledger:
        return []

    draft_equations = _extract_draft_equations(draft_text)
    findings: list[dict[str, Any]] = []

    for entry in ledger:
        norm = _normalize_latex(entry.get("latex", ""))
        if _deterministic_match(norm, draft_equations):
            continue

        judged_present = False
        if judge_fn is not None:
            try:
                judged_present = bool(judge_fn(entry, draft_text))
            except Exception:
                # Fail-closed (honesty-gates.md §5): a judge error is treated
                # as "not represented" — never silently pass.
                judged_present = False
        if judged_present:
            continue

        critical = entry.get("critical")
        if critical is True:
            severity = "critical"
        elif critical is False:
            severity = "non-critical"
        else:
            severity = "unmarked"

        findings.append({
            "note": entry.get("note", ""),
            "label": entry.get("label", ""),
            "title": entry.get("title", ""),
            "critical": critical,
            "severity": severity,
            "class": "SIGNAL",
            "message": (
                f"equation {entry.get('label', '?')!r} "
                f"({'marked critical' if critical else 'unmarked' if critical is None else 'marked non-critical'}) "
                f"from {entry.get('note', '?')} not found in the draft (deterministic match"
                f"{' + judge' if judge_fn is not None else ''} both missed) — SIGNAL, not BLOCK "
                f"(D-MS-2): surfaced for the review loop, build not failed."
            ),
        })

    return findings
