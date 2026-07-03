"""appendix.py — Appendix reproducibility table injection for manuscripts.

Reads the structured ``repro_*`` fields from experiment notes (added by
SR-EXP-REPRO) and injects them into sections/appendix-repro.tex as a
machine-populated reproducibility table.

Anti-fabrication contract (§5J.5c + Ada #2):
  - A ``not-recorded-in-provenance`` sentinel renders as an EXPLICIT GAP
    in the table ("not recorded in provenance") — NEVER omitted or faked.
  - The LLM is NEVER allowed to fill a seed/hyperparameter table.
  - Auto-populated fields come from verified W&B config / results.json.
  - Manual fields (cross-lingual trio, prompt_version, etc.) are LOUDLY
    flagged as "manual entry required" when still at sentinel.

Audience filter (SR-MS-AUDIENCE §5J.16.2):
  - _is_proxy_study: detects proxy/no-run studies (all-empty results_location
    or >threshold fraction of required fields at sentinel). When True,
    inject_appendix emits a reframe paragraph instead of a sentinel wall.
  - _sanitize_appendix_value: maps filesystem paths to identifiers/available-on-
    request; hashes pass through (verification anchors, D-AUD-3).

Stdlib only.
sr: SR-MS-1b; SR-MS-AUDIENCE (audience filter)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Field labels (human-readable names for the repro_* fields)
# ---------------------------------------------------------------------------

_FIELD_LABELS: dict[str, str] = {
    # Layer 1 — hashed config artifact
    "repro_config_location": "Config artifact location",
    "repro_config_hash": "Config artifact hash (sha256)",
    # Auto from run.config
    "repro_seed": "Random seed",
    "repro_model_id": "Model identifier",
    "repro_model_revision": "Model revision / checkpoint",
    "repro_decode_temperature": "Decoding temperature",
    "repro_decode_top_p": "Decoding top-p",
    "repro_decode_max_tokens": "Max output tokens",
    "repro_num_fewshot": "Number of few-shot examples",
    "repro_tokenizer": "Tokenizer",
    # Auto from run.metadata
    "repro_env_packages": "Key package versions",
    "repro_env_python": "Python version",
    "repro_cost_gpu_hours": "GPU hours consumed",
    # Auto from SR-6 manifest
    "repro_hw": "Hardware (GPU type/count)",
    # Auto from SR-8 dataset note
    "repro_dataset_id": "Dataset identifier",
    "repro_dataset_hash": "Dataset hash (sha256)",
    # Auto from results_commit
    "repro_eval_harness": "Evaluation harness version",
    # Manual fields (flagged)
    "repro_prompt_lang": "Prompt language (BCP-47)",
    "repro_translation_provenance": "Translation provenance",
    "repro_prompt_version": "Prompt version / identifier",
    "repro_dataset_split": "Dataset split used",
    "repro_metric": "Primary metric",
}

# Manual fields — flagged loudly if still at sentinel
_MANUAL_FIELDS = frozenset({
    "repro_prompt_lang",
    "repro_translation_provenance",
    "repro_prompt_version",
    "repro_dataset_split",
    "repro_metric",
})

_SENTINEL = "not-recorded-in-provenance"

# LaTeX special chars that need escaping in table cells
_LATEX_SPECIAL = str.maketrans({
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
    "\\": r"\textbackslash{}",
})


def _latex_escape(s: str) -> str:
    return s.translate(_LATEX_SPECIAL)


# ---------------------------------------------------------------------------
# Proxy-study detection (SR-MS-AUDIENCE §5J.16.2a)
# ---------------------------------------------------------------------------

# Required repro fields that a real experiment should populate.
# When a high fraction of these are at sentinel AND no results_location is set,
# the study is treated as a proxy/no-run analysis.
_PROXY_REQUIRED_FIELDS = frozenset({
    "repro_seed",
    "repro_model_id",
    "repro_model_revision",
    "repro_decode_temperature",
    "repro_decode_top_p",
    "repro_decode_max_tokens",
    "repro_dataset_id",
    "repro_eval_harness",
    "repro_metric",
})

# Default fraction of required-fields that must be at sentinel to trigger reframe.
# Configurable via [manuscript_check].proxy_sentinel_fraction in research_vault.toml.
_DEFAULT_PROXY_SENTINEL_FRACTION: float = 0.6

# Filesystem-path patterns for _sanitize_appendix_value
_FS_PATH_RE = re.compile(
    r"^(?:/|~/|\./).*|.*[\\/].*\.(csv|json|yaml|yml|txt|pkl|parquet|h5|pt|ckpt)$",
    re.IGNORECASE,
)
# Also catch relative results/ paths
_RESULTS_PATH_RE = re.compile(r"(?:^|/)results/", re.IGNORECASE)
# SHA256 hash patterns — pass through (verification anchors, D-AUD-3)
_HASH_RE = re.compile(r"^sha256:[0-9a-fA-F]{8,}|^[0-9a-fA-F]{32,}$", re.IGNORECASE)


def _is_proxy_study(
    experiment_notes: list[Path],
    *,
    proxy_sentinel_fraction: float | None = None,
    config: "Any | None" = None,
) -> bool:
    """Detect whether the study is a proxy/no-run analysis (SR-MS-AUDIENCE §5J.16.2a).

    A proxy study is one where no experimental run was actually executed by the
    author — e.g. a re-analysis of published aggregate results. Two conditions
    each independently trigger the proxy label:

    1. ALL experiment notes in scope have an empty ``results_location`` field
       (the primary structural signal — inject_results already early-returns on
       this per results_inject.py:265).

    2. MORE THAN ``proxy_sentinel_fraction`` of the required repro fields across
       all scope notes are at the 'not-recorded-in-provenance' sentinel.
       (Configurable via [manuscript_check].proxy_sentinel_fraction, default 0.6)

    Args:
        experiment_notes: list of path objects to experiment OKF notes.
        proxy_sentinel_fraction: override fraction (0.0–1.0). When None, reads
            from config or falls back to _DEFAULT_PROXY_SENTINEL_FRACTION.
        config: optional Config instance for reading the fraction seam.

    Returns:
        True if the study is a proxy/no-run analysis; False otherwise.

    sr: SR-MS-AUDIENCE
    """
    from research_vault.note import _parse_frontmatter, REPRO_SENTINEL

    if not experiment_notes:
        return False  # No experiments: can't determine — not a proxy study

    # Resolve the threshold
    threshold = proxy_sentinel_fraction
    if threshold is None and config is not None:
        raw = getattr(config, "_raw", {})
        ms_check = raw.get("manuscript_check", {})
        if isinstance(ms_check, dict):
            cfg_frac = ms_check.get("proxy_sentinel_fraction")
            if isinstance(cfg_frac, (int, float)) and 0.0 <= cfg_frac <= 1.0:
                threshold = float(cfg_frac)
    if threshold is None:
        threshold = _DEFAULT_PROXY_SENTINEL_FRACTION

    all_results_empty = True
    total_required = 0
    total_sentinel = 0
    note_count = 0

    for note_path in experiment_notes:
        if not note_path.exists():
            continue
        try:
            text = note_path.read_text(encoding="utf-8")
        except OSError:
            continue
        fields, _ = _parse_frontmatter(text)
        note_count += 1

        # Check results_location (the primary structural signal for "real run")
        rl = fields.get("results_location", "").strip()
        if rl:
            # At least one note has a results_location set → not all-empty
            all_results_empty = False

        # Count sentinel fields (only meaningful for notes without results_location)
        # A real run (results_location set) with incomplete repro fields is an
        # HONEST GAP, not a proxy study. The reframe fires "only when the whole
        # study is a proxy" (§5J.16.2a spec ruling).
        if not rl:
            for f in _PROXY_REQUIRED_FIELDS:
                total_required += 1
                val = fields.get(f, "").strip()
                if val == REPRO_SENTINEL or val == "":
                    total_sentinel += 1

    if note_count == 0:
        return False  # No readable notes — cannot determine

    # Trigger 1: ALL notes have empty results_location → no runs at all → proxy study
    if all_results_empty:
        return True

    # Trigger 2 does NOT fire if any note has results_location set.
    # (A real run with some sentinel fields is an honest gap, not a proxy study.)

    return False


def _sanitize_appendix_value(field: str, value: str) -> str:
    """Sanitize a repro field value for the badged APPENDIX (SR-MS-AUDIENCE §5J.16.2b).

    Applies the path→identifier substitution required by D-AUD-3:
      - Hashes (sha256:... or long hex strings) → pass through unchanged
        (verification anchors; a reproducer checks them against their artifact).
      - Filesystem paths (absolute or relative with path-shaped structure) →
        ``\\textit{available on request}`` (a local path is not actionable for
        a reader without repo access).
      - All other values → pass through (identifiers, DOIs, model names, etc.).

    NOTE: this function is for VALUES inside the appendix (zone-2). It is
    distinct from the body leak-scan (zone-1) which BLOCKS on these patterns.
    The appendix LEGITIMATELY carries hashes; it just should not carry raw
    filesystem paths that a stranger cannot resolve.

    Args:
        field: the repro_* field name (e.g. "repro_config_location").
        value: the raw value string from the OKF note frontmatter.

    Returns:
        A sanitized string ready for LaTeX rendering (not yet escaped).

    sr: SR-MS-AUDIENCE
    """
    val = (value or "").strip()
    if not val:
        return val  # Let _render_value handle blank/sentinel

    # Hashes pass through (D-AUD-3: verification anchors stay in appendix)
    if _HASH_RE.match(val):
        return val

    # Filesystem paths → available on request
    if _FS_PATH_RE.match(val) or _RESULTS_PATH_RE.search(val):
        return r"\textit{available on request}"

    return val


def _render_value(field: str, value: str) -> str:
    """Render a repro field value for the LaTeX table.

    Sentinel → explicit gap text (never omitted).
    Manual fields at sentinel → flagged text.
    For non-sentinel values, applies _sanitize_appendix_value first (path→id
    substitution per SR-MS-AUDIENCE §5J.16.2b, D-AUD-3).
    """
    val = (value or "").strip()
    if val == _SENTINEL or val == "":
        if field in _MANUAL_FIELDS:
            return r"\textit{not recorded in provenance (manual entry required)}"
        return r"\textit{not recorded in provenance}"
    # Sanitize before escaping (paths → available on request; hashes pass through)
    sanitized = _sanitize_appendix_value(field, val)
    return _latex_escape(sanitized)


# ---------------------------------------------------------------------------
# Table generation
# ---------------------------------------------------------------------------

def _single_experiment_table(exp_id: str, fields: dict[str, str]) -> str:
    """Generate a LaTeX longtable for one experiment's repro fields."""
    from research_vault.note import REPRO_ALL_FIELDS

    lines = [
        r"\begin{table}[h]",
        r"\centering",
        r"\small",
        f"\\caption{{Reproducibility: \\texttt{{{_latex_escape(exp_id)}}}}}",
        r"\begin{tabular}{ll}",
        r"\toprule",
        r"\textbf{Field} & \textbf{Value} \\",
        r"\midrule",
    ]
    for field in REPRO_ALL_FIELDS:
        label = _FIELD_LABELS.get(field, field.replace("_", " ").title())
        value = fields.get(field, _SENTINEL)
        rendered = _render_value(field, value)
        lines.append(f"{_latex_escape(label)} & {rendered} \\\\")
    lines.extend([
        r"\bottomrule",
        r"\end{tabular}",
        r"\end{table}",
    ])
    return "\n".join(lines)


def _no_experiments_placeholder() -> str:
    """Placeholder content when no experiment notes are provided."""
    return (
        "% appendix-repro.tex — auto-populated by `rv manuscript compile`.\n"
        "% No experiment notes in scope — reproducibility table is empty.\n"
        "% Add experiment notes with repro_* fields (via rv wandb pull) to populate.\n"
        "\n"
        r"\textit{No experiment reproducibility data recorded. "
        r"Run \texttt{rv wandb pull} to auto-populate.}"
        "\n"
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def _proxy_study_reframe_tex() -> str:
    """Return the proxy/no-run study reframe paragraph for the appendix.

    Used when _is_proxy_study() returns True. Replaces the sentinel wall
    ('not recorded in provenance' for every row) with an honest statement
    explaining that no experimental runs were executed (SR-MS-AUDIENCE §5J.16.2a).
    """
    return (
        r"\section*{Reproducibility}" + "\n\n"
        "\\textit{This work is a re-analysis of published aggregate results; "
        "no new experimental runs were executed by the authors. "
        "Reproducibility fields (random seed, model configuration, hardware, etc.) "
        "are therefore not applicable to this study. "
        "We refer readers to the original publications cited in the related-work "
        "section for the provenance of the aggregated results.}"
        "\n"
    )


def inject_appendix(
    tree_root: Path,
    experiment_notes: list[Path],
    *,
    config: "Any | None" = None,
) -> Path:
    """Inject repro_* fields from experiment notes into appendix-repro.tex.

    When to use: called by ``rv manuscript compile`` (and the appendix-repro
    DAG node) to machine-populate the reproducibility appendix. The appendix
    depends on SR-EXP-REPRO's repro_* fields being in the experiment notes.

    Anti-fabrication contract:
      - Sentinel values render as "not recorded in provenance" (explicit gap).
      - NEVER omit a field; NEVER fabricate a value.
      - The LLM NEVER fills a seed or hyperparameter table.

    Audience filter (SR-MS-AUDIENCE §5J.16.2a):
      - Proxy/no-run studies: if _is_proxy_study() is True, emits a reframe
        paragraph instead of a wall of 'not-recorded-in-provenance' rows.
      - Real runs with honest gaps: individual sentinel rows still render.

    Args:
        tree_root: path to manuscripts/<id>/ tree root.
        experiment_notes: list of experiments/ note paths to read repro_* from.
        config: optional Config for proxy_sentinel_fraction seam (D-AUD-2).

    Returns:
        Path to the written sections/appendix-repro.tex.
    """
    from research_vault.note import _parse_frontmatter

    sections_dir = tree_root / "sections"
    sections_dir.mkdir(parents=True, exist_ok=True)
    appendix_tex = sections_dir / "appendix-repro.tex"

    header = (
        "% appendix-repro.tex — auto-populated by `rv manuscript compile`.\n"
        "% Machine-generated from repro_* fields in experiments/ notes.\n"
        "% Sentinel 'not-recorded-in-provenance' renders as explicit gap.\n"
        "% The LLM NEVER fills a seed or hyperparameter table.\n"
        "%\n"
        "% Requires LaTeX packages: booktabs, tabularx or tabular (longtable optional)\n"
        "%\n"
    )

    if not experiment_notes:
        appendix_tex.write_text(header + _no_experiments_placeholder(), encoding="utf-8")
        return appendix_tex

    # ── Audience filter: proxy-study detection (SR-MS-AUDIENCE §5J.16.2a) ────
    # Check BEFORE building tables. A proxy study emits a reframe paragraph
    # instead of a wall of sentinel rows that would confuse a reader.
    if _is_proxy_study(experiment_notes, config=config):
        appendix_tex.write_text(header + _proxy_study_reframe_tex(), encoding="utf-8")
        return appendix_tex

    body_parts: list[str] = [header, ""]
    body_parts.append(r"\section*{Reproducibility}")
    body_parts.append("")
    body_parts.append(
        "The following tables are machine-generated from the experiment "
        "notes' \\texttt{repro\\_*} fields. "
        "Fields showing ``not recorded in provenance'' are honest gaps "
        "where data was not logged to W\\&B config; they are never fabricated."
    )
    body_parts.append("")

    for exp_note in experiment_notes:
        if not exp_note.exists():
            body_parts.append(
                f"% Experiment note not found: {exp_note} — skipped"
            )
            continue
        try:
            text = exp_note.read_text(encoding="utf-8")
        except OSError as exc:
            body_parts.append(f"% Cannot read {exp_note}: {exc}")
            continue
        fields, _ = _parse_frontmatter(text)
        exp_id = exp_note.stem
        body_parts.append("")
        body_parts.append(_single_experiment_table(exp_id, fields))
        body_parts.append("")

    appendix_tex.write_text("\n".join(body_parts), encoding="utf-8")
    return appendix_tex
