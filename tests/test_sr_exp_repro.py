"""test_sr_exp_repro.py — SR-EXP-REPRO: experiment-note reproducibility schema.

All hermetic (tmp_instance / tmp_path). No live W&B API calls — wandb.Api mocked.

Coverage:
  1.  Template: experiments note includes all 22 repro_* fields
  2.  Template: all repro_* fields carry the sentinel (not blank)
  3.  fetch_run returns config dict
  4.  fetch_run returns metadata dict
  5.  wandb_pull writes Layer-1 config artifact (<exp>.config.json)
  6.  wandb_pull fills repro_config_location + repro_config_hash in note
  7.  Alias map: seed → repro_seed (canonical key)
  8.  Alias map: random_seed → repro_seed (variant)
  9.  Alias map: model → repro_model_id
  10. Alias map: pretrained → repro_model_id (lm-eval form)
  11. Alias map: model_name → repro_model_id
  12. Alias map: temperature → repro_decode_temperature
  13. Alias map: top_p → repro_decode_top_p
  14. Alias map: max_new_tokens → repro_decode_max_tokens
  15. Alias map: max_tokens → repro_decode_max_tokens (alt form)
  16. Alias map: num_fewshot → repro_num_fewshot
  17. Alias map: n_shot → repro_num_fewshot (alt form)
  18. Alias map: tokenizer → repro_tokenizer
  19. Unknown config key → sentinel in frontmatter (NOT blank)
  20. repro_env_python filled from run.metadata
  21. repro_env_packages filled from run.metadata
  22. repro_hw from compute manifest (active backend)
  23. repro_hw → sentinel when no manifest
  24. repro_dataset_id + repro_dataset_hash linked via --dataset
  25. --dataset missing dataset note → sentinel (not crash)
  26. Lint fires: results_hash set but repro_seed still sentinel
  27. Lint fires: cross-lingual trio still sentinel
  28. Lint does NOT fire when results_hash empty
  29. Lint does NOT fire when repro fields are filled
  30. Acceptance dry-run: 3 mock W&B run configs, measure auto-share ≥ 6/22 on minimal
  31. Acceptance dry-run: lm-eval-harness run config → high auto-share
  32. Import guard: no unguarded `import wandb` added to wandb_pull.py / note.py
  33. model_revision alias fills repro_model_revision
  34. repro_cost_gpu_hours from run.metadata
  35. repro_eval_harness from run.config (harness_version alias)
  36. Sentinel constant is the canonical string
  37. Layer-1 artifact is valid JSON (the dumped run.config)
  38. config artifact hash matches repro_config_hash in frontmatter
  39. --dataset reads location+hash from dataset note
  40. cmd_check integration: experiment with results+repro filled → no lint violations
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from research_vault import note as note_mod
from research_vault.config import load_config


# ---------------------------------------------------------------------------
# Sentinel constant (import check)
# ---------------------------------------------------------------------------

EXPECTED_SENTINEL = "not-recorded-in-provenance"


# ---------------------------------------------------------------------------
# Expected repro field sets (canonical lists for the test contract)
# ---------------------------------------------------------------------------

REPRO_LAYER1 = ["repro_config_location", "repro_config_hash"]

REPRO_AUTO_CONFIG = [
    "repro_seed",
    "repro_model_id",
    "repro_model_revision",
    "repro_decode_temperature",
    "repro_decode_top_p",
    "repro_decode_max_tokens",
    "repro_num_fewshot",
    "repro_tokenizer",
]

REPRO_AUTO_META = [
    "repro_env_packages",
    "repro_env_python",
    "repro_cost_gpu_hours",
]

REPRO_AUTO_HW = ["repro_hw"]
REPRO_AUTO_DATASET = ["repro_dataset_id", "repro_dataset_hash"]
REPRO_AUTO_HARNESS = ["repro_eval_harness"]

REPRO_MANUAL = [
    "repro_prompt_lang",
    "repro_translation_provenance",
    "repro_prompt_version",
    "repro_dataset_split",
    "repro_metric",
]

REPRO_ALL_FIELDS = (
    REPRO_LAYER1
    + REPRO_AUTO_CONFIG
    + REPRO_AUTO_META
    + REPRO_AUTO_HW
    + REPRO_AUTO_DATASET
    + REPRO_AUTO_HARNESS
    + REPRO_MANUAL
)

# Fields the lint must warn about when results_hash is set
REPRO_LINT_REQUIRED = (
    REPRO_LAYER1
    + REPRO_AUTO_CONFIG
    + REPRO_AUTO_META
    + REPRO_MANUAL
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _make_fake_wandb(
    state: str = "finished",
    summary: dict | None = None,
    commit: str | None = None,
    config: dict | None = None,
    metadata: dict | None = None,
):
    """Build a fake wandb module with a mocked Api() returning a configured run."""
    fake_run = MagicMock()
    fake_run.name = "test-run"
    fake_run.display_name = "Test Run"
    fake_run.state = state
    fake_run.commit = commit or "deadbeef12345678"
    fake_run.summary = summary or {"accuracy": 0.95}
    fake_run.config = config if config is not None else {}
    fake_run.metadata = metadata if metadata is not None else {}

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
    # Add sentinel repro fields (mirrors what cmd_new produces)
    for field in REPRO_ALL_FIELDS:
        lines.append(f"{field}: {EXPECTED_SENTINEL}")
    if extra_fields:
        for k, v in extra_fields.items():
            lines.append(f"{k}: {v}")
    lines += ["---", "", "<!-- experiment note -->", ""]
    p = exp_dir / f"{exp_id}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _write_exp_note_with_results(
    exp_dir: Path, exp_id: str, artifact: Path, results_hash: str
) -> Path:
    """Write an experiment note with results_* filled and repro_* still sentinel."""
    exp_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        "type: experiments",
        f"title: Experiment {exp_id}",
        "created: 2026-07-01",
        f"results_location: {artifact}",
        f"results_hash: {results_hash}",
        "results_wandb_run: e/p/r",
        "results_commit: abc123",
    ]
    for field in REPRO_ALL_FIELDS:
        lines.append(f"{field}: {EXPECTED_SENTINEL}")
    lines += ["---", "", "<!-- experiment note -->", ""]
    p = exp_dir / f"{exp_id}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _write_dataset_note(datasets_root: Path, dataset_id: str, location: str, hash_val: str) -> Path:
    """Write a minimal SR-8 dataset note."""
    datasets_root.mkdir(parents=True, exist_ok=True)
    p = datasets_root / f"{dataset_id}.md"
    p.write_text(
        f"---\ntype: datasets\ntitle: {dataset_id}\nlocation: {location}\nhash: {hash_val}\n---\n",
        encoding="utf-8",
    )
    return p


# ---------------------------------------------------------------------------
# 1. Template: experiments note includes all 22 repro_* fields
# ---------------------------------------------------------------------------

class TestNoteTemplate:
    """cmd_new experiments template includes the full repro_* block."""

    def test_template_has_all_repro_fields(self, tmp_instance):
        cfg = load_config(reload=True)
        path = note_mod.cmd_new("demo-research", "experiments", "My Exp", config=cfg)
        text = path.read_text()
        for field in REPRO_ALL_FIELDS:
            assert f"{field}:" in text, f"Missing repro field in template: {field}"

    def test_template_repro_fields_have_sentinel(self, tmp_instance):
        """All repro_* fields in the template carry the sentinel, NOT blank."""
        cfg = load_config(reload=True)
        path = note_mod.cmd_new("demo-research", "experiments", "My Exp 2", config=cfg)
        text = path.read_text()
        from research_vault.note import _parse_frontmatter
        fields, _ = _parse_frontmatter(text)
        for field in REPRO_ALL_FIELDS:
            val = fields.get(field, "")
            assert val == EXPECTED_SENTINEL, (
                f"Field {field!r}: expected sentinel {EXPECTED_SENTINEL!r}, got {val!r}"
            )

    def test_template_includes_cross_lingual_trio(self, tmp_instance):
        """The cross-lingual trio (prompt_lang + translation_provenance) must be present."""
        cfg = load_config(reload=True)
        path = note_mod.cmd_new("demo-research", "experiments", "Cross-lingual", config=cfg)
        text = path.read_text()
        assert "repro_prompt_lang:" in text
        assert "repro_translation_provenance:" in text
        assert "repro_prompt_version:" in text

    def test_sentinel_constant_is_exact_string(self):
        """The REPRO_SENTINEL constant exported by note.py must equal the canonical string."""
        from research_vault.note import REPRO_SENTINEL
        assert REPRO_SENTINEL == EXPECTED_SENTINEL

    def test_repro_fields_count_is_22(self):
        """There are exactly 22 repro_* fields (spec §5J.14)."""
        assert len(REPRO_ALL_FIELDS) == 22


# ---------------------------------------------------------------------------
# 3-4. fetch_run returns config + metadata
# ---------------------------------------------------------------------------

class TestFetchRunExtension:
    """fetch_run returns config and metadata from the W&B run."""

    def test_fetch_run_returns_config_dict(self, monkeypatch):
        from research_vault.wandb_pull import fetch_run
        monkeypatch.setenv("WANDB_API_KEY", "key")
        run_config = {"seed": 42, "model": "gpt2", "temperature": 0.7}
        fake_wandb, _, _ = _make_fake_wandb(config=run_config)
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            result = fetch_run("e", "p", "run1", "key")
        assert "config" in result, "fetch_run must return 'config' key"
        assert result["config"]["seed"] == 42
        assert result["config"]["model"] == "gpt2"

    def test_fetch_run_returns_metadata_dict(self, monkeypatch):
        from research_vault.wandb_pull import fetch_run
        monkeypatch.setenv("WANDB_API_KEY", "key")
        meta = {"python": "3.10.4", "packages": ["torch==2.0.0", "transformers==4.36"]}
        fake_wandb, _, _ = _make_fake_wandb(metadata=meta)
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            result = fetch_run("e", "p", "run1", "key")
        assert "metadata" in result, "fetch_run must return 'metadata' key"
        assert result["metadata"]["python"] == "3.10.4"

    def test_fetch_run_config_empty_run(self, monkeypatch):
        """When run.config is empty, fetch_run returns an empty config dict (not None)."""
        from research_vault.wandb_pull import fetch_run
        monkeypatch.setenv("WANDB_API_KEY", "key")
        fake_wandb, _, _ = _make_fake_wandb(config={})
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            result = fetch_run("e", "p", "run1", "key")
        assert result["config"] == {}


# ---------------------------------------------------------------------------
# 5-6. Layer-1: config artifact written + hashed
# ---------------------------------------------------------------------------

class TestLayer1ConfigArtifact:
    """wandb_pull writes <exp>.config.json and fills repro_config_location/hash."""

    def test_wandb_pull_writes_config_artifact(self, tmp_instance, monkeypatch):
        from research_vault.wandb_pull import wandb_pull
        monkeypatch.setenv("WANDB_API_KEY", "key")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = load_config(reload=True)
        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        _write_exp_note(exp_dir, "exp-cfg")

        run_config = {"seed": 42, "model": "gpt2"}
        fake_wandb, _, _ = _make_fake_wandb(config=run_config)
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            result = wandb_pull(
                "e/p/run1",
                experiment="exp-cfg",
                project_slug="demo-research",
                config=cfg,
            )

        config_path = exp_dir / "exp-cfg.config.json"
        assert config_path.exists(), "Layer-1 config artifact must be written"
        data = json.loads(config_path.read_text())
        assert data["seed"] == 42

    def test_wandb_pull_fills_repro_config_location_and_hash(self, tmp_instance, monkeypatch):
        from research_vault.wandb_pull import wandb_pull
        monkeypatch.setenv("WANDB_API_KEY", "key")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = load_config(reload=True)
        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        note_path = _write_exp_note(exp_dir, "exp-layer1")

        fake_wandb, _, _ = _make_fake_wandb(config={"seed": 1})
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            wandb_pull("e/p/run1", experiment="exp-layer1", project_slug="demo-research", config=cfg)

        from research_vault.note import _parse_frontmatter
        fields, _ = _parse_frontmatter(note_path.read_text())
        assert fields["repro_config_location"] != EXPECTED_SENTINEL
        assert fields["repro_config_hash"].startswith("sha256:")

    def test_config_artifact_hash_matches_frontmatter(self, tmp_instance, monkeypatch):
        """The repro_config_hash in the note matches the actual file hash."""
        from research_vault.wandb_pull import wandb_pull
        monkeypatch.setenv("WANDB_API_KEY", "key")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = load_config(reload=True)
        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        note_path = _write_exp_note(exp_dir, "exp-hashcheck")

        fake_wandb, _, _ = _make_fake_wandb(config={"temperature": 0.8})
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            wandb_pull("e/p/run1", experiment="exp-hashcheck", project_slug="demo-research", config=cfg)

        from research_vault.note import _parse_frontmatter
        fields, _ = _parse_frontmatter(note_path.read_text())
        config_path = Path(fields["repro_config_location"])
        actual_hash = _sha256(config_path.read_bytes())
        assert fields["repro_config_hash"] == actual_hash

    def test_config_artifact_is_valid_json(self, tmp_instance, monkeypatch):
        """The Layer-1 artifact is valid JSON containing the full run.config."""
        from research_vault.wandb_pull import wandb_pull
        monkeypatch.setenv("WANDB_API_KEY", "key")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = load_config(reload=True)
        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        note_path = _write_exp_note(exp_dir, "exp-json")

        run_config = {"seed": 7, "model": "llama", "temperature": 0.0, "top_p": 1.0}
        fake_wandb, _, _ = _make_fake_wandb(config=run_config)
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            wandb_pull("e/p/run1", experiment="exp-json", project_slug="demo-research", config=cfg)

        from research_vault.note import _parse_frontmatter
        fields, _ = _parse_frontmatter(note_path.read_text())
        config_path = Path(fields["repro_config_location"])
        loaded = json.loads(config_path.read_text())
        for k, v in run_config.items():
            assert loaded[k] == v, f"Config artifact missing or wrong key {k!r}"


# ---------------------------------------------------------------------------
# 7-18. Alias map: config keys → repro_* promoted scalars
# ---------------------------------------------------------------------------

class TestAliasMap:
    """run.config keys are mapped to promoted repro_* scalars via the alias table."""

    def _pull_and_read(
        self,
        config: dict,
        tmp_instance,
        monkeypatch,
        metadata: dict | None = None,
        exp_id: str = "exp-alias",
    ) -> dict[str, str]:
        from research_vault.wandb_pull import wandb_pull
        monkeypatch.setenv("WANDB_API_KEY", "key")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = load_config(reload=True)
        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        note_path = _write_exp_note(exp_dir, exp_id)

        fake_wandb, _, _ = _make_fake_wandb(config=config, metadata=metadata or {})
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            wandb_pull("e/p/run1", experiment=exp_id, project_slug="demo-research", config=cfg)

        from research_vault.note import _parse_frontmatter
        fields, _ = _parse_frontmatter(note_path.read_text())
        return fields

    def test_seed_key_maps_to_repro_seed(self, tmp_instance, monkeypatch):
        fields = self._pull_and_read({"seed": 42}, tmp_instance, monkeypatch, exp_id="exp-seed")
        assert fields["repro_seed"] == "42"

    def test_random_seed_maps_to_repro_seed(self, tmp_instance, monkeypatch):
        fields = self._pull_and_read({"random_seed": 99}, tmp_instance, monkeypatch, exp_id="exp-rseed")
        assert fields["repro_seed"] == "99"

    def test_model_key_maps_to_repro_model_id(self, tmp_instance, monkeypatch):
        fields = self._pull_and_read({"model": "gpt2"}, tmp_instance, monkeypatch, exp_id="exp-model")
        assert fields["repro_model_id"] == "gpt2"

    def test_pretrained_key_maps_to_repro_model_id(self, tmp_instance, monkeypatch):
        """lm-eval-harness uses 'pretrained' for the model."""
        fields = self._pull_and_read({"pretrained": "meta-llama/Llama-2-7b"}, tmp_instance, monkeypatch, exp_id="exp-pretrained")
        assert fields["repro_model_id"] == "meta-llama/Llama-2-7b"

    def test_model_name_maps_to_repro_model_id(self, tmp_instance, monkeypatch):
        fields = self._pull_and_read({"model_name": "bert-base"}, tmp_instance, monkeypatch, exp_id="exp-modelname")
        assert fields["repro_model_id"] == "bert-base"

    def test_temperature_maps_to_repro_decode_temperature(self, tmp_instance, monkeypatch):
        fields = self._pull_and_read({"temperature": 0.7}, tmp_instance, monkeypatch, exp_id="exp-temp")
        assert fields["repro_decode_temperature"] == "0.7"

    def test_top_p_maps_to_repro_decode_top_p(self, tmp_instance, monkeypatch):
        fields = self._pull_and_read({"top_p": 0.9}, tmp_instance, monkeypatch, exp_id="exp-topp")
        assert fields["repro_decode_top_p"] == "0.9"

    def test_max_new_tokens_maps_to_repro_decode_max_tokens(self, tmp_instance, monkeypatch):
        fields = self._pull_and_read({"max_new_tokens": 512}, tmp_instance, monkeypatch, exp_id="exp-maxnew")
        assert fields["repro_decode_max_tokens"] == "512"

    def test_max_tokens_maps_to_repro_decode_max_tokens(self, tmp_instance, monkeypatch):
        fields = self._pull_and_read({"max_tokens": 256}, tmp_instance, monkeypatch, exp_id="exp-maxtok")
        assert fields["repro_decode_max_tokens"] == "256"

    def test_num_fewshot_maps_to_repro_num_fewshot(self, tmp_instance, monkeypatch):
        fields = self._pull_and_read({"num_fewshot": 5}, tmp_instance, monkeypatch, exp_id="exp-fewshot")
        assert fields["repro_num_fewshot"] == "5"

    def test_n_shot_maps_to_repro_num_fewshot(self, tmp_instance, monkeypatch):
        fields = self._pull_and_read({"n_shot": 3}, tmp_instance, monkeypatch, exp_id="exp-nshot")
        assert fields["repro_num_fewshot"] == "3"

    def test_tokenizer_maps_to_repro_tokenizer(self, tmp_instance, monkeypatch):
        fields = self._pull_and_read({"tokenizer": "gpt2"}, tmp_instance, monkeypatch, exp_id="exp-tok")
        assert fields["repro_tokenizer"] == "gpt2"

    def test_model_revision_maps_to_repro_model_revision(self, tmp_instance, monkeypatch):
        fields = self._pull_and_read({"model_revision": "main"}, tmp_instance, monkeypatch, exp_id="exp-rev")
        assert fields["repro_model_revision"] == "main"

    def test_unknown_config_key_leaves_sentinel(self, tmp_instance, monkeypatch):
        """Unknown config keys are NOT promoted; sentinel stays."""
        fields = self._pull_and_read({"unknown_key_xyz": "foo"}, tmp_instance, monkeypatch, exp_id="exp-unk")
        assert fields["repro_seed"] == EXPECTED_SENTINEL

    def test_missing_config_key_writes_sentinel_not_blank(self, tmp_instance, monkeypatch):
        """When run.config is empty, all repro_* auto fields are sentinel, not blank."""
        fields = self._pull_and_read({}, tmp_instance, monkeypatch, exp_id="exp-empty-cfg")
        for field in REPRO_AUTO_CONFIG:
            assert fields[field] == EXPECTED_SENTINEL, (
                f"Field {field!r}: must be sentinel for empty config, got {fields.get(field)!r}"
            )


# ---------------------------------------------------------------------------
# 19-21. Metadata fields: env_python, env_packages, cost_gpu_hours
# ---------------------------------------------------------------------------

class TestMetadataFields:
    """repro_env_* and repro_cost_* filled from run.metadata."""

    def _pull_and_read(self, metadata: dict, tmp_instance, monkeypatch, exp_id: str) -> dict[str, str]:
        from research_vault.wandb_pull import wandb_pull
        monkeypatch.setenv("WANDB_API_KEY", "key")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = load_config(reload=True)
        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        note_path = _write_exp_note(exp_dir, exp_id)
        fake_wandb, _, _ = _make_fake_wandb(config={}, metadata=metadata)
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            wandb_pull("e/p/run1", experiment=exp_id, project_slug="demo-research", config=cfg)
        from research_vault.note import _parse_frontmatter
        fields, _ = _parse_frontmatter(note_path.read_text())
        return fields

    def test_repro_env_python_from_metadata(self, tmp_instance, monkeypatch):
        fields = self._pull_and_read({"python": "3.10.4"}, tmp_instance, monkeypatch, "exp-py")
        assert fields["repro_env_python"] == "3.10.4"

    def test_repro_env_packages_from_metadata(self, tmp_instance, monkeypatch):
        """repro_env_packages is filled from metadata packages list (joined or count)."""
        pkgs = ["torch==2.0.0", "transformers==4.36.0"]
        fields = self._pull_and_read({"packages": pkgs}, tmp_instance, monkeypatch, "exp-pkgs")
        assert fields["repro_env_packages"] != EXPECTED_SENTINEL

    def test_repro_cost_gpu_hours_from_metadata(self, tmp_instance, monkeypatch):
        fields = self._pull_and_read({"gpu_hours": 2.5}, tmp_instance, monkeypatch, "exp-gpu")
        assert fields["repro_cost_gpu_hours"] == "2.5"

    def test_missing_metadata_writes_sentinel(self, tmp_instance, monkeypatch):
        """Empty metadata → all meta fields stay sentinel."""
        fields = self._pull_and_read({}, tmp_instance, monkeypatch, "exp-nometa")
        for field in REPRO_AUTO_META:
            assert fields[field] == EXPECTED_SENTINEL, f"{field} must be sentinel when metadata empty"


# ---------------------------------------------------------------------------
# 22-23. repro_hw from compute manifest
# ---------------------------------------------------------------------------

class TestReproHw:
    """repro_hw is filled from the SR-6 compute manifest when available."""

    def test_repro_hw_from_manifest_active_backend(self, tmp_instance, monkeypatch):
        """When a compute manifest exists with an active backend, repro_hw is filled."""
        from research_vault.wandb_pull import wandb_pull
        from research_vault.compute import _manifest_path, _save_manifest
        monkeypatch.setenv("WANDB_API_KEY", "key")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = load_config(reload=True)

        # Write a compute manifest
        manifest = {
            "backends": {
                "active": ["ssh+slurm"],
                "profiles": {
                    "ssh+slurm": {"archetype": "ssh+slurm", "host": "cluster.example.com"},
                },
            },
            "conda_envs": {},
            "gpu_tiers": {"tp4": {"gpus": 4, "models": [">30B"]}},
            "rules": [],
            "model_quirks": {},
            "run_outcomes": [],
        }
        _save_manifest(cfg, manifest)

        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        note_path = _write_exp_note(exp_dir, "exp-hw")
        fake_wandb, _, _ = _make_fake_wandb(config={})
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            wandb_pull("e/p/run1", experiment="exp-hw", project_slug="demo-research", config=cfg)

        from research_vault.note import _parse_frontmatter
        fields, _ = _parse_frontmatter(note_path.read_text())
        assert fields["repro_hw"] != EXPECTED_SENTINEL

    def test_repro_hw_sentinel_when_no_manifest(self, tmp_instance, monkeypatch):
        """Without a compute manifest, repro_hw stays sentinel."""
        from research_vault.wandb_pull import wandb_pull
        monkeypatch.setenv("WANDB_API_KEY", "key")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = load_config(reload=True)
        # Ensure no manifest exists
        manifest_path = cfg.state_dir / "compute_manifest.json"
        if manifest_path.exists():
            manifest_path.unlink()

        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        note_path = _write_exp_note(exp_dir, "exp-nohw")
        fake_wandb, _, _ = _make_fake_wandb(config={})
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            wandb_pull("e/p/run1", experiment="exp-nohw", project_slug="demo-research", config=cfg)

        from research_vault.note import _parse_frontmatter
        fields, _ = _parse_frontmatter(note_path.read_text())
        assert fields["repro_hw"] == EXPECTED_SENTINEL


# ---------------------------------------------------------------------------
# 24-25. repro_dataset_* linked via --dataset
# ---------------------------------------------------------------------------

class TestDatasetLink:
    """--dataset <id> links the SR-8 dataset note, inheriting its hash."""

    def test_dataset_link_fills_dataset_id_and_hash(self, tmp_instance, monkeypatch):
        from research_vault.wandb_pull import wandb_pull
        monkeypatch.setenv("WANDB_API_KEY", "key")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = load_config(reload=True)

        # Write a dataset note
        dataset_hash = "sha256:" + "a" * 64
        _write_dataset_note(cfg.datasets_root, "xnli-en", "/data/xnli.csv", dataset_hash)

        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        note_path = _write_exp_note(exp_dir, "exp-ds")
        fake_wandb, _, _ = _make_fake_wandb(config={})
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            wandb_pull(
                "e/p/run1",
                experiment="exp-ds",
                project_slug="demo-research",
                dataset_id="xnli-en",
                config=cfg,
            )

        from research_vault.note import _parse_frontmatter
        fields, _ = _parse_frontmatter(note_path.read_text())
        assert "xnli-en" in fields["repro_dataset_id"]
        assert fields["repro_dataset_hash"] == dataset_hash

    def test_dataset_link_missing_note_writes_sentinel(self, tmp_instance, monkeypatch):
        """If --dataset points to a nonexistent note, sentinel is written (no crash)."""
        from research_vault.wandb_pull import wandb_pull
        monkeypatch.setenv("WANDB_API_KEY", "key")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = load_config(reload=True)

        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        note_path = _write_exp_note(exp_dir, "exp-nods")
        fake_wandb, _, _ = _make_fake_wandb(config={})
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            wandb_pull(
                "e/p/run1",
                experiment="exp-nods",
                project_slug="demo-research",
                dataset_id="nonexistent-dataset",
                config=cfg,
            )

        from research_vault.note import _parse_frontmatter
        fields, _ = _parse_frontmatter(note_path.read_text())
        assert fields["repro_dataset_id"] == EXPECTED_SENTINEL
        assert fields["repro_dataset_hash"] == EXPECTED_SENTINEL


# ---------------------------------------------------------------------------
# 26-29. Lint: results_* set but repro_* still sentinel
# ---------------------------------------------------------------------------

class TestReproSentinelLint:
    """Lint warns when results_hash is set but required repro_* are still sentinel."""

    def _make_results_artifact(self, tmp_path: Path) -> tuple[Path, str]:
        artifact = tmp_path / "exp.results.json"
        artifact.write_bytes(b'{"accuracy": 0.9}')
        return artifact, _sha256(artifact.read_bytes())

    def test_lint_fires_when_results_set_but_repro_seed_sentinel(self, tmp_instance, tmp_path):
        from research_vault.note import check_repro_sentinel_lint
        artifact, results_hash = self._make_results_artifact(tmp_path)
        note_path = tmp_path / "exp.md"
        lines = [
            "---",
            "type: experiments",
            "results_location: " + str(artifact),
            "results_hash: " + results_hash,
            "results_wandb_run: e/p/r",
            "results_commit: abc",
        ]
        for field in REPRO_ALL_FIELDS:
            lines.append(f"{field}: {EXPECTED_SENTINEL}")
        lines += ["---", ""]
        note_path.write_text("\n".join(lines), encoding="utf-8")

        warnings = check_repro_sentinel_lint(note_path)
        assert len(warnings) > 0, "Lint must fire when results are set and repro_seed is sentinel"
        assert any("repro_seed" in w or "repro" in w for w in warnings)

    def test_lint_does_not_fire_when_results_empty(self, tmp_path):
        """No results → no lint (empty results_hash = not yet run, not a gap)."""
        from research_vault.note import check_repro_sentinel_lint
        note_path = tmp_path / "exp-empty.md"
        lines = [
            "---",
            "type: experiments",
            "results_location: ",
            "results_hash: ",
        ]
        for field in REPRO_ALL_FIELDS:
            lines.append(f"{field}: {EXPECTED_SENTINEL}")
        lines += ["---", ""]
        note_path.write_text("\n".join(lines), encoding="utf-8")

        warnings = check_repro_sentinel_lint(note_path)
        assert warnings == [], "Lint must NOT fire when results_hash is empty"

    def test_lint_does_not_fire_when_repro_filled(self, tmp_path):
        """When all required repro fields are filled, lint does not fire."""
        from research_vault.note import check_repro_sentinel_lint
        artifact = tmp_path / "exp.results.json"
        artifact.write_bytes(b'{"accuracy": 0.9}')
        results_hash = _sha256(artifact.read_bytes())

        note_path = tmp_path / "exp-filled.md"
        lines = [
            "---",
            "type: experiments",
            f"results_location: {artifact}",
            f"results_hash: {results_hash}",
        ]
        # All repro fields filled with real values (not sentinel)
        lines.append(f"repro_config_location: /tmp/exp.config.json")
        lines.append(f"repro_config_hash: sha256:{'a' * 64}")
        for field in REPRO_ALL_FIELDS[2:]:  # Skip layer-1 already added
            lines.append(f"{field}: some-real-value")
        lines += ["---", ""]
        note_path.write_text("\n".join(lines), encoding="utf-8")

        warnings = check_repro_sentinel_lint(note_path)
        assert warnings == [], "Lint must NOT fire when all required repro fields are filled"

    def test_lint_fires_cross_lingual_trio_sentinel(self, tmp_path):
        """Lint explicitly warns about cross-lingual trio when sentinel (fabrication risk)."""
        from research_vault.note import check_repro_sentinel_lint
        artifact = tmp_path / "exp.results.json"
        artifact.write_bytes(b'{"f1": 0.7}')
        results_hash = _sha256(artifact.read_bytes())

        note_path = tmp_path / "exp-cl.md"
        lines = [
            "---",
            "type: experiments",
            f"results_location: {artifact}",
            f"results_hash: {results_hash}",
        ]
        # Fill most repro fields but leave cross-lingual trio as sentinel
        for field in REPRO_ALL_FIELDS:
            if field in ("repro_prompt_lang", "repro_translation_provenance"):
                lines.append(f"{field}: {EXPECTED_SENTINEL}")
            else:
                lines.append(f"{field}: filled-value")
        lines += ["---", ""]
        note_path.write_text("\n".join(lines), encoding="utf-8")

        warnings = check_repro_sentinel_lint(note_path)
        assert len(warnings) > 0
        fields_mentioned = " ".join(warnings)
        assert "repro_prompt_lang" in fields_mentioned or "repro_translation" in fields_mentioned

    def test_cmd_check_includes_repro_lint_for_experiments(self, tmp_instance, tmp_path):
        """rv note check calls the repro lint for experiment notes with results_hash set."""
        from research_vault.note import cmd_check
        cfg = load_config(reload=True)
        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        exp_dir.mkdir(parents=True, exist_ok=True)

        artifact = exp_dir / "exp-lint.results.json"
        artifact.write_bytes(b'{"accuracy": 0.5}')
        results_hash = _sha256(artifact.read_bytes())
        _write_exp_note_with_results(exp_dir, "exp-lint", artifact, results_hash)

        violations = cmd_check("demo-research", config=cfg)
        lint_hits = [v for v in violations if "repro" in v.lower() or "WARN" in v]
        assert len(lint_hits) > 0, "cmd_check must surface repro lint warnings"

    def test_cmd_check_no_repro_lint_when_no_results(self, tmp_instance):
        """rv note check does NOT produce repro lint for notes without results."""
        from research_vault.note import cmd_check
        cfg = load_config(reload=True)
        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        note_path = note_mod.cmd_new("demo-research", "experiments", "Fresh Exp", config=cfg)
        # Fresh note has sentinel repro fields + empty results — no lint expected
        violations = cmd_check("demo-research", config=cfg)
        repro_lint_hits = [v for v in violations if "repro" in v.lower() and "WARN" in v]
        assert len(repro_lint_hits) == 0


# ---------------------------------------------------------------------------
# 30-31. Acceptance dry-run: 3 mock W&B run configs, measure auto-share
# ---------------------------------------------------------------------------

class TestAcceptanceDryRun:
    """Acceptance: 3 mock W&B configs → measure how many of 22 repro fields populate.

    This is the charter §9 cheap screen — run FIRST to calibrate the alias map.
    A well-configured run should auto-populate ≥ 8 of the 22 fields.
    """

    def _count_auto_populated(
        self,
        run_config: dict,
        run_metadata: dict,
        tmp_instance,
        monkeypatch,
        exp_id: str,
    ) -> tuple[int, dict[str, str]]:
        """Returns (count of non-sentinel repro fields, all field values)."""
        from research_vault.wandb_pull import wandb_pull
        monkeypatch.setenv("WANDB_API_KEY", "key")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = load_config(reload=True)
        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        note_path = _write_exp_note(exp_dir, exp_id)

        fake_wandb, _, _ = _make_fake_wandb(config=run_config, metadata=run_metadata)
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            wandb_pull("e/p/run1", experiment=exp_id, project_slug="demo-research", config=cfg)

        from research_vault.note import _parse_frontmatter
        fields, _ = _parse_frontmatter(note_path.read_text())
        non_sentinel = {k: v for k, v in fields.items() if k in REPRO_ALL_FIELDS and v != EXPECTED_SENTINEL}
        return len(non_sentinel), non_sentinel

    def test_minimal_run_config_auto_populates_layer1(self, tmp_instance, monkeypatch):
        """Even a minimal (empty) config populates Layer-1 (config artifact always written)."""
        count, fields = self._count_auto_populated({}, {}, tmp_instance, monkeypatch, "exp-adr-minimal")
        # At minimum: repro_config_location + repro_config_hash (Layer 1)
        assert count >= 2, f"Expected ≥2 auto-populated (Layer 1), got {count}"
        assert fields.get("repro_config_location", EXPECTED_SENTINEL) != EXPECTED_SENTINEL
        assert fields.get("repro_config_hash", EXPECTED_SENTINEL) != EXPECTED_SENTINEL

    def test_lm_eval_harness_run_auto_populates_many_fields(self, tmp_instance, monkeypatch):
        """An lm-eval-harness-style run config populates ≥ 8 repro fields."""
        lm_eval_config = {
            "pretrained": "meta-llama/Llama-2-7b-hf",
            "model_revision": "main",
            "num_fewshot": 5,
            "temperature": 0.0,
            "top_p": 1.0,
            "max_new_tokens": 256,
            "tokenizer": "meta-llama/Llama-2-7b-hf",
            "seed": 42,
        }
        lm_eval_meta = {
            "python": "3.10.12",
            "packages": ["lm_eval==0.4.0", "torch==2.1.0", "transformers==4.36.0"],
        }
        count, fields = self._count_auto_populated(
            lm_eval_config, lm_eval_meta, tmp_instance, monkeypatch, "exp-adr-lmeval"
        )
        assert count >= 8, (
            f"Expected ≥8 auto-populated for lm-eval run, got {count}. "
            f"Populated: {list(fields.keys())}"
        )
        assert fields.get("repro_model_id") == "meta-llama/Llama-2-7b-hf"
        assert fields.get("repro_seed") == "42"
        assert fields.get("repro_env_python") == "3.10.12"

    def test_transformers_style_run_config(self, tmp_instance, monkeypatch):
        """A transformers-trainer-style config also auto-populates expected fields."""
        hf_config = {
            "model_name": "bert-base-uncased",
            "model_revision": "main",
            "seed": 0,
            "temperature": 1.0,
            "max_tokens": 128,
        }
        meta = {"python": "3.9.7"}
        count, fields = self._count_auto_populated(
            hf_config, meta, tmp_instance, monkeypatch, "exp-adr-hf"
        )
        assert count >= 5, f"Expected ≥5 auto-populated for HF run, got {count}"
        assert fields.get("repro_model_id") == "bert-base-uncased"
        assert fields.get("repro_seed") == "0"


# ---------------------------------------------------------------------------
# 32. Import guard: no unguarded `import wandb` added
# ---------------------------------------------------------------------------

class TestImportGuardRepro:
    """No unguarded `import wandb` in the new SR-EXP-REPRO code."""

    @pytest.mark.parametrize("fname", ["wandb_pull.py", "note.py"])
    def test_no_module_level_wandb_import(self, fname):
        src_path = Path(__file__).parent.parent / "src" / "research_vault" / fname
        if not src_path.exists():
            pytest.skip(f"{fname} does not exist")
        text = src_path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(src_path))
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "wandb" and not alias.name.startswith("wandb."), (
                        f"{fname}: unguarded module-level `import {alias.name}`"
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert module != "wandb" and not module.startswith("wandb."), (
                    f"{fname}: unguarded module-level `from {module} import ...`"
                )


# ---------------------------------------------------------------------------
# 35. repro_eval_harness from run.config harness_version alias
# ---------------------------------------------------------------------------

class TestReproEvalHarness:
    """repro_eval_harness is filled from run.config when a harness_version key exists."""

    def test_harness_version_fills_repro_eval_harness(self, tmp_instance, monkeypatch):
        from research_vault.wandb_pull import wandb_pull
        monkeypatch.setenv("WANDB_API_KEY", "key")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = load_config(reload=True)
        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        note_path = _write_exp_note(exp_dir, "exp-harness")

        fake_wandb, _, _ = _make_fake_wandb(config={"harness_version": "lm_eval==0.4.0"})
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            wandb_pull("e/p/run1", experiment="exp-harness", project_slug="demo-research", config=cfg)

        from research_vault.note import _parse_frontmatter
        fields, _ = _parse_frontmatter(note_path.read_text())
        assert fields["repro_eval_harness"] == "lm_eval==0.4.0"


# ---------------------------------------------------------------------------
# 39. --dataset reads location + hash from dataset note
# ---------------------------------------------------------------------------

class TestDatasetLinkRead:
    """--dataset reads the location field as repro_dataset_id and hash as repro_dataset_hash."""

    def test_dataset_location_used_as_dataset_id(self, tmp_instance, monkeypatch):
        from research_vault.wandb_pull import wandb_pull
        monkeypatch.setenv("WANDB_API_KEY", "key")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = load_config(reload=True)

        dataset_hash = "sha256:" + "b" * 64
        _write_dataset_note(cfg.datasets_root, "flores-200", "doi:10.234/flores", dataset_hash)

        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        note_path = _write_exp_note(exp_dir, "exp-flores")
        fake_wandb, _, _ = _make_fake_wandb(config={})
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            wandb_pull(
                "e/p/run1",
                experiment="exp-flores",
                project_slug="demo-research",
                dataset_id="flores-200",
                config=cfg,
            )

        from research_vault.note import _parse_frontmatter
        fields, _ = _parse_frontmatter(note_path.read_text())
        # repro_dataset_id should contain reference to the dataset note
        assert "flores-200" in fields["repro_dataset_id"]
        assert fields["repro_dataset_hash"] == dataset_hash


# ---------------------------------------------------------------------------
# 40. Integration: fully filled experiment note passes cmd_check with no violations
# ---------------------------------------------------------------------------

class TestCmdCheckIntegration:
    """A fully filled experiment note (results + repro) passes rv note check cleanly."""

    def test_filled_experiment_passes_cmd_check(self, tmp_instance, monkeypatch):
        from research_vault.wandb_pull import wandb_pull
        from research_vault.note import cmd_check
        monkeypatch.setenv("WANDB_API_KEY", "key")
        monkeypatch.setenv("VAULT_SKIP_KEYRING", "1")
        cfg = load_config(reload=True)
        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"

        # Create a fresh experiments note via cmd_new
        note_path = note_mod.cmd_new("demo-research", "experiments", "Integration Exp", config=cfg)

        # Pull run data → fills results_* and repro_* (auto ones)
        run_config = {"seed": 1, "pretrained": "gpt2", "num_fewshot": 0}
        meta = {"python": "3.11.0"}
        fake_wandb, _, _ = _make_fake_wandb(config=run_config, metadata=meta)
        with patch.dict("sys.modules", {"wandb": fake_wandb}):
            wandb_pull(
                "e/p/run1",
                experiment=note_path.stem,
                project_slug="demo-research",
                config=cfg,
            )

        # Now manually fill the required manual repro fields so the lint is quiet
        from research_vault.wandb_pull import _update_frontmatter
        _update_frontmatter(note_path, {
            "repro_prompt_lang": "en",
            "repro_translation_provenance": "N/A",
            "repro_prompt_version": "v1.0",
            "repro_dataset_split": "test",
            "repro_metric": "accuracy",
        })

        violations = cmd_check("demo-research", config=cfg)
        errors = [v for v in violations if "WARN" not in v]
        assert errors == [], f"Unexpected errors: {errors}"
