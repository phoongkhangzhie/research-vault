"""test_sr_ms_1a.py — SR-MS-1a: manuscript structure tests.

Tests the manuscript OKF type (9th type), the rv manuscript new/list verbs,
the 16-node DAG scaffolder, and the per_section_tips config seam.

All hermetic (tmp_instance). Zero ~/vault reads.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# ensure src on path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault import note as note_mod
from research_vault.config import load_config
from research_vault.dag.schema import validate_manifest, ManifestError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_instance):
    return load_config(reload=True)


# ---------------------------------------------------------------------------
# 1. OKF_TYPES extension
# ---------------------------------------------------------------------------

def test_manuscript_type_in_okf_types():
    """'manuscript' is the 9th member of note.OKF_TYPES."""
    assert "manuscript" in note_mod.OKF_TYPES


def test_okf_types_is_nine():
    """OKF_TYPES has exactly 10 members after SR-LR-2 added 'gaps' as the 10th."""
    assert len(note_mod.OKF_TYPES) == 10
    assert "gaps" in note_mod.OKF_TYPES


# ---------------------------------------------------------------------------
# 2. note.cmd_new manuscript template branch
# ---------------------------------------------------------------------------

def test_manuscript_note_created_in_project_subdir(cfg):
    """cmd_new('manuscript') creates the note in project_notes_dir/manuscript/."""
    path = note_mod.cmd_new("demo-research", "manuscript", "Paper v1", config=cfg)
    assert path.exists()
    assert path.parent.name == "manuscript"
    # Must be project-scoped, not a shared root
    proj_notes = cfg.project_notes_dir("demo-research")
    assert path.is_relative_to(proj_notes)


def test_manuscript_note_has_required_frontmatter_fields(cfg):
    """Manuscript notes include all 7 flat provenance fields."""
    path = note_mod.cmd_new("demo-research", "manuscript", "Test Paper", config=cfg)
    content = path.read_text()
    required = [
        "manuscript_location",
        "manuscript_pdf",
        "manuscript_hash",
        "thesis",
        "synthesized_okf",
        "section_outline",
        "dag_run",
    ]
    for field in required:
        assert f"{field}:" in content, f"Missing frontmatter field: {field}"


def test_manuscript_note_type_field_is_manuscript(cfg):
    """Manuscript note has type: manuscript in frontmatter."""
    path = note_mod.cmd_new("demo-research", "manuscript", "Test Paper", config=cfg)
    content = path.read_text()
    assert "type: manuscript" in content


# ---------------------------------------------------------------------------
# 3. note.cmd_check: manuscript notes validate correctly
# ---------------------------------------------------------------------------

def test_note_check_passes_for_valid_manuscript(cfg):
    """rv note check reports no violations for a freshly-created manuscript note."""
    note_mod.cmd_new("demo-research", "manuscript", "Grounded Draft", config=cfg)
    violations = note_mod.cmd_check("demo-research", config=cfg)
    ms_violations = [v for v in violations if "manuscript" in v]
    assert ms_violations == [], f"Unexpected manuscript violations: {ms_violations}"


def test_note_check_catches_wrong_type_in_manuscript_dir(cfg):
    """rv note check catches a note with wrong type in manuscript/ dir."""
    # Manually create a note with type: findings in the manuscript/ dir
    ms_dir = cfg.project_notes_dir("demo-research") / "manuscript"
    ms_dir.mkdir(parents=True, exist_ok=True)
    bad_note = ms_dir / "bad-note.md"
    bad_note.write_text(
        "---\ntype: findings\ntitle: Wrong\ncreated: 2026-01-01\n---\n",
        encoding="utf-8",
    )
    violations = note_mod.cmd_check("demo-research", config=cfg)
    assert any("manuscript" in v or "bad-note" in v for v in violations), \
        f"Expected violation not found. Got: {violations}"


# ---------------------------------------------------------------------------
# 4. rv manuscript new / scaffold
# ---------------------------------------------------------------------------

def test_manuscript_new_creates_okf_note(cfg):
    """manuscript.cmd_new creates the OKF note file."""
    from research_vault import manuscript as ms_mod
    note_path, _, _ = ms_mod.cmd_new(
        "demo-research", "ms-001",
        thesis="Cross-lingual competence generalizes",
        scope=["findings/find-q1"],
        config=cfg,
    )
    assert note_path.exists()
    content = note_path.read_text()
    assert "type: manuscript" in content
    assert "thesis: Cross-lingual competence generalizes" in content


def test_manuscript_new_writes_thesis_field(cfg):
    """--thesis is stored in the note's thesis: field."""
    from research_vault import manuscript as ms_mod
    thesis = "LLMs underperform on cross-cultural pragmatics"
    note_path, _, _ = ms_mod.cmd_new(
        "demo-research", "ms-002",
        thesis=thesis,
        scope=[],
        config=cfg,
    )
    content = note_path.read_text()
    assert f"thesis: {thesis}" in content


def test_manuscript_new_writes_scope_to_synthesized_okf(cfg):
    """--scope OKF ids are stored as synthesized_okf."""
    from research_vault import manuscript as ms_mod
    scope = ["findings/find-q1", "experiments/exp-q1"]
    note_path, _, _ = ms_mod.cmd_new(
        "demo-research", "ms-003",
        thesis="Test claim",
        scope=scope,
        config=cfg,
    )
    content = note_path.read_text()
    assert "synthesized_okf:" in content
    for s in scope:
        assert s in content


def test_manuscript_new_scaffolds_directory_tree(cfg):
    """rv manuscript new scaffolds manuscripts/<id>/{main.tex,sections/,refs.bib,results.tex}."""
    from research_vault import manuscript as ms_mod
    _, tree_root, _ = ms_mod.cmd_new(
        "demo-research", "ms-004",
        thesis="Thesis here",
        scope=[],
        config=cfg,
    )
    assert (tree_root / "main.tex").exists(), "main.tex not created"
    assert (tree_root / "sections").is_dir(), "sections/ dir not created"
    assert (tree_root / "refs.bib").exists(), "refs.bib not created"
    assert (tree_root / "results.tex").exists(), "results.tex not created"


def test_manuscript_tree_is_project_scoped(cfg):
    """The manuscript tree lives under project_notes_dir, not a global root."""
    from research_vault import manuscript as ms_mod
    _, tree_root, _ = ms_mod.cmd_new(
        "demo-research", "ms-005",
        thesis="T",
        scope=[],
        config=cfg,
    )
    proj_notes = cfg.project_notes_dir("demo-research")
    assert tree_root.is_relative_to(proj_notes), \
        f"tree_root {tree_root} not under {proj_notes}"


# ---------------------------------------------------------------------------
# 5. DAG manifest validation
# ---------------------------------------------------------------------------

def test_manifest_validates_with_no_errors(cfg):
    """The scaffolded DAG manifest passes validate_manifest."""
    from research_vault import manuscript as ms_mod
    _, _, manifest = ms_mod.cmd_new(
        "demo-research", "ms-006",
        thesis="Validate me",
        scope=[],
        config=cfg,
    )
    # Should not raise
    validate_manifest(manifest)


def test_manifest_has_expected_node_count(cfg):
    """Scaffolded manifest has 16 nodes (the 5J.2 shape)."""
    from research_vault import manuscript as ms_mod
    _, _, manifest = ms_mod.cmd_new(
        "demo-research", "ms-007",
        thesis="Node count",
        scope=[],
        config=cfg,
    )
    assert len(manifest["nodes"]) == 16


def test_manifest_all_agent_nodes_have_spec(cfg):
    """Every agent node in the scaffolded manifest has a non-empty spec."""
    from research_vault import manuscript as ms_mod
    _, _, manifest = ms_mod.cmd_new(
        "demo-research", "ms-008",
        thesis="Spec check",
        scope=[],
        config=cfg,
    )
    for node in manifest["nodes"]:
        if node.get("type", "agent") == "agent":
            spec = node.get("spec", "")
            assert spec and spec.strip(), \
                f"Agent node {node['id']!r} missing non-empty spec"


def test_manifest_human_go_nodes_have_no_reads(cfg):
    """Human-go nodes in the scaffolded manifest carry no reads: field."""
    from research_vault import manuscript as ms_mod
    _, _, manifest = ms_mod.cmd_new(
        "demo-research", "ms-009",
        thesis="HG reads check",
        scope=[],
        config=cfg,
    )
    hg_nodes = [n for n in manifest["nodes"] if n.get("type") == "human-go"]
    assert len(hg_nodes) == 3, f"Expected 3 human-go gates, got {len(hg_nodes)}"
    for node in hg_nodes:
        assert "reads" not in node, \
            f"Human-go node {node['id']!r} must not carry reads:"


def test_manifest_three_human_go_gates(cfg):
    """Scaffolded manifest has exactly 3 human-go gates: approve-thesis, approve-framing, approve-manuscript."""
    from research_vault import manuscript as ms_mod
    _, _, manifest = ms_mod.cmd_new(
        "demo-research", "ms-010",
        thesis="Gate count",
        scope=[],
        config=cfg,
    )
    hg_ids = {n["id"] for n in manifest["nodes"] if n.get("type") == "human-go"}
    expected = {"approve-thesis", "approve-framing", "approve-manuscript"}
    assert hg_ids == expected, f"Expected gates {expected}, got {hg_ids}"


def test_appendix_repro_branches_off_approve_thesis_not_framing(cfg):
    """appendix-repro depends on approve-thesis and NOT on approve-framing."""
    from research_vault import manuscript as ms_mod
    _, _, manifest = ms_mod.cmd_new(
        "demo-research", "ms-011",
        thesis="Branch check",
        scope=[],
        config=cfg,
    )
    by_id = {n["id"]: n for n in manifest["nodes"]}
    appendix = by_id["appendix-repro"]
    needs_from = {need["from"] for need in appendix.get("needs", [])}
    assert "approve-thesis" in needs_from, \
        "appendix-repro must depend on approve-thesis (Gate 1)"
    assert "approve-framing" not in needs_from, \
        "appendix-repro must NOT depend on approve-framing (skips Gate 2)"


def test_assemble_waits_for_both_abstract_and_appendix(cfg):
    """assemble node depends on both abstract and appendix-repro."""
    from research_vault import manuscript as ms_mod
    _, _, manifest = ms_mod.cmd_new(
        "demo-research", "ms-012",
        thesis="Assemble join",
        scope=[],
        config=cfg,
    )
    by_id = {n["id"]: n for n in manifest["nodes"]}
    assemble = by_id["assemble"]
    needs_from = {need["from"] for need in assemble.get("needs", [])}
    assert "abstract" in needs_from, "assemble must depend on abstract"
    assert "appendix-repro" in needs_from, "assemble must depend on appendix-repro"


def test_approve_manuscript_is_last_gate(cfg):
    """approve-manuscript is downstream of critic."""
    from research_vault import manuscript as ms_mod
    _, _, manifest = ms_mod.cmd_new(
        "demo-research", "ms-013",
        thesis="Last gate",
        scope=[],
        config=cfg,
    )
    by_id = {n["id"]: n for n in manifest["nodes"]}
    gate = by_id["approve-manuscript"]
    needs_from = {need["from"] for need in gate.get("needs", [])}
    assert "critic" in needs_from, "approve-manuscript must depend on critic"


# ---------------------------------------------------------------------------
# 6. reads: pointer resolution — sections/ dir gotcha regression
# ---------------------------------------------------------------------------

def test_reads_sections_dir_resolves_zero_hard_errors(cfg):
    """resolve_reads_pointers reports zero hard errors on a freshly-scaffolded manifest.

    Regression for the 5J.2 gotcha: section reads: point at sections/ dir (not specific
    .tex files), which exists after scaffolding. No file-not-found hard errors.
    """
    from research_vault import manuscript as ms_mod
    from research_vault.dag.reads import resolve_reads_pointers

    _, tree_root, manifest = ms_mod.cmd_new(
        "demo-research", "ms-reads",
        thesis="Reads resolution",
        scope=[],
        config=cfg,
    )
    # The project_root for reads resolution = project_notes_dir
    project_root = cfg.project_notes_dir("demo-research")

    errors, _warns = resolve_reads_pointers(manifest, project_root=project_root)
    assert errors == [], \
        f"Expected zero hard reads errors, got: {errors}"


# ---------------------------------------------------------------------------
# 7. Manifest is saved as JSON on disk
# ---------------------------------------------------------------------------

def test_manifest_saved_to_disk(cfg):
    """rv manuscript new writes the manifest JSON next to the manuscript tree."""
    from research_vault import manuscript as ms_mod
    _, tree_root, manifest = ms_mod.cmd_new(
        "demo-research", "ms-save",
        thesis="Saved manifest",
        scope=[],
        config=cfg,
    )
    manifest_path = tree_root / "drafting-dag.json"
    assert manifest_path.exists(), "drafting-dag.json not written to tree_root"
    on_disk = json.loads(manifest_path.read_text())
    assert on_disk["run_id"] == manifest["run_id"]


# ---------------------------------------------------------------------------
# 8. rv manuscript list
# ---------------------------------------------------------------------------

def test_manuscript_list_returns_empty_when_none(cfg):
    """manuscript.cmd_list returns [] when no manuscript notes exist."""
    from research_vault import manuscript as ms_mod
    notes = ms_mod.cmd_list("demo-research", config=cfg)
    assert notes == []


def test_manuscript_list_returns_created_notes(cfg):
    """manuscript.cmd_list returns created manuscript notes."""
    from research_vault import manuscript as ms_mod
    ms_mod.cmd_new("demo-research", "ms-list-a", thesis="A", scope=[], config=cfg)
    ms_mod.cmd_new("demo-research", "ms-list-b", thesis="B", scope=[], config=cfg)
    notes = ms_mod.cmd_list("demo-research", config=cfg)
    assert len(notes) == 2
    ids = {n["fields"].get("title", n["path"].stem) for n in notes}
    assert "A" in ids
    assert "B" in ids


# ---------------------------------------------------------------------------
# 9. per_section_tips config seam
# ---------------------------------------------------------------------------

def test_per_section_tips_has_all_section_keys():
    """per_section_tips contains keys for all 16 DAG nodes (or at least agent section nodes)."""
    from research_vault.manuscript.style import per_section_tips, SECTION_KEYS
    for key in SECTION_KEYS:
        assert key in per_section_tips, f"Missing per_section_tips key: {key!r}"


def test_per_section_tips_values_are_non_empty():
    """All per_section_tips entries are non-empty strings."""
    from research_vault.manuscript.style import per_section_tips
    for key, val in per_section_tips.items():
        assert isinstance(val, str) and val.strip(), \
            f"per_section_tips[{key!r}] is empty or not a string"


def test_gather_scope_tip_mentions_k1_ledger():
    """gather-scope tip contains K-1 ledger guidance (inclusion ledger + plan_role: main)."""
    from research_vault.manuscript.style import per_section_tips
    tip = per_section_tips["gather-scope"]
    tip_lower = tip.lower()
    assert "inclusion ledger" in tip_lower or "ledger" in tip_lower, \
        "gather-scope tip must mention the inclusion ledger"
    # K-1 guidance: covers: / plan_role: main / preregistration
    has_k1 = (
        "plan_role" in tip_lower
        or "covers:" in tip_lower
        or "preregistration" in tip_lower
        or "plan_kind" in tip_lower
    )
    assert has_k1, \
        "gather-scope tip must include K-1 guidance (plan_role: main / covers: / preregistration)"


def test_limitations_tip_mentions_harvesting_caveats():
    """limitations tip directs agent to harvest from Caveats/Confidence fields."""
    from research_vault.manuscript.style import per_section_tips
    tip = per_section_tips["limitations"]
    tip_lower = tip.lower()
    assert "caveat" in tip_lower or "confidence" in tip_lower, \
        "limitations tip must reference Caveats or Confidence fields from finding notes"


def test_results_discussion_tip_mentions_macros():
    """results-discussion tip requires machine-injected macros (no typed numbers)."""
    from research_vault.manuscript.style import per_section_tips
    tip = per_section_tips["results-discussion"]
    tip_lower = tip.lower()
    assert "macro" in tip_lower or "\\result" in tip_lower or "injected" in tip_lower, \
        "results-discussion tip must require machine-injected macros"


def test_get_section_tips_returns_defaults():
    """get_section_tips() returns the default per_section_tips dict."""
    from research_vault.manuscript.style import get_section_tips
    tips = get_section_tips()
    assert isinstance(tips, dict)
    assert "gather-scope" in tips


def test_get_section_tips_override():
    """get_section_tips(override) merges custom values on top of defaults."""
    from research_vault.manuscript.style import get_section_tips
    override = {"gather-scope": "Custom gather scope instructions."}
    tips = get_section_tips(override=override)
    assert tips["gather-scope"] == "Custom gather scope instructions."
    # Other keys still have defaults
    assert "related-work" in tips


# ---------------------------------------------------------------------------
# 10. CLI verb registration and rv help --check
# ---------------------------------------------------------------------------

def test_manuscript_verb_registered_with_when_to_use():
    """'manuscript' is in _VERB_REGISTRY with a non-empty when_to_use."""
    from research_vault.cli import _VERB_REGISTRY
    assert "manuscript" in _VERB_REGISTRY, "'manuscript' not in _VERB_REGISTRY"
    entry = _VERB_REGISTRY["manuscript"]
    assert entry.get("when_to_use", "").strip(), \
        "'manuscript' verb has no when_to_use string"


def test_rv_help_check_passes_with_manuscript(tmp_instance):
    """rv help --check exits 0 — all verbs (incl. manuscript) have when_to_use."""
    from research_vault.cli import _check_verb_docstrings
    violations = _check_verb_docstrings()
    assert violations == [], f"help --check violations: {violations}"


# ---------------------------------------------------------------------------
# 11. LaTeX template exists
# ---------------------------------------------------------------------------

def test_latex_template_exists():
    """The neutral LaTeX template file is present in the package data directory.

    SR-PKG: templates/ moved to src/research_vault/data/templates/.
    """
    src_root = Path(__file__).parent.parent / "src" / "research_vault"
    template_path = src_root / "data" / "templates" / "manuscript.tex"
    assert template_path.exists(), f"LaTeX template not found at {template_path}"
    content = template_path.read_text()
    assert "\\documentclass" in content, "Template must contain \\documentclass"
    assert "\\begin{document}" in content, "Template must contain \\begin{document}"


# ---------------------------------------------------------------------------
# 12. note.cmd_check PDF-hash provenance (optional branch)
# ---------------------------------------------------------------------------

def test_manuscript_pdf_hash_check_skipped_when_empty(cfg):
    """cmd_check does not error on manuscript note with empty manuscript_pdf/hash."""
    note_mod.cmd_new("demo-research", "manuscript", "Draft", config=cfg)
    violations = note_mod.cmd_check("demo-research", config=cfg)
    # empty pdf fields should NOT trigger a violation (unfilled = not yet compiled)
    ms_pdf_viol = [v for v in violations if "manuscript_pdf" in v or "manuscript_hash" in v]
    assert ms_pdf_viol == [], f"Unexpected pdf/hash violations: {ms_pdf_viol}"


def test_manuscript_pdf_hash_mismatch_reported(cfg, tmp_path):
    """cmd_check flags a manuscript note whose manuscript_hash doesn't match the actual PDF."""
    import hashlib
    # Create a fake PDF file
    fake_pdf = tmp_path / "paper.pdf"
    fake_pdf.write_bytes(b"fake pdf content")
    correct_hash = "sha256:" + hashlib.sha256(b"fake pdf content").hexdigest()
    wrong_hash = "sha256:" + "a" * 64  # deliberate mismatch

    # Write manuscript note with wrong hash
    ms_dir = cfg.project_notes_dir("demo-research") / "manuscript"
    ms_dir.mkdir(parents=True, exist_ok=True)
    note_path = ms_dir / "hash-test.md"
    note_path.write_text(
        f"---\ntype: manuscript\ntitle: Hash test\ncreated: 2026-01-01\n"
        f"manuscript_pdf: {fake_pdf}\nmanuscript_hash: {wrong_hash}\n"
        f"manuscript_location: \nthesis: \nsynthesized_okf: \nsection_outline: \ndag_run: \n---\n",
        encoding="utf-8",
    )
    violations = note_mod.cmd_check("demo-research", config=cfg)
    ms_viol = [v for v in violations if "hash-test" in v or "manuscript_hash" in v or "hash" in v.lower()]
    assert ms_viol, f"Expected hash-mismatch violation, got violations: {violations}"
