"""verbs.py — `rv dag` verb implementations for Research Vault (SR-3 + SR-SCOPE + SR-RETRY).

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

# ---------------------------------------------------------------------------
# SR-RETRY: diagnose-before-retry doctrine string (§5I.5b, D-RETRY-8)
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

    SR-DISP: DISPATCH lines carry the spec pointer and dispatch mode:
      FRESH — spec:<ptr>             (no continues field — default fresh dispatch)
      CONTINUES <node> — <reason> — spec:<ptr>   (explicit resume exception)

    SR-SCOPE: when reads: is present on the node, appends the bounded grounding scope:
      FRESH — spec:<ptr> — reads: <p1>, <p2>, …
      CONTINUES <node> — <reason> — spec:<ptr> — reads: <p1>, <p2>, …
    When reads: is absent the suffix is omitted (non-breaking additive suffix).

    SR-RETRY: for a dispatch node with attempts > 0, renders the diagnose-first block
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
            # SR-RETRY: read attempts from node_states to decide if this is a retry dispatch
            ns = (node_states or {}).get(item.node_id, {})
            attempts = ns.get("attempts", 0)
            last_failure = ns.get("last_failure")
            max_retries = item.node.get("max_retries", 0)

            print(f"  → DISPATCH  [{item.node_id}] {label}")
            # SR-RETRY: retry indicator in the header line
            if attempts > 0:
                print(f"      attempt {attempts + 1}/{max_retries + 1}")
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
            # SR-RETRY: render diagnose-first block only on retry dispatches (attempts > 0)
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
# SR-RESOLVE-SCOPE: project-scoped typed produces gate
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
    SR-RESOLVE-SCOPE.
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
    return _check_okf_note_type(str(note_path), proj_notes)


# ---------------------------------------------------------------------------
# SR-DAG-BRIEF: resolve_produces_paths — informational path list for build_brief
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

_FAILURE_SUMMARY_MAX_CHARS = 4000  # SR-RETRY: cap stored failure summaries (§5I.2)


def cmd_complete(args: argparse.Namespace) -> int:
    """Mark a node complete and re-print the frontier.

    SR-RETRY: on --status failed, reads --error / --error-file, persists
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

    # ── SR-RETRY: failure capture + retry-reset logic ─────────────────────────
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
            ns["started_at"] = None  # per §5I.5: reset for truthful per-attempt timing

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
            # SR-RETRY cosmetic fix: only mention "retries exhausted" when there were
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
        if "note" in produces:
            # F21 (adopter fix): resolve produces.note against the project's
            # source_dir, not the shared notes_root.  The manifest may declare
            # a "project" slug at the top level; when it does, use
            # cfg.project_notes_dir(slug) as the resolution base so that
            # multi-repo adopters (source_dir != notes_root) are handled
            # correctly.  Falls back to cfg.notes_root for manifests with no
            # "project" field (demo case; source_dir == notes_root stays green).
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
            issues = _check_okf_note_type(produces["note"], _note_root)
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
            # SR-8 amendment: datasets are shared — resolve against cfg.datasets_root
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
        # SR-RESOLVE-SCOPE: project-scoped typed produces gate.
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
    # ``approve-protocol`` node (§5L.3 convention — see review/_build_phase1_manifest)
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
            from ..review import check_protocol_gate
            ok, msg = check_protocol_gate(Path(protocol_ref))
            if not ok:
                print(msg, file=sys.stderr)
                return 1

    # PR-M6: the lit-review manuscript type's framework-selection Phase-1 gate
    # (design §5, D5) — mirrors the L-2 gate above. ``approve-framework`` may
    # not be approved unless the manuscript's ``_manuscript.md`` (sibling to
    # this Phase-1 manifest, at ``manifest_path.parent``) carries a non-empty
    # ``spine_shape``+``branches``. Only the lit-review type registers a
    # Phase-1 with this node id; other types' Phase-1 (if any) never hits this
    # branch. --reject is the escape hatch, same convention as approve-protocol.
    if node_id == "approve-framework" and not reject:
        manuscript_note_path = manifest_path.parent / "_manuscript.md"
        from ..manuscript.types.lit_review import check_framework_gate
        ok, msg = check_framework_gate(manuscript_note_path)
        if not ok:
            print(msg, file=sys.stderr)
            return 1

    # Manuscript-integration PR: the assembled gate payload (hermetic .bib
    # BLOCK, equation-fidelity SIGNAL, support-matcher/cold-read BLOCK/SIGNAL
    # behind the judge guard — manuscript/check_gates.py::build_approve_payload)
    # gates ``approve-manuscript``. Mirrors the ``approve-framework`` wiring
    # above exactly: ``manifest_path.parent`` IS the manuscript tree root
    # (Phase-2 manifests are written to ``manuscripts/<slug>/phase2-dag.json``,
    # sibling to ``_manuscript.md``). --reject is the same escape hatch.
    if node_id == "approve-manuscript" and not reject:
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
                payload = build_approve_payload(tree_root, project_notes_dir, _ms_type)
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
                # PR-M5 fix (integration-reviewer followup, charter §2): an
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
                    "the hermetic .bib, equation-fidelity, support-matcher, and "
                    "cold-read gates were NOT run for this manuscript. This is "
                    "NOT a pass: fix `manuscript_type:` in "
                    f"{manuscript_note_path} to a registered type (see "
                    "`rv manuscript <project> new --type <type>`) and re-run "
                    "`rv dag approve` before trusting this manuscript.",
                    file=sys.stderr,
                )

    # K-3 freeze-set verify hook (§5K.5.1, SR-PLAN-1, SR-FREEZE-FIX).
    #
    # When a covers:-freeze hash is stored in run_state.meta["plan_freeze"]
    # AND the node being approved is NOT the plan-freeze gate itself
    # (convention: node_id == "human-go-plan" is the freeze gate), re-derive
    # the hash and BLOCK approval on mismatch.
    #
    # SR-FREEZE-FIX (hole b): The stored plan_freeze["notes_root"] is used for
    # re-derivation — NOT re-derived from cfg.notes_root.  The config re-derive
    # was the source of the non-reproducibility bug.
    #
    # SR-FREEZE-FIX (approve hardening): on a verify EXCEPTION, BLOCK (return 1)
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
                # SR-FREEZE-FIX: BLOCK on exception — an integrity gate must not
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

    # SR-APPROVE-GATE: human-presence check — BEFORE any state write.
    # Covers both approve (→ succeeded) and --reject (→ blocked).
    # Fail-closed: non-TTY + no valid token → return 1, state UNCHANGED.
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

    # SR-APPROVE-GATE Slice 2: record approval provenance.
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
# Verb: status
# ---------------------------------------------------------------------------

def cmd_templates(args: argparse.Namespace) -> int:
    """Print the built-in loop catalog — discovery entry for all four research loops.

    SR-HUB-DAG §A2: pure read, no config needed.
    """
    from .catalog import LOOP_CATALOG

    for entry in LOOP_CATALOG:
        print(f"Loop: {entry.key}")
        print(f"  scaffolder : {entry.scaffolder or '(none — manifest authored manually)'}")
        print(f"  entry verb : {entry.entry_verb}")
        has_scaffolder = entry.scaffolder is not None
        print(f"  scaffolder exists: {'yes' if has_scaffolder else 'no'}")
        if entry.human_go_gates:
            print(f"  human-go gates ({len(entry.human_go_gates)}):")
            for g in entry.human_go_gates:
                print(f"    [{g.node_id}] {g.label}")
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
        # SR-RETRY: show attempt progress only on genuinely retry-queued (pending) nodes.
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
        # SR-APPROVE-GATE Slice 2: show approval provenance for decided human-go nodes.
        if node.get("type") == "human-go" and status in ("succeeded", "blocked"):
            _by = ns.get("approved_by", "")
            _meth = ns.get("approval_method", "")
            _at = ns.get("approved_at", "")
            if _by or _meth:
                _prov = f"      approved_by={_by!r} method={_meth!r}"
                if _at:
                    _prov += f" at={_at}"
                print(_prov)
        # SR-RETRY: for pending nodes with prior failures, print last_failure
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
# Verb: brief  (SR-DAG-BRIEF)
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
    # SR-RETRY: failure capture for diagnose-before-retry (§5I, D-RETRY-9)
    comp_p.add_argument(
        "--error",
        metavar="SUMMARY",
        default=None,
        help=(
            "Short failure summary (SR-RETRY). "
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
            "Path to a file whose content is used as the failure summary (SR-RETRY). "
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
            "SR-APPROVE-GATE: skip the confirmation keystroke when a TTY is present. "
            "Has NO EFFECT when stdin is not a TTY — the gate still fails closed "
            "(use a provisioned token for non-interactive approval instead)."
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

    # status
    stat_p = sub.add_parser("status", help="Print the current run status.")
    stat_p.add_argument("run_id", help="The run_id.")

    # templates  (SR-HUB-DAG §A2 — discovery entry for all four research loops)
    sub.add_parser(
        "templates",
        help=(
            "Print the built-in loop catalog: all four research loops with their "
            "scaffolder verb, entry command, and human-go gate locations."
        ),
    )

    # brief  (SR-DAG-BRIEF — deterministic crew dispatch brief emitter)
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
