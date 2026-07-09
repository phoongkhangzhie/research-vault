"""test_sources_base.py — NG-1 PaperHit / SourceAdapter protocol shape."""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.sources.base import NotSupported, PaperHit, SourceAdapter


def test_paperhit_defaults() -> None:
    hit = PaperHit(
        title="A Paper", year=2020, authors=["A. Author"],
        external_ids={"doi": "10.1/x"}, abstract="abs", citation_count=5,
        source="semantic-scholar",
    )
    assert hit.raw == {}
    assert hit.derivative_of is None
    assert hit.below_floor is False
    # OA-fulltext-enrichment tier 1: optional, small provenance-only fields —
    # absent by default (an adapter that never touches OA leaves them None).
    assert hit.oa_url is None
    assert hit.oa_status is None
    assert hit.oa_source is None


def test_paperhit_oa_fields_settable() -> None:
    hit = PaperHit(
        title="A Paper", year=2020, authors=["A. Author"],
        external_ids={"doi": "10.1/x"}, abstract="abs", citation_count=5,
        source="semantic-scholar",
        oa_url="https://example.org/paper.pdf",
        oa_status="gold",
        oa_source="s2",
    )
    assert hit.oa_url == "https://example.org/paper.pdf"
    assert hit.oa_status == "gold"
    assert hit.oa_source == "s2"


def test_notsupported_is_exception() -> None:
    assert issubclass(NotSupported, Exception)


def test_source_adapter_is_runtime_checkable_protocol() -> None:
    class Fake:
        name = "fake"

        def search(self, query, *, limit=20):
            return []

        def cited_by(self, paper_id, *, limit=20):
            raise NotSupported

        def references(self, paper_id, *, limit=20):
            raise NotSupported

    assert isinstance(Fake(), SourceAdapter)


def test_source_adapter_rejects_incomplete_impl() -> None:
    class Incomplete:
        name = "incomplete"

        def search(self, query, *, limit=20):
            return []

    assert not isinstance(Incomplete(), SourceAdapter)
