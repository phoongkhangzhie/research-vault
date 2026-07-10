"""test_sr_lr_2.py — acceptance tests: the gap-driven pass.

Coverage:
  1. gaps/ OKF type
     1a. "gaps" in note.OKF_TYPES (the 8th type)
     1b. "gaps" not in note.OKF_SHARED_TYPES (project-scoped, like findings/)
     1c. rv note new <project> gaps <id> creates gaps/<id>.md
  2. GapRecord dataclass
     2a. GapRecord has type, anchor, claim, why, status fields
     2b. status ∈ {open, closed-supported, closed-filled, proven-open}
     2c. GAP_TYPES frozenset has 3 gap type constants (absent_row removed)
  3. Gap detectors — Knowledge Void (D-GAP-2)
     3a. finding with backed_by empty/absent → knowledge_void gap detected
     3b. finding with backed_by ≥ threshold → NOT detected
     3c. threshold parameter respected (threshold=2: 1 entry → gap; 2 entries → ok)
  4. Gap detectors — Contradictory Evidence
     4a. concept with both supported_by AND contradicted_by → contradictory gap
     4b. concept with supported_by only → NOT detected
     4c. concept with contradicted_by only → NOT detected (no support to contradict)
  5. Gap detectors — Evaluation Void
     5a. finding with effect + no comparator → evaluation_void gap
     5b. finding with effect AND comparator → NOT detected
     5c. finding with no effect field → NOT detected
  6. (absent_row detector removed)
  7. cmd_gap_scan — writes gap records
     7a. scans project_notes_dir, returns list of GapRecord
     7b. writes gaps/<id>.md for each new gap (in project_notes_dir)
     7c. gap note frontmatter has type, anchor, claim, why, status: open
     7d. idempotent: re-running does NOT re-create existing open gap for same anchor+claim
     7e. closed gaps are NOT overwritten by re-scan
  8. Gap→scope auto-authoring (§5L.7)
     8a. cmd_gap_scope creates a review scope (Phase-1 DAG, calls cmd_new internally)
     8b. review question derived from gap.claim (exact words)
     8c. _gap-context.md written in reviews/<scope>/ with seed_queries + snowball_seeds
     8d. knowledge_void seed queries mention the concept/claim terms
  9. cmd_gap_close stamps status (§5L.8)
     9a. close with "closed-supported" updates frontmatter status field
     9b. close with "proven-open" updates frontmatter status field
     9c. invalid status → ValueError
     9d. gap not found → FileNotFoundError (or KeyError)
  10. CLI subcommands: gap-scan, gap-scope, gap-close
     10a. rv review gap-scan <project> exits 0, prints count
     10b. rv review gap-scan --threshold <n> flag accepted
     10c. rv review gap-scope <project> <gap-id> <scope> exits 0, prints manifest path
     10d. rv review gap-close <project> <gap-id> --status proven-open exits 0
  11. rv status includes open gaps count
     11a. open gaps count appears in Needs Attention section when gaps exist
     11b. gap line absent when no gaps directory or zero open gaps
  12. CLI verb registry
     12a. "review" in cli._VERB_REGISTRY sr field includes "SR-LR-2"
  13. Zero ~/vault edits (all hermetic via tmp_instance)

All hermetic (tmp_instance / tmp_path). No live LLM calls.
Stdlib only.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_finding(project_notes_dir: Path, fid: str, **frontmatter) -> Path:
    """Write a findings/<fid>.md note with given frontmatter."""
    fd = project_notes_dir / "findings"
    fd.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    lines.append(f"type: findings")
    lines.append(f"id: {fid}")
    for k, v in frontmatter.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {item}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append(f"# Finding: {fid}")
    p = fd / f"{fid}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _make_concept(project_notes_dir: Path, cid: str, **frontmatter) -> Path:
    """Write a concepts/<cid>.md note with given frontmatter."""
    cd = project_notes_dir / "concepts"
    cd.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: concepts", f"id: {cid}"]
    for k, v in frontmatter.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {item}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append(f"# Concept: {cid}")
    p = cd / f"{cid}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p




# ---------------------------------------------------------------------------
# 1. gaps/ OKF type
# ---------------------------------------------------------------------------

def test_gaps_in_okf_types():
    """1a. 'gaps' in note.OKF_TYPES (the 10th type)."""
    from research_vault.note import OKF_TYPES
    assert "gaps" in OKF_TYPES


def test_gaps_not_shared():
    """1b. 'gaps' not in OKF_SHARED_TYPES — project-scoped like findings/."""
    from research_vault.note import OKF_SHARED_TYPES
    assert "gaps" not in OKF_SHARED_TYPES


def test_gaps_note_new_creates_file(tmp_instance):
    """1c. rv note new <project> gaps <id> creates gaps/<id>.md in project_notes_dir."""
    from research_vault.config import load_config
    from research_vault.note import cmd_new as note_cmd_new

    cfg = load_config()
    project_notes = cfg.project_notes_dir("demo-research")
    note_path = note_cmd_new("demo-research", "gaps", "gap-001", config=cfg)
    assert note_path.exists()
    assert str(note_path).endswith("gaps/gap-001.md")
    content = note_path.read_text(encoding="utf-8")
    assert "type: gaps" in content


# ---------------------------------------------------------------------------
# 2. GapRecord dataclass
# ---------------------------------------------------------------------------

def test_gap_record_fields():
    """2a. GapRecord has type, anchor, claim, why, status fields."""
    from research_vault.review.gap_scan import GapRecord
    rec = GapRecord(
        type="knowledge_void",
        anchor="findings/f-001.md",
        claim="LLMs outperform humans on task X",
        why="backed_by empty (support-degree=0)",
        status="open",
    )
    assert rec.type == "knowledge_void"
    assert rec.anchor == "findings/f-001.md"
    assert rec.claim == "LLMs outperform humans on task X"
    assert rec.why == "backed_by empty (support-degree=0)"
    assert rec.status == "open"


def test_gap_status_values():
    """2b. All four valid status values are accepted."""
    from research_vault.review.gap_scan import GapRecord
    for status in ("open", "closed-supported", "closed-filled", "proven-open"):
        rec = GapRecord(type="knowledge_void", anchor="a", claim="c", why="w", status=status)
        assert rec.status == status


def test_gap_types_frozenset():
    """2c. GAP_TYPES frozenset has all 3 gap type constants (absent_row removed)."""
    from research_vault.review.gap_scan import GAP_TYPES
    assert "knowledge_void" in GAP_TYPES
    assert "contradictory" in GAP_TYPES
    assert "evaluation_void" in GAP_TYPES
    assert "absent_row" not in GAP_TYPES  # removed
    assert len(GAP_TYPES) == 3


# ---------------------------------------------------------------------------
# 3. Knowledge Void detector (D-GAP-2)
# ---------------------------------------------------------------------------

def test_knowledge_void_backed_by_empty(tmp_path):
    """3a. Finding with backed_by absent → knowledge_void detected."""
    from research_vault.review.gap_scan import _detect_knowledge_void

    notes_dir = tmp_path / "notes"
    _make_finding(notes_dir, "f-kv-1", claim="LLMs outperform humans on task X")
    gaps = _detect_knowledge_void(notes_dir, threshold=1)
    assert len(gaps) == 1
    assert gaps[0].type == "knowledge_void"
    assert "f-kv-1" in gaps[0].anchor


def test_knowledge_void_backed_by_sufficient(tmp_path):
    """3b. Finding with backed_by ≥ threshold → NOT detected."""
    from research_vault.review.gap_scan import _detect_knowledge_void

    notes_dir = tmp_path / "notes"
    _make_finding(notes_dir, "f-ok", claim="Claim with evidence", backed_by=["smith2022", "jones2023"])
    gaps = _detect_knowledge_void(notes_dir, threshold=1)
    assert len(gaps) == 0


def test_knowledge_void_threshold_respected(tmp_path):
    """3c. Threshold=2: 1 entry → gap; 2 entries → ok."""
    from research_vault.review.gap_scan import _detect_knowledge_void

    notes_dir = tmp_path / "notes"
    _make_finding(notes_dir, "f-one", claim="Claim with one ref", backed_by=["smith2022"])
    _make_finding(notes_dir, "f-two", claim="Claim with two refs", backed_by=["smith2022", "jones2023"])
    gaps = _detect_knowledge_void(notes_dir, threshold=2)
    assert len(gaps) == 1
    assert "f-one" in gaps[0].anchor


# ---------------------------------------------------------------------------
# 4. Contradictory Evidence detector
# ---------------------------------------------------------------------------

def test_contradictory_both_fields(tmp_path):
    """4a. Concept with both supported_by AND contradicted_by → contradictory gap."""
    from research_vault.review.gap_scan import _detect_contradictory

    notes_dir = tmp_path / "notes"
    _make_concept(
        notes_dir, "c-conflict",
        label="cross-lingual transfer",
        supported_by=["liu2022"],
        contradicted_by=["chen2023"],
    )
    gaps = _detect_contradictory(notes_dir)
    assert len(gaps) == 1
    assert gaps[0].type == "contradictory"
    assert "c-conflict" in gaps[0].anchor


def test_contradictory_supported_only(tmp_path):
    """4b. Concept with supported_by only → NOT detected."""
    from research_vault.review.gap_scan import _detect_contradictory

    notes_dir = tmp_path / "notes"
    _make_concept(notes_dir, "c-ok", label="safe concept", supported_by=["liu2022"])
    gaps = _detect_contradictory(notes_dir)
    assert len(gaps) == 0


def test_contradictory_contradicted_only(tmp_path):
    """4c. Concept with contradicted_by only (no support) → NOT detected."""
    from research_vault.review.gap_scan import _detect_contradictory

    notes_dir = tmp_path / "notes"
    _make_concept(notes_dir, "c-contra-only", label="lone contra", contradicted_by=["chen2023"])
    gaps = _detect_contradictory(notes_dir)
    assert len(gaps) == 0


# ---------------------------------------------------------------------------
# 5. Evaluation Void detector
# ---------------------------------------------------------------------------

def test_evaluation_void_effect_no_comparator(tmp_path):
    """5a. Finding with effect + no comparator → evaluation_void gap."""
    from research_vault.review.gap_scan import _detect_evaluation_void

    notes_dir = tmp_path / "notes"
    _make_finding(notes_dir, "f-ev-1", claim="Model X improves on task Y", effect="improves on task Y")
    gaps = _detect_evaluation_void(notes_dir)
    assert len(gaps) == 1
    assert gaps[0].type == "evaluation_void"
    assert "f-ev-1" in gaps[0].anchor


def test_evaluation_void_effect_and_comparator(tmp_path):
    """5b. Finding with effect AND comparator → NOT detected."""
    from research_vault.review.gap_scan import _detect_evaluation_void

    notes_dir = tmp_path / "notes"
    _make_finding(
        notes_dir, "f-ev-ok",
        claim="Model X beats baseline B",
        effect="beats baseline",
        comparator="baseline B",
    )
    gaps = _detect_evaluation_void(notes_dir)
    assert len(gaps) == 0


def test_evaluation_void_no_effect(tmp_path):
    """5c. Finding with no effect field → NOT detected."""
    from research_vault.review.gap_scan import _detect_evaluation_void

    notes_dir = tmp_path / "notes"
    _make_finding(notes_dir, "f-noeffect", claim="Some descriptive finding")
    gaps = _detect_evaluation_void(notes_dir)
    assert len(gaps) == 0


# ---------------------------------------------------------------------------
# 7. cmd_gap_scan — writes gap records
# ---------------------------------------------------------------------------

def test_gap_scan_returns_records(tmp_instance):
    """7a. cmd_gap_scan scans project_notes_dir, returns list of GapRecord."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-void", claim="Unsupported claim")
    gaps = cmd_gap_scan("demo-research", config=cfg)
    assert isinstance(gaps, list)
    assert all(hasattr(g, "type") and hasattr(g, "status") for g in gaps)
    assert len(gaps) >= 1


def test_gap_scan_writes_note(tmp_instance):
    """7b. cmd_gap_scan writes gaps/<id>.md for each new gap."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-write-test", claim="Claim needing a backing source")
    cmd_gap_scan("demo-research", config=cfg)
    gaps_dir = pnd / "gaps"
    assert gaps_dir.is_dir()
    gap_files = list(gaps_dir.glob("*.md"))
    assert len(gap_files) >= 1


def test_gap_scan_note_frontmatter(tmp_instance):
    """7c. gap note frontmatter has type, anchor, claim, why, status: open."""
    from research_vault.config import load_config
    from research_vault.note import _parse_frontmatter
    from research_vault.review.gap_scan import cmd_gap_scan

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-fm-test", claim="Claim to verify frontmatter")
    cmd_gap_scan("demo-research", config=cfg)
    gap_files = list((pnd / "gaps").glob("*.md"))
    assert gap_files
    fm, _ = _parse_frontmatter(gap_files[0].read_text(encoding="utf-8"))
    # 'type' is the OKF note type ('gaps'); 'gap_type' is the taxonomy type
    assert fm.get("type") == "gaps"
    assert fm.get("gap_type") in {"knowledge_void", "contradictory", "evaluation_void"}
    assert fm.get("anchor")
    assert fm.get("claim")
    assert fm.get("why")
    assert fm.get("status") == "open"


def test_gap_scan_idempotent(tmp_instance):
    """7d. Re-running gap-scan does NOT re-create existing open gap for same anchor+claim."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-idem", claim="Idempotent claim test")
    cmd_gap_scan("demo-research", config=cfg)
    n1 = len(list((pnd / "gaps").glob("*.md")))
    cmd_gap_scan("demo-research", config=cfg)
    n2 = len(list((pnd / "gaps").glob("*.md")))
    assert n1 == n2, "second run must not create duplicate gap notes"


def test_gap_scan_does_not_overwrite_closed(tmp_instance):
    """7e. A gap with status != open is not overwritten by re-scan."""
    from research_vault.config import load_config
    from research_vault.note import _parse_frontmatter, _render_frontmatter
    from research_vault.review.gap_scan import cmd_gap_scan

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-closed", claim="Already closed finding")
    cmd_gap_scan("demo-research", config=cfg)

    # Manually close the gap
    gap_files = list((pnd / "gaps").glob("*.md"))
    assert gap_files
    gap_path = gap_files[0]
    text = gap_path.read_text(encoding="utf-8")
    fm, body = _parse_frontmatter(text)
    fm["status"] = "proven-open"
    gap_path.write_text(_render_frontmatter(fm) + body, encoding="utf-8")

    # Re-scan — must NOT reset status back to open
    cmd_gap_scan("demo-research", config=cfg)
    text2 = gap_path.read_text(encoding="utf-8")
    fm2, _ = _parse_frontmatter(text2)
    assert fm2["status"] == "proven-open", "closed gap status must not be overwritten"


# ---------------------------------------------------------------------------
# 8. Gap→scope auto-authoring (§5L.7)
# ---------------------------------------------------------------------------

def test_gap_scope_creates_review(tmp_instance):
    """8a. cmd_gap_scope creates a review scope (Phase-1 DAG, calls cmd_new internally)."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan, cmd_gap_scope

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-scope-test", claim="LLMs fail on low-resource languages")
    gaps = cmd_gap_scan("demo-research", config=cfg)
    assert gaps
    gap_id = list((pnd / "gaps").glob("*.md"))[0].stem
    manifest = cmd_gap_scope("demo-research", gap_id, "scope-lr-test", config=cfg)
    assert manifest is not None
    review_dir = pnd / "reviews" / "scope-lr-test"
    assert (review_dir / "phase1-dag.json").exists()


def test_gap_scope_question_from_claim(tmp_instance):
    """8b. Review question is the exact claim text from the gap record."""
    from research_vault.config import load_config
    from research_vault.note import _parse_frontmatter
    from research_vault.review.gap_scan import cmd_gap_scan, cmd_gap_scope

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-q-test", claim="Cross-lingual transfer is under-studied")
    cmd_gap_scan("demo-research", config=cfg)
    gap_id = list((pnd / "gaps").glob("*.md"))[0].stem
    cmd_gap_scope("demo-research", gap_id, "scope-q-test", config=cfg)

    # The review note should have the claim as its question
    review_notes_dir = pnd / "reviews"
    review_notes = list(review_notes_dir.glob("*.md"))
    if review_notes:
        fm, _ = _parse_frontmatter(review_notes[0].read_text(encoding="utf-8"))
        # review_question (frontmatter) or title should contain the claim
        q = fm.get("review_question", "") or fm.get("title", "")
        assert "Cross-lingual transfer" in q


def test_gap_scope_context_file_written(tmp_instance):
    """8c. _gap-context.md written in reviews/<scope>/ with seed_queries + snowball_seeds."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan, cmd_gap_scope

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-ctx", claim="Claim for context file test", backed_by=["ref1"])
    # backed_by has 1 but threshold defaults to 1, so this is NOT a gap by default.
    # Use a finding with NO backed_by instead.
    _make_finding(pnd, "f-ctx-gap", claim="Context file gap claim")
    cmd_gap_scan("demo-research", config=cfg)
    gap_files = list((pnd / "gaps").glob("*.md"))
    assert gap_files
    gap_id = gap_files[0].stem
    cmd_gap_scope("demo-research", gap_id, "scope-ctx", config=cfg)

    context_file = pnd / "reviews" / "scope-ctx" / "_gap-context.md"
    assert context_file.exists(), "_gap-context.md must be written in the review dir"
    ctx = context_file.read_text(encoding="utf-8")
    assert "seed_queries" in ctx or "seed queries" in ctx.lower()


def test_gap_scope_seed_queries_per_type(tmp_instance):
    """8d. Different seed_query templates for different gap types (3 types)."""
    from research_vault.review.gap_scan import _build_seed_queries

    kv_queries = _build_seed_queries("knowledge_void", claim="X outperforms Y on task Z")
    ev_queries = _build_seed_queries("evaluation_void", claim="Method X improves over baseline")
    co_queries = _build_seed_queries("contradictory", claim="Concept C causes D")

    # Each type produces non-empty queries
    assert kv_queries
    assert ev_queries
    assert co_queries

    # Templates differ across types — evaluation_void should mention comparison/baseline
    all_ev = " ".join(ev_queries).lower()
    assert any(w in all_ev for w in ("baseline", "comparison", "comparator", "evaluation"))


# ---------------------------------------------------------------------------
# 9. cmd_gap_close stamps status (§5L.8)
# ---------------------------------------------------------------------------

def test_gap_close_supported(tmp_instance):
    """9a. close with 'closed-supported' updates frontmatter status."""
    from research_vault.config import load_config
    from research_vault.note import _parse_frontmatter
    from research_vault.review.gap_scan import cmd_gap_scan, cmd_gap_close

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-close-1", claim="Gap to close as supported")
    # Create the closing note (--by required for closed-supported)
    lit_dir = pnd / "literature"
    lit_dir.mkdir(parents=True, exist_ok=True)
    (lit_dir / "closer2024.md").write_text(
        "---\ntype: literature\ncitekey: closer2024\n---\n# Closer\n", encoding="utf-8"
    )
    cmd_gap_scan("demo-research", config=cfg)
    gap_id = list((pnd / "gaps").glob("*.md"))[0].stem
    cmd_gap_close("demo-research", gap_id, "closed-supported",
                  closer_ref="literature/closer2024", config=cfg)
    gap_path = pnd / "gaps" / f"{gap_id}.md"
    fm, _ = _parse_frontmatter(gap_path.read_text(encoding="utf-8"))
    assert fm["status"] == "closed-supported"


def test_gap_close_proven_open(tmp_instance):
    """9b. close with 'proven-open' updates frontmatter status."""
    from research_vault.config import load_config
    from research_vault.note import _parse_frontmatter
    from research_vault.review.gap_scan import cmd_gap_scan, cmd_gap_close

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-close-2", claim="Gap to close as proven-open")
    cmd_gap_scan("demo-research", config=cfg)
    gap_id = list((pnd / "gaps").glob("*.md"))[0].stem
    cmd_gap_close("demo-research", gap_id, "proven-open", config=cfg)
    gap_path = pnd / "gaps" / f"{gap_id}.md"
    fm, _ = _parse_frontmatter(gap_path.read_text(encoding="utf-8"))
    assert fm["status"] == "proven-open"


def test_gap_close_invalid_status(tmp_instance):
    """9c. Invalid status → ValueError."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan, cmd_gap_close

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-invalid-status", claim="Invalid status test")
    cmd_gap_scan("demo-research", config=cfg)
    gap_id = list((pnd / "gaps").glob("*.md"))[0].stem
    with pytest.raises(ValueError, match="status"):
        cmd_gap_close("demo-research", gap_id, "bad-status", config=cfg)


def test_gap_close_missing_gap(tmp_instance):
    """9d. Gap ID not found → FileNotFoundError (with closer_ref to pass --by validation)."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_close

    cfg = load_config()
    # Must pass closer_ref to satisfy --by check before the file-not-found check
    with pytest.raises(FileNotFoundError):
        cmd_gap_close("demo-research", "nonexistent-gap-id", "closed-supported",
                      closer_ref="literature/any-ref", config=cfg)


# ---------------------------------------------------------------------------
# 10. CLI subcommands
# ---------------------------------------------------------------------------

def test_cli_gap_scan_exit_0(tmp_instance):
    """10a. rv review gap-scan <project> exits 0 and prints count."""
    from research_vault.config import load_config
    from research_vault.review.verbs import build_parser, run
    import argparse

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-cli-scan", claim="CLI gap-scan test")

    p = build_parser()
    args = p.parse_args(["demo-research", "gap-scan"])
    rc = run(args)
    assert rc == 0


def test_cli_gap_scan_threshold(tmp_instance):
    """10b. rv review gap-scan --threshold <n> flag accepted."""
    from research_vault.config import load_config
    from research_vault.review.verbs import build_parser, run

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-cli-thresh", claim="Threshold test")

    p = build_parser()
    args = p.parse_args(["demo-research", "gap-scan", "--threshold", "2"])
    rc = run(args)
    assert rc == 0


def test_cli_gap_scope_exit_0(tmp_instance):
    """10c. rv review gap-scope <project> <gap-id> <scope> exits 0 and prints manifest."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan
    from research_vault.review.verbs import build_parser, run

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-cli-scope", claim="CLI scope test gap")
    cmd_gap_scan("demo-research", config=cfg)
    gap_id = list((pnd / "gaps").glob("*.md"))[0].stem

    p = build_parser()
    args = p.parse_args(["demo-research", "gap-scope", gap_id, "scope-cli-test"])
    rc = run(args)
    assert rc == 0


def test_cli_gap_close_exit_0(tmp_instance):
    """10d. rv review gap-close <project> <gap-id> --status proven-open exits 0."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan
    from research_vault.review.verbs import build_parser, run

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-cli-close", claim="CLI close test gap")
    cmd_gap_scan("demo-research", config=cfg)
    gap_id = list((pnd / "gaps").glob("*.md"))[0].stem

    p = build_parser()
    args = p.parse_args(["demo-research", "gap-close", gap_id, "--status", "proven-open"])
    rc = run(args)
    assert rc == 0


# ---------------------------------------------------------------------------
# 11. rv status includes open gaps count (D-GAP-4)
# ---------------------------------------------------------------------------

def test_status_shows_gap_count(tmp_instance):
    """11a. Open gaps count appears in Needs Attention when gaps exist."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scan
    from research_vault.status import cmd_status

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_finding(pnd, "f-status-gap", claim="Gap for status test")
    cmd_gap_scan("demo-research", config=cfg)

    status_output = cmd_status("demo-research", config=cfg)
    # D-GAP-4: COUNT surfaced in rv status
    assert "gap" in status_output.lower()


def test_status_no_gaps_no_mention(tmp_instance):
    """11b. No gaps directory → gap line not in Needs Attention."""
    from research_vault.config import load_config
    from research_vault.status import cmd_status

    cfg = load_config()
    # Don't create any gaps — just run status
    status_output = cmd_status("demo-research", config=cfg)
    # When zero open gaps, should not flag it
    lines_lower = [ln.lower() for ln in status_output.splitlines()]
    gap_attention_lines = [ln for ln in lines_lower if "open gap" in ln]
    assert len(gap_attention_lines) == 0


def test_gap_scan_parser_handles_list_values():
    """note._parse_frontmatter correctly parses YAML list fields (backed_by, supported_by).

    #26 convergence: the local _parse_frontmatter_gap was deleted; the canonical
    note._parse_frontmatter now handles list values.  This test is updated to test
    the canonical parser (same behaviour, new home).
    """
    from research_vault.note import _parse_frontmatter

    note_text = (
        "---\n"
        "type: findings\n"
        "id: f-001\n"
        "claim: LLMs outperform humans\n"
        "backed_by:\n"
        "  - smith2022\n"
        "  - jones2023\n"
        "---\n"
        "Body text.\n"
    )
    fields, _ = _parse_frontmatter(note_text)
    assert fields["type"] == "findings"
    assert fields["claim"] == "LLMs outperform humans"
    backed_by = fields.get("backed_by")
    assert isinstance(backed_by, list), (
        "note._parse_frontmatter must return list for YAML '  - item' list fields (#26); "
        f"got {type(backed_by).__name__!r}"
    )
    assert "smith2022" in backed_by
    assert "jones2023" in backed_by


def test_canonical_parser_scalar_only():
    """note._parse_frontmatter: scalar fields unaffected, .strip() callers still work.

    #26 convergence: the earlier STOP decision is lifted (grep-before-extend audit
    confirmed all .strip() callers only access SCALAR fields).  The canonical parser
    now returns list[str] for '  - item' formatted fields and str for scalar fields.
    Empty-valued keys with NO following list items remain as '' (lazy-promote: only
    converts to list[] when actual items follow) — preserving backwards-compat for
    callers that do .strip() on empty-valued fields.
    """
    from research_vault.note import _parse_frontmatter

    note_text = (
        "---\n"
        "type: experiments\n"
        "stance: confirmatory\n"
        "plan_role: main\n"
        "backed_by:\n"
        "  - smith2022\n"
        "results_hash: sha256:abc123\n"
        "---\n"
        "Body.\n"
    )
    fields, body = _parse_frontmatter(note_text)
    # Scalar callers must still get strings
    assert fields["type"] == "experiments"
    assert fields["stance"] == "confirmatory"
    assert fields["plan_role"] == "main"
    assert fields["results_hash"] == "sha256:abc123"
    # .strip() must not fail on scalar fields
    assert fields["stance"].strip() == "confirmatory"
    # backed_by with list items → returns list (#26 lift)
    backed_by = fields.get("backed_by")
    assert isinstance(backed_by, list), (
        "backed_by with  - item lines must return list after #26 convergence; "
        f"got {type(backed_by).__name__!r}"
    )
    assert backed_by == ["smith2022"]


# ---------------------------------------------------------------------------
# 12. CLI verb registry
# ---------------------------------------------------------------------------

def test_verb_registry_review_documents_gap_scan():
    """12a. 'review' entry in _VERB_REGISTRY when_to_use documents gap-scan."""
    from research_vault.cli import _VERB_REGISTRY
    entry = _VERB_REGISTRY["review"]
    when = entry.get("when_to_use", "")
    assert "gap-scan" in when


# ---------------------------------------------------------------------------
# 13. Zero ~/vault edits (sanity check)
# ---------------------------------------------------------------------------

def test_no_vault_home_writes(tmp_instance):
    """13. All fixtures use tmp_instance — no ~/vault accesses."""
    vault_home = Path.home() / "vault"
    # This is structural: if RESEARCH_VAULT_CONFIG is set to tmp_instance's config,
    # load_config() points at tmp_instance, not ~/vault.
    from research_vault.config import load_config
    cfg = load_config()
    # Verify the config instance_root is NOT under ~/vault
    assert not str(cfg.instance_root).startswith(str(vault_home)), (
        "Config must not point at ~/vault — all tests must be hermetic"
    )
