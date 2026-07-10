"""test_review_protocol_gate.py — task #33 acceptance tests.

E2E-MED: review protocol has no structural lint — counter-position not
mechanically enforced.

Before this fix, the L-2 anti-fishing gate (§5L.3) was agent-prose-only:
``review_scope_tips``/``review_critic_tips`` instruct the agent to freeze a
non-empty ``counter-position`` field in ``_protocol.md``, but nothing in code
checked it.  This closes the gap: ``rv dag approve <run> approve-protocol``
now structurally refuses when ``counter-position`` is empty or missing.

Coverage:
  1. check_protocol_gate (review/__init__.py) — unit-level
     1a. missing file → (False, ...)
     1b. empty counter-position field → (False, ...)
     1c. whitespace-only counter-position → (False, ...)
     1d. non-empty counter-position → (True, "OK")
  2. cmd_approve wiring (real DAG path, non-vacuous)
     2a. approve-protocol REFUSED when _protocol.md has empty counter-position:
         exit nonzero, node status stays 'awaiting-go' (no state mutation)
     2b. approve-protocol SUCCEEDS when _protocol.md has non-empty counter-position
     2c. --reject bypasses the gate (explicit abandon path always available)
     2d. mutation test: neutralize the check (monkeypatch check_protocol_gate to
         always return (True, "OK")) → the empty case now sails through (RED
         without the wiring; proves the test is sensitive to the gate, not
         to some other unrelated block)
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
# Shared helpers
# ---------------------------------------------------------------------------

def _protocol_note(
    path: Path,
    *,
    counter_position: str | None = "Opposing view: X does not scale.",
    deliverable: str | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if counter_position is None:
        fm = "type: literature\nquestion: does X scale?\n"
    else:
        fm = f"type: literature\nquestion: does X scale?\ncounter-position: {counter_position}\n"
    if deliverable is not None:
        fm += f"deliverable: {deliverable}\n"
    path.write_text(f"---\n{fm}---\n\n# Protocol\n", encoding="utf-8")
    return path


def _cfg_file(tmp_path: Path) -> Path:
    f = tmp_path / "research_vault.toml"
    f.write_text(
        f'instance_root = "{tmp_path}"\n'
        f'notes_root = "{tmp_path / "notes"}"\n'
        f'state_dir = "{tmp_path / "state"}"\n'
        f'agents_dir = "{tmp_path / ".agents"}"\n'
        f'tasks_dir = "{tmp_path / "tasks"}"\n'
        f'control_dir = "{tmp_path / "control"}"\n'
        '[adapters]\nnotifier = "file"\nbackend = "local"\nsecrets = "env"\n'
        '[approval]\nenforce = true\n'
        'token_fingerprint = "d309a810bb5f40cef518202e46d197aa61e4dddafc5984c8c698da29ac8fd2bc"\n'
        'enforce_sig = ""\n',
        encoding="utf-8",
    )
    return f


def _review_manifest(run_id: str, protocol_path: Path) -> dict:
    """A minimal manifest with the real review-loop node shape (§5L.1)."""
    return {
        "run_id": run_id,
        "name": "test review",
        "global_cap": 1,
        "nodes": [
            {
                "id": "review-scope",
                "type": "agent",
                "spec": "task://demo#scope",
                "produces": {"_protocol.md": str(protocol_path)},
                "needs": [],
            },
            {
                "id": "approve-protocol",
                "type": "human-go",
                "label": "Gate 1",
                "needs": [{"from": "review-scope", "edge": "afterok"}],
            },
        ],
    }


def _set_run_env(tmp_path: Path):
    cfg_file = _cfg_file(tmp_path)
    old = os.environ.get("RESEARCH_VAULT_CONFIG")
    os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_file)
    return old


def _restore_env(old):
    if old is None:
        os.environ.pop("RESEARCH_VAULT_CONFIG", None)
    else:
        os.environ["RESEARCH_VAULT_CONFIG"] = old


def _make_awaiting_run(tmp_path: Path, run_id: str, protocol_path: Path):
    """Build a run with 'approve-protocol' already promoted to awaiting-go."""
    from research_vault.dag.store import RunState, RunStore

    manifest = _review_manifest(run_id, protocol_path)
    manifest_path = tmp_path / f"{run_id}-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    store = RunStore(tmp_path / "state")
    rs = RunState(run_id=run_id, manifest_path=str(manifest_path))
    rs.init_nodes(manifest)
    rs.set_node_status("review-scope", "succeeded")
    rs.set_node_status("approve-protocol", "awaiting-go")
    store.create(rs)
    return store


# ---------------------------------------------------------------------------
# 1. check_protocol_gate — unit level
# ---------------------------------------------------------------------------

class TestCheckProtocolGate:
    def test_missing_file_blocks(self, tmp_path):
        from research_vault.review import check_protocol_gate
        ok, msg = check_protocol_gate(tmp_path / "nope" / "_protocol.md")
        assert ok is False
        assert "_protocol.md" in msg

    def test_empty_counter_position_blocks(self, tmp_path):
        from research_vault.review import check_protocol_gate
        p = _protocol_note(tmp_path / "_protocol.md", counter_position="")
        ok, msg = check_protocol_gate(p)
        assert ok is False
        assert "counter-position" in msg

    def test_missing_counter_position_field_blocks(self, tmp_path):
        from research_vault.review import check_protocol_gate
        p = _protocol_note(tmp_path / "_protocol.md", counter_position=None)
        ok, msg = check_protocol_gate(p)
        assert ok is False
        assert "counter-position" in msg

    def test_whitespace_only_counter_position_blocks(self, tmp_path):
        from research_vault.review import check_protocol_gate
        p = _protocol_note(tmp_path / "_protocol.md", counter_position="   ")
        ok, msg = check_protocol_gate(p)
        assert ok is False

    def test_non_empty_counter_position_passes(self, tmp_path):
        from research_vault.review import check_protocol_gate
        p = _protocol_note(
            tmp_path / "_protocol.md",
            counter_position="Contrarian literature Y claims the opposite.",
        )
        ok, msg = check_protocol_gate(p)
        assert ok is True
        assert msg == "OK"


# ---------------------------------------------------------------------------
# 1b. check_protocol_gate — deliverable field validation (default review,
#     opt-in manuscript, reject non-vocab)
# ---------------------------------------------------------------------------

class TestCheckProtocolGateDeliverable:
    def test_missing_deliverable_field_still_passes_defaults_review(self, tmp_path):
        """Absence of `deliverable` is the safe/smaller-commitment default
        (review, not a fail-closed halt) — the gate must NOT block on it."""
        from research_vault.review import check_protocol_gate
        p = _protocol_note(tmp_path / "_protocol.md", deliverable=None)
        ok, msg = check_protocol_gate(p)
        assert ok is True
        assert msg == "OK"

    def test_explicit_review_deliverable_passes(self, tmp_path):
        from research_vault.review import check_protocol_gate
        p = _protocol_note(tmp_path / "_protocol.md", deliverable="review")
        ok, msg = check_protocol_gate(p)
        assert ok is True

    def test_explicit_manuscript_deliverable_passes(self, tmp_path):
        from research_vault.review import check_protocol_gate
        p = _protocol_note(tmp_path / "_protocol.md", deliverable="manuscript")
        ok, msg = check_protocol_gate(p)
        assert ok is True

    def test_malformed_deliverable_value_blocks_loudly(self, tmp_path):
        """A typo'd/non-vocab `deliverable` value (e.g. 'paper') is a
        mis-stated intent, not an absence — reject loudly, never silently
        default it away."""
        from research_vault.review import check_protocol_gate
        p = _protocol_note(tmp_path / "_protocol.md", deliverable="paper")
        ok, msg = check_protocol_gate(p)
        assert ok is False
        assert "deliverable" in msg

    def test_blank_deliverable_value_still_passes_defaults_review(self, tmp_path):
        """A present-but-blank `deliverable:` field is treated like absence
        (default review), not like a malformed non-vocab value."""
        from research_vault.review import check_protocol_gate
        p = _protocol_note(tmp_path / "_protocol.md", deliverable="")
        ok, msg = check_protocol_gate(p)
        assert ok is True


# ---------------------------------------------------------------------------
# 1c. read_protocol_deliverable — the read-side helper `_emit_next_phase`
#     uses at approve-review to decide manuscript-emission
# ---------------------------------------------------------------------------

class TestReadProtocolDeliverable:
    def test_missing_file_defaults_review(self, tmp_path):
        from research_vault.review import read_protocol_deliverable
        assert read_protocol_deliverable(tmp_path / "nope" / "_protocol.md") == "review"

    def test_absent_field_defaults_review(self, tmp_path):
        from research_vault.review import read_protocol_deliverable
        p = _protocol_note(tmp_path / "_protocol.md", deliverable=None)
        assert read_protocol_deliverable(p) == "review"

    def test_explicit_review_reads_review(self, tmp_path):
        from research_vault.review import read_protocol_deliverable
        p = _protocol_note(tmp_path / "_protocol.md", deliverable="review")
        assert read_protocol_deliverable(p) == "review"

    def test_explicit_manuscript_reads_manuscript(self, tmp_path):
        from research_vault.review import read_protocol_deliverable
        p = _protocol_note(tmp_path / "_protocol.md", deliverable="manuscript")
        assert read_protocol_deliverable(p) == "manuscript"

    def test_case_and_whitespace_tolerant(self, tmp_path):
        from research_vault.review import read_protocol_deliverable
        p = _protocol_note(tmp_path / "_protocol.md", deliverable="  Manuscript  ")
        assert read_protocol_deliverable(p) == "manuscript"

    def test_malformed_value_defaults_review_not_crash(self, tmp_path):
        """Defensive: by the time approve-review runs, approve-protocol's
        gate should already have rejected a malformed value — but this
        read-side helper never enforces the vocab itself, so an unexpected
        value here must fall back to the conservative default, never crash
        or accidentally read as manuscript."""
        from research_vault.review import read_protocol_deliverable
        p = _protocol_note(tmp_path / "_protocol.md", deliverable="paper")
        assert read_protocol_deliverable(p) == "review"


# ---------------------------------------------------------------------------
# 2. cmd_approve wiring — real DAG path, non-vacuous
# ---------------------------------------------------------------------------

class TestApproveProtocolGateWiring:
    def test_empty_counter_position_refuses_approval_no_state_mutation(self, tmp_path):
        """RED-before-GREEN: empty counter-position → nonzero exit, node status
        stays 'awaiting-go' (no state mutation)."""
        from research_vault.dag.verbs import cmd_approve
        from research_vault.dag.store import RunStore

        old = _set_run_env(tmp_path)
        try:
            protocol_path = tmp_path / "reviews" / "scope-a" / "_protocol.md"
            _protocol_note(protocol_path, counter_position="")
            store = _make_awaiting_run(tmp_path, "review-empty-cp", protocol_path)

            args = argparse.Namespace(run_id="review-empty-cp", node_id="approve-protocol")
            rc = cmd_approve(args)

            assert rc != 0, "approve-protocol must refuse on empty counter-position"

            rs = store.load("review-empty-cp")
            assert rs.node_status("approve-protocol") == "awaiting-go", (
                "node must NOT mutate to 'succeeded' when the L-2 gate blocks"
            )
        finally:
            _restore_env(old)

    def test_missing_counter_position_field_refuses_approval(self, tmp_path):
        from research_vault.dag.verbs import cmd_approve
        from research_vault.dag.store import RunStore

        old = _set_run_env(tmp_path)
        try:
            protocol_path = tmp_path / "reviews" / "scope-b" / "_protocol.md"
            _protocol_note(protocol_path, counter_position=None)
            store = _make_awaiting_run(tmp_path, "review-missing-cp", protocol_path)

            args = argparse.Namespace(run_id="review-missing-cp", node_id="approve-protocol")
            rc = cmd_approve(args)

            assert rc != 0
            rs = store.load("review-missing-cp")
            assert rs.node_status("approve-protocol") == "awaiting-go"
        finally:
            _restore_env(old)

    def test_non_empty_counter_position_approves_cleanly(self, tmp_path):
        """Non-empty counter-position → approval proceeds normally (node → succeeded)."""
        from research_vault.dag.verbs import cmd_approve
        from research_vault.dag.store import RunStore

        old = _set_run_env(tmp_path)
        try:
            protocol_path = tmp_path / "reviews" / "scope-c" / "_protocol.md"
            _protocol_note(
                protocol_path,
                counter_position="Sought the opposing 'small models suffice' literature.",
            )
            store = _make_awaiting_run(tmp_path, "review-good-cp", protocol_path)

            args = argparse.Namespace(run_id="review-good-cp", node_id="approve-protocol")
            rc = cmd_approve(args)

            assert rc == 0
            rs = store.load("review-good-cp")
            assert rs.node_status("approve-protocol") == "succeeded"
        finally:
            _restore_env(old)

    def test_reject_bypasses_the_gate(self, tmp_path):
        """--reject is an explicit abandon/redo path; it must not be blocked by
        this gate even when counter-position is empty (a human choosing to
        reject the protocol outright should always be able to)."""
        from research_vault.dag.verbs import cmd_approve
        from research_vault.dag.store import RunStore

        old = _set_run_env(tmp_path)
        try:
            protocol_path = tmp_path / "reviews" / "scope-d" / "_protocol.md"
            _protocol_note(protocol_path, counter_position="")
            store = _make_awaiting_run(tmp_path, "review-reject-cp", protocol_path)

            args = argparse.Namespace(
                run_id="review-reject-cp", node_id="approve-protocol", reject=True
            )
            rc = cmd_approve(args)

            assert rc == 0
            rs = store.load("review-reject-cp")
            assert rs.node_status("approve-protocol") == "blocked"
        finally:
            _restore_env(old)

    def test_mutation_neutralize_check_lets_empty_case_sail_through(self, tmp_path, monkeypatch):
        """Mutation test: with check_protocol_gate neutralized to always pass,
        the empty-counter-position case now sails through — proving the
        refusal in test_empty_counter_position_refuses_approval_no_state_mutation
        is actually load-bearing on this gate (not some unrelated block)."""
        import research_vault.review as review_mod

        old = _set_run_env(tmp_path)
        try:
            monkeypatch.setattr(
                review_mod, "check_protocol_gate", lambda p: (True, "OK")
            )
            # cmd_approve does `from ..review import check_protocol_gate` inline,
            # which re-binds the name from the (now-patched) module each call.
            from research_vault.dag.verbs import cmd_approve
            from research_vault.dag.store import RunStore

            protocol_path = tmp_path / "reviews" / "scope-e" / "_protocol.md"
            _protocol_note(protocol_path, counter_position="")
            store = _make_awaiting_run(tmp_path, "review-neutered", protocol_path)

            args = argparse.Namespace(run_id="review-neutered", node_id="approve-protocol")
            rc = cmd_approve(args)

            assert rc == 0, (
                "with the gate neutralized, the empty counter-position case "
                "must sail through — confirms the real gate is what blocks it"
            )
            rs = store.load("review-neutered")
            assert rs.node_status("approve-protocol") == "succeeded"
        finally:
            _restore_env(old)
