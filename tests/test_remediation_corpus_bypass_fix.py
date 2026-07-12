"""tests/test_remediation_corpus_bypass_fix.py — the remediation
corpus-bypass fix: curate-bloat (bare/untagged rows silently certified)
and the off-domain field-leak (unscreened remediation appends).

Design of record: internal design note (operator-private, not shipped).

Two independent defects, one shared locus: ``review.remediation.
run_directed_remediation_round`` (the critic-backtrack loop) used to
append raw sweep/snowball hits DIRECTLY into ``_corpus.md`` as bare
``[NEW]`` rows — downstream of curate, the relevance-screen, AND the cold
final-corpus verify. This file covers:
  1. ``review.check_corpus_all_accept_tagged`` — the new mechanical gate.
  2. The unified bracket-annotation grammar (``review.relevance.
     corpus_row_annotation_tags``) converging three previously-diverging
     parsers — ``review.ledger._corpus_rows``'s undercount is the load-
     bearing regression pin.
  3. ``run_directed_remediation_round`` now screens every hit through
     ``review.relevance.relevance_gate`` before it can reach
     ``_corpus.md`` — LEAK-PLANT tests for the off-domain astronomy canary
     and a cross-domain-generic token collision.
  4. The B2 stoplist hardening on ``review.relevance._STOPWORDS``.
  5. ``dag/verbs.py``'s coverage-gate + approve-review wiring HALTs on any
     ``[NEEDS-CURATE]``-tagged row still sitting in the corpus.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.review import (  # noqa: E402
    CorpusSchemaError,
    _parse_corpus_citekeys,
    check_corpus_all_accept_tagged,
)
from research_vault.review import autonomy as auto  # noqa: E402
from research_vault.review import corpus_freeze as cf  # noqa: E402
from research_vault.review import ledger as review_ledger  # noqa: E402
from research_vault.review import relevance as rel  # noqa: E402
from research_vault.review import remediation as rem  # noqa: E402
from research_vault.sources.dedup import DedupedHit  # noqa: E402
from research_vault.sources.base import PaperHit  # noqa: E402
from research_vault.sources.sweep import SweepResult  # noqa: E402


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _corpus_note(path: Path, rows: list[tuple[str, str, str]]) -> None:
    """``rows`` is a list of ``(annotation, citekey, title)`` triples."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"| {ann} | {ck} | {title} |" for ann, ck, title in rows)
    path.write_text(
        "| annotation | citekey | title |\n|---|---|---|\n" + body + "\n",
        encoding="utf-8",
    )


def _protocol_note(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "question: does an LLM persona's stated values drift over long "
        "multi-turn dialogue?\n"
        "inclusion: RCTs and controlled LLM-persona conversation studies\n"
        "exclusion: non-English\n"
        "coverage_claim: all English papers 2015-2025 on persona drift\n"
        "counter-position: persona value stability — evidence the persona "
        "is NOT drifting under conversational variance collapse\n"
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


def _hit(title: str, *, abstract: str = "", family: str = "Author", year: int = 2024) -> PaperHit:
    return PaperHit(
        title=title, authors=[f"X. {family}"], year=year,
        external_ids={}, abstract=abstract, citation_count=0, source="semantic-scholar",
    )


def _deduped(hit: PaperHit) -> DedupedHit:
    return DedupedHit(hit=hit, external_ids=dict(hit.external_ids), sources={hit.source})


# ===========================================================================
# 1. check_corpus_all_accept_tagged — the new mechanical gate
# ===========================================================================

class TestCheckCorpusAllAcceptTagged:
    def test_all_recognized_rows_pass(self, tmp_path):
        corpus = tmp_path / "_corpus.md"
        _corpus_note(corpus, [
            ("[NEW]", "smith2024", "A curated paper"),
            ("[LEG-1][NEW] {SF}", "jones2023", "A leg-tagged paper"),
            ("[IN-CORPUS:old2019]", "old2019", "An already-vetted paper"),
        ])
        info = check_corpus_all_accept_tagged(corpus)
        assert info["exists"] is True
        assert info["all_tagged"] is True
        assert info["untagged_citekeys"] == []
        assert info["total_rows"] == 3

    def test_needs_curate_row_is_flagged(self, tmp_path):
        """LEAK-PLANT: a mechanically-screened-in but not-yet-recurated
        remediation append (the ACTUAL shape this fix's append helper now
        emits) must be caught — never silently treated as accepted."""
        corpus = tmp_path / "_corpus.md"
        _corpus_note(corpus, [
            ("[NEW]", "smith2024", "A curated paper"),
            ("[NEW][NEEDS-CURATE]", "leaked2025", "A remediation-appended paper"),
        ])
        info = check_corpus_all_accept_tagged(corpus)
        assert info["all_tagged"] is False
        assert info["untagged_citekeys"] == ["leaked2025"]
        assert info["total_rows"] == 2

    def test_needs_curate_with_relevance_uncertain_flag_still_flagged(self, tmp_path):
        """screen_and_append_facet_hits appends an UNCERTAIN row as
        ``[NEW][NEEDS-CURATE] [RELEVANCE:UNCERTAIN]`` — the extra bracket
        token must not mask the NEEDS-CURATE tag."""
        corpus = tmp_path / "_corpus.md"
        _corpus_note(corpus, [
            ("[NEW][NEEDS-CURATE] [RELEVANCE:UNCERTAIN]", "thin2025", "A thin-abstract candidate"),
        ])
        info = check_corpus_all_accept_tagged(corpus)
        assert info["all_tagged"] is False
        assert info["untagged_citekeys"] == ["thin2025"]

    def test_missing_corpus_is_honest_vacuous_pass(self, tmp_path):
        info = check_corpus_all_accept_tagged(tmp_path / "_corpus.md")
        assert info == {
            "exists": False, "all_tagged": True,
            "untagged_citekeys": [], "total_rows": 0,
        }

    def test_malformed_row_is_not_this_functions_concern(self, tmp_path):
        """A bracket-shaped-but-unrecognized annotation is
        ``_parse_corpus_citekeys``'s CorpusSchemaError's job — this gate
        must not duplicate that reject (it silently skips it here, never
        crashes)."""
        corpus = tmp_path / "_corpus.md"
        _corpus_note(corpus, [("[WEIRD]", "ghost2024", "malformed row")])
        info = check_corpus_all_accept_tagged(corpus)
        assert info["all_tagged"] is True  # not this function's job
        with pytest.raises(CorpusSchemaError):
            _parse_corpus_citekeys(corpus)


# ===========================================================================
# 2. Unified bracket-annotation grammar — parser convergence
# ===========================================================================

class TestUnifiedAnnotationGrammar:
    def test_ledger_corpus_rows_no_longer_undercounts_compound_annotation(self, tmp_path):
        """The architect-flagged bug: ``review.ledger._corpus_rows`` used an
        exact ``annotation.upper() == "[NEW]"`` match, silently excluding
        (as a 'malformed' gap) a real ``review-curate`` compound annotation
        like ``[LEG-1][NEW]``. This is the load-bearing regression pin."""
        corpus = tmp_path / "_corpus.md"
        _corpus_note(corpus, [
            ("[LEG-1][NEW] {SF,silicon-sampling}", "smith2024", "A compound-annotated paper"),
            ("[LEG-2][IN-CORPUS:old2019]", "old2019", "An already-vetted, leg-tagged paper"),
        ])
        rows, gaps = review_ledger._corpus_rows(corpus)
        assert len(rows) == 2
        assert not any("malformed" in g for g in gaps)

    def test_ledger_k_block_counts_compound_annotation_rows(self, tmp_path):
        corpus = tmp_path / "_corpus.md"
        _corpus_note(corpus, [
            ("[LEG-1][NEW] {SF}", "smith2024", "A compound-annotated paper"),
            ("[LEG-2][IN-CORPUS:old2019]", "old2019", "An already-vetted paper"),
        ])
        k_block = review_ledger._k_block(corpus, literature_dir=None, literature_root=None)
        assert k_block["accepted"] == 2
        assert k_block["in_corpus"] == 1

    def test_relevance_annotation_is_new_still_correct_after_convergence(self):
        assert rel._annotation_is_new("[LEG-1][NEW] {SF,silicon-sampling}") is True
        assert rel._annotation_is_new("[LEG-1][IN-CORPUS] {SF}") is False
        assert rel._annotation_is_new("[NEW]") is True

    def test_annotation_needs_curate(self):
        assert rel.annotation_needs_curate("[NEW][NEEDS-CURATE]") is True
        assert rel.annotation_needs_curate("[NEW][NEEDS-CURATE] [RELEVANCE:UNCERTAIN]") is True
        assert rel.annotation_needs_curate("[NEW]") is False
        assert rel.annotation_needs_curate("[LEG-1][NEW]") is False

    def test_parse_corpus_citekeys_still_includes_bare_new(self, tmp_path):
        """Deliberately UNCHANGED: ``_parse_corpus_citekeys``'s "all
        citekeys" contract still includes a bare ``[NEW]`` row (the
        legitimate ``review-curate`` accept shape — no code path anywhere
        instructs curate to emit a ``[LEG-N]`` tag, so requiring one
        universally would falsely reject every ordinary curated corpus).
        The NEW gate that DOES reject an un-recurated row is
        ``check_corpus_all_accept_tagged`` (keyed to the ``[NEEDS-CURATE]``
        tag this fix's append path now stamps), not this parser."""
        corpus = tmp_path / "_corpus.md"
        _corpus_note(corpus, [("[NEW]", "smith2024", "A curated paper")])
        assert _parse_corpus_citekeys(corpus) == ["smith2024"]


# ===========================================================================
# 3. run_directed_remediation_round screens hits before they can reach
#    _corpus.md — the sibling-bug fix (Fix B1)
# ===========================================================================

class TestDirectedRemediationRoundScreensHits:
    def _seed(self, tmp_path):
        corpus = tmp_path / "_corpus.md"
        protocol = tmp_path / "_protocol.md"
        deviations = tmp_path / "_deviations.md"
        _corpus_note(corpus, [("[NEW]", "driftpaper2023", "title-driftpaper2023")])
        _protocol_note(protocol)
        meta: dict = {}
        cf.stamp_corpus_freeze(meta, corpus_path=corpus, protocol_path=protocol)
        return meta, corpus, protocol, deviations

    def test_off_domain_astronomy_canary_never_reaches_corpus(self, tmp_path):
        """LEAK-PLANT: the shipped off-domain astronomy canary (the same
        contamination class the design grounds this fix in — a
        spectroscopic galaxy/AGN survey) must be screened OUT, declared to
        a residue file, and never appear in ``_corpus.md``."""
        meta, corpus, protocol, deviations = self._seed(tmp_path)

        astro_hit = _hit(
            rel._CANARY_OFF_DOMAIN_TITLE,
            abstract=rel._CANARY_OFF_DOMAIN_ABSTRACT,
        )

        def fake_tool_op(op, **kwargs):
            if op == "sweep":
                return SweepResult(
                    kept=[_deduped(astro_hit)],
                    independent_count=1, total_hits_fetched=1, cells=[], errors=[],
                )
            return {}

        result = rem.run_directed_remediation_round(
            meta, pole="by-temporal", protocol_path=protocol, corpus_path=corpus,
            deviations_path=deviations, out_dir=tmp_path, tool_op_fn=fake_tool_op,
        )
        assert result["added"] == []
        assert result["stopped"] == "zero-new"
        assert rel._CANARY_OFF_DOMAIN_TITLE not in corpus.read_text(encoding="utf-8")
        residue = tmp_path / "_critic-backtrack-residue.md"
        assert residue.exists()
        assert rel._CANARY_OFF_DOMAIN_TITLE in residue.read_text(encoding="utf-8")

    def test_token_collision_off_domain_hit_never_reaches_corpus(self, tmp_path):
        """LEAK-PLANT: a physics 'quantum collapse' abstract that shares
        the bare stoplisted token 'collapse' with this protocol's own
        counter-position ('variance collapse') must NOT ride that generic
        collision to IN — B2 stoplisting excludes it from the overlap
        check, so it correctly resolves OFF_DOMAIN and is screened out."""
        meta, corpus, protocol, deviations = self._seed(tmp_path)

        physics_hit = _hit(
            "Quantum collapse and decoherence in open quantum systems",
            abstract=(
                "We study the quantum collapse of a wavefunction under "
                "environmental decoherence, deriving a master equation for "
                "the reduced density matrix of an open quantum system."
            ),
        )

        def fake_tool_op(op, **kwargs):
            if op == "sweep":
                return SweepResult(
                    kept=[_deduped(physics_hit)],
                    independent_count=1, total_hits_fetched=1, cells=[], errors=[],
                )
            return {}

        result = rem.run_directed_remediation_round(
            meta, pole="by-temporal", protocol_path=protocol, corpus_path=corpus,
            deviations_path=deviations, out_dir=tmp_path, tool_op_fn=fake_tool_op,
        )
        assert result["added"] == []
        assert "Quantum collapse" not in corpus.read_text(encoding="utf-8")
        residue = tmp_path / "_critic-backtrack-residue.md"
        assert residue.exists() and "Quantum collapse" in residue.read_text(encoding="utf-8")

    def test_in_scope_hit_tagged_needs_curate_never_bare_new(self, tmp_path):
        """A genuinely in-scope hit (overlaps the counter-position vocab)
        is added — but tagged [NEW][NEEDS-CURATE], never a bare or
        leg-tagged row (this fix's Fix A.1: distinct provenance tag)."""
        meta, corpus, protocol, deviations = self._seed(tmp_path)

        stability_hit = _hit(
            "Persona Value Stability Under Long Multi-Turn Dialogue",
            abstract=(
                "We show LLM persona values remain stable across long "
                "multi-turn conversations, resisting drift even under "
                "adversarial prompting."
            ),
        )

        def fake_tool_op(op, **kwargs):
            if op == "sweep":
                return SweepResult(
                    kept=[_deduped(stability_hit)],
                    independent_count=1, total_hits_fetched=1, cells=[], errors=[],
                )
            if op == "snowball":
                return {"corpus_raw": None, "walk": None, "stop_reason": "walk-complete:1-hops"}
            return {}

        result = rem.run_directed_remediation_round(
            meta, pole="by-temporal", protocol_path=protocol, corpus_path=corpus,
            deviations_path=deviations, out_dir=tmp_path, tool_op_fn=fake_tool_op,
        )
        assert len(result["added"]) == 1
        text = corpus.read_text(encoding="utf-8")
        assert "[NEW][NEEDS-CURATE]" in text
        assert "[LEG-" not in text  # never fabricates a leg tag
        # and the gate now correctly flags this corpus as pending re-curate:
        info = check_corpus_all_accept_tagged(corpus)
        assert info["all_tagged"] is False
        assert result["added"][0] in info["untagged_citekeys"]


# ===========================================================================
# 4. B2 stoplist hardening — the domain-collision unit tests
# ===========================================================================

class TestStoplistHardening:
    def test_cross_domain_generic_tokens_excluded_from_tokenize(self):
        tokens = rel._tokenize("a wide-field spectroscopic survey of collapse dynamics")
        assert "survey" not in tokens
        assert "collapse" not in tokens
        assert "dynamics" not in tokens

    def test_galaxy_survey_vs_value_survey_no_longer_collides(self):
        criteria = {
            "question": "how do public value surveys measure trust in institutions?",
            "inclusion": "value survey methodology",
            "exclusion": "", "coverage_claim": "",
        }
        candidate = {
            "title": "A wide-field spectroscopic survey of active galactic nuclei",
            "abstract": (
                "We present a spectroscopic survey of active galactic "
                "nuclei, deriving black hole mass estimates via "
                "reverberation mapping of quasar emission-line ratios."
            ),
        }
        assert rel.relevance_gate(candidate, criteria, "") == rel.OFF_DOMAIN


# ===========================================================================
# 5. dag/verbs.py wiring — coverage-gate + approve-review HALT on
#    [NEEDS-CURATE] rows
# ===========================================================================

class TestDagVerbsCorpusTaggingWiring:
    def test_coverage_gate_halts_on_needs_curate_row(self, tmp_path, monkeypatch):
        from research_vault.dag.verbs import _evaluate_autonomous_gate

        review_dir = tmp_path
        corpus = review_dir / "_corpus.md"
        protocol = review_dir / "_protocol.md"
        walk = review_dir / "_walk.md"
        _corpus_note(corpus, [
            ("[NEW]", "smith2024", "A curated paper"),
            ("[NEW][NEEDS-CURATE]", "leaked2025", "A remediation-appended paper"),
        ])
        _protocol_note(protocol)
        walk.write_text("stop_reason: walk-complete:1-hops\n", encoding="utf-8")

        nodes_lookup = {
            "review-snowball": {"produces": {"_walk.md": str(walk)}},
            "review-search": {"produces": {"_search_hits.md": ""}},
        }

        class _FakeRunState:
            def __init__(self):
                self.meta: dict = {}

        disposition = _evaluate_autonomous_gate(
            "coverage-gate", nodes_lookup, review_dir / "manifest.json", _FakeRunState(),
        )
        assert disposition.disposition == auto.HALT_DECLARE
        assert "corpus-tagging" in disposition.reason
        assert "leaked2025" in disposition.evidence.get("untagged_citekeys", [])
