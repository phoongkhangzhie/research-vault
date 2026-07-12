"""test_provenance_registration.py — the ``register_provenance`` tool op.

The mechanical half of the spine's "registration = provenance edges" move:
a completed run/score/analyze node is followed by a deterministic tool node
that AUTHORS the provenance edge(s) the step implies (PRODUCED to an
artifact, DERIVED-FROM/ADDRESSES/ANSWERS between project notes), rather than
declaring a bare ``produces:`` path contract with nothing wired to it.

Coverage:
  1. within-project kind writes a RECIPROCAL edge (both notes, converse tag).
  2. artifact kind writes ONE one-way edge (source note only).
  3. Multiple edges in one op call (the analyze-register shape).
  4. A one-way tag (PRODUCED/USES/GROUNDED-IN) on a within-project edge
     raises — never silently downgrades to a single-edge write (this is
     the writer's OWN guard, exercised through the op seam).
  5. An unknown ``kind`` raises loudly.
  6. A missing source note raises (FileNotFoundError, from the writer).
  7. registered via OP_REGISTRY / run_tool_op (the seam every other tool op
     is dispatched through — no bespoke second entry point).

All tests hermetic — no ~/vault, no real cluster, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import load_config
from research_vault.review.autonomy import OP_REGISTRY, run_tool_op, _op_register_provenance


def _write_note(path: Path, body: str = "# note\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntype: experiments\n---\n\n{body}", encoding="utf-8")


class TestRegisterProvenanceOp:
    def test_registered_in_op_registry(self):
        assert "register_provenance" in OP_REGISTRY
        assert OP_REGISTRY["register_provenance"] is _op_register_provenance

    def test_within_project_writes_reciprocal_converse_edge(self, tmp_instance):
        cfg = load_config()
        notes_dir = cfg.project_notes_dir("demo-research")
        _write_note(notes_dir / "findings" / "q1-main1.md")
        _write_note(notes_dir / "experiments" / "q1-main1.md")

        result = run_tool_op(
            "register_provenance",
            project="demo-research",
            edges=[{
                "kind": "within-project",
                "source_type": "findings", "source_id": "q1-main1",
                "target_type": "experiments", "target_id": "q1-main1",
                "tag": "DERIVED-FROM", "reason": "finding derived from this run",
            }],
        )
        assert len(result["edges_written"]) == 1

        finding_text = (notes_dir / "findings" / "q1-main1.md").read_text()
        exp_text = (notes_dir / "experiments" / "q1-main1.md").read_text()
        assert "DERIVED-FROM" in finding_text
        assert "/experiments/q1-main1.md" in finding_text
        # converse token on the target note (never the same token — the
        # unified typed-edge engine's _TAG_SYMMETRY asymmetric-mirror guarantee)
        assert "SHOWS" in exp_text
        assert "DERIVED-FROM" not in exp_text
        assert "/findings/q1-main1.md" in exp_text

    def test_artifact_kind_writes_one_way_edge_only(self, tmp_instance):
        cfg = load_config()
        notes_dir = cfg.project_notes_dir("demo-research")
        _write_note(notes_dir / "experiments" / "q1-main1.md")

        run_tool_op(
            "register_provenance",
            project="demo-research",
            edges=[{
                "kind": "artifact",
                "source_type": "experiments", "source_id": "q1-main1",
                "tag": "PRODUCED", "reason": "scores artifact",
                "artifact": "results/scores/q1-main1.csv",
            }],
        )
        exp_text = (notes_dir / "experiments" / "q1-main1.md").read_text()
        assert "PRODUCED" in exp_text
        assert "results/scores/q1-main1.csv" in exp_text
        # No second note was created/written for the artifact target.
        assert not (notes_dir / "results").exists()

    def test_multiple_edges_in_one_call(self, tmp_instance):
        """The analyze-register shape: one tool node, three edges."""
        cfg = load_config()
        notes_dir = cfg.project_notes_dir("demo-research")
        _write_note(notes_dir / "findings" / "q1-main1.md")
        _write_note(notes_dir / "experiments" / "q1-main1.md")
        _write_note(notes_dir / "gaps" / "q1-gap-main1.md")

        result = run_tool_op(
            "register_provenance",
            project="demo-research",
            edges=[
                {
                    "kind": "within-project",
                    "source_type": "findings", "source_id": "q1-main1",
                    "target_type": "experiments", "target_id": "q1-main1",
                    "tag": "DERIVED-FROM", "reason": "derived from this run",
                },
                {
                    "kind": "within-project",
                    "source_type": "experiments", "source_id": "q1-main1",
                    "target_type": "gaps", "target_id": "q1-gap-main1",
                    "tag": "ADDRESSES", "reason": "this experiment targets the gap",
                },
                {
                    "kind": "within-project",
                    "source_type": "findings", "source_id": "q1-main1",
                    "target_type": "gaps", "target_id": "q1-gap-main1",
                    "tag": "ANSWERS", "reason": "this finding answers the gap",
                },
            ],
        )
        assert len(result["edges_written"]) == 3

        gap_text = (notes_dir / "gaps" / "q1-gap-main1.md").read_text()
        assert "ADDRESSED-BY" in gap_text  # converse of ADDRESSES
        assert "ANSWERED-BY" in gap_text   # converse of ANSWERS

    def test_one_way_tag_on_within_project_raises(self, tmp_instance):
        """PRODUCED/USES/GROUNDED-IN are never auto-mirrored — the writer's
        own ValueError propagates through the op seam, never silently
        downgrades to a single-edge write."""
        cfg = load_config()
        notes_dir = cfg.project_notes_dir("demo-research")
        _write_note(notes_dir / "experiments" / "q1-main1.md")
        _write_note(notes_dir / "gaps" / "q1-gap-main1.md")

        with pytest.raises(ValueError, match="never auto-mirrored"):
            run_tool_op(
                "register_provenance",
                project="demo-research",
                edges=[{
                    "kind": "within-project",
                    "source_type": "experiments", "source_id": "q1-main1",
                    "target_type": "gaps", "target_id": "q1-gap-main1",
                    "tag": "PRODUCED", "reason": "bad tag for this kind",
                }],
            )

    def test_unknown_kind_raises(self, tmp_instance):
        cfg = load_config()
        notes_dir = cfg.project_notes_dir("demo-research")
        _write_note(notes_dir / "experiments" / "q1-main1.md")

        with pytest.raises(ValueError, match="unknown kind"):
            run_tool_op(
                "register_provenance",
                project="demo-research",
                edges=[{
                    "kind": "bogus",
                    "source_type": "experiments", "source_id": "q1-main1",
                    "tag": "PRODUCED", "reason": "x",
                }],
            )

    def test_missing_source_note_raises(self, tmp_instance):
        """A source note absent from disk is an integrity issue — never
        silently stubbed into existence (the writer's own contract)."""
        with pytest.raises(FileNotFoundError):
            run_tool_op(
                "register_provenance",
                project="demo-research",
                edges=[{
                    "kind": "artifact",
                    "source_type": "experiments", "source_id": "does-not-exist",
                    "tag": "PRODUCED", "reason": "x",
                    "artifact": "results/runs/does-not-exist.jsonl",
                }],
            )
