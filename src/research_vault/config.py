# SPDX-License-Identifier: AGPL-3.0-or-later
"""config.py — the config plane SSOT for Research Vault.

When to use: import `load_config()` whenever a verb needs to resolve a path or adapter.
Every data path in Research Vault reads from config — zero hardcoded paths, zero codenames.

Config file: `research_vault.toml` (TOML format) in the instance root.

Resolution precedence (highest → lowest):
  1. ``--config PATH`` CLI flag  (wired via RESEARCH_VAULT_CONFIG env by the CLI)
  2. ``RESEARCH_VAULT_CONFIG`` env var (absolute path to the TOML file)
  3. CWD walk-up — search current directory and parents for ``research_vault.toml``
  4. XDG user config — ``$XDG_CONFIG_HOME/research_vault/config.toml``, falling
     back to ``~/.config/research_vault/config.toml`` when XDG_CONFIG_HOME is
     unset. This is the discovery level that fixes the out-of-repo case: a
     `rv` call from anywhere on the machine (no repo-local research_vault.toml
     underfoot, no explicit --config/env) still resolves the operator's
     vault registry if it's symlinked/copied to the XDG path.
  5. None found — falls through to zero-config defaults (empty registry,
     instance_root = cwd).

Both ``--config`` and ``RESEARCH_VAULT_CONFIG`` error loudly when the path does not
exist, rather than silently falling through to CWD walk-up / XDG. Steps 3–5 are
soft: not finding a file at that step just falls through to the next.

Run ``rv --show-instance`` to see which config file (if any) was resolved and
via which of the levels above (``--config`` / ``env`` / ``walk-up`` / ``xdg`` /
``none``) — resolution is meant to be debuggable, never magic.

Multi-project registry: config["projects"] is a dict mapping project-slug → project record.
Verb invocations are project-scoped: `rv task <project> …` resolves paths via the registry.

Stdlib only.
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Location resolution
# ---------------------------------------------------------------------------

def _xdg_config_path() -> Path:
    """Return the XDG user-config location for research_vault (may not exist).

    ``$XDG_CONFIG_HOME/research_vault/config.toml``, falling back to
    ``~/.config/research_vault/config.toml`` per the XDG base-dir spec.
    Stdlib only — no `platformdirs` dependency (research-vault ships dep-light
    by design; see the module docstring).
    """
    xdg_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_home).expanduser() if xdg_home else Path.home() / ".config"
    return base / "research_vault" / "config.toml"


def _locate_config_with_source() -> tuple[Path | None, str]:
    """Locate the research_vault.toml file, and report *how* it was found.

    Search order (first hit wins):
    1. RESEARCH_VAULT_CONFIG env var (must point to an existing file)
    2. CWD walk-up — current directory and parents
    3. XDG user config — see `_xdg_config_path()`
    Returns (None, "none") if nothing is found (caller decides whether to
    error or fall through to defaults).

    Source labels: "env" | "walk-up" | "xdg" | "none". The CLI's `--config`
    flag is injected into RESEARCH_VAULT_CONFIG upstream (see cli.main), so
    it is indistinguishable from a real env var at this layer — the CLI
    itself relabels it "--config" for `--show-instance` display, since it
    alone knows whether the flag was passed.
    """
    env_override = os.environ.get("RESEARCH_VAULT_CONFIG")
    if env_override:
        p = Path(env_override)
        if p.is_file():
            return p, "env"
        # Explicit override that doesn't exist — surface loudly rather than fall through
        raise FileNotFoundError(
            f"RESEARCH_VAULT_CONFIG={env_override!r} does not exist or is not a file"
        )

    # Walk upward from cwd
    cwd = Path.cwd()
    for directory in [cwd, *cwd.parents]:
        candidate = directory / "research_vault.toml"
        if candidate.is_file():
            return candidate, "walk-up"

    xdg_path = _xdg_config_path()
    if xdg_path.is_file():
        return xdg_path, "xdg"

    return None, "none"


def _find_config_path() -> Path | None:
    """Locate the research_vault.toml file (path only — no source label).

    Back-compat wrapper around `_locate_config_with_source()` for callers
    (project.py) that only need the path, not how it was resolved.
    """
    path, _source = _locate_config_with_source()
    return path


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
        # Human-presence enforcement at rv dag approve.
        # enforce=true (default): require TTY keystroke or valid RV_APPROVER_TOKEN.
        # token_fingerprint: sha256 of the provisioned token (written by rv approval setup).
        # enforce_sig: HMAC keyed on the token (written by rv approval disable — Slice 3).
        "approval": {
            "enforce": True,
            "token_fingerprint": "",
            "enforce_sig": "",
        },
        # Observability layer for the provided ModelClient.
        # backend: local (zero-infra JSONL default) | weave (Plane-A traces, needs
        #   the [observability] extra) | langfuse (adopter's own install) | none.
        # run_logging: Plane-B classic W&B run (rv wandb pull-readable) — opt-in.
        # wandb_project: weave/run target ("entity/project"); empty → the compute
        #   manifest results.wandb block is the SSOT.
        "observability": {
            "backend": "local",
            "run_logging": False,
            "wandb_project": "",
        },
        # OA-fulltext-enrichment (tier 1, 0.3.0): unpaywall_email is a REQUIRED
        # contact-info query param on Unpaywall's API terms (a config value,
        # not a credential/secret — stays reproducible). Absent -> the
        # unpaywall provider self-skips and says so in the run log, never
        # silently. pdf_backend is currently informational only (pymupdf is
        # the sole core PDF backend as of 0.3.0 — no adopter-selectable
        # alternates shipped yet).
        "fulltext": {
            "unpaywall_email": "",
            "pdf_backend": "pymupdf",
        },
    }


# ---------------------------------------------------------------------------
# Config loading + validation
# ---------------------------------------------------------------------------

def _merge(base: dict, override: dict) -> dict:
    """Deep merge: override wins at every level; nested dicts are recursively merged.

    Forward-flag fix: the original shallow (one-level) merge drops
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
    # datasets_root is included here so a toml-set value is expanded correctly.
    # When absent from the toml, Config.__init__ derives it from notes_root/datasets.
    path_keys = ("notes_root", "state_dir", "agents_dir", "tasks_dir", "control_dir",
                 "datasets_root", "literature_root", "concepts_root")
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


def resolve_repo_root(source_dir: str | Path) -> Path:
    """Resolve a project's repo-root directory from its `source_dir`.

    Two conventions coexist (doctrine/project-structure.md):

    - **CS-project convention** ("repo root IS the vault"): `source_dir =
      <repo>/notes` and root-level artifacts (`pointers.md`, `architecture.md`)
      live at `<repo>` = `source_dir.parent`. Structural marker: `source_dir`'s
      basename is exactly `"notes"` (P1: "source_dir = <repo>/notes").
    - **Flat/legacy convention**: `source_dir` IS the repo root; those
      artifacts live directly under `source_dir`.

    Never guesses from file existence (a flat project may simply lack
    pointers.md yet) — the basename marker is grounded in how `source_dir`
    is configured, not in what happens to be on disk.
    """
    p = Path(source_dir)
    return p.parent if p.name == "notes" else p


# ---------------------------------------------------------------------------
# Cross-bundle backbone links — rv's OKF extension
# ---------------------------------------------------------------------------
#
# The Open Knowledge Format spec (v0.1) is one-bundle-scoped: it defines
# `/section/slug.md` links resolved against a single bundle root, and is
# explicitly silent on cross-bundle references. Each rv bundle (the shared
# literature store, the shared datasets store, each project's own notes
# tree) is individually OKF-conformant; the `okf:<bundle>/<path>.md` URI
# scheme below is rv's documented extension for the one thing every
# project-scoped overlay needs: a pointer OUT of its own bundle at a shared
# store. A plain OKF reader that doesn't know the `okf:` scheme tolerates it
# as an unrecognized (but well-formed) link target — never a parse failure.
_OKF_URI_RE = re.compile(r"^okf:([A-Za-z0-9_.\-]+)/(.+\.md)$")


class Config:
    """Resolved Research Vault configuration.

    Attributes mirror the TOML schema. Access paths as Path objects via the
    `path_*` properties; raw strings via direct attribute access.
    """

    def __init__(
        self,
        raw: dict[str, Any],
        config_file: Path | None = None,
        config_source: str = "none",
    ):
        self._raw = raw
        self.config_file = config_file
        # How config_file was resolved: "env" | "walk-up" | "xdg" | "none".
        # ("--config" is relabeled by the CLI, which alone knows the flag was
        # passed — see _locate_config_with_source()'s docstring.)
        self.config_source = config_source
        self.instance_root = Path(raw["instance_root"])
        self.notes_root = Path(raw["notes_root"])
        self.state_dir = Path(raw["state_dir"])
        self.agents_dir = Path(raw["agents_dir"])
        self.tasks_dir = Path(raw["tasks_dir"])
        self.control_dir = Path(raw["control_dir"])
        # datasets_root — shared cross-project dataset provenance store.
        # Default: notes_root/datasets (derived, not stored in _default_config so
        # that a custom notes_root still yields notes_root/datasets by default).
        # Override in research_vault.toml: datasets_root = "/shared/datasets"
        if "datasets_root" in raw:
            self.datasets_root = Path(raw["datasets_root"])
        else:
            self.datasets_root = self.notes_root / "datasets"
        # literature_root — the central, cross-project two-layer literature
        # store. Mirrors datasets_root exactly: default
        # notes_root/literature (hub/instance level, sibling of
        # datasets_root); override in research_vault.toml:
        # literature_root = "/shared/literature". This is the store's ONE
        # location — cfg.project_notes_dir(project)/literature/ holds the
        # thin per-project overlays, never the core.
        if "literature_root" in raw:
            self.literature_root = Path(raw["literature_root"])
        else:
            self.literature_root = self.notes_root / "literature"
        # concepts_root — the shared-canonical cross-project concepts store.
        # Mirrors datasets_root exactly (a plain shared bundle, ONE note per
        # concept, no per-project overlay — unlike literature_root, which is
        # still two-layer). Default: notes_root/concepts; override in
        # research_vault.toml: concepts_root = "/shared/concepts".
        if "concepts_root" in raw:
            self.concepts_root = Path(raw["concepts_root"])
        else:
            self.concepts_root = self.notes_root / "concepts"
        self.adapters: dict[str, str] = raw.get("adapters", {})
        # Observability config block (backend/run_logging/wandb_project).
        # Empty dict when absent so callers can .get(...) with defaults.
        self.observability: dict[str, Any] = raw.get("observability", {})
        # OA-fulltext-enrichment: [fulltext] config block (see _default_config).
        self.fulltext: dict[str, Any] = raw.get("fulltext", {})
        self.projects: dict[str, dict[str, Any]] = raw.get("projects", {})
        # Slug-collision guard — reject project slugs that collide
        # with OKF type names. Such slugs silently shadow note routing (the project
        # notes dir becomes indistinguishable from the shared OKF type root).
        #
        # Call-time import (not module-level) avoids circular import: note.py imports
        # Config from this module at module load, but Config.__init__ only runs after
        # both modules are fully loaded — by then note.py is in sys.modules, so the
        # import resolves without recursion. This makes the guard a TRUE SSOT consumer
        # of note.OKF_TYPES | note.OKF_SHARED_TYPES — no hardcoded fork that can drift.
        from .note import OKF_TYPES as _OKF_TYPES, OKF_SHARED_TYPES as _OKF_SHARED_TYPES  # call-time; see comment above
        _reserved = _OKF_TYPES | _OKF_SHARED_TYPES
        _colliding = [s for s in self.projects if s in _reserved]
        if _colliding:
            bad = ", ".join(repr(s) for s in sorted(_colliding))
            raise ValueError(
                f"Project slug(s) {bad} collide with reserved OKF type names "
                f"({sorted(_reserved)}). OKF type names are reserved "
                f"routing identifiers — choose a different project slug."
            )

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

    def project_repo_root(self, slug: str) -> Path:
        """Resolve the project's repo root — where root-level artifacts
        (`pointers.md`, `architecture.md`) live. See `resolve_repo_root`."""
        return resolve_repo_root(self.project_notes_dir(slug))

    def project_devlog(self, slug: str) -> Path:
        """Resolve the DEVLOG.md path for a project.

        DEVLOG.md is a repo-root doctrine file — same convention as
        pointers.md/architecture.md (see `resolve_repo_root`). For the
        CS-project convention (`source_dir = <repo>/notes`) it lives at the
        repo root (`source_dir.parent`), not under `source_dir` itself.
        """
        proj = self.project(slug)
        src = Path(proj.get("source_dir", self.notes_root / slug))
        return resolve_repo_root(src) / "DEVLOG.md"

    def all_project_slugs(self) -> list[str]:
        """Return all registered project slugs."""
        return list(self.projects.keys())

    def project_edges_path(self) -> "Path":
        """Return the path to the edge store JSON file (state_dir/project_edges.json)."""
        return self.state_dir / "project_edges.json"

    # --- cross-bundle backbone registry ---

    def bundle_registry(self) -> dict[str, Path]:
        """The named bundle -> root-path map for `resolve_bundle_link`.

        Every instance-level shared store gets a name (``literature`` ->
        ``literature_root``, ``datasets`` -> ``datasets_root``); every
        registered project gets its own slug -> ``project_notes_dir(slug)``.
        This is the ONE registry every cross-bundle `okf:` link resolves
        through — a general rv primitive, not literature-specific (see
        note-conventions.md's OKF-extension section).
        """
        registry: dict[str, Path] = {
            "literature": self.literature_root,
            "datasets": self.datasets_root,
            "concepts": self.concepts_root,
        }
        for slug in self.projects:
            registry[slug] = self.project_notes_dir(slug)
        return registry

    def shared_type_root(self, note_type: str) -> Path:
        """Resolve the shared-canonical root for a shared OKF type.

        SSOT for per-type shared-store resolution — ``"datasets"`` ->
        ``datasets_root``, ``"concepts"`` -> ``concepts_root``. Extend the
        mapping here (never a hardcoded fork at a call site) when a new
        shared type is added. Callers gate on ``note_type in
        note.OKF_SHARED_TYPES`` first; this raises ``KeyError`` for a
        non-shared type.
        """
        return {
            "datasets": self.datasets_root,
            "concepts": self.concepts_root,
        }[note_type]

    def resolve_bundle_link(self, link: str) -> Path | None:
        """Resolve an rv cross-bundle ``okf:<bundle>/<path>.md`` link to an
        absolute ``Path`` via the bundle registry.

        Returns ``None`` on ANY unresolvable link — malformed URI, unknown
        bundle name, or a well-formed pointer to a file that does not exist
        on disk. Never raises: this is the resolution primitive underneath
        every cross-bundle reader; the caller decides how loudly to surface
        an unresolved link (charter §2 — surfaced by the caller, never
        silently swallowed at this layer either).
        """
        link = (link or "").strip()
        m = _OKF_URI_RE.match(link)
        if not m:
            return None
        bundle_name, rel_path = m.groups()
        root = self.bundle_registry().get(bundle_name)
        if root is None:
            return None
        resolved = root / rel_path
        if not resolved.is_file():
            return None
        return resolved

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
    config_path, config_source = _locate_config_with_source()

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
    _CACHE = Config(cfg, config_path, config_source)
    return _CACHE


def reset_config_cache() -> None:
    """Reset the module-level cache. Use in tests that mutate the config path."""
    global _CACHE
    _CACHE = None
