"""tests/test_pr3_sweep_angle_keys_and_snowball_seed_ids.py — PR-3 D-5a
additive seams: `run_sweep_from_protocol`'s `angle_keys`/`sources_override`
filter (selects EXISTING frozen queries, never authors a new one), and the
`snowball` tool op's `seed_ids` bypass (no `_screen.md` needed for a
critic-backtrack round, which has no screen step of its own).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.review import autonomy as auto  # noqa: E402
from research_vault.sources.sweep import parse_angle_matrix, run_sweep_from_protocol  # noqa: E402


_PROTOCOL_TEXT = (
    "---\n"
    "question: q\ninclusion: i\nexclusion: e\ncoverage_claim: c\n"
    "counter-position: cp\n"
    "seed_queries:\n"
    "  by-temporal:\n"
    "    thesis:\n"
    "      - \"drift query\"\n"
    "    counter:\n"
    "      - \"stability query\"\n"
    "  by-method:      \"legacy scalar query\"\n"
    "sources: [semantic-scholar]\n"
    "---\n\nProtocol.\n"
)


class TestAngleKeysFilter:
    def test_none_sweeps_full_matrix(self, tmp_path, monkeypatch):
        protocol = tmp_path / "_protocol.md"
        protocol.write_text(_PROTOCOL_TEXT, encoding="utf-8")

        seen_matrices = []

        def fake_run_width_sweep(angle_matrix, sources, **kwargs):
            seen_matrices.append(angle_matrix)
            return []

        monkeypatch.setattr("research_vault.sources.sweep.run_width_sweep", fake_run_width_sweep)
        run_sweep_from_protocol(protocol)
        assert set(seen_matrices[0]) == {"by-temporal.thesis.0", "by-temporal.counter.0", "by-method"}

    def test_angle_keys_restricts_to_the_named_facets_counter_side(self, tmp_path, monkeypatch):
        protocol = tmp_path / "_protocol.md"
        protocol.write_text(_PROTOCOL_TEXT, encoding="utf-8")

        seen_matrices = []
        seen_sources = []

        def fake_run_width_sweep(angle_matrix, sources, **kwargs):
            seen_matrices.append(angle_matrix)
            seen_sources.append(sources)
            return []

        monkeypatch.setattr("research_vault.sources.sweep.run_width_sweep", fake_run_width_sweep)
        run_sweep_from_protocol(
            protocol, angle_keys={"by-temporal.counter"}, sources_override=["semantic-scholar", "arxiv", "openalex", "pubmed"],
        )
        assert set(seen_matrices[0]) == {"by-temporal.counter.0"}
        assert seen_sources[0] == ["semantic-scholar", "arxiv", "openalex", "pubmed"]

    def test_angle_keys_matching_nothing_raises_never_silently_sweeps_zero(self, tmp_path):
        protocol = tmp_path / "_protocol.md"
        protocol.write_text(_PROTOCOL_TEXT, encoding="utf-8")

        with pytest.raises(ValueError, match="ZERO keys"):
            run_sweep_from_protocol(protocol, angle_keys={"by-nonexistent.counter"})

    def test_angle_keys_can_never_author_a_new_key(self, tmp_path, monkeypatch):
        """angle_keys only SELECTS from the parsed matrix — it is applied
        AFTER parse_angle_matrix, so it structurally cannot introduce a key
        that wasn't already in the frozen protocol."""
        protocol = tmp_path / "_protocol.md"
        protocol.write_text(_PROTOCOL_TEXT, encoding="utf-8")
        frozen_keys = set(parse_angle_matrix(protocol.read_text(encoding="utf-8")))

        seen_matrices = []

        def fake_run_width_sweep(angle_matrix, sources, **kwargs):
            seen_matrices.append(angle_matrix)
            return []

        monkeypatch.setattr("research_vault.sources.sweep.run_width_sweep", fake_run_width_sweep)
        run_sweep_from_protocol(protocol, angle_keys={"by-temporal"})
        assert set(seen_matrices[0]) <= frozen_keys


class TestSnowballOpSeedIdsBypass:
    def test_seed_ids_bypasses_screen_file(self, tmp_path, monkeypatch):
        from research_vault.review.autonomy import run_tool_op

        captured = {}

        class _FakeResult:
            stop_reason = "walk-complete:1-hops"

        def fake_run_citation_neighbor_walk(seed_ids, **kwargs):
            captured["seed_ids"] = seed_ids
            return _FakeResult()

        def fake_write_corpus_raw(result, path, **kwargs):
            path.write_text("| annotation | id | title | venue | year | abstract | flags |\n", encoding="utf-8")
            return path

        def fake_write_walk_report(result, path):
            path.write_text("---\nstop_reason: walk-complete:1-hops\n---\n", encoding="utf-8")
            return path

        monkeypatch.setattr(
            "research_vault.sources.snowball.run_citation_neighbor_walk",
            fake_run_citation_neighbor_walk,
        )
        monkeypatch.setattr("research_vault.sources.snowball.write_corpus_raw", fake_write_corpus_raw)
        monkeypatch.setattr("research_vault.sources.snowball.write_walk_report", fake_write_walk_report)

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        result = run_tool_op("snowball", seed_ids=["10.1/x", "2401.00002"], out_dir=str(out_dir))
        assert captured["seed_ids"] == ["10.1/x", "2401.00002"]
        assert result["stop_reason"] == "walk-complete:1-hops"

    def test_both_seed_and_seed_ids_raises(self, tmp_path):
        from research_vault.review.autonomy import run_tool_op

        with pytest.raises(ValueError, match="EXACTLY ONE"):
            run_tool_op("snowball", seed=str(tmp_path / "x.md"), seed_ids=["10.1/x"], out_dir=str(tmp_path))

    def test_neither_seed_nor_seed_ids_raises(self, tmp_path):
        from research_vault.review.autonomy import run_tool_op

        with pytest.raises(ValueError, match="EXACTLY ONE"):
            run_tool_op("snowball", out_dir=str(tmp_path))
