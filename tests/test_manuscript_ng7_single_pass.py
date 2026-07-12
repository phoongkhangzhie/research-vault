"""test_manuscript_ng7_single_pass.py — NG-7: single-pass manuscript.

Next-gen lit-review design §2: replaces the 9-node (RD-2/RD-4: 8-node)
per-section chain with `outline -> draft -> assemble` — one subagent holds
the whole survey for coherence. Load-bearing acceptance per the dispatch
brief: the single-pass draft CONSUMES PR-2's paper->paper typed edges (via
`review.relations_report`, traversed, not re-derived).

sr: NG-lit-review-waveB (NG-7)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.manuscript.types.lit_review import (
    check_outline_gate,
    render_relations_ledger,
    _get_single_pass_corpus_ceiling,
    _corpus_size,
)


# ---------------------------------------------------------------------------
# check_outline_gate — the cheap, rejects-only pre-pass screen
# ---------------------------------------------------------------------------

def test_outline_gate_missing_file_fails(tmp_path: Path):
    issues = check_outline_gate(tmp_path / "_outline.md", ["branch-a"])
    assert issues
    assert any("not found" in i for i in issues)


def test_outline_gate_unanchored_branch_fails(tmp_path: Path):
    outline = tmp_path / "_outline.md"
    outline.write_text("Some prose with no branch mention at all.\n", encoding="utf-8")
    issues = check_outline_gate(outline, ["representation-learning"])
    assert any("representation-learning" in i for i in issues)


def test_outline_gate_no_exemplar_move_citation_fails(tmp_path: Path):
    outline = tmp_path / "_outline.md"
    outline.write_text(
        "## representation-learning\n\n"
        "Thesis: X. Compares [[smith2023]] and [[jones2022]].\n",
        encoding="utf-8",
    )
    issues = check_outline_gate(outline, ["representation-learning"])
    assert any("exemplar-move" in i for i in issues)


def test_outline_gate_fewer_than_two_papers_fails(tmp_path: Path):
    outline = tmp_path / "_outline.md"
    outline.write_text(
        "## representation-learning\n\n"
        "Thesis: X. Imitates e07 (comparison-synthesis move). "
        "Compares [[smith2023]] only.\n",
        encoding="utf-8",
    )
    issues = check_outline_gate(outline, ["representation-learning"])
    assert any("papers" in i.lower() for i in issues)


def test_outline_gate_complete_passes(tmp_path: Path):
    outline = tmp_path / "_outline.md"
    outline.write_text(
        "## representation-learning\n\n"
        "Thesis: representation-learning unifies X and Y. "
        "Anchors: concepts/repr-learning.md, gaps/gap-01.md. "
        "Compares [[smith2023]] and [[jones2022]]. "
        "Imitates e07 (comparison-synthesis move).\n",
        encoding="utf-8",
    )
    issues = check_outline_gate(outline, ["representation-learning"])
    assert issues == []


def test_outline_gate_normalizes_quoted_frozen_branch(tmp_path: Path):
    """A frozen branch supplied with literal surrounding quotes (the parser-
    origin bug: a YAML block-list item like `- "survey to behaviour"` used to
    parse with the quote characters baked into the value) must still match a
    PLAIN heading in the outline — the gate normalizes both sides (strip
    quotes, casefold, collapse whitespace) before the containment check.
    """
    outline = tmp_path / "_outline.md"
    outline.write_text(
        "## survey to behaviour\n\n"
        "Thesis: X unifies Y and Z. "
        "Compares [[smith2023]] and [[jones2022]]. "
        "Imitates e07 (comparison-synthesis move).\n",
        encoding="utf-8",
    )
    issues_quoted = check_outline_gate(outline, ['"survey to behaviour"'])
    assert issues_quoted == [], f"Expected no findings for quoted branch, got {issues_quoted}"

    issues_bare = check_outline_gate(outline, ["survey to behaviour"])
    assert issues_bare == [], f"Expected no findings for bare branch, got {issues_bare}"


def test_outline_gate_still_rejects_genuinely_absent_branch(tmp_path: Path):
    """Normalization must not neuter the gate: a branch truly absent from the
    outline (not just differently-quoted) must still be flagged."""
    outline = tmp_path / "_outline.md"
    outline.write_text(
        "## survey to behaviour\n\n"
        "Thesis: X unifies Y and Z. "
        "Compares [[smith2023]] and [[jones2022]]. "
        "Imitates e07 (comparison-synthesis move).\n",
        encoding="utf-8",
    )
    issues = check_outline_gate(outline, ['"a completely different branch"'])
    assert any("completely different branch" in i for i in issues), (
        f"Expected the genuinely-absent branch to still be flagged, got {issues}"
    )


def test_outline_gate_no_frozen_branches_is_a_noop():
    """No frozen branches -> nothing to anchor -> vacuously OK (never a
    forced failure on an empty spine — that's check_framework_gate's job)."""
    issues = check_outline_gate(Path("/nonexistent-but-branches-empty.md"), [])
    # Still fails on the missing file (a real check), but with an empty
    # branches list there's no per-branch anchoring/exemplar/paper-count
    # requirement layered on top.
    assert len(issues) == 1
    assert "not found" in issues[0]


# ---------------------------------------------------------------------------
# render_relations_ledger — PR-2 consume seam (Wave 0)
# ---------------------------------------------------------------------------

def _write_lit_note_with_relation(
    literature_dir: Path, citekey: str, target: str, tag: str = "SUPPORTS", reason: str = "shares the method"
) -> None:
    literature_dir.mkdir(parents=True, exist_ok=True)
    (literature_dir / f"{citekey}.md").write_text(
        "---\ntype: literature\ntitle: A Paper\ncitekey: " + citekey + "\n---\n\n"
        "## Related papers\n\n"
        f"- [{tag}] [{target}](/literature/{target}.md) — {reason}\n",
        encoding="utf-8",
    )


def test_render_relations_ledger_traverses_pr2_edges(tmp_path: Path):
    from research_vault.config import Config, _default_config, _expand_paths, Config as _CfgCls

    project_notes_dir = tmp_path / "notes"
    _write_lit_note_with_relation(project_notes_dir / "literature", "xiong2023", "smith2022", tag="SUPPORTS")
    _write_lit_note_with_relation(project_notes_dir / "literature", "smith2022", "xiong2023", tag="CONTRADICTS")

    # Build a minimal Config whose project_notes_dir resolves to project_notes_dir.
    raw = _default_config()
    raw["projects"] = {"demo": {"source_dir": str(project_notes_dir)}}
    raw = _expand_paths(raw, tmp_path)
    cfg = _CfgCls(raw)

    ledger = render_relations_ledger("demo", "any-scope", config=cfg)
    assert "xiong2023" in ledger
    assert "smith2022" in ledger
    # relations_report derives the typed relation kind from the tag
    # (SUPPORTS -> reciprocal) — the ledger traverses the derived kind.
    assert "reciprocal" in ledger
    assert "TRAVERSE" in ledger.upper()


def test_render_relations_ledger_empty_corpus_is_honest(tmp_path: Path):
    from research_vault.config import Config as _CfgCls, _default_config, _expand_paths

    project_notes_dir = tmp_path / "notes"
    raw = _default_config()
    raw["projects"] = {"demo": {"source_dir": str(project_notes_dir)}}
    raw = _expand_paths(raw, tmp_path)
    cfg = _CfgCls(raw)

    ledger = render_relations_ledger("demo", "any-scope", config=cfg)
    assert "no paper->paper typed edges" in ledger.lower() or "no paper" in ledger.lower()


# ---------------------------------------------------------------------------
# single_pass_corpus_ceiling config resolution
# ---------------------------------------------------------------------------

def test_single_pass_corpus_ceiling_default():
    assert _get_single_pass_corpus_ceiling(None) == 40


def test_single_pass_corpus_ceiling_override():
    class _FakeCfg:
        _raw = {"manuscript_lit_review": {"single_pass_corpus_ceiling": 12}}

    assert _get_single_pass_corpus_ceiling(_FakeCfg()) == 12


def test_corpus_size_zero_when_no_frozen_corpus(tmp_path: Path):
    assert _corpus_size(tmp_path, "no-such-scope") == 0


def test_corpus_size_counts_frozen_citekeys(tmp_path: Path):
    review_dir = tmp_path / "reviews" / "scope-a"
    review_dir.mkdir(parents=True)
    (review_dir / "_corpus.md").write_text(
        "| status | citekey |\n| --- | --- |\n"
        "| [NEW] | a2024 |\n| [NEW] | b2024 |\n| [NEW] | c2024 |\n",
        encoding="utf-8",
    )
    assert _corpus_size(tmp_path, "scope-a") == 3


# ---------------------------------------------------------------------------
# phase2_builder — the real end-to-end single-pass manifest
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_instance):
    from research_vault.config import load_config
    return load_config(reload=True)


def _freeze_spine(note_path: Path, *, spine_shape: str, branches: list[str]) -> None:
    text = note_path.read_text(encoding="utf-8")
    lines = text.split("\n")
    new_lines = []
    skip = False
    for line in lines:
        if line.startswith("spine_shape:"):
            new_lines.append(f"spine_shape: {spine_shape}")
            continue
        if line.startswith("branches:"):
            skip = True
            new_lines.append("branches:")
            for b in branches:
                new_lines.append(f"  - {b}")
            continue
        if skip and line.startswith("  - "):
            continue
        skip = False
        new_lines.append(line)
    body_start = text.index("\n---\n", text.index("---\n") + 4) + 5
    body = text[body_start - 5:]
    insert_at = max(i for i, ln in enumerate(new_lines) if ln == "---")
    note_path.write_text("\n".join(new_lines[:insert_at + 1]) + "\n" + "\n".join(
        new_lines[insert_at + 1:]
    ).lstrip("\n") + "\n", encoding="utf-8")


def test_phase2_builder_default_single_pass_below_ceiling(cfg):
    """Below single_pass_corpus_ceiling: outline -> draft -> assemble ->
    approve-manuscript, no fan-out, no label-manifest node."""
    from research_vault.manuscript import cmd_new, cmd_expand

    note_path, tree_root, _ = cmd_new(
        "demo-research", "survey-ng7-default", ms_type_key="lit-review", config=cfg,
    )
    manifest = cmd_expand("demo-research", "survey-ng7-default", config=cfg)
    ids = [n["id"] for n in manifest["nodes"]]
    assert ids == ["outline", "draft", "assemble", "approve-manuscript"]
    outline_node = next(n for n in manifest["nodes"] if n["id"] == "outline")
    assert outline_node["produces"]["_outline.md"] == str(tree_root / "_outline.md")
    draft_node = next(n for n in manifest["nodes"] if n["id"] == "draft")
    assert draft_node["needs"] == [{"from": "outline", "edge": "afterok"}]


def test_phase2_builder_draft_consumes_pr2_relations_ledger(cfg):
    """★ Load-bearing acceptance: the single-pass draft brief consumes PR-2's
    paper->paper typed edges (review.relations_report), traversed — not
    re-derived from prose."""
    from research_vault.manuscript import cmd_new, cmd_expand

    project_notes_dir = cfg.project_notes_dir("demo-research")
    _write_lit_note_with_relation(project_notes_dir / "literature", "xiong2023", "smith2022", tag="SUPPORTS")
    _write_lit_note_with_relation(project_notes_dir / "literature", "smith2022", "xiong2023", tag="CONTRADICTS")

    cmd_new("demo-research", "survey-ng7-relations", ms_type_key="lit-review", config=cfg)
    manifest = cmd_expand("demo-research", "survey-ng7-relations", config=cfg)
    draft_spec = next(n["spec"] for n in manifest["nodes"] if n["id"] == "draft")

    assert "xiong2023" in draft_spec
    assert "smith2022" in draft_spec
    assert "TRAVERSE" in draft_spec.upper()


def test_phase2_builder_fan_out_above_ceiling(cfg):
    """Above single_pass_corpus_ceiling (D3's fan-out path): per-branch
    draft-<branch> nodes + a coherence node with the label-manifest check,
    instead of one "draft" node."""
    from research_vault.manuscript import cmd_new, cmd_expand
    import research_vault.config as _cfg_mod

    # Force the ceiling low + a frozen corpus above it.
    cfg._raw.setdefault("manuscript_lit_review", {})["single_pass_corpus_ceiling"] = 2
    project_notes_dir = cfg.project_notes_dir("demo-research")
    review_dir = project_notes_dir / "reviews" / "survey-ng7-fanout"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "_corpus.md").write_text(
        "| status | citekey |\n| --- | --- |\n"
        "| [NEW] | a2024 |\n| [NEW] | b2024 |\n| [NEW] | c2024 |\n",
        encoding="utf-8",
    )

    note_path, tree_root, _ = cmd_new(
        "demo-research", "survey-ng7-fanout", ms_type_key="lit-review", config=cfg,
    )
    _freeze_spine(note_path, spine_shape="pipeline", branches=["alpha", "beta"])

    manifest = cmd_expand("demo-research", "survey-ng7-fanout", config=cfg)
    ids = [n["id"] for n in manifest["nodes"]]
    assert "draft" not in ids
    assert "draft-alpha" in ids
    assert "draft-beta" in ids
    assert "coherence" in ids
    coherence_node = next(n for n in manifest["nodes"] if n["id"] == "coherence")
    assert coherence_node["needs"] == [
        {"from": "draft-alpha", "edge": "afterok"},
        {"from": "draft-beta", "edge": "afterok"},
    ]
    assert "label-manifest" in coherence_node["spec"].lower() or "label manifest" in coherence_node["spec"].lower()
    assemble_node = next(n for n in manifest["nodes"] if n["id"] == "assemble")
    assert assemble_node["needs"] == [{"from": "coherence", "edge": "afterok"}]


# ---------------------------------------------------------------------------
# check_outline_gate wired into `rv dag complete` at the "outline" node
# ---------------------------------------------------------------------------

def test_dag_complete_outline_gate_blocks_unanchored_branch(cfg, tmp_path: Path):
    import argparse
    from research_vault.dag.verbs import cmd_complete
    from research_vault.dag.store import RunState, RunStore

    from research_vault.manuscript import cmd_new, cmd_expand

    note_path, tree_root, _ = cmd_new(
        "demo-research", "survey-ng7-outline-gate", ms_type_key="lit-review", config=cfg,
    )
    _freeze_spine(note_path, spine_shape="pipeline", branches=["representation-learning"])
    manifest = cmd_expand("demo-research", "survey-ng7-outline-gate", config=cfg)

    # An outline that never mentions the frozen branch -> gate must BLOCK.
    (tree_root / "_outline.md").write_text("Nothing about any branch here.\n", encoding="utf-8")

    manifest_path = tree_root / "phase2-dag.json"
    store = RunStore.from_config(cfg)
    rs = RunState(run_id=manifest["run_id"], manifest_path=str(manifest_path))
    rs.init_nodes(manifest)
    rs.set_node_status("outline", "running")
    store.create(rs)

    args = argparse.Namespace(
        run_id=manifest["run_id"], node_id="outline", status="succeeded",
        error=None, error_file=None,
    )
    rc = cmd_complete(args)
    assert rc == 1


def test_dag_complete_outline_gate_passes_anchored_branch(cfg, tmp_path: Path):
    import argparse
    from research_vault.dag.verbs import cmd_complete
    from research_vault.dag.store import RunState, RunStore

    from research_vault.manuscript import cmd_new, cmd_expand

    note_path, tree_root, _ = cmd_new(
        "demo-research", "survey-ng7-outline-gate-ok", ms_type_key="lit-review", config=cfg,
    )
    _freeze_spine(note_path, spine_shape="pipeline", branches=["representation-learning"])
    manifest = cmd_expand("demo-research", "survey-ng7-outline-gate-ok", config=cfg)

    (tree_root / "_outline.md").write_text(
        "## representation-learning\n\n"
        "Thesis: X. Compares [[smith2023]] and [[jones2022]]. "
        "Imitates e07 (comparison-synthesis move).\n",
        encoding="utf-8",
    )

    manifest_path = tree_root / "phase2-dag.json"
    store = RunStore.from_config(cfg)
    rs = RunState(run_id=manifest["run_id"], manifest_path=str(manifest_path))
    rs.init_nodes(manifest)
    rs.set_node_status("outline", "running")
    store.create(rs)

    args = argparse.Namespace(
        run_id=manifest["run_id"], node_id="outline", status="succeeded",
        error=None, error_file=None,
    )
    rc = cmd_complete(args)
    assert rc == 0
    assert store.load(manifest["run_id"]).node_status("outline") == "succeeded"


# ---------------------------------------------------------------------------
# NG-7 §2.6 — `rv manuscript new --from-review <scope>` + warn-at-creation
# ---------------------------------------------------------------------------

def test_cmd_new_from_review_adopts_scope_as_slug(cfg):
    from research_vault.manuscript import cmd_new

    note_path, tree_root, _ = cmd_new(
        "demo-research", ms_type_key="lit-review", config=cfg,
        from_review="survey-ng7-scope-a",
    )
    assert tree_root.name == "survey-ng7-scope-a"
    assert note_path.exists()


def test_cmd_new_no_slug_no_from_review_raises(cfg):
    from research_vault.manuscript import cmd_new

    with pytest.raises(ValueError, match="slug is required"):
        cmd_new("demo-research", ms_type_key="lit-review", config=cfg)


def test_cmd_new_explicit_slug_differs_from_review_warns(cfg):
    from research_vault.manuscript import cmd_new

    with pytest.warns(UserWarning, match="differs from"):
        cmd_new(
            "demo-research", "survey-explicit-slug", ms_type_key="lit-review",
            config=cfg, from_review="survey-other-scope",
        )


def test_cmd_new_slug_with_no_matching_corpus_warns_at_creation(cfg):
    from research_vault.manuscript import cmd_new

    with pytest.warns(UserWarning, match="no frozen review corpus"):
        cmd_new("demo-research", "survey-no-corpus-yet", ms_type_key="lit-review", config=cfg)


def test_cmd_new_slug_with_matching_corpus_no_warning(cfg, recwarn):
    from research_vault.manuscript import cmd_new

    project_notes_dir = cfg.project_notes_dir("demo-research")
    review_dir = project_notes_dir / "reviews" / "survey-has-corpus"
    review_dir.mkdir(parents=True)
    (review_dir / "_corpus.md").write_text(
        "| status | citekey |\n| --- | --- |\n| [NEW] | a2024 |\n", encoding="utf-8",
    )
    cmd_new("demo-research", "survey-has-corpus", ms_type_key="lit-review", config=cfg)
    corpus_warnings = [w for w in recwarn.list if "no frozen review corpus" in str(w.message)]
    assert corpus_warnings == []


# ---------------------------------------------------------------------------
# HR-craft rec 5 (design §7) — the deterministic H2-order diff
# ---------------------------------------------------------------------------

def test_heading_order_matches_frozen_contract():
    from research_vault.manuscript.check_gates import check_heading_order

    expected = ("introduction", "thematic-sections", "conclusion")
    draft_text = "## Introduction\n\nfoo\n\n## Thematic-sections\n\nbar\n\n## Conclusion\n\nbaz\n"
    result = check_heading_order(draft_text, expected)
    assert result["ok"] is True
    assert result["warnings"] == []


def test_heading_order_out_of_order_signals():
    from research_vault.manuscript.check_gates import check_heading_order

    expected = ("introduction", "thematic-sections", "conclusion")
    draft_text = "## Conclusion\n\nbaz\n\n## Introduction\n\nfoo\n\n## Thematic-sections\n\nbar\n"
    result = check_heading_order(draft_text, expected)
    assert result["ok"] is False
    assert any("heading-order" in w for w in result["warnings"])


def test_heading_order_fewer_than_two_matches_is_ok():
    from research_vault.manuscript.check_gates import check_heading_order

    result = check_heading_order("## Introduction\n\nonly one heading\n", ("introduction", "conclusion"))
    assert result["ok"] is True


def test_build_approve_payload_wires_heading_order_signal_for_lit_review(tmp_path):
    from research_vault.manuscript.check_gates import build_approve_payload
    from research_vault.manuscript.types import get_type

    project_notes_dir = tmp_path / "notes"
    project_notes_dir.mkdir()
    tree_root = tmp_path / "manuscripts" / "survey-heading"
    (tree_root / "sections").mkdir(parents=True)
    (tree_root / "sections" / "draft.md").write_text(
        "## Conclusion\n\nbaz\n\n## Introduction\n\nfoo\n", encoding="utf-8",
    )

    ms_type = get_type("lit-review")
    payload = build_approve_payload(tree_root, project_notes_dir, ms_type)
    assert any("heading-order" in s for s in payload["signals"])
    assert not any("heading-order" in b for b in payload["blocking"])
