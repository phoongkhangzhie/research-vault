"""test_sr_lr_polish_s4.py — review-loop polish Slice 4: F16+F17 coverage report.

Acceptance criteria:
  F17: corpus citekey zheng2023-pride → note zheng2023-pride-mc-selectors.md
       carrying citekey: zheng2023-pride → materialized, NOT orphan.
  F16: corpus citekey with no matching note → unmaterialized (surfaced).
  Orphan: materialized note absent from all MOCs → orphan.
  rv review <p> coverage <scope> exits 0 with counts+lists.
  cmd_expand emits one-liner with coverage summary.
  review_critic_tips axis-2 references rv review coverage.
  coverage-gate label references rv review coverage.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_instance):
    from research_vault.config import load_config
    return load_config(reload=True)


@pytest.fixture
def review_with_corpus(cfg, tmp_instance):
    """Scaffold a review and provide a pre-written _corpus.md for testing."""
    from research_vault.review import cmd_new, _review_artifact_dir
    from research_vault.config import load_config

    note_path, review_dir, manifest = cmd_new(
        "demo-research",
        "scope-cov",
        question="Test coverage report",
        config=cfg,
    )

    # Write a corpus with 3 citekeys
    corpus = review_dir / "_corpus.md"
    corpus.write_text("""\
| Annotation | Citekey | Title |
|---|---|---|
| [NEW] | zheng2023-pride | Zheng 2023 Pride paper |
| [NEW] | wang2024-eval | Wang 2024 evaluation |
| [IN-CORPUS:smith2020] | smith2020 | Smith 2020 (already in corpus) |
""", encoding="utf-8")

    return cfg, review_dir, manifest


# ---------------------------------------------------------------------------
# F17: citekey: field identity — descriptive filename not orphaned
# ---------------------------------------------------------------------------

def test_f17_descriptive_filename_not_orphaned(review_with_corpus, tmp_instance):
    """F17: note with descriptive filename but correct citekey: field → materialized.

    Corpus citekey: zheng2023-pride
    Note filename: zheng2023-pride-mc-selectors.md
    Note citekey: field: zheng2023-pride

    Must be: materialized and NOT orphan (once a MOC mentions it).
    Without F17: stem-matching would yield stem=zheng2023-pride-mc-selectors
    → not in corpus → false unmaterialized or orphan.
    """
    from research_vault.review import coverage_report
    from research_vault.note import _render_frontmatter

    cfg, review_dir, _ = review_with_corpus
    project_notes_dir = cfg.project_notes_dir("demo-research")
    # the overlay unwind (0.3.2): literature is shared-canonical —
    # materialized notes live at cfg.literature_root, not a per-project dir.
    literature_dir = cfg.literature_root
    literature_dir.mkdir(parents=True, exist_ok=True)
    mocs_dir = project_notes_dir / "mocs"
    mocs_dir.mkdir(parents=True, exist_ok=True)

    # Write the note with a DESCRIPTIVE filename but the correct citekey: field
    note = literature_dir / "zheng2023-pride-mc-selectors.md"
    note.write_text(
        _render_frontmatter({
            "type": "literature",
            "citekey": "zheng2023-pride",          # F17: correct citekey field
            "title": "Zheng 2023 Pride paper",
            "year": "2023",
        }) + "\n## TL;DR\nSome content.\n",
        encoding="utf-8",
    )

    # Also write a MOC that mentions the citekey (so it's not orphan)
    moc = mocs_dir / "cultural-benchmarks.md"
    moc.write_text(
        "# Cultural Benchmarks\n\n"
        "- [zheng2023-pride] supports cultural evaluation (supporting)\n",
        encoding="utf-8",
    )

    report = coverage_report("demo-research", "scope-cov", config=cfg)

    # zheng2023-pride must be materialized (F17 fixed)
    assert "zheng2023-pride" in report["materialized"], (
        "F17: note with citekey: zheng2023-pride must be materialized "
        f"(got materialized={report['materialized']!r})"
    )
    # Must NOT be orphan (MOC mentions it)
    assert "zheng2023-pride" not in report["orphan"], (
        f"zheng2023-pride in a MOC must not be orphan (got orphan={report['orphan']!r})"
    )
    # Must NOT be in unmaterialized
    assert "zheng2023-pride" not in report["unmaterialized"], (
        f"zheng2023-pride must not be unmaterialized (got unmaterialized={report['unmaterialized']!r})"
    )


# ---------------------------------------------------------------------------
# F16: unmaterialized citekey surfaces
# ---------------------------------------------------------------------------

def test_f16_unmaterialized_citekey_surfaces(review_with_corpus, tmp_instance):
    """F16: corpus citekey with no matching note must appear in unmaterialized."""
    from research_vault.review import coverage_report

    cfg, review_dir, _ = review_with_corpus
    project_notes_dir = cfg.project_notes_dir("demo-research")
    literature_dir = project_notes_dir / "literature"
    literature_dir.mkdir(parents=True, exist_ok=True)

    # wang2024-eval: no note created → unmaterialized
    report = coverage_report("demo-research", "scope-cov", config=cfg)

    assert "wang2024-eval" in report["unmaterialized"], (
        f"wang2024-eval has no note; must be unmaterialized. "
        f"Got: {report['unmaterialized']!r}"
    )


# ---------------------------------------------------------------------------
# Orphan: materialized but absent from all MOCs
# ---------------------------------------------------------------------------

def test_orphan_materialized_but_not_in_any_moc(review_with_corpus, tmp_instance):
    """A materialized citekey absent from all MOC files must appear in orphan."""
    from research_vault.review import coverage_report
    from research_vault.note import _render_frontmatter

    cfg, review_dir, _ = review_with_corpus
    project_notes_dir = cfg.project_notes_dir("demo-research")
    # the overlay unwind (0.3.2): literature is shared-canonical —
    # materialized notes live at cfg.literature_root, not a per-project dir.
    literature_dir = cfg.literature_root
    literature_dir.mkdir(parents=True, exist_ok=True)
    mocs_dir = project_notes_dir / "mocs"
    mocs_dir.mkdir(parents=True, exist_ok=True)

    # Write a note for zheng2023-pride with the correct citekey: field
    note = literature_dir / "zheng2023-pride.md"
    note.write_text(
        _render_frontmatter({
            "type": "literature",
            "citekey": "zheng2023-pride",
            "title": "Zheng 2023",
        }) + "\n## TL;DR\nContent.\n",
        encoding="utf-8",
    )

    # No MOC file mentions zheng2023-pride → should be orphan
    report = coverage_report("demo-research", "scope-cov", config=cfg)

    assert "zheng2023-pride" in report["materialized"]
    assert "zheng2023-pride" in report["orphan"], (
        f"Materialized note absent from all MOCs must be orphan. "
        f"Got orphan={report['orphan']!r}"
    )


# ---------------------------------------------------------------------------
# coverage_report returns structured dict always
# ---------------------------------------------------------------------------

def test_coverage_report_empty_corpus_returns_zeroes(cfg, tmp_instance):
    """coverage_report on a scope with no _corpus.md returns zeros (not crash)."""
    from research_vault.review import coverage_report

    # No corpus file written — should return empty lists, not raise
    report = coverage_report("demo-research", "scope-nonexistent", config=cfg)

    assert report["counts"]["corpus"] == 0
    assert report["materialized"] == []
    assert report["unmaterialized"] == []
    assert report["orphan"] == []


def test_coverage_report_includes_all_keys(review_with_corpus, tmp_instance):
    """coverage_report always returns all required keys."""
    from research_vault.review import coverage_report
    cfg, _, _ = review_with_corpus

    report = coverage_report("demo-research", "scope-cov", config=cfg)

    required_keys = {"corpus_citekeys", "materialized", "unmaterialized", "orphan",
                     "mention_only", "counts"}
    assert required_keys <= set(report.keys()), (
        f"coverage_report missing keys: {required_keys - set(report.keys())}"
    )
    count_keys = {"corpus", "materialized", "unmaterialized", "orphan", "mention_only"}
    assert count_keys <= set(report["counts"].keys())


# ---------------------------------------------------------------------------
# rv review <p> coverage <scope> verb — exits 0 with output
# ---------------------------------------------------------------------------

def test_coverage_verb_removed_stub_exits_2(review_with_corpus, tmp_instance, capsys):
    """D1 (verb consolidation): rv review <project> coverage <scope> is a
    HARD-REMOVED stub — exits 2 with a redirect breadcrumb, never dispatches
    to coverage_report anymore (that now runs as the 'coverage' tool
    node-op). See test_coverage_presentation_exits_0 below for the
    underlying presentation function, which is unchanged."""
    from research_vault.review.verbs import run as review_run, build_parser

    p = build_parser()
    args = p.parse_args(["demo-research", "coverage", "scope-cov"])
    rc = review_run(args)
    assert rc == 2
    assert "REMOVED" in capsys.readouterr().err


def test_coverage_presentation_exits_0(review_with_corpus, tmp_instance, capsys):
    """The underlying presentation function (_run_coverage) still exits 0
    on success — it is unchanged, only no longer reachable via the CLI."""
    from research_vault.review import verbs as review_mod_verbs
    cfg, _, _ = review_with_corpus

    from research_vault import review as review_mod
    orig_coverage_report = review_mod.coverage_report

    def mock_coverage_report(project, scope, *, config=None):
        return orig_coverage_report(project, scope, config=cfg)

    review_mod.coverage_report = mock_coverage_report
    try:
        args = argparse.Namespace(project="demo-research", scope="scope-cov")
        rc = review_mod_verbs._run_coverage(args)
    finally:
        review_mod.coverage_report = orig_coverage_report

    assert rc in (0, 1), f"_run_coverage must exit 0 or 1; got {rc}"


def test_coverage_subcommand_is_removed_stub(tmp_instance):
    """coverage still PARSES (as a D1 redirect stub) but no longer carries
    a 'scope' positional — the real scope-checking logic lives in the
    'coverage' tool node-op now."""
    from research_vault.review.verbs import build_parser
    p = build_parser()
    args = p.parse_args(["demo-research", "coverage", "scope-test"])
    assert args.review_cmd == "coverage"
    assert args.project == "demo-research"
    assert getattr(args, "_rv_removed_verb", None) is not None


# ---------------------------------------------------------------------------
# cmd_expand emits one-liner coverage summary
# ---------------------------------------------------------------------------

def test_cmd_expand_emits_coverage_summary(review_with_corpus, tmp_instance, capsys):
    """cmd_expand must emit a coverage summary line after writing the manifest."""
    from research_vault.review import cmd_expand
    cfg, review_dir, _ = review_with_corpus
    corpus = review_dir / "_corpus.md"

    cmd_expand("demo-research", "scope-cov", corpus_path=corpus, config=cfg)

    out = capsys.readouterr().out
    # The one-liner must include "coverage" and key counts
    assert "coverage" in out.lower(), (
        f"cmd_expand must emit a coverage summary; got stdout: {out!r}"
    )


# ---------------------------------------------------------------------------
# review_critic_tips axis-2 references rv review coverage
# ---------------------------------------------------------------------------

def test_review_critic_tips_axis2_references_coverage_verb():
    """review_critic_tips axis-2 (orphan) must instruct running rv review coverage."""
    from research_vault.review.style import get_review_tips

    tips = get_review_tips()
    critic_tip = tips["review_critic_tips"]

    # Must reference the coverage verb (not hand stem-matching)
    assert "rv review" in critic_tip and "coverage" in critic_tip, (
        "review_critic_tips must reference `rv review <project> coverage <scope>` "
        f"for orphan detection. Got:\n{critic_tip[:400]}"
    )


def test_review_critic_tips_axis2_mentions_citekey_field():
    """review_critic_tips axis-2 must mention 'citekey:' frontmatter field."""
    from research_vault.review.style import get_review_tips
    tips = get_review_tips()
    critic_tip = tips["review_critic_tips"]
    assert "citekey" in critic_tip.lower(), (
        "review_critic_tips must mention 'citekey' for F17 (field-based identity). "
        f"Got:\n{critic_tip[:400]}"
    )


# ---------------------------------------------------------------------------
# coverage-gate label references coverage checking (not a fabricated CLI verb —
# `rv review coverage` is REMOVED, D1 verb consolidation: coverage is checked
# via the 'coverage' tool node-op, not a hand-run subcommand)
# ---------------------------------------------------------------------------

def test_coverage_gate_label_references_coverage_verb(cfg, tmp_instance):
    """coverage-gate label in Phase-1 manifest must reference coverage checking,
    but must NOT instruct a hand-run `rv review coverage` (that verb is
    HARD-REMOVED, D1)."""
    from research_vault.review import cmd_new

    _, _, manifest = cmd_new(
        "demo-research",
        "scope-gate-label",
        question="Test gate label",
        config=cfg,
    )

    gate = next(n for n in manifest["nodes"] if n["id"] == "coverage-gate")
    label = gate.get("label", "")

    assert "coverage" in label.lower(), (
        f"coverage-gate label must reference 'coverage'; got: {label!r}"
    )
    assert "rv review <project> coverage" not in label, (
        f"coverage-gate label must not instruct a hand-run 'rv review coverage' "
        f"(that verb is HARD-REMOVED, D1); got: {label!r}"
    )
