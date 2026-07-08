"""test_sources_pubmed.py — NG-2 PubMedAdapter (opt-in, search-only)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.sources import pubmed as pubmed_mod
from research_vault.sources.base import NotSupported

ESEARCH_RESULT = {"esearchresult": {"idlist": ["12345"]}}
ESUMMARY_RESULT = {
    "result": {
        "uids": ["12345"],
        "12345": {
            "title": "A Biomedical Paper",
            "pubdate": "2021 Jun",
            "authors": [{"name": "Smith J"}],
            "articleids": [{"idtype": "doi", "value": "10.1/xyz"}],
        },
    }
}


def test_search_esearch_then_esummary(monkeypatch) -> None:
    calls = []

    def fake_fetch(url):
        calls.append(url)
        if "esearch" in url:
            return ESEARCH_RESULT
        return ESUMMARY_RESULT

    monkeypatch.setattr(pubmed_mod, "_fetch_json", fake_fetch)
    adapter = pubmed_mod.PubMedAdapter()
    hits = adapter.search("covid", limit=10)

    assert len(calls) == 2
    assert len(hits) == 1
    hit = hits[0]
    assert hit.title == "A Biomedical Paper"
    assert hit.year == 2021
    assert hit.authors == ["Smith J"]
    assert hit.external_ids["pmid"] == "12345"
    assert hit.external_ids["doi"] == "10.1/xyz"
    assert hit.source == "pubmed"


def test_search_no_results(monkeypatch) -> None:
    monkeypatch.setattr(
        pubmed_mod, "_fetch_json",
        lambda url: {"esearchresult": {"idlist": []}},
    )
    adapter = pubmed_mod.PubMedAdapter()
    assert adapter.search("nonsense") == []


def test_cited_by_not_supported() -> None:
    adapter = pubmed_mod.PubMedAdapter()
    with pytest.raises(NotSupported):
        adapter.cited_by("12345")


def test_references_not_supported() -> None:
    adapter = pubmed_mod.PubMedAdapter()
    with pytest.raises(NotSupported):
        adapter.references("12345")
