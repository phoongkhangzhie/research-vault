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


def _saturation_info(*, stop_reason: str = "saturated") -> dict:
    return {"exists": True, "stop_reason": stop_reason, "is_backstop": stop_reason.startswith("backstop:")}


class TestCoverageGateDeviationCheck:
    def test_first_pass_stamps_frozen_baseline_and_proceeds(self, tmp_path: Path):
        meta: dict = {}
        corpus_path = tmp_path / "reviews" / "s1" / "_corpus.md"
        _corpus_note(corpus_path, ["paperA2024", "paperB2024"])
        deviations_path = tmp_path / "reviews" / "s1" / "_deviations.md"

        result = auto.classify_coverage_gate_with_deviation_check(
            meta, _saturation_info(), corpus_path=corpus_path, deviations_path=deviations_path,
        )
        assert result.disposition == auto.GO
        assert meta["frozen_corpus_citekeys"] == sorted(["paperA2024", "paperB2024"])

    def test_frozen_run_no_delta_is_ok(self, tmp_path: Path):
        corpus_path = tmp_path / "reviews" / "s2" / "_corpus.md"
        _corpus_note(corpus_path, ["paperA2024", "paperB2024"])
        deviations_path = tmp_path / "reviews" / "s2" / "_deviations.md"
        meta = {"frozen_corpus_citekeys": ["paperA2024", "paperB2024"]}

        result = auto.classify_coverage_gate_with_deviation_check(
            meta, _saturation_info(), corpus_path=corpus_path, deviations_path=deviations_path,
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
            meta, _saturation_info(), corpus_path=corpus_path, deviations_path=deviations_path,
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
            meta, _saturation_info(), corpus_path=corpus_path, deviations_path=deviations_path,
        )
        assert result.disposition == auto.GO

    def test_undeclared_delta_short_circuits_before_saturation_check(self, tmp_path: Path):
        """An undeclared deviation HALTs even when the saturation record
        itself would have cleanly GO'd — the deviation check is a fail-closed
        gate in front of, not behind, the saturation disposition."""
        corpus_path = tmp_path / "reviews" / "s5" / "_corpus.md"
        deviations_path = tmp_path / "reviews" / "s5" / "_deviations.md"
        _corpus_note(corpus_path, ["paperA2024", "paperC2024"])  # added + removed vs frozen
        meta = {"frozen_corpus_citekeys": ["paperA2024", "paperB2024"]}

        result = auto.classify_coverage_gate_with_deviation_check(
            meta, _saturation_info(stop_reason="saturated"), corpus_path=corpus_path,
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

    def _drive_to_coverage_gate(self, run_id: str, review_dir: Path, store, cfg, *, stop_reason: str, monkeypatch=None):
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
            (out / "_saturation.md").write_text(
                f"---\nstop_reason: {stop_reason}\n---\n\nSaturation curve.\n", encoding="utf-8",
            )
            return {"stop_reason": stop_reason}

        monkeypatch.setitem(_auto.OP_REGISTRY, "sweep", _fake_sweep)
        monkeypatch.setitem(_auto.OP_REGISTRY, "snowball", _fake_snowball)

        # review-scope "completes": writes _protocol.md with a counter-position
        # (L-2 gate requirement for approve-protocol).
        protocol_path = review_dir / "_protocol.md"
        protocol_path.write_text(
            "---\ncounter-position: a real counter-position\n---\n\nProtocol.\n",
            encoding="utf-8",
        )
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
        # (+ _coverage-gaps.md on backstop-termination).
        corpus_path = review_dir / "_corpus.md"
        corpus_path.write_text(
            "| annotation | citekey | title |\n|---|---|---|\n"
            "| [NEW] | alpha2024 | Alpha paper |\n| [NEW] | beta2024 | Beta paper |\n",
            encoding="utf-8",
        )
        if stop_reason.startswith("backstop:"):
            (review_dir / "_coverage-gaps.md").write_text("open frontier\n", encoding="utf-8")
        cmd_tick(argparse.Namespace(run_id=run_id))
        _mark_succeeded(store, run_id, "review-curate")

    def test_kick_walk_self_advances_and_auto_emits_phase2_on_go(self, tmp_instance: Path, monkeypatch):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        run_id, review_dir, store = self._kick_review(tmp_instance, cfg, scope="scope-go")
        self._drive_to_coverage_gate(run_id, review_dir, store, cfg, stop_reason="saturated", monkeypatch=monkeypatch)

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
        self._drive_to_coverage_gate(run_id, review_dir, store, cfg, stop_reason="backstop:3-waves", monkeypatch=monkeypatch)

        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("coverage-gate") == "succeeded"
        assert "GO-WITH-RESIDUE" in rs.node_states["coverage-gate"]["decision_note"]
        # Still proceeds — Phase-2 emitted exactly as the clean-GO case.
        assert "emitted_next_phase_run_id" in rs.node_states["coverage-gate"]

    def test_malformed_saturation_halts_never_emits_phase2(self, tmp_instance: Path, monkeypatch):
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
        self._drive_to_coverage_gate(run_id, review_dir, store, cfg, stop_reason="saturated", monkeypatch=monkeypatch)

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

        rs = store.load(child_run_id)
        relate_ids = [nid for nid in rs.node_states if nid.startswith("relate-")]
        assert relate_ids, "expected at least one relate-<key> node in Phase-2"

        project_notes_dir = review_dir.parent.parent
        for nid in relate_ids:
            citekey = nid[len("relate-"):]
            lit_path = project_notes_dir / "literature" / f"{citekey}.md"
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
        self._drive_to_coverage_gate(run_id, review_dir, store, cfg, stop_reason="saturated", monkeypatch=monkeypatch)

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

        # Chain continuity: the manuscript run self-advances scope ->
        # framework-propose, and STOPS at approve-framework (never silently
        # jumps to approve-manuscript) — framework-propose only writes a
        # candidate menu, never commits a spine (§5/D5 human-commitment gate).
        ms_store = store  # same RunStore backend
        _mark_succeeded(ms_store, ms_run_id, "scope")
        cmd_tick(argparse.Namespace(run_id=ms_run_id))
        ms_rs = ms_store.load(ms_run_id)
        assert ms_rs.node_status("framework-propose") in ("succeeded", "pending")
        if ms_rs.node_status("framework-propose") != "succeeded":
            _mark_succeeded(ms_store, ms_run_id, "framework-propose")
            cmd_tick(argparse.Namespace(run_id=ms_run_id))
            ms_rs = ms_store.load(ms_run_id)
        assert ms_rs.node_status("approve-framework") in ("awaiting-go", "blocked", "pending")
        assert ms_rs.node_status("approve-manuscript") if "approve-manuscript" in ms_rs.node_states else True

    def test_approve_review_go_with_residue_still_chains(self, tmp_instance: Path, monkeypatch):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        scope = "scope-chain-residue"
        run_id, review_dir, store = self._kick_review(tmp_instance, cfg, scope=scope)
        self._drive_to_coverage_gate(run_id, review_dir, store, cfg, stop_reason="backstop:3-waves", monkeypatch=monkeypatch)
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
        self._drive_to_coverage_gate(run_id, review_dir, store, cfg, stop_reason="saturated", monkeypatch=monkeypatch)
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
            (out / "_saturation.md").write_text(
                f"---\nstop_reason: {stop_reason}\n---\n\nSaturation curve.\n", encoding="utf-8",
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
        if stop_reason.startswith("backstop:"):
            (review_dir / "_coverage-gaps.md").write_text("open frontier\n", encoding="utf-8")
        cmd_tick(argparse.Namespace(run_id=run_id))
        _mark_succeeded(store, run_id, "review-curate")

    def test_declared_dark_source_halts_and_names_it(self, tmp_instance: Path, monkeypatch):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        run_id, review_dir, store = self._kick_review(tmp_instance, cfg, scope="scope-src-dark")
        self._drive_to_coverage_gate_with_sources(
            run_id, review_dir, store, cfg, stop_reason="saturated",
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
            run_id, review_dir, store, cfg, stop_reason="saturated",
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
            run_id, review_dir, store, cfg, stop_reason="saturated",
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
