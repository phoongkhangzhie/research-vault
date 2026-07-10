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
# Test 5: Parser — ``rv research references`` is D1-HARD-REMOVED (verb
# consolidation) — it still PARSES (as a redirect stub), it no longer runs
# the old cited-by/references behavior. See test_verb_consolidation.py for
# the shared D1 acceptance; this file's own regression pin lives here too
# since it tests cmd_references (the underlying function, unchanged/still
# importable) heavily above.
# ---------------------------------------------------------------------------

def test_references_parser_registered_as_removed_stub() -> None:
    """build_parser() still registers 'references' (as a D1 redirect stub) —
    it no longer accepts paper_id; any trailing args are swallowed."""
    p = research_mod.build_parser()
    args = p.parse_args(["references", "ARXIV:2005.14165"])
    assert args.research_cmd == "references"
    assert getattr(args, "_rv_removed_verb", None) is not None


# ---------------------------------------------------------------------------
# Test 6: run() dispatches the D1 stub to its redirect breadcrumb (exit 2),
# NOT to cmd_references (the CLI verb is removed; cmd_references itself is
# still directly callable, per tests 1-4/10 above).
# ---------------------------------------------------------------------------

def test_run_dispatches_removed_references_stub(capsys) -> None:
    """run() must route the D1-removed 'references' verb to the redirect
    breadcrumb (exit 2) — never to cmd_references anymore."""
    p = research_mod.build_parser()
    args = p.parse_args(["references", "ARXIV:2005.14165"])
    rc = research_mod.run(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "REMOVED" in err


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


# ---------------------------------------------------------------------------
# Test 8: rv help --check stays green
# ---------------------------------------------------------------------------

def test_cli_help_check_still_green() -> None:
    """_check_verb_docstrings() must return no violations after adding references."""
    from research_vault.cli import _check_verb_docstrings
    violations = _check_verb_docstrings()
    assert violations == [], f"rv help --check violations: {violations}"


# ---------------------------------------------------------------------------
# Test 9: D1 (verb consolidation) — cited-by/references are both REMOVED
# stubs now; the cross-referencing help text they used to carry is retired
# along with the verbs. Each stub's help instead redirects to the DAG
# node-op equivalent (see cli_removed_verbs.py / test_verb_consolidation.py).
# ---------------------------------------------------------------------------

def test_cited_by_and_references_stubs_redirect_to_dag(capsys) -> None:
    """Both removed stubs point at `rv dag run` (the node-op equivalent),
    not at each other — the old cross-reference help text is retired with
    the verbs it described."""
    p = research_mod.build_parser()
    for verb in ("cited-by", "references"):
        args = p.parse_args([verb, "ARXIV:1"])
        rc = research_mod.run(args)
        assert rc == 2
        err = capsys.readouterr().err
        assert "rv dag run" in err


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
