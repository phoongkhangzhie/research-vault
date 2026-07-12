"""tests/test_pr4a_edge_write_foundation.py — the unified typed-edge
engine's two remaining foundation pieces (the knowledge-graph model's
typed-edge foundation):

  1. THE WRITER — ``append_within_project_bidirectional_edge`` generalizes
     the bidirectional-write PATTERN (``append_bidirectional_edge``'s
     converse-lookup mechanism, reusing ``relate_check._TAG_SYMMETRY`` as
     the one SSOT) beyond the literature-only write root to an arbitrary
     within-project type pair (``project_notes_dir/<type>/``). The
     read/resolve side for this scope already exists
     (``review.check_link_resolution``'s ``within-project`` arm); this is
     the WRITE side.

  2. THE CAP — ``check_relate_presence``'s Defect #71 retrieval-tier gate
     is now FAMILY-KEYED: every ARGUMENTATIVE edge (SUPPORTS/CONTRADICTS)
     gets the read-basis cap regardless of scope (intra-shared,
     cross-bundle, within-project) — closing a grounding-gate gap: a
     skimmed-basis note could otherwise CONTRADICTS a paper uncapped by
     writing the edge as a cross-bundle ``okf:...`` link instead of an
     intra-shared ``/literature/...`` one. Provenance/structural edges
     (USES/PRODUCED/DERIVED-FROM/GROUNDED-IN/ADDRESSES/ANSWERS) get NO cap.

Plant-the-failure discipline: both classes below include a
test that directly demonstrates what the PRE-FIX code path would have
done (uncapped cross-bundle CONTRADICTS; a same-token within-project
mirror) and asserts the REAL code does NOT do that.

All hermetic (tmp_path). No live LLM calls, no network.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from research_vault.review import incremental_relate as ir
from research_vault.review.relate_check import (
    check_relate_presence,
    parse_typed_edges,
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
    "type": "findings",
    "contribution_kind": "mechanism",
    "role": "empirical",
    "position": "This finding derives from the q1-main1 experiment run.",
    "result_reported": "yes",
    "paper_relations_sought": "no",
    "read_basis": "full-text",
}

_RESULT_BODY = (
    "## Result\n\n"
    "The finding shows a measurable effect under the tested condition, "
    "holding across three seeds; the note records the magnitude.\n\n"
)


# ===========================================================================
# 1. THE WRITER — append_within_project_bidirectional_edge
# ===========================================================================

class TestWithinProjectBidirectionalWrite:
    def test_asymmetric_writes_converse_and_lands_in_correct_type_dirs(self, tmp_path):
        """finding DERIVED-FROM experiment: the finding's own edge carries
        DERIVED-FROM; the experiment's mirror carries the converse SHOWS —
        and each note lands under project_notes_dir/<type>/, not a
        literature-only root."""
        project_notes_dir = tmp_path / "notes" / "demo"
        finding = project_notes_dir / "findings" / "finding1.md"
        experiment = project_notes_dir / "experiments" / "exp1.md"
        finding.parent.mkdir(parents=True)
        experiment.parent.mkdir(parents=True)
        finding.write_text("---\ntype: findings\n---\n\nbody\n", encoding="utf-8")
        experiment.write_text("---\ntype: experiments\n---\n\nbody\n", encoding="utf-8")

        ir.append_within_project_bidirectional_edge(
            project_notes_dir, "findings", "finding1", "experiments", "exp1",
            tag="DERIVED-FROM", reason="the finding derives from this run's analysis",
        )

        finding_text = finding.read_text(encoding="utf-8")
        exp_text = experiment.read_text(encoding="utf-8")
        assert "[experiments/exp1.md" not in finding_text  # sanity: no double-nesting
        assert "(/experiments/exp1.md) — DERIVED-FROM: the finding derives" in finding_text
        # THE GUARD (mirrors the guard test): the reciprocal edge on the
        # experiment note carries the CONVERSE, never the same token.
        assert "(/findings/finding1.md) — SHOWS: the finding derives" in exp_text
        assert "— DERIVED-FROM:" not in exp_text

    @pytest.mark.parametrize(
        "tag,converse,target_scope",
        [
            ("ADDRESSES", "ADDRESSED-BY", "gaps"),
            ("ANSWERS", "ANSWERED-BY", "gaps"),
        ],
    )
    def test_other_asymmetric_within_project_tags(self, tmp_path, tag, converse, target_scope):
        project_notes_dir = tmp_path / "notes" / "demo"
        experiment = project_notes_dir / "experiments" / "exp2.md"
        gap = project_notes_dir / target_scope / "gap1.md"
        experiment.parent.mkdir(parents=True)
        gap.parent.mkdir(parents=True)
        experiment.write_text("---\ntype: experiments\n---\n\nbody\n", encoding="utf-8")
        gap.write_text(f"---\ntype: {target_scope}\n---\n\nbody\n", encoding="utf-8")

        ir.append_within_project_bidirectional_edge(
            project_notes_dir, "experiments", "exp2", target_scope, "gap1",
            tag=tag, reason="a real, considered reasoning clause",
        )
        exp_text = experiment.read_text(encoding="utf-8")
        gap_text = gap.read_text(encoding="utf-8")
        assert f"— {tag}: a real, considered reasoning clause" in exp_text
        assert f"— {converse}: a real, considered reasoning clause" in gap_text
        assert f"— {tag}:" not in gap_text

    def test_symmetric_within_project_tag_mirrors_same_token(self, tmp_path):
        """SUPPORTS/CONTRADICTS/PARTIAL are argumentative and rarely land
        within-project, but the writer must apply the SAME symmetry rule
        (same-token mirror) regardless of which scope calls it — one SSOT
        (_TAG_SYMMETRY), no scope-specific fork."""
        project_notes_dir = tmp_path / "notes" / "demo"
        finding_a = project_notes_dir / "findings" / "findingA.md"
        finding_b = project_notes_dir / "findings" / "findingB.md"
        finding_a.parent.mkdir(parents=True)
        finding_a.write_text("---\ntype: findings\n---\n\nbody\n", encoding="utf-8")
        finding_b.write_text("---\ntype: findings\n---\n\nbody\n", encoding="utf-8")

        ir.append_within_project_bidirectional_edge(
            project_notes_dir, "findings", "findingA", "findings", "findingB",
            tag="CONTRADICTS", reason="the two findings disagree on direction",
        )
        a_text = finding_a.read_text(encoding="utf-8")
        b_text = finding_b.read_text(encoding="utf-8")
        assert "— CONTRADICTS: the two findings disagree" in a_text
        assert "— CONTRADICTS: the two findings disagree" in b_text

    @pytest.mark.parametrize("tag", ("USES", "GROUNDED-IN", "PRODUCED"))
    def test_never_mirrored_tags_refuse_bidirectional_write(self, tmp_path, tag):
        project_notes_dir = tmp_path / "notes" / "demo"
        exp = project_notes_dir / "experiments" / "exp3.md"
        lit = project_notes_dir / "findings" / "finding3.md"
        exp.parent.mkdir(parents=True)
        lit.parent.mkdir(parents=True)
        exp.write_text("---\ntype: experiments\n---\n\nbody\n", encoding="utf-8")
        lit.write_text("---\ntype: findings\n---\n\nbody\n", encoding="utf-8")

        with pytest.raises(ValueError, match="never auto-mirrored"):
            ir.append_within_project_bidirectional_edge(
                project_notes_dir, "experiments", "exp3", "findings", "finding3",
                tag=tag, reason="a real, considered reasoning clause",
            )
        assert "— " not in exp.read_text(encoding="utf-8")
        assert "— " not in lit.read_text(encoding="utf-8")

    def test_plant_the_failure_pre_fix_same_token_default_not_written(self, tmp_path):
        """The literal defect this writer must not reintroduce: a
        same-token mirror for an asymmetric within-project tag. Confirm the
        real code path writes the CONVERSE, not ADDRESSES on both notes."""
        project_notes_dir = tmp_path / "notes" / "demo"
        experiment = project_notes_dir / "experiments" / "expZ.md"
        gap = project_notes_dir / "gaps" / "gapZ.md"
        experiment.parent.mkdir(parents=True)
        gap.parent.mkdir(parents=True)
        experiment.write_text("---\ntype: experiments\n---\n\nbody\n", encoding="utf-8")
        gap.write_text("---\ntype: gaps\n---\n\nbody\n", encoding="utf-8")

        ir.append_within_project_bidirectional_edge(
            project_notes_dir, "experiments", "expZ", "gaps", "gapZ",
            tag="ADDRESSES", reason="this experiment closes the open gap",
        )
        gap_text = gap.read_text(encoding="utf-8")
        pre_fix_would_have_written = "— ADDRESSES: this experiment closes" in gap_text
        assert pre_fix_would_have_written is False
        assert "— ADDRESSED-BY: this experiment closes" in gap_text


# ===========================================================================
# append_typed_edge — the general write mechanism append_related_papers_edge
# now delegates to (byte-identical golden behavior confirmed separately)
# ===========================================================================

class TestAppendTypedEdgeGeneralMechanism:
    def test_cross_bundle_edge_writes_okf_link_target(self, tmp_path):
        project_notes_dir = tmp_path / "notes" / "demo"
        experiment = project_notes_dir / "experiments" / "exp4.md"
        experiment.parent.mkdir(parents=True)
        experiment.write_text("---\ntype: experiments\n---\n\nbody\n", encoding="utf-8")

        ir.append_typed_edge(
            experiment, display="baltaji2024",
            target_link="okf:literature/baltaji2024.md",
            tag="USES", reason="grounds the experiment in this paper's method",
        )
        text = experiment.read_text(encoding="utf-8")
        assert "[baltaji2024](okf:literature/baltaji2024.md) — USES:" in text

    def test_append_related_papers_edge_still_byte_identical(self, tmp_path):
        """Golden discipline: the literature-specific wrapper must
        still write EXACTLY the same bytes as before this PR."""
        note = tmp_path / "literature" / "a.md"
        note.parent.mkdir(parents=True)
        note.write_text("---\ncitekey: a\n---\n\nbody\n", encoding="utf-8")

        ir.append_related_papers_edge(
            note, display="huang2022", target="huang2022",
            tag="CONTRADICTS", reason="a real, considered reasoning clause",
        )
        text = note.read_text(encoding="utf-8")
        assert (
            "- [huang2022](/literature/huang2022.md) — CONTRADICTS: "
            "a real, considered reasoning clause" in text
        )


# ===========================================================================
# 2. THE CAP — family-keyed read-basis gate
# ===========================================================================

class TestFamilyKeyedReadBasisCap:
    def test_cross_bundle_contradicts_from_skimmed_basis_is_capped(self, tmp_path):
        """THE PLANT-THE-FAILURE PROOF: without family-keying, a
        cross-bundle CONTRADICTS edge from a skimmed-basis note would pass
        UNCAPPED (the pre-fix cap only walked parsed_relations/
        parsed_concepts, never other_edges). Confirm it is now capped."""
        fields = dict(_COMPLETE_FIELDS)
        fields["read_basis"] = "abstract-only"
        body = (
            _RESULT_BODY
            + "## Related papers\n\n"
            + "- [smith2024](okf:literature/smith2024.md) — CONTRADICTS: "
            "disagrees with the paper's central claim.\n"
        )
        note = _write_note(tmp_path / "notes" / "demo" / "findings" / "f1.md", fields, body=body)

        # Sanity: this edge really is in the cross-bundle "other" bucket,
        # not the intra-shared paper/concept buckets — otherwise the test
        # would pass vacuously via the PRE-EXISTING (uncapped-irrelevant)
        # code path instead of exercising the new family-keyed branch.
        other = parse_typed_edges(body)
        assert any(e["tag"] == "CONTRADICTS" and e["scope"] == "cross-bundle" for e in other.edges)

        result = check_relate_presence(note)
        assert not result.ok
        assert any(
            "cross-bundle" in f and "read_basis" in f and "PARTIAL" in f
            for f in result.findings
        ), result.findings

    def test_cross_bundle_contradicts_from_full_text_basis_is_not_capped(self, tmp_path):
        fields = dict(_COMPLETE_FIELDS)
        fields["read_basis"] = "full-text"
        body = (
            _RESULT_BODY
            + "## Related papers\n\n"
            + "- [smith2024](okf:literature/smith2024.md) — CONTRADICTS: "
            "disagrees with the paper's central claim.\n"
        )
        note = _write_note(tmp_path / "notes" / "demo" / "findings" / "f2.md", fields, body=body)
        result = check_relate_presence(note)
        assert not any("read_basis" in f for f in result.findings), result.findings

    def test_within_project_supports_from_skimmed_basis_is_capped(self, tmp_path):
        fields = dict(_COMPLETE_FIELDS)
        fields["read_basis"] = "title-only"
        body = (
            _RESULT_BODY
            + "## Related papers\n\n"
            + "- [otherfinding](/findings/otherfinding.md) — SUPPORTS: "
            "agrees with the other finding's direction.\n"
        )
        note = _write_note(tmp_path / "notes" / "demo" / "findings" / "f3.md", fields, body=body)
        result = check_relate_presence(note)
        assert not result.ok
        assert any(
            "within-project" in f and "read_basis" in f for f in result.findings
        ), result.findings

    def test_provenance_edge_from_skimmed_basis_is_never_capped(self, tmp_path):
        """Structural/provenance edges (USES/PRODUCED/DERIVED-FROM/etc.)
        get NO cap regardless of read_basis — a PRODUCED edge is a fact
        about a run, not a claim requiring source fidelity."""
        fields = dict(_COMPLETE_FIELDS)
        fields["read_basis"] = "abstract-only"
        body = (
            _RESULT_BODY
            + "## Related papers\n\n"
            + "- [exp1](/experiments/exp1.md) — DERIVED-FROM: this finding "
            "derives from the run's analysis output.\n"
        )
        note = _write_note(tmp_path / "notes" / "demo" / "findings" / "f4.md", fields, body=body)
        result = check_relate_presence(note)
        assert not any("read_basis" in f for f in result.findings), result.findings
