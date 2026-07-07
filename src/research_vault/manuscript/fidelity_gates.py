"""fidelity_gates.py — manuscript-loop THIN ADAPTER over the shared gates (PR-M3).

The two hard fidelity gates re-instantiated at PR-M3 — the claim->source
support-matcher and the self-containment cold-read judge — live in the
SHAREABLE ``research_vault.gates`` package (D-SV-0), not here. This module is
the manuscript-loop's own thin, additive wiring on top of them:

  check_support_tally(tree_root, ...) — walks every ``*.tex`` file under a
      manuscript tree, finds every sentence carrying a ``\\cite{key}``, and
      calls ``gates.support_matcher.match_support()`` once per (sentence,
      citekey) pair. Runs the blind-judge canary FIRST (honesty-gates.md §4)
      — a known-supported synthetic probe through the SAME extractor+judge
      path; if it comes back [ABSENT], the judge/extractor is blind and the
      whole tally aborts loudly rather than emit false-BLOCKs.

  check_cold_read_tally(tree_root, ...) — resolves the manuscript's rendered
      text (pdftotext output if a PDF exists, else a main.tex/sections/*.tex
      fallback) and calls ``gates.coldread.run_cold_read()`` once. The
      bidirectional canary + Flag-A deterministic scan both live inside
      ``run_cold_read`` itself; this adapter only composes the honest
      errors/warnings list from the returned ``ColdReadResult``.

Both functions return a plain dict (not a dataclass) — the same shape the
design's §10 hard-fidelity-gate section expects an ``rv manuscript check``
payload assembler (out of scope here — PR-M6/M8 territory) to consume:
``errors`` (BLOCK-level strings), ``warnings`` (WARN-level strings),
``honest_report`` (never says "verified"), and ``canary_aborted``.

Design: docs/superpowers/specs/2026-07-07-survey-capability-design.md §10.
Doctrine: data/doctrine/honesty-gates.md, data/doctrine/review-board.md.

SCOPE — additive, minimal shared-seam edit:
  This file does NOT touch ``manuscript/check_gates.py`` (the fuller
  structural+semantic gate assembly is PR-M2/PR-M6 territory, built
  concurrently). It is a standalone new module the future
  ``build_approve_payload`` assembler can import from once it lands.

Stdlib only. Hermetic in tests (judge_fn is always injectable — no live LLM
call required to exercise this module).
sr: PR-M3
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable

from research_vault.gates.support_matcher import match_support
from research_vault.gates.coldread import run_cold_read

# Opus-tier model for semantic judgment gates (D-MS-4). Resolved via
# RV_JUDGE_MODEL env var; never pinned to a versioned ID in source.
_DEFAULT_JUDGE_MODEL: str = os.environ.get("RV_JUDGE_MODEL", "")

# Same pattern as gates.support_matcher / bib.py (inline to avoid an import
# cycle with the not-yet-built manuscript.bib / manuscript.check_gates).
_CITE_RE = re.compile(r"\\cite[a-z]*\*?\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")


def _strip_comments(text: str) -> str:
    """Strip LaTeX line comments (% to end of line, excluding \\%)."""
    lines: list[str] = []
    for line in text.split("\n"):
        stripped = line
        i = 0
        while i < len(line):
            if line[i] == "%" and (i == 0 or line[i - 1] != "\\"):
                stripped = line[:i]
                break
            i += 1
        lines.append(stripped)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# check_support_tally — batch support-match over a manuscript tree
# ---------------------------------------------------------------------------

def check_support_tally(
    tree_root: Path,
    *,
    notes_root: Path | None = None,
    judge_fn: Callable[[str], str] | None = None,
    judge_model: str = _DEFAULT_JUDGE_MODEL,
    rubric_override: str | None = None,
    config: Any | None = None,
) -> dict[str, Any]:
    r"""Run the claim->source support-matcher on all (sentence, \\cite{key}) pairs.

    For each sentence containing a \\cite{}, calls gates.support_matcher's
    match_support() with the cited literature/ note's structured fields.

    Returns a dict with:
      "verdicts":      list of SupportVerdict (one per (sentence, citekey) pair)
      "n_sentences":   int
      "m_citations":   int
      "k_block":       int (ABSENT or CONTRADICTS)
      "j_warn":        int (PARTIAL)
      "honest_report": str — "N sentences, M citations, k BLOCK, j WARN"
      "errors":        list of BLOCK-level strings
      "warnings":      list of WARN-level strings
      "canary_aborted": bool

    BLOCK on [ABSENT] / [CONTRADICTS]; WARN on [PARTIAL].
    Honest output: 'N sentences, M citations, k BLOCK, j WARN' — never 'verified'.

    When notes_root is None: inferred from tree_root (manuscripts/<id>/ ->
    the project notes root two levels up).
    """
    tex_files = list(tree_root.rglob("*.tex"))
    if not tex_files:
        return {
            "verdicts": [], "n_sentences": 0, "m_citations": 0,
            "k_block": 0, "j_warn": 0,
            "honest_report": "0 sentences, 0 citations, 0 BLOCK, 0 WARN",
            "errors": [], "warnings": [],
            "canary_aborted": False,
        }

    _notes_root = notes_root
    if _notes_root is None:
        _notes_root = tree_root.parent.parent  # manuscripts/<id>/ -> project root

    # ── Blind-judge canary (honesty-gates.md §4) ────────────────────────────
    # Before running the real tally, run one synthetic KNOWN-SUPPORTED probe
    # through the SAME extractor+judge pipeline. If it returns [ABSENT], the
    # judge is blind (extraction empty or judge mis-wired) — indistinguishable
    # from a real refutation. ABORT the gate LOUDLY rather than surface the
    # BLOCKs below as if they were real.
    with tempfile.TemporaryDirectory() as _canary_dir:
        _canary_note = Path(_canary_dir) / "canary_probe.md"
        _canary_note.write_text(
            "---\ntype: literature\n---\n"
            "## Result\n"
            "The accuracy on the benchmark is 85.3%, a statistically significant "
            "improvement over the 80.1% baseline (p < 0.01).\n",
            encoding="utf-8",
        )
        _canary_claim = (
            "The model achieves 85.3% accuracy, significantly above the 80.1% baseline."
        )
        try:
            _canary_verdict = match_support(
                claim=_canary_claim,
                citekey="canary_probe_known_positive",
                note_path=_canary_note,
                rubric_override=rubric_override,
                config=config,
                judge_fn=judge_fn,
                judge_model=judge_model,
            )
        except Exception:  # noqa: BLE001
            _canary_verdict = None

        _canary_absent = _canary_verdict is None or _canary_verdict.verdict == "ABSENT"
        if _canary_absent:
            _abort_msg = (
                "support-judge appears blind on a known-supported probe — "
                "extraction or judge mis-wired; the BLOCKs below are NOT real "
                "refutations. Fix wiring before trusting this gate."
            )
            return {
                "verdicts": [],
                "n_sentences": 0,
                "m_citations": 0,
                "k_block": 0,
                "j_warn": 0,
                "honest_report": "0 sentences, 0 citations, 0 BLOCK, 0 WARN (CANARY ABORTED)",
                "errors": [_abort_msg],
                "warnings": [],
                "canary_aborted": True,
            }

    # ── Collect every (sentence, citekey, section) triple ───────────────────
    all_items: list[tuple[str, str, str]] = []
    for tex in tex_files:
        if not tex.exists():
            continue
        try:
            text = tex.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = _strip_comments(text)
        section_name = tex.stem
        sentences = re.split(r"(?<=[.!?])\s+|\n{2,}", text)
        for sent in sentences:
            sent = sent.strip()
            if not sent:
                continue
            for cm in _CITE_RE.finditer(sent):
                for k in cm.group(1).split(","):
                    k = k.strip()
                    if k:
                        all_items.append((sent, k, section_name))

    verdicts: list[Any] = []
    errors: list[str] = []
    warnings: list[str] = []
    n_sentences = len({item[0] for item in all_items})
    m_citations = len(all_items)

    for sentence, citekey, section in all_items:
        note_path = _notes_root / "literature" / f"{citekey}.md"
        if not note_path.exists():
            note_path = _notes_root / f"{citekey}.md"

        stance: str | None = None
        plan_role: str | None = None
        if note_path.exists():
            try:
                ntext = note_path.read_text(encoding="utf-8")
            except OSError:
                ntext = ""
            from research_vault.note import _parse_frontmatter as _pfm
            nf, _ = _pfm(ntext)
            stance = nf.get("stance") or None
            plan_role = nf.get("plan_role") or None

        v = match_support(
            claim=sentence,
            citekey=citekey,
            note_path=note_path,
            stance=stance,
            plan_role=plan_role,
            rubric_override=rubric_override,
            config=config,
            judge_fn=judge_fn,
            judge_model=judge_model,
            section=section,
        )
        verdicts.append(v)

        if v.blocks:
            errors.append(
                f"support-matcher [{v.verdict}] BLOCK: \\cite{{{citekey}}} — "
                f"claim: '{sentence[:120]}' — "
                f"quoted span: {v.verbatim_span or 'none'} — "
                f"reasoning: {v.reasoning[:200]}"
            )
        elif v.warns:
            warnings.append(
                f"support-matcher [PARTIAL] WARN: \\cite{{{citekey}}} — "
                f"claim: '{sentence[:120]}' — "
                f"reasoning: {v.reasoning[:200]}"
            )

    k_block = sum(1 for v in verdicts if v.blocks)
    j_warn = sum(1 for v in verdicts if v.warns)

    return {
        "verdicts": verdicts,
        "n_sentences": n_sentences,
        "m_citations": m_citations,
        "k_block": k_block,
        "j_warn": j_warn,
        "honest_report": (
            f"{n_sentences} sentences, {m_citations} citations, {k_block} BLOCK, {j_warn} WARN"
        ),
        "errors": errors,
        "warnings": warnings,
        "canary_aborted": False,
    }


# ---------------------------------------------------------------------------
# check_cold_read_tally — the self-containment gate over a manuscript tree
# ---------------------------------------------------------------------------

def check_cold_read_tally(
    tree_root: Path,
    *,
    judge_fn: Callable[[str], str] | None = None,
    judge_model: str = _DEFAULT_JUDGE_MODEL,
    rubric_override: str | None = None,
    config: Any | None = None,
    pdf_text: str | None = None,
) -> dict[str, Any]:
    """Run the LLM cold-read self-containment judge on the compiled manuscript.

    Two-layer gate:
      Layer 1 (hermetic, inside gates.coldread.run_cold_read): Flag-A
        deterministic scan over the pdftotext output.
      Layer 2 (LLM, inside run_cold_read): a fresh-reader judge that flags
        every reference that doesn't resolve from the paper alone.

    Both the bidirectional canary and Flag-A run inside run_cold_read() —
    this adapter only resolves the manuscript's text and composes the
    honest errors/warnings list from the returned ColdReadResult.

    Args:
        tree_root:       path to the manuscript artifact tree (manuscripts/<id>/).
        judge_fn:        injectable LLM call (prompt: str) -> str. Mock in tests.
        judge_model:     the model-id to log (D-MS-4: Opus-tier).
        rubric_override: optional rubric override.
        config:          optional Config for rubric key lookup.
        pdf_text:        optional pre-extracted pdftotext output. When None,
                         attempts pdftotext on any PDF in tree_root; falls back
                         to reading main.tex + sections/*.tex.

    Returns a dict with:
      "flags", "flag_a_hits", "overall", "block_count", "warn_count",
      "honest_report", "errors", "warnings", "canary_aborted", "meta".

    BLOCK on [DANGLING] (LLM) or any Flag-A hit; WARN on [NEEDS-CONTEXT].
    """
    import shutil
    import subprocess

    resolved_pdf_text = pdf_text
    if resolved_pdf_text is None:
        pdf_files = list(tree_root.glob("*.pdf"))
        if pdf_files and shutil.which("pdftotext"):
            try:
                r = subprocess.run(
                    ["pdftotext", str(pdf_files[0]), "-"],
                    capture_output=True, text=True, timeout=60,
                )
                if r.returncode == 0 and r.stdout.strip():
                    resolved_pdf_text = r.stdout
            except (subprocess.TimeoutExpired, OSError):
                pass

        if resolved_pdf_text is None:
            ms_text_parts: list[str] = []
            main_tex = tree_root / "main.tex"
            if main_tex.exists():
                try:
                    ms_text_parts.append(main_tex.read_text(encoding="utf-8", errors="replace")[:4000])
                except OSError:
                    pass
            sections_dir = tree_root / "sections"
            if sections_dir.exists():
                for tex in sorted(sections_dir.glob("*.tex"))[:6]:
                    try:
                        ms_text_parts.append(tex.read_text(encoding="utf-8", errors="replace")[:1500])
                    except OSError:
                        pass
            resolved_pdf_text = "\n\n".join(ms_text_parts) if ms_text_parts else ""

    if not resolved_pdf_text.strip():
        return {
            "flags": [],
            "flag_a_hits": [],
            "overall": "STANDS-ALONE",
            "block_count": 0,
            "warn_count": 0,
            "honest_report": "0 passages, 0 LLM BLOCK, 0 LLM WARN, 0 Flag-A BLOCK (no text extracted)",
            "errors": [],
            "warnings": ["cold-read: no PDF text extracted — pdftotext absent or PDF not compiled yet"],
            "canary_aborted": False,
            "meta": {},
        }

    result = run_cold_read(
        resolved_pdf_text,
        rubric_override=rubric_override,
        config=config,
        judge_fn=judge_fn,
        judge_model=judge_model,
    )

    errors: list[str] = []
    warnings: list[str] = []

    if result.canary_aborted:
        errors.append(f"cold-read gate ABORTED: {result.abort_reason}")
        return {
            "flags": [],
            "flag_a_hits": result.flag_a_hits,
            "overall": "STANDS-ALONE",
            "block_count": 0,
            "warn_count": 0,
            "honest_report": result.honest_report,
            "errors": errors,
            "warnings": warnings,
            "canary_aborted": True,
            "meta": result.to_meta_dict(),
        }

    if result.overall == "UNPARSEABLE":
        errors.append(
            "cold-read [UNPARSEABLE] BLOCK: judge returned malformed output on the real "
            "paper (no SUMMARY block or unrecognized OVERALL token). "
            "Flag-A is deterministic and covers hash/path shapes only — a malformed "
            "real-paper response cannot certify the paper. "
            "Check judge model / rubric wiring and re-run."
        )

    for hit in result.flag_a_hits:
        errors.append(f"cold-read [Flag-A] BLOCK: {hit}")

    for fl in result.flags:
        if fl.verdict == "DANGLING":
            errors.append(
                f"cold-read [DANGLING] BLOCK: span: '{fl.span[:120]}' — "
                f"kind: {fl.kind} — missing: {fl.missing[:200]}"
            )
        elif fl.verdict == "NEEDS-CONTEXT":
            warnings.append(
                f"cold-read [NEEDS-CONTEXT] WARN: span: '{fl.span[:120]}' — "
                f"kind: {fl.kind} — missing: {fl.missing[:200]}"
            )

    return {
        "flags": result.flags,
        "flag_a_hits": result.flag_a_hits,
        "overall": result.overall,
        "block_count": result.block_count,
        "warn_count": result.warn_count,
        "honest_report": result.honest_report,
        "errors": errors,
        "warnings": warnings,
        "canary_aborted": False,
        "meta": result.to_meta_dict(),
    }
