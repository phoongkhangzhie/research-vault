# SPDX-License-Identifier: AGPL-3.0-or-later
"""dag — DAG-orchestration primitives for Research Vault (SR-3).

Sub-modules:
  schema  — manifest loading + validation (JSON format, node types, edge kinds)
  walker  — pure compute_frontier (PURE: same inputs → same outputs, no I/O)
  store   — atomic file state for run records
  verbs   — CLI verb implementations (dag run/tick/complete/approve/add/insert/status)

Entry point for the `rv dag` verb: research_vault.dag.verbs
"""
from .schema import load_manifest, validate_manifest, ManifestError, NODE_TYPES, EDGE_KINDS
from .walker import compute_frontier, FrontierNode, TERMINAL_STATUSES
from .store import RunStore, RunState

__all__ = [
    "load_manifest",
    "validate_manifest",
    "ManifestError",
    "NODE_TYPES",
    "EDGE_KINDS",
    "compute_frontier",
    "FrontierNode",
    "TERMINAL_STATUSES",
    "RunStore",
    "RunState",
]
