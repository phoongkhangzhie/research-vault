"""test_sr_lr_polish_s1.py — SR-LR-POLISH Slice 1: F12 arXiv-id normalization shim.

Acceptance criteria:
  - Unit table: bare new/old arXiv → ARXIV:, bare DOI → DOI:, already-prefixed
    → unchanged, S2 sha → unchanged, junk → unchanged.
  - cmd_cited_by builds asta argv with the normalized id (subprocess stubbed).
  - cmd_references builds asta argv with the normalized id (subprocess stubbed).
  - Zero-result on a bare/normalized id prints the hint to stderr.
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
from research_vault.research import _normalize_paper_id_for_asta


# ---------------------------------------------------------------------------
# _normalize_paper_id_for_asta unit table
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    # Bare new-style arXiv
    ("2005.14165",          "ARXIV:2005.14165"),
    ("1706.03762",          "ARXIV:1706.03762"),
    ("2305.10403v2",        "ARXIV:2305.10403v2"),
    # Bare old-style arXiv
    ("cs.LG/0604056",       "ARXIV:cs.LG/0604056"),
    ("hep-ph/9901001",      "ARXIV:hep-ph/9901001"),
    # Bare DOI
    ("10.18653/v1/N19-1423", "DOI:10.18653/v1/N19-1423"),
    ("10.1234/test.paper",   "DOI:10.1234/test.paper"),
    # Already prefixed — pass through unchanged (all supported schemes)
    ("ARXIV:2005.14165",    "ARXIV:2005.14165"),
    ("arxiv:2005.14165",    "arxiv:2005.14165"),  # lowercase preserved
    ("DOI:10.18653/v1/N19-1423", "DOI:10.18653/v1/N19-1423"),
    ("CorpusId:123456",     "CorpusId:123456"),
    ("MAG:1234567890",      "MAG:1234567890"),
    ("PMID:123456",         "PMID:123456"),
    ("URL:https://arxiv.org/abs/2005.14165", "URL:https://arxiv.org/abs/2005.14165"),
    # 40-hex S2 SHA — pass through unchanged
    ("a" * 40,              "a" * 40),
    ("0123456789abcdef" * 2 + "01234567", "0123456789abcdef" * 2 + "01234567"),
    # Junk / unknown format — pass through unchanged
    ("some-random-string",  "some-random-string"),
    ("notanid",             "notanid"),
    ("",                    ""),
])
def test_normalize_paper_id_table(raw, expected):
    """_normalize_paper_id_for_asta matches the expected output for all cases."""
    assert _normalize_paper_id_for_asta(raw) == expected


def test_normalize_bare_doi_short_prefix_not_matched():
    """A DOI with fewer than 4-digit publisher prefix must NOT be normalized (don't guess)."""
    # "10.1/x" has a 1-digit publisher prefix — not a valid DOI pattern
    raw = "10.1/something"
    result = _normalize_paper_id_for_asta(raw)
    assert result == raw, f"Should pass through; got {result!r}"


# ---------------------------------------------------------------------------
# cmd_cited_by uses normalized id in asta argv
# ---------------------------------------------------------------------------

def _make_cited_by_args(paper_id: str, limit: int = 20) -> argparse.Namespace:
    return argparse.Namespace(paper_id=paper_id, project=None, limit=limit)


def _mock_citations(papers: list[dict], returncode: int = 0) -> MagicMock:
    r = MagicMock()
    r.returncode = returncode
    r.stdout = json.dumps({"data": [{"citingPaper": p} for p in papers]})
    r.stderr = ""
    return r


def test_cited_by_passes_normalized_id_to_asta(monkeypatch, capsys):
    """cmd_cited_by must call asta with the normalized (scheme-prefixed) id."""
    captured_argv = []

    def fake_run(cmd, **kwargs):
        captured_argv.extend(cmd)
        return _mock_citations([])

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)

    args = _make_cited_by_args("2005.14165")  # bare arXiv
    research_mod.cmd_cited_by(args)

    # The normalized id must appear in the command
    assert "ARXIV:2005.14165" in captured_argv, (
        f"Expected normalized ARXIV:2005.14165 in argv; got: {captured_argv}"
    )
    # The bare id must NOT appear as a separate argument
    assert "2005.14165" not in captured_argv, (
        f"Bare id should not appear in argv (normalized form used); got: {captured_argv}"
    )


def test_cited_by_already_prefixed_unchanged(monkeypatch, capsys):
    """cmd_cited_by must not double-prefix an already-prefixed id."""
    captured_argv = []

    def fake_run(cmd, **kwargs):
        captured_argv.extend(cmd)
        return _mock_citations([])

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)

    args = _make_cited_by_args("ARXIV:2005.14165")
    research_mod.cmd_cited_by(args)

    # Appears exactly once (not doubled)
    count = captured_argv.count("ARXIV:2005.14165")
    assert count == 1, f"Expected exactly 1 occurrence; got {count} in {captured_argv}"
    assert "ARXIV:ARXIV:2005.14165" not in captured_argv


# ---------------------------------------------------------------------------
# cmd_references uses normalized id in asta argv
# ---------------------------------------------------------------------------

def _make_references_args(paper_id: str) -> argparse.Namespace:
    return argparse.Namespace(paper_id=paper_id, project=None)


def _mock_get(refs: list[dict], returncode: int = 0) -> MagicMock:
    r = MagicMock()
    r.returncode = returncode
    r.stdout = json.dumps({"references": refs})
    r.stderr = ""
    return r


def test_references_passes_normalized_id_to_asta(monkeypatch, capsys):
    """cmd_references must call asta with the normalized (scheme-prefixed) id."""
    captured_argv = []

    def fake_run(cmd, **kwargs):
        captured_argv.extend(cmd)
        return _mock_get([])

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)

    args = _make_references_args("1706.03762")  # bare arXiv
    research_mod.cmd_references(args)

    assert "ARXIV:1706.03762" in captured_argv, (
        f"Expected normalized ARXIV:1706.03762 in argv; got: {captured_argv}"
    )
    assert "1706.03762" not in captured_argv


def test_references_bare_doi_normalized(monkeypatch, capsys):
    """cmd_references normalizes a bare DOI before passing to asta."""
    captured_argv = []

    def fake_run(cmd, **kwargs):
        captured_argv.extend(cmd)
        return _mock_get([])

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)

    args = _make_references_args("10.18653/v1/N19-1423")  # bare DOI
    research_mod.cmd_references(args)

    assert "DOI:10.18653/v1/N19-1423" in captured_argv, (
        f"Expected DOI:10.18653/v1/N19-1423 in argv; got: {captured_argv}"
    )


# ---------------------------------------------------------------------------
# Zero-result on bare/normalized id emits stderr hint
# ---------------------------------------------------------------------------

def test_cited_by_zero_results_bare_id_emits_hint(monkeypatch, capsys):
    """cmd_cited_by with 0 results and a bare (normalized) id must print a hint to stderr."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _mock_citations([]))
    monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)

    args = _make_cited_by_args("2005.14165")  # bare → normalized → 0 results
    research_mod.cmd_cited_by(args)

    err = capsys.readouterr().err
    assert "normalized" in err.lower() or "arxiv" in err.lower(), (
        f"Expected zero-result hint in stderr; got: {err!r}"
    )


def test_cited_by_zero_results_prefixed_no_hint(monkeypatch, capsys):
    """cmd_cited_by with 0 results but an already-prefixed id must NOT emit the hint."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _mock_citations([]))
    monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)

    args = _make_cited_by_args("ARXIV:2005.14165")  # already prefixed
    research_mod.cmd_cited_by(args)

    err = capsys.readouterr().err
    # No hint expected — the id was not normalized
    assert "normalized" not in err.lower(), (
        f"Unexpected hint for already-prefixed id; stderr: {err!r}"
    )


def test_references_zero_results_bare_id_emits_hint(monkeypatch, capsys):
    """cmd_references with 0 results and a bare id must print a hint to stderr."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _mock_get([]))
    monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)

    args = _make_references_args("2005.14165")
    research_mod.cmd_references(args)

    err = capsys.readouterr().err
    assert "normalized" in err.lower() or "arxiv" in err.lower(), (
        f"Expected zero-result hint in stderr; got: {err!r}"
    )


def test_references_zero_results_prefixed_no_hint(monkeypatch, capsys):
    """cmd_references with 0 results but already-prefixed id must NOT emit hint."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _mock_get([]))
    monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)

    args = _make_references_args("ARXIV:2005.14165")
    research_mod.cmd_references(args)

    err = capsys.readouterr().err
    assert "normalized" not in err.lower(), (
        f"Unexpected hint for prefixed id; stderr: {err!r}"
    )
