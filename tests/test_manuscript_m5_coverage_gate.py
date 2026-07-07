"""test_manuscript_m5_coverage_gate.py — PR-M5's coverage-gate wiring
(design §10 gate-4), landed in ``manuscript/check_gates.py::check_coverage_gate``
and wired into ``build_approve_payload`` (single-sourced — no duplicate
assembly for PR-M5's per-round re-fire).

Also covers the integration-reviewer followup: an unregistered/malformed
``manuscript_type`` at ``rv dag approve``'s ``approve-manuscript`` node must
surface a loud NOT-RUN message (never a silent skip).

sr: PR-M5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.hashing import hash_file


def _write_corpus(project_notes_dir: Path, slug: str, citekeys: list[str]) -> Path:
    review_dir = project_notes_dir / "reviews" / slug
    review_dir.mkdir(parents=True, exist_ok=True)
    corpus_path = review_dir / "_corpus.md"
    lines = ["| status | citekey |", "| --- | --- |"]
    lines.extend(f"| [NEW] | {ck} |" for ck in citekeys)
    corpus_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return corpus_path


def _write_manuscript_note(tree_root: Path, *, corpus_hash: str = "") -> Path:
    tree_root.mkdir(parents=True, exist_ok=True)
    note_path = tree_root / "_manuscript.md"
    note_path.write_text(
        "---\n"
        "type: manuscript\n"
        "manuscript_type: lit-review\n"
        f"corpus_hash: {corpus_hash}\n"
        "---\n\n## Scope\n",
        encoding="utf-8",
    )
    return note_path


class TestCheckCoverageGate:
    def test_no_corpus_hash_stamped_is_a_correct_noop(self, tmp_path):
        from research_vault.manuscript.check_gates import check_coverage_gate

        project_notes_dir = tmp_path / "notes"
        tree_root = tmp_path / "manuscripts" / "survey-a"
        _write_manuscript_note(tree_root, corpus_hash="")

        result = check_coverage_gate(project_notes_dir, tree_root)
        assert result["ok"] is True
        assert result["errors"] == []
        assert any("no corpus_hash stamped" in w for w in result["warnings"])

    def test_matching_hash_no_prisma_line_passes(self, tmp_path):
        from research_vault.manuscript.check_gates import check_coverage_gate

        project_notes_dir = tmp_path / "notes"
        slug = "survey-b"
        corpus_path = _write_corpus(project_notes_dir, slug, ["smith2024", "jones2023"])
        digest = hash_file(corpus_path)
        tree_root = project_notes_dir / "manuscripts" / slug
        _write_manuscript_note(tree_root, corpus_hash=digest)

        result = check_coverage_gate(project_notes_dir, tree_root)
        assert result["ok"] is True
        assert result["errors"] == []

    def test_stale_corpus_hash_mismatch_blocks(self, tmp_path):
        """The corpus mutated (a citekey added) AFTER the freeze — the
        stamped hash no longer matches; BLOCK (the stale-corpus guard,
        design §4.5.5)."""
        from research_vault.manuscript.check_gates import check_coverage_gate

        project_notes_dir = tmp_path / "notes"
        slug = "survey-c"
        corpus_path = _write_corpus(project_notes_dir, slug, ["smith2024"])
        stale_digest = hash_file(corpus_path)
        tree_root = project_notes_dir / "manuscripts" / slug
        _write_manuscript_note(tree_root, corpus_hash=stale_digest)

        # Corpus mutates after the freeze.
        _write_corpus(project_notes_dir, slug, ["smith2024", "jones2023"])

        result = check_coverage_gate(project_notes_dir, tree_root)
        assert result["ok"] is False
        assert any("stale-corpus" in e or "changed since" in e for e in result["errors"])

    def test_missing_frozen_corpus_blocks(self, tmp_path):
        from research_vault.manuscript.check_gates import check_coverage_gate

        project_notes_dir = tmp_path / "notes"
        slug = "survey-d"
        tree_root = project_notes_dir / "manuscripts" / slug
        _write_manuscript_note(tree_root, corpus_hash="deadbeef" * 4)

        result = check_coverage_gate(project_notes_dir, tree_root)
        assert result["ok"] is False
        assert any("no longer exists" in e for e in result["errors"])

    def test_prisma_scope_narrowing_in_draft_blocks(self, tmp_path):
        """The frozen corpus has 3 papers; the draft's own PRISMA ledger
        claims only 2 — a revise narrowed scope to shrink the denominator
        (design §10 gate-4's literal example)."""
        from research_vault.manuscript.check_gates import check_coverage_gate

        project_notes_dir = tmp_path / "notes"
        slug = "survey-e"
        corpus_path = _write_corpus(
            project_notes_dir, slug, ["smith2024", "jones2023", "lee2022"],
        )
        digest = hash_file(corpus_path)
        tree_root = project_notes_dir / "manuscripts" / slug
        _write_manuscript_note(tree_root, corpus_hash=digest)

        (tree_root / "sections").mkdir(parents=True, exist_ok=True)
        (tree_root / "sections" / "prisma-scope.tex").write_text(
            "| Category | Count |\n"
            "| --- | --- |\n"
            "| Corpus (frozen citekeys) | 2 |\n",
            encoding="utf-8",
        )

        result = check_coverage_gate(project_notes_dir, tree_root)
        assert result["ok"] is False
        assert any("narrowed scope" in e for e in result["errors"])

    def test_prisma_scope_matching_count_passes(self, tmp_path):
        """Sanity control: the draft's PRISMA count matches the true frozen
        corpus — no BLOCK. Proves the BLOCK above is a real distinction."""
        from research_vault.manuscript.check_gates import check_coverage_gate

        project_notes_dir = tmp_path / "notes"
        slug = "survey-f"
        corpus_path = _write_corpus(project_notes_dir, slug, ["smith2024", "jones2023"])
        digest = hash_file(corpus_path)
        tree_root = project_notes_dir / "manuscripts" / slug
        _write_manuscript_note(tree_root, corpus_hash=digest)

        (tree_root / "sections").mkdir(parents=True, exist_ok=True)
        (tree_root / "sections" / "prisma-scope.tex").write_text(
            "| Category | Count |\n"
            "| --- | --- |\n"
            "| Corpus (frozen citekeys) | 2 |\n",
            encoding="utf-8",
        )

        result = check_coverage_gate(project_notes_dir, tree_root)
        assert result["ok"] is True
        assert result["errors"] == []


class TestBuildApprovePayloadWiresCoverageGate:
    def test_coverage_block_surfaces_in_blocking_not_not_run(self, tmp_path):
        """The integration PR left the coverage gate in `not_run` — PR-M5
        wires it for real: a stale corpus_hash must now BLOCK the payload,
        not just be recorded as deferred."""
        from research_vault.manuscript.check_gates import build_approve_payload
        from research_vault.manuscript.types import get_type

        project_notes_dir = tmp_path / "notes"
        slug = "survey-g"
        corpus_path = _write_corpus(project_notes_dir, slug, ["smith2024"])
        stale_digest = hash_file(corpus_path)
        tree_root = project_notes_dir / "manuscripts" / slug
        _write_manuscript_note(tree_root, corpus_hash=stale_digest)
        (tree_root / "refs.bib").write_text("", encoding="utf-8")
        _write_corpus(project_notes_dir, slug, ["smith2024", "jones2023"])  # mutate

        ms_type = get_type("lit-review")
        payload = build_approve_payload(tree_root, project_notes_dir, ms_type)

        assert payload["ok"] is False
        assert any("coverage-gate" in b for b in payload["blocking"])
        assert not any(
            "coverage-gate (design" in n and "deferred" in n for n in payload["not_run"]
        ), "the deferred not_run message should be GONE now that PR-M5 wires the gate for real"


class TestMalformedManuscriptTypeSurfacesLoudly:
    """Integration-reviewer followup: an unregistered/malformed
    manuscript_type used to fall through the approve-manuscript wiring
    silently (verbs.py:978's `if _ms_type is not None:` had no else) — no
    gates ran, nothing printed, human-go passed on zero checking. PR-M5 adds
    a loud NOT-RUN surface (charter §2)."""

    def test_unregistered_type_surfaces_not_run_but_does_not_block(self, tmp_path, capsys):
        from tests.test_manuscript_integration import (
            _set_run_env, _restore_env, _make_awaiting_run, _manuscript_note_for_wiring,
        )
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            project_notes_dir = tmp_path / "notes" / "projects" / "demo-research"
            manifest_dir = project_notes_dir / "manuscripts" / "survey-bad-type"
            note_path = _manuscript_note_for_wiring(
                manifest_dir / "_manuscript.md", spine_shape="pipeline", branches=["a"],
            )
            text = note_path.read_text(encoding="utf-8")
            note_path.write_text(
                text.replace("manuscript_type: lit-review", "manuscript_type: bogus-type"),
                encoding="utf-8",
            )
            (manifest_dir / "sections").mkdir(parents=True, exist_ok=True)
            (manifest_dir / "sections" / "thematic-sections.tex").write_text(
                "hi\n", encoding="utf-8",
            )
            store = _make_awaiting_run(tmp_path, "ms-bad-type", manifest_dir)

            args = argparse.Namespace(run_id="ms-bad-type", node_id="approve-manuscript")
            rc = cmd_approve(args)
            captured = capsys.readouterr()

            assert rc == 0, "an unregistered type is a data problem, not a BLOCK"
            rs = store.load("ms-bad-type")
            assert rs.node_status("approve-manuscript") == "succeeded"
            assert "NOT RUN" in captured.err
            assert "bogus-type" in captured.err
            assert "were NOT run" in captured.err
        finally:
            _restore_env(old)
