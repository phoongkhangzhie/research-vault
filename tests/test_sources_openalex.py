"""test_sources_openalex.py — NG-2 OpenAlexAdapter (search + both citation directions)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.sources import openalex as oa_mod

WORK = {
    "id": "https://openalex.org/W2963341956",
    "title": "Attention Is All You Need",
    "publication_year": 2017,
    "doi": "https://doi.org/10.48550/arxiv.1706.03762",
    "ids": {"mag": "2963341956", "pmid": "https://pubmed.ncbi.nlm.nih.gov/12345"},
    "authorships": [{"author": {"display_name": "Ashish Vaswani"}}],
    "abstract_inverted_index": {"We": [0], "propose": [1], "attention": [2]},
    "cited_by_count": 90000,
    "referenced_works": ["https://openalex.org/W111"],
    "open_access": {"is_oa": True, "oa_status": "green", "oa_url": "https://arxiv.org/pdf/1706.03762"},
    "primary_location": {"pdf_url": "https://example.org/1706.03762.pdf"},
}

WORK_CLOSED = {**WORK, "open_access": {"is_oa": False, "oa_status": "closed", "oa_url": None}}


def test_search_parses_works(monkeypatch) -> None:
    monkeypatch.setattr(oa_mod, "_fetch_json", lambda url: {"results": [WORK]})
    adapter = oa_mod.OpenAlexAdapter()
    hits = adapter.search("attention")

    assert len(hits) == 1
    hit = hits[0]
    assert hit.title == "Attention Is All You Need"
    assert hit.year == 2017
    assert hit.external_ids["openalex"] == "W2963341956"
    assert hit.external_ids["doi"] == "10.48550/arxiv.1706.03762"
    assert hit.external_ids["mag"] == "2963341956"
    assert hit.external_ids["pmid"] == "12345"
    assert hit.citation_count == 90000
    assert hit.abstract == "We propose attention"
    assert hit.source == "openalex"
    # OA-fulltext-enrichment: open_access.oa_url is already in hit.raw — zero
    # extra request, just stop discarding it.
    assert hit.oa_url == "https://arxiv.org/pdf/1706.03762"
    assert hit.oa_status == "green"
    assert hit.oa_source == "openalex"


def test_search_carries_venue_from_primary_location(monkeypatch) -> None:
    work_with_venue = {
        **WORK,
        "primary_location": {**WORK["primary_location"], "source": {"display_name": "NeurIPS"}},
    }
    monkeypatch.setattr(oa_mod, "_fetch_json", lambda url: {"results": [work_with_venue]})
    adapter = oa_mod.OpenAlexAdapter()
    hits = adapter.search("attention")
    assert hits[0].venue == "NeurIPS"


def test_search_venue_falls_back_to_host_venue(monkeypatch) -> None:
    work_legacy = {**WORK, "host_venue": {"display_name": "Legacy Venue"}}
    monkeypatch.setattr(oa_mod, "_fetch_json", lambda url: {"results": [work_legacy]})
    adapter = oa_mod.OpenAlexAdapter()
    hits = adapter.search("attention")
    assert hits[0].venue == "Legacy Venue"


def test_search_venue_absent_is_none(monkeypatch) -> None:
    monkeypatch.setattr(oa_mod, "_fetch_json", lambda url: {"results": [WORK]})
    adapter = oa_mod.OpenAlexAdapter()
    hits = adapter.search("attention")
    assert hits[0].venue is None


def test_search_no_oa_when_closed(monkeypatch) -> None:
    monkeypatch.setattr(oa_mod, "_fetch_json", lambda url: {"results": [WORK_CLOSED]})
    adapter = oa_mod.OpenAlexAdapter()
    hits = adapter.search("attention")

    assert hits[0].oa_url is None
    assert hits[0].oa_status == "closed"
    assert hits[0].oa_source is None


def test_cited_by_uses_cites_filter(monkeypatch) -> None:
    captured = {}

    def fake_fetch(url):
        captured["url"] = url
        return {"results": [WORK]}

    monkeypatch.setattr(oa_mod, "_fetch_json", fake_fetch)
    adapter = oa_mod.OpenAlexAdapter()
    hits = adapter.cited_by("W2963341956")

    assert "cites%3AW2963341956" in captured["url"] or "cites:W2963341956" in captured["url"]
    assert len(hits) == 1


def test_references_resolves_referenced_works(monkeypatch) -> None:
    calls = []

    def fake_fetch(url):
        calls.append(url)
        if url.endswith("W2963341956"):
            return WORK
        return {**WORK, "id": url, "title": "Referenced Paper"}

    monkeypatch.setattr(oa_mod, "_fetch_json", fake_fetch)
    adapter = oa_mod.OpenAlexAdapter()
    hits = adapter.references("W2963341956")

    assert len(hits) == 1
    assert hits[0].title == "Referenced Paper"
