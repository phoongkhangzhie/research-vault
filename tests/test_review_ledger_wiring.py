"""test_review_ledger_wiring.py — PR-5 acceptance (f): the coverage-gate
node writes ``_corpus_ledger.md`` as its FINAL act, driven through the REAL
self-advancing DAG runner (mirrors ``test_ng4b_autonomy_wiring.py``'s
``TestSelfAdvancingRunner`` harness — reused, not reinvented).

Coverage:
  - GO (saturated): coverage-gate resolves, ledger written, ledger_complete
    true (once the literature note fixtures + relevance verdict exist).
  - GO-WITH-RESIDUE (backstop): ledger written, ledger_complete still true
    (all sources present) but stop_reason/bounded_not_saturated reflect the
    backstop residue.
  - HALT-DECLARE (malformed stop_reason): ledger STILL written, with
    ledger_complete: false and the HALT reason surfaced verbatim.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.note import _parse_frontmatter  # noqa: E402


def _mark_succeeded(store, run_id: str, node_id: str) -> None:
    from research_vault.dag.verbs import cmd_complete

    rc = cmd_complete(argparse.Namespace(run_id=run_id, node_id=node_id, status="succeeded"))
    assert rc == 0, f"cmd_complete({node_id}) failed"


def _drive_through_relevance_verify(run_id: str, review_dir: Path, store, citekeys: list[str]) -> None:
    from research_vault.dag.verbs import cmd_tick
    from research_vault.review.relevance import (
        CANARY_IN_SCOPE_CITEKEY, CANARY_OFF_DOMAIN_CITEKEY, IN, OFF_DOMAIN,
    )

    cmd_tick(argparse.Namespace(run_id=run_id))
    rs = store.load(run_id)
    assert rs.node_status("review-relevance-verify-prep") == "succeeded"

    verdict_path = review_dir / "_relevance-verdict.md"
    lines = ["| Citekey | Verdict |", "|---|---|"]
    for ck in citekeys:
        lines.append(f"| {ck} | {IN} |")
    lines.append(f"| {CANARY_IN_SCOPE_CITEKEY} | {IN} |")
    lines.append(f"| {CANARY_OFF_DOMAIN_CITEKEY} | {OFF_DOMAIN} |")
    verdict_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _mark_succeeded(store, run_id, "review-relevance-verify")


class TestCoverageGateWritesLedger:
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
        self, run_id: str, review_dir: Path, store, cfg, *, stop_reason: str, monkeypatch,
    ):
        from research_vault.dag.verbs import cmd_tick, cmd_approve
        from research_vault.review import autonomy as _auto

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
            (out / "_saturation.md").write_text(
                f"---\nstop_reason: {stop_reason}\n---\n\n"
                "| Round | New (forward) | New (backward) | New independent | Cumulative | Direction-starved |\n"
                "|---|---|---|---|---|---|\n"
                "| 1 | 2 | 0 | 2 | 2 |  |\n",
                encoding="utf-8",
            )
            return {"stop_reason": stop_reason}

        monkeypatch.setitem(_auto.OP_REGISTRY, "sweep", _fake_sweep)
        monkeypatch.setitem(_auto.OP_REGISTRY, "snowball", _fake_snowball)

        protocol_path = review_dir / "_protocol.md"
        protocol_path.write_text(
            "---\ncounter-position: a real counter-position\n---\n\nProtocol.\n",
            encoding="utf-8",
        )
        _mark_succeeded(store, run_id, "review-scope")
        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("approve-protocol") == "awaiting-go"

        rc = cmd_approve(argparse.Namespace(
            run_id=run_id, node_id="approve-protocol", note=None, output=[], reject=False, auto=False,
        ))
        assert rc == 0

        screen_path = review_dir / "_screen.md"
        screen_path.write_text("10.1/alpha2024\n10.1/beta2024\n", encoding="utf-8")
        _mark_succeeded(store, run_id, "review-screen")

        cmd_tick(argparse.Namespace(run_id=run_id))
        rs = store.load(run_id)
        assert rs.node_status("review-snowball") == "succeeded"

        corpus_path = review_dir / "_corpus.md"
        corpus_path.write_text(
            "| annotation | citekey | title |\n|---|---|---|\n"
            "| [NEW] | alpha2024 | Alpha paper |\n| [NEW] | beta2024 | Beta paper |\n",
            encoding="utf-8",
        )
        if stop_reason.startswith("backstop:"):
            (review_dir / "_coverage-gaps.md").write_text(
                "terminated by backstop after 3 waves.\n\n- open frontier remains\n",
                encoding="utf-8",
            )
        cmd_tick(argparse.Namespace(run_id=run_id))
        _mark_succeeded(store, run_id, "review-curate")
        _drive_through_relevance_verify(run_id, review_dir, store, ["alpha2024", "beta2024"])

    def _write_literature_notes(self, cfg, project: str = "demo-research"):
        # PR-A: intrinsic fields (citekey/doi/arxiv_id) live on the CENTRAL
        # CORE, not the per-project overlay — the ledger's K-block resolves
        # against cfg.literature_root.
        core_dir = cfg.literature_root
        core_dir.mkdir(parents=True, exist_ok=True)
        (core_dir / "alpha2024.md").write_text(
            "---\ntype: literature\ncitekey: alpha2024\ndoi: 10.1/alpha2024\n---\n", encoding="utf-8",
        )
        (core_dir / "beta2024.md").write_text(
            "---\ntype: literature\ncitekey: beta2024\narxiv_id: 2401.00002\n---\n", encoding="utf-8",
        )
        overlay_dir = cfg.project_notes_dir(project) / "literature"
        overlay_dir.mkdir(parents=True, exist_ok=True)
        (overlay_dir / "alpha2024.md").write_text(
            "---\ntype: literature\ncentral: alpha2024\n---\n", encoding="utf-8",
        )
        (overlay_dir / "beta2024.md").write_text(
            "---\ntype: literature\ncentral: beta2024\n---\n", encoding="utf-8",
        )

    def test_go_saturated_writes_complete_ledger(self, tmp_instance: Path, monkeypatch):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        self._write_literature_notes(cfg)
        run_id, review_dir, store = self._kick_review(cfg, scope="scope-ledger-go")
        self._drive_to_coverage_gate(
            run_id, review_dir, store, cfg, stop_reason="saturated", monkeypatch=monkeypatch,
        )

        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("coverage-gate") == "succeeded"
        assert "GO" in rs.node_states["coverage-gate"]["decision_note"]

        ledger_path = review_dir / "_corpus_ledger.md"
        assert ledger_path.exists(), "coverage-gate GO must write _corpus_ledger.md"
        fields, text = _parse_frontmatter(ledger_path.read_text(encoding="utf-8"))
        assert fields["type"] == "corpus-ledger"
        assert fields["stop_reason"] == "saturated"
        assert str(fields["bounded_not_saturated"]).strip().lower() == "false"
        assert str(fields["ledger_complete"]).strip().lower() == "true", text
        assert int(fields["accepted"]) == 2
        assert "alpha2024" in text and "doi:10.1/alpha2024" in text

    def test_go_with_residue_backstop_reflected_in_ledger(self, tmp_instance: Path, monkeypatch):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        self._write_literature_notes(cfg)
        run_id, review_dir, store = self._kick_review(cfg, scope="scope-ledger-residue")
        self._drive_to_coverage_gate(
            run_id, review_dir, store, cfg, stop_reason="backstop:3-waves", monkeypatch=monkeypatch,
        )

        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("coverage-gate") == "succeeded"
        assert "GO-WITH-RESIDUE" in rs.node_states["coverage-gate"]["decision_note"]

        ledger_path = review_dir / "_corpus_ledger.md"
        assert ledger_path.exists()
        fields, text = _parse_frontmatter(ledger_path.read_text(encoding="utf-8"))
        assert fields["stop_reason"] == "backstop:3-waves"
        assert str(fields["bounded_not_saturated"]).strip().lower() == "true"
        assert "open frontier remains" in fields["open_counter_poles"]
        assert "backstop after 3 waves" in text  # verbatim residue section

    def test_halt_declare_writes_incomplete_ledger_with_reason(self, tmp_instance: Path, monkeypatch):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick

        cfg = load_config()
        self._write_literature_notes(cfg)
        run_id, review_dir, store = self._kick_review(cfg, scope="scope-ledger-halt")
        self._drive_to_coverage_gate(
            run_id, review_dir, store, cfg,
            stop_reason="garbage-not-a-real-reason", monkeypatch=monkeypatch,
        )

        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("coverage-gate") == "blocked"
        assert "HALT-DECLARE" in rs.node_states["coverage-gate"]["decision_note"]

        ledger_path = review_dir / "_corpus_ledger.md"
        assert ledger_path.exists(), "coverage-gate HALT must STILL write a ledger snapshot"
        fields, text = _parse_frontmatter(ledger_path.read_text(encoding="utf-8"))
        assert str(fields["ledger_complete"]).strip().lower() == "false"
        assert "[LEDGER-GAP] HALT:" in text


class TestMissingSaturationProducerWritesLedger:
    """PR-5 fix-round CHANGE 1 (acceptance f): the missing-``_saturation.md``-
    producer HALT (``review-snowball`` node missing/malformed from the
    manifest) used to ``return`` directly, bypassing ``_write_ledger_final_act``
    entirely -- the ONE coverage-gate HALT path that never wrote a ledger
    snapshot. Now it routes through the same wrapper as every other
    coverage-gate disposition."""

    def test_missing_snowball_producer_halt_still_writes_ledger(self, tmp_instance: Path):
        from research_vault.config import load_config
        from research_vault.dag.verbs import _evaluate_autonomous_gate
        from research_vault.dag.store import RunState
        from research_vault.review import autonomy as _auto
        from research_vault.review import cmd_new

        cfg = load_config()
        _note_path, review_dir, phase1 = cmd_new(
            "demo-research", "scope-ledger-missing-producer", config=cfg,
            question="Does X generalize across Y?",
        )
        manifest_path = review_dir / "phase1-dag.json"

        # nodes_lookup deliberately has NO "review-snowball" entry -- the
        # exact malformed/missing-producer case this HALT branch guards.
        nodes_lookup: dict = {}
        run_state = RunState(run_id=phase1["run_id"], manifest_path=str(manifest_path))

        disposition = _evaluate_autonomous_gate(
            "coverage-gate", nodes_lookup, manifest_path, run_state,
        )
        assert disposition.disposition == _auto.HALT_DECLARE
        assert "no _saturation.md producer found" in disposition.reason

        ledger_path = review_dir / "_corpus_ledger.md"
        assert ledger_path.exists(), (
            "the missing-saturation-producer HALT must STILL write a "
            "ledger snapshot, not silently bypass it"
        )
        fields, text = _parse_frontmatter(ledger_path.read_text(encoding="utf-8"))
        assert str(fields["ledger_complete"]).strip().lower() == "false"
        assert "no _saturation.md producer found" in text
