# SPDX-License-Identifier: AGPL-3.0-or-later
"""review/relate_judge_seam.py — PR-3b fix (Shape B): the harness
emit/ingest cold-judge fan-out for incremental-relate's paper<->paper edge
judgment.

**Diagnosis this closes.** PR-3b's shipped default (``remediation.
_default_relate_fn``) called the deleted direct-API judge helper in
``gates/_llm.py`` — a doctrine-violating direct-API judge path (PR-F
deleted that module entirely and added a grep-guard forbidding it). The
RELATE judgment (does
paper A relate to paper B; edge-type/strength) IS a judge, so per rv
doctrine it must route the CC harness cold emit/ingest fan-out — never a
synchronous in-process LLM call.

**Why the fan-out lives HERE, driven by the DAG layer, not inside
``incremental_relate.run_incremental_relate``'s own per-pair ``relate_fn``
callback (the rejected "Shape A").** The harness fan-out is asynchronous and
two-phase: emit a batched task file -> [the hub cold-fans-out fresh
subagent-judges, out of process] -> ingest the returned verdicts file on a
LATER invocation. A synchronous per-pair ``relate_fn(a, b) -> edge`` signature
cannot BE that two-phase protocol — it would force one wasteful emit/wait
round-trip PER PAIR instead of one batched emit covering every candidate
pair discovered this round. So the batch-level orchestration (compute every
candidate pair for a round, emit them ALL as one task set, later ingest ALL
their verdicts) happens at the DAG layer (``dag/verbs.py``'s ``approve-review``
branch), which then injects an already-resolved SYNCHRONOUS dict-lookup
closure down into ``run_incremental_relate``'s ``relate_fn``/
``escalate_relate_fn`` parameters — no API call, no async anywhere below
this module.

**Candidate generation is REUSED, not re-implemented** (charter §6): this
module calls ``incremental_relate.build_concept_index``/``note_concepts``
directly — the exact same concept-graph blocking rule
``run_incremental_relate`` itself uses internally, so the pairs this module
emits tasks for are IDENTICALLY the pairs ``run_incremental_relate`` would
have judged had a synchronous judge been available. No new candidate-
generation mechanism, no algorithm change.

**Built on ``gates.judge_seam``** — the SAME low-level primitives
(``interleave_with_canaries``, ``check_canaries``, ``fail_closed_fill``,
``fanout_incomplete``, ``read_json_or_none``, ``write_json``) that
``counter_facet_guard.py`` / ``support_matcher.py`` already use (PR-F).
No new injection convention, no new schema machinery — same shape, a new
verdict vocabulary (paper<->paper relation tags rather than STRONG/STRAWMAN).

**Fail-closed default is NONE (no edge), not a hard BLOCK** — unlike the
counter-facet guard (where a straw-man IS the actionable defect), a missing/
unparseable/canary-failed relate verdict simply means "don't write this
edge" (ABSENT-safe default, engineer memory: "ABSENT = safe default"). A
whole-batch canary failure still HALTs (untrustworthy judge — don't write
ANY edge from this batch), but an individual missing/garbled per-pair
verdict just skips that one edge, never blocks the review loop.

THE THREE ARTIFACTS (mirrors judge_seam's NG-4 contract, relate-scoped):
  _relate-tasks.json       (rv -> hub -> cold judges; carries the round's
                            ``new_citekeys``/``baseline_citekeys`` too, so a
                            LATER ingest can reconstruct which citekeys to
                            call ``run_incremental_relate`` on without any
                            separate state file)
  _relate-canary-key.json  (rv-PRIVATE, never emitted to hub/judge)
  _relate-verdicts.json    (cold judges/hub -> rv)

Stdlib only (+ intra-package imports).
sr: PR-3b fix (Shape B)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from research_vault.gates import judge_seam
from research_vault.gates.judge_seam import CanaryAbortError  # re-export

from .incremental_relate import _note_path, build_concept_index, note_concepts

_RELATE_VERDICT_RE = re.compile(
    r"\[(SUPPORTS|CONTRADICTS|PARTIAL|EXTENDS|NONE)\]", re.IGNORECASE
)

# Fixed verdict vocab + fail-closed default. NONE is a real, expected verdict
# (many pairs genuinely don't relate) as well as the fail-closed default for
# a missing/unparseable verdict — "cannot confirm a relation -> write no
# edge" (never fabricate an edge from an unreadable judge response).
_RELATE_VOCAB: frozenset[str] = frozenset(
    {"SUPPORTS", "CONTRADICTS", "PARTIAL", "EXTENDS", "NONE"}
)
_RELATE_FAIL_CLOSED_DEFAULT = "NONE"

_RELATE_NOTE_CHAR_CAP = 3000

_RELATE_JUDGE_RUBRIC = (
    "You are judging whether two research papers relate to each other, for a "
    "systematic-review corpus's paper->paper edge graph.\n\n"
    "Read both paper summaries below and classify their relation as EXACTLY "
    "one of:\n"
    "  [SUPPORTS] — paper B corroborates/reinforces paper A's claim (reciprocal).\n"
    "  [CONTRADICTS] — paper B's findings refute or conflict with paper A's "
    "claim (refutational).\n"
    "  [PARTIAL] — paper B bears on paper A's claim but only partially, or "
    "under different conditions/scope (line-of-argument).\n"
    "  [EXTENDS] — paper B builds on/generalizes paper A's claim without "
    "contradicting it (line-of-argument).\n"
    "  [NONE] — the two papers do not meaningfully relate; no edge should be "
    "written.\n\n"
    "=== PAPER A ({A_KEY}) ===\n{A_TEXT}\n=== END PAPER A ===\n\n"
    "=== PAPER B ({B_KEY}) ===\n{B_TEXT}\n=== END PAPER B ===\n\n"
    "Answer with exactly one bracketed verdict, followed by one sentence "
    "giving the reason (this sentence becomes the edge's stored reason)."
)


def _read_note_body(literature_dir: Path, citekey: str) -> str:
    """The de-frontmatter'd, capped body of a literature note — an empty
    string (never a crash) if the note is absent; only real ``literature/``
    citekeys (already-distilled, per ``run_incremental_relate``'s own
    caller contract) or the two fixed canary sentinels are ever passed
    here."""
    from ..note import _parse_frontmatter

    path = literature_dir / f"{citekey}.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    _fields, body = _parse_frontmatter(text)
    body = body.strip()
    if len(body) > _RELATE_NOTE_CHAR_CAP:
        body = body[:_RELATE_NOTE_CHAR_CAP] + f" […truncated {len(body) - _RELATE_NOTE_CHAR_CAP} chars…]"
    return body


def build_relate_task_prompt(a_key: str, a_text: str, b_key: str, b_text: str) -> str:
    return _RELATE_JUDGE_RUBRIC.format(A_KEY=a_key, A_TEXT=a_text, B_KEY=b_key, B_TEXT=b_text)


# ---------------------------------------------------------------------------
# Candidate-pair generation — REUSES incremental_relate's own concept index
# (no new blocking rule; see module docstring).
# ---------------------------------------------------------------------------

def build_relate_candidate_pairs(
    new_citekeys: list[str], *, literature_dir: Path, baseline_citekeys: set[str],
) -> tuple[list[tuple[str, str]], list[str]]:
    """Returns ``(pairs, islands)`` — ``pairs`` is every ``(new_ck, cand_ck)``
    concept-graph-blocked candidate (identical rule to
    ``run_incremental_relate``'s internal loop); ``islands`` is every
    ``new_ck`` with zero concept-graph candidates (the safety-valve case
    that gets escalated against the whole baseline instead)."""
    _citekey_concepts, concept_to_citekeys = build_concept_index(literature_dir, baseline_citekeys)
    pairs: list[tuple[str, str]] = []
    islands: list[str] = []
    for new_ck in new_citekeys:
        new_concepts = note_concepts(_note_path(literature_dir, new_ck))
        candidates: set[str] = set()
        for c in new_concepts:
            candidates |= concept_to_citekeys.get(c, set())
        candidates.discard(new_ck)
        if not candidates:
            islands.append(new_ck)
        else:
            for cand in sorted(candidates):
                pairs.append((new_ck, cand))
    return pairs, islands


# ---------------------------------------------------------------------------
# Canary bank — substance-only distinguishable (the standing ★ rule), a
# SUPPORTS-domain probe vs. a NONE-domain probe, neither labeled in its own
# text. Sentinel citekeys ("canary-a"/"canary-b" etc.) never collide with a
# real citekey (rv citekeys are always author+title+year derived).
# ---------------------------------------------------------------------------

def _relate_canary_bank() -> list[tuple[str, str, str, str, str]]:
    """``(a_key, a_text, b_key, b_text, expected_verdict)``."""
    return [
        (
            "canary-support-a",
            "This study reports that a 6-week cognitive-behavioral "
            "intervention reduced self-reported anxiety scores by 30% in a "
            "randomized sample of 200 adults, replicating an earlier trial's "
            "effect size and direction.",
            "canary-support-b",
            "An independent replication with a different clinical sample "
            "(n=150) finds the same 6-week cognitive-behavioral intervention "
            "reduces anxiety scores by a comparable margin, confirming the "
            "original trial's direction and magnitude.",
            "SUPPORTS",
        ),
        (
            "canary-none-a",
            "This paper surveys soil-erosion patterns in temperate "
            "grassland ecosystems following controlled grazing regimes.",
            "canary-none-b",
            "This paper proposes a new compiler optimization for reducing "
            "cache-miss latency in out-of-order superscalar processors.",
            "NONE",
        ),
    ]


def _extract_relate_verdict(response: str) -> tuple[str, str] | None:
    """``(tag, reason)`` or ``None`` if unparseable. An explicit ``[NONE]``
    still returns ``("NONE", reason)`` here (verdict extraction is neutral
    about whether a tag is "actionable" — ``ingest_relate_verdicts`` is the
    layer that decides NONE means "write no edge")."""
    m = _RELATE_VERDICT_RE.search(response or "")
    if m is None:
        return None
    tag = m.group(1).upper()
    reason = response[m.end():].strip().lstrip("—-: ").strip() or "no reason given"
    return tag, reason


# ---------------------------------------------------------------------------
# emit / ingest — the NG-4-shaped fan-out contract
# ---------------------------------------------------------------------------

def emit_relate_tasks(
    pairs: list[tuple[str, str]],
    islands: list[str],
    *,
    literature_dir: Path,
    baseline_citekeys: set[str],
    new_citekeys: list[str],
    scope: str = "",
) -> dict[str, Any]:
    """Emit the ``_relate-tasks.json`` + ``_relate-canary-key.json`` docs for
    ONE round's newly-added counter-papers. One ``relate-pair`` task per
    concept-graph-blocked candidate pair, plus one ``relate-escalate`` task
    per (island, every-baseline-citekey) pair (the island safety-valve's
    wider relate — scoped to only that island paper, mirrors
    ``run_incremental_relate``'s own escalation scoping). rv calls NO LLM
    here; the hub fans cold subagent-judges out over the emitted tasks.

    ``new_citekeys``/``baseline_citekeys`` are stamped onto the tasks doc
    itself — the round's own checkpoint, so a LATER ``ingest_relate_verdicts``
    call can reconstruct exactly which citekeys to run
    ``run_incremental_relate`` against without any separate persisted state.
    """
    real_tasks: list[dict[str, Any]] = []
    for a, b in pairs:
        a_text = _read_note_body(literature_dir, a)
        b_text = _read_note_body(literature_dir, b)
        real_tasks.append({
            "kind": "relate-pair", "a": a, "b": b,
            "prompt": build_relate_task_prompt(a, a_text, b, b_text),
        })
    for a in islands:
        for b in sorted(baseline_citekeys):
            a_text = _read_note_body(literature_dir, a)
            b_text = _read_note_body(literature_dir, b)
            real_tasks.append({
                "kind": "relate-escalate", "a": a, "b": b,
                "prompt": build_relate_task_prompt(a, a_text, b, b_text),
            })

    canary_items: list[tuple[dict[str, Any], str]] = [
        (
            {
                "kind": "relate-canary", "a": a_key, "b": b_key,
                "prompt": build_relate_task_prompt(a_key, a_text, b_key, b_text),
            },
            expected,
        )
        for a_key, a_text, b_key, b_text, expected in _relate_canary_bank()
    ]

    combined, canary_key = judge_seam.interleave_with_canaries(real_tasks, canary_items)
    tasks_doc = {
        "schema": judge_seam.TASKS_SCHEMA,
        "gate": "incremental-relate",
        "scope": scope,
        "judge_kind": "cold",
        "created": judge_seam.now_iso(),
        "new_citekeys": sorted(set(new_citekeys)),
        "baseline_citekeys": sorted(set(baseline_citekeys)),
        "tasks": combined,
    }
    canary_key_doc = {"schema": judge_seam.CANARY_KEY_SCHEMA, "canaries": canary_key}
    return {"tasks_doc": tasks_doc, "canary_key_doc": canary_key_doc}


def ingest_relate_verdicts(
    tasks_doc: dict[str, Any],
    canary_key_doc: dict[str, Any] | None,
    verdicts_doc: dict[str, Any] | None,
) -> dict[str, Any]:
    """Ingest ``_relate-verdicts.json`` — id-join, canary check (raises
    ``CanaryAbortError`` on a bad canary), fail-closed fill (missing/
    unparseable/unrecognized -> ``NONE``, i.e. no edge).

    Returns ``{ok, edges, escalated_edges, not_run, canary_aborted, halt,
    halt_reason, missing_ids, unrecognized_ids}``:
      - ``edges``: ``{(a, b): {"tag", "reason"}}`` for every ``relate-pair``
        task whose verdict is NOT ``NONE``.
      - ``escalated_edges``: same shape, for ``relate-escalate`` tasks.
      - Empty real-task set -> honest no-op (``ok=True``, both dicts empty).
      - Missing/empty verdicts while real tasks exist -> HALT (fail-closed,
        the fan-out never completed) — mirrors ``counter_facet_guard``'s
        unified-HALT posture (PR-F), NOT a silent "no edges" pass.
    """
    tasks = tasks_doc.get("tasks", []) or []
    canaries = (canary_key_doc or {}).get("canaries", {})
    real_tasks = [t for t in tasks if t.get("id") not in canaries]

    if not real_tasks:
        return {
            "ok": True, "edges": {}, "escalated_edges": {}, "not_run": [],
            "canary_aborted": False, "halt": False, "halt_reason": "",
            "missing_ids": [], "unrecognized_ids": [],
        }

    if judge_seam.fanout_incomplete(tasks_doc, verdicts_doc):
        return {
            "ok": False, "edges": {}, "escalated_edges": {},
            "not_run": [
                "incremental-relate judge fan-out HALT-DECLARE: "
                "_relate-verdicts.json is missing or empty while real relate "
                "tasks were emitted — the paper<->paper relation judgment "
                "was never checked for this round's newly-added counter-"
                "papers. Re-run the relate judge-emit and let the hub fan "
                "out the cold judges."
            ],
            "canary_aborted": False, "halt": True,
            "halt_reason": (
                "verdicts file absent/empty for a non-empty relate task set "
                "— fan-out did not complete."
            ),
            "missing_ids": [t["id"] for t in real_tasks], "unrecognized_ids": [],
        }

    verdict_by_id: dict[str, str] = {}
    reason_by_id: dict[str, str] = {}
    for v in (verdicts_doc or {}).get("verdicts", []):
        vid = v.get("id")
        if vid:
            verdict_by_id[vid] = str(v.get("verdict", ""))
            reason_by_id[vid] = str(v.get("reason", "")) or "no reason given"

    # Canary check FIRST — an untrustworthy judge invalidates the whole batch.
    judge_seam.check_canaries(canaries, verdict_by_id)

    real_ids = [t["id"] for t in real_tasks]
    filled, missing_ids, unrecognized_ids = judge_seam.fail_closed_fill(
        real_ids, verdict_by_id, _RELATE_VOCAB, _RELATE_FAIL_CLOSED_DEFAULT,
    )

    edges: dict[tuple[str, str], dict[str, str]] = {}
    escalated_edges: dict[tuple[str, str], dict[str, str]] = {}
    for t in real_tasks:
        tid = t["id"]
        tag = filled[tid]
        if tag == "NONE":
            continue
        reason = reason_by_id.get(tid, "no reason given")
        key = (t["a"], t["b"])
        entry = {"tag": tag, "reason": reason}
        if t.get("kind") == "relate-escalate":
            escalated_edges[key] = entry
        else:
            edges[key] = entry

    return {
        "ok": True, "edges": edges, "escalated_edges": escalated_edges,
        "not_run": [], "canary_aborted": False, "halt": False, "halt_reason": "",
        "missing_ids": missing_ids, "unrecognized_ids": unrecognized_ids,
    }


def emit_relate_tasks_to_dir(
    judge_dir: Path,
    pairs: list[tuple[str, str]],
    islands: list[str],
    *,
    literature_dir: Path,
    baseline_citekeys: set[str],
    new_citekeys: list[str],
    scope: str = "",
) -> dict[str, Any]:
    """Convenience: emit + write both artifacts under ``judge_dir``
    (typically ``reviews/<scope>/judge/relate/``)."""
    result = emit_relate_tasks(
        pairs, islands, literature_dir=literature_dir,
        baseline_citekeys=baseline_citekeys, new_citekeys=new_citekeys, scope=scope,
    )
    judge_seam.write_json(judge_dir / "_relate-tasks.json", result["tasks_doc"])
    judge_seam.write_json(judge_dir / "_relate-canary-key.json", result["canary_key_doc"])
    return result


def ingest_relate_verdicts_from_dir(judge_dir: Path) -> dict[str, Any]:
    """Convenience: read all three artifacts from ``judge_dir`` and ingest."""
    tasks_doc = judge_seam.read_json_or_none(judge_dir / "_relate-tasks.json")
    if tasks_doc is None:
        tasks_doc = {"tasks": []}
    canary_key_doc = judge_seam.read_json_or_none(judge_dir / "_relate-canary-key.json")
    verdicts_doc = judge_seam.read_json_or_none(judge_dir / "_relate-verdicts.json")
    return ingest_relate_verdicts(tasks_doc, canary_key_doc, verdicts_doc)


def relate_fanout_present(judge_dir: Path) -> bool:
    """True iff a relate fan-out task set was emitted under ``judge_dir``
    (``_relate-tasks.json`` exists) — mirrors
    ``counter_facet_guard.cf_fanout_present``."""
    return (judge_dir / "_relate-tasks.json").exists()


def read_relate_tasks_doc(judge_dir: Path) -> dict[str, Any] | None:
    """The raw ``_relate-tasks.json`` doc, or ``None`` if never emitted —
    exposes the round's stamped ``new_citekeys``/``baseline_citekeys`` to a
    caller that needs to reconstruct which citekeys to relate after ingest."""
    return judge_seam.read_json_or_none(judge_dir / "_relate-tasks.json")


def clear_relate_fanout(judge_dir: Path) -> None:
    """Remove the three relate fan-out artifacts after a successful ingest
    + write, so the round's fan-out is idempotently "consumed" and the NEXT
    round's emit starts fresh under the same ``judge_dir``."""
    for name in ("_relate-tasks.json", "_relate-canary-key.json", "_relate-verdicts.json"):
        p = judge_dir / name
        if p.exists():
            p.unlink()
