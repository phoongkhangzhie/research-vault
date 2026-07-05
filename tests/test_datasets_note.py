"""test_datasets_note.py — F24: datasets-note discoverability.

Tests:
  1. rv experiment new plan note contains a dataset provenance section
  2. rv experiment new printed next steps include a dataset prompt
  3. check_dataset_provenance_warn fires when results_hash set + repro_dataset_id sentinel
  4. check_dataset_provenance_warn is silent when repro_dataset_id is filled
  5. check_dataset_provenance_warn is silent when results_hash is empty (not yet run)
  6. cmd_check (rv note check) includes dataset provenance warn for experiments
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.note import REPRO_SENTINEL, REPRO_NOT_APPLICABLE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_exp_note(
    path: Path,
    *,
    results_hash: str = "",
    repro_dataset_id: str = REPRO_SENTINEL,
) -> Path:
    """Write a minimal experiments note for provenance-warn tests."""
    lines = [
        "---",
        "type: experiments",
        "citekey: test-exp",
        f"results_hash: {results_hash}",
        f"repro_dataset_id: {repro_dataset_id}",
        "repro_dataset_hash: " + REPRO_SENTINEL,
        "---",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _make_instance(tmp_path: Path) -> Path:
    cfg_path = tmp_path / "research_vault.toml"
    proj_dir = tmp_path / "projects" / "demo-research"
    proj_dir.mkdir(parents=True)
    notes = tmp_path / "notes"
    notes.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    datasets = tmp_path / "datasets"
    datasets.mkdir()
    cfg_path.write_text(
        f'instance_root = "{tmp_path}"\n'
        f'notes_root = "{notes}"\n'
        f'state_dir = "{state}"\n'
        f'datasets_root = "{datasets}"\n'
        f'agents_dir = "{tmp_path / ".agents"}"\n'
        f'tasks_dir = "{tmp_path / "tasks"}"\n'
        f'control_dir = "{tmp_path / "control"}"\n'
        '[adapters]\nnotifier = "file"\nbackend = "local"\nsecrets = "env"\n'
        f'\n[projects.demo-research]\nsource_dir = "{proj_dir}"\n',
        encoding="utf-8",
    )
    return cfg_path


@pytest.fixture
def instance(tmp_path, monkeypatch):
    cfg_path = _make_instance(tmp_path)
    monkeypatch.setenv("RESEARCH_VAULT_CONFIG", str(cfg_path))
    from research_vault.config import reset_config_cache
    reset_config_cache()
    yield tmp_path
    reset_config_cache()


# ---------------------------------------------------------------------------
# 1. Plan note contains dataset provenance section
# ---------------------------------------------------------------------------

class TestExperimentPlanNoteDatasetSection:
    """rv experiment new plan note must include dataset provenance guidance."""

    def test_plan_note_contains_dataset_section(self, instance):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        plan_path, _ = cmd_new(
            "demo-research", "q1",
            question="Does prompt language drive accuracy?",
            n_mains=1,
            config=cfg,
        )
        text = plan_path.read_text(encoding="utf-8")
        # The plan note must surface dataset provenance guidance
        assert "dataset" in text.lower(), (
            "Plan note must mention dataset provenance so the researcher doesn't skip it"
        )

    def test_plan_note_mentions_rv_note_datasets_command(self, instance):
        from research_vault.experiment import cmd_new
        from research_vault.config import load_config
        cfg = load_config()
        plan_path, _ = cmd_new(
            "demo-research", "q1",
            question="test question",
            n_mains=1,
            config=cfg,
        )
        text = plan_path.read_text(encoding="utf-8")
        # The plan note must reference how to create a datasets note
        assert "rv note" in text and "datasets" in text, (
            "Plan note must tell researcher to use 'rv note <p> new datasets ...'"
        )


# ---------------------------------------------------------------------------
# 2. Printed next steps include dataset prompt
# ---------------------------------------------------------------------------

class TestExperimentNewPrintsDatasetPrompt:
    """rv experiment new output must surface the dataset provenance next step."""

    def test_run_prints_dataset_prompt(self, instance, capsys):
        from research_vault.experiment import run as exp_run, build_parser
        p = build_parser()
        args = p.parse_args([
            "demo-research", "new", "q1",
            "--question", "test question",
        ])
        exit_code = exp_run(args)
        captured = capsys.readouterr()
        assert exit_code == 0
        # The printed next steps must mention datasets / dataset provenance
        output = captured.out.lower()
        assert "dataset" in output, (
            "rv experiment new next steps must mention dataset provenance"
        )


# ---------------------------------------------------------------------------
# 3-5. check_dataset_provenance_warn logic
# ---------------------------------------------------------------------------

class TestCheckDatasetProvenanceWarn:
    """check_dataset_provenance_warn: fires on sentinel, silent when filled/no-run."""

    def test_warn_fires_when_results_set_and_dataset_sentinel(self, tmp_path):
        """When results_hash is set but repro_dataset_id is sentinel → WARN."""
        from research_vault.note import check_dataset_provenance_warn
        note = _write_exp_note(
            tmp_path / "exp.md",
            results_hash="sha256:" + "a" * 64,
            repro_dataset_id=REPRO_SENTINEL,
        )
        warnings = check_dataset_provenance_warn(note)
        assert len(warnings) > 0, (
            "Must warn when experiment ran but dataset provenance is unrecorded"
        )
        combined = " ".join(warnings)
        assert "dataset" in combined.lower()
        # Must be a WARN, not a BLOCK
        assert "[dataset-provenance] WARN" in combined or "WARN" in combined

    def test_warn_is_silent_when_dataset_id_filled(self, tmp_path):
        """When repro_dataset_id is filled (not sentinel) → no warn."""
        from research_vault.note import check_dataset_provenance_warn
        note = _write_exp_note(
            tmp_path / "exp-filled.md",
            results_hash="sha256:" + "a" * 64,
            repro_dataset_id="datasets/my-corpus",
        )
        warnings = check_dataset_provenance_warn(note)
        assert warnings == [], (
            "Must NOT warn when repro_dataset_id is filled"
        )

    def test_warn_is_silent_when_results_hash_empty(self, tmp_path):
        """When results_hash is empty (not yet run) → no warn."""
        from research_vault.note import check_dataset_provenance_warn
        note = _write_exp_note(
            tmp_path / "exp-no-run.md",
            results_hash="",
            repro_dataset_id=REPRO_SENTINEL,
        )
        warnings = check_dataset_provenance_warn(note)
        assert warnings == [], (
            "Must NOT warn when results_hash is empty (experiment not yet run)"
        )

    def test_warn_is_silent_when_results_hash_is_sentinel(self, tmp_path):
        """When results_hash itself is the sentinel (not yet run) → no warn."""
        from research_vault.note import check_dataset_provenance_warn
        note = _write_exp_note(
            tmp_path / "exp-sentinel-hash.md",
            results_hash=REPRO_SENTINEL,
            repro_dataset_id=REPRO_SENTINEL,
        )
        warnings = check_dataset_provenance_warn(note)
        assert warnings == [], (
            "Must NOT warn when results_hash is the sentinel (experiment not yet run)"
        )

    def test_warn_is_silent_when_not_applicable(self, tmp_path):
        """When repro_dataset_id is REPRO_NOT_APPLICABLE → no warn (proxy analysis)."""
        from research_vault.note import check_dataset_provenance_warn
        note = _write_exp_note(
            tmp_path / "exp-na.md",
            results_hash="sha256:" + "b" * 64,
            repro_dataset_id=REPRO_NOT_APPLICABLE,
        )
        warnings = check_dataset_provenance_warn(note)
        assert warnings == [], (
            "Must NOT warn when repro_dataset_id is not-applicable (proxy analysis)"
        )

    def test_warn_is_not_a_block(self, tmp_path):
        """The warn must be a warning, not a blocking violation (SURFACE, never block)."""
        from research_vault.note import check_dataset_provenance_warn
        note = _write_exp_note(
            tmp_path / "exp-block-check.md",
            results_hash="sha256:" + "c" * 64,
            repro_dataset_id=REPRO_SENTINEL,
        )
        warnings = check_dataset_provenance_warn(note)
        assert len(warnings) > 0
        for w in warnings:
            # Must not use BLOCK or ERROR prefix
            assert "[BLOCK]" not in w
            assert "[ERROR]" not in w
            assert "[FAIL]" not in w


# ---------------------------------------------------------------------------
# 6. cmd_check integrates dataset provenance warn
# ---------------------------------------------------------------------------

class TestCmdCheckDatasetProvenance:
    """rv note check calls check_dataset_provenance_warn for experiment notes."""

    def test_cmd_check_warns_on_unrecorded_dataset_provenance(self, instance, tmp_path):
        """cmd_check emits a dataset provenance warn for a ran experiment with sentinel."""
        from research_vault.note import cmd_check
        from research_vault.config import load_config
        cfg = load_config()
        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        exp_dir.mkdir(parents=True, exist_ok=True)

        note = _write_exp_note(
            exp_dir / "exp-ds.md",
            results_hash="sha256:" + "d" * 64,
            repro_dataset_id=REPRO_SENTINEL,
        )

        violations = cmd_check("demo-research", config=cfg)
        combined = " ".join(violations)
        assert "dataset" in combined.lower(), (
            "cmd_check must surface dataset provenance warn for ran experiments"
        )

    def test_cmd_check_silent_when_dataset_provenance_recorded(self, instance, tmp_path):
        """cmd_check does not warn when repro_dataset_id is filled."""
        from research_vault.note import cmd_check
        from research_vault.config import load_config
        cfg = load_config()
        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        exp_dir.mkdir(parents=True, exist_ok=True)

        note = _write_exp_note(
            exp_dir / "exp-ds-filled.md",
            results_hash="sha256:" + "e" * 64,
            repro_dataset_id="datasets/my-corpus",
        )

        violations = cmd_check("demo-research", config=cfg)
        # Filter to only the dataset-provenance warn (other violations may exist from
        # missing repro fields, which are expected for this minimal note)
        ds_violations = [v for v in violations if "dataset-provenance" in v]
        assert ds_violations == [], (
            "cmd_check must NOT emit dataset-provenance warn when repro_dataset_id is filled"
        )
