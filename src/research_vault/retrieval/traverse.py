# SPDX-License-Identifier: AGPL-3.0-or-later
"""retrieval/traverse.py — the traversal policy over a project's knowledge
graph: a broad multi-anchor select followed by a shallow, intent-routed
beam over the typed-edge graph.

**Governing principle.** Because a project's Tier-0 knowledge map
(``retrieval/map.py``) fits comfortably in one context window, broad
up-front selection dominates: an LLM reads the whole map ONCE and selects
EVERY plausibly-relevant anchor in one pass, not a single best guess.
Iterative hopping over typed edges is coverage TOP-UP on top of that broad
select, not the primary retrieval mechanism — so the beam this module runs
is deliberately shallow (a handful of hops, a handful of nodes wide), never
a big-graph beam-search-first design.

**Two phases, two LLM calls, both harness-native.**
  1. Broad multi-anchor select — one judge-fanout task per query, reading
     the ENTIRE Tier-0 candidate set (concepts + MOCs + findings/gaps) and
     returning every plausibly-relevant slug.
  2. Intent-routed, per-hop beam — from the anchors, walk typed edges
     filtered to the tag family the query implies (see
     ``INTENT_EDGE_ROUTES``), pruning each hop's candidate neighbours from
     their DESCRIPTIONS ONLY (never their body) before deciding to visit
     them. One judge-fanout task PER BEAM LAYER (batched across every
     frontier node in that hop), not one per candidate — a naive per-node
     round-trip would make even a 3-hop beam serially slow.

Both LLM decision points go through the SAME cold-agent-judge fan-out
contract every other gate in this codebase uses
(``gates.judge_seam``): rv emits a task manifest, a fresh cold subagent
judges it, rv ingests the verdicts. There is no direct API call anywhere
in this module and no reading of a judge API key — the fan-out is
harness-side by design (memoryless judges, no draft-thesis anchoring, no
extra credential surface).

**Fail-closed, but the closed direction differs by call.** A missing/
unrecognized anchor-select verdict yields an honest empty anchor set
(never fabricated anchors). A missing/unrecognized per-hop prune verdict
defaults to KEEP, not DROP — dropping a frontier node loses recall
permanently (no later hop recovers it), so the safe direction here is the
opposite of a typical reject-only gate.

**Cross-layer reach.** The two knowledge sub-layers (project-scoped notes
and the shared-canonical concepts/literature stores) connect through this
beam: a paper is never enumerated in Tier-0, but is reached by hopping
from the concept it grounds, via the SAME typed-edge readers
``review/relate_check.py`` already owns as the vocabulary SSOT — this
module imports that vocabulary, it never re-hardcodes it.

Stdlib only (+ intra-package imports).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Config
from ..note import _parse_frontmatter
from ..gates import judge_seam
from ..review.relate_check import (
    _TAG_FAMILY,
    _TARGET_RE,
    parse_concept_edges,
    parse_paper_relations,
    parse_typed_edges,
)

# ---------------------------------------------------------------------------
# Beam discipline — named, tunable constants (never hardcoded inline below).
# The corpus a project's Tier-0 map spans is small by design (map.py keeps
# it context-sized), so this beam is deliberately shallow: coverage top-up
# on top of the dominant broad-select step, not a big-graph search.
# ---------------------------------------------------------------------------

BEAM_WIDTH: int = 3   # max frontier nodes carried into the next hop (2-3 by design)
BEAM_DEPTH: int = 3   # max hops walked from the anchor set (~3 by design)


# ---------------------------------------------------------------------------
# Intent -> edge-tag routing. The tag VOCABULARY is imported verbatim from
# ``review.relate_check._TAG_FAMILY`` (the SSOT) — this table only decides
# WHICH of those already-defined tags a given query intent should walk; it
# never invents or renames a tag. The module-level assertion below pins
# that every routed tag actually exists in the SSOT, so a future rename in
# relate_check.py fails loudly here instead of silently routing nothing.
# ---------------------------------------------------------------------------

INTENT_EDGE_ROUTES: dict[str, frozenset[str]] = {
    "counter-evidence": frozenset({"CONTRADICTS"}),
    "basis": frozenset({"GROUNDED-IN", "USES", "DERIVED-FROM"}),
    "results": frozenset({"ANSWERS", "ANSWERED-BY", "ADDRESSES", "ADDRESSED-BY"}),
    "lineage": frozenset({"EXTENDS", "FOUNDATION-FOR", "SUPPORTS"}),
}

_ALL_ROUTED_TAGS: frozenset[str] = frozenset().union(*INTENT_EDGE_ROUTES.values())
assert _ALL_ROUTED_TAGS <= frozenset(_TAG_FAMILY), (
    "traverse.py's INTENT_EDGE_ROUTES references a tag absent from "
    "review.relate_check._TAG_FAMILY (the edge-vocabulary SSOT) — fix the "
    "routing table, never re-hardcode the vocabulary here."
)

# A lightweight, mechanical (non-LLM) keyword classifier — intent-routing
# is a cheap deterministic filter over which edge tags to walk, not a
# judgment call worth spending an LLM round-trip on. The two genuinely
# LLM-shaped decisions in this module are the anchor select and the
# per-hop prune (see the module docstring); this is neither.
_INTENT_KEYWORDS: dict[str, frozenset[str]] = {
    "counter-evidence": frozenset({
        "contradict", "disagree", "counter", "refute", "conflicting", "dispute",
    }),
    "basis": frozenset({
        "basis", "ground", "grounded", "based on", "rely", "relies", "derive",
        "derived",
    }),
    "results": frozenset({
        "result", "answer", "gap", "address", "close", "closes", "closed",
    }),
    "lineage": frozenset({
        "extend", "extends", "develop", "lineage", "evolve", "build on",
        "builds on", "foundation",
    }),
}


def classify_intent(query: str) -> tuple[frozenset[str], bool]:
    """Route a free-text query to the edge tags it implies.

    Returns ``(routed_tags, matched)``. ``matched`` is False when no
    keyword fired for any intent — the honest fallback is to walk EVERY
    known tag (never silently walk nothing just because the classifier
    found no signal), surfaced via ``matched=False`` so a caller can
    distinguish "routed narrowly" from "no intent signal, walked broad".
    """
    q = query.lower()
    routed: set[str] = set()
    matched = False
    for intent, keywords in _INTENT_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            matched = True
            routed |= INTENT_EDGE_ROUTES[intent]
    if not matched:
        return frozenset(_TAG_FAMILY), False
    return frozenset(routed), True


# ---------------------------------------------------------------------------
# Edge collection + target resolution — reuses review.relate_check's
# parsers and unified target grammar (``_TARGET_RE``) verbatim; this module
# adds no new edge grammar.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Edge:
    tag: str
    reason: str
    # "literature" | "concepts" | "cross-bundle" | "within-project"
    # (an "artifact"-scoped typed edge is dropped upstream — an artifact
    # target has no description/body, so it is not a traversable note).
    scope: str
    # slug (literature/concepts scope) or the raw matched target string
    # (cross-bundle/within-project scope — re-parsed via ``_TARGET_RE`` at
    # resolution time so no new grammar is defined here).
    target: str


def collect_edges(body: str) -> list[_Edge]:
    """Every outgoing typed edge from a note body, across all three
    ``review.relate_check`` parsers (full-body scan, same as every other
    consumer of that module) — artifact-scoped edges excluded (not
    traversable notes)."""
    edges: list[_Edge] = []
    for e in parse_paper_relations(body).edges:
        edges.append(_Edge(tag=e["tag"], reason=e["reason"], scope="literature", target=e["target"]))
    for e in parse_concept_edges(body).edges:
        edges.append(_Edge(tag=e["tag"], reason=e["reason"], scope="concepts", target=e["target"]))
    for e in parse_typed_edges(body).edges:
        if e["scope"] == "artifact":
            continue
        edges.append(_Edge(tag=e["tag"], reason=e["reason"], scope=e["scope"], target=e["target"]))
    return edges


def _slug_from_x_path(x_path: str) -> str:
    """Basename (no extension) of a cross-bundle ``x_path`` capture (the
    group includes the trailing ``.md`` per ``_TARGET_RE`` — stripped
    here)."""
    stem = x_path[:-3] if x_path.endswith(".md") else x_path
    return Path(stem).name


def resolve_edge_path(cfg: Config, project: str | None, edge: _Edge) -> tuple[Path | None, str, str]:
    """Resolve one edge's target to ``(path, okf_type, slug)``.

    ``path`` is ``None`` for an unresolvable target (unknown bundle,
    absent project context for a within-project target, or a target that
    simply does not exist on disk) — never raises, mirroring
    ``Config.resolve_bundle_link``'s own contract; the caller decides how
    loudly to surface an unresolved edge.
    """
    if edge.scope in ("literature", "concepts"):
        slug = edge.target
        return cfg.shared_type_root(edge.scope) / f"{slug}.md", edge.scope, slug

    if edge.scope == "cross-bundle":
        m = _TARGET_RE.fullmatch(edge.target)
        if m is None or m.group("x_bundle") is None:
            return None, "unknown", ""
        bundle = m.group("x_bundle")
        slug = _slug_from_x_path(m.group("x_path"))
        return cfg.resolve_bundle_link(edge.target), bundle, slug

    if edge.scope == "within-project":
        m = _TARGET_RE.fullmatch(edge.target)
        if m is None or m.group("p_type") is None:
            return None, "unknown", ""
        p_type, p_slug = m.group("p_type"), m.group("p_slug")
        if project is None:
            return None, p_type, p_slug
        return cfg.project_notes_dir(project) / p_type / f"{p_slug}.md", p_type, p_slug

    return None, "unknown", ""


def _describe(path: Path) -> tuple[str, str]:
    """``(title, description)`` from a note's frontmatter only — the
    description-first discipline: the per-hop prune judges a candidate on
    this alone, never its body. The body is only ever parsed (via
    ``collect_edges``) for a node that is actually VISITED (kept)."""
    text = path.read_text(encoding="utf-8")
    fields, _body = _parse_frontmatter(text)
    return str(fields.get("title") or "").strip(), str(fields.get("description") or "").strip()


def _resolve_tier0_path(cfg: Config, project: str, okf_type: str, slug: str) -> Path:
    """Resolve a Tier-0 anchor candidate (from ``retrieval.map.generate_map``'s
    ``concept_index``/``moc_index``/``findings_gaps_index``) to its note
    path. Concepts are shared-canonical; everything else in Tier-0 is this
    project's own (mocs/findings/gaps)."""
    if okf_type == "concepts":
        return cfg.shared_type_root("concepts") / f"{slug}.md"
    return cfg.project_notes_dir(project) / okf_type / f"{slug}.md"


# ---------------------------------------------------------------------------
# Phase 1 — broad multi-anchor select (the dominant step)
# ---------------------------------------------------------------------------

_ANCHOR_KIND = "anchor-select"


def _anchor_candidates_from_map(tier0_map: dict[str, Any]) -> list[dict[str, str]]:
    """Flatten a ``retrieval.map.generate_map`` result's three Tier-0
    indices into one candidate list for the anchor-select task — this
    module consumes the map, it never rebuilds it."""
    candidates: list[dict[str, str]] = []
    for c in tier0_map.get("concept_index", []) or []:
        candidates.append({
            "okf_type": "concepts", "slug": c["slug"],
            "title": c.get("title", ""), "description": c.get("description", ""),
        })
    for m in tier0_map.get("moc_index", []) or []:
        candidates.append({
            "okf_type": "mocs", "slug": m["slug"],
            "title": m.get("title", ""), "description": m.get("description", ""),
        })
    for f in tier0_map.get("findings_gaps_index", []) or []:
        candidates.append({
            "okf_type": f.get("note_type", "findings"), "slug": f["slug"],
            "title": f.get("title", ""), "description": f.get("description", ""),
        })
    return candidates


def _anchor_canary_bank() -> list[tuple[dict[str, Any], str]]:
    """One bidirectional-flavoured anchor-select canary: an unambiguous
    on-topic candidate alongside an unambiguous off-topic decoy, verifying
    the judge is actually reading candidates against the query rather than
    rubber-stamping or ignoring the task."""
    task = {
        "kind": _ANCHOR_KIND,
        "query": "canary probe: which of these concerns bicycle maintenance?",
        "candidates": [
            {
                "okf_type": "concepts", "slug": "canary-bicycle-chain-lubrication",
                "title": "Bicycle chain lubrication",
                "description": "How and when to lubricate a bicycle chain to prevent wear.",
            },
            {
                "okf_type": "concepts", "slug": "canary-quantum-decoherence-timescales",
                "title": "Quantum decoherence timescales",
                "description": "Timescales over which quantum coherence is lost in open systems.",
            },
        ],
    }
    return [(task, "canary-bicycle-chain-lubrication")]


def emit_anchor_select_task(tier0_map: dict[str, Any], query: str) -> dict[str, Any]:
    """Emit the broad multi-anchor select task (Phase 1) — ONE task
    covering the entire Tier-0 candidate set, plus one interleaved canary.

    Returns ``{"tasks_doc": {...}, "canary_key_doc": {...}}`` — write both
    with ``gates.judge_seam.write_json`` for the harness fan-out.
    """
    real_task = {
        "kind": _ANCHOR_KIND,
        "query": query,
        "candidates": _anchor_candidates_from_map(tier0_map),
    }
    combined, canary_key = judge_seam.interleave_with_canaries([real_task], _anchor_canary_bank())
    tasks_doc = {
        "schema": judge_seam.TASKS_SCHEMA,
        "gate": "traversal-anchor-select",
        "judge_kind": "cold",
        "created": judge_seam.now_iso(),
        "instructions": (
            "For EACH task, read every offered candidate's title + "
            "description against the query. Select EVERY candidate that "
            "is plausibly relevant — broad recall, not a single best "
            "match. Answer with a comma-separated list of the selected "
            "candidates' `slug` values (an empty string if none apply)."
        ),
        "tasks": combined,
    }
    canary_key_doc = {"schema": judge_seam.CANARY_KEY_SCHEMA, "canaries": canary_key}
    return {"tasks_doc": tasks_doc, "canary_key_doc": canary_key_doc}


def ingest_anchor_select_verdicts(
    tasks_doc: dict[str, Any],
    canary_key_doc: dict[str, Any] | None,
    verdicts_doc: dict[str, Any] | None,
) -> dict[str, Any]:
    """Ingest the anchor-select verdicts (Phase 1) — id-join, canary
    check, fail-closed to an EMPTY anchor set (never a fabricated one) on
    any missing/incomplete fan-out.

    Returns ``{"anchors": [...], "halt": bool, "halt_reason": str,
    "errors": [...], "warnings": [...]}``. Each anchor dict carries
    ``{"okf_type", "slug", "title", "description"}`` — unresolved to a
    filesystem path here (this function has no ``Config``); the caller
    (``TraversalEngine``) resolves paths.
    """
    canaries = (canary_key_doc or {}).get("canaries", {})
    real_tasks = [t for t in tasks_doc.get("tasks", []) if t.get("id") not in canaries]
    if not real_tasks:
        return {"anchors": [], "halt": False, "halt_reason": "", "errors": [], "warnings": []}

    if judge_seam.fanout_incomplete(tasks_doc, verdicts_doc):
        return {
            "anchors": [],
            "halt": True,
            "halt_reason": (
                "verdicts file absent/empty for a non-empty anchor-select "
                "task — fan-out did not complete."
            ),
            "errors": [
                "traversal anchor-select judge-fanout HALT: verdicts "
                "missing/empty — anchor selection was never run. This is "
                "NOT a pass."
            ],
            "warnings": [],
        }

    verdict_by_id: dict[str, str] = {}
    for v in (verdicts_doc or {}).get("verdicts", []):
        vid = v.get("id")
        if vid:
            verdict_by_id[vid] = str(v.get("verdict", ""))

    # Canary check FIRST — an untrustworthy judge invalidates everything else.
    judge_seam.check_canaries(canaries, verdict_by_id)

    task = real_tasks[0]
    candidate_by_slug = {c["slug"]: c for c in task.get("candidates", [])}
    raw = verdict_by_id.get(task["id"])

    errors: list[str] = []
    warnings: list[str] = []
    if raw is None:
        errors.append(
            f"traversal anchor-select: verdict for task {task['id']!r} is "
            f"missing from the verdicts file — no anchors selected "
            f"(fail-closed: an empty anchor set, never a fabricated one)."
        )
        return {"anchors": [], "halt": False, "halt_reason": "", "errors": errors, "warnings": warnings}

    anchors: list[dict[str, Any]] = []
    for slug in (s.strip() for s in raw.split(",")):
        if not slug:
            continue
        cand = candidate_by_slug.get(slug)
        if cand is None:
            warnings.append(
                f"traversal anchor-select: judge returned slug {slug!r} "
                f"not present in the offered candidate set — ignored."
            )
            continue
        anchors.append(cand)

    return {"anchors": anchors, "halt": False, "halt_reason": "", "errors": errors, "warnings": warnings}


# ---------------------------------------------------------------------------
# Phase 2 — intent-routed shallow beam, per-hop batched prune
# ---------------------------------------------------------------------------

@dataclass
class VisitedNode:
    """A node this engine has actually visited (an anchor, or a
    prune-kept beam candidate)."""

    path: str
    okf_type: str
    slug: str
    title: str
    description: str
    depth: int
    via_tag: str | None
    reason: str | None


@dataclass
class Candidate:
    """A neighbour discovered from an edge, NOT yet pruned — carries only
    its description (never its body) until a KEEP verdict promotes it to
    a ``VisitedNode``."""

    edge_tag: str
    reason: str
    okf_type: str
    slug: str
    path: str
    title: str
    description: str
    source_path: str
    depth: int


_PRUNE_KIND = "prune"
_PRUNE_VOCAB: frozenset[str] = frozenset({"KEEP", "DROP"})
# A dropped frontier node is lost recall FOREVER (no later hop recovers
# it) — the fail-closed direction here is the opposite of a typical
# reject-only gate: default to KEEP, not DROP, on anything missing or
# unparseable.
_PRUNE_FAIL_CLOSED_DEFAULT = "KEEP"


def _prune_canary_bank() -> list[tuple[dict[str, Any], str]]:
    """Bidirectional per-hop-prune canaries: one unambiguous KEEP, one
    unambiguous DROP — catches both a rubber-stamping judge (always KEEP)
    and a blind/broken one (always DROP, which would silently starve the
    beam)."""
    keep_task = {
        "kind": _PRUNE_KIND,
        "edge_tag": "CONTRADICTS",
        "edge_reason": "canary probe: directly disputes the anchor's central claim",
        "candidate_slug": "canary-obvious-keep",
        "candidate_title": "Direct refutation of the anchor's central claim",
        "candidate_description": (
            "This note reports data that directly contradicts the anchor "
            "finding under discussion, on the exact same measured outcome."
        ),
    }
    drop_task = {
        "kind": _PRUNE_KIND,
        "edge_tag": "USES",
        "edge_reason": "canary probe: an unrelated registration edge",
        "candidate_slug": "canary-obvious-drop",
        "candidate_title": "Unrelated dataset licensing note",
        "candidate_description": (
            "A note recording the software license terms of an unrelated "
            "dataset, with no bearing on the anchor's subject matter."
        ),
    }
    return [(keep_task, "KEEP"), (drop_task, "DROP")]


def emit_hop_prune_tasks(candidates: list[Candidate]) -> dict[str, Any]:
    """Emit ONE batched task doc for an entire beam layer's candidates —
    one round-trip per hop, not one per candidate (the latency discipline
    the design calls for: a naive per-node emit/ingest would make even a
    3-hop beam serially slow)."""
    real_tasks = [
        {
            "kind": _PRUNE_KIND,
            "edge_tag": c.edge_tag,
            "edge_reason": c.reason,
            "candidate_slug": c.slug,
            "candidate_title": c.title,
            "candidate_description": c.description,
        }
        for c in candidates
    ]
    combined, canary_key = judge_seam.interleave_with_canaries(real_tasks, _prune_canary_bank())
    tasks_doc = {
        "schema": judge_seam.TASKS_SCHEMA,
        "gate": "traversal-hop-prune",
        "judge_kind": "cold",
        "created": judge_seam.now_iso(),
        "instructions": (
            "For EACH task, decide whether this neighbouring note is "
            "worth visiting, given ONLY the edge that leads to it and its "
            "title + description (its body is not provided — judge on "
            "the description alone). Answer KEEP or DROP."
        ),
        "tasks": combined,
    }
    canary_key_doc = {"schema": judge_seam.CANARY_KEY_SCHEMA, "canaries": canary_key}
    return {"tasks_doc": tasks_doc, "canary_key_doc": canary_key_doc}


def ingest_hop_prune_verdicts(
    tasks_doc: dict[str, Any],
    canary_key_doc: dict[str, Any] | None,
    verdicts_doc: dict[str, Any] | None,
) -> dict[str, Any]:
    """Ingest one hop layer's prune verdicts — id-join, canary check,
    fail-closed-to-KEEP fill (see ``_PRUNE_FAIL_CLOSED_DEFAULT``).

    Returns ``{"decisions": {task_id: "KEEP"|"DROP"}, "halt": bool,
    "halt_reason": str, "errors": [...], "warnings": [...]}``.
    """
    canaries = (canary_key_doc or {}).get("canaries", {})
    real_task_ids = [t["id"] for t in tasks_doc.get("tasks", []) if t["id"] not in canaries]
    if not real_task_ids:
        return {"decisions": {}, "halt": False, "halt_reason": "", "errors": [], "warnings": []}

    if judge_seam.fanout_incomplete(tasks_doc, verdicts_doc):
        return {
            "decisions": {},
            "halt": True,
            "halt_reason": (
                "verdicts file absent/empty for a non-empty hop-prune "
                "task set — fan-out did not complete."
            ),
            "errors": [
                "traversal hop-prune judge-fanout HALT: verdicts "
                "missing/empty. This is NOT a pass."
            ],
            "warnings": [],
        }

    verdict_by_id: dict[str, str] = {}
    for v in (verdicts_doc or {}).get("verdicts", []):
        vid = v.get("id")
        if vid:
            verdict_by_id[vid] = str(v.get("verdict", ""))

    judge_seam.check_canaries(canaries, verdict_by_id)

    filled, missing_ids, unrecognized_ids = judge_seam.fail_closed_fill(
        real_task_ids, verdict_by_id, _PRUNE_VOCAB, _PRUNE_FAIL_CLOSED_DEFAULT,
    )

    warnings: list[str] = []
    if missing_ids:
        warnings.append(
            f"traversal hop-prune: {len(missing_ids)} task(s) missing a "
            f"verdict, defaulted KEEP (fail-closed — never silently drop "
            f"a frontier node): {missing_ids}"
        )
    if unrecognized_ids:
        warnings.append(
            f"traversal hop-prune: {len(unrecognized_ids)} task(s) had an "
            f"unrecognized verdict string, defaulted KEEP (fail-closed): "
            f"{unrecognized_ids}"
        )

    return {"decisions": filled, "halt": False, "halt_reason": "", "errors": [], "warnings": warnings}


# ---------------------------------------------------------------------------
# The stepwise engine — drives both phases across the async harness
# emit/ingest boundary (a cold subagent runs between emit and ingest; this
# class is a resumable state machine, not a single blocking call).
# ---------------------------------------------------------------------------

class TraversalEngine:
    """Broad-select-then-beam traversal, driven step by step across the
    harness fan-out boundary.

    Usage (mirrors every other cold-agent-judge gate in this codebase):
        engine = TraversalEngine(cfg, project, tier0_map)
        tasks_doc = engine.emit_anchor_select(query)
        # ... hand tasks_doc to the harness fan-out, get back verdicts_doc ...
        engine.ingest_anchor_select(verdicts_doc)
        while not engine.done:
            tasks_doc = engine.emit_hop_prune()
            if tasks_doc is None:
                break
            # ... fan-out again ...
            engine.ingest_hop_prune(verdicts_doc)
        result = engine.result()

    **Backtrack.** When a frontier node's body yields ZERO fresh candidate
    edges (a dead end — no productive edges, everything already visited,
    or everything filtered out by intent routing), the engine substitutes
    a sibling candidate from the SAME hop's backtrack pool: a candidate
    the judge already KEPT but that lost out to the beam-width cap. No
    extra judge round-trip is spent on the substitute (it was already
    judged) — this is retreat-and-try-an-alternative, not re-judging.

    **Visited-set.** ``self.visited`` (path -> ``VisitedNode``) is checked
    before a candidate is even offered to the prune judge (no wasted
    LLM call) AND again before it is promoted into the next frontier — a
    node is never pulled twice.
    """

    def __init__(
        self,
        cfg: Config,
        project: str,
        tier0_map: dict[str, Any],
        *,
        width: int = BEAM_WIDTH,
        depth: int = BEAM_DEPTH,
    ) -> None:
        self.cfg = cfg
        self.project = project
        self.tier0_map = tier0_map
        self.width = width
        self.depth = depth

        self.query = ""
        self.routed_tags: frozenset[str] = frozenset()
        self.route_matched = False

        self.visited: dict[str, VisitedNode] = {}
        self._frontier: list[VisitedNode] = []
        self._backtrack_pool: list[Candidate] = []
        self._edges_walked: list[dict[str, Any]] = []
        self._hop_index = 0

        self._anchor_task_state: tuple[dict[str, Any], dict[str, Any]] | None = None
        self._hop_task_state: tuple[dict[str, Any], dict[str, Any]] | None = None
        self._pending: list[tuple[Candidate, str]] = []

        self.done = False
        self.halted = False
        self.halt_reason = ""
        self.errors: list[str] = []
        self.warnings: list[str] = []

    # --- Phase 1 ---

    def emit_anchor_select(self, query: str) -> dict[str, Any]:
        self.query = query
        self.routed_tags, self.route_matched = classify_intent(query)
        result = emit_anchor_select_task(self.tier0_map, query)
        self._anchor_task_state = (result["tasks_doc"], result["canary_key_doc"])
        return result["tasks_doc"]

    def ingest_anchor_select(self, verdicts_doc: dict[str, Any] | None) -> dict[str, Any]:
        assert self._anchor_task_state is not None, "ingest_anchor_select called before emit_anchor_select"
        tasks_doc, canary_key_doc = self._anchor_task_state
        result = ingest_anchor_select_verdicts(tasks_doc, canary_key_doc, verdicts_doc)
        self.errors.extend(result["errors"])
        self.warnings.extend(result["warnings"])
        if result["halt"]:
            self.halted = True
            self.halt_reason = result["halt_reason"]
            self.done = True
            return result

        for anc in result["anchors"]:
            path = _resolve_tier0_path(self.cfg, self.project, anc["okf_type"], anc["slug"])
            if not path.is_file():
                self.warnings.append(
                    f"traversal: anchor {anc['slug']!r} ({anc['okf_type']}) "
                    f"selected by the judge has no materialized note at "
                    f"{path} — skipped."
                )
                continue
            key = str(path)
            if key in self.visited:
                continue
            node = VisitedNode(
                path=key, okf_type=anc["okf_type"], slug=anc["slug"],
                title=anc.get("title", ""), description=anc.get("description", ""),
                depth=0, via_tag=None, reason="anchor",
            )
            self.visited[key] = node
            self._frontier.append(node)

        if not self._frontier:
            self.done = True
        return result

    # --- Phase 2 ---

    def emit_hop_prune(self) -> dict[str, Any] | None:
        """Returns the tasks doc for this hop layer, or ``None`` (and
        marks ``self.done``) when there is nothing left to expand — depth
        exhausted, the frontier is empty, or the frontier yielded zero
        fresh candidates (a whole-beam dead end)."""
        if self.done or self._hop_index >= self.depth or not self._frontier:
            self.done = True
            return None

        candidates = self._collect_candidates(self._frontier)
        if not candidates:
            self.done = True
            return None

        result = emit_hop_prune_tasks(candidates)
        tasks_doc, canary_key_doc = result["tasks_doc"], result["canary_key_doc"]
        canaries = canary_key_doc.get("canaries", {})
        real_ids_in_order = [t["id"] for t in tasks_doc["tasks"] if t["id"] not in canaries]
        self._pending = list(zip(candidates, real_ids_in_order))
        self._hop_task_state = (tasks_doc, canary_key_doc)
        return tasks_doc

    def ingest_hop_prune(self, verdicts_doc: dict[str, Any] | None) -> dict[str, Any]:
        assert self._hop_task_state is not None, "ingest_hop_prune called before emit_hop_prune"
        tasks_doc, canary_key_doc = self._hop_task_state
        result = ingest_hop_prune_verdicts(tasks_doc, canary_key_doc, verdicts_doc)
        self.errors.extend(result["errors"])
        self.warnings.extend(result["warnings"])
        if result["halt"]:
            self.halted = True
            self.halt_reason = result["halt_reason"]
            self.done = True
            return result

        decisions = result["decisions"]
        kept = [c for c, tid in self._pending if decisions.get(tid) == "KEEP"]
        beam = kept[: self.width]
        self._backtrack_pool = kept[self.width:]

        next_frontier: list[VisitedNode] = []
        for c in beam:
            if c.path in self.visited:
                continue
            node = VisitedNode(
                path=c.path, okf_type=c.okf_type, slug=c.slug,
                title=c.title, description=c.description,
                depth=c.depth, via_tag=c.edge_tag, reason=c.reason,
            )
            self.visited[c.path] = node
            next_frontier.append(node)
            self._edges_walked.append({
                "from": c.source_path, "to": c.path, "tag": c.edge_tag,
                "reason": c.reason, "backtrack": False,
            })

        self._hop_index += 1
        self._frontier = next_frontier
        self._pending = []
        if not self._frontier:
            self.done = True
        return result

    # --- internals ---

    def _collect_candidates(self, frontier: list[VisitedNode]) -> list[Candidate]:
        """Every fresh, intent-routed, unvisited candidate edge across an
        entire beam layer — with dead-end backtrack substitution (see the
        class docstring)."""
        candidates: list[Candidate] = []
        seen_this_hop: set[str] = set()
        working = list(frontier)
        idx = 0
        while idx < len(working):
            node = working[idx]
            node_candidates = self._edges_for_node(node, seen_this_hop)
            if not node_candidates:
                replacement = self._pop_backtrack_replacement()
                if replacement is not None:
                    repl_node = VisitedNode(
                        path=replacement.path, okf_type=replacement.okf_type,
                        slug=replacement.slug, title=replacement.title,
                        description=replacement.description, depth=replacement.depth,
                        via_tag=replacement.edge_tag, reason=replacement.reason,
                    )
                    self.visited[replacement.path] = repl_node
                    self._edges_walked.append({
                        "from": replacement.source_path, "to": replacement.path,
                        "tag": replacement.edge_tag, "reason": replacement.reason,
                        "backtrack": True,
                    })
                    working[idx] = repl_node
                    continue  # retry expansion with the substitute, same index
            candidates.extend(node_candidates)
            idx += 1
        return candidates

    def _pop_backtrack_replacement(self) -> Candidate | None:
        while self._backtrack_pool:
            cand = self._backtrack_pool.pop(0)
            if cand.path not in self.visited:
                return cand
        return None

    def _edges_for_node(self, node: VisitedNode, seen_this_hop: set[str]) -> list[Candidate]:
        path = Path(node.path)
        if not path.is_file():
            return []
        text = path.read_text(encoding="utf-8")
        _fields, body = _parse_frontmatter(text)

        out: list[Candidate] = []
        for edge in collect_edges(body):
            if self.route_matched and edge.tag not in self.routed_tags:
                continue
            target_path, okf_type, slug = resolve_edge_path(self.cfg, self.project, edge)
            if target_path is None or not target_path.is_file():
                continue
            key = str(target_path)
            if key in self.visited or key in seen_this_hop:
                continue
            seen_this_hop.add(key)
            title, description = _describe(target_path)
            out.append(Candidate(
                edge_tag=edge.tag, reason=edge.reason, okf_type=okf_type, slug=slug,
                path=key, title=title, description=description,
                source_path=node.path, depth=node.depth + 1,
            ))
        return out

    # --- result ---

    def result(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "routed_tags": sorted(self.routed_tags) if self.route_matched else [],
            "route_matched": self.route_matched,
            "visited": [
                {
                    "path": n.path, "okf_type": n.okf_type, "slug": n.slug,
                    "title": n.title, "description": n.description,
                    "depth": n.depth, "via_tag": n.via_tag, "reason": n.reason,
                }
                for n in self.visited.values()
            ],
            "edges_walked": list(self._edges_walked),
            "hops": self._hop_index,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }
