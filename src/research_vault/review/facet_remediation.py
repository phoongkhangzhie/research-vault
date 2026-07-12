# SPDX-License-Identifier: AGPL-3.0-or-later
"""review/facet_remediation.py — 0.3.1 Layer 3: the tiered-hash facet
re-search remediation loop (the "reborn remediation" — anti-fishing-fenced).

Design of record: internal design note (operator-private, not shipped).

★ Sibling-bug avoidance (grounded in a downstream project's real
corpus-contamination diagnosis): the OLD saturation-era remediation
(``review.remediation._append_new_corpus_rows``, still used by the
UNRELATED critic-backtrack loop) appended raw sweep/snowball hits DIRECTLY
into ``_corpus.md`` as bare ``[NEW]`` rows — DOWNSTREAM of curate's
leg-classification AND the relevance-screen AND the cold final-corpus
verify. That is how 562 untagged rows and 103 off-domain citation-neighbor
papers reached a "certified" corpus: the append path bypassed every
precision gate, not just one.

This module's round driver structurally CANNOT repeat that:
  1. **Mechanical relevance screen, in-process** — every re-searched hit is
     passed through ``review.relevance.relevance_gate`` (the SAME primitive
     ``screen_corpus_raw`` uses between snowball and curate) BEFORE it is
     eligible to join the corpus. An ``OFF_DOMAIN`` hit is DECLARED into a
     residue file, never silently dropped, never appended.
  2. **Never a bare ``[NEW]`` row** — a screened-IN/UNCERTAIN hit is
     appended tagged ``[NEW][NEEDS-CURATE]`` (see ``_NEEDS_CURATE_TAG``),
     visibly distinct from an already-leg-classified ``[LEG-N][NEW]`` row.
     Leg-classification is an AGENT judgment (which spine section a paper
     belongs to) this module cannot fabricate — the tag is a loud
     call-to-action for a re-curate pass, never a silent substitute for one.
  3. **Never reuses a stale cold verify** — a round that adds ANY row
     invalidates the existing ``_relevance-verdict.md`` (if present), so
     the NEXT coverage-gate evaluation reads ``exists: False`` and the
     relevance disposition fails closed to HALT-DECLARE (``not_run``) —
     forcing a FRESH cold relevance-verify pass over the round's additions
     before the corpus can be re-certified. A round's own disposition is
     therefore ALWAYS HALT-DECLARE ("awaiting re-curate + fresh
     relevance-verify"), never a same-invocation GO.

Stdlib only (+ intra-package imports).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from ..cite import _make_citekey
from .autonomy import (
    DEVIATION_KIND_WITHIN_FACET_QUERY_APPEND,
    FACET_REMEDIATE,
    HALT_DECLARE,
    DispositionResult,
    record_deviation,
    run_tool_op,
)
from .corpus_freeze import (
    check_facet_query_append_re_gate,
    hash_criteria_bytes,
    hash_query_matrix_bytes,
    refresh,
    stamp_corpus_freeze,
)

# ---------------------------------------------------------------------------
# 1. The gate-policy extension — mirrors `resolve_coverage_critic`'s shape
#    (a `classify_disposition`-produced `base` extended with a SEPARATE,
#    orthogonal round-bounded decision), but simpler: no
#    `remediation_target_expected` precondition — Layer 2's thin-pole list
#    is already structured/unambiguous (no free-prose critic verdict to
#    localize first).
# ---------------------------------------------------------------------------

_POLE_KEY_RE = re.compile(r"^(?P<angle>[\w-]+)\.(?P<stance>thesis|counter)$")


def resolve_facet_coverage(
    base: DispositionResult,
    facet_coverage_info: dict[str, Any] | None,
    *,
    remediation_state: dict[str, Any] | None = None,
    max_rounds: int | None = None,
    config: Any = None,
    protocol_declares_facets: bool | None = None,
) -> DispositionResult:
    """Extend a coverage-gate ``base`` disposition with the Layer-2/3
    facet-coverage decision (the explicit 3-tier fold, item 2 of the
    design — the CALLER, ``dag/verbs.py``, is responsible for calling this
    ONLY when ``base`` is not already HALT-DECLARE; tier 1, "any HALT
    dominates", is enforced by never reaching this function otherwise).

    - ``facet_coverage_info`` is ``None`` or ``not facet_coverage_info.get
      ("declared")`` -> ``base`` unchanged (an honest no-op: a manifest
      with no nested D-3 facets, or a pre-0.3.1 sweep, never computed
      facet-coverage at all — never a fabricated thin-pole signal) —
      UNLESS ``protocol_declares_facets`` is explicitly ``True`` (below).
    - no ``thin_poles`` -> ``base`` unchanged (nothing to remediate).
    - thin pole(s), remediation budget remaining -> ``FACET_REMEDIATE``
      (dispatch one bounded round for the FIRST thin pole, deterministic
      alphabetical order — one pole per round, mirrors the critic-backtrack
      loop's one-pole-per-round shape).
    - budget exhausted -> ``HALT_DECLARE``, fail-closed (never fish
      further; a still-thin pole after R rounds needs a human call —
      widen the criteria, or accept the residue via a human decision).

    Hardening (missing-SET fail-closed): the "not declared" no-op above is
    correct for a genuinely legacy/non-faceted protocol, but gives no
    signal if the PROTOCOL itself declared nested facets (see
    ``sources.sweep.group_facet_stances``) and Layer-2's stamp onto
    ``_search_hits.md`` silently failed to write — the breadth
    guarantee would then be skipped with no trace. The CALLER may pass
    ``protocol_declares_facets`` (``bool(group_facet_stances(parse_angle_
    matrix(protocol_text)))``) to cross-check this. Left ``None`` (the
    default), this cross-check is skipped — an honest "unknown", not a
    silent pass — so existing callers/tests that never pass it keep the
    prior no-op behavior. When it IS supplied as ``True`` and the sweep's
    own coverage payload is absent/undeclared, that is treated as a
    stamping failure, not a legacy protocol, and resolves ``HALT_DECLARE``
    rather than falling through to ``base``.
    """
    if base.disposition == HALT_DECLARE:
        return base
    if not facet_coverage_info or not facet_coverage_info.get("declared"):
        if protocol_declares_facets:
            return DispositionResult(
                HALT_DECLARE,
                "Layer-2 facet-coverage: the protocol declares nested "
                "facets/poles, but the sweep's _search_hits.md carries no "
                "facet-coverage stamp (facet_pole_counts/declared) — this "
                "is a stamping failure, not a legacy non-faceted protocol, "
                "and the breadth guarantee cannot be silently skipped. "
                "Re-run the sweep so Layer-2 coverage is computed and "
                "stamped, or investigate why the stamp was dropped.",
                {"facet_coverage_info": facet_coverage_info},
            )
        return base
    thin_poles = facet_coverage_info.get("thin_poles") or []
    if not thin_poles:
        return base

    rs = remediation_state or {}
    rounds_used = int(rs.get("rounds_used", 0))
    if max_rounds is None:
        from .style import get_max_facet_remediation_rounds

        cap = get_max_facet_remediation_rounds(config)
    else:
        cap = max_rounds

    target_pole = sorted(thin_poles)[0]

    if rounds_used < cap:
        return DispositionResult(
            FACET_REMEDIATE,
            f"Layer-2 facet-coverage: pole {target_pole!r} surfaced fewer than "
            f"{facet_coverage_info.get('min_hits_per_pole', '?')} distinct papers "
            f"this sweep — remediation budget remaining ({rounds_used}/{cap} "
            "rounds used) — dispatch one bounded facet re-search round.",
            {"thin_poles": list(thin_poles), "target_pole": target_pole,
             "rounds_used": rounds_used, "max_rounds": cap},
        )

    return DispositionResult(
        HALT_DECLARE,
        f"Layer-2 facet-coverage: pole(s) {sorted(thin_poles)} still thin "
        f"after the facet-remediation budget ({cap} rounds) was exhausted — "
        "this is a hard structural signal, not auto-fishable further; "
        "widen the frozen criteria/pole by hand (a human "
        "criteria-change decision) or accept the residue explicitly.",
        {"thin_poles": list(thin_poles), "rounds_used": rounds_used, "max_rounds": cap},
    )


# ---------------------------------------------------------------------------
# 2. Emit/ingest task files — a fresh AGENT authors the new queries (never
#    in-process; unlike the critic-backtrack's `run_directed_remediation_
#    round`, which only re-runs an ALREADY-frozen counter query harder, this
#    loop must AUTHOR new query text). Mirrors `relate_judge_seam`'s
#    emit-then-later-ingest shape, simplified: one small markdown task +
#    one small markdown response, no batching (one facet per round).
# ---------------------------------------------------------------------------

_TASK_FILENAME = "_facet-query-task.md"
_RESPONSE_FILENAME = "_facet-query-response.md"
_QUERY_FENCE_RE = re.compile(r"```queries\s*\n(.*?)```", re.DOTALL)


def facet_task_dir(out_dir: Path, pole: str) -> Path:
    """The task/response directory for one pole's remediation round —
    ``<out_dir>/judge/facet-remediate/<pole-with-dots-as-dashes>/``."""
    safe = pole.replace(".", "-")
    return out_dir / "judge" / "facet-remediate" / safe


def emit_facet_query_task(
    task_dir: Path,
    *,
    pole: str,
    existing_queries: list[str],
    min_queries_needed: int,
    min_hits_per_pole: int,
    current_count: int,
) -> Path:
    """Write the task doc a fresh agent reads to author NEW queries for
    ``pole`` (an ALREADY-declared facet/stance — this loop can only append,
    never author a new facet; see ``sources.sweep.
    append_queries_to_protocol_text``'s own invariant)."""
    task_dir.mkdir(parents=True, exist_ok=True)
    existing_block = "\n".join(f"- {q!r}" for q in existing_queries) or "(none)"
    text = (
        "# Facet re-search remediation task\n\n"
        f"**Pole:** {pole}\n"
        f"**Current distinct-paper coverage:** {current_count} "
        f"(floor: {min_hits_per_pole})\n\n"
        "This pole surfaced too few DISTINCT papers this sweep. Author "
        f"AT LEAST {min_queries_needed} NEW, genuinely distinct search "
        f"queries for this SAME frozen facet/pole — never a different "
        "facet, never a rephrasing of an already-declared query below.\n\n"
        "## Already-declared queries for this pole\n\n"
        f"{existing_block}\n\n"
        "## Your new queries\n\n"
        "Write your new queries inside a fenced ```queries``` block, one "
        "per line, in this exact file (append below, do not remove this "
        "task content):\n\n"
        "```queries\n```\n"
    )
    path = task_dir / _TASK_FILENAME
    path.write_text(text, encoding="utf-8")
    return path


def facet_task_pending(task_dir: Path) -> bool:
    """True iff a task was emitted for this pole and no response has been
    written yet — the round is awaiting the hub's agent fan-out."""
    return (task_dir / _TASK_FILENAME).exists() and not (task_dir / _RESPONSE_FILENAME).exists()


def read_facet_query_response(task_dir: Path) -> list[str] | None:
    """Parse the agent-authored new queries from ``_facet-query-response.md``
    (a fenced ```queries``` block, one query per non-empty line — same
    fenced-block convention ``review.autonomy._extract_seed_ids_from_screen``
    already uses). Returns ``None`` if no response file exists yet."""
    path = task_dir / _RESPONSE_FILENAME
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    m = _QUERY_FENCE_RE.search(text)
    lines = m.group(1).splitlines() if m else text.splitlines()
    return [ln.strip() for ln in lines if ln.strip()]


def clear_facet_task(task_dir: Path) -> None:
    """Remove the task+response files after a round has consumed them
    (mirrors ``review.relate_judge_seam.clear_relate_fanout``)."""
    for name in (_TASK_FILENAME, _RESPONSE_FILENAME):
        p = task_dir / name
        if p.exists():
            p.unlink()


# ---------------------------------------------------------------------------
# 3. The screen-tag-append helper — the sibling-bug fix. Deliberately NOT a
#    reuse of `review.remediation._append_new_corpus_rows` (that helper
#    appends raw, unscreened, un-tagged rows — exactly the bypass this
#    module exists to close).
# ---------------------------------------------------------------------------

_NEEDS_CURATE_TAG = "[NEEDS-CURATE]"


def _norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").strip().lower())


def _parse_corpus_row_titles(corpus_path: Path) -> set[str]:
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


def screen_and_append_facet_hits(
    corpus_path: Path,
    hits: list[Any],
    *,
    criteria: dict[str, Any],
    counter_position: str,
    residue_path: Path,
    existing_citekeys: set[str],
) -> dict[str, Any]:
    """Screen ``hits`` through ``review.relevance.relevance_gate`` BEFORE
    any of them reach ``_corpus.md`` (the mechanical gate between snowball
    and curate — reused here, not reimplemented). ``OFF_DOMAIN`` hits are
    DECLARED into ``residue_path``, never appended. ``IN``/``UNCERTAIN``
    hits are appended tagged ``[NEW]{_NEEDS_CURATE_TAG}`` — visibly
    distinct from an already-leg-classified row, a loud call-to-action for
    a re-curate pass rather than a silent bypass of one.

    Returns ``{"added": [citekey, ...], "off_domain": [title, ...],
    "uncertain": [citekey, ...]}``.
    """
    from .relevance import IN, OFF_DOMAIN, UNCERTAIN, relevance_gate

    existing_titles = _parse_corpus_row_titles(corpus_path)
    seen_this_round: set[str] = set()
    new_rows: list[str] = []
    added_citekeys: list[str] = []
    uncertain_citekeys: list[str] = []
    off_domain_titles: list[str] = []
    all_citekeys = set(existing_citekeys)

    for hit in hits:
        title = getattr(hit, "title", "") or ""
        if not title:
            continue
        norm = _norm_title(title)
        if norm in existing_titles or norm in seen_this_round:
            continue
        seen_this_round.add(norm)

        candidate = {"title": title, "abstract": getattr(hit, "abstract", "") or ""}
        verdict = relevance_gate(candidate, criteria, counter_position)
        if verdict == OFF_DOMAIN:
            off_domain_titles.append(title)
            continue

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
        if verdict == UNCERTAIN:
            uncertain_citekeys.append(citekey)
            new_rows.append(f"| [NEW]{_NEEDS_CURATE_TAG} [RELEVANCE:UNCERTAIN] | {citekey} | {title} |")
        else:
            new_rows.append(f"| [NEW]{_NEEDS_CURATE_TAG} | {citekey} | {title} |")

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

    if off_domain_titles:
        residue_path.parent.mkdir(parents=True, exist_ok=True)
        existing_residue = residue_path.read_text(encoding="utf-8") if residue_path.exists() else (
            "# Facet-remediation off-domain residue\n\n"
            "Papers surfaced by a Layer-3 facet re-search round that the "
            "mechanical relevance-gate rejected as OFF_DOMAIN — declared "
            "here, never silently dropped, never appended to `_corpus.md`.\n"
        )
        block = "\n".join(f"- {t}" for t in off_domain_titles) + "\n"
        residue_path.write_text(existing_residue + block, encoding="utf-8")

    return {
        "added": sorted(added_citekeys),
        "off_domain": off_domain_titles,
        "uncertain": sorted(uncertain_citekeys),
    }


# ---------------------------------------------------------------------------
# 4. The round driver — applies the tiered-hash append, re-sweeps the ONE
#    pole, screens+tags+appends (never raw), declares the deviation,
#    refreshes, invalidates any stale cold-verify artifact, and updates the
#    persisted facet-coverage snapshot.
# ---------------------------------------------------------------------------

_RELAXED_PER_CELL_LIMIT = 40


def run_facet_query_append_round(
    run_state_meta: dict[str, Any],
    *,
    pole: str,
    new_queries: list[str],
    protocol_path: Path,
    corpus_path: Path,
    deviations_path: Path,
    out_dir: Path,
    search_hits_path: Path | None = None,
    relevance_verdict_path: Path | None = None,
    min_hits_per_pole: int = 3,
    tool_op_fn: Callable[..., Any] | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Execute ONE bounded, POLE-DIRECTED facet re-search remediation round.

    Order: structural re-gate on the CANDIDATE protocol text (never write
    an invalid mutation) -> write the append -> re-sweep ONLY this pole's
    (now-grown) query list -> mechanically screen+tag+append the new hits
    (``screen_and_append_facet_hits`` — never raw, see module docstring) ->
    declare the ``within-facet-query-append`` deviation -> ``corpus_freeze.
    refresh`` (gates on the frozen tier, which the structural re-gate
    already proved unchanged — passes) -> invalidate any stale
    ``_relevance-verdict.md`` (a round that adds rows makes the existing
    cold-verify artifact stale; the NEXT coverage-gate evaluation must fail
    closed to a fresh cold verify, never silently reuse it) -> update the
    persisted per-pole facet-coverage snapshot on ``_search_hits.md``.

    Returns ``{"pole", "added", "uncertain", "off_domain", "new_queries",
    "facet_coverage"}``. Raises ``ValueError`` if the structural re-gate
    rejects the candidate mutation (never writes a rejected mutation to
    disk) or if ``pole`` is not shaped ``"<angle>.(thesis|counter)"``.
    """
    m = _POLE_KEY_RE.match(pole)
    if not m:
        raise ValueError(
            f"run_facet_query_append_round: pole {pole!r} is not shaped "
            "'<angle>.(thesis|counter)'"
        )
    angle, stance = m.group("angle"), m.group("stance")

    if tool_op_fn is None:
        tool_op_fn = run_tool_op

    baseline = run_state_meta.get("corpus_freeze")
    if baseline is None:
        baseline = stamp_corpus_freeze(
            run_state_meta, corpus_path=corpus_path, protocol_path=protocol_path, now=now,
        )

    from ..sources.sweep import append_queries_to_protocol_text

    pre_text = protocol_path.read_text(encoding="utf-8")
    pre_frozen_hash = hash_criteria_bytes(protocol_path)
    pre_query_hash = hash_query_matrix_bytes(protocol_path)

    post_text = append_queries_to_protocol_text(pre_text, angle, stance, new_queries)

    ok, gate_msg = check_facet_query_append_re_gate(pre_text, post_text)
    if not ok:
        raise ValueError(f"run_facet_query_append_round: {gate_msg}")

    protocol_path.write_text(post_text, encoding="utf-8")

    from . import _parse_corpus_citekeys
    from .relevance import parse_protocol_criteria
    from .remediation import _extract_hits

    existing_citekeys = set(_parse_corpus_citekeys(corpus_path))
    criteria, counter_position = parse_protocol_criteria(protocol_path)

    sweep_result = tool_op_fn(
        "sweep", protocol=str(protocol_path), angle_keys={pole},
        per_cell_limit=_RELAXED_PER_CELL_LIMIT,
    )
    hits = _extract_hits(sweep_result)

    residue_path = out_dir / "_facet-remediation-residue.md"
    screen_result = screen_and_append_facet_hits(
        corpus_path, hits,
        criteria=criteria, counter_position=counter_position,
        residue_path=residue_path, existing_citekeys=existing_citekeys,
    )
    added = screen_result["added"]

    post_query_hash = hash_query_matrix_bytes(protocol_path)
    record_deviation(
        deviations_path,
        version=baseline["version"] + 1,
        pre_criteria=pre_frozen_hash, post_criteria=pre_frozen_hash,
        removed=[], added=added,
        rationale=(
            f"autonomous facet re-search remediation for thin pole {pole!r}: "
            f"authored {len(new_queries)} new queries; "
            f"{len(added)} screened-in paper(s) added "
            f"({len(screen_result['off_domain'])} rejected off-domain, "
            f"declared in {residue_path.name})."
        ),
        kind=DEVIATION_KIND_WITHIN_FACET_QUERY_APPEND,
        facet_key=pole, new_queries=new_queries,
        pre_query_matrix_hash=pre_query_hash, post_query_matrix_hash=post_query_hash,
        now=now,
    )

    refresh(
        run_state_meta, corpus_path=corpus_path, protocol_path=protocol_path,
        deviations_path=deviations_path, now=now,
    )

    # A round that added ANY row invalidates the existing cold relevance-
    # verify artifact — never let coverage-gate's NEXT evaluation silently
    # reuse a verdict computed before these rows existed (the exact
    # stale-artifact class this whole fix exists to prevent).
    if added and relevance_verdict_path is not None and relevance_verdict_path.exists():
        relevance_verdict_path.unlink()

    updated_coverage = None
    if search_hits_path is not None and search_hits_path.exists():
        from . import update_search_hits_pole_count

        updated_coverage = update_search_hits_pole_count(
            search_hits_path, pole, len(added), min_hits_per_pole=min_hits_per_pole,
        )

    return {
        "pole": pole,
        "added": added,
        "uncertain": screen_result["uncertain"],
        "off_domain": screen_result["off_domain"],
        "new_queries": new_queries,
        "facet_coverage": updated_coverage,
    }
