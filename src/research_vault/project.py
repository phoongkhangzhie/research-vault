"""project.py — config-registry verbs for Research Vault.

When to use: ``rv project add <name>`` registers a new project into the
multi-project TOML config registry (the config SSOT). ``rv project list``
enumerates all registered projects — the cross-project discovery substrate.
``rv project new <name>`` stands up a WHOLE new research project (git init +
registry + OKF dirs + control bus + DEVLOG + architecture + corpus + crew)
in one transactional command.

Design constraints honored here:
  - VALIDATE: the entry is schema-checked before writing.
  - WRITE MINIMALLY: only the new [projects.<name>] section is appended to the
    TOML file. The rest of the file is byte-unchanged (no whole-file re-dump).
  - IDEMPOTENT: a duplicate name OR code raises a clear error; never silently
    clobbers an existing entry.
  - CANONICAL form: field order is fixed (code, source_dir, roster).
  - SR-1 FORWARD-FLAG: roster and code are written from day one so SR-4
    reads existing fields with no registry re-migration.
  - REGISTER-FIRST: project.new registers before scaffolding so that all
    downstream primitives can resolve paths via cfg.project(slug).

Stdlib only — no third-party deps.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import importlib.resources
from datetime import date as _date

from .config import Config, load_config, reset_config_cache, _find_config_path, _load_toml

# ---------------------------------------------------------------------------
# Registry schema
# ---------------------------------------------------------------------------

# Required fields on every project record
_REQUIRED_FIELDS = ("name", "code", "source_dir")

# Optional fields with their defaults (used for canonical form)
_OPTIONAL_FIELDS = {
    "roster": [],
}

# Project name: lowercase letters, digits, hyphens only; must start with a letter
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")

# Project code: short identifier, same character set
_CODE_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def _validate_entry(name: str, code: str, source_dir: str,
                    roster: list[str]) -> None:
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
    extra: dict[str, Any] | None = None,
) -> str:
    """Render the canonical TOML section for a new project entry.

    Field order is fixed: code, source_dir, roster, then any extra keys.
    The section header is ``[projects.<name>]``.
    This is the EMIT CANONICAL FORM requirement — SR-4 can rely on this order.

    extra: optional dict of additional string key→value pairs appended after
    the canonical fields. Values are rendered as TOML basic strings.
    """
    lines = [f"\n[projects.{name}]"]
    lines.append(f"code = {_toml_string(code)}")
    lines.append(f"source_dir = {_toml_string(source_dir)}")
    if roster:
        roster_toml = "[" + ", ".join(_toml_string(r) for r in roster) + "]"
        lines.append(f"roster = {roster_toml}")
    else:
        lines.append("roster = []")
    if extra:
        for k, v in extra.items():
            lines.append(f"{k} = {_toml_string(str(v))}")
    lines.append("")  # trailing newline after section
    return "\n".join(lines)


def cmd_add(
    name: str,
    code: str,
    source_dir: str,
    roster: list[str],
    *,
    config_path: Path | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Register a new project entry in the TOML config.

    Performs schema validation, duplicate check, then APPENDS the new
    [projects.<name>] section to the config file — without reformatting
    the rest of the file.

    extra: optional dict of additional string key→value pairs to write into
    the section (e.g. {"refs": "/path/library.json"}). Backward-compatible;
    callers that don't need extra keys pass nothing.

    Raises ValueError on invalid input or duplicate name/code.
    Raises FileNotFoundError if no config file is found.
    """
    # Normalize source_dir
    source_dir = str(Path(source_dir).expanduser())

    # Validate fields
    _validate_entry(name, code, source_dir, roster)

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
    new_section = _render_project_section(name, code, source_dir, roster, extra)

    # MINIMAL WRITE: append only the new section (rest of file byte-unchanged)
    with open(config_path, "a", encoding="utf-8") as f:
        f.write(new_section)

    print(f"Registered project {name!r} (code={code!r}) in {config_path}")


def cmd_list(cfg: Config | None = None) -> int:
    """List all registered projects from the config registry.

    Enumerates: slug, code, source_dir, roster.
    This is the cross-project discovery substrate — agents use this to enumerate
    peer projects and find their note stores for cross-project corroboration.
    Returns 0 on success, 1 on config error.
    """
    if cfg is None:
        try:
            cfg = load_config()
        except Exception as e:
            print(f"rv project list: config error: {e}", file=sys.stderr)
            return 1
    slugs = cfg.all_project_slugs()
    if not slugs:
        print("No projects registered.")
        return 0
    print(f"{len(slugs)} project(s):")
    for slug in slugs:
        proj = cfg.projects[slug]
        code = proj.get("code", "?")
        source = proj.get("source_dir", "")
        roster = proj.get("roster", [])
        roster_str = "[" + ", ".join(roster) + "]" if roster else "[]"
        print(f"  {slug:<24} code={code:<12} roster={roster_str}")
        if source:
            print(f"  {'':24} source={source}")
    return 0


# ---------------------------------------------------------------------------
# project new — stand-up-a-whole-project capstone
# ---------------------------------------------------------------------------

# Project-level architecture.md template (NOT the instance-shaped one in init.py)
_PROJECT_ARCHITECTURE_TEMPLATE = """\
# Architecture — {slug}

> Auto-generated by `rv project new`. Fill in the component/data-flow skeleton.

## Overview

<!-- Describe the research project's structure: inputs, steps, outputs. -->

## Components

```mermaid
graph TD
    A[Source data / corpus] --> B[Processing / annotation]
    B --> C[Analysis / modelling]
    C --> D[Findings]
    D --> E[Synthesis / write-up]
```

## Data flows

| Stage | Input | Output | Module / verb |
|-------|-------|--------|---------------|
| Corpus | raw papers | library.json | rv cite add |
| Notes | papers | literature/*.md | rv note |
| Analysis | notes | findings/*.md | rv research |
| Synthesis | findings | write-up | — |

## Key decisions

- _(fill in architectural decisions as you make them)_

## Open questions

- _(fill in open design questions)_
"""


def _render_contract_template(
    slug: str,
    code: str,
    source_dir: str,
    roster: list[str],
) -> str:
    """Load and interpolate the portable CONTRACT.md template.

    Fills only the mechanically-known fields: {slug}, {code}, {source_dir},
    {roster}, {date}.  Every editorial field remains a <!-- FILL --> placeholder.
    Template is loaded from the package data (templates/CONTRACT.md.tmpl).
    """
    pkg = importlib.resources.files("research_vault")
    tmpl_path = Path(str(pkg)) / "templates" / "CONTRACT.md.tmpl"
    template = tmpl_path.read_text(encoding="utf-8")
    roster_str = " · ".join(roster) if roster else "(no roster defined)"
    today = _date.today().isoformat()
    return template.format(
        slug=slug,
        code=code,
        source_dir=source_dir,
        roster=roster_str,
        date=today,
    )


def cmd_new(
    name: str,
    code: str,
    source_dir: str | None,
    roster: list[str],
    *,
    zotero: bool = False,
    git_discipline: bool = False,
    force: bool = False,
    config_path: Path | None = None,
) -> int:
    """Stand up a whole new research project in one transactional command.

    Sequence (register-first, then scaffold):
      1. Guards (duplicate slug/code, source-dir overwrite).
      2. git init --initial-branch=main <source_dir>.
      3. project.cmd_add (register) + reload config cache.
      4. OKF dirs via note.scaffold_okf_dirs.
      5. control.cmd_init (SR-CP write-face).
      6. devlog.cmd_init.
      7. architecture.md (project-shaped template).
      7b. CONTRACT.md (portable lens skeleton — fill before first crew dispatch).
      8. library.json (empty corpus or Zotero-synced if --zotero).
      9. [optional --zotero] cite.create_collection + sync_library.
     10. build_agents.cmd_build (if roster non-empty).
     11. [optional --git-discipline] git_discipline._install_repo; else print offer.
     12. Initial conventional commit.
     13. Next-steps print.

    source_dir: path where the new repo will live. If None, defaults to
    cfg.instance_root.parent / name (sibling-of-instance convention).

    Rollback: any failure at steps 4–12 un-appends the registry section,
    shutil.rmtree(source_dir), AND removes cfg.agents_dir/<slug>/ (so no
    orphan CONTRACT or hat files are left behind — D-CONTRACT-2 fix).
    Failure before step 3 → rmtree only.
    Returns 0 on success, 1 on any error.
    """
    from .note import scaffold_okf_dirs
    from . import control, devlog, build_agents, git_discipline as gd
    from .config import load_config, reset_config_cache

    # ── Resolve config path ──────────────────────────────────────────────────
    if config_path is None:
        config_path = _find_config_path()
    if config_path is None:
        print(
            "rv project new: no research_vault.toml found. "
            "Run `rv init <dir>` to create an instance first.",
            file=sys.stderr,
        )
        return 1

    # ── Resolve source_dir — default = sibling of instance_root ─────────────
    if source_dir is None:
        _cfg_for_default = load_config(reload=True)
        source_path = _cfg_for_default.instance_root.parent / name
    else:
        source_path = Path(source_dir).expanduser().resolve()

    # ── STEP 1: preflight guards ─────────────────────────────────────────────
    try:
        _validate_entry(name, code, str(source_path), roster)
    except ValueError as e:
        print(f"rv project new: {e}", file=sys.stderr)
        return 1

    existing = _read_existing_projects(config_path)
    try:
        _check_no_duplicate(existing, name, code)
    except ValueError as e:
        print(
            f"rv project new: {e}\n"
            "  Hint: use `rv project add` if the repo already exists and you only need the registry entry.",
            file=sys.stderr,
        )
        return 1

    if source_path.exists() and any(source_path.iterdir()):
        if not force:
            print(
                f"rv project new: {source_path} already exists and is non-empty. "
                "Use --force to overwrite (destructive).",
                file=sys.stderr,
            )
            return 1
        # --force: tear down the existing dir and any prior registry entry
        shutil.rmtree(source_path)
        # Remove any prior registry entry for this slug (best-effort)
        _rollback_registry(config_path, name)

    # Track whether we've registered (so rollback knows what to undo)
    registered = False

    # ── STEP 2: git init ─────────────────────────────────────────────────────
    source_path.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["git", "init", "--initial-branch=main", str(source_path)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"rv project new: git init failed: {r.stderr.strip()}", file=sys.stderr)
        shutil.rmtree(source_path, ignore_errors=True)
        return 1

    # Configure a git user so the initial commit works in bare environments
    subprocess.run(
        ["git", "-C", str(source_path), "config", "user.email", "rv-project-new@example.invalid"],
        capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "-C", str(source_path), "config", "user.name", "rv project new"],
        capture_output=True, text=True,
    )

    try:
        # ── STEP 3: register ─────────────────────────────────────────────────
        refs_path = source_path / "library.json"
        cmd_add(
            name=name,
            code=code,
            source_dir=str(source_path),
            roster=roster,
            config_path=config_path,
            extra={"refs": str(refs_path)},
        )
        registered = True

        # Mandatory cache reload — stale cache causes cfg.project(slug) to KeyError
        reset_config_cache()
        cfg = load_config(reload=True)

        # ── STEP 4: OKF dirs ─────────────────────────────────────────────────
        scaffold_okf_dirs(source_path)
        print(f"  created: OKF type dirs under {source_path}/")

        # ── STEP 5: control bus ──────────────────────────────────────────────
        control.cmd_init(name, config=cfg)
        print(f"  created: control/{name}.md")

        # ── STEP 6: DEVLOG.md ────────────────────────────────────────────────
        devlog.cmd_init(name, config=cfg)
        print(f"  created: DEVLOG.md")

        # ── STEP 7: architecture.md ──────────────────────────────────────────
        arch_path = source_path / "architecture.md"
        arch_path.write_text(
            _PROJECT_ARCHITECTURE_TEMPLATE.format(slug=name),
            encoding="utf-8",
        )
        print(f"  created: architecture.md")

        # ── STEP 7b: CONTRACT.md (project lens skeleton) ─────────────────────
        # Must come BEFORE crew hats (step 10) so that build_agents.cmd_build
        # can compose a real CONTRACT into every hat from the first run.
        contract_dir = cfg.agents_dir / name
        contract_dir.mkdir(parents=True, exist_ok=True)
        contract_path = contract_dir / "CONTRACT.md"
        contract_path.write_text(
            _render_contract_template(name, code, str(source_path), roster),
            encoding="utf-8",
        )
        print(f"  created: .agents/{name}/CONTRACT.md (lens skeleton — fill before first crew dispatch)")

        # ── STEP 8: library.json (empty corpus) ──────────────────────────────
        refs_path.write_text("[]", encoding="utf-8")
        print(f"  created: library.json (empty corpus)")

        # ── STEP 9: Zotero collection + library.json sync (optional) ────────
        if zotero:
            try:
                from .cite import create_collection, sync_library, _get_zotero_key, _whoami
                key = _get_zotero_key()
                uid, _, _ = _whoami(key)
                coll_key = create_collection(name, key=key, uid=uid)
                # Write collection key into the registry section (minimal append)
                _append_project_key(config_path, name, "collection", coll_key)
                reset_config_cache()
                cfg = load_config(reload=True)
                print(f"  created: Zotero collection {name!r} (key={coll_key})")
                # Sync library.json from the (initially empty) collection
                # — establishes the mirror pattern; for a new collection yields []
                items = sync_library(coll_key, key=key, uid=uid, refs_path=refs_path)
                print(f"  synced: library.json ({len(items)} items from Zotero)")
            except (Exception, SystemExit) as e:
                # _get_zotero_key() calls sys.exit() on missing key → SystemExit
                print(
                    f"rv project new: --zotero failed ({e}); continuing without Zotero.",
                    file=sys.stderr,
                )

        # ── STEP 10: crew hats ────────────────────────────────────────────────
        if roster:
            build_agents.cmd_build(name, cfg)
            print(f"  created: .agents/{name}/ hat files for {roster}")

        # ── STEP 11: git-discipline ───────────────────────────────────────────
        if git_discipline:
            gd._install_repo(source_path, alias=name)
        else:
            print(
                f"\n  Git discipline not installed. To add it later:\n"
                f"    rv git-discipline install --project {name}"
            )

        # ── STEP 12: initial commit ───────────────────────────────────────────
        subprocess.run(
            ["git", "-C", str(source_path), "add", "-A"],
            capture_output=True, text=True,
        )
        r = subprocess.run(
            ["git", "-C", str(source_path), "commit",
             "-m", f"chore: scaffold {name} project"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"git commit failed: {r.stderr.strip()}")
        print(f"  committed: chore: scaffold {name} project")

        # ── STEP 13: next-steps print ─────────────────────────────────────────
        _print_next_steps(name, source_path, git_discipline)

        return 0

    except Exception as e:
        print(f"\nrv project new: error: {e}", file=sys.stderr)
        print("  Rolling back...", file=sys.stderr)
        if registered:
            _rollback_registry(config_path, name)
        shutil.rmtree(source_path, ignore_errors=True)
        # D-CONTRACT-2 fix: also clean the agents-dir entry so no orphan CONTRACT
        # or hat files are left behind (agents_dir is outside source_path and
        # was not covered by the prior rmtree).
        try:
            _cfg_for_rollback = load_config(reload=True)
            agents_slug_dir = _cfg_for_rollback.agents_dir / name
            shutil.rmtree(agents_slug_dir, ignore_errors=True)
        except Exception:
            # Best-effort: if config is broken at this point, skip silently
            pass
        reset_config_cache()
        print(
            "  Rollback complete: registry entry removed, source dir deleted, agents dir cleaned.",
            file=sys.stderr,
        )
        return 1


def _rollback_registry(config_path: Path, name: str) -> None:
    """Un-append the [projects.<name>] section from the config file.

    Safe to call even if the section was never written (no-op if not found).
    """
    text = config_path.read_text(encoding="utf-8")
    marker = f"\n[projects.{name}]"
    idx = text.rfind(marker)  # rfind: last occurrence (we appended it)
    if idx != -1:
        config_path.write_text(text[:idx], encoding="utf-8")


def _append_project_key(config_path: Path, name: str, key: str, value: str) -> None:
    """Append a single key=value line into the [projects.<name>] section.

    Used by the Zotero step to write the collection key after registration.
    """
    text = config_path.read_text(encoding="utf-8")
    marker = f"[projects.{name}]"
    idx = text.find(marker)
    if idx == -1:
        return
    # Find the end of the section (next section header or EOF)
    next_section = text.find("\n[", idx + len(marker))
    if next_section == -1:
        insert_at = len(text)
    else:
        insert_at = next_section
    # Strip trailing newline before inserting
    new_line = f'{key} = "{value}"\n'
    updated = text[:insert_at].rstrip("\n") + "\n" + new_line + text[insert_at:]
    config_path.write_text(updated, encoding="utf-8")


def _print_next_steps(name: str, source_path: Path, gd_installed: bool) -> None:
    """Print the discovery/next-steps surface after a successful `project new`."""
    print(f"\nProject {name!r} is ready at {source_path}")
    print("\nNext steps:")
    print(f"  1. Fill .agents/{name}/CONTRACT.md (the project lens — replace <!-- FILL --> blocks)")
    print(f"  rv build-agents --project {name}   # re-bake hats after you fill the CONTRACT")
    print(f"  rv wt add <task> --project {name}   # create an isolated task worktree")
    print(f"  rv note {name} new findings \"<title>\"  # create your first finding")
    print(f"  rv research add --project {name} <doi>   # add a paper to the corpus")
    if not gd_installed:
        print(f"  rv git-discipline install --project {name}   # add commit-msg + protect-main hooks")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``project`` verb.

    When to use: ``rv project add <name> --code <c> --source <dir>`` registers
    a project into the multi-project config registry. ``rv project list``
    enumerates all registered projects. ``rv project new`` stands up a complete
    new research project (git init + registry + scaffolding) in one command.
    """
    desc = (
        "Manage the project config registry. "
        "``rv project add`` registers a new project into research_vault.toml. "
        "``rv project list`` enumerates all registered projects. "
        "``rv project new`` stands up a whole new research project (git init + "
        "registry + OKF dirs + control bus + DEVLOG + architecture + corpus + crew)."
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

    # list — real implementation (SR-XP)
    sub.add_parser("list", help="List all registered projects (slug, code, roster, source).")

    # new — stand-up-a-whole-project capstone (SR-NEW)
    new_p = sub.add_parser(
        "new",
        help=(
            "Stand up a whole new research project: git init + registry + OKF dirs + "
            "control bus + DEVLOG + architecture.md + library.json + crew."
        ),
    )
    new_p.add_argument("name", help="Project slug (lowercase letters, digits, hyphens).")
    new_p.add_argument(
        "--code", required=True,
        help="Short project code identifier (unique across the registry).",
    )
    new_p.add_argument(
        "--source", dest="source_dir", default=None,
        help=(
            "Path where the new git repo will be created. "
            "Default: sibling of the RV instance root (instance_root/../<slug>)."
        ),
    )
    new_p.add_argument(
        "--roster", nargs="+", default=[],
        metavar="ROLE",
        help="Agent roles for this project (e.g. engineer researcher reviewer).",
    )
    new_p.add_argument(
        "--zotero", action="store_true", default=False,
        help=(
            "Create a Zotero collection for this project (requires ZOTERO_KEY). "
            "Without this flag, an empty library.json is scaffolded (zero-infra default)."
        ),
    )
    new_p.add_argument(
        "--git-discipline", action="store_true", default=False,
        help="Install git-discipline hooks (.githooks/ + core.hooksPath) in the new repo now.",
    )
    new_p.add_argument(
        "--force", action="store_true", default=False,
        help="Overwrite an existing source dir and registry entry (destructive).",
    )

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
        return cmd_list()

    elif args.project_cmd == "new":
        return cmd_new(
            name=args.name,
            code=args.code,
            source_dir=args.source_dir,
            roster=args.roster,
            zotero=args.zotero,
            git_discipline=args.git_discipline,
            force=args.force,
        )

    return 0
