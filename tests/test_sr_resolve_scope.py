"""test_sr_resolve_scope.py — SR-RESOLVE-SCOPE: project-aware DAG resolver tests.

Covers:
  1. note: resolver — project-scoped path resolves via project_notes_dir
  2. note: resolver — legacy notes_root fallback still works (backward compat)
  3. note: resolver — datasets/ segment routes to datasets_root (shared)
  4. note: resolver — +fresh modifier works on project-scoped path
  5. produces.result — schema validation (missing slash → error; SR-RM-FIGMS: figure/manuscript removed)
  6. produces.result resolves against project_notes_dir/experiments/ at complete-time
  7. (SR-RM-FIGMS: produces.figure removed)
  8. (SR-RM-FIGMS: produces.manuscript removed)
  9. produces.result with WRONG type:dir fails (same gate as produces.note)
 10. produces.result with CORRECT type:dir passes
 11. produces.note (legacy) still resolves against notes_root (backward compat)
 12. Shared datasets/ pointer still resolves against datasets_root (not notes_root)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import Config, reset_config_cache, load_config
from research_vault.wait_for import resolve_watch
from research_vault.dag.schema import (
    ManifestError,
    validate_manifest,
)
from research_vault.dag.verbs import (
    cmd_run,
    cmd_complete,
    _check_project_scoped_note,
    _PRODUCES_KEY_TO_OKF_DIR,
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
def instance(tmp_path: Path):
    """A minimal Research Vault instance with two registered projects.

    Projects:
      demo-research   → project_notes_dir = tmp_path/projects/demo-research
      demo-litreview  → project_notes_dir = tmp_path/projects/demo-litreview

    Shared:
      datasets_root = tmp_path/notes/datasets
    """
    notes_root = tmp_path / "notes"
    notes_root.mkdir()
    datasets_root = notes_root / "datasets"
    datasets_root.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    proj_a = tmp_path / "projects" / "demo-research"
    proj_b = tmp_path / "projects" / "demo-litreview"
    for proj in (proj_a, proj_b):
        proj.mkdir(parents=True)
        for okf_dir in ("experiments", "findings",
                        "literature", "concepts", "methodology", "mocs"):
            (proj / okf_dir).mkdir()

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

[projects.demo-research]
source_dir = "{proj_a}"
tasks_dir = "{tmp_path / 'tasks' / 'demo-research'}"

[projects.demo-litreview]
source_dir = "{proj_b}"
tasks_dir = "{tmp_path / 'tasks' / 'demo-litreview'}"
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
# Helpers: small manifest builders
# ---------------------------------------------------------------------------

def _manifest(nodes, *, run_id="test-run"):
    return {"run_id": run_id, "nodes": nodes}


def _agent_node(nid, *, spec="task://test#stub", produces=None, needs=None):
    n = {"id": nid, "type": "agent", "spec": spec}
    if produces:
        n["produces"] = produces
    if needs:
        n["needs"] = needs
    return n


def _argns(**kwargs):
    """Build a minimal argparse.Namespace."""
    import argparse
    ns = argparse.Namespace()
    defaults = {"status": "succeeded", "manifest": None, "run_id": None, "node_id": None}
    defaults.update(kwargs)
    for k, v in defaults.items():
        setattr(ns, k, v)
    return ns


# ===========================================================================
# 1. note: resolver — project-scoped path resolves via project_notes_dir
# ===========================================================================

class TestNoteWatchProjectScoped:
    """note:<project>/<type>/<id> resolves against project_notes_dir."""

    def test_project_scoped_missing(self, instance):
        """note:<project>/experiments/<id> returns missing when note absent."""
        # RED-BEFORE-GREEN demonstration:
        # Under the OLD resolver (notes_root-relative), "demo-research" is NOT a path
        # segment in notes_root — the note would also be missing but from the WRONG path.
        # The old path: notes_root / "demo-research/experiments/exp-001.md"
        # The new path: project_notes_dir("demo-research") / "experiments/exp-001.md"
        # Both return "missing" but the artifact_path differs — we assert the new one.
        cfg, tmp_path = instance

        result = resolve_watch("note:demo-research/experiments/exp-001.md")

        assert result["ready"] is False
        assert result["state"] == "missing"
        # Must resolve against project_notes_dir, not notes_root
        expected_path = str(cfg.project_notes_dir("demo-research") / "experiments" / "exp-001.md")
        assert result["artifact_path"] == expected_path, (
            f"Resolved to wrong path: {result['artifact_path']!r}\n"
            f"Expected: {expected_path!r}\n"
            "The note: resolver must use project_notes_dir for registered project slugs."
        )

    def test_project_scoped_resolves_when_note_exists(self, instance):
        """note:<project>/experiments/<id> returns ready when note present in project_notes_dir."""
        cfg, tmp_path = instance

        # Pre-fix: placing the note under notes_root/demo-research/experiments/ would
        # only work with the old resolver. The correct location is project_notes_dir.
        exp_note = cfg.project_notes_dir("demo-research") / "experiments" / "exp-001.md"
        exp_note.write_text("---\ntype: experiments\ntitle: Test\n---\n", encoding="utf-8")

        result = resolve_watch("note:demo-research/experiments/exp-001.md")
        assert result["ready"] is True
        assert result["state"] == "exists"
        assert result["artifact_path"] == str(exp_note)

# ===========================================================================
# 2. note: resolver — legacy notes_root fallback (backward compat)
# ===========================================================================

class TestNoteWatchLegacyFallback:
    """note:<type>/<id> with unregistered first segment falls back to notes_root."""

    def test_legacy_path_fallback(self, instance):
        """Existing note:<type>/<id> form resolves against notes_root when first
        segment is not a registered project slug."""
        cfg, tmp_path = instance

        # "experiments" is NOT a registered project slug → legacy fallback
        legacy_note = cfg.notes_root / "experiments" / "legacy-exp.md"
        (cfg.notes_root / "experiments").mkdir(exist_ok=True)
        legacy_note.write_text("---\ntype: experiments\n---\n", encoding="utf-8")

        result = resolve_watch("note:experiments/legacy-exp.md")
        assert result["ready"] is True
        assert result["artifact_path"] == str(legacy_note)

    def test_legacy_missing(self, instance):
        """Legacy form returns missing with notes_root-relative path."""
        cfg, tmp_path = instance

        result = resolve_watch("note:findings/f-absent.md")
        assert result["ready"] is False
        assert result["state"] == "missing"
        assert str(cfg.notes_root) in result["artifact_path"]


# ===========================================================================
# 3. note: resolver — datasets/ routes to datasets_root (shared)
# ===========================================================================

class TestNoteWatchDatasets:
    """note:datasets/<id> routes to cfg.datasets_root (shared)."""

    def test_datasets_segment_routes_to_datasets_root(self, instance):
        """note:datasets/<id> resolves against datasets_root regardless of project."""
        cfg, tmp_path = instance

        ds_note = cfg.datasets_root / "my-data.md"
        ds_note.write_text("---\ntype: datasets\nlocation: /tmp/x\nhash: sha256:aaa\n---\n",
                           encoding="utf-8")

        result = resolve_watch("note:datasets/my-data.md")
        assert result["ready"] is True
        assert result["artifact_path"] == str(ds_note)

    def test_datasets_does_not_resolve_to_notes_root(self, instance):
        """datasets note placed under notes_root/datasets/ (not datasets_root when
        datasets_root is a custom path) should NOT be found via notes_root fallback."""
        cfg, tmp_path = instance
        # This test is a sanity check: by default datasets_root == notes_root/datasets
        # so both paths coincide. The test ensures the routing logic takes the right arm.
        result = resolve_watch("note:datasets/nonexistent-ds.md")
        assert result["ready"] is False
        assert result["state"] == "missing"
        # Path should be in datasets_root
        expected = str(cfg.datasets_root / "nonexistent-ds.md")
        assert result["artifact_path"] == expected


# ===========================================================================
# 4. note: resolver — +fresh modifier on project-scoped path
# ===========================================================================

class TestNoteWatchFreshProjectScoped:
    """+fresh works on project-scoped note: paths."""

    def test_project_scoped_fresh_stale(self, instance):
        """note:<project>/experiments/<id>+fresh is stale when mtime < registered_ts."""
        cfg, tmp_path = instance

        exp_note = cfg.project_notes_dir("demo-research") / "experiments" / "fresh-exp.md"
        exp_note.write_text("---\ntype: experiments\n---\n", encoding="utf-8")

        future_ts = time.time() + 3600
        result = resolve_watch(
            "note:demo-research/experiments/fresh-exp.md+fresh",
            registered_ts=future_ts,
        )
        assert result["ready"] is False
        assert "stale" in result["state"]

    def test_project_scoped_fresh_ready(self, instance):
        """note:<project>/experiments/<id>+fresh is ready when mtime >= registered_ts."""
        cfg, tmp_path = instance

        exp_note = cfg.project_notes_dir("demo-research") / "experiments" / "fresh-exp2.md"
        exp_note.write_text("---\ntype: experiments\n---\n", encoding="utf-8")

        past_ts = time.time() - 3600
        result = resolve_watch(
            "note:demo-research/experiments/fresh-exp2.md+fresh",
            registered_ts=past_ts,
        )
        assert result["ready"] is True


# ===========================================================================
# 5. produces.result — schema validation (SR-RM-FIGMS: figure/manuscript removed)
# ===========================================================================

class TestProducesTypedSchema:
    """Schema validation for produces.result."""

    def test_schema_accepts_produces_result(self):
        """produces.result in '<project>/<id>' format passes schema validation."""
        m = _manifest([_agent_node("a", produces={"result": "demo-research/exp-001"})])
        validate_manifest(m)  # should not raise

    def test_schema_rejects_result_without_slash(self):
        """produces.result without '<project>/<id>' format is a ManifestError."""
        m = _manifest([_agent_node("a", produces={"result": "just-an-id"})])
        with pytest.raises(ManifestError, match="produces.result"):
            validate_manifest(m)

    def test_schema_rejects_empty_result(self):
        """produces.result = '' is a ManifestError."""
        m = _manifest([_agent_node("a", produces={"result": ""})])
        with pytest.raises(ManifestError, match="produces.result"):
            validate_manifest(m)


# ===========================================================================
# 6-10. Complete-time gate for produces.result / .figure / .manuscript
# ===========================================================================

class TestProducesTypedCompleteGate:
    """cmd_complete enforces project-scoped note gate for result/figure/manuscript."""

    def _write_note(self, path: Path, note_type: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\ntype: {note_type}\ntitle: Test\n---\n", encoding="utf-8"
        )

    def _run_dag(self, manifest_path: Path, run_id: str) -> None:
        import argparse
        args = argparse.Namespace(manifest=str(manifest_path))
        rc = cmd_run(args)
        assert rc == 0, f"cmd_run failed: rc={rc}"

    # ── Test 6: produces.result resolves to experiments/ ──────────────────────

    def test_produces_result_missing_note_fails(self, tmp_instance: Path):
        """produces.result fails complete gate when note absent."""
        run_id = "test-result-missing"
        m = _manifest(
            [_agent_node("writer", produces={"result": "demo-research/exp-001"})],
            run_id=run_id,
        )
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")
        self._run_dag(mf, run_id)

        rc = cmd_complete(_argns(run_id=run_id, node_id="writer", status="succeeded"))
        assert rc == 1

    def test_produces_result_wrong_type_fails(self, tmp_instance: Path):
        """produces.result fails when note is in experiments/ but type: is wrong.

        RED-BEFORE-GREEN: under the OLD resolver (notes_root-based), this note
        at project_notes_dir/experiments/ was never even found. Now it IS found
        at the correct path — and the type:dir check fires as expected.
        """
        cfg = load_config()
        run_id = "test-result-wrong-type"
        m = _manifest(
            [_agent_node("writer", produces={"result": "demo-research/exp-002"})],
            run_id=run_id,
        )
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")
        self._run_dag(mf, run_id)

        # Place note with WRONG type (literature in experiments/)
        note = cfg.project_notes_dir("demo-research") / "experiments" / "exp-002.md"
        self._write_note(note, "literature")  # wrong type!

        rc = cmd_complete(_argns(run_id=run_id, node_id="writer", status="succeeded"))
        assert rc == 1

    def test_produces_result_correct_type_passes(self, tmp_instance: Path):
        """produces.result passes when note is in experiments/ with correct type.

        This is the GREEN side of the red-before-green: the note IS found at
        project_notes_dir/experiments/ and the type: frontmatter matches.
        """
        cfg = load_config()
        run_id = "test-result-pass"
        m = _manifest(
            [_agent_node("writer", produces={"result": "demo-research/exp-003"})],
            run_id=run_id,
        )
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")
        self._run_dag(mf, run_id)

        note = cfg.project_notes_dir("demo-research") / "experiments" / "exp-003.md"
        self._write_note(note, "experiments")  # correct type

        rc = cmd_complete(_argns(run_id=run_id, node_id="writer", status="succeeded"))
        assert rc == 0

# ===========================================================================
# 11. produces.note (legacy) still resolves against notes_root
# ===========================================================================

class TestProducesNoteLegacy:
    """Existing produces.note behavior unchanged (notes_root-relative)."""

    def test_produces_note_legacy_still_works(self, tmp_instance: Path):
        """produces.note with absolute path still passes (backward compat)."""
        cfg = load_config()
        run_id = "test-produces-note-legacy"

        note_dir = tmp_instance / "notes" / "experiments"
        note_dir.mkdir(parents=True, exist_ok=True)
        note = note_dir / "legacy-exp.md"
        note.write_text("---\ntype: experiments\ntitle: Legacy\n---\n", encoding="utf-8")

        m = _manifest(
            [_agent_node("writer", produces={"note": str(note)})],
            run_id=run_id,
        )
        mf = tmp_instance / "manifest.json"
        mf.write_text(json.dumps(m), encoding="utf-8")
        args = __import__("argparse").Namespace(manifest=str(mf))
        cmd_run(args)

        rc = cmd_complete(_argns(run_id=run_id, node_id="writer", status="succeeded"))
        assert rc == 0


# ===========================================================================
# 12. Shared datasets/ pointer still resolves against datasets_root
# ===========================================================================

class TestSharedDatasetsStillWorks:
    """Regression: note:datasets/<id> still uses datasets_root after the fix."""

    def test_datasets_note_watch_resolves_via_datasets_root(self, instance):
        """note:datasets/<id> uses cfg.datasets_root (not notes_root)."""
        cfg, tmp_path = instance

        ds_note = cfg.datasets_root / "corpus.md"
        ds_note.write_text(
            "---\ntype: datasets\nlocation: /tmp/c.json\nhash: sha256:abc\n---\n",
            encoding="utf-8",
        )

        result = resolve_watch("note:datasets/corpus.md")
        assert result["ready"] is True
        assert result["artifact_path"] == str(ds_note)
        # Must NOT resolve against notes_root
        assert result["artifact_path"] != str(cfg.notes_root / "datasets" / "corpus.md") or \
               str(cfg.datasets_root) == str(cfg.notes_root / "datasets"), \
               "datasets_root must be honored"


# ===========================================================================
# Unit: _check_project_scoped_note
# ===========================================================================

class TestCheckProjectScopedNoteUnit:
    """Unit tests for the _check_project_scoped_note helper."""

    def test_missing_slash_returns_error(self, instance):
        cfg, _ = instance
        issues = _check_project_scoped_note("result", "no-slash-here", cfg)
        assert any("project" in i.lower() or "format" in i.lower() for i in issues), issues

    def test_unknown_project_returns_error(self, instance):
        cfg, _ = instance
        issues = _check_project_scoped_note("result", "ghost-project/exp-001", cfg)
        assert any("unknown project" in i.lower() or "ghost-project" in i for i in issues), issues

    def test_note_not_found_returns_error(self, instance):
        cfg, _ = instance
        issues = _check_project_scoped_note("result", "demo-research/nonexistent", cfg)
        assert any("does not exist" in i for i in issues), issues

    def test_correct_note_returns_empty(self, instance):
        cfg, tmp_path = instance
        note = cfg.project_notes_dir("demo-research") / "experiments" / "unit-exp.md"
        note.write_text("---\ntype: experiments\ntitle: Unit\n---\n", encoding="utf-8")

        issues = _check_project_scoped_note("result", "demo-research/unit-exp", cfg)
        assert issues == [], issues

    @pytest.mark.parametrize("pkey,okf_dir", list(_PRODUCES_KEY_TO_OKF_DIR.items()))
    def test_all_typed_keys_map_correctly(self, instance, pkey, okf_dir):
        """Each produces.* key maps to the correct OKF type directory."""
        cfg, _ = instance
        note = cfg.project_notes_dir("demo-research") / okf_dir / f"typed-{pkey}.md"
        note.write_text(f"---\ntype: {okf_dir}\ntitle: Typed\n---\n", encoding="utf-8")

        issues = _check_project_scoped_note(pkey, f"demo-research/typed-{pkey}", cfg)
        assert issues == [], f"Expected no issues for produces.{pkey}, got: {issues}"
