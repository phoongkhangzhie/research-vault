"""scaffold.py — shared framework-materialization helpers for init + update.

The single home for the "copy framework-managed files out of the installed
package into a vault" logic.  BOTH ``rv init`` (first scaffold) and ``rv update``
(framework refresh) call these helpers, so a file added to one path can never
be silently missing from the other (the invisible-on-upgrade bug this module
exists to prevent).

Framework-managed STATICS (copied verbatim from package data, hash-tracked):
  - ``CLAUDE.md``        (from ``data/templates/CLAUDE.md.tmpl``)
  - ``QUICKSTART.md``    (from ``data/templates/QUICKSTART.md``)
  - ``doctrine/**``      (from ``data/doctrine/``)

Crew hats (``.claude/agents/<role>.md``) are NOT statics — they are DERIVED by
``build_agents._compose_hat()`` from ``doctrine/`` + build_agents constants.
``rv update`` never diffs/copies a hat; it refreshes ``doctrine/`` then re-runs
``build-agents --target claude-code`` to recompose them.

Package data is loaded via ``importlib.resources`` + ``as_file()`` so the copy
works from a regular wheel install AND a zipped wheel (zipimport-safe).  A
missing package-data file is a HARD ERROR, never a silent skeleton (charter §2).

Stdlib only.
"""
from __future__ import annotations

import importlib.resources
import json
from pathlib import Path
from typing import Iterator

from .hashing import hash_file

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Sidecar manifest filename at the vault root — TRACKED (not gitignored).
#: Records the per-file hashes AS-SHIPPED at the last init/update; the
#: drift-detection substrate for the user-modified policy in ``rv update``.
MANIFEST_NAME = ".rv-manifest.json"

#: Suffix for a user-modified framework file that ``rv update`` backs up
#: before overwriting with the new framework version.
BACKUP_SUFFIX = ".rv-bak"

#: Framework-managed single-file statics: (package-relative source, vault-relative dst).
_MANAGED_STATIC_FILES: list[tuple[str, str]] = [
    ("templates/CLAUDE.md.tmpl", "CLAUDE.md"),
    ("templates/QUICKSTART.md", "QUICKSTART.md"),
]

#: Framework-managed directory trees copied verbatim: (package-relative, vault-relative).
_MANAGED_STATIC_TREES: list[tuple[str, str]] = [
    ("doctrine", "doctrine"),
]

#: USER-OWNED top-level names ``rv update`` must NEVER regenerate or overwrite.
#: ``architecture.md`` is the architect's living per-project map — regenerating
#: it would clobber real content, so it is locked here alongside the runtime
#: state dirs and the config SSOT.
USER_OWNED_NEVER_TOUCH: frozenset[str] = frozenset({
    "notes",
    "projects",
    "control",
    "state",
    "tasks",
    "research_vault.toml",
    "DEVLOG.md",
    "architecture.md",
})


# ---------------------------------------------------------------------------
# Package-data access
# ---------------------------------------------------------------------------

def pkg_data() -> "importlib.resources.abc.Traversable":
    """Return the package-data root Traversable (``src/research_vault/data/``).

    Package-relative so it works from a wheel install, an editable install, or a
    zipimport — there is no ``__file__``-based repo-root path.
    """
    return importlib.resources.files("research_vault") / "data"


def count_files(path: Path) -> int:
    """Count files recursively under a directory."""
    return sum(1 for _ in path.rglob("*") if _.is_file())


# ---------------------------------------------------------------------------
# Managed-static enumeration (the SSOT both init + update consume)
# ---------------------------------------------------------------------------

def iter_managed_statics() -> Iterator[tuple[str, bytes]]:
    """Yield ``(vault_relpath, shipped_bytes)`` for every framework-managed static.

    Covers the single-file statics (CLAUDE.md, QUICKSTART.md) and every file
    under the managed trees (doctrine/**).  This is the SINGLE enumeration both
    ``rv init`` and ``rv update`` consume — a file listed here is materialized by
    both paths, so nothing can be init-only (the invisible-on-upgrade bug).

    Raises ``RuntimeError`` if a declared package-data source is missing (the
    wheel is incomplete) — never silently skips (charter §2).
    """
    data = pkg_data()

    for pkg_rel, vault_rel in _MANAGED_STATIC_FILES:
        with importlib.resources.as_file(data / pkg_rel) as src:
            if not src.is_file():
                raise RuntimeError(
                    f"Package data missing: data/{pkg_rel}. "
                    "The wheel is incomplete — reinstall research-vault."
                )
            yield vault_rel, src.read_bytes()

    for pkg_rel, vault_rel in _MANAGED_STATIC_TREES:
        with importlib.resources.as_file(data / pkg_rel) as src_dir:
            if not src_dir.is_dir():
                raise RuntimeError(
                    f"Package data missing: data/{pkg_rel}/. "
                    "The wheel is incomplete — reinstall research-vault."
                )
            for f in sorted(p for p in src_dir.rglob("*") if p.is_file()):
                rel = f.relative_to(src_dir).as_posix()
                yield f"{vault_rel}/{rel}", f.read_bytes()


def write_managed_statics(target: Path) -> dict[str, str]:
    """Write every framework-managed static into ``target`` (overwriting).

    Returns ``{vault_relpath: "sha256:<hex>"}`` for the files written — the
    as-shipped hash map for the ``.rv-manifest.json`` sidecar.  Used by
    ``rv init`` (fresh scaffold) and reused by ``rv update`` via the plan path.
    """
    hashes: dict[str, str] = {}
    for vault_rel, content in iter_managed_statics():
        dst = target / vault_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(content)
        hashes[vault_rel] = _hash_bytes(content)
    return hashes


# ---------------------------------------------------------------------------
# Hashing (as-shipped bytes → canonical "sha256:<hex>")
# ---------------------------------------------------------------------------

def _hash_bytes(content: bytes) -> str:
    """Hash bytes to the canonical ``sha256:<hex>`` format (matches hashing.hash_file)."""
    import hashlib
    return "sha256:" + hashlib.sha256(content).hexdigest()


def hash_path(path: Path) -> str:
    """Hash a file on disk to the canonical ``sha256:<hex>`` format."""
    return hash_file(path)


# ---------------------------------------------------------------------------
# Version helpers (stdlib tuple split — no `packaging` dep in core)
# ---------------------------------------------------------------------------

def package_version() -> str:
    """Return the installed research-vault version.

    Prefers the installed distribution metadata; falls back to the in-tree
    ``__version__`` (editable / source runs where metadata may lag).
    """
    try:
        from importlib.metadata import version as _dist_version
        return _dist_version("research-vault")
    except Exception:
        from . import __version__
        return __version__


def version_tuple(v: str) -> tuple[int, ...]:
    """Split a dotted version string into an int tuple for comparison.

    Numeric leading components only (``0.1.0`` → ``(0, 1, 0)``); a non-numeric
    or suffixed component (``1.2.0rc1``) is truncated at the first non-integer
    part, which is sufficient for the update nudge's "newer package" check
    without pulling in the ``packaging`` dependency.
    """
    parts: list[int] = []
    for chunk in str(v).split("."):
        num = ""
        for ch in chunk:
            if ch.isdigit():
                num += ch
            else:
                break
        if num == "":
            break
        parts.append(int(num))
    return tuple(parts)


def version_lt(a: str, b: str) -> bool:
    """Return True if version ``a`` is strictly older than version ``b``."""
    return version_tuple(a) < version_tuple(b)


# ---------------------------------------------------------------------------
# Manifest read / write
# ---------------------------------------------------------------------------

def manifest_path(target: Path) -> Path:
    """Return the ``.rv-manifest.json`` path at the vault root."""
    return target / MANIFEST_NAME


def read_manifest(target: Path) -> dict:
    """Read the ``.rv-manifest.json`` sidecar, or ``{}`` if absent/unreadable."""
    p = manifest_path(target)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_manifest(target: Path, framework_version: str, managed: dict[str, str]) -> None:
    """Write the ``.rv-manifest.json`` sidecar (framework_version + per-file hashes).

    The file carries a leading ``_comment`` marking it machine-managed so a human
    reading the vault knows not to hand-edit it.
    """
    payload = {
        "_comment": (
            "Machine-managed by `rv init` / `rv update`. Records per-file hashes "
            "of framework-managed files AS-SHIPPED. Do not hand-edit."
        ),
        "framework_version": framework_version,
        "managed": dict(sorted(managed.items())),
    }
    manifest_path(target).write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Crew-hat helpers (DERIVED files — recomposed by build_agents, hash-tracked)
# ---------------------------------------------------------------------------

def hat_relpaths(target: Path) -> list[str]:
    """Return the vault-relative paths of the composed crew-hat files.

    Derived from ``build_agents._VAULT_ROLES`` so it never drifts from the
    actual set of roles ``build-agents --target claude-code`` emits.
    """
    from .build_agents import _VAULT_ROLES
    return [f".claude/agents/{role}.md" for role in _VAULT_ROLES]


def hash_hats(target: Path) -> dict[str, str]:
    """Hash the composed crew-hat files present under ``.claude/agents/``.

    Returns ``{vault_relpath: "sha256:<hex>"}`` for every expected hat that
    exists on disk (a missing hat is simply absent from the map — the caller's
    plan handles NEW/missing separately).
    """
    out: dict[str, str] = {}
    for rel in hat_relpaths(target):
        p = target / rel
        if p.is_file():
            out[rel] = hash_file(p)
    return out


# ---------------------------------------------------------------------------
# [meta] block upsert / read (surgical — never rewrites the user-owned TOML)
# ---------------------------------------------------------------------------

_META_HEADER = "[meta]"


def upsert_meta_block(
    toml_text: str,
    *,
    framework_version: str,
    scaffolded_at: str,
    updated_at: str,
) -> str:
    """Insert or replace the ``[meta]`` block in a research_vault.toml text.

    The ``[meta]`` block is the only part of the (otherwise USER-OWNED)
    research_vault.toml that ``rv init`` / ``rv update`` write.  This helper
    replaces an EXISTING ``[meta]`` section (from its header up to the next
    ``[section]`` header or EOF) or APPENDS one if absent — user content in
    every other section is preserved byte-for-byte.

    Returns the new TOML text.
    """
    block = (
        f"{_META_HEADER}\n"
        "# Machine-managed by `rv init` / `rv update` — framework version stamp.\n"
        "# Edit real projects/paths above; leave this block to the tooling.\n"
        f'framework_version = "{framework_version}"\n'
        f'scaffolded_at = "{scaffolded_at}"\n'
        f'updated_at = "{updated_at}"\n'
    )

    lines = toml_text.splitlines(keepends=True)
    start = None
    for i, line in enumerate(lines):
        if line.strip() == _META_HEADER:
            start = i
            break

    if start is None:
        # Append (ensure a trailing blank-line separator).
        sep = "" if toml_text.endswith("\n\n") else ("\n" if toml_text.endswith("\n") else "\n\n")
        return toml_text + sep + block

    # Find the end of the block: next top-level [section] header, or EOF.
    end = len(lines)
    for j in range(start + 1, len(lines)):
        stripped = lines[j].lstrip()
        if stripped.startswith("[") and not stripped.startswith("#"):
            end = j
            break
    new_lines = lines[:start] + [block] + lines[end:]
    return "".join(new_lines)


def read_meta(toml_text: str) -> dict[str, str]:
    """Extract the ``[meta]`` block's scalar string fields from TOML text.

    A minimal stdlib parser (no tomllib round-trip needed) — reads
    ``framework_version`` / ``scaffolded_at`` / ``updated_at`` from the
    ``[meta]`` section.  Returns ``{}`` if there is no ``[meta]`` block.
    """
    out: dict[str, str] = {}
    in_meta = False
    for line in toml_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and not stripped.startswith("#"):
            in_meta = stripped == _META_HEADER
            continue
        if not in_meta or not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            k, _, v = stripped.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out
