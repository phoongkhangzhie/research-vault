"""test_coverage_allocation_wiring.py — PR-A: the coverage-allocation contract
wired into phase1-builder (produce `_coverage-map.md`), phase2-builder (the
ceiling fix — coverage-safe single-pass AND ledger-chunked fan-out), and
`approve-framework`'s autonomous evaluation (most-severe-wins fold-in).

Acceptance (from the dispatch brief):
  (b) delete one paper's allocation -> approve-framework BLOCKs naming the
      unallocated citekey.
  (c) a >ceiling corpus does NOT silently drop to the lossy path — each branch
      drafter gets its ledger-allocated `used` papers with a must-cite mandate.
  (d) determinism (no LLM in the gate), fail-closed on missing ledger.

sr: PR-A
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.manuscript.types.lit_review import (
    phase1_builder,
    phase2_builder,
    render_synthesize_brief,
    read_coverage_used_by_branch,
    read_coverage_used_citekeys,
)


def _write_corpus(path: Path, citekeys: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Corpus\n", "| Status | Citekey | Title |", "| --- | --- | --- |"]
    for ck in citekeys:
        lines.append(f"| [NEW] | {ck} | Some title |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_map(path: Path, *, used=None, clustered=None, deferred=None) -> None:
    fm = ["---", "coverage_map: true"]
    if used:
        fm.append("used:")
        for ck, branch in used:
            fm += [f"  - citekey: {ck}", f"    branch: {branch}"]
    if clustered:
        fm.append("clustered:")
        for ck, group, reason in clustered:
            fm += [f"  - citekey: {ck}", f"    group: {group}", f"    reason: {reason}"]
    if deferred:
        fm.append("deferred:")
        for ck, reason in deferred:
            fm += [f"  - citekey: {ck}", f"    reason: {reason}"]
    fm += ["---", "", "## rationale\n\nprose.\n"]
    path.write_text("\n".join(fm), encoding="utf-8")


# ---------------------------------------------------------------------------
# phase1 — framework-synthesize produces _coverage-map.md + injects corpus keys
# ---------------------------------------------------------------------------

def test_phase1_synthesize_produces_coverage_map(tmp_path: Path):
    notes = tmp_path / "notes"
    tree_root = notes / "manuscripts" / "survey-a"
    tree_root.mkdir(parents=True)
    _write_corpus(notes / "reviews" / "survey-a" / "_corpus.md", ["a2020", "b2021"])

    manifest = phase1_builder(
        project="demo", slug="survey-a",
        project_notes_dir=notes, tree_root=tree_root,
    )
    syn = next(n for n in manifest["nodes"] if n["id"] == "framework-synthesize")
    assert "_coverage-map.md" in syn["produces"]
    assert syn["produces"]["_coverage-map.md"].endswith("_coverage-map.md")
    # The exact corpus keys are injected into the brief (results-inject
    # discipline: the machine supplies the keys, the agent allocates).
    assert "a2020" in syn["spec"]
    assert "b2021" in syn["spec"]
    assert "coverage" in syn["spec"].lower()


def test_synthesize_brief_lists_injected_corpus_keys():
    brief = render_synthesize_brief(
        {"by-mechanism": "/x/_framework-candidate-by-mechanism.md"},
        corpus_citekeys=["smith2023", "jones2022"],
    )
    assert "smith2023" in brief
    assert "jones2022" in brief
    assert "used" in brief and "clustered" in brief and "deferred" in brief


# ---------------------------------------------------------------------------
# read helpers
# ---------------------------------------------------------------------------

def test_read_coverage_used_by_branch(tmp_path: Path):
    cmap = tmp_path / "_coverage-map.md"
    _write_map(
        cmap,
        used=[("a2020", "mech-A"), ("b2021", "mech-A"), ("c2022", "mech-B")],
        deferred=[("d2023", "out of scope")],
    )
    by_branch = read_coverage_used_by_branch(cmap)
    assert by_branch == {"mech-A": ["a2020", "b2021"], "mech-B": ["c2022"]}
    assert read_coverage_used_citekeys(cmap) == ["a2020", "b2021", "c2022"]


def test_read_coverage_missing_map_is_empty(tmp_path: Path):
    assert read_coverage_used_by_branch(tmp_path / "nope.md") == {}
    assert read_coverage_used_citekeys(tmp_path / "nope.md") == []


# ---------------------------------------------------------------------------
# phase2 ceiling fix — coverage-safe single-pass + ledger-chunked fan-out
# ---------------------------------------------------------------------------

def _phase2(tmp_path: Path, *, corpus, used, branches, ceiling):
    notes = tmp_path / "notes"
    tree_root = notes / "manuscripts" / "survey-c"
    tree_root.mkdir(parents=True)
    _write_corpus(notes / "reviews" / "survey-c" / "_corpus.md", corpus)
    _write_map(tree_root / "_coverage-map.md", used=used)

    class _Cfg:
        _raw = {"manuscript_lit_review": {"single_pass_corpus_ceiling": ceiling}}
    return phase2_builder(
        project="demo", slug="survey-c",
        project_notes_dir=notes, tree_root=tree_root,
        manuscript_fields={"spine_shape": "n-axis", "branches": branches},
        config=_Cfg(),
    ), tree_root


def test_phase2_single_pass_injects_full_used_mandate(tmp_path: Path):
    manifest, _ = _phase2(
        tmp_path,
        corpus=["a2020", "b2021", "c2022"],
        used=[("a2020", "br-A"), ("b2021", "br-A"), ("c2022", "br-B")],
        branches=["br-A", "br-B"],
        ceiling=40,  # 3 <= 40 -> single-pass
    )
    ids = [n["id"] for n in manifest["nodes"]]
    assert ids == ["outline", "draft", "assemble", "approve-manuscript"]
    draft = next(n for n in manifest["nodes"] if n["id"] == "draft")
    assert "COVERAGE MANDATE" in draft["spec"]
    for ck in ("a2020", "b2021", "c2022"):
        assert f"[[{ck}]]" in draft["spec"]


def test_phase2_above_ceiling_ledger_chunks_per_branch(tmp_path: Path):
    manifest, _ = _phase2(
        tmp_path,
        corpus=["a2020", "b2021", "c2022"],
        used=[("a2020", "br-A"), ("b2021", "br-A"), ("c2022", "br-B")],
        branches=["br-A", "br-B"],
        ceiling=1,  # 3 > 1 -> fan-out
    )
    ids = [n["id"] for n in manifest["nodes"]]
    assert "draft-br-a" in ids and "draft-br-b" in ids and "coherence" in ids
    draft_a = next(n for n in manifest["nodes"] if n["id"] == "draft-br-a")
    draft_b = next(n for n in manifest["nodes"] if n["id"] == "draft-br-b")
    # br-A drafter gets ONLY its allocated papers; br-B gets ONLY its.
    assert "[[a2020]]" in draft_a["spec"] and "[[b2021]]" in draft_a["spec"]
    assert "[[c2022]]" not in draft_a["spec"]
    assert "[[c2022]]" in draft_b["spec"]
    assert "[[a2020]]" not in draft_b["spec"]
    # The coherence node still sees the WHOLE used set (the coverage check).
    coherence = next(n for n in manifest["nodes"] if n["id"] == "coherence")
    for ck in ("a2020", "b2021", "c2022"):
        assert f"[[{ck}]]" in coherence["spec"]


def test_phase2_fanout_union_covers_all_used(tmp_path: Path):
    # Coverage-safety invariant: the union of per-branch chunks == whole used set.
    manifest, tree_root = _phase2(
        tmp_path,
        corpus=["a", "b", "c", "d"],
        used=[("a", "br-A"), ("b", "br-A"), ("c", "br-B"), ("d", "br-B")],
        branches=["br-A", "br-B"],
        ceiling=1,
    )
    by_branch = read_coverage_used_by_branch(tree_root / "_coverage-map.md")
    union = {ck for cks in by_branch.values() for ck in cks}
    assert union == {"a", "b", "c", "d"}


# ---------------------------------------------------------------------------
# approve-framework — the autonomous fold-in (acceptance b + d)
# ---------------------------------------------------------------------------

def _setup_approve_framework(tmp_path, *, corpus, coverage_used, origin="machine"):
    from research_vault.dag.store import RunState

    notes = tmp_path / "notes"
    tree_root = notes / "manuscripts" / "survey-af"
    tree_root.mkdir(parents=True)
    _write_corpus(notes / "reviews" / "survey-af" / "_corpus.md", corpus)

    (tree_root / "_manuscript.md").write_text(
        "---\n"
        "manuscript_type: lit-review\n"
        f"framework_origin: {origin}\n"
        "spine_shape: n-axis\n"
        "branches:\n  - br-A\n  - br-B\n"
        "---\n\nbody\n",
        encoding="utf-8",
    )
    if coverage_used is not None:
        _write_map(tree_root / "_coverage-map.md", used=coverage_used)

    canary = "CANARY-AF-123456"
    critique = tree_root / "_framework-critique.md"
    critique.write_text(f"---\nverdict: PASS\ncanary_id: {canary}\n---\n\nclean.\n", encoding="utf-8")

    manifest_path = tree_root / "phase1-dag.json"
    manifest_path.write_text("{}", encoding="utf-8")
    nodes_lookup = {
        "framework-critic": {
            "produces": {"_framework-critique.md": str(critique)},
            "canary_id": canary,
        }
    }
    run_state = RunState(run_id="manuscript-survey-af-phase1", manifest_path=str(manifest_path))
    return nodes_lookup, manifest_path, run_state


def test_approve_framework_all_allocated_gos(tmp_path: Path):
    from research_vault.dag.verbs import _evaluate_autonomous_gate
    from research_vault.review import autonomy as A

    nodes_lookup, manifest_path, run_state = _setup_approve_framework(
        tmp_path,
        corpus=["a2020", "b2021"],
        coverage_used=[("a2020", "br-A"), ("b2021", "br-B")],
    )
    disp = _evaluate_autonomous_gate("approve-framework", nodes_lookup, manifest_path, run_state)
    assert disp.disposition == A.GO, disp.reason


def test_approve_framework_delete_one_allocation_blocks_naming_it(tmp_path: Path):
    # Acceptance (b): remove one paper's allocation -> approve-framework BLOCKs
    # naming the unallocated citekey (never a silent GO).
    from research_vault.dag.verbs import _evaluate_autonomous_gate
    from research_vault.review import autonomy as A

    nodes_lookup, manifest_path, run_state = _setup_approve_framework(
        tmp_path,
        corpus=["a2020", "b2021", "dropped2099"],
        coverage_used=[("a2020", "br-A"), ("b2021", "br-B")],  # dropped2099 omitted
    )
    disp = _evaluate_autonomous_gate("approve-framework", nodes_lookup, manifest_path, run_state)
    assert disp.disposition != A.GO
    assert disp.disposition in (A.REVISE, A.HALT_DECLARE)
    assert "dropped2099" in disp.reason


def test_approve_framework_missing_map_with_corpus_blocks(tmp_path: Path):
    # Acceptance (d): fail-closed on a missing ledger when a real corpus exists.
    from research_vault.dag.verbs import _evaluate_autonomous_gate
    from research_vault.review import autonomy as A

    nodes_lookup, manifest_path, run_state = _setup_approve_framework(
        tmp_path,
        corpus=["a2020", "b2021"],
        coverage_used=None,  # no _coverage-map.md at all
    )
    disp = _evaluate_autonomous_gate("approve-framework", nodes_lookup, manifest_path, run_state)
    assert disp.disposition != A.GO
    assert "_coverage-map.md" in disp.reason


def test_approve_framework_human_spine_also_gated_on_coverage(tmp_path: Path):
    # The coverage contract applies regardless of spine origin — a human-authored
    # spine with an unallocated corpus still BLOCKs.
    from research_vault.dag.verbs import _evaluate_autonomous_gate
    from research_vault.review import autonomy as A

    nodes_lookup, manifest_path, run_state = _setup_approve_framework(
        tmp_path,
        corpus=["a2020", "unalloc2099"],
        coverage_used=[("a2020", "br-A")],
        origin="human",
    )
    disp = _evaluate_autonomous_gate("approve-framework", nodes_lookup, manifest_path, run_state)
    assert disp.disposition != A.GO
    assert "unalloc2099" in disp.reason


def test_approve_framework_no_corpus_still_gos(tmp_path: Path):
    # No frozen corpus -> coverage gate is a no-op; the pre-PR-A path (structural
    # + critic) governs unchanged. Guards against a false BLOCK for a manuscript
    # that never had a review corpus.
    from research_vault.dag.store import RunState
    from research_vault.dag.verbs import _evaluate_autonomous_gate
    from research_vault.review import autonomy as A

    notes = tmp_path / "notes"
    tree_root = notes / "manuscripts" / "survey-nc"
    tree_root.mkdir(parents=True)
    (tree_root / "_manuscript.md").write_text(
        "---\nmanuscript_type: lit-review\nframework_origin: machine\n"
        "spine_shape: n-axis\nbranches:\n  - br-A\n---\n\nbody\n",
        encoding="utf-8",
    )
    canary = "CANARY-NC-1"
    (tree_root / "_framework-critique.md").write_text(
        f"---\nverdict: PASS\ncanary_id: {canary}\n---\n\nclean.\n", encoding="utf-8"
    )
    manifest_path = tree_root / "phase1-dag.json"
    manifest_path.write_text("{}", encoding="utf-8")
    nodes_lookup = {
        "framework-critic": {
            "produces": {"_framework-critique.md": str(tree_root / "_framework-critique.md")},
            "canary_id": canary,
        }
    }
    run_state = RunState(run_id="r", manifest_path=str(manifest_path))
    disp = _evaluate_autonomous_gate("approve-framework", nodes_lookup, manifest_path, run_state)
    assert disp.disposition == A.GO, disp.reason
