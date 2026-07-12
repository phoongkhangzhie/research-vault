# SPDX-License-Identifier: AGPL-3.0-or-later
"""devlog.py — DEVLOG management, structure lint, freshness check, and index/search
for projects.

When to use: use `rv devlog <project> <subcommand>` to seed, append to, check the
structure/freshness of, or search a project's DEVLOG.md. The DEVLOG is the grounded
per-project record of decisions and progress, split into three zones with different
lifecycles — see `data/doctrine/devlog-journal.md` for the full convention:

  ## Now         — mutable resume-point, overwritten each session (not appended)
  ## Decisions   — append-only, immutable ADR-lite ledger (D-NNN records)
  ## Log         — append-only terse daybook, one dated entry per session

Use `rv devlog index` and `rv devlog search` to navigate the DEVLOG without loading the
whole file. Anti-pattern: do NOT grep or cat DEVLOG.md directly — use the index face.

Check semantics (`rv devlog check`):
  MISSING — no DEVLOG.md found                                      -> hard FAIL
  FAIL    — a structure lint failed (missing Now / dangling
            superseded-by / empty Rejected field)                   -> hard FAIL
  WARN    — the DEVLOG is stale or has no dated Log entries yet      -> non-blocking
  OK      — all else

Only MISSING and FAIL are hard-blocking; staleness is a surfaced signal, never a gate
(cadence compliance is not something to game).

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
# Constants
# ---------------------------------------------------------------------------

STALE_DAYS = 14

# A Log entry header: "### 2026-07-12" — bare date, no trailing text.
_DATE_ENTRY_RE = re.compile(r"^### (\d{4}-\d{2}-\d{2})\s*$", re.MULTILINE)

# A Decisions record header: "### D-003 · 2026-07-12 · in-force"
_DECISION_HEADER_RE = re.compile(
    r"^### (D-\d+) · (\d{4}-\d{2}-\d{2}) · (.+?)\s*$", re.MULTILINE
)

_SUPERSEDED_BY_RE = re.compile(r"superseded-by\s+(D-\d+)")


def _today() -> str:
    return datetime.date.today().isoformat()


# ---------------------------------------------------------------------------
# Zone / seed template helpers
# ---------------------------------------------------------------------------

def _seed_now_body() -> str:
    return "_(current state — 1-3 lines; open threads/next; active decision pointers)_\n"


def _seed_decisions_body() -> str:
    return "_(no decisions recorded yet)_\n"


def _seed_log_entry(date: str) -> str:
    return f"### {date}\n\n#### Done\n- _(add what was accomplished)_\n"


def _zone_header_re(name: str) -> re.Pattern:
    return re.compile(r"^## " + re.escape(name) + r"\s*$", re.MULTILINE)


def _zone_body_bounds(content: str, name: str) -> tuple[int, int] | None:
    """Return (body_start, body_end) offsets for a `## <name>` zone's body,
    or None if the zone header isn't present. body_end is the start of the
    next top-level `## ` header, or end-of-file."""
    m = _zone_header_re(name).search(content)
    if not m:
        return None
    body_start = m.end() + 1 if content[m.end():m.end() + 1] == "\n" else m.end()
    next_m = re.compile(r"^## ", re.MULTILINE).search(content, body_start)
    body_end = next_m.start() if next_m else len(content)
    return body_start, body_end


def _replace_zone_body(content: str, name: str, new_body: str) -> str:
    """Overwrite a zone's body wholesale (used for the mutable `Now` zone)."""
    bounds = _zone_body_bounds(content, name)
    stripped = new_body.rstrip("\n")
    if bounds is None:
        return content.rstrip() + f"\n\n## {name}\n\n{stripped}\n"
    start, end = bounds
    return content[:start] + "\n" + stripped + "\n\n" + content[end:]


def _next_decision_id(content: str) -> int:
    ids = [int(m.group(1)[2:]) for m in _DECISION_HEADER_RE.finditer(content)]
    return max(ids) + 1 if ids else 1


def _compose_decision_body(text: str, touches: str | None) -> str:
    """Build a decision record body. If `text` already carries the ADR field
    markers (composed by the caller), use it as-is; otherwise wrap it into
    the standard field template with honest defaults."""
    if "**Rejected:**" in text or "**Context:**" in text:
        body = text.strip()
    else:
        body = (
            "**Context:** _(n/a — quick-appended decision)_\n"
            f"**Decision:** {text}\n"
            "**Rejected:** _(no alternative considered)_\n"
            "**Consequences:** _(n/a)_"
        )
    if touches:
        if "**Touches:**" in body:
            body = re.sub(r"\*\*Touches:\*\*.*", f"**Touches:** {touches}", body)
        else:
            body = body.rstrip() + f"\n**Touches:** {touches}"
    elif "**Touches:**" not in body:
        body = body.rstrip() + "\n**Touches:** _(none)_"
    return body.rstrip() + "\n"


def _prepend_decision(
    content: str, date: str, text: str, touches: str | None, *, status: str = "in-force"
) -> str:
    decision_id = _next_decision_id(content)
    body = _compose_decision_body(text, touches)
    entry = f"### D-{decision_id:03d} · {date} · {status}\n\n{body}\n"

    bounds = _zone_body_bounds(content, "Decisions")
    if bounds is None:
        return content.rstrip() + f"\n\n## Decisions\n\n{entry}"
    start, end = bounds
    zone_body = content[start:end]
    placeholder = _seed_decisions_body()
    if placeholder.strip() in zone_body:
        zone_body = zone_body.replace(placeholder, "", 1)
    new_zone_body = entry + zone_body.lstrip("\n")
    return content[:start] + "\n" + new_zone_body + content[end:]


def _append_done_bullet(content: str, date: str, text: str, touches: str | None) -> str:
    bullet = f"- {text}"
    if touches:
        bullet += f" (touches: {touches})"
    bullet += "\n"

    date_header_re = re.compile(r"^### " + re.escape(date) + r"\s*$", re.MULTILINE)
    m = date_header_re.search(content)

    if not m:
        # New dated entry, newest-on-top within the Log zone.
        new_entry = f"### {date}\n\n#### Done\n{bullet}\n"
        log_bounds = _zone_body_bounds(content, "Log")
        if log_bounds is None:
            return content.rstrip() + f"\n\n## Log\n\n{new_entry}"
        start, _end = log_bounds
        return content[:start] + "\n" + new_entry + content[start:]

    # Today's entry already exists — locate its block (until the next
    # `### <date>`-shaped header, or end of file) and its `#### Done` section.
    block_re = re.compile(
        r"(^### " + re.escape(date) + r"\s*\n)(.*?)(?=\n### |\Z)",
        re.DOTALL | re.MULTILINE,
    )
    bm = block_re.search(content)
    block_body = bm.group(2)

    done_re = re.compile(r"(#### Done\n)(.*?)(?=\n#### |\Z)", re.DOTALL)
    dm = done_re.search(block_body)
    if dm:
        section_body = dm.group(2)
        if "_(add" in section_body:
            new_section = bullet
        else:
            new_section = section_body.rstrip("\n") + "\n" + bullet
        block_body = block_body[:dm.start(2)] + new_section + block_body[dm.end(2):]
    else:
        block_body = block_body.rstrip() + f"\n\n#### Done\n{bullet}"

    return content[:bm.start(2)] + block_body + content[bm.end(2):]


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init(project: str, note: str = "", *,
             config: Config | None = None, overwrite: bool = False) -> Path:
    """Create a DEVLOG.md for the given project, seeded with the 3-zone
    structure (Now / Decisions / Log).

    Raises FileExistsError if the DEVLOG already exists and overwrite=False.
    """
    cfg = config or load_config()
    devlog_path = cfg.project_devlog(project)
    devlog_path.parent.mkdir(parents=True, exist_ok=True)

    if devlog_path.exists() and not overwrite:
        raise FileExistsError(
            f"DEVLOG already exists: {devlog_path}. Use --overwrite to reset."
        )

    header = note or f"Project {project!r} — devlog started."
    content = (
        f"# DEVLOG — {project}\n\n"
        "Newest entries on top. Three zones: Now (mutable resume-point) · "
        "Decisions (append-only ADR-lite ledger) · Log (append-only daybook). "
        "See data/doctrine/devlog-journal.md.\n"
        f"{header}\n\n"
        "## Now\n\n" + _seed_now_body() + "\n"
        "## Decisions\n\n" + _seed_decisions_body() + "\n"
        "## Log\n\n" + _seed_log_entry(_today()) + "\n"
    )

    devlog_path.write_text(content, encoding="utf-8")
    return devlog_path


def cmd_append(project: str, section: str, text: str, *,
               config: Config | None = None,
               date: str | None = None,
               touches: str | None = None) -> Path:
    """Update a zone of the DEVLOG.

    section:
      "Done"      — append a bullet to today's (or --date's) Log entry.
      "Decisions" — prepend a new ADR-lite record to the Decisions ledger
                    (auto-assigns the next D-NNN).
      "Now"       — REPLACE the Now zone body wholesale (it is mutable).

    touches: an OKF cross-link (e.g. "[title](/path/to/note.md)") stamped
    into the entry — the journal-to-note direction. Only ever set OUT of the
    journal; an OKF note must never link back to a dated journal entry.
    """
    cfg = config or load_config()
    devlog_path = cfg.project_devlog(project)

    if not devlog_path.exists():
        cmd_init(project, config=cfg)

    content = devlog_path.read_text(encoding="utf-8")
    entry_date = date or _today()

    if section == "Now":
        content = _replace_zone_body(content, "Now", text)
    elif section == "Decisions":
        content = _prepend_decision(content, entry_date, text, touches)
    elif section == "Done":
        content = _append_done_bullet(content, entry_date, text, touches)
    else:
        raise ValueError(
            f"Unknown devlog section {section!r} — expected one of: Done, Decisions, Now."
        )

    devlog_path.write_text(content, encoding="utf-8")
    return devlog_path


def _iter_decision_blocks(content: str) -> list[tuple[str, str, str, str]]:
    """Return [(decision_id, date, status, body), ...] for every Decisions
    record found anywhere in the file (in document order)."""
    matches = list(_DECISION_HEADER_RE.finditer(content))
    blocks = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        blocks.append((m.group(1), m.group(2), m.group(3).strip(), content[start:end]))
    return blocks


def _extract_field(body: str, name: str) -> str:
    # [ \t]* (not \s*) — must not cross the newline into the next field's line.
    m = re.search(r"\*\*" + re.escape(name) + r":\*\*[ \t]*(.*)", body)
    return m.group(1).strip() if m else ""


def cmd_check(project: str, *, config: Config | None = None) -> dict:
    """Check DEVLOG structure and freshness for the given project.

    Returns {"status": ..., "message": ..., "errors": [...], "warnings": [...]}
    status is one of: OK | WARN | FAIL | MISSING.

    Only MISSING (no DEVLOG.md) and FAIL (a structure lint failed) are
    hard-blocking; staleness/no-Log-entries-yet is surfaced as WARN only.
    """
    cfg = config or load_config()
    devlog_path = cfg.project_devlog(project)

    if not devlog_path.exists():
        return {
            "status": "MISSING",
            "message": f"No DEVLOG.md found at: {devlog_path}",
            "errors": [], "warnings": [],
        }

    content = devlog_path.read_text(encoding="utf-8")
    errors: list[str] = []
    warnings: list[str] = []

    # Lint (a): '## Now' zone present.
    if not _zone_header_re("Now").search(content):
        errors.append("Missing '## Now' zone — every DEVLOG.md must have a Now head.")

    # Lint (b): every 'superseded-by D-NNN' resolves to a recorded decision.
    decision_ids = {m.group(1) for m in _DECISION_HEADER_RE.finditer(content)}
    for ref in _SUPERSEDED_BY_RE.findall(content):
        if ref not in decision_ids:
            errors.append(
                f"'superseded-by {ref}' does not resolve to any recorded decision "
                f"({ref} not found in the Decisions ledger)."
            )

    # Lint (c): no decision record has an empty 'Rejected' field.
    for decision_id, d_date, _status, body in _iter_decision_blocks(content):
        if not _extract_field(body, "Rejected"):
            errors.append(
                f"Decision {decision_id} ({d_date}) has an empty 'Rejected' field."
            )

    # Staleness — surfaced as a WARN, never a hard gate.
    dates = _DATE_ENTRY_RE.findall(content)
    if not dates:
        warnings.append("DEVLOG.md has no dated Log entries yet.")
    else:
        latest_str = max(dates)
        try:
            latest_date = datetime.date.fromisoformat(latest_str)
            age_days = (datetime.date.today() - latest_date).days
            if age_days > STALE_DAYS:
                warnings.append(
                    f"Latest Log entry is {latest_str!r} ({age_days} days ago) — "
                    f"stale (threshold: {STALE_DAYS} days)."
                )
        except ValueError:
            warnings.append(f"Could not parse latest Log entry date: {latest_str!r}.")

    if errors:
        status = "FAIL"
    elif warnings:
        status = "WARN"
    else:
        status = "OK"

    message = "; ".join(errors + warnings) if (errors or warnings) else "DEVLOG structure OK."
    return {"status": status, "message": message, "errors": errors, "warnings": warnings}


def cmd_view(project: str, *, config: Config | None = None, lines: int = 50) -> str:
    """Return the first `lines` lines of the project DEVLOG."""
    cfg = config or load_config()
    devlog_path = cfg.project_devlog(project)
    if not devlog_path.exists():
        raise FileNotFoundError(f"No DEVLOG.md at: {devlog_path}")
    content = devlog_path.read_text(encoding="utf-8")
    return "\n".join(content.splitlines()[:lines])


# ---------------------------------------------------------------------------
# INDEX / SEARCH commands (append-only-record read face)
# ---------------------------------------------------------------------------

def _parse_entries(devlog_path: Path) -> list[dict]:
    """Parse dated Log entries AND Decisions records from a DEVLOG.md into a
    unified, date-ordered list.

    Returns list of dicts: {date, summary, body, lineno, kind}.
    kind is "log" or "decision". summary is a one-liner for index/search.

    Does NOT load the whole file into one string for search — iterates lines.
    """
    if not devlog_path.exists():
        return []

    text = devlog_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    entries: list[dict] = []
    current: dict | None = None
    current_body_lines: list[str] = []

    def _flush() -> None:
        if current is None:
            return
        body = "\n".join(current_body_lines).strip()
        summary = ""
        for ln in current_body_lines:
            s = ln.strip()
            if s and not s.startswith("#") and not s.startswith("- _(add") and "_(no decisions" not in s:
                s = s[2:].strip() if s.startswith("- ") else s
                s = re.sub(r"^\*\*\w+:\*\*\s*", "", s)
                summary = s
                break
        if not summary:
            summary = body[:80] if body else "(empty)"
        entries.append({
            "date": current["date"],
            "summary": summary,
            "body": body,
            "lineno": current["lineno"],
            "kind": current["kind"],
        })

    for lineno, line in enumerate(lines, 1):
        dm = _DATE_ENTRY_RE.match(line)
        decm = _DECISION_HEADER_RE.match(line)
        if dm:
            _flush()
            current = {"date": dm.group(1), "lineno": lineno, "kind": "log"}
            current_body_lines = []
        elif decm:
            _flush()
            current = {"date": decm.group(2), "lineno": lineno, "kind": "decision"}
            current_body_lines = []
        elif current is not None:
            current_body_lines.append(line)

    _flush()
    entries.sort(key=lambda e: (e["date"], e["lineno"]), reverse=True)
    return entries


def cmd_index(project: str, *, config: Config | None = None) -> list[dict]:
    """Return a one-liner index of all dated DEVLOG entries (Log entries and
    Decisions records), newest first.

    Returns list of dicts: {date, summary}.
    Idempotent: calling twice returns the same list.
    Does NOT require loading the whole file to get the index.

    Anti-pattern: do NOT grep/cat DEVLOG.md to find entries — use this instead.
    """
    cfg = config or load_config()
    devlog_path = cfg.project_devlog(project)
    entries = _parse_entries(devlog_path)
    return [{"date": e["date"], "summary": e["summary"]} for e in entries]


def cmd_search(project: str, query: str, *, config: Config | None = None) -> list[dict]:
    """Search dated DEVLOG entries (Log and Decisions) for a keyword/phrase.

    Returns list of matching dicts: {date, summary, body}.
    Case-insensitive substring match against entry body.

    Anti-pattern: do NOT grep the raw DEVLOG file — use this for indexed access.
    """
    cfg = config or load_config()
    devlog_path = cfg.project_devlog(project)
    entries = _parse_entries(devlog_path)
    q_low = query.lower()
    results = []
    for e in entries:
        if q_low in e["body"].lower() or q_low in e["summary"].lower():
            results.append({
                "date": e["date"],
                "summary": e["summary"],
                "body": e["body"],
            })
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(parent: argparse._SubParsersAction | None = None) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Build the argument parser for the `devlog` verb.

    When to use: use `rv devlog <project> <subcommand>` to manage a project's DEVLOG.md.
    The DEVLOG is the grounded per-project record, split into three zones — Now
    (mutable resume-point), Decisions (append-only ADR-lite ledger), Log (append-only
    daybook). Use `check` in CI to enforce DEVLOG structure/presence. Use `append` to
    update a zone. Use `index` to get a one-liner per entry. Use `search` to find
    entries by keyword.

    Anti-pattern: do NOT grep/cat DEVLOG.md to find or read entries — that loads the whole
    file and misses the structured index. Use `rv devlog index` and `rv devlog search` instead.
    """
    desc = "Manage project DEVLOG.md — the grounded decision and progress record."
    if parent is not None:
        p = parent.add_parser("devlog", help="Project DEVLOG management.", description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv devlog", description=desc)

    p.add_argument("project", help="Project slug.")

    sub = p.add_subparsers(dest="devlog_cmd", required=True)

    # init
    init_p = sub.add_parser("init", help="Create the DEVLOG.md for a project.")
    init_p.add_argument("--note", default="", help="Optional header note.")
    init_p.add_argument("--overwrite", action="store_true")

    # append
    app_p = sub.add_parser(
        "append",
        help="Update a DEVLOG zone: append to Done/Decisions, or replace Now wholesale.",
    )
    app_p.add_argument("section", choices=["Done", "Decisions", "Now"],
                       help="Zone to update.")
    app_p.add_argument("text", help="Bullet/record text (or the full Now body).")
    app_p.add_argument("--date", default=None, help="Override date (YYYY-MM-DD).")
    app_p.add_argument(
        "--touches", default=None,
        help="OKF cross-link to stamp into the entry, e.g. '[title](/path/to/note.md)'.",
    )

    # check
    sub.add_parser("check", help="Check DEVLOG structure and freshness.")

    # view
    view_p = sub.add_parser("view", help="Print the top of the DEVLOG.")
    view_p.add_argument("--lines", type=int, default=50, help="Number of lines to show.")

    # index
    sub.add_parser(
        "index",
        help="Print a one-liner per dated entry (the structured index face).",
    )

    # search
    search_p = sub.add_parser(
        "search",
        help="Search DEVLOG entries by keyword (anti-pattern: do not grep the raw file).",
    )
    search_p.add_argument("query", help="Keyword or phrase to search for.")

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch devlog subcommands. Returns exit code."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv devlog: config error: {e}", file=sys.stderr)
        return 1

    try:
        if args.devlog_cmd == "init":
            path = cmd_init(args.project, args.note, config=cfg, overwrite=args.overwrite)
            print(f"Created: {path}")
            return 0

        elif args.devlog_cmd == "append":
            path = cmd_append(args.project, args.section, args.text,
                              config=cfg, date=args.date, touches=args.touches)
            print(f"Updated: {path}")
            return 0

        elif args.devlog_cmd == "check":
            result = cmd_check(args.project, config=cfg)
            status = result["status"]
            print(f"rv devlog check: {status} — {args.project!r}: {result['message']}")
            for e in result["errors"]:
                print(f"  FAIL: {e}", file=sys.stderr)
            for w in result["warnings"]:
                print(f"  WARN: {w}", file=sys.stderr)
            return 0 if status in ("OK", "WARN") else 1

        elif args.devlog_cmd == "view":
            print(cmd_view(args.project, config=cfg, lines=args.lines))
            return 0

        elif args.devlog_cmd == "index":
            entries = cmd_index(args.project, config=cfg)
            if not entries:
                print(f"rv devlog index: no dated entries in {args.project!r}")
                return 0
            for e in entries:
                print(f"  {e['date']} — {e['summary'][:80]}")
            return 0

        elif args.devlog_cmd == "search":
            results = cmd_search(args.project, args.query, config=cfg)
            if not results:
                print(f"rv devlog search: no matches for {args.query!r}")
                return 0
            for r in results:
                print(f"  {r['date']} — {r['summary'][:80]}")
            return 0

    except (KeyError, FileNotFoundError, FileExistsError) as e:
        print(f"rv devlog: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"rv devlog: unexpected error: {e}", file=sys.stderr)
        return 1

    return 0
