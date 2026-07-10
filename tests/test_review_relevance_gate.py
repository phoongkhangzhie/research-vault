"""tests/test_review_relevance_gate.py — PR-1: the trustworthy-curation
relevance gate (design 2026-07-10-trustworthy-curation-relevance-gate-design.md).

Coverage:
  1. relevance_gate calibration — leak-planting against the REAL grounding-run
     contamination classes (astronomy, materials physics) -> OFF_DOMAIN.
  2. Disconfirming protection — a boundary/counter-position-named paper
     survives (the recall-protection test that matters most).
  3. Uncertain (unfetchable/too-short abstract) -> UNCERTAIN, never OFF_DOMAIN.
  4. screen_corpus_raw — the mechanical snowball-screen tool op: rejects
     never silently dropped (audit section), UNCERTAIN visibly flagged.
  5. Cold-verifier canary construction + parsing + check_relevance_verifier.
  6. classify_relevance_verdict — mutation-tested HALT/auto-prune boundary.
  7. prune_off_domain_from_corpus — idempotent corpus mutation + residue note.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.review import relevance as rel  # noqa: E402


# A frozen protocol's criteria for a downstream project-shaped review: LLM
# cultural/social competence. Used across the leak-plant tests below.
_DEMO_CRITERIA = {
    "question": (
        "Do large language models exhibit consistent, measurable cultural "
        "and social behavioral competence across diverse human populations?"
    ),
    "inclusion": (
        "Papers that measure a language model's cultural values, social "
        "norms, or behavioral competence, including cross-cultural "
        "psychometric evaluation of LLMs and default-model bias studies."
    ),
    "exclusion": "Papers with no language model or behavioral/cultural construct.",
    "coverage_claim": (
        "All English-language papers 2020-2026 evaluating LLM cultural or "
        "social behavior against human population baselines."
    ),
}

_COUNTER_POSITION = (
    "The default-model literature that assumes a single unmarked WEIRD "
    "(Western, Educated, Industrialized, Rich, Democratic) culture as the "
    "implicit human baseline without cross-cultural framing."
)


# ---------------------------------------------------------------------------
# 1 + 2 + 3. relevance_gate calibration
# ---------------------------------------------------------------------------

class TestRelevanceGateCalibration:
    def test_astronomy_contaminant_rejected(self):
        """Real grounding-run contamination class: a galaxy/AGN survey."""
        candidate = {
            "title": "A wide-field spectroscopic survey of methanol masers in star-forming regions",
            "abstract": (
                "We present a spectroscopic survey of 3,500 galactic methanol "
                "maser sources, deriving distance estimates via trigonometric "
                "parallax and characterizing the spatial distribution of "
                "star-forming regions across the Milky Way disk."
            ),
        }
        verdict = rel.relevance_gate(candidate, _DEMO_CRITERIA, _COUNTER_POSITION)
        assert verdict == rel.OFF_DOMAIN

    def test_materials_physics_contaminant_rejected(self):
        """Real grounding-run contamination class: Silicon-on-Sapphire materials physics."""
        candidate = {
            "title": "Strain relaxation in Silicon-on-Sapphire epitaxial heterostructures",
            "abstract": (
                "We investigate strain relaxation mechanisms in Silicon-on-"
                "Sapphire epitaxial thin films grown via molecular beam "
                "epitaxy, measuring dislocation density and lattice mismatch "
                "as a function of annealing temperature."
            ),
        }
        verdict = rel.relevance_gate(candidate, _DEMO_CRITERIA, _COUNTER_POSITION)
        assert verdict == rel.OFF_DOMAIN

    def test_boundary_disconfirming_paper_survives(self):
        """The recall-protection test that matters most: a default-model
        WEIRD-baseline contrast anchor named in counter-position must NOT
        be rejected, even though it may share little inclusion-criteria
        vocabulary (it's the disconfirming literature, not the confirming
        one)."""
        candidate = {
            "title": "Benchmarking language models on standard English NLU tasks",
            "abstract": (
                "We evaluate several large language models purely on English "
                "GLUE/SuperGLUE benchmarks, implicitly treating a single "
                "WEIRD (Western, Educated, Industrialized, Rich, Democratic) "
                "population as the default human baseline with no "
                "cross-cultural framing."
            ),
        }
        verdict = rel.relevance_gate(candidate, _DEMO_CRITERIA, _COUNTER_POSITION)
        assert verdict == rel.IN

    def test_in_scope_paper_kept(self):
        candidate = {
            "title": "Cross-cultural evaluation of LLM value alignment across 30 countries",
            "abstract": (
                "We measure large language models' cultural values and social "
                "norm adherence across a diverse sample of human populations, "
                "comparing model responses to the World Values Survey."
            ),
        }
        verdict = rel.relevance_gate(candidate, _DEMO_CRITERIA, _COUNTER_POSITION)
        assert verdict == rel.IN

    def test_unfetchable_abstract_is_uncertain_not_off_domain(self):
        candidate = {"title": "Untitled preprint", "abstract": ""}
        verdict = rel.relevance_gate(candidate, _DEMO_CRITERIA, _COUNTER_POSITION)
        assert verdict == rel.UNCERTAIN

    def test_short_stub_is_uncertain(self):
        candidate = {"title": "A note", "abstract": "TBD."}
        verdict = rel.relevance_gate(candidate, _DEMO_CRITERIA, _COUNTER_POSITION)
        assert verdict == rel.UNCERTAIN

    def test_empty_criteria_never_rejects(self):
        """No domain vocabulary to judge against -> UNCERTAIN, never a
        confident OFF_DOMAIN (fail toward keep)."""
        candidate = {
            "title": "A wide-field spectroscopic survey of active galactic nuclei",
            "abstract": (
                "We present a spectroscopic survey of active galactic nuclei "
                "deriving black hole mass estimates via reverberation mapping."
            ),
        }
        verdict = rel.relevance_gate(candidate, {}, "")
        assert verdict == rel.UNCERTAIN

    def test_verdict_always_in_fixed_vocab(self):
        for candidate in (
            {"title": "x", "abstract": "y" * 50},
            {},
            {"title": "", "abstract": ""},
        ):
            assert rel.relevance_gate(candidate, _DEMO_CRITERIA, _COUNTER_POSITION) in rel.VALID_VERDICTS


# ---------------------------------------------------------------------------
# Protocol criteria extraction
# ---------------------------------------------------------------------------

class TestParseProtocolCriteria:
    def test_reads_all_fields(self, tmp_path: Path):
        protocol = tmp_path / "_protocol.md"
        protocol.write_text(
            "---\n"
            "question: Do LLMs show cultural competence?\n"
            "inclusion: papers measuring cultural values in LLMs\n"
            "exclusion: no LLM involved\n"
            "coverage_claim: all English papers 2020-2026\n"
            "counter-position: default-model WEIRD-baseline literature\n"
            "---\n\nBody.\n",
            encoding="utf-8",
        )
        criteria, counter_position = rel.parse_protocol_criteria(protocol)
        assert criteria["question"] == "Do LLMs show cultural competence?"
        assert "cultural values" in criteria["inclusion"]
        assert counter_position == "default-model WEIRD-baseline literature"

    def test_missing_protocol_returns_empty(self, tmp_path: Path):
        criteria, counter_position = rel.parse_protocol_criteria(tmp_path / "nope.md")
        assert criteria == {}
        assert counter_position == ""


# ---------------------------------------------------------------------------
# 4. screen_corpus_raw — snowball-screen mechanical tool op
# ---------------------------------------------------------------------------

def _write_protocol(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"question: {_DEMO_CRITERIA['question']}\n"
        f"inclusion: {_DEMO_CRITERIA['inclusion']}\n"
        f"exclusion: {_DEMO_CRITERIA['exclusion']}\n"
        f"coverage_claim: {_DEMO_CRITERIA['coverage_claim']}\n"
        f"counter-position: {_COUNTER_POSITION}\n"
        "---\n\nBody.\n",
        encoding="utf-8",
    )


def _corpus_raw_row(pid: str, title: str, abstract: str) -> str:
    return f"| [NEW] | {pid} | {title} | | | {abstract} | |"


class TestScreenCorpusRaw:
    def test_off_domain_rejected_before_curate_sees_it(self, tmp_path: Path):
        protocol = tmp_path / "_protocol.md"
        _write_protocol(protocol)
        corpus_raw = tmp_path / "_corpus_raw.md"
        corpus_raw.write_text(
            "# Corpus (raw, pre-curation)\n\n"
            "| Annotation | Paper-id | Title | Venue | Year | Abstract/TL;DR | Flags |\n"
            "|---|---|---|---|---|---|---|\n"
            + _corpus_raw_row(
                "10.1/gal2024", "Spectroscopic survey of quasar emission-line ratios",
                "We present a wide-field spectroscopic survey of active galactic "
                "nuclei deriving black hole mass estimates via reverberation "
                "mapping and echelle spectroscopy of quasar emission lines.",
            ) + "\n"
            + _corpus_raw_row(
                "10.1/llm2024", "Cross-cultural evaluation of LLM value alignment",
                "We measure large language models' cultural values and social "
                "norm adherence across diverse human populations.",
            ) + "\n",
            encoding="utf-8",
        )
        out_path = tmp_path / "_corpus_raw_screened.md"

        # RED (structural proof): before screening, both rows are present
        # in the raw pool that would otherwise reach review-curate directly.
        raw_rows = rel.parse_corpus_raw_rows(corpus_raw.read_text(encoding="utf-8"))
        assert len(raw_rows) == 2

        counts = rel.screen_corpus_raw(corpus_raw, protocol, out_path)
        assert counts == {"total": 2, "in": 1, "uncertain": 0, "off_domain": 1}

        screened_text = out_path.read_text(encoding="utf-8")
        # SURVIVES: the in-scope paper is in the kept table.
        assert "10.1/llm2024" in screened_text
        # REJECTED, but never silently dropped — preserved in the audit section.
        assert "## Rejected as off-domain" in screened_text
        assert "10.1/gal2024" in screened_text
        # The rejected row must NOT appear in the kept table region (before
        # the "Rejected" heading).
        kept_region = screened_text.split("## Rejected as off-domain")[0]
        assert "10.1/gal2024" not in kept_region

    def test_uncertain_row_kept_and_visibly_flagged(self, tmp_path: Path):
        protocol = tmp_path / "_protocol.md"
        _write_protocol(protocol)
        corpus_raw = tmp_path / "_corpus_raw.md"
        corpus_raw.write_text(
            "| Annotation | Paper-id | Title | Venue | Year | Abstract/TL;DR | Flags |\n"
            "|---|---|---|---|---|---|---|\n"
            + _corpus_raw_row("10.1/stub2024", "Untitled", "") + "\n",
            encoding="utf-8",
        )
        out_path = tmp_path / "_corpus_raw_screened.md"
        counts = rel.screen_corpus_raw(corpus_raw, protocol, out_path)
        assert counts["uncertain"] == 1
        assert counts["off_domain"] == 0
        text = out_path.read_text(encoding="utf-8")
        assert "10.1/stub2024" in text
        assert "[RELEVANCE:UNCERTAIN]" in text

    def test_missing_corpus_raw_is_empty_not_a_crash(self, tmp_path: Path):
        protocol = tmp_path / "_protocol.md"
        _write_protocol(protocol)
        out_path = tmp_path / "_corpus_raw_screened.md"
        counts = rel.screen_corpus_raw(tmp_path / "nope.md", protocol, out_path)
        assert counts == {"total": 0, "in": 0, "uncertain": 0, "off_domain": 0}


# ---------------------------------------------------------------------------
# 5. Cold-verifier: canary construction, verdict parsing, check_relevance_verifier
# ---------------------------------------------------------------------------

class TestCanaryAndVerdictParsing:
    def test_build_canary_rows_domain_agnostic_in_scope(self):
        rows = rel.build_canary_rows(_DEMO_CRITERIA)
        assert len(rows) == 2
        in_scope = next(r for r in rows if r["citekey"] == rel.CANARY_IN_SCOPE_CITEKEY)
        off_domain = next(r for r in rows if r["citekey"] == rel.CANARY_OFF_DOMAIN_CITEKEY)
        # The in-scope canary is BUILT FROM the live criteria — it must
        # actually classify IN against those same criteria (self-consistency).
        assert rel.relevance_gate(
            {"title": in_scope["title"], "abstract": in_scope["abstract"]},
            _DEMO_CRITERIA, _COUNTER_POSITION,
        ) == rel.IN
        assert rel.relevance_gate(
            {"title": off_domain["title"], "abstract": off_domain["abstract"]},
            _DEMO_CRITERIA, _COUNTER_POSITION,
        ) == rel.OFF_DOMAIN

    def test_build_verify_input_injects_canaries_unmarked(self, tmp_path: Path):
        corpus_path = tmp_path / "_corpus.md"
        corpus_path.write_text(
            "| annotation | citekey | title | abstract |\n|---|---|---|---|\n"
            "| [NEW] | smith2024 | Cross-cultural LLM study | Measures LLM cultural values. |\n",
            encoding="utf-8",
        )
        protocol_path = tmp_path / "_protocol.md"
        _write_protocol(protocol_path)
        out_path = tmp_path / "_corpus_verify_input.md"

        result = rel.build_verify_input(corpus_path, protocol_path, out_path)
        assert result["real_citekeys"] == ["smith2024"]
        assert set(result["canary_citekeys"]) == {
            rel.CANARY_IN_SCOPE_CITEKEY, rel.CANARY_OFF_DOMAIN_CITEKEY,
        }
        text = out_path.read_text(encoding="utf-8")
        assert "smith2024" in text
        assert rel.CANARY_IN_SCOPE_CITEKEY in text
        assert rel.CANARY_OFF_DOMAIN_CITEKEY in text
        # Unmarked: the literal word "canary" never appears in the agent-
        # visible text (it would tip off the judge).
        assert "canary" not in text.lower()

    def test_parse_relevance_verdict_table(self):
        text = (
            "Reasoning prose here.\n\n"
            "| Citekey | Verdict |\n"
            "|---|---|\n"
            "| smith2024 | IN |\n"
            f"| {rel.CANARY_IN_SCOPE_CITEKEY} | IN |\n"
            f"| {rel.CANARY_OFF_DOMAIN_CITEKEY} | OFF_DOMAIN |\n"
        )
        verdicts, malformed = rel.parse_relevance_verdict_table(text)
        assert verdicts["smith2024"] == "IN"
        assert verdicts[rel.CANARY_OFF_DOMAIN_CITEKEY] == "OFF_DOMAIN"
        assert malformed == []

    def test_malformed_verdict_row_surfaced_not_dropped(self):
        text = (
            "| Citekey | Verdict |\n"
            "|---|---|\n"
            "| smith2024 | IN |\n"
            "| jones2023 | maybe? |\n"
        )
        verdicts, malformed = rel.parse_relevance_verdict_table(text)
        assert "jones2023" not in verdicts
        assert any("jones2023" in m for m in malformed)

    def test_canary_both_correct_not_aborted(self, tmp_path: Path):
        path = tmp_path / "_relevance-verdict.md"
        path.write_text(
            "| Citekey | Verdict |\n|---|---|\n"
            "| smith2024 | IN |\n"
            f"| {rel.CANARY_IN_SCOPE_CITEKEY} | IN |\n"
            f"| {rel.CANARY_OFF_DOMAIN_CITEKEY} | OFF_DOMAIN |\n",
            encoding="utf-8",
        )
        payload = rel.check_relevance_verifier(path)
        assert payload["exists"] is True
        assert payload["canary_aborted"] is False
        assert payload["verdicts"] == {"smith2024": "IN"}

    def test_canary_off_domain_misclassified_aborts(self, tmp_path: Path):
        path = tmp_path / "_relevance-verdict.md"
        path.write_text(
            "| Citekey | Verdict |\n|---|---|\n"
            "| smith2024 | IN |\n"
            f"| {rel.CANARY_IN_SCOPE_CITEKEY} | IN |\n"
            f"| {rel.CANARY_OFF_DOMAIN_CITEKEY} | IN |\n",  # judge missed it
            encoding="utf-8",
        )
        payload = rel.check_relevance_verifier(path)
        assert payload["canary_aborted"] is True
        assert "off-domain canary" in payload["canary_detail"]

    def test_canary_in_scope_misclassified_aborts(self, tmp_path: Path):
        path = tmp_path / "_relevance-verdict.md"
        path.write_text(
            "| Citekey | Verdict |\n|---|---|\n"
            f"| {rel.CANARY_IN_SCOPE_CITEKEY} | OFF_DOMAIN |\n"
            f"| {rel.CANARY_OFF_DOMAIN_CITEKEY} | OFF_DOMAIN |\n",
            encoding="utf-8",
        )
        payload = rel.check_relevance_verifier(path)
        assert payload["canary_aborted"] is True
        assert "in-scope canary" in payload["canary_detail"]

    def test_missing_canary_row_aborts(self, tmp_path: Path):
        """A verifier note that never mentions the canary rows at all — the
        agent skipped them, or the prep step's rows weren't actually
        forwarded — must abort exactly like a misclassification."""
        path = tmp_path / "_relevance-verdict.md"
        path.write_text(
            "| Citekey | Verdict |\n|---|---|\nsmith2024 | IN |\n",
            encoding="utf-8",
        )
        payload = rel.check_relevance_verifier(path)
        assert payload["canary_aborted"] is True

    def test_missing_artifact_is_not_run(self, tmp_path: Path):
        payload = rel.check_relevance_verifier(tmp_path / "nope.md")
        assert payload["exists"] is False
        assert payload["empty_verdict_set"] is True

    def test_empty_verdict_set_flagged(self, tmp_path: Path):
        path = tmp_path / "_relevance-verdict.md"
        path.write_text("No table here, just prose.\n", encoding="utf-8")
        payload = rel.check_relevance_verifier(path)
        assert payload["exists"] is True
        assert payload["empty_verdict_set"] is True


# ---------------------------------------------------------------------------
# 6. classify_relevance_verdict — mutation-tested HALT/auto-prune boundary
# ---------------------------------------------------------------------------

class TestClassifyRelevanceVerdict:
    def test_not_run_halts(self):
        result = rel.classify_relevance_verdict({"exists": False, "empty_verdict_set": True})
        from research_vault.review.autonomy import HALT_DECLARE
        assert result.disposition == HALT_DECLARE

    def test_canary_aborted_halts(self):
        payload = {
            "exists": True, "canary_aborted": True, "canary_detail": "x",
            "empty_verdict_set": False, "verdicts": {},
        }
        result = rel.classify_relevance_verdict(payload)
        from research_vault.review.autonomy import HALT_DECLARE
        assert result.disposition == HALT_DECLARE

    def test_empty_verdict_set_halts(self):
        payload = {
            "exists": True, "canary_aborted": False, "empty_verdict_set": True,
            "verdicts": {},
        }
        result = rel.classify_relevance_verdict(payload)
        from research_vault.review.autonomy import HALT_DECLARE
        assert result.disposition == HALT_DECLARE

    def test_zero_off_domain_is_plain_go(self):
        payload = {
            "exists": True, "canary_aborted": False, "empty_verdict_set": False,
            "verdicts": {f"paper{i}": "IN" for i in range(10)},
        }
        result = rel.classify_relevance_verdict(payload)
        from research_vault.review.autonomy import GO
        assert result.disposition == GO

    def test_small_fraction_5pct_auto_prunes_and_proceeds(self):
        """Mutation-test the HALT boundary: a fixture at ~5% off-domain
        must auto-prune + proceed (GO-WITH-RESIDUE), never HALT."""
        verdicts = {f"paper{i}": "IN" for i in range(19)}
        verdicts["contaminant1"] = "OFF_DOMAIN"  # 1/20 = 5%
        payload = {
            "exists": True, "canary_aborted": False, "empty_verdict_set": False,
            "verdicts": verdicts,
        }
        result = rel.classify_relevance_verdict(payload)
        from research_vault.review.autonomy import GO_WITH_RESIDUE
        assert result.disposition == GO_WITH_RESIDUE
        assert result.evidence["off_domain_citekeys"] == ["contaminant1"]

    def test_large_fraction_50pct_halts(self):
        """Mutation-test the HALT boundary: a fixture at ~50% off-domain
        (the real grounding-run 51/97 ratio) must HALT-DECLARE, never silently prune
        half the corpus."""
        verdicts = {}
        for i in range(10):
            verdicts[f"paper{i}"] = "IN"
        for i in range(10):
            verdicts[f"contaminant{i}"] = "OFF_DOMAIN"
        payload = {
            "exists": True, "canary_aborted": False, "empty_verdict_set": False,
            "verdicts": verdicts,
        }
        result = rel.classify_relevance_verdict(payload)
        from research_vault.review.autonomy import HALT_DECLARE
        assert result.disposition == HALT_DECLARE

    def test_threshold_boundary_is_inclusive_of_halt(self):
        """Exactly at the threshold -> HALT (>=), never a boundary auto-prune."""
        verdicts = {"a": "IN", "b": "OFF_DOMAIN"}  # 50% >> 20% threshold, sanity
        payload = {
            "exists": True, "canary_aborted": False, "empty_verdict_set": False,
            "verdicts": verdicts,
        }
        result = rel.classify_relevance_verdict(payload, threshold=0.5)
        from research_vault.review.autonomy import HALT_DECLARE
        assert result.disposition == HALT_DECLARE

    def test_malformed_missing_per_paper_verdict_never_silent_drop(self):
        """A paper simply absent from the verdicts dict (malformed row,
        filtered out upstream) is never counted as off-domain — it's
        recall-safe KEEP+flag, handled by the caller never touching it
        (this dict just doesn't mention it; classify only sees what
        actually parsed)."""
        payload = {
            "exists": True, "canary_aborted": False, "empty_verdict_set": False,
            "verdicts": {"a": "IN", "b": "UNCERTAIN"},
        }
        result = rel.classify_relevance_verdict(payload)
        from research_vault.review.autonomy import GO
        assert result.disposition == GO
        # UNCERTAIN is never treated as OFF_DOMAIN.
        assert "b" not in result.evidence.get("off_domain_citekeys", [])


# ---------------------------------------------------------------------------
# 7. prune_off_domain_from_corpus
# ---------------------------------------------------------------------------

class TestPruneOffDomainFromCorpus:
    def test_prunes_flagged_rows_and_declares_residue(self, tmp_path: Path):
        corpus_path = tmp_path / "_corpus.md"
        corpus_path.write_text(
            "| annotation | citekey | title |\n|---|---|---|\n"
            "| [NEW] | keep2024 | A kept paper |\n"
            "| [NEW] | drop2024 | An off-domain paper |\n",
            encoding="utf-8",
        )
        residue_path = tmp_path / "_relevance-residue.md"
        n = rel.prune_off_domain_from_corpus(corpus_path, ["drop2024"], residue_path)
        assert n == 1
        text = corpus_path.read_text(encoding="utf-8")
        assert "keep2024" in text
        assert "drop2024" not in text
        residue_text = residue_path.read_text(encoding="utf-8")
        assert "drop2024" in residue_text

    def test_idempotent_second_call_no_op(self, tmp_path: Path):
        corpus_path = tmp_path / "_corpus.md"
        corpus_path.write_text(
            "| annotation | citekey | title |\n|---|---|---|\n"
            "| [NEW] | keep2024 | A kept paper |\n",
            encoding="utf-8",
        )
        residue_path = tmp_path / "_relevance-residue.md"
        # drop2024 already absent from corpus_path — pruning it again is a no-op.
        n = rel.prune_off_domain_from_corpus(corpus_path, ["drop2024"], residue_path)
        assert n == 0
        assert "keep2024" in corpus_path.read_text(encoding="utf-8")

    def test_no_citekeys_is_a_no_op(self, tmp_path: Path):
        corpus_path = tmp_path / "_corpus.md"
        corpus_path.write_text("| annotation | citekey | title |\n", encoding="utf-8")
        residue_path = tmp_path / "_relevance-residue.md"
        n = rel.prune_off_domain_from_corpus(corpus_path, [], residue_path)
        assert n == 0
        assert not residue_path.exists()
