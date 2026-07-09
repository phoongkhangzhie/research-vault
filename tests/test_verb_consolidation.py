"""test_verb_consolidation.py — D1/D2/D3 acceptance:
docs/superpowers/specs/2026-07-08-rv-verb-consolidation.md

Coverage:
  1. D1 hard-remove — the 8 collapsed step-verbs are gone from the curated
     CLI surface and instead print a redirect breadcrumb + exit 2:
       research: sweep, cited-by, references
       review:   expand, coverage, relations
       manuscript: expand, review
     The underlying library functions remain importable (no reuse broken).
  2. D2 — `rv review <project> run <scope> --question ...` fuses
     `review new` + `dag run` in one call; end-to-end (real cmd_new +
     real cmd_run against a tmp instance) — the Phase-1 run actually
     starts and the initial frontier is printed.
  3. D3 — `rv dag veto <run> <node> --reason ...` casts a veto over an
     OPEN provisional decision note: rolls back (provisional: vetoed) +
     blocks the node. Refuses when the note is not provisional.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# 1. D1 hard-remove — redirect breadcrumb, exit 2, functions still importable
# ---------------------------------------------------------------------------

class TestD1HardRemove:
    @pytest.mark.parametrize("argv", [["sweep", "x.md"], ["cited-by", "ARXIV:1"], ["references", "ARXIV:1"]])
    def test_research_step_verbs_removed(self, argv, capsys):
        from research_vault.research import build_parser, run

        p = build_parser()
        args = p.parse_args(argv)
        rc = run(args)
        assert rc == 2
        err = capsys.readouterr().err
        assert "REMOVED" in err
        assert "D1" in err

    @pytest.mark.parametrize("argv", [["demo", "expand", "s"], ["demo", "coverage", "s"], ["demo", "relations", "s"]])
    def test_review_step_verbs_removed(self, argv, capsys):
        from research_vault.review.verbs import build_parser, run

        p = build_parser()
        args = p.parse_args(argv)
        rc = run(args)
        assert rc == 2
        err = capsys.readouterr().err
        assert "REMOVED" in err

    @pytest.mark.parametrize("argv", [["demo", "expand", "slug"], ["demo", "review", "slug"]])
    def test_manuscript_step_verbs_removed(self, argv, capsys):
        from research_vault.manuscript.verbs import build_parser, run

        p = build_parser()
        args = p.parse_args(argv)
        rc = run(args)
        assert rc == 2
        err = capsys.readouterr().err
        assert "REMOVED" in err

    def test_underlying_functions_remain_importable(self):
        """D1 does not delete anything — only the CLI surface shrinks."""
        from research_vault.research import cmd_sweep, cmd_cited_by, cmd_references  # noqa: F401
        from research_vault.review import cmd_expand, coverage_report, relations_report  # noqa: F401
        from research_vault.manuscript import cmd_expand as ms_cmd_expand  # noqa: F401
        from research_vault.manuscript.review_board import run_review_board  # noqa: F401

    @staticmethod
    def _subparser_choices(parser: argparse.ArgumentParser) -> dict:
        for action in parser._actions:  # noqa: SLF001
            if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
                return action.choices
        raise AssertionError("no subparsers action found")

    def test_kept_primitives_still_present(self):
        """find/add/corroborate (research), new/list/tips/gap-* (review),
        new/list/judge-* (manuscript) are KEEP-bucket — untouched."""
        from research_vault.research import build_parser as rbp
        choices = self._subparser_choices(rbp())
        for kept in ("find", "add", "corroborate"):
            assert kept in choices

    def test_review_kept_verbs_present(self):
        from research_vault.review.verbs import build_parser as rvbp
        choices = self._subparser_choices(rvbp())
        for kept in ("new", "run", "list", "tips", "gap-scan"):
            assert kept in choices
        for removed in ("expand", "coverage", "relations"):
            assert removed in choices  # still parses (stub), just redirects


# ---------------------------------------------------------------------------
# 2. D2 — `rv review run` fuses new + dag run
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_instance(tmp_path: Path, monkeypatch):
    cfg_file = tmp_path / "research_vault.toml"
    (tmp_path / "state").mkdir()
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "demo").mkdir()
    cfg_file.write_text(
        f"""
instance_root = "{tmp_path}"
notes_root = "{tmp_path / 'notes'}"
state_dir = "{tmp_path / 'state'}"
agents_dir = "{tmp_path / '.agents'}"
tasks_dir = "{tmp_path / 'tasks'}"
control_dir = "{tmp_path / 'control'}"

[adapters]
notifier = "file"
backend = "local"
secrets = "env"

[projects.demo]
source_dir = "{tmp_path / 'notes' / 'demo'}"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_file))
    from research_vault.config import reset_config_cache
    reset_config_cache()
    yield tmp_path
    reset_config_cache()


class TestD2ReviewRun:
    def test_review_run_fuses_new_and_dag_run(self, tmp_instance: Path, capsys):
        from research_vault.review.verbs import build_parser, run

        p = build_parser()
        args = p.parse_args(["demo", "run", "scope-fused", "--question", "does X help Y?"])
        rc = run(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "Phase-1 manifest" in out
        assert "Initial frontier" in out or "review-scope" in out

        # the review OKF note + artifact dir were really scaffolded
        review_dir = tmp_instance / "notes" / "demo" / "reviews" / "scope-fused"
        assert (review_dir / "phase1-dag.json").exists()

        # and the DAG run was really started (state persisted)
        from research_vault.dag.store import RunStore
        from research_vault.config import load_config
        manifest = json.loads((review_dir / "phase1-dag.json").read_text())
        rs = RunStore.from_config(load_config()).load(manifest["run_id"])
        assert rs.node_status("review-scope") in ("pending", "dispatched")


# ---------------------------------------------------------------------------
# 3. D3 — `rv dag veto`
# ---------------------------------------------------------------------------

class TestD3DagVeto:
    def _manifest(self, run_id: str, note_path: Path) -> dict:
        return {
            "run_id": run_id, "name": "veto-test", "global_cap": 4,
            "nodes": [
                {
                    "id": "framework-propose", "type": "agent", "spec": "task://demo#fw",
                    "produces": {"note": str(note_path)}, "needs": [],
                },
            ],
        }

    def _setup(self, tmp_path: Path, run_id: str, provisional: str = "true"):
        from research_vault.dag.store import RunState, RunStore

        note = tmp_path / "_manuscript.md"
        note.write_text(f"---\nprovisional: {provisional}\n---\n\nbody\n", encoding="utf-8")
        manifest = self._manifest(run_id, note)
        manifest_path = tmp_path / f"{run_id}.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        store = RunStore(tmp_path / "state")
        rs = RunState(run_id=run_id, manifest_path=str(manifest_path))
        rs.init_nodes(manifest)
        rs.set_node_status("framework-propose", "succeeded")
        store.create(rs)
        return store, note

    def test_veto_rolls_back_provisional_decision(self, tmp_path: Path, monkeypatch, capsys):
        cfg_file = tmp_path / "research_vault.toml"
        cfg_file.write_text(
            f'instance_root = "{tmp_path}"\nnotes_root = "{tmp_path / "notes"}"\n'
            f'state_dir = "{tmp_path / "state"}"\nagents_dir = "{tmp_path / ".agents"}"\n'
            f'tasks_dir = "{tmp_path / "tasks"}"\ncontrol_dir = "{tmp_path / "control"}"\n'
            '[adapters]\nnotifier = "file"\nbackend = "local"\nsecrets = "env"\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_file))
        from research_vault.config import reset_config_cache
        reset_config_cache()

        (tmp_path / "notes").mkdir(); (tmp_path / "state").mkdir()
        store, note = self._setup(tmp_path, "veto-run-1")

        from research_vault.dag.verbs import cmd_veto
        args = argparse.Namespace(
            run_id="veto-run-1", node_id="framework-propose",
            reason="spine choice not justified", note=None,
        )
        rc = cmd_veto(args)
        assert rc == 0
        assert "provisional: vetoed" in note.read_text()

        rs = store.load("veto-run-1")
        assert rs.node_status("framework-propose") == "blocked"
        assert "VETOED" in rs.node_states["framework-propose"]["decision_note"]

        reset_config_cache()

    def test_veto_refuses_when_not_provisional(self, tmp_path: Path, monkeypatch, capsys):
        cfg_file = tmp_path / "research_vault.toml"
        cfg_file.write_text(
            f'instance_root = "{tmp_path}"\nnotes_root = "{tmp_path / "notes"}"\n'
            f'state_dir = "{tmp_path / "state"}"\nagents_dir = "{tmp_path / ".agents"}"\n'
            f'tasks_dir = "{tmp_path / "tasks"}"\ncontrol_dir = "{tmp_path / "control"}"\n'
            '[adapters]\nnotifier = "file"\nbackend = "local"\nsecrets = "env"\n',
            encoding="utf-8",
        )
        monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_file))
        from research_vault.config import reset_config_cache
        reset_config_cache()

        (tmp_path / "notes").mkdir(); (tmp_path / "state").mkdir()
        store, note = self._setup(tmp_path, "veto-run-2", provisional="false")

        from research_vault.dag.verbs import cmd_veto
        args = argparse.Namespace(
            run_id="veto-run-2", node_id="framework-propose",
            reason="too late", note=None,
        )
        rc = cmd_veto(args)
        assert rc == 1
        rs = store.load("veto-run-2")
        assert rs.node_status("framework-propose") == "succeeded"  # unchanged

        reset_config_cache()
