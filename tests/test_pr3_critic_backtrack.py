"""tests/test_pr3_critic_backtrack.py — PR-3 D-5a: wiring the critic-BLOCK
-> bounded, pole-directed backtrack (the un-wired REVISE path a human used
to hand-direct).

Design of record: the PR-3 remediation brief (rv-architect, 2026-07-10) —
wires review.check_coverage_critic_verdict's remediation_target,
review.remediation.resolve_coverage_critic/run_directed_remediation_round,
and their approve-review wiring in dag/verbs.py.

PR-3b (Shape B) removed ``review.remediation.run_bounded_critic_backtrack``
(the synchronous in-process backtrack loop) — the round-stepping is now
driven directly by ``dag/verbs.py``'s approve-review branch, pausing
between rounds for the harness's async cold-judge relate fan-out. Its
dedicated test class was removed with it; ``run_directed_remediation_round``
itself is still live (called per-round from ``dag/verbs.py``) and its tests
below are unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.review import _parse_corpus_citekeys, check_coverage_critic_verdict  # noqa: E402
from research_vault.review import autonomy as auto  # noqa: E402
from research_vault.review import corpus_freeze as cf  # noqa: E402
from research_vault.review import remediation as rem  # noqa: E402
from research_vault.sources.sweep import DedupedHit, PaperHit, SweepResult  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _corpus_note(path: Path, citekeys: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(f"| [NEW] | {ck} | title-{ck} |" for ck in citekeys)
    path.write_text(
        "| annotation | citekey | title |\n|---|---|---|\n" + rows + "\n",
        encoding="utf-8",
    )


def _protocol_note(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "question: is persona-drift stable or does it decay over time?\n"
        "inclusion: RCTs and controlled LLM-persona studies\n"
        "exclusion: non-English\n"
        "coverage_claim: all English papers 2015-2025 on persona drift\n"
        "counter-position: persona/value stability — evidence the persona is NOT drifting\n"
        "seed_queries:\n"
        "  by-temporal:\n"
        "    thesis:\n"
        "      - \"persona drift over long conversations\"\n"
        "    counter:\n"
        "      - \"persona value stability long conversations\"\n"
        "sources: [semantic-scholar, arxiv]\n"
        "---\n\nProtocol.\n",
        encoding="utf-8",
    )


def _critic_note(
    path: Path,
    *,
    verdict: str = "BLOCK",
    reasons: list[str] | None = None,
    remediation_target: dict[str, str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"verdict: {verdict}"]
    if remediation_target is not None:
        lines.append(f"remediation_target_node: {remediation_target['node']}")
        lines.append(f"remediation_target_pole: {remediation_target['pole']}")
        lines.append(f"remediation_target_directive: {remediation_target['directive']}")
    lines.append("---")
    lines.append("")
    for r in reasons or []:
        lines.append(f"- {r}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _hit(title: str, *, doi: str | None = None, arxiv: str | None = None) -> PaperHit:
    return PaperHit(
        title=title, authors=["A. Author"], year=2024,
        external_ids={k: v for k, v in {"doi": doi, "arxiv": arxiv}.items() if v},
        abstract="", citation_count=0, source="semantic-scholar",
    )


def _deduped(hit: PaperHit) -> DedupedHit:
    return DedupedHit(hit=hit, external_ids=dict(hit.external_ids), sources={hit.source})


# ===========================================================================
# 1. check_coverage_critic_verdict — remediation_target classification
# ===========================================================================

class TestCoverageCriticVerdictRemediationTarget:
    def test_pure_counter_position_block_surfaces_target(self, tmp_path):
        note = tmp_path / "_coverage-critic.md"
        _critic_note(
            note,
            reasons=["COUNTER-POSITION THIN-POLE by-temporal — the stability pole is empty"],
            remediation_target={
                "node": "review-snowball", "pole": "by-temporal",
                "directive": "re-run the counter queries harder",
            },
        )
        payload = check_coverage_critic_verdict(note)
        assert payload["remediation_target_expected"] is True
        assert payload["remediation_target"] == {
            "node": "review-snowball", "pole": "by-temporal",
            "directive": "re-run the counter queries harder",
        }

    def test_mixed_block_never_expects_target(self, tmp_path):
        note = tmp_path / "_coverage-critic.md"
        _critic_note(
            note,
            reasons=[
                "COUNTER-POSITION THIN-POLE by-temporal — the stability pole is empty",
                "DIRECTION-STARVED plateau (axis 1)",
            ],
            remediation_target={
                "node": "review-snowball", "pole": "by-temporal", "directive": "x",
            },
        )
        payload = check_coverage_critic_verdict(note)
        assert payload["remediation_target_expected"] is False
        # target is a moot field once expected is False; resolve_coverage_critic
        # never reads it in that branch either way.

    def test_protocol_drift_alone_never_expects_target(self, tmp_path):
        note = tmp_path / "_coverage-critic.md"
        _critic_note(note, reasons=["PROTOCOL-DRIFT (axis 3)"])
        payload = check_coverage_critic_verdict(note)
        assert payload["remediation_target_expected"] is False
        assert payload["remediation_target"] is None

    def test_pure_counter_position_but_incomplete_target_is_none(self, tmp_path):
        note = tmp_path / "_coverage-critic.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(
            "---\nverdict: BLOCK\nremediation_target_pole: by-temporal\n---\n\n"
            "- COUNTER-POSITION THIN-POLE by-temporal — thin pole\n",
            encoding="utf-8",
        )
        payload = check_coverage_critic_verdict(note)
        assert payload["remediation_target_expected"] is True
        assert payload["remediation_target"] is None  # incomplete -> fail-closed, never guessed

    def test_pass_never_expects_target(self, tmp_path):
        note = tmp_path / "_coverage-critic.md"
        _critic_note(note, verdict="PASS")
        payload = check_coverage_critic_verdict(note)
        assert payload["remediation_target_expected"] is False
        assert payload["remediation_target"] is None

    def test_missing_note_carries_the_new_keys_too(self, tmp_path):
        payload = check_coverage_critic_verdict(tmp_path / "nope.md")
        assert payload["remediation_target_expected"] is False
        assert payload["remediation_target"] is None
        assert payload["not_run"]


# ===========================================================================
# 2. resolve_coverage_critic — the disposition extension
# ===========================================================================

class TestResolveCoverageCritic:
    def _base_revise(self, expected: bool, target: dict | None):
        base = auto.DispositionResult(auto.REVISE, "block", {"blocking": ["x"]})
        payload = {
            "blocking": ["x"], "not_run": [],
            "remediation_target_expected": expected, "remediation_target": target,
        }
        return base, payload

    def test_non_revise_base_passes_through_unchanged(self):
        base = auto.DispositionResult(auto.GO, "clean")
        out = rem.resolve_coverage_critic(base, {"remediation_target_expected": True})
        assert out is base

    def test_mixed_block_not_expected_stays_revise(self):
        base, payload = self._base_revise(expected=False, target=None)
        out = rem.resolve_coverage_critic(base, payload)
        assert out is base
        assert out.disposition == auto.REVISE

    def test_expected_but_missing_target_halts(self):
        base, payload = self._base_revise(expected=True, target=None)
        out = rem.resolve_coverage_critic(base, payload)
        assert out.disposition == auto.HALT_DECLARE

    def test_expected_but_incomplete_target_halts(self):
        base, payload = self._base_revise(expected=True, target={"node": "x", "pole": "", "directive": "y"})
        out = rem.resolve_coverage_critic(base, payload)
        assert out.disposition == auto.HALT_DECLARE

    def test_valid_target_and_budget_dispatches_backtrack(self):
        base, payload = self._base_revise(
            expected=True, target={"node": "review-snowball", "pole": "by-temporal", "directive": "d"},
        )
        out = rem.resolve_coverage_critic(base, payload, remediation_state=None, max_rounds=2)
        assert out.disposition == auto.CRITIC_BACKTRACK
        assert out.evidence["remediation_target"]["pole"] == "by-temporal"

    def test_budget_exhausted_halts_never_residues(self):
        base, payload = self._base_revise(
            expected=True, target={"node": "review-snowball", "pole": "by-temporal", "directive": "d"},
        )
        state = {"rounds_used": 2, "last_wave_added_count": 3}
        out = rem.resolve_coverage_critic(base, payload, remediation_state=state, max_rounds=2)
        assert out.disposition == auto.HALT_DECLARE  # axis-4 is hard, never GO-WITH-RESIDUE

    def test_zero_new_last_wave_halts(self):
        base, payload = self._base_revise(
            expected=True, target={"node": "review-snowball", "pole": "by-temporal", "directive": "d"},
        )
        state = {"rounds_used": 1, "last_wave_added_count": 0}
        out = rem.resolve_coverage_critic(base, payload, remediation_state=state, max_rounds=5)
        assert out.disposition == auto.HALT_DECLARE


# ===========================================================================
# 3. run_directed_remediation_round — the pole-directed sweep+snowball
# ===========================================================================

class TestDirectedRemediationRound:
    def _seed(self, tmp_path):
        corpus = tmp_path / "_corpus.md"
        protocol = tmp_path / "_protocol.md"
        deviations = tmp_path / "_deviations.md"
        _corpus_note(corpus, ["driftpaper2023"])
        _protocol_note(protocol)
        meta: dict = {}
        cf.stamp_corpus_freeze(meta, corpus_path=corpus, protocol_path=protocol)
        return meta, corpus, protocol, deviations

    def test_sweep_is_restricted_to_the_named_poles_counter_queries_and_all_sources(self, tmp_path):
        meta, corpus, protocol, deviations = self._seed(tmp_path)
        calls = []

        def fake_tool_op(op, **kwargs):
            calls.append((op, kwargs))
            if op == "sweep":
                return []
            return {}

        rem.run_directed_remediation_round(
            meta, pole="by-temporal", protocol_path=protocol, corpus_path=corpus,
            deviations_path=deviations, out_dir=tmp_path, tool_op_fn=fake_tool_op,
        )
        sweep_calls = [c for c in calls if c[0] == "sweep"]
        assert len(sweep_calls) == 1
        _, kwargs = sweep_calls[0]
        assert kwargs["angle_keys"] == {"by-temporal.counter"}
        assert set(kwargs["sources_override"]) >= {"semantic-scholar", "arxiv", "openalex"}
        assert kwargs["per_cell_limit"] == rem._RELAXED_PER_CELL_LIMIT

    def test_replay_stability_pole_block_finds_huang_and_lee(self, tmp_path):
        """★ ACCEPTANCE: replay the downstream-project stability-pole BLOCK — the backtrack
        re-runs the frozen counter-query and finds Huang/Lee WITHOUT a human
        directing it (the incident this PR-3 exists to close)."""
        meta, corpus, protocol, deviations = self._seed(tmp_path)

        huang = _hit("Huang et al. — Persona Value Stability Under Long Dialogue", doi="10.1/huang2024")
        lee = _hit("Lee & Park — Stable Personas Resist Conversational Drift", arxiv="2401.00001")

        def fake_tool_op(op, **kwargs):
            if op == "sweep":
                return SweepResult(
                    kept=[_deduped(huang), _deduped(lee)],
                    independent_count=2, total_hits_fetched=2, cells=[], errors=[],
                )
            if op == "snowball":
                assert set(kwargs["seed_ids"]) == {"10.1/huang2024", "2401.00001"}
                return {"corpus_raw": None, "saturation": None, "stop_reason": "saturated"}
            raise AssertionError(f"unexpected op {op!r}")

        result = rem.run_directed_remediation_round(
            meta, pole="by-temporal", protocol_path=protocol, corpus_path=corpus,
            deviations_path=deviations, out_dir=tmp_path, tool_op_fn=fake_tool_op,
        )
        assert result["stopped"] is None
        assert len(result["added"]) == 2
        citekeys = set(_parse_corpus_citekeys(corpus))
        assert any("huang" in ck.lower() for ck in citekeys)
        assert any("lee" in ck.lower() for ck in citekeys)
        assert deviations.exists()
        dev_text = deviations.read_text(encoding="utf-8")
        assert "within-criteria-append" in dev_text
        assert "by-temporal" in dev_text

    def test_zero_new_stops_without_declaring(self, tmp_path):
        meta, corpus, protocol, deviations = self._seed(tmp_path)

        def fake_tool_op(op, **kwargs):
            if op == "sweep":
                return []
            return {}

        result = rem.run_directed_remediation_round(
            meta, pole="by-temporal", protocol_path=protocol, corpus_path=corpus,
            deviations_path=deviations, out_dir=tmp_path, tool_op_fn=fake_tool_op,
        )
        assert result["stopped"] == "zero-new"
        assert not deviations.exists()

    def test_sweep_exception_degrades_never_crashes(self, tmp_path):
        meta, corpus, protocol, deviations = self._seed(tmp_path)

        def raising_tool_op(op, **kwargs):
            raise RuntimeError("all sources down")

        result = rem.run_directed_remediation_round(
            meta, pole="by-temporal", protocol_path=protocol, corpus_path=corpus,
            deviations_path=deviations, out_dir=tmp_path, tool_op_fn=raising_tool_op,
        )
        assert result["stopped"] == "zero-new"


# ===========================================================================
# 4. dag/verbs.py wiring — the real approve-review dispatch
# ===========================================================================

class TestDagVerbsApproveReviewWiring:
    def test_pure_counter_position_block_dispatches_backtrack_in_process(self, tmp_path, monkeypatch):
        from research_vault.dag.verbs import _evaluate_autonomous_gate

        review_dir = tmp_path
        corpus = review_dir / "_corpus.md"
        protocol = review_dir / "_protocol.md"
        critic_note = review_dir / "_coverage-critic.md"
        _corpus_note(corpus, ["driftpaper2023"])
        _protocol_note(protocol)
        _critic_note(
            critic_note,
            reasons=["COUNTER-POSITION THIN-POLE by-temporal — the stability pole is empty"],
            remediation_target={
                "node": "review-snowball", "pole": "by-temporal",
                "directive": "re-run the counter queries harder",
            },
        )

        nodes_lookup = {
            "review-coverage-critic": {"produces": {"_coverage-critic.md": str(critic_note)}},
        }

        huang = _hit("Huang et al. — Persona Value Stability", doi="10.1/huang2024")

        def fake_run_tool_op(op, **kwargs):
            if op == "sweep":
                return SweepResult(kept=[_deduped(huang)], independent_count=1, total_hits_fetched=1, cells=[], errors=[])
            if op == "snowball":
                return {"corpus_raw": None, "saturation": None, "stop_reason": "saturated"}
            raise AssertionError(op)

        monkeypatch.setattr(rem, "run_tool_op", fake_run_tool_op)

        class _FakeRunState:
            def __init__(self):
                self.meta: dict = {}

        run_state = _FakeRunState()
        disposition = _evaluate_autonomous_gate(
            "approve-review", nodes_lookup, tmp_path / "manifest.json", run_state,
        )
        # One round found Huang -> new -> next resolve sees last_added=1,
        # budget still remaining (default cap 2, 1 used) -> dispatches a
        # SECOND round; that one (still returning the same fake hit each
        # time since fake_tool_op is stateless) keeps finding "new" until
        # dedup kicks in on round 2 (same title => dedup'd => zero-new =>
        # HALT-DECLARE). Either way: Huang must be in the corpus, and the
        # gate must not silently GO.
        assert disposition.disposition == auto.HALT_DECLARE
        citekeys = set(_parse_corpus_citekeys(corpus))
        assert any("huang" in ck.lower() for ck in citekeys)

    def test_mixed_block_untouched_stays_revise(self, tmp_path, monkeypatch):
        from research_vault.dag.verbs import _evaluate_autonomous_gate

        review_dir = tmp_path
        corpus = review_dir / "_corpus.md"
        protocol = review_dir / "_protocol.md"
        critic_note = review_dir / "_coverage-critic.md"
        _corpus_note(corpus, ["driftpaper2023"])
        _protocol_note(protocol)
        _critic_note(
            critic_note,
            reasons=[
                "COUNTER-POSITION THIN-POLE by-temporal — the stability pole is empty",
                "PROTOCOL-DRIFT (axis 3)",
            ],
        )
        nodes_lookup = {
            "review-coverage-critic": {"produces": {"_coverage-critic.md": str(critic_note)}},
        }

        def fake_run_tool_op(op, **kwargs):
            raise AssertionError("a mixed BLOCK must never dispatch the backtrack machinery")

        monkeypatch.setattr(rem, "run_tool_op", fake_run_tool_op)

        class _FakeRunState:
            def __init__(self):
                self.meta: dict = {}

        run_state = _FakeRunState()
        disposition = _evaluate_autonomous_gate(
            "approve-review", nodes_lookup, tmp_path / "manifest.json", run_state,
        )
        assert disposition.disposition == auto.REVISE
