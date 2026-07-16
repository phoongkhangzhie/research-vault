# SPDX-License-Identifier: AGPL-3.0-or-later
"""sources/derivative.py — ``derivative-of`` overlap discounting
("recommended", promoted from optional).

>60%-shared-quoted-support / citation-ancestry discounting on the snowball
frontier: DISCOUNT, never delete. A near-duplicate restatement of an already-
seen paper stays in the corpus (provenance preserved) but is excluded from the
INDEPENDENT count the saturation stopping rule reads — this is the direct fix
for a project's exploding-intersection frontier (an intersection of many near-
duplicate restatements looked like fresh evidence and kept the 2-consecutive-
zero rule from ever firing).

Overlap proxy (no full-text access at fetch time): normalized-abstract-token
Jaccard similarity against every OTHER hit already accepted into the corpus.
A hit whose overlap with any single prior hit is > 0.6 is flagged
``derivative_of = <that hit's identity>``.
"""
from __future__ import annotations

import re

from .base import PaperHit

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _hit_key(hit: PaperHit) -> str:
    return hit.external_ids.get("doi") or hit.external_ids.get("arxiv") or hit.title


def mark_derivatives(hits: list[PaperHit], *, threshold: float = 0.6) -> list[PaperHit]:
    """Stamp ``hit.derivative_of`` on any hit whose abstract overlaps > threshold
    with an EARLIER hit in the list (order = discovery order; the earlier hit
    is treated as the "original", later near-duplicates as derivatives).

    Mutates and returns the same list (in place) — hits are never removed.
    """
    accepted: list[tuple[str, set[str]]] = []  # (key, tokens) of non-derivative hits so far
    for hit in hits:
        tok = _tokens(hit.abstract or hit.title)
        derivative_of = None
        for key, prior_tok in accepted:
            if _jaccard(tok, prior_tok) > threshold:
                derivative_of = key
                break
        hit.derivative_of = derivative_of
        if derivative_of is None:
            accepted.append((_hit_key(hit), tok))
    return hits


def count_independent(hits: list[PaperHit]) -> int:
    """Return the count of hits NOT flagged as a derivative — the number the
    saturation stopping rule should read (discount, don't delete: the full
    ``hits`` list is unchanged length; this is the reader for the count that
    matters)."""
    return sum(1 for h in hits if h.derivative_of is None)
