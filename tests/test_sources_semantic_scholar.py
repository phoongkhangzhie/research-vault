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

import pytest

from research_vault.sources.base import AdapterFetchError
from research_vault.sources.semantic_scholar import SemanticScholarAdapter

S2_PAPER = {
    "title": "Attention Is All You Need",
    "year": 2017,
    "authors": [{"name": "Ashish Vaswani"}],
    "externalIds": {"DOI": "10.48550/ARXIV.1706.03762", "ArXiv": "1706.03762"},
    "citationCount": 50000,
    "abstract": "We propose a new architecture...",
}

S2_PAPER_WITH_OA = {
    **S2_PAPER,
    "openAccessPdf": {"url": "https://arxiv.org/pdf/1706.03762", "status": "GREEN"},
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


def test_search_requests_openaccesspdf_field(monkeypatch) -> None:
    # OA-fulltext-enrichment: openAccessPdf must be in the --fields projection
    # or the field is never even in hit.raw for downstream OA capture.
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
    adapter.search("attention", limit=10)

    fields_idx = captured["cmd"].index("--fields") + 1
    assert "openAccessPdf" in captured["cmd"][fields_idx]


def test_search_captures_oa_pointer_from_openaccesspdf(monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"data": [S2_PAPER_WITH_OA]})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()
    hits = adapter.search("attention", limit=10)

    assert hits[0].oa_url == "https://arxiv.org/pdf/1706.03762"
    assert hits[0].oa_status == "green"
    assert hits[0].oa_source == "semantic-scholar"


def test_search_no_oa_pointer_when_openaccesspdf_absent(monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = json.dumps({"data": [S2_PAPER]})
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()
    hits = adapter.search("attention", limit=10)

    assert hits[0].oa_url is None
    assert hits[0].oa_status is None
    assert hits[0].oa_source is None


def test_search_exits_on_asta_failure(monkeypatch) -> None:
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


# ---------------------------------------------------------------------------
# 2026-07-09 live-asta validation bug: cited_by/references must raise a
# catchable AdapterFetchError (NOT sys.exit) on a non-zero asta exit (e.g. a
# 404 for one seed id) — SystemExit is a BaseException, invisible to the
# multi-round snowball walk's `except Exception` degrade clauses, so it used
# to abort the ENTIRE walk on one unresolvable seed. `search` is unaffected
# (still sys.exit — a single-shot CLI action, no multi-item walk to degrade).
# ---------------------------------------------------------------------------

def test_cited_by_raises_adapter_fetch_error_not_systemexit(monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 1
        r.stdout = ""
        r.stderr = "asta: paper not found (404)"
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()
    with pytest.raises(AdapterFetchError):
        adapter.cited_by("ARXIV:9999.9999")
    # Explicitly NOT SystemExit — the whole point of the fix.
    try:
        adapter.cited_by("ARXIV:9999.9999")
    except AdapterFetchError:
        pass
    except SystemExit:
        pytest.fail("cited_by must not raise SystemExit — it must raise AdapterFetchError")


def test_references_raises_adapter_fetch_error_not_systemexit(monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 1
        r.stdout = ""
        r.stderr = "asta: paper not found (404)"
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = SemanticScholarAdapter()
    with pytest.raises(AdapterFetchError):
        adapter.references("ARXIV:9999.9999")
    try:
        adapter.references("ARXIV:9999.9999")
    except AdapterFetchError:
        pass
    except SystemExit:
        pytest.fail("references must not raise SystemExit — it must raise AdapterFetchError")
