"""test_relate_check.py — Wave 0 (Reading) PR-1/PR-2/PR-4/PR-5.

Covers:
  1. parse_paper_relations — the PR-2 paper→paper typed-edge parser.
  2. check_relate_presence — the PR-1 rejects-only mandatory-question
     presence check (Move 1 contribution_kind, PR-4 role/position,
     Move 3/PR-5 result_reported, Move 4/PR-2 paper_relations_sought).
  3. The whitelist-not-blacklist discipline: non-canonical spellings of
     yes/no fail closed, never silently pass (PR #175-delta lesson).
  4. The over-rigidity guard: a bare tag with no reasoning is rejected.

All hermetic (tmp_path). No live LLM calls, no network.
sr: NG-lit-review-wave0
"""
from __future__ import annotations

from pathlib import Path

import pytest

from research_vault.review.relate_check import (
    CONTRIBUTION_KINDS,
    ROLE_TYPES,
    RELATION_TYPES,
    check_relate_presence,
    parse_paper_relations,
)


def _write_note(path: Path, fields: dict[str, str], body: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for k, v in fields.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append(body)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


_COMPLETE_FIELDS = {
    "type": "literature",
    "citekey": "xiong2023-stepwise",
    "title": "Test Paper",
    "contribution_kind": "theory-bound",
    "role": "theoretical",
    "position": "This is the counter-position to the safe-exploration rebuttals.",
    "result_reported": "yes",
    "paper_relations_sought": "yes",
}

_RESULT_BODY = (
    "## Result\n\n"
    "The violation lower bound scales as Ω(√ST) under regret budget S,T; "
    "holds only for tabular MDPs; the paper notes this may not generalize.\n\n"
)

_RELATED_BODY = (
    "## Related papers\n\n"
    "- [CONTRADICTS] huang2022 — huang2022 claims near-free safe exploration "
    "under a known safe baseline; this paper's bound shows that assumption is "
    "load-bearing. (refutational)\n"
)


# ===========================================================================
# parse_paper_relations
# ===========================================================================

class TestParsePaperRelations:
    def test_parses_a_typed_edge(self):
        edges = parse_paper_relations(_RELATED_BODY)
        assert len(edges) == 1
        e = edges[0]
        assert e["tag"] == "CONTRADICTS"
        assert e["target"] == "huang2022"
        assert e["type"] == "refutational"
        assert "load-bearing" in e["reason"]

    def test_no_section_returns_empty(self):
        assert parse_paper_relations("## Some other section\n\ntext\n") == []

    def test_section_present_but_empty_returns_empty(self):
        assert parse_paper_relations("## Related papers\n\n## Next\n\ntext\n") == []

    def test_multiple_edges_all_parsed(self):
        body = (
            "## Related papers\n\n"
            "- [SUPPORTS] li2023 — replicates the same bound in a related "
            "setting, agreeing on the core mechanism. (reciprocal)\n"
            "- [PARTIAL] liu2021 — extends the argument to stochastic "
            "rewards, a special case of the general claim. (line-of-argument)\n"
        )
        edges = parse_paper_relations(body)
        assert {e["target"] for e in edges} == {"li2023", "liu2021"}
        assert {e["type"] for e in edges} == {"reciprocal", "line-of-argument"}

    def test_paper_edge_distinct_from_concept_edge(self):
        # A paper->concept edge elsewhere in the body must NOT be picked up
        # as a paper->paper edge (different target shape, different section).
        body = (
            "## Verified concept edges\n\n"
            "- [SUPPORTS] concepts/exploration.md — directly supports.\n\n"
            "## Related papers\n\n"
            "- [SUPPORTS] li2023 — agrees on mechanism, same regime tested. (reciprocal)\n"
        )
        edges = parse_paper_relations(body)
        assert len(edges) == 1
        assert edges[0]["target"] == "li2023"


# ===========================================================================
# check_relate_presence — happy path
# ===========================================================================

class TestCheckRelatePresenceHappyPath:
    def test_complete_note_passes(self, tmp_path):
        note = _write_note(
            tmp_path / "literature" / "xiong2023-stepwise.md",
            _COMPLETE_FIELDS,
            body=_RESULT_BODY + _RELATED_BODY,
        )
        result = check_relate_presence(note)
        assert result.ok, result.findings

    def test_no_result_and_no_relations_is_legitimate(self, tmp_path):
        """A paper with no quantitative result and no bearing on the corpus
        is a legitimate answer — 'no' to both is not a FAIL (flexible-not-rigid:
        the check verifies the question was ANSWERED, not that the answer is
        always 'yes')."""
        fields = dict(_COMPLETE_FIELDS)
        fields["result_reported"] = "no"
        fields["paper_relations_sought"] = "no"
        note = _write_note(tmp_path / "literature" / "novel2024.md", fields, body="")
        result = check_relate_presence(note)
        assert result.ok, result.findings

    def test_whitespace_and_case_tolerant(self, tmp_path):
        fields = dict(_COMPLETE_FIELDS)
        fields["result_reported"] = " YES "
        fields["paper_relations_sought"] = " No "
        fields["role"] = " Theoretical "
        fields["contribution_kind"] = " Theory-Bound "
        note = _write_note(
            tmp_path / "literature" / "tolerant.md", fields, body=_RESULT_BODY
        )
        result = check_relate_presence(note)
        assert result.ok, result.findings


# ===========================================================================
# check_relate_presence — Move 1 (contribution_kind)
# ===========================================================================

class TestMove1ContributionKind:
    def test_missing_contribution_kind_fails(self, tmp_path):
        fields = dict(_COMPLETE_FIELDS)
        del fields["contribution_kind"]
        note = _write_note(
            tmp_path / "literature" / "a.md", fields, body=_RESULT_BODY + _RELATED_BODY
        )
        result = check_relate_presence(note)
        assert not result.ok
        assert any("contribution_kind" in f for f in result.findings)

    def test_unrecognized_contribution_kind_fails(self, tmp_path):
        fields = dict(_COMPLETE_FIELDS)
        fields["contribution_kind"] = "vibes"
        note = _write_note(
            tmp_path / "literature" / "a.md", fields, body=_RESULT_BODY + _RELATED_BODY
        )
        result = check_relate_presence(note)
        assert not result.ok
        assert any("contribution_kind" in f for f in result.findings)

    def test_every_canonical_kind_accepted(self, tmp_path):
        for i, kind in enumerate(sorted(CONTRIBUTION_KINDS)):
            fields = dict(_COMPLETE_FIELDS)
            fields["contribution_kind"] = kind
            note = _write_note(
                tmp_path / "literature" / f"k{i}.md",
                fields,
                body=_RESULT_BODY + _RELATED_BODY,
            )
            result = check_relate_presence(note)
            assert result.ok, (kind, result.findings)


# ===========================================================================
# check_relate_presence — PR-4 (role / position)
# ===========================================================================

class TestPR4RoleAndPosition:
    def test_missing_role_fails(self, tmp_path):
        fields = dict(_COMPLETE_FIELDS)
        del fields["role"]
        note = _write_note(
            tmp_path / "literature" / "a.md", fields, body=_RESULT_BODY + _RELATED_BODY
        )
        result = check_relate_presence(note)
        assert not result.ok
        assert any("'role'" in f for f in result.findings)

    def test_unrecognized_role_fails(self, tmp_path):
        fields = dict(_COMPLETE_FIELDS)
        fields["role"] = "supporting"  # the OLD stance vocabulary — no longer valid for role
        note = _write_note(
            tmp_path / "literature" / "a.md", fields, body=_RESULT_BODY + _RELATED_BODY
        )
        result = check_relate_presence(note)
        assert not result.ok
        assert any("'role'" in f for f in result.findings)

    def test_missing_position_fails(self, tmp_path):
        fields = dict(_COMPLETE_FIELDS)
        del fields["position"]
        note = _write_note(
            tmp_path / "literature" / "a.md", fields, body=_RESULT_BODY + _RELATED_BODY
        )
        result = check_relate_presence(note)
        assert not result.ok
        assert any("'position'" in f for f in result.findings)

    def test_placeholder_position_fails(self, tmp_path):
        fields = dict(_COMPLETE_FIELDS)
        fields["position"] = "n/a"
        note = _write_note(
            tmp_path / "literature" / "a.md", fields, body=_RESULT_BODY + _RELATED_BODY
        )
        result = check_relate_presence(note)
        assert not result.ok
        assert any("too thin" in f for f in result.findings)

    def test_every_canonical_role_accepted(self, tmp_path):
        for i, role in enumerate(sorted(ROLE_TYPES)):
            fields = dict(_COMPLETE_FIELDS)
            fields["role"] = role
            note = _write_note(
                tmp_path / "literature" / f"r{i}.md",
                fields,
                body=_RESULT_BODY + _RELATED_BODY,
            )
            result = check_relate_presence(note)
            assert result.ok, (role, result.findings)


# ===========================================================================
# check_relate_presence — Move 3 / PR-5 (result_reported)
# ===========================================================================

class TestMove3ResultReported:
    def test_missing_result_reported_fails(self, tmp_path):
        fields = dict(_COMPLETE_FIELDS)
        del fields["result_reported"]
        note = _write_note(
            tmp_path / "literature" / "a.md", fields, body=_RESULT_BODY + _RELATED_BODY
        )
        result = check_relate_presence(note)
        assert not result.ok
        assert any("result_reported" in f for f in result.findings)

    def test_result_reported_yes_but_no_section_fails(self, tmp_path):
        # The mavorparker-had-no-number gap, made mechanically catchable.
        note = _write_note(
            tmp_path / "literature" / "mavorparker2021-noisytv.md",
            _COMPLETE_FIELDS,
            body=_RELATED_BODY,  # no '## Result' section at all
        )
        result = check_relate_presence(note)
        assert not result.ok
        assert any("Result" in f for f in result.findings)

    def test_result_reported_yes_with_empty_section_fails(self, tmp_path):
        note = _write_note(
            tmp_path / "literature" / "a.md",
            _COMPLETE_FIELDS,
            body="## Result\n\n" + _RELATED_BODY,
        )
        result = check_relate_presence(note)
        assert not result.ok
        assert any("empty" in f.lower() or "thin" in f.lower() for f in result.findings)

    def test_result_reported_no_requires_no_section(self, tmp_path):
        fields = dict(_COMPLETE_FIELDS)
        fields["result_reported"] = "no"
        note = _write_note(
            tmp_path / "literature" / "a.md", fields, body=_RELATED_BODY
        )
        result = check_relate_presence(note)
        assert result.ok, result.findings

    @pytest.mark.parametrize(
        "bad_spelling",
        ["Yes please", "reported", "true", "1", "y", "maybe", "backstop:reported"],
    )
    def test_non_canonical_spelling_fails_closed(self, tmp_path, bad_spelling):
        """Whitelist, not blacklist (PR #175-delta lesson): ANY non-canonical
        spelling of yes/no must fail loudly, never silently pass as if it
        were a recognized answer."""
        fields = dict(_COMPLETE_FIELDS)
        fields["result_reported"] = bad_spelling
        note = _write_note(
            tmp_path / "literature" / "a.md", fields, body=_RESULT_BODY + _RELATED_BODY
        )
        result = check_relate_presence(note)
        assert not result.ok
        assert any("result_reported" in f and "unrecognized" in f for f in result.findings)


# ===========================================================================
# check_relate_presence — Move 4 / PR-2 (paper_relations_sought)
# ===========================================================================

class TestMove4PaperRelationsSought:
    def test_missing_paper_relations_sought_fails(self, tmp_path):
        fields = dict(_COMPLETE_FIELDS)
        del fields["paper_relations_sought"]
        note = _write_note(
            tmp_path / "literature" / "a.md", fields, body=_RESULT_BODY + _RELATED_BODY
        )
        result = check_relate_presence(note)
        assert not result.ok
        assert any("paper_relations_sought" in f for f in result.findings)

    def test_sought_yes_but_no_related_papers_section_fails(self, tmp_path):
        note = _write_note(
            tmp_path / "literature" / "a.md", _COMPLETE_FIELDS, body=_RESULT_BODY
        )
        result = check_relate_presence(note)
        assert not result.ok
        assert any("Related papers" in f for f in result.findings)

    def test_sought_yes_but_empty_section_fails(self, tmp_path):
        note = _write_note(
            tmp_path / "literature" / "a.md",
            _COMPLETE_FIELDS,
            body=_RESULT_BODY + "## Related papers\n\n",
        )
        result = check_relate_presence(note)
        assert not result.ok
        assert any("Related papers" in f for f in result.findings)

    def test_sought_no_requires_no_section(self, tmp_path):
        fields = dict(_COMPLETE_FIELDS)
        fields["paper_relations_sought"] = "no"
        note = _write_note(
            tmp_path / "literature" / "a.md", fields, body=_RESULT_BODY
        )
        result = check_relate_presence(note)
        assert result.ok, result.findings

    def test_bare_tag_no_reasoning_fails_the_over_rigidity_guard(self, tmp_path):
        """A relation reduced to a bare tag with no reasoning is as thin as
        no relation — Ada's §5/§6 caveat, made mechanically catchable."""
        body = (
            _RESULT_BODY
            + "## Related papers\n\n"
            + "- [SUPPORTS] li2023 — yes. (reciprocal)\n"
        )
        note = _write_note(tmp_path / "literature" / "a.md", _COMPLETE_FIELDS, body=body)
        result = check_relate_presence(note)
        assert not result.ok
        assert any("bare tag" in f for f in result.findings)

    def test_non_canonical_spelling_fails_closed(self, tmp_path):
        fields = dict(_COMPLETE_FIELDS)
        fields["paper_relations_sought"] = "sought"
        note = _write_note(
            tmp_path / "literature" / "a.md", fields, body=_RESULT_BODY + _RELATED_BODY
        )
        result = check_relate_presence(note)
        assert not result.ok
        assert any(
            "paper_relations_sought" in f and "unrecognized" in f for f in result.findings
        )


# ===========================================================================
# Structural edge cases
# ===========================================================================

class TestStructuralEdgeCases:
    def test_missing_note_reported(self, tmp_path):
        result = check_relate_presence(tmp_path / "literature" / "ghost.md")
        assert not result.ok
        assert any("does not exist" in f for f in result.findings)

    def test_findings_accumulate_all_failures_not_just_first(self, tmp_path):
        note = _write_note(tmp_path / "literature" / "empty.md", {"type": "literature"}, body="")
        result = check_relate_presence(note)
        assert not result.ok
        # All 5 mandatory checklist items should be flagged (contribution_kind,
        # role, position, result_reported, paper_relations_sought).
        assert len(result.findings) >= 5
