# SPDX-License-Identifier: AGPL-3.0-or-later
"""review/counter_facet_guard.py D-6: the cold, rejects-only,
canary-verified guard on the Step-C counter-facet EXTRACTION output.

**Why this exists (not just D-7's existence check).** D-7
(``review.check_counter_facet_gate``) proves a counter-pole EXISTS — a
frozen ``counter:`` query list is present for every ``thesis:`` facet. It
does NOT prove the counter-facet is STRONG. A thesis-biased generator can
satisfy D-7 with a straw-man ("does X never happen?") instead of the real
refuting sub-literature (the researcher's Q2 in the search-breadth design; the design
doc's bake-off explicitly routes a straw-man-generator FAIL here). D-7
is the floor; this module is the ceiling.

**The trustworthy-verdict shape** (engineer memory: "Trustworthy LLM-verdict
shape" — recurred in this codebase: board reviewers, cold-judge, support-matcher):
  - **COLD** — the judge prompt carries ONLY the counter-facet's query
    terms, never the thesis facet, the research question, or which paper
    prompted the review. Thesis-anchoring is exactly how a strawman gets
    rubber-stamped ("of course it opposes X, look, it says 'not X'").
  - **REJECTS-ONLY** — a [STRONG] verdict is NOT itself a certification
    (many genuinely-strong facets will simply never be flagged); only a
    non-STRONG verdict is actionable (BLOCK). The guard's job is to catch
    the bad case, not to bless the good one.
  - **FAIL-CLOSED / UNIFIED HALT ** — no judge / no verdicts /
    incomplete fanout -> **HALT-DECLARE** (never a silent pass, and no
    longer the old SIGNAL). This supersedes the -HALT-vs- -SIGNAL
    split with the board + support-matcher's single rule: a relied-on cold
    gate that cannot run HALTs. An unparseable verdict -> treated as
    REJECTED (STRAWMAN, the fail-closed value).
  - **CANARY-VERIFIED** — before judging any real facet, the guard probes
    itself on TWO fixed, UNMARKED probes (one genuinely strong counter-facet,
    one straw-man) from the SAME general domain — substance-only
    distinguishable, never title-obvious. If either canary misclassifies,
    the guard ABORTS loudly (``CanaryAbortError``).

**The direct-API judge path is DELETED.** The production judge path is
the cold-agent-judge **emit/ingest fan-out** (``emit_counter_facet_tasks`` /
``ingest_counter_facet_verdicts``, built on ``gates.judge_seam`` — the SAME
shape as the support-matcher). rv reads NO judge-model / API-key env var to
run a judge, and constructs no in-process judge. The inline
``check_counter_facet_strength(judge_fn=...)`` path is exercised only with a
TEST-injected ``judge_fn``; a ``judge_fn=None`` call HALT-DECLAREs.

Only judges facets that ALREADY have a non-empty ``counter`` list (D-7
already BLOCKs the empty case — nothing to judge there).

Stdlib only. Hermetic in tests (``judge_fn`` injectable; the emit/ingest
functions never call an LLM at all).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from research_vault.gates import judge_seam
from research_vault.gates.judge_seam import CanaryAbortError  # re-export

_VERDICT_RE = re.compile(r"\[(STRONG|STRAWMAN)\]", re.IGNORECASE)

# Fixed verdict vocab + fail-closed default (rejects-only: cannot confirm a
# STRONG counter-pole -> treat as a STRAWMAN -> BLOCK). Never a certifying
# default.
_CF_VOCAB: frozenset[str] = frozenset({"STRONG", "STRAWMAN"})
_CF_FAIL_CLOSED_DEFAULT = "STRAWMAN"

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


def _build_counter_facet_judge_prompt(queries: list[str]) -> str:
    joined = "\n".join(f"- {q}" for q in queries)
    return _COUNTER_FACET_RUBRIC.format(QUERIES=joined)


def _extract_counter_facet_verdict(response: str) -> str | None:
    m = _VERDICT_RE.search(response or "")
    return m.group(1).upper() if m else None


def _counter_facet_canary_bank() -> list[tuple[list[str], str]]:
    """(counter_facet_queries, expected_verdict) — SUBSTANCE-ONLY
    distinguishable (the fit-check's ★ rule): both probes are drawn
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


# ---------------------------------------------------------------------------
#  the cold-agent-judge emit/ingest fan-out (the PRODUCTION path)
# ---------------------------------------------------------------------------

def _collect_counter_facets(protocol_text: str) -> list[tuple[str, list[str]]]:
    """Return every (angle, counter_queries) with a NON-empty counter list —
    the facets this guard actually judges (D-7 already BLOCKs the empty
    case). Shared by BOTH the emit path and the inline test path so they see
    the identical facet set (charter §6)."""
    from research_vault.sources.sweep import group_facet_stances, parse_angle_matrix

    angle_matrix = parse_angle_matrix(protocol_text)
    facets = group_facet_stances(angle_matrix)
    out: list[tuple[str, list[str]]] = []
    for angle in sorted(facets):
        counter_queries = facets[angle]["counter"]
        if counter_queries:
            out.append((angle, counter_queries))
    return out


def emit_counter_facet_tasks(
    protocol_text: str, *, scope: str = "",
) -> dict[str, Any]:
    """Emit ``_cf-tasks.json`` + ``_cf-canary-key.json`` for the counter-facet
    strength cold-agent-judge fan-out (same shape as the support-matcher).

    One real task per facet with a non-empty counter list, plus the 2
    interleaved unmarked canary probes. rv calls NO LLM here — the hub fans
    fresh cold subagent-judges out over the tasks file and writes
    ``_cf-verdicts.json`` alongside it.

    A protocol with zero judgeable facets is an honest no-op (empty tasks).
    """
    facets = _collect_counter_facets(protocol_text)
    real_tasks: list[dict[str, Any]] = [
        {
            "kind": "counter-facet",
            "angle": angle,
            "queries": list(queries),
            "prompt": _build_counter_facet_judge_prompt(queries),
        }
        for angle, queries in facets
    ]

    canary_items: list[tuple[dict[str, Any], str]] = [
        (
            {
                "kind": "counter-facet",
                "angle": "",
                "queries": list(q),
                "prompt": _build_counter_facet_judge_prompt(q),
            },
            expected,
        )
        for q, expected in _counter_facet_canary_bank()
    ]

    combined, canary_key = judge_seam.interleave_with_canaries(real_tasks, canary_items)
    tasks_doc = {
        "schema": judge_seam.TASKS_SCHEMA,
        "gate": "counter-facet",
        "scope": scope,
        "judge_kind": "cold",
        "created": judge_seam.now_iso(),
        "tasks": combined,
    }
    canary_key_doc = {"schema": judge_seam.CANARY_KEY_SCHEMA, "canaries": canary_key}
    return {"tasks_doc": tasks_doc, "canary_key_doc": canary_key_doc}


def ingest_counter_facet_verdicts(
    tasks_doc: dict[str, Any],
    canary_key_doc: dict[str, Any] | None,
    verdicts_doc: dict[str, Any] | None,
) -> dict[str, Any]:
    """Ingest ``_cf-verdicts.json`` — id-join, canary check (raises
    ``CanaryAbortError``), fail-closed fill. Returns:

      ``{ok, blocking, not_run, canary_aborted, halt, halt_reason,
      missing_ids, unrecognized_ids}``

    - Empty task set (no judgeable facet) -> honest no-op (ok=True).
    - Missing/empty verdicts while tasks exist -> HALT (fail-closed).
    - A canary miss -> ``CanaryAbortError`` (let it propagate / caller HALTs).
    - A non-STRONG (or missing/unrecognized -> STRAWMAN) verdict on a real
      facet -> blocking.
    """
    tasks = tasks_doc.get("tasks", []) or []
    canaries = (canary_key_doc or {}).get("canaries", {})
    real_tasks = [t for t in tasks if t.get("id") not in canaries]

    if not real_tasks:
        return {
            "ok": True, "blocking": [], "not_run": [],
            "canary_aborted": False, "halt": False, "halt_reason": "",
            "missing_ids": [], "unrecognized_ids": [],
        }

    if judge_seam.fanout_incomplete(tasks_doc, verdicts_doc):
        return {
            "ok": False,
            "blocking": [],
            "not_run": [
                "counter-facet strength guard (D-6) HALT-DECLARE: "
                "_cf-verdicts.json is missing or empty while real facet tasks "
                "were emitted — the counter-facet STRENGTH floor was never "
                "checked. This is NOT a pass; re-run the counter-facet "
                "judge-emit and let the hub fan out the cold judges."
            ],
            "canary_aborted": False,
            "halt": True,
            "halt_reason": (
                "verdicts file absent/empty for a non-empty counter-facet "
                "task set — fan-out did not complete."
            ),
            "missing_ids": [t["id"] for t in real_tasks],
            "unrecognized_ids": [],
        }

    verdict_by_id: dict[str, str] = {}
    for v in (verdicts_doc or {}).get("verdicts", []):
        vid = v.get("id")
        if vid:
            verdict_by_id[vid] = str(v.get("verdict", ""))

    # Canary check FIRST — an untrustworthy judge invalidates everything else.
    judge_seam.check_canaries(canaries, verdict_by_id)

    real_ids = [t["id"] for t in real_tasks]
    filled, missing_ids, unrecognized_ids = judge_seam.fail_closed_fill(
        real_ids, verdict_by_id, _CF_VOCAB, _CF_FAIL_CLOSED_DEFAULT,
    )
    angle_by_id = {t["id"]: t.get("angle", "") for t in real_tasks}

    blocking: list[str] = []
    for tid in real_ids:
        if filled[tid] != "STRONG":
            angle = angle_by_id.get(tid) or tid
            reason = (
                "no verdict returned (defaulted fail-closed)" if tid in missing_ids
                else "unrecognized verdict (defaulted fail-closed)" if tid in unrecognized_ids
                else f"judge verdict: {filled[tid]}"
            )
            blocking.append(
                f"counter-facet strength guard (D-6) REJECTED facet "
                f"'{angle}' — {reason}. This reads as a straw-man counter-pole "
                f"(existence != strength) — re-author its counter queries to "
                f"name the real refuting sub-literature, then re-run "
                f"`rv dag approve <run_id> approve-protocol`."
            )

    return {
        "ok": not blocking, "blocking": blocking, "not_run": [],
        "canary_aborted": False, "halt": False, "halt_reason": "",
        "missing_ids": missing_ids, "unrecognized_ids": unrecognized_ids,
    }


def emit_counter_facet_tasks_to_dir(
    judge_dir: Path, protocol_text: str, **kwargs: Any
) -> dict[str, Any]:
    """Convenience: emit + write both artifacts under ``judge_dir`` (typically
    ``reviews/<scope>/judge/counter-facet/``)."""
    result = emit_counter_facet_tasks(protocol_text, **kwargs)
    judge_seam.write_json(judge_dir / "_cf-tasks.json", result["tasks_doc"])
    judge_seam.write_json(judge_dir / "_cf-canary-key.json", result["canary_key_doc"])
    return result


def ingest_counter_facet_verdicts_from_dir(judge_dir: Path) -> dict[str, Any]:
    """Convenience: read all three artifacts from ``judge_dir`` and ingest. A
    missing ``_cf-tasks.json`` (nothing ever emitted) is an honest no-op."""
    tasks_doc = judge_seam.read_json_or_none(judge_dir / "_cf-tasks.json")
    if tasks_doc is None:
        tasks_doc = {"tasks": []}
    canary_key_doc = judge_seam.read_json_or_none(judge_dir / "_cf-canary-key.json")
    verdicts_doc = judge_seam.read_json_or_none(judge_dir / "_cf-verdicts.json")
    return ingest_counter_facet_verdicts(tasks_doc, canary_key_doc, verdicts_doc)


def cf_fanout_present(judge_dir: Path) -> bool:
    """True iff a counter-facet fan-out task set was emitted under
    ``judge_dir`` (``_cf-tasks.json`` exists) — mirrors
    ``check_gates._cold_fanout_dirs_present`` for the support-matcher."""
    return (judge_dir / "_cf-tasks.json").exists()


# ---------------------------------------------------------------------------
# check_counter_facet_strength — the structural guard + the inline TEST path
# ---------------------------------------------------------------------------

def _run_judge(queries: list[str], *, judge_fn: Callable[[str], str]) -> str | None:
    prompt = _build_counter_facet_judge_prompt(queries)
    try:
        response = judge_fn(prompt)
    except Exception:  # noqa: BLE001 — a judge-call failure is unparseable, not a crash
        response = ""
    return _extract_counter_facet_verdict(response)


def check_counter_facet_strength(
    protocol_text: str,
    *,
    judge_fn: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """D-6: cold, rejects-only, canary-verified guard on every facet's
    counter-side queries.

    Returns a dict:
      {"ok": bool, "blocking": [str, ...], "not_run": [str, ...],
       "canary_aborted": bool, "halt": bool, "halt_reason": str}

    - Structural (judge-INDEPENDENT): a malformed ``seed_queries:`` block that
      parses to ZERO usable queries is a hard BLOCK (an empty facet-iteration
      loop must never look identical to "nothing to judge").
    - UNIFIED HALT: ``judge_fn is None`` -> ``halt=True`` (production runs
      via the emit/ingest fan-out; the DAG approve-protocol gate ingests that,
      or HALT-DECLAREs when no fan-out was emitted). This is NOT the old
      SIGNAL — a relied-on cold gate that cannot run HALTs.
    - Canary fails on either probe -> ``canary_aborted=True``, aborts BEFORE
      judging any real facet.
    - A non-STRONG (or unparseable) verdict on a real facet -> blocking.

    The inline judge path here is exercised only with a TEST-injected
    ``judge_fn``.
    """
    from research_vault.sources.sweep import seed_queries_declared_but_unparsed

    # Structural, judge-independent BLOCK (fires before the judge branch).
    if seed_queries_declared_but_unparsed(protocol_text):
        return {
            "ok": False,
            "blocking": [
                "counter-facet strength guard (D-6) BLOCKED — `seed_queries:` "
                "is declared but parses to ZERO usable queries (malformed "
                "nesting/indentation, or an empty/garbage block). An empty "
                "facet-iteration loop must never look identical to 'no "
                "counter-facets to check'."
            ],
            "not_run": [],
            "canary_aborted": False,
            "halt": False,
            "halt_reason": "",
        }

    # unified HALT: no judge -> HALT-DECLARE (never the old SIGNAL).
    if judge_fn is None:
        return {
            "ok": False,
            "blocking": [],
            "not_run": [
                "counter-facet strength guard (D-6) HALT-DECLARE: no judge. "
                "The direct-API judge path was deleted — this cold "
                "guard runs via the emit/ingest fan-out. Emit the "
                "counter-facet task set and let the hub fan out the cold "
                "judges before approving the protocol. Counter-facets were "
                "checked only for EXISTENCE (D-7), never STRENGTH — a "
                "straw-man could still be sitting in this protocol."
            ],
            "canary_aborted": False,
            "halt": True,
            "halt_reason": "no counter-facet judge available (inline path, judge_fn=None)",
        }

    # Canary FIRST — a blind guard must never surface its own verdicts.
    for queries, expected in _counter_facet_canary_bank():
        verdict = _run_judge(queries, judge_fn=judge_fn)
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
                "halt": False,
                "halt_reason": "",
            }

    blocking: list[str] = []
    for angle, counter_queries in _collect_counter_facets(protocol_text):
        verdict = _run_judge(counter_queries, judge_fn=judge_fn)
        if verdict != "STRONG":
            blocking.append(
                f"counter-facet strength guard (D-6) REJECTED facet "
                f"'{angle}' — judge verdict: {verdict or 'UNPARSEABLE'}. "
                f"This reads as a straw-man counter-pole (existence != "
                f"strength) — re-author its counter queries to name the "
                f"real refuting sub-literature, then re-run "
                f"`rv dag approve <run_id> approve-protocol`."
            )

    return {
        "ok": not blocking, "blocking": blocking, "not_run": [],
        "canary_aborted": False, "halt": False, "halt_reason": "",
    }
