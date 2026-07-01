"""note.py — OKF note creation and listing for a project.

When to use: use `rv note <project> <type> …` to create or list OKF notes for a project.
Notes follow the Open Knowledge Format: markdown + YAML frontmatter with a required `type` field.
The type determines the subdirectory: literature/, concepts/, methods/, experiments/,
findings/, mocs/, datasets/.

Path resolution: always via Config — zero hardcoded paths.
Stdlib only.
"""

import argparse
import datetime
import re
import sys
from pathlib import Path
from typing import Any

from .config import Config, load_config

# ---------------------------------------------------------------------------
# OKF note types
# ---------------------------------------------------------------------------

OKF_TYPES = frozenset({
    "literature",
    "concepts",
    "methods",
    "experiments",
    "findings",
    "mocs",
    "datasets",   # SR-8: provenance note for data artifacts (points to data, never contains it)
})


def scaffold_okf_dirs(base: Path) -> None:
    """Create OKF note-type subdirectories under *base*.

    This is the canonical helper — callers (init, project new) MUST use this
    instead of re-listing the types, so note.OKF_TYPES stays the SSOT.
    """
    for note_type in OKF_TYPES:
        (base / note_type).mkdir(parents=True, exist_ok=True)


def _today() -> str:
    return datetime.date.today().isoformat()


def _slugify(text: str) -> str:
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")[:80] or "note"


def _render_frontmatter(fields: dict[str, str]) -> str:
    lines = ["---"]
    for key, val in fields.items():
        lines.append(f"{key}: {val}")
    lines.append("---")
    return "\n".join(lines)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    fm_block = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")
    fields: dict[str, str] = {}
    for line in fm_block.splitlines():
        m = re.match(r"^(\w+):\s*(.*)$", line)
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val.startswith(("'", '"')) and val.endswith(val[0]):
                val = val[1:-1]
            fields[key] = val
    return fields, body


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_new(project: str, note_type: str, title: str, *,
            config: Config | None = None,
            note_id: str | None = None,
            tags: list[str] | None = None) -> Path:
    """Create a new OKF note of the given type for the given project.

    Returns the path to the created note file.
    Raises ValueError if note_type is not a valid OKF type.

    SR-8: for note_type == 'datasets', the template includes placeholder fields:
      location — path/URL/DOI of the actual data artifact (fill this in)
      hash     — content hash in sha256:<hex> format (fill this in)
    Anti-pattern: do NOT hand-copy a data path into a finding — file a datasets/
    provenance note and afterok on it, so lineage is structural.
    """
    if note_type not in OKF_TYPES:
        raise ValueError(
            f"Unknown note type {note_type!r}. Valid types: {sorted(OKF_TYPES)}"
        )
    cfg = config or load_config()

    # SR-8: datasets are SHARED cross-project — live in cfg.datasets_root, not
    # in the project-scoped notes directory. A dataset note filed for one project
    # is visible and lineage-gatable from any other project.
    if note_type == "datasets":
        notes_dir = cfg.datasets_root
    else:
        notes_dir = cfg.project_notes_dir(project) / note_type
    notes_dir.mkdir(parents=True, exist_ok=True)

    slug = note_id or _slugify(title)
    note_path = notes_dir / f"{slug}.md"
    if note_path.exists():
        slug = f"{slug}-{_today()}"
        note_path = notes_dir / f"{slug}.md"

    fields: dict[str, str] = {
        "type": note_type,
        "title": title,
        "created": _today(),
    }

    # SR-8: datasets notes carry provenance-specific placeholder fields
    if note_type == "datasets":
        fields["location"] = ""   # fill in: path/URL/DOI of the data artifact
        fields["hash"] = ""       # fill in: sha256:<hex> content hash of the artifact

    if tags:
        fields["tags"] = "[" + ", ".join(tags) + "]"

    if note_type == "datasets":
        body = (
            "\n"
            "<!-- Datasets provenance note (SR-8) -->\n"
            "<!-- Fill in 'location' and 'hash' above before completing the DAG node. -->\n"
            "<!--   location: /path/to/data.csv  OR  https://...  OR  doi:10.xxx/... -->\n"
            "<!--   hash: sha256:<hex>  (run: sha256sum <file>) -->\n"
            "\n"
            "## What this dataset is\n\n"
            "<!-- Describe the dataset: domain, size, format, collection method. -->\n\n"
            "## Provenance\n\n"
            "<!-- Which step/commit/input-datasets produced this? -->\n\n"
            "## Schema\n\n"
            "<!-- Column/field descriptions (optional — used for schema-shape validation). -->\n"
        )
    else:
        body = "\n<!-- Write your note here -->\n"

    note_path.write_text(_render_frontmatter(fields) + "\n" + body, encoding="utf-8")
    return note_path


def cmd_list(project: str, note_type: str | None = None, *,
             config: Config | None = None) -> list[dict[str, Any]]:
    """List OKF notes for the given project.

    If note_type is given, list only that type's subdirectory.
    Returns list of {path, fields} dicts.

    SR-8: datasets are SHARED — cmd_list for note_type='datasets' scans
    cfg.datasets_root rather than the project-scoped notes directory.
    """
    cfg = config or load_config()
    base = cfg.project_notes_dir(project)

    if note_type:
        types_to_scan = [note_type]
    else:
        types_to_scan = sorted(OKF_TYPES)

    notes = []
    for t in types_to_scan:
        # SR-8: datasets live in the shared datasets_root, not project_notes_dir/datasets/
        if t == "datasets":
            subdir = cfg.datasets_root
        else:
            subdir = base / t
        if not subdir.exists():
            continue
        for p in sorted(subdir.glob("*.md")):
            text = p.read_text(encoding="utf-8")
            fields, _ = _parse_frontmatter(text)
            notes.append({"path": p, "fields": fields})
    return notes


def cmd_check(project: str, *, config: Config | None = None) -> list[str]:
    """Validate OKF notes for the given project.

    Checks that:
    - Each note has a `type` frontmatter field
    - The `type` value matches its parent directory name (non-datasets types)
    - The `type` is a known OKF type
    - SR-8: datasets notes (scanned from cfg.datasets_root) have non-empty
      `location` and `hash` fields. The type-dir check is skipped for datasets
      since datasets_root may have any directory name.

    SR-8 note: datasets are SHARED across projects. cmd_check scans
    cfg.datasets_root for the datasets type (same root for all projects);
    the 6 other OKF types remain project-scoped in project_notes_dir.

    Returns a list of violation strings (empty = all clear).
    """
    cfg = config or load_config()
    base = cfg.project_notes_dir(project)
    violations = []

    for t in OKF_TYPES:
        # SR-8: datasets live in the shared datasets_root
        if t == "datasets":
            subdir = cfg.datasets_root
        else:
            subdir = base / t
        if not subdir.exists():
            continue

        for p in sorted(subdir.glob("*.md")):
            text = p.read_text(encoding="utf-8")
            fields, _ = _parse_frontmatter(text)
            note_type = fields.get("type", "")

            if not note_type:
                violations.append(f"{p}: missing 'type' frontmatter field")
                continue

            if note_type not in OKF_TYPES:
                violations.append(f"{p}: unknown type {note_type!r}")
                continue

            if t == "datasets":
                # For the shared datasets type, check type == "datasets" (not type-dir
                # match, since datasets_root may have any directory name).
                if note_type != "datasets":
                    violations.append(
                        f"{p}: expected type='datasets', got {note_type!r}"
                    )
                # SR-8: datasets notes must have location and hash filled in
                if not fields.get("location", "").strip():
                    violations.append(
                        f"{p}: datasets note missing 'location' field "
                        f"(path/URL/DOI of the actual data artifact)"
                    )
                if not fields.get("hash", "").strip():
                    violations.append(
                        f"{p}: datasets note missing 'hash' field "
                        f"(content hash in sha256:<hex> format)"
                    )
            else:
                # Standard OKF type-dir contract for the 6 project-scoped types
                if note_type != t:
                    violations.append(
                        f"{p}: type={note_type!r} but file is in {t!r} directory"
                    )

    return violations


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(parent: argparse._SubParsersAction | None = None) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Build the argument parser for the `note` verb.

    When to use: use `rv note <project> <subcommand>` to create or inspect OKF notes.
    Notes are typed markdown files (literature, concepts, methods, experiments, findings,
    mocs, datasets) stored under the project's notes directory. The type field in
    frontmatter is enforced. datasets notes are SR-8 provenance metadata — they POINT to
    data artifacts (path/URL/DOI + content-hash), never contain the data itself.
    Anti-pattern: do NOT hand-copy a data path into a finding — file a datasets/
    provenance note and afterok on it so lineage is structural.
    """
    desc = "Create and list OKF notes for a project."
    if parent is not None:
        p = parent.add_parser("note", help="OKF note management.", description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv note", description=desc)

    p.add_argument("project", help="Project slug.")

    sub = p.add_subparsers(dest="note_cmd", required=True)

    # new
    new_p = sub.add_parser("new", help="Create a new OKF note.")
    new_p.add_argument("type", choices=sorted(OKF_TYPES), help="OKF note type.")
    new_p.add_argument("title", help="Note title.")
    new_p.add_argument("--id", dest="note_id", default=None,
                       help="Override the auto-generated slug.")
    new_p.add_argument("--tags", nargs="*", default=None,
                       help="Optional tags.")

    # list
    list_p = sub.add_parser("list", help="List OKF notes for a project.")
    list_p.add_argument("--type", dest="note_type", default=None,
                        choices=sorted(OKF_TYPES), help="Filter by OKF type.")

    # check
    sub.add_parser("check", help="Validate OKF note frontmatter.")

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch note subcommands. Returns exit code."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv note: config error: {e}", file=sys.stderr)
        return 1

    try:
        if args.note_cmd == "new":
            path = cmd_new(
                args.project, args.type, args.title,
                config=cfg,
                note_id=args.note_id,
                tags=args.tags,
            )
            print(f"Created: {path}")
            return 0

        elif args.note_cmd == "list":
            notes = cmd_list(args.project, args.note_type, config=cfg)
            if not notes:
                msg = f"No notes for {args.project!r}"
                if args.note_type:
                    msg += f" (type={args.note_type!r})"
                print(msg + ".")
                return 0
            print(f"Notes for {args.project!r}:")
            for note in notes:
                t = note["fields"].get("type", "?")
                title = note["fields"].get("title", note["path"].stem)
                print(f"  [{t:<12}] {note['path'].stem}: {title}")
            return 0

        elif args.note_cmd == "check":
            violations = cmd_check(args.project, config=cfg)
            if not violations:
                print(f"rv note check: OK — {args.project!r}")
                return 0
            for v in violations:
                print(f"  VIOLATION: {v}")
            return 1

    except (ValueError, KeyError) as e:
        print(f"rv note: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"rv note: unexpected error: {e}", file=sys.stderr)
        return 1

    return 0
