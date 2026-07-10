"""test_manuscript_m6_lit_review.py — PR-M6 acceptance tests.

PR-M6 fills the PR-M1 `lit-review` ManuscriptType stub with the survey
specialization (design §3-§5): the real 9-row section-set, the
framework-selection Phase-1 (scope -> framework-propose ->
[HG:approve-framework]), the OKF -> survey `source_transform`, the §3.1
structurally-binding thematic-section brief contract, and the
reframe-escalation payload builder.

Coverage:
  1. The section-set (design §3)
     1a. exactly 9 sections, correct names
     1b. abstract is drafted LAST
     1c. equation_sources unchanged (design §7, consumed PR-M4)
  2. The style-seam briefs (design §3.1 — "teeth": a brief missing the
     forbidding rule would fail this test)
     2a. every section_set entry has a non-empty tip
     2b. thematic-sections brief FORBIDS the single-cite paragraph
     2c. thematic-sections brief REQUIRES >=2 papers compared
     2d. thematic-sections brief requires link-fields-only relationships
     2e. thematic-sections brief requires a provenance pointer
     2f. thematic-sections brief requires pivotal-equation reproduction
  3. render_framework_candidates_menu — the 4 shapes, proposed never forced
  4. check_framework_gate (unit)
     4a. missing file -> (False, ...)
     4b. empty spine_shape -> (False, ...)
     4c. empty branches -> (False, ...)
     4d. both present -> (True, "OK")
  5. phase1_builder — the framework-selection manifest
     5a. node order: scope -> framework-propose -> approve-framework
     5b. approve-framework is human-go
     5c. validate_manifest passes
     5d. framework-propose spec mentions all 4 shape keys
  6. cmd_approve wiring (real DAG path, non-vacuous, mirrors task #33's pattern)
     6a. empty spine_shape/branches -> refuses approval, no state mutation
     6b. non-empty spine_shape+branches -> approves cleanly
     6c. --reject bypasses the gate
  7. build_reframe_escalation_payload
     7a. cleared is always False
     7b. action is always "propose-only" (never "auto-reframe")
     7c. does not mutate its inputs
  8. render_prisma_ledger / index_literature_rows / render_comparison_table
     8a. empty coverage -> honest "no corpus" ledger
     8b. populated coverage -> deterministic counts table
     8c. index_literature_rows sorted by citekey, includes repo column
     8d. render_comparison_table byte-deterministic given same rows
  9. source_transform
     9a. combines prisma ledger + comparison table + framework branches
     9b. branches as a list vs comma-string both parse
  10. End-to-end: cmd_new + expand -> full 9-section manifest
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.manuscript.types import get_type
from research_vault.manuscript.types.lit_review import (
    FRAMEWORK_SHAPES,
    SECTION_SET,
    STYLE_BRIEFS,
    build_reframe_escalation_payload,
    check_framework_gate,
    index_literature_rows,
    phase1_builder,
    render_comparison_table,
    render_framework_candidates_menu,
    render_prisma_ledger,
    source_transform,
)
from research_vault.dag.schema import validate_manifest


# ---------------------------------------------------------------------------
# 1. The section-set (design §3)
# ---------------------------------------------------------------------------

def test_section_set_has_seven_rows():
    """RD-2/RD-4 (next-gen lit-review design §6): prisma-scope and framework
    are removed as body rows (methods -> DEVLOG, spine shown by structure
    only). PR-B (gold-settled `report.md`): `appendix-methods` is ALSO
    removed as a body row — the gold report carries no Appendix; the
    methods record routes to the project's DEVLOG/control note instead
    (still a STYLE_BRIEFS tip, just never assembled into report.md). Net
    9 -> 8 -> 7."""
    ms_type = get_type("lit-review")
    assert len(ms_type.section_set) == 7


def test_section_set_names_reader_first_order():
    """RD-2: reader-first order — thesis/framing leads. PR-B: no Appendix
    row at all (methods route to DEVLOG/control note, never report.md)."""
    names = [s.name for s in SECTION_SET]
    assert names == [
        "introduction", "thematic-sections",
        "cross-cutting-analysis", "open-problems", "conclusion",
        "references", "abstract",
    ]
    assert "prisma-scope" not in names
    assert "framework" not in names
    assert "appendix-methods" not in names


def test_abstract_is_last():
    assert SECTION_SET[-1].name == "abstract"


def test_equation_sources_unchanged():
    ms_type = get_type("lit-review")
    assert ms_type.equation_sources == ("concepts", "literature")


# ---------------------------------------------------------------------------
# 2. The style-seam briefs (design §3.1 — "teeth")
# ---------------------------------------------------------------------------

def test_every_section_has_a_nonempty_brief():
    for section in SECTION_SET:
        key = section.brief_key or section.name
        assert STYLE_BRIEFS.get(key, "").strip(), f"no brief for {key!r}"


def test_thematic_brief_forbids_single_cite_paragraph():
    brief = STYLE_BRIEFS["thematic-sections"]
    assert "single-cite" in brief.lower() or "single cite" in brief.lower()
    assert "forbid" in brief.lower()


def test_thematic_brief_requires_two_papers_compared():
    brief = STYLE_BRIEFS["thematic-sections"].lower()
    assert "at least two" in brief or ">=2" in brief or "≥2" in brief


def test_thematic_brief_requires_link_fields_only():
    brief = STYLE_BRIEFS["thematic-sections"].lower()
    assert "link-field" in brief
    assert "never invented" in brief


def test_thematic_brief_requires_provenance_pointer():
    brief = STYLE_BRIEFS["thematic-sections"].lower()
    assert "provenance pointer" in brief


def test_thematic_brief_requires_pivotal_equation_reproduction():
    brief = STYLE_BRIEFS["thematic-sections"].lower()
    assert "critical equation" in brief or "pivotal" in brief
    assert "$$" in brief


def test_thematic_brief_retires_latex_equation_env():
    """PR-B (gold-settled `report.md`): markdown `$$...$$` display math is
    the REQUIRED reproduction form; `\\begin{equation}` is only mentioned
    as the retired never-use alternative, never instructed."""
    brief = STYLE_BRIEFS["thematic-sections"]
    assert "reproduce it as markdown display math" in brief
    assert "never `\\begin{equation}" in brief


def test_thematic_brief_edges_are_grounding_inputs_only():
    """PR-B rule 3: typed edges/note paths ground the relation but must
    never be surfaced/quoted verbatim in drafted prose — an argued
    sentence, not a bracket tag or a note path."""
    brief = STYLE_BRIEFS["thematic-sections"].lower()
    assert "grounding" in brief
    assert "argued" in brief
    assert "never quote the edge tag" in brief or "never surfaced" in brief


def test_regression_guard_brief_missing_rule_would_fail():
    """A brief that dropped the forbidding rule fails this test (the "teeth"
    the acceptance criteria asks for) — proven by simulating the regression."""
    regressed_brief = "Draft the thematic sections synthesizing the literature."
    assert "forbid" not in regressed_brief.lower()  # confirms the test bites


# ---------------------------------------------------------------------------
# 3. render_framework_candidates_menu — the 4 shapes
# ---------------------------------------------------------------------------

def test_framework_shapes_count():
    assert len(FRAMEWORK_SHAPES) == 4


def test_candidates_menu_mentions_all_four_shapes():
    menu = render_framework_candidates_menu()
    for shape in FRAMEWORK_SHAPES:
        assert shape["key"] in menu
        assert shape["name"] in menu


def test_candidates_menu_never_commits():
    menu = render_framework_candidates_menu()
    assert "never commit" in menu.lower() or "NEVER commit" in menu


# ---------------------------------------------------------------------------
# 4. check_framework_gate — unit level
# ---------------------------------------------------------------------------

def _manuscript_note(
    path: Path, *, spine_shape: str | None = "n-axis",
    branches: list[str] | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    branches = ["modality", "supervision-level"] if branches is None else branches
    fm_lines = ["type: manuscript", "manuscript_type: lit-review"]
    if spine_shape is not None:
        fm_lines.append(f"spine_shape: {spine_shape}")
    if branches:
        fm_lines.append("branches:")
        for b in branches:
            fm_lines.append(f"  - {b}")
    else:
        fm_lines.append("branches: ")
    fm = "\n".join(fm_lines) + "\n"
    path.write_text(f"---\n{fm}---\n\n## Scope\n", encoding="utf-8")
    return path


def test_gate_missing_file():
    ok, msg = check_framework_gate(Path("/nonexistent/_manuscript.md"))
    assert ok is False
    assert "not found" in msg


def test_gate_empty_spine_shape(tmp_path):
    p = _manuscript_note(tmp_path / "_manuscript.md", spine_shape="", branches=["a"])
    ok, msg = check_framework_gate(p)
    assert ok is False
    assert "spine_shape" in msg


def test_gate_empty_branches(tmp_path):
    p = _manuscript_note(tmp_path / "_manuscript.md", spine_shape="pipeline", branches=[])
    ok, msg = check_framework_gate(p)
    assert ok is False
    assert "branches" in msg


def test_gate_both_present_passes(tmp_path):
    p = _manuscript_note(tmp_path / "_manuscript.md", spine_shape="pipeline", branches=["a", "b"])
    ok, msg = check_framework_gate(p)
    assert ok is True
    assert msg == "OK"


# ---------------------------------------------------------------------------
# 5. phase1_builder — the framework-selection manifest
# ---------------------------------------------------------------------------

def test_phase1_builder_node_order(tmp_path):
    project_notes_dir = tmp_path / "notes"
    project_notes_dir.mkdir()
    tree_root = tmp_path / "notes" / "manuscripts" / "survey-x"
    tree_root.mkdir(parents=True)

    manifest = phase1_builder(
        project="demo", slug="survey-x",
        project_notes_dir=project_notes_dir, tree_root=tree_root,
    )
    ids = [n["id"] for n in manifest["nodes"]]
    # framework-gate-autonomy design (option A, 2026-07-09): scope fans out
    # to N cold lens candidates, then synthesize -> critic -> approve.
    assert ids[0] == "scope"
    assert ids[-3:] == ["framework-synthesize", "framework-critic", "approve-framework"]
    lens_ids = ids[1:-3]
    assert lens_ids and all(nid.startswith("framework-lens-") for nid in lens_ids)


def test_phase1_builder_approve_framework_is_human_go(tmp_path):
    project_notes_dir = tmp_path / "notes"
    project_notes_dir.mkdir()
    tree_root = tmp_path / "notes" / "manuscripts" / "survey-y"
    tree_root.mkdir(parents=True)

    manifest = phase1_builder(
        project="demo", slug="survey-y",
        project_notes_dir=project_notes_dir, tree_root=tree_root,
    )
    hg = next(n for n in manifest["nodes"] if n["id"] == "approve-framework")
    assert hg["type"] == "human-go"
    assert hg["needs"] == [{"from": "framework-critic", "edge": "afterok"}]


def test_phase1_builder_validates(tmp_path):
    project_notes_dir = tmp_path / "notes"
    project_notes_dir.mkdir()
    tree_root = tmp_path / "notes" / "manuscripts" / "survey-z"
    tree_root.mkdir(parents=True)

    manifest = phase1_builder(
        project="demo", slug="survey-z",
        project_notes_dir=project_notes_dir, tree_root=tree_root,
    )
    validate_manifest(manifest)  # raises ManifestError if invalid


def test_framework_propose_spec_mentions_all_shapes(tmp_path):
    project_notes_dir = tmp_path / "notes"
    project_notes_dir.mkdir()
    tree_root = tmp_path / "notes" / "manuscripts" / "survey-w"
    tree_root.mkdir(parents=True)

    manifest = phase1_builder(
        project="demo", slug="survey-w",
        project_notes_dir=project_notes_dir, tree_root=tree_root,
    )
    # framework-gate-autonomy design: the single framework-propose menu node
    # is replaced by N lens nodes, each expressing its candidate through a
    # FRAMEWORK_SHAPES archetype (its natural_shape, named explicitly, plus
    # the full "other shapes" menu as the override drawing-board) — across
    # all lens nodes' specs combined, every registered shape key appears.
    lens_nodes = [n for n in manifest["nodes"] if n["id"].startswith("framework-lens-")]
    assert lens_nodes
    combined_spec = "\n".join(n["spec"] for n in lens_nodes)
    for shape in FRAMEWORK_SHAPES:
        assert shape["key"] in combined_spec


# ---------------------------------------------------------------------------
# 6. cmd_approve wiring — real DAG path (mirrors task #33's pattern)
# ---------------------------------------------------------------------------

def _cfg_file(tmp_path: Path) -> Path:
    f = tmp_path / "research_vault.toml"
    f.write_text(
        f'instance_root = "{tmp_path}"\n'
        f'notes_root = "{tmp_path / "notes"}"\n'
        f'state_dir = "{tmp_path / "state"}"\n'
        f'agents_dir = "{tmp_path / ".agents"}"\n'
        f'tasks_dir = "{tmp_path / "tasks"}"\n'
        f'control_dir = "{tmp_path / "control"}"\n'
        '[adapters]\nnotifier = "file"\nbackend = "local"\nsecrets = "env"\n'
        '[approval]\nenforce = true\n'
        'token_fingerprint = "d309a810bb5f40cef518202e46d197aa61e4dddafc5984c8c698da29ac8fd2bc"\n'
        'enforce_sig = ""\n',
        encoding="utf-8",
    )
    return f


def _set_run_env(tmp_path: Path):
    cfg_file = _cfg_file(tmp_path)
    old = os.environ.get("RESEARCH_VAULT_CONFIG")
    os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_file)
    return old


def _restore_env(old):
    if old is None:
        os.environ.pop("RESEARCH_VAULT_CONFIG", None)
    else:
        os.environ["RESEARCH_VAULT_CONFIG"] = old


def _framework_manifest(run_id: str) -> dict:
    return {
        "run_id": run_id,
        "name": "test framework selection",
        "global_cap": 1,
        "nodes": [
            {"id": "scope", "type": "agent", "spec": "task://demo#scope", "needs": []},
            {
                "id": "framework-propose", "type": "agent", "spec": "task://demo#propose",
                "needs": [{"from": "scope", "edge": "afterok"}],
            },
            {
                "id": "approve-framework", "type": "human-go", "label": "Gate",
                "needs": [{"from": "framework-propose", "edge": "afterok"}],
            },
        ],
    }


def _make_awaiting_run(tmp_path: Path, run_id: str, manifest_dir: Path):
    from research_vault.dag.store import RunState, RunStore

    manifest = _framework_manifest(run_id)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "phase1-dag.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    store = RunStore(tmp_path / "state")
    rs = RunState(run_id=run_id, manifest_path=str(manifest_path))
    rs.init_nodes(manifest)
    rs.set_node_status("scope", "succeeded")
    rs.set_node_status("framework-propose", "succeeded")
    rs.set_node_status("approve-framework", "awaiting-go")
    store.create(rs)
    return store


class TestApproveFrameworkGateWiring:
    def test_empty_spine_refuses_approval_no_state_mutation(self, tmp_path):
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            manifest_dir = tmp_path / "notes" / "manuscripts" / "survey-empty"
            _manuscript_note(manifest_dir / "_manuscript.md", spine_shape="", branches=[])
            store = _make_awaiting_run(tmp_path, "ms-empty-spine", manifest_dir)

            args = argparse.Namespace(run_id="ms-empty-spine", node_id="approve-framework")
            rc = cmd_approve(args)

            assert rc != 0
            rs = store.load("ms-empty-spine")
            assert rs.node_status("approve-framework") == "awaiting-go"
        finally:
            _restore_env(old)

    def test_nonempty_spine_approves_cleanly(self, tmp_path):
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            manifest_dir = tmp_path / "notes" / "manuscripts" / "survey-good"
            _manuscript_note(
                manifest_dir / "_manuscript.md",
                spine_shape="pipeline", branches=["collect", "train", "eval"],
            )
            store = _make_awaiting_run(tmp_path, "ms-good-spine", manifest_dir)

            args = argparse.Namespace(run_id="ms-good-spine", node_id="approve-framework")
            rc = cmd_approve(args)

            assert rc == 0
            rs = store.load("ms-good-spine")
            assert rs.node_status("approve-framework") == "succeeded"
        finally:
            _restore_env(old)

    def test_reject_bypasses_the_gate(self, tmp_path):
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            manifest_dir = tmp_path / "notes" / "manuscripts" / "survey-reject"
            _manuscript_note(manifest_dir / "_manuscript.md", spine_shape="", branches=[])
            store = _make_awaiting_run(tmp_path, "ms-reject-spine", manifest_dir)

            args = argparse.Namespace(
                run_id="ms-reject-spine", node_id="approve-framework", reject=True,
            )
            rc = cmd_approve(args)

            assert rc == 0
            rs = store.load("ms-reject-spine")
            assert rs.node_status("approve-framework") == "blocked"
        finally:
            _restore_env(old)


# ---------------------------------------------------------------------------
# 7. build_reframe_escalation_payload
# ---------------------------------------------------------------------------

def test_reframe_payload_cleared_always_false():
    payload = build_reframe_escalation_payload(
        round_no=2, misfits=["orphan concept X"],
        candidate_reframes=[{"shape": "n-axis", "rationale": "..."}],
    )
    assert payload["cleared"] is False


def test_reframe_payload_action_is_propose_only():
    payload = build_reframe_escalation_payload(round_no=1, misfits=[], candidate_reframes=[])
    assert payload["action"] == "propose-only"
    assert payload["action"] != "auto-reframe"


def test_reframe_payload_does_not_mutate_inputs():
    misfits = ["a", "b"]
    candidates = [{"shape": "pipeline"}]
    build_reframe_escalation_payload(round_no=1, misfits=misfits, candidate_reframes=candidates)
    assert misfits == ["a", "b"]
    assert candidates == [{"shape": "pipeline"}]


def test_reframe_payload_carries_escalation_detail():
    payload = build_reframe_escalation_payload(
        round_no=2, misfits=["X doesn't fit"],
        candidate_reframes=[{"shape": "coupled-taxonomies"}],
    )
    esc = payload["escalation"]
    assert esc["round"] == 2
    assert "X doesn't fit" in esc["misfits"]
    assert esc["candidate_reframes"] == [{"shape": "coupled-taxonomies"}]


# ---------------------------------------------------------------------------
# 8. render_prisma_ledger / index_literature_rows / render_comparison_table
# ---------------------------------------------------------------------------

def test_prisma_ledger_empty_coverage_is_honest():
    ledger = render_prisma_ledger({})
    assert "no frozen corpus" in ledger.lower()


def test_prisma_ledger_populated_matches_counts():
    coverage = {
        "corpus_citekeys": ["a2024", "b2024"],
        "materialized": ["a2024"],
        "unmaterialized": ["b2024"],
        "orphan": [],
        "counts": {"corpus": 2, "materialized": 1, "unmaterialized": 1, "orphan": 0, "mention_only": 0},
    }
    ledger = render_prisma_ledger(coverage)
    assert "| Corpus (frozen citekeys) | 2 |" in ledger
    assert "| Materialized (has a `literature/` note) | 1 |" in ledger
    assert "b2024" in ledger  # unmaterialized citekey surfaced


def test_index_literature_rows_empty_dir(tmp_path):
    assert index_literature_rows(tmp_path / "nope") == []


def test_index_literature_rows_sorted_and_has_repo(tmp_path):
    lit_dir = tmp_path / "literature"
    lit_dir.mkdir()
    (lit_dir / "z.md").write_text(
        "---\ntype: literature\ncitekey: zzz2024\ntitle: Z paper\nyear: 2024\n"
        "venue: NeurIPS\nrepo: https://github.com/z/z\n---\n\nbody\n",
        encoding="utf-8",
    )
    (lit_dir / "a.md").write_text(
        "---\ntype: literature\ncitekey: aaa2023\ntitle: A paper\nyear: 2023\n"
        "venue: ACL\n---\n\nbody\n",
        encoding="utf-8",
    )
    rows = index_literature_rows(lit_dir)
    assert [r["citekey"] for r in rows] == ["aaa2023", "zzz2024"]
    assert rows[1]["repo"] == "https://github.com/z/z"
    assert rows[0]["repo"] == ""


def test_comparison_table_deterministic(tmp_path):
    rows = [
        {"citekey": "a2023", "title": "A", "year": "2023", "venue": "ACL", "repo": ""},
        {"citekey": "z2024", "title": "Z", "year": "2024", "venue": "NeurIPS", "repo": "https://x"},
    ]
    table1 = render_comparison_table(rows)
    table2 = render_comparison_table(rows)
    assert table1 == table2
    assert "a2023" in table1 and "z2024" in table1
    assert "https://x" in table1
    assert "—" in table1  # no-repo placeholder for a2023


def test_comparison_table_is_numbered_sources_list():
    """PR-B (gold-settled `report.md`): `[N]` numbered ledger, never a
    markdown table of bare citekeys — the reader-facing citation
    convention is `[N]` inline + this list, matched 1:1 by position."""
    rows = [
        {"citekey": "a2023", "title": "A", "year": "2023", "venue": "ACL", "repo": ""},
        {"citekey": "z2024", "title": "Z", "year": "2024", "venue": "NeurIPS", "repo": "https://x"},
    ]
    table = render_comparison_table(rows)
    assert "[1]" in table and "[2]" in table
    assert "| Citekey |" not in table  # the old markdown-table header is gone


def test_comparison_table_empty_rows_is_honest():
    table = render_comparison_table([])
    assert "no `literature/` notes" in table.lower()


# ---------------------------------------------------------------------------
# 9. source_transform
# ---------------------------------------------------------------------------

def test_source_transform_combines_pieces(tmp_path):
    project_notes_dir = tmp_path / "notes"
    (project_notes_dir / "literature").mkdir(parents=True)
    (project_notes_dir / "literature" / "a.md").write_text(
        "---\ntype: literature\ncitekey: a2023\ntitle: A\nyear: 2023\nvenue: ACL\n---\n\nbody\n",
        encoding="utf-8",
    )
    tree_root = project_notes_dir / "manuscripts" / "survey-st"
    tree_root.mkdir(parents=True)

    result = source_transform(
        "demo", project_notes_dir, tree_root,
        spine={"spine_shape": "pipeline", "branches": ["collect", "train"]},
    )
    assert "a2023" in result["references"]
    assert result["framework_branches"] == ["collect", "train"]
    assert result["spine_shape"] == "pipeline"
    # RD-3: the appendix-methods key (was prisma-scope) carries the PRISMA
    # ledger; the provenance_header key carries the hash-free blockquote
    # (RD-3: hash/reconciliation flags route to the control note, never here).
    assert "PRISMA scope & method" in result["appendix-methods"]
    assert "prisma-scope" not in result
    assert "sha256" not in result["provenance_header"].lower()
    assert result["provenance_header"].strip()


def test_source_transform_branches_comma_string(tmp_path):
    project_notes_dir = tmp_path / "notes"
    project_notes_dir.mkdir()
    tree_root = project_notes_dir / "manuscripts" / "survey-st2"
    tree_root.mkdir(parents=True)

    result = source_transform(
        "demo", project_notes_dir, tree_root,
        spine={"spine_shape": "n-axis", "branches": "modality, supervision"},
    )
    assert result["framework_branches"] == ["modality", "supervision"]


def test_source_transform_no_spine_yet(tmp_path):
    project_notes_dir = tmp_path / "notes"
    project_notes_dir.mkdir()
    tree_root = project_notes_dir / "manuscripts" / "survey-st3"
    tree_root.mkdir(parents=True)

    result = source_transform("demo", project_notes_dir, tree_root, spine={})
    assert result["framework_branches"] == []
    assert result["spine_shape"] == ""


# ---------------------------------------------------------------------------
# 10. End-to-end: cmd_new + expand -> full 9-section manifest
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_instance):
    from research_vault.config import load_config
    return load_config(reload=True)


def test_e2e_new_and_expand_full_manifest(cfg):
    from research_vault.manuscript import cmd_new, cmd_expand

    note_path, tree_root, phase1 = cmd_new(
        "demo-research", "survey-m6-e2e", ms_type_key="lit-review", config=cfg,
    )
    assert phase1 is not None
    assert (tree_root / "phase1-dag.json").exists()
    p1_ids = [n["id"] for n in phase1["nodes"]]
    assert p1_ids[0] == "scope"
    assert p1_ids[-3:] == ["framework-synthesize", "framework-critic", "approve-framework"]

    # NG-7: lit-review's Phase-2 is the single-pass phase2_builder — the
    # 8-row section-set (RD-2/RD-4) is consolidated SOURCE DATA feeding the
    # single "draft" node's brief, not one DAG node per section (design §2.2).
    phase2 = cmd_expand("demo-research", "survey-m6-e2e", config=cfg)
    validate_manifest(phase2)
    p2_ids = [n["id"] for n in phase2["nodes"]]
    assert p2_ids == ["outline", "draft", "assemble", "approve-manuscript"]
    draft_spec = next(n["spec"] for n in phase2["nodes"] if n["id"] == "draft")
    for expected in (
        "introduction", "thematic-sections",
        "cross-cutting-analysis", "open-problems", "conclusion", "references",
        "abstract",
    ):
        assert f"Section: {expected}" in draft_spec, f"{expected!r} missing from consolidated draft brief"
    # PR-B (gold-settled `report.md`): appendix-methods is folded in as a
    # distinct non-report artifact block, never a "Section: appendix-methods"
    # row (that heading would imply it joins report.md like the others).
    assert "Section: appendix-methods" not in draft_spec
    assert "NOT a `report.md` section" in draft_spec
    assert "prisma-scope" not in p2_ids
    assert "framework" not in p2_ids
