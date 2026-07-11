"""test_pr_d2_source_routing_driver.py — PR-D2 acceptance tests: the
2-artifact rename (``_report.md`` SOURCE / ``report.md`` RENDER) + the
board-emit production driver's source-routing.

Coverage:
  1. resolve_draft_files (AC2) — the SOURCE, never the render.
  2. AC7 collision guard — a stray drafter write to `report.md` never
     silently clobbers the render / never becomes a draft file.
  3. ★ AC3 — the DRIVER-LEVEL source-routing proof: ``cmd_board_emit``
     assembles ``coverage_diff`` from ``_report.md`` (the source), never
     the ``[N]``-numbered ``report.md`` render. The negative control proves
     the test is non-vacuous: feeding the RENDER into the same mechanical
     diff function false-criticals every committed paper.
  4. AC6 — the ledger -> methods fold-in: ``render_methods_from_ledger``
     traces every number to ``_corpus_ledger.md``, never re-derives.

sr: PR-D2
"""
from __future__ import annotations

from pathlib import Path

import pytest

from research_vault.manuscript import bib
from research_vault.manuscript.check_gates import compute_coverage_diff
from research_vault.manuscript.draft_files import resolve_draft_files
from research_vault.review.ledger import write_corpus_ledger, render_methods_from_ledger


@pytest.fixture
def cfg(tmp_instance):
    from research_vault.config import load_config
    return load_config(reload=True)


def _write_lit_note(literature_dir: Path, citekey: str, title: str = "A Paper") -> None:
    literature_dir.mkdir(parents=True, exist_ok=True)
    (literature_dir / f"{citekey}.md").write_text(
        f"---\ntype: literature\ntitle: {title}\ncitekey: {citekey}\n---\n\nBody.\n",
        encoding="utf-8",
    )


def _write_coverage_map(tree_root: Path, used_citekeys: list[str]) -> Path:
    path = tree_root / "_coverage-map.md"
    lines = ["---", "type: coverage-map", "used:"]
    for ck in used_citekeys:
        lines.append(f"  - citekey: {ck}")
        lines.append("    branch: main")
    lines.append("---\n")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. resolve_draft_files (AC2)
# ---------------------------------------------------------------------------

def test_resolve_draft_files_is_source_not_render(tmp_path: Path):
    tree_root = tmp_path / "manuscripts" / "survey"
    tree_root.mkdir(parents=True)
    (tree_root / "_report.md").write_text("Source [[a2020]].\n", encoding="utf-8")

    files = resolve_draft_files(tree_root)
    assert [p.name for p in files] == ["_report.md"]


# ---------------------------------------------------------------------------
# 2. AC7 collision guard
# ---------------------------------------------------------------------------

def test_collision_guard_report_md_never_a_draft_file(tmp_path: Path):
    """A stray drafter write to `report.md` (a missed brief string, the
    exact failure mode PR-D2 exists to prevent) must never be picked up as
    a draft file — it would silently clobber the render on the next pass."""
    tree_root = tmp_path / "manuscripts" / "survey"
    tree_root.mkdir(parents=True)
    (tree_root / "_report.md").write_text("Source [[a2020]].\n", encoding="utf-8")
    (tree_root / "report.md").write_text("Stray reader render text.\n", encoding="utf-8")

    files = resolve_draft_files(tree_root)
    assert not any(p.name == "report.md" for p in files)
    assert not any(p == tree_root / "report.md" for p in files)


# ---------------------------------------------------------------------------
# 3. ★ AC3 — the driver-level source-routing proof
# ---------------------------------------------------------------------------

class TestBoardEmitSourceRouting:
    def _setup(self, cfg, project: str, slug: str) -> tuple[Path, Path]:
        from research_vault.manuscript import cmd_new

        project_notes_dir = cfg.project_notes_dir(project)
        _write_lit_note(project_notes_dir / "literature", "smith2023", title="Smith Paper")
        _write_lit_note(project_notes_dir / "literature", "jones2022", title="Jones Paper")

        note_path, tree_root, _ = cmd_new(project, slug, ms_type_key="lit-review", config=cfg)
        _write_coverage_map(tree_root, ["smith2023", "jones2022"])

        # Simulate the assemble node's real output: _report.md carries the
        # [[citekey]] SOURCE citing every 'used' paper.
        (tree_root / "_report.md").write_text(
            "Smith [[smith2023]] and Jones [[jones2022]] both address this.\n",
            encoding="utf-8",
        )
        return project_notes_dir, tree_root

    def test_driver_reads_source_missing_is_empty(self, cfg):
        """The real driver call (``cmd_board_emit``) assembles
        ``coverage_diff`` from `_report.md` — every 'used' paper IS cited
        there, so `missing` must be empty."""
        from research_vault.manuscript import cmd_board_emit

        project_notes_dir, tree_root = self._setup(cfg, "demo-research", "survey-d2-driver-a")

        result = cmd_board_emit("demo-research", "survey-d2-driver-a", config=cfg)

        assert result["coverage_diff"]["missing"] == []
        assert set(result["coverage_diff"]["used"]) == {"smith2023", "jones2022"}
        # The WIDTH task in the emitted tasks_doc carries the SAME coverage_diff.
        width_tasks = [
            t for t in result["tasks_doc"]["tasks"] if t.get("axis") == "WIDTH"
        ]
        assert width_tasks, "no WIDTH lens task emitted"
        assert width_tasks[0]["coverage_diff"]["missing"] == []

    def test_driver_never_reads_the_render_negative_control(self, cfg):
        """★ Non-vacuous proof: mispointing the SAME mechanical function at
        the RENDER (report.md, [N]-numbered) instead of the source produces
        FALSE-CRITICAL missing == every used paper — the exact failure mode
        the driver must avoid. This demonstrates the test can actually
        detect a mispointed call site, not just that the correct one works."""
        project_notes_dir, tree_root = self._setup(cfg, "demo-research", "survey-d2-driver-b")

        # Render the reader-facing report.md from the real _report.md source.
        render_result = bib.render_numbered_manuscript(project_notes_dir, tree_root)
        assert render_result["ok"] is True
        rendered_text = render_result["rendered_report_path"].read_text(encoding="utf-8")
        assert "[[smith2023]]" not in rendered_text  # confirms it's really the render

        coverage_map_path = tree_root / "_coverage-map.md"

        # Correct routing: SOURCE.
        from research_vault.manuscript.check_gates import _read_draft_text
        source_body = _read_draft_text(tree_root)
        correct_diff = compute_coverage_diff(coverage_map_path, source_body)
        assert correct_diff["missing"] == []

        # Mispointed routing: RENDER (the bug this whole PR exists to
        # prevent) — WIKILINK_CITE_RE finds zero citekeys in the render, so
        # every committed 'used' paper false-criticals as missing.
        mispointed_diff = compute_coverage_diff(coverage_map_path, rendered_text)
        assert set(mispointed_diff["missing"]) == {"smith2023", "jones2022"}

    def test_driver_uses_source_even_after_render_exists_on_disk(self, cfg):
        """The most realistic failure surface: `report.md` (the render)
        ALREADY EXISTS on disk (a normal post-render manuscript state) when
        the driver runs. ``cmd_board_emit`` must still route to `_report.md`
        — proves the driver's routing is structural (resolve_draft_files
        never returns report.md), not merely "report.md doesn't exist yet
        in this fixture."""
        from research_vault.manuscript import cmd_board_emit

        project_notes_dir, tree_root = self._setup(cfg, "demo-research", "survey-d2-driver-c")
        render_result = bib.render_numbered_manuscript(project_notes_dir, tree_root)
        assert render_result["ok"] is True
        assert (tree_root / "report.md").exists()

        result = cmd_board_emit("demo-research", "survey-d2-driver-c", config=cfg)
        assert result["coverage_diff"]["missing"] == []


# ---------------------------------------------------------------------------
# 4. AC6 — ledger -> methods fold-in
# ---------------------------------------------------------------------------

class TestRenderMethodsFromLedger:
    def test_absent_ledger_is_honest_no_op(self, tmp_path: Path):
        result = render_methods_from_ledger(tmp_path / "_corpus_ledger.md")
        assert "PRISMA scope & method" in result
        assert "No `_corpus_ledger.md` found" in result

    def test_numbers_trace_to_ledger_not_rederived(self, tmp_path: Path):
        """Every count in the rendered methods section must come from the
        ledger's OWN frontmatter/body — plant a corpus with a specific,
        arbitrary count and confirm it (not some independently-recomputed
        value) appears verbatim."""
        review_dir = tmp_path / "reviews" / "survey-ledger"
        review_dir.mkdir(parents=True)
        (review_dir / "_corpus.md").write_text(
            "| Annotation | Citekey |\n|---|---|\n"
            "| [NEW] | alpha2024 |\n"
            "| [NEW] | beta2024 |\n"
            "| [IN-CORPUS: prior] | gamma2023 |\n",
            encoding="utf-8",
        )

        ledger_path = write_corpus_ledger(review_dir, review_scope="survey-ledger")
        ledger_text = ledger_path.read_text(encoding="utf-8")
        assert "new: 2" in ledger_text
        assert "in_corpus: 1" in ledger_text

        rendered = render_methods_from_ledger(ledger_path)
        assert "## PRISMA scope & method" in rendered
        # AC6: the counts trace verbatim to the ledger's own frontmatter —
        # never re-derived independently by the methods renderer.
        assert "| New | 2 |" in rendered
        assert "| In-corpus (previously known) | 1 |" in rendered

    def test_ledger_gap_incompleteness_surfaced(self, tmp_path: Path):
        """A ledger written with gaps (e.g. no _protocol.md) must surface
        `ledger_complete: false` as a loud, visible line in the rendered
        methods section — never silently dropped."""
        review_dir = tmp_path / "reviews" / "survey-gap"
        review_dir.mkdir(parents=True)
        # No _protocol.md / _saturation.md / _corpus.md — every Q/K source absent.
        ledger_path = write_corpus_ledger(review_dir, review_scope="survey-gap")

        rendered = render_methods_from_ledger(ledger_path)
        assert "[LEDGER-GAP]" in rendered
