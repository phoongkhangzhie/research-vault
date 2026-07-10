"""test_snowball.py — sources.snowball.run_snowball_to_saturation (Option C
§4-B, docs/superpowers/specs/2026-07-09-review-loop-nodekind-drift-fix.md).

Coverage:
  1. saturation: 2 consecutive zero-independent-new rounds -> stop_reason == "saturated"
  2. backstop: a never-saturating neighborhood hits the wave cap -> "backstop:N-waves"
  3. direction-starved flag when only one direction returns hits
  4. derivative discount: a near-duplicate restatement doesn't count as independent-new
  5. an adapter raising NotSupported degrades gracefully (no crash)
  6. write_corpus_raw / write_saturation render the expected artifacts
  7. resumable checkpoint: a mid-walk kill leaves a checkpoint + partial
     corpus on disk; re-invoking RESUMES (no re-fetch of visited ids) and
     reaches the same terminal corpus as an uninterrupted run
  8. round-by-round progress logging
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.sources.base import NotSupported, PaperHit
from research_vault.sources.snowball import (
    run_snowball_to_saturation,
    write_corpus_raw,
    write_saturation,
)


def _hit(title: str, *, doi: str | None = None, abstract: str = "", citation_count: int = 0) -> PaperHit:
    ext = {"doi": doi} if doi else {}
    return PaperHit(
        title=title, year=2024, authors=["A. Author"], external_ids=ext,
        abstract=abstract or title, citation_count=citation_count, source="semantic-scholar",
    )


class _ScriptedAdapter:
    """Fake SourceAdapter: scripted per-round (forward, backward) hit lists,
    keyed by round number (1-based). Missing rounds return [].

    Round tracking: every test in this file seeds a single-paper frontier
    each round (the scripted rounds each yield at most one new paper), so
    ``cited_by`` is called exactly once per round — its own call count IS
    the round number. ``references`` reads the SAME counter (cited_by is
    always invoked first for a given frontier paper by the real snowball
    loop), so this stays correct even when a subclass overrides one of the
    two methods to raise.
    """

    name = "fake"

    def __init__(self, script: dict[int, tuple[list[PaperHit], list[PaperHit]]]):
        self.script = script
        self.calls: list[tuple[str, str]] = []
        self._cited_by_calls = 0

    def search(self, query, *, limit=20):
        raise NotSupported("search not used by snowball")

    def cited_by(self, paper_id, *, limit=20):
        self._cited_by_calls += 1
        self.calls.append(("cited_by", paper_id))
        fwd, _ = self.script.get(self._cited_by_calls, ([], []))
        return fwd

    def references(self, paper_id, *, limit=20):
        self.calls.append(("references", paper_id))
        _, bwd = self.script.get(self._cited_by_calls, ([], []))
        return bwd


def test_saturation_two_consecutive_zero_rounds():
    adapter = _ScriptedAdapter({
        1: ([_hit("New Paper 1", doi="10.1/new1")], []),
        2: ([], []),
        3: ([], []),
    })
    result = run_snowball_to_saturation(["10.1/seed"], adapter=adapter, backstop_waves=3)
    assert result.stop_reason == "saturated"
    assert result.is_backstop is False
    # round 1 found something, rounds 2+3 found nothing -> 2 consecutive zero -> stop
    assert len(result.rounds) == 3
    assert result.rounds[0].new_independent == 1
    assert result.rounds[1].new_independent == 0
    assert result.rounds[2].new_independent == 0


def test_backstop_never_saturates():
    # Every round returns a genuinely new, non-derivative paper -> never
    # 2-consecutive-zero -> backstop fires at the wave cap.
    adapter = _ScriptedAdapter({
        1: ([_hit("Distinct Paper Alpha population outcome method one", doi="10.1/a1")], []),
        2: ([_hit("Distinct Paper Beta measurement design cohort two", doi="10.1/a2")], []),
        3: ([_hit("Distinct Paper Gamma protocol trial sample three", doi="10.1/a3")], []),
    })
    result = run_snowball_to_saturation(["10.1/seed"], adapter=adapter, backstop_waves=3)
    assert result.stop_reason == "backstop:3-waves"
    assert result.is_backstop is True
    assert len(result.rounds) == 3


def test_direction_starved_flag():
    adapter = _ScriptedAdapter({
        1: ([_hit("Forward Only Paper", doi="10.1/f1")], []),
        2: ([], []),
        3: ([], []),
    })
    result = run_snowball_to_saturation(["10.1/seed"], adapter=adapter, backstop_waves=3)
    assert result.rounds[0].direction_starved is True
    assert result.rounds[0].new_forward == 1
    assert result.rounds[0].new_backward == 0


def test_derivative_discount_does_not_count_as_independent_new():
    original = _hit("A Detailed Study Of Exploration Bonuses In Deep RL", doi="10.1/orig",
                     abstract="exploration bonuses deep reinforcement learning stochastic drives robust")
    restatement = _hit("A Detailed Study Of Exploration Bonuses In Deep RL (preprint)", doi="10.1/dup",
                        abstract="exploration bonuses deep reinforcement learning stochastic drives robust")
    adapter = _ScriptedAdapter({
        1: ([original, restatement], []),
        2: ([], []),
        3: ([], []),
    })
    result = run_snowball_to_saturation(["10.1/seed"], adapter=adapter, backstop_waves=3)
    # Two hits arrived, but one is a near-duplicate restatement of the
    # other -> only 1 counts as independent-new.
    assert result.rounds[0].new_independent == 1
    # Both still appear in kept (discount, never delete).
    assert len(result.kept) == 2


def test_not_supported_degrades_gracefully():
    class _NoBackwardAdapter(_ScriptedAdapter):
        def references(self, paper_id, *, limit=20):
            raise NotSupported("no reference graph")

    adapter = _NoBackwardAdapter({1: ([_hit("Fwd Only", doi="10.1/f")], []), 2: ([], []), 3: ([], [])})
    result = run_snowball_to_saturation(["10.1/seed"], adapter=adapter, backstop_waves=3)
    assert result.errors == []
    assert result.stop_reason in ("saturated", "backstop:3-waves")


def test_unexpected_exception_recorded_not_raised():
    class _BoomAdapter(_ScriptedAdapter):
        def cited_by(self, paper_id, *, limit=20):
            raise RuntimeError("adapter unreachable")

    adapter = _BoomAdapter({1: ([], []), 2: ([], []), 3: ([], [])})
    result = run_snowball_to_saturation(["10.1/seed"], adapter=adapter, backstop_waves=3)
    assert any("adapter unreachable" in e for e in result.errors)
    assert result.stop_reason  # never blank


def test_stop_reason_never_blank_and_exactly_canonical():
    adapter = _ScriptedAdapter({1: ([], []), 2: ([], []), 3: ([], [])})
    result = run_snowball_to_saturation(["10.1/seed"], adapter=adapter, backstop_waves=3)
    assert result.stop_reason == "saturated"


# ---------------------------------------------------------------------------
# 2026-07-09 live-asta validation bugs — graceful degradation on an
# adapter error (Bug 1) + id normalization before the citations/references
# call (Bug 2). These close the "faked-adapter" gap: the adapter here RAISES
# a catchable error (mirroring the real AdapterFetchError a live 404
# produces) rather than a fully scripted always-succeeds double.
# ---------------------------------------------------------------------------

class _RaisingSeedAdapter:
    """A fake SourceAdapter where ONE specific (normalized) paper id raises
    on both directions (a 404-style lookup failure); every other id follows
    the scripted round script normally. Records the exact ids it was called
    with (for the id-normalization spy tests)."""

    name = "fake"

    def __init__(self, script, *, bad_ids: set[str]):
        self.script = script
        self.bad_ids = bad_ids
        self._cited_by_calls = 0
        self.cited_by_ids: list[str] = []
        self.references_ids: list[str] = []

    def search(self, query, *, limit=20):
        raise NotSupported("search not used by snowball")

    def cited_by(self, paper_id, *, limit=20):
        self.cited_by_ids.append(paper_id)
        if paper_id in self.bad_ids:
            raise RuntimeError(f"asta papers citations failed: 404 for {paper_id}")
        self._cited_by_calls += 1
        fwd, _ = self.script.get(self._cited_by_calls, ([], []))
        return fwd

    def references(self, paper_id, *, limit=20):
        self.references_ids.append(paper_id)
        if paper_id in self.bad_ids:
            raise RuntimeError(f"asta papers get failed: 404 for {paper_id}")
        _, bwd = self.script.get(self._cited_by_calls, ([], []))
        return bwd


def test_one_bad_seed_is_skipped_walk_continues_and_completes():
    """The live-crash regression: seed `2407.16891` 404s -> the WHOLE node
    must NOT abort. A good seed resolves normally alongside it; the walk
    completes with the good seed's hits kept and the bad one recorded."""
    good_hit = _hit("Good Seed Citation", doi="10.1/good1")
    adapter = _RaisingSeedAdapter(
        {1: ([good_hit], [])}, bad_ids={"ARXIV:2407.16891"},
    )
    result = run_snowball_to_saturation(
        ["10.1/goodseed", "2407.16891"], adapter=adapter, backstop_waves=3,
    )
    # Never aborts/raises (the test reaching here at all is half the proof).
    assert result.stop_reason  # never blank
    assert result.stop_reason != "no-seeds-resolved"  # one seed DID resolve
    assert any(d.hit.title == "Good Seed Citation" for d in result.kept)
    assert "2407.16891" in result.unresolvable_ids
    assert any("2407.16891" in e for e in result.errors)


def test_all_seeds_fail_degrades_gracefully_no_crash():
    """Every seed 404s -> graceful empty-corpus outcome with a distinct,
    honest stop_reason (never mislabeled "saturated") — no crash."""
    adapter = _RaisingSeedAdapter({}, bad_ids={"ARXIV:1111.11111", "DOI:10.1234/allbad2"})
    result = run_snowball_to_saturation(
        ["1111.11111", "10.1234/allbad2"], adapter=adapter, backstop_waves=3,
    )
    assert result.stop_reason == "no-seeds-resolved"
    assert result.kept == []
    assert set(result.unresolvable_ids) == {"1111.11111", "10.1234/allbad2"}
    assert len(result.errors) >= 2  # both directions, both seeds


def test_all_seeds_fail_still_writes_artifacts(tmp_path):
    adapter = _RaisingSeedAdapter({}, bad_ids={"ARXIV:1111.11111"})
    result = run_snowball_to_saturation(["1111.11111"], adapter=adapter, backstop_waves=3)

    corpus_out = write_corpus_raw(result, tmp_path / "_corpus_raw.md")
    assert corpus_out.exists()
    assert "no-seeds-resolved" in corpus_out.read_text()

    sat_out = write_saturation(result, tmp_path / "_saturation.md")
    sat_text = sat_out.read_text()
    assert "stop_reason: no-seeds-resolved" in sat_text
    assert "unresolvable_count: 1" in sat_text
    assert "1111.11111" in sat_text


def test_seed_ids_normalized_before_adapter_call():
    """Bug 2: a bare arXiv id must reach the adapter ARXIV:-prefixed; a bare
    DOI reaches it DOI:-prefixed; an already-prefixed / S2-sha id passes
    through unchanged. Spy on the actual argument the adapter receives."""
    adapter = _RaisingSeedAdapter({}, bad_ids=set())
    run_snowball_to_saturation(
        ["2005.14165", "10.1234/x.2023", "ARXIV:1706.03762"],
        adapter=adapter, backstop_waves=1,
    )
    assert adapter.cited_by_ids == ["ARXIV:2005.14165", "DOI:10.1234/x.2023", "ARXIV:1706.03762"]
    assert adapter.references_ids == ["ARXIV:2005.14165", "DOI:10.1234/x.2023", "ARXIV:1706.03762"]


def test_real_semantic_scholar_adapter_404_degrades_walk_continues(monkeypatch):
    """Closes the faked-adapter gap directly: drives the REAL
    ``SemanticScholarAdapter`` (only ``subprocess.run`` mocked at the network
    boundary — the same seam a live 404 crosses) through
    ``run_snowball_to_saturation`` with its DEFAULT adapter (``adapter=None``).
    One seed 404s on both directions; the other resolves normally. Before
    the fix, the adapter's ``sys.exit`` on a non-zero asta exit (SystemExit,
    a BaseException) would propagate straight out of this call and abort the
    whole test/process — this proves it no longer does."""
    import json
    import subprocess as _subprocess
    from unittest.mock import MagicMock

    good_paper = {
        "title": "A Good Citing Paper", "year": 2023,
        "authors": [{"name": "A. Author"}],
        "externalIds": {"DOI": "10.1/goodcite"},
        "citationCount": 5,
    }

    def fake_run(cmd, **kwargs):
        r = MagicMock()
        if any("9999.99999" in str(a) for a in cmd):
            r.returncode = 1
            r.stdout = ""
            r.stderr = "asta: 404 not found"
        else:
            r.returncode = 0
            if "citations" in cmd:
                r.stdout = json.dumps({"data": [{"citingPaper": good_paper}]})
            else:
                r.stdout = json.dumps({"references": []})
            r.stderr = ""
        return r

    monkeypatch.setattr(_subprocess, "run", fake_run)

    result = run_snowball_to_saturation(
        ["9999.99999", "ARXIV:1706.03762"], backstop_waves=2,
    )
    assert result.stop_reason  # never blank, walk completed (didn't crash)
    assert "9999.99999" in result.unresolvable_ids
    assert any("citations failed" in e or "get failed" in e for e in result.errors)
    assert any(d.hit.title == "A Good Citing Paper" for d in result.kept)


# ---------------------------------------------------------------------------
# Resumable checkpoint (log-as-you-go, "gets dropped mid-flight" fix)
# ---------------------------------------------------------------------------

class _KillSwitchAdapter(_ScriptedAdapter):
    """Like ``_ScriptedAdapter``, but raises ``KeyboardInterrupt`` (a
    ``BaseException`` — exactly what a real process kill/Ctrl-C looks like,
    and NOT caught by the walk's per-(pid,direction) ``except Exception``)
    on the Nth ``cited_by`` call. Simulates "the process died mid-round"."""

    def __init__(self, script, *, kill_at_call: int):
        super().__init__(script)
        self.kill_at_call = kill_at_call

    def cited_by(self, paper_id, *, limit=20):
        self._cited_by_calls += 1
        self.calls.append(("cited_by", paper_id))
        if self._cited_by_calls == self.kill_at_call:
            raise KeyboardInterrupt("simulated process kill")
        fwd, _ = self.script.get(self._cited_by_calls, ([], []))
        return fwd


def test_resume_after_kill_mid_walk(tmp_path):
    """Round 1 completes and is checkpointed; the kill fires at the START of
    round 2 (its first ``cited_by`` call). Re-invoking with the SAME
    checkpoint path must resume from round 2 — never re-fetching the round-1
    seed — and reach the same terminal corpus an uninterrupted 3-round walk
    would."""
    seed = "10.1/seed"
    ckpt = tmp_path / "_snowball_checkpoint.json"

    kill_adapter = _KillSwitchAdapter(
        {1: ([_hit("New Paper 1", doi="10.1/new1")], [])}, kill_at_call=2,
    )
    with pytest.raises(KeyboardInterrupt):
        run_snowball_to_saturation(
            [seed], adapter=kill_adapter, backstop_waves=3, checkpoint_path=ckpt,
        )

    # 1. Checkpoint + partial corpus survive the kill.
    assert ckpt.exists()
    data = json.loads(ckpt.read_text(encoding="utf-8"))
    assert data["completed_round"] == 1
    assert [h["title"] for h in data["all_hits"]] == ["New Paper 1"]
    assert data["frontier"]  # round 1's new paper feeds round 2's frontier

    # Round 1's seed was fetched exactly once before the kill fired.
    assert kill_adapter.calls.count(("cited_by", seed)) == 1

    # 2. Resume: a BRAND NEW adapter instance. If the walk re-fetched round 1
    # (the seed), this adapter's own call log would show it — it never does.
    resume_adapter = _ScriptedAdapter({1: ([], []), 2: ([], [])})
    result = run_snowball_to_saturation(
        [seed], adapter=resume_adapter, backstop_waves=3, checkpoint_path=ckpt,
    )

    fetched_ids = [pid for _, pid in resume_adapter.calls]
    assert seed not in fetched_ids  # no re-fetch of the visited round-1 seed
    assert result.stop_reason == "saturated"
    assert [d.hit.title for d in result.kept] == ["New Paper 1"]
    assert not ckpt.exists()  # cleaned up on clean completion

    # 3. Same terminal corpus as an uninterrupted run over the identical script.
    baseline_adapter = _ScriptedAdapter({
        1: ([_hit("New Paper 1", doi="10.1/new1")], []),
        2: ([], []),
        3: ([], []),
    })
    baseline = run_snowball_to_saturation(
        [seed], adapter=baseline_adapter, backstop_waves=3,
    )
    assert {d.hit.title for d in baseline.kept} == {d.hit.title for d in result.kept}
    assert baseline.stop_reason == result.stop_reason


def test_fresh_run_with_checkpoint_path_but_no_prior_checkpoint(tmp_path):
    """No checkpoint file present -> behaves exactly like today's uncheckpointed
    run, and cleans up (no leftover checkpoint) on completion."""
    ckpt = tmp_path / "_snowball_checkpoint.json"
    adapter = _ScriptedAdapter({
        1: ([_hit("New Paper 1", doi="10.1/new1")], []),
        2: ([], []),
        3: ([], []),
    })
    result = run_snowball_to_saturation(
        ["10.1/seed"], adapter=adapter, backstop_waves=3, checkpoint_path=ckpt,
    )
    assert result.stop_reason == "saturated"
    assert not ckpt.exists()


def test_no_checkpoint_path_behaves_as_today():
    """Backward compat: omitting checkpoint_path entirely is unchanged."""
    adapter = _ScriptedAdapter({
        1: ([_hit("New Paper 1", doi="10.1/new1")], []),
        2: ([], []),
        3: ([], []),
    })
    result = run_snowball_to_saturation(["10.1/seed"], adapter=adapter, backstop_waves=3)
    assert result.stop_reason == "saturated"


def test_progress_log_emits_round_lines(capsys):
    adapter = _ScriptedAdapter({
        1: ([_hit("New Paper 1", doi="10.1/new1")], []),
        2: ([], []),
        3: ([], []),
    })
    run_snowball_to_saturation(["10.1/seed"], adapter=adapter, backstop_waves=3)
    captured = capsys.readouterr()
    assert "round 1/3" in captured.err
    assert "round 2/3" in captured.err
    assert "round 3/3" in captured.err


def test_progress_log_custom_callback():
    """A caller (e.g. the ``snowball`` tool op) can supply its own sink
    instead of stderr — e.g. to route into a review-node log file."""
    lines: list[str] = []
    adapter = _ScriptedAdapter({1: ([], []), 2: ([], []), 3: ([], [])})
    run_snowball_to_saturation(
        ["10.1/seed"], adapter=adapter, backstop_waves=3, progress_cb=lines.append,
    )
    assert any("round 1/3" in line for line in lines)


def test_write_corpus_raw_and_saturation(tmp_path):
    adapter = _ScriptedAdapter({
        1: ([_hit("New Paper 1", doi="10.1/new1")], []),
        2: ([], []),
        3: ([], []),
    })
    result = run_snowball_to_saturation(["10.1/seed"], adapter=adapter, backstop_waves=3)

    corpus_out = write_corpus_raw(result, tmp_path / "_corpus_raw.md", notes_index={})
    text = corpus_out.read_text()
    assert "[NEW]" in text
    assert "New Paper 1" in text
    assert f"Stop reason: {result.stop_reason}" in text

    sat_out = write_saturation(result, tmp_path / "_saturation.md")
    sat_text = sat_out.read_text()
    assert "stop_reason: saturated" in sat_text
    assert "| Round |" in sat_text


# ---------------------------------------------------------------------------
# Paper-id join-key regression (F1 teeth followup, PR #206 review delta):
# `_paper_id_of` at BOTH call sites (frontier re-seed + `write_corpus_raw`)
# must read the MERGED `d.external_ids` off the `DedupedHit` wrapper, never
# the bare `d.hit.external_ids` — a strict subset in the common case where a
# leaner adapter's hit wins representative status over a richer duplicate
# that only collapses via a shared normalized title (s2 ids never factor
# into `identity_key`'s priority chain). Reverting either call site to
# `d.hit.external_ids` must turn this test RED.
# ---------------------------------------------------------------------------

def test_snowball_paper_id_never_blank_when_a_duplicate_has_it(tmp_path):
    """Round 1's forward direction surfaces a narrow hit (no ids at all);
    the SAME round's backward direction surfaces a title-identical
    duplicate carrying an s2 id. `dedup_hits` collapses them onto one
    identity (title match — s2 never influences `identity_key`), with the
    narrow forward hit as the first-seen representative. The merged union
    DOES carry the s2 id; both the frontier re-seed AND the rendered
    `_corpus_raw.md` row must reflect it — never a blank/`[NO-ID]`
    Paper-id."""
    title = "Activation Steering For Cultural Values In Cross Lingual Models"
    narrow_hit = PaperHit(
        title=title, year=2024, authors=["A. One"], external_ids={},
        abstract=title, citation_count=5, source="openalex",
    )
    rich_duplicate = PaperHit(
        title=title, year=2024, authors=["A. One"], external_ids={"s2": "abc123steer"},
        abstract=title, citation_count=5, source="semantic-scholar",
    )
    adapter = _ScriptedAdapter({
        1: ([narrow_hit], [rich_duplicate]),
        2: ([], []),
        3: ([], []),
    })
    result = run_snowball_to_saturation(["10.1/seed"], adapter=adapter, backstop_waves=3)

    assert len(result.kept) == 1
    kept = result.kept[0]
    # sanity: the merged union really does carry the s2 id (proves this test
    # is non-vacuous — round 1's own merge is the ONLY source of the id,
    # since `narrow_hit` itself never carried one).
    assert kept.external_ids == {"s2": "abc123steer"}
    assert kept.hit.external_ids == {"s2": "abc123steer"}

    corpus_out = write_corpus_raw(result, tmp_path / "_corpus_raw.md", notes_index={})
    text = corpus_out.read_text()
    row = next(line for line in text.splitlines() if title in line)
    assert "abc123steer" in row, f"Paper-id column is blank despite the merged union carrying an s2 id:\n{row}"
    assert "[NO-ID" not in row


def test_snowball_frontier_reseed_resolves_merged_pid(tmp_path, monkeypatch):
    """Directly tests the FRONTIER RE-SEED call site (~line 435), not just
    the render — a separate assertion from the test above, since the render
    fix and the frontier-reseed fix are two distinct call sites that could
    independently regress. Spies on ``_atomic_write_json`` (the checkpoint
    writer) to capture ``visited_pids`` as of round 1's completion — the
    checkpoint itself is deleted on clean completion, so this is the only
    window to observe it. Under the pre-fix bug (frontier reseed reading
    the bare, narrower ``d.hit.external_ids``), round 1's ``pid`` resolves
    to ``None`` (the representative hit has no ids of its own) and
    ``abc123steer`` never enters ``visited_pids`` at all."""
    from research_vault.sources import snowball as snowball_mod

    title = "Activation Steering For Cultural Values In Cross Lingual Models"
    narrow_hit = PaperHit(
        title=title, year=2024, authors=["A. One"], external_ids={},
        abstract=title, citation_count=5, source="openalex",
    )
    rich_duplicate = PaperHit(
        title=title, year=2024, authors=["A. One"], external_ids={"s2": "abc123steer"},
        abstract=title, citation_count=5, source="semantic-scholar",
    )
    adapter = _ScriptedAdapter({
        1: ([narrow_hit], [rich_duplicate]),
        2: ([], []),
        3: ([], []),
    })

    captured_calls: list[dict] = []
    real_atomic_write = snowball_mod._atomic_write_json

    def _spy_atomic_write(path, data):
        captured_calls.append(dict(data))
        real_atomic_write(path, data)

    monkeypatch.setattr(snowball_mod, "_atomic_write_json", _spy_atomic_write)

    ckpt = tmp_path / "_snowball_checkpoint.json"
    run_snowball_to_saturation(
        ["10.1/seed"], adapter=adapter, backstop_waves=3, checkpoint_path=ckpt,
    )

    assert len(captured_calls) >= 1, "checkpoint must be written after round 1"
    round1_visited_pids = captured_calls[0]["visited_pids"]
    assert "abc123steer" in round1_visited_pids, (
        "the frontier re-seed loop failed to resolve the merged s2 id — "
        f"visited_pids after round 1 was {round1_visited_pids!r}"
    )


# ---------------------------------------------------------------------------
# Pre-publish hardening #199 followup: checkpoint write must degrade
# gracefully on a non-JSON-serializable state, and checkpoint LOAD must
# treat a key-missing (truncated/foreign) file as absent, never crash.
# ---------------------------------------------------------------------------


class _NotJSONSerializable:
    """A stand-in for a future adapter's non-serializable ``raw`` payload."""

    def __repr__(self):
        return "<_NotJSONSerializable>"


def test_atomic_write_json_degrades_on_non_serializable_state(tmp_path, capsys):
    from research_vault.sources.snowball import _atomic_write_json

    ckpt = tmp_path / "_snowball_checkpoint.json"
    # A dict with a genuinely non-JSON-serializable value (a bare object,
    # mirroring a future adapter stashing something exotic in PaperHit.raw).
    bad_state = {"all_hits": [{"raw": _NotJSONSerializable()}]}

    _atomic_write_json(ckpt, bad_state)  # must NOT raise

    assert not ckpt.exists()  # no half-written / corrupt file left behind
    err = capsys.readouterr().err
    assert "checkpoint write skipped" in err


def test_snowball_walk_survives_non_serializable_raw_in_hit(tmp_path):
    """End-to-end: a hit carrying a non-serializable ``raw`` must not crash
    the whole walk at end-of-round — the walk completes, just without a
    persisted checkpoint for that round."""
    ckpt = tmp_path / "_snowball_checkpoint.json"
    bad_hit = _hit("Bad Raw Paper", doi="10.1/badraw")
    bad_hit.raw = _NotJSONSerializable()
    adapter = _ScriptedAdapter({
        1: ([bad_hit], []),
        2: ([], []),
        3: ([], []),
    })
    result = run_snowball_to_saturation(
        ["10.1/seed"], adapter=adapter, backstop_waves=3, checkpoint_path=ckpt,
    )
    assert result.stop_reason == "saturated"
    assert [d.hit.title for d in result.kept] == ["Bad Raw Paper"]


def test_checkpoint_missing_required_key_treated_as_absent(tmp_path):
    """A checkpoint that matches version/seed_ids/backstop_waves but is
    missing a key the resume path reads directly (e.g. a truncated write,
    or a hand-edited/foreign file) must be treated as absent — a fresh
    start — never a KeyError crash."""
    ckpt = tmp_path / "_snowball_checkpoint.json"
    seed = "10.1/seed"
    # Matches the version/seed_ids/backstop_waves gate, but is missing
    # "all_hits" (and several other required resume-path keys) entirely.
    ckpt.write_text(json.dumps({
        "version": 1,
        "seed_ids": [seed],
        "backstop_waves": 3,
        "completed_round": 1,
        "frontier": [seed],
        # "all_hits", "seen_identities", "visited_pids", "errors", "rounds",
        # "unresolvable_ids", "unresolvable_seen", "consecutive_zero" absent.
    }), encoding="utf-8")

    adapter = _ScriptedAdapter({
        1: ([_hit("New Paper 1", doi="10.1/new1")], []),
        2: ([], []),
        3: ([], []),
    })
    # Must NOT KeyError — must fall through to a fresh start (round 1 runs
    # again, fetching the seed).
    result = run_snowball_to_saturation(
        [seed], adapter=adapter, backstop_waves=3, checkpoint_path=ckpt,
    )
    assert result.stop_reason == "saturated"
    assert adapter.calls.count(("cited_by", seed)) == 1  # fresh-start refetch


# ---------------------------------------------------------------------------
# Breadth x depth bounds — a broad-topic downstream-project validation walk ran
# unbounded (1hr+); these cap it (seed_cap / frontier_cap / fetch_budget /
# backstop_waves=2 default).
# ---------------------------------------------------------------------------


class _SpyAdapter:
    """Fake adapter recording every (direction, pid) call it receives, keyed
    scripted response per pid (not per round — needed for multi-pid-per-round
    frontier-cap tests, unlike ``_ScriptedAdapter`` which scripts by round
    index)."""

    name = "fake"

    def __init__(self, fwd_by_pid: dict[str, list[PaperHit]] | None = None,
                 bwd_by_pid: dict[str, list[PaperHit]] | None = None):
        self.fwd_by_pid = fwd_by_pid or {}
        self.bwd_by_pid = bwd_by_pid or {}
        self.cited_by_ids: list[str] = []
        self.references_ids: list[str] = []

    def search(self, query, *, limit=20):
        raise NotSupported("search not used by snowball")

    def cited_by(self, paper_id, *, limit=20):
        self.cited_by_ids.append(paper_id)
        return self.fwd_by_pid.get(paper_id, [])

    def references(self, paper_id, *, limit=20):
        self.references_ids.append(paper_id)
        return self.bwd_by_pid.get(paper_id, [])


def test_seed_cap_caps_to_top_25_preserving_input_order():
    """No PaperHit exists at the seed stage (bare ids off ``_screen.md``), so
    the declared fallback is: preserve input order, take the first
    ``seed_cap``. 30 seeds in -> only the first 25 are ever fetched."""
    seeds = [f"10.1234/seed{i}" for i in range(30)]
    adapter = _SpyAdapter()
    result = run_snowball_to_saturation(
        seeds, adapter=adapter, backstop_waves=1, seed_cap=25,
    )
    assert len(adapter.cited_by_ids) == 25
    assert adapter.cited_by_ids == [f"DOI:10.1234/seed{i}" for i in range(25)]
    assert result.seed_count == 25


def test_seed_cap_no_op_when_under_the_cap():
    seeds = [f"10.1234/seed{i}" for i in range(5)]
    adapter = _SpyAdapter()
    result = run_snowball_to_saturation(
        seeds, adapter=adapter, backstop_waves=1, seed_cap=25,
    )
    assert len(adapter.cited_by_ids) == 5
    assert result.seed_count == 5


def test_frontier_cap_promotes_only_top_25_by_citation_count():
    """Round 1's single seed discovers 30 distinct new papers with varying
    ``citation_count``. Only the top 25 (by citation_count desc) may seed
    round 2's frontier — the other 5 are still kept in the corpus (§ discount
    never delete) but don't expand the walk further."""
    seed = "10.1234/seed"
    thirty_hits = [
        _hit(f"Distinct Topic Paper {i} on subject area {i}", doi=f"10.1234/p{i}",
             abstract=f"unique abstract content about subject matter number {i} only",
             citation_count=i)
        for i in range(30)
    ]
    adapter = _SpyAdapter(fwd_by_pid={"DOI:10.1234/seed": thirty_hits})
    result = run_snowball_to_saturation(
        [seed], adapter=adapter, backstop_waves=2, frontier_cap=25,
    )
    # Round 1: only the seed is fetched (1 cited_by + 1 references call).
    # Round 2: exactly the top-25-by-citation_count promoted papers are
    # fetched — never all 30.
    round2_fetched = adapter.cited_by_ids[1:]
    assert len(round2_fetched) == 25
    # citation_count 0..4 (the bottom 5) must NOT have been promoted.
    for low in range(5):
        assert f"DOI:10.1234/p{low}" not in round2_fetched
    # citation_count 5..29 (the top 25) must all have been promoted.
    for high in range(5, 30):
        assert f"DOI:10.1234/p{high}" in round2_fetched
    # All 30 are still kept in the corpus — discount/cap, never delete.
    assert len(result.kept) == 30


def test_fetch_budget_stops_walk_gracefully_with_distinct_stop_reason():
    """A never-saturating neighborhood would otherwise run indefinitely (up
    to the wave backstop); the fetch-budget is a HARD ceiling on total asta
    calls, independent of waves. Set a tiny budget + a huge wave cap so only
    the budget can be the thing that stops the walk."""
    adapter = _SpyAdapter(
        fwd_by_pid={
            "DOI:10.1234/seed": [_hit("Paper A distinct topic alpha", doi="10.1234/a",
                                    abstract="distinct alpha topic content")],
            "DOI:10.1234/a": [_hit("Paper B distinct topic beta", doi="10.1234/b",
                                 abstract="distinct beta topic content")],
        },
    )
    result = run_snowball_to_saturation(
        ["10.1234/seed"], adapter=adapter, backstop_waves=100, fetch_budget=3,
    )
    assert result.stop_reason == "budget:3-calls"
    # Never exceeds the budget (2 calls in round 1, budget hit mid-round 2).
    total_calls = len(adapter.cited_by_ids) + len(adapter.references_ids)
    assert total_calls <= 3
    assert len(result.rounds) == 2  # round 1 complete, round 2 truncated by budget


def test_fetch_budget_stop_reason_never_exceeds_configured_value():
    adapter = _SpyAdapter()
    result = run_snowball_to_saturation(
        ["10.1234/seed"], adapter=adapter, backstop_waves=5, fetch_budget=1,
    )
    assert result.stop_reason == "budget:1-calls"


def test_backstop_waves_default_is_2():
    assert __import__(
        "research_vault.sources.snowball", fromlist=["DEFAULT_BACKSTOP_WAVES"]
    ).DEFAULT_BACKSTOP_WAVES == 2

    adapter = _SpyAdapter(
        fwd_by_pid={
            "DOI:10.1234/seed": [_hit("Paper A distinct topic alpha", doi="10.1234/a",
                                    abstract="distinct alpha topic content")],
            "DOI:10.1234/a": [_hit("Paper B distinct topic beta", doi="10.1234/b",
                                 abstract="distinct beta topic content")],
        },
    )
    # Never-saturating (each round finds a genuinely new independent paper)
    # -> hits the DEFAULT wave cap (no explicit backstop_waves passed).
    result = run_snowball_to_saturation(["10.1234/seed"], adapter=adapter)
    assert result.stop_reason == "backstop:2-waves"
    assert len(result.rounds) == 2


def test_resume_carries_over_total_calls_and_respects_budget(tmp_path):
    """The checkpoint must persist the running fetch-count so a resumed walk
    doesn't reset it to 0 and blow past the budget across the resume
    boundary."""
    seed = "10.1234/seed"
    ckpt = tmp_path / "_snowball_checkpoint.json"

    class _KillOnSecondCitedBy(_SpyAdapter):
        def cited_by(self, paper_id, *, limit=20):
            self.cited_by_ids.append(paper_id)
            if len(self.cited_by_ids) == 2:
                raise KeyboardInterrupt("simulated process kill")
            return self.fwd_by_pid.get(paper_id, [])

    kill_adapter = _KillOnSecondCitedBy(
        fwd_by_pid={"DOI:10.1234/seed": [_hit("Paper A", doi="10.1234/a")]},
    )
    with pytest.raises(KeyboardInterrupt):
        run_snowball_to_saturation(
            [seed], adapter=kill_adapter, backstop_waves=100,
            fetch_budget=3, checkpoint_path=ckpt,
        )
    # Round 1 completed (2 calls: cited_by + references on the seed);
    # checkpoint persists total_calls == 2.
    data = json.loads(ckpt.read_text(encoding="utf-8"))
    assert data["total_calls"] == 2

    resume_adapter = _SpyAdapter()
    result = run_snowball_to_saturation(
        [seed], adapter=resume_adapter, backstop_waves=100,
        fetch_budget=3, checkpoint_path=ckpt,
    )
    # Only ONE more call is permitted before the budget (3) is hit —
    # never re-counts from 0 (which would allow 3 MORE calls, total 5).
    resume_total = len(resume_adapter.cited_by_ids) + len(resume_adapter.references_ids)
    assert resume_total == 1
    assert result.stop_reason == "budget:3-calls"


def test_budget_stop_reason_is_fail_closed_not_saturated_at_coverage_gate():
    """Confirms the coverage-gate whitelist treats a distinct
    ``budget:N-calls`` stop_reason exactly like any other non-canonical
    value — HALT-DECLARE, never a silent GO. ``is_backstop`` must be False
    (it does not start with ``backstop:``)."""
    from research_vault.review import autonomy as auto

    info = {
        "exists": True, "stop_reason": "budget:200-calls",
        "is_backstop": False, "wave_count": None,
    }
    result = auto.classify_coverage_gate(info)
    assert result.disposition == auto.HALT_DECLARE
