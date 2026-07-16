# SPDX-License-Identifier: AGPL-3.0-or-later
"""review/corpus_freeze.py — the explicit, versioned
``corpus_freeze`` baseline + the fail-closed ``rv review refresh`` re-freeze.

Design of record: internal design note.
Builds ON the baseline (``frozen_corpus_citekeys`` in
``run_state.meta``, ``review.autonomy.classify_coverage_gate_with_deviation_check``,
``check_undeclared_deviation``) — does NOT re-implement it.

The freeze precedent mirrored here is ``plan/freeze.py`` (a hash + resolution
pin stored in ``run_state.meta``, re-verified fail-closed at the gate) —
charter §6 reuse-over-create; a sibling module for the corpus, same shape.

``corpus_freeze`` (this module) and ``frozen_corpus_citekeys`` (
``review.autonomy``) are kept IN SYNC deliberately, not merged into one
field: ``frozen_corpus_citekeys`` remains the flat SSOT the already-wired D2
BLOCK (``classify_coverage_gate_with_deviation_check``) reads/writes — that
wiring + its integration tests are untouched by this module. ``corpus_freeze``
is the richer, versioned, hashed wrapper this module adds on top: every time this
module re-freezes (``refresh``/a remediation round), it writes the SAME
citekey set into BOTH ``run_state.meta["corpus_freeze"]["corpus_citekeys"]``
and ``run_state.meta["frozen_corpus_citekeys"]`` — so the next
``classify_coverage_gate_with_deviation_check`` call (unmodified) compares
against the moved-forward baseline, never a stale one.

Stdlib only.
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Any

from ..hashing import hash_file
from ..note import _parse_frontmatter
from ..sources.sweep import group_facet_stances, parse_angle_matrix, parse_sources


class RefreshBlocked(Exception):
    """Raised (never silently swallowed) when ``refresh`` cannot proceed —
    an absent baseline, an undeclared criteria change, or an undeclared
    corpus delta. Refresh can only ACCEPT or REJECT a re-freeze, never
    silently proceed with a partial/degraded one (fail-closed order)."""


# ---------------------------------------------------------------------------
# Criteria-hash canonicalization (the anti-fishing pin)
# ---------------------------------------------------------------------------

def _norm_criteria_value(v: Any) -> str:
    if isinstance(v, list):
        return "|".join(str(x).strip() for x in v)
    return str(v).strip()


# ---------------------------------------------------------------------------
# 0.3.1 tiered-hash split (the search-breadth + facet-coverage redesign's
# crux). Precedent = ``plan/freeze.py``'s ``covers_hash`` vs
# ``covers_retries_hash`` split — charter §6, a sibling shape, not a new
# mechanism.
#
# BEFORE this split, ``canonicalize_criteria`` hashed question + inclusion +
# exclusion + coverage_claim + sources + the FULL query TEXT of
# ``seed_queries:`` in one blob — so ``refresh`` step 3 BLOCKed on ANY
# change to a query string, meaning a facet re-search remediation round
# (which must AUTHOR new queries for a thin pole) could never re-hash
# autonomously; every round would look identical to an undeclared criteria
# edit and demand a human ``criteria-change`` deviation.
#
# Split into two independently-hashed tiers:
#   1. ``criteria_hash`` (frozen bright line, HUMAN-gated) = question +
#      inclusion + exclusion + coverage_claim + sources + the facet KEY SET
#      (sorted top-level ``seed_queries:`` key names — legacy scalar AND
#      nested-facet — plus sorted ``(angle, stance)`` pairs for every
#      DECLARED pole) — NEVER the individual query strings. A NEW facet, a
#      NEW pole, or an edited inclusion/exclusion/sources still changes this
#      hash (a real scope change); appending a query string to an EXISTING
#      declared pole does not.
#   2. ``query_matrix_hash`` (may grow autonomously WITHIN a stable facet
#      key set) = the full ``seed_queries:`` query TEXT, unchanged from the
#      old ``canonicalize_criteria`` behavior for this one field.
# ---------------------------------------------------------------------------

_NESTED_QUERY_KEY_RE = re.compile(r"^(?P<angle>[\w-]+)\.(?P<stance>thesis|counter)\.\d+$")


def _facet_key_set_canon(protocol_text: str) -> str:
    """Canonicalize the SHAPE of ``seed_queries:`` — which top-level angles
    exist (legacy scalar or nested) and which ``(angle, stance)`` poles are
    DECLARED — never the query text itself. This is the facet-key-set half
    of the frozen-tier hash; ``within-facet-query-append``'s structural
    re-gate asserts this canon is byte-identical pre/post a remediation
    round (see ``review.autonomy.record_deviation``)."""
    angle_matrix = parse_angle_matrix(protocol_text)
    legacy_keys = sorted(k for k in angle_matrix if not _NESTED_QUERY_KEY_RE.match(k))
    facets = group_facet_stances(angle_matrix)
    facet_names = sorted(facets.keys())
    pole_pairs = sorted(
        f"{angle}.{stance}"
        for angle, stances in facets.items()
        for stance in ("thesis", "counter")
        if stances[stance]
    )
    return (
        f"legacy_keys={','.join(legacy_keys)}\n"
        f"facet_names={','.join(facet_names)}\n"
        f"poles={','.join(pole_pairs)}"
    )


def canonicalize_frozen_criteria(protocol_text: str) -> str:
    """Canonicalize the FROZEN-TIER criteria fields of ``_protocol.md`` into
    a stable byte form — the human-gated bright line between "denominator"
    (citekey set, may grow if declared) and "criteria" (these hashed bytes).

    question/inclusion/exclusion/coverage_claim/sources (unchanged from the
    pre-0.3.1 ``canonicalize_criteria``) PLUS the facet KEY SET
    (``_facet_key_set_canon``) — NEVER the individual query strings (see
    module-level tiered-hash note above).
    """
    fields, _ = _parse_frontmatter(protocol_text)
    question = _norm_criteria_value(fields.get("question", ""))
    inclusion = _norm_criteria_value(fields.get("inclusion", ""))
    exclusion = _norm_criteria_value(fields.get("exclusion", ""))
    coverage_claim = _norm_criteria_value(fields.get("coverage_claim", ""))

    sources = parse_sources(protocol_text)
    sources_canon = ",".join(sorted(sources))

    facet_key_canon = _facet_key_set_canon(protocol_text)

    return (
        f"question={question}\n"
        f"inclusion={inclusion}\n"
        f"exclusion={exclusion}\n"
        f"coverage_claim={coverage_claim}\n"
        f"sources={sources_canon}\n"
        f"{facet_key_canon}\n"
    )


def canonicalize_query_matrix(protocol_text: str) -> str:
    """Canonicalize the QUERY-TEXT tier of ``seed_queries:`` — the full
    flattened-key -> query-string content, sorted by key. This tier MAY
    grow autonomously (a ``within-facet-query-append`` deviation appends new
    queries under an existing, stable facet key) — unlike
    ``canonicalize_frozen_criteria``, a change here alone never trips the
    human-gated ``criteria-change`` BLOCK."""
    angle_matrix = parse_angle_matrix(protocol_text)
    return "\n".join(f"{k}={angle_matrix[k]}" for k in sorted(angle_matrix))


def check_facet_query_append_re_gate(pre_text: str, post_text: str) -> tuple[bool, str]:
    """Layer 3's STRUCTURAL re-gate (item 4 of the design — name the
    structural fence explicitly, not just "the hash didn't change"):
    verifies a candidate ``within-facet-query-append`` mutation is airtight.

    Asserts, in order:
      1. The frozen-tier canon (``canonicalize_frozen_criteria`` —
         question/inclusion/exclusion/coverage_claim/sources + facet KEY
         SET) is byte-identical pre/post.
      2. The facet NAME set is unchanged (defense-in-depth; already implied
         by 1, checked explicitly for a precise failure message).
      3. Every declared ``(angle, stance)`` query list in ``post_text`` is
         an APPEND-ONLY superset of ``pre_text``'s — the post list's first
         N entries (N = the pre list's length) are BYTE-IDENTICAL to the
         pre list, in order; nothing removed, nothing edited, nothing
         reordered — only new entries appended at the tail.

    This is the STRUCTURAL half of the two-fence design (item 4): it
    catches a NEW facet, a NEW pole, a removed/edited existing query, or a
    changed inclusion/exclusion/sources field. It CANNOT catch a technically
    in-facet query that targets a different population/scope — that is the
    SEMANTIC fence, owned by the frozen inclusion/exclusion criteria plus
    the cold ``review.relevance.classify_relevance_verdict`` off-domain HALT
    screening every newly-surfaced paper downstream (never re-implemented
    here).

    Returns ``(ok, message)`` — never raises; the caller decides whether a
    failed re-gate is a hard abort (Layer 3's round driver treats it as
    exactly that, via ``ValueError``, never a silent proceed).
    """
    pre_frozen = canonicalize_frozen_criteria(pre_text)
    post_frozen = canonicalize_frozen_criteria(post_text)
    if pre_frozen != post_frozen:
        return False, (
            "structural re-gate FAILED: the frozen-tier criteria canon "
            "changed (facet key set, sources, or scope fields) — this is "
            "NOT a pure within-facet query append."
        )

    pre_facets = group_facet_stances(parse_angle_matrix(pre_text))
    post_facets = group_facet_stances(parse_angle_matrix(post_text))

    if set(pre_facets.keys()) != set(post_facets.keys()):
        return False, "structural re-gate FAILED: the facet NAME set changed."

    for angle, pre_stances in pre_facets.items():
        post_stances = post_facets[angle]
        for stance in ("thesis", "counter"):
            pre_list = pre_stances[stance]
            post_list = post_stances[stance]
            if len(post_list) < len(pre_list):
                return False, (
                    f"structural re-gate FAILED: {angle}.{stance}'s query "
                    f"list SHRANK ({len(pre_list)} -> {len(post_list)})."
                )
            if post_list[: len(pre_list)] != pre_list:
                return False, (
                    f"structural re-gate FAILED: {angle}.{stance}'s existing "
                    "queries were edited or reordered — not a pure "
                    "append-at-the-tail."
                )

    return True, "OK"


def hash_criteria_bytes(protocol_path: Path) -> str:
    """``sha256:<hex>`` of the canonicalized FROZEN-TIER criteria bytes of
    ``_protocol.md`` (``canonicalize_frozen_criteria`` — NEVER the query
    text; see the module-level tiered-hash note). A missing protocol hashes
    the empty canonical form (deterministic, never crashes — the absence
    itself will trip other gates, e.g. ``check_protocol_gate``, this
    function's job is only the hash)."""
    text = protocol_path.read_text(encoding="utf-8") if protocol_path.exists() else ""
    canon = canonicalize_frozen_criteria(text)
    return "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()


def hash_query_matrix_bytes(protocol_path: Path) -> str:
    """``sha256:<hex>`` of the canonicalized QUERY-MATRIX tier bytes of
    ``_protocol.md`` (``canonicalize_query_matrix``). This hash is expected
    to CHANGE across a ``within-facet-query-append`` round — it is NOT the
    fail-closed human-gated bright line (that is ``hash_criteria_bytes``)."""
    text = protocol_path.read_text(encoding="utf-8") if protocol_path.exists() else ""
    canon = canonicalize_query_matrix(text)
    return "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Corpus-row parsing helper (lazy import to avoid a review/__init__ cycle)
# ---------------------------------------------------------------------------

def _parse_corpus_citekeys_safe(corpus_path: Path) -> list[str]:
    from . import _parse_corpus_citekeys  # lazy — avoids a module-load cycle

    return _parse_corpus_citekeys(corpus_path)


def _corpus_tagging_safe(corpus_path: Path) -> dict[str, Any]:
    """Informational-only snapshot of the corpus-tagging invariant
    (``review.check_corpus_all_accept_tagged``) at freeze/refresh time —
    NEVER a BLOCK here. ``stamp_corpus_freeze``/``refresh`` must still
    succeed unconditionally right after a facet-remediation/critic-
    backtrack round appends ``[NEEDS-CURATE]`` rows (both round drivers
    call ``refresh`` immediately after their own append, by design — see
    ``review.facet_remediation.run_facet_query_append_round`` and
    ``review.remediation.run_directed_remediation_round``); the CERTIFYING
    gates (``coverage-gate``/``approve-review``, wired in ``dag/verbs.py``)
    are the ones that HALT on this, not the freeze baseline itself. Kept
    here purely so a human/agent inspecting ``run_state.meta["corpus_
    freeze"]`` can see the tagging state without a second round-trip."""
    from . import check_corpus_all_accept_tagged  # lazy — avoids a module-load cycle

    return check_corpus_all_accept_tagged(corpus_path)


# ---------------------------------------------------------------------------
# The explicit, versioned corpus_freeze baseline
# ---------------------------------------------------------------------------

def stamp_corpus_freeze(
    run_state_meta: dict[str, Any],
    *,
    corpus_path: Path,
    protocol_path: Path,
    now: float | None = None,
) -> dict[str, Any]:
    """Idempotent: write ``run_state_meta["corpus_freeze"]`` v1 the FIRST
    time this is called for a given run (mirrors
    ``frozen_corpus_citekeys`` first-stamp semantics). A later call is a
    correct no-op that returns the EXISTING freeze unchanged — re-freezing
    is exclusively ``refresh``'s job (fail-closed, declared-delta-only).

    Also keeps the legacy flat ``frozen_corpus_citekeys`` field in sync on
    first stamp (single-sourced denominator for the already-wired D2 BLOCK).
    """
    existing = run_state_meta.get("corpus_freeze")
    if existing is not None:
        return existing

    citekeys = sorted(_parse_corpus_citekeys_safe(corpus_path))
    tagging = _corpus_tagging_safe(corpus_path)
    freeze = {
        "version": 1,
        "corpus_hash": hash_file(corpus_path) if corpus_path.exists() else "",
        "corpus_citekeys": citekeys,
        "criteria_hash": hash_criteria_bytes(protocol_path),
        # 0.3.1 tiered-hash split: the query-text tier, tracked ALONGSIDE
        # the frozen-tier `criteria_hash` (never merged into it) — see the
        # module-level tiered-hash note above `canonicalize_frozen_criteria`.
        "query_matrix_hash": hash_query_matrix_bytes(protocol_path),
        "corpus_path": str(corpus_path.resolve()) if corpus_path.exists() else str(corpus_path),
        "protocol_path": str(protocol_path.resolve()) if protocol_path.exists() else str(protocol_path),
        "frozen_at": now if now is not None else time.time(),
        # informational only (see _corpus_tagging_safe) — never a block here.
        "all_accept_tagged": tagging["all_tagged"],
        "untagged_citekeys": tagging["untagged_citekeys"],
    }
    run_state_meta["corpus_freeze"] = freeze
    run_state_meta.setdefault("frozen_corpus_citekeys", citekeys)
    return freeze


# ---------------------------------------------------------------------------
# rv review refresh — the fail-closed re-freeze
# ---------------------------------------------------------------------------

_KIND_LINE_RE = re.compile(r"^\*\*Kind:\*\*\s*(.*)$", re.MULTILINE)


def _has_criteria_change_deviation(deviations_path: Path) -> bool:
    """True iff ``_deviations.md`` carries at least one human-authored
    ``kind: criteria-change`` block (``record_deviation(..., kind="criteria-change")``).

    Scoped to the fixed ``**Kind:**`` line ``record_deviation`` writes — not
    a general markdown parser (mirrors ``autonomy._parse_deviation_citekey_deltas``'s
    scoping)."""
    if not deviations_path.exists():
        return False
    text = deviations_path.read_text(encoding="utf-8")
    for m in _KIND_LINE_RE.finditer(text):
        if m.group(1).strip() == "criteria-change":
            return True
    return False


_DEVIATION_HEADER_RE = re.compile(r"^## Deviation v\d+ -> v\d+ \(.*?\)\s*$", re.MULTILINE)
_FACET_KEY_LINE_RE = re.compile(r"^\*\*Facet key:\*\*\s*(.*)$", re.MULTILINE)
_NEW_QUERIES_LINE_RE = re.compile(r'^\s*-\s*"(.*)"\s*$', re.MULTILINE)
_PRE_QM_HASH_LINE_RE = re.compile(r"^\*\*Pre query_matrix_hash:\*\*\s*(.*)$", re.MULTILINE)
_POST_QM_HASH_LINE_RE = re.compile(r"^\*\*Post query_matrix_hash:\*\*\s*(.*)$", re.MULTILINE)


def _iter_within_facet_query_append_deviations(deviations_path: Path) -> list[dict[str, Any]]:
    """Parse every ``kind: within-facet-query-append`` block out of
    ``_deviations.md`` (scoped to the fixed field lines
    ``record_deviation`` itself writes — same scoping discipline as
    ``_has_criteria_change_deviation``/``_parse_deviation_citekey_deltas``,
    never a general markdown parser).

    Returns a list of ``{"facet_key", "new_queries", "pre_query_matrix_hash",
    "post_query_matrix_hash"}`` dicts, one per declared block, in file
    order. A block missing any of these fields is skipped (an honest
    absence, not a fabricated partial record — ``record_deviation`` itself
    never writes a partial ``within-facet-query-append`` block, see its own
    fail-loud invariant)."""
    if not deviations_path.exists():
        return []
    text = deviations_path.read_text(encoding="utf-8")
    headers = list(_DEVIATION_HEADER_RE.finditer(text))
    out: list[dict[str, Any]] = []
    for i, h in enumerate(headers):
        start = h.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        block = text[start:end]
        if "**Kind:** within-facet-query-append" not in block:
            continue
        facet_m = _FACET_KEY_LINE_RE.search(block)
        pre_m = _PRE_QM_HASH_LINE_RE.search(block)
        post_m = _POST_QM_HASH_LINE_RE.search(block)
        if not facet_m or not pre_m or not post_m:
            continue
        new_queries = [m.group(1) for m in _NEW_QUERIES_LINE_RE.finditer(block)]
        out.append({
            "facet_key": facet_m.group(1).strip(),
            "new_queries": new_queries,
            "pre_query_matrix_hash": pre_m.group(1).strip(),
            "post_query_matrix_hash": post_m.group(1).strip(),
        })
    return out


def check_facet_query_append_re_gate_against_deviation_log(
    protocol_path: Path,
    deviations_path: Path,
    *,
    pre_query_matrix_hash: str,
    post_query_matrix_hash: str,
) -> tuple[bool, str]:
    """The out-of-band-edit re-gate (defense-in-depth): ``refresh`` calls
    this whenever the query-matrix tier moved while the frozen tier stayed
    stable, to close the gap that only ``run_facet_query_append_round``'s
    OWN structural re-gate (``check_facet_query_append_re_gate``) was
    enforced at the autonomous-mutation site — an out-of-band MANUAL query
    edit/removal (never routed through that function) would otherwise reach
    ``refresh`` unre-gated.

    Asserts, in order:
      1. A ``within-facet-query-append`` deviation is on record whose
         declared ``(pre_query_matrix_hash, post_query_matrix_hash)`` pair
         is EXACTLY ``(pre_query_matrix_hash, post_query_matrix_hash)`` —
         i.e. the declared block bridges the baseline's query-matrix hash
         to the current one with no gap. Absent -> reject.
      2. That deviation's declared ``new_queries`` are found as an
         append-at-the-TAIL of the CURRENT protocol's query list for the
         declared facet/pole (the same append-only invariant
         ``check_facet_query_append_re_gate`` enforces at round-time,
         re-verified here against the durable record rather than an
         in-memory pre/post pair) — an edited, reordered, or short tail
         -> reject.

    Returns ``(ok, message)`` — never raises.
    """
    matches = [
        d for d in _iter_within_facet_query_append_deviations(deviations_path)
        if d["pre_query_matrix_hash"] == pre_query_matrix_hash
        and d["post_query_matrix_hash"] == post_query_matrix_hash
    ]
    if not matches:
        return False, (
            "structural re-gate FAILED: the query-matrix tier changed "
            f"(hash {pre_query_matrix_hash[:16]}... -> "
            f"{post_query_matrix_hash[:16]}...) with no matching "
            "'within-facet-query-append' deviation on record bridging "
            "exactly that pair — an out-of-band manual query edit/removal "
            "is not distinguishable from a declared append-only round."
        )

    dev = matches[-1]
    m = _POLE_KEY_RE.match(dev["facet_key"]) if dev["facet_key"] else None
    if not m:
        return False, (
            "structural re-gate FAILED: the matching deviation's facet key "
            f"{dev['facet_key']!r} is not shaped '<angle>.(thesis|counter)'."
        )
    angle, stance = m.group("angle"), m.group("stance")
    protocol_text = protocol_path.read_text(encoding="utf-8") if protocol_path.exists() else ""
    current_facets = group_facet_stances(parse_angle_matrix(protocol_text))
    post_list = current_facets.get(angle, {}).get(stance, [])
    new_queries = dev["new_queries"]
    if not new_queries or post_list[-len(new_queries):] != new_queries:
        return False, (
            f"structural re-gate FAILED: the declared new queries for "
            f"{dev['facet_key']!r} are not an append-at-the-tail of the "
            "CURRENT protocol's query list — edited, reordered, or "
            "removed since the deviation was recorded."
        )
    return True, "OK"


_POLE_KEY_RE = re.compile(r"^(?P<angle>[\w-]+)\.(?P<stance>thesis|counter)$")


def refresh(
    run_state_meta: dict[str, Any],
    *,
    corpus_path: Path,
    protocol_path: Path,
    deviations_path: Path,
    now: float | None = None,
) -> dict[str, Any]:
    """Fail-closed re-freeze. Every step can only REJECT
    (``RefreshBlocked``) — refresh never launders an undeclared mutation or
    a criteria edit into a fresh hash.

    Order:
      1. Load the ``corpus_freeze`` baseline. Absent -> BLOCK.
      2. Re-parse ``_corpus.md`` (the hardened parser — a malformed row
         raises ``CorpusSchemaError``, propagated, never silently skipped).
      3. Criteria-hash check (0.3.1: the FROZEN TIER only —
         ``hash_criteria_bytes``/``canonicalize_frozen_criteria`` — NEVER the
         query-text tier; see the module-level tiered-hash note): a changed
         frozen-tier hash with no human ``criteria-change`` deviation on
         record -> BLOCK (the anti-fishing pin firing). A change confined to
         the query-matrix tier (a ``within-facet-query-append`` round) never
         trips this BLOCK on its own — BUT (defense-in-depth hardening) it
         MUST still be re-gated: see 3b below.
      3b. Query-matrix re-gate (hardening — closes the out-of-band-edit
         gap): when the frozen tier is stable but the query-matrix hash
         moved, ``refresh`` no longer admits the delta unconditionally —
         it requires a matching ``within-facet-query-append`` deviation on
         record AND re-runs the append-only structural invariant against
         it (``check_facet_query_append_re_gate_against_deviation_log``).
         Previously this tier's re-gate was enforced ONLY at the
         autonomous mutation site (``run_facet_query_append_round``); an
         out-of-band manual query edit never routed through that function
         would reach here unre-gated. No matching deviation, or a failed
         re-gate -> BLOCK, same teeth as the frozen-tier BLOCK above.
      4. Declared-delta check (``check_undeclared_deviation``, the SAME
         repurposed function the coverage-gate path uses — single-sourced).
         Any undeclared citekey delta -> BLOCK.
      5. Re-freeze: bump version, re-hash, write the new ``corpus_freeze``
         block AND keep ``frozen_corpus_citekeys`` in sync (so the next
         coverage-gate evaluation reads the refreshed baseline, never a
         stale delta).

    Returns the NEW ``corpus_freeze`` block on success.
    Raises ``RefreshBlocked`` on any reject.
    Never touches ``_manuscript.md`` — refresh is review-scoped only; the
    manuscript's own stale-corpus guard (``manuscript.check_gates.check_coverage_gate``)
    re-binds on its own next run (cascade note).
    """
    from .autonomy import check_undeclared_deviation

    baseline = run_state_meta.get("corpus_freeze")
    if baseline is None:
        raise RefreshBlocked(
            "rv review refresh: BLOCKED — no corpus_freeze baseline in "
            "run_state.meta. Run coverage-gate at least once to establish "
            "the initial freeze before refreshing."
        )

    current_citekeys = set(_parse_corpus_citekeys_safe(corpus_path))  # CorpusSchemaError propagates

    current_criteria_hash = hash_criteria_bytes(protocol_path)
    if current_criteria_hash != baseline["criteria_hash"]:
        if not _has_criteria_change_deviation(deviations_path):
            raise RefreshBlocked(
                "rv review refresh: BLOCKED — the frozen _protocol.md "
                f"criteria changed (criteria_hash {baseline['criteria_hash'][:16]}... "
                f"-> {current_criteria_hash[:16]}...) with no human-authored "
                "'criteria-change' deviation recorded in "
                f"{deviations_path}. A criteria edit cannot be re-frozen as "
                "a within-criteria refresh — record a criteria-change "
                "deviation (record_deviation(..., kind='criteria-change')) "
                "first, or revert the protocol edit."
            )
    else:
        current_query_matrix_hash = hash_query_matrix_bytes(protocol_path)
        baseline_query_matrix_hash = baseline.get("query_matrix_hash")
        if (
            baseline_query_matrix_hash is not None
            and current_query_matrix_hash != baseline_query_matrix_hash
        ):
            ok, msg = check_facet_query_append_re_gate_against_deviation_log(
                protocol_path, deviations_path,
                pre_query_matrix_hash=baseline_query_matrix_hash,
                post_query_matrix_hash=current_query_matrix_hash,
            )
            if not ok:
                raise RefreshBlocked(f"rv review refresh: BLOCKED — {msg}")

    ok, msg = check_undeclared_deviation(
        set(baseline["corpus_citekeys"]), current_citekeys, deviations_path,
    )
    if not ok:
        raise RefreshBlocked(f"rv review refresh: BLOCKED — {msg}")

    tagging = _corpus_tagging_safe(corpus_path)
    new_freeze = {
        "version": baseline["version"] + 1,
        "corpus_hash": hash_file(corpus_path),
        "corpus_citekeys": sorted(current_citekeys),
        "criteria_hash": current_criteria_hash,
        "query_matrix_hash": hash_query_matrix_bytes(protocol_path),
        "corpus_path": str(corpus_path.resolve()),
        "protocol_path": str(protocol_path.resolve()) if protocol_path.exists() else str(protocol_path),
        "frozen_at": now if now is not None else time.time(),
        # informational only (see _corpus_tagging_safe) — never a block here.
        "all_accept_tagged": tagging["all_tagged"],
        "untagged_citekeys": tagging["untagged_citekeys"],
    }
    run_state_meta["corpus_freeze"] = new_freeze
    run_state_meta["frozen_corpus_citekeys"] = new_freeze["corpus_citekeys"]
    return new_freeze


# ---------------------------------------------------------------------------
# CLI entry point — `rv review refresh <scope>` (in-process callable too, the
# remediation loop must not shell out)
# ---------------------------------------------------------------------------

def declare_delta(
    run_state_meta: dict[str, Any],
    *,
    corpus_path: Path,
    deviations_path: Path,
    rationale: str,
    now: Any = None,
) -> dict[str, Any] | None:
    """Section F(a) — a HUMAN-invoked convenience verb, never called from
    the autonomous remediation loop. Computes ``baseline_citekeys -
    current_citekeys`` (the corpus rows dropped since the last freeze) and
    writes a DECLARED, human-authored ``kind="criteria-change"`` deviation
    block capturing exactly that removed set — automating the keystrokes of
    hand-writing a removed-set into ``_deviations.md`` (the run's actual
    pain point; see the design note above ``RefreshBlocked``).

    This does NOT give the autonomous loop shrink power. It reuses
    ``record_deviation``/``check_undeclared_deviation`` VERBATIM — no gate
    is loosened, no new autonomous deviation ``kind`` is added. The two
    self-authorable kinds (``within-criteria-append``,
    ``within-facet-query-append``) still assert ``removed == []`` inside
    ``record_deviation`` itself (D2's structural invariant, untouched by
    this function) — a removal can ONLY ever be written as
    ``criteria-change``, and this function only ever calls
    ``record_deviation`` with that kind, from a human-invoked CLI path
    (``rv review declare-delta``), never from ``review.remediation`` /
    ``review.facet_remediation``'s autonomous rounds.

    ``rationale`` is REQUIRED and must be human-supplied — never
    auto-fabricated (charter §1: no invented justification for a removal).

    Returns ``None`` (writes NOTHING — no vacuous deviation) when the delta
    is empty. Otherwise returns ``{"removed": [...], "block": <appended
    text>}``. Does not itself re-freeze — the human's next
    ``rv review refresh`` call moves the baseline forward once the delta is
    declared (mirrors the existing declare-then-refresh flow every other
    deviation kind already uses).
    """
    if not rationale or not rationale.strip():
        raise ValueError(
            "declare_delta: a human-supplied rationale is required — "
            "refusing to auto-fabricate one for a corpus removal."
        )

    baseline = run_state_meta.get("corpus_freeze")
    if baseline is None:
        raise RefreshBlocked(
            "rv review declare-delta: BLOCKED — no corpus_freeze baseline "
            "in run_state.meta. Run coverage-gate at least once to "
            "establish the initial freeze before declaring a delta."
        )

    baseline_citekeys = set(baseline["corpus_citekeys"])
    current_citekeys = set(_parse_corpus_citekeys_safe(corpus_path))  # CorpusSchemaError propagates
    removed = sorted(baseline_citekeys - current_citekeys)
    if not removed:
        return None

    from .autonomy import DEVIATION_KIND_CRITERIA_CHANGE, record_deviation

    criteria_snapshot = baseline.get("criteria_hash", "")
    block = record_deviation(
        deviations_path,
        version=int(baseline.get("version", 1)) + 1,
        pre_criteria=criteria_snapshot,
        post_criteria=criteria_snapshot,
        removed=removed,
        added=[],
        rationale=rationale.strip(),
        kind=DEVIATION_KIND_CRITERIA_CHANGE,
        now=now,
    )
    return {"removed": removed, "block": block}


# ---------------------------------------------------------------------------
# CLI entry point — `rv review declare-delta <scope>` (Section F(a))
# ---------------------------------------------------------------------------

def cmd_declare_delta(
    project: str, scope: str, rationale: str, *, config: Any = None,
) -> dict[str, Any] | None:
    """Resolve the review run's ``run_state``, call ``declare_delta``. Never
    persists ``run_state`` — ``declare_delta`` does not mutate
    ``run_state.meta`` (it only appends to ``_deviations.md``; the actual
    baseline re-freeze remains ``rv review refresh``'s job).

    Raises ``RefreshBlocked`` (propagated) on no baseline;
    ``research_vault.dag.store.StoreError`` if the run isn't found;
    ``ValueError`` on a missing/blank rationale.
    """
    from ..config import load_config
    from ..dag.store import RunStore
    from . import _review_artifact_dir

    cfg = config or load_config()
    run_id = f"review-{scope}-phase1"
    store = RunStore.from_config(cfg)
    run_state = store.load(run_id)

    review_dir = _review_artifact_dir(project, scope, cfg)
    corpus_path = review_dir / "_corpus.md"
    deviations_path = review_dir / "_deviations.md"

    return declare_delta(
        run_state.meta,
        corpus_path=corpus_path,
        deviations_path=deviations_path,
        rationale=rationale,
    )


def cmd_refresh(project: str, scope: str, *, config: Any = None) -> dict[str, Any]:
    """Resolve the review run's ``run_state``, call ``refresh``, persist.

    Mirrors the Phase-1 run_id convention (``review._build_phase1_manifest``:
    ``run_id = f"review-{scope_id}-phase1"``) rather than hand-rolling a
    second lookup. Raises ``RefreshBlocked`` (propagated) on any reject;
    ``research_vault.dag.store.StoreError`` if the run isn't found.
    """
    from ..config import load_config
    from ..dag.store import RunStore
    from . import _review_artifact_dir

    cfg = config or load_config()
    run_id = f"review-{scope}-phase1"
    store = RunStore.from_config(cfg)
    run_state = store.load(run_id)

    review_dir = _review_artifact_dir(project, scope, cfg)
    corpus_path = review_dir / "_corpus.md"
    protocol_path = review_dir / "_protocol.md"
    deviations_path = review_dir / "_deviations.md"

    new_freeze = refresh(
        run_state.meta,
        corpus_path=corpus_path,
        protocol_path=protocol_path,
        deviations_path=deviations_path,
    )
    store.save(run_state)
    return new_freeze
