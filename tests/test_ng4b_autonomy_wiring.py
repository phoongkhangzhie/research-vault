"""tests/test_ng4b_autonomy_wiring.py — NG-4b: wiring the autonomy engine
into a genuinely hands-off, self-advancing loop.

Design of record: docs/superpowers/specs/2026-07-08-next-gen-lit-review-loop-design.md
(§1, NG-4/5/6). This PR wires the NG-4/5/6 primitives (#182) so the loop
actually "kicks and walks away":

  1. Phase-transition auto-emission — coverage-gate / approve-framework GO
     auto-emits + auto-starts the next phase's DAG run in-process, instead
     of stranding at a human needing to hand-run `rv review expand` /
     `rv manuscript expand` + `rv dag run`.
  2. Live coverage-deviation BLOCK — the frozen corpus citekey-set is
     stamped the first time coverage-gate is evaluated; a later undeclared
     delta (vs `_deviations.md`) trips a direct HALT-DECLARE (never
     auto-revise — a silent corpus edit must surface to a human).
  3. Canary-abort hardening on approve-manuscript --auto — a support-matcher
     CanaryAbortError must classify as HALT-DECLARE, never REVISE (a
     canary-abort landing in `blocking` would be downgraded to REVISE by
     the generic gate-policy engine — the exact priority violation the
     ordering exists to prevent).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.review import autonomy as auto  # noqa: E402


# ===========================================================================
# 2. Live coverage-deviation BLOCK — review.autonomy unit level
# ===========================================================================

def _corpus_note(path: Path, citekeys: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(f"| [NEW] | {ck} | title-{ck} |" for ck in citekeys)
    path.write_text(
        "| annotation | citekey | title |\n|---|---|---|\n" + rows + "\n",
        encoding="utf-8",
    )


def _saturation_info(*, stop_reason: str = "saturated") -> dict:
    return {"exists": True, "stop_reason": stop_reason, "is_backstop": stop_reason.startswith("backstop:")}


class TestCoverageGateDeviationCheck:
    def test_first_pass_stamps_frozen_baseline_and_proceeds(self, tmp_path: Path):
        meta: dict = {}
        corpus_path = tmp_path / "reviews" / "s1" / "_corpus.md"
        _corpus_note(corpus_path, ["paperA2024", "paperB2024"])
        deviations_path = tmp_path / "reviews" / "s1" / "_deviations.md"

        result = auto.classify_coverage_gate_with_deviation_check(
            meta, _saturation_info(), corpus_path=corpus_path, deviations_path=deviations_path,
        )
        assert result.disposition == auto.GO
        assert meta["frozen_corpus_citekeys"] == sorted(["paperA2024", "paperB2024"])

    def test_frozen_run_no_delta_is_ok(self, tmp_path: Path):
        corpus_path = tmp_path / "reviews" / "s2" / "_corpus.md"
        _corpus_note(corpus_path, ["paperA2024", "paperB2024"])
        deviations_path = tmp_path / "reviews" / "s2" / "_deviations.md"
        meta = {"frozen_corpus_citekeys": ["paperA2024", "paperB2024"]}

        result = auto.classify_coverage_gate_with_deviation_check(
            meta, _saturation_info(), corpus_path=corpus_path, deviations_path=deviations_path,
        )
        assert result.disposition == auto.GO

    def test_undeclared_removal_halts_never_revises(self, tmp_path: Path):
        """★ leak-plant: an undeclared corpus removal mid-run -> HALT."""
        corpus_path = tmp_path / "reviews" / "s3" / "_corpus.md"
        deviations_path = tmp_path / "reviews" / "s3" / "_deviations.md"
        # Frozen baseline had 2 papers; the corpus on disk now silently has 1
        # (paperB2024 was removed with no matching _deviations.md entry).
        _corpus_note(corpus_path, ["paperA2024"])
        meta = {"frozen_corpus_citekeys": ["paperA2024", "paperB2024"]}

        result = auto.classify_coverage_gate_with_deviation_check(
            meta, _saturation_info(), corpus_path=corpus_path, deviations_path=deviations_path,
        )
        assert result.disposition == auto.HALT_DECLARE
        assert "undeclared" in result.reason.lower()
        assert "REVISE" not in result.disposition

    def test_declared_removal_proceeds(self, tmp_path: Path):
        corpus_path = tmp_path / "reviews" / "s4" / "_corpus.md"
        deviations_path = tmp_path / "reviews" / "s4" / "_deviations.md"
        _corpus_note(corpus_path, ["paperA2024"])
        meta = {"frozen_corpus_citekeys": ["paperA2024", "paperB2024"]}
        auto.record_deviation(
            deviations_path,
            version=2,
            pre_criteria="include X",
            post_criteria="include X, excluding duplicate paperB2024",
            removed=["paperB2024"],
            rationale="paperB2024 was a duplicate of paperA2024 discovered on re-read.",
        )

        result = auto.classify_coverage_gate_with_deviation_check(
            meta, _saturation_info(), corpus_path=corpus_path, deviations_path=deviations_path,
        )
        assert result.disposition == auto.GO

    def test_undeclared_delta_short_circuits_before_saturation_check(self, tmp_path: Path):
        """An undeclared deviation HALTs even when the saturation record
        itself would have cleanly GO'd — the deviation check is a fail-closed
        gate in front of, not behind, the saturation disposition."""
        corpus_path = tmp_path / "reviews" / "s5" / "_corpus.md"
        deviations_path = tmp_path / "reviews" / "s5" / "_deviations.md"
        _corpus_note(corpus_path, ["paperA2024", "paperC2024"])  # added + removed vs frozen
        meta = {"frozen_corpus_citekeys": ["paperA2024", "paperB2024"]}

        result = auto.classify_coverage_gate_with_deviation_check(
            meta, _saturation_info(stop_reason="saturated"), corpus_path=corpus_path,
            deviations_path=deviations_path,
        )
        assert result.disposition == auto.HALT_DECLARE


# ===========================================================================
# 3. Canary-abort hardening — evaluation_from_structural_payload
# ===========================================================================

class TestStructuralPayloadCanaryHardening:
    def test_canary_aborted_true_halts_never_revises(self):
        payload = {
            "ok": False,
            "blocking": ["[support-matcher] CANARY ABORT (HALT-DECLARE): judge blind to planted probe"],
            "signals": [],
            "not_run": [],
            "canary_aborted": True,
        }
        ev = auto.evaluation_from_structural_payload(payload)
        assert ev.canary_aborted is True
        result = auto.classify_disposition(ev)
        assert result.disposition == auto.HALT_DECLARE
        assert result.disposition != auto.REVISE

    def test_no_canary_abort_regular_block_still_revises(self):
        payload = {
            "ok": False,
            "blocking": ["[hermetic-bib] unresolved citekey foo2024"],
            "signals": [],
            "not_run": [],
            "canary_aborted": False,
        }
        ev = auto.evaluation_from_structural_payload(payload)
        assert ev.canary_aborted is False
        result = auto.classify_disposition(ev)
        assert result.disposition == auto.REVISE

    def test_missing_canary_aborted_key_defaults_false_backward_compat(self):
        payload = {"ok": True, "blocking": [], "signals": [], "not_run": []}
        ev = auto.evaluation_from_structural_payload(payload)
        assert ev.canary_aborted is False


class TestBuildApprovePayloadPropagatesCanaryAborted:
    def test_live_judge_canary_abort_sets_top_level_flag(self, tmp_path: Path):
        from research_vault.manuscript.check_gates import build_approve_payload

        tree_root = tmp_path / "manuscripts" / "ms-canary"
        (tree_root / "sections").mkdir(parents=True, exist_ok=True)
        (tree_root / "sections" / "intro.tex").write_text(
            "A finding. \\cite{paper2024}\n", encoding="utf-8",
        )
        project_notes_dir = tmp_path
        lit_dir = project_notes_dir / "literature"
        lit_dir.mkdir(parents=True, exist_ok=True)
        (lit_dir / "paper2024.md").write_text(
            "---\ntype: literature\n---\n## Result\nSome finding.\n", encoding="utf-8",
        )

        class _FakeType:
            key = "lit-review"
            equation_sources = ()

        # A judge_fn that always answers [ABSENT] is blind on the known-
        # supported canary probe -> check_support_tally aborts, canary_aborted=True.
        payload = build_approve_payload(
            tree_root, project_notes_dir, _FakeType(), judge_fn=lambda p: "[ABSENT]",
        )
        assert payload.get("canary_aborted") is True
        assert not payload["ok"]

    def test_no_canary_abort_flag_false(self, tmp_path: Path):
        from research_vault.manuscript.check_gates import build_approve_payload

        tree_root = tmp_path / "manuscripts" / "ms-clean"
        (tree_root / "sections").mkdir(parents=True, exist_ok=True)
        project_notes_dir = tmp_path

        class _FakeType:
            key = "lit-review"
            equation_sources = ()

        payload = build_approve_payload(tree_root, project_notes_dir, _FakeType())
        assert payload.get("canary_aborted") is False
