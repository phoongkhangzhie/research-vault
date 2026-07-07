"""test_pr_cc2_repro_determinism.py — PR-CC-2: tolerance-taxonomy field (D-CC-2).

Design: docs/superpowers/specs/2026-07-07-code-conventions-design.md §2.4 / §3
CHECK-4b / §8 PR-CC-2.

CHECK-4b is a schema/consumer hook only in this PR — no golden-rerun runner is
built here (soft/deferred per the design). This file proves:
  1. A freshly scaffolded experiments note carries `repro_determinism: exact`.
  2. `repro_determinism` is excluded from REPRO_LINT_REQUIRED (the sentinel
     lint must never flag it — it scaffolds to a complete default, not a hole).
  3. `rv note check` (cmd_check) is green on a fresh scaffold.
  4. A note declaring `tol:1e-6` or `stochastic` validates (no gate rejects it
     — the static gate does not assert a value per CHECK-4b).
  5. `repro_determinism` is present among REPRO_ALL_FIELDS (schema landed).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from research_vault import note as note_mod
from research_vault.config import load_config
from research_vault.note import (
    REPRO_ALL_FIELDS,
    REPRO_LINT_REQUIRED,
    REPRO_SENTINEL,
    _parse_frontmatter,
    check_repro_sentinel_lint,
    cmd_check,
    cmd_new,
)


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class TestSchemaLanded:
    def test_repro_determinism_in_all_fields(self):
        assert "repro_determinism" in REPRO_ALL_FIELDS

    def test_repro_determinism_not_in_lint_required(self):
        """The field is a complete default, not a fabrication-risk hole — it
        must never contribute sentinel-lint noise."""
        assert "repro_determinism" not in REPRO_LINT_REQUIRED


class TestScaffoldDefault:
    def test_fresh_note_scaffolds_exact_default(self, tmp_instance):
        cfg = load_config(reload=True)
        path = cmd_new("demo-research", "experiments", "CC2 Exp", config=cfg)
        text = path.read_text()
        fields, _ = _parse_frontmatter(text)
        assert fields.get("repro_determinism") == "exact"

    def test_fresh_note_default_is_not_the_sentinel(self, tmp_instance):
        """repro_determinism must NOT default to REPRO_SENTINEL like the other
        21 completeness fields — a default of 'exact' is complete, not a hole."""
        cfg = load_config(reload=True)
        path = cmd_new("demo-research", "experiments", "CC2 Exp 2", config=cfg)
        fields, _ = _parse_frontmatter(path.read_text())
        assert fields.get("repro_determinism") != REPRO_SENTINEL

    def test_doc_comment_names_the_taxonomy(self, tmp_instance):
        cfg = load_config(reload=True)
        path = cmd_new("demo-research", "experiments", "CC2 Exp 3", config=cfg)
        text = path.read_text()
        assert "repro_determinism" in text
        assert "tol:" in text
        assert "stochastic" in text


class TestSentinelLintUnaffected:
    def test_lint_does_not_fire_on_determinism_default(self, tmp_instance, tmp_path):
        """A note with results claimed, all REPRO_LINT_REQUIRED fields filled,
        and repro_determinism at its 'exact' default → no lint warnings
        mentioning repro_determinism (it's not a REQUIRED field)."""
        artifact = tmp_path / "exp.results.json"
        artifact.write_bytes(b'{"accuracy": 0.9}')
        results_hash = _sha256(artifact.read_bytes())

        note_path = tmp_path / "exp-filled.md"
        lines = [
            "---",
            "type: experiments",
            f"results_location: {artifact}",
            f"results_hash: {results_hash}",
        ]
        for field in REPRO_LINT_REQUIRED:
            lines.append(f"{field}: some-real-value")
        lines.append("repro_determinism: exact")
        lines += ["---", ""]
        note_path.write_text("\n".join(lines), encoding="utf-8")

        warnings = check_repro_sentinel_lint(note_path)
        assert warnings == []
        assert not any("repro_determinism" in w for w in warnings)

    def test_lint_ignores_determinism_even_when_absent(self, tmp_instance, tmp_path):
        """Omitting repro_determinism entirely (legacy note, pre-PR-CC-2) must
        not trip the lint — it was never a REQUIRED field."""
        artifact = tmp_path / "exp.results.json"
        artifact.write_bytes(b'{"accuracy": 0.8}')
        results_hash = _sha256(artifact.read_bytes())

        note_path = tmp_path / "exp-legacy.md"
        lines = [
            "---",
            "type: experiments",
            f"results_location: {artifact}",
            f"results_hash: {results_hash}",
        ]
        for field in REPRO_LINT_REQUIRED:
            lines.append(f"{field}: some-real-value")
        # deliberately no repro_determinism line at all
        lines += ["---", ""]
        note_path.write_text("\n".join(lines), encoding="utf-8")

        warnings = check_repro_sentinel_lint(note_path)
        assert warnings == []


class TestNoteCheckGreenOnScaffold:
    def test_rv_note_check_green_on_fresh_scaffold(self, tmp_instance):
        cfg = load_config(reload=True)
        cmd_new("demo-research", "experiments", "CC2 Green", config=cfg)
        violations = cmd_check("demo-research", config=cfg)
        repro_determinism_hits = [v for v in violations if "repro_determinism" in v]
        assert repro_determinism_hits == []


class TestTaxonomyValuesValidate:
    """The static gate does not assert a value (CHECK-4b) — any of the three
    taxonomy values must pass rv note check untouched."""

    def _write_note_with_determinism(self, base: Path, value: str) -> Path:
        base.mkdir(parents=True, exist_ok=True)
        p = base / f"exp-{value.replace(':', '-')}.md"
        lines = ["---", "type: experiments", "title: t", "created: 2026-07-01"]
        for field in REPRO_ALL_FIELDS:
            if field == "repro_determinism":
                lines.append(f"repro_determinism: {value}")
            else:
                lines.append(f"{field}: {REPRO_SENTINEL}")
        lines += ["---", "", "<!-- note -->", ""]
        p.write_text("\n".join(lines), encoding="utf-8")
        return p

    def test_tol_eps_value_validates(self, tmp_instance):
        cfg = load_config(reload=True)
        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        self._write_note_with_determinism(exp_dir, "tol:1e-6")
        violations = cmd_check("demo-research", config=cfg)
        assert not any("repro_determinism" in v for v in violations)

    def test_stochastic_value_validates(self, tmp_instance):
        cfg = load_config(reload=True)
        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        self._write_note_with_determinism(exp_dir, "stochastic")
        violations = cmd_check("demo-research", config=cfg)
        assert not any("repro_determinism" in v for v in violations)

    def test_exact_value_validates(self, tmp_instance):
        cfg = load_config(reload=True)
        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        self._write_note_with_determinism(exp_dir, "exact")
        violations = cmd_check("demo-research", config=cfg)
        assert not any("repro_determinism" in v for v in violations)
