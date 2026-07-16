# SPDX-License-Identifier: AGPL-3.0-or-later
"""review/incremental_relate.py D-5b: incremental, concept-graph-
blocked relate for a batch of newly-appended (counter-)papers.

WHY THIS EXISTS
================
A naive "relate every newcomer against the whole corpus" step is `new x N`
— fine at N=20, ruinous at N=2000. This module is the MECHANICAL half of
the incremental-relate move: it never re-full-distills or re-relates the
EXISTING corpus, and it never checks a new paper against every existing
paper — only against those already sharing >=1 CONCEPT (the post-
concept graph: each literature note's own OKF ``## Concept edges``
section is the join key). Candidate generation is therefore
sub-quadratic by construction — cost tracks the new paper's concept
NEIGHBORHOOD, not corpus size.

  - **Full-distill ONLY the new papers** — this module's caller contract:
    every ``new_citekeys`` entry MUST already have a
    ``literature/<citekey>.md`` note (the expensive full-read/distill step
    happened upstream, bounded to the new batch — never re-run on the
    existing corpus).
  - **Concept-graph blocking** — a new paper's candidates are exactly the
    baseline citekeys sharing >=1 concept slug with it. No shared concept
    -> not a candidate; the paper is never checked against the rest of the
    corpus at all.
  - **Note-level cross-linking** — a candidate is a `literature/<key>.md`
    NOTE already on disk (already distilled); the edge decision
    (``relate_fn``) reads two ALREADY-WRITTEN notes, never re-distills
    full text a second time.
  - **Bidirectional edge write** — a paper->paper edge is a fact about
    BOTH papers (central-notes model): the SAME relation is appended to
    both notes' ``## Related papers`` sections in one call
    (``append_bidirectional_edge``), not just the new note's.
  - **Island detector (safety valve)** — a newcomer with ZERO concept-graph
    candidates is never silently dropped (charter §2): it is recorded in
    ``.islands`` and, if ``escalate_relate_fn`` is given, escalated to a
    WIDER relate over the whole baseline corpus — scoped to ONLY that
    island paper, never fanning the wider relate out to every newcomer.

The RELATION JUDGMENT itself (does paper A support/contradict/extend paper
B, and why) is an LLM/agent decision in production — this module owns the
candidate generation + bidirectional write MECHANISM only, taking
``relate_fn``/``escalate_relate_fn`` as injectable judgment callables
(same seam shape as ``counter_facet_guard``'s ``judge_fn`` and
``manuscript.check_gates``'s board callables — charter §6, no new
injection convention invented).

Stdlib only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..note import _parse_frontmatter
from .relate_check import _RELATED_PAPERS_HEADING_RE, _TAG_SYMMETRY, parse_concept_edges


def _note_path(literature_dir: Path, citekey: str) -> Path:
    return literature_dir / f"{citekey}.md"


def note_concepts(note_path: Path) -> set[str]:
    """The set of concept slugs a literature note is tagged with — read
    from its own ``## Concept edges`` body section (paper->concept typed
    edges, the OKF markdown-link format). A note with no concept
    edges (Move 5's mandatory gating is deferred) or a
    note that does not exist returns an empty set, never an error — an
    empty concept set correctly means "this paper has no concept-graph
    candidates" rather than crashing the whole batch."""
    if not note_path.exists():
        return set()
    text = note_path.read_text(encoding="utf-8")
    _fields, body = _parse_frontmatter(text)
    return {e["target"] for e in parse_concept_edges(body).edges}


def build_concept_index(
    literature_dir: Path, citekeys: set[str],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Build the concept graph for a corpus subset: ``(citekey -> concepts,
    concept -> citekeys)``. The inverse index is the O(1)-per-concept
    lookup that makes candidate generation sub-quadratic — a new paper's
    candidates are ``union(concept_to_citekeys[c] for c in new_concepts)``,
    never a full scan of ``citekeys``."""
    citekey_concepts: dict[str, set[str]] = {}
    concept_citekeys: dict[str, set[str]] = {}
    for ck in citekeys:
        concepts = note_concepts(_note_path(literature_dir, ck))
        citekey_concepts[ck] = concepts
        for c in concepts:
            concept_citekeys.setdefault(c, set()).add(ck)
    return citekey_concepts, concept_citekeys


def _note_write_path(core_dir: Path | None, overlay_dir: Path, citekey: str) -> Path:
    """The path an edge write targets — ``cfg.literature_root/<citekey>.md``.

    the overlay unwind (0.3.2): literature is shared-canonical, ONE
    write root. ``core_dir``/``overlay_dir`` are kept as two parameters for
    call-site back-compat (every caller still threads both through) but now
    resolve to the SAME root when both are given; ``core_dir=None`` degrades
    to ``overlay_dir`` — the pre-unwind two-layer callers' fallback, harmless
    now that there is only one root to fall back to."""
    root = core_dir if core_dir is not None else overlay_dir
    return root / f"{citekey}.md"


def append_typed_edge(
    note_path: Path, *, display: str, target_link: str, tag: str, reason: str,
) -> None:
    """Append one OKF-conformant typed-edge line
    (``[display](target_link) — TAG: reason`` — relationship type as a
    prose token, not a link-prefix tag) to ``note_path``'s body — creating
    the ``## Related papers`` heading if absent. Round-trips through
    ``relate_check``'s unified parser (``parse_paper_relations``/
    ``parse_concept_edges``/``parse_typed_edges``, all backed by the same
    ``_EDGE_LINE_RE``) unchanged, regardless of scope.

    ``target_link`` is the ALREADY-FORMED OKF link target for whichever
    scope the caller is writing — intra-shared (``/literature/<ck>.md``,
    ``/concepts/<slug>.md``), cross-bundle (``okf:literature/<ck>.md``,
    ``okf:concepts/<slug>.md``, ``okf:datasets/<ds>.md``), within-project
    (``/experiments|findings|gaps|methodology/<id>.md``), or artifact
    (``results/runs|scores/<id>.jsonl|csv``). This is the ONE write
    mechanism the unified typed-edge model uses regardless of scope —
    every scope's presence check reads the same full-body scan
    (``relate_check._scan_edge_lines``), so a single ``## Related papers``
    heading convention is sufficient; the presence check only requires
    this heading's PRESENCE for the intra-shared paper→paper scope
    (Move 4) — every other scope's edge lines are found by the full-body
    scan regardless of which heading (if any) they sit under.

    Raises ``FileNotFoundError`` if ``note_path`` does not exist — a
    candidate/target note absent from disk is an integrity issue (the
    "dangling edge" case this module's caller contract explicitly rules
    out: every note a caller writes to must already exist on disk), never
    silently stubbed into existence.
    """
    if not note_path.exists():
        raise FileNotFoundError(
            f"incremental_relate: cannot append an edge to {note_path} — "
            "it does not exist. Every note a typed-edge writer targets "
            "(new or candidate, source or target) must already exist on disk."
        )
    text = note_path.read_text(encoding="utf-8")
    line = f"- [{display}]({target_link}) — {tag}: {reason}"
    if not text.endswith("\n"):
        text += "\n"
    if _RELATED_PAPERS_HEADING_RE.search(text):
        text += line + "\n"
    else:
        text += "\n## Related papers\n" + line + "\n"
    note_path.write_text(text, encoding="utf-8")


def append_related_papers_edge(
    note_path: Path, *, display: str, target: str, tag: str, reason: str,
) -> None:
    """Append one OKF-conformant paper->paper edge line
    (``[display](/literature/<target>.md) — TAG: reason``) to
    ``note_path``'s body. A thin literature-specific wrapper over
    ``append_typed_edge`` — kept as its own function (rather than inlined
    at every paper->paper call site) so existing callers' import surface
    and written bytes stay identical (the unified typed-edge engine's
    golden discipline — see this module's own docstring)."""
    append_typed_edge(
        note_path, display=display, target_link=f"/literature/{target}.md",
        tag=tag, reason=reason,
    )


def append_bidirectional_edge(
    literature_dir: Path,
    new_citekey: str,
    candidate_citekey: str,
    *,
    new_tag: str,
    new_reason: str,
    candidate_tag: str | None = None,
    candidate_reason: str | None = None,
    core_dir: Path | None = None,
) -> None:
    """Append a relation to BOTH notes (D-5b: a paper->paper edge is a fact
    about both papers, the central-notes model — the corpus gains
    connections without re-relating).

    DIRECTIONALITY (``relate_check._TAG_SYMMETRY``): the candidate's
    own edge direction, when the caller does not state one explicitly,
    is derived from ``new_tag``'s symmetry class — a SYMMETRIC/self-
    converse tag (``SUPPORTS``, ``CONTRADICTS``, ``PARTIAL``) mirrors with
    the SAME token; an ASYMMETRIC tag (``EXTENDS``) mirrors with its
    CONVERSE token (``FOUNDATION-FOR``) — never the same token. A caller-
    supplied ``candidate_tag``/``candidate_reason`` always overrides this
    default (e.g. a considered asymmetric pairing the caller has already
    judged). A ``new_tag`` absent from ``_TAG_SYMMETRY`` (a NEVER-
    auto-mirrored tag — ``USES``/``GROUNDED-IN``/``PRODUCED``, one-way by
    the shared-never-refs-project invariant / the artifact-target
    invariant) raises ``ValueError`` unconditionally: this function only
    ever writes the SAME paper->paper relation reciprocally, so a one-way
    tag reaching it is a caller bug, not a case to silently degrade.

    ``literature_dir`` is the dir candidate generation reads concept
    graphs from (each note's own ``## Concept edges`` section — 0.3.2
    (the overlay unwind): literature is shared-canonical, so this IS the write root too).
    ``core_dir``, when given, is where the edge is WRITTEN — the same
    shared store (``cfg.literature_root``). ``core_dir=None`` degrades to
    writing at ``literature_dir`` — back-compat only; every production
    caller (``dag/verbs.py``'s ``approve-review`` branch) passes
    ``core_dir`` explicitly.
    """
    if new_tag not in _TAG_SYMMETRY:
        raise ValueError(
            f"append_bidirectional_edge: tag {new_tag!r} is never auto-"
            "mirrored (absent from relate_check._TAG_SYMMETRY — USES/"
            "GROUNDED-IN/PRODUCED are one-way by design). This function "
            "always writes a reciprocal edge to both notes; a one-way tag "
            "must be written with a single-edge call "
            "(append_related_papers_edge) on the source note only."
        )
    new_note = _note_write_path(core_dir, literature_dir, new_citekey)
    candidate_note = _note_write_path(core_dir, literature_dir, candidate_citekey)
    append_related_papers_edge(
        new_note, display=candidate_citekey, target=candidate_citekey,
        tag=new_tag, reason=new_reason,
    )
    append_related_papers_edge(
        candidate_note, display=new_citekey, target=new_citekey,
        tag=candidate_tag if candidate_tag is not None else _TAG_SYMMETRY[new_tag],
        reason=candidate_reason if candidate_reason is not None else new_reason,
    )


def append_within_project_bidirectional_edge(
    project_notes_dir: Path,
    source_type: str,
    source_id: str,
    target_type: str,
    target_id: str,
    *,
    tag: str,
    reason: str,
    source_display: str | None = None,
    target_display: str | None = None,
) -> None:
    """Write a WITHIN-PROJECT typed edge reciprocally to both notes — the
    writer half of the unified typed-edge engine's within-project scope
    (the read/resolve side already exists; see
    ``review.check_link_resolution``'s ``within-project`` arm).
    Generalizes ``append_bidirectional_edge``'s
    bidirectional-write PATTERN (same converse-lookup mechanism, same
    refusal for never-mirrored tags) beyond the literature-only
    ``literature_dir``/``core_dir`` routing to an arbitrary within-project
    type pair — the write root is ``project_notes_dir/<type>/`` (e.g. a
    finding ``DERIVED-FROM`` an experiment writes
    ``project_notes_dir/findings/<id>.md`` and
    ``project_notes_dir/experiments/<id>.md``).

    ``source_type``/``target_type`` are within-project OKF types
    (``experiments``/``findings``/``gaps``/``methodology`` —
    ``relate_check._TARGET_RE``'s ``p_type`` group); each note is written
    at ``project_notes_dir/<type>/<id>.md``.

    DIRECTIONALITY (``relate_check._TAG_SYMMETRY``) — same rule
    ``append_bidirectional_edge`` already applies to paper->paper,
    reused here unchanged (one SSOT, no second symmetry table): a
    SYMMETRIC/self-converse tag mirrors with the SAME token; an
    ASYMMETRIC within-project tag (``DERIVED-FROM``/``ADDRESSES``/
    ``ANSWERS``) mirrors with its CONVERSE token (``SHOWS``/
    ``ADDRESSED-BY``/``ANSWERED-BY``) — never the same token. A tag
    absent from ``_TAG_SYMMETRY`` (``USES``/``GROUNDED-IN``/``PRODUCED`` —
    never auto-mirrored, one-way by design) raises ``ValueError``
    unconditionally: this function always writes a reciprocal edge to
    both notes, so a one-way tag reaching it is a caller bug, not a case
    to silently degrade — a one-way within-project/cross-bundle/artifact
    edge must be written with a single-edge call (``append_typed_edge``)
    on the source note only.
    """
    if tag not in _TAG_SYMMETRY:
        raise ValueError(
            f"append_within_project_bidirectional_edge: tag {tag!r} is "
            "never auto-mirrored (absent from relate_check._TAG_SYMMETRY — "
            "USES/GROUNDED-IN/PRODUCED are one-way by design). This "
            "function always writes a reciprocal edge to both notes; a "
            "one-way tag must be written with a single-edge call "
            "(append_typed_edge) on the source note only."
        )
    source_note = project_notes_dir / source_type / f"{source_id}.md"
    target_note = project_notes_dir / target_type / f"{target_id}.md"
    append_typed_edge(
        source_note,
        display=target_display if target_display is not None else target_id,
        target_link=f"/{target_type}/{target_id}.md",
        tag=tag, reason=reason,
    )
    append_typed_edge(
        target_note,
        display=source_display if source_display is not None else source_id,
        target_link=f"/{source_type}/{source_id}.md",
        tag=_TAG_SYMMETRY[tag],
        reason=reason,
    )


@dataclass
class IncrementalRelateResult:
    """The batch result — every field surfaced (charter §2), never a bare
    "done" signal."""

    added_edges: list[dict[str, Any]] = field(default_factory=list)
    islands: list[str] = field(default_factory=list)
    escalated: list[dict[str, Any]] = field(default_factory=list)
    candidate_pairs_checked: int = 0
    corpus_size: int = 0


def run_incremental_relate(
    new_citekeys: list[str],
    *,
    literature_dir: Path,
    baseline_citekeys: set[str],
    relate_fn: Callable[[str, str], dict[str, str] | None] | None,
    escalate_relate_fn: Callable[[str, set[str]], list[dict[str, str]]] | None = None,
    core_dir: Path | None = None,
) -> IncrementalRelateResult:
    """Concept-graph-blocked incremental relate for a batch of new papers.

    ``literature_dir`` is read for concept-graph candidate generation
    (each note's own ``## Concept edges`` section). ``core_dir``, when
    given, is where every written edge lands — 0.3.2 (the overlay unwind): literature is
    shared-canonical, so this is the SAME store (``cfg.literature_root``);
    ``core_dir=None`` degrades to writing at ``literature_dir`` (back-compat
    only — see ``append_bidirectional_edge``).

    Every entry in ``new_citekeys`` must already have a full-distilled
    ``literature/<citekey>.md`` note (D-5b: "full-distill ONLY the new
    counter-papers" happens upstream, bounded to this batch).
    ``baseline_citekeys`` is the EXISTING corpus at the start of this call —
    candidates are drawn from it only, never from other papers in the same
    new batch (a within-batch relation, if any, is out of this module's
    scope this wave).

    ``relate_fn(new_citekey, candidate_citekey) -> {"tag", "reason"} | None``
    is the judgment call — ``None`` means "no relation found", any dict
    means "write this bidirectional edge". This module owns candidate
    generation (concept-graph blocking, sub-quadratic) + the bidirectional
    write; it never judges the relation itself.

    A newcomer with ZERO concept-graph candidates is an ISLAND: recorded in
    ``.islands`` and, if ``escalate_relate_fn`` is given, escalated to a
    wider relate over the WHOLE baseline corpus — scoped to ONLY that one
    island paper (never fans out to any other newcomer, even in the same
    batch).

    ``relate_fn=None`` raises ``RuntimeError`` — fail-closed (fix,
    Shape B). This module NEVER self-judges: the judgment callable must
    already be resolved (a synchronous dict-lookup over harness-ingested
    verdicts, injected by the caller — see ``review.relate_judge_seam`` /
    ``dag/verbs.py``'s ``approve-review`` branch), never a live API default
    constructed in-process here.
    """
    if relate_fn is None:
        raise RuntimeError(
            "run_incremental_relate: relate_fn is required — this module "
            "never self-judges the paper<->paper relation. Pass an explicit "
            "callable resolved from the harness cold-judge fan-out (see "
            "review.relate_judge_seam.ingest_relate_verdicts + "
            "dag/verbs.py's approve-review branch)."
        )
    result = IncrementalRelateResult(corpus_size=len(baseline_citekeys))
    _citekey_concepts, concept_to_citekeys = build_concept_index(literature_dir, baseline_citekeys)

    for new_ck in new_citekeys:
        new_concepts = note_concepts(_note_path(literature_dir, new_ck))
        candidates: set[str] = set()
        for c in new_concepts:
            candidates |= concept_to_citekeys.get(c, set())
        candidates.discard(new_ck)

        result.candidate_pairs_checked += len(candidates)

        if not candidates:
            result.islands.append(new_ck)
            if escalate_relate_fn is not None:
                escalated_edges = escalate_relate_fn(new_ck, set(baseline_citekeys))
                for edge in escalated_edges:
                    append_bidirectional_edge(
                        literature_dir, new_ck, edge["candidate"],
                        new_tag=edge["tag"], new_reason=edge["reason"],
                        core_dir=core_dir,
                    )
                    result.added_edges.append({
                        "new": new_ck, "candidate": edge["candidate"],
                        "tag": edge["tag"], "reason": edge["reason"],
                        "escalated": True,
                    })
                result.escalated.append({"citekey": new_ck, "edges_written": len(escalated_edges)})
            continue

        for cand in sorted(candidates):
            verdict = relate_fn(new_ck, cand)
            if verdict is None:
                continue
            append_bidirectional_edge(
                literature_dir, new_ck, cand,
                new_tag=verdict["tag"], new_reason=verdict["reason"],
                core_dir=core_dir,
            )
            result.added_edges.append({
                "new": new_ck, "candidate": cand,
                "tag": verdict["tag"], "reason": verdict["reason"],
                "escalated": False,
            })

    return result
