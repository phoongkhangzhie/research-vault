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

★ Resumable / log-as-you-go (2026-07-09 — the operator's ask: log as you go
so a walk is resumable and not lost if the process gets dropped): a walk
over 20+ seeds x several rounds is many minutes of wall clock and hundreds
of API calls; a kill at minute 40 of 45 used to lose the entire walk (all
state lived only in memory, artifacts written only at the very end). When
``checkpoint_path`` is passed, ROUND-GRANULARITY state (visited-set,
frontier, accumulated hits, round records, consecutive-zero counter) is
persisted to that path after every completed round; a re-invocation with
the SAME path + same seeds/backstop detects the checkpoint and RESUMES
from the next round — the already-visited papers are never re-fetched
(the round loop simply starts later; see ``_load_checkpoint``). On clean
completion (any ``stop_reason``) the checkpoint file is removed. Omitting
``checkpoint_path`` (or a fresh run with no prior checkpoint on disk)
behaves exactly as before this feature — fully backward compatible.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .base import AdapterFetchError, NotSupported, PaperHit, SourceAdapter
from .dedup import DedupedHit, dedup_hits, identity_key
from .derivative import count_independent, mark_derivatives

DEFAULT_BACKSTOP_WAVES = 2

# Breadth x depth bounds (2026-07-09 — a broad-topic downstream-project validation walk ran
# unbounded for 1+ hour: per_round_limit only capped fetches PER PAPER, never
# the number of frontier papers, so the walk grew O(per_round_limit^waves).
# These three, plus backstop_waves=2 above, are the closed set of knobs that
# bound total work: seed_cap bounds the STARTING width, frontier_cap bounds
# each round's re-seeding width, fetch_budget is the hard backstop-of-
# backstops on total asta calls regardless of waves/width.
DEFAULT_SEED_CAP = 25
DEFAULT_FRONTIER_CAP = 25
DEFAULT_FETCH_BUDGET = 200

# Bump if the on-disk checkpoint shape ever changes incompatibly — a
# mismatched version is treated exactly like "no checkpoint" (start fresh),
# never a crash on an old/foreign file (charter §5: reversible, never trust
# a stale/foreign artifact blindly). Bumped 1->2 for the fetch-budget
# addition (total_calls must be resumed, not reset to 0 — a pre-existing
# checkpoint from before this feature can't supply it, so it's dropped and
# the walk restarts fresh, same as any other incompatible-shape checkpoint).
_CHECKPOINT_VERSION = 2

# Every key the resume path reads directly off a loaded checkpoint dict. A
# checkpoint missing ANY of these (truncated write, hand-edited, a foreign
# file that happens to parse as JSON) must be treated as absent/corrupt —
# i.e. a fresh start — never a KeyError crash (charter §5: same "never trust
# a stale/foreign artifact blindly" reversibility this module already
# applies to the version/seed_ids/backstop_waves mismatch case).
_REQUIRED_CHECKPOINT_KEYS = (
    "seen_identities", "visited_pids", "all_hits", "errors", "rounds",
    "unresolvable_ids", "unresolvable_seen", "frontier", "consecutive_zero",
    "completed_round", "total_calls",
)


def _default_progress(msg: str) -> None:
    """Default progress sink — stderr (keeps stdout clean for any caller
    that parses this process's stdout; mirrors the CLI's own
    ``print(..., file=sys.stderr)`` convention elsewhere in this codebase)."""
    print(msg, file=sys.stderr, flush=True)


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON atomically (tmp file + ``os.replace``) — a kill mid-write
    must never leave a half-written, corrupt checkpoint on disk.

    All adapters today put a JSON-serializable ``dict``/``list``/scalar into
    ``PaperHit.raw``, but a future adapter storing something else (a custom
    object, a set, ``bytes``) must not crash the whole walk at end-of-round —
    that would be strictly WORSE than not having checkpointing at all. On a
    ``json.dumps`` failure the checkpoint write is skipped (loudly, to
    stderr) and the walk continues in-memory-only for this round; the next
    round tries again (transient/self-healing if a later round's state is
    serializable)."""
    try:
        text = json.dumps(data)
    except (TypeError, ValueError) as e:
        print(
            "snowball: checkpoint write skipped this round — state is not "
            f"JSON-serializable ({type(e).__name__}: {e}); walk continues "
            "in-memory (this round's progress is not persisted)",
            file=sys.stderr,
        )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _load_checkpoint(path: Path) -> dict[str, Any] | None:
    """Best-effort checkpoint load. A missing, unreadable, or corrupt file
    is treated as "no checkpoint" — never a hard crash (the walk always has
    a safe fresh-start fallback)."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


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


def _paper_id_of(external_ids: dict[str, str]) -> str | None:
    """Best-available id to re-seed the frontier with for the NEXT round —
    prefer DOI, then arXiv, then OpenAlex, then the adapter's own id (e.g.
    S2 corpus id).

    Takes the MERGED ``external_ids`` off a ``DedupedHit`` (``d.external_ids``)
    — mirrors ``sweep._paper_id_of_hit``'s fix (2026-07-09 a downstream project's live-e2e-run
    finding): a bare ``hit.external_ids`` is only the first-seen
    representative's own ids, a strict subset of what ``dedup_hits`` merged
    across every duplicate that collapsed onto this identity.
    """
    return (
        external_ids.get("doi")
        or external_ids.get("arxiv")
        or external_ids.get("openalex")
        or external_ids.get("s2")
    )


def run_snowball_to_saturation(
    seed_ids: list[str],
    *,
    adapter: SourceAdapter | None = None,
    backstop_waves: int = DEFAULT_BACKSTOP_WAVES,
    derivative_threshold: float = 0.6,
    per_round_limit: int = 20,
    seed_cap: int = DEFAULT_SEED_CAP,
    frontier_cap: int = DEFAULT_FRONTIER_CAP,
    fetch_budget: int = DEFAULT_FETCH_BUDGET,
    checkpoint_path: Path | str | None = None,
    progress_cb: Callable[[str], None] | None = None,
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
        seed_cap: hard cap on the STARTING frontier width. No ``PaperHit``
            (hence no relevance score) exists yet at the seed stage — seeds
            are bare ids off ``_screen.md`` — so the ranking here is the
            declared fallback: preserve input order, keep the first
            ``seed_cap``. A no-op when ``len(seed_ids) <= seed_cap``.
        frontier_cap: each round, after discovering ``new_frontier_ids``,
            only the top ``frontier_cap`` (ranked by ``citation_count``
            desc, stable tie-break on discovery order) are PROMOTED to seed
            the next round. Every discovered paper still counts toward the
            corpus (``kept``, ``rounds[].new_independent``) — capping only
            bounds which papers get to EXPAND the walk further, never drops
            a paper from the corpus (discount, never delete — the same
            discipline ``derivative_of`` already uses).
        fetch_budget: hard ceiling on total ``cited_by``/``references``
            calls across the whole walk (both directions, every round). A
            broad-topic neighborhood that never saturates and outlives even
            ``backstop_waves`` still terminates here — the backstop-of-
            backstops. Checked before each call; the walk stops as soon as
            the budget would be exceeded (never over-shoots), finishes
            processing the partial round's already-fetched hits, then sets
            ``stop_reason == f"budget:{fetch_budget}-calls"`` and returns —
            never a crash, never silently truncated without a distinct,
            non-``"saturated"`` stop reason (charter §2).
        checkpoint_path: when given, round-granularity walk state is
            persisted here after every completed round, and a PRIOR
            checkpoint at this same path (matching ``seed_ids`` +
            ``backstop_waves``) is loaded and RESUMED from — the walk
            continues from the next round rather than re-fetching anything
            already visited. The running ``total_calls`` fetch-count is
            part of that persisted state, so a resumed walk's fetch_budget
            check starts from where the killed walk left off — never
            resets to 0 (which would let a resumed walk blow past the
            budget across the resume boundary). Removed on any clean
            completion. A mismatched or corrupt checkpoint is treated as
            absent (fresh start) — never a crash. ``None`` (the default)
            disables checkpointing entirely — unchanged, backward-compatible
            in-memory-only walk.
        progress_cb: called with one human-readable line after each
            completed round (``"round N/backstop: frontier=.. new=..
            unresolvable=.. corpus=.."``) — liveness for an operator
            watching a long walk. Defaults to printing to stderr.

    Returns:
        A ``SnowballResult`` whose ``stop_reason`` is exactly ``"saturated"``
        (2 consecutive rounds with 0 new independent papers),
        ``f"backstop:{backstop_waves}-waves"``,
        ``f"budget:{fetch_budget}-calls"`` (the total-fetch ceiling fired —
        a bounded, non-saturated corpus), or ``"no-seeds-resolved"`` (every
        seed id failed to resolve on BOTH directions — an all-seeds lookup
        failure, never mislabeled as genuine saturation; see below) — never
        anything else, and never left blank (charter §2). Like
        ``"backstop:N-waves"``, ``"budget:N-calls"`` does NOT start with
        ``"backstop:"`` so ``is_backstop`` is False for it — the
        coverage-gate whitelist (``review.autonomy.classify_coverage_gate``)
        therefore fail-closes on it (HALT-DECLARE) exactly like any other
        non-canonical, non-``"saturated"`` value; it is not wired into the
        ``GO_WITH_RESIDUE`` backstop branch (SR-175 confirmed unchanged).

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

    progress = progress_cb or _default_progress
    ckpt_file = Path(checkpoint_path) if checkpoint_path else None

    seed_ids = [s for s in seed_ids if s]

    # Seed cap (breadth bound, §1): no PaperHit/relevance score exists yet at
    # the seed stage (bare ids off _screen.md) — the declared fallback is
    # input-order preservation, first `seed_cap` kept. Applied BEFORE the
    # checkpoint match check, so a resumed walk's `seed_ids` comparison is
    # against the SAME (already-capped) set the original run started with.
    if len(seed_ids) > seed_cap:
        seed_ids = seed_ids[:seed_cap]

    start_round = 1
    loaded = _load_checkpoint(ckpt_file) if ckpt_file is not None else None
    if loaded is not None and (
        loaded.get("version") != _CHECKPOINT_VERSION
        or set(loaded.get("seed_ids", [])) != set(seed_ids)
        or loaded.get("backstop_waves") != backstop_waves
        or loaded.get("seed_cap") != seed_cap
        or loaded.get("frontier_cap") != frontier_cap
        or loaded.get("fetch_budget") != fetch_budget
    ):
        progress(
            "snowball: checkpoint present but does not match this walk's "
            "seed_ids/backstop_waves/seed_cap/frontier_cap/fetch_budget — "
            "ignoring it, starting fresh"
        )
        loaded = None

    if loaded is not None and any(k not in loaded for k in _REQUIRED_CHECKPOINT_KEYS):
        progress(
            "snowball: checkpoint present but missing required fields "
            "(truncated or foreign file) — ignoring it, starting fresh"
        )
        loaded = None

    if loaded is not None:
        seen_identities = set(loaded["seen_identities"])
        visited_pids = set(loaded["visited_pids"])
        all_hits = [PaperHit(**h) for h in loaded["all_hits"]]
        errors = list(loaded["errors"])
        rounds = [SnowballRoundRecord(**r) for r in loaded["rounds"]]
        unresolvable_ids = list(loaded["unresolvable_ids"])
        _unresolvable_seen = set(loaded["unresolvable_seen"])
        frontier = list(loaded["frontier"])
        consecutive_zero = loaded["consecutive_zero"]
        total_calls = loaded["total_calls"]
        start_round = loaded["completed_round"] + 1
        progress(
            f"snowball: resuming from checkpoint after round "
            f"{loaded['completed_round']}/{backstop_waves} "
            f"(cumulative so far: {len(all_hits)} hits, "
            f"{total_calls}/{fetch_budget} asta calls used)"
        )
    else:
        seen_identities = set()
        visited_pids = set(seed_ids)
        all_hits = []
        errors = []
        rounds = []
        unresolvable_ids = []
        _unresolvable_seen = set()
        frontier = list(seed_ids)
        consecutive_zero = 0
        total_calls = 0

    stop_reason = ""

    for round_num in range(start_round, backstop_waves + 1):
        round_frontier_size = len(frontier)
        round_hits: list[PaperHit] = []
        directions_by_identity: dict[str, set[str]] = {}
        budget_exhausted = False

        for pid in frontier:
            if total_calls >= fetch_budget:
                budget_exhausted = True
                break

            asta_id = _normalize_paper_id_for_asta(pid)
            fwd_failed = False
            total_calls += 1
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

            if total_calls >= fetch_budget:
                # Budget hit exactly on the forward call — skip the backward
                # call for THIS pid (never overshoot the ceiling) and stop
                # fetching entirely; already-collected round_hits are still
                # processed normally below (partial round, never dropped).
                budget_exhausted = True
                break

            bwd_failed = False
            total_calls += 1
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
        # (pid, citation_count) pairs — capped to `frontier_cap` (§2, ranked
        # citation_count desc, stable tie-break on discovery order) AFTER
        # this loop, below. Every discovered paper still lands in
        # `new_this_round`/`all_hits` regardless of the cap — capping only
        # bounds which papers seed the NEXT round's frontier.
        new_frontier_candidates: list[tuple[str, int]] = []
        new_fwd = 0
        new_bwd = 0
        for d in deduped_round:
            ident = identity_key(d.hit)
            pid = _paper_id_of(d.external_ids)
            if ident in seen_identities or (pid and pid in visited_pids):
                continue
            seen_identities.add(ident)
            if pid:
                visited_pids.add(pid)
                new_frontier_candidates.append((pid, d.hit.citation_count))
            new_this_round.append(d.hit)
            dirs = directions_by_identity.get(ident, set())
            if "forward" in dirs:
                new_fwd += 1
            if "backward" in dirs:
                new_bwd += 1

        # Frontier cap (breadth bound, §2): sort DESC by citation_count;
        # Python's sort is stable, so ties preserve discovery order. Cap to
        # `frontier_cap` — the rest are still kept in `all_hits`/`kept`
        # above, they just don't expand the walk further.
        new_frontier_candidates.sort(key=lambda pair: pair[1], reverse=True)
        new_frontier_ids = [pid for pid, _ in new_frontier_candidates[:frontier_cap]]

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

        progress(
            f"round {round_num}/{backstop_waves}: frontier={round_frontier_size}, "
            f"new={independent_new}, unresolvable={len(unresolvable_ids)}, "
            f"corpus={cumulative_independent}"
        )

        if consecutive_zero >= 2:
            stop_reason = "saturated"
            break

        if budget_exhausted:
            # Total-fetch ceiling (backstop-of-backstops, §3): a bounded,
            # NOT-saturated corpus — distinct from both "saturated" and
            # "backstop:N-waves" so the coverage-gate whitelist fail-closes
            # on it (never mislabeled as convergence).
            stop_reason = f"budget:{fetch_budget}-calls"
            break

        frontier = new_frontier_ids

        # Log-as-you-go (round-granularity checkpoint): persist everything
        # needed to resume from the NEXT round without re-fetching anything
        # already visited. Written after the round is fully processed (never
        # mid-round) — a kill anywhere in round N+1's fetch loop resumes
        # cleanly at round N+1, re-doing at most the in-flight round.
        if ckpt_file is not None:
            _atomic_write_json(ckpt_file, {
                "version": _CHECKPOINT_VERSION,
                "seed_ids": seed_ids,
                "backstop_waves": backstop_waves,
                "seed_cap": seed_cap,
                "frontier_cap": frontier_cap,
                "fetch_budget": fetch_budget,
                "total_calls": total_calls,
                "completed_round": round_num,
                "frontier": frontier,
                "consecutive_zero": consecutive_zero,
                "visited_pids": sorted(visited_pids),
                "seen_identities": sorted(seen_identities),
                "unresolvable_seen": sorted(_unresolvable_seen),
                "unresolvable_ids": unresolvable_ids,
                "errors": errors,
                "rounds": [asdict(r) for r in rounds],
                "all_hits": [asdict(h) for h in all_hits],
            })

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

    # Clean completion (any stop_reason) — the checkpoint's job is done;
    # remove it so a future re-run of this same seed set starts fresh
    # rather than "resuming" a walk that already finished.
    if ckpt_file is not None:
        try:
            ckpt_file.unlink(missing_ok=True)
            ckpt_file.with_suffix(ckpt_file.suffix + ".tmp").unlink(missing_ok=True)
        except OSError:
            pass

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
    external_ids: dict[str, str] | None = None,
    notes_index: dict[str, str] | None,
    notes_title_index: dict[str, list[tuple[str, str]]] | None,
) -> str:
    """[NEW] / [IN-CORPUS:<citekey>] annotation — mirrors
    ``sweep._annotate_hit`` exactly (same bridge to ``_corpus_annotation``,
    the mechanical corpus-index check; charter §6, do not reinvent).

    ``external_ids`` is the caller's MERGED ids (``d.external_ids``) when
    available — same fix as ``_paper_id_of``. Defaults to
    ``hit.external_ids`` for back-compat."""
    from research_vault.research import _corpus_annotation  # avoid import cycle

    ids = external_ids if external_ids is not None else hit.external_ids
    paper = {
        "externalIds": {
            "DOI": ids.get("doi"),
            "ArXiv": ids.get("arxiv"),
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
        annotation = _annotate_hit(
            hit, external_ids=d.external_ids,
            notes_index=notes_index, notes_title_index=notes_title_index,
        )
        pid = _paper_id_of(d.external_ids) or ""
        flags: list[str] = []
        if not pid:
            flags.append("[NO-ID: cannot resolve doi/arxiv/openalex/s2 — needs manual id lookup]")
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
