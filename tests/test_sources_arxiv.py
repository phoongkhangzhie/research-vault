"""test_sources_arxiv.py — NG-2 ArxivAdapter (search + NotSupported citation graph)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.sources import arxiv as arxiv_mod
from research_vault.sources.base import NotSupported

ATOM_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/1706.03762v5</id>
    <published>2017-06-12T17:57:34Z</published>
    <title>  Attention   Is All You Need
    </title>
    <summary>We propose a new simple network architecture.</summary>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
    <arxiv:doi>10.48550/arXiv.1706.03762</arxiv:doi>
  </entry>
</feed>
"""


def test_search_parses_atom_entries(monkeypatch) -> None:
    monkeypatch.setattr(arxiv_mod, "_fetch_atom", lambda query, *, limit: ATOM_FEED)
    adapter = arxiv_mod.ArxivAdapter()
    hits = adapter.search("attention", limit=5)

    assert len(hits) == 1
    hit = hits[0]
    assert hit.title == "Attention Is All You Need"
    assert hit.year == 2017
    assert hit.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert hit.external_ids["arxiv"] == "1706.03762"
    assert hit.external_ids["doi"] == "10.48550/arXiv.1706.03762"
    assert hit.source == "arxiv"
    assert hit.citation_count == 0
    # OA-fulltext-enrichment: arXiv is trivially derivable OA (green) — every
    # arXiv hit with an arxiv id gets an oa_url, no extra request needed.
    assert hit.oa_url == "https://arxiv.org/pdf/1706.03762.pdf"
    assert hit.oa_status == "green"
    assert hit.oa_source == "arxiv"


ATOM_FEED_WITH_JOURNAL_REF = ATOM_FEED.replace(
    "<arxiv:doi>10.48550/arXiv.1706.03762</arxiv:doi>",
    "<arxiv:doi>10.48550/arXiv.1706.03762</arxiv:doi>\n"
    "    <arxiv:journal_ref>NeurIPS 2017</arxiv:journal_ref>",
)


def test_search_carries_venue_from_journal_ref(monkeypatch) -> None:
    monkeypatch.setattr(arxiv_mod, "_fetch_atom", lambda query, *, limit: ATOM_FEED_WITH_JOURNAL_REF)
    adapter = arxiv_mod.ArxivAdapter()
    hits = adapter.search("attention", limit=5)
    assert hits[0].venue == "NeurIPS 2017"


def test_search_venue_absent_is_none(monkeypatch) -> None:
    monkeypatch.setattr(arxiv_mod, "_fetch_atom", lambda query, *, limit: ATOM_FEED)
    adapter = arxiv_mod.ArxivAdapter()
    hits = adapter.search("attention", limit=5)
    assert hits[0].venue is None


def test_cited_by_not_supported() -> None:
    adapter = arxiv_mod.ArxivAdapter()
    with pytest.raises(NotSupported):
        adapter.cited_by("1706.03762")


def test_references_not_supported() -> None:
    adapter = arxiv_mod.ArxivAdapter()
    with pytest.raises(NotSupported):
        adapter.references("1706.03762")
