"""tests/test_framework_gate_autonomy.py — framework-gate-autonomy design
(option A, 2026-07-09): wiring a multi-researcher lens ensemble ->
select-and-graft synthesis -> cold fail-closed critic -> auto-GO into
manuscript Phase-1's ``approve-framework`` gate.

Design of record: docs/superpowers/specs/2026-07-09-framework-gate-autonomy-design.md.

★ GROUNDING CONTRADICTION (surfaced, not silently resolved — charter §7):
the framework-gate-autonomy design's original prose asked for an
async-veto window (``open_veto_window``/``check_declare_final_gate``/
``rv dag veto``) opening on a GO, with the human's veto as a passive
backstop after the fact. Re-grounding against ``origin/main`` (this PR's
required first step) found that the ENTIRE async-veto/provisional
machinery was REMOVED, same day, by the single-human-gate design
(commit e411021 / PR #201 — see ``review/autonomy.py``'s module docstring
and DEVLOG.md's "single-human-gate design: approve-review autonomous,
async-veto removed" entry): "only `approve-protocol` is a human gate...
an auto-resolved decision is FINAL THE MOMENT IT RESOLVES: no `provisional`
stamp, no async-veto window." ``VetoWindow``/``open_veto_window``/
``cast_veto``/``clear_provisional_if_elapsed``/``check_declare_final_gate``
and the ``rv dag veto`` CLI verb do not exist on ``origin/main`` at all —
resurrecting them here would directly contradict a recorded, deliberate,
same-day architectural decision (not a stale line-number drift; a whole
subsystem's deletion). This PR does NOT re-add them: ``approve-framework``
resolves fully autonomously (consistent with every other autonomous gate
— coverage-gate/approve-review/approve-manuscript), with NO provisional
stamp anywhere. Tests 5/6 below are adapted accordingly: test 5 asserts
the happy-path auto-GO + Phase-2 auto-emission with NO veto/provisional
bookkeeping; test 6 (in place of the literally-specified async-veto BLOCK
path, which cannot be built against deleted primitives) asserts the veto
machinery stays absent post-PR and that a machine-synthesized spine's
_framework-decision.md carries the full provenance record a human would
need to review retrospectively, with no provisional stamp. Flagged
prominently in the PR body / DEVLOG for the Architect + Khang's fit-check.

Real-runner integration: drives the ACTUAL DAG runner (cmd_run/cmd_tick/
cmd_complete/cmd_approve) against a REAL phase1_builder manifest — never a
hand-mocked function call. Agent nodes are "completed" by writing their
real artifact shape and marking succeeded (the same convention every other
DAG test in this suite uses — the runner cannot execute an agent node
in-process).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from research_vault.manuscript.types.lit_review import FRAMEWORK_SHAPES


def _mark_succeeded(store, run_id: str, node_id: str) -> None:
    from research_vault.dag.verbs import cmd_complete
    rc = cmd_complete(argparse.Namespace(run_id=run_id, node_id=node_id, status="succeeded"))
    assert rc == 0, f"cmd_complete({node_id}) failed"


def _new_manuscript(cfg, slug: str):
    """Scaffold a real lit-review manuscript + Phase-1 manifest, run it."""
    from research_vault.manuscript import cmd_new
    from research_vault.dag.verbs import cmd_run
    from research_vault.dag.store import RunStore

    with pytest.warns(UserWarning, match="no frozen review corpus"):
        note_path, tree_root, phase1 = cmd_new(
            "demo-research", slug, ms_type_key="lit-review", config=cfg,
        )
    manifest_path = tree_root / "phase1-dag.json"
    rc = cmd_run(argparse.Namespace(manifest=str(manifest_path)))
    assert rc == 0
    store = RunStore.from_config(cfg)
    return phase1["run_id"], tree_root, store, phase1


def _lens_ids(phase1: dict) -> list[str]:
    return [n["id"] for n in phase1["nodes"] if n["id"].startswith("framework-lens-")]


def _write_candidate(tree_root: Path, lens_key: str, *, spine_shape: str, branches: list[str]) -> None:
    branches_block = "\n".join(f"  - {b}" for b in branches)
    (tree_root / f"_framework-candidate-{lens_key}.md").write_text(
        f"---\nlens: {lens_key}\nspine_shape: {spine_shape}\nbranches:\n{branches_block}\n---\n\n"
        f"Candidate for lens {lens_key}: draws on the corpus's {lens_key} axis; "
        f"misfits: none material.\n",
        encoding="utf-8",
    )


def _commit_spine(tree_root: Path, *, spine_shape: str, branches: list[str], decision_body: str) -> None:
    """Simulate framework-synthesize's commit: write the frozen spine into
    ``_manuscript.md`` (machine origin) + the full veto-provenance record."""
    note_path = tree_root / "_manuscript.md"
    text = note_path.read_text(encoding="utf-8")
    text = text.replace("spine_shape: \n", f"spine_shape: {spine_shape}\n")
    branches_block = "\n".join(f"  - {b}" for b in branches)
    text = text.replace("branches: \n", f"branches:\n{branches_block}\n")
    if "framework_origin" not in text:
        text = text.replace("---\n", "---\nframework_origin: machine\n", 1)
    note_path.write_text(text, encoding="utf-8")
    (tree_root / "_framework-decision.md").write_text(decision_body, encoding="utf-8")


def _write_critique(tree_root: Path, *, verdict: str, canary_id: str, reasons: list[str] | None = None) -> None:
    reasons = reasons or []
    body = "\n".join(f"- {r}" for r in reasons)
    (tree_root / "_framework-critique.md").write_text(
        f"---\nverdict: {verdict}\ncanary_id: {canary_id}\n---\n\n{body}\n",
        encoding="utf-8",
    )


def _drive_to_critic_ready(cfg, slug: str):
    """Common setup for tests 3-6: scope -> all N lens candidates written +
    marked succeeded -> tick (framework-synthesize now dispatch-ready)."""
    from research_vault.dag.verbs import cmd_tick

    run_id, tree_root, store, phase1 = _new_manuscript(cfg, slug)
    _mark_succeeded(store, run_id, "scope")
    cmd_tick(argparse.Namespace(run_id=run_id))

    lens_ids = _lens_ids(phase1)
    assert lens_ids, "expected at least one framework-lens-<lens> node"
    for lens_id in lens_ids:
        lens_key = lens_id[len("framework-lens-"):]
        _write_candidate(tree_root, lens_key, spine_shape="n-axis", branches=[f"{lens_key}-a", f"{lens_key}-b"])
        _mark_succeeded(store, run_id, lens_id)
    cmd_tick(argparse.Namespace(run_id=run_id))
    return run_id, tree_root, store, phase1, lens_ids


class TestEnsembleFanOut:
    """Test 1: N lens nodes run as separate cold nodes, producing N DISTINCT
    candidate files (not N copies of one)."""

    def test_n_lens_nodes_produce_n_distinct_candidates(self, tmp_instance, monkeypatch):
        from research_vault.config import load_config

        cfg = load_config()
        run_id, tree_root, store, phase1, lens_ids = _drive_to_critic_ready(cfg, "survey-fanout")

        assert len(lens_ids) == len(FRAMEWORK_SHAPES) or len(lens_ids) >= 3, (
            "expected a real multi-lens ensemble, not a single-candidate menu"
        )
        # Distinctness: every lens node has its OWN candidate file, with its
        # OWN lens-scoped content — never N copies of one shared file.
        candidate_paths = [
            tree_root / f"_framework-candidate-{lens_id[len('framework-lens-'):]}.md"
            for lens_id in lens_ids
        ]
        assert len(candidate_paths) == len(set(candidate_paths))
        contents = [p.read_text(encoding="utf-8") for p in candidate_paths]
        assert len(set(contents)) == len(contents), "candidates must be distinct, not copies"
        for lens_id, text in zip(lens_ids, contents):
            lens_key = lens_id[len("framework-lens-"):]
            assert f"lens: {lens_key}" in text

        # Independence: each lens node's manifest spec never mentions the
        # OTHER lenses' candidate filenames (no sibling-candidate visibility).
        lens_nodes_by_id = {n["id"]: n for n in phase1["nodes"] if n["id"] in lens_ids}
        for lens_id in lens_ids:
            spec = lens_nodes_by_id[lens_id]["spec"]
            for other_id in lens_ids:
                if other_id == lens_id:
                    continue
                other_key = other_id[len("framework-lens-"):]
                assert f"_framework-candidate-{other_key}.md" not in spec

        # framework-synthesize is dispatch-ready (needs every lens afterok).
        rs = store.load(run_id)
        assert rs.node_status("framework-synthesize") in ("pending", "succeeded")


class TestLensShapeValidity:
    """Test 2: every candidate's spine_shape is a valid FRAMEWORK_SHAPES key;
    _framework-decision.md records a (lens, shape) pair for all N + backbone."""

    def test_every_candidate_shape_is_registered_and_decision_records_all(self, tmp_instance, monkeypatch):
        from research_vault.config import load_config
        from research_vault.note import _parse_frontmatter
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        run_id, tree_root, store, phase1, lens_ids = _drive_to_critic_ready(cfg, "survey-shapes")

        valid_keys = {s["key"] for s in FRAMEWORK_SHAPES}
        recorded_pairs: list[tuple[str, str]] = []
        for lens_id in lens_ids:
            lens_key = lens_id[len("framework-lens-"):]
            text = (tree_root / f"_framework-candidate-{lens_key}.md").read_text(encoding="utf-8")
            fields, _ = _parse_frontmatter(text)
            shape = str(fields.get("spine_shape", "")).strip()
            assert shape in valid_keys, f"{lens_key}'s candidate shape {shape!r} not in FRAMEWORK_SHAPES"
            recorded_pairs.append((lens_key, shape))

        backbone_lens = recorded_pairs[0][0]
        backbone_shape = recorded_pairs[0][1]
        decision_body = (
            "## Candidates\n"
            + "\n".join(f"- ({lens}, {shape})" for lens, shape in recorded_pairs)
            + f"\n\n## Backbone\nSelected: ({backbone_lens}, {backbone_shape}) — most coherent.\n"
            + "\n## Rejections\n"
            + "\n".join(f"- {lens} rejected: less coherent than the backbone." for lens, _ in recorded_pairs[1:])
        )
        _commit_spine(tree_root, spine_shape=backbone_shape, branches=["a", "b"], decision_body=decision_body)
        _mark_succeeded(store, run_id, "framework-synthesize")
        cmd_tick(argparse.Namespace(run_id=run_id))

        decision_text = (tree_root / "_framework-decision.md").read_text(encoding="utf-8")
        for lens, shape in recorded_pairs:
            assert f"({lens}, {shape})" in decision_text
        assert f"({backbone_lens}, {backbone_shape})" in decision_text


class TestSynthesizeSelectsCoherentBackbone:
    """Test 3: framework-synthesize commits EXACTLY ONE spine
    (framework_origin: machine, non-empty spine_shape+branches);
    _framework-decision.md records backbone + grafted axes + per-loser
    rejection rationale + all N candidates."""

    def test_synthesize_commits_one_spine_with_full_provenance(self, tmp_instance, monkeypatch):
        from research_vault.config import load_config
        from research_vault.note import _parse_frontmatter
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        run_id, tree_root, store, phase1, lens_ids = _drive_to_critic_ready(cfg, "survey-synth")

        decision_body = (
            "## All N candidates\n"
            + "\n".join(
                f"- ({lens_id[len('framework-lens-'):]}, n-axis, "
                f"[{lens_id[len('framework-lens-'):]}-a, {lens_id[len('framework-lens-'):]}-b])"
                for lens_id in lens_ids
            )
            + "\n\n## Backbone selected\nby-mechanism (pipeline) — cleanest partition, fewest misfits.\n"
            + "\n## Grafted\nGrafted the by-population axis's population tag onto every pipeline stage.\n"
            + "\n## Rejection rationale (every loser)\n"
            + "\n".join(
                f"- {lens_id[len('framework-lens-'):]}: rejected — weaker coherence than the backbone."
                for lens_id in lens_ids
            )
        )
        _commit_spine(
            tree_root, spine_shape="pipeline", branches=["collect", "analyze", "report"],
            decision_body=decision_body,
        )
        _mark_succeeded(store, run_id, "framework-synthesize")
        cmd_tick(argparse.Namespace(run_id=run_id))

        note_text = (tree_root / "_manuscript.md").read_text(encoding="utf-8")
        fields, _ = _parse_frontmatter(note_text)
        assert str(fields.get("framework_origin", "")).strip() == "machine"
        assert str(fields.get("spine_shape", "")).strip() == "pipeline"
        branches = fields.get("branches", "")
        assert branches, "branches must be non-empty"

        decision_text = (tree_root / "_framework-decision.md").read_text(encoding="utf-8")
        assert "Backbone selected" in decision_text
        assert "Grafted" in decision_text
        assert "Rejection rationale" in decision_text
        for lens_id in lens_ids:
            assert lens_id[len("framework-lens-"):] in decision_text

        rs = store.load(run_id)
        assert rs.node_status("framework-synthesize") == "succeeded"
        assert rs.node_status("framework-critic") in ("pending", "succeeded")


class TestCriticFailClosed:
    """Test 4: a deliberately-incoherent synthesized spine -> critic BLOCK
    -> HALT past the bounded budget. Missing/malformed critic verdict ->
    HALT. Tripped/absent canary -> HALT."""

    def _commit_and_tick_to_critic(self, cfg, slug: str):
        from research_vault.dag.verbs import cmd_tick

        run_id, tree_root, store, phase1, lens_ids = _drive_to_critic_ready(cfg, slug)
        _commit_spine(
            tree_root, spine_shape="n-axis", branches=["x", "y"],
            decision_body="Backbone selected: incoherent Frankenstein union of two spines.\n",
        )
        _mark_succeeded(store, run_id, "framework-synthesize")
        cmd_tick(argparse.Namespace(run_id=run_id))
        critic_node = next(n for n in phase1["nodes"] if n["id"] == "framework-critic")
        canary_id = critic_node["canary_id"]
        return run_id, tree_root, store, phase1, canary_id

    def test_critic_block_halts(self, tmp_instance, monkeypatch):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        run_id, tree_root, store, phase1, canary_id = self._commit_and_tick_to_critic(cfg, "survey-critic-block")

        _write_critique(
            tree_root, verdict="BLOCK", canary_id=canary_id,
            reasons=["Frankenstein-graft incoherence: grafted axis contradicts backbone branch b."],
        )
        _mark_succeeded(store, run_id, "framework-critic")
        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        # A critic BLOCK is a deterministic, fixable finding — REVISE while
        # budget remains (awaiting-go for a human/agent revise dispatch),
        # never a silent GO. It must NEVER succeed.
        assert rs.node_status("approve-framework") != "succeeded"
        assert "emitted_next_phase_run_id" not in rs.node_states.get("approve-framework", {})

    def test_missing_critique_halts(self, tmp_instance, monkeypatch):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        run_id, tree_root, store, phase1, canary_id = self._commit_and_tick_to_critic(cfg, "survey-critic-missing")

        # Deliberately do NOT write _framework-critique.md.
        _mark_succeeded(store, run_id, "framework-critic")
        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("approve-framework") == "blocked"
        assert "HALT-DECLARE" in rs.node_states["approve-framework"]["decision_note"]
        assert "emitted_next_phase_run_id" not in rs.node_states["approve-framework"]

    def test_canary_mismatch_halts(self, tmp_instance, monkeypatch):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        run_id, tree_root, store, phase1, canary_id = self._commit_and_tick_to_critic(cfg, "survey-critic-canary")

        _write_critique(tree_root, verdict="PASS", canary_id="WRONG-CANARY-VALUE")
        _mark_succeeded(store, run_id, "framework-critic")
        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        # A canary mismatch is an untrustworthy-signal HALT (§1.2 priority 1)
        # — a mismatched PASS must NEVER be treated as a real pass.
        assert rs.node_status("approve-framework") == "blocked"
        assert "HALT-DECLARE" in rs.node_states["approve-framework"]["decision_note"]
        assert "emitted_next_phase_run_id" not in rs.node_states["approve-framework"]

    def test_absent_canary_field_halts(self, tmp_instance, monkeypatch):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        run_id, tree_root, store, phase1, canary_id = self._commit_and_tick_to_critic(cfg, "survey-critic-nocanary")

        # PASS verdict but no canary_id field at all.
        (tree_root / "_framework-critique.md").write_text(
            "---\nverdict: PASS\n---\n\nNo defects found.\n", encoding="utf-8",
        )
        _mark_succeeded(store, run_id, "framework-critic")
        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("approve-framework") == "blocked"
        assert "emitted_next_phase_run_id" not in rs.node_states["approve-framework"]


class TestHappyPathAutoGoAndNoVetoBookkeeping:
    """Test 5: coherent synthesized spine + not-reject critique -> auto-GO,
    Phase-2 auto-emits. (Adapted per the grounding contradiction above: NO
    async-veto window opens — the primitives were removed system-wide by
    the single-human-gate design, same day — so this asserts the clean
    auto-GO chain-through with NO provisional/veto stamp anywhere.)"""

    def test_happy_path_auto_gos_and_chains_to_phase2(self, tmp_instance, monkeypatch):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        run_id, tree_root, store, phase1, lens_ids = _drive_to_critic_ready(cfg, "survey-happy")

        _commit_spine(
            tree_root, spine_shape="n-axis", branches=["alpha", "beta"],
            decision_body="Backbone selected cleanly; no incompatible grafts; all losers rejected on coherence.\n",
        )
        _mark_succeeded(store, run_id, "framework-synthesize")
        cmd_tick(argparse.Namespace(run_id=run_id))

        critic_node = next(n for n in phase1["nodes"] if n["id"] == "framework-critic")
        canary_id = critic_node["canary_id"]
        _write_critique(tree_root, verdict="PASS", canary_id=canary_id)
        _mark_succeeded(store, run_id, "framework-critic")

        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("approve-framework") == "succeeded"
        assert "GO" in rs.node_states["approve-framework"]["decision_note"]
        assert rs.node_states["approve-framework"]["approved_by"] == "review.autonomy"

        # Phase-2 auto-emitted + auto-started in the SAME tick.
        child_run_id = rs.node_states["approve-framework"]["emitted_next_phase_run_id"]
        assert child_run_id == rs.meta["child_runs"]["approve-framework"]
        assert (tree_root / "phase2-dag.json").exists()
        child_rs = store.load(child_run_id)
        assert child_rs.node_status("outline") in ("succeeded", "pending")

        # No provisional/veto bookkeeping anywhere (single-human-gate design
        # — the async-veto window was removed, this PR does not resurrect it).
        note_text = (tree_root / "_manuscript.md").read_text(encoding="utf-8")
        assert "provisional" not in note_text
        assert "provisional" not in (tree_root / "_framework-decision.md").read_text(encoding="utf-8")


class TestVetoMachineryStaysAbsent:
    """Test 6 (adapted — see the grounding-contradiction note at module top):
    the literally-specified 'async-veto BLOCK path' cannot be built —
    VetoWindow/check_declare_final_gate/rv-dag-veto do not exist on
    origin/main (removed by the single-human-gate design, same day). This
    PR must not resurrect them. Asserts the veto machinery stays absent."""

    def test_veto_primitives_remain_absent(self):
        from research_vault.review import autonomy as auto

        for name in (
            "VetoWindow", "open_veto_window", "cast_veto",
            "clear_provisional_if_elapsed", "check_declare_final_gate",
        ):
            assert not hasattr(auto, name), (
                f"{name} must stay absent — resurrecting it would contradict "
                "the single-human-gate design (PR #201, same-day removal)."
            )

    def test_cmd_veto_remains_absent(self):
        from research_vault.dag import verbs

        assert not hasattr(verbs, "cmd_veto")


class TestF2PartialAdoptReentersFrameworkPipeline:
    """Test 8: a partial-adopt state (a manuscripts/<scope>/_manuscript.md
    note exists, but no phase1-dag.json) must re-enter Phase-1 (the
    framework-ensemble pipeline), never bypass straight to Phase-2 with no
    committed, critic-cleared spine (the F2 fix)."""

    def test_partial_scaffold_reenters_phase1_not_phase2(self, tmp_instance, monkeypatch):
        from research_vault.config import load_config
        from research_vault.dag.store import RunState, RunStore
        from research_vault.dag.verbs import _emit_next_phase
        from research_vault.manuscript import cmd_new

        cfg = load_config()
        scope_id = "survey-partial"

        # Simulate an operator/prior-partial scaffold: the manuscript note
        # exists (a real cmd_new call), but its phase1-dag.json was removed
        # (interrupted before Phase-1 ever ran) — the exact partial state
        # the F2 fix targets.
        with pytest.warns(UserWarning, match="no frozen review corpus"):
            note_path, tree_root, phase1 = cmd_new(
                "demo-research", scope_id, ms_type_key="lit-review", config=cfg,
            )
        (tree_root / "phase1-dag.json").unlink()
        assert note_path.exists()
        assert not (tree_root / "phase1-dag.json").exists()

        # A minimal parent manifest whose approve-review node this partial
        # state is discovered from (the real cross-loop caller shape).
        parent_manifest = {
            "run_id": f"review-{scope_id}-phase2",
            "project": "demo-research",
            "name": "parent",
            "global_cap": 1,
            "nodes": [
                {"id": "approve-review", "type": "human-go", "needs": []},
            ],
        }
        store = RunStore.from_config(cfg)
        parent_manifest_path = tree_root.parent.parent / "reviews" / scope_id / "phase2-dag.json"
        parent_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        parent_manifest_path.write_text(json.dumps(parent_manifest), encoding="utf-8")
        run_state = RunState(run_id=parent_manifest["run_id"], manifest_path=str(parent_manifest_path))
        run_state.init_nodes(parent_manifest)
        store.create(run_state)

        _emit_next_phase("approve-review", parent_manifest, parent_manifest_path, run_state, store)

        assert "phase_transition_error" not in run_state.node_states.get("approve-review", {}), (
            run_state.node_states.get("approve-review", {}).get("phase_transition_error")
        )
        child_run_id = run_state.node_states["approve-review"]["emitted_next_phase_run_id"]
        assert child_run_id.endswith("-phase1"), (
            f"partial-adopt must re-enter Phase-1 (the framework pipeline), "
            f"got child_run_id={child_run_id!r}"
        )
        assert (tree_root / "phase1-dag.json").exists(), "Phase-1 manifest must be (re)written"
        assert not (tree_root / "phase2-dag.json").exists(), (
            "must NOT bypass straight to Phase-2 — that is the exact F2 bug"
        )

        rewritten = json.loads((tree_root / "phase1-dag.json").read_text(encoding="utf-8"))
        rewritten_ids = [n["id"] for n in rewritten["nodes"]]
        assert any(nid.startswith("framework-lens-") for nid in rewritten_ids)

        child_rs = store.load(child_run_id)
        assert child_rs.node_status("scope") == "pending"
