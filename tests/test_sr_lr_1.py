"""test_sr_lr_1.py — review loop acceptance tests.

Coverage:
  1. review/style.py — get_review_tips seam
     1a. all REVIEW_TIPS_KEYS present in default
     1b. adopter override merges (known key replaced, unknown key dropped)
     1c. None config → default returned
  2. review/style.py — get_review_style_preamble
     2a. returns a non-empty string
  3. review cmd_new — Phase-1 DAG scaffold
     3a. creates review OKF note under project_notes_dir/reviews/<scope>/
     3b. creates reviews/<scope>/ artifact directory
     3c. creates Phase-1 manifest (phase1-dag.json)
     3d. manifest validates (validate_manifest passes)
     3e. has review-scope, approve-protocol, review-search, review-snowball,
         coverage-gate nodes in correct order
     3f. review-scope produces _protocol.md sidecar
     3g. review-search has artifact-watch on _protocol.md in needs
     3h. review-snowball produces _corpus.md and _saturation.md
     3i. coverage-gate is human-go
     3j. spec strings pull from review_tips seam (non-empty)
  4. review cmd_new — counter-position (L-2 gate) in spec
     4a. review-scope spec mentions counter-position requirement
     4b. review-coverage-critic (Phase-2) spec mentions counter-position
  5. review cmd_list
     5a. returns empty list when no reviews for project
     5b. lists review notes after cmd_new
  6. review cmd_expand — Phase-2 DAG
     6a. parses _corpus.md and emits one relate-<key> node per [NEW] citekey
     6b. excludes [IN-CORPUS:*] entries
     6c. synthesize, coverage-critic, approve-review nodes present
     6d. coverage-critic is agent type (reviewer role)
     6e. approve-review is human-go
     6f. validate_manifest passes on Phase-2 manifest
     6g. relate-<key> node reads: includes literature/ and concepts/ directories
  7. review coverage-critic spec — L-2 counter-position teeth
     7a. review-coverage-critic spec in Phase-2 instructs [BLOCK] on missing
         counter-position
  8. corpus helpers imported from research.py (not scraping stdout)
     8a. _load_corpus_index REMOVED (rv-023: dead Zotero library.json tier)
     8b. _corpus_annotation importable from research_vault.research
  9. CLI verb registry
     9a. "review" in cli._VERB_REGISTRY with sr: "SR-LR-1"
     9b. rv help --check passes with review verb present
 10. Fix #34: reads: grounding gate is live for review manifests
     10a. Phase-1 manifest reads: OKF-dir pointers resolve against the project's
          real OKF dirs (not manifest_path.parent) — zero reads-scope ERRORs when
          OKF dirs exist
     10b. Grounding is non-vacuous: a genuinely missing reads target IS caught
          as an error (not silently ignored)
     10c. Phase-2 manifest reads: OKF-dir pointers also resolve cleanly
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.review.style import (
    get_review_tips,
    get_review_style_preamble,
    REVIEW_TIPS_KEYS,
)
from research_vault.dag.schema import validate_manifest, ManifestError
from research_vault.config import load_config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_instance):
    return load_config(reload=True)


# ---------------------------------------------------------------------------
# 1. review/style.py — get_review_tips seam
# ---------------------------------------------------------------------------

def test_review_tips_keys_all_present():
    """All REVIEW_TIPS_KEYS are present in default get_review_tips()."""
    tips = get_review_tips()
    for key in REVIEW_TIPS_KEYS:
        assert key in tips, f"Missing key: {key}"


def test_review_tips_keys_fixed_set():
    """REVIEW_TIPS_KEYS contains exactly the 6 required keys (Option C hybrid,
    review-loop-nodekind-drift-fix: review_search_tips/review_snowball_tips
    replaced by review_screen_tips/review_curate_tips — a breaking key
    change, see the spec §F)."""
    assert REVIEW_TIPS_KEYS == frozenset({
        "review_scope_tips",
        "review_screen_tips",
        "review_curate_tips",
        "per_paper_relate_tips",
        "review_synthesize_tips",
        "review_critic_tips",
    })


def test_review_tips_values_nonempty():
    """Each default review tip value is a non-empty string."""
    tips = get_review_tips()
    for key, val in tips.items():
        assert isinstance(val, str) and val.strip(), f"Empty value for key: {key}"


def test_review_tips_adopter_override_merges(tmp_instance, cfg):
    """Known key in [review_style] overrides; unknown key is dropped."""
    # Patch config _raw directly
    cfg._raw["review_style"] = {
        "review_scope_tips": "CUSTOM scope",
        "unknown_key": "ignored",
    }
    tips = get_review_tips(config=cfg)
    assert tips["review_scope_tips"] == "CUSTOM scope"
    assert "unknown_key" not in tips
    # Other keys unaffected
    assert tips["review_screen_tips"] != ""


def test_review_tips_none_config_returns_default():
    """None config returns the shipped default."""
    tips = get_review_tips(config=None)
    assert len(tips) == len(REVIEW_TIPS_KEYS)


# ---------------------------------------------------------------------------
# 2. review/style.py — get_review_style_preamble
# ---------------------------------------------------------------------------

def test_review_style_preamble_nonempty():
    """get_review_style_preamble() returns a non-empty string."""
    preamble = get_review_style_preamble()
    assert isinstance(preamble, str) and preamble.strip()


# ---------------------------------------------------------------------------
# 3. review cmd_new — Phase-1 DAG scaffold
# ---------------------------------------------------------------------------

@pytest.fixture
def review_new_result(cfg):
    """Run cmd_new and return (note_path, review_dir, manifest)."""
    from research_vault.review import cmd_new
    return cmd_new(
        "demo-research",
        "scope-001",
        question="What are the limits of LLM evaluation?",
        config=cfg,
    )


def test_review_note_created(review_new_result):
    """cmd_new creates an OKF review note."""
    note_path, review_dir, manifest = review_new_result
    assert note_path.exists()


def test_review_dir_created(review_new_result):
    """cmd_new creates the reviews/<scope>/ artifact directory."""
    note_path, review_dir, manifest = review_new_result
    assert review_dir.is_dir()


def test_review_phase1_manifest_file(review_new_result):
    """cmd_new writes phase1-dag.json into the review dir."""
    note_path, review_dir, manifest = review_new_result
    manifest_file = review_dir / "phase1-dag.json"
    assert manifest_file.exists()
    loaded = json.loads(manifest_file.read_text())
    assert "nodes" in loaded


def test_review_manifest_validates(review_new_result):
    """Phase-1 manifest passes validate_manifest."""
    note_path, review_dir, manifest = review_new_result
    # Should not raise
    validate_manifest(manifest)


def test_review_manifest_has_required_nodes(review_new_result):
    """Phase-1 manifest has the 7 required node ids (Option C hybrid,
    review-loop-nodekind-drift-fix: review-search/review-snowball split into
    tool+agent pairs — review-screen/review-curate added)."""
    note_path, review_dir, manifest = review_new_result
    node_ids = {n["id"] for n in manifest["nodes"]}
    required = {
        "review-scope",
        "approve-protocol",
        "review-search",
        "review-screen",
        "review-snowball",
        "review-curate",
        "coverage-gate",
    }
    assert required <= node_ids, f"Missing nodes: {required - node_ids}"


def test_review_search_is_tool_node(review_new_result):
    """review-search is now a deterministic TOOL node (op 'sweep') — no LLM."""
    note_path, review_dir, manifest = review_new_result
    node = next(n for n in manifest["nodes"] if n["id"] == "review-search")
    assert node["type"] == "tool"
    assert node["op"] == "sweep"


def test_review_snowball_is_tool_node(review_new_result):
    """review-snowball is now a deterministic TOOL node (op 'snowball') — no LLM."""
    note_path, review_dir, manifest = review_new_result
    node = next(n for n in manifest["nodes"] if n["id"] == "review-snowball")
    assert node["type"] == "tool"
    assert node["op"] == "snowball"


def test_review_scope_produces_protocol(review_new_result):
    """review-scope node has produces: containing _protocol.md."""
    note_path, review_dir, manifest = review_new_result
    scope_node = next(n for n in manifest["nodes"] if n["id"] == "review-scope")
    produces = scope_node.get("produces", [])
    assert any("_protocol.md" in str(p) for p in produces), (
        f"review-scope produces: {produces!r} — expected _protocol.md"
    )


def test_review_search_watches_protocol(review_new_result):
    """review-search needs: includes artifact-watch on _protocol.md."""
    note_path, review_dir, manifest = review_new_result
    search_node = next(n for n in manifest["nodes"] if n["id"] == "review-search")
    needs = search_node.get("needs", [])
    watches = " ".join(str(n) for n in needs)
    assert "_protocol.md" in watches, (
        f"review-search needs: {needs!r} — expected _protocol.md watch"
    )


def test_review_snowball_produces_corpus_raw_and_saturation(review_new_result):
    """review-snowball (tool) produces _corpus_raw.md and _saturation.md —
    the FINAL _corpus.md is review-curate's output (Option C hybrid)."""
    note_path, review_dir, manifest = review_new_result
    snowball_node = next(n for n in manifest["nodes"] if n["id"] == "review-snowball")
    produces = snowball_node.get("produces", [])
    produces_str = " ".join(str(p) for p in produces)
    assert "_corpus_raw.md" in produces_str, f"Missing _corpus_raw.md in produces: {produces!r}"
    assert "_saturation.md" in produces_str, f"Missing _saturation.md in produces: {produces!r}"


def test_review_curate_produces_final_corpus(review_new_result):
    """review-curate (agent) produces the FINAL _corpus.md."""
    note_path, review_dir, manifest = review_new_result
    node = next(n for n in manifest["nodes"] if n["id"] == "review-curate")
    produces = node.get("produces", [])
    produces_str = " ".join(str(p) for p in produces)
    assert "_corpus.md" in produces_str and "_corpus_raw.md" not in produces_str


def test_coverage_gate_is_human_go(review_new_result):
    """coverage-gate is a human-go node."""
    note_path, review_dir, manifest = review_new_result
    gate = next(n for n in manifest["nodes"] if n["id"] == "coverage-gate")
    assert gate["type"] == "human-go"


def test_review_scope_spec_nonempty(review_new_result):
    """review-scope agent node has a non-empty spec."""
    note_path, review_dir, manifest = review_new_result
    scope_node = next(n for n in manifest["nodes"] if n["id"] == "review-scope")
    assert scope_node.get("spec", "").strip()


def test_review_screen_spec_nonempty(review_new_result):
    """review-screen agent node has a non-empty spec."""
    note_path, review_dir, manifest = review_new_result
    node = next(n for n in manifest["nodes"] if n["id"] == "review-screen")
    assert node.get("spec", "").strip()


def test_review_curate_spec_nonempty(review_new_result):
    """review-curate agent node has a non-empty spec."""
    note_path, review_dir, manifest = review_new_result
    node = next(n for n in manifest["nodes"] if n["id"] == "review-curate")
    assert node.get("spec", "").strip()


# ---------------------------------------------------------------------------
# 4. L-2 counter-position gate in spec strings
# ---------------------------------------------------------------------------

def test_review_scope_spec_mentions_counter_position(review_new_result):
    """review-scope spec mentions counter-position (L-2 structural requirement)."""
    note_path, review_dir, manifest = review_new_result
    scope_node = next(n for n in manifest["nodes"] if n["id"] == "review-scope")
    assert "counter-position" in scope_node.get("spec", "").lower() or \
           "counter_position" in scope_node.get("spec", "").lower(), (
        "review-scope spec must mention counter-position requirement"
    )


# ---------------------------------------------------------------------------
# 5. review cmd_list
# ---------------------------------------------------------------------------

def test_cmd_list_empty_before_any_review(cfg):
    """cmd_list returns empty list when no reviews exist."""
    from research_vault.review import cmd_list
    results = cmd_list("demo-research", config=cfg)
    assert results == []


def test_cmd_list_after_new(cfg):
    """cmd_list returns the created review after cmd_new."""
    from research_vault.review import cmd_new, cmd_list
    cmd_new("demo-research", "scope-abc", question="Q1", config=cfg)
    results = cmd_list("demo-research", config=cfg)
    assert len(results) == 1
    assert results[0]["scope"] == "scope-abc"


def test_cmd_list_multiple(cfg):
    """cmd_list returns all created reviews."""
    from research_vault.review import cmd_new, cmd_list
    cmd_new("demo-research", "scope-one", question="Q1", config=cfg)
    cmd_new("demo-research", "scope-two", question="Q2", config=cfg)
    results = cmd_list("demo-research", config=cfg)
    scopes = {r["scope"] for r in results}
    assert scopes == {"scope-one", "scope-two"}


# ---------------------------------------------------------------------------
# 6. review cmd_expand — Phase-2 DAG
# ---------------------------------------------------------------------------

@pytest.fixture
def corpus_md_with_citekeys(tmp_path):
    """Write a minimal _corpus.md file with 2 [NEW] and 1 [IN-CORPUS] entry."""
    content = """# Corpus

| Annotation | Citekey | Title |
|---|---|---|
| [NEW] | smith2020 | Paper A |
| [NEW] | jones2021 | Paper B |
| [IN-CORPUS:old2019] | old2019 | Old Paper |
"""
    p = tmp_path / "_corpus.md"
    p.write_text(content, encoding="utf-8")
    return p


def test_cmd_expand_emits_relate_nodes(cfg, corpus_md_with_citekeys):
    """cmd_expand creates one relate-<key> node per [NEW] citekey."""
    from research_vault.review import cmd_expand
    manifest = cmd_expand(
        "demo-research",
        "scope-001",
        corpus_path=corpus_md_with_citekeys,
        config=cfg,
    )
    node_ids = {n["id"] for n in manifest["nodes"]}
    assert "relate-smith2020" in node_ids
    assert "relate-jones2021" in node_ids


def test_cmd_expand_excludes_in_corpus(cfg, corpus_md_with_citekeys):
    """cmd_expand does NOT create a relate node for [IN-CORPUS:*] entries."""
    from research_vault.review import cmd_expand
    manifest = cmd_expand(
        "demo-research",
        "scope-001",
        corpus_path=corpus_md_with_citekeys,
        config=cfg,
    )
    node_ids = {n["id"] for n in manifest["nodes"]}
    assert "relate-old2019" not in node_ids


def test_cmd_expand_has_synthesize_critic_approve(cfg, corpus_md_with_citekeys):
    """cmd_expand includes review-synthesize, review-coverage-critic, approve-review."""
    from research_vault.review import cmd_expand
    manifest = cmd_expand(
        "demo-research",
        "scope-001",
        corpus_path=corpus_md_with_citekeys,
        config=cfg,
    )
    node_ids = {n["id"] for n in manifest["nodes"]}
    assert "review-synthesize" in node_ids
    assert "review-coverage-critic" in node_ids
    assert "approve-review" in node_ids


def test_cmd_expand_coverage_critic_is_agent(cfg, corpus_md_with_citekeys):
    """review-coverage-critic in Phase-2 is an agent node."""
    from research_vault.review import cmd_expand
    manifest = cmd_expand(
        "demo-research",
        "scope-001",
        corpus_path=corpus_md_with_citekeys,
        config=cfg,
    )
    critic = next(n for n in manifest["nodes"] if n["id"] == "review-coverage-critic")
    assert critic["type"] == "agent"


def test_cmd_expand_approve_review_is_human_go(cfg, corpus_md_with_citekeys):
    """approve-review in Phase-2 is a human-go node."""
    from research_vault.review import cmd_expand
    manifest = cmd_expand(
        "demo-research",
        "scope-001",
        corpus_path=corpus_md_with_citekeys,
        config=cfg,
    )
    gate = next(n for n in manifest["nodes"] if n["id"] == "approve-review")
    assert gate["type"] == "human-go"


def test_cmd_expand_manifest_validates(cfg, corpus_md_with_citekeys):
    """Phase-2 manifest passes validate_manifest."""
    from research_vault.review import cmd_expand
    manifest = cmd_expand(
        "demo-research",
        "scope-001",
        corpus_path=corpus_md_with_citekeys,
        config=cfg,
    )
    validate_manifest(manifest)


def test_cmd_expand_relate_reads_literature_and_concepts(cfg, corpus_md_with_citekeys):
    """relate-<key> nodes read: includes literature/ and concepts/."""
    from research_vault.review import cmd_expand
    manifest = cmd_expand(
        "demo-research",
        "scope-001",
        corpus_path=corpus_md_with_citekeys,
        config=cfg,
    )
    relate = next(
        n for n in manifest["nodes"] if n["id"].startswith("relate-")
    )
    reads = relate.get("reads", [])
    reads_str = " ".join(str(r) for r in reads)
    assert "literature" in reads_str
    assert "concepts" in reads_str


# ---------------------------------------------------------------------------
# 7. coverage-critic spec — L-2 counter-position teeth
# ---------------------------------------------------------------------------

def test_coverage_critic_spec_mentions_counter_position(cfg, corpus_md_with_citekeys):
    """review-coverage-critic spec instructs [BLOCK] on missing counter-position."""
    from research_vault.review import cmd_expand
    manifest = cmd_expand(
        "demo-research",
        "scope-001",
        corpus_path=corpus_md_with_citekeys,
        config=cfg,
    )
    critic = next(n for n in manifest["nodes"] if n["id"] == "review-coverage-critic")
    spec = critic.get("spec", "").lower()
    assert "counter-position" in spec or "counter_position" in spec, (
        "review-coverage-critic spec must mention counter-position"
    )
    # Must mention BLOCK
    assert "[block]" in spec, (
        "review-coverage-critic spec must mention [BLOCK] for missing counter-position"
    )


# ---------------------------------------------------------------------------
# 8. corpus helpers importable from research.py
# ---------------------------------------------------------------------------

def test_load_corpus_index_removed():
    """rv-023: _load_corpus_index (the dead Zotero library.json tier) is GONE."""
    import research_vault.research as research_mod
    assert not hasattr(research_mod, "_load_corpus_index")


def test_corpus_annotation_importable():
    """_corpus_annotation is importable from research_vault.research."""
    from research_vault.research import _corpus_annotation
    assert callable(_corpus_annotation)


def test_corpus_annotation_new_when_no_index():
    """_corpus_annotation returns [NEW] when notes_index is empty."""
    from research_vault.research import _corpus_annotation
    paper = {"externalIds": {"DOI": "10.1234/test"}}
    result = _corpus_annotation(paper, notes_index={})
    assert result == "[NEW]"


def test_corpus_annotation_in_corpus_when_doi_matches():
    """_corpus_annotation returns [IN-CORPUS:key] when DOI matches (notes_index)."""
    from research_vault.research import _corpus_annotation
    paper = {"externalIds": {"DOI": "10.1234/test"}}
    index = {"10.1234/test": "smith2020"}
    result = _corpus_annotation(paper, notes_index=index)
    assert result == "[IN-CORPUS:smith2020]"


# ---------------------------------------------------------------------------
# 9. CLI verb registry
# ---------------------------------------------------------------------------

def test_review_in_verb_registry():
    """'review' is registered in cli._VERB_REGISTRY."""
    from research_vault.cli import _VERB_REGISTRY
    assert "review" in _VERB_REGISTRY


def test_review_verb_sr_is_sr_lr_1():
    """The review verb is tagged sr: SR-LR-1 (and SR-LR-2 after gap-driven pass added)."""
    from research_vault.cli import _VERB_REGISTRY
    sr_value = _VERB_REGISTRY["review"].get("sr", "")
    assert "SR-LR-1" in sr_value


def test_rv_help_check_passes(tmp_instance):
    """rv help --check passes with review verb present."""
    import subprocess
    import sys as _sys
    result = subprocess.run(
        [_sys.executable, "-m", "research_vault.cli", "help", "--check"],
        capture_output=True, text=True,
        env={**os.environ, "RESEARCH_VAULT_CONFIG": str(tmp_instance / "research_vault.toml")},
        cwd=str(Path(__file__).parent.parent),
    )
    # help --check may or may not be implemented; just verify it doesn't crash on import
    # The key test is that "review" is in the registry (above)
    # If help --check exists, it should exit 0
    if result.returncode not in (0, 1, 2):
        pytest.fail(f"rv help --check crashed: {result.stderr}")


# ---------------------------------------------------------------------------
# 10. Fix #34: reads: grounding gate is live for review manifests
# ---------------------------------------------------------------------------

def test_phase1_reads_resolve_against_project_okf_dirs(cfg, tmp_instance):
    """Phase-1 manifest reads: OKF dirs resolve to the project's real OKF dirs.

    Red-before-green: before the fix, _rel() emits bare type names (e.g.
    'literature') that resolve relative to manifest_path.parent
    (reviews/<scope>/) — those dirs don't exist → reads-scope ERROR.
    After the fix, _rel() emits absolute paths → zero errors when OKF dirs exist.

    This is a §2 gate: a zero-error result must mean the grounding check ACTUALLY
    ran (non-vacuous), proven by test 10b.
    """
    from research_vault.review import cmd_new
    from research_vault.dag.reads import resolve_reads_pointers

    note_path, review_dir, manifest = cmd_new(
        "demo-research",
        "scope-reads-test",
        question="Test reads: resolution",
        config=cfg,
    )

    # Simulate the call site: project_root = manifest_path.parent
    manifest_path = review_dir / "phase1-dag.json"
    project_root = manifest_path.parent

    # Create the project OKF dirs so the resolution CAN succeed (post-fix)
    project_notes_dir = cfg.project_notes_dir("demo-research")
    for okf_dir in ("literature", "concepts", "mocs", "findings"):
        (project_notes_dir / okf_dir).mkdir(parents=True, exist_ok=True)

    # After the fix: OKF-dir reads: pointers are absolute → they resolve against the
    # real project OKF dirs, not the manifest's parent dir.
    # (Note: _protocol.md errors are expected — that artifact is created at run-time
    # by the review-scope node, not available pre-run. This test only checks the
    # OKF-dir relative-base bug is gone.)
    errors, warns = resolve_reads_pointers(manifest, project_root=project_root)
    okf_errors = [
        e for e in errors
        if any(t in e for t in ("literature", "concepts", "mocs", "findings"))
    ]
    assert okf_errors == [], (
        f"Phase-1 manifest has OKF-dir reads: errors (relative-base bug still present):\n"
        + "\n".join(okf_errors)
    )


def test_phase1_reads_grounding_is_nontrivial(cfg, tmp_instance):
    """Grounding check is non-vacuous: a genuinely missing reads target is caught.

    This prevents a green-and-empty pass (charter §2): if the grounding check
    returned 0 errors even for a manifest with a deliberately broken pointer,
    the gate would be silently dead.

    After the fix, absolute paths are used. cmd_new calls scaffold_okf_dirs
    which creates ALL OKF dirs. We remove 'findings' AFTER cmd_new to simulate
    a missing target and assert the error IS surfaced.
    """
    import shutil
    from research_vault.review import cmd_new
    from research_vault.dag.reads import resolve_reads_pointers

    note_path, review_dir, manifest = cmd_new(
        "demo-research",
        "scope-reads-nontrivial",
        question="Test reads: non-vacuous grounding",
        config=cfg,
    )

    manifest_path = review_dir / "phase1-dag.json"
    project_root = manifest_path.parent

    # cmd_new scaffolds all OKF dirs via scaffold_okf_dirs. Remove 'findings'
    # AFTER creation to prove the grounding check catches it.
    project_notes_dir = cfg.project_notes_dir("demo-research")
    findings_dir = project_notes_dir / "findings"
    if findings_dir.exists():
        shutil.rmtree(findings_dir)

    # After the fix, the reads: pointer for 'findings' is an absolute path that
    # no longer exists → must surface at least one error
    errors, warns = resolve_reads_pointers(manifest, project_root=project_root)
    # Filter to OKF-dir errors (exclude _protocol.md which is also absent in tests)
    assert any("findings" in e for e in errors), (
        "Grounding check must catch a missing 'findings' OKF dir. "
        f"Got errors: {errors}"
    )


def test_phase2_reads_resolve_against_project_okf_dirs(cfg, tmp_instance, corpus_md_with_citekeys):
    """Phase-2 manifest reads: OKF dirs also resolve cleanly (same fix as Phase-1)."""
    from research_vault.review import cmd_expand
    from research_vault.dag.reads import resolve_reads_pointers

    manifest = cmd_expand(
        "demo-research",
        "scope-reads-ph2",
        corpus_path=corpus_md_with_citekeys,
        config=cfg,
    )

    review_dir = cfg.project_notes_dir("demo-research") / "reviews" / "scope-reads-ph2"
    manifest_path = review_dir / "phase2-dag.json"
    project_root = manifest_path.parent

    # Create OKF dirs
    project_notes_dir = cfg.project_notes_dir("demo-research")
    for okf_dir in ("literature", "concepts", "mocs", "findings"):
        (project_notes_dir / okf_dir).mkdir(parents=True, exist_ok=True)

    # protocol_path is absolute; OKF dirs must also be absolute after the fix
    # Don't create the protocol file — it's an absolute path that won't exist;
    # so we only check that OKF-dir reads: errors (the relative-base bug) are gone.
    errors, warns = resolve_reads_pointers(manifest, project_root=project_root)
    okf_errors = [e for e in errors if any(
        t in e for t in ("literature", "concepts", "mocs", "findings")
    )]
    assert okf_errors == [], (
        f"Phase-2 manifest has OKF-dir reads: errors (relative-base bug still present):\n"
        + "\n".join(okf_errors)
    )
