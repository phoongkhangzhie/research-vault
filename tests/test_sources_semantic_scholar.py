"""test_sources_semantic_scholar.py — NG-1 SemanticScholarAdapter parity.

Pure-refactor acceptance: the adapter must shell to the SAME asta subcommands
research.py used to call inline, and produce PaperHit.raw == the original S2
dict (byte-identical downstream behavior for _corpus_annotation/_print_candidates).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.sources.semantic_scholar import SemanticScholarAdapter

S2_PAPER = {
    "title": "Attention Is All You Need",
    "year": 2017,
    "authors": [{"name": "Ashish Vaswani"}],
    "externalIds": {"DOI": "10.48550/ARXIV.1706.03762", "ArXiv": "1706.03762"},
    "citationCount": 50000,
    "abstract": "We propose a new architecture...",
}


def test_search_calls_asta_papers_search(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"data": [S2_PAPER]})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()
    hits = adapter.search("attention", limit=10)

    assert captured["cmd"][:3] == ["asta", "papers", "search"]
    assert len(hits) == 1
    hit = hits[0]
    assert hit.title == "Attention Is All You Need"
    assert hit.year == 2017
    assert hit.authors == ["Ashish Vaswani"]
    assert hit.external_ids["doi"] == "10.48550/ARXIV.1706.03762"
    assert hit.external_ids["arxiv"] == "1706.03762"
    assert hit.citation_count == 50000
    assert hit.source == "semantic-scholar"
    assert hit.raw == S2_PAPER  # zero-behavior-change seam


def test_cited_by_calls_asta_papers_citations(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"data": [{"citingPaper": S2_PAPER}]})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()
    hits = adapter.cited_by("ARXIV:1706.03762", limit=20)

    assert captured["cmd"][:3] == ["asta", "papers", "citations"]
    assert len(hits) == 1
    assert hits[0].raw == S2_PAPER


def test_references_calls_asta_papers_get(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"references": [S2_PAPER]})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()
    hits = adapter.references("ARXIV:1706.03762")

    assert captured["cmd"][:3] == ["asta", "papers", "get"]
    assert len(hits) == 1
    assert hits[0].raw == S2_PAPER


def test_search_exits_on_asta_failure(monkeypatch) -> None:
    import pytest

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 1
        r.stdout = ""
        r.stderr = "boom"
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()
    with pytest.raises(SystemExit):
        adapter.search("q")
