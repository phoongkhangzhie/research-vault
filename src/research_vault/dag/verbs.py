"""verbs.py — `rv dag` verb implementations for Research Vault (SR-3 + SR-SCOPE).

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

  rv dag approve <run_id> <node_id>
      Approve a human-go node that is in 'awaiting-go' state.
      Moves it to 'succeeded' and re-prints the frontier.

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
  which is SR-2's background-poller pattern composing SR-3's dag tick.

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


def _sym(status: str) -> str:
    return _STATUS_SYMBOL.get(status, f"  ?({status})")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_frontier(frontier: list[FrontierNode], run_id: str) -> None:
    """Print frontier items and actionable commands.

    SR-DISP: DISPATCH lines carry the spec pointer and dispatch mode:
      FRESH — spec:<ptr>             (no continues field — default fresh dispatch)
      CONTINUES <node> — <reason> — spec:<ptr>   (explicit resume exception)

    SR-SCOPE: when reads: is present on the node, appends the bounded grounding scope:
      FRESH — spec:<ptr> — reads: <p1>, <p2>, …
      CONTINUES <node> — <reason> — spec:<ptr> — reads: <p1>, <p2>, …
    When reads: is absent the suffix is omitted (non-breaking additive suffix).
    """
    if not frontier:
        print("  (frontier empty — all nodes terminal or waiting for external conditions)")
        return
    for item in frontier:
        label = item.node.get("label", item.node_id)
        if item.action == "dispatch":
            print(f"  → DISPATCH  [{item.node_id}] {label}")
            # SR-DISP: print mode line (spec pointer + fresh/continues mode)
            spec = item.node.get("spec", "")
            continues = item.node.get("continues")
            if continues and isinstance(continues, dict):
                cont_node = continues.get("node", "")
                cont_reason = continues.get("reason", "")
                mode_line = f"      CONTINUES {cont_node} — {cont_reason} — spec:{spec}"
            else:
                mode_line = f"      FRESH — spec:{spec}"
            # SR-SCOPE: append reads: suffix if present
            reads = item.node.get("reads")
            if reads and isinstance(reads, list):
                refs = []
                for r in reads:
                    if isinstance(r, str):
                        refs.append(r)
                    elif isinstance(r, dict):
                        ref = r.get("ref", "")
                        if ref:
                            refs.append(ref)
                if refs:
                    mode_line += f" — reads: {', '.join(refs)}"
            print(mode_line)
        elif item.action == "await-go":
            print(f"  ⏸ AWAIT-GO  [{item.node_id}] {label}")
            print(f"      run: rv dag approve {run_id} {item.node_id}")


def _print_manifest_warns(manifest: dict[str, Any]) -> None:
    """Print non-fatal SR-DISP/SR-SCOPE warnings to stdout (if any).

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
) -> None:
    """SR-SCOPE: resolve reads: pointers; print errors + warns to stdout/stderr.

    Hard errors (unresolvable pointers) are printed to stderr — they signal the
    manifest's reading-scope is broken and the agent would re-ground blindly.
    Soft warns (symbol not found) are printed to stdout.

    This is a non-blocking advisory pass — it does NOT abort the run/tick
    (the manifest already passed validate_manifest structurally). Surfacing the
    errors here is the run/tick equivalent of reconcile's artifact checks.
    """
    errors, warns = resolve_reads_pointers(manifest, project_root=project_root)
    for w in warns:
        print(f"  ⚠ reads-scope: {w}")
    if errors:
        import sys as _sys
        for e in errors:
            print(f"{verb_prefix}: reads-scope ERROR: {e}", file=_sys.stderr)
        print(f"{verb_prefix}: {len(errors)} reads pointer(s) unresolvable — "
              f"fix the manifest's reads: field(s) before dispatching.", file=_sys.stderr)


def _recompute_awaiting_go(
    run_state: RunState,
    manifest: dict[str, Any],
    store: RunStore,
) -> list[FrontierNode]:
    """Compute frontier and auto-advance human-go nodes to 'awaiting-go' status.

    Any human-go node that appears in the frontier as "await-go" and is still
    "pending" in the run state gets promoted to "awaiting-go" here.
    This is the transition: pending → awaiting-go (not dispatchable).
    The run state is saved after any promotions.
    """
    cap = manifest_global_cap(manifest)
    frontier = compute_frontier(
        manifest,
        run_state.node_states,
        run_state.edge_registered_ts,
        cap,
    )

    # Promote pending human-go nodes that are now await-go-ready
    promoted = False
    for item in frontier:
        if item.action == "await-go":
            current = run_state.node_status(item.node_id)
            if current == "pending":
                run_state.set_node_status(item.node_id, "awaiting-go")
                promoted = True

    if promoted:
        store.save(run_state)

    # Recompute after promotion (awaiting-go nodes are now non-advanceable,
    # so the frontier won't include them again — but we return the pre-promotion
    # frontier so the caller can print the await-go items with their commands).
    return frontier


# ---------------------------------------------------------------------------
# OKF note type-directory check
# ---------------------------------------------------------------------------

def _check_okf_note_type(note_path_str: str, notes_root: Path) -> list[str]:
    """Validate that an OKF note's type: frontmatter matches its parent directory.

    Returns a list of issue strings (empty = OK).
    This is the vault check gate for produces-typed nodes:
      A node that writes the WRONG type dir fails this check.
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

    # The type must match the parent directory name
    parent_dir = note_path.parent.name
    if declared_type != parent_dir:
        return [
            f"note type mismatch: type={declared_type!r} but "
            f"file is in {parent_dir!r} directory ({note_path})"
        ]

    return []


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

    # SR-SCOPE: resolve reads: pointers (I/O pass — after pure validate)
    _resolve_reads_or_warn(manifest, manifest_path.parent, "rv dag run")

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

    # SR-SCOPE: resolve reads: pointers (I/O pass — after pure validate)
    _resolve_reads_or_warn(manifest, manifest_path.parent, "rv dag tick")

    print(f"Tick: run {run_id!r}")
    frontier = _recompute_awaiting_go(run_state, manifest, store)
    print("Frontier:")
    _print_frontier(frontier, run_id)
    return 0


# ---------------------------------------------------------------------------
# Verb: complete
# ---------------------------------------------------------------------------

def cmd_complete(args: argparse.Namespace) -> int:
    """Mark a node complete and re-print the frontier."""
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

    # OKF produces check: if the node has produces.note and status is succeeded,
    # validate the note's type:dir matches.
    node = nodes_lookup[node_id]
    if status == "succeeded" and "produces" in node:
        produces = node["produces"]
        if "note" in produces:
            issues = _check_okf_note_type(produces["note"], cfg.notes_root)
            if issues:
                print(f"rv dag complete: OKF vault check FAILED for node {node_id!r}:", file=sys.stderr)
                for issue in issues:
                    print(f"  {issue}", file=sys.stderr)
                print("  Fix: ensure the note's type: frontmatter matches its parent directory.", file=sys.stderr)
                return 1
        # SR-8: dataset provenance gate — complete-time check.
        # The gate: note exists + location non-empty + hash non-empty +
        # (if local path) file exists and sha256 matches.
        # NOT-done when hash mismatches — "you structurally cannot publish a finding
        # whose data lineage isn't recorded" (the structural teeth are on the
        # watch/frontier path; this is the post-hoc complete-time check).
        if "dataset" in produces:
            from ..wait_for import check_dataset_provenance
            issues = check_dataset_provenance(produces["dataset"], cfg.notes_root)
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

    run_state.set_node_status(node_id, status)
    store.save(run_state)

    print(f"Node {node_id!r} → {status}")
    frontier = _recompute_awaiting_go(run_state, manifest, store)
    print("Frontier:")
    _print_frontier(frontier, run_id)
    return 0


# ---------------------------------------------------------------------------
# Verb: approve
# ---------------------------------------------------------------------------

def cmd_approve(args: argparse.Namespace) -> int:
    """Approve a human-go node in 'awaiting-go' state → 'succeeded'."""
    run_id = args.run_id
    node_id = args.node_id

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

    run_state.set_node_status(node_id, "succeeded")
    store.save(run_state)

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
# Verb: status
# ---------------------------------------------------------------------------

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
        print(f"  {sym} {nid}  ({status}){err_str}")
        if node.get("type") != "human-go" and label != nid:
            print(f"      {label}")

    # Print awaiting-go commands
    awaiting = [
        n for n in manifest["nodes"]
        if run_state.node_status(n["id"]) == "awaiting-go"
    ]
    if awaiting:
        print()
        print("Awaiting human approval:")
        for node in awaiting:
            nid = node["id"]
            label = node.get("label", nid)
            print(f"  [{nid}] {label}")
            print(f"      run: rv dag approve {run_id} {nid}")

    # Show current frontier
    print()
    print("Current frontier:")
    frontier = compute_frontier(
        manifest,
        run_state.node_states,
        run_state.edge_registered_ts,
        manifest_global_cap(manifest),
    )
    _print_frontier(frontier, run_id)

    return 0


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``dag`` verb.

    When to use: ``rv dag run/tick/complete/approve/add/insert/status``
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
            help="Orchestrate a multi-node research DAG (SR-3).",
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

    # approve
    app_p = sub.add_parser("approve", help="Approve a human-go node.")
    app_p.add_argument("run_id", help="The run_id.")
    app_p.add_argument("node_id", help="The human-go node id to approve.")

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

    # status
    stat_p = sub.add_parser("status", help="Print the current run status.")
    stat_p.add_argument("run_id", help="The run_id.")

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
        "status": cmd_status,
    }
    dag_cmd = getattr(args, "dag_cmd", None)
    fn = cmd_map.get(dag_cmd)
    if fn is None:
        print(f"rv dag: unknown subcommand {dag_cmd!r}", file=sys.stderr)
        return 1
    return fn(args)
