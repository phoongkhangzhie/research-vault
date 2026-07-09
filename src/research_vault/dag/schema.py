"""schema.py — DAG manifest schema for Research Vault (SR-3 + SR-DISP + SR-SCOPE + SR-RETRY).

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
        "max_retries": 2,              # OPTIONAL — opt-in retry count (SR-RETRY; default 0)
                                       #   agent nodes only; 0 <= N <= 10; integer
                                       #   ManifestError if negative, >10, non-int, or on human-go
        "retry_diagnosis_tips": "Check W&B exit code before assuming OOM.",
                                       # OPTIONAL — domain-specific append to RETRY_DIAGNOSIS_DIRECTIVE
                                       #   str or list[str]; never replaces the standing directive
        "reads": [                     # OPTIONAL — bounded reading-scope (SR-SCOPE)
          "src/research_vault/dag/schema.py",       # bare = file pointer
          "tasks/design.md#5B-SCOPE",               # <file>#<anchor> = doc/task section
          {"ref": "control/rv.md#sr-scope", "why": "prior verdict"}  # {ref, why?}
        ],
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

  human-go nodes are EXEMPT from spec/continues/reads requirements (they are decision
  gates, not dispatch targets).

SR-SCOPE additions (bounded reading-scope grounding manifest):
  Agent nodes:
  - reads OPTIONAL — but absent emits a non-fatal WARN (manifest_warns).
    When present, must be a non-empty list; each item must be either:
      - a non-empty string (bare pointer: file path, file#anchor, or path:symbol), OR
      - a dict {ref: <non-empty str>, why?: <optional str>}
    Any violation is a ManifestError (structural, pure/in-memory check).
  - Pointer RESOLUTION (I/O: file exists, anchor found) is NOT done here —
    it is deferred to the run/tick pass in dag/reads.py (resolve_reads_pointers).
  - human-go nodes must NOT carry reads: (ManifestError if present).

SR-RETRY additions (node-level diagnose-before-retry):
  Agent nodes:
  - max_retries OPTIONAL int — default 0 (N=0 = today's behavior: first failure is terminal).
    Must be 0 <= N <= 10. Non-int, negative, or >10 → ManifestError.
    human-go nodes must NOT carry max_retries (ManifestError; D-RETRY-1).
  - retry_diagnosis_tips OPTIONAL — appends domain guidance after RETRY_DIAGNOSIS_DIRECTIVE
    (the standing non-blind-repeat teeth). Must be str or list[str] when present.
    Non-string or list containing non-str → ManifestError.

Non-fatal WARNs (manifest_warns):
  1. A continues path crossing a produces: or human-go node between the continued ancestor
     and the current node → structural boundary-smell WARN (SR-DISP).
  2. An agent node with NO reads: field → reads-scope WARN (SR-SCOPE).
  Both are non-fatal: the manifest is still valid, but the runtime should surface them.

Stdlib only (plus intra-package walker import for _transitive_upstream reuse — no circularity).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

NODE_TYPES: frozenset[str] = frozenset({"agent", "human-go", "tool"})
EDGE_KINDS: frozenset[str] = frozenset({"afterok", "after", "afterany", "soft"})
REQUIRED_MANIFEST_FIELDS: frozenset[str] = frozenset({"run_id", "nodes"})

DEFAULT_GLOBAL_CAP = 4
DEFAULT_NODE_TYPE = "agent"
DEFAULT_EDGE_KIND = "afterok"

# SR-RETRY: hard cap on max_retries (D-RETRY-4) — cheap insurance against typos.
MAX_RETRIES_CAP = 10


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
            _validate_reads_structure(nid, node)
            # ── SR-RETRY: opt-in retry + diagnosis seam ───────────────────────
            _validate_max_retries(nid, node)
            _validate_retry_diagnosis_tips(nid, node)

        # ── SR-SCOPE: human-go nodes must NOT carry reads: ────────────────────
        if node_type == "human-go":
            _validate_no_reads_on_human_go(nid, node)
            # ── SR-RETRY: human-go must NOT carry max_retries (D-RETRY-1) ─────
            _validate_no_max_retries_on_human_go(nid, node)

        # ── D4 (verb consolidation): tool nodes require a non-empty 'op' ──────
        # and are exempt from spec/continues/reads/max_retries (they are
        # executed IN-PROCESS by the runner, never dispatched to a crew agent
        # or a human — same "not a dispatch target" shape as human-go).
        if node_type == "tool":
            _validate_tool_op(nid, node)
            _validate_no_reads_on_human_go(nid, node, kind="tool")
            _validate_no_max_retries_on_human_go(nid, node, kind="tool")

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
            # SR-8: dataset provenance note (points to data artifact, never contains it)
            if "dataset" in produces:
                dataset_path = produces["dataset"]
                if not isinstance(dataset_path, str) or not dataset_path.strip():
                    raise ManifestError(
                        f"Node {nid!r}: produces.dataset must be a non-empty string "
                        f"(path to the datasets/ provenance note, e.g. 'datasets/my-data.md')"
                    )
            # SR-RESOLVE-SCOPE: project-scoped typed produces subkeys.
            # Each takes "<project>/<id>" — the resolver maps to the correct OKF type dir.
            #   produces.result → experiments/<id>.md in project_notes_dir
            for _pkey in ("result",):
                if _pkey in produces:
                    _pval = produces[_pkey]
                    if not isinstance(_pval, str) or not _pval.strip():
                        raise ManifestError(
                            f"Node {nid!r}: produces.{_pkey} must be a non-empty string "
                            f"in '<project>/<id>' format (e.g. 'my-project/exp-001')"
                        )
                    if "/" not in _pval:
                        raise ManifestError(
                            f"Node {nid!r}: produces.{_pkey} must include a project slug: "
                            f"'<project>/<id>' (e.g. 'my-project/exp-001'), got {_pval!r}"
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


def _validate_reads_structure(nid: str, node: dict) -> None:
    """Validate the structural shape of a reads field on an agent node (if present).

    SR-SCOPE structural teeth (pure/in-memory — no I/O):
      - reads is OPTIONAL; absent is valid.
      - If present: must be a non-empty list.
      - Each item must be either:
          a non-empty string (bare pointer), OR
          a dict with a non-empty 'ref' key (and optional 'why': str).
      - Any violation is a ManifestError.

    Pointer RESOLUTION (filesystem) is NOT done here — that is the run/tick pass
    in dag/reads.py (resolve_reads_pointers), respecting the purity boundary.

    Raises ManifestError on any structural violation.
    """
    reads = node.get("reads")
    if reads is None:
        return  # optional — absent is valid

    if not isinstance(reads, list):
        raise ManifestError(
            f"Node {nid!r}: 'reads' must be a list of pointer strings or {{ref:...}} dicts, "
            f"got {type(reads).__name__!r} (SR-SCOPE)"
        )

    if len(reads) == 0:
        raise ManifestError(
            f"Node {nid!r}: 'reads' must be non-empty if present — "
            f"an empty declared scope is invalid (SR-SCOPE). "
            f"Remove the field entirely if there are no grounding pointers."
        )

    for i, item in enumerate(reads):
        if isinstance(item, str):
            if not item.strip():
                raise ManifestError(
                    f"Node {nid!r}: reads[{i}] must be a non-empty string, "
                    f"got {item!r} (SR-SCOPE)"
                )
        elif isinstance(item, dict):
            ref = item.get("ref")
            if ref is None:
                raise ManifestError(
                    f"Node {nid!r}: reads[{i}] dict must have a 'ref' key "
                    f"(SR-SCOPE). Got keys: {sorted(item.keys())}"
                )
            if not isinstance(ref, str) or not ref.strip():
                raise ManifestError(
                    f"Node {nid!r}: reads[{i}].ref must be a non-empty string, "
                    f"got {ref!r} (SR-SCOPE)"
                )
            # 'why' is optional — if present, should be a string (non-fatal structural check)
            why = item.get("why")
            if why is not None and not isinstance(why, str):
                raise ManifestError(
                    f"Node {nid!r}: reads[{i}].why must be a string if present, "
                    f"got {type(why).__name__!r} (SR-SCOPE)"
                )
        else:
            raise ManifestError(
                f"Node {nid!r}: reads[{i}] must be a non-empty string or a "
                f"{{ref: <str>, why?: <str>}} dict, got {type(item).__name__!r} (SR-SCOPE)"
            )


def _validate_no_reads_on_human_go(nid: str, node: dict, *, kind: str = "human-go") -> None:
    """Raise ManifestError if a human-go (or, per D4, a tool) node carries a
    reads: field.

    human-go nodes are decision gates, not dispatch targets — they have no
    reading-scope (same exemption as spec/continues, SR-SCOPE). tool nodes
    (D4, verb consolidation) are executed IN-PROCESS by the runner, not
    dispatched to a crew agent — same exemption, shared validator.
    """
    if "reads" in node:
        raise ManifestError(
            f"Node {nid!r}: 'reads' is not allowed on {kind} nodes — "
            f"{kind} nodes are not dispatch targets (SR-SCOPE). "
            f"Remove the 'reads' field from this node."
        )


def _validate_max_retries(nid: str, node: dict) -> None:
    """Validate max_retries on an agent node (SR-RETRY, §5I.4).

    - OPTIONAL; absent means default 0 (first failure is terminal — backward-compat).
    - Must be an int (not float, not str): non-int → ManifestError.
    - Must satisfy 0 <= N <= MAX_RETRIES_CAP: negative or over-cap → ManifestError.
    """
    v = node.get("max_retries")
    if v is None:
        return  # absent → default 0; valid
    # isinstance check: booleans are ints in Python but nonsensical here — reject them too.
    if not isinstance(v, int) or isinstance(v, bool):
        raise ManifestError(
            f"Node {nid!r}: 'max_retries' must be a non-negative integer "
            f"(0 <= N <= {MAX_RETRIES_CAP}), got {v!r} (SR-RETRY)"
        )
    if v < 0:
        raise ManifestError(
            f"Node {nid!r}: 'max_retries' must be >= 0, got {v!r} (SR-RETRY)"
        )
    if v > MAX_RETRIES_CAP:
        raise ManifestError(
            f"Node {nid!r}: 'max_retries' must be <= {MAX_RETRIES_CAP} "
            f"(hard cap against runaway retries), got {v!r} (SR-RETRY)"
        )


def _validate_tool_op(nid: str, node: dict) -> None:
    """Validate that a tool (D4, deterministic-op) node has a non-empty
    'op' field naming a registered ``review.autonomy.OP_REGISTRY`` entry.

    Registry membership is NOT checked here (schema.py is a pure/in-memory
    validator with no import of the op registry — a circularity smell) —
    only the structural shape (non-empty string). An unregistered op name
    fails loudly at execution time (``review.autonomy.run_tool_op``).
    """
    op = node.get("op")
    if op is None:
        raise ManifestError(
            f"Node {nid!r}: tool nodes require a non-empty 'op' field naming "
            f"the deterministic op to invoke IN-PROCESS (D4, verb "
            f"consolidation). Example: \"op\": \"coverage\""
        )
    if not isinstance(op, str) or not op.strip():
        raise ManifestError(
            f"Node {nid!r}: 'op' must be a non-empty string, got {op!r}"
        )
    args = node.get("args")
    if args is not None and not isinstance(args, dict):
        raise ManifestError(
            f"Node {nid!r}: 'args' must be a dict when present, got {type(args).__name__}"
        )


def _validate_no_max_retries_on_human_go(nid: str, node: dict, *, kind: str = "human-go") -> None:
    """Raise ManifestError if a human-go (or, per D4, a tool) node carries
    max_retries (D-RETRY-1, SR-RETRY).

    human-go/tool nodes are decision gates or in-process ops, not dispatch
    targets — retry is meaningless. Mirror of _validate_no_reads_on_human_go.
    """
    if "max_retries" in node:
        raise ManifestError(
            f"Node {nid!r}: 'max_retries' is not allowed on {kind} nodes — "
            f"{kind} nodes are not dispatch targets (SR-RETRY, D-RETRY-1). "
            f"Remove the 'max_retries' field from this node."
        )


def _validate_retry_diagnosis_tips(nid: str, node: dict) -> None:
    """Validate retry_diagnosis_tips on an agent node (SR-RETRY, §5I, D-RETRY-8).

    - OPTIONAL; absent → only the standing RETRY_DIAGNOSIS_DIRECTIVE is used.
    - When present: must be a str, or a list where every element is a str.
    - Non-str, list with non-str items → ManifestError.
    """
    v = node.get("retry_diagnosis_tips")
    if v is None:
        return  # absent → valid
    if isinstance(v, str):
        return  # single string → valid
    if isinstance(v, list):
        for i, item in enumerate(v):
            if not isinstance(item, str):
                raise ManifestError(
                    f"Node {nid!r}: 'retry_diagnosis_tips[{i}]' must be a string, "
                    f"got {type(item).__name__!r} (SR-RETRY)"
                )
        return
    raise ManifestError(
        f"Node {nid!r}: 'retry_diagnosis_tips' must be a str or list[str], "
        f"got {type(v).__name__!r} (SR-RETRY)"
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

    Currently detected:
      1. SR-DISP: a 'continues' path from the continued ancestor to the current node
         that crosses a node with 'produces:' or a 'human-go' node. Structural signal
         that a durable-artifact or decision boundary was crossed — prefer a fresh
         dispatch pointed at the artifact.
      2. SR-SCOPE: an agent node with NO 'reads:' field — dispatched with an unbounded
         reading-scope; the agent will re-ground by broad exploration. Bound it with
         the artifacts the agent must read (add a 'reads:' field).

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

    # ── SR-SCOPE: absent reads: WARN on agent nodes ───────────────────────────
    for nid, node in nodes_by_id.items():
        node_type = node.get("type", DEFAULT_NODE_TYPE)
        if node_type != "agent":
            continue  # human-go exempt
        if node.get("reads") is None:
            warns.append(
                f"⚠ [{nid}] dispatched with an unbounded reading-scope (no 'reads:') "
                f"— the agent will re-ground by broad exploration; bound it with "
                f"the artifacts it must read (SR-SCOPE)."
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
