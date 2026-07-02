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
