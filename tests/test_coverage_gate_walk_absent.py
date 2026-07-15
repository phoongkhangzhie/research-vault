# SPDX-License-Identifier: AGPL-3.0-or-later
"""test_coverage_gate_walk_absent.py — coverage-gate refactor (search-primary
redesign, Section D part 1): ``_evaluate_autonomous_gate``'s coverage-gate
branch certifies correctly in BOTH worlds — a walk ran (unchanged, pre-
existing behavior) and no walk ran at all (the surgical-walk-absent steady
state this refactor makes safe).

Mirrors ``test_review_ledger_wiring.py``'s ``TestMissingWalkProducerWritesLedger``
harness (reused, not reinvented — charter §6): hand-build a ``nodes_lookup``
+ ``RunState`` and call ``_evaluate_autonomous_gate`` directly — the SAME
real production function ``cmd_tick``'s self-advancing runner dispatches
to. This is the correct seam for exercising a walk-absent evaluation
TODAY: the shipped manifest still declares ``review-snowball``'s
``produces`` unconditionally (``_corpus_raw.md`` + ``_walk.md``), so the
tool-node auto-executor's own fail-closed "declared produces artifact
missing on disk" check would block BEFORE coverage-gate is ever reached if
driven through the full ``cmd_tick`` DAG-runner path with a fake snowball
op that skips writing ``_walk.md`` — that structural manifest lever is the
follow-up PR's job (the surgical-walk modes + manifest default change), NOT
this one. Calling ``_evaluate_autonomous_gate`` directly is the honest way
to prove the GATE's own contract is correct for a world where a
``review-snowball``-shaped node declares (but doesn't always write) a walk
record, without touching the manifest ahead of that follow-up.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _search_hits_note(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\ndark_sources: \n---\n\n# fake search hits\n", encoding="utf-8")


def _corpus_note(path: Path, citekeys: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(f"| [NEW] | {ck} | Paper {ck} |" for ck in citekeys)
    path.write_text(
        "| annotation | citekey | title |\n|---|---|---|\n" + rows + "\n",
        encoding="utf-8",
    )


def _relevance_verdict_note(path: Path, citekeys: list[str], *, empty: bool = False) -> None:
    from research_vault.review.relevance import (
        CANARY_IN_SCOPE_CITEKEY,
        CANARY_OFF_DOMAIN_CITEKEY,
        IN,
        OFF_DOMAIN,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    if empty:
        # A genuinely-missing required signal: the artifact exists but
        # carries NO verdicts at all (not even the canaries) — must
        # fail-closed regardless of whether a walk ran.
        path.write_text("| Citekey | Verdict |\n|---|---|\n", encoding="utf-8")
        return
    lines = ["| Citekey | Verdict |", "|---|---|"]
    for ck in citekeys:
        lines.append(f"| {ck} | {IN} |")
    lines.append(f"| {CANARY_IN_SCOPE_CITEKEY} | {IN} |")
    lines.append(f"| {CANARY_OFF_DOMAIN_CITEKEY} | {OFF_DOMAIN} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_review(cfg, scope: str):
    from research_vault.review import cmd_new

    _note_path, review_dir, phase1 = cmd_new(
        "demo-research", scope, question="Does X generalize across Y?", config=cfg,
    )
    manifest_path = review_dir / "phase1-dag.json"
    return review_dir, manifest_path, phase1


def _run_state(phase1, manifest_path: Path):
    from research_vault.dag.store import RunState

    return RunState(run_id=phase1["run_id"], manifest_path=str(manifest_path))


def _nodes_lookup(review_dir: Path, *, write_walk: bool) -> dict:
    """A hand-built ``nodes_lookup`` shaped exactly like the real manifest's
    (``review-search``/``review-snowball``/``review-relevance-verify``
    entries — the only three ``_evaluate_autonomous_gate``'s coverage-gate
    branch reads), pointing at real files under ``review_dir``. ``review-
    snowball`` still DECLARES a ``_walk.md`` produces path (the manifest is
    untouched by this PR) — whether the file is actually written is
    controlled by the caller, exactly mirroring "a walk fired this
    evaluation" vs. "no walk fired" (the surgical-walk-absent steady
    state)."""
    walk_path = review_dir / "_walk.md"
    corpus_raw_path = review_dir / "_corpus_raw.md"
    search_hits_path = review_dir / "_search_hits.md"
    relevance_verdict_path = review_dir / "_relevance-verdict.md"

    if write_walk:
        walk_path.write_text(
            "---\nstop_reason: walk-complete:1-hops\n---\n\n"
            "| Round | New (forward) | New (backward) | New independent | Cumulative | Direction-starved |\n"
            "|---|---|---|---|---|---|\n"
            "| 1 | 2 | 0 | 2 | 2 |  |\n",
            encoding="utf-8",
        )
    corpus_raw_path.write_text("| [NEW] | paper02024 | Paper 0 |\n", encoding="utf-8")

    return {
        "review-search": {
            "id": "review-search",
            "produces": {"_search_hits.md": str(search_hits_path)},
        },
        "review-snowball": {
            "id": "review-snowball",
            "produces": {
                "_corpus_raw.md": str(corpus_raw_path),
                "_walk.md": str(walk_path),
            },
        },
        "review-relevance-verify": {
            "id": "review-relevance-verify",
            "produces": {"_relevance-verdict.md": str(relevance_verdict_path)},
        },
    }


class TestCoverageGateCertifiesBothWorlds:
    def test_walk_ran_still_certifies_correctly(self, tmp_instance: Path):
        """Regression pin (a): the pre-existing, walk-ran world is
        unaffected by the refactor — a clean walk-complete terminal still
        certifies GO exactly as before."""
        from research_vault.config import load_config
        from research_vault.dag.verbs import _evaluate_autonomous_gate
        from research_vault.review import autonomy as _auto

        cfg = load_config()
        review_dir, manifest_path, phase1 = _build_review(cfg, scope="scope-walk-ran")
        nodes_lookup = _nodes_lookup(review_dir, write_walk=True)
        citekeys = ["paper02024"]
        _search_hits_note(review_dir / "_search_hits.md")
        _corpus_note(review_dir / "_corpus.md", citekeys)
        _relevance_verdict_note(review_dir / "_relevance-verdict.md", citekeys)
        (review_dir / "_protocol.md").write_text(
            "---\ncounter-position: a real counter-position\n---\n\nProtocol.\n", encoding="utf-8",
        )
        run_state = _run_state(phase1, manifest_path)

        disposition = _evaluate_autonomous_gate(
            "coverage-gate", nodes_lookup, manifest_path, run_state,
            manifest={"project": "demo-research"},
        )
        assert disposition.disposition == _auto.GO
        assert disposition.evidence.get("stop_reason") == "walk-complete:1-hops"

    def test_walk_absent_still_certifies_go(self, tmp_instance: Path):
        """The key new behavior (b): no ``_walk.md`` on disk at all this
        evaluation (the surgical-walk-absent steady state) must NOT halt —
        the gate certifies on facet-coverage + source-coverage +
        relevance-verify + deviation-check alone."""
        from research_vault.config import load_config
        from research_vault.dag.verbs import _evaluate_autonomous_gate
        from research_vault.review import autonomy as _auto

        cfg = load_config()
        review_dir, manifest_path, phase1 = _build_review(cfg, scope="scope-walk-absent")
        nodes_lookup = _nodes_lookup(review_dir, write_walk=False)
        assert not (review_dir / "_walk.md").exists(), "fixture must not have written _walk.md"
        citekeys = ["paper02024"]
        _search_hits_note(review_dir / "_search_hits.md")
        _corpus_note(review_dir / "_corpus.md", citekeys)
        _relevance_verdict_note(review_dir / "_relevance-verdict.md", citekeys)
        (review_dir / "_protocol.md").write_text(
            "---\ncounter-position: a real counter-position\n---\n\nProtocol.\n", encoding="utf-8",
        )
        run_state = _run_state(phase1, manifest_path)

        disposition = _evaluate_autonomous_gate(
            "coverage-gate", nodes_lookup, manifest_path, run_state,
            manifest={"project": "demo-research"},
        )
        assert disposition.disposition == _auto.GO, (
            f"coverage-gate must certify GO purely from facet/source/"
            f"relevance/deviation signals when no walk ran this evaluation "
            f"-- an absent _walk.md is a valid, expected surgical-only "
            f"state, never a fail-closed HALT. Got: {disposition.disposition} "
            f"({disposition.reason})"
        )
        assert disposition.evidence.get("walk_ran") is False

    def test_missing_relevance_verdicts_halts_even_with_walk_absent(self, tmp_instance: Path):
        """A genuinely-missing REQUIRED signal (c) — zero relevance-verify
        verdicts recorded — must still HALT, walk or no walk. Demoting the
        walk-terminal to a conditional contributor must never widen the
        fail-closed floor on the signals that ARE required."""
        from research_vault.config import load_config
        from research_vault.dag.verbs import _evaluate_autonomous_gate
        from research_vault.review import autonomy as _auto

        cfg = load_config()
        review_dir, manifest_path, phase1 = _build_review(cfg, scope="scope-empty-verdicts")
        nodes_lookup = _nodes_lookup(review_dir, write_walk=False)
        citekeys = ["paper02024"]
        _search_hits_note(review_dir / "_search_hits.md")
        _corpus_note(review_dir / "_corpus.md", citekeys)
        _relevance_verdict_note(review_dir / "_relevance-verdict.md", citekeys, empty=True)
        (review_dir / "_protocol.md").write_text(
            "---\ncounter-position: a real counter-position\n---\n\nProtocol.\n", encoding="utf-8",
        )
        run_state = _run_state(phase1, manifest_path)

        disposition = _evaluate_autonomous_gate(
            "coverage-gate", nodes_lookup, manifest_path, run_state,
            manifest={"project": "demo-research"},
        )
        assert disposition.disposition == _auto.HALT_DECLARE, (
            "an empty relevance-verify verdict set is a genuinely-missing "
            "required signal and must fail-closed to a HALT-DECLARE, "
            "independent of whether a walk ran."
        )
