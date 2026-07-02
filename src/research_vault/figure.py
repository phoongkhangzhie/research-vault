"""figure.py — `rv figure` verb: data→figure provenance + publication-quality plots.

When to use: When you have a dataset/scores note (SR-8 datasets/) and need a
publication-quality plot. Creates a figure-spec note (figures/<id>.md) with full
provenance — dataset OKF link, content hash, extract/filter recipe, style preset —
and renders styled SVG+PNG images via the apply_style seam.

Anti-pattern: do NOT hand-write a one-off matplotlib script and drop a PNG into a
finding — declare `rv figure new` against a `datasets/` note so the figure carries
dataset→filter→style provenance and afterok-able lineage.

Commands:
  rv figure <project> new <fig-id> --dataset <id> [options]  — create figure-spec note
  rv figure <project> preview <fig-id>                       — print frame + write view CSV
  rv figure <project> render <fig-id>                        — render SVG+PNG
  rv figure <project> list                                    — list figure specs

Requires the optional [figures] extra for preview/render:
  pip install research-vault[figures]
  (matplotlib>=3.8, seaborn>=0.13, pandas>=2.2)

The style seam: render calls `figures.style.apply_style(preset, skin=<project>)` before
plotting. The plumbing calls it; Iris replaces the stub with the real aesthetic.

figures are PROJECT-SCOPED: figures/<id>.md lives in project_notes_dir(project)/figures/.
This is deliberately NOT a shared root (only datasets/ gets the SR-8 shared treatment).

Stdlib only at import time — pandas/matplotlib are guarded behind the [figures] extra
and imported only in preview/render, never at module level.
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


def _parse_dataset_note_hash(datasets_root: Path, dataset_id: str) -> str:
    """Read the dataset hash from the SR-8 datasets provenance note.

    Returns the hash string (e.g. "sha256:abc...") or empty string if absent.
    Raises ValueError if the note doesn't exist.
    """
    note_path = datasets_root / f"{dataset_id}.md"
    if not note_path.exists():
        raise ValueError(
            f"dataset note not found: {note_path}\n"
            f"Create it first with: rv note <project> new datasets <title>"
        )
    text = note_path.read_text(encoding="utf-8")
    fields = _parse_frontmatter(text)
    return fields.get("hash", "").strip()


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

def cmd_new(
    project: str,
    fig_id: str,
    *,
    dataset_id: str,
    select: list[str] | None = None,
    filter_expr: str | None = None,
    plot_type: str = "line",
    style: str = "publication",
    config: Config | None = None,
) -> Path:
    """Create a figure-spec note (figures/<fig-id>.md) in the project's notes directory.

    The figure-spec records the full provenance:
      - source_dataset: datasets/<dataset_id>  — OKF link to the SR-8 datasets note
      - dataset_hash: sha256:<hex>             — content hash from the datasets note
      - select: <col1,col2,...>               — column subset (empty = all columns)
      - filter: <expr>                         — pandas query expression (empty = no filter)
      - plot_type: <type>                      — line | scatter | bar | box | hist | ...
      - style: <preset>                        — publication | slide | poster

    figures are PROJECT-SCOPED — the note lives at project_notes_dir(project)/figures/.

    Raises ValueError if the dataset note (datasets/<dataset_id>.md) does not exist
    in cfg.datasets_root — you must create the SR-8 datasets note first.

    Returns the path to the created figure note.
    """
    cfg = config or load_config()

    # Verify the dataset note exists and read its hash
    dataset_hash = _parse_dataset_note_hash(cfg.datasets_root, dataset_id)

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
        "source_dataset": f"datasets/{dataset_id}",
        "dataset_hash": dataset_hash,
        "select": select_str,
        "filter": filter_str,
        "plot_type": plot_type,
        "style": style,
        "rendered": "false",
    }

    body = (
        "\n"
        "<!-- Figures provenance note (SR-FIG) -->\n"
        "<!-- This note POINTS to image files — it does NOT embed image bytes. -->\n"
        "<!-- Run: rv figure preview <fig-id>  to inspect the data frame. -->\n"
        "<!-- Run: rv figure render <fig-id>   to produce SVG+PNG images. -->\n"
        "\n"
        "## What this figure shows\n\n"
        "<!-- Describe the plot: what it communicates, axes, key takeaway. -->\n\n"
        "## Render lineage\n\n"
        "<!-- Filled by `rv figure render` — rv version, render timestamp, image paths. -->\n"
    )

    note_path.write_text(_render_frontmatter(fields) + "\n" + body, encoding="utf-8")
    return note_path


def cmd_preview(
    project: str,
    fig_id: str,
    *,
    config: Config | None = None,
) -> int:
    """Print the data frame head + write the -view artifact (CSV + markdown table).

    This is the human-go inspection surface (§5E.3): the operator eyeballs the
    exact frame that will feed the plot before approving the data-check DAG node.

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
            f"  Create it first: rv figure {project} new {fig_id} --dataset <id>",
            file=sys.stderr,
        )
        return 1

    fields = _parse_frontmatter(note_path.read_text(encoding="utf-8"))
    dataset_id = fields.get("source_dataset", "").replace("datasets/", "").strip()
    if not dataset_id:
        print(
            f"rv figure preview: figure spec missing 'source_dataset' field: {note_path}",
            file=sys.stderr,
        )
        return 1

    # Resolve the dataset data file from the SR-8 datasets note
    dataset_note = cfg.datasets_root / f"{dataset_id}.md"
    if not dataset_note.exists():
        print(
            f"rv figure preview: dataset note not found: {dataset_note}",
            file=sys.stderr,
        )
        return 1

    ds_fields = _parse_frontmatter(dataset_note.read_text(encoding="utf-8"))
    location = ds_fields.get("location", "").strip()
    if not location:
        print(
            f"rv figure preview: dataset note has no 'location' field: {dataset_note}",
            file=sys.stderr,
        )
        return 1

    # Load the data
    try:
        if location.lower().endswith(".csv") or not location.startswith(("http", "doi:")):
            df = pd.read_csv(location)
        else:
            print(
                f"rv figure preview: only local CSV files supported in preview; "
                f"got location={location!r}",
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
        f"Source: `datasets/{dataset_id}` · Shape: {nrows}×{ncols}\n"
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
    config: Config | None = None,
) -> int:
    """Render the figure spec to SVG+PNG images.

    Reads the figure spec note, loads the dataset, applies extract/filter,
    calls apply_style(preset, skin=project), and renders to:
      state/figures/<fig-id>.svg
      state/figures/<fig-id>.png

    Updates the figure spec note with render_timestamp + rv_version.

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
    dataset_id = fields.get("source_dataset", "").replace("datasets/", "").strip()
    if not dataset_id:
        print(
            f"rv figure render: figure spec missing 'source_dataset': {note_path}",
            file=sys.stderr,
        )
        return 1

    # Load dataset
    dataset_note = cfg.datasets_root / f"{dataset_id}.md"
    if not dataset_note.exists():
        print(f"rv figure render: dataset note not found: {dataset_note}", file=sys.stderr)
        return 1
    ds_fields = _parse_frontmatter(dataset_note.read_text(encoding="utf-8"))
    location = ds_fields.get("location", "").strip()
    if not location:
        print(f"rv figure render: dataset note missing 'location': {dataset_note}", file=sys.stderr)
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

    # Apply style via the seam — Iris replaces this stub
    from .figures.style import apply_style
    preset = fields.get("style", "publication")
    apply_style(preset, project)

    # Basic render (Iris's implementation will be richer — plot_type→seaborn mapping)
    plot_type = fields.get("plot_type", "line")
    fig, ax = plt.subplots()
    numeric_cols = df.select_dtypes("number").columns.tolist()
    if numeric_cols:
        df[numeric_cols].plot(ax=ax, kind="line" if plot_type not in ("bar", "scatter", "hist", "box") else plot_type)
    ax.set_title(fig_id)
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
            new_lines.append(f"rendered: true")
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

    When to use: When you have a dataset/scores note (SR-8 datasets/) and need a
    publication-quality plot with full dataset→filter→style provenance. Use
    `rv figure new` to declare the figure spec (without rendering), `rv figure preview`
    to inspect the exact data frame, and `rv figure render` to produce the images.

    Anti-pattern: do NOT hand-write a one-off matplotlib script and drop a PNG into a
    finding — declare `rv figure new` against a `datasets/` note so the figure carries
    dataset→filter→style provenance and afterok-able lineage. One-off scripts drop the
    reproducibility chain; rv figure new builds it structurally.
    """
    desc = (
        "Create, preview, render, and list publication-quality figures from SR-8 dataset notes.\n"
        "Figures carry full provenance: dataset OKF link + hash + filter recipe + style preset.\n"
        "Requires pip install research-vault[figures] for preview/render (matplotlib/seaborn/pandas)."
    )
    if parent is not None:
        p = parent.add_parser("figure", help="Data→figure provenance + render.", description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv figure", description=desc)

    p.add_argument("project", help="Project slug.")

    sub = p.add_subparsers(dest="figure_cmd", required=True)

    # new
    new_p = sub.add_parser(
        "new",
        help="Create a figure-spec note (figures/<id>.md) without rendering.",
    )
    new_p.add_argument("fig_id", metavar="fig-id", help="Figure identifier (slug).")
    new_p.add_argument(
        "--dataset", dest="dataset_id", required=True, metavar="DATASET-ID",
        help="ID of the SR-8 datasets note (e.g. 'hfs-run-007').",
    )
    new_p.add_argument(
        "--select", nargs="+", default=None, metavar="COL",
        help="Column(s) to select from the dataset. Omit to use all columns.",
    )
    new_p.add_argument(
        "--filter", dest="filter_expr", default=None, metavar="EXPR",
        help="Pandas query expression to filter rows (e.g. 'lang == \"en\"').",
    )
    new_p.add_argument(
        "--type", dest="plot_type", default="line",
        metavar="PLOT-TYPE",
        help="Plot type: line (default), scatter, bar, box, hist.",
    )
    new_p.add_argument(
        "--style", dest="style", default="publication",
        choices=["publication", "slide", "poster"],
        help="Style preset (default: publication).",
    )

    # preview
    prev_p = sub.add_parser(
        "preview",
        help=(
            "Print the exact data frame + write state/figures/<id>-view.csv. "
            "The human-go data-check inspection surface (§5E.3). Requires [figures] extra."
        ),
    )
    prev_p.add_argument("fig_id", metavar="fig-id", help="Figure identifier.")

    # render
    rend_p = sub.add_parser(
        "render",
        help="Render the figure spec to SVG+PNG. Requires [figures] extra.",
    )
    rend_p.add_argument("fig_id", metavar="fig-id", help="Figure identifier.")

    # list
    list_p = sub.add_parser("list", help="List figure specs for the project.")

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
            path = cmd_new(
                args.project,
                args.fig_id,
                dataset_id=args.dataset_id,
                select=args.select,
                filter_expr=args.filter_expr,
                plot_type=args.plot_type,
                style=args.style,
                config=cfg,
            )
            print(f"Created figure spec: {path}")
            return 0

        elif args.figure_cmd == "preview":
            return cmd_preview(args.project, args.fig_id, config=cfg)

        elif args.figure_cmd == "render":
            return cmd_render(args.project, args.fig_id, config=cfg)

        elif args.figure_cmd == "list":
            results = cmd_list(args.project, config=cfg)
            if not results:
                print(f"No figure specs for {args.project!r}.")
                return 0
            print(f"Figure specs for {args.project!r}:")
            for r in results:
                fid = r["path"].stem
                ds = r["fields"].get("source_dataset", "?")
                style = r["fields"].get("style", "?")
                rendered = r["fields"].get("rendered", "false")
                rendered_tag = " [rendered]" if rendered == "true" else ""
                print(f"  {fid}: dataset={ds} style={style}{rendered_tag}")
            return 0

    except (ValueError, KeyError) as e:
        print(f"rv figure: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"rv figure: unexpected error: {e}", file=sys.stderr)
        return 1

    return 0
