# SPDX-License-Identifier: AGPL-3.0-or-later
"""manuscript: the manuscript-loop TYPE-GENERIC core.

Re-instantiates the removed ``manuscript`` loop (deleted earlier — the
craft is preserved in ``honesty-gates.md``/``review-board.md``), rebuilt with a
TYPE system (``manuscript/types/``): the manuscript loop turns ``notes/`` (the
crew-reasoning pillar, built by the knowledge loops) into ``manuscripts/<slug>/``
(the user-facing deliverable pillar), BY TYPE. ``type: lit-review`` is the
survey/review-paper specialization; a future ``type: experiment-paper`` is a
results paper — both consume this same type-generic machinery.

(TL;DR reframe, type system, module layout).

 scope (the type-generic core ONLY — modeled on the review loop's
two-phase scaffolder pattern, review/__init__.py):
  - cmd_new:    scaffold the per-manuscript folder + (type-optional) Phase-1
                manifest.
  - cmd_expand: build the Phase-2 manifest generically from the type's
                ``section_set`` (one node per section -> assemble ->
                approve-manuscript (auto-resolved)).
  - cmd_review: run the 2-round x 3-reviewer adversarial review-revise board
                (``manuscript/review_board.py``).
  - cmd_list:   list manuscript folders for a project (parity with cmd_list
                on the sibling review/experiment loops).

Explicitly OUT of scope for (stub/interface only here at the time;
STATUS as — M2/M3/M4/M5/M6 have since LANDED and are wired together
by ``manuscript/check_gates.py::build_approve_payload``, called from
``rv dag approve`` at ``approve-manuscript`` AND re-fired every review-revise
round via ``review_board.run_revise``):
  the hermetic .bib build (landed — ``manuscript/bib.py``), the hard
  fidelity gates (landed — ``manuscript/fidelity_gates.py``), the
  equation machinery (landed — ``manuscript/equations.py``), the
  review-revise board (landed — ``manuscript/review_board.py``, a
  PLACEHOLDER rubric/canary swaps in the researcher's calibrated versions), the
  lit-review type's real section table + framework-selection Phase-1 +
  ``source_transform`` (landed — ``manuscript/types/lit_review.py``,
  wired into this module's ``_build_phase2_manifest``), exemplars (
  building in a parallel wave — not yet merged), the
  rubric/canary calibration (NOT YET BUILT).

Per-manuscript folder (NOT an OKF taxonomy — too few manuscripts to
warrant one). Markdown is the ONLY render target — LaTeX has been removed
entirely (the operator's explicit call — see DEVLOG):
  manuscripts/<slug>/
  ├── _manuscript.md   # control + frontmatter: manuscript_type, spine, corpus_hash, run_state
  ├── _report.md # RD-1: internal [[citekey]] SOURCE (drafter/assemble write target)
  ├── report.md # reader-facing [N]-numbered render (bib.render_numbered_manuscript)
  ├── sections/*.md    # RD-1: markdown sections
  ├── references.md # hermetic citekey-resolution ledger — see manuscript/bib.py
  ├── references.bib # hermetic BibTeX build (paired with the report.md render)
  └── figures/

Stdlib only.
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
from research_vault.manuscript import equations as _equations # seam
from research_vault.manuscript import exemplars as _exemplars # seam


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


def _write_report_md_stub(tree_root: Path, slug: str, ms_type_key: str) -> None:
    """Write a neutral markdown report stub to tree_root (idempotent).

    RD-1 (next-gen lit-review): the manuscript's reader path
    renders MARKDOWN — ``_report.md`` (this stub: the internal
    ``[[citekey]]`` SOURCE, the assemble node's write target) +
    ``sections/*.md``. The reader-facing ``report.md`` (no underscore) is a
    SEPARATE artifact produced later by ``bib.render_numbered_manuscript``,
    never scaffolded here. LaTeX (``main.tex``/tex-macro injection) has been
    removed entirely as a render target. Citations use the ``[[citekey]]``
    wikilink form (see ``manuscript/bib.py``'s ``_WIKILINK_CITE_RE``);
    ``references.md`` is the hermetic-bib gate's artifact — a
    markdown-native citekey-resolution ledger, never a BibTeX file.

    A self-contained inline template (no package-data dependency) — the real
    per-type template/exemplar machinery is territory; this
    only needs a scaffolded skeleton so the folder is genuinely scaffolded.
    """
    report_md = tree_root / "_report.md"
    if report_md.exists():
        return  # idempotent — never overwrite an existing draft
    content = (
        f"# {slug}\n\n"
        f"<!-- Manuscript: {slug}  (type: {ms_type_key}) -->\n"
        "<!-- Machine-injected results/equation data + hermetic citekey resolution:\n"
        "     references.md is built hermetically by manuscript/bib.py (RD-1);\n"
        "     pivotal equations are injected into the writer briefs by\n"
        "     manuscript/equations.py and checked every round by\n"
        "     check_gates.py::build_approve_payload. -->\n\n"
        "<!-- Body sections (populated by rv dag run against the Phase-2\n"
        "     manifest) are joined here in reading order by the assemble node. -->\n"
        "<!: this file (_report.md) is the internal wikilink-citation\n"
        "     SOURCE (citekeys as double-bracket wikilinks). The reader-facing\n"
        "     report.md (no underscore) is a SEPARATE artifact produced by\n"
        "     manuscript/bib.py::render_numbered_manuscript. -->\n"
    )
    report_md.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Phase-1 manifest (type-optional table row 1)
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
    Phase-1 shape — e.g. lit-review's framework-selection sub-loop,
    ). Otherwise return ``None``: the default pass-through skips Phase-1
    entirely ("A `type` whose `phase1_builder` is the default
    pass-through … skips this entirely") — ``rv manuscript expand`` is the
    very next step.

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

def _inject_source_transform_tips(
    tips: dict[str, str], transform: dict[str, Any]
) -> dict[str, str]:
    """Wire ``ms_type.source_transform``'s output into the matching section tips.

    ★ Integration-PR seam edit (mirrors ``inject_equation_brief``
    pattern — additive, a NEW dict returned, the input never mutated):
    ``lit_review.source_transform`` was dead code — computed but
    never injected anywhere, so its briefs' "use the injected PRISMA ledger /
    comparison table" instructions dangled. This makes it real:

      - ``appendix-methods``   -> appended to the ``appendix-methods`` tip
        (gold-settled: the PRISMA ledger relocates from the removed
        ``prisma-scope`` body row all the way OUT of ``report.md`` — the
        tip now instructs a DEVLOG/control-note write, never a body
        section or an appendix; ``report.md`` carries no Appendix at all).
      - ``references``         -> appended to the ``references`` tip.
      - ``provenance_header``  -> appended to the ``assemble`` tip (RD-3: the
        hash-free blockquote the assembler prepends atop ``report.md``).
      - ``framework_branches`` -> appended to BOTH the ``introduction`` tip
        (RD-4: the spine-at-a-glance orientation table folded into the
        opening section, since the standalone ``framework`` body row is
        deleted) and the ``thematic-sections`` tip (which needs to know how
        many branches to draft one section per).

    A key absent from ``transform`` (e.g. a future type whose
    ``source_transform`` returns a different shape) or a falsy value is a
    no-op for that key — never an error; a type with no ``source_transform``
    at all never calls this function (guarded by the caller).
    """
    result = dict(tips)

    appendix_methods = transform.get("appendix-methods")
    if appendix_methods and "appendix-methods" in result:
        result["appendix-methods"] = (
            result["appendix-methods"].rstrip() + "\n\n---\n\n" + appendix_methods
        )

    references = transform.get("references")
    if references and "references" in result:
        result["references"] = result["references"].rstrip() + "\n\n---\n\n" + references

    provenance_header = transform.get("provenance_header")
    if provenance_header and "assemble" in result:
        result["assemble"] = (
            result["assemble"].rstrip() + "\n\n---\n\nInjected provenance_header "
            "(prepend verbatim atop _report.md):\n\n" + provenance_header
        )

    branches = transform.get("framework_branches")
    if branches:
        branches_block = (
            "Frozen framework branches (from `_manuscript.md`, "
            "approve-framework — NEVER re-derive): "
            + ", ".join(branches if isinstance(branches, list) else [str(branches)])
        )
        for key in ("introduction", "thematic-sections"):
            if key in result:
                result[key] = result[key].rstrip() + "\n\n" + branches_block

    return result


def _build_phase2_manifest(
    project: str,
    slug: str,
    ms_type: ManuscriptType,
    project_notes_dir: Path,
    tree_root: Path,
    *,
    config: Any = None,
    manuscript_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the Phase-2 draft manifest generically from ``ms_type.section_set``.

    Topology (type-generic — the section_set order IS the chain order):
      section-1 -> section-2 -> ... -> section-N -> assemble -> approve-manuscript (auto-resolved)

    Each section node reads its declared ``source_atoms`` (OKF type dirs,
    absolute paths — Fix #34 lesson: absolute so the reads:-grounding resolver
    finds them regardless of project_root at run/tick time) + the sections/
    working dir. ``assemble`` joins the drafted sections into ``_report.md``
    (RD-1, the internal ``[[citekey]]`` SOURCE — never ``report.md``,
    which is a separate reader-facing render produced later).
    ``approve-manuscript`` is the terminal human-go gate; the hermetic-.bib,
    fidelity, and equation gates that feed it are
    assembled by ``manuscript/check_gates.py::build_approve_payload`` and
    wired into ``rv dag approve`` (the manuscript-integration PR).

    Raises ValueError if ``ms_type.section_set`` is empty — a type with no
    sections has nothing to draft; this is a structural inconsistency to
    surface loudly, never a fabricated empty-but-green manifest (charter §2).

    Args:
        manuscript_fields: the ``_manuscript.md`` frontmatter fields dict
            (as read by ``cmd_expand``), used to pass the FROZEN spine
            (``spine_shape``+``branches``) into ``ms_type.source_transform``
            — valid because ``expand`` runs after ``approve-framework`` has
            already frozen it. ``None``/absent-keys degrade to an empty
            spine (a type with no ``source_transform``, or a manuscript
            whose framework isn't frozen yet, is a correct no-op).

    """
    # NG-7 (next-gen lit-review): a type's custom Phase-2 builder
    # (single-pass outline -> draft -> assemble) takes over entirely when
    # present — mirrors ``_build_phase1_manifest``'s delegation exactly.
    if ms_type.phase2_builder is not None:
        return ms_type.phase2_builder(
            project=project,
            slug=slug,
            project_notes_dir=project_notes_dir,
            tree_root=tree_root,
            manuscript_fields=manuscript_fields,
            config=config,
        )

    if not ms_type.section_set:
        raise ValueError(
            f"rv manuscript expand: type {ms_type.key!r} has an empty section_set — "
            f"no sections to draft. This type is not yet populated (see the type's "
            f"module docstring for which PR lands its section table)."
        )

    tips = get_manuscript_section_tips(ms_type, config=config)
    preamble = get_manuscript_style_preamble(config=config)

    # (seam edit — minimal + additive): inject the equation ledger
    # into the relevant sections' briefs. A type with no equation_sources, or
    # a corpus with no pivotal equations, is a no-op (empty ledger -> tips
    # unchanged) — never an error.
    if ms_type.equation_sources:
        equation_ledger = _equations.extract_equation_ledger(
            project_notes_dir, ms_type.equation_sources,
            literature_root=getattr(config, "literature_root", None),
            concepts_root=getattr(config, "concepts_root", None),
        )
        tips = _equations.inject_equation_brief(
            tips, equation_ledger, ms_type.section_set, ms_type.equation_sources
        )

    # Integration-PR (seam edit — minimal + additive, mirrors the block
    # above): wire M6's `source_transform` (previously computed nowhere —
    # dead code). A type with no `source_transform` is a no-op (unchanged
    # tips); cmd_expand passes the frozen spine (`spine_shape`+`branches`)
    # read from `_manuscript.md` via `manuscript_fields` — valid because
    # `expand` runs after `approve-framework` has already frozen it.
    if ms_type.source_transform is not None:
        spine = {
            "spine_shape": (manuscript_fields or {}).get("spine_shape", ""),
            "branches": (manuscript_fields or {}).get("branches", ""),
        }
        transform = ms_type.source_transform(
            project, project_notes_dir, tree_root, spine, config=config
        )
        tips = _inject_source_transform_tips(tips, transform)

    #  NG-8 (next-gen lit-review, supersedes the
    # verbatim form): embed the type's exemplar bundle into the matching
    # sections' briefs as MUST-READ POINTERS (``read <path>``), not a
    # verbatim embed and not a prose "write in a synthesis style"
    # description. A type with no `exemplar_bundle`, or a bundle dir that
    # doesn't exist yet, is a no-op (empty bundle -> tips/preamble unchanged)
    # — never an error.
    exemplar_blocks: list[dict[str, Any]] = []
    if ms_type.exemplar_bundle:
        exemplar_blocks = _exemplars.load_exemplar_bundle(ms_type.exemplar_bundle)
        tips = _exemplars.inject_exemplar_briefs(tips, exemplar_blocks)
        principle_block = _exemplars.build_principle_anchor_block(exemplar_blocks)
        if principle_block:
            preamble = preamble.rstrip() + "\n\n---\n\n" + principle_block

    exemplar_bundle_dir = _exemplars.resolve_exemplar_bundle_path(ms_type.exemplar_bundle)

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
        # NG-8: wire the exemplar bundle's absolute dir into `reads:` so the
        # harness's reads-grounding resolver surfaces the pointed-at files as
        # available context ("the `reads:` wiring guarantees
        # availability; the outline citation guarantees use").
        if exemplar_bundle_dir is not None:
            reads.append(str(exemplar_bundle_dir))
        node_spec = _spec(section.brief_key or section.name)

        # NG-8: the pre-dispatch presence assertion — a driver that
        # somehow bypassed inject_exemplar_briefs for a section this bundle
        # covers fails LOUDLY here, never silently ships a voiceless brief.
        if exemplar_blocks:
            ok, msg = _exemplars.check_exemplar_pointer_presence(node_id, node_spec, exemplar_blocks)
            if not ok:
                raise ValueError(f"rv manuscript expand: {msg}")

        node: dict[str, Any] = {
            "id": node_id,
            "type": "agent",
            "label": f"Draft section '{section.name}' (assembly class: {section.assembly_class})",
            "spec": node_spec,
            "reads": reads,
            "needs": [_afterok(prev_id)] if prev_id else [],
        }
        nodes.append(node)
        section_ids.append(node_id)
        prev_id = node_id

    # assemble — joins the drafted sections into _report.md (RD-1,
    # the internal [[citekey]] SOURCE — never the rendered `report.md`).
    nodes.append({
        "id": "assemble",
        "type": "agent",
        "label": "Assemble — join drafted sections into _report.md (RD-1)",
        "spec": _spec("assemble"),
        "reads": [sections_dir_abs],
        "needs": [_afterok(section_ids[-1])],
        "produces": {"_report.md": str(tree_root / "_report.md")},
    })

    # approve-manuscript — terminal human-go gate. The hermetic-.bib,
    # fidelity, and equation gates are LANDED and assembled by
    # check_gates.py::build_approve_payload, wired into `rv dag approve` at
    # this node; the review-revise board is NOT YET built.
    nodes.append({
        "id": "approve-manuscript",
        "type": "human-go",
        "label": (
            "Gate: Approve manuscript draft (gated by "
            "manuscript/check_gates.py::build_approve_payload — hermetic .bib "
            "BLOCK, equation-fidelity SIGNAL, support-matcher BLOCK/SIGNAL "
            "behind the judge guard; the review-revise board "
            " will re-fire these ahead of this gate once it lands)"
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
    slug: str | None = None,
    *,
    ms_type_key: str,
    config: Config | None = None,
    from_review: str | None = None,
) -> tuple[Path, Path, dict[str, Any] | None]:
    """Scaffold a per-manuscript folder + (type-optional) Phase-1 manifest.

    When to use: use ``rv manuscript <project> new <slug> --type <type>`` to
    scaffold a new manuscript. This is the ONLY path that creates the
    per-manuscript folder convention — hand-creating
    ``manuscripts/<slug>/`` skips the type registration + the DAG-driven loop.

    Anti-pattern: do NOT hand-write markdown sections and hand-collect
    citations from OKF piles — run this so the drafting DAG (``rv manuscript
    expand`` + ``rv dag run``) drives the section-by-section scaffold, with
    the hermetic references build, fidelity gates, equation
    machinery, and review-revise board plugging into this
    same folder as they land.

    NG-7 (explore-rl friction #6): the manuscript slug is expected to
    match its underlying ``rv review`` scope id (``reviews/<slug>/_corpus.md``)
    — a silent mismatch surfaces two DAG nodes deep as an unexplained "no
    frozen corpus" from the ``scope``/``coverage-gate`` machinery. Two fixes:
      - ``from_review``: adopts the scope id AS the slug (pre-binds the
        corpus by construction) when ``slug`` is omitted. Passing BOTH an
        explicit ``slug`` and a DIFFERENT ``from_review`` is a real mismatch
        — warned, never silently "fixed" by picking one for you.
      - a warn-at-creation: ANY slug (with or without ``--from-review``)
        that has no matching ``reviews/<slug>/_corpus.md`` gets a loud
        ``UserWarning`` at creation time, not a confusing failure downstream.

    Args:
        project: project slug (must be registered in config).
        slug: manuscript identifier slug (e.g. "survey-llm-eval"). Optional
            when ``from_review`` is given (adopted from it).
        ms_type_key: the registered ManuscriptType key (e.g. "lit-review").
            Unknown types fail loudly — see ``_unknown_type_error``.
        config: optional Config (loaded if None).
        from_review: an ``rv review`` scope id to adopt as the slug (NG-7
            ) — pre-binds the corpus by making the manuscript slug equal
            the review scope id, the convention every corpus-lookup keys off.

    Returns:
        (note_path, tree_root, manifest) where:
          note_path: path to ``manuscripts/<slug>/_manuscript.md``
          tree_root: path to ``manuscripts/<slug>/``
          manifest:  the Phase-1 manifest dict, or None (pass-through type —
                     this type's Phase-1 is skipped entirely).

    """
    import warnings

    if from_review:
        if slug and slug != from_review:
            warnings.warn(
                f"rv manuscript new: explicit slug {slug!r} differs from "
                f"--from-review scope {from_review!r} — the manuscript-slug "
                f"== review-scope-id convention will NOT hold for this "
                f"manuscript (corpus_hash/coverage-gate lookups key off the "
                f"slug). Proceeding with the EXPLICIT slug {slug!r} — "
                f"--from-review is not silently substituted when a slug is "
                f"also given.",
                UserWarning,
                stacklevel=2,
            )
        elif not slug:
            slug = from_review

    if not slug:
        raise ValueError(
            "rv manuscript new: a slug is required — pass it directly, or "
            "pass --from-review <scope> to adopt the review scope id as the "
            "slug (NG-7)."
        )

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

    # NG-7 warn-at-creation: a slug with no matching frozen review
    # corpus is a silent landmine that surfaces two nodes deep (scope/
    # coverage-gate report "no frozen corpus" with no explanation of why).
    expected_corpus = project_notes_dir / "reviews" / slug / "_corpus.md"
    if not expected_corpus.exists():
        warnings.warn(
            f"rv manuscript new: no frozen review corpus found at "
            f"{expected_corpus} for slug {slug!r}. If this manuscript "
            f"summarizes a completed `rv review` loop, the manuscript slug "
            f"is expected to MATCH that review's scope id — consider `rv "
            f"manuscript new --from-review <scope>` instead. Proceeding — "
            f"source_transform/coverage-gate will render an honest 'no "
            f"corpus' state until one is frozen at this slug.",
            UserWarning,
            stacklevel=2,
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
        "spine": "",          # filled by approve-framework (lit-review only)
        "spine_shape": "",    # one of pipeline|evolution-arc|n-axis|coupled-taxonomies|custom
        "branches": "",       # scalar list of the frozen framework's top-level branch names
        "corpus_hash": "",    # stale-corpus guard
        "run_state": "",      # dag_run id, set once Phase-1/2 is emitted
        "manuscript_location": str(tree_root / "report.md"),  # reader-facing render, produced later
    }
    body = (
        "\n"
        "<!-- Manuscript control note -->\n"
        f"<!-- type: {ms_type.key} -->\n"
        "<!-- Use `rv manuscript <project> expand <slug>` to emit the Phase-2 "
        "draft+review manifest. -->\n"
        "<!-- The hermetic .bib build, fidelity gates, and equation -->\n"
        "<!-- machinery are LANDED and assembled by -->\n"
        "<!-- manuscript/check_gates.py::build_approve_payload, gating -->\n"
        "<!-- approve-manuscript. The review-revise board is NOT YET -->\n"
        "<!-- built — this note and the drafting DAG remain the durable -->\n"
        "<!-- control surface across all of them. -->\n"
        "\n"
        "## Scope\n\n"
        f"<!-- manuscript_type: {ms_type.key} -->\n"
    )
    note_path.write_text(_render_frontmatter(fields) + "\n" + body, encoding="utf-8")

    _write_report_md_stub(tree_root, slug, ms_type.key)

    references_md = tree_root / "references.md"
    if not references_md.exists():
        references_md.write_text(
            "# References\n\n"
            "<!-- references.md — hermetic build from literature/ frontmatter, -->\n"
            "<! (landed). See manuscript/bib.py::build_references_md — -->\n"
            "<!-- re-run the manuscript bib gate to regenerate. Do NOT hand-edit -->\n"
            "<!-- citekeys here. -->\n",
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
    sequentially, joined by ``assemble`` -> ``approve-manuscript (auto-resolved)``.

    Anti-pattern: do NOT hand-write a Phase-2 manifest — the section_set comes
    from the registered ManuscriptType; hand-writing would drift from the
    type's real section table as it's populated.

    Args:
        project: project slug.
        slug: manuscript identifier (same as passed to ``cmd_new``).
        config: optional Config (loaded if None).

    Returns:
        The Phase-2 manifest dict (also saved as ``phase2-dag.json``).

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
        manuscript_fields=fields,
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
    judge_fn: Any | None = None,
    canary_judge_fn: Any | None = None,
) -> dict[str, Any]:
    """Drive the 2-round x 3-reviewer adversarial review-revise board.

    When to use: ``rv manuscript <project> review <slug>`` runs the bounded
    review-revise loop (``manuscript/review_board.py``) against the
    manuscript's current draft. The in-process API judge default was
    DELETED — a None ``judge_fn`` raises loudly (charter §2: never a silent
    no-op). Production cold-judge review runs via the 6-lens board's
    emit/ingest fan-out (``manuscript.board`` + ``gates.board_seam``), driven
    out-of-band by the hub; this OLD 2x3 in-process board is exercised only
    with a test-injected ``judge_fn``.

    Args:
        project: project slug.
        slug: manuscript identifier (same as passed to ``cmd_new``).
        config: optional Config (loaded if None).
        judge_fn: injectable reviewer judge — ``(prompt: str) -> str``.
            REQUIRED: None raises loudly — there is no live-API
            default. Pass a mock only in tests.
        canary_judge_fn: injectable canary judge (defaults to the same judge
            as ``judge_fn`` — the mandatory canary always fires against the
            SAME judge it's calibrating).

    Returns:
        The ``run_review_board`` result dict (``cleared``, ``rounds``,
        ``not_cleared``, ``escalation``, ``honest_report``, ``meta``, ...).

    """
    from research_vault.manuscript import review_board as _review_board
    from research_vault.manuscript.check_gates import _read_draft_text

    cfg = config or load_config()
    project_notes_dir = cfg.project_notes_dir(project)
    tree_root = _manuscript_tree_root(project, slug, cfg)
    note_path = tree_root / "_manuscript.md"

    if not note_path.exists():
        raise FileNotFoundError(
            f"rv manuscript review: {note_path} not found. "
            f"Run `rv manuscript {project} new {slug} --type <type>` first."
        )

    text = note_path.read_text(encoding="utf-8")
    fields, _ = _parse_frontmatter(text)
    ms_type_key = fields.get("manuscript_type", "")
    ms_type = get_type(ms_type_key)
    if ms_type is None:
        raise _unknown_type_error(ms_type_key)

    # the in-process API judge-construction block was DELETED. rv NEVER
    # builds a live judge here — the production cold-judge path is the 6-lens
    # board's emit/ingest fan-out (``gates.board_seam`` + ``manuscript.board``,
    # driven out-of-band by the hub, its result consumed at
    # ``approve-manuscript``). This OLD 2x3 in-process board
    # (``review_board.run_review_board``) is exercised only with a
    # test-injected ``judge_fn``; a None ``judge_fn`` raises loudly rather
    # than reach for a deleted live-API default (charter §2: never a silent
    # no-op).
    if judge_fn is None:
        raise RuntimeError(
            "rv manuscript review: no judge_fn supplied. The in-process API "
            "judge path was deleted — the production cold-judge review "
            "runs via the 6-lens board's emit/ingest fan-out (the hub fans "
            "out fresh cold subagent-judges; the result is consumed at "
            "approve-manuscript). Pass an explicit judge_fn only in tests."
        )
    _judge_fn = judge_fn
    _canary_judge_fn = canary_judge_fn if canary_judge_fn is not None else _judge_fn

    review_cfg = _review_board.get_review_config(cfg)
    draft_text = _read_draft_text(tree_root)

    result = _review_board.run_review_board(
        draft_text,
        tree_root,
        project_notes_dir,
        ms_type,
        N=review_cfg["max_rounds"],
        K=review_cfg["reviewers_per_round"],
        floor_dims=review_cfg["floor_dimensions"],
        floor_value=review_cfg["floor_value"],
        judge_fn=_judge_fn,
        judge_model="",  # audit label only; no env-var judge-model read
        rubric_override=ms_type.rubric,
        config=cfg,
        canary_judge_fn=_canary_judge_fn,
        revise_judge_fn=_judge_fn,
    )

    # meta["manuscript_review"] logging — stamped onto the
    # control note so the run's outcome is durable, human-auditable state,
    # not just a stdout line that scrolls away.
    _stamp_review_meta(note_path, result)

    return result


def _stamp_review_meta(note_path: Path, result: dict[str, Any]) -> None:
    """Append an honest review-board run record to ``_manuscript.md``.

    Never says "approved" — records ``cleared``/``cleared_at``/the honest
    report string, appended (never overwrites prior runs' history).

    also stamps ``judge_model`` + the set of reviewer ``prompt_hash``
    values actually used this run — audit + drift-detection provenance (the
    support-matcher convention), so a run's judge identity is
    durable state, not just a stdout line that scrolls away.

    """
    text = note_path.read_text(encoding="utf-8")

    judge_model = ""
    prompt_hashes: list[str] = []
    for round_record in result.get("rounds", []):
        for reviewer in round_record.get("reviewers", []) or []:
            if reviewer.get("judge_model"):
                judge_model = reviewer["judge_model"]
            ph = reviewer.get("prompt_hash", "")
            if ph:
                prompt_hashes.append(ph)

    stamp = (
        "\n<!-- manuscript_review run "
        f"{_today()}: cleared={result['cleared']} "
        f"cleared_at={result['cleared_at']} "
        f"judge_model={judge_model!r} "
        f"prompt_hashes={prompt_hashes!r} "
        f"honest_report={result['honest_report']!r} -->\n"
    )
    note_path.write_text(text + stamp, encoding="utf-8")


def _judge_dir(tree_root: Path, gate: str) -> Path:
    """``manuscripts/<slug>/judge/<gate>/`` — one dir per gate (
    NG-4's "one file per gate")."""
    return tree_root / "judge" / gate


def cmd_judge_emit(
    project: str,
    slug: str,
    *,
    config: Config | None = None,
    gate: str = "support-matcher",
) -> dict[str, Any]:
    """Emit the NG-4 cold-agent-judge fan-out task set (
    Phase A) — ``rv manuscript <project> judge-emit <slug>``.

    Writes ``manuscripts/<slug>/judge/support-matcher/_judge-tasks.json`` +
    ``_judge-canary-key.json``. Support-matcher-ONLY — the cold-read
    self-containment critic that originally shared this seam was removed
    (SIGNAL-only, non-actionable under hands-off autonomy, redundant with
    the review board + RD-6; the operator's call, see DEVLOG). rv calls NO LLM on
    this path — the hub is responsible for fanning cold subagent-judges
    out over the written tasks file and writing ``_judge-verdicts.json``
    alongside it; run ``rv manuscript <project> judge-ingest <slug>`` once
    that lands.

    ``gate`` is kept as a parameter (fixed to ``"support-matcher"``, the
    only accepted value) rather than dropped outright, so the CLI wrapper
    and any caller that already threads it through stay source-compatible.

    Returns ``{"support-matcher": {...}}`` — the value is the emit
    function's own ``{"tasks_doc", "canary_key_doc"}`` return.

    """
    from research_vault.manuscript import fidelity_gates as _fg

    if gate != "support-matcher":
        raise ValueError(
            f"rv manuscript judge-emit: unknown gate {gate!r} — only "
            f"'support-matcher' is supported (the cold-read gate was "
            f"removed; see DEVLOG)."
        )

    cfg = config or load_config()
    project_notes_dir = cfg.project_notes_dir(project)
    tree_root = _manuscript_tree_root(project, slug, cfg)
    note_path = tree_root / "_manuscript.md"

    if not note_path.exists():
        raise FileNotFoundError(
            f"rv manuscript judge-emit: {note_path} not found. "
            f"Run `rv manuscript {project} new {slug} --type <type>` first."
        )

    out: dict[str, Any] = {
        "support-matcher": _fg.emit_support_tasks_to_dir(
            _judge_dir(tree_root, "support-matcher"),
            tree_root,
            notes_root=project_notes_dir,
            manuscript_slug=slug,
            literature_root=cfg.literature_root,
        ),
    }
    return out


def cmd_judge_ingest(
    project: str,
    slug: str,
    *,
    config: Config | None = None,
    gate: str = "support-matcher",
) -> dict[str, Any]:
    """Ingest ``_judge-verdicts.json`` for the NG-4 fan-out (
    Phase C) — ``rv manuscript <project> judge-ingest <slug>``.

    Reads whatever the hub wrote to
    ``manuscripts/<slug>/judge/support-matcher/_judge-verdicts.json`` and
    assembles the ingest result. Does NOT raise on a canary abort — that
    exception is caught here and folded into the return dict
    (``canary_aborted``, ``halt``) so the CLI wrapper can print it loudly
    rather than crash; ``rv dag approve`` (via ``build_approve_payload``)
    is the actual gate that BLOCKs on this — this verb is a
    diagnostic/dry-run surface. Support-matcher-ONLY — see
    ``cmd_judge_emit``'s docstring for the cold-read removal rationale.

    Returns ``{"support-matcher": {...}}``.

    """
    from research_vault.manuscript import fidelity_gates as _fg
    from research_vault.gates.judge_seam import CanaryAbortError

    if gate != "support-matcher":
        raise ValueError(
            f"rv manuscript judge-ingest: unknown gate {gate!r} — only "
            f"'support-matcher' is supported (the cold-read gate was "
            f"removed; see DEVLOG)."
        )

    cfg = config or load_config()
    tree_root = _manuscript_tree_root(project, slug, cfg)

    out: dict[str, Any] = {}
    try:
        out["support-matcher"] = _fg.ingest_support_verdicts_from_dir(
            _judge_dir(tree_root, "support-matcher"), tree_root=tree_root,
        )
    except CanaryAbortError as e:
        out["support-matcher"] = {
            "errors": [str(e)], "warnings": [], "canary_aborted": True,
            "halt": True, "halt_reason": str(e), "missing_ids": [],
            "unrecognized_ids": [], "k_block": 0, "j_warn": 0,
            "honest_report": "CANARY ABORTED",
        }
    return out


def cmd_board_emit(
    project: str,
    slug: str,
    *,
    config: Config | None = None,
    round: int = 1,  # noqa: A002 - matches emit_board_tasks' field name
) -> dict[str, Any]:
    """★ the 6-lens board's production emit driver — the missing
    call site ``compute_coverage_diff`` never had (pinned the
    source-routing CONTRACT as a unit regression; this is the driver that
    actually exercises it in production).

    Writes ``manuscripts/<slug>/judge/board/_board-tasks.json`` +
    ``_board-canary-key.json`` (``gates.board_seam.emit_board_tasks_to_dir``,
    ). rv calls NO LLM here — the hub fans cold subagent-judges out
    over the written tasks; run ``rv manuscript <project> board-ingest
    <slug>`` (or ``build_approve_payload``'s board consumption) once
    ``_board-verdicts.json`` lands.

    ★ SOURCE-ROUTING (non-negotiable): ``reader_body`` is
    assembled via ``check_gates._read_draft_text`` — the ``[[citekey]]``
    SOURCE (``_report.md`` + ``sections/*.md``), NEVER ``[N]``-
    numbered render (``report.md``). Feeding the render here would make
    ``WIKILINK_CITE_RE`` find zero citekeys, so ``compute_coverage_diff``
    would flag EVERY committed ``used`` paper as "missing" and
    false-critical the entire corpus (see ``compute_coverage_diff``'s
    docstring). ``test_pr_d2_source_routing_driver.py`` asserts this at
    the DRIVER level — the unit floor
    (``test_coverage_diff_source_routing``) alone cannot catch a
    mispointed call site.

    Args:
        project: project slug.
        slug: manuscript identifier.
        config: optional Config (loaded if None).
        round: the board round number (default 1 — the first-round emit;
            re-fire with a higher round number for the revise loop).

    Returns:
        ``{"tasks_doc": ..., "canary_key_doc": ..., "coverage_diff": ...}``
        — ``coverage_diff`` is surfaced separately (not just embedded in
        the WIDTH task) so a caller/test can assert on it directly without
        digging through ``tasks_doc["tasks"]``.

    """
    from research_vault.gates.board_seam import emit_board_tasks_to_dir
    from research_vault.manuscript.check_gates import _read_draft_text, compute_coverage_diff

    cfg = config or load_config()
    project_notes_dir = cfg.project_notes_dir(project)
    tree_root = _manuscript_tree_root(project, slug, cfg)
    note_path = tree_root / "_manuscript.md"

    if not note_path.exists():
        raise FileNotFoundError(
            f"rv manuscript board-emit: {note_path} not found. "
            f"Run `rv manuscript {project} new {slug} --type <type>` first."
        )

    # ★ The load-bearing line: the SOURCE, never the render (see docstring).
    reader_body = _read_draft_text(tree_root)

    coverage_map_path = tree_root / "_coverage-map.md"
    coverage_diff = compute_coverage_diff(coverage_map_path, reader_body)

    judge_dir = _judge_dir(tree_root, "board")
    emitted = emit_board_tasks_to_dir(
        judge_dir,
        reader_body,
        manuscript=slug,
        round=round,
        coverage_diff=coverage_diff,
    )
    emitted["coverage_diff"] = coverage_diff
    return emitted


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
