"""test_sr8.py — SR-8: DATASETS as typed OKF artifact + data-processing integration seams.

All hermetic (tmp_instance). No ~/vault reads or writes.

Four seams tested:
  1. OKF type `datasets` — 7th canonical type; cross-project SHARED (datasets_root, not
     project_notes_dir); cmd_new/list/check all use cfg.datasets_root.
  2. DAG `produces: {dataset: …}` — schema validates; complete-time gate (exists + hash).
  3. Resolver predicate `dataset:<id>` — resolve_watch returns ready only when note+hash valid.
  4. Walker/frontier path — finding node cannot enter frontier until dataset:<id> resolves.

Amendment (2026-07-01) changes:
  - Config key `datasets_root` (default: notes_root/datasets, overridable).
  - datasets notes are SHARED (datasets_root), not project-scoped (project_notes_dir).
  - _verify_local_file_hash uses streaming chunked read (no full-file RAM load).
  - Cross-project test: two projects share the same dataset note at datasets_root.

All agent nodes in test manifests carry `spec:` and `reads:` (SR-DISP/SR-SCOPE compliance).
"""

import hashlib
import json
import time

import pytest
from pathlib import Path

from research_vault import note as note_mod
from research_vault.config import load_config
from research_vault.dag.schema import validate_manifest, ManifestError
from research_vault.dag.walker import compute_frontier
from research_vault.wait_for import resolve_watch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _write_dataset_note(
    datasets_root: Path,
    note_id: str,
    *,
    location: str = "",
    hash_val: str = "",
    title: str = "Test dataset",
    description: str = "",
) -> Path:
    """Write a datasets provenance note DIRECTLY in datasets_root/. Returns path.

    datasets_root is cfg.datasets_root — the shared cross-project root.
    Notes live at datasets_root/<note_id>.md (no subdirectory within datasets_root).
    """
    datasets_root.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: datasets", f"title: {title}", "created: 2026-07-01"]
    if location:
        lines.append(f"location: {location}")
    if hash_val:
        lines.append(f"hash: {hash_val}")
    if description:
        lines.append(f"description: {description}")
    lines += ["---", "", "<!-- provenance note -->", ""]
    p = datasets_root / f"{note_id}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _minimal_manifest(nodes: list[dict]) -> dict:
    """Build a minimal valid manifest dict."""
    return {"run_id": "test-run", "nodes": nodes}


def _agent_node(nid: str, **kwargs) -> dict:
    """Build a minimal valid agent node (spec + reads required)."""
    node: dict = {
        "id": nid,
        "type": "agent",
        "spec": f"task://test#{nid}",
        "reads": [f"tasks/test.md#{nid}"],
    }
    node.update(kwargs)
    return node


# ============================================================================
# Config: datasets_root
# ============================================================================

class TestDatasetsRootConfig:
    """datasets_root is a first-class config key that defaults to notes_root/datasets."""

    def test_datasets_root_defaults_to_notes_root_slash_datasets(self, tmp_instance):
        """When not set in TOML, datasets_root = notes_root / 'datasets'."""
        cfg = load_config(reload=True)
        assert cfg.datasets_root == cfg.notes_root / "datasets"

    def test_datasets_root_overridable_in_toml(self, tmp_path):
        """A custom datasets_root path in TOML is respected."""
        import os
        shared_dir = tmp_path / "shared-data"
        config_file = tmp_path / "research_vault.toml"
        config_file.write_text(
            f'instance_root = "{tmp_path}"\n'
            f'notes_root = "{tmp_path / "notes"}"\n'
            f'state_dir = "{tmp_path / "state"}"\n'
            f'agents_dir = "{tmp_path / ".agents"}"\n'
            f'tasks_dir = "{tmp_path / "tasks"}"\n'
            f'control_dir = "{tmp_path / "control"}"\n'
            f'datasets_root = "{shared_dir}"\n',
            encoding="utf-8",
        )
        old = os.environ.get("RESEARCH_VAULT_CONFIG")
        os.environ["RESEARCH_VAULT_CONFIG"] = str(config_file)
        try:
            cfg = load_config(reload=True)
            assert cfg.datasets_root == shared_dir
        finally:
            if old is None:
                os.environ.pop("RESEARCH_VAULT_CONFIG", None)
            else:
                os.environ["RESEARCH_VAULT_CONFIG"] = old
            load_config(reload=True)

    def test_datasets_root_is_path_object(self, tmp_instance):
        """cfg.datasets_root is a Path, not a string."""
        cfg = load_config(reload=True)
        assert isinstance(cfg.datasets_root, Path)


# ============================================================================
# Seam 1: OKF type `datasets` — shared, cross-project
# ============================================================================

class TestDatasetsOkfType:
    """datasets is the 7th canonical OKF type, shared across projects via datasets_root."""

    def test_datasets_in_okf_types(self):
        """'datasets' must be in the OKF_TYPES frozenset."""
        assert "datasets" in note_mod.OKF_TYPES

    def test_okf_type_count_is_eight(self):
        """OKF_TYPES has exactly 8 types (SR-RM-FIGMS removes figures + manuscript).

        Updated 8→9 when SR-MS-1a added manuscript; updated 9→10 when SR-LR-2 added
        gaps; reduced 10→8 by SR-RM-FIGMS (removed figures and manuscript).
        datasets + concepts are SHARED types (0.3.2 moved concepts to shared);
        gaps/methodology/experiments/findings/mocs are PROJECT-SCOPED; literature
        is two-layer. "methods" was renamed "methodology" in 0.3.2.
        """
        assert len(note_mod.OKF_TYPES) == 8
        expected = {
            "literature", "concepts", "methodology", "experiments",
            "findings", "mocs", "datasets", "gaps",
        }
        assert note_mod.OKF_TYPES == expected

    def test_new_dataset_note_creates_in_datasets_root(self, tmp_instance):
        """cmd_new for datasets writes to cfg.datasets_root, NOT project_notes_dir."""
        cfg = load_config(reload=True)
        path = note_mod.cmd_new("demo-research", "datasets", "My provenance note", config=cfg)
        assert path.exists()
        # Must be in datasets_root, not in project_notes_dir
        assert path.parent == cfg.datasets_root
        # Must NOT be in project_notes_dir
        project_dir = cfg.project_notes_dir("demo-research")
        assert not path.is_relative_to(project_dir), (
            f"datasets note must not be in project_notes_dir; got {path}"
        )

    def test_new_dataset_note_has_type_frontmatter(self, tmp_instance):
        """New datasets note has type: datasets in frontmatter."""
        cfg = load_config(reload=True)
        path = note_mod.cmd_new("demo-research", "datasets", "Data note", config=cfg)
        content = path.read_text()
        assert "type: datasets" in content

    def test_new_dataset_note_has_location_and_hash_fields(self, tmp_instance):
        """New datasets note template includes location and hash placeholder fields."""
        cfg = load_config(reload=True)
        path = note_mod.cmd_new("demo-research", "datasets", "Data note", config=cfg)
        content = path.read_text()
        assert "location:" in content
        assert "hash:" in content

    def test_check_dataset_note_ok(self, tmp_instance):
        """A well-formed datasets note passes cmd_check (type + location + hash present)."""
        cfg = load_config(reload=True)
        data_file = Path(tmp_instance) / "mydata.csv"
        data_file.write_bytes(b"col1,col2\n1,2\n")
        h = _sha256_hex(data_file.read_bytes())
        _write_dataset_note(
            cfg.datasets_root,
            "check-ok",
            location=str(data_file),
            hash_val=h,
            description="A tiny 2-row CSV fixture for the check-ok test.",
        )
        violations = note_mod.cmd_check("demo-research", config=cfg)
        assert violations == [], f"Expected no violations, got: {violations}"

    def test_check_dataset_note_fails_missing_location(self, tmp_instance):
        """A datasets note without a location field fails cmd_check."""
        cfg = load_config(reload=True)
        _write_dataset_note(
            cfg.datasets_root,
            "no-location",
            location="",   # missing
            hash_val="sha256:abc123",
        )
        violations = note_mod.cmd_check("demo-research", config=cfg)
        assert any("location" in v for v in violations), (
            f"Expected a 'location' violation, got: {violations}"
        )

    def test_check_dataset_note_fails_missing_hash(self, tmp_instance):
        """A datasets note without a hash field fails cmd_check."""
        cfg = load_config(reload=True)
        _write_dataset_note(
            cfg.datasets_root,
            "no-hash",
            location="/some/path.csv",
            hash_val="",   # missing
        )
        violations = note_mod.cmd_check("demo-research", config=cfg)
        assert any("hash" in v for v in violations), (
            f"Expected a 'hash' violation, got: {violations}"
        )

    def test_check_dataset_scans_datasets_root_not_project_dir(self, tmp_instance):
        """cmd_check for datasets scans datasets_root, not project_notes_dir.

        Non-vacuous discriminant: we write an INVALID note (missing hash) to
        datasets_root/ and a VALID note to the old project_notes_dir/datasets/.
        If cmd_check binds to datasets_root, it finds the invalid note → hash
        violation detected.  If it regressed to project-scoped, it finds only
        the valid stale.md → violations == [] → this test fails.
        """
        cfg = load_config(reload=True)

        # File a VALID note in project_notes_dir/datasets/ (old wrong place)
        # — a regressed impl would scan here and see a clean note, yielding [].
        old_dir = cfg.project_notes_dir("demo-research") / "datasets"
        old_dir.mkdir(parents=True, exist_ok=True)
        (old_dir / "stale.md").write_text(
            "---\ntype: datasets\ntitle: stale\ncreated: 2026-07-01\n"
            "location: /x\nhash: sha256:abc\n---\n",
            encoding="utf-8",
        )

        # File an INVALID note (no hash) in datasets_root/ — cmd_check must find
        # and flag it.  A correct impl yields a hash violation; a regressed impl
        # (scanning project dir instead) yields [] and fails this assertion.
        _write_dataset_note(
            cfg.datasets_root, "no-hash-shared",
            location="https://example.com/data.csv",
            hash_val="",   # deliberately invalid
        )

        violations = note_mod.cmd_check("demo-research", config=cfg)

        # Must detect the missing hash in datasets_root/ (proves it was scanned).
        assert any("hash" in v for v in violations), (
            f"Expected a hash violation from datasets_root scan; got: {violations}"
        )
        # Must NOT flag stale.md from project_notes_dir (proves that dir was not scanned).
        assert not any("stale" in v for v in violations), (
            f"Should not scan project_notes_dir for datasets; got: {violations}"
        )

    def test_datasets_note_visible_across_projects(self, tmp_instance):
        """A datasets note in datasets_root is visible to cmd_check for ANY project.

        Non-vacuous discriminant: we write an INVALID note (missing location) to
        datasets_root/ once.  Both projects must detect the violation, proving they
        both scan the shared datasets_root.  If cmd_check were project-scoped, each
        project would scan its own (empty) project_notes_dir → violations == [] for
        both → both assertions below would fail.
        """
        cfg = load_config(reload=True)

        # File an INVALID note (no location) at datasets_root/ once.
        _write_dataset_note(
            cfg.datasets_root, "shared-corpus",
            location="",                     # deliberately invalid
            hash_val="sha256:abc123def456",
        )

        violations_p1 = note_mod.cmd_check("demo-research", config=cfg)
        violations_p2 = note_mod.cmd_check("demo-litreview", config=cfg)

        # Both projects must detect the missing-location violation in datasets_root.
        assert any("location" in v for v in violations_p1), (
            f"demo-research should flag missing location in shared note; got: {violations_p1}"
        )
        assert any("location" in v for v in violations_p2), (
            f"demo-litreview should flag missing location in shared note; got: {violations_p2}"
        )


# ============================================================================
# Seam 2: DAG produces.dataset — schema + complete-time gate
# ============================================================================

class TestProducesDatasetSchema:
    """Schema validates produces.dataset on agent nodes."""

    def test_schema_accepts_produces_dataset(self):
        """A manifest with produces.dataset (non-empty string) passes validation."""
        m = _minimal_manifest([
            _agent_node("data-step", produces={"dataset": "my-data.md"}),
        ])
        validate_manifest(m)  # must not raise

    def test_schema_rejects_empty_produces_dataset(self):
        """produces.dataset = empty string is a ManifestError."""
        m = _minimal_manifest([
            _agent_node("data-step", produces={"dataset": ""}),
        ])
        with pytest.raises(ManifestError, match="produces.dataset"):
            validate_manifest(m)

    def test_schema_rejects_non_string_produces_dataset(self):
        """produces.dataset must be a string, not a dict/int."""
        m = _minimal_manifest([
            _agent_node("data-step", produces={"dataset": 123}),
        ])
        with pytest.raises(ManifestError, match="produces.dataset"):
            validate_manifest(m)

    def test_schema_accepts_produces_note_and_dataset_together(self):
        """A node may declare both produces.note and produces.dataset."""
        m = _minimal_manifest([
            _agent_node(
                "data-step",
                produces={
                    "note": "experiments/exp-001.md",
                    "dataset": "my-data.md",
                },
            ),
        ])
        validate_manifest(m)  # must not raise


class TestCompleteProducesDatasetGate:
    """dag complete: produces.dataset gate checks note+location+hash at complete-time.

    The produces.dataset value is the note filename (e.g. 'my-data.md') resolved
    against cfg.datasets_root (the shared cross-project datasets store).
    """

    def _make_run(self, tmp_instance, manifest: dict):
        """Helper: create a run state for a manifest. Returns (store, run_state, cfg)."""
        from research_vault.dag.store import RunStore, RunState
        import time as _time
        cfg = load_config(reload=True)
        store = RunStore.from_config(cfg)
        run_state = RunState(
            run_id=manifest["run_id"],
            manifest_path=str(tmp_instance / "manifest.json"),
            created_at=_time.time(),
        )
        run_state.init_nodes(manifest)
        mf_path = Path(tmp_instance) / "manifest.json"
        mf_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        store.create(run_state)
        return store, run_state, cfg

    def test_complete_produces_dataset_fails_note_missing(self, tmp_instance):
        """dag complete with produces.dataset fails when the provenance note doesn't exist."""
        from research_vault.dag import verbs as dag_verbs
        import argparse

        manifest = _minimal_manifest([
            _agent_node("data-step", produces={"dataset": "missing.md"}),
        ])
        store, run_state, cfg = self._make_run(tmp_instance, manifest)

        args = argparse.Namespace(run_id="test-run", node_id="data-step", status="succeeded")
        rc = dag_verbs.cmd_complete(args)
        assert rc != 0, "Should fail when dataset provenance note is missing"

    def test_complete_produces_dataset_fails_location_missing(self, tmp_instance):
        """dag complete fails when dataset provenance note has no location field."""
        from research_vault.dag import verbs as dag_verbs
        import argparse

        cfg = load_config(reload=True)
        _write_dataset_note(cfg.datasets_root, "no-loc", location="", hash_val="sha256:abc")

        manifest = _minimal_manifest([
            _agent_node("data-step", produces={"dataset": "no-loc.md"}),
        ])
        store, run_state, _ = self._make_run(tmp_instance, manifest)

        args = argparse.Namespace(run_id="test-run", node_id="data-step", status="succeeded")
        rc = dag_verbs.cmd_complete(args)
        assert rc != 0, "Should fail when dataset note missing location"

    def test_complete_produces_dataset_fails_hash_missing(self, tmp_instance):
        """dag complete fails when dataset provenance note has no hash field."""
        from research_vault.dag import verbs as dag_verbs
        import argparse

        cfg = load_config(reload=True)
        _write_dataset_note(cfg.datasets_root, "no-hash", location="/tmp/data.csv", hash_val="")

        manifest = _minimal_manifest([
            _agent_node("data-step", produces={"dataset": "no-hash.md"}),
        ])
        store, run_state, _ = self._make_run(tmp_instance, manifest)

        args = argparse.Namespace(run_id="test-run", node_id="data-step", status="succeeded")
        rc = dag_verbs.cmd_complete(args)
        assert rc != 0, "Should fail when dataset note missing hash"

    def test_complete_produces_dataset_fails_hash_mismatch(self, tmp_instance):
        """dag complete FAILS (NOT-done) when the recorded hash mismatches the actual file.

        This is the critical NOT-done test: a node with a filed provenance note but the
        wrong hash cannot be marked complete — the gate catches the mismatch.
        """
        from research_vault.dag import verbs as dag_verbs
        import argparse

        cfg = load_config(reload=True)
        data_file = Path(tmp_instance) / "data.csv"
        data_file.write_bytes(b"col1,col2\n1,2\n")
        wrong_hash = "sha256:" + "deadbeef" * 8
        _write_dataset_note(
            cfg.datasets_root, "hash-mismatch",
            location=str(data_file),
            hash_val=wrong_hash,
        )

        manifest = _minimal_manifest([
            _agent_node("data-step", produces={"dataset": "hash-mismatch.md"}),
        ])
        store, run_state, _ = self._make_run(tmp_instance, manifest)

        args = argparse.Namespace(run_id="test-run", node_id="data-step", status="succeeded")
        rc = dag_verbs.cmd_complete(args)
        assert rc != 0, "Should fail when recorded hash mismatches actual file hash"

    def test_complete_produces_dataset_passes_with_valid_local_file(self, tmp_instance):
        """dag complete succeeds when note + location + hash are all valid (local file)."""
        from research_vault.dag import verbs as dag_verbs
        import argparse

        cfg = load_config(reload=True)
        data_bytes = b"col1,col2\n1,2\n3,4\n"
        data_file = Path(tmp_instance) / "good-data.csv"
        data_file.write_bytes(data_bytes)
        correct_hash = _sha256_hex(data_bytes)

        _write_dataset_note(
            cfg.datasets_root, "good-data",
            location=str(data_file),
            hash_val=correct_hash,
        )

        manifest = _minimal_manifest([
            _agent_node("data-step", produces={"dataset": "good-data.md"}),
        ])
        store, run_state, _ = self._make_run(tmp_instance, manifest)

        args = argparse.Namespace(run_id="test-run", node_id="data-step", status="succeeded")
        rc = dag_verbs.cmd_complete(args)
        assert rc == 0, "Should succeed when note + location + hash are valid"

    def test_complete_produces_dataset_passes_url_location(self, tmp_instance):
        """dag complete succeeds for a URL-location dataset (hash recorded, no file check)."""
        from research_vault.dag import verbs as dag_verbs
        import argparse

        cfg = load_config(reload=True)
        _write_dataset_note(
            cfg.datasets_root, "url-data",
            location="https://example.com/data.csv",
            hash_val="sha256:abc123def456",
        )

        manifest = _minimal_manifest([
            _agent_node("data-step", produces={"dataset": "url-data.md"}),
        ])
        store, run_state, _ = self._make_run(tmp_instance, manifest)

        args = argparse.Namespace(run_id="test-run", node_id="data-step", status="succeeded")
        rc = dag_verbs.cmd_complete(args)
        assert rc == 0, "Should succeed for URL-location dataset (hash recorded)"


# ============================================================================
# Seam 3: Resolver predicate `dataset:<id>`
# ============================================================================

class TestDatasetResolver:
    """resolve_watch('dataset:<id>') gate: note exists + hash + location valid.

    All notes written to cfg.datasets_root (the shared root).
    """

    def test_dataset_resolver_not_ready_when_note_missing(self, tmp_instance):
        """dataset: returns not-ready when the provenance note doesn't exist.

        Non-vacuous: verify the 'ready' key is False, not just truthy-false.
        """
        result = resolve_watch("dataset:nonexistent")
        assert result["ready"] is False
        assert result.get("state") is not None  # some diagnostic state

    def test_dataset_resolver_not_ready_when_hash_missing(self, tmp_instance):
        """dataset: returns not-ready when the provenance note has no hash field.

        Non-vacuous: explicitly distinct from 'note missing' case — the note
        exists but is incomplete, which is a different failure mode.
        """
        cfg = load_config(reload=True)
        _write_dataset_note(cfg.datasets_root, "no-hash-resolver", location="/tmp/x.csv", hash_val="")
        result = resolve_watch("dataset:no-hash-resolver")
        assert result["ready"] is False
        assert "hash" in (result.get("state", "") + str(result.get("error", "")))

    def test_dataset_resolver_not_ready_when_location_missing(self, tmp_instance):
        """dataset: returns not-ready when the provenance note has no location field.

        Non-vacuous: location absence is distinct from hash absence.
        """
        cfg = load_config(reload=True)
        _write_dataset_note(cfg.datasets_root, "no-loc-resolver", location="", hash_val="sha256:abc")
        result = resolve_watch("dataset:no-loc-resolver")
        assert result["ready"] is False
        assert "location" in (result.get("state", "") + str(result.get("error", "")))

    def test_dataset_resolver_not_ready_when_file_hash_mismatch(self, tmp_instance):
        """dataset: returns not-ready when the recorded hash mismatches the local file.

        Non-vacuous: the note exists and has BOTH fields but the hash is wrong —
        a corrupt/swapped artifact. This is distinct from missing fields.
        """
        cfg = load_config(reload=True)
        data_file = Path(tmp_instance) / "mismatch.csv"
        data_file.write_bytes(b"real,data\n1,2\n")
        wrong_hash = "sha256:" + "0" * 64

        _write_dataset_note(
            cfg.datasets_root, "hash-mismatch-resolver",
            location=str(data_file),
            hash_val=wrong_hash,
        )
        result = resolve_watch("dataset:hash-mismatch-resolver")
        assert result["ready"] is False
        assert "mismatch" in result.get("state", "")

    def test_dataset_resolver_ready_with_matching_local_file(self, tmp_instance):
        """dataset: returns ready when note + local file + correct hash all match."""
        cfg = load_config(reload=True)
        data_bytes = b"a,b,c\n1,2,3\n"
        data_file = Path(tmp_instance) / "match.csv"
        data_file.write_bytes(data_bytes)
        correct_hash = _sha256_hex(data_bytes)

        _write_dataset_note(
            cfg.datasets_root, "hash-match-resolver",
            location=str(data_file),
            hash_val=correct_hash,
        )
        result = resolve_watch("dataset:hash-match-resolver")
        assert result["ready"] is True, f"Expected ready, got: {result}"

    def test_dataset_resolver_ready_with_url_location(self, tmp_instance):
        """dataset: returns ready for URL-location when note has hash (no file check)."""
        cfg = load_config(reload=True)
        _write_dataset_note(
            cfg.datasets_root, "url-resolver",
            location="https://zenodo.org/record/123/data.csv",
            hash_val="sha256:abc123def456abc123def456abc123def456abc123def456abc123def456abc1",
        )
        result = resolve_watch("dataset:url-resolver")
        assert result["ready"] is True, f"Expected ready for URL location, got: {result}"

    def test_dataset_resolver_ready_with_doi_location(self, tmp_instance):
        """dataset: returns ready for DOI-location when note has hash (no file check)."""
        cfg = load_config(reload=True)
        _write_dataset_note(
            cfg.datasets_root, "doi-resolver",
            location="doi:10.5281/zenodo.12345",
            hash_val="sha256:def456abc123def456abc123def456abc123def456abc123def456abc123def4",
        )
        result = resolve_watch("dataset:doi-resolver")
        assert result["ready"] is True, f"Expected ready for DOI location, got: {result}"

    def test_dataset_in_known_prefixes(self, tmp_instance):
        """'dataset:' must be in the _KNOWN_PREFIXES tuple in wait_for.run().

        Checked via AST inspection of the tuple literal — a comment mentioning
        'dataset:' cannot rescue a missing tuple entry (AST carries no comment
        text, only live code values).
        """
        import ast, inspect, textwrap
        from research_vault import wait_for as wf_mod

        src = textwrap.dedent(inspect.getsource(wf_mod.run))
        tree = ast.parse(src)
        known_prefixes: list | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "_KNOWN_PREFIXES":
                        known_prefixes = [
                            elt.value
                            for elt in node.value.elts
                            if isinstance(elt, ast.Constant)
                            and isinstance(elt.value, str)
                        ]
        assert known_prefixes is not None, (
            "_KNOWN_PREFIXES assignment not found in wait_for.run()"
        )
        assert "dataset:" in known_prefixes, (
            "'dataset:' not in _KNOWN_PREFIXES — rv wait-for will reject dataset: watches"
        )

    def test_note_prefix_in_known_prefixes(self, tmp_instance):
        """'note:' must be in the _KNOWN_PREFIXES tuple in wait_for.run() (omitted pre-SR-8).

        Checked via AST inspection of the tuple literal — comment-free by
        construction, so a note in a docstring cannot produce a false pass.
        """
        import ast, inspect, textwrap
        from research_vault import wait_for as wf_mod

        src = textwrap.dedent(inspect.getsource(wf_mod.run))
        tree = ast.parse(src)
        known_prefixes: list | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "_KNOWN_PREFIXES":
                        known_prefixes = [
                            elt.value
                            for elt in node.value.elts
                            if isinstance(elt, ast.Constant)
                            and isinstance(elt.value, str)
                        ]
        assert known_prefixes is not None, (
            "_KNOWN_PREFIXES assignment not found in wait_for.run()"
        )
        assert "note:" in known_prefixes, (
            "'note:' not in _KNOWN_PREFIXES — was omitted pre-SR-8, "
            "rv wait-for rejects note: watches"
        )

    def test_dataset_resolver_resolves_from_datasets_root_not_project_dir(self, tmp_instance):
        """dataset:<id> resolves from cfg.datasets_root, not project_notes_dir.

        This is the cross-project sharing test: a note at datasets_root is resolved
        by the dataset: resolver regardless of which project the DAG belongs to.
        """
        cfg = load_config(reload=True)

        # Write the note at datasets_root (NOT inside any project directory)
        _write_dataset_note(
            cfg.datasets_root, "cross-project-data",
            location="https://example.com/corpus.jsonl",
            hash_val="sha256:feedcafe" + "0" * 56,
        )

        # Resolver should find it
        result = resolve_watch("dataset:cross-project-data")
        assert result["ready"] is True, (
            f"dataset: resolver must find note at datasets_root, not project dir. Got: {result}"
        )

        # And it should NOT be in demo-research's project notes dir
        project_dir = cfg.project_notes_dir("demo-research")
        project_note = project_dir / "datasets" / "cross-project-data.md"
        assert not project_note.exists(), "Note must not have leaked into project dir"


# ============================================================================
# Streaming hash: _verify_local_file_hash reads in chunks (not full RAM load)
# ============================================================================

class TestStreamingHash:
    """_verify_local_file_hash must use chunked streaming read (not p.read_bytes()).

    Datasets are big-by-premise — loading the whole artifact into RAM would OOM
    on large files. The implementation must stream in fixed-size chunks.
    """

    def test_streaming_hash_correct_for_matching_file(self, tmp_instance):
        """Streaming hash produces correct result when hash matches."""
        from research_vault.wait_for import _verify_local_file_hash

        data = b"x" * (3 << 20)  # 3 MiB — spans multiple 1 MiB chunks
        data_file = Path(tmp_instance) / "big.bin"
        data_file.write_bytes(data)
        correct_hash = "sha256:" + hashlib.sha256(data).hexdigest()

        result = _verify_local_file_hash(str(data_file), correct_hash)
        assert result["ok"] is True, f"Expected ok=True, got: {result}"

    def test_streaming_hash_detects_mismatch(self, tmp_instance):
        """Streaming hash correctly detects a hash mismatch."""
        from research_vault.wait_for import _verify_local_file_hash

        data = b"y" * (2 << 20)  # 2 MiB
        data_file = Path(tmp_instance) / "big-mismatch.bin"
        data_file.write_bytes(data)
        wrong_hash = "sha256:" + "0" * 64

        result = _verify_local_file_hash(str(data_file), wrong_hash)
        assert result["ok"] is False
        assert "mismatch" in result["state"]

    def test_streaming_hash_uses_canonical_hasher(self):
        """Implementation delegates to hashing._hash_file — NOT an inline loop.

        Consolidated in fix/hasher-consolidation: the while-walrus chunked-read
        loop was extracted to hashing.hash_file (the canonical hasher), so this
        test now checks delegation rather than the inline loop structure.

        Checks:
          - ``_hash_file`` is called inside ``_verify_local_file_hash`` (AST check,
            comment-free — proves delegation to the canonical streaming hasher).
          - No ``read_bytes()`` call anywhere (RAM-load pattern still forbidden).
        """
        import ast, inspect, textwrap
        from research_vault import wait_for as wf_mod

        src = textwrap.dedent(inspect.getsource(wf_mod._verify_local_file_hash))
        tree = ast.parse(src)

        # Positive: _hash_file(...) is called somewhere in the function
        found_hash_file_call = any(
            isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "_hash_file")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "_hash_file")
            )
            for node in ast.walk(tree)
        )
        assert found_hash_file_call, (
            "_verify_local_file_hash must delegate to _hash_file (canonical streaming hasher); "
            "inline hashlib.sha256 loop removed in fix/hasher-consolidation"
        )

        # Negative: no read_bytes() call (RAM-load pattern forbidden)
        has_read_bytes = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "read_bytes"
            for node in ast.walk(tree)
        )
        assert not has_read_bytes, (
            "_verify_local_file_hash must not use p.read_bytes() — "
            "datasets are big; use chunked streaming"
        )


# ============================================================================
# Seam 4: Walker / frontier structural teeth
# (The "structurally cannot publish a finding" guarantee)
# ============================================================================

class TestDatasetWalkerFrontier:
    """The structural teeth: a finding node cannot enter the frontier until
    dataset:<id> resolves via the watch expression on its afterok edge.

    Tests are against the watch/frontier path (compute_frontier → _edge_satisfied
    → resolve_watch), NOT the produces post-check.
    """

    def _make_dag_for_finding_gate(self, cfg) -> tuple[dict, dict]:
        """Build manifest + node_states with data-step succeeded, finding pending."""
        manifest = _minimal_manifest([
            _agent_node("data-step"),
            _agent_node(
                "finding-node",
                needs=[{
                    "from": "data-step",
                    "edge": "afterok",
                    "watch": "dataset:my-data",
                }],
            ),
        ])
        validate_manifest(manifest)
        node_states = {
            "data-step": {"status": "succeeded"},
            "finding-node": {"status": "pending"},
        }
        return manifest, node_states

    def test_finding_NOT_in_frontier_when_dataset_note_missing(self, tmp_instance):
        """Finding node cannot enter frontier when dataset note is absent.

        This is THE structural teeth test: data-step = succeeded, but the
        dataset:my-data watch is not satisfied (note missing) → finding blocked.

        Non-vacuous: data-step IS in the frontier (it has no needs) only if both
        data-step and finding-node were pending. Here data-step = succeeded, so
        the only candidate is finding-node, which must be blocked.
        """
        cfg = load_config(reload=True)
        manifest, node_states = self._make_dag_for_finding_gate(cfg)

        dataset_note = cfg.datasets_root / "my-data.md"
        assert not dataset_note.exists(), "Test setup: note must not pre-exist"

        frontier = compute_frontier(manifest, node_states, {}, global_cap=4)
        frontier_ids = [f.node_id for f in frontier]

        assert "finding-node" not in frontier_ids, (
            f"finding-node must NOT be in frontier when dataset note is missing. "
            f"Got frontier: {frontier_ids}"
        )
        # data-step is already terminal (succeeded), so frontier should be empty
        assert frontier_ids == [], (
            f"Frontier must be empty when data-step=succeeded and finding-node blocked. "
            f"Got: {frontier_ids}"
        )

    def test_finding_enters_frontier_when_dataset_note_ready(self, tmp_instance):
        """Finding node enters frontier once dataset:<id> resolves (note + hash + location).

        This proves the structural teeth: data-step = succeeded AND dataset:my-data
        resolves → finding enters frontier.

        Non-vacuous: verified the resolver actually passes (not a trivially empty
        watch check). The note has BOTH required fields with a correct hash.
        """
        cfg = load_config(reload=True)
        manifest, node_states = self._make_dag_for_finding_gate(cfg)

        data_bytes = b"result,value\n1,42\n"
        data_file = Path(tmp_instance) / "my-data.csv"
        data_file.write_bytes(data_bytes)
        correct_hash = _sha256_hex(data_bytes)

        _write_dataset_note(
            cfg.datasets_root, "my-data",
            location=str(data_file),
            hash_val=correct_hash,
        )

        # First verify the resolver itself is ready (non-vacuous: confirm precondition)
        resolver_result = resolve_watch("dataset:my-data")
        assert resolver_result["ready"] is True, (
            f"Precondition: resolver must be ready before frontier check. Got: {resolver_result}"
        )

        frontier = compute_frontier(manifest, node_states, {}, global_cap=4)
        frontier_ids = [f.node_id for f in frontier]

        assert "finding-node" in frontier_ids, (
            f"finding-node must be in frontier when dataset note is ready. "
            f"Got frontier: {frontier_ids}"
        )

    def test_finding_NOT_in_frontier_when_hash_mismatch(self, tmp_instance):
        """Finding stays blocked when dataset note exists but hash mismatches.

        Non-vacuous: the note is PRESENT with both fields filled — only the hash
        value is wrong. Confirms the gate inspects the hash, not just field presence.
        """
        cfg = load_config(reload=True)
        manifest, node_states = self._make_dag_for_finding_gate(cfg)

        data_file = Path(tmp_instance) / "mismatch-data.csv"
        data_file.write_bytes(b"real,data\n1,2\n")
        wrong_hash = "sha256:" + "0" * 64

        _write_dataset_note(
            cfg.datasets_root, "my-data",
            location=str(data_file),
            hash_val=wrong_hash,
        )

        # Confirm the resolver itself reports not-ready (non-vacuous: the mismatch
        # blocks the resolver, not some other condition)
        resolver_result = resolve_watch("dataset:my-data")
        assert resolver_result["ready"] is False, (
            f"Precondition: resolver must be NOT ready on hash mismatch. Got: {resolver_result}"
        )

        frontier = compute_frontier(manifest, node_states, {}, global_cap=4)
        frontier_ids = [f.node_id for f in frontier]

        assert "finding-node" not in frontier_ids, (
            f"finding-node must NOT be in frontier when hash mismatches. "
            f"Got frontier: {frontier_ids}"
        )

    def test_finding_NOT_in_frontier_when_data_step_not_succeeded(self, tmp_instance):
        """Finding stays blocked when data-step has not succeeded yet.

        Non-vacuous: even with a VALID dataset note, the afterok edge on data-step
        itself blocks the finding. Confirms the afterok gate (predecessor status)
        is checked independently of the watch expression.
        """
        cfg = load_config(reload=True)
        manifest, _ = self._make_dag_for_finding_gate(cfg)

        # Write valid dataset note
        data_bytes = b"col\n1\n"
        data_file = Path(tmp_instance) / "data-pending.csv"
        data_file.write_bytes(data_bytes)
        _write_dataset_note(
            cfg.datasets_root, "my-data",
            location=str(data_file),
            hash_val=_sha256_hex(data_bytes),
        )

        # data-step is pending, not succeeded — afterok edge not satisfied
        node_states = {
            "data-step": {"status": "pending"},
            "finding-node": {"status": "pending"},
        }

        frontier = compute_frontier(manifest, node_states, {}, global_cap=4)
        frontier_ids = [f.node_id for f in frontier]

        assert "finding-node" not in frontier_ids
        # data-step itself should be in frontier (it has no needs)
        assert "data-step" in frontier_ids, (
            "data-step must be in frontier when it is pending with no needs"
        )
