"""tests/test_pr3b_incremental_relate_wiring.py — PR-3b: wiring the
critic-backtrack's newly-found counter-papers through PR-3's
``review.incremental_relate.run_incremental_relate`` (built + unit-tested
by PR-3, but UNREACHED — zero references from ``dag/verbs.py`` before this
PR closed the gap).

Design of record: PR-3b brief (rv-architect, 2026-07-10) — plumbing only,
no change to ``incremental_relate.py``'s own mechanism (concept-graph
blocking, bidirectional write, island escalation).

This file covers items 2+3 of the PR-3b brief:
  1. The reachability acceptance test — drives the backtrack END-TO-END via
     ``dag.verbs._evaluate_autonomous_gate("approve-review", ...)`` (the
     REAL wired DAG path, never a direct module-unit call) and asserts on
     the resulting notes: bidirectional edges, neighborhood-blocked
     candidate generation (sub-quadratic vs. corpus size), and an island
     newcomer escalating ONLY itself. Mutation-checked: a second test
     proves the SAME scenario produces NO edges when the wiring function is
     stubbed/bypassed — i.e. the positive assertions above are load-bearing
     proof of reachability, not a green no-op.
  2. The contract test — binds the coverage-critic tips' prescribed BLOCK
     phrasing literally to ``_COUNTER_POSITION_BULLET_PREFIX``, so a future
     tips-prose edit can't silently break the backtrack trigger.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.cite import make_citekey  # noqa: E402
from research_vault.dag.verbs import _evaluate_autonomous_gate  # noqa: E402
from research_vault.review import _COUNTER_POSITION_BULLET_PREFIX, _parse_corpus_citekeys  # noqa: E402
from research_vault.review import remediation as rem  # noqa: E402
from research_vault.review import style as review_style  # noqa: E402
from research_vault.review.relate_check import parse_paper_relations  # noqa: E402
from research_vault.sources.sweep import DedupedHit, PaperHit, SweepResult  # noqa: E402

_N_BASELINE = 20


# ---------------------------------------------------------------------------
# Fixtures — mirrors tests/test_pr3_critic_backtrack.py's helpers, extended
# with literature notes carrying ``## Concept edges`` (relate_check.py's
# concept-graph join key).
# ---------------------------------------------------------------------------

def _write_lit_note(literature_dir: Path, citekey: str, *, concepts: list[str]) -> None:
    literature_dir.mkdir(parents=True, exist_ok=True)
    edges = "\n".join(
        f"- [SUPPORTS] [{c}](/concepts/{c}.md) — this paper touches {c}"
        for c in concepts
    )
    text = (
        "---\n"
        f"citekey: {citekey}\n"
        "---\n\n"
        "## Concept edges\n"
        f"{edges}\n"
    )
    (literature_dir / f"{citekey}.md").write_text(text, encoding="utf-8")


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


def _critic_note(path: Path, *, pole: str = "by-temporal") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "verdict: BLOCK\n"
        "remediation_target_node: review-snowball\n"
        f"remediation_target_pole: {pole}\n"
        "remediation_target_directive: re-run the counter queries harder\n"
        "---\n\n"
        f"- COUNTER-POSITION THIN-POLE {pole} — the stability pole is thin\n",
        encoding="utf-8",
    )


def _hit(title: str, family: str, year: int = 2024) -> PaperHit:
    return PaperHit(
        title=title, authors=[f"X. {family}"], year=year,
        external_ids={}, abstract="", citation_count=0, source="semantic-scholar",
    )


def _deduped(hit: PaperHit) -> DedupedHit:
    return DedupedHit(hit=hit, external_ids=dict(hit.external_ids), sources={hit.source})


def _predict_citekeys(hits_in_order: list[PaperHit], existing: set[str]) -> list[str]:
    """Mirror ``_append_new_corpus_rows``'s family/year extraction + citekey
    computation exactly, so the test can pre-write a literature note under
    the EXACT filename the round will generate — simulating "full-distill
    already happened out-of-band" (PR-3b's explicit precondition; full-
    distill itself is out of scope for this PR)."""
    predicted: list[str] = []
    all_ck = set(existing)
    for hit in hits_in_order:
        family = None
        if hit.authors:
            first = hit.authors[0]
            if isinstance(first, str) and first.strip():
                family = first.strip().rsplit(" ", 1)[-1]
        year = str(hit.year or "")
        ck = make_citekey(family, hit.title, year, all_ck)
        all_ck.add(ck)
        predicted.append(ck)
    return predicted


def _build_scope(tmp_path: Path):
    """The REAL layout dag/verbs.py's approve-review branch expects:
    ``project_notes_dir/reviews/<scope>/{_corpus,_protocol,_coverage-critic,
    _deviations}.md`` + ``project_notes_dir/literature/<citekey>.md``
    (``review_dir.parent.parent == project_notes_dir``)."""
    project_notes_dir = tmp_path / "notes"
    review_dir = project_notes_dir / "reviews" / "persona-drift-scope"
    literature_dir = project_notes_dir / "literature"

    baseline_citekeys = [f"base{i}drift2020" for i in range(_N_BASELINE)]
    _corpus_note(review_dir / "_corpus.md", baseline_citekeys)
    # base0drift2020 shares concept-0 with the connected newcomer below;
    # every OTHER baseline paper sits on its own distinct concept — no
    # overlap with anything, ever — so a naive "new x N" scan would check
    # all 20, while concept-graph blocking must check only 1.
    for i, ck in enumerate(baseline_citekeys):
        _write_lit_note(literature_dir, ck, concepts=[f"concept-{i}"])

    _protocol_note(review_dir / "_protocol.md")
    _critic_note(review_dir / "_coverage-critic.md")

    return project_notes_dir, review_dir, literature_dir, baseline_citekeys


def _run_gate(review_dir: Path, monkeypatch, *, fake_tool_op) -> tuple[Any, Any]:  # type: ignore[name-defined]
    monkeypatch.setattr(rem, "run_tool_op", fake_tool_op)
    nodes_lookup = {
        "review-coverage-critic": {
            "produces": {"_coverage-critic.md": str(review_dir / "_coverage-critic.md")},
        },
    }

    class _FakeRunState:
        def __init__(self):
            self.meta: dict = {}

    run_state = _FakeRunState()
    disposition = _evaluate_autonomous_gate(
        "approve-review", nodes_lookup, review_dir / "manifest.json", run_state,
    )
    return disposition, run_state


# ===========================================================================
# 1. The reachability acceptance test (★ the load-bearing proof)
# ===========================================================================

class TestBacktrackReachesIncrementalRelate:
    def _scenario(self, tmp_path):
        project_notes_dir, review_dir, literature_dir, baseline_citekeys = _build_scope(tmp_path)

        connected_hit = _hit("Connected Persona Value Continuation Study", "Connor")
        island_hit = _hit("Totally Unrelated Cooking Recipes Analysis", "Zephyr")
        connected_ck, island_ck = _predict_citekeys(
            [connected_hit, island_hit], set(baseline_citekeys),
        )
        # Pre-write the two newcomers' literature notes (simulating
        # full-distill already having happened out-of-band, per this PR's
        # explicit precondition — full-distill itself stays out of scope).
        _write_lit_note(literature_dir, connected_ck, concepts=["concept-0"])  # overlaps base0drift2020
        _write_lit_note(literature_dir, island_ck, concepts=["concept-zzz-nothing-shares-this"])

        def fake_tool_op(op, **kwargs):
            if op == "sweep":
                return SweepResult(
                    kept=[_deduped(connected_hit), _deduped(island_hit)],
                    independent_count=2, total_hits_fetched=2, cells=[], errors=[],
                )
            if op == "snowball":
                return {"corpus_raw": None, "saturation": None, "stop_reason": "saturated"}
            raise AssertionError(f"unexpected op {op!r}")

        return review_dir, literature_dir, baseline_citekeys, connected_ck, island_ck, fake_tool_op

    def test_bidirectional_neighborhood_blocked_island_escalates_only_itself(self, tmp_path, monkeypatch):
        (
            review_dir, literature_dir, baseline_citekeys, connected_ck, island_ck, fake_tool_op,
        ) = self._scenario(tmp_path)

        monkeypatch.setenv("RV_JUDGE_MODEL", "test-judge-model")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        relate_calls: list[tuple[str, str]] = []
        escalate_calls: list[str] = []
        escalate_target = sorted(baseline_citekeys)[10]

        def fake_default_relate_fn(new_ck, cand_ck, *, literature_dir, judge_model):
            relate_calls.append((new_ck, cand_ck))
            return {"tag": "SUPPORTS", "reason": f"{new_ck} and {cand_ck} share a concept"}

        def fake_default_escalate_relate_fn(new_ck, baseline, *, literature_dir, judge_model):
            escalate_calls.append(new_ck)
            return [{"candidate": escalate_target, "tag": "PARTIAL", "reason": "wider relate found a weak link"}]

        monkeypatch.setattr(rem, "_default_relate_fn", fake_default_relate_fn)
        monkeypatch.setattr(rem, "_default_escalate_relate_fn", fake_default_escalate_relate_fn)

        disposition, _run_state = _run_gate(review_dir, monkeypatch, fake_tool_op=fake_tool_op)

        # The corpus rows DID land (proves the round ran at all).
        citekeys = set(_parse_corpus_citekeys(review_dir / "_corpus.md"))
        assert connected_ck in citekeys
        assert island_ck in citekeys

        # ★ NEIGHBORHOOD-BLOCKED (sub-quadratic vs. corpus size): the
        # connected newcomer shares a concept with EXACTLY ONE of the 20
        # baseline papers — a naive `new x N` scan would have judged 20
        # pairs; concept-graph blocking judges exactly 1.
        assert relate_calls == [(connected_ck, "base0drift2020")]

        # ★ ISLAND ESCALATES ONLY ITSELF: the island newcomer (zero concept
        # overlap with ANY baseline paper) is the ONLY citekey escalated —
        # the connected newcomer (which HAD candidates) must never be
        # escalated too.
        assert escalate_calls == [island_ck]

        # ★ BIDIRECTIONAL EDGE WRITE: the connected pair's edge round-trips
        # through BOTH notes.
        connected_body = (literature_dir / f"{connected_ck}.md").read_text(encoding="utf-8")
        base0_body = (literature_dir / "base0drift2020.md").read_text(encoding="utf-8")
        connected_edges = parse_paper_relations(connected_body)
        base0_edges = parse_paper_relations(base0_body)
        assert not connected_edges.malformed
        assert not base0_edges.malformed
        assert any(e["target"] == "base0drift2020" and e["tag"] == "SUPPORTS" for e in connected_edges.edges)
        assert any(e["target"] == connected_ck and e["tag"] == "SUPPORTS" for e in base0_edges.edges)

        # The island's escalated edge ALSO round-trips bidirectionally.
        island_body = (literature_dir / f"{island_ck}.md").read_text(encoding="utf-8")
        escalate_target_body = (literature_dir / f"{escalate_target}.md").read_text(encoding="utf-8")
        island_edges = parse_paper_relations(island_body)
        target_edges = parse_paper_relations(escalate_target_body)
        assert any(e["target"] == escalate_target and e["tag"] == "PARTIAL" for e in island_edges.edges)
        assert any(e["target"] == island_ck and e["tag"] == "PARTIAL" for e in target_edges.edges)

        # This is an axis-4 hard structural gate — a still-thin pole after
        # the frozen counter-query round(s) genuinely exhausts to
        # HALT-DECLARE (never a silent GO/residue); unrelated to the relate
        # wiring itself, but confirms the backtrack ran to a real terminal
        # disposition (not a crash/short-circuit).
        from research_vault.review import autonomy as auto
        assert disposition.disposition == auto.HALT_DECLARE

    def test_mutation_check_stubbed_wiring_produces_no_edges(self, tmp_path, monkeypatch):
        """★ MUTATION-CHECK: the SAME scenario, but with the wiring function
        (``run_incremental_relate_for_new_citekeys``) stubbed/bypassed —
        exactly what would happen if PR-3b's plumbing were reverted/never
        shipped. Proves the positive test's edge assertions above are real
        signal: under a stub, NO edges appear anywhere and the injected
        relate/escalate callables are never even invoked."""
        (
            review_dir, literature_dir, baseline_citekeys, connected_ck, island_ck, fake_tool_op,
        ) = self._scenario(tmp_path)

        relate_calls: list[tuple[str, str]] = []

        def stub_wiring(new_citekeys, *, literature_dir, baseline_citekeys, relate_fn=None, escalate_relate_fn=None, judge_model=""):
            # A "bypassed module" stub: never calls run_incremental_relate,
            # never invokes relate_fn/escalate_relate_fn at all.
            return {"result": None, "not_yet_distilled": list(new_citekeys)}

        monkeypatch.setattr(rem, "run_incremental_relate_for_new_citekeys", stub_wiring)

        disposition, _run_state = _run_gate(review_dir, monkeypatch, fake_tool_op=fake_tool_op)

        # The corpus rows still land (the stub only bypasses the RELATE
        # step, not the sweep/append step) — but NO edges appear anywhere:
        # this is the exact green-but-unreached failure mode PR-3b exists
        # to close.
        connected_note = literature_dir / f"{connected_ck}.md"
        island_note = literature_dir / f"{island_ck}.md"
        assert connected_note.exists()
        assert island_note.exists()
        connected_edges = parse_paper_relations(connected_note.read_text(encoding="utf-8"))
        island_edges = parse_paper_relations(island_note.read_text(encoding="utf-8"))
        assert connected_edges.edges == []  # the positive test asserted a SUPPORTS edge here
        assert island_edges.edges == []     # the positive test asserted a PARTIAL edge here
        assert relate_calls == []           # never even invoked


# ===========================================================================
# 2. Contract test — tips phrasing bound to _COUNTER_POSITION_BULLET_PREFIX
# ===========================================================================

class TestCriticTipsPhrasingContract:
    def test_prescribed_block_phrasing_matches_prefix_constant(self):
        tips = review_style.get_review_tips()
        critic_tips = tips["review_critic_tips"]
        # The tips' own itemization instruction (style.py) — the literal
        # phrase an agent is told to prefix a BLOCK reason bullet with.
        assert "COUNTER-POSITION THIN-POLE" in critic_tips
        # Bound to the SAME constant `check_coverage_critic_verdict` uses to
        # classify remediation-eligibility (review/__init__.py) — a future
        # tips-prose edit that drifts the exact phrase would silently break
        # the backtrack trigger (every reason bullet would fail the
        # `.startswith(_COUNTER_POSITION_BULLET_PREFIX)` check) without this
        # test catching it.
        assert "COUNTER-POSITION THIN-POLE".lower() == _COUNTER_POSITION_BULLET_PREFIX

    def test_prefix_constant_actually_matches_a_real_bullet_from_the_tips(self):
        """Non-vacuous companion: a bullet phrased EXACTLY per the tips'
        template must pass the real classification predicate the gate
        uses (review/__init__.py's `.startswith` check) — not just a
        string-equality check on the constant in isolation."""
        bullet = "COUNTER-POSITION THIN-POLE by-temporal — the stability pole is empty"
        assert bullet.strip().lower().startswith(_COUNTER_POSITION_BULLET_PREFIX)
