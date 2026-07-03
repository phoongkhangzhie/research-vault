"""test_sr_draft_render.py — SR-DRAFT-RENDER: guarded terminal render.

Covers:
  1. Missing toolchain → status "blocked-prereq" (DISTINCT from "failed"), no traceback.
  2. Grounding hard-fail (unmatched \\cite) → status "failed" (NOT "blocked-prereq").
  3. Compile node spec in scaffolded manifest directs rv manuscript <project> compile <id>
     AND states blocked-vs-failed semantics AND names main.pdf as the deliverable.
  4. PDF terminal artifact: on mock-successful compile, pdf_path non-None + surfaced in
     message; manuscript_pdf/manuscript_hash stamped in note; status "ok".

All hermetic (tmp_instance). No real texlive needed for guard tests.
sr: SR-DRAFT-RENDER
Stdlib only.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_instance):
    from research_vault.config import load_config
    return load_config(reload=True)


@pytest.fixture
def manuscript_tree(cfg):
    """Scaffold a fresh manuscript tree for SR-DRAFT-RENDER tests."""
    from research_vault import manuscript as ms_mod
    note_path, tree_root, manifest = ms_mod.cmd_new(
        "demo-research", "ms-render",
        thesis="SR-DRAFT-RENDER: terminal PDF render test",
        scope=[],
        config=cfg,
    )
    return note_path, tree_root, manifest, cfg


def _write_empty_library(note_path: Path) -> None:
    """Write an empty library.json beside the manuscript's project notes root."""
    lib = note_path.parent.parent / "library.json"
    lib.write_text("[]", encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. blocked-prereq: missing toolchain is distinct from failed
# ---------------------------------------------------------------------------

class TestBlockedPrereq:
    """Missing toolchain → status 'blocked-prereq', NOT 'failed'; never a traceback."""

    def test_missing_pdflatex_returns_blocked_prereq_status(
        self, manuscript_tree, monkeypatch
    ):
        """Missing pdflatex on PATH → status is 'blocked-prereq' (not 'failed')."""
        from research_vault.manuscript import compile as ms_compile
        note_path, tree_root, _manifest, _cfg = manuscript_tree
        _write_empty_library(note_path)

        monkeypatch.setattr(ms_compile, "_find_tool", lambda name: None)
        result = ms_compile.run_compile(note_path, tree_root)

        assert result.get("status") == "blocked-prereq", (
            f"Expected status='blocked-prereq' for missing toolchain, "
            f"got: {result.get('status')!r}\n"
            f"message: {result.get('message', '')}"
        )

    def test_blocked_prereq_status_is_not_failed(
        self, manuscript_tree, monkeypatch
    ):
        """blocked-prereq and failed statuses must be distinguishable (regression guard)."""
        from research_vault.manuscript import compile as ms_compile
        note_path, tree_root, _manifest, _cfg = manuscript_tree
        _write_empty_library(note_path)

        monkeypatch.setattr(ms_compile, "_find_tool", lambda name: None)
        result = ms_compile.run_compile(note_path, tree_root)

        assert result.get("status") != "failed", (
            "A missing toolchain must NOT return status='failed' — "
            "the two failure modes must be distinguishable so operators can tell "
            "'install texlive to finish' from 'the draft is broken'."
        )

    def test_no_traceback_on_missing_toolchain(
        self, manuscript_tree, monkeypatch
    ):
        """Missing toolchain never produces a traceback in the message (exec-guard holds)."""
        from research_vault.manuscript import compile as ms_compile
        note_path, tree_root, _manifest, _cfg = manuscript_tree
        _write_empty_library(note_path)

        monkeypatch.setattr(ms_compile, "_find_tool", lambda name: None)
        result = ms_compile.run_compile(note_path, tree_root)

        msg = result.get("message", "")
        assert "Traceback" not in msg, (
            f"Traceback detected in missing-toolchain message:\n{msg}"
        )

    def test_friendly_install_message_on_missing_toolchain(
        self, manuscript_tree, monkeypatch
    ):
        """Missing toolchain message mentions texlive/pdflatex install instructions."""
        from research_vault.manuscript import compile as ms_compile
        note_path, tree_root, _manifest, _cfg = manuscript_tree
        _write_empty_library(note_path)

        monkeypatch.setattr(ms_compile, "_find_tool", lambda name: None)
        result = ms_compile.run_compile(note_path, tree_root)

        msg = result.get("message", "").lower()
        assert (
            "texlive" in msg or "pdflatex" in msg or "latex" in msg
        ), (
            f"Friendly install hint missing from blocked-prereq message:\n"
            f"{result.get('message', '')}"
        )

    def test_blocked_prereq_exit_code_is_nonzero(
        self, manuscript_tree, monkeypatch
    ):
        """blocked-prereq still returns exit_code != 0 (verb exits non-0 for operators)."""
        from research_vault.manuscript import compile as ms_compile
        note_path, tree_root, _manifest, _cfg = manuscript_tree
        _write_empty_library(note_path)

        monkeypatch.setattr(ms_compile, "_find_tool", lambda name: None)
        result = ms_compile.run_compile(note_path, tree_root)
        assert result["exit_code"] != 0


# ---------------------------------------------------------------------------
# 2. Grounding hard-fail → status "failed" (distinct from blocked-prereq)
# ---------------------------------------------------------------------------

class TestGroundingFail:
    """Unmatched \\cite hard-fail → status 'failed', NOT 'blocked-prereq' (un-regressed)."""

    def _plant_unmatched_cite(self, tree_root: Path) -> None:
        """Append \\cite{nonexistent2024} to main.tex."""
        main_tex = tree_root / "main.tex"
        existing = main_tex.read_text(encoding="utf-8")
        main_tex.write_text(
            existing + "\n% SR-DRAFT-RENDER test: unmatched cite\n"
            "\\cite{nonexistent2024}\n",
            encoding="utf-8",
        )

    def test_unmatched_cite_returns_failed_status(self, manuscript_tree):
        """An unmatched \\cite hard-fail returns status 'failed'."""
        from research_vault.manuscript import compile as ms_compile
        note_path, tree_root, _manifest, _cfg = manuscript_tree
        _write_empty_library(note_path)
        self._plant_unmatched_cite(tree_root)

        result = ms_compile.run_compile(note_path, tree_root)

        assert result.get("status") == "failed", (
            f"Expected status='failed' for unmatched \\cite, "
            f"got: {result.get('status')!r}\n"
            f"message: {result.get('message', '')}"
        )

    def test_unmatched_cite_status_not_blocked_prereq(self, manuscript_tree):
        """A grounding hard-fail must NOT return 'blocked-prereq' (must be distinguishable)."""
        from research_vault.manuscript import compile as ms_compile
        note_path, tree_root, _manifest, _cfg = manuscript_tree
        _write_empty_library(note_path)
        self._plant_unmatched_cite(tree_root)

        result = ms_compile.run_compile(note_path, tree_root)

        assert result.get("status") != "blocked-prereq", (
            "A grounding hard-fail must NOT return status='blocked-prereq' — "
            "only a missing toolchain should be 'blocked-prereq'."
        )

    def test_grounding_fail_exit_code_is_1(self, manuscript_tree):
        """Grounding hard-fail still returns exit_code=1 (un-regressed)."""
        from research_vault.manuscript import compile as ms_compile
        note_path, tree_root, _manifest, _cfg = manuscript_tree
        _write_empty_library(note_path)
        self._plant_unmatched_cite(tree_root)

        result = ms_compile.run_compile(note_path, tree_root)
        assert result["exit_code"] == 1


# ---------------------------------------------------------------------------
# 3. Compile node spec directs the real verb + blocked-vs-failed semantics
# ---------------------------------------------------------------------------

class TestCompileNodeSpec:
    """The compile DAG node's spec crisply directs rv manuscript <project> compile <id>."""

    def _get_compile_spec(self, manuscript_tree) -> str:
        """Return the spec string from the compile node in the scaffolded manifest."""
        _note_path, _tree_root, manifest, _cfg = manuscript_tree
        compile_node = next(
            (n for n in manifest["nodes"] if n["id"] == "compile"),
            None,
        )
        assert compile_node is not None, "compile node not found in manifest"
        return compile_node["spec"]

    def test_compile_spec_contains_rv_manuscript(self, manuscript_tree):
        """Compile node spec directs 'rv manuscript' (not bare pdflatex)."""
        spec = self._get_compile_spec(manuscript_tree)
        assert "rv manuscript" in spec, (
            f"compile node spec must contain 'rv manuscript' — got:\n{spec[:600]}"
        )

    def test_compile_spec_contains_compile_verb(self, manuscript_tree):
        """Compile node spec mentions the 'compile' verb."""
        spec = self._get_compile_spec(manuscript_tree)
        # Must explicitly say "compile" as the verb to run
        assert "compile" in spec, (
            f"compile node spec must mention the 'compile' verb:\n{spec[:600]}"
        )

    def test_compile_spec_states_blocked_prereq_semantics(self, manuscript_tree):
        """Compile node spec explains blocked-prereq: missing toolchain is NOT a failure."""
        spec = self._get_compile_spec(manuscript_tree)
        blocked_terms = ["blocked-prereq", "blocked_prereq", "install texlive", "toolchain"]
        assert any(t in spec.lower() for t in blocked_terms), (
            f"compile node spec must describe blocked-prereq semantics.\n"
            f"Expected one of {blocked_terms} in spec, got:\n{spec[:600]}"
        )

    def test_compile_spec_names_pdf_as_deliverable(self, manuscript_tree):
        """Compile node spec names main.pdf (or .pdf) as the node's deliverable."""
        spec = self._get_compile_spec(manuscript_tree)
        assert ".pdf" in spec or "main.pdf" in spec, (
            f"compile node spec must name the PDF deliverable — got:\n{spec[:600]}"
        )

    def test_compile_spec_distinguishes_failed_from_blocked(self, manuscript_tree):
        """Compile node spec distinguishes a real FAIL (grounding) from blocked-prereq."""
        spec = self._get_compile_spec(manuscript_tree)
        # Must mention "fail" or "failed" in the context of grounding
        fail_terms = ["fail", "failed", "grounding", "unmatched"]
        assert any(t in spec.lower() for t in fail_terms), (
            f"compile node spec must mention the real-fail case (grounding/unmatched cite).\n"
            f"Expected one of {fail_terms} in spec, got:\n{spec[:600]}"
        )


# ---------------------------------------------------------------------------
# 4. PDF terminal artifact: path surfaced + note stamped on success
# ---------------------------------------------------------------------------

class TestPdfTerminalArtifact:
    """On mock-successful compile: pdf_path surfaced, note fields stamped, status 'ok'."""

    def _mock_successful_compile(self, monkeypatch, ms_compile_mod) -> None:
        """Monkeypatch _find_tool + _run_cmd to simulate a successful pdflatex run."""
        monkeypatch.setattr(ms_compile_mod, "_find_tool", lambda name: f"/usr/bin/{name}")

        def _fake_run_cmd(cmd, *, cwd, timeout=120, env=None):
            # Create a stub main.pdf on the first call (simulates pdflatex producing the PDF)
            pdf = Path(cwd) / "main.pdf"
            if not pdf.exists():
                pdf.write_bytes(b"%PDF-1.4 stub")
            return 0, "mock pdflatex/bibtex output", ""

        monkeypatch.setattr(ms_compile_mod, "_run_cmd", _fake_run_cmd)

    def test_success_pdf_path_is_non_none(self, manuscript_tree, monkeypatch):
        """On successful compile, result['pdf_path'] is non-None."""
        from research_vault.manuscript import compile as ms_compile
        note_path, tree_root, _manifest, _cfg = manuscript_tree
        _write_empty_library(note_path)
        self._mock_successful_compile(monkeypatch, ms_compile)

        result = ms_compile.run_compile(note_path, tree_root)

        assert result["exit_code"] == 0, (
            f"Expected exit_code=0 for mock-successful compile, got {result['exit_code']}\n"
            f"message: {result.get('message', '')}"
        )
        assert result["pdf_path"] is not None, (
            "pdf_path must be non-None on successful compile"
        )

    def test_success_pdf_path_points_to_main_pdf(self, manuscript_tree, monkeypatch):
        """On successful compile, pdf_path points to main.pdf."""
        from research_vault.manuscript import compile as ms_compile
        note_path, tree_root, _manifest, _cfg = manuscript_tree
        _write_empty_library(note_path)
        self._mock_successful_compile(monkeypatch, ms_compile)

        result = ms_compile.run_compile(note_path, tree_root)

        assert result["exit_code"] == 0
        assert "main.pdf" in str(result["pdf_path"]), (
            f"pdf_path must point to main.pdf — got: {result['pdf_path']}"
        )

    def test_success_message_surfaces_pdf_path(self, manuscript_tree, monkeypatch):
        """On successful compile, the PDF path appears in the success message."""
        from research_vault.manuscript import compile as ms_compile
        note_path, tree_root, _manifest, _cfg = manuscript_tree
        _write_empty_library(note_path)
        self._mock_successful_compile(monkeypatch, ms_compile)

        result = ms_compile.run_compile(note_path, tree_root)

        assert result["exit_code"] == 0
        msg = result.get("message", "")
        assert ".pdf" in msg or "main.pdf" in msg, (
            f"Success message must surface the PDF path — got:\n{msg}"
        )

    def test_success_stamps_manuscript_pdf_in_note(self, manuscript_tree, monkeypatch):
        """On successful compile, manuscript_pdf field is stamped in the note."""
        from research_vault.manuscript import compile as ms_compile
        note_path, tree_root, _manifest, _cfg = manuscript_tree
        _write_empty_library(note_path)
        self._mock_successful_compile(monkeypatch, ms_compile)

        ms_compile.run_compile(note_path, tree_root)

        note_content = note_path.read_text(encoding="utf-8")
        m = re.search(r"^manuscript_pdf:\s*(.+)$", note_content, re.MULTILINE)
        assert m and m.group(1).strip(), (
            f"manuscript_pdf must be stamped non-empty in note after compile:\n"
            f"{note_content[:600]}"
        )

    def test_success_stamps_manuscript_hash_in_note(self, manuscript_tree, monkeypatch):
        """On successful compile, manuscript_hash field is stamped with sha256: prefix."""
        from research_vault.manuscript import compile as ms_compile
        note_path, tree_root, _manifest, _cfg = manuscript_tree
        _write_empty_library(note_path)
        self._mock_successful_compile(monkeypatch, ms_compile)

        ms_compile.run_compile(note_path, tree_root)

        note_content = note_path.read_text(encoding="utf-8")
        m = re.search(r"^manuscript_hash:\s*(.+)$", note_content, re.MULTILINE)
        assert m and m.group(1).strip().startswith("sha256:"), (
            f"manuscript_hash must start with 'sha256:' after compile:\n"
            f"{note_content[:600]}"
        )

    def test_success_status_is_ok(self, manuscript_tree, monkeypatch):
        """On successful compile, result['status'] is 'ok'."""
        from research_vault.manuscript import compile as ms_compile
        note_path, tree_root, _manifest, _cfg = manuscript_tree
        _write_empty_library(note_path)
        self._mock_successful_compile(monkeypatch, ms_compile)

        result = ms_compile.run_compile(note_path, tree_root)

        assert result["exit_code"] == 0
        assert result.get("status") == "ok", (
            f"Expected status='ok' on successful compile, got: {result.get('status')!r}"
        )
