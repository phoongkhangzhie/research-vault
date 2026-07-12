"""test_literature_demo_fixtures.py — 0.3.2 (the overlay unwind): the shipped
demo-litreview literature fixtures are shared-canonical (the overlay
unwind's migration was run on them) and read cleanly through the shared
seams (cmd_list/cmd_check/literature.cmd_list).

Design of record: the overlay unwind (0.3.2) design + migration
section — "the published example teaches the contract — a fixture in the
old two-layer shape would teach the wrong layout on day one."

Covers:
  1. The shipped fixture exists at the expected shared-canonical location
     (``cfg.literature_root``) — the pre-unwind per-project overlay dir is
     GONE (the migration deleted it, since there's nothing left for it to
     carry).
  2. Copied into a tmp instance, ``note.cmd_list``/``note.cmd_check`` read
     the shared fixture cleanly.
  3. The former overlay content — ``## Concept edges`` (intra-shared) and
     role/position (curated MOC narration) — is present at its NEW home,
     not silently dropped.
  4. ``rv literature list`` (the registry verb) enumerates the shipped
     pair via the corpus ledger — an honest empty result here (this demo
     ships no ``_corpus_ledger.md``, so "adopted" is correctly empty; the
     registry's OWN unit tests in test_note.py/test_literature_store.py
     cover the ledger-driven enumeration path with a real ledger fixture).

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
from research_vault.note import _parse_frontmatter, cmd_check, cmd_list


@pytest.fixture
def cfg(tmp_instance):
    return load_config(reload=True)


def _copy_pkg_examples(cfg, tmp_path):
    """Copy the shipped demo-litreview literature fixture (the shared
    store only — there is no overlay dir any more, the overlay unwind (0.3.2)) into the
    tmp_instance's real cfg-resolved paths."""
    data_root = scaffold.pkg_data() / "examples"

    with importlib.resources.as_file(data_root / "literature") as core_src:
        cfg.literature_root.mkdir(parents=True, exist_ok=True)
        for f in core_src.iterdir():
            # _README.md ships as shared-store documentation, not an OKF
            # note (no `type:` field) — a real adopter's clone doesn't
            # copy it into a live instance the same way `rv update` does;
            # exclude it here so this hermetic copy matches that reality.
            if f.is_file() and f.suffix == ".md" and not f.name.startswith("_"):
                shutil.copy(str(f), str(cfg.literature_root / f.name))

    with importlib.resources.as_file(
        data_root / "demo-litreview" / "notes" / "mocs"
    ) as mocs_src:
        mocs_dir = cfg.project_notes_dir("demo-litreview") / "mocs"
        mocs_dir.mkdir(parents=True, exist_ok=True)
        for f in mocs_src.iterdir():
            if f.is_file():
                shutil.copy(str(f), str(mocs_dir / f.name))


# ---------------------------------------------------------------------------
# 1. Fixture ships at the shared-canonical location; overlay dir is GONE
# ---------------------------------------------------------------------------

class TestFixturesShipAtSharedCanonicalLocation:
    def test_shared_notes_exist(self):
        data_root = scaffold.pkg_data() / "examples" / "literature"
        with importlib.resources.as_file(data_root) as lit_dir:
            names = {f.name for f in lit_dir.iterdir() if f.is_file() and f.suffix == ".md"}
        assert "smith2024.md" in names
        assert "jones2023.md" in names

    def test_no_project_scoped_overlay_dir_shipped(self):
        """The migration deleted demo-litreview/notes/literature/ entirely
        — nothing is left there for a per-project overlay to carry."""
        data_root = scaffold.pkg_data() / "examples" / "demo-litreview" / "notes"
        with importlib.resources.as_file(data_root) as notes_dir:
            assert not (notes_dir / "literature").exists()

    def test_moc_carries_the_relocated_roles(self):
        data_root = (
            scaffold.pkg_data() / "examples" / "demo-litreview" / "notes" / "mocs"
        )
        with importlib.resources.as_file(data_root) as mocs_dir:
            names = {f.name for f in mocs_dir.iterdir() if f.is_file()}
        assert "literature-roles.md" in names


# ---------------------------------------------------------------------------
# 2 + 3. Shared reads work cleanly; relocated content is present at its home
# ---------------------------------------------------------------------------

class TestFixturesReadThroughSharedSeams:
    def test_cmd_list_enumerates_both_shipped_papers(self, cfg, tmp_path):
        _copy_pkg_examples(cfg, tmp_path)
        notes = cmd_list("demo-litreview", "literature", config=cfg)
        citekeys = {n["fields"].get("citekey") for n in notes}
        assert citekeys == {"smith2024", "jones2023"}

    def test_intrinsic_fields_present_directly_on_the_note(self, cfg, tmp_path):
        _copy_pkg_examples(cfg, tmp_path)
        text = (cfg.literature_root / "smith2024.md").read_text(encoding="utf-8")
        fields, body = _parse_frontmatter(text)
        assert fields.get("doi") == "10.9999/example.smith2024"
        assert fields.get("contribution_kind") == "method"
        assert "## Result" in body

    def test_concept_edges_relocated_onto_the_shared_note(self, cfg, tmp_path):
        _copy_pkg_examples(cfg, tmp_path)
        text = (cfg.literature_root / "smith2024.md").read_text(encoding="utf-8")
        _fields, body = _parse_frontmatter(text)
        assert "## Concept edges" in body
        assert "retrieval-augmented-reasoning" in body

    def test_no_central_field_survives_the_migration(self, cfg, tmp_path):
        _copy_pkg_examples(cfg, tmp_path)
        for citekey in ("smith2024", "jones2023"):
            fields, _ = _parse_frontmatter(
                (cfg.literature_root / f"{citekey}.md").read_text(encoding="utf-8")
            )
            assert "central" not in fields

    def test_role_and_position_relocated_to_the_moc_not_lost(self, cfg, tmp_path):
        _copy_pkg_examples(cfg, tmp_path)
        moc_text = (
            cfg.project_notes_dir("demo-litreview") / "mocs" / "literature-roles.md"
        ).read_text(encoding="utf-8")
        assert "smith2024" in moc_text and "methodological" in moc_text
        assert "jones2023" in moc_text and "counter-position" in moc_text

    def test_cmd_check_clean_on_shipped_fixture(self, cfg, tmp_path):
        _copy_pkg_examples(cfg, tmp_path)
        violations = cmd_check("demo-litreview", config=cfg)
        hard = [v for v in violations if "BLOCK" in v]
        assert hard == [], hard


# ---------------------------------------------------------------------------
# 4. rv literature list — an honest empty result (no ledger shipped)
# ---------------------------------------------------------------------------

class TestRegistryOverShippedFixtures:
    def test_literature_list_is_honestly_empty_without_a_ledger(self, cfg, tmp_path):
        """This demo ships no `_corpus_ledger.md` (it's a linear DAG
        walkthrough, not a full review run) — the overlay unwind (0.3.2) made membership
        MECHANICAL (the ledger), so with no ledger written yet, the
        registry correctly reports nothing adopted. Never a fabricated
        row for a paper that merely exists in the shared store."""
        _copy_pkg_examples(cfg, tmp_path)
        rows = literature_mod.cmd_list("demo-litreview", config=cfg)
        assert rows == []
