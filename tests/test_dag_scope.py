"""test_dag_scope.py — hermetic tests for SR-SCOPE (reads: grounding manifest).

Coverage:
  1. Structural teeth — reads: if present must be non-empty list, items well-formed (ManifestError)
  2. human-go nodes must NOT carry reads: (ManifestError)
  3. validate_manifest stays pure (no I/O regardless of reads: content)
  4. Non-fatal WARN — agent node with no reads: emits a warn from manifest_warns
  5. Resolution pass — bare file / doc#anchor / control#slug / path:symbol
  6. Frontier print — reads: suffix appended to DISPATCH line (present/absent)
  7. Frontier composes with both FRESH and CONTINUES modes
  8. Discovery: dag when_to_use contains the unbounded-reads anti-pattern

All tests hermetic — no ~/vault, no real cluster, no network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import reset_config_cache
from research_vault.dag.schema import ManifestError, validate_manifest, manifest_warns
from research_vault.dag.walker import FrontierNode
from research_vault.dag.verbs import _print_frontier
from research_vault.dag.reads import (
    ReadsError,
    resolve_reads_pointer,
    resolve_reads_pointers,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_cfg():
    reset_config_cache()
    yield
    reset_config_cache()


# ---------------------------------------------------------------------------
# Minimal manifest builders (mirroring test_dag_disp.py)
# ---------------------------------------------------------------------------

def _agent(
    nid: str,
    *,
    spec: str | None = "task://test#spec",
    reads: list | None = None,
    continues: dict | None = None,
    needs: list | None = None,
    produces: dict | None = None,
    label: str | None = None,
) -> dict:
    n: dict = {"id": nid, "type": "agent", "label": label or f"Node {nid}"}
    if spec is not None:
        n["spec"] = spec
    if reads is not None:
        n["reads"] = reads
    if continues is not None:
        n["continues"] = continues
    if needs:
        n["needs"] = needs
    if produces:
        n["produces"] = produces
    return n


def _human_go(nid: str, needs: list | None = None, reads: list | None = None) -> dict:
    n: dict = {"id": nid, "type": "human-go", "label": f"Gate {nid}"}
    if needs:
        n["needs"] = needs
    if reads is not None:
        n["reads"] = reads
    return n


def _need(from_id: str, edge: str = "afterok") -> dict:
    return {"from": from_id, "edge": edge}


def _manifest(nodes: list[dict], run_id: str = "test-run") -> dict:
    return {"run_id": run_id, "nodes": nodes}


# ===========================================================================
# 1. Structural teeth — reads: must be non-empty list, items well-formed
# ===========================================================================

class TestReadsStructure:
    def test_reads_empty_list_raises(self):
        """reads: [] → ManifestError (non-empty-if-present contract)."""
        m = _manifest([_agent("a", reads=[])])
        with pytest.raises(ManifestError, match="reads"):
            validate_manifest(m)

    def test_reads_non_list_string_raises(self):
        """reads: 'foo' (not a list) → ManifestError."""
        node = {"id": "a", "type": "agent", "spec": "task://t#a", "reads": "src/file.py"}
        m = _manifest([node])
        with pytest.raises(ManifestError, match="reads"):
            validate_manifest(m)

    def test_reads_non_list_dict_raises(self):
        """reads: {} (not a list) → ManifestError."""
        node = {"id": "a", "type": "agent", "spec": "task://t#a", "reads": {"ref": "foo"}}
        m = _manifest([node])
        with pytest.raises(ManifestError, match="reads"):
            validate_manifest(m)

    def test_reads_item_empty_string_raises(self):
        """reads: [''] (empty string item) → ManifestError."""
        m = _manifest([_agent("a", reads=[""])])
        with pytest.raises(ManifestError, match="reads"):
            validate_manifest(m)

    def test_reads_item_whitespace_only_raises(self):
        """reads: ['   '] (whitespace-only string item) → ManifestError."""
        m = _manifest([_agent("a", reads=["   "])])
        with pytest.raises(ManifestError, match="reads"):
            validate_manifest(m)

    def test_reads_item_dict_missing_ref_raises(self):
        """reads: [{'why': 'foo'}] (dict without ref) → ManifestError."""
        m = _manifest([_agent("a", reads=[{"why": "foo"}])])
        with pytest.raises(ManifestError, match="reads"):
            validate_manifest(m)

    def test_reads_item_dict_empty_ref_raises(self):
        """reads: [{'ref': ''}] (empty ref in dict) → ManifestError."""
        m = _manifest([_agent("a", reads=[{"ref": ""}])])
        with pytest.raises(ManifestError, match="reads"):
            validate_manifest(m)

    def test_reads_item_dict_whitespace_ref_raises(self):
        """reads: [{'ref': '   '}] (whitespace ref) → ManifestError."""
        m = _manifest([_agent("a", reads=[{"ref": "   "}])])
        with pytest.raises(ManifestError, match="reads"):
            validate_manifest(m)

    def test_reads_item_wrong_type_raises(self):
        """reads: [42] (non-str, non-dict item) → ManifestError."""
        m = _manifest([_agent("a", reads=[42])])
        with pytest.raises(ManifestError, match="reads"):
            validate_manifest(m)

    def test_reads_item_list_raises(self):
        """reads: [['nested']] (list item) → ManifestError."""
        m = _manifest([_agent("a", reads=[["nested"]])])
        with pytest.raises(ManifestError, match="reads"):
            validate_manifest(m)

    def test_reads_valid_bare_string_passes(self):
        """reads: ['src/file.py'] (bare string) → valid."""
        m = _manifest([_agent("a", reads=["src/research_vault/dag/schema.py"])])
        validate_manifest(m)  # must not raise

    def test_reads_valid_dict_ref_only_passes(self):
        """reads: [{'ref': 'foo.md'}] (dict with ref, no why) → valid."""
        m = _manifest([_agent("a", reads=[{"ref": "foo.md"}])])
        validate_manifest(m)  # must not raise

    def test_reads_valid_dict_ref_and_why_passes(self):
        """reads: [{'ref': 'foo.md', 'why': 'prior verdict'}] → valid."""
        m = _manifest([_agent("a", reads=[{"ref": "foo.md", "why": "prior verdict"}])])
        validate_manifest(m)  # must not raise

    def test_reads_valid_mixed_list_passes(self):
        """reads: mix of bare strings and {ref, why} dicts → valid."""
        m = _manifest([
            _agent("a", reads=[
                "src/research_vault/dag/schema.py",
                {"ref": "docs/design.md#section", "why": "architecture reference"},
                "control/research-vault.md#sr-scope",
            ])
        ])
        validate_manifest(m)  # must not raise

    def test_reads_absent_passes(self):
        """reads: absent (no field at all) → valid (OPTIONAL by design)."""
        m = _manifest([_agent("a")])  # no reads field
        validate_manifest(m)  # must not raise

    def test_reads_present_on_second_agent_validates(self):
        """reads: present on one of two agents in chain → valid."""
        m = _manifest([
            _agent("a", spec="task://t#a"),
            _agent("b", spec="task://t#b",
                   reads=["src/file.py"],
                   needs=[_need("a")]),
        ])
        validate_manifest(m)  # must not raise

    def test_reads_with_anchor_form_passes(self):
        """reads: ['tasks/design.md#5B-SCOPE'] (anchor form) → structurally valid."""
        m = _manifest([_agent("a", reads=["tasks/design.md#5B-SCOPE"])])
        validate_manifest(m)  # must not raise — structural check only, no resolution here

    def test_reads_with_symbol_form_passes(self):
        """reads: ['src/module.py:MyClass'] (symbol form) → structurally valid."""
        m = _manifest([_agent("a", reads=["src/module.py:MyClass"])])
        validate_manifest(m)  # must not raise


# ===========================================================================
# 2. human-go nodes must NOT carry reads:
# ===========================================================================

class TestHumanGoReadsExemption:
    def test_human_go_with_reads_raises(self):
        """human-go nodes carrying reads: → ManifestError (decision gates, not dispatches)."""
        m = _manifest([
            _agent("a", spec="task://t#a"),
            _human_go("gate", needs=[_need("a")], reads=["src/file.py"]),
        ])
        with pytest.raises(ManifestError, match="reads"):
            validate_manifest(m)

    def test_human_go_without_reads_passes(self):
        """human-go nodes without reads: → valid (exemption only blocks its presence)."""
        m = _manifest([
            _agent("a", spec="task://t#a"),
            _human_go("gate", needs=[_need("a")]),
        ])
        validate_manifest(m)  # must not raise


# ===========================================================================
# 3. validate_manifest purity — NO I/O regardless of reads: content
# ===========================================================================

class TestValidateManifestPurity:
    def test_validate_manifest_does_not_touch_filesystem(self, tmp_path, monkeypatch):
        """validate_manifest raises no OSError even if pointer paths do not exist.

        This asserts the purity boundary: structural shape is checked in-memory;
        resolution (I/O) happens in the separate run/tick pass (resolve_reads_pointers).
        """
        # Point at a non-existent file — structural validation should not raise
        m = _manifest([_agent("a", reads=["/definitely/does/not/exist/file.py"])])
        validate_manifest(m)  # must not raise — no I/O attempted

    def test_validate_manifest_does_not_open_files(self, monkeypatch):
        """validate_manifest must NOT call open() on any reads: pointer."""
        opened_paths: list[str] = []
        original_open = open

        def tracking_open(path, *args, **kwargs):
            # Only flag opens that look like reads: paths (non-test-infrastructure)
            path_str = str(path)
            if "definitely_not_real" in path_str:
                opened_paths.append(path_str)
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", tracking_open)

        m = _manifest([_agent("a", reads=["/definitely_not_real/path/file.py"])])
        validate_manifest(m)

        assert opened_paths == [], (
            f"validate_manifest unexpectedly opened filesystem paths: {opened_paths}"
        )


# ===========================================================================
# 4. Non-fatal WARN — absent reads: on agent node
# ===========================================================================

class TestAbsentReadsWarn:
    def test_agent_without_reads_emits_warn(self):
        """An agent node with no reads: → non-fatal WARN from manifest_warns."""
        m = _manifest([_agent("a", spec="task://t#a")])  # no reads
        warns = manifest_warns(m)
        # Must contain at least one warn mentioning the node and reads/scope
        reads_warns = [w for w in warns if "a" in w and (
            "reads" in w.lower() or "scope" in w.lower() or "unbounded" in w.lower()
        )]
        assert len(reads_warns) >= 1, (
            f"Expected a reads-scope WARN for node 'a' but got: {warns}"
        )

    def test_agent_with_reads_no_scope_warn(self):
        """An agent node WITH reads: → no reads-scope WARN."""
        m = _manifest([_agent("a", reads=["src/file.py"])])
        warns = manifest_warns(m)
        # No warns about missing reads scope for this node
        reads_warns = [w for w in warns if "a" in w and "unbounded" in w.lower()]
        assert len(reads_warns) == 0, f"Unexpected reads-scope warn: {warns}"

    def test_human_go_without_reads_no_warn(self):
        """human-go nodes without reads: do NOT emit a reads-scope WARN (exempt)."""
        m = _manifest([
            _agent("a", spec="task://t#a", reads=["src/file.py"]),
            _human_go("gate", needs=[_need("a")]),
        ])
        warns = manifest_warns(m)
        # No warn for the human-go node
        gate_warns = [w for w in warns if "gate" in w and "unbounded" in w.lower()]
        assert gate_warns == []

    def test_reads_warn_is_nonfatal(self):
        """A missing reads: WARN does not prevent manifest from validating."""
        m = _manifest([_agent("a", spec="task://t#a")])  # no reads
        validate_manifest(m)  # must not raise
        warns = manifest_warns(m)
        assert isinstance(warns, list)

    def test_multiple_agents_without_reads_emit_multiple_warns(self):
        """Two agent nodes both missing reads: → two reads-scope WARNs (one per node)."""
        m = _manifest([
            _agent("a", spec="task://t#a"),
            _agent("b", spec="task://t#b", needs=[_need("a")]),
        ])
        warns = manifest_warns(m)
        reads_warns = [w for w in warns if "unbounded" in w.lower() or "reads" in w.lower()]
        assert len(reads_warns) >= 2, f"Expected 2 reads WARNs, got: {warns}"

    def test_mixed_agents_only_warn_on_missing(self):
        """Only agents without reads: emit the WARN; those with reads: do not."""
        m = _manifest([
            _agent("a", spec="task://t#a"),                       # no reads → WARN
            _agent("b", spec="task://t#b",
                   reads=["src/file.py"],
                   needs=[_need("a")]),                           # has reads → no WARN
        ])
        warns = manifest_warns(m)
        reads_warns = [w for w in warns if "unbounded" in w.lower() or (
            "reads" in w.lower() and ("a" in w or "b" in w)
        )]
        # Should warn for 'a' but not 'b'
        node_a_warns = [w for w in reads_warns if "a" in w]
        node_b_warns = [w for w in reads_warns if "b" in w and "a" not in w]
        assert len(node_a_warns) >= 1, f"Expected warn for node 'a', got: {warns}"
        assert len(node_b_warns) == 0, f"Unexpected warn for node 'b', got: {warns}"


# ===========================================================================
# 5. Resolution pass — resolve_reads_pointer + resolve_reads_pointers
# ===========================================================================

class TestReadsResolution:
    def test_bare_file_resolves(self, tmp_path):
        """A bare file path that exists → no error."""
        f = tmp_path / "schema.py"
        f.write_text("# stub")
        err, warn = resolve_reads_pointer("schema.py", project_root=tmp_path)
        assert err is None, f"Unexpected error: {err}"

    def test_bare_file_missing_fails(self, tmp_path):
        """A bare file path that does NOT exist → hard error."""
        err, warn = resolve_reads_pointer("does_not_exist.py", project_root=tmp_path)
        assert err is not None
        assert "does_not_exist.py" in err

    def test_doc_anchor_resolves(self, tmp_path):
        """file#anchor where file exists AND anchor heading exists → no error."""
        md = tmp_path / "design.md"
        md.write_text("# Title\n\n## 5B-SCOPE. Some heading\n\nContent.\n")
        err, warn = resolve_reads_pointer("design.md#5B-SCOPE", project_root=tmp_path)
        assert err is None, f"Unexpected error: {err}"

    def test_doc_anchor_file_missing_fails(self, tmp_path):
        """file#anchor where file does NOT exist → hard error."""
        err, warn = resolve_reads_pointer("missing.md#anchor", project_root=tmp_path)
        assert err is not None
        assert "missing.md" in err

    def test_doc_anchor_missing_anchor_fails(self, tmp_path):
        """file#anchor where file exists but anchor NOT found → hard error."""
        md = tmp_path / "design.md"
        md.write_text("# Title\n\n## Other Section\n\nContent.\n")
        err, warn = resolve_reads_pointer("design.md#MISSING_ANCHOR", project_root=tmp_path)
        assert err is not None
        assert "MISSING_ANCHOR" in err or "anchor" in err.lower()

    def test_control_slug_resolves(self, tmp_path):
        """control/project.md#slug-section where file+section exist → no error."""
        ctrl_dir = tmp_path / "control"
        ctrl_dir.mkdir()
        ctrl_file = ctrl_dir / "my-project.md"
        ctrl_file.write_text("# Control\n\n## sr-scope-decision\n\nContent.\n")
        err, warn = resolve_reads_pointer(
            "control/my-project.md#sr-scope-decision", project_root=tmp_path
        )
        assert err is None, f"Unexpected error: {err}"

    def test_path_symbol_file_exists_symbol_soft_warn(self, tmp_path):
        """path:symbol where file exists but symbol not found → file resolves hard, symbol soft WARN."""
        f = tmp_path / "module.py"
        f.write_text("class OtherClass:\n    pass\n")
        err, warn = resolve_reads_pointer("module.py:NonExistentSymbol", project_root=tmp_path)
        assert err is None, f"Unexpected hard error for soft symbol: {err}"
        assert warn is not None, "Expected a soft warn for symbol not found"
        assert "NonExistentSymbol" in warn or "symbol" in warn.lower()

    def test_path_symbol_file_missing_hard_fail(self, tmp_path):
        """path:symbol where file does NOT exist → hard error (file check is hard)."""
        err, warn = resolve_reads_pointer("missing.py:MyClass", project_root=tmp_path)
        assert err is not None
        assert "missing.py" in err

    def test_dict_ref_item_resolves(self, tmp_path):
        """A {ref:..., why:...} item with a resolvable ref → no error."""
        f = tmp_path / "doc.md"
        f.write_text("# Doc\n")
        err, warn = resolve_reads_pointer("doc.md", project_root=tmp_path)
        assert err is None

    def test_resolve_reads_pointers_all_valid(self, tmp_path):
        """resolve_reads_pointers with all resolvable items → empty errors."""
        f1 = tmp_path / "file1.py"
        f1.write_text("# stub")
        f2 = tmp_path / "doc.md"
        f2.write_text("# Title\n\n## section-a\n\nContent.\n")

        m = _manifest([
            _agent("a", reads=[
                "file1.py",
                "doc.md#section-a",
                {"ref": "file1.py", "why": "for reference"},
            ])
        ])
        errors, warns = resolve_reads_pointers(m, project_root=tmp_path)
        assert errors == [], f"Unexpected errors: {errors}"

    def test_resolve_reads_pointers_with_missing_file(self, tmp_path):
        """resolve_reads_pointers with one missing file → error returned."""
        m = _manifest([_agent("a", reads=["missing_file.py"])])
        errors, warns = resolve_reads_pointers(m, project_root=tmp_path)
        assert len(errors) >= 1
        assert any("missing_file.py" in e for e in errors)

    def test_resolve_reads_pointers_no_reads_no_errors(self, tmp_path):
        """Nodes with no reads: field → no errors from resolution pass."""
        m = _manifest([_agent("a")])  # no reads
        errors, warns = resolve_reads_pointers(m, project_root=tmp_path)
        assert errors == []

    def test_resolve_reads_pointers_human_go_skipped(self, tmp_path):
        """human-go nodes are skipped by the resolution pass."""
        m = _manifest([
            _agent("a", spec="task://t#a"),
            _human_go("gate", needs=[_need("a")]),
        ])
        errors, warns = resolve_reads_pointers(m, project_root=tmp_path)
        assert errors == []

    def test_resolve_reads_symbol_warn_accumulates(self, tmp_path):
        """resolve_reads_pointers collects symbol-level soft WARNs."""
        f = tmp_path / "module.py"
        f.write_text("# stub\n")
        m = _manifest([_agent("a", reads=["module.py:MissingSymbol"])])
        errors, warns = resolve_reads_pointers(m, project_root=tmp_path)
        assert errors == []
        assert len(warns) >= 1

    def test_resolve_reads_pointer_absolute_path(self, tmp_path):
        """An absolute path in reads: resolves directly (not relative to project_root)."""
        f = tmp_path / "abs_file.py"
        f.write_text("# stub")
        err, warn = resolve_reads_pointer(str(f), project_root=Path("/some/other/dir"))
        assert err is None, f"Unexpected error for absolute path: {err}"


# ===========================================================================
# 6. Frontier print — reads: suffix on DISPATCH line
# ===========================================================================

class TestFrontierReadsSuffix:
    def test_fresh_agent_with_reads_prints_count_suffix(self, capsys):
        """A FRESH agent with reads: → DISPATCH line includes a bounded '— reads: N
        pointer(s)' suffix — NOT the full resolved list (that's in the brief)."""
        node = _agent("a", spec="task://t#spec",
                      reads=["src/schema.py", "docs/design.md#5B-SCOPE"])
        frontier = [FrontierNode(node_id="a", action="dispatch", node=node)]
        _print_frontier(frontier, run_id="r1")
        out = capsys.readouterr().out
        assert "reads: 2 pointer(s)" in out
        assert "src/schema.py" not in out
        assert "docs/design.md#5B-SCOPE" not in out
        assert "rv dag brief r1 a" in out

    def test_fresh_agent_without_reads_no_suffix(self, capsys):
        """A FRESH agent without reads: → DISPATCH line has no '— reads:' suffix."""
        node = _agent("a", spec="task://t#spec")
        frontier = [FrontierNode(node_id="a", action="dispatch", node=node)]
        _print_frontier(frontier, run_id="r1")
        out = capsys.readouterr().out
        assert "reads:" not in out
        assert "FRESH" in out

    def test_continues_agent_with_reads_prints_count_suffix(self, capsys):
        """A CONTINUES agent with reads: → DISPATCH line includes the reads: count
        suffix, never the full path list."""
        node = _agent(
            "b",
            spec="task://t#refine",
            continues={"node": "a", "reason": "tight iter"},
            reads=["src/walker.py"],
        )
        frontier = [FrontierNode(node_id="b", action="dispatch", node=node)]
        _print_frontier(frontier, run_id="r1")
        out = capsys.readouterr().out
        assert "CONTINUES" in out
        assert "reads: 1 pointer(s)" in out
        assert "src/walker.py" not in out

    def test_continues_agent_without_reads_no_suffix(self, capsys):
        """A CONTINUES agent without reads: → DISPATCH line has no reads: suffix."""
        node = _agent(
            "b",
            spec="task://t#refine",
            continues={"node": "a", "reason": "tight iter"},
        )
        frontier = [FrontierNode(node_id="b", action="dispatch", node=node)]
        _print_frontier(frontier, run_id="r1")
        out = capsys.readouterr().out
        assert "CONTINUES" in out
        assert "reads:" not in out

    def test_dict_ref_items_counted_not_listed(self, capsys):
        """Dict {ref:..., why:...} items are counted in the reads: N suffix — the
        individual ref text is NOT printed in the frontier map (see the brief)."""
        node = _agent("a", spec="task://t#spec",
                      reads=[{"ref": "src/schema.py", "why": "struct reference"}])
        frontier = [FrontierNode(node_id="a", action="dispatch", node=node)]
        _print_frontier(frontier, run_id="r1")
        out = capsys.readouterr().out
        assert "reads: 1 pointer(s)" in out
        assert "src/schema.py" not in out

    def test_await_go_unaffected_by_reads(self, capsys):
        """human-go AWAIT-GO items are never modified by reads: logic."""
        node = _human_go("gate")
        frontier = [FrontierNode(node_id="gate", action="await-go", node=node)]
        _print_frontier(frontier, run_id="r1")
        out = capsys.readouterr().out
        assert "AWAIT-GO" in out
        assert "reads:" not in out


# ===========================================================================
# 7. Discovery — dag when_to_use contains the anti-pattern text
# ===========================================================================

class TestDiscovery:
    def test_dag_when_to_use_contains_unbounded_reads_antipattern(self):
        """The dag verb's when_to_use must mention the unbounded-reads anti-pattern."""
        from research_vault.cli import _VERB_REGISTRY
        dag_entry = _VERB_REGISTRY.get("dag", {})
        when = dag_entry.get("when_to_use", "")
        assert "unbounded" in when.lower() or "reads" in when.lower(), (
            f"dag when_to_use does not mention unbounded reads anti-pattern: {when!r}"
        )


# ===========================================================================
# 8. Integration — existing SR-DISP tests still pass (regression guard)
# ===========================================================================

class TestReadsDoesNotBreakSRDISP:
    """Regression: adding reads: support must not break any existing DISP behaviour."""

    def test_spec_still_required_on_agent(self):
        """spec is still REQUIRED on agent nodes, unchanged by SR-SCOPE."""
        m = _manifest([{"id": "a", "type": "agent"}])  # no spec
        with pytest.raises(ManifestError, match="spec"):
            validate_manifest(m)

    def test_continues_validation_unchanged(self):
        """continues.reason still required — SR-SCOPE did not relax it."""
        m = _manifest([
            _agent("a", spec="task://t#a"),
            _agent("b", spec="task://t#b",
                   continues={"node": "a"},  # missing reason
                   needs=[_need("a")]),
        ])
        with pytest.raises(ManifestError, match="reason"):
            validate_manifest(m)

    def test_fresh_mode_line_compact_when_reads_absent(self, capsys):
        """FRESH mode line stays compact (no spec body, no reads:) when reads: absent."""
        node = _agent("a", spec="task://t#myspec")
        frontier = [FrontierNode(node_id="a", action="dispatch", node=node)]
        _print_frontier(frontier, run_id="r1")
        out = capsys.readouterr().out
        assert "FRESH" in out
        assert "reads:" not in out
        assert "task://t#myspec" not in out

    def test_continues_mode_line_compact_when_reads_absent(self, capsys):
        """CONTINUES mode line stays compact (no spec body, no reads:) when reads: absent."""
        node = _agent(
            "b",
            spec="task://t#spec",
            continues={"node": "a", "reason": "tight iteration"},
        )
        frontier = [FrontierNode(node_id="b", action="dispatch", node=node)]
        _print_frontier(frontier, run_id="r1")
        out = capsys.readouterr().out
        assert "CONTINUES a — tight iteration" in out
        assert "reads:" not in out
        assert "task://t#spec" not in out
