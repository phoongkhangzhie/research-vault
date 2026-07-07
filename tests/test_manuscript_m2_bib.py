"""test_manuscript_m2_bib.py — PR-M2 acceptance tests: the hermetic .bib build +
citation-resolve gate (design §6, D-SV-A).

Coverage:
  1. extract_cited_keys — \\cite{}/\\citep{}/\\citet{} extraction from .tex,
     multi-cite, commented-out examples ignored.
  2. _load_literature_bib_index — citekey -> frontmatter-fields index, reusing
     the F17 citekey:-field-with-filename-stem-fallback convention.
  3. _fields_to_bib_entry — deterministic BibTeX entry from note frontmatter
     (title always; doi/arxiv_id/authors/year/venue only when present; never
     fabricated).
  4. build_refs_bib — end-to-end: writes refs.bib from literature/ frontmatter,
     byte-deterministic, only cited keys included (closed bibliography).
       4a. a \\cite{} with a real literature/ note -> written, no errors.
       4b. a \\cite{} with NO backing note -> flagged (non-empty errors),
           .bib still written (best-effort) but the key is absent.
       4c. re-running the build with unchanged inputs -> byte-identical output
           (determinism / no order-flakiness).
  5. check_hermetic_bib — the gate wrapper.
       5a. missing-cite -> gate reports not-ok + the missing citekey.
       5b. self-contained build -> gate reports ok, matches build_refs_bib.
  6. Hermetic-ness proof — no network reachable from the build/gate path:
       6a. static AST scan: bib.py imports nothing from `cite.py`, `urllib`,
           `http`, `requests`, `socket`.
       6b. behavioural: monkeypatch socket.socket to raise if called; build
           succeeds untouched (proves no network call fires during a build).
"""
from __future__ import annotations

import ast
import socket
from pathlib import Path

import pytest

from research_vault.manuscript import bib


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write_lit_note(
    literature_dir: Path,
    stem: str,
    *,
    citekey: str | None = None,
    title: str = "A Paper",
    doi: str = "",
    arxiv_id: str = "",
    authors: str = "",
    year: str = "",
    venue: str = "",
) -> Path:
    literature_dir.mkdir(parents=True, exist_ok=True)
    fields = [f"type: literature", f"title: {title}", "created: 2026-07-07"]
    if citekey is not None:
        fields.append(f"citekey: {citekey}")
    if doi:
        fields.append(f"doi: {doi}")
    if arxiv_id:
        fields.append(f"arxiv_id: {arxiv_id}")
    if authors:
        fields.append(f"authors: {authors}")
    if year:
        fields.append(f"year: {year}")
    if venue:
        fields.append(f"venue: {venue}")
    text = "---\n" + "\n".join(fields) + "\n---\n\nBody.\n"
    path = literature_dir / f"{stem}.md"
    path.write_text(text, encoding="utf-8")
    return path


def _write_tex(tree_root: Path, name: str, content: str) -> Path:
    path = tree_root / name
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. extract_cited_keys
# ---------------------------------------------------------------------------

class TestExtractCitedKeys:
    def test_simple_cite(self, tmp_path: Path) -> None:
        tex = _write_tex(tmp_path, "a.tex", r"Some text \cite{smith2023}.")
        assert bib.extract_cited_keys([tex]) == {"smith2023"}

    def test_natbib_variants(self, tmp_path: Path) -> None:
        tex = _write_tex(
            tmp_path, "a.tex",
            r"\citep{a2020} and \citet{b2021} and \citealt{c2022}.",
        )
        assert bib.extract_cited_keys([tex]) == {"a2020", "b2021", "c2022"}

    def test_multi_cite(self, tmp_path: Path) -> None:
        tex = _write_tex(tmp_path, "a.tex", r"\cite{a2020,b2021}")
        assert bib.extract_cited_keys([tex]) == {"a2020", "b2021"}

    def test_optional_note_arg(self, tmp_path: Path) -> None:
        tex = _write_tex(tmp_path, "a.tex", r"\cite[p. 12]{smith2023}")
        assert bib.extract_cited_keys([tex]) == {"smith2023"}

    def test_commented_out_cite_ignored(self, tmp_path: Path) -> None:
        tex = _write_tex(
            tmp_path, "a.tex",
            "Real \\cite{real2023}.\n% every \\cite{fake2023} must resolve\n",
        )
        assert bib.extract_cited_keys([tex]) == {"real2023"}

    def test_missing_file_skipped(self, tmp_path: Path) -> None:
        assert bib.extract_cited_keys([tmp_path / "nope.tex"]) == set()


# ---------------------------------------------------------------------------
# 2. _load_literature_bib_index
# ---------------------------------------------------------------------------

class TestLoadLiteratureBibIndex:
    def test_citekey_field_preferred_over_stem(self, tmp_path: Path) -> None:
        lit_dir = tmp_path / "literature"
        _write_lit_note(lit_dir, "zheng2023-pride-mc-selectors", citekey="zheng2023-pride")
        index = bib._load_literature_bib_index(lit_dir)
        assert "zheng2023-pride" in index
        assert index["zheng2023-pride"]["title"] == "A Paper"

    def test_falls_back_to_stem_when_no_citekey_field(self, tmp_path: Path) -> None:
        lit_dir = tmp_path / "literature"
        _write_lit_note(lit_dir, "smith2023", citekey=None)
        index = bib._load_literature_bib_index(lit_dir)
        assert "smith2023" in index

    def test_absent_dir_returns_empty(self, tmp_path: Path) -> None:
        assert bib._load_literature_bib_index(tmp_path / "nope") == {}


# ---------------------------------------------------------------------------
# 3. _fields_to_bib_entry
# ---------------------------------------------------------------------------

class TestFieldsToBibEntry:
    def test_minimal_title_only(self) -> None:
        entry = bib._fields_to_bib_entry("smith2023", {"title": "A Paper"})
        assert entry.startswith("@misc{smith2023,")
        assert "title = {A Paper}" in entry
        # Never fabricate a year/author/doi that wasn't present.
        assert "author" not in entry
        assert "year" not in entry

    def test_full_fields(self) -> None:
        entry = bib._fields_to_bib_entry(
            "smith2023",
            {
                "title": "A Paper",
                "authors": "Smith, John",
                "year": "2023",
                "venue": "NeurIPS",
                "doi": "10.1234/example",
                "arxiv_id": "2005.14165",
            },
        )
        assert "title = {A Paper}" in entry
        assert "author = {Smith, John}" in entry
        assert "year = {2023}" in entry
        assert "journal = {NeurIPS}" in entry
        assert "doi = {10.1234/example}" in entry
        assert "@article{smith2023," in entry  # venue present -> article

    def test_arxiv_only_no_venue(self) -> None:
        entry = bib._fields_to_bib_entry(
            "smith2023", {"title": "A Paper", "arxiv_id": "2005.14165"},
        )
        assert entry.startswith("@misc{smith2023,")
        assert "2005.14165" in entry


# ---------------------------------------------------------------------------
# 4. build_refs_bib
# ---------------------------------------------------------------------------

class TestBuildRefsBib:
    def _setup(self, tmp_path: Path):
        project_notes_dir = tmp_path / "notes"
        tree_root = tmp_path / "manuscripts" / "survey"
        tree_root.mkdir(parents=True)
        lit_dir = project_notes_dir / "literature"
        _write_lit_note(lit_dir, "smith2023", citekey="smith2023", title="A Paper")
        return project_notes_dir, tree_root

    def test_cited_and_backed_key_written(self, tmp_path: Path) -> None:
        project_notes_dir, tree_root = self._setup(tmp_path)
        _write_tex(tree_root, "main.tex", r"\cite{smith2023}")
        errors, bib_path = bib.build_refs_bib(project_notes_dir, tree_root)
        assert errors == []
        text = bib_path.read_text(encoding="utf-8")
        assert "@misc{smith2023," in text
        assert "A Paper" in text

    def test_missing_note_flagged(self, tmp_path: Path) -> None:
        project_notes_dir, tree_root = self._setup(tmp_path)
        _write_tex(tree_root, "main.tex", r"\cite{ghost2024}")
        errors, bib_path = bib.build_refs_bib(project_notes_dir, tree_root)
        assert any("ghost2024" in e for e in errors)
        text = bib_path.read_text(encoding="utf-8")
        assert "ghost2024" not in text  # never fabricate an entry for it

    def test_mixed_backed_and_missing(self, tmp_path: Path) -> None:
        project_notes_dir, tree_root = self._setup(tmp_path)
        _write_tex(tree_root, "main.tex", r"\cite{smith2023} \cite{ghost2024}")
        errors, bib_path = bib.build_refs_bib(project_notes_dir, tree_root)
        assert len(errors) == 1
        assert "ghost2024" in errors[0]
        text = bib_path.read_text(encoding="utf-8")
        assert "smith2023" in text
        assert "ghost2024" not in text

    def test_deterministic_rebuild(self, tmp_path: Path) -> None:
        project_notes_dir, tree_root = self._setup(tmp_path)
        _write_tex(tree_root, "main.tex", r"\cite{smith2023}")
        _errors1, bib_path1 = bib.build_refs_bib(project_notes_dir, tree_root)
        text1 = bib_path1.read_text(encoding="utf-8")
        _errors2, bib_path2 = bib.build_refs_bib(project_notes_dir, tree_root)
        text2 = bib_path2.read_text(encoding="utf-8")
        assert text1 == text2

    def test_no_cites_writes_empty_bib_no_errors(self, tmp_path: Path) -> None:
        project_notes_dir, tree_root = self._setup(tmp_path)
        _write_tex(tree_root, "main.tex", "No citations here.")
        errors, bib_path = bib.build_refs_bib(project_notes_dir, tree_root)
        assert errors == []
        assert bib_path.exists()

    def test_sorted_by_citekey(self, tmp_path: Path) -> None:
        project_notes_dir, tree_root = self._setup(tmp_path)
        lit_dir = project_notes_dir / "literature"
        _write_lit_note(lit_dir, "b2020", citekey="b2020", title="B")
        _write_lit_note(lit_dir, "a2019", citekey="a2019", title="A")
        _write_tex(tree_root, "main.tex", r"\cite{smith2023,b2020,a2019}")
        _errors, bib_path = bib.build_refs_bib(project_notes_dir, tree_root)
        text = bib_path.read_text(encoding="utf-8")
        assert text.index("a2019") < text.index("b2020") < text.index("smith2023")


# ---------------------------------------------------------------------------
# 5. check_hermetic_bib gate
# ---------------------------------------------------------------------------

class TestCheckHermeticBib:
    def _setup(self, tmp_path: Path):
        project_notes_dir = tmp_path / "notes"
        tree_root = tmp_path / "manuscripts" / "survey"
        tree_root.mkdir(parents=True)
        lit_dir = project_notes_dir / "literature"
        _write_lit_note(lit_dir, "smith2023", citekey="smith2023", title="A Paper")
        return project_notes_dir, tree_root

    def test_missing_cite_not_ok(self, tmp_path: Path) -> None:
        project_notes_dir, tree_root = self._setup(tmp_path)
        _write_tex(tree_root, "main.tex", r"\cite{ghost2024}")
        result = bib.check_hermetic_bib(project_notes_dir, tree_root)
        assert result["ok"] is False
        assert any("ghost2024" in e for e in result["errors"])

    def test_self_contained_ok(self, tmp_path: Path) -> None:
        project_notes_dir, tree_root = self._setup(tmp_path)
        _write_tex(tree_root, "main.tex", r"\cite{smith2023}")
        result = bib.check_hermetic_bib(project_notes_dir, tree_root)
        assert result["ok"] is True
        assert result["errors"] == []
        assert result["bib_path"].exists()

    def test_every_bib_entry_has_provenance_id(self, tmp_path: Path) -> None:
        # D-SV-A part 2: self-contained means every emitted entry is backed —
        # confirm no entry appears in refs.bib without a resolvable citekey
        # in the literature index (the gate's own invariant, checked structurally).
        project_notes_dir, tree_root = self._setup(tmp_path)
        _write_tex(tree_root, "main.tex", r"\cite{smith2023} \cite{ghost2024}")
        result = bib.check_hermetic_bib(project_notes_dir, tree_root)
        lit_index = bib._load_literature_bib_index(project_notes_dir / "literature")
        text = result["bib_path"].read_text(encoding="utf-8")
        for key in bib._BIB_ENTRY_KEY_RE.findall(text):
            assert key in lit_index


# ---------------------------------------------------------------------------
# 6. Hermetic-ness proof — no network reachable
# ---------------------------------------------------------------------------

class TestHermeticNoNetwork:
    def test_static_no_network_or_zotero_imports(self) -> None:
        """AST-scan bib.py's import statements: no cite.py, no network libs."""
        import research_vault.manuscript.bib as bib_mod

        src = Path(bib_mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        forbidden = {"urllib", "http", "requests", "socket", "httpx"}
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
                    # catch `from research_vault.cite import x`
                    if node.module.endswith(".cite") or node.module == "cite":
                        pytest.fail("bib.py must not import cite.py (the Zotero bridge)")
        assert not (imported & forbidden), f"bib.py imports network module(s): {imported & forbidden}"

    def test_behavioural_no_socket_call_during_build(self, tmp_path: Path, monkeypatch) -> None:
        """A build with a real cited note must succeed even if socket() would raise —
        proving no network call is reachable from the build path."""
        project_notes_dir = tmp_path / "notes"
        tree_root = tmp_path / "manuscripts" / "survey"
        tree_root.mkdir(parents=True)
        lit_dir = project_notes_dir / "literature"
        _write_lit_note(lit_dir, "smith2023", citekey="smith2023", title="A Paper")
        _write_tex(tree_root, "main.tex", r"\cite{smith2023}")

        def _blocked(*_a, **_kw):
            raise AssertionError("network call attempted during hermetic .bib build")

        monkeypatch.setattr(socket, "socket", _blocked)
        errors, bib_path = bib.build_refs_bib(project_notes_dir, tree_root)
        assert errors == []
        assert bib_path.exists()
