# SPDX-License-Identifier: AGPL-3.0-or-later
"""review/remediation.py — NG-6a piece 2: the autonomous, bounded
coverage-gap remediation loop.

Design of record: docs/superpowers/specs/2026-07-08-ng6a-refresh-autonomous-remediation.md
(§4, §5). Extends the coverage-gate disposition (``review.autonomy.classify_coverage_gate``,
already wired into ``dag/verbs.py`` via #185's
``classify_coverage_gate_with_deviation_check``) with a REMEDIATE decision —
gated on backstop (frontier open), never on genuine saturation (§4.1's
composition rule: a corpus that hit the primary 2-consecutive-zero rule is
exhausted, more waves find nothing; a gap under real saturation needs a
criteria change, which is a human decision, never auto-remediation).

The anti-fishing spine (§5) is mechanical on three independent layers, ALL
enforced by this module's construction, not by convention:
  1. Source restriction — ``run_remediation_round`` invokes ONLY frozen-
     protocol-keyed deterministic tool-ops (``review.autonomy.run_tool_op``,
     "sweep"). There is no code path here to inject a new seed or source.
  2. Criteria-hash pin — every round ends with ``corpus_freeze.refresh``,
     which re-verifies the criteria hash is unchanged (§2 step 3).
  3. Declared-denominator BLOCK — every round's append is declared via
     ``record_deviation(..., kind="within-criteria-append")``, whose
     invariant (``pre==post`` criteria, ``removed==[]``) means this loop
     structurally cannot self-author a criteria change or a removal.

Termination (§4.3, three independent bounds — any ONE alone guarantees the
loop cannot run forever):
  1. Zero-new saturation — a round that appends nothing stops immediately.
  2. Remediation-round cap (``review.style.get_remediation_max_rounds``).
  3. Snowball backstop — each round's tool-op calls are single-shot
     (bounded by construction, not a further internal loop).

Stdlib only (+ intra-package imports). sr: NG-6a
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from ..cite import _make_citekey
from .autonomy import (
    GO_WITH_RESIDUE,
    HALT_DECLARE,
    REMEDIATE,
    DispositionResult,
    classify_coverage_gate_with_deviation_check,
    record_deviation,
    run_tool_op,
)
from .corpus_freeze import hash_criteria_bytes, refresh, stamp_corpus_freeze
from .style import get_remediation_max_rounds

# An independent hard cap on the outer resolve<->remediate loop, distinct
# from `remediation_max_rounds` (which bounds remediation ROUNDS specifically
# — this bounds the outer disposition-resolution loop itself, a defence-in-
# depth backstop in case a future disposition wiring bug ever made rounds_used
# fail to increment; should be unreachable under a correctly-configured
# max_rounds).
_OUTER_LOOP_GUARD = 10


# ---------------------------------------------------------------------------
# The REMEDIATE decision (§4.1) — pure, unit-testable
# ---------------------------------------------------------------------------

def resolve_coverage_gate(
    base: DispositionResult,
    saturation_info: dict[str, Any],
    *,
    remediation_state: dict[str, Any] | None = None,
    max_rounds: int | None = None,
) -> DispositionResult:
    """Extend a ``classify_coverage_gate``-shaped ``base`` disposition with
    the REMEDIATE decision (§4.1's composition table).

    - HALT-DECLARE / GO -> unchanged (nothing to remediate: either fatally
      malformed, or already fully saturated-and-clean).
    - GO-WITH-RESIDUE, but NOT backstop-terminated -> unchanged (defensive;
      ``classify_coverage_gate`` only ever returns GO-WITH-RESIDUE for a
      backstop-terminated saturation record, but this function stays honest
      about the precondition rather than assuming it).
    - GO-WITH-RESIDUE, backstop-terminated:
        - remediation budget remaining AND the last wave found something new
          (or no round has run yet) -> REMEDIATE.
        - budget exhausted OR the last wave found zero-new -> unchanged
          (GO-WITH-RESIDUE — declare residue, the honest "can't close this
          without a criteria change" outcome).

    ``remediation_state`` is the ``run_state.meta["remediation_state"]``
    dict (``{"rounds_used": int, "last_wave_added_count": int | None}``);
    ``None``/absent means "no round has run yet" (first evaluation).
    """
    if base.disposition != GO_WITH_RESIDUE:
        return base
    if not saturation_info.get("is_backstop"):
        return base

    rs = remediation_state or {}
    rounds_used = int(rs.get("rounds_used", 0))
    cap = max_rounds if max_rounds is not None else get_remediation_max_rounds()
    last_added = rs.get("last_wave_added_count")  # None | int

    budget_remaining = rounds_used < cap
    last_wave_found_new = last_added is None or last_added > 0

    if budget_remaining and last_wave_found_new:
        return DispositionResult(
            REMEDIATE,
            f"backstop-terminated (open frontier), remediation budget "
            f"remaining ({rounds_used}/{cap} rounds used) and the last wave "
            f"found new in-scope papers (last_wave_added_count={last_added!r})"
            " — dispatch one bounded within-criteria remediation round.",
            {"rounds_used": rounds_used, "max_rounds": cap, "stop_reason": saturation_info.get("stop_reason")},
        )
    reason = (
        "remediation budget exhausted"
        if not budget_remaining
        else "the last remediation wave found zero new in-scope papers "
        "(frozen protocol exhausted)"
    )
    return DispositionResult(
        GO_WITH_RESIDUE,
        f"backstop-terminated but {reason} — declaring residue "
        "(closing this gap needs a criteria change, a human decision, not "
        "auto-remediation).",
        {**base.evidence, "rounds_used": rounds_used, "max_rounds": cap},
    )


# ---------------------------------------------------------------------------
# One bounded remediation round (§4.2) — all reuse, no new discovery machinery
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
    corpus-file-local — §4.2 step 2) and append ``[NEW]`` rows in the
    recognized schema (NG-6a §3's one recognized row shape). Returns the
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


def run_remediation_round(
    run_state_meta: dict[str, Any],
    *,
    protocol_path: Path,
    corpus_path: Path,
    deviations_path: Path,
    config: Any = None,
    tool_op_fn: Callable[..., Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Execute ONE bounded remediation round (§4.2, steps 1-4). Mutates
    ``run_state_meta`` in place: ``remediation_state`` (rounds_used,
    last_wave_added_count), and — on a non-empty round —
    ``corpus_freeze``/``frozen_corpus_citekeys`` via ``corpus_freeze.refresh``.

    Returns a summary dict: ``{"round": int, "added": [...], "stopped": str | None}``.
    ``stopped == "zero-new"`` means the round found nothing (bound 1, §4.3) —
    the caller's disposition-resolution loop will see
    ``last_wave_added_count == 0`` on its next ``resolve_coverage_gate`` call
    and correctly decline to REMEDIATE again.

    ``tool_op_fn`` is injectable (``None`` resolves to the module-global
    ``run_tool_op`` at CALL time, not at function-definition time — a
    late-bound default rather than a bound-once default arg, so
    ``monkeypatch.setattr(review.remediation, "run_tool_op", fake)`` works
    even though ``dag/verbs.py``'s real wiring never passes ``tool_op_fn``
    explicitly). Mirrors the existing op-registry's own test seams (charter
    §6).
    """
    if tool_op_fn is None:
        tool_op_fn = run_tool_op
    rs_state = run_state_meta.setdefault(
        "remediation_state",
        {"rounds_used": 0, "last_wave_added_count": None},
    )

    baseline = run_state_meta.get("corpus_freeze")
    if baseline is None:
        baseline = stamp_corpus_freeze(
            run_state_meta, corpus_path=corpus_path, protocol_path=protocol_path, now=now,
        )

    from . import _parse_corpus_citekeys  # lazy — module-load-cycle safety

    existing_citekeys = set(_parse_corpus_citekeys(corpus_path))

    # 1. Search more, within frozen criteria — deterministic tool-op only,
    #    frozen-protocol-keyed (§5 layer 1: no agent node, no new seeds).
    hits: list[Any] = []
    try:
        sweep_result = tool_op_fn("sweep", protocol=str(protocol_path))
        hits = _extract_hits(sweep_result)
    except Exception:
        # A sweep failure degrades this round to "found nothing new" —
        # never crashes the remediation loop (charter §2: surface via the
        # zero-new stop, not via an uncaught exception that would look like
        # a HALT-DECLARE-worthy integrity failure).
        hits = []

    # 2. Dedup + annotate + append (§4.2 step 2).
    added = _append_new_corpus_rows(corpus_path, hits, existing_citekeys)

    rs_state["rounds_used"] = int(rs_state.get("rounds_used", 0)) + 1
    rs_state["last_wave_added_count"] = len(added)

    if not added:
        return {"round": rs_state["rounds_used"], "added": [], "stopped": "zero-new"}

    # 3. Declare the denominator growth (§4.2 step 3, §5 layer 2/3). The
    #    criteria snapshot is the SAME string for pre/post — trivially
    #    satisfies the within-criteria-append invariant (this loop cannot
    #    author a criteria edit; it never even constructs two different
    #    criteria strings).
    criteria_snapshot = hash_criteria_bytes(protocol_path)
    record_deviation(
        deviations_path,
        version=baseline["version"] + 1,
        pre_criteria=criteria_snapshot,
        post_criteria=criteria_snapshot,
        removed=[],
        added=added,
        rationale=(
            "autonomous within-criteria remediation wave; frozen protocol "
            "re-run (sweep) to close a coverage gap on a backstop-"
            "terminated (open frontier) corpus."
        ),
        kind="within-criteria-append",
    )

    # 4. Refresh — bumps corpus_freeze + keeps frozen_corpus_citekeys in
    #    sync (§2), so the next coverage-gate evaluation reads the
    #    refreshed set instead of re-tripping the undeclared-delta BLOCK.
    refresh(
        run_state_meta,
        corpus_path=corpus_path,
        protocol_path=protocol_path,
        deviations_path=deviations_path,
        now=now,
    )

    return {"round": rs_state["rounds_used"], "added": added, "stopped": None}


# ---------------------------------------------------------------------------
# The bounded outer loop (§4.4 wiring target: dag/verbs.py's coverage-gate
# --auto branch calls this once `resolve_coverage_gate` first returns
# REMEDIATE)
# ---------------------------------------------------------------------------

def run_bounded_remediation(
    run_state_meta: dict[str, Any],
    initial: DispositionResult,
    saturation_info: dict[str, Any],
    *,
    protocol_path: Path,
    corpus_path: Path,
    deviations_path: Path,
    coverage_gaps_path: Path | None = None,
    config: Any = None,
    tool_op_fn: Callable[..., Any] | None = None,
    max_rounds: int | None = None,
) -> DispositionResult:
    """Drive the resolve -> remediate -> re-resolve cycle to a non-REMEDIATE
    disposition (§4.3, §4.4). Every iteration runs exactly one bounded
    round; the loop terminates the moment ``resolve_coverage_gate`` stops
    returning REMEDIATE (zero-new, round-cap, or non-backstop/HALT).

    ``tool_op_fn=None`` late-binds to the module-global ``run_tool_op`` at
    call time (see ``run_remediation_round``'s docstring for why this
    matters for monkeypatching).

    ``_OUTER_LOOP_GUARD`` is a defence-in-depth backstop, not the primary
    bound — the primary bound is ``remediation_state["rounds_used"]`` vs
    ``max_rounds`` inside ``resolve_coverage_gate`` itself (§4.3 bound 2).
    """
    if tool_op_fn is None:
        tool_op_fn = run_tool_op
    disposition = initial
    for _ in range(_OUTER_LOOP_GUARD):
        if disposition.disposition != REMEDIATE:
            return disposition

        run_remediation_round(
            run_state_meta,
            protocol_path=protocol_path,
            corpus_path=corpus_path,
            deviations_path=deviations_path,
            config=config,
            tool_op_fn=tool_op_fn,
        )

        base = classify_coverage_gate_with_deviation_check(
            run_state_meta,
            saturation_info,
            corpus_path=corpus_path,
            deviations_path=deviations_path,
            coverage_gaps_path=coverage_gaps_path,
        )
        if base.disposition == HALT_DECLARE:
            return base

        disposition = resolve_coverage_gate(
            base,
            saturation_info,
            remediation_state=run_state_meta.get("remediation_state"),
            max_rounds=max_rounds,
        )

    # Unreachable under a correctly-configured max_rounds (bound 2 always
    # fires first) — fail-closed rather than silently looping forever if it
    # somehow is reached (a defence-in-depth backstop, §4.3).
    return DispositionResult(
        HALT_DECLARE,
        "remediation outer-loop guard exhausted — this should be "
        "unreachable under a correctly-configured remediation_max_rounds; "
        "fail-closed rather than loop unboundedly.",
        {},
    )
