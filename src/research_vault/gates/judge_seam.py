# SPDX-License-Identifier: AGPL-3.0-or-later
"""judge_seam.py — the emit-tasks / ingest-verdicts contract for the
cold-agent-judge fan-out (the PRIMARY judge-orchestration path).

The whole framework already runs on the Claude Code harness, so the fidelity
judge (support-matcher — the citation-fidelity floor) is harness-orchestrated
by default, not API-orchestrated: rv EMITS a task manifest, the hub fans OUT
fresh cold subagent-judges over it (memoryless — no draft-thesis anchoring
possible), and rv INGESTS the returned verdicts. This module is the SHARED
low-level primitive the gate's emit_*_tasks/ingest_*_verdicts functions
build on — the schema names, the id-join, the canary-key check, and the
fail-closed defaulting stay generic (any future judge-gated fan-out reuses
this, not a fork). (The cold-read self-containment critic
that originally shared this seam was removed — SIGNAL-only, non-actionable
under hands-off autonomy, redundant with the review board's own reader-hygiene
checks. An explicit, documented design call; see DEVLOG.)

THE THREE ARTIFACTS (contract):
  _judge-tasks.json       (rv -> hub -> cold judges; PUBLIC — canaries carry
                           NO marker distinguishing them from real tasks)
  _judge-canary-key.json  (rv-PRIVATE, NEVER emitted to hub/judge — task_id
                           -> expected verdict)
  _judge-verdicts.json    (cold judges/hub -> rv; verdicts keyed by task_id)

GUARDS THIS MODULE ENFORCES (undiminished vs. the
live API-judge path; a cold subagent-judge can hallucinate too):
  - id<->id join is the contract, never prompt-text matching (fragile
    across re-emits).
  - Rejects-only: a "certifying" verdict never certifies on its own; only
    the reject-class verdicts in a gate's vocab may BLOCK/flag.
  - Fail-closed: a task present in the tasks file but missing/unparseable
    in the verdicts file defaults to the gate's fail-closed (reject) value
    — never a silent pass. An entirely-missing (or effectively-empty)
    verdicts file is the "floor gate NOT RUN" case -> HALT (the
    caller declares HALT-DECLARE; this module signals it via
    ``halt``/``halt_reason``, never by raising for that specific case —
    HALT is a disposition a caller must see and act on, not an exception
    that could be swallowed by a bare except).
  - Canary-verified: an out-of-bounds (missing or mismatched) canary
    verdict raises ``CanaryAbortError`` — the "is the judge actually
    working" check on this no-API-key path (replaces the liveness-ping,
    ). A canary is missing-counts-as-failed (fail-closed applies to
    canaries too — a judge that silently dropped the canary is exactly as
    untrustworthy as one that answered it wrong).
  - Idempotent + resumable: re-emitting is deterministic given the same
    input (stable id assignment order); a partial verdicts file can be
    completed by re-fanning-out only the missing ids (surfaced via
    ``missing_ids`` on the ingest result — never silently patched over).

Stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Schema constants (the contract — exact names)
# ---------------------------------------------------------------------------

TASKS_SCHEMA = "rv-judge-tasks/v1"
CANARY_KEY_SCHEMA = "rv-judge-canary-key/v1"
VERDICTS_SCHEMA = "rv-judge-verdicts/v1"


class CanaryAbortError(RuntimeError):
    """Raised when an interleaved judge-task canary comes back wrong.

    Either the fan-out judge missed the canary entirely (fail-closed:
    missing counts as failed) or answered it with a verdict that does not
    match the private canary key. Either way the whole batch's real
    verdicts are untrustworthy — abort loudly ("untrustworthy
    signal" policy: HALT-DECLARE, fail-closed, never auto-retry the same
    broken judge).

    This is a DELIBERATELY SEPARATE class from
    ``manuscript.review_board.CanaryAbortError`` (the review-board's own
    score-bounds canary) rather than a reuse of it: that class lives in
    ``manuscript/`` (a layer ABOVE ``gates/`` in this codebase's dependency
    direction — ``manuscript`` imports from ``gates``, never the reverse)
    and checks a different thing (dimensioned review scores vs. this
    module's task-id verdicts). Importing it here would invert the
    intended layering for a same-named-but-different concept; two small
    classes with the same name in two layers is the honest boundary, not
    a duplication to dedup.
    """


def now_iso() -> str:
    """UTC ISO-8601 timestamp, second precision (for the ``created`` field)."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# JSON file I/O — tiny, hermetic, never raises on absence
# ---------------------------------------------------------------------------

def read_json_or_none(path: Path) -> dict[str, Any] | None:
    """Read a JSON file; return None if absent, unreadable, or malformed.

    Never raises — an absent/corrupt verdicts file is the "floor gate
    NOT RUN" case, which callers must treat as HALT, not as a crash.
    """
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_json(path: Path, doc: dict[str, Any]) -> None:
    """Write a JSON doc, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=False) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# id assignment — deterministic, stable across re-emits
# ---------------------------------------------------------------------------

def make_task_id(n: int) -> str:
    """``t0001``-style deterministic id from a 1-based sequence number."""
    return f"t{n:04d}"


def interleave_with_canaries(
    real_items: list[dict[str, Any]],
    canary_items: list[tuple[dict[str, Any], str]],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Interleave canary items among real items, assign ids, return the
    combined ordered task list + the canary-key mapping (id -> expected verdict).

    Canaries are spread at evenly-spaced positions among the real items
    (deterministic given the same inputs — idempotent re-emit) so their
    position in the list does not itself mark them as canaries. Every item
    (real or canary) gets a plain sequential ``t%04d`` id — the id itself
    carries no tell either.

    Args:
        real_items:   list of task dicts (WITHOUT ``id``) in extraction order.
        canary_items: list of (task_dict_without_id, expected_verdict) pairs.

    Returns:
        (combined_tasks_with_ids, canary_key) — canary_key maps only the
        canary task ids to their expected verdicts.
    """
    n_real = len(real_items)
    n_canary = len(canary_items)
    total = n_real + n_canary

    if n_canary == 0:
        combined = [dict(item, id=make_task_id(i + 1)) for i, item in enumerate(real_items)]
        return combined, {}

    # Evenly-spaced insertion positions among the combined sequence
    # (deterministic: canary k targets round(total * (k+1) / (n_canary+1))).
    #
    # ★ Build a LIST of length n_canary (NOT a set): for a small ``total``,
    # two canaries can target the SAME slot; a set comprehension would COLLAPSE
    # them and silently DROP a canary (a vanished probe weakens the guard).
    # Resolve every collision to a distinct free slot instead, so
    # ALL n_canary canaries are always placed (verified: len(canary_key) ==
    # n_canary for any total >= 1).
    raw_positions = [
        round(total * (k + 1) / (n_canary + 1)) for k in range(n_canary)
    ]
    fixed_positions: list[int] = []
    used: set[int] = set()
    for p in raw_positions:
        p = max(0, min(p, total))
        # try forward to the next free slot...
        while p in used and p <= total:
            p += 1
        # ...else backward (forward ran off the end).
        while p in used and p >= 0:
            p -= 1
        used.add(p)
        fixed_positions.append(p)
    fixed_positions.sort()

    combined_raw: list[dict[str, Any]] = list(real_items)
    canary_key: dict[str, str] = {}

    # Insert canaries at their target positions, in order, adjusting for
    # the shift each insertion causes.
    for offset, (pos, (item, expected)) in enumerate(zip(fixed_positions, canary_items)):
        insert_at = min(pos, len(combined_raw))
        combined_raw.insert(insert_at, dict(item, _is_canary_expected=expected))

    combined: list[dict[str, Any]] = []
    for i, item in enumerate(combined_raw):
        tid = make_task_id(i + 1)
        expected = item.pop("_is_canary_expected", None)
        item_with_id = dict(item, id=tid)
        combined.append(item_with_id)
        if expected is not None:
            canary_key[tid] = expected

    return combined, canary_key


# ---------------------------------------------------------------------------
# Canary verification — fail-closed, missing counts as failed
# ---------------------------------------------------------------------------

def check_canaries(canary_key: dict[str, str], verdict_by_id: dict[str, str]) -> None:
    """Verify every canary in *canary_key* against the ingested verdicts.

    Raises ``CanaryAbortError`` on the FIRST out-of-bounds canary (missing
    OR mismatched, case-insensitive compare) — a batch with any bad canary
    is wholly untrustworthy, so there is no reason to keep checking the
    rest before aborting.

    A caller with an EMPTY canary_key (e.g. a gate emitted with
    ``num_canaries=0``, or a fixture with no real tasks and thus no
    canaries either) sees no-op success — this is an honest, deliberate
    degrade (documented at the emit call site), never a hidden default.
    """
    for tid, expected in canary_key.items():
        actual = verdict_by_id.get(tid)
        if actual is None:
            raise CanaryAbortError(
                f"judge-fanout canary {tid!r} is MISSING from the verdicts "
                f"file (expected {expected!r}) — a missing canary counts as "
                f"failed (fail-closed): the fan-out judge either dropped this "
                f"task or the batch never completed. Cannot trust any verdict "
                f"in this round."
            )
        if actual.strip().upper() != expected.strip().upper():
            raise CanaryAbortError(
                f"judge-fanout canary {tid!r} came back {actual!r}, expected "
                f"{expected!r} — the fan-out judge is either rubber-stamping, "
                f"blind, or broken-harsh on this planted probe. Cannot trust "
                f"any real verdict alongside it."
            )


# ---------------------------------------------------------------------------
# Fail-closed fill — every real task id gets a verdict, one way or another
# ---------------------------------------------------------------------------

def fail_closed_fill(
    real_task_ids: list[str],
    verdict_by_id: dict[str, str],
    vocab: frozenset[str],
    default: str,
) -> tuple[dict[str, str], list[str], list[str]]:
    """Fill every real (non-canary) task id with a verdict, defaulting the
    fail-closed value for anything missing or outside the fixed vocab.

    Returns (filled, missing_ids, unrecognized_ids):
      filled:            id -> verdict, one entry per real_task_ids, every
                         value drawn from vocab (never a raw unvalidated
                         string).
      missing_ids:       ids present in real_task_ids but absent from
                         verdict_by_id (surfaced — never silently patched).
      unrecognized_ids:  ids whose verdict string was present but NOT in
                         vocab (widened vocab or a garbled response) —
                         also defaulted, also surfaced.
    """
    filled: dict[str, str] = {}
    missing_ids: list[str] = []
    unrecognized_ids: list[str] = []

    for tid in real_task_ids:
        raw = verdict_by_id.get(tid)
        if raw is None:
            filled[tid] = default
            missing_ids.append(tid)
            continue
        norm = raw.strip().upper()
        if norm not in vocab:
            filled[tid] = default
            unrecognized_ids.append(tid)
            continue
        filled[tid] = norm

    return filled, missing_ids, unrecognized_ids


def fanout_incomplete(tasks_doc: dict[str, Any] | None, verdicts_doc: dict[str, Any] | None) -> bool:
    """True iff the fan-out effectively never ran (the floor-gate
    NOT-RUN case) — the verdicts file is absent, unreadable, or empty while
    the tasks file declares at least one real task.

    A PARTIAL verdicts file (some ids present, some missing) is NOT this
    case — see ``fail_closed_fill``'s per-id fail-closed defaulting and
    the module docstring's resumability note; only a wholesale non-return
    (indistinguishable from "the fan-out crashed / never started") halts.
    """
    if tasks_doc is None:
        return False  # nothing to run — caller's own no-op path handles this
    n_tasks = len(tasks_doc.get("tasks", []) or [])
    if n_tasks == 0:
        return False
    if verdicts_doc is None:
        return True
    verdicts_list = verdicts_doc.get("verdicts", []) or []
    return len(verdicts_list) == 0
