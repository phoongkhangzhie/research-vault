"""test_literature_registry.py — `rv literature list <project>`, the
project's adopted-literature registry.

Design of record: internal design note (the architect, 2026-07-10);
re-derived for the overlay unwind (0.3.2 — literature became
shared-canonical, no per-project overlay).

Covers:
  1. Registry = mechanical corpus-ledger membership (the union of every
     citekey across this project's `_corpus_ledger.md` files) — NOT a
     filesystem dir any more. No ledger ever written -> empty list, never
     an error. A note DISTILLED into the shared store but never recorded
     in ANY ledger is correctly invisible (adoption is membership, not
     mere existence in the shared store).
  2. Enrichment comes from an already-written `_corpus_ledger.md` — the
     resolving ids + conformance verdict are read back from the ledger's
     rendered table, not recomputed.
  3. ★ Zero-recomputation proof: `review.ledger`'s own resolving-id /
     conformance functions are NEVER called by `literature.cmd_list` —
     patched to raise, cmd_list must still return correct enrichment
     (because it only re-parses the ledger's already-rendered table).
  4. Every row here comes FROM a ledger by construction — `in_ledger` is
     always True (there is no more "distilled but never ledgered, yet
     still listed" honest-gap case; that state is now simply absent from
     the list, not a listed-with-a-gap row).
  5. A ledgered citekey whose shared note doesn't exist yet is surfaced
     (an `error` row, "adopted but not materialized"), never silently
     dropped.

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


def _make_paper(cfg, note_id, *, doi=None):
    """File a shared-canonical literature note (the overlay unwind, 0.3.2) + optionally
    stamp its doi, mirroring a real relate-node distillation. Filing a
    note alone is NOT adoption any more — membership comes from a ledger
    (see ``_write_ledger_for``)."""
    note_path = note_mod.cmd_new("demo-research", "literature", "A Paper", config=cfg, note_id=note_id)
    if doi is not None:
        text = note_path.read_text(encoding="utf-8")
        text = text.replace("doi: \n", f"doi: {doi}\n")
        note_path.write_text(text, encoding="utf-8")
    return note_path


def _write_ledger_for(cfg, project, review_scope, citekeys):
    """A real, minimally-populated `_corpus_ledger.md` — genuinely produced
    by review.ledger.write_corpus_ledger (never hand-crafted), so the
    canonical-key map + accepted/in_corpus/new counts are the SAME
    machinery a real review run produces. This is what makes a citekey
    "adopted" under the overlay unwind's mechanical-membership model."""
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
# 1. Registry = mechanical corpus-ledger membership
# ---------------------------------------------------------------------------

class TestRegistryIsLedgerMembership:
    def test_no_ledger_is_empty_list_not_error(self, cfg):
        rows = literature_mod.cmd_list("demo-research", config=cfg)
        assert rows == []

    def test_distilled_but_never_ledgered_is_invisible(self, cfg):
        """0.3.2 (the overlay unwind): filing a shared note is not itself adoption — with
        no ledger ever written, membership is honestly empty, not a
        fabricated row for a paper that merely exists in the shared
        store."""
        _make_paper(cfg, "smith2024")
        rows = literature_mod.cmd_list("demo-research", config=cfg)
        assert rows == []

    def test_enumerates_every_ledgered_citekey(self, cfg):
        _make_paper(cfg, "smith2024")
        _make_paper(cfg, "jones2023")
        _write_ledger_for(cfg, "demo-research", "scope1", ["smith2024", "jones2023"])
        rows = literature_mod.cmd_list("demo-research", config=cfg)
        assert sorted(r["citekey"] for r in rows) == ["jones2023", "smith2024"]

    def test_another_projects_ledger_never_leaks_in(self, cfg):
        """A ledger this project never wrote (a different project's
        membership) never leaks into this project's registry."""
        _make_paper(cfg, "smith2024")
        _write_ledger_for(cfg, "demo-litreview", "scope1", ["smith2024"])
        rows = literature_mod.cmd_list("demo-research", config=cfg)
        assert rows == []


# ---------------------------------------------------------------------------
# 2 + 3. Enrichment via the ledger, zero recomputation
# ---------------------------------------------------------------------------

class TestLedgerEnrichmentZeroRecomputation:
    def test_enriched_from_real_ledger(self, cfg):
        _make_paper(cfg, "smith2024", doi="10.1234/smith2024")
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
        _make_paper(cfg, "smith2024", doi="10.1234/smith2024")
        _write_ledger_for(cfg, "demo-research", "scope1", ["smith2024"])

        def _boom(*_a, **_kw):
            raise AssertionError("literature.cmd_list must never recompute ledger content")

        monkeypatch.setattr(ledger_mod, "_resolving_ids_for_note", _boom)
        monkeypatch.setattr(ledger_mod, "_k_block", _boom)

        rows = literature_mod.cmd_list("demo-research", config=cfg)
        assert rows[0]["resolving_ids"] == "doi:10.1234/smith2024"
        assert rows[0]["conformant"] is True

    def test_nonconformant_citekey_surfaced_from_ledger(self, cfg):
        _make_paper(cfg, "Not_A_Citekey")
        _write_ledger_for(cfg, "demo-research", "scope1", ["Not_A_Citekey"])
        rows = literature_mod.cmd_list("demo-research", config=cfg)
        assert rows[0]["conformant"] is False


# ---------------------------------------------------------------------------
# 4. Every row is in_ledger by construction; role is no longer mechanical
# ---------------------------------------------------------------------------

class TestRoleIsNoLongerAField:
    def test_in_ledger_always_true(self, cfg):
        """0.3.2 (the overlay unwind): since membership IS ledger membership, every row
        this registry ever returns came from a ledger — there is no more
        'listed but never ledgered' honest-gap row."""
        _make_paper(cfg, "smith2024")
        _write_ledger_for(cfg, "demo-research", "scope1", ["smith2024"])
        rows = literature_mod.cmd_list("demo-research", config=cfg)
        assert rows[0]["in_ledger"] is True

    def test_role_is_always_none(self, cfg):
        """Role moved to curated project MOCs (the overlay unwind, 0.3.2) —
        this mechanical registry never enumerates it."""
        _make_paper(cfg, "smith2024")
        _write_ledger_for(cfg, "demo-research", "scope1", ["smith2024"])
        rows = literature_mod.cmd_list("demo-research", config=cfg)
        assert rows[0]["role"] is None

    def test_multiple_ledgers_merge_across_scopes(self, cfg):
        _make_paper(cfg, "smith2024", doi="10.1234/smith2024")
        _make_paper(cfg, "jones2023")
        _write_ledger_for(cfg, "demo-research", "scope-a", ["smith2024"])
        _write_ledger_for(cfg, "demo-research", "scope-b", ["jones2023"])
        rows = {r["citekey"]: r for r in literature_mod.cmd_list("demo-research", config=cfg)}
        assert rows["smith2024"]["in_ledger"] is True
        assert rows["jones2023"]["in_ledger"] is True


# ---------------------------------------------------------------------------
# 5. Adopted-but-not-materialized surfaced, never silently dropped
# ---------------------------------------------------------------------------

class TestAdoptedButNotMaterializedSurfaced:
    def test_ledgered_citekey_with_no_shared_note_is_an_error_row(self, cfg):
        """A citekey this project's ledger claims membership for, but
        whose shared note was never filed (or was later removed) —
        surfaced as an error row, never silently dropped from the list."""
        _write_ledger_for(cfg, "demo-research", "scope1", ["ghost2024"])
        rows = literature_mod.cmd_list("demo-research", config=cfg)
        assert len(rows) == 1
        assert rows[0]["citekey"] == "ghost2024"
        assert rows[0]["error"] is not None
        assert "not yet materialized" in rows[0]["error"].lower() or "no shared" in rows[0]["error"].lower()


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
        _make_paper(cfg, "smith2024", doi="10.1234/smith2024")
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
# `rv init` scaffolds literature_root (the shared store); `rv project new`
# still scaffolds a project-scoped literature/ dir as part of the generic
# OKF-type scaffold (note.scaffold_okf_dirs — unused for literature content
# post-overlay-unwind, but harmless: an empty dir a shared type's project-side
# reader never reads).
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

    def test_rv_project_new_scaffolds_generic_type_dirs(self, cfg, tmp_path):
        from research_vault import project as project_mod

        source_dir = tmp_path / "new-project-repo"
        rc = project_mod.cmd_new(
            "freshproj", "fp", str(source_dir), roster=[],
            config_path=None,
        )
        assert rc == 0
        # scaffold_okf_dirs creates every OKF_TYPES dir generically —
        # this project-scoped literature/ dir is never read by a shared-
        # type reader post-unwind, but scaffolding it is harmless.
        literature_dir = source_dir / "literature"
        assert literature_dir.exists()
        assert literature_dir.is_dir()
