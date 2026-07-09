"""tests/test_board_disposition_b5.py — PR-B5: disposition routing + the
#185 fix (design 2026-07-08-autonomous-board-design.md §5.2/§5.3).

Two regressions this file pins:

  1. ``build_approve_payload``'s incomplete cold-fanout HALT (the
     support-matcher fan-out was emitted but never completed) must land in
     ``not_run`` (never ``blocking``-only) and set the payload's
     ``canary_aborted`` flag correctly, so
     ``evaluation_from_structural_payload`` -> ``classify_disposition``
     reads HALT-DECLARE via the ``not_run``/``canary_aborted`` priority
     paths — never REVISE (which today happens because
     ``evaluation_from_structural_payload`` never saw a ``not_run`` signal
     for this case; the HALT message was buried in ``blocking`` only).

  2. ``evaluation_from_board`` (decision #6): a board quality shortfall
     that survives the bounded revise rounds routes to GO-WITH-RESIDUE
     (``residue`` populated, ``blocking`` empty) — never HALT-DECLARE.
     Only a canary-abort on the board fan-out still HALTs.

Both regressions are RED against the pre-B5 code (confirmed this session):
  - build_approve_payload put the incomplete-fanout HALT message only in
    ``blocking`` and had no ``canary_aborted`` field at all.
  - evaluation_from_board set blocking=failing_dims + revise_budget_exhausted
    True on any not-cleared board, which classify_disposition routes to
    HALT-DECLARE unconditionally — decision #6 requires GO-WITH-RESIDUE for
    a bare quality shortfall.

sr: PR-B5
"""
from __future__ import annotations

from pathlib import Path

import pytest

from research_vault.gates.judge_seam import CanaryAbortError, write_json
from research_vault.manuscript.check_gates import build_approve_payload
from research_vault.manuscript.fidelity_gates import emit_support_tasks
from research_vault.review import autonomy as auto


def _make_ms_tree(tmp_path: Path) -> Path:
    tree_root = tmp_path / "manuscripts" / "ms-test"
    (tree_root / "sections").mkdir(parents=True, exist_ok=True)
    return tree_root


def _write_tex_with_cites(tree_root: Path, n: int) -> None:
    lines = [f"This is claim {i}. \\cite{{paper{i}}}" for i in range(n)]
    (tree_root / "sections" / "intro.tex").write_text("\n\n".join(lines), encoding="utf-8")


def _literature_note(notes_root: Path, citekey: str) -> None:
    lit_dir = notes_root / "literature"
    lit_dir.mkdir(parents=True, exist_ok=True)
    fm = "---\ntype: literature\ntldr: This paper demonstrates X.\nfindings: Finding A.\n---\n"
    (lit_dir / f"{citekey}.md").write_text(fm, encoding="utf-8")


class _FakeType:
    key = "lit-review"
    equation_sources = ()


# ---------------------------------------------------------------------------
# 1. build_approve_payload: incomplete fanout -> not_run + canary_aborted,
#    never a blocking-only REVISE-shaped signal.
# ---------------------------------------------------------------------------

class TestIncompleteFanoutRoutesToNotRun:
    def test_missing_verdicts_file_lands_in_not_run_and_halts(self, tmp_path):
        tree_root = _make_ms_tree(tmp_path)
        project_notes_dir = tmp_path
        _write_tex_with_cites(tree_root, 2)
        for i in range(2):
            _literature_note(project_notes_dir, f"paper{i}")

        emitted = emit_support_tasks(tree_root, notes_root=project_notes_dir, manuscript_slug="ms-test")
        judge_dir = tree_root / "judge" / "support-matcher"
        write_json(judge_dir / "_judge-tasks.json", emitted["tasks_doc"])
        write_json(judge_dir / "_judge-canary-key.json", emitted["canary_key_doc"])
        # Deliberately never write _judge-verdicts.json — the fanout never
        # completed (the §1.8 floor-gate NOT RUN case).

        payload = build_approve_payload(tree_root, project_notes_dir, _FakeType())

        # ★ The regression: this message must be surfaced via not_run, not
        # buried as a blocking-only signal the structural adapter can't see.
        assert any("support-matcher" in n for n in payload["not_run"]), payload
        assert payload.get("canary_aborted", None) is False

        ev = auto.evaluation_from_structural_payload(payload)
        result = auto.classify_disposition(ev)
        assert result.disposition == auto.HALT_DECLARE, result
        # Must NOT be classified as a plain fixable BLOCK (which would
        # route to REVISE) — the not_run signal must dominate.
        assert "not_run" in result.evidence or "not run" in result.reason.lower()

    def test_canary_abort_sets_payload_flag_and_halts(self, tmp_path):
        tree_root = _make_ms_tree(tmp_path)
        project_notes_dir = tmp_path
        _write_tex_with_cites(tree_root, 2)
        for i in range(2):
            _literature_note(project_notes_dir, f"paper{i}")

        emitted = emit_support_tasks(tree_root, notes_root=project_notes_dir, manuscript_slug="ms-test")
        judge_dir = tree_root / "judge" / "support-matcher"
        write_json(judge_dir / "_judge-tasks.json", emitted["tasks_doc"])
        write_json(judge_dir / "_judge-canary-key.json", emitted["canary_key_doc"])

        canary_id = next(iter(emitted["canary_key_doc"]["canaries"]))
        expected = emitted["canary_key_doc"]["canaries"][canary_id]
        wrong = "CONTRADICTS" if expected != "CONTRADICTS" else "SUPPORTS"
        verdicts = []
        for t in emitted["tasks_doc"]["tasks"]:
            if t["id"] == canary_id:
                verdicts.append({"id": t["id"], "verdict": wrong})
            else:
                exp = emitted["canary_key_doc"]["canaries"].get(t["id"])
                verdicts.append({"id": t["id"], "verdict": exp or "SUPPORTS"})
        write_json(judge_dir / "_judge-verdicts.json", {"verdicts": verdicts})

        payload = build_approve_payload(tree_root, project_notes_dir, _FakeType())
        assert payload.get("canary_aborted") is True

        ev = auto.evaluation_from_structural_payload(payload)
        result = auto.classify_disposition(ev)
        assert result.disposition == auto.HALT_DECLARE
        assert result.evidence.get("canary_aborted") is True

    def test_no_judge_dir_unchanged_not_run_path_regression(self, tmp_path):
        """No judge/ dir at all -> the pre-existing not_run message + a
        clean (non-aborted) canary_aborted=False flag -- unchanged."""
        tree_root = _make_ms_tree(tmp_path)
        payload = build_approve_payload(tree_root, tmp_path, _FakeType())
        assert payload.get("canary_aborted") is False
        assert any("NOT RUN" in n or "not configured" in n.lower() for n in payload["not_run"])


# ---------------------------------------------------------------------------
# 2. evaluation_from_board (decision #6): quality shortfall -> GO-WITH-RESIDUE
# ---------------------------------------------------------------------------

class TestBoardQualityShortfallRoutesToResidue:
    def test_not_cleared_quality_shortfall_is_go_with_residue(self):
        board_result = {
            "cleared": False,
            "not_cleared": {
                "failing_dims": ["CONTENT (min score 2 < floor 3)"],
                "persistent_weakness": "CONTENT axis did not clear after 2 rounds.",
            },
        }
        ev = auto.evaluation_from_board(board_result)
        assert ev.blocking == []
        assert ev.canary_aborted is False
        assert ev.residue
        result = auto.classify_disposition(ev)
        assert result.disposition == auto.GO_WITH_RESIDUE, result
        assert result.is_go

    def test_canary_abort_still_halts_never_residue(self):
        board_result = {"cleared": False, "not_cleared": {"failing_dims": ["CONTENT"]}}
        ev = auto.evaluation_from_board(board_result, canary_aborted=True)
        result = auto.classify_disposition(ev)
        assert result.disposition == auto.HALT_DECLARE
        assert result.evidence.get("canary_aborted") is True

    def test_cleared_board_is_go(self):
        ev = auto.evaluation_from_board({"cleared": True, "not_cleared": None})
        result = auto.classify_disposition(ev)
        assert result.disposition == auto.GO


# ---------------------------------------------------------------------------
# 3. Integrity floor (bib/support) never routes to residue — decision #6's
#    "broken != best version" split, most-severe-wins when combined with a
#    board evaluation.
# ---------------------------------------------------------------------------

class TestIntegrityFloorNeverResidue:
    def test_structural_block_with_budget_exhausted_halts_not_residue(self):
        ev = auto.evaluation_from_structural_payload(
            {"ok": False, "blocking": ["[support-matcher] claim ABSENT from source"], "not_run": []}
        )
        ev.revise_budget_exhausted = True
        result = auto.classify_disposition(ev)
        assert result.disposition == auto.HALT_DECLARE
        assert result.disposition != auto.GO_WITH_RESIDUE

    def test_most_severe_wins_across_structural_and_board(self):
        """A HALT from the structural (integrity) side must dominate a
        GO-WITH-RESIDUE from the board side when both are evaluated for the
        same approve-manuscript run."""
        structural_ev = auto.evaluation_from_structural_payload(
            {"ok": False, "blocking": [], "not_run": ["support-matcher"]}
        )
        board_ev = auto.evaluation_from_board(
            {"cleared": False, "not_cleared": {"failing_dims": ["CONTENT"]}}
        )
        structural_result = auto.classify_disposition(structural_ev)
        board_result = auto.classify_disposition(board_ev)
        assert structural_result.disposition == auto.HALT_DECLARE
        assert board_result.disposition == auto.GO_WITH_RESIDUE
        # Severity ranking the caller (dag/verbs.py wiring) must apply:
        severity = {auto.HALT_DECLARE: 3, auto.GO_WITH_RESIDUE: 2, auto.REVISE: 1, auto.GO: 0}
        most_severe = max([structural_result, board_result], key=lambda r: severity[r.disposition])
        assert most_severe.disposition == auto.HALT_DECLARE
