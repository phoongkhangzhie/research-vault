"""test_manuscript_rd1_markdown.py — RD-1: markdown render target.

Next-gen lit-review design §6, RD-1: the manuscript renders markdown
(``report.md`` + ``sections/*.md``); citations are ``[[citekey]]`` wikilinks
backed by a mechanical ``## Sources`` list. The load-bearing acceptance
condition: the hermetic-bib BLOCK and the support-matcher/cold-read fidelity
gates keep firing — now against markdown, not just legacy ``.tex``.

sr: NG-lit-review-waveB (RD-1)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.manuscript import bib
from research_vault.manuscript.draft_files import resolve_draft_files


def _write_lit_note(literature_dir: Path, citekey: str, title: str = "A Paper") -> None:
    literature_dir.mkdir(parents=True, exist_ok=True)
    (literature_dir / f"{citekey}.md").write_text(
        f"---\ntype: literature\ntitle: {title}\ncitekey: {citekey}\n---\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# resolve_draft_files
# ---------------------------------------------------------------------------

def test_resolve_draft_files_empty_when_nothing_drafted(tmp_path: Path):
    tree_root = tmp_path / "manuscripts" / "survey"
    tree_root.mkdir(parents=True)
    assert resolve_draft_files(tree_root) == []


def test_resolve_draft_files_finds_report_md_and_sections(tmp_path: Path):
    tree_root = tmp_path / "manuscripts" / "survey"
    (tree_root / "sections").mkdir(parents=True)
    (tree_root / "report.md").write_text("# Report\n", encoding="utf-8")
    (tree_root / "sections" / "framing.md").write_text("Framing.\n", encoding="utf-8")
    (tree_root / "sections" / "moves.md").write_text("Moves.\n", encoding="utf-8")

    files = resolve_draft_files(tree_root)
    names = [f.name for f in files]
    assert names == ["report.md", "framing.md", "moves.md"]


def test_resolve_draft_files_legacy_tex_still_scanned_mid_migration(tmp_path: Path):
    """A manuscript already in progress under the pre-RD-1 .tex target must
    not be silently orphaned — both extensions are scanned."""
    tree_root = tmp_path / "manuscripts" / "survey"
    (tree_root / "sections").mkdir(parents=True)
    (tree_root / "main.tex").write_text("\\documentclass{article}\n", encoding="utf-8")
    (tree_root / "sections" / "framework.tex").write_text("Framework.\n", encoding="utf-8")

    files = resolve_draft_files(tree_root)
    names = [f.name for f in files]
    assert "main.tex" in names
    assert "framework.tex" in names


# ---------------------------------------------------------------------------
# extract_cited_keys — [[wikilink]] markdown citations alongside \cite{}
# ---------------------------------------------------------------------------

def test_extract_cited_keys_from_markdown_wikilinks(tmp_path: Path):
    md = tmp_path / "sections.md"
    md.write_text(
        "Smith [[smith2023]] and Jones [[jones2022]] both target this problem.\n",
        encoding="utf-8",
    )
    keys = bib.extract_cited_keys([md])
    assert keys == {"smith2023", "jones2022"}


def test_extract_cited_keys_mixed_tex_and_markdown_files(tmp_path: Path):
    tex = tmp_path / "old.tex"
    tex.write_text("Earlier work \\cite{legacy2020} established this.\n", encoding="utf-8")
    md = tmp_path / "new.md"
    md.write_text("Newer work [[fresh2026]] extends it.\n", encoding="utf-8")
    keys = bib.extract_cited_keys([tex, md])
    assert keys == {"legacy2020", "fresh2026"}


# ---------------------------------------------------------------------------
# build_refs_bib / check_hermetic_bib against markdown draft files
# ---------------------------------------------------------------------------

def test_build_refs_bib_resolves_markdown_wikilink_citation(tmp_path: Path):
    project_notes_dir = tmp_path / "notes"
    _write_lit_note(project_notes_dir / "literature", "smith2023")

    tree_root = tmp_path / "manuscripts" / "survey"
    (tree_root / "sections").mkdir(parents=True)
    (tree_root / "sections" / "moves.md").write_text(
        "Smith [[smith2023]] showed this.\n", encoding="utf-8",
    )

    errors, bib_path = bib.build_refs_bib(
        project_notes_dir, tree_root, tex_files=resolve_draft_files(tree_root),
    )
    assert errors == []
    assert "smith2023" in bib_path.read_text(encoding="utf-8")


def test_check_hermetic_bib_blocks_on_dangling_markdown_wikilink(tmp_path: Path):
    project_notes_dir = tmp_path / "notes"
    (project_notes_dir / "literature").mkdir(parents=True)

    tree_root = tmp_path / "manuscripts" / "survey"
    (tree_root / "sections").mkdir(parents=True)
    (tree_root / "sections" / "moves.md").write_text(
        "Ghost work [[nosuchpaper2099]] claims this.\n", encoding="utf-8",
    )

    result = bib.check_hermetic_bib(
        project_notes_dir, tree_root, tex_files=resolve_draft_files(tree_root),
    )
    assert result["ok"] is False
    assert any("nosuchpaper2099" in e for e in result["errors"])
