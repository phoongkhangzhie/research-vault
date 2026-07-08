"""test_research_corpus_dedup.py — TDD tests for corpus-dedup annotation (SR-LR-1 prereq).

Design invariants tested:
  1.  _load_corpus_index builds DOI + ArXiv id → citekey lookup from library.json
  2.  _corpus_annotation returns [IN-CORPUS:<citekey>] for a matching DOI
  3.  _corpus_annotation returns [IN-CORPUS:<citekey>] for a matching ArXiv id
  4.  _corpus_annotation returns [NEW] for a paper not in the corpus
  5.  _print_candidates prints [IN-CORPUS:…] annotation when corpus_index provided
  6.  _print_candidates prints [NEW] annotation for unmatched candidates
  7.  cmd_find — annotated output with --project wired to corpus
  8.  cmd_cited_by — annotated output with --project wired to corpus
  9.  cmd_references — annotated output with --project wired to corpus
 10.  Missing --project → no crash, graceful output (no annotation or [NEW] for all)
 11.  refs_path missing / empty library.json → graceful (treats all as [NEW])
 12.  Case-insensitive DOI matching (Zotero may uppercase)
 13.  ArXiv version suffix stripped (2005.14165v2 → matches 2005.14165)
 14.  references --project help text describes corpus annotation (no overpromise)
 15.  _load_corpus_index falls back to citationKey-less items that have Citation Key in extra

All tests hermetic: asta and file I/O are mocked via monkeypatch + tmp_path.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault import research as research_mod


# ---------------------------------------------------------------------------
# Fixtures / shared data
# ---------------------------------------------------------------------------

# A minimal Zotero library.json with two items
ZOTERO_ITEM_DOI = {
    "key": "ABCD1234",
    "data": {
        "citationKey": "vaswani2017Attention",
        "DOI": "10.48550/ARXIV.1706.03762",
        "title": "Attention Is All You Need",
        "itemType": "journalArticle",
        "extra": "",
    },
}

ZOTERO_ITEM_ARXIV = {
    "key": "EFGH5678",
    "data": {
        "citationKey": "devlin2018BERT",
        "archiveID": "arXiv:1810.04805",
        "DOI": "",
        "title": "BERT",
        "itemType": "preprint",
        "extra": "",
    },
}

# Item with no citationKey field — citekey in extra only
ZOTERO_ITEM_EXTRA_CK = {
    "key": "IJKL9012",
    "data": {
        "DOI": "10.18653/v1/N19-1423",
        "title": "BERT NAACL paper",
        "itemType": "conferencePaper",
        "extra": "Citation Key: devlinBERT2019",
    },
}

SAMPLE_CORPUS = [ZOTERO_ITEM_DOI, ZOTERO_ITEM_ARXIV, ZOTERO_ITEM_EXTRA_CK]

# S2 candidate papers
CANDIDATE_DOI_MATCH = {
    "title": "Attention Is All You Need",
    "year": 2017,
    "authors": [{"name": "Ashish Vaswani"}],
    "externalIds": {"DOI": "10.48550/ARXIV.1706.03762", "ArXiv": "1706.03762"},
    "citationCount": 50000,
}

CANDIDATE_ARXIV_MATCH = {
    "title": "BERT: Pre-training of Deep Bidirectional Transformers",
    "year": 2018,
    "authors": [{"name": "Jacob Devlin"}],
    "externalIds": {"ArXiv": "1810.04805"},
    "citationCount": 40000,
}

CANDIDATE_NEW = {
    "title": "A Brand New Paper",
    "year": 2024,
    "authors": [{"name": "Alice Smith"}],
    "externalIds": {"ArXiv": "2401.99999"},
    "citationCount": 5,
}

CANDIDATE_EXTRA_CK_MATCH = {
    "title": "NAACL BERT paper",
    "year": 2019,
    "authors": [{"name": "Jacob Devlin"}],
    "externalIds": {"DOI": "10.18653/v1/N19-1423"},
    "citationCount": 1000,
}


def _make_library_json(tmp_path: Path, items: list[dict] | None = None) -> Path:
    lib = tmp_path / "library.json"
    lib.write_text(json.dumps(items if items is not None else SAMPLE_CORPUS), encoding="utf-8")
    return lib


def _fake_asta_run(papers: list[dict], returncode: int = 0):
    """Return a fake subprocess.run callable that emits papers as S2 search results."""
    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = returncode
        if "citations" in cmd:
            # cited-by format: data → [{citingPaper: {...}}]
            r.stdout = json.dumps({"data": [{"citingPaper": p} for p in papers]})
        elif "get" in cmd and "references" not in " ".join(cmd):
            # get format for references
            r.stdout = json.dumps({"references": papers})
        elif "search" in cmd:
            r.stdout = json.dumps({"data": papers})
        else:
            # references / get with --fields references.*
            r.stdout = json.dumps({"references": papers})
        r.stderr = ""
        return r
    return fake_run


# ---------------------------------------------------------------------------
# Test 1: _load_corpus_index builds DOI + ArXiv id → citekey lookup
# ---------------------------------------------------------------------------

def test_load_corpus_index_doi(tmp_path: Path) -> None:
    """_load_corpus_index must index DOI → citekey."""
    lib = _make_library_json(tmp_path)
    idx = research_mod._load_corpus_index(str(lib))
    # DOI is lowercased in the index key
    assert "10.48550/arxiv.1706.03762" in idx
    assert idx["10.48550/arxiv.1706.03762"] == "vaswani2017Attention"


def test_load_corpus_index_arxiv(tmp_path: Path) -> None:
    """_load_corpus_index must index ArXiv id → citekey (strip 'arXiv:' prefix)."""
    lib = _make_library_json(tmp_path)
    idx = research_mod._load_corpus_index(str(lib))
    assert "1810.04805" in idx
    assert idx["1810.04805"] == "devlin2018BERT"


def test_load_corpus_index_extra_citekey(tmp_path: Path) -> None:
    """_load_corpus_index must parse 'Citation Key: <ck>' from data.extra."""
    lib = _make_library_json(tmp_path)
    idx = research_mod._load_corpus_index(str(lib))
    assert "10.18653/v1/n19-1423" in idx
    assert idx["10.18653/v1/n19-1423"] == "devlinBERT2019"


def test_load_corpus_index_missing_path(tmp_path: Path) -> None:
    """_load_corpus_index must return empty dict when refs_path does not exist."""
    idx = research_mod._load_corpus_index(str(tmp_path / "nonexistent.json"))
    assert idx == {}


def test_load_corpus_index_none_path() -> None:
    """_load_corpus_index must return empty dict when refs_path is None."""
    idx = research_mod._load_corpus_index(None)
    assert idx == {}


def test_load_corpus_index_empty_library(tmp_path: Path) -> None:
    """_load_corpus_index must return empty dict for an empty library.json."""
    lib = _make_library_json(tmp_path, items=[])
    idx = research_mod._load_corpus_index(str(lib))
    assert idx == {}


# ---------------------------------------------------------------------------
# Test 2–4: _corpus_annotation
# ---------------------------------------------------------------------------

def test_corpus_annotation_doi_match(tmp_path: Path) -> None:
    """_corpus_annotation returns [IN-CORPUS:<citekey>] for a matching DOI."""
    lib = _make_library_json(tmp_path)
    idx = research_mod._load_corpus_index(str(lib))
    result = research_mod._corpus_annotation(CANDIDATE_DOI_MATCH, idx)
    assert result == "[IN-CORPUS:vaswani2017Attention]"


def test_corpus_annotation_arxiv_match(tmp_path: Path) -> None:
    """_corpus_annotation returns [IN-CORPUS:<citekey>] for a matching ArXiv id."""
    lib = _make_library_json(tmp_path)
    idx = research_mod._load_corpus_index(str(lib))
    result = research_mod._corpus_annotation(CANDIDATE_ARXIV_MATCH, idx)
    assert result == "[IN-CORPUS:devlin2018BERT]"


def test_corpus_annotation_new(tmp_path: Path) -> None:
    """_corpus_annotation returns [NEW] for a paper not in the corpus."""
    lib = _make_library_json(tmp_path)
    idx = research_mod._load_corpus_index(str(lib))
    result = research_mod._corpus_annotation(CANDIDATE_NEW, idx)
    assert result == "[NEW]"


def test_corpus_annotation_extra_citekey_match(tmp_path: Path) -> None:
    """_corpus_annotation matches a paper whose citekey is in data.extra."""
    lib = _make_library_json(tmp_path)
    idx = research_mod._load_corpus_index(str(lib))
    result = research_mod._corpus_annotation(CANDIDATE_EXTRA_CK_MATCH, idx)
    assert result == "[IN-CORPUS:devlinBERT2019]"


def test_corpus_annotation_empty_index() -> None:
    """_corpus_annotation returns [NEW] when corpus_index is empty."""
    result = research_mod._corpus_annotation(CANDIDATE_DOI_MATCH, {})
    assert result == "[NEW]"


# ---------------------------------------------------------------------------
# Test 5–6: _print_candidates annotation in output
# ---------------------------------------------------------------------------

def test_print_candidates_in_corpus_annotation(tmp_path: Path, capsys) -> None:
    """_print_candidates prints [IN-CORPUS:<citekey>] for matched candidates."""
    lib = _make_library_json(tmp_path)
    idx = research_mod._load_corpus_index(str(lib))
    research_mod._print_candidates([CANDIDATE_DOI_MATCH, CANDIDATE_NEW], corpus_index=idx)
    out = capsys.readouterr().out
    assert "[IN-CORPUS:vaswani2017Attention]" in out
    assert "[NEW]" in out


def test_print_candidates_all_new_when_no_index(capsys) -> None:
    """_print_candidates with no corpus_index prints [NEW] for all."""
    research_mod._print_candidates([CANDIDATE_DOI_MATCH, CANDIDATE_NEW])
    out = capsys.readouterr().out
    assert out.count("[NEW]") == 2
    assert "[IN-CORPUS" not in out


# ---------------------------------------------------------------------------
# Test 7: cmd_find annotated with --project
# ---------------------------------------------------------------------------

def test_cmd_find_annotated_with_project(tmp_path: Path, monkeypatch, capsys) -> None:
    """cmd_find uses --project to annotate candidates [IN-CORPUS] vs [NEW]."""
    lib = _make_library_json(tmp_path)

    # Build a minimal config with the project pointing at our library.json
    import tomllib
    cfg_path = tmp_path / "research_vault.toml"
    cfg_path.write_text(
        f'[projects.my-proj]\n'
        f'refs = "{lib}"\n'
        f'source_dir = "{tmp_path / "notes"}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_path))
    from research_vault.config import reset_config_cache
    reset_config_cache()

    papers = [CANDIDATE_DOI_MATCH, CANDIDATE_NEW]

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"data": papers})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)

    args = argparse.Namespace(
        research_cmd="find",
        query="attention",
        deep=False,
        limit=10,
        project="my-proj",
    )
    rc = research_mod.cmd_find(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "[IN-CORPUS:vaswani2017Attention]" in out
    assert "[NEW]" in out


# ---------------------------------------------------------------------------
# Test 8: cmd_cited_by annotated with --project
# ---------------------------------------------------------------------------

def test_cmd_cited_by_annotated_with_project(tmp_path: Path, monkeypatch, capsys) -> None:
    """cmd_cited_by uses --project to annotate candidates [IN-CORPUS] vs [NEW]."""
    lib = _make_library_json(tmp_path)

    cfg_path = tmp_path / "research_vault.toml"
    cfg_path.write_text(
        f'[projects.my-proj]\n'
        f'refs = "{lib}"\n'
        f'source_dir = "{tmp_path / "notes"}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_path))
    from research_vault.config import reset_config_cache
    reset_config_cache()

    papers = [CANDIDATE_ARXIV_MATCH, CANDIDATE_NEW]

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"data": [{"citingPaper": p} for p in papers]})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)

    args = argparse.Namespace(
        research_cmd="cited-by",
        paper_id="ARXIV:2005.14165",
        limit=20,
        project="my-proj",
    )
    rc = research_mod.cmd_cited_by(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "[IN-CORPUS:devlin2018BERT]" in out
    assert "[NEW]" in out


# ---------------------------------------------------------------------------
# Test 9: cmd_references annotated with --project
# ---------------------------------------------------------------------------

def test_cmd_references_annotated_with_project(tmp_path: Path, monkeypatch, capsys) -> None:
    """cmd_references uses --project to annotate candidates [IN-CORPUS] vs [NEW]."""
    lib = _make_library_json(tmp_path)

    cfg_path = tmp_path / "research_vault.toml"
    cfg_path.write_text(
        f'[projects.my-proj]\n'
        f'refs = "{lib}"\n'
        f'source_dir = "{tmp_path / "notes"}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_path))
    from research_vault.config import reset_config_cache
    reset_config_cache()

    papers = [CANDIDATE_DOI_MATCH, CANDIDATE_NEW]

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"references": papers})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)

    args = argparse.Namespace(
        research_cmd="references",
        paper_id="ARXIV:2005.14165",
        project="my-proj",
    )
    rc = research_mod.cmd_references(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "[IN-CORPUS:vaswani2017Attention]" in out
    assert "[NEW]" in out


# ---------------------------------------------------------------------------
# Test 10: Missing --project → graceful, no crash
# ---------------------------------------------------------------------------

def test_cmd_find_no_project_graceful(tmp_path: Path, monkeypatch, capsys) -> None:
    """cmd_find with --project=None must not crash; all candidates show [NEW]."""
    cfg_path = tmp_path / "research_vault.toml"
    cfg_path.write_text(
        f'instance_root = "{tmp_path}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_path))
    from research_vault.config import reset_config_cache
    reset_config_cache()

    papers = [CANDIDATE_DOI_MATCH]

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"data": papers})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)

    args = argparse.Namespace(
        research_cmd="find",
        query="attention",
        deep=False,
        limit=10,
        project=None,
    )
    rc = research_mod.cmd_find(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "[NEW]" in out or "candidate" in out  # must not crash; [NEW] shown or annotation absent


def test_cmd_cited_by_no_project_graceful(tmp_path: Path, monkeypatch, capsys) -> None:
    """cmd_cited_by with --project=None must not crash."""
    cfg_path = tmp_path / "research_vault.toml"
    cfg_path.write_text(f'instance_root = "{tmp_path}"\n', encoding="utf-8")
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_path))
    from research_vault.config import reset_config_cache
    reset_config_cache()

    papers = [CANDIDATE_NEW]

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"data": [{"citingPaper": p} for p in papers]})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)

    args = argparse.Namespace(
        research_cmd="cited-by",
        paper_id="ARXIV:2005.14165",
        limit=20,
        project=None,
    )
    rc = research_mod.cmd_cited_by(args)
    assert rc == 0


def test_cmd_references_no_project_graceful(tmp_path: Path, monkeypatch, capsys) -> None:
    """cmd_references with --project=None must not crash."""
    cfg_path = tmp_path / "research_vault.toml"
    cfg_path.write_text(f'instance_root = "{tmp_path}"\n', encoding="utf-8")
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_path))
    from research_vault.config import reset_config_cache
    reset_config_cache()

    papers = [CANDIDATE_NEW]

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"references": papers})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)

    args = argparse.Namespace(
        research_cmd="references",
        paper_id="ARXIV:2005.14165",
        project=None,
    )
    rc = research_mod.cmd_references(args)
    assert rc == 0


# ---------------------------------------------------------------------------
# Test 11: Empty / missing library.json → all [NEW]
# ---------------------------------------------------------------------------

def test_empty_library_all_new(tmp_path: Path, monkeypatch, capsys) -> None:
    """Empty library.json → all candidates annotated [NEW]."""
    lib = _make_library_json(tmp_path, items=[])

    cfg_path = tmp_path / "research_vault.toml"
    cfg_path.write_text(
        f'[projects.my-proj]\n'
        f'refs = "{lib}"\n'
        f'source_dir = "{tmp_path / "notes"}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_path))
    from research_vault.config import reset_config_cache
    reset_config_cache()

    papers = [CANDIDATE_DOI_MATCH]

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"data": papers})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)

    args = argparse.Namespace(
        research_cmd="find", query="attention", deep=False, limit=10, project="my-proj",
    )
    rc = research_mod.cmd_find(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "[IN-CORPUS" not in out
    assert "[NEW]" in out


# ---------------------------------------------------------------------------
# Test 12: Case-insensitive DOI matching
# ---------------------------------------------------------------------------

def test_doi_case_insensitive(tmp_path: Path) -> None:
    """DOI matching is case-insensitive (Zotero may uppercase prefixes)."""
    # Corpus has uppercase DOI
    item = {
        "key": "AAA",
        "data": {
            "citationKey": "Smith2020Something",
            "DOI": "10.1234/UPPER.CASE",
            "title": "A paper",
            "extra": "",
        },
    }
    lib = _make_library_json(tmp_path, items=[item])
    idx = research_mod._load_corpus_index(str(lib))

    # Candidate has lowercase DOI
    candidate = {
        "title": "A paper",
        "year": 2020,
        "authors": [{"name": "Alice Smith"}],
        "externalIds": {"DOI": "10.1234/upper.case"},
    }
    result = research_mod._corpus_annotation(candidate, idx)
    assert result == "[IN-CORPUS:Smith2020Something]"


# ---------------------------------------------------------------------------
# Test 13: ArXiv version suffix stripped
# ---------------------------------------------------------------------------

def test_arxiv_version_stripped(tmp_path: Path) -> None:
    """ArXiv id version suffix (v2, v3…) is stripped before matching."""
    # Corpus has base id (no version)
    item = {
        "key": "BBB",
        "data": {
            "citationKey": "Brown2020GPT3",
            "archiveID": "arXiv:2005.14165",
            "DOI": "",
            "title": "Language Models Are Few-Shot Learners",
            "extra": "",
        },
    }
    lib = _make_library_json(tmp_path, items=[item])
    idx = research_mod._load_corpus_index(str(lib))

    # Candidate has versioned ArXiv id
    candidate = {
        "title": "Language Models Are Few-Shot Learners",
        "year": 2020,
        "authors": [{"name": "Tom Brown"}],
        "externalIds": {"ArXiv": "2005.14165v3"},
    }
    result = research_mod._corpus_annotation(candidate, idx)
    assert result == "[IN-CORPUS:Brown2020GPT3]"


# ---------------------------------------------------------------------------
# Test 14: references --project help text describes corpus annotation
# ---------------------------------------------------------------------------

def test_references_project_help_mentions_corpus() -> None:
    """--project help for 'references' must not overpromise (now it IS implemented)."""
    p = research_mod.build_parser()
    sub_actions = p._subparsers._group_actions[0]._name_parser_map  # type: ignore[attr-defined]
    ref_parser = sub_actions.get("references")
    assert ref_parser is not None

    help_text = ref_parser.format_help()
    # After SR-LR-1 prereq, --project must be described (corpus dedup is live)
    assert "project" in help_text.lower() or "--project" in help_text, (
        f"references help must mention --project; got:\n{help_text}"
    )
    # Must not contain empty placeholder / "not yet implemented" wording
    assert "not yet" not in help_text.lower() and "todo" not in help_text.lower(), (
        f"references help contains overpromise wording: {help_text}"
    )


# ---------------------------------------------------------------------------
# Test 15: _load_corpus_index: item with citationKey field takes precedence over extra
# ---------------------------------------------------------------------------

def test_load_corpus_index_citationkey_field_priority(tmp_path: Path) -> None:
    """citationKey field takes priority over Citation Key in extra."""
    item = {
        "key": "CCC",
        "data": {
            "citationKey": "FieldKey",
            "DOI": "10.9999/test",
            "title": "Test",
            "extra": "Citation Key: ExtraKey",
        },
    }
    lib = _make_library_json(tmp_path, items=[item])
    idx = research_mod._load_corpus_index(str(lib))
    # Field takes priority
    assert idx.get("10.9999/test") == "FieldKey"


# ---------------------------------------------------------------------------
# Test 16–20: Fix #32 — notes-dir dedup (literature/<citekey>.md counts as in-corpus)
# ---------------------------------------------------------------------------

def _make_literature_note(
    literature_dir: Path,
    citekey: str,
    doi: str = "",
    arxiv_id: str = "",
) -> Path:
    """Write a minimal literature note with optional doi/arxiv_id frontmatter."""
    literature_dir.mkdir(parents=True, exist_ok=True)
    note_path = literature_dir / f"{citekey}.md"
    lines = ["---", f"type: literature", f"title: Test paper {citekey}", f"created: 2026-01-01"]
    if doi:
        lines.append(f"doi: {doi}")
    if arxiv_id:
        lines.append(f"arxiv_id: {arxiv_id}")
    lines += ["---", "", f"# {citekey}", ""]
    note_path.write_text("\n".join(lines), encoding="utf-8")
    return note_path


def test_load_notes_index_importable() -> None:
    """_load_notes_index is importable from research_vault.research (Fix #32)."""
    from research_vault.research import _load_notes_index
    assert callable(_load_notes_index)


def test_load_notes_index_doi(tmp_path: Path) -> None:
    """_load_notes_index builds doi → citekey lookup from literature/*.md frontmatter."""
    lit_dir = tmp_path / "literature"
    _make_literature_note(lit_dir, "vaswani2017Attention", doi="10.48550/ARXIV.1706.03762")

    idx = research_mod._load_notes_index(lit_dir)
    assert "10.48550/arxiv.1706.03762" in idx
    assert idx["10.48550/arxiv.1706.03762"] == "vaswani2017Attention"


def test_load_notes_index_arxiv(tmp_path: Path) -> None:
    """_load_notes_index builds arxiv → citekey lookup from literature/*.md frontmatter."""
    lit_dir = tmp_path / "literature"
    _make_literature_note(lit_dir, "devlin2018BERT", arxiv_id="1810.04805")

    idx = research_mod._load_notes_index(lit_dir)
    assert "1810.04805" in idx
    assert idx["1810.04805"] == "devlin2018BERT"


def test_load_notes_index_no_doi_field_not_indexed(tmp_path: Path) -> None:
    """A literature note without doi/arxiv_id fields is NOT indexed (can't match by id)."""
    lit_dir = tmp_path / "literature"
    _make_literature_note(lit_dir, "unlisted2020", doi="", arxiv_id="")

    idx = research_mod._load_notes_index(lit_dir)
    # No doi/arxiv field → the note doesn't contribute any lookup entry
    assert "unlisted2020" not in idx.values() or all(
        v != "unlisted2020" for v in idx.values()
    ), f"Expected no entry for unlisted2020, got: {idx}"


def test_load_notes_index_none_dir() -> None:
    """_load_notes_index returns empty dict for None (no project notes dir)."""
    idx = research_mod._load_notes_index(None)
    assert idx == {}


def test_load_notes_index_missing_dir(tmp_path: Path) -> None:
    """_load_notes_index returns empty dict when the literature dir doesn't exist."""
    idx = research_mod._load_notes_index(tmp_path / "nonexistent" / "literature")
    assert idx == {}


def test_corpus_annotation_filed_note_doi_in_corpus(tmp_path: Path) -> None:
    """_corpus_annotation returns [IN-CORPUS:<key>] for a paper with a filed literature note
    (doi frontmatter match) even when library.json is empty.

    Red-before-green (Fix #32): currently _corpus_annotation ignores the notes dir
    and returns [NEW] for this paper.
    """
    lit_dir = tmp_path / "literature"
    _make_literature_note(lit_dir, "vaswani2017Attention", doi="10.48550/ARXIV.1706.03762")

    notes_index = research_mod._load_notes_index(lit_dir)
    assert notes_index, "notes_index must be non-empty (test setup check)"

    result = research_mod._corpus_annotation(
        CANDIDATE_DOI_MATCH,  # has DOI 10.48550/ARXIV.1706.03762
        corpus_index={},       # empty — not in library.json
        notes_index=notes_index,
    )
    assert result == "[IN-CORPUS:vaswani2017Attention]", (
        f"A filed literature note with doi frontmatter must be [IN-CORPUS]; got {result!r}"
    )


def test_corpus_annotation_filed_note_arxiv_in_corpus(tmp_path: Path) -> None:
    """_corpus_annotation returns [IN-CORPUS:<key>] for a filed note with arxiv_id match."""
    lit_dir = tmp_path / "literature"
    _make_literature_note(lit_dir, "devlin2018BERT", arxiv_id="1810.04805")

    notes_index = research_mod._load_notes_index(lit_dir)
    result = research_mod._corpus_annotation(
        CANDIDATE_ARXIV_MATCH,  # has ArXiv 1810.04805
        corpus_index={},
        notes_index=notes_index,
    )
    assert result == "[IN-CORPUS:devlin2018BERT]", (
        f"A filed literature note with arxiv_id frontmatter must be [IN-CORPUS]; got {result!r}"
    )


def test_corpus_annotation_genuinely_new_stays_new(tmp_path: Path) -> None:
    """A paper with no library.json entry and no filed note stays [NEW]."""
    lit_dir = tmp_path / "literature"
    _make_literature_note(lit_dir, "vaswani2017Attention", doi="10.48550/ARXIV.1706.03762")
    notes_index = research_mod._load_notes_index(lit_dir)

    result = research_mod._corpus_annotation(
        CANDIDATE_NEW,  # has ArXiv 2401.99999 — genuinely new
        corpus_index={},
        notes_index=notes_index,
    )
    assert result == "[NEW]", f"Genuinely-new paper must stay [NEW]; got {result!r}"


def test_corpus_annotation_library_json_wins_when_note_also_present(tmp_path: Path) -> None:
    """When a paper is in BOTH library.json AND has a filed note, library.json citekey wins."""
    lit_dir = tmp_path / "literature"
    # Note uses one citekey; library.json uses another (simulates the common case)
    _make_literature_note(lit_dir, "vaswani2017Attention", doi="10.48550/ARXIV.1706.03762")
    notes_index = research_mod._load_notes_index(lit_dir)

    corpus_index = {"10.48550/arxiv.1706.03762": "vaswani2017Attention"}
    result = research_mod._corpus_annotation(
        CANDIDATE_DOI_MATCH,
        corpus_index=corpus_index,
        notes_index=notes_index,
    )
    # Either citekey is acceptable; the key invariant is [IN-CORPUS] (not [NEW])
    assert result.startswith("[IN-CORPUS:"), (
        f"A paper in both sources must be [IN-CORPUS]; got {result!r}"
    )


def test_cmd_find_filed_note_shows_in_corpus(tmp_path: Path, monkeypatch, capsys) -> None:
    """cmd_find with --project annotates [IN-CORPUS] from a filed literature note.

    Red-before-green (Fix #32): before the fix, this shows [NEW] because cmd_find
    only checks library.json, not the literature/ OKF dir.
    """
    # library.json is EMPTY (Zotero not synced)
    lib = _make_library_json(tmp_path, items=[])

    # Project notes dir has a filed literature note for the candidate paper
    project_notes_dir = tmp_path / "notes" / "demo-proj"
    lit_dir = project_notes_dir / "literature"
    _make_literature_note(lit_dir, "vaswani2017Attention", doi="10.48550/ARXIV.1706.03762")

    cfg_path = tmp_path / "research_vault.toml"
    cfg_path.write_text(
        f'[projects.demo-proj]\n'
        f'refs = "{lib}"\n'
        f'source_dir = "{project_notes_dir}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_path))
    from research_vault.config import reset_config_cache
    reset_config_cache()

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"data": [CANDIDATE_DOI_MATCH, CANDIDATE_NEW]})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)

    args = argparse.Namespace(
        research_cmd="find",
        query="attention",
        deep=False,
        limit=10,
        project="demo-proj",
    )
    rc = research_mod.cmd_find(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "[IN-CORPUS:vaswani2017Attention]" in out, (
        f"Filed literature note must show [IN-CORPUS]; got output:\n{out}"
    )
    assert "[NEW]" in out  # CANDIDATE_NEW has no matching note


# ---------------------------------------------------------------------------
# Tests 21+: rv-refs-corpus-fix — csb bug reproduction
#
# Root-cause investigation (2026-07-07) disconfirmed the original hypothesis
# ("references-payload id-shape differs from cited-by's").  Live-checked: both
# `asta papers citations` (cited-by) and `asta papers get --fields
# references.*` (references) hand back items with an identical
# `externalIds: {ArXiv, DOI, ...}` shape, and cmd_cited_by/cmd_references
# already call the exact same _corpus_annotation/_print_candidates path.
#
# The REAL bug is upstream of both: real cultural-social-sim literature notes
# never populate `doi:`/`arxiv_id:` frontmatter — only a `url:` field (e.g.
# `https://arxiv.org/abs/2209.06899`) or, for some papers (e.g. a conference
# proceedings page with no id at all), nothing extractable whatsoever.  So
# `_load_notes_index` returns an EMPTY index for every real note, and ALL
# THREE rv verbs (find/cited-by/references) under-annotate identically.  The
# fixtures below mirror the two real cases (Argyle 2022 — url has an
# extractable arXiv id; Aher 2022 — no id in url, but title+first-author
# match, with a canonical S2 year (2022) that differs from the note's own
# venue year (2023, ICML publication year vs. arXiv preprint year)).
# ---------------------------------------------------------------------------

ARGYLE_NOTE_FRONTMATTER = {
    "title": "Out of One, Many: Using Language Models to Simulate Human Samples",
    "authors": "Argyle, Lisa P.; Busby, E. C.; Fulda, N.; Gubler, J. R.; Rytting, C.; Wingate, D.",
    "year": "2023",
    "url": "https://arxiv.org/abs/2209.06899",
}

AHER_NOTE_FRONTMATTER = {
    "title": "Using Large Language Models to Simulate Multiple Humans and Replicate Human Subject Studies",
    "authors": "Aher, Gati; Arriaga, Rosa I.; Kalai, Adam Tauman",
    "year": "2023",
    "url": "https://proceedings.mlr.press/v202/aher23a.html",
}

# Real S2 reference-item shapes (from a live `asta papers get --fields
# references.*` call against arXiv:2511.04500), confirming the externalIds
# shape is identical to cited-by's and find's.
ARGYLE_S2_CANDIDATE = {
    "title": "Out of One, Many: Using Language Models to Simulate Human Samples",
    "year": 2022,
    "authors": [{"name": "Lisa P. Argyle"}],
    "externalIds": {"ArXiv": "2209.06899", "DOI": "10.1017/pan.2023.2", "CorpusId": 252280474},
    "citationCount": 1161,
}

AHER_S2_CANDIDATE = {
    "title": "Using Large Language Models to Simulate Multiple Humans and Replicate Human Subject Studies",
    "year": 2022,  # canonical S2 year differs from the note's own venue year (2023)
    "authors": [{"name": "Gati Aher"}],
    "externalIds": {"ArXiv": "2208.10264", "CorpusId": 251719353},
    "citationCount": 741,
}


def _write_realistic_note(literature_dir: Path, citekey: str, frontmatter: dict) -> Path:
    """Write a literature note with the REAL frontmatter shape (url:, no doi:/arxiv_id:)."""
    literature_dir.mkdir(parents=True, exist_ok=True)
    note_path = literature_dir / f"{citekey}.md"
    lines = ["---", "type: literature"]
    for k, v in frontmatter.items():
        lines.append(f'{k}: {v}')
    lines.append(f"citekey: {citekey}")
    lines += ["---", "", f"# {citekey}", ""]
    note_path.write_text("\n".join(lines), encoding="utf-8")
    return note_path


def test_load_notes_index_extracts_arxiv_from_url_field(tmp_path: Path) -> None:
    """RED before fix: a note with ONLY a url: field (no arxiv_id:) is not indexed.

    GREEN after fix: `_load_notes_index` must also mine the url: field for an
    arXiv id — this is the real-world shape (Argyle's csb note).
    """
    lit_dir = tmp_path / "literature"
    _write_realistic_note(lit_dir, "argyleOutOneMany2022", ARGYLE_NOTE_FRONTMATTER)

    idx = research_mod._load_notes_index(lit_dir)
    assert "2209.06899" in idx, (
        f"Expected the url-derived arXiv id to be indexed; got {idx}"
    )
    assert idx["2209.06899"] == "argyleOutOneMany2022"


def test_corpus_annotation_argyle_url_only_note_is_in_corpus(tmp_path: Path) -> None:
    """The real Argyle S2 candidate must annotate [IN-CORPUS] from a url-only note."""
    lit_dir = tmp_path / "literature"
    _write_realistic_note(lit_dir, "argyleOutOneMany2022", ARGYLE_NOTE_FRONTMATTER)
    notes_index = research_mod._load_notes_index(lit_dir)

    result = research_mod._corpus_annotation(
        ARGYLE_S2_CANDIDATE, corpus_index={}, notes_index=notes_index,
    )
    assert result == "[IN-CORPUS:argyleOutOneMany2022]", (
        f"Argyle must be [IN-CORPUS] via url-derived arXiv id; got {result!r}"
    )


def test_corpus_annotation_aher_title_author_fallback_is_in_corpus(tmp_path: Path) -> None:
    """Aher has NO id anywhere in its note (mlr.press url) — only title+author fallback
    can recognize it, and the canonical S2 year (2022) differs from the note's own
    venue year (2023) — the fallback must be year-agnostic.
    """
    lit_dir = tmp_path / "literature"
    _write_realistic_note(lit_dir, "aherLargeLanguageModels2022", AHER_NOTE_FRONTMATTER)

    # Confirm the id-based index genuinely has nothing for Aher (no id extractable)
    notes_index = research_mod._load_notes_index(lit_dir)
    assert "aherLargeLanguageModels2022" not in notes_index.values(), (
        "Aher's note has no id anywhere — the id-index must NOT contain it "
        "(this is the structural proof that only the title fallback can catch it)"
    )

    notes_title_index = research_mod._load_notes_title_index(lit_dir)
    result = research_mod._corpus_annotation(
        AHER_S2_CANDIDATE,
        corpus_index={},
        notes_index=notes_index,
        notes_title_index=notes_title_index,
    )
    assert result == "[IN-CORPUS:aherLargeLanguageModels2022]", (
        f"Aher must be [IN-CORPUS] via title+author fallback; got {result!r}"
    )


def test_cmd_references_parity_with_cited_by_on_notes_only_corpus(tmp_path: Path, monkeypatch, capsys) -> None:
    """RED before fix: cmd_references under-annotates relative to cmd_cited_by even
    though both call the identical _corpus_annotation/_print_candidates path — because
    the shared upstream notes_index/notes_title_index never mined url:/title fallback.
    GREEN after fix: cmd_references and cmd_cited_by annotate the SAME S2 candidate
    (Argyle) identically as [IN-CORPUS], proving parity restored.
    """
    project_notes_dir = tmp_path / "notes" / "demo-proj"
    lit_dir = project_notes_dir / "literature"
    _write_realistic_note(lit_dir, "argyleOutOneMany2022", ARGYLE_NOTE_FRONTMATTER)

    lib = _make_library_json(tmp_path, items=[])  # empty — Zotero not synced
    cfg_path = tmp_path / "research_vault.toml"
    cfg_path.write_text(
        f'[projects.demo-proj]\n'
        f'refs = "{lib}"\n'
        f'source_dir = "{project_notes_dir}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_path))
    from research_vault.config import reset_config_cache
    reset_config_cache()

    monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)

    # cited-by path
    def fake_run_cited_by(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"data": [{"citingPaper": ARGYLE_S2_CANDIDATE}]})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run_cited_by)
    rc = research_mod.cmd_cited_by(argparse.Namespace(
        research_cmd="cited-by", paper_id="ARXIV:2511.04500", limit=20, project="demo-proj",
    ))
    assert rc == 0
    cited_by_out = capsys.readouterr().out
    assert "[IN-CORPUS:argyleOutOneMany2022]" in cited_by_out

    # references path — must match
    def fake_run_references(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"references": [ARGYLE_S2_CANDIDATE]})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run_references)
    rc = research_mod.cmd_references(argparse.Namespace(
        research_cmd="references", paper_id="ARXIV:2511.04500", project="demo-proj",
    ))
    assert rc == 0
    references_out = capsys.readouterr().out
    assert "[IN-CORPUS:argyleOutOneMany2022]" in references_out, (
        f"references must annotate Argyle [IN-CORPUS] exactly like cited-by; got:\n{references_out}"
    )
