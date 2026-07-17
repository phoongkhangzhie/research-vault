"""tests/test_router.py — the query-mode router: emit/ingest cold-agent-judge
fan-out for classifying a query into no-traversal / local / global.

All hermetic — no live LLM calls; verdicts docs are hand-authored fixtures
standing in for a completed hub fan-out (mirrors
``tests/test_manuscript_judge_fanout.py``'s discipline for the
support-matcher gate).

Covers:
  1. emit_router_tasks: schema shape, canaries interleaved unmarked (no
     mode-token tell anywhere in a public task field), deterministic
     re-emit, empty-queries honest no-op.
  2. ingest_router_verdicts: id-join happy path, fixed vocab, fail-closed
     default to GLOBAL (never a cheap mode) for both missing and
     unrecognized verdicts.
  3. Halt: an entirely missing/empty verdicts file for a non-empty task
     set -> halt=True, never a fabricated route.
  4. Canary: a missing or mismatched canary verdict raises
     CanaryAbortError before any route is trusted.
  5. The shy boundary: the canary bank's own "substantive but map-adjacent"
     probe expects LOCAL, not NO-TRAVERSAL — proving the discipline is
     baked into the fan-out contract, not just prose in the rubric.
  6. render_map_view_for_router: truncation is visibly marked, never a
     silent cutoff.
"""
from __future__ import annotations

import json

import pytest

from research_vault.gates.judge_seam import CanaryAbortError
from research_vault.retrieval.router import (
    ROUTER_MODES,
    emit_router_tasks,
    ingest_router_verdicts,
    render_map_view_for_router,
)


def _sample_map_view() -> dict:
    return {
        "concept_index": [
            {"slug": "grounding-floor", "title": "Grounding floor", "description": "Every claim traces to a source."},
        ],
        "moc_index": [
            {"slug": "core-loop", "title": "Core loop", "description": "The map/route/walk pipeline."},
        ],
        "findings_gaps_index": [
            {"slug": "open-gap-coverage", "title": "Coverage gap", "description": "Some concepts lack an owning MOC.", "note_type": "gaps"},
        ],
    }


def _verdicts_doc(pairs: dict[str, str]) -> dict:
    return {"verdicts": [{"id": tid, "verdict": v} for tid, v in pairs.items()]}


# ===========================================================================
# emit_router_tasks
# ===========================================================================

class TestEmitRouterTasks:
    def test_emits_tasks_schema_shape(self):
        result = emit_router_tasks(
            ["What concepts does this corpus cover?", "What did the grounding-floor note conclude?"],
            _sample_map_view(),
        )
        tasks_doc = result["tasks_doc"]
        assert tasks_doc["schema"] == "rv-judge-tasks/v1"
        assert tasks_doc["gate"] == "query-router"
        assert tasks_doc["judge_kind"] == "cold"
        assert "created" in tasks_doc
        # The rubric ships with {QUERY}/{MAP_VIEW} slots — the hub fills
        # them per-task from each task's own fields (mirrors the
        # support-matcher rubric's {CLAIM}/{NOTE_CONTENT} slots).
        assert "rubric" in tasks_doc
        assert "{QUERY}" in tasks_doc["rubric"]
        assert "{MAP_VIEW}" in tasks_doc["rubric"]

        # 2 real queries + 3 interleaved canaries.
        n_tasks = len(tasks_doc["tasks"])
        assert n_tasks == 5
        for t in tasks_doc["tasks"]:
            assert set(t.keys()) >= {"id", "kind", "query", "map_view"}
            assert t["kind"] == "route"

        canary_key_doc = result["canary_key_doc"]
        assert canary_key_doc["schema"] == "rv-judge-canary-key/v1"
        assert len(canary_key_doc["canaries"]) == 3
        for expected in canary_key_doc["canaries"].values():
            assert expected in {"NO-TRAVERSAL", "LOCAL", "GLOBAL"}

    def test_no_tell_anywhere_in_public_tasks_doc(self):
        """No canary-identifying marker, and no mode token, may appear
        anywhere in a public task field — a cold judge must classify the
        query, not read the answer off the file. (Vocab words legitimately
        appear in the shared ``rubric`` field — scoped out below.)
        """
        result = emit_router_tasks(["What are the main findings across the corpus?"], _sample_map_view())
        tasks_doc = result["tasks_doc"]

        serialized_full = json.dumps(tasks_doc).lower()
        assert "canary" not in serialized_full

        serialized_tasks = json.dumps(tasks_doc["tasks"]).lower()
        for token in ("no-traversal", "local", "global"):
            assert token not in serialized_tasks, (
                f"mode token {token!r} leaked into a public task field — a "
                f"judge could read the expected verdict off the file"
            )

    def test_canary_ids_are_a_subset_of_task_ids(self):
        result = emit_router_tasks(["one query"], _sample_map_view())
        task_ids = {t["id"] for t in result["tasks_doc"]["tasks"]}
        canary_ids = set(result["canary_key_doc"]["canaries"].keys())
        assert canary_ids <= task_ids
        assert len(canary_ids) == 3

    def test_deterministic_reemit(self):
        r1 = emit_router_tasks(["q1", "q2", "q3"], _sample_map_view())
        r2 = emit_router_tasks(["q1", "q2", "q3"], _sample_map_view())
        # Everything except the timestamp must match byte-for-byte.
        d1 = dict(r1["tasks_doc"])
        d2 = dict(r2["tasks_doc"])
        d1.pop("created")
        d2.pop("created")
        assert d1 == d2
        assert r1["canary_key_doc"] == r2["canary_key_doc"]

    def test_empty_queries_is_honest_noop(self):
        result = emit_router_tasks([], _sample_map_view())
        assert result["tasks_doc"]["tasks"] == []
        assert result["canary_key_doc"]["canaries"] == {}

    def test_rubric_override_and_config_seam(self):
        result = emit_router_tasks(["q"], _sample_map_view(), rubric_override="CUSTOM RUBRIC")
        assert result["tasks_doc"]["rubric"] == "CUSTOM RUBRIC"


# ===========================================================================
# ingest_router_verdicts
# ===========================================================================

class TestIngestRouterVerdicts:
    def _emit_two(self):
        return emit_router_tasks(["what concepts exist?", "what did paper X conclude?"], _sample_map_view())

    def _real_ids(self, tasks_doc, canary_key_doc):
        canaries = set(canary_key_doc["canaries"].keys())
        return [t["id"] for t in tasks_doc["tasks"] if t["id"] not in canaries]

    def test_happy_path_id_join(self):
        emitted = self._emit_two()
        tasks_doc, canary_key_doc = emitted["tasks_doc"], emitted["canary_key_doc"]
        real_ids = self._real_ids(tasks_doc, canary_key_doc)

        pairs = dict(canary_key_doc["canaries"])  # canaries answered correctly
        pairs[real_ids[0]] = "NO-TRAVERSAL"
        pairs[real_ids[1]] = "LOCAL"
        verdicts_doc = _verdicts_doc(pairs)

        result = ingest_router_verdicts(tasks_doc, canary_key_doc, verdicts_doc)
        assert result["halt"] is False
        assert result["canary_aborted"] is False
        assert result["missing_ids"] == []
        assert result["unrecognized_ids"] == []
        modes_by_id = {r["id"]: r["mode"] for r in result["routes"]}
        assert modes_by_id[real_ids[0]] == "no-traversal"
        assert modes_by_id[real_ids[1]] == "local"
        for mode in modes_by_id.values():
            assert mode in ROUTER_MODES

    def test_fail_closed_missing_verdict_defaults_to_global(self):
        emitted = self._emit_two()
        tasks_doc, canary_key_doc = emitted["tasks_doc"], emitted["canary_key_doc"]
        real_ids = self._real_ids(tasks_doc, canary_key_doc)

        pairs = dict(canary_key_doc["canaries"])
        pairs[real_ids[0]] = "LOCAL"
        # real_ids[1] intentionally absent — simulates the fan-out dropping it.
        verdicts_doc = _verdicts_doc(pairs)

        result = ingest_router_verdicts(tasks_doc, canary_key_doc, verdicts_doc)
        assert result["halt"] is False
        assert real_ids[1] in result["missing_ids"]
        modes_by_id = {r["id"]: r["mode"] for r in result["routes"]}
        assert modes_by_id[real_ids[1]] == "global"
        assert any(real_ids[1] in e for e in result["errors"])

    def test_fail_closed_unrecognized_verdict_defaults_to_global(self):
        emitted = self._emit_two()
        tasks_doc, canary_key_doc = emitted["tasks_doc"], emitted["canary_key_doc"]
        real_ids = self._real_ids(tasks_doc, canary_key_doc)

        pairs = dict(canary_key_doc["canaries"])
        pairs[real_ids[0]] = "LOCAL"
        pairs[real_ids[1]] = "MAYBE-LOCAL-ISH"  # garbled / non-canonical
        verdicts_doc = _verdicts_doc(pairs)

        result = ingest_router_verdicts(tasks_doc, canary_key_doc, verdicts_doc)
        assert result["halt"] is False
        assert real_ids[1] in result["unrecognized_ids"]
        modes_by_id = {r["id"]: r["mode"] for r in result["routes"]}
        assert modes_by_id[real_ids[1]] == "global"

    def test_halt_when_verdicts_file_entirely_missing(self):
        emitted = self._emit_two()
        tasks_doc, canary_key_doc = emitted["tasks_doc"], emitted["canary_key_doc"]

        result = ingest_router_verdicts(tasks_doc, canary_key_doc, None)
        assert result["halt"] is True
        assert result["routes"] == []
        assert result["halt_reason"]
        assert any("HALT" in e for e in result["errors"])

    def test_halt_when_verdicts_file_present_but_empty(self):
        emitted = self._emit_two()
        tasks_doc, canary_key_doc = emitted["tasks_doc"], emitted["canary_key_doc"]

        result = ingest_router_verdicts(tasks_doc, canary_key_doc, {"verdicts": []})
        assert result["halt"] is True
        assert result["routes"] == []

    def test_canary_abort_on_missing_canary(self):
        emitted = self._emit_two()
        tasks_doc, canary_key_doc = emitted["tasks_doc"], emitted["canary_key_doc"]
        real_ids = self._real_ids(tasks_doc, canary_key_doc)

        canary_ids = list(canary_key_doc["canaries"].keys())
        pairs = {cid: exp for cid, exp in canary_key_doc["canaries"].items() if cid != canary_ids[0]}
        pairs[real_ids[0]] = "LOCAL"
        pairs[real_ids[1]] = "GLOBAL"
        verdicts_doc = _verdicts_doc(pairs)

        with pytest.raises(CanaryAbortError):
            ingest_router_verdicts(tasks_doc, canary_key_doc, verdicts_doc)

    def test_canary_abort_on_mismatched_canary(self):
        emitted = self._emit_two()
        tasks_doc, canary_key_doc = emitted["tasks_doc"], emitted["canary_key_doc"]
        real_ids = self._real_ids(tasks_doc, canary_key_doc)

        pairs = dict(canary_key_doc["canaries"])
        # flip every canary to a wrong-but-in-vocab verdict.
        for cid in pairs:
            pairs[cid] = "GLOBAL" if pairs[cid] != "GLOBAL" else "LOCAL"
        pairs[real_ids[0]] = "LOCAL"
        pairs[real_ids[1]] = "GLOBAL"
        verdicts_doc = _verdicts_doc(pairs)

        with pytest.raises(CanaryAbortError):
            ingest_router_verdicts(tasks_doc, canary_key_doc, verdicts_doc)

    def test_zero_task_is_honest_noop(self):
        emitted = emit_router_tasks([], _sample_map_view())
        result = ingest_router_verdicts(emitted["tasks_doc"], emitted["canary_key_doc"], None)
        assert result == {
            "routes": [], "errors": [], "warnings": [],
            "canary_aborted": False, "halt": False, "halt_reason": "",
            "missing_ids": [], "unrecognized_ids": [],
        }


# ===========================================================================
# The shy boundary — baked into the fan-out contract, not just the rubric
# ===========================================================================

class TestShyBoundary:
    def test_substantive_map_adjacent_canary_expects_local_not_no_traversal(self):
        """A query whose subject a map description happens to mention (but
        which asks about the SUBSTANCE of that concept's note, not the
        map's shape) must be classified LOCAL. If the router were biased
        toward NO-TRAVERSAL whenever the query overlaps map vocabulary,
        this canary would catch it: the canary bank's own expected verdict
        for the "substantive but map-adjacent" probe is LOCAL.
        """
        from research_vault.retrieval.router import _router_canary_bank

        bank = _router_canary_bank()
        local_probes = [expected for _task, expected in bank if expected == "LOCAL"]
        assert local_probes, "canary bank must include a LOCAL probe"
        # And no probe with substantive/content-seeking phrasing may be
        # keyed to NO-TRAVERSAL.
        for task, expected in bank:
            if "concept" in task["query"].lower() and "note said" in task["query"].lower():
                assert expected == "LOCAL"

    def test_fail_closed_default_is_the_widest_mode_never_a_cheap_one(self):
        from research_vault.retrieval.router import _ROUTER_FAIL_CLOSED_DEFAULT
        assert _ROUTER_FAIL_CLOSED_DEFAULT == "GLOBAL"


# ===========================================================================
# render_map_view_for_router
# ===========================================================================

class TestRenderMapViewForRouter:
    def test_renders_sections(self):
        rendered = render_map_view_for_router(_sample_map_view())
        assert "grounding-floor" in rendered
        assert "core-loop" in rendered
        assert "open-gap-coverage" in rendered

    def test_empty_map_view_is_honest(self):
        rendered = render_map_view_for_router({"concept_index": [], "moc_index": [], "findings_gaps_index": []})
        assert "(none)" in rendered

    def test_truncation_is_visibly_marked(self):
        huge = {
            "concept_index": [
                {"slug": f"c{i}", "title": f"Concept {i}", "description": "x" * 500}
                for i in range(50)
            ],
            "moc_index": [],
            "findings_gaps_index": [],
        }
        rendered = render_map_view_for_router(huge)
        assert "truncated" in rendered.lower()
