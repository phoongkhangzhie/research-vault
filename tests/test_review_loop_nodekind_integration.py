# SPDX-License-Identifier: AGPL-3.0-or-later
"""test_review_loop_nodekind_integration.py — the REQUIRED real-runner
integration test for the review-loop node-kind drift fix (Option C hybrid,
docs/superpowers/specs/2026-07-09-review-loop-nodekind-drift-fix.md §5).

This drift (D1 hard-removed ``sweep``/``cited-by``/``references``, but the
builder kept emitting AGENT nodes whose specs instructed shelling them)
survived because every existing review-loop test either hand-built a
minimal manifest or monkeypatched the op registry / the runner directly.
This test drives the REAL DAG runner (``cmd_run``/``cmd_tick``/
``cmd_approve``/``cmd_complete``) over the REAL Phase-1 manifest built by
``review._build_phase1_manifest`` (via ``review.cmd_new``), injecting ONLY
the network boundary — a fake ``SourceAdapter`` registered where a real
adapter would normally be resolved. It never monkeypatches ``run_tool_op``,
``_op_sweep``, ``_op_snowball``, or ``run_citation_neighbor_walk`` — the
real op bodies run, for real, against the fake adapter.

Coverage (mirrors the spec's 5 numbered asserts):
  1. Build -> run through the real runner (review-scope -> approve-protocol
     -> review-search[tool] -> review-screen[agent] -> review-snowball[tool]
     -> review-curate[agent] -> coverage-gate).
  2. A subprocess spy proves the removed verbs (sweep/cited-by/references)
     are never shelled, for the entire run.
  3. _search_hits.md ([NEW]/[IN-CORPUS] + per-cell counts), _walk.md
     (stop_reason: exactly walk-complete:N-hops|neighborhood-exhausted|
     budget:N-calls), _corpus_raw.md all exist on disk, written by the REAL
     tool ops.
  4. produces: enforcement — an op that returns without writing its
     declared artifact drives the node to blocked, not succeeded.
  5. Budget path: a never-exhausting fake neighborhood with a tiny fetch
     budget -> stop_reason budget:N-calls + _coverage-gaps.md written by
     review-curate.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.sources.base import NotSupported, PaperHit  # noqa: E402


# ---------------------------------------------------------------------------
# The fake SourceAdapter — the ONLY injected boundary (never the runner, the
# op registry, or the builder). Registered under "semantic-scholar" via BOTH
# resolution paths a real adapter would be reached through:
#   - sources.registry._REGISTRY  (sweep's per-cell get_adapter lookup)
#   - sources.semantic_scholar.SemanticScholarAdapter (snowball's default)
# ---------------------------------------------------------------------------

def _hit(title: str, doi: str, abstract: str = "") -> PaperHit:
    return PaperHit(
        title=title, year=2024, authors=["A. Author"], external_ids={"doi": doi},
        abstract=abstract or title, citation_count=0, source="semantic-scholar",
    )


class _FakeSourceAdapter:
    """A fake SourceAdapter: canned search hits + a scripted, round-keyed
    citation-graph walk (cited_by/references). Round tracking mirrors
    tests/test_snowball.py's `_ScriptedAdapter` — a single seed paper per
    round, so ``cited_by``'s own call count IS the round number."""

    name = "semantic-scholar"

    # Class-level so every fresh instance (sweep's get_adapter makes a new
    # one per cell) shares the SAME script — only the citation-graph walk
    # (snowball's single long-lived instance) actually reads round state.
    search_hits: list[PaperHit] = []
    graph_script: dict[int, tuple[list[PaperHit], list[PaperHit]]] = {}

    def __init__(self) -> None:
        self._cited_by_calls = 0

    def search(self, query: str, *, limit: int = 20) -> list[PaperHit]:
        return list(type(self).search_hits)

    def cited_by(self, paper_id: str, *, limit: int = 20) -> list[PaperHit]:
        self._cited_by_calls += 1
        fwd, _ = type(self).graph_script.get(self._cited_by_calls, ([], []))
        return fwd

    def references(self, paper_id: str, *, limit: int = 20) -> list[PaperHit]:
        _, bwd = type(self).graph_script.get(self._cited_by_calls, ([], []))
        return bwd


def _register_fake_adapter(monkeypatch, *, search_hits, graph_script):
    import research_vault.sources.registry as registry_mod
    import research_vault.sources.semantic_scholar as s2_mod

    _FakeSourceAdapter.search_hits = search_hits
    _FakeSourceAdapter.graph_script = graph_script
    monkeypatch.setitem(registry_mod._REGISTRY, "semantic-scholar", _FakeSourceAdapter)
    monkeypatch.setattr(s2_mod, "SemanticScholarAdapter", _FakeSourceAdapter)


_PROTOCOL_TEXT = (
    "---\n"
    "counter-position: a real, actively-sought counter-position\n"
    "seed_queries:\n"
    "  by-method:     \"width-sweep method query\"\n"
    "  by-outcome:    \"width-sweep outcome query\"\n"
    "sources: [semantic-scholar]\n"
    "---\n\n# Protocol\n"
)


class _SubprocessSpy:
    """Wraps subprocess.run/Popen: records every call, still delegates to
    the real function (pass-through — never breaks a legitimate unrelated
    call), so the test can assert on the recorded argv afterward."""

    def __init__(self, real_run, real_popen):
        self.calls: list[list[str]] = []
        self._real_run = real_run
        self._real_popen = real_popen

    def run(self, argv, *a, **kw):
        self.calls.append(list(argv) if isinstance(argv, (list, tuple)) else [str(argv)])
        return self._real_run(argv, *a, **kw)

    def popen(self, argv, *a, **kw):
        self.calls.append(list(argv) if isinstance(argv, (list, tuple)) else [str(argv)])
        return self._real_popen(argv, *a, **kw)


@pytest.fixture
def subprocess_spy(monkeypatch):
    spy = _SubprocessSpy(subprocess.run, subprocess.Popen)
    monkeypatch.setattr(subprocess, "run", spy.run)
    monkeypatch.setattr(subprocess, "Popen", spy.popen)
    return spy


def _assert_removed_verbs_never_shelled(spy: "_SubprocessSpy") -> None:
    forbidden = {"sweep", "cited-by", "references"}
    for argv in spy.calls:
        joined = " ".join(argv)
        for f in forbidden:
            assert f not in argv and f"rv research {f}" not in joined, (
                f"removed verb {f!r} was shelled: {argv!r}"
            )


_REALISTIC_SCREEN_MD_TEMPLATE = (
    "---\n"
    "run_id: r1\n"
    "node_id: review-screen\n"
    "---\n\n"
    "# Screen\n\n"
    "## Exclusion audit trail\n\n"
    "- [EXCLUDE] 10.1000/notrelevant — off-topic, does not address the RQ "
    "per protocol criterion C2.\n"
    "- A follow-up sentence continuing the audit trail prose, no leading dash.\n\n"
    "## Accepted seeds\n\n"
    "```seeds\n"
    "{seed_ids}\n"
    "```\n"
)


def _drive_through_screen(run_id, review_dir, store, seed_line: str = "10.1000/fakeseed\n") -> None:
    """review-scope -> approve-protocol -> review-search(tool, real op) ->
    review-screen(agent, hand-completed) — the shared prefix of both the
    neighborhood-exhausted and walk-complete scenarios.

    ``_screen.md`` is written as a REALISTIC note (YAML frontmatter + prose
    exclusion audit trail + a fenced ```seeds``` block) — not a bare-id
    file — so this integration test exercises the review-snowball tool
    op's real ``_screen.md`` parsing path end-to-end (the exact shape that
    crashed the naive whole-file scan before the fenced-block fix)."""
    from research_vault.dag.verbs import cmd_tick, cmd_approve, cmd_complete

    protocol_path = review_dir / "_protocol.md"
    protocol_path.write_text(_PROTOCOL_TEXT, encoding="utf-8")
    rc = cmd_complete(argparse.Namespace(run_id=run_id, node_id="review-scope", status="succeeded"))
    assert rc == 0
    rc = cmd_tick(argparse.Namespace(run_id=run_id))
    assert rc == 0

    rs = store.load(run_id)
    assert rs.node_status("approve-protocol") == "awaiting-go"

    # approve-protocol's internal frontier recompute auto-executes
    # review-search (tool, op "sweep") in the SAME call — the REAL op body
    # runs here, against the fake adapter.
    rc = cmd_approve(argparse.Namespace(
        run_id=run_id, node_id="approve-protocol", note=None, output=[], reject=False, auto=False,
    ))
    assert rc == 0

    rs = store.load(run_id)
    assert rs.node_status("review-search") == "succeeded", rs.node_states.get("review-search")

    screen_path = review_dir / "_screen.md"
    seed_ids = "\n".join(s.strip() for s in seed_line.strip().splitlines() if s.strip())
    screen_path.write_text(
        _REALISTIC_SCREEN_MD_TEMPLATE.format(seed_ids=seed_ids), encoding="utf-8",
    )
    rc = cmd_complete(argparse.Namespace(run_id=run_id, node_id="review-screen", status="succeeded"))
    assert rc == 0  # review-snowball (tool, real op) + review-relevance-screen (tool, real op) auto-execute in this same call


def _complete_relevance_verify(run_id, review_dir, store, real_citekeys: list[str]) -> None:
    """review-relevance-verify-prep (tool, real op) auto-executes when
    review-curate completes; this hand-completes the COLD agent node with a
    canary-clean, all-IN verdict (PR-1, design 2026-07-10-trustworthy-
    curation-relevance-gate-design.md §3b) so coverage-gate can resolve."""
    from research_vault.dag.verbs import cmd_complete
    from research_vault.review.relevance import (
        CANARY_IN_SCOPE_CITEKEY, CANARY_OFF_DOMAIN_CITEKEY, IN, OFF_DOMAIN,
    )

    verdict_path = review_dir / "_relevance-verdict.md"
    lines = ["| Citekey | Verdict |", "|---|---|"]
    for ck in real_citekeys:
        lines.append(f"| {ck} | {IN} |")
    lines.append(f"| {CANARY_IN_SCOPE_CITEKEY} | {IN} |")
    lines.append(f"| {CANARY_OFF_DOMAIN_CITEKEY} | {OFF_DOMAIN} |")
    verdict_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    rc = cmd_complete(argparse.Namespace(
        run_id=run_id, node_id="review-relevance-verify", status="succeeded",
    ))
    assert rc == 0


class TestRealRunnerEndToEnd:
    """Items 1-3 (search-primary redesign, Section D retarget): build -> run
    through the real runner; no removed verb shelled; the tool-written
    artifacts land on disk with the expected shape — AT THE SHIPPED DEFAULT,
    which no longer runs a citation-neighbor walk at all (surgical-only).
    ``_walk.md`` is never written by ``review-snowball`` on this path;
    ``_corpus_raw.md`` is built entirely from the screen-accepted seed
    frontier's OWN row, carried through from ``_search_hits.md`` (the
    seed-row merge this PR adds — the load-bearing substrate for "search
    owns recall" to hold once the walk stops firing by default)."""

    def test_default_no_walk_path_writes_corpus_from_seed_merge_no_removed_verb_shelled(
        self, tmp_instance: Path, monkeypatch, subprocess_spy,
    ):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick, cmd_complete
        from research_vault.dag.store import RunStore
        from research_vault.review import cmd_new

        cfg = load_config()

        # The seed accepted by review-screen (10.1000/searchhit1, matching
        # _drive_through_screen's seed_line override below) IS one of the
        # search hits — its full row (title/venue/year/abstract) must be
        # carried through into _corpus_raw.md via the seed-row merge, with
        # NO citation-neighbor walk ever firing (graph_script is present
        # but must NEVER be consulted — proven by _assert_removed_verbs_
        # never_shelled below AND the _walk.md-absent assert).
        _register_fake_adapter(
            monkeypatch,
            search_hits=[_hit("A Width-Swept Paper", "10.1000/searchhit1")],
            graph_script={1: ([_hit("A Snowballed Paper", "10.1000/new1")], [])},
        )

        from research_vault.dag.verbs import cmd_run
        note_path, review_dir, phase1 = cmd_new(
            "demo-research", "scope-integration-sat", question="Does X generalize across Y?", config=cfg,
        )
        manifest_path = review_dir / "phase1-dag.json"
        rc = cmd_run(argparse.Namespace(manifest=str(manifest_path)))
        assert rc == 0
        run_id = phase1["run_id"]
        store = RunStore.from_config(cfg)

        _drive_through_screen(run_id, review_dir, store, seed_line="10.1000/searchhit1\n")

        rs = store.load(run_id)
        assert rs.node_status("review-snowball") == "succeeded", rs.node_states.get("review-snowball")

        # --- Assert 3: the real tool ops wrote the expected artifacts ---
        search_hits_path = review_dir / "_search_hits.md"
        assert search_hits_path.exists()
        search_hits_text = search_hits_path.read_text(encoding="utf-8")
        assert "[NEW]" in search_hits_text
        assert "A Width-Swept Paper" in search_hits_text
        assert "by-method" in search_hits_text and "by-outcome" in search_hits_text

        corpus_raw_path = review_dir / "_corpus_raw.md"
        assert corpus_raw_path.exists()
        corpus_raw_text = corpus_raw_path.read_text(encoding="utf-8")
        # The seed's OWN row (from _search_hits.md) — carried through, not
        # a walk discovery (the walk never ran on this path).
        assert "A Width-Swept Paper" in corpus_raw_text
        assert "seed_rows_merged: 1" in corpus_raw_text
        # The citation-neighbor walk's own graph_script paper is NEVER
        # discovered on this path — the walk (which would have found it)
        # never fired.
        assert "A Snowballed Paper" not in corpus_raw_text

        # ★ The new default: NO _walk.md at all (surgical-only; no thin
        # pole to fill, no anchor to chase) — the D-1 walk-absent GO state,
        # made real by this PR's manifest default flip.
        walk_path = review_dir / "_walk.md"
        assert not walk_path.exists(), (
            "review-snowball wrote _walk.md on the DEFAULT path — the "
            "blanket walk must not fire by default post-redesign"
        )

        # review-curate (agent) writes the FINAL _corpus.md.
        corpus_path = review_dir / "_corpus.md"
        corpus_path.write_text(
            "| annotation | citekey | title |\n|---|---|---|\n"
            "| [NEW] | widthswept2024 | A Width-Swept Paper |\n",
            encoding="utf-8",
        )
        rc = cmd_complete(argparse.Namespace(run_id=run_id, node_id="review-curate", status="succeeded"))
        assert rc == 0

        _complete_relevance_verify(run_id, review_dir, store, ["widthswept2024"])

        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("coverage-gate") == "succeeded"
        decision_note = rs.node_states["coverage-gate"]["decision_note"]
        assert "GO" in decision_note
        assert "HALT" not in decision_note
        # Phase-2 auto-emitted — coverage-gate's producer lookup resolved
        # even with _walk.md absent (D-1's walk-CONDITIONAL contract, made
        # real end-to-end by this PR's manifest change).
        assert (review_dir / "phase2-dag.json").exists()

        # --- Assert 2: no removed verb was ever shelled, anywhere in the run ---
        _assert_removed_verbs_never_shelled(subprocess_spy)


class TestProducesEnforcementRealPath:
    """Item 4: an op that returns without writing its declared produces:
    artifact drives the node to blocked — proven via the REAL review-search
    tool node (op "sweep"), not a hand-rolled manifest, by pointing the
    fake adapter at a protocol with NO parseable angle matrix (the real
    ``run_sweep_from_protocol`` raises before ever calling ``write_search_hits``,
    so the declared ``_search_hits.md`` is never written)."""

    def test_sweep_op_failure_blocks_node_not_a_crash(self, tmp_instance: Path, monkeypatch):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick, cmd_approve, cmd_complete, cmd_run
        from research_vault.dag.store import RunStore
        from research_vault.review import cmd_new

        cfg = load_config()
        _register_fake_adapter(monkeypatch, search_hits=[], graph_script={})

        note_path, review_dir, phase1 = cmd_new(
            "demo-research", "scope-integration-blocked", question="Q?", config=cfg,
        )
        manifest_path = review_dir / "phase1-dag.json"
        rc = cmd_run(argparse.Namespace(manifest=str(manifest_path)))
        assert rc == 0
        run_id = phase1["run_id"]
        store = RunStore.from_config(cfg)

        # A protocol with NO seed_queries: angle matrix — run_sweep_from_protocol
        # raises ValueError before write_search_hits is ever called, so the
        # node's declared produces: artifact is never written.
        protocol_path = review_dir / "_protocol.md"
        protocol_path.write_text(
            "---\ncounter-position: a real counter-position\n---\n\nNo seed queries here.\n",
            encoding="utf-8",
        )
        cmd_complete(argparse.Namespace(run_id=run_id, node_id="review-scope", status="succeeded"))
        cmd_tick(argparse.Namespace(run_id=run_id))
        cmd_approve(argparse.Namespace(
            run_id=run_id, node_id="approve-protocol", note=None, output=[], reject=False, auto=False,
        ))

        rs = store.load(run_id)
        assert rs.node_status("review-search") == "blocked"
        assert not (review_dir / "_search_hits.md").exists()
        assert "seed_queries" in rs.node_states["review-search"].get("tool_error", "")


class TestSurgicalTriggerWalkCompletePath:
    """Item 5 (search-primary redesign, Section D retarget): the walk is
    surgical-only now — this class proves an EXPLICIT, named trigger
    (``run_thin_pole_fill``) still reaches the SAME clean terminal
    (``walk-complete:N-hops``, no residue demanded) the old blanket-default
    test exercised, now fired deliberately rather than automatically. Runs
    against a REAL ``review_dir`` the real DAG runner already produced up
    through ``review-snowball`` (the default no-walk node), so the trigger
    is proven against real on-disk state, not a hand-built fixture."""

    def test_thin_pole_fill_trigger_walk_complete_goes_no_residue_demanded(
        self, tmp_instance: Path, monkeypatch,
    ):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick, cmd_approve, cmd_complete, cmd_run
        from research_vault.dag.store import RunStore
        from research_vault.review import cmd_new
        from research_vault.review.autonomy import run_thin_pole_fill

        cfg = load_config()

        # Every round would yield a genuinely distinct, non-derivative new
        # paper (never 2-consecutive-zero) — at relevance_hops=1, the
        # explicitly-triggered walk runs exactly ONE hop cleanly to depth
        # and stops with "walk-complete:1-hops" — round 2's script entry is
        # deliberately present to prove it's NEVER reached (the walk still
        # respects its depth bound when triggered surgically).
        _register_fake_adapter(
            monkeypatch,
            search_hits=[_hit("Seed Search Hit", "10.1000/searchhit2")],
            graph_script={
                1: ([_hit("Distinct Paper Alpha population outcome method one", "10.1000/a1")], []),
                2: ([_hit("Distinct Paper Beta measurement design cohort two", "10.1000/a2")], []),
            },
        )

        note_path, review_dir, phase1 = cmd_new(
            "demo-research", "scope-integration-walk-complete", question="Q2?", config=cfg,
        )
        manifest_path = review_dir / "phase1-dag.json"
        rc = cmd_run(argparse.Namespace(manifest=str(manifest_path)))
        assert rc == 0
        run_id = phase1["run_id"]
        store = RunStore.from_config(cfg)

        _drive_through_screen(run_id, review_dir, store, seed_line="10.1000/searchhit2\n")

        rs = store.load(run_id)
        assert rs.node_status("review-snowball") == "succeeded", rs.node_states.get("review-snowball")
        # The default no-walk node ran first — no _walk.md yet.
        assert not (review_dir / "_walk.md").exists()

        # An explicit thin-pole-fill trigger, fired directly (not a DAG
        # node — the caller is a remediation round, see
        # run_thin_pole_fill's docstring) — walks ONLY from the pole's own
        # seed(s), never the review's full frontier.
        result = run_thin_pole_fill(
            pole_seed_ids=["10.1000/searchhit2"], out_dir=str(review_dir), relevance_hops=1,
        )
        assert result["walk_ran"] is True
        assert result["stop_reason"] == "walk-complete:1-hops"

        walk_path = review_dir / "_walk.md"
        walk_text = walk_path.read_text(encoding="utf-8")
        assert "stop_reason: walk-complete:1-hops" in walk_text
        assert "walk_trigger: thin-pole-fill" in walk_text
        # Round 2's script entry (Distinct Paper Beta) was never reached —
        # the walk stopped at the 1-hop bound, not because it ran dry.
        assert "Distinct Paper Beta" not in (review_dir / "_corpus_raw.md").read_text(encoding="utf-8")
        assert "Distinct Paper Alpha" in (review_dir / "_corpus_raw.md").read_text(encoding="utf-8")

        # review-curate (agent) writes the FINAL _corpus.md — NO
        # _coverage-gaps.md needed at walk-complete:N-hops (a clean,
        # expected terminal, never a shortfall to declare).
        corpus_path = review_dir / "_corpus.md"
        corpus_path.write_text(
            "| annotation | citekey | title |\n|---|---|---|\n"
            "| [NEW] | alpha2024 | Distinct Paper Alpha |\n",
            encoding="utf-8",
        )
        gaps_path = review_dir / "_coverage-gaps.md"
        assert not gaps_path.exists()
        rc = cmd_complete(argparse.Namespace(run_id=run_id, node_id="review-curate", status="succeeded"))
        assert rc == 0

        _complete_relevance_verify(run_id, review_dir, store, ["alpha2024"])

        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("coverage-gate") == "succeeded"
        decision_note = rs.node_states["coverage-gate"]["decision_note"]
        assert "GO" in decision_note
        assert "GO-WITH-RESIDUE" not in decision_note
        assert not gaps_path.exists()  # never demanded, never fabricated
