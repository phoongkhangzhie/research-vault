"""test_pr_cc1_provenance_chain.py — PR-CC-1: provenance-chain completeness gate.

Design: docs/superpowers/specs/2026-07-07-code-conventions-design.md §3 CHECK-1
(flagship, folds CHECK-2 config-hash-match + CHECK-3a notebook-invariant).

Coverage:
  1.  No scores claimed → [] (not-yet-run, unchanged skip semantics)
  2.  Fully complete chain → [] (all fields non-sentinel, config hash matches,
      dataset linked, seed recorded)
  3.  Missing results_commit → HARD violation (no WARN prefix)
  4.  Sentinel results_commit → HARD violation
  5.  results_commit == not-applicable → exempt (proxy/no-run)
  6.  Missing/sentinel repro_seed → HARD violation (R1 promotion)
  7.  repro_seed == not-applicable → exempt
  8.  Missing repro_config_location → HARD violation
  9.  Missing repro_config_hash → HARD violation
  10. repro_config_location + repro_config_hash both not-applicable → exempt
  11. Config artifact missing on disk → HARD violation
  12. Config hash mismatch → HARD violation
  13. Config hash match (real file) → no violation for that field
  14. Missing dataset link (id+hash both sentinel) → HARD violation
  15. repro_dataset_id == not-applicable → exempt
  16. repro_dataset_hash alone (id sentinel) → satisfies dataset link
  17. Notebook-sourced score location (.ipynb) → HARD violation (CHECK-3a)
  18. Legacy flat note with real result but no chain → HARD-blocked (the exact
      shape a pre-CHECK-1 note has — this is the dogfood's expected failure mode)
  19. Demo-research stub notes (not-yet-run) still pass unchanged
  20. All violations carry NO _WARN_PREFIXES prefix (HARD, never degrades to warn)
  21. cmd_check wiring: violation surfaces, flips exit 1 (via run())
  22. cmd_complete ride: succeeded produces.result node with incomplete chain BLOCKS
  23. cmd_complete ride: succeeded produces.result node with complete chain passes
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault import note as note_mod
from research_vault.config import load_config, reset_config_cache
from research_vault.hashing import hash_file
from research_vault.note import (
    REPRO_SENTINEL,
    REPRO_NOT_APPLICABLE,
    check_provenance_chain,
)


# ---------------------------------------------------------------------------
# Frontmatter builders
# ---------------------------------------------------------------------------

def _complete_fields(config_path: Path, config_hash: str) -> dict[str, str]:
    return {
        "type": "experiments",
        "title": "Test Exp",
        "results_hash": "sha256:" + "a" * 64,
        "results_location": "results/scores.jsonl",
        "results_commit": "abc123deadbeef",
        "repro_seed": "42",
        "repro_config_location": str(config_path),
        "repro_config_hash": config_hash,
        "repro_dataset_id": "datasets/xnli-en",
        "repro_dataset_hash": REPRO_SENTINEL,
    }


def _write_note(path: Path, fields: dict[str, str], body: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["---"]
    for k, v in fields.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append(body)
    path.write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture
def config_artifact(tmp_path):
    """A real config artifact + its correct sha256 hash."""
    config_path = tmp_path / "exp.config.json"
    config_path.write_text(json.dumps({"lr": 0.001, "seed": 42}), encoding="utf-8")
    return config_path, hash_file(config_path)


# ===========================================================================
# 1-2. Baseline: no scores / complete chain
# ===========================================================================

class TestBaseline:
    def test_no_scores_claimed_returns_empty(self, tmp_path):
        note = tmp_path / "exp.md"
        _write_note(note, {"type": "experiments", "title": "Stub"})
        assert check_provenance_chain(note) == []

    def test_complete_chain_returns_empty(self, tmp_path, config_artifact):
        config_path, config_hash = config_artifact
        note = tmp_path / "exp.md"
        _write_note(note, _complete_fields(config_path, config_hash))
        violations = check_provenance_chain(note)
        assert violations == [], f"Expected clean chain, got: {violations}"


# ===========================================================================
# 3-5. results_commit
# ===========================================================================

class TestResultsCommit:
    def test_missing_results_commit_blocks(self, tmp_path, config_artifact):
        config_path, config_hash = config_artifact
        fields = _complete_fields(config_path, config_hash)
        fields["results_commit"] = ""
        note = tmp_path / "exp.md"
        _write_note(note, fields)
        violations = check_provenance_chain(note)
        assert any("results_commit" in v for v in violations)

    def test_sentinel_results_commit_blocks(self, tmp_path, config_artifact):
        config_path, config_hash = config_artifact
        fields = _complete_fields(config_path, config_hash)
        fields["results_commit"] = REPRO_SENTINEL
        note = tmp_path / "exp.md"
        _write_note(note, fields)
        violations = check_provenance_chain(note)
        assert any("results_commit" in v for v in violations)

    def test_not_applicable_results_commit_blocks(self, tmp_path, config_artifact):
        """TIGHTENED (2026-07-07, operator review call): results_commit is
        ALWAYS required once a result is claimed — REPRO_NOT_APPLICABLE no
        longer exempts it. A note cannot escape the "traces to a commit"
        guarantee by marking results_commit itself not-applicable."""
        config_path, config_hash = config_artifact
        fields = _complete_fields(config_path, config_hash)
        fields["results_commit"] = REPRO_NOT_APPLICABLE
        note = tmp_path / "exp.md"
        _write_note(note, fields)
        violations = check_provenance_chain(note)
        assert any("results_commit" in v for v in violations)


# ===========================================================================
# 6-7. repro_seed (R1 promotion)
# ===========================================================================

class TestReproSeed:
    def test_missing_seed_blocks(self, tmp_path, config_artifact):
        config_path, config_hash = config_artifact
        fields = _complete_fields(config_path, config_hash)
        fields["repro_seed"] = REPRO_SENTINEL
        note = tmp_path / "exp.md"
        _write_note(note, fields)
        violations = check_provenance_chain(note)
        assert any("repro_seed" in v for v in violations)

    def test_not_applicable_seed_blocks(self, tmp_path, config_artifact):
        """TIGHTENED (2026-07-07, operator review call): repro_seed is ALWAYS
        required once a result is claimed — REPRO_NOT_APPLICABLE no longer
        exempts it. Same rationale as results_commit."""
        config_path, config_hash = config_artifact
        fields = _complete_fields(config_path, config_hash)
        fields["repro_seed"] = REPRO_NOT_APPLICABLE
        note = tmp_path / "exp.md"
        _write_note(note, fields)
        violations = check_provenance_chain(note)
        assert any("repro_seed" in v for v in violations)


# ===========================================================================
# 8-13. Config chain (CHECK-2 folded)
# ===========================================================================

class TestConfigChain:
    def test_missing_config_location_blocks(self, tmp_path, config_artifact):
        config_path, config_hash = config_artifact
        fields = _complete_fields(config_path, config_hash)
        fields["repro_config_location"] = REPRO_SENTINEL
        note = tmp_path / "exp.md"
        _write_note(note, fields)
        violations = check_provenance_chain(note)
        assert any("repro_config_location" in v for v in violations)

    def test_missing_config_hash_blocks(self, tmp_path, config_artifact):
        config_path, config_hash = config_artifact
        fields = _complete_fields(config_path, config_hash)
        fields["repro_config_hash"] = REPRO_SENTINEL
        note = tmp_path / "exp.md"
        _write_note(note, fields)
        violations = check_provenance_chain(note)
        assert any("repro_config_hash" in v for v in violations)

    def test_config_both_not_applicable_exempt(self, tmp_path):
        """results_commit/repro_seed are ALWAYS required (tightened 2026-07-07)
        so this proxy fixture supplies real values for those two; only the
        genuinely-exemptible fields (config artifact, dataset link) are
        marked not-applicable."""
        note = tmp_path / "exp.md"
        fields = {
            "type": "experiments",
            "title": "Proxy",
            "results_hash": "sha256:" + "a" * 64,
            "results_location": "results/scores.jsonl",
            "results_commit": "abc123deadbeef",
            "repro_seed": "42",
            "repro_config_location": REPRO_NOT_APPLICABLE,
            "repro_config_hash": REPRO_NOT_APPLICABLE,
            "repro_dataset_id": REPRO_NOT_APPLICABLE,
        }
        _write_note(note, fields)
        violations = check_provenance_chain(note)
        assert violations == [], f"Expected proxy exemption, got: {violations}"

    def test_config_artifact_missing_on_disk_blocks(self, tmp_path):
        fields = _complete_fields(tmp_path / "does-not-exist.json", "sha256:" + "b" * 64)
        note = tmp_path / "exp.md"
        _write_note(note, fields)
        violations = check_provenance_chain(note)
        assert any("not found" in v and "config" in v for v in violations)

    def test_config_hash_mismatch_blocks(self, tmp_path, config_artifact):
        config_path, _real_hash = config_artifact
        fields = _complete_fields(config_path, "sha256:" + "f" * 64)
        note = tmp_path / "exp.md"
        _write_note(note, fields)
        violations = check_provenance_chain(note)
        assert any("mismatch" in v for v in violations)

    def test_config_hash_match_no_violation(self, tmp_path, config_artifact):
        config_path, config_hash = config_artifact
        fields = _complete_fields(config_path, config_hash)
        note = tmp_path / "exp.md"
        _write_note(note, fields)
        violations = check_provenance_chain(note)
        assert not any("mismatch" in v or "not found" in v for v in violations)


# ===========================================================================
# 14-16. Dataset link
# ===========================================================================

class TestDatasetLink:
    def test_missing_dataset_link_blocks(self, tmp_path, config_artifact):
        config_path, config_hash = config_artifact
        fields = _complete_fields(config_path, config_hash)
        fields["repro_dataset_id"] = REPRO_SENTINEL
        fields["repro_dataset_hash"] = REPRO_SENTINEL
        note = tmp_path / "exp.md"
        _write_note(note, fields)
        violations = check_provenance_chain(note)
        assert any("dataset" in v for v in violations)

    def test_dataset_not_applicable_exempt(self, tmp_path, config_artifact):
        config_path, config_hash = config_artifact
        fields = _complete_fields(config_path, config_hash)
        fields["repro_dataset_id"] = REPRO_NOT_APPLICABLE
        fields["repro_dataset_hash"] = REPRO_SENTINEL
        note = tmp_path / "exp.md"
        _write_note(note, fields)
        violations = check_provenance_chain(note)
        assert not any("dataset" in v for v in violations)

    def test_dataset_hash_alone_satisfies_link(self, tmp_path, config_artifact):
        config_path, config_hash = config_artifact
        fields = _complete_fields(config_path, config_hash)
        fields["repro_dataset_id"] = REPRO_SENTINEL
        fields["repro_dataset_hash"] = "sha256:" + "c" * 64
        note = tmp_path / "exp.md"
        _write_note(note, fields)
        violations = check_provenance_chain(note)
        assert not any("dataset" in v for v in violations)


# ===========================================================================
# 17. Notebook invariant (CHECK-3a)
# ===========================================================================

class TestNotebookInvariant:
    def test_notebook_sourced_score_blocks(self, tmp_path, config_artifact):
        config_path, config_hash = config_artifact
        note = tmp_path / "exp.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        text = (
            "---\n"
            "type: experiments\n"
            "title: Notebook exp\n"
            "results_commit: abc123\n"
            "repro_seed: '42'\n"
            f"repro_config_location: {config_path}\n"
            f"repro_config_hash: {config_hash}\n"
            "repro_dataset_id: not-applicable\n"
            "scores:\n"
            "  - label: main\n"
            "    location: notebooks/explore.ipynb\n"
            "    hash: sha256:" + "d" * 64 + "\n"
            "---\n"
        )
        note.write_text(text, encoding="utf-8")
        violations = check_provenance_chain(note)
        assert any("notebook" in v.lower() or ".ipynb" in v for v in violations)


# ===========================================================================
# 18. Legacy flat note with real result but no chain (dogfood shape)
# ===========================================================================

class TestLegacyDogfoodShape:
    def test_legacy_note_with_result_no_chain_blocks(self, tmp_path):
        """A note claiming a result but leaving the whole chain sentinel — the
        exact shape rv's pre-CHECK-1 notes have (this IS the dogfood's target)."""
        note = tmp_path / "exp.md"
        _write_note(note, {
            "type": "experiments",
            "title": "Legacy exp",
            "results_hash": "sha256:" + "e" * 64,
            "results_location": "results/scores.jsonl",
            "results_commit": "",
            # repro_* fields absent entirely — simulating a pre-SR-EXP-REPRO note
        })
        violations = check_provenance_chain(note)
        assert violations, "expected the incomplete chain to be flagged"
        assert any("results_commit" in v for v in violations)
        assert any("repro_seed" in v for v in violations)
        assert any("repro_config_location" in v for v in violations)
        assert any("dataset" in v for v in violations)


# ===========================================================================
# 19. demo-research stub notes still pass (backward compat)
# ===========================================================================

class TestDemoResearchBackwardCompat:
    def test_stub_notes_unaffected(self):
        demo_dir = (
            Path(__file__).parent.parent
            / "src" / "research_vault" / "data" / "examples"
            / "demo-research" / "notes" / "experiments"
        )
        stub_notes = sorted(demo_dir.glob("*.md"))
        assert stub_notes, "expected shipped demo-research experiment notes to exist"
        for note_path in stub_notes:
            if note_path.name == "_PLACEHOLDER.md":
                continue
            violations = check_provenance_chain(note_path)
            assert violations == [], (
                f"{note_path.name}: expected stub (not-yet-run) note to pass "
                f"CHECK-1 unchanged, got: {violations}"
            )


# ===========================================================================
# 20. HARD — never carries a WARN prefix
# ===========================================================================

class TestHardSeverity:
    def test_violations_never_warn_prefixed(self, tmp_path):
        note = tmp_path / "exp.md"
        _write_note(note, {
            "type": "experiments",
            "title": "Incomplete",
            "results_hash": "sha256:" + "a" * 64,
            "results_location": "x",
            "results_commit": "",
        })
        violations = check_provenance_chain(note)
        assert violations
        _WARN_PREFIXES = ("[repro-lint]", "[gap-hygiene]", "[dataset-provenance]")
        for v in violations:
            assert not v.startswith(_WARN_PREFIXES), (
                f"CHECK-1 must be HARD (no warn prefix), got: {v!r}"
            )


# ===========================================================================
# 21. cmd_check wiring
# ===========================================================================

class TestCmdCheckWiring:
    def test_incomplete_chain_flips_cmd_check_exit(self, tmp_instance, monkeypatch):
        cfg = load_config(reload=True)
        note_path = note_mod.cmd_new("demo-research", "experiments", "Incomplete Exp", config=cfg)
        from research_vault.wandb_pull import _update_frontmatter
        _update_frontmatter(note_path, {
            "results_hash": "sha256:" + "a" * 64,
            "results_location": "results/scores.jsonl",
            # results_commit / repro_seed / repro_config_* / repro_dataset_*
            # remain the cmd_new sentinel default -> incomplete chain
        })

        violations = note_mod.cmd_check("demo-research", config=cfg)
        hard = [
            v for v in violations
            if not v.startswith(("[repro-lint]", "[gap-hygiene]", "[dataset-provenance]"))
        ]
        assert hard, f"expected HARD chain violations, got only: {violations}"

        args = argparse.Namespace(project="demo-research", note_cmd="check")
        rc = note_mod.run(args)
        assert rc == 1


# ===========================================================================
# 22-23. cmd_complete ride (the DAG complete-gate)
# ===========================================================================

class TestCompleteGateRide:
    def _run_dag(self, manifest_path: Path) -> None:
        from research_vault.dag.verbs import cmd_run
        args = argparse.Namespace(manifest=str(manifest_path))
        rc = cmd_run(args)
        assert rc == 0, f"cmd_run failed: rc={rc}"

    def _agent_node(self, nid, produces):
        return {"id": nid, "type": "agent", "spec": "task://test#stub", "produces": produces}

    def _argns(self, **kwargs):
        ns = argparse.Namespace()
        defaults = {"status": "succeeded", "manifest": None, "run_id": None, "node_id": None}
        defaults.update(kwargs)
        for k, v in defaults.items():
            setattr(ns, k, v)
        return ns

    def test_incomplete_chain_blocks_complete_gate(self, tmp_instance, config_artifact):
        from research_vault.dag.verbs import cmd_complete
        cfg = load_config(reload=True)
        run_id = "test-cc1-incomplete"
        m = {
            "run_id": run_id,
            "nodes": [self._agent_node("writer", {"result": "demo-research/exp-cc1-a"})],
        }
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")
        self._run_dag(mf)

        note = cfg.project_notes_dir("demo-research") / "experiments" / "exp-cc1-a.md"
        _write_note(note, {
            "type": "experiments",
            "title": "Incomplete chain",
            "results_hash": "sha256:" + "a" * 64,
            "results_location": "results/scores.jsonl",
            "results_commit": "",  # incomplete
        })

        rc = cmd_complete(self._argns(run_id=run_id, node_id="writer", status="succeeded"))
        assert rc == 1

    def test_complete_chain_passes_complete_gate(self, tmp_instance, config_artifact):
        from research_vault.dag.verbs import cmd_complete
        cfg = load_config(reload=True)
        config_path, config_hash = config_artifact
        run_id = "test-cc1-complete"
        m = {
            "run_id": run_id,
            "nodes": [self._agent_node("writer", {"result": "demo-research/exp-cc1-b"})],
        }
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")
        self._run_dag(mf)

        note = cfg.project_notes_dir("demo-research") / "experiments" / "exp-cc1-b.md"
        _write_note(note, _complete_fields(config_path, config_hash))

        rc = cmd_complete(self._argns(run_id=run_id, node_id="writer", status="succeeded"))
        assert rc == 0
