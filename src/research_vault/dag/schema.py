"""schema.py — DAG manifest schema for Research Vault (SR-3).

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

Validation rules:
  - run_id and nodes are required at the top level.
  - Node IDs must be unique strings.
  - All needs.from values must resolve to a known node ID.
  - A node cannot list itself in needs.from (no self-references).
  - The graph must be acyclic (checked via Kahn's topological sort).
  - node type must be "agent" or "human-go".
  - edge kind must be one of the four above.

Stdlib only. No external deps.
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
# Convenience helpers
# ---------------------------------------------------------------------------

def nodes_by_id(manifest: dict[str, Any]) -> dict[str, dict]:
    """Return a {node_id: node_dict} mapping from a validated manifest."""
    return {n["id"]: n for n in manifest["nodes"]}


def global_cap(manifest: dict[str, Any]) -> int:
    """Return the global_cap from a manifest (defaulting to DEFAULT_GLOBAL_CAP)."""
    return manifest.get("global_cap", DEFAULT_GLOBAL_CAP)
