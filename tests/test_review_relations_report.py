"""test_review_relations_report.py — Wave 0 (Reading) PR-2: the "consume" seam.

Covers ``relations_report`` (review/__init__.py) — the deterministic,
corpus-wide fold of the '## Related papers' typed edges the relate-<key>
fan-out emits — and the ``rv review <project> relations <scope>`` CLI verb
that surfaces it (mirrors ``coverage_report`` / ``rv review coverage``
exactly, per the design doc's "reuse over create" + the anti-pattern
"do NOT hand-stem-match... run the deterministic command").

sr: NG-lit-review-wave0 (PR-2)
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from research_vault.config import load_config


def _write_lit_note(cfg, literature_dir: Path, citekey: str, body: str) -> None:
    """Write a two-layer pair: a thin overlay carrying a resolving
    ``central:`` backbone link, and the matching central core (at
    ``cfg.literature_root``) carrying the body — ``## Related papers`` is
    core-only content (``relations_report`` resolves each overlay's
    backbone link and reads edges from the resolved core, never the
    overlay directly)."""
    literature_dir.mkdir(parents=True, exist_ok=True)
    overlay_text = (
        "---\n"
        "type: literature\n"
        f"central: [{citekey}](okf:literature/{citekey}.md)\n"
        "---\n"
    )
    (literature_dir / f"{citekey}.md").write_text(overlay_text, encoding="utf-8")

    cfg.literature_root.mkdir(parents=True, exist_ok=True)
    core_text = (
        "---\n"
        "type: literature\n"
        f"citekey: {citekey}\n"
        f"title: {citekey} title\n"
        "---\n"
        f"{body}\n"
    )
    (cfg.literature_root / f"{citekey}.md").write_text(core_text, encoding="utf-8")


class TestRelationsReport:
    def test_empty_corpus_returns_zeroes(self, tmp_instance):
        from research_vault.review import relations_report
        cfg = load_config(reload=True)
        report = relations_report("demo-litreview", "scope-empty", config=cfg)
        assert report["edges"] == []
        assert report["counts"]["total"] == 0

    def test_aggregates_edges_across_the_corpus(self, tmp_instance):
        from research_vault.review import relations_report
        cfg = load_config(reload=True)
        literature_dir = cfg.project_notes_dir("demo-litreview") / "literature"

        _write_lit_note(
            cfg,
            literature_dir,
            "xiong2023-stepwise",
            "## Related papers\n\n"
            "- [huang2022](/literature/huang2022.md) — CONTRADICTS: huang2022 assumes a known safe "
            "baseline; this paper's bound removes that assumption. (refutational)\n",
        )
        _write_lit_note(
            cfg,
            literature_dir,
            "li2023",
            "## Related papers\n\n"
            "- [xiong2023-stepwise](/literature/xiong2023-stepwise.md) — SUPPORTS: replicates the same bound in "
            "a related regime, agreeing on the mechanism. (reciprocal)\n",
        )

        report = relations_report("demo-litreview", "scope-any", config=cfg)
        assert report["counts"]["total"] == 2
        assert report["counts"]["refutational"] == 1
        assert report["counts"]["reciprocal"] == 1
        sources = {e["source"] for e in report["edges"]}
        assert sources == {"xiong2023-stepwise", "li2023"}

        pair = report["by_pair"][("xiong2023-stepwise", "huang2022")]
        assert pair["tag"] == "CONTRADICTS"
        assert pair["type"] == "refutational"

    def test_no_related_papers_section_contributes_nothing(self, tmp_instance):
        from research_vault.review import relations_report
        cfg = load_config(reload=True)
        literature_dir = cfg.project_notes_dir("demo-litreview") / "literature"
        _write_lit_note(cfg, literature_dir, "novel2024", "No relations section here.\n")

        report = relations_report("demo-litreview", "scope-any", config=cfg)
        assert report["edges"] == []

    def test_malformed_edge_surfaced_not_silently_dropped(self, tmp_instance):
        """Architect review, the load-bearing fix: a typo'd tag under
        '## Related papers' must be surfaced in `malformed`, never silently
        absorbed — even though the corpus also has 2 well-formed edges."""
        from research_vault.review import relations_report
        cfg = load_config(reload=True)
        literature_dir = cfg.project_notes_dir("demo-litreview") / "literature"
        _write_lit_note(
            cfg,
            literature_dir,
            "xiong2023-stepwise",
            "## Related papers\n\n"
            "- [li2023](/literature/li2023.md) — SUPPORTS: agrees on the mechanism in a related setting.\n"
            "- [huang2022](/literature/huang2022.md) — CONTRADCTS: typo'd type, must be surfaced.\n",
        )
        report = relations_report("demo-litreview", "scope-any", config=cfg)
        assert len(report["edges"]) == 1
        assert len(report["malformed"]) == 1
        assert report["counts"]["malformed"] == 1
        assert "CONTRADCTS" in report["malformed"][0]["line"]
        assert report["malformed"][0]["source"] == "xiong2023-stepwise"

    def test_dangling_edge_flagged_target_not_in_corpus(self, tmp_instance):
        """Recommended (architect review): an edge whose target citekey has
        no matching literature note in this project is flagged dangling —
        mirrors coverage_report's orphan reporting."""
        from research_vault.review import relations_report
        cfg = load_config(reload=True)
        literature_dir = cfg.project_notes_dir("demo-litreview") / "literature"
        _write_lit_note(
            cfg,
            literature_dir,
            "xiong2023-stepwise",
            "## Related papers\n\n"
            "- [never-ingested-2019](/literature/never-ingested-2019.md) — CONTRADICTS: a citekey with no note.\n",
        )
        report = relations_report("demo-litreview", "scope-any", config=cfg)
        assert len(report["dangling"]) == 1
        assert report["counts"]["dangling"] == 1
        assert report["dangling"][0]["target"] == "never-ingested-2019"

    def test_edge_to_a_real_corpus_paper_is_not_dangling(self, tmp_instance):
        from research_vault.review import relations_report
        cfg = load_config(reload=True)
        literature_dir = cfg.project_notes_dir("demo-litreview") / "literature"
        _write_lit_note(
            cfg,
            literature_dir,
            "xiong2023-stepwise",
            "## Related papers\n\n"
            "- [li2023](/literature/li2023.md) — SUPPORTS: agrees on the mechanism in a related setting.\n",
        )
        _write_lit_note(cfg, literature_dir, "li2023", "No relations here.\n")
        report = relations_report("demo-litreview", "scope-any", config=cfg)
        assert report["dangling"] == []

    def test_kind_mismatch_surfaced_in_edge(self, tmp_instance):
        """The TYPE token is authoritative; a disagreeing (kind) mirror is
        surfaced on the edge, never silently resolved (mirrors
        key_equations' ledger-wins-over-body-mirror precedent)."""
        from research_vault.review import relations_report
        cfg = load_config(reload=True)
        literature_dir = cfg.project_notes_dir("demo-litreview") / "literature"
        _write_lit_note(
            cfg,
            literature_dir,
            "xiong2023-stepwise",
            "## Related papers\n\n"
            "- [huang2022](/literature/huang2022.md) — CONTRADICTS: removes the baseline assumption. (reciprocal)\n",
        )
        report = relations_report("demo-litreview", "scope-any", config=cfg)
        e = report["edges"][0]
        assert e["type"] == "refutational"  # tag wins over stated 'reciprocal'
        assert e["kind_mismatch"] == {"stated": "reciprocal", "derived": "refutational"}


class TestRelationsVerb:
    """D1 (verb consolidation): `rv review relations` is HARD-REMOVED from
    the curated CLI — the parsed subcommand is now a redirect stub
    (test_relations_subcommand_is_removed_stub below). The presentation
    logic these tests protect (`_run_relations`'s formatting of edge
    counts/malformed/dangling sections) is UNCHANGED and remains
    importable — exercised directly rather than through `run()`'s dispatch,
    which now short-circuits to the D1 redirect for the real CLI path."""

    def test_relations_subcommand_is_removed_stub(self, tmp_instance, capsys):
        from research_vault.review.verbs import build_parser, run
        parser = build_parser()
        args = parser.parse_args(["demo-litreview", "relations", "scope-any"])
        assert args.review_cmd == "relations"
        assert getattr(args, "_rv_removed_verb", None) is not None
        rc = run(args)
        assert rc == 2
        assert "REMOVED" in capsys.readouterr().err

    def test_relations_presentation_reports_edges(self, tmp_instance, capsys):
        from research_vault.review import verbs as review_verbs
        cfg = load_config(reload=True)
        literature_dir = cfg.project_notes_dir("demo-litreview") / "literature"
        _write_lit_note(
            cfg,
            literature_dir,
            "xiong2023-stepwise",
            "## Related papers\n\n"
            "- [li2023](/literature/li2023.md) — EXTENDS: generalizes the same result to a broader "
            "class of MDPs, building on the earlier special case. "
            "(line-of-argument)\n",
        )

        args = argparse.Namespace(project="demo-litreview", scope="scope-any")
        rc = review_verbs._run_relations(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "1 paper→paper edge" in out
        assert "xiong2023-stepwise → li2023" in out

    def test_relations_presentation_no_edges_reports_zero_not_crash(self, tmp_instance, capsys):
        from research_vault.review import verbs as review_verbs
        args = argparse.Namespace(project="demo-litreview", scope="scope-any")
        rc = review_verbs._run_relations(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "0 paper→paper edge" in out
        assert "No paper→paper edges found yet." in out

    def test_relations_presentation_surfaces_malformed_edges(self, tmp_instance, capsys):
        """Architect review, the load-bearing fix: the presentation layer
        must print malformed edges under their own headed section — never
        silently absorbed into a clean-looking edge total."""
        from research_vault.review import verbs as review_verbs
        cfg = load_config(reload=True)
        literature_dir = cfg.project_notes_dir("demo-litreview") / "literature"
        _write_lit_note(
            cfg,
            literature_dir,
            "xiong2023-stepwise",
            "## Related papers\n\n"
            "- [huang2022](/literature/huang2022.md) — CONTRADCTS: typo'd type, must be surfaced.\n",
        )
        args = argparse.Namespace(project="demo-litreview", scope="scope-any")
        rc = review_verbs._run_relations(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Malformed (1)" in out
        assert "CONTRADCTS" in out

    def test_relations_presentation_surfaces_dangling_edges(self, tmp_instance, capsys):
        from research_vault.review import verbs as review_verbs
        cfg = load_config(reload=True)
        literature_dir = cfg.project_notes_dir("demo-litreview") / "literature"
        _write_lit_note(
            cfg,
            literature_dir,
            "xiong2023-stepwise",
            "## Related papers\n\n"
            "- [never-ingested-2019](/literature/never-ingested-2019.md) — CONTRADICTS: a citekey with no note.\n",
        )
        args = argparse.Namespace(project="demo-litreview", scope="scope-any")
        rc = review_verbs._run_relations(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Dangling (1)" in out
        assert "never-ingested-2019" in out
