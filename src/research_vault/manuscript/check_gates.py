# SPDX-License-Identifier: AGPL-3.0-or-later
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

  - ``check_citation_resolve`` (``manuscript/bib.py``, PR-M2)     -> hard BLOCK,
    deterministic, ALWAYS runs (no judge dependency at all).
  - ``check_equation_fidelity`` (``manuscript/equations.py``, PR-M4) -> SIGNAL
    ONLY (D-MS-2 — never BLOCK, even marked-critical). Deterministic; ALWAYS
    runs (no judge dependency — the LLM-judge fallback inside the gate itself
    is a separate, optional refinement not wired here).
  - ``check_support_tally`` (``manuscript/fidelity_gates.py``, PR-M3) -> BLOCK
    on ``[ABSENT]``/``[CONTRADICTS]`` (the citation-fidelity FLOOR) — BEHIND
    the judge guard. Support-matcher is the ONE judge-gated LLM check now
    (the former ``check_cold_read_tally`` self-containment critic was
    removed — SIGNAL-only, non-actionable under hands-off autonomy,
    redundant with the review board's coherence axis + RD-6's hard
    term-definition gate. The operator's call; see DEVLOG).

**The judge guard** (design doctrine: ``honesty-gates.md`` fail-closed
discipline, applied honestly in the OTHER direction here): the LLM gate
runs ONLY when a judge is configured — ``RV_JUDGE_MODEL`` + ``ANTHROPIC_API_KEY``
both set in the environment, OR an explicit ``judge_fn`` is passed in (tests
inject a mock this way; that counts as "configured"). When neither is present,
the LLM gate is NOT silently skipped — it lands in the payload's
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

from research_vault.manuscript.bib import check_citation_resolve
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
# just one section — the same report.md+sections resolution pattern used
# elsewhere in this loop, PR-M3).
# ---------------------------------------------------------------------------

def _read_draft_text(tree_root: Path) -> str:
    """Join every draft file (``report.md`` + ``sections/*.md`` — see
    ``draft_files.py``) into one draft-text blob.

    Best-effort, never raises: an unreadable/missing file simply contributes
    nothing (a fresh manuscript folder with no draft yet -> empty string,
    which every gate treats as "nothing to check yet", never an error).
    """
    from research_vault.manuscript.draft_files import resolve_draft_files

    parts: list[str] = []
    for draft_file in resolve_draft_files(tree_root):
        try:
            parts.append(draft_file.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# check_reader_hygiene — RD-5, next-gen lit-review design §6 (deterministic,
# ALWAYS runs, hard BLOCK, no judge dependency — the presentation program's
# most transferable HR mechanic, rv's biggest packaging gap before this PR).
# ---------------------------------------------------------------------------

# Internal pipeline-vocabulary handles that must never leak into reader prose.
# \bCP\d+\b / \bQ\d+\b require a digit immediately after the letter(s) so
# ordinary prose ("Q&A", "CParser", "Quarter") never false-positives — only
# the exact counter-position/question handle shape trips this.
_LEAK_CP_HANDLE_RE = re.compile(r"\bCP\d+\b")
_LEAK_Q_HANDLE_RE = re.compile(r"\bQ\d+\b")
_LEAK_SHA256_RE = re.compile(r"\bsha256:[0-9a-fA-F]+\b")
# Loop/control-artifact filenames (the review/manuscript control notes) —
# never a reader-facing citation; a real citekey never starts with '_'.
_LEAK_ARTIFACT_FILENAME_RE = re.compile(r"\b_[a-z][a-z-]*\.md\b")
# Tool/verb/node-vocabulary tokens leaking loop internals into reader prose.
_LEAK_TOOL_TOKENS: tuple[str, ...] = (
    "rv research",
    "rv review",
    "rv manuscript",
    "rv dag",
    "review-snowball",
    "review-search",
    "review-synthesize",
    "review-coverage-critic",
    "coverage-gate",
    "coverage-critic",
    "approve-protocol",
    "approve-framework",
    "approve-manuscript",
)


def check_reader_hygiene(reader_body: str) -> dict[str, Any]:
    """The reader-hygiene leak-gate (RD-5) — BLOCK on pipeline vocabulary
    leaking into reader-facing prose.

    When to use: run over the ASSEMBLED reader body (the joined, rendered
    survey text a reader will actually see — never the internal control
    artifacts like ``_framework-candidates.md``/``_saturation.md``, which are
    ALLOWED to carry these handles). Fail-closed, rv-style: any hit BLOCKs
    declare-final; a clean body passes with zero errors.

    Deterministic and independent of every other gate — no judge, no network,
    no dependency on markdown vs. tex render target. Every hit is surfaced
    (never truncated to the first match, charter §2 — a `.strip()`/`[:1]`
    shortcut here would silently hide every leak after the first).

    Args:
        reader_body: the assembled reader-facing text to scan.

    Returns:
        {"ok": bool, "errors": list[str]} — ok is False iff errors is non-empty.

    sr: NG-lit-review-waveB (RD-5)
    """
    errors: list[str] = []

    for m in _LEAK_CP_HANDLE_RE.finditer(reader_body):
        errors.append(
            f"reader-hygiene BLOCK: counter-position handle {m.group(0)!r} leaked "
            f"into reader prose — name the counter-position inline (RD-6), never "
            f"by its internal handle."
        )
    for m in _LEAK_Q_HANDLE_RE.finditer(reader_body):
        errors.append(
            f"reader-hygiene BLOCK: internal question handle {m.group(0)!r} leaked "
            f"into reader prose — this is a loop-control artifact, never reader-facing."
        )
    for m in _LEAK_SHA256_RE.finditer(reader_body):
        errors.append(
            f"reader-hygiene BLOCK: a corpus hash {m.group(0)!r} leaked into reader "
            f"prose — route hashes to the control note / DEVLOG (RD-3), never the "
            f"manuscript body."
        )
    for m in _LEAK_ARTIFACT_FILENAME_RE.finditer(reader_body):
        errors.append(
            f"reader-hygiene BLOCK: internal artifact filename {m.group(0)!r} leaked "
            f"into reader prose — this is a loop-control artifact name, not a citation."
        )
    for token in _LEAK_TOOL_TOKENS:
        if token in reader_body:
            errors.append(
                f"reader-hygiene BLOCK: tool/loop vocabulary {token!r} leaked into "
                f"reader prose — the reader never needs to know which rv verb "
                f"produced this survey."
            )

    return {"ok": not errors, "errors": errors}


# ---------------------------------------------------------------------------
# check_heading_order — HR-craft rec 5 (design §7), NG-7's structural-mirror
# H2-order diff (deterministic, ALWAYS runs, SIGNAL only — no judge dependency)
# ---------------------------------------------------------------------------

def check_heading_order(draft_text: str, expected_order: "list[str] | tuple[str, ...]") -> dict[str, Any]:
    """HR-craft rec 5 (design §7): a deterministic H2-heading-order diff.

    HR's instruction-critic diffs the draft's ordered H2 list element-wise
    against a frozen heading contract; NG-7's single-pass outline already
    freezes a reading-order spine (``lit_review.READING_ORDER``, RD-2) — this
    is the cheap, mechanical cross-check confirming the draft actually
    delivered the frozen frame.

    SIGNAL only, never BLOCK (design table): a structural drift is
    informative — the writer may have deliberately merged/split sections —
    never a hard stop on its own.

    Headings not among ``expected_order`` (e.g. a sub-heading, a figure
    caption) are ignored — this only orders the INTERSECTION of found H2s
    against the frozen contract, never penalizes extra structure.

    Args:
        draft_text: the assembled reader body (or the whole draft blob).
        expected_order: the frozen heading contract, e.g.
            ``manuscript.types.lit_review.READING_ORDER``.

    Returns:
        {"ok": bool, "warnings": list[str]} — ok is True when the found H2
        order (filtered to the expected set) matches the expected order, or
        when fewer than 2 matching headings are found (nothing to compare).

    sr: NG-lit-review-waveB (NG-7, HR-craft rec 5)
    """
    import re

    found = re.findall(r"^\s*#{1,2}\s+(.+?)\s*$", draft_text, re.MULTILINE)
    expected_norm = [str(e).strip().lower() for e in expected_order]

    def _norm(h: str) -> str:
        return h.strip().lower().lstrip("#").strip()

    found_norm = [_norm(h) for h in found]
    filtered_found = [h for h in found_norm if any(e in h or h in e for e in expected_norm)]

    if len(filtered_found) < 2:
        return {"ok": True, "warnings": []}

    # Build the expected sub-order restricted to headings actually found.
    def _matches(found_h: str, exp: str) -> bool:
        return exp in found_h or found_h in exp

    expected_restricted = [e for e in expected_norm if any(_matches(h, e) for h in filtered_found)]

    if filtered_found == expected_restricted:
        return {"ok": True, "warnings": []}

    return {
        "ok": False,
        "warnings": [
            f"heading-order diff SIGNAL: the draft's H2 order {filtered_found!r} "
            f"does not match the frozen reading-order contract "
            f"{expected_restricted!r} — check whether this is a deliberate "
            f"merge/split or an assembly drift."
        ],
    }


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
# _cold_fanout_dirs_present — NG-4 detector (design §1.9)
# ---------------------------------------------------------------------------

def _cold_fanout_dirs_present(tree_root: Path) -> bool:
    """True iff a cold-agent-judge fan-out task set was ever emitted for
    this manuscript (``rv manuscript <project> judge-emit <slug>`` or
    equivalent) — i.e. ``judge/support-matcher/_judge-tasks.json`` exists
    under ``tree_root``. Support-matcher-ONLY (the cold-read gate was
    removed; see DEVLOG).

    Deliberately checks presence of the TASKS file, not the verdicts file
    — the whole point of this detector is to distinguish "a fan-out was
    attempted (verdicts may or may not have landed yet)" from "nothing was
    ever configured on the judge path" (the not_run bucket below).
    """
    judge_dir = tree_root / "judge"
    return (judge_dir / "support-matcher" / "_judge-tasks.json").exists()


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
    """Assemble the manuscript-loop gates for ``approve-manuscript``.

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
        judge_fn: optional injectable LLM call for ``check_support_tally``
            (the ``(prompt: str) -> str`` shape it already accepts). Passing
            one counts as "judge configured" even absent env vars (test seam).

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
    # NG-4b item 3: a support-matcher canary-abort (blind-judge probe fails)
    # must be visible to review.autonomy's gate-policy engine as a TOP-LEVEL
    # flag, not buried inside a `blocking` string. classify_disposition's
    # priority order checks `canary_aborted` BEFORE `blocking` (untrustworthy
    # signal > deterministic block) — without this flag, a canary-abort was
    # indistinguishable from an ordinary fixable BLOCK, so the gate-policy
    # engine would REVISE it (dispatch a bounded auto-revise against the SAME
    # broken judge) instead of HALT-DECLARE-ing (fail-closed, never retry an
    # untrustworthy judge — charter §10). See evaluation_from_structural_payload.
    canary_aborted = False

    # ── 1. Hermetic references.md — deterministic, ALWAYS runs, hard BLOCK
    #      (PR-M2) ──
    bib_result = check_citation_resolve(project_notes_dir, tree_root)
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

    # ── 3. The LLM gate — BEHIND the judge guard (PR-M3). ────────────────────
    #      Support-matcher is the ONE judge-gated gate now (the former
    #      cold-read self-containment critic was removed; see DEVLOG).
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
        if support_result.get("canary_aborted"):
            canary_aborted = True
        else:
            signals.extend(f"[support-matcher:PARTIAL] {w}" for w in support_result["warnings"])
    elif _cold_fanout_dirs_present(tree_root):
        # NG-4 (design §1.9, PRIMARY path): no live judge_fn/env, but a
        # hub-orchestrated cold-agent-judge fan-out was emitted for this
        # manuscript (``judge/support-matcher/_judge-tasks.json`` present)
        # — ingest whatever verdicts landed instead of falling into the
        # generic "not configured" not_run bucket below. A CanaryAbortError
        # here (the fan-out judge failed its planted probe) or a halt (the
        # fan-out never completed) is escalated to a hard BLOCK, not a
        # soft not_run — unlike "nothing was ever attempted," a task set
        # was emitted and something SHOULD have come back; treat that
        # gap the same way the live path treats a canary abort: cannot
        # self-certify -> cannot proceed (design §1.2's HALT-DECLARE
        # policy for both "untrustworthy signal" and "floor gate NOT RUN").
        from research_vault.gates.judge_seam import CanaryAbortError

        try:
            support_result = _fidelity_gates.ingest_support_verdicts_from_dir(
                tree_root / "judge" / "support-matcher", tree_root=tree_root,
            )
        except CanaryAbortError as e:
            support_result = {
                "errors": [f"CANARY ABORT (HALT-DECLARE): {e}"],
                "warnings": [], "canary_aborted": True, "halt": True,
            }
        if support_result.get("canary_aborted"):
            canary_aborted = True
            blocking.extend(f"[support-matcher] {e}" for e in support_result["errors"])
        elif support_result.get("halt"):
            # NG-4b: an incomplete/missing judge-fanout is the §1.2 "floor
            # gate NOT RUN" failure class, NOT a fixable BLOCK — it belongs
            # in `not_run` (-> HALT-DECLARE, priority 2) so the gate-policy
            # engine never dispatches a bounded auto-revise against a floor
            # that never actually ran (explore-rl #3: a floor gate that
            # didn't run must never look like an ordinary fixable finding).
            not_run.extend(f"[support-matcher] {e}" for e in support_result["errors"])
            not_run.append(
                "[support-matcher] HALT-DECLARE: judge-fanout did not "
                "complete — see the error above; this manuscript cannot "
                "self-certify its citation-fidelity floor."
            )
        else:
            blocking.extend(f"[support-matcher] {e}" for e in support_result["errors"])
            signals.extend(f"[support-matcher:PARTIAL] {w}" for w in support_result.get("warnings", []))
    else:
        not_run.append(
            "support-matcher gate NOT RUN — RV_JUDGE_MODEL and/or "
            "ANTHROPIC_API_KEY are not configured (and no judge_fn was supplied), "
            "and no cold-agent-judge fan-out was emitted (no `judge/` directory "
            "under this manuscript). This is NOT a pass: the citation-fidelity "
            "FLOOR (support-matcher) has NOT been checked on this manuscript. "
            "Configure both env vars, or emit a judge-fanout task set "
            "(design §1.9), and re-run `rv dag approve` before trusting this "
            "manuscript's citation fidelity."
        )

    # ── 5. The coverage gate (design §10 gate-4) — deterministic, ALWAYS
    #      runs, hard BLOCK (PR-M5). No judge dependency at all — the
    #      integration PR deferred this into not_run; wired for real here. ──
    coverage_result = check_coverage_gate(project_notes_dir, tree_root)
    blocking.extend(f"[coverage-gate] {e}" for e in coverage_result["errors"])
    if coverage_result["warnings"]:
        not_run.extend(f"[coverage-gate] {w}" for w in coverage_result["warnings"])

    # ── 6. Reader-hygiene leak-gate (RD-5) — deterministic, ALWAYS runs,
    #      hard BLOCK. No judge dependency; independent of every other gate.
    hygiene_draft_text = _read_draft_text(tree_root)
    hygiene_result = check_reader_hygiene(hygiene_draft_text)
    blocking.extend(f"[reader-hygiene] {e}" for e in hygiene_result["errors"])

    # ── 7. Heading-order diff (HR-craft rec 5, NG-7) — deterministic, ALWAYS
    #      runs (when the type declares a frozen reading order), SIGNAL only.
    #      Only lit-review declares READING_ORDER today; a type with none is
    #      a correct no-op (never fabricated for a type that hasn't defined one).
    if getattr(ms_type, "key", "") == "lit-review":
        from research_vault.manuscript.types.lit_review import READING_ORDER

        heading_result = check_heading_order(hygiene_draft_text, READING_ORDER)
        signals.extend(f"[heading-order] {w}" for w in heading_result["warnings"])

    return {
        "ok": not blocking,
        "blocking": blocking,
        "signals": signals,
        "not_run": not_run,
        # NG-4b: top-level canary-abort flag — see comment at the top of
        # this function. Consumed by review.autonomy.evaluation_from_structural_payload.
        "canary_aborted": canary_aborted,
    }
