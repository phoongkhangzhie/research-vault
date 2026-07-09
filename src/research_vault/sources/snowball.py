# SPDX-License-Identifier: AGPL-3.0-or-later
"""sources/snowball.py — the both-direction, multi-round snowball-to-
saturation walk (Option C hybrid — the review-loop node-kind drift fix,
docs/superpowers/specs/2026-07-09-review-loop-nodekind-drift-fix.md §4-B).

Mirrors ``sweep.py``'s shape: fetch (both directions, each round) -> dedup
-> derivative discount -> compose. Reuses ``sources/derivative.py``
(``mark_derivatives``/``count_independent``) and ``sources/dedup.py`` — no
mechanism is reimplemented (charter §6).

★ Known, DECLARED caveat (spec §4-B — do not let this silently vanish):
the shipped ``review_snowball_tips`` prose's stop rule is "0 new
independent citekeys AND 0 new concept-tags" for 2 consecutive rounds.
Concept-tags are an LLM signal with no mechanical detector, so THIS
mechanical op stops on the CITEKEY HALF ONLY (0 new independent papers,
2 consecutive rounds). The concept-tag half of the rule is enforced
downstream by the ``review-curate`` agent node, which may flag a
"tag-under-counting / premature-plateau" residue in ``_coverage-gaps.md``
if verified concept-tags were still growing at the mechanical stop. This
is a deliberate, logged narrowing of where that half of the rule is
enforced — never a silent regression of the saturation discipline.

Stdlib only (+ intra-package imports); network access is entirely through
the injected ``adapter`` (a ``SourceAdapter``), so this module is hermetic
in tests via a fake adapter — no live network call required.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .base import AdapterFetchError, NotSupported, PaperHit, SourceAdapter
from .dedup import DedupedHit, dedup_hits, identity_key
from .derivative import count_independent, mark_derivatives

DEFAULT_BACKSTOP_WAVES = 3


@dataclass
class SnowballRoundRecord:
    """One row of the saturation curve — the ``_saturation.md`` body table."""

    round_num: int
    new_forward: int
    new_backward: int
    new_independent: int
    cumulative: int
    direction_starved: bool = False


@dataclass
class SnowballResult:
    kept: list[DedupedHit]
    rounds: list[SnowballRoundRecord]
    stop_reason: str
    seed_count: int
    errors: list[str] = field(default_factory=list)
    # 2026-07-09 live-asta validation fix: paper ids for which BOTH
    # cited_by AND references raised an error this walk — a genuinely
    # unresolvable id (e.g. a 404), never silently absorbed into "0 new
    # this round" without a trace (charter §2).
    unresolvable_ids: list[str] = field(default_factory=list)

    @property
    def is_backstop(self) -> bool:
        return self.stop_reason.lower().startswith("backstop:")


def _paper_id_of(hit: PaperHit) -> str | None:
    """Best-available id to re-seed the frontier with for the NEXT round —
    prefer DOI, then arXiv, then the adapter's own id (e.g. S2 corpus id)."""
    return (
        hit.external_ids.get("doi")
        or hit.external_ids.get("arxiv")
        or hit.external_ids.get("s2")
    )


def run_snowball_to_saturation(
    seed_ids: list[str],
    *,
    adapter: SourceAdapter | None = None,
    backstop_waves: int = DEFAULT_BACKSTOP_WAVES,
    derivative_threshold: float = 0.6,
    per_round_limit: int = 20,
) -> SnowballResult:
    """Both-direction, multi-round snowball walk to saturation (or the
    guaranteed-termination backstop).

    Args:
        seed_ids: paper IDENTIFIERS (DOI/arXiv/S2 id) resolvable by the
            adapter's ``cited_by``/``references`` — the accepted seed
            frontier ``review-screen`` hands off (NOT literature citekeys;
            no literature note exists yet at this point in the loop).
        adapter: the ``SourceAdapter`` to fan out both directions on.
            Defaults to ``SemanticScholarAdapter()`` (the only adapter with
            a citation graph in the D4 default-on set today).
        backstop_waves: the guaranteed-termination cap (SR-LR-1-BACKSTOP).
        derivative_threshold: passed through to ``mark_derivatives``.
        per_round_limit: per-(paper,direction) fetch limit each round.

    Returns:
        A ``SnowballResult`` whose ``stop_reason`` is exactly ``"saturated"``
        (2 consecutive rounds with 0 new independent papers),
        ``f"backstop:{backstop_waves}-waves"``, or ``"no-seeds-resolved"``
        (every seed id failed to resolve on BOTH directions — an all-seeds
        lookup failure, never mislabeled as genuine saturation; see below)
        — never anything else, and never left blank (charter §2).

    An adapter direction that raises ``NotSupported`` for a given paper id is
    skipped for that (paper, direction) this round — graceful degradation,
    mirroring ``sweep.py``'s per-cell degrade (§10). Any OTHER exception
    (``AdapterFetchError`` especially — a live asta 404 for one seed) is
    recorded in ``errors`` and likewise degrades only that (paper,
    direction) — it NEVER aborts the whole walk (2026-07-09 live-asta
    validation fix: a single unresolvable seed used to ``sys.exit`` the
    entire node). A ``pid`` that fails on BOTH directions is additionally
    recorded in ``unresolvable_ids`` (deduped). If EVERY original seed ends
    up unresolvable and zero hits were ever obtained, ``stop_reason`` is the
    distinct ``"no-seeds-resolved"`` — never silently reported as
    ``"saturated"`` (that would misrepresent a total lookup failure as a
    genuine, converged saturation plateau).

    Seed and frontier ids are normalized to the asta-resolvable
    scheme-prefixed form (``research.py``'s ``_normalize_paper_id_for_asta``
    — reused, not reimplemented, charter §6) immediately before each
    ``cited_by``/``references`` call: a bare arXiv id 404s on asta where the
    ``ARXIV:``-prefixed form resolves (verified live, 2026-07-09).
    """
    if adapter is None:
        from .semantic_scholar import SemanticScholarAdapter

        adapter = SemanticScholarAdapter()

    # Lazy import — avoid the research.py <-> sources.snowball import cycle
    # (research.py imports sources.semantic_scholar at module level); mirrors
    # `_annotate_hit`'s existing lazy import below.
    from research_vault.research import _normalize_paper_id_for_asta

    seed_ids = [s for s in seed_ids if s]
    seen_identities: set[str] = set()
    visited_pids: set[str] = set(seed_ids)
    all_hits: list[PaperHit] = []
    errors: list[str] = []
    rounds: list[SnowballRoundRecord] = []
    unresolvable_ids: list[str] = []
    _unresolvable_seen: set[str] = set()

    frontier = list(seed_ids)
    consecutive_zero = 0
    stop_reason = ""

    for round_num in range(1, backstop_waves + 1):
        round_hits: list[PaperHit] = []
        directions_by_identity: dict[str, set[str]] = {}

        for pid in frontier:
            asta_id = _normalize_paper_id_for_asta(pid)
            fwd_failed = False
            try:
                fwd = adapter.cited_by(asta_id, limit=per_round_limit)
            except NotSupported:
                fwd = []
            except Exception as e:  # noqa: BLE001 — degrade this (pid, dir), not the round
                errors.append(f"cited_by({pid}): {type(e).__name__}: {e}")
                fwd = []
                fwd_failed = True
            for h in fwd:
                round_hits.append(h)
                directions_by_identity.setdefault(identity_key(h), set()).add("forward")

            bwd_failed = False
            try:
                bwd = adapter.references(asta_id, limit=per_round_limit)
            except NotSupported:
                bwd = []
            except Exception as e:  # noqa: BLE001
                errors.append(f"references({pid}): {type(e).__name__}: {e}")
                bwd = []
                bwd_failed = True
            for h in bwd:
                round_hits.append(h)
                directions_by_identity.setdefault(identity_key(h), set()).add("backward")

            if fwd_failed and bwd_failed and pid not in _unresolvable_seen:
                _unresolvable_seen.add(pid)
                unresolvable_ids.append(pid)

        deduped_round = dedup_hits(round_hits)

        new_this_round: list[PaperHit] = []
        new_frontier_ids: list[str] = []
        new_fwd = 0
        new_bwd = 0
        for d in deduped_round:
            ident = identity_key(d.hit)
            pid = _paper_id_of(d.hit)
            if ident in seen_identities or (pid and pid in visited_pids):
                continue
            seen_identities.add(ident)
            if pid:
                visited_pids.add(pid)
                new_frontier_ids.append(pid)
            new_this_round.append(d.hit)
            dirs = directions_by_identity.get(ident, set())
            if "forward" in dirs:
                new_fwd += 1
            if "backward" in dirs:
                new_bwd += 1

        all_hits.extend(new_this_round)
        # Discount derivatives against the FULL accumulated history — never
        # re-derivative-check the seed set (it isn't a PaperHit list here).
        mark_derivatives(all_hits, threshold=derivative_threshold)
        cumulative_independent = count_independent(all_hits)
        independent_new = sum(1 for h in new_this_round if h.derivative_of is None)

        direction_starved = (new_fwd == 0) != (new_bwd == 0) and (new_fwd + new_bwd) > 0

        rounds.append(SnowballRoundRecord(
            round_num=round_num,
            new_forward=new_fwd,
            new_backward=new_bwd,
            new_independent=independent_new,
            cumulative=cumulative_independent,
            direction_starved=direction_starved,
        ))

        if independent_new == 0:
            consecutive_zero += 1
        else:
            consecutive_zero = 0

        if consecutive_zero >= 2:
            stop_reason = "saturated"
            break

        frontier = new_frontier_ids
        if not frontier:
            # Nothing left to crawl from — the NEXT round would fetch zero
            # from an empty frontier anyway; let the consecutive-zero count
            # keep accumulating naturally rather than special-casing here.
            continue
    else:
        stop_reason = f"backstop:{backstop_waves}-waves"

    if not stop_reason:
        # Defensive — should be unreachable (the for/else above always sets
        # it), but never leave the field blank (charter §2).
        stop_reason = f"backstop:{backstop_waves}-waves"

    # Every original seed failed to resolve on BOTH directions and zero hits
    # were ever obtained — an all-seeds lookup failure, not a genuine
    # saturation plateau (which would otherwise get mislabeled "saturated"
    # here, since 0-hits-for-2-rounds is exactly the saturation signature).
    # This must be surfaced distinctly so the coverage-gate's whitelist-only
    # check (review.autonomy.classify_coverage_gate) fails closed on it
    # instead of silently GO-ing on a corpus that never actually ran.
    if seed_ids and not all_hits and set(seed_ids) <= _unresolvable_seen:
        stop_reason = "no-seeds-resolved"

    deduped_final = dedup_hits(all_hits)
    return SnowballResult(
        kept=deduped_final,
        rounds=rounds,
        stop_reason=stop_reason,
        seed_count=len(seed_ids),
        errors=errors,
        unresolvable_ids=unresolvable_ids,
    )


# ---------------------------------------------------------------------------
# Artifact rendering — _corpus_raw.md + _saturation.md
# ---------------------------------------------------------------------------

def _annotate_hit(
    hit: PaperHit,
    *,
    notes_index: dict[str, str] | None,
    notes_title_index: dict[str, list[tuple[str, str]]] | None,
) -> str:
    """[NEW] / [IN-CORPUS:<citekey>] annotation — mirrors
    ``sweep._annotate_hit`` exactly (same bridge to ``_corpus_annotation``,
    the mechanical corpus-index check; charter §6, do not reinvent)."""
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


def write_corpus_raw(
    result: SnowballResult,
    out_path: Path,
    *,
    notes_index: dict[str, str] | None = None,
    notes_title_index: dict[str, list[tuple[str, str]]] | None = None,
) -> Path:
    """Render the RAW (pre-curation) snowball corpus to ``_corpus_raw.md``.

    This is the candidate list ``review-curate`` reads to concept-tag and
    produce the FINAL ``_corpus.md`` — the tool op writes the mechanical
    record (annotation + derivative flags), the agent adds the judgment
    layer (concept-tags, honest residue prose) on top.
    """
    lines: list[str] = ["# Corpus (raw, pre-curation)\n"]
    lines.append(f"Seed count: {result.seed_count}\n")
    lines.append(f"Stop reason: {result.stop_reason}\n")
    lines.append("| Annotation | Paper-id | Title | Flags |")
    lines.append("|---|---|---|---|")
    for d in result.kept:
        hit = d.hit
        annotation = _annotate_hit(hit, notes_index=notes_index, notes_title_index=notes_title_index)
        pid = _paper_id_of(hit) or ""
        flags: list[str] = []
        if hit.derivative_of is not None:
            flags.append(f"[DERIVATIVE-OF:{hit.derivative_of}]")
        title = (hit.title or "").replace("|", "/")
        lines.append(f"| {annotation} | {pid} | {title} | {' '.join(flags)} |")
    lines.append("")

    if result.errors:
        lines.append("## Errors\n")
        for e in result.errors:
            lines.append(f"- {e}")
        lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def write_saturation(result: SnowballResult, out_path: Path) -> Path:
    """Render the saturation curve to ``_saturation.md``.

    Stamps flat frontmatter with the REQUIRED ``stop_reason:`` field —
    exactly ``saturated``, ``backstop:N-waves``, or ``no-seeds-resolved``
    (SR-LR-1-BACKSTOP contract, ``review.check_saturation_backstop`` reads
    this verbatim) — followed by the round-by-round curve body and an
    "Unresolvable ids" count (2026-07-09 live-asta fix: surface, never
    silently drop, a seed/frontier id that 404'd — charter §2).
    """
    lines: list[str] = [
        "---",
        f"stop_reason: {result.stop_reason}",
        f"unresolvable_count: {len(result.unresolvable_ids)}",
        "---",
        "",
        "# Saturation curve",
        "",
        "| Round | New (forward) | New (backward) | New independent | Cumulative | Direction-starved |",
        "|---|---|---|---|---|---|",
    ]
    for r in result.rounds:
        starved = "DIRECTION-STARVED" if r.direction_starved else ""
        lines.append(
            f"| {r.round_num} | {r.new_forward} | {r.new_backward} | "
            f"{r.new_independent} | {r.cumulative} | {starved} |"
        )
    lines.append("")
    lines.append(f"Unresolvable ids: {len(result.unresolvable_ids)}")
    if result.unresolvable_ids:
        lines.append("")
        lines.append("## Unresolvable ids\n")
        for uid in result.unresolvable_ids:
            lines.append(f"- {uid}")
    lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
