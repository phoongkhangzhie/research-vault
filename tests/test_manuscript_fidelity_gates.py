"""test_manuscript_fidelity_gates.py — PR-M3: manuscript/fidelity_gates.py adapter.

Covers the manuscript-loop's thin adapter over the shared gates
(research_vault.gates.support_matcher / .coldread):

  1. check_support_tally: honest tally format, never says "verified".
  2. check_support_tally: blind-judge canary aborts loudly on a broken judge
     (rather than surfacing false-BLOCKs for every real citation).
  3. check_support_tally: sighted judge proceeds normally, no abort.
  4. check_cold_read_tally: honest tally + errors composed from a DANGLING result.
  5. check_cold_read_tally: canary_aborted surfaced when the judge is blind.
  6. check_cold_read_tally: graceful degrade when no PDF/text is available.

All hermetic (tmp_path, mock judge_fn). No live LLM calls.
sr: PR-M3
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


def _make_ms_tree(tmp_path: Path) -> Path:
    tree_root = tmp_path / "manuscripts" / "ms-test"
    (tree_root / "sections").mkdir(parents=True, exist_ok=True)
    return tree_root


def _literature_note(notes_root: Path, citekey: str, *, fields: dict | None = None) -> Path:
    lit_dir = notes_root / "literature"
    lit_dir.mkdir(parents=True, exist_ok=True)
    ffields = {"type": "literature", "tldr": "This paper demonstrates X.", "findings": "Finding A: X is true."}
    if fields:
        ffields.update(fields)
    fm = "---\n" + "\n".join(f"{k}: {v}" for k, v in ffields.items()) + "\n---\n"
    path = lit_dir / f"{citekey}.md"
    path.write_text(fm, encoding="utf-8")
    return path


# ===========================================================================
# check_support_tally
# ===========================================================================

class TestCheckSupportTally:
    def test_honest_report_format(self, tmp_path):
        from research_vault.manuscript.fidelity_gates import check_support_tally
        tree_root = _make_ms_tree(tmp_path)
        result = check_support_tally(tree_root, judge_fn=lambda p: "VERDICT: [SUPPORTS]\n")
        assert re.match(
            r"\d+ sentences, \d+ citations, \d+ BLOCK, \d+ WARN",
            result["honest_report"],
        )
        assert "verified" not in result["honest_report"].lower()

    def test_tally_counts_and_verdicts(self, tmp_path):
        from research_vault.manuscript.fidelity_gates import check_support_tally
        tree_root = _make_ms_tree(tmp_path)
        notes_root = tmp_path / "notes"
        _literature_note(notes_root, "smith2023")
        _literature_note(notes_root, "jones2024")
        (tree_root / "sections" / "results-discussion.tex").write_text(
            r"We found X \cite{smith2023}. Additionally Y \cite{jones2024}.",
            encoding="utf-8",
        )

        def _judge(prompt: str) -> str:
            if "smith2023" in prompt:
                return "VERDICT: [SUPPORTS]\nVERBATIM_SPAN: Finding A.\nREASONING: Backs claim.\n"
            return "VERDICT: [PARTIAL]\nVERBATIM_SPAN: Related but partial.\nREASONING: Overclaim.\n"

        result = check_support_tally(tree_root, notes_root=notes_root, judge_fn=_judge)
        assert result["m_citations"] >= 2
        assert result["j_warn"] >= 1
        assert not result["canary_aborted"]

    def test_blind_judge_canary_aborts(self, tmp_path):
        """A judge that always returns ABSENT — even on the known-positive canary
        probe — must abort the whole tally loudly, not emit false-BLOCKs."""
        from research_vault.manuscript.fidelity_gates import check_support_tally
        tree_root = _make_ms_tree(tmp_path)
        notes_root = tmp_path / "notes"
        _literature_note(notes_root, "smith2023")
        (tree_root / "sections" / "results.tex").write_text(
            r"We found that X is true \cite{smith2023}.", encoding="utf-8",
        )

        def _blind_judge(prompt: str) -> str:
            return "VERDICT: [ABSENT]\nSPAN: none\nREASONING: nothing found.\n"

        result = check_support_tally(tree_root, notes_root=notes_root, judge_fn=_blind_judge)
        assert result["canary_aborted"] is True
        abort_msg = " ".join(result["errors"]).lower()
        assert "blind" in abort_msg or "not real" in abort_msg or "canary" in abort_msg

    def test_sighted_judge_no_abort(self, tmp_path):
        from research_vault.manuscript.fidelity_gates import check_support_tally
        tree_root = _make_ms_tree(tmp_path)
        notes_root = tmp_path / "notes"
        _literature_note(notes_root, "smith2023")
        (tree_root / "sections" / "results.tex").write_text(
            r"We found that X is true \cite{smith2023}.", encoding="utf-8",
        )

        def _sighted_judge(prompt: str) -> str:
            return "VERDICT: [SUPPORTS]\nSPAN: Finding A: X is true.\nREASONING: Note backs claim.\n"

        result = check_support_tally(tree_root, notes_root=notes_root, judge_fn=_sighted_judge)
        assert result["canary_aborted"] is False

    def test_no_tex_files_returns_zero_tally(self, tmp_path):
        from research_vault.manuscript.fidelity_gates import check_support_tally
        tree_root = _make_ms_tree(tmp_path)
        result = check_support_tally(tree_root, judge_fn=lambda p: "VERDICT: [SUPPORTS]\n")
        assert result["m_citations"] == 0
        assert result["canary_aborted"] is False


# ===========================================================================
# check_cold_read_tally
# ===========================================================================

class TestCheckColdReadTally:
    def test_honest_report_and_dangling_errors(self, tmp_path):
        from research_vault.manuscript.fidelity_gates import check_cold_read_tally

        tree_root = _make_ms_tree(tmp_path)
        _calls = {"n": 0}

        def _judge(prompt: str) -> str:
            _calls["n"] += 1
            if _calls["n"] == 1:  # canary a
                return (
                    "SUMMARY:\nOVERALL: [STANDS-ALONE]\nBLOCK_COUNT: 0\nWARN_COUNT: 0\n"
                    "SWEPT: done.\n"
                )
            if _calls["n"] == 2:  # canary b
                return (
                    "FLAG:\nVERDICT: [DANGLING]\nSPAN: \"run covers_hash abc\"\n"
                    "KIND: internal-plumbing\nWHERE: S3\nMISSING: internal id.\n\n"
                    "SUMMARY:\nOVERALL: [DANGLING]\nBLOCK_COUNT: 2\nWARN_COUNT: 0\nSWEPT: done.\n"
                )
            # real call — a genuinely leaky passage
            return (
                "FLAG:\nVERDICT: [DANGLING]\nSPAN: \"see results/foo.csv\"\n"
                "KIND: artifact-path\nWHERE: S1\nMISSING: filesystem path.\n\n"
                "SUMMARY:\nOVERALL: [DANGLING]\nBLOCK_COUNT: 1\nWARN_COUNT: 0\nSWEPT: done.\n"
            )

        result = check_cold_read_tally(
            tree_root, judge_fn=_judge,
            pdf_text="See results/foo.csv for the full table.",
        )
        assert not result["canary_aborted"]
        assert result["overall"] == "DANGLING"
        assert any("DANGLING" in e for e in result["errors"])
        assert "verified" not in result["honest_report"].lower()

    def test_canary_aborted_surfaced(self, tmp_path):
        from research_vault.manuscript.fidelity_gates import check_cold_read_tally

        tree_root = _make_ms_tree(tmp_path)

        def _blind_judge(prompt: str) -> str:
            # Always STANDS-ALONE — blind to the leaky canary (b).
            return "SUMMARY:\nOVERALL: [STANDS-ALONE]\nBLOCK_COUNT: 0\nWARN_COUNT: 0\nSWEPT: done.\n"

        result = check_cold_read_tally(tree_root, judge_fn=_blind_judge, pdf_text="Some paper text.")
        assert result["canary_aborted"] is True
        assert any("ABORTED" in e for e in result["errors"])

    def test_no_text_graceful_degrade(self, tmp_path):
        from research_vault.manuscript.fidelity_gates import check_cold_read_tally
        tree_root = _make_ms_tree(tmp_path)
        result = check_cold_read_tally(tree_root, judge_fn=lambda p: "unused")
        assert result["overall"] == "STANDS-ALONE"
        assert result["canary_aborted"] is False
        assert any("no pdf text extracted" in w.lower() or "no text" in w.lower() for w in result["warnings"])
