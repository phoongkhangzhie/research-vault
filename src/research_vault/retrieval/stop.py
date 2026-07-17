# SPDX-License-Identifier: AGPL-3.0-or-later
"""retrieval/stop.py — the claim-driven stop condition: a DRAFT -> CHECK ->
HOP loop wrapped around ``retrieval/traverse.py``'s ``TraversalEngine`` that
decides when retrieval has enough coverage of a query, and emits the
structured abstention set for what it could not cover.

**Grounding.** The classic technique here is FLARE (active retrieval keyed
to a token-level confidence signal from the generating model's own
logprobs). rv cannot borrow FLARE's trigger verbatim: the harness fan-out
this codebase's judges run on (``gates.judge_seam``) exposes no calibrated
per-token logprobs — every LLM decision point is a cold subagent verdict,
not an in-process scored generation. So this module swaps FLARE's
logprob-confidence trigger for **claim coverage**: decompose the candidate
answer into checkable sub-claims, and treat an UNCOVERED sub-claim as the
retrieval-confidence signal FLARE would have read off a low logprob.

**The loop.**
  1. DRAFT   — an LLM drafts a candidate answer to the query, decomposed
     into sub-claims, given the notes retrieved so far as context.
  2. CHECK   — per sub-claim, decide COVERED (some retrieved note supports
     it) vs UNCOVERED. This is a coverage/recall check — "does SOMETHING
     already retrieved back this claim" — not the faithfulness/rejects-only
     gate that reads the FINAL drafted prose against its citations (that is
     a separate, later concern; this module never builds it).
  3. HOP     — the highest-priority UNCOVERED sub-claim's own text becomes
     the query for one more reasoning-conditioned traversal hop: it is
     re-classified via ``traverse.classify_intent`` and walked from the
     current frontier, not a generic "expand everything" next hop.

**Four stop conditions** (see ``CoverageLoop`` for exactly where each is
checked): all sub-claims COVERED; a fixed hop budget exhausted; saturation
(a hop round adds no new visited node); and the fail-closed structural
signal, uncovered-maps-to-no-edge — an UNCOVERED claim's routed edge tags
have no reachable candidate edge anywhere in the currently-visited graph,
so the corpus cannot structurally cover it and the loop stops hopping into
that void rather than burning budget on a dead end.

**The abstention set is the load-bearing output.** The UNCOVERED sub-claims
at stop time are returned as structured data (never prose), each tagged
with WHY it went uncovered, for a downstream faithfulness gate and the
retrieval engine assembly to consume directly.

**Harness-native, undiminished.** Both LLM decision points (DRAFT and
CHECK) go through the same cold-agent-judge emit/ingest fan-out contract
every other gate in this codebase uses (``gates.judge_seam``): rv emits a
task manifest, a fresh cold subagent judges it, rv ingests the returned
verdicts. There is no direct API call anywhere in this module and no
reading of a judge API key.

**Fail-closed direction.** A missing/malformed CHECK verdict for a
sub-claim defaults to UNCOVERED (never a fabricated coverage claim). A
missing verdict SET (the fan-out never ran at all) HALTs loudly via
``gates.judge_seam.fanout_incomplete`` — never a silent all-covered or
all-uncovered pass.

**CHECK's reuse of the support-matcher.** The coverage verdict vocabulary
(SUPPORTS / PARTIAL / ABSENT / CONTRADICTS) and the fail-closed default
(ABSENT) intentionally mirror ``gates.support_matcher`` — COVERED is
defined as "some retrieved note returns SUPPORTS" — and this module reuses
``support_matcher._read_note_structured_fields`` verbatim to build the note
excerpt a cold judge sees (charter: reuse over re-derive). The vocab
constant itself is kept LOCAL rather than imported from
``manuscript.fidelity_gates`` (which owns the manuscript-draft-specific
emit/ingest pair over ``\\cite`` pairs in drafted prose — a genuinely
different shape: one designated citekey per claim, bound to a draft's
citation-set hash) to avoid coupling the retrieval layer to the manuscript
loop; the layering in this codebase runs manuscript -> gates/retrieval,
never the reverse. See the module's PR description for this judgment call.

Stdlib only (+ intra-package imports).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..gates import judge_seam
from ..gates.support_matcher import _read_note_structured_fields
from .traverse import TraversalEngine, classify_intent

# ---------------------------------------------------------------------------
# Named, tunable constants (never hardcoded inline below).
# ---------------------------------------------------------------------------

MAX_STOP_HOPS: int = 4     # max claim-targeted HOP rounds this loop will spend
MAX_SUB_CLAIMS: int = 8    # cap on DRAFT's sub-claim decomposition (surfaced, not silent)

_DRAFT_KIND = "draft"
_CHECK_KIND = "coverage"

_COVERAGE_VOCAB: frozenset[str] = frozenset({"SUPPORTS", "PARTIAL", "ABSENT", "CONTRADICTS"})
_COVERAGE_FAIL_CLOSED_DEFAULT = "ABSENT"  # can't confirm -> uncovered, never a silent pass

# Abstention reasons — the WHY tag on every entry in the abstention set.
REASON_BUDGET_EXHAUSTED = "budget-exhausted"
REASON_SATURATION = "saturation"
REASON_NO_EDGE = "uncovered-maps-to-no-edge"
REASON_UNCHECKED = "loop-ended-before-check"  # honest fallback; see CoverageLoop.result


# ---------------------------------------------------------------------------
# Sub-claim + abstention data model
# ---------------------------------------------------------------------------

@dataclass
class SubClaim:
    """One DRAFT-decomposed checkable proposition from the candidate
    answer, tracked across CHECK/HOP rounds."""

    id: str
    text: str
    covered: bool = False
    supporting_note: str | None = None   # path of the note that covered it, once known
    checked_against: set[str] = field(default_factory=set)  # note paths already CHECKed


@dataclass
class AbstentionEntry:
    """One UNCOVERED sub-claim at stop time, tagged with why."""

    claim_id: str
    claim: str
    reason: str
    detail: str


# ---------------------------------------------------------------------------
# DRAFT — decompose a candidate answer into sub-claims (harness fan-out)
# ---------------------------------------------------------------------------

_BULLET_RE = re.compile(r"^\s*-\s+(.+?)\s*$", re.MULTILINE)


def _draft_context_from_engine(engine: TraversalEngine) -> list[dict[str, str]]:
    """The retrieved-so-far context DRAFT reads — title + description of
    every currently visited node, exactly as ``TraversalEngine.result()``
    reports it (this module consumes the engine, it never re-derives its
    visited set)."""
    return [
        {"slug": n.slug, "title": n.title, "description": n.description}
        for n in engine.visited.values()
    ]


def emit_draft_task(query: str, engine: TraversalEngine) -> dict[str, Any]:
    """Emit the DRAFT task (Phase 1 of this loop) — one task asking the
    judge to draft a candidate answer to *query* from the retrieved
    context and decompose it into checkable sub-claims.

    Returns ``{"tasks_doc": {...}, "canary_key_doc": {...}}`` (no canary
    entries — DRAFT is generative, not verified against a fixed vocab;
    the RELIED-UPON verdict in this loop is CHECK's, which is canaried;
    see the module docstring).
    """
    real_task = {
        "kind": _DRAFT_KIND,
        "query": query,
        "context": _draft_context_from_engine(engine),
    }
    combined, canary_key = judge_seam.interleave_with_canaries([real_task], [])
    tasks_doc = {
        "schema": judge_seam.TASKS_SCHEMA,
        "gate": "stop-condition-draft",
        "judge_kind": "cold",
        "created": judge_seam.now_iso(),
        "instructions": (
            "Draft a candidate answer to the query using ONLY the "
            "retrieved context notes given (title + description each). "
            "Then decompose that candidate answer into its individual "
            "checkable sub-claims. Answer with ONE sub-claim per line, "
            "each line starting with '- ' (a literal dash and a space). "
            "Do not include anything else in the response."
        ),
        "tasks": combined,
    }
    canary_key_doc = {"schema": judge_seam.CANARY_KEY_SCHEMA, "canaries": canary_key}
    return {"tasks_doc": tasks_doc, "canary_key_doc": canary_key_doc}


def ingest_draft_verdicts(
    tasks_doc: dict[str, Any],
    canary_key_doc: dict[str, Any] | None,
    verdicts_doc: dict[str, Any] | None,
) -> dict[str, Any]:
    """Ingest DRAFT's verdict — parse the bullet-list response into
    sub-claims, fail-closed to an EMPTY sub-claim set (never fabricated
    claims) on any missing/incomplete fan-out.

    Returns ``{"sub_claims": [SubClaim, ...], "halt": bool,
    "halt_reason": str, "errors": [...], "warnings": [...]}``.
    """
    real_tasks = [t for t in tasks_doc.get("tasks", []) if t.get("kind") == _DRAFT_KIND]
    if not real_tasks:
        return {"sub_claims": [], "halt": False, "halt_reason": "", "errors": [], "warnings": []}

    if judge_seam.fanout_incomplete(tasks_doc, verdicts_doc):
        return {
            "sub_claims": [],
            "halt": True,
            "halt_reason": (
                "verdicts file absent/empty for a non-empty DRAFT task — "
                "fan-out did not complete."
            ),
            "errors": [
                "stop-condition DRAFT judge-fanout HALT: verdicts "
                "missing/empty — the candidate answer was never drafted. "
                "This is NOT a pass."
            ],
            "warnings": [],
        }

    verdict_by_id: dict[str, str] = {}
    for v in (verdicts_doc or {}).get("verdicts", []):
        vid = v.get("id")
        if vid:
            verdict_by_id[vid] = str(v.get("verdict", ""))

    task = real_tasks[0]
    raw = verdict_by_id.get(task["id"])

    errors: list[str] = []
    warnings: list[str] = []
    if raw is None:
        errors.append(
            f"stop-condition DRAFT: verdict for task {task['id']!r} is "
            f"missing from the verdicts file — zero sub-claims (fail-closed: "
            f"an empty sub-claim set, never a fabricated one)."
        )
        return {"sub_claims": [], "halt": False, "halt_reason": "", "errors": errors, "warnings": warnings}

    lines = [m.group(1) for m in _BULLET_RE.finditer(raw)]
    if not lines and raw.strip():
        warnings.append(
            "stop-condition DRAFT: response was non-empty but no "
            "'- <sub-claim>' bullet lines were parseable — proceeding with "
            "ZERO sub-claims. This trivially satisfies 'all covered'; treat "
            "the result with suspicion (see result()['draft_malformed'])."
        )

    if len(lines) > MAX_SUB_CLAIMS:
        warnings.append(
            f"stop-condition DRAFT: {len(lines)} sub-claims decomposed, "
            f"capped at MAX_SUB_CLAIMS={MAX_SUB_CLAIMS} — the extra "
            f"{len(lines) - MAX_SUB_CLAIMS} were dropped (surfaced here, "
            f"never silently discarded)."
        )
        lines = lines[:MAX_SUB_CLAIMS]

    sub_claims = [
        SubClaim(id=f"sc{i + 1:04d}", text=text)
        for i, text in enumerate(lines)
    ]
    return {"sub_claims": sub_claims, "halt": False, "halt_reason": "", "errors": errors, "warnings": warnings}


# ---------------------------------------------------------------------------
# CHECK — per-sub-claim coverage verdict against retrieved notes (harness fan-out)
# ---------------------------------------------------------------------------

def _check_canary_bank() -> list[tuple[dict[str, Any], str]]:
    """Bidirectional CHECK canaries — an unambiguous SUPPORTS and an
    unambiguous ABSENT, catching both a rubber-stamping judge (always
    SUPPORTS) and a blind/broken one (always ABSENT, which would silently
    starve every claim into the abstention set)."""
    supports_task = {
        "kind": _CHECK_KIND,
        "claim": "canary probe: the sky is blue during a clear daytime.",
        "note_slug": "canary-sky-is-blue",
        "source": (
            "result: Direct observation confirms the sky appears blue "
            "under clear daytime conditions due to Rayleigh scattering."
        ),
    }
    absent_task = {
        "kind": _CHECK_KIND,
        "claim": "canary probe: the moon is composed primarily of green cheese.",
        "note_slug": "canary-unrelated-dataset-license",
        "source": (
            "result: A note recording the software license terms of an "
            "unrelated dataset, with no bearing on lunar composition."
        ),
    }
    return [(supports_task, "SUPPORTS"), (absent_task, "ABSENT")]


def _pending_check_pairs(sub_claims: list[SubClaim], engine: TraversalEngine) -> list[tuple[SubClaim, str]]:
    """Every (uncovered sub-claim, unchecked visited-note path) pair —
    skips claims already COVERED and (claim, note) pairs already CHECKed
    (never re-emits a redundant task)."""
    pairs: list[tuple[SubClaim, str]] = []
    for sc in sub_claims:
        if sc.covered:
            continue
        for note_path in engine.visited:
            if note_path in sc.checked_against:
                continue
            pairs.append((sc, note_path))
    return pairs


def emit_check_tasks(sub_claims: list[SubClaim], engine: TraversalEngine) -> dict[str, Any] | None:
    """Emit the CHECK task doc (Phase 2) — one task per (uncovered
    sub-claim, not-yet-checked visited note) pair, batched into a single
    round-trip. Returns ``None`` when there is nothing left to check
    (every uncovered claim has been checked against every visited note).
    """
    pairs = _pending_check_pairs(sub_claims, engine)
    if not pairs:
        return None

    real_tasks: list[dict[str, Any]] = []
    pair_by_index: list[tuple[SubClaim, str]] = []
    for sc, note_path in pairs:
        node = engine.visited[note_path]
        fields = _read_note_structured_fields(Path(note_path))
        source_lines = [f"{k}: {v}" for k, v in sorted(fields.items()) if v]
        source = "\n".join(source_lines) if source_lines else "(no structured fields available)"
        real_tasks.append({
            "kind": _CHECK_KIND,
            "claim": sc.text,
            "note_slug": node.slug,
            "source": source,
        })
        pair_by_index.append((sc, note_path))

    combined, canary_key = judge_seam.interleave_with_canaries(real_tasks, _check_canary_bank())
    tasks_doc = {
        "schema": judge_seam.TASKS_SCHEMA,
        "gate": "stop-condition-check",
        "judge_kind": "cold",
        "created": judge_seam.now_iso(),
        "instructions": (
            "For EACH task, decide whether the note excerpt (`source`) "
            "supports the claim. Read adversarially — look first for "
            "how the claim could FAIL to be backed before accepting it. "
            "Answer with exactly one bare word (no brackets): SUPPORTS "
            "(the excerpt directly backs the claim), PARTIAL (backs a "
            "weaker/narrower version), ABSENT (the excerpt is silent — "
            "this is the mandatory answer whenever you cannot quote a "
            "supporting span), or CONTRADICTS (the excerpt opposes the "
            "claim)."
        ),
        "tasks": combined,
    }
    canary_key_doc = {"schema": judge_seam.CANARY_KEY_SCHEMA, "canaries": canary_key}
    # Stash the (sub_claim, note_path) pairing in-band on the doc object
    # for the caller to hand straight to ingest_check_verdicts — mirrors
    # TraversalEngine._pending's role for hop-prune.
    return {
        "tasks_doc": tasks_doc,
        "canary_key_doc": canary_key_doc,
        "real_ids_in_order": [t["id"] for t in combined if t["id"] not in canary_key],
        "pairs_in_order": pair_by_index,
    }


def ingest_check_verdicts(
    tasks_doc: dict[str, Any],
    canary_key_doc: dict[str, Any] | None,
    verdicts_doc: dict[str, Any] | None,
    pairs_in_order: list[tuple[SubClaim, str]],
) -> dict[str, Any]:
    """Ingest CHECK verdicts — id-join, canary check (raises
    ``judge_seam.CanaryAbortError`` on a bad canary — CHECK is the RELIED
    UPON verdict this loop's stop conditions key off), fail-closed-to-ABSENT
    fill, then mutate each ``SubClaim`` in *pairs_in_order* in place: a
    SUPPORTS verdict on any checked note marks the claim COVERED (recorded
    once, first SUPPORTS wins); every checked pair is recorded on
    ``checked_against`` regardless of verdict (never re-emitted next round).

    Returns ``{"newly_covered": [...], "halt": bool, "halt_reason": str,
    "errors": [...], "warnings": [...]}``.
    """
    real_task_ids = [t["id"] for t in tasks_doc.get("tasks", []) if t.get("kind") == _CHECK_KIND]
    canaries = (canary_key_doc or {}).get("canaries", {})
    real_task_ids = [tid for tid in real_task_ids if tid not in canaries]
    if not real_task_ids:
        return {"newly_covered": [], "halt": False, "halt_reason": "", "errors": [], "warnings": []}

    if judge_seam.fanout_incomplete(tasks_doc, verdicts_doc):
        return {
            "newly_covered": [],
            "halt": True,
            "halt_reason": (
                "verdicts file absent/empty for a non-empty CHECK task "
                "set — fan-out did not complete."
            ),
            "errors": [
                "stop-condition CHECK judge-fanout HALT: verdicts "
                "missing/empty. This is NOT a pass."
            ],
            "warnings": [],
        }

    verdict_by_id: dict[str, str] = {}
    for v in (verdicts_doc or {}).get("verdicts", []):
        vid = v.get("id")
        if vid:
            verdict_by_id[vid] = str(v.get("verdict", ""))

    # Canary check FIRST — CHECK is relied upon by every stop condition
    # this loop computes; an untrustworthy judge invalidates all of them.
    judge_seam.check_canaries(canaries, verdict_by_id)

    filled, missing_ids, unrecognized_ids = judge_seam.fail_closed_fill(
        real_task_ids, verdict_by_id, _COVERAGE_VOCAB, _COVERAGE_FAIL_CLOSED_DEFAULT,
    )

    warnings: list[str] = []
    if missing_ids:
        warnings.append(
            f"stop-condition CHECK: {len(missing_ids)} task(s) missing a "
            f"verdict, defaulted ABSENT/uncovered (fail-closed): {missing_ids}"
        )
    if unrecognized_ids:
        warnings.append(
            f"stop-condition CHECK: {len(unrecognized_ids)} task(s) had an "
            f"unrecognized verdict string, defaulted ABSENT/uncovered "
            f"(fail-closed): {unrecognized_ids}"
        )

    newly_covered: list[str] = []
    id_by_pair_index = {tid: idx for idx, tid in enumerate(
        [t["id"] for t in tasks_doc.get("tasks", []) if t.get("kind") == _CHECK_KIND and t["id"] not in canaries]
    )}
    for tid, idx in id_by_pair_index.items():
        sc, note_path = pairs_in_order[idx]
        sc.checked_against.add(note_path)
        verdict = filled.get(tid, _COVERAGE_FAIL_CLOSED_DEFAULT)
        if verdict == "SUPPORTS" and not sc.covered:
            sc.covered = True
            sc.supporting_note = note_path
            newly_covered.append(sc.id)

    return {
        "newly_covered": newly_covered, "halt": False, "halt_reason": "",
        "errors": [], "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# HOP — the uncovered claim's text becomes the next reasoning-conditioned
# traversal hop, driven directly off the wrapped TraversalEngine.
# ---------------------------------------------------------------------------

def _prime_engine_for_claim_hop(engine: TraversalEngine, claim_text: str) -> bool:
    """Reconfigure *engine* to spend exactly ONE more hop toward
    *claim_text* — re-classifies intent off the CLAIM (not the original
    query), and extends the engine's depth budget by exactly one hop from
    wherever it currently stands. Returns False when the engine's frontier
    is already empty (nothing left to expand from at all — see
    ``REASON_SATURATION``).
    """
    if not engine._frontier:
        return False
    engine.routed_tags, engine.route_matched = classify_intent(claim_text)
    engine.depth = engine._hop_index + 1
    engine.done = False
    return True


# ---------------------------------------------------------------------------
# CoverageLoop — the stepwise DRAFT -> CHECK -> HOP state machine
# ---------------------------------------------------------------------------

class CoverageLoop:
    """Drives DRAFT -> CHECK -> HOP across the harness fan-out boundary,
    wrapping an already-constructed ``TraversalEngine`` (this loop never
    builds its own engine or duplicates ``map.py``/``router.py`` work — it
    consumes ``TraversalEngine``'s public surface plus its ``_frontier``
    internal exactly as ``traverse.py``'s own hop-prune step does).

    Usage (mirrors every other cold-agent-judge gate in this codebase):
        loop = CoverageLoop(engine)
        tasks_doc = loop.emit_draft(query)
        # ... fan-out ...
        loop.ingest_draft(verdicts_doc)
        while not loop.done:
            tasks_doc = loop.emit_check()
            if tasks_doc is not None:
                # ... fan-out ...
                loop.ingest_check(verdicts_doc)
            if loop.done:
                break
            tasks_doc = loop.emit_hop()
            if tasks_doc is not None:
                # ... fan-out ...
                loop.ingest_hop(verdicts_doc)
        result = loop.result()

    **The four stop conditions**, checked at the point each becomes
    knowable:
      - all-covered           -> after ``ingest_check`` if every sub-claim
                                  is COVERED.
      - budget-exhausted       -> at the top of ``emit_hop`` once
                                  ``self.hops_spent >= max_hops``; every
                                  still-uncovered claim is abstained with
                                  this reason.
      - saturation             -> (a) ``emit_hop`` finds the wrapped
                                  engine's frontier already empty (nothing
                                  left to expand from at all), or (b) a
                                  completed hop round leaves
                                  ``len(engine.visited)`` unchanged.
      - uncovered-maps-to-no-edge -> ``emit_hop`` primes the engine toward
                                  the target claim and the engine's own
                                  ``emit_hop_prune`` returns None with a
                                  non-empty frontier — the claim's routed
                                  edge tags have no fresh candidate edge
                                  anywhere in the currently-visited graph.
    """

    def __init__(self, engine: TraversalEngine, *, max_hops: int = MAX_STOP_HOPS) -> None:
        self.engine = engine
        self.max_hops = max_hops

        self.query = ""
        self.sub_claims: list[SubClaim] = []
        self.hops_spent = 0
        self.draft_malformed = False

        self.done = False
        self.halted = False
        self.halt_reason = ""
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.abstentions: dict[str, AbstentionEntry] = {}  # claim_id -> entry

        self._draft_task_state: tuple[dict[str, Any], dict[str, Any]] | None = None
        self._check_state: dict[str, Any] | None = None
        self._active_claim: SubClaim | None = None

    # --- DRAFT ---

    def emit_draft(self, query: str) -> dict[str, Any]:
        self.query = query
        result = emit_draft_task(query, self.engine)
        self._draft_task_state = (result["tasks_doc"], result["canary_key_doc"])
        return result["tasks_doc"]

    def ingest_draft(self, verdicts_doc: dict[str, Any] | None) -> dict[str, Any]:
        assert self._draft_task_state is not None, "ingest_draft called before emit_draft"
        tasks_doc, canary_key_doc = self._draft_task_state
        result = ingest_draft_verdicts(tasks_doc, canary_key_doc, verdicts_doc)
        self.errors.extend(result["errors"])
        self.warnings.extend(result["warnings"])
        if result["halt"]:
            self.halted = True
            self.halt_reason = result["halt_reason"]
            self.done = True
            return result

        self.sub_claims = result["sub_claims"]
        self.draft_malformed = any("parseable" in w for w in result["warnings"])
        if not self.sub_claims:
            self.done = True
        return result

    # --- CHECK ---

    def emit_check(self) -> dict[str, Any] | None:
        emitted = emit_check_tasks(self.sub_claims, self.engine)
        if emitted is None:
            self._check_state = None
            return None
        self._check_state = emitted
        return emitted["tasks_doc"]

    def ingest_check(self, verdicts_doc: dict[str, Any] | None) -> dict[str, Any]:
        assert self._check_state is not None, "ingest_check called before a non-None emit_check"
        emitted = self._check_state
        result = ingest_check_verdicts(
            emitted["tasks_doc"], emitted["canary_key_doc"], verdicts_doc,
            emitted["pairs_in_order"],
        )
        self.errors.extend(result["errors"])
        self.warnings.extend(result["warnings"])
        if result["halt"]:
            self.halted = True
            self.halt_reason = result["halt_reason"]
            self.done = True
            return result

        if all(sc.covered for sc in self.sub_claims):
            self.done = True
        return result

    # --- HOP ---

    def _next_uncovered_claim(self) -> SubClaim | None:
        for sc in self.sub_claims:
            if not sc.covered and sc.id not in self.abstentions:
                return sc
        return None

    def emit_hop(self) -> dict[str, Any] | None:
        """Emit one claim-targeted hop-prune task doc, or ``None`` (and
        possibly marks ``self.done``) when there is nothing productive
        left to hop toward this round."""
        self._active_claim = None
        if self.done:
            return None

        claim = self._next_uncovered_claim()
        if claim is None:
            # Every uncovered claim already has a definitive reason
            # (or there are none) -> nothing left to hop toward.
            self.done = True
            return None

        if self.hops_spent >= self.max_hops:
            self._abstain_all_remaining(REASON_BUDGET_EXHAUSTED, f"hop budget ({self.max_hops}) exhausted")
            self.done = True
            return None

        primed = _prime_engine_for_claim_hop(self.engine, claim.text)
        if not primed:
            self._abstain_all_remaining(
                REASON_SATURATION,
                "the traversal frontier is empty — nothing left to expand from",
            )
            self.done = True
            return None

        tasks_doc = self.engine.emit_hop_prune()
        if tasks_doc is None:
            # Frontier was non-empty (checked above) but yielded zero
            # fresh candidates under this claim's routed edge tags: the
            # corpus structurally cannot reach evidence for this claim.
            self.abstentions[claim.id] = AbstentionEntry(
                claim_id=claim.id, claim=claim.text, reason=REASON_NO_EDGE,
                detail=(
                    f"routed tags {sorted(self.engine.routed_tags)} yield no "
                    f"fresh candidate edge from the current visited graph"
                ),
            )
            return None

        self._active_claim = claim
        return tasks_doc

    def ingest_hop(self, verdicts_doc: dict[str, Any] | None) -> dict[str, Any]:
        assert self._active_claim is not None, "ingest_hop called without a claim primed by emit_hop"
        before = len(self.engine.visited)
        result = self.engine.ingest_hop_prune(verdicts_doc)
        self.errors.extend(result.get("errors", []))
        self.warnings.extend(result.get("warnings", []))
        if result.get("halt"):
            self.halted = True
            self.halt_reason = result.get("halt_reason", "")
            self.done = True
            return result

        self.hops_spent += 1
        after = len(self.engine.visited)
        if after == before:
            self._abstain_all_remaining(
                REASON_SATURATION,
                "this hop added no new visited node — the traversal has saturated",
            )
            self.done = True
        self._active_claim = None
        return result

    def _abstain_all_remaining(self, reason: str, detail: str) -> None:
        for sc in self.sub_claims:
            if not sc.covered and sc.id not in self.abstentions:
                self.abstentions[sc.id] = AbstentionEntry(
                    claim_id=sc.id, claim=sc.text, reason=reason, detail=detail,
                )

    # --- result ---

    def result(self) -> dict[str, Any]:
        """The structured, load-bearing return — the UNCOVERED sub-claims
        at stop time, each tagged with why, for a downstream faithfulness
        gate / engine assembly to consume directly (never prose)."""
        # Any sub-claim that is neither covered nor already abstained
        # when the loop ends (e.g. an early HALT) still needs an honest
        # entry — never silently drop it from the abstention set.
        for sc in self.sub_claims:
            if not sc.covered and sc.id not in self.abstentions:
                self.abstentions[sc.id] = AbstentionEntry(
                    claim_id=sc.id, claim=sc.text, reason=REASON_UNCHECKED,
                    detail="the loop ended before this claim was resolved",
                )

        abstention_set = [
            {"claim_id": a.claim_id, "claim": a.claim, "reason": a.reason, "detail": a.detail}
            for a in self.abstentions.values()
        ]
        all_covered = bool(self.sub_claims) and not abstention_set
        if not self.sub_claims:
            stop_reason = "no-claims"
        elif all_covered:
            stop_reason = "all-covered"
        else:
            reasons = sorted({a.reason for a in self.abstentions.values()})
            stop_reason = "|".join(reasons) if reasons else "unknown"

        return {
            "query": self.query,
            "sub_claims": [
                {
                    "id": sc.id, "text": sc.text, "covered": sc.covered,
                    "supporting_note": sc.supporting_note,
                }
                for sc in self.sub_claims
            ],
            "abstention_set": abstention_set,
            "stop_reason": stop_reason,
            "hops_spent": self.hops_spent,
            "draft_malformed": self.draft_malformed,
            "halted": self.halted,
            "halt_reason": self.halt_reason,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }
