# SPDX-License-Identifier: AGPL-3.0-or-later
"""walker.py — pure DAG frontier computation for Research Vault.

THE CORE GUARANTEE: compute_frontier is PURE.
  Same (manifest, node_states, edge_registered_ts, global_cap) inputs
  → same FrontierNode list output.
  No I/O, no side effects, no mutations.

Frontier items:
  FrontierNode(node_id, action="dispatch", node=...)
    — an agent node whose all incoming edges are satisfied; ready to dispatch.
  FrontierNode(node_id, action="await-go", node=...)
    — a human-go node whose ALL TRANSITIVE UPSTREAM nodes are terminal;
      ready for human approval via `dag approve`.

THE TRANSITIVE-UPSTREAM INVARIANT:
  A human-go node MUST NOT appear in the frontier (as "await-go") until every
  transitive ancestor is in a terminal state (succeeded/failed/blocked).

  The prior defect: the walker checked only the direct incoming edge, not the
  full transitive upstream set. A human-go node became approvable while upstream
  nodes were still dispatched/pending. Approving it released the downstream,
  leaving the run-state showing un-finished upstream UNDER a succeeded
  downstream — the graph lied about its own state.

  Fix: _transitive_upstream() computes the full ancestor set. The human-go
  readiness check iterates ALL ancestors and requires every one to be terminal.

NO LIVENESS NET: this module NEVER imports pollers, drain, launchd, or any
background-scheduler module. Afterok watch expressions are resolved INLINE
(synchronously) via resolve_watch from the wait_for module. Unsatisfied
external watches → the documented shell pattern:
  wait-for <cond> --then 'rv dag tick <run_id>' &

Stdlib only (plus the intra-package resolve_watch import).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..wait_for import resolve_watch

# ---------------------------------------------------------------------------
# Status sets
# ---------------------------------------------------------------------------

TERMINAL_STATUSES: frozenset[str] = frozenset({"succeeded", "failed", "blocked"})

# Statuses that cannot advance further in the frontier — already done or in-flight.
_NON_ADVANCEABLE: frozenset[str] = frozenset({
    "succeeded",
    "failed",
    "blocked",
    "dispatched",
    "running",
    "awaiting-go",
})


# ---------------------------------------------------------------------------
# Frontier item
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FrontierNode:
    """A node ready to advance, returned by compute_frontier.

    Attributes:
      node_id  — the node's id string
      action   — "dispatch" (send to agent) | "await-go" (waiting for human approval)
      node     — the raw node dict from the manifest
    """
    node_id: str
    action: str   # "dispatch" | "await-go"
    node: dict    # the manifest node dict (frozen by reference)


# ---------------------------------------------------------------------------
# Transitive upstream
# ---------------------------------------------------------------------------

def _transitive_upstream(node_id: str, nodes_lookup: dict[str, dict]) -> set[str]:
    """Return the set of ALL transitive ancestor node IDs (not including node_id itself).

    Uses iterative DFS to avoid recursion limits on deep DAGs.
    """
    ancestors: set[str] = set()
    stack = [node_id]
    while stack:
        nid = stack.pop()
        node = nodes_lookup.get(nid)
        if node is None:
            continue
        for need in node.get("needs", []):
            from_id = need["from"]
            if from_id not in ancestors:
                ancestors.add(from_id)
                stack.append(from_id)
    return ancestors


def _all_transitive_upstream_terminal(
    node_id: str,
    nodes_lookup: dict[str, dict],
    node_states: dict[str, dict],
) -> bool:
    """Return True iff every transitive ancestor of node_id is in a terminal status.

    This is the invariant check that prevents a human-go node from becoming
    approvable while any upstream work is still in-flight or pending.
    """
    ancestors = _transitive_upstream(node_id, nodes_lookup)
    for anc_id in ancestors:
        anc_status = node_states.get(anc_id, {}).get("status", "pending")
        if anc_status not in TERMINAL_STATUSES:
            return False
    return True


# ---------------------------------------------------------------------------
# Edge satisfaction
# ---------------------------------------------------------------------------

def _edge_satisfied(
    need: dict,
    to_id: str,
    need_index: int,
    node_states: dict[str, dict],
    edge_registered_ts: dict[str, float],
) -> bool:
    """Return True iff a single need/edge is satisfied given the current node_states.

    For afterok edges with a watch expression, the watch is resolved INLINE
    (synchronously) using resolve_watch. This is the in-session
    resolution contract: satisfied watches resolve immediately; unsatisfied
    external watches remain False until the next dag tick.

    Edge key for registered_ts lookup: "{to_id}:{from_id}:{need_index}"
    """
    from_id = need["from"]
    edge_kind = need.get("edge", "afterok")
    from_status = node_states.get(from_id, {}).get("status", "pending")

    if edge_kind == "afterok":
        if from_status != "succeeded":
            return False
        # Optional watch expression — gated artifact check
        watch = need.get("watch")
        if watch:
            edge_key = f"{to_id}:{from_id}:{need_index}"
            reg_ts = edge_registered_ts.get(edge_key)
            result = resolve_watch(watch, registered_ts=reg_ts)
            if not result["ready"]:
                return False
        return True

    elif edge_kind == "after":
        return from_status in TERMINAL_STATUSES

    elif edge_kind == "afterany":
        # Satisfied when the predecessor has reached any terminal state.
        # For multi-predecessor afterany groups, each need is checked independently;
        # the group logic (any-one-of-many) is achieved by the caller treating
        # afterany edges as a group where at least one satisfied edge suffices.
        # In this implementation, each afterany need is treated as an independent
        # gate — a node with two afterany needs will advance when EITHER predecessor
        # is terminal (since we break on first satisfaction at the node level).
        # See compute_frontier's afterany group handling below.
        return from_status in TERMINAL_STATUSES

    elif edge_kind == "soft":
        # Advisory — never blocks.
        return True

    # Unknown edge kind (should not reach here after manifest validation)
    return False


# ---------------------------------------------------------------------------
# Core: compute_frontier
# ---------------------------------------------------------------------------

def compute_frontier(
    manifest: dict[str, Any],
    node_states: dict[str, dict],
    edge_registered_ts: dict[str, float],
    global_cap: int,
) -> list[FrontierNode]:
    """Compute the frontier: nodes ready to advance (dispatch or await-go).

    PURE FUNCTION — same inputs → same output. No I/O, no mutation.

    Args:
      manifest           — validated manifest dict (from schema.load_manifest)
      node_states        — {node_id: {"status": str, ...}} current run state
      edge_registered_ts — {edge_key: float} per-edge registration timestamps
                           for artifact+fresh watch resolution
      global_cap         — max concurrent dispatched+running agents

    Returns:
      List of FrontierNode in manifest order (action="dispatch" or "await-go").
      dispatch items are capped at global_cap - currently_active.

    Transitive-upstream invariant:
      A human-go node appears as "await-go" ONLY when EVERY transitive ancestor
      is terminal. This is checked by _all_transitive_upstream_terminal().

    afterok+watch resolution:
      Resolved INLINE via resolve_watch. No background poller.
    """
    nodes_lookup: dict[str, dict] = {n["id"]: n for n in manifest["nodes"]}

    # Count currently active (dispatched + running) agent nodes toward the cap.
    active_count = sum(
        1
        for st in node_states.values()
        if st.get("status") in ("dispatched", "running")
    )
    dispatch_slots = max(0, global_cap - active_count)

    frontier: list[FrontierNode] = []
    dispatch_count = 0  # newly added dispatch items in this frontier

    for node in manifest["nodes"]:
        nid = node["id"]
        status = node_states.get(nid, {}).get("status", "pending")

        if status in _NON_ADVANCEABLE:
            continue

        # Only pending nodes are candidates for advancement.
        node_type = node.get("type", "agent")
        needs = node.get("needs", [])

        if node_type == "human-go":
            # THE INVARIANT: all transitive upstream must be terminal.
            if _all_transitive_upstream_terminal(nid, nodes_lookup, node_states):
                frontier.append(FrontierNode(node_id=nid, action="await-go", node=node))
            # If not all upstream terminal: NOT in frontier at all.
            continue

        # ── Agent node: check all needs ──────────────────────────────────────
        # Group afterany needs together: a set of afterany edges is satisfied
        # if AT LEAST ONE of them is satisfied.
        afterany_groups: dict[str, list[int]] = {}  # from_id -> [need_indices]
        required_needs: list[tuple[int, dict]] = []  # (index, need) for non-afterany

        for idx, need in enumerate(needs):
            edge_kind = need.get("edge", "afterok")
            if edge_kind == "afterany":
                from_id = need["from"]
                afterany_groups.setdefault(from_id, []).append(idx)
            else:
                required_needs.append((idx, need))

        ready = True

        # Check all non-afterany needs (afterok, after, soft)
        for idx, need in required_needs:
            if not _edge_satisfied(need, nid, idx, node_states, edge_registered_ts):
                ready = False
                break

        if ready and afterany_groups:
            # For afterany: satisfied if at least one predecessor in the group is terminal
            any_satisfied = any(
                node_states.get(from_id, {}).get("status", "pending") in TERMINAL_STATUSES
                for from_id in afterany_groups
            )
            if not any_satisfied:
                ready = False

        if ready:
            if dispatch_count < dispatch_slots:
                frontier.append(FrontierNode(node_id=nid, action="dispatch", node=node))
                dispatch_count += 1
            # If cap is reached, don't add more dispatch items but continue scanning
            # so await-go items (which don't count toward cap) are still included.

    return frontier
