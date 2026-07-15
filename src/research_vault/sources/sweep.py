# SPDX-License-Identifier: AGPL-3.0-or-later
"""sources/sweep.py — the parallel width-sweep orchestrator (NG-3).

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
(graceful degradation, risk: "an adapter down must degrade gracefully").
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .base import NotSupported, PaperHit, SourceAdapter, format_poles, format_rerank_score
from .dedup import DedupedHit, dedup_hits, identity_key
from .derivative import count_independent, mark_derivatives
from .ranker import UtilityScore, rank_and_select, score_hit
from .registry import DEFAULT_SOURCES, get_adapter

DEFAULT_FETCH_BUDGET = 100 # D-1: HR's diminishing-returns cap (~100 planned
# searches); raised from 65 now that the facet-matrix generator (see
# `parse_angle_matrix`/`group_facet_stances` below) derives ~40-100 queries
# per protocol instead of the old fixed 5-angle set — the old 65 cap would
# silently truncate a properly-broad matrix before it ever reached the width
# sweep.

# Retry-with-backoff on a transient adapter/cell failure (pre-publish
# hardening batch, a downstream project's live-e2e-run finding 2026-07-09: all 5 arXiv cells timed
# out in one run and the sweep degraded them to zero with no retry — a
# single transient network blip looked identical to a genuinely-dead
# adapter). Bounded — never infinite — so a cell always terminates: at most
# ``_CELL_RETRY_ATTEMPTS`` total tries, exponential backoff seeded by
# ``_CELL_RETRY_BACKOFF_BASE`` seconds (0.5s, 1s — capped, two sleeps
# between three tries).
_CELL_RETRY_ATTEMPTS = 3
_CELL_RETRY_BACKOFF_BASE = 0.5


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

    Two shapes, mixable in the SAME protocol (a to-be-migrated protocol is
    never forced to rewrite every angle in one pass):

    **Legacy scalar** (pre-, one query per angle)::

        seed_queries:
          by-method:     "<query>"
          by-outcome:    "<query>"

    Returned unchanged: ``{"by-method": "<query>", "by-outcome": "<query>"}``.

    **Nested stance-tagged facet** (D-3 — the facet-matrix generator's
    output; the researcher's Step-C counter-position facets as a first-class class)::

        seed_queries:
          by-temporal:
            thesis:
              - "<drift query 1>"
              - "<drift query 2>"
            counter:
              - "<stability query 1>"

    is FLATTENED into distinct keys — one per enumerated query —
    ``"by-temporal.thesis.0"``, ``"by-temporal.thesis.1"``,
    ``"by-temporal.counter.0"``. This is the reuse move (charter §6):
    ``run_width_sweep``'s ``(angle-key x source)`` cross-product and
    ``corpus_freeze.canonicalize_criteria``'s sorted-key hash canon both
    consume the plain ``dict[str, str]`` return unchanged — no second parse,
    no restructuring of the concurrency/hashing machinery, just more/richer
    flat keys. Use ``group_facet_stances()`` when the FACET STRUCTURE
    (thesis/counter grouping, not just the flat query list) is needed — e.g.
    the D-7 empty-counter-pole gate, or the D-6 cold counter-facet guard.

    Returns ``{}`` if ``seed_queries:`` is absent or the legacy FLAT-LIST
    shape (a bare list under ``seed_queries:``, no per-angle keys at all) —
    callers must treat an empty return as "no angle matrix; fall back to
    legacy handling", never crash.
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
    current_angle: str | None = None
    current_stance: str | None = None
    angle_indent: int | None = None

    for line in lines:
        if line.strip() == "":
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        if indent == 0:
            # top-level key line — (re)entering or leaving the seed_queries block
            in_block = stripped.rstrip(":") == "seed_queries" and stripped.endswith(":")
            current_angle = None
            current_stance = None
            angle_indent = None
            continue
        if not in_block:
            continue

        if stripped.startswith("- "):
            item = stripped[2:].strip()
            if item.startswith(("'", '"')) and item.endswith(item[0]) and len(item) >= 2:
                item = item[1:-1]
            if current_angle is not None and current_stance is not None:
                prefix = f"{current_angle}.{current_stance}."
                idx = sum(1 for k in out if k.startswith(prefix))
                out[f"{prefix}{idx}"] = item
            continue

        m = _KV_RE.match(stripped)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val.startswith(("'", '"')) and val.endswith(val[0]) and len(val) >= 2:
            val = val[1:-1]

        if angle_indent is None or indent <= angle_indent:
            # angle-level key: first indented line establishes the depth;
            # any sibling line back at that SAME depth is a new angle.
            angle_indent = indent
            current_angle = key
            current_stance = None
            if val:
                # legacy scalar leaf — no nested thesis/counter children.
                out[key] = val
                current_angle = None
            continue

        if key in ("thesis", "counter"):
            current_stance = key
            if val:
                # rare inline-scalar-under-stance form — treat as sole item.
                prefix = f"{current_angle}.{key}."
                idx = sum(1 for k in out if k.startswith(prefix))
                out[f"{prefix}{idx}"] = val
            continue

    return out


# ---------------------------------------------------------------------------
# Layer 3 (0.3.1) — the append-only protocol-text writer. The COUNTERPART to
# `parse_angle_matrix` above: appends new query strings under an ALREADY-
# DECLARED ``(angle, stance)`` pole's ``- "..."`` list, in place, leaving
# every other byte of the frontmatter untouched (never a full YAML
# re-render — this file has no yaml dependency and the parser above is
# deliberately a narrow, hand-rolled walk, not a general parser).
# ---------------------------------------------------------------------------

def append_queries_to_protocol_text(
    protocol_text: str, angle: str, stance: str, new_queries: list[str],
) -> str:
    """Return NEW protocol text with ``new_queries`` appended under the
    ALREADY-DECLARED ``seed_queries.<angle>.<stance>`` list (matches the
    indentation of the existing items when any exist; falls back to
    ``stance_indent + 2`` when the pole is declared with a stance key but
    ZERO items yet).

    Pure (never mutates ``protocol_text`` or writes a file) — callers pair
    this with ``review.corpus_freeze``'s structural re-gate BEFORE writing
    the result to disk, so a violated invariant never reaches the file.

    Raises ``ValueError`` if ``angle``/``stance`` is not a declared pole in
    ``protocol_text`` at all (this function can only APPEND to an existing
    pole, it can never author a new facet/pole — that would be exactly the
    fishing hole the frozen-tier hash exists to close).
    """
    if not new_queries:
        return protocol_text
    if not protocol_text.startswith("---"):
        raise ValueError("append_queries_to_protocol_text: no frontmatter block found")
    end = protocol_text.find("\n---", 3)
    if end == -1:
        raise ValueError("append_queries_to_protocol_text: no frontmatter block found")
    fm_block = protocol_text[3:end]
    rest = protocol_text[end:]

    lines = fm_block.split("\n")
    in_block = False
    current_angle: str | None = None
    current_stance: str | None = None
    angle_indent: int | None = None
    stance_indent: int | None = None
    stance_line_idx: int | None = None
    last_item_idx: int | None = None
    last_item_indent: int | None = None
    found_pole = False

    for i, line in enumerate(lines):
        if line.strip() == "":
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()

        if indent == 0:
            in_block = stripped.rstrip(":") == "seed_queries" and stripped.endswith(":")
            current_angle = None
            current_stance = None
            angle_indent = None
            stance_indent = None
            continue
        if not in_block:
            continue

        if stripped.startswith("- "):
            if current_angle == angle and current_stance == stance:
                last_item_idx = i
                last_item_indent = indent
            continue

        m = _KV_RE.match(stripped)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()

        if angle_indent is None or indent <= angle_indent:
            angle_indent = indent
            current_angle = key
            current_stance = None
            if val:
                current_angle = None  # legacy scalar leaf, no thesis/counter children
            continue

        if key in ("thesis", "counter"):
            current_stance = key
            stance_indent = indent
            if current_angle == angle and current_stance == stance:
                found_pole = True
                stance_line_idx = i
            continue

    if not found_pole:
        raise ValueError(
            f"append_queries_to_protocol_text: pole {angle!r}.{stance!r} is "
            "not a DECLARED pole in this protocol's seed_queries: — this "
            "function can only APPEND to an EXISTING pole, never author a "
            "new facet/pole."
        )

    if last_item_idx is not None:
        insert_at = last_item_idx + 1
        insert_indent = last_item_indent
    else:
        # the pole is declared (a `thesis:`/`counter:` key exists) but has
        # ZERO items yet — insert right after the stance line itself.
        insert_at = stance_line_idx + 1  # type: ignore[operator]
        insert_indent = (stance_indent or 0) + 2

    new_lines = [f'{" " * insert_indent}- "{q}"' for q in new_queries]
    lines[insert_at:insert_at] = new_lines

    new_fm_block = "\n".join(lines)
    return protocol_text[:3] + new_fm_block + rest


_FACET_KEY_RE = re.compile(r"^(?P<angle>[\w-]+)\.(?P<stance>thesis|counter)\.(?P<idx>\d+)$")


def group_facet_stances(angle_matrix: dict[str, str]) -> dict[str, dict[str, list[str]]]:
    """Group a flattened ``parse_angle_matrix`` result back into stance-tagged
    facets: ``{angle: {"thesis": [...], "counter": [...]}}``, ordered by the
    flattened index (so query order is preserved, not re-sorted).

    Legacy scalar keys (no ``.thesis.``/``.counter.`` suffix) never declared
    a counter-pole — they are simply ABSENT from the returned mapping, never
    surfaced as an empty facet (which would wrongly make them eligible for
    the D-7 empty-counter-pole gate below).
    """
    buckets: dict[str, dict[str, dict[int, str]]] = {}
    for key, val in angle_matrix.items():
        m = _FACET_KEY_RE.match(key)
        if not m:
            continue
        angle = m.group("angle")
        stance = m.group("stance")
        idx = int(m.group("idx"))
        buckets.setdefault(angle, {"thesis": {}, "counter": {}})[stance][idx] = val

    return {
        angle: {
            "thesis": [stances["thesis"][i] for i in sorted(stances["thesis"])],
            "counter": [stances["counter"][i] for i in sorted(stances["counter"])],
        }
        for angle, stances in buckets.items()
    }


def seed_queries_declared_but_unparsed(protocol_text: str) -> bool:
    """True iff the protocol declares a ``seed_queries:`` key at all, but
    ``parse_angle_matrix`` yields ZERO usable queries (architect fit-check
    finding, judge-independent fail-open): a malformed/mis-indented
    nested block, or an otherwise-garbage ``seed_queries:`` value, can
    silently collapse to ``{}`` — and an empty facet-iteration loop at
    ``approve-protocol`` then looks IDENTICAL to "this protocol has no
    counter-facets to check", clearing BOTH the D-7 existence gate and the
    D-6 strength guard for a protocol that never actually froze anything
    usable.

    A protocol that never declares ``seed_queries:`` at all is a DIFFERENT,
    already-handled case (``run_sweep_from_protocol`` raises ``ValueError``
    on a wholly-absent angle matrix) — this check is scoped to "declared but
    empty", not "absent".
    """
    if not protocol_text.startswith("---"):
        return False
    end = protocol_text.find("\n---", 3)
    if end == -1:
        return False
    fm_block = protocol_text[3:end]

    declared = False
    for line in fm_block.splitlines():
        stripped = line.strip()
        if stripped == "":
            continue
        if not line.startswith((" ", "\t")) and stripped.rstrip(":") == "seed_queries" and stripped.endswith(":"):
            declared = True
            break

    if not declared:
        return False
    return len(parse_angle_matrix(protocol_text)) == 0


# ---------------------------------------------------------------------------
# Query-time near-dup filter (D-4) + post-dedup distinct-query count/band
# (D-1, friction iii): a semantic (asta/S2) backend collapses near-literal
# combinatorial restatements to one NL query, so the 40-100 breadth
# assertion must hold on the POST-dedup distinct count, never the raw
# enumerated-cell count — the pairwise C(facets,2) combinatorics can
# overstate distinct coverage on paper.
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def dedupe_near_duplicate_queries(queries: list[str], *, threshold: float = 0.9) -> list[str]:
    """Drop a query that is a near-literal restatement of one already kept,
    via a cheap token-Jaccard similarity (stdlib only — no embedding call at
    query-generation time). This is the literal-restatement floor, NOT the
    semantic layer — result-pool dedup (``angles_by_identity`` /
    ``dedup_hits`` in ``compose_sweep_result``) already handles the same
    paper surfaced by two DIFFERENT-worded queries; this only avoids firing
    two near-identical queries in the first place and wasting fetch budget.

    Order-preserving: the first-seen phrasing of a near-dup cluster wins.
    """
    kept: list[str] = []
    kept_tokensets: list[set[str]] = []
    for q in queries:
        tokens = set(_TOKEN_RE.findall(q.lower()))
        is_dup = False
        for existing in kept_tokensets:
            if not tokens or not existing:
                continue
            jaccard = len(tokens & existing) / len(tokens | existing)
            if jaccard >= threshold:
                is_dup = True
                break
        if not is_dup:
            kept.append(q)
            kept_tokensets.append(tokens)
    return kept


def count_distinct_queries(angle_matrix: dict[str, str], *, near_dup_threshold: float = 0.9) -> int:
    """The post-dedup distinct query count the 40-100 band assertion (D-1)
    must be checked against — never the raw ``len(angle_matrix)``."""
    return len(dedupe_near_duplicate_queries(list(angle_matrix.values()), threshold=near_dup_threshold))


MATRIX_BAND_LO = 40
MATRIX_BAND_HI = 100


def validate_matrix_band(
    angle_matrix: dict[str, str],
    *,
    lo: int = MATRIX_BAND_LO,
    hi: int = MATRIX_BAND_HI,
    near_dup_threshold: float = 0.9,
) -> tuple[bool, str]:
    """Non-blocking band check (SIGNAL at `approve-protocol`, never a hard
    BLOCK — D-1 recommends the derived ~40-100 count as a target the
    generator should land near, not an exact requirement every RQ must hit).

    Returns ``(in_band, message)``.
    """
    n = count_distinct_queries(angle_matrix, near_dup_threshold=near_dup_threshold)
    if n < lo:
        return False, (
            f"query matrix has only {n} distinct queries post-dedup "
            f"(target band: {lo}-{hi}) — likely too narrow for HR-scale breadth"
        )
    if n > hi:
        return False, (
            f"query matrix has {n} distinct queries post-dedup "
            f"(target band: {lo}-{hi}) — likely over the diminishing-returns cap"
        )
    return True, f"{n} distinct queries post-dedup (in the {lo}-{hi} target band)"


# ---------------------------------------------------------------------------
# Layer 1 (0.3.1) — the generation-time facet-BREADTH floor, a HARD BLOCK at
# approve-protocol. `dedupe_near_duplicate_queries` above (query-time,
# stdlib token-Jaccard, no domain-vocab awareness) is a rejects-only SCREEN
# here — the real breadth guarantee is Layer 2 (result-pool distinct-paper
# coverage, below). Tightened by stripping the frozen protocol's own
# domain-vocabulary (shared jargon every query in-scope legitimately
# repeats) before computing Jaccard, so shared jargon alone never inflates
# apparent distinctness between two genuinely-different queries.
# ---------------------------------------------------------------------------

def _dedupe_domain_stripped(
    queries: list[str], domain_vocab: frozenset[str] | set[str], *, threshold: float = 0.9,
) -> list[str]:
    """Same order-preserving near-dup collapse as
    ``dedupe_near_duplicate_queries``, but the Jaccard token sets have the
    frozen protocol's domain vocabulary (question/inclusion/exclusion/
    coverage_claim tokens — ``review.relevance._domain_vocabulary``)
    subtracted first. Reuses ``review.relevance._tokenize`` (charter §6 —
    the SAME stopword-aware tokenizer the relevance gate itself judges
    with; no second tokenizer)."""
    from research_vault.review.relevance import _tokenize as _rel_tokenize

    kept: list[str] = []
    kept_tokensets: list[set[str]] = []
    for q in queries:
        tokens = _rel_tokenize(q) - domain_vocab
        is_dup = False
        for existing in kept_tokensets:
            if not tokens or not existing:
                continue
            jaccard = len(tokens & existing) / len(tokens | existing)
            if jaccard >= threshold:
                is_dup = True
                break
        if not is_dup:
            kept.append(q)
            kept_tokensets.append(tokens)
    return kept


def check_facet_breadth_floor(
    protocol_text: str,
    *,
    min_per_facet: int = 3,
    min_per_pole: int = 2,
    near_dup_threshold: float = 0.9,
) -> tuple[bool, str]:
    """Layer 1 (0.3.1): the generation-time facet-breadth HARD BLOCK.

    Scoped to ``group_facet_stances``' output ONLY (the nested D-3
    thesis/counter facet form) — mirrors D-7's own scoping (a purely-legacy
    scalar matrix declares no pole structure at all and has nothing for this
    floor to check; see ``group_facet_stances``' docstring).

    For every facet:
      - a DECLARED counter pole (non-empty ``counter`` list) -> BOTH poles
        are checked against ``min_per_pole`` (M) INDEPENDENTLY — never
        summed against ``min_per_facet`` (non-additive; a 2-pole facet
        needs >= 2M queries, which already exceeds N by the shipped
        defaults).
      - NO declared counter pole (a thesis-only facet — defense-in-depth:
        by construction this should already have been rejected by D-7
        upstream at the SAME approve-protocol gate, but this function does
        not assume gate ORDER) -> the facet's total distinct query count is
        checked against ``min_per_facet`` (N).

    Distinctness is measured POST domain-vocab-stripped near-dup collapse
    (``_dedupe_domain_stripped``) — a rejects-only screen (item 3 of the
    design); the real breadth guarantee is Layer 2's result-pool distinct-
    paper count, not this generation-time query count.

    Returns ``(ok, message)`` — ``ok`` is False when any facet/pole reads
    thin; the message names every thin facet/pole (never just the first,
    charter §2 surface-don't-drop).
    """
    from research_vault.note import _parse_frontmatter
    from research_vault.review.relevance import _domain_vocabulary

    fields, _ = _parse_frontmatter(protocol_text)
    criteria = {
        key: fields.get(key, "")
        for key in ("question", "inclusion", "exclusion", "coverage_claim")
    }
    domain_vocab = _domain_vocabulary(criteria)

    angle_matrix = parse_angle_matrix(protocol_text)
    facets = group_facet_stances(angle_matrix)

    thin: list[str] = []
    for angle in sorted(facets):
        stances = facets[angle]
        thesis_kept = _dedupe_domain_stripped(stances["thesis"], domain_vocab, threshold=near_dup_threshold)
        counter_kept = _dedupe_domain_stripped(stances["counter"], domain_vocab, threshold=near_dup_threshold)

        if stances["counter"]:
            if len(thesis_kept) < min_per_pole:
                thin.append(
                    f"{angle}.thesis: {len(thesis_kept)} distinct query(ies) "
                    f"(need >= {min_per_pole})"
                )
            if len(counter_kept) < min_per_pole:
                thin.append(
                    f"{angle}.counter: {len(counter_kept)} distinct query(ies) "
                    f"(need >= {min_per_pole})"
                )
        else:
            total_kept = len(thesis_kept)
            if total_kept < min_per_facet:
                thin.append(
                    f"{angle}: {total_kept} distinct query(ies) (need >= {min_per_facet})"
                )

    if thin:
        return False, (
            "rv dag approve: facet-breadth floor BLOCKED — the frozen "
            "query matrix under-samples the following facet(s)/pole(s): "
            f"{'; '.join(thin)}.\n"
            "Fix: add more DISTINCT (non-near-dup, non-domain-jargon-only) "
            "queries under the named facet/pole in `seed_queries:`, then "
            "re-run `rv dag approve <run_id> approve-protocol`."
        )
    return True, "OK"


# ---------------------------------------------------------------------------
# Layer 2 (0.3.1) — result-time facet-coverage: count result-pool-DEDUPED
# DISTINCT PAPERS per declared pole (never raw hit rows — 3 near-dup queries
# returning the same 2 papers must read as 2, not 3; the load-bearing
# breadth guarantee, unlike Layer 1's rejects-only query-count screen).
# ---------------------------------------------------------------------------

def compute_facet_pole_coverage(cells: list["SweepCell"]) -> dict[str, set[str]]:
    """Per-pole (``"<angle>.<stance>"``) DISTINCT-PAPER identity sets across
    the FULL fetched result pool (every non-errored cell — pre-budget,
    pre-rank-and-select; the acceptance criterion is the result POOL, not
    the final kept/budget-selected subset).

    Keyed by the facet+stance, NOT the per-query flattened key
    (``<angle>.<stance>.<idx>``) — every ``(angle, stance, idx)`` cell for
    the SAME pole accumulates onto the SAME identity set via
    ``sources.dedup.identity_key``, so a paper surfaced by 3 near-dup
    queries under one pole counts ONCE, never 3.

    A legacy (non-thesis/counter) cell's ``angle`` never matches
    ``_FACET_KEY_RE`` and is silently excluded (facet-coverage is scoped to
    the nested D-3 form only — mirrors Layer 1 / D-7's own scoping).
    """
    coverage: dict[str, set[str]] = {}
    for cell in cells:
        if cell.error:
            continue
        m = _FACET_KEY_RE.match(cell.angle)
        if not m:
            continue
        pole_key = f"{m.group('angle')}.{m.group('stance')}"
        bucket = coverage.setdefault(pole_key, set())
        for hit in cell.hits:
            bucket.add(identity_key(hit))
    return coverage


def check_facet_coverage(
    angle_matrix: dict[str, str],
    cells: list["SweepCell"],
    *,
    min_hits_per_pole: int = 3,
) -> dict[str, Any]:
    """Layer 2 facet-coverage payload — every DECLARED pole (from
    ``group_facet_stances(angle_matrix)``) is seeded into the result with a
    count, even one that never surfaced a SINGLE hit (a pole absent from
    ``compute_facet_pole_coverage``'s output because no cell for it ever
    matched must never look identical to "no facet-coverage information
    exists" — charter §2, it must read as count 0, a maximally-thin pole).

    Returns ``{"pole_counts": {pole: int}, "thin_poles": [pole, ...],
    "min_hits_per_pole": int}`` — ``thin_poles`` sorted, every pole with
    ``count < min_hits_per_pole``.
    """
    facets = group_facet_stances(angle_matrix)
    declared_poles = sorted(
        f"{angle}.{stance}"
        for angle, stances in facets.items()
        for stance in ("thesis", "counter")
        if stances[stance]
    )
    coverage = compute_facet_pole_coverage(cells)
    pole_counts = {pole: len(coverage.get(pole, set())) for pole in declared_poles}
    thin_poles = sorted(p for p, c in pole_counts.items() if c < min_hits_per_pole)
    return {
        "pole_counts": pole_counts,
        "thin_poles": thin_poles,
        "min_hits_per_pole": min_hits_per_pole,
    }


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


def _stamp_rerank_scores(hits: list[PaperHit], query: str) -> None:
    """A1 (task #86): TF-IDF-rerank ``hits`` against ``query`` and stamp
    each hit's ``rerank_score`` in place.

    Reuses ``cross_project.rank_candidates`` — the SAME TF-IDF cosine-
    similarity scorer ``rv research find``'s existing ``--rerank`` already
    applies at ad-hoc search time (charter §6: one scorer, not two
    diverging re-implementations). ``min_score=0.0``/``top_k=len(hits)``
    means every hit is scored and none is dropped — this call only
    ANNOTATES a strength signal, it must never filter/reorder the sweep's
    own kept set (that stays ``ranker.rank_and_select``'s job).

    A hit whose title+abstract is empty gets an empty TF-IDF document —
    ``rank_candidates`` still returns a (typically 0.0) score for it, never
    a crash; that is a REAL (if weak) score, distinct from the ``None``
    "never went through a rerank pass at all" sentinel other code paths
    (e.g. the citation-neighbor walk) use.
    """
    if not hits:
        return
    from ..cross_project import rank_candidates  # lazy — avoid a needless import at module load

    candidates = [
        {
            "idx": i,
            "body": (h.title or "") + ("\n" + h.abstract if h.abstract else ""),
        }
        for i, h in enumerate(hits)
    ]
    scored = rank_candidates(query, candidates, min_score=0.0, top_k=len(candidates))
    for c in scored:
        hits[c["idx"]].rerank_score = c["score"]


def _fetch_cell(
    angle: str,
    query: str,
    source: str,
    *,
    limit: int,
    retry_attempts: int = _CELL_RETRY_ATTEMPTS,
    backoff_base: float = _CELL_RETRY_BACKOFF_BASE,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> SweepCell:
    """Fetch one ``(angle, source)`` cell, retrying a TRANSIENT adapter
    failure with bounded exponential backoff before degrading the cell.

    ``NotSupported`` and an unknown-adapter-name ``ValueError`` are NEVER
    retried — both are permanent signals (the source genuinely doesn't
    support this op / the name is a protocol typo), not a transient network
    blip; retrying them would just burn the backoff budget for no chance of
    success. Every other exception (timeout, connection error, 5xx, ...) is
    treated as transient and retried up to ``retry_attempts`` times; only
    after the LAST attempt still fails does the cell record ``error`` and
    degrade to zero hits (graceful degradation — unchanged contract,
    just no longer on the FIRST transient blip).
    """
    try:
        adapter: SourceAdapter = get_adapter(source)
    except ValueError as e:
        return SweepCell(angle=angle, query=query, source=source, error=str(e))

    last_error: Exception | None = None
    for attempt in range(retry_attempts):
        try:
            hits = adapter.search(query, limit=limit)
            # A1 (task #86): rerank at the source — TF-IDF-score each hit
            # against the query that fetched it, so a strength signal
            # survives all the way to `_corpus.md` (Section C). Stamping
            # here, not in `compose_sweep_result`, means the score is
            # always against the SPECIFIC angle-query that surfaced this
            # exact hit (a paper surfaced by two cells gets two candidate
            # scores; dedup's first-seen-wins keeps whichever one the
            # representative hit carried — documented on `dedup_hits`).
            _stamp_rerank_scores(hits, query)
            return SweepCell(angle=angle, query=query, source=source, hits=hits)
        except NotSupported as e:
            return SweepCell(angle=angle, query=query, source=source, error=str(e))
        except Exception as e:  # noqa: BLE001 — retried transient; degrades only after exhaustion
            last_error = e
            if attempt < retry_attempts - 1:
                sleep_fn(backoff_base * (2 ** attempt))
    return SweepCell(
        angle=angle, query=query, source=source,
        error=f"{type(last_error).__name__}: {last_error} (after {retry_attempts} attempts)",
    )


def run_width_sweep(
    angle_matrix: dict[str, str],
    sources: list[str],
    *,
    per_cell_limit: int = 20,
    max_workers: int = 8,
    sleep_fn: Callable[[float], None] = time.sleep,
    dedupe_queries: bool = True,
    near_dup_threshold: float = 0.9,
) -> list[SweepCell]:
    """Fetch the cross-product ``(angle × source)`` concurrently.

    ``dedupe_queries`` (D-4, default on): before building the cross-product,
    collapse any query that is a near-literal restatement of one already
    kept (``dedupe_near_duplicate_queries``) — the facet-matrix generator's
    pairwise combinatorics deliberately overlap, and firing two near-
    identical queries just burns fetch budget for the same hits. This is the
    query-TIME half of D-4; the result-POOL half (overlap raises confidence
    via ``angles_by_identity``) is unchanged, in ``compose_sweep_result``.

    Returns one ``SweepCell`` per (angle, source) pair, in the original
    angle-then-source enumeration order (order-preserving, so dedup's
    "first-seen wins as representative" stays deterministic across runs).
    A cell with ``error`` set contributes zero hits — the sweep degrades
    gracefully per adapter/pair, never fails wholesale — but only
    after ``_fetch_cell``'s bounded retry-with-backoff has exhausted its
    attempts on a transient failure. ``sleep_fn`` is test-injectable (never
    real ``time.sleep`` in a hermetic test).
    """
    cells: list[SweepCell] = []
    items = list(angle_matrix.items())
    if dedupe_queries:
        kept_items: list[tuple[str, str]] = []
        kept_tokensets: list[set[str]] = []
        for angle, query in items:
            tokens = set(_TOKEN_RE.findall(query.lower()))
            is_dup = False
            for existing in kept_tokensets:
                if tokens and existing:
                    jaccard = len(tokens & existing) / len(tokens | existing)
                    if jaccard >= near_dup_threshold:
                        is_dup = True
                        break
            if not is_dup:
                kept_items.append((angle, query))
                kept_tokensets.append(tokens)
        items = kept_items
    jobs = [
        (angle, query, source)
        for angle, query in items
        for source in sources
    ]
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_fetch_cell, angle, query, source, limit=per_cell_limit, sleep_fn=sleep_fn): (angle, query, source)
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
# Dark-source detection (pre-publish hardening batch, 2026-07-09 a downstream project's live-e2e-run
# finding): a whole source going dark (every one of its cells errored or
# returned zero hits, across ALL angles) looks near-identical to a healthy,
# genuinely-thin sweep at the coverage-gate — nothing in the composed result
# previously distinguished "this source never actually answered" from "this
# source answered honestly with few/no hits". per-cell graceful
# degradation is right at the CELL level; it must not silently compose up
# into "the whole source is dark and nobody noticed".
# ---------------------------------------------------------------------------

def detect_dark_sources(cells: list[SweepCell]) -> list[str]:
    """Return the names of sources that are DARK across the whole sweep.

    A source is dark iff EVERY cell for it (across all angles) either
    errored or returned zero hits — i.e. it never once contributed a hit on
    any angle. A source that returned even one hit on one angle is NOT
    dark, however thin it looks overall (that's "legitimately thin", a
    different — and fine — outcome from "never actually reached").

    Deterministic, sorted output (never depends on the sweep's concurrent
    completion order).
    """
    hit_seen: dict[str, bool] = {}
    for cell in cells:
        hit_seen.setdefault(cell.source, False)
        if cell.hits:
            hit_seen[cell.source] = True
    return sorted(name for name, has_hit in hit_seen.items() if not has_hit)


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
    dark_sources: list[str] = field(default_factory=list)


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

    # C (task #86): per-identity DECLARED facet-pole membership, reusing
    # ``compute_facet_pole_coverage`` (charter §6 — the SAME pole-key
    # derivation Layer 2's facet-coverage check already uses, never a
    # second, drifting implementation). A legacy flat (non-thesis/counter)
    # angle matrix's cells never match ``_FACET_KEY_RE`` and contribute no
    # poles — the honest "no pole structure to tag" case.
    poles_by_identity: dict[str, set[str]] = {}
    for pole, identities in compute_facet_pole_coverage(cells).items():
        for ident in identities:
            poles_by_identity.setdefault(ident, set()).add(pole)

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
        d.hit.poles = frozenset(poles_by_identity.get(identity_key(d.hit), set()))

    kept = rank_and_select(deduped, budget=budget, floor=floor, scores=scores)
    dark_sources = detect_dark_sources(cells)

    return SweepResult(
        kept=kept,
        independent_count=independent_count,
        total_hits_fetched=total_fetched,
        cells=cells,
        errors=errors,
        dark_sources=dark_sources,
    )


def run_sweep_from_protocol(
    protocol_path: Path,
    *,
    budget: int = DEFAULT_FETCH_BUDGET,
    per_cell_limit: int = 20,
    floor: int = 3,
    angle_keys: set[str] | None = None,
    sources_override: list[str] | None = None,
) -> SweepResult:
    """End-to-end: read the frozen ``_protocol.md``, parse the angle matrix +
    sources, run the parallel width-sweep, compose the ranked/deduped result.

    ``angle_keys`` (critic-backtrack D-5a): restrict the sweep to a
    SUBSET of the FROZEN angle matrix's own keys — an exact flattened key
    (``"by-temporal.counter.0"``) or a prefix (``"by-temporal.counter"``,
    matched via ``key == prefix or key.startswith(prefix + ".")``). This
    SELECTS existing frozen queries; it can never author a new one — the
    matrix itself is unchanged, only which of its already-frozen keys are
    swept this call. ``None`` (default) sweeps the full matrix, unchanged
    behavior.

    ``sources_override`` (D-5a): sweep against this explicit source
    list instead of the protocol's declared ``sources:`` — e.g. "all
    registered sources" for a backtrack round that intensifies beyond the
    protocol's normal default-on subset. ``None`` (default) uses
    ``parse_sources(text)``, unchanged behavior.

    Raises ``ValueError`` if the protocol carries no parseable angle matrix,
    or if ``angle_keys`` filters the matrix down to nothing (never silently
    sweeps zero queries)."""
    text = protocol_path.read_text(encoding="utf-8")
    angle_matrix = parse_angle_matrix(text)
    if not angle_matrix:
        raise ValueError(
            f"{protocol_path}: no `seed_queries:` angle matrix found "
            "(expected by-method/by-outcome/by-paradigm/by-population keys)"
        )
    if angle_keys is not None:
        angle_matrix = {
            k: v for k, v in angle_matrix.items()
            if any(k == ak or k.startswith(ak + ".") for ak in angle_keys)
        }
        if not angle_matrix:
            raise ValueError(
                f"{protocol_path}: angle_keys={sorted(angle_keys)!r} matched "
                "ZERO keys in the frozen angle matrix — a directed backtrack "
                "must never silently sweep zero queries."
            )
    sources = sources_override if sources_override is not None else parse_sources(text)
    cells = run_width_sweep(angle_matrix, sources, per_cell_limit=per_cell_limit)
    return compose_sweep_result(cells, budget=budget, floor=floor)


# ---------------------------------------------------------------------------
# _search_hits.md rendering (review-loop-nodekind-drift-fix -A)
# ---------------------------------------------------------------------------

def _paper_id_of_hit(external_ids: dict[str, str]) -> str | None:
    """Best-available external identifier — DOI > arXiv > OpenAlex > S2 id.

    Takes the MERGED ``external_ids`` off a ``DedupedHit`` (``d.external_ids``)
    — NEVER a bare ``hit.external_ids`` (the enrichment regression, a
    downstream project's live e2e run 2026-07-09: ``dedup_hits`` unions every duplicate's ids onto the
    ``DedupedHit`` wrapper, but the wrapper's ``hit`` field stays the FIRST-
    seen representative — its OWN ``external_ids`` can be a strict subset of
    the merged union. The 4 strongest accepted seeds that run came out with a
    BLANK Paper-id because the id lookup read the narrower representative
    dict instead of the merged one that actually had the id). Used both for
    the [NEW]/[IN-CORPUS] annotation lookup and as the seed identifier the
    review-screen agent hands to the review-snowball tool op.
    """
    return (
        external_ids.get("doi")
        or external_ids.get("arxiv")
        or external_ids.get("openalex")
        or external_ids.get("s2")
    )


def _annotate_hit(
    hit: PaperHit,
    *,
    external_ids: dict[str, str] | None = None,
    notes_index: dict[str, str] | None,
    notes_title_index: dict[str, list[tuple[str, str]]] | None,
) -> str:
    """[NEW] / [IN-CORPUS:<citekey>] annotation for a PaperHit.

    Bridges the PaperHit shape (normalized ``external_ids`` dict) to the
    ``_corpus_annotation`` S2-native-dict contract it was written against —
    reuse over reinvention (charter §6), not a second annotation mechanism.

    ``external_ids`` is the caller's MERGED ids (``d.external_ids`` off a
    ``DedupedHit``) when available — same fix as ``_paper_id_of_hit``, a
    hit's own ``external_ids`` can be a narrower subset. Defaults to
    ``hit.external_ids`` for a caller with no ``DedupedHit`` wrapper on hand
    (never a required-but-missing param).
    """
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


def _evidence_snippet(hit: PaperHit, *, max_chars: int = 800) -> str:
    """Abstract text (or, when absent, an S2 ``tldr``) for a kept row —
    review-screen evidence enrichment (a downstream project's validation-run
    finding, 2026-07-09): the screen node was judging the seed-axis call on TITLES
    ALONE because the abstract never made it into ``_search_hits.md``, even
    though every adapter that has one already puts it on ``hit.abstract``.

    Falls back to ``hit.raw["tldr"]["text"]`` (S2-only shape) when the
    abstract is empty — never fabricates evidence when neither is present
    (an honestly-blank cell, not a placeholder string).

    Default cap raised 280 -> 800 (pre-publish hardening, v0.3.0): a live
    curation run found 280 chars too short to verify the "measured human
    baseline" inclusion axis, which often sits deeper in the abstract — this
    is a display-cap change only (the full abstract is already fetched onto
    ``hit.abstract``; nothing here re-fetches). Feeds both the sweep writer
    (``write_search_hits``) and the snowball raw-pool writer
    (``write_corpus_raw``, via reuse of this helper)."""
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
    facet_coverage: dict[str, Any] | None = None,
    attempt_id_backfill: bool = False,
    backfill_adapters: Any = None,
) -> Path:
    """Render the width-sweep result to ``_search_hits.md`` (Option C -A).

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

    Stamps flat frontmatter with ``dark_sources:`` (comma-joined, empty when
    none) — same convention ``sources/snowball.py``'s ``write_walk_report``
    uses for ``stop_reason:`` — the machine-readable signal
    ``review.check_source_coverage`` reads to fail-closed the coverage-gate
    when a source declared in the protocol's ``sources:`` list never
    actually contributed a hit (pre-publish hardening batch, a downstream project's live-e2e-run
    finding 2026-07-09).

    Id-resolution: before a candidate is flagged
    ``[NO-ID]`` below, ``sources.identifiers.backfill_missing_ids`` gets one
    targeted title/year lookup attempt at it — a resolvable-but-messy hit
    (a real paper with no doi/arxiv/openalex/s2 in its search-hit metadata)
    is backfilled and kept, never silently dropped. ``attempt_id_backfill``
    is False by default (no network — a caller opts in explicitly);
    ``backfill_adapters`` (an explicit adapter list, including ``[]``) both
    opts in AND fixes the adapter chain (test injection). The resolution
    rate (missing / backfilled / unresolved) is ALWAYS counted (zero-cost
    when nothing is missing) and surfaced — both a prose line and
    machine-readable frontmatter — never silently swallowed.
    """
    from .identifiers import backfill_missing_ids

    resolve_with = (
        backfill_adapters if backfill_adapters is not None
        else (None if attempt_id_backfill else [])
    )
    id_stats = backfill_missing_ids(result.kept, adapters=resolve_with)

    fm_lines = [
        "---",
        f"dark_sources: {', '.join(result.dark_sources)}",
        f"id_backfill_missing: {id_stats['missing']}",
        f"id_backfill_resolved: {id_stats['backfilled']}",
        f"id_backfill_unresolved: {id_stats['unresolved']}",
    ]
    if facet_coverage is not None:
        # 0.3.1 Layer 2: stamp the per-pole distinct-paper coverage +
        # thin-pole markers so a LATER `coverage-gate` evaluation (a
        # different process invocation — the cells themselves are
        # ephemeral, never persisted) can read the facet-coverage disposition
        # back mechanically. Same flat-frontmatter-comma-joined convention
        # `dark_sources:`/`stop_reason:` already use.
        pole_counts = facet_coverage.get("pole_counts", {})
        counts_canon = ", ".join(f"{p}={c}" for p, c in sorted(pole_counts.items()))
        fm_lines.append(f"facet_pole_counts: {counts_canon}")
        fm_lines.append(f"facet_thin_poles: {', '.join(facet_coverage.get('thin_poles', []))}")
        fm_lines.append(f"facet_min_hits_per_pole: {facet_coverage.get('min_hits_per_pole', '')}")
    fm_lines.append("---")

    lines: list[str] = [*fm_lines, "", "# Search hits\n"]

    if id_stats["missing"]:
        lines.append(
            "> Id-resolution: "
            f"{id_stats['missing']} candidate(s) carried no doi/arxiv/openalex/s2 "
            f"id at fetch time — {id_stats['backfilled']} backfilled via a "
            f"title/year lookup, {id_stats['unresolved']} still unresolved after "
            "the attempt (flagged `[NO-ID]` below; a counted drop, not a silent "
            "one).\n"
        )

    if facet_coverage is not None and facet_coverage.get("thin_poles"):
        lines.append(
            "> ⚠ THIN FACET-POLE: "
            f"{', '.join(facet_coverage['thin_poles'])} — surfaced fewer than "
            f"{facet_coverage.get('min_hits_per_pole', '?')} DISTINCT (deduped) "
            "papers this sweep. Eligible for Layer-3 facet re-search "
            "remediation at coverage-gate.\n"
        )

    if result.dark_sources:
        lines.append(
            "> ⚠ SOURCE DARK: "
            f"{', '.join(result.dark_sources)} — every cell for this source "
            "errored or returned zero hits across ALL angles this sweep. If "
            "this source is declared in the protocol's `sources:` list, the "
            "corpus CANNOT be trusted as covering it — re-run the sweep "
            "once the source is reachable before accepting a seed frontier.\n"
        )

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
    lines.append("| Annotation | Paper-id | Title | Venue | Year | Abstract/TL;DR | Flags | Rerank | Poles |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for d in result.kept:
        hit = d.hit
        annotation = _annotate_hit(
            hit, external_ids=d.external_ids,
            notes_index=notes_index, notes_title_index=notes_title_index,
        )
        pid = _paper_id_of_hit(d.external_ids) or ""
        flags: list[str] = []
        if not pid:
            # The id is the JOIN KEY the review-screen agent hands to the
            # snowball tool op as a seed — a hit with no resolvable id can
            # never be emitted as a seed. Flag it loudly rather than let an
            # empty Paper-id cell look like an oversight (charter §2).
            flags.append("[NO-ID: cannot resolve doi/arxiv/openalex/s2 — needs manual id lookup]")
        if hit.derivative_of is not None:
            flags.append(f"[DERIVATIVE-OF:{hit.derivative_of}]")
        if hit.below_floor and not below_floor_suppressed:
            flags.append("[BELOW-FLOOR: needs more sources]")
        title = (hit.title or "").replace("|", "/")
        venue = (hit.venue or "").replace("|", "/")
        year = str(hit.year) if hit.year is not None else ""
        evidence = _evidence_snippet(hit)
        # A1 (task #86): the TF-IDF rerank score this hit scored against
        # the angle query that surfaced it — stamped by `_fetch_cell` at
        # fetch time, the strength signal Section C's curation bound
        # reads. Honest-blank sentinel (never a fabricated number) when
        # this hit never went through a rerank pass.
        rerank = format_rerank_score(hit.rerank_score)
        # C (task #86): the DECLARED facet-pole(s) this hit matched — the
        # stratification-bucket signal. Honest-blank sentinel (never a
        # fabricated pole) when this hit's sweep cell carried no pole
        # structure (legacy flat matrix).
        poles = format_poles(hit.poles)
        lines.append(
            f"| {annotation} | {pid} | {title} | {venue} | {year} | {evidence} | {' '.join(flags)} | {rerank} | {poles} |"
        )
    lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path
