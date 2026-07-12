"""tests/test_pr1_edge_engine_symmetry.py — the unified typed-edge engine
(PR-1 of the knowledge-graph model): family + symmetry maps, scope+target
grammar, family-slot validation, and the LOAD-BEARING converse-mirror
guard test.

THE GUARD TEST (the load-bearing PR-1 test)
================================================================================
Auto-mirroring an ASYMMETRIC tag with the SAME token, or auto-mirroring a
project→shared/artifact edge AT ALL, would silently plant a wrong or
invariant-breaking reverse edge (e.g. a shared note referencing a project
note). This module asserts, structurally:
  (a) no asymmetric tag (EXTENDS/DERIVED-FROM/ADDRESSES/ANSWERS) ever
      auto-mirrors with the SAME token — only its CONVERSE
      (FOUNDATION-FOR/SHOWS/ADDRESSED-BY/ANSWERED-BY respectively);
  (b) no project→shared (USES/GROUNDED-IN) or artifact-targeted (PRODUCED)
      edge auto-mirrors at all — append_bidirectional_edge refuses these
      tags outright (ValueError), forcing callers to a single-edge write.

Plant-the-failure proof: TestPlantTheFailure directly re-derives what the
PRE-FIX shipped default (same-token mirror for every tag, unconditionally)
would have produced for DERIVED-FROM, and asserts that is NOT what the
real code path writes — i.e. this test suite would go RED if
append_bidirectional_edge's converse-lookup regressed to the old
same-token default.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.review import incremental_relate as ir  # noqa: E402
from research_vault.review.relate_check import (  # noqa: E402
    _TAG_FAMILY,
    _TAG_SYMMETRY,
    parse_concept_edges,
    parse_paper_relations,
    parse_typed_edges,
)

_ASYMMETRIC_TAGS = ("EXTENDS", "DERIVED-FROM", "ADDRESSES", "ANSWERS")
_ASYMMETRIC_CONVERSE = {
    "EXTENDS": "FOUNDATION-FOR",
    "DERIVED-FROM": "SHOWS",
    "ADDRESSES": "ADDRESSED-BY",
    "ANSWERS": "ANSWERED-BY",
}
_SYMMETRIC_TAGS = ("SUPPORTS", "CONTRADICTS", "PARTIAL")
_NEVER_MIRRORED_TAGS = ("USES", "GROUNDED-IN", "PRODUCED")


def _write_note(literature_dir: Path, citekey: str) -> Path:
    literature_dir.mkdir(parents=True, exist_ok=True)
    path = literature_dir / f"{citekey}.md"
    path.write_text(f"---\ncitekey: {citekey}\n---\n\nplaceholder body\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# _TAG_FAMILY / _TAG_SYMMETRY — the maps, as built
# ---------------------------------------------------------------------------

class TestTagFamilyMap:
    def test_argumentative_tags(self):
        for tag in ("SUPPORTS", "CONTRADICTS", "PARTIAL", "EXTENDS", "FOUNDATION-FOR"):
            assert _TAG_FAMILY[tag] == "argumentative"

    def test_structural_tags(self):
        for tag in (
            "USES", "PRODUCED", "DERIVED-FROM", "SHOWS", "GROUNDED-IN",
            "ADDRESSES", "ADDRESSED-BY", "ANSWERS", "ANSWERED-BY",
        ):
            assert _TAG_FAMILY[tag] == "structural"

    def test_scored_is_not_in_the_vocabulary(self):
        """SCORED is dropped — folded into PRODUCED (prov:generated)."""
        assert "SCORED" not in _TAG_FAMILY


class TestTagSymmetryMap:
    def test_symmetric_tags_self_converse(self):
        for tag in _SYMMETRIC_TAGS:
            assert _TAG_SYMMETRY[tag] == tag

    def test_asymmetric_tags_map_to_converse(self):
        for tag, converse in _ASYMMETRIC_CONVERSE.items():
            assert _TAG_SYMMETRY[tag] == converse
            # and the converse maps back to the original — round-trips,
            # never a third token.
            assert _TAG_SYMMETRY[converse] == tag

    def test_never_mirrored_tags_absent_from_symmetry_map(self):
        for tag in _NEVER_MIRRORED_TAGS:
            assert tag not in _TAG_SYMMETRY


# ---------------------------------------------------------------------------
# (a) Guard test — no asymmetric tag ever auto-mirrors the SAME token
# ---------------------------------------------------------------------------

class TestAsymmetricNeverSameTokenMirror:
    @pytest.mark.parametrize("tag,converse", list(_ASYMMETRIC_CONVERSE.items()))
    def test_default_mirror_is_the_converse(self, tmp_path, tag, converse):
        """Exercises append_bidirectional_edge's symmetry-driven default
        directly. Asserted on the RAW written text, not through
        parse_paper_relations: append_bidirectional_edge writes to a
        `/literature/<citekey>.md` target (this wave's only wired write
        path — see append_related_papers_edge), which is an intra-shared
        ARGUMENTATIVE-only slot (PR-1 family-slot validation, tested
        separately in TestFamilySlotValidation). DERIVED-FROM/ADDRESSES/
        ANSWERS are STRUCTURAL tags whose real target scope is
        within-project (PR-4 generalizes the write ROUTING; PR-1 only
        fixes the symmetry-selection LOGIC this function uses regardless
        of where it writes) — so the round-trip-through-the-parser
        assertion would conflate two different PRs' scope. Reading the
        raw text isolates exactly what this PR changed: the TOKEN chosen
        for the mirror.
        """
        literature_dir = tmp_path / "literature"
        _write_note(literature_dir, "sourceA")
        _write_note(literature_dir, "targetB")

        ir.append_bidirectional_edge(
            literature_dir, "sourceA", "targetB",
            new_tag=tag, new_reason="a real, considered reasoning clause",
        )
        source_text = (literature_dir / "sourceA.md").read_text(encoding="utf-8")
        target_text = (literature_dir / "targetB.md").read_text(encoding="utf-8")
        assert f"— {tag}: a real, considered reasoning clause" in source_text
        # THE GUARD: the mirror on the target note carries the CONVERSE,
        # never the same token.
        assert f"— {converse}: a real, considered reasoning clause" in target_text
        assert f"— {tag}: a real, considered reasoning clause" not in target_text

    @pytest.mark.parametrize("tag", _SYMMETRIC_TAGS)
    def test_symmetric_tags_still_mirror_same_token(self, tmp_path, tag):
        """The converse machinery ONLY bites the four asymmetric tags —
        symmetric tags are unaffected (same behavior as pre-PR-1)."""
        literature_dir = tmp_path / "literature"
        _write_note(literature_dir, "sourceC")
        _write_note(literature_dir, "targetD")

        ir.append_bidirectional_edge(
            literature_dir, "sourceC", "targetD",
            new_tag=tag, new_reason="a real, considered reasoning clause",
        )
        target_edges = parse_paper_relations(
            (literature_dir / "targetD.md").read_text(encoding="utf-8")
        )
        assert any(e["tag"] == tag and e["target"] == "sourceC" for e in target_edges.edges)


# ---------------------------------------------------------------------------
# (b) Guard test — no project→shared or artifact edge auto-mirrors AT ALL
# ---------------------------------------------------------------------------

class TestNeverMirroredTagsRefuseBidirectionalWrite:
    @pytest.mark.parametrize("tag", _NEVER_MIRRORED_TAGS)
    def test_append_bidirectional_edge_refuses(self, tmp_path, tag):
        literature_dir = tmp_path / "literature"
        _write_note(literature_dir, "sourceE")
        _write_note(literature_dir, "targetF")

        with pytest.raises(ValueError, match="never auto-mirrored"):
            ir.append_bidirectional_edge(
                literature_dir, "sourceE", "targetF",
                new_tag=tag, new_reason="a real, considered reasoning clause",
            )
        # Neither note was mutated — the refusal happens BEFORE any write
        # (never a half-written bidirectional pair).
        source_edges = parse_paper_relations(
            (literature_dir / "sourceE.md").read_text(encoding="utf-8")
        )
        target_edges = parse_paper_relations(
            (literature_dir / "targetF.md").read_text(encoding="utf-8")
        )
        assert source_edges.edges == []
        assert target_edges.edges == []

    def test_single_edge_write_still_works_for_never_mirrored_tags(self, tmp_path):
        """The one-way write path (append_related_papers_edge, a single
        note, no auto-mirror) remains available for USES/GROUNDED-IN/
        PRODUCED — only the BIDIRECTIONAL convenience wrapper refuses
        them. Asserted on raw text (see TestAsymmetricNeverSameTokenMirror
        for why: append_related_papers_edge's only wired target this wave
        is `/literature/...`, an argumentative-only intra-shared slot;
        USES is structural, so the round-trip parse correctly rejects it
        as a family-slot violation — this test isolates the one-way WRITE
        mechanism, not the target scope, which is PR-4's concern)."""
        literature_dir = tmp_path / "literature"
        _write_note(literature_dir, "sourceG")
        ir.append_related_papers_edge(
            literature_dir / "sourceG.md",
            display="run1", target="run1", tag="USES",
            reason="grounds the experiment in this paper's method",
        )
        text = (literature_dir / "sourceG.md").read_text(encoding="utf-8")
        assert "— USES: grounds the experiment in this paper's method" in text


# ---------------------------------------------------------------------------
# Plant-the-failure: re-derive the pre-fix behavior and prove it differs
# ---------------------------------------------------------------------------

class TestPlantTheFailure:
    def test_pre_fix_same_token_default_would_have_written_derived_from_both_ways(self, tmp_path):
        """This is the literal defect the guard exists to prevent: had
        append_bidirectional_edge kept the pre-PR-1 default ("candidate_tag
        if candidate_tag is not None else new_tag" — same token,
        unconditionally), a DERIVED-FROM write would have mirrored as
        DERIVED-FROM on both notes. Confirm the REAL code path does NOT do
        this — the mirror is SHOWS, not DERIVED-FROM."""
        literature_dir = tmp_path / "literature"
        _write_note(literature_dir, "expX")
        _write_note(literature_dir, "findingY")

        ir.append_bidirectional_edge(
            literature_dir, "findingY", "expX",
            new_tag="DERIVED-FROM", new_reason="the finding derives from this run's analysis",
        )
        exp_text = (literature_dir / "expX.md").read_text(encoding="utf-8")
        # The pre-fix bug shape: DERIVED-FROM mirrored as DERIVED-FROM.
        pre_fix_would_have_written = (
            "— DERIVED-FROM: the finding derives from this run's analysis" in exp_text
        )
        assert pre_fix_would_have_written is False
        # The fixed shape: the converse SHOWS.
        assert "— SHOWS: the finding derives from this run's analysis" in exp_text


# ---------------------------------------------------------------------------
# Scope + target grammar (widened _LINK_PROBE_RE / _EDGE_LINE_RE)
# ---------------------------------------------------------------------------

class TestScopeGrammar:
    def test_intra_shared_literature_unchanged(self):
        body = "## Related papers\n- [X](/literature/smith2024.md) — SUPPORTS: replicates the effect here today\n"
        parsed = parse_paper_relations(body)
        assert parsed.edges == [{
            "tag": "SUPPORTS", "target": "smith2024",
            "reason": "replicates the effect here today",
            "type": "reciprocal", "kind_mismatch": None,
        }]
        assert parsed.malformed == []

    def test_intra_shared_concepts_unchanged(self):
        body = "## Concept edges\n- [X](/concepts/weird-default.md) — CONTRADICTS: pushes back on the default here\n"
        parsed = parse_concept_edges(body)
        assert parsed.edges == [{
            "tag": "CONTRADICTS", "target": "weird-default",
            "reason": "pushes back on the default here",
        }]

    def test_cross_bundle_okf_edge(self):
        body = "- [E1](okf:literature/smith2024.md) — USES: grounds the design in this method\n"
        parsed = parse_typed_edges(body)
        assert len(parsed.edges) == 1
        e = parsed.edges[0]
        assert e["scope"] == "cross-bundle"
        assert e["family"] == "structural"
        assert e["tag"] == "USES"
        assert e["target"] == "okf:literature/smith2024.md"

    def test_within_project_edge(self):
        body = "- [F1](/findings/f1.md) — DERIVED-FROM: analysis of the main run here\n"
        parsed = parse_typed_edges(body)
        assert len(parsed.edges) == 1
        e = parsed.edges[0]
        assert e["scope"] == "within-project"
        assert e["family"] == "structural"
        assert e["target"] == "/findings/f1.md"

    def test_artifact_edge(self):
        body = "- [scores](results/scores/hfs-landscape.csv) — PRODUCED: the computed scores csv here\n"
        parsed = parse_typed_edges(body)
        assert len(parsed.edges) == 1
        e = parsed.edges[0]
        assert e["scope"] == "artifact"
        assert e["family"] == "structural"
        assert e["target"] == "results/scores/hfs-landscape.csv"


# ---------------------------------------------------------------------------
# Family-slot validation — reject a family-mismatched tag in a slot
# ---------------------------------------------------------------------------

class TestFamilySlotValidation:
    def test_structural_tag_on_intra_shared_literature_is_malformed(self):
        """A '## Related papers' line may only carry an argumentative
        tag — a structural tag there is a family-slot violation."""
        body = "## Related papers\n- [X](/literature/smith2024.md) — USES: this is family-mismatched here\n"
        parsed = parse_paper_relations(body)
        assert parsed.edges == []
        assert len(parsed.malformed) == 1
        assert "USES" in parsed.malformed[0]

    def test_structural_tag_on_intra_shared_concepts_is_malformed(self):
        body = "## Concept edges\n- [X](/concepts/weird-default.md) — PRODUCED: also family-mismatched here\n"
        parsed = parse_paper_relations(body)
        assert len(parsed.malformed) == 1

    def test_argumentative_tag_on_artifact_is_malformed(self):
        """A registration edge (artifact target) may only carry a
        provenance/structural tag — an artifact is not claim-bearing."""
        body = "- [scores](results/scores/hfs.csv) — SUPPORTS: an artifact cannot be supported like this\n"
        parsed = parse_paper_relations(body)
        assert parsed.edges == []
        assert len(parsed.malformed) == 1
        assert "SUPPORTS" in parsed.malformed[0]

    def test_cross_bundle_accepts_either_family(self):
        body = (
            "- [E1](okf:literature/smith2024.md) — USES: structural is fine here today\n"
            "- [F1](okf:literature/jones2023.md) — SUPPORTS: argumentative is also fine here\n"
        )
        parsed = parse_typed_edges(body)
        assert {e["tag"] for e in parsed.edges} == {"USES", "SUPPORTS"}

    def test_within_project_accepts_either_family(self):
        body = (
            "- [F1](/findings/f1.md) — DERIVED-FROM: structural is fine here today\n"
            "- [F2](/findings/f2.md) — CONTRADICTS: argumentative is also fine here\n"
        )
        parsed = parse_typed_edges(body)
        assert {e["tag"] for e in parsed.edges} == {"DERIVED-FROM", "CONTRADICTS"}


# ---------------------------------------------------------------------------
# Golden discipline — existing edges parse byte-identically
# ---------------------------------------------------------------------------

class TestGoldenByteIdentical:
    def test_existing_paper_to_paper_edges_unchanged(self):
        body = (
            "## Related papers\n"
            "- [Baltaji 2024](/literature/baltajipersonainconstancymulti2024.md) "
            "— SUPPORTS: replicates the persona-inconstancy effect (reciprocal)\n"
            "- [Jones 2023](/literature/jones2023.md) — CONTRADICTS: "
            "reports the opposite direction on this benchmark\n"
        )
        parsed = parse_paper_relations(body)
        assert len(parsed.edges) == 2
        assert parsed.malformed == []
        assert parsed.edges[0]["tag"] == "SUPPORTS"
        assert parsed.edges[0]["type"] == "reciprocal"
        assert parsed.edges[1]["tag"] == "CONTRADICTS"
        assert parsed.edges[1]["type"] == "refutational"

    def test_existing_paper_to_concept_edges_unchanged(self):
        body = (
            "## Concept edges\n"
            "- [WEIRD default](/concepts/western-consensus-default.md) — "
            "SUPPORTS: directly supports the WEIRD-default concept here\n"
        )
        parsed = parse_concept_edges(body)
        assert parsed.edges == [{
            "tag": "SUPPORTS", "target": "western-consensus-default",
            "reason": "directly supports the WEIRD-default concept here",
        }]
