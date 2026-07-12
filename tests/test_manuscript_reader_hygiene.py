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


# ---------------------------------------------------------------------------
# PR-C — the three verified-missing leak classes (hub bake-off, 0.2.6
# manuscript: 5/25 leak-lines caught). Each gets its own planted-leak RED
# proof — silence is not proof (charter §2).
# ---------------------------------------------------------------------------

def test_planted_edge_tag_blocks():
    """A literal bracketed epistemic edge tag in reader prose -> BLOCK."""
    body = "This paper [SUPPORTS] the claim that fidelity is unproven."
    result = check_reader_hygiene(body)
    assert result["ok"] is False
    assert any("[SUPPORTS]" in e for e in result["errors"])


def test_planted_edge_tag_contradicts_blocks():
    body = "The finding [CONTRADICTS] the earlier survey's framing."
    result = check_reader_hygiene(body)
    assert result["ok"] is False
    assert any("[CONTRADICTS]" in e for e in result["errors"])


def test_planted_edge_tag_partial_blocks():
    body = "This note [PARTIAL] matches the claimed effect."
    result = check_reader_hygiene(body)
    assert result["ok"] is False
    assert any("[PARTIAL]" in e for e in result["errors"])


def test_planted_edge_tag_extends_blocks():
    body = "The follow-up work [EXTENDS] the original finding."
    result = check_reader_hygiene(body)
    assert result["ok"] is False
    assert any("[EXTENDS]" in e for e in result["errors"])


# -- OKF-conformant prose-token edge grammar (type moved out of the
# -- link-prefix tag, into a trailing prose token) — the leak gate must
# -- catch a leaked edge line in EITHER note vintage.

def test_planted_prose_token_edge_supports_blocks():
    body = "[Baltaji 2024](/literature/baltaji2024.md) — SUPPORTS: the claim."
    result = check_reader_hygiene(body)
    assert result["ok"] is False
    assert any("SUPPORTS:" in e for e in result["errors"])


def test_planted_prose_token_edge_contradicts_blocks():
    body = "As the corpus notes, — CONTRADICTS: the earlier survey's framing."
    result = check_reader_hygiene(body)
    assert result["ok"] is False
    assert any("CONTRADICTS:" in e for e in result["errors"])


def test_planted_prose_token_edge_partial_blocks():
    body = "This note — PARTIAL: matches the claimed effect."
    result = check_reader_hygiene(body)
    assert result["ok"] is False
    assert any("PARTIAL:" in e for e in result["errors"])


def test_planted_prose_token_edge_extends_blocks():
    body = "The follow-up work — EXTENDS: the original finding."
    result = check_reader_hygiene(body)
    assert result["ok"] is False
    assert any("EXTENDS:" in e for e in result["errors"])


def test_planted_okf_path_fragment_blocks():
    """A note-path fragment like 'concepts/foo.md' -> BLOCK, a NEW marker
    since the existing _LEAK_ARTIFACT_FILENAME_RE only matches
    underscore-prefixed control filenames, not OKF note paths."""
    body = "See concepts/cultural-fidelity.md for the definition."
    result = check_reader_hygiene(body)
    assert result["ok"] is False
    assert any("concepts/cultural-fidelity.md" in e for e in result["errors"])


def test_planted_okf_path_fragment_literature_blocks():
    body = "The claim traces to literature/smith2024, per the corpus."
    result = check_reader_hygiene(body)
    assert result["ok"] is False
    assert any("literature/smith2024" in e for e in result["errors"])


def test_planted_okf_path_fragment_mocs_gaps_reviews_blocks():
    body = "Cross-referenced against mocs/index.md, gaps/g1.md, reviews/scope.md."
    result = check_reader_hygiene(body)
    assert result["ok"] is False
    joined = " ".join(result["errors"])
    assert "mocs/index.md" in joined
    assert "gaps/g1.md" in joined
    assert "reviews/scope.md" in joined


def test_false_positive_guard_bare_okf_word_not_flagged():
    """The bare word ('literature', 'reviews', ...) without a trailing
    slash-path-fragment must NOT trip the marker — only the path shape does."""
    body = (
        "The prior behavioural-economics literature does not supply a "
        "baseline. Peer reviews of this manuscript were positive. This "
        "survey covers concepts central to the field, with clear gaps "
        "left for future mocs of the corpus."
    )
    result = check_reader_hygiene(body)
    assert result["ok"] is True
    assert result["errors"] == []


def test_planted_source_notes_label_blocks():
    body = "Source notes: the corpus was screened by title and abstract."
    result = check_reader_hygiene(body)
    assert result["ok"] is False
    assert any("source notes:" in e.lower() for e in result["errors"])


def test_false_positive_guard_dollar_math_and_gold_prose_pass():
    """The gold report's inline math + legitimate prose must not false-positive."""
    body = (
        r"steering along one WVS axis reliably drags the orthogonal axis "
        r"with it, an entanglement quantified by "
        r"$$ E = \frac{|\Delta d_{\text{unintended}}|}{|\Delta d_{\text{intended}}|} $$ "
        r"where $E=0$ is perfect axis orthogonality."
    )
    result = check_reader_hygiene(body)
    assert result["ok"] is True
    assert result["errors"] == []


def test_multiple_hits_all_surfaced_across_new_classes():
    """Every hit surfaces across ALL three new classes together, never
    truncated to the first match (charter §2)."""
    body = (
        "This work [SUPPORTS] the claim, per concepts/fidelity.md. "
        "Source notes: see literature/smith2024 for the raw coding."
    )
    result = check_reader_hygiene(body)
    assert result["ok"] is False
    joined = " ".join(result["errors"])
    assert "[SUPPORTS]" in joined
    assert "concepts/fidelity.md" in joined
    assert "source notes:" in joined.lower()
    assert "literature/smith2024" in joined
