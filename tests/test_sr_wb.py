"""test_sr_wb.py — SR-WB: W&B results core (wandb_pull + wandb: predicate + results attachment).

All hermetic (tmp_instance / tmp_path). No live W&B API calls — wandb.Api is mocked.

Seams tested:
  1. parse_run_id: three forms (bare-id, project/run-id, entity/project/run-id)
  2. fetch_run: mocked wandb.Api() → parses run state/summary/commit
  3. wandb_pull: mocked SDK → results JSON written + results_* fields filled
  4. wandb: predicate: finished→ready; failed/crashed→ready+state; running→not ready;
     SDK unavailable → clean error (no traceback)
  5. Experiment-results attachment: hash match passes; hash mismatch is violation
  6. Manual/CSV fallback: results_* fillable by hand, validated the same way
  7. Stdlib-only sentinel: no bare `import wandb` (SDK) in the diff *without* guard
  8. CLI verb: wandb in _VERB_REGISTRY with when_to_use, rv help --check passes
  9. check.py: WANDB_API_KEY + SDK surfaced as wandb prereq
"""

import hashlib
import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from research_vault import note as note_mod
from research_vault.config import load_config
from research_vault.wait_for import resolve_watch


# ---------------------------------------------------------------------------
# Fixture: wandb_absent — simulate wandb SDK not installed
# ---------------------------------------------------------------------------

class _WandbBlocker:
    """Meta-path finder that raises ImportError for wandb — simulates absent SDK.

    Works even when wandb IS pip-installed (needed now that wandb is Tier-1).
    """

    def find_spec(self, fullname, path, target=None):
        if fullname == "wandb" or fullname.startswith("wandb."):
            raise ImportError("[test] wandb simulated absent by _WandbBlocker")
        return None


@pytest.fixture()
def wandb_absent():
    """Simulate the wandb SDK being absent, even when it is pip-installed.

    Full sys.modules snapshot + restore so downstream tests are unaffected.
    """
    saved_all = dict(sys.modules)
    # Remove any cached wandb modules so the blocker intercepts on next import
    for name in list(sys.modules.keys()):
        if name == "wandb" or name.startswith("wandb."):
            del sys.modules[name]
    blocker = _WandbBlocker()
    sys.meta_path.insert(0, blocker)
    yield
    sys.meta_path.remove(blocker)
    sys.modules.clear()
    sys.modules.update(saved_all)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _make_fake_wandb(state: str, summary: dict | None = None, commit: str | None = None):
    """Build a fake wandb module with a mocked Api() that returns a fake run."""
    fake_run = MagicMock()
    fake_run.name = "test-run"
    fake_run.display_name = "Test Run Display"
    fake_run.state = state
    fake_run.commit = commit or "deadbeef12345678"
    fake_run.summary = summary or {"accuracy": 0.95, "loss": 0.12}

    fake_api = MagicMock()
    fake_api.run.return_value = fake_run

    fake_wandb = MagicMock()
    fake_wandb.Api.return_value = fake_api
    fake_wandb.__version__ = "0.16.0"

    return fake_wandb, fake_run, fake_api


def _write_exp_note(exp_dir: Path, exp_id: str, *, extra_fields: dict | None = None) -> Path:
    """Write a minimal experiments note with optional extra frontmatter fields."""
    exp_dir.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: experiments", f"title: Experiment {exp_id}", "created: 2026-07-01"]
    if extra_fields:
        for k, v in extra_fields.items():
            lines.append(f"{k}: {v}")
    lines += ["---", "", "<!-- experiment note -->", ""]
    p = exp_dir / f"{exp_id}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 1. parse_run_id — three forms
# ---------------------------------------------------------------------------

class TestParseRunId:
    """parse_run_id correctly handles all three run-id forms."""

    def test_fully_qualified_three_parts(self):
        from research_vault.wandb_pull import parse_run_id
        ent, proj, run_name = parse_run_id("myentity/myproject/abc123")
        assert ent == "myentity"
        assert proj == "myproject"
        assert run_name == "abc123"

    def test_two_parts_uses_env_entity(self, monkeypatch):
        from research_vault.wandb_pull import parse_run_id
        monkeypatch.setenv("WANDB_ENTITY", "enventity")
        ent, proj, run_name = parse_run_id("myproject/run99")
        assert ent == "enventity"
        assert proj == "myproject"
        assert run_name == "run99"

    def test_two_parts_missing_entity_raises(self, monkeypatch):
        from research_vault.wandb_pull import parse_run_id
        monkeypatch.delenv("WANDB_ENTITY", raising=False)
        with pytest.raises(ValueError, match="WANDB_ENTITY"):
            parse_run_id("myproject/run99")

    def test_bare_id_uses_env_entity_and_project(self, monkeypatch):
        from research_vault.wandb_pull import parse_run_id
        monkeypatch.setenv("WANDB_ENTITY", "enventity")
        monkeypatch.setenv("WANDB_PROJECT", "envproject")
        ent, proj, run_name = parse_run_id("barerun")
        assert ent == "enventity"
        assert proj == "envproject"
        assert run_name == "barerun"

    def test_bare_id_missing_entity_raises(self, monkeypatch):
        from research_vault.wandb_pull import parse_run_id
        monkeypatch.delenv("WANDB_ENTITY", raising=False)
        monkeypatch.setenv("WANDB_PROJECT", "p")
        with pytest.raises(ValueError, match="WANDB_ENTITY"):
            parse_run_id("barerun")

    def test_bare_id_missing_project_raises(self, monkeypatch):
        from research_vault.wandb_pull import parse_run_id
        monkeypatch.setenv("WANDB_ENTITY", "e")
        monkeypatch.delenv("WANDB_PROJECT", raising=False)
        with pytest.raises(ValueError, match="WANDB_PROJECT"):
            parse_run_id("barerun")

    def test_too_many_parts_raises(self):
        from research_vault.wandb_pull import parse_run_id
        with pytest.raises(ValueError, match="Invalid"):
            parse_run_id("a/b/c/d")

    def test_explicit_entity_overrides_env(self, monkeypatch):
        from research_vault.wandb_pull import parse_run_id
        monkeypatch.setenv("WANDB_ENTITY", "enventity")
        ent, proj, run_name = parse_run_id("proj/run1", entity="explicit-ent")
        assert ent == "explicit-ent"
        assert proj == "proj"
        assert run_name == "run1"


# ---------------------------------------------------------------------------
# 2. fetch_run — mocked wandb.Api
# ---------------------------------------------------------------------------

class TestFetchRun:
    """fetch_run parses the mocked wandb SDK response correctly."""

    def test_fetch_run_finished(self, monkeypatch):
        from research_vault.wandb_pull import fetch_run
        monkeypatch.setenv("WANDB_API_KEY", "test-key")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        fake_wandb, fake_run, _ = _make_fake_wandb("finished", {"acc": 0.9}, commit="abc1234")
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            result = fetch_run("myentity", "myproject", "abc123", "test-key")
        assert result["state"] == "finished"
        assert result["commit"] == "abc1234"
        assert result["summaryMetrics"]["acc"] == 0.9

    def test_fetch_run_returns_display_name(self, monkeypatch):
        from research_vault.wandb_pull import fetch_run
        fake_wandb, _, _ = _make_fake_wandb("running")
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            result = fetch_run("e", "p", "abc123", "key")
        assert result["displayName"] == "Test Run Display"

    def test_fetch_run_missing_run_raises(self, monkeypatch):
        """When run() raises an exception with 'not found', fetch_run raises ValueError."""
        from research_vault.wandb_pull import fetch_run
        fake_wandb = MagicMock()
        fake_wandb.__version__ = "0.16.0"
        fake_api = MagicMock()
        fake_api.run.side_effect = Exception("Run does not exist or is not found")
        fake_wandb.Api.return_value = fake_api
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            with pytest.raises(ValueError, match="not found"):
                fetch_run("e", "p", "run1", "key")

    def test_fetch_run_sdk_unavailable_raises_import_error(self, wandb_absent):
        """When wandb SDK is not installed, fetch_run raises ImportError with help msg."""
        from research_vault.wandb_pull import fetch_run
        with pytest.raises(ImportError, match="prerequisite"):
            fetch_run("e", "p", "run1", "key")


# ---------------------------------------------------------------------------
# 3. wandb_pull — writes results artifact + fills experiment note
# ---------------------------------------------------------------------------

class TestWandbPull:
    """wandb_pull writes results.json and fills the four results_* fields."""

    def test_pull_writes_results_json(self, tmp_instance, monkeypatch):
        from research_vault.wandb_pull import wandb_pull
        monkeypatch.setenv("WANDB_API_KEY", "test-key-123")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = load_config(reload=True)
        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        _write_exp_note(exp_dir, "exp-q1")

        fake_wandb, _, _ = _make_fake_wandb(
            "finished",
            {"accuracy": 0.92, "loss": 0.11},
            commit="abc1234def5",
        )
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            result = wandb_pull(
                "myentity/myproject/abc123",
                experiment="exp-q1",
                project_slug="demo-research",
                config=cfg,
            )

        assert result["state"] == "finished"
        # results artifact must exist
        results_path = Path(result["results_location"])
        assert results_path.exists()
        data = json.loads(results_path.read_text())
        assert data["accuracy"] == 0.92
        # hash must be recorded
        assert result["results_hash"].startswith("sha256:")
        # run provenance
        assert result["results_wandb_run"] == "myentity/myproject/abc123"
        assert result["results_commit"] == "abc1234def5"

    def test_pull_fills_experiment_note_frontmatter(self, tmp_instance, monkeypatch):
        from research_vault.wandb_pull import wandb_pull
        monkeypatch.setenv("WANDB_API_KEY", "test-key-123")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = load_config(reload=True)
        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        note_path = _write_exp_note(exp_dir, "exp-q2")

        fake_wandb, _, _ = _make_fake_wandb("finished", {"f1": 0.88}, commit="commit42")
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            wandb_pull(
                "myentity/myproject/run99",
                experiment="exp-q2",
                project_slug="demo-research",
                config=cfg,
            )

        text = note_path.read_text()
        assert "results_location:" in text
        assert "results_hash: sha256:" in text
        assert "results_wandb_run: myentity/myproject/run99" in text
        assert "results_commit: commit42" in text

    def test_pull_without_experiment_returns_state(self, tmp_instance, monkeypatch):
        """Without --experiment, wandb_pull returns metrics without writing any artifact."""
        from research_vault.wandb_pull import wandb_pull
        monkeypatch.setenv("WANDB_API_KEY", "test-key-123")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = load_config(reload=True)
        fake_wandb, _, _ = _make_fake_wandb("finished", {"val_acc": 0.77})
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            result = wandb_pull(
                "myentity/myproject/run77",
                experiment=None,
                project_slug=None,
                config=cfg,
            )
        assert result["state"] == "finished"
        assert result.get("results_location") is None

    def test_pull_hash_matches_artifact(self, tmp_instance, monkeypatch):
        """The results_hash in the return value matches the sha256 of the written artifact."""
        from research_vault.wandb_pull import wandb_pull
        monkeypatch.setenv("WANDB_API_KEY", "key")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = load_config(reload=True)
        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        _write_exp_note(exp_dir, "exp-hash")
        metrics = {"auc": 0.99, "precision": 0.88}
        fake_wandb, _, _ = _make_fake_wandb("finished", metrics)
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            result = wandb_pull(
                "myentity/myproject/runhash",
                experiment="exp-hash",
                project_slug="demo-research",
                config=cfg,
            )
        artifact = Path(result["results_location"])
        raw = artifact.read_bytes()
        expected_hash = _sha256(raw)
        assert result["results_hash"] == expected_hash

    def test_pull_missing_api_key_raises(self, tmp_instance, monkeypatch):
        """wandb_pull raises KeyError when WANDB_API_KEY is missing."""
        from research_vault.wandb_pull import wandb_pull
        monkeypatch.delenv("WANDB_API_KEY", raising=False)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = load_config(reload=True)
        with pytest.raises((KeyError, ValueError)):
            wandb_pull("myentity/myproject/run1", config=cfg)

    def test_pull_sdk_unavailable_raises_import_error(self, tmp_instance, monkeypatch, wandb_absent):
        """When wandb SDK is not installed, wandb_pull raises ImportError with a help msg."""
        from research_vault.wandb_pull import wandb_pull
        monkeypatch.setenv("WANDB_API_KEY", "key")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = load_config(reload=True)
        with pytest.raises(ImportError, match="prerequisite"):
            wandb_pull("e/p/run1", config=cfg)


# ---------------------------------------------------------------------------
# 4. wandb: wait-for predicate
# ---------------------------------------------------------------------------

class TestWandbPredicate:
    """resolve_watch('wandb:<run-id>') returns the correct ready/state."""

    def _resolve(self, state: str, monkeypatch) -> dict:
        monkeypatch.setenv("WANDB_API_KEY", "test-key")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        fake_wandb, _, _ = _make_fake_wandb(state)
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            return resolve_watch("wandb:myentity/myproject/run1")

    def test_finished_is_ready(self, monkeypatch):
        result = self._resolve("finished", monkeypatch)
        assert result["ready"] is True
        assert result["state"] == "finished"
        assert result["error"] is None

    def test_failed_is_ready_with_state(self, monkeypatch):
        """A failed run is terminal — wakes the waiter with state=failed (D-WB-4)."""
        result = self._resolve("failed", monkeypatch)
        assert result["ready"] is True
        assert result["state"] == "failed"

    def test_crashed_is_ready_with_state(self, monkeypatch):
        """A crashed run is terminal — wakes the waiter with state=crashed (D-WB-4)."""
        result = self._resolve("crashed", monkeypatch)
        assert result["ready"] is True
        assert result["state"] == "crashed"

    def test_killed_is_ready(self, monkeypatch):
        result = self._resolve("killed", monkeypatch)
        assert result["ready"] is True
        assert result["state"] == "killed"

    def test_preempted_is_ready(self, monkeypatch):
        result = self._resolve("preempted", monkeypatch)
        assert result["ready"] is True
        assert result["state"] == "preempted"

    def test_running_is_not_ready(self, monkeypatch):
        result = self._resolve("running", monkeypatch)
        assert result["ready"] is False
        assert result["state"] == "running"

    def test_pending_is_not_ready(self, monkeypatch):
        result = self._resolve("pending", monkeypatch)
        assert result["ready"] is False
        assert result["state"] == "pending"

    def test_artifact_path_is_none(self, monkeypatch):
        """wandb: predicate carries no artifact_path (it's a run state, not a file)."""
        result = self._resolve("finished", monkeypatch)
        assert result["artifact_path"] is None

    def test_missing_api_key_returns_error(self, monkeypatch):
        """Missing WANDB_API_KEY returns error state, not ready."""
        monkeypatch.delenv("WANDB_API_KEY", raising=False)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        fake_wandb, _, _ = _make_fake_wandb("finished")
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            result = resolve_watch("wandb:e/p/run9")
        assert result["ready"] is False
        assert result["error"] is not None

    def test_sdk_unavailable_clean_error(self, monkeypatch, wandb_absent):
        """When wandb SDK is not installed, predicate returns clean not-ready — no traceback."""
        monkeypatch.setenv("WANDB_API_KEY", "test-key")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        result = resolve_watch("wandb:e/p/run10")
        assert result["ready"] is False
        assert result["state"] == "sdk-unavailable"
        assert result["error"] is not None

    def test_wandb_known_prefix_accepted_by_wait_for(self, monkeypatch):
        """rv wait-for validates wandb: as a known prefix (not rejected as unknown)."""
        import argparse
        from research_vault.wait_for import run as wait_for_run
        monkeypatch.setenv("WANDB_API_KEY", "test-key")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        fake_wandb, _, _ = _make_fake_wandb("finished")
        args = argparse.Namespace(
            watch="wandb:e/p/runX",
            then_cmd="",
            timeout=0,
            interval=1,
            log="",
            sync=True,
        )
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            rc = wait_for_run(args)
        assert rc == 0


# ---------------------------------------------------------------------------
# 5. Experiment-results attachment — check_result_provenance + cmd_check
# ---------------------------------------------------------------------------

class TestCheckResultProvenance:
    """check_result_provenance validates results_hash against the artifact."""

    def test_valid_hash_passes(self, tmp_path):
        from research_vault.note import check_result_provenance
        artifact = tmp_path / "exp-q1.results.json"
        artifact.write_bytes(b'{"accuracy": 0.95}')
        good_hash = _sha256(b'{"accuracy": 0.95}')
        note_path = tmp_path / "exp-q1.md"
        note_path.write_text(
            "---\n"
            "type: experiments\n"
            "title: Q1 Experiment\n"
            f"results_location: {artifact}\n"
            f"results_hash: {good_hash}\n"
            "results_wandb_run: myentity/myproject/run1\n"
            "results_commit: abc123\n"
            "---\n",
            encoding="utf-8",
        )
        issues = check_result_provenance(note_path)
        assert issues == []

    def test_hash_mismatch_is_violation(self, tmp_path):
        from research_vault.note import check_result_provenance
        artifact = tmp_path / "exp-q2.results.json"
        artifact.write_bytes(b'{"accuracy": 0.95}')
        bad_hash = "sha256:" + "0" * 64
        note_path = tmp_path / "exp-q2.md"
        note_path.write_text(
            "---\n"
            "type: experiments\n"
            f"results_location: {artifact}\n"
            f"results_hash: {bad_hash}\n"
            "results_wandb_run: e/p/r\n"
            "results_commit: abc\n"
            "---\n",
            encoding="utf-8",
        )
        issues = check_result_provenance(note_path)
        assert len(issues) == 1
        assert "hash" in issues[0].lower() or "mismatch" in issues[0].lower()

    def test_empty_results_fields_not_violation(self, tmp_path):
        """An experiment note with empty results_* (not yet pulled) is not a violation."""
        from research_vault.note import check_result_provenance
        note_path = tmp_path / "exp-fresh.md"
        note_path.write_text(
            "---\n"
            "type: experiments\n"
            "results_location: \n"
            "results_hash: \n"
            "results_wandb_run: \n"
            "results_commit: \n"
            "---\n",
            encoding="utf-8",
        )
        issues = check_result_provenance(note_path)
        assert issues == []

    def test_artifact_missing_is_violation(self, tmp_path):
        """When results_hash is set but the artifact file doesn't exist: violation."""
        from research_vault.note import check_result_provenance
        note_path = tmp_path / "exp-gone.md"
        note_path.write_text(
            "---\n"
            "type: experiments\n"
            f"results_location: {tmp_path / 'nonexistent.json'}\n"
            "results_hash: sha256:" + "a" * 64 + "\n"
            "results_wandb_run: e/p/r\n"
            "results_commit: abc\n"
            "---\n",
            encoding="utf-8",
        )
        issues = check_result_provenance(note_path)
        assert len(issues) == 1


class TestCmdCheckExperimentsResults:
    """rv note check validates experiments results_* fields when filled (SR-WB extension)."""

    def test_cmd_check_passes_for_valid_results(self, tmp_instance):
        """A valid experiment note with hash-verified results passes cmd_check."""
        from research_vault.note import cmd_check
        cfg = load_config(reload=True)
        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        exp_dir.mkdir(parents=True, exist_ok=True)
        artifact = exp_dir / "exp-chk.results.json"
        data = b'{"accuracy": 0.9}'
        artifact.write_bytes(data)
        good_hash = _sha256(data)
        (exp_dir / "exp-chk.md").write_text(
            "---\n"
            "type: experiments\n"
            "title: Check test\n"
            f"results_location: {artifact}\n"
            f"results_hash: {good_hash}\n"
            "results_wandb_run: e/p/run\n"
            "results_commit: abc\n"
            # F24: explicitly opt out of dataset provenance warn (no external dataset)
            "repro_dataset_id: not-applicable\n"
            "repro_dataset_hash: not-applicable\n"
            "---\n",
            encoding="utf-8",
        )
        violations = cmd_check("demo-research", config=cfg)
        assert violations == []

    def test_cmd_check_fails_for_hash_mismatch(self, tmp_instance):
        """cmd_check catches a hash mismatch in experiment results."""
        from research_vault.note import cmd_check
        cfg = load_config(reload=True)
        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        exp_dir.mkdir(parents=True, exist_ok=True)
        artifact = exp_dir / "exp-bad.results.json"
        artifact.write_bytes(b'{"accuracy": 0.5}')
        (exp_dir / "exp-bad.md").write_text(
            "---\n"
            "type: experiments\n"
            "title: Bad hash\n"
            f"results_location: {artifact}\n"
            "results_hash: sha256:" + "0" * 64 + "\n"
            "results_wandb_run: e/p/r\n"
            "results_commit: abc\n"
            "---\n",
            encoding="utf-8",
        )
        violations = cmd_check("demo-research", config=cfg)
        assert len(violations) >= 1
        assert any("hash" in v.lower() or "mismatch" in v.lower() for v in violations)

    def test_cmd_check_ignores_empty_results_fields(self, tmp_instance):
        """An experiment note with empty results_* (not yet pulled) is not a violation."""
        from research_vault.note import cmd_check
        cfg = load_config(reload=True)
        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        exp_dir.mkdir(parents=True, exist_ok=True)
        (exp_dir / "exp-empty.md").write_text(
            "---\n"
            "type: experiments\n"
            "title: Empty results\n"
            "results_location: \n"
            "results_hash: \n"
            "results_wandb_run: \n"
            "results_commit: \n"
            "---\n",
            encoding="utf-8",
        )
        violations = cmd_check("demo-research", config=cfg)
        assert violations == []


# ---------------------------------------------------------------------------
# 6. Manual/CSV fallback — hand-filled results_* validated the same way
# ---------------------------------------------------------------------------

class TestManualResultsFallback:
    """results_* fields can be filled by hand (CSV/manual) and validate the same way."""

    def test_manual_fill_validates_with_hash_match(self, tmp_path):
        """Hand-filled results_location + results_hash: valid if hash matches."""
        from research_vault.note import check_result_provenance
        csv_data = b"run_id,accuracy,loss\nrun1,0.95,0.05\n"
        csv_path = tmp_path / "manual_results.csv"
        csv_path.write_bytes(csv_data)
        csv_hash = _sha256(csv_data)
        note_path = tmp_path / "exp-manual.md"
        note_path.write_text(
            "---\n"
            "type: experiments\n"
            "title: Manual CSV fallback\n"
            f"results_location: {csv_path}\n"
            f"results_hash: {csv_hash}\n"
            "results_wandb_run: \n"
            "results_commit: \n"
            "---\n",
            encoding="utf-8",
        )
        issues = check_result_provenance(note_path)
        assert issues == []

    def test_manual_fill_url_location_trusts_hash(self, tmp_path):
        """For URL results_location, hash is trusted without fetching (zero-infra)."""
        from research_vault.note import check_result_provenance
        note_path = tmp_path / "exp-url.md"
        note_path.write_text(
            "---\n"
            "type: experiments\n"
            "results_location: https://example.com/results.json\n"
            "results_hash: sha256:" + "a" * 64 + "\n"
            "results_wandb_run: \n"
            "results_commit: \n"
            "---\n",
            encoding="utf-8",
        )
        issues = check_result_provenance(note_path)
        assert issues == []

    def test_cmd_new_experiments_template_has_results_fields(self, tmp_instance):
        """rv note new <project> experiments <title> includes the 4 results_* placeholder fields."""
        cfg = load_config(reload=True)
        path = note_mod.cmd_new("demo-research", "experiments", "My Experiment", config=cfg)
        text = path.read_text()
        assert "results_location:" in text
        assert "results_hash:" in text
        assert "results_wandb_run:" in text
        assert "results_commit:" in text


# ---------------------------------------------------------------------------
# 7. check.py — wandb prereq (SDK + WANDB_API_KEY)
# ---------------------------------------------------------------------------

class TestCheckWandbPrereq:
    """rv check surfaces wandb SDK + WANDB_API_KEY as a prereq probe."""

    def test_wandb_ok_when_sdk_and_key_present(self, monkeypatch):
        from research_vault.check import run_preflight
        monkeypatch.setenv("WANDB_API_KEY", "wk-test-key-12345")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        fake_wandb, _, _ = _make_fake_wandb("finished")
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            result = run_preflight()
        assert result.get("wandb_key") is True
        assert "wandb" in result["report"].lower()

    def test_wandb_warn_when_key_missing(self, monkeypatch):
        from research_vault.check import run_preflight
        monkeypatch.delenv("WANDB_API_KEY", raising=False)
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        fake_wandb, _, _ = _make_fake_wandb("finished")
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            result = run_preflight()
        assert result.get("wandb_key") is False
        # WANDB_API_KEY missing must NOT flip all_required_ok (W&B is not globally required)
        report_lines = result["report"].splitlines()
        integrations_idx = next(
            (i for i, l in enumerate(report_lines) if "Integrations" in l and "API access" in l),
            None,
        )
        assert integrations_idx is not None, "report must have an 'Integrations (keys / API access):' section"
        integrations_section = "\n".join(report_lines[integrations_idx:])
        assert "wandb" in integrations_section.lower()

    def test_wandb_sdk_not_installed_reports_install(self, monkeypatch, wandb_absent):
        """When wandb SDK isn't installed, rv check reports a clear install message."""
        from research_vault.check import run_preflight
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        result = run_preflight()
        assert result.get("wandb_key") is False
        # Should suggest pip install
        assert "pip install wandb" in result["report"] or "wandb" in result["report"].lower()


# ---------------------------------------------------------------------------
# 8. Import-guard test: SDK unavailable → CLI prints help, exits cleanly
# ---------------------------------------------------------------------------

class TestImportGuard:
    """When wandb SDK is unavailable, rv wandb pull exits cleanly — no traceback."""

    def test_cli_run_sdk_unavailable(self, tmp_instance, monkeypatch, capsys, wandb_absent):
        """rv wandb pull with SDK unavailable prints friendly message, exits 1."""
        from research_vault.wandb_pull import run as wandb_run
        import argparse
        monkeypatch.setenv("WANDB_API_KEY", "key")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        args = argparse.Namespace(
            wandb_cmd="pull",
            run_id="e/p/run1",
            experiment=None,
            project=None,
            json_out=False,
        )
        rc = wandb_run(args)
        assert rc == 1
        captured = capsys.readouterr()
        # Error message must be on stderr — no raw ImportError traceback
        assert "prerequisite" in captured.err.lower() or "not installed" in captured.err.lower()
        assert "Traceback" not in captured.err


# ---------------------------------------------------------------------------
# 9. Stdlib-only sentinel — no *unguarded* `import wandb`
# ---------------------------------------------------------------------------

class TestStdlibOnlySentinel:
    """No unguarded `import wandb` at module level in the SR-WB diff files."""

    @pytest.mark.parametrize("fname", [
        "wandb_pull.py",
        "wait_for.py",
        "note.py",
        "cli.py",
        "check.py",
    ])
    def test_no_module_level_wandb_import(self, fname):
        """Module-level `import wandb` is never allowed — must be inside a try/except guard."""
        import ast
        src_path = Path(__file__).parent.parent / "src" / "research_vault" / fname
        if not src_path.exists():
            pytest.skip(f"{fname} does not exist yet")
        text = src_path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(src_path))
        # Collect all top-level import nodes (not inside try/except)
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "wandb" and not alias.name.startswith("wandb."), (
                        f"{fname}: found module-level `import {alias.name}` — "
                        "wandb SDK must be import-guarded (inside try/except ImportError)"
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert module != "wandb" and not module.startswith("wandb."), (
                    f"{fname}: found module-level `from {module} import ...` — "
                    "wandb SDK must be import-guarded (inside try/except ImportError)"
                )


# ---------------------------------------------------------------------------
# 10. CLI verb: wandb is registered with when_to_use
# ---------------------------------------------------------------------------

class TestWandbVerbRegistry:
    """wandb verb is in _VERB_REGISTRY with when_to_use and sr: SR-WB."""

    def test_wandb_in_registry(self):
        from research_vault.cli import _VERB_REGISTRY
        assert "wandb" in _VERB_REGISTRY

    def test_wandb_has_when_to_use(self):
        from research_vault.cli import _VERB_REGISTRY
        entry = _VERB_REGISTRY["wandb"]
        assert entry.get("when_to_use", "").strip() != ""

    def test_wandb_sr_is_sr_wb(self):
        from research_vault.cli import _VERB_REGISTRY
        assert _VERB_REGISTRY["wandb"].get("sr") == "SR-WB"

    def test_help_check_passes_with_wandb_verb(self):
        """rv help --check should still return zero violations after adding wandb."""
        from research_vault.cli import _check_verb_docstrings
        violations = _check_verb_docstrings()
        assert violations == [], f"Verb docstring violations: {violations}"
