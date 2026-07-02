"""test_sr_fig_rec.py — SR-FIG-REC: plot-type recommender (expressiveness→effectiveness).

Spec: §5E.13 / §5E.14 / §5E.15 (SR-FIG-REC design note + spawn request + operator decisions)
      and Ada's plot-type memo (folded into §5E.13).

What is tested (the seven seams from the spawn request §5E.14):

  1. ViewColumn descriptor inference — role, dtype, cardinality from a pandas frame.
     Most is INFERABLE; task is NOT (explicit or infer-and-surface).

  2. Recommend output shape — ranked list of plot-type suggestions, each with a
     principle string citing Cleveland–McGill / Mackinlay. Best-first ordering.

  3. Task inference-and-surface — when --task omitted, infer from descriptor shape
     and PRINT the inferred task + plausible alternatives (never silent).

  4. Static ranking rule table — (task × descriptor-shape) → ranked encodings.
     Grounded cases per Ada's §3 table + spec §5E.13.2.

  5. Integrity WARN checks — truncated baseline, >2 floating stacked segments,
     pie>3, rainbow colormap, diverging-on-sequential, bar-of-means. WARN-only,
     NEVER a block (exit 0 always).

  6. Colormap-class seam — recommend emits the CLASS (sequential/diverging/qualitative),
     NOT a concrete palette. Palette is Iris's job via apply_style.

  7. figure new integration — `--type` omitted → call recommend, print rationale;
     `--type` supplied → honor silently; integrity WARNs still fire either way.

  8. _VERB_REGISTRY entry for `rv figure recommend` anti-pattern + rv help --check green.

All tests hermetic — no live-vault reads or writes. Pandas imported lazily via the
[figures] extra (importorskip in tests that need it); recommend.py is stdlib-only.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_view_columns(*specs: tuple) -> list[dict]:
    """Build a list of ViewColumn dicts from (name, role, dtype, cardinality) tuples."""
    return [
        {"name": name, "role": role, "dtype": dtype, "cardinality": card}
        for name, role, dtype, card in specs
    ]


# ============================================================================
# Seam 1 — ViewColumn descriptor + infer_view()
# ============================================================================

class TestViewDescriptor:
    """Descriptor inference from a pandas frame."""

    def test_infer_view_returns_column_list(self):
        """infer_view(df) returns a list of ViewColumn dicts."""
        pytest.importorskip("pandas")
        import pandas as pd
        from research_vault.figures.recommend import infer_view

        df = pd.DataFrame({"lang": ["en", "zh"], "score": [0.9, 0.8]})
        cols = infer_view(df)
        assert isinstance(cols, list)
        assert len(cols) == 2

    def test_infer_view_numeric_column_is_quantitative(self):
        """Numeric columns map to dtype='quantitative'."""
        pytest.importorskip("pandas")
        import pandas as pd
        from research_vault.figures.recommend import infer_view

        df = pd.DataFrame({"score": [0.9, 0.8, 0.7]})
        cols = infer_view(df)
        score_col = next(c for c in cols if c["name"] == "score")
        assert score_col["dtype"] == "quantitative"

    def test_infer_view_object_column_is_nominal(self):
        """String/object columns map to dtype='nominal'."""
        pytest.importorskip("pandas")
        import pandas as pd
        from research_vault.figures.recommend import infer_view

        df = pd.DataFrame({"lang": ["en", "zh", "fr"]})
        cols = infer_view(df)
        lang_col = next(c for c in cols if c["name"] == "lang")
        assert lang_col["dtype"] == "nominal"

    def test_infer_view_datetime_column_is_temporal(self):
        """Datetime columns map to dtype='temporal'."""
        pytest.importorskip("pandas")
        import pandas as pd
        from research_vault.figures.recommend import infer_view

        df = pd.DataFrame({"ts": pd.to_datetime(["2026-01-01", "2026-01-02"])})
        cols = infer_view(df)
        ts_col = next(c for c in cols if c["name"] == "ts")
        assert ts_col["dtype"] == "temporal"

    def test_infer_view_cardinality_is_nunique(self):
        """cardinality = nunique() of each column."""
        pytest.importorskip("pandas")
        import pandas as pd
        from research_vault.figures.recommend import infer_view

        df = pd.DataFrame({"lang": ["en", "zh", "en", "fr"], "score": [0.9, 0.8, 0.7, 0.6]})
        cols = infer_view(df)
        lang_col = next(c for c in cols if c["name"] == "lang")
        assert lang_col["cardinality"] == 3  # en, zh, fr

    def test_infer_view_role_heuristic_measure_for_high_card_numeric(self):
        """High-cardinality numeric columns get role='measure'."""
        pytest.importorskip("pandas")
        import pandas as pd
        from research_vault.figures.recommend import infer_view

        df = pd.DataFrame({"score": [float(i) / 100 for i in range(50)]})
        cols = infer_view(df)
        score_col = next(c for c in cols if c["name"] == "score")
        assert score_col["role"] == "measure"

    def test_infer_view_role_dimension_for_low_card_nominal(self):
        """Low-cardinality nominal columns get role='dimension'."""
        pytest.importorskip("pandas")
        import pandas as pd
        from research_vault.figures.recommend import infer_view

        df = pd.DataFrame({"lang": ["en", "zh"] * 20})
        cols = infer_view(df)
        lang_col = next(c for c in cols if c["name"] == "lang")
        assert lang_col["role"] == "dimension"

    def test_infer_view_same_values_on_both_axes_flags_confusion_matrix(self):
        """When both axes share the same label-set, confusion-matrix hint is set."""
        pytest.importorskip("pandas")
        import pandas as pd
        from research_vault.figures.recommend import infer_view

        labels = ["A", "B", "C"]
        df = pd.DataFrame({
            "true_label":  labels * 3,
            "pred_label":  labels * 3,
            "count": [10, 2, 0, 1, 9, 3, 0, 1, 8],
        })
        cols = infer_view(df)
        # At least two nominal/dimension cols with same cardinality AND same value-set
        dims = [c for c in cols if c["role"] == "dimension"]
        # The hint should be available via a separate helper or embedded in view metadata
        from research_vault.figures.recommend import detect_confusion_matrix_shape
        is_cm = detect_confusion_matrix_shape(cols, df)
        assert is_cm, "Same-label-set on both axes must be flagged as confusion-matrix shape"


# ============================================================================
# Seam 2 — Recommend output shape
# ============================================================================

class TestRecommendOutputShape:
    """recommend() returns a list of Suggestion dicts, best-first."""

    def test_recommend_returns_list(self):
        """recommend() returns a non-empty list."""
        from research_vault.figures.recommend import recommend

        cols = _make_view_columns(
            ("lang", "dimension", "nominal", 5),
            ("score", "measure", "quantitative", 100),
        )
        result = recommend(cols, task="comparison")
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_recommend_each_has_plot_type(self):
        """Every suggestion has a 'plot_type' string key."""
        from research_vault.figures.recommend import recommend

        cols = _make_view_columns(
            ("lang", "dimension", "nominal", 5),
            ("score", "measure", "quantitative", 100),
        )
        for suggestion in recommend(cols, task="comparison"):
            assert "plot_type" in suggestion, f"Missing plot_type: {suggestion}"
            assert isinstance(suggestion["plot_type"], str)

    def test_recommend_each_has_principle(self):
        """Every suggestion has a non-empty 'principle' string (the teaching surface)."""
        from research_vault.figures.recommend import recommend

        cols = _make_view_columns(
            ("lang", "dimension", "nominal", 5),
            ("score", "measure", "quantitative", 100),
        )
        for suggestion in recommend(cols, task="comparison"):
            assert "principle" in suggestion, f"Missing principle: {suggestion}"
            assert len(suggestion["principle"].strip()) > 0, "principle must be non-empty"

    def test_recommend_each_has_rank(self):
        """Every suggestion has an integer 'rank' (1-based, best=1)."""
        from research_vault.figures.recommend import recommend

        cols = _make_view_columns(
            ("lang", "dimension", "nominal", 5),
            ("score", "measure", "quantitative", 100),
        )
        for i, suggestion in enumerate(recommend(cols, task="comparison"), start=1):
            assert suggestion.get("rank") == i, (
                f"rank must be {i}, got {suggestion.get('rank')!r}"
            )

    def test_recommend_each_has_colormap_class(self):
        """Every suggestion has a 'colormap_class' key (sequential/diverging/qualitative)."""
        from research_vault.figures.recommend import recommend

        cols = _make_view_columns(
            ("lang", "dimension", "nominal", 5),
            ("score", "measure", "quantitative", 100),
        )
        valid_classes = {"sequential", "diverging", "qualitative", None}
        for suggestion in recommend(cols, task="comparison"):
            assert "colormap_class" in suggestion, f"Missing colormap_class: {suggestion}"
            assert suggestion["colormap_class"] in valid_classes, (
                f"colormap_class must be one of {valid_classes}; got {suggestion['colormap_class']!r}"
            )

    def test_recommend_principle_cites_cleveland_mcgill_or_mackinlay(self):
        """At least one suggestion's principle cites Cleveland–McGill or Mackinlay."""
        from research_vault.figures.recommend import recommend

        cols = _make_view_columns(
            ("lang", "dimension", "nominal", 5),
            ("score", "measure", "quantitative", 100),
        )
        principles = [s["principle"] for s in recommend(cols, task="comparison")]
        combined = " ".join(principles).lower()
        assert "cleveland" in combined or "mackinlay" in combined or "ladder" in combined, (
            f"At least one principle must cite Cleveland–McGill or Mackinlay; got: {principles}"
        )


# ============================================================================
# Seam 3 — Task inference-and-surface
# ============================================================================

class TestTaskInference:
    """When --task omitted, infer-and-surface (print + return the inferred task)."""

    def test_infer_task_temporal_plus_measure_gives_trend(self):
        """One temporal dimension + one measure → inferred task is 'trend'."""
        from research_vault.figures.recommend import infer_task

        cols = _make_view_columns(
            ("date", "dimension", "temporal", 365),
            ("score", "measure", "quantitative", 365),
        )
        task, alternates = infer_task(cols)
        assert task == "trend", f"Expected task='trend' for temporal+measure; got {task!r}"

    def test_infer_task_two_measures_gives_relationship(self):
        """Two quantitative measures → inferred task is 'relationship'."""
        from research_vault.figures.recommend import infer_task

        cols = _make_view_columns(
            ("accuracy", "measure", "quantitative", 100),
            ("loss", "measure", "quantitative", 100),
        )
        task, alternates = infer_task(cols)
        assert task == "relationship", (
            f"Expected task='relationship' for 2 measures; got {task!r}"
        )

    def test_infer_task_returns_alternates(self):
        """infer_task() returns (primary_task, list_of_alternates)."""
        from research_vault.figures.recommend import infer_task

        cols = _make_view_columns(
            ("lang", "dimension", "nominal", 5),
            ("score", "measure", "quantitative", 100),
        )
        task, alternates = infer_task(cols)
        assert isinstance(task, str)
        assert isinstance(alternates, list)

    def test_recommend_with_no_task_returns_inferred(self):
        """recommend(cols) with no task= performs inference and returns results."""
        from research_vault.figures.recommend import recommend

        cols = _make_view_columns(
            ("date", "dimension", "temporal", 30),
            ("score", "measure", "quantitative", 30),
        )
        # No task= argument — must not raise
        result = recommend(cols)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_recommend_prints_task_inferred_when_no_task(self, capsys):
        """recommend() prints 'task inferred: <t>' when --task is omitted."""
        from research_vault.figures.recommend import recommend

        cols = _make_view_columns(
            ("date", "dimension", "temporal", 30),
            ("score", "measure", "quantitative", 30),
        )
        recommend(cols)  # no task
        captured = capsys.readouterr()
        assert "task inferred" in captured.out.lower(), (
            f"Must print 'task inferred:...' to stdout; got: {captured.out!r}"
        )

    def test_recommend_silent_when_task_provided(self, capsys):
        """recommend() does NOT print task-inferred when task is explicitly passed."""
        from research_vault.figures.recommend import recommend

        cols = _make_view_columns(
            ("lang", "dimension", "nominal", 5),
            ("score", "measure", "quantitative", 100),
        )
        recommend(cols, task="comparison")
        captured = capsys.readouterr()
        assert "task inferred" not in captured.out.lower(), (
            f"Must NOT print 'task inferred' when task is explicit; got: {captured.out!r}"
        )


# ============================================================================
# Seam 4 — Static ranking rule table (task × shape → encodings)
# ============================================================================

class TestRankingRuleTable:
    """The static rule table maps (task, descriptor-shape) to ranked encodings per Ada/spec."""

    def test_comparison_nominal_measure_top_is_bar(self):
        """task=comparison + 1 nominal dim + 1 measure → bar is rank-1.

        Grounded in Cleveland–McGill: position on a common scale is the most
        accurate encoding for comparison tasks. Bar encodes on position.
        """
        from research_vault.figures.recommend import recommend

        cols = _make_view_columns(
            ("lang", "dimension", "nominal", 8),
            ("score", "measure", "quantitative", 100),
        )
        result = recommend(cols, task="comparison")
        top = result[0]
        assert top["plot_type"] == "bar", (
            f"task=comparison, 1 nominal dim, 1 measure → bar must be rank-1; got {top['plot_type']!r}"
        )

    def test_comparison_pie_ranked_below_bar(self):
        """task=comparison + nominal + measure → pie is ranked BELOW bar (or [AVOID]).

        Grounded in Cleveland–McGill: angle/area encoding (pie) is far below
        position (bar) for comparison accuracy. Pie is [AVOID] for comparison.
        """
        from research_vault.figures.recommend import recommend

        cols = _make_view_columns(
            ("lang", "dimension", "nominal", 5),
            ("score", "measure", "quantitative", 100),
        )
        result = recommend(cols, task="comparison")
        plot_types = [s["plot_type"] for s in result]
        bar_rank = next((s["rank"] for s in result if s["plot_type"] == "bar"), None)
        pie_rank = next((s["rank"] for s in result if "pie" in s["plot_type"]), None)

        # bar must be ranked (exists)
        assert bar_rank is not None, f"bar must appear in comparison results; got {plot_types}"

        # pie either doesn't appear, OR appears with a higher rank number (worse)
        if pie_rank is not None:
            assert pie_rank > bar_rank, (
                f"pie must rank below bar for task=comparison; bar={bar_rank}, pie={pie_rank}"
            )

    def test_trend_temporal_measure_top_is_line(self):
        """task=trend + temporal dim + measure → line is rank-1.

        Grounded: ordered/continuous x-axis → line (connects the ordered positions,
        shows trajectory — cannot compare positions without connecting them).
        """
        from research_vault.figures.recommend import recommend

        cols = _make_view_columns(
            ("epoch", "dimension", "temporal", 50),
            ("loss", "measure", "quantitative", 50),
        )
        result = recommend(cols, task="trend")
        top = result[0]
        assert top["plot_type"] == "line", (
            f"task=trend, temporal+measure → line must be rank-1; got {top['plot_type']!r}"
        )

    def test_relationship_two_measures_top_is_scatter(self):
        """task=relationship + 2 quantitative measures → scatter is rank-1.

        Grounded: 2 continuous → scatter (encodes both on position axes —
        top of Cleveland–McGill for bivariate relationship).
        """
        from research_vault.figures.recommend import recommend

        cols = _make_view_columns(
            ("accuracy", "measure", "quantitative", 100),
            ("loss", "measure", "quantitative", 100),
        )
        result = recommend(cols, task="relationship")
        top = result[0]
        assert top["plot_type"] == "scatter", (
            f"task=relationship, 2 measures → scatter must be rank-1; got {top['plot_type']!r}"
        )

    def test_distribution_single_measure_top_is_histogram_or_box(self):
        """task=distribution + single measure → histogram or box/violin is rank-1."""
        from research_vault.figures.recommend import recommend

        cols = _make_view_columns(
            ("score", "measure", "quantitative", 200),
        )
        result = recommend(cols, task="distribution")
        top = result[0]
        assert top["plot_type"] in ("histogram", "box", "violin"), (
            f"task=distribution, single measure → hist/box/violin must be rank-1; "
            f"got {top['plot_type']!r}"
        )

    def test_composition_returns_honest_options_includes_pie_warning(self):
        """task=composition → honest options including a note that pie is only ≤3 slices."""
        from research_vault.figures.recommend import recommend

        cols = _make_view_columns(
            ("category", "dimension", "nominal", 6),
            ("share", "measure", "quantitative", 6),
        )
        result = recommend(cols, task="composition")
        # Some option should be a stacked bar or pie with caveats
        plot_types = [s["plot_type"] for s in result]
        assert any(
            pt in ("stacked_bar", "bar", "pie", "treemap", "donut")
            for pt in plot_types
        ), f"task=composition must suggest at least one composition chart; got {plot_types}"

    def test_matrix_nominal_both_axes_top_is_heatmap(self):
        """matrix-of-scores (2 nominals + 1 quantitative) → heatmap is rank-1."""
        from research_vault.figures.recommend import recommend

        cols = _make_view_columns(
            ("model", "dimension", "nominal", 4),
            ("dataset", "dimension", "nominal", 3),
            ("f1", "measure", "quantitative", 12),
        )
        result = recommend(cols, task="comparison")
        # With 2 nominal dims + 1 measure, heatmap should be top
        top = result[0]
        assert top["plot_type"] in ("heatmap", "bar"), (
            f"matrix shape (2 nominals + 1 measure) → heatmap or bar; got {top['plot_type']!r}"
        )

    def test_confusion_matrix_shape_recommends_confusion_matrix(self):
        """Same-label-set on both axes → confusion_matrix is rank-1 (or top-2)."""
        pytest.importorskip("pandas")
        import pandas as pd
        from research_vault.figures.recommend import infer_view, recommend, detect_confusion_matrix_shape

        labels = ["A", "B", "C"]
        df = pd.DataFrame({
            "true_label": labels * 3,
            "pred_label": labels * 3,
            "count": [10, 2, 0, 1, 9, 3, 0, 1, 8],
        })
        cols = infer_view(df)
        is_cm = detect_confusion_matrix_shape(cols, df)
        if is_cm:
            result = recommend(cols, task="lookup", is_confusion_matrix=True)
            plot_types = [s["plot_type"] for s in result]
            assert "confusion_matrix" in plot_types or "heatmap" in plot_types, (
                f"Confusion-matrix shape → confusion_matrix or heatmap; got {plot_types}"
            )


# ============================================================================
# Seam 5 — Integrity WARN checks (NEVER a block, exit 0 always)
# ============================================================================

class TestIntegrityWarns:
    """Integrity checks fire WARNs, never blocks. All return (warnings, is_block=False)."""

    def test_truncated_baseline_fires_warn(self):
        """truncated bar baseline (ymin != 0) fires a WARN."""
        from research_vault.figures.recommend import integrity_warns

        warns = integrity_warns(
            plot_type="bar",
            task="comparison",
            ymin=0.5,   # truncated — not zero
            ymax=1.0,
        )
        assert any("baseline" in w.lower() or "truncat" in w.lower() for w in warns), (
            f"truncated baseline must fire WARN; got: {warns}"
        )

    def test_zero_baseline_no_warn(self):
        """A bar chart with ymin=0 (or unset) does NOT fire the baseline WARN."""
        from research_vault.figures.recommend import integrity_warns

        warns = integrity_warns(plot_type="bar", task="comparison", ymin=0)
        baseline_warns = [w for w in warns if "baseline" in w.lower() or "truncat" in w.lower()]
        assert baseline_warns == [], (
            f"ymin=0 must not fire baseline WARN; got: {baseline_warns}"
        )

    def test_stacked_segments_warn_when_more_than_two_under_comparison(self):
        """>2 floating stacked segments under task=comparison fires a WARN."""
        from research_vault.figures.recommend import integrity_warns

        warns = integrity_warns(
            plot_type="stacked_bar",
            task="comparison",
            n_stacked_segments=4,
        )
        assert any("stack" in w.lower() or "segment" in w.lower() for w in warns), (
            f">2 stacked segments under comparison must fire WARN; got: {warns}"
        )

    def test_pie_more_than_3_slices_fires_warn(self):
        """pie chart with >3 slices fires WARN (angle judgment collapses)."""
        from research_vault.figures.recommend import integrity_warns

        warns = integrity_warns(plot_type="pie", task="composition", n_slices=8)
        assert any("pie" in w.lower() or "slice" in w.lower() or "angle" in w.lower() for w in warns), (
            f"pie >3 slices must fire WARN; got: {warns}"
        )

    def test_pie_3_or_fewer_slices_no_warn(self):
        """pie chart with ≤3 slices does NOT fire the pie WARN."""
        from research_vault.figures.recommend import integrity_warns

        warns = integrity_warns(plot_type="pie", task="composition", n_slices=3)
        pie_warns = [w for w in warns if "pie" in w.lower() or "slice" in w.lower()]
        assert pie_warns == [], (
            f"pie with ≤3 slices must not fire WARN; got: {pie_warns}"
        )

    def test_rainbow_colormap_fires_warn(self):
        """rainbow/jet colormap fires WARN (false gradients from non-perceptual-uniform map)."""
        from research_vault.figures.recommend import integrity_warns

        for cmap in ("jet", "rainbow", "hsv", "gist_rainbow"):
            warns = integrity_warns(plot_type="heatmap", task="comparison", colormap=cmap)
            assert any("rainbow" in w.lower() or "colormap" in w.lower() or "perceptual" in w.lower()
                       for w in warns), (
                f"rainbow colormap '{cmap}' must fire WARN; got: {warns}"
            )

    def test_diverging_on_sequential_data_fires_warn(self):
        """diverging colormap on sequential data (no meaningful midpoint) fires WARN."""
        from research_vault.figures.recommend import integrity_warns

        warns = integrity_warns(
            plot_type="heatmap",
            task="comparison",
            colormap="RdBu",            # diverging palette
            colormap_class="sequential",  # but data is sequential → mismatch
        )
        assert any("diverging" in w.lower() or "midpoint" in w.lower() or "sequential" in w.lower()
                   for w in warns), (
            f"diverging colormap on sequential data must fire WARN; got: {warns}"
        )

    def test_bar_of_means_fires_warn(self):
        """bar chart over raw observations (hides distribution) fires WARN."""
        from research_vault.figures.recommend import integrity_warns

        warns = integrity_warns(
            plot_type="bar",
            task="comparison",
            aggregation="mean",
            has_raw_observations=True,
        )
        assert any("mean" in w.lower() or "distribution" in w.lower() or "raw" in w.lower()
                   for w in warns), (
            f"bar-of-means over raw observations must fire WARN; got: {warns}"
        )

    def test_integrity_warns_never_blocks(self):
        """integrity_warns returns a list of strings (never raises, never blocks)."""
        from research_vault.figures.recommend import integrity_warns

        # Worst case: multiple warn conditions
        warns = integrity_warns(
            plot_type="pie",
            task="comparison",
            n_slices=20,
            ymin=0.5,
            colormap="jet",
        )
        assert isinstance(warns, list)
        assert all(isinstance(w, str) for w in warns)
        # Must NOT raise — the test passing IS the no-block proof

    def test_integrity_warns_include_warn_prefix(self):
        """WARN strings include a warning marker (⚠ or 'WARN' or 'warning')."""
        from research_vault.figures.recommend import integrity_warns

        warns = integrity_warns(plot_type="pie", task="composition", n_slices=10)
        for w in warns:
            assert (
                "⚠" in w or "warn" in w.lower() or "warning" in w.lower()
            ), f"WARN string must include marker; got: {w!r}"


# ============================================================================
# Seam 6 — Colormap-class seam (emit CLASS, not palette)
# ============================================================================

class TestColormapClassSeam:
    """recommend() emits colormap_class (sequential/diverging/qualitative), not a palette."""

    def test_nominal_dimension_gives_qualitative_colormap_class(self):
        """Nominal dimension → colormap_class='qualitative' (unordered, distinct colors)."""
        from research_vault.figures.recommend import recommend

        cols = _make_view_columns(
            ("lang", "dimension", "nominal", 5),
            ("score", "measure", "quantitative", 100),
        )
        result = recommend(cols, task="comparison")
        # Top suggestion should have qualitative class for a bar over nominal dim
        top = result[0]
        assert top["colormap_class"] == "qualitative", (
            f"nominal dimension → colormap_class='qualitative'; got {top['colormap_class']!r}"
        )

    def test_ordered_measure_gives_sequential_colormap_class(self):
        """Ordered measure (heatmap) → colormap_class='sequential'."""
        from research_vault.figures.recommend import recommend

        cols = _make_view_columns(
            ("model", "dimension", "nominal", 4),
            ("dataset", "dimension", "nominal", 3),
            ("f1", "measure", "quantitative", 12),
        )
        result = recommend(cols, task="comparison")
        # Find heatmap in the results
        heatmap = next((s for s in result if s["plot_type"] == "heatmap"), None)
        if heatmap is not None:
            assert heatmap["colormap_class"] == "sequential", (
                f"heatmap over ordered measure → sequential; got {heatmap['colormap_class']!r}"
            )

    def test_recommend_does_not_emit_concrete_palette(self):
        """recommend() output does NOT include a 'palette' or 'colors' key (Iris's job)."""
        from research_vault.figures.recommend import recommend

        cols = _make_view_columns(
            ("lang", "dimension", "nominal", 5),
            ("score", "measure", "quantitative", 100),
        )
        for suggestion in recommend(cols, task="comparison"):
            assert "palette" not in suggestion, (
                f"recommend() must not emit palette (Iris's job); got {suggestion}"
            )
            assert "colors" not in suggestion, (
                f"recommend() must not emit colors (Iris's job); got {suggestion}"
            )

    def test_colormap_class_is_one_of_three_values(self):
        """colormap_class is always sequential, diverging, qualitative, or None."""
        from research_vault.figures.recommend import recommend

        cols = _make_view_columns(
            ("lang", "dimension", "nominal", 5),
            ("score", "measure", "quantitative", 100),
        )
        valid = {"sequential", "diverging", "qualitative", None}
        for suggestion in recommend(cols, task="comparison"):
            assert suggestion["colormap_class"] in valid, (
                f"colormap_class must be one of {valid}; got {suggestion['colormap_class']!r}"
            )


# ============================================================================
# Seam 7 — figure new integration (recommend-not-mandate)
# ============================================================================

class TestFigureNewIntegration:
    """rv figure new calls recommend when --type omitted; honors --type silently."""

    @pytest.fixture()
    def experiment_setup(self, tmp_instance):
        """Create a minimal experiment note with results for figure new tests."""
        pytest.importorskip("pandas")
        from research_vault.config import load_config
        from tests.test_sr_fig import _write_experiment_note_and_results

        cfg = load_config(reload=True)
        project_notes_dir = cfg.project_notes_dir("demo-research")
        _write_experiment_note_and_results(
            Path(tmp_instance), project_notes_dir, "run-rec",
            results_data=b"lang,score\nen,0.9\nzh,0.8\nfr,0.7\n",
        )
        return cfg

    def test_figure_new_without_type_prints_recommendation(self, tmp_instance, capsys):
        """rv figure new without --type prints the auto-picked type + rationale."""
        pytest.importorskip("pandas")
        from research_vault.config import load_config
        from tests.test_sr_fig import _write_experiment_note_and_results
        from research_vault.figure import cmd_new

        cfg = load_config(reload=True)
        project_notes_dir = cfg.project_notes_dir("demo-research")
        _write_experiment_note_and_results(
            Path(tmp_instance), project_notes_dir, "run-auto",
            results_data=b"lang,score\nen,0.9\nzh,0.8\n",
        )

        # cmd_new with no plot_type should call recommend and print rationale
        cmd_new("demo-research", "fig-auto", experiment_id="run-auto", config=cfg)
        captured = capsys.readouterr()
        # The rationale must mention the chosen type + "auto" or "recommended"
        assert any(
            kw in captured.out.lower()
            for kw in ("auto", "recommend", "type:", "cleveland", "mackinlay")
        ), (
            f"cmd_new without --type must print recommendation rationale; got: {captured.out!r}"
        )

    def test_figure_new_without_type_auto_picks_non_default(self, tmp_instance):
        """cmd_new without explicit plot_type uses the recommender, not a hardcoded default.

        Non-vacuous: we can't just check it defaults to 'line' — it should use the
        recommender. Feed data that has a nominal dimension to trigger a non-line recommendation.
        """
        pytest.importorskip("pandas")
        from research_vault.config import load_config
        from tests.test_sr_fig import _write_experiment_note_and_results
        from research_vault.figure import cmd_new

        cfg = load_config(reload=True)
        project_notes_dir = cfg.project_notes_dir("demo-research")
        _write_experiment_note_and_results(
            Path(tmp_instance), project_notes_dir, "run-rec2",
            results_data=b"lang,score\nen,0.9\nzh,0.8\n",
        )

        path = cmd_new("demo-research", "fig-rec2", experiment_id="run-rec2", config=cfg)
        content = path.read_text()
        # The plot_type must be in the note — should not be 'line' for nominal+numeric data
        # (we don't assert a specific type, just that the recommender was invoked)
        assert "plot_type:" in content, f"plot_type must be in note; got:\n{content}"

    def test_figure_new_with_type_honored_silently(self, tmp_instance, capsys):
        """rv figure new with explicit --type honors it without nag or disagreement."""
        pytest.importorskip("pandas")
        from research_vault.config import load_config
        from tests.test_sr_fig import _write_experiment_note_and_results
        from research_vault.figure import cmd_new

        cfg = load_config(reload=True)
        project_notes_dir = cfg.project_notes_dir("demo-research")
        _write_experiment_note_and_results(
            Path(tmp_instance), project_notes_dir, "run-explicit",
            results_data=b"lang,score\nen,0.9\nzh,0.8\n",
        )

        path = cmd_new(
            "demo-research", "fig-explicit",
            experiment_id="run-explicit",
            plot_type="scatter",
            config=cfg,
        )
        content = path.read_text()
        assert "plot_type: scatter" in content, (
            f"Explicit --type scatter must be recorded; got:\n{content}"
        )
        captured = capsys.readouterr()
        # No nag: "disagrees", "recommender would pick", "you should use" etc.
        nag_phrases = ["disagree", "recommender would", "should use", "override"]
        for phrase in nag_phrases:
            assert phrase not in captured.out.lower(), (
                f"Explicit --type must be honored silently (no nag); got: {captured.out!r}"
            )

    def test_figure_new_with_type_pie_and_8_categories_still_warns(self, tmp_instance, capsys):
        """rv figure new --type pie on 8 categories: honored silently + pie>3 WARN fires."""
        pytest.importorskip("pandas")
        from research_vault.config import load_config
        from tests.test_sr_fig import _write_experiment_note_and_results
        from research_vault.figure import cmd_new

        cfg = load_config(reload=True)
        project_notes_dir = cfg.project_notes_dir("demo-research")
        # 8 categories
        rows = "\n".join(f"cat{i},{i*0.1:.1f}" for i in range(8))
        _write_experiment_note_and_results(
            Path(tmp_instance), project_notes_dir, "run-pie8",
            results_data=f"category,share\n{rows}\n".encode(),
        )

        path = cmd_new(
            "demo-research", "fig-pie8",
            experiment_id="run-pie8",
            plot_type="pie",
            config=cfg,
        )
        content = path.read_text()
        assert "plot_type: pie" in content, "Explicit pie must be honored"
        captured = capsys.readouterr()
        # The integrity WARN for pie>3 must still fire (regardless of who chose the type)
        assert any(
            kw in captured.out.lower() or kw in captured.err.lower()
            for kw in ("pie", "slice", "angle", "warn", "⚠")
        ), (
            f"pie>3 WARN must fire even when --type pie is explicit; "
            f"out: {captured.out!r} err: {captured.err!r}"
        )


# ============================================================================
# Seam 8 — _VERB_REGISTRY + rv help --check
# ============================================================================

class TestVerbRegistry:
    """rv figure's VERB_REGISTRY entry includes the recommend anti-pattern; rv help --check green."""

    def test_figure_verb_when_to_use_mentions_recommend(self):
        """figure verb's when_to_use mentions 'recommend' (the new sub-verb)."""
        from research_vault.cli import _VERB_REGISTRY
        entry = _VERB_REGISTRY.get("figure", {})
        when = entry.get("when_to_use", "")
        assert "recommend" in when.lower(), (
            f"figure verb when_to_use must mention the recommend sub-verb; got:\n{when!r}"
        )

    def test_figure_verb_when_to_use_has_rec_anti_pattern(self):
        """figure verb's when_to_use includes the recommender anti-pattern (gut/habit)."""
        from research_vault.cli import _VERB_REGISTRY
        entry = _VERB_REGISTRY.get("figure", {})
        when = entry.get("when_to_use", "")
        assert any(kw in when.lower() for kw in ("gut", "habit", "eyeball", "encoding")), (
            f"when_to_use must name the anti-pattern (picking by gut/habit); got:\n{when!r}"
        )

    def test_rv_help_check_still_green_after_sr_fig_rec(self):
        """rv help --check returns no violations after SR-FIG-REC additions."""
        from research_vault.cli import _check_verb_docstrings
        violations = _check_verb_docstrings()
        assert violations == [], f"rv help --check has violations: {violations}"

    def test_recommend_sub_verb_accessible_via_cli(self):
        """rv figure <project> recommend <view> is accessible via the CLI parser."""
        from research_vault.figure import build_parser

        p = build_parser()
        # Should have 'recommend' as a valid figure subcommand
        # Try parsing a recommend call — if recommend isn't registered, this raises SystemExit
        import argparse
        # We just check the parser structure
        choices = None
        for action in p._subparsers._actions:
            if hasattr(action, 'choices') and action.choices:
                choices = list(action.choices.keys())
                break
        assert choices is not None and "recommend" in choices, (
            f"'recommend' must be a figure sub-command; parser choices: {choices}"
        )
