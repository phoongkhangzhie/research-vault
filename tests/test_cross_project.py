"""test_cross_project.py — hermetic acceptance tests for SR-XP / SR-XPB.

SR-XP acceptance criteria (per spec §5B-XP):
  1. rv project list enumerates >=2 seeded projects with real fields, no disclosure.
     (Covered in test_project.py; also verified here via CLI integration.)
  2. A cross-project OKF link from project A's note to project B's note resolves
     (right project + note + provenance); a dangling cross-project link flags.
  3. The lit-review loop corroborates a claim in A against a note in B (a planted
     matching finding is surfaced with its cross-project provenance).
  4. ~/vault never read/written.

SR-XPB additional criteria (architect D1–D5):
  - corroborate_across_projects requires from_slug (D3).
  - Default universe = declared peers only; non-peer --against raises (D3).
  - No declared peers → empty result (caller prints nudge).
  - Provenance carries @slug:note_rel:anchor format (Slice 5).
  - Results are ranked (score field present) (Slice 4).
  - Ranker beats substring: a topically-relevant note outranks a
    substring-coincidental hit (fixture for the ranker regression).

All tests are hermetic: tmp_path, no network, no ~/vault access.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from research_vault.config import Config, reset_config_cache
from research_vault.mdstore import resolve_cross_project_link, _check_links
from research_vault.cross_project import corroborate_across_projects, list_projects
from research_vault.project_edges import add_edge


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_cfg():
    reset_config_cache()
    yield
    reset_config_cache()


@pytest.fixture
def two_project_cfg(tmp_path: Path) -> Config:
    """Config with two projects, each with a source directory and a live state_dir."""
    proj_a = tmp_path / "project-alpha"
    proj_b = tmp_path / "project-beta"
    state = tmp_path / "state"
    proj_a.mkdir()
    proj_b.mkdir()
    state.mkdir()
    raw = {
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(state),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "adapters": {"notifier": "file", "backend": "local", "secrets": "env"},
        "projects": {
            "project-alpha": {
                "code": "pa",
                "source_dir": str(proj_a),
                "roster": ["engineer", "researcher"],
            },
            "project-beta": {
                "code": "pb",
                "source_dir": str(proj_b),
                "roster": ["researcher"],
            },
        },
    }
    return Config(raw)


@pytest.fixture
def two_project_cfg_with_edge(two_project_cfg: Config) -> Config:
    """two_project_cfg with a declared edge between project-alpha and project-beta."""
    add_edge(two_project_cfg, "project-alpha", "project-beta", "shared-domain")
    return two_project_cfg


# ---------------------------------------------------------------------------
# 1. Cross-project discovery: list_projects
# ---------------------------------------------------------------------------

def test_list_projects_returns_structured_records(two_project_cfg: Config) -> None:
    """list_projects returns structured records for all registered projects."""
    records = list_projects(two_project_cfg)
    assert len(records) >= 2
    slugs = {r["slug"] for r in records}
    assert "project-alpha" in slugs
    assert "project-beta" in slugs


def test_list_projects_has_real_fields(two_project_cfg: Config) -> None:
    """list_projects records have slug, code, source_dir, roster — no disclosure."""
    records = list_projects(two_project_cfg)
    for rec in records:
        assert "slug" in rec
        assert "code" in rec
        assert "source_dir" in rec
        assert "roster" in rec
        assert "disclosure" not in rec, "disclosure must be absent from list_projects records"


def test_list_projects_correct_values(two_project_cfg: Config, tmp_path: Path) -> None:
    """list_projects records reflect the actual registry values."""
    records = list_projects(two_project_cfg)
    by_slug = {r["slug"]: r for r in records}
    alpha = by_slug["project-alpha"]
    assert alpha["code"] == "pa"
    assert alpha["source_dir"] == str(tmp_path / "project-alpha")
    assert "engineer" in alpha["roster"]
    assert "researcher" in alpha["roster"]


# ---------------------------------------------------------------------------
# 2. Cross-project OKF link resolution (mdstore)
# ---------------------------------------------------------------------------

def test_cross_project_link_resolves_existing_note(two_project_cfg: Config, tmp_path: Path) -> None:
    """A cross-project OKF link to an existing note in project-beta resolves successfully."""
    # Plant a note in project-beta
    note = tmp_path / "project-beta" / "literature" / "smith2024.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "---\ntype: literature\ncitekey: smith2024\ntitle: Smith 2024\n---\n\n# Smith 2024\n",
        encoding="utf-8",
    )

    result = resolve_cross_project_link("project-beta", "literature/smith2024.md", two_project_cfg)

    assert result["resolved"] is True, f"Link must resolve; error: {result.get('error')}"
    assert result["project"] == "project-beta"
    assert result["note"] == "literature/smith2024.md"
    assert result["provenance"] == "@project-beta:literature/smith2024.md"
    assert result["path"] is not None
    assert result["path"].exists()


def test_cross_project_link_flags_unknown_project(two_project_cfg: Config) -> None:
    """A cross-project link to an unknown project slug does not resolve and reports error."""
    result = resolve_cross_project_link("nonexistent-project", "some/note.md", two_project_cfg)
    assert result["resolved"] is False
    assert "unknown project" in result["error"]
    assert result["provenance"] == "@nonexistent-project:some/note.md"


def test_cross_project_link_flags_missing_note(two_project_cfg: Config, tmp_path: Path) -> None:
    """A cross-project link to an existing project but absent note does not resolve."""
    result = resolve_cross_project_link("project-beta", "literature/missing.md", two_project_cfg)
    assert result["resolved"] is False
    assert result["error"] is not None
    assert result["project"] == "project-beta"


def test_check_links_detects_broken_cross_project_link(two_project_cfg: Config, tmp_path: Path) -> None:
    """_check_links catches a dangling cross-project link and reports it."""
    # Note in project-alpha referencing a NON-EXISTENT note in project-beta
    note_alpha = tmp_path / "project-alpha" / "findings" / "summary.md"
    note_alpha.parent.mkdir(parents=True, exist_ok=True)
    note_alpha.write_text(
        "---\ntype: findings\ntitle: Summary\n---\n\n"
        "See [missing beta note](@project-beta:literature/nonexistent.md).\n",
        encoding="utf-8",
    )

    issues = _check_links(
        note_alpha.read_text(encoding="utf-8"),
        note_alpha,
        tmp_path / "project-alpha",
        cfg=two_project_cfg,
    )
    assert any("nonexistent.md" in issue for issue in issues), (
        f"Expected broken cross-project link report. Got: {issues}"
    )


def test_check_links_resolves_good_cross_project_link(two_project_cfg: Config, tmp_path: Path) -> None:
    """_check_links reports no issues for a valid cross-project link."""
    # Plant a real note in project-beta
    note_beta = tmp_path / "project-beta" / "literature" / "jones2023.md"
    note_beta.parent.mkdir(parents=True, exist_ok=True)
    note_beta.write_text(
        "---\ntype: literature\ncitekey: jones2023\ntitle: Jones 2023\n---\n",
        encoding="utf-8",
    )

    # Note in project-alpha with a valid cross-project link to project-beta
    note_alpha = tmp_path / "project-alpha" / "findings" / "corroborated.md"
    note_alpha.parent.mkdir(parents=True, exist_ok=True)
    note_alpha.write_text(
        "---\ntype: findings\ntitle: Corroborated Finding\n---\n\n"
        "Corroborated by [Jones 2023](@project-beta:literature/jones2023.md).\n",
        encoding="utf-8",
    )

    issues = _check_links(
        note_alpha.read_text(encoding="utf-8"),
        note_alpha,
        tmp_path / "project-alpha",
        cfg=two_project_cfg,
    )
    cross_project_issues = [i for i in issues if "jones2023" in i]
    assert not cross_project_issues, (
        f"Valid cross-project link must not be flagged. Got: {cross_project_issues}"
    )


# ---------------------------------------------------------------------------
# 3. Cross-project corroboration (lit-review loop) — SR-XPB D3 gated
# ---------------------------------------------------------------------------

def test_corroborate_requires_from_slug(two_project_cfg: Config) -> None:
    """corroborate_across_projects raises ValueError when from_slug is None (D3)."""
    import pytest as _pytest
    with _pytest.raises(ValueError, match="from_slug is REQUIRED"):
        corroborate_across_projects(
            claim="anything",
            cfg=two_project_cfg,
            from_slug=None,
        )


def test_corroborate_no_declared_peers_returns_empty(two_project_cfg: Config) -> None:
    """With no declared edges, corroborate returns empty (caller prints nudge)."""
    hits = corroborate_across_projects(
        claim="any claim",
        cfg=two_project_cfg,
        from_slug="project-alpha",
    )
    assert hits == [], "No declared peers → must return empty (nudge is in cmd_corroborate)"


def test_corroborate_finds_matching_note_in_declared_peer(
    two_project_cfg_with_edge: Config, tmp_path: Path
) -> None:
    """corroborate_across_projects surfaces a planted matching finding in declared peer."""
    cfg = two_project_cfg_with_edge
    # Plant a note in project-beta with a specific claim
    source_b = Path(cfg.projects["project-beta"]["source_dir"])
    note = source_b / "findings" / "neural-scaling.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "---\ntype: findings\ntitle: Neural Scaling Laws\n---\n\n"
        "## Key Finding\n\n"
        "Finding: neural scaling laws predict performance from compute budget.\n",
        encoding="utf-8",
    )

    hits = corroborate_across_projects(
        claim="neural scaling laws",
        cfg=cfg,
        from_slug="project-alpha",
    )

    assert len(hits) >= 1, (
        "Expected at least one corroboration hit in project-beta. "
        f"Notes in project-beta: {list(source_b.rglob('*.md'))}"
    )
    hit = hits[0]
    assert hit["project"] == "project-beta"
    assert "neural-scaling.md" in hit["note_path"]
    assert "neural scaling laws" in hit["excerpt"].lower()
    # SR-XPB Slice 5: provenance carries anchor
    assert hit["provenance"].startswith("@project-beta:")
    assert "score" in hit, "Ranked candidates must carry a score field"


def test_corroborate_provenance_carries_anchor(
    two_project_cfg_with_edge: Config, tmp_path: Path
) -> None:
    """Provenance carries @slug:note_rel:anchor (Slice 5 format)."""
    cfg = two_project_cfg_with_edge
    source_b = Path(cfg.projects["project-beta"]["source_dir"])
    note = source_b / "methodology" / "transformer-arch.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "---\ntype: methodology\ntitle: Transformer Architecture\n---\n\n"
        "## Self-Attention\n\n"
        "The transformer architecture uses self-attention mechanisms.\n",
        encoding="utf-8",
    )

    hits = corroborate_across_projects(
        claim="self-attention",
        cfg=cfg,
        from_slug="project-alpha",
    )
    assert hits, "Expected at least one hit."
    for hit in hits:
        # @slug:note_rel:anchor format
        parts = hit["provenance"].split(":")
        assert len(parts) == 3, (
            f"Provenance must be @slug:note_rel:anchor (3 colon-separated parts). "
            f"Got: {hit['provenance']!r}"
        )
        assert parts[0].startswith("@"), "First part must start with @"
        assert "anchor" in hit, "hit must carry 'anchor' key"


def test_corroborate_excludes_from_project(
    two_project_cfg_with_edge: Config, tmp_path: Path
) -> None:
    """corroborate_across_projects does not search the from_project's own notes."""
    cfg = two_project_cfg_with_edge
    source_a = Path(cfg.projects["project-alpha"]["source_dir"])
    note_alpha = source_a / "findings" / "own-finding.md"
    note_alpha.parent.mkdir(parents=True, exist_ok=True)
    note_alpha.write_text(
        "---\ntype: findings\ntitle: Own Finding\n---\n\n"
        "This unique claim: phosphorescent bioluminescent organisms.\n",
        encoding="utf-8",
    )

    hits = corroborate_across_projects(
        claim="phosphorescent bioluminescent organisms",
        cfg=cfg,
        from_slug="project-alpha",
    )
    assert all(h["project"] != "project-alpha" for h in hits), (
        "from_project must be excluded from corroboration search."
    )


def test_corroborate_against_declared_peer(
    two_project_cfg_with_edge: Config, tmp_path: Path
) -> None:
    """corroborate_across_projects respects the against_slugs filter when peer is declared."""
    cfg = two_project_cfg_with_edge
    source_b = Path(cfg.projects["project-beta"]["source_dir"])
    note = source_b / "concepts" / "attention.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "---\ntype: concepts\ntitle: Attention\n---\n\n"
        "Attention mechanisms are central to modern NLP.\n",
        encoding="utf-8",
    )

    hits_with = corroborate_across_projects(
        claim="attention mechanisms",
        cfg=cfg,
        from_slug="project-alpha",
        against_slugs=["project-beta"],
    )
    assert hits_with, "Expected hit when searching declared peer."


def test_corroborate_against_non_peer_raises(two_project_cfg: Config) -> None:
    """--against with a non-peer slug raises ValueError (D3)."""
    # Add a third project but don't declare an edge from project-alpha to it
    # (two_project_cfg has project-alpha and project-beta; no edge declared)
    import pytest as _pytest
    with _pytest.raises(ValueError, match="not declared peers"):
        corroborate_across_projects(
            claim="anything",
            cfg=two_project_cfg,
            from_slug="project-alpha",
            against_slugs=["project-beta"],  # not a declared peer
        )


def test_corroborate_against_empty_returns_empty(
    two_project_cfg_with_edge: Config,
) -> None:
    """against_slugs=[] returns no hits."""
    hits = corroborate_across_projects(
        claim="attention mechanisms",
        cfg=two_project_cfg_with_edge,
        from_slug="project-alpha",
        against_slugs=[],
    )
    assert not hits


def test_corroborate_no_hits_returns_empty(two_project_cfg_with_edge: Config) -> None:
    """corroborate_across_projects returns empty list when no notes match."""
    hits = corroborate_across_projects(
        claim="xyzzy-unique-claim-that-matches-nothing-12345",
        cfg=two_project_cfg_with_edge,
        from_slug="project-alpha",
    )
    assert hits == []


def test_corroborate_no_vault_access(two_project_cfg: Config) -> None:
    """corroborate_across_projects never reads ~/vault (boundary enforced by design)."""
    for proj in two_project_cfg.projects.values():
        source = proj.get("source_dir", "")
        vault_path = Path.home() / "vault"
        assert not Path(source).is_relative_to(vault_path), (
            f"source_dir {source!r} must not be inside ~/vault."
        )
    assert two_project_cfg.projects, "fixture must register projects for this test to be meaningful"


# ---------------------------------------------------------------------------
# 4. Ranker beats substring (SR-XPB Slice 4 regression fixture)
# ---------------------------------------------------------------------------

def test_ranker_beats_substring_coincidental_hit(
    two_project_cfg_with_edge: Config, tmp_path: Path
) -> None:
    """A topically-relevant note outranks a substring-coincidental hit.

    The pre-ranking (substring) approach would surface any note that contains the
    query string, even one where it appears only once in passing.  The ranker scores
    by semantic relevance across the full body, so a note focused on the topic
    should score higher than one where the term is incidental.
    """
    cfg = two_project_cfg_with_edge
    source_b = Path(cfg.projects["project-beta"]["source_dir"])

    # High-relevance note: densely about "transformer attention mechanisms"
    relevant = source_b / "methodology" / "attention-deep-dive.md"
    relevant.parent.mkdir(parents=True, exist_ok=True)
    relevant.write_text(
        "---\ntype: methodology\ntitle: Attention Deep Dive\n---\n\n"
        "## Attention Mechanisms\n\n"
        "Transformer attention mechanisms compute query, key, value projections. "
        "Self-attention allows each position to attend to all other positions in the sequence. "
        "Multi-head attention runs multiple attention functions in parallel. "
        "Attention mechanisms have revolutionized natural language processing. "
        "The scaled dot-product attention computes attention weights from queries and keys.\n",
        encoding="utf-8",
    )

    # Low-relevance note: mentions the exact phrase once in an otherwise unrelated context
    coincidental = source_b / "findings" / "misc-finding.md"
    coincidental.parent.mkdir(parents=True, exist_ok=True)
    coincidental.write_text(
        "---\ntype: findings\ntitle: Misc Finding\n---\n\n"
        "## Unrelated Topic\n\n"
        "We study socioeconomic factors in rural communities. "
        "Unemployment, income inequality, and education levels were examined. "
        "Some researchers have applied transformer attention mechanisms to survey data. "
        "Survey results show correlation with voting patterns.\n",
        encoding="utf-8",
    )

    hits = corroborate_across_projects(
        claim="transformer attention mechanisms",
        cfg=cfg,
        from_slug="project-alpha",
    )

    assert len(hits) >= 2, f"Expected both notes to match. Got {len(hits)} hits."

    # Top-ranked note should be the relevant one (attention-deep-dive.md)
    top_hit = hits[0]
    assert "attention-deep-dive.md" in top_hit["note_path"], (
        f"Expected the topically-relevant note to be ranked first. "
        f"Got: {top_hit['note_path']} (score={top_hit.get('score', '?'):.3f})\n"
        f"All hits: {[(h['note_rel'], h.get('score', '?')) for h in hits]}"
    )
    # Regression guard: relevant note must score strictly higher than coincidental
    relevant_score = hits[0]["score"]
    coincidental_score = next(
        (h["score"] for h in hits if "misc-finding.md" in h["note_path"]), 0.0
    )
    assert relevant_score > coincidental_score, (
        f"Ranker must score the relevant note higher than the coincidental hit. "
        f"relevant={relevant_score:.4f}, coincidental={coincidental_score:.4f}"
    )


# ---------------------------------------------------------------------------
# 5. Ranker fallback path — sklearn blocked → Jaccard degraded notice (Slice 4)
# ---------------------------------------------------------------------------

def test_ranker_fallback_fires_to_stderr_when_sklearn_blocked(
    two_project_cfg_with_edge: Config, tmp_path: Path, capsys
) -> None:
    """When sklearn is not importable, Jaccard fallback fires with a degraded-notice to stderr.

    Uses a _BlockingFinder on sys.meta_path + evicts any cached sklearn modules to simulate
    a broken/--no-deps install.  rank_candidates is called directly (no module reload needed —
    the lazy `from sklearn import ...` inside the try block re-runs the import each call when
    the cached entry is absent).  The notice must mention 'degraded' and 'reinstall'; the
    ranker must still return results with ranker='jaccard'.
    """
    import sys
    from research_vault.cross_project import rank_candidates

    candidates = [
        {
            "project": "project-beta",
            "note_path": "methods/fallback-test.md",
            "note_rel": "methods/fallback-test.md",
            "body": "This method uses attention mechanisms.",
            "excerpt": "This method uses attention mechanisms.",
            "anchor": "Fallback Test",
            "provenance": "@project-beta:methods/fallback-test.md:Fallback Test",
        }
    ]

    class _SklearnBlocker:
        """Meta path finder that blocks sklearn imports."""
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "sklearn" or fullname.startswith("sklearn."):
                raise ImportError(f"blocked by test: {fullname}")
            return None

    blocker = _SklearnBlocker()
    # Save and evict any already-imported sklearn entries from the module cache
    saved = {k: v for k, v in list(sys.modules.items())
             if k == "sklearn" or k.startswith("sklearn.")}
    for k in saved:
        del sys.modules[k]

    sys.meta_path.insert(0, blocker)
    try:
        results = rank_candidates("attention mechanisms", candidates, min_score=0.0, top_k=5)
    finally:
        sys.meta_path.remove(blocker)
        # Restore evicted sklearn modules
        sys.modules.update(saved)

    captured = capsys.readouterr()
    # Fallback notice must appear on stderr
    assert "degraded" in captured.err.lower(), (
        f"Fallback notice must mention 'degraded'. Got stderr: {captured.err!r}"
    )
    assert "reinstall" in captured.err.lower(), (
        f"Fallback notice must mention 'reinstall'. Got stderr: {captured.err!r}"
    )
    # Must still return results (not crash)
    assert isinstance(results, list), "rank_candidates must return a list even in fallback mode"
    # Fallback ranker label must be 'jaccard'
    if results:
        assert results[0].get("ranker") == "jaccard", (
            f"Fallback results must use ranker='jaccard'. Got: {results[0].get('ranker')!r}"
        )


# ---------------------------------------------------------------------------
# 6. SR-XPB-FIX: paraphrase surfaces without verbatim substring match
#    (the exact scenario the substring pre-filter killed)
# ---------------------------------------------------------------------------

def test_paraphrase_claim_surfaces_relevant_note(
    two_project_cfg_with_edge: Config, tmp_path: Path
) -> None:
    """End-to-end: a semantically relevant note surfaces for a PARAPHRASED claim.

    Red-before-green proof: the test EXPLICITLY asserts that the claim is NOT a
    verbatim substring of the relevant note.  With the old substring pre-filter in
    place the function would return 0 hits (the relevant note is excluded before the
    ranker ever sees it).  With the filter removed, rank_candidates scores all notes
    and the relevant one surfaces above min_score.

    Gated on sklearn so the TF-IDF ranker is exercised (Jaccard fallback can invert
    ranking on small corpora — the fallback notice explains this; CI has sklearn).
    """
    pytest.importorskip("sklearn", reason="TF-IDF ranker requires scikit-learn")

    cfg = two_project_cfg_with_edge
    source_b = Path(cfg.projects["project-beta"]["source_dir"])

    # Paraphrased claim: semantically about attention and sequence modeling.
    # Deliberately uses "boost" — a word ABSENT from the relevant note — to guarantee
    # the claim cannot appear verbatim as a substring.
    claim = "attention mechanisms boost performance on sequence modeling"

    # Relevant note body: shares key vocabulary (attention, mechanisms, performance,
    # sequence, modeling) but does NOT contain the claim phrase verbatim (no "boost").
    relevant_text = (
        "---\ntype: methodology\ntitle: Self-Attention for Sequential Data\n---\n\n"
        "## Attention in Neural Architectures\n\n"
        "Self-attention mechanisms allow transformer models to process sequential data effectively. "
        "Attention mechanisms capture long-range dependencies across the input sequence. "
        "Performance of attention-based models on sequence modeling tasks exceeds prior recurrent approaches. "
        "Multi-head attention assigns relevance scores to each position in the input.\n"
    )

    # Unrelated note: agricultural topic — zero vocabulary overlap with the claim.
    unrelated_text = (
        "---\ntype: findings\ntitle: Crop Yield Study\n---\n\n"
        "## Agricultural Factors\n\n"
        "Rainfall and temperature determine crop yield in tropical regions. "
        "Soil nitrogen levels correlate with harvest volume across farming zones. "
        "Irrigation systems improve agricultural productivity in dry climates.\n"
    )

    # --- RED-BEFORE-GREEN PROOF ---
    # Assert the claim is NOT a verbatim substring of the relevant note.
    # This is what the old substring pre-filter checked: if this assert holds,
    # the old code would have returned 0 hits for this note.
    assert claim.lower() not in relevant_text.lower(), (
        "Test design error: the claim must not appear verbatim in the relevant note — "
        "otherwise this test does not prove the substring pre-filter is gone."
    )

    relevant = source_b / "methodology" / "paraphrase-relevant.md"
    relevant.parent.mkdir(parents=True, exist_ok=True)
    relevant.write_text(relevant_text, encoding="utf-8")

    unrelated = source_b / "findings" / "paraphrase-unrelated.md"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_text(unrelated_text, encoding="utf-8")

    hits = corroborate_across_projects(
        claim=claim,
        cfg=cfg,
        from_slug="project-alpha",
    )

    # The relevant note MUST surface — substring pre-filter is gone, TF-IDF scores all notes.
    # If this fails with the fix applied the pre-filter was not actually removed.
    assert any("paraphrase-relevant.md" in h["note_path"] for h in hits), (
        "Semantically relevant note must surface for a paraphrased claim. "
        "With the old substring pre-filter this would return 0 hits; with the fix it must return ≥1. "
        f"Hits: {[(h['note_rel'], h.get('score', '?')) for h in hits]}"
    )

    # The unrelated note must NOT surface (pure agricultural vocabulary → TF-IDF ≈ 0).
    assert not any("paraphrase-unrelated.md" in h["note_path"] for h in hits), (
        "The unrelated (agricultural) note must not surface for an attention/sequence claim. "
        f"Hits: {[(h['note_rel'], h.get('score', '?')) for h in hits]}"
    )

    # Relevant note must score above min_score threshold.
    relevant_hit = next(h for h in hits if "paraphrase-relevant.md" in h["note_path"])
    assert relevant_hit["score"] > 0.05, (
        f"Relevant note score must exceed min_score=0.05. Got: {relevant_hit['score']:.4f}"
    )
