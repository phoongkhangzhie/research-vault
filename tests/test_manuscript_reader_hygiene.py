"""test_manuscript_reader_hygiene.py — RD-5: the reader-hygiene leak-gate.

Wave B (presentation), next-gen lit-review design §6, RD-5: HR's most
transferable mechanic, rv's biggest gap. A mechanical, deterministic,
fail-closed BLOCK over the assembled reader body — pipeline vocabulary
(`CP1`, `Q3`, `sha256:...`, artifact filenames, tool/loop tokens) must never
leak into reader-facing prose.

sr: NG-lit-review-waveB (RD-5)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.manuscript.check_gates import check_reader_hygiene


def test_clean_body_passes():
    body = (
        "The field has converged on three dominant training regimes. "
        "Smith and Jones both target sample efficiency, but Smith's "
        "curriculum outperforms Jones's on the harder regime because it "
        "adapts batch composition."
    )
    result = check_reader_hygiene(body)
    assert result["ok"] is True
    assert result["errors"] == []


def test_planted_counter_position_handle_blocks():
    """The exact planted-leak proof: a CP3 handle in reader prose -> BLOCK."""
    body = "This survey defends CP3 against the alternative framing."
    result = check_reader_hygiene(body)
    assert result["ok"] is False
    assert any("CP3" in e for e in result["errors"])


def test_planted_question_handle_blocks():
    body = "As established under Q7, the mechanism generalizes."
    result = check_reader_hygiene(body)
    assert result["ok"] is False
    assert any("Q7" in e for e in result["errors"])


def test_sha256_hash_blocks():
    body = "The frozen corpus hash is sha256:abc123def456 for this survey."
    result = check_reader_hygiene(body)
    assert result["ok"] is False
    assert any("sha256:" in e for e in result["errors"])


def test_artifact_filename_blocks():
    body = "See the notes recorded in _saturation.md for how we stopped."
    result = check_reader_hygiene(body)
    assert result["ok"] is False
    assert any("_saturation.md" in e for e in result["errors"])


def test_tool_token_blocks():
    body = "We ran review-snowball until the corpus saturated."
    result = check_reader_hygiene(body)
    assert result["ok"] is False
    assert any("review-snowball" in e for e in result["errors"])


def test_rv_command_token_blocks():
    body = "Running rv research find surfaced the missing seed."
    result = check_reader_hygiene(body)
    assert result["ok"] is False
    assert any("rv research" in e for e in result["errors"])


def test_multiple_hits_all_surfaced_not_first_only():
    """Every hit must surface — never truncate to the first match (charter §2)."""
    body = "CP1 and CP2 both fail Q4; see _corpus.md and sha256:deadbeef."
    result = check_reader_hygiene(body)
    assert result["ok"] is False
    joined = " ".join(result["errors"])
    assert "CP1" in joined and "CP2" in joined
    assert "Q4" in joined
    assert "_corpus.md" in joined
    assert "sha256:" in joined


def test_false_positive_guard_normal_prose_with_letter_q_or_p_not_flagged():
    """A bare 'Q' or 'CP' substring in ordinary prose must NOT false-positive —
    the regex requires a digit immediately after (\\bCP\\d+\\b / \\bQ\\d+\\b)."""
    body = (
        "The Q&A session raised concerns about CParser's output format. "
        "Quarter-over-quarter growth was steady."
    )
    result = check_reader_hygiene(body)
    assert result["ok"] is True
    assert result["errors"] == []


def test_empty_body_is_a_clean_noop():
    result = check_reader_hygiene("")
    assert result["ok"] is True
    assert result["errors"] == []
