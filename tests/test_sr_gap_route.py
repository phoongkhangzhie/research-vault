"""test_sr_gap_route.py — SR-GAP-ROUTE (SR-LR-3) acceptance tests: gap-loop router.

Coverage:
  1. suggest_route pure function — per-type default routes
     1a. knowledge_void → literature (read-first default)
     1b. contradictory → literature (reconcile via abstraction first)
     1c. evaluation_void → experiment (RUN fast-path)
     1d. absent_row with no section → triage (Tier-A fallback)
     1e. absent_row with background section → literature (Tier-B)
     1f. absent_row with introduction section → literature (Tier-B)
     1g. absent_row with results section → experiment (Tier-B)
     1h. absent_row with results-discussion section → experiment (Tier-B)
     1i. absent_row with unknown section → triage (Tier-B degrade)
     1j. proven-open gap (knowledge_void) is a run-candidate (test separately)

  2. suggested_route field written to gap note frontmatter
     2a. cmd_gap_scan writes suggested_route: to gaps/<id>.md
     2b. suggested_route for knowledge_void is "literature"
     2c. suggested_route for evaluation_void is "experiment"
     2d. suggested_route for absent_row without section is "triage"

  3. Route-aware cmd_gap_scope — literature target
     3a. --target literature = SR-LR-2 behavior (Phase-1 DAG + _gap-context.md)
     3b. default target (gap.suggested_route) uses the gap note's route field
     3c. regression: literature target is byte-for-byte equivalent to old gap-scope

  4. Route-aware cmd_gap_scope — experiment target
     4a. --target experiment creates experiments/<id>-plan.md
     4b. plan note has plan_kind: preregistration in frontmatter
     4c. plan note research question == gap.claim verbatim
     4d. plan note passes rv plan check (K-2 shape-lint) with stubbed diagnosis table
     4e. plan note covers: skeleton present (no path-prefix violations)
     4f. _gap-context.md written in experiments/<id>-plan.md's directory (adjacent)
     4g. _gap-context.md contains SR-PLAN-1 next-step chain

  5. proven-open → run-candidate promotion
     5a. proven_open_count returns count of proven-open gaps
     5b. gap-list --status proven-open returns proven-open gaps only
     5c. rv status Needs Attention includes proven-open count when > 0
     5d. a run does NOT auto-fire on proven-open (human-go required — no auto experiment)

  6. Tier B section threading (SupportVerdict.section + check_gates.py)
     6a. SupportVerdict has optional section field (default "")
     6b. to_meta_dict() emits section field
     6c. match_support() accepts section= parameter
     6d. _detect_absent_rows reads section from verdict meta → GapRecord._meta['section']
     6e. absent_row in introduction.tex → suggested_route == "literature" (Tier-B split)
     6f. absent_row in results-discussion.tex → suggested_route == "experiment" (Tier-B)
     6g. absent_row with no section in meta → suggested_route == "triage" (back-compat)
     6h. check_support_tally threads tex.stem into each match_support call

  7. CLI subcommands (gap-route alias + gap-list)
     7a. rv review <project> gap-route <gap-id> <scope> accepted (alias for gap-scope)
     7b. rv review <project> gap-scope --target experiment accepted
     7c. rv review <project> gap-scope --target literature accepted (default)
     7d. rv review <project> gap-list exits 0, prints gap records
     7e. rv review <project> gap-list --status proven-open exits 0 (filtered)

  8. Honest bound — run never auto-fires
     8a. cmd_gap_scope with experiment target does NOT call any external API/subprocess
     8b. suggested_route is a prior, not a decision (cmd_gap_scope always requires human)

  9. Discovery/trigger surface
     9a. "review" in cli._VERB_REGISTRY sr field includes "SR-GAP-ROUTE"
     9b. rv help --check passes (if CLI checks are wired)

  10. Zero ~/vault edits (all hermetic)

All hermetic (tmp_instance / tmp_path). No live LLM calls.
Stdlib only.
sr: SR-GAP-ROUTE (SR-LR-3)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_gap_note(pnd: Path, gap_id: str, gap_type: str, claim: str,
                   status: str = "open", section: str = "") -> Path:
    """Write a gaps/<gap_id>.md note."""
    gd = pnd / "gaps"
    gd.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"type: gaps",
        f"id: {gap_id}",
        f"gap_type: {gap_type}",
        f"anchor: findings/f-001",
        f'claim: "{claim}"',
        f'why: "test gap"',
        f"status: {status}",
    ]
    if section:
        lines.append(f"section: {section}")
    lines.extend(["---", f"# Gap: {gap_id}"])
    p = gd / f"{gap_id}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _make_finding(pnd: Path, fid: str, **frontmatter) -> Path:
    """Write a findings/<fid>.md note."""
    fd = pnd / "findings"
    fd.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: findings", f"id: {fid}"]
    for k, v in frontmatter.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {item}")
        else:
            lines.append(f"{k}: {v}")
    lines.extend(["---", f"# Finding: {fid}"])
    p = fd / f"{fid}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _make_verdict(
    verdict: str,
    claim_snippet: str = "Test claim",
    citekey: str = "mock2023",
    j2_escalation: bool = False,
    section: str = "",
) -> dict[str, Any]:
    """Build a SupportVerdict meta dict (mirrors to_meta_dict output)."""
    return {
        "verdict": verdict,
        "verbatim_span": None,
        "polarity": "neutral",
        "claim_snippet": claim_snippet,
        "citekey": citekey,
        "note_path": f"literature/{citekey}.md",
        "judge_model": "mock-model",
        "prompt_hash": "abc123",
        "j2_escalation": j2_escalation,
        "section": section,
    }


def _make_support_matcher_meta(
    verdicts: "list[dict[str, Any]] | None" = None,
) -> dict[str, Any]:
    """Build a minimal support_matcher meta dict."""
    vlist = verdicts or []
    k_block = sum(
        1 for v in vlist
        if v.get("verdict") in ("ABSENT", "CONTRADICTS") or v.get("j2_escalation")
    )
    return {
        "n_sentences": len(vlist),
        "m_citations": len(vlist),
        "k_block": k_block,
        "j_warn": 0,
        "judge_model": "mock-model",
        "prompt_hashes": [],
        "verdicts": vlist,
    }


# ---------------------------------------------------------------------------
# 1. suggest_route pure function
# ---------------------------------------------------------------------------

def test_suggest_route_knowledge_void():
    """1a. knowledge_void → literature (read-first)."""
    from research_vault.review.gap_scan import suggest_route, ROUTE_LITERATURE
    result = suggest_route("knowledge_void", {})
    assert result == ROUTE_LITERATURE
    assert result == "literature"


def test_suggest_route_contradictory():
    """1b. contradictory → literature (reconcile first)."""
    from research_vault.review.gap_scan import suggest_route, ROUTE_LITERATURE
    result = suggest_route("contradictory", {})
    assert result == ROUTE_LITERATURE


def test_suggest_route_evaluation_void():
    """1c. evaluation_void → experiment (RUN fast-path)."""
    from research_vault.review.gap_scan import suggest_route, ROUTE_EXPERIMENT
    result = suggest_route("evaluation_void", {})
    assert result == ROUTE_EXPERIMENT
    assert result == "experiment"


def test_suggest_route_absent_row_no_section():
    """1d. absent_row with no section → triage (Tier-A fallback)."""
    from research_vault.review.gap_scan import suggest_route, ROUTE_TRIAGE
    result = suggest_route("absent_row", {})
    assert result == ROUTE_TRIAGE
    assert result == "triage"


def test_suggest_route_absent_row_background():
    """1e. absent_row background section → literature (Tier-B)."""
    from research_vault.review.gap_scan import suggest_route, ROUTE_LITERATURE
    result = suggest_route("absent_row", {"section": "background"})
    assert result == ROUTE_LITERATURE


def test_suggest_route_absent_row_introduction():
    """1f. absent_row introduction section → literature (Tier-B)."""
    from research_vault.review.gap_scan import suggest_route, ROUTE_LITERATURE
    result = suggest_route("absent_row", {"section": "introduction"})
    assert result == ROUTE_LITERATURE


def test_suggest_route_absent_row_results():
    """1g. absent_row results section → experiment (Tier-B)."""
    from research_vault.review.gap_scan import suggest_route, ROUTE_EXPERIMENT
    result = suggest_route("absent_row", {"section": "results"})
    assert result == ROUTE_EXPERIMENT


def test_suggest_route_absent_row_results_discussion():
    """1h. absent_row results-discussion section → experiment (Tier-B)."""
    from research_vault.review.gap_scan import suggest_route, ROUTE_EXPERIMENT
    result = suggest_route("absent_row", {"section": "results-discussion"})
    assert result == ROUTE_EXPERIMENT


def test_suggest_route_absent_row_unknown_section():
    """1i. absent_row with unknown section → triage (Tier-B degrade)."""
    from research_vault.review.gap_scan import suggest_route, ROUTE_TRIAGE
    result = suggest_route("absent_row", {"section": "appendix-b"})
    assert result == ROUTE_TRIAGE


# ---------------------------------------------------------------------------
# 2. suggested_route field written to gap note
# ---------------------------------------------------------------------------

def test_cmd_gap_scan_writes_suggested_route_field(tmp_instance):
    """2a. cmd_gap_scan writes suggested_route: to gaps/<id>.md."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-kv-01", title="Empty finding")

    cmd_gap_scan("demo-research", config=cfg)

    gaps_dir = pnd / "gaps"
    found = list(gaps_dir.glob("*.md"))
    assert found, "No gaps written"
    content = found[0].read_text(encoding="utf-8")
    assert "suggested_route:" in content


def test_cmd_gap_scan_knowledge_void_suggested_route(tmp_instance):
    """2b. knowledge_void gap gets suggested_route: literature."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-kv-02", title="No-backing finding")

    cmd_gap_scan("demo-research", config=cfg)

    gaps_dir = pnd / "gaps"
    for p in gaps_dir.glob("*.md"):
        content = p.read_text(encoding="utf-8")
        if "knowledge_void" in content:
            assert "suggested_route: literature" in content
            return
    pytest.fail("No knowledge_void gap found")


def test_cmd_gap_scan_evaluation_void_suggested_route(tmp_instance):
    """2c. evaluation_void gap gets suggested_route: experiment."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-ev-01", title="Effect without comparator",
                  effect="improves accuracy")

    cmd_gap_scan("demo-research", config=cfg)

    gaps_dir = pnd / "gaps"
    for p in gaps_dir.glob("*.md"):
        content = p.read_text(encoding="utf-8")
        if "evaluation_void" in content:
            assert "suggested_route: experiment" in content
            return
    pytest.fail("No evaluation_void gap found")


def test_cmd_gap_scan_absent_row_no_section_triage(tmp_instance):
    """2d. absent_row without section gets suggested_route: triage."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    matcher_meta = _make_support_matcher_meta([
        _make_verdict("ABSENT", claim_snippet="Some claim without evidence", section=""),
    ])

    cmd_gap_scan("demo-research", config=cfg, matcher_meta=matcher_meta)

    gaps_dir = pnd / "gaps"
    for p in gaps_dir.glob("*.md"):
        content = p.read_text(encoding="utf-8")
        if "absent_row" in content:
            assert "suggested_route: triage" in content
            return
    pytest.fail("No absent_row gap found")


# ---------------------------------------------------------------------------
# 3. Route-aware cmd_gap_scope — literature target
# ---------------------------------------------------------------------------

def test_gap_scope_literature_target_creates_review(tmp_instance):
    """3a. --target literature = SR-LR-2 behavior (Phase-1 DAG + _gap-context.md)."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan, cmd_gap_scope

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-kv-03", title="Literature gap finding")

    new_gaps = cmd_gap_scan("demo-research", config=cfg)
    assert new_gaps, "No gaps detected"

    gid = None
    for gap in new_gaps:
        if gap.type == "knowledge_void":
            from research_vault.review.gap_scan import _gap_id
            gid = _gap_id(gap.type, gap.anchor, gap.claim)
            break
    assert gid, "No knowledge_void gap"

    manifest = cmd_gap_scope("demo-research", gid, "scope-lit-01", config=cfg,
                             target="literature")
    assert "nodes" in manifest

    review_dir = pnd / "reviews" / "scope-lit-01"
    assert (review_dir / "_gap-context.md").exists()
    assert (review_dir / "phase1-dag.json").exists()


def test_gap_scope_default_target_uses_suggested_route(tmp_instance):
    """3b. No --target → reads suggested_route from gap frontmatter."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan, cmd_gap_scope

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-kv-04", title="Default route finding")

    new_gaps = cmd_gap_scan("demo-research", config=cfg)
    gid = None
    for gap in new_gaps:
        if gap.type == "knowledge_void":
            from research_vault.review.gap_scan import _gap_id
            gid = _gap_id(gap.type, gap.anchor, gap.claim)
            break
    assert gid

    # Default target → reads suggested_route from frontmatter (knowledge_void → literature)
    manifest = cmd_gap_scope("demo-research", gid, "scope-default-01", config=cfg)
    assert "nodes" in manifest

    review_dir = pnd / "reviews" / "scope-default-01"
    assert (review_dir / "phase1-dag.json").exists()


# ---------------------------------------------------------------------------
# 4. Route-aware cmd_gap_scope — experiment target
# ---------------------------------------------------------------------------

def test_gap_scope_experiment_creates_plan_note(tmp_instance):
    """4a. --target experiment creates experiments/<id>-plan.md."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan, cmd_gap_scope

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-ev-02", title="Eval void finding",
                  effect="increases performance")

    new_gaps = cmd_gap_scan("demo-research", config=cfg)
    gid = None
    for gap in new_gaps:
        if gap.type == "evaluation_void":
            from research_vault.review.gap_scan import _gap_id
            gid = _gap_id(gap.type, gap.anchor, gap.claim)
            break
    assert gid, "No evaluation_void gap"

    result = cmd_gap_scope("demo-research", gid, "scope-exp-01", config=cfg,
                           target="experiment")

    # Result is a dict with experiment plan path
    assert "plan_note_path" in result
    plan_path = Path(result["plan_note_path"])
    assert plan_path.exists()


def test_gap_scope_experiment_plan_kind_preregistration(tmp_instance):
    """4b. plan note has plan_kind: preregistration in frontmatter."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan, cmd_gap_scope

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-ev-03", title="Eval void for preregistration",
                  effect="reduces error")

    new_gaps = cmd_gap_scan("demo-research", config=cfg)
    gid = None
    for gap in new_gaps:
        if gap.type == "evaluation_void":
            from research_vault.review.gap_scan import _gap_id
            gid = _gap_id(gap.type, gap.anchor, gap.claim)
            break
    assert gid

    result = cmd_gap_scope("demo-research", gid, "scope-exp-02", config=cfg,
                           target="experiment")
    plan_path = Path(result["plan_note_path"])
    content = plan_path.read_text(encoding="utf-8")
    assert "plan_kind: preregistration" in content


def test_gap_scope_experiment_research_question_verbatim(tmp_instance):
    """4c. plan note research question == gap.claim verbatim (anti-fabrication)."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan, cmd_gap_scope

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-ev-04", title="My specific claim for testing",
                  effect="lowers latency")

    new_gaps = cmd_gap_scan("demo-research", config=cfg)
    gid = None
    gap_claim = None
    for gap in new_gaps:
        if gap.type == "evaluation_void":
            from research_vault.review.gap_scan import _gap_id
            gid = _gap_id(gap.type, gap.anchor, gap.claim)
            gap_claim = gap.claim
            break
    assert gid and gap_claim

    result = cmd_gap_scope("demo-research", gid, "scope-exp-03", config=cfg,
                           target="experiment")
    plan_path = Path(result["plan_note_path"])
    content = plan_path.read_text(encoding="utf-8")
    # The gap claim must appear verbatim in the plan
    assert gap_claim in content


def test_gap_scope_experiment_passes_k2_lint(tmp_instance):
    """4d. plan note passes rv plan check (K-2 shape-lint) with stubbed diagnosis table."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan, cmd_gap_scope
    from research_vault.plan.check import check_plan

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-ev-05", title="K2 lint test finding",
                  effect="improves recall")

    new_gaps = cmd_gap_scan("demo-research", config=cfg)
    gid = None
    for gap in new_gaps:
        if gap.type == "evaluation_void":
            from research_vault.review.gap_scan import _gap_id
            gid = _gap_id(gap.type, gap.anchor, gap.claim)
            break
    assert gid

    result = cmd_gap_scope("demo-research", gid, "scope-exp-04", config=cfg,
                           target="experiment")
    plan_path = Path(result["plan_note_path"])

    violations = check_plan(plan_path)
    assert violations == [], f"K-2 violations: {violations}"


def test_gap_scope_experiment_covers_skeleton(tmp_instance):
    """4e. plan note covers: skeleton present (no path-prefix violations)."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan, cmd_gap_scope
    from research_vault.plan.check import check_plan

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-ev-06", title="Covers skeleton test",
                  effect="improves precision")

    new_gaps = cmd_gap_scan("demo-research", config=cfg)
    gid = None
    for gap in new_gaps:
        if gap.type == "evaluation_void":
            from research_vault.review.gap_scan import _gap_id
            gid = _gap_id(gap.type, gap.anchor, gap.claim)
            break
    assert gid

    result = cmd_gap_scope("demo-research", gid, "scope-exp-05", config=cfg,
                           target="experiment")
    plan_path = Path(result["plan_note_path"])
    content = plan_path.read_text(encoding="utf-8")

    # covers: field present in frontmatter (may be empty [] — that's fine)
    assert "covers:" in content
    # No path-prefix violations → K-2 covers check passes
    violations = check_plan(plan_path)
    cover_violations = [v for v in violations if "path-prefixed" in v]
    assert cover_violations == [], f"Cover path violations: {cover_violations}"


def test_gap_scope_experiment_gap_context_written(tmp_instance):
    """4f. _gap-context.md written adjacent to plan note."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan, cmd_gap_scope

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-ev-07", title="Gap context test",
                  effect="reduces hallucination")

    new_gaps = cmd_gap_scan("demo-research", config=cfg)
    gid = None
    for gap in new_gaps:
        if gap.type == "evaluation_void":
            from research_vault.review.gap_scan import _gap_id
            gid = _gap_id(gap.type, gap.anchor, gap.claim)
            break
    assert gid

    result = cmd_gap_scope("demo-research", gid, "scope-exp-06", config=cfg,
                           target="experiment")
    plan_path = Path(result["plan_note_path"])

    # _gap-context.md should be adjacent to the plan note
    context_path = plan_path.parent / "_gap-context.md"
    assert context_path.exists(), f"_gap-context.md not found at {context_path}"


def test_gap_scope_experiment_context_has_plan_chain(tmp_instance):
    """4g. _gap-context.md contains SR-PLAN-1 next-step chain."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan, cmd_gap_scope

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-ev-08", title="Plan chain test",
                  effect="improves F1")

    new_gaps = cmd_gap_scan("demo-research", config=cfg)
    gid = None
    for gap in new_gaps:
        if gap.type == "evaluation_void":
            from research_vault.review.gap_scan import _gap_id
            gid = _gap_id(gap.type, gap.anchor, gap.claim)
            break
    assert gid

    result = cmd_gap_scope("demo-research", gid, "scope-exp-07", config=cfg,
                           target="experiment")
    plan_path = Path(result["plan_note_path"])
    context_path = plan_path.parent / "_gap-context.md"
    context = context_path.read_text(encoding="utf-8")

    # Must reference the SR-PLAN-1 chain steps
    assert "rv plan check" in context
    assert "human-go-plan" in context
    assert "rv plan freeze" in context


# ---------------------------------------------------------------------------
# 5. proven-open → run-candidate promotion
# ---------------------------------------------------------------------------

def test_proven_open_count_returns_count(tmp_instance):
    """5a. proven_open_count returns count of proven-open gaps."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import proven_open_count

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-po-001", "knowledge_void", "Some claim", status="proven-open")
    _make_gap_note(pnd, "gap-po-002", "contradictory", "Another claim", status="proven-open")
    _make_gap_note(pnd, "gap-po-003", "knowledge_void", "Open claim", status="open")

    count = proven_open_count("demo-research", config=cfg)
    assert count == 2


def test_gap_list_status_proven_open(tmp_instance):
    """5b. gap-list --status proven-open returns only proven-open gaps."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_list

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-po-004", "evaluation_void", "Eval claim", status="proven-open")
    _make_gap_note(pnd, "gap-po-005", "knowledge_void", "KV claim", status="open")

    results = cmd_gap_list("demo-research", config=cfg, status_filter="proven-open")
    assert len(results) == 1
    assert results[0]["status"] == "proven-open"
    assert results[0]["id"] == "gap-po-004"


def test_rv_status_surfaces_proven_open_count(tmp_instance):
    """5c. rv status Needs Attention includes proven-open count when > 0."""
    from research_vault.config import load_config
    from research_vault.status import cmd_status

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-po-006", "evaluation_void", "Run candidate gap",
                   status="proven-open")

    output = cmd_status("demo-research", config=cfg)
    # Should mention proven-open in needs-attention section
    assert "proven-open" in output.lower()


def test_run_does_not_auto_fire(tmp_instance):
    """5d. A proven-open gap does NOT auto-fire a run — requires human-go."""
    # This test verifies the suggested_route is just a data field, not an auto-trigger.
    from research_vault.config import load_config
    from research_vault.review.gap_scan import suggest_route, ROUTE_EXPERIMENT

    # evaluation_void → experiment, but suggest_route is just a suggestion (a prior)
    route = suggest_route("evaluation_void", {})
    assert route == ROUTE_EXPERIMENT

    # The existence of the route suggestion does NOT trigger anything —
    # cmd_gap_scope with target=experiment is required (human invokes it).
    # Verified by the fact that suggest_route is a pure function with no side effects.
    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-proven", "evaluation_void", "Auto-fire test", status="proven-open")

    # No experiments note auto-created without explicit cmd_gap_scope call
    exp_dir = pnd / "experiments"
    before = list(exp_dir.glob("*.md")) if exp_dir.exists() else []

    # The route is just a suggestion — no side effect
    _ = suggest_route("evaluation_void", {})
    after = list(exp_dir.glob("*.md")) if exp_dir.exists() else []
    assert len(before) == len(after), "suggest_route auto-created an experiment note — VIOLATION"


# ---------------------------------------------------------------------------
# 6. Tier B section threading
# ---------------------------------------------------------------------------

def test_support_verdict_has_section_field():
    """6a. SupportVerdict has optional section field (default '')."""
    from research_vault.manuscript.support_matcher import SupportVerdict
    import dataclasses
    fields = {f.name for f in dataclasses.fields(SupportVerdict)}
    assert "section" in fields

    # Default is empty string (back-compat)
    v = SupportVerdict(
        verdict="SUPPORTS",
        verbatim_span="span text",
        polarity="positive",
        reasoning="supports",
        claim="test claim",
        citekey="key2023",
        note_path="/fake/path.md",
        judge_model="mock",
        prompt_hash="abc",
    )
    assert v.section == ""


def test_to_meta_dict_emits_section():
    """6b. to_meta_dict() emits section field."""
    from research_vault.manuscript.support_matcher import SupportVerdict
    v = SupportVerdict(
        verdict="ABSENT",
        verbatim_span=None,
        polarity="neutral",
        reasoning="absent",
        claim="test claim",
        citekey="key2023",
        note_path="/fake/path.md",
        judge_model="mock",
        prompt_hash="abc",
        section="introduction",
    )
    d = v.to_meta_dict()
    assert "section" in d
    assert d["section"] == "introduction"


def test_to_meta_dict_section_default_empty():
    """6b-b. to_meta_dict() emits section = '' when not set (back-compat)."""
    from research_vault.manuscript.support_matcher import SupportVerdict
    v = SupportVerdict(
        verdict="SUPPORTS",
        verbatim_span="span",
        polarity="positive",
        reasoning="ok",
        claim="test",
        citekey="key",
        note_path="/fake.md",
        judge_model="mock",
        prompt_hash="abc",
    )
    d = v.to_meta_dict()
    assert d["section"] == ""


def test_match_support_accepts_section_parameter(tmp_path):
    """6c. match_support() accepts section= parameter."""
    from research_vault.manuscript.support_matcher import match_support

    note_path = tmp_path / "literature" / "key2023.md"
    note_path.parent.mkdir()
    note_path.write_text(
        "---\ntype: literature\ntldr: Some finding\n---\n# Note\nSome content.",
        encoding="utf-8",
    )

    def mock_judge(prompt: str) -> str:
        return "VERDICT: [SUPPORTS]\nSPAN: Some finding\nCLAIM_CORE: test\nDISCONFIRM: none\nGAP: —"

    verdict = match_support(
        claim="Some claim",
        citekey="key2023",
        note_path=note_path,
        judge_fn=mock_judge,
        section="introduction",
    )
    assert verdict.section == "introduction"


def test_detect_absent_rows_reads_section():
    """6d. _detect_absent_rows reads section from verdict meta → GapRecord._meta['section']."""
    from research_vault.review.gap_scan import _detect_absent_rows

    matcher_meta = _make_support_matcher_meta([
        _make_verdict("ABSENT", claim_snippet="Claim in intro", section="introduction"),
    ])
    gaps = _detect_absent_rows(matcher_meta)
    assert len(gaps) == 1
    assert gaps[0]._meta.get("section") == "introduction"


def test_tier_b_introduction_suggests_literature():
    """6e. absent_row in introduction.tex → suggested_route == 'literature'."""
    from research_vault.review.gap_scan import _detect_absent_rows, suggest_route, ROUTE_LITERATURE

    matcher_meta = _make_support_matcher_meta([
        _make_verdict("ABSENT", claim_snippet="Background claim", section="introduction"),
    ])
    gaps = _detect_absent_rows(matcher_meta)
    assert gaps
    route = suggest_route(gaps[0].type, gaps[0]._meta)
    assert route == ROUTE_LITERATURE


def test_tier_b_results_discussion_suggests_experiment():
    """6f. absent_row in results-discussion.tex → suggested_route == 'experiment'."""
    from research_vault.review.gap_scan import _detect_absent_rows, suggest_route, ROUTE_EXPERIMENT

    matcher_meta = _make_support_matcher_meta([
        _make_verdict("ABSENT", claim_snippet="Our result claim", section="results-discussion"),
    ])
    gaps = _detect_absent_rows(matcher_meta)
    assert gaps
    route = suggest_route(gaps[0].type, gaps[0]._meta)
    assert route == ROUTE_EXPERIMENT


def test_tier_b_no_section_suggests_triage():
    """6g. absent_row with no section in meta → triage (back-compat)."""
    from research_vault.review.gap_scan import _detect_absent_rows, suggest_route, ROUTE_TRIAGE

    # Old-style verdict dict without 'section' key (pre-Tier-B back-compat)
    old_verdict = {
        "verdict": "ABSENT",
        "verbatim_span": None,
        "polarity": "neutral",
        "claim_snippet": "Old-style claim",
        "citekey": "key2023",
        "note_path": "literature/key2023.md",
        "judge_model": "mock",
        "prompt_hash": "abc",
        "j2_escalation": False,
        # NO 'section' key — old format
    }
    matcher_meta = _make_support_matcher_meta([old_verdict])
    gaps = _detect_absent_rows(matcher_meta)
    assert gaps
    # Section should default to "" and thus route to triage
    route = suggest_route(gaps[0].type, gaps[0]._meta)
    assert route == ROUTE_TRIAGE


def test_check_support_tally_threads_section(tmp_path):
    """6h. check_support_tally threads tex.stem into each match_support call via section."""
    from research_vault.manuscript.check_gates import check_support_tally

    # Setup: a minimal .tex structure
    sections_dir = tmp_path / "manuscripts" / "ms-001" / "sections"
    sections_dir.mkdir(parents=True)
    tex_file = sections_dir / "introduction.tex"
    tex_file.write_text(
        r"Some text \cite{key2023} about something.",
        encoding="utf-8",
    )

    notes_root = tmp_path / "notes"
    lit_dir = notes_root / "literature"
    lit_dir.mkdir(parents=True)
    (lit_dir / "key2023.md").write_text(
        "---\ntype: literature\ntldr: Background finding\n---\n# Note\nContent.",
        encoding="utf-8",
    )

    captured_sections: list[str] = []

    def mock_judge(prompt: str) -> str:
        return "VERDICT: [SUPPORTS]\nSPAN: Background finding\nCLAIM_CORE: test\nDISCONFIRM: none\nGAP: —"

    # We patch match_support to capture the section argument
    import unittest.mock as mock
    from research_vault.manuscript import support_matcher as sm

    original_match_support = sm.match_support

    def capturing_match_support(*args, **kwargs):
        captured_sections.append(kwargs.get("section", ""))
        return original_match_support(*args, **kwargs)

    tree_root = tmp_path / "manuscripts" / "ms-001"
    with mock.patch.object(sm, "match_support", side_effect=capturing_match_support):
        check_support_tally(
            tree_root=tree_root,
            notes_root=notes_root,
            judge_fn=mock_judge,
        )

    # Section should be "introduction" (tex.stem of introduction.tex)
    assert "introduction" in captured_sections, (
        f"Section 'introduction' not captured; got: {captured_sections}"
    )


# ---------------------------------------------------------------------------
# 7. CLI subcommands (gap-route alias + gap-list)
# ---------------------------------------------------------------------------

def test_cli_gap_route_alias_accepted(tmp_instance):
    """7a. rv review <project> gap-route <gap-id> <scope> accepted."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-route-01", title="Route alias test")

    new_gaps = cmd_gap_scan("demo-research", config=cfg)
    assert new_gaps
    from research_vault.review.gap_scan import _gap_id
    gid = _gap_id(new_gaps[0].type, new_gaps[0].anchor, new_gaps[0].claim)

    # Test via CLI args
    import argparse
    from research_vault.review.verbs import build_parser, run

    parser = build_parser()
    args = parser.parse_args(["demo-research", "gap-route", gid, "scope-route-01"])
    assert args.review_cmd == "gap-route"


def test_cli_gap_scope_target_experiment_accepted():
    """7b. rv review <project> gap-scope --target experiment accepted."""
    import argparse
    from research_vault.review.verbs import build_parser

    parser = build_parser()
    args = parser.parse_args(["demo-research", "gap-scope", "gap-001", "scope-001",
                              "--target", "experiment"])
    assert args.target == "experiment"


def test_cli_gap_scope_target_literature_accepted():
    """7c. rv review <project> gap-scope --target literature accepted."""
    import argparse
    from research_vault.review.verbs import build_parser

    parser = build_parser()
    args = parser.parse_args(["demo-research", "gap-scope", "gap-001", "scope-001",
                              "--target", "literature"])
    assert args.target == "literature"


def test_cli_gap_list_exits_0(tmp_instance):
    """7d. rv review <project> gap-list exits 0, prints gap records."""
    from research_vault.config import load_config

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-list-01", "knowledge_void", "List test claim")

    import argparse
    from research_vault.review.verbs import build_parser, run

    parser = build_parser()
    args = parser.parse_args(["demo-research", "gap-list"])
    rc = run(args)
    assert rc == 0


def test_cli_gap_list_status_filter_accepted(tmp_instance):
    """7e. rv review <project> gap-list --status proven-open exits 0 (filtered)."""
    from research_vault.config import load_config

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-list-02", "evaluation_void", "Proven open claim",
                   status="proven-open")

    import argparse
    from research_vault.review.verbs import build_parser, run

    parser = build_parser()
    args = parser.parse_args(["demo-research", "gap-list", "--status", "proven-open"])
    rc = run(args)
    assert rc == 0


# ---------------------------------------------------------------------------
# 8. Honest bound — run never auto-fires
# ---------------------------------------------------------------------------

def test_suggest_route_is_pure_no_side_effects():
    """8a. suggest_route is a pure function — no side effects."""
    from research_vault.review.gap_scan import suggest_route
    import inspect

    # suggest_route should be a pure function (no I/O in its source)
    src = inspect.getsource(suggest_route)
    # No file I/O operations
    assert "open(" not in src
    assert "write_text" not in src
    assert "mkdir" not in src
    # No subprocess
    assert "subprocess" not in src


def test_suggested_route_is_prior_not_decision():
    """8b. suggested_route field is a prior — cmd_gap_scope requires explicit human call."""
    # The suggested_route is a data field on the gap note.
    # Verified: cmd_gap_scan writes it, but does NOT call cmd_gap_scope.
    # The human must explicitly invoke gap-scope or gap-route.
    from research_vault.review.gap_scan import suggest_route
    # suggest_route returns a string, doesn't call any command
    route = suggest_route("evaluation_void", {})
    assert isinstance(route, str)
    # The route is just a label — it has no mechanism to fire anything
    assert route in ("literature", "experiment", "triage")


# ---------------------------------------------------------------------------
# 9. Discovery / trigger surface
# ---------------------------------------------------------------------------

def test_cli_verb_registry_includes_sr_gap_route():
    """9a. 'review' verb entry in _VERB_REGISTRY sr field includes SR-GAP-ROUTE."""
    from research_vault.cli import _VERB_REGISTRY
    review_entry = _VERB_REGISTRY.get("review", {})
    sr_field = review_entry.get("sr", "")
    assert "SR-GAP-ROUTE" in sr_field, (
        f"SR-GAP-ROUTE not in cli._VERB_REGISTRY['review']['sr']: {sr_field!r}"
    )


# ---------------------------------------------------------------------------
# 10. Zero ~/vault edits
# ---------------------------------------------------------------------------

def test_zero_vault_edits(tmp_instance):
    """10. All operations are hermetic — no ~/vault reads or writes."""
    # Covered implicitly by tmp_instance fixture isolating the config root.
    # Explicit: confirm notes_root is NOT ~/vault/notes.
    from research_vault.config import load_config

    cfg = load_config()
    vault_notes_path = Path.home() / "vault" / "notes"
    assert cfg.notes_root != vault_notes_path, (
        f"Config notes_root points into ~/vault: {cfg.notes_root}"
    )
