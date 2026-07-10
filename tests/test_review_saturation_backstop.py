"""test_review_saturation_backstop.py — SR-LR-1-BACKSTOP acceptance tests.

The review-snowball loop's PRIMARY saturation rule (2-consecutive-zero
rounds, §5L.2) is principled but not guaranteed to converge — an
exploding-intersection review question (every wave finds more) can run it
unboundedly. This adds HyperResearch's termination-guarantee backstop
ADDITIVELY: the primary rule is unchanged and preferred; the backstop only
fires when the primary rule doesn't converge within
``saturation_backstop_waves`` rounds (default 3).

Coverage:
  1. get_saturation_backstop_waves (review/style.py) — config seam
     1a. no config → default 3
     1b. config override (positive int) → override value
     1c. config override invalid (non-int / 0 / negative / bool) → falls back to default
  2. check_saturation_backstop (review/__init__.py) — stop_reason parsing
     2a. missing file → exists False, is_backstop False
     2b. stop_reason: saturated → is_backstop False
     2c. stop_reason: backstop:3-waves → is_backstop True, wave_count 3
     2d. no stop_reason field → stop_reason "", is_backstop False (never fabricated)
  3. cmd_approve wiring at "coverage-gate" (real DAG path, non-vacuous)
     3a. saturated → no backstop SIGNAL printed, approval succeeds
     3b. backstop-terminated + _coverage-gaps.md present → SIGNAL printed,
         approval still succeeds (non-blocking — an escape hatch, not a failure)
     3c. backstop-terminated + _coverage-gaps.md MISSING → an ADDITIONAL
         SIGNAL flags the missing residue note
     3d. --reject bypasses the surfacing entirely (still succeeds as blocked)
  4. review_snowball_tips prose documents the backstop
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
# 1. get_saturation_backstop_waves — config seam
# ---------------------------------------------------------------------------

class _FakeConfig:
    def __init__(self, raw: dict):
        self._raw = raw


class TestGetSaturationBackstopWaves:
    def test_no_config_returns_default(self):
        from research_vault.review.style import (
            get_saturation_backstop_waves,
            DEFAULT_SATURATION_BACKSTOP_WAVES,
        )
        assert get_saturation_backstop_waves(None) == DEFAULT_SATURATION_BACKSTOP_WAVES
        assert DEFAULT_SATURATION_BACKSTOP_WAVES == 2

    def test_config_override_positive_int(self):
        from research_vault.review.style import get_saturation_backstop_waves
        cfg = _FakeConfig({"review_style": {"saturation_backstop_waves": 5}})
        assert get_saturation_backstop_waves(cfg) == 5

    def test_config_override_non_int_falls_back(self):
        from research_vault.review.style import (
            get_saturation_backstop_waves,
            DEFAULT_SATURATION_BACKSTOP_WAVES,
        )
        cfg = _FakeConfig({"review_style": {"saturation_backstop_waves": "five"}})
        assert get_saturation_backstop_waves(cfg) == DEFAULT_SATURATION_BACKSTOP_WAVES

    def test_config_override_zero_falls_back(self):
        from research_vault.review.style import (
            get_saturation_backstop_waves,
            DEFAULT_SATURATION_BACKSTOP_WAVES,
        )
        cfg = _FakeConfig({"review_style": {"saturation_backstop_waves": 0}})
        assert get_saturation_backstop_waves(cfg) == DEFAULT_SATURATION_BACKSTOP_WAVES

    def test_config_override_negative_falls_back(self):
        from research_vault.review.style import (
            get_saturation_backstop_waves,
            DEFAULT_SATURATION_BACKSTOP_WAVES,
        )
        cfg = _FakeConfig({"review_style": {"saturation_backstop_waves": -1}})
        assert get_saturation_backstop_waves(cfg) == DEFAULT_SATURATION_BACKSTOP_WAVES

    def test_config_override_bool_falls_back(self):
        """bool is a subclass of int in Python — must be explicitly excluded."""
        from research_vault.review.style import (
            get_saturation_backstop_waves,
            DEFAULT_SATURATION_BACKSTOP_WAVES,
        )
        cfg = _FakeConfig({"review_style": {"saturation_backstop_waves": True}})
        assert get_saturation_backstop_waves(cfg) == DEFAULT_SATURATION_BACKSTOP_WAVES

    def test_no_override_section_returns_default(self):
        from research_vault.review.style import (
            get_saturation_backstop_waves,
            DEFAULT_SATURATION_BACKSTOP_WAVES,
        )
        cfg = _FakeConfig({})
        assert get_saturation_backstop_waves(cfg) == DEFAULT_SATURATION_BACKSTOP_WAVES


# ---------------------------------------------------------------------------
# 2. check_saturation_backstop — stop_reason parsing
# ---------------------------------------------------------------------------

def _saturation_note(path: Path, *, stop_reason: str | None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if stop_reason is None:
        fm = ""
    else:
        fm = f"stop_reason: {stop_reason}\n"
    path.write_text(
        f"---\n{fm}---\n\n"
        "## Saturation curve\n\n"
        "| round | new_citekeys_forward | new_citekeys_backward | new_concept_tags | cumulative_corpus |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 1 | 4 | 2 | 1 | 6 |\n",
        encoding="utf-8",
    )
    return path


class TestCheckSaturationBackstop:
    def test_missing_file(self, tmp_path):
        from research_vault.review import check_saturation_backstop
        info = check_saturation_backstop(tmp_path / "nope" / "_saturation.md")
        assert info["exists"] is False
        assert info["is_backstop"] is False
        assert info["stop_reason"] == ""
        assert info["wave_count"] is None

    def test_saturated_stop_reason(self, tmp_path):
        from research_vault.review import check_saturation_backstop
        p = _saturation_note(tmp_path / "_saturation.md", stop_reason="saturated")
        info = check_saturation_backstop(p)
        assert info["exists"] is True
        assert info["is_backstop"] is False
        assert info["stop_reason"] == "saturated"
        assert info["wave_count"] is None

    def test_backstop_stop_reason(self, tmp_path):
        from research_vault.review import check_saturation_backstop
        p = _saturation_note(tmp_path / "_saturation.md", stop_reason="backstop:3-waves")
        info = check_saturation_backstop(p)
        assert info["exists"] is True
        assert info["is_backstop"] is True
        assert info["stop_reason"] == "backstop:3-waves"
        assert info["wave_count"] == 3

    def test_missing_stop_reason_field_never_fabricated_as_saturated(self, tmp_path):
        from research_vault.review import check_saturation_backstop
        p = _saturation_note(tmp_path / "_saturation.md", stop_reason=None)
        info = check_saturation_backstop(p)
        assert info["exists"] is True
        assert info["stop_reason"] == ""
        assert info["is_backstop"] is False


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


def _coverage_gate_manifest(run_id: str, saturation_path: Path) -> dict:
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
                "produces": {"_saturation.md": str(saturation_path)},
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


def _make_awaiting_run(tmp_path: Path, run_id: str, saturation_path: Path):
    from research_vault.dag.store import RunState, RunStore

    manifest = _coverage_gate_manifest(run_id, saturation_path)
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
    run_id: str, saturation_path: Path, search_hits_path: Path,
) -> dict:
    """Same shape as `_coverage_gate_manifest`, plus the real `review-search`
    node — needed to exercise the source-coverage fail-closed wiring, which
    reads `_search_hits.md` off `nodes_lookup["review-search"]`."""
    manifest = _coverage_gate_manifest(run_id, saturation_path)
    manifest["nodes"].insert(0, {
        "id": "review-search",
        "type": "tool",
        "op": "sweep",
        "produces": {"_search_hits.md": str(search_hits_path)},
        "needs": [],
    })
    return manifest


def _make_awaiting_run_with_search(
    tmp_path: Path, run_id: str, saturation_path: Path, search_hits_path: Path,
):
    from research_vault.dag.store import RunState, RunStore

    manifest = _coverage_gate_manifest_with_search(run_id, saturation_path, search_hits_path)
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
            saturation_path = review_dir / "_saturation.md"
            search_hits_path = review_dir / "_search_hits.md"
            _saturation_note(saturation_path, stop_reason="saturated")
            _search_hits_note(search_hits_path, dark_sources=["arxiv"])
            _protocol_note(review_dir / "_protocol.md", sources=["semantic-scholar", "arxiv"])
            store = _make_awaiting_run_with_search(
                tmp_path, "review-dark", saturation_path, search_hits_path,
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
            saturation_path = review_dir / "_saturation.md"
            search_hits_path = review_dir / "_search_hits.md"
            _saturation_note(saturation_path, stop_reason="saturated")
            _search_hits_note(search_hits_path, dark_sources=["pubmed"])  # never declared
            _protocol_note(review_dir / "_protocol.md", sources=["semantic-scholar", "arxiv"])
            store = _make_awaiting_run_with_search(
                tmp_path, "review-ok", saturation_path, search_hits_path,
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


class TestApproveCoverageGateBackstopSurfacing:
    def test_saturated_no_backstop_signal(self, tmp_path, capsys):
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            saturation_path = tmp_path / "reviews" / "scope-a" / "_saturation.md"
            _saturation_note(saturation_path, stop_reason="saturated")
            store = _make_awaiting_run(tmp_path, "review-saturated", saturation_path)

            args = argparse.Namespace(run_id="review-saturated", node_id="coverage-gate")
            rc = cmd_approve(args)
            captured = capsys.readouterr()

            assert rc == 0
            assert "backstop-terminated" not in captured.err
            rs = store.load("review-saturated")
            assert rs.node_status("coverage-gate") == "succeeded"
        finally:
            _restore_env(old)

    def test_backstop_terminated_with_residue_note_signals_but_succeeds(self, tmp_path, capsys):
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            review_dir = tmp_path / "reviews" / "scope-b"
            saturation_path = review_dir / "_saturation.md"
            _saturation_note(saturation_path, stop_reason="backstop:3-waves")
            (review_dir / "_coverage-gaps.md").write_text(
                "terminated by backstop after 3 waves; corpus is bounded-not-saturated.\n",
                encoding="utf-8",
            )
            store = _make_awaiting_run(tmp_path, "review-backstop-ok", saturation_path)

            args = argparse.Namespace(run_id="review-backstop-ok", node_id="coverage-gate")
            rc = cmd_approve(args)
            captured = capsys.readouterr()

            assert rc == 0, "backstop-termination is non-blocking — must still succeed"
            assert "backstop-terminated" in captured.err
            assert "NOT saturated" in captured.err
            assert "_coverage-gaps.md" in captured.err
            # residue note exists — must NOT ALSO get the "missing residue note" signal
            assert "residue note is REQUIRED" not in captured.err

            rs = store.load("review-backstop-ok")
            assert rs.node_status("coverage-gate") == "succeeded"
        finally:
            _restore_env(old)

    def test_backstop_terminated_missing_residue_note_extra_signal(self, tmp_path, capsys):
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            review_dir = tmp_path / "reviews" / "scope-c"
            saturation_path = review_dir / "_saturation.md"
            _saturation_note(saturation_path, stop_reason="backstop:3-waves")
            # deliberately do NOT write _coverage-gaps.md
            store = _make_awaiting_run(tmp_path, "review-backstop-missing", saturation_path)

            args = argparse.Namespace(run_id="review-backstop-missing", node_id="coverage-gate")
            rc = cmd_approve(args)
            captured = capsys.readouterr()

            assert rc == 0
            assert "backstop-terminated" in captured.err
            assert "residue note is REQUIRED" in captured.err

            rs = store.load("review-backstop-missing")
            assert rs.node_status("coverage-gate") == "succeeded"
        finally:
            _restore_env(old)

    def test_missing_stop_reason_signals_ambiguity(self, tmp_path, capsys):
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            saturation_path = tmp_path / "reviews" / "scope-d" / "_saturation.md"
            _saturation_note(saturation_path, stop_reason=None)
            store = _make_awaiting_run(tmp_path, "review-no-reason", saturation_path)

            args = argparse.Namespace(run_id="review-no-reason", node_id="coverage-gate")
            rc = cmd_approve(args)
            captured = capsys.readouterr()

            assert rc == 0
            assert "not the exact string 'saturated'" in captured.err

            rs = store.load("review-no-reason")
            assert rs.node_status("coverage-gate") == "succeeded"
        finally:
            _restore_env(old)

    def test_reject_bypasses_surfacing(self, tmp_path, capsys):
        """--reject is the explicit abandon path — it must not be blocked, and
        the backstop signal is not relevant to an already-abandoned gate."""
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            review_dir = tmp_path / "reviews" / "scope-e"
            saturation_path = review_dir / "_saturation.md"
            _saturation_note(saturation_path, stop_reason="backstop:3-waves")
            store = _make_awaiting_run(tmp_path, "review-backstop-reject", saturation_path)

            args = argparse.Namespace(
                run_id="review-backstop-reject", node_id="coverage-gate", reject=True
            )
            rc = cmd_approve(args)

            assert rc == 0
            rs = store.load("review-backstop-reject")
            assert rs.node_status("coverage-gate") == "blocked"
        finally:
            _restore_env(old)


# ---------------------------------------------------------------------------
# 3b. Non-canonical stop_reason sweep — the M3 fail-open regression guard
#     (independent reviewer, PR #175 delta): a BLACKLIST that only recognizes the
#     literal ``backstop:`` prefix fails OPEN on every other spelling — those
#     used to sail through SILENTLY, looking identical to a genuine saturated
#     corpus at the gate. The fix is a WHITELIST: only the exact canonical
#     string ``saturated`` may stay silent; every other value (empty,
#     malformed backstop variants, garbage) must trip the loud SIGNAL.
# ---------------------------------------------------------------------------

class TestNonCanonicalStopReasonSweep:
    @pytest.mark.parametrize(
        "stop_reason",
        [
            "backstop-3-waves",        # dash instead of colon
            "backstop after 3 waves",  # free prose
            "backstop",                # bare, no wave count
            "terminated by wave cap",  # unrelated prose describing the same event
            "garbage-token-xyz",       # pure garbage
        ],
    )
    def test_non_canonical_stop_reason_trips_loud_signal(self, tmp_path, capsys, stop_reason):
        """Every non-'saturated' value must trip the loud catch-all SIGNAL —
        never a silent pass. This is the M3 fail-open regression guard: a
        blacklist that only recognized the literal 'backstop:' prefix let all
        of these sail through silently before the fix."""
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            run_id = f"review-sweep-{abs(hash(stop_reason))}"
            saturation_path = tmp_path / "reviews" / f"scope-{abs(hash(stop_reason))}" / "_saturation.md"
            _saturation_note(saturation_path, stop_reason=stop_reason)
            store = _make_awaiting_run(tmp_path, run_id, saturation_path)

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

    def test_exact_saturated_stays_silent(self, tmp_path, capsys):
        """The ONLY value permitted to stay silent: the exact canonical string
        'saturated' (case/whitespace-insensitively, since cmd_approve compares
        via .strip().lower())."""
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            saturation_path = tmp_path / "reviews" / "scope-canonical" / "_saturation.md"
            _saturation_note(saturation_path, stop_reason="saturated")
            store = _make_awaiting_run(tmp_path, "review-canonical-sat", saturation_path)

            args = argparse.Namespace(run_id="review-canonical-sat", node_id="coverage-gate")
            rc = cmd_approve(args)
            captured = capsys.readouterr()

            assert rc == 0
            assert captured.err == "", (
                f"exact 'saturated' must stay silent at the gate; got: {captured.err!r}"
            )

            rs = store.load("review-canonical-sat")
            assert rs.node_status("coverage-gate") == "succeeded"
        finally:
            _restore_env(old)

    @pytest.mark.parametrize("stop_reason", ["Saturated", " saturated ", "SATURATED"])
    def test_canonical_normalization_tolerates_case_and_whitespace(
        self, tmp_path, capsys, stop_reason
    ):
        """The whitelist compares via .strip().lower() — case and surrounding
        whitespace around the canonical word are tolerated (still silent);
        this is deliberate normalization, not a fail-open hole, since the only
        thing being tolerated is the exact same word under trivial formatting."""
        from research_vault.dag.verbs import cmd_approve

        old = _set_run_env(tmp_path)
        try:
            run_id = f"review-norm-{abs(hash(stop_reason))}"
            saturation_path = (
                tmp_path / "reviews" / f"scope-norm-{abs(hash(stop_reason))}" / "_saturation.md"
            )
            _saturation_note(saturation_path, stop_reason=stop_reason)
            store = _make_awaiting_run(tmp_path, run_id, saturation_path)

            args = argparse.Namespace(run_id=run_id, node_id="coverage-gate")
            rc = cmd_approve(args)
            captured = capsys.readouterr()

            assert rc == 0
            assert captured.err == ""

            rs = store.load(run_id)
            assert rs.node_status("coverage-gate") == "succeeded"
        finally:
            _restore_env(old)


# ---------------------------------------------------------------------------
# 4. review_curate_tips prose documents the backstop (Option C hybrid,
#    review-loop-nodekind-drift-fix: the backstop residue-note discipline
#    moved from review_snowball_tips, since review-snowball is now a
#    deterministic tool node and review-curate is the judgment layer that
#    writes _coverage-gaps.md)
# ---------------------------------------------------------------------------

class TestReviewCurateTipsDocumentsBackstop:
    def test_tips_mention_backstop_config_and_stop_reason(self):
        from research_vault.review.style import get_review_tips
        tips = get_review_tips(config=None)
        curate = tips["review_curate_tips"]
        assert "stop_reason" in curate
        assert "_coverage-gaps.md" in curate
        assert "backstop" in curate.lower()
