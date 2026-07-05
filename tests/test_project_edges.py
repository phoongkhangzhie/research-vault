"""test_project_edges.py — unit tests for SR-XPB Slice 1 (edge store).

Acceptance criteria (from SR-XPB spec):
  - Non-existent slug errors.
  - Declaring without --kind rejected.
  - Idempotent: re-declaring same pair+kind is a no-op.
  - --remove deletes; missing edge prints message, returns success.
  - edges lists undirected pairs + kind.
  - Round-trip: add → load → remove → load.
  - peers_of returns the correct set.
  - Pair normalisation: (b, a) and (a, b) are the same edge.
"""
from __future__ import annotations

import pytest

from research_vault.config import Config, reset_config_cache
from research_vault.project_edges import (
    add_edge,
    load_edges,
    peers_of,
    remove_edge,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_cfg():
    reset_config_cache()
    yield
    reset_config_cache()


@pytest.fixture
def two_cfg(tmp_path):
    """Config with two registered projects and a live state_dir."""
    state = tmp_path / "state"
    state.mkdir()
    pa = tmp_path / "project-alpha"
    pb = tmp_path / "project-beta"
    pa.mkdir()
    pb.mkdir()
    raw = {
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(state),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "adapters": {"notifier": "file", "backend": "local", "secrets": "env"},
        "projects": {
            "project-alpha": {"code": "pa", "source_dir": str(pa), "roster": []},
            "project-beta": {"code": "pb", "source_dir": str(pb), "roster": []},
        },
    }
    return Config(raw)


@pytest.fixture
def three_cfg(tmp_path):
    """Config with three registered projects and a live state_dir."""
    state = tmp_path / "state"
    state.mkdir()
    for slug in ("proj-a", "proj-b", "proj-c"):
        (tmp_path / slug).mkdir()
    raw = {
        "instance_root": str(tmp_path),
        "notes_root": str(tmp_path / "notes"),
        "state_dir": str(state),
        "agents_dir": str(tmp_path / ".agents"),
        "tasks_dir": str(tmp_path / "tasks"),
        "control_dir": str(tmp_path / "control"),
        "adapters": {"notifier": "file", "backend": "local", "secrets": "env"},
        "projects": {
            "proj-a": {"code": "a1", "source_dir": str(tmp_path / "proj-a"), "roster": []},
            "proj-b": {"code": "b1", "source_dir": str(tmp_path / "proj-b"), "roster": []},
            "proj-c": {"code": "c1", "source_dir": str(tmp_path / "proj-c"), "roster": []},
        },
    }
    return Config(raw)


# ---------------------------------------------------------------------------
# Normalisation / add / load
# ---------------------------------------------------------------------------

def test_add_edge_basic_and_load(two_cfg):
    """add_edge declares an edge; load_edges returns it."""
    add_edge(two_cfg, "project-alpha", "project-beta", "shares-methodology")
    edges = load_edges(two_cfg)
    assert len(edges) == 1
    e = edges[0]
    # Pair is normalised to sorted order
    assert e["a"] == "project-alpha"
    assert e["b"] == "project-beta"
    assert e["kind"] == "shares-methodology"


def test_normalisation_reversed_order(two_cfg):
    """Adding (b, a) is the same edge as (a, b)."""
    add_edge(two_cfg, "project-beta", "project-alpha", "shared-domain")
    edges = load_edges(two_cfg)
    assert len(edges) == 1
    # Always stored as sorted pair
    assert edges[0]["a"] <= edges[0]["b"]


def test_idempotent_same_kind(two_cfg, capsys):
    """Re-declaring the same pair+kind is a no-op (idempotent)."""
    add_edge(two_cfg, "project-alpha", "project-beta", "same-domain")
    add_edge(two_cfg, "project-alpha", "project-beta", "same-domain")
    edges = load_edges(two_cfg)
    assert len(edges) == 1
    out = capsys.readouterr().out
    assert "idempotent" in out.lower()


def test_update_kind_same_pair(two_cfg):
    """Re-declaring the same pair with a different kind updates the kind."""
    add_edge(two_cfg, "project-alpha", "project-beta", "old-kind")
    add_edge(two_cfg, "project-alpha", "project-beta", "new-kind")
    edges = load_edges(two_cfg)
    assert len(edges) == 1
    assert edges[0]["kind"] == "new-kind"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_require_kind(two_cfg):
    """add_edge raises ValueError when kind is empty."""
    with pytest.raises(ValueError, match="--kind"):
        add_edge(two_cfg, "project-alpha", "project-beta", "")


def test_require_kind_whitespace(two_cfg):
    """add_edge raises ValueError when kind is blank whitespace."""
    with pytest.raises(ValueError, match="--kind"):
        add_edge(two_cfg, "project-alpha", "project-beta", "   ")


def test_nonexistent_slug_raises(two_cfg):
    """add_edge raises KeyError when a slug is not in the registry."""
    with pytest.raises(KeyError):
        add_edge(two_cfg, "project-alpha", "nonexistent-project", "test")


def test_self_edge_raises(two_cfg):
    """add_edge raises ValueError when both slugs are the same."""
    with pytest.raises(ValueError, match="itself"):
        add_edge(two_cfg, "project-alpha", "project-alpha", "loop")


# ---------------------------------------------------------------------------
# remove_edge
# ---------------------------------------------------------------------------

def test_remove_edge(two_cfg):
    """remove_edge removes the declared edge."""
    add_edge(two_cfg, "project-alpha", "project-beta", "test")
    remove_edge(two_cfg, "project-alpha", "project-beta")
    assert load_edges(two_cfg) == []


def test_remove_edge_reversed_order(two_cfg):
    """remove_edge works regardless of argument order (undirected)."""
    add_edge(two_cfg, "project-alpha", "project-beta", "test")
    remove_edge(two_cfg, "project-beta", "project-alpha")
    assert load_edges(two_cfg) == []


def test_remove_missing_edge_is_noop(two_cfg, capsys):
    """remove_edge on an absent edge prints a message and succeeds (no-op)."""
    remove_edge(two_cfg, "project-alpha", "project-beta")  # never declared
    out = capsys.readouterr().out
    assert "no declared edge" in out.lower()
    assert load_edges(two_cfg) == []


# ---------------------------------------------------------------------------
# peers_of
# ---------------------------------------------------------------------------

def test_peers_of_basic(three_cfg):
    """peers_of returns the set of declared peers for a project."""
    add_edge(three_cfg, "proj-a", "proj-b", "sister-experiment")
    add_edge(three_cfg, "proj-a", "proj-c", "shares-methodology")
    peers = peers_of(three_cfg, "proj-a")
    assert peers == {"proj-b", "proj-c"}


def test_peers_of_symmetric(three_cfg):
    """peers_of is symmetric: if a is a peer of b, b is a peer of a."""
    add_edge(three_cfg, "proj-a", "proj-b", "related")
    assert "proj-a" in peers_of(three_cfg, "proj-b")
    assert "proj-b" in peers_of(three_cfg, "proj-a")


def test_peers_of_empty(three_cfg):
    """peers_of returns an empty set when no edges are declared."""
    peers = peers_of(three_cfg, "proj-a")
    assert peers == set()


def test_peers_of_after_remove(three_cfg):
    """peers_of reflects a removed edge."""
    add_edge(three_cfg, "proj-a", "proj-b", "test")
    assert "proj-b" in peers_of(three_cfg, "proj-a")
    remove_edge(three_cfg, "proj-a", "proj-b")
    assert peers_of(three_cfg, "proj-a") == set()


# ---------------------------------------------------------------------------
# Round-trip (atomic write / reload)
# ---------------------------------------------------------------------------

def test_round_trip_persist(three_cfg):
    """Edges persist across a fresh load_edges call (atomic write)."""
    add_edge(three_cfg, "proj-a", "proj-b", "shared-domain")
    add_edge(three_cfg, "proj-b", "proj-c", "sister-experiment")
    reloaded = load_edges(three_cfg)
    assert len(reloaded) == 2
    pairs = {(e["a"], e["b"]) for e in reloaded}
    assert ("proj-a", "proj-b") in pairs
    assert ("proj-b", "proj-c") in pairs


# ---------------------------------------------------------------------------
# Config.project_edges_path accessor
# ---------------------------------------------------------------------------

def test_config_project_edges_path(two_cfg):
    """Config.project_edges_path() returns the expected sidecar path."""
    p = two_cfg.project_edges_path()
    assert p.name == "project_edges.json"
    assert p.parent == two_cfg.state_dir
