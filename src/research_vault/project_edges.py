# SPDX-License-Identifier: AGPL-3.0-or-later
"""project_edges.py — hub-owned cross-project edge store for Research Vault (SR-XPB).

When to use:
  - ``add_edge(cfg, a, b, kind)`` — declare a cross-project edge (hub coordination act).
  - ``remove_edge(cfg, a, b)`` — prune a stale edge.
  - ``peers_of(cfg, slug)`` — return the set of declared peer slugs for a project.
  - ``load_edges(cfg)`` — return all declared edges as structured records.

Design (SR-XPB D1–D5):
  D1: Sidecar JSON edge store at ``state_dir/project_edges.json``.
  D2: Undirected (pair normalised to sorted order); ``kind`` + rationale REQUIRED on declare.
  D3: ``corroborate`` requires ``from_slug``, ``against`` ⊆ peers (enforced in cross_project.py).
  D4: Judge-gated assert — rank narrows, judge confirms, human reviews. Never auto-assert.
  D5: Hub declares edges outright (surfaced via ``rv project edges``; prunable).

Framing: ``rv project relate`` is a **hub coordination act** — the hub has the cross-project
registry overview and grants intentional reach by declaring edges. The human may also declare
or prune edges. The *scientific gate* (corroboration quality) lives downstream on the assertion
(the judge step), NOT here. Declaring an edge says "these projects share a domain where
cross-project reading is meaningful" — not "any hit is valid." Blanket-relating all projects
preserves correctness (the judge still filters) but forfeits the narrowing/efficiency benefit.
Declare on genuine relatedness.

Stdlib only — no third-party deps.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from .config import Config


# ---------------------------------------------------------------------------
# Edge file location
# ---------------------------------------------------------------------------

def _edges_path(cfg: Config) -> Path:
    """Return the path to the edge store JSON file.

    Delegates to ``cfg.project_edges_path()`` — single source of truth for the path.
    """
    return cfg.project_edges_path()


# ---------------------------------------------------------------------------
# Low-level persistence
# ---------------------------------------------------------------------------

def _normalise_pair(a: str, b: str) -> tuple[str, str]:
    """Normalise an undirected project pair to canonical (sorted) order."""
    return (a, b) if a <= b else (b, a)


def _load_raw(cfg: Config) -> list[dict[str, Any]]:
    """Load the raw edge list from disk.  Returns [] if file absent or empty."""
    p = _edges_path(cfg)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return data
    except (json.JSONDecodeError, OSError):
        return []


def _save_raw(cfg: Config, edges: list[dict[str, Any]]) -> None:
    """Atomically write the edge list to disk.

    Atomic write: write to a temp sidecar, then replace, so a crash mid-write
    never leaves a corrupt store.
    """
    p = _edges_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(edges, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_edges(cfg: Config) -> list[dict[str, Any]]:
    """Return all declared edges as structured records.

    Each record has:
        a (str)         — first slug (sorted pair)
        b (str)         — second slug (sorted pair)
        kind (str)      — edge kind / rationale (e.g. "shares-methodology")
    """
    return [dict(e) for e in _load_raw(cfg)]


def add_edge(cfg: Config, a: str, b: str, kind: str) -> None:
    """Declare a cross-project edge between projects ``a`` and ``b``.

    This is a **hub coordination act**: the hub has the registry overview and
    grants intentional cross-project reach.  Human operators may also call this.
    The scientific gate lives downstream (the corroboration judge step).

    Parameters
    ----------
    cfg:
        Loaded Config.  Both slugs are validated against the registry.
    a, b:
        Project slugs to relate.  Normalised to sorted order internally.
    kind:
        Non-empty string describing the edge's purpose / rationale (required).
        Examples: ``"shares-methodology"``, ``"same-domain"``, ``"sister-experiment"``.

    Raises
    ------
    KeyError:
        If ``a`` or ``b`` are not registered project slugs.
    ValueError:
        If ``a == b``, ``kind`` is empty, or the edge already exists.
    """
    if a == b:
        raise ValueError(f"Cannot relate a project to itself: {a!r}")
    if not kind.strip():
        raise ValueError(
            f"--kind is REQUIRED when declaring an edge.  "
            f"Describe the genuine relatedness (e.g. 'shares-methodology')."
        )
    # Validate both slugs exist in the registry
    cfg.project(a)  # raises KeyError with a clear message if absent
    cfg.project(b)

    na, nb = _normalise_pair(a, b)
    edges = _load_raw(cfg)

    # Idempotent: if the same pair+kind already exists, do nothing
    for e in edges:
        if e.get("a") == na and e.get("b") == nb:
            existing_kind = e.get("kind", "")
            if existing_kind == kind:
                print(f"Edge {na!r} ↔ {nb!r} kind={kind!r} already declared (idempotent).")
                return
            # Same pair, different kind — update kind
            e["kind"] = kind
            _save_raw(cfg, edges)
            print(f"Updated edge {na!r} ↔ {nb!r} → kind={kind!r}")
            return

    edges.append({"a": na, "b": nb, "kind": kind})
    _save_raw(cfg, edges)
    print(f"Declared edge {na!r} ↔ {nb!r}  kind={kind!r}")


def remove_edge(cfg: Config, a: str, b: str) -> None:
    """Prune the declared edge between projects ``a`` and ``b``.

    No-op (with a message) if the edge does not exist.

    Parameters
    ----------
    cfg:
        Loaded Config.
    a, b:
        Project slugs whose edge should be removed.  Order does not matter.
    """
    na, nb = _normalise_pair(a, b)
    edges = _load_raw(cfg)
    before = len(edges)
    edges = [e for e in edges if not (e.get("a") == na and e.get("b") == nb)]
    if len(edges) == before:
        print(f"No declared edge between {na!r} and {nb!r} — nothing to remove.")
        return
    _save_raw(cfg, edges)
    print(f"Removed edge {na!r} ↔ {nb!r}")


def peers_of(cfg: Config, slug: str) -> set[str]:
    """Return the set of project slugs that have a declared edge with ``slug``.

    Returns an empty set if the project has no declared edges.

    Parameters
    ----------
    cfg:
        Loaded Config.
    slug:
        The project whose declared peers to return.
    """
    edges = _load_raw(cfg)
    peers: set[str] = set()
    for e in edges:
        a, b = e.get("a", ""), e.get("b", "")
        if a == slug:
            peers.add(b)
        elif b == slug:
            peers.add(a)
    return peers
