"""test_type_taxonomy_shared_concepts.py — 0.3.2 type-taxonomy: concepts
joins the shared-canonical partition, `methods` renames to `methodology`.

Covers:
  1. concepts_root config default + toml override (mirrors literature_root).
  2. cfg.bundle_registry() / cfg.shared_type_root() include concepts.
  3. OKF_SHARED_TYPES = {datasets, concepts}; OKF_TWO_LAYER_TYPES unchanged
     ({literature}) — the SSOT class-partition test (test_literature_store.py)
     stays green because the classes remain pairwise-disjoint.
  4. cmd_new/cmd_list/cmd_check route "concepts" to cfg.concepts_root, not
     project_notes_dir/concepts — mirroring the "datasets" arm exactly.
  5. check_link_resolution's paper->concept edge arm resolves against
     cfg.concepts_root, and its project-wide legacy-concepts-orphan guard
     (silent-invisibility on a multi-tier instance) fires loudly instead of
     dropping notes silently.
  6. "methods" is no longer a valid OKF type; "methodology" is, and is
     project-scoped (routes to project_notes_dir/methodology).
  7. note.relocate_legacy_concepts — the mechanical migration path off the
     legacy per-project concepts/ location, on a multi-tier topology.

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

    def test_two_layer_types_dissolved(self):
        """the overlay unwind (0.3.2): OKF_TWO_LAYER_TYPES is gone —
        literature joined OKF_SHARED_TYPES like concepts/datasets."""
        assert not hasattr(note_mod, "OKF_TWO_LAYER_TYPES")
        assert "literature" in note_mod.OKF_SHARED_TYPES

    def test_concepts_not_in_project_types(self):
        assert "concepts" not in note_mod.OKF_PROJECT_TYPES

    def test_classes_still_pairwise_disjoint(self):
        proj = note_mod.OKF_PROJECT_TYPES
        shared = note_mod.OKF_SHARED_TYPES
        assert proj & shared == frozenset()

    def test_classes_still_union_to_okf_types(self):
        proj = note_mod.OKF_PROJECT_TYPES
        shared = note_mod.OKF_SHARED_TYPES
        assert proj | shared == note_mod.OKF_TYPES


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

        # the overlay unwind (0.3.2): a literature note's
        # '## Concept edges' section lives directly on the SHARED note
        # (cfg.literature_root) — no overlay, no `central:` indirection.
        cfg.literature_root.mkdir(parents=True, exist_ok=True)
        (cfg.literature_root / "smith2024.md").write_text(
            "---\ntype: literature\ncitekey: smith2024\ntitle: Smith 2024\n---\n\n"
            "## Result\n\nSome result.\n\n"
            "## Concept edges\n\n"
            f"- [target](/concepts/{concept_slug}.md) — SUPPORTS: grounds the claim\n",
            encoding="utf-8",
        )

        result = check_link_resolution("demo-research", config=cfg)
        assert result["ok"], f"Unexpected unresolved edges: {result['errors']}"

    def test_concept_edge_unresolved_when_slug_absent_from_shared_root(self, tmp_instance, cfg):
        from research_vault.review import check_link_resolution

        cfg.literature_root.mkdir(parents=True, exist_ok=True)
        (cfg.literature_root / "jones2024.md").write_text(
            "---\ntype: literature\ncitekey: jones2024\ntitle: Jones 2024\n---\n\n"
            "## Result\n\nSome result.\n\n"
            "## Concept edges\n\n"
            "- [target](/concepts/nonexistent-slug.md) — SUPPORTS: grounds the claim\n",
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


# ---------------------------------------------------------------------------
# 7. Legacy-concepts orphan guard — silent-invisibility on a multi-tier
#    instance (coordinator fit-check finding). `demo-research` in
#    tmp_instance IS a multi-tier topology already: it has an explicit
#    source_dir, so cfg.project_notes_dir("demo-research") ("<tmp>/projects/
#    demo-research") and cfg.concepts_root ("<tmp>/notes/concepts") are
#    genuinely different directories — exactly the shape a real registered
#    code-repo project has.
# ---------------------------------------------------------------------------

class TestLegacyConceptsOrphanGuard:
    def _plant_legacy_concept(self, cfg, project="demo-research", slug="orphan-concept"):
        legacy_dir = cfg.project_notes_dir(project) / "concepts"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        p = legacy_dir / f"{slug}.md"
        p.write_text(
            f"---\ntype: concepts\ntitle: Orphan Concept\n---\n\nStranded at the legacy path.\n",
            encoding="utf-8",
        )
        return p

    def test_multi_tier_topology_precondition(self, tmp_instance, cfg):
        """Precondition check: the fixture project really is multi-tier —
        the legacy project-scoped concepts dir and cfg.concepts_root are
        different paths. If this ever stops holding (e.g. the fixture is
        simplified to a single-tier layout), every other test in this
        class would pass VACUOUSLY (the legacy and shared dirs would
        coincide, so nothing could ever be "orphaned") — pin it
        explicitly."""
        legacy_dir = cfg.project_notes_dir("demo-research") / "concepts"
        assert legacy_dir != cfg.concepts_root

    def test_red_before_green_orphan_is_actually_invisible_to_cmd_list(self, tmp_instance, cfg):
        """RED-BEFORE-GREEN proof: a legacy-planted concept note is a real
        silent-invisibility case, not a hypothetical — cmd_list/cmd_new's
        shared-root reader genuinely returns nothing for it. This is what
        makes the guard load-bearing rather than decorative."""
        self._plant_legacy_concept(cfg)
        notes = note_mod.cmd_list("demo-research", "concepts", config=cfg)
        assert notes == [], (
            "Test design error: the legacy-planted note IS visible via the "
            "shared-root reader — this test no longer proves the silent-"
            "invisibility case the orphan guard exists to catch."
        )

    def test_orphan_guard_fires_in_check_link_resolution(self, tmp_instance, cfg):
        from research_vault.review import check_link_resolution

        self._plant_legacy_concept(cfg)
        result = check_link_resolution("demo-research", config=cfg)
        assert result["ok"] is False
        assert any(
            "stranded" in e and "concepts" in e and "0.3.2" in e
            for e in result["errors"]
        ), f"Expected a loud stranded-concepts finding, got: {result['errors']}"

    def test_orphan_guard_fires_even_with_no_literature_overlays(self, tmp_instance, cfg):
        """The orphan check must NOT be gated on literature_dir existing —
        a project can have zero literature overlays and still have a
        stranded legacy concept. Regression pin for the early-return path
        in check_link_resolution."""
        from research_vault.review import check_link_resolution

        proj_notes = cfg.project_notes_dir("demo-research")
        assert not (proj_notes / "literature").exists()
        self._plant_legacy_concept(cfg)
        result = check_link_resolution("demo-research", config=cfg)
        assert result["ok"] is False

    def test_orphan_guard_silent_when_nothing_stranded(self, tmp_instance, cfg):
        from research_vault.review import check_link_resolution

        result = check_link_resolution("demo-research", config=cfg)
        assert result == {"ok": True, "errors": []}

    def test_cmd_check_default_warns_not_blocks(self, tmp_instance, cfg):
        """Day-to-day posture: WARN, never flips the hard-violation set."""
        self._plant_legacy_concept(cfg)
        violations = note_mod.cmd_check("demo-research", config=cfg, strict_links=False)
        warn_hits = [v for v in violations if v.startswith("[link-lint] WARN:") and "stranded" in v]
        assert warn_hits, f"Expected a [link-lint] WARN: stranded-concepts finding, got: {violations}"

    def test_cmd_check_strict_links_blocks(self, tmp_instance, cfg):
        """Curation-time posture: the SAME finding promotes to a hard BLOCK."""
        self._plant_legacy_concept(cfg)
        violations = note_mod.cmd_check("demo-research", config=cfg, strict_links=True)
        block_hits = [v for v in violations if v.startswith("[link-lint] BLOCK:") and "stranded" in v]
        assert block_hits, f"Expected a [link-lint] BLOCK: stranded-concepts finding, got: {violations}"

    def test_manuscript_approval_gate_always_blocks_on_orphan(self, tmp_instance, cfg):
        """The manuscript-approval gate (build_approve_payload) calls
        check_link_resolution DIRECTLY and always treats its errors as a
        hard BLOCK — the exact 'manuscript read path' the coordinator
        flagged. Proven here without constructing a full manuscript
        (build_approve_payload has many other preconditions) by exercising
        the same call check_gates.py makes."""
        from research_vault.review import check_link_resolution

        self._plant_legacy_concept(cfg)
        project_notes_dir = cfg.project_notes_dir("demo-research")
        link_result = check_link_resolution(project_notes_dir=project_notes_dir, config=cfg)
        assert not link_result["ok"]
        assert any("stranded" in e for e in link_result["errors"])


# ---------------------------------------------------------------------------
# 8. relocate_legacy_concepts — the mechanical migration path
# ---------------------------------------------------------------------------

class TestRelocateLegacyConcepts:
    def _plant_legacy_concept(self, cfg, project="demo-research", slug="orphan-concept", body="Stranded."):
        legacy_dir = cfg.project_notes_dir(project) / "concepts"
        legacy_dir.mkdir(parents=True, exist_ok=True)
        p = legacy_dir / f"{slug}.md"
        p.write_text(f"---\ntype: concepts\ntitle: Orphan Concept\n---\n\n{body}\n", encoding="utf-8")
        return p

    def test_relocate_moves_the_note_and_makes_it_visible(self, tmp_instance, cfg):
        legacy_path = self._plant_legacy_concept(cfg)
        result = note_mod.relocate_legacy_concepts(
            cfg.project_notes_dir("demo-research"), cfg.concepts_root,
        )
        assert not legacy_path.exists(), "legacy file must be gone after a real (non-dry-run) move"
        dest = cfg.concepts_root / "orphan-concept.md"
        assert dest.exists()
        assert result["moved"] == [dest]
        assert result["already_present"] == []
        assert result["conflicts"] == []

        # Now visible via the shared-root reader (the whole point).
        notes = note_mod.cmd_list("demo-research", "concepts", config=cfg)
        assert any(n["path"] == dest for n in notes)

        # And the orphan guard is silent post-relocation.
        from research_vault.review import check_link_resolution
        result2 = check_link_resolution("demo-research", config=cfg)
        assert result2 == {"ok": True, "errors": []}

    def test_relocate_dry_run_never_touches_disk(self, tmp_instance, cfg):
        legacy_path = self._plant_legacy_concept(cfg)
        result = note_mod.relocate_legacy_concepts(
            cfg.project_notes_dir("demo-research"), cfg.concepts_root, dry_run=True,
        )
        assert legacy_path.exists(), "dry_run must not move anything"
        assert not (cfg.concepts_root / "orphan-concept.md").exists()
        assert result["dry_run"] is True
        assert result["moved"] == [cfg.concepts_root / "orphan-concept.md"]

    def test_relocate_is_idempotent(self, tmp_instance, cfg):
        self._plant_legacy_concept(cfg)
        r1 = note_mod.relocate_legacy_concepts(
            cfg.project_notes_dir("demo-research"), cfg.concepts_root,
        )
        assert len(r1["moved"]) == 1
        # Second run: legacy dir is now empty — a correct no-op.
        r2 = note_mod.relocate_legacy_concepts(
            cfg.project_notes_dir("demo-research"), cfg.concepts_root,
        )
        assert r2 == {"moved": [], "already_present": [], "conflicts": [], "dry_run": False}

    def test_relocate_dedupes_byte_identical_leftover(self, tmp_instance, cfg):
        """A note already relocated (present at concepts_root) but a
        byte-identical copy still lingering at the legacy path (e.g. a
        prior manual copy) is deduped — the legacy copy is removed, never
        left behind as a second silent source of truth."""
        legacy_path = self._plant_legacy_concept(cfg, body="Same content.")
        cfg.concepts_root.mkdir(parents=True, exist_ok=True)
        (cfg.concepts_root / "orphan-concept.md").write_text(
            legacy_path.read_text(encoding="utf-8"), encoding="utf-8",
        )
        result = note_mod.relocate_legacy_concepts(
            cfg.project_notes_dir("demo-research"), cfg.concepts_root,
        )
        assert result["already_present"] == [legacy_path]
        assert result["moved"] == []
        assert not legacy_path.exists(), "byte-identical legacy leftover must be removed (deduped)"

    def test_relocate_surfaces_conflict_never_auto_resolves(self, tmp_instance, cfg):
        """A filename collision with DIFFERENT content is a conflict —
        left in place on BOTH sides, never silently overwritten either
        direction."""
        legacy_path = self._plant_legacy_concept(cfg, body="Legacy version.")
        cfg.concepts_root.mkdir(parents=True, exist_ok=True)
        (cfg.concepts_root / "orphan-concept.md").write_text(
            "---\ntype: concepts\ntitle: Orphan Concept\n---\n\nShared version (different).\n",
            encoding="utf-8",
        )
        result = note_mod.relocate_legacy_concepts(
            cfg.project_notes_dir("demo-research"), cfg.concepts_root,
        )
        assert result["conflicts"] == [legacy_path]
        assert result["moved"] == []
        assert legacy_path.exists(), "conflicting legacy file must be left in place"
        assert (cfg.concepts_root / "orphan-concept.md").read_text(encoding="utf-8").endswith(
            "Shared version (different).\n"
        )

    def test_relocate_no_legacy_dir_is_a_correct_no_op(self, tmp_instance, cfg):
        result = note_mod.relocate_legacy_concepts(
            cfg.project_notes_dir("demo-research"), cfg.concepts_root,
        )
        assert result == {"moved": [], "already_present": [], "conflicts": [], "dry_run": False}
