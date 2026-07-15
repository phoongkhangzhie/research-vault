"""test_facet_remediation_section_e.py — Section E (thin-pole-as-finding)
+ B2 (cold-verify-before-land) from the search-primary lit-review redesign.

Pins:
  (a) still-thin-after-one-attempt + a recorded within-facet-query-append
      round for that pole -> PASS-with-gap (GO/GO-WITH-RESIDUE), never HALT.
  (b) still-thin with NO recorded round for that pole -> HALT (the teeth).
  (c) the resulting leaves-open gap passes gap_coverage_gate cleanly.
  (d) remediation adds are capped at the floor (min_hits_per_pole).
  (e) a FACET_REMEDIATE-blocked coverage-gate node re-evaluates on tick
      once the awaited response file exists — no manual redo.
  (+) B2: a facet-remediation add is cold-verified before it lands in
      _corpus.md — never appended straight off the mechanical screen.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.review import autonomy as auto
from research_vault.review import corpus_freeze as cf
from research_vault.review import facet_remediation as fremed
from research_vault.review import gap_scan
from research_vault.review import gap_coverage_gate as gcg
from research_vault.review import relevance as rel
from research_vault.sources.sweep import parse_angle_matrix

NESTED_PROTOCOL_BROAD = """---
type: review-protocol
question: "Do LLM personas drift over multi-turn conversation?"
inclusion: "LLM persona, multi-turn dialogue"
exclusion: "single-turn only"
seed_queries:
  by-temporal:
    thesis:
      - "cultural drift multi-turn LLM persona"
      - "homogenization LLM roleplay over turns"
      - "value shift long conversation agent"
    counter:
      - "persona stability multi-turn LLM"
      - "value persistence long-horizon dialogue agent"
sources: [semantic-scholar, arxiv]
counter-position: "persona-consistency literature"
---

# Protocol
"""


def _record_within_facet_deviation(deviations_path: Path, *, facet_key: str, now=None) -> None:
    auto.record_deviation(
        deviations_path,
        version=1,
        pre_criteria="abc123", post_criteria="abc123",
        removed=[], added=[],
        rationale="autonomous facet re-search remediation round",
        kind=auto.DEVIATION_KIND_WITHIN_FACET_QUERY_APPEND,
        facet_key=facet_key, new_queries=["a new query"],
        pre_query_matrix_hash="qh1", post_query_matrix_hash="qh2",
        now=now,
    )


class TestResolveFacetCoverageAntiGamingTeeth:
    """(a) + (b): the exhausted-budget branch autonomously PASSes-with-gap
    when a within-facet-query-append round for that pole is on record, and
    still HALTs when it is not (never fishable further, but also never
    trusted on a bare round-counter with no corroborating record)."""

    def test_exhausted_with_recorded_round_passes_with_gap(self, tmp_path) -> None:
        deviations_path = tmp_path / "_deviations.md"
        _record_within_facet_deviation(deviations_path, facet_key="by-a.counter")

        base = auto.DispositionResult(auto.GO, "clean")
        result = fremed.resolve_facet_coverage(
            base,
            {"declared": True, "thin_poles": ["by-a.counter"], "min_hits_per_pole": 3,
             "pole_counts": {"by-a.counter": 1}},
            remediation_state={"rounds_used": 1}, max_rounds=1,
            deviations_path=deviations_path,
        )
        assert result.disposition in (auto.GO, auto.GO_WITH_RESIDUE)
        assert result.disposition != auto.HALT_DECLARE
        assert result.evidence.get("sparse_pole_dispositions", {}).get("by-a.counter")

    def test_exhausted_with_no_recorded_round_halts(self, tmp_path) -> None:
        deviations_path = tmp_path / "_deviations.md"
        # No deviation ever recorded for this pole.
        base = auto.DispositionResult(auto.GO, "clean")
        result = fremed.resolve_facet_coverage(
            base,
            {"declared": True, "thin_poles": ["by-a.counter"], "min_hits_per_pole": 3,
             "pole_counts": {"by-a.counter": 1}},
            remediation_state={"rounds_used": 1}, max_rounds=1,
            deviations_path=deviations_path,
        )
        assert result.disposition == auto.HALT_DECLARE
        assert "never" in result.reason.lower() or "no" in result.reason.lower()

    def test_exhausted_with_round_recorded_for_a_DIFFERENT_pole_still_halts(self, tmp_path) -> None:
        deviations_path = tmp_path / "_deviations.md"
        _record_within_facet_deviation(deviations_path, facet_key="by-a.thesis")  # wrong pole

        base = auto.DispositionResult(auto.GO, "clean")
        result = fremed.resolve_facet_coverage(
            base,
            {"declared": True, "thin_poles": ["by-a.counter"], "min_hits_per_pole": 3,
             "pole_counts": {"by-a.counter": 1}},
            remediation_state={"rounds_used": 1}, max_rounds=1,
            deviations_path=deviations_path,
        )
        assert result.disposition == auto.HALT_DECLARE

    def test_backward_compat_no_deviations_path_still_halts_conservatively(self) -> None:
        """A caller that never opts into the teeth (deviations_path=None)
        gets the OLD, conservative HALT — never a silent behavior change
        for an existing caller that hasn't wired the new param."""
        base = auto.DispositionResult(auto.GO, "clean")
        result = fremed.resolve_facet_coverage(
            base,
            {"declared": True, "thin_poles": ["by-a.counter"], "min_hits_per_pole": 3},
            remediation_state={"rounds_used": 1}, max_rounds=1,
        )
        assert result.disposition == auto.HALT_DECLARE


class TestGapBindingLeavesOpenPassesGapCoverageGate:
    """(c): a sparse-pole gap bound with disposition: leaves-open +
    disposition_reason passes gap_coverage_gate.check_gap_coverage_gate
    cleanly — no ANSWERS edge required, never re-blocks."""

    def test_leaves_open_gap_note_passes_gate(self, tmp_path) -> None:
        pnd = tmp_path
        gaps_dir = pnd / "gaps"
        gaps_dir.mkdir()
        rec = gap_scan.GapRecord(
            type=gap_scan.GAP_TYPE_COVERAGE_VOID,
            anchor="reviews/scope-a/_search_hits",
            claim="facet 'by-a.counter'",
            why="facet surfaced 1 paper, below floor 3",
            status="open",
        )
        gid = "coverage-void-facet-by-a-counter"
        rec.disposition = "leaves-open"
        rec.disposition_reason = (
            "genuinely-sparse after one bounded remediation attempt; "
            "a within-facet-query-append round is on record"
        )
        gap_scan._write_gap_note(rec, gid, pnd)

        result = gcg.check_gap_coverage_gate(pnd)
        assert result["ok"] is True
        assert gid in result["leaves_open"]
        assert gid not in result["open_uncovered"]

    def test_default_status_open_gap_still_blocks(self, tmp_path) -> None:
        """Regression pin: a gap emitted WITHOUT the disposition (the old
        default shape) still correctly blocks — the fix is additive, not a
        loosening of the gate itself."""
        pnd = tmp_path
        gaps_dir = pnd / "gaps"
        gaps_dir.mkdir()
        rec = gap_scan.GapRecord(
            type=gap_scan.GAP_TYPE_COVERAGE_VOID,
            anchor="reviews/scope-a/_search_hits",
            claim="facet 'by-b.counter'",
            why="facet surfaced 0 papers, below floor 3",
            status="open",
        )
        gid = "coverage-void-facet-by-b-counter"
        gap_scan._write_gap_note(rec, gid, pnd)

        result = gcg.check_gap_coverage_gate(pnd)
        assert result["ok"] is False
        assert gid in result["open_uncovered"]


class _FakeHit:
    def __init__(self, title: str, abstract: str = "", authors=None, year=2024):
        self.title = title
        self.abstract = abstract
        self.authors = authors or []
        self.year = year


class TestRemediationCapAndColdVerifyBeforeLand:
    """(d) + B2: candidate adds are capped at the floor, and NEVER land in
    _corpus.md before a cold relevance-verify verdict confirms them."""

    def _setup(self, tmp_path):
        protocol = tmp_path / "_protocol.md"
        protocol.write_text(NESTED_PROTOCOL_BROAD, encoding="utf-8")
        corpus = tmp_path / "_corpus.md"
        corpus.write_text(
            "| annotation | citekey | title |\n|---|---|---|\n"
            "| [NEW] | alpha2024 | Alpha |\n",
            encoding="utf-8",
        )
        deviations = tmp_path / "_deviations.md"
        return protocol, corpus, deviations

    def _many_hits(self, n: int) -> list[_FakeHit]:
        return [
            _FakeHit(
                f"Persona Stability Study {i} Multi-Turn Dialogue",
                abstract="persona stability multi-turn dialogue drift study",
                authors=[f"Author{i}"],
                year=2025,
            )
            for i in range(n)
        ]

    def test_candidates_never_reach_corpus_before_cold_verify(self, tmp_path) -> None:
        protocol, corpus, deviations = self._setup(tmp_path)

        def fake_tool_op(op, **kwargs):
            return self._many_hits(2)

        meta: dict = {}
        result = fremed.run_facet_query_append_round(
            meta, pole="by-temporal.counter", new_queries=["persona rigidity long dialogue"],
            protocol_path=protocol, corpus_path=corpus, deviations_path=deviations,
            out_dir=tmp_path, min_hits_per_pole=3, tool_op_fn=fake_tool_op,
        )

        assert result["phase"] == "awaiting_cold_verify"
        # never appended yet
        assert "Persona Stability Study" not in corpus.read_text()
        assert not deviations.exists() or "within-facet-query-append" not in deviations.read_text()

        task_dir = fremed.facet_task_dir(tmp_path, "by-temporal.counter")
        verify_input_path = task_dir / fremed._REMEDIATION_VERIFY_INPUT_FILENAME
        assert verify_input_path.exists()

    def test_cap_at_floor_kills_the_flood(self, tmp_path) -> None:
        protocol, corpus, deviations = self._setup(tmp_path)

        def fake_tool_op(op, **kwargs):
            return self._many_hits(40)  # far above the floor of 3

        meta: dict = {}
        result = fremed.run_facet_query_append_round(
            meta, pole="by-temporal.counter", new_queries=["q"],
            protocol_path=protocol, corpus_path=corpus, deviations_path=deviations,
            out_dir=tmp_path, min_hits_per_pole=3, tool_op_fn=fake_tool_op,
        )
        assert result["phase"] == "awaiting_cold_verify"
        assert len(result["candidates"]) == 3  # capped at the floor
        assert len(result["capped"]) == 40 - 3

    def test_full_round_after_cold_verify_confirms_lands_in_corpus(self, tmp_path) -> None:
        protocol, corpus, deviations = self._setup(tmp_path)

        def fake_tool_op(op, **kwargs):
            return [self._many_hits(1)[0]]

        meta: dict = {}
        first = fremed.run_facet_query_append_round(
            meta, pole="by-temporal.counter", new_queries=["persona rigidity"],
            protocol_path=protocol, corpus_path=corpus, deviations_path=deviations,
            out_dir=tmp_path, min_hits_per_pole=3, tool_op_fn=fake_tool_op,
        )
        assert first["phase"] == "awaiting_cold_verify"
        task_dir = fremed.facet_task_dir(tmp_path, "by-temporal.counter")
        verify_input_path = task_dir / fremed._REMEDIATION_VERIFY_INPUT_FILENAME
        text = verify_input_path.read_text(encoding="utf-8")
        assert rel.CANARY_IN_SCOPE_CITEKEY in text
        assert rel.CANARY_OFF_DOMAIN_CITEKEY in text

        # write a verdict table confirming the one real candidate + the two canaries
        real_citekey = first["candidates"][0]
        verdict_path = task_dir / fremed._REMEDIATION_VERIFY_VERDICT_FILENAME
        lines = ["| Citekey | Verdict |", "|---|---|"]
        lines.append(f"| {real_citekey} | IN |")
        lines.append(f"| {rel.CANARY_IN_SCOPE_CITEKEY} | IN |")
        lines.append(f"| {rel.CANARY_OFF_DOMAIN_CITEKEY} | OFF_DOMAIN |")
        verdict_path.write_text("\n".join(lines), encoding="utf-8")

        second = fremed.run_facet_query_append_round(
            meta, pole="by-temporal.counter", new_queries=["persona rigidity"],
            protocol_path=protocol, corpus_path=corpus, deviations_path=deviations,
            out_dir=tmp_path, min_hits_per_pole=3, tool_op_fn=fake_tool_op,
        )
        assert second["phase"] == "applied"
        assert second["added"] == [real_citekey]
        assert real_citekey in corpus.read_text()
        assert "within-facet-query-append" in deviations.read_text()

    def test_cold_verify_rejects_a_mechanically_screened_in_candidate(self, tmp_path) -> None:
        """The cold verify is a SEPARATE gate from the mechanical screen —
        it can still reject something the token-overlap screen let through."""
        protocol, corpus, deviations = self._setup(tmp_path)

        def fake_tool_op(op, **kwargs):
            return [self._many_hits(1)[0]]

        meta: dict = {}
        first = fremed.run_facet_query_append_round(
            meta, pole="by-temporal.counter", new_queries=["persona rigidity"],
            protocol_path=protocol, corpus_path=corpus, deviations_path=deviations,
            out_dir=tmp_path, min_hits_per_pole=3, tool_op_fn=fake_tool_op,
        )
        task_dir = fremed.facet_task_dir(tmp_path, "by-temporal.counter")
        real_citekey = first["candidates"][0]
        verdict_path = task_dir / fremed._REMEDIATION_VERIFY_VERDICT_FILENAME
        lines = ["| Citekey | Verdict |", "|---|---|"]
        lines.append(f"| {real_citekey} | OFF_DOMAIN |")
        lines.append(f"| {rel.CANARY_IN_SCOPE_CITEKEY} | IN |")
        lines.append(f"| {rel.CANARY_OFF_DOMAIN_CITEKEY} | OFF_DOMAIN |")
        verdict_path.write_text("\n".join(lines), encoding="utf-8")

        second = fremed.run_facet_query_append_round(
            meta, pole="by-temporal.counter", new_queries=["persona rigidity"],
            protocol_path=protocol, corpus_path=corpus, deviations_path=deviations,
            out_dir=tmp_path, min_hits_per_pole=3, tool_op_fn=fake_tool_op,
        )
        assert second["phase"] == "applied"
        assert second["added"] == []
        assert real_citekey not in corpus.read_text()

    def test_canary_abort_never_lands_anything(self, tmp_path) -> None:
        protocol, corpus, deviations = self._setup(tmp_path)

        def fake_tool_op(op, **kwargs):
            return [self._many_hits(1)[0]]

        meta: dict = {}
        first = fremed.run_facet_query_append_round(
            meta, pole="by-temporal.counter", new_queries=["persona rigidity"],
            protocol_path=protocol, corpus_path=corpus, deviations_path=deviations,
            out_dir=tmp_path, min_hits_per_pole=3, tool_op_fn=fake_tool_op,
        )
        task_dir = fremed.facet_task_dir(tmp_path, "by-temporal.counter")
        real_citekey = first["candidates"][0]
        verdict_path = task_dir / fremed._REMEDIATION_VERIFY_VERDICT_FILENAME
        # canary misclassified -> the whole verify pass is untrustworthy
        lines = ["| Citekey | Verdict |", "|---|---|"]
        lines.append(f"| {real_citekey} | IN |")
        lines.append(f"| {rel.CANARY_IN_SCOPE_CITEKEY} | OFF_DOMAIN |")
        lines.append(f"| {rel.CANARY_OFF_DOMAIN_CITEKEY} | OFF_DOMAIN |")
        verdict_path.write_text("\n".join(lines), encoding="utf-8")

        second = fremed.run_facet_query_append_round(
            meta, pole="by-temporal.counter", new_queries=["persona rigidity"],
            protocol_path=protocol, corpus_path=corpus, deviations_path=deviations,
            out_dir=tmp_path, min_hits_per_pole=3, tool_op_fn=fake_tool_op,
        )
        assert second["phase"] == "canary_aborted"
        assert real_citekey not in corpus.read_text()


class TestTickReopensBlockedFacetRemediateGate:
    """(e): a coverage-gate node blocked awaiting a facet-remediation
    response (query-authoring, or cold-verify) re-evaluates automatically
    on `rv dag tick` once the response exists — no manual `redo`."""

    def test_awaiting_response_helper_reflects_both_phases(self, tmp_path) -> None:
        task_dir = tmp_path / "judge" / "facet-remediate" / "by-a-counter"
        # nothing emitted yet -> not "awaiting" (no task exists to await)
        assert fremed.facet_remediation_awaiting_response(task_dir) is False

        fremed.emit_facet_query_task(
            task_dir, pole="by-a.counter", existing_queries=["q1"],
            min_queries_needed=2, min_hits_per_pole=3, current_count=1,
        )
        assert fremed.facet_remediation_awaiting_response(task_dir) is True

        (task_dir / fremed._RESPONSE_FILENAME).write_text(
            "```queries\nnew query\n```\n", encoding="utf-8",
        )
        assert fremed.facet_remediation_awaiting_response(task_dir) is False

        # simulate the cold-verify phase being emitted
        (task_dir / fremed._REMEDIATION_VERIFY_INPUT_FILENAME).write_text("x", encoding="utf-8")
        assert fremed.facet_remediation_awaiting_response(task_dir) is True
        (task_dir / fremed._REMEDIATION_VERIFY_VERDICT_FILENAME).write_text("y", encoding="utf-8")
        assert fremed.facet_remediation_awaiting_response(task_dir) is False

    def test_reopen_blocked_gate_helper(self, tmp_path) -> None:
        """The narrowly-scoped tick auto-reopen: a coverage-gate node
        blocked + stamped with a task_dir reopens to 'pending' once that
        task_dir is no longer 'awaiting response' — every OTHER blocked
        reason (no stamp) is left untouched."""
        from research_vault.dag.store import RunState
        from research_vault.dag.verbs import _reopen_facet_remediate_blocked_gates

        task_dir = tmp_path / "judge" / "facet-remediate" / "by-a-counter"
        fremed.emit_facet_query_task(
            task_dir, pole="by-a.counter", existing_queries=[],
            min_queries_needed=2, min_hits_per_pole=3, current_count=0,
        )

        run_state = RunState(run_id="r1", manifest_path=str(tmp_path / "phase1-dag.json"))
        run_state.set_node_status("coverage-gate", "blocked", error="awaiting response")
        run_state.node_states["coverage-gate"]["facet_remediate_task_dir"] = str(task_dir)
        # an unrelated blocked node with no stamp — must NEVER be touched.
        run_state.set_node_status("gap-coverage-gate", "blocked", error="unrelated reason")

        # Still awaiting -> no reopen.
        assert fremed.facet_remediation_awaiting_response(task_dir) is True
        reopened = _reopen_facet_remediate_blocked_gates(run_state)
        assert reopened is False
        assert run_state.node_status("coverage-gate") == "blocked"
        assert run_state.node_status("gap-coverage-gate") == "blocked"

        # Response now exists -> reopens to 'pending', stamp cleared.
        (task_dir / fremed._RESPONSE_FILENAME).write_text(
            "```queries\nnew query\n```\n", encoding="utf-8",
        )
        reopened = _reopen_facet_remediate_blocked_gates(run_state)
        assert reopened is True
        assert run_state.node_status("coverage-gate") == "pending"
        assert "facet_remediate_task_dir" not in run_state.node_states["coverage-gate"]
        # the unrelated blocked node (no stamp) is untouched.
        assert run_state.node_status("gap-coverage-gate") == "blocked"
