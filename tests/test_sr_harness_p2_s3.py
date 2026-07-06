"""test_sr_harness_p2_s3.py — SR-HARNESS-P2 Slice 3 acceptance tests.

Covers experiment.py topology changes:
  S3a. cmd_new(n_mains=2) → manifest has harness triple per main:
       - {main_id}-harness, {main_id}-harness-review, human-go-harness-main{k}
  S3b. {main_id}-run.needs references human-go-harness-main{k} NOT human-go-plan
  S3c. plan+watch stub-freshness edge intact on run nodes
  S3d. validate_manifest passes on the harness-extended manifest
  S3e. --shared-harness → exactly ONE triple (shared-harness, shared-harness-review,
       human-go-harness-shared); both mains' run/abl-A-run depend on shared gate
  S3f. --shared-harness validates
  S3g. Existing two-mains / human-go-plan / human-go-findings tests still pass
  S3h. --shared-harness CLI flag registered in build_parser
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.dag.schema import validate_manifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_instance(tmp_path: Path) -> object:
    cfg_path = tmp_path / "research_vault.toml"
    proj_dir = tmp_path / "projects" / "demo-research"
    proj_dir.mkdir(parents=True)
    notes = tmp_path / "notes"
    notes.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    cfg_path.write_text(
        f'instance_root = "{tmp_path}"\n'
        f'notes_root = "{notes}"\n'
        f'state_dir = "{state}"\n'
        f'agents_dir = "{tmp_path / ".agents"}"\n'
        f'tasks_dir = "{tmp_path / "tasks"}"\n'
        f'control_dir = "{tmp_path / "control"}"\n'
        '[adapters]\nnotifier = "file"\nbackend = "local"\nsecrets = "env"\n'
        f'\n[projects.demo-research]\nsource_dir = "{proj_dir}"\n',
        encoding="utf-8",
    )
    return cfg_path


@pytest.fixture
def instance(tmp_path, monkeypatch):
    cfg_path = _make_instance(tmp_path)
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_path))
    from research_vault.config import reset_config_cache
    reset_config_cache()
    yield tmp_path
    reset_config_cache()


# ===========================================================================
# S3a — harness triple per main in manifest
# ===========================================================================

class TestHarnessTriplePerMain:
    def test_two_mains_has_both_harness_triples(self, instance, tmp_path):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        _, manifest_path = cmd_new(
            "demo-research", "q1", question="test?", n_mains=2, config=cfg
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        node_ids = {n["id"] for n in manifest["nodes"]}

        # Main 1 harness triple
        assert "q1-main1-harness" in node_ids
        assert "q1-main1-harness-review" in node_ids
        assert "human-go-harness-main1" in node_ids

        # Main 2 harness triple
        assert "q1-main2-harness" in node_ids
        assert "q1-main2-harness-review" in node_ids
        assert "human-go-harness-main2" in node_ids

    def test_harness_nodes_have_correct_types(self, instance, tmp_path):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        _, manifest_path = cmd_new(
            "demo-research", "q2", question="test?", n_mains=1, config=cfg
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        by_id = {n["id"]: n for n in manifest["nodes"]}

        assert by_id["q2-main1-harness"]["type"] == "agent"
        assert by_id["q2-main1-harness"]["role"] == "engineer"
        assert by_id["q2-main1-harness-review"]["type"] == "agent"
        assert by_id["q2-main1-harness-review"]["role"] == "reviewer"
        assert by_id["human-go-harness-main1"]["type"] == "human-go"

    def test_harness_reads_doctrine(self, instance, tmp_path):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        _, manifest_path = cmd_new(
            "demo-research", "q3", question="test?", n_mains=1, config=cfg
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        by_id = {n["id"]: n for n in manifest["nodes"]}

        harness_reads = by_id["q3-main1-harness"].get("reads", [])
        review_reads = by_id["q3-main1-harness-review"].get("reads", [])
        assert any("harness-contract.md" in r for r in harness_reads)
        assert any("harness-contract.md" in r for r in review_reads)


# ===========================================================================
# S3b — run/abl-run depend on harness gate not human-go-plan
# ===========================================================================

class TestRunNodeRewired:
    def test_run_depends_on_harness_gate(self, instance, tmp_path):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        _, manifest_path = cmd_new(
            "demo-research", "q4", question="test?", n_mains=2, config=cfg
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        by_id = {n["id"]: n for n in manifest["nodes"]}

        for k in [1, 2]:
            main_id = f"q4-main{k}"
            run_node = by_id[f"{main_id}-run"]
            afterok_deps = [
                d["from"] for d in run_node["needs"]
                if d.get("edge") == "afterok" and "watch" not in d
            ]
            # Must depend on harness gate, NOT human-go-plan
            assert f"human-go-harness-main{k}" in afterok_deps, (
                f"{main_id}-run must depend on human-go-harness-main{k}"
            )
            assert "human-go-plan" not in afterok_deps, (
                f"{main_id}-run must NOT depend directly on human-go-plan"
            )

    def test_abl_run_depends_on_harness_gate(self, instance, tmp_path):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        _, manifest_path = cmd_new(
            "demo-research", "q5", question="test?", n_mains=2, config=cfg
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        by_id = {n["id"]: n for n in manifest["nodes"]}

        for k in [1, 2]:
            abl_id = f"q5-main{k}-abl-A"
            run_node = by_id[f"{abl_id}-run"]
            afterok_deps = [
                d["from"] for d in run_node["needs"]
                if d.get("edge") == "afterok" and "watch" not in d
            ]
            assert f"human-go-harness-main{k}" in afterok_deps
            assert "human-go-plan" not in afterok_deps


# ===========================================================================
# S3c — stub-freshness watch edge preserved
# ===========================================================================

class TestStubFreshnessEdgeIntact:
    def test_watch_edge_preserved_on_run(self, instance, tmp_path):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        _, manifest_path = cmd_new(
            "demo-research", "q6", question="test?", n_mains=1, config=cfg
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        by_id = {n["id"]: n for n in manifest["nodes"]}

        run_node = by_id["q6-main1-run"]
        watch_edges = [d for d in run_node["needs"] if "watch" in d]
        assert len(watch_edges) >= 1
        assert watch_edges[0]["from"] == "plan"
        assert "q6-main1.md" in watch_edges[0]["watch"]

    def test_watch_edge_preserved_on_abl_run(self, instance, tmp_path):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        _, manifest_path = cmd_new(
            "demo-research", "q7", question="test?", n_mains=1, config=cfg
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        by_id = {n["id"]: n for n in manifest["nodes"]}

        abl_run = by_id["q7-main1-abl-A-run"]
        watch_edges = [d for d in abl_run["needs"] if "watch" in d]
        assert len(watch_edges) >= 1
        assert watch_edges[0]["from"] == "plan"


# ===========================================================================
# S3d — validate_manifest passes
# ===========================================================================

class TestHarnessManifestValidates:
    def test_single_main_validates(self, instance, tmp_path):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        _, manifest_path = cmd_new(
            "demo-research", "val1", question="test?", n_mains=1, config=cfg
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        validate_manifest(manifest)  # should not raise

    def test_two_mains_validates(self, instance, tmp_path):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        _, manifest_path = cmd_new(
            "demo-research", "val2", question="test?", n_mains=2, config=cfg
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        validate_manifest(manifest)


# ===========================================================================
# S3e — --shared-harness: one triple, all mains depend on shared gate
# ===========================================================================

class TestSharedHarness:
    def test_shared_harness_single_triple(self, instance, tmp_path):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        _, manifest_path = cmd_new(
            "demo-research", "sh1", question="test?", n_mains=2,
            shared_harness=True, config=cfg
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        node_ids = {n["id"] for n in manifest["nodes"]}

        # Shared triple
        assert "shared-harness" in node_ids
        assert "shared-harness-review" in node_ids
        assert "human-go-harness-shared" in node_ids

        # No per-main harness triples
        assert "sh1-main1-harness" not in node_ids
        assert "sh1-main2-harness" not in node_ids
        assert "human-go-harness-main1" not in node_ids
        assert "human-go-harness-main2" not in node_ids

    def test_shared_harness_all_mains_depend_on_shared_gate(self, instance, tmp_path):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        _, manifest_path = cmd_new(
            "demo-research", "sh2", question="test?", n_mains=2,
            shared_harness=True, config=cfg
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        by_id = {n["id"]: n for n in manifest["nodes"]}

        for k in [1, 2]:
            main_id = f"sh2-main{k}"
            run_node = by_id[f"{main_id}-run"]
            afterok_deps = [
                d["from"] for d in run_node["needs"]
                if d.get("edge") == "afterok" and "watch" not in d
            ]
            assert "human-go-harness-shared" in afterok_deps
            assert "human-go-plan" not in afterok_deps

            abl_node = by_id[f"sh2-main{k}-abl-A-run"]
            abl_deps = [
                d["from"] for d in abl_node["needs"]
                if d.get("edge") == "afterok" and "watch" not in d
            ]
            assert "human-go-harness-shared" in abl_deps


# ===========================================================================
# S3f — --shared-harness validates
# ===========================================================================

class TestSharedHarnessValidates:
    def test_shared_harness_validates(self, instance, tmp_path):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        _, manifest_path = cmd_new(
            "demo-research", "shval", question="test?", n_mains=2,
            shared_harness=True, config=cfg
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        validate_manifest(manifest)


# ===========================================================================
# S3g — existing tests still pass (regression guards)
# ===========================================================================

class TestExistingTopologyPreserved:
    def test_human_go_plan_still_present(self, instance, tmp_path):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        _, manifest_path = cmd_new(
            "demo-research", "reg1", question="test?", n_mains=2, config=cfg
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        node_ids = {n["id"] for n in manifest["nodes"]}
        assert "human-go-plan" in node_ids

    def test_human_go_findings_still_present(self, instance, tmp_path):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        _, manifest_path = cmd_new(
            "demo-research", "reg2", question="test?", n_mains=2, config=cfg
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        node_ids = {n["id"] for n in manifest["nodes"]}
        assert "human-go-findings" in node_ids

    def test_human_go_conditionals_still_present(self, instance, tmp_path):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        _, manifest_path = cmd_new(
            "demo-research", "reg3", question="test?", n_mains=2, config=cfg
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        node_ids = {n["id"] for n in manifest["nodes"]}
        assert "human-go-conditionals-main1" in node_ids
        assert "human-go-conditionals-main2" in node_ids

    def test_methods_update_still_present(self, instance, tmp_path):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        _, manifest_path = cmd_new(
            "demo-research", "reg4", question="test?", n_mains=1, config=cfg
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        node_ids = {n["id"] for n in manifest["nodes"]}
        assert "methods-update" in node_ids

    def test_plan_note_still_has_preregistration_kind(self, instance, tmp_path):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        plan_path, _ = cmd_new(
            "demo-research", "reg5", question="test?", n_mains=1, config=cfg
        )
        text = plan_path.read_text(encoding="utf-8")
        assert "plan_kind: preregistration" in text

    def test_plan_note_no_prewritten_harness_commits(self, instance, tmp_path):
        """Frontmatter must NOT pre-write harness_commits: field.
        (Body prose may reference it by name, so we check the FM block only.)
        """
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        plan_path, _ = cmd_new(
            "demo-research", "reg6", question="test?", n_mains=1, config=cfg
        )
        text = plan_path.read_text(encoding="utf-8")
        # Extract frontmatter block (between opening --- and closing ---)
        parts = text.split("---", 2)
        fm_block = parts[1] if len(parts) >= 3 else ""
        assert "harness_commits:" not in fm_block, (
            "harness_commits: must not be pre-written in plan note frontmatter; "
            "it is written only by rv plan freeze-harness"
        )


# ===========================================================================
# S3h — --shared-harness CLI flag in build_parser
# ===========================================================================

class TestSharedHarnessFlag:
    def test_shared_harness_flag_in_parser(self):
        from research_vault.experiment import build_parser
        p = build_parser()
        args = p.parse_args([
            "demo-research", "new", "q1",
            "--question", "test?",
            "--shared-harness",
        ])
        assert args.shared_harness is True

    def test_shared_harness_default_false(self):
        from research_vault.experiment import build_parser
        p = build_parser()
        args = p.parse_args([
            "demo-research", "new", "q1",
            "--question", "test?",
        ])
        assert args.shared_harness is False
