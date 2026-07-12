"""test_review_walk_terminal.py — citation-neighbor relevance walk terminal
acceptance tests (0.3.1; renamed from test_review_saturation_backstop.py).

The review-snowball loop is now a DEPTH-BOUNDED citation-neighbor relevance
walk (corpus = the vetted core plus its immediate citation neighborhood,
default 1 hop, configurable via ``relevance_hops``). This replaces the old
saturation-gated snowball (2-consecutive-zero primary rule + a wave-count
backstop) — depth-bounding makes an additive "backstop" auto-re-expansion
contradictory, so the coverage-gate REMEDIATE machinery was deleted along
with it (see ``review/remediation.py``'s module docstring).

Coverage:
  1. get_relevance_hops (review/style.py) — config seam
     1a. no config → default 1
     1b. config override (positive int) → override value
     1c. config override invalid (non-int / 0 / negative / bool) → falls back to default
  2. check_walk_terminal (review/__init__.py) — stop_reason parsing
     2a. missing file → exists False, walk_complete False
     2b. stop_reason: walk-complete:3-hops → walk_complete True, hop_count 3
     2c. stop_reason: neighborhood-exhausted → walk_complete False
     2d. no stop_reason field → stop_reason "", walk_complete False (never fabricated)
  3. cmd_approve wiring at "coverage-gate" (real DAG path, non-vacuous)
     3a. walk-complete:N-hops / neighborhood-exhausted → no SIGNAL, approval succeeds
     3b. budget-terminated + _coverage-gaps.md present → SIGNAL printed,
         approval still succeeds (non-blocking — an escape hatch, not a failure)
     3c. budget-terminated + _coverage-gaps.md MISSING → an ADDITIONAL
         SIGNAL flags the missing residue note
     3d. --reject bypasses the surfacing entirely (still succeeds as blocked)
  4. review_curate_tips prose documents the walk-budget residue discipline
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# 1. get_relevance_hops — config seam
# ---------------------------------------------------------------------------

class _FakeConfig:
    def __init__(self, raw: dict):
        self._raw = raw


class TestGetRelevanceHops:
    def test_no_config_returns_default(self):
        from research_vault.review.style import (
            get_relevance_hops,
            DEFAULT_RELEVANCE_HOPS,
        )
        assert get_relevance_hops(None) == DEFAULT_RELEVANCE_HOPS
        assert DEFAULT_RELEVANCE_HOPS == 1

    def test_config_override_positive_int(self):
        from research_vault.review.style import get_relevance_hops
        cfg = _FakeConfig({"review_style": {"relevance_hops": 5}})
        assert get_relevance_hops(cfg) == 5

    def test_config_override_non_int_falls_back(self):
        from research_vault.review.style import (
            get_relevance_hops,
            DEFAULT_RELEVANCE_HOPS,
        )
        cfg = _FakeConfig({"review_style": {"relevance_hops": "five"}})
        assert get_relevance_hops(cfg) == DEFAULT_RELEVANCE_HOPS

    def test_config_override_zero_falls_back(self):
        from research_vault.review.style import (
            get_relevance_hops,
            DEFAULT_RELEVANCE_HOPS,
        )
        cfg = _FakeConfig({"review_style": {"relevance_hops": 0}})
        assert get_relevance_hops(cfg) == DEFAULT_RELEVANCE_HOPS

    def test_config_override_negative_falls_back(self):
        from research_vault.review.style import (
            get_relevance_hops,
            DEFAULT_RELEVANCE_HOPS,
        )
        cfg = _FakeConfig({"review_style": {"relevance_hops": -1}})
        assert get_relevance_hops(cfg) == DEFAULT_RELEVANCE_HOPS

    def test_config_override_bool_falls_back(self):
        """bool is a subclass of int in Python — must be explicitly excluded."""
        from research_vault.review.style import (
            get_relevance_hops,
            DEFAULT_RELEVANCE_HOPS,
        )
        cfg = _FakeConfig({"review_style": {"relevance_hops": True}})
        assert get_relevance_hops(cfg) == DEFAULT_RELEVANCE_HOPS

    def test_no_override_section_returns_default(self):
        from research_vault.review.style import (
            get_relevance_hops,
            DEFAULT_RELEVANCE_HOPS,
        )
        cfg = _FakeConfig({})
        assert get_relevance_hops(cfg) == DEFAULT_RELEVANCE_HOPS

    def test_deprecated_legacy_key_accepted_with_warning(self):
        """One-release back-compat (0.3.1): the legacy
        ``saturation_backstop_waves`` config key is accepted as a deprecated
        alias when ``relevance_hops`` is absent."""
        from research_vault.review.style import get_relevance_hops
        cfg = _FakeConfig({"review_style": {"saturation_backstop_waves": 4}})
        with pytest.warns(DeprecationWarning):
            assert get_relevance_hops(cfg) == 4

    def test_relevance_hops_wins_over_legacy_key(self):
        from research_vault.review.style import get_relevance_hops
        cfg = _FakeConfig({"review_style": {
            "relevance_hops": 2, "saturation_backstop_waves": 4,
        }})
        assert get_relevance_hops(cfg) == 2


# ---------------------------------------------------------------------------
# 2. check_walk_terminal — stop_reason parsing
# ---------------------------------------------------------------------------

def _walk_note(path: Path, *, stop_reason: str | None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if stop_reason is None:
        fm = ""
    else:
        fm = f"stop_reason: {stop_reason}\n"
    path.write_text(
        f"---\n{fm}---\n\n"
        "## Citation-neighbor relevance walk\n\n"
        "| Hop | New (forward) | New (backward) | New independent | Cumulative | Direction-starved |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | 4 | 2 | 5 | 6 | |\n",
        encoding="utf-8",
    )
    return path


class TestCheckWalkTerminal:
    def test_missing_file(self, tmp_path):
        from research_vault.review import check_walk_terminal
        info = check_walk_terminal(tmp_path / "nope" / "_walk.md")
        assert info["exists"] is False
        assert info["walk_complete"] is False
        assert info["stop_reason"] == ""
        assert info["hop_count"] is None

    def test_walk_complete_stop_reason(self, tmp_path):
        from research_vault.review import check_walk_terminal
        p = _walk_note(tmp_path / "_walk.md", stop_reason="walk-complete:3-hops")
        info = check_walk_terminal(p)
        assert info["exists"] is True
        assert info["walk_complete"] is True
        assert info["stop_reason"] == "walk-complete:3-hops"
        assert info["hop_count"] == 3

    def test_neighborhood_exhausted_stop_reason(self, tmp_path):
        from research_vault.review import check_walk_terminal
        p = _walk_note(tmp_path / "_walk.md", stop_reason="neighborhood-exhausted")
        info = check_walk_terminal(p)
        assert info["exists"] is True
        assert info["walk_complete"] is False
        assert info["stop_reason"] == "neighborhood-exhausted"
        assert info["hop_count"] is None

    def test_budget_stop_reason(self, tmp_path):
        from research_vault.review import check_walk_terminal
        p = _walk_note(tmp_path / "_walk.md", stop_reason="budget:200-calls")
        info = check_walk_terminal(p)
        assert info["exists"] is True
        assert info["walk_complete"] is False
        assert info["stop_reason"] == "budget:200-calls"

    def test_missing_stop_reason_field_never_fabricated_as_complete(self, tmp_path):
        from research_vault.review import check_walk_terminal
        p = _walk_note(tmp_path / "_walk.md", stop_reason=None)
        info = check_walk_terminal(p)
        assert info["exists"] is True
        assert info["stop_reason"] == ""
        assert info["walk_complete"] is False

    def test_deprecated_alias_is_the_same_function(self):
        from research_vault.review import check_saturation_backstop, check_walk_terminal
        assert check_saturation_backstop is check_walk_terminal


# ---------------------------------------------------------------------------
# 2b. check_source_coverage — dark-source × declared-sources cross-check
# (pre-publish hardening batch, 2026-07-09 downstream e2e-run finding)
# ---------------------------------------------------------------------------

def _search_hits_note(path: Path, *, dark_sources: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ndark_sources: {', '.join(dark_sources)}\n---\n\n# Search hits\n",
        encoding="utf-8",
    )
    return path


def _protocol_note(path: Path, *, sources: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        'type: review-protocol\n'
        'question: "Does X improve Y?"\n'
        "seed_queries:\n"
        '  by-method: "q1"\n'
        f"sources: [{', '.join(sources)}]\n"
        "---\n",
        encoding="utf-8",
    )
    return path


class TestCheckSourceCoverage:
    def test_missing_search_hits_file(self, tmp_path):
        from research_vault.review import check_source_coverage
        info = check_source_coverage(tmp_path / "nope" / "_search_hits.md", tmp_path / "_protocol.md")
        assert info["exists"] is False
        assert info["dark_sources"] == []
        assert info["declared_dark"] == []

    def test_declared_dark_source_flagged(self, tmp_path):
        from research_vault.review import check_source_coverage
        hits = _search_hits_note(tmp_path / "_search_hits.md", dark_sources=["arxiv"])
        protocol = _protocol_note(tmp_path / "_protocol.md", sources=["semantic-scholar", "arxiv", "openalex"])
        info = check_source_coverage(hits, protocol)
        assert info["exists"] is True
        assert info["dark_sources"] == ["arxiv"]
        assert info["declared_dark"] == ["arxiv"]

    def test_dark_but_undeclared_source_not_flagged(self, tmp_path):
        """A source dark this sweep but NEVER in the protocol's declared
        `sources:` list must not be flagged — nothing was promised for it."""
        from research_vault.review import check_source_coverage
        hits = _search_hits_note(tmp_path / "_search_hits.md", dark_sources=["pubmed"])
        protocol = _protocol_note(tmp_path / "_protocol.md", sources=["semantic-scholar", "arxiv"])
        info = check_source_coverage(hits, protocol)
        assert info["dark_sources"] == ["pubmed"]
        assert info["declared_dark"] == []

    def test_no_dark_sources(self, tmp_path):
        from research_vault.review import check_source_coverage
        hits = _search_hits_note(tmp_path / "_search_hits.md", dark_sources=[])
        protocol = _protocol_note(tmp_path / "_protocol.md", sources=["arxiv"])
        info = check_source_coverage(hits, protocol)
        assert info["dark_sources"] == []
        assert info["declared_dark"] == []

    def test_missing_protocol_defaults_to_no_declared_sources(self, tmp_path):
        """A missing `_protocol.md` must never crash — no declared sources
        means nothing can be cross-checked as "declared dark"."""
        from research_vault.review import check_source_coverage
        hits = _search_hits_note(tmp_path / "_search_hits.md", dark_sources=["arxiv"])
        info = check_source_coverage(hits, tmp_path / "nope_protocol.md")
        assert info["dark_sources"] == ["arxiv"]
        assert info["declared_dark"] == []


# ---------------------------------------------------------------------------
# 3. cmd_approve wiring at "coverage-gate" — real DAG path
# ---------------------------------------------------------------------------

def _cfg_file(tmp_path: Path) -> Path:
    f = tmp_path / "research_vault.toml"
    f.write_text(
        f'instance_root = "{tmp_path}"\n'
        f'notes_root = "{tmp_path / "notes"}"\n'
        f'state_dir = "{tmp_path / "state"}"\n'
        f'agents_dir = "{tmp_path / ".agents"}"\n'
        f'tasks_dir = "{tmp_path / "tasks"}"\n'
        f'control_dir = "{tmp_path / "control"}"\n'
        '[adapters]\nnotifier = "file"\nbackend = "local"\nsecrets = "env"\n'
        '[approval]\nenforce = true\n'
        'token_fingerprint = "d309a810bb5f40cef518202e46d197aa61e4dddafc5984c8c698da29ac8fd2bc"\n'
        'enforce_sig = ""\n',
        encoding="utf-8",
    )
    return f


def _set_run_env(tmp_path: Path):
    cfg_file = _cfg_file(tmp_path)
    old = os.environ.get("RESEARCH_VAULT_CONFIG")
    os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_file)
    return old


def _restore_env(old):
    if old is None:
        os.environ.pop("RESEARCH_VAULT_CONFIG", None)
    else:
        os.environ["RESEARCH_VAULT_CONFIG"] = old


def _coverage_gate_manifest(run_id: str, walk_path: Path) -> dict:
    """Minimal manifest with the real review-snowball → coverage-gate shape (§5L.1)."""
    return {
        "run_id": run_id,
        "name": "test review",
        "global_cap": 1,
        "nodes": [
            {
                "id": "review-snowball",
                "type": "agent",
                "spec": "task://demo#snowball",
                "produces": {"_walk.md": str(walk_path)},
                "needs": [],
            },
            {
                "id": "coverage-gate",
                "type": "human-go",
                "label": "Gate 2",
                "needs": [{"from": "review-snowball", "edge": "afterok"}],
            },
        ],
    }


def _make_awaiting_run(tmp_path: Path, run_id: str, walk_path: Path):
    from research_vault.dag.store import RunState, RunStore

    manifest = _coverage_gate_manifest(run_id, walk_path)
    manifest_path = tmp_path / f"{run_id}-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    store = RunStore(tmp_path / "state")
    rs = RunState(run_id=run_id, manifest_path=str(manifest_path))
    rs.init_nodes(manifest)
    rs.set_node_status("review-snowball", "succeeded")
    rs.set_node_status("coverage-gate", "awaiting-go")
    store.create(rs)
    return store


def _coverage_gate_manifest_with_search(
    run_id: str, walk_path: Path, search_hits_path: Path,
) -> dict:
    """Same shape as `_coverage_gate_manifest`, plus the real `review-search`
    node — needed to exercise the source-coverage fail-closed wiring, which
    reads `_search_hits.md` off `nodes_lookup["review-search"]`."""
    manifest = _coverage_gate_manifest(run_id, walk_path)
    manifest["nodes"].insert(0, {
        "id": "review-search",
        "type": "tool",
        "op": "sweep",
        "produces": {"_search_hits.md": str(search_hits_path)},
        "needs": [],
    })
    return manifest


def _make_awaiting_run_with_search(
    tmp_path: Path, run_id: str, walk_path: Path, search_hits_path: Path,
):
    from research_vault.dag.store import RunState, RunStore

    manifest = _coverage_gate_manifest_with_search(run_id, walk_path, search_hits_path)
    manifest_path = tmp_path / f"{run_id}-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    store = RunStore(tmp_path / "state")
    rs = RunState(run_id=run_id, manifest_path=str(manifest_path))
    rs.init_nodes(manifest)
    rs.set_node_status("review-search", "succeeded")
    rs.set_node_status("review-snowball", "succeeded")
    rs.set_node_status("coverage-gate", "awaiting-go")
    store.create(rs)
    return store


class TestApproveCoverageGateSourceDark:
    """cmd_approve wiring (manual, non-auto path): a source declared in the
    protocol's `sources:` list that went DARK this sweep must BLOCK
    approval outright — never a mere SIGNAL (pre-publish hardening batch,
    2026-07-09 downstream e2e-run finding)."""

    def test_declared_dark_source_blocks_approval(self, tmp_path, capsys):
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            review_dir = tmp_path / "reviews" / "scope-dark"
            walk_path = review_dir / "_walk.md"
            search_hits_path = review_dir / "_search_hits.md"
            _walk_note(walk_path, stop_reason="walk-complete:1-hops")
            _search_hits_note(search_hits_path, dark_sources=["arxiv"])
            _protocol_note(review_dir / "_protocol.md", sources=["semantic-scholar", "arxiv"])
            store = _make_awaiting_run_with_search(
                tmp_path, "review-dark", walk_path, search_hits_path,
            )

            args = argparse.Namespace(run_id="review-dark", node_id="coverage-gate")
            rc = cmd_approve(args)
            captured = capsys.readouterr()

            assert rc == 1, "a declared-dark source must BLOCK, not just signal"
            assert "BLOCKED" in captured.err
            assert "arxiv" in captured.err
            rs = store.load("review-dark")
            assert rs.node_status("coverage-gate") == "awaiting-go", "must NOT have advanced past the gate"
        finally:
            _restore_env(old)

    def test_dark_but_undeclared_source_does_not_block(self, tmp_path, capsys):
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            review_dir = tmp_path / "reviews" / "scope-ok"
            walk_path = review_dir / "_walk.md"
            search_hits_path = review_dir / "_search_hits.md"
            _walk_note(walk_path, stop_reason="walk-complete:1-hops")
            _search_hits_note(search_hits_path, dark_sources=["pubmed"])  # never declared
            _protocol_note(review_dir / "_protocol.md", sources=["semantic-scholar", "arxiv"])
            store = _make_awaiting_run_with_search(
                tmp_path, "review-ok", walk_path, search_hits_path,
            )

            args = argparse.Namespace(run_id="review-ok", node_id="coverage-gate")
            rc = cmd_approve(args)
            captured = capsys.readouterr()

            assert rc == 0
            assert "BLOCKED" not in captured.err
            rs = store.load("review-ok")
            assert rs.node_status("coverage-gate") == "succeeded"
        finally:
            _restore_env(old)


class TestApproveCoverageGateWalkTerminalSurfacing:
    def test_walk_complete_no_signal(self, tmp_path, capsys):
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            walk_path = tmp_path / "reviews" / "scope-a" / "_walk.md"
            _walk_note(walk_path, stop_reason="walk-complete:1-hops")
            store = _make_awaiting_run(tmp_path, "review-walk-complete", walk_path)

            args = argparse.Namespace(run_id="review-walk-complete", node_id="coverage-gate")
            rc = cmd_approve(args)
            captured = capsys.readouterr()

            assert rc == 0
            assert "budget-terminated" not in captured.err
            rs = store.load("review-walk-complete")
            assert rs.node_status("coverage-gate") == "succeeded"
        finally:
            _restore_env(old)

    def test_neighborhood_exhausted_no_signal(self, tmp_path, capsys):
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            walk_path = tmp_path / "reviews" / "scope-ne" / "_walk.md"
            _walk_note(walk_path, stop_reason="neighborhood-exhausted")
            store = _make_awaiting_run(tmp_path, "review-neighborhood-exhausted", walk_path)

            args = argparse.Namespace(run_id="review-neighborhood-exhausted", node_id="coverage-gate")
            rc = cmd_approve(args)
            captured = capsys.readouterr()

            assert rc == 0
            assert captured.err == ""
            rs = store.load("review-neighborhood-exhausted")
            assert rs.node_status("coverage-gate") == "succeeded"
        finally:
            _restore_env(old)

    def test_budget_terminated_with_residue_note_signals_but_succeeds(self, tmp_path, capsys):
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            review_dir = tmp_path / "reviews" / "scope-b"
            walk_path = review_dir / "_walk.md"
            _walk_note(walk_path, stop_reason="budget:200-calls")
            (review_dir / "_coverage-gaps.md").write_text(
                "terminated by the total-fetch budget; corpus is bounded, not depth-complete.\n",
                encoding="utf-8",
            )
            store = _make_awaiting_run(tmp_path, "review-budget-ok", walk_path)

            args = argparse.Namespace(run_id="review-budget-ok", node_id="coverage-gate")
            rc = cmd_approve(args)
            captured = capsys.readouterr()

            assert rc == 0, "budget-termination is non-blocking — must still succeed"
            assert "budget-terminated" in captured.err
            assert "_coverage-gaps.md" in captured.err
            # residue note exists — must NOT ALSO get the "missing residue note" signal
            assert "residue note is REQUIRED" not in captured.err

            rs = store.load("review-budget-ok")
            assert rs.node_status("coverage-gate") == "succeeded"
        finally:
            _restore_env(old)

    def test_budget_terminated_missing_residue_note_extra_signal(self, tmp_path, capsys):
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            review_dir = tmp_path / "reviews" / "scope-c"
            walk_path = review_dir / "_walk.md"
            _walk_note(walk_path, stop_reason="budget:200-calls")
            # deliberately do NOT write _coverage-gaps.md
            store = _make_awaiting_run(tmp_path, "review-budget-missing", walk_path)

            args = argparse.Namespace(run_id="review-budget-missing", node_id="coverage-gate")
            rc = cmd_approve(args)
            captured = capsys.readouterr()

            assert rc == 0
            assert "budget-terminated" in captured.err
            assert "residue note is REQUIRED" in captured.err

            rs = store.load("review-budget-missing")
            assert rs.node_status("coverage-gate") == "succeeded"
        finally:
            _restore_env(old)

    def test_missing_stop_reason_signals_ambiguity(self, tmp_path, capsys):
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            walk_path = tmp_path / "reviews" / "scope-d" / "_walk.md"
            _walk_note(walk_path, stop_reason=None)
            store = _make_awaiting_run(tmp_path, "review-no-reason", walk_path)

            args = argparse.Namespace(run_id="review-no-reason", node_id="coverage-gate")
            rc = cmd_approve(args)
            captured = capsys.readouterr()

            assert rc == 0
            assert "not a recognized citation-neighbor walk terminal" in captured.err

            rs = store.load("review-no-reason")
            assert rs.node_status("coverage-gate") == "succeeded"
        finally:
            _restore_env(old)

    def test_reject_bypasses_surfacing(self, tmp_path, capsys):
        """--reject is the explicit abandon path — it must not be blocked, and
        the budget-terminal signal is not relevant to an already-abandoned gate."""
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            review_dir = tmp_path / "reviews" / "scope-e"
            walk_path = review_dir / "_walk.md"
            _walk_note(walk_path, stop_reason="budget:200-calls")
            store = _make_awaiting_run(tmp_path, "review-budget-reject", walk_path)

            args = argparse.Namespace(
                run_id="review-budget-reject", node_id="coverage-gate", reject=True
            )
            rc = cmd_approve(args)

            assert rc == 0
            rs = store.load("review-budget-reject")
            assert rs.node_status("coverage-gate") == "blocked"
        finally:
            _restore_env(old)


# ---------------------------------------------------------------------------
# 3b. Non-canonical stop_reason sweep — the M3 fail-open regression guard
#     (independent reviewer, PR #175 delta), retargeted at the 0.3.1 vocab: a
#     BLACKLIST that only recognizes known-bad prefixes fails OPEN on every
#     other spelling — those used to sail through SILENTLY, looking identical
#     to a genuine clean terminal at the gate. The fix is a WHITELIST: only
#     the two clean terminals (``walk-complete:N-hops``/
#     ``neighborhood-exhausted``) may stay silent; every other value (empty,
#     malformed, a legacy ``saturated``/``backstop:N-waves`` string, garbage)
#     must trip the loud SIGNAL.
# ---------------------------------------------------------------------------

class TestNonCanonicalStopReasonSweep:
    @pytest.mark.parametrize(
        "stop_reason",
        [
            "walk-complete-3-hops",     # dash instead of colon
            "walk complete after 3 hops",  # free prose
            "walk-complete",            # bare, no hop count
            "saturated",                # legacy pre-0.3.1 vocab
            "backstop:3-waves",         # legacy pre-0.3.1 vocab
            "terminated by hop cap",    # unrelated prose describing the same event
            "garbage-token-xyz",        # pure garbage
        ],
    )
    def test_non_canonical_stop_reason_trips_loud_signal(self, tmp_path, capsys, stop_reason):
        """Every non-whitelisted value must trip the loud catch-all SIGNAL —
        never a silent pass. This is the M3 fail-open regression guard,
        retargeted at the 0.3.1 vocab."""
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            run_id = f"review-sweep-{abs(hash(stop_reason))}"
            walk_path = tmp_path / "reviews" / f"scope-{abs(hash(stop_reason))}" / "_walk.md"
            _walk_note(walk_path, stop_reason=stop_reason)
            store = _make_awaiting_run(tmp_path, run_id, walk_path)

            args = argparse.Namespace(run_id=run_id, node_id="coverage-gate")
            rc = cmd_approve(args)
            captured = capsys.readouterr()

            assert rc == 0, "surfacing is non-blocking — approval still proceeds"
            assert captured.err.strip() != "", (
                f"stop_reason={stop_reason!r} sailed through with NO signal at all — "
                "fail-open regression (M3 class)"
            )
            assert "SIGNAL" in captured.err, (
                f"stop_reason={stop_reason!r} produced output but not a SIGNAL — "
                f"got: {captured.err!r}"
            )

            rs = store.load(run_id)
            assert rs.node_status("coverage-gate") == "succeeded"
        finally:
            _restore_env(old)

    @pytest.mark.parametrize("stop_reason", ["walk-complete:1-hops", "neighborhood-exhausted"])
    def test_whitelisted_clean_terminals_stay_silent(self, tmp_path, capsys, stop_reason):
        """The two values permitted to stay silent: the exact canonical
        strings ``walk-complete:N-hops`` and ``neighborhood-exhausted``."""
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            run_id = f"review-clean-{abs(hash(stop_reason))}"
            walk_path = tmp_path / "reviews" / f"scope-clean-{abs(hash(stop_reason))}" / "_walk.md"
            _walk_note(walk_path, stop_reason=stop_reason)
            store = _make_awaiting_run(tmp_path, run_id, walk_path)

            args = argparse.Namespace(run_id=run_id, node_id="coverage-gate")
            rc = cmd_approve(args)
            captured = capsys.readouterr()

            assert rc == 0
            assert captured.err == "", (
                f"{stop_reason!r} must stay silent at the gate; got: {captured.err!r}"
            )

            rs = store.load(run_id)
            assert rs.node_status("coverage-gate") == "succeeded"
        finally:
            _restore_env(old)


# ---------------------------------------------------------------------------
# 4. review_curate_tips prose documents the walk-budget residue discipline
#    (0.3.1: the residue-note discipline moved to the budget-termination
#    case, replacing the old backstop-termination framing)
# ---------------------------------------------------------------------------

class TestReviewCurateTipsDocumentsWalkBudget:
    def test_tips_mention_budget_config_and_stop_reason(self):
        from research_vault.review.style import get_review_tips
        tips = get_review_tips(config=None)
        curate = tips["review_curate_tips"]
        assert "stop_reason" in curate
        assert "_coverage-gaps.md" in curate
        assert "budget" in curate.lower()
        assert "walk-complete" in curate
        assert "neighborhood-exhausted" in curate
