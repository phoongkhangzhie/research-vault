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


# ---------------------------------------------------------------------------
# 7. CLI degrade: [dataset-provenance] WARN must exit 0, not 1
# ---------------------------------------------------------------------------

class TestCliDatasetProvenanceDegrade:
    """rv note check must degrade [dataset-provenance] WARN to exit 0 (SURFACE, not BLOCK)
    — but PR-CC-1's CHECK-1 now ALSO requires a dataset link (HARD) for the same gap.

    check_dataset_provenance_warn's own docstring still states: "This is a SURFACE,
    never a BLOCK — INFO/WARN only", and note.run()'s _WARN_PREFIXES tuple still
    degrades its own message unchanged. But PR-CC-1 (design §3 CHECK-1) deliberately
    promotes "no dataset link recorded" to a HARD chain requirement (unless the note
    declares repro_dataset_id: not-applicable) — so the AGGREGATE `rv note check`
    exit code for this exact gap is now 1, even though check_dataset_provenance_warn's
    own WARN still surfaces alongside it.
    """

    def test_cli_note_check_dataset_provenance_warn_surfaces_but_check1_blocks(
        self, instance, tmp_path, capsys
    ):
        """The [dataset-provenance] WARN still surfaces; CHECK-1 now also HARD-blocks
        the identical gap (dataset link missing), so the aggregate exit code is 1.

        Every OTHER CHECK-1-required field is filled (results_commit, repro_seed,
        repro_config_location/hash with a real hash-matched artifact) so the dataset
        link is the ONLY thing unmet — isolating this exact interaction.
        """
        from research_vault.note import run, build_parser
        from research_vault.config import load_config
        from research_vault.hashing import hash_file

        cfg = load_config()
        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        exp_dir.mkdir(parents=True, exist_ok=True)

        config_path = exp_dir / "exp-ds-cli.config.json"
        config_path.write_text('{"lr": 0.001}', encoding="utf-8")
        config_hash = hash_file(config_path)

        # A remote (non-local) results_location: check_result_provenance trusts
        # the recorded hash for URL/DOI locations (zero-infra) so this note has
        # NO check_result_provenance violation — only the dataset-link gap remains.
        note_path = exp_dir / "exp-ds-cli.md"
        note_path.write_text(
            "\n".join([
                "---",
                "type: experiments",
                "citekey: test-exp",
                "results_hash: sha256:" + "f" * 64,
                "results_location: doi:10.1234/example",
                "results_commit: abc123",
                "repro_seed: '42'",
                f"repro_config_location: {config_path}",
                f"repro_config_hash: {config_hash}",
                f"repro_dataset_id: {REPRO_SENTINEL}",
                "repro_dataset_hash: " + REPRO_SENTINEL,
                "---",
                "",
            ]),
            encoding="utf-8",
        )

        parser = build_parser()
        args = parser.parse_args(["demo-research", "check"])
        exit_code = run(args)
        out = capsys.readouterr().out

        assert exit_code == 1, (
            "PR-CC-1: a missing dataset link is now ALSO a HARD CHECK-1 violation "
            "(unless repro_dataset_id: not-applicable) — the aggregate exit code "
            "flips to 1 even though the underlying WARN mechanism is unchanged"
        )
        assert "[dataset-provenance] WARN" in out, (
            "check_dataset_provenance_warn's own SURFACE-only message must still "
            "appear in stdout, unchanged"
        )
        assert "dataset link" in out, (
            "CHECK-1's own HARD violation message must also appear"
        )

    def test_cli_note_check_hard_violation_still_exits_one(self, instance, tmp_path, capsys):
        """A genuine hard provenance violation (scores hash mismatch) must still exit 1.

        This proves the degrade fix is scoped to the WARN class only — it must
        NOT loosen the hard gate that check_result_provenance enforces.
        """
        from research_vault.note import run, build_parser
        from research_vault.config import load_config
        import hashlib

        cfg = load_config()
        exp_dir = cfg.project_notes_dir("demo-research") / "experiments"
        exp_dir.mkdir(parents=True, exist_ok=True)

        # Write the actual artifact so a real (mismatching) hash can be computed.
        run1_dir = exp_dir / "run1"
        run1_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = run1_dir / "metrics.json"
        artifact_path.write_text('{"acc": 0.5}', encoding="utf-8")

        # A results_* location with a hash that does NOT match the actual content
        # (genuine hard violation — see check_result_provenance). Use an absolute
        # path so resolution is independent of process CWD.
        note_path = exp_dir / "exp-hard-violation.md"
        note_path.write_text(
            "\n".join([
                "---",
                "type: experiments",
                "citekey: test-exp-hard",
                "results_hash: sha256:" + "0" * 64,  # deliberately wrong hash
                f"results_location: {artifact_path}",
                "repro_dataset_id: datasets/my-corpus",
                "repro_dataset_hash: " + REPRO_SENTINEL,
                "---",
                "",
            ]),
            encoding="utf-8",
        )

        parser = build_parser()
        args = parser.parse_args(["demo-research", "check"])
        exit_code = run(args)
        out = capsys.readouterr().out

        assert exit_code == 1, (
            "A genuine results-hash mismatch must still hard-fail (exit 1) — "
            "the dataset-provenance degrade must not loosen this gate"
        )
        assert "hash mismatch" in out.lower() or "VIOLATION" in out
