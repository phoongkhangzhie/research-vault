"""manuscript — PR-M1: the manuscript-loop TYPE-GENERIC core.

Re-instantiates the removed ``manuscript`` loop (SR-RM-FIGMS deleted it — the
craft is preserved in ``honesty-gates.md``/``review-board.md``), rebuilt with a
TYPE system (``manuscript/types/``): the manuscript loop turns ``notes/`` (the
crew-reasoning pillar, built by the knowledge loops) into ``manuscripts/<slug>/``
(the user-facing deliverable pillar), BY TYPE. ``type: lit-review`` is the
survey/review-paper specialization; a future ``type: experiment-paper`` is a
results paper — both consume this same type-generic machinery.

Design: docs/superpowers/specs/2026-07-07-survey-capability-design.md
(§0 TL;DR reframe, §1 type system, §2 module layout, §14 PR-M1).

PR-M1 scope (the type-generic core ONLY — modeled on the review loop's
two-phase scaffolder pattern, review/__init__.py):
  - cmd_new:    scaffold the per-manuscript folder + (type-optional) Phase-1
                manifest.
  - cmd_expand: build the Phase-2 manifest generically from the type's
                ``section_set`` (one node per section -> assemble ->
                [HG:approve-manuscript]).
  - cmd_review: PR-M5 stub — raises loudly (the review-revise board is not
                built yet; never a silent no-op).
  - cmd_list:   list manuscript folders for a project (parity with cmd_list
                on the sibling review/experiment loops).

Explicitly OUT of scope for PR-M1 (separate PRs; stub/interface only here):
  the hermetic .bib build (PR-M2), the hard fidelity gates (PR-M3), the
  equation machinery (PR-M4), the review-revise board (PR-M5), the lit-review
  type's real section table + framework-selection Phase-1 (PR-M6), exemplars
  (PR-M7 -> shipped at PR-M8), the rubric/canary calibration (PR-M8).

Per-manuscript folder (design §0, NOT an OKF taxonomy — too few manuscripts to
warrant one):
  manuscripts/<slug>/
  ├── _manuscript.md   # control + frontmatter: manuscript_type, spine, corpus_hash, run_state
  ├── main.tex
  ├── sections/*.tex
  ├── refs.bib         # hermetic build lands PR-M2 — empty stub here
  └── figures/

Stdlib only.
sr: PR-M1
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from research_vault.config import Config, load_config
from research_vault.note import (
    _parse_frontmatter,   # noqa: WPS301 (private — in-package reuse, mirrors review/__init__.py)
    _render_frontmatter,  # noqa: WPS301
    scaffold_okf_dirs,
)
from research_vault.manuscript.style import (
    get_manuscript_section_tips,
    get_manuscript_style_preamble,
)
from research_vault.manuscript.types import ManuscriptType, get_type, all_type_keys
from research_vault.manuscript import equations as _equations  # PR-M4 seam (§7)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _today() -> str:
    return datetime.date.today().isoformat()


def _manuscripts_root(project: str, cfg: Config) -> Path:
    """Root of the manuscripts pillar: project_notes_dir/manuscripts/."""
    return cfg.project_notes_dir(project) / "manuscripts"


def _manuscript_tree_root(project: str, slug: str, cfg: Config) -> Path:
    """Root of one manuscript's folder: project_notes_dir/manuscripts/<slug>/."""
    return _manuscripts_root(project, cfg) / slug


def _unknown_type_error(key: str) -> ValueError:
    known = all_type_keys()
    return ValueError(
        f"rv manuscript: unknown --type {key!r}. "
        f"Known types: {known or '(none registered)'}. "
        f"An unknown --type fails loudly — it does not silently fall back."
    )


def _write_main_tex_stub(tree_root: Path, slug: str, ms_type_key: str) -> None:
    """Write a neutral article-class main.tex stub to tree_root (idempotent).

    A self-contained inline template (no package-data dependency) — the real
    per-type template/exemplar machinery is design §8/PR-M8 territory; PR-M1
    only needs a compilable skeleton so the folder is genuinely scaffolded.
    """
    main_tex = tree_root / "main.tex"
    if main_tex.exists():
        return  # idempotent — never overwrite an existing draft
    content = (
        "\\documentclass[11pt]{article}\n"
        "\\usepackage[utf8]{inputenc}\n"
        "\\usepackage[T1]{fontenc}\n"
        "\\usepackage{hyperref}\n"
        "\\usepackage{natbib}\n"
        "\n"
        f"% Manuscript: {slug}  (type: {ms_type_key})\n"
        "% Machine-injected results/equation macros land here in PR-M2/PR-M4.\n"
        "\n"
        "\\begin{document}\n"
        "\n"
        f"\\title{{{slug}}}\n"
        "\\author{}\n"
        "\\date{}\n"
        "\\maketitle\n"
        "\n"
        "% Body sections (populated by rv dag run against the Phase-2 manifest)\n"
        "% \\input{sections/draft}\n"
        "\n"
        "\\bibliographystyle{plainnat}\n"
        "\\bibliography{refs}\n"
        "\n"
        "\\end{document}\n"
    )
    main_tex.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Phase-1 manifest (type-optional — design §5 table row 1)
# ---------------------------------------------------------------------------

def _build_phase1_manifest(
    project: str,
    slug: str,
    ms_type: ManuscriptType,
    project_notes_dir: Path,
    tree_root: Path,
    *,
    config: Any = None,
) -> dict[str, Any] | None:
    """Build the Phase-1 manifest for ``ms_type``, or ``None`` (pass-through).

    If ``ms_type.phase1_builder`` is set, delegate to it (a type-specific
    Phase-1 shape — e.g. lit-review's framework-selection sub-loop, design §5,
    PR-M6). Otherwise return ``None``: the default pass-through skips Phase-1
    entirely (design §1: "A `type` whose `phase1_builder` is the default
    pass-through … skips this entirely") — ``rv manuscript expand`` is the
    very next step.

    sr: PR-M1
    """
    if ms_type.phase1_builder is not None:
        return ms_type.phase1_builder(
            project=project,
            slug=slug,
            project_notes_dir=project_notes_dir,
            tree_root=tree_root,
            config=config,
        )
    return None


# ---------------------------------------------------------------------------
# Phase-2 manifest (type-generic — driven entirely by ms_type.section_set)
# ---------------------------------------------------------------------------

def _build_phase2_manifest(
    project: str,
    slug: str,
    ms_type: ManuscriptType,
    project_notes_dir: Path,
    tree_root: Path,
    *,
    config: Any = None,
) -> dict[str, Any]:
    """Build the Phase-2 draft manifest generically from ``ms_type.section_set``.

    Topology (type-generic — the section_set order IS the chain order):
      section-1 -> section-2 -> ... -> section-N -> assemble -> [HG:approve-manuscript]

    Each section node reads its declared ``source_atoms`` (OKF type dirs,
    absolute paths — Fix #34 lesson: absolute so the reads:-grounding resolver
    finds them regardless of project_root at run/tick time) + the sections/
    working dir. ``assemble`` joins the drafted sections into ``main.tex``.
    ``approve-manuscript`` is the terminal human-go gate; the structural/
    fidelity/equation gates that will feed it land in PR-M2/M3/M4.

    Raises ValueError if ``ms_type.section_set`` is empty — a type with no
    sections has nothing to draft; this is a structural inconsistency to
    surface loudly, never a fabricated empty-but-green manifest (charter §2).

    sr: PR-M1
    """
    if not ms_type.section_set:
        raise ValueError(
            f"rv manuscript expand: type {ms_type.key!r} has an empty section_set — "
            f"no sections to draft. This type is not yet populated (see the type's "
            f"module docstring for which PR lands its section table)."
        )

    tips = get_manuscript_section_tips(ms_type, config=config)
    preamble = get_manuscript_style_preamble(config=config)

    # PR-M4 (§7, seam edit — minimal + additive): inject the equation ledger
    # into the relevant sections' briefs. A type with no equation_sources, or
    # a corpus with no pivotal equations, is a no-op (empty ledger -> tips
    # unchanged) — never an error.
    if ms_type.equation_sources:
        equation_ledger = _equations.extract_equation_ledger(
            project_notes_dir, ms_type.equation_sources
        )
        tips = _equations.inject_equation_brief(
            tips, equation_ledger, ms_type.section_set, ms_type.equation_sources
        )

    def _spec(key: str) -> str:
        tip = tips.get(key, f"Write the {key} section.")
        return preamble.rstrip() + "\n\n---\n\n" + tip

    def _afterok(from_id: str) -> dict[str, str]:
        return {"from": from_id, "edge": "afterok"}

    def _rel(okf_type: str) -> str:
        # Absolute path (Fix #34 lesson — project_root at tick time is the
        # manifest's parent dir, i.e. manuscripts/<slug>/, NOT project_notes_dir).
        return str(project_notes_dir / okf_type)

    sections_dir_abs = str(tree_root / "sections")

    nodes: list[dict[str, Any]] = []
    section_ids: list[str] = []
    prev_id: str | None = None

    for section in ms_type.section_set:
        node_id = section.name
        reads = [_rel(atom) for atom in section.source_atoms] + [sections_dir_abs]
        node: dict[str, Any] = {
            "id": node_id,
            "type": "agent",
            "label": f"Draft section '{section.name}' (assembly class: {section.assembly_class})",
            "spec": _spec(section.brief_key or section.name),
            "reads": reads,
            "needs": [_afterok(prev_id)] if prev_id else [],
        }
        nodes.append(node)
        section_ids.append(node_id)
        prev_id = node_id

    # assemble — joins the drafted sections into main.tex
    nodes.append({
        "id": "assemble",
        "type": "agent",
        "label": "Assemble — join drafted sections into main.tex",
        "spec": _spec("assemble"),
        "reads": [sections_dir_abs],
        "needs": [_afterok(section_ids[-1])],
    })

    # approve-manuscript — terminal human-go gate (structural/fidelity/equation
    # gates feeding it land in PR-M2/PR-M3/PR-M4; the review-revise board in PR-M5)
    nodes.append({
        "id": "approve-manuscript",
        "type": "human-go",
        "label": (
            "Gate: Approve manuscript draft (structural gates PR-M2/M3, equation "
            "gate PR-M4, and the review-revise board PR-M5 plug in ahead of this "
            "gate as they land)"
        ),
        "needs": [_afterok("assemble")],
    })

    manifest: dict[str, Any] = {
        "run_id": f"manuscript-{slug}-phase2",
        "project": project,
        "name": f"Manuscript Phase-2 ({ms_type.key}): {slug}",
        "global_cap": 1,
        "nodes": nodes,
    }
    return manifest


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def cmd_new(
    project: str,
    slug: str,
    *,
    ms_type_key: str,
    config: Config | None = None,
) -> tuple[Path, Path, dict[str, Any] | None]:
    """Scaffold a per-manuscript folder + (type-optional) Phase-1 manifest.

    When to use: use ``rv manuscript <project> new <slug> --type <type>`` to
    scaffold a new manuscript. This is the ONLY path that creates the
    per-manuscript folder convention (design §0/§12) — hand-creating
    ``manuscripts/<slug>/`` skips the type registration + the DAG-driven loop.

    Anti-pattern: do NOT hand-write a .tex and hand-collect citations from
    OKF piles — run this so the drafting DAG (``rv manuscript expand`` +
    ``rv dag run``) drives the section-by-section scaffold, with the hermetic
    ``.bib`` (PR-M2), fidelity gates (PR-M3), equation machinery (PR-M4), and
    review-revise board (PR-M5) plugging into this same folder as they land.

    Args:
        project: project slug (must be registered in config).
        slug: manuscript identifier slug (e.g. "survey-llm-eval").
        ms_type_key: the registered ManuscriptType key (e.g. "lit-review").
            Unknown types fail loudly — see ``_unknown_type_error``.
        config: optional Config (loaded if None).

    Returns:
        (note_path, tree_root, manifest) where:
          note_path: path to ``manuscripts/<slug>/_manuscript.md``
          tree_root: path to ``manuscripts/<slug>/``
          manifest:  the Phase-1 manifest dict, or None (pass-through type —
                     design §1: this type's Phase-1 is skipped entirely).

    sr: PR-M1
    """
    ms_type = get_type(ms_type_key)
    if ms_type is None:
        raise _unknown_type_error(ms_type_key)

    cfg = config or load_config()
    project_notes_dir = cfg.project_notes_dir(project)

    # Ensure OKF type dirs exist so section reads: pointers resolve (idempotent).
    scaffold_okf_dirs(project_notes_dir)

    tree_root = _manuscript_tree_root(project, slug, cfg)
    note_path = tree_root / "_manuscript.md"
    if note_path.exists():
        raise FileExistsError(
            f"rv manuscript new: {note_path} already exists. "
            f"Pick a different slug, or remove the existing folder to recreate it "
            f"(avoiding a silent overwrite of an in-progress manuscript)."
        )

    tree_root.mkdir(parents=True, exist_ok=True)
    (tree_root / "sections").mkdir(parents=True, exist_ok=True)
    (tree_root / "figures").mkdir(parents=True, exist_ok=True)

    # ── _manuscript.md — the control + provenance note ────────────────────
    fields: dict[str, str] = {
        "type": "manuscript",
        "manuscript_type": ms_type.key,
        "title": slug,
        "created": _today(),
        "slug": slug,
        "spine": "",          # filled by approve-framework (PR-M6, lit-review only)
        "spine_shape": "",    # PR-M6: one of pipeline|evolution-arc|n-axis|coupled-taxonomies|custom
        "branches": "",       # PR-M6: scalar list of the frozen framework's top-level branch names
        "corpus_hash": "",    # stale-corpus guard (PR-M6)
        "run_state": "",      # dag_run id, set once Phase-1/2 is emitted
        "manuscript_location": str(tree_root / "main.tex"),
    }
    body = (
        "\n"
        "<!-- Manuscript control note (PR-M1) -->\n"
        f"<!-- type: {ms_type.key} -->\n"
        "<!-- Use `rv manuscript <project> expand <slug>` to emit the Phase-2 "
        "draft+review manifest. -->\n"
        "<!-- The hermetic .bib build (PR-M2), fidelity gates (PR-M3), equation -->\n"
        "<!-- machinery (PR-M4), and review-revise board (PR-M5) plug into this -->\n"
        "<!-- folder as they land — this note and the drafting DAG are the -->\n"
        "<!-- durable control surface across all of them. -->\n"
        "\n"
        "## Scope\n\n"
        f"<!-- manuscript_type: {ms_type.key} -->\n"
    )
    note_path.write_text(_render_frontmatter(fields) + "\n" + body, encoding="utf-8")

    _write_main_tex_stub(tree_root, slug, ms_type.key)

    refs_bib = tree_root / "refs.bib"
    if not refs_bib.exists():
        refs_bib.write_text(
            "% refs.bib — hermetic build from literature/ frontmatter lands in PR-M2.\n"
            "% Do NOT hand-edit citekeys here.\n",
            encoding="utf-8",
        )

    manifest = _build_phase1_manifest(
        project=project,
        slug=slug,
        ms_type=ms_type,
        project_notes_dir=project_notes_dir,
        tree_root=tree_root,
        config=cfg,
    )
    if manifest is not None:
        manifest_path = tree_root / "phase1-dag.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    return note_path, tree_root, manifest


def cmd_expand(
    project: str,
    slug: str,
    *,
    config: Config | None = None,
) -> dict[str, Any]:
    """Emit the Phase-2 manifest generically from the manuscript's registered type.

    When to use: ``rv manuscript <project> expand <slug>`` after ``rv manuscript
    new`` (and, for a type with a real Phase-1, after its human-go gate is
    approved). Builds one node per ``ms_type.section_set`` entry, chained
    sequentially, joined by ``assemble`` -> ``[HG:approve-manuscript]``.

    Anti-pattern: do NOT hand-write a Phase-2 manifest — the section_set comes
    from the registered ManuscriptType; hand-writing would drift from the
    type's real section table as it's populated (PR-M6).

    Args:
        project: project slug.
        slug: manuscript identifier (same as passed to ``cmd_new``).
        config: optional Config (loaded if None).

    Returns:
        The Phase-2 manifest dict (also saved as ``phase2-dag.json``).

    sr: PR-M1
    """
    cfg = config or load_config()
    project_notes_dir = cfg.project_notes_dir(project)
    tree_root = _manuscript_tree_root(project, slug, cfg)
    note_path = tree_root / "_manuscript.md"

    if not note_path.exists():
        raise FileNotFoundError(
            f"rv manuscript expand: {note_path} not found. "
            f"Run `rv manuscript {project} new {slug} --type <type>` first."
        )

    text = note_path.read_text(encoding="utf-8")
    fields, _ = _parse_frontmatter(text)
    ms_type_key = fields.get("manuscript_type", "")
    ms_type = get_type(ms_type_key)
    if ms_type is None:
        raise _unknown_type_error(ms_type_key)

    manifest = _build_phase2_manifest(
        project=project,
        slug=slug,
        ms_type=ms_type,
        project_notes_dir=project_notes_dir,
        tree_root=tree_root,
        config=cfg,
    )

    manifest_path = tree_root / "phase2-dag.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def cmd_review(
    project: str,
    slug: str,
    *,
    config: Config | None = None,
) -> dict[str, Any]:
    """PR-M5 stub: the review-revise board is not built yet.

    When to use: ``rv manuscript <project> review <slug>`` will drive the
    2-round x 3-reviewer adversarial review-revise board (design §9) once
    PR-M5 lands. Today it raises loudly rather than silently no-op-ing
    (charter §2: surface, never silently drop).

    sr: PR-M1
    """
    raise NotImplementedError(
        f"rv manuscript review: the review-revise board (2 rounds x 3 reviewers, "
        f"design §9) ships in PR-M5 — not yet implemented in PR-M1 (the "
        f"type-generic core). project={project!r} slug={slug!r}."
    )


def cmd_list(
    project: str,
    *,
    config: Config | None = None,
) -> list[dict[str, Any]]:
    """List manuscript folders for the given project.

    When to use: ``rv manuscript <project> list`` to enumerate all manuscripts
    scaffolded for a project.

    Returns:
        List of {slug, manuscript_type, path, fields} dicts, one per
        manuscript folder found. Empty list when none exist yet.

    sr: PR-M1
    """
    cfg = config or load_config()
    root = _manuscripts_root(project, cfg)
    if not root.exists():
        return []

    results: list[dict[str, Any]] = []
    for note_path in sorted(root.glob("*/_manuscript.md")):
        text = note_path.read_text(encoding="utf-8")
        fields, _ = _parse_frontmatter(text)
        if fields.get("type") == "manuscript":
            results.append({
                "slug": fields.get("slug", note_path.parent.name),
                "manuscript_type": fields.get("manuscript_type", ""),
                "path": note_path,
                "fields": fields,
            })
    return results
