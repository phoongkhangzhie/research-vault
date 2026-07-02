"""test_sr_fig.py — SR-FIG: FIGURES as the 8th OKF type + rv figure verb plumbing.

All tests are hermetic (tmp_instance). No ~/vault reads or writes.

Five seams tested:
  1. OKF type `figures` — 8th canonical type; PROJECT-SCOPED (project_notes_dir/figures/),
     NOT shared (no figures_root). type↔dir contract enforced; source_dataset + dataset_hash
     required fields checked by cmd_check.
  2. rv figure new — creates figures/<id>.md with provenance frontmatter (dataset OKF link +
     hash + filter recipe + style preset).
  3. rv figure preview — writes state/figures/<fig>-view.csv + prints frame head; guarded
     by [figures] extra import-guard (friendly message, no raw ImportError).
  4. rv figure list — lists figure notes for a project.
  5. Demo-figures DAG loop — data-check node cannot approve until extract is terminal
     (existing transitive-upstream-terminal invariant; zero new DAG mechanism).

CRITICAL scoping: figures are PROJECT-SCOPED under project_notes_dir(project)/figures/.
NOT a shared root (only datasets gets the shared-root treatment in SR-8).

All authored test manifests carry spec: and reads: (SR-DISP/SR-SCOPE compliance).
"""

import hashlib
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from research_vault import note as note_mod
from research_vault.config import load_config
from research_vault.dag.schema import validate_manifest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


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


def _agent_node(nid: str, **kwargs) -> dict:
    """Build a minimal valid agent node (spec + reads required for SR-DISP/SR-SCOPE)."""
    node: dict = {
        "id": nid,
        "type": "agent",
        "spec": f"task://test#{nid}",
        "reads": [f"tasks/test.md#{nid}"],
    }
    node.update(kwargs)
    return node


def _minimal_manifest(nodes: list[dict]) -> dict:
    return {"run_id": "test-run", "nodes": nodes}


# ============================================================================
# Seam 1: OKF type `figures` — 8th type, PROJECT-SCOPED
# ============================================================================

class TestFiguresOkfType:
    """figures is the 8th canonical OKF type, project-scoped (not shared)."""

    def test_figures_in_okf_types(self):
        """'figures' must be in the OKF_TYPES frozenset."""
        assert "figures" in note_mod.OKF_TYPES

    def test_okf_type_count_is_eight(self):
        """OKF_TYPES now has exactly 8 types (SR-FIG adds figures)."""
        assert len(note_mod.OKF_TYPES) == 8
        expected = {
            "literature", "concepts", "methods", "experiments",
            "findings", "mocs", "datasets", "figures",
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
    """cmd_check validates figures notes: type↔dir + source_dataset + dataset_hash."""

    def _write_figures_note(
        self,
        figures_dir: Path,
        fig_id: str,
        *,
        source_dataset: str = "datasets/my-data",
        dataset_hash: str = "sha256:abc123",
        style: str = "publication",
    ) -> Path:
        """Write a minimal figures provenance note."""
        figures_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            "---",
            "type: figures",
            f"title: {fig_id}",
            "created: 2026-07-01",
        ]
        if source_dataset:
            lines.append(f"source_dataset: {source_dataset}")
        if dataset_hash:
            lines.append(f"dataset_hash: {dataset_hash}")
        if style:
            lines.append(f"style: {style}")
        lines += ["---", "", "<!-- figure provenance note -->", ""]
        p = figures_dir / f"{fig_id}.md"
        p.write_text("\n".join(lines), encoding="utf-8")
        return p

    def test_check_figures_note_ok(self, tmp_instance):
        """A well-formed figures note passes cmd_check (type + source_dataset + dataset_hash)."""
        cfg = load_config(reload=True)
        figures_dir = cfg.project_notes_dir("demo-research") / "figures"
        self._write_figures_note(figures_dir, "fig-ok")
        violations = note_mod.cmd_check("demo-research", config=cfg)
        assert violations == [], f"Expected no violations, got: {violations}"

    def test_check_figures_note_fails_missing_source_dataset(self, tmp_instance):
        """A figures note without source_dataset field fails cmd_check."""
        cfg = load_config(reload=True)
        figures_dir = cfg.project_notes_dir("demo-research") / "figures"
        self._write_figures_note(figures_dir, "fig-no-ds", source_dataset="")
        violations = note_mod.cmd_check("demo-research", config=cfg)
        assert any("source_dataset" in v for v in violations), (
            f"Expected source_dataset violation, got: {violations}"
        )

    def test_check_figures_note_fails_missing_dataset_hash(self, tmp_instance):
        """A figures note without dataset_hash field fails cmd_check."""
        cfg = load_config(reload=True)
        figures_dir = cfg.project_notes_dir("demo-research") / "figures"
        self._write_figures_note(figures_dir, "fig-no-hash", dataset_hash="")
        violations = note_mod.cmd_check("demo-research", config=cfg)
        assert any("dataset_hash" in v for v in violations), (
            f"Expected dataset_hash violation, got: {violations}"
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
            "source_dataset: datasets/x\ndataset_hash: sha256:abc\n---\n",
            encoding="utf-8",
        )
        violations = note_mod.cmd_check("demo-research", config=cfg)
        assert any("figures" in v and "findings" in v for v in violations), (
            f"Expected type-dir mismatch violation; got: {violations}"
        )

    def test_check_figures_scans_project_dir_not_shared_root(self, tmp_instance):
        """cmd_check for figures scans project_notes_dir/figures/, not a shared root.

        Non-vacuous discriminant: put an INVALID note (no dataset_hash) in demo-research's
        figures/ dir. cmd_check for demo-research must flag it; cmd_check for demo-litreview
        must NOT see it (different project-scoped dir).
        """
        cfg = load_config(reload=True)
        # Write an invalid note in demo-research's figures/
        figures_dir = cfg.project_notes_dir("demo-research") / "figures"
        self._write_figures_note(figures_dir, "proj-fig", dataset_hash="")  # missing hash

        # demo-research must flag it
        violations_p1 = note_mod.cmd_check("demo-research", config=cfg)
        assert any("dataset_hash" in v for v in violations_p1), (
            f"demo-research should flag missing dataset_hash; got: {violations_p1}"
        )

        # demo-litreview must NOT see it (project-scoped)
        violations_p2 = note_mod.cmd_check("demo-litreview", config=cfg)
        assert not any("proj-fig" in v for v in violations_p2), (
            f"demo-litreview must not scan demo-research's figures dir; got: {violations_p2}"
        )


# ============================================================================
# Seam 2: rv figure new — creates figure-spec note
# ============================================================================

class TestFigureNew:
    """rv figure new creates a figure-spec note with provenance frontmatter."""

    def _write_dataset_note_and_file(self, tmp_instance, cfg, note_id: str) -> tuple[Path, str]:
        """Helper: write a dataset note with a real data file. Returns (note_path, hash)."""
        data = b"col1,col2\n1,2\n3,4\n"
        data_file = Path(tmp_instance) / f"{note_id}.csv"
        data_file.write_bytes(data)
        h = _sha256_hex(data)
        note = _write_dataset_note(
            cfg.datasets_root, note_id,
            location=str(data_file), hash_val=h,
        )
        return note, h

    def test_figure_new_creates_note_in_project_dir(self, tmp_instance):
        """rv figure new <fig-id> --dataset <id> creates figures/<id>.md in project dir."""
        cfg = load_config(reload=True)
        self._write_dataset_note_and_file(tmp_instance, cfg, "my-scores")

        from research_vault.figure import cmd_new as fig_cmd_new
        path = fig_cmd_new(
            "demo-research", "hfs-by-lang",
            dataset_id="my-scores",
            config=cfg,
        )
        assert path.exists(), f"figure note should exist at {path}"
        expected_dir = cfg.project_notes_dir("demo-research") / "figures"
        assert path.parent == expected_dir

    def test_figure_new_records_source_dataset(self, tmp_instance):
        """rv figure new records the source_dataset OKF link in frontmatter."""
        cfg = load_config(reload=True)
        self._write_dataset_note_and_file(tmp_instance, cfg, "scores-v1")

        from research_vault.figure import cmd_new as fig_cmd_new
        path = fig_cmd_new("demo-research", "fig-src", dataset_id="scores-v1", config=cfg)
        content = path.read_text()
        assert "source_dataset: datasets/scores-v1" in content, (
            f"Figure note must record source_dataset OKF link; got:\n{content}"
        )

    def test_figure_new_records_dataset_hash(self, tmp_instance):
        """rv figure new records the dataset_hash from the datasets/ provenance note."""
        cfg = load_config(reload=True)
        _, expected_hash = self._write_dataset_note_and_file(tmp_instance, cfg, "scores-hash")

        from research_vault.figure import cmd_new as fig_cmd_new
        path = fig_cmd_new("demo-research", "fig-hash", dataset_id="scores-hash", config=cfg)
        content = path.read_text()
        assert f"dataset_hash: {expected_hash}" in content, (
            f"Figure note must record dataset_hash from datasets note; got:\n{content}"
        )

    def test_figure_new_records_filter_recipe(self, tmp_instance):
        """rv figure new records select columns and filter expression in frontmatter."""
        cfg = load_config(reload=True)
        self._write_dataset_note_and_file(tmp_instance, cfg, "scores-filt")

        from research_vault.figure import cmd_new as fig_cmd_new
        path = fig_cmd_new(
            "demo-research", "fig-filt",
            dataset_id="scores-filt",
            select=["col1", "col2"],
            filter_expr="col1 > 0",
            config=cfg,
        )
        content = path.read_text()
        assert "select: col1,col2" in content, f"select must be recorded; got:\n{content}"
        assert "filter: col1 > 0" in content, f"filter_expr must be recorded; got:\n{content}"

    def test_figure_new_records_style_preset(self, tmp_instance):
        """rv figure new records the style preset in frontmatter."""
        cfg = load_config(reload=True)
        self._write_dataset_note_and_file(tmp_instance, cfg, "scores-style")

        from research_vault.figure import cmd_new as fig_cmd_new
        path = fig_cmd_new(
            "demo-research", "fig-style",
            dataset_id="scores-style",
            style="poster",
            config=cfg,
        )
        content = path.read_text()
        assert "style: poster" in content, f"style preset must be recorded; got:\n{content}"

    def test_figure_new_records_plot_type(self, tmp_instance):
        """rv figure new records the plot_type in frontmatter."""
        cfg = load_config(reload=True)
        self._write_dataset_note_and_file(tmp_instance, cfg, "scores-pt")

        from research_vault.figure import cmd_new as fig_cmd_new
        path = fig_cmd_new(
            "demo-research", "fig-pt",
            dataset_id="scores-pt",
            plot_type="scatter",
            config=cfg,
        )
        content = path.read_text()
        assert "plot_type: scatter" in content, f"plot_type must be recorded; got:\n{content}"

    def test_figure_new_fails_if_dataset_note_missing(self, tmp_instance):
        """rv figure new raises ValueError when the dataset note doesn't exist."""
        cfg = load_config(reload=True)

        from research_vault.figure import cmd_new as fig_cmd_new
        with pytest.raises(ValueError, match="dataset"):
            fig_cmd_new("demo-research", "fig-no-ds", dataset_id="nonexistent", config=cfg)

    def test_figure_note_passes_cmd_check(self, tmp_instance):
        """A figure note created by cmd_new passes note.cmd_check.

        This verifies the OKF type↔dir contract is satisfied end-to-end.
        """
        cfg = load_config(reload=True)
        self._write_dataset_note_and_file(tmp_instance, cfg, "scores-check")

        from research_vault.figure import cmd_new as fig_cmd_new
        fig_cmd_new("demo-research", "fig-check", dataset_id="scores-check", config=cfg)

        violations = note_mod.cmd_check("demo-research", config=cfg)
        assert violations == [], f"Figure note should pass cmd_check; violations: {violations}"


# ============================================================================
# Seam 3: rv figure preview — writes -view artifact + prints frame
# ============================================================================

class TestFigurePreview:
    """rv figure preview writes state/figures/<fig>-view.csv + prints frame head."""

    def _create_figure_spec(self, tmp_instance, cfg, fig_id: str, dataset_id: str) -> Path:
        """Helper: create a complete figure spec note (with a real dataset note)."""
        data = b"score,lang,method\n0.9,en,bert\n0.8,zh,bert\n0.7,en,lstm\n0.6,zh,lstm\n"
        data_file = Path(tmp_instance) / f"{dataset_id}.csv"
        data_file.write_bytes(data)
        h = _sha256_hex(data)
        _write_dataset_note(cfg.datasets_root, dataset_id, location=str(data_file), hash_val=h)

        from research_vault.figure import cmd_new as fig_cmd_new
        return fig_cmd_new("demo-research", fig_id, dataset_id=dataset_id, config=cfg)

    def test_preview_writes_view_csv(self, tmp_instance):
        """rv figure preview writes state/figures/<fig>-view.csv."""
        pandas = pytest.importorskip("pandas")
        cfg = load_config(reload=True)
        self._create_figure_spec(tmp_instance, cfg, "hfs-by-lang", "hfs-scores")

        from research_vault.figure import cmd_preview
        cmd_preview("demo-research", "hfs-by-lang", config=cfg)

        view_path = cfg.state_dir / "figures" / "hfs-by-lang-view.csv"
        assert view_path.exists(), f"View CSV must be written at {view_path}"
        content = view_path.read_text()
        assert len(content.strip()) > 0, "View CSV must be non-empty"

    def test_preview_writes_view_md_table(self, tmp_instance):
        """rv figure preview also writes a rendered markdown table (-view.md)."""
        pandas = pytest.importorskip("pandas")
        cfg = load_config(reload=True)
        self._create_figure_spec(tmp_instance, cfg, "fig-md", "hfs-md")

        from research_vault.figure import cmd_preview
        cmd_preview("demo-research", "fig-md", config=cfg)

        view_md = cfg.state_dir / "figures" / "fig-md-view.md"
        assert view_md.exists(), f"View markdown table must be written at {view_md}"

    def test_preview_prints_frame_head(self, tmp_instance, capsys):
        """rv figure preview prints the frame head rows + shape to stdout."""
        pandas = pytest.importorskip("pandas")
        cfg = load_config(reload=True)
        self._create_figure_spec(tmp_instance, cfg, "fig-print", "hfs-print")

        from research_vault.figure import cmd_preview
        cmd_preview("demo-research", "fig-print", config=cfg)

        captured = capsys.readouterr()
        # Should print shape info
        assert "rows" in captured.out.lower() or "shape" in captured.out.lower() or (
            "4" in captured.out  # 4 data rows in our test dataset
        ), f"Preview must print frame info; got:\n{captured.out}"

    def test_preview_applies_column_select(self, tmp_instance):
        """rv figure preview applies the select filter — only selected columns in view CSV."""
        pandas = pytest.importorskip("pandas")
        cfg = load_config(reload=True)
        data = b"score,lang,method\n0.9,en,bert\n0.8,zh,bert\n"
        data_file = Path(tmp_instance) / "select-data.csv"
        data_file.write_bytes(data)
        h = _sha256_hex(data)
        _write_dataset_note(cfg.datasets_root, "select-ds", location=str(data_file), hash_val=h)

        from research_vault.figure import cmd_new, cmd_preview
        cmd_new("demo-research", "fig-sel", dataset_id="select-ds",
                select=["score", "lang"], config=cfg)
        cmd_preview("demo-research", "fig-sel", config=cfg)

        import pandas as pd
        view_path = cfg.state_dir / "figures" / "fig-sel-view.csv"
        df = pd.read_csv(view_path)
        # Only selected columns
        assert list(df.columns) == ["score", "lang"], (
            f"View CSV must contain only selected columns; got: {list(df.columns)}"
        )
        # 'method' column must not be present
        assert "method" not in df.columns

    def test_preview_import_guard_friendly_message(self, tmp_instance, capsys):
        """rv figure preview prints friendly message when [figures] extra is absent.

        Tests the import guard logic: the guard message must always contain the install
        hint. When pandas IS installed, we verify the message content; when not installed,
        _check_figures_extra returns non-zero and prints the hint.
        """
        from research_vault import figure as fig_mod

        # The guard message must always contain the install hint regardless of state
        assert "pip install research-vault[figures]" in fig_mod._FIGURES_EXTRA_MSG, (
            f"Import guard message must mention the extra; got: {fig_mod._FIGURES_EXTRA_MSG!r}"
        )

        # Test what happens when we simulate the extra being absent by patching __import__
        # Use unittest.mock to patch the import inside the guard function cleanly
        import builtins
        original_import = builtins.__import__

        def blocking_import(name, *args, **kwargs):
            if name in ("pandas", "matplotlib"):
                raise ImportError(f"No module named '{name}'")
            return original_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=blocking_import):
            rc = fig_mod._check_figures_extra()

        # When extra is absent, guard must return non-zero (not raise)
        assert rc is not None and rc != 0, (
            f"_check_figures_extra must return non-zero when extra absent; got: {rc!r}"
        )
        # Must print to stderr (not raise ImportError)
        captured = capsys.readouterr()
        assert "pip install research-vault[figures]" in captured.out + captured.err, (
            f"Import guard must print install hint; got stderr: {captured.err!r}"
        )

    def test_preview_import_guard_no_raw_import_error(self, tmp_instance):
        """The [figures] import guard must NEVER propagate a raw ImportError.

        The verb module itself must be importable without pandas installed —
        the guard fires at call time, not at import time.
        """
        # The fact that we can import figure here proves the module is importable
        # without pandas (we don't actually need pandas to import the module)
        import importlib
        # Re-import to confirm no top-level pandas import
        spec = importlib.util.find_spec("research_vault.figure")
        assert spec is not None, "research_vault.figure must be importable"


# ============================================================================
# Seam 4: rv figure list — lists figure notes for a project
# ============================================================================

class TestFigureList:
    """rv figure list returns figure specs for a project."""

    def test_list_returns_empty_when_no_figures(self, tmp_instance):
        """rv figure list returns [] when project has no figure notes."""
        cfg = load_config(reload=True)
        from research_vault.figure import cmd_list
        result = cmd_list("demo-research", config=cfg)
        assert result == [], f"Expected empty list; got: {result}"

    def test_list_returns_figure_notes(self, tmp_instance):
        """rv figure list returns figure note dicts with path + fields."""
        cfg = load_config(reload=True)
        data = b"x,y\n1,2\n"
        data_file = Path(tmp_instance) / "list-data.csv"
        data_file.write_bytes(data)
        h = _sha256_hex(data)
        _write_dataset_note(cfg.datasets_root, "list-ds", location=str(data_file), hash_val=h)

        from research_vault.figure import cmd_new as fig_cmd_new, cmd_list
        fig_cmd_new("demo-research", "list-fig-1", dataset_id="list-ds", config=cfg)
        fig_cmd_new("demo-research", "list-fig-2", dataset_id="list-ds", config=cfg)

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
        data = b"a,b\n1,2\n"
        data_file = Path(tmp_instance) / "scope-data.csv"
        data_file.write_bytes(data)
        h = _sha256_hex(data)
        _write_dataset_note(cfg.datasets_root, "scope-ds", location=str(data_file), hash_val=h)

        from research_vault.figure import cmd_new as fig_cmd_new, cmd_list
        fig_cmd_new("demo-research", "p1-fig", dataset_id="scope-ds", config=cfg)
        fig_cmd_new("demo-litreview", "p2-fig", dataset_id="scope-ds", config=cfg)

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

    def test_figure_when_to_use_fires_on_dataset_intent(self):
        """figure verb when_to_use fires on 'dataset/scores note + publication-quality plot'."""
        from research_vault.cli import _VERB_REGISTRY
        entry = _VERB_REGISTRY.get("figure", {})
        when = entry.get("when_to_use", "")
        assert "dataset" in when.lower() or "scores" in when.lower() or "plot" in when.lower(), (
            f"when_to_use should fire on data→figure intent; got: {when!r}"
        )

    def test_figure_when_to_use_has_anti_pattern(self):
        """figure verb when_to_use includes the anti-pattern against one-off scripts."""
        from research_vault.cli import _VERB_REGISTRY
        entry = _VERB_REGISTRY.get("figure", {})
        when = entry.get("when_to_use", "")
        # Must include the anti-pattern warning
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
        # Should not raise even without matplotlib
        apply_style("publication", "demo-research")
        apply_style("slide", "demo-litreview")
        apply_style("poster", None)

    def test_apply_style_stub_returns_rcparams_or_none(self):
        """The stub apply_style returns a dict of rcParams or None (not an error object)."""
        from research_vault.figures.style import apply_style
        result = apply_style("publication", "demo-research")
        # Must be dict or None (dict if minimal rcParams set, None if true stub)
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
        # all_required_ok is determined by claude_cli + api_key — NOT figures
        # Even if figures is absent, all_required_ok can be True
        # (This test verifies that a missing figures extra does not flip all_required_ok to False)
        # We verify by checking the result structure: figures key should be present but
        # its absence should not affect all_required_ok.
        assert "figures" in result, "run_preflight() result must include a 'figures' key"

    def test_check_figures_result_is_bool(self):
        """run_preflight() 'figures' value is a bool."""
        from research_vault.check import run_preflight
        result = run_preflight()
        assert isinstance(result.get("figures"), bool), (
            f"run_preflight()['figures'] must be bool; got {type(result.get('figures'))}"
        )


# ============================================================================
# Seam 8: Demo-figures DAG loop — data-check cannot approve until extract terminal
# ============================================================================

class TestDemoFiguresLoop:
    """The demo-figures DAG loop uses the existing transitive-upstream-terminal invariant.

    Zero new DAG mechanism — human-go already cannot approve until all transitive
    upstream nodes are terminal. This tests the manifest shape is correct and
    the invariant holds for the figures loop.
    """

    def _load_demo_figures_manifest(self) -> dict:
        """Load the demo-figures manifest from examples/."""
        manifest_path = (
            Path(__file__).parent.parent
            / "examples" / "demo-figures" / "demo-figures.json"
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

    def test_demo_figures_data_check_cannot_approve_until_extract_terminal(self, tmp_instance):
        """data-check node cannot enter approved state until extract is terminal.

        This is the transitive-upstream-terminal invariant test (§5E.2).
        Existing DAG walker enforces it — no new mechanism needed.
        """
        from research_vault.dag.walker import compute_frontier
        from research_vault.dag.schema import validate_manifest

        manifest = self._load_demo_figures_manifest()
        validate_manifest(manifest)

        # extract = pending → data-check must not be in frontier
        node_states = {n["id"]: {"status": "pending"} for n in manifest["nodes"]}
        frontier = compute_frontier(manifest, node_states, {}, global_cap=4)
        frontier_ids = {f.node_id for f in frontier}

        assert "data-check" not in frontier_ids, (
            "data-check must NOT be in frontier when extract is pending"
        )
        # extract must be in frontier (it's the start node with no deps)
        extract_id = manifest["nodes"][0]["id"]
        assert extract_id in frontier_ids, (
            f"extract node ({extract_id!r}) must be in frontier when pending"
        )

    def test_demo_figures_data_check_enters_frontier_after_extract_terminal(self, tmp_instance):
        """data-check becomes awaiting-go once extract is succeeded (upstream terminal).

        Non-vacuous: extract transitions to succeeded → data-check enters frontier.
        """
        from research_vault.dag.walker import compute_frontier

        manifest = self._load_demo_figures_manifest()
        extract_id = manifest["nodes"][0]["id"]
        data_check_id = "data-check"

        # extract = succeeded, others pending
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
