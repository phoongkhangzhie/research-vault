# SPDX-License-Identifier: AGPL-3.0-or-later
"""sources/dedup.py — cross-source identity collapse (NG-2, §4.3).

Multi-source hits collapse on a normalized identity, priority order
DOI > arXiv > OpenAlex > normalized-title (§4.3). The union of each group's
``external_ids`` is kept, and ``sources`` records every adapter name that
independently surfaced the paper — the raw signal NG-3's utility-ranker
"coverage" dim and NG-9's derivative-of discounting both consume.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .base import PaperHit

_NORM_RE = re.compile(r"[^a-z0-9]")


def _norm_title(title: str) -> str:
    return _NORM_RE.sub("", (title or "").lower())


def identity_key(hit: PaperHit) -> str:
    """Return the normalized-identity key a hit collapses on.

    Priority: DOI > arXiv > OpenAlex > normalized-title. A hit with none of
    the first three ids falls back to title — the weakest signal, but still
    deterministic (never a random/None key that would defeat dedup silently).
    """
    doi = hit.external_ids.get("doi")
    if doi:
        return f"doi:{doi.strip().lower()}"
    arxiv = hit.external_ids.get("arxiv")
    if arxiv:
        return f"arxiv:{re.sub(r'v[0-9]+$', '', arxiv.strip().lower())}"
    openalex = hit.external_ids.get("openalex")
    if openalex:
        return f"openalex:{openalex.strip().lower()}"
    return f"title:{_norm_title(hit.title)}"


@dataclass
class DedupedHit:
    """A PaperHit after cross-source collapse: the union of external_ids and
    the set of sources that independently surfaced it."""

    hit: PaperHit
    sources: set[str] = field(default_factory=set)
    external_ids: dict[str, str] = field(default_factory=dict)

    @property
    def source_count(self) -> int:
        return len(self.sources)


def dedup_hits(hits: list[PaperHit]) -> list[DedupedHit]:
    """Collapse multi-source hits on normalized identity.

    Order-preserving: the FIRST hit seen for a given identity key seeds the
    representative ``hit`` (its title/abstract/authors are kept as-is); later
    duplicates only contribute their ``source`` + ``external_ids`` to the
    union. This keeps the richest-first behavior deterministic (adapters are
    queried in a fixed order by the sweep orchestrator).
    """
    by_key: dict[str, DedupedHit] = {}
    order: list[str] = []
    for h in hits:
        key = identity_key(h)
        if key not in by_key:
            by_key[key] = DedupedHit(hit=h, sources={h.source}, external_ids=dict(h.external_ids))
            order.append(key)
        else:
            d = by_key[key]
            d.sources.add(h.source)
            for k, v in h.external_ids.items():
                d.external_ids.setdefault(k, v)
    return [by_key[k] for k in order]
