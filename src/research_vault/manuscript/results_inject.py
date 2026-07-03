"""results_inject.py — Machine-injected results macros for manuscript grounding.

The LLM drafter writes prose AROUND macros (\\resultAcc, \\resultFone, etc.)
and NEVER types a literal number. This module:

  1. Reads each experiment note's results_location + results_hash.
  2. Hash-verifies the artifact (via check_result_provenance from note.py).
  3. Parses the JSON dict (or 2-col CSV) and emits \\newcommand{\\result<Key>}{<value>}
     macros into manuscripts/<id>/results.tex.
  4. Stamps results_hash + results_commit into the manuscript note's
     provenance block (the drift-guard stamp, §5J.5b).

Anti-fabrication contract:
  - If hash verification fails → hard ValueError (never silently proceed).
  - Only numeric/string values from hash-verified artifacts become macros.
  - Provenance stamp in the note records which artifact each number comes from.

SR-MS2-FIX (fix d): results artifact may be .json (dict) OR 2-col .csv (key,value).
  - .json: dict path (unchanged).
  - .csv: exactly 2 columns; may have a header row where the second column is
    'value' (case-insensitive) OR rows are plain key,value pairs.
    An ambiguous CSV (wrong column count) → clear error, not silent zero-macro skip.

Stdlib only.
sr: SR-MS-1b, SR-MS2-FIX
"""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Macro name generation
# ---------------------------------------------------------------------------

def _to_macro_name(key: str) -> str:
    """Convert a results JSON key to a valid LaTeX macro name component.

    Rules:
      - Strip non-alphanumeric characters.
      - CamelCase the segments split by _, -, .
      - Prefix: always starts with a letter.
      - Max length: 30 chars (LaTeX has no hard limit but be practical).

    Examples:
      "accuracy"          → "Accuracy"
      "f1_macro"          → "FOneMacro"    (digits get spelled out: f→F, 1→One)
      "bleu_score"        → "BleuScore"
      "top_5_accuracy"    → "TopFiveAccuracy"

    Digit-spelling ensures the macro name is valid LaTeX (no digit after \\).
    """
    _digit_words = {
        "0": "Zero", "1": "One", "2": "Two", "3": "Three", "4": "Four",
        "5": "Five", "6": "Six", "7": "Seven", "8": "Eight", "9": "Nine",
    }
    # Split on non-alnum boundaries
    parts = re.split(r"[^a-zA-Z0-9]+", key.strip())
    camel_parts: list[str] = []
    for part in parts:
        if not part:
            continue
        # Spell out digits within a part
        spelled: list[str] = []
        for i, ch in enumerate(part):
            if ch.isdigit():
                spelled.append(_digit_words[ch])
            elif i == 0:
                spelled.append(ch.upper())
            else:
                spelled.append(ch)
        camel_parts.append("".join(spelled))
    if not camel_parts:
        return "Result"
    result = "".join(camel_parts)
    # Ensure starts with uppercase letter (LaTeX \\Result...)
    if not result[0].isalpha():
        result = "R" + result
    return result[:30]


# ---------------------------------------------------------------------------
# Value formatting for LaTeX macros
# ---------------------------------------------------------------------------

def _format_value(v: Any) -> str:
    """Format a JSON value as a LaTeX macro body string.

    - float: format with up to 4 significant digits, strip trailing zeros.
    - int: as string.
    - str: as-is (LaTeX-safe — brace-wrapping done in the macro definition).
    - None/bool: convert to string.
    """
    if isinstance(v, float):
        # Use up to 4 decimal places, strip trailing zeros
        formatted = f"{v:.4f}".rstrip("0").rstrip(".")
        return formatted
    if isinstance(v, bool):
        return "True" if v else "False"
    if v is None:
        return ""
    return str(v)


# ---------------------------------------------------------------------------
# Provenance stamp
# ---------------------------------------------------------------------------

def _stamp_provenance(note_path: Path, stamp_lines: list[str]) -> None:
    """Append a provenance stamp block to the manuscript note's body.

    The stamp records which results artifact(s) contributed macros, with their
    hash and commit for drift detection (§5J.5b).

    Append-only: the stamp is idempotent (each compile rewrites the block).
    """
    if not note_path.exists():
        return
    text = note_path.read_text(encoding="utf-8")
    # Remove any previous stamp block (bounded by sentinel comments)
    cleaned = re.sub(
        r"<!-- results-provenance-stamp-start -->.*?<!-- results-provenance-stamp-end -->",
        "",
        text,
        flags=re.DOTALL,
    ).rstrip()
    stamp_block = "\n\n<!-- results-provenance-stamp-start -->\n"
    stamp_block += "## Results Provenance Stamp\n\n"
    stamp_block += "| Experiment | results_hash | results_commit |\n"
    stamp_block += "|---|---|---|\n"
    for line in stamp_lines:
        stamp_block += line + "\n"
    stamp_block += "\n<!-- results-provenance-stamp-end -->\n"
    note_path.write_text(cleaned + stamp_block, encoding="utf-8")


# ---------------------------------------------------------------------------
# CSV parser (SR-MS2-FIX fix d)
# ---------------------------------------------------------------------------

def _parse_csv_results(
    artifact: Path,
    errors_out: list[str],
) -> dict[str, Any] | None:
    """Parse a 2-column key,value CSV artifact into a dict.

    Accepted forms:
      (a) Header row where 2nd col is 'value' (case-insensitive) + data rows:
            key,value
            accuracy,0.85
            f1_macro,0.83
      (b) No header (or header where 2nd col is NOT 'value') + rows are key,value:
            accuracy,0.85
            f1_macro,0.83

    Returns None and appends to errors_out on ambiguity (wrong column count).
    Returns the dict on success (may be empty if no data rows).

    Anti-fabrication: an ambiguous CSV is a clear error, never a silent skip.
    """
    try:
        text = artifact.read_text(encoding="utf-8")
    except OSError as exc:
        errors_out.append(
            f"results_inject: cannot read CSV artifact {artifact}: {exc}"
        )
        return None

    reader = csv.reader(io.StringIO(text))
    rows = [row for row in reader if any(cell.strip() for cell in row)]

    if not rows:
        return {}  # empty file → empty dict (no macros, no error)

    # Validate column count — all rows must be exactly 2 columns
    bad_rows = [i for i, row in enumerate(rows) if len(row) != 2]
    if bad_rows:
        errors_out.append(
            f"results_inject: ambiguous CSV in {artifact.name} — expected exactly "
            f"2 columns (key,value) but row(s) {bad_rows} have {[len(rows[i]) for i in bad_rows]} "
            f"column(s). A CSV results artifact must be a simple key,value table. "
            f"Use JSON for multi-column or matrix results."
        )
        return None

    # Check if first row is a header (2nd col is 'value', case-insensitive)
    data_rows = rows
    first_row = rows[0]
    if first_row[1].strip().lower() == "value":
        # Header row — skip it
        data_rows = rows[1:]

    # Build dict from remaining rows
    result: dict[str, Any] = {}
    for row in data_rows:
        key = row[0].strip()
        val_raw = row[1].strip()
        if not key:
            continue
        # Try to parse as float/int; keep as string otherwise
        try:
            val: Any = int(val_raw)
        except ValueError:
            try:
                val = float(val_raw)
            except ValueError:
                val = val_raw
        result[key] = val

    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def inject_results(
    manuscript_note_path: Path,
    experiment_notes: list[Path],
    tree_root: Path,
) -> dict[str, Any]:
    """Read hash-verified experiment results and write results.tex macros.

    When to use: called by ``rv manuscript compile`` to populate results.tex
    with \\newcommand macros from the manuscript's scoped experiments.

    Anti-fabrication contract:
      - Hash mismatch → raises ValueError (never silently proceed with wrong data).
      - Each macro definition cites its source experiment note in a comment.
      - Provenance stamp (results_hash + results_commit) is written into the
        manuscript note for drift detection at the approve-manuscript gate.

    Args:
        manuscript_note_path: path to the manuscript/<id>.md OKF note.
        experiment_notes: list of experiments/ note paths to read results from.
            An empty list → writes a comment-only results.tex (no macros).
        tree_root: path to manuscripts/<id>/ (where results.tex lives).

    Returns:
        dict with "macros" (list of macro names emitted) and "errors" (list of
        error strings for non-fatal issues, e.g. non-numeric values skipped).

    Raises:
        ValueError: if results_hash verification fails (hash mismatch).
    """
    from research_vault.note import check_result_provenance, _parse_frontmatter

    results_tex = tree_root / "results.tex"
    macros_emitted: list[str] = []
    non_fatal_errors: list[str] = []
    stamp_lines: list[str] = []

    header = (
        "% results.tex — auto-populated by `rv manuscript compile`.\n"
        "% Each \\result* macro is injected from hash-verified experiment results.\n"
        "% The LLM MUST reference macros (\\resultAcc), NEVER type literal numbers.\n"
        "% Anti-fabrication: changing results.json invalidates the manuscript hash.\n"
    )

    if not experiment_notes:
        results_tex.write_text(
            header + "% No experiment notes in scope — no macros injected.\n",
            encoding="utf-8",
        )
        return {"macros": [], "errors": []}

    macro_lines: list[str] = [header, ""]

    for exp_note in experiment_notes:
        if not exp_note.exists():
            non_fatal_errors.append(
                f"results_inject: experiment note not found: {exp_note}"
            )
            continue

        # ── Hash verification ──────────────────────────────────────────────
        provenance_errors = check_result_provenance(exp_note)
        if provenance_errors:
            # Hard error: mismatch detected
            raise ValueError(
                f"results_inject: provenance check failed for {exp_note.name}:\n"
                + "\n".join(f"  {e}" for e in provenance_errors)
                + "\nFix: re-run the experiment or update results_hash in the note."
            )

        # ── Read experiment note fields ────────────────────────────────────
        text = exp_note.read_text(encoding="utf-8")
        fields, _ = _parse_frontmatter(text)
        results_location = fields.get("results_location", "").strip()
        results_hash = fields.get("results_hash", "").strip()
        results_commit = fields.get("results_commit", "").strip()
        exp_id = exp_note.stem

        if not results_location or not results_hash:
            # No results attached to this note — skip, not an error
            continue

        # ── Load results (JSON dict or 2-col CSV) ─────────────────────────
        artifact = Path(results_location)
        if not artifact.exists():
            non_fatal_errors.append(
                f"results_inject: results artifact not found: {results_location} "
                f"(from {exp_note.name})"
            )
            continue

        suffix = artifact.suffix.lower()
        if suffix == ".csv":
            # SR-MS2-FIX (fix d): branch on .csv → parse 2-column key,value into dict.
            # Accepts two forms:
            #   (a) header row + single data row: "key,value\nacc,0.85\n"
            #   (b) no header, each row is "key,value"
            # An ambiguous CSV (wrong column count, multiple data rows without
            # clear key/value structure) → clear error, NOT silent skip.
            raw = _parse_csv_results(artifact, non_fatal_errors)
            if raw is None:
                continue  # error already appended by _parse_csv_results
        else:
            try:
                raw = json.loads(artifact.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                non_fatal_errors.append(
                    f"results_inject: cannot parse {results_location}: {exc}"
                )
                continue

            if not isinstance(raw, dict):
                non_fatal_errors.append(
                    f"results_inject: results JSON is not a dict in {results_location}"
                )
                continue

        # ── Emit macros ────────────────────────────────────────────────────
        macro_lines.append(f"% ── {exp_id} (hash: {results_hash[:20]}…) ──")
        for key, value in raw.items():
            if not isinstance(key, str):
                continue
            macro_name = _to_macro_name(key)
            full_macro = f"result{macro_name}"
            value_str = _format_value(value)
            if not value_str:
                continue
            # IMPORTANT: the comment MUST come AFTER the closing brace.
            # Putting % inside the macro body (e.g. {0.85%  % comment})
            # causes a LaTeX runaway-argument/brace error — the % comments
            # out the closing } entirely.
            # Also escape any % in the value itself (percent as data, e.g. "85%").
            safe_value = value_str.replace("%", r"\%")
            macro_lines.append(
                f"\\newcommand{{\\{full_macro}}}{{{safe_value}}}"
                f"  % {exp_id}:{key}"
            )
            macros_emitted.append(full_macro)

        # ── Build provenance stamp line ────────────────────────────────────
        stamp_lines.append(
            f"| {exp_id} | `{results_hash[:20]}…` | `{results_commit[:12] or '—'}` |"
        )

    macro_lines.append("")  # trailing newline
    results_tex.write_text("\n".join(macro_lines), encoding="utf-8")

    # ── Stamp provenance into note ─────────────────────────────────────────
    if stamp_lines:
        _stamp_provenance(manuscript_note_path, stamp_lines)

    return {"macros": macros_emitted, "errors": non_fatal_errors}
