"""test_review_ledger.py — PR-5 acceptance tests for the additive
``_corpus_ledger.md`` assembler (``review/ledger.py``).

Coverage (mirrors the PR-5 brief's ACCEPTANCE list):
  (a) schema-conformant assembly from a fixture scope; every frontmatter
      scalar traces to a named source artifact.
  (b) COMPLETE / CLEAN / CANONICALLY-KEYED all verifiable from the ledger
      alone.
  (c) additive — sibling artifacts untouched by this module.
  (d) fail-closed mutation test — a corrupted/missing source flips
      ``ledger_complete`` to false AND names the gap.
  (e) idempotent — re-running on unchanged state yields a byte-identical
      ledger; re-running after a simulated backtrack-append reflects the
      new state.
  (f) HALT snapshot — ``halt_reason`` produces ``ledger_complete: false``
      with the reason surfaced.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.review.ledger import write_corpus_ledger
from research_vault.note import _parse_frontmatter


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _protocol_note(path: Path, *, counter_position: str = "stability literature") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "type: review-protocol\n"
        'question: "Does X improve Y?"\n'
        'inclusion: "empirical studies"\n'
        'exclusion: "non-empirical"\n'
        'coverage_claim: "broad"\n'
        f'counter-position: "{counter_position}"\n'
        "seed_queries:\n"
        '  by-method: "q1"\n'
        '  by-outcome: "q2"\n'
        "sources: [semantic-scholar, arxiv]\n"
        "---\n",
        encoding="utf-8",
    )
    return path


def _search_hits_note(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ndark_sources: \n---\n\n# Search hits\n\n## Cells\n\n"
        "| Angle | Source | Hits | Error |\n|---|---|---|---|\n"
        "| by-method | semantic-scholar | 5 |  |\n"
        "| by-outcome | arxiv | 3 |  |\n\n"
        "Total hits fetched: 8\n",
        encoding="utf-8",
    )
    return path


def _walk_note(path: Path, *, stop_reason: str = "walk-complete:1-hops") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nstop_reason: {stop_reason}\nunresolvable_count: 0\n---\n\n"
        "# Citation-neighbor relevance walk\n\n"
        "| Round | New (forward) | New (backward) | New independent | Cumulative | Direction-starved |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | 4 | 2 | 5 | 5 |  |\n"
        "| 2 | 0 | 0 | 0 | 5 |  |\n",
        encoding="utf-8",
    )
    return path


def _corpus_note(path: Path, rows: list[tuple[str, str]] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = rows if rows is not None else [
        ("[NEW]", "smith2024a"),
        ("[IN-CORPUS:jones2023]", "jones2023"),
    ]
    lines = [f"| {ann} | {ck} | Title | abstract |" for ann, ck in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _literature_notes(lit_dir: Path) -> None:
    lit_dir.mkdir(parents=True, exist_ok=True)
    (lit_dir / "smith2024a.md").write_text(
        "---\ntype: literature\ncitekey: smith2024a\ndoi: 10.1234/abcd\n---\n", encoding="utf-8",
    )
    (lit_dir / "jones2023.md").write_text(
        "---\ntype: literature\ncitekey: jones2023\narxiv_id: 2301.00001\n---\n", encoding="utf-8",
    )


def _build_scope(tmp_path: Path, name: str = "demo-scope") -> tuple[Path, Path]:
    review_dir = tmp_path / "reviews" / name
    _protocol_note(review_dir / "_protocol.md")
    _search_hits_note(review_dir / "_search_hits.md")
    _walk_note(review_dir / "_walk.md")
    _corpus_note(review_dir / "_corpus.md")
    lit_dir = tmp_path / "notes" / "demo" / "literature"
    _literature_notes(lit_dir)
    return review_dir, lit_dir


def _snapshot_mtimes(paths: list[Path]) -> dict[Path, float]:
    return {p: p.stat().st_mtime for p in paths if p.exists()}


# ---------------------------------------------------------------------------
# (a) schema-conformant assembly
# ---------------------------------------------------------------------------

class TestSchemaConformance:
    def test_assembles_all_frontmatter_scalars(self, tmp_path):
        review_dir, lit_dir = _build_scope(tmp_path)
        out = write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir)
        assert out == review_dir / "_corpus_ledger.md"
        text = out.read_text(encoding="utf-8")
        fields, body = _parse_frontmatter(text)

        expected_keys = {
            "type", "review_scope", "schema_version", "ledger_complete",
            "matrix_hash", "angles_searched", "distinct_query_count", "matrix_band_ok",
            "stop_reason", "walk_bounded", "open_counter_poles",
            "critic_backtrack_rounds",
            "relevance_verdict_total", "off_domain_count", "uncertain_count",
            "off_domain_fraction", "relevance_disposition", "relevance_canary_ok",
            "pruned_off_domain",
            "citekey_convention", "citekey_conformant_count", "citekey_nonconformant_count",
            "citekey_migrated_count", "accepted", "in_corpus", "new",
        }
        assert expected_keys.issubset(fields.keys())
        assert fields["type"] == "corpus-ledger"
        assert fields["review_scope"] == "demo-scope"
        assert str(fields["ledger_complete"]).strip().lower() == "true"
        assert fields["matrix_hash"].startswith("sha256:")
        assert fields["angles_searched"] == "by-method, by-outcome"
        assert str(fields["distinct_query_count"]) == "2"
        assert fields["stop_reason"] == "walk-complete:1-hops"
        assert fields["citekey_convention"] == "authorYearWord"
        assert str(fields["citekey_conformant_count"]) == "2"
        assert str(fields["citekey_nonconformant_count"]) == "0"
        # PR-5 fix-round CHANGE 2: no _citekey_migration_ledger.json in
        # lit_dir -> honest "untracked" sentinel, never a fabricated 0.
        assert fields["citekey_migrated_count"] == "untracked"
        assert str(fields["accepted"]) == "2"
        assert str(fields["in_corpus"]) == "1"
        assert str(fields["new"]) == "1"

    def test_body_has_five_tables(self, tmp_path):
        review_dir, lit_dir = _build_scope(tmp_path)
        out = write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir)
        text = out.read_text(encoding="utf-8")
        for heading in (
            "## Search plan provenance",
            "## Citation-neighbor walk",
            "## Relevance-gate dispositions",
            "## Canonical-key map",
            "## Open coverage residue",
        ):
            assert heading in text, f"missing {heading}"

    def test_search_hits_row_traced_to_source(self, tmp_path):
        review_dir, lit_dir = _build_scope(tmp_path)
        out = write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir)
        text = out.read_text(encoding="utf-8")
        assert "by-method" in text and "semantic-scholar" in text and "| 5 |" in text

    def test_key_map_traces_to_literature_note(self, tmp_path):
        review_dir, lit_dir = _build_scope(tmp_path)
        out = write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir)
        text = out.read_text(encoding="utf-8")
        assert "smith2024a" in text and "doi:10.1234/abcd" in text
        assert "jones2023" in text and "arxiv:2301.00001" in text

    def test_missing_literature_dir_gap_not_guessed(self, tmp_path):
        """No literature_dir supplied -> resolving id column blank, a
        surfaced gap, never a fabricated id."""
        review_dir, _lit_dir = _build_scope(tmp_path)
        out = write_corpus_ledger(review_dir, literature_dir=None)
        fields, text = _parse_frontmatter(out.read_text(encoding="utf-8"))
        assert str(fields["ledger_complete"]).strip().lower() == "false"
        assert "[LEDGER-GAP]" in text
        assert "no literature note found" in text


# ---------------------------------------------------------------------------
# (b) COMPLETE / CLEAN / CANONICALLY-KEYED verifiable from the ledger alone
# ---------------------------------------------------------------------------

class TestVerifiablePropertiesFromLedgerAlone:
    def test_complete_verifiable(self, tmp_path):
        review_dir, lit_dir = _build_scope(tmp_path)
        out = write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir)
        fields, _ = _parse_frontmatter(out.read_text(encoding="utf-8"))
        # COMPLETE: angles + walk-terminal stop_reason + open poles all present/derivable
        assert fields["angles_searched"]
        assert fields["stop_reason"] == "walk-complete:1-hops"
        assert fields["open_counter_poles"] == ""  # no _coverage-gaps.md -> nothing open

    def test_clean_verifiable(self, tmp_path):
        review_dir, lit_dir = _build_scope(tmp_path)
        out = write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir)
        fields, _ = _parse_frontmatter(out.read_text(encoding="utf-8"))
        # CLEAN: relevance disposition + off-domain fraction + prune count
        assert fields["off_domain_count"] == "0" or int(fields["off_domain_count"]) == 0
        assert int(fields["pruned_off_domain"]) == 0

    def test_canonically_keyed_verifiable(self, tmp_path):
        review_dir, lit_dir = _build_scope(tmp_path)
        out = write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir)
        fields, _ = _parse_frontmatter(out.read_text(encoding="utf-8"))
        assert fields["citekey_convention"] == "authorYearWord"
        assert int(fields["citekey_conformant_count"]) == 2
        assert int(fields["citekey_nonconformant_count"]) == 0

    def test_nonconformant_citekey_detected(self, tmp_path):
        review_dir = tmp_path / "reviews" / "bad-scope"
        _protocol_note(review_dir / "_protocol.md")
        _search_hits_note(review_dir / "_search_hits.md")
        _walk_note(review_dir / "_walk.md")
        _corpus_note(review_dir / "_corpus.md", rows=[("[NEW]", "S2:123456")])
        out = write_corpus_ledger(review_dir, literature_dir=None)
        fields, _ = _parse_frontmatter(out.read_text(encoding="utf-8"))
        assert int(fields["citekey_nonconformant_count"]) == 1
        assert int(fields["citekey_conformant_count"]) == 0


# ---------------------------------------------------------------------------
# (c) additive — siblings untouched
# ---------------------------------------------------------------------------

class TestAdditive:
    def test_sibling_artifacts_untouched(self, tmp_path):
        review_dir, lit_dir = _build_scope(tmp_path)
        siblings = [
            review_dir / "_protocol.md",
            review_dir / "_search_hits.md",
            review_dir / "_walk.md",
            review_dir / "_corpus.md",
        ]
        before_text = {p: p.read_text(encoding="utf-8") for p in siblings}
        before_mtime = _snapshot_mtimes(siblings)

        write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir)

        after_text = {p: p.read_text(encoding="utf-8") for p in siblings}
        after_mtime = _snapshot_mtimes(siblings)
        assert before_text == after_text, "a sibling artifact's CONTENT changed"
        assert before_mtime == after_mtime, "a sibling artifact was RE-WRITTEN (mtime changed)"

    def test_no_coverage_gaps_file_created_when_absent(self, tmp_path):
        review_dir, lit_dir = _build_scope(tmp_path)
        write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir)
        assert not (review_dir / "_coverage-gaps.md").exists()


# ---------------------------------------------------------------------------
# (d) fail-closed mutation test
# ---------------------------------------------------------------------------

class TestFailClosed:
    def test_missing_corpus_flips_ledger_incomplete(self, tmp_path):
        review_dir, lit_dir = _build_scope(tmp_path)
        (review_dir / "_corpus.md").unlink()
        out = write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir)
        fields, text = _parse_frontmatter(out.read_text(encoding="utf-8"))
        assert str(fields["ledger_complete"]).strip().lower() == "false"
        assert "[LEDGER-GAP]" in text
        assert "_corpus.md not found" in text

    def test_malformed_corpus_row_flips_ledger_incomplete_and_names_gap(self, tmp_path):
        review_dir, lit_dir = _build_scope(tmp_path)
        # A bracket-shaped but unrecognized annotation — malformed row.
        (review_dir / "_corpus.md").write_text(
            "| [NEW] | smith2024a | Title A | abstract |\n"
            "| [WEIRD-TAG] | ghost2099 | Title Ghost | abstract |\n",
            encoding="utf-8",
        )
        out = write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir)
        fields, text = _parse_frontmatter(out.read_text(encoding="utf-8"))
        assert str(fields["ledger_complete"]).strip().lower() == "false"
        assert "[LEDGER-GAP]" in text
        assert "malformed row annotation" in text
        # The malformed row must not silently inflate the counted total.
        assert int(fields["accepted"]) == 1

    def test_missing_walk_report_flips_ledger_incomplete(self, tmp_path):
        review_dir, lit_dir = _build_scope(tmp_path)
        (review_dir / "_walk.md").unlink()
        out = write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir)
        fields, text = _parse_frontmatter(out.read_text(encoding="utf-8"))
        assert str(fields["ledger_complete"]).strip().lower() == "false"
        assert fields["stop_reason"] == ""
        assert "[LEDGER-GAP]" in text

    def test_missing_protocol_flips_ledger_incomplete(self, tmp_path):
        review_dir, lit_dir = _build_scope(tmp_path)
        (review_dir / "_protocol.md").unlink()
        out = write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir)
        fields, text = _parse_frontmatter(out.read_text(encoding="utf-8"))
        assert str(fields["ledger_complete"]).strip().lower() == "false"
        assert fields["matrix_hash"] == ""
        assert "[LEDGER-GAP]" in text

    def test_clean_state_is_complete(self, tmp_path):
        """Positive control — proves the FP-guard above isn't just always False."""
        review_dir, lit_dir = _build_scope(tmp_path)
        out = write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir)
        fields, text = _parse_frontmatter(out.read_text(encoding="utf-8"))
        assert str(fields["ledger_complete"]).strip().lower() == "true"
        assert "[LEDGER-GAP]" not in text


# ---------------------------------------------------------------------------
# (e) idempotence
# ---------------------------------------------------------------------------

class TestIdempotence:
    def test_rerun_on_unchanged_state_is_byte_identical(self, tmp_path):
        review_dir, lit_dir = _build_scope(tmp_path)
        out1 = write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir)
        first = out1.read_bytes()
        out2 = write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir)
        second = out2.read_bytes()
        assert first == second

    def test_rerun_after_backtrack_append_reflects_new_state(self, tmp_path):
        review_dir, lit_dir = _build_scope(tmp_path)
        write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir)

        # Simulate a PR-3 backtrack round appending a new corpus row +
        # literature note (append-only source mutation).
        _corpus_note(review_dir / "_corpus.md", rows=[
            ("[NEW]", "smith2024a"),
            ("[IN-CORPUS:jones2023]", "jones2023"),
            ("[NEW]", "lee2025b"),
        ])
        (lit_dir / "lee2025b.md").write_text(
            "---\ntype: literature\ncitekey: lee2025b\ndoi: 10.9999/zzzz\n---\n", encoding="utf-8",
        )

        out = write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir)
        fields, text = _parse_frontmatter(out.read_text(encoding="utf-8"))
        assert int(fields["accepted"]) == 3
        assert int(fields["new"]) == 2
        assert "lee2025b" in text


# ---------------------------------------------------------------------------
# (f) HALT snapshot
# ---------------------------------------------------------------------------

class TestHaltSnapshot:
    def test_halt_reason_flips_incomplete_and_surfaces_reason(self, tmp_path):
        review_dir, lit_dir = _build_scope(tmp_path)
        out = write_corpus_ledger(
            review_dir, literature_dir=lit_dir, literature_root=lit_dir,
            halt_reason="off-domain fraction 35% at/above threshold",
        )
        fields, text = _parse_frontmatter(out.read_text(encoding="utf-8"))
        assert str(fields["ledger_complete"]).strip().lower() == "false"
        assert "[LEDGER-GAP] HALT:" in text
        assert "off-domain fraction 35%" in text


# ---------------------------------------------------------------------------
# Relevance-payload wiring (P block)
# ---------------------------------------------------------------------------

class TestRelevanceBlock:
    def test_no_relevance_node_is_honest_no_op(self, tmp_path):
        review_dir, lit_dir = _build_scope(tmp_path)
        out = write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir, relevance_payload=None)
        fields, text = _parse_frontmatter(out.read_text(encoding="utf-8"))
        # Absence of the node is NOT a gap (honest no-op, see dag/verbs.py's
        # own optional-collaborator handling of review-relevance-verify).
        assert str(fields["ledger_complete"]).strip().lower() == "true"
        assert "review-relevance-verify not wired" in text

    def test_relevance_verdicts_populate_p_block(self, tmp_path):
        review_dir, lit_dir = _build_scope(tmp_path)
        payload = {
            "exists": True,
            "canary_aborted": False,
            "canary_detail": "both canaries classified correctly",
            "verdicts": {
                "smith2024a": "IN", "jones2023": "OFF_DOMAIN",
                "p3": "IN", "p4": "IN", "p5": "IN", "p6": "IN",
            },
            "malformed": [],
            "empty_verdict_set": False,
        }
        out = write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir, relevance_payload=payload)
        fields, text = _parse_frontmatter(out.read_text(encoding="utf-8"))
        assert int(fields["relevance_verdict_total"]) == 6
        assert int(fields["off_domain_count"]) == 1
        assert fields["relevance_disposition"] == "GO-WITH-RESIDUE"
        assert str(fields["relevance_canary_ok"]).strip().lower() == "true"
        assert int(fields["pruned_off_domain"]) == 1
        assert "jones2023" in text and "OFF_DOMAIN" in text and "pruned" in text

    def test_canary_aborted_surfaces_gap(self, tmp_path):
        review_dir, lit_dir = _build_scope(tmp_path)
        payload = {
            "exists": True,
            "canary_aborted": True,
            "canary_detail": "off-domain canary misclassified",
            "verdicts": {"smith2024a": "IN"},
            "malformed": [],
            "empty_verdict_set": False,
        }
        out = write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir, relevance_payload=payload)
        fields, text = _parse_frontmatter(out.read_text(encoding="utf-8"))
        assert str(fields["ledger_complete"]).strip().lower() == "false"
        assert str(fields["relevance_canary_ok"]).strip().lower() == "false"
        assert "[LEDGER-GAP]" in text and "canary" in text.lower()


# ---------------------------------------------------------------------------
# Coverage-gaps residue (best-effort, verbatim)
# ---------------------------------------------------------------------------

class TestCoverageGapsResidue:
    def test_coverage_gaps_file_surfaced_verbatim(self, tmp_path):
        review_dir, lit_dir = _build_scope(tmp_path)
        (review_dir / "_coverage-gaps.md").write_text(
            "terminated by the total-fetch budget; corpus is bounded, not depth-complete.\n\n"
            "- stability sub-literature remains under-explored\n"
            "- concepts/robustness still growing at termination\n",
            encoding="utf-8",
        )
        out = write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir)
        fields, text = _parse_frontmatter(out.read_text(encoding="utf-8"))
        assert "stability sub-literature" in fields["open_counter_poles"]
        assert "not depth-complete" in text
        assert str(fields["ledger_complete"]).strip().lower() == "true"


# ---------------------------------------------------------------------------
# PR-5 fix-round CHANGE 2: citekey_migrated_count traces to the real
# _citekey_migration_ledger.json (never a fabricated bare 0).
# ---------------------------------------------------------------------------

class TestCitekeyMigratedCount:
    def test_absent_ledger_is_untracked_not_zero(self, tmp_path):
        """No _citekey_migration_ledger.json in lit_dir -> honest sentinel,
        never a bare 0 (charter §1: don't fabricate a "measured zero")."""
        review_dir, lit_dir = _build_scope(tmp_path)
        assert not (lit_dir / "_citekey_migration_ledger.json").exists()

        out = write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir)
        fields, _ = _parse_frontmatter(out.read_text(encoding="utf-8"))
        assert fields["citekey_migrated_count"] == "untracked"
        # Non-gating: the absence of a migration ledger does not itself
        # flip the review's own ledger_complete (migrate-citekeys is an
        # optional, project-wide one-shot pass, not part of THIS review's
        # completeness contract).
        assert str(fields["ledger_complete"]).strip().lower() == "true"

    def test_no_literature_dir_is_untracked(self, tmp_path):
        """literature_dir=None (no lookup possible at all) -> same honest
        sentinel, not a crash and not a fabricated 0."""
        review_dir, _lit_dir = _build_scope(tmp_path)
        out = write_corpus_ledger(review_dir, literature_dir=None)
        fields, _ = _parse_frontmatter(out.read_text(encoding="utf-8"))
        assert fields["citekey_migrated_count"] == "untracked"

    def test_present_ledger_traces_real_count(self, tmp_path):
        """A real _citekey_migration_ledger.json intersected with THIS
        review's _corpus.md citekeys -> a real, traceable count, not the
        raw entry count (an entry for a citekey NOT in this review's
        corpus must not be counted)."""
        import json

        review_dir, lit_dir = _build_scope(tmp_path)
        # _build_scope's default corpus rows are smith2024a + jones2023.
        ledger_entries = [
            {"note": "smith2024a.md", "old": "smith24", "new": "smith2024a",
             "migrated_at": "2026-07-01T00:00:00Z"},
            {"note": "unrelated.md", "old": "unrelated23", "new": "unrelated2023",
             "migrated_at": "2026-07-01T00:00:00Z"},
        ]
        (lit_dir / "_citekey_migration_ledger.json").write_text(
            json.dumps(ledger_entries), encoding="utf-8",
        )

        out = write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir)
        fields, _ = _parse_frontmatter(out.read_text(encoding="utf-8"))
        # Only smith2024a is in THIS review's corpus -> count is 1, not 2.
        assert str(fields["citekey_migrated_count"]) == "1"
        assert str(fields["ledger_complete"]).strip().lower() == "true"

    def test_malformed_ledger_json_is_untracked_not_a_crash(self, tmp_path):
        review_dir, lit_dir = _build_scope(tmp_path)
        (lit_dir / "_citekey_migration_ledger.json").write_text(
            "{not valid json", encoding="utf-8",
        )
        out = write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir)
        fields, _ = _parse_frontmatter(out.read_text(encoding="utf-8"))
        assert fields["citekey_migrated_count"] == "untracked"
