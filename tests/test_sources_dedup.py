"""test_sources_dedup.py — NG-2 cross-source identity collapse.

Priority: DOI > arXiv > OpenAlex > normalized-title. Union of external_ids;
`sources` records every adapter that independently surfaced the paper.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.sources.base import PaperHit
from research_vault.sources.dedup import dedup_hits, identity_key


def _hit(source, **kw) -> PaperHit:
    defaults = dict(
        title="Attention Is All You Need", year=2017, authors=["Ashish Vaswani"],
        external_ids={}, abstract="", citation_count=0,
    )
    defaults.update(kw)
    return PaperHit(source=source, **defaults)


def test_dedup_collapses_by_doi() -> None:
    a = _hit("semantic-scholar", external_ids={"doi": "10.1/x", "arxiv": "1706.03762"})
    b = _hit("openalex", external_ids={"doi": "10.1/X"})  # case-insensitive
    deduped = dedup_hits([a, b])
    assert len(deduped) == 1
    d = deduped[0]
    assert d.sources == {"semantic-scholar", "openalex"}
    assert d.external_ids["arxiv"] == "1706.03762"  # union preserved


def test_dedup_falls_back_to_arxiv_when_no_doi() -> None:
    a = _hit("semantic-scholar", external_ids={"arxiv": "1706.03762v1"})
    b = _hit("arxiv", external_ids={"arxiv": "1706.03762v5"})  # version stripped
    deduped = dedup_hits([a, b])
    assert len(deduped) == 1
    assert deduped[0].sources == {"semantic-scholar", "arxiv"}


def test_dedup_falls_back_to_title_when_no_ids() -> None:
    a = _hit("arxiv", title="A Novel Method", external_ids={})
    b = _hit("openalex", title="A  Novel   Method", external_ids={})  # whitespace differs
    deduped = dedup_hits([a, b])
    assert len(deduped) == 1


def test_dedup_keeps_distinct_papers_separate() -> None:
    a = _hit("semantic-scholar", title="Paper A", external_ids={"doi": "10.1/a"})
    b = _hit("semantic-scholar", title="Paper B", external_ids={"doi": "10.1/b"})
    deduped = dedup_hits([a, b])
    assert len(deduped) == 2


def test_dedup_order_preserving_first_seen_wins() -> None:
    a = _hit("semantic-scholar", abstract="rich abstract from S2", external_ids={"doi": "10.1/x"})
    b = _hit("arxiv", abstract="", external_ids={"doi": "10.1/x"})
    deduped = dedup_hits([a, b])
    assert deduped[0].hit.abstract == "rich abstract from S2"


def test_identity_key_priority_doi_over_arxiv() -> None:
    hit = _hit("s2", external_ids={"doi": "10.1/x", "arxiv": "1706.03762"})
    assert identity_key(hit).startswith("doi:")
