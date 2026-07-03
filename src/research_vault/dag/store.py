"""store.py — atomic file state for DAG runs (SR-3).

State file location: <config.state_dir>/dag/<run_id>.json

Run state JSON structure:
  {
    "run_id": "loop-q1",
    "manifest_path": "/abs/path/to/manifest.json",
    "created_at": 1720000000.0,
    "node_states": {
      "node-a": {
        "status": "pending",
        "started_at": null,
        "completed_at": null,
        "error": null,
        "attempts": 0,
        "last_failure": null,
        "failures": []
      }
    },
    "edge_registered_ts": {
      "node-b:node-a:0": 1720000000.0
    }
  }

SR-RETRY fields (§5I.5, SR-RETRY):
  attempts     — persisted count of failed attempts so far (default 0; incremented at FAIL,
                 D-RETRY-5). The retry-decision input: retry fires iff attempts < max_retries.
  last_failure — the latest failure summary string (length-capped ~4000 chars) persisted on
                 --status failed. Single string threaded into the re-dispatch augmentation.
                 Retained across retry-resets; cleared only if node is re-created.
  failures     — append-only per-attempt history [{attempt, summary, ts}, ...].
                 Retained on retry-reset AND on terminal exhaustion (D-RETRY-7 = both).
                 Gives the downstream human diagnostician the full failure trajectory.
  NOTE: the walker (compute_frontier) reads NONE of these fields — they are part of
  node_states (the pure walker input) but the walker is byte-for-byte unchanged (§5I.1).

Valid statuses:
  pending      — not yet started
  dispatched   — sent to an agent, not yet running
  running      — actively executing
  succeeded    — completed successfully
  failed       — completed with failure
  blocked      — cannot proceed (upstream failed non-recoverable)
  awaiting-go  — human-go node waiting for human approval

Atomic writes: write to <file>.tmp, then rename → no torn reads.
Stdlib only.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Config

VALID_STATUSES: frozenset[str] = frozenset({
    "pending",
    "dispatched",
    "running",
    "succeeded",
    "failed",
    "blocked",
    "awaiting-go",
})


class StoreError(OSError):
    """Raised when a run state operation fails."""


# ---------------------------------------------------------------------------
# Run state dataclass
# ---------------------------------------------------------------------------

@dataclass
class RunState:
    """In-memory representation of a DAG run's current state.

    Attributes:
      run_id              — matches the manifest run_id
      manifest_path       — absolute path to the manifest JSON
      created_at          — Unix timestamp of run creation
      node_states         — {node_id: {status, started_at, completed_at, error}}
      edge_registered_ts  — {edge_key: float} for afterok+fresh watch resolution
                            edge_key format: "{to_id}:{from_id}:{need_index}"
    """
    run_id: str
    manifest_path: str
    created_at: float = field(default_factory=time.time)
    node_states: dict[str, dict[str, Any]] = field(default_factory=dict)
    edge_registered_ts: dict[str, float] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    """Generic key-value metadata for the run.

    Used by plan/freeze.py (K-3, SR-PLAN-1) to store the covers:-freeze-set hash
    at human-go-plan approval time and re-verify at human-go-findings.

    Known keys:
      "plan_freeze": {
          "covers_hash":  "<sha256-hex>",       — hash of sorted (child_id, stance, plan_role) tuples
          "plan_note":    "<abs-path-str>",      — path to the plan master note that was frozen
          "notes_root":   "<abs-path-str>|null", — resolution input pin (SR-FREEZE-FIX, hole b);
                                                    stored at freeze time so verify_freeze_hash
                                                    re-derives with the SAME notes_root regardless
                                                    of caller cwd/config.  null = legacy format
                                                    (pre-SR-FREEZE-FIX) — requires explicit
                                                    --notes-root at verify time.
          "frozen_at":    <float>,               — Unix timestamp of freeze
      }
    """

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "manifest_path": self.manifest_path,
            "created_at": self.created_at,
            "node_states": self.node_states,
            "edge_registered_ts": self.edge_registered_ts,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RunState":
        return cls(
            run_id=d["run_id"],
            manifest_path=d["manifest_path"],
            created_at=d.get("created_at", 0.0),
            node_states=d.get("node_states", {}),
            edge_registered_ts=d.get("edge_registered_ts", {}),
            meta=d.get("meta", {}),
        )

    # ── Convenience helpers ───────────────────────────────────────────────────

    def node_status(self, node_id: str) -> str:
        """Return the current status of a node (default 'pending')."""
        return self.node_states.get(node_id, {}).get("status", "pending")

    def set_node_status(
        self,
        node_id: str,
        status: str,
        *,
        error: str | None = None,
    ) -> None:
        """Update a node's status in-memory (does not persist; call store.save())."""
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status {status!r}. Valid: {sorted(VALID_STATUSES)}")
        ns = self.node_states.setdefault(node_id, {})
        ns["status"] = status
        if status in ("dispatched", "running") and ns.get("started_at") is None:
            ns["started_at"] = time.time()
        if status in ("succeeded", "failed", "blocked", "awaiting-go"):
            ns["completed_at"] = time.time()
        if error is not None:
            ns["error"] = error
        elif "error" not in ns:
            ns["error"] = None

    def init_nodes(self, manifest: dict[str, Any]) -> None:
        """Initialize node_states for all nodes in the manifest to 'pending'.

        Also populates edge_registered_ts for all afterok+watch edges
        (SR-3 owns persisting this timestamp, per the architect's flag).
        """
        ts = time.time()
        for node in manifest["nodes"]:
            nid = node["id"]
            if nid not in self.node_states:
                self.node_states[nid] = {
                    "status": "pending",
                    "started_at": None,
                    "completed_at": None,
                    "error": None,
                    # SR-RETRY: retry state fields (§5I.5) — walker never reads these
                    "attempts": 0,
                    "last_failure": None,
                    "failures": [],
                }
            # Register timestamps for afterok+watch edges
            for idx, need in enumerate(node.get("needs", [])):
                if need.get("edge", "afterok") == "afterok" and need.get("watch"):
                    from_id = need["from"]
                    edge_key = f"{nid}:{from_id}:{idx}"
                    if edge_key not in self.edge_registered_ts:
                        self.edge_registered_ts[edge_key] = ts


# ---------------------------------------------------------------------------
# Store (file-backed, atomic)
# ---------------------------------------------------------------------------

class RunStore:
    """File-backed run state store.

    All reads/writes are atomic: write to a .tmp file, then rename.
    One JSON file per run: <state_dir>/dag/<run_id>.json
    """

    def __init__(self, state_dir: Path) -> None:
        self._dag_dir = state_dir / "dag"

    def _run_path(self, run_id: str) -> Path:
        return self._dag_dir / f"{run_id}.json"

    def _ensure_dir(self) -> None:
        self._dag_dir.mkdir(parents=True, exist_ok=True)

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def create(self, run_state: RunState) -> None:
        """Persist a new run state. Raises StoreError if run_id already exists."""
        self._ensure_dir()
        path = self._run_path(run_state.run_id)
        if path.exists():
            raise StoreError(
                f"Run {run_state.run_id!r} already exists at {path}. "
                "Use a unique run_id or remove the existing state file."
            )
        self._write(path, run_state)

    def load(self, run_id: str) -> RunState:
        """Load a run state by run_id. Raises StoreError if not found."""
        path = self._run_path(run_id)
        if not path.exists():
            raise StoreError(
                f"Run {run_id!r} not found at {path}. "
                "Run `rv dag run <manifest>` to start a new run."
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise StoreError(f"Cannot load run {run_id!r}: {e}") from e
        return RunState.from_dict(data)

    def save(self, run_state: RunState) -> None:
        """Atomically persist an updated run state."""
        self._ensure_dir()
        path = self._run_path(run_state.run_id)
        self._write(path, run_state)

    def _write(self, path: Path, run_state: RunState) -> None:
        """Atomic write: write to .tmp, then rename."""
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(
                json.dumps(run_state.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(path)
        except OSError as e:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise StoreError(f"Cannot write run state to {path}: {e}") from e

    def list_runs(self) -> list[str]:
        """Return a sorted list of all run_ids in the store."""
        if not self._dag_dir.exists():
            return []
        return sorted(
            p.stem for p in self._dag_dir.glob("*.json")
            if not p.name.endswith(".tmp")
        )

    def delete(self, run_id: str) -> None:
        """Delete a run state file. Raises StoreError if not found."""
        path = self._run_path(run_id)
        if not path.exists():
            raise StoreError(f"Run {run_id!r} not found at {path}")
        try:
            path.unlink()
        except OSError as e:
            raise StoreError(f"Cannot delete run {run_id!r}: {e}") from e

    # ── Factory from Config ───────────────────────────────────────────────────

    @classmethod
    def from_config(cls, cfg: Config) -> "RunStore":
        """Create a RunStore using the state_dir from Config."""
        return cls(cfg.state_dir)
