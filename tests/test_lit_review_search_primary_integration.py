# SPDX-License-Identifier: AGPL-3.0-or-later
"""tests/test_lit_review_search_primary_integration.py — Tier-1 deterministic
integration test for the search-primary lit-review redesign (design doc
2026-07-12-rv-lit-review-search-primary-redesign-design.md).

Unit tests already cover every seam of the redesign in isolation (B1's
token-overlap floor, A1's rerank carry-through, A3's id-backfill, C's
stratified bound, D's surgical-walk default, E's anti-gaming teeth, D-1's
walk-absent-is-not-a-failure coverage-gate refactor). This test drives the
REAL loop end-to-end over a recorded fixture so a regression that only shows
up at a SEAM boundary (the original dogfood run's failure mode) cannot hide.

Mocked (the external network boundary ONLY):
  - the ``sweep`` tool op's underlying fetch — replaced by a hand-built
    ``SweepResult``/``SweepCell``/``DedupedHit`` fixture standing in for what
    a live width-sweep would have returned. The REAL
    ``sources.sweep.write_search_hits`` (id-backfill, rerank/pole rendering,
    facet-coverage stamping) still runs on top of it.
  - the id-backfill title-lookup adapter (a fake ``.search()`` standing in
    for a live crossref/OpenAlex/S2 call) — the REAL
    ``sources.identifiers.backfill_missing_ids`` still runs against it.
  - the two thin AGENT judgment layers (review-screen, review-curate,
    review-relevance-verify) — these are LLM nodes in production; here they
    "complete" with a hand-authored artifact, exactly the same stand-in
    ``test_relevance_gate_integration.py`` (the sibling Tier-1 precedent for
    this DAG) already uses.

NEVER mocked (every internal seam the redesign touches, driven for real via
the real DAG runner — ``cmd_run``/``cmd_tick``/``cmd_approve``/
``cmd_complete``):
  - ``sources.sweep.write_search_hits`` (A1 rerank + C poles rendering,
    facet-coverage stamping, id-backfill)
  - ``sources.sweep.check_facet_coverage`` / ``compute_facet_pole_coverage``
  - the REAL ``review.autonomy._op_snowball`` (``run_walk=False`` — the
    surgical-only seed-row merge, D) via
    ``sources.snowball.build_seed_rows_from_search_hits`` /
    ``write_corpus_raw``
  - ``review.autonomy._op_relevance_screen`` ->
    ``review.relevance.screen_corpus_raw`` (B1's mechanical token-overlap
    gate)
  - ``review.autonomy._op_relevance_verify_prep`` ->
    ``review.relevance.build_verify_input``
  - ``review.relevance.classify_relevance_verdict`` /
    ``prune_off_domain_from_corpus``
  - ``review.corpus_bound.apply_corpus_bound`` (C's stratified
    largest-remainder selection + #59 protected-pin)
  - ``review.autonomy.classify_coverage_gate_with_deviation_check`` (D-1's
    walk-absent refactor)
  - ``review.facet_remediation.resolve_facet_coverage`` (E's anti-gaming
    teeth)
  - ``dag.verbs._evaluate_autonomous_gate``'s coverage-gate wiring (the
    seam that folds all of the above together — the actual dogfood-failure
    layer)

Fixture domain (neutral, invented — not the vault's real research topics):
simulated multi-agent ant-colony foraging coordination. Chosen for its
off-domain-homograph flood risk: "behavior" is a real domain term here
(the redesign's B1 fix target — the original 212-paper flood was admitted
on exactly this class of single-token collision) but also appears
incidentally in unrelated engineering literature (a piezoelectric sensor
paper), which is the flood contaminant fixture below.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.sources.base import PaperHit
from research_vault.sources.dedup import DedupedHit
from research_vault.sources.sweep import (
    SweepCell,
    SweepResult,
    check_facet_coverage,
    write_search_hits,
)
from research_vault.sources.sweep import parse_angle_matrix

# ---------------------------------------------------------------------------
# Fixture: the frozen protocol + the search-originated candidate pool
# ---------------------------------------------------------------------------

_PROTOCOL_TEXT = (
    "---\n"
    "question: \"Do simulated multi-agent ant colonies exhibit robust "
    "collective foraging behavior under resource scarcity, matching "
    "biological pheromone-trail coordination strategies?\"\n"
    "inclusion: \"Papers modeling or measuring collective foraging "
    "behavior, pheromone-trail coordination, or resource-allocation "
    "strategies in simulated multi-agent ant/insect colonies.\"\n"
    "exclusion: \"Papers with no multi-agent simulation and no "
    "foraging/resource-coordination behavior.\"\n"
    "coverage_claim: \"All papers 2020-2026 studying simulated "
    "multi-agent colony foraging coordination strategies.\"\n"
    "seed_queries:\n"
    "  by-mechanism:\n"
    "    thesis:\n"
    "      - \"decentralized pheromone-trail foraging coordination colony\"\n"
    "      - \"self-organized resource allocation ant colony simulation\"\n"
    "    counter:\n"
    "      - \"centralized planner foraging coordination colony\"\n"
    "      - \"global controller resource allocation simulated colony\"\n"
    "sources: [semantic-scholar, arxiv]\n"
    "counter-position: \"the centralized-controller literature that "
    "assumes a single global planner coordinates colony foraging, rather "
    "than decentralized pheromone-based self-organization.\"\n"
    "---\n\n# Protocol\n"
)

_THESIS_POLE = "by-mechanism.thesis"
_COUNTER_POLE = "by-mechanism.counter"


def _hit(
    *,
    title: str,
    abstract: str,
    year: int = 2023,
    doi: str | None = None,
    rerank_score: float | None = None,
    poles: frozenset[str] = frozenset(),
) -> DedupedHit:
    external_ids = {"doi": doi} if doi else {}
    h = PaperHit(
        title=title, year=year, authors=["A. Researcher"],
        external_ids=dict(external_ids), abstract=abstract,
        citation_count=0, source="fake-search",
        rerank_score=rerank_score, poles=poles,
    )
    return DedupedHit(hit=h, sources={"fake-search"}, external_ids=dict(external_ids))


def _pole_hits(prefix: str, pole: str, n: int, *, rerank_first: float | None = None) -> list[DedupedHit]:
    hits = []
    for i in range(1, n + 1):
        hits.append(_hit(
            title=f"{prefix}{i}: Pheromone-Trail Foraging Coordination Study {i}",
            abstract=(
                "We model decentralized pheromone-trail coordination in a "
                "simulated multi-agent ant colony, measuring collective "
                "foraging behavior and resource-allocation efficiency under "
                "increasing resource scarcity across repeated trials."
            ),
            doi=f"10.9999/{prefix.lower()}-{i}",
            rerank_score=rerank_first if i == 1 else None,
            poles=frozenset({pole}),
        ))
    return hits


# B1: a single-token-overlap off-domain contaminant — shares only the
# distinctive token "behavior" with the domain vocabulary (an unrelated
# piezoelectric-sensor engineering paper). The original 212-paper flood was
# admitted on exactly this class of single-token collision.
_FLOOD_HIT = _hit(
    title="Behavior of Piezoelectric Sensor Arrays Under Thermal Cycling Stress",
    abstract=(
        "We characterize the behavior of piezoelectric sensor arrays "
        "subjected to repeated thermal cycling, quantifying drift in "
        "resonant frequency across five hundred cycles and correlating "
        "shifts with lattice strain accumulation in the ceramic substrate."
    ),
    doi="10.9999/piezo-2021",
)

# A3: a canonical paper with messy metadata (no doi/arxiv/openalex/s2 at
# fetch time) but a resolvable title/year — must be BACKFILLED and KEPT,
# never silently [NO-ID]-dropped. Deliberately carries NO declared pole
# (poles=frozenset()) and is the weakest-ranked candidate in the pool, so it
# can ONLY survive C's corpus-bound selection via the #59 protected-stratum
# pin (a concept note already forward-references it) — proving the pin,
# not luck of the ranking, is what saves it.
_MESSY_HIT = _hit(
    title="Colony-Scale Foraging Coordination Under Pheromone Decay: A Field Survey",
    abstract=(
        "We survey colony-scale foraging coordination strategies under "
        "pheromone decay, drawing on decentralized trail-based resource "
        "allocation observed across several ant species in the field, and "
        "compare the observed behavior against simulated colony models."
    ),
    year=2019,
    doi=None,  # messy metadata — resolved via the id-backfill adapter below
)
_MESSY_RESOLVED_ID = "1904.14265"


def _build_sweep_result() -> tuple[SweepResult, dict[str, str]]:
    """The fixture's ``SweepResult`` — standing in for the network fetch.

    Returns ``(result, angle_matrix)`` — the angle matrix (parsed from the
    REAL protocol text, never hand-duplicated) is what the REAL
    ``sources.sweep.check_facet_coverage`` needs to compute pole coverage
    over the cells below.
    """
    angle_matrix = parse_angle_matrix(_PROTOCOL_TEXT)
    thesis_key = next(k for k in angle_matrix if k.startswith(_THESIS_POLE))
    counter_key = next(k for k in angle_matrix if k.startswith(_COUNTER_POLE))

    thesis_hits = _pole_hits("T", _THESIS_POLE, 4, rerank_first=0.831)
    counter_hits = _pole_hits("C", _COUNTER_POLE, 4, rerank_first=0.774)

    cells = [
        SweepCell(angle=thesis_key, query=angle_matrix[thesis_key], source="fake-search",
                   hits=[d.hit for d in thesis_hits]),
        SweepCell(angle=counter_key, query=angle_matrix[counter_key], source="fake-search",
                   hits=[d.hit for d in counter_hits]),
        # A generic, non-facet-tagged cell — the flood contaminant and the
        # messy-metadata canonical paper both surface here (an unnamed
        # angle never registers as a declared pole — mirrors a real
        # width-sweep's "general" residual cell).
        SweepCell(angle="general", query="colony foraging coordination", source="fake-search",
                   hits=[_FLOOD_HIT.hit, _MESSY_HIT.hit]),
    ]
    kept = [*thesis_hits, *counter_hits, _FLOOD_HIT, _MESSY_HIT]
    result = SweepResult(
        kept=kept, independent_count=len(kept), total_hits_fetched=len(kept),
        cells=cells, errors=[], dark_sources=[],
    )
    return result, angle_matrix


class _FakeBackfillAdapter:
    """Stands in for a live crossref/OpenAlex/S2 title-lookup call — the
    ONLY thing mocked in the A3 id-resolution path; the REAL
    ``sources.identifiers.backfill_missing_ids``/``resolve_missing_id``
    still run against it."""

    def __init__(self, title: str, year: int, arxiv_id: str) -> None:
        self._title = title
        self._year = year
        self._arxiv_id = arxiv_id

    def search(self, title: str, limit: int = 3):
        return [PaperHit(
            title=self._title, year=self._year, authors=[], external_ids={"arxiv": self._arxiv_id},
            abstract="", citation_count=0, source="fake-backfill",
        )]


# ---------------------------------------------------------------------------
# Driving helper — mirrors test_relevance_gate_integration.py's
# _drive_to_relevance_screen, extended through the REAL snowball merge +
# relevance-screen (never mocked, per this file's module docstring).
# ---------------------------------------------------------------------------


def _drive_search_through_relevance_screen(monkeypatch, tmp_instance: Path, scope: str):
    from research_vault.config import load_config
    from research_vault.dag.verbs import cmd_run, cmd_tick, cmd_approve, cmd_complete
    from research_vault.dag.store import RunStore
    from research_vault.review import cmd_new, autonomy as _auto

    cfg = load_config()
    note_path, review_dir, phase1 = cmd_new(
        "demo-research", scope, question="Do simulated colonies coordinate foraging?", config=cfg,
    )
    manifest_path = review_dir / "phase1-dag.json"
    rc = cmd_run(argparse.Namespace(manifest=str(manifest_path)))
    assert rc == 0
    run_id = phase1["run_id"]
    store = RunStore.from_config(cfg)

    def _fake_sweep(*, out=None, **_kw):
        result, angle_matrix = _build_sweep_result()
        facet_coverage = check_facet_coverage(angle_matrix, result.cells, min_hits_per_pole=2)
        adapter = _FakeBackfillAdapter(_MESSY_HIT.hit.title, _MESSY_HIT.hit.year, _MESSY_RESOLVED_ID)
        write_search_hits(
            result, Path(out), facet_coverage=facet_coverage,
            attempt_id_backfill=True, backfill_adapters=[adapter],
        )
        return str(out)

    monkeypatch.setitem(_auto.OP_REGISTRY, "sweep", _fake_sweep)
    # review-snowball ("snowball" op) and review-relevance-screen
    # ("relevance_screen" op) are the REAL production ops — never
    # monkeypatched (this is exactly what's under test).

    protocol_path = review_dir / "_protocol.md"
    protocol_path.write_text(_PROTOCOL_TEXT, encoding="utf-8")
    cmd_complete(argparse.Namespace(run_id=run_id, node_id="review-scope", status="succeeded"))
    cmd_tick(argparse.Namespace(run_id=run_id))
    rc = cmd_approve(argparse.Namespace(
        run_id=run_id, node_id="approve-protocol", note=None, output=[], reject=False, auto=False,
    ))
    assert rc == 0  # review-search (fake sweep, real write_search_hits) auto-executed

    # review-screen (agent stand-in): accepts the FULL candidate frontier,
    # including the flood contaminant — simulating a screen pass that,
    # like a real LLM screen, does not catch a well-disguised off-domain
    # paper. This is deliberate: it proves B1's MECHANICAL floor catches
    # what the judgment layer might miss, never merely that the judgment
    # layer itself was careful.
    all_ids = [
        *[f"10.9999/t-{i}" for i in range(1, 5)],
        *[f"10.9999/c-{i}" for i in range(1, 5)],
        _MESSY_RESOLVED_ID,
        "10.9999/piezo-2021",
    ]
    screen_path = review_dir / "_screen.md"
    screen_path.write_text(
        "# Screen\n\nPRISMA exclusion audit trail (agent-authored prose).\n\n"
        "```seeds\n" + "\n".join(all_ids) + "\n```\n",
        encoding="utf-8",
    )
    rc = cmd_complete(argparse.Namespace(run_id=run_id, node_id="review-screen", status="succeeded"))
    assert rc == 0  # review-snowball (REAL) then review-relevance-screen (REAL) auto-executed

    return run_id, review_dir, store, cfg


def _write_curate_stand_in(review_dir: Path) -> list[str]:
    """review-curate (agent stand-in): carries the RELEVANCE-SCREENED
    pool's rows verbatim into ``_corpus.md``, exactly as the real curate
    agent's tips instruct (``review.style.review_curate_tips``: carry
    abstract/rerank/poles through unchanged). Returns the ordered citekey
    list actually written (sweep_rank == table position, C's own
    contract)."""
    from research_vault.review.relevance import parse_corpus_raw_rows

    screened_text = (review_dir / "_corpus_raw_screened.md").read_text(encoding="utf-8")
    kept_region = screened_text.split("## Rejected as off-domain")[0]
    rows = parse_corpus_raw_rows(kept_region)

    # Deterministic table order: thesis, counter, then the unassigned
    # messy-metadata paper LAST (C's own worst-ranked-candidate setup —
    # see _MESSY_HIT's docstring).
    def _order_key(r: dict[str, str]) -> tuple[int, str]:
        pid = r["paper_id"]
        if pid.startswith("10.9999/t-"):
            return (0, pid)
        if pid.startswith("10.9999/c-"):
            return (1, pid)
        return (2, pid)  # the messy paper — sorts last

    rows = sorted(rows, key=_order_key)

    lines = ["| Annotation | Citekey | Title | Abstract | Rerank | Poles |", "|---|---|---|---|---|---|"]
    citekeys: list[str] = []
    for r in rows:
        lines.append(
            f"| [NEW] | {r['paper_id']} | {r['title']} | {r['abstract']} | "
            f"{r['rerank']} | {r['poles']} |"
        )
        citekeys.append(r["paper_id"])
    (review_dir / "_corpus.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return citekeys


def _complete_curate_and_cold_verify(monkeypatch, run_id: str, review_dir: Path, store, citekeys: list[str]) -> None:
    from research_vault.dag.verbs import cmd_tick, cmd_complete
    from research_vault.review.relevance import (
        CANARY_IN_SCOPE_CITEKEY, CANARY_OFF_DOMAIN_CITEKEY, IN,
    )

    rc = cmd_complete(argparse.Namespace(run_id=run_id, node_id="review-curate", status="succeeded"))
    assert rc == 0

    cmd_tick(argparse.Namespace(run_id=run_id))  # review-relevance-verify-prep (REAL op)
    rs = store.load(run_id)
    assert rs.node_status("review-relevance-verify-prep") == "succeeded", (
        rs.node_states.get("review-relevance-verify-prep")
    )

    # review-relevance-verify (COLD agent stand-in): all fixture papers are
    # genuinely in-scope (the flood contaminant was already mechanically
    # stripped upstream by B1 and never reaches this artifact) — IN for
    # every real citekey, plus the two canaries classified correctly.
    verdict_lines = ["| Citekey | Verdict |", "|---|---|"]
    for ck in citekeys:
        verdict_lines.append(f"| {ck} | {IN} |")
    from research_vault.review.relevance import OFF_DOMAIN
    verdict_lines.append(f"| {CANARY_IN_SCOPE_CITEKEY} | {IN} |")
    verdict_lines.append(f"| {CANARY_OFF_DOMAIN_CITEKEY} | {OFF_DOMAIN} |")
    (review_dir / "_relevance-verdict.md").write_text("\n".join(verdict_lines) + "\n", encoding="utf-8")

    rc = cmd_complete(argparse.Namespace(run_id=run_id, node_id="review-relevance-verify", status="succeeded"))
    assert rc == 0


# ---------------------------------------------------------------------------
# The full-loop test: B1, A1, A3, C, D, D-1, + the defect-regression.
# ---------------------------------------------------------------------------


class TestSearchPrimaryFullLoop:
    def test_default_surgical_path(self, tmp_instance: Path, monkeypatch):
        from research_vault.dag.verbs import cmd_tick
        from research_vault.review import style as _review_style

        run_id, review_dir, store, cfg = _drive_search_through_relevance_screen(
            monkeypatch, tmp_instance, "scope-search-primary",
        )

        # --- B1: the single-token-overlap flood contaminant is screened
        # OUT before review-curate ever sees the pool. ---
        screened_path = review_dir / "_corpus_raw_screened.md"
        assert screened_path.exists()
        screened_text = screened_path.read_text(encoding="utf-8")
        kept_region = screened_text.split("## Rejected as off-domain")[0]
        assert "10.9999/piezo-2021" not in kept_region
        assert "## Rejected as off-domain" in screened_text
        assert "10.9999/piezo-2021" in screened_text

        # --- Defect-regression: the corpus does NOT balloon with
        # off-domain contamination — every candidate that DID survive the
        # screen is a genuine fixture paper (9 total: 4 thesis + 4 counter
        # + 1 messy-metadata canonical), never the flood. ---
        assert "10.9999/t-1" in kept_region and "10.9999/c-1" in kept_region
        for pid in ["10.9999/t-1", "10.9999/t-2", "10.9999/t-3", "10.9999/t-4",
                    "10.9999/c-1", "10.9999/c-2", "10.9999/c-3", "10.9999/c-4"]:
            assert pid in kept_region

        # --- A3: the messy-metadata canonical paper was BACKFILLED (its
        # doi resolved via the fake title-lookup adapter), never
        # [NO-ID]-dropped. ---
        assert "1904.14265" in kept_region
        assert "[NO-ID" not in [
            line for line in kept_region.splitlines() if "1904.14265" in line
        ][0]

        # --- D: no blanket walk fired by default — _walk.md is absent,
        # _corpus_raw.md is non-empty (the seed-merge, not a walk
        # discovery). ---
        assert not (review_dir / "_walk.md").exists()
        corpus_raw_text = (review_dir / "_corpus_raw.md").read_text(encoding="utf-8")
        assert "10.9999/t-1" in corpus_raw_text
        # 10 seeds accepted at review-screen (9 genuine + the flood
        # contaminant, deliberately accepted here — see the screen
        # stand-in's docstring); the merge itself carries all 10 through
        # (it is not the merge's job to filter — that's B1's, downstream).
        assert "seed_rows_merged: 10" in corpus_raw_text

        # --- Curate stand-in: carry the screened pool through, ordered
        # thesis -> counter -> messy (the messy paper is deliberately
        # worst-ranked; see C below). ---
        citekeys = _write_curate_stand_in(review_dir)
        assert citekeys[-1] == "1904.14265"

        # --- C setup: a small, tractable bound (5) + floor (2) so the
        # bound actually binds+stratifies over the 9 IN candidates. The
        # messy paper is protected by a #59 concept forward-reference
        # (never by ranking — it has no declared pole, so its composite
        # strength is strictly weaker than every thesis/counter candidate,
        # including the 4 that get dropped by the bound). Applied BEFORE
        # completing review-relevance-verify: ``cmd_complete`` on a node
        # whose completion unblocks an autonomous gate auto-resolves that
        # gate IN THE SAME CALL (``_recompute_awaiting_go``) — coverage-gate
        # would otherwise self-certify against the (100-paper) shipped
        # default before this test ever gets a chance to override it. ---
        monkeypatch.setattr(_review_style, "get_corpus_bound", lambda *a, **kw: 5)
        monkeypatch.setattr(_review_style, "get_min_hits_per_pole", lambda *a, **kw: 2)

        concepts_dir = cfg.shared_type_root("concepts")
        concepts_dir.mkdir(parents=True, exist_ok=True)
        (concepts_dir / "colony-foraging-coordination.md").write_text(
            "---\ntitle: Colony foraging coordination\n---\n\n"
            "Grounded by "
            "[Colony-scale foraging coordination under pheromone decay]"
            "(/literature/1904.14265.md) — SUPPORTS: sole "
            "field-survey grounding this concept region.\n",
            encoding="utf-8",
        )

        _complete_curate_and_cold_verify(monkeypatch, run_id, review_dir, store, citekeys)

        rc = cmd_tick(argparse.Namespace(run_id=run_id))  # idempotent — coverage-gate already resolved above
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("coverage-gate") == "succeeded", rs.node_states.get("coverage-gate")
        decision_note = rs.node_states["coverage-gate"]["decision_note"]
        assert "HALT" not in decision_note, decision_note

        # --- D-1: certified WITHOUT a _walk.md (walk-absent is not a
        # failure) — Phase-2 auto-emitted, exactly like a clean GO. ---
        assert not (review_dir / "_walk.md").exists()
        assert (review_dir / "phase2-dag.json").exists()

        final_corpus = (review_dir / "_corpus.md").read_text(encoding="utf-8")

        # --- C: the bound actually bound + stratified — floor(2) from
        # each pole (best-ranked: t-1,t-2 / c-1,c-2), the messy paper
        # surviving ONLY via the pin, and the 4 weakest pole candidates
        # (t-3,t-4,c-3,c-4) correctly dropped (never padded, never a flat
        # top-N — see the corpus-bound residue note). ---
        for pid in ["10.9999/t-1", "10.9999/t-2", "10.9999/c-1", "10.9999/c-2",
                    "1904.14265"]:
            assert pid in final_corpus, f"{pid} missing from bounded corpus"
        for pid in ["10.9999/t-3", "10.9999/t-4", "10.9999/c-3", "10.9999/c-4"]:
            assert pid not in final_corpus, f"{pid} should have been bound-dropped"

        residue_path = review_dir / "_corpus-bound-residue.md"
        assert residue_path.exists()
        residue_text = residue_path.read_text(encoding="utf-8")
        assert "10.9999/t-3" in residue_text and "10.9999/c-3" in residue_text

        # --- A1: the rerank score persisted end-to-end through
        # _search_hits.md -> _corpus_raw.md -> _corpus_raw_screened.md ->
        # _corpus.md for a scored row, and the honest-blank sentinel for an
        # unscored one — never fabricated, never dropped. ---
        for line in final_corpus.splitlines():
            if "10.9999/t-1" in line:
                assert "0.831" in line, line
            if "10.9999/c-1" in line:
                assert "0.774" in line, line
            if "10.9999/t-2" in line:
                assert "—" in line, line  # unscored — honest-blank, not fabricated


# ---------------------------------------------------------------------------
# Section E — thin-pole anti-gaming teeth, driven through the REAL
# coverage-gate node handler (never a direct resolve_facet_coverage() unit
# call — the unit tests already cover the pure function; this proves the
# SEAM: dag/verbs.py's coverage-gate wiring actually reads the deviations
# file + remediation_state and reaches the right disposition).
# ---------------------------------------------------------------------------

_THIN_PROTOCOL_TEXT = (
    "---\n"
    "question: \"Do simulated colonies use stigmergic nest-repair "
    "coordination under structural damage?\"\n"
    "inclusion: \"Papers modeling stigmergic nest-repair coordination in "
    "simulated multi-agent colonies.\"\n"
    "exclusion: \"Papers with no colony repair/reconstruction behavior.\"\n"
    "coverage_claim: \"All papers 2020-2026 on simulated colony "
    "nest-repair coordination.\"\n"
    "seed_queries:\n"
    "  by-repair:\n"
    "    thesis:\n"
    "      - \"stigmergic nest-repair coordination colony simulation\"\n"
    "      - \"decentralized nest reconstruction termite colony\"\n"
    "    counter:\n"
    "      - \"centralized repair scheduling colony simulation\"\n"
    "      - \"planned reconstruction schedule termite colony\"\n"
    "sources: [semantic-scholar, arxiv]\n"
    "counter-position: \"the centralized-scheduling repair literature.\"\n"
    "---\n\n# Protocol\n"
)
_THIN_POLE = "by-repair.thesis"


def _drive_to_thin_pole_coverage_gate(
    monkeypatch, tmp_instance: Path, scope: str, *, seed_deviation: bool,
):
    from research_vault.config import load_config
    from research_vault.dag.verbs import cmd_run, cmd_tick, cmd_approve, cmd_complete
    from research_vault.dag.store import RunStore
    from research_vault.review import cmd_new, autonomy as _auto

    cfg = load_config()
    note_path, review_dir, phase1 = cmd_new(
        "demo-research", scope, question="Do colonies repair nests via stigmergy?", config=cfg,
    )
    manifest_path = review_dir / "phase1-dag.json"
    rc = cmd_run(argparse.Namespace(manifest=str(manifest_path)))
    assert rc == 0
    run_id = phase1["run_id"]
    store = RunStore.from_config(cfg)

    single_paper = _hit(
        title="Stigmergic Nest-Repair Coordination in a Simulated Termite Colony",
        abstract=(
            "We model stigmergic nest-repair coordination in a simulated "
            "termite colony, showing decentralized repair converges "
            "without centralized scheduling across structural damage "
            "scenarios."
        ),
        doi="10.9999/thin-repair-1",
        poles=frozenset({_THIN_POLE}),
    )
    # The counter pole is deliberately kept NON-thin (2 papers, >= the
    # floor) so the exhausted-budget branch's "first thin pole,
    # alphabetical" selection deterministically targets the thesis pole
    # under test — not an accidental collision with the counter pole.
    counter_papers = [
        _hit(
            title=f"Centralized Repair Scheduling in a Simulated Colony {i}",
            abstract=(
                "We model centralized repair scheduling coordination in a "
                "simulated colony, comparing planned reconstruction "
                "schedules against decentralized alternatives under "
                "structural damage."
            ),
            doi=f"10.9999/thin-counter-{i}",
            poles=frozenset({"by-repair.counter"}),
        )
        for i in (1, 2)
    ]

    def _fake_sweep(*, out=None, **_kw):
        angle_matrix = parse_angle_matrix(_THIN_PROTOCOL_TEXT)
        thesis_key = next(k for k in angle_matrix if k.startswith(_THIN_POLE))
        counter_key = next(k for k in angle_matrix if k.startswith("by-repair.counter"))
        cells = [
            SweepCell(angle=thesis_key, query=angle_matrix[thesis_key], source="fake-search",
                       hits=[single_paper.hit]),
            SweepCell(angle=counter_key, query=angle_matrix[counter_key], source="fake-search",
                       hits=[p.hit for p in counter_papers]),
        ]
        # min_hits_per_pole=2 here -> the thesis pole (1 paper) is thin;
        # the counter pole (2 papers) is not.
        facet_coverage = check_facet_coverage(angle_matrix, cells, min_hits_per_pole=2)
        kept = [single_paper, *counter_papers]
        result = SweepResult(
            kept=kept, independent_count=len(kept), total_hits_fetched=len(kept),
            cells=cells, errors=[], dark_sources=[],
        )
        write_search_hits(result, Path(out), facet_coverage=facet_coverage, attempt_id_backfill=True, backfill_adapters=[])
        return str(out)

    monkeypatch.setitem(_auto.OP_REGISTRY, "sweep", _fake_sweep)

    protocol_path = review_dir / "_protocol.md"
    protocol_path.write_text(_THIN_PROTOCOL_TEXT, encoding="utf-8")
    cmd_complete(argparse.Namespace(run_id=run_id, node_id="review-scope", status="succeeded"))
    cmd_tick(argparse.Namespace(run_id=run_id))
    rc = cmd_approve(argparse.Namespace(
        run_id=run_id, node_id="approve-protocol", note=None, output=[], reject=False, auto=False,
    ))
    assert rc == 0

    screen_path = review_dir / "_screen.md"
    screen_path.write_text(
        "```seeds\n10.9999/thin-repair-1\n10.9999/thin-counter-1\n"
        "10.9999/thin-counter-2\n```\n",
        encoding="utf-8",
    )
    rc = cmd_complete(argparse.Namespace(run_id=run_id, node_id="review-screen", status="succeeded"))
    assert rc == 0  # real snowball + real relevance-screen

    citekeys = _write_curate_stand_in(review_dir)
    assert set(citekeys) == {"10.9999/thin-repair-1", "10.9999/thin-counter-1", "10.9999/thin-counter-2"}

    # Pre-seed the exhausted-budget state directly (mirrors
    # test_facet_remediation_section_e.py's own unit-test setup for the
    # SAME anti-gaming-teeth branch) — the ONE remediation attempt has
    # already run in this scenario; what's under test is whether the REAL
    # coverage-gate wiring correctly reads the deviations file to decide
    # PASS-with-gap vs HALT, not the remediation round mechanics
    # themselves (already covered by test_facet_remediation_section_e.py).
    # Seeded BEFORE completing review-relevance-verify: ``cmd_complete`` on
    # the node that unblocks coverage-gate auto-resolves that gate IN THE
    # SAME CALL (``_recompute_awaiting_go``) — see the identical fix in
    # ``TestSearchPrimaryFullLoop.test_default_surgical_path``.
    rs = store.load(run_id)
    rs.meta["facet_remediation_state"] = {"rounds_used": 1}
    store.save(rs)

    # The anti-gaming-teeth mechanical proof-of-seeking record — seeded
    # (or deliberately withheld) BEFORE completing review-relevance-verify
    # for the exact same reason: cmd_complete on the node that unblocks
    # coverage-gate auto-resolves that gate IN THE SAME CALL
    # (``_recompute_awaiting_go``).
    if seed_deviation:
        from research_vault.review import autonomy as _auto

        _auto.record_deviation(
            review_dir / "_deviations.md", version=1, pre_criteria="abc", post_criteria="abc",
            removed=[], added=[], rationale="one bounded facet re-search round",
            kind=_auto.DEVIATION_KIND_WITHIN_FACET_QUERY_APPEND,
            facet_key=_THIN_POLE, new_queries=["a genuinely new query"],
            pre_query_matrix_hash="qh1", post_query_matrix_hash="qh2",
        )

    _complete_curate_and_cold_verify(monkeypatch, run_id, review_dir, store, citekeys)

    return run_id, review_dir, store, cfg


class TestThinPoleAntiGamingTeethThroughRealGate:
    def test_recorded_round_passes_with_gap(self, tmp_instance: Path, monkeypatch):
        from research_vault.dag.verbs import cmd_tick

        run_id, review_dir, store, cfg = _drive_to_thin_pole_coverage_gate(
            monkeypatch, tmp_instance, "scope-thin-pole-pass", seed_deviation=True,
        )

        rc = cmd_tick(argparse.Namespace(run_id=run_id))  # idempotent — coverage-gate already resolved
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("coverage-gate") == "succeeded", rs.node_states.get("coverage-gate")
        decision_note = rs.node_states["coverage-gate"]["decision_note"]
        assert "HALT" not in decision_note, decision_note
        assert rs.node_states["coverage-gate"].get("sparse_pole_dispositions"), (
            "expected the sparse-pole disposition to be bound for a later "
            "leaves-open gap note — never a silent pass with no record"
        )
        assert (review_dir / "phase2-dag.json").exists()

    def test_no_recorded_round_halts(self, tmp_instance: Path, monkeypatch):
        from research_vault.dag.verbs import cmd_tick

        run_id, review_dir, store, cfg = _drive_to_thin_pole_coverage_gate(
            monkeypatch, tmp_instance, "scope-thin-pole-halt", seed_deviation=False,
        )
        # No within-facet-query-append deviation recorded for this pole —
        # the anti-gaming teeth must fail closed.

        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("coverage-gate") == "blocked", rs.node_states.get("coverage-gate")
        decision_note = rs.node_states["coverage-gate"]["decision_note"]
        assert "HALT" in decision_note
        assert not (review_dir / "phase2-dag.json").exists()
