# SPDX-License-Identifier: AGPL-3.0-or-later
"""retrieval/router.py — the query-mode router: a zero-shot classifier that
sorts an incoming query into exactly one of three retrieval modes.

THE THREE MODES
================
  no-traversal — answerable from the Tier-0 map alone (meta/map-shape
                 queries: "what concepts does this corpus cover?", "which
                 MOC holds X?"). No walk over the notes.
  local        — anchor on one/few notes and walk the neighbourhood; the
                 common case for any substantive question about the
                 corpus's content.
  global       — corpus-wide sensemaking that no single neighbourhood can
                 answer ("what are the main themes / open gaps across the
                 corpus?").

**The boundary is deliberately SHY toward pulling content.** ``no-traversal``
is reserved for OBVIOUS meta/map-shape queries only; anything substantive
routes to ``local``/``global``. On genuine doubt between ``no-traversal``
and ``local``, the rubric instructs the judge to choose ``local`` — a thin
description-only answer to a substantive question is the failure this
boundary exists to avoid.

It is a ZERO-SHOT classifier — one LLM call per query, over the query text
plus a compact rendering of the project's Tier-0 knowledge map (built by
``retrieval.map.generate_map``; this module consumes that map, it does not
rebuild it). It is not a trained model and carries no learned weights.

HARNESS-NATIVE JUDGE CALL (no direct API path)
================================================
Per the project's cold-agent-judge doctrine, this classify is NOT an
in-process API call: rv EMITS a task manifest (``emit_router_tasks``), the
hub fans out fresh cold subagent-judges over it, and rv INGESTS the
returned verdicts (``ingest_router_verdicts``). This module reuses the
SAME low-level primitives every other cold-fanout gate in this codebase
shares — ``gates.judge_seam`` — for the schema names, the id-join, the
interleaved bidirectional canary check, and the fail-closed defaulting.

rv does not read an API key or a judge-model env var on this path; there
is no judge_fn default here to call directly.

FAIL-CLOSED CONTRACT (the router narrows; on doubt it must not narrow)
========================================================================
An unclassifiable, missing, or malformed verdict defaults to ``global`` —
the WIDEST, most complete, most expensive mode — never to a cheap mode
that could silently drop coverage. This mirrors the project's general
fail-closed posture (a missing/malformed judge verdict never resolves to
the option that does less work) but is inverted in direction from a
BLOCK-style gate: here "safe" means "spend more", not "reject".

Stdlib only (+ intra-package imports at call time, mirroring the rest of
this codebase's judge-seam consumers).
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Modes + fixed vocab
# ---------------------------------------------------------------------------

ROUTER_MODES: tuple[str, str, str] = ("no-traversal", "local", "global")

# Uppercase, fixed — the vocabulary a judge's verdict string must match
# (case/whitespace-tolerant compare via judge_seam.fail_closed_fill).
# Lowercasing a vocab member yields exactly the mode string it names
# (e.g. "NO-TRAVERSAL".lower() == "no-traversal") — one definition, no
# separate uppercase<->lowercase mapping to keep in sync.
_ROUTER_VERDICT_VOCAB: frozenset[str] = frozenset(
    {"NO-TRAVERSAL", "LOCAL", "GLOBAL"}
)

# Fail-closed default: the WIDEST mode, never a cheap one — an
# unclassifiable query must not be silently narrowed to less retrieval.
_ROUTER_FAIL_CLOSED_DEFAULT = "GLOBAL"


# ---------------------------------------------------------------------------
# Rubric — the seam default (ships ready, override via config)
# ---------------------------------------------------------------------------

DEFAULT_ROUTER_RUBRIC: str = """\
QUERY-MODE ROUTER RUBRIC

You classify ONE query into exactly one retrieval mode: NO-TRAVERSAL,
LOCAL, or GLOBAL. Reply with exactly one of those three tokens and
nothing else.

────────────────────────────────────────────────────────────────────────
INPUTS
────────────────────────────────────────────────────────────────────────
THE QUERY:
{QUERY}

THE PROJECT'S TIER-0 KNOWLEDGE MAP (concepts, MOCs, findings/gaps this
project's notes reference — descriptions only, not the full note bodies):
{MAP_VIEW}

────────────────────────────────────────────────────────────────────────
THE THREE MODES
────────────────────────────────────────────────────────────────────────
NO-TRAVERSAL — the query is answerable from the map above ALONE, with no
  need to open any note. Reserved for OBVIOUS meta/map-shape questions:
  "what concepts does this corpus cover?", "which MOC organizes X?",
  "how many findings are recorded?", "what edge types exist?". A query
  asking about the SUBSTANCE of a concept/finding/paper is NOT this mode,
  even if the map's description happens to mention it.

LOCAL — the query anchors on one or a few notes and needs the actual
  content of those notes (and their immediate neighbourhood) to answer.
  This is the common case: any substantive question about a specific
  concept, finding, method, result, or paper.

GLOBAL — the query requires corpus-wide sensemaking that no single
  neighbourhood of notes can answer on its own: "what are the main themes
  across this corpus?", "what open gaps exist overall?", "summarize
  everything we know about X across every finding".

────────────────────────────────────────────────────────────────────────
THE DECISION RULE — read before you answer
────────────────────────────────────────────────────────────────────────
The boundary is SHY toward pulling content. NO-TRAVERSAL is for obvious
map-shape queries only. On genuine doubt between NO-TRAVERSAL and LOCAL,
answer LOCAL — a thin, description-only answer to a substantive question
is a worse failure than one extra walk. Do not reach for NO-TRAVERSAL
just because the map's descriptions happen to contain words from the
query; the map is an index, not the content.

Reply with exactly one token: NO-TRAVERSAL, LOCAL, or GLOBAL.
"""


def get_router_rubric(
    override: str | None = None,
    config: Any | None = None,
) -> str:
    """Return the active router-judge rubric.

    Priority: override arg > ``[retrieval_router].rubric`` in config >
    ``DEFAULT_ROUTER_RUBRIC``. Mirrors the config-seam convention every
    other cold-judge rubric in this codebase uses (adopter-override
    without touching shipped code).
    """
    if override is not None:
        return override
    if config is not None:
        raw = getattr(config, "_raw", {})
        section = raw.get("retrieval_router", {})
        if isinstance(section, dict):
            rubric_cfg = section.get("rubric")
            if isinstance(rubric_cfg, str) and rubric_cfg.strip():
                return rubric_cfg
    return DEFAULT_ROUTER_RUBRIC


# ---------------------------------------------------------------------------
# Map-view rendering — compact, capped, never silently truncated
# ---------------------------------------------------------------------------

_PER_ITEM_CAP = 300
_OVERALL_BUDGET = 4000


def _cap_item(text: str, cap: int = _PER_ITEM_CAP) -> str:
    text = (text or "").strip()
    if len(text) <= cap:
        return text
    return text[:cap] + f" […truncated {len(text) - cap} chars…]"


def render_map_view_for_router(map_view: dict[str, Any]) -> str:
    """Render a compact text view of a ``retrieval.map.generate_map()``
    result for the router's prompt — slugs + titles + (capped)
    descriptions across the concept/MOC/findings-gaps indexes, never the
    full note bodies (this is a Tier-0 index, not a content dump).

    Overall length is capped at ``_OVERALL_BUDGET`` chars with a visible
    ``[…truncated…]`` marker if exceeded — never a silent, unmarked cutoff
    (a truncated-without-marker prompt is indistinguishable from a
    complete one to the judge; the marker at least tells it the view is
    partial).
    """
    lines: list[str] = []

    def _emit_section(title: str, items: list[dict[str, Any]]) -> None:
        lines.append(f"{title}:")
        if not items:
            lines.append("  (none)")
            return
        for item in items:
            slug = item.get("slug", "")
            item_title = item.get("title", "")
            desc = _cap_item(str(item.get("description", "")))
            lines.append(f"  - {slug}: {item_title} — {desc}")

    _emit_section("Concepts", list(map_view.get("concept_index", []) or []))
    _emit_section("MOCs", list(map_view.get("moc_index", []) or []))
    _emit_section("Findings & gaps", list(map_view.get("findings_gaps_index", []) or []))

    rendered = "\n".join(lines)
    total_chars = 0
    out_lines: list[str] = []
    for line in rendered.splitlines():
        if total_chars + len(line) + 1 > _OVERALL_BUDGET:
            remaining = _OVERALL_BUDGET - total_chars
            if remaining > 0:
                out_lines.append(line[:remaining])
            out_lines.append(
                f"[…truncated {len(rendered) - total_chars} chars of map view…]"
            )
            break
        out_lines.append(line)
        total_chars += len(line) + 1
    return "\n".join(out_lines)


# ---------------------------------------------------------------------------
# Canary bank — bidirectional across all three modes, no self-labeling tell
# ---------------------------------------------------------------------------

def _router_canary_bank() -> list[tuple[dict[str, str], str]]:
    """Interleaved query-router canary probes, one per mode — catches a
    judge that rubber-stamps a single mode (always answers the same
    token regardless of input) as well as one that is simply blind.

    Returns (task_fields_without_id, expected_verdict) pairs. ``kind``
    and the query/map_view shape are IDENTICAL to a real task — no field
    marks these as canaries, and neither the query text nor the fixed
    sample map view names a mode token (a self-labeling probe would let
    a judge ace it without actually classifying).
    """
    _sample_map_view = (
        "Concepts:\n"
        "  - retrieval-scope: Retrieval scope — how wide a query walks the graph\n"
        "  - grounding-floor: Grounding floor — every claim traces to a source\n"
        "MOCs:\n"
        "  - core-loop: Core loop — the map/route/walk pipeline\n"
        "Findings & gaps:\n"
        "  - open-gap-coverage: Coverage gap — some concepts lack an owning MOC"
    )
    return [
        (
            {
                "kind": "route",
                "query": "How many MOCs does this project have, and what are they called?",
                "map_view": _sample_map_view,
            },
            "NO-TRAVERSAL",
        ),
        (
            {
                "kind": "route",
                "query": "What did the grounding-floor concept's note say about how a claim traces to a source?",
                "map_view": _sample_map_view,
            },
            "LOCAL",
        ),
        (
            {
                "kind": "route",
                "query": "Across every finding and gap in this corpus, what are the recurring open problems?",
                "map_view": _sample_map_view,
            },
            "GLOBAL",
        ),
    ]


# ---------------------------------------------------------------------------
# emit_router_tasks — Phase A
# ---------------------------------------------------------------------------

def emit_router_tasks(
    queries: list[str],
    map_view: dict[str, Any],
    *,
    rubric_override: str | None = None,
    config: Any | None = None,
) -> dict[str, Any]:
    """Emit the ``_judge-tasks.json`` + ``_judge-canary-key.json`` pair
    (returned as dicts — writing to disk is the caller's choice, mirroring
    ``gates.judge_seam.write_json``) for the query-router cold-agent-judge
    fan-out.

    Args:
        queries:  the query strings to classify, in the caller's order.
        map_view: a ``retrieval.map.generate_map()`` result — consumed via
                  ``render_map_view_for_router``, not rebuilt here.
        rubric_override: optional rubric override, stamped into the tasks
                  doc's ``rubric`` field.
        config:   optional Config for the rubric config-seam.

    Returns:
        ``{"tasks_doc": {...}, "canary_key_doc": {...}}``.

    An empty ``queries`` list is a correct, honest no-op: both docs carry
    an empty ``tasks``/``canaries`` collection, never fabricated.
    """
    from research_vault.gates import judge_seam

    map_view_text = render_map_view_for_router(map_view)
    rubric = get_router_rubric(override=rubric_override, config=config)

    real_tasks = [
        {"kind": "route", "query": q, "map_view": map_view_text}
        for q in queries
    ]

    if not real_tasks:
        tasks_doc = {
            "schema": judge_seam.TASKS_SCHEMA,
            "gate": "query-router",
            "judge_kind": "cold",
            "created": judge_seam.now_iso(),
            "rubric": rubric,
            "tasks": [],
        }
        canary_key_doc = {"schema": judge_seam.CANARY_KEY_SCHEMA, "canaries": {}}
        return {"tasks_doc": tasks_doc, "canary_key_doc": canary_key_doc}

    combined, canary_key = judge_seam.interleave_with_canaries(
        real_tasks, _router_canary_bank(),
    )

    tasks_doc = {
        "schema": judge_seam.TASKS_SCHEMA,
        "gate": "query-router",
        "judge_kind": "cold",
        "created": judge_seam.now_iso(),
        "rubric": rubric,
        "tasks": combined,
    }
    canary_key_doc = {"schema": judge_seam.CANARY_KEY_SCHEMA, "canaries": canary_key}

    return {"tasks_doc": tasks_doc, "canary_key_doc": canary_key_doc}


# ---------------------------------------------------------------------------
# ingest_router_verdicts — Phase C
# ---------------------------------------------------------------------------

def ingest_router_verdicts(
    tasks_doc: dict[str, Any],
    canary_key_doc: dict[str, Any] | None,
    verdicts_doc: dict[str, Any] | None,
) -> dict[str, Any]:
    """Ingest ``_judge-verdicts.json`` for the query-router fan-out
    (Phase C) — the id-join, canary check, and fail-closed assembly.

    Guards (undiminished vs. any live-judge path):
      - id<->id join (never prompt-text matching).
      - Canary-verified FIRST: ``gates.judge_seam.check_canaries`` raises
        ``CanaryAbortError`` on any missing/mismatched canary — callers
        MUST let this propagate (or catch it and HALT-DECLARE; never
        swallow it and proceed to trust the real routes alongside it).
      - Fail-closed: a verdicts file entirely missing, or present but
        carrying ZERO verdicts while real tasks exist, is the
        "floor gate NOT RUN" case -> ``halt=True`` (never a route
        returned as if classification happened).
        A PARTIAL file (some ids present, some missing) is NOT a halt —
        each missing real-task id defaults to ``global`` (the fail-closed,
        widest mode) and is surfaced in ``missing_ids`` (resumable: the
        caller can re-fan just those ids).
      - Fixed vocab: an unrecognized verdict string also fail-closed
        defaults to ``global``, surfaced in ``unrecognized_ids`` — never
        silently coerced or ignored.

    Returns:
        ``{"routes": [{"id", "query", "mode"}, ...], "errors": [...],
        "warnings": [...], "canary_aborted": False, "halt": bool,
        "halt_reason": str, "missing_ids": [...], "unrecognized_ids": [...]}``.

    A zero-task ``tasks_doc`` (no queries to route) is an honest no-op —
    no halt, zero everything.
    """
    from research_vault.gates import judge_seam

    real_task_ids = [t["id"] for t in tasks_doc.get("tasks", [])]
    canaries = (canary_key_doc or {}).get("canaries", {})
    real_task_ids = [tid for tid in real_task_ids if tid not in canaries]
    task_by_id = {t["id"]: t for t in tasks_doc.get("tasks", [])}

    if not task_by_id:
        return {
            "routes": [], "errors": [], "warnings": [],
            "canary_aborted": False, "halt": False, "halt_reason": "",
            "missing_ids": [], "unrecognized_ids": [],
        }

    if judge_seam.fanout_incomplete(tasks_doc, verdicts_doc):
        return {
            "routes": [],
            "errors": [
                "query-router judge-fanout HALT: _judge-verdicts.json is "
                "missing or empty while real tasks were emitted — no query "
                "was classified (floor gate NOT RUN). This is NOT a pass."
            ],
            "warnings": [],
            "canary_aborted": False,
            "halt": True,
            "halt_reason": (
                "verdicts file absent/empty for a non-empty query-router "
                "task set — fan-out did not complete."
            ),
            "missing_ids": list(real_task_ids),
            "unrecognized_ids": [],
        }

    verdict_by_id: dict[str, str] = {}
    for v in (verdicts_doc or {}).get("verdicts", []):
        vid = v.get("id")
        if vid:
            verdict_by_id[vid] = str(v.get("verdict", ""))

    # Canary check FIRST — an untrustworthy judge invalidates every route
    # alongside it; let CanaryAbortError propagate to the caller.
    judge_seam.check_canaries(canaries, verdict_by_id)

    filled, missing_ids, unrecognized_ids = judge_seam.fail_closed_fill(
        real_task_ids, verdict_by_id, _ROUTER_VERDICT_VOCAB, _ROUTER_FAIL_CLOSED_DEFAULT,
    )

    routes: list[dict[str, Any]] = []
    errors: list[str] = []
    for tid in real_task_ids:
        task = task_by_id[tid]
        mode = filled[tid].lower()
        routes.append({"id": tid, "query": task["query"], "mode": mode})
        if tid in missing_ids:
            errors.append(
                f"query-router: id {tid} had no verdict returned by the "
                f"fan-out — defaulted fail-closed to 'global'."
            )
        elif tid in unrecognized_ids:
            errors.append(
                f"query-router: id {tid} verdict string was unrecognized — "
                f"defaulted fail-closed to 'global'."
            )

    return {
        "routes": routes,
        "errors": errors,
        "warnings": [],
        "canary_aborted": False,
        "halt": False,
        "halt_reason": "",
        "missing_ids": missing_ids,
        "unrecognized_ids": unrecognized_ids,
    }
