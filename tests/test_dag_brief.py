"""test_dag_brief.py — SR-DAG-BRIEF acceptance tests.

Coverage:
  B1.  BRIEF_PREAMBLE is non-empty, contains role framing, instance boundary,
       anti-fabrication, and STRUCTURED-RETURN contract markers.
  B2.  build_brief: attempts==0 → NO diagnose-first block.
  B3.  build_brief: attempts>0  → diagnose-first block present with last_failure.
  B4.  build_brief: spec: is verbatim in the output.
  B5.  build_brief: resolved ABSOLUTE reads: paths in CONTEXT.
  B6.  build_brief: resolved produces: path(s) in CONTEXT.
  B7.  build_brief: deterministic — same inputs → byte-identical output.
  B8.  cmd_brief (CLI): emits brief on stdout, exit 0.
  B9.  cmd_brief (CLI): human-go node → stderr + exit 1.
  B10. cmd_brief (CLI): unknown node → stderr + exit 1.

  Golden-file tests (both loops):
  G1.  Experiment loop node (first attempt): brief structure matches golden pattern.
  G2.  Lit-review loop node (first attempt): brief structure matches golden pattern.

  SSOT test:
  S1.  resolve_produces_paths: produces.note path matches cmd_complete's gate path.
  S2.  resolve_reads_paths: returns ABSOLUTE paths (no unresolved entries for existing files).

  experiment.py task:// purge:
  E1.  No node in scaffolded experiment manifest has spec starting with "task://".
  E2.  No task:// dereferencer was added (no new resolver mechanism).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import Config, reset_config_cache
from research_vault.dag.brief import BRIEF_PREAMBLE, build_brief
from research_vault.dag.reads import resolve_reads_paths
from research_vault.dag.schema import validate_manifest, ManifestError
from research_vault.dag.store import RunState, RunStore
from research_vault.dag.verbs import (
    RETRY_DIAGNOSIS_DIRECTIVE,
    cmd_brief,
    resolve_produces_paths,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_cfg():
    reset_config_cache()
    yield
    reset_config_cache()


@pytest.fixture
def tmp_instance(tmp_path: Path):
    """Full tmp instance with RESEARCH_VAULT_CONFIG set + demo-research project."""
    cfg_file = tmp_path / "research_vault.toml"
    (tmp_path / "state").mkdir()
    (tmp_path / "notes").mkdir()
    proj_dir = tmp_path / "projects" / "demo-research"
    proj_dir.mkdir(parents=True)
    proj_notes = tmp_path / "notes" / "demo-research"
    proj_notes.mkdir(parents=True)
    # Create some OKF dirs to make reads: resolution pass
    (proj_notes / "experiments").mkdir(parents=True)
    (proj_notes / "findings").mkdir(parents=True)

    cfg_file.write_text(
        f"""
instance_root = "{tmp_path}"
notes_root = "{tmp_path / 'notes'}"
state_dir = "{tmp_path / 'state'}"
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
""",
        encoding="utf-8",
    )
    old = os.environ.get("RESEARCH_VAULT_CONFIG")
    os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_file)
    yield tmp_path
    if old is None:
        os.environ.pop("RESEARCH_VAULT_CONFIG", None)
    else:
        os.environ["RESEARCH_VAULT_CONFIG"] = old


@pytest.fixture
def tmp_cfg(tmp_path: Path) -> Config:
    """Minimal Config with no projects (for unit tests that don't need project I/O)."""
    (tmp_path / "state").mkdir()
    (tmp_path / "notes").mkdir()
    raw = {
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(tmp_path / "state"),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "adapters": {"notifier": "file", "backend": "local", "secrets": "env"},
        "projects": {},
    }
    return Config(raw)


def _make_agent_node(
    nid: str = "test-node",
    spec: str = "Do the thing.",
    reads: list | None = None,
    produces: dict | None = None,
    max_retries: int = 0,
) -> dict:
    n: dict = {
        "id": nid,
        "type": "agent",
        "label": f"Test node {nid}",
        "spec": spec,
    }
    if reads is not None:
        n["reads"] = reads
    if produces is not None:
        n["produces"] = produces
    if max_retries:
        n["max_retries"] = max_retries
    return n


def _make_manifest(nodes: list[dict], run_id: str = "test-run") -> dict:
    return {"run_id": run_id, "name": "Test", "global_cap": 4, "nodes": nodes}


def _start_run(tmp_instance: Path, manifest: dict) -> tuple[str, Path]:
    """Write manifest to disk and create a run state. Returns (run_id, manifest_path)."""
    from research_vault.config import load_config
    from research_vault.dag.store import RunStore
    cfg = load_config()
    store = RunStore.from_config(cfg)
    manifest_path = tmp_instance / "state" / f"{manifest['run_id']}.json"
    from research_vault.dag.schema import dump_manifest
    dump_manifest(manifest, manifest_path)
    run_state = RunState(
        run_id=manifest["run_id"],
        manifest_path=str(manifest_path),
        created_at=time.time(),
    )
    run_state.init_nodes(manifest)
    store.create(run_state)
    return manifest["run_id"], manifest_path


# ---------------------------------------------------------------------------
# B1: BRIEF_PREAMBLE content
# ---------------------------------------------------------------------------

class TestBriefPreamble:
    def test_non_empty(self):
        """BRIEF_PREAMBLE must be a non-empty string."""
        assert isinstance(BRIEF_PREAMBLE, str)
        assert len(BRIEF_PREAMBLE) > 100

    def test_role_framing(self):
        """Contains role framing language."""
        assert "crew subagent" in BRIEF_PREAMBLE
        assert "one node" in BRIEF_PREAMBLE.lower() or "ONE node" in BRIEF_PREAMBLE

    def test_instance_boundary(self):
        """Contains instance boundary rule."""
        assert "~/vault" in BRIEF_PREAMBLE
        assert "source_dir" in BRIEF_PREAMBLE

    def test_anti_fabrication(self):
        """Contains anti-fabrication marker."""
        assert "charter" in BRIEF_PREAMBLE.lower()
        assert "fabricat" in BRIEF_PREAMBLE.lower()

    def test_structured_return_contract(self):
        """Contains STRUCTURED-RETURN contract with ⟦RETURN⟧ schema."""
        assert "RETURN" in BRIEF_PREAMBLE
        assert "rv dag complete" in BRIEF_PREAMBLE
        assert "did:" in BRIEF_PREAMBLE
        assert "outcome:" in BRIEF_PREAMBLE
        assert "provenance:" in BRIEF_PREAMBLE


# ---------------------------------------------------------------------------
# B2/B3: diagnose-first block gating on attempts
# ---------------------------------------------------------------------------

class TestDiagnoseBlock:
    def test_no_diagnose_on_first_attempt(self, tmp_cfg: Config, tmp_path: Path):
        """attempts==0 must NOT render the diagnose-first block."""
        node = _make_agent_node(spec="Run the experiment.")
        brief = build_brief(
            node=node,
            node_state={},
            cfg=tmp_cfg,
            run_id="r1",
            project_root=tmp_path,
        )
        assert "DIAGNOSE FIRST" not in brief
        # The preamble mentions D-RETRY-9 in the return contract, so "RETRY" alone
        # is not a valid signal — check specifically for the diagnose block header.
        assert "=== DIAGNOSE FIRST" not in brief

    def test_diagnose_on_retry(self, tmp_cfg: Config, tmp_path: Path):
        """attempts>0 MUST render the diagnose-first block with last_failure."""
        node = _make_agent_node(spec="Run the experiment.", max_retries=2)
        node_state = {
            "attempts": 1,
            "last_failure": "The job ran out of memory on the GPU node.",
        }
        brief = build_brief(
            node=node,
            node_state=node_state,
            cfg=tmp_cfg,
            run_id="r1",
            project_root=tmp_path,
        )
        assert "DIAGNOSE FIRST" in brief
        assert "The job ran out of memory on the GPU node." in brief
        assert "RETRY" in brief

    def test_retry_block_reuses_directive(self, tmp_cfg: Config, tmp_path: Path):
        """The retry block must REUSE RETRY_DIAGNOSIS_DIRECTIVE (format compatibility)."""
        node = _make_agent_node(spec="Do the thing.", max_retries=1)
        node_state = {"attempts": 1, "last_failure": "timeout"}
        brief = build_brief(
            node=node,
            node_state=node_state,
            cfg=tmp_cfg,
            run_id="r1",
            project_root=tmp_path,
        )
        # RETRY_DIAGNOSIS_DIRECTIVE contains "blind-repeat" and "root-cause"
        assert "blind-repeat" in brief or "blind repeat" in brief.lower()
        assert "root-cause" in brief or "root cause" in brief.lower()

    def test_retry_domain_tips_str(self, tmp_cfg: Config, tmp_path: Path):
        """retry_diagnosis_tips (str) appears in the retry block."""
        node = _make_agent_node(spec="Run.", max_retries=1)
        node["retry_diagnosis_tips"] = "Check the GPU allocation before re-submitting."
        node_state = {"attempts": 1, "last_failure": "OOM"}
        brief = build_brief(
            node=node, node_state=node_state, cfg=tmp_cfg, run_id="r1",
            project_root=tmp_path,
        )
        assert "Check the GPU allocation before re-submitting." in brief

    def test_retry_domain_tips_list(self, tmp_cfg: Config, tmp_path: Path):
        """retry_diagnosis_tips (list) each item appears in the retry block."""
        node = _make_agent_node(spec="Run.", max_retries=2)
        node["retry_diagnosis_tips"] = ["Check GPU.", "Verify conda env."]
        node_state = {"attempts": 2, "last_failure": "killed"}
        brief = build_brief(
            node=node, node_state=node_state, cfg=tmp_cfg, run_id="r1",
            project_root=tmp_path,
        )
        assert "Check GPU." in brief
        assert "Verify conda env." in brief


# ---------------------------------------------------------------------------
# B4: spec verbatim
# ---------------------------------------------------------------------------

class TestSpecVerbatim:
    def test_spec_verbatim_in_brief(self, tmp_cfg: Config, tmp_path: Path):
        """The node's spec: text must appear verbatim in the brief."""
        spec_text = (
            "Run experiment q1-main1.\n\n"
            "Step 1: read the plan.\nStep 2: execute.\nStep 3: record provenance."
        )
        node = _make_agent_node(spec=spec_text)
        brief = build_brief(
            node=node, node_state={}, cfg=tmp_cfg, run_id="r1", project_root=tmp_path
        )
        assert spec_text in brief

    def test_spec_section_header(self, tmp_cfg: Config, tmp_path: Path):
        """SPEC section header is present."""
        node = _make_agent_node(spec="Do the thing.")
        brief = build_brief(
            node=node, node_state={}, cfg=tmp_cfg, run_id="r1", project_root=tmp_path
        )
        assert "=== SPEC" in brief

    def test_no_spec_declared(self, tmp_cfg: Config, tmp_path: Path):
        """A node with no spec yields a safe placeholder (not an empty brief)."""
        node = {"id": "no-spec", "type": "agent", "label": "no spec node"}
        brief = build_brief(
            node=node, node_state={}, cfg=tmp_cfg, run_id="r1", project_root=tmp_path
        )
        assert "no spec declared" in brief


# ---------------------------------------------------------------------------
# B5: reads paths in CONTEXT
# ---------------------------------------------------------------------------

class TestReadsInContext:
    def test_reads_paths_absolute(self, tmp_cfg: Config, tmp_path: Path):
        """reads: file paths are resolved to ABSOLUTE paths in the CONTEXT block."""
        doc = tmp_path / "doc.md"
        doc.write_text("# heading\n## section\n", encoding="utf-8")
        node = _make_agent_node(reads=[str(doc)])
        brief = build_brief(
            node=node, node_state={}, cfg=tmp_cfg, run_id="r1", project_root=tmp_path
        )
        assert str(doc) in brief
        assert "reads" in brief.lower()

    def test_reads_relative_resolved(self, tmp_cfg: Config, tmp_path: Path):
        """Relative reads: path is resolved via project_root."""
        doc = tmp_path / "notes" / "doc.md"
        doc.parent.mkdir(exist_ok=True)
        doc.write_text("# section\n", encoding="utf-8")
        node = _make_agent_node(reads=["notes/doc.md"])
        brief = build_brief(
            node=node, node_state={}, cfg=tmp_cfg, run_id="r1", project_root=tmp_path
        )
        assert str(doc) in brief

    def test_no_reads_declared(self, tmp_cfg: Config, tmp_path: Path):
        """No reads: field → 'reads: (none declared)' in CONTEXT."""
        node = _make_agent_node()  # no reads
        brief = build_brief(
            node=node, node_state={}, cfg=tmp_cfg, run_id="r1", project_root=tmp_path
        )
        assert "none declared" in brief


# ---------------------------------------------------------------------------
# B6: produces paths in CONTEXT
# ---------------------------------------------------------------------------

class TestProducesInContext:
    def test_produces_note_path_in_context(self, tmp_cfg: Config, tmp_path: Path):
        """produces.note path appears in CONTEXT block."""
        node = _make_agent_node(produces={"note": "experiments/q1.md"})
        brief = build_brief(
            node=node, node_state={}, cfg=tmp_cfg, run_id="r1", project_root=tmp_path
        )
        assert "experiments/q1.md" in brief
        assert "produces" in brief.lower()

    def test_no_produces_declared(self, tmp_cfg: Config, tmp_path: Path):
        """No produces field → 'none declared' in CONTEXT."""
        node = _make_agent_node()
        brief = build_brief(
            node=node, node_state={}, cfg=tmp_cfg, run_id="r1", project_root=tmp_path
        )
        assert "none declared" in brief


# ---------------------------------------------------------------------------
# B7: determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_inputs_byte_identical(self, tmp_cfg: Config, tmp_path: Path):
        """Same inputs → byte-identical output (pure function, no random state)."""
        node = _make_agent_node(
            spec="Run the experiment.",
            reads=["some/path.md"],
            produces={"note": "experiments/q1.md"},
        )
        node_state = {"attempts": 1, "last_failure": "OOM", "attempts": 1}
        kwargs = dict(
            node=node, node_state=node_state, cfg=tmp_cfg,
            run_id="r-123", project_root=tmp_path,
        )
        assert build_brief(**kwargs) == build_brief(**kwargs)

    def test_context_contains_run_and_node_id(self, tmp_cfg: Config, tmp_path: Path):
        """CONTEXT block contains run_id and node_id."""
        node = _make_agent_node(nid="my-node")
        brief = build_brief(
            node=node, node_state={}, cfg=tmp_cfg,
            run_id="my-run-42", project_root=tmp_path,
        )
        assert "my-run-42" in brief
        assert "my-node" in brief


# ---------------------------------------------------------------------------
# B8/B9/B10: cmd_brief CLI
# ---------------------------------------------------------------------------

class TestCmdBrief:
    def test_cmd_brief_emits_brief(self, tmp_instance: Path, capsys):
        """cmd_brief on a valid agent node exits 0 and prints a brief."""
        node = _make_agent_node(nid="n1", spec="Execute step 1.")
        manifest = _make_manifest([node], run_id="run-brief-test")
        run_id, manifest_path = _start_run(tmp_instance, manifest)

        args = argparse.Namespace(run_id=run_id, node_id="n1")
        rc = cmd_brief(args)
        assert rc == 0
        out, _ = capsys.readouterr()
        assert "DISPATCH BRIEF" in out
        assert "Execute step 1." in out

    def test_cmd_brief_human_go_node_errors(self, tmp_instance: Path, capsys):
        """cmd_brief on a human-go node exits 1 with informative stderr."""
        human_node = {"id": "hg1", "type": "human-go", "label": "Gate", "needs": []}
        agent_node = _make_agent_node(nid="n1", spec="Do step 1.")
        manifest = _make_manifest([agent_node, human_node], run_id="run-hg-test")
        run_id, _ = _start_run(tmp_instance, manifest)

        args = argparse.Namespace(run_id=run_id, node_id="hg1")
        rc = cmd_brief(args)
        assert rc == 1
        _, err = capsys.readouterr()
        assert "human-go" in err

    def test_cmd_brief_unknown_node_errors(self, tmp_instance: Path, capsys):
        """cmd_brief on an unknown node id exits 1 with informative stderr."""
        node = _make_agent_node(nid="n1", spec="Do step 1.")
        manifest = _make_manifest([node], run_id="run-unk-test")
        run_id, _ = _start_run(tmp_instance, manifest)

        args = argparse.Namespace(run_id=run_id, node_id="does-not-exist")
        rc = cmd_brief(args)
        assert rc == 1
        _, err = capsys.readouterr()
        assert "does-not-exist" in err


# ---------------------------------------------------------------------------
# G1: Golden-file test — experiment loop node
# ---------------------------------------------------------------------------

class TestGoldenExperiment:
    """Golden-file structure test for an experiment-loop node."""

    def _build_experiment_node(self, tmp_path: Path) -> tuple[dict, Path]:
        """Build a brief-grade experiment run node and a project_root."""
        # This is what experiment.py now emits for a main-run node
        exp_id = "q1"
        main_id = "q1-main1"
        notes_dir = tmp_path / "notes" / "demo-research"
        notes_dir.mkdir(parents=True)
        (notes_dir / "experiments").mkdir(exist_ok=True)

        node = {
            "id": f"{main_id}-run",
            "type": "agent",
            "label": f"Run Main 1 — {main_id} (researcher)",
            "role": "researcher",
            "spec": (
                f"Run the pre-registered Main 1 experiment: {main_id}.\n\n"
                f"Research question: Does prompt language drive cross-lingual accuracy?\n\n"
                f"Your task:\n"
                f"1. Read the pre-registration plan note (experiments/{exp_id}-plan.md).\n"
                f"2. Execute the run.\n"
                f"3. Record provenance in experiments/{main_id}.md.\n"
                f"4. rv dag complete <run_id> {main_id}-run"
            ),
            "produces": {"note": f"experiments/{main_id}.md"},
            "reads": [str(notes_dir / "experiments")],
        }
        return node, tmp_path

    def test_golden_experiment_brief_structure(self, tmp_cfg: Config, tmp_path: Path):
        """Experiment loop brief contains all required structural sections."""
        node, project_root = self._build_experiment_node(tmp_path)
        brief = build_brief(
            node=node,
            node_state={},
            cfg=tmp_cfg,
            run_id="q1-loop",
            project_root=project_root,
        )
        # All four required sections
        assert "=== RESEARCH VAULT — DISPATCH BRIEF ===" in brief
        assert "=== SPEC (VERBATIM" in brief
        assert "=== CONTEXT ===" in brief
        # SPEC is verbatim
        assert "Run the pre-registered Main 1 experiment:" in brief
        # Context has run_id + node_id
        assert "q1-loop" in brief
        assert "q1-main1-run" in brief
        # No diagnose block on first attempt
        assert "DIAGNOSE FIRST" not in brief

    def test_golden_experiment_brief_retry(self, tmp_cfg: Config, tmp_path: Path):
        """Experiment loop brief on retry has the diagnose-first block."""
        node, project_root = self._build_experiment_node(tmp_path)
        node["max_retries"] = 1
        node_state = {"attempts": 1, "last_failure": "SLURM job killed (OOM)"}
        brief = build_brief(
            node=node,
            node_state=node_state,
            cfg=tmp_cfg,
            run_id="q1-loop",
            project_root=project_root,
        )
        assert "DIAGNOSE FIRST" in brief
        assert "SLURM job killed (OOM)" in brief
        assert "=== SPEC (VERBATIM" in brief


# ---------------------------------------------------------------------------
# G2: Golden-file test — lit-review loop node
# ---------------------------------------------------------------------------

class TestGoldenLitReview:
    """Golden-file structure test for a lit-review loop node."""

    def _build_litreview_node(self, tmp_path: Path) -> tuple[dict, Path]:
        """Build a Phase-1 review-scope node (as emitted by review scaffolder)."""
        notes_dir = tmp_path / "notes" / "demo-litreview"
        notes_dir.mkdir(parents=True)
        review_dir = notes_dir / "reviews" / "xling"
        review_dir.mkdir(parents=True)
        (notes_dir / "literature").mkdir(exist_ok=True)
        (notes_dir / "concepts").mkdir(exist_ok=True)

        protocol_path = str(review_dir / "_protocol.md")

        # spec is an inline body (as emitted by _spec() in review/__init__.py)
        spec_text = (
            "Freeze the review protocol.\n\n"
            "1. Define the research question exactly.\n"
            "2. List inclusion/exclusion criteria.\n"
            "3. Declare a counter-position (required, L-2 gate).\n"
            "4. Write _protocol.md.\n"
            "5. rv dag complete <run_id> review-scope"
        )

        node = {
            "id": "review-scope",
            "type": "agent",
            "label": "Freeze review protocol",
            "spec": spec_text,
            "reads": [
                str(notes_dir / "concepts"),
                str(notes_dir / "literature"),
            ],
            "produces": {"_protocol.md": protocol_path},
        }
        return node, tmp_path

    def test_golden_litreview_brief_structure(self, tmp_cfg: Config, tmp_path: Path):
        """Lit-review brief contains all required structural sections."""
        node, project_root = self._build_litreview_node(tmp_path)
        brief = build_brief(
            node=node,
            node_state={},
            cfg=tmp_cfg,
            run_id="review-xling-phase1",
            project_root=project_root,
        )
        assert "=== RESEARCH VAULT — DISPATCH BRIEF ===" in brief
        assert "=== SPEC (VERBATIM" in brief
        assert "=== CONTEXT ===" in brief
        assert "Freeze the review protocol." in brief
        assert "review-xling-phase1" in brief
        assert "review-scope" in brief
        assert "DIAGNOSE FIRST" not in brief

    def test_golden_litreview_reads_absolute(self, tmp_cfg: Config, tmp_path: Path):
        """Lit-review brief shows absolute paths for reads:."""
        node, project_root = self._build_litreview_node(tmp_path)
        notes_dir = tmp_path / "notes" / "demo-litreview"
        brief = build_brief(
            node=node,
            node_state={},
            cfg=tmp_cfg,
            run_id="review-xling-phase1",
            project_root=project_root,
        )
        assert str(notes_dir / "concepts") in brief
        assert str(notes_dir / "literature") in brief


# ---------------------------------------------------------------------------
# S1/S2: SSOT tests
# ---------------------------------------------------------------------------

class TestSSOT:
    def test_produces_note_ssot(self, tmp_cfg: Config, tmp_path: Path):
        """resolve_produces_paths returns the same path cmd_complete would gate on."""
        # The note root is tmp_cfg.notes_root
        note_rel = "experiments/q1.md"
        node = _make_agent_node(produces={"note": note_rel})
        paths = resolve_produces_paths(node, tmp_cfg)
        assert len(paths) == 1
        expected = tmp_cfg.notes_root / note_rel
        assert paths[0] == expected

    def test_resolve_reads_paths_absolute_for_existing_file(
        self, tmp_cfg: Config, tmp_path: Path
    ):
        """resolve_reads_paths returns absolute paths for existing files."""
        doc = tmp_path / "notes" / "doc.md"
        doc.write_text("# heading\n", encoding="utf-8")
        node = _make_agent_node(reads=[str(doc)])
        paths = resolve_reads_paths(node, project_root=tmp_path)
        assert len(paths) == 1
        assert paths[0] == str(doc)
        assert "(unresolved)" not in paths[0]

    def test_resolve_reads_paths_unresolved_surfaced(
        self, tmp_cfg: Config, tmp_path: Path
    ):
        """Missing file yields an '(unresolved)' entry — not silently dropped."""
        node = _make_agent_node(reads=["does/not/exist.md"])
        paths = resolve_reads_paths(node, project_root=tmp_path)
        assert len(paths) == 1
        assert "(unresolved)" in paths[0]

    def test_produces_ssot_gate_matches_brief(self, tmp_cfg: Config, tmp_path: Path):
        """The path in the brief's CONTEXT == the path cmd_complete would check."""
        node = _make_agent_node(produces={"note": "experiments/q1.md"})
        # The brief and cmd_complete both use resolve_produces_paths
        paths_from_ssot = resolve_produces_paths(node, tmp_cfg)
        brief = build_brief(
            node=node, node_state={}, cfg=tmp_cfg, run_id="r1", project_root=tmp_path
        )
        for p in paths_from_ssot:
            assert str(p) in brief

    def test_gate_path_equals_brief_path_by_construction(self, tmp_path: Path):
        """PIN: gate-resolved produces.result path == build_brief's path BY CONSTRUCTION.

        Both _check_project_scoped_note (the cmd_complete gate) and
        resolve_produces_paths (used by build_brief) call _project_scoped_note_path.
        This test proves the IDENTICAL path is computed — not just asserted by docstring.
        """
        from research_vault.dag.verbs import _project_scoped_note_path

        proj_dir = tmp_path / "projects" / "my-proj"
        proj_dir.mkdir(parents=True)
        notes_dir = tmp_path / "notes" / "my-proj"
        notes_dir.mkdir(parents=True)
        (notes_dir / "experiments").mkdir(exist_ok=True)
        (tmp_path / "state").mkdir(exist_ok=True)

        raw = {
            "instance_root": str(tmp_path),
            "notes_root": str(tmp_path / "notes"),
            "state_dir": str(tmp_path / "state"),
            "agents_dir": str(tmp_path / ".agents"),
            "tasks_dir": str(tmp_path / "tasks"),
            "control_dir": str(tmp_path / "control"),
            "adapters": {"notifier": "file", "backend": "local", "secrets": "env"},
            "projects": {
                "my-proj": {
                    "source_dir": str(proj_dir),
                    "tasks_dir": str(tmp_path / "tasks" / "my-proj"),
                },
            },
        }
        cfg = Config(raw)

        note_ref = "my-proj/q1-main1"
        pkey = "result"

        # Path from the shared primitive (what _check_project_scoped_note uses)
        gate_path = _project_scoped_note_path(pkey, note_ref, cfg)

        # Path from resolve_produces_paths (what build_brief uses)
        node = _make_agent_node(produces={pkey: note_ref})
        brief_paths = resolve_produces_paths(node, cfg)

        assert len(brief_paths) == 1, f"Expected 1 path, got: {brief_paths}"
        assert brief_paths[0] == gate_path, (
            f"SSOT BROKEN: gate_path={gate_path} != brief_path={brief_paths[0]}"
        )

    def test_parse_pointer_ssot_no_inline_scheme_tuples(self):
        """PIN: resolve_reads_paths must NOT contain an inline scheme tuple.

        The pointer grammar (scheme detection, anchor split) lives in
        _parse_pointer ONLY.  Both resolve_reads_pointer and resolve_reads_paths
        must call it.  We verify by checking that no tuple with "http" as a
        first element appears in the AST of resolve_reads_paths.
        """
        import inspect
        import ast
        from research_vault.dag import reads as reads_mod

        assert hasattr(reads_mod, "_URL_SCHEMES"), "_URL_SCHEMES SSOT constant missing"
        assert isinstance(reads_mod._URL_SCHEMES, frozenset)
        assert "http" in reads_mod._URL_SCHEMES

        src = inspect.getsource(reads_mod.resolve_reads_paths)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Tuple):
                if node.elts and isinstance(node.elts[0], ast.Constant):
                    if node.elts[0].value == "http":
                        raise AssertionError(
                            "resolve_reads_paths contains an inline scheme tuple "
                            "— violates _parse_pointer SSOT"
                        )

    def test_manifest_project_field_uses_manifest_source_dir(self, tmp_path: Path):
        """PIN: build_brief uses the manifest's 'project' field to resolve source_dir.

        When a manifest declares 'project: <slug>', the CONTEXT block must show
        THAT project's source_dir — not projects[0]'s source_dir (inference fallback).
        A two-project instance proves we pick the right one.
        """
        proj_a_dir = tmp_path / "projects" / "proj-a"
        proj_b_dir = tmp_path / "projects" / "proj-b"
        proj_a_dir.mkdir(parents=True)
        proj_b_dir.mkdir(parents=True)
        (tmp_path / "state").mkdir(exist_ok=True)

        raw = {
            "instance_root": str(tmp_path),
            "notes_root": str(tmp_path / "notes"),
            "state_dir": str(tmp_path / "state"),
            "agents_dir": str(tmp_path / ".agents"),
            "tasks_dir": str(tmp_path / "tasks"),
            "control_dir": str(tmp_path / "control"),
            "adapters": {"notifier": "file", "backend": "local", "secrets": "env"},
            "projects": {
                "proj-a": {"source_dir": str(proj_a_dir), "tasks_dir": str(tmp_path / "tasks")},
                "proj-b": {"source_dir": str(proj_b_dir), "tasks_dir": str(tmp_path / "tasks")},
            },
        }
        cfg = Config(raw)

        # Node belongs to proj-b (NOT the first registered project proj-a)
        node = _make_agent_node(spec="Run analysis for proj-b.")
        # We pass manifest_project explicitly so brief.py uses the manifest field
        brief = build_brief(
            node=node,
            node_state={},
            cfg=cfg,
            run_id="r1",
            project_root=tmp_path,
            manifest_project="proj-b",
        )
        # The CONTEXT must show proj-b's source_dir
        assert str(proj_b_dir) in brief, "proj-b source_dir not in brief CONTEXT"
        # And must NOT show proj-a's source_dir (the inference fallback target)
        assert str(proj_a_dir) not in brief, (
            "proj-a source_dir appeared — inference branch used instead of manifest field"
        )


# ---------------------------------------------------------------------------
# E1/E2: experiment.py task:// purge
# ---------------------------------------------------------------------------

class TestExperimentNoBareTaskPointers:
    def _scaffold_manifest(self, tmp_path: Path) -> dict:
        """Scaffold an experiment manifest and return it."""
        from research_vault.experiment import cmd_new
        proj_dir = tmp_path / "projects" / "proj1"
        proj_dir.mkdir(parents=True)
        notes_dir = tmp_path / "notes" / "proj1"
        notes_dir.mkdir(parents=True)
        (notes_dir / "experiments").mkdir(exist_ok=True)

        raw = {
            "instance_root": str(tmp_path),
            "notes_root": str(tmp_path / "notes"),
            "state_dir": str(tmp_path / "state"),
            "agents_dir": str(tmp_path / ".agents"),
            "tasks_dir": str(tmp_path / "tasks"),
            "control_dir": str(tmp_path / "control"),
            "adapters": {"notifier": "file", "backend": "local", "secrets": "env"},
            "projects": {
                "proj1": {
                    "source_dir": str(proj_dir),
                    "tasks_dir": str(tmp_path / "tasks" / "proj1"),
                },
            },
        }
        (tmp_path / "state").mkdir(exist_ok=True)
        cfg = Config(raw)
        _, manifest_path = cmd_new(
            project="proj1",
            exp_id="test-exp",
            question="Does X cause Y?",
            n_mains=1,
            config=cfg,
        )
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    def test_no_task_pointer_in_specs(self, tmp_path: Path):
        """No node in the scaffolded manifest has spec starting with 'task://'."""
        manifest = self._scaffold_manifest(tmp_path)
        for node in manifest["nodes"]:
            spec = node.get("spec", "")
            assert not spec.startswith("task://"), (
                f"Node {node['id']!r} still has bare task:// spec: {spec[:80]}"
            )

    def test_all_agent_nodes_have_non_empty_spec(self, tmp_path: Path):
        """All agent nodes have a non-empty spec (brief-grade or doc-pointer)."""
        manifest = self._scaffold_manifest(tmp_path)
        for node in manifest["nodes"]:
            if node.get("type", "agent") == "agent":
                spec = node.get("spec", "")
                assert spec, f"Agent node {node['id']!r} has empty spec"
                # The task:// placeholder pattern is what we're removing.
                # Doc-pointer specs like "doctrine/plan-critic-spec.md" are valid.
                assert not spec.startswith("task://"), (
                    f"Agent node {node['id']!r} still uses bare task:// pointer: {spec!r}"
                )

    def test_scaffolded_manifest_has_project_field(self, tmp_path: Path):
        """PIN (BLOCK-2): scaffolded experiment manifest declares 'project' field.

        Without this field, build_brief falls into the inference branch (projects[0])
        which prints the WRONG source_dir in a multi-project instance.  The manifest
        must carry an explicit 'project' key so build_brief can use it directly.
        """
        manifest = self._scaffold_manifest(tmp_path)
        assert "project" in manifest, (
            "Scaffolded manifest missing 'project' field — build_brief will use "
            "inference (projects[0]) instead of the declared project"
        )
        assert manifest["project"] == "proj1"

    def test_scaffolded_manifest_project_used_in_brief(self, tmp_path: Path):
        """PIN (BLOCK-2): build_brief on the scaffolded manifest uses manifest project.

        Uses a two-project instance to prove the right source_dir is shown.
        """
        from research_vault.experiment import cmd_new

        proj_dir_a = tmp_path / "projects" / "other-proj"
        proj_dir_b = tmp_path / "projects" / "real-proj"
        proj_dir_a.mkdir(parents=True)
        proj_dir_b.mkdir(parents=True)
        (tmp_path / "notes" / "real-proj" / "experiments").mkdir(parents=True)
        (tmp_path / "state").mkdir(exist_ok=True)

        raw = {
            "instance_root": str(tmp_path),
            "notes_root": str(tmp_path / "notes"),
            "state_dir": str(tmp_path / "state"),
            "agents_dir": str(tmp_path / ".agents"),
            "tasks_dir": str(tmp_path / "tasks"),
            "control_dir": str(tmp_path / "control"),
            "adapters": {"notifier": "file", "backend": "local", "secrets": "env"},
            "projects": {
                "other-proj": {
                    "source_dir": str(proj_dir_a),
                    "tasks_dir": str(tmp_path / "tasks"),
                },
                "real-proj": {
                    "source_dir": str(proj_dir_b),
                    "tasks_dir": str(tmp_path / "tasks"),
                },
            },
        }
        cfg = Config(raw)

        _, manifest_path = cmd_new(
            project="real-proj",
            exp_id="test-exp",
            question="Does X cause Y?",
            n_mains=1,
            config=cfg,
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        # Pick first agent node to build brief
        agent_node = next(n for n in manifest["nodes"] if n.get("type", "agent") == "agent")
        manifest_project = manifest.get("project")

        brief = build_brief(
            node=agent_node,
            node_state={},
            cfg=cfg,
            run_id=manifest["run_id"],
            project_root=tmp_path,
            manifest_project=manifest_project,
        )
        # Must show real-proj's source_dir (proj_dir_b)
        assert str(proj_dir_b) in brief, (
            f"real-proj source_dir ({proj_dir_b}) not in brief — wrong project used"
        )
        # Must NOT show other-proj's source_dir (inference fallback would use projects[0])
        assert str(proj_dir_a) not in brief, (
            f"other-proj source_dir appeared — inference branch fired instead of manifest field"
        )

    def test_no_task_dereferencer_added(self):
        """No task:// dereferencer mechanism was added to the dag package."""
        import research_vault.dag.reads as reads_mod
        import research_vault.dag.schema as schema_mod
        import research_vault.dag.walker as walker_mod
        import research_vault.dag.verbs as verbs_mod
        import research_vault.dag.brief as brief_mod
        for mod in (reads_mod, schema_mod, walker_mod, verbs_mod, brief_mod):
            import inspect
            src = inspect.getsource(mod)
            # Should NOT have a 'task://' dereferencer / resolver
            # (task:// in schema.py only appears in the docstring/comment example)
            # We check that no function is NAMED to handle task:// URLs
            assert "dereference_task" not in src
            assert "resolve_task_url" not in src
            assert "task_resolver" not in src


# ---------------------------------------------------------------------------
# Parser test: brief subcommand is registered
# ---------------------------------------------------------------------------

class TestBriefParser:
    def test_brief_subcommand_parseable(self):
        """rv dag brief <run_id> <node_id> parses without error."""
        from research_vault.dag.verbs import build_parser
        p = build_parser()
        args = p.parse_args(["brief", "my-run", "my-node"])
        assert args.dag_cmd == "brief"
        assert args.run_id == "my-run"
        assert args.node_id == "my-node"

    def test_brief_in_verb_registry(self):
        """'dag' entry in _VERB_REGISTRY mentions 'brief'."""
        from research_vault.cli import _VERB_REGISTRY
        dag_entry = _VERB_REGISTRY.get("dag", {})
        when_to_use = dag_entry.get("when_to_use", "")
        assert "brief" in when_to_use

    def test_brief_anti_pattern_in_registry(self):
        """_VERB_REGISTRY dag entry has the anti-pattern for hand-rolling."""
        from research_vault.cli import _VERB_REGISTRY
        dag_entry = _VERB_REGISTRY.get("dag", {})
        when_to_use = dag_entry.get("when_to_use", "")
        assert "hand" in when_to_use.lower()
