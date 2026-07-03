"""test_sr_ms_audience.py — SR-MS-AUDIENCE: audience filter + Layer-1 body leak-scan + title node.

Covers:
  1. check_body_leakage BLOCKs on sha256 hex in body tex
  2. check_body_leakage BLOCKs on covers_hash/results_hash/run_id/dag_run token in body tex
  3. check_body_leakage BLOCKs on not-recorded-in-provenance sentinel in body tex
  4. check_body_leakage BLOCKs on results/foo.csv path in body tex
  5. check_body_leakage BLOCKs on absolute /Users/ path in body tex
  6. check_body_leakage passes clean body — zero hits
  7. check_body_leakage does NOT block on sha256 inside appendix-repro.tex (zone-2 exclusion)
  8. check_body_leakage does NOT block on same tokens inside data-code-availability.tex (zone-2)
  9. title guard: \\title{ms-foo-draft} → BLOCK
  10. title guard: \\title{A Curated Reader Title} → passes
  11. title guard: \\title{dag_run_value} → BLOCK (matches dag_run token)
  12. _is_proxy_study: all empty results_location → True
  13. _is_proxy_study: populated results_location → False
  14. _is_proxy_study: >threshold sentinel fraction → True
  15. inject_appendix proxy study → reframe paragraph (no sentinel wall)
  16. inject_appendix real run (one honest gap) → table still renders with sentinel row
  17. _sanitize_appendix_value: filesystem path → available on request
  18. _sanitize_appendix_value: sha256 hash → passes through unchanged
  19. _sanitize_appendix_value: normal value → passes through unchanged
  20. build_approve_payload has 7th section body_leakage
  21. build_approve_payload has 8th section title_candidates
  22. DAG manifest has title node afterok abstract
  23. DAG manifest has cold-read node afterok compile
  24. approve-manuscript node has both critic and cold-read as needs
  25. check_manuscript wires check_body_leakage (returns errors on leak)
  26. verbs parser --cold-read flag exists on check subcommand
  27. SECTION_KEYS contains title and cold-read (16→18 widening)

All hermetic (tmp_path). No live LLM calls. Stdlib only.
sr: SR-MS-AUDIENCE
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ms_tree(tmp_path: Path, ms_id: str = "ms-test") -> tuple[Path, Path]:
    """Create a minimal manuscript tree for testing."""
    ms_dir = tmp_path / "manuscript"
    ms_dir.mkdir(parents=True, exist_ok=True)
    note_path = ms_dir / f"{ms_id}.md"
    note_path.write_text(
        "---\ntype: manuscript\nthesis: Test thesis\nsynthesized_okf: \nmanuscript_pdf: \ndag_run: ms-ms-test-draft\n---\n",
        encoding="utf-8",
    )
    tree_root = tmp_path / "manuscripts" / ms_id
    sections_dir = tree_root / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)
    # Write a clean main.tex stub
    (tree_root / "main.tex").write_text(
        "\\documentclass{article}\n\\title{A Curated Reader Title}\n\\begin{document}\n\\end{document}\n",
        encoding="utf-8",
    )
    return note_path, tree_root


def _write_section(tree_root: Path, section: str, content: str) -> Path:
    """Write a .tex section file."""
    p = tree_root / "sections" / f"{section}.tex"
    p.write_text(content, encoding="utf-8")
    return p


def _make_experiment_note(tmp_path: Path, note_id: str, fields: dict[str, str]) -> Path:
    """Write an experiment note with given fields."""
    exp_dir = tmp_path / "experiments"
    exp_dir.mkdir(parents=True, exist_ok=True)
    note_path = exp_dir / f"{note_id}.md"
    front = "---\n"
    front += "type: experiment\n"
    for k, v in fields.items():
        front += f"{k}: {v}\n"
    front += "---\n"
    note_path.write_text(front, encoding="utf-8")
    return note_path


# ---------------------------------------------------------------------------
# Tests: check_body_leakage
# ---------------------------------------------------------------------------

class TestCheckBodyLeakage:
    """Layer-1 hermetic body leak-scan."""

    def test_blocks_on_sha256_hex_in_body(self, tmp_path: Path) -> None:
        """A 64-char hex string prefixed sha256: in body tex → BLOCK."""
        from research_vault.manuscript.check_gates import check_body_leakage

        _, tree_root = _make_ms_tree(tmp_path)
        hex64 = "a" * 64
        _write_section(tree_root, "results-discussion", f"Our results (sha256:{hex64}) confirm.")
        errors = check_body_leakage(tree_root)
        assert errors, "Expected BLOCK on sha256 hex in body tex"
        assert any("sha256" in e.lower() or hex64[:8] in e for e in errors), errors

    def test_blocks_on_bare_64hex_in_body(self, tmp_path: Path) -> None:
        """A bare 64-char hex run in body tex → BLOCK."""
        from research_vault.manuscript.check_gates import check_body_leakage

        _, tree_root = _make_ms_tree(tmp_path)
        hex64 = "b" * 64
        _write_section(tree_root, "results-discussion", f"Hash: {hex64} was verified.")
        errors = check_body_leakage(tree_root)
        assert errors, "Expected BLOCK on bare 64-hex in body"

    def test_blocks_on_covers_hash_token(self, tmp_path: Path) -> None:
        """The literal token 'covers_hash' in body tex → BLOCK."""
        from research_vault.manuscript.check_gates import check_body_leakage

        _, tree_root = _make_ms_tree(tmp_path)
        _write_section(tree_root, "method", "Provenance tracked via covers_hash field.")
        errors = check_body_leakage(tree_root)
        assert errors, "Expected BLOCK on covers_hash token"
        assert any("covers_hash" in e for e in errors), errors

    def test_blocks_on_results_hash_token(self, tmp_path: Path) -> None:
        """The literal token 'results_hash' in body tex → BLOCK."""
        from research_vault.manuscript.check_gates import check_body_leakage

        _, tree_root = _make_ms_tree(tmp_path)
        _write_section(tree_root, "method", "The results_hash was verified before submission.")
        errors = check_body_leakage(tree_root)
        assert errors, "Expected BLOCK on results_hash token"

    def test_blocks_on_not_recorded_sentinel(self, tmp_path: Path) -> None:
        """The sentinel 'not-recorded-in-provenance' in body tex → BLOCK."""
        from research_vault.manuscript.check_gates import check_body_leakage

        _, tree_root = _make_ms_tree(tmp_path)
        _write_section(tree_root, "experimental-setup", "Seed: not-recorded-in-provenance.")
        errors = check_body_leakage(tree_root)
        assert errors, "Expected BLOCK on not-recorded-in-provenance sentinel"

    def test_blocks_on_results_csv_path(self, tmp_path: Path) -> None:
        """A 'results/foo.csv' artifact path in body tex → BLOCK."""
        from research_vault.manuscript.check_gates import check_body_leakage

        _, tree_root = _make_ms_tree(tmp_path)
        _write_section(tree_root, "results-discussion", "Data from results/hfs-scores.csv analyzed.")
        errors = check_body_leakage(tree_root)
        assert errors, "Expected BLOCK on results/*.csv path in body"

    def test_blocks_on_absolute_users_path(self, tmp_path: Path) -> None:
        """An absolute /Users/ path in body tex → BLOCK."""
        from research_vault.manuscript.check_gates import check_body_leakage

        _, tree_root = _make_ms_tree(tmp_path)
        _write_section(tree_root, "method", "Artifacts stored at /Users/researcher/results/exp.csv.")
        errors = check_body_leakage(tree_root)
        assert errors, "Expected BLOCK on absolute /Users/ path in body"

    def test_clean_body_passes(self, tmp_path: Path) -> None:
        """A clean body with no provenance leaks → zero errors."""
        from research_vault.manuscript.check_gates import check_body_leakage

        _, tree_root = _make_ms_tree(tmp_path)
        _write_section(
            tree_root,
            "results-discussion",
            "The model achieved \\resultAccHFS accuracy, a significant improvement.",
        )
        errors = check_body_leakage(tree_root)
        assert not errors, f"Expected zero errors on clean body, got: {errors}"

    def test_zone2_appendix_repro_not_blocked(self, tmp_path: Path) -> None:
        """sha256 hex inside appendix-repro.tex (zone-2) does NOT trigger BLOCK."""
        from research_vault.manuscript.check_gates import check_body_leakage

        _, tree_root = _make_ms_tree(tmp_path)
        hex64 = "c" * 64
        # Write the leak into the APPENDIX (zone-2), not the body
        _write_section(tree_root, "appendix-repro", f"Config hash: sha256:{hex64}")
        # Body is clean
        _write_section(tree_root, "results-discussion", "Results show improvement.")
        errors = check_body_leakage(tree_root)
        assert not errors, (
            f"appendix-repro.tex is zone-2 — sha256 hex should NOT be flagged there. Errors: {errors}"
        )

    def test_zone2_data_code_availability_not_blocked(self, tmp_path: Path) -> None:
        """sha256 hex inside data-code-availability.tex (zone-2) does NOT trigger BLOCK."""
        from research_vault.manuscript.check_gates import check_body_leakage

        _, tree_root = _make_ms_tree(tmp_path)
        hex64 = "d" * 64
        _write_section(tree_root, "data-code-availability", f"Verified by sha256:{hex64}")
        _write_section(tree_root, "results-discussion", "Results show improvement.")
        errors = check_body_leakage(tree_root)
        assert not errors, (
            f"data-code-availability.tex is zone-2 — sha256 hex should NOT be flagged. Errors: {errors}"
        )

    def test_same_token_body_and_appendix_blocks_for_body(self, tmp_path: Path) -> None:
        """sha256 in BOTH body and appendix-repro → BLOCK (body is flagged, not appendix)."""
        from research_vault.manuscript.check_gates import check_body_leakage

        _, tree_root = _make_ms_tree(tmp_path)
        hex64 = "e" * 64
        # Leak in body
        _write_section(tree_root, "results-discussion", f"sha256:{hex64} verified.")
        # Also in appendix (zone-2)
        _write_section(tree_root, "appendix-repro", f"sha256:{hex64} for repro.")
        errors = check_body_leakage(tree_root)
        assert errors, "Body has sha256 leak — should BLOCK"
        # The error should point to results-discussion, not appendix-repro
        assert any("results-discussion" in e or "body" in e.lower() for e in errors), errors


# ---------------------------------------------------------------------------
# Tests: title guard (structural)
# ---------------------------------------------------------------------------

class TestTitleGuard:
    """Structural title guard in check_body_leakage."""

    def test_title_is_ms_id_blocks(self, tmp_path: Path) -> None:
        """\\title{ms-foo-draft} (matches run-id shape) → BLOCK."""
        from research_vault.manuscript.check_gates import check_body_leakage

        _, tree_root = _make_ms_tree(tmp_path, "ms-foo")
        # main.tex with title that is the ms_id run name
        (tree_root / "main.tex").write_text(
            "\\documentclass{article}\n\\title{ms-foo-draft}\n\\begin{document}\n\\end{document}\n",
            encoding="utf-8",
        )
        errors = check_body_leakage(tree_root)
        assert errors, "title == run-id shape should BLOCK"
        assert any("title" in e.lower() for e in errors), errors

    def test_title_is_dag_run_blocks(self, tmp_path: Path) -> None:
        """\\title{ms-ms-test-draft} (matches dag_run value) → BLOCK."""
        from research_vault.manuscript.check_gates import check_body_leakage

        note_path, tree_root = _make_ms_tree(tmp_path, "ms-test")
        # The dag_run in the note is 'ms-ms-test-draft' (from the note frontmatter)
        (tree_root / "main.tex").write_text(
            "\\documentclass{article}\n\\title{ms-ms-test-draft}\n\\begin{document}\n\\end{document}\n",
            encoding="utf-8",
        )
        errors = check_body_leakage(tree_root, note_path=note_path)
        assert errors, "title == dag_run value should BLOCK"
        assert any("title" in e.lower() for e in errors), errors

    def test_curated_title_passes(self, tmp_path: Path) -> None:
        """\\title{Cross-Lingual Cultural Competence in LLMs} → passes."""
        from research_vault.manuscript.check_gates import check_body_leakage

        _, tree_root = _make_ms_tree(tmp_path)
        (tree_root / "main.tex").write_text(
            "\\documentclass{article}\n\\title{Cross-Lingual Cultural Competence in LLMs}\n\\begin{document}\n\\end{document}\n",
            encoding="utf-8",
        )
        errors = check_body_leakage(tree_root)
        assert not errors, f"Curated title should pass, got: {errors}"

    def test_ms_slug_shape_in_title_blocks(self, tmp_path: Path) -> None:
        """\\title{my-paper-a3b} (slug-with-hash shape) → BLOCK."""
        from research_vault.manuscript.check_gates import check_body_leakage

        _, tree_root = _make_ms_tree(tmp_path)
        (tree_root / "main.tex").write_text(
            "\\documentclass{article}\n\\title{my-paper-a3b}\n\\begin{document}\n\\end{document}\n",
            encoding="utf-8",
        )
        errors = check_body_leakage(tree_root)
        assert errors, "Slug-with-hash title shape should BLOCK"


# ---------------------------------------------------------------------------
# Tests: proxy-study appendix reframe
# ---------------------------------------------------------------------------

class TestProxyStudyReframe:
    """Audience filter: proxy-study appendix reframe."""

    def test_is_proxy_study_all_empty_results_location(self, tmp_path: Path) -> None:
        """All experiment notes with empty results_location → proxy study."""
        from research_vault.manuscript.appendix import _is_proxy_study

        notes = [
            _make_experiment_note(tmp_path, "exp-a", {"results_location": ""}),
            _make_experiment_note(tmp_path, "exp-b", {"results_location": ""}),
        ]
        assert _is_proxy_study(notes), "All-empty results_location should be a proxy study"

    def test_is_proxy_study_populated_results_location(self, tmp_path: Path) -> None:
        """Experiment notes with populated results_location → NOT proxy study."""
        from research_vault.manuscript.appendix import _is_proxy_study

        notes = [
            _make_experiment_note(tmp_path, "exp-c", {"results_location": "/data/results.csv"}),
        ]
        assert not _is_proxy_study(notes), "Populated results_location should NOT be proxy study"

    def test_is_proxy_study_mixed_fields_above_threshold(self, tmp_path: Path) -> None:
        """Notes with >60% required fields at sentinel → proxy study (threshold met)."""
        from research_vault.manuscript.appendix import _is_proxy_study
        from research_vault.note import REPRO_SENTINEL

        # Note with results_location empty and nearly all sentinel fields
        sentinel_fields = {
            "results_location": "",
            "repro_seed": REPRO_SENTINEL,
            "repro_model_id": REPRO_SENTINEL,
            "repro_model_revision": REPRO_SENTINEL,
            "repro_decode_temperature": REPRO_SENTINEL,
            "repro_decode_top_p": REPRO_SENTINEL,
        }
        notes = [_make_experiment_note(tmp_path, "exp-proxy", sentinel_fields)]
        assert _is_proxy_study(notes), "High sentinel fraction should trigger proxy reframe"

    def test_inject_appendix_proxy_study_reframes(self, tmp_path: Path) -> None:
        """Proxy study (all empty results_location) → reframe paragraph, no sentinel wall."""
        from research_vault.manuscript.appendix import inject_appendix

        tree_root = tmp_path / "manuscripts" / "ms-test"
        sections_dir = tree_root / "sections"
        sections_dir.mkdir(parents=True, exist_ok=True)

        notes = [
            _make_experiment_note(tmp_path, "exp-noop", {"results_location": ""}),
        ]
        result_path = inject_appendix(tree_root, notes)
        content = result_path.read_text(encoding="utf-8")

        # Should NOT have a table with sentinel wall
        assert "not recorded in provenance" not in content.lower() or \
            "re-analysis" in content.lower() or "proxy" in content.lower(), (
            "Proxy study should emit reframe paragraph, not a sentinel wall"
        )
        # Should have a reframe statement
        assert any(phrase in content.lower() for phrase in [
            "re-analysis", "proxy", "no new runs", "not applicable",
            "no experimental runs", "conceptual", "published aggregates",
        ]), f"Expected reframe paragraph in proxy appendix, got:\n{content}"
        # Should NOT be a LaTeX table
        assert "\\begin{table}" not in content, (
            "Proxy study appendix should not contain a LaTeX table"
        )

    def test_inject_appendix_real_run_renders_table(self, tmp_path: Path) -> None:
        """Real run (non-empty results_location) → table still renders (one honest gap ok)."""
        from research_vault.manuscript.appendix import inject_appendix
        from research_vault.note import REPRO_SENTINEL

        tree_root = tmp_path / "manuscripts" / "ms-real"
        sections_dir = tree_root / "sections"
        sections_dir.mkdir(parents=True, exist_ok=True)

        # One experiment with actual results but one honest gap
        fields = {
            "results_location": "/data/results.csv",
            "repro_seed": "42",
            "repro_model_id": "gpt-4",
            "repro_model_revision": REPRO_SENTINEL,  # honest gap
        }
        notes = [_make_experiment_note(tmp_path, "exp-real", fields)]
        result_path = inject_appendix(tree_root, notes)
        content = result_path.read_text(encoding="utf-8")

        # Should have a table
        assert "\\begin{table}" in content, "Real run should produce a reproducibility table"
        # The honest gap (sentinel) should still render
        assert "not recorded in provenance" in content.lower(), (
            "Honest gap in a real run should still render as 'not recorded in provenance'"
        )


# ---------------------------------------------------------------------------
# Tests: _sanitize_appendix_value
# ---------------------------------------------------------------------------

class TestSanitizeAppendixValue:
    """Path→identifier sanitization for appendix values."""

    def test_filesystem_path_becomes_available_on_request(self) -> None:
        """A local filesystem path → 'available on request'."""
        from research_vault.manuscript.appendix import _sanitize_appendix_value

        result = _sanitize_appendix_value("repro_config_location", "/Users/researcher/exp/config.json")
        assert "available on request" in result or "available" in result.lower(), (
            f"Expected 'available on request' for filesystem path, got: {result!r}"
        )

    def test_results_path_becomes_available_on_request(self) -> None:
        """A results/ path → 'available on request'."""
        from research_vault.manuscript.appendix import _sanitize_appendix_value

        result = _sanitize_appendix_value("repro_config_location", "results/hfs-scores.csv")
        assert "available" in result.lower(), f"Expected sanitized output for results/ path: {result!r}"

    def test_sha256_hash_passes_through(self) -> None:
        """A sha256 hash value passes through unchanged (verification anchor)."""
        from research_vault.manuscript.appendix import _sanitize_appendix_value

        sha_val = "sha256:" + "a" * 64
        result = _sanitize_appendix_value("repro_config_hash", sha_val)
        assert sha_val in result or "sha256" in result.lower(), (
            f"Hash should pass through for appendix (verification anchor): {result!r}"
        )

    def test_normal_value_passes_through(self) -> None:
        """A normal field value (seed, model ID) passes through unchanged."""
        from research_vault.manuscript.appendix import _sanitize_appendix_value

        result = _sanitize_appendix_value("repro_seed", "42")
        assert "42" in result, f"Normal value should pass through: {result!r}"

    def test_dataset_id_passes_through(self) -> None:
        """A dataset identifier (DOI, URL) passes through unchanged."""
        from research_vault.manuscript.appendix import _sanitize_appendix_value

        doi = "10.1234/dataset.2024"
        result = _sanitize_appendix_value("repro_dataset_id", doi)
        assert doi in result or "10.1234" in result, f"DOI should pass through: {result!r}"


# ---------------------------------------------------------------------------
# Tests: build_approve_payload (payload sections 7 + 8)
# ---------------------------------------------------------------------------

class TestApprovePayloadExtensions:
    """build_approve_payload has body_leakage (7th) and title_candidates (8th) sections."""

    def _mock_judge(self, prompt: str) -> str:
        """Mock judge that returns a minimal SUPPORTS response."""
        return "[SUPPORTS] — The source directly supports the claim.\nSUMMARY: Clean."

    def test_build_approve_payload_has_body_leakage_key(self, tmp_path: Path) -> None:
        """build_approve_payload returns a 'body_leakage' key with leak results."""
        from research_vault.manuscript.check_gates import build_approve_payload

        note_path, tree_root = _make_ms_tree(tmp_path)
        # Write a body section with a leak
        hex64 = "f" * 64
        _write_section(tree_root, "results-discussion", f"Hash sha256:{hex64} verified.")

        payload = build_approve_payload(
            note_path, tree_root, judge_fn=self._mock_judge
        )
        assert "body_leakage" in payload, (
            f"build_approve_payload missing 'body_leakage' key. Keys: {list(payload.keys())}"
        )
        # The leakage section should report the leak
        leakage = payload["body_leakage"]
        assert isinstance(leakage, list), f"body_leakage should be a list, got {type(leakage)}"

    def test_build_approve_payload_has_title_candidates_key(self, tmp_path: Path) -> None:
        """build_approve_payload returns a 'title_candidates' key."""
        from research_vault.manuscript.check_gates import build_approve_payload

        note_path, tree_root = _make_ms_tree(tmp_path)
        payload = build_approve_payload(
            note_path, tree_root, judge_fn=self._mock_judge
        )
        assert "title_candidates" in payload, (
            f"build_approve_payload missing 'title_candidates' key. Keys: {list(payload.keys())}"
        )

    def test_body_leakage_blocks_propagate_to_errors(self, tmp_path: Path) -> None:
        """Body leaks in build_approve_payload → all_ok=False, errors populated."""
        from research_vault.manuscript.check_gates import build_approve_payload

        note_path, tree_root = _make_ms_tree(tmp_path)
        hex64 = "abcdef1234567890" * 4  # 64-char hex
        _write_section(tree_root, "results-discussion", f"sha256:{hex64} verified.")

        payload = build_approve_payload(
            note_path, tree_root, judge_fn=self._mock_judge
        )
        assert not payload["all_ok"], "Body leak should make all_ok=False"
        assert any("sha256" in e.lower() or "leak" in e.lower() for e in payload["errors"]), (
            f"Expected body leak in errors: {payload['errors']}"
        )


# ---------------------------------------------------------------------------
# Tests: check_manuscript wires check_body_leakage
# ---------------------------------------------------------------------------

class TestCheckManuscriptBodyLeakage:
    """check_manuscript (structural gates) includes body leak-scan."""

    def test_check_manuscript_returns_error_on_body_leak(self, tmp_path: Path) -> None:
        """check_manuscript with body hash leak → error in result."""
        from research_vault.manuscript.check_gates import check_manuscript

        note_path, tree_root = _make_ms_tree(tmp_path)
        hex64 = "a" * 64
        _write_section(tree_root, "results-discussion", f"sha256:{hex64} confirmed.")

        result = check_manuscript(note_path, tree_root)
        assert not result["all_ok"], "Body leak should fail check_manuscript"
        assert any("sha256" in e.lower() or "leak" in e.lower() for e in result["errors"]), (
            f"Expected body leak error in check_manuscript: {result['errors']}"
        )

    def test_check_manuscript_clean_body_passes(self, tmp_path: Path) -> None:
        """check_manuscript with clean body (no leaks) → passes (no extra errors)."""
        from research_vault.manuscript.check_gates import check_manuscript

        note_path, tree_root = _make_ms_tree(tmp_path)
        _write_section(tree_root, "results-discussion", "The model achieved \\resultAcc accuracy.")

        result = check_manuscript(note_path, tree_root)
        # No body leakage errors (other gates may warn but should not add body-leak errors)
        leak_errors = [e for e in result["errors"] if "leak" in e.lower() or "covers_hash" in e.lower()
                       or "sha256" in e.lower()]
        assert not leak_errors, f"Clean body should not produce body-leak errors: {leak_errors}"


# ---------------------------------------------------------------------------
# Tests: DAG manifest — title and cold-read nodes
# ---------------------------------------------------------------------------

class TestDagManifestNodes:
    """DAG manifest has title and cold-read nodes (16→18 widening)."""

    def _build_manifest_default(self, tmp_path: Path) -> dict[str, Any]:
        """Build default manifest using cmd_new helper."""
        from research_vault.manuscript import cmd_new
        from research_vault.config import Config, _default_config, _expand_paths, _merge

        project = "test-proj"
        notes_dir = tmp_path / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)

        # Build config using the standard merge+expand pipeline
        override: dict[str, Any] = {
            "instance_root": str(tmp_path),
            "notes_root": str(notes_dir),
            "projects": {
                project: {
                    "name": project,
                    "notes": str(notes_dir),
                }
            },
        }
        defaults = _default_config()
        merged = _merge(defaults, override)
        expanded = _expand_paths(merged, tmp_path)
        cfg = Config(expanded)
        _, _, manifest = cmd_new(
            project,
            "ms-001",
            thesis="Test thesis for audience filter.",
            scope=[],
            config=cfg,
        )
        return manifest

    def test_title_node_in_manifest(self, tmp_path: Path) -> None:
        """DAG manifest contains a 'title' agent node."""
        manifest = self._build_manifest_default(tmp_path)
        node_ids = [n["id"] for n in manifest["nodes"]]
        assert "title" in node_ids, (
            f"Expected 'title' node in manifest. Node ids: {node_ids}"
        )

    def test_cold_read_node_in_manifest(self, tmp_path: Path) -> None:
        """DAG manifest contains a 'cold-read' agent node."""
        manifest = self._build_manifest_default(tmp_path)
        node_ids = [n["id"] for n in manifest["nodes"]]
        assert "cold-read" in node_ids, (
            f"Expected 'cold-read' node in manifest. Node ids: {node_ids}"
        )

    def test_title_node_afterok_abstract(self, tmp_path: Path) -> None:
        """'title' node is afterok 'abstract'."""
        manifest = self._build_manifest_default(tmp_path)
        title_node = next((n for n in manifest["nodes"] if n["id"] == "title"), None)
        assert title_node is not None, "title node must exist"
        needs_from = [n.get("from") for n in title_node.get("needs", [])]
        assert "abstract" in needs_from, (
            f"'title' node must be afterok 'abstract'. needs: {title_node.get('needs')}"
        )

    def test_cold_read_node_afterok_compile(self, tmp_path: Path) -> None:
        """'cold-read' node is afterok 'compile'."""
        manifest = self._build_manifest_default(tmp_path)
        cr_node = next((n for n in manifest["nodes"] if n["id"] == "cold-read"), None)
        assert cr_node is not None, "cold-read node must exist"
        needs_from = [n.get("from") for n in cr_node.get("needs", [])]
        assert "compile" in needs_from, (
            f"'cold-read' node must be afterok 'compile'. needs: {cr_node.get('needs')}"
        )

    def test_approve_manuscript_needs_cold_read(self, tmp_path: Path) -> None:
        """'approve-manuscript' node depends on both 'critic' and 'cold-read'."""
        manifest = self._build_manifest_default(tmp_path)
        gate3 = next((n for n in manifest["nodes"] if n["id"] == "approve-manuscript"), None)
        assert gate3 is not None, "approve-manuscript must exist"
        needs_from = [n.get("from") for n in gate3.get("needs", [])]
        assert "cold-read" in needs_from, (
            f"approve-manuscript must depend on cold-read. needs: {gate3.get('needs')}"
        )

    def test_manifest_has_18_or_more_nodes(self, tmp_path: Path) -> None:
        """Default manifest has ≥18 nodes (16→18 widening: +title +cold-read)."""
        manifest = self._build_manifest_default(tmp_path)
        n = len(manifest["nodes"])
        assert n >= 18, (
            f"Expected ≥18 nodes after 16→18 widening (title + cold-read). Got {n}: "
            + ", ".join(nd["id"] for nd in manifest["nodes"])
        )


# ---------------------------------------------------------------------------
# Tests: SECTION_KEYS / SECTION_STATUS widening
# ---------------------------------------------------------------------------

class TestSectionKeysWidening:
    """SECTION_KEYS and SECTION_STATUS contain title and cold-read."""

    def test_section_keys_contains_title(self) -> None:
        """SECTION_KEYS contains 'title'."""
        from research_vault.manuscript.style import SECTION_KEYS
        assert "title" in SECTION_KEYS, f"'title' missing from SECTION_KEYS: {SECTION_KEYS}"

    def test_section_keys_contains_cold_read(self) -> None:
        """SECTION_KEYS contains 'cold-read'."""
        from research_vault.manuscript.style import SECTION_KEYS
        assert "cold-read" in SECTION_KEYS, f"'cold-read' missing from SECTION_KEYS: {SECTION_KEYS}"

    def test_section_status_contains_title(self) -> None:
        """SECTION_STATUS contains 'title'."""
        from research_vault.manuscript.style import SECTION_STATUS
        assert "title" in SECTION_STATUS, f"'title' missing from SECTION_STATUS"

    def test_section_status_contains_cold_read(self) -> None:
        """SECTION_STATUS contains 'cold-read'."""
        from research_vault.manuscript.style import SECTION_STATUS
        assert "cold-read" in SECTION_STATUS, f"'cold-read' missing from SECTION_STATUS"

    def test_per_section_tips_contains_title(self) -> None:
        """per_section_tips contains an entry for 'title'."""
        from research_vault.manuscript.style import per_section_tips
        assert "title" in per_section_tips, f"'title' missing from per_section_tips"
        assert len(per_section_tips["title"]) > 10, "title tip should be non-trivial"

    def test_per_section_tips_contains_cold_read(self) -> None:
        """per_section_tips contains an entry for 'cold-read'."""
        from research_vault.manuscript.style import per_section_tips
        assert "cold-read" in per_section_tips, f"'cold-read' missing from per_section_tips"


# ---------------------------------------------------------------------------
# Tests: verbs --cold-read flag
# ---------------------------------------------------------------------------

class TestVerbsColdReadFlag:
    """rv manuscript check --cold-read flag exists."""

    def test_check_cold_read_flag_parseable(self) -> None:
        """build_parser supports --cold-read on the check subcommand."""
        from research_vault.manuscript.verbs import build_parser

        p = build_parser()
        args = p.parse_args(["test-proj", "check", "ms-001", "--cold-read"])
        assert getattr(args, "cold_read", None) is True, (
            "Expected --cold-read to set cold_read=True on parsed args"
        )

    def test_check_without_cold_read_defaults_false(self) -> None:
        """check without --cold-read has cold_read=False."""
        from research_vault.manuscript.verbs import build_parser

        p = build_parser()
        args = p.parse_args(["test-proj", "check", "ms-001"])
        assert getattr(args, "cold_read", False) is False, (
            "Expected cold_read=False when --cold-read is not passed"
        )
