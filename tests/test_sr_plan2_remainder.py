"""test_sr_plan2_remainder.py — SR-PLAN-2 REMAINDER acceptance tests.

Covers the three §5K.7 items deferred from PR #32:

  1. covers:/stance link-validation (the note.py touch)
       - Plan master: each covers: child EXISTS with valid stance + plan_role
       - BLOCKs on missing child, invalid/missing stance, invalid/missing plan_role
       - Child note: supports_main target exists; warns if plan_role set but stance missing
       - Child note: stance=confirmatory but absent from any plan master's covers:

  2. rv result assert — predicate-assertion verb
       - Reads the experiment note's results_location + verifies results_hash
       - Extracts metric M from JSON at results_location
       - Evaluates M OP V (gt, lt, ge, le, eq, ne)
       - Exits 0 if true, 1 if false
       - Is usable as a watch: cmd: predicate in the DAG

  3. predicate-hash-into-run-state (§5K.5.4)
       - When --run-id is given, logs predicate string + hash + result to run state
       - meta["predicate_log"][<node-id>] = {predicate, predicate_hash, metric,
           op, value, metric_actual, result, evaluated_at}

Scope ground-check (§5K.7 verification):
  Item 1 — CONFIRMED: "the note.py validation moved here (5K.10): rv note check
            resolves the master's covers: links, checks each supports_main: target
            exists, and warns on a confirmatory note missing stance/absent from covers:"
  Item 2 — CONFIRMED: "The thin rv result assert <exp> --metric M --op gt --value V
            helper (frozen-predicate evaluation over the hash-verified results_location,
            logged to run state)"
  Item 3 — CONFIRMED (5K.5.4): "Log each conditional's verbatim predicate string +
            its evaluated result into the DAG run state"
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _exp_note(
    exp_dir: Path,
    note_id: str,
    *,
    stance: str = "",
    plan_role: str = "",
    plan_kind: str = "",
    covers: str = "",
    supports_main: str = "",
    results_location: str = "",
    results_hash: str = "",
) -> Path:
    """Write a minimal experiment note into exp_dir and return its path."""
    exp_dir.mkdir(parents=True, exist_ok=True)
    p = exp_dir / f"{note_id}.md"
    fm_lines = ["type: experiments", f"citekey: {note_id}"]
    if plan_kind:
        fm_lines.append(f"plan_kind: {plan_kind}")
    if covers:
        fm_lines.append(f"covers: {covers}")
    if stance:
        fm_lines.append(f"stance: {stance}")
    if plan_role:
        fm_lines.append(f"plan_role: {plan_role}")
    if supports_main:
        fm_lines.append(f"supports_main: {supports_main}")
    if results_location:
        fm_lines.append(f"results_location: {results_location}")
    if results_hash:
        fm_lines.append(f"results_hash: {results_hash}")
    p.write_text("---\n" + "\n".join(fm_lines) + f"\n---\n\n# {note_id}\n", encoding="utf-8")
    return p


def _results_json(tmp_path: Path, data: dict, filename: str = "results.json") -> Path:
    """Write a JSON results file and return its path."""
    p = tmp_path / filename
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _results_json_hash(p: Path) -> str:
    """Compute sha256:<hex> of the given file."""
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


# ===========================================================================
# 1. covers:/stance link-validation (note.py cmd_check experiments elif)
# ===========================================================================

class TestCoversLinkValidation:
    """SR-PLAN-2: covers: link-validation in rv note check (plan masters)."""

    def _exp_dir(self, tmp_instance: Path) -> Path:
        """Return the experiments dir for 'demo-research' project (via config)."""
        from research_vault.config import load_config
        cfg = load_config()
        return cfg.project_notes_dir("demo-research") / "experiments"

    def _run_check(self, tmp_instance: Path) -> list[str]:
        from research_vault.note import cmd_check
        from research_vault.config import load_config
        cfg = load_config()
        return cmd_check("demo-research", config=cfg)

    def test_valid_plan_master_passes(self, tmp_instance):
        """A plan master with all covers: children present + valid fields passes."""
        exp_dir = self._exp_dir(tmp_instance)
        _exp_note(exp_dir, "q1-plan", plan_kind="preregistration",
                  covers="[q1-main1, q1-abl-A]")
        _exp_note(exp_dir, "q1-main1", stance="confirmatory", plan_role="main")
        _exp_note(exp_dir, "q1-abl-A", stance="confirmatory",
                  plan_role="supporting_ablation")

        violations = self._run_check(tmp_instance)
        plan_violations = [v for v in violations if "covers" in v.lower() and "child" in v.lower()]
        assert not plan_violations, (
            f"Valid plan master should produce no covers: violations; got: {violations}"
        )

    def test_missing_child_blocks(self, tmp_instance):
        """A covers: child that doesn't exist → BLOCK violation."""
        exp_dir = self._exp_dir(tmp_instance)
        _exp_note(exp_dir, "q1-plan", plan_kind="preregistration",
                  covers="[q1-main1, q1-missing]")
        _exp_note(exp_dir, "q1-main1", stance="confirmatory", plan_role="main")
        # q1-missing note is NOT created

        violations = self._run_check(tmp_instance)
        assert any("q1-missing" in v and "not found" in v for v in violations), (
            f"Expected violation for missing covers: child; got: {violations}"
        )

    def test_child_missing_stance_blocks(self, tmp_instance):
        """A covers: child missing 'stance' → violation."""
        exp_dir = self._exp_dir(tmp_instance)
        _exp_note(exp_dir, "q1-plan", plan_kind="preregistration", covers="[q1-main1]")
        _exp_note(exp_dir, "q1-main1", plan_role="main")  # no stance

        violations = self._run_check(tmp_instance)
        assert any(
            "q1-main1" in v and "stance" in v.lower()
            for v in violations
        ), f"Expected stance violation for covers: child; got: {violations}"

    def test_child_invalid_stance_blocks(self, tmp_instance):
        """A covers: child with invalid stance → violation."""
        exp_dir = self._exp_dir(tmp_instance)
        _exp_note(exp_dir, "q1-plan", plan_kind="preregistration", covers="[q1-main1]")
        _exp_note(exp_dir, "q1-main1", stance="pre-registered", plan_role="main")

        violations = self._run_check(tmp_instance)
        assert any(
            "q1-main1" in v and "stance" in v.lower()
            for v in violations
        ), f"Expected invalid-stance violation; got: {violations}"

    def test_child_missing_plan_role_blocks(self, tmp_instance):
        """A covers: child missing 'plan_role' → violation."""
        exp_dir = self._exp_dir(tmp_instance)
        _exp_note(exp_dir, "q1-plan", plan_kind="preregistration", covers="[q1-main1]")
        _exp_note(exp_dir, "q1-main1", stance="confirmatory")  # no plan_role

        violations = self._run_check(tmp_instance)
        assert any(
            "q1-main1" in v and "plan_role" in v.lower()
            for v in violations
        ), f"Expected plan_role violation for covers: child; got: {violations}"

    def test_child_invalid_plan_role_blocks(self, tmp_instance):
        """A covers: child with invalid plan_role → violation."""
        exp_dir = self._exp_dir(tmp_instance)
        _exp_note(exp_dir, "q1-plan", plan_kind="preregistration", covers="[q1-main1]")
        _exp_note(exp_dir, "q1-main1", stance="confirmatory", plan_role="bad-role")

        violations = self._run_check(tmp_instance)
        assert any(
            "q1-main1" in v and "plan_role" in v.lower()
            for v in violations
        ), f"Expected invalid plan_role violation; got: {violations}"

    def test_exploratory_stance_valid(self, tmp_instance):
        """exploratory is a valid stance."""
        exp_dir = self._exp_dir(tmp_instance)
        _exp_note(exp_dir, "q1-plan", plan_kind="preregistration",
                  covers="[q1-main1, q1-abl-A]")
        _exp_note(exp_dir, "q1-main1", stance="confirmatory", plan_role="main")
        _exp_note(exp_dir, "q1-abl-A", stance="exploratory",
                  plan_role="supporting_ablation")

        violations = self._run_check(tmp_instance)
        covers_violations = [
            v for v in violations if "covers" in v.lower() and "child" in v.lower()
        ]
        assert not covers_violations, (
            f"exploratory stance should be valid; got: {violations}"
        )

    def test_all_plan_roles_valid(self, tmp_instance):
        """main, supporting_ablation, conditional_ablation are all valid plan_role values."""
        exp_dir = self._exp_dir(tmp_instance)
        _exp_note(exp_dir, "q1-plan", plan_kind="preregistration",
                  covers="[q1-main1, q1-abl-A, q1-cabl-X]")
        _exp_note(exp_dir, "q1-main1", stance="confirmatory", plan_role="main")
        _exp_note(exp_dir, "q1-abl-A", stance="confirmatory",
                  plan_role="supporting_ablation")
        _exp_note(exp_dir, "q1-cabl-X", stance="confirmatory",
                  plan_role="conditional_ablation")

        violations = self._run_check(tmp_instance)
        covers_violations = [
            v for v in violations if "covers" in v.lower() and "child" in v.lower()
        ]
        assert not covers_violations, (
            f"All valid plan_role values should pass; got: {violations}"
        )

    def test_non_plan_note_not_validated(self, tmp_instance):
        """An experiments note without plan_kind: preregistration is not plan-master-validated."""
        exp_dir = self._exp_dir(tmp_instance)
        _exp_note(exp_dir, "ordinary-run")  # no plan_kind, no covers:

        violations = self._run_check(tmp_instance)
        covers_violations = [
            v for v in violations if "covers" in v.lower() and "child" in v.lower()
        ]
        assert not covers_violations

    def test_multiple_children_multiple_violations(self, tmp_instance):
        """Each invalid child gets its own violation."""
        exp_dir = self._exp_dir(tmp_instance)
        _exp_note(exp_dir, "q1-plan", plan_kind="preregistration",
                  covers="[q1-main1, q1-main2]")
        # Both children missing stance + plan_role
        _exp_note(exp_dir, "q1-main1")
        _exp_note(exp_dir, "q1-main2")

        violations = self._run_check(tmp_instance)
        covers_violations = [v for v in violations if "covers" in v.lower()]
        # At least 2 violations (one set per child)
        assert len(covers_violations) >= 2, (
            f"Expected ≥2 covers: violations (one per child); got: {violations}"
        )


class TestChildNoteValidation:
    """SR-PLAN-2: child note checks (supports_main, stance presence, absent-from-covers)."""

    def _exp_dir(self, tmp_instance: Path) -> Path:
        from research_vault.config import load_config
        cfg = load_config()
        return cfg.project_notes_dir("demo-research") / "experiments"

    def _run_check(self, tmp_instance: Path) -> list[str]:
        from research_vault.note import cmd_check
        from research_vault.config import load_config
        cfg = load_config()
        return cmd_check("demo-research", config=cfg)

    def test_plan_role_without_stance_warns(self, tmp_instance):
        """A child note with plan_role but no stance → violation (stance required)."""
        exp_dir = self._exp_dir(tmp_instance)
        _exp_note(exp_dir, "q1-main1", plan_role="main")  # no stance

        violations = self._run_check(tmp_instance)
        assert any(
            "stance" in v.lower() and "plan_role" in v.lower()
            for v in violations
        ), f"Expected stance-missing warning for plan_role note; got: {violations}"

    def test_plan_role_with_stance_passes(self, tmp_instance):
        """A child note with both plan_role and stance → no child-note violation."""
        exp_dir = self._exp_dir(tmp_instance)
        _exp_note(exp_dir, "q1-main1", stance="confirmatory", plan_role="main")

        violations = self._run_check(tmp_instance)
        child_viol = [
            v for v in violations
            if "plan_role" in v.lower() and "stance" in v.lower()
        ]
        assert not child_viol, (
            f"Child with both fields should pass child-note validation; got: {violations}"
        )

    def test_supports_main_target_missing_blocks(self, tmp_instance):
        """A child note with supports_main pointing to a missing note → violation."""
        exp_dir = self._exp_dir(tmp_instance)
        _exp_note(exp_dir, "q1-abl-A", stance="confirmatory",
                  plan_role="supporting_ablation",
                  supports_main="q1-main1")  # q1-main1 doesn't exist

        violations = self._run_check(tmp_instance)
        assert any(
            "supports_main" in v.lower() and "q1-main1" in v
            for v in violations
        ), f"Expected supports_main violation; got: {violations}"

    def test_supports_main_target_present_passes(self, tmp_instance):
        """A child note with supports_main pointing to an existing note → no violation."""
        exp_dir = self._exp_dir(tmp_instance)
        _exp_note(exp_dir, "q1-main1", stance="confirmatory", plan_role="main")
        _exp_note(exp_dir, "q1-abl-A", stance="confirmatory",
                  plan_role="supporting_ablation",
                  supports_main="q1-main1")

        violations = self._run_check(tmp_instance)
        sm_viol = [v for v in violations if "supports_main" in v.lower()]
        assert not sm_viol, (
            f"Valid supports_main target should pass; got: {violations}"
        )

    def test_confirmatory_absent_from_covers_warns(self, tmp_instance):
        """stance=confirmatory + plan_role but NOT in any plan master's covers: → violation."""
        exp_dir = self._exp_dir(tmp_instance)
        # Plan master exists but does NOT list q1-main1 in covers:
        _exp_note(exp_dir, "q1-plan", plan_kind="preregistration", covers="[q1-main2]")
        _exp_note(exp_dir, "q1-main2", stance="confirmatory", plan_role="main")
        # q1-main1 is confirmatory+plan_role but NOT in covers:
        _exp_note(exp_dir, "q1-main1", stance="confirmatory", plan_role="main")

        violations = self._run_check(tmp_instance)
        absent_violations = [
            v for v in violations
            if "q1-main1" in v and "covers" in v.lower() and "confirmatory" in v.lower()
        ]
        assert absent_violations, (
            f"Expected absent-from-covers violation for q1-main1; got: {violations}"
        )

    def test_no_plan_masters_no_absent_violation(self, tmp_instance):
        """If there are no plan masters, absent-from-covers check is skipped."""
        exp_dir = self._exp_dir(tmp_instance)
        # Confirmatory child note but no plan master at all
        _exp_note(exp_dir, "q1-main1", stance="confirmatory", plan_role="main")

        violations = self._run_check(tmp_instance)
        absent_violations = [
            v for v in violations
            if "q1-main1" in v and "covers" in v.lower() and "confirmatory" in v.lower()
        ]
        assert not absent_violations, (
            f"No absent-from-covers violation expected without plan masters; got: {violations}"
        )

    def test_exploratory_not_flagged_for_absent_covers(self, tmp_instance):
        """exploratory notes are NOT required to be in covers: — only confirmatory."""
        exp_dir = self._exp_dir(tmp_instance)
        _exp_note(exp_dir, "q1-plan", plan_kind="preregistration", covers="[q1-main1]")
        _exp_note(exp_dir, "q1-main1", stance="confirmatory", plan_role="main")
        # Exploratory note — NOT in covers: (correct per spec)
        _exp_note(exp_dir, "q1-explore", stance="exploratory",
                  plan_role="conditional_ablation")

        violations = self._run_check(tmp_instance)
        explore_absent = [
            v for v in violations
            if "q1-explore" in v and "covers" in v.lower() and "confirmatory" in v.lower()
        ]
        assert not explore_absent, (
            f"Exploratory note should NOT be flagged as absent from covers:; got: {violations}"
        )


# ===========================================================================
# 2+3. rv result assert — predicate-assertion verb + predicate-hash-into-run-state
# ===========================================================================

class TestResultAssert:
    """rv result assert: predicate-assertion verb (§5K.7 items 2+3)."""

    def _build_exp_note(
        self,
        tmp_path: Path,
        note_id: str,
        results_data: dict,
        *,
        include_hash: bool = True,
    ) -> tuple[Path, Path]:
        """Write experiment note + JSON results file, return (note_path, results_path)."""
        results_p = _results_json(tmp_path, results_data, f"{note_id}-results.json")
        results_hash = _results_json_hash(results_p) if include_hash else ""

        exp_dir = tmp_path / "experiments"
        exp_dir.mkdir(parents=True, exist_ok=True)
        note_p = exp_dir / f"{note_id}.md"
        fm = f"type: experiments\ncitekey: {note_id}"
        fm += f"\nresults_location: {results_p}"
        if results_hash:
            fm += f"\nresults_hash: {results_hash}"
        note_p.write_text(f"---\n{fm}\n---\n\n# {note_id}\n", encoding="utf-8")
        return note_p, results_p

    def _store(self, tmp_path: Path):
        """Return a RunStore backed by tmp_path/state."""
        from research_vault.dag.store import RunStore
        return RunStore(tmp_path / "state")

    def _run_assert(self, args_list: list[str]) -> int:
        """Run rv result assert via the verbs module, return exit code."""
        import argparse
        from research_vault.result import build_parser, run as result_run

        parent = argparse.ArgumentParser()
        subs = parent.add_subparsers()
        build_parser(subs)
        args = parent.parse_args(args_list)
        return result_run(args)

    # -----------------------------------------------------------------------
    # 2a. Basic predicate evaluation — exits 0 when true, 1 when false
    # -----------------------------------------------------------------------

    def test_gt_true_exits_0(self, tmp_path):
        """M > V → exit 0."""
        note_p, _ = self._build_exp_note(tmp_path, "run1", {"accuracy": 0.85})
        rc = self._run_assert([
            "result", "assert", str(note_p),
            "--metric", "accuracy", "--op", "gt", "--value", "0.8",
        ])
        assert rc == 0, "0.85 > 0.8 should be True → exit 0"

    def test_gt_false_exits_1(self, tmp_path):
        """M > V false → exit 1."""
        note_p, _ = self._build_exp_note(tmp_path, "run1", {"accuracy": 0.75})
        rc = self._run_assert([
            "result", "assert", str(note_p),
            "--metric", "accuracy", "--op", "gt", "--value", "0.8",
        ])
        assert rc == 1, "0.75 > 0.8 should be False → exit 1"

    def test_lt_true_exits_0(self, tmp_path):
        """M < V → exit 0."""
        note_p, _ = self._build_exp_note(tmp_path, "run1", {"loss": 0.3})
        rc = self._run_assert([
            "result", "assert", str(note_p),
            "--metric", "loss", "--op", "lt", "--value", "0.5",
        ])
        assert rc == 0

    def test_ge_true_exits_0(self, tmp_path):
        """M >= V (exact) → exit 0."""
        note_p, _ = self._build_exp_note(tmp_path, "run1", {"f1": 0.8})
        rc = self._run_assert([
            "result", "assert", str(note_p),
            "--metric", "f1", "--op", "ge", "--value", "0.8",
        ])
        assert rc == 0, "0.8 >= 0.8 should be True → exit 0"

    def test_le_true_exits_0(self, tmp_path):
        """M <= V (exact) → exit 0."""
        note_p, _ = self._build_exp_note(tmp_path, "run1", {"error_rate": 0.5})
        rc = self._run_assert([
            "result", "assert", str(note_p),
            "--metric", "error_rate", "--op", "le", "--value", "0.5",
        ])
        assert rc == 0

    def test_eq_true_exits_0(self, tmp_path):
        """M == V → exit 0."""
        note_p, _ = self._build_exp_note(tmp_path, "run1", {"score": 1.0})
        rc = self._run_assert([
            "result", "assert", str(note_p),
            "--metric", "score", "--op", "eq", "--value", "1.0",
        ])
        assert rc == 0

    def test_ne_true_exits_0(self, tmp_path):
        """M != V → exit 0."""
        note_p, _ = self._build_exp_note(tmp_path, "run1", {"score": 0.9})
        rc = self._run_assert([
            "result", "assert", str(note_p),
            "--metric", "score", "--op", "ne", "--value", "1.0",
        ])
        assert rc == 0

    # -----------------------------------------------------------------------
    # 2b. Hash verification — only when results_hash is set
    # -----------------------------------------------------------------------

    def test_hash_verified_correct_passes(self, tmp_path):
        """Correct hash → assertion runs normally."""
        note_p, _ = self._build_exp_note(tmp_path, "run1", {"accuracy": 0.9},
                                         include_hash=True)
        rc = self._run_assert([
            "result", "assert", str(note_p),
            "--metric", "accuracy", "--op", "gt", "--value", "0.8",
        ])
        assert rc == 0

    def test_no_hash_field_still_evaluates(self, tmp_path):
        """Missing results_hash → no hash check, predicate evaluates normally."""
        note_p, _ = self._build_exp_note(tmp_path, "run1", {"accuracy": 0.9},
                                         include_hash=False)
        rc = self._run_assert([
            "result", "assert", str(note_p),
            "--metric", "accuracy", "--op", "gt", "--value", "0.8",
        ])
        assert rc == 0

    def test_wrong_hash_exits_1(self, tmp_path):
        """Tampered results_hash → hash mismatch exits 1."""
        results_p = _results_json(tmp_path, {"accuracy": 0.9}, "run1-results.json")

        exp_dir = tmp_path / "experiments"
        exp_dir.mkdir(parents=True, exist_ok=True)
        note_p = exp_dir / "run1.md"
        note_p.write_text(
            f"---\ntype: experiments\ncitekey: run1\n"
            f"results_location: {results_p}\n"
            f"results_hash: sha256:0000000000000000000000000000000000000000000000000000000000000000\n"
            f"---\n\n# run1\n",
            encoding="utf-8",
        )
        rc = self._run_assert([
            "result", "assert", str(note_p),
            "--metric", "accuracy", "--op", "gt", "--value", "0.8",
        ])
        assert rc == 1, "Tampered hash should cause exit 1"

    # -----------------------------------------------------------------------
    # 2c. Metric extraction
    # -----------------------------------------------------------------------

    def test_missing_metric_key_exits_1(self, tmp_path):
        """Metric key not in results JSON → exit 1 (can't evaluate)."""
        note_p, _ = self._build_exp_note(tmp_path, "run1", {"loss": 0.3})
        rc = self._run_assert([
            "result", "assert", str(note_p),
            "--metric", "accuracy", "--op", "gt", "--value", "0.5",
        ])
        assert rc == 1, "Missing metric key should exit 1"

    def test_dotpath_metric_extraction(self, tmp_path):
        """Dot-path metric key 'metrics.accuracy' extracts nested JSON value."""
        note_p, _ = self._build_exp_note(
            tmp_path, "run1", {"metrics": {"accuracy": 0.88}}
        )
        rc = self._run_assert([
            "result", "assert", str(note_p),
            "--metric", "metrics.accuracy", "--op", "gt", "--value", "0.8",
        ])
        assert rc == 0, "Dot-path metric extraction should work"

    # -----------------------------------------------------------------------
    # 3. Predicate-hash into run state (§5K.5.4)
    # -----------------------------------------------------------------------

    def test_run_id_logs_predicate_to_run_state(self, tmp_instance):
        """--run-id causes predicate + hash to be logged to run state meta."""
        from research_vault.dag.store import RunState, RunStore
        from research_vault.config import load_config

        cfg = load_config()
        store = RunStore.from_config(cfg)
        rs = RunState(run_id="dag-run-1", manifest_path=str(tmp_instance / "m.json"))
        store.create(rs)

        note_p, _ = self._build_exp_note(tmp_instance, "run1", {"accuracy": 0.9})
        cmd = [
            "result", "assert", str(note_p),
            "--metric", "accuracy", "--op", "gt", "--value", "0.8",
            "--run-id", "dag-run-1", "--node-id", "human-go-conditionals-main1",
        ]
        rc = self._run_assert(cmd)
        assert rc == 0

        loaded = store.load("dag-run-1")
        assert "predicate_log" in loaded.meta, (
            "predicate_log key should be in run state meta"
        )
        node_log = loaded.meta["predicate_log"].get("human-go-conditionals-main1")
        assert node_log is not None, "Entry for node-id should be in predicate_log"
        assert "predicate" in node_log
        assert "predicate_hash" in node_log
        assert "metric_actual" in node_log
        assert "result" in node_log
        assert node_log["result"] is True
        assert node_log["metric"] == "accuracy"
        assert node_log["op"] == "gt"
        assert abs(float(node_log["metric_actual"]) - 0.9) < 1e-9

    def test_predicate_hash_is_sha256_of_predicate_str(self, tmp_instance):
        """The predicate_hash in run state is sha256 of the predicate string."""
        import hashlib
        from research_vault.dag.store import RunState, RunStore
        from research_vault.config import load_config

        cfg = load_config()
        store = RunStore.from_config(cfg)
        rs = RunState(run_id="dag-run-2", manifest_path=str(tmp_instance / "m.json"))
        store.create(rs)

        note_p, _ = self._build_exp_note(tmp_instance, "run2", {"accuracy": 0.9})
        cmd = [
            "result", "assert", str(note_p),
            "--metric", "accuracy", "--op", "gt", "--value", "0.8",
            "--run-id", "dag-run-2", "--node-id", "gate-main1",
        ]
        self._run_assert(cmd)

        loaded = store.load("dag-run-2")
        node_log = loaded.meta["predicate_log"]["gate-main1"]
        predicate_str = node_log["predicate"]
        expected_hash = hashlib.sha256(predicate_str.encode("utf-8")).hexdigest()
        assert node_log["predicate_hash"] == expected_hash, (
            f"predicate_hash should be sha256 of predicate string; "
            f"got {node_log['predicate_hash']!r}, expected {expected_hash!r}"
        )

    def test_predicate_log_false_result_also_logged(self, tmp_instance):
        """A false predicate (exit 1) is ALSO logged when --run-id given."""
        from research_vault.dag.store import RunState, RunStore
        from research_vault.config import load_config

        cfg = load_config()
        store = RunStore.from_config(cfg)
        rs = RunState(run_id="dag-run-3", manifest_path=str(tmp_instance / "m.json"))
        store.create(rs)

        note_p, _ = self._build_exp_note(tmp_instance, "run3", {"accuracy": 0.7})
        rc = self._run_assert([
            "result", "assert", str(note_p),
            "--metric", "accuracy", "--op", "gt", "--value", "0.8",
            "--run-id", "dag-run-3", "--node-id", "gate-main1",
        ])
        assert rc == 1

        loaded = store.load("dag-run-3")
        node_log = loaded.meta["predicate_log"]["gate-main1"]
        assert node_log["result"] is False

    def test_no_run_id_no_run_state_change(self, tmp_path):
        """Without --run-id, run state is not touched."""
        from research_vault.dag.store import RunState, RunStore

        store = RunStore(tmp_path / "state")
        rs = RunState(run_id="dag-run-4", manifest_path=str(tmp_path / "m.json"))
        store.create(rs)

        note_p, _ = self._build_exp_note(tmp_path, "run4", {"accuracy": 0.9})
        # No --run-id
        self._run_assert([
            "result", "assert", str(note_p),
            "--metric", "accuracy", "--op", "gt", "--value", "0.8",
        ])

        loaded = store.load("dag-run-4")
        assert "predicate_log" not in loaded.meta, (
            "predicate_log should NOT be added without --run-id"
        )

    def test_default_node_id_is_predicate(self, tmp_instance):
        """Without --node-id, a default key is used (not crash)."""
        from research_vault.dag.store import RunState, RunStore
        from research_vault.config import load_config

        cfg = load_config()
        store = RunStore.from_config(cfg)
        rs = RunState(run_id="dag-run-5", manifest_path=str(tmp_instance / "m.json"))
        store.create(rs)

        note_p, _ = self._build_exp_note(tmp_instance, "run5", {"accuracy": 0.9})
        rc = self._run_assert([
            "result", "assert", str(note_p),
            "--metric", "accuracy", "--op", "gt", "--value", "0.8",
            "--run-id", "dag-run-5",  # no --node-id
        ])
        assert rc == 0
        loaded = store.load("dag-run-5")
        assert "predicate_log" in loaded.meta
        # Some key should exist
        assert len(loaded.meta["predicate_log"]) > 0

    # -----------------------------------------------------------------------
    # 2d. Verb registry
    # -----------------------------------------------------------------------

    def test_result_verb_in_registry(self):
        """'result' is registered in the CLI verb registry (SR-PLAN-2)."""
        from research_vault.cli import _VERB_REGISTRY
        assert "result" in _VERB_REGISTRY, (
            "'result' verb must be registered in _VERB_REGISTRY (SR-PLAN-2)"
        )

    def test_result_sr_field(self):
        """'result' verb registry entry has sr=SR-PLAN-2."""
        from research_vault.cli import _VERB_REGISTRY
        assert _VERB_REGISTRY.get("result", {}).get("sr") == "SR-PLAN-2", (
            "'result' verb sr field must be SR-PLAN-2"
        )
