"""test_dag_approve_auto.py — NG-4 §1: `rv dag approve --auto` end-to-end.

Coverage: the four autonomous gates (coverage-gate / approve-framework /
approve-manuscript / approve-review) resolved by the gate-policy engine, real
DAG path (cmd_approve), not just the unit-level classify_disposition tests in
test_review_autonomy.py.

  1. coverage-gate --auto: saturated -> auto-approved, no human-presence
     check required (RV_APPROVER_TOKEN unset — proves the bypass).
  2. coverage-gate --auto: malformed stop_reason -> auto-rejected
     (HALT-DECLARE), node -> blocked, decision_note carries the reason.
  3. approve-framework --auto: empty spine -> REVISE, rc == 2, node
     REMAINS awaiting-go (no state mutation on REVISE).
  4. approve-protocol NEVER autonomizes even with --auto (falls through to
     the human-presence check, which fails closed with no token).
  5. approve-review --auto (single-human-gate design, 2026-07-09): a
     coverage-critic ``verdict: PASS`` frontmatter field -> auto-approved;
     ``verdict: BLOCK`` -> REVISE with the blocking reasons surfaced; a
     missing critic artifact -> HALT-DECLARE (never a silent GO).
  6. Structured-verdict fix (PR #201 review delta, 2026-07-09):
     ``check_coverage_critic_verdict`` reads ONLY the ``verdict:``
     frontmatter field — prose ``[PASS]``/``[BLOCK]`` bracket tokens
     anywhere in the body are never consulted. This replaces three
     iterations of prose-scanning heuristics (first-match -> line-anchor ->
     ambiguity-check) that each closed one evasion and left another open.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


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


@pytest.fixture
def run_env(tmp_path: Path, monkeypatch):
    cfg_file = _cfg_file(tmp_path)
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_file))
    # ★ Deliberately UNSET the approver token — proves --auto's autonomous
    # gates never touch check_human_presence, while approve-protocol still
    # requires it (test 4).
    monkeypatch.delenv("RV_APPROVER_TOKEN", raising=False)
    from research_vault.config import reset_config_cache
    reset_config_cache()
    yield tmp_path
    reset_config_cache()


def _walk_note(path: Path, *, stop_reason: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nstop_reason: {stop_reason}\n---\n\nCitation-neighbor relevance walk.\n", encoding="utf-8")


def _coverage_gate_manifest(run_id: str, walk_path: Path) -> dict:
    return {
        "run_id": run_id,
        "name": "test review",
        "global_cap": 1,
        "nodes": [
            {
                "id": "review-snowball", "type": "agent", "spec": "task://demo#snowball",
                "produces": {"_walk.md": str(walk_path)}, "needs": [],
            },
            {
                "id": "coverage-gate", "type": "human-go", "label": "Gate 2",
                "needs": [{"from": "review-snowball", "edge": "afterok"}],
            },
        ],
    }


def _make_awaiting_run(tmp_path: Path, run_id: str, manifest: dict, gate_node_id: str):
    from research_vault.dag.store import RunState, RunStore

    manifest_path = tmp_path / f"{run_id}-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    store = RunStore(tmp_path / "state")
    rs = RunState(run_id=run_id, manifest_path=str(manifest_path))
    rs.init_nodes(manifest)
    for node in manifest["nodes"]:
        if node["id"] != gate_node_id:
            rs.set_node_status(node["id"], "succeeded")
    rs.set_node_status(gate_node_id, "awaiting-go")
    store.create(rs)
    return store


class TestCoverageGateAuto:
    def test_walk_complete_auto_approves_without_human_presence(self, run_env: Path):
        from research_vault.dag.verbs import cmd_approve

        walk_path = run_env / "reviews" / "scope-a" / "_walk.md"
        _walk_note(walk_path, stop_reason="walk-complete:1-hops")
        manifest = _coverage_gate_manifest("auto-run-1", walk_path)
        store = _make_awaiting_run(run_env, "auto-run-1", manifest, "coverage-gate")

        args = argparse.Namespace(run_id="auto-run-1", node_id="coverage-gate", auto=True)
        rc = cmd_approve(args)
        assert rc == 0
        rs = store.load("auto-run-1")
        assert rs.node_status("coverage-gate") == "succeeded"
        assert rs.node_states["coverage-gate"]["approved_by"] == "review.autonomy"

    def test_malformed_stop_reason_auto_rejects(self, run_env: Path, capsys):
        from research_vault.dag.verbs import cmd_approve

        walk_path = run_env / "reviews" / "scope-b" / "_walk.md"
        _walk_note(walk_path, stop_reason="walk-complete-1-hops")  # non-canonical
        manifest = _coverage_gate_manifest("auto-run-2", walk_path)
        store = _make_awaiting_run(run_env, "auto-run-2", manifest, "coverage-gate")

        args = argparse.Namespace(run_id="auto-run-2", node_id="coverage-gate", auto=True)
        rc = cmd_approve(args)
        captured = capsys.readouterr()
        # HALT-DECLARE resolves via the reject path -> succeeds mechanically
        # (state write happens) but the node is BLOCKED, not succeeded.
        assert rc == 0
        rs = store.load("auto-run-2")
        assert rs.node_status("coverage-gate") == "blocked"
        assert "HALT-DECLARE" in rs.node_states["coverage-gate"].get("decision_note", "")
        assert "HALT-DECLARE" in captured.err

    def test_budget_with_declared_residue_goes_with_residue(self, run_env: Path):
        from research_vault.dag.verbs import cmd_approve

        review_dir = run_env / "reviews" / "scope-c"
        walk_path = review_dir / "_walk.md"
        _walk_note(walk_path, stop_reason="budget:200-calls")
        (review_dir / "_coverage-gaps.md").write_text("open frontier\n", encoding="utf-8")
        manifest = _coverage_gate_manifest("auto-run-3", walk_path)
        store = _make_awaiting_run(run_env, "auto-run-3", manifest, "coverage-gate")

        args = argparse.Namespace(run_id="auto-run-3", node_id="coverage-gate", auto=True)
        rc = cmd_approve(args)
        assert rc == 0
        rs = store.load("auto-run-3")
        assert rs.node_status("coverage-gate") == "succeeded"


class TestFrameworkGateAuto:
    def _framework_manifest(self, run_id: str) -> dict:
        return {
            "run_id": run_id, "name": "ms", "global_cap": 1,
            "nodes": [
                {"id": "framework-propose", "type": "agent", "spec": "task://demo#fw", "needs": []},
                {
                    "id": "approve-framework", "type": "human-go",
                    "needs": [{"from": "framework-propose", "edge": "afterok"}],
                },
            ],
        }

    def test_empty_spine_revises_without_state_change(self, run_env: Path, capsys):
        from research_vault.dag.verbs import cmd_approve

        # _manuscript.md sits next to the manifest (manifest_path.parent)
        (run_env / "_manuscript.md").write_text(
            "---\ntitle: t\n---\n\nno spine_shape here\n", encoding="utf-8",
        )
        manifest = self._framework_manifest("auto-run-4")
        store = _make_awaiting_run(run_env, "auto-run-4", manifest, "approve-framework")

        args = argparse.Namespace(run_id="auto-run-4", node_id="approve-framework", auto=True)
        rc = cmd_approve(args)
        captured = capsys.readouterr()
        assert rc == 2
        assert "REVISE" in captured.err
        rs = store.load("auto-run-4")
        # No state mutation on REVISE — the node stays awaiting-go.
        assert rs.node_status("approve-framework") == "awaiting-go"


class TestApproveProtocolNeverAutonomizes:
    def test_approve_protocol_ignores_auto_and_fails_closed(self, run_env: Path, capsys):
        from research_vault.dag.verbs import cmd_approve

        protocol_path = run_env / "reviews" / "scope-d" / "_protocol.md"
        protocol_path.parent.mkdir(parents=True, exist_ok=True)
        protocol_path.write_text(
            "---\ncounter-position: a real counter-position\n---\n\nprotocol\n", encoding="utf-8",
        )
        manifest = {
            "run_id": "auto-run-5", "name": "review", "global_cap": 1,
            "nodes": [
                {
                    "id": "review-scope", "type": "agent", "spec": "task://demo#scope",
                    "produces": {"_protocol.md": str(protocol_path)}, "needs": [],
                },
                {
                    "id": "approve-protocol", "type": "human-go",
                    "needs": [{"from": "review-scope", "edge": "afterok"}],
                },
            ],
        }
        store = _make_awaiting_run(run_env, "auto-run-5", manifest, "approve-protocol")

        args = argparse.Namespace(run_id="auto-run-5", node_id="approve-protocol", auto=True)
        rc = cmd_approve(args)
        captured = capsys.readouterr()
        # approve-protocol is NEVER in _AUTONOMOUS_GATE_IDS -> falls through
        # to check_human_presence, which fails closed with no token/TTY.
        assert rc == 1
        rs = store.load("auto-run-5")
        assert rs.node_status("approve-protocol") == "awaiting-go"


class TestApproveReviewGateAuto:
    """Single-human-gate design (2026-07-09): approve-review is the 4th
    autonomous gate — resolved from review-coverage-critic's STRUCTURED
    ``verdict:`` frontmatter field on `_coverage-critic.md`, same shape as
    approve-framework's structural-payload wiring
    (evaluation_from_structural_payload -> classify_disposition). No new
    disposition path invented.
    """

    def _critic_note(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _review_manifest(self, run_id: str, critic_path: Path) -> dict:
        return {
            "run_id": run_id, "name": "review-phase2", "global_cap": 1,
            "nodes": [
                {
                    "id": "review-coverage-critic", "type": "agent",
                    "spec": "task://demo#critic",
                    "produces": {"_coverage-critic.md": str(critic_path)},
                    "needs": [],
                },
                {
                    "id": "approve-review", "type": "human-go",
                    "needs": [{"from": "review-coverage-critic", "edge": "afterok"}],
                },
            ],
        }

    def test_pass_verdict_auto_approves(self, run_env: Path):
        from research_vault.dag.verbs import cmd_approve

        critic_path = run_env / "reviews" / "scope-e" / "_coverage-critic.md"
        self._critic_note(
            critic_path,
            "---\nverdict: PASS\n---\n\n"
            "12 papers, 4 rounds, plateau at round 3; 0 orphan concepts "
            "(soft); counter-position: sought; 0 BLOCK(s).\n",
        )
        manifest = self._review_manifest("auto-run-6", critic_path)
        store = _make_awaiting_run(run_env, "auto-run-6", manifest, "approve-review")

        args = argparse.Namespace(run_id="auto-run-6", node_id="approve-review", auto=True)
        rc = cmd_approve(args)
        assert rc == 0
        rs = store.load("auto-run-6")
        assert rs.node_status("approve-review") == "succeeded"
        assert rs.node_states["approve-review"]["approved_by"] == "review.autonomy"

    def test_block_verdict_revises_without_state_change(self, run_env: Path, capsys):
        from research_vault.dag.verbs import cmd_approve

        critic_path = run_env / "reviews" / "scope-f" / "_coverage-critic.md"
        self._critic_note(
            critic_path,
            "---\nverdict: BLOCK\n---\n\n"
            "5 papers, 2 rounds, plateau at round 2; 0 orphan concepts "
            "(soft); counter-position: absent; 1 BLOCK(s).\n"
            "  - COUNTER-POSITION ABSENT (axis 4 — hard block)\n",
        )
        manifest = self._review_manifest("auto-run-7", critic_path)
        store = _make_awaiting_run(run_env, "auto-run-7", manifest, "approve-review")

        args = argparse.Namespace(run_id="auto-run-7", node_id="approve-review", auto=True)
        rc = cmd_approve(args)
        captured = capsys.readouterr()
        assert rc == 2
        assert "REVISE" in captured.err
        assert "COUNTER-POSITION ABSENT" in captured.err
        rs = store.load("auto-run-7")
        # No state mutation on REVISE — the node stays awaiting-go.
        assert rs.node_status("approve-review") == "awaiting-go"

    def test_missing_critic_artifact_halts(self, run_env: Path, capsys):
        from research_vault.dag.verbs import cmd_approve

        critic_path = run_env / "reviews" / "scope-g" / "_coverage-critic.md"
        # Never written.
        manifest = self._review_manifest("auto-run-8", critic_path)
        store = _make_awaiting_run(run_env, "auto-run-8", manifest, "approve-review")

        args = argparse.Namespace(run_id="auto-run-8", node_id="approve-review", auto=True)
        rc = cmd_approve(args)
        captured = capsys.readouterr()
        assert rc == 0
        rs = store.load("auto-run-8")
        assert rs.node_status("approve-review") == "blocked"
        assert "HALT-DECLARE" in rs.node_states["approve-review"].get("decision_note", "")
        assert "HALT-DECLARE" in captured.err

    def test_no_provisional_bookkeeping_anywhere(self, run_env: Path):
        """The auto-resolved decision is final immediately — no provisional
        stamp is ever written to the critic note or anywhere else."""
        from research_vault.dag.verbs import cmd_approve

        critic_path = run_env / "reviews" / "scope-h" / "_coverage-critic.md"
        self._critic_note(
            critic_path, "---\nverdict: PASS\n---\n\n3 papers, 1 round; 0 BLOCK(s).\n"
        )
        manifest = self._review_manifest("auto-run-9", critic_path)
        _make_awaiting_run(run_env, "auto-run-9", manifest, "approve-review")

        args = argparse.Namespace(run_id="auto-run-9", node_id="approve-review", auto=True)
        cmd_approve(args)
        assert "provisional" not in critic_path.read_text().lower()


class TestCoverageCriticStructuredVerdict:
    """PR #201 review delta (2026-07-09): ``check_coverage_critic_verdict``
    reads ONLY the structured ``verdict:`` frontmatter field — prose is
    NEVER scanned for ``[PASS]``/``[BLOCK]`` tokens. This replaces three
    successive prose-scanning heuristics (first-match anywhere -> line-start
    anchoring -> single-token ambiguity check) that each closed one evasion
    and left another open. A fixed 2-value vocab has no such evasion
    surface — this is a design change (stop parsing prose), not a further
    tightened heuristic.
    """

    def test_wellformed_pass_go(self, tmp_path: Path):
        from research_vault.review import check_coverage_critic_verdict

        note = tmp_path / "_coverage-critic.md"
        note.write_text(
            "---\nverdict: PASS\n---\n\n"
            "12 papers, 4 rounds, plateau at round 3; 0 BLOCK(s).\n",
            encoding="utf-8",
        )
        result = check_coverage_critic_verdict(note)
        assert result == {
            "blocking": [], "not_run": [],
            "remediation_target": None, "remediation_target_expected": False,
        }

    def test_wellformed_block_surfaces_reasons(self, tmp_path: Path):
        from research_vault.review import check_coverage_critic_verdict

        note = tmp_path / "_coverage-critic.md"
        note.write_text(
            "---\nverdict: BLOCK\n---\n\n"
            "5 papers, 2 rounds; 1 BLOCK(s).\n"
            "  - COUNTER-POSITION ABSENT (axis 4 — hard block)\n",
            encoding="utf-8",
        )
        result = check_coverage_critic_verdict(note)
        assert result["not_run"] == []
        assert result["blocking"] == ["COUNTER-POSITION ABSENT (axis 4 — hard block)"]

    def test_verdict_case_normalized(self, tmp_path: Path):
        from research_vault.review import check_coverage_critic_verdict

        note = tmp_path / "_coverage-critic.md"
        note.write_text("---\nverdict: pass\n---\n\nlowercase ok.\n", encoding="utf-8")
        result = check_coverage_critic_verdict(note)
        assert result == {
            "blocking": [], "not_run": [],
            "remediation_target": None, "remediation_target_expected": False,
        }

    def test_duplicate_verdict_keys_fail_closed(self, tmp_path: Path):
        """Contradictory duplicate ``verdict:`` keys must fail-closed, NOT
        resolve last-wins to GO. `_parse_frontmatter` is last-wins on repeated
        scalar keys, so `verdict: BLOCK` then `verdict: PASS` would silently
        GO without this guard — a residual silent-GO on the humanless gate."""
        from research_vault.review import check_coverage_critic_verdict

        note = tmp_path / "_coverage-critic.md"
        note.write_text(
            "---\nverdict: BLOCK\nverdict: PASS\n---\n\nprotocol drift.\n",
            encoding="utf-8",
        )
        result = check_coverage_critic_verdict(note)
        assert result["blocking"] == []
        assert result["not_run"], "duplicate verdict keys must fail-closed (not GO)"
        # and the reverse order (PASS then BLOCK) also fails closed, not a pass
        note.write_text(
            "---\nverdict: PASS\nverdict: BLOCK\n---\n\nx.\n", encoding="utf-8"
        )
        assert check_coverage_critic_verdict(note)["not_run"]

    def test_prose_only_bracket_tokens_no_verdict_field_fails_closed(self, tmp_path: Path):
        """THE anti-evasion test: a note with [PASS]/[BLOCK] ONLY in body
        prose and NO `verdict:` frontmatter field must fail closed — proves
        prose is completely ignored, not merely anchored/disambiguated."""
        from research_vault.review import check_coverage_critic_verdict

        note = tmp_path / "_coverage-critic.md"
        note.write_text(
            "[PASS]: 12 papers, 4 rounds, plateau at round 3; 0 BLOCK(s).\n"
            "Everything looks great, clearly a [PASS].\n",
            encoding="utf-8",
        )
        result = check_coverage_critic_verdict(note)
        assert result["blocking"] == []
        assert result["not_run"] != []
        assert "verdict" in result["not_run"][0].lower()

    def test_missing_verdict_field_with_frontmatter_present_fails_closed(self, tmp_path: Path):
        from research_vault.review import check_coverage_critic_verdict

        note = tmp_path / "_coverage-critic.md"
        note.write_text(
            "---\nother_field: something\n---\n\n[BLOCK]: prose only.\n",
            encoding="utf-8",
        )
        result = check_coverage_critic_verdict(note)
        assert result["blocking"] == []
        assert result["not_run"] != []

    def test_empty_verdict_field_fails_closed(self, tmp_path: Path):
        from research_vault.review import check_coverage_critic_verdict

        note = tmp_path / "_coverage-critic.md"
        note.write_text("---\nverdict: \n---\n\nno value given.\n", encoding="utf-8")
        result = check_coverage_critic_verdict(note)
        assert result["blocking"] == []
        assert result["not_run"] != []

    def test_non_vocab_verdict_value_fails_closed(self, tmp_path: Path):
        from research_vault.review import check_coverage_critic_verdict

        note = tmp_path / "_coverage-critic.md"
        note.write_text("---\nverdict: MAYBE\n---\n\nambiguous.\n", encoding="utf-8")
        result = check_coverage_critic_verdict(note)
        assert result["blocking"] == []
        assert result["not_run"] != []

    def test_malformed_frontmatter_fails_closed(self, tmp_path: Path):
        from research_vault.review import check_coverage_critic_verdict

        note = tmp_path / "_coverage-critic.md"
        # No closing `---` — malformed frontmatter, no field can be trusted.
        note.write_text("---\nverdict: PASS\n\nno closing delimiter.\n", encoding="utf-8")
        result = check_coverage_critic_verdict(note)
        assert result["blocking"] == []
        assert result["not_run"] != []

    def test_block_with_no_bullets_gets_generic_reason(self, tmp_path: Path):
        from research_vault.review import check_coverage_critic_verdict

        note = tmp_path / "_coverage-critic.md"
        note.write_text("---\nverdict: BLOCK\n---\n\nno bullets here.\n", encoding="utf-8")
        result = check_coverage_critic_verdict(note)
        assert result["not_run"] == []
        assert len(result["blocking"]) == 1
        assert "no itemized reason bullets" in result["blocking"][0].lower()

    def test_missing_artifact_still_not_run(self, tmp_path: Path):
        from research_vault.review import check_coverage_critic_verdict

        note = tmp_path / "_never-written.md"
        result = check_coverage_critic_verdict(note)
        assert result["blocking"] == []
        assert result["not_run"] == [str(note)]


class TestApproveReviewGateAntiFishing:
    """End-to-end (cmd_approve --auto) proof that a note carrying ONLY
    prose ``[PASS]``/``[BLOCK]`` bracket tokens — and NO structured
    ``verdict:`` frontmatter field — resolves HALT-DECLARE (rc == 0,
    node -> blocked), never a silent GO. The single-human-gate design has
    no backstop, so a silent GO here would ship an unreviewed corpus
    (PR #201 review delta — structured-verdict fix, 2026-07-09). This
    supersedes the prior anchoring/ambiguity-heuristic tests: prose is now
    unconditionally ignored, not merely disambiguated."""

    def _critic_note(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _review_manifest(self, run_id: str, critic_path: Path) -> dict:
        return {
            "run_id": run_id, "name": "review-phase2", "global_cap": 1,
            "nodes": [
                {
                    "id": "review-coverage-critic", "type": "agent",
                    "spec": "task://demo#critic",
                    "produces": {"_coverage-critic.md": str(critic_path)},
                    "needs": [],
                },
                {
                    "id": "approve-review", "type": "human-go",
                    "needs": [{"from": "review-coverage-critic", "edge": "afterok"}],
                },
            ],
        }

    def test_prose_only_pass_token_resolves_halt_not_go(self, run_env: Path, capsys):
        """The exact evasion shape that shipped green three times: a note
        opening a line with ``[PASS]`` and NO ``verdict:`` field. Under the
        structured-field fix this is fail-closed HALT, never an auto-GO."""
        from research_vault.dag.verbs import cmd_approve

        critic_path = run_env / "reviews" / "scope-i" / "_coverage-critic.md"
        self._critic_note(
            critic_path,
            "[PASS]: 12 papers, 4 rounds, plateau at round 3; 0 BLOCK(s).\n"
            "Everything looks great, clearly a [PASS].\n",
        )
        manifest = self._review_manifest("auto-run-10", critic_path)
        store = _make_awaiting_run(run_env, "auto-run-10", manifest, "approve-review")

        args = argparse.Namespace(run_id="auto-run-10", node_id="approve-review", auto=True)
        rc = cmd_approve(args)
        captured = capsys.readouterr()
        assert rc == 0
        assert "HALT-DECLARE" in captured.err
        rs = store.load("auto-run-10")
        assert rs.node_status("approve-review") == "blocked"
        assert "HALT-DECLARE" in rs.node_states["approve-review"].get("decision_note", "")

    def test_prose_only_block_token_resolves_halt_not_go(self, run_env: Path, capsys):
        """A note carrying only prose [BLOCK] (no verdict: field) must also
        fail closed — the gate never infers a verdict from prose, in
        either direction."""
        from research_vault.dag.verbs import cmd_approve

        critic_path = run_env / "reviews" / "scope-j" / "_coverage-critic.md"
        self._critic_note(
            critic_path,
            "Output legend: [PASS] = clean, [BLOCK] = holes.\n"
            "\n"
            "[BLOCK]: 3 papers, 1 round; PROTOCOL-DRIFT detected.\n"
            "  - PROTOCOL-DRIFT\n",
        )
        manifest = self._review_manifest("auto-run-11", critic_path)
        store = _make_awaiting_run(run_env, "auto-run-11", manifest, "approve-review")

        args = argparse.Namespace(run_id="auto-run-11", node_id="approve-review", auto=True)
        rc = cmd_approve(args)
        captured = capsys.readouterr()
        assert rc == 0
        assert "HALT-DECLARE" in captured.err
        rs = store.load("auto-run-11")
        assert rs.node_status("approve-review") == "blocked"

    def test_structured_block_verdict_resolves_revise_not_go(self, run_env: Path, capsys):
        """Positive control: a WELL-FORMED verdict: BLOCK still correctly
        resolves REVISE with reasons surfaced (the fix must not also break
        the legitimate BLOCK path)."""
        from research_vault.dag.verbs import cmd_approve

        critic_path = run_env / "reviews" / "scope-k" / "_coverage-critic.md"
        self._critic_note(
            critic_path,
            "---\nverdict: BLOCK\n---\n\n"
            "3 papers, 1 round; PROTOCOL-DRIFT detected.\n"
            "  - PROTOCOL-DRIFT\n",
        )
        manifest = self._review_manifest("auto-run-12", critic_path)
        store = _make_awaiting_run(run_env, "auto-run-12", manifest, "approve-review")

        args = argparse.Namespace(run_id="auto-run-12", node_id="approve-review", auto=True)
        rc = cmd_approve(args)
        captured = capsys.readouterr()
        assert rc == 2
        assert "REVISE" in captured.err
        assert "PROTOCOL-DRIFT" in captured.err
        rs = store.load("auto-run-12")
        assert rs.node_status("approve-review") == "awaiting-go"
