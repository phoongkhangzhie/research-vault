"""test_okf_pr3_link_resolution.py — producer-strict / consumer-tolerant
broken links.

Covers ``review.check_link_resolution`` (the curation-time gate) and its
three wiring points:

  1. ``rv note check`` (default) — a dangling link degrades to a
     ``[link-lint] WARN:`` (never flips the exit code): not-yet-written
     knowledge is tolerated during day-to-day authoring.
  2. ``rv note check --strict-links`` — the SAME finding is promoted to a
     hard ``[link-lint] BLOCK:`` (flips the exit code).
  3. The ``approve-review`` autonomous gate — a curation-time producer
     check; a corpus with an unresolved link is HALT-DECLAREd, never
     silently certified.

Plus the consumer-tolerance regression: a reader over a corpus carrying a
dangling link never raises — it returns the partial + a SIGNAL list.

the overlay unwind (0.3.2): literature is shared-canonical — every
fixture here writes ONE note directly under ``cfg.literature_root``, no
per-project overlay, no ``central:`` pointer.

All hermetic (``tmp_instance`` fixture from conftest.py). No live-instance reads.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from research_vault.config import load_config
from research_vault import note as note_mod


def _write_lit_note(
    cfg,
    citekey: str,
    *,
    core_body: str = "",
    concept_edges_body: str = "",
) -> None:
    """Write a single shared-canonical literature note (the overlay unwind (0.3.2)) —
    optionally carrying a ``## Related papers`` section (``core_body``) and
    a ``## Concept edges`` section (``concept_edges_body``)."""
    cfg.literature_root.mkdir(parents=True, exist_ok=True)
    text = (
        "---\n"
        "type: literature\n"
        f"citekey: {citekey}\n"
        f"title: {citekey} title\n"
        "---\n"
        f"{core_body}\n"
        f"{concept_edges_body}\n"
    )
    (cfg.literature_root / f"{citekey}.md").write_text(text, encoding="utf-8")


class TestCheckLinkResolution:
    """Direct tests of the resolver, before any WARN/BLOCK posture applies."""

    def test_clean_corpus_resolves(self, tmp_instance):
        from research_vault.review import check_link_resolution

        cfg = load_config(reload=True)
        _write_lit_note(cfg, "smith2024")
        result = check_link_resolution("demo-research", config=cfg)
        assert result == {"ok": True, "errors": []}

    def test_dangling_paper_edge_target_is_an_error(self, tmp_instance):
        from research_vault.review import check_link_resolution

        cfg = load_config(reload=True)
        _write_lit_note(
            cfg, "smith2024",
            core_body=(
                "## Related papers\n\n"
                "- [nonexistent](/literature/nonexistent.md) — SUPPORTS: a claim.\n"
            ),
        )
        result = check_link_resolution("demo-research", config=cfg)
        assert result["ok"] is False
        assert any(
            "nonexistent" in e and "Related papers" in e for e in result["errors"]
        )

    def test_dangling_concept_edge_target_is_an_error(self, tmp_instance):
        from research_vault.review import check_link_resolution

        cfg = load_config(reload=True)
        _write_lit_note(
            cfg, "smith2024",
            concept_edges_body=(
                "## Concept edges\n\n"
                "- [ghost concept](/concepts/ghost-concept.md) — SUPPORTS: a claim.\n"
            ),
        )
        result = check_link_resolution("demo-research", config=cfg)
        assert result["ok"] is False
        assert any(
            "ghost-concept" in e and "Concept edges" in e for e in result["errors"]
        )

    def test_resolvable_concept_edge_is_clean(self, tmp_instance):
        from research_vault.review import check_link_resolution

        cfg = load_config(reload=True)
        # concepts is shared-canonical (0.3.2) — resolves against
        # cfg.concepts_root, not project_notes_dir/concepts.
        concepts_dir = cfg.concepts_root
        concepts_dir.mkdir(parents=True, exist_ok=True)
        (concepts_dir / "real-concept.md").write_text(
            "---\ntype: concepts\n---\n\nA real concept.\n", encoding="utf-8",
        )
        _write_lit_note(
            cfg, "smith2024",
            concept_edges_body=(
                "## Concept edges\n\n"
                "- [real concept](/concepts/real-concept.md) — SUPPORTS: a claim.\n"
            ),
        )
        result = check_link_resolution("demo-research", config=cfg)
        assert result == {"ok": True, "errors": []}

    def test_no_literature_dir_is_a_correct_no_op(self, tmp_instance):
        from research_vault.review import check_link_resolution

        cfg = load_config(reload=True)
        result = check_link_resolution("demo-research", config=cfg)
        assert result == {"ok": True, "errors": []}

    def test_accepts_project_notes_dir_directly(self, tmp_instance):
        from research_vault.review import check_link_resolution

        cfg = load_config(reload=True)
        _write_lit_note(
            cfg, "smith2024",
            core_body=(
                "## Related papers\n\n"
                "- [nonexistent](/literature/nonexistent.md) — SUPPORTS: a claim.\n"
            ),
        )
        result = check_link_resolution(
            project_notes_dir=cfg.project_notes_dir("demo-research"),
        )
        assert result["ok"] is False

    def test_requires_project_or_project_notes_dir(self, tmp_instance):
        from research_vault.review import check_link_resolution

        with pytest.raises(ValueError):
            check_link_resolution()


class TestNoteCheckDefaultWarnStrictBlock:
    """The load-bearing split: default WARN (exit 0), --strict-links BLOCK
    (exit non-zero) — SAME underlying finding, two postures."""

    def test_default_check_warns_and_does_not_block(self, tmp_instance):
        cfg = load_config(reload=True)
        _write_lit_note(
            cfg, "smith2024",
            core_body=(
                "## Related papers\n\n"
                "- [nonexistent](/literature/nonexistent.md) — SUPPORTS: a claim.\n"
            ),
        )
        violations = note_mod.cmd_check("demo-research", config=cfg)
        link_findings = [v for v in violations if v.startswith("[link-lint]")]
        assert link_findings, "expected a link-lint finding to surface"
        assert all(v.startswith("[link-lint] WARN:") for v in link_findings)

    def test_strict_links_blocks(self, tmp_instance):
        cfg = load_config(reload=True)
        _write_lit_note(
            cfg, "smith2024",
            core_body=(
                "## Related papers\n\n"
                "- [nonexistent](/literature/nonexistent.md) — SUPPORTS: a claim.\n"
            ),
        )
        violations = note_mod.cmd_check("demo-research", config=cfg, strict_links=True)
        link_findings = [v for v in violations if v.startswith("[link-lint]")]
        assert link_findings, "expected a link-lint finding to surface"
        assert all(v.startswith("[link-lint] BLOCK:") for v in link_findings)

    def test_clean_corpus_no_link_findings_either_posture(self, tmp_instance):
        cfg = load_config(reload=True)
        _write_lit_note(cfg, "smith2024")
        for strict in (False, True):
            violations = note_mod.cmd_check(
                "demo-research", config=cfg, strict_links=strict,
            )
            link_findings = [v for v in violations if v.startswith("[link-lint]")]
            assert link_findings == []

    def test_cli_default_exit_zero_on_dangling_link(self, tmp_instance, capsys):
        import argparse

        cfg = load_config(reload=True)
        _write_lit_note(
            cfg, "smith2024",
            core_body=(
                "## Related papers\n\n"
                "- [nonexistent](/literature/nonexistent.md) — SUPPORTS: a claim.\n"
            ),
        )
        args = argparse.Namespace(project="demo-research", note_cmd="check", strict_links=False)
        rc = note_mod.run(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "[link-lint] WARN:" in out

    def test_cli_strict_links_exit_nonzero_on_dangling_link(self, tmp_instance, capsys):
        import argparse

        cfg = load_config(reload=True)
        _write_lit_note(
            cfg, "smith2024",
            core_body=(
                "## Related papers\n\n"
                "- [nonexistent](/literature/nonexistent.md) — SUPPORTS: a claim.\n"
            ),
        )
        args = argparse.Namespace(project="demo-research", note_cmd="check", strict_links=True)
        rc = note_mod.run(args)
        out = capsys.readouterr().out
        assert rc == 1
        assert "[link-lint] BLOCK:" in out

    def test_strict_links_flag_wired_into_parser(self):
        parser = note_mod.build_parser()
        args = parser.parse_args(["demo-research", "check", "--strict-links"])
        assert args.strict_links is True

        args_default = parser.parse_args(["demo-research", "check"])
        assert args_default.strict_links is False


class TestConsumerNeverRaises:
    """Regression pin: no reader over a corpus with a dangling link raises
    — it returns the partial (or a report) + a surfaced signal."""

    def test_cmd_check_tolerant_on_note_with_no_type_dir_match(self, tmp_instance):
        """0.3.2 (the overlay unwind): the old two-layer 'dangling central: pointer'
        tolerant-load case no longer exists (there is no backbone link to
        dangle) — the closest surviving consumer-tolerance regression is a
        malformed/incomplete note under the shared root never raising when
        read through cmd_check."""
        cfg = load_config(reload=True)
        cfg.literature_root.mkdir(parents=True, exist_ok=True)
        (cfg.literature_root / "ghost2025.md").write_text(
            "---\ntype: literature\n---\n\n", encoding="utf-8",
        )
        # Reaching this line without an exception IS the regression pin.
        violations = note_mod.cmd_check("demo-research", config=cfg)
        assert isinstance(violations, list)

    def test_relations_report_surfaces_dangling_as_a_signal_list(self, tmp_instance):
        from research_vault.review import relations_report

        cfg = load_config(reload=True)
        review_dir = cfg.project_notes_dir("demo-research") / "reviews" / "scope-x"
        review_dir.mkdir(parents=True, exist_ok=True)
        (review_dir / "_corpus.md").write_text(
            "| Annotation | Citekey |\n|---|---|\n"
            "| [IN-CORPUS] | smith2024 |\n",
            encoding="utf-8",
        )
        _write_lit_note(
            cfg, "smith2024",
            core_body=(
                "## Related papers\n\n"
                "- [nonexistent](/literature/nonexistent.md) — SUPPORTS: a claim.\n"
            ),
        )
        report = relations_report("demo-research", "scope-x", config=cfg)
        assert report["dangling"], "expected the dangling edge to be surfaced"
        assert report["counts"]["dangling"] == len(report["dangling"])
        # never raised — reaching this line at all is the regression pin.


class TestApproveReviewBlocksOnUnresolvedLink:
    """The curation-time producer gate: approve-review must never certify a
    corpus carrying an unresolved link."""

    def test_evaluate_autonomous_gate_halts_on_unresolved_link(self, tmp_instance, monkeypatch):
        from research_vault.dag.verbs import _evaluate_autonomous_gate
        from research_vault.dag.store import RunState
        from research_vault.review import autonomy as _autonomy

        cfg = load_config(reload=True)
        review_dir = cfg.project_notes_dir("demo-research") / "reviews" / "scope-x"
        review_dir.mkdir(parents=True, exist_ok=True)
        critic_path = review_dir / "_coverage-critic.md"
        critic_path.write_text(
            "---\ntype: reviews\nverdict: PASS\n---\n\nAll clear.\n", encoding="utf-8",
        )
        (review_dir / "_corpus.md").write_text("", encoding="utf-8")

        _write_lit_note(
            cfg, "smith2024",
            core_body=(
                "## Related papers\n\n"
                "- [nonexistent](/literature/nonexistent.md) — SUPPORTS: a claim.\n"
            ),
        )

        nodes_lookup = {
            "review-coverage-critic": {
                "produces": {"_coverage-critic.md": str(critic_path)},
            },
        }
        run_state = RunState(run_id="test-run", manifest_path=str(review_dir / "phase2-dag.json"), meta={})
        result = _evaluate_autonomous_gate(
            "approve-review", nodes_lookup, review_dir / "phase2-dag.json", run_state,
        )
        assert result.disposition == _autonomy.HALT_DECLARE
        assert "unresolved" in result.reason.lower()

    def test_evaluate_autonomous_gate_passes_clean_corpus(self, tmp_instance):
        from research_vault.dag.verbs import _evaluate_autonomous_gate
        from research_vault.dag.store import RunState
        from research_vault.review import autonomy as _autonomy

        cfg = load_config(reload=True)
        review_dir = cfg.project_notes_dir("demo-research") / "reviews" / "scope-x"
        review_dir.mkdir(parents=True, exist_ok=True)
        critic_path = review_dir / "_coverage-critic.md"
        critic_path.write_text(
            "---\ntype: reviews\nverdict: PASS\n---\n\nAll clear.\n", encoding="utf-8",
        )
        (review_dir / "_corpus.md").write_text("", encoding="utf-8")

        _write_lit_note(cfg, "smith2024")

        nodes_lookup = {
            "review-coverage-critic": {
                "produces": {"_coverage-critic.md": str(critic_path)},
            },
        }
        run_state = RunState(run_id="test-run", manifest_path=str(review_dir / "phase2-dag.json"), meta={})
        result = _evaluate_autonomous_gate(
            "approve-review", nodes_lookup, review_dir / "phase2-dag.json", run_state,
        )
        assert result.disposition != _autonomy.HALT_DECLARE
