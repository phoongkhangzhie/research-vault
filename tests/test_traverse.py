# SPDX-License-Identifier: AGPL-3.0-or-later
"""test_traverse.py — acceptance tests for retrieval/traverse.py: the
broad-multi-anchor-select + intent-routed shallow beam traversal policy.

rv unit tests mock the LLM fan-out (charter: harness-native judges are
never called directly from a unit test) — verdicts docs are hand-built
dicts, exactly the shape a cold subagent would write to
``_judge-verdicts.json``, and fed straight to ``ingest_*`` without ever
touching a real judge or the filesystem's fan-out directory.

Coverage:
  - broad-select: one pass over the whole Tier-0 candidate set returns
    every selected anchor.
  - intent-routing: a counter-evidence query walks CONTRADICTS only; a
    basis/grounding query walks GROUNDED-IN/USES/DERIVED-FROM only; no
    intent signal walks every tag (honest fallback).
  - visited-set: a diamond (two anchors -> one shared neighbour) offers
    that neighbour to the prune judge exactly once and visits it once.
  - backtrack: a dead-end beam pick is replaced by a same-hop sibling
    candidate the judge already KEPT but that lost the width cap.
  - width/depth bounds: a wide/deep fixture never exceeds the configured
    beam constants.
  - cross-layer reach: a paper absent from Tier-0 is reached via a
    concept->paper edge.
  - fail-closed: a malformed per-hop prune verdict defaults to KEEP
    (never silently drops a frontier node); canary mismatch aborts the
    round.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import Config, reset_config_cache
from research_vault.gates.judge_seam import CanaryAbortError
from research_vault.retrieval.traverse import (
    BEAM_DEPTH,
    BEAM_WIDTH,
    TraversalEngine,
    classify_intent,
    ingest_hop_prune_verdicts,
)


@pytest.fixture(autouse=True)
def _reset_cfg():
    reset_config_cache()
    yield
    reset_config_cache()


# ---------------------------------------------------------------------------
# Fixture builders — mirrors tests/test_map.py's conventions.
# ---------------------------------------------------------------------------

def _cfg(tmp_path: Path) -> Config:
    proj_dir = tmp_path / "projects" / "demo-proj"
    proj_dir.mkdir(parents=True)
    raw = {
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(tmp_path / "state"),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "projects": {
            "demo-proj": {
                "code": "dp",
                "source_dir": str(proj_dir),
                "roster": ["engineer"],
            },
        },
    }
    return Config(raw)


def _write(path: Path, *, note_type: str, title: str, description: str, edges: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    edge_block = "\n".join(edges)
    path.write_text(
        f'---\ntype: {note_type}\ntitle: {title}\ndescription: "{description}"\n---\n\n'
        f"## Edges\n\n{edge_block}\n",
        encoding="utf-8",
    )
    return path


def _concept(cfg: Config, slug: str, *, description: str = "A concept.", edges: list[str] | None = None) -> Path:
    return _write(cfg.concepts_root / f"{slug}.md", note_type="concepts", title=slug,
                  description=description, edges=edges or [])


def _finding(cfg: Config, project: str, slug: str, *, description: str = "A finding.",
             edges: list[str] | None = None) -> Path:
    return _write(cfg.project_notes_dir(project) / "findings" / f"{slug}.md", note_type="findings",
                  title=slug, description=description, edges=edges or [])


def _gap(cfg: Config, project: str, slug: str, *, description: str = "A gap.",
         edges: list[str] | None = None) -> Path:
    return _write(cfg.project_notes_dir(project) / "gaps" / f"{slug}.md", note_type="gaps",
                  title=slug, description=description, edges=edges or [])


def _experiment(cfg: Config, project: str, slug: str, *, description: str = "An experiment.",
                 edges: list[str] | None = None) -> Path:
    return _write(cfg.project_notes_dir(project) / "experiments" / f"{slug}.md", note_type="experiments",
                  title=slug, description=description, edges=edges or [])


def _literature(cfg: Config, citekey: str, *, description: str = "A paper.",
                 edges: list[str] | None = None) -> Path:
    return _write(cfg.literature_root / f"{citekey}.md", note_type="literature",
                  title=citekey, description=description, edges=edges or [])


def _tier0_map(*, concepts: list[str] = (), findings: list[str] = (), gaps: list[str] = (),
                mocs: list[str] = ()) -> dict:
    """A hand-built ``generate_map``-shaped dict — traverse.py consumes
    this shape, it never rebuilds it."""
    return {
        "concept_index": [
            {"slug": s, "title": s, "description": f"Concept {s}."} for s in concepts
        ],
        "moc_index": [
            {"slug": s, "title": s, "description": f"MOC {s}."} for s in mocs
        ],
        "findings_gaps_index": [
            {"slug": s, "title": s, "description": f"Finding {s}.", "note_type": "findings"} for s in findings
        ] + [
            {"slug": s, "title": s, "description": f"Gap {s}.", "note_type": "gaps"} for s in gaps
        ],
    }


def _anchor_verdicts_doc(tasks_doc: dict, *, selected: list[str]) -> dict:
    real_task = next(t for t in tasks_doc["tasks"] if t.get("kind") == "anchor-select")
    canary_task = next(t for t in tasks_doc["tasks"] if t.get("kind") == "anchor-select" and t["id"] != real_task["id"])
    verdicts = [
        {"id": real_task["id"], "verdict": ",".join(selected)},
        {"id": canary_task["id"], "verdict": "canary-bicycle-chain-lubrication"},
    ]
    return {"schema": "rv-judge-verdicts/v1", "verdicts": verdicts}


def _prune_verdicts_doc(tasks_doc: dict, *, keep: set[str] | None = None, override: dict | None = None) -> dict:
    """Build a verdicts doc for a hop-prune tasks_doc. ``keep`` names the
    ``candidate_slug`` values that should get KEEP (everything else DROP);
    correctly answers both canaries. ``override`` maps task id -> raw
    verdict string, applied AFTER the keep/drop pass (for malformed/missing
    verdict tests)."""
    keep = keep or set()
    verdict_by_id: dict[str, str] = {}
    for t in tasks_doc["tasks"]:
        if t.get("candidate_slug") == "canary-obvious-keep":
            verdict_by_id[t["id"]] = "KEEP"
        elif t.get("candidate_slug") == "canary-obvious-drop":
            verdict_by_id[t["id"]] = "DROP"
        else:
            verdict_by_id[t["id"]] = "KEEP" if t.get("candidate_slug") in keep else "DROP"
    if override:
        for tid, v in override.items():
            if v is None:
                verdict_by_id.pop(tid, None)
            else:
                verdict_by_id[tid] = v
    return {
        "schema": "rv-judge-verdicts/v1",
        "verdicts": [{"id": tid, "verdict": v} for tid, v in verdict_by_id.items()],
    }


def _run_anchor_select(engine: TraversalEngine, query: str, *, selected: list[str]) -> dict:
    tasks_doc = engine.emit_anchor_select(query)
    verdicts_doc = _anchor_verdicts_doc(tasks_doc, selected=selected)
    return engine.ingest_anchor_select(verdicts_doc)


def _real_prune_slugs(tasks_doc: dict) -> set[str]:
    """Candidate slugs offered in a hop-prune tasks_doc, excluding the
    interleaved canary probes (``canary-obvious-keep``/``-drop``)."""
    return {
        t["candidate_slug"] for t in tasks_doc["tasks"]
        if t.get("kind") == "prune" and not str(t.get("candidate_slug", "")).startswith("canary-")
    }


def _run_one_hop(engine: TraversalEngine, *, keep: set[str] | None = None, override: dict | None = None):
    tasks_doc = engine.emit_hop_prune()
    if tasks_doc is None:
        return None, None
    verdicts_doc = _prune_verdicts_doc(tasks_doc, keep=keep, override=override)
    result = engine.ingest_hop_prune(verdicts_doc)
    return tasks_doc, result


# ---------------------------------------------------------------------------
# classify_intent — mechanical routing, no LLM
# ---------------------------------------------------------------------------

def test_intent_counter_evidence_routes_to_contradicts():
    tags, matched = classify_intent("what contradicts this finding?")
    assert matched is True
    assert tags == frozenset({"CONTRADICTS"})


def test_intent_basis_routes_to_grounding_family():
    tags, matched = classify_intent("what is this claim grounded in?")
    assert matched is True
    assert tags == frozenset({"GROUNDED-IN", "USES", "DERIVED-FROM"})


def test_intent_no_signal_falls_back_to_every_tag():
    tags, matched = classify_intent("tell me about the corpus")
    assert matched is False
    from research_vault.review.relate_check import _TAG_FAMILY
    assert tags == frozenset(_TAG_FAMILY)


# ---------------------------------------------------------------------------
# Phase 1 — broad multi-anchor select
# ---------------------------------------------------------------------------

def test_broad_select_returns_all_plausible_anchors_in_one_pass(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _concept(cfg, "concept-a")
    _concept(cfg, "concept-b")
    _concept(cfg, "concept-c")
    m = _tier0_map(concepts=["concept-a", "concept-b", "concept-c"])

    engine = TraversalEngine(cfg, "demo-proj", m)
    result = _run_anchor_select(engine, "tell me about a and c", selected=["concept-a", "concept-c"])

    assert result["halt"] is False
    assert {a["slug"] for a in result["anchors"]} == {"concept-a", "concept-c"}
    # one pass -> both anchors already visited at depth 0
    assert {n.slug for n in engine.visited.values()} == {"concept-a", "concept-c"}
    assert all(n.depth == 0 for n in engine.visited.values())


def test_broad_select_missing_verdict_is_empty_not_fabricated(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _concept(cfg, "concept-a")
    m = _tier0_map(concepts=["concept-a"])
    engine = TraversalEngine(cfg, "demo-proj", m)

    tasks_doc = engine.emit_anchor_select("anything")
    canary_task = next(t for t in tasks_doc["tasks"] if t["id"] in engine._anchor_task_state[1]["canaries"])
    # only the canary answered -- the real task id got no verdict at all.
    verdicts_doc = {"verdicts": [{"id": canary_task["id"], "verdict": "canary-bicycle-chain-lubrication"}]}
    result = engine.ingest_anchor_select(verdicts_doc)

    assert result["halt"] is False
    assert result["anchors"] == []
    assert any("missing" in e for e in result["errors"])
    assert engine.done is True  # empty frontier -> nothing to traverse


def test_broad_select_halts_when_fanout_never_ran(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _concept(cfg, "concept-a")
    m = _tier0_map(concepts=["concept-a"])
    engine = TraversalEngine(cfg, "demo-proj", m)

    engine.emit_anchor_select("anything")
    result = engine.ingest_anchor_select(None)

    assert result["halt"] is True
    assert engine.halted is True
    assert engine.done is True


def test_broad_select_canary_mismatch_aborts(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _concept(cfg, "concept-a")
    m = _tier0_map(concepts=["concept-a"])
    engine = TraversalEngine(cfg, "demo-proj", m)

    tasks_doc = engine.emit_anchor_select("anything")
    real_task = next(t for t in tasks_doc["tasks"] if t.get("kind") == "anchor-select" and t["id"] not in engine._anchor_task_state[1]["canaries"])
    canary_task = next(t for t in tasks_doc["tasks"] if t["id"] in engine._anchor_task_state[1]["canaries"])
    bad_verdicts = {
        "verdicts": [
            {"id": real_task["id"], "verdict": "concept-a"},
            {"id": canary_task["id"], "verdict": "canary-quantum-decoherence-timescales"},
        ]
    }
    with pytest.raises(CanaryAbortError):
        engine.ingest_anchor_select(bad_verdicts)


# ---------------------------------------------------------------------------
# Intent-routing through a live hop
# ---------------------------------------------------------------------------

def test_hop_routes_only_contradicts_edges(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _finding(cfg, "demo-proj", "finding1", edges=[
        "- [f2](/findings/finding2.md) — nope",  # not a valid target grammar; ignored
    ])
    # anchor finding has BOTH a CONTRADICTS and a SUPPORTS outgoing edge
    _write(
        cfg.project_notes_dir("demo-proj") / "findings" / "finding1.md",
        note_type="findings", title="finding1", description="Anchor finding.",
        edges=[
            "- [Rival](/literature/rival2024.md) — CONTRADICTS: disputes the anchor's central claim.",
            "- [Foundation](/literature/foundation2020.md) — SUPPORTS: agrees with the anchor.",
        ],
    )
    _literature(cfg, "rival2024")
    _literature(cfg, "foundation2020")
    m = _tier0_map(findings=["finding1"])

    engine = TraversalEngine(cfg, "demo-proj", m)
    _run_anchor_select(engine, "what contradicts this finding?", selected=["finding1"])
    assert engine.route_matched is True
    assert engine.routed_tags == frozenset({"CONTRADICTS"})

    tasks_doc, result = _run_one_hop(engine, keep={"rival2024", "foundation2020"})
    assert tasks_doc is not None
    offered_slugs = _real_prune_slugs(tasks_doc)
    # SUPPORTS-tagged neighbour never even offered to the prune judge --
    # intent-routing filters BEFORE the LLM call, not after.
    assert "rival2024" in offered_slugs
    assert "foundation2020" not in offered_slugs
    assert all(e["tag"] == "CONTRADICTS" for e in engine._edges_walked)


def test_hop_routes_grounding_family_edges(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write(
        cfg.concepts_root / "concept-a.md",
        note_type="concepts", title="concept-a", description="Anchor concept.",
        edges=[
            "- [Paper](okf:literature/basis2019.md) — GROUNDED-IN: this concept grounds in the paper.",
            "- [Other](okf:literature/other2019.md) — SUPPORTS: agrees with a claim.",
        ],
    )
    _literature(cfg, "basis2019")
    _literature(cfg, "other2019")
    m = _tier0_map(concepts=["concept-a"])

    engine = TraversalEngine(cfg, "demo-proj", m)
    _run_anchor_select(engine, "what is this grounded in?", selected=["concept-a"])
    assert engine.routed_tags == frozenset({"GROUNDED-IN", "USES", "DERIVED-FROM"})

    tasks_doc, result = _run_one_hop(engine, keep={"basis2019", "other2019"})
    offered_slugs = _real_prune_slugs(tasks_doc)
    assert offered_slugs == {"basis2019"}


# ---------------------------------------------------------------------------
# Visited-set — no node pulled twice
# ---------------------------------------------------------------------------

def test_diamond_shared_neighbour_offered_and_visited_once(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write(
        cfg.project_notes_dir("demo-proj") / "findings" / "finding-a.md",
        note_type="findings", title="finding-a", description="Anchor A.",
        edges=["- [Shared](/literature/shared2021.md) — SUPPORTS: both anchors cite this."],
    )
    _write(
        cfg.project_notes_dir("demo-proj") / "findings" / "finding-b.md",
        note_type="findings", title="finding-b", description="Anchor B.",
        edges=["- [Shared](/literature/shared2021.md) — SUPPORTS: both anchors cite this."],
    )
    _literature(cfg, "shared2021")
    m = _tier0_map(findings=["finding-a", "finding-b"])

    engine = TraversalEngine(cfg, "demo-proj", m, width=5, depth=2)
    _run_anchor_select(engine, "tell me everything", selected=["finding-a", "finding-b"])

    tasks_doc, result = _run_one_hop(engine, keep={"shared2021"})
    offered = [t for t in tasks_doc["tasks"] if t.get("candidate_slug") == "shared2021"]
    assert len(offered) == 1  # offered to the judge exactly once, not twice

    shared_paths = [n for n in engine.visited.values() if n.slug == "shared2021"]
    assert len(shared_paths) == 1  # visited exactly once


# ---------------------------------------------------------------------------
# Backtrack on a dead-end
# ---------------------------------------------------------------------------

def test_backtrack_substitutes_a_kept_sibling_on_dead_end(tmp_path: Path):
    cfg = _cfg(tmp_path)
    # anchor has two outgoing edges, both KEPT by the judge, but width=1
    # so only ONE makes the beam. The primary pick is a dead end (no
    # outgoing edges of its own); the backtrack pool holds the other.
    _write(
        cfg.project_notes_dir("demo-proj") / "findings" / "finding1.md",
        note_type="findings", title="finding1", description="Anchor.",
        edges=[
            "- [Dead](/literature/deadend2020.md) — SUPPORTS: leads nowhere further.",
            "- [Live](/literature/alive2020.md) — SUPPORTS: leads somewhere further.",
        ],
    )
    _literature(cfg, "deadend2020")  # no outgoing edges -> dead end
    _write(
        cfg.literature_root / "alive2020.md",
        note_type="literature", title="alive2020", description="Has more edges.",
        edges=["- [Further](/literature/further2020.md) — SUPPORTS: keeps going."],
    )
    _literature(cfg, "further2020")
    m = _tier0_map(findings=["finding1"])

    engine = TraversalEngine(cfg, "demo-proj", m, width=1, depth=3)
    _run_anchor_select(engine, "tell me everything", selected=["finding1"])

    # Hop 1: both candidates offered, both KEPT, beam-width=1 caps to one
    # (deterministic: candidates are collected in edge order, "deadend2020"
    # first) -- "alive2020" becomes the backtrack pool.
    tasks_doc1, result1 = _run_one_hop(engine, keep={"deadend2020", "alive2020"})
    frontier_slugs_after_hop1 = {n.slug for n in engine.visited.values() if n.depth == 1}
    assert frontier_slugs_after_hop1 == {"deadend2020"}
    assert len(engine._backtrack_pool) == 1
    assert engine._backtrack_pool[0].slug == "alive2020"

    # Hop 2: deadend2020 has zero outgoing edges -> backtrack substitutes
    # alive2020 (already judge-approved) into the working set, and ITS
    # edge (to further2020) is what actually gets offered this hop.
    tasks_doc2, result2 = _run_one_hop(engine, keep={"further2020"})
    assert tasks_doc2 is not None
    offered = _real_prune_slugs(tasks_doc2)
    assert offered == {"further2020"}
    assert any(n.slug == "alive2020" for n in engine.visited.values())
    assert any(e.get("backtrack") for e in engine._edges_walked)


# ---------------------------------------------------------------------------
# Width / depth bounds
# ---------------------------------------------------------------------------

def test_beam_width_bound_respected_on_a_wide_fixture(tmp_path: Path):
    cfg = _cfg(tmp_path)
    n_neighbours = 10
    edges = [
        f"- [P{i}](/literature/wide{i}.md) — SUPPORTS: neighbour {i}."
        for i in range(n_neighbours)
    ]
    _write(
        cfg.project_notes_dir("demo-proj") / "findings" / "finding1.md",
        note_type="findings", title="finding1", description="Wide anchor.",
        edges=edges,
    )
    for i in range(n_neighbours):
        _literature(cfg, f"wide{i}")
    m = _tier0_map(findings=["finding1"])

    engine = TraversalEngine(cfg, "demo-proj", m)  # default BEAM_WIDTH
    _run_anchor_select(engine, "tell me everything", selected=["finding1"])
    keep_all = {f"wide{i}" for i in range(n_neighbours)}
    tasks_doc, result = _run_one_hop(engine, keep=keep_all)

    frontier_after_hop1 = [n for n in engine.visited.values() if n.depth == 1]
    assert len(frontier_after_hop1) == BEAM_WIDTH
    assert len(frontier_after_hop1) <= engine.width


def test_beam_depth_bound_respected_on_a_deep_chain(tmp_path: Path):
    cfg = _cfg(tmp_path)
    chain_len = BEAM_DEPTH + 3  # deliberately longer than the depth bound
    for i in range(chain_len):
        nxt = f"chain{i + 1}"
        edges = [f"- [Next](/literature/{nxt}.md) — SUPPORTS: continues the chain."] if i + 1 < chain_len else []
        _literature(cfg, f"chain{i}", edges=edges)
    _write(
        cfg.project_notes_dir("demo-proj") / "findings" / "finding1.md",
        note_type="findings", title="finding1", description="Head of a long chain.",
        edges=["- [Head](/literature/chain0.md) — SUPPORTS: enters the chain."],
    )
    m = _tier0_map(findings=["finding1"])

    engine = TraversalEngine(cfg, "demo-proj", m, width=1, depth=BEAM_DEPTH)
    _run_anchor_select(engine, "tell me everything", selected=["finding1"])

    hops_run = 0
    while not engine.done:
        tasks_doc = engine.emit_hop_prune()
        if tasks_doc is None:
            break
        keep_all_offered = _real_prune_slugs(tasks_doc)
        engine.ingest_hop_prune(_prune_verdicts_doc(tasks_doc, keep=keep_all_offered))
        hops_run += 1

    assert hops_run <= BEAM_DEPTH
    assert engine._hop_index <= BEAM_DEPTH


# ---------------------------------------------------------------------------
# Cross-layer reach: a paper not in Tier-0, reached via a concept edge
# ---------------------------------------------------------------------------

def test_reaches_paper_via_concept_edge_though_absent_from_tier0(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write(
        cfg.concepts_root / "concept-a.md",
        note_type="concepts", title="concept-a", description="Anchor concept.",
        edges=["- [Grounding paper](okf:literature/grounding2022.md) — GROUNDED-IN: this concept is grounded in this paper."],
    )
    _literature(cfg, "grounding2022")
    m = _tier0_map(concepts=["concept-a"])  # literature never enumerated in Tier-0

    engine = TraversalEngine(cfg, "demo-proj", m)
    _run_anchor_select(engine, "what is this grounded in?", selected=["concept-a"])
    tasks_doc, result = _run_one_hop(engine, keep={"grounding2022"})

    assert any(n.slug == "grounding2022" and n.okf_type == "literature" for n in engine.visited.values())


# ---------------------------------------------------------------------------
# Fail-closed: malformed prune verdict never silently drops a frontier node
# ---------------------------------------------------------------------------

def test_malformed_prune_verdict_defaults_to_keep(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write(
        cfg.project_notes_dir("demo-proj") / "findings" / "finding1.md",
        note_type="findings", title="finding1", description="Anchor.",
        edges=["- [Paper](/literature/garbled2020.md) — SUPPORTS: leads to a paper."],
    )
    _literature(cfg, "garbled2020")
    m = _tier0_map(findings=["finding1"])

    engine = TraversalEngine(cfg, "demo-proj", m)
    _run_anchor_select(engine, "tell me everything", selected=["finding1"])

    tasks_doc = engine.emit_hop_prune()
    real_task = next(t for t in tasks_doc["tasks"] if t.get("candidate_slug") == "garbled2020")
    verdicts_doc = _prune_verdicts_doc(tasks_doc, keep=set(), override={real_task["id"]: "not-a-real-verdict"})
    result = engine.ingest_hop_prune(verdicts_doc)

    assert result["halt"] is False
    assert any(n.slug == "garbled2020" for n in engine.visited.values())
    assert any("unrecognized" in w for w in result["warnings"])


def test_missing_prune_verdict_defaults_to_keep(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write(
        cfg.project_notes_dir("demo-proj") / "findings" / "finding1.md",
        note_type="findings", title="finding1", description="Anchor.",
        edges=["- [Paper](/literature/missingverdict2020.md) — SUPPORTS: leads to a paper."],
    )
    _literature(cfg, "missingverdict2020")
    m = _tier0_map(findings=["finding1"])

    engine = TraversalEngine(cfg, "demo-proj", m)
    _run_anchor_select(engine, "tell me everything", selected=["finding1"])

    tasks_doc = engine.emit_hop_prune()
    real_task = next(t for t in tasks_doc["tasks"] if t.get("candidate_slug") == "missingverdict2020")
    verdicts_doc = _prune_verdicts_doc(tasks_doc, keep=set(), override={real_task["id"]: None})
    result = engine.ingest_hop_prune(verdicts_doc)

    assert result["halt"] is False
    assert any(n.slug == "missingverdict2020" for n in engine.visited.values())
    assert any("missing" in w for w in result["warnings"])


def test_hop_prune_canary_mismatch_aborts(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write(
        cfg.project_notes_dir("demo-proj") / "findings" / "finding1.md",
        note_type="findings", title="finding1", description="Anchor.",
        edges=["- [Paper](/literature/anypaper2020.md) — SUPPORTS: leads to a paper."],
    )
    _literature(cfg, "anypaper2020")
    m = _tier0_map(findings=["finding1"])

    engine = TraversalEngine(cfg, "demo-proj", m)
    _run_anchor_select(engine, "tell me everything", selected=["finding1"])

    tasks_doc = engine.emit_hop_prune()
    verdicts_doc = _prune_verdicts_doc(tasks_doc, keep={"anypaper2020"})
    # flip the canaries: obviously-keep answered DROP, obviously-drop
    # answered KEEP.
    for v in verdicts_doc["verdicts"]:
        task = next(t for t in tasks_doc["tasks"] if t["id"] == v["id"])
        if task.get("candidate_slug") == "canary-obvious-keep":
            v["verdict"] = "DROP"
        elif task.get("candidate_slug") == "canary-obvious-drop":
            v["verdict"] = "KEEP"

    with pytest.raises(CanaryAbortError):
        engine.ingest_hop_prune(verdicts_doc)


def test_hop_prune_halts_when_fanout_never_ran(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write(
        cfg.project_notes_dir("demo-proj") / "findings" / "finding1.md",
        note_type="findings", title="finding1", description="Anchor.",
        edges=["- [Paper](/literature/anypaper2020.md) — SUPPORTS: leads to a paper."],
    )
    _literature(cfg, "anypaper2020")
    m = _tier0_map(findings=["finding1"])

    engine = TraversalEngine(cfg, "demo-proj", m)
    _run_anchor_select(engine, "tell me everything", selected=["finding1"])
    engine.emit_hop_prune()
    result = engine.ingest_hop_prune(None)

    assert result["halt"] is True
    assert engine.halted is True
    assert engine.done is True


# ---------------------------------------------------------------------------
# Batching — one round-trip per beam layer, not per candidate
# ---------------------------------------------------------------------------

def test_hop_prune_batches_whole_layer_into_one_emit(tmp_path: Path):
    cfg = _cfg(tmp_path)
    _write(
        cfg.project_notes_dir("demo-proj") / "findings" / "finding-a.md",
        note_type="findings", title="finding-a", description="Anchor A.",
        edges=["- [PA](/literature/papera2020.md) — SUPPORTS: from A."],
    )
    _write(
        cfg.project_notes_dir("demo-proj") / "findings" / "finding-b.md",
        note_type="findings", title="finding-b", description="Anchor B.",
        edges=["- [PB](/literature/paperb2020.md) — SUPPORTS: from B."],
    )
    _literature(cfg, "papera2020")
    _literature(cfg, "paperb2020")
    m = _tier0_map(findings=["finding-a", "finding-b"])

    engine = TraversalEngine(cfg, "demo-proj", m, width=5, depth=2)
    _run_anchor_select(engine, "tell me everything", selected=["finding-a", "finding-b"])

    tasks_doc = engine.emit_hop_prune()
    offered = _real_prune_slugs(tasks_doc)
    # BOTH frontier nodes' candidates land in the SAME tasks_doc -- one
    # emit for the whole layer, not one per source node.
    assert offered == {"papera2020", "paperb2020"}


# ---------------------------------------------------------------------------
# Pure ingest_hop_prune_verdicts — fail-closed default value
# ---------------------------------------------------------------------------

def test_pure_ingest_hop_prune_fail_closed_default_is_keep():
    tasks_doc = {
        "schema": "rv-judge-tasks/v1", "gate": "traversal-hop-prune",
        "tasks": [{"id": "t0001", "kind": "prune", "candidate_slug": "x"}],
    }
    result = ingest_hop_prune_verdicts(tasks_doc, {"canaries": {}}, {"verdicts": []})
    # zero verdicts + a non-empty real task set -> the "fan-out never ran" HALT,
    # not a per-id fill; assert that shape explicitly.
    assert result["halt"] is True
