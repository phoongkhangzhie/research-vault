"""test_sources_registry.py — NG-2 adapter registry + D4 default-on set."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.sources.registry import (
    DEFAULT_SOURCES,
    OPT_IN_SOURCES,
    get_adapter,
)
from research_vault.sources.arxiv import ArxivAdapter
from research_vault.sources.openalex import OpenAlexAdapter
from research_vault.sources.pubmed import PubMedAdapter
from research_vault.sources.semantic_scholar import SemanticScholarAdapter


def test_default_sources_is_d4_set() -> None:
    assert DEFAULT_SOURCES == ("semantic-scholar", "arxiv", "openalex")


def test_pubmed_is_opt_in_not_default() -> None:
    assert "pubmed" in OPT_IN_SOURCES
    assert "pubmed" not in DEFAULT_SOURCES


def test_get_adapter_resolves_each_default_source() -> None:
    assert isinstance(get_adapter("semantic-scholar"), SemanticScholarAdapter)
    assert isinstance(get_adapter("arxiv"), ArxivAdapter)
    assert isinstance(get_adapter("openalex"), OpenAlexAdapter)


def test_get_adapter_resolves_pubmed() -> None:
    assert isinstance(get_adapter("pubmed"), PubMedAdapter)


def test_get_adapter_unknown_name_raises_loud() -> None:
    with pytest.raises(ValueError, match="unknown source adapter"):
        get_adapter("web")


def test_get_adapter_returns_fresh_instance_each_call() -> None:
    a = get_adapter("arxiv")
    b = get_adapter("arxiv")
    assert a is not b
