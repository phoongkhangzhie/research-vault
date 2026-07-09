# SPDX-License-Identifier: AGPL-3.0-or-later
"""sources/sweep.py — the parallel width-sweep orchestrator (NG-3, §4.2/§4.3).

Reads the FROZEN angle matrix + sources list from ``_protocol.md`` (frozen at
``approve-protocol`` — a mid-run change to either is a criteria deviation,
never silently honored here: this module only READS what was frozen, it
never writes/widens it), runs the cross-product ``(angle-query × source-
adapter)`` concurrently under the fetch budget, then composes:

  fetch (parallel)  →  dedup (NG-2)  →  derivative-of discount (NG-9)
                    →  6-dim utility rank + saturation-paired floor (NG-3)
                    →  corpus annotation ([NEW] / [IN-CORPUS:<citekey>])

An adapter that fails or raises ``NotSupported`` for a given op is skipped
for that (angle, source) cell — never treated as a fatal sweep failure
(graceful degradation, §10 risk: "an adapter down must degrade gracefully").
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from .base import NotSupported, PaperHit, SourceAdapter
from .dedup import DedupedHit, dedup_hits, identity_key
from .derivative import count_independent, mark_derivatives
from .ranker import UtilityScore, rank_and_select, score_hit
from .registry import DEFAULT_SOURCES, get_adapter

DEFAULT_FETCH_BUDGET = 65  # HR's validated "diminishing returns beyond ~80" range (D4)


# ---------------------------------------------------------------------------
# Frozen-protocol parsing — a local, honest fork (not note._parse_frontmatter):
# the angle matrix is a flat NESTED mapping under `seed_queries:`, a shape the
# canonical parser does not support (it handles scalar-list and mapping-LIST,
# not a bare mapping under one key) — see engineer memory "Parser extension
# STOP decision". Extending the shared parser needs a full-caller audit; this
# module owns its narrow, documented need instead.
# ---------------------------------------------------------------------------

_KV_RE = re.compile(r"^(\w[\w_-]*):\s*(.*)$")


def parse_angle_matrix(protocol_text: str) -> dict[str, str]:
    """Parse the ``seed_queries:`` angle matrix out of a ``_protocol.md``
    frontmatter block.

    Expected shape::

        seed_queries:
          by-method:     "<query>"
          by-outcome:    "<query>"
          by-paradigm:   "<query>"
          by-population: "<query>"

    Returns ``{}`` if ``seed_queries:`` is absent or not in this nested-
    mapping shape (e.g. the legacy flat-list form) — callers must treat an
    empty return as "no angle matrix; fall back to legacy handling", never
    crash.
    """
    if not protocol_text.startswith("---"):
        return {}
    end = protocol_text.find("\n---", 3)
    if end == -1:
        return {}
    fm_block = protocol_text[3:end]

    lines = fm_block.splitlines()
    out: dict[str, str] = {}
    in_block = False
    for line in lines:
        if line.strip() == "" :
            continue
        if not line.startswith((" ", "\t")):
            # top-level key line
            in_block = line.strip().rstrip(":") == "seed_queries" and line.rstrip().endswith(":")
            continue
        if not in_block:
            continue
        stripped = line.strip()
        m = _KV_RE.match(stripped)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val.startswith(("'", '"')) and val.endswith(val[0]) and len(val) >= 2:
            val = val[1:-1]
        out[key] = val
    return out


def parse_sources(protocol_text: str) -> list[str]:
    """Parse the ``sources: [a, b, c]`` inline-list field. Falls back to
    ``DEFAULT_SOURCES`` (D4) if absent."""
    m = re.search(r"^sources:\s*\[(.*?)\]\s*$", protocol_text, re.MULTILINE)
    if not m:
        return list(DEFAULT_SOURCES)
    raw = m.group(1)
    names = [n.strip().strip("'\"") for n in raw.split(",")]
    return [n for n in names if n]


# ---------------------------------------------------------------------------
# Parallel fetch
# ---------------------------------------------------------------------------

@dataclass
class SweepCell:
    angle: str
    query: str
    source: str
    hits: list[PaperHit] = field(default_factory=list)
    error: str | None = None


def _fetch_cell(angle: str, query: str, source: str, *, limit: int) -> SweepCell:
    try:
        adapter: SourceAdapter = get_adapter(source)
    except ValueError as e:
        return SweepCell(angle=angle, query=query, source=source, error=str(e))
    try:
        hits = adapter.search(query, limit=limit)
        return SweepCell(angle=angle, query=query, source=source, hits=hits)
    except NotSupported as e:
        return SweepCell(angle=angle, query=query, source=source, error=str(e))
    except Exception as e:  # noqa: BLE001 — an adapter failure degrades the cell, not the sweep
        return SweepCell(angle=angle, query=query, source=source, error=f"{type(e).__name__}: {e}")


def run_width_sweep(
    angle_matrix: dict[str, str],
    sources: list[str],
    *,
    per_cell_limit: int = 20,
    max_workers: int = 8,
) -> list[SweepCell]:
    """Fetch the cross-product ``(angle × source)`` concurrently.

    Returns one ``SweepCell`` per (angle, source) pair, in the original
    angle-then-source enumeration order (order-preserving, so dedup's
    "first-seen wins as representative" stays deterministic across runs).
    A cell with ``error`` set contributes zero hits — the sweep degrades
    gracefully per adapter/pair, never fails wholesale (§10).
    """
    cells: list[SweepCell] = []
    jobs = [
        (angle, query, source)
        for angle, query in angle_matrix.items()
        for source in sources
    ]
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_fetch_cell, angle, query, source, limit=per_cell_limit): (angle, query, source)
            for angle, query, source in jobs
        }
        results_by_key = {}
        for fut in as_completed(futures):
            angle, query, source = futures[fut]
            results_by_key[(angle, query, source)] = fut.result()
    for angle, query, source in jobs:
        cells.append(results_by_key[(angle, query, source)])
    return cells


# ---------------------------------------------------------------------------
# Compose: dedup -> derivative discount -> rank+floor
# ---------------------------------------------------------------------------

@dataclass
class SweepResult:
    kept: list[DedupedHit]
    independent_count: int
    total_hits_fetched: int
    cells: list[SweepCell]
    errors: list[str]


def compose_sweep_result(
    cells: list[SweepCell],
    *,
    budget: int = DEFAULT_FETCH_BUDGET,
    floor: int = 3,
    derivative_threshold: float = 0.6,
) -> SweepResult:
    """Compose fetched cells into the final ranked, deduped, discounted set.

    Order: dedup (NG-2) -> derivative-of discount (NG-9, on the representative
    hit of each deduped identity) -> 6-dim utility rank + saturation-paired
    floor selection (NG-3).
    """
    all_hits: list[PaperHit] = []
    # angle provenance, keyed by normalized IDENTITY (not object id — the same
    # paper surfaced by two (angle, source) cells must accumulate onto one
    # identity before dedup collapses the duplicate PaperHit objects).
    angles_by_identity: dict[str, set[str]] = {}

    errors: list[str] = []
    for cell in cells:
        if cell.error:
            errors.append(f"{cell.angle}/{cell.source}: {cell.error}")
            continue
        for hit in cell.hits:
            all_hits.append(hit)
            angles_by_identity.setdefault(identity_key(hit), set()).add(cell.angle)

    total_fetched = len(all_hits)
    deduped = dedup_hits(all_hits)

    # NG-9: discount near-duplicate restatements (mutates hit.derivative_of).
    mark_derivatives([d.hit for d in deduped], threshold=derivative_threshold)
    independent_count = count_independent([d.hit for d in deduped])

    scores: dict[int, UtilityScore] = {}
    for d in deduped:
        angles = angles_by_identity.get(identity_key(d.hit), set())
        scores[id(d)] = score_hit(
            d,
            angle_hit_count=len(angles),
            # Stance/framing-diversity proxy: distinct angle CATEGORIES that
            # surfaced this paper (documented approximation — a full stance
            # classifier is out of scope for the fetch-time ranker; distinct
            # `coverage` (independent SOURCES) is tracked separately so the
            # two dims never collapse to the same signal for a single-source,
            # multi-angle hit).
            angle_category_count=len(angles),
            is_derivative=d.hit.derivative_of is not None,
        )

    kept = rank_and_select(deduped, budget=budget, floor=floor, scores=scores)

    return SweepResult(
        kept=kept,
        independent_count=independent_count,
        total_hits_fetched=total_fetched,
        cells=cells,
        errors=errors,
    )


def run_sweep_from_protocol(
    protocol_path: Path,
    *,
    budget: int = DEFAULT_FETCH_BUDGET,
    per_cell_limit: int = 20,
    floor: int = 3,
) -> SweepResult:
    """End-to-end: read the frozen ``_protocol.md``, parse the angle matrix +
    sources, run the parallel width-sweep, compose the ranked/deduped result.

    Raises ``ValueError`` if the protocol carries no parseable angle matrix
    (never silently sweeps zero queries)."""
    text = protocol_path.read_text(encoding="utf-8")
    angle_matrix = parse_angle_matrix(text)
    if not angle_matrix:
        raise ValueError(
            f"{protocol_path}: no `seed_queries:` angle matrix found "
            "(expected by-method/by-outcome/by-paradigm/by-population keys)"
        )
    sources = parse_sources(text)
    cells = run_width_sweep(angle_matrix, sources, per_cell_limit=per_cell_limit)
    return compose_sweep_result(cells, budget=budget, floor=floor)


# ---------------------------------------------------------------------------
# _search_hits.md rendering (review-loop-nodekind-drift-fix §4-A)
# ---------------------------------------------------------------------------

def _paper_id_of_hit(hit: PaperHit) -> str | None:
    """Best-available external identifier for a hit — DOI > arXiv > S2 id.

    Used both for the [NEW]/[IN-CORPUS] annotation lookup and as the seed
    identifier the review-screen agent hands to the review-snowball tool op.
    """
    return (
        hit.external_ids.get("doi")
        or hit.external_ids.get("arxiv")
        or hit.external_ids.get("s2")
    )


def _annotate_hit(
    hit: PaperHit,
    *,
    notes_index: dict[str, str] | None,
    notes_title_index: dict[str, list[tuple[str, str]]] | None,
) -> str:
    """[NEW] / [IN-CORPUS:<citekey>] annotation for a PaperHit.

    Bridges the PaperHit shape (normalized ``external_ids`` dict) to the
    ``_corpus_annotation`` S2-native-dict contract it was written against —
    reuse over reinvention (charter §6), not a second annotation mechanism.
    """
    from research_vault.research import _corpus_annotation  # avoid import cycle

    paper = {
        "externalIds": {
            "DOI": hit.external_ids.get("doi"),
            "ArXiv": hit.external_ids.get("arxiv"),
        },
        "title": hit.title,
        "authors": [{"name": a} for a in hit.authors],
    }
    return _corpus_annotation(paper, notes_index=notes_index, notes_title_index=notes_title_index)


def _evidence_snippet(hit: PaperHit, *, max_chars: int = 280) -> str:
    """Abstract text (or, when absent, an S2 ``tldr``) for a kept row —
    review-screen evidence enrichment (a downstream project's validation-run
    finding, 2026-07-09): the screen node was judging the seed-axis call on TITLES
    ALONE because the abstract never made it into ``_search_hits.md``, even
    though every adapter that has one already puts it on ``hit.abstract``.

    Falls back to ``hit.raw["tldr"]["text"]`` (S2-only shape) when the
    abstract is empty — never fabricates evidence when neither is present
    (an honestly-blank cell, not a placeholder string)."""
    text = (hit.abstract or "").strip()
    if not text and isinstance(hit.raw, dict):
        tldr = hit.raw.get("tldr")
        if isinstance(tldr, dict):
            text = (tldr.get("text") or "").strip()
    text = re.sub(r"\s+", " ", text)
    text = text.replace("|", "/")
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def write_search_hits(
    result: SweepResult,
    out_path: Path,
    *,
    notes_index: dict[str, str] | None = None,
    notes_title_index: dict[str, list[tuple[str, str]]] | None = None,
) -> Path:
    """Render the width-sweep result to ``_search_hits.md`` (Option C §4-A).

    Per-``(angle,source)`` cell counts (including degraded/errored cells),
    the ranked deduped kept set with ``[NEW]``/``[IN-CORPUS:<citekey>]``
    annotation (mechanical, against the real corpus index — never
    reinvented), an abstract/tldr evidence snippet + venue/year (when the
    adapter carried one), and ``[DERIVATIVE-OF:*]``/``[BELOW-FLOOR:*]``
    flags.

    This is the artifact the ``review-screen`` agent node reads to apply the
    frozen protocol's inclusion/exclusion criteria and accept a seed
    frontier — the tool op writes the mechanical record, the agent judges
    it. The evidence columns exist so that judgment is made on real
    evidence (abstract, venue, year), not on titles alone.
    """
    lines: list[str] = ["# Search hits\n"]

    lines.append("## Cells\n")
    lines.append("| Angle | Source | Hits | Error |")
    lines.append("|---|---|---|---|")
    for cell in result.cells:
        err = cell.error or ""
        lines.append(f"| {cell.angle} | {cell.source} | {len(cell.hits)} | {err} |")
    lines.append("")

    lines.append(f"Total hits fetched: {result.total_hits_fetched}\n")
    lines.append(f"Independent (non-derivative) count: {result.independent_count}\n")

    if result.errors:
        lines.append("## Errors\n")
        for e in result.errors:
            lines.append(f"- {e}")
        lines.append("")

    # BELOW-FLOOR discrimination fix: a live run showed the flag firing on
    # ~100% of kept rows (zero signal — every row looked "boundary"). It's
    # only informative when it DIFFERENTIATES within the kept set: suppress
    # it entirely (never per-row-silently — always with a loud, explicit
    # note) when every row shares the same below_floor=True value across
    # more than one kept hit — that is exactly the non-discriminating case.
    total_kept = len(result.kept)
    below_count = sum(1 for d in result.kept if d.hit.below_floor)
    below_floor_suppressed = total_kept > 1 and below_count == total_kept

    lines.append("## Kept (ranked, deduped, budget-selected)\n")
    if below_floor_suppressed:
        lines.append(
            f"> Note: `[BELOW-FLOOR]` suppressed below — {below_count}/{total_kept} "
            "kept hits are below the source-independence floor this run, so "
            "the per-row flag is non-discriminating (zero signal). Treat "
            "the whole kept set as boundary-sourced; the snowball walk "
            "should chase all of it.\n"
        )
    lines.append("| Annotation | Paper-id | Title | Venue | Year | Abstract/TL;DR | Flags |")
    lines.append("|---|---|---|---|---|---|---|")
    for d in result.kept:
        hit = d.hit
        annotation = _annotate_hit(hit, notes_index=notes_index, notes_title_index=notes_title_index)
        pid = _paper_id_of_hit(hit) or ""
        flags: list[str] = []
        if hit.derivative_of is not None:
            flags.append(f"[DERIVATIVE-OF:{hit.derivative_of}]")
        if hit.below_floor and not below_floor_suppressed:
            flags.append("[BELOW-FLOOR: needs more sources]")
        title = (hit.title or "").replace("|", "/")
        venue = (hit.venue or "").replace("|", "/")
        year = str(hit.year) if hit.year is not None else ""
        evidence = _evidence_snippet(hit)
        lines.append(
            f"| {annotation} | {pid} | {title} | {venue} | {year} | {evidence} | {' '.join(flags)} |"
        )
    lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
