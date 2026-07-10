# SPDX-License-Identifier: AGPL-3.0-or-later
"""review/autonomy.py — the gate-policy engine + deviation log +
the deterministic tool-op registry.

★ SINGLE-HUMAN-GATE DESIGN (2026-07-09): only ``approve-protocol`` (Gate 1,
the plan/scope gate before search) is a human gate. Every downstream gate —
``coverage-gate``, ``approve-framework``, ``approve-manuscript``,
``approve-review`` — resolves AUTONOMOUSLY through the gate-policy engine
below, and an auto-resolved decision is FINAL THE MOMENT IT RESOLVES: no
``provisional`` stamp, no async-veto window, no user-facing
provisional/confirmed bookkeeping. The §1.7 async-veto window
(``VetoWindow``/``open_veto_window``/``cast_veto``/
``clear_provisional_if_elapsed``/``check_declare_final_gate``) and the
``rv dag veto`` CLI surface it backed were REMOVED for this reason — see
DEVLOG.md. This module is the single-sourced home (mirrors
``check_gates.build_approve_payload``'s assembler pattern, charter §6
reuse-over-create) for TWO things that were previously a human keypress at
``rv dag approve``:

  1. **The gate-policy engine** (§1.2) — ``classify_disposition`` maps any
     mechanical-gate outcome to exactly ONE of GO / GO-WITH-RESIDUE / REVISE /
     HALT-DECLARE, by FAILURE CLASS. Adapters (``evaluation_from_*``) turn the
     existing gate payload shapes (``check_gates.build_approve_payload``,
     ``review_board.run_review_board``, ``check_framework_gate``,
     ``check_saturation_backstop``) into the normalized ``GateEvaluation``
     input, so no existing gate is reimplemented — only consumed.

  2. **The deviation log** (§1.5, D2) — ``record_deviation`` writes the
     DECLARED v(k)->v(k+1) transparency block; ``check_undeclared_deviation``
     is the REPURPOSED denominator-shrink BLOCK: a corpus delta from the
     frozen baseline is a BLOCK unless every citekey delta is declared,
     citekey-for-citekey, in ``_deviations.md``. This is the mechanical
     teeth that keeps D2 (all scope revisions auto) out of fishing
     territory — see the leak-planted acceptance test in
     ``tests/test_review_autonomy.py``. This BLOCK is a SEPARATE, fail-closed
     safety net (stops a silent corpus/criteria mutation) — it is NOT the
     removed async-veto/provisional machinery and stays fully intact.

  3. **The deterministic tool-op registry** (verb-consolidation D4) — the
     ``OP_REGISTRY``/``run_tool_op`` seam a DAG ``"type": "tool"`` node
     invokes IN-PROCESS (no subprocess, no human, no CLI verb) when the
     runner executes it (``dag/verbs.py``'s ``_auto_execute_tool_nodes``).
     No op is reimplemented here — every entry is a thin call-through to the
     existing library function (``run_sweep_from_protocol``,
     ``SemanticScholarAdapter``, ``coverage_report``, ``relations_report``).

Stdlib only (+ intra-package imports). Hermetic in tests — no live LLM/network
call is required to exercise the disposition/deviation logic; the op
registry's network-touching ops are exercised via injected fakes in tests.

sr: D4 (verb consolidation)
"""
from __future__ import annotations

import dataclasses
import datetime
import re
from pathlib import Path
from typing import Any, Callable

from research_vault.research import (
    _ARXIV_NEW_RE,
    _ARXIV_OLD_RE,
    _ASTA_SCHEME_PREFIXES,
    _DOI_BARE_RE,
    _S2_SHA_RE,
)

# ---------------------------------------------------------------------------
# 1. Dispositions — the gate-policy engine (§1.2)
# ---------------------------------------------------------------------------

GO = "GO"
GO_WITH_RESIDUE = "GO-WITH-RESIDUE"
REVISE = "REVISE"
HALT_DECLARE = "HALT-DECLARE"
# A coverage-gate-only disposition — backstop-terminated (open
# frontier) + remediation budget remaining + the last wave found something
# new. Never returned by `classify_coverage_gate`/`classify_disposition`
# (the general gate-policy engine); only by
# `review.remediation.resolve_coverage_gate`, which extends the coverage-gate
# disposition specifically. `dag/verbs.py` is the sole consumer that acts on
# it (dispatches one bounded remediation round, `review.remediation`).
REMEDIATE = "REMEDIATE"

_VALID_DISPOSITIONS: frozenset[str] = frozenset(
    {GO, GO_WITH_RESIDUE, REVISE, HALT_DECLARE, REMEDIATE}
)


@dataclasses.dataclass
class DispositionResult:
    """The gate-policy engine's output: exactly one disposition + why.

    charter §2: every non-GO is a loud, first-class artifact (``reason`` +
    ``evidence`` are always populated, never blank on a non-GO). Every GO is
    either fully-green (``GO``) or explicitly residue-annotated
    (``GO_WITH_RESIDUE``) — there is no green-and-empty path.
    """

    disposition: str
    reason: str
    evidence: dict[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.disposition not in _VALID_DISPOSITIONS:
            raise ValueError(
                f"unknown disposition {self.disposition!r}; valid: {sorted(_VALID_DISPOSITIONS)}"
            )

    @property
    def is_go(self) -> bool:
        return self.disposition in (GO, GO_WITH_RESIDUE)

    @property
    def is_halt(self) -> bool:
        return self.disposition == HALT_DECLARE


@dataclasses.dataclass
class GateEvaluation:
    """Normalized input to ``classify_disposition`` — the four failure
    classes from design §1.2's table, reduced to four independent signals.
    Build one of these via an ``evaluation_from_*`` adapter; never hand-roll
    one against a gate's raw payload shape (that duplicates the adapter).
    """

    blocking: list[str] = dataclasses.field(default_factory=list)
    canary_aborted: bool = False
    not_run: list[str] = dataclasses.field(default_factory=list)
    residue: str | None = None
    revise_budget_exhausted: bool = False


def classify_disposition(ev: GateEvaluation) -> DispositionResult:
    """The gate-policy engine.

    Priority order (most severe first — a gate can trip more than one
    signal; the MOST severe wins, never averaged):

      1. Untrustworthy signal (canary abort)        -> HALT-DECLARE, fail-closed.
         Never auto-retry the same broken judge (charter §10).
      2. Floor gate NOT RUN / incomplete fan-out     -> HALT-DECLARE, fail-closed.
         A floor gate that didn't run must never look like a pass (explore-rl #3).
      3. Deterministic fixable BLOCK, budget spent   -> HALT-DECLARE.
      4. Deterministic fixable BLOCK, budget left    -> REVISE (bounded auto-revise).
      5. Declared residue (non-convergence)          -> GO-WITH-RESIDUE.
      6. Nothing wrong                               -> GO.
    """
    if ev.canary_aborted:
        return DispositionResult(
            HALT_DECLARE,
            "canary-abort: the judge signal is untrustworthy (broken-harsh, "
            "rubber-stamping, or blind to the planted probe) — fail-closed, "
            "never auto-retry the same broken judge.",
            {"canary_aborted": True},
        )
    if ev.not_run:
        return DispositionResult(
            HALT_DECLARE,
            f"floor gate(s) not run / incomplete fan-out: {ev.not_run} — a "
            "floor gate that never ran cannot self-certify a GO.",
            {"not_run": list(ev.not_run)},
        )
    if ev.blocking:
        if ev.revise_budget_exhausted:
            return DispositionResult(
                HALT_DECLARE,
                f"deterministic BLOCK(s) persisted after the bounded "
                f"auto-revise budget was exhausted: {ev.blocking}",
                {"blocking": list(ev.blocking), "revise_budget_exhausted": True},
            )
        return DispositionResult(
            REVISE,
            f"deterministic, fixable BLOCK(s): {ev.blocking} — dispatch a "
            "bounded auto-revise round targeting the specific finding.",
            {"blocking": list(ev.blocking)},
        )
    if ev.residue:
        return DispositionResult(
            GO_WITH_RESIDUE,
            f"declared non-convergence residue: {ev.residue!r} — proceeding "
            "(HR-style: log the residue and continue), residue surfaced loudly.",
            {"residue": ev.residue},
        )
    return DispositionResult(GO, "every binding gate passes.", {})


# ---------------------------------------------------------------------------
# Adapters — turn an existing gate's real payload shape into a GateEvaluation
# ---------------------------------------------------------------------------

def evaluation_from_structural_payload(payload: dict[str, Any]) -> GateEvaluation:
    """Adapt ``manuscript.check_gates.build_approve_payload``'s
    ``{ok, blocking, signals, not_run, canary_aborted}`` shape (hermetic-bib /
    equation / support-matcher) into a ``GateEvaluation``.

    ``canary_aborted`` is read as a TOP-LEVEL flag, not
    inferred from ``blocking`` text — a support-matcher ``CanaryAbortError``
    must classify as HALT-DECLARE (untrustworthy signal, priority 1), never
    REVISE (a canary-abort landing only in ``blocking`` would be downgraded
    to an ordinary fixable BLOCK and dispatch a bounded auto-revise against
    the SAME broken judge — the exact priority violation the gate-policy
    engine exists to prevent; charter §10, never auto-retry an untrustworthy
    judge). Absent
    key defaults to ``False`` — backward compatible with any older payload
    shape that predates this field.
    """
    return GateEvaluation(
        blocking=list(payload.get("blocking", [])),
        not_run=list(payload.get("not_run", [])),
        canary_aborted=bool(payload.get("canary_aborted", False)),
    )


def evaluation_from_board(
    board_result: dict[str, Any],
    *,
    canary_aborted: bool = False,
) -> GateEvaluation:
    """Adapt ``review_board.run_review_board``'s
    ``{cleared, not_cleared, ...}`` shape into a ``GateEvaluation``.

    ``run_review_board`` already runs its OWN bounded N=2/hardcap-3 unroll
    internally (this reuses that machinery rather than reinventing a
    second revise loop) — so "not cleared after N rounds" IS "revise budget
    exhausted" from the outer gate-policy engine's point of view; there is
    no further external revise to dispatch.

    ★ PR-B5 (decision #6): a
    board quality shortfall (CONTENT/SELFCONT/ADVERS/FRAMEWORK axis not
    cleared after the bounded revise rounds) is deliberately NOT the same
    failure class as an integrity BLOCK (bib/support). "The output IS the
    deliverable" — a not-cleared board routes to ``residue`` (never
    ``blocking``), so ``classify_disposition`` returns GO-WITH-RESIDUE, not
    HALT-DECLARE. The ONLY board-side failure that still HALTs is a canary
    abort (an untrustworthy judge — priority #1, checked first below,
    unchanged from before this PR).
    """
    cleared = bool(board_result.get("cleared", False))
    if canary_aborted:
        # Untrustworthy signal — never routed to residue (charter §10: never
        # auto-trust an untrustworthy judge's "quality shortfall").
        return GateEvaluation(canary_aborted=True)
    residue: str | None = None
    if not cleared:
        nc = board_result.get("not_cleared") or {}
        residue = (
            str(nc.get("persistent_weakness", "")).strip()
            or "; ".join(nc.get("failing_dims", []))
            or "review-board did not clear"
        )
    return GateEvaluation(residue=residue)


def evaluation_from_framework_gate(ok: bool, msg: str) -> GateEvaluation:
    """Adapt ``manuscript.types.lit_review.check_framework_gate``'s
    ``(ok, msg)`` shape into a ``GateEvaluation``. An empty/malformed spine
    is a deterministic, fixable BLOCK — eligible for the bounded auto-revise
    extension.
    """
    if ok:
        return GateEvaluation()
    return GateEvaluation(blocking=[msg])


def evaluation_from_framework_critic(payload: dict[str, Any]) -> GateEvaluation:
    """Adapt ``manuscript.types.lit_review.check_framework_critique_verdict``'s
    ``{blocking, not_run, canary_aborted}`` structural-payload shape into a
    ``GateEvaluation`` (framework-gate-autonomy design, option A, 2026-07-09).

    This is the SAME shape ``evaluation_from_structural_payload`` already
    consumes (support-matcher / equation / hermetic-bib) — a thin,
    named call-through, not a second disposition path (charter §6): the
    critic's payload already carries ``canary_aborted`` as a top-level flag,
    so an untrustworthy (canary-mismatched) verdict classifies HALT-DECLARE
    at priority 1, never downgraded to an ordinary fixable BLOCK/REVISE.
    """
    return evaluation_from_structural_payload(payload)


def classify_coverage_gate(
    saturation_info: dict[str, Any],
    *,
    coverage_gaps_path: Path | None = None,
    source_coverage_info: dict[str, Any] | None = None,
) -> DispositionResult:
    """§1.6: the coverage-gate disposition, keyed to the EXACT shipped
    0.2.4+ ``_saturation.md`` ``stop_reason:`` contract
    (``review.check_saturation_backstop``'s return shape).

    - a DECLARED protocol source is DARK this sweep (``source_coverage_info
      ["declared_dark"]`` non-empty, ``review.check_source_coverage``)
      -> HALT-DECLARE, fail-closed, BEFORE the saturation logic below runs
      at all — a corpus can never be certified saturated while a source
      named in the protocol's ``sources:`` was never actually reached
      (pre-publish hardening batch, 2026-07-09 downstream e2e-run finding:
      a dark source looked identical to a healthy sweep at this gate).
    - ``stop_reason == "saturated"``               -> GO.
    - ``stop_reason == "backstop:N-waves"``         -> GO-WITH-RESIDUE, IFF
      the required ``_coverage-gaps.md`` residue note exists; its absence is
      itself a HALT-DECLARE (the open frontier was never declared).
    - absent / malformed / anything else            -> HALT-DECLARE,
      fail-closed (never treat an unparseable stop-reason as saturated —
      charter §2 whitelist-not-blacklist).
    """
    if source_coverage_info is not None and source_coverage_info.get("declared_dark"):
        declared_dark = source_coverage_info["declared_dark"]
        return DispositionResult(
            HALT_DECLARE,
            "coverage-gate: source(s) declared in the protocol's `sources:` "
            f"list were DARK this sweep — {', '.join(declared_dark)} — every "
            "cell for each errored or returned zero hits across ALL angles. "
            "The corpus cannot be certified saturated while a declared "
            "source was never actually reached; re-run the sweep once the "
            "source is reachable before re-evaluating this gate.",
            {"declared_dark_sources": declared_dark},
        )

    if not saturation_info.get("exists", False):
        return DispositionResult(
            HALT_DECLARE,
            "no _saturation.md found — the saturation record is missing, "
            "cannot self-certify coverage-gate.",
            {"stop_reason": ""},
        )

    stop_reason = str(saturation_info.get("stop_reason", "")).strip()

    if stop_reason == "saturated":
        return DispositionResult(
            GO,
            "stop_reason == 'saturated' (2-consecutive-zero rule fired).",
            {"stop_reason": stop_reason},
        )

    if saturation_info.get("is_backstop"):
        if coverage_gaps_path is not None and not coverage_gaps_path.exists():
            return DispositionResult(
                HALT_DECLARE,
                "backstop-terminated but the REQUIRED _coverage-gaps.md "
                f"residue note is missing at {coverage_gaps_path} — the "
                "open frontier was never declared.",
                {"stop_reason": stop_reason},
            )
        return DispositionResult(
            GO_WITH_RESIDUE,
            f"stop_reason == {stop_reason!r} (backstop-terminated, NOT "
            "saturated) — declared non-convergence residue; the whitelist-"
            "SIGNAL trips loudly but does not block.",
            {"stop_reason": stop_reason},
        )

    # Neither the exact "saturated" string nor a recognized "backstop:N-waves"
    # form — a non-canonical spelling, free prose, or garbage. Fail-closed:
    # this must NEVER be silently treated as saturated (charter §2).
    return DispositionResult(
        HALT_DECLARE,
        f"stop_reason {stop_reason!r} is neither the exact string "
        "'saturated' nor a recognized 'backstop:N-waves' form — the "
        "saturation record is untrustworthy/malformed, fail-closed.",
        {"stop_reason": stop_reason},
    )


def classify_coverage_gate_with_deviation_check(
    run_state_meta: dict[str, Any],
    saturation_info: dict[str, Any],
    *,
    corpus_path: Path,
    deviations_path: Path,
    coverage_gaps_path: Path | None = None,
    source_coverage_info: dict[str, Any] | None = None,
) -> DispositionResult:
    """The LIVE coverage-deviation BLOCK — wires
    ``check_undeclared_deviation`` (D2) into the coverage-gate --auto
    path, in front of (not behind) the saturation disposition.

    The frozen corpus citekey-set is stamped into ``run_state_meta`` (a
    mutable dict — the caller passes ``run_state.meta`` directly and is
    responsible for persisting it, e.g. via ``store.save``) the FIRST time
    this function is called for a given scope. Every subsequent call
    compares the corpus currently on disk against that frozen baseline via
    ``check_undeclared_deviation``: an undeclared delta is a DIRECT
    HALT-DECLARE (never routed through the generic bounded-auto-revise
    class — a silent corpus edit must surface to a human, not be
    "fixed" by an autonomous revise round, per the transparency-not-
    permission contract). A fully declared delta (recorded via
    ``record_deviation`` into ``deviations_path``) passes through to the
    normal ``classify_coverage_gate`` saturation-based disposition.

    ★ Engineering note (grounded against the actual Phase-1 DAG
    shape — review-scope -> approve-protocol -> review-search ->
    review-snowball -> coverage-gate): an earlier design's prose said the
    frozen baseline is stamped "at approve-protocol". No corpus exists at
    that point in the shipped DAG (``_corpus.md`` is a review-snowball
    output, downstream of approve-protocol) — there is nothing to stamp
    yet. This function instead stamps the baseline at coverage-gate's FIRST
    evaluation (the earliest point a citekey set actually exists), which is
    the structurally-sound equivalent: "frozen the first time the corpus is
    evaluated" mirrors "frozen at the human pre-registration gate" in
    spirit (a single, load-bearing baseline that every later delta is
    measured against) without requiring citekeys that don't exist yet.
    """
    current_citekeys = set(_parse_corpus_citekeys_helper(corpus_path))
    frozen_raw = run_state_meta.get("frozen_corpus_citekeys")

    if frozen_raw is None:
        run_state_meta["frozen_corpus_citekeys"] = sorted(current_citekeys)
    else:
        frozen_citekeys = set(frozen_raw)
        ok, msg = check_undeclared_deviation(frozen_citekeys, current_citekeys, deviations_path)
        if not ok:
            return DispositionResult(
                HALT_DECLARE,
                msg,
                {"undeclared_deviation": True, "frozen": sorted(frozen_citekeys), "current": sorted(current_citekeys)},
            )

    return classify_coverage_gate(
        saturation_info,
        coverage_gaps_path=coverage_gaps_path,
        source_coverage_info=source_coverage_info,
    )


def _parse_corpus_citekeys_helper(corpus_path: Path) -> list[str]:
    """Thin call-through to ``review._parse_corpus_citekeys`` (reuse, charter
    §6) — a lazy import avoids a module-level circular import (``review``'s
    package ``__init__`` imports from other review submodules; autonomy.py
    is itself imported by ``review/verbs.py`` and ``dag/verbs.py``).
    """
    from research_vault.review import _parse_corpus_citekeys

    return _parse_corpus_citekeys(corpus_path)


# ---------------------------------------------------------------------------
# 2. The deviation log (§1.5, D2) — the transparency contract + repurposed BLOCK
# ---------------------------------------------------------------------------

# The two recognized `kind` values. `within-criteria-append`
# is the ONLY kind the autonomous remediation loop may self-author — its
# invariant (pre==post criteria, no removals) is asserted below, so the loop
# can never smuggle a criteria edit or a removal through this kind. A
# `criteria-change` deviation is unconstrained (any pre/post, any
# removed/added) and is human-authored only (never called by
# `review.remediation`). ``None`` (the default) is a generic/legacy
# deviation with no kind-specific invariant — back-compat for callers that
# predate this typing.
DEVIATION_KIND_WITHIN_CRITERIA_APPEND = "within-criteria-append"
DEVIATION_KIND_CRITERIA_CHANGE = "criteria-change"


def record_deviation(
    deviations_path: Path,
    *,
    version: int,
    pre_criteria: str,
    post_criteria: str,
    removed: list[str] | None = None,
    added: list[str] | None = None,
    rationale: str,
    kind: str | None = None,
    now: datetime.datetime | None = None,
) -> str:
    """Append a DECLARED ``v(k)->v(k+1)`` deviation block to
    ``_deviations.md`` (§1.5 requirement 1). Never a silent edit — every
    criteria/membership change goes through this function or it is
    undeclared (and will trip ``check_undeclared_deviation``'s BLOCK).

    ``kind`` (optional — ``None`` is back-compat with older callers):
      - ``"within-criteria-append"`` — asserts the invariant
        ``pre_criteria == post_criteria and removed == []``. This is the
        ONLY kind ``review.remediation``'s autonomous loop may author; the
        assertion means the loop structurally CANNOT self-author a criteria
        edit or a removal — a violation raises ``ValueError`` rather than
        silently recording an out-of-invariant block.
      - ``"criteria-change"`` — unconstrained; human-authored only (never
        called by the remediation loop).
      - ``None`` — no invariant enforced (generic/legacy deviation).

    Returns the appended block (for the caller to also push into the
    ``⟦RETURN⟧``/control-bus surface, per §1.5's auditability-teeth
    requirement — pushed, not merely written).
    """
    now = now or datetime.datetime.now(tz=datetime.timezone.utc)
    removed = removed or []
    added = added or []
    if kind == DEVIATION_KIND_WITHIN_CRITERIA_APPEND:
        if pre_criteria != post_criteria or removed:
            raise ValueError(
                "record_deviation: kind='within-criteria-append' requires "
                "pre_criteria == post_criteria AND removed == [] (the "
                "denominator may only GROW within the frozen criteria "
                "invariant). Got "
                f"pre_criteria==post_criteria: {pre_criteria == post_criteria!r}, "
                f"removed={removed!r}. A criteria edit or a removal must be "
                "recorded as a human-authored kind='criteria-change' "
                "deviation, never self-authored by the autonomous "
                "remediation loop."
            )
    kind_line = f"**Kind:** {kind}\n" if kind else ""
    block = (
        f"\n## Deviation v{version - 1} -> v{version} ({now.isoformat()})\n\n"
        f"{kind_line}"
        f"**Pre-criteria:**\n{pre_criteria}\n\n"
        f"**Post-criteria:**\n{post_criteria}\n\n"
        f"**Removed citekeys:** {', '.join(sorted(removed)) if removed else '(none)'}\n"
        f"**Added citekeys:** {', '.join(sorted(added)) if added else '(none)'}\n\n"
        f"**Rationale:** {rationale}\n"
    )
    if deviations_path.exists():
        text = deviations_path.read_text(encoding="utf-8")
    else:
        deviations_path.parent.mkdir(parents=True, exist_ok=True)
        text = (
            "# Deviation log\n\n"
            "Every scope/membership revision under D2's transparency "
            "contract (§1.5): DECLARED, PRISMA-integrated, reproducible, "
            "and final the moment it is recorded here (single-human-gate "
            "design, 2026-07-09 — no async-veto window).\n"
        )
    deviations_path.write_text(text + block, encoding="utf-8")
    return block


def _parse_deviation_citekey_deltas(deviations_path: Path) -> tuple[set[str], set[str]]:
    """Return ``(all_declared_removed, all_declared_added)`` citekeys across
    every block ``record_deviation`` has written to ``deviations_path``.

    Scoped to the two fixed lines this module itself writes — not a
    general markdown parser (there is nothing else in this file's format
    to parse robustly against).
    """
    if not deviations_path.exists():
        return set(), set()
    text = deviations_path.read_text(encoding="utf-8")
    removed: set[str] = set()
    added: set[str] = set()
    for m in re.finditer(r"^\*\*Removed citekeys:\*\*\s*(.*)$", text, re.MULTILINE):
        vals = m.group(1).strip()
        if vals and vals != "(none)":
            removed.update(v.strip() for v in vals.split(",") if v.strip())
    for m in re.finditer(r"^\*\*Added citekeys:\*\*\s*(.*)$", text, re.MULTILINE):
        vals = m.group(1).strip()
        if vals and vals != "(none)":
            added.update(v.strip() for v in vals.split(",") if v.strip())
    return removed, added


def check_undeclared_deviation(
    frozen_citekeys: set[str] | list[str],
    current_citekeys: set[str] | list[str],
    deviations_path: Path,
) -> tuple[bool, str]:
    """The REPURPOSED denominator-shrink BLOCK (§1.5, D2's mechanical
    teeth).

    Under D2, ALL scope/membership revisions are auto — this BLOCK no
    longer PREVENTS a corpus delta; it requires the delta be DECLARED
    (citekey-for-citekey) in ``_deviations.md`` before proceeding. An
    undeclared delta trips the BLOCK; a fully declared one passes.

    This is what keeps "all scope revisions auto" out of fishing territory:
    *the difference between D2 and fishing is transparency, not permission.*
    """
    frozen = set(frozen_citekeys)
    current = set(current_citekeys)
    actually_removed = frozen - current
    actually_added = current - frozen
    if not actually_removed and not actually_added:
        return True, "OK — no corpus delta vs the frozen baseline."

    declared_removed, declared_added = _parse_deviation_citekey_deltas(deviations_path)
    undeclared_removed = actually_removed - declared_removed
    undeclared_added = actually_added - declared_added
    if undeclared_removed or undeclared_added:
        return False, (
            "rv autonomy: coverage-gate BLOCKED — undeclared corpus delta "
            f"vs the frozen baseline. Undeclared removed: "
            f"{sorted(undeclared_removed) or '(none)'}; undeclared added: "
            f"{sorted(undeclared_added) or '(none)'}. Every membership/"
            f"criteria change must be DECLARED in {deviations_path} "
            "(pre/post criteria + citekey deltas + rationale) via "
            "record_deviation() before proceeding — D2's mechanical "
            "transparency contract (§1.5). A silent corpus edit is not "
            "permitted even though scope revisions are auto."
        )
    return True, (
        f"OK — corpus delta ({len(actually_removed)} removed, "
        f"{len(actually_added)} added) is fully declared in {deviations_path}."
    )


# ---------------------------------------------------------------------------
# 3. The deterministic tool-op registry (verb-consolidation D4)
# ---------------------------------------------------------------------------
#
# Every entry is a thin call-through — NO op is reimplemented here. This is
# the natural single-sourced home for the op registry (it already single-
# sources gate dispositions; §3.3 of the consolidation doc says it can
# single-source op-dispatch too). A DAG "type": "tool" node carries
# {"op": "<name>", "args": {...}}; the runner (dag/verbs.py's
# _auto_execute_tool_nodes) looks the op up here and calls it IN-PROCESS —
# no subprocess, no human, no CLI verb.


def _op_sweep(
    *,
    protocol: str,
    out: str | None = None,
    budget: int = 65,
    per_cell_limit: int = 20,
    project: str | None = None,
    config: Any = None,
    **_: Any,
) -> Any:
    """The ``sweep`` tool op: run the parallel width-sweep AND write
    ``_search_hits.md`` — the
    mechanical half of what ``review-search`` used to be an agent node for.
    Returns the written path (str) when ``out`` is given, else the raw
    ``SweepResult`` (back-compat for any caller that doesn't want the
    artifact written, e.g. a unit test exercising the op in isolation).
    """
    from research_vault.sources.sweep import run_sweep_from_protocol, write_search_hits

    result = run_sweep_from_protocol(Path(protocol), budget=budget, per_cell_limit=per_cell_limit)
    if out is None:
        return result

    notes_index = None
    notes_title_index = None
    if project:
        from research_vault.config import load_config
        from research_vault.research import _load_notes_index, _load_notes_title_index

        cfg = config if config is not None else load_config()
        literature_dir = cfg.project_notes_dir(project) / "literature"
        notes_index = _load_notes_index(literature_dir)
        notes_title_index = _load_notes_title_index(literature_dir)

    written = write_search_hits(
        result, Path(out), notes_index=notes_index, notes_title_index=notes_title_index,
    )
    return str(written)


_SEED_FENCE_RE = re.compile(r"```seeds\s*\n(.*?)```", re.DOTALL)


def _is_valid_paper_id(token: str) -> bool:
    """Is ``token`` shaped like a paper identifier asta can resolve (DOI /
    arXiv id / S2 40-hex corpus id / scheme-prefixed form)?

    Reuses the SAME id shapes ``research.py``'s ``_normalize_paper_id_for_asta``
    already recognizes (charter §6 reuse-over-create) — no second id grammar
    to keep in sync. This is the hard backstop that keeps a stray frontmatter
    ``---``, a prose sentence, or a table row from ever reaching asta as a
    seed id, regardless of which extraction path found the line.
    """
    if not token or token.startswith("-"):
        # Defensive: asta parses a leading '-' as a CLI flag (the exact
        # crash this fix exists for) — never emit such a token, even if it
        # happened to look id-shaped after the dash.
        return False
    if any(token.upper().startswith(p) for p in _ASTA_SCHEME_PREFIXES):
        return True
    return bool(
        _S2_SHA_RE.match(token)
        or _ARXIV_NEW_RE.match(token)
        or _ARXIV_OLD_RE.match(token)
        or _DOI_BARE_RE.match(token)
    )


def _extract_seed_ids_from_screen(text: str) -> list[str]:
    """Extract the accepted seed paper-ids from a ``_screen.md`` note body.

    ``_screen.md`` is a real OKF-shaped note: YAML frontmatter (``---``
    delimiters), a prose PRISMA exclusion audit trail, THEN the accepted
    ids — not a bare id-per-line file. Two paths, in priority order:

      1. **Canonical**: a fenced ```seeds``` block (see
         ``review/style.py``'s ``review_screen_tips``) — every non-empty
         line inside it, validated against ``_is_valid_paper_id``.
      2. **Legacy fallback**: no fenced block present (an old bare-id
         ``_screen.md``, or a hand-edited one) — scan every line of the
         WHOLE file, but still validate each token against
         ``_is_valid_paper_id`` rather than accepting any non-empty,
         non-``#``, non-``|`` line. This is what makes the fallback safe
         against frontmatter/prose instead of just re-introducing the bug
         for anyone who skips the fence.

    Either path only ever returns id-shaped tokens — a frontmatter ``---``,
    a prose sentence, or a table row is silently EXCLUDED (charter §2: this
    is a narrow, unambiguous filter — not silently dropping a *malformed
    but intended* id, which would need a wider net; a prose sentence or a
    ``---`` was never an id contender in the first place).
    """
    fence_match = _SEED_FENCE_RE.search(text)
    if fence_match:
        lines = fence_match.group(1).splitlines()
    else:
        lines = text.splitlines()

    seed_ids: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("|"):
            continue
        if _is_valid_paper_id(stripped):
            seed_ids.append(stripped)
    return seed_ids


def _op_snowball(
    *,
    seed: str,
    out_dir: str,
    backstop_waves: int = 2,
    seed_cap: int | None = None,
    frontier_cap: int | None = None,
    fetch_budget: int | None = None,
    project: str | None = None,
    config: Any = None,
    **_: Any,
) -> Any:
    """The ``snowball`` tool op: run the both-
    direction, multi-round saturation walk AND write ``_corpus_raw.md`` +
    ``_saturation.md`` — replaces the removed single-paper, single-direction
    ``snowball-forward``/``snowball-backward`` ops (D4 predecessor, which
    never looped, never wrote an artifact, and had no stopping rule).

    ``seed`` is the path to the review-screen agent's ``_screen.md`` —
    parsed here (via ``_extract_seed_ids_from_screen``) for its accepted
    seed paper-id frontier, which lives in a fenced ```seeds``` block (the
    screen note's own prose PRISMA exclusion audit trail lives freely above
    it; see ``review/style.py``'s ``review_screen_tips``).

    ``seed_cap``/``frontier_cap``/``fetch_budget``: breadth x depth bounds
    (2026-07-09 — a broad-topic downstream-project validation walk ran unbounded for 1+
    hour). ``None`` (the manifest's default) lets
    ``run_snowball_to_saturation`` apply its own shipped defaults
    (``DEFAULT_SEED_CAP``/``DEFAULT_FRONTIER_CAP``/``DEFAULT_FETCH_BUDGET``
    — 25/25/200); passed through explicitly only when the DAG manifest
    overrides them.
    """
    from research_vault.sources.snowball import (
        run_snowball_to_saturation,
        write_corpus_raw,
        write_saturation,
    )

    seed_path = Path(seed)
    seed_ids: list[str] = []
    if seed_path.exists():
        seed_ids = _extract_seed_ids_from_screen(seed_path.read_text(encoding="utf-8"))

    # Resumable / log-as-you-go (2026-07-09): a long snowball walk that gets
    # dropped mid-flight resumes from its last completed round instead of
    # restarting from scratch — the checkpoint lives alongside the other
    # review-dir artifacts and is removed automatically on clean completion.
    out_dir_path = Path(out_dir)
    checkpoint_path = out_dir_path / "_snowball_checkpoint.json"

    cap_kwargs: dict[str, int] = {}
    if seed_cap is not None:
        cap_kwargs["seed_cap"] = seed_cap
    if frontier_cap is not None:
        cap_kwargs["frontier_cap"] = frontier_cap
    if fetch_budget is not None:
        cap_kwargs["fetch_budget"] = fetch_budget

    result = run_snowball_to_saturation(
        seed_ids, backstop_waves=backstop_waves, checkpoint_path=checkpoint_path,
        **cap_kwargs,
    )

    notes_index = None
    notes_title_index = None
    if project:
        from research_vault.config import load_config
        from research_vault.research import _load_notes_index, _load_notes_title_index

        cfg = config if config is not None else load_config()
        literature_dir = cfg.project_notes_dir(project) / "literature"
        notes_index = _load_notes_index(literature_dir)
        notes_title_index = _load_notes_title_index(literature_dir)

    corpus_raw_path = write_corpus_raw(
        result, out_dir_path / "_corpus_raw.md",
        notes_index=notes_index, notes_title_index=notes_title_index,
    )
    saturation_path = write_saturation(result, out_dir_path / "_saturation.md")
    return {
        "corpus_raw": str(corpus_raw_path),
        "saturation": str(saturation_path),
        "stop_reason": result.stop_reason,
    }


def _op_relevance_screen(
    *,
    corpus_raw: str,
    protocol: str,
    out: str,
    **_: Any,
) -> Any:
    """The ``relevance_screen`` tool op (PR-1, design 2026-07-10-trustworthy-
    curation-relevance-gate-design.md §3d) — the mechanical snowball-screen
    gate between ``review-snowball`` and ``review-curate``. Thin call-
    through to ``review.relevance.screen_corpus_raw`` (charter §6 — no
    mechanism reimplemented here).
    """
    from research_vault.review.relevance import screen_corpus_raw

    counts = screen_corpus_raw(Path(corpus_raw), Path(protocol), Path(out))
    return {"out": out, **counts}


def _op_relevance_verify_prep(
    *,
    corpus: str,
    protocol: str,
    out: str,
    **_: Any,
) -> Any:
    """The ``relevance_verify_prep`` tool op (PR-1, design §3b) — builds the
    cold verifier's canary-seeded input artifact from the final
    ``_corpus.md``. Thin call-through to
    ``review.relevance.build_verify_input``.
    """
    from research_vault.review.relevance import build_verify_input

    result = build_verify_input(Path(corpus), Path(protocol), Path(out))
    return {"out": out, **result}


def _op_coverage(*, project: str, scope: str, config: Any = None, **_: Any) -> Any:
    from research_vault.config import load_config
    from research_vault.review import coverage_report

    cfg = config if config is not None else load_config()
    return coverage_report(project, scope, config=cfg)


def _op_relations(*, project: str, scope: str, config: Any = None, **_: Any) -> Any:
    from research_vault.config import load_config
    from research_vault.review import relations_report

    cfg = config if config is not None else load_config()
    return relations_report(project, scope, config=cfg)


OP_REGISTRY: dict[str, Callable[..., Any]] = {
    "sweep": _op_sweep,
    "snowball": _op_snowball,
    "relevance_screen": _op_relevance_screen,
    "relevance_verify_prep": _op_relevance_verify_prep,
    "coverage": _op_coverage,
    "relations": _op_relations,
}


def run_tool_op(op: str, **kwargs: Any) -> Any:
    """Look up and call a registered deterministic op IN-PROCESS.

    Raises KeyError on an unregistered op (never silently no-ops — a tool
    node with a typo'd/unregistered op must fail loudly, not look like a
    no-op success).
    """
    if op not in OP_REGISTRY:
        raise KeyError(
            f"rv autonomy: unknown tool op {op!r}; registered ops: "
            f"{sorted(OP_REGISTRY)}"
        )
    return OP_REGISTRY[op](**kwargs)
