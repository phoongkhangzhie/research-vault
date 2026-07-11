# SPDX-License-Identifier: AGPL-3.0-or-later
"""test_research_add_identifier_persistence.py — `rv research add` write path
(identifier-persistence): the full normalized external-id set resolved at
add-time is stamped into the already-filed literature note's frontmatter.

Hermetic: `subprocess.run` (both `rv cite add` and asta's `SemanticScholarAdapter.get`
shell out through it) is faked; no live Zotero/asta calls.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from research_vault import research as research_mod
from research_vault.sources.identifiers import read_external_ids_from_note


def _cfg(tmp_path: Path, monkeypatch, project: str = "my-proj") -> Path:
    project_notes_dir = tmp_path / "notes" / project
    cfg_path = tmp_path / "research_vault.toml"
    cfg_path.write_text(
        f'[projects.{project}]\n'
        f'source_dir = "{project_notes_dir}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_path))
    from research_vault.config import reset_config_cache
    reset_config_cache()
    return project_notes_dir


def _file_literature_note(notes_dir: Path, citekey: str) -> Path:
    """PR-A: doi/arxiv_id/pmcid/openalex/pmid/s2 are intrinsic (core-only)
    content — file the fixture in the CENTRAL CORE (notes_dir.parent /
    "literature", i.e. the default cfg.literature_root = notes_root/
    literature), not the per-project overlay dir `notes_dir` points at."""
    core_dir = notes_dir.parent / "literature"
    core_dir.mkdir(parents=True, exist_ok=True)
    note = core_dir / f"{citekey}.md"
    note.write_text(
        f"---\ntype: literature\ncitekey: {citekey}\ndoi: \narxiv_id: \n"
        "pmcid: \nopenalex: \npmid: \ns2: \n---\n\nBody.\n",
        encoding="utf-8",
    )
    return note


_S2_GET_RESPONSE = {
    "title": "Attention Is All You Need",
    "year": 2017,
    "authors": [{"name": "Ashish Vaswani"}],
    "externalIds": {
        "DOI": "10.5555/3295222.3295349",
        "ArXiv": "1706.03762",
        "CorpusId": "13756489",
        "PMID": "31000111",
    },
    "abstract": "",
    "citationCount": 1,
}


def _fake_subprocess_run(citekey: str, s2_response: dict | None = _S2_GET_RESPONSE):
    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.stderr = ""
        if cmd[:3] == ["rv", "cite", "add"]:
            r.returncode = 0
            r.stdout = f"Added: {citekey}\n"
            return r
        if cmd[:3] == ["asta", "papers", "get"]:
            r.returncode = 0 if s2_response is not None else 1
            r.stdout = json.dumps(s2_response) if s2_response is not None else ""
            return r
        raise AssertionError(f"unexpected subprocess call: {cmd}")
    return fake_run


class TestCmdAddPersistsIdentifiers:
    def test_every_present_id_lands_in_note_frontmatter(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        notes_dir = _cfg(tmp_path, monkeypatch)
        note = _file_literature_note(notes_dir, "vaswani2017")

        monkeypatch.setattr(subprocess, "run", _fake_subprocess_run("vaswani2017"))
        monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)
        monkeypatch.setattr(research_mod, "_preflight_zotero", lambda: None)

        args = argparse.Namespace(
            ident="10.5555/3295222.3295349", project="my-proj",
            force=False, dry_run=False,
        )
        rc = research_mod.cmd_add(args)
        assert rc == 0

        ids = read_external_ids_from_note(note)
        # doi: resolved from the ident itself (cite._resolve_ident) — authoritative.
        assert ids["doi"] == "10.5555/3295222.3295349"
        # arxiv/s2/pmid: filled in by the S2 enrichment lookup.
        assert ids["arxiv"] == "1706.03762"
        assert ids["s2"] == "13756489"
        assert ids["pmid"] == "31000111"

    def test_dry_run_persists_nothing(self, tmp_path: Path, monkeypatch) -> None:
        notes_dir = _cfg(tmp_path, monkeypatch)
        note = _file_literature_note(notes_dir, "vaswani2017")

        monkeypatch.setattr(subprocess, "run", _fake_subprocess_run("vaswani2017"))
        monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)
        monkeypatch.setattr(research_mod, "_preflight_zotero", lambda: None)

        args = argparse.Namespace(
            ident="10.5555/3295222.3295349", project="my-proj",
            force=False, dry_run=True,
        )
        rc = research_mod.cmd_add(args)
        assert rc == 0
        assert read_external_ids_from_note(note) == {}

    def test_note_not_filed_yet_is_a_clean_noop(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        _cfg(tmp_path, monkeypatch)  # no literature note filed

        monkeypatch.setattr(subprocess, "run", _fake_subprocess_run("vaswani2017"))
        monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)
        monkeypatch.setattr(research_mod, "_preflight_zotero", lambda: None)

        args = argparse.Namespace(
            ident="10.5555/3295222.3295349", project="my-proj",
            force=False, dry_run=False,
        )
        rc = research_mod.cmd_add(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "does not exist yet" in out
        # The recovery pointer must be actionable and factually correct: nothing
        # was persisted (the note didn't exist to write into), so the correct
        # recovery is to re-run `rv research add` (re-resolves + stamps) after
        # filing the note — NOT `rv research fulltext`, which would just read
        # the (empty) note and silently degrade to abstract-only.
        assert "rv research add" in out
        assert "rv research fulltext" not in out

    def test_s2_enrichment_failure_degrades_gracefully(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        """asta being unavailable/failing must not abort the add — the
        ident-derived id (doi/arxiv) still persists."""
        notes_dir = _cfg(tmp_path, monkeypatch)
        note = _file_literature_note(notes_dir, "vaswani2017")

        monkeypatch.setattr(
            subprocess, "run", _fake_subprocess_run("vaswani2017", s2_response=None),
        )
        monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)
        monkeypatch.setattr(research_mod, "_preflight_zotero", lambda: None)

        args = argparse.Namespace(
            ident="10.5555/3295222.3295349", project="my-proj",
            force=False, dry_run=False,
        )
        rc = research_mod.cmd_add(args)
        assert rc == 0
        ids = read_external_ids_from_note(note)
        assert ids == {"doi": "10.5555/3295222.3295349"}
