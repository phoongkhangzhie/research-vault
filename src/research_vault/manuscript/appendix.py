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

Stdlib only.
sr: SR-MS-1b
"""
from __future__ import annotations

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


def _render_value(field: str, value: str) -> str:
    """Render a repro field value for the LaTeX table.

    Sentinel → explicit gap text (never omitted).
    Manual fields at sentinel → flagged text.
    """
    val = (value or "").strip()
    if val == _SENTINEL or val == "":
        if field in _MANUAL_FIELDS:
            return r"\textit{not recorded in provenance (manual entry required)}"
        return r"\textit{not recorded in provenance}"
    return _latex_escape(val)


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

def inject_appendix(
    tree_root: Path,
    experiment_notes: list[Path],
) -> Path:
    """Inject repro_* fields from experiment notes into appendix-repro.tex.

    When to use: called by ``rv manuscript compile`` (and the appendix-repro
    DAG node) to machine-populate the reproducibility appendix. The appendix
    depends on SR-EXP-REPRO's repro_* fields being in the experiment notes.

    Anti-fabrication contract:
      - Sentinel values render as "not recorded in provenance" (explicit gap).
      - NEVER omit a field; NEVER fabricate a value.
      - The LLM NEVER fills a seed or hyperparameter table.

    Args:
        tree_root: path to manuscripts/<id>/ tree root.
        experiment_notes: list of experiments/ note paths to read repro_* from.

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
