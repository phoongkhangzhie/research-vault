"""tests/test_pr3_incremental_relate.py — PR-3 D-5b: concept-graph-blocked
incremental relate (sub-quadratic candidate generation, bidirectional edge
write, island escalation).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.review import incremental_relate as ir  # noqa: E402
from research_vault.review.relate_check import parse_paper_relations  # noqa: E402


def _write_note(literature_dir: Path, citekey: str, *, concepts: list[str]) -> Path:
    literature_dir.mkdir(parents=True, exist_ok=True)
    path = literature_dir / f"{citekey}.md"
    edges = "\n".join(
        f"- [{c}](/concepts/{c}.md) — SUPPORTS: this paper touches {c}"
        for c in concepts
    )
    text = (
        "---\n"
        f"citekey: {citekey}\n"
        "---\n\n"
        "## Concept edges\n"
        f"{edges}\n"
    )
    path.write_text(text, encoding="utf-8")
    return path


class TestNoteConcepts:
    def test_reads_concept_edges(self, tmp_path):
        p = _write_note(tmp_path, "alpha2024", concepts=["persona-drift", "temporal-stability"])
        assert ir.note_concepts(p) == {"persona-drift", "temporal-stability"}

    def test_absent_note_returns_empty_set(self, tmp_path):
        assert ir.note_concepts(tmp_path / "nope.md") == set()

    def test_no_concept_edges_returns_empty_set(self, tmp_path):
        p = _write_note(tmp_path, "beta2024", concepts=[])
        assert ir.note_concepts(p) == set()


class TestConceptGraphBlockingSubQuadratic:
    def _big_corpus(self, tmp_path, *, n_concepts=20, per_concept=10):
        """200 baseline papers spread across 20 concepts, 10 each — no
        paper shares a concept with more than 9 others."""
        literature_dir = tmp_path / "literature"
        citekeys = set()
        for ci in range(n_concepts):
            concept = f"concept-{ci}"
            for pi in range(per_concept):
                ck = f"paper-{ci}-{pi}"
                _write_note(literature_dir, ck, concepts=[concept])
                citekeys.add(ck)
        return literature_dir, citekeys

    def test_candidate_pairs_track_neighborhood_not_corpus_size(self, tmp_path):
        literature_dir, baseline = self._big_corpus(tmp_path)
        corpus_size = len(baseline)
        assert corpus_size == 200

        new_ck = "newpaper2026"
        _write_note(literature_dir, new_ck, concepts=["concept-0"])  # shares 1 concept -> 10 candidates

        def relate_fn(a, b):
            return None  # no relation needed for this instrumentation test

        result = ir.run_incremental_relate(
            [new_ck], literature_dir=literature_dir, baseline_citekeys=baseline,
            relate_fn=relate_fn,
        )
        assert result.corpus_size == 200
        # ★ THE SUB-QUADRATIC ASSERTION: candidate pairs checked tracks the
        # concept NEIGHBORHOOD (10), never the full corpus (200) — a naive
        # `new x N` scan would have checked 200 pairs here.
        assert result.candidate_pairs_checked == 10
        assert result.candidate_pairs_checked < corpus_size / 10

    def test_multiple_new_papers_stay_sub_quadratic_in_aggregate(self, tmp_path):
        literature_dir, baseline = self._big_corpus(tmp_path)
        new_cks = [f"newpaper-{i}" for i in range(5)]
        for i, ck in enumerate(new_cks):
            _write_note(literature_dir, ck, concepts=[f"concept-{i}"])  # each: 1 concept, 10 candidates

        result = ir.run_incremental_relate(
            new_cks, literature_dir=literature_dir, baseline_citekeys=baseline,
            relate_fn=lambda a, b: None,
        )
        # 5 new papers x 10 candidates each = 50 total pairs — vs. a naive
        # `new x N` scan of 5 x 200 = 1000.
        assert result.candidate_pairs_checked == 50
        assert result.candidate_pairs_checked < 5 * len(baseline) / 10


class TestBidirectionalEdgeWrite:
    def test_edge_written_to_both_notes(self, tmp_path):
        literature_dir = tmp_path / "literature"
        _write_note(literature_dir, "existing2024", concepts=["persona-drift"])
        _write_note(literature_dir, "newone2026", concepts=["persona-drift"])

        def relate_fn(new_ck, cand_ck):
            assert new_ck == "newone2026"
            assert cand_ck == "existing2024"
            return {"tag": "SUPPORTS", "reason": "both find the same drift mechanism"}

        result = ir.run_incremental_relate(
            ["newone2026"], literature_dir=literature_dir,
            baseline_citekeys={"existing2024"}, relate_fn=relate_fn,
        )
        assert len(result.added_edges) == 1
        assert result.islands == []

        new_body = (literature_dir / "newone2026.md").read_text(encoding="utf-8")
        existing_body = (literature_dir / "existing2024.md").read_text(encoding="utf-8")

        new_edges = parse_paper_relations(new_body)
        existing_edges = parse_paper_relations(existing_body)
        assert not new_edges.malformed
        assert not existing_edges.malformed
        assert any(e["target"] == "existing2024" and e["tag"] == "SUPPORTS" for e in new_edges.edges)
        assert any(e["target"] == "newone2026" and e["tag"] == "SUPPORTS" for e in existing_edges.edges)

    def test_asymmetric_candidate_side_honored(self, tmp_path):
        literature_dir = tmp_path / "literature"
        _write_note(literature_dir, "base2024", concepts=["x"])
        _write_note(literature_dir, "new2026", concepts=["x"])

        def relate_fn(new_ck, cand_ck):
            return {"tag": "EXTENDS", "reason": "the new paper extends the base paper's method"}

        ir.run_incremental_relate(
            ["new2026"], literature_dir=literature_dir, baseline_citekeys={"base2024"},
            relate_fn=relate_fn,
        )
        # default (no candidate_tag override) mirrors the same tag both ways
        base_edges = parse_paper_relations((literature_dir / "base2024.md").read_text(encoding="utf-8"))
        assert any(e["target"] == "new2026" and e["tag"] == "EXTENDS" for e in base_edges.edges)

    def test_missing_note_raises_loudly(self, tmp_path):
        literature_dir = tmp_path / "literature"
        with pytest.raises(FileNotFoundError):
            ir.append_related_papers_edge(
                literature_dir / "ghost.md", display="x", target="y", tag="SUPPORTS", reason="r" * 20,
            )


class TestIslandEscalation:
    def test_zero_candidate_newcomer_is_an_island(self, tmp_path):
        literature_dir = tmp_path / "literature"
        _write_note(literature_dir, "base2024", concepts=["concept-a"])
        _write_note(literature_dir, "orphan2026", concepts=["concept-z"])  # no overlap

        result = ir.run_incremental_relate(
            ["orphan2026"], literature_dir=literature_dir, baseline_citekeys={"base2024"},
            relate_fn=lambda a, b: None,
        )
        assert result.islands == ["orphan2026"]
        assert result.candidate_pairs_checked == 0
        assert result.added_edges == []

    def test_escalation_scoped_to_only_the_island_paper(self, tmp_path):
        """An island newcomer escalates to a wider relate; a SIBLING newcomer
        in the same batch that DID have concept-graph candidates must NOT be
        escalated (never fanned out beyond the one island paper)."""
        literature_dir = tmp_path / "literature"
        _write_note(literature_dir, "base2024", concepts=["concept-a"])
        _write_note(literature_dir, "island2026", concepts=["concept-z"])       # no overlap -> island
        _write_note(literature_dir, "connected2026", concepts=["concept-a"])   # overlap -> not an island

        escalate_calls = []

        def escalate_relate_fn(new_ck, whole_corpus):
            escalate_calls.append(new_ck)
            assert whole_corpus == {"base2024"}
            return [{"candidate": "base2024", "tag": "PARTIAL", "reason": "wider relate found a weak link"}]

        result = ir.run_incremental_relate(
            ["island2026", "connected2026"],
            literature_dir=literature_dir, baseline_citekeys={"base2024"},
            relate_fn=lambda a, b: None,  # connected2026's candidate check finds nothing
            escalate_relate_fn=escalate_relate_fn,
        )
        assert escalate_calls == ["island2026"]  # ONLY the island paper, never connected2026
        assert result.islands == ["island2026"]
        assert result.escalated == [{"citekey": "island2026", "edges_written": 1}]
        assert any(e["new"] == "island2026" and e["escalated"] for e in result.added_edges)

        island_body = (literature_dir / "island2026.md").read_text(encoding="utf-8")
        island_edges = parse_paper_relations(island_body)
        assert any(e["target"] == "base2024" for e in island_edges.edges)

    def test_island_without_escalate_fn_is_recorded_never_dropped(self, tmp_path):
        literature_dir = tmp_path / "literature"
        _write_note(literature_dir, "base2024", concepts=["concept-a"])
        _write_note(literature_dir, "island2026", concepts=["concept-z"])

        result = ir.run_incremental_relate(
            ["island2026"], literature_dir=literature_dir, baseline_citekeys={"base2024"},
            relate_fn=lambda a, b: None,
            escalate_relate_fn=None,
        )
        assert result.islands == ["island2026"]
        assert result.escalated == []
        assert result.added_edges == []
