# SPDX-License-Identifier: AGPL-3.0-or-later
"""tests/test_relevance_gate_integration.py — PR-1: the REQUIRED real-runner
integration test for the trustworthy-curation relevance gate (design
2026-07-10-trustworthy-curation-relevance-gate-design.md).

Drives the REAL DAG runner (``cmd_run``/``cmd_tick``/``cmd_approve``/
``cmd_complete``) through
``review-curate -> review-relevance-verify -> coverage-gate`` on a fixture
with planted contaminants, mirroring the acceptance criteria in the PR-1
dispatch brief:

  1. Snowball-screen (mechanical, real op — never mocked): a planted
     astronomy contaminant and a planted materials-physics contaminant are
     REJECTED before review-curate ever sees the pool; a planted
     boundary/disconfirming paper (named in counter-position) SURVIVES.
  2. Cold-verifier canary: an unmarked in-scope + an unmarked off-domain
     probe must both classify correctly, else the run aborts (HALT).
  3. coverage-gate disposition, mutation-tested at the ~15-20% boundary: a
     small off-domain residue (below threshold) auto-prunes and proceeds
     (GO-WITH-RESIDUE); a large fraction (at/above threshold) HALTs, corpus
     untouched.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


_PROTOCOL_TEXT = (
    "---\n"
    "question: Do large language models exhibit consistent, measurable "
    "cultural and social behavioral competence across diverse human "
    "populations?\n"
    "inclusion: Papers that measure a language model's cultural values, "
    "social norms, or behavioral competence, including cross-cultural "
    "psychometric evaluation of LLMs.\n"
    "exclusion: Papers with no language model or behavioral/cultural "
    "construct.\n"
    "coverage_claim: All English-language papers 2020-2026 evaluating LLM "
    "cultural or social behavior against human population baselines.\n"
    "counter-position: The default-model literature that assumes a single "
    "unmarked WEIRD (Western, Educated, Industrialized, Rich, Democratic) "
    "culture as the implicit human baseline without cross-cultural framing.\n"
    "---\n\n# Protocol\n"
)

_CORPUS_RAW_ROWS = (
    # In-scope survivor.
    "| [NEW] | 10.1/llm2024 | Cross-cultural evaluation of LLM value alignment "
    "| | | We measure large language models' cultural values and social norm "
    "adherence across a diverse sample of human populations, comparing model "
    "responses to the World Values Survey. | |\n"
    # Real contamination class #1: an astronomy survey.
    "| [NEW] | 10.1/astro2024 | A wide-field spectroscopic survey of "
    "methanol masers in star-forming regions | | | We present a "
    "spectroscopic survey of 3,500 galactic methanol maser sources, "
    "deriving distance estimates via trigonometric parallax and "
    "characterizing the spatial distribution of star-forming regions "
    "across the Milky Way disk. | |\n"
    # Real contamination class #2: materials physics.
    "| [NEW] | 10.1/materials2024 | Strain relaxation in Silicon-on-Sapphire "
    "epitaxial heterostructures | | | We investigate strain relaxation "
    "mechanisms in Silicon-on-Sapphire epitaxial thin films grown via "
    "molecular beam epitaxy, measuring dislocation density and lattice "
    "mismatch as a function of annealing temperature. | |\n"
    # Boundary/disconfirming paper named in counter-position — the
    # recall-protection test that matters most.
    "| [NEW] | 10.1/weird2024 | Benchmarking language models on standard "
    "English NLU tasks | | | We evaluate several large language models "
    "purely on English GLUE/SuperGLUE benchmarks, implicitly treating a "
    "single WEIRD (Western, Educated, Industrialized, Rich, Democratic) "
    "population as the default human baseline with no cross-cultural "
    "framing. | |\n"
)


def _drive_to_relevance_screen(monkeypatch, tmp_instance: Path, scope: str):
    """review-scope -> approve-protocol -> review-search(tool, fake) ->
    review-screen(agent) -> review-snowball(tool, fake, writes the planted
    ``_corpus_raw.md`` above) -> review-relevance-screen (TOOL, the REAL
    op — never mocked, this is exactly what's under test)."""
    from research_vault.config import load_config
    from research_vault.dag.verbs import cmd_run, cmd_tick, cmd_approve, cmd_complete
    from research_vault.dag.store import RunStore
    from research_vault.review import cmd_new, autonomy as _auto

    cfg = load_config()
    note_path, review_dir, phase1 = cmd_new(
        "demo-research", scope, question="Does X generalize across Y?", config=cfg,
    )
    manifest_path = review_dir / "phase1-dag.json"
    rc = cmd_run(argparse.Namespace(manifest=str(manifest_path)))
    assert rc == 0
    run_id = phase1["run_id"]
    store = RunStore.from_config(cfg)

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
            "| Annotation | Paper-id | Title | Venue | Year | Abstract/TL;DR | Flags |\n"
            "|---|---|---|---|---|---|---|\n" + _CORPUS_RAW_ROWS,
            encoding="utf-8",
        )
        (out / "_saturation.md").write_text(
            "---\nstop_reason: saturated\n---\n\nSaturation curve.\n", encoding="utf-8",
        )
        return {"stop_reason": "saturated"}

    monkeypatch.setitem(_auto.OP_REGISTRY, "sweep", _fake_sweep)
    monkeypatch.setitem(_auto.OP_REGISTRY, "snowball", _fake_snowball)

    protocol_path = review_dir / "_protocol.md"
    protocol_path.write_text(_PROTOCOL_TEXT, encoding="utf-8")
    cmd_complete(argparse.Namespace(run_id=run_id, node_id="review-scope", status="succeeded"))
    cmd_tick(argparse.Namespace(run_id=run_id))
    rc = cmd_approve(argparse.Namespace(
        run_id=run_id, node_id="approve-protocol", note=None, output=[], reject=False, auto=False,
    ))
    assert rc == 0  # review-search (fake tool) auto-executed in this same call

    screen_path = review_dir / "_screen.md"
    screen_path.write_text(
        "10.1/llm2024\n10.1/astro2024\n10.1/materials2024\n10.1/weird2024\n", encoding="utf-8",
    )
    rc = cmd_complete(argparse.Namespace(run_id=run_id, node_id="review-screen", status="succeeded"))
    assert rc == 0  # review-snowball (fake tool) auto-executed; then
    # review-relevance-screen (REAL op, never mocked) auto-executes too.

    return run_id, review_dir, store


class TestSnowballScreenRealRunner:
    """Item 1 — the mechanical relevance-screen gate, driven for real."""

    def test_contaminants_rejected_boundary_paper_survives(self, tmp_instance: Path, monkeypatch):
        run_id, review_dir, store = _drive_to_relevance_screen(monkeypatch, tmp_instance, "scope-relgate-screen")

        rs = store.load(run_id)
        assert rs.node_status("review-relevance-screen") == "succeeded", (
            rs.node_states.get("review-relevance-screen")
        )

        screened_path = review_dir / "_corpus_raw_screened.md"
        assert screened_path.exists()
        screened_text = screened_path.read_text(encoding="utf-8")

        kept_region = screened_text.split("## Rejected as off-domain")[0]
        # SURVIVES: in-scope + boundary/disconfirming papers both kept.
        assert "10.1/llm2024" in kept_region
        assert "10.1/weird2024" in kept_region
        # REJECTED: both real contamination classes, never silently dropped
        # (preserved in the audit section, absent from the kept region).
        assert "10.1/astro2024" not in kept_region
        assert "10.1/materials2024" not in kept_region
        assert "## Rejected as off-domain" in screened_text
        assert "10.1/astro2024" in screened_text
        assert "10.1/materials2024" in screened_text

        # review-curate (agent) is what's dispatch-ready next — it reads the
        # SCREENED pool, not the raw one.
        curate_node = next(
            n for n in __import__("json").loads(
                (review_dir / "phase1-dag.json").read_text(encoding="utf-8")
            )["nodes"]
            if n["id"] == "review-curate"
        )
        assert str(screened_path) in curate_node["reads"]
        assert str(review_dir / "_corpus_raw.md") not in curate_node["reads"]


def _complete_curate_and_verify(
    monkeypatch, run_id: str, review_dir: Path, store, *,
    corpus_rows: list[tuple[str, str, str]],
    verify_verdicts: dict[str, str] | None = None,
    skip_canaries: bool = False,
) -> None:
    """review-curate (agent) "completes": writes the FINAL _corpus.md with
    an abstract column (PR-1's curate output contract extension).
    review-relevance-verify-prep (TOOL, real op) auto-executes.
    review-relevance-verify (COLD agent) "completes" with the caller-
    supplied verdict table (canary-correct by default; callers exercising
    the abort/HALT paths override ``verify_verdicts``/``skip_canaries``)."""
    from research_vault.dag.verbs import cmd_tick, cmd_complete
    from research_vault.review.relevance import (
        CANARY_IN_SCOPE_CITEKEY, CANARY_OFF_DOMAIN_CITEKEY, IN, OFF_DOMAIN,
    )

    corpus_path = review_dir / "_corpus.md"
    lines = ["| annotation | citekey | title | abstract |", "|---|---|---|---|"]
    for citekey, title, abstract in corpus_rows:
        lines.append(f"| [NEW] | {citekey} | {title} | {abstract} |")
    corpus_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    rc = cmd_complete(argparse.Namespace(run_id=run_id, node_id="review-curate", status="succeeded"))
    assert rc == 0

    cmd_tick(argparse.Namespace(run_id=run_id))  # review-relevance-verify-prep (real op)
    rs = store.load(run_id)
    assert rs.node_status("review-relevance-verify-prep") == "succeeded", (
        rs.node_states.get("review-relevance-verify-prep")
    )

    verdicts = dict(verify_verdicts or {ck: IN for ck, _, _ in corpus_rows})
    verdict_lines = ["| Citekey | Verdict |", "|---|---|"]
    for ck, v in verdicts.items():
        verdict_lines.append(f"| {ck} | {v} |")
    if not skip_canaries:
        verdict_lines.append(f"| {CANARY_IN_SCOPE_CITEKEY} | {IN} |")
        verdict_lines.append(f"| {CANARY_OFF_DOMAIN_CITEKEY} | {OFF_DOMAIN} |")

    (review_dir / "_relevance-verdict.md").write_text(
        "\n".join(verdict_lines) + "\n", encoding="utf-8",
    )
    rc = cmd_complete(argparse.Namespace(run_id=run_id, node_id="review-relevance-verify", status="succeeded"))
    assert rc == 0


class TestColdVerifierRealRunner:
    """Items 2 + 3 — the cold verifier's canary + coverage-gate disposition,
    driven for real through curate -> verifier -> coverage-gate."""

    def test_clean_corpus_goes(self, tmp_instance: Path, monkeypatch):
        from research_vault.dag.verbs import cmd_tick
        run_id, review_dir, store = _drive_to_relevance_screen(monkeypatch, tmp_instance, "scope-relgate-go")

        _complete_curate_and_verify(
            monkeypatch, run_id, review_dir, store,
            corpus_rows=[
                ("llm2024", "Cross-cultural evaluation of LLM value alignment", "measures cultural values"),
                ("weird2024", "Benchmarking on English NLU tasks", "WEIRD default baseline"),
            ],
        )
        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("coverage-gate") == "succeeded"
        assert "GO" in rs.node_states["coverage-gate"]["decision_note"]
        assert "HALT" not in rs.node_states["coverage-gate"]["decision_note"]

    def test_canary_abort_halts_the_whole_run(self, tmp_instance: Path, monkeypatch):
        """The judge missed the off-domain canary — untrustworthy signal,
        fail-closed, never a silent GO."""
        from research_vault.dag.verbs import cmd_tick
        from research_vault.review.relevance import IN

        run_id, review_dir, store = _drive_to_relevance_screen(monkeypatch, tmp_instance, "scope-relgate-canary")

        _complete_curate_and_verify(
            monkeypatch, run_id, review_dir, store,
            corpus_rows=[("llm2024", "Cross-cultural LLM study", "measures cultural values")],
            skip_canaries=True,
        )
        # Overwrite the verdict with a canary MISS: off-domain canary
        # classified IN (the judge missed it).
        from research_vault.review.relevance import CANARY_IN_SCOPE_CITEKEY, CANARY_OFF_DOMAIN_CITEKEY
        (review_dir / "_relevance-verdict.md").write_text(
            "| Citekey | Verdict |\n|---|---|\n"
            f"| llm2024 | {IN} |\n"
            f"| {CANARY_IN_SCOPE_CITEKEY} | {IN} |\n"
            f"| {CANARY_OFF_DOMAIN_CITEKEY} | {IN} |\n",
            encoding="utf-8",
        )

        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("coverage-gate") == "blocked"
        assert "HALT-DECLARE" in rs.node_states["coverage-gate"]["decision_note"]
        assert "canary" in rs.node_states["coverage-gate"]["decision_note"].lower()
        # Never GO'd — no Phase-2 auto-emitted on a canary abort.
        assert not (review_dir / "phase2-dag.json").exists()

    def test_small_off_domain_fraction_auto_prunes_and_proceeds(self, tmp_instance: Path, monkeypatch):
        """Mutation-test the ~15-20% HALT boundary: a fixture at ~5%
        off-domain must auto-prune + proceed, never HALT."""
        from research_vault.dag.verbs import cmd_tick
        from research_vault.review.relevance import IN, OFF_DOMAIN

        run_id, review_dir, store = _drive_to_relevance_screen(monkeypatch, tmp_instance, "scope-relgate-prune")

        rows = [(f"paper{i}", f"Paper {i}", "in-scope substance") for i in range(19)]
        rows.append(("slipped2024", "A paper that slipped past curate", "off-domain substance"))
        verdicts = {ck: IN for ck, _, _ in rows}
        verdicts["slipped2024"] = OFF_DOMAIN  # 1/20 = 5%

        _complete_curate_and_verify(
            monkeypatch, run_id, review_dir, store, corpus_rows=rows, verify_verdicts=verdicts,
        )

        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("coverage-gate") == "succeeded"
        assert "GO-WITH-RESIDUE" in rs.node_states["coverage-gate"]["decision_note"]

        # The corpus was ACTUALLY pruned (not just a paper claim in the
        # decision note) — verify against the real file on disk.
        corpus_text = (review_dir / "_corpus.md").read_text(encoding="utf-8")
        assert "slipped2024" not in corpus_text
        assert "paper0" in corpus_text

        residue_path = review_dir / "_relevance-residue.md"
        assert residue_path.exists()
        assert "slipped2024" in residue_path.read_text(encoding="utf-8")

        # Still proceeds — Phase-2 auto-emitted exactly like a clean GO.
        assert (review_dir / "phase2-dag.json").exists()

    def test_large_off_domain_fraction_halts_corpus_untouched(self, tmp_instance: Path, monkeypatch):
        """Mutation-test the ~15-20% HALT boundary: a fixture at ~50%
        off-domain (the real grounding-run 51/97 ratio) must HALT, corpus
        left UNTOUCHED (never silently pruning half the corpus)."""
        from research_vault.dag.verbs import cmd_tick
        from research_vault.review.relevance import IN, OFF_DOMAIN

        run_id, review_dir, store = _drive_to_relevance_screen(monkeypatch, tmp_instance, "scope-relgate-halt")

        rows = [(f"good{i}", f"Good paper {i}", "in-scope substance") for i in range(5)]
        rows += [(f"bad{i}", f"Contaminant {i}", "off-domain substance") for i in range(5)]
        verdicts = {ck: (OFF_DOMAIN if ck.startswith("bad") else IN) for ck, _, _ in rows}

        _complete_curate_and_verify(
            monkeypatch, run_id, review_dir, store, corpus_rows=rows, verify_verdicts=verdicts,
        )

        corpus_text_before = (review_dir / "_corpus.md").read_text(encoding="utf-8")

        rc = cmd_tick(argparse.Namespace(run_id=run_id))
        assert rc == 0
        rs = store.load(run_id)
        assert rs.node_status("coverage-gate") == "blocked"
        assert "HALT-DECLARE" in rs.node_states["coverage-gate"]["decision_note"]
        assert not (review_dir / "phase2-dag.json").exists()

        # Corpus untouched — this is a signal curate/search is broken, not a trim.
        corpus_text_after = (review_dir / "_corpus.md").read_text(encoding="utf-8")
        assert corpus_text_after == corpus_text_before
        assert not (review_dir / "_relevance-residue.md").exists()
