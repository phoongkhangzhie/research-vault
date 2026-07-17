# SPDX-License-Identifier: AGPL-3.0-or-later
"""test_stop.py — acceptance tests for retrieval/stop.py: the claim-driven
DRAFT -> CHECK -> HOP stop condition wrapped around ``TraversalEngine``.

rv unit tests mock the LLM fan-out (charter: harness-native judges are
never called directly from a unit test) — verdicts docs are hand-built
dicts, exactly the shape a cold subagent would write to
``_judge-verdicts.json``, and fed straight to ``ingest_*`` without ever
touching a real judge or the filesystem's fan-out directory.

Coverage:
  - DRAFT decomposes a bullet-list response into sub-claims.
  - CHECK marks a sub-claim COVERED on a SUPPORTS verdict, leaves it
    UNCOVERED on ABSENT/PARTIAL/CONTRADICTS.
  - HOP: an uncovered claim's own text drives one more reasoning-
    conditioned traversal hop (re-classified intent, not the original
    query's).
  - all four stop conditions fire, each with a dedicated test.
  - the abstention set carries the right claims + why-uncovered tags.
  - fail-closed: a malformed CHECK verdict defaults that claim UNCOVERED
    (mutation-checkable).
  - a missing verdict SET (fan-out never ran) HALTs, never a silent pass.
  - a bad CHECK canary aborts.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import Config, reset_config_cache
from research_vault.gates.judge_seam import CanaryAbortError
from research_vault.retrieval.stop import (
    MAX_SUB_CLAIMS,
    REASON_BUDGET_EXHAUSTED,
    REASON_NO_EDGE,
    REASON_SATURATION,
    CoverageLoop,
    SubClaim,
    _prime_engine_for_claim_hop,
    emit_check_tasks,
    emit_draft_task,
    ingest_check_verdicts,
    ingest_draft_verdicts,
)
from research_vault.retrieval.traverse import TraversalEngine


@pytest.fixture(autouse=True)
def _reset_cfg():
    reset_config_cache()
    yield
    reset_config_cache()


# ---------------------------------------------------------------------------
# Fixture builders — mirrors tests/test_traverse.py's conventions verbatim.
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
        f"## Result\n\nSome recorded content for {title}.\n\n"
        f"## Edges\n\n{edge_block}\n",
        encoding="utf-8",
    )
    return path


def _finding(cfg: Config, project: str, slug: str, *, description: str = "A finding.",
             edges: list[str] | None = None) -> Path:
    return _write(cfg.project_notes_dir(project) / "findings" / f"{slug}.md", note_type="findings",
                  title=slug, description=description, edges=edges or [])


def _literature(cfg: Config, citekey: str, *, description: str = "A paper.",
                 edges: list[str] | None = None) -> Path:
    return _write(cfg.literature_root / f"{citekey}.md", note_type="literature",
                  title=citekey, description=description, edges=edges or [])


def _tier0_map(*, findings: list[str] = ()) -> dict:
    return {
        "concept_index": [],
        "moc_index": [],
        "findings_gaps_index": [
            {"slug": s, "title": s, "description": f"Finding {s}.", "note_type": "findings"} for s in findings
        ],
    }


def _anchor_verdicts_doc(tasks_doc: dict, *, selected: list[str]) -> dict:
    real_task = next(t for t in tasks_doc["tasks"] if t.get("kind") == "anchor-select")
    canary_task = next(
        t for t in tasks_doc["tasks"]
        if t.get("kind") == "anchor-select" and t["id"] != real_task["id"]
    )
    verdicts = [
        {"id": real_task["id"], "verdict": ",".join(selected)},
        {"id": canary_task["id"], "verdict": "canary-bicycle-chain-lubrication"},
    ]
    return {"schema": "rv-judge-verdicts/v1", "verdicts": verdicts}


def _run_anchor_select(engine: TraversalEngine, query: str, *, selected: list[str]) -> dict:
    tasks_doc = engine.emit_anchor_select(query)
    verdicts_doc = _anchor_verdicts_doc(tasks_doc, selected=selected)
    return engine.ingest_anchor_select(verdicts_doc)


def _prune_verdicts_doc(tasks_doc: dict, *, keep: set[str] | None = None) -> dict:
    keep = keep or set()
    verdict_by_id: dict[str, str] = {}
    for t in tasks_doc["tasks"]:
        if t.get("candidate_slug") == "canary-obvious-keep":
            verdict_by_id[t["id"]] = "KEEP"
        elif t.get("candidate_slug") == "canary-obvious-drop":
            verdict_by_id[t["id"]] = "DROP"
        else:
            verdict_by_id[t["id"]] = "KEEP" if t.get("candidate_slug") in keep else "DROP"
    return {"schema": "rv-judge-verdicts/v1", "verdicts": [{"id": tid, "verdict": v} for tid, v in verdict_by_id.items()]}


def _run_one_hop(engine: TraversalEngine, *, keep: set[str] | None = None):
    tasks_doc = engine.emit_hop_prune()
    if tasks_doc is None:
        return None, None
    verdicts_doc = _prune_verdicts_doc(tasks_doc, keep=keep)
    result = engine.ingest_hop_prune(verdicts_doc)
    return tasks_doc, result


def _draft_verdicts_doc(tasks_doc: dict, claims: list[str]) -> dict:
    task = tasks_doc["tasks"][0]
    body = "\n".join(f"- {c}" for c in claims)
    return {"schema": "rv-judge-verdicts/v1", "verdicts": [{"id": task["id"], "verdict": body}]}


def _run_draft(loop: CoverageLoop, query: str, claims: list[str]) -> dict:
    tasks_doc = loop.emit_draft(query)
    verdicts_doc = _draft_verdicts_doc(tasks_doc, claims)
    return loop.ingest_draft(verdicts_doc)


def _check_verdicts_doc(tasks_doc: dict, canary_key_doc: dict, decide) -> dict:
    """``decide(task)->verdict`` for REAL tasks; canaries auto-answered
    correctly so the round is trustworthy unless a test wants otherwise."""
    canaries = canary_key_doc["canaries"]
    verdicts = []
    for t in tasks_doc["tasks"]:
        if t["id"] in canaries:
            verdicts.append({"id": t["id"], "verdict": canaries[t["id"]]})
        else:
            verdicts.append({"id": t["id"], "verdict": decide(t)})
    return {"schema": "rv-judge-verdicts/v1", "verdicts": verdicts}


def _run_check(loop: CoverageLoop, decide) -> dict | None:
    tasks_doc = loop.emit_check()
    if tasks_doc is None:
        return None
    verdicts_doc = _check_verdicts_doc(tasks_doc, loop._check_state["canary_key_doc"], decide)
    return loop.ingest_check(verdicts_doc)


def _run_hop(loop: CoverageLoop, *, keep: set[str] | None = None):
    tasks_doc = loop.emit_hop()
    if tasks_doc is None:
        return None
    verdicts_doc = _prune_verdicts_doc(tasks_doc, keep=keep)
    return loop.ingest_hop(verdicts_doc)


def _engine_with_one_anchor(cfg: Config, *, edges: list[str] | None = None) -> TraversalEngine:
    """A TraversalEngine parked at Phase-1-complete with exactly one
    visited anchor (``finding1``), whose outgoing edges are configurable —
    the common starting point for CHECK/HOP-focused tests."""
    _finding(cfg, "demo-proj", "finding1", edges=edges or [])
    m = _tier0_map(findings=["finding1"])
    engine = TraversalEngine(cfg, "demo-proj", m)
    _run_anchor_select(engine, "tell me about finding1", selected=["finding1"])
    return engine


# ---------------------------------------------------------------------------
# DRAFT
# ---------------------------------------------------------------------------

def test_draft_decomposes_bullet_response_into_sub_claims(tmp_path: Path):
    cfg = _cfg(tmp_path)
    engine = _engine_with_one_anchor(cfg)
    loop = CoverageLoop(engine)

    result = _run_draft(loop, "what does finding1 show?", [
        "The effect size is large.",
        "The result replicates across two datasets.",
    ])

    assert result["halt"] is False
    assert [sc.text for sc in loop.sub_claims] == [
        "The effect size is large.",
        "The result replicates across two datasets.",
    ]
    assert all(not sc.covered for sc in loop.sub_claims)


def test_draft_caps_sub_claims_and_surfaces_the_truncation(tmp_path: Path):
    cfg = _cfg(tmp_path)
    engine = _engine_with_one_anchor(cfg)
    loop = CoverageLoop(engine)

    many = [f"claim number {i}" for i in range(MAX_SUB_CLAIMS + 3)]
    result = _run_draft(loop, "q", many)

    assert len(loop.sub_claims) == MAX_SUB_CLAIMS
    assert any("capped" in w for w in result["warnings"])


def test_draft_missing_verdict_set_halts(tmp_path: Path):
    cfg = _cfg(tmp_path)
    engine = _engine_with_one_anchor(cfg)
    loop = CoverageLoop(engine)

    loop.emit_draft("q")
    result = loop.ingest_draft(None)

    assert result["halt"] is True
    assert loop.halted is True
    assert loop.done is True
    assert any("NOT a pass" in e for e in result["errors"])


def test_draft_no_parseable_bullets_flags_malformed(tmp_path: Path):
    cfg = _cfg(tmp_path)
    engine = _engine_with_one_anchor(cfg)
    loop = CoverageLoop(engine)

    tasks_doc = loop.emit_draft("q")
    task = tasks_doc["tasks"][0]
    verdicts_doc = {"verdicts": [{"id": task["id"], "verdict": "just a paragraph, no bullets"}]}
    result = loop.ingest_draft(verdicts_doc)

    assert loop.sub_claims == []
    assert loop.draft_malformed is True
    assert any("parseable" in w for w in result["warnings"])


# ---------------------------------------------------------------------------
# CHECK
# ---------------------------------------------------------------------------

def test_check_marks_covered_on_supports_verdict(tmp_path: Path):
    cfg = _cfg(tmp_path)
    engine = _engine_with_one_anchor(cfg)
    loop = CoverageLoop(engine)
    _run_draft(loop, "q", ["claim A"])

    result = _run_check(loop, decide=lambda t: "SUPPORTS")

    assert result["halt"] is False
    assert loop.sub_claims[0].covered is True
    assert loop.sub_claims[0].supporting_note is not None
    assert loop.done is True  # all-covered


@pytest.mark.parametrize("verdict", ["ABSENT", "PARTIAL", "CONTRADICTS"])
def test_check_leaves_uncovered_on_non_supports_verdict(tmp_path: Path, verdict: str):
    cfg = _cfg(tmp_path)
    engine = _engine_with_one_anchor(cfg)
    loop = CoverageLoop(engine)
    _run_draft(loop, "q", ["claim A"])

    _run_check(loop, decide=lambda t: verdict)

    assert loop.sub_claims[0].covered is False
    assert loop.done is False


def test_check_fail_closed_malformed_verdict_leaves_claim_uncovered(tmp_path: Path):
    """Mutation-checkable: a garbled verdict string must default UNCOVERED,
    never silently pass as SUPPORTS."""
    cfg = _cfg(tmp_path)
    engine = _engine_with_one_anchor(cfg)
    loop = CoverageLoop(engine)
    _run_draft(loop, "q", ["claim A"])

    _run_check(loop, decide=lambda t: "definitely yes, totally supported!!")

    assert loop.sub_claims[0].covered is False


def test_check_missing_verdict_set_halts(tmp_path: Path):
    cfg = _cfg(tmp_path)
    engine = _engine_with_one_anchor(cfg)
    loop = CoverageLoop(engine)
    _run_draft(loop, "q", ["claim A"])

    loop.emit_check()
    result = loop.ingest_check(None)

    assert result["halt"] is True
    assert loop.halted is True
    assert loop.done is True
    assert any("NOT a pass" in e for e in result["errors"])


def test_check_bad_canary_aborts(tmp_path: Path):
    cfg = _cfg(tmp_path)
    engine = _engine_with_one_anchor(cfg)
    loop = CoverageLoop(engine)
    _run_draft(loop, "q", ["claim A"])

    tasks_doc = loop.emit_check()
    # answer every task (including canaries) with ABSENT — wrong for the
    # SUPPORTS canary.
    verdicts_doc = {
        "verdicts": [{"id": t["id"], "verdict": "ABSENT"} for t in tasks_doc["tasks"]]
    }
    with pytest.raises(CanaryAbortError):
        loop.ingest_check(verdicts_doc)


def test_check_never_re_emits_an_already_checked_pair(tmp_path: Path):
    cfg = _cfg(tmp_path)
    engine = _engine_with_one_anchor(cfg)
    loop = CoverageLoop(engine)
    _run_draft(loop, "q", ["claim A"])

    _run_check(loop, decide=lambda t: "ABSENT")  # stays uncovered, pair now checked
    tasks_doc = loop.emit_check()
    # no fresh (claim, note) pairs left against the single visited note.
    assert tasks_doc is None


# ---------------------------------------------------------------------------
# HOP — reasoning-conditioned, not a generic next hop
# ---------------------------------------------------------------------------

def test_hop_routes_off_the_claim_text_not_the_original_query(tmp_path: Path):
    cfg = _cfg(tmp_path)
    engine = _engine_with_one_anchor(cfg, edges=[
        "- [Rival](/literature/rival2024.md) — CONTRADICTS: disputes the anchor's central claim.",
        "- [Foundation](/literature/foundation2020.md) — SUPPORTS: agrees with the anchor.",
    ])
    _literature(cfg, "rival2024")
    _literature(cfg, "foundation2020")
    loop = CoverageLoop(engine)
    # the ORIGINAL query has no intent signal (walks every tag); the
    # CLAIM explicitly asks for counter-evidence.
    _run_draft(loop, "tell me about finding1", ["what contradicts this?"])
    _run_check(loop, decide=lambda t: "ABSENT")

    tasks_doc = loop.emit_hop()

    assert tasks_doc is not None
    assert engine.routed_tags == frozenset({"CONTRADICTS"})
    offered = {t["candidate_slug"] for t in tasks_doc["tasks"] if not str(t.get("candidate_slug", "")).startswith("canary-")}
    assert offered == {"rival2024"}  # SUPPORTS-tagged neighbour filtered out


def test_hop_visits_a_new_node_toward_the_uncovered_claim(tmp_path: Path):
    cfg = _cfg(tmp_path)
    engine = _engine_with_one_anchor(cfg, edges=[
        "- [Basis](okf:literature/basis2019.md) — GROUNDED-IN: grounds the claim.",
    ])
    _literature(cfg, "basis2019")
    loop = CoverageLoop(engine)
    _run_draft(loop, "q", ["what is this grounded in?"])
    _run_check(loop, decide=lambda t: "ABSENT")

    before = set(engine.visited)
    _run_hop(loop, keep={"basis2019"})
    after = set(engine.visited)

    assert after - before == {str(cfg.literature_root / "basis2019.md")}
    assert loop.hops_spent == 1


class _StandInEngineMissingAttr:
    """A minimal stand-in that has every TraversalEngine attribute
    ``_prime_engine_for_claim_hop`` touches EXCEPT one — simulates a
    traverse.py rename/removal of that attribute (e.g. ``depth`` ->
    ``hop_budget``). Without the guard, writing to the missing attribute
    would silently create a phantom instance attribute instead of
    raising — this fixture is the mutation-proof for that failure mode.
    """

    def __init__(self, *, omit: str):
        for attr in ("depth", "done", "routed_tags", "route_matched", "_hop_index"):
            if attr != omit:
                setattr(self, attr, 0)
        if omit != "_frontier":
            self._frontier = ["a-non-empty-frontier-node"]


@pytest.mark.parametrize(
    "omit", ["depth", "done", "routed_tags", "route_matched", "_frontier", "_hop_index"],
)
def test_prime_engine_for_claim_hop_fails_loud_on_renamed_attribute(omit: str):
    """The regression pin for the review finding: a WRITE to a
    renamed/removed TraversalEngine attribute must raise, not silently
    create a phantom attribute and lose control of the hop budget.

    Without the guard in ``_prime_engine_for_claim_hop`` this test goes
    RED — with ``omit="depth"``, ``engine.depth = ...`` would silently
    succeed (a phantom write), the function returns True, and this test's
    ``pytest.raises(AssertionError)`` never fires.
    """
    stand_in = _StandInEngineMissingAttr(omit=omit)
    with pytest.raises(AssertionError, match=omit):
        _prime_engine_for_claim_hop(stand_in, "any claim text")


# ---------------------------------------------------------------------------
# Stop condition 1 — all sub-claims COVERED
# ---------------------------------------------------------------------------

def test_stop_all_covered(tmp_path: Path):
    cfg = _cfg(tmp_path)
    engine = _engine_with_one_anchor(cfg)
    loop = CoverageLoop(engine)
    _run_draft(loop, "q", ["claim A", "claim B"])

    _run_check(loop, decide=lambda t: "SUPPORTS")

    result = loop.result()
    assert loop.done is True
    assert result["stop_reason"] == "all-covered"
    assert result["abstention_set"] == []


# ---------------------------------------------------------------------------
# Stop condition 2 — budget exhausted
# ---------------------------------------------------------------------------

def test_stop_budget_exhausted(tmp_path: Path):
    cfg = _cfg(tmp_path)
    # a long chain so the beam never runs dry before the hop budget does.
    edges = ["- [N0](/literature/n0.md) — SUPPORTS: leads on."]
    engine = _engine_with_one_anchor(cfg, edges=edges)
    prev = "n0"
    for i in range(1, 8):
        nxt = f"n{i}"
        _literature(cfg, prev, edges=[f"- [Next](/literature/{nxt}.md) — SUPPORTS: leads on."])
        prev = nxt
    _literature(cfg, prev)  # terminal node, no further edges

    loop = CoverageLoop(engine, max_hops=2)
    _run_draft(loop, "q", ["an ever-uncovered claim"])
    _run_check(loop, decide=lambda t: "ABSENT")

    rounds = 0
    while not loop.done and rounds < 10:
        _run_hop(loop, keep={f"n{i}" for i in range(8)})
        if not loop.done:
            _run_check(loop, decide=lambda t: "ABSENT")
        rounds += 1

    result = loop.result()
    assert loop.hops_spent == 2  # never exceeds max_hops
    assert result["stop_reason"] == REASON_BUDGET_EXHAUSTED
    assert result["abstention_set"][0]["reason"] == REASON_BUDGET_EXHAUSTED


# ---------------------------------------------------------------------------
# Stop condition 3 — saturation
# ---------------------------------------------------------------------------

def test_stop_saturation_hop_adds_no_new_node(tmp_path: Path):
    cfg = _cfg(tmp_path)
    engine = _engine_with_one_anchor(cfg, edges=[
        "- [Basis](okf:literature/basis2019.md) — GROUNDED-IN: grounds the claim.",
    ])
    _literature(cfg, "basis2019")
    loop = CoverageLoop(engine)
    _run_draft(loop, "q", ["what is this grounded in?"])
    _run_check(loop, decide=lambda t: "ABSENT")

    # DROP the only candidate -> the hop completes but visits nothing new.
    result = _run_hop(loop, keep=set())

    assert loop.done is True
    final = loop.result()
    assert final["stop_reason"] == REASON_SATURATION
    assert final["abstention_set"][0]["reason"] == REASON_SATURATION


def test_stop_saturation_empty_frontier(tmp_path: Path):
    cfg = _cfg(tmp_path)
    engine = _engine_with_one_anchor(cfg, edges=[])
    loop = CoverageLoop(engine)
    _run_draft(loop, "q", ["an unreachable claim"])
    _run_check(loop, decide=lambda t: "ABSENT")

    # A genuinely empty frontier (distinct from "a frontier node with zero
    # outgoing edges", which is the uncovered-maps-to-no-edge case, tested
    # separately) — the traversal has nothing left to expand from at all.
    engine._frontier = []

    tasks_doc = loop.emit_hop()

    assert tasks_doc is None
    assert loop.done is True
    result = loop.result()
    assert result["stop_reason"] == REASON_SATURATION


# ---------------------------------------------------------------------------
# Stop condition 4 — uncovered-maps-to-no-edge (fail-closed structural signal)
# ---------------------------------------------------------------------------

def test_stop_uncovered_maps_to_no_edge(tmp_path: Path):
    cfg = _cfg(tmp_path)
    # anchor has a SUPPORTS edge only; the claim routes to CONTRADICTS,
    # which has zero candidate edges anywhere in the visited graph.
    engine = _engine_with_one_anchor(cfg, edges=[
        "- [Foundation](/literature/foundation2020.md) — SUPPORTS: agrees with the anchor.",
    ])
    _literature(cfg, "foundation2020")
    loop = CoverageLoop(engine)
    _run_draft(loop, "q", ["what contradicts this?"])
    _run_check(loop, decide=lambda t: "ABSENT")

    tasks_doc = loop.emit_hop()

    assert tasks_doc is None
    assert loop.abstentions["sc0001"].reason == REASON_NO_EDGE
    result = loop.result()
    assert result["abstention_set"][0]["reason"] == REASON_NO_EDGE
    assert "CONTRADICTS" in result["abstention_set"][0]["detail"]


# ---------------------------------------------------------------------------
# The abstention set as structured, load-bearing data
# ---------------------------------------------------------------------------

def test_abstention_set_carries_claim_text_and_reason(tmp_path: Path):
    cfg = _cfg(tmp_path)
    engine = _engine_with_one_anchor(cfg, edges=[])
    loop = CoverageLoop(engine)
    _run_draft(loop, "q", ["claim A", "claim B"])
    _run_check(loop, decide=lambda t: "ABSENT")
    engine._frontier = []  # genuinely empty -> saturation, abstains everything

    loop.emit_hop()

    result = loop.result()
    entries = {e["claim"]: e for e in result["abstention_set"]}
    assert set(entries) == {"claim A", "claim B"}
    for e in entries.values():
        assert e["reason"] == REASON_SATURATION
        assert e["claim_id"]
        assert e["detail"]


def test_full_loop_end_to_end_mixed_coverage(tmp_path: Path):
    """DRAFT -> CHECK -> HOP -> CHECK, one claim gets covered by a hop,
    the other structurally cannot be — end-to-end sanity over the whole
    stepwise protocol."""
    cfg = _cfg(tmp_path)
    engine = _engine_with_one_anchor(cfg, edges=[
        "- [Basis](okf:literature/basis2019.md) — GROUNDED-IN: grounds the claim.",
    ])
    _literature(cfg, "basis2019")
    loop = CoverageLoop(engine)

    _run_draft(loop, "q", ["what is this grounded in?", "what contradicts this?"])
    _run_check(loop, decide=lambda t: "ABSENT")
    assert loop.done is False

    # round 1: hops toward the FIRST uncovered claim (grounding) -> visits basis2019
    _run_hop(loop, keep={"basis2019"})
    _run_check(loop, decide=lambda t: (
        "SUPPORTS" if t["note_slug"] == "basis2019" and t["claim"] == "what is this grounded in?"
        else "ABSENT"
    ))
    assert loop.sub_claims[0].covered is True
    assert loop.done is False  # the contradicts claim is still open

    # round 2: hop toward the second (contradicts) claim -> no CONTRADICTS edge anywhere
    assert loop.emit_hop() is None  # the claim dead-ends, abstained individually
    assert loop.done is False       # but the loop itself keeps polling
    assert loop.emit_hop() is None  # next poll finds no uncovered claim left
    assert loop.done is True

    result = loop.result()
    assert result["sub_claims"][0]["covered"] is True
    assert result["abstention_set"][0]["claim"] == "what contradicts this?"
    assert result["abstention_set"][0]["reason"] == REASON_NO_EDGE
