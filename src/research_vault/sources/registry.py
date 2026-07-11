# SPDX-License-Identifier: AGPL-3.0-or-later
"""sources/registry.py — source-name -> adapter-instance resolution (NG-2).

``DEFAULT_SOURCES`` is the operator's D4 default-on set: semantic-scholar + arxiv +
openalex. PubMed and web are opt-in — a protocol adds them to its ``sources:``
field explicitly. A ``sources:`` value not in ``ADAPTER_NAMES`` is a
protocol error, surfaced loudly (never silently dropped).
"""
from __future__ import annotations

from .arxiv import ArxivAdapter
from .base import SourceAdapter
from .openalex import OpenAlexAdapter
from .pubmed import PubMedAdapter
from .semantic_scholar import SemanticScholarAdapter

DEFAULT_SOURCES: tuple[str, ...] = ("semantic-scholar", "arxiv", "openalex")

# Opt-in only (D4) — never in DEFAULT_SOURCES.
OPT_IN_SOURCES: tuple[str, ...] = ("pubmed",)

ADAPTER_NAMES: tuple[str, ...] = DEFAULT_SOURCES + OPT_IN_SOURCES

_REGISTRY: dict[str, type] = {
    "semantic-scholar": SemanticScholarAdapter,
    "arxiv": ArxivAdapter,
    "openalex": OpenAlexAdapter,
    "pubmed": PubMedAdapter,
}


def get_adapter(name: str) -> SourceAdapter:
    """Return a fresh adapter instance for a protocol-declared source name.

    Raises ``ValueError`` (never a silent None/skip) for an unknown source
    name — a typo'd or unsupported ``sources:`` entry must fail loud at
    protocol-freeze time, not be silently ignored by the width sweep.
    """
    try:
        cls = _REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"unknown source adapter {name!r}; known adapters: "
            f"{', '.join(sorted(_REGISTRY))}"
        ) from None
    return cls()
