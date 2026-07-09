# SPDX-License-Identifier: AGPL-3.0-or-later
"""sources/ranker.py — the 6-dim utility score + the saturation-paired floor
(NG-3, HR-craft rec 2, §7.2).

HR's Authority/Novelty/Stance-diversity/Coverage/Redundancy/Freshness rubric
(0-3 each) turns "core vs boundary" into a number the width-sweep can budget
against. ★ The reviewer's guard, load-bearing: the ``floor`` (every atomic item needs
≥3 independent sources) must NOT silently cap a boundary item out of the kept
set — a below-floor item is a signal for the depth snowball to keep chasing,
never a reason to drop it. ``rank_and_select`` enforces this: budget governs
ranking/ordering among ABOVE-floor items; a below-floor item is retained
unconditionally and flagged ``below_floor=True`` so downstream saturation
logic (and any residue report) sees it.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass

from .dedup import DedupedHit


@dataclass(frozen=True)
class UtilityScore:
    authority: int          # citation-count bucket, 0-3
    novelty: int            # distinct angle-queries that surfaced it, 0-3
    stance_diversity: int   # distinct angle CATEGORIES that surfaced it, 0-3
    coverage: int           # distinct independent sources (adapters), 0-3
    redundancy: int         # inverse of derivative-cluster size, 0-3 (3 = fully independent)
    freshness: int          # recency bucket, 0-3

    @property
    def total(self) -> int:
        return (
            self.authority + self.novelty + self.stance_diversity
            + self.coverage + self.redundancy + self.freshness
        )


def _bucket3(value: float, thresholds: tuple[float, float, float]) -> int:
    """Map a value to a 0-3 bucket given three ascending thresholds."""
    lo, mid, hi = thresholds
    if value >= hi:
        return 3
    if value >= mid:
        return 2
    if value >= lo:
        return 1
    return 0


def score_hit(
    deduped: DedupedHit,
    *,
    angle_hit_count: int = 1,
    angle_category_count: int = 1,
    is_derivative: bool = False,
    current_year: int | None = None,
) -> UtilityScore:
    """Score one deduped hit against the 6-dim rubric.

    ``angle_hit_count``  — how many distinct angle QUERIES (out of the
                            frozen angle matrix) surfaced this paper.
    ``angle_category_count`` — how many distinct angle CATEGORIES
                            (by-method/by-outcome/by-paradigm/by-population)
                            surfaced it — the stance/framing-diversity proxy.
    ``is_derivative``    — NG-9's derivative-of flag; discounts redundancy.
    """
    hit = deduped.hit
    year = current_year or datetime.date.today().year

    authority = _bucket3(hit.citation_count, (1, 10, 100))
    novelty = _bucket3(angle_hit_count, (1, 2, 3))
    stance_diversity = _bucket3(angle_category_count, (1, 2, 3))
    coverage = _bucket3(deduped.source_count, (1, 2, 3))

    if hit.year is None:
        freshness = 0
    else:
        age = max(0, year - hit.year)
        freshness = _bucket3(-age, (-10, -5, -2))  # newer -> higher bucket

    redundancy = 0 if is_derivative else 3

    return UtilityScore(
        authority=authority,
        novelty=novelty,
        stance_diversity=stance_diversity,
        coverage=coverage,
        redundancy=redundancy,
        freshness=freshness,
    )


def rank_and_select(
    deduped_hits: list[DedupedHit],
    *,
    budget: int,
    floor: int = 3,
    scores: dict[int, UtilityScore] | None = None,
) -> list[DedupedHit]:
    """Rank deduped hits by utility and select under ``budget``.

    ★ The floor never caps a boundary item (the reviewer's guard, §7.2): any hit whose
    ``source_count < floor`` is stamped ``below_floor=True`` on its
    ``PaperHit`` and is ALWAYS included in the return, regardless of budget.
    Budget governs ordering/inclusion only among hits that already meet the
    floor — so a genuinely well-covered surplus is what gets trimmed, never a
    thin boundary item.

    ``scores`` (keyed by ``id(deduped)``) lets a caller pre-compute scores
    (e.g. with real angle-hit-count context); defaults to a flat score_hit()
    call per item with angle_hit_count=1.
    """
    scores = scores or {}

    def _score(d: DedupedHit) -> UtilityScore:
        return scores.get(id(d)) or score_hit(d)

    for d in deduped_hits:
        d.hit.below_floor = d.source_count < floor

    ranked = sorted(deduped_hits, key=lambda d: _score(d).total, reverse=True)

    above_floor = [d for d in ranked if not d.hit.below_floor]
    below_floor = [d for d in ranked if d.hit.below_floor]

    kept_above = above_floor[:budget]
    # below-floor items are NEVER excluded (the guard) — always appended.
    return kept_above + below_floor
