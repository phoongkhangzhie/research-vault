"""test_sources_derivative.py — NG-9 derivative-of >60%-overlap discounting.

★ Gate requirement: plant a real derivative near-duplicate, confirm it is
DISCOUNTED (flagged, excluded from the independent count) — never deleted
from the hit list (provenance preserved, §7.3).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.sources.base import PaperHit
from research_vault.sources.derivative import count_independent, mark_derivatives


def _hit(title, abstract, **kw) -> PaperHit:
    defaults = dict(year=2020, authors=["A"], external_ids={}, citation_count=0, source="semantic-scholar")
    defaults.update(kw)
    return PaperHit(title=title, abstract=abstract, **defaults)


ORIGINAL_ABSTRACT = (
    "We propose a new simple network architecture the Transformer based "
    "solely on attention mechanisms dispensing with recurrence and "
    "convolutions entirely experiments on two machine translation tasks"
)

# A near-duplicate restatement — a preprint-vs-camera-ready pair, or a
# citation-ancestry derivative that quotes most of the same passages.
NEAR_DUPLICATE_ABSTRACT = (
    "We propose a new simple network architecture the Transformer based "
    "solely on attention mechanisms dispensing with recurrence and "
    "convolutions entirely. Experiments on two machine translation tasks "
    "show these models are superior in quality."
)

INDEPENDENT_ABSTRACT = (
    "This paper surveys reinforcement learning algorithms for continuous "
    "control tasks in robotics, comparing sample efficiency across methods."
)


def test_planted_derivative_is_discounted_not_deleted() -> None:
    original = _hit("Attention Is All You Need (arXiv preprint)", ORIGINAL_ABSTRACT)
    derivative = _hit("Attention Is All You Need (camera-ready)", NEAR_DUPLICATE_ABSTRACT)
    independent = _hit("Continuous Control Survey", INDEPENDENT_ABSTRACT)

    hits = [original, derivative, independent]
    mark_derivatives(hits, threshold=0.6)

    # DISCOUNTED: flagged, not counted as independent.
    assert derivative.derivative_of is not None
    # NOT DELETED: still present in the list (provenance preserved).
    assert derivative in hits
    assert len(hits) == 3

    # Independent items are unflagged.
    assert original.derivative_of is None
    assert independent.derivative_of is None

    # Saturation must read the DISCOUNTED count, not the raw length.
    assert count_independent(hits) == 2
    assert len(hits) == 3  # discount, not delete


def test_below_threshold_overlap_is_not_flagged() -> None:
    a = _hit("Paper A", ORIGINAL_ABSTRACT)
    b = _hit("Paper B", INDEPENDENT_ABSTRACT)
    mark_derivatives([a, b], threshold=0.6)
    assert a.derivative_of is None
    assert b.derivative_of is None


def test_derivative_of_points_at_the_original_hits_key() -> None:
    original = _hit(
        "Attention Is All You Need", ORIGINAL_ABSTRACT,
        external_ids={"doi": "10.1/orig"},
    )
    derivative = _hit("Attention Is All You Need v2", NEAR_DUPLICATE_ABSTRACT)
    mark_derivatives([original, derivative], threshold=0.6)
    assert derivative.derivative_of == "10.1/orig"
