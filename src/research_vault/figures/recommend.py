"""figures/recommend.py — SR-FIG-REC: plot-type recommender.

Turns a data-view descriptor into ranked plot-type suggestions + principle strings,
grounded in:
  - Cleveland & McGill (1984) "Graphical Perception" — the accuracy ladder:
      position on a common scale > length > angle/area > color/volume
  - Mackinlay (1986) "Automating the Design of Graphical Presentations" —
      expressiveness (the encoding must faithfully express the data's structure)
      then effectiveness (rank encodings by perceptual accuracy for the task)

This module is STDLIB ONLY (no pandas/matplotlib at import time). pandas is
used only in infer_view() and detect_confusion_matrix_shape(), and callers
(figure.py) already guard the [figures] extra before calling those functions.

Key contracts:
  - recommend() → list of Suggestion dicts, ranked best-first (rank=1 is best)
  - Each Suggestion: {rank, plot_type, principle, colormap_class}
  - colormap_class ∈ {"sequential", "diverging", "qualitative", None}
    — the CORRECTNESS call; the designer picks the palette within the class
  - integrity_warns() → list of WARN strings (never empty-but-truthy, never raises)
  - infer_task() → (primary_task, [alternatives]) — inference from descriptor shape
  - infer_view(df) → list of ViewColumn dicts from a pandas DataFrame
  - detect_confusion_matrix_shape(cols, df) → bool

Suggest but never mandate; warn but never block.

sr: SR-FIG-REC
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

# A ViewColumn descriptor dict: {name, role, dtype, cardinality}
#   role:        "measure" | "dimension"
#   dtype:       "quantitative" | "ordinal" | "nominal" | "temporal"
#   cardinality: int (nunique)
ViewColumn = dict[str, Any]

# A ranked plot-type suggestion
Suggestion = dict[str, Any]

# Supported task archetypes (Abela/Munzner)
TASK_ARCHETYPES = frozenset({
    "comparison", "relationship", "distribution",
    "composition", "trend", "lookup", "deviation",
})

# Rainbow/non-perceptually-uniform colormaps that trigger the WARN
_RAINBOW_CMAPS = frozenset({
    "jet", "rainbow", "hsv", "gist_rainbow", "nipy_spectral",
    "gist_ncar", "spectral", "Paired",
})


# ---------------------------------------------------------------------------
# Descriptor inference from a pandas DataFrame
# ---------------------------------------------------------------------------

def infer_view(
    df: Any,
    role_overrides: "dict[str, str] | None" = None,
) -> list[ViewColumn]:
    """Infer ViewColumn descriptors from a pandas DataFrame.

    Each column gets:
      - name:        column name
      - role:        "measure" (high-cardinality numeric) | "dimension" (categorical/low-card)
      - dtype:       "quantitative" | "ordinal" | "nominal" | "temporal"
      - cardinality: nunique()

    Role heuristic (in priority order):
      1. Dense integer run (span/card <= 1.5): identifier/enumeration → dimension/ordinal.
         Catches model_id=[1..5], seed=[41..45], epoch=[0..N], integer-coded CM labels.
         Fires BEFORE the measure-promotion branch to avoid the ID-misfire bug.
      2. quantitative with card > 10 or uniqueness fraction > 0.5 → measure.
      3. Everything else → dimension (nominal/ordinal/temporal).

    role_overrides: {col_name: "measure"|"dimension"} — applied LAST, unconditionally.
      When an override changes the inferred role, prints to stdout:
        "role override: <col> → <new> (was inferred <old>)"
      When override forces role=measure on an ordinal col, dtype snaps to quantitative.
      Validated to {"measure","dimension"} at the CLI boundary before being passed here.
    """
    cols: list[ViewColumn] = []
    for col in df.columns:
        series = df[col]
        card = int(series.nunique())

        # --- dtype inference ---
        # Try pandas type checks; fall back to duck-typing when pandas absent.
        is_integer = False  # used by dense-int-sequence check below
        try:
            import pandas as pd
            if pd.api.types.is_datetime64_any_dtype(series):
                dtype = "temporal"
            elif pd.api.types.is_numeric_dtype(series):
                dtype = "quantitative"
                is_integer = pd.api.types.is_integer_dtype(series)
            elif hasattr(series, "cat"):  # CategoricalDtype
                dtype = "ordinal"
            else:
                dtype = "nominal"
        except ImportError:
            # Fallback without pandas type checks — is_integer stays False (guard is a no-op)
            try:
                float(series.iloc[0])
                dtype = "quantitative"
            except (ValueError, TypeError):
                dtype = "nominal"

        # --- role heuristic ---
        # A quantitative column is a "measure" when:
        #   (a) absolute cardinality > 10 (many distinct values — clearly continuous), OR
        #   (b) uniqueness fraction > 0.5 (>50% of rows are distinct — e.g. 9-row CM count col).
        # BUT: dense integer run (span/card <= 1.5) → identifier/enumeration → dimension/ordinal.
        #   Fires FIRST to avoid the ID-misfire bug where model_id=[1..5] on 5 rows
        #   triggers card_fraction=1.0 > 0.5 → measure (wrong — it's a categorical key).
        #   The same fix reclassifies integer-coded CM labels ([0,1,2]) from 'quantitative'
        #   to 'ordinal', making them visible to detect_confusion_matrix_shape.
        nrows = max(len(df), 1)
        card_fraction = card / nrows

        is_dense_int_sequence = (
            is_integer and card > 1
            and (int(series.max()) - int(series.min()) + 1) <= card * 1.5
        )

        if dtype == "quantitative" and is_dense_int_sequence:
            # Dense integer run: index / seed / code / class-label → ordered key
            role, dtype = "dimension", "ordinal"
        elif dtype == "quantitative" and (card > 10 or card_fraction > 0.5):
            role = "measure"
        elif dtype == "temporal":
            role = "dimension"
        else:
            role = "dimension"
            # Any remaining quantitative integer col (card<=10, fraction<=0.5, not dense-int)
            # is also an ordered categorical → ordinal.
            if is_integer and dtype == "quantitative":
                dtype = "ordinal"

        # --- role overrides (escape valve — applied LAST, unconditionally) ---
        if role_overrides and col in role_overrides:
            new_role = role_overrides[col]
            if new_role != role:
                print(f"role override: {col} → {new_role} (was inferred {role})")
            role = new_role
            # Forcing to measure a col inferred as ordinal: snap dtype to quantitative
            # so detect_confusion_matrix_shape and infer_task treat it as a measure.
            if role == "measure" and dtype == "ordinal":
                dtype = "quantitative"

        cols.append({
            "name": col,
            "role": role,
            "dtype": dtype,
            "cardinality": card,
        })
    return cols


def detect_confusion_matrix_shape(
    cols: list[ViewColumn],
    df: Any,  # pandas DataFrame
) -> bool:
    """Return True if the frame has the confusion-matrix shape.

    Confusion-matrix shape: two nominal/dimension columns with the same
    cardinality AND the same value-set (both axes label the same classes),
    plus at least one quantitative measure column.
    """
    dims = [c for c in cols if c["role"] == "dimension" and c["dtype"] in ("nominal", "ordinal")]
    measures = [c for c in cols if c["role"] == "measure"]

    if len(dims) < 2 or not measures:
        return False

    # Check every pair of dim columns for same-label-set
    for i in range(len(dims)):
        for j in range(i + 1, len(dims)):
            col_i = dims[i]["name"]
            col_j = dims[j]["name"]
            if dims[i]["cardinality"] != dims[j]["cardinality"]:
                continue
            # Same value-set?
            vals_i = set(df[col_i].dropna().unique())
            vals_j = set(df[col_j].dropna().unique())
            if vals_i == vals_j and len(vals_i) > 1:
                return True
    return False


# ---------------------------------------------------------------------------
# Task inference
# ---------------------------------------------------------------------------

def infer_task(cols: list[ViewColumn]) -> tuple[str, list[str]]:
    """Infer the most likely reader task from the descriptor shape.

    Returns (primary_task, [plausible_alternatives]).

    Heuristics (in priority order):
      - 1 temporal dim + any measure       → trend
      - 2+ measures + 0 dims              → relationship
      - 2+ measures + some dims           → relationship (alt: comparison)
      - 1 nominal dim + 1 measure          → comparison (alt: composition)
      - 1 dim + 0 measures (categorical)  → lookup
      - 1 measure only                    → distribution
      - 2 nominal dims + 1 measure        → comparison (matrix / heatmap)
      - fallback                          → comparison
    """
    measures = [c for c in cols if c["role"] == "measure"]
    dims = [c for c in cols if c["role"] == "dimension"]
    temporal_dims = [c for c in dims if c["dtype"] == "temporal"]
    nominal_dims = [c for c in dims if c["dtype"] in ("nominal", "ordinal")]

    n_measures = len(measures)
    n_dims = len(dims)
    n_temporal = len(temporal_dims)
    n_nominal = len(nominal_dims)

    if n_temporal >= 1 and n_measures >= 1:
        return "trend", ["relationship", "comparison"]

    if n_measures >= 2 and n_dims == 0:
        return "relationship", ["distribution"]

    if n_measures >= 2 and n_dims >= 1:
        return "relationship", ["comparison"]

    if n_nominal >= 2 and n_measures >= 1:
        return "comparison", ["lookup"]

    if n_nominal == 1 and n_measures == 1:
        return "comparison", ["composition", "distribution"]

    if n_measures == 1 and n_dims == 0:
        return "distribution", ["trend"]

    if n_dims >= 1 and n_measures == 0:
        return "lookup", []

    return "comparison", ["distribution"]


# ---------------------------------------------------------------------------
# Colormap class inference
# ---------------------------------------------------------------------------

def _colormap_class_for(plot_type: str, cols: list[ViewColumn], task: str) -> str | None:
    """Return the colormap CLASS (correctness call) for the given plot type and data.

    sequential   — ordered measure with no meaningful midpoint (heatmap of scores)
    diverging    — measure with a meaningful midpoint / signed data (e.g. residuals)
    qualitative  — nominal/unordered grouping variable
    None         — no color encoding needed
    """
    dims = [c for c in cols if c["role"] == "dimension"]
    measures = [c for c in cols if c["role"] == "measure"]
    nominal_dims = [c for c in dims if c["dtype"] in ("nominal", "ordinal")]

    if plot_type in ("line", "scatter"):
        # Color often used for grouping — qualitative if there's a nominal dim
        if nominal_dims:
            return "qualitative"
        return None

    if plot_type == "bar":
        # Color for grouping dimension — qualitative
        if nominal_dims:
            return "qualitative"
        return None

    if plot_type in ("heatmap", "confusion_matrix"):
        # The cell fill is a quantitative measure — sequential by default
        return "sequential"

    if plot_type in ("box", "violin", "histogram"):
        if nominal_dims:
            return "qualitative"
        return "sequential"

    if plot_type == "pie":
        return "qualitative"

    if plot_type in ("stacked_bar", "donut", "treemap"):
        return "qualitative"

    return None


# ---------------------------------------------------------------------------
# Static ranking rule table
# ---------------------------------------------------------------------------

def _make_suggestion(
    rank: int,
    plot_type: str,
    principle: str,
    colormap_class: str | None = None,
) -> Suggestion:
    return {
        "rank": rank,
        "plot_type": plot_type,
        "principle": principle,
        "colormap_class": colormap_class,
    }


def _rank_comparison(cols: list[ViewColumn]) -> list[Suggestion]:
    """Rankings for task=comparison.

    Two sub-cases:
      A) 1 dim + 1 measure          → bar (position on common scale, #1 on ladder)
      B) 2+ nominal dims + 1 measure → heatmap (matrix-of-scores pattern)
    """
    dims = [c for c in cols if c["role"] == "dimension"]
    measures = [c for c in cols if c["role"] == "measure"]
    nominal_dims = [c for c in dims if c["dtype"] in ("nominal", "ordinal")]

    # Matrix-of-scores: 2+ nominal dims + 1+ measures
    if len(nominal_dims) >= 2 and len(measures) >= 1:
        cc_heat = _colormap_class_for("heatmap", cols, "comparison")
        cc_bar = _colormap_class_for("bar", cols, "comparison")
        return [
            _make_suggestion(1, "heatmap",
                "Matrix-of-scores shape: position+fill — heatmap encodes the cell "
                "value with color (Mackinlay: most expressive for 2-nominal × 1-measure). "
                "Sequential colormap so relative magnitudes are readable "
                "(Cleveland–McGill: position rows/columns + color fill).",
                cc_heat),
            _make_suggestion(2, "bar",
                "Grouped bar: position on a common scale "
                "(Cleveland–McGill: #1 for magnitude comparison). "
                "Requires faceting or color grouping when 2 nominal dims.",
                cc_bar),
            _make_suggestion(3, "[AVOID] pie",
                "[AVOID] Pie: angle/area encoding — near bottom of Cleveland–McGill ladder. "
                "Angle judgments collapse beyond ≤3 slices; unreadable for matrix comparison.",
                "qualitative"),
        ]

    # Single dim: bar is the canonical comparison chart
    cc_bar = _colormap_class_for("bar", cols, "comparison")
    return [
        _make_suggestion(1, "bar",
            "Position on a common scale — the top of the Cleveland–McGill perceptual-accuracy "
            "ladder for comparison tasks. Bar encodes each category's magnitude on a shared "
            "baseline, making differences directly readable. "
            "Mackinlay: first in expressiveness for ordinal comparison of nominal dim + measure.",
            cc_bar),
        _make_suggestion(2, "dot_plot",
            "Dot plot: same position-on-scale encoding as bar, less ink, cleaner at high "
            "cardinality (Cleveland–McGill: position accurate; fewer visual fills).",
            cc_bar),
        _make_suggestion(3, "[AVOID] pie",
            "[AVOID] Pie: angle/area encoding — near the bottom of the Cleveland–McGill ladder. "
            "Angle judgments are inaccurate beyond ≤3 slices; misleads for comparison tasks. "
            "Mackinlay: pie expressiveness is lower-bounded by the number of slices.",
            "qualitative"),
    ]


def _rank_trend(cols: list[ViewColumn]) -> list[Suggestion]:
    """Rankings for task=trend (ordered/continuous x-axis)."""
    cc = _colormap_class_for("line", cols, "trend")
    return [
        _make_suggestion(1, "line",
            "Ordered x-axis → line chart: connects positions in sequence, "
            "showing trajectory (Cleveland–McGill: position on common scale + "
            "path connects ordered points). Mackinlay: the temporal dimension's "
            "most expressive encoding is an ordered spatial axis.",
            cc),
        _make_suggestion(2, "area",
            "Area chart: line + filled area underneath — same position encoding, "
            "adds visual weight for cumulative or magnitude emphasis. "
            "Cleveland–McGill: area encoding is lower-ranked than position alone, "
            "so use only when the cumulation metaphor matters.",
            cc),
        _make_suggestion(3, "[AVOID] bar",
            "[AVOID] Bar on ordered axis: treats the x-axis as discrete categories, "
            "losing the ordered-scale reading. Misleads for continuous trend. "
            "Use only when the x-axis is truly discrete (e.g. years as fixed points).",
            cc),
    ]


def _rank_relationship(cols: list[ViewColumn]) -> list[Suggestion]:
    """Rankings for task=relationship (2 continuous measures)."""
    measures = [c for c in cols if c["role"] == "measure"]
    cc = _colormap_class_for("scatter", cols, "relationship")
    if len(measures) >= 2:
        return [
            _make_suggestion(1, "scatter",
                "2 continuous → scatter: both measures encoded on position axes "
                "(Cleveland–McGill: top of ladder for bivariate relationship). "
                "Mackinlay: scatterplot is the most expressive encoding for a pair "
                "of quantitative variables.",
                cc),
            _make_suggestion(2, "line",
                "Line/connected scatter: adds trajectory if there is a natural "
                "ordering to the observations (time, iteration). "
                "Position encoding preserved; best when order matters.",
                cc),
            _make_suggestion(3, "[AVOID] bar",
                "[AVOID] Bar: collapses one continuous variable to discrete bins, "
                "losing the bivariate structure. Use scatter or hexbin for 2 continuous.",
                cc),
        ]
    # 1 measure + 1 dim (fallthrough)
    return _rank_comparison(cols)


def _rank_distribution(cols: list[ViewColumn]) -> list[Suggestion]:
    """Rankings for task=distribution (single quantitative measure)."""
    measures = [c for c in cols if c["role"] == "measure"]
    dims = [c for c in cols if c["role"] == "dimension"]
    nominal_dims = [c for c in dims if c["dtype"] in ("nominal", "ordinal")]

    # With a grouping dimension: compare distributions
    if nominal_dims and measures:
        cc = _colormap_class_for("box", cols, "distribution")
        return [
            _make_suggestion(1, "box",
                "Box-and-whisker: shows median, IQR, and outliers across groups "
                "(Cleveland–McGill: position encoding for the 5-number summary). "
                "Mackinlay: box is the most expressive compact distribution summary.",
                cc),
            _make_suggestion(2, "violin",
                "Violin: extends box to show full density shape — more expressive "
                "for multimodal distributions, higher ink cost. "
                "Position + area encoding (Cleveland–McGill: position dominant).",
                cc),
            _make_suggestion(3, "strip_plot",
                "Strip/jitter plot: shows all individual points — best when n is "
                "small enough to see individual observations without overplotting.",
                cc),
        ]

    # Single measure, no grouping: histogram
    if measures:
        return [
            _make_suggestion(1, "histogram",
                "Histogram: position + height encodes frequency — the canonical "
                "single-variable distribution chart (Cleveland–McGill: position on "
                "common scale for each bin). Mackinlay: length encoding for frequency.",
                None),
            _make_suggestion(2, "box",
                "Box: compact 5-number summary — best when distribution shape "
                "is known or a density estimate is not needed.",
                None),
            _make_suggestion(3, "violin",
                "Violin: full kernel density estimate — best when shape and "
                "multi-modality matter more than the 5-number summary.",
                None),
        ]

    return _rank_comparison(cols)


def _rank_composition(cols: list[ViewColumn]) -> list[Suggestion]:
    """Rankings for task=composition (part-to-whole)."""
    dims = [c for c in cols if c["role"] == "dimension"]
    nominal_dims = [c for c in dims if c["dtype"] in ("nominal", "ordinal")]
    # Cardinality of the primary grouping dimension
    card = nominal_dims[0]["cardinality"] if nominal_dims else 10

    cc = "qualitative"
    suggestions = [
        _make_suggestion(1, "stacked_bar",
            "Stacked bar: parts as stacked segments — the top + bottom of the stack "
            "sit on a common baseline (readable); middle segments lack a baseline "
            "(less accurate for >2 segments). Cleveland–McGill: only bottom + top on scale. "
            "Mackinlay: expressive for 2–3 segments; loses effectiveness above that.",
            cc),
    ]

    # Pie: only honest ≤3 slices
    if card <= 3:
        suggestions.append(_make_suggestion(2, "pie",
            "Pie chart: honest at ≤3 slices (angle judgments are accurate for coarse "
            "comparisons). Cleveland–McGill: area/angle encoding — low accuracy for fine "
            "differences. Mackinlay: acceptable when the part-to-whole metaphor is the "
            "primary reading (not comparison of parts).",
            cc))
    else:
        suggestions.append(_make_suggestion(2, "treemap",
            "Treemap: area encoding for many parts — more scalable than pie (no angle "
            "clutter). Cleveland–McGill: area encoding lower than position, but necessary "
            "for high-cardinality composition.",
            cc))
        suggestions.append(_make_suggestion(3, "[AVOID] pie",
            f"[AVOID] Pie at {card} categories: angle judgment collapses beyond ≤3 slices. "
            "Cleveland–McGill: area/angle is near the bottom of the accuracy ladder. "
            "Use stacked bar or treemap instead.",
            cc))

    return suggestions


def _rank_lookup(
    cols: list[ViewColumn],
    is_confusion_matrix: bool = False,
) -> list[Suggestion]:
    """Rankings for task=lookup (find a specific value by key)."""
    if is_confusion_matrix:
        return [
            _make_suggestion(1, "confusion_matrix",
                "Confusion matrix heatmap: annotated cell values with color intensity — "
                "optimal for 2-nominal × 1-quantitative where both axes share the same labels. "
                "Position (row/col) encodes the label pair; fill encodes the count/rate. "
                "Cleveland–McGill: position + annotated value is the most accurate lookup encoding.",
                "sequential"),
            _make_suggestion(2, "heatmap",
                "Heatmap (unnormalized): same matrix encoding without confusion-matrix styling. "
                "Use when the 'correct diagonal' metaphor is not meaningful.",
                "sequential"),
        ]

    dims = [c for c in cols if c["role"] == "dimension"]
    nominal_dims = [c for c in dims if c["dtype"] in ("nominal", "ordinal")]

    if len(nominal_dims) >= 2:
        return [
            _make_suggestion(1, "heatmap",
                "2-nominal × 1-measure lookup: heatmap with annotated values. "
                "Position (row/column) encodes the key pair; fill encodes the value. "
                "Cleveland–McGill: position is the dominant encoding for locating cells.",
                "sequential"),
            _make_suggestion(2, "table",
                "Table: exact lookup — zero encoding loss, no Cleveland–McGill "
                "degradation. Use when readers need exact values over trends.",
                None),
        ]

    return [
        _make_suggestion(1, "table",
            "Single-key lookup: a table is the most accurate encoding — "
            "no encoding of the value means no Cleveland–McGill degradation. "
            "Mackinlay: for lookup tasks, explicit text annotation outperforms any chart.",
            None),
        _make_suggestion(2, "bar",
            "Bar with labels: position encoding + annotated values — "
            "combines visual pattern (which is tallest?) with exact lookup.",
            "qualitative"),
    ]


def _rank_deviation(cols: list[ViewColumn]) -> list[Suggestion]:
    """Rankings for task=deviation (signed values around a meaningful midpoint)."""
    cc_div = "diverging"
    cc_bar = _colormap_class_for("bar", cols, "deviation")
    return [
        _make_suggestion(1, "bar",
            "Diverging bar: positive values extend right/up, negative left/down — "
            "the zero baseline is the meaningful midpoint. "
            "Cleveland–McGill: position on a common scale (the midpoint) is accurate. "
            "Use a diverging colormap for the fill to reinforce sign.",
            cc_div),
        _make_suggestion(2, "heatmap",
            "Diverging heatmap: for a matrix of signed values around a midpoint "
            "(e.g. residual correlation matrix). Diverging colormap maps ± to color. "
            "Cleveland–McGill: fill alone has lower accuracy than position; annotate values.",
            cc_div),
        _make_suggestion(3, "dot_plot",
            "Dot plot with reference line: dots on a common scale, zero reference line "
            "explicit. Position encoding preserved; cleaner than bar for sparse data.",
            cc_bar),
    ]


# ---------------------------------------------------------------------------
# Main recommend() function
# ---------------------------------------------------------------------------

def recommend(
    cols: list[ViewColumn],
    task: str | None = None,
    is_confusion_matrix: bool = False,
) -> list[Suggestion]:
    """Return ranked plot-type suggestions for the given view descriptor + task.

    Args:
      cols:                ViewColumn list (from infer_view() or hand-built)
      task:                One of TASK_ARCHETYPES, or None to infer-and-surface.
      is_confusion_matrix: Hint that the shape is a confusion matrix (from
                           detect_confusion_matrix_shape()). Overrides lookup ranking.

    Returns:
      List of Suggestion dicts, ranked best-first (rank=1 is best).

    Side-effects:
      When task is None (inferred), prints "task inferred: <t> (pass --task to
      override; also plausible: <alternates>)" to stdout — the §5M/charter-§2
      surface-don't-bury rule.
    """
    if task is None:
        inferred_task, alternates = infer_task(cols)
        alt_str = ", ".join(alternates) if alternates else "none"
        print(
            f"task inferred: {inferred_task} "
            f"(pass --task to override; also plausible: {alt_str})"
        )
        task = inferred_task

    if task not in TASK_ARCHETYPES:
        # Unknown task — fall back to comparison with a note
        print(f"warning: unknown task {task!r}; falling back to 'comparison'")
        task = "comparison"

    if task == "comparison":
        return _rank_comparison(cols)
    elif task == "trend":
        return _rank_trend(cols)
    elif task == "relationship":
        return _rank_relationship(cols)
    elif task == "distribution":
        return _rank_distribution(cols)
    elif task == "composition":
        return _rank_composition(cols)
    elif task == "lookup":
        return _rank_lookup(cols, is_confusion_matrix=is_confusion_matrix)
    elif task == "deviation":
        return _rank_deviation(cols)

    # Fallback (should not happen)
    return _rank_comparison(cols)


# ---------------------------------------------------------------------------
# Integrity WARN checks
# ---------------------------------------------------------------------------

def integrity_warns(
    plot_type: str,
    task: str,
    *,
    ymin: float | None = None,
    ymax: float | None = None,
    n_stacked_segments: int | None = None,
    n_slices: int | None = None,
    colormap: str | None = None,
    colormap_class: str | None = None,
    aggregation: str | None = None,
    has_raw_observations: bool = False,
) -> list[str]:
    """Return a list of WARN strings for the given plot parameters.

    Never raises. Never returns a truthy non-list. A non-empty list means
    at least one integrity condition fired — but these are advisory only
    (the researcher may proceed; they are never a block).

    Checks (§5E.13.3):
      1. Truncated bar baseline (ymin != 0)
      2. >2 floating stacked segments under task=comparison
      3. Pie >3 slices (angle judgment collapses)
      4. Rainbow/non-perceptually-uniform colormap (jet, rainbow, hsv, ...)
      5. Diverging colormap on sequential data (colormap_class="sequential" + diverging cmap)
      6. Bar-of-means when raw observations are available
    """
    warns: list[str] = []

    # 1. Truncated bar baseline
    if plot_type in ("bar", "bar_chart") and ymin is not None and ymin != 0:
        warns.append(
            f"⚠ truncated baseline (ymin={ymin}) — differences visually exaggerated "
            "by the non-zero baseline; set ymin=0 or annotate the truncation. "
            "(Cleveland–McGill: position accuracy requires a common zero baseline.)"
        )

    # 2. >2 floating stacked segments under comparison
    if (
        plot_type in ("stacked_bar",)
        and task == "comparison"
        and n_stacked_segments is not None
        and n_stacked_segments > 2
    ):
        warns.append(
            f"⚠ {n_stacked_segments} stacked segments under task=comparison — "
            "only the bottom and top of a stack share a common baseline; "
            "middle segments are unanchored and hard to compare accurately. "
            "(Cleveland–McGill: only anchored segments have position accuracy.)"
        )

    # 3. Pie >3 slices
    if plot_type == "pie" and n_slices is not None and n_slices > 3:
        warns.append(
            f"⚠ pie chart with {n_slices} slices — angle judgment collapses beyond "
            "≤3 slices; readers cannot accurately compare segments. "
            "Consider a bar chart (position encoding) or treemap (area for many parts). "
            "(Cleveland–McGill: angle/area is near the bottom of the accuracy ladder.)"
        )

    # 4. Rainbow/non-perceptually-uniform colormap
    if colormap is not None:
        cmap_lower = colormap.lower()
        if any(rc.lower() in cmap_lower or cmap_lower in rc.lower()
               for rc in _RAINBOW_CMAPS):
            warns.append(
                f"⚠ non-perceptually-uniform colormap {colormap!r} — rainbow/jet colormaps "
                "introduce false gradients and are inaccessible to colorblind readers. "
                "Replace with a perceptually-uniform sequential colormap "
                "(e.g. viridis, plasma, cividis) or a diverging one for signed data. "
                "(Cleveland–McGill: color saturation is already a low-accuracy encoding; "
                "non-uniform maps amplify the problem.)"
            )

    # 5. Diverging colormap on sequential data (mismatch)
    if colormap is not None and colormap_class == "sequential":
        _DIVERGING_CMAPS = {
            "rdbu", "bwr", "seismic", "coolwarm", "piyg", "prgn",
            "rdylbu", "rdylgn", "rdgy",
        }
        cmap_lower = colormap.lower()
        if any(dc in cmap_lower for dc in _DIVERGING_CMAPS):
            warns.append(
                f"⚠ diverging colormap {colormap!r} applied to sequential data — "
                "a diverging colormap implies a meaningful midpoint (e.g. zero, neutral), "
                "but the data has no such midpoint. Readers will see a false center. "
                "Use a sequential colormap instead (e.g. viridis, Blues). "
                "(Mackinlay: the encoding must be expressive — diverging implies "
                "a structure the sequential data does not have.)"
            )

    # 6. Bar-of-means over raw observations
    if (
        plot_type in ("bar", "bar_chart")
        and aggregation == "mean"
        and has_raw_observations
    ):
        warns.append(
            "⚠ bar of means over raw observations — the mean bar hides the distribution "
            "(outliers, skew, multi-modality, sample size). "
            "Consider overlaying a dot plot, box, or violin plot to show the full distribution. "
            "(Cleveland–McGill: a single bar at the mean suppresses 90% of the information "
            "in the raw data.)"
        )

    return warns


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def format_recommendations(
    suggestions: list[Suggestion],
    *,
    inferred_task: str | None = None,
    alternates: list[str] | None = None,
) -> str:
    """Format the ranked suggestions as a human-readable string for print/display."""
    lines: list[str] = []
    for s in suggestions:
        rank = s["rank"]
        pt = s["plot_type"]
        principle = s["principle"]
        cc = s.get("colormap_class")
        cc_str = f" [colormap: {cc}]" if cc else ""
        lines.append(f"  {rank}. {pt}{cc_str}")
        lines.append(f"     {principle}")
    return "\n".join(lines)
