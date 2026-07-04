"""figures/render_script.py — pre-exec static check + scaffold emitter for render scripts.

A figure note MAY carry an optional ``render_script:`` frontmatter field pointing to an
authored Python script.  When present, ``rv figure render`` runs this script instead of
the built-in ``df.plot`` stub.  The authored script has full plotting authority — it can
use seaborn slopegraphs, custom layouts, multi-panel figures — everything the stub cannot.

The honesty gate: the script is STATICALLY CHECKED before execution.  ``static_check``
runs BEFORE exec (never executes untrusted code to check it) and enforces four classes
of violation:

  V1 — Forbidden import: any ``import X`` / ``from X import …`` where X is not in the
       allowlist.  Keeps the script surface minimal and auditable.

  V2 — Missing apply_style call: the script must call ``apply_style(`` at least once.
       Ensures the project identity (palette, rcParams) is applied — not raw defaults.

  V3 — Missing hash-verify: the script must (a) use ``hashlib.sha256`` or ``_hash_file``
       to hash the results file, (b) compare the digest against ``experiment_results_hash``
       (a name that must appear in the script), and (c) call ``sys.exit`` or ``raise`` on
       mismatch.  All three components must be present.  This is the integrity lock: a
       script that silently plots tampered data is an honesty failure.

  V4 — Baked-claim title: ``ax.set_title(…)`` or ``fig.suptitle(…)`` with a string-
       literal argument.  Reuses the figure-minimalism no-title rule (SR-FIG-MINIMAL,
       §5J.16.5): descriptive text belongs in the LaTeX ``\\caption``, not the raster.

``emit_scaffold`` emits an AUTHOR-ME template.  CRITICAL: the scaffold deliberately does
NOT satisfy ``static_check`` — a machine-generated always-green script makes the gate
vacuous (Wren's ruling).  The scaffold is a starting point for a human/LLM to author;
the check runs on the AUTHORED result.

Authored-script contract (summary; full details in the scaffold docstring):
  - Loads the frozen CSV from ``results_location`` (read from experiment note).
  - Recomputes sha256 of the CSV and ABORTs loudly on mismatch vs
    ``experiment_results_hash``.
  - Imports ``apply_style`` from ``research_vault.figures.style`` and calls it.
  - Draws the figure per-plot in a ``try/except``.
  - Saves at dpi 300; writes ``state/figures/<id>.provenance.json``.

Stdlib only — no matplotlib/seaborn at module level (pure ast + pathlib + subprocess).
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Import allowlist — the only top-level modules a render script may import
# ---------------------------------------------------------------------------

_ALLOWED_IMPORT_ROOTS: frozenset[str] = frozenset({
    "matplotlib",
    "seaborn",
    "pandas",
    "numpy",
    "research_vault",
    "hashlib",
    "pathlib",
    "sys",
    "json",
})

# Full qualified names explicitly allowed (covers ``from matplotlib import pyplot``)
_ALLOWED_IMPORT_FULL: frozenset[str] = frozenset({
    "matplotlib",
    "matplotlib.pyplot",
    "seaborn",
    "pandas",
    "numpy",
    "research_vault.figures.style",
    "hashlib",
    "pathlib",
    "sys",
    "json",
})


def _get_import_module(node: ast.stmt) -> str | None:
    """Return the module name string for an Import or ImportFrom node.

    For ``import foo.bar``: returns ``"foo.bar"``.
    For ``from foo.bar import baz``: returns ``"foo.bar"``.
    Returns None for non-import nodes.
    """
    if isinstance(node, ast.Import):
        # Multi-alias imports: ``import foo, bar`` — check each alias
        return None  # handled in caller by iterating aliases
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        return module
    return None


def _module_allowed(module: str) -> bool:
    """Return True if the import module is in the allowlist.

    Checks exact match against the full allowlist first, then falls back to
    checking the root package (first component) against the root allowlist.
    This permits e.g. ``import matplotlib.patches`` (root "matplotlib" is allowed)
    while still blocking ``import os`` (root "os" is not allowed).
    """
    if module in _ALLOWED_IMPORT_FULL:
        return True
    root = module.split(".")[0]
    return root in _ALLOWED_IMPORT_ROOTS


# ---------------------------------------------------------------------------
# AST walker helpers
# ---------------------------------------------------------------------------

def _collect_call_names(tree: ast.AST) -> set[str]:
    """Return a set of all function call names in the AST.

    For ``foo()``: returns ``{"foo"}``.
    For ``foo.bar()``: returns ``{"foo.bar"}``.
    For ``obj.method()``: returns ``{"obj.method"}``.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Name):
                names.add(fn.id)
            elif isinstance(fn, ast.Attribute):
                # Reconstruct dotted name (one level: ``obj.method``)
                if isinstance(fn.value, ast.Name):
                    names.add(f"{fn.value.id}.{fn.attr}")
                else:
                    names.add(fn.attr)  # bare method name as fallback
    return names


def _collect_names(tree: ast.AST) -> set[str]:
    """Return all ast.Name ids that appear anywhere in the tree."""
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def _has_raise_or_sys_exit(tree: ast.AST) -> bool:
    """Return True if the tree contains a ``raise`` statement or ``sys.exit(…)``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise):
            return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "sys"
            and node.func.attr == "exit"
        ):
            return True
    return False


def _has_hash_verify(tree: ast.AST, call_names: set[str], all_names: set[str]) -> bool:
    """Return True if the hash-verify pattern is present.

    Requires ALL THREE of:
      (a) ``hashlib.sha256`` call OR ``_hash_file`` call (hash computation)
      (b) the name ``experiment_results_hash`` appears in the AST
      (c) ``sys.exit`` or ``raise`` appears (abort on mismatch)
    """
    has_hash_call = (
        "hashlib.sha256" in call_names
        or "_hash_file" in call_names
        or "hash_file" in call_names
    )
    has_hash_ref = "experiment_results_hash" in all_names
    has_abort = _has_raise_or_sys_exit(tree)
    return has_hash_call and has_hash_ref and has_abort


def _has_baked_title(tree: ast.AST) -> bool:
    """Return True if any set_title / suptitle call has a string-literal first arg.

    String-literal first args are baked claims — they burn factual text into the
    raster (SR-FIG-MINIMAL violation: descriptive text belongs in LaTeX caption).
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        is_title_call = (
            (isinstance(fn, ast.Name) and fn.id in ("set_title", "suptitle"))
            or (isinstance(fn, ast.Attribute) and fn.attr in ("set_title", "suptitle"))
        )
        if not is_title_call:
            continue
        # Check first positional arg
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            return True
        # Check ``title=`` keyword arg
        for kw in node.keywords:
            if kw.arg == "title" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def static_check(script_path: Path) -> list[str]:
    """Run a pre-exec static check on a render script.  Returns violation strings.

    Runs BEFORE execution — never executes untrusted code to check it.  Uses
    stdlib ``ast`` only.

    Four violation classes (V1–V4):

      V1: ``[V1-IMPORT] forbidden import: <module>`` — any import outside the
          allowlist ``{matplotlib, matplotlib.pyplot, seaborn, pandas, numpy,
          research_vault.figures.style, hashlib, pathlib, sys, json}``.

      V2: ``[V2-STYLE] missing apply_style() call`` — the script must call
          ``apply_style(`` at least once to install the project palette/rcParams.

      V3: ``[V3-INTEGRITY] missing hash-verify`` — the script must compute a
          sha256 hash (via ``hashlib.sha256`` or ``_hash_file``/``hash_file``),
          reference ``experiment_results_hash``, AND contain a ``sys.exit``
          or ``raise`` to abort on mismatch.

      V4: ``[V4-TITLE] baked-claim title`` — ``set_title(…)`` / ``suptitle(…)``
          with a string-literal argument.  Descriptive text belongs in LaTeX
          ``\\caption``, not the raster (SR-FIG-MINIMAL, §5J.16.5).

    Args:
        script_path: Path to the Python render script.

    Returns:
        List of violation strings.  Empty list means the script passes all checks.

    Raises:
        FileNotFoundError: if ``script_path`` does not exist.
        SyntaxError: if the script has a Python syntax error (wraps ast.parse).
    """
    source = script_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(script_path))

    violations: list[str] = []

    # --- V1: Forbidden imports ---
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _module_allowed(alias.name):
                    violations.append(
                        f"[V1-IMPORT] forbidden import: '{alias.name}' "
                        f"(line {node.lineno}) — allowlist: "
                        "{matplotlib, matplotlib.pyplot, seaborn, pandas, numpy, "
                        "research_vault.figures.style, hashlib, pathlib, sys, json}"
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if not _module_allowed(module):
                violations.append(
                    f"[V1-IMPORT] forbidden import: '{module}' "
                    f"(line {node.lineno}) — allowlist: "
                    "{matplotlib, matplotlib.pyplot, seaborn, pandas, numpy, "
                    "research_vault.figures.style, hashlib, pathlib, sys, json}"
                )

    # --- V2: Missing apply_style() call ---
    call_names = _collect_call_names(tree)
    if "apply_style" not in call_names:
        violations.append(
            "[V2-STYLE] missing apply_style() call — the script must call "
            "apply_style(preset, skin) from research_vault.figures.style to install "
            "the project palette and rcParams before plotting."
        )

    # --- V3: Missing hash-verify ---
    all_names = _collect_names(tree)
    if not _has_hash_verify(tree, call_names, all_names):
        violations.append(
            "[V3-INTEGRITY] missing hash-verify — the script must: "
            "(a) compute a sha256 hash via hashlib.sha256() or _hash_file()/_hash_file(), "
            "(b) reference the name 'experiment_results_hash' for comparison, AND "
            "(c) call sys.exit() or raise an exception on mismatch.  "
            "All three components must be present."
        )

    # --- V4: Baked-claim title ---
    if _has_baked_title(tree):
        violations.append(
            "[V4-TITLE] baked-claim title — set_title() or suptitle() with a string-literal "
            "argument burns factual text into the raster.  Descriptive text belongs in the "
            "LaTeX \\caption{} (SR-FIG-MINIMAL, §5J.16.5).  Use ax.set_ylabel() for "
            "axis labels; omit set_title() on the publication path."
        )

    return violations


def emit_scaffold(fig_note_fields: dict[str, Any]) -> str:
    """Emit an AUTHOR-ME render-script template for a figure note.

    CRITICAL: this scaffold deliberately does NOT satisfy static_check.
    A machine-generated always-green script makes the honesty gate vacuous
    (Wren's ruling).  The scaffold is a starting point — a human or LLM fills
    in the FILL markers; static_check runs on the AUTHORED result.

    The scaffold structure:
      - FILL-IN markers for every decision that requires human judgment
        (plot type, axis labels, legend placement, colour assignment).
      - Authored-script contract documented inline (hash-verify pattern,
        apply_style call, provenance JSON output).
      - Does NOT include a pre-written apply_style call (V2 placeholder only).
      - Does NOT include a pre-written hash-verify block (V3 placeholder only).
      - Does NOT set a baked title (V4 compliant).

    Args:
        fig_note_fields: frontmatter fields dict from the figure note
                         (as returned by ``_parse_frontmatter``).  Used to
                         populate known fields (fig_id, experiment path, style preset).

    Returns:
        Python source string for the scaffold render script.
    """
    fig_id = fig_note_fields.get("title", "FILL_FIG_ID")
    source_exp = fig_note_fields.get("source_experiment", "experiments/FILL_EXP_ID")
    style_preset = fig_note_fields.get("style", "publication")
    results_hash = fig_note_fields.get("experiment_results_hash", "sha256:FILL_HASH")
    select = fig_note_fields.get("select", "")
    filter_expr = fig_note_fields.get("filter", "")

    select_comment = f"# Columns: {select}" if select else "# select: (all columns)"
    filter_comment = f"# Filter : {filter_expr}" if filter_expr else "# filter : (none)"

    return f'''"""render_script — authored render for figure: {fig_id}

AUTHOR-ME TEMPLATE.  This scaffold does not pass static_check until you fill in
the FILL markers.  Run ``rv figure <project> render <fig-id>`` to verify after authoring.

Authored-script contract:
  1. Load the frozen CSV from ``results_location`` (from the experiment note).
  2. Recompute sha256 of the CSV and ABORT loudly on mismatch vs
     ``experiment_results_hash``.  NEVER plot tampered data.
  3. Import ``apply_style`` and call it BEFORE any plt.subplots / sns call.
  4. Draw the figure (per-plot in try/except to surface partial failures).
  5. Save at dpi=300 (handled by rcParams after apply_style).
  6. Write state/figures/{fig_id}.provenance.json with run metadata.

Source experiment: {source_exp}
Style preset     : {style_preset}
Results hash     : {results_hash}
{select_comment}
{filter_comment}
"""
# FILL: add only imports from the allowlist:
#   matplotlib, matplotlib.pyplot, seaborn, pandas, numpy,
#   research_vault.figures.style, hashlib, pathlib, sys, json
import sys
from pathlib import Path

# FILL: import the plotting libraries you need, e.g.:
#   import matplotlib.pyplot as plt
#   import seaborn as sns
#   import pandas as pd

# FILL: import apply_style from the seam
#   from research_vault.figures.style import apply_style
# (Required by static_check V2.)


# ---------------------------------------------------------------------------
# Configuration (populated by rv figure render — do not hard-code paths here)
# ---------------------------------------------------------------------------
# rv figure render injects these names into the script's execution namespace:
#   results_location        — absolute path to the frozen CSV
#   experiment_results_hash — "sha256:<hex>" from the experiment note
#   fig_id                  — figure identifier (used for output paths)
#   state_figures_dir       — absolute path to state/figures/
#   preset                  — style preset name ("{style_preset}")
#   project                 — project slug


# ---------------------------------------------------------------------------
# Step 1 — Hash-verify (V3 REQUIRED — fill this in)
# ---------------------------------------------------------------------------
# FILL: verify the CSV digest before plotting anything.
# Pattern (do not skip this block):
#
#   h = hashlib.sha256()
#   with open(results_location, "rb") as fh:
#       while chunk := fh.read(1 << 20):
#           h.update(chunk)
#   actual_hash = "sha256:" + h.hexdigest()
#   if actual_hash != experiment_results_hash:
#       print(
#           f"ABORT: results CSV hash mismatch!\\n"
#           f"  expected: {{experiment_results_hash}}\\n"
#           f"  actual  : {{actual_hash}}",
#           file=sys.stderr,
#       )
#       sys.exit(1)


# ---------------------------------------------------------------------------
# Step 2 — Load data
# ---------------------------------------------------------------------------
# FILL: load the CSV and apply any column select / filter from the figure spec.
#   df = pd.read_csv(results_location)


# ---------------------------------------------------------------------------
# Step 3 — Apply project style (V2 REQUIRED — call apply_style before plotting)
# ---------------------------------------------------------------------------
# FILL: uncomment and call apply_style.
#   apply_style(preset, project)


# ---------------------------------------------------------------------------
# Step 4 — Draw the figure
# ---------------------------------------------------------------------------
# FILL: create your figure with seaborn / matplotlib.
# No set_title() or suptitle() with a string literal (V4: baked-claim title
# rule; descriptive text belongs in the LaTeX \\caption{{}}).
# Use ax.set_ylabel("…") for axis labels.
#
# try:
#     fig, ax = plt.subplots()
#     # FILL: your plot here (slopegraph, bar chart, scatter, etc.)
#     ax.set_ylabel("FILL: axis label")      # informative label, no underscores
# except Exception as e:
#     print(f"render error: {{e}}", file=sys.stderr)
#     sys.exit(1)


# ---------------------------------------------------------------------------
# Step 5 — Save outputs
# ---------------------------------------------------------------------------
# FILL: save SVG + PNG.  dpi is set by apply_style (rcParams savefig.dpi).
#
# out_dir = Path(state_figures_dir)
# out_dir.mkdir(parents=True, exist_ok=True)
# fig.savefig(str(out_dir / f"{{fig_id}}.svg"), format="svg")
# fig.savefig(str(out_dir / f"{{fig_id}}.png"), format="png")
# plt.close(fig)


# ---------------------------------------------------------------------------
# Step 6 — Write provenance JSON
# ---------------------------------------------------------------------------
# FILL: record render metadata.
#
# import json
# prov = {{
#     "fig_id": fig_id,
#     "experiment_results_hash": experiment_results_hash,
#     "preset": preset,
#     "project": project,
# }}
# (out_dir / f"{{fig_id}}.provenance.json").write_text(
#     json.dumps(prov, indent=2), encoding="utf-8"
# )
# print(f"Rendered: {{out_dir / fig_id}}.svg")
# print(f"Rendered: {{out_dir / fig_id}}.png")
'''
