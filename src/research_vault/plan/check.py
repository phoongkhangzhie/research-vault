# SPDX-License-Identifier: AGPL-3.0-or-later
"""plan/check.py — shape-lint for pre-registered experiment plans (SR-PLAN-1/2, K-2).

REJECTS-ONLY structural screen (charter §9): can only FAIL an ill-formed plan,
never certify a good one.  The semantic completeness judgment (is the diagnosis
table *sensible*?) stays with the plan-critic (reviewer); this lint catches what
does NOT need an LLM.

Three rules (§5K.5.5 + SR-PLAN-2):
  (a) BRANCH-PRESENCE — every diagnosis table in the plan master note has a
      named conclusion AND a committed action for every outcome row.  An empty
      cell, a "fallback" row, or a "TBD" cell is a lint FAIL.

  (b) ONE-COMPONENT-PER-ABLATION — the plan note body's supporting-ablation
      sections must not list more than one component being manipulated.  The
      lint looks for "Component manipulated:" lines; a multi-component statement
      (contains " and " or a comma-separated list with ≥2 items) is a FAIL.

  (c) COVERS-ID CONVENTION (SR-PLAN-2) — every entry in the covers: field must
      be a bare experiment ID (e.g. "q1-main1"), NOT a path-prefixed ID
      (e.g. "experiments/q1-main1").  Path-prefixed entries break freeze.py's
      child-note resolution and violate the SR-PLAN-1 bare-ID convention.

Usage (programmatic):
    from research_vault.plan.check import check_plan
    violations = check_plan(plan_note_path)
    # violations is [] on pass; list of strings on fail.

Usage (CLI): ``rv plan check <plan-note-path>``

note.py-FREE (§5K.10): this module reads note files by path; it does NOT call
note.py functions and does NOT modify note.py.

Stdlib only.
"""
from __future__ import annotations

import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return (fields_dict, body) from a YAML-frontmatter markdown file.

    Matches the note.py contract: key = ``^(\\w[\\w_-]*):`` regex.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_block = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    fields: dict[str, str] = {}
    for line in fm_block.splitlines():
        m = re.match(r"^(\w[\w_-]*):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val.startswith(("'", '"')) and val.endswith(val[0]):
                val = val[1:-1]
            fields[key] = val
    return fields, body


def _parse_covers(covers_str: str) -> list[str]:
    """Parse a flat YAML list string like '[a, b, c]' into a Python list."""
    s = covers_str.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    return [item.strip() for item in s.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Rule (a): branch-presence in diagnosis tables
# ---------------------------------------------------------------------------

_EMPTY_CELL_RE = re.compile(r"^\s*$")
_FALLBACK_RE = re.compile(r"\bfallback\b", re.IGNORECASE)
_TBD_RE = re.compile(r"\bTBD\b", re.IGNORECASE)

def _check_diagnosis_tables(body: str, source: str) -> list[str]:
    """Scan *body* for markdown tables and check every data row for completeness.

    A table is a sequence of lines starting with '|'.
    The header row and separator row are skipped.
    Data rows must have:
      - No empty cells (after stripping whitespace and '|')
      - No cell containing only "fallback" (case-insensitive)
      - No cell containing "TBD"

    Returns a list of violation strings.
    """
    violations: list[str] = []
    lines = body.splitlines()
    in_table = False
    header_seen = False
    separator_seen = False
    row_index = 0

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("|"):
            if not in_table:
                in_table = True
                header_seen = False
                separator_seen = False
                row_index = 0

            # Skip header (first row of a new table)
            if not header_seen:
                header_seen = True
                continue

            # Skip separator row (---/===)
            if header_seen and not separator_seen:
                if re.match(r"^\|[\s\-:|]+\|", stripped):
                    separator_seen = True
                    continue

            # Data row — check cells.
            # Split on '|' and strip only the leading/trailing split artefacts
            # (the empty strings before the first '|' and after the last '|').
            # Do NOT pre-strip cell content before the empty-cell check — a cell
            # consisting only of whitespace is an empty cell and must be flagged.
            raw_cells = stripped.split("|")
            # Remove the leading '' before the first '|'
            if raw_cells and raw_cells[0] == "":
                raw_cells = raw_cells[1:]
            # Remove the trailing '' after the last '|'
            if raw_cells and raw_cells[-1] == "":
                raw_cells = raw_cells[:-1]
            cells = raw_cells

            row_index += 1
            for col_idx, cell in enumerate(cells, 1):
                cell_stripped = cell.strip()
                if _EMPTY_CELL_RE.match(cell_stripped):
                    violations.append(
                        f"{source}: diagnosis table row {row_index} col {col_idx} "
                        f"is empty (line {lineno}) — every outcome must have a "
                        f"named conclusion and committed action."
                    )
                elif _FALLBACK_RE.search(cell_stripped):
                    violations.append(
                        f"{source}: diagnosis table row {row_index} col {col_idx} "
                        f"contains 'fallback' (line {lineno}) — no fallback rows; "
                        f"name a specific conclusion and action for this outcome."
                    )
                elif _TBD_RE.search(cell_stripped):
                    violations.append(
                        f"{source}: diagnosis table row {row_index} col {col_idx} "
                        f"contains 'TBD' (line {lineno}) — pre-registration requires "
                        f"committed conclusions and actions, not placeholders."
                    )
        else:
            in_table = False
            header_seen = False
            separator_seen = False
            row_index = 0

    return violations


# ---------------------------------------------------------------------------
# Rule (b): one-component-per-ablation
# ---------------------------------------------------------------------------

def _check_one_component(body: str, source: str) -> list[str]:
    """Scan *body* for 'Component manipulated:' lines.

    Flags any line where the value contains ' and ' or is a comma-separated list
    with 2 or more items (multi-component ablation).

    Returns a list of violation strings.
    """
    violations: list[str] = []
    pattern = re.compile(r"(?i)component(?:s)?\s+manipulated\s*:\s*(.+)")

    for lineno, line in enumerate(body.splitlines(), 1):
        m = pattern.search(line)
        if not m:
            continue
        value = m.group(1).strip()
        # Multi-component if: contains " and " or comma-separated 2+ items
        has_and = bool(re.search(r"\band\b", value, re.IGNORECASE))
        comma_items = [v.strip() for v in value.split(",") if v.strip()]
        if has_and or len(comma_items) >= 2:
            violations.append(
                f"{source}: 'Component manipulated: {value}' on line {lineno} "
                f"lists multiple components — each supporting ablation must isolate "
                f"EXACTLY ONE component (§5K.4, 5K.5.5)."
            )

    return violations


# ---------------------------------------------------------------------------
# Rule (c): covers: bare-id convention (SR-PLAN-2)
# ---------------------------------------------------------------------------

def _check_covers_ids(fields: dict[str, str], source: str) -> list[str]:
    """Validate that every entry in covers: is a bare experiment ID.

    The SR-PLAN-1 convention (grounded in the demo plan + freeze.py's child-note
    resolution) is: covers: entries are BARE IDs such as "q1-main1",
    NOT path-prefixed like "experiments/q1-main1".

    freeze.py resolves children as ``notes_root / f"{child_id}.md"`` where
    notes_root is the experiments directory.  A path-prefixed entry would look
    for ``notes_root / "experiments" / "q1-main1.md"`` — one directory too deep.

    Returns a list of violation strings.
    """
    covers_str = fields.get("covers", "")
    if not covers_str:
        return []

    child_ids = _parse_covers(covers_str)
    violations: list[str] = []
    for child_id in child_ids:
        if "/" in child_id:
            violations.append(
                f"{source}: covers: entry {child_id!r} is path-prefixed — "
                f"use a bare ID (e.g. {child_id.split('/')[-1]!r}) instead.  "
                f"freeze.py resolves children as <notes_root>/<id>.md where "
                f"notes_root is the experiments directory (SR-PLAN-1 convention)."
            )
    return violations


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class PlanCheckError(ValueError):
    """Raised when the plan note file cannot be read or parsed."""


def check_plan(plan_note_path: Path) -> list[str]:
    """Run K-2 shape-lint on the plan master note at *plan_note_path*.

    Checks:
      (a) Branch-presence: every diagnosis table has named conclusion + action for
          every outcome row (no empty cells, no 'fallback', no 'TBD').
      (b) One-component-per-ablation: 'Component manipulated:' lines must not
          list multiple components (' and ' or comma-separated 2+ items).

    Args:
        plan_note_path: absolute or relative path to the plan master note
                        (the ``experiments/<id>-plan.md`` file with
                        ``plan_kind: preregistration`` in its frontmatter).

    Returns:
        list of violation strings.  Empty list = clean.

    Raises:
        PlanCheckError if the file cannot be read.
        PlanCheckError if the file does not have ``plan_kind: preregistration``
        in its frontmatter (wrong file passed by mistake).
    """
    p = Path(plan_note_path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise PlanCheckError(f"Cannot read plan note {p}: {e}") from e

    fields, body = _parse_frontmatter(text)

    if fields.get("plan_kind") != "preregistration":
        raise PlanCheckError(
            f"{p}: not a preregistration plan note "
            f"(plan_kind = {fields.get('plan_kind')!r}, expected 'preregistration'). "
            f"Pass the plan master note (experiments/<id>-plan.md)."
        )

    source = str(p)
    violations: list[str] = []

    # Rule (a): diagnosis table branch-presence
    violations.extend(_check_diagnosis_tables(body, source))

    # Rule (b): one-component-per-ablation
    violations.extend(_check_one_component(body, source))

    # Rule (c): covers: bare-id convention (SR-PLAN-2)
    violations.extend(_check_covers_ids(fields, source))

    return violations
