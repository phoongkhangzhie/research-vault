"""test_literature_registry.py — PR-B: `rv literature list <project>`, the
per-project two-layer literature registry (registry-as-pointer).

Design of record: docs/superpowers/specs/2026-07-10-central-note-store-
cross-project-design.md §0.5 PR-B.

Covers:
  1. Registry = the overlay dir (filesystem-is-registry); no overlay dir ->
     empty list, never an error.
  2. Enrichment comes from an already-written `_corpus_ledger.md` — the
     resolving ids + conformance verdict are read back from the ledger's
     rendered table, not recomputed.
  3. ★ Zero-recomputation proof: `review.ledger`'s own resolving-id /
     conformance functions are NEVER called by `literature.cmd_list` —
     patched to raise, cmd_list must still return correct enrichment
     (because it only re-parses the ledger's already-rendered table).
  4. A citekey never seen by any ledger -> honest gap (resolving_ids="",
     conformant=None, in_ledger=False), never a fabricated value.
  5. A dangling `central:` pointer is surfaced (an `error` row), not
     silently dropped from the list.

All hermetic (tmp_instance fixture from conftest.py). No ~/vault reads.
"""
from __future__ import annotations

import pytest

from research_vault.config import load_config
from research_vault import literature as literature_mod
from research_vault import note as note_mod
from research_vault.review import ledger as ledger_mod


@pytest.fixture
def cfg(tmp_instance):
    return load_config(reload=True)


def _make_adopted_paper(cfg, project, note_id, *, doi=None, role=None):
    """Create a two-layer literature note + stamp doi/role, mirroring a
    real relate-node distillation."""
    overlay_path = note_mod.cmd_new(project, "literature", "A Paper", config=cfg, note_id=note_id)
    core_path = cfg.literature_root / f"{note_id}.md"
    if doi is not None:
        text = core_path.read_text(encoding="utf-8")
        text = text.replace("doi: \n", f"doi: {doi}\n")
        core_path.write_text(text, encoding="utf-8")
    if role is not None:
        text = overlay_path.read_text(encoding="utf-8")
        text = text.rstrip("\n") + f"\nrole: {role}\n"
        # role must be in frontmatter, not appended after the body — rewrite properly.
        lines = overlay_path.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "---"
        end = lines[1:].index("---") + 1
        lines.insert(end, f"role: {role}")
        overlay_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return overlay_path, core_path


def _write_ledger_for(cfg, project, review_scope, citekeys):
    """A real, minimally-populated `_corpus_ledger.md` — genuinely produced
    by review.ledger.write_corpus_ledger (never hand-crafted), so the
    canonical-key map + accepted/in_corpus/new counts are the SAME
    machinery a real review run produces."""
    review_dir = cfg.project_notes_dir(project) / "reviews" / review_scope
    review_dir.mkdir(parents=True, exist_ok=True)
    corpus_lines = ["| Annotation | Citekey |", "|---|---|"]
    for ck in citekeys:
        corpus_lines.append(f"| [NEW] | {ck} |")
    (review_dir / "_corpus.md").write_text("\n".join(corpus_lines) + "\n", encoding="utf-8")
    return ledger_mod.write_corpus_ledger(
        review_dir,
        review_scope=review_scope,
        literature_dir=cfg.project_notes_dir(project) / "literature",
        literature_root=cfg.literature_root,
    )


# ---------------------------------------------------------------------------
# 1. Registry = the overlay dir
# ---------------------------------------------------------------------------

class TestRegistryIsOverlayDir:
    def test_no_overlay_dir_is_empty_list_not_error(self, cfg):
        rows = literature_mod.cmd_list("demo-research", config=cfg)
        assert rows == []

    def test_enumerates_every_adopted_overlay(self, cfg):
        _make_adopted_paper(cfg, "demo-research", "smith2024")
        _make_adopted_paper(cfg, "demo-research", "jones2023")
        rows = literature_mod.cmd_list("demo-research", config=cfg)
        assert sorted(r["citekey"] for r in rows) == ["jones2023", "smith2024"]

    def test_distilled_but_not_adopted_is_invisible(self, cfg):
        """A core with no overlay in THIS project never appears — a
        different project's adoption doesn't leak into this list."""
        _make_adopted_paper(cfg, "demo-litreview", "smith2024")
        rows = literature_mod.cmd_list("demo-research", config=cfg)
        assert rows == []


# ---------------------------------------------------------------------------
# 2 + 3. Enrichment via the ledger, zero recomputation
# ---------------------------------------------------------------------------

class TestLedgerEnrichmentZeroRecomputation:
    def test_enriched_from_real_ledger(self, cfg):
        _make_adopted_paper(cfg, "demo-research", "smith2024", doi="10.1234/smith2024")
        _write_ledger_for(cfg, "demo-research", "scope1", ["smith2024"])

        rows = literature_mod.cmd_list("demo-research", config=cfg)
        assert len(rows) == 1
        row = rows[0]
        assert row["citekey"] == "smith2024"
        assert row["resolving_ids"] == "doi:10.1234/smith2024"
        assert row["conformant"] is True
        assert row["in_ledger"] is True

    def test_zero_recomputation_ledger_functions_never_called(self, cfg, monkeypatch):
        """The load-bearing proof: patch review.ledger's resolving-id and
        conformance functions to raise — cmd_list must still return the
        correct enrichment, because it only re-parses the ALREADY-WRITTEN
        ledger table, never re-derives it."""
        _make_adopted_paper(cfg, "demo-research", "smith2024", doi="10.1234/smith2024")
        _write_ledger_for(cfg, "demo-research", "scope1", ["smith2024"])

        def _boom(*_a, **_kw):
            raise AssertionError("literature.cmd_list must never recompute ledger content")

        monkeypatch.setattr(ledger_mod, "_resolving_ids_for_note", _boom)
        monkeypatch.setattr(ledger_mod, "_k_block", _boom)

        rows = literature_mod.cmd_list("demo-research", config=cfg)
        assert rows[0]["resolving_ids"] == "doi:10.1234/smith2024"
        assert rows[0]["conformant"] is True

    def test_nonconformant_citekey_surfaced_from_ledger(self, cfg):
        _make_adopted_paper(cfg, "demo-research", "Not_A_Citekey")
        _write_ledger_for(cfg, "demo-research", "scope1", ["Not_A_Citekey"])
        rows = literature_mod.cmd_list("demo-research", config=cfg)
        assert rows[0]["conformant"] is False


# ---------------------------------------------------------------------------
# 4. Honest gaps — never fabricated
# ---------------------------------------------------------------------------

class TestHonestGaps:
    def test_citekey_never_in_any_ledger_is_honest_gap(self, cfg):
        _make_adopted_paper(cfg, "demo-research", "smith2024")
        # No ledger ever written for this project.
        rows = literature_mod.cmd_list("demo-research", config=cfg)
        assert rows[0]["resolving_ids"] == ""
        assert rows[0]["conformant"] is None
        assert rows[0]["in_ledger"] is False

    def test_multiple_ledgers_merge_across_scopes(self, cfg):
        _make_adopted_paper(cfg, "demo-research", "smith2024", doi="10.1234/smith2024")
        _make_adopted_paper(cfg, "demo-research", "jones2023")
        _write_ledger_for(cfg, "demo-research", "scope-a", ["smith2024"])
        _write_ledger_for(cfg, "demo-research", "scope-b", ["jones2023"])
        rows = {r["citekey"]: r for r in literature_mod.cmd_list("demo-research", config=cfg)}
        assert rows["smith2024"]["in_ledger"] is True
        assert rows["jones2023"]["in_ledger"] is True


# ---------------------------------------------------------------------------
# 5. Dangling pointer surfaced, never silently dropped
# ---------------------------------------------------------------------------

class TestDanglingPointerSurfaced:
    def test_dangling_central_pointer_is_an_error_row_not_a_silent_drop(self, cfg):
        overlay_dir = cfg.project_notes_dir("demo-research") / "literature"
        overlay_dir.mkdir(parents=True, exist_ok=True)
        (overlay_dir / "ghost2024.md").write_text(
            "---\ntype: literature\ntitle: Ghost\ncentral: ghost2024\n---\n\n"
            "## Concept edges\n\n",
            encoding="utf-8",
        )
        # No central core ever created — dangling.
        rows = literature_mod.cmd_list("demo-research", config=cfg)
        assert len(rows) == 1
        assert rows[0]["citekey"] == "ghost2024"
        assert rows[0]["error"] is not None
        assert "dangling" in rows[0]["error"].lower() or "central" in rows[0]["error"].lower()


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

class TestCLIWiring:
    def test_build_parser_list_subcommand(self):
        p = literature_mod.build_parser()
        args = p.parse_args(["list", "demo-research"])
        assert args.literature_cmd == "list"
        assert args.project == "demo-research"

    def test_run_no_adopted_papers_prints_message_exit_0(self, cfg, capsys):
        import argparse
        args = argparse.Namespace(literature_cmd="list", project="demo-research")
        rc = literature_mod.run(args)
        assert rc == 0
        assert "No adopted literature" in capsys.readouterr().out

    def test_run_prints_enriched_rows(self, cfg, capsys):
        import argparse
        _make_adopted_paper(cfg, "demo-research", "smith2024", doi="10.1234/smith2024")
        _write_ledger_for(cfg, "demo-research", "scope1", ["smith2024"])
        args = argparse.Namespace(literature_cmd="list", project="demo-research")
        rc = literature_mod.run(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "smith2024" in out
        assert "conformant=yes" in out

    def test_registered_in_verb_registry(self):
        from research_vault.cli import _VERB_REGISTRY
        assert "literature" in _VERB_REGISTRY
        assert _VERB_REGISTRY["literature"]["module"] == "research_vault.literature"
        assert _VERB_REGISTRY["literature"]["when_to_use"]


# ---------------------------------------------------------------------------
# PR-B acceptance item 3: `rv init` scaffolds literature_root + `rv project
# new` scaffolds the per-project overlay dir.
# ---------------------------------------------------------------------------

class TestScaffolding:
    def test_rv_init_scaffolds_literature_root(self, tmp_path):
        from research_vault.config import load_config, reset_config_cache
        from research_vault.init import cmd_init_in_dir

        rc = cmd_init_in_dir(str(tmp_path))
        assert rc == 0

        import os
        old = os.environ.get("RESEARCH_VAULT_CONFIG")
        os.environ["RESEARCH_VAULT_CONFIG"] = str(tmp_path / "research_vault.toml")
        reset_config_cache()
        try:
            cfg = load_config(reload=True)
            assert cfg.literature_root.exists()
            assert cfg.literature_root.is_dir()
            assert cfg.literature_root == tmp_path / "notes" / "literature"
        finally:
            if old is None:
                os.environ.pop("RESEARCH_VAULT_CONFIG", None)
            else:
                os.environ["RESEARCH_VAULT_CONFIG"] = old
            reset_config_cache()

    def test_rv_project_new_scaffolds_overlay_dir(self, cfg, tmp_path):
        from research_vault import project as project_mod

        source_dir = tmp_path / "new-project-repo"
        rc = project_mod.cmd_new(
            "freshproj", "fp", str(source_dir), roster=[],
            config_path=None,
        )
        assert rc == 0
        overlay_dir = source_dir / "literature"
        assert overlay_dir.exists()
        assert overlay_dir.is_dir()
