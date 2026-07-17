# SPDX-License-Identifier: AGPL-3.0-or-later
"""retrieval/map.py — the `rv map <project>` generator.

This is PR-1 of the knowledge/retrieval layer: the substrate a future
router/traversal layer will sit on. It makes NO LLM calls and does NO
traversal — it is a purely mechanical, single-writer, additive, idempotent
assembler that reads durable OKF note artifacts and emits a per-project
knowledge map, one Tier-0 index (concepts, MOCs, findings/gaps, edge-type
legend) at a time.

Follows the pattern established by ``review/ledger.py``'s
``write_corpus_ledger``: read-only over existing sources, additive (never
mutates the notes it reads), idempotent (byte-identical output for an
unchanged corpus), and fail-loud rather than silently partial — a gap is
surfaced as a ``[MAP-GAP]`` line in the rendered map AND flips the
top-level ``map_complete`` frontmatter scalar to ``false``, never a map
that reads complete while actually dropping a region.

The map spans BOTH knowledge sub-layers, per-project:
  - shared-canonical (project-independent): the project's referenced slice
    of ``concepts`` (``note.OKF_SHARED_TYPES``, ``cfg.shared_type_root``).
  - project-scoped (this project only): ``mocs``, ``findings``, ``gaps``
    (``note.OKF_PROJECT_TYPES``, ``cfg.project_notes_dir``).

Literature is deliberately NOT enumerated in Tier-0 — a project's corpus
can run to dozens/hundreds of papers, and a future traversal layer reaches
a paper from the concepts it grounds rather than the map listing every
paper up front (a Tier-0 literature listing would blow the context budget
a `rv ask` router is meant to stay inside).

**The orphan-coverage check (load-bearing).** rv's MOCs are hand-authored
(unlike, e.g., GraphRAG's algorithmic community partition), so a project's
map completeness is not structurally guaranteed — a concept this project
references but no project MOC organizes is invisible to a future global
query and would vanish from view with no signal. This module verifies,
project-scoped: every concept this project's notes reference (via a
cross-bundle ``okf:concepts/...`` typed edge, or the plain markdown-link
convention project MOCs already use for curation) belongs to at least one
of this project's own MOCs. An unorganized-but-referenced concept is an
ORPHAN, surfaced as a ``[MAP-GAP]`` line and reflected in
``map_complete: false`` — this is what converts a structural guarantee
into an explicit, checked property (charter §2: surface, never silently
drop).

**"Map scope" — the widest interpretation, by design.** Because there is
no algorithmic partition to consult, "does this project reference concept
X" is decided from real note bodies via the SAME typed-edge readers
``review/relate_check.py`` already owns as the SSOT (``parse_concept_edges``
for the intra-shared ``/concepts/<slug>.md`` form, ``parse_typed_edges``
for the cross-bundle ``okf:concepts/<path>.md`` form) — scanned over EVERY
project-scoped note (mocs/findings/gaps), not just findings. Where a
scope-detection call is ambiguous, this module always picks the WIDER
reading (more concepts counted as "referenced" -> more candidates for the
orphan check to catch) rather than the narrower one, favoring fail-loud
over fail-quiet (charter §2 in the concrete: an under-scoped map-orphan
check is strictly worse than an over-eager one, since the former silently
loses coverage and the latter merely nudges a human to add a MOC entry).

Stdlib only (+ intra-package imports).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from ..config import Config, load_config
from ..note import (
    OKF_PROJECT_TYPES,
    OKF_RESERVED_FILENAMES,
    _parse_frontmatter,
    check_description_lint,
)
from ..review.relate_check import _TAG_FAMILY, parse_concept_edges, parse_typed_edges

SCHEMA_VERSION = 1

# The project-scoped OKF types this map actually indexes at Tier-0.
# ``experiments`` and ``methodology`` are project-scoped too (OKF_PROJECT_TYPES)
# but are not part of the Tier-0 index per the design brief (only
# mocs/findings/gaps are indexed; a future tier can extend this).
_MOC_TYPE = "mocs"
_FINDINGS_GAPS_TYPES: tuple[str, ...] = ("findings", "gaps")

# Every project-scoped note type this module scans for referenced concepts
# (the WIDEST reading of "this project's notes" — every project-scoped OKF
# type, not just findings/gaps, so a concept referenced from e.g. a MOC's
# own body or a methodology note is still counted).
_SCANNED_PROJECT_TYPES: frozenset[str] = OKF_PROJECT_TYPES

# A generic markdown-link probe for "does this MOC organize concept X" —
# deliberately looser than the typed-edge grammar (`_EDGE_LINE_RE`): a MOC
# is a curated, free-form bullet list (see the shipped demo MOC convention),
# not necessarily a typed SUPPORTS/GROUNDED-IN edge. Matches EITHER the
# intra-shared `/concepts/<slug>.md` form or the cross-bundle
# `okf:concepts/<path>.md` form, anywhere in the MOC body — the widest
# "this MOC mentions concept X" reading.
_ORGANIZED_CONCEPT_RE = re.compile(
    r"\]\("
    r"(?:/concepts/(?P<i_slug>[A-Za-z0-9][A-Za-z0-9_.\-]*)\.md"
    r"|okf:concepts/(?P<x_path>[A-Za-z0-9][A-Za-z0-9_.\-/]*)\.md)"
    r"\)"
)


# ---------------------------------------------------------------------------
# Small readers
# ---------------------------------------------------------------------------

def _iter_notes(directory: Path) -> list[Path]:
    """Every ``*.md`` note directly under ``directory``, reserved filenames
    excluded, sorted for deterministic (idempotent) output."""
    if not directory.exists():
        return []
    return sorted(
        p for p in directory.glob("*.md")
        if p.name not in OKF_RESERVED_FILENAMES
    )


def _note_summary(note_path: Path) -> dict[str, Any]:
    """slug/title/description for one note, plus its own description-lint
    findings (never dropped — the caller folds these into the map's gaps)."""
    text = note_path.read_text(encoding="utf-8")
    fields, _body = _parse_frontmatter(text)
    description = str(fields.get("description") or "").strip()
    title = str(fields.get("title") or "").strip()
    return {
        "slug": note_path.stem,
        "title": title,
        "description": description,
        "lint": check_description_lint(note_path, fields),
    }


def _concept_slug_from_x_path(x_path: str) -> str:
    """Basename (no extension) of a cross-bundle concepts x_path, e.g.
    ``"sub/dir/concept-a"`` from ``x_path="sub/dir/concept-a"`` (the
    ``.md`` suffix is already stripped by the capturing regex group)."""
    return Path(x_path).stem if x_path.endswith(".md") else Path(x_path).name


def _referenced_concepts(cfg: Config, project: str) -> set[str]:
    """Every concept slug this project's notes reference, via either the
    intra-shared ``/concepts/<slug>.md`` edge form (``parse_concept_edges``)
    or the cross-bundle ``okf:concepts/<path>.md`` edge form
    (``parse_typed_edges``). Scanned over every project-scoped note type
    (the widest reading — see module docstring)."""
    referenced: set[str] = set()
    notes_dir = cfg.project_notes_dir(project)
    for note_type in sorted(_SCANNED_PROJECT_TYPES):
        for note_path in _iter_notes(notes_dir / note_type):
            text = note_path.read_text(encoding="utf-8")
            _fields, body = _parse_frontmatter(text)

            for edge in parse_concept_edges(body).edges:
                slug = str(edge.get("target") or "").strip()
                if slug:
                    referenced.add(slug)

            for edge in parse_typed_edges(body).edges:
                target = str(edge.get("target") or "")
                m = re.match(r"^okf:concepts/(?P<x_path>.+)\.md$", target)
                if m:
                    referenced.add(_concept_slug_from_x_path(m.group("x_path")))
    return referenced


def _organized_concepts(cfg: Config, project: str) -> set[str]:
    """Every concept slug organized under at least one of this project's
    MOCs — a plain markdown-link scan (see ``_ORGANIZED_CONCEPT_RE``), not
    the typed-edge grammar, since real MOCs are curated free-form bullet
    lists (see the shipped demo MOC's convention)."""
    organized: set[str] = set()
    moc_dir = cfg.project_notes_dir(project) / _MOC_TYPE
    for note_path in _iter_notes(moc_dir):
        text = note_path.read_text(encoding="utf-8")
        _fields, body = _parse_frontmatter(text)
        for m in _ORGANIZED_CONCEPT_RE.finditer(body):
            slug = m.group("i_slug") or _concept_slug_from_x_path(m.group("x_path"))
            if slug:
                organized.add(slug)
    return organized


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def generate_map(cfg: Config, project: str) -> dict[str, Any]:
    """Assemble the Tier-0 knowledge map for ``project``. Read-only over
    the project's notes + the shared concepts store — never mutates
    anything it reads.

    Returns a dict with (see the fields below for the full shape):
      ``concept_index``, ``moc_index``, ``findings_gaps_index`` — each a
      list of ``{"slug", "title", "description"}`` summaries.
      ``edge_type_legend`` — ``_TAG_FAMILY`` verbatim (the SSOT; never
      re-hardcoded here).
      ``orphan_concepts`` — sorted list of referenced-but-unorganized
      concept slugs (the honesty gate's finding).
      ``description_gaps`` — every ``[description-lint] WARN`` line this
      project's Tier-0 notes carry (surfaced, never silently dropped).
      ``map_complete`` — ``False`` iff ``orphan_concepts`` is non-empty.
      ``rendered`` — the full markdown+frontmatter text (what ``write_map``
      writes to disk).
    """
    concepts_root = cfg.shared_type_root("concepts")
    notes_dir = cfg.project_notes_dir(project)

    # Design judgment call: the concept index is scoped to concepts THIS
    # PROJECT actually references (not every concept in the shared store
    # across every project) — the same reasoning that excludes literature
    # from Tier-0: the concepts store is shared-canonical and can grow to
    # cover many unrelated projects, so enumerating it in full here would
    # defeat the per-project context-budget purpose the map exists for.
    # This also keeps the concept index and the orphan-coverage check
    # scoped to the exact same "referenced" set (one definition, not two).
    referenced = _referenced_concepts(cfg, project)
    organized = _organized_concepts(cfg, project)
    orphan_concepts = sorted(referenced - organized)

    concept_index: list[dict[str, Any]] = []
    description_gaps: list[str] = []
    for slug in sorted(referenced):
        note_path = concepts_root / f"{slug}.md"
        if not note_path.is_file():
            # A referenced concept with no materialized note at all is its
            # own honest gap — surfaced, never silently skipped.
            description_gaps.append(
                f"[description-lint] WARN: {slug}.md: concept note not found "
                f"under {concepts_root} — referenced but not materialized"
            )
            concept_index.append({"slug": slug, "title": "", "description": ""})
            continue
        summary = _note_summary(note_path)
        concept_index.append({
            "slug": summary["slug"],
            "title": summary["title"],
            "description": summary["description"],
        })
        description_gaps.extend(summary["lint"])

    moc_index: list[dict[str, Any]] = []
    for note_path in _iter_notes(notes_dir / _MOC_TYPE):
        summary = _note_summary(note_path)
        moc_index.append({
            "slug": summary["slug"],
            "title": summary["title"],
            "description": summary["description"],
        })
        description_gaps.extend(summary["lint"])

    findings_gaps_index: list[dict[str, Any]] = []
    for note_type in _FINDINGS_GAPS_TYPES:
        for note_path in _iter_notes(notes_dir / note_type):
            summary = _note_summary(note_path)
            findings_gaps_index.append({
                "slug": summary["slug"],
                "title": summary["title"],
                "description": summary["description"],
                "note_type": note_type,
            })
            description_gaps.extend(summary["lint"])

    edge_type_legend = dict(_TAG_FAMILY)
    map_complete = not orphan_concepts

    rendered = _render(
        project=project,
        concept_index=concept_index,
        moc_index=moc_index,
        findings_gaps_index=findings_gaps_index,
        edge_type_legend=edge_type_legend,
        orphan_concepts=orphan_concepts,
        description_gaps=description_gaps,
        map_complete=map_complete,
    )

    return {
        "project": project,
        "schema_version": SCHEMA_VERSION,
        "concept_index": concept_index,
        "moc_index": moc_index,
        "findings_gaps_index": findings_gaps_index,
        "edge_type_legend": edge_type_legend,
        "orphan_concepts": orphan_concepts,
        "description_gaps": description_gaps,
        "map_complete": map_complete,
        "rendered": rendered,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _fm_line(key: str, value: Any) -> str:
    if isinstance(value, bool):
        value = "true" if value else "false"
    return f"{key}: {value}"


def _render(
    *,
    project: str,
    concept_index: list[dict[str, Any]],
    moc_index: list[dict[str, Any]],
    findings_gaps_index: list[dict[str, Any]],
    edge_type_legend: dict[str, str],
    orphan_concepts: list[str],
    description_gaps: list[str],
    map_complete: bool,
) -> str:
    lines = [
        "---",
        _fm_line("type", "knowledge-map"),
        _fm_line("project", project),
        _fm_line("schema_version", SCHEMA_VERSION),
        _fm_line("map_complete", map_complete),
        _fm_line("concept_count", len(concept_index)),
        _fm_line("moc_count", len(moc_index)),
        _fm_line("findings_gaps_count", len(findings_gaps_index)),
        _fm_line("orphan_concept_count", len(orphan_concepts)),
        "---",
        "",
        f"# Knowledge map — {project}\n",
    ]

    if not map_complete:
        lines.append(
            "> [MAP-GAP] this map is INCOMPLETE — the following referenced "
            "concepts are organized under NO project MOC:\n"
        )
        for slug in orphan_concepts:
            lines.append(f"> [MAP-GAP] orphan concept: {slug}")
        lines.append("")

    if description_gaps:
        lines.append("> [MAP-GAP] the following notes have a missing/empty "
                      "`description:` field:\n")
        for gap in description_gaps:
            lines.append(f"> [MAP-GAP] {gap}")
        lines.append("")

    lines.append("## Concept index\n")
    lines.append("| Slug | Title | Description |")
    lines.append("|---|---|---|")
    if not concept_index:
        lines.append("| _(no concepts referenced by this project)_ | | |")
    for c in concept_index:
        lines.append(f"| {c['slug']} | {c['title']} | {c['description']} |")
    lines.append("")

    lines.append("## MOC index\n")
    lines.append("| Slug | Title | Description |")
    lines.append("|---|---|---|")
    if not moc_index:
        lines.append("| _(no MOCs)_ | | |")
    for m in moc_index:
        lines.append(f"| {m['slug']} | {m['title']} | {m['description']} |")
    lines.append("")

    lines.append("## Findings & gaps index\n")
    lines.append("| Type | Slug | Title | Description |")
    lines.append("|---|---|---|---|")
    if not findings_gaps_index:
        lines.append("| _(no findings/gaps)_ | | | |")
    for f in findings_gaps_index:
        lines.append(f"| {f['note_type']} | {f['slug']} | {f['title']} | {f['description']} |")
    lines.append("")

    lines.append("## Edge-type legend\n")
    lines.append("| Tag | Family |")
    lines.append("|---|---|")
    for tag in sorted(edge_type_legend):
        lines.append(f"| {tag} | {edge_type_legend[tag]} |")
    lines.append("")

    return "\n".join(lines)


def write_map(cfg: Config, project: str, *, out_path: Path | None = None) -> Path:
    """Generate and write ``<project_notes_dir>/_map.md`` (single-writer,
    additive, idempotent — re-running regenerates identically from the same
    corpus). Returns the path written."""
    m = generate_map(cfg, project)
    out = out_path or (cfg.project_notes_dir(project) / "_map.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(m["rendered"], encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(parent: "argparse._SubParsersAction | None" = None) -> argparse.ArgumentParser:
    """Build the argument parser for the `map` verb.

    When to use: `rv map <project>` to (re)generate a project's knowledge
    map — a mechanical index over its referenced concepts, MOCs, findings,
    gaps, and the edge-type vocabulary, with a fail-loud check that every
    referenced concept is organized under at least one project MOC.
    Anti-pattern: do NOT hand-maintain a knowledge map by eye — it is
    generated fresh from the note corpus every run; a stale hand-edited
    map silently drifts from the real notes.
    """
    desc = "Generate a project's knowledge map (concepts/MOCs/findings/gaps index + orphan-coverage gate)."
    if parent is not None:
        p = parent.add_parser("map", help="Generate the project knowledge map.", description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv map", description=desc)
    p.add_argument("project", help="Project slug.")
    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch the `map` verb. Returns exit code (always 0 — orphan gaps
    are surfaced, never a hard failure; the map itself IS the surface)."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv map: config error: {e}", file=sys.stderr)
        return 1

    out = write_map(cfg, args.project)
    m = generate_map(cfg, args.project)
    print(f"Knowledge map written: {out}")
    print(
        f"  concepts={len(m['concept_index'])} "
        f"mocs={len(m['moc_index'])} findings/gaps={len(m['findings_gaps_index'])} "
        f"orphans={len(m['orphan_concepts'])}"
    )
    if m["map_complete"]:
        print("  map_complete: true")
    else:
        print(f"  map_complete: false — {len(m['orphan_concepts'])} orphan concept(s), see {out}")
    return 0
