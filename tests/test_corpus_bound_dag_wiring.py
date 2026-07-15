# SPDX-License-Identifier: AGPL-3.0-or-later
"""test_corpus_bound_dag_wiring.py — Section C (task #86): the REAL
self-advancing DAG runner drives a review through coverage-gate and the
stratified corpus-bound selection actually mutates ``_corpus.md`` (trims
[NEW] rows beyond ``corpus_bound``, declares the residue, and leaves
[IN-CORPUS:*] rows untouched) — not just the pure ``review.corpus_bound``
unit tests, but the real wiring in ``dag/verbs.py``'s coverage-gate branch.

Mirrors ``test_review_ledger_wiring.py``'s harness (reused, not
reinvented — charter §6): a fake sweep/snowball op registry, a hand-
written ``_corpus.md``, and a hand-written ``_relevance-verdict.md`` drive
the SAME real ``cmd_tick``/``cmd_approve`` path production code runs.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _mark_succeeded(store, run_id: str, node_id: str) -> None:
    from research_vault.dag.verbs import cmd_complete

    rc = cmd_complete(argparse.Namespace(run_id=run_id, node_id=node_id, status="succeeded"))
    assert rc == 0, f"cmd_complete({node_id}) failed"


class TestCorpusBoundAppliedAtCoverageGate:
    def _kick_review(self, cfg, scope: str):
        from research_vault.dag.store import RunStore
        from research_vault.dag.verbs import cmd_run
        from research_vault.review import cmd_new

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
        self, run_id: str, review_dir: Path, store, cfg, *, n_new: int, monkeypatch,
    ):
        from research_vault.dag.verbs import cmd_approve, cmd_tick
        from research_vault.review import autonomy as _auto
        from research_vault.review.relevance import (
            CANARY_IN_SCOPE_CITEKEY,
            CANARY_OFF_DOMAIN_CITEKEY,
            IN,
            OFF_DOMAIN,
        )

        citekeys = [f"paper{i}2024" for i in range(n_new)]

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
                "\n".join(f"| [NEW] | {ck} | Paper {ck} |" for ck in citekeys) + "\n",
                encoding="utf-8",
            )
            (out / "_walk.md").write_text(
                "---\nstop_reason: walk-complete:1-hops\n---\n\n"
                "| Round | New (forward) | New (backward) | New independent | Cumulative | Direction-starved |\n"
                "|---|---|---|---|---|---|\n"
                f"| 1 | {n_new} | 0 | {n_new} | {n_new} |  |\n",
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
        _mark_succeeded(store, run_id, "review-scope")
        cmd_tick(argparse.Namespace(run_id=run_id))
        rs = store.load(run_id)
        assert rs.node_status("approve-protocol") == "awaiting-go"
        rc = cmd_approve(argparse.Namespace(
            run_id=run_id, node_id="approve-protocol", note=None, output=[], reject=False, auto=False,
        ))
        assert rc == 0

        screen_path = review_dir / "_screen.md"
        screen_path.write_text("\n".join(f"10.1/{ck}" for ck in citekeys) + "\n", encoding="utf-8")
        _mark_succeeded(store, run_id, "review-screen")

        cmd_tick(argparse.Namespace(run_id=run_id))
        rs = store.load(run_id)
        assert rs.node_status("review-snowball") == "succeeded"

        # The curate agent's final _corpus.md — carrying declared poles so
        # the stratification quota has something real to bucket on. Every
        # other candidate matches "x.thesis"; every third one ALSO matches
        # "y.counter" (2 poles, stronger composite) so the strength order
        # is exercised, not just the floor/proportional math.
        corpus_path = review_dir / "_corpus.md"
        rows = []
        for i, ck in enumerate(citekeys):
            poles = "x.thesis, y.counter" if i % 3 == 0 else "x.thesis"
            rows.append(f"| [NEW] | {ck} | Paper {ck} | abstract text | — | {poles} |")
        rows.append("| [IN-CORPUS:already2019] | already2019 | Already accepted | | | |")
        corpus_path.write_text(
            "| Annotation | Citekey | Title | Abstract | Rerank | Poles |\n"
            "|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n",
            encoding="utf-8",
        )
        cmd_tick(argparse.Namespace(run_id=run_id))
        _mark_succeeded(store, run_id, "review-curate")

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

    def test_bound_trims_new_rows_leaves_in_corpus_untouched(self, tmp_instance: Path, monkeypatch):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick
        from research_vault.review import style as _review_style

        cfg = load_config()
        monkeypatch.setattr(_review_style, "get_corpus_bound", lambda *a, **kw: 5)
        monkeypatch.setattr(_review_style, "get_min_hits_per_pole", lambda *a, **kw: 1)

        run_id, review_dir, store = self._kick_review(cfg, scope="scope-corpus-bound")
        self._drive_to_coverage_gate(run_id, review_dir, store, cfg, n_new=12, monkeypatch=monkeypatch)

        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("coverage-gate") == "succeeded"

        corpus_text = (review_dir / "_corpus.md").read_text(encoding="utf-8")
        # exactly 5 [NEW] rows survive (corpus_bound=5), the [IN-CORPUS]
        # row is untouched regardless of the bound.
        new_row_count = sum(1 for line in corpus_text.splitlines() if line.strip().startswith("| [NEW]"))
        assert new_row_count == 5
        assert "already2019" in corpus_text

        # The strongest composite rows (2-pole matches at i%3==0: paper0,
        # paper3, paper6, paper9) rank first within their pole bucket —
        # the weakest single-pole, worst-sweep-rank rows are the ones cut.
        assert "paper0" in corpus_text

        residue_path = review_dir / "_corpus-bound-residue.md"
        assert residue_path.exists(), "coverage-gate must declare the bound-driven residue"
        residue_text = residue_path.read_text(encoding="utf-8")
        assert "Dropped" in residue_text

    def test_bound_is_a_no_op_when_pool_fits_within_bound(self, tmp_instance: Path, monkeypatch):
        from research_vault.config import load_config
        from research_vault.dag.verbs import cmd_tick
        from research_vault.review import style as _review_style

        cfg = load_config()
        monkeypatch.setattr(_review_style, "get_corpus_bound", lambda *a, **kw: 100)
        monkeypatch.setattr(_review_style, "get_min_hits_per_pole", lambda *a, **kw: 1)

        run_id, review_dir, store = self._kick_review(cfg, scope="scope-corpus-bound-fits")
        self._drive_to_coverage_gate(run_id, review_dir, store, cfg, n_new=3, monkeypatch=monkeypatch)

        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("coverage-gate") == "succeeded"

        corpus_text = (review_dir / "_corpus.md").read_text(encoding="utf-8")
        new_row_count = sum(1 for line in corpus_text.splitlines() if line.strip().startswith("| [NEW]"))
        assert new_row_count == 3
