"""test_literature_demo_fixtures.py — PR-B: the shipped demo-litreview
literature fixtures are in the TWO-LAYER shape and read cleanly through the
resolver (acceptance item 2).

Design of record: docs/superpowers/specs/2026-07-10-central-note-store-
cross-project-design.md §0.5 PR-B ("the published example teaches the
contract — a fixture in the old monolithic shape would teach the wrong
layout on day one").

Covers:
  1. The shipped fixtures exist at the expected two-layer locations.
  2. Copied into a tmp instance, `note.load_literature_note`/
     `note.iter_literature_notes` read them cleanly (no exception) — this
     is the load-bearing proof: a stray non-conformant .md file in the
     overlay dir would raise (iter_literature_notes fail-closes on every
     file it finds there), so a clean read proves the dir contains ONLY
     valid two-layer overlays.
  3. `note.check_two_layer_invariants` is clean on both shipped pairs — no
     intrinsic field leaked into an overlay, no overlay-only field leaked
     into a core.
  4. `rv literature list` (the PR-B registry verb) enumerates the shipped
     pair correctly once copied into a live project.

All hermetic — reads shipped package data via importlib.resources
(scaffold.pkg_data()), copies into tmp_instance. No ~/vault reads.
"""
from __future__ import annotations

import importlib.resources
import shutil

import pytest

from research_vault import literature as literature_mod
from research_vault import scaffold
from research_vault.config import load_config
from research_vault.note import (
    DanglingCentralPointerError,
    check_two_layer_invariants,
    iter_literature_notes,
    load_literature_note,
)


@pytest.fixture
def cfg(tmp_instance):
    return load_config(reload=True)


def _copy_pkg_examples(cfg, tmp_path):
    """Copy the shipped demo-litreview literature fixtures (core store +
    demo-litreview overlay) into the tmp_instance's real cfg-resolved
    paths — the same shape `rv update`/an adopter's clone would produce."""
    data_root = scaffold.pkg_data() / "examples"

    with importlib.resources.as_file(data_root / "literature") as core_src:
        cfg.literature_root.mkdir(parents=True, exist_ok=True)
        for f in core_src.iterdir():
            if f.is_file():
                shutil.copy(str(f), str(cfg.literature_root / f.name))

    with importlib.resources.as_file(
        data_root / "demo-litreview" / "notes" / "literature"
    ) as overlay_src:
        overlay_dir = cfg.project_notes_dir("demo-litreview") / "literature"
        overlay_dir.mkdir(parents=True, exist_ok=True)
        for f in overlay_src.iterdir():
            if f.is_file():
                shutil.copy(str(f), str(overlay_dir / f.name))


# ---------------------------------------------------------------------------
# 1. Fixtures exist at the expected two-layer locations
# ---------------------------------------------------------------------------

class TestFixturesShipAtTwoLayerLocations:
    def test_central_cores_exist(self):
        data_root = scaffold.pkg_data() / "examples" / "literature"
        with importlib.resources.as_file(data_root) as core_dir:
            names = {f.name for f in core_dir.iterdir() if f.is_file()}
        assert "smith2024.md" in names
        assert "jones2023.md" in names

    def test_overlays_exist_in_demo_litreview(self):
        data_root = (
            scaffold.pkg_data() / "examples" / "demo-litreview" / "notes" / "literature"
        )
        with importlib.resources.as_file(data_root) as overlay_dir:
            names = {f.name for f in overlay_dir.iterdir() if f.is_file()}
        assert "smith2024.md" in names
        assert "jones2023.md" in names
        # No stray placeholder / doc file — every file in a two-layer
        # overlay dir is fail-closed-read by iter_literature_notes (see
        # test_iter_literature_notes_reads_cleanly below).
        assert names == {"smith2024.md", "jones2023.md"}


# ---------------------------------------------------------------------------
# 2 + 3. Resolver + invariant lint read cleanly
# ---------------------------------------------------------------------------

class TestFixturesReadThroughResolver:
    def test_iter_literature_notes_reads_cleanly(self, cfg, tmp_path):
        """The load-bearing proof: iter_literature_notes fail-closes (raises)
        on ANY overlay file with a missing/dangling central: pointer. A
        clean iteration over both shipped papers proves the overlay dir
        contains only valid two-layer pairs — no stray doc file, no
        monolithic leftover."""
        _copy_pkg_examples(cfg, tmp_path)
        notes = list(iter_literature_notes(cfg, "demo-litreview"))
        assert {n.citekey for n in notes} == {"smith2024", "jones2023"}

    def test_core_only_fields_present_on_assembled_note(self, cfg, tmp_path):
        _copy_pkg_examples(cfg, tmp_path)
        assembled = load_literature_note(cfg, "demo-litreview", "smith2024")
        assert assembled.fields.get("doi") == "10.9999/example.smith2024"
        assert assembled.fields.get("contribution_kind") == "method"
        assert "## Result" in assembled.body

    def test_overlay_only_fields_present_on_assembled_note(self, cfg, tmp_path):
        _copy_pkg_examples(cfg, tmp_path)
        assembled = load_literature_note(cfg, "demo-litreview", "smith2024")
        assert assembled.fields.get("role") == "methodological"
        assert assembled.fields.get("position")
        assert "## Concept edges" in assembled.body

    def test_invariant_lint_clean_on_both_shipped_pairs(self, cfg, tmp_path):
        _copy_pkg_examples(cfg, tmp_path)
        for citekey in ("smith2024", "jones2023"):
            core_path = cfg.literature_root / f"{citekey}.md"
            overlay_path = cfg.project_notes_dir("demo-litreview") / "literature" / f"{citekey}.md"
            violations = check_two_layer_invariants(core_path, overlay_path)
            hard = [v for v in violations if "BLOCK" in v]
            assert hard == [], f"{citekey}: {violations}"

    def test_no_dangling_pointer(self, cfg, tmp_path):
        _copy_pkg_examples(cfg, tmp_path)
        # Would raise DanglingCentralPointerError if either pointer failed
        # to resolve — the absence of an exception IS the assertion.
        load_literature_note(cfg, "demo-litreview", "smith2024")
        load_literature_note(cfg, "demo-litreview", "jones2023")


# ---------------------------------------------------------------------------
# 4. rv literature list enumerates the shipped pair
# ---------------------------------------------------------------------------

class TestRegistryOverShippedFixtures:
    def test_literature_list_enumerates_shipped_pair(self, cfg, tmp_path):
        _copy_pkg_examples(cfg, tmp_path)
        rows = literature_mod.cmd_list("demo-litreview", config=cfg)
        assert {r["citekey"] for r in rows} == {"smith2024", "jones2023"}
        for row in rows:
            assert row["error"] is None
            assert row["title"]
