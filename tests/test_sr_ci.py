"""test_sr_ci.py — acceptance tests for SR-CI (gate-clean verdict header by construction).

TOOL-D3: ``rv control return`` emits a negation-free PASS/BLOCK verdict header as the
**first line of the block body** when the verdict field is PASS or BLOCK.  A narrative
that quotes "BLOCK" or "FAIL" (e.g. describing a red-before-green proof) still produces a
machine-readable header the approve-gate can read as PASS/BLOCK without tripping on the
negative words in the body.

All tests are hermetic: tmp_path only, no ~/vault reads or writes, no gh/network calls.

Test map:
  1. PASS verdict → VERDICT: PASS first line
  2. BLOCK verdict → VERDICT: BLOCK first line
  3. BLOCK/FAIL-quoting narrative + verdict:PASS → VERDICT: PASS header (key acceptance test)
  4. No verdict field → no VERDICT: header
  5. Non-PASS/BLOCK verdict (e.g. "approve") → no VERDICT: header (backward compat)
  6. VERDICT: header still parseable by controllib block parser
  7. CLI path: ``rv control return`` emits the header (smoke test via CLI)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import load_config
from research_vault import control as control_mod
from research_vault import controllib as cl


# ---------------------------------------------------------------------------
# Fixtures (reuse the shared tmp_instance + cfg pattern from test_sr_cp.py)
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_instance):
    return load_config(reload=True)


@pytest.fixture
def ctl_file(cfg):
    """Fresh demo-research control file."""
    return control_mod.cmd_init("demo-research", config=cfg, overwrite=True)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_BASE_FIELDS = {
    "did": "built the feature",
    "outcome": "PR #10",
    "confidence": "high",
    "next": "merge",
    "provenance": "sha:abc123",
    "retro": "—",
}


def _first_block_line_after_marker(text: str) -> str | None:
    """Return the first content line after ⟦RETURN⟧ in *text*, or None."""
    m = re.search(r"⟦RETURN⟧\n(.+)", text)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Test 1: PASS verdict emits VERDICT: PASS as first block line
# ---------------------------------------------------------------------------

def test_pass_verdict_emits_gate_clean_header(cfg, ctl_file):
    """cmd_return_entry with verdict:PASS emits 'VERDICT: PASS' as the first block line."""
    fields = {**_BASE_FIELDS, "verdict": "PASS"}
    control_mod.cmd_return_entry("demo-research", fields=fields, config=cfg)
    text = ctl_file.read_text(encoding="utf-8")

    first_line = _first_block_line_after_marker(text)
    assert first_line == "VERDICT: PASS", (
        f"Expected first block line to be 'VERDICT: PASS', got {first_line!r}\n\n{text}"
    )


# ---------------------------------------------------------------------------
# Test 2: BLOCK verdict emits VERDICT: BLOCK as first block line
# ---------------------------------------------------------------------------

def test_block_verdict_emits_gate_clean_header(cfg, ctl_file):
    """cmd_return_entry with verdict:BLOCK emits 'VERDICT: BLOCK' as the first block line."""
    fields = {**_BASE_FIELDS, "verdict": "BLOCK"}
    control_mod.cmd_return_entry("demo-research", fields=fields, config=cfg)
    text = ctl_file.read_text(encoding="utf-8")

    first_line = _first_block_line_after_marker(text)
    assert first_line == "VERDICT: BLOCK", (
        f"Expected first block line to be 'VERDICT: BLOCK', got {first_line!r}\n\n{text}"
    )


# ---------------------------------------------------------------------------
# Test 3: BLOCK/FAIL-quoting narrative + verdict:PASS → PASS header  (KEY TEST)
# ---------------------------------------------------------------------------

def test_block_fail_quoting_narrative_yields_pass_header(cfg, ctl_file):
    """The spec acceptance test: a narrative quoting BLOCK/FAIL still yields a PASS header.

    Scenario: an agent reports a red-before-green proof — the narrative explicitly mentions
    FAIL and BLOCK states that existed before the fix.  The gate must read VERDICT: PASS from
    the header, not get tripped by the words in the body.
    """
    fields = {
        "did": "fixed the gate — it was BLOCK before, now green",
        "outcome": "Red → green: tests were FAIL before my fix. All now PASS.",
        "confidence": "high — previously FAIL state documented in CI run abc",
        "next": "merge — no remaining BLOCK conditions",
        "provenance": "sha:ghi789",
        "retro": "Had to diagnose BLOCK state → added instrumentation, now resolved",
        "verdict": "PASS",
    }
    control_mod.cmd_return_entry("demo-research", fields=fields, config=cfg)
    text = ctl_file.read_text(encoding="utf-8")

    # Header must be PASS
    first_line = _first_block_line_after_marker(text)
    assert first_line == "VERDICT: PASS", (
        f"Expected 'VERDICT: PASS' header even with BLOCK/FAIL in narrative, "
        f"got {first_line!r}\n\n{text}"
    )

    # The narrative still contains the BLOCK/FAIL words (not stripped)
    assert "BLOCK" in text, "Expected 'BLOCK' to appear in the narrative body"
    assert "FAIL" in text, "Expected 'FAIL' to appear in the narrative body"


# ---------------------------------------------------------------------------
# Test 4: No verdict field → no VERDICT: header
# ---------------------------------------------------------------------------

def test_no_verdict_field_emits_no_header(cfg, ctl_file):
    """Without a verdict field, no VERDICT: header is emitted."""
    control_mod.cmd_return_entry("demo-research", fields=dict(_BASE_FIELDS), config=cfg)
    text = ctl_file.read_text(encoding="utf-8")

    first_line = _first_block_line_after_marker(text)
    assert first_line is None or not first_line.startswith("VERDICT:"), (
        f"Expected no VERDICT: header, but first block line is {first_line!r}"
    )


# ---------------------------------------------------------------------------
# Test 5: Non-PASS/BLOCK verdict → no VERDICT: header (backward compat)
# ---------------------------------------------------------------------------

def test_non_gate_vocab_verdict_emits_no_header(cfg, ctl_file):
    """A verdict value that is not PASS or BLOCK does not emit the gate-clean header.

    Backward compat: old usage of 'verdict: approve' must not be broken.
    """
    fields = {**_BASE_FIELDS, "verdict": "approve"}
    control_mod.cmd_return_entry("demo-research", fields=fields, config=cfg)
    text = ctl_file.read_text(encoding="utf-8")

    first_line = _first_block_line_after_marker(text)
    assert first_line is None or not first_line.startswith("VERDICT:"), (
        f"Expected no VERDICT: header for non-gate-vocab verdict, "
        f"but first block line is {first_line!r}"
    )
    # The verdict value is still present in the block body
    assert "approve" in text


# ---------------------------------------------------------------------------
# Test 6: Parsed block — verdict field still readable after header emission
# ---------------------------------------------------------------------------

def test_parsed_block_verdict_field_readable_after_header(cfg, ctl_file):
    """After gate-clean header emission, the verdict field is still parseable.

    The parser must read 'verdict: PASS' from the header line so the field is
    accessible to tools that query parsed blocks.
    """
    fields = {**_BASE_FIELDS, "verdict": "PASS"}
    control_mod.cmd_return_entry("demo-research", fields=fields, config=cfg)

    cf = cl.parse_control_file(ctl_file)
    return_blocks = [b for b in cf.blocks if b.kind == "RETURN"]
    assert return_blocks, "Expected at least one RETURN block in control file"

    blk = return_blocks[-1]
    assert blk.fields.get("verdict") == "PASS", (
        f"Expected parsed verdict='PASS' but got {blk.fields.get('verdict')!r}.\n"
        f"Full parsed fields: {blk.fields}"
    )


# ---------------------------------------------------------------------------
# Test 7: Required fields still present after header emission
# ---------------------------------------------------------------------------

def test_required_fields_present_after_header_emission(cfg, ctl_file):
    """All RETURN_REQUIRED fields are still written when a gate-clean header is emitted."""
    from research_vault.controllib import RETURN_REQUIRED
    fields = {**_BASE_FIELDS, "verdict": "PASS"}
    control_mod.cmd_return_entry("demo-research", fields=fields, config=cfg)

    cf = cl.parse_control_file(ctl_file)
    return_blocks = [b for b in cf.blocks if b.kind == "RETURN"]
    assert return_blocks
    blk = return_blocks[-1]

    for required_field in RETURN_REQUIRED:
        assert required_field in blk.fields, (
            f"Required field {required_field!r} missing from parsed block after header emission.\n"
            f"Fields present: {list(blk.fields.keys())}"
        )
