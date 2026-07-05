"""test_dag_adopter_fixes.py — RED-before-GREEN regression tests for two adopter bugs.

F21 (★★★ blocker): rv dag complete resolves produces.note against cfg.notes_root even
  when the manifest declares a "project" with a separate source_dir.  In the real
  multi-repo adopter model (source_dir != notes_root), the note lives under source_dir
  and the OKF check returns "note does not exist" → complete BLOCKS on success.

F13 (★★): rv dag approve is missing --note / --output / --reject flags advertised in
  the docstring.  A human-go gate cannot record a decision rationale, cannot reject,
  and cannot emit outputs (the experiment loop's human-go-conditionals gates need
  --output to communicate the branch decision to downstream nodes).

All tests run entirely in tmp_path.  No ~/vault, no real cluster, no network.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import Config, reset_config_cache
from research_vault.dag.store import RunStore, RunState
from research_vault.dag.verbs import (
    build_parser,
    cmd_complete,
    cmd_approve,
    cmd_run,
    cmd_status,
    cmd_tick,
)
from research_vault.dag.walker import compute_frontier


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_cfg():
    reset_config_cache()
    yield
    reset_config_cache()


def _argns(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


def _manifest(nodes: list[dict], run_id: str = "test-run", project: str | None = None) -> dict:
    m: dict = {"run_id": run_id, "name": "Test DAG", "global_cap": 4, "nodes": nodes}
    if project is not None:
        m["project"] = project
    return m


def _agent_node(nid: str, produces: dict | None = None) -> dict:
    n: dict = {"id": nid, "type": "agent", "spec": "fixture://test", "label": nid}
    if produces:
        n["produces"] = produces
    return n


def _human_go_node(nid: str, needs: list | None = None) -> dict:
    n: dict = {"id": nid, "type": "human-go", "label": nid}
    if needs:
        n["needs"] = needs
    return n


def _note_need(from_id: str) -> dict:
    return {"from": from_id, "edge": "afterok"}


# ---------------------------------------------------------------------------
# Instance fixture helpers
# ---------------------------------------------------------------------------

def _make_instance_with_project(
    tmp_path: Path,
    project_slug: str,
    source_dir: Path,
) -> Path:
    """
    Create a research_vault.toml where notes_root != source_dir.

    The shared notes_root is tmp_path/notes (a different directory).
    The project's source_dir is the supplied source_dir.
    Returns tmp_path (the instance root).
    """
    notes_root = tmp_path / "notes"
    notes_root.mkdir(exist_ok=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)
    cfg_file = tmp_path / "research_vault.toml"
    cfg_file.write_text(
        f"""
instance_root = "{tmp_path}"
notes_root = "{notes_root}"
state_dir = "{state_dir}"
agents_dir = "{tmp_path / '.agents'}"
tasks_dir = "{tmp_path / 'tasks'}"
control_dir = "{tmp_path / 'control'}"

[adapters]
notifier = "file"
backend = "local"
secrets = "env"

[projects.{project_slug}]
source_dir = "{source_dir}"
""",
        encoding="utf-8",
    )
    old = os.environ.get("RESEARCH_VAULT_CONFIG")
    os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_file)
    return tmp_path


# ===========================================================================
# F21: produces.note resolution against project source_dir
# ===========================================================================

class TestF21ProducesNoteResolution:
    """
    RED → GREEN proof for F21.

    Setup: a project "my-proj" whose source_dir is SEPARATE from notes_root.
    A manifest for that project declares "project": "my-proj" and one node
    produces a note at source_dir/experiments/exp-001.md.

    OLD (broken) code: resolves against cfg.notes_root → file not found → BLOCK.
    NEW  (fixed) code: resolves against cfg.project_notes_dir("my-proj") = source_dir
                       → file found → PASS.

    The demo case (notes_root == source_dir, no "project" key) must stay green.
    """

    def test_red_adopter_source_dir_separate_from_notes_root(self, tmp_path: Path, capsys):
        """
        ★ RED-before-GREEN anchor for F21.

        source_dir != notes_root, manifest declares "project": slug.
        The note lives in source_dir/experiments/.  OLD code would BLOCK because
        cfg.notes_root/experiments/exp-001.md does NOT exist.
        Fixed code passes because project_notes_dir("my-proj") == source_dir.

        This test WOULD have failed against the old code (before the F21 fix).
        We document that by asserting rc == 0.  If this test goes RED, F21 is
        re-introduced.
        """
        project_slug = "my-proj"
        source_dir = tmp_path / "my-proj-notes"
        source_dir.mkdir()
        _make_instance_with_project(tmp_path, project_slug, source_dir)

        # Write the note in source_dir/experiments/ (NOT notes_root/experiments/)
        exp_dir = source_dir / "experiments"
        exp_dir.mkdir()
        note = exp_dir / "exp-001.md"
        note.write_text(
            "---\ntype: experiments\ntitle: Experiment 001\n---\nBody.\n",
            encoding="utf-8",
        )

        # Build manifest with "project" field pointing to the right slug
        m = _manifest(
            [_agent_node("run-exp", produces={"note": "experiments/exp-001.md"})],
            run_id="f21-adopter-run",
            project=project_slug,
        )
        mf = tmp_path / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")

        # Boot the run
        rc_run = cmd_run(_argns(manifest=str(mf)))
        assert rc_run == 0, "rv dag run should succeed"
        capsys.readouterr()

        # Complete the producing node with status=succeeded → must PASS the OKF check
        rc = cmd_complete(
            _argns(run_id="f21-adopter-run", node_id="run-exp", status="succeeded")
        )
        err = capsys.readouterr().err
        assert rc == 0, (
            "rv dag complete should PASS the OKF check when note is in project source_dir.\n"
            f"  stderr: {err!r}\n"
            f"  source_dir: {source_dir}\n"
            f"  notes_root: {tmp_path / 'notes'}\n"
            "  (F21 re-introduced: code is resolving against notes_root instead of source_dir)"
        )
        assert "OKF vault check FAILED" not in err
        assert "note does not exist" not in err

    def test_note_only_in_notes_root_without_project_field_passes(self, tmp_path: Path, capsys):
        """
        Demo case: no "project" field in manifest, note is in notes_root/experiments/.
        Must stay green after the F21 fix (no regression on the existing path).
        """
        # Instance with no project registry — notes_root IS the only root
        notes_root = tmp_path / "notes"
        notes_root.mkdir()
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        cfg_file = tmp_path / "research_vault.toml"
        cfg_file.write_text(
            f"""
instance_root = "{tmp_path}"
notes_root = "{notes_root}"
state_dir = "{state_dir}"
agents_dir = "{tmp_path / '.agents'}"
tasks_dir = "{tmp_path / 'tasks'}"
control_dir = "{tmp_path / 'control'}"

[adapters]
notifier = "file"
backend = "local"
secrets = "env"
""",
            encoding="utf-8",
        )
        old = os.environ.get("RESEARCH_VAULT_CONFIG")
        os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_file)
        try:
            exp_dir = notes_root / "experiments"
            exp_dir.mkdir()
            note = exp_dir / "exp-demo.md"
            note.write_text(
                "---\ntype: experiments\ntitle: Demo\n---\n",
                encoding="utf-8",
            )

            # Manifest with NO "project" field
            m = _manifest(
                [_agent_node("demo-node", produces={"note": str(note)})],
                run_id="f21-demo-run",
                project=None,
            )
            mf = tmp_path / "manifest.json"
            mf.write_text(json.dumps(m), encoding="utf-8")

            cmd_run(_argns(manifest=str(mf)))
            capsys.readouterr()

            rc = cmd_complete(
                _argns(run_id="f21-demo-run", node_id="demo-node", status="succeeded")
            )
            err = capsys.readouterr().err
            assert rc == 0, f"Demo case (no project field) must still pass. stderr: {err!r}"
        finally:
            if old is None:
                os.environ.pop("RESEARCH_VAULT_CONFIG", None)
            else:
                os.environ["RESEARCH_VAULT_CONFIG"] = old

    def test_project_field_unknown_slug_fails_fast(self, tmp_path: Path, capsys):
        """
        If manifest has a "project" field but the slug is not in the config registry,
        cmd_complete must FAIL FAST (rc=1) with the precise "Unknown project" message
        rather than silently falling back to notes_root (which would hide the real cause
        when the note lives in a separate source_dir, not notes_root).
        """
        notes_root = tmp_path / "notes"
        notes_root.mkdir()
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        cfg_file = tmp_path / "research_vault.toml"
        cfg_file.write_text(
            f"""
instance_root = "{tmp_path}"
notes_root = "{notes_root}"
state_dir = "{state_dir}"
agents_dir = "{tmp_path / '.agents'}"
tasks_dir = "{tmp_path / 'tasks'}"
control_dir = "{tmp_path / 'control'}"

[adapters]
notifier = "file"
backend = "local"
secrets = "env"
""",
            encoding="utf-8",
        )
        old = os.environ.get("RESEARCH_VAULT_CONFIG")
        os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_file)
        try:
            exp_dir = notes_root / "experiments"
            exp_dir.mkdir()
            note = exp_dir / "exp-fallback.md"
            note.write_text(
                "---\ntype: experiments\ntitle: Fallback\n---\n",
                encoding="utf-8",
            )
            m = _manifest(
                [_agent_node("fb-node", produces={"note": str(note)})],
                run_id="f21-failfast-run",
                project="unknown-project",  # not in registry
            )
            mf = tmp_path / "manifest.json"
            mf.write_text(json.dumps(m), encoding="utf-8")

            cmd_run(_argns(manifest=str(mf)))
            capsys.readouterr()

            rc = cmd_complete(
                _argns(run_id="f21-failfast-run", node_id="fb-node", status="succeeded")
            )
            err = capsys.readouterr().err
            # Unknown slug → must fail fast, not silently fall back to notes_root
            assert rc == 1, (
                "Unknown project slug must produce rc=1; "
                f"silent fallback hides the real cause. rc={rc!r}, err={err!r}"
            )
            assert "Unknown project" in err or "unknown-project" in err, (
                f"Error output must surface the unknown slug. err={err!r}"
            )
        finally:
            if old is None:
                os.environ.pop("RESEARCH_VAULT_CONFIG", None)
            else:
                os.environ["RESEARCH_VAULT_CONFIG"] = old

    def test_source_dir_separate_wrong_type_still_blocks(self, tmp_path: Path, capsys):
        """
        Sanity gate: with a separate source_dir and a WRONG-typed note, complete still
        BLOCKs.  The fix must not make the vault check vacuous.
        """
        project_slug = "my-proj2"
        source_dir = tmp_path / "my-proj2-notes"
        source_dir.mkdir()
        _make_instance_with_project(tmp_path, project_slug, source_dir)

        exp_dir = source_dir / "experiments"
        exp_dir.mkdir()
        note = exp_dir / "exp-bad.md"
        # WRONG type (literature in experiments/)
        note.write_text(
            "---\ntype: literature\ntitle: Bad\n---\n",
            encoding="utf-8",
        )

        m = _manifest(
            [_agent_node("bad-node", produces={"note": "experiments/exp-bad.md"})],
            run_id="f21-bad-run",
            project=project_slug,
        )
        mf = tmp_path / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")

        cmd_run(_argns(manifest=str(mf)))
        capsys.readouterr()

        rc = cmd_complete(
            _argns(run_id="f21-bad-run", node_id="bad-node", status="succeeded")
        )
        err = capsys.readouterr().err
        assert rc == 1, "Wrong-typed note must still BLOCK even with source_dir fix"
        assert "OKF vault check FAILED" in err or "type mismatch" in err


# ===========================================================================
# F13: rv dag approve --note / --output / --reject flags
# ===========================================================================

def _make_simple_instance(tmp_path: Path) -> Path:
    """Create a minimal instance and set RESEARCH_VAULT_CONFIG."""
    notes_root = tmp_path / "notes"
    notes_root.mkdir(exist_ok=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)
    cfg_file = tmp_path / "research_vault.toml"
    cfg_file.write_text(
        f"""
instance_root = "{tmp_path}"
notes_root = "{notes_root}"
state_dir = "{state_dir}"
agents_dir = "{tmp_path / '.agents'}"
tasks_dir = "{tmp_path / 'tasks'}"
control_dir = "{tmp_path / 'control'}"

[adapters]
notifier = "file"
backend = "local"
secrets = "env"

# SR-APPROVE-GATE: token fingerprint for test-time token approval.
[approval]
enforce = true
token_fingerprint = "d309a810bb5f40cef518202e46d197aa61e4dddafc5984c8c698da29ac8fd2bc"
enforce_sig = ""
""",
        encoding="utf-8",
    )
    os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_file)
    return tmp_path


@pytest.fixture
def simple_instance(tmp_path: Path):
    old = os.environ.get("RESEARCH_VAULT_CONFIG")
    _make_simple_instance(tmp_path)
    yield tmp_path
    if old is None:
        os.environ.pop("RESEARCH_VAULT_CONFIG", None)
    else:
        os.environ["RESEARCH_VAULT_CONFIG"] = old


def _boot_run_to_awaiting_go(instance: Path, run_id: str) -> Path:
    """
    Build a minimal manifest: work-node → gate (human-go).
    Complete work-node, tick, so gate reaches awaiting-go.
    Returns the manifest path.
    """
    m = {
        "run_id": run_id,
        "name": "F13 test run",
        "global_cap": 4,
        "nodes": [
            {
                "id": "work",
                "type": "agent",
                "spec": "fixture://test",
                "label": "Work node",
            },
            {
                "id": "gate",
                "type": "human-go",
                "label": "Decision gate",
                "needs": [{"from": "work", "edge": "afterok"}],
            },
        ],
    }
    mf = instance / f"{run_id}.json"
    mf.write_text(json.dumps(m), encoding="utf-8")

    cmd_run(_argns(manifest=str(mf)))
    cmd_complete(_argns(run_id=run_id, node_id="work", status="succeeded"))
    cmd_tick(_argns(run_id=run_id))
    return mf


class TestF13ApproveFlags:
    """
    RED → GREEN proof for F13.

    Tests that --note / --output / --reject are parsed, stored, and acted upon.
    A bare approve (no flags) must remain backward-compatible.
    """

    def test_bare_approve_still_works(self, simple_instance: Path, capsys):
        """Backward compat: bare approve (no flags) still sets status=succeeded."""
        run_id = "f13-bare"
        _boot_run_to_awaiting_go(simple_instance, run_id)
        capsys.readouterr()

        rc = cmd_approve(_argns(run_id=run_id, node_id="gate"))
        out = capsys.readouterr().out
        assert rc == 0, "Bare approve must succeed"
        assert "approved → succeeded" in out

        store = RunStore(simple_instance / "state")
        rs = store.load(run_id)
        assert rs.node_status("gate") == "succeeded"

    def test_parser_has_note_flag(self):
        """--note TEXT must be a recognized flag in the approve subparser."""
        p = build_parser()
        sub = p._subparsers._actions[-1]
        app_p = sub.choices["approve"]
        args = app_p.parse_args(["my-run", "my-node", "--note", "looks good"])
        assert args.note == "looks good"

    def test_parser_has_output_flag(self):
        """--output k=v (repeatable) must be a recognized flag in approve."""
        p = build_parser()
        sub = p._subparsers._actions[-1]
        app_p = sub.choices["approve"]
        args = app_p.parse_args(
            ["my-run", "my-node", "--output", "tier=A", "--output", "n=50"]
        )
        assert args.output == ["tier=A", "n=50"]

    def test_parser_has_reject_flag(self):
        """--reject must be a recognized flag in the approve subparser."""
        p = build_parser()
        sub = p._subparsers._actions[-1]
        app_p = sub.choices["approve"]
        args = app_p.parse_args(["my-run", "my-node", "--reject"])
        assert args.reject is True

    def test_note_persisted_round_trip(self, simple_instance: Path, capsys):
        """--note TEXT is stored in node_states and round-trips via store.load()."""
        run_id = "f13-note"
        _boot_run_to_awaiting_go(simple_instance, run_id)
        capsys.readouterr()

        rc = cmd_approve(
            _argns(run_id=run_id, node_id="gate", note="Plan looks solid; approved.")
        )
        assert rc == 0, "approve with --note must succeed"

        store = RunStore(simple_instance / "state")
        rs = store.load(run_id)
        ns = rs.node_states.get("gate", {})
        assert ns.get("decision_note") == "Plan looks solid; approved.", (
            f"decision_note not persisted. node_states: {ns!r}"
        )

    def test_outputs_persisted_round_trip(self, simple_instance: Path, capsys):
        """--output k=v pairs are stored in node_states['outputs'] and round-trip."""
        run_id = "f13-outputs"
        _boot_run_to_awaiting_go(simple_instance, run_id)
        capsys.readouterr()

        rc = cmd_approve(
            _argns(
                run_id=run_id,
                node_id="gate",
                output=["tier=A", "n=50", "mode=full"],
            )
        )
        assert rc == 0, "approve with --output must succeed"

        store = RunStore(simple_instance / "state")
        rs = store.load(run_id)
        ns = rs.node_states.get("gate", {})
        outputs = ns.get("outputs", {})
        assert outputs == {"tier": "A", "n": "50", "mode": "full"}, (
            f"outputs not persisted correctly. node_states: {ns!r}"
        )

    def test_reject_sets_blocked(self, simple_instance: Path, capsys):
        """--reject marks the gate 'blocked' (terminal) instead of 'succeeded'."""
        run_id = "f13-reject"
        _boot_run_to_awaiting_go(simple_instance, run_id)
        capsys.readouterr()

        rc = cmd_approve(
            _argns(
                run_id=run_id,
                node_id="gate",
                reject=True,
                note="Pilot failed threshold; rejecting.",
            )
        )
        out = capsys.readouterr().out
        assert rc == 0, "approve --reject must return 0 (command succeeded)"
        assert "REJECTED" in out or "blocked" in out, (
            f"reject output must mention REJECTED/blocked. out: {out!r}"
        )

        store = RunStore(simple_instance / "state")
        rs = store.load(run_id)
        assert rs.node_status("gate") == "blocked", (
            "after --reject, gate must be 'blocked'"
        )
        ns = rs.node_states.get("gate", {})
        assert ns.get("decision_note") == "Pilot failed threshold; rejecting."

    def test_reject_halts_downstream_frontier(self, simple_instance: Path, capsys):
        """
        After --reject, downstream afterok nodes do NOT appear in the frontier.

        Structure: work → gate (human-go) → downstream
        Reject gate → downstream stays out of frontier (afterok not satisfied).
        """
        run_id = "f13-halt"
        # Build a three-node manifest
        m = {
            "run_id": run_id,
            "name": "F13 halt test",
            "global_cap": 4,
            "nodes": [
                {"id": "work", "type": "agent", "spec": "fixture://t", "label": "work"},
                {
                    "id": "gate",
                    "type": "human-go",
                    "label": "gate",
                    "needs": [{"from": "work", "edge": "afterok"}],
                },
                {
                    "id": "downstream",
                    "type": "agent",
                    "spec": "fixture://t",
                    "label": "downstream",
                    "needs": [{"from": "gate", "edge": "afterok"}],
                },
            ],
        }
        mf = simple_instance / f"{run_id}.json"
        mf.write_text(json.dumps(m), encoding="utf-8")

        cmd_run(_argns(manifest=str(mf)))
        cmd_complete(_argns(run_id=run_id, node_id="work", status="succeeded"))
        cmd_tick(_argns(run_id=run_id))
        capsys.readouterr()

        # Reject the gate
        cmd_approve(_argns(run_id=run_id, node_id="gate", reject=True))
        capsys.readouterr()

        # Tick to re-compute frontier — downstream must NOT be dispatchable.
        # We check the store directly (not the printed output, which includes
        # SR-SCOPE label warnings that mention all node ids).
        cmd_tick(_argns(run_id=run_id))
        capsys.readouterr()

        store = RunStore(simple_instance / "state")
        rs = store.load(run_id)
        # node_status("downstream") == "pending" is true for BOTH the reject and
        # approve cases (dispatchable-pending vs blocked-pending look identical).
        # The real halt signal is frontier non-membership: a blocked downstream
        # must NOT appear in compute_frontier's dispatch items.
        frontier = compute_frontier(
            manifest=m,
            node_states=rs.node_states,
            edge_registered_ts=rs.edge_registered_ts,
            global_cap=m.get("global_cap", 4),
        )
        frontier_ids = {fn.node_id for fn in frontier}
        assert "downstream" not in frontier_ids, (
            f"After --reject, 'downstream' must be absent from the frontier; "
            f"got frontier_ids={frontier_ids!r}"
        )

    def test_output_malformed_returns_error(self, simple_instance: Path, capsys):
        """--output without '=' returns rc=1 and a clear error."""
        run_id = "f13-bad-output"
        _boot_run_to_awaiting_go(simple_instance, run_id)
        capsys.readouterr()

        rc = cmd_approve(
            _argns(run_id=run_id, node_id="gate", output=["not_kv_format"])
        )
        err = capsys.readouterr().err
        assert rc == 1, "Malformed --output must return rc=1"
        assert "k=v" in err or "format" in err

    def test_note_and_output_and_approve_combined(self, simple_instance: Path, capsys):
        """All three optional fields together work correctly."""
        run_id = "f13-combined"
        _boot_run_to_awaiting_go(simple_instance, run_id)
        capsys.readouterr()

        rc = cmd_approve(
            _argns(
                run_id=run_id,
                node_id="gate",
                note="All conditions met.",
                output=["alpha=1", "beta=2"],
                reject=False,
            )
        )
        assert rc == 0

        store = RunStore(simple_instance / "state")
        rs = store.load(run_id)
        ns = rs.node_states.get("gate", {})
        assert rs.node_status("gate") == "succeeded"
        assert ns.get("decision_note") == "All conditions met."
        assert ns.get("outputs") == {"alpha": "1", "beta": "2"}


# ===========================================================================
# F6: dag status and dag complete agree on a pending human-go frontier
# ===========================================================================


class TestF6StatusCompleteFrontierAgreement:
    """
    F6 regression test.

    Defect: after dag complete advances the work node (which calls
    _recompute_awaiting_go and promotes the human-go node to "awaiting-go"),
    dag status's "Current frontier" section called compute_frontier directly.
    compute_frontier skips "awaiting-go" nodes (they are in _NON_ADVANCEABLE),
    so the human-go node silently disappeared from the status frontier even
    though it still required human action.

    Fix: dag status appends already-promoted "awaiting-go" nodes to the
    frontier it displays, so BOTH dag complete and dag status show the same
    pending-human-go information (including the exact `rv dag approve` command).
    """

    def test_complete_shows_human_go_in_frontier(self, simple_instance: Path, capsys):
        """dag complete's frontier output includes the human-go node as AWAIT-GO."""
        run_id = "f6-complete"
        _boot_run_to_awaiting_go(simple_instance, run_id)
        # _boot_run_to_awaiting_go does: cmd_run, cmd_complete(work), cmd_tick
        # After that the gate node is "awaiting-go". Drain output.
        captured = capsys.readouterr().out

        # The cmd_complete call inside _boot_run_to_awaiting_go should have
        # printed a frontier that includes the AWAIT-GO line for the gate node.
        assert "AWAIT-GO" in captured, (
            "dag complete must include AWAIT-GO in its frontier output when a "
            f"human-go node is ready.\nGot output:\n{captured}"
        )
        assert f"rv dag approve {run_id} gate" in captured, (
            "dag complete must print the exact rv dag approve command for the "
            f"pending human-go node.\nGot output:\n{captured}"
        )

    def test_status_shows_human_go_in_frontier_after_complete(
        self, simple_instance: Path, capsys
    ):
        """dag status's Current frontier includes the human-go node after dag complete promotes it.

        This is the red-before-green anchor for F6:
        - OLD code: compute_frontier skips "awaiting-go" nodes → Current frontier empty
        - NEW  code: awaiting-go nodes injected back → Current frontier shows AWAIT-GO + approve command
        """
        run_id = "f6-status"
        _boot_run_to_awaiting_go(simple_instance, run_id)
        capsys.readouterr()  # drain output from boot

        # At this point the gate node is "awaiting-go" in the run state.
        rc = cmd_status(_argns(run_id=run_id))
        assert rc == 0
        out = capsys.readouterr().out

        # The Current frontier section must include the AWAIT-GO entry and approve command.
        assert "AWAIT-GO" in out, (
            "dag status's Current frontier must include AWAIT-GO for a promoted "
            f"human-go node.\nGot output:\n{out}"
        )
        assert f"rv dag approve {run_id} gate" in out, (
            "dag status must print the exact rv dag approve command for the "
            f"pending human-go node.\nGot output:\n{out}"
        )

    def test_status_and_complete_both_show_approve_command(
        self, simple_instance: Path, capsys
    ):
        """Both dag complete and dag status print the rv dag approve command for the same node.

        This is the agreement test: the same approve command appears in both outputs,
        confirming the two verbs agree on the frontier.
        """
        run_id = "f6-agreement"
        m = {
            "run_id": run_id,
            "name": "F6 agreement test",
            "global_cap": 4,
            "nodes": [
                {
                    "id": "work",
                    "type": "agent",
                    "spec": "fixture://test",
                    "label": "Work node",
                },
                {
                    "id": "gate",
                    "type": "human-go",
                    "label": "Approval gate",
                    "needs": [{"from": "work", "edge": "afterok"}],
                },
            ],
        }
        mf = simple_instance / f"{run_id}.json"
        mf.write_text(json.dumps(m), encoding="utf-8")

        cmd_run(_argns(manifest=str(mf)))
        capsys.readouterr()

        # Complete the work node — the frontier in the complete output should show AWAIT-GO
        cmd_complete(_argns(run_id=run_id, node_id="work", status="succeeded"))
        complete_out = capsys.readouterr().out
        approve_cmd = f"rv dag approve {run_id} gate"
        assert approve_cmd in complete_out, (
            f"dag complete must show '{approve_cmd}' in frontier.\nGot:\n{complete_out}"
        )

        # Now dag status — the gate is already "awaiting-go" in the run state
        cmd_status(_argns(run_id=run_id))
        status_out = capsys.readouterr().out
        assert approve_cmd in status_out, (
            f"dag status must also show '{approve_cmd}' in Current frontier.\nGot:\n{status_out}"
        )
