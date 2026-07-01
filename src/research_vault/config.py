"""config.py — the config plane SSOT for Research Vault.

When to use: import `load_config()` whenever a verb needs to resolve a path or adapter.
Every data path in Research Vault reads from config — zero hardcoded paths, zero codenames.

Config file: `research_vault.toml` (TOML format) in the instance root.
Override via: `RESEARCH_VAULT_CONFIG` env var (absolute path to the TOML file).

Multi-project registry: config["projects"] is a dict mapping project-slug → project record.
Verb invocations are project-scoped: `rv task <project> …` resolves paths via the registry.

Stdlib only.
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Location resolution
# ---------------------------------------------------------------------------

def _find_config_path() -> Path | None:
    """Locate the research_vault.toml file.

    Search order:
    1. RESEARCH_VAULT_CONFIG env var (must be an absolute path)
    2. Current working directory
    3. Parent directories up to the filesystem root
    Returns None if not found (caller decides whether to error or use defaults).
    """
    env_override = os.environ.get("RESEARCH_VAULT_CONFIG")
    if env_override:
        p = Path(env_override)
        if p.is_file():
            return p
        # Explicit override that doesn't exist — surface loudly rather than fall through
        raise FileNotFoundError(
            f"RESEARCH_VAULT_CONFIG={env_override!r} does not exist or is not a file"
        )

    # Walk upward from cwd
    cwd = Path.cwd()
    for directory in [cwd, *cwd.parents]:
        candidate = directory / "research_vault.toml"
        if candidate.is_file():
            return candidate

    return None


# ---------------------------------------------------------------------------
# TOML parsing (stdlib — no tomllib < 3.11 fallback needed; we require 3.12)
# ---------------------------------------------------------------------------

def _load_toml(path: Path) -> dict[str, Any]:
    """Load a TOML file using the stdlib tomllib (Python 3.11+)."""
    import tomllib
    with open(path, "rb") as f:
        return tomllib.load(f)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

def _default_config() -> dict[str, Any]:
    """Return the zero-config defaults (usable without a research_vault.toml).

    Sub-paths are relative so that _expand_paths() resolves them against the
    resolved instance_root. This means a config that sets ONLY instance_root
    will have all derived paths (tasks_dir, control_dir, notes_root, state_dir)
    derived from that root — not from cwd(), which would violate the config-SSOT
    guarantee.
    """
    return {
        "instance_root": str(Path.cwd()),
        "notes_root": "notes",
        "state_dir": "state",
        "agents_dir": ".agents",
        "tasks_dir": "tasks",
        "control_dir": "control",
        "adapters": {
            "notifier": "file",
            "backend": "local",
            "secrets": "env",
        },
        "projects": {},
    }


# ---------------------------------------------------------------------------
# Config loading + validation
# ---------------------------------------------------------------------------

def _merge(base: dict, override: dict) -> dict:
    """Deep merge: override wins at every level; nested dicts are recursively merged.

    ARGUS SR-1 forward-flag fix: the original shallow (one-level) merge drops
    sibling defaults when a depth-2 key is overridden (e.g.
    ``[adapters.backend.slurm]`` overriding ``[adapters.backend]`` would lose
    ``adapters.notifier``). Full recursion prevents that.

    Key mapping note (camelCase → snake_case for future projects.json backfill):
      projects.json (camelCase)  →  research_vault.toml (snake_case)
      sourceDir                  →  source_dir
      tasksDir                   →  tasks_dir
      controlFile                →  control_file
      This is the one-pass rename needed when backfilling live instances.
    """
    merged = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _merge(merged[k], v)
        else:
            merged[k] = v
    return merged


def _expand_paths(cfg: dict, instance_root: Path) -> dict:
    """Expand ~ and make relative paths absolute against instance_root."""
    path_keys = ("notes_root", "state_dir", "agents_dir", "tasks_dir", "control_dir")
    for key in path_keys:
        if key in cfg:
            p = Path(cfg[key]).expanduser()
            if not p.is_absolute():
                p = instance_root / p
            cfg[key] = str(p)
    # Expand per-project paths
    for project_slug, proj in cfg.get("projects", {}).items():
        for pkey in ("source_dir", "agents", "tasks_dir", "control_file"):
            if pkey in proj:
                p = Path(proj[pkey]).expanduser()
                if not p.is_absolute():
                    p = instance_root / p
                proj[pkey] = str(p)
    return cfg


class Config:
    """Resolved Research Vault configuration.

    Attributes mirror the TOML schema. Access paths as Path objects via the
    `path_*` properties; raw strings via direct attribute access.
    """

    def __init__(self, raw: dict[str, Any], config_file: Path | None = None):
        self._raw = raw
        self.config_file = config_file
        self.instance_root = Path(raw["instance_root"])
        self.notes_root = Path(raw["notes_root"])
        self.state_dir = Path(raw["state_dir"])
        self.agents_dir = Path(raw["agents_dir"])
        self.tasks_dir = Path(raw["tasks_dir"])
        self.control_dir = Path(raw["control_dir"])
        self.adapters: dict[str, str] = raw.get("adapters", {})
        self.projects: dict[str, dict[str, Any]] = raw.get("projects", {})

    # --- project registry helpers ---

    def project(self, slug: str) -> dict[str, Any]:
        """Return the project record for slug, or raise KeyError."""
        if slug not in self.projects:
            known = ", ".join(self.projects) or "(none)"
            raise KeyError(
                f"Unknown project {slug!r}. Known projects: {known}"
            )
        return self.projects[slug]

    def project_tasks_dir(self, slug: str) -> Path:
        """Resolve the tasks directory for a project.

        Falls back to config.tasks_dir / slug if the project has no explicit tasks_dir.
        """
        proj = self.project(slug)
        if "tasks_dir" in proj:
            return Path(proj["tasks_dir"])
        return self.tasks_dir / slug

    def project_control_file(self, slug: str) -> Path:
        """Resolve the control file path for a project."""
        proj = self.project(slug)
        if "control_file" in proj:
            return Path(proj["control_file"])
        return self.control_dir / f"{slug}.md"

    def project_notes_dir(self, slug: str) -> Path:
        """Resolve the notes directory for a project."""
        proj = self.project(slug)
        if "source_dir" in proj:
            return Path(proj["source_dir"])
        return self.notes_root / slug

    def project_devlog(self, slug: str) -> Path:
        """Resolve the DEVLOG.md path for a project."""
        proj = self.project(slug)
        src = Path(proj.get("source_dir", self.notes_root / slug))
        return src / "DEVLOG.md"

    def all_project_slugs(self) -> list[str]:
        """Return all registered project slugs."""
        return list(self.projects.keys())

    def __repr__(self) -> str:  # pragma: no cover
        return f"Config(instance_root={self.instance_root!r}, projects={list(self.projects)!r})"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_CACHE: Config | None = None


def load_config(*, reload: bool = False) -> Config:
    """Load (and cache) the Research Vault config.

    Call with reload=True in tests or when the config path changes.
    """
    global _CACHE
    if _CACHE is not None and not reload:
        return _CACHE

    defaults = _default_config()
    config_path = _find_config_path()

    if config_path is None:
        cfg = defaults
        instance_root = Path(defaults["instance_root"])
    else:
        raw = _load_toml(config_path)
        instance_root = Path(raw.get("instance_root", config_path.parent)).expanduser()
        if not instance_root.is_absolute():
            instance_root = config_path.parent / instance_root
        defaults["instance_root"] = str(instance_root)
        cfg = _merge(defaults, raw)

    cfg = _expand_paths(cfg, instance_root if config_path else Path(defaults["instance_root"]))
    _CACHE = Config(cfg, config_path)
    return _CACHE


def reset_config_cache() -> None:
    """Reset the module-level cache. Use in tests that mutate the config path."""
    global _CACHE
    _CACHE = None
