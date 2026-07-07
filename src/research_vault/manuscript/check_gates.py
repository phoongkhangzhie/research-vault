"""manuscript/check_gates.py — the INTEGRATION PR: assemble the manuscript-loop
gates the parallel wave (M2/M3/M4/M6) built but never wired together.

``build_approve_payload`` is the single entry point ``rv dag approve`` calls at
the ``approve-manuscript`` node (mirrors ``check_framework_gate``'s wiring at
``approve-framework``, PR-M6) — and the future PR-M5 per-round review-revise
re-fire is designed to import THIS function rather than duplicate the gate
assembly (single-sourced, per the integration-PR brief).

Assembles the four gates by HONESTY-CLASS, per the operator's LOCKED
judge-guard policy (the resolved call carried in the dispatching brief — see
``manuscript/equations.py``'s D-MS-2 note for the parallel precedent: an
explicit operator override on a design doc's own recommendation is followed,
and the divergence is documented, not silently applied):

  - ``check_hermetic_bib`` (``manuscript/bib.py``, PR-M2)         -> hard BLOCK,
    deterministic, ALWAYS runs (no judge dependency at all).
  - ``check_equation_fidelity`` (``manuscript/equations.py``, PR-M4) -> SIGNAL
    ONLY (D-MS-2 — never BLOCK, even marked-critical). Deterministic; ALWAYS
    runs (no judge dependency — the LLM-judge fallback inside the gate itself
    is a separate, optional refinement not wired here).
  - ``check_support_tally`` (``manuscript/fidelity_gates.py``, PR-M3) -> BLOCK
    on ``[ABSENT]``/``[CONTRADICTS]`` (the citation-fidelity FLOOR) — BEHIND
    the judge guard.
  - ``check_cold_read_tally`` (``manuscript/fidelity_gates.py``, PR-M3)
    -> SIGNAL — BEHIND the judge guard.

**The judge guard** (design doctrine: ``honesty-gates.md`` fail-closed
discipline, applied honestly in the OTHER direction here): the two LLM gates
run ONLY when a judge is configured — ``RV_JUDGE_MODEL`` + ``ANTHROPIC_API_KEY``
both set in the environment, OR an explicit ``judge_fn`` is passed in (tests
inject a mock this way; that counts as "configured"). When neither is present,
the LLM gates are NOT silently skipped — they land in the payload's
``not_run`` list with a LOUD message surfaced at the human-go (charter §2:
surface, never silently drop; never green-and-empty). This is NOT a hard
block: a manuscript with no judge configured can still reach
``approve-manuscript`` on the deterministic bib gate alone, but the human
sees, unmistakably, that the citation-fidelity floor was never checked.

The coverage gate (design §10 gate-4) LANDS HERE at PR-M5 — deterministic,
ALWAYS runs, hard BLOCK. ``check_coverage_gate`` re-derives the frozen
corpus's citekey set from ``reviews/<slug>/_corpus.md`` (the same
``review._parse_corpus_citekeys`` source-of-truth ``review.coverage_report()``
uses) and BLOCKs on either (a) the stamped ``corpus_hash`` no longer matching
the frozen ``_corpus.md`` bytes (the corpus mutated since the Phase-1 freeze
— the stale-corpus guard, design §4.5.5), or (b) the draft's own rendered
PRISMA-ledger corpus count reading SMALLER than the true frozen corpus count
(a revise that narrows scope to shrink the denominator, design §10 gate-4's
literal example). A manuscript with no ``corpus_hash`` stamped yet (no
frozen corpus to check against — a type with no Phase-1, or a lit-review
whose framework isn't approved yet) is a correct, honest no-op — never a
BLOCK for absence, mirroring the ``doi``/``arxiv_id`` precedent elsewhere.

Design: docs/superpowers/specs/2026-07-07-survey-capability-design.md §9-§10.
Doctrine: data/doctrine/honesty-gates.md.

Stdlib only. Hermetic in tests — the judge guard means a bare call with no
env vars and no judge_fn never reaches out to a live LLM.
sr: manuscript-integration (post PR-M2/M3/M4/M6)
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable

from research_vault.manuscript.bib import check_hermetic_bib
from research_vault.manuscript import equations as _equations
from research_vault.manuscript import fidelity_gates as _fidelity_gates
from research_vault.note import _parse_frontmatter as _pfm_gates
from research_vault.review import _parse_corpus_citekeys


# ---------------------------------------------------------------------------
# Judge-guard predicate
# ---------------------------------------------------------------------------

def _judge_configured(judge_fn: Callable[[str], str] | None) -> bool:
    """True iff a judge is usable — env vars set, OR an explicit judge_fn.

    An explicit ``judge_fn`` (the test-injection seam every gate already
    supports) counts as "configured" even with no env vars — this is what
    lets the calibration/mock tests exercise the LLM-gate branch hermetically
    without ever touching a live model.
    """
    if judge_fn is not None:
        return True
    return bool(os.environ.get("RV_JUDGE_MODEL", "").strip()) and bool(
        os.environ.get("ANTHROPIC_API_KEY", "").strip()
    )


# ---------------------------------------------------------------------------
# Draft-text assembly (shared by the equation gate — the whole draft, not
# just one section — mirrors check_cold_read_tally's own main.tex+sections
# fallback resolution, PR-M3).
# ---------------------------------------------------------------------------

def _read_draft_text(tree_root: Path) -> str:
    """Join ``main.tex`` + every ``sections/*.tex`` into one draft-text blob.

    Best-effort, never raises: an unreadable/missing file simply contributes
    nothing (a fresh manuscript folder with no draft yet -> empty string,
    which every gate treats as "nothing to check yet", never an error).
    """
    parts: list[str] = []
    main_tex = tree_root / "main.tex"
    if main_tex.exists():
        try:
            parts.append(main_tex.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    sections_dir = tree_root / "sections"
    if sections_dir.exists():
        for tex in sorted(sections_dir.glob("*.tex")):
            try:
                parts.append(tex.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# check_coverage_gate — design §10 gate-4, PR-M5's scope (deterministic,
# ALWAYS runs, hard BLOCK — no judge dependency)
# ---------------------------------------------------------------------------

# PRISMA-ledger corpus-count line, rendered by
# manuscript.types.lit_review.render_prisma_ledger: "| Corpus (frozen
# citekeys) | N |". Parsed to detect a revise that narrows scope by
# re-stating a smaller denominator than the true frozen corpus.
_PRISMA_CORPUS_COUNT_RE = re.compile(
    r"\|\s*Corpus \(frozen citekeys\)\s*\|\s*(\d+)\s*\|"
)


def check_coverage_gate(
    project_notes_dir: Path,
    tree_root: Path,
) -> dict[str, Any]:
    """Re-run the coverage check on the revised corpus (design §10 gate-4).

    When to use: called by ``build_approve_payload`` every time it assembles
    the gate payload — including PR-M5's per-round re-fire (same function,
    single-sourced, never duplicated). Deterministic, no LLM, ALWAYS runs.

    Convention (shared with ``manuscript.types.lit_review._compute_corpus_hash_note``):
    a manuscript slug (``tree_root.name``) matches the ``rv review`` scope id
    whose frozen corpus lives at ``reviews/<slug>/_corpus.md``.

    BLOCKs on:
      (a) ``corpus_hash`` stamped in ``_manuscript.md`` no longer matches the
          hash of the frozen ``_corpus.md`` on disk (the corpus mutated since
          the Phase-1 freeze — the stale-corpus guard, design §4.5.5), or the
          stamped hash points at a ``_corpus.md`` that no longer exists.
      (b) the draft's own rendered PRISMA-ledger corpus-count line states a
          SMALLER corpus than the true frozen corpus (a revise narrowing
          scope to shrink the denominator, design §10 gate-4's literal
          example).

    A manuscript with no ``corpus_hash`` stamped yet is a correct, honest
    no-op (nothing frozen to verify against yet — never a BLOCK for absence,
    mirroring the ``doi``/``arxiv_id`` precedent).

    Args:
        project_notes_dir: the project's OKF notes root.
        tree_root: the manuscript folder (``manuscripts/<slug>/``).

    Returns:
        ``{"ok": bool, "errors": [...], "warnings": [...]}``.

    sr: PR-M5
    """
    from research_vault.hashing import hash_file

    errors: list[str] = []
    warnings: list[str] = []

    manuscript_note_path = tree_root / "_manuscript.md"
    if not manuscript_note_path.exists():
        # No control note at all — nothing to check against; the hermetic
        # .bib gate already covers "manuscript folder missing" concerns.
        return {"ok": True, "errors": [], "warnings": []}

    fields, _ = _pfm_gates(manuscript_note_path.read_text(encoding="utf-8"))
    stamped_hash = str(fields.get("corpus_hash", "")).strip()
    if not stamped_hash:
        warnings.append(
            "coverage-gate: no corpus_hash stamped in _manuscript.md yet — "
            "skipping (nothing frozen to verify scope against)."
        )
        return {"ok": True, "errors": [], "warnings": warnings}

    slug = tree_root.name
    corpus_path = project_notes_dir / "reviews" / slug / "_corpus.md"
    if not corpus_path.exists():
        errors.append(
            f"coverage-gate BLOCK: corpus_hash is stamped ({stamped_hash[:16]}...) "
            f"but the frozen corpus {corpus_path} no longer exists — cannot "
            f"verify the corpus hasn't narrowed since the Phase-1 freeze."
        )
        return {"ok": False, "errors": errors, "warnings": warnings}

    current_hash = hash_file(corpus_path)
    if current_hash != stamped_hash:
        errors.append(
            f"coverage-gate BLOCK: _corpus.md has changed since the Phase-1 "
            f"freeze (stamped corpus_hash {stamped_hash[:16]}... != current "
            f"{current_hash[:16]}...) — the stale-corpus guard (design "
            f"§4.5.5). Re-freeze the corpus_hash deliberately if this "
            f"corpus growth/narrowing is intentional."
        )
        return {"ok": False, "errors": errors, "warnings": warnings}

    # ── (b) draft's own PRISMA count vs the true frozen corpus count ────────
    true_corpus_citekeys = _parse_corpus_citekeys(corpus_path)
    draft_text = _read_draft_text(tree_root)
    m = _PRISMA_CORPUS_COUNT_RE.search(draft_text)
    if m is not None:
        stated_count = int(m.group(1))
        true_count = len(true_corpus_citekeys)
        if stated_count < true_count:
            errors.append(
                f"coverage-gate BLOCK: the draft's PRISMA ledger states "
                f"{stated_count} corpus citekeys but the frozen corpus has "
                f"{true_count} — a revise appears to have narrowed scope to "
                f"shrink the denominator (design §10 gate-4)."
            )
            return {"ok": False, "errors": errors, "warnings": warnings}

    return {"ok": True, "errors": errors, "warnings": warnings}


# ---------------------------------------------------------------------------
# build_approve_payload — the single gate-assembly entry point
# ---------------------------------------------------------------------------

def build_approve_payload(
    tree_root: Path,
    project_notes_dir: Path,
    ms_type: Any,
    *,
    judge_fn: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Assemble the four manuscript-loop gates for ``approve-manuscript``.

    When to use: called by ``rv dag approve`` at the ``approve-manuscript``
    human-go node (wired in ``dag/verbs.py::cmd_approve``, mirroring
    ``check_framework_gate``'s wiring at ``approve-framework``). ★ Also the
    single-sourced import point for PR-M5's per-round re-fire — do NOT
    duplicate this gate-assembly logic in the review-revise board; call this.

    Args:
        tree_root: the manuscript folder (``manuscripts/<slug>/`` — the
            manifest's parent dir at tick time).
        project_notes_dir: the project's OKF notes root (``literature/``,
            ``concepts/``, etc. live directly under this).
        ms_type: the manuscript's registered ``ManuscriptType`` (for
            ``equation_sources`` — a type with none is a correct no-op for
            the equation gate).
        judge_fn: optional injectable LLM call, shared by BOTH LLM gates
            (``check_support_tally`` + ``check_cold_read_tally`` — same
            ``(prompt: str) -> str`` shape both already accept). Passing one
            counts as "judge configured" even absent env vars (test seam).

    Returns:
        ``{"ok": bool, "blocking": [...], "signals": [...], "not_run": [...]}``
        — ``ok`` is False iff ``blocking`` is non-empty. Every string in every
        list is prefixed with its originating gate in brackets, so a human
        (or PR-M5's meta-review) can triage by source at a glance.

    sr: manuscript-integration
    """
    blocking: list[str] = []
    signals: list[str] = []
    not_run: list[str] = []

    # ── 1. Hermetic .bib — deterministic, ALWAYS runs, hard BLOCK (PR-M2) ──
    bib_result = check_hermetic_bib(project_notes_dir, tree_root)
    if not bib_result["ok"]:
        blocking.extend(f"[hermetic-bib] {e}" for e in bib_result["errors"])

    # ── 2. Equation-fidelity — deterministic, ALWAYS runs, SIGNAL only,
    #      D-MS-2 (PR-M4). A type with no equation_sources is a correct
    #      no-op (nothing declared to mine, never an error). ────────────────
    equation_sources = getattr(ms_type, "equation_sources", ()) or ()
    if equation_sources:
        ledger = _equations.extract_equation_ledger(project_notes_dir, equation_sources)
        draft_text = _read_draft_text(tree_root)
        eq_findings = _equations.check_equation_fidelity(ledger, draft_text)
        signals.extend(f"[equation-fidelity:{f['severity']}] {f['message']}" for f in eq_findings)

    # ── 3+4. The LLM gates — BEHIND the judge guard (PR-M3). ────────────────
    if _judge_configured(judge_fn):
        support_result = _fidelity_gates.check_support_tally(
            tree_root, notes_root=project_notes_dir, judge_fn=judge_fn,
        )
        # ``errors`` already carries the canary-abort message when
        # canary_aborted is True (fidelity_gates.py's own abort path) — a
        # blind-judge canary failure means the tally could NOT be trusted,
        # so it BLOCKs regardless (fail-closed: cannot confirm citation
        # fidelity -> cannot proceed, never silently treated as a pass).
        blocking.extend(f"[support-matcher] {e}" for e in support_result["errors"])
        if not support_result.get("canary_aborted"):
            signals.extend(f"[support-matcher:PARTIAL] {w}" for w in support_result["warnings"])

        coldread_result = _fidelity_gates.check_cold_read_tally(tree_root, judge_fn=judge_fn)
        # SIGNAL-class gate per design (never BLOCK) — a canary abort here is
        # surfaced as a SIGNAL too (still loud, never swallowed), matching
        # the gate's own honesty class rather than escalating its severity.
        signals.extend(f"[cold-read] {e}" for e in coldread_result["errors"])
        if not coldread_result.get("canary_aborted"):
            signals.extend(f"[cold-read] {w}" for w in coldread_result["warnings"])
    else:
        not_run.append(
            "support-matcher + cold-read gates NOT RUN — RV_JUDGE_MODEL and/or "
            "ANTHROPIC_API_KEY are not configured (and no judge_fn was supplied). "
            "This is NOT a pass: the citation-fidelity FLOOR (support-matcher) and "
            "the self-containment critic (cold-read) have NOT been checked on this "
            "manuscript. Configure both env vars and re-run `rv dag approve` before "
            "trusting this manuscript's citation fidelity."
        )

    # ── 5. The coverage gate (design §10 gate-4) — deterministic, ALWAYS
    #      runs, hard BLOCK (PR-M5). No judge dependency at all — the
    #      integration PR deferred this into not_run; wired for real here. ──
    coverage_result = check_coverage_gate(project_notes_dir, tree_root)
    blocking.extend(f"[coverage-gate] {e}" for e in coverage_result["errors"])
    if coverage_result["warnings"]:
        not_run.extend(f"[coverage-gate] {w}" for w in coverage_result["warnings"])

    return {
        "ok": not blocking,
        "blocking": blocking,
        "signals": signals,
        "not_run": not_run,
    }
