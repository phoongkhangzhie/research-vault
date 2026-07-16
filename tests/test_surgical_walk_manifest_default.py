# SPDX-License-Identifier: AGPL-3.0-or-later
"""test_surgical_walk_manifest_default.py — search-primary redesign,
Section D part 2 (the batch closer): the blanket 1-hop citation-neighbor
walk is REMOVED as the default; two explicit, named surgical modes
(``run_thin_pole_fill``/``run_named_anchor_chase``) replace it; the shipped
Phase-1 manifest's ``review-snowball`` node no longer runs a walk by
default.

TDD pins (per the D-2 build brief):
  (a) no trigger -> no walk runs, no ``_walk.md``, the tool node doesn't
      error on the absent (optional) ``produces:`` artifact.
  (b) a thin-pole trigger -> surgical walk from the pole's seeds ONLY, not
      the review's full accepted frontier.
  (c) a named-anchor trigger -> chase ONLY the resolved-id anchors.
  (d) a surgical chase writes a valid ``_walk.md`` with a whitelisted
      ``stop_reason`` that ``classify_coverage_gate`` (D-1) accepts as GO.
  (e) the manifest default no longer blanket-walks.

Mirrors ``test_coverage_gate_walk_absent.py``'s framing (reused, not
reinvented — charter §6) and hands off to it for the coverage-gate's OWN
walk-absent disposition contract (unchanged here, D-1's job).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import load_config  # noqa: E402
from research_vault.review import autonomy as auto  # noqa: E402
from research_vault.sources.base import PaperHit  # noqa: E402


@pytest.fixture
def cfg(tmp_instance):
    return load_config(reload=True)


def _hit(title: str, doi: str) -> PaperHit:
    return PaperHit(
        title=title, year=2024, authors=["A. Author"], external_ids={"doi": doi},
        abstract=title, citation_count=0, source="semantic-scholar",
    )


def _search_hits_md(hits: list[tuple[str, str]]) -> str:
    """Build a minimal ``_search_hits.md``-shaped table (paper_id, title)."""
    lines = [
        "---\n---\n\n# Search hits\n",
        "| Annotation | Paper-id | Title | Venue | Year | Abstract/TL;DR | Flags | Rerank | Poles |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for pid, title in hits:
        lines.append(f"| [NEW] | {pid} | {title} |  | 2024 | some abstract text |  |  |  |")
    return "\n".join(lines) + "\n"


class TestPinA_NoTriggerNoWalk:
    """(a) no trigger -> no walk runs, no _walk.md, node doesn't error on
    the absent produces artifact (the new default)."""

    def test_run_walk_false_writes_no_walk_md(self, tmp_path):
        out_dir = tmp_path / "review"
        out_dir.mkdir()
        (out_dir / "_search_hits.md").write_text(
            _search_hits_md([("10.1/seed", "A Seed Paper")]), encoding="utf-8",
        )
        result = auto.run_tool_op(
            "snowball",
            seed_ids=["10.1/seed"], out_dir=str(out_dir),
            search_hits=str(out_dir / "_search_hits.md"),
            run_walk=False,
        )
        assert result["walk_ran"] is False
        assert result["walk"] is None
        assert result["stop_reason"] == ""
        assert not (out_dir / "_walk.md").exists()
        assert (out_dir / "_corpus_raw.md").exists()

    def test_run_walk_false_never_calls_the_citation_graph(self, tmp_path, monkeypatch):
        """RED-before-GREEN proof: patch run_citation_neighbor_walk to
        raise if ever called — run_walk=False must never reach it."""
        def _boom(*a, **kw):
            raise AssertionError("run_citation_neighbor_walk called despite run_walk=False")

        monkeypatch.setattr(
            "research_vault.sources.snowball.run_citation_neighbor_walk", _boom,
        )
        out_dir = tmp_path / "review"
        out_dir.mkdir()
        result = auto.run_tool_op(
            "snowball", seed_ids=["10.1/seed"], out_dir=str(out_dir), run_walk=False,
        )
        assert result["walk_ran"] is False

    def test_missing_produces_artifacts_exempts_optional_key(self):
        """the tool-node auto-executor's fail-closed check
        (dag/verbs.py::_missing_produces_artifacts) must NOT flag an
        absent-but-declared-optional _walk.md."""
        from research_vault.dag.verbs import _missing_produces_artifacts

        node = {
            "produces": {
                "_corpus_raw.md": "/does/not/exist/_corpus_raw.md",
                "_walk.md": "/does/not/exist/_walk.md",
            },
            "produces_optional": ["_walk.md"],
        }
        missing = _missing_produces_artifacts(node)
        # _corpus_raw.md is NOT optional and genuinely missing here — still
        # correctly flagged (the exemption is narrow, not a blanket bypass).
        assert any("_corpus_raw.md" in m for m in missing)
        assert not any("_walk.md" in m for m in missing)

    def test_mutation_without_produces_optional_the_old_block_fires(self):
        """Mutation test (proves the fix has real teeth): the SAME node
        WITHOUT ``produces_optional`` — simulating the pre-fix manifest —
        DOES flag the absent _walk.md. This is the exact block the D-2
        fix removes for the legitimate no-walk-ran case."""
        from research_vault.dag.verbs import _missing_produces_artifacts

        node = {
            "produces": {
                "_corpus_raw.md": "/does/not/exist/_corpus_raw.md",
                "_walk.md": "/does/not/exist/_walk.md",
            },
            # no produces_optional — old (pre-D-2) shape
        }
        missing = _missing_produces_artifacts(node)
        assert any("_walk.md" in m for m in missing), (
            "mutation guard: without produces_optional, an absent _walk.md "
            "must still block — proving produces_optional (not some other "
            "change) is what makes the D-2 default no-walk path safe"
        )


class TestPinB_ThinPoleFill:
    """(b) a thin-pole trigger -> surgical walk from the pole's seeds ONLY,
    not the review's full accepted frontier."""

    def test_walks_only_the_pole_seed_ids(self, tmp_path, monkeypatch):
        captured = {}
        from research_vault.sources.snowball import SnowballResult

        def fake_run(seed_ids, **kwargs):
            captured["seed_ids"] = list(seed_ids)
            return SnowballResult(kept=[], rounds=[], stop_reason="walk-complete:1-hops", seed_count=len(seed_ids))

        monkeypatch.setattr(
            "research_vault.sources.snowball.run_citation_neighbor_walk", fake_run,
        )
        out_dir = tmp_path / "review"
        out_dir.mkdir()

        result = auto.run_thin_pole_fill(
            pole_seed_ids=["10.1/pole-a", "10.1/pole-b"], out_dir=str(out_dir),
        )
        # ONLY the pole's own seeds were walked — never a broader frontier.
        assert captured["seed_ids"] == ["10.1/pole-a", "10.1/pole-b"]
        assert result["stop_reason"] == "walk-complete:1-hops"

    def test_never_widens_beyond_the_named_pole_seeds(self, tmp_path, monkeypatch):
        """A full accepted-seed frontier (bigger than the pole's own seeds)
        must NEVER leak into the walk when only a subset is named."""
        from research_vault.sources.snowball import SnowballResult

        captured = {}

        def fake_run(seed_ids, **kwargs):
            captured["seed_ids"] = list(seed_ids)
            return SnowballResult(kept=[], rounds=[], stop_reason="walk-complete:1-hops", seed_count=len(seed_ids))

        monkeypatch.setattr(
            "research_vault.sources.snowball.run_citation_neighbor_walk", fake_run,
        )
        out_dir = tmp_path / "review"
        out_dir.mkdir()

        full_frontier = ["10.1/a", "10.1/b", "10.1/c", "10.1/pole-only"]
        auto.run_thin_pole_fill(pole_seed_ids=["10.1/pole-only"], out_dir=str(out_dir))
        assert captured["seed_ids"] == ["10.1/pole-only"]
        assert set(captured["seed_ids"]) != set(full_frontier)

    def test_empty_pole_seeds_raises_never_a_silent_noop(self, tmp_path):
        with pytest.raises(ValueError, match="non-empty"):
            auto.run_thin_pole_fill(pole_seed_ids=[], out_dir=str(tmp_path))


class TestPinC_NamedAnchorChase:
    """(c) a named-anchor trigger -> chase ONLY the resolved-id anchors."""

    def test_walks_only_the_anchor_ids(self, tmp_path, monkeypatch):
        from research_vault.sources.snowball import SnowballResult

        captured = {}

        def fake_run(seed_ids, **kwargs):
            captured["seed_ids"] = list(seed_ids)
            return SnowballResult(kept=[], rounds=[], stop_reason="walk-complete:1-hops", seed_count=len(seed_ids))

        monkeypatch.setattr(
            "research_vault.sources.snowball.run_citation_neighbor_walk", fake_run,
        )
        out_dir = tmp_path / "review"
        out_dir.mkdir()

        result = auto.run_named_anchor_chase(
            anchor_ids=["10.1234/herrmann2008"], out_dir=str(out_dir),
        )
        assert captured["seed_ids"] == ["10.1234/herrmann2008"]
        assert result["stop_reason"] == "walk-complete:1-hops"

    def test_empty_anchor_ids_raises_never_a_silent_noop(self, tmp_path):
        with pytest.raises(ValueError, match="non-empty"):
            auto.run_named_anchor_chase(anchor_ids=[], out_dir=str(tmp_path))

    def test_thin_pole_fill_and_named_anchor_chase_are_distinctly_tagged(self, tmp_path, monkeypatch):
        """Provenance never collapses the two modes into one indistinct
        signal — each stamps its own walk_trigger."""
        from research_vault.sources.snowball import SnowballResult

        def fake_run(seed_ids, **kwargs):
            return SnowballResult(kept=[], rounds=[], stop_reason="walk-complete:1-hops", seed_count=len(seed_ids))

        monkeypatch.setattr(
            "research_vault.sources.snowball.run_citation_neighbor_walk", fake_run,
        )
        pole_dir = tmp_path / "pole"
        pole_dir.mkdir()
        anchor_dir = tmp_path / "anchor"
        anchor_dir.mkdir()

        auto.run_thin_pole_fill(pole_seed_ids=["10.1/x"], out_dir=str(pole_dir))
        auto.run_named_anchor_chase(anchor_ids=["10.1/y"], out_dir=str(anchor_dir))

        assert "walk_trigger: thin-pole-fill" in (pole_dir / "_walk.md").read_text(encoding="utf-8")
        assert "walk_trigger: named-anchor-chase" in (anchor_dir / "_walk.md").read_text(encoding="utf-8")


class TestPinD_SurgicalChaseWritesGateValidWalk:
    """(d) a surgical chase writes a valid _walk.md with a whitelisted
    stop_reason that classify_coverage_gate (D-1) accepts."""

    def test_thin_pole_fill_walk_complete_is_accepted_go(self, tmp_path, monkeypatch):
        from research_vault.sources.snowball import SnowballResult
        from research_vault.review import check_walk_terminal
        from research_vault.review.autonomy import classify_coverage_gate, GO

        def fake_run(seed_ids, **kwargs):
            return SnowballResult(kept=[], rounds=[], stop_reason="walk-complete:1-hops", seed_count=len(seed_ids))

        monkeypatch.setattr(
            "research_vault.sources.snowball.run_citation_neighbor_walk", fake_run,
        )
        out_dir = tmp_path / "review"
        out_dir.mkdir()

        auto.run_thin_pole_fill(pole_seed_ids=["10.1/pole"], out_dir=str(out_dir))
        walk_info = check_walk_terminal(out_dir / "_walk.md")
        assert walk_info["exists"] is True
        assert walk_info["stop_reason"] == "walk-complete:1-hops"

        disposition = classify_coverage_gate(walk_info)
        assert disposition.disposition == GO

    def test_named_anchor_chase_neighborhood_exhausted_is_accepted_go(self, tmp_path, monkeypatch):
        from research_vault.sources.snowball import SnowballResult
        from research_vault.review import check_walk_terminal
        from research_vault.review.autonomy import classify_coverage_gate, GO

        def fake_run(seed_ids, **kwargs):
            return SnowballResult(kept=[], rounds=[], stop_reason="neighborhood-exhausted", seed_count=len(seed_ids))

        monkeypatch.setattr(
            "research_vault.sources.snowball.run_citation_neighbor_walk", fake_run,
        )
        out_dir = tmp_path / "review"
        out_dir.mkdir()

        auto.run_named_anchor_chase(anchor_ids=["10.1/anchor"], out_dir=str(out_dir))
        walk_info = check_walk_terminal(out_dir / "_walk.md")
        disposition = classify_coverage_gate(walk_info)
        assert disposition.disposition == GO


class TestPinE_ManifestDefaultNoLongerBlanketWalks:
    """(e) the manifest default no longer blanket-walks."""

    def test_review_snowball_node_run_walk_false(self, cfg, tmp_instance):
        from research_vault.review import cmd_new

        _, review_dir, phase1 = cmd_new(
            "demo-research", "scope-surgical-default", question="Q?", config=cfg,
        )
        node = next(n for n in phase1["nodes"] if n["id"] == "review-snowball")
        assert node["args"]["run_walk"] is False

    def test_review_snowball_node_declares_walk_md_as_optional_produces(self, cfg, tmp_instance):
        from research_vault.review import cmd_new

        _, review_dir, phase1 = cmd_new(
            "demo-research", "scope-surgical-default-2", question="Q?", config=cfg,
        )
        node = next(n for n in phase1["nodes"] if n["id"] == "review-snowball")
        # _walk.md stays a declared produces: KEY (coverage-gate's wiring
        # resolves review_dir from it) but is exempt from the "must exist"
        # enforcement.
        assert "_walk.md" in node["produces"]
        assert "_walk.md" in node.get("produces_optional", [])

    def test_review_snowball_node_still_declares_corpus_raw_required(self, cfg, tmp_instance):
        """The non-optional artifact (_corpus_raw.md, now always produced
        via the seed-row merge) must still be REQUIRED — the exemption is
        narrow, not a blanket bypass of the produces: enforcement."""
        from research_vault.review import cmd_new

        _, review_dir, phase1 = cmd_new(
            "demo-research", "scope-surgical-default-3", question="Q?", config=cfg,
        )
        node = next(n for n in phase1["nodes"] if n["id"] == "review-snowball")
        assert "_corpus_raw.md" not in node.get("produces_optional", [])

    def test_review_snowball_node_args_carry_search_hits_for_seed_merge(self, cfg, tmp_instance):
        from research_vault.review import cmd_new

        _, review_dir, phase1 = cmd_new(
            "demo-research", "scope-surgical-default-4", question="Q?", config=cfg,
        )
        node = next(n for n in phase1["nodes"] if n["id"] == "review-snowball")
        assert node["args"]["search_hits"] == str(review_dir / "_search_hits.md")


class TestSeedRowMerge:
    """The corpus_raw.md is non-empty by default: the search-accepted seed
    frontier's own rows are carried through — the substrate that makes
    Pin (a)/(e) safe (a default no-walk corpus is NOT an empty corpus)."""

    def test_build_seed_rows_from_search_hits_matches_by_id(self, tmp_path):
        from research_vault.sources.snowball import build_seed_rows_from_search_hits

        search_hits_path = tmp_path / "_search_hits.md"
        search_hits_path.write_text(
            _search_hits_md([
                ("10.1/a", "Paper A"), ("10.1/b", "Paper B"), ("10.1/c", "Paper C"),
            ]),
            encoding="utf-8",
        )
        matched, unmatched = build_seed_rows_from_search_hits(
            search_hits_path, ["10.1/a", "10.1/c", "10.1/nonexistent"],
        )
        assert [r["paper_id"] for r in matched] == ["10.1/a", "10.1/c"]
        assert [r["title"] for r in matched] == ["Paper A", "Paper C"]
        assert unmatched == ["10.1/nonexistent"]

    def test_build_seed_rows_no_search_hits_file_all_unmatched(self, tmp_path):
        from research_vault.sources.snowball import build_seed_rows_from_search_hits

        matched, unmatched = build_seed_rows_from_search_hits(
            tmp_path / "_search_hits.md", ["10.1/a"],
        )
        assert matched == []
        assert unmatched == ["10.1/a"]

    def test_write_corpus_raw_renders_seed_rows_and_skips_duplicate_walk_hit(self, tmp_path):
        from research_vault.sources.snowball import (
            SnowballResult, write_corpus_raw,
        )
        from research_vault.sources.dedup import DedupedHit

        seed_rows = [
            {"annotation": "[NEW]", "paper_id": "10.1/dup", "title": "Duplicate Seed Paper",
             "venue": "", "year": "2024", "abstract": "abs", "flags": "", "rerank": "", "poles": ""},
        ]
        # The walk ALSO rediscovers the same paper as a citation neighbor —
        # must not appear twice.
        dup_hit = _hit("Duplicate Seed Paper (walk copy)", "10.1/dup")
        fresh_hit = _hit("A Genuinely New Walk Discovery", "10.1/fresh")
        result = SnowballResult(
            kept=[DedupedHit(hit=dup_hit, external_ids={"doi": "10.1/dup"}),
                  DedupedHit(hit=fresh_hit, external_ids={"doi": "10.1/fresh"})],
            rounds=[], stop_reason="walk-complete:1-hops", seed_count=1,
        )
        out_path = write_corpus_raw(
            result, tmp_path / "_corpus_raw.md", seed_rows=seed_rows,
        )
        text = out_path.read_text(encoding="utf-8")
        assert "Duplicate Seed Paper" in text
        assert "Duplicate Seed Paper (walk copy)" not in text  # skipped, seed row wins
        assert "A Genuinely New Walk Discovery" in text
        assert "seed_rows_merged: 1" in text

    def test_write_corpus_raw_unmatched_seed_gets_flagged_fallback_row(self, tmp_path):
        from research_vault.sources.snowball import SnowballResult, write_corpus_raw

        result = SnowballResult(kept=[], rounds=[], stop_reason="", seed_count=1)
        out_path = write_corpus_raw(
            result, tmp_path / "_corpus_raw.md",
            unmatched_seed_ids=["10.1/no-search-hits-row"],
        )
        text = out_path.read_text(encoding="utf-8")
        assert "10.1/no-search-hits-row" in text
        assert "[SEED-METADATA-UNMATCHED" in text
        assert "seed_rows_unmatched: 1" in text
