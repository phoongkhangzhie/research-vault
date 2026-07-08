"""test_research_corpus_dedup.py — TDD tests for corpus-dedup annotation (SR-LR-1 prereq).

Design invariants tested:
  1.  _corpus_annotation returns [IN-CORPUS:<citekey>] for a matching DOI (notes_index)
  2.  _corpus_annotation returns [IN-CORPUS:<citekey>] for a matching ArXiv id (notes_index)
  3.  _corpus_annotation returns [NEW] for a paper not in the corpus
  4.  _print_candidates prints [IN-CORPUS:…] annotation when notes_index provided
  5.  _print_candidates prints [NEW] annotation for unmatched candidates
  6.  cmd_find — annotated output with --project wired to filed literature notes
  7.  cmd_cited_by — annotated output with --project wired to filed literature notes
  8.  cmd_references — annotated output with --project wired to filed literature notes
  9.  Missing --project → no crash, graceful output (no annotation or [NEW] for all)
 10.  No filed notes → graceful (treats all as [NEW])
 11.  Case-insensitive DOI matching
 12.  ArXiv version suffix stripped (2005.14165v2 → matches 2005.14165)
 13.  references --project help text describes corpus annotation (no overpromise)
 14.  rv-023: _load_notes_index / _load_notes_title_index emit the note's OWN
      `citekey:` frontmatter field (Khang's Better BibTeX scheme), not the
      filename stem, when present — falling back to the stem only when absent.
 15.  rv-023: the dead library.json / Zotero corpus-index tier
      (_load_corpus_index / _refs_path_for_project) has been REMOVED — this is
      a structural regression guard, not a feature test.

All tests hermetic: asta and file I/O are mocked via monkeypatch + tmp_path.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault import research as research_mod


# ---------------------------------------------------------------------------
# Fixtures / shared data
# ---------------------------------------------------------------------------

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


def _make_literature_note(
    literature_dir: Path,
    citekey: str,
    doi: str = "",
    arxiv_id: str = "",
    frontmatter_citekey: str | None = None,
) -> Path:
    """Write a minimal literature note with optional doi/arxiv_id frontmatter.

    ``citekey`` names the FILE (the filename slug). ``frontmatter_citekey``,
    when given, is written as the note's own `citekey:` field — the
    rv-023 Better BibTeX scheme, which may differ from the filename.
    """
    literature_dir.mkdir(parents=True, exist_ok=True)
    note_path = literature_dir / f"{citekey}.md"
    lines = ["---", "type: literature", f"title: Test paper {citekey}", "created: 2026-01-01"]
    if doi:
        lines.append(f"doi: {doi}")
    if arxiv_id:
        lines.append(f"arxiv_id: {arxiv_id}")
    if frontmatter_citekey:
        lines.append(f"citekey: {frontmatter_citekey}")
    lines += ["---", "", f"# {citekey}", ""]
    note_path.write_text("\n".join(lines), encoding="utf-8")
    return note_path


# ---------------------------------------------------------------------------
# rv-023: dead library.json / Zotero corpus-index tier removed (regression guard)
# ---------------------------------------------------------------------------

def test_load_corpus_index_removed() -> None:
    """_load_corpus_index must be GONE — the dead Zotero library.json tier
    (rv-023: nothing wired a `refs =` path into real project config, and the
    parser expected the raw Zotero-API shape, never the flat CSL-JSON a real
    library.json actually contains)."""
    assert not hasattr(research_mod, "_load_corpus_index")


def test_refs_path_for_project_removed() -> None:
    """_refs_path_for_project must be GONE (rv-023 dead-tier removal)."""
    assert not hasattr(research_mod, "_refs_path_for_project")


def test_corpus_annotation_no_longer_accepts_corpus_index_positional() -> None:
    """_corpus_annotation's signature dropped the corpus_index positional param."""
    with pytest.raises(TypeError):
        research_mod._corpus_annotation(CANDIDATE_DOI_MATCH, {})


def test_print_candidates_no_longer_accepts_corpus_index() -> None:
    """_print_candidates's signature dropped the corpus_index param."""
    with pytest.raises(TypeError):
        research_mod._print_candidates([CANDIDATE_NEW], {})


# ---------------------------------------------------------------------------
# _corpus_annotation via notes_index (the sole id-based tier after rv-023)
# ---------------------------------------------------------------------------

def test_corpus_annotation_doi_match(tmp_path: Path) -> None:
    """_corpus_annotation returns [IN-CORPUS:<citekey>] for a matching DOI."""
    lit_dir = tmp_path / "literature"
    _make_literature_note(lit_dir, "vaswani-2017-attention", doi="10.48550/ARXIV.1706.03762")
    notes_index = research_mod._load_notes_index(lit_dir)
    result = research_mod._corpus_annotation(CANDIDATE_DOI_MATCH, notes_index=notes_index)
    assert result == "[IN-CORPUS:vaswani-2017-attention]"


def test_corpus_annotation_arxiv_match(tmp_path: Path) -> None:
    """_corpus_annotation returns [IN-CORPUS:<citekey>] for a matching ArXiv id."""
    lit_dir = tmp_path / "literature"
    _make_literature_note(lit_dir, "devlin-2018-bert", arxiv_id="1810.04805")
    notes_index = research_mod._load_notes_index(lit_dir)
    result = research_mod._corpus_annotation(CANDIDATE_ARXIV_MATCH, notes_index=notes_index)
    assert result == "[IN-CORPUS:devlin-2018-bert]"


def test_corpus_annotation_new() -> None:
    """_corpus_annotation returns [NEW] for a paper not in the corpus."""
    result = research_mod._corpus_annotation(CANDIDATE_NEW, notes_index={})
    assert result == "[NEW]"


def test_corpus_annotation_empty_index() -> None:
    """_corpus_annotation returns [NEW] when no indexes are provided."""
    result = research_mod._corpus_annotation(CANDIDATE_DOI_MATCH)
    assert result == "[NEW]"


# ---------------------------------------------------------------------------
# _print_candidates annotation in output
# ---------------------------------------------------------------------------

def test_print_candidates_in_corpus_annotation(tmp_path: Path, capsys) -> None:
    """_print_candidates prints [IN-CORPUS:<citekey>] for matched candidates."""
    lit_dir = tmp_path / "literature"
    _make_literature_note(lit_dir, "vaswani-2017-attention", doi="10.48550/ARXIV.1706.03762")
    notes_index = research_mod._load_notes_index(lit_dir)
    research_mod._print_candidates([CANDIDATE_DOI_MATCH, CANDIDATE_NEW], notes_index=notes_index)
    out = capsys.readouterr().out
    assert "[IN-CORPUS:vaswani-2017-attention]" in out
    assert "[NEW]" in out


def test_print_candidates_all_new_when_no_index(capsys) -> None:
    """_print_candidates with no indexes prints [NEW] for all."""
    research_mod._print_candidates([CANDIDATE_DOI_MATCH, CANDIDATE_NEW])
    out = capsys.readouterr().out
    assert out.count("[NEW]") == 2
    assert "[IN-CORPUS" not in out


# ---------------------------------------------------------------------------
# cmd_find / cmd_cited_by / cmd_references annotated with --project
# ---------------------------------------------------------------------------

def test_cmd_find_annotated_with_project(tmp_path: Path, monkeypatch, capsys) -> None:
    """cmd_find uses --project to annotate candidates [IN-CORPUS] vs [NEW]
    from filed literature notes."""
    project_notes_dir = tmp_path / "notes" / "my-proj"
    lit_dir = project_notes_dir / "literature"
    _make_literature_note(lit_dir, "vaswani-2017-attention", doi="10.48550/ARXIV.1706.03762")

    cfg_path = tmp_path / "research_vault.toml"
    cfg_path.write_text(
        '[projects.my-proj]\n'
        f'source_dir = "{project_notes_dir}"\n',
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
    assert "[IN-CORPUS:vaswani-2017-attention]" in out
    assert "[NEW]" in out


def test_cmd_cited_by_annotated_with_project(tmp_path: Path, monkeypatch, capsys) -> None:
    """cmd_cited_by uses --project to annotate candidates [IN-CORPUS] vs [NEW]."""
    project_notes_dir = tmp_path / "notes" / "my-proj"
    lit_dir = project_notes_dir / "literature"
    _make_literature_note(lit_dir, "devlin-2018-bert", arxiv_id="1810.04805")

    cfg_path = tmp_path / "research_vault.toml"
    cfg_path.write_text(
        '[projects.my-proj]\n'
        f'source_dir = "{project_notes_dir}"\n',
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
    assert "[IN-CORPUS:devlin-2018-bert]" in out
    assert "[NEW]" in out


def test_cmd_references_annotated_with_project(tmp_path: Path, monkeypatch, capsys) -> None:
    """cmd_references uses --project to annotate candidates [IN-CORPUS] vs [NEW]."""
    project_notes_dir = tmp_path / "notes" / "my-proj"
    lit_dir = project_notes_dir / "literature"
    _make_literature_note(lit_dir, "vaswani-2017-attention", doi="10.48550/ARXIV.1706.03762")

    cfg_path = tmp_path / "research_vault.toml"
    cfg_path.write_text(
        '[projects.my-proj]\n'
        f'source_dir = "{project_notes_dir}"\n',
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
    assert "[IN-CORPUS:vaswani-2017-attention]" in out
    assert "[NEW]" in out


# ---------------------------------------------------------------------------
# Missing --project → graceful, no crash
# ---------------------------------------------------------------------------

def test_cmd_find_no_project_graceful(tmp_path: Path, monkeypatch, capsys) -> None:
    """cmd_find with --project=None must not crash; all candidates show [NEW]."""
    cfg_path = tmp_path / "research_vault.toml"
    cfg_path.write_text(f'instance_root = "{tmp_path}"\n', encoding="utf-8")
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
# No filed notes → all [NEW]
# ---------------------------------------------------------------------------

def test_no_filed_notes_all_new(tmp_path: Path, monkeypatch, capsys) -> None:
    """No filed literature notes → all candidates annotated [NEW]."""
    project_notes_dir = tmp_path / "notes" / "my-proj"
    (project_notes_dir / "literature").mkdir(parents=True, exist_ok=True)

    cfg_path = tmp_path / "research_vault.toml"
    cfg_path.write_text(
        '[projects.my-proj]\n'
        f'source_dir = "{project_notes_dir}"\n',
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
# Case-insensitive DOI matching / ArXiv version-suffix stripping
# ---------------------------------------------------------------------------

def test_doi_case_insensitive(tmp_path: Path) -> None:
    """DOI matching is case-insensitive (a note may record an uppercase DOI)."""
    lit_dir = tmp_path / "literature"
    _make_literature_note(lit_dir, "smith2020Something", doi="10.1234/UPPER.CASE")
    notes_index = research_mod._load_notes_index(lit_dir)

    # Candidate has lowercase DOI
    candidate = {
        "title": "A paper",
        "year": 2020,
        "authors": [{"name": "Alice Smith"}],
        "externalIds": {"DOI": "10.1234/upper.case"},
    }
    result = research_mod._corpus_annotation(candidate, notes_index=notes_index)
    assert result == "[IN-CORPUS:smith2020Something]"


def test_arxiv_version_stripped(tmp_path: Path) -> None:
    """ArXiv id version suffix (v2, v3…) is stripped before matching."""
    lit_dir = tmp_path / "literature"
    _make_literature_note(lit_dir, "brown2020GPT3", arxiv_id="2005.14165")
    notes_index = research_mod._load_notes_index(lit_dir)

    # Candidate has versioned ArXiv id
    candidate = {
        "title": "Language Models Are Few-Shot Learners",
        "year": 2020,
        "authors": [{"name": "Tom Brown"}],
        "externalIds": {"ArXiv": "2005.14165v3"},
    }
    result = research_mod._corpus_annotation(candidate, notes_index=notes_index)
    assert result == "[IN-CORPUS:brown2020GPT3]"


# ---------------------------------------------------------------------------
# references --project help text describes corpus annotation
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
    # rv-023: must not reference the removed library.json tier
    assert "library.json" not in help_text.lower(), (
        f"references help must not reference the removed library.json corpus tier: {help_text}"
    )


# ---------------------------------------------------------------------------
# Fix #32 — notes-dir dedup (literature/<citekey>.md counts as in-corpus)
# ---------------------------------------------------------------------------

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
    (doi frontmatter match)."""
    lit_dir = tmp_path / "literature"
    _make_literature_note(lit_dir, "vaswani2017Attention", doi="10.48550/ARXIV.1706.03762")

    notes_index = research_mod._load_notes_index(lit_dir)
    assert notes_index, "notes_index must be non-empty (test setup check)"

    result = research_mod._corpus_annotation(
        CANDIDATE_DOI_MATCH,  # has DOI 10.48550/ARXIV.1706.03762
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
        notes_index=notes_index,
    )
    assert result == "[IN-CORPUS:devlin2018BERT]", (
        f"A filed literature note with arxiv_id frontmatter must be [IN-CORPUS]; got {result!r}"
    )


def test_corpus_annotation_genuinely_new_stays_new(tmp_path: Path) -> None:
    """A paper with no filed note stays [NEW]."""
    lit_dir = tmp_path / "literature"
    _make_literature_note(lit_dir, "vaswani2017Attention", doi="10.48550/ARXIV.1706.03762")
    notes_index = research_mod._load_notes_index(lit_dir)

    result = research_mod._corpus_annotation(
        CANDIDATE_NEW,  # has ArXiv 2401.99999 — genuinely new
        notes_index=notes_index,
    )
    assert result == "[NEW]", f"Genuinely-new paper must stay [NEW]; got {result!r}"


def test_cmd_find_filed_note_shows_in_corpus(tmp_path: Path, monkeypatch, capsys) -> None:
    """cmd_find with --project annotates [IN-CORPUS] from a filed literature note."""
    project_notes_dir = tmp_path / "notes" / "demo-proj"
    lit_dir = project_notes_dir / "literature"
    _make_literature_note(lit_dir, "vaswani2017Attention", doi="10.48550/ARXIV.1706.03762")

    cfg_path = tmp_path / "research_vault.toml"
    cfg_path.write_text(
        '[projects.demo-proj]\n'
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
# rv-refs-corpus-fix — real-project bug reproduction (url-derived id / title
# fallback for notes with no extractable id anywhere)
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
# shape is identical across find/cited-by/references.
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


def _write_realistic_note(
    literature_dir: Path, citekey: str, frontmatter: dict, *, bbt_citekey: str | None = None,
) -> Path:
    """Write a literature note with the REAL frontmatter shape (url:, no doi:/arxiv_id:).

    ``citekey`` names the FILE. ``bbt_citekey``, when given, is ALSO written as
    the note's own `citekey:` frontmatter field (may differ from the filename —
    rv-023's real-world case).  When omitted, the note carries no `citekey:`
    field at all (the pre-rv-023 fixture shape).
    """
    literature_dir.mkdir(parents=True, exist_ok=True)
    note_path = literature_dir / f"{citekey}.md"
    lines = ["---", "type: literature"]
    for k, v in frontmatter.items():
        lines.append(f'{k}: {v}')
    if bbt_citekey:
        lines.append(f"citekey: {bbt_citekey}")
    lines += ["---", "", f"# {citekey}", ""]
    note_path.write_text("\n".join(lines), encoding="utf-8")
    return note_path


def test_load_notes_index_extracts_arxiv_from_url_field(tmp_path: Path) -> None:
    """RED before fix: a note with ONLY a url: field (no arxiv_id:) is not indexed.

    GREEN after fix: `_load_notes_index` must also mine the url: field for an
    arXiv id — this is the real-world shape (Argyle's filed note).
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

    result = research_mod._corpus_annotation(ARGYLE_S2_CANDIDATE, notes_index=notes_index)
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
        notes_index=notes_index,
        notes_title_index=notes_title_index,
    )
    assert result == "[IN-CORPUS:aherLargeLanguageModels2022]", (
        f"Aher must be [IN-CORPUS] via title+author fallback; got {result!r}"
    )


def test_cmd_references_parity_with_cited_by_on_notes_only_corpus(tmp_path: Path, monkeypatch, capsys) -> None:
    """cmd_references and cmd_cited_by annotate the SAME S2 candidate (Argyle)
    identically as [IN-CORPUS] — both share the identical
    _corpus_annotation/_print_candidates path over the notes-index tier.
    """
    project_notes_dir = tmp_path / "notes" / "demo-proj"
    lit_dir = project_notes_dir / "literature"
    _write_realistic_note(lit_dir, "argyleOutOneMany2022", ARGYLE_NOTE_FRONTMATTER)

    cfg_path = tmp_path / "research_vault.toml"
    cfg_path.write_text(
        '[projects.demo-proj]\n'
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


# ---------------------------------------------------------------------------
# reviewer-verified title-fallback over-match tightening (2026-07-07)
#
# Independent review of PR #172 reproduced three genuinely-distinct-paper
# false [IN-CORPUS] matches under the original loose heuristic
# (title[:30]-prefix-equal OR either-contains-the-other, no length gate):
#   A. title-superset  — a shorter title is a strict prefix of a longer,
#                         DIFFERENT paper's title by the same author.
#   B. series prefix   — "Part I" is a strict prefix of "Part II ...<subtitle>"
#                         (a real sequel paper — genuinely a different entry).
#   C. surname collision — two DIFFERENT people sharing a surname, titles
#                         sharing a long generic opening phrase then diverging
#                         (the exact vector the removed 30-char-prefix arm let
#                         through).
# A false [IN-CORPUS] is the SILENT failure mode for SR-LR-1 saturation
# (a non-saturated round looks saturated, hiding a real frontier paper) —
# worse than the false-NEW this fix was closing.  The tightening: exact
# normalized-title equality, OR containment gated by a length ratio
# min(len)/max(len) >= 0.9.  All three repros here fail that ratio gate
# (the shared-prefix case has NO containment at all once the 30-char-prefix
# arm is removed); the legitimate Aher catch (identical titles, ratio 1.0)
# still passes.
# ---------------------------------------------------------------------------

def test_title_fallback_match_rejects_title_superset() -> None:
    """Case A: a shorter title is a strict prefix of a longer, different title."""
    note_title = research_mod._norm_title_str("Language Models Are Few-Shot Learners")
    s2_title = research_mod._norm_title_str(
        "Language Models Are Few-Shot Learners For Clinical Diagnosis And "
        "Treatment Planning In Radiology"
    )
    assert note_title in s2_title, "test setup check: old heuristic's containment must hold"
    assert research_mod._title_fallback_match(s2_title, note_title) is False


def test_title_fallback_match_rejects_series_prefix() -> None:
    """Case B: 'Part I' is a strict prefix of a genuinely different 'Part II' paper."""
    note_title = research_mod._norm_title_str(
        "Emergent Reasoning Abilities in Large Language Models Part I"
    )
    s2_title = research_mod._norm_title_str(
        "Emergent Reasoning Abilities in Large Language Models Part II: "
        "A Follow-Up Study With Expanded Benchmarks"
    )
    assert note_title in s2_title, "test setup check: old heuristic's containment must hold"
    assert research_mod._title_fallback_match(s2_title, note_title) is False


def test_title_fallback_match_rejects_shared_prefix_surname_collision() -> None:
    """Case C: two different people sharing a surname; titles share a long
    generic opening phrase (>= 30 normalized chars) then diverge completely —
    the exact vector the removed 30-char-prefix arm let through."""
    note_title = research_mod._norm_title_str(
        "Self-Instruct: Aligning Language Models with Self-Generated Instructions"
    )
    s2_title = research_mod._norm_title_str(
        "Self-Instruct: Aligning Language Models for Robotic Manipulation "
        "Policies in Simulated Warehouse Environments"
    )
    assert note_title[:30] == s2_title[:30], "test setup check: shared 30-char prefix must hold"
    assert note_title not in s2_title and s2_title not in note_title, (
        "test setup check: no containment relationship (isolates the prefix-arm vector)"
    )
    assert research_mod._title_fallback_match(s2_title, note_title) is False


def test_title_fallback_match_accepts_exact_title_ratio_one() -> None:
    """The legitimate Aher catch (identical normalized titles, ratio 1.0) must survive."""
    title = research_mod._norm_title_str(
        "Using Large Language Models to Simulate Multiple Humans and "
        "Replicate Human Subject Studies"
    )
    assert research_mod._title_fallback_match(title, title) is True


def test_corpus_annotation_title_superset_stays_new(tmp_path: Path) -> None:
    """End-to-end (Case A): a filed note must NOT falsely annotate a longer,
    genuinely different paper by the same author as [IN-CORPUS]."""
    lit_dir = tmp_path / "literature"
    _write_realistic_note(lit_dir, "brownFewShotLearners2020", {
        "title": "Language Models Are Few-Shot Learners",
        "authors": "Brown, Tom B.",
        "year": "2020",
        "url": "https://proceedings.neurips.cc/paper/2020/brown",
    })
    notes_index = research_mod._load_notes_index(lit_dir)
    notes_title_index = research_mod._load_notes_title_index(lit_dir)

    distinct_candidate = {
        "title": "Language Models Are Few-Shot Learners For Clinical Diagnosis "
                 "And Treatment Planning In Radiology",
        "year": 2022,
        "authors": [{"name": "Tom B. Brown"}],
        "externalIds": {},
    }
    result = research_mod._corpus_annotation(
        distinct_candidate, notes_index=notes_index,
        notes_title_index=notes_title_index,
    )
    assert result == "[NEW]", f"Genuinely different superset-titled paper must be [NEW]; got {result!r}"


def test_corpus_annotation_series_sequel_stays_new(tmp_path: Path) -> None:
    """End-to-end (Case B): a 'Part I' note must NOT falsely annotate the
    genuinely different 'Part II' sequel paper as [IN-CORPUS]."""
    lit_dir = tmp_path / "literature"
    _write_realistic_note(lit_dir, "smithEmergentReasoningPart12023", {
        "title": "Emergent Reasoning Abilities in Large Language Models Part I",
        "authors": "Smith, Jane",
        "year": "2023",
        "url": "https://proceedings.example.org/smith-part-i",
    })
    notes_index = research_mod._load_notes_index(lit_dir)
    notes_title_index = research_mod._load_notes_title_index(lit_dir)

    part_two_candidate = {
        "title": "Emergent Reasoning Abilities in Large Language Models Part II: "
                 "A Follow-Up Study With Expanded Benchmarks",
        "year": 2024,
        "authors": [{"name": "Jane Smith"}],
        "externalIds": {},
    }
    result = research_mod._corpus_annotation(
        part_two_candidate, notes_index=notes_index,
        notes_title_index=notes_title_index,
    )
    assert result == "[NEW]", f"Genuinely different Part II sequel must be [NEW]; got {result!r}"


def test_corpus_annotation_surname_collision_shared_prefix_stays_new(tmp_path: Path) -> None:
    """End-to-end (Case C): two different people sharing a surname, titles
    sharing a long generic opening phrase, must NOT false-match."""
    lit_dir = tmp_path / "literature"
    _write_realistic_note(lit_dir, "wangSelfInstruct2022", {
        "title": "Self-Instruct: Aligning Language Models with Self-Generated Instructions",
        "authors": "Wang, Li",
        "year": "2022",
        "url": "https://proceedings.example.org/wang-self-instruct",  # no extractable id
    })
    notes_index = research_mod._load_notes_index(lit_dir)
    notes_title_index = research_mod._load_notes_title_index(lit_dir)

    different_wang_candidate = {
        "title": "Self-Instruct: Aligning Language Models for Robotic Manipulation "
                 "Policies in Simulated Warehouse Environments",
        "year": 2023,
        "authors": [{"name": "Chen Wang"}],
        "externalIds": {},
    }
    result = research_mod._corpus_annotation(
        different_wang_candidate, notes_index=notes_index,
        notes_title_index=notes_title_index,
    )
    assert result == "[NEW]", f"A different Wang's paper must stay [NEW]; got {result!r}"


def test_load_notes_title_index_scoped_to_notes_without_id(tmp_path: Path) -> None:
    """_load_notes_title_index must SKIP notes that already carry an
    extractable id (declared or url-derived) — id-carrying notes are fully
    served by _load_notes_index (tier 2); including them here only widens
    the over-match surface for no additional detection power."""
    lit_dir = tmp_path / "literature"
    # Argyle's note HAS an extractable id (url-derived arXiv) — must be excluded
    _write_realistic_note(lit_dir, "argyleOutOneMany2022", ARGYLE_NOTE_FRONTMATTER)
    # Aher's note has NO extractable id — must be included
    _write_realistic_note(lit_dir, "aherLargeLanguageModels2022", AHER_NOTE_FRONTMATTER)

    notes_title_index = research_mod._load_notes_title_index(lit_dir)
    all_citekeys = {ck for lst in notes_title_index.values() for ck, _ in lst}
    assert "argyleOutOneMany2022" not in all_citekeys, (
        f"An id-carrying note must not appear in the title index; got {notes_title_index}"
    )
    assert "aherLargeLanguageModels2022" in all_citekeys, (
        f"A no-id note must appear in the title index; got {notes_title_index}"
    )


# ---------------------------------------------------------------------------
# rv-023: BBT citekey field emitted, not filename stem
# ---------------------------------------------------------------------------

def test_load_notes_index_emits_bbt_citekey_field_when_present(tmp_path: Path) -> None:
    """RED before fix: _load_notes_index emits the FILENAME stem
    (argyle-2023-silicon-sampling), never the note's own `citekey:` frontmatter
    field (argyleOutOneMany2022) — the Better BibTeX key a researcher actually
    cites. GREEN after fix: the citekey: field wins when present.
    """
    lit_dir = tmp_path / "literature"
    _make_literature_note(
        lit_dir,
        "argyle-2023-silicon-sampling",  # filename slug — differs from the BBT key
        doi="10.1017/pan.2023.2",
        frontmatter_citekey="argyleOutOneMany2022",
    )
    idx = research_mod._load_notes_index(lit_dir)
    assert idx.get("10.1017/pan.2023.2") == "argyleOutOneMany2022", (
        f"Expected the note's citekey: field (BBT key), not the filename stem; got {idx}"
    )


def test_load_notes_index_falls_back_to_filename_stem_when_no_citekey_field(tmp_path: Path) -> None:
    """A note with NO `citekey:` frontmatter field falls back to the filename
    stem (the ~10 unlinked notes in the real corpus)."""
    lit_dir = tmp_path / "literature"
    _make_literature_note(
        lit_dir, "unlinked-note-2020", doi="10.9999/unlinked",
    )  # no frontmatter_citekey given
    idx = research_mod._load_notes_index(lit_dir)
    assert idx.get("10.9999/unlinked") == "unlinked-note-2020"


def test_load_notes_title_index_emits_bbt_citekey_field_when_present(tmp_path: Path) -> None:
    """_load_notes_title_index (the no-id fallback tier) must also emit the
    note's own `citekey:` field, not the filename stem, when present."""
    lit_dir = tmp_path / "literature"
    _write_realistic_note(
        lit_dir,
        "aher-2023-simulate-humans",  # filename slug — differs from the BBT key
        AHER_NOTE_FRONTMATTER,
        bbt_citekey="aherLargeLanguageModels2022",
    )
    notes_title_index = research_mod._load_notes_title_index(lit_dir)
    all_citekeys = {ck for lst in notes_title_index.values() for ck, _ in lst}
    assert "aherLargeLanguageModels2022" in all_citekeys, (
        f"Expected the note's citekey: field (BBT key); got {notes_title_index}"
    )
    assert "aher-2023-simulate-humans" not in all_citekeys


def test_corpus_annotation_emits_bbt_citekey_end_to_end(tmp_path: Path) -> None:
    """End-to-end: `rv research references` style annotation must show the
    BBT citekey (argyleOutOneMany2022), not the filename slug
    (argyle-2023-silicon-sampling), for a note filed with a `citekey:` field
    that differs from its filename — the real 116/126 csb-notes case (rv-023).
    """
    lit_dir = tmp_path / "literature"
    _make_literature_note(
        lit_dir,
        "argyle-2023-silicon-sampling",
        arxiv_id="2209.06899",
        frontmatter_citekey="argyleOutOneMany2022",
    )
    notes_index = research_mod._load_notes_index(lit_dir)
    result = research_mod._corpus_annotation(ARGYLE_S2_CANDIDATE, notes_index=notes_index)
    assert result == "[IN-CORPUS:argyleOutOneMany2022]", (
        f"Must emit the BBT citekey, not the filename slug; got {result!r}"
    )
