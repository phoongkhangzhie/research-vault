# SPDX-License-Identifier: AGPL-3.0-or-later
"""review/remediation.py — the pole-directed critic-backtrack loop (D-5a).

★ NOTE (0.3.1): the coverage-gate saturation-remediation machinery that
used to live in this module (``resolve_coverage_gate``'s REMEDIATE upgrade,
``run_remediation_round``, ``run_bounded_remediation``, the outer-loop
guard) was DELETED along with the saturation-gated snowball. The
citation-neighbor relevance walk is depth-bounded by design
(``relevance_hops``), which makes auto-re-expanding a "backstop-terminated"
corpus contradictory — depth-bounding IS the design, not a shortfall to
remediate. What remains below (the counter-position/
thin-pole critic backtrack, ``resolve_coverage_critic`` +
``run_directed_remediation_round``) is an UNRELATED mechanism — it answers
a completely different gate (``approve-review``'s L-2 counter-position
critic BLOCK), not the walk's own terminal — and is untouched by this
deletion.

The anti-fishing spine is mechanical on three independent layers, ALL
enforced by this module's construction, not by convention:
  1. Source restriction — ``run_directed_remediation_round`` invokes ONLY
     frozen-protocol-keyed deterministic tool-ops
     (``review.autonomy.run_tool_op``, "sweep"/"snowball"). There is no
     code path here to inject a new seed or source.
  2. Criteria-hash pin — every round ends with ``corpus_freeze.refresh``,
     which re-verifies the criteria hash is unchanged.
  3. Declared-denominator BLOCK — every round's append is declared via
     ``record_deviation(..., kind="within-criteria-append")``, whose
     invariant (``pre==post`` criteria, ``removed==[]``) means this loop
     structurally cannot self-author a criteria change or a removal.

Stdlib only (+ intra-package imports). sr: NG-6a / D-5a
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from ..cite import _make_citekey
from .autonomy import (
    CRITIC_BACKTRACK,
    HALT_DECLARE,
    REVISE,
    DispositionResult,
    record_deviation,
    run_tool_op,
)
from .corpus_freeze import hash_criteria_bytes, refresh, stamp_corpus_freeze
from .style import get_critic_backtrack_max_rounds


# ---------------------------------------------------------------------------
# One bounded critic-backtrack round — all reuse, no new discovery machinery
# ---------------------------------------------------------------------------

def _norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").strip().lower())


def _parse_corpus_row_titles(corpus_path: Path) -> set[str]:
    """Normalized titles of every tagged row already in ``_corpus.md`` —
    used for remediation-round self-dedup. Corpus-file-local (no
    ``literature/`` note lookup needed): the row's own title column is the
    dedup key, exactly the same table the round appends to."""
    if not corpus_path.exists():
        return set()
    text = corpus_path.read_text(encoding="utf-8")
    titles: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cols = [c.strip() for c in stripped.split("|") if c.strip()]
        if len(cols) < 3:
            continue
        if re.match(r"^\[.*\]$", cols[0]):
            titles.add(_norm_title(cols[2]))
    return titles


def _extract_hits(tool_op_result: Any) -> list[Any]:
    """Normalize either tool-op result shape into a flat ``list[PaperHit]``:
    ``sweep`` returns a ``SweepResult`` (``.kept: list[DedupedHit]``);
    ``snowball-forward``/``snowball-backward`` return ``list[PaperHit]``
    directly."""
    kept = getattr(tool_op_result, "kept", None)
    if kept is not None:
        return [dh.hit for dh in kept]
    if isinstance(tool_op_result, list):
        return tool_op_result
    return []


def _append_new_corpus_rows(
    corpus_path: Path,
    hits: list[Any],
    existing_citekeys: set[str],
) -> list[str]:
    """Dedup ``hits`` against the existing corpus (by normalized title,
    corpus-file-local step 2) and append ``[NEW]`` rows in the
    recognized schema (NG-6a one recognized row shape). Returns the
    sorted list of newly-added citekeys."""
    existing_titles = _parse_corpus_row_titles(corpus_path)
    seen_this_round: set[str] = set()
    new_rows: list[str] = []
    added_citekeys: list[str] = []
    all_citekeys = set(existing_citekeys)

    for hit in hits:
        title = getattr(hit, "title", "") or ""
        if not title:
            continue
        norm = _norm_title(title)
        if norm in existing_titles or norm in seen_this_round:
            continue
        seen_this_round.add(norm)

        authors = getattr(hit, "authors", None) or []
        family = None
        if authors:
            first = authors[0]
            if isinstance(first, str) and first.strip():
                family = first.strip().rsplit(" ", 1)[-1]
        year = str(getattr(hit, "year", "") or "")

        citekey = _make_citekey(family, title, year, all_citekeys)
        all_citekeys.add(citekey)
        added_citekeys.append(citekey)
        new_rows.append(f"| [NEW] | {citekey} | {title} |")

    if new_rows:
        if corpus_path.exists():
            text = corpus_path.read_text(encoding="utf-8")
        else:
            corpus_path.parent.mkdir(parents=True, exist_ok=True)
            text = "| annotation | citekey | title |\n|---|---|---|\n"
        if not text.endswith("\n"):
            text += "\n"
        text += "\n".join(new_rows) + "\n"
        corpus_path.write_text(text, encoding="utf-8")

    return sorted(added_citekeys)


# ---------------------------------------------------------------------------
# D-5a: the critic-BLOCK -> bounded, pole-directed backtrack
#
# Wires the previously un-wired approve-review REVISE path: a PURE
# counter-position/thin-pole critic BLOCK (review.check_coverage_critic_
# verdict's `remediation_target_expected`) with a valid `remediation_target`
# dispatches ONE bounded backtrack round that re-runs the NAMED facet's
# frozen counter queries harder — instead of sitting at "awaiting-go" for a
# human to hand-direct it (the downstream-project incident this PR replays: the stability
# pole's empty counter-side was fixed by a hand-directed round-3).
#
# A MIXED BLOCK (any PROTOCOL-DRIFT/DIRECTION-STARVED/TAG-UNDER-COUNTING
# reason present alongside a counter-position one) is untouched by this
# whole section — `remediation_target_expected` is False for that case, so
# `resolve_coverage_critic` passes `base` straight through, REVISE/HALT
# exactly as before this PR.
# ---------------------------------------------------------------------------

def resolve_coverage_critic(
    base: DispositionResult,
    critic_payload: dict[str, Any],
    *,
    remediation_state: dict[str, Any] | None = None,
    max_rounds: int | None = None,
) -> DispositionResult:
    """Extend a ``classify_disposition``-produced ``base`` (from
    ``evaluation_from_structural_payload(critic_payload)``, the SAME adapter
    ``approve-review`` already calls) with the pole-directed backtrack
    decision.

    - ``base.disposition != REVISE`` -> unchanged (GO / GO-WITH-RESIDUE /
      HALT-DECLARE pass straight through — nothing to backtrack).
    - REVISE but ``not critic_payload["remediation_target_expected"]`` ->
      unchanged (a mixed BLOCK, or a BLOCK the critic never localized to a
      single facet — stays REVISE, a human/agent revise round exactly as
      today; PROTOCOL-DRIFT/DIRECTION-STARVED are always this branch).
    - REVISE, ``remediation_target_expected``, but ``remediation_target`` is
      ``None`` (missing/incomplete ``remediation_target_*`` fields) ->
      HALT-DECLARE, fail-closed. Never guess which pole to backtrack from an
      incomplete signal.
    - REVISE, ``remediation_target_expected``, a valid ``remediation_target``,
      backtrack budget remaining, AND the last backtrack wave found
      something new (or none has run yet) -> ``CRITIC_BACKTRACK`` (dispatch
      one bounded, pole-directed round).
    - Budget exhausted OR the last backtrack wave found zero-new -> HALT-
      DECLARE. Axis 4 (counter-position) is a HARD structural gate — unlike
      the coverage-gate's saturation remediation, it cannot declare residue
      and proceed; a still-thin pole after the frozen counter-query is
      genuinely exhausted needs a CRITERIA CHANGE (the frozen counter-query
      was wrong by phrasing) — a human decision, recorded via
      ``record_deviation(..., kind="criteria-change")``, never auto-fished
      further by this loop.

    ``remediation_state`` is ``run_state.meta["critic_backtrack_state"]``
    (``{"rounds_used": int, "last_wave_added_count": int | None}``);
    ``None``/absent means "no backtrack round has run yet".
    """
    if base.disposition != REVISE:
        return base
    if not critic_payload.get("remediation_target_expected"):
        return base

    target = critic_payload.get("remediation_target")
    if not target or not all(str(target.get(k, "")).strip() for k in ("node", "pole", "directive")):
        return DispositionResult(
            HALT_DECLARE,
            "counter-position/thin-pole critic BLOCK, but no valid "
            "'remediation_target' (node/pole/directive) frontmatter fields "
            "found on _coverage-critic.md — fail-closed; a pole-directed "
            "backtrack can never guess which facet to re-run.",
            {"remediation_target": target, "blocking": list(base.evidence.get("blocking", []))},
        )

    rs = remediation_state or {}
    rounds_used = int(rs.get("rounds_used", 0))
    cap = max_rounds if max_rounds is not None else get_critic_backtrack_max_rounds()
    last_added = rs.get("last_wave_added_count")  # None | int

    budget_remaining = rounds_used < cap
    last_wave_found_new = last_added is None or last_added > 0

    if budget_remaining and last_wave_found_new:
        return DispositionResult(
            CRITIC_BACKTRACK,
            f"counter-position/thin-pole critic BLOCK on pole "
            f"{target['pole']!r} — backtrack budget remaining "
            f"({rounds_used}/{cap} rounds used) and the last backtrack wave "
            f"found new in-scope papers (last_wave_added_count={last_added!r})"
            " — dispatch one bounded, pole-directed backtrack round.",
            {"rounds_used": rounds_used, "max_rounds": cap, "remediation_target": target},
        )

    reason = (
        "critic-backtrack budget exhausted"
        if not budget_remaining
        else "the last pole-directed backtrack wave found zero new in-scope "
        "papers (frozen counter-query exhausted)"
    )
    return DispositionResult(
        HALT_DECLARE,
        f"counter-position/thin-pole critic BLOCK on pole {target['pole']!r} "
        f"but {reason} — this is a hard structural gate (axis 4), not a "
        "residue-able one; closing it needs a criteria change (a human "
        "decision — record via record_deviation(kind='criteria-change') if "
        "the frozen counter-query is genuinely wrong by phrasing), never "
        "auto-fished further by this loop.",
        {
            "rounds_used": rounds_used, "max_rounds": cap,
            "remediation_target": target,
            "blocking": list(base.evidence.get("blocking", [])),
        },
    )


# The "all registered sources" widen (D-5a: "all sources" for the backtrack,
# vs. the protocol's normal default-on subset) — imported lazily inside the
# function that uses it (module-load-cycle safety, same convention as the
# other lazy imports in this file).
def _all_registered_source_names() -> list[str]:
    from research_vault.sources.registry import ADAPTER_NAMES

    return list(ADAPTER_NAMES)


def _best_paper_id(external_ids: dict[str, str]) -> str | None:
    """Best-available external identifier — DOI > arXiv > OpenAlex > S2 id.

    Deliberately DUPLICATED (not imported) from
    ``sources.sweep._paper_id_of_hit`` — same precedent as
    ``counter_facet_guard._judge_configured``'s duplication note: a private,
    single-purpose helper reused across a module boundary is copied with a
    pointer comment rather than importing a private symbol (charter §6 is
    about not reimplementing MECHANISM; a 4-line id-priority rule is not
    worth a shared-module dependency for). MUST be called with the MERGED
    ``external_ids`` (a ``DedupedHit.external_ids``, never a bare
    ``hit.external_ids``) — see the source docstring for the enrichment
    regression this guards against.
    """
    return (
        external_ids.get("doi")
        or external_ids.get("arxiv")
        or external_ids.get("openalex")
        or external_ids.get("s2")
    )


def _extract_seed_ids_for_snowball(tool_op_result: Any) -> list[str]:
    """Best-effort seed-id extraction for the snowball re-seed step (§D-5a).

    Prefers the REAL sweep shape (``SweepResult.kept: list[DedupedHit]``,
    each carrying merged ``external_ids``). Falls back to a plain list of
    hit-like test doubles that may carry their own ``external_ids`` dict
    (test convenience — a hermetic fixture rarely bothers building a real
    ``DedupedHit`` wrapper). Never raises; an id-less hit is simply skipped
    (a seedless backtrack round degrades to "sweep only, no snowball
    widening" rather than crashing).
    """
    kept = getattr(tool_op_result, "kept", None)
    ids: list[str] = []
    if kept is not None:
        for dh in kept:
            pid = _best_paper_id(getattr(dh, "external_ids", None) or {})
            if pid:
                ids.append(pid)
        return sorted(set(ids))
    if isinstance(tool_op_result, list):
        for h in tool_op_result:
            eids = getattr(h, "external_ids", None) or {}
            pid = _best_paper_id(eids) if eids else None
            if pid:
                ids.append(pid)
    return sorted(set(ids))


class _RawRowHit:
    """Adapts a ``review.relevance.parse_corpus_raw_rows`` row dict to the
    ``.title``/``.authors``/``.year`` shape ``_append_new_corpus_rows``
    expects — the snowball op's ``_corpus_raw.md`` table has no author
    column, so ``authors`` is always empty here (``_make_citekey`` handles a
    ``None``/empty family gracefully; a slightly less specific citekey is an
    acceptable tradeoff for reusing the existing table format unchanged)."""

    def __init__(self, row: dict[str, str]):
        self.title = row.get("title", "")
        self.year = row.get("year", "")
        self.authors: list[str] = []


def _extract_snowball_hits(tool_op_result: Any) -> list[Any]:
    """Normalize the ``snowball`` tool op's result into a flat hit list, for
    ``_append_new_corpus_rows``. Handles both the REAL op's return shape
    (``{"corpus_raw": <path>, ...}`` — read back via
    ``review.relevance.parse_corpus_raw_rows``) and a hermetic test fake
    that just returns a plain hit list directly (mirrors ``_extract_hits``'s
    tolerance of either shape, same rationale)."""
    if isinstance(tool_op_result, list):
        return tool_op_result
    if isinstance(tool_op_result, dict) and tool_op_result.get("corpus_raw"):
        from .relevance import parse_corpus_raw_rows

        raw_path = Path(tool_op_result["corpus_raw"])
        if raw_path.exists():
            rows = parse_corpus_raw_rows(raw_path.read_text(encoding="utf-8"))
            return [_RawRowHit(r) for r in rows]
    return []


_RELAXED_PER_CELL_LIMIT = 40  # a backtrack round intensifies vs. the sweep op's default 20


# ---------------------------------------------------------------------------
# fix (Shape B): wiring the backtrack's newly-found counter-papers
# through ``review.incremental_relate`` module (D-5b) — previously
# built + unit-tested but UNREACHED from the running loop (zero references
# from dag/verbs.py). This section owns ONLY the plumbing: filtering
# ``added`` citekeys to those with an already-distilled
# ``literature/<citekey>.md`` note (``run_incremental_relate``'s own caller
# contract).
#
# The direct-API judge defaults originally shipped here
# (``_default_relate_fn`` calling the deleted ``gates/_llm.py`` helper)
# were doctrine-violating (deleted the direct-API judge path
# wholesale) and are DELETED, not patched. This module NEVER self-judges —
# ``relate_fn``/``escalate_relate_fn`` must be an already-resolved,
# synchronous callable (a dict-lookup over harness cold-judge fan-out
# verdicts) injected by the caller. Production callers get that resolved
# callable from ``review.relate_judge_seam`` via ``dag/verbs.py``'s
# ``approve-review`` branch (the harness emit/ingest fan-out is
# asynchronous + two-phase — it cannot live inside a synchronous per-pair
# ``relate_fn(a, b) -> edge`` default; see ``relate_judge_seam``'s module
# docstring for the full "why Shape B" reasoning). ``run_incremental_relate``
# itself (concept-graph blocking, bidirectional write, island escalation) is
# untouched — this is plumbing only.
# ---------------------------------------------------------------------------

def run_incremental_relate_for_new_citekeys(
    new_citekeys: list[str],
    *,
    literature_dir: Path,
    baseline_citekeys: set[str],
    relate_fn: Callable[[str, str], dict[str, str] | None] | None = None,
    escalate_relate_fn: Callable[[str, set[str]], list[dict[str, str]]] | None = None,
) -> dict[str, Any]:
    """The wiring layer between a remediation/backtrack round's ``added``
    citekeys and ``review.incremental_relate.run_incremental_relate``.

    Filters ``new_citekeys`` to those with an already-existing
    ``literature/<citekey>.md`` note (``run_incremental_relate``'s own
    caller contract — full-distill happens upstream/out-of-band). A
    corpus-row-only citekey with no distilled note yet is surfaced in
    ``not_yet_distilled`` — never silently dropped, never crashed on
    (charter §2).

    ``relate_fn``/``escalate_relate_fn`` are passed straight through to
    ``run_incremental_relate`` — NEVER resolved to a live-API default here.
    A caller with nothing to relate (every ``new_citekeys`` entry not yet
    distilled) never even reaches the requirement (``run_incremental_relate``
    is only called when ``ready`` is non-empty); a caller WITH something to
    relate but ``relate_fn=None`` gets ``run_incremental_relate``'s own
    fail-closed ``RuntimeError`` (this module never self-judges fix,
    Shape B).

    Returns ``{"result": IncrementalRelateResult | None, "not_yet_distilled":
    [...]}``. ``result`` is ``None`` only when EVERY new citekey lacks a
    distilled note (nothing to relate this round).
    """
    from .incremental_relate import run_incremental_relate

    ready = [ck for ck in new_citekeys if (literature_dir / f"{ck}.md").exists()]
    not_yet_distilled = [ck for ck in new_citekeys if ck not in ready]

    if not ready:
        return {"result": None, "not_yet_distilled": not_yet_distilled}

    result = run_incremental_relate(
        ready, literature_dir=literature_dir, baseline_citekeys=baseline_citekeys,
        relate_fn=relate_fn, escalate_relate_fn=escalate_relate_fn,
    )
    return {"result": result, "not_yet_distilled": not_yet_distilled}


def run_directed_remediation_round(
    run_state_meta: dict[str, Any],
    *,
    pole: str,
    protocol_path: Path,
    corpus_path: Path,
    deviations_path: Path,
    out_dir: Path,
    config: Any = None,
    tool_op_fn: Callable[..., Any] | None = None,
    literature_dir: Path | None = None,
    relate_fn: Callable[[str, str], dict[str, str] | None] | None = None,
    escalate_relate_fn: Callable[[str, set[str]], list[dict[str, str]]] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Execute ONE bounded, POLE-DIRECTED critic-backtrack round (§D-5a).

    Re-executes the FROZEN counter-query named by ``pole`` HARDER: every
    registered source (not just the protocol's declared subset) and a
    relaxed per-cell fetch limit, then re-seeds a snowball citation-chase
    from whatever thin counter-hits the harder sweep turns up. Mirrors
    ``run_remediation_round``'s anti-fishing spine exactly (same three
    layers): ONLY the frozen-protocol-keyed ``sweep``/``snowball`` tool ops
    are invoked; ``pole`` SELECTS an existing angle-matrix key
    (``<pole>.counter.N`` — the facet's ALREADY-frozen counter queries), it
    never authors a new one — there is no code path here that can inject a
    new query.

    on a non-empty round, the newly-added counter-papers are ALSO
    flowed through ``run_incremental_relate_for_new_citekeys`` (concept-
    graph-blocked candidate generation, bidirectional edge write, island
    escalation — see that function + ``incremental_relate.py``). This is
    the wiring this PR closes: previously the round appended corpus rows
    only, and ``review.incremental_relate`` was never reached from here.
    ``literature_dir`` defaults to the standard ``project_notes_dir/
    literature`` layout derived from ``corpus_path`` (which lives at
    ``project_notes_dir/reviews/<scope>/_corpus.md``) when not given.

    Returns ``{"round": int, "added": [...], "stopped": str | None,
    "related": {...} | None}`` — ``related`` (new in this PR) is the dict
    ``run_incremental_relate_for_new_citekeys`` returns, or ``None`` when
    the round found zero-new (nothing to relate).
    """
    if tool_op_fn is None:
        tool_op_fn = run_tool_op
    rs_state = run_state_meta.setdefault(
        "critic_backtrack_state",
        {"rounds_used": 0, "last_wave_added_count": None},
    )

    baseline = run_state_meta.get("corpus_freeze")
    if baseline is None:
        baseline = stamp_corpus_freeze(
            run_state_meta, corpus_path=corpus_path, protocol_path=protocol_path, now=now,
        )

    from . import _parse_corpus_citekeys  # lazy — module-load-cycle safety

    existing_citekeys = set(_parse_corpus_citekeys(corpus_path))

    # 1. Re-sweep the named pole's frozen counter queries, HARDER (all
    #    sources, relaxed per-cell limit) — frozen-protocol-keyed, no new
    #    query injected (layer 1).
    hits: list[Any] = []
    seed_ids: list[str] = []
    try:
        sweep_result = tool_op_fn(
            "sweep",
            protocol=str(protocol_path),
            angle_keys={f"{pole}.counter"},
            sources_override=_all_registered_source_names(),
            per_cell_limit=_RELAXED_PER_CELL_LIMIT,
        )
        hits = _extract_hits(sweep_result)
        seed_ids = _extract_seed_ids_for_snowball(sweep_result)
    except Exception:
        # Same "degrade to zero-new, never crash" posture as
        # run_remediation_round — a backtrack round failure is surfaced via
        # the zero-new stop, never an uncaught exception.
        hits = []
        seed_ids = []

    # 2. Re-seed a snowball citation-chase from those thin counter-hits — a
    #    direct-query result this thin often sits one citation-hop from the
    #    real refuting sub-literature. Reuses the SAME `snowball` op
    #    unchanged (via its `seed_ids` bypass, layer 1 — still only the
    #    frozen-protocol-keyed ops, no new discovery mechanism).
    if seed_ids:
        try:
            snow_result = tool_op_fn(
                "snowball", seed_ids=seed_ids, out_dir=str(out_dir),
            )
            hits.extend(_extract_snowball_hits(snow_result))
        except Exception:
            pass  # the sweep's own hits still count; snowball widening is best-effort

    # 3. Dedup + annotate + append (mirrors run_remediation_round step 2).
    added = _append_new_corpus_rows(corpus_path, hits, existing_citekeys)

    rs_state["rounds_used"] = int(rs_state.get("rounds_used", 0)) + 1
    rs_state["last_wave_added_count"] = len(added)

    if not added:
        return {"round": rs_state["rounds_used"], "added": [], "stopped": "zero-new"}

    # 4. Declare the denominator growth — SAME within-criteria-append kind
    #    as the saturation-remediation loop; a frozen-facet re-sweep+
    #    snowball never authors a criteria edit (layers 2/3).
    criteria_snapshot = hash_criteria_bytes(protocol_path)
    record_deviation(
        deviations_path,
        version=baseline["version"] + 1,
        pre_criteria=criteria_snapshot,
        post_criteria=criteria_snapshot,
        removed=[],
        added=added,
        rationale=(
            f"autonomous pole-directed critic-backtrack round for pole "
            f"{pole!r}; frozen counter-query re-run harder (all registered "
            "sources, relaxed per-cell limit) + a snowball citation-chase "
            "re-seed, to close a counter-position/thin-pole coverage-critic "
            "BLOCK."
        ),
        kind="within-criteria-append",
    )

    refresh(
        run_state_meta, corpus_path=corpus_path, protocol_path=protocol_path,
        deviations_path=deviations_path, now=now,
    )

    # flow the newly-found counter-papers through the concept-graph
    # -blocked incremental relate (never re-fans-out over the whole corpus,
    # never re-relates the existing baseline — ``existing_citekeys`` here is
    # the baseline BEFORE this round's additions, matching "against already-
    # distilled EXISTING notes" per incremental_relate.py's contract).
    lit_dir = literature_dir if literature_dir is not None else corpus_path.parent.parent / "literature"
    related = run_incremental_relate_for_new_citekeys(
        added, literature_dir=lit_dir, baseline_citekeys=existing_citekeys,
        relate_fn=relate_fn, escalate_relate_fn=escalate_relate_fn,
    )

    return {"round": rs_state["rounds_used"], "added": added, "stopped": None, "related": related}
