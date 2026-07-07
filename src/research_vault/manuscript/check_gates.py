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

The coverage gate (design §10 gate-4, re-run ``coverage_report()`` on the
revised corpus) is explicitly OUT OF SCOPE here — it is PR-M5's territory
(the review-revise board re-fires it every round). It is recorded in
``not_run`` as a deferred/documented gap, never silently omitted.

Design: docs/superpowers/specs/2026-07-07-survey-capability-design.md §10.
Doctrine: data/doctrine/honesty-gates.md.

Stdlib only. Hermetic in tests — the judge guard means a bare call with no
env vars and no judge_fn never reaches out to a live LLM.
sr: manuscript-integration (post PR-M2/M3/M4/M6)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from research_vault.manuscript.bib import check_hermetic_bib
from research_vault.manuscript import equations as _equations
from research_vault.manuscript import fidelity_gates as _fidelity_gates


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

    # ── The coverage gate (design §10 gate-4) — PR-M5's scope, deferred ─────
    not_run.append(
        "coverage-gate (design §10 gate-4 — re-run coverage_report() on the "
        "revised corpus, BLOCK on scope-narrowing) is NOT assembled here — it "
        "is PR-M5's territory (the review-revise board re-fires it every "
        "round). Deliberately deferred, not silently omitted."
    )

    return {
        "ok": not blocking,
        "blocking": blocking,
        "signals": signals,
        "not_run": not_run,
    }
