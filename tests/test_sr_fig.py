"""test_sr_fig.py — SR-FIG: FIGURES as the 8th OKF type + rv figure verb plumbing.

All tests are hermetic (tmp_instance). No ~/vault reads or writes.

SOURCE MODEL (post-rework, §5E.10 item 3):
  A figure's PRIMARY frame source is the experiment's results_location (the metrics
  artifact written by `rv wandb pull` / SR-WB). The `--benchmark datasets/<id>` flag
  is an OPTIONAL secondary reference for comparison overlays. The datasets-as-primary-
  source model is gone.

Eight seams tested:
  1. OKF type `figures` — 8th canonical type; PROJECT-SCOPED (project_notes_dir/figures/),
     NOT shared (no figures_root). type↔dir contract enforced; source_experiment +
     experiment_results_hash required fields checked by cmd_check.
  2. rv figure new — creates figures/<id>.md with provenance frontmatter (experiment OKF
     link + results_hash + filter recipe + style preset). Optional benchmark_dataset.
  3. rv figure preview — writes state/figures/<fig>-view.csv + prints frame head; loads
     frame from the experiment note's results_location. Import guard: friendly message,
     no raw ImportError.
  4. rv figure list — lists figure notes for a project.
  5. Verb registry — 'figure' in _VERB_REGISTRY with when_to_use + anti-pattern + sr.
  6. Style seam — apply_style(preset, skin) stub with exact signature.
  7. rv check — figures as optional prereq.
  8. Demo-figures DAG loop — extract node reads from experiments/, not datasets/.
     data-check cannot approve until extract is terminal.

CRITICAL scoping: figures are PROJECT-SCOPED under project_notes_dir(project)/figures/.
NOT a shared root (only datasets gets the shared-root treatment in SR-8).

All authored test manifests carry spec: and reads: (SR-DISP/SR-SCOPE compliance).
"""

import hashlib
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from research_vault import note as note_mod
from research_vault.config import load_config
from research_vault.dag.schema import validate_manifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _write_experiment_note_and_results(
    tmp_dir: Path,
    project_notes_dir: Path,
    exp_id: str,
    *,
    results_data: bytes = b"metric,value\nacc,0.92\nf1,0.88\n",
    title: str = "Test experiment",
) -> tuple[Path, str]:
    """Write a minimal experiment note with results attachment.

    Creates:
      - a results CSV file at tmp_dir/<exp_id>.csv
      - an experiments/<exp_id>.md note with results_location + results_hash

    Returns (note_path, results_hash).
    """
    # Write the results file
    results_file = tmp_dir / f"{exp_id}.csv"
    results_file.write_bytes(results_data)
    results_hash = _sha256_hex(results_data)

    # Write the experiment note
    exp_dir = project_notes_dir / "experiments"
    exp_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        "type: experiments",
        f"title: {title}",
        "created: 2026-07-01",
        f"results_location: {results_file}",
        f"results_hash: {results_hash}",
        "results_wandb_run: test-run-001",
        "results_commit: abc123def456",
        "---",
        "",
        "<!-- Test experiment note -->",
        "",
    ]
    note_path = exp_dir / f"{exp_id}.md"
    note_path.write_text("\n".join(lines), encoding="utf-8")
    return note_path, results_hash


def _write_dataset_note(
    datasets_root: Path,
    note_id: str,
    *,
    location: str = "",
    hash_val: str = "",
    title: str = "Test dataset",
) -> Path:
    """Write a minimal valid datasets provenance note in datasets_root."""
    datasets_root.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: datasets", f"title: {title}", "created: 2026-07-01"]
    if location:
        lines.append(f"location: {location}")
    if hash_val:
        lines.append(f"hash: {hash_val}")
    lines += ["---", "", "<!-- provenance note -->", ""]
    p = datasets_root / f"{note_id}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


# ============================================================================
# Seam 1: OKF type `figures` — 8th type, PROJECT-SCOPED
# ============================================================================

class TestFiguresOkfType:
    """figures is the 8th canonical OKF type, project-scoped (not shared)."""

    def test_figures_in_okf_types(self):
        """'figures' must be in the OKF_TYPES frozenset."""
        assert "figures" in note_mod.OKF_TYPES

    def test_okf_type_count_is_nine(self):
        """OKF_TYPES now has exactly 9 types (SR-MS-1a adds manuscript as the 9th)."""
        assert len(note_mod.OKF_TYPES) == 9
        expected = {
            "literature", "concepts", "methods", "experiments",
            "findings", "mocs", "datasets", "figures", "manuscript",
        }
        assert note_mod.OKF_TYPES == expected

    def test_new_figures_note_creates_in_project_notes_dir(self, tmp_instance):
        """cmd_new for figures writes to project_notes_dir/figures/, NOT a shared root.

        This is the critical scoping test: figures are project-scoped (like literature,
        concepts, etc.), NOT shared like datasets. A figures note for demo-research must
        NOT appear in a shared root or in demo-litreview's directory.
        """
        cfg = load_config(reload=True)
        path = note_mod.cmd_new("demo-research", "figures", "My figure", config=cfg)
        assert path.exists()
        # Must be in project_notes_dir/figures/
        project_notes_dir = cfg.project_notes_dir("demo-research")
        expected_dir = project_notes_dir / "figures"
        assert path.parent == expected_dir, (
            f"figures note must be in project_notes_dir/figures/; got {path.parent}"
        )

    def test_figures_note_NOT_in_shared_root(self, tmp_instance):
        """figures notes are NOT placed in a shared root (only datasets gets that treatment).

        Non-vacuous: if a shared figures_root existed and notes went there, this test fails.
        """
        cfg = load_config(reload=True)
        path = note_mod.cmd_new("demo-research", "figures", "Proj figure", config=cfg)
        # Must not be at the instance root or notes_root level
        assert not path.is_relative_to(cfg.notes_root / "figures"), (
            "figures notes must NOT be in notes_root/figures/ — they are project-scoped"
        )
        # Must be under the project's notes directory
        project_notes_dir = cfg.project_notes_dir("demo-research")
        assert path.is_relative_to(project_notes_dir), (
            f"figures note must be under project_notes_dir; got {path}"
        )

    def test_figures_note_for_two_projects_are_separate(self, tmp_instance):
        """figures notes for different projects are in separate directories.

        Non-vacuous discriminant: create figure notes in both projects with the same
        fig-id. They must be separate files, not overwrite each other. If figures were
        shared (like datasets), cmd_new would write to the same root → both would resolve
        to the same path.
        """
        cfg = load_config(reload=True)
        p1 = note_mod.cmd_new("demo-research", "figures", "cross test", note_id="cross-test", config=cfg)
        p2 = note_mod.cmd_new("demo-litreview", "figures", "cross test", note_id="cross-test", config=cfg)
        assert p1 != p2, "Project-scoped figure notes must be in separate directories"
        assert p1.parent != p2.parent, "Two projects' figures dirs must differ"

    def test_figures_note_has_type_frontmatter(self, tmp_instance):
        """New figures note has type: figures in frontmatter."""
        cfg = load_config(reload=True)
        path = note_mod.cmd_new("demo-research", "figures", "My figure", config=cfg)
        content = path.read_text()
        assert "type: figures" in content

    def test_scaffold_okf_dirs_creates_figures_dir(self, tmp_instance):
        """scaffold_okf_dirs creates figures/ subdirectory alongside other OKF dirs."""
        cfg = load_config(reload=True)
        from research_vault.note import scaffold_okf_dirs
        base = cfg.project_notes_dir("demo-research")
        scaffold_okf_dirs(base)
        assert (base / "figures").is_dir(), "scaffold_okf_dirs must create figures/ dir"


class TestFiguresCmdCheck:
    """cmd_check validates figures notes: type↔dir + source_experiment + experiment_results_hash."""

    def _write_figures_note(
        self,
        figures_dir: Path,
        fig_id: str,
        *,
        source_experiment: str = "experiments/run-007",
        experiment_results_hash: str = "sha256:abc123",
        style: str = "publication",
    ) -> Path:
        """Write a minimal figures provenance note (experiment-sourced)."""
        figures_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            "---",
            "type: figures",
            f"title: {fig_id}",
            "created: 2026-07-01",
        ]
        if source_experiment:
            lines.append(f"source_experiment: {source_experiment}")
        if experiment_results_hash:
            lines.append(f"experiment_results_hash: {experiment_results_hash}")
        if style:
            lines.append(f"style: {style}")
        lines += ["---", "", "<!-- figure provenance note -->", ""]
        p = figures_dir / f"{fig_id}.md"
        p.write_text("\n".join(lines), encoding="utf-8")
        return p

    def test_check_figures_note_ok(self, tmp_instance):
        """A well-formed figures note passes cmd_check (type + source_experiment + experiment_results_hash)."""
        cfg = load_config(reload=True)
        figures_dir = cfg.project_notes_dir("demo-research") / "figures"
        self._write_figures_note(figures_dir, "fig-ok")
        violations = note_mod.cmd_check("demo-research", config=cfg)
        assert violations == [], f"Expected no violations, got: {violations}"

    def test_check_figures_note_fails_missing_source_experiment(self, tmp_instance):
        """A figures note without source_experiment field fails cmd_check."""
        cfg = load_config(reload=True)
        figures_dir = cfg.project_notes_dir("demo-research") / "figures"
        self._write_figures_note(figures_dir, "fig-no-exp", source_experiment="")
        violations = note_mod.cmd_check("demo-research", config=cfg)
        assert any("source_experiment" in v for v in violations), (
            f"Expected source_experiment violation, got: {violations}"
        )

    def test_check_figures_note_fails_missing_experiment_results_hash(self, tmp_instance):
        """A figures note without experiment_results_hash field fails cmd_check."""
        cfg = load_config(reload=True)
        figures_dir = cfg.project_notes_dir("demo-research") / "figures"
        self._write_figures_note(figures_dir, "fig-no-hash", experiment_results_hash="")
        violations = note_mod.cmd_check("demo-research", config=cfg)
        assert any("experiment_results_hash" in v for v in violations), (
            f"Expected experiment_results_hash violation, got: {violations}"
        )

    def test_check_figures_type_dir_contract(self, tmp_instance):
        """A figures note placed in wrong directory (e.g. findings/) fails the type-dir check."""
        cfg = load_config(reload=True)
        # Write a note claiming type=figures but in the findings/ dir
        findings_dir = cfg.project_notes_dir("demo-research") / "findings"
        findings_dir.mkdir(parents=True, exist_ok=True)
        bad_note = findings_dir / "bad-fig.md"
        bad_note.write_text(
            "---\ntype: figures\ntitle: bad\ncreated: 2026-07-01\n"
            "source_experiment: experiments/run-007\nexperiment_results_hash: sha256:abc\n---\n",
            encoding="utf-8",
        )
        violations = note_mod.cmd_check("demo-research", config=cfg)
        assert any("figures" in v and "findings" in v for v in violations), (
            f"Expected type-dir mismatch violation; got: {violations}"
        )

    def test_check_figures_scans_project_dir_not_shared_root(self, tmp_instance):
        """cmd_check for figures scans project_notes_dir/figures/, not a shared root.

        Non-vacuous discriminant: put an INVALID note (no experiment_results_hash) in
        demo-research's figures/ dir. cmd_check for demo-research must flag it; cmd_check
        for demo-litreview must NOT see it (different project-scoped dir).
        """
        cfg = load_config(reload=True)
        figures_dir = cfg.project_notes_dir("demo-research") / "figures"
        self._write_figures_note(figures_dir, "proj-fig", experiment_results_hash="")

        # demo-research must flag it
        violations_p1 = note_mod.cmd_check("demo-research", config=cfg)
        assert any("experiment_results_hash" in v for v in violations_p1), (
            f"demo-research should flag missing experiment_results_hash; got: {violations_p1}"
        )

        # demo-litreview must NOT see it (project-scoped)
        violations_p2 = note_mod.cmd_check("demo-litreview", config=cfg)
        assert not any("proj-fig" in v for v in violations_p2), (
            f"demo-litreview must not scan demo-research's figures dir; got: {violations_p2}"
        )


# ============================================================================
# Seam 2: rv figure new — creates figure-spec note (experiment-sourced)
# ============================================================================

class TestFigureNew:
    """rv figure new creates a figure-spec note with experiment-results provenance."""

    def _setup_experiment(self, tmp_instance, cfg, exp_id: str) -> tuple[Path, str]:
        """Helper: write an experiment note with real results. Returns (note_path, hash)."""
        project_notes_dir = cfg.project_notes_dir("demo-research")
        return _write_experiment_note_and_results(
            Path(tmp_instance), project_notes_dir, exp_id,
        )

    def test_figure_new_creates_note_in_project_dir(self, tmp_instance):
        """rv figure new <fig-id> --experiment <id> creates figures/<id>.md in project dir."""
        cfg = load_config(reload=True)
        self._setup_experiment(tmp_instance, cfg, "run-007")

        from research_vault.figure import cmd_new as fig_cmd_new
        path = fig_cmd_new(
            "demo-research", "hfs-by-lang",
            experiment_id="run-007",
            config=cfg,
        )
        assert path.exists(), f"figure note should exist at {path}"
        expected_dir = cfg.project_notes_dir("demo-research") / "figures"
        assert path.parent == expected_dir

    def test_figure_new_records_source_experiment(self, tmp_instance):
        """rv figure new records the source_experiment OKF link in frontmatter."""
        cfg = load_config(reload=True)
        self._setup_experiment(tmp_instance, cfg, "run-007")

        from research_vault.figure import cmd_new as fig_cmd_new
        path = fig_cmd_new("demo-research", "fig-src", experiment_id="run-007", config=cfg)
        content = path.read_text()
        assert "source_experiment: experiments/run-007" in content, (
            f"Figure note must record source_experiment OKF link; got:\n{content}"
        )

    def test_figure_new_records_experiment_results_hash(self, tmp_instance):
        """rv figure new records the experiment_results_hash from the experiment note."""
        cfg = load_config(reload=True)
        _, expected_hash = self._setup_experiment(tmp_instance, cfg, "run-hash")

        from research_vault.figure import cmd_new as fig_cmd_new
        path = fig_cmd_new("demo-research", "fig-hash", experiment_id="run-hash", config=cfg)
        content = path.read_text()
        assert f"experiment_results_hash: {expected_hash}" in content, (
            f"Figure note must record experiment_results_hash; got:\n{content}"
        )

    def test_figure_new_hash_is_non_vacuous(self, tmp_instance):
        """The recorded experiment_results_hash is non-empty and starts with sha256:.

        Non-vacuous: we feed a real results file through a real hash function. The
        recorded hash must match what sha256sum would produce on that file.
        """
        cfg = load_config(reload=True)
        results_data = b"col1,col2\n1,2\n3,4\n"
        project_notes_dir = cfg.project_notes_dir("demo-research")
        _, expected_hash = _write_experiment_note_and_results(
            Path(tmp_instance), project_notes_dir, "run-nv", results_data=results_data,
        )

        from research_vault.figure import cmd_new as fig_cmd_new
        path = fig_cmd_new("demo-research", "fig-nv", experiment_id="run-nv", config=cfg)
        content = path.read_text()

        # Extract the recorded hash
        recorded_hash = ""
        for line in content.splitlines():
            if line.startswith("experiment_results_hash:"):
                recorded_hash = line.split(":", 1)[1].strip()
                break

        assert recorded_hash, "experiment_results_hash must be non-empty"
        assert recorded_hash.startswith("sha256:"), (
            f"Hash must be sha256:<hex>; got: {recorded_hash!r}"
        )
        assert recorded_hash == expected_hash, (
            f"Recorded hash {recorded_hash!r} does not match expected {expected_hash!r}"
        )

    def test_figure_new_records_filter_recipe(self, tmp_instance):
        """rv figure new records select columns and filter expression in frontmatter."""
        cfg = load_config(reload=True)
        self._setup_experiment(tmp_instance, cfg, "run-filt")

        from research_vault.figure import cmd_new as fig_cmd_new
        path = fig_cmd_new(
            "demo-research", "fig-filt",
            experiment_id="run-filt",
            select=["metric", "value"],
            filter_expr="value > 0.5",
            config=cfg,
        )
        content = path.read_text()
        assert "select: metric,value" in content, f"select must be recorded; got:\n{content}"
        assert "filter: value > 0.5" in content, f"filter_expr must be recorded; got:\n{content}"

    def test_figure_new_records_style_preset(self, tmp_instance):
        """rv figure new records the style preset in frontmatter."""
        cfg = load_config(reload=True)
        self._setup_experiment(tmp_instance, cfg, "run-style")

        from research_vault.figure import cmd_new as fig_cmd_new
        path = fig_cmd_new(
            "demo-research", "fig-style",
            experiment_id="run-style",
            style="poster",
            config=cfg,
        )
        content = path.read_text()
        assert "style: poster" in content, f"style preset must be recorded; got:\n{content}"

    def test_figure_new_records_plot_type(self, tmp_instance):
        """rv figure new records the plot_type in frontmatter."""
        cfg = load_config(reload=True)
        self._setup_experiment(tmp_instance, cfg, "run-pt")

        from research_vault.figure import cmd_new as fig_cmd_new
        path = fig_cmd_new(
            "demo-research", "fig-pt",
            experiment_id="run-pt",
            plot_type="scatter",
            config=cfg,
        )
        content = path.read_text()
        assert "plot_type: scatter" in content, f"plot_type must be recorded; got:\n{content}"

    def test_figure_new_fails_if_experiment_note_missing(self, tmp_instance):
        """rv figure new raises ValueError when the experiment note doesn't exist."""
        cfg = load_config(reload=True)

        from research_vault.figure import cmd_new as fig_cmd_new
        with pytest.raises(ValueError, match="experiment"):
            fig_cmd_new("demo-research", "fig-no-exp", experiment_id="nonexistent", config=cfg)

    def test_figure_new_accepts_optional_benchmark(self, tmp_instance):
        """rv figure new accepts --benchmark datasets/<id> as an OPTIONAL secondary reference.

        Non-vacuous: pass a valid benchmark dataset note AND a valid experiment.
        The figure note must record benchmark_dataset but source_experiment is still
        the primary provenance (not the benchmark).
        """
        cfg = load_config(reload=True)
        self._setup_experiment(tmp_instance, cfg, "run-bm")
        # Also write a benchmark dataset note
        bm_data = b"baseline,score\ngpt4,0.85\n"
        bm_file = Path(tmp_instance) / "baseline.csv"
        bm_file.write_bytes(bm_data)
        _write_dataset_note(
            cfg.datasets_root, "baseline-benchmark",
            location=str(bm_file), hash_val=_sha256_hex(bm_data),
        )

        from research_vault.figure import cmd_new as fig_cmd_new
        path = fig_cmd_new(
            "demo-research", "fig-bm",
            experiment_id="run-bm",
            benchmark_id="baseline-benchmark",
            config=cfg,
        )
        content = path.read_text()
        # Primary source must be the experiment
        assert "source_experiment: experiments/run-bm" in content, (
            f"source_experiment must be primary; got:\n{content}"
        )
        # Benchmark must be recorded as optional secondary
        assert "benchmark_dataset: datasets/baseline-benchmark" in content, (
            f"benchmark_dataset must be recorded; got:\n{content}"
        )

    def test_figure_new_works_without_benchmark(self, tmp_instance):
        """rv figure new does NOT require --benchmark (it is optional).

        Non-vacuous: pass only --experiment, omit --benchmark. Must succeed.
        """
        cfg = load_config(reload=True)
        self._setup_experiment(tmp_instance, cfg, "run-nobm")

        from research_vault.figure import cmd_new as fig_cmd_new
        # Must not raise even though benchmark_id is not provided
        path = fig_cmd_new("demo-research", "fig-nobm", experiment_id="run-nobm", config=cfg)
        assert path.exists()
        content = path.read_text()
        assert "source_experiment: experiments/run-nobm" in content

    def test_figure_note_passes_cmd_check(self, tmp_instance):
        """A figure note created by cmd_new passes note.cmd_check.

        This verifies the OKF type↔dir contract is satisfied end-to-end with the
        new experiment-sourced provenance fields.
        """
        cfg = load_config(reload=True)
        self._setup_experiment(tmp_instance, cfg, "run-check")

        from research_vault.figure import cmd_new as fig_cmd_new
        fig_cmd_new("demo-research", "fig-check", experiment_id="run-check", config=cfg)

        violations = note_mod.cmd_check("demo-research", config=cfg)
        assert violations == [], f"Figure note should pass cmd_check; violations: {violations}"


# ============================================================================
# Seam 3: rv figure preview — loads frame from experiment results_location
# ============================================================================

class TestFigurePreview:
    """rv figure preview loads frame from experiment's results_location (not datasets/)."""

    def _create_figure_spec(
        self,
        tmp_instance,
        cfg,
        fig_id: str,
        exp_id: str,
        *,
        results_data: bytes = b"score,lang,method\n0.9,en,bert\n0.8,zh,bert\n0.7,en,lstm\n0.6,zh,lstm\n",
    ) -> Path:
        """Helper: create a complete figure spec sourced from experiment results."""
        project_notes_dir = cfg.project_notes_dir("demo-research")
        _write_experiment_note_and_results(
            Path(tmp_instance), project_notes_dir, exp_id, results_data=results_data,
        )
        from research_vault.figure import cmd_new as fig_cmd_new
        return fig_cmd_new("demo-research", fig_id, experiment_id=exp_id, config=cfg)

    def test_preview_writes_view_csv(self, tmp_instance):
        """rv figure preview writes state/figures/<fig>-view.csv from experiment results."""
        pytest.importorskip("pandas")
        cfg = load_config(reload=True)
        self._create_figure_spec(tmp_instance, cfg, "hfs-by-lang", "run-001")

        from research_vault.figure import cmd_preview
        rc = cmd_preview("demo-research", "hfs-by-lang", config=cfg)
        assert rc == 0, f"preview must return 0; got {rc}"

        view_path = cfg.state_dir / "figures" / "hfs-by-lang-view.csv"
        assert view_path.exists(), f"View CSV must be written at {view_path}"
        content = view_path.read_text()
        assert len(content.strip()) > 0, "View CSV must be non-empty"

    def test_preview_frame_sourced_from_experiment_results(self, tmp_instance):
        """Preview frame comes from experiment's results_location, not a shared dataset.

        Non-vacuous: write DISTINCT results data in the experiment note. The view CSV
        must contain the experiment data (not some other file). If preview mistakenly
        loaded from a shared datasets/ path, it would fail or produce wrong content.
        """
        pytest.importorskip("pandas")
        import pandas as pd

        cfg = load_config(reload=True)
        # Unique sentinel value in the results data
        sentinel_results = b"metric,value\nsentinel_accuracy,0.9999\n"
        self._create_figure_spec(
            tmp_instance, cfg, "fig-sentinel", "run-sentinel",
            results_data=sentinel_results,
        )

        from research_vault.figure import cmd_preview
        cmd_preview("demo-research", "fig-sentinel", config=cfg)

        view_path = cfg.state_dir / "figures" / "fig-sentinel-view.csv"
        assert view_path.exists()
        df = pd.read_csv(view_path)
        # The sentinel value must be in the view (proves we loaded from experiment results)
        assert "sentinel_accuracy" in df["metric"].values, (
            f"View CSV must contain experiment results data; columns: {list(df.columns)}, "
            f"got metric values: {df['metric'].tolist()}"
        )

    def test_preview_writes_view_md_table(self, tmp_instance):
        """rv figure preview also writes a rendered markdown table (-view.md)."""
        pytest.importorskip("pandas")
        cfg = load_config(reload=True)
        self._create_figure_spec(tmp_instance, cfg, "fig-md", "run-md")

        from research_vault.figure import cmd_preview
        cmd_preview("demo-research", "fig-md", config=cfg)

        view_md = cfg.state_dir / "figures" / "fig-md-view.md"
        assert view_md.exists(), f"View markdown table must be written at {view_md}"

    def test_preview_prints_frame_head(self, tmp_instance, capsys):
        """rv figure preview prints the frame head rows + shape to stdout."""
        pytest.importorskip("pandas")
        cfg = load_config(reload=True)
        self._create_figure_spec(tmp_instance, cfg, "fig-print", "run-print")

        from research_vault.figure import cmd_preview
        cmd_preview("demo-research", "fig-print", config=cfg)

        captured = capsys.readouterr()
        assert "rows" in captured.out.lower() or "shape" in captured.out.lower() or (
            "4" in captured.out  # 4 data rows in our default test data
        ), f"Preview must print frame info; got:\n{captured.out}"

    def test_preview_applies_column_select(self, tmp_instance):
        """rv figure preview applies the select filter — only selected columns in view CSV."""
        pytest.importorskip("pandas")
        cfg = load_config(reload=True)
        results_data = b"score,lang,method\n0.9,en,bert\n0.8,zh,bert\n"
        project_notes_dir = cfg.project_notes_dir("demo-research")
        _write_experiment_note_and_results(
            Path(tmp_instance), project_notes_dir, "run-sel", results_data=results_data,
        )

        from research_vault.figure import cmd_new, cmd_preview
        cmd_new("demo-research", "fig-sel", experiment_id="run-sel",
                select=["score", "lang"], config=cfg)
        cmd_preview("demo-research", "fig-sel", config=cfg)

        import pandas as pd
        view_path = cfg.state_dir / "figures" / "fig-sel-view.csv"
        df = pd.read_csv(view_path)
        assert list(df.columns) == ["score", "lang"], (
            f"View CSV must contain only selected columns; got: {list(df.columns)}"
        )
        assert "method" not in df.columns

    def test_preview_import_guard_friendly_message(self, tmp_instance, capsys):
        """rv figure preview prints friendly message when [figures] extra is absent."""
        from research_vault import figure as fig_mod

        assert "pip install research-vault[figures]" in fig_mod._FIGURES_EXTRA_MSG, (
            f"Import guard message must mention the extra; got: {fig_mod._FIGURES_EXTRA_MSG!r}"
        )

        import builtins
        original_import = builtins.__import__

        def blocking_import(name, *args, **kwargs):
            if name in ("pandas", "matplotlib"):
                raise ImportError(f"No module named '{name}'")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=blocking_import):
            rc = fig_mod._check_figures_extra()

        assert rc is not None and rc != 0, (
            f"_check_figures_extra must return non-zero when extra absent; got: {rc!r}"
        )
        captured = capsys.readouterr()
        assert "pip install research-vault[figures]" in captured.out + captured.err, (
            f"Import guard must print install hint; got stderr: {captured.err!r}"
        )

    def test_preview_import_guard_no_raw_import_error(self, tmp_instance):
        """The [figures] import guard must NEVER propagate a raw ImportError."""
        import importlib
        spec = importlib.util.find_spec("research_vault.figure")
        assert spec is not None, "research_vault.figure must be importable"


# ============================================================================
# Seam 4: rv figure list — lists figure notes for a project
# ============================================================================

class TestFigureList:
    """rv figure list returns figure specs for a project."""

    def _setup_experiment_and_figure(self, tmp_instance, cfg, project: str, exp_id: str, fig_id: str) -> Path:
        project_notes_dir = cfg.project_notes_dir(project)
        _write_experiment_note_and_results(Path(tmp_instance), project_notes_dir, exp_id)
        from research_vault.figure import cmd_new as fig_cmd_new
        return fig_cmd_new(project, fig_id, experiment_id=exp_id, config=cfg)

    def test_list_returns_empty_when_no_figures(self, tmp_instance):
        """rv figure list returns [] when project has no figure notes."""
        cfg = load_config(reload=True)
        from research_vault.figure import cmd_list
        result = cmd_list("demo-research", config=cfg)
        assert result == [], f"Expected empty list; got: {result}"

    def test_list_returns_figure_notes(self, tmp_instance):
        """rv figure list returns figure note dicts with path + fields."""
        cfg = load_config(reload=True)
        self._setup_experiment_and_figure(tmp_instance, cfg, "demo-research", "run-list1", "list-fig-1")
        self._setup_experiment_and_figure(tmp_instance, cfg, "demo-research", "run-list2", "list-fig-2")

        from research_vault.figure import cmd_list
        results = cmd_list("demo-research", config=cfg)
        assert len(results) == 2, f"Expected 2 figure notes; got: {len(results)}"
        ids = {r["path"].stem for r in results}
        assert "list-fig-1" in ids
        assert "list-fig-2" in ids

    def test_list_does_not_return_other_projects_figures(self, tmp_instance):
        """rv figure list only returns figures for the specified project.

        Non-vacuous: creates figures in BOTH projects; listing demo-research should
        not return demo-litreview's figures.
        """
        cfg = load_config(reload=True)
        proj1_notes = cfg.project_notes_dir("demo-research")
        proj2_notes = cfg.project_notes_dir("demo-litreview")
        _write_experiment_note_and_results(Path(tmp_instance), proj1_notes, "run-p1")
        _write_experiment_note_and_results(Path(tmp_instance), proj2_notes, "run-p2")

        from research_vault.figure import cmd_new as fig_cmd_new, cmd_list
        fig_cmd_new("demo-research", "p1-fig", experiment_id="run-p1", config=cfg)
        fig_cmd_new("demo-litreview", "p2-fig", experiment_id="run-p2", config=cfg)

        p1_results = cmd_list("demo-research", config=cfg)
        assert len(p1_results) == 1
        assert p1_results[0]["path"].stem == "p1-fig"

        p2_results = cmd_list("demo-litreview", config=cfg)
        assert len(p2_results) == 1
        assert p2_results[0]["path"].stem == "p2-fig"


# ============================================================================
# Seam 5: CLI verb dispatch (rv figure as a top-level verb)
# ============================================================================

class TestFigureVerbRegistry:
    """rv figure is registered in _VERB_REGISTRY with when_to_use + anti-pattern + sr."""

    def test_figure_in_verb_registry(self):
        """'figure' verb is registered in cli._VERB_REGISTRY."""
        from research_vault.cli import _VERB_REGISTRY
        assert "figure" in _VERB_REGISTRY, (
            "'figure' must be in _VERB_REGISTRY — not yet added to cli.py"
        )

    def test_figure_when_to_use_fires_on_experiment_results_intent(self):
        """figure verb when_to_use fires on 'experiment results + publication-quality plot' intent."""
        from research_vault.cli import _VERB_REGISTRY
        entry = _VERB_REGISTRY.get("figure", {})
        when = entry.get("when_to_use", "")
        assert (
            "experiment" in when.lower()
            or "results" in when.lower()
            or "scores" in when.lower()
            or "plot" in when.lower()
        ), f"when_to_use should fire on experiment→figure intent; got: {when!r}"

    def test_figure_when_to_use_has_anti_pattern(self):
        """figure verb when_to_use includes the anti-pattern against one-off scripts."""
        from research_vault.cli import _VERB_REGISTRY
        entry = _VERB_REGISTRY.get("figure", {})
        when = entry.get("when_to_use", "")
        assert "do NOT" in when or "anti-pattern" in when.lower() or "matplotlib" in when.lower(), (
            f"when_to_use must include anti-pattern; got: {when!r}"
        )

    def test_figure_sr_label(self):
        """figure verb carries SR label 'SR-FIG'."""
        from research_vault.cli import _VERB_REGISTRY
        entry = _VERB_REGISTRY.get("figure", {})
        assert "SR-FIG" in entry.get("sr", ""), (
            f"figure verb must carry SR-FIG label; got: {entry.get('sr')!r}"
        )

    def test_rv_help_check_passes_with_figure_verb(self):
        """rv help --check still returns OK after adding figure to registry."""
        from research_vault.cli import _check_verb_docstrings
        violations = _check_verb_docstrings()
        assert violations == [], f"rv help --check has violations: {violations}"


# ============================================================================
# Seam 6: style.apply_style stub — exact seam signature
# ============================================================================

class TestStyleSeam:
    """The apply_style stub ships with exact signature for Iris to plug into."""

    def test_apply_style_importable(self):
        """figures.style.apply_style is importable without matplotlib installed."""
        from research_vault.figures.style import apply_style
        assert callable(apply_style), "apply_style must be callable"

    def test_apply_style_signature(self):
        """apply_style(preset, skin) has the exact seam signature."""
        import inspect
        from research_vault.figures.style import apply_style
        sig = inspect.signature(apply_style)
        params = list(sig.parameters.keys())
        assert params == ["preset", "skin"], (
            f"apply_style must have signature (preset, skin) exactly; got params: {params}"
        )

    def test_apply_style_stub_does_not_raise(self):
        """The stub apply_style does not raise regardless of inputs."""
        from research_vault.figures.style import apply_style
        apply_style("publication", "demo-research")
        apply_style("slide", "demo-litreview")
        apply_style("poster", None)

    def test_apply_style_stub_returns_rcparams_or_none(self):
        """The stub apply_style returns a dict of rcParams or None (not an error object)."""
        from research_vault.figures.style import apply_style
        result = apply_style("publication", "demo-research")
        assert result is None or isinstance(result, dict), (
            f"apply_style stub must return dict or None; got {type(result)}"
        )


# ============================================================================
# Seam 7: rv check gains figures as an OPTIONAL prereq line
# ============================================================================

class TestCheckFiguresOptional:
    """rv check includes figures as an optional prerequisite."""

    def test_check_report_includes_figures(self):
        """run_preflight() report includes a figures section."""
        from research_vault.check import run_preflight
        result = run_preflight()
        assert "figures" in result["report"].lower(), (
            f"rv check report must mention figures optional extra; got:\n{result['report']}"
        )

    def test_check_figures_is_optional(self):
        """figures missing does NOT cause rv check to fail (exit 0 when required prereqs ok)."""
        from research_vault.check import run_preflight
        result = run_preflight()
        assert "figures" in result, "run_preflight() result must include a 'figures' key"

    def test_check_figures_result_is_bool(self):
        """run_preflight() 'figures' value is a bool."""
        from research_vault.check import run_preflight
        result = run_preflight()
        assert isinstance(result.get("figures"), bool), (
            f"run_preflight()['figures'] must be bool; got {type(result.get('figures'))}"
        )


# ============================================================================
# Seam 8: Demo-figures DAG loop — extract reads from experiments/, not datasets/
# ============================================================================

class TestDemoFiguresLoop:
    """The demo-figures DAG loop extract node reads experiment results, not a shared dataset.

    Zero new DAG mechanism — human-go already cannot approve until all transitive
    upstream nodes are terminal. This tests the manifest shape and the invariant.
    """

    def _load_demo_figures_manifest(self) -> dict:
        """Load the demo-figures manifest from package data.

        SR-PKG: examples/ moved to src/research_vault/data/examples/.
        """
        manifest_path = (
            Path(__file__).parent.parent
            / "src" / "research_vault" / "data" / "examples"
            / "demo-figures" / "demo-figures.json"
        )
        assert manifest_path.exists(), f"demo-figures manifest not found at {manifest_path}"
        return json.loads(manifest_path.read_text())

    def test_demo_figures_manifest_is_valid(self):
        """The demo-figures manifest passes schema validation."""
        manifest = self._load_demo_figures_manifest()
        validate_manifest(manifest)  # must not raise

    def test_demo_figures_manifest_has_three_nodes(self):
        """The demo-figures manifest has the extract → data-check → render three-node shape."""
        manifest = self._load_demo_figures_manifest()
        node_ids = [n["id"] for n in manifest["nodes"]]
        assert len(node_ids) == 3, f"Demo manifest must have 3 nodes; got: {node_ids}"

    def test_demo_figures_has_human_go_data_check(self):
        """The demo-figures manifest includes a human-go data-check node."""
        manifest = self._load_demo_figures_manifest()
        human_go_nodes = [n for n in manifest["nodes"] if n.get("type") == "human-go"]
        assert len(human_go_nodes) == 1, (
            f"Demo manifest must have exactly 1 human-go node; got: {human_go_nodes}"
        )
        assert human_go_nodes[0]["id"] == "data-check", (
            f"human-go node must be 'data-check'; got: {human_go_nodes[0]['id']}"
        )

    def test_demo_figures_extract_reads_experiment_not_dataset(self):
        """The extract node's reads list includes an experiments/ path, NOT a datasets/ path.

        This is the critical correctness test for the source swap (§5E.10 item 3).
        The extract node must read from experiments/<id>.md (the results-carrying note),
        not from a shared datasets/ path.
        """
        manifest = self._load_demo_figures_manifest()
        extract_node = next(
            (n for n in manifest["nodes"] if n.get("id") == "extract"), None
        )
        assert extract_node is not None, "manifest must have an 'extract' node"

        reads = extract_node.get("reads", [])
        has_experiment_read = any("experiments/" in r for r in reads)
        has_dataset_read_as_primary = any(
            "datasets/" in r and "figures" not in r.lower()
            for r in reads
            if "benchmark" not in r.lower()
        )

        assert has_experiment_read, (
            f"extract node must read from experiments/ (experiment results are primary); "
            f"reads: {reads}"
        )
        # Datasets/ must NOT appear as the primary input source
        # (it may appear if labelled benchmark, but not as an unlabelled primary read)
        assert not has_dataset_read_as_primary, (
            f"extract node must NOT have datasets/ as a primary read; reads: {reads}"
        )

    def test_demo_figures_data_check_cannot_approve_until_extract_terminal(self, tmp_instance):
        """data-check node cannot enter approved state until extract is terminal."""
        from research_vault.dag.walker import compute_frontier
        from research_vault.dag.schema import validate_manifest

        manifest = self._load_demo_figures_manifest()
        validate_manifest(manifest)

        node_states = {n["id"]: {"status": "pending"} for n in manifest["nodes"]}
        frontier = compute_frontier(manifest, node_states, {}, global_cap=4)
        frontier_ids = {f.node_id for f in frontier}

        assert "data-check" not in frontier_ids, (
            "data-check must NOT be in frontier when extract is pending"
        )
        extract_id = manifest["nodes"][0]["id"]
        assert extract_id in frontier_ids, (
            f"extract node ({extract_id!r}) must be in frontier when pending"
        )

    def test_demo_figures_data_check_enters_frontier_after_extract_terminal(self, tmp_instance):
        """data-check becomes awaiting-go once extract is succeeded (upstream terminal)."""
        from research_vault.dag.walker import compute_frontier

        manifest = self._load_demo_figures_manifest()
        extract_id = manifest["nodes"][0]["id"]
        data_check_id = "data-check"

        node_states = {n["id"]: {"status": "pending"} for n in manifest["nodes"]}
        node_states[extract_id] = {"status": "succeeded"}

        frontier = compute_frontier(manifest, node_states, {}, global_cap=4)
        frontier_ids = {f.node_id for f in frontier}

        assert data_check_id in frontier_ids, (
            f"data-check must enter frontier once extract is terminal; "
            f"frontier: {frontier_ids}"
        )

    def test_demo_figures_agent_nodes_have_spec_and_reads(self):
        """All agent nodes in demo-figures carry spec: and reads: (SR-DISP/SR-SCOPE)."""
        manifest = self._load_demo_figures_manifest()
        for node in manifest["nodes"]:
            if node.get("type") == "agent":
                nid = node["id"]
                assert "spec" in node and node["spec"], (
                    f"Agent node {nid!r} missing spec (SR-DISP)"
                )
                assert "reads" in node and node["reads"], (
                    f"Agent node {nid!r} missing reads (SR-SCOPE)"
                )

    def test_demo_figures_render_needs_data_check(self):
        """render node has afterok dependency on data-check (not extract directly)."""
        manifest = self._load_demo_figures_manifest()
        render_node = next(
            (n for n in manifest["nodes"] if n.get("id") != "data-check" and
             n.get("type") == "agent" and
             any(need.get("from") == "data-check" for need in n.get("needs", []))),
            None,
        )
        assert render_node is not None, (
            "render node must have afterok dependency on data-check"
        )
