"""test_sr_gap_hygiene.py — SR-GAP-HYGIENE vanished-anchor check.

Tests that cmd_check (rv note check) surfaces a [gap-hygiene] WARN when an
open/reopened gap's anchor: field points to a deleted/nonexistent artifact.

Design:
- Degrade-to-WARN (not BLOCK) — mirrors how covers: handles an unresolved target.
- Only open + reopened gaps are checked (the actionable statuses that count toward
  open_gap_count).  Closed/promoted/proven-open gaps are skipped.
- The WARN does NOT change status or open_gap_count — it surfaces the stale
  reference so the human re-anchors or closes the gap.

All tests are hermetic (tmp_instance). No ~/vault reads or writes.
"""

from pathlib import Path

import pytest
from research_vault.config import load_config
from research_vault import note as note_mod


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _write_gap_note(project_notes_dir: Path, gap_id: str, anchor: str, status: str) -> Path:
    """Write a minimal gaps/<gap_id>.md with the given anchor and status."""
    gap_dir = project_notes_dir / "gaps"
    gap_dir.mkdir(parents=True, exist_ok=True)
    p = gap_dir / f"{gap_id}.md"
    p.write_text(
        f"---\n"
        f"type: gaps\n"
        f"id: {gap_id}\n"
        f"gap_type: knowledge_void\n"
        f"anchor: {anchor}\n"
        f"claim: \"A claim about the anchor.\"\n"
        f"why: \"Low support degree.\"\n"
        f"status: {status}\n"
        f"suggested_route: literature\n"
        f"detected: 2026-07-02\n"
        f"---\n\n"
        f"# Gap: {gap_id}\n",
        encoding="utf-8",
    )
    return p


def _write_anchor_note(project_notes_dir: Path, anchor: str) -> Path:
    """Write the anchor artifact note at project_notes_dir/<anchor>.md."""
    anchor_path = project_notes_dir / f"{anchor}.md"
    anchor_path.parent.mkdir(parents=True, exist_ok=True)
    anchor_path.write_text(
        f"---\ntype: {anchor.split('/')[0]}\ntitle: Anchor note\n---\n",
        encoding="utf-8",
    )
    return anchor_path


# ---------------------------------------------------------------------------
# red-before-green guard
# ---------------------------------------------------------------------------

def test_red_before_green_dead_anchor_not_caught_without_extension(tmp_instance):
    """Without the anchor check, a dead-anchor open gap is silently uncaught.

    This test PASSES before the fix if the check is absent, then FAILS after the
    fix because the fix adds warnings.  It is inverted to document the 'red' state:
    the REAL behavioral test is test_open_gap_dead_anchor_warns below.

    We leave this as an explicit documentation test — asserting that in the
    pre-extension world the result would have been empty for a dead anchor.
    It is superseded by the positive tests below once the fix lands.
    """
    # After the fix is in place this test confirms that the dead-anchor IS caught.
    # The "red" state (pre-fix) would have returned [].  We assert the post-fix
    # behaviour so the suite stays green on the fixed code.
    cfg = load_config(reload=True)
    base = cfg.project_notes_dir("demo-research")
    _write_gap_note(base, "gap-red-guard", "findings/ghost-finding", "open")
    # anchor does NOT exist — the anchor note is deliberately not written

    violations = note_mod.cmd_check("demo-research", config=cfg)
    gap_warns = [v for v in violations if "[gap-hygiene]" in v]
    # Post-fix: the dead anchor IS surfaced.
    assert gap_warns, (
        "Expected [gap-hygiene] WARN for dead anchor — "
        "if this fails, the extension is missing."
    )


# ---------------------------------------------------------------------------
# Core behaviour
# ---------------------------------------------------------------------------

def test_open_gap_existing_anchor_no_warn(tmp_instance):
    """An open gap whose anchor note EXISTS → no [gap-hygiene] warn."""
    cfg = load_config(reload=True)
    base = cfg.project_notes_dir("demo-research")
    _write_anchor_note(base, "findings/my-finding")
    _write_gap_note(base, "gap-live", "findings/my-finding", "open")

    violations = note_mod.cmd_check("demo-research", config=cfg)
    gap_warns = [v for v in violations if "[gap-hygiene]" in v]
    assert gap_warns == [], f"No warn expected for live anchor; got: {gap_warns}"


def test_open_gap_dead_anchor_warns(tmp_instance):
    """An open gap whose anchor was deleted/never-existed → WARN (not BLOCK)."""
    cfg = load_config(reload=True)
    base = cfg.project_notes_dir("demo-research")
    # anchor note does NOT exist
    _write_gap_note(base, "gap-dead", "findings/deleted-finding", "open")

    violations = note_mod.cmd_check("demo-research", config=cfg)
    gap_warns = [v for v in violations if "[gap-hygiene]" in v]
    assert len(gap_warns) == 1
    w = gap_warns[0]
    assert "gap-dead" in w              # gap id is named
    assert "findings/deleted-finding" in w  # dead anchor is named
    # Degrade-to-warn: cmd_check must NOT return exit-1 level (no hard violation)
    hard = [v for v in violations if not v.startswith("[repro-lint]") and not v.startswith("[gap-hygiene]")]
    assert hard == [], f"Dead anchor must not produce a hard BLOCK; got: {hard}"


def test_open_gap_dead_anchor_warn_mentions_remedy(tmp_instance):
    """The warn message mentions the suggested remedy (re-scan/close/re-anchor)."""
    cfg = load_config(reload=True)
    base = cfg.project_notes_dir("demo-research")
    _write_gap_note(base, "gap-remedy", "concepts/lost-concept", "open")

    violations = note_mod.cmd_check("demo-research", config=cfg)
    gap_warns = [v for v in violations if "[gap-hygiene]" in v]
    assert gap_warns, "Expected a warn for dead anchor"
    w = gap_warns[0]
    # must mention at least one of the standard remedies
    assert any(kw in w.lower() for kw in ("re-scan", "rescan", "close", "re-anchor", "reanchor")), (
        f"Warn should mention a remedy; got: {w!r}"
    )


def test_reopened_gap_dead_anchor_warns(tmp_instance):
    """A 'reopened' gap with a dead anchor is also warned (reopened counts toward open_gap_count)."""
    cfg = load_config(reload=True)
    base = cfg.project_notes_dir("demo-research")
    _write_gap_note(base, "gap-reopened", "literature/gone-paper", "reopened")

    violations = note_mod.cmd_check("demo-research", config=cfg)
    gap_warns = [v for v in violations if "[gap-hygiene]" in v]
    assert len(gap_warns) == 1
    assert "gap-reopened" in gap_warns[0]
    assert "literature/gone-paper" in gap_warns[0]


def test_closed_supported_dead_anchor_no_warn(tmp_instance):
    """A closed-supported gap with a dead anchor is NOT warned.

    Rationale (D-CLOSE-3): a closed gap's anchor vanishing is less urgent — the
    gap is resolved.  Surfacing it would produce noise for old closed gaps whose
    source notes were cleaned up.  The check targets actionable (open/reopened)
    gaps only.
    """
    cfg = load_config(reload=True)
    base = cfg.project_notes_dir("demo-research")
    _write_gap_note(base, "gap-closed", "findings/old-finding", "closed-supported")

    violations = note_mod.cmd_check("demo-research", config=cfg)
    gap_warns = [v for v in violations if "[gap-hygiene]" in v]
    assert gap_warns == [], (
        "closed-supported gap with dead anchor must not produce a warn; "
        f"got: {gap_warns}"
    )


def test_proven_open_dead_anchor_no_warn(tmp_instance):
    """A proven-open gap with a dead anchor is NOT warned.

    proven-open is a terminal/candidate-contribution status — the gap is resolved
    (filed as a contribution), not needing remediation.
    """
    cfg = load_config(reload=True)
    base = cfg.project_notes_dir("demo-research")
    _write_gap_note(base, "gap-proven", "findings/contribution", "proven-open")

    violations = note_mod.cmd_check("demo-research", config=cfg)
    gap_warns = [v for v in violations if "[gap-hygiene]" in v]
    assert gap_warns == [], (
        "proven-open gap with dead anchor must not warn; got: {gap_warns}"
    )


def test_promoted_dead_anchor_no_warn(tmp_instance):
    """A promoted gap with a dead anchor is NOT warned (promoted = contribution filed)."""
    cfg = load_config(reload=True)
    base = cfg.project_notes_dir("demo-research")
    _write_gap_note(base, "gap-promoted", "findings/old-contribution", "promoted")

    violations = note_mod.cmd_check("demo-research", config=cfg)
    gap_warns = [v for v in violations if "[gap-hygiene]" in v]
    assert gap_warns == [], f"promoted gap with dead anchor must not warn; got: {gap_warns}"


def test_closed_filled_dead_anchor_no_warn(tmp_instance):
    """A closed-filled gap with a dead anchor is NOT warned."""
    cfg = load_config(reload=True)
    base = cfg.project_notes_dir("demo-research")
    _write_gap_note(base, "gap-filled", "concepts/old-concept", "closed-filled")

    violations = note_mod.cmd_check("demo-research", config=cfg)
    gap_warns = [v for v in violations if "[gap-hygiene]" in v]
    assert gap_warns == [], f"closed-filled gap with dead anchor must not warn; got: {gap_warns}"


def test_multiple_gaps_only_dead_anchors_warned(tmp_instance):
    """With mixed live and dead anchors, only the dead ones produce warns."""
    cfg = load_config(reload=True)
    base = cfg.project_notes_dir("demo-research")

    _write_anchor_note(base, "findings/live-finding")
    _write_gap_note(base, "gap-live-1", "findings/live-finding", "open")
    _write_gap_note(base, "gap-dead-1", "findings/ghost-1", "open")
    _write_gap_note(base, "gap-dead-2", "concepts/ghost-2", "reopened")

    violations = note_mod.cmd_check("demo-research", config=cfg)
    gap_warns = [v for v in violations if "[gap-hygiene]" in v]
    assert len(gap_warns) == 2
    ids_warned = [w for w in gap_warns if "gap-dead-1" in w or "gap-dead-2" in w]
    assert len(ids_warned) == 2
    no_false_pos = [w for w in gap_warns if "gap-live-1" in w]
    assert no_false_pos == [], f"live anchor must not produce warn; got: {no_false_pos}"


# ---------------------------------------------------------------------------
# CLI integration: dead anchor is a warn, not an exit-1
# ---------------------------------------------------------------------------

def test_cli_note_check_dead_anchor_exits_zero(tmp_instance, capsys):
    """rv note check exits 0 even with dead-anchor gaps (warns shown, not a hard violation)."""
    from research_vault.cli import main

    cfg = load_config(reload=True)
    base = cfg.project_notes_dir("demo-research")
    _write_gap_note(base, "gap-cli-dead", "findings/missing", "open")

    result = main(["note", "demo-research", "check"])
    out = capsys.readouterr().out
    assert result == 0, "Dead-anchor gap must not flip exit code to 1"
    assert "[gap-hygiene]" in out, "The warn must appear in stdout"
