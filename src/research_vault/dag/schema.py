"""schema.py — DAG manifest schema for Research Vault (SR-3 + SR-DISP).

JSON manifest format:
  {
    "run_id": "loop-q1",
    "name": "Research loop Q1",        # optional display name
    "global_cap": 4,                   # max concurrent dispatched agents (default 4)
    "nodes": [
      {
        "id": "lit-search",
        "type": "agent",               # "agent" | "human-go"
        "label": "Literature search",  # optional display label
        "spec": "task://research#lit-search",  # REQUIRED on agent nodes (SR-DISP)
        "continues": {                 # OPTIONAL — explicit resume exception (SR-DISP)
          "node": "prior-agent-id",    # must be a transitive-upstream agent ancestor
          "reason": "tight iterative continuation — one-step refinement, no artifact boundary"
        },
        "produces": {                  # optional — declares artifact this node creates
          "note": "experiments/exp-001.md"   # OKF note path, relative to notes_root
        },
        "needs": [
          {"from": "prev-node", "edge": "afterok"},
          {
            "from": "prev-node",
            "edge": "afterok",
            "watch": "artifact:/abs/path/to/file.md+fresh"
          }
        ]
      }
    ]
  }

Edge kinds:
  afterok   — predecessor status == succeeded; optionally gated by a watch expression
              that resolves via resolve_watch (SR-2's artifact:/sacct:/etc. grammar).
  after     — predecessor has reached any terminal state (succeeded/failed/blocked).
  afterany  — at least one predecessor in a group has reached any terminal state.
  soft      — advisory; never blocks the downstream node from advancing.

Validation rules (SR-3):
  - run_id and nodes are required at the top level.
  - Node IDs must be unique strings.
  - All needs.from values must resolve to a known node ID.
  - A node cannot list itself in needs.from (no self-references).
  - The graph must be acyclic (checked via Kahn's topological sort).
  - node type must be "agent" or "human-go".
  - edge kind must be one of the four above.

SR-DISP additions (schema-by-construction dispatch discipline):
  Agent nodes:
  - spec REQUIRED (non-empty string): points to the durable brief for this dispatch.
    Absence is a ManifestError — fresh-by-default enforced by construction.
  - continues OPTIONAL = {node, reason}:
    - continues.node must be a string that names an existing agent node
    - continues.node must be a transitive-upstream ancestor of this node
    - continues.node must not equal this node's own id
    - continues.reason must be a non-empty string (forces articulation of the judgment)
    Any violation is a ManifestError.

  human-go nodes are EXEMPT from spec/continues requirements (they are decision gates,
  not dispatch targets).

Non-fatal WARNs (manifest_warns):
  A continues path crossing a produces: or human-go node between the continued ancestor
  and the current node → structural boundary-smell WARN. Non-fatal: the manifest is still
  valid, but the external runtime should prefer a fresh dispatch pointed at the artifact.

Stdlib only (plus intra-package walker import for _transitive_upstream reuse — no circularity).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

NODE_TYPES: frozenset[str] = frozenset({"agent", "human-go"})
EDGE_KINDS: frozenset[str] = frozenset({"afterok", "after", "afterany", "soft"})
REQUIRED_MANIFEST_FIELDS: frozenset[str] = frozenset({"run_id", "nodes"})

DEFAULT_GLOBAL_CAP = 4
DEFAULT_NODE_TYPE = "agent"
DEFAULT_EDGE_KIND = "afterok"


class ManifestError(ValueError):
    """Raised when a manifest fails structural or semantic validation."""


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_manifest(path: Path) -> dict[str, Any]:
    """Load and validate a DAG manifest from a JSON file.

    Returns the parsed manifest dict on success.
    Raises ManifestError on any structural or semantic violation.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ManifestError(f"Cannot read manifest {path}: {e}") from e
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ManifestError(f"Invalid JSON in {path}: {e}") from e
    if not isinstance(data, dict):
        raise ManifestError(f"Manifest must be a JSON object, got {type(data).__name__}")
    validate_manifest(data)
    return data


def dump_manifest(manifest: dict[str, Any], path: Path) -> None:
    """Write a manifest dict to a JSON file (pretty-printed, atomic write)."""
    text = json.dumps(manifest, indent=2, ensure_ascii=False)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_manifest(manifest: dict[str, Any]) -> None:
    """Validate a manifest dict in-memory.

    Raises ManifestError describing the first violation found.
    Call this after loading from JSON, or before writing a new manifest.

    SR-DISP: validates spec/continues on agent nodes (see module docstring).
    """
    if not isinstance(manifest, dict):
        raise ManifestError("Manifest must be a dict")

    # Required top-level fields
    for field in REQUIRED_MANIFEST_FIELDS:
        if field not in manifest:
            raise ManifestError(f"Manifest missing required field {field!r}")

    run_id = manifest["run_id"]
    if not run_id or not isinstance(run_id, str):
        raise ManifestError("'run_id' must be a non-empty string")

    nodes = manifest["nodes"]
    if not isinstance(nodes, list):
        raise ManifestError("'nodes' must be a list")
    if not nodes:
        raise ManifestError("'nodes' must contain at least one node")

    cap = manifest.get("global_cap", DEFAULT_GLOBAL_CAP)
    if not isinstance(cap, int) or cap < 1:
        raise ManifestError(f"'global_cap' must be a positive integer, got {cap!r}")

    # ── Validate each node and collect IDs ───────────────────────────────────
    nodes_by_id: dict[str, dict] = {}
    for i, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise ManifestError(f"nodes[{i}]: must be a dict, got {type(node).__name__}")

        nid = node.get("id")
        if not nid or not isinstance(nid, str):
            raise ManifestError(f"nodes[{i}]: missing or invalid 'id'")
        if nid in nodes_by_id:
            raise ManifestError(f"Duplicate node id: {nid!r}")
        nodes_by_id[nid] = node

        node_type = node.get("type", DEFAULT_NODE_TYPE)
        if node_type not in NODE_TYPES:
            raise ManifestError(
                f"Node {nid!r}: unknown type {node_type!r}. Valid: {sorted(NODE_TYPES)}"
            )

        # ── SR-DISP: spec and continues validation for agent nodes ────────────
        if node_type == "agent":
            _validate_agent_spec(nid, node)
            _validate_continues_structure(nid, node)

        # Validate produces (optional)
        produces = node.get("produces")
        if produces is not None:
            if not isinstance(produces, dict):
                raise ManifestError(f"Node {nid!r}: 'produces' must be a dict")
            if "note" in produces:
                note_path = produces["note"]
                if not isinstance(note_path, str) or not note_path.strip():
                    raise ManifestError(
                        f"Node {nid!r}: produces.note must be a non-empty string"
                    )

        # Validate needs list
        needs = node.get("needs", [])
        if not isinstance(needs, list):
            raise ManifestError(f"Node {nid!r}: 'needs' must be a list")

        for j, need in enumerate(needs):
            if not isinstance(need, dict):
                raise ManifestError(
                    f"Node {nid!r}: needs[{j}] must be a dict, got {type(need).__name__}"
                )
            from_id = need.get("from")
            if not from_id or not isinstance(from_id, str):
                raise ManifestError(f"Node {nid!r}: needs[{j}] missing 'from'")
            if from_id == nid:
                raise ManifestError(f"Node {nid!r}: self-reference in needs[{j}]")

            edge_kind = need.get("edge", DEFAULT_EDGE_KIND)
            if edge_kind not in EDGE_KINDS:
                raise ManifestError(
                    f"Node {nid!r}: needs[{j}]: unknown edge kind {edge_kind!r}. "
                    f"Valid: {sorted(EDGE_KINDS)}"
                )

    # ── Validate all needs.from references resolve ────────────────────────────
    for node in nodes:
        nid = node["id"]
        for j, need in enumerate(node.get("needs", [])):
            from_id = need["from"]
            if from_id not in nodes_by_id:
                raise ManifestError(
                    f"Node {nid!r}: needs[{j}].from {from_id!r} is not a known node id"
                )

    # ── Acyclicity check via Kahn's topological sort ──────────────────────────
    _assert_acyclic(nodes, nodes_by_id)

    # ── SR-DISP: continues cross-node validation (post-acyclicity) ────────────
    # Requires: all nodes known, graph acyclic, needs.from refs resolved.
    for node in nodes:
        nid = node["id"]
        node_type = node.get("type", DEFAULT_NODE_TYPE)
        if node_type != "agent":
            continue
        continues = node.get("continues")
        if continues is None:
            continue
        _validate_continues_cross_node(nid, node, nodes_by_id)


def _validate_agent_spec(nid: str, node: dict) -> None:
    """Validate that an agent node has a non-empty spec field.

    Raises ManifestError if spec is missing or empty.
    """
    spec = node.get("spec")
    if spec is None:
        raise ManifestError(
            f"Node {nid!r}: agent nodes require a 'spec' field pointing to the durable brief "
            f"(SR-DISP: fresh-by-default enforcement). "
            f"Example: \"spec\": \"task://research#lit-search\""
        )
    if not isinstance(spec, str) or not spec.strip():
        raise ManifestError(
            f"Node {nid!r}: 'spec' must be a non-empty string, got {spec!r}"
        )


def _validate_continues_structure(nid: str, node: dict) -> None:
    """Validate the structural shape of a continues field (if present).

    Checks: is a dict, has 'node' (string), has 'reason' (non-empty string).
    Cross-node checks (ancestor resolution) are done in _validate_continues_cross_node.

    Raises ManifestError on any violation.
    """
    continues = node.get("continues")
    if continues is None:
        return

    if not isinstance(continues, dict):
        raise ManifestError(
            f"Node {nid!r}: 'continues' must be a dict with 'node' and 'reason' fields, "
            f"got {type(continues).__name__}"
        )

    cont_node = continues.get("node")
    if cont_node is None:
        raise ManifestError(
            f"Node {nid!r}: 'continues' missing 'node' field"
        )
    if not isinstance(cont_node, str):
        raise ManifestError(
            f"Node {nid!r}: 'continues.node' must be a string, got {type(cont_node).__name__}"
        )

    reason = continues.get("reason")
    if reason is None:
        raise ManifestError(
            f"Node {nid!r}: 'continues' missing 'reason' field — "
            f"the justification for a resume is required by construction (SR-DISP)"
        )
    if not isinstance(reason, str) or not reason.strip():
        raise ManifestError(
            f"Node {nid!r}: 'continues.reason' must be a non-empty string — "
            f"articulate why this is a tight iteration, not a fresh dispatch"
        )


def _validate_continues_cross_node(
    nid: str,
    node: dict,
    nodes_by_id: dict[str, dict],
) -> None:
    """Validate continues.node cross-node constraints.

    Checks:
    1. continues.node exists in the manifest
    2. continues.node is type: agent
    3. continues.node is not self
    4. continues.node is a transitive-upstream ancestor of nid

    Raises ManifestError on any violation.
    """
    from .walker import _transitive_upstream  # no circular dep: walker doesn't import schema

    continues = node["continues"]
    cont_node_id = continues["node"]  # already validated as a string

    # 1. Must exist in manifest
    if cont_node_id not in nodes_by_id:
        raise ManifestError(
            f"Node {nid!r}: continues.node {cont_node_id!r} is not a known node id"
        )

    # 2. Must be type: agent
    cont_node = nodes_by_id[cont_node_id]
    cont_type = cont_node.get("type", DEFAULT_NODE_TYPE)
    if cont_type != "agent":
        raise ManifestError(
            f"Node {nid!r}: continues.node {cont_node_id!r} is type {cont_type!r}, "
            f"not 'agent' — can only continue an agent thread (SR-DISP)"
        )

    # 3. Must not be self
    if cont_node_id == nid:
        raise ManifestError(
            f"Node {nid!r}: continues.node must not be the node itself (self-continuation)"
        )

    # 4. Must be a transitive-upstream ancestor
    ancestors = _transitive_upstream(nid, nodes_by_id)
    if cont_node_id not in ancestors:
        raise ManifestError(
            f"Node {nid!r}: continues.node {cont_node_id!r} is not a transitive-upstream "
            f"ancestor of {nid!r} — can only continue a thread from actual upstream work "
            f"(SR-DISP: same rule as needs.from resolution)"
        )


def _assert_acyclic(nodes: list[dict], nodes_by_id: dict[str, dict]) -> None:
    """Raise ManifestError if the DAG contains a cycle (Kahn's algorithm)."""
    # Build adjacency and per-predecessor in-degree counts.
    # Use sets to avoid double-counting duplicate (from, to) pairs.
    predecessors: dict[str, set[str]] = {nid: set() for nid in nodes_by_id}
    successors: dict[str, set[str]] = {nid: set() for nid in nodes_by_id}

    for node in nodes:
        nid = node["id"]
        for need in node.get("needs", []):
            from_id = need["from"]
            predecessors[nid].add(from_id)
            successors[from_id].add(nid)

    in_degree = {nid: len(preds) for nid, preds in predecessors.items()}

    # Kahn: start with all zero-in-degree nodes
    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    processed = 0
    while queue:
        nid = queue.pop()
        processed += 1
        for successor in successors[nid]:
            in_degree[successor] -= 1
            if in_degree[successor] == 0:
                queue.append(successor)

    if processed != len(nodes_by_id):
        raise ManifestError(
            "DAG manifest contains a cycle — topological sort incomplete "
            f"({processed} of {len(nodes_by_id)} nodes processed)"
        )


# ---------------------------------------------------------------------------
# SR-DISP: non-fatal boundary-smell WARNs
# ---------------------------------------------------------------------------

def manifest_warns(manifest: dict[str, Any]) -> list[str]:
    """Return non-fatal warning strings for structural boundary smells.

    Does NOT raise. A manifest that passes validate_manifest may still have
    structural smells surfaced here.

    Currently detected: a 'continues' path from the continued ancestor to the
    current node that crosses a node with 'produces:' or a 'human-go' node.
    This is a structural signal that a durable-artifact or decision boundary
    was crossed — prefer a fresh dispatch pointed at the artifact.

    Called by verbs (dag run / tick / status) to surface smells at runtime.
    """
    from .walker import _transitive_upstream  # no circular dep

    nodes_by_id: dict[str, dict] = {n["id"]: n for n in manifest.get("nodes", [])}
    warns: list[str] = []

    # Build a successors map for forward reachability from the continues ancestor.
    successors: dict[str, list[str]] = {nid: [] for nid in nodes_by_id}
    for nid, node in nodes_by_id.items():
        for need in node.get("needs", []):
            from_id = need.get("from")
            if from_id and from_id in successors:
                successors[from_id].append(nid)

    for nid, node in nodes_by_id.items():
        node_type = node.get("type", DEFAULT_NODE_TYPE)
        if node_type != "agent":
            continue
        continues = node.get("continues")
        if not continues or not isinstance(continues, dict):
            continue
        cont_node_id = continues.get("node")
        if not cont_node_id or not isinstance(cont_node_id, str):
            continue
        if cont_node_id not in nodes_by_id:
            continue  # will have been caught by validate_manifest

        # Find nodes "between" cont_node_id and nid:
        #   = ancestors(nid) ∩ descendants(cont_node_id)
        # ancestors(nid): _transitive_upstream(nid)
        # descendants(cont_node_id): forward BFS from cont_node_id
        ancestors_nid = _transitive_upstream(nid, nodes_by_id)

        # Forward BFS from cont_node_id
        descendants_cont: set[str] = set()
        bfs_queue = [cont_node_id]
        while bfs_queue:
            n = bfs_queue.pop()
            for succ in successors.get(n, []):
                if succ not in descendants_cont:
                    descendants_cont.add(succ)
                    bfs_queue.append(succ)

        # Between = ancestors of nid that are also descendants of cont_node_id
        # (excludes cont_node_id itself and nid itself)
        between = ancestors_nid & descendants_cont - {nid}

        # Check if any node in 'between' has produces: or is human-go
        boundary_crossings = []
        for bn in between:
            bn_node = nodes_by_id[bn]
            bn_type = bn_node.get("type", DEFAULT_NODE_TYPE)
            if bn_type == "human-go" or bn_node.get("produces"):
                boundary_crossings.append(bn)

        if boundary_crossings:
            crossed = ", ".join(sorted(boundary_crossings))
            warns.append(
                f"⚠ [{nid}] resumes across a durable-artifact/decision boundary "
                f"(produces/human-go at: {crossed}, between {cont_node_id!r} and {nid!r}) "
                f"— prefer a fresh dispatch pointed at the artifact."
            )

    return warns


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def nodes_by_id(manifest: dict[str, Any]) -> dict[str, dict]:
    """Return a {node_id: node_dict} mapping from a validated manifest."""
    return {n["id"]: n for n in manifest["nodes"]}


def global_cap(manifest: dict[str, Any]) -> int:
    """Return the global_cap from a manifest (defaulting to DEFAULT_GLOBAL_CAP)."""
    return manifest.get("global_cap", DEFAULT_GLOBAL_CAP)
