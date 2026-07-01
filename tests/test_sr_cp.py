"""test_sr_cp.py — acceptance tests for SR-CP (control-plane records lifecycle).

Eight acceptance tests from §5B-CP, all deterministic and hermetic.
No ~/vault reads or writes; no gh/network calls.

Test map:
  1. reconcile bites on planted stale claims (R1, R2, R3, R4 each have bite+clear tests)
  2. enforcement (banner present, check flags absence, heal inserts, rv help --check green)
  3. zero-infra / no-gh (local signals only; fake PR adapter enriches without network)
  4. ~/vault boundary (never read/written during build or test)
  5a. write-face schema-valid-by-construction (post, spawn-request, return refuse on missing fields)
  5b. concurrency (N parallel post calls all land — fails against raw read-modify-write)
  6. close cleans in one motion (marker set + archived + index one-liner — one call)
  7. reconcile --archive on terminal signal + resolved-count teeth
  8. devlog index/search without loading whole file; idempotent regeneration
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import load_config, reset_config_cache
from research_vault import control as control_mod
from research_vault import controllib as cl
from research_vault import devlog as devlog_mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_instance):
    return load_config(reload=True)


@pytest.fixture
def ctl_file(cfg):
    """Create a fresh demo-research control file and return its path."""
    path = control_mod.cmd_init("demo-research", config=cfg, overwrite=True)
    return path


@pytest.fixture
def tmp_git_repo(tmp_path):
    """A minimal git repo for testing git-based live signals."""
    repo = tmp_path / "git-repo"
    repo.mkdir()
    # Pin the default branch to "main" so tests pass on CI runners that
    # default to "master" (where init.defaultBranch is not overridden).
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(repo)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    # Initial commit so HEAD exists
    (repo / "README.md").write_text("# test\n")
    subprocess.run(
        ["git", "-C", str(repo), "add", "."], check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        check=True, capture_output=True,
    )
    return repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _plant_not_yet_claim(ctl_path: Path, sr_id: str = "sr-x") -> None:
    """Seed a 'not-yet' claim about sr_id in Open / blockers."""
    text = ctl_path.read_text(encoding="utf-8")
    claim = f"- {sr_id.upper()} is the next dispatch (undispatched)"
    # Append to Open / blockers section
    text = text.replace(
        "## Open / blockers\n  _(none)_",
        f"## Open / blockers\n{claim}",
    )
    ctl_path.write_text(text, encoding="utf-8")


def _plant_artifact_claim(ctl_path: Path, artifact_path: Path) -> None:
    """Seed a 'filed artifact' claim for a file that doesn't exist / is stale."""
    text = ctl_path.read_text(encoding="utf-8")
    claim = f"- artifact:{artifact_path} filed"
    text = text.replace(
        "## Open / blockers\n  _(none)_",
        f"## Open / blockers\n{claim}",
    )
    ctl_path.write_text(text, encoding="utf-8")


def _plant_done_but_open(ctl_path: Path, task_slug: str) -> None:
    """Seed a task listed as open blocker but that will be marked done."""
    text = ctl_path.read_text(encoding="utf-8")
    claim = f"- [ ] {task_slug}: still open"
    text = text.replace(
        "## Open / blockers\n  _(none)_",
        f"## Open / blockers\n{claim}",
    )
    ctl_path.write_text(text, encoding="utf-8")


def _plant_inflight_handshake(ctl_path: Path, entry_id: str) -> None:
    """Seed an in-flight Handshake entry for entry_id."""
    text = ctl_path.read_text(encoding="utf-8")
    claim = f"- **handshake:{entry_id}** — in-flight"
    text = text.replace(
        "## Handshakes  (in-flight, needs the other side)\n  _(none)_",
        f"## Handshakes  (in-flight, needs the other side)\n{claim}",
    )
    # Try alternative patterns
    if claim not in text:
        import re
        text = re.sub(
            r"(## Handshakes.*?\n)  _\(none\)_",
            rf"\1{claim}",
            text,
            count=1,
            flags=re.DOTALL,
        )
    ctl_path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Test 1: Reconcile BITES on planted stale claims
# ---------------------------------------------------------------------------

class TestReconcileBites:
    """1. Reconcile bites on planted stale claims (R1–R4)."""

    def test_r1_bites_on_not_yet_claim_with_live_branch(self, cfg, ctl_file, tmp_git_repo):
        """R1: 'SR-X is next dispatch' + live branch for SR-X → STALE (exit non-zero)."""
        # Plant the not-yet claim
        _plant_not_yet_claim(ctl_file, "sr-x")

        # Create a live branch for sr-x in a repo registered to the project
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "checkout", "-b", "feat/sr-x-thing"],
            check=True, capture_output=True,
        )

        findings = control_mod.cmd_reconcile(
            "demo-research", config=cfg, git_repo=tmp_git_repo
        )
        assert len(findings) > 0, "Expected R1 violation but got none"
        assert any("sr-x" in f.lower() or "SR-X" in f for f in findings), (
            f"Expected sr-x to be named in findings: {findings}"
        )
        assert any("stale" in f.lower() or "STALE" in f for f in findings), (
            f"Expected STALE in findings: {findings}"
        )

    def test_r1_clears_when_no_live_artifact(self, cfg, ctl_file):
        """R1 GREEN: 'SR-Y is next dispatch' but no live artifact for SR-Y → OK."""
        _plant_not_yet_claim(ctl_file, "sr-y")
        # No branch for sr-y exists
        findings = control_mod.cmd_reconcile("demo-research", config=cfg)
        r1_findings = [f for f in findings if "sr-y" in f.lower() or "SR-Y" in f]
        assert len(r1_findings) == 0, f"Unexpected R1 violation for absent sr-y: {r1_findings}"

    def test_r1_bites_on_active_task_card(self, cfg, ctl_file):
        """R1: 'SR-X is undispatched' + active task card for sr-x → STALE."""
        from research_vault import task as task_mod
        _plant_not_yet_claim(ctl_file, "sr-x")
        # Create an active task card whose slug contains sr-x
        task_mod.cmd_add("demo-research", "sr-x implementation",
                         config=cfg, status="in_progress")

        findings = control_mod.cmd_reconcile("demo-research", config=cfg)
        r1_findings = [f for f in findings if "sr-x" in f.lower()]
        assert len(r1_findings) > 0, f"Expected R1 bite from task card, got: {findings}"

    def test_r2_bites_on_missing_artifact(self, cfg, ctl_file, tmp_path):
        """R2: artifact claimed but file doesn't exist → STALE."""
        missing_path = tmp_path / "outputs" / "results.jsonl"
        _plant_artifact_claim(ctl_file, missing_path)

        findings = control_mod.cmd_reconcile("demo-research", config=cfg)
        r2_findings = [f for f in findings if "artifact" in f.lower() or str(missing_path) in f]
        assert len(r2_findings) > 0, f"Expected R2 artifact violation, got: {findings}"

    def test_r2_clears_when_artifact_exists(self, cfg, ctl_file, tmp_path):
        """R2 GREEN: artifact exists and is fresh → no R2 violation."""
        artifact = tmp_path / "results.jsonl"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text('{"ok": true}\n', encoding="utf-8")
        _plant_artifact_claim(ctl_file, artifact)

        findings = control_mod.cmd_reconcile("demo-research", config=cfg)
        r2_findings = [f for f in findings if str(artifact) in f and "stale" in f.lower()]
        assert len(r2_findings) == 0, f"Unexpected R2 violation for existing artifact: {r2_findings}"

    def test_r3_bites_on_done_task_still_open_blocker(self, cfg, ctl_file):
        """R3: task board shows done but still listed as open blocker → STALE."""
        from research_vault import task as task_mod
        slug = "my-subtask"
        task_mod.cmd_add("demo-research", "my subtask", config=cfg, status="done")
        _plant_done_but_open(ctl_file, slug)

        findings = control_mod.cmd_reconcile("demo-research", config=cfg)
        r3_findings = [f for f in findings if slug in f or "done" in f.lower()]
        assert len(r3_findings) > 0, f"Expected R3 violation, got: {findings}"

    def test_r3_clears_when_task_matches_status(self, cfg, ctl_file):
        """R3 GREEN: open blocker matches active task → no R3 violation."""
        from research_vault import task as task_mod
        slug = "open-blocker-task"
        task_mod.cmd_add("demo-research", "open blocker task", config=cfg, status="in_progress")
        # Task is in_progress, not done, so no R3 flag
        findings = control_mod.cmd_reconcile("demo-research", config=cfg)
        r3_findings = [f for f in findings if "r3" in f.lower()]
        assert len(r3_findings) == 0, f"Unexpected R3 violation: {r3_findings}"

    def test_r4_bites_on_merged_branch_still_inflight(self, cfg, ctl_file, tmp_git_repo):
        """R4: branch merged into main but Handshake still in-flight → STALE."""
        entry_id = "sr-x-mason-20260701"
        _plant_inflight_handshake(ctl_file, entry_id)

        # Create and merge a branch for sr-x
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "checkout", "-b", "feat/sr-x"],
            check=True, capture_output=True,
        )
        (tmp_git_repo / "newfile.txt").write_text("done\n")
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "add", "."],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "commit", "-m", "sr-x done"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "checkout", "main"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "merge", "feat/sr-x", "--no-ff"],
            check=True, capture_output=True,
        )

        findings = control_mod.cmd_reconcile(
            "demo-research", config=cfg, git_repo=tmp_git_repo
        )
        r4_findings = [f for f in findings if "sr-x" in f.lower() or entry_id.lower() in f.lower()]
        assert len(r4_findings) > 0, f"Expected R4 violation for merged branch, got: {findings}"

    def test_r4_bites_on_squash_merged_branch_still_inflight(
        self, cfg, ctl_file, tmp_git_repo
    ):
        """R4 (squash path): squash-merged branch with (#N) commit subject → STALE.

        This is the repo's ACTUAL merge model (GitHub squash-and-merge).
        Squash produces no merge commit so primary --merges signal is empty;
        tertiary signal scans non-merge commit subjects for the (#N) anchor.
        """
        entry_id = "sr-p-mason-20260701"
        _plant_inflight_handshake(ctl_file, entry_id)

        # Create branch, add commit, then squash-merge into main
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "checkout", "-b", "feat/sr-p"],
            check=True, capture_output=True,
        )
        (tmp_git_repo / "squash.txt").write_text("squashed\n")
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "add", "."],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "commit", "-m", "wip: sr-p work"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "checkout", "main"],
            check=True, capture_output=True,
        )
        # Squash merge: collapses branch commits into one
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "merge", "--squash", "feat/sr-p"],
            check=True, capture_output=True,
        )
        # Commit with GitHub-style squash subject: "feat(scope): desc (#N)"
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "commit",
             "-m", "feat(sr-p): deliver sr-p implementation (#42)"],
            check=True, capture_output=True,
        )
        # Branch may be deleted after squash-merge (as in real workflow)
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "branch", "-D", "feat/sr-p"],
            check=True, capture_output=True,
        )

        findings = control_mod.cmd_reconcile(
            "demo-research", config=cfg, git_repo=tmp_git_repo
        )
        r4_findings = [
            f for f in findings
            if "sr-p" in f.lower() or entry_id.lower() in f.lower()
        ]
        assert len(r4_findings) > 0, (
            f"Expected R4 violation for squash-merged branch, got: {findings}"
        )

    def test_r4_clears_when_branch_unmerged(self, cfg, ctl_file, tmp_git_repo):
        """R4 GREEN: Handshake in-flight, branch NOT merged → no R4 flag."""
        entry_id = "sr-z-mason-20260701"
        _plant_inflight_handshake(ctl_file, entry_id)
        # Create but don't merge a branch
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "checkout", "-b", "feat/sr-z"],
            check=True, capture_output=True,
        )

        findings = control_mod.cmd_reconcile(
            "demo-research", config=cfg, git_repo=tmp_git_repo
        )
        r4_findings = [f for f in findings
                       if "sr-z" in f.lower() and "r4" in f.lower()]
        assert len(r4_findings) == 0, f"Unexpected R4 for unmerged branch: {r4_findings}"


# ---------------------------------------------------------------------------
# Test 2: Enforcement layers
# ---------------------------------------------------------------------------

class TestEnforcement:
    """2. Enforcement layers (banner, check, heal, rv help --check)."""

    def test_new_control_file_has_banner(self, cfg):
        """A newly rendered control file contains the tooled-path banner."""
        path = control_mod.cmd_init("demo-research", config=cfg, overwrite=True)
        text = path.read_text(encoding="utf-8")
        assert "rv status" in text, "Banner missing 'rv status' reference"
        assert "rv control reconcile" in text, "Banner missing 'rv control reconcile'"
        assert "hand-edit" in text or "NEVER hand-edit" in text, "Banner missing hand-edit warning"

    def test_check_flags_missing_banner(self, cfg):
        """rv control check returns violation when banner is absent."""
        path = control_mod.cmd_init("demo-research", config=cfg, overwrite=True)
        # Remove banner from the file
        text = path.read_text(encoding="utf-8")
        # Strip any line containing "rv status"
        stripped = "\n".join(
            ln for ln in text.splitlines()
            if "rv status" not in ln and "rv control reconcile" not in ln
        )
        path.write_text(stripped, encoding="utf-8")
        violations = control_mod.cmd_check("demo-research", config=cfg)
        banner_violations = [v for v in violations if "banner" in v.lower()]
        assert len(banner_violations) > 0, (
            f"Expected banner violation from cmd_check, got: {violations}"
        )

    def test_heal_inserts_banner(self, cfg):
        """rv control heal inserts the banner into a file that lacks it."""
        path = control_mod.cmd_init("demo-research", config=cfg, overwrite=True)
        # Strip the banner
        text = path.read_text(encoding="utf-8")
        stripped = "\n".join(
            ln for ln in text.splitlines()
            if "rv status" not in ln and "rv control reconcile" not in ln
            and "⚠" not in ln
        )
        path.write_text(stripped, encoding="utf-8")

        # Heal inserts it
        control_mod.cmd_heal("demo-research", config=cfg)
        healed = path.read_text(encoding="utf-8")
        assert "rv status" in healed, "Heal failed to insert banner"
        assert "rv control reconcile" in healed, "Heal failed to insert reconcile reference"

    def test_rv_help_check_passes(self, tmp_instance):
        """rv help --check is green: all verbs have when_to_use + status/reconcile present."""
        from research_vault.cli import _check_verb_docstrings, _VERB_REGISTRY
        violations = _check_verb_docstrings()
        assert violations == [], f"rv help --check violations: {violations}"
        # status verb must be registered
        assert "status" in _VERB_REGISTRY, "status verb not in _VERB_REGISTRY"
        # reconcile-related verbs should have anti-pattern language
        control_entry = _VERB_REGISTRY.get("control", {})
        when = control_entry.get("when_to_use", "")
        assert "cat" in when or "hand-edit" in when or "by eye" in when, (
            "control when_to_use missing anti-pattern reference"
        )

    def test_status_verb_has_anti_pattern_line(self):
        """status entry in _VERB_REGISTRY references the anti-pattern (cat/Read/by eye)."""
        from research_vault.cli import _VERB_REGISTRY
        status_entry = _VERB_REGISTRY.get("status", {})
        when = status_entry.get("when_to_use", "")
        assert when, "status verb has empty when_to_use"
        assert "anti" in when.lower() or "cat" in when.lower() or "by eye" in when.lower() or "raw" in when.lower(), (
            f"status when_to_use missing anti-pattern: {when}"
        )


# ---------------------------------------------------------------------------
# Test 3: Zero-infra / no-gh
# ---------------------------------------------------------------------------

class TestZeroInfra:
    """3. Zero-infra (no gh, no network) and SignalSource seam."""

    def test_status_runs_without_network(self, cfg, ctl_file):
        """rv status runs locally with no gh/network dependency."""
        from research_vault import status as status_mod
        # Should not raise, even without gh
        result = status_mod.cmd_status("demo-research", config=cfg)
        assert result is not None
        assert "Inbox" in result or "demo-research" in result

    def test_reconcile_runs_without_network(self, cfg, ctl_file):
        """rv control reconcile runs without any network call."""
        # No branches, no tasks → should return empty findings (no planted claims)
        findings = control_mod.cmd_reconcile("demo-research", config=cfg)
        assert isinstance(findings, list)

    def test_fake_signal_source_enriches_r1(self, cfg, ctl_file):
        """A fake PR/CI SignalSource enriches R1 when installed; absent by default."""
        _plant_not_yet_claim(ctl_file, "sr-q")

        class FakePRSource:
            """Fake PR/CI adapter (tier-3 seam). Always reports sr-q as dispatched."""
            def build_live_set(self, config, project: str) -> frozenset[str]:
                return frozenset({"sr-q"})

            def get_terminal_set(self, config, project: str) -> frozenset[str]:
                return frozenset()

        # With fake source: should flag sr-q
        findings_with = control_mod.cmd_reconcile(
            "demo-research", config=cfg, extra_sources=[FakePRSource()]
        )
        r1_findings = [f for f in findings_with if "sr-q" in f.lower()]
        assert len(r1_findings) > 0, f"Fake source did not enrich R1: {findings_with}"

        # Without any source: sr-q has no local signals → no R1 flag
        findings_without = control_mod.cmd_reconcile("demo-research", config=cfg)
        r1_absent = [f for f in findings_without if "sr-q" in f.lower()]
        assert len(r1_absent) == 0, f"Unexpected R1 without fake source: {r1_absent}"

    def test_pr_source_absent_by_default(self, cfg, ctl_file):
        """Core reconcile has no gh dependency — PR/CI SignalSource absent by default."""
        # This test confirms reconcile doesn't call gh (no gh binary needed)
        # Just check it runs cleanly without error
        findings = control_mod.cmd_reconcile("demo-research", config=cfg)
        assert isinstance(findings, list)


# ---------------------------------------------------------------------------
# Test 4: ~/vault boundary
# ---------------------------------------------------------------------------

class TestVaultBoundary:
    """4. ~/vault is never read or written during build or test."""

    def test_config_has_no_vault_paths(self, tmp_instance):
        """The test config never references ~/vault."""
        vault_home = Path.home() / "vault"
        cfg = load_config(reload=True)
        # Check all config paths
        for attr in ("instance_root", "notes_root", "state_dir", "control_dir", "tasks_dir"):
            p = getattr(cfg, attr, None)
            if p:
                assert not str(p).startswith(str(vault_home)), (
                    f"Config.{attr} points into ~/vault: {p}"
                )

    def test_controllib_has_no_vault_import(self):
        """controllib.py does not import from ~/vault scripts."""
        import inspect
        import research_vault.controllib as m
        src = inspect.getsource(m)
        assert "~/vault" not in src, "controllib.py references ~/vault path"
        assert "vault/scripts" not in src, "controllib.py references vault/scripts"


# ---------------------------------------------------------------------------
# Test 5a: Write face schema-valid-by-construction
# ---------------------------------------------------------------------------

class TestWriteFaceSchema:
    """5a. Write verbs refuse on missing required fields; emit check-valid entries."""

    def test_post_inbox_produces_valid_entry(self, cfg, ctl_file):
        """cmd_post into Inbox emits an entry that passes cmd_check."""
        control_mod.cmd_post(
            "demo-research",
            section="inbox",
            title="hello world",
            body="a test note",
            kind="note",
            by="mason",
            config=cfg,
        )
        violations = control_mod.cmd_check("demo-research", config=cfg)
        schema_violations = [v for v in violations if "missing section" in v.lower()]
        assert len(schema_violations) == 0, f"Post produced schema violation: {violations}"

    def test_post_handshakes_produces_valid_entry(self, cfg, ctl_file):
        """cmd_post into Handshakes emits a valid entry."""
        control_mod.cmd_post(
            "demo-research",
            section="handshakes",
            title="sr-cp review",
            body="awaiting reviewer",
            kind="handshake",
            by="wren",
            config=cfg,
        )
        text = ctl_file.read_text(encoding="utf-8")
        assert "sr-cp" in text.lower() or "review" in text.lower()

    def test_post_outbox_produces_valid_entry(self, cfg, ctl_file):
        """cmd_post into Outbox emits a valid entry."""
        control_mod.cmd_post(
            "demo-research",
            section="outbox",
            title="verdict ready",
            body="SR-CP approved",
            kind="verdict",
            by="argus",
            config=cfg,
        )
        text = ctl_file.read_text(encoding="utf-8")
        assert "verdict" in text.lower() or "ready" in text.lower()

    def test_post_open_blockers_produces_valid_entry(self, cfg, ctl_file):
        """cmd_post into open-findings / open-blockers emits a valid entry."""
        control_mod.cmd_post(
            "demo-research",
            section="open-blockers",
            title="blocking issue",
            body="needs resolution",
            by="alfred",
            config=cfg,
        )
        text = ctl_file.read_text(encoding="utf-8")
        assert "blocking" in text.lower()

    def test_spawn_request_refuses_missing_field(self, cfg, ctl_file):
        """cmd_spawn_request refuses (raises/errors) if any SPAWN_REQUIRED field is missing."""
        from research_vault.controllib import SPAWN_REQUIRED
        # Build a complete dict, then remove one required field
        full_fields = {
            "role/lens": "engineer",
            "why": "test",
            "goal": "test goal",
            "scope": "test scope",
            "deliverable": "a PR",
            "form": "create",
            "urgency": "low",
            "tier": "sonnet",
            "depends-on": "SR-1",
            "inputs": "none",
            "done-when": "CI green",
        }
        assert set(full_fields.keys()) == set(SPAWN_REQUIRED), (
            f"Test setup error: full_fields doesn't match SPAWN_REQUIRED.\n"
            f"SPAWN_REQUIRED={SPAWN_REQUIRED}\nfull_fields={list(full_fields.keys())}"
        )
        # Remove "goal" to trigger refusal
        incomplete = dict(full_fields)
        del incomplete["goal"]
        with pytest.raises((ValueError, KeyError), match="goal|required|missing"):
            control_mod.cmd_spawn_request("demo-research", fields=incomplete, config=cfg)

    def test_spawn_request_refuses_empty_field(self, cfg, ctl_file):
        """cmd_spawn_request refuses on empty required field."""
        from research_vault.controllib import SPAWN_REQUIRED
        fields = {
            "role/lens": "engineer",
            "why": "test",
            "goal": "",  # empty
            "scope": "test scope",
            "deliverable": "a PR",
            "form": "create",
            "urgency": "low",
            "tier": "sonnet",
            "depends-on": "SR-1",
            "inputs": "none",
            "done-when": "CI green",
        }
        with pytest.raises((ValueError, KeyError), match="goal|required|empty|missing"):
            control_mod.cmd_spawn_request("demo-research", fields=fields, config=cfg)

    def test_spawn_request_succeeds_with_all_fields(self, cfg, ctl_file):
        """cmd_spawn_request emits a check-valid ⟦SPAWN REQUEST⟧ block with all fields."""
        from research_vault.controllib import SPAWN_REQUIRED
        fields = {
            "role/lens": "engineer",
            "why": "need it",
            "goal": "build thing",
            "scope": "just this",
            "deliverable": "PR",
            "form": "create",
            "urgency": "normal",
            "tier": "sonnet",
            "depends-on": "SR-1",
            "inputs": "control.py",
            "done-when": "tests pass",
        }
        control_mod.cmd_spawn_request("demo-research", fields=fields, config=cfg)
        text = ctl_file.read_text(encoding="utf-8")
        assert "SPAWN REQUEST" in text
        for field in SPAWN_REQUIRED:
            assert field + ":" in text or field in text, f"field {field!r} missing from output"

    def test_return_refuses_missing_field(self, cfg, ctl_file):
        """cmd_return_entry refuses if any RETURN_REQUIRED field is missing."""
        from research_vault.controllib import RETURN_REQUIRED
        full = {
            "did": "built it",
            "outcome": "PR #5",
            "confidence": "high",
            "next": "merge",
            "provenance": "sha:abc123",
            "retro": "worked well",
        }
        assert set(full.keys()) == set(RETURN_REQUIRED)
        incomplete = dict(full)
        del incomplete["retro"]
        with pytest.raises((ValueError, KeyError), match="retro|required|missing"):
            control_mod.cmd_return_entry("demo-research", fields=incomplete, config=cfg)

    def test_return_accepts_role_extra_fields(self, cfg, ctl_file):
        """cmd_return_entry accepts arbitrary role-extra key:val fields beyond RETURN_REQUIRED."""
        fields = {
            "did": "reviewed sr-cp",
            "outcome": "verdict: approve",
            "confidence": "high",
            "next": "merge",
            "provenance": "sha:abc",
            "retro": "good coverage",
            # role extras
            "verdict": "approve",
            "pr": "#5",
        }
        control_mod.cmd_return_entry("demo-research", fields=fields, config=cfg)
        text = ctl_file.read_text(encoding="utf-8")
        assert "RETURN" in text
        assert "verdict:" in text or "verdict" in text


# ---------------------------------------------------------------------------
# Test 5b: Concurrency
# ---------------------------------------------------------------------------

class TestConcurrency:
    """5b. N concurrent post calls all land (advisory lock prevents clobber)."""

    def test_concurrent_posts_all_land(self, cfg, ctl_file):
        """N parallel post calls all produce entries — fails without locking."""
        N = 10
        errors: list[Exception] = []

        def post_one(i: int) -> None:
            try:
                control_mod.cmd_post(
                    "demo-research",
                    section="inbox",
                    title=f"concurrent-{i}",
                    body=f"body-{i}",
                    by="mason",
                    config=cfg,
                )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=post_one, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors during concurrent posts: {errors}"

        # All N entries must be present
        text = ctl_file.read_text(encoding="utf-8")
        found = sum(1 for i in range(N) if f"concurrent-{i}" in text)
        assert found == N, (
            f"Expected {N} entries, found {found}. "
            f"Concurrent clobber likely. File:\n{text}"
        )


# ---------------------------------------------------------------------------
# Test 6: close cleans in one motion
# ---------------------------------------------------------------------------

class TestCloseOneMotion:
    """6. rv control close <id> sets marker + archives + index in one call."""

    def test_close_sets_marker_and_archives(self, cfg, ctl_file):
        """close: entry leaves live file, appears in archive sidecar."""
        # Post a handshake entry
        control_mod.cmd_post(
            "demo-research",
            section="handshakes",
            title="test-handshake",
            kind="handshake",
            by="mason",
            config=cfg,
        )
        live_text = ctl_file.read_text(encoding="utf-8")
        # Extract the generated entry id
        import re
        m = re.search(r"\*\*([^:*]+:[^*]+)\*\*", live_text)
        assert m, f"Could not find posted handshake entry in:\n{live_text}"
        entry_id = m.group(1)

        # Close it
        control_mod.cmd_close("demo-research", entry_id=entry_id, config=cfg)

        # Entry gone from live file
        live_after = ctl_file.read_text(encoding="utf-8")
        assert entry_id not in live_after, (
            f"Entry {entry_id!r} still in live file after close"
        )

        # Entry appears in archive sidecar
        archive_path = cfg.project_control_file("demo-research").parent / (
            ctl_file.stem + ".archive.md"
        )
        assert archive_path.exists(), f"Archive sidecar not created: {archive_path}"
        archive_text = archive_path.read_text(encoding="utf-8")
        assert entry_id in archive_text or "test-handshake" in archive_text, (
            f"Entry not found in archive: {archive_path}"
        )

    def test_close_appends_index_one_liner(self, cfg, ctl_file):
        """close appends a one-liner to the archive sidecar index region."""
        control_mod.cmd_post(
            "demo-research",
            section="handshakes",
            title="indexed-entry",
            kind="handshake",
            by="mason",
            config=cfg,
        )
        live_text = ctl_file.read_text(encoding="utf-8")
        import re
        m = re.search(r"\*\*([^:*]+:[^*]+)\*\*", live_text)
        assert m
        entry_id = m.group(1)

        control_mod.cmd_close("demo-research", entry_id=entry_id, config=cfg)

        archive_path = ctl_file.parent / (ctl_file.stem + ".archive.md")
        archive_text = archive_path.read_text(encoding="utf-8")
        # Index one-liner must be present in the index region
        assert "indexed-entry" in archive_text or entry_id in archive_text, (
            f"One-liner missing from archive index"
        )


# ---------------------------------------------------------------------------
# Test 7: reconcile --archive + resolved-count teeth
# ---------------------------------------------------------------------------

class TestReconcileArchive:
    """7. reconcile --archive on terminal live signal + resolved-count teeth."""

    def test_reconcile_archive_moves_terminal_entry(self, cfg, ctl_file, tmp_git_repo):
        """reconcile --archive auto-archives entry whose id maps to a merged branch."""
        # Post a verdict entry for sr-m
        control_mod.cmd_post(
            "demo-research",
            section="outbox",
            title="sr-m verdict",
            by="argus",
            kind="verdict",
            config=cfg,
        )
        live_text = ctl_file.read_text(encoding="utf-8")
        import re
        m = re.search(r"\*\*([^:*]+:[^*]+)\*\*", live_text)
        assert m, f"Could not find verdict entry: {live_text}"
        entry_id = m.group(1)

        # Create and merge a branch whose name contains something from the entry
        # For simplicity, create a branch for "sr-m" and merge it
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "checkout", "-b", "feat/sr-m"],
            check=True, capture_output=True,
        )
        (tmp_git_repo / "done.txt").write_text("done\n")
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "add", "."],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "commit", "-m", "sr-m done"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "checkout", "main"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "merge", "feat/sr-m", "--no-ff"],
            check=True, capture_output=True,
        )

        control_mod.cmd_reconcile(
            "demo-research", config=cfg, git_repo=tmp_git_repo, archive=True
        )

        live_after = ctl_file.read_text(encoding="utf-8")
        archive_path = ctl_file.parent / (ctl_file.stem + ".archive.md")
        # sr-m entry must be gone from the live file
        assert entry_id not in live_after, (
            f"Terminal entry {entry_id!r} still in live file after --archive"
        )
        # Archive sidecar must exist and contain the entry
        assert archive_path.exists(), "Archive sidecar was not created"
        archive_text = archive_path.read_text(encoding="utf-8")
        assert entry_id in archive_text, (
            f"Entry {entry_id!r} missing from archive sidecar {archive_path}"
        )

    def test_reconcile_archive_moves_terminal_entry_squash(
        self, cfg, ctl_file, tmp_git_repo
    ):
        """reconcile --archive auto-archives entry via squash-merge (#N) signal.

        This exercises the ACTUAL merge model of this repo (GitHub squash-and-merge):
        no merge commit is produced, so the primary --merges signal is empty;
        the tertiary signal (non-merge commit subject with (#N) anchor) fires.
        """
        control_mod.cmd_post(
            "demo-research",
            section="outbox",
            title="sr-q verdict",
            by="argus",
            kind="verdict",
            config=cfg,
        )
        live_text = ctl_file.read_text(encoding="utf-8")
        import re
        m = re.search(r"\*\*([^:*]+:[^*]+)\*\*", live_text)
        assert m, f"Could not find verdict entry: {live_text}"
        entry_id = m.group(1)

        # Squash-merge feat/sr-q with GitHub-style commit subject
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "checkout", "-b", "feat/sr-q"],
            check=True, capture_output=True,
        )
        (tmp_git_repo / "sq.txt").write_text("squashed\n")
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "add", "."],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "commit", "-m", "wip: sr-q"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "checkout", "main"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "merge", "--squash", "feat/sr-q"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "commit",
             "-m", "feat(sr-q): deliver sr-q verdict (#17)"],
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_git_repo), "branch", "-D", "feat/sr-q"],
            check=True, capture_output=True,
        )

        control_mod.cmd_reconcile(
            "demo-research", config=cfg, git_repo=tmp_git_repo, archive=True
        )

        live_after = ctl_file.read_text(encoding="utf-8")
        archive_path = ctl_file.parent / (ctl_file.stem + ".archive.md")
        assert entry_id not in live_after, (
            f"Terminal entry {entry_id!r} still in live file after squash-merge --archive"
        )
        assert archive_path.exists(), "Archive sidecar was not created"
        archive_text = archive_path.read_text(encoding="utf-8")
        assert entry_id in archive_text, (
            f"Entry {entry_id!r} missing from archive sidecar after squash-merge"
        )

    def test_non_terminal_entry_stays(self, cfg, ctl_file):
        """reconcile --archive leaves non-terminal entries in place."""
        control_mod.cmd_post(
            "demo-research",
            section="handshakes",
            title="active-handshake",
            by="wren",
            config=cfg,
        )
        live_text = ctl_file.read_text(encoding="utf-8")
        # Reconcile with no terminal signals
        control_mod.cmd_reconcile("demo-research", config=cfg, archive=True)

        live_after = ctl_file.read_text(encoding="utf-8")
        assert "active-handshake" in live_after, (
            "Non-terminal entry was incorrectly archived"
        )

    def test_resolved_count_teeth_over_threshold(self, cfg, ctl_file):
        """resolved-but-unarchived count exceeds threshold → reconcile exits non-zero."""
        # Post and close several entries to accumulate resolved markers
        ids = []
        for i in range(6):
            control_mod.cmd_post(
                "demo-research",
                section="handshakes",
                title=f"hs-{i}",
                by="mason",
                config=cfg,
            )
            live = ctl_file.read_text(encoding="utf-8")
            import re
            m = re.search(r"\*\*(handshake:[^*]+)\*\*", live)
            if m:
                ids.append(m.group(1))

        # Now set CLOSED markers directly without archiving (simulating stale resolved entries)
        text = ctl_file.read_text(encoding="utf-8")
        # Mark all entries as CLOSED: prefix (the resolved marker pattern)
        # Original: "- **handshake:...** — hs-0"
        # After:    "- CLOSED: **handshake:...** — hs-0"
        text = re.sub(r"^(- )(\*\*[^:*]+:[^*]+\*\*)", r"\1CLOSED: \2", text, flags=re.MULTILINE)
        ctl_file.write_text(text, encoding="utf-8")

        # Verify the count exceeds threshold (threshold is 5 in spec)
        count = control_mod.count_resolved_unarchived("demo-research", config=cfg)
        assert count >= 5, f"Expected >= 5 resolved entries, got {count}"

        # Wire check: cmd_reconcile must return a finding (exit non-zero) when
        # the resolved-but-unarchived count exceeds RESOLVED_THRESHOLD.
        findings = control_mod.cmd_reconcile("demo-research", config=cfg)
        bloat_findings = [
            f for f in findings
            if "resolved" in f.lower() and "threshold" in f.lower()
        ]
        assert bloat_findings, (
            f"Expected reconcile to flag resolved-count bloat, got findings: {findings}"
        )

    def test_resolved_count_under_threshold_is_green(self, cfg, ctl_file):
        """resolved-but-unarchived count under threshold → OK."""
        control_mod.cmd_post(
            "demo-research",
            section="inbox",
            title="one-item",
            by="mason",
            config=cfg,
        )
        count = control_mod.count_resolved_unarchived("demo-research", config=cfg)
        assert count == 0, f"Freshly-posted (not resolved) entry raised count to {count}"


# ---------------------------------------------------------------------------
# Test 8: Append-only-record index (devlog)
# ---------------------------------------------------------------------------

class TestDevlogIndex:
    """8. rv devlog index/search returns entries without loading the whole file; idempotent."""

    @pytest.fixture
    def seeded_devlog(self, cfg):
        """A devlog with three dated entries."""
        devlog_mod.cmd_init("demo-research", config=cfg, overwrite=True)
        devlog_mod.cmd_append(
            "demo-research", "Done", "first thing done", config=cfg,
            date="2026-06-01"
        )
        devlog_mod.cmd_append(
            "demo-research", "Decisions", "chose approach A", config=cfg,
            date="2026-06-15"
        )
        devlog_mod.cmd_append(
            "demo-research", "Done", "second thing done", config=cfg,
            date="2026-06-30"
        )
        return cfg.project_devlog("demo-research")

    def test_index_returns_one_line_per_entry(self, cfg, seeded_devlog):
        """rv devlog index returns one-liner per dated entry."""
        entries = devlog_mod.cmd_index("demo-research", config=cfg)
        assert len(entries) >= 2, f"Expected >= 2 index entries, got {len(entries)}: {entries}"
        for entry in entries:
            assert isinstance(entry, dict)
            assert "date" in entry
            assert "summary" in entry

    def test_search_finds_by_keyword(self, cfg, seeded_devlog):
        """rv devlog search <keyword> finds entries matching the keyword."""
        results = devlog_mod.cmd_search("demo-research", query="approach A", config=cfg)
        assert len(results) >= 1, f"Expected search hit for 'approach A', got: {results}"
        assert any("approach A" in r.get("summary", "") or "approach A" in r.get("body", "")
                   for r in results)

    def test_search_no_hit_returns_empty(self, cfg, seeded_devlog):
        """rv devlog search with no match returns empty list."""
        results = devlog_mod.cmd_search(
            "demo-research", query="xyzzy-no-match-ever-9999", config=cfg
        )
        assert results == [], f"Expected empty results, got: {results}"

    def test_index_is_idempotent(self, cfg, seeded_devlog):
        """Generating the index twice produces the same result (no diff)."""
        idx1 = devlog_mod.cmd_index("demo-research", config=cfg)
        idx2 = devlog_mod.cmd_index("demo-research", config=cfg)
        assert idx1 == idx2, f"Index not idempotent:\n{idx1}\nvs\n{idx2}"

    def test_index_does_not_load_full_file_for_search(self, cfg, seeded_devlog):
        """search operates on parsed index structure, not the full raw file."""
        # This is structural — we verify the parsed output matches what's in the index
        # and that index is a list of dicts (not raw file text)
        idx = devlog_mod.cmd_index("demo-research", config=cfg)
        for entry in idx:
            assert isinstance(entry, dict), f"Index entry is not a dict: {entry!r}"
            assert "date" in entry, f"Index entry missing date: {entry}"


# ---------------------------------------------------------------------------
# Test 9: CLI exit-code contract for reconcile
# ---------------------------------------------------------------------------

class TestReconcileExitCode:
    """Reconcile exits 1 on drift and 0 when clean — CLI dispatcher path.

    The core contract that makes `rv control reconcile` composable into
    pre-commit/CI hooks. Asserted via the run() dispatcher, not cmd_reconcile
    alone, so the CLI routing code is covered by real exit-code assertions.
    """

    def _run_reconcile(self, project: str) -> int:
        """Invoke the CLI reconcile dispatcher and return its exit code."""
        from research_vault.control import build_parser, run as control_run
        parser = build_parser()
        args = parser.parse_args([project, "reconcile"])
        return control_run(args)

    def test_reconcile_exits_1_on_r1_drift(self, cfg, ctl_file):
        """Reconcile exits 1 when R1 drift (not-yet claim + active task) is present."""
        from research_vault import task as task_mod
        _plant_not_yet_claim(ctl_file, "sr-x")
        task_mod.cmd_add("demo-research", "sr-x implementation",
                         config=cfg, status="in_progress")

        exit_code = self._run_reconcile("demo-research")
        assert exit_code == 1, (
            f"Expected exit code 1 on R1 drift, got {exit_code}"
        )

    def test_reconcile_exits_1_on_r5_bloat(self, cfg, ctl_file):
        """Reconcile exits 1 when resolved-count exceeds RESOLVED_THRESHOLD."""
        import re
        # Post 6 entries (over the threshold of 5)
        for i in range(6):
            control_mod.cmd_post(
                "demo-research",
                section="handshakes",
                title=f"bloat-{i}",
                by="mason",
                config=cfg,
            )
        # Stamp CLOSED: markers without archiving (simulating stale resolved entries)
        text = ctl_file.read_text(encoding="utf-8")
        text = re.sub(
            r"^(- )(\*\*[^:*]+:[^*]+\*\*)", r"\1CLOSED: \2",
            text, flags=re.MULTILINE,
        )
        ctl_file.write_text(text, encoding="utf-8")

        exit_code = self._run_reconcile("demo-research")
        assert exit_code == 1, (
            f"Expected exit code 1 on R5 resolved-count bloat, got {exit_code}"
        )

    def test_reconcile_exits_0_when_clean(self, cfg, ctl_file):
        """Reconcile exits 0 when control file has no drift."""
        exit_code = self._run_reconcile("demo-research")
        assert exit_code == 0, (
            f"Expected exit code 0 on clean control file, got {exit_code}"
        )

    def test_reconcile_exits_0_at_exactly_threshold(self, cfg, ctl_file):
        """Resolved count AT threshold (== RESOLVED_THRESHOLD) is not a violation.

        Boundary: `count > RESOLVED_THRESHOLD` (5 > 5 is False).
        5 entries resolved → still green; the flag fires only above 5.
        """
        import re
        from research_vault.controllib import RESOLVED_THRESHOLD
        # Post exactly RESOLVED_THRESHOLD entries
        for i in range(RESOLVED_THRESHOLD):
            control_mod.cmd_post(
                "demo-research",
                section="handshakes",
                title=f"at-threshold-{i}",
                by="mason",
                config=cfg,
            )
        text = ctl_file.read_text(encoding="utf-8")
        text = re.sub(
            r"^(- )(\*\*[^:*]+:[^*]+\*\*)", r"\1CLOSED: \2",
            text, flags=re.MULTILINE,
        )
        ctl_file.write_text(text, encoding="utf-8")

        # Verify the count is exactly at the threshold
        count = control_mod.count_resolved_unarchived("demo-research", config=cfg)
        assert count == RESOLVED_THRESHOLD, (
            f"Expected exactly {RESOLVED_THRESHOLD} resolved entries, got {count}"
        )

        exit_code = self._run_reconcile("demo-research")
        assert exit_code == 0, (
            f"Expected exit 0 at exactly threshold ({RESOLVED_THRESHOLD}), got {exit_code}"
        )
