"""test_pr2_pr3_type_taxonomy.py — 0.3.2 PR-2/PR-3: concepts joins the
shared-canonical partition, `methods` renames to `methodology`.

Covers (design 2026-07-12-rv-0.3.2-knowledge-graph-model-design.md §3 PR-2/PR-3):
  1. concepts_root config default + toml override (mirrors literature_root).
  2. cfg.bundle_registry() / cfg.shared_type_root() include concepts.
  3. OKF_SHARED_TYPES = {datasets, concepts}; OKF_TWO_LAYER_TYPES unchanged
     ({literature}) — the SSOT class-partition test (test_literature_store.py)
     stays green because the classes remain pairwise-disjoint.
  4. cmd_new/cmd_list/cmd_check route "concepts" to cfg.concepts_root, not
     project_notes_dir/concepts — mirroring the "datasets" arm exactly.
  5. check_link_resolution's paper->concept edge arm resolves against
     cfg.concepts_root.
  6. "methods" is no longer a valid OKF type; "methodology" is, and is
     project-scoped (routes to project_notes_dir/methodology).

All hermetic (tmp_instance fixture from conftest.py). No ~/vault reads.
"""
from __future__ import annotations

import pytest

from research_vault.config import load_config
from research_vault import note as note_mod


@pytest.fixture
def cfg(tmp_instance):
    return load_config(reload=True)


# ---------------------------------------------------------------------------
# 1-2. concepts_root config plumbing
# ---------------------------------------------------------------------------

class TestConceptsRootConfig:
    def test_concepts_root_default_mirrors_literature_root_shape(self, cfg):
        assert cfg.concepts_root == cfg.notes_root / "concepts"

    def test_concepts_root_toml_override(self, tmp_path):
        from research_vault.config import Config

        raw = {
            "instance_root": str(tmp_path),
            "notes_root": str(tmp_path / "notes"),
            "state_dir": str(tmp_path / "state"),
            "agents_dir": str(tmp_path / ".agents"),
            "tasks_dir": str(tmp_path / "tasks"),
            "control_dir": str(tmp_path / "control"),
            "concepts_root": str(tmp_path / "shared-concepts"),
            "projects": {},
        }
        c = Config(raw)
        assert c.concepts_root == tmp_path / "shared-concepts"

    def test_bundle_registry_includes_concepts(self, cfg):
        registry = cfg.bundle_registry()
        assert registry["concepts"] == cfg.concepts_root

    def test_shared_type_root_dispatches_per_type(self, cfg):
        assert cfg.shared_type_root("datasets") == cfg.datasets_root
        assert cfg.shared_type_root("concepts") == cfg.concepts_root


# ---------------------------------------------------------------------------
# 3. Partition membership
# ---------------------------------------------------------------------------

class TestPartitionMembership:
    def test_concepts_in_okf_shared_types(self):
        assert "concepts" in note_mod.OKF_SHARED_TYPES

    def test_datasets_still_in_okf_shared_types(self):
        assert "datasets" in note_mod.OKF_SHARED_TYPES

    def test_two_layer_types_unchanged(self):
        assert note_mod.OKF_TWO_LAYER_TYPES == frozenset({"literature"})

    def test_concepts_not_in_project_types(self):
        assert "concepts" not in note_mod.OKF_PROJECT_TYPES

    def test_classes_still_pairwise_disjoint(self):
        proj = note_mod.OKF_PROJECT_TYPES
        shared = note_mod.OKF_SHARED_TYPES
        two_layer = note_mod.OKF_TWO_LAYER_TYPES
        assert proj & shared == frozenset()
        assert proj & two_layer == frozenset()
        assert shared & two_layer == frozenset()

    def test_classes_still_union_to_okf_types(self):
        proj = note_mod.OKF_PROJECT_TYPES
        shared = note_mod.OKF_SHARED_TYPES
        two_layer = note_mod.OKF_TWO_LAYER_TYPES
        assert proj | shared | two_layer == note_mod.OKF_TYPES


# ---------------------------------------------------------------------------
# 4. cmd_new/cmd_list/cmd_check routing for concepts
# ---------------------------------------------------------------------------

class TestConceptsRouting:
    def test_cmd_new_concepts_routes_to_shared_root(self, tmp_instance, cfg):
        path = note_mod.cmd_new("demo-research", "concepts", "A Concept", config=cfg)
        assert path.exists()
        assert path.parent == cfg.concepts_root
        proj_concepts = cfg.project_notes_dir("demo-research") / "concepts"
        assert not (proj_concepts / path.name).exists()

    def test_cmd_list_concepts_scans_shared_root(self, tmp_instance, cfg):
        note_mod.cmd_new("demo-research", "concepts", "Shared Concept", config=cfg)
        notes = note_mod.cmd_list("demo-research", "concepts", config=cfg)
        assert len(notes) == 1
        assert notes[0]["fields"].get("type") == "concepts"
        assert notes[0]["path"].parent == cfg.concepts_root

    def test_cmd_check_reports_no_violation_for_valid_concept_note(self, tmp_instance, cfg):
        note_mod.cmd_new("demo-research", "concepts", "Valid Concept", config=cfg)
        violations = note_mod.cmd_check("demo-research", config=cfg)
        assert not violations, f"Unexpected violations: {violations}"

    def test_two_projects_share_the_same_concept(self, tmp_instance, cfg):
        """concepts is shared-canonical: a note filed under one project is
        visible via cfg.concepts_root regardless of which project filed it —
        no per-project overlay/copy."""
        path = note_mod.cmd_new("demo-research", "concepts", "Cross Project Concept", config=cfg)
        notes = note_mod.cmd_list("demo-litreview", "concepts", config=cfg)
        assert any(n["path"] == path for n in notes)


# ---------------------------------------------------------------------------
# 5. check_link_resolution's concept-edge arm resolves against concepts_root
# ---------------------------------------------------------------------------

class TestConceptEdgeResolution:
    def test_concept_edge_resolves_against_concepts_root_not_project_dir(self, tmp_instance, cfg):
        from research_vault.review import check_link_resolution

        # A concept note filed in the SHARED root (0.3.2 routing).
        note_mod.cmd_new("demo-research", "concepts", "Target Concept", config=cfg)
        concept_slug = next(cfg.concepts_root.glob("*.md")).stem

        # A literature overlay carrying a paper->concept edge to that slug.
        proj_notes = cfg.project_notes_dir("demo-research")
        lit_dir = proj_notes / "literature"
        lit_dir.mkdir(parents=True, exist_ok=True)
        overlay = lit_dir / "smith2024.md"
        overlay.write_text(
            "---\ntype: literature\ncentral: smith2024\n---\n\n"
            "## Concept edges\n\n"
            f"- [target](/concepts/{concept_slug}.md) — SUPPORTS: grounds the claim\n",
            encoding="utf-8",
        )
        # Central core (so the backbone resolves too — not the focus here,
        # but keeps this test from tripping an unrelated dangling-central error).
        cfg.literature_root.mkdir(parents=True, exist_ok=True)
        (cfg.literature_root / "smith2024.md").write_text(
            "---\ntype: literature\ncitekey: smith2024\ntitle: Smith 2024\n---\n\n"
            "## Result\n\nSome result.\n",
            encoding="utf-8",
        )

        result = check_link_resolution("demo-research", config=cfg)
        assert result["ok"], f"Unexpected unresolved edges: {result['errors']}"

    def test_concept_edge_unresolved_when_slug_absent_from_shared_root(self, tmp_instance, cfg):
        from research_vault.review import check_link_resolution

        proj_notes = cfg.project_notes_dir("demo-research")
        lit_dir = proj_notes / "literature"
        lit_dir.mkdir(parents=True, exist_ok=True)
        overlay = lit_dir / "jones2024.md"
        overlay.write_text(
            "---\ntype: literature\ncentral: jones2024\n---\n\n"
            "## Concept edges\n\n"
            "- [target](/concepts/nonexistent-slug.md) — SUPPORTS: grounds the claim\n",
            encoding="utf-8",
        )
        cfg.literature_root.mkdir(parents=True, exist_ok=True)
        (cfg.literature_root / "jones2024.md").write_text(
            "---\ntype: literature\ncitekey: jones2024\ntitle: Jones 2024\n---\n\n"
            "## Result\n\nSome result.\n",
            encoding="utf-8",
        )

        result = check_link_resolution("demo-research", config=cfg)
        assert not result["ok"]
        assert any("nonexistent-slug" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# 6. methods -> methodology rename
# ---------------------------------------------------------------------------

class TestMethodologyRename:
    def test_methods_is_not_a_valid_okf_type(self):
        assert "methods" not in note_mod.OKF_TYPES

    def test_methodology_is_a_valid_okf_type(self):
        assert "methodology" in note_mod.OKF_TYPES

    def test_methodology_is_project_scoped(self):
        assert "methodology" in note_mod.OKF_PROJECT_TYPES

    def test_cmd_new_methods_raises(self, tmp_instance, cfg):
        with pytest.raises(ValueError):
            note_mod.cmd_new("demo-research", "methods", "Old Type", config=cfg)

    def test_cmd_new_methodology_routes_to_project_dir(self, tmp_instance, cfg):
        path = note_mod.cmd_new("demo-research", "methodology", "New Method", config=cfg)
        assert path.exists()
        expected_parent = cfg.project_notes_dir("demo-research") / "methodology"
        assert path.parent == expected_parent

    def test_cmd_check_reports_no_violation_for_valid_methodology_note(self, tmp_instance, cfg):
        note_mod.cmd_new("demo-research", "methodology", "Valid Method", config=cfg)
        violations = note_mod.cmd_check("demo-research", config=cfg)
        assert not violations, f"Unexpected violations: {violations}"
