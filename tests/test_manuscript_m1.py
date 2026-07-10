"""test_manuscript_m1.py — PR-M1 acceptance tests: manuscript-loop type-generic core.

Coverage:
  1. manuscript/types — ManuscriptType registry
     1a. "lit-review" is registered
     1b. unknown key -> get_type returns None
     1c. all_type_keys() includes "lit-review"
  2. manuscript/style.py — the style seam
     2a. get_manuscript_style_preamble returns non-empty default
     2b. adopter [manuscript_style] preamble override
     2c. get_manuscript_section_tips covers every section_set entry
     2d. adopter [manuscript_style] section-key override
  3. manuscript cmd_new — per-manuscript folder scaffold
     3a. creates manuscripts/<slug>/_manuscript.md with manuscript_type field
     3b. creates report.md (RD-1: markdown reader path), references.md, sections/, figures/
     3c. unknown --type fails loudly (ValueError, no silent fallback)
     3d. re-creating an existing slug raises FileExistsError (no silent overwrite)
     3e. lit-review stub has phase1_builder=None -> cmd_new returns manifest=None
         (Phase-1 pass-through — design §1)
  4. manuscript cmd_expand — Phase-2 DAG
     4a. one node per section_set entry + assemble + approve-manuscript
     4b. validate_manifest passes
     4c. approve-manuscript is human-go, needs afterok assemble
     4d. missing _manuscript.md -> FileNotFoundError
     4e. section reads: include declared source_atoms + sections dir (absolute)
  5. manuscript cmd_review — PR-M5 stub
     5a. raises NotImplementedError (never silently no-ops)
  6. manuscript cmd_list
     6a. empty list when no manuscripts
     6b. lists after cmd_new
  7. CLI verb registry + rv help --check
     7a. "manuscript" in cli._VERB_REGISTRY with sr: "PR-M1"
     7b. rv help --check passes with manuscript verb present
  8. manuscript/verbs.py — parser wiring
     8a. new/expand/review/list subcommands parse
     8b. --type required on new
  9. Smoke: `rv manuscript new --type lit-review` scaffolds the folder end-to-end
     via the CLI entry point (main()).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.manuscript.types import (
    ManuscriptType,
    SectionSpec,
    get_type,
    all_type_keys,
    register_type,
)
from research_vault.manuscript.style import (
    get_manuscript_style_preamble,
    get_manuscript_section_tips,
)
from research_vault.dag.schema import validate_manifest


# ---------------------------------------------------------------------------
# 1. manuscript/types — ManuscriptType registry
# ---------------------------------------------------------------------------

def test_lit_review_type_registered():
    ms_type = get_type("lit-review")
    assert ms_type is not None
    assert ms_type.key == "lit-review"


def test_unknown_type_returns_none():
    assert get_type("nonexistent-type") is None


def test_all_type_keys_includes_lit_review():
    assert "lit-review" in all_type_keys()


def test_lit_review_section_set_nonempty_stub():
    """The section-set is non-empty so the machinery is exercisable.

    PR-M6: this is now the real 9-row survey table (design §3), superseding
    the PR-M1 stub's single placeholder section."""
    ms_type = get_type("lit-review")
    assert len(ms_type.section_set) >= 1


def test_lit_review_phase1_builder_is_real_now():
    """PR-M6: lit-review's real Phase-1 (framework selection, design §5) is
    now populated — supersedes the PR-M1 stub's pass-through."""
    ms_type = get_type("lit-review")
    assert ms_type.phase1_builder is not None


def test_passthrough_type_has_no_phase1():
    """A type with no framework/human-owned-shape step (phase1_builder=None)
    is still a valid, exercisable pass-through (design §1) — proven with a
    dedicated stub type, since lit-review itself is no longer pass-through
    (PR-M6)."""
    from research_vault.manuscript.types import ManuscriptType, SectionSpec, register_type

    passthrough_type = ManuscriptType(
        key="test-passthrough-type",
        section_set=(SectionSpec(name="draft"),),
    )
    register_type(passthrough_type)
    assert get_type("test-passthrough-type").phase1_builder is None


# ---------------------------------------------------------------------------
# 2. manuscript/style.py — the style seam
# ---------------------------------------------------------------------------

def test_style_preamble_nonempty_default():
    preamble = get_manuscript_style_preamble()
    assert isinstance(preamble, str) and preamble.strip()


def test_style_preamble_adopter_override(cfg):
    cfg._raw["manuscript_style"] = {"preamble": "CUSTOM PREAMBLE"}
    assert get_manuscript_style_preamble(config=cfg) == "CUSTOM PREAMBLE"


def test_section_tips_covers_every_section():
    ms_type = get_type("lit-review")
    tips = get_manuscript_section_tips(ms_type)
    for section in ms_type.section_set:
        key = section.brief_key or section.name
        assert key in tips
        assert tips[key].strip()


def test_section_tips_adopter_override(cfg):
    cfg._raw["manuscript_style"] = {"draft": "CUSTOM DRAFT TIP", "preamble": "ignored-here"}
    ms_type = get_type("lit-review")
    tips = get_manuscript_section_tips(ms_type, config=cfg)
    assert tips["draft"] == "CUSTOM DRAFT TIP"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_instance):
    from research_vault.config import load_config
    return load_config(reload=True)


# ---------------------------------------------------------------------------
# 3. manuscript cmd_new — per-manuscript folder scaffold
# ---------------------------------------------------------------------------

def test_cmd_new_scaffolds_manuscript_note(cfg):
    from research_vault.manuscript import cmd_new

    note_path, tree_root, manifest = cmd_new(
        "demo-research", "survey-eval", ms_type_key="lit-review", config=cfg,
    )
    assert note_path.exists()
    text = note_path.read_text(encoding="utf-8")
    assert "manuscript_type: lit-review" in text
    assert "type: manuscript" in text


def test_cmd_new_scaffolds_folder_shape(cfg):
    from research_vault.manuscript import cmd_new

    _, tree_root, _ = cmd_new(
        "demo-research", "survey-shape", ms_type_key="lit-review", config=cfg,
    )
    assert (tree_root / "report.md").exists()
    assert (tree_root / "references.md").exists()
    assert (tree_root / "sections").is_dir()
    assert (tree_root / "figures").is_dir()


def test_cmd_new_unknown_type_fails_loudly(cfg):
    from research_vault.manuscript import cmd_new

    with pytest.raises(ValueError, match="unknown --type"):
        cmd_new("demo-research", "survey-bad", ms_type_key="nonexistent-type", config=cfg)


def test_cmd_new_duplicate_slug_raises(cfg):
    from research_vault.manuscript import cmd_new

    cmd_new("demo-research", "survey-dup", ms_type_key="lit-review", config=cfg)
    with pytest.raises(FileExistsError):
        cmd_new("demo-research", "survey-dup", ms_type_key="lit-review", config=cfg)


def test_cmd_new_passthrough_type_returns_none_manifest(cfg):
    """A pass-through type (phase1_builder=None) -> no Phase-1 manifest emitted.

    PR-M6: lit-review itself now has a REAL Phase-1 (framework selection,
    design §5) — this is proven separately below
    (test_cmd_new_lit_review_emits_phase1_manifest). Pass-through behavior is
    proven here with a dedicated stub type (design §1's contract for a type
    with no framework/human-owned-shape step).
    """
    from research_vault.manuscript import cmd_new
    from research_vault.manuscript.types import ManuscriptType, SectionSpec, register_type

    passthrough_type = ManuscriptType(
        key="test-passthrough-type-2",
        section_set=(SectionSpec(name="draft"),),
    )
    register_type(passthrough_type)

    _, tree_root, manifest = cmd_new(
        "demo-research", "survey-passthrough", ms_type_key="test-passthrough-type-2", config=cfg,
    )
    assert manifest is None
    assert not (tree_root / "phase1-dag.json").exists()


def test_cmd_new_lit_review_emits_phase1_manifest(cfg):
    """PR-M6: lit-review's real Phase-1 (framework selection) IS emitted."""
    from research_vault.manuscript import cmd_new

    _, tree_root, manifest = cmd_new(
        "demo-research", "survey-lr-phase1", ms_type_key="lit-review", config=cfg,
    )
    assert manifest is not None
    assert (tree_root / "phase1-dag.json").exists()
    ids = [n["id"] for n in manifest["nodes"]]
    # framework-gate-autonomy design (option A, 2026-07-09): the single
    # framework-propose menu node is replaced by an N-lens ensemble ->
    # synthesize -> critic chain.
    assert ids[0] == "scope"
    assert ids[-3:] == ["framework-synthesize", "framework-critic", "approve-framework"]
    assert any(nid.startswith("framework-lens-") for nid in ids)
    hg = next(n for n in manifest["nodes"] if n["id"] == "approve-framework")
    assert hg["type"] == "human-go"


# ---------------------------------------------------------------------------
# 4. manuscript cmd_expand — Phase-2 DAG
# ---------------------------------------------------------------------------

def test_cmd_expand_emits_valid_manifest(cfg):
    from research_vault.manuscript import cmd_new, cmd_expand

    cmd_new("demo-research", "survey-expand", ms_type_key="lit-review", config=cfg)
    manifest = cmd_expand("demo-research", "survey-expand", config=cfg)
    validate_manifest(manifest)  # raises ManifestError if invalid


def test_cmd_expand_node_shape(cfg):
    """NG-7: lit-review's Phase-2 is the single-pass outline -> draft ->
    assemble -> approve-manuscript chain (design §2.2), replacing the
    PR-M6/RD-2/RD-4 8-row per-section chain — the 8-row section-set
    (types/lit_review.py SECTION_SET) is now SOURCE DATA consolidated into
    the single "draft" node's brief, not one DAG node per section."""
    from research_vault.manuscript import cmd_new, cmd_expand

    cmd_new("demo-research", "survey-shape2", ms_type_key="lit-review", config=cfg)
    manifest = cmd_expand("demo-research", "survey-shape2", config=cfg)
    ids = [n["id"] for n in manifest["nodes"]]
    assert ids == ["outline", "draft", "assemble", "approve-manuscript"]
    assert "prisma-scope" not in ids
    assert "framework" not in ids
    assert "introduction" not in ids
    assert ids.index("outline") < ids.index("draft") < ids.index("assemble") < ids.index("approve-manuscript")


def test_cmd_expand_approve_manuscript_is_human_go(cfg):
    from research_vault.manuscript import cmd_new, cmd_expand

    cmd_new("demo-research", "survey-hg", ms_type_key="lit-review", config=cfg)
    manifest = cmd_expand("demo-research", "survey-hg", config=cfg)
    node = next(n for n in manifest["nodes"] if n["id"] == "approve-manuscript")
    assert node["type"] == "human-go"
    assert node["needs"] == [{"from": "assemble", "edge": "afterok"}]


def test_cmd_expand_missing_note_raises(cfg):
    from research_vault.manuscript import cmd_expand

    with pytest.raises(FileNotFoundError):
        cmd_expand("demo-research", "no-such-slug", config=cfg)


def test_cmd_expand_reads_are_absolute(cfg):
    """Fix #34 lesson: reads: pointers must be absolute (survive tick-time
    project_root != project_notes_dir)."""
    from research_vault.manuscript import cmd_new, cmd_expand

    cmd_new("demo-research", "survey-reads", ms_type_key="lit-review", config=cfg)
    manifest = cmd_expand("demo-research", "survey-reads", config=cfg)
    draft_node = next(n for n in manifest["nodes"] if n["id"] == "draft")
    for r in draft_node["reads"]:
        assert Path(r).is_absolute(), f"reads: pointer not absolute: {r}"


def test_cmd_expand_empty_section_set_raises(cfg):
    """A type with an empty section_set surfaces loudly (never a fabricated manifest)."""
    from research_vault.manuscript import cmd_new, _build_phase2_manifest

    empty_type = ManuscriptType(key="empty-stub-type", section_set=())
    register_type(empty_type)
    try:
        note_path, tree_root, _ = cmd_new(
            "demo-research", "survey-empty", ms_type_key="empty-stub-type", config=cfg,
        )
        project_notes_dir = cfg.project_notes_dir("demo-research")
        with pytest.raises(ValueError, match="empty section_set"):
            _build_phase2_manifest(
                "demo-research", "survey-empty", empty_type, project_notes_dir, tree_root,
            )
    finally:
        # no unregister API — leaving a stray test type registered is harmless
        # (keys are opaque; no other test asserts an exact registry size)
        pass


# ---------------------------------------------------------------------------
# 5. manuscript cmd_review — PR-M5 landed the review-revise board; the loud
#    judge-not-configured guard (no NotImplementedError anymore) is covered
#    end-to-end in tests/test_manuscript_review_board.py::TestCmdReviewWiring.
# ---------------------------------------------------------------------------

def test_cmd_review_raises_loudly_with_no_judge_configured(cfg, monkeypatch):
    from research_vault.manuscript import cmd_new, cmd_review

    monkeypatch.delenv("RV_JUDGE_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cmd_new("demo-research", "survey-review-stub", ms_type_key="lit-review", config=cfg)
    with pytest.raises(RuntimeError, match="no judge configured"):
        cmd_review("demo-research", "survey-review-stub", config=cfg)


# ---------------------------------------------------------------------------
# 6. manuscript cmd_list
# ---------------------------------------------------------------------------

def test_cmd_list_empty(cfg):
    from research_vault.manuscript import cmd_list

    assert cmd_list("demo-research", config=cfg) == []


def test_cmd_list_after_new(cfg):
    from research_vault.manuscript import cmd_new, cmd_list

    cmd_new("demo-research", "survey-list", ms_type_key="lit-review", config=cfg)
    results = cmd_list("demo-research", config=cfg)
    slugs = [r["slug"] for r in results]
    assert "survey-list" in slugs


# ---------------------------------------------------------------------------
# 7. CLI verb registry + rv help --check
# ---------------------------------------------------------------------------

def test_manuscript_in_verb_registry():
    from research_vault.cli import _VERB_REGISTRY

    assert "manuscript" in _VERB_REGISTRY
    # PR-M9 (capstone): the "sr" tag now spans the full build wave, not just
    # PR-M1 — the when_to_use text was rewritten to describe the shipped
    # (post-M8) capability, not the PR-M1-era stub.
    assert _VERB_REGISTRY["manuscript"]["sr"] == "PR-M0..PR-M9"
    assert _VERB_REGISTRY["manuscript"]["when_to_use"].strip()


def test_help_check_passes_with_manuscript(tmp_instance):
    from research_vault.cli import main

    result = main(["help", "--check"])
    assert result == 0


# ---------------------------------------------------------------------------
# 8. manuscript/verbs.py — parser wiring
# ---------------------------------------------------------------------------

def test_verbs_new_parses():
    from research_vault.manuscript.verbs import build_parser

    p = build_parser()
    args = p.parse_args(["demo-research", "new", "survey-x", "--type", "lit-review"])
    assert args.manuscript_cmd == "new"
    assert args.slug == "survey-x"
    assert args.type == "lit-review"


def test_verbs_expand_parses():
    from research_vault.manuscript.verbs import build_parser

    p = build_parser()
    args = p.parse_args(["demo-research", "expand", "survey-x"])
    assert args.manuscript_cmd == "expand"


def test_verbs_review_parses():
    from research_vault.manuscript.verbs import build_parser

    p = build_parser()
    args = p.parse_args(["demo-research", "review", "survey-x"])
    assert args.manuscript_cmd == "review"


def test_verbs_list_parses():
    from research_vault.manuscript.verbs import build_parser

    p = build_parser()
    args = p.parse_args(["demo-research", "list"])
    assert args.manuscript_cmd == "list"


def test_verbs_new_requires_type():
    from research_vault.manuscript.verbs import build_parser

    p = build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["demo-research", "new", "survey-x"])  # missing --type


# ---------------------------------------------------------------------------
# 9. Smoke: `rv manuscript new --type <stub>` scaffolds the folder end-to-end
# ---------------------------------------------------------------------------

def test_smoke_cli_new_scaffolds_folder(tmp_instance):
    from research_vault.cli import main

    rc = main(["manuscript", "demo-research", "new", "survey-smoke", "--type", "lit-review"])
    assert rc == 0

    from research_vault.config import load_config
    cfg = load_config(reload=True)
    tree_root = cfg.project_notes_dir("demo-research") / "manuscripts" / "survey-smoke"
    assert (tree_root / "_manuscript.md").exists()
    assert (tree_root / "report.md").exists()
    assert (tree_root / "references.md").exists()
    assert (tree_root / "sections").is_dir()
    assert (tree_root / "figures").is_dir()


def test_smoke_cli_expand_then_validate(tmp_instance):
    """D1 (verb consolidation): `manuscript expand` is a HARD-REMOVED CLI
    stub — the manifest-emission logic it drove (manuscript.cmd_expand) is
    unchanged and still directly callable; this smoke test now exercises
    it that way instead of through the retired CLI verb."""
    from research_vault.cli import main

    rc1 = main(["manuscript", "demo-research", "new", "survey-smoke2", "--type", "lit-review"])
    assert rc1 == 0

    from research_vault.config import load_config
    from research_vault.manuscript import cmd_expand
    cfg = load_config(reload=True)
    cmd_expand("demo-research", "survey-smoke2", config=cfg)

    tree_root = cfg.project_notes_dir("demo-research") / "manuscripts" / "survey-smoke2"
    manifest_path = tree_root / "phase2-dag.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_manifest(manifest)
