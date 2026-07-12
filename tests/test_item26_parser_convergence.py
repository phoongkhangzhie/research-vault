"""test_item26_parser_convergence.py — #26 parser convergence: note._parse_frontmatter + gap_scan.

Tests that:
1. note._parse_frontmatter handles YAML list values (  - item syntax) — the extension.
2. Scalar callers are unaffected (their fields still return str).
3. gap_scan no longer defines its own _parse_frontmatter_gap (duplicate removed).
4. The gap detectors (contradictory, knowledge_void) still correctly parse list-valued
   fields (backed_by, supported_by, contradicted_by) via the canonical parser.

TDD: tests written BEFORE the fix.  The first two groups (list-extension, gap-no-duplicate)
are RED on the current code.  Scalar-caller tests and end-to-end detector tests should
already be GREEN (confirming existing callers are unaffected by the extension).

Stdlib only.  Hermetic (no live note files needed for parser unit tests).
sr: #26
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# 1. note._parse_frontmatter: YAML list extension
# ---------------------------------------------------------------------------

def test_parse_frontmatter_returns_list_for_yaml_list_field():
    """note._parse_frontmatter returns a list[str] for '  - item' formatted fields.

    Currently RED: _parse_frontmatter is scalar-only and returns '' for list-valued keys.
    After the fix it must return ['lit-A', 'lit-B'] for the backed_by field.
    """
    from research_vault.note import _parse_frontmatter

    text = """\
---
type: findings
id: f-001
claim: "LLMs underperform on cross-lingual tasks"
backed_by:
  - lit-A
  - lit-B
status: open
---
# Finding body
"""
    fields, body = _parse_frontmatter(text)
    backed_by = fields.get("backed_by")
    assert isinstance(backed_by, list), (
        f"Expected list for backed_by, got {type(backed_by).__name__}: {backed_by!r}. "
        "note._parse_frontmatter must handle YAML '  - item' list syntax after #26."
    )
    assert backed_by == ["lit-A", "lit-B"], (
        f"Expected ['lit-A', 'lit-B'], got {backed_by!r}"
    )


def test_parse_frontmatter_supported_by_and_contradicted_by():
    """note._parse_frontmatter returns lists for supported_by and contradicted_by.

    These are the gap-scan concept fields that require list parsing (#26).
    """
    from research_vault.note import _parse_frontmatter

    text = """\
---
type: concepts
id: c-contested
label: c-contested
supported_by:
  - smith2024
  - jones2023
contradicted_by:
  - rebuttal2024
---
# Concept body
"""
    fields, _ = _parse_frontmatter(text)
    assert fields.get("supported_by") == ["smith2024", "jones2023"]
    assert fields.get("contradicted_by") == ["rebuttal2024"]


def test_parse_frontmatter_strips_quotes_from_plain_scalar_list_items():
    """note._parse_frontmatter strips surrounding quotes on '  - item' entries.

    The two sibling paths (inline scalar; mapping-list-item) already strip
    surrounding quote chars. The plain scalar-list-item path did not, so a
    frozen `branches:` block list with quoted items parsed the literal quote
    characters into the value — breaking any downstream case-folded substring
    match (e.g. the outline gate) against an unquoted heading.
    """
    from research_vault.note import _parse_frontmatter

    text = """\
---
type: manuscript
id: ms-002
branches:
  - "survey to behaviour"
  - plain
---
# Manuscript body
"""
    fields, _ = _parse_frontmatter(text)
    branches = fields.get("branches")
    assert branches == ["survey to behaviour", "plain"], (
        f"Expected quotes stripped from list items, got {branches!r}"
    )


def test_parse_frontmatter_empty_list_field():
    """note._parse_frontmatter returns [] for a key with empty value and no items."""
    from research_vault.note import _parse_frontmatter

    text = """\
---
type: findings
id: f-empty
claim: "claim"
backed_by:
status: open
---
"""
    fields, _ = _parse_frontmatter(text)
    # A key: with no value AND no  - items → empty string (lazy-promote: only
    # becomes [] when actual list items follow).  Backwards-compat: callers do
    # .strip() on empty-valued keys and must not get AttributeError.
    val = fields.get("backed_by")
    assert val == "" or val == [], (
        f"Empty key with no list items must be '' or [] (falsy, .strip()-safe if str); "
        f"got {val!r}"
    )
    # Confirm no crash from .strip() on the result of a typical caller pattern
    assert fields.get("backed_by", "").strip() == "" or isinstance(fields.get("backed_by"), list)


# ---------------------------------------------------------------------------
# 2. Scalar callers unaffected (existing behaviour preserved)
# ---------------------------------------------------------------------------

def test_parse_frontmatter_scalar_fields_unaffected():
    """Scalar fields still return str after the list extension (backwards-compat test).

    Simulates the check_gates / review/__init__ pattern: .get(key, "").strip()
    on fields that are not list-formatted — must not break.
    """
    from research_vault.note import _parse_frontmatter

    text = """\
---
type: manuscript
id: ms-001
synthesized_okf: findings/f-001, findings/f-002
plan_kind: preregistration
confidence: high
title: Cross-lingual benchmark study
---
"""
    fields, _ = _parse_frontmatter(text)
    # All these must remain str and .strip() must work
    assert fields.get("synthesized_okf", "").strip() == "findings/f-001, findings/f-002"
    assert fields.get("plan_kind", "").strip().lower() == "preregistration"
    assert fields.get("confidence", "").strip().lower() == "high"
    assert fields.get("title", "").strip() == "Cross-lingual benchmark study"


def test_parse_frontmatter_covers_inline_still_scalar():
    """covers: [] (inline syntax) must remain a string, not a list.

    The plan check (check_gates.py line 737) does covers_raw.split(',') on this value.
    Extending _parse_frontmatter for '  - item' syntax must NOT change inline [] values.
    """
    from research_vault.note import _parse_frontmatter

    text = """\
---
type: experiments
plan_kind: preregistration
covers: []
---
"""
    fields, _ = _parse_frontmatter(text)
    covers = fields.get("covers", "")
    # Must be a string (inline syntax is not  - item YAML list)
    assert isinstance(covers, str), (
        f"covers: [] (inline) must remain str, got {type(covers).__name__}: {covers!r}"
    )


# ---------------------------------------------------------------------------
# 3. Duplicate removed from gap_scan (the convergence invariant)
# ---------------------------------------------------------------------------

def test_parse_frontmatter_gap_removed_from_gap_scan():
    """_parse_frontmatter_gap must NOT be defined in gap_scan after convergence.

    This is the structural invariant for #26: the duplicate local parser is the
    reuse debt; once note._parse_frontmatter handles lists, the fork is deleted.
    RED on current code (gap_scan still has _parse_frontmatter_gap defined).
    """
    import research_vault.review.gap_scan as gs
    assert not hasattr(gs, "_parse_frontmatter_gap"), (
        "_parse_frontmatter_gap is still defined in gap_scan — duplicate not removed yet. "
        "After #26 convergence, gap_scan must use note._parse_frontmatter directly."
    )


# ---------------------------------------------------------------------------
# 4. End-to-end: detectors still work via canonical parser (gap_scan)
# ---------------------------------------------------------------------------

def test_contradictory_detector_still_parses_lists(tmp_instance):
    """_detect_contradictory correctly parses supported_by/contradicted_by lists after #26.

    Structural regression: verifies the canonical parser correctly surfaces both edges
    when gap_scan uses note._parse_frontmatter instead of its local fork.
    """
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan, GAP_TYPE_CONTRADICTORY

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")

    # Write a concept note with YAML list fields
    cd = pnd / "concepts"
    cd.mkdir(parents=True, exist_ok=True)
    (cd / "c-list-contested.md").write_text(
        "---\ntype: concepts\nid: c-list-contested\nlabel: c-list-contested\n"
        "supported_by:\n  - smith2024\n  - jones2023\n"
        "contradicted_by:\n  - rebuttal2024\n---\n# Concept\n",
        encoding="utf-8",
    )

    new_gaps = cmd_gap_scan("demo-research", config=cfg)
    assert len(new_gaps) == 1, f"Expected 1 gap (contradictory), got {len(new_gaps)}"
    assert new_gaps[0].type == GAP_TYPE_CONTRADICTORY


def test_knowledge_void_detector_still_parses_backed_by_list(tmp_instance):
    """_detect_knowledge_void correctly reads backed_by as a list after #26.

    A finding with backed_by: [lit-A] (list syntax) should count as 1 backing → above
    threshold → NOT a knowledge void.  Verifies the list-count logic works end-to-end.
    """
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")

    fd = pnd / "findings"
    fd.mkdir(parents=True, exist_ok=True)
    # backed_by has 1 item → threshold=1 → NOT a void
    (fd / "f-backed-list.md").write_text(
        "---\ntype: findings\nid: f-backed-list\nclaim: \"Has backing\"\n"
        "backed_by:\n  - lit-A\n---\n# Finding\n",
        encoding="utf-8",
    )

    new_gaps = cmd_gap_scan("demo-research", config=cfg)
    # Should be 0 gaps (backed_by count == threshold, so no void)
    assert len(new_gaps) == 0, (
        f"A finding with backed_by: [lit-A] (count=1) must NOT be a knowledge_void "
        f"(threshold=1). Got {len(new_gaps)} gaps. The list parser may not be counting correctly."
    )
