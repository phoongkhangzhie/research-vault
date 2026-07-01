"""test_sr8.py — SR-8: DATASETS as typed OKF artifact + data-processing integration seams.

All hermetic (tmp_instance). No ~/vault reads or writes.

Four seams tested:
  1. OKF type `datasets/` — new type in OKF_TYPES; note creation + check validates.
  2. DAG `produces: {dataset: …}` — schema validates; complete-time gate (exists + hash).
  3. Resolver predicate `dataset:<id>` — resolve_watch returns ready only when note+hash valid.
  4. Walker/frontier path — finding node cannot enter frontier until dataset:<id> resolves.

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
    base_dir: Path,
    note_id: str,
    *,
    location: str = "",
    hash_val: str = "",
    title: str = "Test dataset",
) -> Path:
    """Write a datasets provenance note under base_dir/datasets/. Returns path.

    base_dir is typically cfg.project_notes_dir('demo-research') for cmd_check tests,
    or cfg.notes_root for resolve_watch (dataset:<id>) tests.
    """
    subdir = base_dir / "datasets"
    subdir.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: datasets", f"title: {title}", "created: 2026-07-01"]
    if location:
        lines.append(f"location: {location}")
    if hash_val:
        lines.append(f"hash: {hash_val}")
    lines += ["---", "", "<!-- provenance note -->", ""]
    p = subdir / f"{note_id}.md"
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
# Seam 1: OKF type `datasets/`
# ============================================================================

class TestDatasetsOkfType:
    """datasets is the 7th canonical OKF type."""

    def test_datasets_in_okf_types(self):
        """'datasets' must be in the OKF_TYPES frozenset."""
        assert "datasets" in note_mod.OKF_TYPES

    def test_okf_type_count_is_seven(self):
        """OKF_TYPES now has exactly 7 types."""
        assert len(note_mod.OKF_TYPES) == 7
        expected = {"literature", "concepts", "methods", "experiments", "findings", "mocs", "datasets"}
        assert note_mod.OKF_TYPES == expected

    def test_new_dataset_note_creates_in_datasets_dir(self, tmp_instance):
        """cmd_new creates a datasets note in the datasets/ subdirectory."""
        cfg = load_config(reload=True)
        path = note_mod.cmd_new("demo-research", "datasets", "My provenance note", config=cfg)
        assert path.exists()
        assert path.parent.name == "datasets"

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
            cfg.project_notes_dir("demo-research"),
            "check-ok",
            location=str(data_file),
            hash_val=h,
        )
        violations = note_mod.cmd_check("demo-research", config=cfg)
        assert violations == [], f"Expected no violations, got: {violations}"

    def test_check_dataset_note_fails_missing_location(self, tmp_instance):
        """A datasets note without a location field fails cmd_check."""
        cfg = load_config(reload=True)
        _write_dataset_note(
            cfg.project_notes_dir("demo-research"),
            "no-location",
            location="",  # missing
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
            cfg.project_notes_dir("demo-research"),
            "no-hash",
            location="/some/path.csv",
            hash_val="",  # missing
        )
        violations = note_mod.cmd_check("demo-research", config=cfg)
        assert any("hash" in v for v in violations), (
            f"Expected a 'hash' violation, got: {violations}"
        )

    def test_check_dataset_note_type_dir_mismatch_caught(self, tmp_instance):
        """A datasets note filed in the wrong directory is caught by cmd_check."""
        cfg = load_config(reload=True)
        # File a note in findings/ with type: datasets
        wrong_dir = cfg.project_notes_dir("demo-research") / "findings"
        wrong_dir.mkdir(parents=True, exist_ok=True)
        wrong_note = wrong_dir / "misplaced.md"
        wrong_note.write_text(
            "---\ntype: datasets\ntitle: wrong\ncreated: 2026-07-01\n"
            "location: /x\nhash: sha256:abc\n---\n\nbody\n",
            encoding="utf-8",
        )
        violations = note_mod.cmd_check("demo-research", config=cfg)
        assert any("datasets" in v or "misplaced" in v for v in violations), (
            f"Expected a type-dir violation, got: {violations}"
        )


# ============================================================================
# Seam 2: DAG produces.dataset — schema + complete-time gate
# ============================================================================

class TestProducesDatasetSchema:
    """Schema validates produces.dataset on agent nodes."""

    def test_schema_accepts_produces_dataset(self):
        """A manifest with produces.dataset (non-empty string) passes validation."""
        m = _minimal_manifest([
            _agent_node("data-step", produces={"dataset": "datasets/my-data.md"}),
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
                    "dataset": "datasets/my-data.md",
                },
            ),
        ])
        validate_manifest(m)  # must not raise


class TestCompleteProducesDatasetGate:
    """dag complete: produces.dataset gate checks note+location+hash at complete-time."""

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
        # Write manifest to tmp path
        mf_path = Path(tmp_instance) / "manifest.json"
        mf_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        store.create(run_state)
        return store, run_state, cfg

    def test_complete_produces_dataset_fails_note_missing(self, tmp_instance):
        """dag complete with produces.dataset fails when the provenance note doesn't exist."""
        from research_vault.dag import verbs as dag_verbs
        import argparse

        manifest = _minimal_manifest([
            _agent_node("data-step", produces={"dataset": "datasets/missing.md"}),
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
        # Write a note without location
        _write_dataset_note(cfg.notes_root, "no-loc", location="", hash_val="sha256:abc")

        manifest = _minimal_manifest([
            _agent_node("data-step", produces={"dataset": "datasets/no-loc.md"}),
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
        _write_dataset_note(cfg.notes_root, "no-hash", location="/tmp/data.csv", hash_val="")

        manifest = _minimal_manifest([
            _agent_node("data-step", produces={"dataset": "datasets/no-hash.md"}),
        ])
        store, run_state, _ = self._make_run(tmp_instance, manifest)

        args = argparse.Namespace(run_id="test-run", node_id="data-step", status="succeeded")
        rc = dag_verbs.cmd_complete(args)
        assert rc != 0, "Should fail when dataset note missing hash"

    def test_complete_produces_dataset_fails_hash_mismatch(self, tmp_instance):
        """dag complete FAILS (NOT-done) when the recorded hash mismatches the actual file."""
        from research_vault.dag import verbs as dag_verbs
        import argparse

        cfg = load_config(reload=True)

        # Write a real data file
        data_file = Path(tmp_instance) / "data.csv"
        data_file.write_bytes(b"col1,col2\n1,2\n")

        # Record the WRONG hash in the note
        wrong_hash = "sha256:" + "deadbeef" * 8  # wrong hash
        _write_dataset_note(
            cfg.notes_root, "hash-mismatch",
            location=str(data_file),
            hash_val=wrong_hash,
        )

        manifest = _minimal_manifest([
            _agent_node("data-step", produces={"dataset": "datasets/hash-mismatch.md"}),
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

        # Write a data file and record its correct hash
        data_file = Path(tmp_instance) / "good-data.csv"
        data_bytes = b"col1,col2\n1,2\n3,4\n"
        data_file.write_bytes(data_bytes)
        correct_hash = _sha256_hex(data_bytes)

        _write_dataset_note(
            cfg.notes_root, "good-data",
            location=str(data_file),
            hash_val=correct_hash,
        )

        manifest = _minimal_manifest([
            _agent_node("data-step", produces={"dataset": "datasets/good-data.md"}),
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

        # URL location: no local file to verify hash against
        _write_dataset_note(
            cfg.notes_root, "url-data",
            location="https://example.com/data.csv",
            hash_val="sha256:abc123def456",
        )

        manifest = _minimal_manifest([
            _agent_node("data-step", produces={"dataset": "datasets/url-data.md"}),
        ])
        store, run_state, _ = self._make_run(tmp_instance, manifest)

        args = argparse.Namespace(run_id="test-run", node_id="data-step", status="succeeded")
        rc = dag_verbs.cmd_complete(args)
        assert rc == 0, "Should succeed for URL-location dataset (hash recorded)"


# ============================================================================
# Seam 3: Resolver predicate `dataset:<id>`
# ============================================================================

class TestDatasetResolver:
    """resolve_watch('dataset:<id>') gate: note exists + hash + location valid."""

    def test_dataset_resolver_not_ready_when_note_missing(self, tmp_instance):
        """dataset: returns not-ready when the provenance note doesn't exist."""
        result = resolve_watch("dataset:nonexistent")
        assert not result["ready"]
        assert "missing" in result["state"] or "not-ready" in result["state"] or not result["ready"]

    def test_dataset_resolver_not_ready_when_hash_missing(self, tmp_instance):
        """dataset: returns not-ready when the provenance note has no hash field."""
        cfg = load_config(reload=True)
        _write_dataset_note(cfg.notes_root, "no-hash-resolver", location="/tmp/x.csv", hash_val="")
        result = resolve_watch("dataset:no-hash-resolver")
        assert not result["ready"]

    def test_dataset_resolver_not_ready_when_location_missing(self, tmp_instance):
        """dataset: returns not-ready when the provenance note has no location field."""
        cfg = load_config(reload=True)
        _write_dataset_note(cfg.notes_root, "no-loc-resolver", location="", hash_val="sha256:abc")
        result = resolve_watch("dataset:no-loc-resolver")
        assert not result["ready"]

    def test_dataset_resolver_not_ready_when_file_hash_mismatch(self, tmp_instance):
        """dataset: returns not-ready when the recorded hash mismatches the local file."""
        cfg = load_config(reload=True)
        data_file = Path(tmp_instance) / "mismatch.csv"
        data_file.write_bytes(b"real,data\n1,2\n")
        wrong_hash = "sha256:" + "0" * 64

        _write_dataset_note(
            cfg.notes_root, "hash-mismatch-resolver",
            location=str(data_file),
            hash_val=wrong_hash,
        )
        result = resolve_watch("dataset:hash-mismatch-resolver")
        assert not result["ready"]
        assert "mismatch" in result["state"] or not result["ready"]

    def test_dataset_resolver_ready_with_matching_local_file(self, tmp_instance):
        """dataset: returns ready when note + local file + correct hash all match."""
        cfg = load_config(reload=True)
        data_bytes = b"a,b,c\n1,2,3\n"
        data_file = Path(tmp_instance) / "match.csv"
        data_file.write_bytes(data_bytes)
        correct_hash = _sha256_hex(data_bytes)

        _write_dataset_note(
            cfg.notes_root, "hash-match-resolver",
            location=str(data_file),
            hash_val=correct_hash,
        )
        result = resolve_watch("dataset:hash-match-resolver")
        assert result["ready"], f"Expected ready, got: {result}"

    def test_dataset_resolver_ready_with_url_location(self, tmp_instance):
        """dataset: returns ready for URL-location when note has hash (no file check)."""
        cfg = load_config(reload=True)
        _write_dataset_note(
            cfg.notes_root, "url-resolver",
            location="https://zenodo.org/record/123/data.csv",
            hash_val="sha256:abc123def456abc123def456abc123def456abc123def456abc123def456abc1",
        )
        result = resolve_watch("dataset:url-resolver")
        assert result["ready"], f"Expected ready for URL location, got: {result}"

    def test_dataset_resolver_ready_with_doi_location(self, tmp_instance):
        """dataset: returns ready for DOI-location when note has hash (no file check)."""
        cfg = load_config(reload=True)
        _write_dataset_note(
            cfg.notes_root, "doi-resolver",
            location="doi:10.5281/zenodo.12345",
            hash_val="sha256:def456abc123def456abc123def456abc123def456abc123def456abc123def4",
        )
        result = resolve_watch("dataset:doi-resolver")
        assert result["ready"], f"Expected ready for DOI location, got: {result}"

    def test_dataset_in_known_prefixes(self, tmp_instance):
        """'dataset:' must be in the known prefixes so rv wait-for accepts it."""
        # The CLI validation uses _KNOWN_PREFIXES; verify it includes 'dataset:'
        from research_vault import wait_for as wf_mod
        import inspect
        src = inspect.getsource(wf_mod.run)
        assert "dataset:" in src, (
            "'dataset:' must be in the _KNOWN_PREFIXES tuple in wait_for.run()"
        )

    def test_dataset_resolver_error_message_on_missing(self, tmp_instance):
        """dataset: resolver populates error or state on missing note."""
        result = resolve_watch("dataset:ghost-note")
        assert not result["ready"]
        # Should have some indication of the problem
        assert result.get("state") or result.get("error")


# ============================================================================
# Seam 4: Walker / frontier structural teeth
# (The "structurally cannot publish a finding" guarantee)
# ============================================================================

class TestDatasetWalkerFrontier:
    """The structural teeth: a finding node cannot enter the frontier until
    dataset:<id> resolves via the watch expression on its afterok edge.

    This is tested against the watch/frontier path (compute_frontier → _edge_satisfied
    → resolve_watch), NOT the produces post-check — the spec's critical note.
    """

    def _make_dag_for_finding_gate(self, tmp_instance, cfg) -> tuple[dict, dict]:
        """Build a manifest with data-step → finding (afterok + watch: dataset:my-data).

        Returns (manifest, node_states_with_data_step_succeeded).
        """
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
        """
        cfg = load_config(reload=True)
        manifest, node_states = self._make_dag_for_finding_gate(tmp_instance, cfg)

        # Ensure the dataset note does NOT exist
        dataset_note = cfg.notes_root / "datasets" / "my-data.md"
        assert not dataset_note.exists(), "Test setup: note must not pre-exist"

        frontier = compute_frontier(manifest, node_states, {}, global_cap=4)
        frontier_ids = [f.node_id for f in frontier]

        assert "finding-node" not in frontier_ids, (
            f"finding-node must NOT be in frontier when dataset note is missing. "
            f"Got frontier: {frontier_ids}"
        )

    def test_finding_enters_frontier_when_dataset_note_ready(self, tmp_instance):
        """Finding node enters frontier once dataset:<id> resolves (note + hash + location).

        This proves the structural teeth: data-step = succeeded AND dataset:my-data
        resolves → finding enters frontier.
        """
        cfg = load_config(reload=True)
        manifest, node_states = self._make_dag_for_finding_gate(tmp_instance, cfg)

        # Write the dataset note with valid location + hash
        data_bytes = b"result,value\n1,42\n"
        data_file = Path(tmp_instance) / "my-data.csv"
        data_file.write_bytes(data_bytes)
        correct_hash = _sha256_hex(data_bytes)

        _write_dataset_note(
            cfg.notes_root, "my-data",
            location=str(data_file),
            hash_val=correct_hash,
        )

        frontier = compute_frontier(manifest, node_states, {}, global_cap=4)
        frontier_ids = [f.node_id for f in frontier]

        assert "finding-node" in frontier_ids, (
            f"finding-node must be in frontier when dataset note is ready. "
            f"Got frontier: {frontier_ids}"
        )

    def test_finding_NOT_in_frontier_when_hash_mismatch(self, tmp_instance):
        """Finding stays blocked when dataset note exists but hash mismatches.

        The 'structurally cannot publish' guarantee holds even with a filed note
        if the hash is wrong — a corrupt or swapped dataset cannot slip through.
        """
        cfg = load_config(reload=True)
        manifest, node_states = self._make_dag_for_finding_gate(tmp_instance, cfg)

        # Write note with wrong hash
        data_file = Path(tmp_instance) / "mismatch-data.csv"
        data_file.write_bytes(b"real,data\n1,2\n")
        wrong_hash = "sha256:" + "0" * 64

        _write_dataset_note(
            cfg.notes_root, "my-data",
            location=str(data_file),
            hash_val=wrong_hash,
        )

        frontier = compute_frontier(manifest, node_states, {}, global_cap=4)
        frontier_ids = [f.node_id for f in frontier]

        assert "finding-node" not in frontier_ids, (
            f"finding-node must NOT be in frontier when hash mismatches. "
            f"Got frontier: {frontier_ids}"
        )

    def test_finding_NOT_in_frontier_when_data_step_not_succeeded(self, tmp_instance):
        """Finding stays blocked when data-step has not succeeded yet.

        Even with a valid dataset note, the afterok edge on data-step blocks.
        """
        cfg = load_config(reload=True)
        manifest, _ = self._make_dag_for_finding_gate(tmp_instance, cfg)

        # Write valid dataset note
        data_bytes = b"col\n1\n"
        data_file = Path(tmp_instance) / "data-pending.csv"
        data_file.write_bytes(data_bytes)
        _write_dataset_note(
            cfg.notes_root, "my-data",
            location=str(data_file),
            hash_val=_sha256_hex(data_bytes),
        )

        # data-step is pending, not succeeded
        node_states = {
            "data-step": {"status": "pending"},
            "finding-node": {"status": "pending"},
        }

        frontier = compute_frontier(manifest, node_states, {}, global_cap=4)
        frontier_ids = [f.node_id for f in frontier]

        assert "finding-node" not in frontier_ids
        # data-step itself should be in frontier (it has no needs)
        assert "data-step" in frontier_ids
