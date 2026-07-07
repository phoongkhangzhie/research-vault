"""test_sr_hub_dag_rails.py — SR-HUB-DAG slices A, B, D acceptance tests.

Coverage:
  Slice A — catalog SSOT + `rv dag templates`
    A1. catalog.py — 2 loops (experiment + lit-review), completeness, gate-location accuracy
    A2. dag/verbs.py — `rv dag templates` subcommand present + output parseable
    A3. cli.py — dag when_to_use mentions `templates`

  Slice B — `rv experiment new`
    B1. cmd_new authors plan note (plan_kind: preregistration, covers: skeleton)
    B2. cmd_new emits valid manifest (validate_manifest passes)
    B3. manifest contains human-go-plan gate
    B4. rv plan freeze round-trip: freeze-hash stores on the run, re-verify passes
    B5. rv help --check green with experiment verb present
    B6. anti-pattern: duplicate id raises FileExistsError
    B7. --mains N=2 produces 2x main branches + 2x human-go-conditionals-main*

  Slice D — rv status orphan-guardrail
    D1. orphan preregistration plan (no registered run) → WARN in attention
    D2. plan with a covering registered run → no false positive
    D3. non-preregistration plan note → not flagged
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.dag.catalog import (
    LOOP_CATALOG,
    get_loop,
    all_keys,
    LoopEntry,
    LoopGate,
)
from research_vault.dag.schema import validate_manifest, ManifestError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_instance(tmp_path: Path) -> Path:
    """Create a minimal Research Vault instance and return the config path."""
    notes_root = tmp_path / "notes"
    notes_root.mkdir(parents=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    proj_dir = tmp_path / "projects" / "demo-research"
    proj_dir.mkdir(parents=True)
    proj_notes = tmp_path / "notes" / "demo-research"
    proj_notes.mkdir(parents=True)

    config_file = tmp_path / "research_vault.toml"
    config_file.write_text(
        f"""
instance_root = "{tmp_path}"
notes_root = "{notes_root}"
state_dir = "{state_dir}"
agents_dir = "{tmp_path / '.agents'}"
tasks_dir = "{tmp_path / 'tasks'}"
control_dir = "{tmp_path / 'control'}"

[adapters]
notifier = "file"
backend = "local"
secrets = "env"

[projects.demo-research]
source_dir = "{proj_dir}"
tasks_dir = "{tmp_path / 'tasks' / 'demo-research'}"
notes_dir = "{proj_notes}"
""",
        encoding="utf-8",
    )
    return config_file


# ===========================================================================
# Slice A — catalog
# ===========================================================================


class TestCatalogCompleteness:
    """A1: catalog has exactly 3 loops with required structure.

    SR-RM-FIGMS removed figure + manuscript (2 loops); PR-M1 re-instantiates
    the manuscript loop with a type system (figure stays removed).
    """

    def test_three_loops(self):
        assert len(LOOP_CATALOG) == 3

    def test_all_keys_match(self):
        expected = {"experiment", "lit-review", "manuscript"}
        assert set(all_keys()) == expected

    def test_no_figure_loop(self):
        """SR-RM-FIGMS: figure loop removed (still absent — not reinstated)."""
        assert get_loop("figure") is None

    def test_manuscript_loop_reinstated(self):
        """PR-M1: the manuscript loop is reinstated with a type system.

        SR-RM-FIGMS removed it; PR-M1 rebuilds it type-generic — this is the
        reversal of the old test_no_manuscript_loop pin.
        """
        assert get_loop("manuscript") is not None

    def test_each_entry_has_scaffolder_or_none(self):
        for entry in LOOP_CATALOG:
            # scaffolder is str or None — no other type
            assert entry.scaffolder is None or isinstance(entry.scaffolder, str)

    def test_experiment_has_scaffolder(self):
        exp = get_loop("experiment")
        assert exp is not None
        assert exp.scaffolder is not None
        assert "rv experiment" in exp.scaffolder

    def test_lit_review_has_scaffolder(self):
        lr = get_loop("lit-review")
        assert lr is not None
        assert lr.scaffolder is not None
        assert "rv review" in lr.scaffolder

    def test_manuscript_has_scaffolder(self):
        """PR-M1: manuscript loop is scaffolded via `rv manuscript new`."""
        ms = get_loop("manuscript")
        assert ms is not None
        assert ms.scaffolder is not None
        assert "rv manuscript" in ms.scaffolder

    def test_topology_summary_non_empty(self):
        for entry in LOOP_CATALOG:
            assert entry.topology_summary.strip(), f"topology_summary empty for {entry.key}"

    def test_human_go_gates_non_empty_for_all_loops(self):
        for entry in LOOP_CATALOG:
            assert entry.human_go_gates, f"no gates for loop {entry.key}"

    def test_each_gate_has_node_id_and_label(self):
        for entry in LOOP_CATALOG:
            for gate in entry.human_go_gates:
                assert gate.node_id.strip()
                assert gate.label.strip()

    def test_as_dict_round_trip(self):
        for entry in LOOP_CATALOG:
            d = entry.as_dict()
            assert d["key"] == entry.key
            assert len(d["human_go_gates"]) == len(entry.human_go_gates)

    def test_get_loop_returns_none_for_unknown(self):
        assert get_loop("nonexistent-loop") is None

    def test_experiment_gate_names_match_shipped_manifest(self):
        """Gate IDs must match the SHIPPED research-loop.json exactly."""
        # Ground truth from data/examples/demo-research/research-loop.json:
        # human-go-plan, human-go-conditionals-main1, human-go-conditionals-main2, human-go-findings
        exp = get_loop("experiment")
        assert exp is not None
        gate_ids = {g.node_id for g in exp.human_go_gates}
        assert "human-go-plan" in gate_ids
        assert "human-go-findings" in gate_ids
        # Conditional gates (at least one)
        assert any("conditionals" in gid for gid in gate_ids)

    def test_lit_review_gate_names_match_scaffolder_output(self):
        """Catalog gate IDs must match what rv review new (review/__init__.py) emits.

        Ground truth from review/__init__.py:
          Phase-1: approve-protocol, coverage-gate
          Phase-2: approve-review
        NOT from the legacy demo-litreview/lit-review-loop.json (old manual format).
        """
        lr = get_loop("lit-review")
        assert lr is not None
        gate_ids = {g.node_id for g in lr.human_go_gates}
        assert "approve-protocol" in gate_ids
        assert "coverage-gate" in gate_ids
        assert "approve-review" in gate_ids

    def test_manuscript_gate_names_match_scaffolder_output(self):
        """Catalog gate IDs must match what manuscript/__init__.py emits (PR-M1).

        Ground truth from manuscript/__init__.py _build_phase2_manifest:
          approve-manuscript (the terminal gate for every registered type).
        """
        ms = get_loop("manuscript")
        assert ms is not None
        gate_ids = {g.node_id for g in ms.human_go_gates}
        assert "approve-manuscript" in gate_ids

    def test_experiment_freeze_gate_has_freeze_action(self):
        """human-go-plan gate must carry the freeze_action (K-3 reminder)."""
        exp = get_loop("experiment")
        assert exp is not None
        plan_gate = next(
            (g for g in exp.human_go_gates if g.node_id == "human-go-plan"), None
        )
        assert plan_gate is not None
        assert plan_gate.freeze_action is not None
        assert "rv plan freeze" in plan_gate.freeze_action


class TestCatalogGrounding:
    """A1b — catalog gate IDs grounded in REAL shipped manifests / scaffolders.

    Every catalog human_go_gate.node_id MUST appear as a real "human-go" typed
    node in the corresponding manifest source. This is the canonical drift detector:
    if a scaffolder renames a gate, this test goes red immediately.
    """

    def _real_human_go_ids_from_json(self, json_path: Path) -> set[str]:
        """Extract human-go node IDs from a manifest JSON file."""
        data = json.loads(json_path.read_text(encoding="utf-8"))
        return {
            n["id"] for n in data.get("nodes", []) if n.get("type") == "human-go"
        }

    def _real_human_go_ids_from_scaffolder(self, fn, *args, **kwargs) -> set[str]:
        """Call a manifest-builder function and extract human-go IDs."""
        manifest = fn(*args, **kwargs)
        return {
            n["id"] for n in manifest.get("nodes", []) if n.get("type") == "human-go"
        }

    def _examples_dir(self) -> Path:
        import research_vault
        pkg_root = Path(research_vault.__file__).parent
        return pkg_root / "data" / "examples"

    def test_experiment_gates_in_shipped_manifest(self):
        """experiment catalog gates must all appear in demo-research/research-loop.json."""
        shipped = self._examples_dir() / "demo-research" / "research-loop.json"
        assert shipped.exists(), f"shipped manifest not found: {shipped}"
        real_ids = self._real_human_go_ids_from_json(shipped)
        exp = get_loop("experiment")
        assert exp is not None
        for gate in exp.human_go_gates:
            # human-go-conditionals-main1 is grounding representative for
            # all human-go-conditionals-main* nodes (shipped manifest has N of them)
            if "conditionals" in gate.node_id:
                assert any("conditionals" in rid for rid in real_ids), (
                    f"No conditionals gate found in shipped manifest. "
                    f"Real gates: {real_ids}"
                )
            else:
                assert gate.node_id in real_ids, (
                    f"Catalog gate {gate.node_id!r} NOT in shipped manifest. "
                    f"Real gates: {real_ids}"
                )

    def test_litreview_gates_in_scaffolder_output(self, tmp_path):
        """lit-review catalog gates must appear in review/__init__.py scaffolder output.

        The REAL source is the scaffolder (review/__init__.py), NOT the legacy
        demo-litreview/lit-review-loop.json which predates SR-LR-1.
        Phase-1 gates: approve-protocol, coverage-gate
        Phase-2 gates: approve-review
        """
        from research_vault.review import _build_phase1_manifest, _build_phase2_manifest

        review_dir = tmp_path / "reviews" / "grounding-test"
        review_dir.mkdir(parents=True)
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir()

        # Phase 1
        p1 = _build_phase1_manifest(
            "grounding-test", "grounding-scope", "Is this grounded?",
            review_dir, notes_dir,
        )
        p1_ids = {n["id"] for n in p1.get("nodes", []) if n.get("type") == "human-go"}

        # Phase 2 (1 dummy paper so nodes are emitted)
        p2 = _build_phase2_manifest(
            "grounding-test", "grounding-scope", ["dummy2024"],
            notes_dir, review_dir,
        )
        p2_ids = {n["id"] for n in p2.get("nodes", []) if n.get("type") == "human-go"}

        all_real = p1_ids | p2_ids
        lr = get_loop("lit-review")
        assert lr is not None
        for gate in lr.human_go_gates:
            assert gate.node_id in all_real, (
                f"Catalog gate {gate.node_id!r} NOT in review scaffolder output. "
                f"Phase-1 gates: {p1_ids}, Phase-2 gates: {p2_ids}"
            )

    def test_manuscript_gates_in_scaffolder_output(self, tmp_path):
        """manuscript catalog gates must appear in manuscript/__init__.py scaffolder output.

        PR-M1: the stub `lit-review` ManuscriptType has phase1_builder=None
        (pass-through — no Phase-1 manifest at all), so only Phase-2 is
        checked here. Phase-2 gate: approve-manuscript.
        """
        from research_vault.manuscript import _build_phase2_manifest
        from research_vault.manuscript.types import get_type

        project_notes_dir = tmp_path / "notes"
        project_notes_dir.mkdir()
        tree_root = tmp_path / "notes" / "manuscripts" / "grounding-ms"
        tree_root.mkdir(parents=True)
        (tree_root / "sections").mkdir()

        ms_type = get_type("lit-review")
        assert ms_type is not None

        p2 = _build_phase2_manifest(
            "grounding-test", "grounding-ms", ms_type, project_notes_dir, tree_root,
        )
        p2_ids = {n["id"] for n in p2.get("nodes", []) if n.get("type") == "human-go"}

        ms = get_loop("manuscript")
        assert ms is not None
        for gate in ms.human_go_gates:
            assert gate.node_id in p2_ids, (
                f"Catalog gate {gate.node_id!r} NOT in manuscript scaffolder output. "
                f"Phase-2 gates: {p2_ids}"
            )

class TestDagTemplatesVerb:
    """A2: rv dag templates subcommand is registered and prints parseable output."""

    def test_templates_in_parser(self):
        from research_vault.dag.verbs import build_parser
        p = build_parser()
        # Should parse without error
        args = p.parse_args(["templates"])
        assert args.dag_cmd == "templates"

    def test_templates_prints_three_loops(self, capsys):
        from research_vault.dag.verbs import build_parser, run
        p = build_parser()
        args = p.parse_args(["templates"])
        rc = run(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "experiment" in out
        assert "lit-review" in out
        # PR-M1: manuscript loop reinstated (figure stays removed — SR-RM-FIGMS)
        assert "manuscript" in out
        assert "figure" not in out

    def test_templates_output_contains_gates(self, capsys):
        from research_vault.dag.verbs import build_parser, run
        p = build_parser()
        args = p.parse_args(["templates"])
        run(args)
        out = capsys.readouterr().out
        # Experiment gates from shipped research-loop.json
        assert "human-go-plan" in out
        assert "human-go-findings" in out
        # Lit-review gates from review/__init__.py scaffolder
        assert "approve-protocol" in out
        assert "coverage-gate" in out
        # Manuscript gate from manuscript/__init__.py scaffolder (PR-M1)
        assert "approve-manuscript" in out

    def test_templates_output_mentions_scaffolders(self, capsys):
        from research_vault.dag.verbs import build_parser, run
        p = build_parser()
        args = p.parse_args(["templates"])
        run(args)
        out = capsys.readouterr().out
        assert "rv experiment" in out
        assert "rv review" in out
        # PR-M1: rv manuscript scaffolder reinstated (rv figure stays removed — SR-RM-FIGMS)
        assert "rv manuscript" in out
        assert "rv figure" not in out


class TestCliDagWhenToUse:
    """A3: cli.py dag when_to_use mentions templates."""

    def test_dag_when_to_use_mentions_templates(self):
        from research_vault.cli import _VERB_REGISTRY
        wtu = _VERB_REGISTRY["dag"]["when_to_use"]
        assert "`rv dag templates`" in wtu or "rv dag templates" in wtu


# ===========================================================================
# Slice B — rv experiment new
# ===========================================================================


class TestExperimentNew:
    """B: rv experiment new scaffolds plan note + manifest."""

    @pytest.fixture
    def instance(self, tmp_path, monkeypatch):
        cfg_path = _make_instance(tmp_path)
        monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_path))
        # Reset config cache so the env var takes effect
        from research_vault.config import reset_config_cache
        reset_config_cache()
        yield tmp_path
        reset_config_cache()

    # B1: plan note authored with correct frontmatter
    def test_plan_note_created(self, instance):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        plan_path, manifest_path = cmd_new(
            "demo-research", "q1", question="Does X cause Y?", config=cfg
        )
        assert plan_path.exists()
        text = plan_path.read_text(encoding="utf-8")
        assert "plan_kind: preregistration" in text
        assert "q1-plan" in text  # citekey

    def test_plan_note_covers_skeleton(self, instance):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        plan_path, _ = cmd_new(
            "demo-research", "q1", question="Does X cause Y?", config=cfg
        )
        text = plan_path.read_text(encoding="utf-8")
        # covers: must list the generated main + ablation IDs
        assert "q1-main1" in text
        assert "q1-main1-abl-A" in text

    def test_plan_note_filename_convention(self, instance):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        plan_path, _ = cmd_new(
            "demo-research", "xling", question="Cross-lingual?", config=cfg
        )
        assert plan_path.name == "xling-plan.md"

    # B2: manifest validates
    def test_manifest_validates(self, instance):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        _, manifest_path = cmd_new(
            "demo-research", "exp1", question="Test?", config=cfg
        )
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        # Should not raise
        validate_manifest(manifest)

    def test_manifest_run_id_convention(self, instance):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        _, manifest_path = cmd_new(
            "demo-research", "q2", question="Test?", config=cfg
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["run_id"] == "q2-loop"

    # B3: human-go-plan gate present
    def test_human_go_plan_in_manifest(self, instance):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        _, manifest_path = cmd_new(
            "demo-research", "q3", question="Test?", config=cfg
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        node_ids = {n["id"] for n in manifest["nodes"]}
        assert "human-go-plan" in node_ids

    def test_human_go_findings_in_manifest(self, instance):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        _, manifest_path = cmd_new(
            "demo-research", "q4", question="Test?", config=cfg
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        node_ids = {n["id"] for n in manifest["nodes"]}
        assert "human-go-findings" in node_ids

    # B4: freeze round-trip
    def test_freeze_round_trip_via_printed_cli_commands(self, instance):
        """B4 — end-to-end: drives the EXACT cli commands printed by rv experiment new.

        Regression test for BLOCKER 1: the old plan/verbs.py defaulted notes_root to
        cfg.notes_root/"experiments" (shared tree), not the project's source_dir.
        The plan note lives in source_dir/experiments — different path — so the hash
        computation found zero child notes and stored a wrong hash.

        This test simulates what the operator does from the printed "Next steps":
          1. rv experiment new  (creates plan note + manifest in source_dir/experiments)
          2. rv dag run <manifest>  (registers the run state — simulated via RunStore API)
          3. rv dag approve <run_id> human-go-plan  (simulated — not tested here)
          4. rv plan freeze <run_id> <plan-note>  NO --notes-root  ← the critical path
          5. rv plan verify-freeze <run_id> <plan-note>  NO --notes-root  ← must pass
        """
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        from research_vault.dag.store import RunStore, RunState
        from research_vault.plan.verbs import _run_freeze, _run_verify_freeze
        import time as _time

        cfg = load_config()
        plan_path, manifest_path = cmd_new(
            "demo-research", "freeze-cli-test",
            question="CLI freeze round-trip?", config=cfg,
        )

        # Step 2: register the run (simulates rv dag run <manifest>)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        store = RunStore.from_config(cfg)
        run_state = RunState(
            run_id=manifest["run_id"],
            manifest_path=str(manifest_path),
            created_at=_time.time(),
        )
        run_state.init_nodes(manifest)
        store.create(run_state)

        # Write child notes in plan_note.parent (same dir as the plan note).
        # This is the correct experiments dir under source_dir — NOT notes_root/experiments.
        experiments_dir = plan_path.parent
        child_ids = ["freeze-cli-test-main1", "freeze-cli-test-main1-abl-A"]
        for cid in child_ids:
            (experiments_dir / f"{cid}.md").write_text(
                f"---\ntype: experiments\ncitekey: {cid}\n"
                f"stance: confirmatory\nplan_role: main\n---\n",
                encoding="utf-8",
            )

        # Step 4: rv plan freeze <run_id> <plan-note>  (NO --notes-root)
        # Simulates the printed CLI command verbatim — no notes_root override.
        freeze_args = argparse.Namespace(
            run_id=manifest["run_id"],
            plan_note=str(plan_path),
            notes_root=None,  # ← the printed command has no --notes-root
        )
        rc_freeze = _run_freeze(freeze_args)
        assert rc_freeze == 0, (
            "rv plan freeze returned non-zero — notes_root defaulted to the WRONG path. "
            "Fix: plan/verbs.py must default notes_root = plan_note.parent."
        )

        # Step 5: rv plan verify-freeze <run_id> <plan-note>  (NO --notes-root)
        verify_args = argparse.Namespace(
            run_id=manifest["run_id"],
            plan_note=str(plan_path),
            notes_root=None,  # stored pin takes precedence (SR-FREEZE-FIX)
        )
        rc_verify = _run_verify_freeze(verify_args)
        assert rc_verify == 0, (
            "rv plan verify-freeze returned non-zero — hash mismatch or pin mismatch."
        )

    # B5: rv help --check green with experiment present
    def test_help_check_green(self, instance):
        from research_vault.cli import main
        result = main(["help", "--check"])
        assert result == 0

    # B6: duplicate id raises
    def test_duplicate_id_raises(self, instance):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        cmd_new("demo-research", "dup", question="First?", config=cfg)
        with pytest.raises(FileExistsError):
            cmd_new("demo-research", "dup", question="Second?", config=cfg)

    # B7: --mains N=2
    def test_two_mains_produces_two_branches(self, instance):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        _, manifest_path = cmd_new(
            "demo-research", "two-mains",
            question="Two mains?", n_mains=2, config=cfg
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        node_ids = {n["id"] for n in manifest["nodes"]}
        # Both main branches
        assert "two-mains-main1-run" in node_ids
        assert "two-mains-main2-run" in node_ids
        # Both conditional gates
        assert "human-go-conditionals-main1" in node_ids
        assert "human-go-conditionals-main2" in node_ids

    def test_two_mains_manifest_validates(self, instance):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        _, manifest_path = cmd_new(
            "demo-research", "two-mains-v",
            question="Two mains?", n_mains=2, config=cfg
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        validate_manifest(manifest)

    # B8: CLI verb round-trip
    def test_cli_experiment_verb_registered(self):
        from research_vault.cli import _VERB_REGISTRY
        assert "experiment" in _VERB_REGISTRY
        assert _VERB_REGISTRY["experiment"]["sr"] == "SR-HUB-DAG"

    def test_cli_experiment_when_to_use_mentions_antipattern(self):
        from research_vault.cli import _VERB_REGISTRY
        wtu = _VERB_REGISTRY["experiment"]["when_to_use"]
        assert "hand-dispatching" in wtu.lower() or "ad-hoc" in wtu.lower()

    def test_cli_experiment_in_help_phase_map(self):
        from research_vault.cli import _HELP_PHASE_MAP
        experiment_group = None
        for group_name, verbs in _HELP_PHASE_MAP:
            if "experiment" in verbs:
                experiment_group = group_name
        assert experiment_group is not None, "'experiment' not in any _HELP_PHASE_MAP group"
        assert experiment_group == "Experiment"

    # B9: plan verb in_registry prints correct sr
    def test_experiment_verb_in_registry_has_sr(self):
        from research_vault.cli import _VERB_REGISTRY
        entry = _VERB_REGISTRY.get("experiment", {})
        assert "SR-HUB-DAG" in entry.get("sr", "")

    # B10: methods-update node present (soft edge)
    def test_methods_update_node_present(self, instance):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        _, manifest_path = cmd_new(
            "demo-research", "meth-test", question="Methods?", config=cfg
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        node_ids = {n["id"] for n in manifest["nodes"]}
        assert "methods-update" in node_ids


# ===========================================================================
# Slice D — rv status orphan-guardrail
# ===========================================================================


class TestOrphanGuardrail:
    """D: rv status emits WARN for orphan preregistration plans."""

    @pytest.fixture
    def instance(self, tmp_path, monkeypatch):
        cfg_path = _make_instance(tmp_path)
        monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_path))
        from research_vault.config import reset_config_cache
        reset_config_cache()
        yield tmp_path
        reset_config_cache()

    def _notes_dir(self) -> Path:
        from research_vault.config import load_config
        cfg = load_config()
        return cfg.project_notes_dir("demo-research")

    def _write_plan_note(
        self,
        plan_id: str,
        plan_kind: str = "preregistration",
    ) -> Path:
        """Write a minimal plan note to the experiments/ dir."""
        experiments_dir = self._notes_dir() / "experiments"
        experiments_dir.mkdir(parents=True, exist_ok=True)
        p = experiments_dir / f"{plan_id}-plan.md"
        p.write_text(
            f"---\ntype: experiments\ncitekey: {plan_id}-plan\n"
            f"plan_kind: {plan_kind}\ncovers: [{plan_id}-main1]\n---\n",
            encoding="utf-8",
        )
        return p

    def _register_run(self, run_id: str, manifest_path: Path):
        """Register a minimal run state in the store."""
        from research_vault.config import load_config
        from research_vault.dag.store import RunStore, RunState
        cfg = load_config()
        store = RunStore.from_config(cfg)
        rs = RunState(
            run_id=run_id,
            manifest_path=str(manifest_path),
            created_at=time.time(),
        )
        # Minimal manifest for the run
        manifest = {
            "run_id": run_id,
            "nodes": [
                {
                    "id": "dummy-node",
                    "type": "agent",
                    "spec": "task://demo#dummy",
                    "needs": [],
                }
            ],
        }
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        rs.init_nodes(manifest)
        store.create(rs)

    # D1: orphan plan → WARN
    def test_orphan_preregistration_plan_warns(self, instance):
        """A preregistration plan with no registered run → WARN in needs-attention."""
        self._write_plan_note("orphan-q1")
        from research_vault.status import cmd_status
        from research_vault.config import load_config
        cfg = load_config()
        output = cmd_status("demo-research", config=cfg)
        assert "WARN" in output
        assert "orphan-q1-plan.md" in output
        assert "rv plan freeze" in output or "rv experiment new" in output

    # D2: plan with covering run → no false positive
    def test_covered_plan_no_false_positive(self, instance):
        """A plan with a matching run_id → no WARN for it."""
        self._write_plan_note("covered-q1")
        # Register a run with the expected run_id: "covered-q1-loop"
        manifest_path = (
            self._notes_dir() / "experiments" / "covered-q1-loop.json"
        )
        self._register_run("covered-q1-loop", manifest_path)

        from research_vault.status import cmd_status
        from research_vault.config import load_config
        cfg = load_config()
        output = cmd_status("demo-research", config=cfg)
        # Should NOT warn about covered-q1-plan.md
        assert "covered-q1-plan.md" not in output

    # D3: non-preregistration plan → not flagged
    def test_non_preregistration_plan_not_flagged(self, instance):
        """A note with plan_kind != preregistration is not an orphan."""
        self._write_plan_note("exploratory-q1", plan_kind="exploratory")
        from research_vault.status import cmd_status
        from research_vault.config import load_config
        cfg = load_config()
        output = cmd_status("demo-research", config=cfg)
        assert "exploratory-q1-plan.md" not in output

    # D4: no experiments dir → no crash
    def test_no_experiments_dir_no_crash(self, instance):
        """Status doesn't crash when experiments/ dir doesn't exist."""
        from research_vault.status import cmd_status
        from research_vault.config import load_config
        cfg = load_config()
        # Should run cleanly
        output = cmd_status("demo-research", config=cfg)
        assert "rv status" in output.lower() or "demo-research" in output

    # D5: remedy text mentions rv experiment new
    def test_orphan_warn_mentions_rv_experiment_new(self, instance):
        """The WARN message tells the operator exactly how to fix it."""
        self._write_plan_note("fix-me-q1")
        from research_vault.status import cmd_status
        from research_vault.config import load_config
        cfg = load_config()
        output = cmd_status("demo-research", config=cfg)
        # Should mention the scaffold command
        assert "rv experiment new" in output
        # Should mention the specific id
        assert "fix-me-q1" in output
