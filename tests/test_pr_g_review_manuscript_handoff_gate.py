"""tests/test_pr_g_review_manuscript_handoff_gate.py — PR-G: the
review->manuscript integration gate, the deferred-integration TESTS half.

TARGETED INTEGRATION (not a full loop re-run). Drives the NEW seams wired
into the merged core as integration tests:

  (A1) The FOUR handoff properties (PR-5) asserted FROM ``_corpus_ledger.md``
       — COMPLETE / CLEAN / CANONICALLY-KEYED / LEDGERED — as a rejects-only
       GATE HARNESS, plus a ``ledger_complete`` MUTATION-CHECK proving the
       harness reads the FLAG (not mere file existence).
  (A2) ``not_yet_distilled_count == 0`` at coverage-gate GO — the PR-G
       derivation (remediation-added corpus citekeys vs. the materialized
       edge graph), now a first-class ledger scalar (``review.ledger``).
  (A3) MULTI-ROUND relate backtrack (PR-3b deferred item a): a pole thin
       enough to force a 2nd emit/HALT/ingest cycle, asserting cross-cycle
       state (fresh ``baseline_before`` re-capture, judge_dir clear-then-
       re-emit, 2nd-cycle papers get their own bidirectional edges).
  (A4) A relate fan-out cycle END-TO-END through the two-phase
       ``approve-review`` gate, plus the GENERIC-DRIVER audit: the relate
       seam shares ``gates.judge_seam``'s three-artifact contract +
       ``TASKS_SCHEMA`` with the board/support-matcher seams.

★ Reuse note (PR-G brief): (A3)/(A4) EXTEND the existing end-to-end driver in
``tests/test_pr3b_incremental_relate_wiring.py`` (which already drives the
two-phase gate via ``dag.verbs._evaluate_autonomous_gate`` with a realistic
verdicts fixture) rather than rebuilding it. This file adds the *multi-round*
and *generic-contract* coverage that test did not.

★ Judge-independence boundary (flagged, per PR-G): a truly COLD relate/board
fan-out needs the hub to spawn fresh subagent-judges. These tests drive the
wiring with a hermetic realistic-verdicts fixture (canaries answered from the
private key) to prove emit->ingest->edges end-to-end; the judge-INDEPENDENCE
property is out of a solo test's reach and is the hub's to drive.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.cite import make_citekey  # noqa: E402
from research_vault.dag.verbs import _evaluate_autonomous_gate  # noqa: E402
from research_vault.gates import judge_seam  # noqa: E402
from research_vault.note import _parse_frontmatter  # noqa: E402
from research_vault.review import _parse_corpus_citekeys  # noqa: E402
from research_vault.review import autonomy as auto  # noqa: E402
from research_vault.review import relate_judge_seam as rjs  # noqa: E402
from research_vault.review import remediation as rem  # noqa: E402
from research_vault.review.ledger import write_corpus_ledger  # noqa: E402
from research_vault.review.relate_check import parse_paper_relations  # noqa: E402
from research_vault.sources.sweep import DedupedHit, PaperHit, SweepResult  # noqa: E402


# ===========================================================================
# The GATE HARNESS — the rejects-only, four-handoff-property assertion over a
# _corpus_ledger.md. This is the artifact PR-G ships: a machine check any
# adopter (or the hub) runs on a completed review's ledger at GO.
# ===========================================================================

class HandoffPropertyError(AssertionError):
    """Raised by ``assert_four_handoff_properties`` when the ledger fails any
    of COMPLETE / CLEAN / CANONICALLY-KEYED / LEDGERED. Rejects-only: a PASS
    never certifies the science, it certifies the handoff CONTRACT held."""


def assert_four_handoff_properties(ledger_path: Path) -> dict[str, Any]:
    """Assert the four review->manuscript handoff properties FROM the ledger
    alone (never re-reading the source artifacts — the ledger IS the contract
    surface). Returns the parsed frontmatter on success; raises
    ``HandoffPropertyError`` on any failure.

    - LEDGERED  — the ledger exists AND its own ``ledger_complete`` flag is
      true. Reads the FLAG, not file existence (the mutation-check proves it).
    - COMPLETE  — search angles + a walk-terminal stop-reason + the open-poles
      field are all present/derivable.
    - CLEAN     — a relevance disposition is recorded, with off-domain and
      prune counts (a GO-class disposition, never HALT).
    - CANONICALLY-KEYED — every accepted row's citekey is convention-
      conformant (``citekey_conformant_count == accepted``) and none are
      non-conformant.
    """
    if not ledger_path.exists():
        raise HandoffPropertyError(f"LEDGERED: no ledger at {ledger_path}")
    fields, _body = _parse_frontmatter(ledger_path.read_text(encoding="utf-8"))

    # --- LEDGERED (the flag, not existence) ---
    if str(fields.get("ledger_complete", "")).strip().lower() != "true":
        raise HandoffPropertyError(
            f"LEDGERED: ledger_complete is not true (got "
            f"{fields.get('ledger_complete')!r}) — the ledger declares itself "
            "INCOMPLETE."
        )

    # --- COMPLETE (angles + walk-terminal stop_reason + open poles) ---
    if not str(fields.get("angles_searched", "")).strip():
        raise HandoffPropertyError("COMPLETE: angles_searched is empty.")
    if not str(fields.get("stop_reason", "")).strip():
        raise HandoffPropertyError("COMPLETE: walk-terminal stop_reason is empty.")
    if "open_counter_poles" not in fields:
        raise HandoffPropertyError("COMPLETE: open_counter_poles field absent.")

    # --- CLEAN (relevance disposition + off-domain + prune) ---
    disposition = str(fields.get("relevance_disposition", "")).strip()
    if disposition not in {"GO", "GO-WITH-RESIDUE"}:
        raise HandoffPropertyError(
            f"CLEAN: relevance_disposition is not a GO-class verdict "
            f"(got {disposition!r})."
        )
    for count_field in ("off_domain_count", "pruned_off_domain"):
        if int(str(fields.get(count_field, "0")).strip() or 0) < 0:
            raise HandoffPropertyError(f"CLEAN: {count_field} is negative.")

    # --- CANONICALLY-KEYED ---
    accepted = int(str(fields["accepted"]).strip())
    conformant = int(str(fields["citekey_conformant_count"]).strip())
    nonconformant = int(str(fields["citekey_nonconformant_count"]).strip())
    if conformant != accepted:
        raise HandoffPropertyError(
            f"CANONICALLY-KEYED: citekey_conformant_count ({conformant}) != "
            f"accepted ({accepted})."
        )
    if nonconformant != 0:
        raise HandoffPropertyError(
            f"CANONICALLY-KEYED: {nonconformant} non-conformant citekey(s)."
        )
    return fields


# ---------------------------------------------------------------------------
# A clean-GO fixture whose ledger satisfies all four properties. Mirrors
# test_review_ledger.py's builders but WITH a wired relevance payload (so the
# CLEAN property is a real GO-class disposition, not an honest no-op).
# ---------------------------------------------------------------------------

def _clean_relevance_payload() -> dict[str, Any]:
    # 5 IN + 1 OFF_DOMAIN -> GO-WITH-RESIDUE, one pruned, canary clean.
    return {
        "exists": True,
        "canary_aborted": False,
        "canary_detail": "both canaries classified correctly",
        "verdicts": {
            "smith2024a": "IN", "jones2023": "IN", "p3": "IN",
            "p4": "IN", "p5": "IN", "ghost2019": "OFF_DOMAIN",
        },
        "malformed": [],
        "empty_verdict_set": False,
    }


def _build_clean_scope(tmp_path: Path, name: str = "gate-scope") -> tuple[Path, Path]:
    review_dir = tmp_path / "reviews" / name
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "_protocol.md").write_text(
        "---\ntype: review-protocol\n"
        'question: "Does X improve Y?"\n'
        'inclusion: "empirical studies"\nexclusion: "non-empirical"\n'
        'coverage_claim: "broad"\ncounter-position: "stability literature"\n'
        "seed_queries:\n  by-method: \"q1\"\n  by-outcome: \"q2\"\n"
        "sources: [semantic-scholar, arxiv]\n---\n",
        encoding="utf-8",
    )
    (review_dir / "_search_hits.md").write_text(
        "---\ndark_sources: \n---\n\n# Search hits\n\n## Cells\n\n"
        "| Angle | Source | Hits | Error |\n|---|---|---|---|\n"
        "| by-method | semantic-scholar | 5 |  |\n"
        "| by-outcome | arxiv | 3 |  |\n\nTotal hits fetched: 8\n",
        encoding="utf-8",
    )
    (review_dir / "_walk.md").write_text(
        "---\nstop_reason: walk-complete:1-hops\nunresolvable_count: 0\n---\n\n"
        "# Citation-neighbor relevance walk\n\n"
        "| Hop | New (forward) | New (backward) | New independent | Cumulative | Direction-starved |\n"
        "|---|---|---|---|---|---|\n| 1 | 4 | 2 | 5 | 5 |  |\n",
        encoding="utf-8",
    )
    (review_dir / "_corpus.md").write_text(
        "| [NEW] | smith2024a | Title A | abstract |\n"
        "| [IN-CORPUS:jones2023] | jones2023 | Title B | abstract |\n",
        encoding="utf-8",
    )
    lit_dir = tmp_path / "notes" / "demo" / "literature"
    lit_dir.mkdir(parents=True, exist_ok=True)
    (lit_dir / "smith2024a.md").write_text(
        "---\ntype: literature\ncitekey: smith2024a\ndoi: 10.1234/abcd\n---\n", encoding="utf-8",
    )
    (lit_dir / "jones2023.md").write_text(
        "---\ntype: literature\ncitekey: jones2023\narxiv_id: 2301.00001\n---\n", encoding="utf-8",
    )
    return review_dir, lit_dir


# ===========================================================================
# (A1) Four handoff properties from the ledger + ledger_complete mutation-check
# ===========================================================================

class TestFourHandoffPropertiesGate:
    def test_clean_ledger_passes_all_four(self, tmp_path):
        review_dir, lit_dir = _build_clean_scope(tmp_path)
        ledger = write_corpus_ledger(
            review_dir, literature_dir=lit_dir, literature_root=lit_dir, relevance_payload=_clean_relevance_payload(),
        )
        fields = assert_four_handoff_properties(ledger)
        # Sanity: the properties were really CHECKED (not a vacuous pass).
        assert fields["relevance_disposition"] == "GO-WITH-RESIDUE"
        assert int(fields["pruned_off_domain"]) == 1
        assert int(fields["citekey_conformant_count"]) == int(fields["accepted"]) == 2

    def test_mutation_ledger_complete_false_is_rejected(self, tmp_path):
        """★ MUTATION-CHECK: flip ONLY ``ledger_complete`` to false on an
        otherwise-clean ledger — the harness must FAIL. Proves LEDGERED reads
        the flag, not file existence (a green-but-incomplete ledger cannot
        sneak through the handoff gate)."""
        review_dir, lit_dir = _build_clean_scope(tmp_path)
        ledger = write_corpus_ledger(
            review_dir, literature_dir=lit_dir, literature_root=lit_dir, relevance_payload=_clean_relevance_payload(),
        )
        # Pre-condition: unmutated ledger passes.
        assert_four_handoff_properties(ledger)

        text = ledger.read_text(encoding="utf-8")
        assert "ledger_complete: true" in text
        ledger.write_text(text.replace("ledger_complete: true", "ledger_complete: false"), encoding="utf-8")

        with pytest.raises(HandoffPropertyError, match="LEDGERED"):
            assert_four_handoff_properties(ledger)

    def test_nonconformant_citekey_rejected_by_gate(self, tmp_path):
        """CANONICALLY-KEYED has teeth: a raw S2-id citekey in the corpus
        (never canonicalized) fails the gate even though the ledger is
        otherwise well-formed."""
        review_dir, lit_dir = _build_clean_scope(tmp_path)
        # A nonconformant citekey (trailing multi-letter tail fails CITEKEY_RE)
        # WITH a resolvable literature note — so the ONLY failing property is
        # CANONICALLY-KEYED (ledger_complete stays true; the gate rejects on
        # the key-conformance check, not on a LEDGER-GAP).
        (review_dir / "_corpus.md").write_text(
            "| [NEW] | smith2024extra | Title A | abstract |\n", encoding="utf-8",
        )
        (lit_dir / "smith2024extra.md").write_text(
            "---\ntype: literature\ncitekey: smith2024extra\ndoi: 10.1/x\n---\n", encoding="utf-8",
        )
        ledger = write_corpus_ledger(
            review_dir, literature_dir=lit_dir, literature_root=lit_dir, relevance_payload=_clean_relevance_payload(),
        )
        with pytest.raises(HandoffPropertyError, match="CANONICALLY-KEYED"):
            assert_four_handoff_properties(ledger)

    def test_missing_relevance_disposition_rejected_by_gate(self, tmp_path):
        """CLEAN has teeth: a review that never wired the relevance-verify
        node has no GO-class disposition, so it cannot claim the CLEAN
        handoff property (honest no-op at the writer, REJECT at the gate)."""
        review_dir, lit_dir = _build_clean_scope(tmp_path)
        ledger = write_corpus_ledger(review_dir, literature_dir=lit_dir, literature_root=lit_dir, relevance_payload=None)
        with pytest.raises(HandoffPropertyError, match="CLEAN"):
            assert_four_handoff_properties(ledger)


# ===========================================================================
# (A2) not_yet_distilled_count == 0 at GO — the PR-G derivation, now a ledger
#      scalar (review/ledger.py::_not_yet_distilled_block).
# ===========================================================================

class TestNotYetDistilledCount:
    def _lit_note(self, lit_dir: Path, citekey: str, *, related_to: str | None = None) -> None:
        lit_dir.mkdir(parents=True, exist_ok=True)
        body = f"---\ntype: literature\ncitekey: {citekey}\ndoi: 10.1/{citekey}\n---\n"
        if related_to is not None:
            body += (
                "\n## Related papers\n"
                f"- [{related_to}](/literature/{related_to}.md) — SUPPORTS: corroborates.\n"
            )
        (lit_dir / f"{citekey}.md").write_text(body, encoding="utf-8")

    def _scope_with_deviation(self, tmp_path: Path, *, added: list[str]) -> tuple[Path, Path]:
        review_dir, lit_dir = _build_clean_scope(tmp_path, name="nd-scope")
        # Record a within-criteria-append deviation naming the remediation-adds.
        auto.record_deviation(
            review_dir / "_deviations.md",
            version=2, pre_criteria="sha256:same", post_criteria="sha256:same",
            removed=[], added=added,
            rationale="autonomous within-criteria remediation wave (test fixture).",
            kind="within-criteria-append",
        )
        return review_dir, lit_dir

    def test_zero_when_every_added_paper_is_related(self, tmp_path):
        """At a clean GO the incremental-relate loop wrote an edge for every
        remediation-added paper -> not_yet_distilled_count == 0."""
        review_dir, lit_dir = self._scope_with_deviation(tmp_path, added=["lee2025b"])
        # lee2025b was distilled AND related (has a paper->paper edge).
        self._lit_note(lit_dir, "lee2025b", related_to="smith2024a")
        self._lit_note(lit_dir, "smith2024a", related_to="lee2025b")
        ledger = write_corpus_ledger(
            review_dir, literature_dir=lit_dir, literature_root=lit_dir, relevance_payload=_clean_relevance_payload(),
        )
        fields, _ = _parse_frontmatter(ledger.read_text(encoding="utf-8"))
        assert int(fields["remediation_added_count"]) == 1
        assert int(fields["not_yet_distilled_count"]) == 0
        assert str(fields["not_yet_distilled_citekeys"]).strip() == ""

    def test_positive_when_added_paper_has_no_edges(self, tmp_path):
        """★ The FP-guard: a remediation-added paper the loop terminated on
        before relating (a note with NO paper->paper edge) surfaces as a
        completeness gap — proving the derivation isn't always 0."""
        review_dir, lit_dir = self._scope_with_deviation(tmp_path, added=["orphan2025z"])
        self._lit_note(lit_dir, "orphan2025z", related_to=None)  # distilled, NEVER related
        ledger = write_corpus_ledger(
            review_dir, literature_dir=lit_dir, literature_root=lit_dir, relevance_payload=_clean_relevance_payload(),
        )
        fields, _ = _parse_frontmatter(ledger.read_text(encoding="utf-8"))
        assert int(fields["not_yet_distilled_count"]) == 1
        assert "orphan2025z" in str(fields["not_yet_distilled_citekeys"])

    def test_positive_when_added_paper_has_no_note_at_all(self, tmp_path):
        """A corpus-row-only remediation add with no literature note yet is
        un-distilled by the strictest reading — counted, never a fabricated 0."""
        review_dir, lit_dir = self._scope_with_deviation(tmp_path, added=["missingnote2025"])
        # No note written for missingnote2025 at all.
        ledger = write_corpus_ledger(
            review_dir, literature_dir=lit_dir, literature_root=lit_dir, relevance_payload=_clean_relevance_payload(),
        )
        fields, _ = _parse_frontmatter(ledger.read_text(encoding="utf-8"))
        assert int(fields["not_yet_distilled_count"]) == 1
        assert "missingnote2025" in str(fields["not_yet_distilled_citekeys"])

    def test_no_deviations_is_zero_not_a_crash(self, tmp_path):
        """A review that never ran a remediation round (no _deviations.md)
        has nothing to distill -> 0, honestly (not a crash, not a gap)."""
        review_dir, lit_dir = _build_clean_scope(tmp_path, name="no-dev-scope")
        ledger = write_corpus_ledger(
            review_dir, literature_dir=lit_dir, literature_root=lit_dir, relevance_payload=_clean_relevance_payload(),
        )
        fields, _ = _parse_frontmatter(ledger.read_text(encoding="utf-8"))
        assert int(fields["remediation_added_count"]) == 0
        assert int(fields["not_yet_distilled_count"]) == 0


# ===========================================================================
# Shared multi-round backtrack scenario builders (self-contained; the shape
# mirrors tests/test_pr3b_incremental_relate_wiring.py's helpers).
# ===========================================================================

_N_BASELINE = 20


def _write_lit_note(literature_dir: Path, citekey: str, *, concepts: list[str]) -> None:
    literature_dir.mkdir(parents=True, exist_ok=True)
    edges = "\n".join(
        f"- [{c}](/concepts/{c}.md) — SUPPORTS: this paper touches {c}" for c in concepts
    )
    (literature_dir / f"{citekey}.md").write_text(
        f"---\ncitekey: {citekey}\n---\n\n## Concept edges\n{edges}\n", encoding="utf-8",
    )


def _corpus_note(path: Path, citekeys: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(f"| [NEW] | {ck} | title-{ck} |" for ck in citekeys)
    path.write_text("| annotation | citekey | title |\n|---|---|---|\n" + rows + "\n", encoding="utf-8")


def _protocol_note(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nquestion: is persona-drift stable or does it decay over time?\n"
        "inclusion: RCTs and controlled LLM-persona studies\nexclusion: non-English\n"
        "coverage_claim: all English papers 2015-2025 on persona drift\n"
        "counter-position: persona/value stability\n"
        "seed_queries:\n  by-temporal:\n    thesis:\n      - \"persona drift over long conversations\"\n"
        "    counter:\n      - \"persona value stability long conversations\"\n"
        "sources: [semantic-scholar, arxiv]\n---\n\nProtocol.\n",
        encoding="utf-8",
    )


def _critic_note(path: Path, *, pole: str = "by-temporal") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nverdict: BLOCK\nremediation_target_node: review-snowball\n"
        f"remediation_target_pole: {pole}\n"
        "remediation_target_directive: re-run the counter queries harder\n---\n\n"
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


def _predict_citekey(hit: PaperHit, existing: set[str]) -> str:
    family = hit.authors[0].strip().rsplit(" ", 1)[-1] if hit.authors else None
    return make_citekey(family, hit.title, str(hit.year or ""), existing)


def _build_scope(tmp_path: Path) -> tuple[Path, Path, list[str]]:
    project_notes_dir = tmp_path / "notes"
    review_dir = project_notes_dir / "reviews" / "persona-drift-scope"
    literature_dir = project_notes_dir / "literature"
    baseline = [f"base{i}drift2020" for i in range(_N_BASELINE)]
    _corpus_note(review_dir / "_corpus.md", baseline)
    for i, ck in enumerate(baseline):
        _write_lit_note(literature_dir, ck, concepts=[f"concept-{i}"])
    _protocol_note(review_dir / "_protocol.md")
    _critic_note(review_dir / "_coverage-critic.md")
    return review_dir, literature_dir, baseline


class _FakeRunState:
    def __init__(self) -> None:
        self.meta: dict = {}


def _run_gate(review_dir: Path, monkeypatch, *, fake_tool_op, run_state=None):
    monkeypatch.setattr(rem, "run_tool_op", fake_tool_op)
    nodes_lookup = {
        "review-coverage-critic": {
            "produces": {"_coverage-critic.md": str(review_dir / "_coverage-critic.md")},
        },
    }
    if run_state is None:
        run_state = _FakeRunState()
    disposition = _evaluate_autonomous_gate(
        "approve-review", nodes_lookup, review_dir / "manifest.json", run_state,
    )
    return disposition, run_state


def _write_fake_verdicts(judge_dir: Path, *, tag_by_pair: dict[tuple[str, str, str], str]) -> None:
    """Hermetic stand-in for the hub's cold judge fan-out: canaries answered
    from the PRIVATE key (a trustworthy-judge stand-in), real tasks answered
    from ``tag_by_pair`` (keyed ``(kind, a, b)``), default NONE."""
    tasks_doc = judge_seam.read_json_or_none(judge_dir / "_relate-tasks.json")
    canary_key_doc = judge_seam.read_json_or_none(judge_dir / "_relate-canary-key.json")
    canaries = (canary_key_doc or {}).get("canaries", {})
    verdicts = []
    for t in tasks_doc["tasks"]:
        tid = t["id"]
        if tid in canaries:
            verdicts.append({"id": tid, "verdict": canaries[tid], "reason": "canary"})
            continue
        tag = tag_by_pair.get((t.get("kind"), t["a"], t["b"]), "NONE")
        verdicts.append({"id": tid, "verdict": tag, "reason": f"scripted {tag}"})
    judge_seam.write_json(judge_dir / "_relate-verdicts.json", {"verdicts": verdicts})


# ===========================================================================
# (A3) MULTI-ROUND relate backtrack — cross-cycle state (PR-3b deferred item a)
# ===========================================================================

class TestMultiRoundBacktrack:
    def test_two_backtrack_cycles_cross_cycle_state(self, tmp_path, monkeypatch):
        """A pole that yields a DISTINCT new counter-paper on round 1 and
        again on round 2 forces two emit/HALT/ingest cycles. Asserts the
        cross-cycle invariants PR-3b deferred:

          - ``baseline_before`` is re-captured FRESH on the 2nd phase-1 (the
            round-2 emit's stamped baseline INCLUDES round-1's paper).
          - the judge_dir CLEARS then RE-EMITS between cycles (round-2 tasks
            are a different set, keyed to round-2's paper).
          - 2nd-cycle papers get their OWN bidirectional edges.

        Default critic-backtrack cap is 2, which would abandon round-2's
        fan-out un-ingested (budget exhausts before the next phase-2). Bump
        to 3 (the config seam, monkeypatched at the module boundary
        ``resolve_coverage_critic`` reads it from) so round-2's edges
        actually materialize before the loop HALTs on a zero-new round 3.
        """
        monkeypatch.setattr(rem, "get_critic_backtrack_max_rounds", lambda config=None: 3)

        review_dir, literature_dir, baseline = _build_scope(tmp_path)
        judge_dir = review_dir / "judge" / "relate"

        # Two DISTINCT connected newcomers, one per round. Each shares a
        # concept with a different single baseline paper (neighborhood-blocked).
        p1_hit = _hit("Round One Persona Value Continuation", "Connor")
        p1_ck = _predict_citekey(p1_hit, set(baseline))
        _write_lit_note(literature_dir, p1_ck, concepts=["concept-0"])  # overlaps base0

        p2_hit = _hit("Round Two Persona Value Persistence", "Delgado")
        p2_ck = _predict_citekey(p2_hit, set(baseline) | {p1_ck})
        _write_lit_note(literature_dir, p2_ck, concepts=["concept-5"])  # overlaps base5

        sweep_calls = {"n": 0}

        def fake_tool_op(op, **kwargs):
            if op == "sweep":
                sweep_calls["n"] += 1
                idx = sweep_calls["n"]
                hits = {1: [p1_hit], 2: [p2_hit]}.get(idx, [])  # round 3+ -> zero-new
                return SweepResult(
                    kept=[_deduped(h) for h in hits],
                    independent_count=len(hits), total_hits_fetched=len(hits), cells=[], errors=[],
                )
            if op == "snowball":
                return {"corpus_raw": None, "walk": None, "stop_reason": "walk-complete:1-hops"}
            raise AssertionError(f"unexpected op {op!r}")

        # --- CALL 1: phase-1 round 1 emits fan-out for p1, HALT.
        disp, run_state = _run_gate(review_dir, monkeypatch, fake_tool_op=fake_tool_op)
        assert disp.disposition == auto.HALT_DECLARE
        assert rjs.relate_fanout_present(judge_dir)
        r1_tasks = rjs.read_relate_tasks_doc(judge_dir)
        # Round-1 baseline is the ORIGINAL 20 (p1 not yet in corpus at capture).
        assert set(r1_tasks["baseline_citekeys"]) == set(baseline)
        assert p1_ck not in r1_tasks["baseline_citekeys"]
        r1_pairs = [(t["a"], t["b"]) for t in r1_tasks["tasks"] if t.get("kind") == "relate-pair"]
        assert r1_pairs == [(p1_ck, "base0drift2020")]

        _write_fake_verdicts(judge_dir, tag_by_pair={
            ("relate-pair", p1_ck, "base0drift2020"): "SUPPORTS",
        })

        # --- CALL 2: phase-2 ingests round-1 (p1 edges written + fan-out
        # cleared), re-derives CRITIC_BACKTRACK, then phase-1 round 2 emits a
        # FRESH fan-out for p2 and HALTs.
        disp, run_state = _run_gate(review_dir, monkeypatch, fake_tool_op=fake_tool_op, run_state=run_state)
        assert disp.disposition == auto.HALT_DECLARE
        assert rjs.relate_fanout_present(judge_dir)

        # ★ p1's bidirectional edge landed (round-1 fan-out was consumed).
        p1_edges = parse_paper_relations((literature_dir / f"{p1_ck}.md").read_text(encoding="utf-8"))
        base0_edges = parse_paper_relations((literature_dir / "base0drift2020.md").read_text(encoding="utf-8"))
        assert any(e["target"] == "base0drift2020" and e["tag"] == "SUPPORTS" for e in p1_edges.edges)
        assert any(e["target"] == p1_ck and e["tag"] == "SUPPORTS" for e in base0_edges.edges)

        # ★ CLEAR-THEN-RE-EMIT: this is a NEW task set (round-2), keyed to p2 —
        # not the stale round-1 tasks.
        r2_tasks = rjs.read_relate_tasks_doc(judge_dir)
        r2_pairs = [(t["a"], t["b"]) for t in r2_tasks["tasks"] if t.get("kind") == "relate-pair"]
        assert r2_pairs == [(p2_ck, "base5drift2020")]
        # ★ FRESH baseline re-capture: round-2's stamped baseline now INCLUDES
        # round-1's p1 (proof baseline_before was re-read from the grown corpus).
        assert p1_ck in r2_tasks["baseline_citekeys"]
        assert set(r2_tasks["baseline_citekeys"]) == set(baseline) | {p1_ck}

        _write_fake_verdicts(judge_dir, tag_by_pair={
            ("relate-pair", p2_ck, "base5drift2020"): "EXTENDS",
        })

        # --- CALL 3: phase-2 ingests round-2 (p2 edges written), re-derives
        # CRITIC_BACKTRACK, phase-1 round 3 finds zero-new -> HALT terminal.
        disp, run_state = _run_gate(review_dir, monkeypatch, fake_tool_op=fake_tool_op, run_state=run_state)
        assert disp.disposition == auto.HALT_DECLARE
        assert not rjs.relate_fanout_present(judge_dir)  # round-3 emitted nothing

        # ★ 2nd-CYCLE PAPER GOT ITS OWN BIDIRECTIONAL EDGES.
        p2_edges = parse_paper_relations((literature_dir / f"{p2_ck}.md").read_text(encoding="utf-8"))
        base5_edges = parse_paper_relations((literature_dir / "base5drift2020.md").read_text(encoding="utf-8"))
        assert any(e["target"] == "base5drift2020" and e["tag"] == "EXTENDS" for e in p2_edges.edges)
        assert any(e["target"] == p2_ck and e["tag"] == "EXTENDS" for e in base5_edges.edges)

        # Both newcomers are in the corpus; three backtrack rounds were used.
        corpus_cks = set(_parse_corpus_citekeys(review_dir / "_corpus.md"))
        assert {p1_ck, p2_ck} <= corpus_cks
        assert run_state.meta["critic_backtrack_state"]["rounds_used"] == 3


# ===========================================================================
# (A4) A relate fan-out cycle END-TO-END + the GENERIC-DRIVER audit
# ===========================================================================

class TestRelateFanoutCycleAndGenericContract:
    def _single_round_scenario(self, tmp_path):
        review_dir, literature_dir, baseline = _build_scope(tmp_path)
        connected = _hit("Connected Persona Value Continuation Study", "Connor")
        connected_ck = _predict_citekey(connected, set(baseline))
        _write_lit_note(literature_dir, connected_ck, concepts=["concept-0"])

        def fake_tool_op(op, **kwargs):
            if op == "sweep":
                return SweepResult(
                    kept=[_deduped(connected)], independent_count=1,
                    total_hits_fetched=1, cells=[], errors=[],
                )
            if op == "snowball":
                return {"corpus_raw": None, "walk": None, "stop_reason": "walk-complete:1-hops"}
            raise AssertionError(op)

        return review_dir, literature_dir, baseline, connected_ck, fake_tool_op

    def test_two_phase_gate_emit_ingest_edges_end_to_end(self, tmp_path, monkeypatch):
        """Drive the two-phase ``approve-review`` gate: phase-1 emits the
        three relate artifacts + pause-HALT; verdicts land; phase-2 ingests
        and the bidirectional edge materializes."""
        review_dir, literature_dir, _baseline, connected_ck, fake = self._single_round_scenario(tmp_path)
        judge_dir = review_dir / "judge" / "relate"

        disp, run_state = _run_gate(review_dir, monkeypatch, fake_tool_op=fake)
        assert disp.disposition == auto.HALT_DECLARE

        # ★ The three-artifact fan-out contract materialized under judge/relate/.
        assert (judge_dir / "_relate-tasks.json").exists()
        assert (judge_dir / "_relate-canary-key.json").exists()
        assert not (judge_dir / "_relate-verdicts.json").exists()  # phase-1: no verdicts yet

        _write_fake_verdicts(judge_dir, tag_by_pair={
            ("relate-pair", connected_ck, "base0drift2020"): "SUPPORTS",
        })
        assert (judge_dir / "_relate-verdicts.json").exists()

        disp, _run_state = _run_gate(review_dir, monkeypatch, fake_tool_op=fake, run_state=run_state)
        edges = parse_paper_relations((literature_dir / f"{connected_ck}.md").read_text(encoding="utf-8"))
        assert any(e["target"] == "base0drift2020" and e["tag"] == "SUPPORTS" for e in edges.edges)
        assert not rjs.relate_fanout_present(judge_dir)  # consumed

    def test_relate_seam_shares_generic_judge_seam_contract(self, tmp_path, monkeypatch):
        """★ GENERIC-DRIVER audit (PR-G item 4). The relate fan-out is NOT
        board-specific: it is built on the SAME ``gates.judge_seam``
        primitives + ``TASKS_SCHEMA`` the board and support-matcher seams
        use, and materializes the identical three-artifact contract
        (``_relate-tasks.json`` / ``_relate-canary-key.json`` /
        ``_relate-verdicts.json``) with interleaved id-keyed canaries.

        NOTE (honest gap, not papered over): there is NO single hub-side
        scanner that generically DRIVES every pending ``*-tasks.json``. Each
        seam is driven by its own path — the relate seam by the
        ``approve-review`` DAG gate's two-phase re-invocation, the board by
        ``rv manuscript judge-emit``/``judge-ingest``. Genericity lives at
        the ``judge_seam`` PRIMITIVE + schema layer, not at a shared driver.
        """
        review_dir, _lit, _baseline, _ck, fake = self._single_round_scenario(tmp_path)
        judge_dir = review_dir / "judge" / "relate"
        _run_gate(review_dir, monkeypatch, fake_tool_op=fake)

        tasks_doc = judge_seam.read_json_or_none(judge_dir / "_relate-tasks.json")
        canary_doc = judge_seam.read_json_or_none(judge_dir / "_relate-canary-key.json")

        # Same TASKS_SCHEMA constant the board/support-matcher emit under.
        assert tasks_doc["schema"] == judge_seam.TASKS_SCHEMA
        assert canary_doc["schema"] == judge_seam.CANARY_KEY_SCHEMA
        # Interleaved id-keyed canaries (the shared cold-judge guard) — every
        # task carries an id; the canary key references a subset of them.
        task_ids = {t["id"] for t in tasks_doc["tasks"]}
        assert task_ids  # non-empty
        canary_ids = set(canary_doc["canaries"].keys())
        assert canary_ids and canary_ids <= task_ids
        # fanout_incomplete — the shared HALT primitive — reports incomplete
        # while verdicts are absent (the generic fail-closed posture).
        assert judge_seam.fanout_incomplete(tasks_doc, None)
