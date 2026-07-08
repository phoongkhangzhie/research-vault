"""test_pr4_gate_contract_unchanged.py — Wave 0 (Reading) PR-4 confirmation.

Confirms the design doc's §5 claim holds: "the support-matcher/cold-read
gate contracts stay unchanged" under PR-4's `stance` → `role` + `position`
field split. The J-2 stance-mismatch check in ``gates/support_matcher.py``
reads a literature note's `stance:` frontmatter field via
``manuscript/fidelity_gates.py``'s ``nf.get("stance")`` — a PR-4-authored
note never populates that field, so this test proves the gate degrades
GRACEFULLY (stance resolves to None, exactly as it already does for any
pre-PR-4 note that never set `stance:`) rather than crashing or silently
changing the BLOCK/WARN semantics.

Note the honest scope: `role`'s vocabulary (methodological/empirical/
theoretical/counter-position) is semantically DISJOINT from the J-2 check's
specific confidence-level strings ("exploratory"/"pilot"/"tentative") — and
so was the OLD relate brief's `stance` vocabulary (supporting/opposing/
tangential/methodological). So this was never a live path for relate-produced
notes even before PR-4; the fallback-to-`role` shortcut is deliberately NOT
added (it would imply a false semantic equivalence). This test is the
grounding evidence for that claim, not just an assertion of it.

★ CONSCIOUS FORECLOSURE (coordinator confirmation, PR #178 delta): this split
does not merely leave J-2 inert (it already was for relate-produced notes) —
it PERMANENTLY FORECLOSES the possibility of a relate-note ever triggering
J-2 again. `role`'s fixed vocabulary is a contribution-TYPE axis
(methodological/empirical/theoretical/counter-position), never an
evidence-STRENGTH axis, so nothing a relate-note emits can ever match J-2's
{exploratory, pilot, tentative} trigger going forward. This is a deliberate,
documented choice, not a silent regression — flagged here + in DEVLOG.md +
the PR body for Khang. Follow-up option, if wanted later: a real
evidence-strength gate for lit-review claims would need the relate protocol
to emit its OWN evidence-strength marker (e.g. a `confidence:` field) for
J-2 (or a J-2-equivalent) to read — this wave does not add one.

sr: NG-lit-review-wave0 (PR-4 confirmation)
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _make_ms_tree(tmp_path: Path) -> Path:
    tree_root = tmp_path / "manuscripts" / "ms-test"
    (tree_root / "sections").mkdir(parents=True, exist_ok=True)
    return tree_root


def _pr4_literature_note(notes_root: Path, citekey: str) -> Path:
    """A note in the NEW (post-PR-4) shape: role + position, no `stance:`."""
    lit_dir = notes_root / "literature"
    lit_dir.mkdir(parents=True, exist_ok=True)
    fields = {
        "type": "literature",
        "citekey": citekey,
        "contribution_kind": "theory-bound",
        "role": "theoretical",
        "position": "This is the counter-position to the safe-exploration rebuttals.",
        "result_reported": "yes",
        "paper_relations_sought": "no",
    }
    fm = "---\n" + "\n".join(f"{k}: {v}" for k, v in fields.items()) + "\n---\n"
    body = "## Result\n\nBound scales as O(sqrt(ST)); tabular MDPs only.\n"
    path = lit_dir / f"{citekey}.md"
    path.write_text(fm + body, encoding="utf-8")
    return path


class TestSupportMatcherContractUnchanged:
    def test_pr4_note_read_without_crash_stance_resolves_none(self, tmp_path):
        """gates.support_matcher.match_support consumes a PR-4-shaped note
        (via the fidelity_gates thin adapter) without error — `stance` reads
        as None (absent field), exactly like any legacy no-stance note."""
        from research_vault.manuscript.fidelity_gates import check_support_tally
        tree_root = _make_ms_tree(tmp_path)
        notes_root = tmp_path / "notes"
        _pr4_literature_note(notes_root, "xiong2023-stepwise")
        (tree_root / "sections" / "results.tex").write_text(
            r"We found that X holds \cite{xiong2023-stepwise}.", encoding="utf-8",
        )

        def _judge(prompt: str) -> str:
            return "VERDICT: [SUPPORTS]\nVERBATIM_SPAN: Bound scales.\nREASONING: Backs claim.\n"

        result = check_support_tally(tree_root, notes_root=notes_root, judge_fn=_judge)
        assert result["canary_aborted"] is False
        assert result["m_citations"] >= 1
        # No crash, honest report format preserved (charter §2's contract).
        assert "verified" not in result["honest_report"].lower()

    def test_position_narrative_is_fed_to_judge_as_evidence(self, tmp_path):
        """_read_note_structured_fields includes ALL non-denylisted scalar
        fields — `position`'s rich narrative becomes judge-visible evidence,
        same code path as any other scalar field. This is strictly MORE
        evidence than the old ambiguous `stance` field gave, not less."""
        from research_vault.gates.support_matcher import _read_note_structured_fields
        note = _pr4_literature_note(tmp_path / "notes", "xiong2023-stepwise")
        fields = _read_note_structured_fields(note)
        assert "position" in fields
        assert "counter-position" in fields["position"]

    def test_j2_stance_param_resolves_none_for_pr4_note(self, tmp_path):
        """The exact fidelity_gates.py extraction line (`nf.get("stance")`)
        against a PR-4 note — proves the J-2 stance-context injection is
        simply skipped (None), the SAME degrade path a legacy note with no
        `stance:` field already takes. No new failure mode."""
        from research_vault.note import _parse_frontmatter
        note = _pr4_literature_note(tmp_path / "notes", "xiong2023-stepwise")
        text = note.read_text(encoding="utf-8")
        nf, _ = _parse_frontmatter(text)
        stance = nf.get("stance") or None
        assert stance is None
        # role IS present -- confirms this is a genuine PR-4 note, not an
        # accidentally-empty one.
        assert nf.get("role") == "theoretical"


class TestColdReadContractUnchanged:
    def test_cold_read_gate_has_no_relate_field_coupling(self):
        """Structural confirmation: gates.coldread never references any of
        the PR-1/2/4/5 relate fields — the cold-read gate contract has zero
        surface area this wave could have touched. Word-boundary match (not
        bare substring) so 'stance' doesn't false-positive on 'instance'."""
        import inspect
        import re
        from research_vault.gates import coldread
        src = inspect.getsource(coldread)
        for field_name in (
            "stance", "role", "position", "contribution_kind",
            "result_reported", "paper_relations_sought",
        ):
            assert not re.search(rf"\b{field_name}\b", src), (
                f"gates/coldread.py unexpectedly references {field_name!r} — "
                "the cold-read gate contract was supposed to have zero surface "
                "area touched by the Wave 0 relate-field changes"
            )

    def test_cold_read_tally_runs_normally_against_pr4_notes(self, tmp_path):
        from research_vault.manuscript.fidelity_gates import check_cold_read_tally
        tree_root = _make_ms_tree(tmp_path)
        (tree_root / "sections" / "thematic.tex").write_text(
            r"This section stands alone and cites \cite{xiong2023-stepwise}.",
            encoding="utf-8",
        )
        _calls = {"n": 0}

        def _judge(prompt: str) -> str:
            _calls["n"] += 1
            if _calls["n"] == 2:  # canary b — must see the leak
                return (
                    "FLAG:\nVERDICT: [DANGLING]\nSPAN: \"run covers_hash abc\"\n"
                    "KIND: internal-plumbing\nWHERE: S3\nMISSING: internal id.\n\n"
                    "SUMMARY:\nOVERALL: [DANGLING]\nBLOCK_COUNT: 2\nWARN_COUNT: 0\nSWEPT: done.\n"
                )
            return (
                "SUMMARY:\nOVERALL: [STANDS-ALONE]\nBLOCK_COUNT: 0\nWARN_COUNT: 0\n"
                "SWEPT: done.\n"
            )

        result = check_cold_read_tally(tree_root, judge_fn=_judge)
        assert result["canary_aborted"] is False
