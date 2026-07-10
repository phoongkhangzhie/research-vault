# SPDX-License-Identifier: AGPL-3.0-or-later
"""review/counter_facet_guard.py — PR-2 D-6: the cold, rejects-only,
canary-verified guard on the Step-C counter-facet EXTRACTION output.

**Why this exists (not just D-7's existence check).** D-7
(``review.check_counter_facet_gate``) proves a counter-pole EXISTS — a
frozen ``counter:`` query list is present for every ``thesis:`` facet. It
does NOT prove the counter-facet is STRONG. A thesis-biased generator can
satisfy D-7 with a straw-man ("does X never happen?") instead of the real
refuting sub-literature (the researcher's Q2 in the search-breadth design; the design
doc's §7 bake-off explicitly routes a straw-man-generator FAIL here). D-7
is the floor; this module is the ceiling.

**The trustworthy-verdict shape** (engineer memory: "Trustworthy LLM-verdict
shape" — recurred 3x already in this codebase: board reviewers, cold-judge,
leak-planting):
  - **COLD** — the judge prompt carries ONLY the counter-facet's query
    terms, never the thesis facet, the research question, or which paper
    prompted the review. Thesis-anchoring is exactly how a strawman gets
    rubber-stamped ("of course it opposes X, look, it says 'not X'").
  - **REJECTS-ONLY** — a [STRONG] verdict is NOT itself a certification
    (many genuinely-strong facets will simply never be flagged); only a
    non-STRONG verdict is actionable (BLOCK). The guard's job is to catch
    the bad case, not to bless the good one.
  - **FAIL-CLOSED** — no judge configured -> ``not_run`` (never a silent
    pass masquerading as "checked"); an unparseable verdict -> treated as
    REJECTED (never silently waved through).
  - **CANARY-VERIFIED** — before judging any real facet, the guard probes
    itself on TWO fixed, UNMARKED probes (one genuinely strong counter-facet,
    one straw-man) from the SAME general domain — substance-only
    distinguishable, never title-obvious (the "screen on substance, not
    titles" trap: a title-obvious canary would let a title-only guard pass
    the canary while silently degrading to title-triage on real facets). If
    either canary misclassifies, the guard ABORTS loudly — it is blind, and
    every verdict below would be untrustworthy.

Only judges facets that ALREADY have a non-empty ``counter`` list (D-7
already BLOCKs the empty case — nothing to judge there).

Stdlib only except the live judge call (``gates._llm.call_anthropic_messages``,
lazily imported — never touched in a hermetic test with an injected
``judge_fn``).
sr: PR-2 (D-6)
"""
from __future__ import annotations

import os
import re
from typing import Any, Callable

_DEFAULT_JUDGE_MODEL: str = os.environ.get("RV_JUDGE_MODEL", "")

_VERDICT_RE = re.compile(r"\[(STRONG|STRAWMAN)\]", re.IGNORECASE)

_COUNTER_FACET_RUBRIC = (
    "You are a COLD, rejects-only referee auditing a systematic-review search "
    "protocol's declared COUNTER-POSITION facet — the query set meant to "
    "actively seek out the literature that would REFUTE the review's working "
    "thesis. You are given ONLY these query terms — not the thesis facet, "
    "not the research question, not which paper prompted this review — "
    "precisely so you cannot anchor on the thesis and rubber-stamp whatever "
    "was written.\n\n"
    "Classify the query set as:\n"
    "  [STRONG] — the queries would genuinely surface real refuting "
    "sub-literatures: they name specific mechanisms, phenomena, or "
    "methodological traditions that could actually disconfirm a thesis "
    "(e.g. a named effect, a specific persistence/stability mechanism, a "
    "distinct empirical tradition).\n"
    "  [STRAWMAN] — the queries are a token negation of a thesis phrasing "
    "('does X ever fail to happen', 'is X not always true') with no real "
    "refuting sub-literature named behind them, or are vague/generic to the "
    "point of surfacing nothing specific.\n\n"
    "Judge SUBSTANCE, not surface wording — a query can use dry, generic-"
    "sounding terms and still be [STRONG] if it names a real, specific "
    "phenomenon; a query can use dramatic-sounding terms and still be "
    "[STRAWMAN] if it names nothing beyond a bare negation.\n\n"
    "=== COUNTER-FACET QUERIES ===\n{QUERIES}\n=== END ===\n\n"
    "Answer with exactly one bracketed verdict — [STRONG] or [STRAWMAN] — "
    "followed by one sentence of reasoning."
)


def _judge_configured(judge_fn: Callable[[str], str] | None) -> bool:
    """Same predicate as ``manuscript/check_gates.py::_judge_configured`` —
    no shared gates-level home for this 4-line check yet (not worth a new
    module for it in this PR); duplicated deliberately rather than importing
    a private symbol across the review/manuscript package boundary."""
    if judge_fn is not None:
        return True
    return bool(os.environ.get("RV_JUDGE_MODEL", "").strip()) and bool(
        os.environ.get("ANTHROPIC_API_KEY", "").strip()
    )


def _default_judge_fn(prompt: str, model: str = _DEFAULT_JUDGE_MODEL) -> str:
    from research_vault.gates._llm import call_anthropic_messages

    return call_anthropic_messages(
        prompt, model, max_tokens=512, timeout=60, caller_label="counter-facet-guard",
    )


def _build_counter_facet_judge_prompt(queries: list[str]) -> str:
    joined = "\n".join(f"- {q}" for q in queries)
    return _COUNTER_FACET_RUBRIC.format(QUERIES=joined)


def _extract_counter_facet_verdict(response: str) -> str | None:
    m = _VERDICT_RE.search(response or "")
    return m.group(1).upper() if m else None


def _counter_facet_canary_bank() -> list[tuple[list[str], str]]:
    """(counter_facet_queries, expected_verdict) — SUBSTANCE-ONLY
    distinguishable (the PR-1 fit-check's ★ rule): both probes are drawn
    from the SAME general domain (misinformation-correction research), so a
    title/topic-only judge cannot tell them apart by subject matter — only
    by whether the queries actually name a specific refuting mechanism
    (STRONG) or are a bare negation with nothing behind it (STRAWMAN).
    Neither probe is labeled 'strong'/'strawman' in its own text.
    """
    return [
        (
            [
                "backfire effect correction failure replication meta-analysis",
                "boomerang effect fact-check ineffective evidence",
                "motivated reasoning resistance to correction persistent false belief",
            ],
            "STRONG",
        ),
        (
            [
                "does fact-checking not always work",
                "is correction sometimes unsuccessful",
            ],
            "STRAWMAN",
        ),
    ]


def _run_judge(
    queries: list[str],
    *,
    judge_fn: Callable[[str], str] | None,
    judge_model: str,
) -> str | None:
    prompt = _build_counter_facet_judge_prompt(queries)
    caller = judge_fn if judge_fn is not None else (lambda p: _default_judge_fn(p, judge_model))
    try:
        response = caller(prompt)
    except Exception:  # noqa: BLE001 — a judge-call failure is treated as unparseable, not a crash
        response = ""
    return _extract_counter_facet_verdict(response)


def check_counter_facet_strength(
    protocol_text: str,
    *,
    judge_fn: Callable[[str], str] | None = None,
    judge_model: str = _DEFAULT_JUDGE_MODEL,
) -> dict[str, Any]:
    """D-6: cold, rejects-only, canary-verified guard on every facet's
    counter-side queries.

    Returns a dict:
      {"ok": bool, "blocking": [str, ...], "not_run": [str, ...], "canary_aborted": bool}

    - No judge configured -> ``ok=True``, ``not_run`` carries a LOUD message
      (charter §2: surface, never silently drop — a caller must not mistake
      "not run" for "passed").
    - Canary fails on either probe -> ``ok=False``, ``canary_aborted=True``,
      the guard aborts BEFORE judging any real facet (a blind guard must
      never surface its own verdicts as trustworthy).
    - A facet with an empty ``counter`` list is skipped — D-7 already BLOCKs
      that case; nothing to judge here.
    - A non-STRONG (or unparseable) verdict on a real facet -> blocking.
    """
    from research_vault.sources.sweep import group_facet_stances, parse_angle_matrix

    if not _judge_configured(judge_fn):
        return {
            "ok": True,
            "blocking": [],
            "not_run": [
                "counter-facet strength guard (D-6): no judge configured "
                "(RV_JUDGE_MODEL/ANTHROPIC_API_KEY unset and no judge_fn) — "
                "the cold guard did NOT run. Counter-facets were only "
                "checked for EXISTENCE (D-7), never STRENGTH — a straw-man "
                "counter-facet could still be sitting in this protocol."
            ],
            "canary_aborted": False,
        }

    for queries, expected in _counter_facet_canary_bank():
        verdict = _run_judge(queries, judge_fn=judge_fn, judge_model=judge_model)
        if verdict != expected:
            return {
                "ok": False,
                "blocking": [
                    f"counter-facet strength guard (D-6) CANARY ABORTED — "
                    f"expected [{expected}] on a known probe, judge returned "
                    f"{verdict!r}. The guard is blind; do not trust any "
                    f"[STRONG] verdict until this is fixed."
                ],
                "not_run": [],
                "canary_aborted": True,
            }

    angle_matrix = parse_angle_matrix(protocol_text)
    facets = group_facet_stances(angle_matrix)

    blocking: list[str] = []
    for angle in sorted(facets):
        counter_queries = facets[angle]["counter"]
        if not counter_queries:
            continue  # D-7 already BLOCKs the empty-counter-pole case
        verdict = _run_judge(counter_queries, judge_fn=judge_fn, judge_model=judge_model)
        if verdict != "STRONG":
            blocking.append(
                f"counter-facet strength guard (D-6) REJECTED facet "
                f"'{angle}' — judge verdict: {verdict or 'UNPARSEABLE'}. "
                f"This reads as a straw-man counter-pole (existence != "
                f"strength) — re-author its counter queries to name the "
                f"real refuting sub-literature, then re-run "
                f"`rv dag approve <run_id> approve-protocol`."
            )

    return {"ok": not blocking, "blocking": blocking, "not_run": [], "canary_aborted": False}
