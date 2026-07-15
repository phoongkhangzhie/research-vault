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

import json
import re
from pathlib import Path
from typing import Any, Callable

from ..cite import _make_citekey
from .autonomy import (
    DEVIATION_KIND_WITHIN_FACET_QUERY_APPEND,
    FACET_REMEDIATE,
    GO_WITH_RESIDUE,
    HALT_DECLARE,
    DispositionResult,
    record_deviation,
    run_tool_op,
)
from .corpus_freeze import (
    _iter_within_facet_query_append_deviations,
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
    deviations_path: Path | None = None,
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
    - budget exhausted -> the autonomous under-searched-vs-sparse judgment
      (search-primary redesign, Section E). This is the agent's/Alfred's
      call, never the user's (``approve-protocol`` stays the sole human
      gate) — but it is NOT an honor-system self-report: it is accepted
      ONLY when mechanical proof-of-seeking is on record.

      Anti-gaming teeth (fail-closed, not honor-system): pass
      ``deviations_path`` and this function checks
      ``corpus_freeze._iter_within_facet_query_append_deviations``,
      filtered to ``facet_key == target_pole``, for a recorded round. A
      recorded round for THIS EXACT pole -> genuinely-sparse, not merely
      "the round counter says exhausted" -> record a gap + PASS
      (``GO_WITH_RESIDUE``, evidence carries ``sparse_pole_dispositions``
      for the caller to bind a ``gaps/<pole>.md`` note with
      ``disposition: leaves-open``). No matching recorded round (never
      dispatched, or dispatched for a DIFFERENT pole — e.g. a corrupted/
      stale ``remediation_state``) -> "never genuinely searched" ->
      ``HALT_DECLARE``, the same fail-closed floor as before.

      Backward compatibility: ``deviations_path=None`` (the default, for
      callers that have not opted into the teeth) preserves the OLD,
      conservative behavior — unconditional ``HALT_DECLARE`` on budget
      exhaustion, never a silent PASS a caller didn't ask for.

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

    if deviations_path is not None:
        recorded_poles = {
            d["facet_key"]
            for d in _iter_within_facet_query_append_deviations(deviations_path)
        }
        proven = sorted(p for p in thin_poles if p in recorded_poles)
        unproven = sorted(p for p in thin_poles if p not in recorded_poles)
        if proven and not unproven:
            # Every thin pole has a mechanical proof-of-seeking record —
            # the agent's/Alfred's autonomous under-searched-vs-sparse call
            # resolves SPARSE (never the user's; approve-protocol stays the
            # sole human gate). Bound to gaps/<pole>.md via
            # sparse_pole_dispositions — the caller writes
            # disposition: leaves-open + a non-empty disposition_reason so
            # gap_coverage_gate passes it cleanly (no ANSWERS edge needed).
            reason = (
                "genuinely-sparse after the bounded facet-remediation "
                f"attempt ({cap} round(s)) — a within-facet-query-append "
                "round is on record for this pole (new queries authored, "
                "swept, still thin), so this is discharged by SEEKING, "
                "not FINDING."
            )
            return DispositionResult(
                GO_WITH_RESIDUE,
                f"Layer-2 facet-coverage: pole(s) {proven} still thin after "
                f"the facet-remediation budget ({cap} rounds) was exhausted, "
                "but a within-facet-query-append round is on record for "
                "each — genuinely-sparse, not under-searched. Recorded as a "
                "gap and PASSED (autonomous call; a human may still widen "
                "the criteria by hand later).",
                {
                    "thin_poles": list(thin_poles), "rounds_used": rounds_used,
                    "max_rounds": cap,
                    "sparse_pole_dispositions": {p: reason for p in proven},
                },
            )
        if unproven:
            return DispositionResult(
                HALT_DECLARE,
                f"Layer-2 facet-coverage: pole(s) {unproven} still thin "
                f"after the facet-remediation budget ({cap} rounds) was "
                "exhausted, and NO 'within-facet-query-append' deviation is "
                "on record for (all of) them — this reads as 'never "
                "genuinely searched', not 'genuinely sparse' (anti-gaming "
                "teeth, fail-closed). Never auto-fishable further; widen "
                "the frozen criteria/pole by hand (a human criteria-change "
                "decision) or accept the residue explicitly.",
                {
                    "thin_poles": list(thin_poles), "rounds_used": rounds_used,
                    "max_rounds": cap, "unproven_poles": unproven,
                },
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
# 2b. B2 — the cold relevance-verify emit/ingest fan-out for facet-
#     remediation candidates. A mechanically-screened-in candidate must be
#     COLD-VERIFIED before it lands in ``_corpus.md`` — reuses the SAME
#     markdown canary/verdict-table shape and parser as the final-corpus
#     cold verifier (``review.relevance.build_canary_rows`` /
#     ``_interleave_canaries_deterministic`` / ``check_relevance_verifier``
#     — same verdict domain, same two fixed canary citekeys; charter §6, no
#     second cold-verify mechanism). Judges are harness-fanout only — this
#     module calls NO LLM; the hub fans fresh cold subagent-judges out over
#     the emitted input and writes the verdict file alongside it.
# ---------------------------------------------------------------------------

_REMEDIATION_VERIFY_INPUT_FILENAME = "_facet-remediation-verify-input.md"
_REMEDIATION_VERIFY_VERDICT_FILENAME = "_facet-remediation-verify-verdict.md"


def emit_remediation_verify_input(
    task_dir: Path,
    candidates: list[dict[str, str]],
    criteria: dict[str, Any],
) -> dict[str, Any]:
    """Write the cold-verify input for THIS round's mechanically-screened
    candidates — the two unmarked canary rows (``review.relevance.
    build_canary_rows``) interleaved at deterministic, non-trailing
    positions, exactly like the final-corpus verifier's own input. Returns
    ``{"real_citekeys": [...], "path": Path}``.
    """
    from .relevance import build_canary_rows, _interleave_canaries_deterministic

    task_dir.mkdir(parents=True, exist_ok=True)
    canary_rows = build_canary_rows(criteria)
    real_rows = [
        {"citekey": c["citekey"], "title": c["title"], "abstract": c.get("abstract", "")}
        for c in candidates
    ]
    all_rows = _interleave_canaries_deterministic(real_rows, canary_rows)

    lines = [
        "# Facet-remediation cold relevance-verify input\n",
        f"<!-- real_row_count: {len(real_rows)} -->\n",
        "Judge EACH row IN/OFF_DOMAIN/UNCERTAIN per the relevance-gate "
        "calibration. These are papers a facet re-search remediation round "
        "mechanically screened as candidates for a thin research-question "
        "facet/pole — cold-verify BEFORE any of them lands in the "
        "corpus.\n",
        "| Citekey | Title | Abstract/TL;DR |",
        "|---|---|---|",
    ]
    for row in all_rows:
        lines.append(f"| {row['citekey']} | {row['title']} | {row['abstract']} |")
    lines.append("")

    out_path = task_dir / _REMEDIATION_VERIFY_INPUT_FILENAME
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return {"real_citekeys": [r["citekey"] for r in real_rows], "path": out_path}


def facet_remediation_verify_pending(task_dir: Path) -> bool:
    """True iff a cold-verify input was emitted for this round and no
    verdict has been written yet — awaiting the hub's cold-judge fan-out."""
    return (
        (task_dir / _REMEDIATION_VERIFY_INPUT_FILENAME).exists()
        and not (task_dir / _REMEDIATION_VERIFY_VERDICT_FILENAME).exists()
    )


def ingest_remediation_verify_verdicts(task_dir: Path) -> dict[str, Any]:
    """Ingest the cold verifier's verdict file — a thin, path-bound
    wrapper over ``review.relevance.check_relevance_verifier`` (SAME
    parser, SAME two canary citekeys, SAME fail-closed/canary-abort
    semantics as the final-corpus cold verifier; charter §6)."""
    from .relevance import check_relevance_verifier

    return check_relevance_verifier(task_dir / _REMEDIATION_VERIFY_VERDICT_FILENAME)


def facet_remediation_awaiting_response(task_dir: Path) -> bool:
    """True iff THIS pole's remediation round is blocked awaiting SOME
    external response file the hub must write — either the agent-authored
    query-response (Phase 1) or the cold relevance-verify verdict (Phase
    2/B2). Used by the DAG tick auto-reopen (Section E: a blocked
    FACET_REMEDIATE gate must re-evaluate on tick once the response
    exists — no manual ``redo``)."""
    return facet_task_pending(task_dir) or facet_remediation_verify_pending(task_dir)


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

# The Section-E cap: remediation adds a round produces are capped at
# roughly the pole's floor (default matches review.style's
# DEFAULT_MIN_HITS_PER_POLE) — this is what kills the 212-flood at the
# source (docs: search-primary redesign, item E, "cap remediation adds at
# ~floor"). Hits beyond the cap are declared, never silently discarded and
# never mechanically screened further (an honest "we stopped once the
# floor was reached", not an off-domain verdict).
_DEFAULT_REMEDIATION_CAP = 3

_ROUND_STATE_FILENAME = "_facet-remediation-round-state.json"


def _screen_facet_candidates(
    hits: list[Any],
    *,
    criteria: dict[str, Any],
    counter_position: str,
    existing_titles: set[str],
    existing_citekeys: set[str],
    cap: int,
) -> dict[str, Any]:
    """Pure mechanical screen (``review.relevance.relevance_gate`` — the
    SAME primitive ``screen_and_append_facet_hits`` uses) that returns
    candidates WITHOUT writing anything. The round driver defers any
    corpus mutation until AFTER the B2 cold relevance-verify fan-out
    confirms each candidate — this is deliberately NOT a reuse of
    ``screen_and_append_facet_hits`` (that helper's contract is an
    immediate append; the round driver's contract now requires a pause
    for a cold verdict in between).

    Caps the IN/UNCERTAIN candidate count at ``cap`` — checked BEFORE the
    mechanical gate call (stop looking, not "looked and rejected"), so a
    hit beyond the cap is declared in the THIRD ``capped`` bucket, never
    conflated with a genuine OFF_DOMAIN verdict.

    Returns ``{"candidates": [{"citekey","title","abstract"}, ...],
    "off_domain": [title, ...], "capped": [title, ...]}``.
    """
    from .relevance import OFF_DOMAIN, relevance_gate

    seen_this_round: set[str] = set()
    candidates: list[dict[str, str]] = []
    off_domain_titles: list[str] = []
    capped_titles: list[str] = []
    all_citekeys = set(existing_citekeys)

    for hit in hits:
        title = getattr(hit, "title", "") or ""
        if not title:
            continue
        norm = _norm_title(title)
        if norm in existing_titles or norm in seen_this_round:
            continue
        seen_this_round.add(norm)

        if len(candidates) >= cap:
            capped_titles.append(title)
            continue

        abstract = getattr(hit, "abstract", "") or ""
        verdict = relevance_gate({"title": title, "abstract": abstract}, criteria, counter_position)
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
        candidates.append({
            "citekey": citekey, "title": title, "abstract": abstract,
            "mechanical_verdict": verdict,
        })

    return {"candidates": candidates, "off_domain": off_domain_titles, "capped": capped_titles}


def _round_state_path(task_dir: Path) -> Path:
    return task_dir / _ROUND_STATE_FILENAME


def clear_remediation_round_state(task_dir: Path) -> None:
    """Remove every artifact of an in-progress/completed round from
    ``task_dir`` — mirrors ``clear_facet_task``."""
    for name in (
        _ROUND_STATE_FILENAME,
        _REMEDIATION_VERIFY_INPUT_FILENAME,
        _REMEDIATION_VERIFY_VERDICT_FILENAME,
    ):
        p = task_dir / name
        if p.exists():
            p.unlink()


def _declare_remediation_residue(
    residue_path: Path,
    *,
    off_domain_titles: list[str],
    capped_titles: list[str],
    cold_rejected_titles: list[str],
) -> None:
    """Declare every non-appended candidate — mechanical off-domain, cap
    overflow, and cold-verify rejection — never a silent drop (charter
    §2). Appends to the SAME residue file across a review's rounds."""
    if not (off_domain_titles or capped_titles or cold_rejected_titles):
        return
    residue_path.parent.mkdir(parents=True, exist_ok=True)
    existing = residue_path.read_text(encoding="utf-8") if residue_path.exists() else (
        "# Facet-remediation residue\n\n"
        "Papers a Layer-3 facet re-search round did NOT add to `_corpus.md` "
        "— declared here, never silently dropped.\n"
    )
    blocks = []
    if off_domain_titles:
        blocks.append(
            "\n## Rejected off-domain (mechanical relevance-gate)\n\n"
            + "\n".join(f"- {t}" for t in off_domain_titles) + "\n"
        )
    if cold_rejected_titles:
        blocks.append(
            "\n## Rejected off-domain (cold relevance-verify, B2)\n\n"
            "Mechanically screened IN/UNCERTAIN, but the cold verify pass "
            "rejected these — the cold verify is a SEPARATE gate from the "
            "mechanical screen, not a rubber stamp of it.\n\n"
            + "\n".join(f"- {t}" for t in cold_rejected_titles) + "\n"
        )
    if capped_titles:
        blocks.append(
            "\n## Capped (remediation floor reached, not screened further)\n\n"
            "The round stopped looking once the pole's floor was reached "
            "(Section E cap) — these were never mechanically screened, "
            "never judged off-domain, simply not needed.\n\n"
            + "\n".join(f"- {t}" for t in capped_titles) + "\n"
        )
    residue_path.write_text(existing + "".join(blocks), encoding="utf-8")


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
    """Execute ONE bounded, POLE-DIRECTED facet re-search remediation round
    — B2: TWO-PHASE, gated on a cold relevance-verify pass over the
    round's candidates BEFORE any of them lands in ``_corpus.md``.

    Phase 1 (first call for this pole/round — no persisted round-state
    yet): structural re-gate on the CANDIDATE protocol text (never write
    an invalid mutation) -> write the query append (ONE TIME; a repeat
    call never re-appends, see below) -> re-sweep ONLY this pole's
    (now-grown) query list -> mechanically screen + CAP at
    ``min_hits_per_pole`` (``_screen_facet_candidates`` — kills the
    212-flood at the source) -> persist the round state (candidates,
    off-domain, capped, query hashes) so a later call doesn't re-sweep ->
    emit the cold-verify input (``emit_remediation_verify_input``) if
    there is at least one candidate. Returns
    ``{"phase": "awaiting_cold_verify", "pole", "candidates": [citekey,
    ...], "off_domain", "capped", "task_dir"}`` — the protocol was
    mutated (safe; adds queries only) but the corpus/deviations were NOT.

    A candidate-free screen (nothing survived the mechanical gate) skips
    the cold-verify pause entirely — there is nothing to verify — and
    falls straight through to Phase 2 in the SAME call.

    Phase 2 (round-state already persisted): if the cold-verify verdict
    is still pending, returns the SAME ``"awaiting_cold_verify"`` shape
    again (idempotent poll). Once the verdict file exists: ingest it
    (``ingest_remediation_verify_verdicts`` — canary-checked; a canary
    miss returns ``{"phase": "canary_aborted", ...}``, appends NOTHING,
    and leaves the round state in place for a fresh cold-verify attempt).
    A clean pass appends only NOT-OFF_DOMAIN candidates (bias-to-keep on
    a missing/malformed per-candidate verdict — recall-safe, mirrors
    ``review.relevance.check_relevance_verifier``'s own philosophy),
    tagged ``[NEW]{_NEEDS_CURATE_TAG}`` (never a bare/leg-tagged row) ->
    declares the ``within-facet-query-append`` deviation -> ``corpus_
    freeze.refresh`` -> invalidates any stale ``_relevance-verdict.md`` ->
    updates the persisted per-pole facet-coverage snapshot -> clears the
    round state. Returns ``{"phase": "applied", "pole", "added",
    "uncertain", "off_domain", "capped", "new_queries",
    "facet_coverage"}``.

    Raises ``ValueError`` if the structural re-gate rejects the candidate
    mutation (never writes a rejected mutation to disk) or if ``pole`` is
    not shaped ``"<angle>.(thesis|counter)"``.
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

    task_dir = facet_task_dir(out_dir, pole)
    round_state_path = _round_state_path(task_dir)
    residue_path = out_dir / "_facet-remediation-residue.md"

    if round_state_path.exists():
        round_state = json.loads(round_state_path.read_text(encoding="utf-8"))
    else:
        from ..sources.sweep import append_queries_to_protocol_text

        pre_text = protocol_path.read_text(encoding="utf-8")
        pre_frozen_hash = hash_criteria_bytes(protocol_path)
        pre_query_hash = hash_query_matrix_bytes(protocol_path)

        post_text = append_queries_to_protocol_text(pre_text, angle, stance, new_queries)

        ok, gate_msg = check_facet_query_append_re_gate(pre_text, post_text)
        if not ok:
            raise ValueError(f"run_facet_query_append_round: {gate_msg}")

        protocol_path.write_text(post_text, encoding="utf-8")
        post_query_hash = hash_query_matrix_bytes(protocol_path)

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

        cap = min_hits_per_pole if min_hits_per_pole and min_hits_per_pole > 0 else _DEFAULT_REMEDIATION_CAP
        screened = _screen_facet_candidates(
            hits, criteria=criteria, counter_position=counter_position,
            existing_titles=_parse_corpus_row_titles(corpus_path),
            existing_citekeys=existing_citekeys, cap=cap,
        )

        round_state = {
            "pre_frozen_hash": pre_frozen_hash,
            "pre_query_hash": pre_query_hash,
            "post_query_hash": post_query_hash,
            "new_queries": new_queries,
            "candidates": screened["candidates"],
            "off_domain": screened["off_domain"],
            "capped": screened["capped"],
        }
        round_state_path.parent.mkdir(parents=True, exist_ok=True)
        round_state_path.write_text(json.dumps(round_state, indent=2), encoding="utf-8")

        if screened["candidates"]:
            from .relevance import parse_protocol_criteria as _reparse

            criteria_for_verify, _cp = _reparse(protocol_path)
            emit_remediation_verify_input(task_dir, screened["candidates"], criteria_for_verify)

    candidates = round_state["candidates"]

    if candidates and facet_remediation_verify_pending(task_dir):
        return {
            "phase": "awaiting_cold_verify",
            "pole": pole,
            "task_dir": str(task_dir),
            "candidates": [c["citekey"] for c in candidates],
            "off_domain": round_state["off_domain"],
            "capped": round_state["capped"],
        }

    verify = (
        ingest_remediation_verify_verdicts(task_dir)
        if candidates
        else {"exists": True, "canary_aborted": False, "verdicts": {}, "malformed": []}
    )

    if verify.get("canary_aborted"):
        # The judge that wrote this verdict file is untrustworthy — delete
        # ONLY the verdict (never the round-state or the verify-input, so
        # a fresh cold-judge fan-out can retry without re-sweeping). This
        # also puts `facet_remediation_verify_pending`/`_awaiting_response`
        # back to True, so the DAG tick auto-reopen (Section E) correctly
        # keeps this node blocked awaiting a NEW verdict, rather than
        # mistaking "a bad verdict exists" for "nothing is pending".
        verdict_path = task_dir / _REMEDIATION_VERIFY_VERDICT_FILENAME
        if verdict_path.exists():
            verdict_path.unlink()
        return {
            "phase": "canary_aborted",
            "pole": pole,
            "task_dir": str(task_dir),
            "canary_detail": verify.get("canary_detail", ""),
        }

    from .relevance import OFF_DOMAIN, UNCERTAIN

    kept: list[dict[str, str]] = []
    uncertain_citekeys: list[str] = []
    cold_rejected_titles: list[str] = []
    for c in candidates:
        verdict = verify["verdicts"].get(c["citekey"])
        if verdict == OFF_DOMAIN:
            cold_rejected_titles.append(c["title"])
            continue
        kept.append(c)
        if verdict == UNCERTAIN or (verdict is None and c.get("mechanical_verdict") == UNCERTAIN):
            uncertain_citekeys.append(c["citekey"])
        # verdict is None (missing/malformed per-candidate verdict) or IN:
        # bias-to-keep, mirrors review.relevance.check_relevance_verifier's
        # own recall-safe philosophy for a malformed/absent verdict row.

    new_rows: list[str] = []
    for c in kept:
        tag = f"[NEW]{_NEEDS_CURATE_TAG}"
        if c["citekey"] in uncertain_citekeys:
            new_rows.append(f"| {tag} [RELEVANCE:UNCERTAIN] | {c['citekey']} | {c['title']} |")
        else:
            new_rows.append(f"| {tag} | {c['citekey']} | {c['title']} |")

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

    _declare_remediation_residue(
        residue_path,
        off_domain_titles=round_state["off_domain"],
        capped_titles=round_state["capped"],
        cold_rejected_titles=cold_rejected_titles,
    )

    added = sorted(c["citekey"] for c in kept)
    record_deviation(
        deviations_path,
        version=baseline["version"] + 1,
        pre_criteria=round_state["pre_frozen_hash"], post_criteria=round_state["pre_frozen_hash"],
        removed=[], added=added,
        rationale=(
            f"autonomous facet re-search remediation for thin pole {pole!r}: "
            f"authored {len(round_state['new_queries'])} new queries; "
            f"{len(added)} cold-verified paper(s) added "
            f"({len(round_state['off_domain'])} rejected off-domain "
            f"mechanically, {len(cold_rejected_titles)} rejected off-domain "
            f"by the cold verify, {len(round_state['capped'])} capped at "
            f"the floor — all declared in {residue_path.name})."
        ),
        kind=DEVIATION_KIND_WITHIN_FACET_QUERY_APPEND,
        facet_key=pole, new_queries=round_state["new_queries"],
        pre_query_matrix_hash=round_state["pre_query_hash"],
        post_query_matrix_hash=round_state["post_query_hash"],
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

    clear_remediation_round_state(task_dir)

    return {
        "phase": "applied",
        "pole": pole,
        "added": added,
        "uncertain": sorted(uncertain_citekeys),
        "off_domain": round_state["off_domain"] + cold_rejected_titles,
        "capped": round_state["capped"],
        "new_queries": round_state["new_queries"],
        "facet_coverage": updated_coverage,
    }
