"""review/autonomy.py — NG-4/5/6: the gate-policy engine + async-veto +
deviation log + the deterministic tool-op registry.

Design of record: docs/superpowers/specs/2026-07-08-next-gen-lit-review-loop-design.md
(§1 the autonomy program) and 2026-07-08-rv-verb-consolidation.md (§3/§6 D4:
the ``tool`` node-kind + op registry).

This module is the single-sourced home (mirrors ``check_gates.build_approve_payload``'s
assembler pattern, charter §6 reuse-over-create) for THREE things that were
previously a human keypress at ``rv dag approve``:

  1. **The gate-policy engine** (§1.2) — ``classify_disposition`` maps any
     mechanical-gate outcome to exactly ONE of GO / GO-WITH-RESIDUE / REVISE /
     HALT-DECLARE, by FAILURE CLASS. Adapters (``evaluation_from_*``) turn the
     existing gate payload shapes (``check_gates.build_approve_payload``,
     ``review_board.run_review_board``, ``check_framework_gate``,
     ``check_saturation_backstop``) into the normalized ``GateEvaluation``
     input, so no existing gate is reimplemented — only consumed.

  2. **The async-veto window** (§1.7) — shared machinery for D1 (the
     framework choice) and D2 (any scope/membership deviation). An
     already-proceeding decision is stamped ``provisional: true`` on its
     note; ``check_declare_final_gate`` mechanically BLOCKs the terminal
     "declare final" step while the window is open or vetoed.

  3. **The deviation log** (§1.5, D2) — ``record_deviation`` writes the
     DECLARED v(k)->v(k+1) transparency block; ``check_undeclared_deviation``
     is the REPURPOSED denominator-shrink BLOCK: a corpus delta from the
     frozen baseline is a BLOCK unless every citekey delta is declared,
     citekey-for-citekey, in ``_deviations.md``. This is the mechanical
     teeth that keeps D2 (all scope revisions auto) out of fishing
     territory — see the leak-planted acceptance test in
     ``tests/test_review_autonomy.py``.

  4. **The deterministic tool-op registry** (verb-consolidation D4) — the
     ``OP_REGISTRY``/``run_tool_op`` seam a DAG ``"type": "tool"`` node
     invokes IN-PROCESS (no subprocess, no human, no CLI verb) when the
     runner executes it (``dag/verbs.py``'s ``_auto_execute_tool_nodes``).
     No op is reimplemented here — every entry is a thin call-through to the
     existing library function (``run_sweep_from_protocol``,
     ``SemanticScholarAdapter``, ``coverage_report``, ``relations_report``).

Stdlib only (+ intra-package imports). Hermetic in tests — no live LLM/network
call is required to exercise the disposition/veto/deviation logic; the op
registry's network-touching ops are exercised via injected fakes in tests.

sr: NG-4, NG-5, NG-6a, NG-6b (deviation log + PRISMA-adjacent), D4 (verb consolidation)
"""
from __future__ import annotations

import dataclasses
import datetime
import re
from pathlib import Path
from typing import Any, Callable

from research_vault.note import _parse_frontmatter
from research_vault.review.gap_scan import _stamp_frontmatter_field

# ---------------------------------------------------------------------------
# 1. Dispositions — the gate-policy engine (§1.2)
# ---------------------------------------------------------------------------

GO = "GO"
GO_WITH_RESIDUE = "GO-WITH-RESIDUE"
REVISE = "REVISE"
HALT_DECLARE = "HALT-DECLARE"

_VALID_DISPOSITIONS: frozenset[str] = frozenset({GO, GO_WITH_RESIDUE, REVISE, HALT_DECLARE})


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
    """The NG-4 §1.2 gate-policy engine.

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
    ``{ok, blocking, signals, not_run}`` shape (hermetic-bib / equation /
    support-matcher) into a ``GateEvaluation``.
    """
    return GateEvaluation(
        blocking=list(payload.get("blocking", [])),
        not_run=list(payload.get("not_run", [])),
    )


def evaluation_from_board(
    board_result: dict[str, Any],
    *,
    canary_aborted: bool = False,
) -> GateEvaluation:
    """Adapt ``review_board.run_review_board``'s
    ``{cleared, not_cleared, ...}`` shape into a ``GateEvaluation``.

    ``run_review_board`` already runs its OWN bounded N=2/hardcap-3 unroll
    internally (§1.3: NG-5 reuses this machinery rather than reinventing a
    second revise loop) — so "not cleared after N rounds" IS "revise budget
    exhausted" from the outer gate-policy engine's point of view; there is
    no further external revise to dispatch.
    """
    cleared = bool(board_result.get("cleared", False))
    blocking: list[str] = []
    if not cleared:
        nc = board_result.get("not_cleared") or {}
        blocking = list(nc.get("failing_dims", [])) or ["review-board did not clear"]
    return GateEvaluation(
        blocking=blocking,
        canary_aborted=canary_aborted,
        revise_budget_exhausted=not cleared,
    )


def evaluation_from_framework_gate(ok: bool, msg: str) -> GateEvaluation:
    """Adapt ``manuscript.types.lit_review.check_framework_gate``'s
    ``(ok, msg)`` shape into a ``GateEvaluation``. An empty/malformed spine
    is a deterministic, fixable BLOCK — eligible for the bounded auto-revise
    extension (§1.3, NG-5).
    """
    if ok:
        return GateEvaluation()
    return GateEvaluation(blocking=[msg])


def classify_coverage_gate(
    saturation_info: dict[str, Any],
    *,
    coverage_gaps_path: Path | None = None,
) -> DispositionResult:
    """§1.6: the coverage-gate disposition, keyed to the EXACT shipped
    0.2.4+ ``_saturation.md`` ``stop_reason:`` contract
    (``review.check_saturation_backstop``'s return shape).

    - ``stop_reason == "saturated"``               -> GO.
    - ``stop_reason == "backstop:N-waves"``         -> GO-WITH-RESIDUE, IFF
      the required ``_coverage-gaps.md`` residue note exists; its absence is
      itself a HALT-DECLARE (the open frontier was never declared).
    - absent / malformed / anything else            -> HALT-DECLARE,
      fail-closed (never treat an unparseable stop-reason as saturated —
      charter §2 whitelist-not-blacklist).
    """
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


# ---------------------------------------------------------------------------
# 2. The async-veto window (§1.7) — shared machinery for D1 + D2
# ---------------------------------------------------------------------------

DEFAULT_VETO_WINDOW_HOURS = 72


@dataclasses.dataclass
class VetoWindow:
    """A provisional decision open to an async human veto (§1.7). The
    persisted record (round-trips through ``to_dict``/``from_dict``) is
    what the caller stores in the decision surface (``_framework-decision.md``
    / a ``_deviations.md`` block / a JSON sidecar) — this dataclass itself
    holds no file handle.
    """

    kind: str  # "framework" | "deviation"
    opened_at: str  # iso8601
    window_hours: int = DEFAULT_VETO_WINDOW_HOURS
    decision_summary: str = ""
    vetoed: bool = False
    veto_reason: str | None = None

    @property
    def elapses_at(self) -> datetime.datetime:
        opened = datetime.datetime.fromisoformat(self.opened_at)
        return opened + datetime.timedelta(hours=self.window_hours)

    def has_elapsed(self, *, now: datetime.datetime | None = None) -> bool:
        now = now or datetime.datetime.now(tz=datetime.timezone.utc)
        return now >= self.elapses_at

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "VetoWindow":
        return cls(
            kind=d["kind"],
            opened_at=d["opened_at"],
            window_hours=int(d.get("window_hours", DEFAULT_VETO_WINDOW_HOURS)),
            decision_summary=d.get("decision_summary", ""),
            vetoed=bool(d.get("vetoed", False)),
            veto_reason=d.get("veto_reason"),
        )


def open_veto_window(
    note_path: Path,
    *,
    kind: str,
    decision_summary: str,
    window_hours: int = DEFAULT_VETO_WINDOW_HOURS,
    now: datetime.datetime | None = None,
) -> VetoWindow:
    """Open an async-veto window over an already-proceeding decision.

    Stamps ``provisional: true`` on ``note_path``'s frontmatter (never a
    silent edit — the terminal declare-final gate below mechanically enforces
    it) and returns the ``VetoWindow`` record the caller persists into the
    decision surface (``_framework-decision.md`` / a ``_deviations.md`` block).
    """
    now = now or datetime.datetime.now(tz=datetime.timezone.utc)
    window = VetoWindow(
        kind=kind,
        opened_at=now.isoformat(),
        window_hours=window_hours,
        decision_summary=decision_summary,
    )
    if note_path.exists():
        text = note_path.read_text(encoding="utf-8")
        note_path.write_text(_stamp_frontmatter_field(text, "provisional", "true"), encoding="utf-8")
    return window


def cast_veto(note_path: Path, window: VetoWindow, *, reason: str) -> VetoWindow:
    """A human veto over an OPEN window: the decision is rolled back and the
    run HALT-DECLAREs. Stamps ``provisional: vetoed`` (never silently
    reverted to a clean state — the audit trail of the veto survives).
    Returns the updated (mutated in place AND returned) window record.
    """
    window.vetoed = True
    window.veto_reason = reason
    if note_path.exists():
        text = note_path.read_text(encoding="utf-8")
        note_path.write_text(_stamp_frontmatter_field(text, "provisional", "vetoed"), encoding="utf-8")
    return window


def clear_provisional_if_elapsed(
    note_path: Path,
    window: VetoWindow,
    *,
    now: datetime.datetime | None = None,
) -> bool:
    """The mechanical enforcement half of §1.7: a note stays
    ``provisional: true`` until its veto window elapses UNVETOED.

    Returns True iff ``provisional`` was cleared to ``false`` (declare-final
    may now proceed). Returns False (no-op) if vetoed or the window has not
    yet elapsed.
    """
    if window.vetoed:
        return False
    if not window.has_elapsed(now=now):
        return False
    if note_path.exists():
        text = note_path.read_text(encoding="utf-8")
        note_path.write_text(_stamp_frontmatter_field(text, "provisional", "false"), encoding="utf-8")
    return True


def check_declare_final_gate(note_path: Path) -> tuple[bool, str]:
    """BLOCKs the terminal "declare final" step while ``note_path`` is still
    ``provisional: true`` (open veto window) or ``provisional: vetoed``.

    Mirrors ``review.check_protocol_gate``'s structural-gate shape
    (``(ok, msg)``) — reused by ``rv dag approve``/``rv dag veto`` wiring.
    """
    if not note_path.exists():
        return False, f"declare-final BLOCKED — {note_path} not found."
    text = note_path.read_text(encoding="utf-8")
    fields, _ = _parse_frontmatter(text)
    provisional = str(fields.get("provisional", "")).strip().lower()
    if provisional == "true":
        return False, (
            f"declare-final BLOCKED — {note_path} is still 'provisional: "
            "true' (an open async-veto window has not elapsed). §1.7: a "
            "framework choice or scope deviation cannot be declared final "
            "while its veto window is open."
        )
    if provisional == "vetoed":
        return False, (
            f"declare-final BLOCKED — {note_path} carries 'provisional: "
            "vetoed'. The decision was rolled back; re-run the gate before "
            "declaring final again."
        )
    return True, "OK"


# ---------------------------------------------------------------------------
# 3. The deviation log (§1.5, D2) — the transparency contract + repurposed BLOCK
# ---------------------------------------------------------------------------

def record_deviation(
    deviations_path: Path,
    *,
    version: int,
    pre_criteria: str,
    post_criteria: str,
    removed: list[str] | None = None,
    added: list[str] | None = None,
    rationale: str,
    now: datetime.datetime | None = None,
) -> str:
    """Append a DECLARED ``v(k)->v(k+1)`` deviation block to
    ``_deviations.md`` (§1.5 requirement 1). Never a silent edit — every
    criteria/membership change goes through this function or it is
    undeclared (and will trip ``check_undeclared_deviation``'s BLOCK).

    Returns the appended block (for the caller to also push into the
    ``⟦RETURN⟧``/control-bus surface, per §1.5's auditability-teeth
    requirement — pushed, not merely written).
    """
    now = now or datetime.datetime.now(tz=datetime.timezone.utc)
    removed = removed or []
    added = added or []
    block = (
        f"\n## Deviation v{version - 1} -> v{version} ({now.isoformat()})\n\n"
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
            "---\nprovisional: true\n---\n\n"
            "# Deviation log\n\n"
            "Every scope/membership revision under D2's transparency "
            "contract (§1.5): DECLARED, PRISMA-integrated, reproducible, "
            "async-vetoable.\n"
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
# 4. The deterministic tool-op registry (verb-consolidation D4)
# ---------------------------------------------------------------------------
#
# Every entry is a thin call-through — NO op is reimplemented here. This is
# the natural single-sourced home for the op registry (it already single-
# sources gate dispositions; §3.3 of the consolidation doc says it can
# single-source op-dispatch too). A DAG "type": "tool" node carries
# {"op": "<name>", "args": {...}}; the runner (dag/verbs.py's
# _auto_execute_tool_nodes) looks the op up here and calls it IN-PROCESS —
# no subprocess, no human, no CLI verb.


def _op_sweep(*, protocol: str, budget: int = 65, per_cell_limit: int = 20, **_: Any) -> Any:
    from research_vault.sources.sweep import run_sweep_from_protocol

    return run_sweep_from_protocol(Path(protocol), budget=budget, per_cell_limit=per_cell_limit)


def _op_snowball_forward(*, paper_id: str, limit: int = 20, **_: Any) -> Any:
    from research_vault.adapters.semantic_scholar import SemanticScholarAdapter

    return SemanticScholarAdapter().cited_by(paper_id, limit=limit)


def _op_snowball_backward(*, paper_id: str, **_: Any) -> Any:
    from research_vault.adapters.semantic_scholar import SemanticScholarAdapter

    return SemanticScholarAdapter().references(paper_id)


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
    "snowball-forward": _op_snowball_forward,
    "snowball-backward": _op_snowball_backward,
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
