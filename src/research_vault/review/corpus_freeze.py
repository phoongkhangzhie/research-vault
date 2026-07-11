# SPDX-License-Identifier: AGPL-3.0-or-later
"""review/corpus_freeze.py — NG-6a piece 1: the explicit, versioned
``corpus_freeze`` baseline + the fail-closed ``rv review refresh`` re-freeze.

Design of record: docs/superpowers/specs/2026-07-08-ng6a-refresh-autonomous-remediation.md
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
is the richer, versioned, hashed wrapper NG-6a adds on top: every time this
module re-freezes (``refresh``/a remediation round), it writes the SAME
citekey set into BOTH ``run_state.meta["corpus_freeze"]["corpus_citekeys"]``
and ``run_state.meta["frozen_corpus_citekeys"]`` — so the next
``classify_coverage_gate_with_deviation_check`` call (unmodified) compares
against the moved-forward baseline, never a stale one.

Stdlib only. sr: NG-6a
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Any

from ..hashing import hash_file
from ..note import _parse_frontmatter
from ..sources.sweep import parse_angle_matrix, parse_sources


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


def canonicalize_criteria(protocol_text: str) -> str:
    """Canonicalize the FROZEN criteria fields of ``_protocol.md`` into a
    stable byte form (sorted keys, normalized whitespace) — the one bright
    line between "denominator" (citekey set, may grow if declared) and
    "criteria" (these hashed bytes, frozen at the one human gate).

    Reuses ``note._parse_frontmatter`` for the flat scalar fields
    (``question``, ``inclusion``, ``exclusion``, ``coverage_claim``) and
    ``sources.sweep.parse_angle_matrix``/``parse_sources`` for the nested
    ``seed_queries:`` angle matrix + the ``sources:`` inline list (charter
     reuse the SAME parsers the width-sweep itself reads the frozen
    protocol with, never a second hand-rolled parse of the same fields).
    """
    fields, _ = _parse_frontmatter(protocol_text)
    question = _norm_criteria_value(fields.get("question", ""))
    inclusion = _norm_criteria_value(fields.get("inclusion", ""))
    exclusion = _norm_criteria_value(fields.get("exclusion", ""))
    coverage_claim = _norm_criteria_value(fields.get("coverage_claim", ""))

    angle_matrix = parse_angle_matrix(protocol_text)
    angles_canon = "\n".join(f"{k}={angle_matrix[k]}" for k in sorted(angle_matrix))

    sources = parse_sources(protocol_text)
    sources_canon = ",".join(sorted(sources))

    return (
        f"question={question}\n"
        f"inclusion={inclusion}\n"
        f"exclusion={exclusion}\n"
        f"coverage_claim={coverage_claim}\n"
        f"seed_queries:\n{angles_canon}\n"
        f"sources={sources_canon}\n"
    )


def hash_criteria_bytes(protocol_path: Path) -> str:
    """``sha256:<hex>`` of the canonicalized criteria bytes of
    ``_protocol.md``. A missing protocol hashes the empty canonical form
    (deterministic, never crashes — the absence itself will trip other
    gates, e.g. ``check_protocol_gate``, this function's job is only the
    hash)."""
    text = protocol_path.read_text(encoding="utf-8") if protocol_path.exists() else ""
    canon = canonicalize_criteria(text)
    return "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Corpus-row parsing helper (lazy import to avoid a review/__init__ cycle)
# ---------------------------------------------------------------------------

def _parse_corpus_citekeys_safe(corpus_path: Path) -> list[str]:
    from . import _parse_corpus_citekeys  # lazy — avoids a module-load cycle

    return _parse_corpus_citekeys(corpus_path)


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
    freeze = {
        "version": 1,
        "corpus_hash": hash_file(corpus_path) if corpus_path.exists() else "",
        "corpus_citekeys": citekeys,
        "criteria_hash": hash_criteria_bytes(protocol_path),
        "corpus_path": str(corpus_path.resolve()) if corpus_path.exists() else str(corpus_path),
        "protocol_path": str(protocol_path.resolve()) if protocol_path.exists() else str(protocol_path),
        "frozen_at": now if now is not None else time.time(),
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
      3. Criteria-hash check: a changed hash with no human
         ``criteria-change`` deviation on record -> BLOCK (the anti-fishing
         pin firing).
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

    ok, msg = check_undeclared_deviation(
        set(baseline["corpus_citekeys"]), current_citekeys, deviations_path,
    )
    if not ok:
        raise RefreshBlocked(f"rv review refresh: BLOCKED — {msg}")

    new_freeze = {
        "version": baseline["version"] + 1,
        "corpus_hash": hash_file(corpus_path),
        "corpus_citekeys": sorted(current_citekeys),
        "criteria_hash": current_criteria_hash,
        "corpus_path": str(corpus_path.resolve()),
        "protocol_path": str(protocol_path.resolve()) if protocol_path.exists() else str(protocol_path),
        "frozen_at": now if now is not None else time.time(),
    }
    run_state_meta["corpus_freeze"] = new_freeze
    run_state_meta["frozen_corpus_citekeys"] = new_freeze["corpus_citekeys"]
    return new_freeze


# ---------------------------------------------------------------------------
# CLI entry point — `rv review refresh <scope>` (in-process callable too, the
# remediation loop must not shell out)
# ---------------------------------------------------------------------------

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
