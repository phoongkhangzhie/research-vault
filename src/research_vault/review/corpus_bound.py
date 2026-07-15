# SPDX-License-Identifier: AGPL-3.0-or-later
"""review/corpus_bound.py — Section C (lit-review search-primary redesign,
task #86): bound the curated corpus to ``corpus_bound`` (~100) papers via
DETERMINISTIC, stratified selection — never a flat top-N, never an LLM.

Principle 2: "a survey is ~100 well-chosen papers, not an exhaustive net."

Strength signal (the build-decision, resolved — do not re-open): the
deterministic composite total order IS the spine —
``(verdict tier: IN > UNCERTAIN) -> (#facet-poles matched, more = stronger)
-> (sweep rank, lower = stronger)`` — with the A1 rerank score used ONLY as
a tiebreaker for rows that remain tied after that full triple, and only for
rows that carry a real (non-``None``) score. The core ranking never depends
on rerank (most rows lack one today — see ``sources.base.PaperHit.
rerank_score``'s docstring).

Stratification (largest-remainder quota, not flat top-N):
  (a) every DECLARED pole gets its floor ``K`` (``min_hits_per_pole``) if
      it has >= K IN-verdict papers;
  (b) the remaining ``N - sum(floors)`` is allocated PROPORTIONALLY to
      each pole's remaining IN-pool size, rounded by the largest-remainder
      (Hamilton) method — never flat top-N;
  (c) a pole with < K IN-papers contributes ALL it has and is never
      padded with fabricated/borrowed rows — that is the thin-pole
      finding (ties to Section E), never hidden here.

Pole-bucket assignment: a candidate matching MULTIPLE declared poles is
bucketed under its PRIMARY pole (the alphabetically-first pole it matched)
for FLOOR/PROPORTIONAL quota purposes — a deliberate simplification that
keeps the quota math over disjoint partitions (a largest-remainder quota
over overlapping pools has no single well-defined semantics). The paper's
FULL pole count still counts toward its composite STRENGTH (more poles
matched ranks it higher within its bucket) — the two roles (bucket
assignment vs strength signal) are intentionally decoupled. Documented
here, not silently chosen — see ``_primary_pole``.

Protected stratum (#59): never drop the last IN-paper grounding a live
concept/MOC region — pinned BEFORE the quota, counted INSIDE the bound.
``find_forward_referenced_citekeys`` is a best-effort, CONSERVATIVE
approximation (see its own docstring for the exact scoping caveat this
class carries — a true "sole grounding paper" traversal is not cheaply
determinable at curate time for a NOT-YET-MATERIALIZED [NEW] candidate,
since the concept<->literature edge for a brand-new paper is authored
during the Phase-2 relate fan-out, which runs AFTER this bound).

No randomness anywhere in this module — reproducibility (same inputs ->
same corpus) is a hard requirement.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

IN = "IN"
UNCERTAIN = "UNCERTAIN"
OFF_DOMAIN = "OFF_DOMAIN"

# Verdict-tier ordering: lower number sorts first (stronger). A verdict this
# module does not recognize (missing, or something other than IN/UNCERTAIN
# — an OFF_DOMAIN row should already have been pruned upstream by
# ``review.relevance.prune_off_domain_from_corpus``, so a survivor still
# carrying it here is itself a signal) sinks to the bottom tier — never
# silently treated as equal to a real IN/UNCERTAIN verdict.
_VERDICT_TIER: dict[str, int] = {IN: 0, UNCERTAIN: 1}
_UNKNOWN_TIER = 2


@dataclass(frozen=True)
class CorpusRow:
    """One [NEW] candidate row's per-row signals for the composite order +
    stratified quota. Built by ``rows_from_corpus_md`` from the real
    ``_corpus.md`` table + the relevance-verify verdict set; every field
    used directly in tests below is also independently constructible for
    unit coverage of the pure selection algorithm.
    """

    citekey: str
    verdict: str
    poles: frozenset[str]
    sweep_rank: int
    rerank: float | None = None


@dataclass
class SelectionResult:
    """The stratified selection's full, auditable output — never just a
    bare citekey list (charter §2: surface what didn't make the cut, not
    just what did).
    """

    selected: list[CorpusRow]
    dropped: list[CorpusRow]
    thin_poles: list[str]
    pole_floor_counts: dict[str, int]
    pole_proportional_counts: dict[str, int]
    floor_exceeded_bound: bool = False


def _verdict_tier(verdict: str) -> int:
    return _VERDICT_TIER.get(verdict, _UNKNOWN_TIER)


def _composite_key(row: CorpusRow) -> tuple[int, int, int]:
    """``(verdict tier, -#poles, sweep rank)`` — the composite spine.
    Lower sorts first (stronger). Never reads ``row.rerank`` — the
    tiebreaker is applied at a strictly lower priority, see
    ``_full_sort_key``.
    """
    return (_verdict_tier(row.verdict), -len(row.poles), row.sweep_rank)


def _full_sort_key(row: CorpusRow) -> tuple[Any, ...]:
    """The composite key PLUS the rerank tiebreaker (only reached when the
    composite triple is an exact tie) PLUS a final citekey tiebreak for
    total determinism (two rows tied on everything else, including rerank,
    must still sort in one stable, reproducible order — never insertion-
    order-dependent).
    """
    has_score = row.rerank is not None
    # A scored row sorts before an unscored one on a true tie (0 < 1);
    # among scored rows, a HIGHER score sorts first (negate for ascending
    # sort). An unscored row's rerank component is a fixed 0.0 placeholder
    # — never read when has_score is False (the has_score flag already
    # ordered it after every scored row).
    rerank_component = -row.rerank if has_score else 0.0
    return (_composite_key(row), 0 if has_score else 1, rerank_component, row.citekey)


def sort_by_composite(rows: list[CorpusRow]) -> list[CorpusRow]:
    """Deterministic total order over *rows* — the composite spine (tier,
    poles, sweep rank), the A1 rerank score as a tiebreaker ONLY for rows
    tied on that full triple, and finally citekey for total determinism.
    Never depends on input order (verified by ``TestDeterminism``).
    """
    return sorted(rows, key=_full_sort_key)


def _primary_pole(row: CorpusRow) -> str | None:
    """The alphabetically-first pole a row matched — its stratification
    BUCKET (see the module docstring's "Pole-bucket assignment" section
    for why a disjoint-primary-pole simplification was chosen over a
    multi-pole-credit quota). ``None`` for a row matching no declared pole
    at all (an "unassigned" candidate — still eligible for the proportional
    fill, just outside any pole's guaranteed floor).
    """
    return min(row.poles) if row.poles else None


_UNASSIGNED_BUCKET = "￿_unassigned"  # sorts last, never collides with a real pole key


def _largest_remainder_allocation(
    remaining_budget: int, pool_sizes: dict[str, int],
) -> dict[str, int]:
    """Hamilton largest-remainder apportionment of *remaining_budget*
    across *pool_sizes*, each allocation CAPPED at that bucket's own pool
    size (a bucket can never be allocated more seats than it has real
    candidates for — the cap-then-redistribute loop below hands any
    seats freed by a capped-out bucket to the next-largest-remainder
    bucket still under its cap).

    Deterministic: ties in the fractional remainder break on the bucket
    key (sorted ascending) — never insertion order, never randomness.
    """
    allocation: dict[str, int] = {k: 0 for k in pool_sizes}
    total_pool = sum(pool_sizes.values())
    if remaining_budget <= 0 or total_pool == 0:
        return allocation

    active = {k: v for k, v in pool_sizes.items() if v > 0}
    budget_left = min(remaining_budget, total_pool)

    while budget_left > 0 and active:
        total_active_pool = sum(active.values())
        shares: dict[str, tuple[int, float]] = {}
        for k, pool in active.items():
            raw_share = budget_left * pool / total_active_pool
            floor_share = int(raw_share)
            # Never allocate more than the pool has, even at the floor step.
            floor_share = min(floor_share, pool - allocation[k])
            shares[k] = (floor_share, raw_share - int(raw_share))

        for k, (floor_share, _frac) in shares.items():
            allocation[k] += floor_share
            budget_left -= floor_share

        # Distribute leftover seats (rounding remainder) by largest
        # fractional remainder, deterministic tie-break on bucket key.
        if budget_left > 0:
            ranked = sorted(
                active.keys(), key=lambda k: (-shares[k][1], k),
            )
            progressed = False
            for k in ranked:
                if budget_left <= 0:
                    break
                if allocation[k] < active[k]:
                    allocation[k] += 1
                    budget_left -= 1
                    progressed = True
            if not progressed:
                break

        # Drop buckets that are now fully capped out; recompute with what's
        # left so freed seats (from a bucket smaller than its raw share)
        # correctly redistribute to still-active buckets.
        active = {k: v for k, v in active.items() if allocation[k] < v}

    return allocation


def select_bounded_corpus(
    rows: list[CorpusRow],
    *,
    corpus_bound: int,
    min_hits_per_pole: int,
    pinned_citekeys: frozenset[str] = frozenset(),
) -> SelectionResult:
    """The stratified largest-remainder selection — Section C's core.

    Order of operations (mirrors the design doc exactly):
      1. Pin protected citekeys (they count INSIDE the bound).
      2. Guarantee every declared pole its floor K, if it has >= K
         IN-verdict rows (across the WHOLE candidate pool for that pole,
         not just what's left after pins — a pin already counts toward
         satisfying its own pole's floor).
      3. Allocate the remaining budget proportionally (largest-remainder)
         across pole pools (+ the "unassigned" bucket for IN rows matching
         no declared pole) by their REMAINING (not-yet-selected) IN-pool
         size.
      4. Fill any leftover budget (rounding slack, or a pool undersized
         relative to its raw share) with the best remaining rows by
         composite order, regardless of verdict/pole — this is what lets
         a genuinely strong UNCERTAIN row still make the cut when nothing
         else is competing for the seat.

    A pole whose TOTAL IN-pool (pins + floor + proportional candidates,
    before any cut) is < ``min_hits_per_pole`` is recorded in
    ``thin_poles`` and never padded (c) — every real candidate for it is
    still selected via the floor step's "contributes all it has" clause
    (the floor step selects ``min(len(pool), min_hits_per_pole)``).
    """
    by_citekey = {r.citekey: r for r in rows}
    valid_pins = frozenset(pinned_citekeys) & by_citekey.keys()

    selected: dict[str, CorpusRow] = {ck: by_citekey[ck] for ck in valid_pins}

    # Group IN-verdict rows by primary pole (None -> unassigned bucket).
    pole_pools: dict[str, list[CorpusRow]] = {}
    for row in rows:
        if row.verdict != IN:
            continue
        bucket = _primary_pole(row) or _UNASSIGNED_BUCKET
        pole_pools.setdefault(bucket, []).append(row)
    for bucket in pole_pools:
        pole_pools[bucket] = sort_by_composite(pole_pools[bucket])

    thin_poles = sorted(
        pole for pole, pool in pole_pools.items()
        if pole != _UNASSIGNED_BUCKET and len(pool) < min_hits_per_pole
    )

    # --- Step 2: floor guarantee, real declared poles only (never the
    # synthetic unassigned bucket — that has no "declared floor"). ---
    pole_floor_counts: dict[str, int] = {}
    for pole in sorted(k for k in pole_pools if k != _UNASSIGNED_BUCKET):
        pool = pole_pools[pole]
        target = min(len(pool), min_hits_per_pole)
        picked = 0
        for row in pool:
            if picked >= target:
                break
            if row.citekey in selected:
                # Already selected (e.g. a pin) — still counts toward the
                # floor being satisfied, but consumes no NEW seat.
                picked += 1
                continue
            selected[row.citekey] = row
            picked += 1
        pole_floor_counts[pole] = picked

    floor_exceeded_bound = len(selected) > corpus_bound

    # --- Step 3: proportional largest-remainder allocation of the
    # remaining budget across each pole's REMAINING (unselected) pool. ---
    remaining_budget = max(0, corpus_bound - len(selected))
    remaining_pools: dict[str, list[CorpusRow]] = {
        pole: [r for r in pool if r.citekey not in selected]
        for pole, pool in pole_pools.items()
    }
    pool_sizes = {pole: len(pool) for pole, pool in remaining_pools.items() if pool}
    allocation = _largest_remainder_allocation(remaining_budget, pool_sizes)

    pole_proportional_counts: dict[str, int] = {}
    for pole, count in sorted(allocation.items()):
        pool = remaining_pools.get(pole, [])
        picked_now = 0
        for row in pool:
            if picked_now >= count:
                break
            if row.citekey in selected:
                continue
            selected[row.citekey] = row
            picked_now += 1
        pole_proportional_counts[pole] = picked_now

    # --- Step 4: fill any leftover budget (rounding slack / undersized
    # pools) with the best remaining rows overall, by composite order. ---
    leftover = corpus_bound - len(selected)
    if leftover > 0:
        remaining_all = sort_by_composite(
            [r for r in rows if r.citekey not in selected]
        )
        for row in remaining_all[:leftover]:
            selected[row.citekey] = row

    dropped = [r for r in rows if r.citekey not in selected]

    selected_ordered = sort_by_composite(list(selected.values()))
    dropped_ordered = sort_by_composite(dropped)

    return SelectionResult(
        selected=selected_ordered,
        dropped=dropped_ordered,
        thin_poles=thin_poles,
        pole_floor_counts=pole_floor_counts,
        pole_proportional_counts=pole_proportional_counts,
        floor_exceeded_bound=floor_exceeded_bound,
    )


# ---------------------------------------------------------------------------
# Protected stratum (#59) — best-effort forward-reference pin
# ---------------------------------------------------------------------------

# Matches the OKF concept<->literature edge convention (see
# data/doctrine/note-conventions.md): "- [display](/literature/<citekey>.md)
# — TAG: reason". Scoped to the literature bundle specifically (not any
# arbitrary link) — the exact substring the concept-authoring convention
# always emits for a literature edge.
_LITERATURE_LINK_RE = re.compile(r"\(/literature/([^)/\s]+?)\.md\)")


def find_forward_referenced_citekeys(
    concepts_dir: Path, candidate_citekeys: set[str] | frozenset[str],
) -> set[str]:
    """Best-effort, CONSERVATIVE approximation of the #59 protected-stratum
    check: which *candidate_citekeys* are already referenced by an
    EXISTING concept note's body via the standard
    ``[display](/literature/<citekey>.md)`` edge convention.

    Scoping caveat (surfaced, not hidden): this is NOT a precise "is this
    the LAST/SOLE paper grounding that concept" traversal — it treats ANY
    forward reference as protection-worthy, which is a conservative
    superset of the exact #59 rule (it can only protect MORE papers than
    the precise rule would, never fewer — the safe direction, since the
    goal is "never silently drop a grounding paper"). A true "sole
    grounding" check would need to resolve every OTHER edge on the same
    concept and confirm none of them resolve to an existing literature
    note either — expensive, and moot for a citekey with only one edge in
    the first place (the common case this approximation actually covers).

    A more fundamental limit (also surfaced, not silently absorbed): a
    brand-new [NEW] candidate that NO existing concept note references yet
    cannot be protected here — the concept<->literature edge for a
    not-yet-materialized paper is authored during the Phase-2 relate
    fan-out, which runs AFTER this bound. This function can only protect
    a candidate some EARLIER review cycle or hand-authored concept note
    already anticipated/cited by exact citekey.

    An absent *concepts_dir* is an honest empty set (never a crash) — a
    review scope with no concept bundle yet simply has nothing to protect.
    """
    if not concepts_dir.exists() or not concepts_dir.is_dir():
        return set()

    candidates = set(candidate_citekeys)
    found: set[str] = set()
    for note_path in sorted(concepts_dir.rglob("*.md")):
        try:
            text = note_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in _LITERATURE_LINK_RE.finditer(text):
            citekey = m.group(1)
            if citekey in candidates:
                found.add(citekey)
    return found


# ---------------------------------------------------------------------------
# Mechanical wiring — read _corpus.md, apply the bound, write back
# ---------------------------------------------------------------------------

def rows_from_corpus_md(corpus_text: str, verdicts: dict[str, str]) -> list[CorpusRow]:
    """Build ``CorpusRow`` objects for every ``[NEW]`` row of a real
    ``_corpus.md`` table, joined against the cold relevance-verifier's
    structured *verdicts* (citekey -> ``"IN"``/``"UNCERTAIN"``/
    ``"OFF_DOMAIN"``).

    ``sweep_rank`` is the row's POSITIONAL INDEX in the parsed table order
    — a cheap, deterministic proxy that needs no NEW column: ``_corpus.md``
    already preserves the sweep's own ranked order (search rows keep the
    NG-3 utility rank; walk-discovered rows keep discovery order), carried
    through unchanged from ``_corpus_raw.md`` -> ``_corpus_raw_screened.md``
    -> ``_corpus.md`` (curate only concept-tags/filters, never reorders).

    A citekey the verifier's structured verdict set does NOT cover (should
    not happen — ``build_verify_input`` interleaves every ``[NEW]`` row —
    but a real pipeline can still degrade) gets the empty-string verdict,
    which ``_verdict_tier`` sinks to the bottom tier — never silently
    promoted to IN/UNCERTAIN. Callers should surface citekeys with no
    verdict coverage (see ``apply_corpus_bound``'s residue note).
    """
    from research_vault.sources.base import parse_poles_cell

    from .relevance import parse_corpus_table_with_abstract

    parsed = parse_corpus_table_with_abstract(corpus_text)
    rows: list[CorpusRow] = []
    for idx, r in enumerate(parsed):
        rerank_raw = (r.get("rerank") or "").strip()
        rerank: float | None
        try:
            rerank = float(rerank_raw) if rerank_raw and rerank_raw != "—" else None
        except ValueError:
            rerank = None
        rows.append(CorpusRow(
            citekey=r["citekey"],
            verdict=verdicts.get(r["citekey"], ""),
            poles=parse_poles_cell(r.get("poles", "")),
            sweep_rank=idx,
            rerank=rerank,
        ))
    return rows


@dataclass
class ApplyResult:
    """``apply_corpus_bound``'s full, auditable outcome — never a bare
    "N removed" count (charter §2)."""

    selection: SelectionResult
    missing_verdict_citekeys: list[str]
    rows_considered: int
    rows_removed: int


def _remove_dropped_rows(corpus_text: str, dropped_citekeys: set[str]) -> str:
    """Remove every ``_corpus.md`` table row whose citekey is in
    *dropped_citekeys* — same line-scan/rebuild pattern as
    ``review.relevance.prune_off_domain_from_corpus`` (charter §6, not a
    second re-implementation). Only rows shaped like a data row (starting
    with ``|``, at least 2 columns) are ever candidates for removal — a
    non-table line (prose, a heading) always survives untouched, and an
    ``[IN-CORPUS:*]``-tagged row is never a member of *dropped_citekeys* in
    the first place (the bound only ever considers ``[NEW]`` rows — see
    ``rows_from_corpus_md``).
    """
    if not dropped_citekeys:
        return corpus_text
    kept_lines: list[str] = []
    for line in corpus_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cols = [c.strip() for c in stripped.split("|")]
            if cols and cols[0] == "":
                cols = cols[1:]
            if cols and cols[-1] == "":
                cols = cols[:-1]
            if len(cols) >= 2 and cols[1] in dropped_citekeys:
                continue
        kept_lines.append(line)
    return "\n".join(kept_lines) + "\n"


def _write_residue_note(
    residue_path: Path,
    *,
    corpus_bound: int,
    min_hits_per_pole: int,
    selection: SelectionResult,
    missing_verdict_citekeys: list[str],
) -> None:
    """Declare the bound-driven non-selection — mirrors
    ``prune_off_domain_from_corpus``'s honest-residue convention (charter
    §2 / D2: a non-selection of a NEW candidate is a curation OUTPUT, never
    a silent autonomous removal of an already-accepted corpus member — see
    the module docstring's "Do-not-regress" framing carried from the
    design doc)."""
    lines = [
        "# Corpus-bound residue (Section C, task #86)\n",
        f"corpus_bound={corpus_bound}, min_hits_per_pole={min_hits_per_pole}\n",
        f"Selected: {len(selection.selected)} · Dropped: {len(selection.dropped)}\n",
    ]
    if selection.floor_exceeded_bound:
        lines.append(
            "> NOTE: one or more pole floors could not be satisfied within "
            "corpus_bound — the floor guarantee won (selected count exceeds "
            "corpus_bound this cycle).\n"
        )
    if selection.thin_poles:
        lines.append(f"Thin poles (< min_hits_per_pole, contributed all, not padded): {', '.join(selection.thin_poles)}\n")
    if missing_verdict_citekeys:
        lines.append(
            "> NOTE: the following [NEW] citekeys had NO relevance-verify "
            "verdict coverage and sank to the lowest composite tier as a "
            f"result: {', '.join(sorted(missing_verdict_citekeys))}\n"
        )
    lines.append("\n## Dropped (bound-driven non-selection)\n")
    if selection.dropped:
        for row in selection.dropped:
            lines.append(f"- `{row.citekey}` (verdict={row.verdict or 'MISSING'}, poles={sorted(row.poles) or '—'})")
    else:
        lines.append("(none — every [NEW] candidate fit within corpus_bound)")
    lines.append("")

    residue_path.parent.mkdir(parents=True, exist_ok=True)
    residue_path.write_text("\n".join(lines), encoding="utf-8")


def apply_corpus_bound(
    corpus_path: Path,
    *,
    verdicts: dict[str, str],
    corpus_bound: int,
    min_hits_per_pole: int,
    concepts_dir: Path | None = None,
    residue_path: Path | None = None,
) -> ApplyResult:
    """The mechanical Section-C step: read the (post off-domain-prune)
    ``_corpus.md``, select the stratified bounded subset of its ``[NEW]``
    rows, remove the rest, and declare the residue. Idempotent — a
    citekey already absent (e.g. a repeat evaluation after a prior bound
    already ran) is simply not found on re-scan; running twice on an
    already-bounded corpus with the same *corpus_bound* is a safe no-op
    (the second run's IN-pool is already <= the bound).

    Only ``[NEW]`` rows are ever candidates for removal — ``[IN-CORPUS:*]``
    rows (already accepted in a prior review cycle) are untouched
    regardless of the bound, preserving the D2 invariant: this is a
    selection decision over NEW candidates competing for promotion this
    cycle, never an autonomous removal of an already-accepted corpus
    member.
    """
    text = corpus_path.read_text(encoding="utf-8") if corpus_path.exists() else ""
    rows = rows_from_corpus_md(text, verdicts)
    missing_verdict_citekeys = sorted(r.citekey for r in rows if r.verdict == "")

    pinned: frozenset[str] = frozenset()
    if concepts_dir is not None:
        pinned = frozenset(find_forward_referenced_citekeys(
            concepts_dir, {r.citekey for r in rows},
        ))

    selection = select_bounded_corpus(
        rows, corpus_bound=corpus_bound, min_hits_per_pole=min_hits_per_pole,
        pinned_citekeys=pinned,
    )

    dropped_citekeys = {r.citekey for r in selection.dropped}
    new_text = _remove_dropped_rows(text, dropped_citekeys)
    if new_text != text:
        corpus_path.write_text(new_text, encoding="utf-8")

    if residue_path is not None:
        _write_residue_note(
            residue_path,
            corpus_bound=corpus_bound, min_hits_per_pole=min_hits_per_pole,
            selection=selection, missing_verdict_citekeys=missing_verdict_citekeys,
        )

    return ApplyResult(
        selection=selection,
        missing_verdict_citekeys=missing_verdict_citekeys,
        rows_considered=len(rows),
        rows_removed=len(dropped_citekeys),
    )
