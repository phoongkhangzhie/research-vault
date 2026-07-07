"""test_pr3_results_schema.py — PR-3: generalized N-runs -> M-scores results schema.

Covers the 5-part PR-3 scope (research-vault CS-project-structure spec, §4.2/§5):
  1. _parse_frontmatter (D8): round-trips a scores: mapping-list, leaves scalar
     lists (backed_by/supported_by/contradicted_by/runs) intact, handles a mixed
     scalar+mapping list, and an empty scores: field.
  2. _normalize_results (D2): the shared read-shim — canonical lists, legacy
     flat-field fold-in, REPRO_SENTINEL exclusion.
  3. check_result_provenance: N->M note verifies (multiple scores, each hashed);
     a planted single-score hash-mismatch fails the gate and the aggregate
     reports every bad score (not just the first); empty scores list still
     skips (unchanged "not-run" semantics).
  4. Legacy flat notes verify UNCHANGED via the shim (backward-compat proof).
  5. cmd_new scaffolds empty runs:/scores: lists (zero items).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from importlib import resources

from research_vault.note import (
    _normalize_results,
    _parse_frontmatter,
    check_result_provenance,
    cmd_new,
    REPRO_SENTINEL,
)
from research_vault.config import load_config


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# 1. _parse_frontmatter (D8) — round-trip tests
# ---------------------------------------------------------------------------

class TestParserMappingListRoundTrip:
    """D8: _parse_frontmatter round-trips a list of mappings, scalar lists intact."""

    def test_mapping_list_round_trips(self):
        text = (
            "---\n"
            "type: experiments\n"
            "scores:\n"
            "  - location: results/scores/hfs-landscape.csv\n"
            "    hash: sha256:aaaa\n"
            "    label: hfs-landscape\n"
            "  - location: results/scores/ap-elicitability.csv\n"
            "    hash: sha256:bbbb\n"
            "---\n"
        )
        fields, _ = _parse_frontmatter(text)
        scores = fields["scores"]
        assert isinstance(scores, list)
        assert len(scores) == 2
        assert scores[0] == {
            "location": "results/scores/hfs-landscape.csv",
            "hash": "sha256:aaaa",
            "label": "hfs-landscape",
        }
        assert scores[1] == {
            "location": "results/scores/ap-elicitability.csv",
            "hash": "sha256:bbbb",
        }

    def test_scalar_list_untouched(self):
        """backed_by/supported_by/contradicted_by/runs: no `key:` shape → stays list[str]."""
        text = (
            "---\n"
            "type: findings\n"
            "backed_by:\n"
            "  - literature/smith2024\n"
            "  - literature/lee2023\n"
            "runs:\n"
            "  - myteam/myproject/run-01\n"
            "  - myteam/myproject/run-02  # label\n"
            "---\n"
        )
        fields, _ = _parse_frontmatter(text)
        assert fields["backed_by"] == ["literature/smith2024", "literature/lee2023"]
        assert fields["runs"] == [
            "myteam/myproject/run-01",
            "myteam/myproject/run-02  # label",
        ]
        for item in fields["backed_by"] + fields["runs"]:
            assert isinstance(item, str)

    def test_mixed_scalar_and_mapping_items_in_one_list(self):
        """A list with both scalar and key:value items — each item classified independently."""
        text = (
            "---\n"
            "type: experiments\n"
            "mixed:\n"
            "  - plain-scalar-item\n"
            "  - location: results/scores/x.csv\n"
            "    hash: sha256:cccc\n"
            "---\n"
        )
        fields, _ = _parse_frontmatter(text)
        mixed = fields["mixed"]
        assert mixed[0] == "plain-scalar-item"
        assert mixed[1] == {"location": "results/scores/x.csv", "hash": "sha256:cccc"}

    def test_empty_scores_field_stays_scalar_empty_string(self):
        """`scores:` with no items (cmd_new's empty scaffold) stays "" — not a list."""
        text = "---\ntype: experiments\nscores: \nruns: \n---\n"
        fields, _ = _parse_frontmatter(text)
        assert fields["scores"] == ""
        assert fields["runs"] == ""

    def test_scalar_list_item_with_no_space_after_colon_stays_scalar(self):
        """Fix 3 (parser tightening): a bare scalar-list item shaped like a
        URL/DOI with a colon but NO space after it (e.g. 'http://x.com/run:5')
        must NOT be mis-parsed as a `key: value` mapping-list entry — YAML
        flow-map semantics require whitespace after the colon."""
        text = (
            "---\n"
            "type: findings\n"
            "backed_by:\n"
            "  - http://example.com/run:5\n"
            "  - 10.1234/some.doi:5\n"
            "---\n"
        )
        fields, _ = _parse_frontmatter(text)
        backed_by = fields["backed_by"]
        assert backed_by == ["http://example.com/run:5", "10.1234/some.doi:5"], backed_by
        for item in backed_by:
            assert isinstance(item, str), f"{item!r} was mis-parsed as a mapping"

    def test_real_scalar_list_callers_parse_identically_after_tightening(self):
        """backed_by/supported_by/contradicted_by/runs — real-shape items with
        a genuine space-delimited `key: value` are unaffected by the
        whitespace-after-colon tightening (they have no key:-shaped items at
        all, so both regex forms already treat them identically)."""
        text = (
            "---\n"
            "type: findings\n"
            "backed_by:\n"
            "  - literature/smith2024\n"
            "supported_by:\n"
            "  - experiments/q1-main1\n"
            "contradicted_by:\n"
            "  - literature/lee2023\n"
            "runs:\n"
            "  - myteam/myproject/run-01\n"
            "  - myteam/myproject/run-02  # label\n"
            "---\n"
        )
        fields, _ = _parse_frontmatter(text)
        assert fields["backed_by"] == ["literature/smith2024"]
        assert fields["supported_by"] == ["experiments/q1-main1"]
        assert fields["contradicted_by"] == ["literature/lee2023"]
        assert fields["runs"] == [
            "myteam/myproject/run-01",
            "myteam/myproject/run-02  # label",
        ]


# ---------------------------------------------------------------------------
# 2. _normalize_results (D2) — the shared read-shim
# ---------------------------------------------------------------------------

class TestNormalizeResults:
    def test_list_form_scores_used_directly(self):
        fields = {
            "scores": [
                {"location": "results/scores/a.csv", "hash": "sha256:1"},
                {"location": "results/scores/b.csv", "hash": "sha256:2"},
            ],
        }
        norm = _normalize_results(fields)
        assert norm["scores"] == fields["scores"]

    def test_legacy_flat_fields_fold_into_1_element_scores_list(self):
        fields = {
            "results_location": "results/scores/a.csv",
            "results_hash": "sha256:1",
        }
        norm = _normalize_results(fields)
        assert norm["scores"] == [{"location": "results/scores/a.csv", "hash": "sha256:1"}]

    def test_legacy_results_wandb_run_folds_into_1_element_runs_list(self):
        fields = {"results_wandb_run": "myteam/myproject/run-01"}
        norm = _normalize_results(fields)
        assert norm["runs"] == ["myteam/myproject/run-01"]

    def test_empty_fields_yield_empty_lists(self):
        fields = {"results_location": "", "results_hash": "", "results_wandb_run": ""}
        norm = _normalize_results(fields)
        assert norm == {"runs": [], "scores": []}

    def test_sentinel_results_hash_treated_as_not_run(self):
        """A legacy results_hash of REPRO_SENTINEL is 'not-recorded', not a real hash."""
        fields = {"results_hash": REPRO_SENTINEL}
        norm = _normalize_results(fields)
        assert norm["scores"] == []

    def test_list_form_takes_priority_over_legacy_flat(self):
        fields = {
            "scores": [{"location": "results/scores/new.csv", "hash": "sha256:new"}],
            "results_location": "legacy/should/be/ignored.csv",
            "results_hash": "sha256:legacy",
        }
        norm = _normalize_results(fields)
        assert norm["scores"] == [{"location": "results/scores/new.csv", "hash": "sha256:new"}]


# ---------------------------------------------------------------------------
# 3. check_result_provenance — N->M verify + aggregate mismatch reporting
# ---------------------------------------------------------------------------

class TestCheckResultProvenanceNtoM:
    def _write_note(self, tmp_path: Path, scores_yaml: str, runs_yaml: str = "") -> Path:
        note_path = tmp_path / "hfs-suite.md"
        note_path.write_text(
            "---\n"
            "type: experiments\n"
            "title: HFS suite (N runs -> M scores)\n"
            f"{runs_yaml}"
            f"{scores_yaml}"
            "---\n",
            encoding="utf-8",
        )
        return note_path

    def test_n_to_m_note_verifies_all_scores_hashed(self, tmp_path):
        """23 runs -> 3 score tables; each independently hash-verified; all pass."""
        csv_a = tmp_path / "hfs_landscape.csv"
        csv_b = tmp_path / "ap_elicitability.csv"
        csv_c = tmp_path / "per_culture_fidelity.csv"
        csv_a.write_bytes(b"model,score\nA,0.9\n")
        csv_b.write_bytes(b"model,ap\nA,0.8\n")
        csv_c.write_bytes(b"model,fid\nA,0.7\n")
        hash_a = _sha256(csv_a.read_bytes())
        hash_b = _sha256(csv_b.read_bytes())
        hash_c = _sha256(csv_c.read_bytes())

        runs_yaml = (
            "runs:\n"
            + "\n".join(f"  - myteam/myproject/run-{i:02d}" for i in range(1, 24))
            + "\n"
        )
        scores_yaml = (
            "scores:\n"
            f"  - location: {csv_a}\n"
            f"    hash: {hash_a}\n"
            f"    label: hfs-landscape\n"
            f"  - location: {csv_b}\n"
            f"    hash: {hash_b}\n"
            f"  - location: {csv_c}\n"
            f"    hash: {hash_c}\n"
        )
        note_path = self._write_note(tmp_path, scores_yaml, runs_yaml)
        fields, _ = _parse_frontmatter(note_path.read_text())
        assert len(fields["runs"]) == 23
        assert len(fields["scores"]) == 3

        violations = check_result_provenance(note_path)
        assert violations == [], f"N->M note with all-correct hashes should verify: {violations}"

    def test_planted_single_score_hash_mismatch_fails_gate(self, tmp_path):
        """One of M=3 scores has a tampered hash -> gate FAILS, reporting exactly that one."""
        csv_a = tmp_path / "hfs_landscape.csv"
        csv_b = tmp_path / "ap_elicitability.csv"
        csv_c = tmp_path / "per_culture_fidelity.csv"
        csv_a.write_bytes(b"model,score\nA,0.9\n")
        csv_b.write_bytes(b"model,ap\nA,0.8\n")
        csv_c.write_bytes(b"model,fid\nA,0.7\n")
        hash_a = _sha256(csv_a.read_bytes())
        bad_hash_b = "sha256:" + "0" * 64  # planted mismatch
        hash_c = _sha256(csv_c.read_bytes())

        scores_yaml = (
            "scores:\n"
            f"  - location: {csv_a}\n"
            f"    hash: {hash_a}\n"
            f"    label: hfs-landscape\n"
            f"  - location: {csv_b}\n"
            f"    hash: {bad_hash_b}\n"
            f"    label: ap-elicitability\n"
            f"  - location: {csv_c}\n"
            f"    hash: {hash_c}\n"
            f"    label: per-culture-fidelity\n"
        )
        note_path = self._write_note(tmp_path, scores_yaml)

        violations = check_result_provenance(note_path)
        assert len(violations) == 1, (
            f"Exactly one planted mismatch should be reported, got: {violations}"
        )
        assert "ap_elicitability" in violations[0]
        assert "mismatch" in violations[0].lower()

    def test_aggregate_reports_every_bad_score_not_just_first(self, tmp_path):
        """Two of M=3 scores are bad (one missing artifact, one hash mismatch) -> BOTH reported."""
        csv_a = tmp_path / "a.csv"
        csv_a.write_bytes(b"x")
        hash_a = _sha256(csv_a.read_bytes())
        missing_path = tmp_path / "missing.csv"
        bad_hash = "sha256:" + "f" * 64

        scores_yaml = (
            "scores:\n"
            f"  - location: {csv_a}\n"
            f"    hash: {hash_a}\n"
            f"    label: good\n"
            f"  - location: {missing_path}\n"
            f"    hash: sha256:{'a' * 64}\n"
            f"    label: missing-artifact\n"
            f"  - location: {csv_a}\n"
            f"    hash: {bad_hash}\n"
            f"    label: bad-hash\n"
        )
        note_path = self._write_note(tmp_path, scores_yaml)

        violations = check_result_provenance(note_path)
        assert len(violations) == 2, violations
        combined = " ".join(violations)
        assert "missing.csv" in combined and "not found" in combined
        assert "mismatch" in combined
        assert "a.csv" in combined

    def test_empty_scores_list_is_not_a_violation(self, tmp_path):
        """Zero-item scores: (the fresh cmd_new scaffold state) is skip, not a violation."""
        note_path = self._write_note(tmp_path, "scores: \n", runs_yaml="runs: \n")
        assert check_result_provenance(note_path) == []


# ---------------------------------------------------------------------------
# 3b. Reviewer BLOCK regression (PR #147): location-only, hash-empty stub notes
#     must still skip (not-yet-run), matching the shipped demo-research corpus.
# ---------------------------------------------------------------------------

class TestNotYetRunLocationOnlyStub:
    """Reviewer-verified regression: a not-yet-run stub note with
    results_location set but results_hash empty (the shipped
    q1-main1-cabl-Y.md shape — a conditional-ablation note whose trigger
    hasn't fired yet) must skip check_result_provenance (return []), matching
    pre-PR-3 behaviour. The trigger for legacy synthesis is results_hash SET
    (spec §4.2 D2) — NOT "either field set" (the regression: a superset
    trigger flagged this shipped stub as missing 'hash')."""

    def test_normalize_results_location_only_hash_empty_yields_no_scores(self):
        """_normalize_results must NOT synthesize a scores entry from a bare
        results_location with no results_hash — the trigger is results_hash
        SET only."""
        fields = {
            "results_location": "results/q1-main1-cabl-Y/scores.jsonl",
            "results_hash": "",
        }
        norm = _normalize_results(fields)
        assert norm["scores"] == [], (
            f"location-only (hash empty) must not synthesize a scores entry, "
            f"got: {norm['scores']}"
        )

    def test_check_result_provenance_skips_location_only_hash_empty_stub(self, tmp_path):
        """Mirrors the shipped q1-main1-cabl-Y.md stub shape exactly."""
        note_path = tmp_path / "cond-ablation-stub.md"
        note_path.write_text(
            "---\n"
            "type: experiments\n"
            "title: Conditional ablation stub (not yet triggered)\n"
            "trigger: \"q1-main1 results_summary > 0.80\"\n"
            "trigger_result: ''\n"
            "results_location: results/q1-main1-cabl-Y/scores.jsonl\n"
            "results_hash: ''\n"
            "results_commit: ''\n"
            "results_summary: ''\n"
            "---\n",
            encoding="utf-8",
        )
        violations = check_result_provenance(note_path)
        assert violations == [], (
            f"not-yet-run stub (location set, hash empty) must skip (empty "
            f"scores = not a violation), got: {violations}"
        )

    def test_hash_set_location_empty_still_flags_missing_location(self):
        """The inverse case (hash set, location empty) is a genuine violation
        (a real, if malformed, legacy entry) — parity with pre-PR-3 OLD
        semantics is preserved for THIS case, only the location-only trigger
        is narrowed."""
        fields = {"results_location": "", "results_hash": "sha256:" + "a" * 64}
        norm = _normalize_results(fields)
        assert norm["scores"] == [{"location": "", "hash": "sha256:" + "a" * 64}]


class TestShippedDemoCorpusRegression:
    """The gap that let the BLOCK pass CI: no test ever exercised the
    PACKAGED demo-research experiments notes (tmp_instance fixtures write
    their own fresh notes and never touch the shipped not-yet-run stub
    state). Run check_result_provenance over every shipped demo-research
    experiment note and assert none regress."""

    def _demo_experiments_dir(self):
        base = resources.files("research_vault") / "data" / "examples" / "demo-research" / "notes" / "experiments"
        return base

    def test_shipped_demo_experiments_notes_all_verify_clean(self):
        base = self._demo_experiments_dir()
        checked = 0
        for entry in base.iterdir():
            if not entry.name.endswith(".md") or entry.name.startswith("_"):
                continue
            with resources.as_file(entry) as note_path:
                violations = check_result_provenance(note_path)
                assert violations == [], (
                    f"shipped demo note {entry.name} regressed: {violations}"
                )
                checked += 1
        assert checked > 0, "expected at least one shipped demo experiments note to check"

    def test_shipped_q1_main1_cabl_y_specifically_skips(self):
        """The exact note the reviewer verified the BLOCK against."""
        base = self._demo_experiments_dir()
        entry = base / "q1-main1-cabl-Y.md"
        with resources.as_file(entry) as note_path:
            assert note_path.exists(), "shipped stub note missing — check packaging"
            violations = check_result_provenance(note_path)
            assert violations == [], (
                f"q1-main1-cabl-Y.md (location set, hash empty, not-yet-run) "
                f"must skip: {violations}"
            )


# ---------------------------------------------------------------------------
# 4. Legacy flat notes verify UNCHANGED (backward-compat proof)
# ---------------------------------------------------------------------------

class TestLegacyFlatNotesUnchanged:
    def test_legacy_flat_note_with_matching_hash_still_verifies(self, tmp_path):
        artifact = tmp_path / "legacy.results.json"
        artifact.write_bytes(b'{"accuracy": 0.9}')
        good_hash = _sha256(artifact.read_bytes())
        note_path = tmp_path / "legacy-exp.md"
        note_path.write_text(
            "---\n"
            "type: experiments\n"
            "title: Legacy flat note\n"
            f"results_location: {artifact}\n"
            f"results_hash: {good_hash}\n"
            "results_wandb_run: myentity/myproject/run1\n"
            "results_commit: abc123\n"
            "---\n",
            encoding="utf-8",
        )
        assert check_result_provenance(note_path) == []

    def test_legacy_flat_note_with_mismatch_still_fails(self, tmp_path):
        artifact = tmp_path / "legacy2.results.json"
        artifact.write_bytes(b'{"accuracy": 0.9}')
        bad_hash = "sha256:" + "0" * 64
        note_path = tmp_path / "legacy-exp2.md"
        note_path.write_text(
            "---\n"
            "type: experiments\n"
            f"results_location: {artifact}\n"
            f"results_hash: {bad_hash}\n"
            "---\n",
            encoding="utf-8",
        )
        violations = check_result_provenance(note_path)
        assert len(violations) == 1
        assert "mismatch" in violations[0].lower()


# ---------------------------------------------------------------------------
# 5. cmd_new scaffolds empty runs:/scores: (zero items)
# ---------------------------------------------------------------------------

class TestCmdNewScaffoldsEmptyLists:
    def test_experiments_scaffold_has_empty_runs_and_scores(self, tmp_instance):
        cfg = load_config(reload=True)
        path = cmd_new("demo-research", "experiments", "Empty scaffold test", config=cfg)
        text = path.read_text()
        fields, _ = _parse_frontmatter(text)
        # Zero items, not blank placeholder entries — parses back as "" (scalar), not [{}].
        assert fields["scores"] == ""
        assert fields["runs"] == ""
        assert "results_commit" in fields
        # Deprecated flat fields are no longer scaffolded.
        assert "results_location" not in fields
        assert "results_hash" not in fields
        assert "results_wandb_run" not in fields

    def test_empty_scaffold_passes_check_result_provenance(self, tmp_instance):
        """The empty scaffold (zero-item runs:/scores:) is not-yet-run -> gate skips."""
        cfg = load_config(reload=True)
        path = cmd_new("demo-research", "experiments", "Fresh scaffold", config=cfg)
        assert check_result_provenance(path) == []
