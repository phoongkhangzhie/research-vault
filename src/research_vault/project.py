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

from datetime import date as _date

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

    No FILL gates — no authoring burden before first crew dispatch.
    Commented prompts suggest what to fill in as the project evolves.
    """
    today = _date.today().isoformat()
    return f"""\
# Pointers — {slug}

*Read fresh by the crew via `rv status --project {slug}`.
Add pointers here as the project develops — no gates, no required fields.*

**Source dir:** `{source_dir}`

<!-- Add the design-of-record path when it exists:
design-of-record: /path/to/tasks/task-name.md
-->

<!-- Add the primary results source when known:
results: wandb.ai/... or /path/to/results/
-->

<!-- Add architecture link when written:
architecture: {source_dir}/architecture.md
-->
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
    from . import control, devlog, git_discipline as gd
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

    return 0
