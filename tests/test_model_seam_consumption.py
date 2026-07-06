"""test_model_seam_consumption.py — SR-MODEL-SEAM S4: the seam is NAMED where crew look.

Guards the consumption surfaces so a harness engineer discovers the ModelClient
seam at dispatch time, not just in doctrine:
  - the harness engineer specs (per-main + shared) require the seam;
  - `rv dag brief` on a harness node NAMES the seam (spec is verbatim in the brief);
  - the shipped docs (compute-run-recipe.md, harness-contract.md) name the seam +
    the anti-pattern.
"""
from __future__ import annotations

import importlib.resources as ir
from pathlib import Path

from research_vault.experiment import (
    _build_experiment_manifest,
    _harness_engineer_spec,
)


# ---------------------------------------------------------------------------
# The harness specs require the ModelClient seam
# ---------------------------------------------------------------------------

def test_per_main_harness_spec_names_the_seam():
    spec = _harness_engineer_spec("exp-main1", 1, "exp")
    assert "adapters.model.complete" in spec
    assert "ZERO observability records" in spec
    # Anti-pattern named:
    assert "hand-roll" in spec.lower()
    assert "rv observability probe" in spec


def test_shared_harness_spec_names_the_seam(tmp_path):
    manifest = _build_experiment_manifest(
        project="demo",
        exp_id="exp",
        question="does X beat Y?",
        n_mains=2,
        plan_note_path=tmp_path / "plan.md",
        notes_dir=tmp_path / "notes",
        shared_harness=True,
    )
    shared = next(n for n in manifest["nodes"] if n["id"] == "shared-harness")
    assert "load_adapters(cfg).model.complete" in shared["spec"]
    assert "ZERO observability records" in shared["spec"]


# ---------------------------------------------------------------------------
# rv dag brief on a harness node NAMES the seam (coordinator add #3)
# ---------------------------------------------------------------------------

def test_dag_brief_on_harness_node_names_the_seam(tmp_path):
    from research_vault.config import Config
    from research_vault.dag.brief import build_brief

    manifest = _build_experiment_manifest(
        project="demo",
        exp_id="exp",
        question="does X beat Y?",
        n_mains=1,
        plan_note_path=tmp_path / "plan.md",
        notes_dir=tmp_path / "notes",
        shared_harness=False,
    )
    harness_node = next(n for n in manifest["nodes"] if n["id"] == "exp-main1-harness")

    cfg = Config({
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(tmp_path / "state"),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "projects": {"demo": {"source_dir": str(tmp_path / "notes" / "demo")}},
    })
    brief = build_brief(harness_node, {}, cfg, "exp-loop", tmp_path, "demo")
    # The spec is emitted verbatim into the brief → the seam is named at dispatch.
    assert "adapters.model.complete" in brief
    assert "hand-roll" in brief.lower()


# ---------------------------------------------------------------------------
# Shipped docs name the seam + the anti-pattern
# ---------------------------------------------------------------------------

def _doctrine_text(name: str) -> str:
    root = ir.files("research_vault") / "data" / "doctrine" / name
    return Path(str(root)).read_text(encoding="utf-8")


def test_compute_run_recipe_names_seam_and_planes():
    text = _doctrine_text("compute-run-recipe.md")
    assert "adapters.model.complete" in text
    assert "load_adapters" in text
    # traces ≠ runs must be stated (don't let a reader assume weave → rv wandb pull)
    assert "traces" in text.lower() and "runs" in text.lower()
    assert "rv observability probe" in text
    # anti-pattern named
    assert "ZERO observability records" in text


def test_harness_contract_requires_seam():
    text = _doctrine_text("harness-contract.md")
    assert "ModelClient" in text
    assert "fails review" in text
    assert "hand-rolled" in text.lower() or "hand-roll" in text.lower()
