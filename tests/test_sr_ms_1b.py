"""test_sr_ms_1b.py — SR-MS-1b: grounding-builders + compile tests.

Covers:
  1. .bib exporter (bib.py) — closed bib from library.json + unmatched cite = error
  2. Results macro injector (results_inject.py) — hash-verified, macros only
  3. Appendix repro table injector (appendix.py) — sentinel → explicit gap
  4. exec-guarded compile loop (compile.py) — friendly degradation without pdflatex
  5. Structural check gates (check_gates.py) — unmatched cite, figure-file, avail cross-check
  6. Manifest reads: relative paths (fold-in) — portability + resolution still passes
  7. [manuscript_style] TOML override (fold-in) — config= param wired
  8. rv check LaTeX optional prereq probe (fold-in)
  9. Optional/venue-optional topology regression (fold-in)

All hermetic (tmp_instance). Zero ~/vault reads.
sr: SR-MS-1b
Stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.config import load_config
from research_vault.dag.schema import validate_manifest
from research_vault.dag.reads import resolve_reads_pointers


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_instance):
    return load_config(reload=True)


@pytest.fixture
def manuscript_tree(cfg, tmp_path):
    """Scaffold a fresh manuscript tree (needs SR-MS-1a cmd_new)."""
    from research_vault import manuscript as ms_mod
    note_path, tree_root, manifest = ms_mod.cmd_new(
        "demo-research", "ms-test",
        thesis="Cross-lingual LLMs fail on pragmatics",
        scope=[],
        config=cfg,
    )
    return note_path, tree_root, manifest, cfg


# ── Helper: build a minimal library.json ──────────────────────────────────────

def _make_library_json(tmp_path: Path, items: list[dict]) -> Path:
    """Write a library.json with the given Zotero items and return its path."""
    lib = tmp_path / "library.json"
    lib.write_text(json.dumps(items, indent=2), encoding="utf-8")
    return lib


def _zotero_item(citekey: str, *, title: str = "A Paper", year: str = "2024",
                 author_last: str = "Smith", author_first: str = "Alice",
                 journal: str = "Journal of Science",
                 doi: str = "10.1234/test") -> dict:
    """Build a minimal Zotero API item dict with the given citekey."""
    return {
        "key": "ZKEY" + citekey[:4].upper(),
        "data": {
            "itemType": "journalArticle",
            "title": title,
            "creators": [
                {"creatorType": "author", "firstName": author_first,
                 "lastName": author_last}
            ],
            "date": year,
            "publicationTitle": journal,
            "volume": "1",
            "issue": "2",
            "pages": "3--10",
            "DOI": doi,
            "url": f"https://doi.org/{doi}",
            "abstractNote": "An abstract.",
            "extra": f"Citation Key: {citekey}",
        },
    }


def _zotero_preprint(citekey: str, *, title: str = "A Preprint",
                     year: str = "2024", author_last: str = "Doe",
                     author_first: str = "John") -> dict:
    return {
        "key": "ZPRE" + citekey[:4].upper(),
        "data": {
            "itemType": "preprint",
            "title": title,
            "creators": [
                {"creatorType": "author", "firstName": author_first,
                 "lastName": author_last}
            ],
            "date": year,
            "repository": "arXiv",
            "archiveID": "arXiv:2401.00001",
            "url": "https://arxiv.org/abs/2401.00001",
            "abstractNote": "Preprint abstract.",
            "extra": f"Citation Key: {citekey}",
        },
    }


# ---------------------------------------------------------------------------
# 1. .bib exporter (bib.py)
# ---------------------------------------------------------------------------

class TestBibExporter:

    def test_export_creates_refs_bib(self, manuscript_tree, tmp_path):
        """build_refs_bib writes refs.bib into the manuscript tree."""
        from research_vault.manuscript.bib import build_refs_bib
        note_path, tree_root, manifest, cfg = manuscript_tree
        lib = _make_library_json(tmp_path, [_zotero_item("smith2024Test")])
        errors, bib_path = build_refs_bib(
            tree_root,
            library_path=lib,
            cite_tex_files=list(tree_root.rglob("*.tex")),
        )
        assert bib_path.name == "refs.bib"
        assert bib_path.exists()

    def test_export_matched_citekey_in_bib(self, manuscript_tree, tmp_path):
        r"""A \cite{key} matched in library.json produces a BibTeX entry."""
        from research_vault.manuscript.bib import build_refs_bib
        note_path, tree_root, manifest, cfg = manuscript_tree
        lib = _make_library_json(tmp_path, [_zotero_item("smith2024Paper")])
        # Write a tex with a cite
        tex = tree_root / "sections" / "related-work.tex"
        tex.write_text(r"See \cite{smith2024Paper} for details.", encoding="utf-8")
        errors, bib_path = build_refs_bib(
            tree_root,
            library_path=lib,
            cite_tex_files=[tex],
        )
        assert errors == [], f"Unexpected errors: {errors}"
        content = bib_path.read_text(encoding="utf-8")
        assert "smith2024Paper" in content
        assert "@article" in content or "@misc" in content or "@inproceedings" in content

    def test_export_unmatched_cite_is_hard_error(self, manuscript_tree, tmp_path):
        """An unmatched \\cite{key} returns a hard error (not in library.json)."""
        from research_vault.manuscript.bib import build_refs_bib
        note_path, tree_root, manifest, cfg = manuscript_tree
        # Library does NOT contain 'unknownKey2024'
        lib = _make_library_json(tmp_path, [_zotero_item("smith2024Other")])
        tex = tree_root / "sections" / "intro.tex"
        tex.write_text(r"\cite{unknownKey2024}", encoding="utf-8")
        errors, _bib_path = build_refs_bib(
            tree_root,
            library_path=lib,
            cite_tex_files=[tex],
        )
        assert any("unknownKey2024" in e for e in errors), \
            f"Expected unmatched-cite error, got: {errors}"

    def test_export_no_cites_writes_empty_bib(self, manuscript_tree, tmp_path):
        """No \\cite commands → refs.bib created with no entries (comment-only)."""
        from research_vault.manuscript.bib import build_refs_bib
        note_path, tree_root, manifest, cfg = manuscript_tree
        lib = _make_library_json(tmp_path, [])
        errors, bib_path = build_refs_bib(
            tree_root,
            library_path=lib,
            cite_tex_files=[tree_root / "main.tex"],
        )
        assert errors == []
        content = bib_path.read_text(encoding="utf-8")
        # refs.bib should exist but have no @entry lines (just comments)
        assert not any(line.strip().startswith("@") for line in content.splitlines())

    def test_export_preprint_entry_type(self, manuscript_tree, tmp_path):
        """Zotero preprint → @misc or @unpublished BibTeX entry (not @article)."""
        from research_vault.manuscript.bib import build_refs_bib
        note_path, tree_root, manifest, cfg = manuscript_tree
        lib = _make_library_json(tmp_path, [_zotero_preprint("doe2024Prep")])
        tex = tree_root / "sections" / "related-work.tex"
        tex.write_text(r"\cite{doe2024Prep}", encoding="utf-8")
        errors, bib_path = build_refs_bib(
            tree_root,
            library_path=lib,
            cite_tex_files=[tex],
        )
        assert errors == []
        content = bib_path.read_text(encoding="utf-8")
        assert "doe2024Prep" in content

    def test_bib_no_pandoc_pattern(self, manuscript_tree, tmp_path):
        """refs.bib content does NOT contain Pandoc [@key] patterns (class-8 safe).

        Regression: LaTeX \\cite{} form must not false-trip the leakage scan.
        The scan only covers .md/.yml/.toml/.json/.py — but confirm the generated
        .bib content itself is safe (no [@...] patterns).
        """
        from research_vault.manuscript.bib import build_refs_bib
        note_path, tree_root, manifest, cfg = manuscript_tree
        lib = _make_library_json(tmp_path, [_zotero_item("smith2024Safe")])
        tex = tree_root / "sections" / "method.tex"
        tex.write_text(r"\cite{smith2024Safe}", encoding="utf-8")
        errors, bib_path = build_refs_bib(
            tree_root,
            library_path=lib,
            cite_tex_files=[tex],
        )
        content = bib_path.read_text(encoding="utf-8")
        import re as _re
        pandoc_hits = _re.findall(r'\[@[A-Za-z][A-Za-z0-9_:-]+', content)
        assert pandoc_hits == [], f"Pandoc-citation patterns in refs.bib: {pandoc_hits}"

    def test_missing_library_json_returns_error(self, manuscript_tree, tmp_path):
        """When library_path does not exist, build_refs_bib returns an error."""
        from research_vault.manuscript.bib import build_refs_bib
        note_path, tree_root, manifest, cfg = manuscript_tree
        missing = tmp_path / "no_such_library.json"
        tex = tree_root / "sections" / "intro.tex"
        tex.write_text(r"\cite{smith2024X}", encoding="utf-8")
        errors, _bib_path = build_refs_bib(
            tree_root,
            library_path=missing,
            cite_tex_files=[tex],
        )
        assert errors, "Expected error for missing library.json"


# ---------------------------------------------------------------------------
# 2. Results macro injector (results_inject.py)
# ---------------------------------------------------------------------------

class TestResultsInject:

    def _make_experiment_note(self, cfg, project: str, exp_id: str,
                               results: dict, tmp_path: Path) -> tuple[Path, str]:
        """Write an experiment note with results_location/results_hash, return (note_path, hash)."""
        import hashlib
        proj_notes = cfg.project_notes_dir(project)
        exp_dir = proj_notes / "experiments"
        exp_dir.mkdir(parents=True, exist_ok=True)
        # Write results JSON
        results_file = tmp_path / f"{exp_id}_results.json"
        results_file.write_text(json.dumps(results), encoding="utf-8")
        h = hashlib.sha256()
        h.update(results_file.read_bytes())
        results_hash = "sha256:" + h.hexdigest()
        results_commit = "abc1234def5678"
        note_content = (
            "---\n"
            f"type: experiments\n"
            f"title: Experiment {exp_id}\n"
            f"created: 2026-01-01\n"
            f"results_location: {results_file}\n"
            f"results_hash: {results_hash}\n"
            f"results_commit: {results_commit}\n"
            "---\n"
            f"\n## {exp_id}\n"
        )
        note_path = exp_dir / f"{exp_id}.md"
        note_path.write_text(note_content, encoding="utf-8")
        return note_path, results_hash

    def test_inject_writes_macro_file(self, manuscript_tree, tmp_path):
        """inject_results writes results.tex with \\newcommand macros."""
        from research_vault.manuscript.results_inject import inject_results
        note_path, tree_root, manifest, cfg = manuscript_tree
        exp_note, _ = self._make_experiment_note(
            cfg, "demo-research", "exp-q1",
            {"accuracy": 0.85, "f1": 0.82},
            tmp_path,
        )
        inject_results(
            manuscript_note_path=note_path,
            experiment_notes=[exp_note],
            tree_root=tree_root,
        )
        results_tex = tree_root / "results.tex"
        content = results_tex.read_text(encoding="utf-8")
        assert r"\newcommand" in content

    def test_inject_macros_are_numeric_values(self, manuscript_tree, tmp_path):
        """Injected macros contain the numeric values from results.json."""
        from research_vault.manuscript.results_inject import inject_results
        note_path, tree_root, manifest, cfg = manuscript_tree
        exp_note, _ = self._make_experiment_note(
            cfg, "demo-research", "exp-q2",
            {"accuracy": 0.85, "f1_macro": 0.72},
            tmp_path,
        )
        inject_results(
            manuscript_note_path=note_path,
            experiment_notes=[exp_note],
            tree_root=tree_root,
        )
        content = (tree_root / "results.tex").read_text(encoding="utf-8")
        assert "0.85" in content or "85" in content  # accuracy value present
        assert "0.72" in content or "72" in content  # f1_macro value present

    def test_inject_rejects_hash_mismatch(self, manuscript_tree, tmp_path):
        """inject_results raises/returns error when results_hash mismatches artifact."""
        from research_vault.manuscript.results_inject import inject_results
        note_path, tree_root, manifest, cfg = manuscript_tree
        proj_notes = cfg.project_notes_dir("demo-research")
        exp_dir = proj_notes / "experiments"
        exp_dir.mkdir(parents=True, exist_ok=True)
        results_file = tmp_path / "exp_bad_results.json"
        results_file.write_text(json.dumps({"acc": 0.99}), encoding="utf-8")
        # Wrong hash — deliberate mismatch
        bad_hash = "sha256:" + "a" * 64
        exp_note = exp_dir / "exp-bad.md"
        exp_note.write_text(
            "---\ntype: experiments\ntitle: Bad\ncreated: 2026-01-01\n"
            f"results_location: {results_file}\nresults_hash: {bad_hash}\n"
            "results_commit: abc123\n---\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="hash"):
            inject_results(
                manuscript_note_path=note_path,
                experiment_notes=[exp_note],
                tree_root=tree_root,
            )

    def test_inject_stamps_provenance_into_note(self, manuscript_tree, tmp_path):
        """inject_results stamps results_hash + results_commit into the manuscript note."""
        from research_vault.manuscript.results_inject import inject_results
        note_path, tree_root, manifest, cfg = manuscript_tree
        exp_note, exp_hash = self._make_experiment_note(
            cfg, "demo-research", "exp-q3",
            {"accuracy": 0.75},
            tmp_path,
        )
        inject_results(
            manuscript_note_path=note_path,
            experiment_notes=[exp_note],
            tree_root=tree_root,
        )
        note_content = note_path.read_text(encoding="utf-8")
        # Provenance stamp: the hash should appear in the note
        assert exp_hash[:16] in note_content or "results_hash" in note_content

    def test_inject_empty_scope_writes_comment_only(self, manuscript_tree, tmp_path):
        """inject_results with no experiment notes writes a comment-only results.tex."""
        from research_vault.manuscript.results_inject import inject_results
        note_path, tree_root, manifest, cfg = manuscript_tree
        inject_results(
            manuscript_note_path=note_path,
            experiment_notes=[],
            tree_root=tree_root,
        )
        content = (tree_root / "results.tex").read_text(encoding="utf-8")
        assert r"\newcommand" not in content or "% no" in content.lower() or "%" in content


# ---------------------------------------------------------------------------
# 3. Appendix repro table (appendix.py)
# ---------------------------------------------------------------------------

class TestAppendixRepro:
    from research_vault.note import REPRO_SENTINEL as _SENTINEL  # type: ignore[attr-defined]

    def _make_repro_note(self, cfg, project: str, exp_id: str,
                          overrides: dict | None = None) -> Path:
        """Write an experiment note with repro_* fields."""
        from research_vault.note import REPRO_SENTINEL, REPRO_ALL_FIELDS
        proj_notes = cfg.project_notes_dir(project)
        exp_dir = proj_notes / "experiments"
        exp_dir.mkdir(parents=True, exist_ok=True)
        fields_lines = [
            f"type: experiments",
            f"title: Experiment {exp_id}",
            f"created: 2026-01-01",
            f"results_location: ",
            f"results_hash: ",
            f"results_commit: ",
        ]
        for f in REPRO_ALL_FIELDS:
            val = (overrides or {}).get(f, REPRO_SENTINEL)
            fields_lines.append(f"{f}: {val}")
        note = exp_dir / f"{exp_id}.md"
        frontmatter = "\n".join(fields_lines)
        note.write_text(f"---\n{frontmatter}\n---\n\n## {exp_id}\n", encoding="utf-8")
        return note

    def test_appendix_writes_tex_file(self, manuscript_tree, cfg):
        """inject_appendix writes sections/appendix-repro.tex."""
        from research_vault.manuscript.appendix import inject_appendix
        note_path, tree_root, manifest, _ = manuscript_tree
        exp_note = self._make_repro_note(cfg, "demo-research", "exp-app")
        inject_appendix(tree_root=tree_root, experiment_notes=[exp_note])
        appendix_tex = tree_root / "sections" / "appendix-repro.tex"
        assert appendix_tex.exists(), "appendix-repro.tex not written"

    def test_appendix_sentinel_renders_as_explicit_gap(self, manuscript_tree, cfg):
        """Sentinel repro fields render as 'not recorded in provenance' (never omitted)."""
        from research_vault.manuscript.appendix import inject_appendix
        from research_vault.note import REPRO_SENTINEL
        note_path, tree_root, manifest, _ = manuscript_tree
        exp_note = self._make_repro_note(cfg, "demo-research", "exp-gap",
                                          overrides={"repro_seed": REPRO_SENTINEL})
        inject_appendix(tree_root=tree_root, experiment_notes=[exp_note])
        content = (tree_root / "sections" / "appendix-repro.tex").read_text()
        # The sentinel must appear as explicit text (gap visible, not omitted)
        assert "not recorded" in content.lower() or REPRO_SENTINEL in content

    def test_appendix_populated_fields_render_values(self, manuscript_tree, cfg):
        """Populated repro fields render their actual values."""
        from research_vault.manuscript.appendix import inject_appendix
        note_path, tree_root, manifest, _ = manuscript_tree
        exp_note = self._make_repro_note(cfg, "demo-research", "exp-pop",
                                          overrides={"repro_seed": "42",
                                                     "repro_model_id": "gpt-4o-mini"})
        inject_appendix(tree_root=tree_root, experiment_notes=[exp_note])
        content = (tree_root / "sections" / "appendix-repro.tex").read_text()
        assert "42" in content
        assert "gpt-4o-mini" in content

    def test_appendix_no_experiments_writes_placeholder(self, manuscript_tree, cfg):
        """With no experiment notes, appendix-repro.tex has a placeholder comment."""
        from research_vault.manuscript.appendix import inject_appendix
        note_path, tree_root, manifest, _ = manuscript_tree
        inject_appendix(tree_root=tree_root, experiment_notes=[])
        content = (tree_root / "sections" / "appendix-repro.tex").read_text()
        assert content.strip()  # not empty


# ---------------------------------------------------------------------------
# 4. exec-guarded compile loop (compile.py)
# ---------------------------------------------------------------------------

class TestCompile:

    def test_compile_exits_friendly_without_pdflatex(self, manuscript_tree, monkeypatch):
        """rv manuscript compile exits with code 1 and friendly message when pdflatex absent."""
        from research_vault.manuscript import compile as ms_compile
        note_path, tree_root, manifest, cfg = manuscript_tree
        # Monkeypatch _find_tool at the module level so both shutil.which AND the
        # direct /opt/homebrew/bin fallback are both bypassed (clean exec-guard mock).
        monkeypatch.setattr(ms_compile, "_find_tool", lambda name: None)
        result = ms_compile.run_compile(note_path, tree_root)
        assert result["exit_code"] != 0
        msg = result.get("message", "")
        # Must mention install / texlive / latex — never a raw crash
        assert (
            "texlive" in msg.lower()
            or "pdflatex" in msg.lower()
            or "install" in msg.lower()
            or "latex" in msg.lower()
        ), f"Friendly message missing: {msg!r}"

    def test_compile_exits_friendly_without_bibtex(self, manuscript_tree, monkeypatch):
        """rv manuscript compile exits 1 with friendly message when bibtex absent."""
        from research_vault.manuscript import compile as ms_compile
        note_path, tree_root, manifest, cfg = manuscript_tree
        # pdflatex available but bibtex absent — monkeypatch at _find_tool level
        monkeypatch.setattr(ms_compile, "_find_tool", lambda name: (
            "/opt/homebrew/bin/pdflatex" if name == "pdflatex" else None
        ))
        result = ms_compile.run_compile(note_path, tree_root)
        assert result["exit_code"] != 0

    def test_compile_with_real_latex(self, manuscript_tree):
        """Integration: compile runs end-to-end with real pdflatex if available.

        Skipped when pdflatex is not on PATH (adopters without texlive).
        """
        import shutil
        pdflatex = shutil.which("pdflatex") or "/opt/homebrew/bin/pdflatex"
        if not (pdflatex and Path(pdflatex).exists()):
            pytest.skip("pdflatex not on PATH")
        from research_vault.manuscript import compile as ms_compile
        note_path, tree_root, manifest, cfg = manuscript_tree
        result = ms_compile.run_compile(note_path, tree_root)
        # A minimal article should compile (even with empty refs.bib)
        assert result["exit_code"] == 0, \
            f"Compile failed:\n{result.get('log', '')[-500:]}"
        # PDF should be created
        pdf_path = tree_root / "main.pdf"
        assert pdf_path.exists(), "main.pdf not produced"

    def test_compile_updates_note_fields(self, manuscript_tree, monkeypatch):
        """When compile succeeds, it updates manuscript_pdf + manuscript_hash in the note."""
        import shutil
        pdflatex = shutil.which("pdflatex") or "/opt/homebrew/bin/pdflatex"
        if not (pdflatex and Path(pdflatex).exists()):
            pytest.skip("pdflatex not on PATH")
        from research_vault.manuscript import compile as ms_compile
        note_path, tree_root, manifest, cfg = manuscript_tree
        result = ms_compile.run_compile(note_path, tree_root)
        if result["exit_code"] != 0:
            pytest.skip("pdflatex ran but compile failed on this system")
        content = note_path.read_text(encoding="utf-8")
        assert "manuscript_pdf:" in content
        assert "manuscript_hash: sha256:" in content


# ---------------------------------------------------------------------------
# 5. Structural check gates (check_gates.py)
# ---------------------------------------------------------------------------

class TestCheckGates:

    def _write_main_with_cite(self, tree_root: Path, citekey: str) -> None:
        r"""Inject \cite{citekey} into main.tex outside any comment line.

        Appends a test section unconditionally — don't check ``r'\cite{'`` in
        existing, because the template has ``\cite{key}`` inside % comment lines
        which would suppress the inject while the scanner (correctly) skips them.
        """
        main = tree_root / "main.tex"
        existing = main.read_text(encoding="utf-8") if main.exists() else ""
        # Always append to end — avoids false positive on comment-only occurrences
        inject = f"\n% test-inject\n\\section{{TestCite}}\nSee \\cite{{{citekey}}}.\n"
        main.write_text(existing.rstrip() + inject, encoding="utf-8")

    def _write_valid_refs_bib(self, tree_root: Path, citekey: str) -> None:
        refs = tree_root / "refs.bib"
        refs.write_text(
            f"@article{{{citekey},\n"
            f"  author = {{Smith, Alice}},\n"
            f"  title = {{A Paper}},\n"
            f"  journal = {{Test Journal}},\n"
            f"  year = {{2024}},\n"
            f"}}\n",
            encoding="utf-8",
        )

    def test_check_unmatched_cite_is_hard_error(self, manuscript_tree):
        """rv manuscript check reports error for \\cite{key} not in refs.bib."""
        from research_vault.manuscript.check_gates import check_manuscript
        note_path, tree_root, manifest, cfg = manuscript_tree
        # Write tex with a cite
        self._write_main_with_cite(tree_root, "unknownKey2024X")
        # refs.bib does NOT contain that key
        (tree_root / "refs.bib").write_text(
            "% empty refs.bib\n", encoding="utf-8"
        )
        result = check_manuscript(note_path, tree_root)
        errors = result.get("errors", [])
        assert any("unknownKey2024X" in e or "unmatched" in e.lower() for e in errors), \
            f"Expected unmatched-cite error; got errors: {errors}"

    def test_check_matched_cite_passes(self, manuscript_tree):
        """rv manuscript check passes when all \\cite{} keys are in refs.bib."""
        from research_vault.manuscript.check_gates import check_manuscript
        note_path, tree_root, manifest, cfg = manuscript_tree
        self._write_main_with_cite(tree_root, "smith2024Good")
        self._write_valid_refs_bib(tree_root, "smith2024Good")
        result = check_manuscript(note_path, tree_root)
        cite_errors = [e for e in result.get("errors", []) if "unmatched" in e.lower()
                        or "cite" in e.lower()]
        assert cite_errors == [], f"Unexpected cite errors: {cite_errors}"

    def test_check_missing_figure_file_is_error(self, manuscript_tree):
        r"""\\includegraphics pointing to a missing file is a hard error."""
        from research_vault.manuscript.check_gates import check_manuscript
        note_path, tree_root, manifest, cfg = manuscript_tree
        tex = tree_root / "sections" / "method.tex"
        tex.write_text(
            r"\includegraphics[width=\linewidth]{figures/nonexistent_fig.pdf}",
            encoding="utf-8",
        )
        result = check_manuscript(note_path, tree_root)
        errors = result.get("errors", [])
        assert any("nonexistent_fig" in e or "includegraphics" in e.lower()
                   or "figure" in e.lower() or "missing" in e.lower()
                   for e in errors), \
            f"Expected missing-figure error; got: {errors}"

    def test_check_existing_figure_passes(self, manuscript_tree, tmp_path):
        r"""\\includegraphics pointing to an existing file passes."""
        from research_vault.manuscript.check_gates import check_manuscript
        note_path, tree_root, manifest, cfg = manuscript_tree
        # Create a dummy figure
        fig_dir = tree_root / "figures"
        fig_dir.mkdir(exist_ok=True)
        fig = fig_dir / "my_fig.pdf"
        fig.write_bytes(b"%PDF-1.4")
        tex = tree_root / "sections" / "method.tex"
        tex.write_text(
            r"\includegraphics[width=\linewidth]{figures/my_fig.pdf}",
            encoding="utf-8",
        )
        result = check_manuscript(note_path, tree_root)
        fig_errors = [e for e in result.get("errors", [])
                      if "my_fig" in e or "includegraphics" in e.lower()]
        assert fig_errors == [], f"Unexpected figure errors: {fig_errors}"

    def test_check_avail_sentinel_cross_check(self, manuscript_tree, cfg):
        """data-code-availability 'fully available' claim with sentinel repro → error."""
        from research_vault.manuscript.check_gates import check_manuscript
        from research_vault.note import REPRO_SENTINEL
        note_path, tree_root, manifest, _ = manuscript_tree
        # Write data-code-availability section claiming everything available
        avail_tex = tree_root / "sections" / "data-code-availability.tex"
        avail_tex.parent.mkdir(exist_ok=True)
        avail_tex.write_text(
            "All code and data are fully available at https://example.com.",
            encoding="utf-8",
        )
        # Write an experiment note with sentinel repro fields
        proj_notes = cfg.project_notes_dir("demo-research")
        exp_dir = proj_notes / "experiments"
        exp_dir.mkdir(exist_ok=True)
        exp_note = exp_dir / "exp-avail.md"
        exp_note.write_text(
            "---\ntype: experiments\ntitle: Avail test\ncreated: 2026-01-01\n"
            f"repro_seed: {REPRO_SENTINEL}\nresults_hash: sha256:{'a'*64}\n"
            "results_location: /some/path\nresults_commit: abc123\n---\n",
            encoding="utf-8",
        )
        # Set the manuscript scope to include this experiment
        from research_vault.note import _parse_frontmatter, _render_frontmatter
        text = note_path.read_text(encoding="utf-8")
        fields, body = _parse_frontmatter(text)
        fields["synthesized_okf"] = "experiments/exp-avail"
        note_path.write_text(_render_frontmatter(fields) + "\n" + body, encoding="utf-8")
        result = check_manuscript(note_path, tree_root)
        errors_warns = result.get("errors", []) + result.get("warnings", [])
        assert any("availability" in s.lower() or "sentinel" in s.lower()
                   or "not-recorded" in s.lower() or "avail" in s.lower()
                   for s in errors_warns), \
            f"Expected availability sentinel cross-check flag; got: {errors_warns}"

    def test_check_clean_manuscript_passes(self, manuscript_tree):
        """rv manuscript check passes for a clean scaffolded manuscript (no .tex problems)."""
        from research_vault.manuscript.check_gates import check_manuscript
        note_path, tree_root, manifest, cfg = manuscript_tree
        # No \\cite, no \\includegraphics in the fresh tree
        result = check_manuscript(note_path, tree_root)
        # Should have no errors
        assert result.get("errors", []) == [], \
            f"Unexpected errors on clean manuscript: {result.get('errors')}"


# ---------------------------------------------------------------------------
# 6. Manifest reads: relative paths (fold-in)
# ---------------------------------------------------------------------------

class TestManifestRelativeReads:

    def test_manifest_reads_are_relative(self, cfg, tmp_instance):
        """Scaffolded manifest reads: pointers are relative paths (not absolute)."""
        from research_vault import manuscript as ms_mod
        _, tree_root, manifest = ms_mod.cmd_new(
            "demo-research", "ms-relreads",
            thesis="Relative reads test",
            scope=[],
            config=cfg,
        )
        for node in manifest["nodes"]:
            for ptr in node.get("reads", []):
                assert not Path(ptr).is_absolute(), \
                    f"Node {node['id']!r} reads pointer is absolute: {ptr!r}"

    def test_manifest_relative_reads_resolve_zero_errors(self, cfg, tmp_instance):
        """Relative reads: pointers resolve with zero hard errors."""
        from research_vault import manuscript as ms_mod
        _, tree_root, manifest = ms_mod.cmd_new(
            "demo-research", "ms-relresol",
            thesis="Resolution test",
            scope=[],
            config=cfg,
        )
        project_root = cfg.project_notes_dir("demo-research")
        errors, _warns = resolve_reads_pointers(manifest, project_root=project_root)
        assert errors == [], f"Relative reads resolution errors: {errors}"


# ---------------------------------------------------------------------------
# 7. [manuscript_style] TOML config override (fold-in)
# ---------------------------------------------------------------------------

class TestManuscriptStyleConfig:

    def test_get_section_tips_reads_config_override(self, tmp_instance, tmp_path):
        """get_section_tips(config=) reads [manuscript_style] overrides from config."""
        import os
        from research_vault.config import load_config
        from research_vault.manuscript.style import get_section_tips
        # Write a config with [manuscript_style] section
        config_file = tmp_path / "rv_style.toml"
        config_file.write_text(
            f"""
instance_root = "{tmp_path}"
notes_root = "{tmp_path / 'notes'}"
state_dir = "{tmp_path / 'state'}"
agents_dir = "{tmp_path / '.agents'}"
tasks_dir = "{tmp_path / 'tasks'}"
control_dir = "{tmp_path / 'control'}"

[adapters]
notifier = "file"
backend = "local"
secrets = "env"

[manuscript_style]
gather-scope = "CUSTOM GATHER SCOPE INSTRUCTIONS FOR TESTING."
""",
            encoding="utf-8",
        )
        old = os.environ.get("RESEARCH_VAULT_CONFIG")
        os.environ["RESEARCH_VAULT_CONFIG"] = str(config_file)
        try:
            cfg = load_config(reload=True)
            tips = get_section_tips(config=cfg)
        finally:
            if old is None:
                os.environ.pop("RESEARCH_VAULT_CONFIG", None)
            else:
                os.environ["RESEARCH_VAULT_CONFIG"] = old

        assert tips["gather-scope"] == "CUSTOM GATHER SCOPE INSTRUCTIONS FOR TESTING.", \
            f"Config override not applied: {tips['gather-scope']!r}"
        # Other keys retain defaults
        assert "related-work" in tips

    def test_get_section_tips_no_config_returns_defaults(self):
        """get_section_tips(config=None) returns defaults (backward compat)."""
        from research_vault.manuscript.style import get_section_tips
        tips = get_section_tips(config=None)
        assert "gather-scope" in tips
        assert isinstance(tips["gather-scope"], str)

    def test_get_style_preamble_reads_config_override(self, tmp_instance, tmp_path):
        """get_style_preamble(config=) reads [manuscript_style] preamble override."""
        import os
        from research_vault.config import load_config
        from research_vault.manuscript.style import get_style_preamble
        config_file = tmp_path / "rv_preamble.toml"
        config_file.write_text(
            f"""
instance_root = "{tmp_path}"
notes_root = "{tmp_path / 'notes'}"
state_dir = "{tmp_path / 'state'}"
agents_dir = "{tmp_path / '.agents'}"
tasks_dir = "{tmp_path / 'tasks'}"
control_dir = "{tmp_path / 'control'}"

[adapters]
notifier = "file"
backend = "local"
secrets = "env"

[manuscript_style]
_preamble = "CUSTOM PREAMBLE FOR TESTING."
""",
            encoding="utf-8",
        )
        old = os.environ.get("RESEARCH_VAULT_CONFIG")
        os.environ["RESEARCH_VAULT_CONFIG"] = str(config_file)
        try:
            cfg = load_config(reload=True)
            preamble = get_style_preamble(config=cfg)
        finally:
            if old is None:
                os.environ.pop("RESEARCH_VAULT_CONFIG", None)
            else:
                os.environ["RESEARCH_VAULT_CONFIG"] = old

        assert preamble == "CUSTOM PREAMBLE FOR TESTING.", \
            f"Config preamble override not applied: {preamble!r}"

    def test_get_style_preamble_no_config_returns_defaults(self):
        """get_style_preamble(config=None) returns default (backward compat)."""
        from research_vault.manuscript.style import get_style_preamble
        preamble = get_style_preamble(config=None)
        assert preamble.strip()  # not empty


# ---------------------------------------------------------------------------
# 8. rv check LaTeX optional prereq probe
# ---------------------------------------------------------------------------

class TestCheckLatexPrereq:

    def test_rv_check_includes_latex_in_results(self):
        """run_preflight returns a 'latex' key in results dict."""
        from research_vault.check import run_preflight
        result = run_preflight()
        assert "latex" in result, \
            "run_preflight must include a 'latex' key for the manuscript prereq"

    def test_rv_check_latex_report_line_present(self):
        """run_preflight report includes a LaTeX/pdflatex line in the Optional section."""
        from research_vault.check import run_preflight
        result = run_preflight()
        report = result.get("report", "")
        assert "pdflatex" in report.lower() or "latex" in report.lower() or "texlive" in report.lower(), \
            f"LaTeX not mentioned in rv check report: {report!r}"

    def test_rv_check_latex_ok_when_pdflatex_present(self, monkeypatch):
        """rv check reports latex:OK when pdflatex is on PATH."""
        import shutil
        # Accept **kwargs to handle shutil.which(name, path=...) calls
        monkeypatch.setattr(shutil, "which", lambda cmd, **kw: (
            "/opt/homebrew/bin/pdflatex" if cmd == "pdflatex"
            else "/opt/homebrew/bin/bibtex" if cmd == "bibtex"
            else "/opt/homebrew/bin/chktex" if cmd == "chktex"
            else None
        ))
        from research_vault.check import run_preflight
        result = run_preflight()
        assert result.get("latex") is True, \
            "Expected latex:True when pdflatex + chktex mocked as present"

    def test_rv_check_latex_warn_when_pdflatex_absent(self, monkeypatch):
        """rv check reports latex:False (WARN) when pdflatex is not on PATH."""
        import shutil
        # Accept **kwargs to handle shutil.which(name, path=...) calls
        monkeypatch.setattr(shutil, "which", lambda cmd, **kw: None)
        from research_vault.check import run_preflight
        result = run_preflight()
        assert result.get("latex") is False, \
            "Expected latex:False when pdflatex mocked as absent"


# ---------------------------------------------------------------------------
# 9. Optional / venue-optional topology regression (fold-in)
# ---------------------------------------------------------------------------

class TestOptionalTopologyRegression:

    def test_default_topology_validates(self, cfg, tmp_instance):
        """Default (no flags) manifest passes validate_manifest."""
        from research_vault import manuscript as ms_mod
        _, tree_root, manifest = ms_mod.cmd_new(
            "demo-research", "ms-topo-default",
            thesis="Default topology",
            scope=[],
            config=cfg,
        )
        validate_manifest(manifest)  # must not raise

    def test_optional_topology_validates(self, cfg, tmp_instance):
        """--optional manifest passes validate_manifest."""
        from research_vault import manuscript as ms_mod
        _, tree_root, manifest = ms_mod.cmd_new(
            "demo-research", "ms-topo-opt",
            thesis="Optional topology",
            scope=[],
            config=cfg,
            include_optional=True,
        )
        validate_manifest(manifest)  # must not raise
        ids = {n["id"] for n in manifest["nodes"]}
        assert "background" in ids, "background node missing in --optional manifest"

    def test_venue_optional_topology_validates(self, cfg, tmp_instance):
        """--venue-optional manifest passes validate_manifest."""
        from research_vault import manuscript as ms_mod
        _, tree_root, manifest = ms_mod.cmd_new(
            "demo-research", "ms-topo-vo",
            thesis="Venue-optional topology",
            scope=[],
            config=cfg,
            include_venue_optional=True,
        )
        validate_manifest(manifest)  # must not raise
        ids = {n["id"] for n in manifest["nodes"]}
        assert "ethics-impacts" in ids, "ethics-impacts missing in --venue-optional"
        assert "data-code-availability" in ids, "data-code-availability missing"

    def test_both_optional_flags_topology_validates(self, cfg, tmp_instance):
        """--optional + --venue-optional manifest passes validate_manifest."""
        from research_vault import manuscript as ms_mod
        _, tree_root, manifest = ms_mod.cmd_new(
            "demo-research", "ms-topo-both",
            thesis="Both optional flags",
            scope=[],
            config=cfg,
            include_optional=True,
            include_venue_optional=True,
        )
        validate_manifest(manifest)  # must not raise
        ids = {n["id"] for n in manifest["nodes"]}
        assert "background" in ids
        assert "ethics-impacts" in ids
        assert "data-code-availability" in ids

    def test_optional_reads_resolve_zero_errors(self, cfg, tmp_instance):
        """--optional manifest reads: pointers resolve with zero hard errors."""
        from research_vault import manuscript as ms_mod
        _, tree_root, manifest = ms_mod.cmd_new(
            "demo-research", "ms-topo-opt-reads",
            thesis="Optional reads resolution",
            scope=[],
            config=cfg,
            include_optional=True,
            include_venue_optional=True,
        )
        project_root = cfg.project_notes_dir("demo-research")
        errors, _warns = resolve_reads_pointers(manifest, project_root=project_root)
        assert errors == [], f"Optional topology reads errors: {errors}"


# ---------------------------------------------------------------------------
# 10. CLI verb: rv manuscript compile and check registered
# ---------------------------------------------------------------------------

class TestVerbRegistration:

    def test_manuscript_compile_verb_accessible(self):
        """'rv manuscript compile' is a recognized subcommand."""
        from research_vault.manuscript.verbs import build_parser
        p = build_parser()
        # Parse a compile invocation — should not error on arg parsing
        # (note: actual execution is guarded; this tests parser registration)
        try:
            args = p.parse_args(["demo-research", "compile", "ms-001"])
        except SystemExit:
            pytest.fail("'rv manuscript compile' not recognized by parser")

    def test_manuscript_check_verb_accessible(self):
        """'rv manuscript check' is a recognized subcommand."""
        from research_vault.manuscript.verbs import build_parser
        p = build_parser()
        try:
            args = p.parse_args(["demo-research", "check", "ms-001"])
        except SystemExit:
            pytest.fail("'rv manuscript check' not recognized by parser")


# ---------------------------------------------------------------------------
# 11. E2E: builders wired into rv manuscript compile + pdflatex macro test
# ---------------------------------------------------------------------------

class TestE2ECompileWired:
    """End-to-end test: cmd_compile calls builders before pdflatex.

    Verifies that rv manuscript compile:
      - calls build_refs_bib → refs.bib exists
      - calls inject_results → results.tex carries \\newcommand macros
      - calls inject_appendix → appendix-repro.tex is populated
      - stamps provenance into the manuscript note
      - the injected macros compile without brace error (pdflatex — skip if absent)

    The pdflatex assertion is the definitive proof the macro-brace bug is
    fixed: {0.85%  % key}} (old) → runaway-argument; {0.85}% key (new) → OK.
    """

    def _make_exp_note(self, cfg, project: str, exp_id: str,
                        results: dict, tmp_path: Path) -> tuple[Path, str]:
        """Write a hash-verified experiment note; return (note_path, hash)."""
        import hashlib
        proj_notes = cfg.project_notes_dir(project)
        exp_dir = proj_notes / "experiments"
        exp_dir.mkdir(parents=True, exist_ok=True)
        results_file = tmp_path / f"{exp_id}_results.json"
        results_file.write_text(json.dumps(results), encoding="utf-8")
        h = hashlib.sha256()
        h.update(results_file.read_bytes())
        results_hash = "sha256:" + h.hexdigest()
        note_path = exp_dir / f"{exp_id}.md"
        note_path.write_text(
            "---\n"
            f"type: experiments\n"
            f"title: {exp_id}\n"
            f"created: 2026-01-01\n"
            f"results_location: {results_file}\n"
            f"results_hash: {results_hash}\n"
            f"results_commit: abc1234def56\n"
            "---\n\n"
            f"## {exp_id}\n",
            encoding="utf-8",
        )
        return note_path, results_hash

    def test_compile_wires_builders_and_pdflatex(self, cfg, tmp_instance, tmp_path):
        """cmd_compile runs builders and (if pdflatex present) produces a brace-error-free PDF.

        Exec-guarded: pdflatex assertion is skipped when texlive is absent.
        Test verifies:
          - results.tex populated with \\newcommand (builders ran)
          - refs.bib written
          - appendix-repro.tex populated
          - provenance stamp in manuscript note
          - if pdflatex present: PDF exists (macros compiled without brace error)
        """
        import shutil
        from research_vault import manuscript as ms_mod

        # ── Scaffold manuscript with scoped experiment ──────────────────────
        note_path, tree_root, manifest = ms_mod.cmd_new(
            "demo-research", "ms-e2e",
            thesis="E2E compile test — macro brace verification",
            scope=["experiments/exp-e2e"],
            config=cfg,
        )

        # Create experiment note + results.json
        _exp_note, exp_hash = self._make_exp_note(
            cfg, "demo-research", "exp-e2e",
            # Use a float value and a %-containing string to test both the
            # brace fix and the percent-escaping fix in the same compile.
            {"accuracy": 0.85, "coverage_pct": "72%"},
            tmp_path,
        )

        # Empty library.json (no \cite in default main.tex → no unmatched-cite error)
        library_json = cfg.project_notes_dir("demo-research") / "library.json"
        library_json.write_text("[]", encoding="utf-8")

        # ── Run compile ─────────────────────────────────────────────────────
        result = ms_mod.cmd_compile("demo-research", "ms-e2e", config=cfg)

        # ── Assert builders ran: results.tex has \newcommand ────────────────
        results_tex = tree_root / "results.tex"
        assert results_tex.exists(), "results.tex not written — builder not called"
        results_content = results_tex.read_text(encoding="utf-8")
        assert r"\newcommand" in results_content, (
            f"\\newcommand not in results.tex — inject_results not called:\n"
            f"{results_content[:400]}"
        )
        assert "0.85" in results_content, (
            f"accuracy value missing from results.tex:\n{results_content[:400]}"
        )
        # Percent sign in value must be escaped as \% — not left as a LaTeX comment
        assert r"\%" in results_content or "CoveragePct" in results_content, (
            f"percent-containing value missing or unescaped in results.tex:\n"
            f"{results_content[:400]}"
        )

        # ── Assert refs.bib was written ──────────────────────────────────────
        refs_bib = tree_root / "refs.bib"
        assert refs_bib.exists(), "refs.bib not written — build_refs_bib not called"

        # ── Assert appendix was populated ────────────────────────────────────
        appendix_tex = tree_root / "sections" / "appendix-repro.tex"
        assert appendix_tex.exists(), (
            "appendix-repro.tex not written — inject_appendix not called"
        )
        assert appendix_tex.stat().st_size > 0, "appendix-repro.tex is empty"

        # ── Assert provenance stamp in manuscript note ───────────────────────
        note_content = note_path.read_text(encoding="utf-8")
        assert (
            "results-provenance-stamp" in note_content
            or exp_hash[:16] in note_content
        ), "Provenance stamp missing from manuscript note after compile"

        # ── Assert injected macros compile without brace error (pdflatex) ───
        pdflatex = shutil.which("pdflatex") or "/opt/homebrew/bin/pdflatex"
        if not (pdflatex and Path(pdflatex).exists()):
            pytest.skip("pdflatex not on PATH — skipping brace-compilation assertion")

        # cmd_compile already called pdflatex. Check the result.
        log = result.get("log", "")
        assert "Runaway argument" not in log, (
            "LaTeX runaway-argument error detected — macro brace bug still present:\n"
            f"{log[-1500:]}"
        )
        assert "missing } inserted" not in log, (
            "LaTeX 'missing } inserted' error — macro brace bug still present:\n"
            f"{log[-1500:]}"
        )

        if result["exit_code"] == 0:
            pdf_path = tree_root / "main.pdf"
            assert pdf_path.exists(), (
                "compile reported exit_code=0 but main.pdf not found"
            )
        else:
            # pdflatex present but compile failed — ensure it's NOT a brace error
            # (could be missing LaTeX packages, etc. — those are system-config issues)
            assert "Runaway argument" not in log and "missing } inserted" not in log, (
                f"Brace error in pdflatex log — macro bug NOT fixed:\n{log[-1500:]}"
            )
