"""test_research_references.py — TDD tests for rv research references (backward snowball).

SR-LR-1 backward citation chase: ``rv research references <paper-id>`` fetches
the seed's own reference list via ``asta papers get --fields references.*``.

Design invariants tested:
  1.  cmd_references calls ``asta papers get`` with ``--fields references.*``
      (NOT ``asta papers citations`` or ``asta papers references``)
  2.  Raw payload is taken from raw["references"] (not raw["data"])
  3.  _print_candidates shared helper is reused — output format matches cited-by
  4.  asta non-zero exit propagates (sys.exit)
  5.  Parser: ``rv research references`` is a registered subcommand
  6.  Dispatcher: run() routes ``research_cmd == "references"`` to cmd_references
  7.  cli.py _VERB_REGISTRY "research" entry contains backward-snowball when_to_use
  8.  rv help --check remains green (no empty when_to_use)
  9.  Cross-reference: cited-by help mentions "references" (and vice versa)
 10.  Empty reference list prints "0 candidate(s)"

All tests hermetic: asta is mocked via monkeypatch, no live network.
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
# Helpers
# ---------------------------------------------------------------------------

def _make_asta_get_result(refs: list[dict], returncode: int = 0) -> MagicMock:
    """Fake subprocess.run result for asta papers get --fields references.*"""
    r = MagicMock()
    r.returncode = returncode
    r.stdout = json.dumps({"references": refs})
    r.stderr = ""
    return r


def _make_args(paper_id: str = "ARXIV:2005.14165", project: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(
        paper_id=paper_id,
        project=project,
    )


SAMPLE_REFS = [
    {
        "title": "Attention Is All You Need",
        "year": 2017,
        "authors": [{"name": "Ashish Vaswani"}],   # S2 format: First Last
        "externalIds": {"ArXiv": "1706.03762"},
        "citationCount": 50000,
    },
    {
        "title": "BERT: Pre-training of Deep Bidirectional Transformers",
        "year": 2018,
        "authors": [{"name": "Jacob Devlin"}],      # S2 format: First Last
        "externalIds": {"DOI": "10.18653/v1/N19-1423"},
        "citationCount": 40000,
    },
]


# ---------------------------------------------------------------------------
# Test 1: asta papers get --fields references.* is called
# ---------------------------------------------------------------------------

def test_references_calls_asta_papers_get(monkeypatch, capsys) -> None:
    """cmd_references must call asta papers get with --fields references.*"""
    captured_cmd = []

    def fake_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        return _make_asta_get_result(SAMPLE_REFS)

    monkeypatch.setattr(subprocess, "run", fake_run)

    # Patch _preflight_asta to skip auth check
    monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)

    args = _make_args(paper_id="ARXIV:2005.14165")
    rc = research_mod.cmd_references(args)
    assert rc == 0

    # Must use 'asta papers get', NOT 'asta papers citations'
    assert "asta" in captured_cmd
    assert "papers" in captured_cmd
    assert "get" in captured_cmd
    assert "citations" not in captured_cmd

    # Must include --fields with references.*
    fields_idx = captured_cmd.index("--fields")
    fields_val = captured_cmd[fields_idx + 1]
    assert "references" in fields_val

    # Paper id must appear
    assert "ARXIV:2005.14165" in captured_cmd


# ---------------------------------------------------------------------------
# Test 2: payload extracted from raw["references"]
# ---------------------------------------------------------------------------

def test_references_extracts_from_references_key(monkeypatch, capsys) -> None:
    """cmd_references must read raw['references'], not raw['data']."""
    payload = {
        "references": SAMPLE_REFS,
        "data": [{"title": "WRONG — should not appear"}],
    }

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps(payload)
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)

    args = _make_args()
    rc = research_mod.cmd_references(args)
    assert rc == 0

    out = capsys.readouterr().out
    assert "Attention Is All You Need" in out
    assert "WRONG" not in out


# ---------------------------------------------------------------------------
# Test 3: _print_candidates shared helper is reused — output matches cited-by format
# ---------------------------------------------------------------------------

def test_references_output_format(monkeypatch, capsys) -> None:
    """Output format must match _print_candidates: N candidate(s), author year title."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _make_asta_get_result(SAMPLE_REFS))
    monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)

    args = _make_args()
    research_mod.cmd_references(args)
    out = capsys.readouterr().out

    assert "2 candidate(s)" in out
    assert "Vaswani" in out  # first author family name
    assert "2017" in out
    assert "Attention Is All You Need" in out
    assert "arXiv:1706.03762" in out


# ---------------------------------------------------------------------------
# Test 4: asta non-zero exit propagates via sys.exit
# ---------------------------------------------------------------------------

def test_references_asta_error_exits(monkeypatch) -> None:
    """When asta returns non-zero, cmd_references must call sys.exit."""
    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 1
        r.stdout = ""
        r.stderr = "asta: paper not found"
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)

    args = _make_args(paper_id="ARXIV:9999.9999")
    with pytest.raises(SystemExit):
        research_mod.cmd_references(args)


# ---------------------------------------------------------------------------
# Test 5: Parser — ``rv research references`` is a registered subcommand
# ---------------------------------------------------------------------------

def test_references_parser_registered() -> None:
    """build_parser() must register 'references' as a subcommand."""
    p = research_mod.build_parser()
    # Should not raise
    args = p.parse_args(["references", "ARXIV:2005.14165"])
    assert args.research_cmd == "references"
    assert args.paper_id == "ARXIV:2005.14165"


# ---------------------------------------------------------------------------
# Test 6: run() dispatches to cmd_references
# ---------------------------------------------------------------------------

def test_run_dispatches_references(monkeypatch, capsys) -> None:
    """run() must route research_cmd=='references' to cmd_references."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _make_asta_get_result(SAMPLE_REFS))
    monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)

    args = argparse.Namespace(research_cmd="references", paper_id="ARXIV:2005.14165", project=None)
    rc = research_mod.run(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "candidate(s)" in out


# ---------------------------------------------------------------------------
# Test 7: _VERB_REGISTRY "research" entry contains backward-snowball when_to_use
# ---------------------------------------------------------------------------

def test_verb_registry_research_mentions_backward_snowball() -> None:
    """_VERB_REGISTRY['research']['when_to_use'] must describe backward-snowball intent."""
    from research_vault.cli import _VERB_REGISTRY

    entry = _VERB_REGISTRY.get("research", {})
    when = entry.get("when_to_use", "")

    # Must reference backward snowball / references
    assert any(phrase in when.lower() for phrase in [
        "backward", "references", "reference list", "backward-snowball",
    ]), (
        f"'research' when_to_use must mention backward-snowball or references; got: {when!r}"
    )

    # Must include SR-LR-1 tag
    sr = entry.get("sr", "")
    assert "SR-LR-1" in sr or "SR-LR-1" in when, (
        f"'research' registry entry must reference SR-LR-1; got sr={sr!r}, when={when!r}"
    )


# ---------------------------------------------------------------------------
# Test 8: rv help --check stays green
# ---------------------------------------------------------------------------

def test_cli_help_check_still_green() -> None:
    """_check_verb_docstrings() must return no violations after adding references."""
    from research_vault.cli import _check_verb_docstrings
    violations = _check_verb_docstrings()
    assert violations == [], f"rv help --check violations: {violations}"


# ---------------------------------------------------------------------------
# Test 9: Cross-reference in help text
# ---------------------------------------------------------------------------

def test_cited_by_help_mentions_references() -> None:
    """cited-by parser help must mention 'references' (cross-reference backward snowball)."""
    p = research_mod.build_parser()
    # Dump the formatter output or check the subparser help
    import io
    buf = io.StringIO()
    try:
        p.parse_args(["cited-by", "--help"])
    except SystemExit:
        pass
    # Check the subparser description / help text for 'references'
    # We verify by inspecting the parser action groups
    sub_actions = p._subparsers._group_actions[0]._name_parser_map  # type: ignore[attr-defined]
    cb_parser = sub_actions.get("cited-by")
    assert cb_parser is not None

    # Help text or description must reference 'references'
    help_text = (cb_parser.description or "") + (cb_parser.format_help() or "")
    assert "references" in help_text.lower() or "backward" in help_text.lower(), (
        f"cited-by help text must mention 'references' or 'backward'; got:\n{help_text}"
    )


def test_references_help_mentions_cited_by() -> None:
    """references parser help must mention 'cited-by' (cross-reference forward snowball)."""
    p = research_mod.build_parser()
    sub_actions = p._subparsers._group_actions[0]._name_parser_map  # type: ignore[attr-defined]
    ref_parser = sub_actions.get("references")
    assert ref_parser is not None, "references subcommand must be registered"

    help_text = (ref_parser.description or "") + (ref_parser.format_help() or "")
    assert "cited-by" in help_text.lower() or "forward" in help_text.lower(), (
        f"references help text must mention 'cited-by' or 'forward'; got:\n{help_text}"
    )


# ---------------------------------------------------------------------------
# Test 10: Empty reference list prints "0 candidate(s)"
# ---------------------------------------------------------------------------

def test_references_empty_list(monkeypatch, capsys) -> None:
    """Empty references payload must print '0 candidate(s)'."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _make_asta_get_result([]))
    monkeypatch.setattr(research_mod, "_preflight_asta", lambda: None)

    args = _make_args()
    rc = research_mod.cmd_references(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "0 candidate(s)" in out
