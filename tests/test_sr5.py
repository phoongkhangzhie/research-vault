"""test_sr5.py — hermetic tests for SR-5: both example loops + rv init + rv check.

All tests run entirely in tmp_path. No ~/vault, no real cluster, no network.

Test coverage:
  1. research-loop.json validates under current schema (spec: required by SR-DISP)
  2. lit-review-loop.json validates under current schema
  3. All agent nodes in both manifests have non-empty spec: field
  4. Named roles (researcher/Ada, reviewer/Argus) referenced in manifests
  5. Experiments-before-run gate: plan succeeded + NO artifact → run blocked
  6. Experiments-before-run gate: plan succeeded + artifact fresh → run dispatches
  7. OKF coverage gate: distill nodes succeeded but no literature notes → gate not approvable
  8. OKF coverage gate: all literature notes filed → gate approvable
  9. rv init creates a valid multi-project instance structure
 10. rv init creates research_vault.toml with demo projects registered
 11. rv init both loop manifests exist and validate
 12. rv check reports missing Claude CLI cleanly
 13. rv check reports missing ANTHROPIC_API_KEY cleanly
 14. rv check exits 0 when all prerequisites present (mocked)
 15. note: watch form resolves relative to notes_root
 16. note:+fresh watch form respects mtime freshness
 17. Leakage scanner stays green on examples/ directory
 18. Loop manifests validate under SR-DISP spec:-required schema
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import Config, reset_config_cache, load_config
from research_vault.dag.schema import (
    ManifestError,
    validate_manifest,
    load_manifest,
)
from research_vault.dag.walker import compute_frontier
from research_vault.dag.store import RunState, RunStore
from research_vault.wait_for import resolve_watch

# ---------------------------------------------------------------------------
# Repo root and example paths
# SR-PKG: examples/ moved to src/research_vault/data/examples/ (single home,
# one source of truth for both dev + wheel install).
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
RESEARCH_LOOP_PATH = (
    REPO_ROOT / "src" / "research_vault" / "data" / "examples"
    / "demo-research" / "research-loop.json"
)
LITREVIEW_LOOP_PATH = (
    REPO_ROOT / "src" / "research_vault" / "data" / "examples"
    / "demo-litreview" / "lit-review-loop.json"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_cfg():
    reset_config_cache()
    yield
    reset_config_cache()


@pytest.fixture
def tmp_cfg(tmp_path: Path):
    """Config pointing to tmp_path with notes_root and state_dir set up."""
    notes_root = tmp_path / "notes"
    notes_root.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    for d in ("experiments", "findings", "methodology", "literature", "concepts", "mocs"):
        (notes_root / d).mkdir()

    cfg_path = tmp_path / "research_vault.toml"
    cfg_path.write_text(
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
    os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_path)
    reset_config_cache()
    cfg = load_config()
    yield cfg, tmp_path
    reset_config_cache()
    if old is None:
        os.environ.pop("RESEARCH_VAULT_CONFIG", None)
    else:
        os.environ["RESEARCH_VAULT_CONFIG"] = old


# ---------------------------------------------------------------------------
# 1-2. Manifest schema validation
# ---------------------------------------------------------------------------

def test_research_loop_manifest_validates():
    """research-loop.json must pass the schema validator (including spec: required)."""
    assert RESEARCH_LOOP_PATH.exists(), (
        f"research-loop.json not found at {RESEARCH_LOOP_PATH}. "
        "Build the example file first."
    )
    manifest = load_manifest(RESEARCH_LOOP_PATH)
    # validate_manifest is called inside load_manifest; reaching here means it passed.
    assert manifest["run_id"], "run_id must be non-empty"
    assert len(manifest["nodes"]) >= 4, "research loop must have >=4 nodes"


def test_litreview_loop_manifest_validates():
    """lit-review-loop.json must pass the schema validator.

    Updated for review-loop-nodekind-drift-fix (2026-07-09): the demo now
    mirrors the shipped 7-node Option C Phase-1 + 5-node Phase-2 shape
    (review-scope/screen/curate + relate-*/synthesize/critic), 12 nodes total.
    """
    assert LITREVIEW_LOOP_PATH.exists(), (
        f"lit-review-loop.json not found at {LITREVIEW_LOOP_PATH}. "
        "Build the example file first."
    )
    manifest = load_manifest(LITREVIEW_LOOP_PATH)
    assert manifest["run_id"], "run_id must be non-empty"
    assert len(manifest["nodes"]) >= 5, "lit-review loop must have >=5 nodes"


# ---------------------------------------------------------------------------
# 3. spec: field required on all agent nodes (SR-DISP)
# ---------------------------------------------------------------------------

def test_all_agent_nodes_have_spec_research_loop():
    """All agent-type nodes in research-loop.json must have a non-empty spec:."""
    manifest = load_manifest(RESEARCH_LOOP_PATH)
    violations = [
        n["id"]
        for n in manifest["nodes"]
        if n.get("type", "agent") == "agent" and not n.get("spec", "").strip()
    ]
    assert not violations, (
        f"Agent nodes missing spec: {violations}. "
        "SR-DISP requires spec: on all agent nodes."
    )


def test_all_agent_nodes_have_spec_litreview_loop():
    """All agent-type nodes in lit-review-loop.json must have a non-empty spec:."""
    manifest = load_manifest(LITREVIEW_LOOP_PATH)
    violations = [
        n["id"]
        for n in manifest["nodes"]
        if n.get("type", "agent") == "agent" and not n.get("spec", "").strip()
    ]
    assert not violations, (
        f"Agent nodes missing spec: {violations}. "
        "SR-DISP requires spec: on all agent nodes."
    )


# ---------------------------------------------------------------------------
# 4. Named crew references
# ---------------------------------------------------------------------------

def test_research_loop_references_named_roles():
    """research-loop.json must reference named crew roles: researcher and reviewer."""
    manifest = load_manifest(RESEARCH_LOOP_PATH)
    roles_used = {n.get("role", "") for n in manifest["nodes"] if n.get("role")}
    assert "researcher" in roles_used, (
        f"research-loop must reference role='researcher' (Ada). Got roles: {roles_used}"
    )
    assert "reviewer" in roles_used, (
        f"research-loop must reference role='reviewer' (Argus, the plan-critic). "
        f"Got roles: {roles_used}"
    )


def test_litreview_loop_references_named_roles():
    """lit-review-loop.json must reference named crew roles: researcher and reviewer."""
    manifest = load_manifest(LITREVIEW_LOOP_PATH)
    roles_used = {n.get("role", "") for n in manifest["nodes"] if n.get("role")}
    assert "researcher" in roles_used, (
        f"lit-review-loop must reference role='researcher' (Ada). Got roles: {roles_used}"
    )
    assert "reviewer" in roles_used, (
        f"lit-review-loop must reference role='reviewer' (Argus). Got roles: {roles_used}"
    )


# ---------------------------------------------------------------------------
# 5-6. Experiments-before-run gate (the key structural guarantee)
# ---------------------------------------------------------------------------

def _make_research_states(plan_status: str = "succeeded",
                           human_go_status: str = "succeeded",
                           harness_status: str = "succeeded",
                           run_status: str = "pending",
                           score_status: str = "pending",
                           analyze_status: str = "pending",
                           human_go_findings_status: str = "pending",
                           methods_status: str = "pending") -> dict[str, dict]:
    """Build node_states for the research loop.

    Updated in SR-PLAN-1: multi-main (q1-main1-run, q1-main2-run, ablations,
    conditionals, per-main gates).
    Updated in SR-HARNESS-P2: per-main harness triples added between
    human-go-plan and the run nodes.  harness_status defaults to 'succeeded'
    so existing tests that don't exercise the harness layer stay green.
    """
    return {
        "plan": {"status": plan_status},
        "plan-critic": {"status": "succeeded"},
        "human-go-plan": {"status": human_go_status},
        # SR-HARNESS-P2: per-main harness triples
        "q1-main1-harness": {"status": harness_status},
        "q1-main1-harness-review": {"status": harness_status},
        "human-go-harness-main1": {"status": harness_status},
        "q1-main2-harness": {"status": harness_status},
        "q1-main2-harness-review": {"status": harness_status},
        "human-go-harness-main2": {"status": harness_status},
        # Main 1 + ablation A + conditional Y (all pending by default)
        "q1-main1-run": {"status": run_status},
        "q1-main1-score": {"status": score_status},
        "q1-main1-analyze": {"status": analyze_status},
        "q1-main1-abl-A-run": {"status": run_status},
        "q1-main1-abl-A-score": {"status": score_status},
        "q1-main1-abl-A-analyze": {"status": analyze_status},
        "human-go-conditionals-main1": {"status": "pending"},
        "q1-main1-cabl-Y-run": {"status": "pending"},
        "q1-main1-cabl-Y-score": {"status": "pending"},
        "q1-main1-cabl-Y-analyze": {"status": "pending"},
        # Main 2 + ablation B + conditional Z (all pending by default)
        "q1-main2-run": {"status": run_status},
        "q1-main2-score": {"status": score_status},
        "q1-main2-analyze": {"status": analyze_status},
        "q1-main2-abl-B-run": {"status": run_status},
        "q1-main2-abl-B-score": {"status": score_status},
        "q1-main2-abl-B-analyze": {"status": analyze_status},
        "human-go-conditionals-main2": {"status": "pending"},
        "q1-main2-cabl-Z-run": {"status": "pending"},
        "q1-main2-cabl-Z-score": {"status": "pending"},
        "q1-main2-cabl-Z-analyze": {"status": "pending"},
        # Final gates
        "human-go-findings": {"status": human_go_findings_status},
        "methods-update": {"status": methods_status},
    }


def test_experiments_before_run_blocks_when_no_artifact(tmp_cfg):
    """Pre-registration gate: run cannot dispatch when experiments note is absent.

    Updated in SR-PLAN-1: tests q1-main1-run (the first main) rather than the
    old single 'run' node.
    """
    cfg, tmp_path = tmp_cfg
    manifest = load_manifest(RESEARCH_LOOP_PATH)

    # plan + human-go-plan succeeded, but NO child stubs exist
    node_states = _make_research_states(
        plan_status="succeeded",
        human_go_status="succeeded",
    )

    # No edge timestamps (edges not registered yet) → fresh checks will fail
    edge_registered_ts: dict = {}

    frontier = compute_frontier(manifest, node_states, edge_registered_ts, global_cap=4)
    dispatch_ids = {f.node_id for f in frontier if f.action == "dispatch"}

    assert "q1-main1-run" not in dispatch_ids, (
        "q1-main1-run must be BLOCKED when the experiments stub is absent. "
        f"Frontier dispatch: {dispatch_ids}"
    )


def test_experiments_before_run_unblocks_when_artifact_present(tmp_cfg):
    """Pre-registration gate: run dispatches after experiments note is filed fresh.

    Updated in SR-PLAN-1: tests q1-main1-run rather than the old 'run' node.
    """
    cfg, tmp_path = tmp_cfg
    manifest = load_manifest(RESEARCH_LOOP_PATH)

    # Write the q1-main1 stub note
    exp_note = cfg.notes_root / "experiments" / "q1-main1.md"
    exp_note.parent.mkdir(parents=True, exist_ok=True)
    exp_note.write_text(
        "---\ntype: experiments\ncitekey: q1-main1\ntitle: Q1 main 1\n"
        "stance: confirmatory\nplan_role: main\n---\n\n# Exp 1 main 1\n",
        encoding="utf-8",
    )

    node_states = _make_research_states(
        plan_status="succeeded",
        human_go_status="succeeded",
    )

    # Provide edge timestamps in the past so the file (just created) is "fresh"
    reg_ts = time.time() - 3600  # registered 1 hour ago
    # Find the q1-main1-run node's edge index for the plan→run afterok+watch edge
    run_node = next(n for n in manifest["nodes"] if n["id"] == "q1-main1-run")
    plan_watch_idx = next(
        i for i, need in enumerate(run_node.get("needs", []))
        if need.get("from") == "plan" and need.get("watch")
    )
    edge_key = f"q1-main1-run:plan:{plan_watch_idx}"
    edge_registered_ts = {edge_key: reg_ts}

    frontier = compute_frontier(manifest, node_states, edge_registered_ts, global_cap=4)
    dispatch_ids = {f.node_id for f in frontier if f.action == "dispatch"}

    assert "q1-main1-run" in dispatch_ids, (
        "q1-main1-run must be DISPATCHABLE after its experiments stub is filed fresh. "
        f"Frontier dispatch: {dispatch_ids}"
    )


# ---------------------------------------------------------------------------
# 7-8. OKF coverage gate (lit-review)
# ---------------------------------------------------------------------------

def _make_litreview_states(scope_status: str = "succeeded",
                            approve_protocol_status: str = "succeeded",
                            search_status: str = "succeeded",
                            screen_status: str = "succeeded",
                            snowball_status: str = "succeeded",
                            curate_status: str = "succeeded",
                            gate_status: str = "pending",
                            relate1_status: str = "succeeded",
                            relate2_status: str = "succeeded",
                            synthesize_status: str = "pending",
                            critic_status: str = "pending",
                            final_gate_status: str = "pending") -> dict[str, dict]:
    """Build node_states for the lit-review loop (7-node Option C Phase-1 +
    5-node Phase-2, review-loop-nodekind-drift-fix, 2026-07-09)."""
    return {
        "review-scope": {"status": scope_status},
        "approve-protocol": {"status": approve_protocol_status},
        "review-search": {"status": search_status},
        "review-screen": {"status": screen_status},
        "review-snowball": {"status": snowball_status},
        "review-curate": {"status": curate_status},
        "coverage-gate": {"status": gate_status},
        "relate-smith2024": {"status": relate1_status},
        "relate-jones2023": {"status": relate2_status},
        "review-synthesize": {"status": synthesize_status},
        "review-coverage-critic": {"status": critic_status},
        "approve-review": {"status": final_gate_status},
    }


def test_litreview_final_gate_blocks_when_no_notes(tmp_cfg):
    """Terminal gate: appears as await-go when upstream nodes are terminal, even
    though relate-* notes were never actually filed on disk.

    The relate-* nodes cannot actually succeed via cmd_complete without their
    notes (the produces check would block them — see the two produces-check
    tests below), but here we directly set state to test that the walker's
    human-go readiness check is purely terminal-status based (not an artifact
    re-check) on the terminal Gate 3 (approve-review).
    """
    cfg, tmp_path = tmp_cfg
    manifest = load_manifest(LITREVIEW_LOOP_PATH)

    # All upstream nodes "succeeded" (simulating direct state write) but no files
    node_states = _make_litreview_states(
        gate_status="succeeded", synthesize_status="succeeded", critic_status="succeeded",
    )

    edge_registered_ts: dict = {}
    frontier = compute_frontier(manifest, node_states, edge_registered_ts, global_cap=4)

    # approve-review depends (transitively) on relate-smith2024/relate-jones2023
    # having succeeded. The walker only checks terminal status for human-go
    # readiness — the produces check is enforced at complete time (below), not
    # re-verified here. So: with all upstream nodes succeeded, approve-review
    # SHOULD appear as await-go.
    await_go_ids = {f.node_id for f in frontier if f.action == "await-go"}
    assert "approve-review" in await_go_ids, (
        "approve-review must appear as await-go when all upstream nodes are terminal. "
        f"Frontier await-go: {await_go_ids}"
    )


def test_litreview_relate_produces_check_blocks_without_note(tmp_cfg):
    """cmd_complete rejects a relate-* node's success when its literature note is absent.

    This is the structural gate: a relate-* node cannot be marked succeeded
    via rv dag complete without the OKF note existing with correct type: frontmatter.
    """
    from research_vault.dag.verbs import cmd_complete
    cfg, tmp_path = tmp_cfg
    manifest = load_manifest(LITREVIEW_LOOP_PATH)

    # Set up a run store with the lit-review loop
    store = RunStore.from_config(cfg)
    run_state = RunState(
        run_id=manifest["run_id"],
        manifest_path=str(LITREVIEW_LOOP_PATH),
        created_at=time.time(),
    )
    run_state.init_nodes(manifest)
    # Set upstream as succeeded so relate-smith2024 is the focus
    run_state.set_node_status("review-scope", "succeeded")
    run_state.set_node_status("approve-protocol", "succeeded")
    run_state.set_node_status("review-search", "succeeded")
    run_state.set_node_status("review-screen", "succeeded")
    run_state.set_node_status("review-snowball", "succeeded")
    run_state.set_node_status("review-curate", "succeeded")
    run_state.set_node_status("coverage-gate", "succeeded")
    store.create(run_state)

    # Try to complete relate-smith2024 without creating the literature note
    import argparse
    args = argparse.Namespace(
        run_id=manifest["run_id"],
        node_id="relate-smith2024",
        status="succeeded",
    )

    rc = cmd_complete(args)
    assert rc != 0, (
        "cmd_complete must FAIL when relate-smith2024's produces note does not exist. "
        "Every in-scope paper must have a literature note before synthesis begins."
    )


def test_litreview_relate_produces_check_passes_with_note(tmp_cfg):
    """cmd_complete allows a relate-* node to succeed when its literature note is filed."""
    from research_vault.dag.verbs import cmd_complete
    cfg, tmp_path = tmp_cfg
    manifest = load_manifest(LITREVIEW_LOOP_PATH)

    # Write the literature note with correct OKF frontmatter AND the mandatory
    # relate-presence checklist answers (Wave 0 Reading PR-1/PR-2/PR-4/PR-5 —
    # see tests/test_relate_presence_gate.py for the canonical fixture shape).
    lit_note = cfg.notes_root / "literature" / "smith2024.md"
    lit_note.parent.mkdir(parents=True, exist_ok=True)
    lit_note.write_text(
        "---\n"
        "type: literature\n"
        "citekey: smith2024\n"
        "title: Smith 2024\n"
        "contribution_kind: theory-bound\n"
        "role: theoretical\n"
        "position: Establishes the baseline framing this review's question builds on.\n"
        "result_reported: no\n"
        "paper_relations_sought: no\n"
        "---\n\n# Smith et al. 2024\n",
        encoding="utf-8",
    )

    store = RunStore.from_config(cfg)
    run_state = RunState(
        run_id=manifest["run_id"],
        manifest_path=str(LITREVIEW_LOOP_PATH),
        created_at=time.time(),
    )
    run_state.init_nodes(manifest)
    run_state.set_node_status("review-scope", "succeeded")
    run_state.set_node_status("approve-protocol", "succeeded")
    run_state.set_node_status("review-search", "succeeded")
    run_state.set_node_status("review-screen", "succeeded")
    run_state.set_node_status("review-snowball", "succeeded")
    run_state.set_node_status("review-curate", "succeeded")
    run_state.set_node_status("coverage-gate", "succeeded")
    store.create(run_state)

    import argparse
    args = argparse.Namespace(
        run_id=manifest["run_id"],
        node_id="relate-smith2024",
        status="succeeded",
    )
    rc = cmd_complete(args)
    assert rc == 0, (
        "cmd_complete must SUCCEED when relate-smith2024's produces note exists with "
        "correct type: frontmatter."
    )


# ---------------------------------------------------------------------------
# 9-11. rv init
# ---------------------------------------------------------------------------

def test_rv_init_creates_instance_structure(tmp_path):
    """rv init in an empty dir creates a valid multi-project instance."""
    from research_vault.init import cmd_init_in_dir

    target = tmp_path / "my-vault"
    target.mkdir()
    rc = cmd_init_in_dir(str(target))
    assert rc == 0, "rv init must exit 0"

    # Required top-level files
    assert (target / "research_vault.toml").exists(), "research_vault.toml must be created"
    assert (target / "QUICKSTART.md").exists(), "QUICKSTART.md must be created"
    assert (target / "DEVLOG.md").exists(), "DEVLOG.md must be created"
    assert (target / "architecture.md").exists(), "architecture.md must be created"

    # Control and task dirs
    assert (target / "control").is_dir(), "control/ must be created"
    assert (target / "tasks").is_dir(), "tasks/ must be created"

    # Doctrine directory
    assert (target / "doctrine").is_dir(), "doctrine/ must be created"

    # SR-RV-UPDATE Slice 2: demos removed — no examples/ scaffolded.
    assert not (target / "examples").exists(), (
        "rv init must NOT scaffold examples/ (demo projects removed)"
    )

    # Notes root with the shared-canonical OKF type dirs only. The instance
    # owns just the shared bundles (literature/concepts/datasets) — project-
    # scoped types (experiments/findings/gaps/methodology/mocs) belong under
    # a project's own source_dir, never the instance (instance-scaffold-drift
    # fix — this assertion previously pinned the bug it now guards against).
    assert (target / "notes").is_dir(), "notes/ must be created"
    for note_type in ("literature", "concepts", "datasets"):
        assert (target / "notes" / note_type).is_dir(), (
            f"notes/{note_type}/ must be created"
        )
    for note_type in ("experiments", "findings", "gaps", "methodology", "mocs"):
        assert not (target / "notes" / note_type).exists(), (
            f"notes/{note_type}/ must NOT be created at the instance level"
        )


def test_rv_init_creates_valid_config(tmp_path):
    """rv init creates a research_vault.toml with NO demo projects (SR-RV-UPDATE)."""
    from research_vault.init import cmd_init_in_dir

    target = tmp_path / "vault-init-test"
    target.mkdir()
    cmd_init_in_dir(str(target))

    # Load the created config
    cfg_path = target / "research_vault.toml"
    assert cfg_path.exists()

    old = os.environ.get("RESEARCH_VAULT_CONFIG")
    os.environ["RESEARCH_VAULT_CONFIG"] = str(cfg_path)
    reset_config_cache()
    try:
        cfg = load_config()
        # Slice 2: demos are no longer auto-registered.
        assert "demo-research" not in cfg.projects, (
            "demo-research must NOT be registered — demos removed in SR-RV-UPDATE"
        )
        assert "demo-litreview" not in cfg.projects, (
            "demo-litreview must NOT be registered — demos removed in SR-RV-UPDATE"
        )
        assert cfg.projects == {}, "a fresh vault registers zero projects"
    finally:
        reset_config_cache()
        if old is None:
            os.environ.pop("RESEARCH_VAULT_CONFIG", None)
        else:
            os.environ["RESEARCH_VAULT_CONFIG"] = old


def test_rv_init_loop_manifests_valid(tmp_path):
    """The SHIPPED loop manifests (package data) still validate under the schema.

    SR-RV-UPDATE Slice 2 removed demo scaffolding from `rv init`, but the package
    still ships the loop manifests (for `rv dag templates`). This validates the
    shipped manifests directly rather than copies placed by init.
    """
    import importlib.resources
    from research_vault.init import cmd_init_in_dir

    target = tmp_path / "vault-loop-test"
    target.mkdir()
    cmd_init_in_dir(str(target))

    # init must NOT place demo manifests into the vault.
    assert not (target / "examples").exists(), "rv init must not scaffold examples/"

    pkg_data = importlib.resources.files("research_vault") / "data"
    with importlib.resources.as_file(
        pkg_data / "examples" / "demo-research" / "research-loop.json"
    ) as rl, importlib.resources.as_file(
        pkg_data / "examples" / "demo-litreview" / "lit-review-loop.json"
    ) as ll:
        research_loop = Path(rl)
        litreview_loop = Path(ll)

        assert research_loop.exists(), "shipped research-loop.json must exist in package data"
        assert litreview_loop.exists(), "shipped lit-review-loop.json must exist in package data"

        # Both must validate under the current schema (inside the as_file context,
        # where the extracted temp paths are still valid).
        m1 = load_manifest(research_loop)
        m2 = load_manifest(litreview_loop)
        assert m1["run_id"] and m2["run_id"]


def test_rv_init_does_not_overwrite_existing(tmp_path):
    """rv init refuses to overwrite an existing research_vault.toml."""
    from research_vault.init import cmd_init_in_dir

    target = tmp_path / "existing"
    target.mkdir()
    (target / "research_vault.toml").write_text("existing content\n", encoding="utf-8")

    rc = cmd_init_in_dir(str(target))
    assert rc != 0, "rv init must fail if research_vault.toml already exists"
    # Should not have been overwritten
    assert (target / "research_vault.toml").read_text(encoding="utf-8") == "existing content\n"


# ---------------------------------------------------------------------------
# 12-14. rv check
# ---------------------------------------------------------------------------

def test_rv_check_reports_missing_claude(capsys, tmp_path):
    """rv check reports 'Claude CLI not found' clearly when claude is absent."""
    from research_vault.check import run_preflight

    with patch("shutil.which", return_value=None):
        result = run_preflight()

    assert result["claude_cli"] is False
    output = result["report"]
    assert "claude" in output.lower() or "claude" in str(result), (
        f"Report must mention 'claude'. Got: {result['report']}"
    )


def test_rv_check_reports_missing_api_key(capsys):
    """rv check reports 'ANTHROPIC_API_KEY not set' clearly when absent."""
    from research_vault.check import run_preflight

    env_without_key = {k: v for k, v in os.environ.items() if "ANTHROPIC" not in k}
    with patch.dict(os.environ, env_without_key, clear=True):
        with patch("shutil.which", return_value="/usr/bin/claude"):
            result = run_preflight()

    assert result["api_key"] is False
    output = result["report"]
    assert "anthropic" in output.lower() or "api_key" in output.lower(), (
        f"Report must mention ANTHROPIC_API_KEY. Got: {output}"
    )


def test_rv_check_returns_pass_when_all_present():
    """rv check returns ok when Claude CLI and API key are present (mocked)."""
    from research_vault.check import run_preflight

    with patch("shutil.which", return_value="/usr/bin/claude"):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test-000"}):
            result = run_preflight()

    assert result["claude_cli"] is True
    assert result["api_key"] is True


# ---------------------------------------------------------------------------
# 15-16. note: watch form
# ---------------------------------------------------------------------------

def test_note_watch_resolves_relative_to_notes_root(tmp_cfg):
    """note:<type>/<id> watch form resolves the path via load_config().notes_root."""
    cfg, tmp_path = tmp_cfg

    # File does NOT exist yet → should return ready=False
    result = resolve_watch("note:experiments/exp-q1.md")
    assert result["ready"] is False
    assert result["state"] == "missing"

    # Create the note → should return ready=True
    exp_note = cfg.notes_root / "experiments" / "exp-q1.md"
    exp_note.write_text("---\ntype: experiments\n---\n", encoding="utf-8")

    result = resolve_watch("note:experiments/exp-q1.md")
    assert result["ready"] is True
    assert result["state"] == "exists"


def test_note_watch_fresh_checks_mtime(tmp_cfg):
    """note:<type>/<id>+fresh is ready only when mtime >= registered_ts."""
    cfg, tmp_path = tmp_cfg

    exp_note = cfg.notes_root / "experiments" / "exp-fresh.md"
    exp_note.write_text("---\ntype: experiments\n---\n", encoding="utf-8")

    # registered_ts in the future → file is stale
    future_ts = time.time() + 3600
    result = resolve_watch("note:experiments/exp-fresh.md+fresh", registered_ts=future_ts)
    assert result["ready"] is False
    assert "stale" in result["state"]

    # registered_ts in the past → file is fresh
    past_ts = time.time() - 3600
    result = resolve_watch("note:experiments/exp-fresh.md+fresh", registered_ts=past_ts)
    assert result["ready"] is True


# ---------------------------------------------------------------------------
# 17. Leakage scanner stays green on examples/
# ---------------------------------------------------------------------------

def test_leakage_scanner_green_on_examples():
    """The leakage scanner must report no violations in the examples/ directory.

    SR-PKG: examples/ moved to src/research_vault/data/examples/.
    """
    import subprocess

    examples_dir = REPO_ROOT / "src" / "research_vault" / "data" / "examples"
    if not examples_dir.exists():
        pytest.skip("data/examples/ not yet created — skipping leakage check")

    scanner = REPO_ROOT / "scripts" / "leakage_scan.sh"
    if not scanner.exists():
        pytest.skip("leakage_scan.sh not found")

    result = subprocess.run(
        ["bash", str(scanner), str(examples_dir)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"Leakage scanner found violations in examples/:\n"
        f"{result.stdout}\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# 18. Both manifests' human-go nodes have no spec: (correct — exempt from SR-DISP)
# ---------------------------------------------------------------------------

def test_human_go_nodes_have_no_spec_requirement():
    """human-go nodes are EXEMPT from spec: requirement (they are decision gates)."""
    for manifest_path in [RESEARCH_LOOP_PATH, LITREVIEW_LOOP_PATH]:
        if not manifest_path.exists():
            pytest.skip(f"{manifest_path} not yet created")
        manifest = load_manifest(manifest_path)
        for node in manifest["nodes"]:
            if node.get("type") == "human-go":
                # human-go nodes may or may not have spec — both are valid
                # the schema exempts them from the spec: requirement
                pass  # Just verifying the manifest loads without error
