"""test_literature_store.py — 0.3.2 (the overlay unwind): literature is shared-canonical
(the overlay unwind).

Covers the collapse of the pre-unwind two-layer literature model (a central
core + a per-project overlay glued by a `central:` pointer) to a single
shared-canonical bundle — one note per paper, no per-project overlay:

  1. The two OKF routing classes (project/shared) are pairwise-disjoint and
     union to OKF_TYPES (SSOT partition test); `literature` now sits in
     OKF_SHARED_TYPES alongside `datasets`/`concepts`.
  2. Config.literature_root mirrors datasets_root/concepts_root (default +
     override) — `cfg.shared_type_root("literature")` resolves it too.
  3. `rv note new literature <proj> "<title>"` produces exactly one shared
     note (no overlay); a second project adopting the SAME slug gets the
     SAME note back, never a duplicate.
  4. cmd_list / cmd_check route literature through the shared-type arm.
  5. The overlay machinery is GONE — no `OKF_TWO_LAYER_TYPES`,
     `_cmd_new_two_layer`, `AssembledNote`, `load_literature_note`,
     `check_two_layer_invariants`, `_extract_central_slug`, `central:`
     anywhere a literature note is created or read.

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
    def test_two_layer_types_dissolved(self):
        assert not hasattr(note_mod, "OKF_TWO_LAYER_TYPES")

    def test_literature_is_shared(self):
        assert "literature" in note_mod.OKF_SHARED_TYPES

    def test_literature_not_project_scoped(self):
        assert "literature" not in note_mod.OKF_PROJECT_TYPES

    def test_two_classes_pairwise_disjoint(self):
        proj = note_mod.OKF_PROJECT_TYPES
        shared = note_mod.OKF_SHARED_TYPES
        assert proj & shared == frozenset()

    def test_two_classes_union_to_okf_types(self):
        proj = note_mod.OKF_PROJECT_TYPES
        shared = note_mod.OKF_SHARED_TYPES
        assert proj | shared == note_mod.OKF_TYPES

    def test_literature_stays_in_okf_types(self):
        assert "literature" in note_mod.OKF_TYPES


# ---------------------------------------------------------------------------
# 2. Config.literature_root
# ---------------------------------------------------------------------------

class TestLiteratureRootConfig:
    def test_default_mirrors_notes_root_literature(self, cfg):
        assert cfg.literature_root == cfg.notes_root / "literature"

    def test_shared_type_root_dispatches_literature(self, cfg):
        assert cfg.shared_type_root("literature") == cfg.literature_root

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
# 3. cmd_new — single shared-canonical note, no overlay
# ---------------------------------------------------------------------------

class TestCmdNewSharedCanonical:
    def test_creates_exactly_one_note_in_shared_root(self, cfg):
        note_path = note_mod.cmd_new(
            "demo-research", "literature", "A Paper", config=cfg, note_id="paper2024"
        )
        expected = cfg.literature_root / "paper2024.md"
        assert note_path == expected
        assert note_path.exists()

        # No stray files anywhere — one note, one root.
        assert list(cfg.literature_root.glob("*.md")) == [note_path]
        # No per-project overlay dir gets created as a side effect.
        overlay_dir = cfg.project_notes_dir("demo-research") / "literature"
        assert not overlay_dir.exists() or list(overlay_dir.glob("*.md")) == []

    def test_note_carries_intrinsic_placeholder_fields(self, cfg):
        note_path = note_mod.cmd_new(
            "demo-research", "literature", "A Paper", config=cfg, note_id="paper2024"
        )
        fields, _ = note_mod._parse_frontmatter(note_path.read_text(encoding="utf-8"))
        for intrinsic_key in ("citekey", "doi", "arxiv_id", "key_equations", "repo", "artifacts"):
            assert intrinsic_key in fields, f"{intrinsic_key} missing from the shared note"
        # No overlay-only fields ever existed here — no `central`, no `role`.
        assert "central" not in fields
        assert "role" not in fields

    def test_returned_path_matches_legacy_shape(self, cfg):
        """cmd_new must still return a path whose parent.name == note_type
        (test_new_all_types_accepted in test_note.py relies on this)."""
        path = note_mod.cmd_new("demo-research", "literature", "Another Paper", config=cfg)
        assert path.parent.name == "literature"

    def test_body_carries_all_former_core_and_overlay_sections(self, cfg):
        note_path = note_mod.cmd_new(
            "demo-research", "literature", "A Paper", config=cfg, note_id="paper2024"
        )
        body = note_path.read_text(encoding="utf-8")
        for heading in ("## Result", "## Key equations", "## Related papers", "## Concept edges"):
            assert heading in body

    def test_second_project_adopts_existing_note_no_duplicate(self, cfg):
        """Two projects creating a literature note with the same slug
        share the SAME shared note — never a duplicate/overwritten copy."""
        note_mod.cmd_new("demo-research", "literature", "Shared Paper", config=cfg, note_id="shared2024")
        note_path = cfg.literature_root / "shared2024.md"
        text_before = note_path.read_text(encoding="utf-8")

        second_path = note_mod.cmd_new(
            "demo-litreview", "literature", "Shared Paper", config=cfg, note_id="shared2024",
        )
        text_after = note_path.read_text(encoding="utf-8")

        assert text_before == text_after
        assert second_path == note_path
        # No second file was created anywhere.
        assert list(cfg.literature_root.glob("*.md")) == [note_path]

    def test_collision_between_two_DIFFERENT_papers_still_returns_existing(self, cfg):
        """A slug collision with an EXPLICIT note_id always adopts the
        existing note (the overlay unwind's identity-is-the-slug convention) — a
        title-derived slug collision (no explicit note_id) still bumps like
        any other OKF type, since there's no explicit identity assertion."""
        note_mod.cmd_new("demo-research", "literature", "Original Title", config=cfg, note_id="samekey")
        first_text = (cfg.literature_root / "samekey.md").read_text(encoding="utf-8")
        # Same explicit note_id, different title — still adopts the existing
        # note (identity is the slug, not the title).
        note_mod.cmd_new("demo-research", "literature", "A Totally Different Paper", config=cfg, note_id="samekey")
        second_text = (cfg.literature_root / "samekey.md").read_text(encoding="utf-8")
        assert first_text == second_text


# ---------------------------------------------------------------------------
# 4. cmd_list / cmd_check route literature through the shared-type arm
# ---------------------------------------------------------------------------

class TestCmdListCmdCheck:
    def test_cmd_list_scans_shared_root(self, cfg):
        note_mod.cmd_new("demo-research", "literature", "Paper A", config=cfg, note_id="a2024")
        notes = note_mod.cmd_list("demo-research", "literature", config=cfg)
        assert any(n["path"] == cfg.literature_root / "a2024.md" for n in notes)

    def test_cmd_list_literature_visible_from_any_project(self, cfg):
        """Shared-canonical: a note filed under one project is visible via
        cmd_list from ANY project — no per-project overlay/copy."""
        path = note_mod.cmd_new("demo-research", "literature", "Cross Project Paper", config=cfg)
        notes = note_mod.cmd_list("demo-litreview", "literature", config=cfg)
        assert any(n["path"] == path for n in notes)

    def test_cmd_check_reports_no_violation_for_conformant_note(self, cfg):
        note_path = note_mod.cmd_new(
            "demo-research", "literature", "Clean Paper", config=cfg, note_id="clean2024",
        )
        text = note_path.read_text(encoding="utf-8").replace(
            "citekey: \n", "citekey: clean2024\n"
        )
        note_path.write_text(text, encoding="utf-8")
        violations = note_mod.cmd_check("demo-research", config=cfg)
        assert violations == []

    def test_cmd_check_warns_on_missing_citekey(self, cfg):
        note_mod.cmd_new(
            "demo-research", "literature", "No Citekey Paper", config=cfg, note_id="nokey2024",
        )
        violations = note_mod.cmd_check("demo-research", config=cfg)
        assert any("nokey2024" in v and "citekey" in v.lower() for v in violations)


# ---------------------------------------------------------------------------
# 5. The overlay machinery is GONE — no two-layer symbols anywhere
# ---------------------------------------------------------------------------

class TestOverlayMachineryDissolved:
    @pytest.mark.parametrize("symbol", [
        "OKF_TWO_LAYER_TYPES",
        "_cmd_new_two_layer",
        "AssembledNote",
        "load_literature_note",
        "literature_overlay_path",
        "iter_literature_notes",
        "check_two_layer_invariants",
        "_extract_central_slug",
        "_CENTRAL_OKF_LINK_RE",
        "DanglingCentralPointerError",
        "_CORE_ONLY_FIELDS",
        "_OVERLAY_ONLY_FIELDS",
    ])
    def test_symbol_removed_from_note_module(self, symbol):
        assert not hasattr(note_mod, symbol), f"{symbol} should be dissolved by the overlay unwind"

    def test_no_central_field_on_a_freshly_created_note(self, cfg):
        note_path = note_mod.cmd_new(
            "demo-research", "literature", "A Paper", config=cfg, note_id="paper2024",
        )
        fields, _ = note_mod._parse_frontmatter(note_path.read_text(encoding="utf-8"))
        assert "central" not in fields

    def test_no_project_scoped_literature_dir_created(self, cfg):
        """A fresh cmd_new never creates project_notes_dir/literature/ —
        there is nothing left to write there."""
        note_mod.cmd_new("demo-research", "literature", "A Paper", config=cfg, note_id="paper2024")
        overlay_dir = cfg.project_notes_dir("demo-research") / "literature"
        assert not overlay_dir.exists()
