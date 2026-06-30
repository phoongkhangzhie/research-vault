"""control.py — project coordination control-file management.

When to use: use `rv control <project> …` to initialize, read, or update the coordination
bus for a project. Each project has one control file at `control/<project>.md` (configurable).
The control file is the async handshake record between the manager, hub, and Khang.

Control file structure:
  # CONTROL — <project>
  ## Inbox   (hub/Khang → manager)
  ## Handshakes  (in-flight, needs the other side)
  ## Outbox  (manager → hub/Khang)
  ## Open / blockers

All paths resolved from Config — zero hardcoded paths.
Stdlib only.
"""

import argparse
import datetime
import re
import sys
from pathlib import Path

from .config import Config, load_config

# ---------------------------------------------------------------------------
# Control file format
# ---------------------------------------------------------------------------

SECTIONS = ["Inbox", "Handshakes", "Outbox", "Open / blockers"]


def _today() -> str:
    return datetime.date.today().isoformat()


def _render_control_file(project: str, note: str = "") -> str:
    """Render a blank control file skeleton for a project."""
    intro = note or f"Created {_today()}."
    return f"""# CONTROL — {project}

The manager bus for this project: an async, durable handshake file. The manager reads it
at the top of each turn; the hub reads it to build the brief. Markdown, near-free, legible.

> *{intro}*

## Inbox  (hub/Khang → manager)
  _(none)_

## Handshakes  (in-flight, needs the other side)
  _(none)_

## Outbox  (manager → hub/Khang)
  _(none)_

## Open / blockers
  _(none)_
"""


def _parse_sections(text: str) -> dict[str, str]:
    """Parse a control file into its sections. Returns dict of section name → content."""
    sections: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []

    for line in text.splitlines():
        m = re.match(r"^## (.+?)(?:\s+\(.*\))?$", line)
        if m:
            if current_name is not None:
                sections[current_name] = "\n".join(current_lines).strip()
            current_name = m.group(1).strip()
            current_lines = []
        elif current_name is not None:
            current_lines.append(line)

    if current_name is not None:
        sections[current_name] = "\n".join(current_lines).strip()

    return sections


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init(project: str, *, config: Config | None = None,
             note: str = "", overwrite: bool = False) -> Path:
    """Initialize a control file for a project.

    Raises FileExistsError if the file already exists and overwrite=False.
    Returns the path to the created control file.
    """
    cfg = config or load_config()
    control_file = cfg.project_control_file(project)
    control_file.parent.mkdir(parents=True, exist_ok=True)

    if control_file.exists() and not overwrite:
        raise FileExistsError(
            f"Control file already exists: {control_file}. Use --overwrite to replace."
        )

    control_file.write_text(_render_control_file(project, note), encoding="utf-8")
    return control_file


def cmd_view(project: str, *, config: Config | None = None) -> str:
    """Return the full content of the project's control file."""
    cfg = config or load_config()
    control_file = cfg.project_control_file(project)
    if not control_file.exists():
        raise FileNotFoundError(
            f"No control file for {project!r}. Run `rv control {project} init` to create one."
        )
    return control_file.read_text(encoding="utf-8")


def cmd_check(project: str, *, config: Config | None = None) -> list[str]:
    """Validate the control file structure for a project.

    Returns a list of violation strings (empty = all clear).
    Checks that all required sections are present.
    """
    cfg = config or load_config()
    control_file = cfg.project_control_file(project)

    if not control_file.exists():
        return [f"Missing control file: {control_file}"]

    text = control_file.read_text(encoding="utf-8")
    parsed = _parse_sections(text)
    violations = []

    for section in SECTIONS:
        if section not in parsed:
            violations.append(f"Missing section: '## {section}'")

    return violations


def cmd_inbox(project: str, message: str, *, config: Config | None = None) -> Path:
    """Append a dated message to the Inbox section of a control file.

    Creates the control file if it doesn't exist.
    """
    cfg = config or load_config()
    control_file = cfg.project_control_file(project)

    if not control_file.exists():
        cmd_init(project, config=cfg)

    text = control_file.read_text(encoding="utf-8")
    entry = f"- _{_today()}_ — {message}"

    # Find the Inbox section and append after it
    inbox_m = re.search(r"(## Inbox.*?)\n(  _\(none\)_)?", text, re.DOTALL)
    if inbox_m:
        # Replace "(none)" placeholder or just append
        if "_(none)_" in text:
            text = text.replace("  _(none)_", entry, 1)
        else:
            # Find end of Inbox section (next ## or EOF)
            next_section = re.search(r"\n## ", text[inbox_m.end():])
            if next_section:
                insert_pos = inbox_m.end() + next_section.start()
                text = text[:insert_pos] + "\n" + entry + text[insert_pos:]
            else:
                text = text.rstrip() + "\n" + entry + "\n"
    else:
        text = text.rstrip() + f"\n\n## Inbox  (hub/Khang → manager)\n{entry}\n"

    control_file.write_text(text, encoding="utf-8")
    return control_file


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(parent: argparse._SubParsersAction | None = None) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Build the argument parser for the `control` verb.

    When to use: use `rv control <project> <subcommand>` to manage the coordination control
    file for a project. The control file is the async manager-hub handshake bus. Use `init`
    to create it, `view` to inspect, `check` to validate structure, `inbox` to log a message.
    """
    desc = "Manage the project coordination control file (the manager-hub bus)."
    if parent is not None:
        p = parent.add_parser("control", help="Project coordination control file.",
                              description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv control", description=desc)

    sub = p.add_subparsers(dest="control_cmd", required=True)

    # init
    init_p = sub.add_parser("init", help="Create the control file for a project.")
    init_p.add_argument("project", help="Project slug.")
    init_p.add_argument("--note", default="", help="Optional creation note (appears in the file header).")
    init_p.add_argument("--overwrite", action="store_true",
                        help="Overwrite if the control file already exists.")

    # view
    view_p = sub.add_parser("view", help="Print the control file.")
    view_p.add_argument("project", help="Project slug.")

    # check
    check_p = sub.add_parser("check", help="Validate control file structure.")
    check_p.add_argument("project", help="Project slug.")

    # inbox
    inbox_p = sub.add_parser("inbox", help="Append a message to the Inbox section.")
    inbox_p.add_argument("project", help="Project slug.")
    inbox_p.add_argument("message", help="Message text.")

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch control subcommands. Returns exit code."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv control: config error: {e}", file=sys.stderr)
        return 1

    try:
        if args.control_cmd == "init":
            path = cmd_init(args.project, config=cfg,
                            note=args.note, overwrite=args.overwrite)
            print(f"Created: {path}")
            return 0

        elif args.control_cmd == "view":
            print(cmd_view(args.project, config=cfg), end="")
            return 0

        elif args.control_cmd == "check":
            violations = cmd_check(args.project, config=cfg)
            if not violations:
                print(f"rv control check: OK — {args.project!r}")
                return 0
            for v in violations:
                print(f"  VIOLATION: {v}")
            return 1

        elif args.control_cmd == "inbox":
            path = cmd_inbox(args.project, args.message, config=cfg)
            print(f"Updated inbox: {path}")
            return 0

    except (KeyError, FileNotFoundError, FileExistsError) as e:
        print(f"rv control: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"rv control: unexpected error: {e}", file=sys.stderr)
        return 1

    return 0
