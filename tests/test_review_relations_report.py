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


def _write_lit_note(literature_dir: Path, citekey: str, body: str) -> None:
    literature_dir.mkdir(parents=True, exist_ok=True)
    text = (
        "---\n"
        "type: literature\n"
        f"citekey: {citekey}\n"
        f"title: {citekey} title\n"
        "---\n"
        f"{body}\n"
    )
    (literature_dir / f"{citekey}.md").write_text(text, encoding="utf-8")


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
            literature_dir,
            "xiong2023-stepwise",
            "## Related papers\n\n"
            "- [CONTRADICTS] huang2022 — huang2022 assumes a known safe "
            "baseline; this paper's bound removes that assumption. (refutational)\n",
        )
        _write_lit_note(
            literature_dir,
            "li2023",
            "## Related papers\n\n"
            "- [SUPPORTS] xiong2023-stepwise — replicates the same bound in "
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
        _write_lit_note(literature_dir, "novel2024", "No relations section here.\n")

        report = relations_report("demo-litreview", "scope-any", config=cfg)
        assert report["edges"] == []


class TestRelationsVerb:
    def test_relations_subcommand_registered(self, tmp_instance):
        from research_vault.review.verbs import build_parser
        parser = build_parser()
        args = parser.parse_args(["demo-litreview", "relations", "scope-any"])
        assert args.review_cmd == "relations"
        assert args.scope == "scope-any"

    def test_relations_verb_reports_edges(self, tmp_instance, capsys):
        from research_vault.review import verbs as review_verbs
        cfg = load_config(reload=True)
        literature_dir = cfg.project_notes_dir("demo-litreview") / "literature"
        _write_lit_note(
            literature_dir,
            "xiong2023-stepwise",
            "## Related papers\n\n"
            "- [EXTENDS] li2023 — generalizes the same result to a broader "
            "class of MDPs, building on the earlier special case. "
            "(line-of-argument)\n",
        )

        parser = review_verbs.build_parser()
        args = parser.parse_args(["demo-litreview", "relations", "scope-any"])
        rc = review_verbs.run(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "1 paper→paper edge" in out
        assert "xiong2023-stepwise → li2023" in out

    def test_relations_verb_no_edges_reports_zero_not_crash(self, tmp_instance, capsys):
        from research_vault.review import verbs as review_verbs
        parser = review_verbs.build_parser()
        args = parser.parse_args(["demo-litreview", "relations", "scope-any"])
        rc = review_verbs.run(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "0 paper→paper edge" in out
        assert "No paper→paper edges found yet." in out
