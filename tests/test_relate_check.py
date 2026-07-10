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
        parsed = parse_paper_relations(_RELATED_BODY)
        assert len(parsed.edges) == 1
        assert parsed.malformed == []
        e = parsed.edges[0]
        assert e["tag"] == "CONTRADICTS"
        assert e["target"] == "huang2022"
        assert e["type"] == "refutational"
        assert "load-bearing" in e["reason"]
        assert e["kind_mismatch"] is None

    def test_no_section_returns_empty(self):
        parsed = parse_paper_relations("## Some other section\n\ntext\n")
        assert parsed.edges == []
        assert parsed.malformed == []

    def test_section_present_but_empty_returns_empty(self):
        parsed = parse_paper_relations("## Related papers\n\n## Next\n\ntext\n")
        assert parsed.edges == []
        assert parsed.malformed == []

    def test_multiple_edges_all_parsed(self):
        body = (
            "## Related papers\n\n"
            "- [SUPPORTS] li2023 — replicates the same bound in a related "
            "setting, agreeing on the core mechanism. (reciprocal)\n"
            "- [PARTIAL] liu2021 — extends the argument to stochastic "
            "rewards, a special case of the general claim. (line-of-argument)\n"
        )
        parsed = parse_paper_relations(body)
        assert {e["target"] for e in parsed.edges} == {"li2023", "liu2021"}
        assert {e["type"] for e in parsed.edges} == {"reciprocal", "line-of-argument"}
        assert parsed.malformed == []

    def test_paper_edge_distinct_from_concept_edge(self):
        # A paper->concept edge elsewhere in the body must NOT be picked up
        # as a paper->paper edge (different target shape, different section).
        body = (
            "## Verified concept edges\n\n"
            "- [SUPPORTS] concepts/exploration.md — directly supports.\n\n"
            "## Related papers\n\n"
            "- [SUPPORTS] li2023 — agrees on mechanism, same regime tested. (reciprocal)\n"
        )
        parsed = parse_paper_relations(body)
        assert len(parsed.edges) == 1
        assert parsed.edges[0]["target"] == "li2023"

    # -- architect review, PR #178 delta: (kind) optional, [TAG] authoritative --

    def test_kind_suffix_is_optional(self):
        """A valid edge with NO trailing (kind) mirror must still parse fully
        — the pre-review regex REQUIRED it, silently losing an otherwise-valid
        edge that simply omitted it (the most likely malformation)."""
        body = (
            "## Related papers\n\n"
            "- [EXTENDS] li2023 — generalizes the earlier special case to a "
            "broader class of MDPs.\n"
        )
        parsed = parse_paper_relations(body)
        assert parsed.malformed == []
        assert len(parsed.edges) == 1
        e = parsed.edges[0]
        assert e["target"] == "li2023"
        assert e["type"] == "line-of-argument"  # derived from [EXTENDS]
        assert e["kind_mismatch"] is None

    def test_tag_derives_kind_for_every_tag(self):
        mapping = {
            "SUPPORTS": "reciprocal",
            "CONTRADICTS": "refutational",
            "PARTIAL": "line-of-argument",
            "EXTENDS": "line-of-argument",
        }
        for tag, expected_kind in mapping.items():
            body = (
                "## Related papers\n\n"
                f"- [{tag}] li2023 — some real reasoning about the relation.\n"
            )
            parsed = parse_paper_relations(body)
            assert parsed.malformed == [], (tag, parsed.malformed)
            assert parsed.edges[0]["type"] == expected_kind, tag

    def test_stated_kind_agreeing_with_tag_no_mismatch(self):
        body = (
            "## Related papers\n\n"
            "- [SUPPORTS] li2023 — agrees on the mechanism. (reciprocal)\n"
        )
        parsed = parse_paper_relations(body)
        assert parsed.edges[0]["kind_mismatch"] is None

    def test_stated_kind_disagreeing_with_tag_surfaces_mismatch_tag_wins(self):
        """Ledger-wins precedent (mirrors key_equations' critical: flag vs its
        *(critical)* body mirror): [TAG] is authoritative; a disagreeing
        (kind) mirror is surfaced, not silently resolved either way."""
        body = (
            "## Related papers\n\n"
            "- [CONTRADICTS] huang2022 — the bound removes the known-baseline "
            "assumption huang2022 relies on. (reciprocal)\n"  # mismatched on purpose
        )
        parsed = parse_paper_relations(body)
        assert len(parsed.edges) == 1
        e = parsed.edges[0]
        # Tag wins: CONTRADICTS derives 'refutational', not the stated 'reciprocal'.
        assert e["type"] == "refutational"
        assert e["kind_mismatch"] == {"stated": "reciprocal", "derived": "refutational"}

    # -- architect review, the LOAD-BEARING fix: surface malformed, never drop --

    def test_typo_tag_is_surfaced_as_malformed_not_silently_dropped(self):
        """The exact defect the architect flagged: a '- [' -shaped line with
        a typo'd tag must be SURFACED in .malformed, never silently skipped
        by a finditer-style scan. RED-before-green regression test."""
        body = (
            "## Related papers\n\n"
            "- [SUPRTS] xiong2023-stepwise — the bound removes the known-"
            "baseline assumption the other paper relies on.\n"
        )
        parsed = parse_paper_relations(body)
        assert parsed.edges == []
        assert len(parsed.malformed) == 1
        assert "SUPRTS" in parsed.malformed[0]

    def test_missing_target_is_surfaced_as_malformed(self):
        body = "## Related papers\n\n- [SUPPORTS] — no citekey given here.\n"
        parsed = parse_paper_relations(body)
        assert parsed.edges == []
        assert len(parsed.malformed) == 1

    def test_valid_and_malformed_edges_coexist_both_surfaced(self):
        """3 edges where 1 is typo'd: the 2 valid edges parse AND the 1
        malformed line is surfaced — neither silently absorbs the other."""
        body = (
            "## Related papers\n\n"
            "- [SUPPORTS] li2023 — agrees on the mechanism in a related setting.\n"
            "- [CONTRADCTS] huang2022 — typo'd tag, should be surfaced.\n"
            "- [EXTENDS] liu2021 — generalizes to a broader class of MDPs.\n"
        )
        parsed = parse_paper_relations(body)
        assert {e["target"] for e in parsed.edges} == {"li2023", "liu2021"}
        assert len(parsed.malformed) == 1
        assert "CONTRADCTS" in parsed.malformed[0]

    def test_plain_prose_bullet_is_not_flagged_malformed(self):
        """Coordinator clarification: a plain '- ' bullet with NO bracket is
        legitimate free-form prose in this section — it must NEVER be
        surfaced as malformed (that would be a false positive)."""
        body = (
            "## Related papers\n\n"
            "- [SUPPORTS] li2023 — agrees on the mechanism.\n"
            "- Also worth noting: both papers share the same benchmark suite.\n"
        )
        parsed = parse_paper_relations(body)
        assert len(parsed.edges) == 1
        assert parsed.malformed == []


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


class TestMove4MalformedEdgeSurfacing:
    """Architect review, PR #178 delta — the LOAD-BEARING fix: a malformed
    '- [' -shaped line under '## Related papers' must FAIL the presence
    check, never silently pass. RED-before-green: this class asserts the
    behavior the pre-fix `finditer`-and-skip implementation did NOT have."""

    def test_typo_tag_line_fails_the_presence_check(self, tmp_path):
        body = (
            _RESULT_BODY
            + "## Related papers\n\n"
            + "- [SUPRTS] xiong2023-stepwise — the bound removes the "
            "known-baseline assumption the other paper relies on.\n"
        )
        note = _write_note(tmp_path / "literature" / "a.md", _COMPLETE_FIELDS, body=body)
        result = check_relate_presence(note)
        assert not result.ok
        assert any("malformed" in f.lower() for f in result.findings)
        assert any("SUPRTS" in f for f in result.findings)

    def test_malformed_line_alongside_valid_edges_still_fails(self, tmp_path):
        """A note with 2 valid edges + 1 typo'd edge must still FAIL on the
        malformed line — the valid edges do not mask the defect."""
        body = (
            _RESULT_BODY
            + "## Related papers\n\n"
            + "- [SUPPORTS] li2023 — agrees on the mechanism in a related setting.\n"
            + "- [CONTRADCTS] huang2022 — typo'd tag.\n"
            + "- [EXTENDS] liu2021 — generalizes to a broader class of MDPs.\n"
        )
        note = _write_note(tmp_path / "literature" / "a.md", _COMPLETE_FIELDS, body=body)
        result = check_relate_presence(note)
        assert not result.ok
        assert any("malformed" in f.lower() and "CONTRADCTS" in f for f in result.findings)

    def test_malformed_line_fails_even_when_sought_is_no(self, tmp_path):
        """A '- [' -shaped line is unambiguously an attempted edge regardless
        of what 'paper_relations_sought' claims — the malformed check is not
        conditioned on the yes/no answer."""
        fields = dict(_COMPLETE_FIELDS)
        fields["paper_relations_sought"] = "no"
        body = (
            _RESULT_BODY
            + "## Related papers\n\n"
            + "- [SUPRTS] xiong2023-stepwise — typo'd tag even though sought=no.\n"
        )
        note = _write_note(tmp_path / "literature" / "a.md", fields, body=body)
        result = check_relate_presence(note)
        assert not result.ok
        assert any("malformed" in f.lower() for f in result.findings)

    def test_plain_prose_bullet_does_not_fail_the_presence_check(self, tmp_path):
        """A plain '- ' bullet with no bracket is legitimate prose commentary
        and must NOT be flagged — this is the false-positive-free boundary
        the coordinator's '- [' clarification establishes."""
        body = (
            _RESULT_BODY
            + "## Related papers\n\n"
            + "- [SUPPORTS] li2023 — agrees on the mechanism in a related setting.\n"
            + "- Also worth noting: both papers share the same benchmark suite.\n"
        )
        note = _write_note(tmp_path / "literature" / "a.md", _COMPLETE_FIELDS, body=body)
        result = check_relate_presence(note)
        assert result.ok, result.findings

    def test_kind_optional_edge_passes_without_the_kind_suffix(self, tmp_path):
        """A valid edge omitting the OPTIONAL (kind) mirror must pass cleanly
        — the pre-review regex required it and silently lost the edge."""
        body = (
            _RESULT_BODY
            + "## Related papers\n\n"
            + "- [EXTENDS] li2023 — generalizes the earlier special case to "
            "a broader class of MDPs.\n"
        )
        note = _write_note(tmp_path / "literature" / "a.md", _COMPLETE_FIELDS, body=body)
        result = check_relate_presence(note)
        assert result.ok, result.findings


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


# ===========================================================================
# Misdiagnosis fixes — run-together fields and YAML block scalars
# ===========================================================================
# The flat-frontmatter parser (note._parse_frontmatter) reads ONE field per
# physical line. Two failure modes an LLM-authored note commonly hits produce
# a MISLEADING diagnostic rather than a correct one:
#   (a) two fields glued onto ONE physical line (no newline between them) —
#       the second field's "key: value" gets absorbed into the first field's
#       parsed VALUE, so the second field then reads as MISSING even though
#       it was written, just mis-attached.
#   (b) a YAML block-scalar marker (`>`/`|`) on a flat field, with the real
#       content on indented lines below — the flat parser only reads the
#       marker line, so the field parses to a degenerate ~1-char value and
#       is reported as "too thin" even though real content exists.
# Both must be DETECTED and hinted, not silently misdiagnosed — the check
# stays fail-closed (still rejects either case), only the diagnostic improves.

def _write_raw(path: Path, raw: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")
    return path


class TestRunTogetherFieldMisdiagnosis:
    def test_run_together_position_and_contribution_kind_hints_not_missing(self, tmp_path):
        """position + contribution_kind glued onto one physical line: the
        parser absorbs 'contribution_kind: benchmark' into position's value.
        The finding must be a run-together HINT naming both fields, never
        the misleading 'missing contribution_kind'."""
        raw = (
            "---\n"
            "type: literature\n"
            "citekey: xiong2023-stepwise\n"
            "title: Test Paper\n"
            'position: "This is the counter-position to the safe-exploration '
            'rebuttals." contribution_kind: benchmark\n'
            "role: theoretical\n"
            "result_reported: yes\n"
            "paper_relations_sought: yes\n"
            "---\n"
        ) + _RESULT_BODY + _RELATED_BODY
        note = _write_raw(tmp_path / "literature" / "a.md", raw)
        result = check_relate_presence(note)
        assert not result.ok
        assert not any(
            f.startswith("missing 'contribution_kind'") for f in result.findings
        ), result.findings
        assert any(
            "run-together" in f.lower()
            and "contribution_kind" in f
            and "position" in f
            for f in result.findings
        ), result.findings

    def test_run_together_result_reported_glued_into_role(self, tmp_path):
        raw = (
            "---\n"
            "type: literature\n"
            "citekey: a\n"
            "title: T\n"
            "contribution_kind: benchmark\n"
            "role: theoretical result_reported: yes\n"
            'position: "A real narrative sentence, long enough."\n'
            "paper_relations_sought: yes\n"
            "---\n"
        ) + _RESULT_BODY + _RELATED_BODY
        note = _write_raw(tmp_path / "literature" / "a.md", raw)
        result = check_relate_presence(note)
        assert not any(
            f.startswith("missing 'result_reported'") for f in result.findings
        ), result.findings
        assert any(
            "run-together" in f.lower() and "result_reported" in f
            for f in result.findings
        ), result.findings

    def test_no_false_positive_when_fields_are_on_separate_lines(self, tmp_path):
        """The complete, correctly-formatted note must NOT trigger a
        run-together hint — the detector only fires on genuine glue."""
        note = _write_note(
            tmp_path / "literature" / "clean.md",
            _COMPLETE_FIELDS,
            body=_RESULT_BODY + _RELATED_BODY,
        )
        result = check_relate_presence(note)
        assert result.ok, result.findings


class TestBlockScalarFieldMisdiagnosis:
    def test_block_scalar_position_hints_not_too_thin(self, tmp_path):
        """position: > with indented body lines parses to a degenerate
        1-char value ('>') under the flat parser. The finding must be a
        block-scalar HINT, never the misleading 'too thin (1 char)'."""
        raw = (
            "---\n"
            "type: literature\n"
            "citekey: a\n"
            "title: T\n"
            "contribution_kind: benchmark\n"
            "role: theoretical\n"
            "position: >\n"
            "  This is the counter-position to the safe-exploration rebuttals,\n"
            "  spanning multiple indented lines as a YAML block scalar.\n"
            "result_reported: yes\n"
            "paper_relations_sought: yes\n"
            "---\n"
        ) + _RESULT_BODY + _RELATED_BODY
        note = _write_raw(tmp_path / "literature" / "a.md", raw)
        result = check_relate_presence(note)
        assert not result.ok
        assert not any("too thin" in f for f in result.findings), result.findings
        assert any(
            "block scalar" in f.lower() and "position" in f for f in result.findings
        ), result.findings

    def test_block_scalar_pipe_style_also_hinted(self, tmp_path):
        raw = (
            "---\n"
            "type: literature\n"
            "citekey: a\n"
            "title: T\n"
            "contribution_kind: benchmark\n"
            "role: theoretical\n"
            "position: |\n"
            "  Line one of the narrative.\n"
            "  Line two of the narrative.\n"
            "result_reported: yes\n"
            "paper_relations_sought: yes\n"
            "---\n"
        ) + _RESULT_BODY + _RELATED_BODY
        note = _write_raw(tmp_path / "literature" / "a.md", raw)
        result = check_relate_presence(note)
        assert not result.ok
        assert any(
            "block scalar" in f.lower() and "position" in f for f in result.findings
        ), result.findings

    def test_correct_short_position_still_reported_as_too_thin(self, tmp_path):
        """A genuinely thin (but not block-scalar) value must keep the
        original 'too thin' diagnostic — the hint is scoped to the two
        specific misdiagnosis shapes, not a blanket replacement."""
        fields = dict(_COMPLETE_FIELDS)
        fields["position"] = "n/a"
        note = _write_note(
            tmp_path / "literature" / "a.md", fields, body=_RESULT_BODY + _RELATED_BODY
        )
        result = check_relate_presence(note)
        assert not result.ok
        assert any("too thin" in f for f in result.findings), result.findings
