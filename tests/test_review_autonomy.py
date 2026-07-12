"""test_review_autonomy.py — NG-4/5/6 acceptance tests: the gate-policy
engine and the deviation log's repurposed BLOCK.

Coverage:
  1. classify_disposition — each of the four failure classes -> the correct
     disposition (§1.2's table), including priority ordering when more than
     one signal fires at once.
  2. classify_coverage_gate — keyed to the EXACT 0.2.4 stop_reason strings
     (saturated / backstop:N-waves / malformed), §1.6.
  3. Adapters (evaluation_from_structural_payload / evaluation_from_board /
     evaluation_from_framework_gate) correctly translate real gate payload
     shapes.
  4. The deviation log (§1.5, D2) — record_deviation writes a declared
     block; check_undeclared_deviation's REPURPOSED BLOCK — ★ leak-planted:
     an undeclared membership removal MUST trip the BLOCK; a declared one
     MUST pass. This is a SEPARATE, fail-closed safety (a silent corpus/
     criteria mutation) — it stays fully intact; it is NOT the removed
     async-veto/provisional machinery (single-human-gate design, 2026-07-09:
     an auto-resolved decision is final immediately, no veto window).
  5. The tool-op registry — unregistered op raises loudly (never a silent
     no-op); registered ops call through to the real library function
     (injected fake, hermetic).

NOTE (2026-07-09): the async-veto window (§1.7 — VetoWindow/open_veto_window/
cast_veto/clear_provisional_if_elapsed/check_declare_final_gate) and its
`rv dag veto` CLI surface were REMOVED. The design intent is a single human
gate (approve-protocol only); every downstream gate resolves autonomously and
FINALLY the moment it resolves — no provisional stamp, no veto window, no
user-facing provisional/confirmed bookkeeping. See DEVLOG.md.
"""
from __future__ import annotations

import ast
import datetime
import importlib
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.review import autonomy as auto  # noqa: E402


# ---------------------------------------------------------------------------
# 1. classify_disposition — the four failure classes
# ---------------------------------------------------------------------------

class TestClassifyDisposition:
    def test_canary_abort_halts_regardless_of_other_signals(self):
        ev = auto.GateEvaluation(canary_aborted=True, blocking=["some finding"])
        result = auto.classify_disposition(ev)
        assert result.disposition == auto.HALT_DECLARE
        assert "canary" in result.reason.lower()
        assert result.evidence["canary_aborted"] is True

    def test_floor_gate_not_run_halts(self):
        ev = auto.GateEvaluation(not_run=["support-matcher"])
        result = auto.classify_disposition(ev)
        assert result.disposition == auto.HALT_DECLARE
        assert "not run" in result.reason.lower() or "not_run" in str(result.evidence)

    def test_fixable_block_with_budget_left_revises(self):
        ev = auto.GateEvaluation(blocking=["unresolved citekey foo2024"])
        result = auto.classify_disposition(ev)
        assert result.disposition == auto.REVISE
        assert result.evidence["blocking"] == ["unresolved citekey foo2024"]

    def test_fixable_block_with_budget_exhausted_halts(self):
        ev = auto.GateEvaluation(blocking=["still ABSENT"], revise_budget_exhausted=True)
        result = auto.classify_disposition(ev)
        assert result.disposition == auto.HALT_DECLARE
        assert result.evidence["revise_budget_exhausted"] is True

    def test_declared_residue_goes_with_residue(self):
        ev = auto.GateEvaluation(residue="backstop:3-waves")
        result = auto.classify_disposition(ev)
        assert result.disposition == auto.GO_WITH_RESIDUE
        assert result.is_go

    def test_clean_gate_is_go(self):
        result = auto.classify_disposition(auto.GateEvaluation())
        assert result.disposition == auto.GO
        assert result.is_go

    def test_priority_canary_beats_block(self):
        """Canary-abort must win even when a fixable BLOCK also fired —
        an untrustworthy judge cannot be allowed to REVISE against its own
        (untrusted) finding."""
        ev = auto.GateEvaluation(canary_aborted=True, blocking=["x"], residue="y")
        result = auto.classify_disposition(ev)
        assert result.disposition == auto.HALT_DECLARE
        assert "canary" in result.reason.lower()

    def test_priority_not_run_beats_residue(self):
        ev = auto.GateEvaluation(not_run=["support-matcher"], residue="backstop:3-waves")
        result = auto.classify_disposition(ev)
        assert result.disposition == auto.HALT_DECLARE

    def test_invalid_disposition_string_rejected(self):
        with pytest.raises(ValueError):
            auto.DispositionResult("MAYBE", "bad")


# ---------------------------------------------------------------------------
# 2. classify_coverage_gate — exact 0.2.4 stop_reason contract
# ---------------------------------------------------------------------------

class TestClassifyCoverageGate:
    def test_walk_complete_is_go(self):
        info = {"exists": True, "stop_reason": "walk-complete:1-hops", "walk_complete": True, "hop_count": 1}
        result = auto.classify_coverage_gate(info)
        assert result.disposition == auto.GO

    def test_neighborhood_exhausted_is_go(self):
        info = {"exists": True, "stop_reason": "neighborhood-exhausted", "walk_complete": False, "hop_count": None}
        result = auto.classify_coverage_gate(info)
        assert result.disposition == auto.GO

    def test_walk_complete_demands_no_gaps_note(self, tmp_path):
        """0.3.1: walk-complete:N-hops is the normal terminal — GO even when
        no _coverage-gaps.md exists at all (never demanded)."""
        gaps = tmp_path / "_coverage-gaps.md"  # never written
        info = {"exists": True, "stop_reason": "walk-complete:1-hops", "walk_complete": True, "hop_count": 1}
        result = auto.classify_coverage_gate(info, coverage_gaps_path=gaps)
        assert result.disposition == auto.GO

    def test_budget_with_gaps_note_is_go_with_residue(self, tmp_path):
        gaps = tmp_path / "_coverage-gaps.md"
        gaps.write_text("open frontier\n")
        info = {"exists": True, "stop_reason": "budget:200-calls", "walk_complete": False, "hop_count": None}
        result = auto.classify_coverage_gate(info, coverage_gaps_path=gaps)
        assert result.disposition == auto.GO_WITH_RESIDUE
        assert result.evidence["stop_reason"] == "budget:200-calls"

    def test_budget_without_gaps_note_halts(self, tmp_path):
        gaps = tmp_path / "_coverage-gaps.md"  # never written
        info = {"exists": True, "stop_reason": "budget:200-calls", "walk_complete": False, "hop_count": None}
        result = auto.classify_coverage_gate(info, coverage_gaps_path=gaps)
        assert result.disposition == auto.HALT_DECLARE

    def test_missing_walk_file_halts(self):
        info = {"exists": False, "stop_reason": "", "walk_complete": False, "hop_count": None}
        result = auto.classify_coverage_gate(info)
        assert result.disposition == auto.HALT_DECLARE

    @pytest.mark.parametrize(
        "stop_reason",
        ["", "walk-complete-1-hops", "walk complete after 1 hop", "saturated", "SATURATED ",
         "backstop:3-waves", "garbage", "not sure", "1"],
    )
    def test_malformed_or_noncanonical_stop_reason_halts(self, stop_reason):
        """Whitelist, not blacklist — every non-canonical spelling (including
        the legacy pre-0.3.1 vocabulary) must fail closed, never sail through
        as if the walk completed cleanly."""
        info = {"exists": True, "stop_reason": stop_reason, "walk_complete": False, "hop_count": None}
        result = auto.classify_coverage_gate(info)
        assert result.disposition == auto.HALT_DECLARE


# ---------------------------------------------------------------------------
# 2b. classify_coverage_gate — source-coverage fail-closed (pre-publish
# hardening batch, 2026-07-09 downstream e2e-run finding): a source declared
# in the protocol's `sources:` list that went DARK this sweep must HALT,
# even when the walk record itself would otherwise say "walk-complete:N-hops".
# ---------------------------------------------------------------------------

class TestClassifyCoverageGateSourceDark:
    WALK_COMPLETE_INFO = {"exists": True, "stop_reason": "walk-complete:1-hops", "walk_complete": True, "hop_count": 1}

    def test_declared_dark_source_halts_even_when_walk_complete(self):
        """The dark-source check must short-circuit BEFORE the (otherwise
        GO) walk-terminal logic runs — a genuinely-clean-looking record is
        NOT trustworthy if a declared source never actually answered."""
        source_info = {"exists": True, "dark_sources": ["arxiv"], "declared_dark": ["arxiv"]}
        result = auto.classify_coverage_gate(self.WALK_COMPLETE_INFO, source_coverage_info=source_info)
        assert result.disposition == auto.HALT_DECLARE
        assert "arxiv" in result.reason
        assert result.evidence["declared_dark_sources"] == ["arxiv"]

    def test_dark_but_undeclared_source_does_not_halt(self):
        """A source that went dark but was NEVER declared in the protocol's
        `sources:` list (e.g. an opt-in source nobody asked for) must not
        block — only DECLARED coverage is a promise the gate must keep."""
        source_info = {"exists": True, "dark_sources": ["pubmed"], "declared_dark": []}
        result = auto.classify_coverage_gate(self.WALK_COMPLETE_INFO, source_coverage_info=source_info)
        assert result.disposition == auto.GO

    def test_no_dark_sources_proceeds_to_normal_walk_terminal_logic(self):
        source_info = {"exists": True, "dark_sources": [], "declared_dark": []}
        result = auto.classify_coverage_gate(self.WALK_COMPLETE_INFO, source_coverage_info=source_info)
        assert result.disposition == auto.GO

    def test_source_coverage_info_none_is_backward_compatible(self):
        """Callers that don't pass source_coverage_info at all (pre-existing
        call sites) must behave exactly as before this feature."""
        result = auto.classify_coverage_gate(self.WALK_COMPLETE_INFO, source_coverage_info=None)
        assert result.disposition == auto.GO


# ---------------------------------------------------------------------------
# 3. Adapters
# ---------------------------------------------------------------------------

class TestAdapters:
    def test_structural_payload_adapter(self):
        payload = {"ok": False, "blocking": ["unresolved citekey"], "signals": [], "not_run": ["support-matcher"]}
        ev = auto.evaluation_from_structural_payload(payload)
        assert ev.blocking == ["unresolved citekey"]
        assert ev.not_run == ["support-matcher"]

    def test_board_adapter_cleared(self):
        board_result = {"cleared": True, "not_cleared": None}
        ev = auto.evaluation_from_board(board_result)
        assert ev.blocking == []
        assert not ev.revise_budget_exhausted

    def test_board_adapter_not_cleared_routes_to_residue(self):
        """Decision #6 (2026-07-08-autonomous-board-design.md §5.2): a bare
        board quality shortfall (no canary abort) is NOT the same failure
        class as an integrity BLOCK — it must populate ``residue`` (never
        ``blocking``) so ``classify_disposition`` returns GO-WITH-RESIDUE,
        not HALT-DECLARE. (Supersedes the pre-B5 behavior this test used to
        pin, where a not-cleared board unconditionally HALTed.)"""
        board_result = {"cleared": False, "not_cleared": {"failing_dims": ["SCOPE (min score 2 < floor 3)"]}}
        ev = auto.evaluation_from_board(board_result)
        assert ev.blocking == []
        assert ev.residue
        result = auto.classify_disposition(ev)
        assert result.disposition == auto.GO_WITH_RESIDUE

    def test_board_adapter_canary_aborted(self):
        ev = auto.evaluation_from_board({"cleared": False, "not_cleared": {}}, canary_aborted=True)
        result = auto.classify_disposition(ev)
        assert result.disposition == auto.HALT_DECLARE
        assert result.evidence["canary_aborted"] is True

    def test_framework_gate_adapter_ok(self):
        ev = auto.evaluation_from_framework_gate(True, "OK")
        assert auto.classify_disposition(ev).disposition == auto.GO

    def test_framework_gate_adapter_empty_spine_revises(self):
        ev = auto.evaluation_from_framework_gate(False, "spine_shape empty")
        result = auto.classify_disposition(ev)
        assert result.disposition == auto.REVISE
        assert "spine_shape empty" in result.evidence["blocking"]


# ---------------------------------------------------------------------------
# 4. The async-veto window (§1.7) is REMOVED (single-human-gate design,
#    2026-07-09). No provisional/vetoed frontmatter stamp is ever written by
#    this module — asserted directly below (no attribute survives to test
#    against, and no note produced by this module ever carries the field).
# ---------------------------------------------------------------------------

class TestVetoMachineryRemoved:
    def test_no_veto_symbols_remain_on_the_module(self):
        for name in (
            "VetoWindow",
            "open_veto_window",
            "cast_veto",
            "clear_provisional_if_elapsed",
            "check_declare_final_gate",
            "DEFAULT_VETO_WINDOW_HOURS",
        ):
            assert not hasattr(auto, name), (
                f"review.autonomy.{name} must be REMOVED (single-human-gate "
                "design, 2026-07-09) — an auto-resolved decision is final "
                "immediately, no async-veto window."
            )

    def test_record_deviation_never_stamps_provisional(self, tmp_path):
        """record_deviation's _deviations.md header must NOT carry a
        'provisional' field — that bookkeeping was purged along with the
        veto window (it must never surface anywhere, incl. a fresh log)."""
        deviations = tmp_path / "_deviations.md"
        auto.record_deviation(
            deviations, version=2, pre_criteria="p1", post_criteria="p2",
            removed=[], added=["x"], rationale="test",
        )
        text = deviations.read_text()
        assert "provisional" not in text.lower()


# ---------------------------------------------------------------------------
# 5. Deviation log (§1.5, D2) — ★ leak-planted acceptance
# ---------------------------------------------------------------------------

class TestDeviationLog:
    def test_record_deviation_writes_declared_block(self, tmp_path):
        deviations = tmp_path / "_deviations.md"
        auto.record_deviation(
            deviations,
            version=2,
            pre_criteria="include RCTs only",
            post_criteria="include RCTs + quasi-experimental",
            removed=["smith2020"],
            added=["jones2021", "lee2022"],
            rationale="quasi-experimental designs found relevant post-hoc",
        )
        text = deviations.read_text()
        assert "smith2020" in text
        assert "jones2021" in text and "lee2022" in text
        assert "Deviation v1 -> v2" in text

    def test_no_delta_passes_trivially(self, tmp_path):
        ok, msg = auto.check_undeclared_deviation(
            {"a", "b", "c"}, {"a", "b", "c"}, tmp_path / "_deviations.md",
        )
        assert ok is True

    def test_declared_delta_passes(self, tmp_path):
        deviations = tmp_path / "_deviations.md"
        auto.record_deviation(
            deviations, version=2, pre_criteria="p1", post_criteria="p2",
            removed=["b"], added=["d"], rationale="tier cut",
        )
        ok, msg = auto.check_undeclared_deviation(
            {"a", "b", "c"}, {"a", "c", "d"}, deviations,
        )
        assert ok is True, msg

    def test_UNDECLARED_membership_removal_trips_the_block(self, tmp_path):
        """★ Leak-planted acceptance (design §10 risk (a) — "weakened-gate
        needs leak-planting, not reasoning"): plant a real undeclared
        removal from the frozen corpus and confirm the BLOCK actually fires.
        No _deviations.md exists at all — the corpus just silently shrank.
        """
        frozen = {"smith2020", "jones2021", "lee2022"}
        current_silently_shrunk = {"smith2020", "jones2021"}  # lee2022 quietly dropped
        deviations = tmp_path / "_deviations.md"  # never written — undeclared
        ok, msg = auto.check_undeclared_deviation(frozen, current_silently_shrunk, deviations)
        assert ok is False
        assert "lee2022" in msg
        assert "undeclared" in msg.lower()

    def test_partially_declared_delta_still_blocks(self, tmp_path):
        """Declaring ONE of two removed papers must not launder the other."""
        deviations = tmp_path / "_deviations.md"
        auto.record_deviation(
            deviations, version=2, pre_criteria="p1", post_criteria="p2",
            removed=["b"], added=[], rationale="tier cut",
        )
        ok, msg = auto.check_undeclared_deviation(
            {"a", "b", "c"}, {"a"}, deviations,  # c also silently dropped, undeclared
        )
        assert ok is False
        assert "c" in msg

    def test_undeclared_addition_also_blocks(self, tmp_path):
        """D2 covers additions too, not just removals — a smuggled-in
        paper with no declared rationale is equally undeclared."""
        deviations = tmp_path / "_deviations.md"
        ok, msg = auto.check_undeclared_deviation({"a"}, {"a", "sneaky2024"}, deviations)
        assert ok is False
        assert "sneaky2024" in msg


# ---------------------------------------------------------------------------
# 6. Tool-op registry
# ---------------------------------------------------------------------------

class TestOpRegistry:
    def test_unregistered_op_raises_loudly(self):
        with pytest.raises(KeyError):
            auto.run_tool_op("not-a-real-op")

    def test_registered_ops_present(self):
        for op in ("sweep", "snowball", "coverage", "relations"):
            assert op in auto.OP_REGISTRY

    def test_run_tool_op_calls_through(self, monkeypatch):
        called = {}

        def fake_coverage_report(project, scope, config=None):
            called["args"] = (project, scope, config)
            return {"counts": {"corpus": 0, "materialized": 0, "unmaterialized": 0, "orphan": 0}}

        monkeypatch.setattr("research_vault.review.coverage_report", fake_coverage_report)
        result = auto.run_tool_op("coverage", project="proj", scope="scope1", config="cfg-sentinel")
        assert called["args"] == ("proj", "scope1", "cfg-sentinel")
        assert result["counts"]["corpus"] == 0

    def test_every_op_import_actually_resolves(self):
        """★ Non-monkeypatched registry import-resolution check.

        `test_registered_ops_present` only checks key-presence; the calls-
        through tests inject fakes via monkeypatch. Neither exercises the
        REAL lazy `from <module> import <Name>` statement inside each op
        body — which is exactly how `_op_snowball_forward`/`_op_snowball_
        backward` shipped pointed at the nonexistent
        `research_vault.adapters.semantic_scholar` (should have been
        `research_vault.sources.semantic_scholar`) and went undetected: CI
        stayed green because nothing ever imported the real module. The two
        removed single-paper ops are gone (collapsed into `_op_snowball`,
        review-loop-nodekind-drift-fix), so this now covers `sweep`,
        `snowball`, `coverage`, `relations`.

        This test parses each op's real source for its `from X import Y`
        statement(s) and does a genuine `importlib.import_module(X)` +
        `getattr(mod, Y)` — no fakes, no injection. A bad module path or a
        renamed/missing symbol fails this test loudly.
        """
        for op_name, fn in auto.OP_REGISTRY.items():
            src = inspect.getsource(fn)
            tree = ast.parse(src)
            import_nodes = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
            ]
            assert import_nodes, f"op {op_name!r} has no lazy import to verify"
            for node in import_nodes:
                assert node.module, f"op {op_name!r}: relative import has no module"
                mod = importlib.import_module(node.module)
                for alias in node.names:
                    imported_name = alias.asname or alias.name
                    assert hasattr(mod, alias.name), (
                        f"op {op_name!r}: {node.module!r} has no attribute "
                        f"{alias.name!r} (imported as {imported_name!r})"
                    )
