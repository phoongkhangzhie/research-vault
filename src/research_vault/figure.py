"""figure.py — `rv figure` verb: experiment-results → publication-quality figures.

When to use: When you have an experiment note (experiments/<id>.md) with attached
results (results_location populated by `rv wandb pull` or manually) and need a
publication-quality plot. Creates a figure-spec note (figures/<id>.md) with full
provenance — experiment OKF link, results content hash, extract/filter recipe,
style preset. Optionally references a shared `datasets/` benchmark for overlay.

Anti-pattern: do NOT hand-write a one-off matplotlib script and drop a PNG into a
finding — declare `rv figure new` against an `experiments/` note so the figure carries
experiment→results→filter→style provenance and afterok-able lineage.
Anti-pattern: do NOT pick a plot type by gut feel or habit — use
`rv figure recommend` to get a ranked, perceptually-accurate encoding recommendation.

Commands:
  rv figure <project> new <fig-id> --experiment <id> [--benchmark <id>] [options]
  rv figure <project> preview <fig-id>
  rv figure <project> render <fig-id>
  rv figure <project> list
  rv figure <project> recommend <view-csv>

Requires the optional [figures] extra for preview/render/recommend (data-frame ops):
  pip install research-vault[figures]
  (matplotlib>=3.8, seaborn>=0.13, pandas>=2.2)

The style seam: render calls `figures.style.apply_style(preset, skin=<project>)` before
plotting. The plumbing calls it; the designer replaces the stub with the real aesthetic.
The recommend seam: emits colormap_class (sequential/diverging/qualitative) — the
CORRECTNESS call; the designer picks the concrete palette within that class in apply_style.

figures are PROJECT-SCOPED: figures/<id>.md lives in project_notes_dir(project)/figures/.
This is deliberately NOT a shared root (only datasets/ gets the SR-8 shared treatment).

Stdlib only at import time — pandas/matplotlib are guarded behind the [figures] extra
and imported only in preview/render/recommend, never at module level.

sr: SR-FIG, SR-FIG-REC
"""
from __future__ import annotations

import argparse
import datetime
import re
import sys
from pathlib import Path
from typing import Any

from .config import Config, load_config

# ---------------------------------------------------------------------------
# Import guard for the [figures] optional extra
# ---------------------------------------------------------------------------

_FIGURES_EXTRA_MSG = (
    "rv figure: the figures capability needs the optional extra — "
    "install it with:\n"
    "  pip install research-vault[figures]\n"
    "  (installs matplotlib>=3.8, seaborn>=0.13, pandas>=2.2)"
)


def _check_figures_extra() -> int | None:
    """Check that the [figures] optional extra is installed.

    Returns None if all packages are present (OK to proceed).
    Returns 1 (exit code) if any required package is absent — prints friendly message.
    NEVER raises ImportError — always returns a printable error.
    """
    missing = []
    for pkg in ("pandas", "matplotlib"):
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(_FIGURES_EXTRA_MSG, file=sys.stderr)
        return 1
    return None


# ---------------------------------------------------------------------------
# Frontmatter helpers (stdlib only — reuse pattern from note.py)
# ---------------------------------------------------------------------------

def _today() -> str:
    return datetime.date.today().isoformat()


def _slugify(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")[:80] or "figure"


def _render_frontmatter(fields: dict[str, str]) -> str:
    lines = ["---"]
    for key, val in fields.items():
        lines.append(f"{key}: {val}")
    lines.append("---")
    return "\n".join(lines)


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Parse YAML frontmatter from a note. Returns field dict."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm_block = text[3:end].strip()
    fields: dict[str, str] = {}
    for line in fm_block.splitlines():
        m = re.match(r"^([\w_-]+):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val.startswith(("'", '"')) and val.endswith(val[0]):
                val = val[1:-1]
            fields[key] = val
    return fields


def _parse_experiment_note(project_notes_dir: Path, experiment_id: str) -> dict[str, str]:
    """Read the experiment note and return its frontmatter fields.

    Returns the field dict with results_location, results_hash, etc.
    Raises ValueError if the note doesn't exist or has no results_location.
    """
    note_path = project_notes_dir / "experiments" / f"{experiment_id}.md"
    if not note_path.exists():
        raise ValueError(
            f"experiment note not found: {note_path}\n"
            f"Create it first with: rv note <project> new experiments <title>\n"
            f"Then populate results with: rv wandb pull <run-id> --experiment {experiment_id}"
        )
    text = note_path.read_text(encoding="utf-8")
    return _parse_frontmatter(text)


# ---------------------------------------------------------------------------
# Figure notes directory
# ---------------------------------------------------------------------------

def _figures_dir(project: str, cfg: Config) -> Path:
    """Return the project-scoped figures directory (project_notes_dir/figures/)."""
    return cfg.project_notes_dir(project) / "figures"


def _figure_note_path(project: str, fig_id: str, cfg: Config) -> Path:
    """Return the path to a figure note (may not exist yet)."""
    return _figures_dir(project, cfg) / f"{fig_id}.md"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _build_role_overrides(
    dimensions: list[str] | None,
    measures: list[str] | None,
) -> "dict[str, str] | None":
    """Build a role_overrides dict from --dimension / --measure CLI flag lists.

    The CLI parser enforces the allowed values structurally (choices or flag names),
    so this is purely a merge helper.
    """
    overrides: dict[str, str] = {}
    if dimensions:
        for col in dimensions:
            overrides[col] = "dimension"
    if measures:
        for col in measures:
            overrides[col] = "measure"
    return overrides or None


def _auto_recommend_plot_type(
    exp_fields: dict[str, str],
    select: list[str] | None,
    filter_expr: str | None,
    task: str | None = None,
    role_overrides: "dict[str, str] | None" = None,
) -> str:
    """Try to auto-recommend a plot type from the experiment's results frame.

    Called when the user omits --type. Loads the frame (requires [figures] extra),
    infers the ViewColumn descriptors, calls recommend(), and prints the rationale.

    Returns the recommended plot type string.
    Falls back to "line" silently if the extra is not installed or if loading fails.
    """
    # Check that the extra is available (don't propagate ImportError)
    try:
        import pandas as pd
        from .figures.recommend import infer_view, recommend, format_recommendations, infer_task
    except ImportError:
        print(
            "rv figure new: [figures] extra not installed — defaulting to plot_type=line. "
            "Install with: pip install research-vault[figures] then rerun for a recommendation.",
            file=sys.stderr,
        )
        return "line"

    location = exp_fields.get("results_location", "").strip()
    if not location or not location.lower().endswith(".csv"):
        # Can't load the frame — no recommendation possible
        return "line"

    try:
        df = pd.read_csv(location)
    except Exception:
        return "line"

    # Apply column select if specified
    if select:
        cols_available = [c for c in select if c in df.columns]
        if cols_available:
            df = df[cols_available]

    # Apply filter if specified
    if filter_expr:
        try:
            df = df.query(filter_expr)
        except Exception:
            pass

    if df.empty or len(df.columns) == 0:
        return "line"

    cols = infer_view(df, role_overrides=role_overrides)
    suggestions = recommend(cols, task=task)
    top = suggestions[0]
    chosen = top["plot_type"]
    # Strip [AVOID] prefix if somehow top-ranked (shouldn't happen, but be safe)
    if chosen.startswith("[AVOID]"):
        chosen = "line"

    # Print the rationale (the recommend-not-mandate surface)
    principle = top.get("principle", "")
    cc = top.get("colormap_class")
    cc_str = f"; colormap: {cc}" if cc else ""
    print(
        f"type: {chosen} (auto — {top.get('principle', '')[:80].split('.')[0]}; "
        f"Cleveland–McGill/Mackinlay{cc_str}; pass --type to override)"
    )

    return chosen


def _fire_integrity_warns(
    plot_type: str,
    exp_fields: dict[str, str],
    select: list[str] | None,
    filter_expr: str | None,
) -> None:
    """Fire integrity WARNs for the chosen plot_type against the data frame.

    Loads the frame to determine n_slices / cardinality. Fires warns to stdout.
    Never raises, never blocks.
    """
    try:
        import pandas as pd
        from .figures.recommend import integrity_warns, infer_view
    except ImportError:
        return

    location = exp_fields.get("results_location", "").strip()
    if not location or not location.lower().endswith(".csv"):
        return

    try:
        df = pd.read_csv(location)
    except Exception:
        return

    if select:
        cols_available = [c for c in select if c in df.columns]
        if cols_available:
            df = df[cols_available]
    if filter_expr:
        try:
            df = df.query(filter_expr)
        except Exception:
            pass

    if df.empty:
        return

    cols = infer_view(df)
    dims = [c for c in cols if c["role"] == "dimension"]
    n_slices = dims[0]["cardinality"] if dims else None

    warns = integrity_warns(
        plot_type=plot_type,
        task="comparison",  # default task for integrity check
        n_slices=n_slices,
    )
    for w in warns:
        print(w)


def cmd_new(
    project: str,
    fig_id: str,
    *,
    experiment_id: str,
    benchmark_id: str | None = None,
    select: list[str] | None = None,
    filter_expr: str | None = None,
    plot_type: str | None = None,
    style: str = "publication",
    task: str | None = None,
    role_overrides: "dict[str, str] | None" = None,
    config: Config | None = None,
) -> Path:
    """Create a figure-spec note (figures/<fig-id>.md) in the project's notes directory.

    The figure-spec records the full provenance:
      - source_experiment: experiments/<experiment_id>  — OKF link to the experiment note
      - experiment_results_hash: sha256:<hex>           — results_hash from the experiment
      - benchmark_dataset: datasets/<benchmark_id>      — OPTIONAL comparison overlay
      - select: <col1,col2,...>                         — column subset (empty = all)
      - filter: <expr>                                  — pandas query expression
      - plot_type: <type>                               — line | scatter | bar | box | hist
                   When omitted (None), the recommender auto-picks from the data view and
                   prints the rationale (recommend-not-mandate: pass --type to override).
      - style: <preset>                                 — publication | slide | poster

    The PRIMARY frame source is the experiment note's results_location (the metrics
    artifact written by `rv wandb pull`). The optional benchmark_id references a shared
    datasets/ note for comparison overlay only — it is never the primary source.

    figures are PROJECT-SCOPED — the note lives at project_notes_dir(project)/figures/.

    Raises ValueError if the experiment note (experiments/<experiment_id>.md) does not
    exist in the project's notes directory — create and pull results first.

    Returns the path to the created figure note.
    """
    cfg = config or load_config()
    project_notes_dir = cfg.project_notes_dir(project)

    # Read the experiment note — the primary results source
    exp_fields = _parse_experiment_note(project_notes_dir, experiment_id)
    results_hash = exp_fields.get("results_hash", "").strip()

    # Resolve plot_type: None → call the recommender (SR-FIG-REC integration)
    explicit_type = plot_type is not None
    if not explicit_type:
        plot_type = _auto_recommend_plot_type(
            exp_fields, select, filter_expr, task, role_overrides=role_overrides
        )

    # Fire integrity WARNs regardless of who chose the type (explicit or auto)
    _fire_integrity_warns(plot_type, exp_fields, select, filter_expr)

    # Create the figures directory (project-scoped)
    figs_dir = _figures_dir(project, cfg)
    figs_dir.mkdir(parents=True, exist_ok=True)

    note_path = figs_dir / f"{fig_id}.md"
    if note_path.exists():
        # Avoid silent overwrite — append today's date like note.py
        note_path = figs_dir / f"{fig_id}-{_today()}.md"

    select_str = ",".join(select) if select else ""
    filter_str = filter_expr or ""

    fields: dict[str, str] = {
        "type": "figures",
        "title": fig_id,
        "created": _today(),
        "source_experiment": f"experiments/{experiment_id}",
        "experiment_results_hash": results_hash,
        "select": select_str,
        "filter": filter_str,
        "plot_type": plot_type,
        "style": style,
        "rendered": "false",
    }

    # Optional benchmark dataset (comparison overlay only, NOT the primary source)
    if benchmark_id:
        fields["benchmark_dataset"] = f"datasets/{benchmark_id}"

    body = (
        "\n"
        "<!-- Figures provenance note (SR-FIG, SR-FIG-REC) -->\n"
        "<!-- Primary source: the experiment's results_location (rv wandb pull output). -->\n"
        "<!-- This note POINTS to image files — it does NOT embed image bytes. -->\n"
        "<!-- Run: rv figure preview <fig-id>     to inspect the data frame. -->\n"
        "<!-- Run: rv figure render <fig-id>      to produce SVG+PNG images. -->\n"
        "<!-- Run: rv figure recommend <view-csv> to get plot-type suggestions. -->\n"
        "\n"
        "## What this figure shows\n\n"
        "<!-- Describe the plot: what it communicates, axes, key takeaway. -->\n\n"
        "## Render lineage\n\n"
        "<!-- Filled by `rv figure render` — rv version, render timestamp, image paths. -->\n"
    )

    note_path.write_text(_render_frontmatter(fields) + "\n" + body, encoding="utf-8")
    return note_path


# ---------------------------------------------------------------------------
# cmd_recommend — rv figure <project> recommend <view-csv>
# ---------------------------------------------------------------------------

def cmd_recommend(
    project: str,
    view_csv: str,
    *,
    task: str | None = None,
    why: bool = False,
    role_overrides: "dict[str, str] | None" = None,
    config: Config | None = None,
) -> int:
    """Print ranked plot-type suggestions for a data-view CSV artifact.

    Reads the view CSV (typically state/figures/<fig>-view.csv produced by
    `rv figure preview`), infers the ViewColumn descriptor, and calls the
    static ranking rule table grounded in:
      - Cleveland & McGill (1984) perceptual-accuracy ladder
      - Mackinlay (1986) expressiveness→effectiveness ordering

    Output:
      - Ranked suggestions (best-first) with plot_type + principle string
      - colormap_class emitted (sequential/diverging/qualitative) — NOT a palette
        (the designer picks the palette within the class via apply_style)
      - When --task omitted: prints "task inferred: <t> (pass --task to override)"

    Integrity WARNs fire for obvious pitfalls (non-blocking, advisory).

    Requires the [figures] optional extra (pandas).
    Returns 0 on success, 1 on error or missing extra.
    """
    rc = _check_figures_extra()
    if rc is not None:
        return rc

    import pandas as pd
    from .figures.recommend import (
        infer_view,
        recommend,
        detect_confusion_matrix_shape,
        format_recommendations,
        integrity_warns,
    )

    # Resolve the view CSV path
    view_path = Path(view_csv)
    if not view_path.is_absolute():
        # Try relative to state/figures/ as a convenience
        cfg = config or load_config()
        candidate = cfg.state_dir / "figures" / view_csv
        if candidate.exists():
            view_path = candidate

    if not view_path.exists():
        print(
            f"rv figure recommend: view file not found: {view_csv}\n"
            f"  Generate it first: rv figure {project} preview <fig-id>",
            file=sys.stderr,
        )
        return 1

    try:
        df = pd.read_csv(str(view_path))
    except Exception as e:
        print(f"rv figure recommend: error reading view CSV: {e}", file=sys.stderr)
        return 1

    if df.empty:
        print("rv figure recommend: view CSV is empty — cannot infer descriptor.", file=sys.stderr)
        return 1

    cols = infer_view(df, role_overrides=role_overrides)
    is_cm = detect_confusion_matrix_shape(cols, df)

    # Print descriptor summary
    print(f"\n=== rv figure recommend: {view_path.name} ===")
    print(f"Columns: {len(cols)}")
    for c in cols:
        print(f"  {c['name']}: role={c['role']} dtype={c['dtype']} cardinality={c['cardinality']}")
    if is_cm:
        print("  [hint: confusion-matrix shape detected (same label-set on both axes)]")
    print()

    suggestions = recommend(cols, task=task, is_confusion_matrix=is_cm)

    print("Ranked plot-type suggestions:")
    for s in suggestions:
        rank = s["rank"]
        pt = s["plot_type"]
        cc = s.get("colormap_class")
        cc_str = f"  colormap_class: {cc}" if cc else ""
        print(f"  {rank}. {pt}{cc_str}")
        if why:
            print(f"     {s['principle']}")

    if not why:
        print("\n  (pass --why to see the Cleveland–McGill / Mackinlay principle for each)")

    # colormap_class seam confirmation
    top = suggestions[0]
    print(f"\ncolormap_class (correctness): {top.get('colormap_class')!r}")
    print("  (the designer picks the concrete palette within this class via apply_style — "
          "not the recommender's job)")

    return 0


def cmd_preview(
    project: str,
    fig_id: str,
    *,
    config: Config | None = None,
) -> int:
    """Print the data frame head + write the -view artifact (CSV + markdown table).

    This is the human-go inspection surface (§5E.3): the operator eyeballs the
    exact frame that will feed the plot before approving the data-check DAG node.

    The frame is loaded from the experiment note's results_location — the metrics
    artifact written by `rv wandb pull` (or set manually).

    Prints to stdout:
      - Frame shape (rows × cols)
      - Column names + dtypes
      - Head rows (first 10)

    Writes:
      - state/figures/<fig-id>-view.csv   — full filtered/selected frame
      - state/figures/<fig-id>-view.md    — rendered markdown table (head only)

    Requires the [figures] optional extra (pandas).
    Returns 0 on success, 1 on error or missing extra.
    """
    rc = _check_figures_extra()
    if rc is not None:
        return rc

    import pandas as pd  # guarded — only here, never at module level

    cfg = config or load_config()
    note_path = _figure_note_path(project, fig_id, cfg)

    if not note_path.exists():
        print(
            f"rv figure preview: figure spec not found: {note_path}\n"
            f"  Create it first: rv figure {project} new {fig_id} --experiment <exp-id>",
            file=sys.stderr,
        )
        return 1

    fields = _parse_frontmatter(note_path.read_text(encoding="utf-8"))
    source_exp = fields.get("source_experiment", "").strip()
    if not source_exp:
        print(
            f"rv figure preview: figure spec missing 'source_experiment' field: {note_path}",
            file=sys.stderr,
        )
        return 1

    # Resolve the experiment note to get results_location
    experiment_id = source_exp.replace("experiments/", "").strip()
    project_notes_dir = cfg.project_notes_dir(project)
    try:
        exp_fields = _parse_experiment_note(project_notes_dir, experiment_id)
    except ValueError as e:
        print(f"rv figure preview: {e}", file=sys.stderr)
        return 1

    location = exp_fields.get("results_location", "").strip()
    if not location:
        print(
            f"rv figure preview: experiment note has no 'results_location' field.\n"
            f"  Run: rv wandb pull <run-id> --experiment {experiment_id} --project {project}\n"
            f"  Or fill results_location manually in: {project_notes_dir}/experiments/{experiment_id}.md",
            file=sys.stderr,
        )
        return 1

    # Load the data from the experiment results artifact
    try:
        if location.lower().endswith(".csv") or not location.startswith(("http", "doi:")):
            df = pd.read_csv(location)
        else:
            print(
                f"rv figure preview: only local CSV files supported in preview; "
                f"got results_location={location!r}",
                file=sys.stderr,
            )
            return 1
    except Exception as e:
        print(f"rv figure preview: error loading data from {location!r}: {e}", file=sys.stderr)
        return 1

    # Apply column select
    select_str = fields.get("select", "").strip()
    if select_str:
        cols = [c.strip() for c in select_str.split(",") if c.strip()]
        missing_cols = [c for c in cols if c not in df.columns]
        if missing_cols:
            print(
                f"rv figure preview: column(s) not in data: {missing_cols}\n"
                f"  Available: {list(df.columns)}",
                file=sys.stderr,
            )
            return 1
        df = df[cols]

    # Apply filter expression
    filter_str = fields.get("filter", "").strip()
    if filter_str:
        try:
            df = df.query(filter_str)
        except Exception as e:
            print(
                f"rv figure preview: filter expression error ({filter_str!r}): {e}",
                file=sys.stderr,
            )
            return 1

    # Print the frame info to stdout
    nrows, ncols = df.shape
    print(f"\n=== figure preview: {fig_id} ===")
    print(f"Shape: {nrows} rows × {ncols} cols")
    print(f"Source: experiments/{experiment_id} → {location}")
    print(f"Columns: {list(df.columns)}")
    print(f"\nDtypes:")
    for col, dtype in df.dtypes.items():
        print(f"  {col}: {dtype}")
    print(f"\nHead (first {min(10, nrows)} rows):")
    print(df.head(10).to_string(index=True))
    print()

    # Write the view artifacts
    state_figs_dir = cfg.state_dir / "figures"
    state_figs_dir.mkdir(parents=True, exist_ok=True)

    view_csv = state_figs_dir / f"{fig_id}-view.csv"
    view_md = state_figs_dir / f"{fig_id}-view.md"

    df.to_csv(view_csv, index=False)

    # Write markdown table (head only to keep the file human-readable)
    head_df = df.head(20)
    try:
        md_table = head_df.to_markdown(index=False)
    except ImportError:
        # tabulate not available — fall back to simple representation
        md_table = "```\n" + head_df.to_string(index=False) + "\n```"

    view_md.write_text(
        f"# Data view: {fig_id}\n\n"
        f"Source: `experiments/{experiment_id}` → `{location}`\n"
        f"Select: `{select_str or '(all columns)'}` · Filter: `{filter_str or '(none)'}`\n\n"
        f"## Frame head (first {min(20, nrows)} rows)\n\n"
        f"{md_table}\n",
        encoding="utf-8",
    )

    print(f"View artifacts written:")
    print(f"  {view_csv}")
    print(f"  {view_md}")

    return 0


def cmd_render(
    project: str,
    fig_id: str,
    *,
    title: str | None = None,
    config: Config | None = None,
) -> int:
    """Render the figure spec to SVG+PNG images.

    Reads the figure spec note, loads the experiment results, applies extract/filter,
    calls apply_style(preset, skin=project), and renders to:
      state/figures/<fig-id>.svg
      state/figures/<fig-id>.png

    Updates the figure spec note with render_timestamp + rv_version.

    FIGURE MINIMALISM (SR-FIG-MINIMAL, §5J.16.5): the raster is PLOT-ONLY. The
    internal fig_id is NEVER burned into the image, and no title/caption/provenance
    is baked into the PNG/SVG — descriptive text belongs in the LaTeX ``\\caption``
    and the lineage in the figures/<id> OKF note. A title is drawn ONLY when the
    operator explicitly opts in via ``title`` (intended for slide/poster decks); the
    ``publication`` preset stays plot-only by default. See the figure-minimalism
    doctrine (doctrine/figure-minimalism.md) for the caption-honesty rules.

    Requires the [figures] optional extra (matplotlib + seaborn + pandas).
    Returns 0 on success, 1 on error or missing extra.
    """
    rc = _check_figures_extra()
    if rc is not None:
        return rc

    import pandas as pd  # guarded
    import matplotlib  # noqa: F401 — guarded import
    import matplotlib.pyplot as plt

    cfg = config or load_config()
    note_path = _figure_note_path(project, fig_id, cfg)

    if not note_path.exists():
        print(
            f"rv figure render: figure spec not found: {note_path}",
            file=sys.stderr,
        )
        return 1

    fields = _parse_frontmatter(note_path.read_text(encoding="utf-8"))
    source_exp = fields.get("source_experiment", "").strip()
    if not source_exp:
        print(
            f"rv figure render: figure spec missing 'source_experiment': {note_path}",
            file=sys.stderr,
        )
        return 1

    experiment_id = source_exp.replace("experiments/", "").strip()
    project_notes_dir = cfg.project_notes_dir(project)
    try:
        exp_fields = _parse_experiment_note(project_notes_dir, experiment_id)
    except ValueError as e:
        print(f"rv figure render: {e}", file=sys.stderr)
        return 1

    location = exp_fields.get("results_location", "").strip()
    if not location:
        print(
            f"rv figure render: experiment note missing 'results_location': "
            f"{project_notes_dir}/experiments/{experiment_id}.md",
            file=sys.stderr,
        )
        return 1

    try:
        df = pd.read_csv(location)
    except Exception as e:
        print(f"rv figure render: error loading data from {location!r}: {e}", file=sys.stderr)
        return 1

    # Apply select/filter
    select_str = fields.get("select", "").strip()
    if select_str:
        cols = [c.strip() for c in select_str.split(",") if c.strip()]
        df = df[[c for c in cols if c in df.columns]]

    filter_str = fields.get("filter", "").strip()
    if filter_str:
        try:
            df = df.query(filter_str)
        except Exception as e:
            print(f"rv figure render: filter error ({filter_str!r}): {e}", file=sys.stderr)
            return 1

    # Apply style via the seam — the designer replaces this stub
    from .figures.style import apply_style
    preset = fields.get("style", "publication")
    apply_style(preset, project)

    # Basic render (designer's implementation will be richer — plot_type→seaborn mapping)
    plot_type = fields.get("plot_type", "line")
    fig, ax = plt.subplots()
    numeric_cols = df.select_dtypes("number").columns.tolist()
    if numeric_cols:
        df[numeric_cols].plot(
            ax=ax,
            kind="line" if plot_type not in ("bar", "scatter", "hist", "box") else plot_type,
        )
    # FIGURE MINIMALISM (SR-FIG-MINIMAL, §5J.16.5): plot-only raster.
    # The internal fig_id is NEVER baked as a title — that is provenance, and
    # provenance lives in the figures/<id> note, not in pixels. A title is drawn
    # ONLY on explicit --title opt-in (slide/poster decks); publication stays plot-only.
    if title:
        if preset == "publication":
            print(
                "rv figure render: WARN — --title on the `publication` preset. "
                "Publication figures are plot-only; descriptive text belongs in the "
                "LaTeX \\caption{…}, not the raster. Rendering the title as an explicit "
                "override (see doctrine/figure-minimalism.md).",
                file=sys.stderr,
            )
        ax.set_title(title)
    ax.set_xlabel("")

    # Write images
    state_figs_dir = cfg.state_dir / "figures"
    state_figs_dir.mkdir(parents=True, exist_ok=True)
    svg_path = state_figs_dir / f"{fig_id}.svg"
    png_path = state_figs_dir / f"{fig_id}.png"
    fig.savefig(str(svg_path), format="svg")
    fig.savefig(str(png_path), format="png")
    plt.close(fig)

    # Update figure note with render provenance
    from . import __version__ as rv_version
    render_ts = datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    _update_figure_note_rendered(note_path, render_ts, rv_version, svg_path, png_path)

    print(f"Rendered: {svg_path}")
    print(f"Rendered: {png_path}")
    print(f"Updated:  {note_path}")
    return 0


def _update_figure_note_rendered(
    note_path: Path,
    render_ts: str,
    rv_version: str,
    svg_path: Path,
    png_path: Path,
) -> None:
    """Update the figure note frontmatter with render provenance (in-place)."""
    text = note_path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return
    end = text.find("\n---", 3)
    if end == -1:
        return
    fm_block = text[3:end]
    body = text[end + 4:]

    # Update rendered / add render fields
    lines = fm_block.splitlines()
    new_lines = []
    for line in lines:
        if line.startswith("rendered:"):
            new_lines.append("rendered: true")
        else:
            new_lines.append(line)

    # Append render provenance if not already present
    fm_str = "\n".join(new_lines)
    if "render_timestamp" not in fm_str:
        new_lines.append(f"render_timestamp: {render_ts}")
    if "rv_version" not in fm_str:
        new_lines.append(f"rv_version: {rv_version}")
    if "svg_path" not in fm_str:
        new_lines.append(f"svg_path: {svg_path}")
    if "png_path" not in fm_str:
        new_lines.append(f"png_path: {png_path}")

    new_text = "---\n" + "\n".join(new_lines) + "\n---" + body
    note_path.write_text(new_text, encoding="utf-8")


def cmd_list(
    project: str,
    *,
    config: Config | None = None,
) -> list[dict[str, Any]]:
    """List figure spec notes for the given project.

    Returns list of {path, fields} dicts (project-scoped — only this project's figures).
    """
    cfg = config or load_config()
    figs_dir = _figures_dir(project, cfg)
    if not figs_dir.exists():
        return []

    results = []
    for p in sorted(figs_dir.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        fields = _parse_frontmatter(text)
        results.append({"path": p, "fields": fields})
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(parent: argparse._SubParsersAction | None = None) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Build the argument parser for the `figure` verb.

    When to use: When you have an experiment note (experiments/<id>.md) with attached
    results (results_location/results_hash from `rv wandb pull` or manual fill) and
    need a publication-quality plot with full experiment→results→filter→style provenance.
    Use `rv figure new` to declare the figure spec, `rv figure preview` to inspect the
    exact data frame, `rv figure render` to produce the images, and
    `rv figure recommend <view-csv>` to get ranked plot-type suggestions grounded in
    the Cleveland–McGill perceptual-accuracy ladder + Mackinlay expressiveness→effectiveness.

    Anti-pattern: do NOT hand-write a one-off matplotlib script and drop a PNG into a
    finding — declare `rv figure new` against an `experiments/` note so the figure carries
    experiment→results→filter→style provenance and afterok-able lineage. One-off scripts
    drop the reproducibility chain; rv figure new builds it structurally.
    Anti-pattern: do NOT pick a plot type by gut feel or habit (eyeballing a chart type
    skips the perceptual encoding-accuracy check) — use `rv figure recommend` to get a
    recommendation for your data's structure and task.

    sr: SR-FIG, SR-FIG-REC
    """
    desc = (
        "Create, preview, render, list, and recommend plot types for publication-quality figures.\n"
        "Figures carry full provenance: experiment OKF link + results hash + filter recipe + style.\n"
        "Primary source: experiments/<id> results_location (populated by rv wandb pull).\n"
        "Optional --benchmark: shared datasets/<id> for comparison overlay only.\n"
        "Requires pip install research-vault[figures] for preview/render/recommend (pandas/matplotlib).\n"
        "recommend: grounded in Cleveland–McGill accuracy ladder + Mackinlay expressiveness ordering."
    )
    if parent is not None:
        p = parent.add_parser("figure", help="Experiment-results → publication figure.", description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv figure", description=desc)

    p.add_argument("project", help="Project slug.")

    sub = p.add_subparsers(dest="figure_cmd", required=True)

    # new
    new_p = sub.add_parser(
        "new",
        help=(
            "Create a figure-spec note (figures/<id>.md) sourced from experiment results. "
            "When --type is omitted, the recommender auto-picks the perceptually-accurate "
            "encoding and prints the rationale (recommend-not-mandate; pass --type to override)."
        ),
    )
    new_p.add_argument("fig_id", metavar="fig-id", help="Figure identifier (slug).")
    new_p.add_argument(
        "--experiment", dest="experiment_id", required=True, metavar="EXP-ID",
        help="ID of the experiment note (e.g. 'hfs-run-007'). PRIMARY source.",
    )
    new_p.add_argument(
        "--benchmark", dest="benchmark_id", default=None, metavar="DATASET-ID",
        help="OPTIONAL: ID of a shared datasets/ note for comparison overlay only.",
    )
    new_p.add_argument(
        "--select", nargs="+", default=None, metavar="COL",
        help="Column(s) to select from the results frame. Omit to use all columns.",
    )
    new_p.add_argument(
        "--filter", dest="filter_expr", default=None, metavar="EXPR",
        help="Pandas query expression to filter rows (e.g. 'lang == \"en\"').",
    )
    new_p.add_argument(
        "--type", dest="plot_type", default=None,
        metavar="PLOT-TYPE",
        help=(
            "Plot type: bar, line, scatter, box, violin, histogram, heatmap, pie, etc. "
            "When OMITTED, the recommender auto-picks from the data view and prints the "
            "rationale (Cleveland–McGill ladder; pass --type to override silently)."
        ),
    )
    new_p.add_argument(
        "--task", dest="task", default=None,
        metavar="TASK",
        choices=["comparison", "relationship", "distribution", "composition",
                 "trend", "lookup", "deviation"],
        help=(
            "Reader task for the recommender: comparison|relationship|distribution|"
            "composition|trend|lookup|deviation. "
            "When omitted, the recommender infers from the data shape and announces it. "
            "Ignored when --type is supplied explicitly."
        ),
    )
    new_p.add_argument(
        "--style", dest="style", default="publication",
        choices=["publication", "slide", "poster"],
        help="Style preset (default: publication).",
    )
    new_p.add_argument(
        "--dimension", dest="dimension", action="append", default=None, metavar="COL",
        help=(
            "Force COL to role=dimension (may be repeated: --dimension col1 --dimension col2). "
            "Escape valve: overrides the recommender's inferred role, applied last. "
            "Useful when a column the recommender classifies as measure is actually a key."
        ),
    )
    new_p.add_argument(
        "--measure", dest="measure", action="append", default=None, metavar="COL",
        help=(
            "Force COL to role=measure (may be repeated). "
            "Escape valve: overrides the inferred role, applied last. "
            "Example: --measure count when count=[1,2,3,4] is dense-int but IS a real measure."
        ),
    )

    # preview
    preview_p = sub.add_parser(
        "preview",
        help=(
            "Print the exact data frame from experiment results + write state/figures/<id>-view.csv. "
            "The human-go data-check inspection surface (§5E.3). Requires [figures] extra."
        ),
    )
    preview_p.add_argument("fig_id", metavar="fig-id", help="Figure identifier (slug).")

    # render
    render_p = sub.add_parser(
        "render",
        help=(
            "Render the figure spec to a PLOT-ONLY SVG+PNG (figure minimalism: no baked "
            "title/caption/provenance — those live in the LaTeX \\caption + the figures/<id> "
            "note). Requires [figures] extra."
        ),
    )
    render_p.add_argument("fig_id", metavar="fig-id", help="Figure identifier (slug).")
    render_p.add_argument(
        "--title", dest="title", default=None, metavar="TEXT",
        help=(
            "OPT-IN in-raster title (intended for slide/poster decks). OMIT for the "
            "`publication` preset — publication figures are PLOT-ONLY; descriptive text "
            "belongs in the LaTeX \\caption, never burned into the image. The internal "
            "fig-id is never used as a title. A title must state WHAT IS PLOTTED, not the "
            "paper's claim (see doctrine/figure-minimalism.md)."
        ),
    )

    # list
    sub.add_parser("list", help="List figure specs for the project.")

    # recommend (SR-FIG-REC)
    rec_p = sub.add_parser(
        "recommend",
        help=(
            "Get ranked plot-type suggestions for a data-view CSV (from rv figure preview). "
            "Grounded in Cleveland–McGill perceptual-accuracy ladder + Mackinlay "
            "expressiveness→effectiveness. Emits colormap_class (correctness) — "
            "NOT a concrete palette (designer's job via apply_style). Requires [figures] extra."
        ),
    )
    rec_p.add_argument(
        "view_csv",
        metavar="view-csv",
        help=(
            "Path to the -view.csv artifact (from rv figure preview). "
            "Relative paths are tried as state/figures/<name> for convenience."
        ),
    )
    rec_p.add_argument(
        "--task", dest="task", default=None,
        choices=["comparison", "relationship", "distribution", "composition",
                 "trend", "lookup", "deviation"],
        help=(
            "Reader task: comparison|relationship|distribution|composition|"
            "trend|lookup|deviation. When omitted, inferred from the data shape "
            "and announced (never silent)."
        ),
    )
    rec_p.add_argument(
        "--why", dest="why", action="store_true", default=False,
        help=(
            "Print the full principle string for each suggestion "
            "(the Cleveland–McGill / Mackinlay rationale). "
            "Default: print only the ranked type names."
        ),
    )
    rec_p.add_argument(
        "--dimension", dest="dimension", action="append", default=None, metavar="COL",
        help=(
            "Force COL to role=dimension (may be repeated). "
            "Escape valve: overrides the inferred role, applied last. "
            "Useful when an integer column the recommender classifies as measure is actually a key."
        ),
    )
    rec_p.add_argument(
        "--measure", dest="measure", action="append", default=None, metavar="COL",
        help=(
            "Force COL to role=measure (may be repeated). "
            "Escape valve: overrides the inferred role, applied last. "
            "Example: --measure count when count=[1,2,3,4] is dense-int but IS a real measure "
            "(e.g. a 2x2 confusion matrix with small dense counts)."
        ),
    )

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch figure subcommands. Returns exit code."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv figure: config error: {e}", file=sys.stderr)
        return 1

    try:
        if args.figure_cmd == "new":
            role_overrides = _build_role_overrides(
                getattr(args, "dimension", None),
                getattr(args, "measure", None),
            )
            path = cmd_new(
                args.project,
                args.fig_id,
                experiment_id=args.experiment_id,
                benchmark_id=args.benchmark_id,
                select=args.select,
                filter_expr=args.filter_expr,
                plot_type=args.plot_type,  # None = use recommender
                task=args.task,
                style=args.style,
                role_overrides=role_overrides,
                config=cfg,
            )
            print(f"Created figure spec: {path}")
            return 0

        elif args.figure_cmd == "preview":
            return cmd_preview(args.project, args.fig_id, config=cfg)

        elif args.figure_cmd == "render":
            return cmd_render(
                args.project, args.fig_id,
                title=getattr(args, "title", None),
                config=cfg,
            )

        elif args.figure_cmd == "list":
            results = cmd_list(args.project, config=cfg)
            if not results:
                print(f"No figure specs for {args.project!r}.")
                return 0
            print(f"Figure specs for {args.project!r}:")
            for r in results:
                fid = r["path"].stem
                exp = r["fields"].get("source_experiment", "?")
                style = r["fields"].get("style", "?")
                rendered = r["fields"].get("rendered", "false")
                rendered_tag = " [rendered]" if rendered == "true" else ""
                print(f"  {fid}: experiment={exp} style={style}{rendered_tag}")
            return 0

        elif args.figure_cmd == "recommend":
            role_overrides = _build_role_overrides(
                getattr(args, "dimension", None),
                getattr(args, "measure", None),
            )
            return cmd_recommend(
                args.project,
                args.view_csv,
                task=args.task,
                why=args.why,
                role_overrides=role_overrides,
                config=cfg,
            )

    except (ValueError, KeyError) as e:
        print(f"rv figure: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"rv figure: unexpected error: {e}", file=sys.stderr)
        return 1

    return 0
