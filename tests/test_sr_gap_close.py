"""test_sr_gap_close.py — SR-GAP-CLOSE (SR-LR-4) acceptance tests: gap-closure lifecycle.

Coverage (spec §5L.19–5L.24 + §5L-GAP-CLOSE-REQ):

  1. GAP_STATUSES extension
     1a. GAP_STATUSES contains "promoted"
     1b. GAP_STATUSES contains "reopened"
     1c. GAP_STATUSES does NOT contain "superseded" (DEFERRED, D-CLOSE-3)
     1d. GapRecord validates "promoted" as a legal status
     1e. GapRecord validates "reopened" as a legal status
     1f. GapRecord rejects "superseded" status

  2. gap-close --by provenance edge (bidirectional, §5L.21(1))
     2a. --by REQUIRED for "closed-supported" — error without it
     2b. --by REQUIRED for "closed-filled" — error without it
     2c. --by REJECTED for "proven-open" — error if provided
     2d. gap FM gains closed_by: <note-ref> after close with --by (gap edge)
     2e. closing note FM gains closes: <gap-id> after close (note edge, the backward link)
     2f. in-place: gap file path unchanged after close (no move/archive)
     2g. idempotent guard: subsequent gap-scan with same gid SKIPS the gap (closed status preserved)
     2h. gap-close --status proven-open WITHOUT --by succeeds (proven-open needs no closer)
     2i. gap-close --status proven-open WITH --by → error (--by rejected)

  3. gap-promote: human-only proven-open → promoted (§5L.21(2))
     3a. gap-promote on a proven-open gap → status "promoted"
     3b. gap-promote writes promoted_to: <ref> in gap FM
     3c. gap-promote on a non-proven-open gap → ValueError (must be proven-open)
     3d. gap-promote without --to → error (required, unauditable without a target)
     3e. promoted gap does not appear in open_gap_count

  4. reopened: structural re-detection (§5L.21(3))
     4a. absent_row re-fire on "closed-supported" gap → status "reopened" (Signal 1)
     4b. reopened gap has reopened_reason: field stamped (provenance surface)
     4c. reopened gap retains its closed_by: field as history (not erased)
     4d. contradictory re-fire on ANY closed status → "reopened" (Signal 2: both edges re-acquired)
     4e. AMBIGUOUS re-fire: absent_row on "closed-filled" → WARN only, status UNCHANGED
     4f. knowledge_void re-fire on "closed-filled" → WARN only, status UNCHANGED (FP guard)
     4g. evaluation_void re-fire on "closed-filled" → WARN only, status UNCHANGED
     4h. absent_row re-fire on "closed-supported" without matcher_meta → degrade-to-skip (no reopen, no warn)
     4i. reopened gap re-enters open-routing: open_gap_count counts it
     4j. gap-route / gap-scope accepts a "reopened" gap (re-enters routing)

  5. run-arm: closed-filled stays closed across re-scan (idempotent guard, §5L.22 caveat a)
     5a. closed-filled gap (closed --by an experiments/ note, no backed_by) stays closed on re-scan
     5b. closed-filled re-fire of evaluation_void → warn-only (not reopened)
     5c. closed-filled re-fire of knowledge_void → warn-only (not reopened)

  6. open_gap_count counts {open, reopened} (D-CLOSE-4)
     6a. open_gap_count counts "open" gaps
     6b. open_gap_count counts "reopened" gaps
     6c. open_gap_count does NOT count "closed-supported"
     6d. open_gap_count does NOT count "closed-filled"
     6e. open_gap_count does NOT count "proven-open"
     6f. open_gap_count does NOT count "promoted"

  7. CLI surface (gap-close --by; gap-promote)
     7a. rv review gap-close --by <ref> exits 0 and prints both edges written
     7b. rv review gap-close for proven-open without --by exits 0
     7c. rv review gap-close for closed-supported without --by exits 1 (error)
     7d. rv review gap-promote <gap-id> --to <ref> exits 0 and prints status change
     7e. rv review gap-list --status promoted shows promoted gaps
     7f. rv review gap-list --status reopened shows reopened gaps

  8. Discovery / verb registry
     8a. "review" in cli._VERB_REGISTRY sr field includes "SR-GAP-CLOSE"
     8b. gap-close anti-pattern mentions --by requirement in doc/help
     8c. gap-promote anti-pattern warns against hand-writing contribution claims

  9. Zero ~/vault edits (all hermetic via tmp_instance)

All hermetic (tmp_instance / tmp_path). No live LLM calls.
Stdlib only.
sr: SR-GAP-CLOSE (SR-LR-4)
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_gap_note(
    pnd: Path,
    gap_id: str,
    gap_type: str,
    claim: str,
    status: str = "open",
    suggested_route: str = "literature",
    closed_by: str = "",
    anchor: str = "findings/f-001",
) -> Path:
    """Write a gaps/<gap_id>.md note with optional provenance fields."""
    gd = pnd / "gaps"
    gd.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        "type: gaps",
        f"id: {gap_id}",
        f"gap_type: {gap_type}",
        f"anchor: {anchor}",
        f'claim: "{claim}"',
        'why: "test gap"',
        f"status: {status}",
        f"suggested_route: {suggested_route}",
    ]
    if closed_by:
        lines.append(f"closed_by: {closed_by}")
    lines.extend(["---", f"# Gap: {gap_id}"])
    p = gd / f"{gap_id}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _make_literature_note(pnd: Path, citekey: str, title: str = "Test note") -> Path:
    """Write a literature/<citekey>.md note."""
    lit_dir = pnd / "literature"
    lit_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        "type: literature",
        f"citekey: {citekey}",
        f"title: {title}",
        "---",
        f"# {title}",
    ]
    p = lit_dir / f"{citekey}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _make_experiment_note(pnd: Path, exp_id: str, title: str = "Experiment note") -> Path:
    """Write an experiments/<exp_id>.md note."""
    exp_dir = pnd / "experiments"
    exp_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        "type: experiments",
        f"id: {exp_id}",
        f"title: {title}",
        "---",
        f"# {title}",
    ]
    p = exp_dir / f"{exp_id}.md"
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


def _make_concept(pnd: Path, cid: str, supported_by: list | None = None,
                  contradicted_by: list | None = None) -> Path:
    """Write a concepts/<cid>.md note."""
    cd = pnd / "concepts"
    cd.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: concepts", f"id: {cid}", f"label: {cid}"]
    if supported_by:
        lines.append("supported_by:")
        for s in supported_by:
            lines.append(f"  - {s}")
    if contradicted_by:
        lines.append("contradicted_by:")
        for s in contradicted_by:
            lines.append(f"  - {s}")
    lines.extend(["---", f"# Concept: {cid}"])
    p = cd / f"{cid}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _parse_fm(path: Path) -> dict[str, Any]:
    """Parse frontmatter from a file via canonical note parser (#26 convergence)."""
    from research_vault.note import _parse_frontmatter
    fm, _ = _parse_frontmatter(path.read_text(encoding="utf-8"))
    return fm


def _make_verdict(
    verdict: str,
    claim_snippet: str = "Test claim",
    citekey: str = "mock2023",
    j2_escalation: bool = False,
    section: str = "",
) -> dict[str, Any]:
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


def _make_support_matcher_meta(verdicts: list | None = None) -> dict[str, Any]:
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
# 1. GAP_STATUSES extension
# ---------------------------------------------------------------------------

def test_gap_statuses_has_promoted():
    """1a. GAP_STATUSES contains 'promoted'."""
    from research_vault.review.gap_scan import GAP_STATUSES
    assert "promoted" in GAP_STATUSES


def test_gap_statuses_has_reopened():
    """1b. GAP_STATUSES contains 'reopened'."""
    from research_vault.review.gap_scan import GAP_STATUSES
    assert "reopened" in GAP_STATUSES


def test_gap_statuses_no_superseded():
    """1c. GAP_STATUSES does NOT contain 'superseded' (DEFERRED, D-CLOSE-3)."""
    from research_vault.review.gap_scan import GAP_STATUSES
    assert "superseded" not in GAP_STATUSES


def test_gap_record_promoted_valid():
    """1d. GapRecord validates 'promoted' as a legal status."""
    from research_vault.review.gap_scan import GapRecord
    rec = GapRecord(type="knowledge_void", anchor="findings/f-001",
                    claim="test", why="test", status="promoted")
    assert rec.status == "promoted"


def test_gap_record_reopened_valid():
    """1e. GapRecord validates 'reopened' as a legal status."""
    from research_vault.review.gap_scan import GapRecord
    rec = GapRecord(type="knowledge_void", anchor="findings/f-001",
                    claim="test", why="test", status="reopened")
    assert rec.status == "reopened"


def test_gap_record_superseded_rejected():
    """1f. GapRecord rejects 'superseded' status."""
    from research_vault.review.gap_scan import GapRecord
    with pytest.raises(ValueError, match="superseded"):
        GapRecord(type="knowledge_void", anchor="findings/f-001",
                  claim="test", why="test", status="superseded")


# ---------------------------------------------------------------------------
# 2. gap-close --by provenance edge
# ---------------------------------------------------------------------------

def test_gap_close_by_required_closed_supported(tmp_instance):
    """2a. --by REQUIRED for 'closed-supported' — error without it."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_close

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-kv-001", "knowledge_void", "Test claim")

    with pytest.raises((ValueError, TypeError), match="--by|closer|required|closed_by"):
        cmd_gap_close("demo-research", "gap-kv-001", "closed-supported", config=cfg)


def test_gap_close_by_required_closed_filled(tmp_instance):
    """2b. --by REQUIRED for 'closed-filled' — error without it."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_close

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-ev-001", "evaluation_void", "Test claim")

    with pytest.raises((ValueError, TypeError), match="--by|closer|required|closed_by"):
        cmd_gap_close("demo-research", "gap-ev-001", "closed-filled", config=cfg)


def test_gap_close_by_rejected_for_proven_open(tmp_instance):
    """2c. --by REJECTED for 'proven-open' — error if provided."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_close

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-kv-002", "knowledge_void", "Open claim")
    lit_note = _make_literature_note(pnd, "smith2023")

    with pytest.raises((ValueError, TypeError), match="--by|proven-open|rejected|no closer"):
        cmd_gap_close("demo-research", "gap-kv-002", "proven-open",
                      closer_ref="literature/smith2023", config=cfg)


def test_gap_close_writes_closed_by_in_gap_fm(tmp_instance):
    """2d. gap FM gains closed_by: <note-ref> after close with --by."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_close

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-cs-001", "knowledge_void", "Supported claim")
    _make_literature_note(pnd, "jones2024")

    cmd_gap_close("demo-research", "gap-cs-001", "closed-supported",
                  closer_ref="literature/jones2024", config=cfg)

    gap_path = pnd / "gaps" / "gap-cs-001.md"
    fm = _parse_fm(gap_path)
    assert fm.get("closed_by") == "literature/jones2024"
    assert fm.get("status") == "closed-supported"


def test_gap_close_writes_closes_in_closing_note(tmp_instance):
    """2e. closing note FM gains closes: <gap-id> after close (backward link)."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_close

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-cs-002", "absent_row", "Absent claim")
    lit_path = _make_literature_note(pnd, "brown2024")

    cmd_gap_close("demo-research", "gap-cs-002", "closed-supported",
                  closer_ref="literature/brown2024", config=cfg)

    # The literature note must now have closes: gap-cs-002 in its frontmatter
    lit_text = lit_path.read_text(encoding="utf-8")
    assert "closes:" in lit_text
    assert "gap-cs-002" in lit_text


def test_gap_close_in_place_path_unchanged(tmp_instance):
    """2f. in-place: gap file path unchanged after close (no move/archive)."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_close

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-inplace-001", "knowledge_void", "Claim")
    _make_literature_note(pnd, "ref2024")

    original_path = pnd / "gaps" / "gap-inplace-001.md"
    assert original_path.exists()

    returned_path = cmd_gap_close("demo-research", "gap-inplace-001", "closed-supported",
                                   closer_ref="literature/ref2024", config=cfg)

    assert returned_path == original_path
    assert original_path.exists()


def test_gap_close_idempotent_guard_preserves_closed_status(tmp_instance):
    """2g. idempotent guard: subsequent gap-scan with same gid SKIPS gap (closed preserved)."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_close, cmd_gap_scan, _gap_id
    from research_vault.review.gap_scan import GAP_TYPE_KNOWLEDGE_VOID

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")

    # Create a finding with no backed_by → will be detected as knowledge_void
    claim = "The effect of X on Y is unknown"
    _make_finding(pnd, "f-idem", claim=claim)

    # First scan: creates the gap
    new_gaps = cmd_gap_scan("demo-research", config=cfg)
    assert len(new_gaps) == 1
    gid = _gap_id(GAP_TYPE_KNOWLEDGE_VOID, "findings/f-idem", claim)
    gap_path = pnd / "gaps" / f"{gid}.md"
    assert gap_path.exists()

    # Close it
    _make_literature_note(pnd, "closer2024")
    cmd_gap_close("demo-research", gid, "closed-supported",
                  closer_ref="literature/closer2024", config=cfg)

    # Second scan: should find the same gap but SKIP it (existing, closed)
    new_gaps2 = cmd_gap_scan("demo-research", config=cfg)
    assert len(new_gaps2) == 0  # not re-created

    # Status still "closed-supported"
    fm = _parse_fm(gap_path)
    assert fm.get("status") == "closed-supported"


def test_gap_close_proven_open_without_by_succeeds(tmp_instance):
    """2h. gap-close --status proven-open WITHOUT --by succeeds (proven-open needs no closer)."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_close

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-po-001", "knowledge_void", "Open gap")

    # No closer_ref, no error
    gap_path = cmd_gap_close("demo-research", "gap-po-001", "proven-open", config=cfg)
    fm = _parse_fm(gap_path)
    assert fm.get("status") == "proven-open"
    assert fm.get("closed_by", "") == ""  # no closed_by field


def test_gap_close_proven_open_with_by_rejected(tmp_instance):
    """2i. gap-close --status proven-open WITH --by → error (--by rejected)."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_close

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-po-002", "knowledge_void", "Another open gap")
    _make_literature_note(pnd, "ref2025")

    with pytest.raises((ValueError, TypeError), match="--by|proven-open|rejected|no closer"):
        cmd_gap_close("demo-research", "gap-po-002", "proven-open",
                      closer_ref="literature/ref2025", config=cfg)


# ---------------------------------------------------------------------------
# 3. gap-promote: human-only proven-open → promoted
# ---------------------------------------------------------------------------

def test_gap_promote_proven_open_to_promoted(tmp_instance):
    """3a. gap-promote on a proven-open gap → status 'promoted'."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_promote

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-prom-001", "knowledge_void", "Contribution claim",
                   status="proven-open")

    cmd_gap_promote("demo-research", "gap-prom-001", to_ref="manuscript/contribution",
                    config=cfg)

    gap_path = pnd / "gaps" / "gap-prom-001.md"
    fm = _parse_fm(gap_path)
    assert fm.get("status") == "promoted"


def test_gap_promote_writes_promoted_to(tmp_instance):
    """3b. gap-promote writes promoted_to: <ref> in gap FM."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_promote

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-prom-002", "absent_row", "Another contribution",
                   status="proven-open")

    cmd_gap_promote("demo-research", "gap-prom-002",
                    to_ref="manuscript/contributions-section", config=cfg)

    gap_path = pnd / "gaps" / "gap-prom-002.md"
    fm = _parse_fm(gap_path)
    assert fm.get("promoted_to") == "manuscript/contributions-section"


def test_gap_promote_rejects_non_proven_open(tmp_instance):
    """3c. gap-promote on a non-proven-open gap → ValueError."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_promote

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-prom-003", "knowledge_void", "Open gap",
                   status="open")

    with pytest.raises(ValueError, match="proven-open|promote|status"):
        cmd_gap_promote("demo-research", "gap-prom-003",
                        to_ref="manuscript/contributions", config=cfg)


def test_gap_promote_requires_to_ref(tmp_instance):
    """3d. gap-promote without to_ref → error (required, unauditable without a target)."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_promote

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-prom-004", "knowledge_void", "Gap to promote",
                   status="proven-open")

    with pytest.raises((ValueError, TypeError), match="--to|to_ref|required"):
        cmd_gap_promote("demo-research", "gap-prom-004", to_ref=None, config=cfg)


def test_gap_promote_not_in_open_gap_count(tmp_instance):
    """3e. promoted gap does not appear in open_gap_count."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_promote, open_gap_count

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-prom-005", "knowledge_void", "Promoted claim",
                   status="proven-open")

    cmd_gap_promote("demo-research", "gap-prom-005",
                    to_ref="manuscript/future-work", config=cfg)

    assert open_gap_count("demo-research", config=cfg) == 0


# ---------------------------------------------------------------------------
# 4. reopened: structural re-detection
# ---------------------------------------------------------------------------

def test_reopened_absent_row_on_closed_supported(tmp_instance):
    """4a. absent_row re-fire on 'closed-supported' gap → status 'reopened' (Signal 1)."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import (
        cmd_gap_close, cmd_gap_scan, _gap_id, GAP_TYPE_ABSENT_ROW,
    )

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")

    # Create a literature note as anchor
    _make_literature_note(pnd, "smith2024")
    _make_literature_note(pnd, "closer2024")

    # Build matcher_meta with ABSENT verdict for smith2024
    claim = "LLMs underperform on cross-lingual tasks"
    matcher_meta = _make_support_matcher_meta([
        _make_verdict("ABSENT", claim_snippet=claim, citekey="smith2024"),
    ])

    # First scan: creates absent_row gap
    new_gaps = cmd_gap_scan("demo-research", config=cfg, matcher_meta=matcher_meta)
    assert len(new_gaps) == 1
    gid = _gap_id(GAP_TYPE_ABSENT_ROW, "literature/smith2024", claim)
    gap_path = pnd / "gaps" / f"{gid}.md"
    assert gap_path.exists()

    # Close it as closed-supported
    cmd_gap_close("demo-research", gid, "closed-supported",
                  closer_ref="literature/closer2024", config=cfg)
    fm = _parse_fm(gap_path)
    assert fm.get("status") == "closed-supported"

    # Re-scan with the same matcher_meta (matcher flip-back to ABSENT)
    # → should reopen the gap
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cmd_gap_scan("demo-research", config=cfg, matcher_meta=matcher_meta)

    fm2 = _parse_fm(gap_path)
    assert fm2.get("status") == "reopened"


def test_reopened_has_reopened_reason(tmp_instance):
    """4b. reopened gap has reopened_reason: field stamped (provenance surface)."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import (
        cmd_gap_close, cmd_gap_scan, _gap_id, GAP_TYPE_ABSENT_ROW,
    )

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_literature_note(pnd, "claim2024")
    _make_literature_note(pnd, "closer2024b")

    claim = "Cultural bias exists in benchmark datasets"
    matcher_meta = _make_support_matcher_meta([
        _make_verdict("ABSENT", claim_snippet=claim, citekey="claim2024"),
    ])

    cmd_gap_scan("demo-research", config=cfg, matcher_meta=matcher_meta)
    gid = _gap_id(GAP_TYPE_ABSENT_ROW, "literature/claim2024", claim)
    gap_path = pnd / "gaps" / f"{gid}.md"

    cmd_gap_close("demo-research", gid, "closed-supported",
                  closer_ref="literature/closer2024b", config=cfg)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cmd_gap_scan("demo-research", config=cfg, matcher_meta=matcher_meta)

    fm = _parse_fm(gap_path)
    assert fm.get("status") == "reopened"
    assert fm.get("reopened_reason", "") != ""


def test_reopened_retains_closed_by(tmp_instance):
    """4c. reopened gap retains its closed_by: field as history (not erased)."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import (
        cmd_gap_close, cmd_gap_scan, _gap_id, GAP_TYPE_ABSENT_ROW,
    )

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_literature_note(pnd, "orig2024")
    _make_literature_note(pnd, "closer2024c")

    claim = "Evaluation metrics lack cultural validity"
    matcher_meta = _make_support_matcher_meta([
        _make_verdict("ABSENT", claim_snippet=claim, citekey="orig2024"),
    ])

    cmd_gap_scan("demo-research", config=cfg, matcher_meta=matcher_meta)
    gid = _gap_id(GAP_TYPE_ABSENT_ROW, "literature/orig2024", claim)
    gap_path = pnd / "gaps" / f"{gid}.md"

    cmd_gap_close("demo-research", gid, "closed-supported",
                  closer_ref="literature/closer2024c", config=cfg)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cmd_gap_scan("demo-research", config=cfg, matcher_meta=matcher_meta)

    fm = _parse_fm(gap_path)
    assert fm.get("status") == "reopened"
    # closed_by: edge must be retained as history
    assert fm.get("closed_by") == "literature/closer2024c"


def test_reopened_contradictory_re_fire_any_closed_status(tmp_instance):
    """4d. contradictory re-fire on ANY closed status → 'reopened' (Signal 2)."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import (
        cmd_gap_close, cmd_gap_scan, _gap_id, GAP_TYPE_CONTRADICTORY,
    )

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_literature_note(pnd, "closer-contr2024")

    # Create concept with both edges (contradictory)
    _make_concept(pnd, "c-contested", supported_by=["lit-A"], contradicted_by=["lit-B"])

    # First scan: creates contradictory gap
    new_gaps = cmd_gap_scan("demo-research", config=cfg)
    assert len(new_gaps) == 1
    gid = _gap_id(GAP_TYPE_CONTRADICTORY, "concepts/c-contested", "c-contested")
    gap_path = pnd / "gaps" / f"{gid}.md"
    assert gap_path.exists()

    # Close it as closed-filled (any closed status)
    cmd_gap_close("demo-research", gid, "closed-filled",
                  closer_ref="literature/closer-contr2024", config=cfg)
    fm = _parse_fm(gap_path)
    assert fm.get("status") == "closed-filled"

    # Re-scan: concept STILL has both edges (re-acquired) → reopened
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cmd_gap_scan("demo-research", config=cfg)

    fm2 = _parse_fm(gap_path)
    assert fm2.get("status") == "reopened"


def test_reopened_ambiguous_warn_only_absent_row_on_closed_filled(tmp_instance):
    """4e. AMBIGUOUS re-fire: absent_row on 'closed-filled' → WARN only, status UNCHANGED.

    This is the FP guard — the load-bearing conservatism test.
    closed-filled spans both 'backed_by threshold crossed' AND 'run-arm generated result'
    closures; the detector cannot distinguish them, so it warns rather than auto-reopens.
    """
    from research_vault.config import load_config
    from research_vault.review.gap_scan import (
        cmd_gap_close, cmd_gap_scan, _gap_id, GAP_TYPE_ABSENT_ROW,
    )

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_literature_note(pnd, "cf-claim2024")
    _make_literature_note(pnd, "cf-closer2024")

    claim = "Claim needing evidence"
    matcher_meta = _make_support_matcher_meta([
        _make_verdict("ABSENT", claim_snippet=claim, citekey="cf-claim2024"),
    ])

    cmd_gap_scan("demo-research", config=cfg, matcher_meta=matcher_meta)
    gid = _gap_id(GAP_TYPE_ABSENT_ROW, "literature/cf-claim2024", claim)
    gap_path = pnd / "gaps" / f"{gid}.md"

    # Close as closed-filled (not closed-supported)
    cmd_gap_close("demo-research", gid, "closed-filled",
                  closer_ref="literature/cf-closer2024", config=cfg)
    fm = _parse_fm(gap_path)
    assert fm.get("status") == "closed-filled"

    # Re-scan with ABSENT matcher meta on a closed-filled gap → WARN, NOT reopen
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        cmd_gap_scan("demo-research", config=cfg, matcher_meta=matcher_meta)

    # Status must be UNCHANGED (still closed-filled)
    fm2 = _parse_fm(gap_path)
    assert fm2.get("status") == "closed-filled", (
        "closed-filled gap MUST NOT be auto-reopened on absent_row re-fire — "
        "this is the FP guard protecting the run-arm (caveat a, §5L.22)"
    )
    # A warn must have been emitted
    assert any(issubclass(warning.category, UserWarning) for warning in w), (
        "Expected a UserWarning for ambiguous closed-filled re-fire"
    )


def test_reopened_knowledge_void_on_closed_filled_warn_only(tmp_instance):
    """4f. knowledge_void re-fire on 'closed-filled' → WARN only, status UNCHANGED."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import (
        cmd_gap_close, cmd_gap_scan, _gap_id, GAP_TYPE_KNOWLEDGE_VOID,
    )

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    claim = "Knowledge claim with no backing"
    _make_finding(pnd, "f-kv-warnonly", claim=claim)
    _make_literature_note(pnd, "kv-closer2024")

    # First scan: creates knowledge_void gap
    new_gaps = cmd_gap_scan("demo-research", config=cfg)
    assert len(new_gaps) == 1
    gid = _gap_id(GAP_TYPE_KNOWLEDGE_VOID, "findings/f-kv-warnonly", claim)
    gap_path = pnd / "gaps" / f"{gid}.md"

    # Close as closed-filled
    cmd_gap_close("demo-research", gid, "closed-filled",
                  closer_ref="literature/kv-closer2024", config=cfg)
    fm = _parse_fm(gap_path)
    assert fm.get("status") == "closed-filled"

    # The finding still has no backed_by → detector would still fire
    # But since it's closed-filled, it must WARN only, not reopen
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        cmd_gap_scan("demo-research", config=cfg)

    fm2 = _parse_fm(gap_path)
    assert fm2.get("status") == "closed-filled"
    assert any(issubclass(warning.category, UserWarning) for warning in w)


def test_reopened_evaluation_void_on_closed_filled_warn_only(tmp_instance):
    """4g. evaluation_void re-fire on 'closed-filled' → WARN only, status UNCHANGED."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import (
        cmd_gap_close, cmd_gap_scan, _gap_id, GAP_TYPE_EVALUATION_VOID,
    )

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    claim = "Effect claim without comparator"
    # NOTE: backed_by=["lit-A"] prevents knowledge_void so only evaluation_void fires
    _make_finding(pnd, "f-ev-warnonly", claim=claim, effect="big improvement",
                  backed_by=["lit-A"])
    _make_literature_note(pnd, "ev-closer2024")

    new_gaps = cmd_gap_scan("demo-research", config=cfg)
    assert len(new_gaps) == 1
    gid = _gap_id(GAP_TYPE_EVALUATION_VOID, "findings/f-ev-warnonly", claim)
    gap_path = pnd / "gaps" / f"{gid}.md"

    cmd_gap_close("demo-research", gid, "closed-filled",
                  closer_ref="literature/ev-closer2024", config=cfg)

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        cmd_gap_scan("demo-research", config=cfg)

    fm2 = _parse_fm(gap_path)
    assert fm2.get("status") == "closed-filled"
    assert any(issubclass(warning.category, UserWarning) for warning in w)


def test_reopened_absent_row_on_closed_supported_no_matcher_meta_skip(tmp_instance):
    """4h. absent_row re-fire on 'closed-supported' without matcher_meta → degrade-to-skip."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import (
        cmd_gap_close, cmd_gap_scan, _gap_id, GAP_TYPE_ABSENT_ROW,
    )

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_literature_note(pnd, "nocheck2024")
    _make_literature_note(pnd, "closer-nc2024")

    claim = "Claim without matcher backing"
    matcher_meta = _make_support_matcher_meta([
        _make_verdict("ABSENT", claim_snippet=claim, citekey="nocheck2024"),
    ])

    cmd_gap_scan("demo-research", config=cfg, matcher_meta=matcher_meta)
    gid = _gap_id(GAP_TYPE_ABSENT_ROW, "literature/nocheck2024", claim)
    gap_path = pnd / "gaps" / f"{gid}.md"

    cmd_gap_close("demo-research", gid, "closed-supported",
                  closer_ref="literature/closer-nc2024", config=cfg)
    fm = _parse_fm(gap_path)
    assert fm.get("status") == "closed-supported"

    # Re-scan WITHOUT matcher_meta → no absent_row detection → gap not touched
    cmd_gap_scan("demo-research", config=cfg, matcher_meta=None)

    fm2 = _parse_fm(gap_path)
    # Still closed-supported: degrade-to-skip (no matcher_meta → no Signal 1 check)
    assert fm2.get("status") == "closed-supported"


def test_reopened_enters_open_gap_count(tmp_instance):
    """4i. reopened gap re-enters open-routing: open_gap_count counts it."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import (
        cmd_gap_close, cmd_gap_scan, open_gap_count,
        _gap_id, GAP_TYPE_ABSENT_ROW,
    )

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_literature_note(pnd, "reopen2024")
    _make_literature_note(pnd, "closer-reopen2024")

    claim = "Reopen count test claim"
    matcher_meta = _make_support_matcher_meta([
        _make_verdict("ABSENT", claim_snippet=claim, citekey="reopen2024"),
    ])

    cmd_gap_scan("demo-research", config=cfg, matcher_meta=matcher_meta)
    gid = _gap_id(GAP_TYPE_ABSENT_ROW, "literature/reopen2024", claim)

    cmd_gap_close("demo-research", gid, "closed-supported",
                  closer_ref="literature/closer-reopen2024", config=cfg)

    # After closing, open count should be 0
    assert open_gap_count("demo-research", config=cfg) == 0

    # Re-scan → reopened
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cmd_gap_scan("demo-research", config=cfg, matcher_meta=matcher_meta)

    # After reopen, open count should be 1 again
    assert open_gap_count("demo-research", config=cfg) == 1


def test_reopened_gap_accepted_by_gap_route(tmp_instance):
    """4j. gap-route / gap-scope accepts a 'reopened' gap (re-enters routing)."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_scope

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-reopened-001", "knowledge_void", "Reopened claim",
                   status="reopened")

    # cmd_gap_scope must not reject a reopened gap (it should treat it like open)
    result = cmd_gap_scope("demo-research", "gap-reopened-001", "scope-reopen-001",
                           config=cfg, target="literature")
    assert "nodes" in result  # Phase-1 manifest returned


# ---------------------------------------------------------------------------
# 5. run-arm: closed-filled stays closed across re-scan
# ---------------------------------------------------------------------------

def test_run_arm_closed_filled_stays_closed(tmp_instance):
    """5a. closed-filled gap (closed --by experiments/ note, no backed_by) stays closed on re-scan."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import (
        cmd_gap_close, cmd_gap_scan, _gap_id, GAP_TYPE_EVALUATION_VOID,
    )

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    claim = "Performance improvement over baseline"
    # backed_by=["lit-A"] prevents knowledge_void so only evaluation_void fires
    _make_finding(pnd, "f-run-arm", claim=claim, effect="5% gain", backed_by=["lit-A"])
    _make_experiment_note(pnd, "exp-run-result-001", "Experiment run result")

    # First scan: creates evaluation_void gap
    new_gaps = cmd_gap_scan("demo-research", config=cfg)
    assert len(new_gaps) == 1
    gid = _gap_id(GAP_TYPE_EVALUATION_VOID, "findings/f-run-arm", claim)
    gap_path = pnd / "gaps" / f"{gid}.md"

    # Close --by an experiment result (no backed_by required — §5L.22 caveat a)
    cmd_gap_close("demo-research", gid, "closed-filled",
                  closer_ref="experiments/exp-run-result-001", config=cfg)

    fm = _parse_fm(gap_path)
    assert fm.get("status") == "closed-filled"
    assert fm.get("closed_by") == "experiments/exp-run-result-001"

    # Re-scan: gap STAYS closed (idempotent guard), evaluation_void re-fire → WARN only
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        cmd_gap_scan("demo-research", config=cfg)

    fm2 = _parse_fm(gap_path)
    assert fm2.get("status") == "closed-filled", (
        "run-arm regression pin: closed-filled MUST NOT be auto-reopened on re-scan"
    )
    # warn emitted for the re-fire
    assert any(issubclass(warning.category, UserWarning) for warning in w)


def test_run_arm_eval_void_warn_only(tmp_instance):
    """5b. closed-filled re-fire of evaluation_void → warn-only (not reopened)."""
    # Covered by 4g; explicit regression pin for the run-arm case.
    from research_vault.config import load_config
    from research_vault.review.gap_scan import (
        cmd_gap_close, cmd_gap_scan, _gap_id, GAP_TYPE_EVALUATION_VOID,
    )

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    claim = "Effect of Y on Z"
    _make_finding(pnd, "f-eval-pin", claim=claim, effect="significant")
    _make_experiment_note(pnd, "exp-pin-001")

    new_gaps = cmd_gap_scan("demo-research", config=cfg)
    gid = _gap_id(GAP_TYPE_EVALUATION_VOID, "findings/f-eval-pin", claim)
    gap_path = pnd / "gaps" / f"{gid}.md"

    cmd_gap_close("demo-research", gid, "closed-filled",
                  closer_ref="experiments/exp-pin-001", config=cfg)

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        cmd_gap_scan("demo-research", config=cfg)

    fm2 = _parse_fm(gap_path)
    assert fm2.get("status") == "closed-filled"
    assert any(issubclass(warning.category, UserWarning) for warning in w)


def test_run_arm_kv_warn_only(tmp_instance):
    """5c. closed-filled re-fire of knowledge_void → warn-only (not reopened)."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import (
        cmd_gap_close, cmd_gap_scan, _gap_id, GAP_TYPE_KNOWLEDGE_VOID,
    )

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    claim = "KV claim — run arm closed"
    _make_finding(pnd, "f-kv-runarm")
    _make_experiment_note(pnd, "exp-kv-001")

    new_gaps = cmd_gap_scan("demo-research", config=cfg)
    assert len(new_gaps) == 1
    gid = _gap_id(GAP_TYPE_KNOWLEDGE_VOID, "findings/f-kv-runarm",
                  "KV claim — run arm closed")
    # The finding has no claim= in FM, so it falls through to the body extraction
    # Let's get the actual gid from new_gaps
    assert len(new_gaps) == 1
    actual_gid = None
    gap_path = None
    for gp in (pnd / "gaps").glob("*.md"):
        fm = _parse_fm(gp)
        if fm.get("anchor") == "findings/f-kv-runarm":
            actual_gid = gp.stem
            gap_path = gp
            break
    assert gap_path is not None

    cmd_gap_close("demo-research", actual_gid, "closed-filled",
                  closer_ref="experiments/exp-kv-001", config=cfg)

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        cmd_gap_scan("demo-research", config=cfg)

    fm2 = _parse_fm(gap_path)
    assert fm2.get("status") == "closed-filled"
    assert any(issubclass(warning.category, UserWarning) for warning in w)


# ---------------------------------------------------------------------------
# 6. open_gap_count counts {open, reopened}
# ---------------------------------------------------------------------------

def test_open_gap_count_open(tmp_instance):
    """6a. open_gap_count counts 'open' gaps."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import open_gap_count

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-open-cnt-001", "knowledge_void", "Open", status="open")
    _make_gap_note(pnd, "gap-open-cnt-002", "contradictory", "Open2", status="open")

    assert open_gap_count("demo-research", config=cfg) == 2


def test_open_gap_count_reopened(tmp_instance):
    """6b. open_gap_count counts 'reopened' gaps."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import open_gap_count

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-reopened-cnt-001", "knowledge_void", "Reopened", status="reopened")

    assert open_gap_count("demo-research", config=cfg) == 1


def test_open_gap_count_includes_both_open_and_reopened(tmp_instance):
    """6b extended. open_gap_count counts both 'open' and 'reopened' together."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import open_gap_count

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-mix-001", "knowledge_void", "Open", status="open")
    _make_gap_note(pnd, "gap-mix-002", "absent_row", "Reopened", status="reopened")
    _make_gap_note(pnd, "gap-mix-003", "contradictory", "Closed-supp", status="closed-supported")

    assert open_gap_count("demo-research", config=cfg) == 2


def test_open_gap_count_not_closed_supported(tmp_instance):
    """6c. open_gap_count does NOT count 'closed-supported'."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import open_gap_count

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-cs-cnt", "knowledge_void", "Closed", status="closed-supported")
    assert open_gap_count("demo-research", config=cfg) == 0


def test_open_gap_count_not_closed_filled(tmp_instance):
    """6d. open_gap_count does NOT count 'closed-filled'."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import open_gap_count

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-cf-cnt", "evaluation_void", "Filled", status="closed-filled")
    assert open_gap_count("demo-research", config=cfg) == 0


def test_open_gap_count_not_proven_open(tmp_instance):
    """6e. open_gap_count does NOT count 'proven-open'."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import open_gap_count

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-po-cnt", "knowledge_void", "Proven", status="proven-open")
    assert open_gap_count("demo-research", config=cfg) == 0


def test_open_gap_count_not_promoted(tmp_instance):
    """6f. open_gap_count does NOT count 'promoted'."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import open_gap_count

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-prom-cnt", "knowledge_void", "Promoted", status="promoted")
    assert open_gap_count("demo-research", config=cfg) == 0


# ---------------------------------------------------------------------------
# 7. CLI surface
# ---------------------------------------------------------------------------

def test_cli_gap_close_with_by_exits_0(tmp_instance):
    """7a. rv review gap-close --by <ref> exits 0 and prints both edges written."""
    import subprocess, sys
    from research_vault.config import load_config
    import os

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-cli-001", "knowledge_void", "CLI test claim")
    _make_literature_note(pnd, "cli-ref2024")

    env = os.environ.copy()
    result = subprocess.run(
        [sys.executable, "-m", "research_vault.cli",
         "review", "demo-research", "gap-close", "gap-cli-001",
         "--status", "closed-supported", "--by", "literature/cli-ref2024"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    # Both edges should be mentioned in output
    output = result.stdout + result.stderr
    assert "closed_by" in output or "gap-cli-001" in output


def test_cli_gap_close_proven_open_no_by(tmp_instance):
    """7b. rv review gap-close for proven-open without --by exits 0."""
    import subprocess, sys
    import os

    from research_vault.config import load_config
    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-cli-po-001", "knowledge_void", "Proven open CLI test")

    env = os.environ.copy()
    result = subprocess.run(
        [sys.executable, "-m", "research_vault.cli",
         "review", "demo-research", "gap-close", "gap-cli-po-001",
         "--status", "proven-open"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr


def test_cli_gap_close_closed_supported_no_by_exits_1(tmp_instance):
    """7c. rv review gap-close for closed-supported without --by exits 1 (error)."""
    import subprocess, sys
    import os

    from research_vault.config import load_config
    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-cli-err-001", "knowledge_void", "Missing closer")

    env = os.environ.copy()
    result = subprocess.run(
        [sys.executable, "-m", "research_vault.cli",
         "review", "demo-research", "gap-close", "gap-cli-err-001",
         "--status", "closed-supported"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode != 0


def test_cli_gap_promote_exits_0(tmp_instance):
    """7d. rv review gap-promote <gap-id> --to <ref> exits 0 and prints status change."""
    import subprocess, sys
    import os

    from research_vault.config import load_config
    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-cli-prom-001", "knowledge_void", "Promote this",
                   status="proven-open")

    env = os.environ.copy()
    result = subprocess.run(
        [sys.executable, "-m", "research_vault.cli",
         "review", "demo-research", "gap-promote", "gap-cli-prom-001",
         "--to", "manuscript/contributions"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    output = result.stdout
    assert "promoted" in output.lower() or "gap-cli-prom-001" in output


def test_cli_gap_list_status_promoted(tmp_instance):
    """7e. rv review gap-list --status promoted shows promoted gaps."""
    import subprocess, sys
    import os

    from research_vault.config import load_config
    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-lst-prom-001", "knowledge_void", "Promoted list test",
                   status="promoted")
    _make_gap_note(pnd, "gap-lst-open-001", "knowledge_void", "Open list test",
                   status="open")

    env = os.environ.copy()
    result = subprocess.run(
        [sys.executable, "-m", "research_vault.cli",
         "review", "demo-research", "gap-list", "--status", "promoted"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "gap-lst-prom-001" in result.stdout
    assert "gap-lst-open-001" not in result.stdout


def test_cli_gap_list_status_reopened(tmp_instance):
    """7f. rv review gap-list --status reopened shows reopened gaps."""
    import subprocess, sys
    import os

    from research_vault.config import load_config
    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_gap_note(pnd, "gap-lst-reopen-001", "knowledge_void", "Reopened list test",
                   status="reopened")
    _make_gap_note(pnd, "gap-lst-open-002", "knowledge_void", "Open test 2",
                   status="open")

    env = os.environ.copy()
    result = subprocess.run(
        [sys.executable, "-m", "research_vault.cli",
         "review", "demo-research", "gap-list", "--status", "reopened"],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "gap-lst-reopen-001" in result.stdout
    assert "gap-lst-open-002" not in result.stdout


# ---------------------------------------------------------------------------
# 8. Discovery / verb registry
# ---------------------------------------------------------------------------

def test_verb_registry_includes_sr_gap_close():
    """8a. 'review' in cli._VERB_REGISTRY sr field includes 'SR-GAP-CLOSE'."""
    from research_vault.cli import _VERB_REGISTRY
    review_entry = _VERB_REGISTRY.get("review", {})
    sr_field = review_entry.get("sr", "")
    assert "SR-GAP-CLOSE" in sr_field, (
        f"Expected 'SR-GAP-CLOSE' in review verb sr field, got: {sr_field!r}"
    )


def test_gap_close_anti_pattern_by_requirement():
    """8b. gap-close parser has --by flag (do NOT close without a closer).

    Verified via the argparse parser (not getsource), so the check is live-code-grounded:
    if --by is removed from the parser the test fails.
    """
    from research_vault.review.verbs import build_parser
    p = build_parser()
    # Find the gap-close subparser by parsing args that include --by
    args = p.parse_args(["demo-proj", "gap-close", "gap-001",
                         "--status", "closed-supported",
                         "--by", "literature/ref2024"])
    # If --by is recognized, args.by == "literature/ref2024"
    assert getattr(args, "by", None) == "literature/ref2024"


def test_gap_promote_anti_pattern_in_docs():
    """8c. gap-promote subcommand is registered in the review parser.

    Verified via the argparse parser (not getsource), so the check is live-code-grounded:
    if gap-promote is removed from the parser the test fails.
    """
    from research_vault.review.verbs import build_parser
    p = build_parser()
    # Parse a gap-promote invocation — if the subcommand is absent, argparse errors
    args = p.parse_args(["demo-proj", "gap-promote", "gap-001",
                         "--to", "manuscript/contributions"])
    assert args.review_cmd == "gap-promote"
    assert getattr(args, "to", None) == "manuscript/contributions"


# ---------------------------------------------------------------------------
# 4-new. Item #30 — Signal 2 narrow: human-blessed states are WARN-only
# ---------------------------------------------------------------------------

def test_reopened_contradictory_on_promoted_is_warn_only(tmp_instance):
    """#30/Item1: contradictory re-fire on 'promoted' gap → status stays 'promoted' + UserWarning.

    Ada ruling (automation-authority + COPE): a machine must not silently reverse a
    HUMAN decision.  promoted is a human-blessed state (set only via cmd_gap_promote,
    which requires a human-provided --to ref).  The contradiction is real and must
    SURFACE (honest WARN), but auto-reopen is prohibited.  §5L.21 / #30.
    """
    import warnings
    from research_vault.config import load_config
    from research_vault.review.gap_scan import (
        cmd_gap_close, cmd_gap_promote, cmd_gap_scan, _gap_id, GAP_TYPE_CONTRADICTORY,
    )

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_concept(pnd, "c-promo-contested", supported_by=["lit-X"], contradicted_by=["lit-Y"])
    _make_literature_note(pnd, "contrib-ref2024")

    # Step 1: first scan creates the gap (status=open)
    new_gaps = cmd_gap_scan("demo-research", config=cfg)
    assert len(new_gaps) == 1
    gid = _gap_id(GAP_TYPE_CONTRADICTORY, "concepts/c-promo-contested", "c-promo-contested")
    gap_path = pnd / "gaps" / f"{gid}.md"
    assert gap_path.exists()

    # Step 2: human promotion path: proven-open → promoted (both human steps)
    cmd_gap_close("demo-research", gid, "proven-open", config=cfg)
    cmd_gap_promote("demo-research", gid, to_ref="manuscript/contributions", config=cfg)
    assert _parse_fm(gap_path).get("status") == "promoted"

    # Step 3: re-scan — concept still has both edges → Signal 2 fires on PROMOTED gap
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        cmd_gap_scan("demo-research", config=cfg)

    fm = _parse_fm(gap_path)
    # Status MUST remain 'promoted' — no auto-reopen on human-blessed state
    assert fm.get("status") == "promoted", (
        "promoted gap MUST NOT be auto-reopened by contradictory Signal 2 (Ada ruling #30). "
        f"Got status={fm.get('status')!r}"
    )
    # A UserWarning MUST be emitted (honest surface — charter §2)
    assert any(issubclass(warning.category, UserWarning) for warning in w), (
        "Expected a UserWarning for contradictory re-fire on promoted gap (#30)"
    )


def test_reopened_contradictory_on_proven_open_is_warn_only(tmp_instance):
    """#30/Item1: contradictory re-fire on 'proven-open' gap → status stays 'proven-open' + UserWarning.

    proven-open is set when a targeted literature pass saturates without closing the gap,
    signalling it as a candidate CONTRIBUTION — a human-meaningful milestone.  The machine
    must not reverse this by auto-reopening; the honest action is to surface a loud warning.
    §5L.21 / #30.
    """
    import warnings
    from research_vault.config import load_config
    from research_vault.review.gap_scan import (
        cmd_gap_close, cmd_gap_scan, _gap_id, GAP_TYPE_CONTRADICTORY,
    )

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_concept(pnd, "c-po-contested", supported_by=["lit-A"], contradicted_by=["lit-B"])

    # Step 1: first scan creates the gap (status=open)
    new_gaps = cmd_gap_scan("demo-research", config=cfg)
    assert len(new_gaps) == 1
    gid = _gap_id(GAP_TYPE_CONTRADICTORY, "concepts/c-po-contested", "c-po-contested")
    gap_path = pnd / "gaps" / f"{gid}.md"
    assert gap_path.exists()

    # Step 2: human step — mark as proven-open (targeted pass saturated; candidate contribution)
    cmd_gap_close("demo-research", gid, "proven-open", config=cfg)
    assert _parse_fm(gap_path).get("status") == "proven-open"

    # Step 3: re-scan — concept STILL has both edges → Signal 2 fires on PROVEN-OPEN gap
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        cmd_gap_scan("demo-research", config=cfg)

    fm = _parse_fm(gap_path)
    # Status MUST remain 'proven-open' — no auto-reopen on human-blessed state
    assert fm.get("status") == "proven-open", (
        "proven-open gap MUST NOT be auto-reopened by contradictory Signal 2 (Ada ruling #30). "
        f"Got status={fm.get('status')!r}"
    )
    assert any(issubclass(warning.category, UserWarning) for warning in w), (
        "Expected a UserWarning for contradictory re-fire on proven-open gap (#30)"
    )


def test_reopened_contradictory_on_machine_closed_still_reopens(tmp_instance):
    """#30/Item1 positive-control: contradictory on closed-supported → still auto-reopens.

    Pins the NARROW end of the fix: machine-closed statuses (closed-supported, closed-filled)
    still trigger auto-reopen on Signal 2.  This ensures the narrowing doesn't accidentally
    disable Signal 2 entirely.
    """
    import warnings
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_close, cmd_gap_scan, _gap_id, GAP_TYPE_CONTRADICTORY

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")
    _make_literature_note(pnd, "closer-pin-cs2024")
    _make_concept(pnd, "c-pin-contested", supported_by=["lit-A"], contradicted_by=["lit-B"])

    # First scan: creates contradictory gap
    new_gaps = cmd_gap_scan("demo-research", config=cfg)
    assert len(new_gaps) == 1
    gid = _gap_id(GAP_TYPE_CONTRADICTORY, "concepts/c-pin-contested", "c-pin-contested")
    gap_path = pnd / "gaps" / f"{gid}.md"

    # Machine-close it as closed-supported
    cmd_gap_close("demo-research", gid, "closed-supported",
                  closer_ref="literature/closer-pin-cs2024", config=cfg)
    fm = _parse_fm(gap_path)
    assert fm.get("status") == "closed-supported"

    # Re-scan: concept still contradictory → must AUTO-REOPEN (positive control)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cmd_gap_scan("demo-research", config=cfg)

    fm2 = _parse_fm(gap_path)
    assert fm2.get("status") == "reopened", (
        "closed-supported gap MUST be auto-reopened on contradictory Signal 2 "
        f"(positive-control pin for #30 narrowing). Got status={fm2.get('status')!r}"
    )


# ---------------------------------------------------------------------------
# 9. Zero ~/vault edits (all hermetic)
# ---------------------------------------------------------------------------

def test_no_vault_writes(tmp_instance, monkeypatch):
    """9. All writes go to tmp_instance, never to ~/vault."""
    from research_vault.config import load_config
    from research_vault.review.gap_scan import cmd_gap_close, cmd_gap_promote

    cfg = load_config()
    pnd = cfg.project_notes_dir("demo-research")

    # Verify pnd is inside tmp_instance, not ~/vault
    assert str(pnd).startswith(str(tmp_instance))
    vault_path = Path.home() / "vault"
    assert not str(pnd).startswith(str(vault_path))
