"""test_citekey_pipeline.py — PR-4/K-2/K-3: the Zotero-free citekey compute
+ stamp path, and the one-shot migration verb.

Hermetic (tmp_instance): no Zotero/asta network calls — these paths never
touch cite.py's Zotero subprocess bridge; they read/stamp a note's own
frontmatter.
"""
from __future__ import annotations

import json

import pytest

from research_vault import note as note_mod
from research_vault import research as research_mod
from research_vault.cite import CITEKEY_RE, CITEKEY_SENTINEL
from research_vault.config import load_config
from research_vault.sources.identifiers import stamp_note_frontmatter


@pytest.fixture
def cfg(tmp_instance):
    return load_config(reload=True)


def _lit_dir(cfg):
    return cfg.project_notes_dir("demo-research") / "literature"


def _stamp(path, **fields):
    """Stamp frontmatter fields into a note via the real stamp-or-inject
    helper (reuse over duplicate) — handles BOTH replacing an existing
    placeholder field (e.g. citekey:, already scaffolded by cmd_new) AND
    injecting a brand-new field (e.g. authors:/year:, which the 5-move
    reading protocol adds by hand and cmd_new does not pre-scaffold)."""
    stamp_note_frontmatter(path, {k: str(v) for k, v in fields.items()})


# ---------------------------------------------------------------------------
# K-2: compute_and_stamp_citekey
# ---------------------------------------------------------------------------

def test_compute_and_stamp_citekey_resolved_metadata(cfg):
    path = note_mod.cmd_new("demo-research", "literature", "A Study of Foo", config=cfg)
    _stamp(path, title="A Study of Foo", authors="Smith, Jane", year="2023")

    citekey = research_mod.compute_and_stamp_citekey(path, _lit_dir(cfg))

    assert CITEKEY_RE.match(citekey)
    assert citekey.startswith("smith")
    assert path.read_text().count(f"citekey: {citekey}") == 1


def test_compute_and_stamp_citekey_unresolved_metadata_writes_sentinel(cfg):
    """No title/year filled in yet -> the visible sentinel, never a guess."""
    path = note_mod.cmd_new("demo-research", "literature", "A paper", config=cfg)
    # title IS filled by cmd_new (the note title arg) but year is not —
    # unresolved on year alone must still fail closed to the sentinel.
    citekey = research_mod.compute_and_stamp_citekey(path, _lit_dir(cfg))

    assert citekey == CITEKEY_SENTINEL
    assert f"citekey: {CITEKEY_SENTINEL}" in path.read_text()
    assert not CITEKEY_RE.match(citekey)


def test_compute_and_stamp_citekey_missing_note_raises(cfg):
    missing = _lit_dir(cfg) / "does-not-exist.md"
    with pytest.raises(FileNotFoundError):
        research_mod.compute_and_stamp_citekey(missing, _lit_dir(cfg))


def test_compute_and_stamp_citekey_disambiguates_against_existing_notes(cfg):
    """Two notes with the same family/title/year get a/b disambiguation."""
    p1 = note_mod.cmd_new("demo-research", "literature", "Repeat Study", config=cfg, note_id="p1")
    _stamp(p1, title="Repeat Study", authors="Lee, Ann", year="2020")
    k1 = research_mod.compute_and_stamp_citekey(p1, _lit_dir(cfg))

    p2 = note_mod.cmd_new("demo-research", "literature", "Repeat Study", config=cfg, note_id="p2")
    _stamp(p2, title="Repeat Study", authors="Lee, Ann", year="2020")
    k2 = research_mod.compute_and_stamp_citekey(p2, _lit_dir(cfg))

    assert k1 != k2
    assert k2 == k1 + "a"


def test_cli_research_citekey_stamps_resolved(cfg, capsys):
    note_mod.cmd_new("demo-research", "literature", "CLI Paper", config=cfg, note_id="cli-paper")
    # PR-A: `rv research citekey` resolves + stamps the CENTRAL CORE
    # (title/authors/year/citekey are all intrinsic content).
    core_path = cfg.literature_root / "cli-paper.md"
    _stamp(core_path, title="CLI Paper", authors="Doe, Ravi", year="2021")

    from research_vault.cli import main
    result = main(["research", "citekey", "demo-research", "cli-paper"])
    assert result == 0
    out = capsys.readouterr().out
    assert "Stamped citekey:" in out


def test_cli_research_citekey_fails_closed_on_unresolved(cfg, capsys):
    note_mod.cmd_new("demo-research", "literature", "Bare Paper", config=cfg, note_id="bare-paper")

    from research_vault.cli import main
    result = main(["research", "citekey", "demo-research", "bare-paper"])
    assert result == 1  # fail-closed — never a silent success on a sentinel
    err = capsys.readouterr().err
    assert CITEKEY_SENTINEL in err


# ---------------------------------------------------------------------------
# K-3: migrate_citekeys
# ---------------------------------------------------------------------------

def test_migrate_stamps_absent_and_non_conformant_never_renames(cfg):
    lit_dir = _lit_dir(cfg)

    p_absent = note_mod.cmd_new("demo-research", "literature", "No Key Paper", config=cfg, note_id="no-key")
    _stamp(p_absent, title="No Key Paper", authors="Kim, Sara", year="2019")

    p_bad = note_mod.cmd_new("demo-research", "literature", "Old Scheme Paper", config=cfg, note_id="2005.14165")
    _stamp(p_bad, title="Old Scheme Paper", authors="Ng, Wei", year="2018", citekey="2005.14165")

    p_good = note_mod.cmd_new("demo-research", "literature", "Already Fine", config=cfg, note_id="already-fine")
    _stamp(p_good, title="Already Fine", authors="Diaz, Ana", year="2017", citekey="diazAlreadyFine2017")

    ledger_path = lit_dir / "_citekey_migration_ledger.json"
    result = research_mod.migrate_citekeys(lit_dir, ledger_path)

    assert result["already_conformant"] == 1
    changed_notes = {c["note"] for c in result["changed"]}
    assert changed_notes == {"no-key.md", "2005.14165.md"}
    assert result["unresolved"] == []

    # Filenames are UNTOUCHED — never renamed.
    assert p_absent.exists() and p_bad.exists() and p_good.exists()

    # citekey: fields ARE rewritten to conformant keys.
    for p in (p_absent, p_bad):
        content = p.read_text()
        m = [ln for ln in content.splitlines() if ln.startswith("citekey:")][0]
        stamped = m.split(":", 1)[1].strip()
        assert CITEKEY_RE.match(stamped)

    # the old->new map is recorded in the ledger.
    ledger = json.loads(ledger_path.read_text())
    old_values = {e["old"] for e in ledger}
    assert "2005.14165" in old_values  # the old non-conformant citekey value
    assert "no-key" in old_values  # fallback to filename stem when absent


def test_migrate_dry_run_writes_nothing(cfg):
    lit_dir = _lit_dir(cfg)
    p = note_mod.cmd_new("demo-research", "literature", "Dry Run Paper", config=cfg, note_id="dry-run")
    _stamp(p, title="Dry Run Paper", authors="Fox, Ivy", year="2022")
    before = p.read_text()

    ledger_path = lit_dir / "_citekey_migration_ledger.json"
    result = research_mod.migrate_citekeys(lit_dir, ledger_path, dry_run=True)

    assert len(result["changed"]) == 1
    assert p.read_text() == before  # untouched
    assert not ledger_path.exists()  # no ledger write on dry-run


def test_migrate_unresolved_gets_sentinel_and_is_surfaced(cfg):
    lit_dir = _lit_dir(cfg)
    p = note_mod.cmd_new("demo-research", "literature", "No Year Yet", config=cfg, note_id="no-year")
    # title present (cmd_new sets it), year left blank — unresolvable.

    ledger_path = lit_dir / "_citekey_migration_ledger.json"
    result = research_mod.migrate_citekeys(lit_dir, ledger_path)

    assert result["unresolved"] == ["no-year.md"]
    assert f"citekey: {CITEKEY_SENTINEL}" in p.read_text()


def test_migrate_within_batch_disambiguation(cfg):
    """Two notes migrated in the SAME run with the same family/title/year
    must not collide with each other."""
    lit_dir = _lit_dir(cfg)
    p1 = note_mod.cmd_new("demo-research", "literature", "Twin Study", config=cfg, note_id="twin1")
    _stamp(p1, title="Twin Study", authors="Cho, Min", year="2016")
    p2 = note_mod.cmd_new("demo-research", "literature", "Twin Study", config=cfg, note_id="twin2")
    _stamp(p2, title="Twin Study", authors="Cho, Min", year="2016")

    ledger_path = lit_dir / "_citekey_migration_ledger.json"
    result = research_mod.migrate_citekeys(lit_dir, ledger_path)

    new_keys = [c["new"] for c in result["changed"]]
    assert len(new_keys) == len(set(new_keys))  # no collision


def test_cli_migrate_citekeys(cfg, capsys):
    lit_dir = _lit_dir(cfg)
    p = note_mod.cmd_new("demo-research", "literature", "CLI Migrate Paper", config=cfg, note_id="cli-migrate")
    _stamp(p, title="CLI Migrate Paper", authors="Osei, Kofi", year="2015")

    from research_vault.cli import main
    result = main(["research", "migrate-citekeys", "demo-research"])
    assert result == 0
    out = capsys.readouterr().out
    assert "migrated" in out
    assert (lit_dir / "_citekey_migration_ledger.json").exists()
