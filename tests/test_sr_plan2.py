"""test_sr_plan2.py — SR-PLAN-2 acceptance tests.

Coverage:
  1. K-2 shape-lint wired into rv plan freeze (non-optional gate)
     1a. plan freeze with violations → BLOCKS (exit 1), hash NOT stored
     1b. plan freeze with clean plan → passes, hash stored (existing behavior)
     1c. plan freeze with missing plan_kind → BLOCKS (PlanCheckError)
  2. covers: bare-id convention lint in check.py
     2a. bare IDs pass (q1-main1, q1-main2-abl-A, etc.)
     2b. experiments/-prefixed IDs fail (experiments/q1-main1)
     2c. mixed (some prefixed, some bare) → violation on each prefixed entry
     2d. empty covers: passes (nothing to lint)
  3. rv plan freeze CLI integration (end-to-end gate)
     3a. rv plan freeze with a failing plan → exit 1, violation printed
     3b. rv plan freeze with a passing plan → exit 0, hash stored (regression)
  4. check_plan returns violations list that includes covers: id violations
  5. _VERB_REGISTRY sr field remains SR-PLAN-1 (plan verb unchanged) — guard
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.plan.check import check_plan, PlanCheckError


# ---------------------------------------------------------------------------
# Helpers (mirrors test_sr_plan1.py helpers)
# ---------------------------------------------------------------------------

def _plan_note(
    tmp_path: Path,
    *,
    plan_kind: str = "preregistration",
    covers: str = "[q1-main1, q1-main1-abl-A]",
    body: str = "",
    filename: str = "q1-plan.md",
) -> Path:
    """Write a minimal plan master note and return its path."""
    p = tmp_path / filename
    fm = f"plan_kind: {plan_kind}\ncitekey: q1-plan\ncovers: {covers}"
    p.write_text(f"---\n{fm}\n---\n\n{body}", encoding="utf-8")
    return p


def _child_note(
    notes_dir: Path,
    child_id: str,
    *,
    stance: str = "confirmatory",
    plan_role: str = "main",
) -> Path:
    """Write a child experiment note with stance + plan_role fields."""
    p = notes_dir / f"{child_id}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\ntype: experiments\ncitekey: {child_id}\n"
        f"stance: {stance}\nplan_role: {plan_role}\n---\n\n# {child_id}\n",
        encoding="utf-8",
    )
    return p


def _cfg_file(tmp_path: Path) -> Path:
    """Write a minimal research_vault.toml and return its path."""
    cfg = tmp_path / "research_vault.toml"
    cfg.write_text(
        f'instance_root = "{tmp_path}"\n'
        f'notes_root = "{tmp_path / "notes"}"\n'
        f'state_dir = "{tmp_path / "state"}"\n'
        f'agents_dir = "{tmp_path / ".agents"}"\n'
        f'tasks_dir = "{tmp_path / "tasks"}"\n'
        f'control_dir = "{tmp_path / "control"}"\n'
        '[adapters]\nnotifier = "file"\nbackend = "local"\nsecrets = "env"\n',
        encoding="utf-8",
    )
    return cfg


# ===========================================================================
# 1. K-2 wired into rv plan freeze (non-optional gate)
# ===========================================================================

class TestFreezeGatesOnK2:
    """rv plan freeze must BLOCK if check_plan reports violations."""

    def _setup_run(self, tmp_path: Path) -> str:
        """Create a minimal RunState and return its run_id."""
        from research_vault.dag.store import RunState, RunStore

        store = RunStore(tmp_path / "state")
        rs = RunState(run_id="gate-test", manifest_path=str(tmp_path / "m.json"))
        store.create(rs)
        return "gate-test"

    def test_freeze_blocks_on_shape_violations(self, tmp_path, capsys):
        """rv plan freeze with a diagnosis-table violation → BLOCKS, hash NOT stored."""
        from research_vault.plan.verbs import run as plan_run, build_parser
        from research_vault.dag.store import RunStore

        cfg = _cfg_file(tmp_path)
        old = os.environ.get("RESEARCH_VAULT_CONFIG")
        os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg)

        try:
            notes_dir = tmp_path / "notes" / "experiments"
            _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")

            # Plan with a TBD cell in the diagnosis table — K-2 violation
            bad_body = textwrap.dedent("""\
                ## Diagnosis Table

                | Outcome | Conclusion | Action |
                |---|---|---|
                | score > 0.8 | load-bearing | TBD |
            """)
            plan_note = _plan_note(tmp_path, covers="[q1-main1]", body=bad_body)

            run_id = self._setup_run(tmp_path)

            parent = argparse.ArgumentParser()
            subs = parent.add_subparsers()
            build_parser(subs)
            args = parent.parse_args([
                "plan", "freeze", run_id, str(plan_note),
                "--notes-root", str(notes_dir),
            ])
            result = plan_run(args)

            # Must BLOCK
            assert result == 1, "freeze must return 1 when K-2 lint has violations"

            # Violation message must be printed
            out, err = capsys.readouterr()
            combined = out + err
            assert "TBD" in combined or "violation" in combined.lower(), (
                f"Expected violation message in output; got:\nout={out!r}\nerr={err!r}"
            )

            # Hash must NOT be stored
            store = RunStore(tmp_path / "state")
            state = store.load(run_id)
            assert "plan_freeze" not in state.meta, (
                "freeze hash must NOT be stored when K-2 lint has violations"
            )
        finally:
            if old is None:
                os.environ.pop("RESEARCH_VAULT_CONFIG", None)
            else:
                os.environ["RESEARCH_VAULT_CONFIG"] = old

    def test_freeze_blocks_on_multi_component_violation(self, tmp_path, capsys):
        """rv plan freeze with multi-component ablation → BLOCKS, hash NOT stored."""
        from research_vault.plan.verbs import run as plan_run, build_parser
        from research_vault.dag.store import RunStore

        cfg = _cfg_file(tmp_path)
        old = os.environ.get("RESEARCH_VAULT_CONFIG")
        os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg)

        try:
            notes_dir = tmp_path / "notes" / "experiments"
            _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")

            bad_body = (
                "## Ablation A\n\n"
                "Component manipulated: prompt template and sampling temperature\n"
            )
            plan_note = _plan_note(tmp_path, covers="[q1-main1]", body=bad_body)

            run_id = self._setup_run(tmp_path)

            parent = argparse.ArgumentParser()
            subs = parent.add_subparsers()
            build_parser(subs)
            args = parent.parse_args([
                "plan", "freeze", run_id, str(plan_note),
                "--notes-root", str(notes_dir),
            ])
            result = plan_run(args)

            assert result == 1

            store = RunStore(tmp_path / "state")
            state = store.load(run_id)
            assert "plan_freeze" not in state.meta

        finally:
            if old is None:
                os.environ.pop("RESEARCH_VAULT_CONFIG", None)
            else:
                os.environ["RESEARCH_VAULT_CONFIG"] = old

    def test_freeze_passes_clean_plan(self, tmp_path, capsys):
        """rv plan freeze with a clean plan → exit 0, hash stored (regression guard)."""
        from research_vault.plan.verbs import run as plan_run, build_parser
        from research_vault.dag.store import RunStore

        cfg = _cfg_file(tmp_path)
        old = os.environ.get("RESEARCH_VAULT_CONFIG")
        os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg)

        try:
            notes_dir = tmp_path / "notes" / "experiments"
            _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")

            clean_body = textwrap.dedent("""\
                ## Diagnosis Table

                | Outcome | Conclusion | Action |
                |---|---|---|
                | score > 0.8 | load-bearing | proceed to write-up |
                | score < 0.5 | mechanism absent | reject claim |
            """)
            plan_note = _plan_note(tmp_path, covers="[q1-main1]", body=clean_body)

            run_id = self._setup_run(tmp_path)

            parent = argparse.ArgumentParser()
            subs = parent.add_subparsers()
            build_parser(subs)
            args = parent.parse_args([
                "plan", "freeze", run_id, str(plan_note),
                "--notes-root", str(notes_dir),
            ])
            result = plan_run(args)

            assert result == 0, "clean plan must pass freeze"

            store = RunStore(tmp_path / "state")
            state = store.load(run_id)
            assert "plan_freeze" in state.meta, "freeze hash must be stored for clean plan"
            assert "covers_hash" in state.meta["plan_freeze"]

        finally:
            if old is None:
                os.environ.pop("RESEARCH_VAULT_CONFIG", None)
            else:
                os.environ["RESEARCH_VAULT_CONFIG"] = old

    def test_freeze_blocks_on_bad_plan_kind(self, tmp_path, capsys):
        """rv plan freeze with wrong plan_kind → BLOCKS (PlanCheckError path)."""
        from research_vault.plan.verbs import run as plan_run, build_parser
        from research_vault.dag.store import RunStore

        cfg = _cfg_file(tmp_path)
        old = os.environ.get("RESEARCH_VAULT_CONFIG")
        os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg)

        try:
            notes_dir = tmp_path / "notes" / "experiments"
            _child_note(notes_dir, "q1-main1", stance="confirmatory", plan_role="main")
            plan_note = _plan_note(tmp_path, plan_kind="experiment", covers="[q1-main1]")

            run_id = self._setup_run(tmp_path)

            parent = argparse.ArgumentParser()
            subs = parent.add_subparsers()
            build_parser(subs)
            args = parent.parse_args([
                "plan", "freeze", run_id, str(plan_note),
                "--notes-root", str(notes_dir),
            ])
            result = plan_run(args)

            assert result == 1

            store = RunStore(tmp_path / "state")
            state = store.load(run_id)
            assert "plan_freeze" not in state.meta

        finally:
            if old is None:
                os.environ.pop("RESEARCH_VAULT_CONFIG", None)
            else:
                os.environ["RESEARCH_VAULT_CONFIG"] = old


# ===========================================================================
# 2. covers: bare-id convention lint in check.py
# ===========================================================================

class TestCoversIdConvention:
    """Rule (c): covers: entries must be bare IDs, not path-prefixed."""

    def test_bare_ids_pass(self, tmp_path):
        """Bare IDs like 'q1-main1', 'q1-main1-abl-A' pass the lint."""
        covers = "[q1-main1, q1-main1-abl-A, q1-main2, q1-main2-abl-B]"
        p = _plan_note(tmp_path, covers=covers)
        violations = check_plan(p)
        assert not any("covers" in v.lower() and "path" in v.lower() for v in violations), (
            f"Bare IDs should pass covers: lint; got violations: {violations}"
        )

    def test_experiments_prefixed_id_fails(self, tmp_path):
        """'experiments/q1-main1' in covers: is a violation — use bare IDs."""
        covers = "[experiments/q1-main1, q1-main1-abl-A]"
        p = _plan_note(tmp_path, covers=covers)
        violations = check_plan(p)
        assert any("covers" in v.lower() for v in violations), (
            f"Expected covers: violation for path-prefixed ID; got: {violations}"
        )
        assert any("experiments/q1-main1" in v for v in violations), (
            f"Violation should name the offending entry; got: {violations}"
        )

    def test_all_prefixed_ids_flagged(self, tmp_path):
        """Each path-prefixed entry gets its own violation."""
        covers = "[experiments/q1-main1, experiments/q1-main2]"
        p = _plan_note(tmp_path, covers=covers)
        violations = check_plan(p)
        prefixed_violations = [v for v in violations if "covers" in v.lower()]
        assert len(prefixed_violations) >= 2, (
            f"Expected at least 2 covers: violations (one per bad entry); got: {violations}"
        )

    def test_mixed_bare_and_prefixed(self, tmp_path):
        """Only the prefixed entry is flagged; the bare one is not."""
        covers = "[q1-main1, experiments/q1-main2]"
        p = _plan_note(tmp_path, covers=covers)
        violations = check_plan(p)
        assert any("experiments/q1-main2" in v for v in violations), (
            "Prefixed entry should be flagged"
        )
        # The bare-id entry should not appear in violations
        assert not any("q1-main1" in v and "path" in v.lower() for v in violations), (
            "Bare-id entry q1-main1 should not be flagged"
        )

    def test_empty_covers_passes(self, tmp_path):
        """Empty covers: field produces no covers: violations."""
        p = _plan_note(tmp_path, covers="[]")
        violations = check_plan(p)
        assert not any("covers" in v.lower() for v in violations)

    def test_missing_covers_passes(self, tmp_path):
        """A plan note without covers: at all produces no covers: violations."""
        p = tmp_path / "no-covers.md"
        p.write_text(
            "---\nplan_kind: preregistration\ncitekey: no-covers\n---\n\n# plan\n",
            encoding="utf-8",
        )
        violations = check_plan(p)
        assert not any("covers" in v.lower() for v in violations)


# ===========================================================================
# 3. End-to-end CLI integration (gate fires on real inputs)
# ===========================================================================

class TestFreezeGateCLIIntegration:
    """Integration tests using the full rv plan freeze CLI path."""

    def test_freeze_cli_with_tbd_table_blocks(self, tmp_path, capsys):
        """End-to-end: rv plan freeze with TBD in table → exit 1."""
        from research_vault.plan.verbs import run as plan_run, build_parser
        from research_vault.dag.store import RunState, RunStore

        cfg = _cfg_file(tmp_path)
        old = os.environ.get("RESEARCH_VAULT_CONFIG")
        os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg)

        try:
            notes_dir = tmp_path / "notes" / "experiments"
            _child_note(notes_dir, "main1", stance="confirmatory", plan_role="main")

            bad_body = textwrap.dedent("""\
                ## Diagnosis

                | Outcome | Conclusion | Action |
                |---|---|---|
                | high | confirmed | TBD |
                | low | refuted | reject |
            """)
            plan_note = _plan_note(tmp_path, covers="[main1]", body=bad_body)

            store = RunStore(tmp_path / "state")
            rs = RunState(run_id="cli-gate-run", manifest_path="/p")
            store.create(rs)

            parent = argparse.ArgumentParser()
            subs = parent.add_subparsers()
            build_parser(subs)
            args = parent.parse_args([
                "plan", "freeze", "cli-gate-run", str(plan_note),
                "--notes-root", str(notes_dir),
            ])
            result = plan_run(args)
            assert result == 1

            loaded = store.load("cli-gate-run")
            assert "plan_freeze" not in loaded.meta

        finally:
            if old is None:
                os.environ.pop("RESEARCH_VAULT_CONFIG", None)
            else:
                os.environ["RESEARCH_VAULT_CONFIG"] = old

    def test_freeze_cli_with_missing_branch_blocks(self, tmp_path, capsys):
        """End-to-end: rv plan freeze with empty cell → exit 1."""
        from research_vault.plan.verbs import run as plan_run, build_parser
        from research_vault.dag.store import RunState, RunStore

        cfg = _cfg_file(tmp_path)
        old = os.environ.get("RESEARCH_VAULT_CONFIG")
        os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg)

        try:
            notes_dir = tmp_path / "notes" / "experiments"
            _child_note(notes_dir, "main1", stance="confirmatory", plan_role="main")

            bad_body = textwrap.dedent("""\
                ## Diagnosis

                | Outcome | Conclusion | Action |
                |---|---|---|
                | high |  | proceed |
            """)
            plan_note = _plan_note(tmp_path, covers="[main1]", body=bad_body)

            store = RunStore(tmp_path / "state")
            rs = RunState(run_id="cli-empty-run", manifest_path="/p")
            store.create(rs)

            parent = argparse.ArgumentParser()
            subs = parent.add_subparsers()
            build_parser(subs)
            args = parent.parse_args([
                "plan", "freeze", "cli-empty-run", str(plan_note),
                "--notes-root", str(notes_dir),
            ])
            result = plan_run(args)
            assert result == 1

            loaded = store.load("cli-empty-run")
            assert "plan_freeze" not in loaded.meta

        finally:
            if old is None:
                os.environ.pop("RESEARCH_VAULT_CONFIG", None)
            else:
                os.environ["RESEARCH_VAULT_CONFIG"] = old


# ===========================================================================
# 4. check_plan returns covers: id violations in the violations list
# ===========================================================================

class TestCheckPlanCoversViolations:
    """check_plan() propagates covers: id violations to its return value."""

    def test_check_plan_returns_covers_violation_in_list(self, tmp_path):
        covers = "[experiments/q1-main1]"
        p = _plan_note(tmp_path, covers=covers)
        violations = check_plan(p)
        assert len(violations) > 0, "check_plan must return violations for prefixed covers: IDs"
        assert any("experiments/q1-main1" in v for v in violations)

    def test_check_plan_covers_violation_and_table_violation_both_returned(self, tmp_path):
        """Both covers: and table violations appear together."""
        covers = "[experiments/q1-main1]"
        body = textwrap.dedent("""\
            ## Diagnosis

            | Outcome | Conclusion | Action |
            |---|---|---|
            | high | good | TBD |
        """)
        p = _plan_note(tmp_path, covers=covers, body=body)
        violations = check_plan(p)
        has_covers = any("covers" in v.lower() for v in violations)
        has_tbd = any("TBD" in v for v in violations)
        assert has_covers and has_tbd, (
            f"Expected both covers: and TBD violations; got: {violations}"
        )


# ===========================================================================
# 5. Guard: verb registry unchanged — no new CLI verb added
# ===========================================================================

class TestVerbRegistryGuard:
    """The plan verb registry entry is unchanged — no new CLI verb added here."""

    def test_plan_verb_unchanged(self):
        from research_vault.cli import _VERB_REGISTRY
        assert "plan" in _VERB_REGISTRY
        # This promotes wiring, not a new verb — the registry entry is unchanged.
        assert _VERB_REGISTRY["plan"]["module"] == "research_vault.plan.verbs"
