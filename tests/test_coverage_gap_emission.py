"""test_coverage_gap_emission.py — gaps = RQ - coverage (0.3.2).

The lit-review loop's own MISSING second output: a facet the frozen
protocol committed to searching but whose corpus support ended up "thin"
(review.check_facet_coverage_from_search_hits' thin_poles, already
computed at review-search time) is an under-covered piece of the research
question — a first-class, typed gaps/<id>.md note, not silently absorbed
into a free-prose _coverage-gaps.md residue note.

Coverage:
  1. A thin pole emits a coverage_void gap note.
  2. No thin poles (or facet coverage never declared) → honest no-op, [].
  3. Idempotent — re-emitting does not duplicate an already-recorded gap.
  4. The gap note carries a resolvable anchor (note.check_gap_anchor is
     clean against it).
  5. GAP_TYPES / suggest_route wiring for the new type.
  6. cmd_gap_scan_coverage (the Config-resolving CLI wrapper) matches the
     path-based emit_coverage_gaps SSOT.

All tests hermetic — no ~/vault, no real cluster, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import load_config
from research_vault.note import _parse_frontmatter, check_gap_anchor
from research_vault.review.gap_scan import (
    GAP_TYPE_COVERAGE_VOID,
    GAP_TYPES,
    cmd_gap_scan_coverage,
    emit_coverage_gaps,
    suggest_route,
    ROUTE_LITERATURE,
)

_NESTED_PROTOCOL = (
    "---\n"
    "question: \"Does X drift over time?\"\n"
    "counter-position: \"X is temporally stable\"\n"
    "seed_queries:\n"
    "  by-temporal:\n"
    "    thesis:\n"
    "      - \"X drift over time\"\n"
    "    counter:\n"
    "      - \"X temporal stability\"\n"
    "---\n\n# Protocol\n"
)


def _write_search_hits(
    review_dir: Path, *, pole_counts: str, thin_poles: str, min_hits: int = 3,
) -> None:
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "_search_hits.md").write_text(
        "---\n"
        f"facet_pole_counts: {pole_counts}\n"
        f"facet_thin_poles: {thin_poles}\n"
        f"facet_min_hits_per_pole: {min_hits}\n"
        "---\n\n# Search hits\n",
        encoding="utf-8",
    )


class TestEmitCoverageGaps:
    def test_thin_pole_emits_coverage_void_gap(self, tmp_path):
        review_dir = tmp_path / "reviews" / "scope-a"
        review_dir.mkdir(parents=True)
        (review_dir / "_protocol.md").write_text(_NESTED_PROTOCOL, encoding="utf-8")
        _write_search_hits(
            review_dir,
            pole_counts="by-temporal.counter=1, by-temporal.thesis=3",
            thin_poles="by-temporal.counter",
        )

        gaps = emit_coverage_gaps(review_dir, tmp_path, scope_id="scope-a")
        assert len(gaps) == 1
        assert gaps[0].type == GAP_TYPE_COVERAGE_VOID

        gap_dir = tmp_path / "gaps"
        gap_files = list(gap_dir.glob("*.md"))
        assert len(gap_files) == 1
        text = gap_files[0].read_text()
        fields, _ = _parse_frontmatter(text)
        assert fields["gap_type"] == "coverage_void"
        assert fields["status"] == "open"
        assert "by-temporal.counter" in text

    def test_no_thin_poles_is_honest_noop(self, tmp_path):
        review_dir = tmp_path / "reviews" / "scope-b"
        review_dir.mkdir(parents=True)
        (review_dir / "_protocol.md").write_text(_NESTED_PROTOCOL, encoding="utf-8")
        _write_search_hits(
            review_dir,
            pole_counts="by-temporal.counter=5, by-temporal.thesis=3",
            thin_poles="",
        )
        gaps = emit_coverage_gaps(review_dir, tmp_path, scope_id="scope-b")
        assert gaps == []
        assert not (tmp_path / "gaps").exists() or not list((tmp_path / "gaps").glob("*.md"))

    def test_facet_coverage_never_declared_is_honest_noop(self, tmp_path):
        """A legacy/flat protocol whose sweep never stamped facet coverage
        fields at all — never fabricate a gap from absence of information."""
        review_dir = tmp_path / "reviews" / "scope-c"
        review_dir.mkdir(parents=True)
        (review_dir / "_protocol.md").write_text("---\nquestion: \"x\"\n---\n", encoding="utf-8")
        (review_dir / "_search_hits.md").write_text(
            "---\ndark_sources: \n---\n\n# Search hits\n", encoding="utf-8",
        )
        gaps = emit_coverage_gaps(review_dir, tmp_path, scope_id="scope-c")
        assert gaps == []

    def test_missing_search_hits_is_honest_noop(self, tmp_path):
        review_dir = tmp_path / "reviews" / "scope-d"
        review_dir.mkdir(parents=True)
        gaps = emit_coverage_gaps(review_dir, tmp_path, scope_id="scope-d")
        assert gaps == []

    def test_idempotent_no_duplicate_on_rescan(self, tmp_path):
        review_dir = tmp_path / "reviews" / "scope-e"
        review_dir.mkdir(parents=True)
        (review_dir / "_protocol.md").write_text(_NESTED_PROTOCOL, encoding="utf-8")
        _write_search_hits(
            review_dir,
            pole_counts="by-temporal.counter=1, by-temporal.thesis=3",
            thin_poles="by-temporal.counter",
        )
        first = emit_coverage_gaps(review_dir, tmp_path, scope_id="scope-e")
        second = emit_coverage_gaps(review_dir, tmp_path, scope_id="scope-e")
        assert len(first) == 1
        assert second == []  # already recorded — idempotent
        assert len(list((tmp_path / "gaps").glob("*.md"))) == 1

    def test_anchor_resolves_cleanly(self, tmp_path):
        """The emitted gap's anchor: field resolves against project_notes_dir
        via note.check_gap_anchor (the SAME live-anchor hygiene check every
        other gap type gets) — no vanished-anchor WARN right after emission."""
        review_dir = tmp_path / "reviews" / "scope-f"
        review_dir.mkdir(parents=True)
        (review_dir / "_protocol.md").write_text(_NESTED_PROTOCOL, encoding="utf-8")
        _write_search_hits(
            review_dir,
            pole_counts="by-temporal.counter=1, by-temporal.thesis=3",
            thin_poles="by-temporal.counter",
        )
        emit_coverage_gaps(review_dir, tmp_path, scope_id="scope-f")
        gap_path = next((tmp_path / "gaps").glob("*.md"))
        fields, _ = _parse_frontmatter(gap_path.read_text())
        warnings = check_gap_anchor(gap_path, fields, tmp_path)
        assert warnings == [], f"Unexpected vanished-anchor warning: {warnings}"

    def test_multiple_thin_poles_each_get_own_gap(self, tmp_path):
        review_dir = tmp_path / "reviews" / "scope-g"
        review_dir.mkdir(parents=True)
        (review_dir / "_protocol.md").write_text(_NESTED_PROTOCOL, encoding="utf-8")
        _write_search_hits(
            review_dir,
            pole_counts="by-temporal.counter=0, by-temporal.thesis=1",
            thin_poles="by-temporal.counter, by-temporal.thesis",
        )
        gaps = emit_coverage_gaps(review_dir, tmp_path, scope_id="scope-g")
        assert len(gaps) == 2
        assert len(list((tmp_path / "gaps").glob("*.md"))) == 2


class TestGapTypeWiring:
    def test_coverage_void_in_gap_types(self):
        assert GAP_TYPE_COVERAGE_VOID in GAP_TYPES

    def test_suggest_route_coverage_void(self):
        assert suggest_route(GAP_TYPE_COVERAGE_VOID, {}) == ROUTE_LITERATURE


class TestCmdGapScanCoverageWrapper:
    def test_matches_path_based_emit(self, tmp_instance):
        cfg = load_config()
        pnd = cfg.project_notes_dir("demo-research")
        review_dir = pnd / "reviews" / "scope-h"
        review_dir.mkdir(parents=True)
        (review_dir / "_protocol.md").write_text(_NESTED_PROTOCOL, encoding="utf-8")
        _write_search_hits(
            review_dir,
            pole_counts="by-temporal.counter=1, by-temporal.thesis=3",
            thin_poles="by-temporal.counter",
        )
        gaps = cmd_gap_scan_coverage("demo-research", "scope-h", config=cfg)
        assert len(gaps) == 1
        assert (pnd / "gaps").exists()
