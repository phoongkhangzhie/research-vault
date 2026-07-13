# SPDX-License-Identifier: AGPL-3.0-or-later
"""verbs.py — `rv dag` verb implementations for Research Vault.

Verbs:
  rv dag run <manifest>
      Load manifest, create a new run state, print the initial frontier.

  rv dag tick <run_id>
      Re-compute the frontier for an existing run. Resolves satisfied afterok+watch
      edges INLINE (synchronously via resolve_watch — in-session only, no pollers).
      Unsatisfied external watches: use the documented shell pattern
        wait-for <cond> --then 'rv dag tick <run_id>' &

  rv dag complete <run_id> <node_id> [--status succeeded|failed|blocked]
      Mark a node complete and re-print the frontier.

  rv dag approve <run_id> <node_id> [--reject] [--note TEXT] [--output k=v]…
      Approve (or reject) a human-go node in 'awaiting-go' state.
      Default: moves to 'succeeded' and re-prints the frontier.
      --reject: moves to 'blocked' (terminal); downstream afterok gates halt.
      --note TEXT: store decision rationale in node_states (audit trail).
      --output k=v: store a decision output (repeatable); downstream nodes
        read these from node_states["outputs"] to branch the experiment loop.

  rv dag add <run_id> <manifest_patch>
      Add a node (from a JSON patch file) to an existing run's manifest in-place.

  rv dag insert <run_id> <manifest_patch> --after <after_node_id>
      Insert a node and wire it after the named node (adds a soft need).

  rv dag status <run_id>
      Print a formatted status table for the run.
      IMPORTANT: Prints the exact `dag approve <run_id> <node_id>` command
      for any awaiting-go node — the human sees exactly what to run.

Afterok+watch (in-session resolution):
  When `dag tick` runs, it calls resolve_watch inline for every pending
  afterok+watch edge. If the watch resolves (artifact exists and is fresh),
  the edge is satisfied and the node enters the frontier.

  For unsatisfied external watchers (e.g., cluster job still running), use:
    rv wait-for sacct:<jobid> --then 'rv dag tick <run_id>' &
  which is the background-poller pattern composing dag tick.

NO POLLERS: this module NEVER imports pollers, drain, launchd, or any async
scheduler. The no-liveness-net contract is grep-asserted in the test suite.

Stdlib only (plus intra-package imports).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from ..config import load_config
from ..wait_for import resolve_watch
from .reads import resolve_reads_pointers
from .schema import (
    load_manifest,
    validate_manifest,
    manifest_warns,
    ManifestError,
    global_cap as manifest_global_cap,
    nodes_by_id as manifest_nodes_by_id,
)
from .store import RunState, RunStore, StoreError, VALID_STATUSES
from .walker import compute_frontier, FrontierNode, TERMINAL_STATUSES

# ---------------------------------------------------------------------------
# Status symbols
# ---------------------------------------------------------------------------

_STATUS_SYMBOL: dict[str, str] = {
    "pending": "  ○",
    "dispatched": "  →",
    "running": "  ▶",
    "succeeded": "  ✓",
    "failed": "  ✗",
    "blocked": "  ⊘",
    "awaiting-go": "  ⏸",
}

# ---------------------------------------------------------------------------
# Diagnose-before-retry doctrine string (D-RETRY-8)
# ---------------------------------------------------------------------------
#
# This constant is prepended to every attempt-k>0 re-dispatch (whenever attempts > 0).
# It is FIXED and UNREMOVABLE — a project's retry_diagnosis_tips seam may APPEND to
# it, but cannot replace it. The teeth (root-cause-first; no blind-repeat) are structural.
#
# Compose: the root-cause-first engineer doctrine (doctrine/standards.md) applied to the
# DAG loop. The same "diagnose-before-fix" stance, instantiated for a retried agent node.

RETRY_DIAGNOSIS_DIRECTIVE = (
    "RETRY — attempt {attempt_k} of {total_attempts}. "
    "This node FAILED on the previous attempt. Do NOT blind-repeat.\n"
    "Your FIRST act is to root-cause the prior failure below — read it, reproduce your "
    "understanding of *why* it failed, and only then decide your action. "
    "If the failure is genuinely TRANSIENT (an infra flake, a rate limit, a nondeterministic "
    "timeout) and the identical work should simply be re-run, say so explicitly and proceed — "
    "'transient, re-running' is a valid fast conclusion. "
    "If it is DETERMINISTIC (a bug, a bad assumption, a wrong input), you MUST change your "
    "approach — repeating the identical failing action is forbidden.\n"
    "PRIOR FAILURE: {last_failure}"
)


def _sym(status: str) -> str:
    return _STATUS_SYMBOL.get(status, f"  ?({status})")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_frontier(
    frontier: list[FrontierNode],
    run_id: str,
    node_states: dict[str, dict] | None = None,
) -> None:
    """Print frontier items and actionable commands.

    This is a "you are here" map of the DAG — NOT the dispatch payload. The
    full node spec (the agent's brief) is emitted ONLY by `rv dag brief` (see
    dag/brief.py). Printing the full spec body here (a fix'd regression: node
    specs are often multi-KB prose, not short pointers) floods the terminal
    and buries the actually-useful "where am I / what's next" information.

    DISPATCH lines carry the dispatch mode + a brief hint:
      FRESH  (brief: rv dag brief <run> <node>)
      CONTINUES <node> — <reason>  (brief: rv dag brief <run> <node>)

    When reads: is present on the node, appends a bounded COUNT
    (not the full list — the full resolved reads: paths are in the brief):
      FRESH — reads: 3 pointer(s)  (brief: rv dag brief <run> <node>)
    When reads: is absent the suffix is omitted (non-breaking additive suffix).

    For a dispatch node with attempts > 0, renders the diagnose-first block
    (RETRY_DIAGNOSIS_DIRECTIVE + optional retry_diagnosis_tips from the node). The
    block appears AFTER the mode line so it's immediately visible to the agent runtime.
    No block is rendered for attempts == 0 (first dispatch).
    """
    if not frontier:
        print("  (frontier empty — all nodes terminal or waiting for external conditions)")
        return
    for item in frontier:
        label = item.node.get("label", item.node_id)
        if item.action == "dispatch":
            # Read attempts from node_states to decide if this is a retry dispatch
            ns = (node_states or {}).get(item.node_id, {})
            attempts = ns.get("attempts", 0)
            last_failure = ns.get("last_failure")
            max_retries = item.node.get("max_retries", 0)

            print(f"  → DISPATCH  [{item.node_id}] {label}")
            # Retry indicator in the header line
            if attempts > 0:
                print(f"      attempt {attempts + 1}/{max_retries + 1}")
            # Print mode line (dispatch mode ONLY — never the full spec
            # body; the spec is often multi-KB prose, not a short pointer, so
            # embedding it here floods the terminal — the full-fidelity brief
            # lives at `rv dag brief`, printed as a hint below instead).
            continues = item.node.get("continues")
            if continues and isinstance(continues, dict):
                cont_node = continues.get("node", "")
                cont_reason = continues.get("reason", "")
                mode_line = f"      CONTINUES {cont_node} — {cont_reason}"
            else:
                mode_line = "      FRESH"
            # Append a bounded reads: COUNT if present (not the full
            # list — the resolved paths are in the brief, not the frontier map).
            reads = item.node.get("reads")
            if reads and isinstance(reads, list):
                n_reads = len(reads)
                mode_line += f" — reads: {n_reads} pointer(s)"
            print(mode_line)
            print(f"      brief: rv dag brief {run_id} {item.node_id}")
            # Render diagnose-first block only on retry dispatches (attempts > 0)
            if attempts > 0:
                failure_summary = last_failure or "(no summary captured)"
                directive = RETRY_DIAGNOSIS_DIRECTIVE.format(
                    attempt_k=attempts + 1,
                    total_attempts=max_retries + 1,
                    last_failure=failure_summary,
                )
                # Append optional per-node retry_diagnosis_tips (D-RETRY-8 seam)
                tips = item.node.get("retry_diagnosis_tips")
                if tips:
                    if isinstance(tips, str):
                        directive += f"\nDOMAIN TIPS: {tips}"
                    elif isinstance(tips, list):
                        directive += "\nDOMAIN TIPS:\n" + "\n".join(f"  - {t}" for t in tips)
                print("      ---")
                print("      DIAGNOSE FIRST:")
                for line in directive.splitlines():
                    print(f"      {line}")
                print("      ---")
        elif item.action == "await-go":
            print(f"  ⏸ AWAIT-GO  [{item.node_id}] {label}")
            print(f"      run: rv dag approve {run_id} {item.node_id}")


def _print_manifest_warns(manifest: dict[str, Any]) -> None:
    """Print non-fatal dispatch/reads-scope warnings to stdout (if any).

    Called by dag run / tick / status after loading the manifest.
    """
    warns = manifest_warns(manifest)
    for w in warns:
        print(w)
    if warns:
        print()


def _resolve_reads_or_warn(
    manifest: dict[str, Any],
    project_root: Path,
    verb_prefix: str,
    *,
    node_states: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Resolve reads: pointers; print errors + warns + pending to stdout/stderr.

    Hard errors (a reads: pointer that no node in this manifest declares as
    its produces: output) are printed to stderr — they signal the
    manifest's reading-scope is genuinely broken and the agent would
    re-ground blindly. Soft warns (symbol not found) are printed to
    stdout. PENDING (a reads: pointer that a node in this SAME manifest
    declares as its produces: output, whose producing node has not yet
    succeeded) is printed to stdout as advisory, non-alarming information —
    "this artifact does not exist yet because its producer hasn't run", not
    "the manifest is wrong" (task #97).

    ``node_states`` — pass the run's ``run_state.node_states`` so a
    produces:-matched pointer can be resolved against the producing node's
    ACTUAL status (still classified pending unless that node has already
    succeeded, in which case a still-missing artifact is a genuine error —
    see ``resolve_reads_pointers``). Omit on a fresh run with no state yet
    (every producer is implicitly not-yet-succeeded).

    This is a non-blocking advisory pass — it does NOT abort the run/tick
    (the manifest already passed validate_manifest structurally). Surfacing the
    errors here is the run/tick equivalent of reconcile's artifact checks.
    """
    errors, warns, pending = resolve_reads_pointers(
        manifest, project_root=project_root, node_states=node_states,
    )
    for w in warns:
        print(f"  ⚠ reads-scope: {w}")
    for p in pending:
        print(f"  ⏳ reads-scope: {p}")
    if errors:
        import sys as _sys
        for e in errors:
            print(f"{verb_prefix}: reads-scope ERROR: {e}", file=_sys.stderr)
        print(f"{verb_prefix}: {len(errors)} reads pointer(s) unresolvable — "
              f"fix the manifest's reads: field(s) before dispatching.", file=_sys.stderr)


# single-human-gate design (2026-07-09): the four gates the
# gate-policy engine (review/autonomy.py) may resolve without a human
# keypress. approve-protocol is DELIBERATELY excluded — it is the ONE
# retained human gate: every downstream gate resolves autonomously
# and FINALLY the moment it resolves (no provisional stamp, no async-veto
# window — that machinery was removed). Module-level (was previously
# redeclared locally inside cmd_approve) so both cmd_approve's explicit
# --auto path and the always-on autonomous resolution inside
# _recompute_awaiting_go share exactly ONE definition (never drift).
_AUTONOMOUS_GATE_IDS = frozenset({
    "coverage-gate", "approve-framework", "approve-manuscript", "approve-review",
    "gap-coverage-gate",
})

_TOOL_AUTO_EXEC_MAX_PASSES = 100  # bounded loop guard — never spin forever

# fix (Shape B): a defence-in-depth backstop on the approve-review
# incremental-relate round-stepping loop — distinct from
# ``review.style.get_critic_backtrack_max_rounds`` (which bounds ROUNDS
# specifically); this bounds the outer resolve<->round<->relate-fanout loop
# itself, in case a wiring bug ever made rounds_used fail to increment.
# Should be unreachable under a correctly-configured max_rounds.
_CRITIC_RELATE_LOOP_GUARD = 20


def _missing_produces_artifacts(node: dict[str, Any]) -> list[str]:
    """Return the declared ``produces:`` values (as strings) that are NOT
    present on disk after a tool op ran.

    A tool node with no ``produces:``
    dict is exempt (nothing declared, nothing to enforce — e.g. ``coverage``/
    ``relations`` ops that return an in-memory report, not a file). A
    ``produces:`` value that isn't a path-shaped string (rare, defensive) is
    skipped rather than false-flagged.
    """
    produces = node.get("produces")
    if not isinstance(produces, dict) or not produces:
        return []
    missing: list[str] = []
    for key, value in produces.items():
        if not isinstance(value, str) or not value:
            continue
        if not Path(value).exists():
            missing.append(f"{key}={value}")
    return missing


def _auto_execute_tool_nodes(
    run_state: RunState,
    manifest: dict[str, Any],
    store: RunStore,
) -> bool:
    """D4 (verb consolidation): execute every ready 'tool' node IN-PROCESS,
    no subprocess, no human, no CLI verb — the DAG runner's op-registry
    seam (``review.autonomy.run_tool_op``).

    Repeatedly recomputes the frontier and executes any 'dispatch'-action
    tool node it finds, since completing one tool node may open the next
    (a tool → tool chain). Bounded by ``_TOOL_AUTO_EXEC_MAX_PASSES`` so a
    manifest bug can never spin this forever.

    On success: node -> succeeded, ``tool_result_summary`` stored on the
    node state (truncated) for the audit trail.
    On exception: node -> blocked (never silently retried — a tool-op
    failure is itself a HALT-DECLARE-shaped signal the human/autonomy
    engine must see, not a transient hiccup to paper over).

    Returns True iff any tool node was executed (caller should save+recompute).
    """
    from research_vault.review.autonomy import run_tool_op

    cap = manifest_global_cap(manifest)
    executed_any = False

    for _ in range(_TOOL_AUTO_EXEC_MAX_PASSES):
        frontier = compute_frontier(
            manifest, run_state.node_states, run_state.edge_registered_ts, cap,
        )
        tool_items = [
            item for item in frontier
            if item.action == "dispatch" and item.node.get("type") == "tool"
        ]
        if not tool_items:
            break

        for item in tool_items:
            nid = item.node_id
            op = item.node.get("op", "")
            op_args = item.node.get("args", {}) or {}
            ns = run_state.node_states.setdefault(nid, {})
            try:
                result = run_tool_op(op, **op_args)
                ns["tool_result_summary"] = str(result)[:2000]
                missing = _missing_produces_artifacts(item.node)
                if missing:
                    # A declared
                    # produces: artifact that isn't on disk after the op
                    # ran is a fail-closed BLOCK, never a green node with
                    # no file (charter §2 — surface, never silently drop).
                    msg = (
                        f"tool node {nid!r} (op={op!r}) declared produces: "
                        f"artifact(s) {missing!r} but none were found on "
                        f"disk after the op ran."
                    )
                    ns["tool_error"] = msg[:2000]
                    run_state.set_node_status(nid, "blocked", error=msg[:_FAILURE_SUMMARY_MAX_CHARS])
                else:
                    run_state.set_node_status(nid, "succeeded")
            except Exception as e:  # noqa: BLE001 — surface, never swallow (charter §2)
                ns["tool_error"] = str(e)[:2000]
                run_state.set_node_status(nid, "blocked", error=str(e)[:_FAILURE_SUMMARY_MAX_CHARS])
            executed_any = True

    return executed_any


def _evaluate_autonomous_gate(
    node_id: str,
    nodes_lookup: dict[str, Any],
    manifest_path: Path,
    run_state: RunState,
    manifest: dict[str, Any] | None = None,
) -> Any:
    """The SINGLE-SOURCED dispatch from a DAG node id to a
    ``review.autonomy`` disposition (previously duplicated inline
    inside ``cmd_approve``'s ``--auto`` block AND absent entirely from the
    always-on runner — now used by BOTH so the four autonomous gates
    resolve identically whether triggered by an explicit ``--auto`` flag
    or by the self-advancing runner in ``_recompute_awaiting_go``).

    ``manifest`` (optional, default ``None``): the full manifest dict —
    only needed by ``gap-coverage-gate`` (below), which reads the
    manifest's top-level ``project`` field to resolve
    ``project_notes_dir``. Every other branch derives everything it needs
    from ``nodes_lookup``/``manifest_path``/``run_state`` alone; ``None``
    is a correct default for those.

    ``coverage-gate`` additionally runs through a live
    coverage-deviation check (``classify_coverage_gate_with_deviation_check``)
    — the frozen-corpus stamp lives in ``run_state.meta``, which is why this
    function (unlike the pre-existing inline block) takes ``run_state``.

    ★ NOTE (0.3.1): the coverage-gate branch used to extend further here —
    ``review.remediation.resolve_coverage_gate`` could upgrade a backstop
    GO-WITH-RESIDUE to REMEDIATE and drive it to completion in-process via
    ``review.remediation.run_bounded_remediation``. That machinery was
    DELETED along with the saturation-gated snowball: the citation-neighbor
    relevance walk is depth-bounded by design (``relevance_hops``), which
    makes auto-re-expanding a "budget-terminated" corpus contradictory. This
    function now only ever returns whatever ``classify_coverage_gate_with_
    deviation_check`` resolves (GO / GO-WITH-RESIDUE / HALT-DECLARE), folded
    with the relevance-verify residue below.

    A ``CorpusSchemaError`` (a malformed corpus row) raised
    anywhere in this path is caught here and surfaced as HALT-DECLARE —
    never an uncaught exception that would crash the runner, and never a
    silent stale-subset GO (the exact green-but-stale hole this fixes).
    """
    from ..review import autonomy as _autonomy
    from ..review import check_walk_terminal
    from ..review import CorpusSchemaError as _CorpusSchemaError
    from ..review import check_facet_coverage_from_search_hits as _check_facet_coverage_from_search_hits
    from ..sources.sweep import group_facet_stances, parse_angle_matrix

    if node_id == "coverage-gate":
        snowball_node = nodes_lookup.get("review-snowball")
        walk_ref = None
        if snowball_node is not None:
            produces = snowball_node.get("produces")
            if isinstance(produces, dict):
                walk_ref = produces.get("_walk.md")

        # review_dir: normally derived from the walk producer's path
        # (Path(walk_ref).parent). When that producer is missing/
        # malformed (walk_ref is falsy) we can't derive it that way —
        # but THIS node's own manifest_path is always
        # `<review_dir>/phase1-dag.json` (review._review_artifact_dir /
        # review.cmd_new write it there directly), so manifest_path.parent
        # is a reliable fallback. This lets the missing-producer HALT below
        # still route through `_write_ledger_final_act` (fix-round CHANGE 1
        # — fixes acceptance (f): every HALT writes a ledger_complete:
        # false snapshot, never a silent bypass of the ledger).
        review_dir = Path(walk_ref).parent if walk_ref else manifest_path.parent
        gaps_path = review_dir / "_coverage-gaps.md"
        corpus_path = review_dir / "_corpus.md"
        protocol_path = review_dir / "_protocol.md"
        deviations_path = review_dir / "_deviations.md"
        # project_notes_dir/reviews/<scope> is the review_dir's shape
        # (review._review_artifact_dir) — literature/ lives one level above
        # "reviews/". Best-effort only: the ledger treats a missing dir as
        # an honest gap (surfaced), never a crash.
        literature_dir_for_ledger = review_dir.parent.parent / "literature"
        # the central store — for the K-block's resolving-id lookup +
        # the not-yet-distilled edge-graph check (both CORE-only content).
        literature_root_for_ledger = load_config().literature_root

        def _write_ledger_final_act(
            disposition: Any,
            relevance_payload_for_ledger: dict[str, Any] | None,
        ) -> Any:
            """the coverage-gate node's FINAL act — assemble
            ``_corpus_ledger.md`` from the durable artifacts this branch
            already read, regardless of which disposition is being
            returned. On HALT-DECLARE, still write a snapshot with
            ``ledger_complete: false`` + the HALT reason (auditable halt) —
            never skip the ledger just because the run didn't GO."""
            from ..review import ledger as _ledger

            try:
                _ledger.write_corpus_ledger(
                    review_dir,
                    literature_dir=literature_dir_for_ledger,
                    literature_root=literature_root_for_ledger,
                    relevance_payload=relevance_payload_for_ledger,
                    # 0.3.1: this coverage-gate node has no remediation
                    # round-counter of its own anymore (deleted with the
                    # saturation-gated snowball) — read the critic-backtrack
                    # loop's OWN state key (approve-review's REVISE path,
                    # an unrelated mechanism) rather than the now-dead
                    # "remediation_state" key this used to (mis)read.
                    critic_backtrack_rounds=int(
                        (run_state.meta.get("critic_backtrack_state") or {}).get("rounds_used", 0)
                    ),
                    halt_reason=(
                        disposition.reason
                        if disposition.disposition == _autonomy.HALT_DECLARE
                        else None
                    ),
                )
            except Exception as e:  # noqa: BLE001 — never let ledger assembly
                # crash the coverage-gate's own disposition (surfaced, never
                # silently swallowed — charter §2).
                import sys as _sys
                print(
                    f"rv dag approve: coverage-gate: _corpus_ledger.md assembly "
                    f"failed ({e}) — the gate's disposition is UNAFFECTED, but "
                    f"the ledger is stale/absent; re-run to retry.",
                    file=_sys.stderr,
                )

            # gaps = RQ - coverage (0.3.2): the lit-review's own
            # missing SECOND output. Run regardless of disposition (GO,
            # GO-WITH-RESIDUE, or HALT-DECLARE) — the search+corpus has
            # already happened by the time coverage-gate evaluates, so the
            # facet-coverage record is available even on a HALT. Never lets
            # a gap-emission failure affect the gate's own disposition
            # (same non-fatal posture as the ledger assembly above).
            try:
                from ..review.gap_scan import emit_coverage_gaps as _emit_coverage_gaps

                _emit_coverage_gaps(
                    review_dir, review_dir.parent.parent, scope_id=review_dir.name,
                )
            except Exception as e:  # noqa: BLE001 — surfaced, never swallowed
                import sys as _sys
                print(
                    f"rv dag approve: coverage-gate: coverage-gap emission "
                    f"failed ({e}) — the gate's disposition is UNAFFECTED, but "
                    f"no gaps/ notes were written for this scope this pass; "
                    f"re-run to retry.",
                    file=_sys.stderr,
                )
            return disposition

        if not walk_ref:
            return _write_ledger_final_act(
                _autonomy.DispositionResult(
                    _autonomy.HALT_DECLARE,
                    "coverage-gate --auto: no _walk.md producer found upstream "
                    "(review-snowball node missing/malformed) — cannot self-certify.",
                ),
                None,
            )

        info = check_walk_terminal(Path(walk_ref))

        search_hits_node = nodes_lookup.get("review-search")
        search_hits_path = None
        if search_hits_node is not None:
            search_hits_produces = search_hits_node.get("produces")
            if isinstance(search_hits_produces, dict):
                search_hits_path = search_hits_produces.get("_search_hits.md")

        # Relevance-verify (design 2026-07-10-trustworthy-curation-
        # relevance-gate-design.md) — evaluated BEFORE the corpus_freeze
        # stamp/deviation check below, so any auto-prune this performs is
        # folded into the VERY FIRST frozen baseline (never flagged as an
        # undeclared deviation against its own pruning on a later
        # re-evaluation — idempotent by construction, see
        # ``review.relevance.prune_off_domain_from_corpus``).
        #
        # Optional-collaborator pattern (mirrors approve-manuscript's
        # board-result handling, above): a manifest that never wired
        # ``review-relevance-verify`` (a pre- manifest, or a minimal
        # hand-built test manifest exercising the walk-terminal disposition
        # in isolation) is an honest no-op — proceed exactly as before this
        # feature, never a forced HALT for a node that was never supposed
        # to be there. A manifest that DOES declare the node but never
        # produced its artifact IS a floor-gate failure (see
        # ``review.relevance.classify_relevance_verdict``'s ``not exists``
        # branch) — HALT-DECLARE, same as the analogous coverage-critic
        # producer-lookup check above.
        relevance_verify_node = nodes_lookup.get("review-relevance-verify")
        relevance_result = None
        relevance_payload_for_ledger: dict[str, Any] | None = None
        if relevance_verify_node is not None:
            from ..review import relevance as _relevance

            relevance_produces = relevance_verify_node.get("produces")
            relevance_ref = (
                relevance_produces.get("_relevance-verdict.md")
                if isinstance(relevance_produces, dict) else None
            )
            relevance_payload = (
                _relevance.check_relevance_verifier(Path(relevance_ref))
                if relevance_ref
                else {
                    "exists": False, "canary_aborted": False,
                    "empty_verdict_set": True, "verdicts": {}, "malformed": [],
                }
            )
            relevance_payload_for_ledger = relevance_payload
            relevance_result = _relevance.classify_relevance_verdict(relevance_payload)
            if relevance_result.disposition == _autonomy.HALT_DECLARE:
                return _write_ledger_final_act(relevance_result, relevance_payload_for_ledger)
            if relevance_result.disposition == _autonomy.GO_WITH_RESIDUE:
                off_domain_citekeys = relevance_result.evidence.get("off_domain_citekeys", [])
                _relevance.prune_off_domain_from_corpus(
                    corpus_path, off_domain_citekeys, review_dir / "_relevance-residue.md",
                )

        try:
            from ..review import corpus_freeze as _corpus_freeze
            from ..review import check_source_coverage

            # Stamp the explicit, versioned corpus_freeze baseline
            # (idempotent — a no-op after the first stamp). Reuses/mirrors
            # the SAME "frozen at coverage-gate's first evaluation" timing
            # frozen_corpus_citekeys already uses (no corpus exists
            # earlier in the shipped Phase-1 DAG); kept IN SYNC with
            # frozen_corpus_citekeys, never a second, drifting baseline.
            _corpus_freeze.stamp_corpus_freeze(
                run_state.meta, corpus_path=corpus_path, protocol_path=protocol_path,
            )

            # Source-coverage fail-closed (pre-publish hardening batch,
            # 2026-07-09 downstream e2e-run finding): a source declared in
            # the protocol's `sources:` list that went DARK this sweep must
            # BLOCK certification, checked BEFORE the walk-terminal-based
            # disposition (`classify_coverage_gate` short-circuits on it).
            source_coverage_info = (
                check_source_coverage(Path(search_hits_path), protocol_path)
                if search_hits_path
                else {"exists": False, "dark_sources": [], "declared_dark": []}
            )

            # 0.3.1: classify_coverage_gate_with_deviation_check's own
            # disposition IS the final structural disposition here — no
            # REMEDIATE upgrade/bounded-loop step anymore (deleted with the
            # saturation-gated snowball; see review/remediation.py's module
            # docstring).
            disposition = _autonomy.classify_coverage_gate_with_deviation_check(
                run_state.meta,
                info,
                corpus_path=corpus_path,
                deviations_path=deviations_path,
                coverage_gaps_path=gaps_path,
                source_coverage_info=source_coverage_info,
            )

            # Fold in the relevance-verify residue: most-severe-wins,
            # same pattern approve-framework/approve-manuscript already use
            # to combine a structural disposition with a second, orthogonal
            # gate's disposition. By construction relevance_result here is
            # never HALT-DECLARE (that already returned above) — only GO or
            # GO-WITH-RESIDUE reach this point, so this can only ever ADD a
            # residue annotation, never downgrade a walk-terminal-based GO.
            if relevance_result is not None:
                _severity = {
                    _autonomy.HALT_DECLARE: 3, _autonomy.GO_WITH_RESIDUE: 2,
                    _autonomy.REVISE: 1, _autonomy.GO: 0,
                }
                disposition = max(
                    (disposition, relevance_result),
                    key=lambda r: _severity[r.disposition],
                )

            # Corpus-tagging invariant (remediation corpus-bypass hardening):
            # any row still tagged [NEEDS-CURATE] (a mechanically-screened-in
            # but not-yet-leg-classified remediation/facet-remediation
            # append — review.facet_remediation.screen_and_append_facet_hits'
            # own tag) means a re-curate pass has not happened for it yet.
            # coverage-gate must never certify (GO/GO-WITH-RESIDUE) while
            # such a row is sitting in the corpus — folded in the SAME
            # most-severe-wins pattern, always as a hard HALT (never a
            # residue-able downgrade: an un-recurated row isn't a known,
            # bounded gap, it's an unfinished pipeline step).
            from ..review import check_corpus_all_accept_tagged as _check_corpus_tagged

            tagging_info = _check_corpus_tagged(corpus_path)
            if tagging_info["exists"] and not tagging_info["all_tagged"]:
                disposition = _autonomy.DispositionResult(
                    _autonomy.HALT_DECLARE,
                    "coverage-gate: corpus-tagging invariant violated — "
                    f"{len(tagging_info['untagged_citekeys'])} row(s) still "
                    "tagged [NEEDS-CURATE] (a remediation/facet-remediation "
                    "append pending a re-curate pass): "
                    f"{tagging_info['untagged_citekeys']!r}. A run cannot "
                    "self-certify while these rows sit unclassified — "
                    "re-curate them (leg-classify or drop) before "
                    "re-evaluating.",
                    {"untagged_citekeys": tagging_info["untagged_citekeys"]},
                )

            # 0.3.1 Layer 2/3: the explicit 3-tier fold (design item 2) —
            # (1) any HALT already returned above / dominates unconditionally
            # (this branch is only reached with disposition != HALT_DECLARE);
            # (2) a thin pole + remediation budget remaining routes through
            # `FACET_REMEDIATE`'s round-stepping loop below; (3) otherwise
            # the existing {GO, GO_WITH_RESIDUE} fold above is final —
            # deliberately NOT leaned on the generic severity `max` (a thin
            # pole must never silently lose to GO_WITH_RESIDUE and skip
            # remediation).
            facet_coverage_info = (
                _check_facet_coverage_from_search_hits(Path(search_hits_path))
                if search_hits_path
                else None
            )
            if disposition.disposition != _autonomy.HALT_DECLARE:
                from ..review import facet_remediation as _fremed

                # missing-SET fail-closed cross-check (hardening): derive
                # whether the FROZEN protocol itself declared nested
                # facets, independent of whatever `_search_hits.md`
                # stamped — a declared-faceted protocol whose sweep never
                # stamped Layer-2 coverage is a stamping failure, not a
                # legacy protocol; see `resolve_facet_coverage`'s docstring.
                protocol_declares_facets = False
                if protocol_path.exists():
                    protocol_declares_facets = bool(
                        group_facet_stances(
                            parse_angle_matrix(protocol_path.read_text(encoding="utf-8"))
                        )
                    )

                disposition = _fremed.resolve_facet_coverage(
                    disposition, facet_coverage_info,
                    remediation_state=run_state.meta.get("facet_remediation_state"),
                    protocol_declares_facets=protocol_declares_facets,
                )

            if disposition.disposition == _autonomy.FACET_REMEDIATE:
                target_pole = disposition.evidence["target_pole"]
                task_dir = _fremed.facet_task_dir(review_dir, target_pole)
                fr_state = run_state.meta.setdefault(
                    "facet_remediation_state", {"rounds_used": 0},
                )

                response = _fremed.read_facet_query_response(task_dir)
                if response is not None:
                    # Phase 2: an agent already authored the new queries —
                    # apply the round (screen+tag+append, never raw; see
                    # review.facet_remediation's module docstring), then
                    # re-derive the disposition to see if ANOTHER round is
                    # needed. relevance_verify_node's produces gives the
                    # cold-verify artifact path to invalidate on any add.
                    relevance_ref = (
                        relevance_verify_node.get("produces", {}).get("_relevance-verdict.md")
                        if relevance_verify_node is not None else None
                    )
                    from ..review.style import get_min_hits_per_pole as _get_min_hits

                    round_result = _fremed.run_facet_query_append_round(
                        run_state.meta, pole=target_pole, new_queries=response,
                        protocol_path=protocol_path, corpus_path=corpus_path,
                        deviations_path=deviations_path, out_dir=review_dir,
                        search_hits_path=Path(search_hits_path) if search_hits_path else None,
                        relevance_verdict_path=Path(relevance_ref) if relevance_ref else None,
                        min_hits_per_pole=(facet_coverage_info or {}).get("min_hits_per_pole")
                        or _get_min_hits(load_config()),
                    )
                    _fremed.clear_facet_task(task_dir)
                    fr_state["rounds_used"] = int(fr_state.get("rounds_used", 0)) + 1

                    return _write_ledger_final_act(
                        _autonomy.DispositionResult(
                            _autonomy.HALT_DECLARE,
                            f"coverage-gate: facet-remediation round for pole "
                            f"{target_pole!r} complete — {len(round_result['added'])} "
                            "screened-in paper(s) added, tagged [NEEDS-CURATE] "
                            "(never a bare/leg-tagged row) and the cold "
                            "relevance-verify artifact was invalidated on any "
                            "add. AWAITING a re-curate pass (leg-classification) "
                            "and a FRESH relevance-verify run before this gate "
                            "can be re-evaluated.",
                            {"round_result": round_result, "rounds_used": fr_state["rounds_used"]},
                        ),
                        relevance_payload_for_ledger,
                    )

                if not _fremed.facet_task_pending(task_dir):
                    # Phase 1: no task emitted yet for this round — emit the
                    # query-authoring task and pause for the hub to dispatch
                    # a fresh agent.
                    existing_matrix = parse_angle_matrix(
                        Path(protocol_path).read_text(encoding="utf-8")
                    ) if protocol_path.exists() else {}
                    facets = group_facet_stances(existing_matrix)
                    _angle, _stance = target_pole.rsplit(".", 1)
                    existing_queries = facets.get(_angle, {}).get(_stance, [])
                    current_count = (facet_coverage_info or {}).get("pole_counts", {}).get(target_pole, 0)
                    from ..review.style import get_min_queries_per_pole as _get_min_per_pole_r

                    _fremed.emit_facet_query_task(
                        task_dir, pole=target_pole, existing_queries=existing_queries,
                        min_queries_needed=_get_min_per_pole_r(load_config()),
                        min_hits_per_pole=(facet_coverage_info or {}).get("min_hits_per_pole", 3),
                        current_count=current_count,
                    )

                return _write_ledger_final_act(
                    _autonomy.DispositionResult(
                        _autonomy.HALT_DECLARE,
                        f"coverage-gate: facet-remediation task emitted/pending "
                        f"for pole {target_pole!r} under {task_dir} — AWAITING "
                        "a fresh agent to author new queries (write "
                        "_facet-query-response.md), then re-evaluate.",
                        {"target_pole": target_pole, "task_dir": str(task_dir)},
                    ),
                    relevance_payload_for_ledger,
                )

            return _write_ledger_final_act(disposition, relevance_payload_for_ledger)
        except _CorpusSchemaError as e:
            return _write_ledger_final_act(_autonomy.DispositionResult(
                _autonomy.HALT_DECLARE,
                f"coverage-gate --auto: {e} — a malformed corpus row was "
                "rejected loudly (never silently dropped); fix "
                "the row schema and re-evaluate.",
                {"corpus_schema_error": str(e)},
            ), relevance_payload_for_ledger)

    if node_id == "approve-framework":
        tree_root = manifest_path.parent
        manuscript_note_path = tree_root / "_manuscript.md"
        from ..manuscript.types.lit_review import check_framework_gate as _cfg_check
        _ok, _msg = _cfg_check(manuscript_note_path)
        structural_result = _autonomy.classify_disposition(
            _autonomy.evaluation_from_framework_gate(_ok, _msg)
        )

        _severity = {
            _autonomy.HALT_DECLARE: 3, _autonomy.GO_WITH_RESIDUE: 2,
            _autonomy.REVISE: 1, _autonomy.GO: 0,
        }

        # the full-corpus coverage-allocation contract, folded
        # most-severe-wins (no second disposition grammar — the SAME
        # structural-payload adapter the framework-critic uses). This applies
        # to ANY spine (machine OR human): the corpus must be fully allocated
        # to the committed framework regardless of how the spine was authored.
        # `_coverage-map.md` lives beside `_manuscript.md`; the frozen corpus
        # lives at `reviews/<slug>/_corpus.md` (slug == tree_root.name), under
        # project_notes_dir = tree_root.parent.parent (manuscripts/<slug>/..).
        from ..manuscript.check_gates import check_coverage_allocation_gate as _ccag
        _slug = tree_root.name
        _project_notes_dir = tree_root.parent.parent
        _corpus_path = _project_notes_dir / "reviews" / _slug / "_corpus.md"
        _coverage_map_path = tree_root / "_coverage-map.md"
        _cov_res = _ccag(_corpus_path, _coverage_map_path)
        coverage_alloc_result = _autonomy.classify_disposition(
            _autonomy.evaluation_from_structural_payload(
                {"blocking": _cov_res["errors"]}
            )
        )

        # framework-gate-autonomy design (option A, 2026-07-09): fold in the
        # framework-critic disposition, most-severe-wins — exactly the
        # pattern approve-manuscript already folds structural+board. Only
        # applies to a MACHINE-synthesized spine (`framework_origin:
        # machine`, stamped by `framework-synthesize`); a human-authored
        # spine (hand-edited `_manuscript.md`, the pre-ensemble path —
        # `check_framework_gate` alone still governs it, unchanged) never
        # required a critic and still doesn't. The coverage-allocation gate,
        # by contrast, folds in for BOTH origins (above).
        _framework_origin = ""
        if manuscript_note_path.exists():
            from ..note import _parse_frontmatter as _pfm_fw
            _fw_text = manuscript_note_path.read_text(encoding="utf-8")
            _fw_fields, _ = _pfm_fw(_fw_text)
            _framework_origin = str(_fw_fields.get("framework_origin", "")).strip()

        if _framework_origin != "machine":
            return max(
                (structural_result, coverage_alloc_result),
                key=lambda r: _severity[r.disposition],
            )

        critic_node = nodes_lookup.get("framework-critic")
        critic_ref = None
        expected_canary_id = None
        if critic_node is not None:
            produces = critic_node.get("produces")
            if isinstance(produces, dict):
                critic_ref = produces.get("_framework-critique.md")
            expected_canary_id = critic_node.get("canary_id")

        if not critic_ref:
            # A machine-synthesized spine with no framework-critic producer
            # upstream is the priority-2 "floor gate NOT RUN" failure
            # class — fail-closed HALT, never a silent GO on an un-critiqued
            # auto-synthesized spine.
            return _autonomy.DispositionResult(
                _autonomy.HALT_DECLARE,
                "approve-framework --auto: _manuscript.md is framework_origin: "
                "machine but no framework-critic producer was found upstream "
                "(framework-critic node missing/malformed produces) — cannot "
                "self-certify a synthesized spine with no critic run.",
                {"not_run": ["framework-critic"]},
            )

        from ..manuscript.types.lit_review import (
            check_framework_critique_verdict as _cfcv,
        )

        critic_payload = _cfcv(Path(critic_ref), expected_canary_id=expected_canary_id)
        critic_result = _autonomy.classify_disposition(
            _autonomy.evaluation_from_framework_critic(critic_payload)
        )

        return max(
            (structural_result, critic_result, coverage_alloc_result),
            key=lambda r: _severity[r.disposition],
        )

    if node_id == "approve-manuscript":
        tree_root = manifest_path.parent
        manuscript_note_path = tree_root / "_manuscript.md"
        if not manuscript_note_path.exists():
            return _autonomy.DispositionResult(
                _autonomy.HALT_DECLARE,
                f"approve-manuscript --auto: {manuscript_note_path} not found.",
            )
        from ..note import _parse_frontmatter as _pfm_auto
        from ..manuscript.types import get_type as _get_ms_type_auto
        from ..manuscript.check_gates import build_approve_payload as _bap

        _text = manuscript_note_path.read_text(encoding="utf-8")
        _fields, _ = _pfm_auto(_text)
        _ms_type = _get_ms_type_auto(_fields.get("manuscript_type", ""))
        if _ms_type is None:
            return _autonomy.DispositionResult(
                _autonomy.HALT_DECLARE,
                f"approve-manuscript --auto: manuscript_type "
                f"{_fields.get('manuscript_type', '')!r} is unrecognized — "
                "cannot self-certify with no registered fidelity gates.",
            )
        _project_notes_dir = tree_root.parent.parent
        _payload = _bap(tree_root, _project_notes_dir, _ms_type)
        structural_result = _autonomy.classify_disposition(
            _autonomy.evaluation_from_structural_payload(_payload)
        )

        # ★ fold in the holistic-quality review board (design
        # 2026-07-08-autonomous-board-design.md) — a SEPARATE failure
        # class from the mechanical integrity floors above. A missing
        # board-result artifact means the board was never driven for this
        # manuscript (an out-of-band, hub-orchestrated multi-round fanout
        # no DAG node can synchronously block on — see
        # ``manuscript.board.write_board_result``'s docstring) and is an
        # honest no-op: the structural-only disposition is returned
        # UNCHANGED (never a fabricated board verdict). When a board
        # result IS present, the MOST SEVERE of the two dispositions wins
        # (HALT > GO-WITH-RESIDUE > REVISE > GO) — an integrity HALT from
        # the structural side always dominates a board GO-WITH-RESIDUE,
        # and a board canary-abort HALT always dominates a clean
        # structural GO.
        from ..manuscript import board as _board

        _board_result = _board.read_board_result(tree_root / "judge" / "board")
        if _board_result is None:
            return structural_result

        _board_canary_aborted = bool(_board_result.get("halt")) and bool(
            _board_result.get("canary_aborted")
        )
        board_eval_result = _autonomy.classify_disposition(
            _autonomy.evaluation_from_board(_board_result, canary_aborted=_board_canary_aborted)
        )
        if _board_result.get("halt") and not _board_canary_aborted:
            # An incomplete board fanout (missing/empty verdicts while
            # tasks were emitted) is the same "floor gate NOT RUN"
            # failure class as the support-matcher's — HALT, never a
            # fabricated GO-WITH-RESIDUE from a board that never actually
            # finished scoring.
            board_eval_result = _autonomy.DispositionResult(
                _autonomy.HALT_DECLARE,
                "approve-manuscript --auto: the review board's fan-out did "
                "not complete (incomplete verdicts while tasks were "
                "emitted) — cannot self-certify the holistic-quality floor.",
                {"not_run": ["review-board"]},
            )

        _severity = {
            _autonomy.HALT_DECLARE: 3, _autonomy.GO_WITH_RESIDUE: 2,
            _autonomy.REVISE: 1, _autonomy.GO: 0,
        }
        return max(
            (structural_result, board_eval_result),
            key=lambda r: _severity[r.disposition],
        )

    if node_id == "approve-review":
        # Single-human-gate design (2026-07-09): Gate 3 (approve-review)
        # resolves autonomously from review-coverage-critic's STRUCTURED
        # ``verdict:`` frontmatter field (PASS/BLOCK, fixed vocab — prose is
        # never scanned) — SAME structural-payload adapter approve-framework
        # already uses (no new disposition path).
        #
        # (D-5a): extended further, mirroring the coverage-gate branch
        # above — ``review.remediation.resolve_coverage_critic`` may upgrade
        # a REVISE (from a PURE counter-position/thin-pole BLOCK) to
        # CRITIC_BACKTRACK. fix (Shape B): the round-stepping loop is
        # driven HERE (not via ``review.remediation.run_bounded_critic_
        # backtrack``, which assumed a synchronous in-process relate_fn) —
        # each round's newly-found counter-papers' relate judgment goes
        # through the harness cold emit/ingest fan-out (``review.
        # relate_judge_seam``), which is asynchronous across DAG
        # invocations; see the CRITIC_BACKTRACK branch below for the full
        # emit/ingest orchestration. A mixed BLOCK (any PROTOCOL-DRIFT/
        # DIRECTION-STARVED/TAG-UNDER-COUNTING reason present) is untouched —
        # `resolve_coverage_critic` passes it straight through, REVISE/HALT
        # exactly as before this PR.
        critic_node = nodes_lookup.get("review-coverage-critic")
        critic_ref = None
        if critic_node is not None:
            produces = critic_node.get("produces")
            if isinstance(produces, dict):
                critic_ref = produces.get("_coverage-critic.md")
        if not critic_ref:
            return _autonomy.DispositionResult(
                _autonomy.HALT_DECLARE,
                "approve-review --auto: no _coverage-critic.md producer found "
                "upstream (review-coverage-critic node missing/malformed "
                "produces) — cannot self-certify.",
            )
        from ..review import check_coverage_critic_verdict as _cccv

        critic_note_path = Path(critic_ref)
        payload = _cccv(critic_note_path)
        base = _autonomy.classify_disposition(
            _autonomy.evaluation_from_structural_payload(payload)
        )

        from ..review import remediation as _remediation

        review_dir = critic_note_path.parent
        disposition = _remediation.resolve_coverage_critic(
            base, payload,
            remediation_state=run_state.meta.get("critic_backtrack_state"),
        )
        if disposition.disposition == _autonomy.CRITIC_BACKTRACK:
            # fix (Shape B): the newly-found counter-papers' relate
            # judgment (does paper A relate to paper B) is a JUDGE, so it
            # routes the CC harness cold emit/ingest fan-out — never a
            # synchronous in-process LLM call (original
            # ``_default_relate_fn`` called ``gates._llm`` directly,
            # doctrine-violating; deleted that path wholesale).
            #
            # The fan-out is async + two-phase, so it CANNOT live inside a
            # single synchronous in-process loop assuming a synchronous
            # ``relate_fn`` that drives the whole resolve->round->re-resolve
            # cycle to completion in one call (removed exactly that
            # shell — ``review.remediation.run_bounded_critic_backtrack`` —
            # once this DAG-level round-stepping replaced it).
            # This DAG layer instead drives round-stepping itself, pausing
            # between rounds to emit a batched relate-task set and, on a
            # LATER invocation (once the hub's cold judges have written
            # ``_relate-verdicts.json``), ingesting the verdicts and
            # injecting a resolved SYNCHRONOUS dict-lookup ``relate_fn``/
            # ``escalate_relate_fn`` down into ``run_incremental_relate``
            # (via ``review.relate_judge_seam`` — see its module docstring
            # for the full "why Shape B, not Shape A" reasoning).
            from ..review import _parse_corpus_citekeys
            from ..review import relate_judge_seam as _rjs
            from ..review.incremental_relate import run_incremental_relate

            protocol_path = review_dir / "_protocol.md"
            corpus_path = review_dir / "_corpus.md"
            deviations_path = review_dir / "_deviations.md"
            # the overlay unwind (0.3.2): literature is shared-
            # canonical — concept-graph candidate generation AND every
            # written paper->paper edge both target the SAME shared store
            # (cfg.literature_root). There is no per-project overlay dir
            # left to read '## Concept edges' from.
            literature_dir = load_config().literature_root
            core_dir = literature_dir
            judge_dir = review_dir / "judge" / "relate"

            # The pole is resolved ONCE from the triggering critic_payload
            # and reused for every round this backtrack drives — same
            # single-pole-per-backtrack behavior as the removed synchronous
            # shell it replaced.
            target = payload.get("remediation_target") or {}
            pole = target.get("pole")

            _noop_relate_fn = lambda a, b: None  # noqa: E731 — phase-1: append only, never judge yet
            _noop_escalate_fn = lambda a, baseline: []  # noqa: E731

            for _ in range(_CRITIC_RELATE_LOOP_GUARD):
                if disposition.disposition != _autonomy.CRITIC_BACKTRACK:
                    break
                if not pole:
                    disposition = _autonomy.DispositionResult(
                        _autonomy.HALT_DECLARE,
                        "approve-review: CRITIC_BACKTRACK disposition but no "
                        "'pole' in remediation_target — fail-closed.",
                        {},
                    )
                    break

                if _rjs.relate_fanout_present(judge_dir):
                    # Phase 2: a relate fan-out from a PRIOR call is ready to
                    # ingest — write this round's edges, then re-derive the
                    # disposition to see if ANOTHER round is needed.
                    try:
                        ingest_result = _rjs.ingest_relate_verdicts_from_dir(judge_dir)
                    except _rjs.CanaryAbortError as e:
                        disposition = _autonomy.DispositionResult(
                            _autonomy.HALT_DECLARE,
                            f"approve-review: incremental-relate judge CANARY "
                            f"ABORTED — the fan-out judge is untrustworthy: {e}",
                            {},
                        )
                        break
                    if ingest_result["halt"]:
                        disposition = _autonomy.DispositionResult(
                            _autonomy.HALT_DECLARE,
                            "; ".join(ingest_result["not_run"]) or ingest_result["halt_reason"],
                            {},
                        )
                        break

                    tasks_doc = _rjs.read_relate_tasks_doc(judge_dir) or {}
                    round_new_citekeys = tasks_doc.get("new_citekeys", [])
                    round_baseline_citekeys = set(tasks_doc.get("baseline_citekeys", []))
                    edges = ingest_result["edges"]
                    escalated_edges = ingest_result["escalated_edges"]

                    def _resolved_relate_fn(a: str, b: str, _edges: dict = edges) -> dict[str, str] | None:
                        return _edges.get((a, b))

                    def _resolved_escalate_fn(
                        a: str, baseline: set[str], _esc: dict = escalated_edges,
                    ) -> list[dict[str, str]]:
                        return [
                            {"candidate": b, **v}
                            for (aa, b), v in _esc.items() if aa == a
                        ]

                    if round_new_citekeys:
                        run_incremental_relate(
                            round_new_citekeys,
                            literature_dir=literature_dir,
                            baseline_citekeys=round_baseline_citekeys,
                            relate_fn=_resolved_relate_fn,
                            escalate_relate_fn=_resolved_escalate_fn,
                            core_dir=core_dir,
                        )
                    _rjs.clear_relate_fanout(judge_dir)

                    payload = _cccv(critic_note_path)
                    base2 = _autonomy.classify_disposition(
                        _autonomy.evaluation_from_structural_payload(payload)
                    )
                    if base2.disposition == _autonomy.HALT_DECLARE:
                        disposition = base2
                        break
                    disposition = _remediation.resolve_coverage_critic(
                        base2, payload,
                        remediation_state=run_state.meta.get("critic_backtrack_state"),
                    )
                    continue

                # Phase 1: run ONE bounded, pole-directed round (search +
                # append + deviation + freeze) with a no-op relate — nothing
                # is judged yet. If the round found new counter-papers, emit
                # their concept-graph-blocked candidate pairs as a batched
                # relate-task set and PAUSE (HALT-DECLARE, awaiting the
                # hub's cold judge fan-out); if it found nothing new, no
                # relate is needed — re-derive the disposition and continue.
                baseline_before = set(_parse_corpus_citekeys(corpus_path))
                round_result = _remediation.run_directed_remediation_round(
                    run_state.meta,
                    pole=pole,
                    protocol_path=protocol_path,
                    corpus_path=corpus_path,
                    deviations_path=deviations_path,
                    out_dir=review_dir,
                    literature_dir=literature_dir,
                    relate_fn=_noop_relate_fn,
                    escalate_relate_fn=_noop_escalate_fn,
                    core_dir=core_dir,
                )
                added = round_result.get("added", [])
                if added:
                    pairs, islands = _rjs.build_relate_candidate_pairs(
                        added, literature_dir=literature_dir, baseline_citekeys=baseline_before,
                    )
                    _rjs.emit_relate_tasks_to_dir(
                        judge_dir, pairs, islands,
                        literature_dir=literature_dir,
                        baseline_citekeys=baseline_before,
                        new_citekeys=added,
                        scope=review_dir.name,
                    )
                    disposition = _autonomy.DispositionResult(
                        _autonomy.HALT_DECLARE,
                        f"approve-review: pole-directed backtrack round found "
                        f"{len(added)} new counter-paper(s) — incremental-"
                        f"relate judge fan-out emitted under {judge_dir} "
                        "(awaiting the hub's cold judge fan-out). Re-run "
                        "approve-review once _relate-verdicts.json is "
                        "written to write the relate edges and continue.",
                        {"pending_relate_pairs": len(pairs), "pending_islands": len(islands)},
                    )
                    break

                payload = _cccv(critic_note_path)
                base2 = _autonomy.classify_disposition(
                    _autonomy.evaluation_from_structural_payload(payload)
                )
                if base2.disposition == _autonomy.HALT_DECLARE:
                    disposition = base2
                    break
                disposition = _remediation.resolve_coverage_critic(
                    base2, payload,
                    remediation_state=run_state.meta.get("critic_backtrack_state"),
                )
            else:
                disposition = _autonomy.DispositionResult(
                    _autonomy.HALT_DECLARE,
                    "approve-review: incremental-relate backtrack outer-loop "
                    "guard exhausted — this should be unreachable under a "
                    "correctly-configured critic_backtrack_max_rounds; "
                    "fail-closed rather than loop unboundedly.",
                    {},
                )

        # Corpus-tagging invariant (remediation corpus-bypass hardening,
        # SAME check as the coverage-gate branch above): a critic-backtrack
        # round appends to THIS review's ``_corpus.md`` too — a row it
        # screened-in but tagged [NEEDS-CURATE] must never let approve-review
        # certify a GO/GO-WITH-RESIDUE into Phase-2's manuscript emission
        # while it sits unclassified. Checked unconditionally (whether or
        # not a CRITIC_BACKTRACK round ran this call) — a mixed BLOCK/REVISE
        # path never touches the corpus, so this is a correct no-op there.
        if disposition.disposition != _autonomy.HALT_DECLARE:
            from ..review import check_corpus_all_accept_tagged as _check_corpus_tagged

            tagging_info = _check_corpus_tagged(review_dir / "_corpus.md")
            if tagging_info["exists"] and not tagging_info["all_tagged"]:
                disposition = _autonomy.DispositionResult(
                    _autonomy.HALT_DECLARE,
                    "approve-review: corpus-tagging invariant violated — "
                    f"{len(tagging_info['untagged_citekeys'])} row(s) still "
                    "tagged [NEEDS-CURATE] (a critic-backtrack/facet-"
                    "remediation append pending a re-curate pass): "
                    f"{tagging_info['untagged_citekeys']!r}. Phase-2 cannot "
                    "be authorized while these rows sit unclassified — "
                    "re-curate them (leg-classify or drop) before "
                    "re-evaluating.",
                    {"untagged_citekeys": tagging_info["untagged_citekeys"]},
                )

        # Producer-strict link resolution: a curation gate (approve-review
        # certifies a corpus into Phase-2's manuscript emission) never
        # authorizes an unresolved cross-bundle backbone or intra-bundle
        # edge link — the same finding a plain ``rv note check`` only
        # WARNs on (day-to-day authoring tolerance) is a hard refusal here
        # (``review.check_link_resolution``'s "one resolution pass, two
        # postures"). Checked unconditionally, same gating as the
        # corpus-tagging invariant above.
        if disposition.disposition != _autonomy.HALT_DECLARE:
            from ..review import check_link_resolution as _check_link_resolution

            link_result = _check_link_resolution(
                project_notes_dir=review_dir.parent.parent
            )
            if not link_result["ok"]:
                disposition = _autonomy.DispositionResult(
                    _autonomy.HALT_DECLARE,
                    "approve-review: unresolved link(s) — the cross-bundle "
                    "backbone or an intra-bundle edge in this corpus does "
                    "not resolve against the resolvable bundle set: "
                    f"{link_result['errors']!r}. Phase-2 cannot be "
                    "authorized while a producer-strict link is unresolved "
                    "— fix the link or remove the edge before re-evaluating.",
                    {"unresolved_links": link_result["errors"]},
                )

        return disposition

    if node_id == "gap-coverage-gate":
        # The spine's SECOND mechanical gate (0.3.2): the
        # existing coverage-gate certifies the CORPUS; this one certifies
        # that the research GAPS a plan committed to were actually closed
        # (a finding's ANSWERS edge) or explicitly declared open
        # (disposition: leaves-open + a reason) — never silently
        # abandoned. Requires the manifest's own 'project' field to
        # resolve project_notes_dir (research-loop.json-shaped experiment
        # manifests declare it; see the top-level 'project' key).
        project = manifest.get("project") if isinstance(manifest, dict) else None
        if not project:
            return _autonomy.DispositionResult(
                _autonomy.HALT_DECLARE,
                "gap-coverage-gate: cannot resolve a 'project' slug from "
                "this manifest (no top-level 'project' field) — cannot "
                "locate project_notes_dir to check gap coverage. Add "
                "\"project\": \"<slug>\" to the manifest.",
                {},
            )

        from ..review.gap_coverage_gate import check_gap_coverage_gate as _check_gap_cov

        cfg = load_config()
        project_notes_dir = cfg.project_notes_dir(project)
        result = _check_gap_cov(project_notes_dir)

        if result["ok"]:
            return _autonomy.DispositionResult(
                _autonomy.GO,
                f"gap-coverage-gate: every open gap is closed (ANSWERS) or "
                f"explicitly LEAVES-OPEN — {len(result['closed'])} closed, "
                f"{len(result['leaves_open'])} leaves-open.",
                result,
            )
        return _autonomy.DispositionResult(
            _autonomy.HALT_DECLARE,
            f"gap-coverage-gate: {len(result['open_uncovered'])} open "
            f"gap(s) neither ANSWERED by a finding nor explicitly "
            f"LEAVES-OPEN: {result['open_uncovered']!r}. Close each with a "
            "finding's ANSWERS edge, or stamp disposition: leaves-open + "
            "disposition_reason on the gap note.",
            result,
        )

    raise ValueError(f"_evaluate_autonomous_gate: {node_id!r} is not an autonomous gate id")


def _derive_project_and_id(manifest: dict[str, Any], *, prefix: str, suffix: str) -> tuple[str, str] | None:
    """Derive ``(project, scope_or_slug)`` from a Phase-1 manifest's
    ``run_id``/``project`` fields (``review-<scope>-phase1`` /
    ``manuscript-<slug>-phase1``, the naming convention — see
    ``review._build_phase1_manifest`` / ``manuscript.types.lit_review.phase1_builder``).

    Returns ``None`` if the manifest doesn't carry the expected shape
    (never guesses — a phase-transition emission that can't derive its own
    inputs must fail loudly, not silently skip, charter §2).
    """
    project = manifest.get("project")
    run_id = str(manifest.get("run_id", ""))
    if not project or not run_id.startswith(prefix) or not run_id.endswith(suffix):
        return None
    ident = run_id[len(prefix):-len(suffix)] if suffix else run_id[len(prefix):]
    if not ident:
        return None
    return str(project), ident


def _start_dag_run_inprocess(
    manifest: dict[str, Any],
    manifest_path: Path,
    store: RunStore,
) -> RunState:
    """The IN-PROCESS core of ``cmd_run`` (no argparse, no printing) — the
    seam ``_emit_next_phase`` uses to auto-start a phase's DAG run instead
    of stranding at "now hand-run `rv dag run <phase2-manifest>`".

    Recurses into ``_recompute_awaiting_go`` on the new run so the
    self-advancing walk continues seamlessly across the phase boundary
    (a chain of tool nodes, or an immediately-resolvable autonomous gate,
    in the new phase advances in the SAME call — no separate tick needed).
    """
    run_id = manifest["run_id"]
    child = RunState(run_id=run_id, manifest_path=str(manifest_path), created_at=time.time())
    child.init_nodes(manifest)
    store.create(child)
    _recompute_awaiting_go(child, manifest, store)
    return child


def _emit_next_phase(
    node_id: str,
    manifest: dict[str, Any],
    manifest_path: Path,
    run_state: RunState,
    store: RunStore,
) -> None:
    """The self-advancing
    runner's phase-transition auto-emission. On a GO/GO-WITH-RESIDUE
    disposition at ``coverage-gate`` (review Phase-1 -> Phase-2),
    ``approve-framework`` (manuscript Phase-1 -> Phase-2), or
    ``approve-review`` (review Phase-2 -> a NEW manuscript tree,
    cross-loop), auto-emit the next manifest AND auto-start its DAG run
    in-process — retiring the "stranded ops" state where a GO'd
    autonomous gate left the loop needing a human to hand-run ``rv review
    expand`` / ``rv manuscript new``+``expand`` + ``rv dag run`` (the CLI
    verbs were hard-removed by verb-consolidation on the assumption this
    wiring would exist — see ``cli_removed_verbs``).

    ``approve-review``'s handoff contract is **slug == review scope id, no
    transform** — the manuscript folder this emits into
    (``manuscripts/<scope_id>/``) is exactly the slug ``manuscript.cmd_new``'s
    ``--from-review`` convention already expects, which is what
    pre-binds the frozen corpus (``reviews/<scope_id>/_corpus.md``) to the
    new manuscript automatically.

    ``approve-review``'s manuscript emission is further gated on the frozen
    ``deliverable`` field in ``_protocol.md`` (2026-07-09, review-only
    default / manuscript opt-in): ``deliverable: manuscript`` emits as
    above; ``deliverable: review`` (or absent -> default ``review``) makes
    this GO **terminal** — the review stands alone as the knowledge
    artifact, no manuscript tree, no ``child_runs`` entry recorded for this
    node. See ``review.read_protocol_deliverable``.

    A no-op for every other node id (nothing to expand after
    ``approve-manuscript`` — it is the terminal gate of its DAG).

    Idempotency: if this node already has a recorded ``child_runs`` entry
    (a prior tick already emitted + started the child), this is a pure
    no-op — never re-scaffold (a second ``cmd_new``/``cmd_expand`` call
    would raise ``FileExistsError`` against the first child's artifacts).

    Any failure to derive/emit is stamped onto the node's state as
    ``phase_transition_error`` and surfaced (never silently dropped,
    charter §2) — the gate itself already resolved GO; a failed
    auto-emission is a distinct, loud signal that manual follow-up
    (``rv review expand`` / ``rv manuscript new``+``expand`` by hand) is
    needed.
    """
    if node_id not in ("coverage-gate", "approve-framework", "approve-review"):
        return

    if run_state.meta.get("child_runs", {}).get(node_id):
        return  # already emitted for this node — never re-scaffold

    ns = run_state.node_states.setdefault(node_id, {})
    try:
        from ..config import load_config
        cfg = load_config()

        if node_id == "coverage-gate":
            derived = _derive_project_and_id(manifest, prefix="review-", suffix="-phase1")
            if derived is None:
                raise ValueError(
                    f"cannot derive (project, scope) from manifest run_id="
                    f"{manifest.get('run_id')!r} project={manifest.get('project')!r}"
                )
            project, scope_id = derived
            from ..review import cmd_expand as _review_cmd_expand
            child_manifest = _review_cmd_expand(project, scope_id, config=cfg)
            child_manifest_path = manifest_path.parent / "phase2-dag.json"

        elif node_id == "approve-framework":
            derived = _derive_project_and_id(manifest, prefix="manuscript-", suffix="-phase1")
            if derived is None:
                raise ValueError(
                    f"cannot derive (project, slug) from manifest run_id="
                    f"{manifest.get('run_id')!r} project={manifest.get('project')!r}"
                )
            project, slug = derived
            from ..manuscript import cmd_expand as _ms_cmd_expand
            child_manifest = _ms_cmd_expand(project, slug, config=cfg)
            child_manifest_path = manifest_path.parent / "phase2-dag.json"

        else:  # approve-review — cross-loop: review Phase-2 -> a NEW manuscript tree
            derived = _derive_project_and_id(manifest, prefix="review-", suffix="-phase2")
            if derived is None:
                raise ValueError(
                    f"cannot derive (project, scope) from manifest run_id="
                    f"{manifest.get('run_id')!r} project={manifest.get('project')!r}"
                )
            project, scope_id = derived

            # Deliverable gate (2026-07-09): manuscript emission is OPT-IN,
            # chosen ONCE at the human gate the operator already touches
            # (approve-protocol), via the frozen `deliverable` field in
            # _protocol.md. `deliverable: manuscript` -> emit (below,
            # unchanged). `deliverable: review` (or absent -> default
            # review, the safe/smaller commitment) -> approve-review's GO
            # is TERMINAL: the review stands alone as the knowledge
            # artifact, no manuscript tree, no child_runs entry. This is a
            # NEW early-return guard at the top of the emit decision — the
            # adopt branches, the F2 partial-adopt-reenters-Phase-1 fix,
            # and child_runs idempotency below are otherwise untouched.
            from ..review import read_protocol_deliverable as _read_deliverable
            protocol_path = cfg.project_notes_dir(project) / "reviews" / scope_id / "_protocol.md"
            deliverable = _read_deliverable(protocol_path)
            ns["deliverable"] = deliverable
            if deliverable != "manuscript":
                ns["phase_transition_note"] = (
                    f"deliverable={deliverable} — review complete, manuscript "
                    f"not emitted (opt-in via protocol deliverable field)"
                )
                return

            from .. import manuscript as _manuscript

            tree_root = cfg.project_notes_dir(project) / "manuscripts" / scope_id
            manuscript_note_path = tree_root / "_manuscript.md"

            if manuscript_note_path.exists():
                # Operator/prior-partial scaffold already present — adopt it
                # rather than clobbering (cmd_new hard-fails on an existing
                # note_path anyway; this branch avoids ever calling it).
                phase1_path = tree_root / "phase1-dag.json"
                if phase1_path.exists():
                    child_manifest = json.loads(phase1_path.read_text(encoding="utf-8"))
                    child_manifest_path = phase1_path
                else:
                    # F2 FIX (framework-gate-autonomy design delta): a
                    # partial/interrupted scaffold (the note exists, but no
                    # Phase-1 manifest was ever written) must RE-ENTER the
                    # framework pipeline — never bypass straight to Phase-2
                    # drafting with no committed, critic-cleared spine. The
                    # pre-fix behavior called `cmd_expand` directly here,
                    # which jumps to Phase-2 unconditionally, skipping the
                    # framework-lens-ensemble/synthesize/critic gate entirely.
                    from ..note import _parse_frontmatter as _pfm_partial
                    from ..manuscript.types import get_type as _get_ms_type_partial
                    from ..manuscript import _build_phase1_manifest as _build_p1

                    _note_text = manuscript_note_path.read_text(encoding="utf-8")
                    _fields_partial, _ = _pfm_partial(_note_text)
                    _ms_type_key_partial = str(_fields_partial.get("manuscript_type", "")).strip()
                    _ms_type_partial = _get_ms_type_partial(_ms_type_key_partial)
                    if _ms_type_partial is None:
                        raise ValueError(
                            f"partial-adopt at {tree_root}: unrecognized/missing "
                            f"manuscript_type {_ms_type_key_partial!r} in "
                            f"{manuscript_note_path} — cannot re-enter Phase-1."
                        )

                    _phase1_manifest = _build_p1(
                        project=project,
                        slug=scope_id,
                        ms_type=_ms_type_partial,
                        project_notes_dir=cfg.project_notes_dir(project),
                        tree_root=tree_root,
                        config=cfg,
                    )
                    if _phase1_manifest is not None:
                        # The type has a real Phase-1 (e.g. lit-review's
                        # framework ensemble) — re-enter it, never Phase-2.
                        phase1_path.write_text(
                            json.dumps(_phase1_manifest, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8",
                        )
                        child_manifest = _phase1_manifest
                        child_manifest_path = phase1_path
                    else:
                        # A pass-through type (no Phase-1 at all) —
                        # the only correct case where going straight to
                        # Phase-2 is honest, not a bypass.
                        child_manifest = _manuscript.cmd_expand(project, scope_id, config=cfg)
                        child_manifest_path = tree_root / "phase2-dag.json"
            else:
                _note_path, tree_root, phase1_manifest = _manuscript.cmd_new(
                    project,
                    slug=scope_id,
                    ms_type_key="lit-review",
                    from_review=scope_id,
                    config=cfg,
                )
                if phase1_manifest is not None:
                    child_manifest = phase1_manifest
                    child_manifest_path = tree_root / "phase1-dag.json"
                else:
                    # Defensive pass-through (lit-review always has a real
                    # Phase-1 today) — a future type registered
                    # under this same node_id branch with no Phase-1 still
                    # advances correctly.
                    child_manifest = _manuscript.cmd_expand(project, scope_id, config=cfg)
                    child_manifest_path = tree_root / "phase2-dag.json"

        child = _start_dag_run_inprocess(child_manifest, child_manifest_path, store)
        run_state.meta.setdefault("child_runs", {})[node_id] = child.run_id
        ns["emitted_next_phase_run_id"] = child.run_id
    except Exception as e:  # noqa: BLE001 — surface, never swallow (charter §2)
        ns["phase_transition_error"] = str(e)[:2000]


def _recompute_awaiting_go(
    run_state: RunState,
    manifest: dict[str, Any],
    store: RunStore,
) -> list[FrontierNode]:
    """Compute frontier and auto-advance human-go/autonomous-gate nodes.

    Three outcomes for a "await-go"-frontier node that is still "pending":
      - **approve-protocol** (or any other true human-go node, never one of
        ``_AUTONOMOUS_GATE_IDS``) — promoted to "awaiting-go" as before
        (the one retained human gate — the run genuinely stops here
        for a human keypress).
      - **An autonomous gate (coverage-gate / approve-framework /
        approve-manuscript)** — resolved AUTOMATICALLY via
        ``_evaluate_autonomous_gate``, no external ``--auto`` call needed.
        GO/GO-WITH-RESIDUE -> "succeeded" (+ ``_emit_next_phase``);
        HALT-DECLARE -> "blocked" (a first-class NOT-CLEARED artifact,
        never left sitting in "awaiting-go" looking like it needs a human);
        REVISE -> promoted to "awaiting-go" same as a human gate (the
        bounded auto-revise dispatch is a SEPARATE, agent-driven follow-up
        this runner cannot execute in-process — an LLM revise round is not
        a tool op).

    D4 (verb consolidation): also auto-executes any ready 'tool' node
    IN-PROCESS, before computing the frontier returned to the caller — a
    tool node must never sit in the frontier waiting for a human/agent to
    "dispatch" it by hand.

    The run state is saved after any promotions/tool-executions/autonomy
    resolutions/phase-emissions.
    """
    tool_executed = _auto_execute_tool_nodes(run_state, manifest, store)

    manifest_path = Path(run_state.manifest_path)
    nodes_lookup = manifest_nodes_by_id(manifest)
    cap = manifest_global_cap(manifest)
    frontier = compute_frontier(
        manifest,
        run_state.node_states,
        run_state.edge_registered_ts,
        cap,
    )

    # Promote pending human-go nodes / auto-resolve pending autonomous gates
    # that are now await-go-ready.
    mutated = False
    for item in frontier:
        if item.action != "await-go":
            continue
        node_id = item.node_id
        current = run_state.node_status(node_id)
        if current != "pending":
            continue

        if node_id in _AUTONOMOUS_GATE_IDS:
            from ..review import autonomy as _autonomy

            disposition = _evaluate_autonomous_gate(
                node_id, nodes_lookup, manifest_path, run_state, manifest=manifest,
            )
            mutated = True
            ns = run_state.node_states.setdefault(node_id, {})
            ns["decision_note"] = f"{disposition.disposition} (auto): {disposition.reason}"
            ns["approved_by"] = "review.autonomy"
            ns["approval_method"] = "autonomous-gate-policy-engine"

            if disposition.disposition == _autonomy.HALT_DECLARE:
                run_state.set_node_status(node_id, "blocked", error=disposition.reason[:4000])
            elif disposition.is_go:
                run_state.set_node_status(node_id, "succeeded")
                _emit_next_phase(node_id, manifest, manifest_path, run_state, store)
            else:  # REVISE — no in-process fix available; stays a stop point.
                run_state.set_node_status(node_id, "awaiting-go")
        else:
            run_state.set_node_status(node_id, "awaiting-go")
            mutated = True

    if mutated or tool_executed:
        store.save(run_state)

    # Recompute after promotion (awaiting-go nodes are now non-advanceable,
    # so the frontier won't include them again — but we return the pre-promotion
    # frontier so the caller can print the await-go items with their commands).
    return frontier


# ---------------------------------------------------------------------------
# OKF note type-directory check
# ---------------------------------------------------------------------------

def _check_okf_note_type(
    note_path_str: str, notes_root: Path, cfg: Any = None,
) -> list[str]:
    """Validate that an OKF note's type: frontmatter matches where it lives.

    Returns a list of issue strings (empty = OK).
    This is the vault check gate for produces-typed nodes:
      A node that writes the WRONG type dir fails this check.

    Two membership rules, by declared type:
      - A SHARED type (OKF_SHARED_TYPES — datasets/concepts/literature)
        must resolve UNDER its shared-type root (``cfg.shared_type_root``),
        checked structurally via ``Path.is_relative_to`` — never by
        immediate-parent-name string equality. A citekey-derived filename
        can legitimately sit flat under its shared root; requiring the
        immediate parent dir name to literally equal the type name breaks
        for any citekey that (pre-sanitization) contained a "/" (e.g. a
        DOI), since the immediate parent then becomes the DOI's own suffix
        segment, not "literature". ``cfg`` is required for this branch —
        callers that omit it (legacy call sites with no Config in scope)
        fall back to the project-scoped check below, which is still
        correct for the common flat-under-notes_root case.
      - A PROJECT-scoped type must have its note's immediate parent
        directory name equal the declared type (unchanged, pre-existing
        behavior).
    """
    note_path = Path(note_path_str)
    if not note_path.is_absolute():
        note_path = notes_root / note_path_str

    if not note_path.exists():
        return [f"note does not exist: {note_path}"]

    try:
        text = note_path.read_text(encoding="utf-8")
    except OSError as e:
        return [f"cannot read note {note_path}: {e}"]

    # Parse frontmatter
    import re
    if not text.startswith("---"):
        return [f"note missing frontmatter: {note_path.name}"]
    end = text.find("\n---", 3)
    if end == -1:
        return [f"note frontmatter not closed: {note_path.name}"]
    fm_block = text[3:end].strip()
    fields: dict[str, str] = {}
    for line in fm_block.splitlines():
        m = re.match(r"^(\w[\w-]*):\s*(.*)$", line)
        if m:
            k, v = m.group(1), m.group(2).strip().strip("\"'")
            fields[k] = v

    declared_type = fields.get("type", "")
    if not declared_type:
        return [f"note missing 'type' frontmatter: {note_path.name}"]

    if cfg is not None:
        from ..note import OKF_SHARED_TYPES as _OKF_SHARED_TYPES_CHECK
        if declared_type in _OKF_SHARED_TYPES_CHECK:
            try:
                shared_root = cfg.shared_type_root(declared_type).resolve()
            except KeyError:
                shared_root = None
            if shared_root is not None:
                if note_path.resolve().is_relative_to(shared_root):
                    return []
                return [
                    f"note type mismatch: type={declared_type!r} is a "
                    f"shared type but {note_path} does not resolve under "
                    f"its shared root ({shared_root})"
                ]

    # Project-scoped fallback: the type must match the immediate parent
    # directory name.
    parent_dir = note_path.parent.name
    if declared_type != parent_dir:
        return [
            f"note type mismatch: type={declared_type!r} but "
            f"file is in {parent_dir!r} directory ({note_path})"
        ]

    return []


def _check_experiments_provenance_chain(note_path_str: str, notes_root: Path) -> list[str]:
    """ CHECK-1: ride the provenance-chain completeness gate at complete-time.

    Called AFTER _check_okf_note_type has already confirmed type:dir match, for
    any produces.note / produces.result target. Only fires when the note's
    declared type is "experiments" — the chain rule (results_commit/repro_seed/
    repro_config_*/dataset-link) is meaningless for other OKF types.

    Reuses note.py::check_provenance_chain verbatim (zero new mechanism) — this
    function is just the resolve-and-dispatch glue so a succeeded produce-note
    node with an incomplete provenance chain BLOCKS at the complete gate, the
    same structural posture the dataset-provenance gate already has.

    Returns a list of violation strings (empty = OK or not an experiments note).
    """
    note_path = Path(note_path_str)
    if not note_path.is_absolute():
        note_path = notes_root / note_path_str

    if not note_path.exists():
        return []  # _check_okf_note_type already reports missing-note; don't double-report

    try:
        text = note_path.read_text(encoding="utf-8")
    except OSError:
        return []  # likewise already reported by _check_okf_note_type

    from ..note import _parse_frontmatter as _pfm_chain
    from ..note import check_provenance_chain

    fields, _ = _pfm_chain(text)
    if fields.get("type", "") != "experiments":
        return []

    return check_provenance_chain(note_path)


def _check_relate_presence(
    note_path_str: str, notes_root: Path, node_id: str,
    literature_root: Path | None = None,
) -> list[str]:
    """Wave 0 (Reading) rejects-only presence check — ride at complete-time.

    Only fires for a ``relate-<key>`` node completing a ``literature``-type
    note (the review loop's Phase-2 fan-out, ``review/__init__.py``
    ``_build_phase2_manifest``). Fixes the READING DISCIPLINE, never the note
    SCHEMA (flexible-not-rigid, design doc) — a note missing a mandatory
    checklist answer (Move 1 contribution_kind, role/position, Move 3/
     result_reported, Move 4 paper_relations_sought) BLOCKs at
    complete-time, mirroring the existing OKF-type and provenance-chain gates'
    structural posture.

    the overlay unwind (0.3.2): a literature note is now
    shared-canonical — ONE note, no per-project overlay to merge.
    ``note_path_str`` (already resolved by the caller against
    ``cfg.shared_type_root("literature")`` — see the call site above) IS the
    note; the checklist is evaluated against its own text directly. The
    ``literature_root`` parameter is kept for call-site back-compat (still
    threaded through by the caller) but is no longer used for a merge —
    there is nothing left to assemble.

    Returns a list of finding strings (empty = OK or not a relate- node).
    """
    if not node_id.startswith("relate-"):
        return []

    note_path = Path(note_path_str)
    if not note_path.is_absolute():
        note_path = notes_root / note_path_str

    if not note_path.exists():
        return []  # _check_okf_note_type already reports missing-note; don't double-report

    try:
        text = note_path.read_text(encoding="utf-8")
    except OSError:
        return []  # likewise already reported by _check_okf_note_type

    from ..note import _parse_frontmatter as _pfm_relate

    fields, _body = _pfm_relate(text)
    if fields.get("type", "") != "literature":
        return []

    from ..review.relate_check import check_relate_presence

    result = check_relate_presence(note_path, text=text)
    return result.findings


# ---------------------------------------------------------------------------
# Project-scoped typed produces gate
# ---------------------------------------------------------------------------

# Maps produces.* subkey → OKF type directory.
# SSOT: the produces subkey name ("result") is the semantic name;
# the OKF type directory is the filesystem path segment.
_PRODUCES_KEY_TO_OKF_DIR: dict[str, str] = {
    "result": "experiments",
}


def _project_scoped_note_path(
    pkey: str,
    note_ref: str,
    cfg,
) -> Path:
    """Resolve a project-scoped produces.* ref to an ABSOLUTE Path.

    note_ref format: "<project>/<id>" (id may or may not include .md extension).
    Resolves to: project_notes_dir(project) / <type_dir> / "<id>.md"

    This is the SSOT for produces.result path resolution — used by BOTH:
      • _check_project_scoped_note  (the complete-gate validator)
      • resolve_produces_paths      (the brief's expected-output context)

    By routing both callers through this single function, the gate-checked
    path and the brief's declared path are IDENTICAL BY CONSTRUCTION.

    Raises
    ------
    ValueError   if note_ref is not in "<project>/<id>" format.
    KeyError     if the project slug is not in the config registry.
    """
    if "/" not in note_ref:
        raise ValueError(
            f"produces.{pkey}: expected '<project>/<id>' format, got {note_ref!r}"
        )

    project_slug, note_id = note_ref.split("/", 1)
    if not project_slug or not note_id:
        raise ValueError(
            f"produces.{pkey}: empty project or id in {note_ref!r}"
        )

    type_dir = _PRODUCES_KEY_TO_OKF_DIR[pkey]
    proj_notes = cfg.project_notes_dir(project_slug)  # raises KeyError if unknown
    note_id_with_ext = note_id if note_id.endswith(".md") else f"{note_id}.md"
    return proj_notes / type_dir / note_id_with_ext


def _check_project_scoped_note(
    pkey: str,
    note_ref: str,
    cfg,
) -> list[str]:
    """Validate a project-scoped produces.result note.

    Resolves the path via _project_scoped_note_path (SSOT) then validates
    via _check_okf_note_type (type:dir match).

    Returns a list of issue strings (empty = OK).
    """
    try:
        note_path = _project_scoped_note_path(pkey, note_ref, cfg)
    except ValueError as e:
        return [str(e)]
    except KeyError:
        project_slug = note_ref.split("/", 1)[0]
        return [
            f"produces.{pkey}: unknown project slug {project_slug!r} "
            f"(not in config projects registry)"
        ]

    # Resolve the project notes dir for _check_okf_note_type (notes_root arg)
    project_slug = note_ref.split("/", 1)[0]
    try:
        proj_notes = cfg.project_notes_dir(project_slug)
    except Exception:
        proj_notes = note_path.parent.parent  # best-effort fallback

    # _check_okf_note_type takes an absolute path; notes_root unused for absolute.
    return _check_okf_note_type(str(note_path), proj_notes, cfg=cfg)


# ---------------------------------------------------------------------------
# resolve_produces_paths — informational path list for build_brief
# ---------------------------------------------------------------------------
#
# Used by build_brief (dag/brief.py) to populate the CONTEXT block with the
# expected output path(s) for the node.
#
# For produces.result (project-scoped typed notes), this function calls
# _project_scoped_note_path — THE SAME PRIMITIVE as _check_project_scoped_note.
# The gate-checked path and the brief's declared path are therefore IDENTICAL
# BY CONSTRUCTION (one code path, not two independent re-implementations).
#
# For validation errors, callers use _check_okf_note_type /
# _check_project_scoped_note directly (the complete-gate path).  This function
# is INFORMATIONAL — it resolves what it can and silently skips unknowns.

def resolve_produces_paths(
    node: dict[str, Any],
    cfg: Any,
    *,
    manifest_project: str | None = None,
) -> list[Path]:
    """Resolve a node's produces: entries to absolute Path objects.

    Parameters
    ----------
    node:              The node dict (may have a ``produces`` key).
    cfg:               The loaded Config object.
    manifest_project:  The manifest-level ``project`` slug (optional).
                       When provided, produces.note is resolved via
                       cfg.project_notes_dir(slug).  When absent, cfg.notes_root.

    Returns
    -------
    A list of absolute Path objects.  One entry per produces sub-key that
    resolves to a deterministic path.  Returns [] when produces is absent.

    SSOT guarantee
    --------------
    For produces.result entries, this function calls _project_scoped_note_path —
    the SAME primitive used by the complete-gate's _check_project_scoped_note.
    The gate-checked path == the brief's "expected output" path by construction.
    """
    produces = node.get("produces")
    if not produces or not isinstance(produces, dict):
        return []

    paths: list[Path] = []

    # Determine note root for produces.note
    if manifest_project:
        try:
            note_root: Path = cfg.project_notes_dir(manifest_project)
        except Exception:
            note_root = cfg.notes_root
    else:
        note_root = cfg.notes_root

    for key, value in produces.items():
        if not isinstance(value, str) or not value:
            continue

        if key == "note":
            # Relative note path within notes_root (same rule as cmd_complete gate)
            p = Path(value)
            if not p.is_absolute():
                p = note_root / value
            paths.append(p)

        elif key == "dataset":
            # Shared datasets store
            p = Path(value)
            if not p.is_absolute():
                p = cfg.datasets_root / value
            paths.append(p)

        elif key in _PRODUCES_KEY_TO_OKF_DIR:
            # Project-scoped typed note — use SSOT primitive (_project_scoped_note_path)
            # so this path is IDENTICAL to what _check_project_scoped_note computes.
            try:
                paths.append(_project_scoped_note_path(key, value, cfg))
            except (ValueError, KeyError):
                pass  # Bad format or unknown project — informational, don't abort

        else:
            # Arbitrary file key (e.g. "_protocol.md": "/abs/path/…")
            p = Path(value)
            if p.is_absolute():
                paths.append(p)

    return paths


# ---------------------------------------------------------------------------
# Verb: run
# ---------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    """Load manifest, create run state, print the initial frontier."""
    manifest_path = Path(args.manifest).expanduser().resolve()
    if not manifest_path.exists():
        print(f"rv dag run: manifest not found: {manifest_path}", file=sys.stderr)
        return 1

    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as e:
        print(f"rv dag run: manifest error: {e}", file=sys.stderr)
        return 1

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv dag run: config error: {e}", file=sys.stderr)
        return 1

    store = RunStore.from_config(cfg)
    run_id = manifest["run_id"]

    # Create the initial run state
    run_state = RunState(
        run_id=run_id,
        manifest_path=str(manifest_path),
        created_at=time.time(),
    )
    run_state.init_nodes(manifest)

    try:
        store.create(run_state)
    except StoreError as e:
        print(f"rv dag run: {e}", file=sys.stderr)
        return 1

    _print_manifest_warns(manifest)

    # Resolve reads: pointers (I/O pass — after pure validate)
    _resolve_reads_or_warn(
        manifest, manifest_path.parent, "rv dag run",
        node_states=run_state.node_states,
    )

    print(f"Run {run_id!r} started.")
    print(f"  manifest: {manifest_path}")
    print(f"  nodes: {len(manifest['nodes'])}")
    print(f"  global_cap: {manifest_global_cap(manifest)}")
    print()
    print("Initial frontier:")
    frontier = _recompute_awaiting_go(run_state, manifest, store)
    _print_frontier(frontier, run_id)
    return 0


# ---------------------------------------------------------------------------
# Verb: tick
# ---------------------------------------------------------------------------

def cmd_tick(args: argparse.Namespace) -> int:
    """Re-compute the frontier. Resolves afterok+watch edges inline."""
    run_id = args.run_id

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv dag tick: config error: {e}", file=sys.stderr)
        return 1

    store = RunStore.from_config(cfg)
    try:
        run_state = store.load(run_id)
    except StoreError as e:
        print(f"rv dag tick: {e}", file=sys.stderr)
        return 1

    # Load the manifest
    manifest_path = Path(run_state.manifest_path)
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as e:
        print(f"rv dag tick: manifest error: {e}", file=sys.stderr)
        return 1

    _print_manifest_warns(manifest)

    # Resolve reads: pointers (I/O pass — after pure validate)
    _resolve_reads_or_warn(
        manifest, manifest_path.parent, "rv dag tick",
        node_states=run_state.node_states,
    )

    print(f"Tick: run {run_id!r}")
    frontier = _recompute_awaiting_go(run_state, manifest, store)
    print("Frontier:")
    _print_frontier(frontier, run_id)
    return 0


# ---------------------------------------------------------------------------
# Verb: complete
# ---------------------------------------------------------------------------

_FAILURE_SUMMARY_MAX_CHARS = 4000  # cap stored failure summaries


def cmd_complete(args: argparse.Namespace) -> int:
    """Mark a node complete and re-print the frontier.

    On --status failed, reads --error / --error-file, persists
    last_failure + failures[], increments attempts.  If attempts_before <
    max_retries → resets to pending (retry-queued); else → terminal failed.
    --error is REQUIRED when the node's max_retries > 0 (D-RETRY-9).
    """
    run_id = args.run_id
    node_id = args.node_id
    status = getattr(args, "status", "succeeded") or "succeeded"

    if status not in ("succeeded", "failed", "blocked"):
        print(
            f"rv dag complete: --status must be 'succeeded', 'failed', or 'blocked', "
            f"got {status!r}",
            file=sys.stderr,
        )
        return 1

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv dag complete: config error: {e}", file=sys.stderr)
        return 1

    store = RunStore.from_config(cfg)
    try:
        run_state = store.load(run_id)
    except StoreError as e:
        print(f"rv dag complete: {e}", file=sys.stderr)
        return 1

    manifest_path = Path(run_state.manifest_path)
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as e:
        print(f"rv dag complete: manifest error: {e}", file=sys.stderr)
        return 1

    nodes_lookup = manifest_nodes_by_id(manifest)
    if node_id not in nodes_lookup:
        print(f"rv dag complete: node {node_id!r} not in manifest", file=sys.stderr)
        return 1

    current_status = run_state.node_status(node_id)
    if current_status in TERMINAL_STATUSES:
        print(
            f"rv dag complete: node {node_id!r} is already terminal "
            f"(status={current_status!r}). No change.",
        )
        return 0

    node = nodes_lookup[node_id]

    # ── failure capture + retry-reset logic ─────────────────────────────────────
    # This block runs BEFORE the OKF produces check (which is succeeded-only)
    # and BEFORE set_node_status, so it controls whether we reach terminal failed
    # or retry-reset to pending.
    if status == "failed":
        max_retries: int = node.get("max_retries", 0)

        # Read failure summary from --error / --error-file (D-RETRY-9)
        error_summary: str | None = getattr(args, "error", None)
        error_file: str | None = getattr(args, "error_file", None)

        if error_file:
            try:
                raw = Path(error_file).read_text(encoding="utf-8")
                # Length-cap to avoid bloating state files on stack-trace dumps
                error_summary = raw[:_FAILURE_SUMMARY_MAX_CHARS]
            except OSError as e:
                print(
                    f"rv dag complete: cannot read --error-file {error_file!r}: {e}",
                    file=sys.stderr,
                )
                return 1

        if error_summary is not None:
            error_summary = error_summary[:_FAILURE_SUMMARY_MAX_CHARS]

        # D-RETRY-9: --error REQUIRED when max_retries > 0 (a retriable failure
        # with no captured summary IS a blind retry — reject it structurally).
        if max_retries > 0 and not error_summary:
            print(
                f"rv dag complete: --error <summary> or --error-file <path> is REQUIRED "
                f"when completing a retriable node ({node_id!r} has max_retries={max_retries}). "
                f"A retry without a captured failure context is a blind retry — forbidden (D-RETRY-9).",
                file=sys.stderr,
            )
            return 1

        # Persist the failure: capture last_failure + append to failures[]
        ns = run_state.node_states.setdefault(node_id, {})
        attempts_before: int = ns.get("attempts", 0)
        attempts_after = attempts_before + 1

        ns["attempts"] = attempts_after
        ns["last_failure"] = error_summary  # None when max_retries==0 and no --error
        failures_list: list[dict] = ns.get("failures", [])
        failures_list.append({
            "attempt": attempts_after,
            "summary": error_summary or "",
            "ts": time.time(),
        })
        ns["failures"] = failures_list

        if attempts_before < max_retries:
            # RETRY-QUEUED: reset to pending — the walker re-surfaces it as dispatch
            # Clear transient fields; RETAIN last_failure/failures (the diagnosis payload)
            ns["status"] = "pending"
            ns["completed_at"] = None
            ns["error"] = None
            ns["started_at"] = None  # reset for truthful per-attempt timing

            store.save(run_state)
            print(
                f"Node {node_id!r} RETRY-QUEUED "
                f"(attempt {attempts_after}/{max_retries} used; resetting to pending)"
            )
            frontier = _recompute_awaiting_go(run_state, manifest, store)
            print("Frontier:")
            _print_frontier(frontier, run_id, node_states=run_state.node_states)
            return 0
        else:
            # EXHAUSTED → terminal failed (D-RETRY-3)
            # failures[] is retained for the human diagnostician
            run_state.set_node_status(node_id, status)
            store.save(run_state)
            # Cosmetic fix: only mention "retries exhausted" when there were
            # retries to exhaust (max_retries > 0).  When max_retries == 0 the node is
            # just a plain terminal failure — printing "retries exhausted: 1/0 attempts"
            # is a nonsensical ratio and confusing to the operator.
            if max_retries > 0:
                exhaustion_detail = (
                    f" (retries exhausted: {attempts_after}/{max_retries} attempts)"
                )
            else:
                exhaustion_detail = ""
            print(f"Node {node_id!r} → {status}{exhaustion_detail}")
            frontier = _recompute_awaiting_go(run_state, manifest, store)
            print("Frontier:")
            _print_frontier(frontier, run_id, node_states=run_state.node_states)
            return 0

    # ── For succeeded / blocked — original path below ─────────────────────────

    # OKF produces check: if the node has produces.note and status is succeeded,
    # validate the note's type:dir matches.
    if status == "succeeded" and "produces" in node:
        produces = node["produces"]

        # The outline pre-pass's
        # cheap, rejects-only gate — ride at complete-time exactly like
        # check_framework_gate rides at approve-time (node-id-keyed gate
        # wiring, the established 3+-instance pattern). Only fires for the
        # lit-review single-pass `outline` node completing `_outline.md`.
        if node_id == "outline" and "_outline.md" in produces:
            outline_ref = produces["_outline.md"]
            outline_path = Path(outline_ref)
            manuscript_note_path = manifest_path.parent / "_manuscript.md"
            branches: list[str] = []
            if manuscript_note_path.exists():
                from ..note import _parse_frontmatter as _pfm_outline

                _fields, _ = _pfm_outline(manuscript_note_path.read_text(encoding="utf-8"))
                _branches_raw = _fields.get("branches", "")
                if isinstance(_branches_raw, str):
                    branches = [b.strip() for b in _branches_raw.split(",") if b.strip()]
                else:
                    branches = [str(b).strip() for b in _branches_raw if str(b).strip()]

            from ..manuscript.types.lit_review import (
                check_outline_gate,
                read_coverage_used_citekeys,
            )

            # coverage-safety: the outline must anchor every `used` paper
            # allocated in `_coverage-map.md` (beside `_manuscript.md`) — a
            # `used` paper unanchored at the outline is about to be dropped in
            # the draft. Absent/empty map -> [] (the allocation gate owns that
            # BLOCK, not this pre-pass).
            used_citekeys = read_coverage_used_citekeys(
                manifest_path.parent / "_coverage-map.md"
            )
            # MOC-entry invariant: manifest_path.parent is the manuscript's
            # tree_root (manuscripts/<slug>/); project_notes_dir is two
            # levels up (manuscripts/<slug>/../.. — the established pattern,
            # see the framework/coverage-hash node wiring above in this
            # file). mocs_dir may not exist yet for an old project — the gate
            # degrades to "no mocs/ dir" via check 5's own dangling-file
            # check, never crashes.
            _project_notes_dir = manifest_path.parent.parent.parent
            mocs_dir = _project_notes_dir / "mocs"
            outline_issues = check_outline_gate(
                outline_path, branches, used_citekeys, mocs_dir=mocs_dir
            )
            if outline_issues:
                print(
                    f"rv dag complete: outline gate FAILED for node {node_id!r}:",
                    file=sys.stderr,
                )
                for issue in outline_issues:
                    print(f"  {issue}", file=sys.stderr)
                print(
                    "  Fix: anchor every frozen branch to a real thesis-claim + "
                    ">=2 papers + the exemplar-move it imitates in _outline.md "
                    "— a cheap screen that catches a "
                    "framework/corpus problem before the expensive whole-draft.",
                    file=sys.stderr,
                )
                return 1

        if "note" in produces:
            # F21 (adopter fix): resolve produces.note against the project's
            # source_dir, not the shared notes_root.  The manifest may declare
            # a "project" slug at the top level; when it does, use
            # cfg.project_notes_dir(slug) as the resolution base so that
            # multi-repo adopters (source_dir != notes_root) are handled
            # correctly.  Falls back to cfg.notes_root for manifests with no
            # "project" field (demo case; source_dir == notes_root stays green).
            #
            # 0.3.2's shared-canonical moves: a
            # shared-canonical type (datasets/concepts/literature) does NOT
            # live under project_notes_dir at all — resolving
            # "literature/<citekey>.md" against a project's source_dir would
            # silently miss the real file (which lives at
            # cfg.literature_root/<citekey>.md). Detect a shared-type prefix
            # on produces.note and resolve directly against the shared root,
            # stripping the type-dir segment (the shared root's own
            # directory listing IS the type — no further <type>/ subdir).
            from ..note import OKF_SHARED_TYPES as _OKF_SHARED_TYPES_PRODUCES
            _note_rel = produces["note"]
            _type_seg, _sep, _rest = _note_rel.partition("/")
            if _type_seg in _OKF_SHARED_TYPES_PRODUCES and _sep:
                _note_root = cfg.notes_root  # unused below — path passed absolute
                _resolved_note_path = str(cfg.shared_type_root(_type_seg) / _rest)
            else:
                _project_slug = manifest.get("project")
                if _project_slug:
                    try:
                        _note_root = cfg.project_notes_dir(_project_slug)
                    except KeyError as _e:
                        print(
                            f"rv dag complete: {_e}",
                            file=sys.stderr,
                        )
                        return 1
                else:
                    _note_root = cfg.notes_root
                _resolved_note_path = _note_rel
            issues = _check_okf_note_type(_resolved_note_path, _note_root, cfg=cfg)
            if issues:
                print(f"rv dag complete: OKF vault check FAILED for node {node_id!r}:", file=sys.stderr)
                for issue in issues:
                    print(f"  {issue}", file=sys.stderr)
                print("  Fix: ensure the note's type: frontmatter matches its parent directory.", file=sys.stderr)
                return 1
            # CHECK-1 (flagship, HARD): ride the provenance-chain
            # completeness gate — only fires for experiments-type notes with a
            # claimed result whose chain is incomplete.
            chain_issues = _check_experiments_provenance_chain(_resolved_note_path, _note_root)
            if chain_issues:
                print(
                    f"rv dag complete: provenance-chain gate FAILED for node {node_id!r}:",
                    file=sys.stderr,
                )
                for issue in chain_issues:
                    print(f"  {issue}", file=sys.stderr)
                print(
                    "  Fix: fill results_commit/repro_seed/repro_config_*/dataset-link "
                    "(CHECK-1).",
                    file=sys.stderr,
                )
                return 1
            # Wave 0 (Reading) relate-<key> node presence-check gate —
            # rejects-only, checklist not schema (see relate_check.py docstring).
            relate_issues = _check_relate_presence(
                _resolved_note_path, _note_root, node_id, literature_root=cfg.literature_root
            )
            if relate_issues:
                print(
                    f"rv dag complete: relate presence check FAILED for node {node_id!r}:",
                    file=sys.stderr,
                )
                for issue in relate_issues:
                    print(f"  {issue}", file=sys.stderr)
                print(
                    "  Fix: answer the missing mandatory checklist question(s) — "
                    "this is a reading-DISCIPLINE check, not a "
                    "rigid schema; the note body/structure stays free-form.",
                    file=sys.stderr,
                )
                return 1
        # Dataset provenance gate — complete-time check.
        # The gate: note exists + location non-empty + hash non-empty +
        # (if local path) file exists and sha256 matches.
        # NOT-done when hash mismatches — "you structurally cannot publish a finding
        # whose data lineage isn't recorded" (the structural teeth are on the
        # watch/frontier path; this is the post-hoc complete-time check).
        if "dataset" in produces:
            from ..wait_for import check_dataset_provenance
            # Datasets are shared — resolve against cfg.datasets_root
            # (not notes_root). The produces.dataset value is the note filename
            # (e.g. "my-data.md") resolved against the shared datasets store.
            issues = check_dataset_provenance(produces["dataset"], cfg.datasets_root)
            if issues:
                print(
                    f"rv dag complete: dataset provenance gate FAILED for node {node_id!r}:",
                    file=sys.stderr,
                )
                for issue in issues:
                    print(f"  {issue}", file=sys.stderr)
                print(
                    "  Fix: ensure the datasets/ provenance note has 'location' and 'hash' "
                    "filled in, and that the hash matches the actual data artifact.",
                    file=sys.stderr,
                )
                return 1
        # Project-scoped typed produces gate.
        # produces.result = "<project>/<id>"
        # Each resolves to project_notes_dir(project) / <type_dir> / <id>.md
        # and validates type:dir frontmatter match (same gate as produces.note).
        for _pkey in _PRODUCES_KEY_TO_OKF_DIR:
            if _pkey in produces:
                issues = _check_project_scoped_note(_pkey, produces[_pkey], cfg)
                if issues:
                    print(
                        f"rv dag complete: OKF vault check FAILED for node {node_id!r} "
                        f"(produces.{_pkey}={produces[_pkey]!r}):",
                        file=sys.stderr,
                    )
                    for issue in issues:
                        print(f"  {issue}", file=sys.stderr)
                    print(
                        f"  Fix: ensure the {_PRODUCES_KEY_TO_OKF_DIR[_pkey]}/ note exists "
                        f"and its type: frontmatter matches its parent directory.",
                        file=sys.stderr,
                    )
                    return 1
                # CHECK-1 (flagship, HARD): ride the provenance-chain
                # completeness gate for project-scoped produces (produces.result).
                # Resolve to an ABSOLUTE path via the SAME primitive the type
                # check used, so notes_root is a no-op (absolute path short-circuits).
                _abs_note_path = str(_project_scoped_note_path(_pkey, produces[_pkey], cfg))
                chain_issues = _check_experiments_provenance_chain(_abs_note_path, cfg.notes_root)
                if chain_issues:
                    print(
                        f"rv dag complete: provenance-chain gate FAILED for node {node_id!r} "
                        f"(produces.{_pkey}={produces[_pkey]!r}):",
                        file=sys.stderr,
                    )
                    for issue in chain_issues:
                        print(f"  {issue}", file=sys.stderr)
                    print(
                        "  Fix: fill results_commit/repro_seed/repro_config_*/dataset-link "
                        "(CHECK-1).",
                        file=sys.stderr,
                    )
                    return 1

    run_state.set_node_status(node_id, status)
    store.save(run_state)

    print(f"Node {node_id!r} → {status}")
    frontier = _recompute_awaiting_go(run_state, manifest, store)
    print("Frontier:")
    _print_frontier(frontier, run_id, node_states=run_state.node_states)
    return 0


# ---------------------------------------------------------------------------
# Verb: approve
# ---------------------------------------------------------------------------

def cmd_approve(args: argparse.Namespace) -> int:
    """Approve (or reject) a human-go node in 'awaiting-go' state.

    Approve path (default):  node → 'succeeded'; frontier advances.
    Reject  path (--reject): node → 'blocked';   frontier halts on this gate.

    Optional flags:
      --note TEXT      Decision rationale (stored in node_states for audit trail).
      --output k=v     Decision output key=value pair (repeatable).  Stored in
                       node_states["outputs"] — downstream nodes that implement
                       human-go-conditional logic read these to branch.
      --reject         Mark as rejected/blocked instead of approved/succeeded.
    """
    run_id = args.run_id
    node_id = args.node_id
    # F13: read the new optional flags (safe getattr — tests that don't set them
    # still work; bare approve calls are backward-compatible).
    decision_note: str | None = getattr(args, "note", None) or None
    raw_outputs: list[str] = getattr(args, "output", None) or []
    reject: bool = bool(getattr(args, "reject", False))
    # The autonomy flag — coverage-gate / approve-framework /
    # approve-manuscript may be resolved by the gate-policy engine instead
    # of a human keypress. approve-protocol is NEVER eligible (the one
    # retained human gate) — see the AUTONOMOUS_GATE_IDS check below.
    auto: bool = bool(getattr(args, "auto", False))

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv dag approve: config error: {e}", file=sys.stderr)
        return 1

    store = RunStore.from_config(cfg)
    try:
        run_state = store.load(run_id)
    except StoreError as e:
        print(f"rv dag approve: {e}", file=sys.stderr)
        return 1

    manifest_path = Path(run_state.manifest_path)
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as e:
        print(f"rv dag approve: manifest error: {e}", file=sys.stderr)
        return 1

    nodes_lookup = manifest_nodes_by_id(manifest)
    if node_id not in nodes_lookup:
        print(f"rv dag approve: node {node_id!r} not in manifest", file=sys.stderr)
        return 1

    node = nodes_lookup[node_id]
    if node.get("type", "agent") != "human-go":
        print(
            f"rv dag approve: node {node_id!r} is type {node.get('type', 'agent')!r}, "
            "not 'human-go'",
            file=sys.stderr,
        )
        return 1

    current_status = run_state.node_status(node_id)
    if current_status != "awaiting-go":
        print(
            f"rv dag approve: node {node_id!r} is not in 'awaiting-go' state "
            f"(current: {current_status!r}). "
            "Run `rv dag tick <run_id>` first to advance the run.",
            file=sys.stderr,
        )
        return 1

    # L-2 anti-fishing structural gate (task #33): the review loop's
    # ``approve-protocol`` node (see review/_build_phase1_manifest)
    # may not be approved unless the upstream ``review-scope`` node's
    # ``_protocol.md`` carries a non-empty ``counter-position`` field.
    #
    # This was previously agent-prose-only (review_scope_tips instructs it, but
    # nothing in code enforced it) — this closes that gap natively in rv so
    # every adopter gets the enforcement, not just a project-local wrapper.
    #
    # Only applies on the approve path — --reject is an explicit escape hatch
    # to abandon/redo the protocol; it must not be blocked by this gate.
    if node_id == "approve-protocol" and not reject:
        review_scope_node = nodes_lookup.get("review-scope")
        protocol_ref = None
        if review_scope_node is not None:
            produces = review_scope_node.get("produces")
            if isinstance(produces, dict):
                protocol_ref = produces.get("_protocol.md")
        if protocol_ref:
            from ..review import check_protocol_gate, check_counter_facet_gate
            ok, msg = check_protocol_gate(Path(protocol_ref))
            if not ok:
                print(msg, file=sys.stderr)
                return 1

            # D-7: a declared thesis facet with no frozen counter-side
            # query is a protocol defect — hard BLOCK, same convention as
            # the check_protocol_gate empty-counter-position field above.
            ok, msg = check_counter_facet_gate(Path(protocol_ref))
            if not ok:
                print(msg, file=sys.stderr)
                return 1

            # D-6: cold, rejects-only, canary-verified
            # counter-facet STRENGTH guard — existence (D-7) != strength; a
            # thesis-biased generator can satisfy D-7 with a straw-man.
            # the direct-API judge path was deleted; the strength judge runs
            # via the cold emit/ingest fan-out. Unified HALT (deliverable #3):
            # no fan-out / no verdicts / incomplete fan-out -> HALT-DECLARE
            # (supersedes the old SIGNAL — a relied-on cold gate that
            # cannot run HALTs). The structural, judge-INDEPENDENT guard still
            # runs synchronously and hard-BLOCKs garbage `seed_queries:`.
            from ..review.counter_facet_guard import (
                CanaryAbortError as _CFCanaryAbortError,
                cf_fanout_present,
                check_counter_facet_strength,
                ingest_counter_facet_verdicts_from_dir,
            )

            protocol_text = Path(protocol_ref).read_text(encoding="utf-8")
            cf_dir = Path(protocol_ref).parent / "judge" / "counter-facet"

            # (a) structural, judge-independent BLOCK (always synchronous).
            struct = check_counter_facet_strength(protocol_text)  # judge_fn=None
            if struct["blocking"]:
                for b in struct["blocking"]:
                    print(f"rv dag approve: approve-protocol BLOCKED: {b}", file=sys.stderr)
                return 1

            # (b) strength via the cold fan-out — ingest if emitted, else the
            #     unified HALT-DECLARE surface. Consistent with the
            #     support-matcher at approve-manuscript: the "cannot run" cases
            #     (no fan-out / incomplete fan-out / no verdicts) are surfaced
            #     LOUDLY as a HALT-DECLARE not_run (the human-go gate sees the
            #     floor was never checked; the autonomous gate-policy engine
            #     treats not_run as HALT-DECLARE) — they do NOT hard-block the
            #     manual gate. Only ACTIONABLE untrustworthy signal — a real
            #     straw-man verdict or a canary abort — is a hard BLOCK.
            if cf_fanout_present(cf_dir):
                try:
                    cf_result = ingest_counter_facet_verdicts_from_dir(cf_dir)
                except _CFCanaryAbortError as e:
                    print(
                        f"rv dag approve: approve-protocol BLOCKED "
                        f"(CANARY ABORT / HALT-DECLARE): {e}",
                        file=sys.stderr,
                    )
                    return 1
                for nr in cf_result["not_run"]:
                    print(f"rv dag approve: approve-protocol HALT-DECLARE: {nr}", file=sys.stderr)
                if cf_result["blocking"]:
                    for b in cf_result["blocking"]:
                        print(f"rv dag approve: approve-protocol BLOCKED: {b}", file=sys.stderr)
                    return 1
            else:
                # No fan-out emitted -> unified HALT-DECLARE surface (`struct`
                # carries the no-judge HALT message, judge_fn=None).
                for nr in struct["not_run"]:
                    print(f"rv dag approve: approve-protocol HALT-DECLARE: {nr}", file=sys.stderr)

            # D-1: the derived 40-100 distinct-query band is a SIGNAL,
            # never a BLOCK (the generator's derived count is a target, not
            # an exact requirement) — always printed so the human sees it at
            # the gate (charter §2: surface, never silently drop).
            from ..sources.sweep import parse_angle_matrix, validate_matrix_band
            angle_matrix = parse_angle_matrix(Path(protocol_ref).read_text(encoding="utf-8"))
            if angle_matrix:
                in_band, band_msg = validate_matrix_band(angle_matrix)
                if not in_band:
                    print(f"rv dag approve: approve-protocol SIGNAL: {band_msg}", file=sys.stderr)

            # 0.3.1 Layer 1: the per-facet/per-pole generation-time breadth
            # floor — a real HARD BLOCK (unlike D-1's band SIGNAL above),
            # scoped to the nested D-3 facet form (mirrors D-7's own
            # scoping). N/M are config-driven, per-review-type overridable
            # (review.style.get_min_queries_per_facet/get_min_queries_per_pole).
            from ..sources.sweep import check_facet_breadth_floor
            from ..review.style import (
                get_min_queries_per_facet as _get_min_per_facet,
                get_min_queries_per_pole as _get_min_per_pole,
            )
            _cfg = load_config()
            ok, msg = check_facet_breadth_floor(
                Path(protocol_ref).read_text(encoding="utf-8"),
                min_per_facet=_get_min_per_facet(_cfg),
                min_per_pole=_get_min_per_pole(_cfg),
            )
            if not ok:
                print(msg, file=sys.stderr)
                return 1

    # the lit-review manuscript type's framework-selection Phase-1 gate
    # (D5) — mirrors the L-2 gate above. ``approve-framework`` may
    # not be approved unless the manuscript's ``_manuscript.md`` (sibling to
    # this Phase-1 manifest, at ``manifest_path.parent``) carries a non-empty
    # ``spine_shape``+``branches``. Only the lit-review type registers a
    # Phase-1 with this node id; other types' Phase-1 (if any) never hits this
    # branch. --reject is the escape hatch, same convention as approve-protocol.
    if node_id == "approve-framework" and not reject and not auto:
        manuscript_note_path = manifest_path.parent / "_manuscript.md"
        from ..manuscript.types.lit_review import check_framework_gate
        ok, msg = check_framework_gate(manuscript_note_path)
        if not ok:
            print(msg, file=sys.stderr)
            return 1

    # Manuscript-integration PR: the assembled gate payload (hermetic
    # references-build BLOCK, equation-fidelity SIGNAL, support-matcher BLOCK/SIGNAL behind
    # the judge guard — manuscript/check_gates.py::build_approve_payload)
    # gates ``approve-manuscript``. Mirrors the ``approve-framework`` wiring
    # above exactly: ``manifest_path.parent`` IS the manuscript tree root
    # (Phase-2 manifests are written to ``manuscripts/<slug>/phase2-dag.json``,
    # sibling to ``_manuscript.md``). --reject is the same escape hatch.
    if node_id == "approve-manuscript" and not reject and not auto:
        tree_root = manifest_path.parent
        manuscript_note_path = tree_root / "_manuscript.md"
        if manuscript_note_path.exists():
            from ..note import _parse_frontmatter as _pfm_approve
            from ..manuscript.types import get_type as _get_ms_type
            from ..manuscript.check_gates import build_approve_payload

            _text = manuscript_note_path.read_text(encoding="utf-8")
            _fields, _ = _pfm_approve(_text)
            _ms_type = _get_ms_type(_fields.get("manuscript_type", ""))
            if _ms_type is not None:
                project_notes_dir = tree_root.parent.parent
                payload = build_approve_payload(
                    tree_root, project_notes_dir, _ms_type,
                    literature_root=cfg.literature_root,
                    concepts_root=cfg.concepts_root,
                )
                if not payload["ok"]:
                    print(
                        "rv dag approve: approve-manuscript BLOCKED by fidelity gates:",
                        file=sys.stderr,
                    )
                    for b in payload["blocking"]:
                        print(f"  BLOCK: {b}", file=sys.stderr)
                    return 1
                # SIGNALs and not_run gates never block approval — but they
                # are ALWAYS printed (charter §2: surface, never silently
                # drop; never green-and-empty) so the human sees them at the
                # gate, not buried in a log file elsewhere.
                for s in payload["signals"]:
                    print(f"rv dag approve: approve-manuscript SIGNAL: {s}", file=sys.stderr)
                for n in payload["not_run"]:
                    print(f"rv dag approve: approve-manuscript NOT RUN: {n}", file=sys.stderr)
            else:
                # fix (integration-reviewer followup, charter §2): an
                # unregistered/malformed ``manuscript_type`` used to fall
                # through this ``if`` silently — no gates ran, nothing was
                # printed, and the human-go gate would pass with ZERO
                # fidelity checking. That is a green-and-empty sliver: a
                # manuscript whose type field is blank, typo'd, or references
                # a type that was never registered must NOT look identical to
                # one that passed every gate. Surface it as loudly as the
                # judge-not-configured NOT-RUN case above (never a silent
                # skip) — but do NOT block: an unknown type is a data problem
                # in ``_manuscript.md``, not a fidelity failure, and blocking
                # here would make the type field un-fixable via the normal
                # approve/reject flow.
                _raw_type = _fields.get("manuscript_type", "")
                print(
                    "rv dag approve: approve-manuscript NOT RUN: manuscript_type "
                    f"{_raw_type!r} is unrecognized (unregistered or missing) — "
                    "the hermetic references build, equation-fidelity, and support-matcher "
                    "gates were NOT run for this manuscript. This is "
                    "NOT a pass: fix `manuscript_type:` in "
                    f"{manuscript_note_path} to a registered type (see "
                    "`rv manuscript <project> new --type <type>`) and re-run "
                    "`rv dag approve` before trusting this manuscript.",
                    file=sys.stderr,
                )

    # Walk-terminal surfacing (0.3.1): the review loop's ``coverage-gate``
    # node (phase boundary) reads ``stop_reason:`` off the ``review-snowball``
    # node's ``_walk.md`` and, when the corpus terminated via the total-fetch
    # BUDGET (bounded, before the walk reached its declared depth/
    # neighborhood bound), LOUDLY flags it to the approving human — a
    # budget-terminated corpus must never look identical to a clean
    # ``walk-complete``/``neighborhood-exhausted`` terminal at this gate.
    # Non-blocking (mirrors the approve-manuscript SIGNAL pattern above):
    # the budget-terminal is a bounded, honest escape hatch, not a failure —
    # approval still proceeds, but the human authorizes it informed.
    # ``walk-complete:N-hops``/``neighborhood-exhausted`` are silent (the
    # normal, expected terminals — nothing to surface). --reject bypasses
    # entirely (an abandoned gate has nothing to surface).
    if node_id == "coverage-gate" and not reject:
        snowball_node = nodes_lookup.get("review-snowball")
        walk_ref = None
        if snowball_node is not None:
            produces = snowball_node.get("produces")
            if isinstance(produces, dict):
                walk_ref = produces.get("_walk.md")
        if walk_ref:
            from ..review import check_walk_terminal, check_source_coverage

            # Source-coverage fail-closed (pre-publish hardening batch,
            # 2026-07-09 downstream e2e-run finding): checked FIRST and
            # BLOCKS (unlike the budget-terminal SIGNAL below) — a source
            # declared in the protocol's `sources:` list that went DARK this
            # sweep must never be certified, whether resolved via --auto or
            # a manual `rv dag approve`.
            review_dir_manual = Path(walk_ref).parent
            search_hits_node_manual = nodes_lookup.get("review-search")
            search_hits_ref_manual = None
            if search_hits_node_manual is not None:
                _produces = search_hits_node_manual.get("produces")
                if isinstance(_produces, dict):
                    search_hits_ref_manual = _produces.get("_search_hits.md")
            if search_hits_ref_manual and not auto:
                # --auto is handled by classify_coverage_gate's own
                # source_coverage_info short-circuit (wired below via
                # `_evaluate_autonomous_gate`) — never duplicate the BLOCK
                # here, or a manual `return 1` would bypass the disposition
                # the auto path is supposed to resolve.
                source_info = check_source_coverage(
                    Path(search_hits_ref_manual), review_dir_manual / "_protocol.md",
                )
                if source_info["declared_dark"]:
                    print(
                        "rv dag approve: coverage-gate BLOCKED — source(s) "
                        "declared in the protocol's `sources:` list were DARK "
                        f"this sweep — {', '.join(source_info['declared_dark'])} "
                        "— every cell for each errored or returned zero hits "
                        "across ALL angles. The corpus cannot be certified "
                        "while a declared source was never actually "
                        "reached; re-run the sweep once the source is "
                        "reachable before re-evaluating this gate.",
                        file=sys.stderr,
                    )
                    return 1

            info = check_walk_terminal(Path(walk_ref))
            if info["exists"] and info["stop_reason"].lower().startswith("budget:"):
                gaps_path = Path(walk_ref).parent / "_coverage-gaps.md"
                print(
                    "rv dag approve: coverage-gate SIGNAL: ⚠ budget-terminated — "
                    "the review-snowball walk hit the total-fetch ceiling "
                    f"({info['stop_reason']}) before reaching its declared "
                    "depth/neighborhood bound. You are authorizing a BOUNDED "
                    f"corpus, not a depth-complete one. See {gaps_path} for the "
                    "declared open frontier.",
                    file=sys.stderr,
                )
                if not gaps_path.exists():
                    print(
                        "rv dag approve: coverage-gate SIGNAL: the residue note is "
                        f"REQUIRED on budget-termination but was not found at "
                        f"{gaps_path} — the open frontier was never declared "
                        "(see review_curate_tips's walk-budget guidance).",
                        file=sys.stderr,
                    )
            elif info["exists"] and not (
                info["stop_reason"].lower().startswith("walk-complete:")
                or info["stop_reason"] == "neighborhood-exhausted"
            ):
                # WHITELIST, not a blacklist: ``stop_reason`` is agent/tool-
                # stamped free-ish text — a blacklist that only recognizes
                # known-bad prefixes fails OPEN on every other spelling
                # (a dash instead of a colon, free prose, garbage, or a
                # legacy ``saturated``/``backstop:N-waves`` string from a
                # pre-0.3.1 ``_saturation.md``). Those would sail through
                # SILENTLY and look identical to a genuine clean terminal at
                # the gate, defeating the whole point of this surfacing. Only
                # the two whitelisted clean terminals
                # (``walk-complete:N-hops``/``neighborhood-exhausted``) stay
                # silent; ``budget:N-calls`` already got its sharper message
                # above; everything else — including empty — trips this
                # catch-all SIGNAL.
                print(
                    "rv dag approve: coverage-gate SIGNAL: _walk.md's "
                    f"stop_reason is {info['stop_reason']!r}, not a recognized "
                    "citation-neighbor walk terminal ('walk-complete:N-hops' / "
                    "'neighborhood-exhausted' / 'budget:N-calls') — cannot "
                    "confirm this corpus's completeness under a non-canonical "
                    "spelling. Verify _coverage-gaps.md and the walk report by "
                    "hand before treating this corpus as complete.",
                    file=sys.stderr,
                )

    # ── Autonomous-gate dispatch ────────────────────────────────────────────
    # coverage-gate / approve-framework / approve-manuscript / approve-review
    # may be resolved by the gate-policy engine (review/autonomy.py) instead
    # of a human keypress. approve-protocol is DELIBERATELY excluded — it is
    # the one retained human gate and is never eligible for --auto.
    # Dispatch through the SAME `_evaluate_autonomous_gate` the
    # self-advancing runner uses (single-sourced, no drift between the
    # explicit --auto flag and the always-on runner path).
    if auto and not reject and node_id in _AUTONOMOUS_GATE_IDS:
        from ..review import autonomy as _autonomy

        _disposition_result = _evaluate_autonomous_gate(
            node_id, nodes_lookup, manifest_path, run_state, manifest=manifest,
        )

        print(
            f"rv dag approve --auto: {node_id!r} disposition = "
            f"{_disposition_result.disposition} — {_disposition_result.reason}",
            file=sys.stderr,
        )

        if _disposition_result.disposition == _autonomy.REVISE:
            print(
                f"rv dag approve --auto: {node_id!r} needs a bounded auto-revise "
                "round before it can autonomously GO — dispatch the revise node "
                "and re-run `rv dag approve --auto`. The node remains "
                "'awaiting-go' — no state change.",
                file=sys.stderr,
            )
            return 2
        if _disposition_result.disposition == _autonomy.HALT_DECLARE:
            # A HALT-DECLARE is a first-class NOT-CLEARED artifact — surface
            # it loudly and reject the gate (never silently pass, charter §2).
            reject = True
            if decision_note is None:
                decision_note = f"HALT-DECLARE (auto): {_disposition_result.reason}"
        else:
            # GO / GO-WITH-RESIDUE: fall through to the normal approve path
            # below (reject stays False) — the gate resolves autonomously.
            if decision_note is None:
                decision_note = f"{_disposition_result.disposition} (auto): {_disposition_result.reason}"
            # An explicit `--auto` call gets the SAME
            # phase-transition auto-emission the always-on runner performs —
            # no behavior gap between "the loop resolved this gate on its
            # own tick" and "an operator explicitly drove --auto by hand".
            _emit_next_phase(node_id, manifest, manifest_path, run_state, store)

    # K-3 freeze-set verify hook.
    #
    # When a covers:-freeze hash is stored in run_state.meta["plan_freeze"]
    # AND the node being approved is NOT the plan-freeze gate itself
    # (convention: node_id == "human-go-plan" is the freeze gate), re-derive
    # the hash and BLOCK approval on mismatch.
    #
    # The stored plan_freeze["notes_root"] is used for
    # re-derivation — NOT re-derived from cfg.notes_root.  The config re-derive
    # was the source of the non-reproducibility bug.
    #
    # On a verify EXCEPTION, BLOCK (return 1)
    # instead of warning-and-proceeding.  An integrity gate must fail-closed on
    # inability-to-verify (charter §2: surface, never swallow).
    #
    # require_frozen=False: the hook already gates on plan_freeze presence above,
    # so it never calls verify on a non-frozen run; the no-op path is never needed.
    plan_freeze = run_state.meta.get("plan_freeze")
    if plan_freeze and node_id != "human-go-plan":
        stored_plan_note = plan_freeze.get("plan_note", "")
        if stored_plan_note:
            from ..plan.freeze import verify_freeze_hash
            try:
                # Pass the stored notes_root (the pin) directly; ignore cfg re-derive.
                stored_notes_root_str = plan_freeze.get("notes_root")
                stored_notes_root = (
                    Path(stored_notes_root_str) if stored_notes_root_str else None
                )
                ok, msg = verify_freeze_hash(
                    store, run_id,
                    Path(stored_plan_note),
                    notes_root=stored_notes_root,
                    require_frozen=False,  # already gated on presence above
                )
                if not ok:
                    print(
                        f"rv dag approve: K-3 covers:-freeze MISMATCH — "
                        f"approval BLOCKED.\n{msg}",
                        file=sys.stderr,
                    )
                    return 1
            except Exception as k3_err:
                # BLOCK on exception — an integrity gate must not
                # proceed when it cannot verify.  Old code warned-and-proceeded
                # (a second fail-open); that is now closed.
                print(
                    f"rv dag approve: K-3 verify FAILED with an exception — "
                    f"approval BLOCKED (integrity gate cannot proceed on "
                    f"inability-to-verify): {k3_err}",
                    file=sys.stderr,
                )
                return 1

    # F13: parse --output k=v pairs into a dict.
    # Reject malformed entries so the human gets a clear error.
    parsed_outputs: dict[str, str] = {}
    for kv in raw_outputs:
        if "=" not in kv:
            print(
                f"rv dag approve: --output must be in 'k=v' format, got {kv!r}",
                file=sys.stderr,
            )
            return 1
        k, _, v = kv.partition("=")
        if not k:
            print(
                f"rv dag approve: --output key cannot be empty in {kv!r}",
                file=sys.stderr,
            )
            return 1
        parsed_outputs[k] = v

    # Human-presence check — BEFORE any state write.
    # Covers both approve (→ succeeded) and --reject (→ blocked).
    # Fail-closed: non-TTY + no valid token → return 1, state UNCHANGED.
    #
    # An autonomous-gate node resolved via --auto (coverage-gate /
    # approve-framework / approve-manuscript) is DELIBERATELY exempt — the
    # whole point of the autonomy program is that no human keypress is
    # required at these three gates; the gate-policy engine's disposition
    # (stamped in decision_note above) IS the authorizing decision, and it
    # is itself grounded in mechanical, reproducible gates. approve-protocol
    # (the one retained human gate) is never in _AUTONOMOUS_GATE_IDS, so it
    # always falls through to the human-presence check below.
    if auto and node_id in _AUTONOMOUS_GATE_IDS:
        _method, _approver = "autonomous-gate-policy-engine", "review.autonomy"
    else:
        from .approval import check_human_presence
        from ..adapters.base import EnvSecretStore
        _secrets = EnvSecretStore()
        _ok, _method, _approver, _reason = check_human_presence(args, cfg, _secrets)
        if not _ok:
            print(_reason, file=sys.stderr)
            return 1

    # F13: determine final status (approve → succeeded; reject → blocked).
    final_status = "blocked" if reject else "succeeded"

    run_state.set_node_status(node_id, final_status)

    # F13: persist decision_note and outputs into the node state so they
    # are available to downstream agents and the audit trail.
    ns = run_state.node_states.setdefault(node_id, {})
    if decision_note is not None:
        ns["decision_note"] = decision_note
    if parsed_outputs:
        ns["outputs"] = parsed_outputs

    # Record approval provenance.
    import datetime as _dt
    ns["approved_by"] = _approver
    ns["approval_method"] = _method
    ns["approved_at"] = _dt.datetime.now(tz=_dt.timezone.utc).isoformat(timespec="seconds")

    store.save(run_state)

    if reject:
        note_suffix = f" — {decision_note}" if decision_note else ""
        print(f"Node {node_id!r} REJECTED → blocked{note_suffix}")
    else:
        print(f"Node {node_id!r} approved → succeeded")
    frontier = _recompute_awaiting_go(run_state, manifest, store)
    print("Frontier:")
    _print_frontier(frontier, run_id)
    return 0


# ---------------------------------------------------------------------------
# Verb: add
# ---------------------------------------------------------------------------

def cmd_add(args: argparse.Namespace) -> int:
    """Add a node to an existing run from a JSON patch file."""
    run_id = args.run_id
    patch_path = Path(args.patch).expanduser().resolve()

    if not patch_path.exists():
        print(f"rv dag add: patch file not found: {patch_path}", file=sys.stderr)
        return 1

    try:
        patch_text = patch_path.read_text(encoding="utf-8")
        new_node = json.loads(patch_text)
    except (OSError, json.JSONDecodeError) as e:
        print(f"rv dag add: cannot read patch: {e}", file=sys.stderr)
        return 1

    if not isinstance(new_node, dict) or "id" not in new_node:
        print("rv dag add: patch must be a JSON object with an 'id' field", file=sys.stderr)
        return 1

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv dag add: config error: {e}", file=sys.stderr)
        return 1

    store = RunStore.from_config(cfg)
    try:
        run_state = store.load(run_id)
    except StoreError as e:
        print(f"rv dag add: {e}", file=sys.stderr)
        return 1

    manifest_path = Path(run_state.manifest_path)
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as e:
        print(f"rv dag add: manifest error: {e}", file=sys.stderr)
        return 1

    # Add the node to the manifest
    existing_ids = {n["id"] for n in manifest["nodes"]}
    if new_node["id"] in existing_ids:
        print(
            f"rv dag add: node {new_node['id']!r} already exists in manifest",
            file=sys.stderr,
        )
        return 1

    manifest["nodes"].append(new_node)

    # Validate the updated manifest
    try:
        validate_manifest(manifest)
    except ManifestError as e:
        print(f"rv dag add: updated manifest invalid: {e}", file=sys.stderr)
        return 1

    # Initialize the new node's state
    run_state.init_nodes(manifest)

    # Save the updated manifest and run state
    from .schema import dump_manifest
    try:
        dump_manifest(manifest, manifest_path)
    except OSError as e:
        print(f"rv dag add: cannot write manifest: {e}", file=sys.stderr)
        return 1

    store.save(run_state)

    print(f"Node {new_node['id']!r} added to run {run_id!r}.")
    frontier = _recompute_awaiting_go(run_state, manifest, store)
    print("Frontier:")
    _print_frontier(frontier, run_id)
    return 0


# ---------------------------------------------------------------------------
# Verb: insert
# ---------------------------------------------------------------------------

def cmd_insert(args: argparse.Namespace) -> int:
    """Insert a node after a named node (adds a soft need from after_node_id)."""
    run_id = args.run_id
    patch_path = Path(args.patch).expanduser().resolve()
    after_node_id = args.after

    if not patch_path.exists():
        print(f"rv dag insert: patch file not found: {patch_path}", file=sys.stderr)
        return 1

    try:
        patch_text = patch_path.read_text(encoding="utf-8")
        new_node = json.loads(patch_text)
    except (OSError, json.JSONDecodeError) as e:
        print(f"rv dag insert: cannot read patch: {e}", file=sys.stderr)
        return 1

    if not isinstance(new_node, dict) or "id" not in new_node:
        print("rv dag insert: patch must be a JSON object with an 'id' field", file=sys.stderr)
        return 1

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv dag insert: config error: {e}", file=sys.stderr)
        return 1

    store = RunStore.from_config(cfg)
    try:
        run_state = store.load(run_id)
    except StoreError as e:
        print(f"rv dag insert: {e}", file=sys.stderr)
        return 1

    manifest_path = Path(run_state.manifest_path)
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as e:
        print(f"rv dag insert: manifest error: {e}", file=sys.stderr)
        return 1

    existing_ids = {n["id"] for n in manifest["nodes"]}
    if new_node["id"] in existing_ids:
        print(
            f"rv dag insert: node {new_node['id']!r} already exists in manifest",
            file=sys.stderr,
        )
        return 1

    if after_node_id not in existing_ids:
        print(
            f"rv dag insert: --after node {after_node_id!r} not in manifest",
            file=sys.stderr,
        )
        return 1

    # Wire: add a soft need from after_node_id
    needs = new_node.setdefault("needs", [])
    # Only add the soft edge if it's not already present
    already_linked = any(
        n.get("from") == after_node_id for n in needs
    )
    if not already_linked:
        needs.append({"from": after_node_id, "edge": "soft"})

    manifest["nodes"].append(new_node)

    try:
        validate_manifest(manifest)
    except ManifestError as e:
        print(f"rv dag insert: updated manifest invalid: {e}", file=sys.stderr)
        return 1

    run_state.init_nodes(manifest)

    from .schema import dump_manifest
    try:
        dump_manifest(manifest, manifest_path)
    except OSError as e:
        print(f"rv dag insert: cannot write manifest: {e}", file=sys.stderr)
        return 1

    store.save(run_state)

    print(f"Node {new_node['id']!r} inserted after {after_node_id!r} in run {run_id!r}.")
    frontier = _recompute_awaiting_go(run_state, manifest, store)
    print("Frontier:")
    _print_frontier(frontier, run_id)
    return 0


# ---------------------------------------------------------------------------
# Verb: redo
# ---------------------------------------------------------------------------
#
# The problem this closes: once a cold critic/gate node reaches a terminal
# status (succeeded/failed/blocked), there was NO way to re-run it —
# `rv dag complete` refuses on an already-terminal node (see the "No change"
# short-circuit above), and there's no other verb that reopens a node.
# The "critic BLOCKs -> revise -> re-verify -> approve" loop — the entire
# point of a rejects-only gate — was undrivable in practice.
#
# `rv dag redo <run> <node>` re-opens a completed node back to 'pending' so
# `rv dag tick` re-offers it for dispatch, WITHOUT erasing the prior attempt:
# the full prior node_state snapshot is preserved in
# node_states[node_id]['redo_history'] (append-only, oldest-first) — the
# revise trail matters for audit.
#
# Cascade safety (the load-bearing design): redoing a node whose
# DESCENDANTS have already CONSUMED its output (i.e. are themselves terminal
# — succeeded/failed/blocked) would leave them silently reading a stale
# upstream artifact.  So:
#   - no completed (terminal) descendants -> redo just this node.
#   - completed (terminal) descendants exist -> BLOCK by default, naming
#     them; --cascade resets the whole descendant subtree to pending too
#     (transitively — _transitive_descendants already returns the full
#     subtree, so one cascade pass covers multi-hop staleness).
# An 'awaiting-go' descendant is deliberately NOT in the blocking set: it
# hasn't consumed anything yet (no human has approved it) — a human
# approving it will read whatever the current upstream artifacts say at
# THAT time, which is fresh by construction.  Only a TERMINAL descendant
# (something already ran/decided against the old output) is a real
# staleness risk.

def _transitive_descendants(node_id: str, nodes_lookup: dict[str, dict]) -> set[str]:
    """Return the set of ALL transitive descendant node IDs (not including
    node_id itself).

    The mirror of walker._transitive_upstream: walks FORWARD along the needs
    edges — i.e. every node whose needs chain traces back to node_id, at any
    depth. Deliberately kept HERE (verbs.py), not in walker.py — walker.py is
    a byte-for-byte-pure module (see test_dag_retry.py's
    test_walker_not_modified_by_sr_retry guard); this helper is redo-specific
    orchestration logic, not part of compute_frontier's contract.

    Uses iterative DFS to avoid recursion limits on deep DAGs.
    """
    children: dict[str, list[str]] = {}
    for nid, node in nodes_lookup.items():
        for need in node.get("needs", []):
            children.setdefault(need["from"], []).append(nid)

    descendants: set[str] = set()
    stack = [node_id]
    while stack:
        nid = stack.pop()
        for child_id in children.get(nid, []):
            if child_id not in descendants:
                descendants.add(child_id)
                stack.append(child_id)
    return descendants


def _redo_snapshot_node_state(ns: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of a node_state dict, excluding its own
    'redo_history' key (history entries are flat — no nesting of histories
    inside histories)."""
    return {k: v for k, v in ns.items() if k != "redo_history"}


def _redo_reset_node(
    run_state: RunState,
    node_id: str,
    note: str | None,
) -> None:
    """Reset a single node's state to 'pending', preserving the prior
    attempt (a full snapshot of its previous node_state) in
    node_states[node_id]['redo_history'] (append-only)."""
    ns = run_state.node_states.setdefault(node_id, {"status": "pending"})
    prior_history: list[dict[str, Any]] = list(ns.get("redo_history", []))
    snapshot = _redo_snapshot_node_state(ns)
    snapshot["redone_at"] = time.time()
    if note:
        snapshot["redo_note"] = note
    prior_history.append(snapshot)

    run_state.node_states[node_id] = {
        "status": "pending",
        "started_at": None,
        "completed_at": None,
        "error": None,
        # Retry-state fields — fresh for the new attempt (a redo is a fresh
        # attempt, not a continuation of the auto-retry counter).
        "attempts": 0,
        "last_failure": None,
        "failures": [],
        "redo_history": prior_history,
    }


def cmd_redo(args: argparse.Namespace) -> int:
    """Re-open a completed (succeeded/failed/blocked) node back to 'pending'.

    Drives the critic-BLOCK -> revise -> re-verify -> approve loop: after a
    node reaches a terminal status, `rv dag redo <run> <node>` reopens it so
    `rv dag tick` re-offers it for dispatch. The prior attempt is preserved
    (never erased) in node_states[node_id]['redo_history'].

    --cascade: when the node has descendants that are ALREADY terminal
    (succeeded/failed/blocked — i.e. they already consumed the old output),
    redo BLOCKS by default naming them; pass --cascade to also reset that
    whole descendant subtree to pending.
    """
    run_id = args.run_id
    node_id = args.node_id
    cascade: bool = bool(getattr(args, "cascade", False))
    note: str | None = getattr(args, "note", None) or None

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv dag redo: config error: {e}", file=sys.stderr)
        return 1

    store = RunStore.from_config(cfg)
    try:
        run_state = store.load(run_id)
    except StoreError as e:
        print(f"rv dag redo: {e}", file=sys.stderr)
        return 1

    manifest_path = Path(run_state.manifest_path)
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as e:
        print(f"rv dag redo: manifest error: {e}", file=sys.stderr)
        return 1

    nodes_lookup = manifest_nodes_by_id(manifest)
    if node_id not in nodes_lookup:
        print(f"rv dag redo: node {node_id!r} not in manifest", file=sys.stderr)
        return 1

    current_status = run_state.node_status(node_id)
    if current_status not in TERMINAL_STATUSES:
        print(
            f"rv dag redo: node {node_id!r} is not completed "
            f"(current status: {current_status!r}). Only a terminal "
            f"({sorted(TERMINAL_STATUSES)}) node can be redone.",
            file=sys.stderr,
        )
        return 1

    descendants = _transitive_descendants(node_id, nodes_lookup)
    completed_descendants = sorted(
        d for d in descendants if run_state.node_status(d) in TERMINAL_STATUSES
    )

    if completed_descendants and not cascade:
        print(
            f"rv dag redo: node {node_id!r} has completed descendants "
            f"{completed_descendants} that would read a stale output; "
            "re-run with --cascade to reset them too.",
            file=sys.stderr,
        )
        return 1

    _redo_reset_node(run_state, node_id, note)
    reset_ids = [node_id]
    if cascade and completed_descendants:
        for d in completed_descendants:
            _redo_reset_node(run_state, d, note)
            reset_ids.append(d)

    store.save(run_state)

    print(f"Node {node_id!r} redone -> pending (prior attempt preserved in redo_history).")
    if cascade and completed_descendants:
        print(f"  --cascade: also reset descendant subtree: {', '.join(completed_descendants)}")

    frontier = _recompute_awaiting_go(run_state, manifest, store)
    print("Frontier:")
    _print_frontier(frontier, run_id, node_states=run_state.node_states)
    return 0


def cmd_templates(args: argparse.Namespace) -> int:
    """Print the built-in loop catalog — discovery entry for all four research loops.

    Pure read, no config needed.
    """
    from .catalog import LOOP_CATALOG

    for entry in LOOP_CATALOG:
        print(f"Loop: {entry.key}")
        print(f"  scaffolder : {entry.scaffolder or '(none — manifest authored manually)'}")
        print(f"  entry verb : {entry.entry_verb}")
        has_scaffolder = entry.scaffolder is not None
        print(f"  scaffolder exists: {'yes' if has_scaffolder else 'no'}")
        if entry.human_go_gates:
            genuine = [g for g in entry.human_go_gates if not g.autonomous]
            autonomous = [g for g in entry.human_go_gates if g.autonomous]
            # The count reflects GENUINE human-keypress gates only — an
            # autonomous gate (resolved by review.autonomy's gate-policy
            # engine, no human keypress) must never inflate this number;
            # doing so would contradict the very next line, which marks
            # that same gate autonomous.
            suffix = f" + {len(autonomous)} autonomous" if autonomous else ""
            print(f"  human-go gates ({len(genuine)}{suffix}):")
            for g in entry.human_go_gates:
                marker = " [AUTONOMOUS — resolves without a human keypress]" if g.autonomous else ""
                print(f"    [{g.node_id}]{marker} {g.label}")
                if g.freeze_action:
                    print(f"      freeze: {g.freeze_action}")
        else:
            print("  human-go gates: (none)")
        print(f"  topology: {entry.topology_summary}")
        print()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Print a formatted status table for the run.

    IMPORTANT: prints the exact `dag approve <run_id> <node_id>` command
    for any awaiting-go node so the human sees exactly what to run.
    """
    run_id = args.run_id

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv dag status: config error: {e}", file=sys.stderr)
        return 1

    store = RunStore.from_config(cfg)
    try:
        run_state = store.load(run_id)
    except StoreError as e:
        print(f"rv dag status: {e}", file=sys.stderr)
        return 1

    manifest_path = Path(run_state.manifest_path)
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as e:
        print(f"rv dag status: manifest error: {e}", file=sys.stderr)
        return 1

    _print_manifest_warns(manifest)
    print(f"Run: {run_id}")
    print(f"  manifest: {manifest_path}")
    import datetime as _dt
    created = _dt.datetime.fromtimestamp(run_state.created_at).isoformat(timespec="seconds")
    print(f"  created: {created}")
    print()
    print("Nodes:")
    for node in manifest["nodes"]:
        nid = node["id"]
        label = node.get("label", nid)
        status = run_state.node_status(nid)
        sym = _sym(status)
        ns = run_state.node_states.get(nid, {})
        err = ns.get("error", "")
        err_str = f" [{err}]" if err else ""
        # Show attempt progress only on genuinely retry-queued (pending) nodes.
        # Terminal nodes (failed/succeeded) must NOT show a live attempt counter — it
        # overshoots (e.g. "[attempt 2/1]" for N=0, "[attempt 4/3]" for exhausted N=2).
        attempts = ns.get("attempts", 0)
        max_retries = node.get("max_retries", 0)
        retry_str = (
            f" [attempt {attempts + 1}/{max_retries + 1}]"
            if status == "pending" and attempts > 0
            else ""
        )
        print(f"  {sym} {nid}  ({status}){err_str}{retry_str}")
        if node.get("type") != "human-go" and label != nid:
            print(f"      {label}")
        # Show approval provenance for decided human-go nodes.
        if node.get("type") == "human-go" and status in ("succeeded", "blocked"):
            _by = ns.get("approved_by", "")
            _meth = ns.get("approval_method", "")
            _at = ns.get("approved_at", "")
            if _by or _meth:
                _prov = f"      approved_by={_by!r} method={_meth!r}"
                if _at:
                    _prov += f" at={_at}"
                print(_prov)
        # For pending nodes with prior failures, print last_failure
        if status == "pending" and attempts > 0:
            last_failure = ns.get("last_failure")
            if last_failure:
                print(f"      PRIOR FAILURE: {last_failure[:200]}"
                      + ("..." if len(last_failure) > 200 else ""))

    # Show current frontier
    # F6: include awaiting-go nodes that have already been promoted by a prior
    # _recompute_awaiting_go call (from dag run/tick/complete).  compute_frontier
    # skips them because "awaiting-go" is in _NON_ADVANCEABLE — so they would
    # silently disappear from the status display even though they still need human
    # action.  We append them explicitly so dag status and dag complete agree.
    print()
    print("Current frontier:")
    frontier = compute_frontier(
        manifest,
        run_state.node_states,
        run_state.edge_registered_ts,
        manifest_global_cap(manifest),
    )
    _frontier_ids = {item.node_id for item in frontier}
    _nodes_by_id = manifest_nodes_by_id(manifest)
    _extra_await: list[FrontierNode] = [
        FrontierNode(node_id=nid, action="await-go", node=_nodes_by_id[nid])
        for nid, ns in run_state.node_states.items()
        if ns.get("status") == "awaiting-go"
        and nid not in _frontier_ids
        and nid in _nodes_by_id
    ]
    frontier = frontier + _extra_await
    _print_frontier(frontier, run_id, node_states=run_state.node_states)

    return 0


# ---------------------------------------------------------------------------
# Verb: brief
# ---------------------------------------------------------------------------

def cmd_brief(args: argparse.Namespace) -> int:
    """Emit a deterministic crew dispatch brief for a DAG agent node.

    Replaces hand-written dispatch briefs: the brief is a pure function of
    (node, run_state, cfg) — same inputs → byte-identical output.

    EMIT, DON'T HAND-ROLL:
      rv dag brief <run_id> <node_id>
    The output is the brief to pass verbatim to the dispatched crew subagent.
    Never hand-transcribe a node's spec/reads into a brief — that is the
    anti-pattern this verb exists to prevent.
    """
    run_id = args.run_id
    node_id = args.node_id

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv dag brief: config error: {e}", file=sys.stderr)
        return 1

    store = RunStore.from_config(cfg)
    try:
        run_state = store.load(run_id)
    except StoreError as e:
        print(f"rv dag brief: {e}", file=sys.stderr)
        return 1

    manifest_path = Path(run_state.manifest_path)
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as e:
        print(f"rv dag brief: manifest error: {e}", file=sys.stderr)
        return 1

    nodes_lookup = manifest_nodes_by_id(manifest)
    if node_id not in nodes_lookup:
        print(f"rv dag brief: node {node_id!r} not in manifest", file=sys.stderr)
        return 1

    node = nodes_lookup[node_id]
    node_type = node.get("type", "agent")
    if node_type == "human-go":
        print(
            f"rv dag brief: node {node_id!r} is a human-go gate — "
            "briefs are for agent nodes only. "
            "Use `rv dag approve <run_id> <node_id>` to advance this gate.",
            file=sys.stderr,
        )
        return 1
    if node_type == "tool":
        print(
            f"rv dag brief: node {node_id!r} is a tool (deterministic-op) node — "
            "briefs are for agent nodes only; tool nodes are executed "
            "IN-PROCESS by the runner (D4, verb consolidation), never "
            "dispatched to a crew agent. It auto-executes when the "
            "run/tick frontier reaches it.",
            file=sys.stderr,
        )
        return 1

    node_state = run_state.node_states.get(node_id, {})

    # Detect the manifest-level project slug for produces-path resolution
    manifest_project: str | None = manifest.get("project")

    from .brief import build_brief
    brief = build_brief(
        node=node,
        node_state=node_state,
        cfg=cfg,
        run_id=run_id,
        project_root=manifest_path.parent,
        manifest_project=manifest_project,
    )
    print(brief, end="")
    return 0


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``dag`` verb.

    When to use: ``rv dag run/tick/complete/approve/add/insert/redo/status``
    to orchestrate a multi-node research DAG. human-go nodes are the decision
    gate; afterok+watch edges gate on artifact freshness (OKF note type-dir check).
    """
    desc = (
        "Orchestrate a multi-node research DAG. "
        "Nodes: agent (dispatchable) | human-go (decision gate). "
        "Edges: afterok | after | afterany | soft. "
        "The human-go node requires all transitive upstream to be terminal before approval."
    )
    if parent is not None:
        p = parent.add_parser(
            "dag",
            help="Orchestrate a multi-node research DAG.",
            description=desc,
        )
    else:
        p = argparse.ArgumentParser(prog="rv dag", description=desc)

    sub = p.add_subparsers(dest="dag_cmd", required=True)

    # run
    run_p = sub.add_parser("run", help="Start a new DAG run from a manifest JSON.")
    run_p.add_argument("manifest", help="Path to the DAG manifest JSON file.")

    # tick
    tick_p = sub.add_parser("tick", help="Re-compute the frontier for an existing run.")
    tick_p.add_argument("run_id", help="The run_id to tick.")

    # complete
    comp_p = sub.add_parser("complete", help="Mark a node complete.")
    comp_p.add_argument("run_id", help="The run_id.")
    comp_p.add_argument("node_id", help="The node id to complete.")
    comp_p.add_argument(
        "--status",
        choices=["succeeded", "failed", "blocked"],
        default="succeeded",
        help="Completion status (default: succeeded).",
    )
    # Failure capture for diagnose-before-retry (D-RETRY-9)
    comp_p.add_argument(
        "--error",
        metavar="SUMMARY",
        default=None,
        help=(
            "Short failure summary. "
            "REQUIRED when --status failed and the node has max_retries > 0 (D-RETRY-9). "
            "Persisted to node_states for diagnose-before-retry augmentation. "
            "Optional when max_retries == 0 (still recorded for the human diagnostician)."
        ),
    )
    comp_p.add_argument(
        "--error-file",
        metavar="PATH",
        default=None,
        help=(
            "Path to a file whose content is used as the failure summary. "
            "Use for multi-line error output (stack traces, logs). "
            "Content is truncated to 4000 chars. Mutually supplements --error; "
            "if both supplied, --error-file takes precedence."
        ),
    )

    # approve  (F13: --note / --output / --reject)
    app_p = sub.add_parser(
        "approve",
        help="Approve (or reject) a human-go node.",
    )
    app_p.add_argument("run_id", help="The run_id.")
    app_p.add_argument("node_id", help="The human-go node id to approve.")
    app_p.add_argument(
        "--note",
        metavar="TEXT",
        default=None,
        help=(
            "Decision rationale (stored in node_states for the audit trail). "
            "Use for recording why you approved or rejected this gate."
        ),
    )
    app_p.add_argument(
        "--output",
        metavar="k=v",
        action="append",
        default=None,
        help=(
            "Decision output key=value pair (repeatable, e.g. --output tier=A --output n=50). "
            "Stored in node_states['outputs']; downstream human-go-conditional nodes read "
            "these to branch the experiment loop."
        ),
    )
    app_p.add_argument(
        "--reject",
        action="store_true",
        default=False,
        help=(
            "Reject (block) this gate instead of approving it. "
            "Moves the node to 'blocked' (terminal) — downstream nodes that "
            "depend on this gate via afterok will NOT advance."
        ),
    )
    app_p.add_argument(
        "--yes",
        action="store_true",
        default=False,
        help=(
            "Skip the confirmation keystroke when a TTY is present. "
            "Has NO EFFECT when stdin is not a TTY — the gate still fails closed "
            "(use a provisioned token for non-interactive approval instead)."
        ),
    )
    app_p.add_argument(
        "--auto",
        action="store_true",
        default=False,
        help=(
            "Resolve this gate via the gate-policy engine "
            "(review/autonomy.py) instead of a human keypress. Only valid on "
            "coverage-gate / approve-framework / approve-manuscript / "
            "approve-review — the four autonomous gates. "
            "approve-protocol is NEVER eligible (the one retained human "
            "gate) and ignores --auto. "
            "GO/GO-WITH-RESIDUE -> approved; HALT-DECLARE -> rejected with "
            "the NOT-CLEARED reason recorded; REVISE -> exit 2, no state "
            "change (dispatch a bounded auto-revise round first)."
        ),
    )

    # add
    add_p = sub.add_parser("add", help="Add a node from a JSON patch file.")
    add_p.add_argument("run_id", help="The run_id.")
    add_p.add_argument("patch", help="Path to a JSON file containing the new node dict.")

    # insert
    ins_p = sub.add_parser(
        "insert",
        help="Insert a node after a named node (soft edge).",
    )
    ins_p.add_argument("run_id", help="The run_id.")
    ins_p.add_argument("patch", help="Path to a JSON file containing the new node dict.")
    ins_p.add_argument("--after", required=True, help="Insert after this node id.")

    # redo
    redo_p = sub.add_parser(
        "redo",
        help="Re-open a completed (succeeded/failed/blocked) node back to 'pending'.",
        description=(
            "Drives the critic-BLOCK -> revise -> re-verify -> approve loop: "
            "reopens a terminal node so `rv dag tick` re-offers it for dispatch. "
            "The prior attempt is preserved (never erased) in "
            "node_states[node_id]['redo_history']. If the node has descendants "
            "that already consumed its output (themselves terminal), redo BLOCKS "
            "by default naming them -- pass --cascade to reset that subtree too."
        ),
    )
    redo_p.add_argument("run_id", help="The run_id.")
    redo_p.add_argument("node_id", help="The completed node id to redo.")
    redo_p.add_argument(
        "--cascade",
        action="store_true",
        default=False,
        help=(
            "Also reset the node's already-terminal (succeeded/failed/blocked) "
            "descendant subtree to pending. Without this flag, redo BLOCKS "
            "when such descendants exist (they would silently read a stale "
            "output otherwise)."
        ),
    )
    redo_p.add_argument(
        "--note",
        metavar="TEXT",
        default=None,
        help="Rationale for the redo, recorded in the preserved history entry.",
    )

    # status
    stat_p = sub.add_parser("status", help="Print the current run status.")
    stat_p.add_argument("run_id", help="The run_id.")

    # templates  (discovery entry for all four research loops)
    sub.add_parser(
        "templates",
        help=(
            "Print the built-in loop catalog: all four research loops with their "
            "scaffolder verb, entry command, and human-go gate locations."
        ),
    )

    # brief  (deterministic crew dispatch brief emitter)
    brief_p = sub.add_parser(
        "brief",
        help=(
            "Emit a deterministic crew dispatch brief for a DAG agent node. "
            "EMIT, DON'T HAND-ROLL: never hand-transcribe a node's spec/reads "
            "into a brief — use this verb."
        ),
    )
    brief_p.add_argument("run_id", help="The run_id.")
    brief_p.add_argument("node_id", help="The agent node id to brief.")

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch dag subcommands. Returns exit code."""
    cmd_map = {
        "run": cmd_run,
        "tick": cmd_tick,
        "complete": cmd_complete,
        "approve": cmd_approve,
        "add": cmd_add,
        "insert": cmd_insert,
        "redo": cmd_redo,
        "status": cmd_status,
        "templates": cmd_templates,
        "brief": cmd_brief,
    }
    dag_cmd = getattr(args, "dag_cmd", None)
    fn = cmd_map.get(dag_cmd)
    if fn is None:
        print(f"rv dag: unknown subcommand {dag_cmd!r}", file=sys.stderr)
        return 1
    return fn(args)
