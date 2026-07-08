"""test_sources_ranker.py — NG-3 6-dim utility score + the saturation-paired floor.

★ Load-bearing test: rank_and_select must NEVER cap a below-floor (boundary)
item out of the kept set, even when the budget is exhausted by above-floor
items (Ada's guard, §7.2).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.sources.base import PaperHit
from research_vault.sources.dedup import DedupedHit
from research_vault.sources.ranker import rank_and_select, score_hit


def _deduped(source_count: int, citation_count: int = 0, title: str = "P") -> DedupedHit:
    hit = PaperHit(
        title=title, year=2020, authors=["A"], external_ids={},
        abstract="", citation_count=citation_count, source="semantic-scholar",
    )
    return DedupedHit(hit=hit, sources={f"src{i}" for i in range(source_count)})


def test_score_hit_all_dims_bounded_0_to_3() -> None:
    d = _deduped(source_count=5, citation_count=1000)
    score = score_hit(d, angle_hit_count=10, angle_category_count=10, current_year=2026)
    for dim in (score.authority, score.novelty, score.stance_diversity,
                score.coverage, score.redundancy, score.freshness):
        assert 0 <= dim <= 3
    assert 0 <= score.total <= 18


def test_score_hit_derivative_gets_zero_redundancy() -> None:
    d = _deduped(source_count=2)
    score = score_hit(d, is_derivative=True)
    assert score.redundancy == 0
    score2 = score_hit(d, is_derivative=False)
    assert score2.redundancy == 3


def test_rank_and_select_orders_by_total_score() -> None:
    strong = _deduped(source_count=3, citation_count=500, title="strong")
    weak = _deduped(source_count=3, citation_count=0, title="weak")
    kept = rank_and_select([weak, strong], budget=10, floor=3)
    assert kept[0].hit.title == "strong"


def test_rank_and_select_never_caps_a_boundary_item_below_floor() -> None:
    """★ The core guard: a below-floor (boundary) item must survive selection
    even when the budget is fully consumed by above-floor items."""
    # 5 well-covered "core" items (source_count=3, meets floor) — budget=2
    # deliberately smaller than the core set so trimming actually happens.
    core_items = [_deduped(source_count=3, citation_count=100, title=f"core{i}") for i in range(5)]
    # 1 boundary item: only 1 independent source (below floor=3).
    boundary = _deduped(source_count=1, citation_count=0, title="boundary")

    kept = rank_and_select(core_items + [boundary], budget=2, floor=3)

    kept_titles = {d.hit.title for d in kept}
    assert "boundary" in kept_titles, (
        "a below-floor boundary item must NEVER be capped out by budget — "
        "it is the exact failure mode Ada flagged (§7.2)"
    )
    assert len(kept) == 2 + 1  # budget=2 above-floor + the 1 unconditional boundary item


def test_rank_and_select_stamps_below_floor_flag() -> None:
    boundary = _deduped(source_count=1)
    core = _deduped(source_count=3)
    rank_and_select([boundary, core], budget=10, floor=3)
    assert boundary.hit.below_floor is True
    assert core.hit.below_floor is False


def test_rank_and_select_above_floor_items_ARE_trimmed_by_budget() -> None:
    """The floor guard is narrow: it protects boundary items, it does not
    disable budgeting for well-covered items."""
    core_items = [_deduped(source_count=3, citation_count=i, title=f"core{i}") for i in range(10)]
    kept = rank_and_select(core_items, budget=3, floor=3)
    assert len(kept) == 3
