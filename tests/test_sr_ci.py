"""test_sr_ci.py — acceptance tests for SR-CI (gate-clean verdict header by construction).

TOOL-D3: ``rv control return`` emits a bracketed gate-clean verdict header as the
**first line of the block body** when the verdict field is ``[PASS]`` or ``[BLOCK]``.

The bracket delimiter is the key property: the approve-gate matches only ``[PASS]`` /
``[BLOCK]``; a bare "PASS", "BLOCK", or "FAIL" anywhere in the narrative fields cannot
false-match, because the gate pattern requires the brackets.

All tests are hermetic: tmp_path only, no ~/vault reads or writes, no gh/network calls.

Test map:
  1. [PASS] verdict → VERDICT: [PASS] as first block line
  2. [BLOCK] verdict → VERDICT: [BLOCK] as first block line
  3. Key acceptance: verdict:[PASS] + BLOCK/FAIL in narrative → header reads [PASS] cleanly
  4. _extract_gate_verdict: bare PASS/BLOCK/FAIL does NOT match (prose decoupling proof)
  5. No verdict field → no VERDICT: header
  6. Non-gate-vocab verdict (e.g. "approve") → no header (backward compat)
  7. Parsed block: verdict field readable as "[PASS]" after header emission
  8. All RETURN_REQUIRED fields present after header emission
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
# Fixtures
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
# Test 1: [PASS] verdict → VERDICT: [PASS] as first block line
# ---------------------------------------------------------------------------

def test_pass_verdict_emits_bracketed_gate_header(cfg, ctl_file):
    """cmd_return_entry with verdict:[PASS] emits 'VERDICT: [PASS]' as the first block line."""
    fields = {**_BASE_FIELDS, "verdict": "[PASS]"}
    control_mod.cmd_return_entry("demo-research", fields=fields, config=cfg)
    text = ctl_file.read_text(encoding="utf-8")

    first_line = _first_block_line_after_marker(text)
    assert first_line == "VERDICT: [PASS]", (
        f"Expected first block line to be 'VERDICT: [PASS]', got {first_line!r}\n\n{text}"
    )


# ---------------------------------------------------------------------------
# Test 2: [BLOCK] verdict → VERDICT: [BLOCK] as first block line
# ---------------------------------------------------------------------------

def test_block_verdict_emits_bracketed_gate_header(cfg, ctl_file):
    """cmd_return_entry with verdict:[BLOCK] emits 'VERDICT: [BLOCK]' as the first block line."""
    fields = {**_BASE_FIELDS, "verdict": "[BLOCK]"}
    control_mod.cmd_return_entry("demo-research", fields=fields, config=cfg)
    text = ctl_file.read_text(encoding="utf-8")

    first_line = _first_block_line_after_marker(text)
    assert first_line == "VERDICT: [BLOCK]", (
        f"Expected first block line to be 'VERDICT: [BLOCK]', got {first_line!r}\n\n{text}"
    )


# ---------------------------------------------------------------------------
# Test 3: Key acceptance test — [PASS] verdict + BLOCK/FAIL narrative → header reads [PASS]
# ---------------------------------------------------------------------------

def test_block_fail_quoting_narrative_yields_clean_pass_header(cfg, ctl_file):
    """Spec acceptance test: narrative quoting BLOCK/FAIL + verdict:[PASS] → VERDICT: [PASS].

    The narrative fields explicitly contain bare "BLOCK" and "FAIL" (red-before-green proof).
    The approve-gate pattern matches ``[PASS]`` (bracketed); bare BLOCK/FAIL in prose cannot
    false-trip it — the bracket is the decoupler.
    """
    fields = {
        "did": "fixed the gate — it was BLOCK before, now green",
        "outcome": "Red → green: tests were FAIL before my fix. All now PASS.",
        "confidence": "high — previously FAIL state documented in CI run abc",
        "next": "merge — no remaining BLOCK conditions",
        "provenance": "sha:ghi789",
        "retro": "Had to diagnose BLOCK state → added instrumentation, now resolved",
        "verdict": "[PASS]",
    }
    control_mod.cmd_return_entry("demo-research", fields=fields, config=cfg)
    text = ctl_file.read_text(encoding="utf-8")

    # Header must be [PASS]
    first_line = _first_block_line_after_marker(text)
    assert first_line == "VERDICT: [PASS]", (
        f"Expected 'VERDICT: [PASS]' header even with bare BLOCK/FAIL in narrative, "
        f"got {first_line!r}\n\n{text}"
    )

    # The narrative still contains the bare BLOCK/FAIL words — not stripped
    assert "BLOCK" in text, "Expected bare 'BLOCK' to appear in the narrative body"
    assert "FAIL" in text, "Expected bare 'FAIL' to appear in the narrative body"

    # The gate pattern (bracketed) matches exactly [PASS], NOT the bare prose words
    bracketed_matches = re.findall(r"\[(PASS|BLOCK)\]", text)
    assert bracketed_matches == ["PASS"], (
        f"Expected exactly one bracketed gate token [PASS] in text, found {bracketed_matches}"
    )


# ---------------------------------------------------------------------------
# Test 4: _extract_gate_verdict — bare words do NOT match (decoupling proof)
# ---------------------------------------------------------------------------

class TestExtractGateVerdict:
    """Unit tests for _extract_gate_verdict — the bracketed-token parser."""

    def test_bracketed_pass_matches(self):
        from research_vault.control import _extract_gate_verdict
        assert _extract_gate_verdict("[PASS]") == "PASS"

    def test_bracketed_block_matches(self):
        from research_vault.control import _extract_gate_verdict
        assert _extract_gate_verdict("[BLOCK]") == "BLOCK"

    def test_lowercase_bracketed_matches(self):
        from research_vault.control import _extract_gate_verdict
        assert _extract_gate_verdict("[pass]") == "PASS"
        assert _extract_gate_verdict("[block]") == "BLOCK"

    def test_bare_pass_does_not_match(self):
        """A bare 'PASS' without brackets must NOT match — prose decoupling."""
        from research_vault.control import _extract_gate_verdict
        assert _extract_gate_verdict("PASS") is None

    def test_bare_block_does_not_match(self):
        """A bare 'BLOCK' without brackets must NOT match — prose decoupling."""
        from research_vault.control import _extract_gate_verdict
        assert _extract_gate_verdict("BLOCK") is None

    def test_bare_fail_does_not_match(self):
        """A bare 'FAIL' must NOT match — same prose decoupling guarantee."""
        from research_vault.control import _extract_gate_verdict
        assert _extract_gate_verdict("FAIL") is None

    def test_approve_does_not_match(self):
        """Old-style 'approve' verdict value does NOT match — backward compat."""
        from research_vault.control import _extract_gate_verdict
        assert _extract_gate_verdict("approve") is None

    def test_empty_does_not_match(self):
        from research_vault.control import _extract_gate_verdict
        assert _extract_gate_verdict("") is None

    def test_pass_with_trailing_prose_does_not_match(self):
        """'[PASS] — reviewer approved' does NOT match (full-value match required)."""
        from research_vault.control import _extract_gate_verdict
        assert _extract_gate_verdict("[PASS] — reviewer approved") is None


# ---------------------------------------------------------------------------
# Test 5: No verdict field → no VERDICT: header
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
# Test 6: Non-gate-vocab verdict → no header (backward compat)
# ---------------------------------------------------------------------------

def test_non_gate_vocab_verdict_emits_no_header(cfg, ctl_file):
    """A verdict value that is not a bracketed gate token does not emit the header.

    Backward compat: 'verdict: approve' (old usage) must not break.
    """
    fields = {**_BASE_FIELDS, "verdict": "approve"}
    control_mod.cmd_return_entry("demo-research", fields=fields, config=cfg)
    text = ctl_file.read_text(encoding="utf-8")

    first_line = _first_block_line_after_marker(text)
    assert first_line is None or not first_line.startswith("VERDICT:"), (
        f"Expected no VERDICT: header for non-gate-vocab verdict, "
        f"but first block line is {first_line!r}"
    )
    # The verdict value is still written to the block body
    assert "approve" in text


# ---------------------------------------------------------------------------
# Test 7: Parsed block — verdict field reads "[PASS]" after bracketed header
# ---------------------------------------------------------------------------

def test_parsed_block_verdict_field_readable_after_header(cfg, ctl_file):
    """After bracket-header emission, the verdict field parses as '[PASS]'.

    The controllib parser reads the unindented 'VERDICT: [PASS]' line via the
    known-key path (verdict is a known key) and stores the value '[PASS]'.
    """
    fields = {**_BASE_FIELDS, "verdict": "[PASS]"}
    control_mod.cmd_return_entry("demo-research", fields=fields, config=cfg)

    cf = cl.parse_control_file(ctl_file)
    return_blocks = [b for b in cf.blocks if b.kind == "RETURN"]
    assert return_blocks, "Expected at least one RETURN block in control file"

    blk = return_blocks[-1]
    assert blk.fields.get("verdict") == "[PASS]", (
        f"Expected parsed verdict='[PASS]' but got {blk.fields.get('verdict')!r}.\n"
        f"Full parsed fields: {blk.fields}"
    )


# ---------------------------------------------------------------------------
# Test 8: All RETURN_REQUIRED fields present after header emission
# ---------------------------------------------------------------------------

def test_required_fields_present_after_header_emission(cfg, ctl_file):
    """All RETURN_REQUIRED fields are still written when a gate-clean header is emitted."""
    from research_vault.controllib import RETURN_REQUIRED
    fields = {**_BASE_FIELDS, "verdict": "[PASS]"}
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
