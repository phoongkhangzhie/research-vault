"""test_manuscript_pr_d_numbered_bib.py — PR-D acceptance tests: mechanical
``[[citekey]] -> [N]`` conversion + hermetic numbered ``## Sources`` +
``references.bib`` (design D-4, manuscript render layer).

The gold shape: ``[N] Authors (Year). *Title*. Venue. doi:X / arXiv:Y`` in a
numbered ``## Sources`` list, built hermetically from ``literature/``
frontmatter (never fabricated) — matching what ``build_references_md``/
``check_citation_resolve`` (PR-M2, unchanged by this PR) already guarantee for
the plain markdown ledger.

Coverage:
  1. extract_cited_keys_ordered — first-appearance dedup order (D-4b/D-4c).
  2. build_citation_numbering — deterministic N assignment from ordered keys
     + the matched (lit_index-backed) subset.
  3. convert_wikilinks_to_numbered / find_residual_wikilinks — mechanical
     substitution + residue detection (D-4d).
  4. _fields_to_sources_entry / build_sources_section — the gold-shape line
     render, never fabricating an absent field.
  5. _fields_to_bibtex_entry / build_references_bib — valid BibTeX, closed
     bibliography (cited + resolved keys only).
  6. render_numbered_manuscript — the full D-4 orchestrator:
       6a. end-to-end deterministic [N] + Sources + .bib.
       6b. D-4e: a blank/CITEKEY-UNRESOLVED citekey token is never numbered
           or emitted — BLOCK naming the offending claim.
       6c. D-4d: any residual [[citekey]] left in the reader body after
           conversion is a hard BLOCK.
       6d. determinism — re-running on the same inputs produces identical
           numbering + rendered text (D-4c).
  7. check_citation_resolve (PR-M2, existing) stays green/untouched.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from research_vault.manuscript import bib


# ---------------------------------------------------------------------------
# Fixtures (mirrors test_manuscript_m2_bib.py's helpers)
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


def _write_md(tree_root: Path, name: str, content: str) -> Path:
    path = tree_root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. extract_cited_keys_ordered
# ---------------------------------------------------------------------------

class TestExtractCitedKeysOrdered:
    def test_first_appearance_order(self, tmp_path: Path) -> None:
        md = _write_md(
            tmp_path, "report.md",
            "Jones [[jones2022]] built on Smith [[smith2023]] and Jones again [[jones2022]].",
        )
        assert bib.extract_cited_keys_ordered([md]) == ["jones2022", "smith2023"]

    def test_order_across_files(self, tmp_path: Path) -> None:
        one = _write_md(tmp_path, "sections/a.md", "First [[b2020]].")
        two = _write_md(tmp_path, "sections/b.md", "Then [[a2019]] and [[b2020]] again.")
        assert bib.extract_cited_keys_ordered([one, two]) == ["b2020", "a2019"]

    def test_missing_file_skipped(self, tmp_path: Path) -> None:
        assert bib.extract_cited_keys_ordered([tmp_path / "nope.md"]) == []


# ---------------------------------------------------------------------------
# 2. build_citation_numbering
# ---------------------------------------------------------------------------

class TestBuildCitationNumbering:
    def test_numbers_only_matched_keys_in_order(self) -> None:
        ordered = ["b2020", "ghost2024", "a2019"]
        matched = {"b2020": {}, "a2019": {}}
        numbering = bib.build_citation_numbering(ordered, matched)
        assert numbering == {"b2020": 1, "a2019": 2}
        assert "ghost2024" not in numbering

    def test_empty_ordered_keys(self) -> None:
        assert bib.build_citation_numbering([], {}) == {}


# ---------------------------------------------------------------------------
# 3. convert_wikilinks_to_numbered / find_residual_wikilinks
# ---------------------------------------------------------------------------

class TestConvertAndResidue:
    def test_converts_known_keys(self) -> None:
        text = "Smith [[smith2023]] and Jones [[jones2022]]."
        numbering = {"smith2023": 1, "jones2022": 2}
        converted = bib.convert_wikilinks_to_numbered(text, numbering)
        assert converted == "Smith [1] and Jones [2]."
        assert bib.find_residual_wikilinks(converted) == []

    def test_unknown_key_left_as_residue(self) -> None:
        text = "Ghost work [[ghost2024]] claims this."
        converted = bib.convert_wikilinks_to_numbered(text, {})
        assert "[[ghost2024]]" in converted
        assert bib.find_residual_wikilinks(converted) == ["ghost2024"]

    def test_dedup_same_key_same_number_everywhere(self) -> None:
        text = "First [[a2020]], later again [[a2020]]."
        converted = bib.convert_wikilinks_to_numbered(text, {"a2020": 1})
        assert converted == "First [1], later again [1]."


# ---------------------------------------------------------------------------
# 4. sources-entry / section render (the gold shape)
# ---------------------------------------------------------------------------

class TestSourcesRender:
    def test_full_fields_gold_shape(self) -> None:
        entry = bib._fields_to_sources_entry(
            1,
            {
                "title": "A Paper",
                "authors": "Smith, John",
                "year": "2023",
                "venue": "NeurIPS",
                "doi": "10.1234/example",
                "arxiv_id": "2005.14165",
            },
        )
        assert entry.startswith("[1] Smith, John (2023). *A Paper*.")
        assert "NeurIPS" in entry
        assert "doi:10.1234/example" in entry
        assert "arXiv:2005.14165" in entry

    def test_title_only_never_fabricates(self) -> None:
        entry = bib._fields_to_sources_entry(2, {"title": "A Paper"})
        assert entry.startswith("[2] *A Paper*.")
        assert "doi:" not in entry
        assert "arXiv:" not in entry
        assert "(" not in entry  # no fabricated year parens

    def test_build_sources_section_numbered_in_order(self) -> None:
        numbering = {"b2020": 2, "a2019": 1}
        matched = {
            "b2020": {"title": "B Paper"},
            "a2019": {"title": "A Paper"},
        }
        section = bib.build_sources_section(numbering, matched)
        assert section.startswith("## Sources")
        assert section.index("[1] *A Paper*") < section.index("[2] *B Paper*")


# ---------------------------------------------------------------------------
# 5. BibTeX render
# ---------------------------------------------------------------------------

_BIBTEX_ENTRY_RE = re.compile(
    r"@(\w+)\{([^,]+),\s*((?:[a-zA-Z]+\s*=\s*\{[^{}]*\},?\s*)*)\}",
    re.MULTILINE,
)


def _parse_bibtex(text: str) -> list[tuple[str, str, dict[str, str]]]:
    """Minimal stdlib BibTeX structural parser (this repo is stdlib-only —
    no bibtexparser dependency) used ONLY to prove references.bib parses as
    valid, well-formed BibTeX (balanced braces, `@type{key, field = {v}, ...}`)."""
    entries = []
    for m in _BIBTEX_ENTRY_RE.finditer(text):
        entry_type, key, fields_blob = m.group(1), m.group(2).strip(), m.group(3)
        fields = dict(re.findall(r"([a-zA-Z]+)\s*=\s*\{([^{}]*)\}", fields_blob))
        entries.append((entry_type, key, fields))
    return entries


class TestBibtexEntry:
    def test_full_fields(self) -> None:
        entry = bib._fields_to_bibtex_entry(
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
        parsed = _parse_bibtex(entry)
        assert len(parsed) == 1
        entry_type, key, fields = parsed[0]
        assert entry_type == "article"
        assert key == "smith2023"
        assert fields["title"] == "A Paper"
        assert fields["author"] == "Smith, John"
        assert fields["year"] == "2023"
        assert fields["journal"] == "NeurIPS"
        assert fields["doi"] == "10.1234/example"
        assert fields["eprint"] == "2005.14165"

    def test_title_only_misc_type(self) -> None:
        entry = bib._fields_to_bibtex_entry("smith2023", {"title": "A Paper"})
        parsed = _parse_bibtex(entry)
        entry_type, key, fields = parsed[0]
        assert entry_type == "misc"
        assert fields["title"] == "A Paper"
        assert "author" not in fields
        assert "doi" not in fields

    def test_balanced_braces(self) -> None:
        entry = bib._fields_to_bibtex_entry(
            "smith2023", {"title": "A Paper", "authors": "Smith"},
        )
        assert entry.count("{") == entry.count("}")


class TestBuildReferencesBib:
    def _setup(self, tmp_path: Path):
        project_notes_dir = tmp_path / "notes"
        tree_root = tmp_path / "manuscripts" / "survey"
        tree_root.mkdir(parents=True)
        lit_dir = project_notes_dir / "literature"
        _write_lit_note(
            lit_dir, "smith2023", citekey="smith2023", title="A Paper",
            authors="Smith, John", year="2023", venue="NeurIPS",
        )
        return project_notes_dir, tree_root

    def test_parses_as_valid_bibtex(self, tmp_path: Path) -> None:
        project_notes_dir, tree_root = self._setup(tmp_path)
        _write_md(tree_root, "report.md", "Smith [[smith2023]] showed this.")
        errors, bib_path = bib.build_references_bib(project_notes_dir, tree_root)
        assert errors == []
        text = bib_path.read_text(encoding="utf-8")
        parsed = _parse_bibtex(text)
        assert len(parsed) == 1
        assert parsed[0][1] == "smith2023"

    def test_never_fabricates_stub_for_unmatched_key(self, tmp_path: Path) -> None:
        project_notes_dir, tree_root = self._setup(tmp_path)
        _write_md(tree_root, "report.md", "Ghost [[ghost2024]] claims this.")
        errors, bib_path = bib.build_references_bib(project_notes_dir, tree_root)
        assert any("ghost2024" in e for e in errors)
        text = bib_path.read_text(encoding="utf-8")
        assert "ghost2024" not in text


# ---------------------------------------------------------------------------
# 6. render_numbered_manuscript — the full D-4 orchestrator
# ---------------------------------------------------------------------------

class TestRenderNumberedManuscript:
    def _setup(self, tmp_path: Path):
        project_notes_dir = tmp_path / "notes"
        tree_root = tmp_path / "manuscripts" / "survey"
        tree_root.mkdir(parents=True)
        lit_dir = project_notes_dir / "literature"
        _write_lit_note(
            lit_dir, "smith2023", citekey="smith2023", title="A Paper",
            authors="Smith, John", year="2023", venue="NeurIPS",
        )
        _write_lit_note(
            lit_dir, "jones2022", citekey="jones2022", title="B Paper",
            authors="Jones, A.", year="2022",
        )
        return project_notes_dir, tree_root

    def test_end_to_end_numbered_render(self, tmp_path: Path) -> None:
        project_notes_dir, tree_root = self._setup(tmp_path)
        _write_md(
            tree_root, "report.md",
            "Jones [[jones2022]] built on Smith [[smith2023]] and cited Jones again [[jones2022]].",
        )
        result = bib.render_numbered_manuscript(project_notes_dir, tree_root)
        assert result["ok"] is True
        assert result["errors"] == []
        assert result["numbering"] == {"jones2022": 1, "smith2023": 2}

        rendered_text = result["rendered_report_path"].read_text(encoding="utf-8")
        assert "[1]" in rendered_text
        assert "[2]" in rendered_text
        assert "[[jones2022]]" not in rendered_text
        assert "[[smith2023]]" not in rendered_text
        assert "## Sources" in rendered_text
        assert rendered_text.index("[1] Jones, A.") < rendered_text.index("[2] Smith, John")

        bib_text = result["bib_path"].read_text(encoding="utf-8")
        assert "smith2023" in bib_text
        assert "jones2022" in bib_text

    def test_d4e_sentinel_citekey_blocks_naming_the_claim(self, tmp_path: Path) -> None:
        project_notes_dir, tree_root = self._setup(tmp_path)
        _write_md(
            tree_root, "report.md",
            "Some unresolved claim about transfer learning [[CITEKEY-UNRESOLVED]] "
            "needs a real source.",
        )
        result = bib.render_numbered_manuscript(project_notes_dir, tree_root)
        assert result["ok"] is False
        assert any("CITEKEY-UNRESOLVED" in e for e in result["errors"])
        assert any("transfer learning" in e for e in result["errors"])
        assert "CITEKEY-UNRESOLVED" not in result["numbering"]
        assert "" not in result["numbering"]
        bib_text = result["bib_path"].read_text(encoding="utf-8")
        assert "CITEKEY-UNRESOLVED" not in bib_text
        # Sources section never gets a numbered entry for the sentinel.
        sources_md = result["sources_md"]
        assert "CITEKEY-UNRESOLVED" not in sources_md

    def test_d4d_residual_wikilink_blocks(self, tmp_path: Path) -> None:
        project_notes_dir, tree_root = self._setup(tmp_path)
        _write_md(
            tree_root, "report.md",
            "Smith [[smith2023]] showed this. Ghost work [[ghost2024]] claims that.",
        )
        result = bib.render_numbered_manuscript(project_notes_dir, tree_root)
        assert result["ok"] is False
        assert any("ghost2024" in e and "residual" in e.lower() for e in result["errors"])
        assert "ghost2024" not in result["numbering"]

    def test_deterministic_rerun(self, tmp_path: Path) -> None:
        project_notes_dir, tree_root = self._setup(tmp_path)
        _write_md(
            tree_root, "report.md",
            "Jones [[jones2022]] built on Smith [[smith2023]].",
        )
        result1 = bib.render_numbered_manuscript(project_notes_dir, tree_root)
        text1 = result1["rendered_report_path"].read_text(encoding="utf-8")
        result2 = bib.render_numbered_manuscript(project_notes_dir, tree_root)
        text2 = result2["rendered_report_path"].read_text(encoding="utf-8")
        assert result1["numbering"] == result2["numbering"]
        assert text1 == text2

    def test_resolve_citations_never_matches_sentinel_or_blank(self, tmp_path: Path) -> None:
        # Direct unit proof at the resolve layer (D-4e): _resolve_citations
        # must exclude the sentinel/blank from `matched` regardless of
        # whether a literature/ note happens to carry that literal citekey
        # (a real failure mode — research.py/note.py stamp
        # `citekey: CITEKEY-UNRESOLVED` on unresolved-metadata notes).
        project_notes_dir, tree_root = self._setup(tmp_path)
        lit_dir = project_notes_dir / "literature"
        _write_lit_note(lit_dir, "stub-unresolved", citekey="CITEKEY-UNRESOLVED", title="Stub")
        draft = _write_md(
            tree_root, "report.md",
            "An unresolved cite [[CITEKEY-UNRESOLVED]] sits here.",
        )
        lit_index = bib._load_literature_bib_index(lit_dir)
        ordered, matched, errors = bib._resolve_citations([draft], lit_index)
        assert "CITEKEY-UNRESOLVED" not in matched
        assert any("CITEKEY-UNRESOLVED" in e for e in errors)


# ---------------------------------------------------------------------------
# 7. check_citation_resolve (PR-M2) stays green/untouched by this PR
# ---------------------------------------------------------------------------

class TestExistingGateUntouched:
    def test_check_citation_resolve_still_works(self, tmp_path: Path) -> None:
        project_notes_dir = tmp_path / "notes"
        tree_root = tmp_path / "manuscripts" / "survey"
        tree_root.mkdir(parents=True)
        lit_dir = project_notes_dir / "literature"
        _write_lit_note(lit_dir, "smith2023", citekey="smith2023", title="A Paper")
        _write_md(tree_root, "report.md", "Smith [[smith2023]] showed this.")
        result = bib.check_citation_resolve(project_notes_dir, tree_root)
        assert result["ok"] is True
        assert result["errors"] == []
