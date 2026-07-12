"""test_migrate_okf_pr2_central_bundle.py — the PR-2 migration script
(scripts/migrate_okf_pr2_central_bundle.py): the bare-slug -> okf: backbone
link rewrite, and the '## Related papers' overlay -> core relocation
(dedupe on parse).

Hermetic — imports the script as a module (no research_vault import
required by the script itself; it's a standalone stdlib tool that must run
against a raw checkout).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).parent.parent / "scripts" / "migrate_okf_pr2_central_bundle.py"
_spec = importlib.util.spec_from_file_location("migrate_okf_pr2_central_bundle", _SCRIPT_PATH)
migrate_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = migrate_mod
_spec.loader.exec_module(migrate_mod)


class TestMigrateCentralPointer:
    def test_bare_slug_rewritten_to_backbone_link(self):
        text = "---\ntype: literature\ncentral: smith2024\n---\n\nbody\n"
        new_text, count = migrate_mod.migrate_central_pointer(text)
        assert count == 1
        assert "central: [smith2024](okf:literature/smith2024.md)\n" in new_text

    def test_already_migrated_is_untouched_idempotent(self):
        text = "---\ntype: literature\ncentral: [smith2024](okf:literature/smith2024.md)\n---\n\nbody\n"
        new_text, count = migrate_mod.migrate_central_pointer(text)
        assert count == 0
        assert new_text == text

    def test_no_central_field_is_untouched(self):
        text = "---\ntype: literature\ntitle: X\n---\n\nbody\n"
        new_text, count = migrate_mod.migrate_central_pointer(text)
        assert count == 0
        assert new_text == text


class TestRelocateRelatedPapers:
    def test_no_section_is_a_no_op(self):
        overlay_text = "---\ntype: literature\n---\n\n## Concept edges\n\n- [x](/concepts/x.md) — SUPPORTS: y\n"
        core_text = "---\ntype: literature\n---\n\n"
        new_overlay, new_core, count = migrate_mod.relocate_related_papers(overlay_text, core_text)
        assert count == 0
        assert new_overlay == overlay_text
        assert new_core == core_text

    def test_relocates_edges_out_of_overlay_into_core(self):
        overlay_text = (
            "---\ntype: literature\n---\n\n"
            "## Related papers\n\n"
            "- [other2024](/literature/other2024.md) — SUPPORTS: shares the method\n\n"
            "## Concept edges\n\n"
            "- [x](/concepts/x.md) — SUPPORTS: y\n"
        )
        core_text = "---\ntype: literature\n---\n\n## Result\n\nsome result\n"

        new_overlay, new_core, count = migrate_mod.relocate_related_papers(overlay_text, core_text)

        assert count == 1
        assert "## Related papers" not in new_overlay
        assert "## Concept edges" in new_overlay  # sibling content untouched
        assert "[other2024](/literature/other2024.md) — SUPPORTS: shares the method" in new_core
        assert "## Result" in new_core  # sibling content untouched

    def test_dedupes_against_edges_the_core_already_has(self):
        edge_line = "- [other2024](/literature/other2024.md) — SUPPORTS: shares the method"
        overlay_text = f"---\ntype: literature\n---\n\n## Related papers\n\n{edge_line}\n"
        core_text = f"---\ntype: literature\n---\n\n## Related papers\n\n{edge_line}\n"

        new_overlay, new_core, count = migrate_mod.relocate_related_papers(overlay_text, core_text)

        assert count == 0  # already present in the core — not relocated a 2nd time
        assert "## Related papers" not in new_overlay  # still removed from the overlay
        assert new_core.count(edge_line) == 1  # no duplicate


class TestMigratePairRoundTrip:
    """The load-bearing round-trip guard: migrate a fixture overlay+core
    pair, assert the okf: backbone resolves and the relocated edges live in
    the central core (not the overlay), deduped."""

    def test_round_trip_migration(self, tmp_path):
        overlay_dir = tmp_path / "project" / "literature"
        core_dir = tmp_path / "central-literature"
        overlay_dir.mkdir(parents=True)
        core_dir.mkdir(parents=True)

        (overlay_dir / "smith2024.md").write_text(
            "---\ntype: literature\ncentral: smith2024\nrole: methodological\n---\n\n"
            "## Related papers\n\n"
            "- [jones2023](/literature/jones2023.md) — EXTENDS: extends the baseline.\n\n"
            "## Concept edges\n\n"
            "- [x](/concepts/x.md) — SUPPORTS: y\n",
            encoding="utf-8",
        )
        (core_dir / "smith2024.md").write_text(
            "---\ntype: literature\ncitekey: smith2024\n---\n\n## Result\n\nsome result\n",
            encoding="utf-8",
        )

        stats = migrate_mod.migrate_pair(overlay_dir, core_dir, dry_run=False)

        assert stats["central_pointers_migrated"] == 1
        assert stats["edges_relocated"] == 1
        assert stats["cores_missing"] == 0

        overlay_text = (overlay_dir / "smith2024.md").read_text(encoding="utf-8")
        core_text = (core_dir / "smith2024.md").read_text(encoding="utf-8")

        # (a) backbone link form, no longer a bare slug.
        assert "central: [smith2024](okf:literature/smith2024.md)" in overlay_text
        # (b) the edge lives in the core now, not the overlay.
        assert "## Related papers" not in overlay_text
        assert "[jones2023](/literature/jones2023.md) — EXTENDS: extends the baseline." in core_text
        # Sibling content is untouched in both files.
        assert "## Concept edges" in overlay_text
        assert "## Result" in core_text

        # Re-run is idempotent: nothing left to migrate or relocate.
        stats2 = migrate_mod.migrate_pair(overlay_dir, core_dir, dry_run=False)
        assert stats2["central_pointers_migrated"] == 0
        assert stats2["edges_relocated"] == 0

    def test_dry_run_writes_nothing(self, tmp_path):
        overlay_dir = tmp_path / "project" / "literature"
        core_dir = tmp_path / "central-literature"
        overlay_dir.mkdir(parents=True)
        core_dir.mkdir(parents=True)

        overlay_path = overlay_dir / "smith2024.md"
        core_path = core_dir / "smith2024.md"
        overlay_text_before = (
            "---\ntype: literature\ncentral: smith2024\n---\n\n"
            "## Related papers\n\n"
            "- [jones2023](/literature/jones2023.md) — EXTENDS: extends the baseline.\n"
        )
        core_text_before = "---\ntype: literature\ncitekey: smith2024\n---\n\n"
        overlay_path.write_text(overlay_text_before, encoding="utf-8")
        core_path.write_text(core_text_before, encoding="utf-8")

        stats = migrate_mod.migrate_pair(overlay_dir, core_dir, dry_run=True)

        assert stats["central_pointers_migrated"] == 1  # counted...
        assert overlay_path.read_text(encoding="utf-8") == overlay_text_before  # ...but not written
        assert core_path.read_text(encoding="utf-8") == core_text_before

    def test_reserved_filenames_are_skipped(self, tmp_path):
        overlay_dir = tmp_path / "project" / "literature"
        core_dir = tmp_path / "central-literature"
        overlay_dir.mkdir(parents=True)
        core_dir.mkdir(parents=True)
        (overlay_dir / "index.md").write_text(
            "---\ntype: literature\ncentral: should-not-migrate\n---\n\n", encoding="utf-8",
        )

        stats = migrate_mod.migrate_pair(overlay_dir, core_dir, dry_run=False)

        assert stats["overlays_scanned"] == 0
        assert (overlay_dir / "index.md").read_text(encoding="utf-8") == (
            "---\ntype: literature\ncentral: should-not-migrate\n---\n\n"
        )

    def test_missing_core_is_surfaced_not_silently_skipped(self, tmp_path, capsys):
        overlay_dir = tmp_path / "project" / "literature"
        core_dir = tmp_path / "central-literature"
        overlay_dir.mkdir(parents=True)
        core_dir.mkdir(parents=True)
        (overlay_dir / "orphan2024.md").write_text(
            "---\ntype: literature\ncentral: orphan2024\n---\n\n", encoding="utf-8",
        )

        stats = migrate_mod.migrate_pair(overlay_dir, core_dir, dry_run=False)

        assert stats["cores_missing"] == 1
        # The pointer rewrite still applies even with no core to relocate into.
        assert stats["central_pointers_migrated"] == 1
        err = capsys.readouterr().err
        assert "no central core" in err.lower()
