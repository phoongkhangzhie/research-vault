"""project.py — config-registry verbs for Research Vault.

When to use: ``rv project add <name>`` registers a new project into the
multi-project TOML config registry (the config SSOT). Additional project
management verbs (list, show, remove) are stubs for future SRs.

Design constraints honored here:
  - VALIDATE: the entry is schema-checked before writing.
  - WRITE MINIMALLY: only the new [projects.<name>] section is appended to the
    TOML file. The rest of the file is byte-unchanged (no whole-file re-dump).
  - IDEMPOTENT: a duplicate name OR code raises a clear error; never silently
    clobbers an existing entry.
  - CANONICAL form: field order is fixed (name, code, source_dir, roster,
    disclosure) — matches what SR-4's crew reader expects.
  - ARGUS SR-1 FORWARD-FLAG: roster, code, and disclosure are written from
    day one so SR-4 reads an existing field with no registry re-migration.

Stdlib only — no third-party deps.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

from .config import Config, load_config, _find_config_path, _load_toml

# ---------------------------------------------------------------------------
# Registry schema
# ---------------------------------------------------------------------------

# Required fields on every project record
_REQUIRED_FIELDS = ("name", "code", "source_dir")

# Optional fields with their defaults (used for canonical form)
_OPTIONAL_FIELDS = {
    "roster": [],
    "disclosure": "private",
}

# Valid disclosure values
_VALID_DISCLOSURE = frozenset({"private", "public"})

# Project name: lowercase letters, digits, hyphens only; must start with a letter
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")

# Project code: short identifier, same character set
_CODE_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def _validate_entry(name: str, code: str, source_dir: str,
                    roster: list[str], disclosure: str) -> None:
    """Raise ValueError with a clear message if any field fails validation."""
    if not _NAME_RE.match(name):
        raise ValueError(
            f"Invalid project name {name!r}. "
            "Must start with a lowercase letter, then only lowercase letters, digits, hyphens."
        )
    if not _CODE_RE.match(code):
        raise ValueError(
            f"Invalid project code {code!r}. "
            "Must start with a lowercase letter, then only lowercase letters, digits, hyphens."
        )
    if not source_dir.strip():
        raise ValueError("source_dir must not be empty.")
    if disclosure not in _VALID_DISCLOSURE:
        raise ValueError(
            f"Invalid disclosure {disclosure!r}. Must be one of: {sorted(_VALID_DISCLOSURE)}"
        )
    for role in roster:
        if not role.strip():
            raise ValueError(f"Roster role name must not be blank: {role!r}")


def _read_existing_projects(config_path: Path) -> dict[str, dict[str, Any]]:
    """Load the current [projects.*] registry from the TOML file.

    Returns a dict mapping project-slug → record dict.
    """
    raw = _load_toml(config_path)
    return raw.get("projects", {})


def _check_no_duplicate(projects: dict[str, Any], name: str, code: str) -> None:
    """Raise ValueError if name or code already exists in the registry."""
    if name in projects:
        raise ValueError(
            f"Project name {name!r} already exists in the registry. "
            "Use a different name, or edit the config directly to update it."
        )
    existing_codes = {
        slug: rec.get("code", "")
        for slug, rec in projects.items()
        if isinstance(rec, dict)
    }
    for slug, existing_code in existing_codes.items():
        if existing_code == code:
            raise ValueError(
                f"Project code {code!r} is already in use by {slug!r}. "
                "Each project must have a unique code."
            )


def _toml_string(val: str) -> str:
    """Render a Python string as a TOML basic string (double-quoted, escaping \\ and \")."""
    escaped = val.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_project_section(
    name: str,
    code: str,
    source_dir: str,
    roster: list[str],
    disclosure: str,
) -> str:
    """Render the canonical TOML section for a new project entry.

    Field order is fixed: code, source_dir, roster, disclosure.
    The section header is ``[projects.<name>]``.
    This is the EMIT CANONICAL FORM requirement — SR-4 can rely on this order.
    """
    lines = [f"\n[projects.{name}]"]
    lines.append(f"code = {_toml_string(code)}")
    lines.append(f"source_dir = {_toml_string(source_dir)}")
    if roster:
        roster_toml = "[" + ", ".join(_toml_string(r) for r in roster) + "]"
        lines.append(f"roster = {roster_toml}")
    else:
        lines.append("roster = []")
    lines.append(f"disclosure = {_toml_string(disclosure)}")
    lines.append("")  # trailing newline after section
    return "\n".join(lines)


def cmd_add(
    name: str,
    code: str,
    source_dir: str,
    roster: list[str],
    disclosure: str,
    *,
    config_path: Path | None = None,
) -> None:
    """Register a new project entry in the TOML config.

    Performs schema validation, duplicate check, then APPENDS the new
    [projects.<name>] section to the config file — without reformatting
    the rest of the file.

    Raises ValueError on invalid input or duplicate name/code.
    Raises FileNotFoundError if no config file is found.
    """
    # Normalize source_dir
    source_dir = str(Path(source_dir).expanduser())

    # Validate fields
    _validate_entry(name, code, source_dir, roster, disclosure)

    # Locate the config file
    if config_path is None:
        config_path = _find_config_path()
    if config_path is None:
        raise FileNotFoundError(
            "No research_vault.toml found. "
            "Create one with `rv project init` or set RESEARCH_VAULT_CONFIG."
        )

    # Load current registry and check for duplicates
    existing = _read_existing_projects(config_path)
    _check_no_duplicate(existing, name, code)

    # Render the new section
    new_section = _render_project_section(name, code, source_dir, roster, disclosure)

    # MINIMAL WRITE: append only the new section (rest of file byte-unchanged)
    with open(config_path, "a", encoding="utf-8") as f:
        f.write(new_section)

    print(f"Registered project {name!r} (code={code!r}) in {config_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``project`` verb.

    When to use: ``rv project add <name> --code <c> --source <dir>`` registers
    a project into the multi-project config registry.
    """
    desc = (
        "Manage the project config registry. "
        "``rv project add`` registers a new project into research_vault.toml."
    )
    if parent is not None:
        p = parent.add_parser("project", help="Manage the project config registry.", description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv project", description=desc)

    sub = p.add_subparsers(dest="project_cmd", required=True)

    # add
    add_p = sub.add_parser(
        "add",
        help="Register a new project in the config registry.",
    )
    add_p.add_argument("name", help="Project slug (lowercase letters, digits, hyphens).")
    add_p.add_argument(
        "--code", required=True,
        help="Short project code identifier (unique across the registry).",
    )
    add_p.add_argument(
        "--source", dest="source_dir", required=True,
        help="Absolute or ~ path to the project's source directory.",
    )
    add_p.add_argument(
        "--roster", nargs="+", default=[],
        metavar="ROLE",
        help="Space-separated list of roles for this project (e.g. engineer researcher).",
    )
    add_p.add_argument(
        "--disclosure", default="private",
        choices=sorted(_VALID_DISCLOSURE),
        help="Disclosure level: private (default) or public.",
    )

    # list (stub — future SR)
    sub.add_parser("list", help="List all registered projects. [SR-future]")

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch project subcommands. Returns exit code."""
    if args.project_cmd == "add":
        try:
            cmd_add(
                name=args.name,
                code=args.code,
                source_dir=args.source_dir,
                roster=args.roster,
                disclosure=args.disclosure,
            )
            return 0
        except FileNotFoundError as e:
            print(f"rv project add: {e}", file=sys.stderr)
            return 1
        except ValueError as e:
            print(f"rv project add: {e}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"rv project add: unexpected error: {e}", file=sys.stderr)
            return 1

    elif args.project_cmd == "list":
        try:
            cfg = load_config()
        except Exception as e:
            print(f"rv project list: config error: {e}", file=sys.stderr)
            return 1
        slugs = cfg.all_project_slugs()
        if not slugs:
            print("No projects registered.")
        else:
            print(f"{len(slugs)} project(s):")
            for slug in slugs:
                proj = cfg.projects[slug]
                code = proj.get("code", "?")
                disclosure = proj.get("disclosure", "private")
                print(f"  {slug:<24} code={code:<12} disclosure={disclosure}")
        return 0

    return 0
