"""tests/test_ng6a_refresh_remediation.py — NG-6a: `rv review refresh` +
autonomous coverage-gap remediation.

Design of record: docs/superpowers/specs/2026-07-08-ng6a-refresh-autonomous-remediation.md
Builds ON the #185 baseline (``frozen_corpus_citekeys``,
``classify_coverage_gate_with_deviation_check``, already covered by
tests/test_ng4b_autonomy_wiring.py) — this file covers the NG-6a DELTA:
corpus_freeze, criteria-hash pin, the parser hardening, the
within-criteria-append deviation kind, ``rv review refresh``, and the
bounded remediation loop.

The leak-plants (LEAK-PLANT 1/2/3) are load-bearing (charter: "weakened-gate
needs leak-planting, not reasoning" + "skip-guard / green-but-stale").
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.review import CorpusSchemaError, _parse_corpus_citekeys  # noqa: E402
from research_vault.review import autonomy as auto  # noqa: E402
from research_vault.review import corpus_freeze as cf  # noqa: E402
from research_vault.review import remediation as rem  # noqa: E402


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _corpus_note(path: Path, citekeys: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(f"| [NEW] | {ck} | title-{ck} |" for ck in citekeys)
    path.write_text(
        "| annotation | citekey | title |\n|---|---|---|\n" + rows + "\n",
        encoding="utf-8",
    )


def _protocol_note(
    path: Path,
    *,
    inclusion: str = "RCTs only",
    exclusion: str = "non-English",
    counter_position: str = "a real counter-position",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "question: does X generalize across Y?\n"
        f"inclusion: {inclusion}\n"
        f"exclusion: {exclusion}\n"
        "coverage_claim: all English papers 2015-2025 on X\n"
        f"counter-position: {counter_position}\n"
        "seed_queries:\n"
        "  by-method:     \"X method\"\n"
        "  by-outcome:    \"X outcome\"\n"
        "sources: [semantic-scholar, arxiv]\n"
        "---\n\nProtocol.\n",
        encoding="utf-8",
    )




# ===========================================================================
# 1. Parser hardening — CorpusSchemaError (§3, the green-but-stale fix)
# ===========================================================================

class TestParserHardening:
    def test_well_formed_rows_still_parse(self, tmp_path):
        corpus = tmp_path / "_corpus.md"
        _corpus_note(corpus, ["alpha2024", "beta2024"])
        assert _parse_corpus_citekeys(corpus) == ["alpha2024", "beta2024"]

    def test_non_bracket_rows_still_silently_skipped(self, tmp_path):
        corpus = tmp_path / "_corpus.md"
        corpus.write_text(
            "| annotation | citekey | title |\n|---|---|---|\n"
            "| [NEW] | alpha2024 | Alpha paper |\n",
            encoding="utf-8",
        )
        # header + separator rows are not bracket-shaped in column 0 — a
        # correct, silent skip (not a schema error).
        assert _parse_corpus_citekeys(corpus) == ["alpha2024"]

    def test_malformed_bracket_annotation_raises_loud(self, tmp_path):
        """★ LEAK-PLANT 3: a remediation-appended (or hand-written) row with
        a bracket-shaped but unrecognized annotation must be rejected
        LOUDLY, never silently skipped (the pre-NG-6a green-but-stale bug)."""
        corpus = tmp_path / "_corpus.md"
        corpus.write_text(
            "| annotation | citekey | title |\n|---|---|---|\n"
            "| [NEW] | alpha2024 | Alpha paper |\n"
            "| [BAD] | ghost2024 | A malformed remediation row |\n",
            encoding="utf-8",
        )
        with pytest.raises(CorpusSchemaError, match="ghost2024|malformed"):
            _parse_corpus_citekeys(corpus)

    def test_compound_annotation_and_doi_citekey_parse(self, tmp_path):
        """Coverage-path twin of PR #250's relevance-verify-prep fix: a real
        ``review-curate`` run emits a COMPOUND annotation
        (``[LEG-1][NEW] {tags}``) and DOI-shaped citekeys
        (``10.48550/arXiv.2604.19787``) — neither the bare ``[NEW]`` exact
        match nor the old no-``/`` citekey charset matched this real shape,
        silently undercounting the corpus fed into ``coverage_report``/the
        coverage-gate."""
        corpus = tmp_path / "_corpus.md"
        corpus.write_text(
            "| annotation | citekey | title |\n|---|---|---|\n"
            "| [LEG-1][NEW] {SF,silicon-sampling} | 10.48550/arXiv.2604.19787 | A compound-annotated paper |\n"
            "| [IN-CORPUS:beta2024] | beta2024 | Prior-cycle paper |\n",
            encoding="utf-8",
        )
        assert _parse_corpus_citekeys(corpus) == [
            "10.48550/arXiv.2604.19787", "beta2024",
        ]

    def test_bracket_shaped_but_no_recognized_tag_still_raises_loud(self, tmp_path):
        """A compound-shaped annotation with NO NEW/IN-CORPUS token among its
        bracket tokens must still be the loud CorpusSchemaError reject —
        broadening the match to compound annotations must not also broaden
        it into silently accepting an unrecognized tag."""
        corpus = tmp_path / "_corpus.md"
        corpus.write_text(
            "| annotation | citekey | title |\n|---|---|---|\n"
            "| [LEG-1][WEIRD] {tags} | ghost2024 | malformed compound row |\n",
            encoding="utf-8",
        )
        with pytest.raises(CorpusSchemaError, match="ghost2024|malformed"):
            _parse_corpus_citekeys(corpus)

    def test_coverage_report_does_not_report_green_over_stale_subset(self, tmp_instance):
        """The malformed row must propagate through coverage_report too —
        never silently absorbed into a clean-looking report."""
        from research_vault.config import load_config
        from research_vault.review import coverage_report

        cfg = load_config()
        corpus = cfg.project_notes_dir("demo-research") / "reviews" / "s1" / "_corpus.md"
        corpus.parent.mkdir(parents=True, exist_ok=True)
        corpus.write_text(
            "| annotation | citekey | title |\n|---|---|---|\n"
            "| [NEW] | alpha2024 | Alpha paper |\n"
            "| [BAD] | ghost2024 | A malformed remediation row |\n",
            encoding="utf-8",
        )
        with pytest.raises(CorpusSchemaError):
            coverage_report("demo-research", "s1", config=cfg)


# ===========================================================================
# 2. record_deviation kind typing + the within-criteria-append invariant
#    (§5 layer 2)
# ===========================================================================

class TestDeviationKindTyping:
    def test_backward_compat_kind_none_no_invariant(self, tmp_path):
        """Pre-NG-6a callers (no kind=) are completely unconstrained —
        this is the exact shape test_ng4b_autonomy_wiring.py's
        test_declared_removal_proceeds uses (removed non-empty, pre!=post
        implied by different criteria strings)."""
        deviations = tmp_path / "_deviations.md"
        block = auto.record_deviation(
            deviations, version=2, pre_criteria="p1", post_criteria="p2",
            removed=["b"], added=[], rationale="human edit",
        )
        assert "Kind" not in block  # no Kind line at all — byte-compat

    def test_within_criteria_append_invariant_rejects_pre_ne_post(self, tmp_path):
        """★ LEAK-PLANT 1 (part 1): the loop cannot self-author a criteria
        change — record_deviation itself refuses."""
        deviations = tmp_path / "_deviations.md"
        with pytest.raises(ValueError, match="within-criteria-append"):
            auto.record_deviation(
                deviations, version=2, pre_criteria="p1", post_criteria="p2",
                removed=[], added=["x"], rationale="sneaky criteria edit",
                kind="within-criteria-append",
            )

    def test_within_criteria_append_invariant_rejects_removal(self, tmp_path):
        deviations = tmp_path / "_deviations.md"
        with pytest.raises(ValueError, match="within-criteria-append"):
            auto.record_deviation(
                deviations, version=2, pre_criteria="p1", post_criteria="p1",
                removed=["b"], added=["x"], rationale="sneaky removal",
                kind="within-criteria-append",
            )

    def test_within_criteria_append_accepts_pure_growth(self, tmp_path):
        deviations = tmp_path / "_deviations.md"
        block = auto.record_deviation(
            deviations, version=2, pre_criteria="p1", post_criteria="p1",
            removed=[], added=["x", "y"], rationale="remediation wave 1",
            kind="within-criteria-append",
        )
        assert "**Kind:** within-criteria-append" in block

    def test_criteria_change_kind_unconstrained(self, tmp_path):
        """A human-authored criteria-change deviation is NOT subject to the
        within-criteria-append invariant."""
        deviations = tmp_path / "_deviations.md"
        block = auto.record_deviation(
            deviations, version=2, pre_criteria="p1", post_criteria="p2",
            removed=["b"], added=["x"], rationale="widened inclusion by hand",
            kind="criteria-change",
        )
        assert "**Kind:** criteria-change" in block


# ===========================================================================
# 3. corpus_freeze baseline + criteria-hash canonicalization
# ===========================================================================

class TestCorpusFreeze:
    def test_stamp_is_idempotent(self, tmp_path):
        corpus = tmp_path / "_corpus.md"
        protocol = tmp_path / "_protocol.md"
        _corpus_note(corpus, ["a2024"])
        _protocol_note(protocol)
        meta: dict = {}
        f1 = cf.stamp_corpus_freeze(meta, corpus_path=corpus, protocol_path=protocol)
        assert f1["version"] == 1
        assert f1["corpus_citekeys"] == ["a2024"]
        # second call: no-op, same object
        f2 = cf.stamp_corpus_freeze(meta, corpus_path=corpus, protocol_path=protocol)
        assert f2 is f1

    def test_stamp_keeps_legacy_frozen_corpus_citekeys_in_sync(self, tmp_path):
        corpus = tmp_path / "_corpus.md"
        protocol = tmp_path / "_protocol.md"
        _corpus_note(corpus, ["a2024", "b2024"])
        _protocol_note(protocol)
        meta: dict = {}
        f1 = cf.stamp_corpus_freeze(meta, corpus_path=corpus, protocol_path=protocol)
        assert meta["frozen_corpus_citekeys"] == f1["corpus_citekeys"]

    def test_criteria_hash_stable_for_identical_protocol(self, tmp_path):
        p1 = tmp_path / "p1.md"
        p2 = tmp_path / "p2.md"
        _protocol_note(p1)
        _protocol_note(p2)
        assert cf.hash_criteria_bytes(p1) == cf.hash_criteria_bytes(p2)

    def test_criteria_hash_changes_on_inclusion_edit(self, tmp_path):
        p1 = tmp_path / "p1.md"
        p2 = tmp_path / "p2.md"
        _protocol_note(p1, inclusion="RCTs only")
        _protocol_note(p2, inclusion="RCTs AND quasi-experimental")
        assert cf.hash_criteria_bytes(p1) != cf.hash_criteria_bytes(p2)

    def test_criteria_hash_unaffected_by_counter_position(self, tmp_path):
        """counter-position is the L-2 gate's field, not a scope-criteria
        field — a counter-position edit is not a criteria deviation."""
        p1 = tmp_path / "p1.md"
        p2 = tmp_path / "p2.md"
        _protocol_note(p1, counter_position="opposing view A")
        _protocol_note(p2, counter_position="opposing view B, much longer")
        assert cf.hash_criteria_bytes(p1) == cf.hash_criteria_bytes(p2)


# ===========================================================================
# 4. rv review refresh (cf.refresh) — fail-closed order (§2)
# ===========================================================================

class TestRefresh:
    def _seed(self, tmp_path, citekeys):
        corpus = tmp_path / "_corpus.md"
        protocol = tmp_path / "_protocol.md"
        deviations = tmp_path / "_deviations.md"
        _corpus_note(corpus, citekeys)
        _protocol_note(protocol)
        meta: dict = {}
        cf.stamp_corpus_freeze(meta, corpus_path=corpus, protocol_path=protocol)
        return meta, corpus, protocol, deviations

    def test_refresh_blocked_absent_baseline(self, tmp_path):
        corpus = tmp_path / "_corpus.md"
        protocol = tmp_path / "_protocol.md"
        deviations = tmp_path / "_deviations.md"
        _corpus_note(corpus, ["a2024"])
        _protocol_note(protocol)
        with pytest.raises(cf.RefreshBlocked, match="no corpus_freeze"):
            cf.refresh(
                {}, corpus_path=corpus, protocol_path=protocol, deviations_path=deviations,
            )

    def test_refresh_no_delta_is_a_noop_version_bump(self, tmp_path):
        meta, corpus, protocol, deviations = self._seed(tmp_path, ["a2024"])
        new_freeze = cf.refresh(
            meta, corpus_path=corpus, protocol_path=protocol, deviations_path=deviations,
        )
        assert new_freeze["version"] == 2
        assert new_freeze["corpus_citekeys"] == ["a2024"]

    def test_refresh_undeclared_criteria_change_blocks(self, tmp_path):
        """★ LEAK-PLANT 1 (part 2): edit _protocol.md's inclusion rule with
        no human criteria-change deviation on record -> BLOCK."""
        meta, corpus, protocol, deviations = self._seed(tmp_path, ["a2024"])
        _protocol_note(protocol, inclusion="RCTs AND quasi-experimental (widened)")
        with pytest.raises(cf.RefreshBlocked, match="criteria"):
            cf.refresh(
                meta, corpus_path=corpus, protocol_path=protocol, deviations_path=deviations,
            )

    def test_refresh_declared_criteria_change_proceeds(self, tmp_path):
        meta, corpus, protocol, deviations = self._seed(tmp_path, ["a2024"])
        _protocol_note(protocol, inclusion="RCTs AND quasi-experimental (widened)")
        auto.record_deviation(
            deviations, version=2, pre_criteria="RCTs only", post_criteria="RCTs AND quasi",
            removed=[], added=[], rationale="human-authored widening",
            kind="criteria-change",
        )
        new_freeze = cf.refresh(
            meta, corpus_path=corpus, protocol_path=protocol, deviations_path=deviations,
        )
        assert new_freeze["version"] == 2
        assert new_freeze["criteria_hash"] == cf.hash_criteria_bytes(protocol)

    def test_refresh_undeclared_corpus_delta_blocks(self, tmp_path):
        """★ LEAK-PLANT 2: append a citekey with no deviation record ->
        check_undeclared_deviation BLOCKs; refresh refuses."""
        meta, corpus, protocol, deviations = self._seed(tmp_path, ["a2024"])
        _corpus_note(corpus, ["a2024", "sneaky2024"])
        with pytest.raises(cf.RefreshBlocked, match="undeclared|BLOCKED"):
            cf.refresh(
                meta, corpus_path=corpus, protocol_path=protocol, deviations_path=deviations,
            )

    def test_refresh_declared_corpus_delta_proceeds_and_bumps_frozen_citekeys(self, tmp_path):
        meta, corpus, protocol, deviations = self._seed(tmp_path, ["a2024"])
        _corpus_note(corpus, ["a2024", "b2024"])
        auto.record_deviation(
            deviations, version=2, pre_criteria="p", post_criteria="p",
            removed=[], added=["b2024"], rationale="manual add",
            kind="within-criteria-append",
        )
        new_freeze = cf.refresh(
            meta, corpus_path=corpus, protocol_path=protocol, deviations_path=deviations,
        )
        assert new_freeze["corpus_citekeys"] == ["a2024", "b2024"]
        assert meta["frozen_corpus_citekeys"] == ["a2024", "b2024"]

    def test_refresh_propagates_corpus_schema_error(self, tmp_path):
        meta, corpus, protocol, deviations = self._seed(tmp_path, ["a2024"])
        corpus.write_text(
            "| annotation | citekey | title |\n|---|---|---|\n"
            "| [NEW] | a2024 | Alpha |\n"
            "| [WEIRD] | ghost2024 | malformed |\n",
            encoding="utf-8",
        )
        with pytest.raises(CorpusSchemaError):
            cf.refresh(
                meta, corpus_path=corpus, protocol_path=protocol, deviations_path=deviations,
            )

    def test_manuscript_binding_untouched_by_refresh(self, tmp_path):
        """Refresh never touches _manuscript.md — a manuscript_note file
        placed alongside must survive byte-identical."""
        meta, corpus, protocol, deviations = self._seed(tmp_path, ["a2024"])
        manuscript_note = tmp_path / "_manuscript.md"
        manuscript_note.write_text("---\ncorpus_hash: sha256:deadbeef\n---\n", encoding="utf-8")
        before = manuscript_note.read_text(encoding="utf-8")
        cf.refresh(
            meta, corpus_path=corpus, protocol_path=protocol, deviations_path=deviations,
        )
        assert manuscript_note.read_text(encoding="utf-8") == before


# ===========================================================================
# 5. End-to-end through the REAL dag-verbs path (not just the review/
#    refresh unit level) — driven through a full DAG tick, mirroring
#    test_ng4b_autonomy_wiring.py's TestSelfAdvancingRunner harness.
#
#    0.3.1 NOTE: the coverage-gate REMEDIATE composition/round/bounded-
#    termination test classes that used to live here were DELETED along
#    with the saturation-gated snowball's remediation machinery (see
#    review/remediation.py's module docstring) — depth-bounding the
#    citation-neighbor walk makes auto-re-expansion contradictory. What
#    remains below exercises the coverage-gate's walk-terminal-based
#    disposition end-to-end (GO on walk-complete, HALT-DECLARE on a
#    malformed corpus row) with no remediation round in the loop.
# ===========================================================================

class TestEndToEndThroughDagVerbs:
    def _kick_review(self, cfg, scope: str):
        from research_vault.review import cmd_new
        from research_vault.dag.verbs import cmd_run
        from research_vault.dag.store import RunStore

        note_path, review_dir, phase1 = cmd_new(
            "demo-research", scope, question="Does X generalize across Y?", config=cfg,
        )
        manifest_path = review_dir / "phase1-dag.json"
        rc = cmd_run(argparse.Namespace(manifest=str(manifest_path)))
        assert rc == 0
        run_id = phase1["run_id"]
        store = RunStore.from_config(cfg)
        return run_id, review_dir, store

    def _drive_to_coverage_gate(
        self, run_id, review_dir, store, *, extra_corpus_citekeys=None, malformed_row=False,
        monkeypatch=None,
    ):
        """review-loop-nodekind-drift-fix (Option C hybrid): review-search/
        review-snowball are TOOL nodes now — fake their OP_REGISTRY entries
        (network-free) BEFORE approve-protocol, since cmd_approve's internal
        frontier recompute auto-executes a newly-ready tool node in the SAME
        call. review-screen/review-curate (the new thin agent nodes) are
        completed by hand, same convention as every other agent node here."""
        from research_vault.dag.verbs import cmd_tick, cmd_approve, cmd_complete
        from research_vault.review import autonomy as _auto

        assert monkeypatch is not None, "_drive_to_coverage_gate requires monkeypatch"

        def _fake_sweep(*, out=None, **_kw):
            if out:
                Path(out).parent.mkdir(parents=True, exist_ok=True)
                Path(out).write_text("# fake search hits\n", encoding="utf-8")
                return str(out)
            return "fake sweep result"

        def _fake_snowball(*, out_dir=None, **_kw):
            out = Path(out_dir)
            out.mkdir(parents=True, exist_ok=True)
            (out / "_corpus_raw.md").write_text(
                "| [NEW] | alpha2024 | Alpha paper |\n| [NEW] | beta2024 | Beta paper |\n",
                encoding="utf-8",
            )
            (out / "_walk.md").write_text(
                "---\nstop_reason: walk-complete:1-hops\n---\n\nCitation-neighbor relevance walk.\n",
                encoding="utf-8",
            )
            return {"stop_reason": "walk-complete:1-hops"}

        monkeypatch.setitem(_auto.OP_REGISTRY, "sweep", _fake_sweep)
        monkeypatch.setitem(_auto.OP_REGISTRY, "snowball", _fake_snowball)

        protocol_path = review_dir / "_protocol.md"
        protocol_path.write_text(
            "---\ncounter-position: a real counter-position\n---\n\nProtocol.\n",
            encoding="utf-8",
        )
        rc = cmd_complete(argparse.Namespace(run_id=run_id, node_id="review-scope", status="succeeded"))
        assert rc == 0
        cmd_tick(argparse.Namespace(run_id=run_id))
        rc = cmd_approve(argparse.Namespace(
            run_id=run_id, node_id="approve-protocol", note=None, output=[], reject=False, auto=False,
        ))
        assert rc == 0  # review-search (tool) auto-executed in this same call

        # review-screen (agent) "completes": accepts the seed frontier.
        (review_dir / "_screen.md").write_text("10.1/alpha2024\n10.1/beta2024\n", encoding="utf-8")
        rc = cmd_complete(argparse.Namespace(run_id=run_id, node_id="review-screen", status="succeeded"))
        assert rc == 0  # review-snowball (tool) auto-executed in this same call

        # review-curate (agent) "completes": writes the FINAL _corpus.md
        # (+ _coverage-gaps.md, since the fake snowball always reports
        # backstop:3-waves).
        citekeys = ["alpha2024", "beta2024"] + (extra_corpus_citekeys or [])
        corpus_path = review_dir / "_corpus.md"
        _corpus_note(corpus_path, citekeys)
        if malformed_row:
            # Injected BEFORE review-curate completes — cmd_complete's own
            # internal frontier recompute is what fires coverage-gate's
            # autonomous resolution (not only a later explicit cmd_tick).
            corpus_path.write_text(
                corpus_path.read_text(encoding="utf-8") + "| [WEIRD] | ghost2024 | malformed |\n",
                encoding="utf-8",
            )
        (review_dir / "_coverage-gaps.md").write_text("open frontier\n", encoding="utf-8")
        rc = cmd_complete(argparse.Namespace(run_id=run_id, node_id="review-curate", status="succeeded"))
        assert rc == 0

        # review-relevance-verify-prep (TOOL, real op) auto-executed above;
        # review-relevance-verify (COLD agent) "completes": a canary-clean,
        # all-IN verdict for the well-formed citekeys (PR-1, design
        # 2026-07-10-trustworthy-curation-relevance-gate-design.md §3b) —
        # the malformed `[WEIRD]` row never parses into a verify-input row
        # in the first place (parse_corpus_table_with_abstract only picks
        # up `[NEW]` rows), so it needs no verdict entry.
        from research_vault.review.relevance import (
            CANARY_IN_SCOPE_CITEKEY, CANARY_OFF_DOMAIN_CITEKEY, IN, OFF_DOMAIN,
        )

        verdict_path = review_dir / "_relevance-verdict.md"
        lines = ["| Citekey | Verdict |", "|---|---|"]
        for ck in citekeys:
            lines.append(f"| {ck} | {IN} |")
        lines.append(f"| {CANARY_IN_SCOPE_CITEKEY} | {IN} |")
        lines.append(f"| {CANARY_OFF_DOMAIN_CITEKEY} | {OFF_DOMAIN} |")
        verdict_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        rc = cmd_complete(argparse.Namespace(run_id=run_id, node_id="review-relevance-verify", status="succeeded"))
        assert rc == 0

    def test_walk_complete_gate_autonomously_goes_no_remediation(
        self, tmp_instance, monkeypatch,
    ):
        """The real end-to-end path (0.3.1): coverage-gate resolves GO
        directly off a ``walk-complete:1-hops`` terminal — no REMEDIATE
        upgrade, no bounded round, no extra tool-op call — driven through a
        REAL `dag tick`, not a monkeypatched internal-only call."""
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        run_id, review_dir, store = self._kick_review(cfg, scope="scope-walk-complete")

        self._drive_to_coverage_gate(run_id, review_dir, store, monkeypatch=monkeypatch)

        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("coverage-gate") == "succeeded"
        assert "GO" in rs.node_states["coverage-gate"]["decision_note"]
        assert "REMEDIATE" not in rs.node_states["coverage-gate"]["decision_note"]

        corpus_path = review_dir / "_corpus.md"
        citekeys = _parse_corpus_citekeys(corpus_path)
        assert len(citekeys) == 2  # alpha2024 + beta2024 — nothing added by remediation

        # No remediation state was ever populated (the machinery is gone).
        assert not rs.meta.get("remediation_state")

        # corpus_freeze stamped, and legacy frozen_corpus_citekeys is in sync.
        assert rs.meta["corpus_freeze"]["version"] == 1
        assert set(rs.meta["frozen_corpus_citekeys"]) == set(citekeys)

        # Phase-2 still auto-emitted on GO.
        assert "emitted_next_phase_run_id" in rs.node_states["coverage-gate"]

    def test_malformed_corpus_row_surfaces_as_halt_declare_not_a_crash(
        self, tmp_instance, monkeypatch,
    ):
        """A CorpusSchemaError anywhere in the coverage-gate evaluation path
        surfaces as a first-class HALT-DECLARE, never an uncaught exception
        that crashes the DAG runner."""
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        run_id, review_dir, store = self._kick_review(cfg, scope="scope-malformed")
        self._drive_to_coverage_gate(run_id, review_dir, store, malformed_row=True, monkeypatch=monkeypatch)

        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0  # tick itself doesn't crash/raise
        rs = store.load(run_id)
        assert rs.node_status("coverage-gate") == "blocked"
        assert "HALT-DECLARE" in rs.node_states["coverage-gate"]["decision_note"]


# ===========================================================================
# 8. rv review refresh CLI wiring
# ===========================================================================

class TestRefreshCliVerb:
    def test_cmd_refresh_end_to_end(self, tmp_instance, monkeypatch):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick, cmd_complete, cmd_approve
        from research_vault.review import cmd_new
        from research_vault.review.corpus_freeze import cmd_refresh
        from research_vault.dag.verbs import cmd_run
        from research_vault.dag.store import RunStore
        from research_vault.review import autonomy as _auto

        cfg = load_config()
        note_path, review_dir, phase1 = cmd_new(
            "demo-research", "scope-refresh-cli", question="Q?", config=cfg,
        )
        manifest_path = review_dir / "phase1-dag.json"
        rc = cmd_run(argparse.Namespace(manifest=str(manifest_path)))
        assert rc == 0
        run_id = phase1["run_id"]
        store = RunStore.from_config(cfg)

        # review-loop-nodekind-drift-fix (Option C hybrid): fake review-search/
        # review-snowball's OP_REGISTRY entries BEFORE approve-protocol —
        # cmd_approve's internal frontier recompute auto-executes a
        # newly-ready tool node in the SAME call.
        def _fake_sweep(*, out=None, **_kw):
            if out:
                Path(out).parent.mkdir(parents=True, exist_ok=True)
                Path(out).write_text("# fake search hits\n", encoding="utf-8")
                return str(out)
            return "fake sweep result"

        def _fake_snowball(*, out_dir=None, **_kw):
            out = Path(out_dir)
            out.mkdir(parents=True, exist_ok=True)
            (out / "_corpus_raw.md").write_text(
                "| [NEW] | alpha2024 | Alpha paper |\n", encoding="utf-8",
            )
            (out / "_walk.md").write_text(
                "---\nstop_reason: walk-complete:1-hops\n---\n\n", encoding="utf-8",
            )
            return {"stop_reason": "walk-complete:1-hops"}

        monkeypatch.setitem(_auto.OP_REGISTRY, "sweep", _fake_sweep)
        monkeypatch.setitem(_auto.OP_REGISTRY, "snowball", _fake_snowball)

        protocol_path = review_dir / "_protocol.md"
        protocol_path.write_text(
            "---\ncounter-position: a real counter-position\n---\n\nProtocol.\n",
            encoding="utf-8",
        )
        cmd_complete(argparse.Namespace(run_id=run_id, node_id="review-scope", status="succeeded"))
        cmd_tick(argparse.Namespace(run_id=run_id))
        cmd_approve(argparse.Namespace(
            run_id=run_id, node_id="approve-protocol", note=None, output=[], reject=False, auto=False,
        ))  # review-search (tool) auto-executed in this same call

        # review-screen (agent) "completes": accepts the seed frontier.
        (review_dir / "_screen.md").write_text("10.1/alpha2024\n", encoding="utf-8")
        cmd_complete(argparse.Namespace(run_id=run_id, node_id="review-screen", status="succeeded"))
        # review-snowball (tool) auto-executed in this same call.

        # review-curate (agent) "completes": writes the FINAL _corpus.md.
        corpus_path = review_dir / "_corpus.md"
        _corpus_note(corpus_path, ["alpha2024"])
        cmd_complete(argparse.Namespace(run_id=run_id, node_id="review-curate", status="succeeded"))

        # review-relevance-verify-prep (TOOL, real op) auto-executed above;
        # review-relevance-verify (COLD agent) "completes": a canary-clean,
        # all-IN verdict (PR-1, design 2026-07-10-trustworthy-curation-
        # relevance-gate-design.md §3b) so coverage-gate can resolve.
        from research_vault.review.relevance import (
            CANARY_IN_SCOPE_CITEKEY, CANARY_OFF_DOMAIN_CITEKEY, IN, OFF_DOMAIN,
        )

        verdict_path = review_dir / "_relevance-verdict.md"
        verdict_path.write_text(
            "| Citekey | Verdict |\n|---|---|\n"
            f"| alpha2024 | {IN} |\n"
            f"| {CANARY_IN_SCOPE_CITEKEY} | {IN} |\n"
            f"| {CANARY_OFF_DOMAIN_CITEKEY} | {OFF_DOMAIN} |\n",
            encoding="utf-8",
        )
        cmd_complete(argparse.Namespace(run_id=run_id, node_id="review-relevance-verify", status="succeeded"))
        cmd_tick(argparse.Namespace(run_id=run_id))  # stamps corpus_freeze v1

        rs = store.load(run_id)
        assert rs.meta["corpus_freeze"]["version"] == 1

        # Declared append + refresh via the CLI-level entry point.
        _corpus_note(corpus_path, ["alpha2024", "beta2024"])
        deviations_path = review_dir / "_deviations.md"
        _auto.record_deviation(
            deviations_path, version=2, pre_criteria="p", post_criteria="p",
            removed=[], added=["beta2024"], rationale="manual add via cli test",
            kind="within-criteria-append",
        )
        new_freeze = cmd_refresh("demo-research", "scope-refresh-cli", config=cfg)
        assert new_freeze["version"] == 2
        assert new_freeze["corpus_citekeys"] == ["alpha2024", "beta2024"]

    def test_refresh_verb_parses(self):
        from research_vault.review.verbs import build_parser

        p = build_parser()
        args = p.parse_args(["demo-research", "refresh", "scope-x"])
        assert args.review_cmd == "refresh"
        assert args.scope == "scope-x"
