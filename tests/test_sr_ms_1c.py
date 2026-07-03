"""test_sr_ms_1c.py — SR-MS-1c: draft-time macro-visibility prep seam.

Covers:
  1. run_prep populates refs.bib (BibTeX entries) without producing a PDF
  2. run_prep populates results.tex (\newcommand macros) without producing a PDF
  3. run_prep populates sections/appendix-repro.tex without producing a PDF
  4. run_prep is idempotent: running twice → identical file contents (no double-append)
  5. run_prep works without pdflatex (exec-guard does NOT trigger for prep)
  6. prep-then-compile → same grounded bib/macros/appendix as compile-alone (idempotency)
  7. cmd_prep public API: resolves library_path + experiment_notes same as cmd_compile
  8. CLI: rv manuscript compile --prep-only <id> exits 0, no PDF produced

All hermetic (tmp_instance). Zero ~/vault reads.
sr: SR-MS-1c
Stdlib only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import load_config


# ---------------------------------------------------------------------------
# Helpers (mirrors test_sr_ms_1b helpers)
# ---------------------------------------------------------------------------

def _make_library_json(path: Path, items: list[dict]) -> Path:
    lib = path / "library.json"
    lib.write_text(json.dumps(items, indent=2), encoding="utf-8")
    return lib


def _zotero_item(citekey: str, *, doi: str = "10.1234/test") -> dict:
    return {
        "key": "ZKEY" + citekey[:4].upper(),
        "data": {
            "itemType": "journalArticle",
            "title": "A Test Paper",
            "creators": [{"creatorType": "author", "firstName": "A", "lastName": "Smith"}],
            "date": "2024",
            "publicationTitle": "J. Science",
            "volume": "1",
            "issue": "1",
            "pages": "1--5",
            "DOI": doi,
            "url": f"https://doi.org/{doi}",
            "abstractNote": "Abstract.",
            "extra": f"Citation Key: {citekey}",
        },
    }


def _write_results_json(tree_root: Path, exp_id: str) -> tuple[Path, str]:
    """Write a minimal results.json and return (path, sha256_hex)."""
    import hashlib
    payload = json.dumps({"accuracy": 0.87, "f1": 0.83})
    h = hashlib.sha256(payload.encode()).hexdigest()
    results_dir = tree_root.parent.parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    results_file = results_dir / f"{exp_id}.json"
    results_file.write_text(payload, encoding="utf-8")
    return results_file, h


def _write_experiment_note(notes_dir: Path, exp_id: str,
                           results_file: Path, results_hash: str) -> Path:
    """Write a minimal experiments/<id>.md note with results_* fields."""
    exp_dir = notes_dir / "experiments"
    exp_dir.mkdir(parents=True, exist_ok=True)
    note_path = exp_dir / f"{exp_id}.md"
    note_path.write_text(
        "---\n"
        "type: experiment\n"
        f"id: {exp_id}\n"
        f"results_location: {results_file}\n"
        f"results_hash: sha256:{results_hash}\n"
        "results_commit: abc1234\n"
        "---\n"
        "Experiment note.\n",
        encoding="utf-8",
    )
    return note_path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_instance):
    return load_config(reload=True)


@pytest.fixture
def manuscript_tree(cfg, tmp_instance):
    """Scaffold a fresh manuscript tree."""
    from research_vault import manuscript as ms_mod
    note_path, tree_root, manifest = ms_mod.cmd_new(
        "demo-research", "ms-prep-test",
        thesis="Draft-time macro-visibility test",
        scope=[],
        config=cfg,
    )
    return note_path, tree_root, cfg


# ---------------------------------------------------------------------------
# 1. run_prep populates refs.bib (no PDF)
# ---------------------------------------------------------------------------

class TestRunPrepBib:

    def test_prep_writes_refs_bib(self, manuscript_tree, tmp_instance):
        """run_prep writes refs.bib into the manuscript tree."""
        from research_vault.manuscript.compile import run_prep
        note_path, tree_root, cfg = manuscript_tree

        lib = _make_library_json(tmp_instance, [_zotero_item("smith2024Prep")])
        result = run_prep(note_path, tree_root, library_path=lib)

        bib_path = tree_root / "refs.bib"
        assert bib_path.exists(), "refs.bib should exist after run_prep"

    def test_prep_matched_cite_in_bib(self, manuscript_tree, tmp_instance):
        r"""run_prep: a \cite{key} matched in library.json → BibTeX entry in refs.bib."""
        from research_vault.manuscript.compile import run_prep
        note_path, tree_root, cfg = manuscript_tree

        lib = _make_library_json(tmp_instance, [_zotero_item("smith2024Match")])
        # Write a cite into the manuscript
        tex = tree_root / "sections" / "results-discussion.tex"
        tex.write_text(r"See \cite{smith2024Match}.", encoding="utf-8")

        result = run_prep(note_path, tree_root, library_path=lib)

        bib_content = (tree_root / "refs.bib").read_text(encoding="utf-8")
        assert "smith2024Match" in bib_content

    def test_prep_no_pdf_produced(self, manuscript_tree, tmp_instance):
        """run_prep does NOT produce a PDF file — builders only."""
        from research_vault.manuscript.compile import run_prep
        note_path, tree_root, cfg = manuscript_tree

        lib = _make_library_json(tmp_instance, [])
        run_prep(note_path, tree_root, library_path=lib)

        pdf = tree_root / "main.pdf"
        assert not pdf.exists(), "run_prep must NOT produce a PDF"

    def test_prep_result_no_pdf_path(self, manuscript_tree, tmp_instance):
        """run_prep return dict has pdf_path=None (no render attempted)."""
        from research_vault.manuscript.compile import run_prep
        note_path, tree_root, cfg = manuscript_tree

        lib = _make_library_json(tmp_instance, [])
        result = run_prep(note_path, tree_root, library_path=lib)

        assert result.get("pdf_path") is None

    def test_prep_exit_code_0_on_success(self, manuscript_tree, tmp_instance):
        """run_prep returns exit_code=0 when builders succeed."""
        from research_vault.manuscript.compile import run_prep
        note_path, tree_root, cfg = manuscript_tree

        lib = _make_library_json(tmp_instance, [])
        result = run_prep(note_path, tree_root, library_path=lib)

        assert result.get("exit_code") == 0


# ---------------------------------------------------------------------------
# 2. run_prep populates results.tex (\newcommand macros, no PDF)
# ---------------------------------------------------------------------------

class TestRunPrepResultsTex:

    def test_prep_writes_results_tex(self, manuscript_tree, tmp_instance):
        """run_prep writes results.tex into the manuscript tree."""
        from research_vault.manuscript.compile import run_prep
        note_path, tree_root, cfg = manuscript_tree

        lib = _make_library_json(tmp_instance, [])
        run_prep(note_path, tree_root, library_path=lib)

        results_tex = tree_root / "results.tex"
        assert results_tex.exists(), "results.tex should exist after run_prep"

    def test_prep_injects_newcommand_macros(self, manuscript_tree, tmp_instance):
        r"""run_prep injects \newcommand macros from hash-verified experiment results."""
        from research_vault.manuscript.compile import run_prep
        note_path, tree_root, cfg = manuscript_tree

        # Set up experiment note + results.json
        notes_dir = cfg.project_notes_dir("demo-research")
        results_file, results_hash = _write_results_json(tree_root, "exp-prep")
        exp_note = _write_experiment_note(notes_dir, "exp-prep", results_file, results_hash)

        lib = _make_library_json(tmp_instance, [])
        result = run_prep(
            note_path, tree_root,
            library_path=lib,
            experiment_notes=[exp_note],
        )

        results_tex = (tree_root / "results.tex").read_text(encoding="utf-8")
        # Should have at least one \newcommand macro
        assert r"\newcommand" in results_tex, \
            "results.tex should contain \\newcommand macros after run_prep"


# ---------------------------------------------------------------------------
# 3. run_prep populates appendix-repro.tex (no PDF)
# ---------------------------------------------------------------------------

class TestRunPrepAppendix:

    def test_prep_writes_appendix_tex(self, manuscript_tree, tmp_instance):
        """run_prep writes sections/appendix-repro.tex."""
        from research_vault.manuscript.compile import run_prep
        note_path, tree_root, cfg = manuscript_tree

        lib = _make_library_json(tmp_instance, [])
        run_prep(note_path, tree_root, library_path=lib)

        appendix_tex = tree_root / "sections" / "appendix-repro.tex"
        assert appendix_tex.exists(), "sections/appendix-repro.tex should exist after run_prep"


# ---------------------------------------------------------------------------
# 4. Idempotency: prep→prep → identical outputs
# ---------------------------------------------------------------------------

class TestRunPrepIdempotency:

    def test_prep_twice_refs_bib_identical(self, manuscript_tree, tmp_instance):
        """run_prep twice → refs.bib content is identical (no double-append)."""
        from research_vault.manuscript.compile import run_prep
        note_path, tree_root, cfg = manuscript_tree

        lib = _make_library_json(tmp_instance, [_zotero_item("smith2024Idem")])
        tex = tree_root / "sections" / "results-discussion.tex"
        tex.write_text(r"\cite{smith2024Idem}", encoding="utf-8")

        run_prep(note_path, tree_root, library_path=lib)
        content_1 = (tree_root / "refs.bib").read_text(encoding="utf-8")

        run_prep(note_path, tree_root, library_path=lib)
        content_2 = (tree_root / "refs.bib").read_text(encoding="utf-8")

        assert content_1 == content_2, \
            "refs.bib must be identical after two run_prep calls (idempotent)"

    def test_prep_twice_results_tex_identical(self, manuscript_tree, tmp_instance):
        r"""run_prep twice → results.tex content is identical (no double \newcommand)."""
        from research_vault.manuscript.compile import run_prep
        note_path, tree_root, cfg = manuscript_tree

        notes_dir = cfg.project_notes_dir("demo-research")
        results_file, results_hash = _write_results_json(tree_root, "exp-idem")
        exp_note = _write_experiment_note(notes_dir, "exp-idem", results_file, results_hash)
        lib = _make_library_json(tmp_instance, [])

        run_prep(note_path, tree_root, library_path=lib, experiment_notes=[exp_note])
        content_1 = (tree_root / "results.tex").read_text(encoding="utf-8")

        run_prep(note_path, tree_root, library_path=lib, experiment_notes=[exp_note])
        content_2 = (tree_root / "results.tex").read_text(encoding="utf-8")

        assert content_1 == content_2, \
            "results.tex must be identical after two run_prep calls (idempotent)"

    def test_prep_twice_appendix_identical(self, manuscript_tree, tmp_instance):
        """run_prep twice → appendix-repro.tex content is identical."""
        from research_vault.manuscript.compile import run_prep
        note_path, tree_root, cfg = manuscript_tree
        lib = _make_library_json(tmp_instance, [])

        run_prep(note_path, tree_root, library_path=lib)
        content_1 = (tree_root / "sections" / "appendix-repro.tex").read_text(encoding="utf-8")

        run_prep(note_path, tree_root, library_path=lib)
        content_2 = (tree_root / "sections" / "appendix-repro.tex").read_text(encoding="utf-8")

        assert content_1 == content_2, \
            "appendix-repro.tex must be identical after two run_prep calls (idempotent)"

    def test_prep_then_compile_bib_same_as_compile_alone(self, manuscript_tree, tmp_instance):
        """prep→compile → refs.bib identical to compile-alone (no double-inject)."""
        from research_vault.manuscript.compile import run_prep, run_compile
        note_path_1, tree_root_1, cfg = manuscript_tree

        # Second manuscript tree for compile-alone baseline
        from research_vault import manuscript as ms_mod
        note_path_2, tree_root_2, _ = ms_mod.cmd_new(
            "demo-research", "ms-prep-baseline",
            thesis="Baseline compile-alone test",
            scope=[],
            config=cfg,
        )

        lib = _make_library_json(tmp_instance, [_zotero_item("smith2024Both")])
        tex1 = tree_root_1 / "sections" / "results-discussion.tex"
        tex2 = tree_root_2 / "sections" / "results-discussion.tex"
        tex1.write_text(r"\cite{smith2024Both}", encoding="utf-8")
        tex2.write_text(r"\cite{smith2024Both}", encoding="utf-8")

        # Path 1: prep then compile (compile re-runs builders)
        run_prep(note_path_1, tree_root_1, library_path=lib)
        # compile will re-run builders then try pdflatex (may fail w/o texlive)
        run_compile(note_path_1, tree_root_1, library_path=lib)

        # Path 2: compile alone
        run_compile(note_path_2, tree_root_2, library_path=lib)

        # The bib output from builders must be identical regardless of path
        bib_1 = (tree_root_1 / "refs.bib").read_text(encoding="utf-8")
        bib_2 = (tree_root_2 / "refs.bib").read_text(encoding="utf-8")
        assert bib_1 == bib_2, \
            "refs.bib must be identical whether built via prep+compile or compile-alone"


# ---------------------------------------------------------------------------
# 5. run_prep works without pdflatex (no exec-guard failure)
# ---------------------------------------------------------------------------

class TestRunPrepNoPdflatex:

    def test_prep_does_not_check_for_pdflatex(self, manuscript_tree, tmp_instance, monkeypatch):
        """run_prep succeeds even when pdflatex is not on PATH."""
        from research_vault.manuscript import compile as compile_mod
        from research_vault.manuscript.compile import run_prep
        note_path, tree_root, cfg = manuscript_tree

        # Simulate pdflatex absent by monkeypatching _find_tool
        original_find = compile_mod._find_tool

        def _absent(name: str) -> str | None:
            if name in ("pdflatex", "bibtex", "chktex"):
                return None
            return original_find(name)

        monkeypatch.setattr(compile_mod, "_find_tool", _absent)

        lib = _make_library_json(tmp_instance, [])
        result = run_prep(note_path, tree_root, library_path=lib)

        # run_prep must succeed (exit_code=0) — it doesn't need pdflatex
        assert result.get("exit_code") == 0, \
            f"run_prep must not require pdflatex; got: {result.get('message')}"
        assert not (tree_root / "main.pdf").exists(), "No PDF from prep-only"


# ---------------------------------------------------------------------------
# 6. cmd_prep public API
# ---------------------------------------------------------------------------

class TestCmdPrep:

    def test_cmd_prep_exists(self):
        """cmd_prep is importable from research_vault.manuscript."""
        from research_vault.manuscript import cmd_prep  # noqa: F401
        assert callable(cmd_prep)

    def test_cmd_prep_returns_dict(self, manuscript_tree):
        """cmd_prep returns a dict with exit_code and builder_warnings."""
        from research_vault.manuscript import cmd_prep
        note_path, tree_root, cfg = manuscript_tree

        result = cmd_prep("demo-research", "ms-prep-test", config=cfg)

        assert isinstance(result, dict)
        assert "exit_code" in result
        assert "builder_warnings" in result

    def test_cmd_prep_exit_code_0(self, manuscript_tree):
        """cmd_prep returns exit_code=0 on success."""
        from research_vault.manuscript import cmd_prep
        note_path, tree_root, cfg = manuscript_tree

        result = cmd_prep("demo-research", "ms-prep-test", config=cfg)
        assert result.get("exit_code") == 0


# ---------------------------------------------------------------------------
# 7. CLI surface: rv manuscript compile --prep-only
# ---------------------------------------------------------------------------

class TestCliPrepOnly:

    def _build_args(self, project: str, ms_id: str, prep_only: bool = True):
        """Build a Namespace matching what argparse produces for compile --prep-only."""
        import argparse
        ns = argparse.Namespace(
            manuscript_cmd="compile",
            project=project,
            ms_id=ms_id,
            prep_only=prep_only,
        )
        return ns

    def test_cli_prep_only_flag_accepted(self, manuscript_tree, cfg):
        """CLI compile --prep-only dispatches to prep and returns 0."""
        from research_vault.manuscript.verbs import run
        note_path, tree_root, _ = manuscript_tree

        args = self._build_args("demo-research", "ms-prep-test", prep_only=True)
        rc = run(args)

        assert rc == 0, "rv manuscript compile --prep-only should return 0"

    def test_cli_prep_only_no_pdf(self, manuscript_tree, cfg):
        """CLI compile --prep-only does not produce a PDF."""
        from research_vault.manuscript.verbs import run
        note_path, tree_root, _ = manuscript_tree

        args = self._build_args("demo-research", "ms-prep-test", prep_only=True)
        run(args)

        assert not (tree_root / "main.pdf").exists(), \
            "--prep-only must not produce a PDF"

    def test_cli_prep_only_default_false(self):
        """The --prep-only flag defaults to False (normal compile when omitted)."""
        from research_vault.manuscript.verbs import build_parser
        parser = build_parser()
        # Parse 'compile' without --prep-only
        args = parser.parse_args(["demo-research", "compile", "ms-001"])
        assert getattr(args, "prep_only", False) is False
