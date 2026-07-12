"""test_literature_store.py — PR-A: the central two-layer literature store.

Covers (see docs/superpowers/specs/2026-07-10-central-note-store-cross-
project-design.md §0.5 PR-A, dispatched as the pre-publish #68 storage
contract):

  1. The three OKF routing classes (project/shared/two-layer) are
     pairwise-disjoint and union to OKF_TYPES (SSOT partition test).
  2. Config.literature_root mirrors datasets_root (default + override).
  3. `rv note new literature <proj> "<title>"` produces exactly one central
     core + one thin overlay with a resolving `central:` pointer.
  4. The resolver (`load_literature_note`/`iter_literature_notes`) merges
     core + overlay into one AssembledNote; fails closed on a dangling
     `central:` pointer; a core-with-no-overlay is a distinct, valid state.
  5. The invariant lint (`check_two_layer_invariants`) is a GATING check:
     no intrinsic field authored in an overlay, no position/role/concept-
     edge frontmatter in a core.
  6. cmd_list / cmd_check route literature through the two-layer split.

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
# 1. SSOT partition test
# ---------------------------------------------------------------------------

class TestOKFTypeClassPartition:
    def test_two_layer_types_is_literature_only(self):
        assert note_mod.OKF_TWO_LAYER_TYPES == frozenset({"literature"})

    def test_three_classes_pairwise_disjoint(self):
        proj = note_mod.OKF_PROJECT_TYPES
        shared = note_mod.OKF_SHARED_TYPES
        two_layer = note_mod.OKF_TWO_LAYER_TYPES
        assert proj & shared == frozenset()
        assert proj & two_layer == frozenset()
        assert shared & two_layer == frozenset()

    def test_three_classes_union_to_okf_types(self):
        proj = note_mod.OKF_PROJECT_TYPES
        shared = note_mod.OKF_SHARED_TYPES
        two_layer = note_mod.OKF_TWO_LAYER_TYPES
        assert proj | shared | two_layer == note_mod.OKF_TYPES

    def test_literature_stays_in_okf_types(self):
        assert "literature" in note_mod.OKF_TYPES


# ---------------------------------------------------------------------------
# 2. Config.literature_root
# ---------------------------------------------------------------------------

class TestLiteratureRootConfig:
    def test_default_mirrors_notes_root_literature(self, cfg):
        assert cfg.literature_root == cfg.notes_root / "literature"

    def test_override_via_toml(self, tmp_path):
        cfg_path = tmp_path / "research_vault.toml"
        lit_root = tmp_path / "shared-lit"
        cfg_path.write_text(
            f'instance_root = "{tmp_path}"\n'
            f'notes_root = "{tmp_path / "notes"}"\n'
            f'state_dir = "{tmp_path / "state"}"\n'
            f'agents_dir = "{tmp_path / ".agents"}"\n'
            f'tasks_dir = "{tmp_path / "tasks"}"\n'
            f'control_dir = "{tmp_path / "control"}"\n'
            f'literature_root = "{lit_root}"\n'
            '[adapters]\nnotifier = "file"\nbackend = "local"\nsecrets = "env"\n',
            encoding="utf-8",
        )
        import os
        from research_vault.config import reset_config_cache
        old = os.environ.get("RESEARCH_VAULT_CONFIG")
        os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_path)
        reset_config_cache()
        try:
            resolved = load_config(reload=True)
            assert resolved.literature_root == lit_root
        finally:
            if old is None:
                os.environ.pop("RESEARCH_VAULT_CONFIG", None)
            else:
                os.environ["RESEARCH_VAULT_CONFIG"] = old
            reset_config_cache()


# ---------------------------------------------------------------------------
# 3. cmd_new two-layer creation
# ---------------------------------------------------------------------------

class TestCmdNewTwoLayer:
    def test_creates_exactly_one_core_and_one_overlay(self, cfg):
        overlay_path = note_mod.cmd_new(
            "demo-research", "literature", "A Paper", config=cfg, note_id="paper2024"
        )
        core_path = cfg.literature_root / "paper2024.md"
        overlay_expected = cfg.project_notes_dir("demo-research") / "literature" / "paper2024.md"

        assert overlay_path == overlay_expected
        assert overlay_path.exists()
        assert core_path.exists()

        # No stray files: exactly one core file, one overlay file.
        assert list(cfg.literature_root.glob("*.md")) == [core_path]
        assert list((cfg.project_notes_dir("demo-research") / "literature").glob("*.md")) == [overlay_path]

    def test_overlay_carries_resolving_central_pointer(self, cfg):
        overlay_path = note_mod.cmd_new(
            "demo-research", "literature", "A Paper", config=cfg, note_id="paper2024"
        )
        fields, _ = note_mod._parse_frontmatter(overlay_path.read_text(encoding="utf-8"))
        # PR-2: the cross-bundle backbone link form, not a bare slug.
        assert fields.get("central") == "[paper2024](okf:literature/paper2024.md)"
        assert note_mod._extract_central_slug(fields["central"]) == "paper2024"
        core_path = cfg.literature_root / "paper2024.md"
        assert core_path.exists()

    def test_overlay_returned_path_matches_legacy_shape(self, cfg):
        """cmd_new must still return a path whose parent.name == note_type
        (test_new_all_types_accepted in test_note.py relies on this)."""
        path = note_mod.cmd_new("demo-research", "literature", "Another Paper", config=cfg)
        assert path.parent.name == "literature"

    def test_core_has_intrinsic_placeholder_fields_not_overlay(self, cfg):
        overlay_path = note_mod.cmd_new(
            "demo-research", "literature", "A Paper", config=cfg, note_id="paper2024"
        )
        core_path = cfg.literature_root / "paper2024.md"
        core_fields, _ = note_mod._parse_frontmatter(core_path.read_text(encoding="utf-8"))
        overlay_fields, _ = note_mod._parse_frontmatter(overlay_path.read_text(encoding="utf-8"))

        for intrinsic_key in ("citekey", "doi", "arxiv_id", "key_equations", "repo", "artifacts"):
            assert intrinsic_key in core_fields, f"{intrinsic_key} missing from core"
            assert intrinsic_key not in overlay_fields, f"{intrinsic_key} leaked into overlay"

        assert "central" in overlay_fields
        assert "central" not in core_fields

    def test_second_project_adopts_existing_core_no_duplicate(self, cfg):
        """Two projects creating a literature note with the same slug
        adopt the SAME central core — 'distilled but not adopted' (§3.4)
        rather than a duplicate/overwritten core."""
        note_mod.cmd_new("demo-research", "literature", "Shared Paper", config=cfg, note_id="shared2024")
        core_path = cfg.literature_root / "shared2024.md"
        core_text_before = core_path.read_text(encoding="utf-8")

        note_mod.cmd_new("demo-litreview", "literature", "Shared Paper", config=cfg, note_id="shared2024")
        core_text_after = core_path.read_text(encoding="utf-8")

        assert core_text_before == core_text_after
        overlay2 = cfg.project_notes_dir("demo-litreview") / "literature" / "shared2024.md"
        assert overlay2.exists()
        fields, _ = note_mod._parse_frontmatter(overlay2.read_text(encoding="utf-8"))
        assert note_mod._extract_central_slug(fields.get("central")) == "shared2024"


# ---------------------------------------------------------------------------
# 4. The resolver
# ---------------------------------------------------------------------------

class TestResolver:
    def test_load_literature_note_merges_core_and_overlay(self, cfg):
        note_mod.cmd_new("demo-research", "literature", "A Paper", config=cfg, note_id="paper2024")
        core_path = cfg.literature_root / "paper2024.md"
        # Stamp an intrinsic field on the core (simulating the relate-<key>
        # agent's Move 1/3 answers).
        core_text = core_path.read_text(encoding="utf-8")
        core_text = core_text.replace("citekey: \n", "citekey: paper2024\n")
        core_path.write_text(core_text, encoding="utf-8")

        overlay_path = cfg.project_notes_dir("demo-research") / "literature" / "paper2024.md"
        overlay_text = overlay_path.read_text(encoding="utf-8")
        overlay_text = overlay_text.replace(
            "---\n\n", "role: counter-position\nposition: pushes back on X.\n---\n\n", 1
        )
        overlay_path.write_text(overlay_text, encoding="utf-8")

        assembled = note_mod.load_literature_note(cfg, "demo-research", "paper2024")
        assert assembled.fields.get("citekey") == "paper2024"
        assert assembled.fields.get("role") == "counter-position"
        assert note_mod._extract_central_slug(assembled.fields.get("central")) == "paper2024"
        assert assembled.core_resolved is True
        assert assembled.core_path == core_path
        assert assembled.overlay_path == overlay_path

    def test_dangling_central_pointer_is_tolerant_loud(self, cfg):
        """PR-2 fork 3: a dangling backbone link never raises — OKF's own
        consumer-MUST-tolerate rule, applied to rv's cross-bundle
        extension. The resolver returns a surfaced, overlay-only
        AssembledNote instead, plus a UserWarning."""
        overlay_dir = cfg.project_notes_dir("demo-research") / "literature"
        overlay_dir.mkdir(parents=True, exist_ok=True)
        bad_overlay = overlay_dir / "ghost2024.md"
        bad_overlay.write_text(
            "---\ntype: literature\ncentral: [ghost2024](okf:literature/ghost2024.md)\n---\n\n",
            encoding="utf-8",
        )
        # No core exists for 'ghost2024' — a dangling pointer.
        with pytest.warns(UserWarning, match="dangling"):
            assembled = note_mod.load_literature_note(cfg, "demo-research", "ghost2024")
        assert assembled.core_resolved is False
        assert assembled.core_path is None
        assert "dangling" in assembled.core_resolve_issue.lower()
        # Overlay content is still present — never a silently-empty note.
        assert assembled.fields.get("type") == "literature"

    def test_absent_central_pointer_is_also_tolerant_loud(self, cfg):
        overlay_dir = cfg.project_notes_dir("demo-research") / "literature"
        overlay_dir.mkdir(parents=True, exist_ok=True)
        (overlay_dir / "nopointer2024.md").write_text(
            "---\ntype: literature\n---\n\n", encoding="utf-8",
        )
        with pytest.warns(UserWarning):
            assembled = note_mod.load_literature_note(cfg, "demo-research", "nopointer2024")
        assert assembled.core_resolved is False
        assert assembled.core_path is None

    def test_bare_slug_central_pointer_still_resolves_back_compat(self, cfg):
        """Migration-window back-compat: a pre-PR-2 bare `central: <slug>`
        value (not yet migrated to the okf: link form) still resolves."""
        note_mod.cmd_new("demo-research", "literature", "Legacy Paper", config=cfg, note_id="legacy2024")
        overlay_path = cfg.project_notes_dir("demo-research") / "literature" / "legacy2024.md"
        text = overlay_path.read_text(encoding="utf-8")
        text = text.replace(
            "central: [legacy2024](okf:literature/legacy2024.md)", "central: legacy2024"
        )
        overlay_path.write_text(text, encoding="utf-8")

        assembled = note_mod.load_literature_note(cfg, "demo-research", "legacy2024")
        assert assembled.core_resolved is True
        assert assembled.citekey == "legacy2024"
        assert assembled.core_path == cfg.literature_root / "legacy2024.md"

    def test_overlay_missing_raises_not_adopted(self, cfg):
        # Core exists (distilled by another project) but demo-research never
        # adopted it (no overlay file) — a distinct, honest FileNotFoundError,
        # not a dangling-pointer violation.
        note_mod.cmd_new("demo-litreview", "literature", "Only Adopted Elsewhere", config=cfg, note_id="other2024")
        with pytest.raises(FileNotFoundError):
            note_mod.load_literature_note(cfg, "demo-research", "other2024")

    def test_iter_literature_notes_enumerates_project_overlays(self, cfg):
        note_mod.cmd_new("demo-research", "literature", "Paper A", config=cfg, note_id="a2024")
        note_mod.cmd_new("demo-research", "literature", "Paper B", config=cfg, note_id="b2024")
        note_mod.cmd_new("demo-litreview", "literature", "Paper C (other project)", config=cfg, note_id="c2024")

        citekeys = sorted(a.overlay_path.stem for a in note_mod.iter_literature_notes(cfg, "demo-research"))
        assert citekeys == ["a2024", "b2024"]

    def test_core_present_no_overlay_is_not_adopted_valid_state(self, cfg):
        """A core distilled by one project but never adopted by another is
        valid — it's simply invisible to iter_literature_notes(other_project)."""
        note_mod.cmd_new("demo-litreview", "literature", "Distilled Elsewhere", config=cfg, note_id="d2024")
        core_path = cfg.literature_root / "d2024.md"
        assert core_path.exists()
        result = list(note_mod.iter_literature_notes(cfg, "demo-research"))
        assert all(a.overlay_path.stem != "d2024" for a in result)


# ---------------------------------------------------------------------------
# 5. The invariant lint — GATING
# ---------------------------------------------------------------------------

class TestInvariantLint:
    def test_clean_core_and_overlay_pass(self, cfg):
        note_mod.cmd_new("demo-research", "literature", "Clean Paper", config=cfg, note_id="clean2024")
        core_path = cfg.literature_root / "clean2024.md"
        overlay_path = cfg.project_notes_dir("demo-research") / "literature" / "clean2024.md"
        violations = note_mod.check_two_layer_invariants(core_path, overlay_path)
        assert violations == []

    def test_intrinsic_field_in_overlay_is_blocked(self, cfg):
        note_mod.cmd_new("demo-research", "literature", "Dirty Paper", config=cfg, note_id="dirty2024")
        core_path = cfg.literature_root / "dirty2024.md"
        overlay_path = cfg.project_notes_dir("demo-research") / "literature" / "dirty2024.md"

        text = overlay_path.read_text(encoding="utf-8")
        text = text.replace("---\n\n", "doi: 10.1234/example\n---\n\n", 1)
        overlay_path.write_text(text, encoding="utf-8")

        violations = note_mod.check_two_layer_invariants(core_path, overlay_path)
        hard = [v for v in violations if not v.startswith("[two-layer-lint] WARN:")]
        assert any("doi" in v and "overlay" in v for v in hard)

    def test_overlay_field_in_core_is_blocked(self, cfg):
        note_mod.cmd_new("demo-research", "literature", "Dirty Paper 2", config=cfg, note_id="dirty2025")
        core_path = cfg.literature_root / "dirty2025.md"
        overlay_path = cfg.project_notes_dir("demo-research") / "literature" / "dirty2025.md"

        text = core_path.read_text(encoding="utf-8")
        text = text.replace("---\n\n", "role: counter-position\n---\n\n", 1)
        core_path.write_text(text, encoding="utf-8")

        violations = note_mod.check_two_layer_invariants(core_path, overlay_path)
        hard = [v for v in violations if not v.startswith("[two-layer-lint] WARN:")]
        assert any("role" in v and "core" in v for v in hard)

    def test_related_papers_in_overlay_is_blocked(self, cfg):
        """PR-2: '## Related papers' left in an overlay is a hard BLOCK, not
        the pre-PR-2 WARN — the edge-write retarget means a core-only body
        heading surfacing in the overlay is always genuine misauthoring."""
        note_mod.cmd_new("demo-research", "literature", "Edge Leak Paper", config=cfg, note_id="edgeleak2024")
        core_path = cfg.literature_root / "edgeleak2024.md"
        overlay_path = cfg.project_notes_dir("demo-research") / "literature" / "edgeleak2024.md"

        overlay_path.write_text(
            overlay_path.read_text(encoding="utf-8")
            + "\n## Related papers\n\n"
            "- [other2024](/literature/other2024.md) — SUPPORTS: planted leak.\n",
            encoding="utf-8",
        )

        violations = note_mod.check_two_layer_invariants(core_path, overlay_path)
        hard = [v for v in violations if v.startswith("[two-layer-lint] BLOCK:")]
        assert any("Related papers" in v or "core-only body" in v for v in hard)
        # No lingering WARN-class marker for this class — it's a hard BLOCK now.
        assert not any(
            v.startswith("[two-layer-lint] WARN:") and "Related papers" in v
            for v in violations
        )

    def test_mutation_guard_lint_is_load_bearing(self, cfg):
        """Proof the lint actually fires — not vacuously green. Weaken the
        check to a no-op and confirm the same fixture that BLOCKs above no
        longer would (documents the RED state the real function fixes)."""
        note_mod.cmd_new("demo-research", "literature", "Dirty Paper 3", config=cfg, note_id="dirty2026")
        core_path = cfg.literature_root / "dirty2026.md"
        overlay_path = cfg.project_notes_dir("demo-research") / "literature" / "dirty2026.md"
        text = overlay_path.read_text(encoding="utf-8")
        text = text.replace("---\n\n", "doi: 10.1234/example\n---\n\n", 1)
        overlay_path.write_text(text, encoding="utf-8")

        def _noop_check(core, overlay):
            return []

        # The real function must differ from a no-op on this dirty fixture.
        assert note_mod.check_two_layer_invariants(core_path, overlay_path) != _noop_check(core_path, overlay_path)

    def test_cmd_check_blocks_on_intrinsic_field_in_overlay(self, cfg):
        """End-to-end GATING proof: `rv note check` (cmd_check) — the same
        check CI runs — flips to a hard violation when the invariant is
        broken, and stays clean when it isn't."""
        note_mod.cmd_new("demo-research", "literature", "Gate Paper", config=cfg, note_id="gate2024")
        core_path = cfg.literature_root / "gate2024.md"
        overlay_path = cfg.project_notes_dir("demo-research") / "literature" / "gate2024.md"
        # Give the core a conformant citekey to isolate the invariant BLOCK
        # from the (WARN-class, non-gating) citekey-lint noise.
        core_text = core_path.read_text(encoding="utf-8").replace(
            "citekey: \n", "citekey: gate2024\n"
        )
        core_path.write_text(core_text, encoding="utf-8")

        violations_clean = note_mod.cmd_check("demo-research", config=cfg)
        hard_clean = [v for v in violations_clean if "[two-layer-lint] BLOCK:" in v]
        assert hard_clean == []

        text = overlay_path.read_text(encoding="utf-8")
        text = text.replace("---\n\n", "doi: 10.1234/example\n---\n\n", 1)
        overlay_path.write_text(text, encoding="utf-8")

        violations_dirty = note_mod.cmd_check("demo-research", config=cfg)
        hard_dirty = [v for v in violations_dirty if "[two-layer-lint] BLOCK:" in v]
        assert hard_dirty != []

    def test_dangling_central_pointer_surfaces_in_cmd_check(self, cfg):
        overlay_dir = cfg.project_notes_dir("demo-research") / "literature"
        overlay_dir.mkdir(parents=True, exist_ok=True)
        (overlay_dir / "ghost2025.md").write_text(
            "---\ntype: literature\ncentral: ghost2025\n---\n\n", encoding="utf-8",
        )
        violations = note_mod.cmd_check("demo-research", config=cfg)
        assert any("dangling" in v.lower() and "ghost2025" in v for v in violations)
