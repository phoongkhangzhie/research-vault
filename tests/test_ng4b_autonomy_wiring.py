"""tests/test_ng4b_autonomy_wiring.py — NG-4b: wiring the autonomy engine
into a genuinely hands-off, self-advancing loop.

Design of record: docs/superpowers/specs/2026-07-08-next-gen-lit-review-loop-design.md
(§1, NG-4/5/6). This PR wires the NG-4/5/6 primitives (#182) so the loop
actually "kicks and walks away":

  1. Phase-transition auto-emission — coverage-gate / approve-framework GO
     auto-emits + auto-starts the next phase's DAG run in-process, instead
     of stranding at a human needing to hand-run `rv review expand` /
     `rv manuscript expand` + `rv dag run`.
  2. Live coverage-deviation BLOCK — the frozen corpus citekey-set is
     stamped the first time coverage-gate is evaluated; a later undeclared
     delta (vs `_deviations.md`) trips a direct HALT-DECLARE (never
     auto-revise — a silent corpus edit must surface to a human).
  3. Canary-abort hardening on approve-manuscript --auto — a support-matcher
     CanaryAbortError must classify as HALT-DECLARE, never REVISE (a
     canary-abort landing in `blocking` would be downgraded to REVISE by
     the generic gate-policy engine — the exact priority violation the
     ordering exists to prevent).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.review import autonomy as auto  # noqa: E402


# ===========================================================================
# 2. Live coverage-deviation BLOCK — review.autonomy unit level
# ===========================================================================

def _corpus_note(path: Path, citekeys: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(f"| [NEW] | {ck} | title-{ck} |" for ck in citekeys)
    path.write_text(
        "| annotation | citekey | title |\n|---|---|---|\n" + rows + "\n",
        encoding="utf-8",
    )


def _walk_info(*, stop_reason: str = "walk-complete:1-hops") -> dict:
    return {
        "exists": True, "stop_reason": stop_reason,
        "walk_complete": stop_reason.startswith("walk-complete:"),
    }


class TestCoverageGateDeviationCheck:
    def test_first_pass_stamps_frozen_baseline_and_proceeds(self, tmp_path: Path):
        meta: dict = {}
        corpus_path = tmp_path / "reviews" / "s1" / "_corpus.md"
        _corpus_note(corpus_path, ["paperA2024", "paperB2024"])
        deviations_path = tmp_path / "reviews" / "s1" / "_deviations.md"

        result = auto.classify_coverage_gate_with_deviation_check(
            meta, _walk_info(), corpus_path=corpus_path, deviations_path=deviations_path,
        )
        assert result.disposition == auto.GO
        assert meta["frozen_corpus_citekeys"] == sorted(["paperA2024", "paperB2024"])

    def test_frozen_run_no_delta_is_ok(self, tmp_path: Path):
        corpus_path = tmp_path / "reviews" / "s2" / "_corpus.md"
        _corpus_note(corpus_path, ["paperA2024", "paperB2024"])
        deviations_path = tmp_path / "reviews" / "s2" / "_deviations.md"
        meta = {"frozen_corpus_citekeys": ["paperA2024", "paperB2024"]}

        result = auto.classify_coverage_gate_with_deviation_check(
            meta, _walk_info(), corpus_path=corpus_path, deviations_path=deviations_path,
        )
        assert result.disposition == auto.GO

    def test_undeclared_removal_halts_never_revises(self, tmp_path: Path):
        """★ leak-plant: an undeclared corpus removal mid-run -> HALT."""
        corpus_path = tmp_path / "reviews" / "s3" / "_corpus.md"
        deviations_path = tmp_path / "reviews" / "s3" / "_deviations.md"
        # Frozen baseline had 2 papers; the corpus on disk now silently has 1
        # (paperB2024 was removed with no matching _deviations.md entry).
        _corpus_note(corpus_path, ["paperA2024"])
        meta = {"frozen_corpus_citekeys": ["paperA2024", "paperB2024"]}

        result = auto.classify_coverage_gate_with_deviation_check(
            meta, _walk_info(), corpus_path=corpus_path, deviations_path=deviations_path,
        )
        assert result.disposition == auto.HALT_DECLARE
        assert "undeclared" in result.reason.lower()
        assert "REVISE" not in result.disposition

    def test_declared_removal_proceeds(self, tmp_path: Path):
        corpus_path = tmp_path / "reviews" / "s4" / "_corpus.md"
        deviations_path = tmp_path / "reviews" / "s4" / "_deviations.md"
        _corpus_note(corpus_path, ["paperA2024"])
        meta = {"frozen_corpus_citekeys": ["paperA2024", "paperB2024"]}
        auto.record_deviation(
            deviations_path,
            version=2,
            pre_criteria="include X",
            post_criteria="include X, excluding duplicate paperB2024",
            removed=["paperB2024"],
            rationale="paperB2024 was a duplicate of paperA2024 discovered on re-read.",
        )

        result = auto.classify_coverage_gate_with_deviation_check(
            meta, _walk_info(), corpus_path=corpus_path, deviations_path=deviations_path,
        )
        assert result.disposition == auto.GO

    def test_undeclared_delta_short_circuits_before_walk_terminal_check(self, tmp_path: Path):
        """An undeclared deviation HALTs even when the walk-terminal record
        itself would have cleanly GO'd — the deviation check is a fail-closed
        gate in front of, not behind, the walk-terminal disposition."""
        corpus_path = tmp_path / "reviews" / "s5" / "_corpus.md"
        deviations_path = tmp_path / "reviews" / "s5" / "_deviations.md"
        _corpus_note(corpus_path, ["paperA2024", "paperC2024"])  # added + removed vs frozen
        meta = {"frozen_corpus_citekeys": ["paperA2024", "paperB2024"]}

        result = auto.classify_coverage_gate_with_deviation_check(
            meta, _walk_info(stop_reason="walk-complete:1-hops"), corpus_path=corpus_path,
            deviations_path=deviations_path,
        )
        assert result.disposition == auto.HALT_DECLARE


# ===========================================================================
# 3. Canary-abort hardening — evaluation_from_structural_payload
# ===========================================================================

class TestStructuralPayloadCanaryHardening:
    def test_canary_aborted_true_halts_never_revises(self):
        payload = {
            "ok": False,
            "blocking": ["[support-matcher] CANARY ABORT (HALT-DECLARE): judge blind to planted probe"],
            "signals": [],
            "not_run": [],
            "canary_aborted": True,
        }
        ev = auto.evaluation_from_structural_payload(payload)
        assert ev.canary_aborted is True
        result = auto.classify_disposition(ev)
        assert result.disposition == auto.HALT_DECLARE
        assert result.disposition != auto.REVISE

    def test_no_canary_abort_regular_block_still_revises(self):
        payload = {
            "ok": False,
            "blocking": ["[hermetic-bib] unresolved citekey foo2024"],
            "signals": [],
            "not_run": [],
            "canary_aborted": False,
        }
        ev = auto.evaluation_from_structural_payload(payload)
        assert ev.canary_aborted is False
        result = auto.classify_disposition(ev)
        assert result.disposition == auto.REVISE

    def test_missing_canary_aborted_key_defaults_false_backward_compat(self):
        payload = {"ok": True, "blocking": [], "signals": [], "not_run": []}
        ev = auto.evaluation_from_structural_payload(payload)
        assert ev.canary_aborted is False


# ===========================================================================
# 1. ★ End-to-end: the self-advancing runner
# ===========================================================================
#
# Drives a REAL `rv review new` Phase-1 DAG through `dag run`/`dag tick`/
# `dag complete` — agent nodes are marked "succeeded" by hand (simulating a
# completed hub dispatch, the same convention every other DAG test in this
# suite uses; the runner cannot execute an agent node in-process — only
# `type: tool` nodes are auto-executed). The claim under test is: coverage-
# gate resolves WITHOUT any `--auto` flag being passed anywhere, and its GO
# auto-emits + auto-starts Phase-2 in the SAME `dag tick` call.

def _mark_succeeded(store, run_id: str, node_id: str) -> None:
    from research_vault.dag.verbs import cmd_complete
    rc = cmd_complete(argparse.Namespace(run_id=run_id, node_id=node_id, status="succeeded"))
    assert rc == 0, f"cmd_complete({node_id}) failed"


def _drive_through_relevance_verify(run_id: str, review_dir: Path, store, citekeys: list[str]) -> None:
    """review-relevance-verify-prep (TOOL, real op — deterministic, no
    network) auto-executes on the next tick, building
    _corpus_verify_input.md from the FINAL _corpus.md already written.
    review-relevance-verify (COLD agent) "completes": a canary-clean,
    all-IN verdict (PR-1, design 2026-07-10-trustworthy-curation-
    relevance-gate-design.md §3b) so coverage-gate can resolve exactly as
    before this feature."""
    from research_vault.dag.verbs import cmd_tick
    from research_vault.review.relevance import (
        CANARY_IN_SCOPE_CITEKEY, CANARY_OFF_DOMAIN_CITEKEY, IN, OFF_DOMAIN,
    )

    cmd_tick(argparse.Namespace(run_id=run_id))
    rs = store.load(run_id)
    assert rs.node_status("review-relevance-verify-prep") == "succeeded", (
        rs.node_states.get("review-relevance-verify-prep")
    )

    verdict_path = review_dir / "_relevance-verdict.md"
    lines = ["| Citekey | Verdict |", "|---|---|"]
    for ck in citekeys:
        lines.append(f"| {ck} | {IN} |")
    lines.append(f"| {CANARY_IN_SCOPE_CITEKEY} | {IN} |")
    lines.append(f"| {CANARY_OFF_DOMAIN_CITEKEY} | {OFF_DOMAIN} |")
    verdict_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _mark_succeeded(store, run_id, "review-relevance-verify")


class TestSelfAdvancingRunner:
    def _kick_review(self, tmp_instance: Path, cfg, scope: str = "scope-e2e"):
        from research_vault.review import cmd_new
        from research_vault.dag.verbs import cmd_run
        from research_vault.dag.store import RunStore

        note_path, review_dir, phase1 = cmd_new(
            "demo-research", scope, question="Does X generalize across Y?", config=cfg,
        )
        manifest_path = review_dir / "phase1-dag.json"
        rc = cmd_run(argparse.Namespace(manifest=str(manifest_path)))
        assert rc == 0
        run_id = phase1["run_id"]
        store = RunStore.from_config(cfg)
        return run_id, review_dir, store

    def _drive_to_coverage_gate(self, run_id: str, review_dir: Path, store, cfg, *, stop_reason: str, monkeypatch=None, deliverable: str | None = None):
        """review-scope -> approve-protocol -> review-search (tool) ->
        review-screen (agent) -> review-snowball (tool) -> review-curate
        (agent), landing coverage-gate as 'pending'/ready — the point where
        the self-advancing runner (not a human) must take over.

        review-loop-nodekind-drift-fix (Option C hybrid): review-search/
        review-snowball are now TOOL nodes — they auto-execute via the
        `sweep`/`snowball` ops on `dag tick`, never by hand-marking them
        succeeded. This test fakes the OP_REGISTRY entries (mirrors the
        established `test_dag_tool_node.py` seam) so no real network call
        happens; the fakes still WRITE their declared produces: artifacts
        (the new §4-D enforcement would otherwise BLOCK the node)."""
        from research_vault.dag.verbs import cmd_tick, cmd_approve
        from research_vault.review import autonomy as _auto

        assert monkeypatch is not None, "_drive_to_coverage_gate requires monkeypatch"

        # Register BOTH fake ops BEFORE approve-protocol — cmd_approve
        # internally recomputes the frontier (_recompute_awaiting_go), which
        # auto-executes any newly-ready tool node IN THE SAME CALL. Patching
        # the ops after cmd_approve would be too late and let the REAL
        # (network-touching) op fire once, unmocked.
        def _fake_sweep(*, out=None, **_kw):
            if out:
                Path(out).parent.mkdir(parents=True, exist_ok=True)
                Path(out).write_text("# fake search hits\n", encoding="utf-8")
                return str(out)
            return "fake sweep result"

        def _fake_snowball(*, out_dir=None, **_kw):
            out = Path(out_dir)
            out.mkdir(parents=True, exist_ok=True)
            (out / "_corpus_raw.md").write_text(
                "| [NEW] | alpha2024 | Alpha paper |\n| [NEW] | beta2024 | Beta paper |\n",
                encoding="utf-8",
            )
            (out / "_walk.md").write_text(
                f"---\nstop_reason: {stop_reason}\n---\n\nCitation-neighbor relevance walk.\n", encoding="utf-8",
            )
            return {"stop_reason": stop_reason}

        monkeypatch.setitem(_auto.OP_REGISTRY, "sweep", _fake_sweep)
        monkeypatch.setitem(_auto.OP_REGISTRY, "snowball", _fake_snowball)

        # review-scope "completes": writes _protocol.md with a counter-position
        # (L-2 gate requirement for approve-protocol). `deliverable` is left
        # unset by default here (-> the safe "review" default at
        # approve-review); callers that need the manuscript auto-chain pass
        # deliverable="manuscript" explicitly.
        protocol_path = review_dir / "_protocol.md"
        _protocol_fm = "---\ncounter-position: a real counter-position\n"
        if deliverable is not None:
            _protocol_fm += f"deliverable: {deliverable}\n"
        _protocol_fm += "---\n\nProtocol.\n"
        protocol_path.write_text(_protocol_fm, encoding="utf-8")
        _mark_succeeded(store, run_id, "review-scope")
        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("approve-protocol") == "awaiting-go"

        # ★ approve-protocol is the ONE retained human gate — it never
        # auto-resolves, proven by the assert above. A human approves it.
        # (review-search, a TOOL node, auto-executes in this SAME call via
        # the fake registered above.)
        rc = cmd_approve(argparse.Namespace(run_id=run_id, node_id="approve-protocol", note=None, output=[], reject=False, auto=False))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("review-search") == "succeeded"

        # review-screen (agent) "completes": accepts a seed frontier.
        screen_path = review_dir / "_screen.md"
        screen_path.write_text("10.1/alpha2024\n10.1/beta2024\n", encoding="utf-8")
        _mark_succeeded(store, run_id, "review-screen")

        # review-snowball (TOOL, op "snowball") auto-executes on the next tick.
        cmd_tick(argparse.Namespace(run_id=run_id))
        rs = store.load(run_id)
        assert rs.node_status("review-snowball") == "succeeded"

        # review-curate (agent) "completes": writes the FINAL _corpus.md
        # (+ _coverage-gaps.md on budget-termination).
        corpus_path = review_dir / "_corpus.md"
        corpus_path.write_text(
            "| annotation | citekey | title |\n|---|---|---|\n"
            "| [NEW] | alpha2024 | Alpha paper |\n| [NEW] | beta2024 | Beta paper |\n",
            encoding="utf-8",
        )
        if stop_reason.startswith("budget:"):
            (review_dir / "_coverage-gaps.md").write_text("open frontier\n", encoding="utf-8")
        cmd_tick(argparse.Namespace(run_id=run_id))
        _mark_succeeded(store, run_id, "review-curate")
        _drive_through_relevance_verify(run_id, review_dir, store, ["alpha2024", "beta2024"])

    def test_kick_walk_self_advances_and_auto_emits_phase2_on_go(self, tmp_instance: Path, monkeypatch):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        run_id, review_dir, store = self._kick_review(tmp_instance, cfg, scope="scope-go")
        self._drive_to_coverage_gate(run_id, review_dir, store, cfg, stop_reason="walk-complete:1-hops", monkeypatch=monkeypatch)

        # THE claim: a plain tick (no --auto anywhere) resolves coverage-gate.
        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("coverage-gate") == "succeeded"
        assert "GO" in rs.node_states["coverage-gate"]["decision_note"]
        assert rs.node_states["coverage-gate"]["approved_by"] == "review.autonomy"

        # Phase-2 was auto-emitted AND auto-started — no `rv review expand` /
        # `rv dag run` hand-run anywhere in this test.
        child_run_id = rs.node_states["coverage-gate"]["emitted_next_phase_run_id"]
        assert child_run_id == rs.meta["child_runs"]["coverage-gate"]
        assert (review_dir / "phase2-dag.json").exists()
        child_rs = store.load(child_run_id)
        assert child_rs.run_id.startswith("review-scope-go-phase2")
        # Phase-2's relate-<key> nodes are agent nodes -> already sitting as
        # a dispatch-ready frontier (self-advanced INTO phase 2, stopping only
        # because an agent node needs a real hub dispatch).
        assert any(
            nid.startswith("relate-") and child_rs.node_status(nid) == "pending"
            for nid in child_rs.node_states
        )

    def test_go_with_residue_still_proceeds_annotated(self, tmp_instance: Path, monkeypatch):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        run_id, review_dir, store = self._kick_review(tmp_instance, cfg, scope="scope-residue")
        self._drive_to_coverage_gate(run_id, review_dir, store, cfg, stop_reason="budget:200-calls", monkeypatch=monkeypatch)

        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("coverage-gate") == "succeeded"
        assert "GO-WITH-RESIDUE" in rs.node_states["coverage-gate"]["decision_note"]
        # Still proceeds — Phase-2 emitted exactly as the clean-GO case.
        assert "emitted_next_phase_run_id" in rs.node_states["coverage-gate"]

    def test_malformed_walk_terminal_halts_never_emits_phase2(self, tmp_instance: Path, monkeypatch):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        run_id, review_dir, store = self._kick_review(tmp_instance, cfg, scope="scope-halt")
        self._drive_to_coverage_gate(run_id, review_dir, store, cfg, stop_reason="garbage-not-a-real-reason", monkeypatch=monkeypatch)

        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        # HALT-DECLARE: blocked, never left sitting in awaiting-go, no phase-2.
        assert rs.node_status("coverage-gate") == "blocked"
        assert "HALT-DECLARE" in rs.node_states["coverage-gate"]["decision_note"]
        assert "emitted_next_phase_run_id" not in rs.node_states["coverage-gate"]
        assert not (review_dir / "phase2-dag.json").exists()

    def test_undeclared_deviation_between_two_coverage_gate_passes_halts(self, tmp_instance: Path, monkeypatch):
        """★ leak-plant, driven through the real dag-verbs code path (not
        just the review.autonomy unit level): simulate coverage-gate's meta
        already carrying a frozen baseline from a prior pass, then an
        UNDECLARED hand-edit removes a citekey before the (re-)evaluation."""
        from research_vault.config import load_config
        from research_vault.dag.verbs import _evaluate_autonomous_gate, _AUTONOMOUS_GATE_IDS
        from research_vault.dag.schema import nodes_by_id as manifest_nodes_by_id
        from research_vault.dag.store import RunState
        import research_vault.review.autonomy as auto

        cfg = load_config()
        run_id, review_dir, store = self._kick_review(tmp_instance, cfg, scope="scope-leak")
        self._drive_to_coverage_gate(run_id, review_dir, store, cfg, stop_reason="walk-complete:1-hops", monkeypatch=monkeypatch)

        from research_vault.dag.verbs import cmd_tick
        cmd_tick(argparse.Namespace(run_id=run_id))
        rs = store.load(run_id)
        assert rs.node_status("coverage-gate") == "succeeded"  # first pass: stamps baseline

        # Simulate a SECOND evaluation of the same gate (e.g. an operator
        # re-running `dag approve --auto` by hand after editing the corpus)
        # against a run_state whose meta already carries the frozen baseline
        # but whose _corpus.md was silently edited — no _deviations.md entry.
        manifest = __import__("json").loads((review_dir / "phase1-dag.json").read_text())
        nodes_lookup = manifest_nodes_by_id(manifest)
        corpus_path = review_dir / "_corpus.md"
        corpus_path.write_text(
            "| annotation | citekey | title |\n|---|---|---|\n"
            "| [NEW] | alpha2024 | Alpha paper |\n",  # beta2024 silently gone
            encoding="utf-8",
        )
        rs2 = RunState(run_id="probe-" + run_id, manifest_path=str(review_dir / "phase1-dag.json"))
        rs2.meta["frozen_corpus_citekeys"] = rs.meta["frozen_corpus_citekeys"]
        assert "beta2024" in rs2.meta["frozen_corpus_citekeys"]

        disposition = _evaluate_autonomous_gate(
            "coverage-gate", nodes_lookup, review_dir / "phase1-dag.json", rs2,
        )
        assert disposition.disposition == auto.HALT_DECLARE
        assert "undeclared" in disposition.reason.lower()


# ===========================================================================
# 4b. auto-chain-review-manuscript — cross-loop auto-emission at approve-review
# ===========================================================================
#
# Drives coverage-gate's GO all the way through the (auto-emitted) review
# Phase-2 DAG — relate-<key> fan-out, review-synthesize, review-coverage-
# critic — to approve-review's own GO, and asserts approve-review auto-emits
# + auto-starts a NEW manuscript tree (cross-loop, not a same-tree Phase-2).

class TestApproveReviewAutoChainsToManuscript(TestSelfAdvancingRunner):
    def _drive_phase2_to_approve_review(self, child_run_id: str, review_dir: Path, store, *, critic_verdict: str | None = "PASS"):
        """review Phase-2: relate-<key> (agent, parallel) -> review-synthesize
        (agent) -> review-coverage-critic (agent, writes structured
        `verdict:`) -> approve-review (autonomous). Returns the loaded
        RunState after ticking past review-coverage-critic (approve-review
        sitting ready for the caller's own tick, so callers can assert on
        the SAME tick that resolves it)."""
        from research_vault.dag.verbs import cmd_tick
        from research_vault.config import load_config

        rs = store.load(child_run_id)
        relate_ids = [nid for nid in rs.node_states if nid.startswith("relate-")]
        assert relate_ids, "expected at least one relate-<key> node in Phase-2"

        # the overlay unwind (0.3.2): a relate-<key> node's `produces:
        # {"note": "literature/<key>.md"}` resolves against cfg.literature_root
        # (shared-canonical), never project_notes_dir/literature.
        cfg = load_config()
        for nid in relate_ids:
            citekey = nid[len("relate-"):]
            lit_path = cfg.literature_root / f"{citekey}.md"
            lit_path.parent.mkdir(parents=True, exist_ok=True)
            lit_path.write_text(
                "---\n"
                "type: literature\n"
                "contribution_kind: application\n"
                "role: empirical\n"
                f"position: {citekey} bears on the review question via a "
                "direct empirical contribution, considered in full.\n"
                "result_reported: no\n"
                "paper_relations_sought: no\n"
                "---\n"
                f"Distilled {citekey}.\n",
                encoding="utf-8",
            )
            _mark_succeeded(store, child_run_id, nid)

        cmd_tick(argparse.Namespace(run_id=child_run_id))
        rs = store.load(child_run_id)
        assert rs.node_status("review-synthesize") == "succeeded" or rs.node_status("review-synthesize") == "pending"
        if rs.node_status("review-synthesize") != "succeeded":
            _mark_succeeded(store, child_run_id, "review-synthesize")
            cmd_tick(argparse.Namespace(run_id=child_run_id))
            rs = store.load(child_run_id)

        if critic_verdict is not None:
            critic_path = review_dir / "_coverage-critic.md"
            critic_path.write_text(
                f"---\nverdict: {critic_verdict}\n---\n\n"
                + ("Coverage looks saturated; counter-position present.\n" if critic_verdict == "PASS"
                   else "- protocol not adhered to\n"),
                encoding="utf-8",
            )
        # critic_verdict=None: deliberately do NOT write _coverage-critic.md
        # — the missing-artifact HALT-DECLARE path (check_coverage_critic_verdict's
        # not_run branch), a genuine HALT, distinct from a BLOCK verdict
        # (which classifies as REVISE/awaiting-go, not HALT — a critic BLOCK
        # is a fixable holes-found signal, not an integrity failure).
        _mark_succeeded(store, child_run_id, "review-coverage-critic")
        return store.load(child_run_id)

    def test_approve_review_go_auto_chains_new_manuscript_tree(self, tmp_instance: Path, monkeypatch):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick
        from research_vault.hashing import hash_file

        cfg = load_config()
        scope = "scope-chain"
        run_id, review_dir, store = self._kick_review(tmp_instance, cfg, scope=scope)
        self._drive_to_coverage_gate(run_id, review_dir, store, cfg, stop_reason="walk-complete:1-hops", monkeypatch=monkeypatch, deliverable="manuscript")

        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        child_run_id = rs.node_states["coverage-gate"]["emitted_next_phase_run_id"]

        self._drive_phase2_to_approve_review(child_run_id, review_dir, store)

        # THE claim: a plain tick resolves approve-review (no --auto anywhere).
        rc = cmd_tick(argparse.Namespace(run_id=child_run_id))
        assert rc == 0
        child_rs = store.load(child_run_id)
        assert child_rs.node_status("approve-review") == "succeeded"
        assert "GO" in child_rs.node_states["approve-review"]["decision_note"]

        ms_run_id = child_rs.node_states["approve-review"]["emitted_next_phase_run_id"]
        assert ms_run_id == child_rs.meta["child_runs"]["approve-review"]
        assert ms_run_id == f"manuscript-{scope}-phase1"

        project_notes_dir = review_dir.parent.parent
        tree_root = project_notes_dir / "manuscripts" / scope
        manuscript_note = tree_root / "_manuscript.md"
        assert manuscript_note.exists()
        text = manuscript_note.read_text(encoding="utf-8")
        assert "manuscript_type: lit-review" in text

        phase1_path = tree_root / "phase1-dag.json"
        assert phase1_path.exists()
        phase1_manifest = json.loads(phase1_path.read_text(encoding="utf-8"))
        scope_node = next(n for n in phase1_manifest["nodes"] if n["id"] == "scope")
        expected_hash = hash_file(review_dir / "_corpus.md")
        assert f"CORPUS_HASH: {expected_hash}" in scope_node["spec"]

        # Emit-once: a second tick creates NO second child run.
        rc = cmd_tick(argparse.Namespace(run_id=child_run_id))
        assert rc == 0
        child_rs2 = store.load(child_run_id)
        assert child_rs2.meta["child_runs"]["approve-review"] == ms_run_id
        assert "phase_transition_error" not in child_rs2.node_states["approve-review"]

        # Chain continuity (framework-gate-autonomy design, option A,
        # 2026-07-09 — UPDATED from the pre-ensemble behavior): the
        # manuscript run self-advances scope -> N cold framework-lens
        # candidates -> framework-synthesize -> framework-critic, and NOW
        # flows THROUGH approve-framework (auto-GO on a critic-cleared,
        # machine-synthesized spine) all the way to approve-manuscript —
        # gated instead by the async-veto window that was the design's
        # intended passive backstop (see the grounding-contradiction note
        # in DEVLOG.md / the PR body: the async-veto/provisional primitives
        # were REMOVED same-day by the single-human-gate design, PR #201 —
        # this test asserts NO provisional/veto stamp anywhere, matching
        # that shipped, current architecture, not the stale design note).
        ms_store = store  # same RunStore backend
        _mark_succeeded(ms_store, ms_run_id, "scope")
        cmd_tick(argparse.Namespace(run_id=ms_run_id))
        ms_rs = ms_store.load(ms_run_id)
        lens_ids = [nid for nid in ms_rs.node_states if nid.startswith("framework-lens-")]
        assert lens_ids, "expected at least one framework-lens-<lens> node"

        ms_manifest = json.loads((tree_root / "phase1-dag.json").read_text(encoding="utf-8"))
        for lens_id in lens_ids:
            lens_key = lens_id[len("framework-lens-"):]
            (tree_root / f"_framework-candidate-{lens_key}.md").write_text(
                f"---\nlens: {lens_key}\nspine_shape: n-axis\nbranches:\n  - alpha\n  - beta\n---\n\n"
                f"Candidate for lens {lens_key}.\n",
                encoding="utf-8",
            )
            _mark_succeeded(ms_store, ms_run_id, lens_id)

        cmd_tick(argparse.Namespace(run_id=ms_run_id))
        ms_rs = ms_store.load(ms_run_id)
        assert ms_rs.node_status("framework-synthesize") in ("succeeded", "pending")
        if ms_rs.node_status("framework-synthesize") != "succeeded":
            # framework-synthesize commits the spine + framework_origin: machine.
            note_text = manuscript_note.read_text(encoding="utf-8")
            note_text = note_text.replace(
                "spine_shape: \n", "spine_shape: n-axis\n",
            ).replace(
                "branches: \n", "branches:\n  - alpha\n  - beta\n",
            )
            if "framework_origin" not in note_text:
                note_text = note_text.replace(
                    "---\n", "---\nframework_origin: machine\n", 1,
                )
            manuscript_note.write_text(note_text, encoding="utf-8")
            (tree_root / "_framework-decision.md").write_text(
                "Backbone selected; all N candidates recorded; grafts + "
                "rejection rationale documented.\n",
                encoding="utf-8",
            )
            _mark_succeeded(ms_store, ms_run_id, "framework-synthesize")
            cmd_tick(argparse.Namespace(run_id=ms_run_id))
            ms_rs = ms_store.load(ms_run_id)

        # PR-A: framework-synthesize also allocates the frozen corpus
        # (alpha2024/beta2024, from the chained review) to the committed spine
        # in _coverage-map.md — approve-framework now folds the
        # coverage-allocation gate most-severe-wins with the critic verdict, so
        # an unallocated corpus BLOCKs. This must exist BEFORE framework-critic
        # is marked succeeded: cmd_complete recomputes the frontier and
        # autonomously evaluates approve-framework in that same call.
        (tree_root / "_coverage-map.md").write_text(
            "---\ncoverage_map: true\n"
            "used:\n"
            "  - citekey: alpha2024\n    branch: alpha\n"
            "  - citekey: beta2024\n    branch: beta\n"
            "---\n\n## rationale\n\nboth papers synthesized in their branches.\n",
            encoding="utf-8",
        )

        assert ms_rs.node_status("framework-critic") in ("succeeded", "pending")
        if ms_rs.node_status("framework-critic") != "succeeded":
            critic_node = next(
                n for n in ms_manifest["nodes"] if n["id"] == "framework-critic"
            )
            canary_id = critic_node["canary_id"]
            (tree_root / "_framework-critique.md").write_text(
                f"---\nverdict: PASS\ncanary_id: {canary_id}\n---\n\nNo coherence defects found.\n",
                encoding="utf-8",
            )
            _mark_succeeded(ms_store, ms_run_id, "framework-critic")

        rc = cmd_tick(argparse.Namespace(run_id=ms_run_id))
        assert rc == 0
        ms_rs = ms_store.load(ms_run_id)
        assert ms_rs.node_status("approve-framework") == "succeeded"
        assert "GO" in ms_rs.node_states["approve-framework"]["decision_note"]

        # No provisional/veto bookkeeping anywhere (single-human-gate design).
        note_text_final = manuscript_note.read_text(encoding="utf-8")
        assert "provisional" not in note_text_final
        assert "provisional" not in (tree_root / "_framework-decision.md").read_text(encoding="utf-8")

        ms2_run_id = ms_rs.node_states["approve-framework"]["emitted_next_phase_run_id"]
        assert ms2_run_id == ms_rs.meta["child_runs"]["approve-framework"]
        ms2_rs = ms_store.load(ms2_run_id)
        assert ms2_rs.node_status("outline") in ("succeeded", "pending")

    def test_approve_review_go_with_residue_still_chains(self, tmp_instance: Path, monkeypatch):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        scope = "scope-chain-residue"
        run_id, review_dir, store = self._kick_review(tmp_instance, cfg, scope=scope)
        self._drive_to_coverage_gate(run_id, review_dir, store, cfg, stop_reason="budget:200-calls", monkeypatch=monkeypatch, deliverable="manuscript")
        cmd_tick(argparse.Namespace(run_id=run_id))
        rs = store.load(run_id)
        child_run_id = rs.node_states["coverage-gate"]["emitted_next_phase_run_id"]

        self._drive_phase2_to_approve_review(child_run_id, review_dir, store, critic_verdict="PASS")
        cmd_tick(argparse.Namespace(run_id=child_run_id))
        child_rs = store.load(child_run_id)
        assert child_rs.node_status("approve-review") == "succeeded"
        assert "emitted_next_phase_run_id" in child_rs.node_states["approve-review"]

    def test_approve_review_halt_never_chains(self, tmp_instance: Path, monkeypatch):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        scope = "scope-chain-halt"
        run_id, review_dir, store = self._kick_review(tmp_instance, cfg, scope=scope)
        self._drive_to_coverage_gate(run_id, review_dir, store, cfg, stop_reason="walk-complete:1-hops", monkeypatch=monkeypatch)
        cmd_tick(argparse.Namespace(run_id=run_id))
        rs = store.load(run_id)
        child_run_id = rs.node_states["coverage-gate"]["emitted_next_phase_run_id"]

        self._drive_phase2_to_approve_review(child_run_id, review_dir, store, critic_verdict=None)
        cmd_tick(argparse.Namespace(run_id=child_run_id))
        child_rs = store.load(child_run_id)
        assert child_rs.node_status("approve-review") == "blocked"
        assert "emitted_next_phase_run_id" not in child_rs.node_states["approve-review"]

        project_notes_dir = review_dir.parent.parent
        tree_root = project_notes_dir / "manuscripts" / scope
        assert not tree_root.exists()

    def test_approve_review_revise_no_emit(self, tmp_instance: Path, monkeypatch):
        """F3 (#205 emission-review teeth gap): a REVISE disposition at
        approve-review (review-coverage-critic verdict BLOCK, deterministic
        fixable, revise budget never exhausted for this structural-payload
        adapter -> classify_disposition's REVISE branch, not HALT-DECLARE)
        must leave the node 'awaiting-go' and emit NOTHING — no
        `emitted_next_phase_run_id`, no `child_runs` entry, no
        `manuscripts/<scope>/` folder at all. REVISE is a genuine stop-point
        distinct from both GO (chains) and HALT-DECLARE (blocked, tested by
        test_approve_review_halt_never_chains above) — this is the third,
        previously-untested disposition arm of the same `_emit_next_phase`
        gate."""
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        scope = "scope-chain-revise"
        run_id, review_dir, store = self._kick_review(tmp_instance, cfg, scope=scope)
        self._drive_to_coverage_gate(run_id, review_dir, store, cfg, stop_reason="walk-complete:1-hops", monkeypatch=monkeypatch)
        cmd_tick(argparse.Namespace(run_id=run_id))
        rs = store.load(run_id)
        child_run_id = rs.node_states["coverage-gate"]["emitted_next_phase_run_id"]

        # critic_verdict="BLOCK" -> a real ``- <reason>`` bullet is written
        # (see the parent helper), so `check_coverage_critic_verdict` returns
        # a non-empty `blocking` list -> classify_disposition's REVISE arm
        # (never HALT-DECLARE, since revise_budget_exhausted is never set on
        # this structural-payload path — matches
        # TestStructuralPayloadCanaryHardening.test_no_canary_abort_regular_block_still_revises
        # above, driven here through the real DAG runner instead of the unit
        # level).
        self._drive_phase2_to_approve_review(child_run_id, review_dir, store, critic_verdict="BLOCK")
        rc = cmd_tick(argparse.Namespace(run_id=child_run_id))
        assert rc == 0
        child_rs = store.load(child_run_id)
        assert child_rs.node_status("approve-review") == "awaiting-go"
        assert "REVISE" in child_rs.node_states["approve-review"]["decision_note"]

        assert "emitted_next_phase_run_id" not in child_rs.node_states["approve-review"]
        assert "approve-review" not in child_rs.meta.get("child_runs", {})

        project_notes_dir = review_dir.parent.parent
        tree_root = project_notes_dir / "manuscripts" / scope
        assert not tree_root.exists(), (
            "REVISE must emit NOTHING — no manuscripts/<scope>/ folder"
        )

    def test_approve_review_go_default_deliverable_is_terminal_no_manuscript(self, tmp_instance: Path, monkeypatch):
        """The DEFAULT: no `deliverable` field in _protocol.md -> GO resolves
        approve-review, but manuscript emission is TERMINAL — no child_runs
        entry, no manuscripts/<scope>/ tree. This is the review-only-by-
        default acceptance case."""
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        scope = "scope-deliv-default"
        run_id, review_dir, store = self._kick_review(tmp_instance, cfg, scope=scope)
        # deliverable=None (default) — no field written into _protocol.md.
        self._drive_to_coverage_gate(run_id, review_dir, store, cfg, stop_reason="walk-complete:1-hops", monkeypatch=monkeypatch)
        cmd_tick(argparse.Namespace(run_id=run_id))
        rs = store.load(run_id)
        child_run_id = rs.node_states["coverage-gate"]["emitted_next_phase_run_id"]

        self._drive_phase2_to_approve_review(child_run_id, review_dir, store, critic_verdict="PASS")
        cmd_tick(argparse.Namespace(run_id=child_run_id))
        child_rs = store.load(child_run_id)

        assert child_rs.node_status("approve-review") == "succeeded"
        assert "GO" in child_rs.node_states["approve-review"]["decision_note"]
        # No manuscript emission — the review-only terminal outcome.
        assert child_rs.node_states["approve-review"]["deliverable"] == "review"
        assert "emitted_next_phase_run_id" not in child_rs.node_states["approve-review"]
        assert "approve-review" not in child_rs.meta.get("child_runs", {})
        assert "review complete" in child_rs.node_states["approve-review"]["phase_transition_note"]

        project_notes_dir = review_dir.parent.parent
        tree_root = project_notes_dir / "manuscripts" / scope
        assert not tree_root.exists()

    def test_approve_review_go_explicit_review_deliverable_terminal(self, tmp_instance: Path, monkeypatch):
        """Explicit `deliverable: review` behaves identically to the default
        (absent) case — terminal, no manuscript."""
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        scope = "scope-deliv-explicit-review"
        run_id, review_dir, store = self._kick_review(tmp_instance, cfg, scope=scope)
        self._drive_to_coverage_gate(run_id, review_dir, store, cfg, stop_reason="walk-complete:1-hops", monkeypatch=monkeypatch, deliverable="review")
        cmd_tick(argparse.Namespace(run_id=run_id))
        rs = store.load(run_id)
        child_run_id = rs.node_states["coverage-gate"]["emitted_next_phase_run_id"]

        self._drive_phase2_to_approve_review(child_run_id, review_dir, store, critic_verdict="PASS")
        cmd_tick(argparse.Namespace(run_id=child_run_id))
        child_rs = store.load(child_run_id)

        assert child_rs.node_status("approve-review") == "succeeded"
        assert "emitted_next_phase_run_id" not in child_rs.node_states["approve-review"]
        assert "approve-review" not in child_rs.meta.get("child_runs", {})

        project_notes_dir = review_dir.parent.parent
        tree_root = project_notes_dir / "manuscripts" / scope
        assert not tree_root.exists()

    def test_approve_review_full_adopt_existing_scaffold(self, tmp_instance: Path, monkeypatch):
        """F4 (#205 emission-review teeth gap): the FULL adopt-existing-
        scaffold branch of `_emit_next_phase`'s approve-review arm —
        `manuscripts/<scope>/_manuscript.md` AND `phase1-dag.json` BOTH
        already exist at GO time (e.g. a prior partial-adopt already
        re-entered and completed Phase-1's own re-scaffold, or an operator
        pre-staged the tree by hand) — must ADOPT the existing manifest
        verbatim: start a DAG run against the pre-existing phase1-dag.json
        (recording `child_runs`/`emitted_next_phase_run_id` as normal), and
        must NEVER re-scaffold (`cmd_new`/`cmd_expand` never called again)
        or clobber the pre-existing note/manifest bytes. This is distinct
        from the PARTIAL-adopt case (note only, no phase1-dag.json — F2,
        already covered by
        TestF2PartialAdoptReentersFrameworkPipeline in
        test_framework_gate_autonomy.py, which re-enters Phase-1 by
        REBUILDING the manifest) — here the manifest already exists and
        must be read as-is, not rebuilt."""
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick
        from research_vault import manuscript as _ms

        cfg = load_config()
        scope = "scope-full-adopt"
        run_id, review_dir, store = self._kick_review(tmp_instance, cfg, scope=scope)
        self._drive_to_coverage_gate(run_id, review_dir, store, cfg, stop_reason="walk-complete:1-hops", monkeypatch=monkeypatch, deliverable="manuscript")
        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        child_run_id = rs.node_states["coverage-gate"]["emitted_next_phase_run_id"]

        # Drive Phase-2 up to (but NOT including) marking
        # review-coverage-critic succeeded — that specific `cmd_complete`
        # call is what auto-resolves approve-review IN PROCESS (`cmd_complete`
        # -> `_recompute_awaiting_go` -> `_emit_next_phase`, same call, no
        # separate tick needed). The pre-existing scaffold MUST be staged
        # before that call fires, or `_emit_next_phase` will already have
        # run (and this test would be probing a no-op, not the full-adopt
        # branch).
        rs2 = store.load(child_run_id)
        relate_ids = [nid for nid in rs2.node_states if nid.startswith("relate-")]
        assert relate_ids, "expected at least one relate-<key> node in Phase-2"
        # the overlay unwind (0.3.2): resolves against cfg.literature_root.
        for nid in relate_ids:
            citekey = nid[len("relate-"):]
            lit_path = cfg.literature_root / f"{citekey}.md"
            lit_path.parent.mkdir(parents=True, exist_ok=True)
            lit_path.write_text(
                "---\ntype: literature\ncontribution_kind: application\nrole: empirical\n"
                f"position: {citekey} bears on the review question via a direct empirical "
                "contribution, considered in full.\nresult_reported: no\n"
                "paper_relations_sought: no\n---\n"
                f"Distilled {citekey}.\n",
                encoding="utf-8",
            )
            _mark_succeeded(store, child_run_id, nid)
        cmd_tick(argparse.Namespace(run_id=child_run_id))
        rs2 = store.load(child_run_id)
        if rs2.node_status("review-synthesize") != "succeeded":
            _mark_succeeded(store, child_run_id, "review-synthesize")
            cmd_tick(argparse.Namespace(run_id=child_run_id))
        critic_path = review_dir / "_coverage-critic.md"
        critic_path.write_text(
            "---\nverdict: PASS\n---\n\nCoverage looks saturated; counter-position present.\n",
            encoding="utf-8",
        )

        # Pre-stage a FULL scaffold at the target tree_root BEFORE the
        # review-coverage-critic completion below auto-resolves
        # approve-review — the frozen _corpus.md already exists (from
        # _drive_to_coverage_gate), so cmd_new's from_review lookup
        # succeeds cleanly (no "no frozen review corpus" warning).
        note_path, tree_root, phase1_manifest = _ms.cmd_new(
            "demo-research", ms_type_key="lit-review", from_review=scope, config=cfg,
        )
        assert phase1_manifest is not None
        phase1_path = tree_root / "phase1-dag.json"
        assert phase1_path.exists()
        pre_manifest_text = phase1_path.read_text(encoding="utf-8")
        pre_note_text = note_path.read_text(encoding="utf-8")

        def _explode(*_a, **_kw):
            raise AssertionError(
                "cmd_new/cmd_expand re-invoked despite a full pre-existing "
                "scaffold — the full-adopt branch must read the existing "
                "phase1-dag.json, never re-scaffold"
            )
        monkeypatch.setattr(_ms, "cmd_new", _explode)
        monkeypatch.setattr(_ms, "cmd_expand", _explode)

        # THE resolving call: marking review-coverage-critic succeeded
        # triggers cmd_complete -> _recompute_awaiting_go ->
        # _emit_next_phase, all in-process, right here.
        _mark_succeeded(store, child_run_id, "review-coverage-critic")

        rc = cmd_tick(argparse.Namespace(run_id=child_run_id))
        assert rc == 0
        child_rs = store.load(child_run_id)
        assert child_rs.node_status("approve-review") == "succeeded"
        assert "GO" in child_rs.node_states["approve-review"]["decision_note"]

        ms_run_id = child_rs.node_states["approve-review"]["emitted_next_phase_run_id"]
        assert ms_run_id == child_rs.meta["child_runs"]["approve-review"]
        # Adopted the PRE-EXISTING manifest's own run_id (not a freshly
        # derived one) — proof the existing file was read, not rebuilt.
        assert ms_run_id == phase1_manifest["run_id"]

        # No clobber: the pre-existing manifest + note are byte-identical.
        assert phase1_path.read_text(encoding="utf-8") == pre_manifest_text
        assert note_path.read_text(encoding="utf-8") == pre_note_text

        # The adopted run genuinely started (real frontier, not a stub).
        ms_rs = store.load(ms_run_id)
        assert ms_rs.node_status("scope") == "pending"


class TestDeliverableGateHasTeeth(TestApproveReviewAutoChainsToManuscript):
    """Mutation-test proof that the deliverable-gate early-return in
    `_emit_next_phase`'s approve-review arm is load-bearing — not vacuous
    against some other unrelated block. Neutralizing the read helper to
    always report "manuscript" makes the default-no-field case emit a
    manuscript tree (RED against the acceptance in
    `test_approve_review_go_default_deliverable_is_terminal_no_manuscript`),
    proving that test is actually sensitive to this gate."""

    def test_mutation_neutralize_deliverable_read_forces_manuscript_emission(self, tmp_instance: Path, monkeypatch):
        import research_vault.dag.verbs as verbs_mod
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        # Neutralize: pretend every protocol says "manuscript", regardless
        # of what's actually frozen in _protocol.md.
        monkeypatch.setattr(
            "research_vault.review.read_protocol_deliverable", lambda p: "manuscript"
        )

        cfg = load_config()
        scope = "scope-deliv-mutation"
        run_id, review_dir, store = self._kick_review(tmp_instance, cfg, scope=scope)
        # deliverable=None (default review) written into _protocol.md — but
        # the neutralized read always reports "manuscript".
        self._drive_to_coverage_gate(run_id, review_dir, store, cfg, stop_reason="walk-complete:1-hops", monkeypatch=monkeypatch)
        cmd_tick(argparse.Namespace(run_id=run_id))
        rs = store.load(run_id)
        child_run_id = rs.node_states["coverage-gate"]["emitted_next_phase_run_id"]

        self._drive_phase2_to_approve_review(child_run_id, review_dir, store, critic_verdict="PASS")
        cmd_tick(argparse.Namespace(run_id=child_run_id))
        child_rs = store.load(child_run_id)

        # With the gate neutralized, the default-review protocol now WRONGLY
        # emits a manuscript — confirms the real gate (not something else)
        # is what makes the default case terminal.
        assert "emitted_next_phase_run_id" in child_rs.node_states["approve-review"], (
            "with read_protocol_deliverable neutralized to always report "
            "'manuscript', the default (review) protocol must now WRONGLY "
            "emit — this proves the real gate is load-bearing"
        )
        project_notes_dir = review_dir.parent.parent
        tree_root = project_notes_dir / "manuscripts" / scope
        assert tree_root.exists()


# ===========================================================================
# 4c. F1 (#205 emission-review teeth gap) — the `child_runs` idempotency
# guard inside `_emit_next_phase` itself, exercised INDEPENDENTLY of
# `_recompute_awaiting_go`'s `if current != "pending": continue` status
# gate. Before this test, emit-once was PROVEN only by the status gate:
# neutering the in-function `child_runs` early-return left every existing
# test in this suite green (the status gate alone still prevents
# `_emit_next_phase` from ever being called a second time through the
# normal tick path). This test calls `_emit_next_phase` directly a second
# time with `child_runs[node_id]` already recorded — the exact situation
# the in-function guard (not the status gate) is responsible for handling
# (e.g. a stale re-tick / re-entrant call after a crash/restart, or any
# future caller of `_emit_next_phase` that does not route through
# `_recompute_awaiting_go`'s status check).
# ===========================================================================

class TestChildRunsGuardIndependentOfStatusGate:
    def test_second_emit_call_is_pure_noop_when_child_runs_already_set(
        self, tmp_instance: Path, monkeypatch,
    ):
        from research_vault.config import load_config
        from research_vault.dag.store import RunState, RunStore
        from research_vault.dag.verbs import _emit_next_phase
        from research_vault import manuscript as _ms

        cfg = load_config()
        scope_id = "scope-guard"

        parent_manifest = {
            "run_id": f"review-{scope_id}-phase2",
            "project": "demo-research",
            "name": "parent",
            "global_cap": 1,
            "nodes": [{"id": "approve-review", "type": "human-go", "needs": []}],
        }
        store = RunStore.from_config(cfg)
        parent_manifest_path = (
            cfg.project_notes_dir("demo-research") / "reviews" / scope_id / "phase2-dag.json"
        )
        parent_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        parent_manifest_path.write_text(json.dumps(parent_manifest), encoding="utf-8")
        run_state = RunState(
            run_id=parent_manifest["run_id"], manifest_path=str(parent_manifest_path),
        )
        run_state.init_nodes(parent_manifest)
        # Simulate: a PRIOR call already emitted + started a child for this
        # node — the exact state the in-function guard must recognize.
        run_state.meta.setdefault("child_runs", {})["approve-review"] = "manuscript-scope-guard-phase1"
        store.create(run_state)

        # Belt: if the guard is bypassed, NOTHING re-scaffold-shaped should
        # be invoked. `cmd_new`/`cmd_expand` raising proves a neutered guard
        # would actually attempt work, not just silently no-op differently.
        def _explode(*_a, **_kw):
            raise AssertionError(
                "cmd_new/cmd_expand invoked on a SECOND _emit_next_phase call "
                "— the child_runs guard did not short-circuit"
            )
        monkeypatch.setattr(_ms, "cmd_new", _explode)
        monkeypatch.setattr(_ms, "cmd_expand", _explode)

        _emit_next_phase(
            "approve-review", parent_manifest, parent_manifest_path, run_state, store,
        )

        # Pure no-op: the pre-existing child_runs entry is untouched, no
        # node_state fields were written (neither a fresh
        # emitted_next_phase_run_id NOR a phase_transition_error — a
        # neutered guard that falls through into the try/except would set
        # phase_transition_error via the mocked cmd_new raising above).
        assert run_state.meta["child_runs"]["approve-review"] == "manuscript-scope-guard-phase1"
        assert "emitted_next_phase_run_id" not in run_state.node_states.get("approve-review", {})
        assert "phase_transition_error" not in run_state.node_states.get("approve-review", {})

        # Teeth confirmed manually (not committed as a source change, per
        # scope): commenting out the early-return
        # (`if run_state.meta.get("child_runs", {}).get(node_id): return`)
        # in `_emit_next_phase` turns this test RED — the mocked cmd_new
        # raises, is caught by the function's own `except Exception`, and
        # `phase_transition_error` appears in node_states, failing the
        # last assertion above. The existing
        # `test_second_tick_creates_no_second_child_run`-shaped assertions
        # elsewhere in this suite (guarded only by the status gate) stay
        # green under the same mutation — proving this test covers a
        # DISTINCT protection.


# ===========================================================================
# 4b. Source-coverage fail-closed — `--auto` end-to-end wiring (F2 teeth
# followup, PR #206 review delta). Drives the REAL `_evaluate_autonomous_gate`
# coverage-gate branch (reads `_search_hits.md` off
# `nodes_lookup["review-search"]`, builds `source_coverage_info`) through the
# self-advancing runner — no unit-level shortcut. Neutering that wiring
# (e.g. dropping the `search_hits_path`/`source_coverage_info` lookup) must
# turn this RED.
# ===========================================================================

class TestCoverageGateSourceDarkAutoWiring(TestSelfAdvancingRunner):
    def _drive_to_coverage_gate_with_sources(
        self, run_id: str, review_dir: Path, store, cfg, *,
        stop_reason: str, declared_sources: list[str], dark_sources: list[str],
        monkeypatch,
    ) -> None:
        """Same shape as the parent's `_drive_to_coverage_gate`, but the fake
        `sweep` op writes a REAL `_search_hits.md` (with a `dark_sources:`
        frontmatter stamp) and the protocol declares a real `sources:` list
        — the two artifacts `check_source_coverage` actually reads. Without
        this, the parent's fake sweep only ever writes a placeholder
        `# fake search hits\n` with no `dark_sources:` field at all, which
        would never exercise the wiring under test."""
        from research_vault.dag.verbs import cmd_tick, cmd_approve
        from research_vault.review import autonomy as _auto

        def _fake_sweep(*, out=None, **_kw):
            if out:
                Path(out).parent.mkdir(parents=True, exist_ok=True)
                Path(out).write_text(
                    f"---\ndark_sources: {', '.join(dark_sources)}\n---\n\n# Search hits\n",
                    encoding="utf-8",
                )
                return str(out)
            return "fake sweep result"

        def _fake_snowball(*, out_dir=None, **_kw):
            out = Path(out_dir)
            out.mkdir(parents=True, exist_ok=True)
            (out / "_corpus_raw.md").write_text(
                "| [NEW] | alpha2024 | Alpha paper |\n| [NEW] | beta2024 | Beta paper |\n",
                encoding="utf-8",
            )
            (out / "_walk.md").write_text(
                f"---\nstop_reason: {stop_reason}\n---\n\nCitation-neighbor relevance walk.\n", encoding="utf-8",
            )
            return {"stop_reason": stop_reason}

        monkeypatch.setitem(_auto.OP_REGISTRY, "sweep", _fake_sweep)
        monkeypatch.setitem(_auto.OP_REGISTRY, "snowball", _fake_snowball)

        protocol_path = review_dir / "_protocol.md"
        protocol_path.write_text(
            "---\ncounter-position: a real counter-position\n"
            f"sources: [{', '.join(declared_sources)}]\n---\n\nProtocol.\n",
            encoding="utf-8",
        )
        _mark_succeeded(store, run_id, "review-scope")
        cmd_tick(argparse.Namespace(run_id=run_id))

        rc = cmd_approve(argparse.Namespace(run_id=run_id, node_id="approve-protocol", note=None, output=[], reject=False, auto=False))
        assert rc == 0

        screen_path = review_dir / "_screen.md"
        screen_path.write_text("10.1/alpha2024\n10.1/beta2024\n", encoding="utf-8")
        _mark_succeeded(store, run_id, "review-screen")
        cmd_tick(argparse.Namespace(run_id=run_id))

        corpus_path = review_dir / "_corpus.md"
        corpus_path.write_text(
            "| annotation | citekey | title |\n|---|---|---|\n"
            "| [NEW] | alpha2024 | Alpha paper |\n| [NEW] | beta2024 | Beta paper |\n",
            encoding="utf-8",
        )
        if stop_reason.startswith("budget:"):
            (review_dir / "_coverage-gaps.md").write_text("open frontier\n", encoding="utf-8")
        cmd_tick(argparse.Namespace(run_id=run_id))
        _mark_succeeded(store, run_id, "review-curate")
        _drive_through_relevance_verify(run_id, review_dir, store, ["alpha2024", "beta2024"])

    def test_declared_dark_source_halts_and_names_it(self, tmp_instance: Path, monkeypatch):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        run_id, review_dir, store = self._kick_review(tmp_instance, cfg, scope="scope-src-dark")
        self._drive_to_coverage_gate_with_sources(
            run_id, review_dir, store, cfg, stop_reason="walk-complete:1-hops",
            declared_sources=["semantic-scholar", "arxiv"], dark_sources=["arxiv"],
            monkeypatch=monkeypatch,
        )

        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("coverage-gate") == "blocked"
        assert "HALT" in rs.node_states["coverage-gate"]["decision_note"]
        assert "arxiv" in rs.node_states["coverage-gate"]["decision_note"]
        assert "emitted_next_phase_run_id" not in rs.node_states["coverage-gate"]

    def test_healthy_sources_go(self, tmp_instance: Path, monkeypatch):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        run_id, review_dir, store = self._kick_review(tmp_instance, cfg, scope="scope-src-healthy")
        self._drive_to_coverage_gate_with_sources(
            run_id, review_dir, store, cfg, stop_reason="walk-complete:1-hops",
            declared_sources=["semantic-scholar", "arxiv"], dark_sources=[],
            monkeypatch=monkeypatch,
        )

        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("coverage-gate") == "succeeded"
        assert "GO" in rs.node_states["coverage-gate"]["decision_note"]

    def test_dark_but_undeclared_source_still_goes(self, tmp_instance: Path, monkeypatch):
        """A source dark this sweep but NEVER named in the protocol's
        declared `sources:` list must not block — only DECLARED coverage is
        a promise the gate must keep."""
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        run_id, review_dir, store = self._kick_review(tmp_instance, cfg, scope="scope-src-undeclared")
        self._drive_to_coverage_gate_with_sources(
            run_id, review_dir, store, cfg, stop_reason="walk-complete:1-hops",
            declared_sources=["semantic-scholar", "arxiv"], dark_sources=["pubmed"],
            monkeypatch=monkeypatch,
        )

        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("coverage-gate") == "succeeded"
        assert "GO" in rs.node_states["coverage-gate"]["decision_note"]


# ===========================================================================
# 5. NG-6b (scoped) — the PRISMA deviation-ledger
# ===========================================================================
#
# Scope note (grounded correction, surfaced per charter §7): the brief scopes
# NG-6b as depending on NG-6a's `rv review refresh` verb (Wave C). That verb
# does not exist on main (grepped — no `cmd_refresh` anywhere in review/) —
# it is a genuine prerequisite gap, not yet landed despite the brief's
# premise. What NG-6a's item 2 (this PR, prior commit) DOES ship is the
# deviation log itself (`record_deviation`/`_deviations.md`) — enough to
# render the deviation ledger's DELTA (denominator change + citekeys +
# rationale) without needing the refresh verb's "materialize the remediation
# append into the frozen corpus" concern, which is a separate problem
# (staleness of `coverage_report`'s materialized/orphan counts, not the
# deviation ledger's own content). This test covers exactly that scoped
# piece — NOT the full NG-6a/NG-6b dependency chain.

class TestPrismaDeviationLedger:
    def test_no_deviations_file_renders_unchanged_ledger(self, tmp_path: Path):
        from research_vault.manuscript.types.lit_review import render_prisma_ledger

        coverage = {
            "counts": {"corpus": 2, "materialized": 2, "unmaterialized": 0, "orphan": 0},
            "corpus_citekeys": ["a2024", "b2024"],
            "unmaterialized": [], "orphan": [],
        }
        out = render_prisma_ledger(coverage, deviations_path=tmp_path / "_deviations.md")
        assert "Deviation" not in out
        assert "| Corpus (frozen citekeys) | 2 |" in out

    def test_deviation_block_renders_denominator_delta_and_reasons(self, tmp_path: Path):
        from research_vault.manuscript.types.lit_review import render_prisma_ledger
        from research_vault.review.autonomy import record_deviation

        deviations_path = tmp_path / "_deviations.md"
        record_deviation(
            deviations_path,
            version=2,
            pre_criteria="include X",
            post_criteria="include X excluding duplicates",
            removed=["b2024"],
            added=["c2024"],
            rationale="b2024 was a near-duplicate preprint of a2024.",
        )
        coverage = {
            "counts": {"corpus": 2, "materialized": 2, "unmaterialized": 0, "orphan": 0},
            "corpus_citekeys": ["a2024", "c2024"],
            "unmaterialized": [], "orphan": [],
        }
        out = render_prisma_ledger(coverage, deviations_path=deviations_path)
        assert "Deviation" in out
        assert "b2024" in out and "c2024" in out
        assert "near-duplicate preprint" in out
        # The reader sees the denominator changed, not just the final count.
        assert "→" in out or "->" in out

    def test_no_corpus_still_degrades_honestly_with_deviations_path(self, tmp_path: Path):
        from research_vault.manuscript.types.lit_review import render_prisma_ledger

        out = render_prisma_ledger({}, deviations_path=tmp_path / "_deviations.md")
        assert "No frozen corpus" in out


class TestBuildApprovePayloadPropagatesCanaryAborted:
    def test_live_judge_canary_abort_sets_top_level_flag(self, tmp_path: Path):
        from research_vault.manuscript.check_gates import build_approve_payload

        tree_root = tmp_path / "manuscripts" / "ms-canary"
        (tree_root / "sections").mkdir(parents=True, exist_ok=True)
        (tree_root / "sections" / "intro.md").write_text(
            "A finding. [[paper2024]]\n", encoding="utf-8",
        )
        project_notes_dir = tmp_path
        lit_dir = project_notes_dir / "literature"
        lit_dir.mkdir(parents=True, exist_ok=True)
        (lit_dir / "paper2024.md").write_text(
            "---\ntype: literature\n---\n## Result\nSome finding.\n", encoding="utf-8",
        )

        class _FakeType:
            key = "lit-review"
            equation_sources = ()

        # A judge_fn that always answers [ABSENT] is blind on the known-
        # supported canary probe -> check_support_tally aborts, canary_aborted=True.
        payload = build_approve_payload(
            tree_root, project_notes_dir, _FakeType(), judge_fn=lambda p: "[ABSENT]",
        )
        assert payload.get("canary_aborted") is True
        assert not payload["ok"]

    def test_no_canary_abort_flag_false(self, tmp_path: Path):
        from research_vault.manuscript.check_gates import build_approve_payload

        tree_root = tmp_path / "manuscripts" / "ms-clean"
        (tree_root / "sections").mkdir(parents=True, exist_ok=True)
        project_notes_dir = tmp_path

        class _FakeType:
            key = "lit-review"
            equation_sources = ()

        payload = build_approve_payload(tree_root, project_notes_dir, _FakeType())
        assert payload.get("canary_aborted") is False
