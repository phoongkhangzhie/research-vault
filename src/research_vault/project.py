# SPDX-License-Identifier: AGPL-3.0-or-later
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

from .config import Config, load_config, reset_config_cache, _find_config_path, _load_toml

# ---------------------------------------------------------------------------
# Registry schema
# ---------------------------------------------------------------------------

# The canonical dispatchable crew for every project.
# Hub and architect are vault-level; all other roles are project-scoped
# and always appear per-project.  Manager is hub-level (the hub coordinates
# directly with the crew — no intermediate manager tier).
# Slug convention matches the functional role name.
DEFAULT_ROSTER: list[str] = [
    "engineer",
    "researcher",
    "designer",
    "reviewer",
]

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

    from .richui import should_render_rich, render_closing
    if should_render_rich():
        try:
            render_closing(
                f"[bold]Registered project {name!r}[/bold] "
                f"[dim](code={code!r})[/dim]\n"
                f"in [dim]{config_path}[/dim]",
                title="rv project add",
            )
            return
        except Exception:
            pass  # fall through to the plain line

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

    # Structured registry view — the SSOT both surfaces read from.
    projects = []
    for slug in slugs:
        proj = cfg.projects[slug]
        projects.append({
            "slug": slug,
            "code": proj.get("code", "?"),
            "source": proj.get("source_dir", ""),
            "roster": proj.get("roster", []),
        })

    from .richui import should_render_rich, render_project_list
    if should_render_rich():
        try:
            render_project_list(projects)
            return 0
        except Exception:
            pass  # fall through to the plain listing on any render hiccup

    print(f"{len(slugs)} project(s):")
    for p in projects:
        roster = p["roster"]
        roster_str = "[" + ", ".join(roster) + "]" if roster else "[]"
        print(f"  {p['slug']:<24} code={p['code']:<12} roster={roster_str}")
        if p["source"]:
            print(f"  {'':24} source={p['source']}")
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

## Folder structure (CS-project convention)

```
{slug}/
├── notes/            OKF knowledge base (literature/ concepts/ methods/ experiments/
│                      findings/ mocs/ gaps/ log/) — datasets/ is SHARED (rv datasets_root)
├── code/{{src,tests,tools}}/   ALL source — freely refactorable, nothing links INTO it
├── data/              raw inputs — read-only, large files gitignored
├── results/{{runs,scores}}/    runs=raw/gitignored, scores=computed/TRACKED SSOT
├── figures/           designed, provenance-stamped — TRACKED
└── manuscripts/       write-ups, paper outlines
```

See `doctrine/project-structure.md` for the full convention and the
notes↔artifacts linkage rules (hashed frontmatter, not prose paths).

## Key decisions

- _(fill in architectural decisions as you make them)_

## Open questions

- _(fill in open design questions)_
"""


def _render_pointers_skeleton(slug: str, source_dir: str) -> str:
    """Generate a minimal pointers.md skeleton for a new project.

    pointers.md is a LIGHTWEIGHT, READ-FRESH file — not a baked hat lens.
    It accrues pointers as the project develops; a brand-new project can
    have an empty skeleton and nothing blocks on it (SR-LENS-RM D-LR-1).

    Blessed MUST-contain shape (the multi-project context-switch convention,
    de-facto per rv's own pointers.md — see doctrine/coordination.md):
    Identity · ★ POINTERS · Roadmap · Team · Operational-state. Five headers,
    each with a commented prompt — no FILL gates, no authoring burden before
    first crew dispatch. `rv orient <project>` reads this file in FULL as
    part of the one-shot cold-context-switch orientation.
    """
    return f"""\
# Pointers — {slug}

*Read fresh by the crew via `rv orient {slug}` (one-shot cold-switch orient) or
`rv status --project {slug}` (operational read only).
Add pointers here as the project develops — no gates, no required fields.*

**Source dir:** `{source_dir}`

## Identity

<!-- What this project is, in a paragraph: profile, thesis, what it produces. -->

## ★ POINTERS — know where things live

<!-- Add the design-of-record path when it exists:
- design-of-record: /path/to/tasks/task-name.md
-->
<!-- Add the primary results source when known:
- results: wandb.ai/... or /path/to/results/
-->
<!-- Add the architecture link when written (rv orient echoes its head automatically):
- architecture: {source_dir}/architecture.md
-->

## Roadmap

<!-- Phase / milestone state, verified against the design-of-record, not memory. -->

## Team

<!-- Roster + what each role leans on for this project (see rv role list). -->

## Operational state

<!-- Read fresh, not baked here — `rv status --project {slug}` (or `rv orient
{slug}` for the full cold-switch bundle) is the live source of truth. -->
"""


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
      7b. pointers.md (lightweight read-fresh pointer skeleton — SR-LENS-RM).
      8. library.json (empty corpus or Zotero-synced if --zotero).
      9. [optional --zotero] cite.create_collection + sync_library.
     10. [optional --git-discipline] git_discipline._install_repo; else print offer.
     11. Initial conventional commit.
     12. Next-steps print.

    source_dir: path where the new repo will live. If None, defaults to
    cfg.instance_root.parent / name (sibling-of-instance convention).

    Rollback: any failure at steps 4–11 un-appends the registry section and
    shutil.rmtree(source_dir).
    Failure before step 3 → rmtree only.
    Returns 0 on success, 1 on any error.
    """
    from .note import scaffold_okf_dirs
    from . import control, devlog, git_discipline as gd, scaffold
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
    _cfg_for_default = load_config(reload=True)
    if source_dir is None:
        source_path = _cfg_for_default.instance_root.parent / name
    else:
        source_path = Path(source_dir).expanduser().resolve()
        # Warn when source_path is nested inside the vault's instance_root.
        # A project nested inside the vault creates a git-repo-inside-git-repo,
        # which confuses git, breaks the leakage boundary, and ties the project's
        # lifecycle to the vault's.  The canonical convention is that every project
        # is a SIBLING of the vault (instance_root.parent/<slug>), each its own
        # independent repository.  We warn but do not hard-block because the
        # operator may have a deliberate reason (e.g. a monorepo layout or a
        # CI environment where the vault IS the workspace root).
        try:
            source_path.relative_to(_cfg_for_default.instance_root)
            print(
                f"WARNING: --source {source_path} is INSIDE the vault "
                f"({_cfg_for_default.instance_root}).\n"
                "  This creates a git repo nested inside another git repo, which can\n"
                "  confuse git and breaks the project-as-sibling convention.\n"
                "  Convention: projects live as SIBLINGS of the vault, e.g.:\n"
                f"    {_cfg_for_default.instance_root.parent / name}\n"
                "  Proceeding anyway — pass no --source to use the sibling default.",
                file=sys.stderr,
            )
        except ValueError:
            pass  # not inside the vault — no warning needed

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

    # Belt-and-suspenders: any direct Python caller passing roster=[] also gets
    # the canonical default crew (same as the CLI path).
    roster = roster or DEFAULT_ROSTER

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

        # ── STEP 4b: CS-project folder-structure convention ─────────────────
        # code/{src,tests,tools}, data/, results/{runs,scores}, figures/,
        # manuscripts/, notes/log/ — see doctrine/project-structure.md.
        scaffold.scaffold_project_dirs(source_path)
        print(f"  created: code/ data/ results/ figures/ manuscripts/ notes/log/ under {source_path}/")

        # ── STEP 4b-2: releasability stubs (PR-CC-4, code-conventions §5.1) ──
        # CITATION.cff + LICENSE — non-destructive; never overwrites an
        # existing/filled-in stub (see scaffold.USER_OWNED_NEVER_TOUCH).
        scaffold.scaffold_release_stubs(source_path, slug=name)
        print(f"  created: CITATION.cff LICENSE (stubs) under {source_path}/")

        # ── STEP 4c: .gitignore (CS-project convention: results/runs/, large data/) ──
        gitignore_path = source_path / ".gitignore"
        gitignore_path.write_text(scaffold.FRAMEWORK_GITIGNORE, encoding="utf-8")
        print(f"  created: .gitignore")

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

        # ── STEP 7b: pointers.md (read-fresh project-context pointer skeleton) ──
        # SR-LENS-RM: replaces the CONTRACT lens skeleton.  No fill-gate; no
        # authoring burden before first crew dispatch; accrues as scope emerges.
        pointers_path = source_path / "pointers.md"
        pointers_path.write_text(
            _render_pointers_skeleton(name, str(source_path)),
            encoding="utf-8",
        )
        print(f"  created: pointers.md (read-fresh project pointer skeleton)")

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

        # ── STEP 10: git-discipline ───────────────────────────────────────────
        if git_discipline:
            gd._install_repo(source_path, alias=name)
        else:
            print(
                f"\n  Git discipline not installed. To add it later:\n"
                f"    rv git-discipline install --project {name}"
            )

        # ── STEP 11: initial commit ───────────────────────────────────────────
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

        # ── STEP 12: next-steps print ─────────────────────────────────────────
        _print_next_steps(name, source_path, git_discipline)

        return 0

    except Exception as e:
        print(f"\nrv project new: error: {e}", file=sys.stderr)
        print("  Rolling back...", file=sys.stderr)
        if registered:
            _rollback_registry(config_path, name)
        shutil.rmtree(source_path, ignore_errors=True)
        reset_config_cache()
        print(
            "  Rollback complete: registry entry removed, source dir deleted.",
            file=sys.stderr,
        )
        return 1


def _archive_root() -> Path:
    """Return the root directory for local-only archives (crew memory, etc.).

    Override via ``RV_ARCHIVE_ROOT`` (test isolation; takes priority).
    Default: ``~/vault-archive/`` (Decision D2, 2026-07-08-rv-project-remove.md §7).
    """
    env = os.environ.get("RV_ARCHIVE_ROOT")
    if env:
        return Path(env)
    return Path.home() / "vault-archive"


def _agents_archive_dest(slug: str) -> Path:
    """The (deterministic, per-day) destination `.agents/<slug>/` archives
    to under --purge-agents.  Shared by the actual archive step and the
    handoff line so both name the same path."""
    import datetime
    return _archive_root() / f"{slug}-{datetime.date.today().isoformat()}"


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


# ---------------------------------------------------------------------------
# project relate / edges — hub-owned cross-project edge management (SR-XPB)
# ---------------------------------------------------------------------------

def cmd_relate(
    a: str,
    b: str,
    kind: str | None = None,
    *,
    remove: bool = False,
    config_path: Path | None = None,
) -> int:
    """Declare or prune a cross-project edge.

    ``rv project relate <a> <b> --kind K`` is a **hub coordination act**: the hub
    holds the registry overview and grants intentional cross-project reach by
    declaring edges.  Human operators may also declare or prune edges.

    The *scientific gate* (corroboration quality) lives downstream on the
    corroboration assertion (the judge step), NOT here.  Declaring an edge says
    "these projects share a domain where cross-project reading is meaningful" —
    not "any hit is valid."

    Over-declaration warning: blanket-relating all projects to each other
    preserves correctness (the judge still filters) but forfeits the narrowing and
    efficiency benefit of the declared-edge gate.  Declare on genuine relatedness.

    Parameters
    ----------
    a, b:
        Project slugs to relate or un-relate.
    kind:
        Required when declaring (``--remove`` absent).  Describes the genuine
        relatedness (e.g. ``"shares-methodology"``, ``"same-domain"``).
    remove:
        If True, prune the edge instead of declaring it.
    config_path:
        Override the config file location.

    Returns
    -------
    int — exit code (0 success, 1 error).
    """
    from .project_edges import add_edge, remove_edge
    from .config import load_config

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv project relate: config error: {e}", file=sys.stderr)
        return 1

    try:
        if remove:
            remove_edge(cfg, a, b)
        else:
            if not kind:
                print(
                    "rv project relate: --kind is REQUIRED when declaring an edge.\n"
                    "  Describe the genuine relatedness, e.g.:\n"
                    "    rv project relate <a> <b> --kind shares-methodology",
                    file=sys.stderr,
                )
                return 1
            add_edge(cfg, a, b, kind)
        return 0
    except KeyError as e:
        print(f"rv project relate: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"rv project relate: {e}", file=sys.stderr)
        return 1


def cmd_relate_suggest(
    *,
    top_k: int = 5,
    config_path: Path | None = None,
) -> int:
    """Suggest candidate cross-project edges based on corpus similarity.

    Runs the Slice-4 TF-IDF ranker across ALL project pairs' note corpora and
    surfaces high cross-corpus-similarity pairs as candidates for the hub to
    declare.

    Prints proposals ONLY — NEVER auto-declares.  The hub inspects suggestions
    and declares with ``rv project relate <a> <b> --kind <why>`` on chosen pairs.

    Returns 0 on success, 1 on error.
    """
    from .config import load_config
    from .cross_project import rank_candidates
    from pathlib import Path as _Path

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv project relate --suggest: config error: {e}", file=sys.stderr)
        return 1

    all_slugs = cfg.all_project_slugs()
    if len(all_slugs) < 2:
        print("rv project relate --suggest: fewer than 2 projects registered — no pairs to rank.")
        return 0

    # Build corpus text for each project (concatenate all .md files)
    def _project_corpus(slug: str) -> str:
        proj = cfg.projects[slug]
        source_dir_str = proj.get("source_dir", "")
        if not source_dir_str:
            return ""
        source_dir = _Path(source_dir_str)
        if not source_dir.exists():
            return ""
        parts = []
        for note_path in sorted(source_dir.rglob("*.md")):
            try:
                parts.append(note_path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                pass
        return " ".join(parts)

    corpora: dict[str, str] = {slug: _project_corpus(slug) for slug in all_slugs}

    # Score all pairs
    import itertools
    from .project_edges import load_edges, _normalise_pair

    already_declared = {
        _normalise_pair(e["a"], e["b"]) for e in load_edges(cfg)
    }

    pair_scores: list[tuple[float, str, str]] = []
    for a, b in itertools.combinations(all_slugs, 2):
        if _normalise_pair(a, b) in already_declared:
            continue  # already declared — skip
        ca, cb = corpora[a], corpora[b]
        if not ca.strip() or not cb.strip():
            continue
        # Use rank_candidates: treat corpus_a as the "claim", corpus_b as a candidate
        candidates = [{"body": cb, "excerpt": cb[:120], "anchor": "", "project": b,
                       "note_path": "", "note_rel": "", "provenance": f"@{b}:corpus"}]
        ranked = rank_candidates(ca, candidates, min_score=0.0, top_k=1)
        score = ranked[0]["score"] if ranked else 0.0
        pair_scores.append((score, a, b))

    pair_scores.sort(reverse=True)
    top = pair_scores[:top_k]

    if not top:
        print("No undeclared project pairs found (all pairs already declared, or empty corpora).")
        return 0

    print(f"Top {len(top)} candidate edge(s) by corpus similarity:")
    print("  (proposals only — declare with: rv project relate <a> <b> --kind <why>)\n")
    for score, a, b in top:
        print(f"  {a} ↔ {b}   similarity={score:.3f}")
        print(f"    → rv project relate {a} {b} --kind <describe-the-genuine-relatedness>")
    print()
    return 0


def cmd_edges(
    project: str | None = None,
    *,
    config_path: Path | None = None,
) -> int:
    """List declared cross-project edges.

    ``rv project edges`` prints all declared edges.
    ``rv project edges --project SLUG`` prints only edges involving SLUG.

    Returns 0 on success, 1 on error.
    """
    from .project_edges import load_edges
    from .config import load_config

    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv project edges: config error: {e}", file=sys.stderr)
        return 1

    edges = load_edges(cfg)

    if project:
        # Validate the slug exists
        try:
            cfg.project(project)
        except KeyError as e:
            print(f"rv project edges: {e}", file=sys.stderr)
            return 1
        edges = [
            e for e in edges
            if e.get("a") == project or e.get("b") == project
        ]

    if not edges:
        if project:
            print(
                f"No declared edges for {project!r}.\n"
                f"  The hub can declare one:  rv project relate {project} <peer> --kind <why>"
            )
        else:
            print("No declared project edges.")
        return 0

    label = f" involving {project!r}" if project else ""
    print(f"{len(edges)} declared edge(s){label}:")
    for e in edges:
        print(f"  {e['a']} ↔ {e['b']}   kind={e.get('kind', '?')!r}")
    return 0


# ---------------------------------------------------------------------------
# project remove — clean local teardown (the reversal of `new`/`add`)
# ---------------------------------------------------------------------------
#
# Design: docs/superpowers/specs/2026-07-08-rv-project-remove.md (the architect).
# Grounded reversal of every artifact `cmd_new`/`cmd_add` stand up (R1-R9 in the
# design's inventory).  rv owns the RV-SIDE teardown authoritatively and emits a
# structured ``VAULT-TEARDOWN`` handoff for the thin `vault project remove` to
# consume (projects.json un-insert via held human-go PR, hub-clone removal,
# deploy suppression) — rv never writes projects.json or touches a hub clone.
#
# THE load-bearing guard (§3.3 of the design): because GitHub preserves all
# pushed work, local removal is reversible (re-clone) EXCEPT for anything not
# yet pushed.  So before clearing worktrees or purging the local repo, we
# enumerate uncommitted/untracked files, unpushed commits, un-pushed branches,
# and stash entries — REFUSE (fail-closed) if any are found, printing the exact
# at-risk manifest.  ``--force`` downgrades a REFUSE into a typed confirmation
# (the operator sees the manifest and types the slug) — never a silent bypass.

_LIVE_DAG_STATUSES = frozenset({"dispatched", "running", "awaiting-go"})


def _git_out(args: list[str], *, cwd: Path) -> tuple[int, str]:
    """Run a git command in *cwd*; return (returncode, stdout stripped)."""
    r = subprocess.run(
        ["git", "-C", str(cwd)] + args, capture_output=True, text=True,
    )
    return r.returncode, r.stdout.strip()


def _repo_uncommitted_issues(repo: Path, *, exclude: Path | None = None) -> list[str]:
    """Return at-risk-work issue lines for uncommitted/untracked content in *repo*.

    ``exclude``: a path whose entry should be dropped from the scan — used
    for the worktree home directory. On the CS convention (`source_dir =
    <repo>/notes`), the worktree home (`<repo>/notes-wt`) is NESTED inside
    `repo`, so it would otherwise self-trip this guard merely by existing
    (git reports it as a bare untracked dir: `?? notes-wt/`) even when every
    worktree inside it is perfectly clean. Its contents are scanned
    separately, per-worktree, by the caller — never silently unscanned.
    """
    if not repo.exists() or not (repo / ".git").exists():
        return []
    rc, out = _git_out(["status", "--porcelain"], cwd=repo)
    if rc != 0 or not out:
        return []
    lines = out.splitlines()
    if exclude is not None:
        try:
            rel = str(exclude.resolve().relative_to(repo.resolve()))
        except ValueError:
            rel = None
        if rel is not None:
            lines = [
                ln for ln in lines
                if ln[3:].strip().rstrip("/") != rel
                and not ln[3:].strip().startswith(rel + "/")
            ]
    if not lines:
        return []
    n = len(lines)
    return [f"{repo}: {n} uncommitted/untracked file(s) not on GitHub"]


def _repo_branch_and_stash_issues(repo: Path) -> list[str]:
    """Return at-risk-work issue lines for stashes and un-pushed/ahead branches.

    Run ONCE at the repo root — a git worktree shares the same object DB and
    ref namespace, so branch/stash state is visible (and identical) from any
    worktree of the same repo.  Calling this per-worktree would double-report.
    """
    issues: list[str] = []
    if not repo.exists() or not (repo / ".git").exists():
        return issues

    rc, out = _git_out(["stash", "list"], cwd=repo)
    if rc == 0 and out:
        n = len(out.splitlines())
        issues.append(f"{repo}: {n} stash entrie(s) — stashes are never on GitHub")

    rc, out = _git_out(
        ["for-each-ref", "--format=%(refname:short)|%(upstream:short)|%(upstream:track)", "refs/heads"],
        cwd=repo,
    )
    if rc == 0:
        for line in out.splitlines():
            if not line.strip():
                continue
            parts = line.split("|")
            branch = parts[0] if len(parts) > 0 else ""
            upstream = parts[1] if len(parts) > 1 else ""
            track = parts[2] if len(parts) > 2 else ""
            if not upstream:
                issues.append(
                    f"{repo}: branch {branch!r} has no upstream — exists only locally, push or lose it"
                )
            elif "ahead" in track:
                issues.append(
                    f"{repo}: branch {branch!r} has commits not on GitHub {track} — push before removing"
                )
    return issues


def _open_prs_warning(repo: Path) -> str | None:
    """Return an informational (non-blocking) note about open PRs, or None.

    Read-only ``gh pr list`` probe.  This is a WARN, never a REFUSE — the PR
    branch itself is already on GitHub.  Absence of ``gh`` (or auth) degrades
    silently: this is awareness-only, not a safety gate.
    """
    try:
        r = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--json", "number,headRefName"],
            cwd=str(repo), capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        prs = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    if not prs:
        return None
    return f"{len(prs)} open PR(s) on the GitHub remote (already pushed — not at risk)."


def _worktree_paths(wt_home: Path) -> list[Path]:
    """Enumerate worktrees living under *wt_home*.

    ``wt_home`` MUST be resolved the same way `wt.cmd_add`/`wt.cmd_clean` do
    (`wt._wt_home_for(wt._resolve_repo(cfg, slug), cfg)`) — NOT off
    `cfg.project_repo_root(slug)`, which differs from `wt._resolve_repo` on
    the CS convention (`source_dir = <repo>/notes`): `project_repo_root`
    resolves to `<repo>` while `wt._resolve_repo` resolves to `source_dir`
    itself, so worktrees actually live at `<repo>/notes-wt`, not `<repo>-wt`.
    Using the wrong helper here made the guard see zero worktrees (and
    `--purge-repo` `rmtree` a repo with live uncommitted work still inside
    it) — see the CS-layout regression tests in test_project_remove.py.
    """
    if not wt_home.exists():
        return []
    return sorted(p for p in wt_home.iterdir() if p.is_dir())


def _project_dag_runs(cfg: Config, slug: str) -> list[tuple[str, "Any"]]:
    """Return [(run_id, RunState)] for DAG runs whose manifest lives under this
    project's repo root or notes dir.  Matching is by resolved path
    containment (component-wise, via ``Path.is_relative_to``) — the
    manifest_path a review/experiment/manuscript loop node writes always
    resolves under the project's own tree.

    NOT a string-prefix match: ``str.startswith`` would false-positive a
    prefix-sibling slug (e.g. "foo" matching "foobar"'s manifest path),
    silently archiving another project's run as this project's own."""
    from .dag.store import RunStore

    repo_root = cfg.project_repo_root(slug).resolve()
    notes_dir = cfg.project_notes_dir(slug).resolve()
    store = RunStore.from_config(cfg)
    matches = []
    for run_id in store.list_runs():
        try:
            rs = store.load(run_id)
        except Exception:
            continue
        mp = Path(rs.manifest_path).resolve()
        if mp.is_relative_to(repo_root) or mp.is_relative_to(notes_dir):
            matches.append((run_id, rs))
    return matches


def _dag_run_is_live(run_state: "Any") -> bool:
    return any(
        ns.get("status") in _LIVE_DAG_STATUSES
        for ns in run_state.node_states.values()
    )


def _typed_confirm(slug: str, prompt: str, input_fn) -> bool:
    """Show *prompt* (the at-risk manifest) and require the operator to type
    the project slug to proceed.  Any other input (including EOF from a
    non-interactive context) is treated as a decline — fail-closed."""
    print(prompt)
    try:
        answer = input_fn(f"Type {slug!r} to proceed anyway: ")
    except EOFError:
        answer = ""
    return answer.strip() == slug


def _yes_no_confirm(prompt: str, input_fn) -> bool:
    try:
        answer = input_fn(prompt)
    except EOFError:
        answer = ""
    return answer.strip().lower() in ("y", "yes")


def _build_removal_plan(cfg: Config, slug: str) -> dict[str, Any]:
    """Resolve every artifact `cmd_remove` will touch, BEFORE any mutation.

    Returns a plan dict consumed by both --dry-run printing and the real
    execution path, so the two never drift.
    """
    proj = cfg.project(slug)  # raises KeyError if unknown
    repo = cfg.project_repo_root(slug)
    notes_dir = cfg.project_notes_dir(slug)
    control_file = cfg.project_control_file(slug)
    tasks_dir = cfg.project_tasks_dir(slug)
    agents_dir_slug = cfg.agents_dir / slug

    # Worktree home MUST be resolved the same way `wt.cmd_add`/`wt.cmd_clean`
    # do — off `wt._resolve_repo` (= source_dir), not `cfg.project_repo_root`.
    # See `_worktree_paths` docstring for why the two diverge on the CS
    # convention (`source_dir = <repo>/notes`).
    from . import wt as wt_mod
    wt_repo = wt_mod._resolve_repo(cfg, slug)
    wt_home = wt_mod._wt_home_for(wt_repo, cfg)
    worktrees = _worktree_paths(wt_home)

    from .project_edges import peers_of
    peers = sorted(peers_of(cfg, slug))

    dag_runs = _project_dag_runs(cfg, slug)
    live_runs = [rid for rid, rs in dag_runs if _dag_run_is_live(rs)]
    terminal_runs = [(rid, rs) for rid, rs in dag_runs if not _dag_run_is_live(rs)]

    from . import task as task_mod
    tasks = task_mod.cmd_list(slug, config=cfg) if tasks_dir.exists() else []
    in_flight_tasks = [
        c["path"].stem for c in tasks if c["fields"].get("status") == "in_progress"
    ]

    from .control import _detect_github_repo
    github_repo = _detect_github_repo(None, cwd=repo) if repo.exists() else None

    guard_issues = list(_repo_uncommitted_issues(repo, exclude=wt_home))
    guard_issues += _repo_branch_and_stash_issues(repo)
    for wt_path in worktrees:
        guard_issues += _repo_uncommitted_issues(wt_path)

    return {
        "slug": slug,
        "proj": proj,
        "repo": repo,
        "notes_dir": notes_dir,
        "control_file": control_file,
        "tasks_dir": tasks_dir,
        "agents_dir_slug": agents_dir_slug,
        "worktrees": worktrees,
        "peers": peers,
        "dag_runs": dag_runs,
        "live_runs": live_runs,
        "terminal_runs": terminal_runs,
        "tasks": tasks,
        "in_flight_tasks": in_flight_tasks,
        "github_repo": github_repo,
        "guard_issues": guard_issues,
        "open_pr_warning": _open_prs_warning(repo) if repo.exists() and github_repo else None,
    }


def _print_removal_plan(plan: dict[str, Any], *, purge_repo: bool, purge_agents: bool) -> None:
    slug = plan["slug"]
    print(f"rv project remove {slug} — plan:")
    print(f"  [default] deregister [projects.{slug}] from research_vault.toml")
    print(f"  [default] archive control/{slug}.md -> control/_archive/")
    if plan["peers"]:
        print(f"  [default] prune edges: {slug} <-> {', '.join(plan['peers'])}")
    else:
        print(f"  [default] prune edges: (none declared)")
    if plan["worktrees"]:
        names = ", ".join(p.name for p in plan["worktrees"])
        print(f"  [default, guarded] clear worktrees: {names}")
    else:
        print(f"  [default] clear worktrees: (none)")
    if plan["tasks"]:
        print(f"  [default] archive {len(plan['tasks'])} task card(s)")
        if plan["in_flight_tasks"]:
            print(f"    FLAG: in-flight (status=in_progress): {', '.join(plan['in_flight_tasks'])}")
    if plan["dag_runs"]:
        print(f"  [default, guarded] archive DAG run(s): {', '.join(rid for rid, _ in plan['terminal_runs'])}")
        if plan["live_runs"]:
            print(f"    REFUSE (live/provisional): {', '.join(plan['live_runs'])}")
    print(f"  [default] suppress deploy/mirror (falls out of deregister)")
    print(f"  repo: {plan['repo']}  {'PURGE (--purge-repo)' if purge_repo else 'left intact'}")
    print(
        f"  .agents/{slug}/: "
        f"{'archive to ' + str(_archive_root()) + ' (--purge-agents)' if purge_agents else 'left intact'}"
    )
    print(f"  github: {plan['github_repo'] or '(no remote detected)'} — PRESERVED, never deleted")
    if plan["guard_issues"]:
        print("\n  UNPUSHED-WORK GUARD — at risk (blocks worktree-clean / --purge-repo):")
        for issue in plan["guard_issues"]:
            print(f"    - {issue}")
    if plan["open_pr_warning"]:
        print(f"  NOTE: {plan['open_pr_warning']}")


def _print_vault_teardown_handoff(
    plan: dict[str, Any],
    *,
    purge_agents: bool = False,
    agents_archived_dest: Path | None = None,
) -> None:
    """Print the ⟦VAULT-TEARDOWN⟧ handoff.

    ``.agents/<slug>/`` is now archived BY RV ITSELF under --purge-agents
    (never by the vault consumer) — so the agents-dir line must never read
    as a vault-side to-do.  It's purely informational, reflecting the
    action rv actually took (or the fact it left the dir untouched)."""
    slug = plan["slug"]
    github_repo = plan["github_repo"] or "(no remote detected)"
    print(f"\n⟦VAULT-TEARDOWN {slug}⟧")
    print(f"  projects.json:  un-insert {slug!r}  -> held human-go PR (protected SSOT)")
    if purge_agents:
        dest = agents_archived_dest or _agents_archive_dest(slug)
        print(f"  agents-dir:     archived by rv -> {dest}")
    else:
        print(
            f"  agents-dir:     left in place at {plan['agents_dir_slug']} "
            "(pass --purge-agents to archive)"
        )
    print(f"  hub-clone:      <hub clone path>  -> remove IF clone_sync classifies IN_SYNC; else FLAG")
    print(f"  deploy/mirror:  suppressed by deregister; external mirror teardown = manual")
    print(f"  github-repo:    {github_repo}  -> PRESERVED, untouched (opt --archive-github = gh repo archive; never deleted)")


def _archive_control_file(cfg: Config, slug: str) -> None:
    control_file = cfg.project_control_file(slug)
    if not control_file.exists():
        return
    archive_dir = cfg.control_dir / "_archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / f"{slug}.md"
    if dest.exists():
        import datetime
        dest = archive_dir / f"{slug}-{datetime.date.today().isoformat()}.md"
    shutil.move(str(control_file), str(dest))
    print(f"  archived: control/{slug}.md -> {dest}")
    sidecar = control_file.parent / (control_file.stem + ".archive.md")
    if sidecar.exists():
        shutil.move(str(sidecar), str(archive_dir / sidecar.name))


def _archive_tasks(cfg: Config, slug: str, tasks: list[dict[str, Any]]) -> None:
    if not tasks:
        return
    archive_dir = cfg.tasks_dir / "_archive" / slug
    archive_dir.mkdir(parents=True, exist_ok=True)
    for card in tasks:
        src_path: Path = card["path"]
        if not src_path.exists():
            continue
        shutil.move(str(src_path), str(archive_dir / src_path.name))
    print(f"  archived: {len(tasks)} task card(s) -> {archive_dir}")


def _archive_dag_runs(cfg: Config, terminal_runs: list[tuple[str, "Any"]]) -> None:
    if not terminal_runs:
        return
    from .dag.store import RunStore
    store = RunStore.from_config(cfg)
    archive_dir = cfg.state_dir / "dag" / "_archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    for run_id, _rs in terminal_runs:
        src_path = cfg.state_dir / "dag" / f"{run_id}.json"
        if src_path.exists():
            shutil.move(str(src_path), str(archive_dir / src_path.name))
    print(f"  archived: {len(terminal_runs)} DAG run(s) -> {archive_dir}")


def cmd_remove(
    slug: str,
    *,
    dry_run: bool = False,
    purge_repo: bool = False,
    purge_agents: bool = False,
    archive_github: bool = False,
    force: bool = False,
    config_path: Path | None = None,
    input_fn: Any = input,
) -> int:
    """Clean LOCAL teardown for a registered project — the reversal of `new`/`add`.

    Default (no flags) is NON-destructive: deregisters, prunes edges, archives
    the control file, clears worktrees (guarded), archives tasks/DAG runs
    (guarded).  Repo, `.agents/<slug>/`, and the GitHub remote are left intact.

    --dry-run prints the full plan and mutates nothing.
    --purge-repo removes the local checkout (guard-gated; GitHub preserved).
    --purge-agents archives (never deletes) `.agents/<slug>/`.
    --archive-github runs the non-destructive `gh repo archive` (opt-in).
    --force downgrades a guard REFUSE into a typed confirmation.

    Returns 0 on a fully-completed run (including --dry-run); 1 if anything
    was blocked/refused (partial teardown) or the project is unknown.
    """
    if config_path is None:
        config_path = _find_config_path()
    if config_path is None:
        print("rv project remove: no research_vault.toml found.", file=sys.stderr)
        return 1

    try:
        cfg = load_config(reload=True)
    except Exception as e:
        print(f"rv project remove: config error: {e}", file=sys.stderr)
        return 1

    try:
        plan = _build_removal_plan(cfg, slug)
    except KeyError as e:
        print(f"rv project remove: {e}", file=sys.stderr)
        return 1

    if dry_run:
        _print_removal_plan(plan, purge_repo=purge_repo, purge_agents=purge_agents)
        _print_vault_teardown_handoff(plan, purge_agents=purge_agents)
        return 0

    _print_removal_plan(plan, purge_repo=purge_repo, purge_agents=purge_agents)
    print()

    blocked = False

    # ── Guard resolution: default worktree-clean + --purge-repo are gated ──
    guard_clean = not plan["guard_issues"]
    proceed_destructive = guard_clean
    if not guard_clean:
        manifest = "UNPUSHED-WORK GUARD tripped — at risk:\n" + "\n".join(
            f"  - {i}" for i in plan["guard_issues"]
        )
        if force:
            proceed_destructive = _typed_confirm(slug, manifest, input_fn)
            if not proceed_destructive:
                print(f"rv project remove: declined — worktrees/repo left untouched.")
                blocked = True
        else:
            print(f"rv project remove: REFUSE — {manifest}")
            print("  Push/commit the above, or pass --force to confirm anyway.")
            blocked = True

    # ── Worktree clearing (default, guarded) ────────────────────────────────
    if plan["worktrees"]:
        if proceed_destructive:
            from . import wt as wt_mod
            wt_mod.cmd_clean(cfg, project=slug)
        else:
            blocked = True

    # ── Repo purge (flag-gated, guarded, single confirm) ────────────────────
    if purge_repo:
        if not proceed_destructive:
            print(f"rv project remove: --purge-repo REFUSED (unpushed-work guard tripped).")
            blocked = True
        else:
            confirmed = force or _yes_no_confirm(
                f"Delete local {slug!r} checkout at {plan['repo']}? "
                "(GitHub preserved; re-clonable) [y/N] ",
                input_fn,
            )
            if confirmed:
                shutil.rmtree(plan["repo"], ignore_errors=True)
                print(f"  purged: local checkout {plan['repo']} (re-clone from GitHub to restore)")
            else:
                print(f"rv project remove: --purge-repo declined; repo left intact.")
                blocked = True

    # ── Deregister (safe, always) ────────────────────────────────────────────
    _rollback_registry(config_path, slug)
    print(f"  deregistered: [projects.{slug}]")
    reset_config_cache()

    # ── Prune edges (safe, always) ───────────────────────────────────────────
    from .project_edges import remove_edge
    for peer in plan["peers"]:
        remove_edge(cfg, slug, peer)

    # ── Archive control file (safe, always) ─────────────────────────────────
    _archive_control_file(cfg, slug)

    # ── Archive tasks (safe, always; flags in-flight) ────────────────────────
    _archive_tasks(cfg, slug, plan["tasks"])

    # ── Archive DAG runs (safe for terminal; REFUSE for live) ───────────────
    if plan["live_runs"]:
        print(f"  REFUSE: live/provisional DAG run(s) not archived: {', '.join(plan['live_runs'])}")
        print("    (don't tear down a running experiment — charter §5)")
        blocked = True
    _archive_dag_runs(cfg, plan["terminal_runs"])

    # ── Purge agents (flag-gated, always archive-not-delete) ────────────────
    agents_archived = False
    agents_archived_dest: Path | None = None
    if purge_agents:
        agents_src = plan["agents_dir_slug"]
        if agents_src.exists():
            confirmed = force or _typed_confirm(
                slug,
                f"Archive .agents/{slug}/ (crew memory, no remote copy) to {_archive_root()}?",
                input_fn,
            )
            if confirmed:
                dest = _agents_archive_dest(slug)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(agents_src), str(dest))
                print(f"  archived: .agents/{slug}/ -> {dest}")
                agents_archived = True
                agents_archived_dest = dest
            else:
                print(f"rv project remove: --purge-agents declined; .agents/{slug}/ left intact.")
                blocked = True

    # ── Archive-github (opt-in, non-destructive) ─────────────────────────────
    if archive_github:
        github_repo = plan["github_repo"]
        if not github_repo:
            print("rv project remove: --archive-github requested but no GitHub remote detected.")
        else:
            try:
                r = subprocess.run(
                    ["gh", "repo", "archive", github_repo, "--yes"],
                    capture_output=True, text=True, timeout=30,
                )
                if r.returncode == 0:
                    print(f"  archived on GitHub: {github_repo} (non-destructive, un-archivable)")
                else:
                    print(
                        f"rv project remove: gh repo archive failed ({r.stderr.strip()}). "
                        f"Manual step: gh repo archive {github_repo}"
                    )
            except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
                print(
                    f"rv project remove: gh unavailable. "
                    f"Manual step: gh repo archive {github_repo}"
                )

    _print_vault_teardown_handoff(
        plan, purge_agents=agents_archived, agents_archived_dest=agents_archived_dest
    )

    return 1 if blocked else 0


def _print_next_steps(name: str, source_path: Path, gd_installed: bool) -> None:
    """Print the discovery/next-steps surface after a successful `project new`.

    Rich closing panel at a TTY; the plain block (byte-intact) otherwise.
    """
    from .richui import should_render_rich, render_closing
    if should_render_rich():
        try:
            gd_line = (
                f"\n  [bold]rv git-discipline install --project {name}[/bold]  "
                "[dim]# commit-msg + protect-main hooks[/dim]"
                if not gd_installed else ""
            )
            body = (
                f"[bold]Project {name!r} is ready[/bold] at [dim]{source_path}[/dim]\n\n"
                "Next steps:\n"
                f"  [bold]rv status --project {name}[/bold]  [dim]# coordination state + pointers.md[/dim]\n"
                f"  [dim]# edit pointers.md as scope emerges (no gates, accrues over time)[/dim]\n"
                f"  [bold]rv wt add <task> --project {name}[/bold]  [dim]# isolated task worktree[/dim]\n"
                f"  [bold]rv note {name} new findings \"<title>\"[/bold]  [dim]# first finding[/dim]\n"
                f"  [bold]rv research add --project {name} <doi>[/bold]  [dim]# add a paper[/dim]"
                f"{gd_line}"
            )
            render_closing(body, title="rv project new")
            return
        except Exception:
            pass  # fall through to the plain block

    print(f"\nProject {name!r} is ready at {source_path}")
    print("\nNext steps:")
    print(f"  rv status --project {name}   # check coordination state + see pointers.md")
    print(f"  # Edit pointers.md as the project scope emerges (no gates, accrues over time)")
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
    # --roster is intentionally absent: every project always gets DEFAULT_ROSTER.

    # list — real implementation (SR-XP)
    sub.add_parser("list", help="List all registered projects (slug, code, roster, source).")

    # relate — declare or prune a cross-project edge (SR-XPB)
    relate_p = sub.add_parser(
        "relate",
        help=(
            "Declare or prune a cross-project edge (hub coordination act). "
            "``rv project relate <a> <b> --kind K`` grants intentional cross-project reach. "
            "``rv project relate <a> <b> --remove`` prunes a stale edge. "
            "ANTI-PATTERN: do not blanket-relate all projects — declare on genuine relatedness only."
        ),
    )
    relate_p.add_argument(
        "project_a", metavar="a", nargs="?", default=None,
        help="First project slug. Not required when --suggest is used.",
    )
    relate_p.add_argument(
        "project_b", metavar="b", nargs="?", default=None,
        help="Second project slug. Not required when --suggest is used.",
    )
    relate_p.add_argument(
        "--kind", default=None,
        help=(
            "REQUIRED when declaring.  Describes the genuine relatedness "
            "(e.g. 'shares-methodology', 'same-domain', 'sister-experiment')."
        ),
    )
    relate_p.add_argument(
        "--remove", action="store_true", default=False,
        help="Prune the declared edge instead of declaring it.",
    )
    relate_p.add_argument(
        "--suggest", action="store_true", default=False,
        help=(
            "Suggest candidate cross-project edges based on corpus similarity. "
            "Prints proposals only — NEVER auto-declares. "
            "No project arguments needed when --suggest is used."
        ),
    )
    relate_p.add_argument(
        "--top-k", dest="top_k", type=int, default=5,
        help="Number of candidate pairs to surface with --suggest (default 5).",
    )

    # edges — list declared cross-project edges (SR-XPB)
    edges_p = sub.add_parser(
        "edges",
        help="List all declared cross-project edges, or those involving a specific project.",
    )
    edges_p.add_argument(
        "--project", default=None,
        help="Filter to edges involving this project slug.",
    )

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
    # --roster is intentionally absent: every project always gets DEFAULT_ROSTER.
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

    # remove — clean local teardown (the reversal of `new`/`add`)
    rm_p = sub.add_parser(
        "remove",
        help=(
            "Clean LOCAL teardown for a registered project (deregister + prune "
            "edges + archive control/tasks/DAG-runs + clear worktrees). "
            "Default is NON-destructive: repo, .agents/<slug>/, and GitHub are "
            "left intact. Emits a VAULT-TEARDOWN handoff for the vault side."
        ),
    )
    rm_p.add_argument("slug", help="Project slug to tear down.")
    rm_p.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Print the full plan; mutate nothing.",
    )
    rm_p.add_argument(
        "--purge-repo", action="store_true", default=False,
        help="Remove the local repo checkout (guard-gated; GitHub preserved — re-clonable).",
    )
    rm_p.add_argument(
        "--purge-agents", action="store_true", default=False,
        help="Archive (never delete) .agents/<slug>/ crew memory — the one no-remote-copy artifact.",
    )
    rm_p.add_argument(
        "--archive-github", action="store_true", default=False,
        help="Run the non-destructive `gh repo archive` on the GitHub remote (off by default).",
    )
    rm_p.add_argument(
        "--force", action="store_true", default=False,
        help="Downgrade an unpushed-work guard REFUSE into a typed confirmation (never a silent bypass).",
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
                roster=DEFAULT_ROSTER,
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

    elif args.project_cmd == "relate":
        if getattr(args, "suggest", False):
            return cmd_relate_suggest(top_k=getattr(args, "top_k", 5))
        if not args.project_a or not args.project_b:
            print(
                "rv project relate: project slugs <a> and <b> are required "
                "(or use --suggest for corpus-based suggestions).",
                file=sys.stderr,
            )
            return 1
        return cmd_relate(
            a=args.project_a,
            b=args.project_b,
            kind=args.kind,
            remove=args.remove,
        )

    elif args.project_cmd == "edges":
        return cmd_edges(project=args.project)

    elif args.project_cmd == "new":
        return cmd_new(
            name=args.name,
            code=args.code,
            source_dir=args.source_dir,
            roster=DEFAULT_ROSTER,
            zotero=args.zotero,
            git_discipline=args.git_discipline,
            force=args.force,
        )

    elif args.project_cmd == "remove":
        return cmd_remove(
            slug=args.slug,
            dry_run=args.dry_run,
            purge_repo=args.purge_repo,
            purge_agents=args.purge_agents,
            archive_github=args.archive_github,
            force=args.force,
        )

    return 0
