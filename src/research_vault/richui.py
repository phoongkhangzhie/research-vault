"""richui.py — rich rendering for the onboarding surfaces (rv check / init / onboard).

Additive by construction: every renderer reads the SAME result dict as the plain
report and the programmatic contract — it never recomputes state.  The console is
auto-detected: a non-TTY stream, ``NO_COLOR``, or ``RV_PLAIN`` degrades to the plain
``report`` string, so piped output and tests (which assert on the dict / plain text)
are unchanged.

This module provides STRUCTURE (tables, panels, layout).  Palette, borders, and
typographic polish are the designer's follow-up pass — the styling knobs are kept
in one place (:data:`_STYLE`) so that pass is a localized edit.

``rich`` is a core dependency, so importing this module is always safe.
"""
from __future__ import annotations

import os
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Style knobs — the design system, in one place (the single edit point).
# ---------------------------------------------------------------------------
# Research Vault's identity is ink-navy + brass — the disciplined research crew.
# Translated to a terminal palette: navy is the print ink, but on a terminal
# (dark-bg is the dev default) it reads as invisible, so the STRUCTURAL neutral
# becomes a lifted *steel-blue* that carries the same cool, disciplined character.
# Every hex is a mid-tone chosen to stay legible on BOTH light and dark terminals
# (rich down-samples to 256/16-colour terminals automatically).
#
#   brass  #C6A24A  the one accent — titles + "locked, your move" (crew identity)
#   teal   #3AA99A  success / unlocked / OK        (identity easy-teal, lifted)
#   clay   #C15F3C  hard failure                   (identity hard-clay, lifted)
#   steel  #5B6683  structural neutral — borders   (navy, lifted for the terminal)
#   slate  #7D8CA8  optional / neutral info
#   link   #6FA8C7  cool link-blue, underlined + OSC-8 clickable
_STYLE: dict[str, str] = {
    # semantic signals
    "ok": "#3AA99A",              # unlocked / OK
    "locked": "#C6A24A",          # locked but actionable — brass, the accent
    "fail": "#C15F3C",            # hard failure
    "info": "#7D8CA8",            # optional / neutral
    "muted": "#6B7180",           # recede metadata (the Class column)
    "header": "bold #8793B0",     # table column heads — mid steel-blue, bold (legible on BOTH light & dark)
    "title": "bold #C6A24A",      # section / panel titles — brass, the one accent
    "url": "#6FA8C7 underline",   # unlock links
    # borders — steel neutral, tinted only to carry the OK/FAIL verdict
    "panel_ok_border": "#3AA99A",
    "panel_fail_border": "#C15F3C",
    "panel_init_border": "#5B6683",
    "border": "#5B6683",          # neutral steel — tables + the onboard header
}

# Tables use a light, editorial box (a header underline, no heavy frame) — the
# disciplined-not-flashy register.  Imported lazily so a rich-less import is safe.

def _table_box() -> Any:
    from rich import box
    return box.SIMPLE_HEAD


# Panel-border resolution by semantic kind — the single dispatch every panel uses.
_PANEL_BORDER: dict[str, str] = {
    "ok": _STYLE["panel_ok_border"],
    "fail": _STYLE["panel_fail_border"],
    "init": _STYLE["panel_init_border"],
    "neutral": _STYLE["border"],
}


# ---------------------------------------------------------------------------
# Status marks — ONE glyph+colour language shared by every verb (the S-polish
# fix for glyph/word drift).  Colour axis is the load-bearing signal and is
# fully consistent: teal = good · clay = hard fail · brass = your-move
# (locked / warn / pending) · steel-grey = benign skip / n-a.  Glyph reinforces
# it (and carries the state for a colour-blind reader).  Glyphs verified to
# render on truecolor + 256/16-colour terminals (rich down-samples the colour).
# ---------------------------------------------------------------------------
_MARKS: dict[str, tuple[str, str]] = {
    "ok":      ("●", _STYLE["ok"]),        # pass / OK / unlocked / installed / done
    "fail":    ("✗", _STYLE["fail"]),      # hard failure
    "warn":    ("○", _STYLE["locked"]),    # actionable gap — WARN / missing (your move)
    "locked":  ("○", _STYLE["locked"]),    # locked-but-actionable
    "pending": ("◐", _STYLE["locked"]),    # in-flight / not-yet-terminal
    "skip":    ("–", _STYLE["info"]),      # benign skip / optional / n-a
    "info":    ("–", _STYLE["info"]),
}


def status_glyph(kind: str) -> str:
    """Return the coloured status glyph for ``kind`` (see :data:`_MARKS`)."""
    glyph, colour = _MARKS.get(kind, _MARKS["info"])
    return f"[{colour}]{glyph}[/]"


def status_cell(kind: str, label: str) -> str:
    """Return a coloured ``glyph label`` cell — the uniform status token.

    The single way any verb renders an OK/FAIL/locked/skip state so the
    glyph+colour language never drifts between surfaces.
    """
    glyph, colour = _MARKS.get(kind, _MARKS["info"])
    return f"[{colour}]{glyph} {label}[/]"


# ---------------------------------------------------------------------------
# Shared primitives — the design-system factory (S1).
#
# Every human renderer builds on these helpers so the palette, box style, and
# panel borders live in exactly ONE place (:data:`_STYLE` / :data:`_PANEL_BORDER`).
# NO verb imports ``rich.*`` colours directly; NO renderer hand-rolls a Table/Panel.
# ---------------------------------------------------------------------------

def make_header(title: str, subtitle: str = "", console: Any = None) -> None:
    """Emit a section header rule — brass title + optional dim subtitle.

    Generalises the ``con.rule(...)`` opener that :func:`render_check` uses.
    """
    con = console if console is not None else get_console()
    text = f"[{_STYLE['title']}]{title}[/]"
    if subtitle:
        text += f" [dim]— {subtitle}[/dim]"
    con.rule(text, style=_STYLE["border"])


def make_panel(
    body: Any,
    *,
    title: str,
    kind: str = "neutral",
    console: Any = None,
) -> None:
    """Print a bordered panel with a brass title and a kind-tinted border.

    ``kind`` ∈ {``ok``, ``fail``, ``init``, ``neutral``} selects the border via
    :data:`_PANEL_BORDER`.  Generalises the closing/required/result panels.
    """
    from rich.panel import Panel
    con = console if console is not None else get_console()
    con.print(
        Panel(
            body,
            title=f"[{_STYLE['title']}]{title}[/]",
            border_style=_PANEL_BORDER.get(kind, _STYLE["border"]),
            padding=(0, 1),
        )
    )


def make_status_table(
    columns: list[dict[str, Any]],
    rows: list[tuple[Any, ...]],
    *,
    title: str = "",
    Table: Any = None,
) -> Any:
    """Build a bordered status Table from a column spec + pre-rendered rows.

    ``columns`` is a list of dicts, each: ``name`` (required) + optional
    ``style`` / ``justify`` / ``no_wrap`` / ``overflow`` / ``ratio`` /
    ``min_width`` / ``max_width`` / ``width``.  Row cells are strings that may
    already carry rich markup (the caller owns per-cell status colouring so the
    factory stays generic).  Generalises ``_tier_matrix_table`` /
    ``_integrations_table``.  Returns the Table; the caller prints it.

    The width knobs (``overflow`` / ``ratio`` / ``min_width``) are the fix for
    the starve-the-wrapping-column trap: several ``no_wrap`` columns otherwise
    eat all the width and collapse a flexible column to a single ``…`` (with the
    row inflating to many blank lines).  Give a prose column ``overflow="fold"``
    + a ``min_width`` so it wraps whole instead of truncating.
    """
    if Table is None:
        from rich.table import Table as _T
        Table = _T
    table = Table(
        title=(f"[{_STYLE['title']}]{title}[/]" if title else None),
        box=_table_box(),
        show_lines=False,
        header_style=_STYLE["header"],
        border_style=_STYLE["border"],
        # Match the brass panel-title register — non-italic (rich italicises
        # table titles by default), so every title across the system reads alike.
        title_style=_STYLE["title"],
        title_justify="left",
        pad_edge=False,
        padding=(0, 1),
    )
    for col in columns:
        kwargs: dict[str, Any] = {
            "style": col.get("style"),
            "justify": col.get("justify", "left"),
            "no_wrap": col.get("no_wrap", False),
        }
        for opt in ("overflow", "ratio", "min_width", "max_width", "width"):
            if col.get(opt) is not None:
                kwargs[opt] = col[opt]
        table.add_column(col["name"], **kwargs)
    for row in rows:
        table.add_row(*row)
    return table


def make_kv_table(
    pairs: list[tuple[str, Any]],
    *,
    label_style: str | None = None,
    value_style: str | None = None,
) -> Any:
    """Build a borderless 2-column key/value grid — label | value.

    Labels align, values start at a common column and never truncate (fold).
    The detail-panel workhorse (compute explain, approval status, doctor rows).
    """
    from rich.table import Table
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style=label_style if label_style is not None else _STYLE["muted"], no_wrap=True)
    grid.add_column(style=value_style, overflow="fold")
    for label, value in pairs:
        grid.add_row(label, value)
    return grid


# ---------------------------------------------------------------------------
# Console detection
# ---------------------------------------------------------------------------

def should_render_rich(stream: Any = None) -> bool:
    """Return True when the rich structure should be rendered.

    False (→ fall back to plain text) when:
      - ``NO_COLOR`` is set (the de-facto standard opt-out), or
      - ``RV_PLAIN`` is set (our explicit force-plain escape hatch), or
      - the target stream is not an interactive TTY (pipes, redirects, tests).
    """
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("RV_PLAIN"):
        return False
    stream = stream if stream is not None else sys.stdout
    try:
        return bool(stream.isatty())
    except Exception:
        return False


def get_console(**kwargs: Any):
    """Construct a rich Console. Kept as a seam for tests / future config.

    ``highlight=False`` by default: rich's ReprHighlighter otherwise auto-tints
    numbers / paths / UUIDs inside our copy (e.g. the ISO timestamp digits in the
    ``rv doctor`` header flash cyan+green), which fights the disciplined palette.
    Our renderers colour intentionally via markup, so the automatic pass is only
    noise.  A caller can still pass ``highlight=True`` to opt back in.
    """
    from rich.console import Console
    kwargs.setdefault("highlight", False)
    return Console(**kwargs)


# ---------------------------------------------------------------------------
# rv check
# ---------------------------------------------------------------------------

def render_check(result: dict[str, Any], console: Any = None) -> None:
    """Render the ``rv check`` result dict as rich structure.

    Layout (the architect's spec):
      1. Header rule.
      2. Required panel — the runtime (the ONLY hard requirement).
      3. Toolkit tier-matrix Table (group · tier · coverage).
      4. Integrations Table — Capability | Unlocks | Class | Status.
      5. Result Panel (OK/FAIL, with locked-feature nudge).
    """
    from rich.table import Table

    con = console if console is not None else get_console()

    make_header("rv check", "Research Vault preflight", console=con)

    # ── 2. Required (runtime only) ───────────────────────────────────────────
    runtime_ok = bool(result.get("claude_cli"))
    runtime_msg = str(result.get("claude_msg", "")).splitlines()[0] if result.get("claude_msg") else (
        "Claude CLI" + (" found" if runtime_ok else " NOT FOUND")
    )
    verdict = status_cell("ok", "OK") if runtime_ok else status_cell("fail", "FAIL")
    make_panel(
        f"{verdict}  {runtime_msg}\n"
        "[dim]The agent runtime is the ONLY hard requirement — no API key is required.[/dim]",
        title="Required",
        kind="ok" if runtime_ok else "fail",
        console=con,
    )

    # ── 3. Toolkit tier-matrix ───────────────────────────────────────────────
    con.print(_tier_matrix_table(result, Table))

    # ── 4. Integrations table (+ unlock-links footnote) ──────────────────────
    con.print(_integrations_table(result, Table))
    _print_unlock_links(result, con)

    # ── 5. Result panel ──────────────────────────────────────────────────────
    ok = bool(result.get("all_required_ok"))
    if ok:
        locked = [f["title"] for f in result.get("features", []) if f["status"] == "locked"]
        body = f"{status_cell('ok', 'Result: OK')} — the agent runtime is present (the only hard requirement)."
        if locked:
            body += (
                f"\n[dim]{len(locked)} feature(s) locked: {', '.join(locked)}.[/dim]"
                "\nRun [bold]rv onboard[/bold] for a guided, idempotent setup."
            )
        kind = "ok"
    else:
        culprits = ", ".join(result.get("required_failed", [])) or "unknown"
        body = f"{status_cell('fail', 'Result: FAIL')} — required prerequisite missing: {culprits}."
        kind = "fail"
    make_panel(body, title="Result", kind=kind, console=con)


def _tier_matrix_table(result: dict[str, Any], Table: Any) -> Any:
    """Build the toolkit tier-matrix Table (group coverage per tier)."""
    from collections import defaultdict

    columns = [
        {"name": "Tier", "style": _STYLE["header"], "no_wrap": True},
        {"name": "Group"},
        {"name": "Coverage", "justify": "right", "no_wrap": True},
        {"name": "Status", "no_wrap": True},
    ]
    rows: list[tuple[Any, ...]] = []
    for tier_label, key in (("Tier-1 (core)", "tier1"), ("Tier-2 (GPU/local)", "tier2")):
        groups: dict[str, list[bool]] = defaultdict(list)
        for item in result.get(key, []):
            groups[item["group"]].append(bool(item["ok"]))
        for group, oks in groups.items():
            ok_n, total = sum(oks), len(oks)
            if ok_n == total:
                status = status_cell("ok", "OK")
            elif key == "tier2":
                status = status_cell("skip", "optional")
            else:
                status = status_cell("warn", "missing")
            rows.append((tier_label, group, f"{ok_n}/{total}", status))
            tier_label = ""  # only label the first row of each tier
    return make_status_table(columns, rows, title="Toolkit tiers", Table=Table)


def _integrations_table(result: dict[str, Any], Table: Any) -> Any:
    """Build the Integrations Table — Capability | Unlocks | Class | Status.

    The Status column carries ONLY the state word (+ masked detail when unlocked);
    the request link for a locked feature moves to the unlock-links footnote below
    (:func:`_print_unlock_links`) so a URL never has to wrap-and-truncate inside a
    narrow table cell — it stays whole and copy-pasteable.
    """
    columns = [
        {"name": "Capability", "style": _STYLE["header"], "no_wrap": True},
        {"name": "Unlocks"},
        {"name": "Class", "style": _STYLE["muted"], "no_wrap": True},
        {"name": "Status", "no_wrap": True},
    ]
    rows: list[tuple[Any, ...]] = []
    for feat in result.get("features", []):
        if feat["status"] == "unlocked":
            status = status_cell("ok", "unlocked")
            if feat["detail"]:
                status += f" [dim]{feat['detail']}[/dim]"
        else:
            status = status_cell("locked", "locked")
        rows.append((feat["title"], feat["unlocks"], feat["class"], status))
    title = (
        "Integrations  [dim]— each a feature you unlock, locked until you add it[/dim]"
    )
    return make_status_table(columns, rows, title=title, Table=Table)


def _print_unlock_links(result: dict[str, Any], con: Any) -> None:
    """Print the unlock-links footnote — one full, clickable link per LOCKED feature.

    Rendered as a borderless grid so capability names align and URLs start at a
    common column; URLs render whole (OSC-8 clickable + underlined) rather than
    truncated inside the table.  Skipped entirely when nothing is locked.
    """
    from rich.table import Table

    locked = [f for f in result.get("features", []) if f["status"] == "locked"]
    if not locked:
        return

    grid = Table.grid(padding=(0, 2))
    grid.add_column(width=1)                                # left indent
    grid.add_column(style=_STYLE["locked"], no_wrap=True)  # capability
    grid.add_column(overflow="fold")                       # link(s) — never truncate

    for feat in locked:
        urls = feat.get("urls", [])
        if urls:
            multi = len(urls) > 1
            lines = []
            for u in urls:
                link = f"[{_STYLE['url']}][link={u['url']}]{u['url']}[/link][/]"
                lines.append(f"[dim]{u['label']}[/dim] {link}" if multi else link)
            link_cell = "\n".join(lines)
        elif feat.get("handoff_cmd"):
            link_cell = f"[dim]run:[/dim] [bold]{feat['handoff_cmd']}[/bold]"
        else:
            link_cell = "[dim](no request link)[/dim]"
        grid.add_row("", feat["title"], link_cell)

    con.print(f"  [dim]Unlock the locked capabilities:[/dim]")
    con.print(grid)


# ---------------------------------------------------------------------------
# rv init — header + closing panels
# ---------------------------------------------------------------------------

def render_init_header(target: str, console: Any = None) -> None:
    """Render the ``rv init`` opening panel."""
    make_panel(
        f"Initialising a Research Vault instance in:\n[bold]{target}[/bold]",
        title="rv init",
        kind="init",
        console=console,
    )


def render_onboard_header(header: str, console: Any = None) -> None:
    """Render the ``rv onboard`` header panel (identity border + brass title)."""
    make_panel(header, title="rv onboard", kind="neutral", console=console)


def render_init_closing(target: str, offer_onboard: bool, console: Any = None) -> None:
    """Render the ``rv init`` closing panel with next steps."""
    body = (
        "[bold]Research Vault instance initialised.[/bold]\n\n"
        "Run [bold]rv start[/bold] to launch Claude Code here — you'll be Alfred, the hub.\n"
        "The crew is stood up as subagents in [dim].claude/agents/[/dim]\n\n"
        "Next steps:\n"
        "  1. [bold]rv onboard[/bold]   — guided setup: add the keys that unlock features\n"
        "  2. [bold]rv check[/bold]     — verify prerequisites\n"
        "  3. [bold]rv start[/bold]     — launch Claude Code in this vault (front door)\n"
        "  4. [bold]rv dag run examples/demo-research/research-loop.json[/bold] — the demo loop\n\n"
        "See [dim]QUICKSTART.md[/dim] for the full walkthrough."
    )
    make_panel(body, title="Done", kind="ok", console=console)


# ---------------------------------------------------------------------------
# S2 — quick wins (render from an existing dict / a closing panel)
# ---------------------------------------------------------------------------

def render_compute_explain(job: str, resolved: dict[str, Any], console: Any = None) -> None:
    """Render ``rv compute explain <job>`` — the resolved env/tier/flags kv table.

    Reads the same dict :func:`compute.cmd_explain` returns; mirrors the plain
    :func:`compute._print_explain` selection (skip ``job`` + None values, nest
    dict values one level).
    """
    con = console if console is not None else get_console()
    make_header("rv compute explain", job, console=con)

    from rich.markup import escape

    pairs: list[tuple[str, Any]] = []
    for key, val in resolved.items():
        if key == "job" or val is None:
            continue
        if isinstance(val, dict):
            if val:
                pairs.append((f"{escape(str(key))}:", ""))
                for k, v in val.items():
                    pairs.append((f"  {escape(str(k))}", escape(str(v))))
        else:
            pairs.append((escape(str(key)), escape(str(val))))

    if not pairs:
        make_panel(
            "[dim]No manifest entries found for this job — defaults apply.[/dim]",
            title="Resolved",
            kind="neutral",
            console=con,
        )
        return
    con.print(make_kv_table(pairs))


def render_approval_status(state: dict[str, Any], console: Any = None) -> None:
    """Render ``rv approval status`` — the gate-state panel.

    Reads the dict :func:`dag.approval.approval_status_state` returns.  Enforce-on
    is the safe state (ok border); enforce-off / env-warning tint the panel.
    """
    con = console if console is not None else get_console()
    enforce = bool(state.get("enforce"))
    token_present = bool(state.get("token_present"))

    signal = _STYLE["ok"] if enforce else _STYLE["locked"]
    tok_signal = _STYLE["ok"] if token_present else _STYLE["info"]
    body = (
        f"[{signal}]{state.get('enforce_label', '?')}[/]"
        f"  ·  token=[{tok_signal}]{state.get('token_label', '?')}[/]"
    )
    kind = "ok" if enforce else "neutral"
    if state.get("env_warning"):
        body += (
            f"\n\n[{_STYLE['fail']}]WARNING[/] — RV_APPROVER_TOKEN is set as a plain env var; "
            "it may propagate into crew dispatch env.\n"
            "Use [bold]rv approval setup --keyring[/bold] to store it in the keyring instead."
        )
        kind = "fail"
    make_panel(body, title="Approval gate", kind=kind, console=con)


def render_project_list(projects: list[dict[str, Any]], console: Any = None) -> None:
    """Render ``rv project list`` — the registry table.

    ``projects`` is a list of ``{slug, code, roster, source}`` dicts (built by
    :func:`project.cmd_list` from the config registry).
    """
    con = console if console is not None else get_console()
    make_header("rv project list", f"{len(projects)} project(s) registered", console=con)
    columns = [
        {"name": "Slug", "style": _STYLE["header"], "no_wrap": True},
        {"name": "Code", "no_wrap": True},
        {"name": "Roster"},
        {"name": "Source", "style": _STYLE["muted"]},
    ]
    from rich.markup import escape

    rows: list[tuple[Any, ...]] = []
    for p in projects:
        roster = p.get("roster", [])
        roster_str = "[" + ", ".join(roster) + "]" if roster else "[]"
        rows.append((
            escape(str(p.get("slug", "?"))),
            escape(str(p.get("code", "?"))),
            escape(roster_str),
            escape(str(p.get("source", ""))),
        ))
    con.print(make_status_table(columns, rows))


def render_doctor(result: dict[str, Any], console: Any = None) -> None:
    """Render ``rv doctor`` — the per-backend capability report.

    Reuses :func:`doctor._backend_report_lines` (the SSOT for capability text) so
    the rich surface never drifts from the plain report — each backend's lines go
    into a kind-tinted panel.  Approval-gate + tier-proposal sections stay on the
    plain print path in :func:`doctor.run` (they are supplementary).
    """
    from .doctor import _backend_report_lines, _backend_status_kind

    con = console if console is not None else get_console()
    from_cache = result.get("from_cache", False)
    ts = result.get("ts", "?")
    source = "from cache" if from_cache else "freshly probed"
    subtitle = f"{ts} ({source})"
    if result.get("_legacy"):
        subtitle += " · legacy cache — run rv doctor --refresh"
    make_header("rv doctor", subtitle, console=con)

    backends = result.get("backends", {})
    if not backends:
        make_panel(
            "[dim]No backends found in cache.[/dim]",
            title="Capabilities",
            kind="neutral",
            console=con,
        )
        return

    from rich.markup import escape

    for backend_name, backend_entry in backends.items():
        caps = backend_entry.get("capabilities", backend_entry)  # tolerate flat shape
        lines = _backend_report_lines(backend_name, caps)
        # Strip the leading two-space indent the plain formatter uses (panels indent).
        # Escape the reused plain text — it may contain [brackets] that rich would
        # otherwise parse as markup and silently drop.
        body = escape("\n".join(line[2:] if line.startswith("  ") else line for line in lines))
        make_panel(
            body or "[dim](no detail)[/dim]",
            title=escape(str(backend_name)),
            kind=_backend_status_kind(caps),
            console=con,
        )


# ---------------------------------------------------------------------------
# S3 — rv status (sectioned panels + tables from the structured dict)
# ---------------------------------------------------------------------------

def render_status(sections: dict[str, Any], console: Any = None) -> None:
    """Render ``rv status <project>`` as sectioned panels + tables.

    Reads the dict :func:`status.status_sections` returns (the SSOT the plain
    :func:`status.cmd_status` string is built from).  All data-bearing text is
    escaped so control-item / branch / devlog brackets are never eaten as markup.
    """
    from rich.markup import escape

    con = console if console is not None else get_console()
    project = escape(str(sections.get("project", "?")))
    make_header(
        f"rv status — {project}",
        escape(f"{sections.get('instance_root', '?')} · {sections.get('config_file', '?')}"),
        console=con,
    )

    # One blank line between sections gives the stacked view a scannable rhythm
    # (otherwise the panels/tables fuse into a wall).
    def _section() -> None:
        con.print()

    # ── Coordination state ───────────────────────────────────────────────────
    _section()
    coord = sections.get("coordination", {})
    if coord.get("error"):
        make_panel(f"[{_STYLE['fail']}]read error:[/] {escape(coord['error'])}",
                   title="Coordination State", kind="fail", console=con)
    elif coord.get("missing"):
        make_panel(f"[{_STYLE['locked']}]No control file.[/] Run [bold]rv control {project} init[/bold].",
                   title="Coordination State", kind="neutral", console=con)
    else:
        columns = [
            {"name": "Section", "style": _STYLE["header"], "no_wrap": True},
            {"name": "Items", "justify": "right", "no_wrap": True},
            {"name": "First entries"},
        ]
        rows: list[tuple[Any, ...]] = []
        for sec in coord.get("sections", []):
            first = "\n".join(
                ("[x] " if it["resolved"] else "") + escape(it["text"][:80])
                for it in sec.get("items", [])
            ) or "[dim]—[/dim]"
            count = sec["count"]
            count_cell = f"[{_STYLE['locked']}]{count}[/]" if count else f"[dim]{count}[/dim]"
            rows.append((sec["name"], count_cell, first))
        con.print(make_status_table(columns, rows, title="Coordination State"))
        if not coord.get("banner_ok", True):
            con.print(f"  [{_STYLE['fail']}]⚠ banner missing[/] — run [bold]rv control heal[/bold]")

    # ── Task board ───────────────────────────────────────────────────────────
    _section()
    task = sections.get("task_board", {})
    if task.get("error"):
        make_panel(f"[{_STYLE['fail']}]error:[/] {escape(task['error'])}",
                   title="Task Board", kind="fail", console=con)
    else:
        counts = task.get("counts", {})
        counts_str = "  ".join(f"{escape(str(s))}={n}" for s, n in counts.items()) or "[dim]—[/dim]"
        active = task.get("active", [])
        body = f"[bold]Total:[/bold] {task.get('total', 0)}    {counts_str}"
        if active:
            body += "\n[dim]active:[/dim] " + escape(", ".join(active[:5]))
        make_panel(body, title="Task Board", kind="neutral", console=con)

    # ── DEVLOG tail ──────────────────────────────────────────────────────────
    _section()
    devlog = sections.get("devlog", {})
    if devlog.get("error"):
        make_panel(f"[{_STYLE['fail']}]error:[/] {escape(devlog['error'])}",
                   title="DEVLOG (latest entry)", kind="fail", console=con)
    else:
        tail = devlog.get("tail")
        body = escape(tail) if tail else "[dim](none or missing)[/dim]"
        make_panel(body, title="DEVLOG (latest entry)", kind="neutral", console=con)

    # ── Local git state ──────────────────────────────────────────────────────
    _section()
    git = sections.get("git", {})
    if git.get("error"):
        make_panel(f"[{_STYLE['fail']}]error:[/] {escape(git['error'])}",
                   title="Local Git State", kind="fail", console=con)
    else:
        branches = git.get("branches", [])
        commits = git.get("commits", [])
        parts: list[str] = []
        if branches:
            parts.append("[dim]recent branches:[/dim]\n" + "\n".join("  " + escape(b) for b in branches))
        else:
            parts.append("[dim](no branches)[/dim]")
        if commits:
            parts.append("[dim]recent commits:[/dim]\n" + "\n".join("  " + escape(c) for c in commits))
        make_panel("\n".join(parts), title="Local Git State  (posted local only, no gh)",
                   kind="neutral", console=con)

    # ── DAG runs ─────────────────────────────────────────────────────────────
    _section()
    dag = sections.get("dag", {})
    if dag.get("error"):
        make_panel(f"[{_STYLE['fail']}]error:[/] {escape(dag['error'])}",
                   title="DAG Runs", kind="fail", console=con)
    else:
        runs = dag.get("runs", [])
        if runs:
            columns = [
                {"name": "Run", "style": _STYLE["header"], "no_wrap": True},
                {"name": "State", "no_wrap": True},
            ]
            rows = []
            for r in runs:
                if r["terminal"]:
                    state = status_cell("ok", "terminal")
                else:
                    state = status_cell("pending", "in-flight")
                rows.append((escape(str(r["run_id"])), state))
            con.print(make_status_table(columns, rows, title="DAG Runs"))
        else:
            make_panel("[dim](none)[/dim]", title="DAG Runs", kind="neutral", console=con)

    # ── Pointers ─────────────────────────────────────────────────────────────
    _section()
    pointers = sections.get("pointers", {})
    if pointers.get("lines") is not None:
        body = f"[dim]from {escape(pointers.get('path', '?'))}[/dim]\n" + "\n".join(
            "  " + escape(ln) for ln in pointers["lines"]
        )
    elif pointers.get("error"):
        body = f"[{_STYLE['fail']}]error:[/] {escape(pointers['error'])}"
    else:
        body = f"[dim]{escape(pointers.get('message', 'none'))}[/dim]"
    make_panel(body, title="Pointers", kind="neutral", console=con)

    # ── Needs attention ──────────────────────────────────────────────────────
    _section()
    attention = sections.get("attention", [])
    if attention:
        body = "\n".join(f"{status_glyph('warn')} {escape(a)}" for a in attention)
        make_panel(body, title="Needs Attention", kind="fail", console=con)
    else:
        make_panel(status_cell("ok", "nothing flagged"), title="Needs Attention",
                   kind="ok", console=con)


# ---------------------------------------------------------------------------
# S4 — rv bootstrap (header + per-tier status lines + Result panel)
# ---------------------------------------------------------------------------

def render_bootstrap(result: dict[str, Any], console: Any = None) -> None:
    """Render ``rv bootstrap`` — per-tier install status + a Result panel.

    Reads the dict :func:`bootstrap._run_bootstrap` returns; mirrors the
    :func:`render_check` shape (header → per-tier lines → Result panel).  The
    plain ``report`` string is unchanged and used on the non-rich path.
    """
    from rich.markup import escape

    con = console if console is not None else get_console()
    make_header("rv bootstrap", "Research Vault toolkit install", console=con)

    tier1_ok = bool(result.get("tier1_ok"))
    venv_dir = escape(str(result.get("venv_dir", "?")))

    # ── Per-tier status lines ────────────────────────────────────────────────
    def _mark(ok: bool) -> str:
        return status_cell("ok", "OK") if ok else status_cell("fail", "FAIL")

    lines: list[str] = [f"[dim]venv:[/dim] {venv_dir}", ""]
    lines.append(f"{_mark(tier1_ok)}  [bold]Tier-1[/bold] (core) — hard requirement")

    if not tier1_ok:
        # Tier-1 failed: surface the pip/venv error tail from the report.
        report = str(result.get("report", ""))
        err_tail = "\n".join(
            escape(ln.strip()) for ln in report.splitlines()
            if ln.strip().lower().startswith(("stderr", "venv:", "pip:"))
        )
        if err_tail:
            lines.append("")
            lines.append(err_tail)
        con.print("\n".join(lines))
        make_panel(
            f"{status_cell('fail', 'Result: FAIL')} — Tier-1 install failed. "
            "Fix the error above and re-run [bold]rv bootstrap[/bold].",
            title="Result",
            kind="fail",
            console=con,
        )
        return

    # Tier-2 (best-effort)
    if not result.get("tier2_attempted", True):
        lines.append(f"{status_cell('skip', 'skip')}  [bold]Tier-2[/bold] (GPU/local) — (--no-tier2)")
    elif result.get("tier2_ok"):
        lines.append(f"{_mark(True)}  [bold]Tier-2[/bold] (GPU/local) — installed")
    else:
        reason = escape(str(result.get("tier2_reason", "")).strip() or "GPU-fragile — needs a CUDA env")
        lines.append(
            f"{status_cell('warn', 'WARN')}  [bold]Tier-2[/bold] (GPU/local) — skipped\n"
            f"      [dim]{reason}[/dim]\n"
            f"      [dim]install on your GPU box:[/dim] [bold]pip install research-vault[local][/bold]"
        )

    # Serve stack (optional sub-extra)
    serve_target = result.get("serve_target")
    if serve_target:
        if result.get("serve_ok"):
            lines.append(f"{_mark(True)}  [bold]Serve[/bold] ({escape(str(serve_target))}) — installed")
        else:
            reason = escape(str(result.get("serve_reason", "")).strip() or "GPU/CUDA-specific")
            lines.append(
                f"{status_cell('warn', 'WARN')}  [bold]Serve[/bold] ({escape(str(serve_target))}) — skipped\n"
                f"      [dim]{reason}[/dim]"
            )

    con.print("\n".join(lines))

    # ── Result panel ─────────────────────────────────────────────────────────
    body = f"{status_cell('ok', 'Result: OK')} — Tier-1 installed."
    if result.get("tier2_attempted", True):
        body += " Tier-2: " + ("installed." if result.get("tier2_ok") else "skipped (see above).")
    body += (
        f"\n\n[dim]activate:[/dim] [bold]source {venv_dir}/bin/activate[/bold]"
        "\n[dim]verify:[/dim] [bold]rv check[/bold]"
    )
    make_panel(body, title="Result", kind="ok", console=con)


def render_closing(body: str, title: str = "Done", kind: str = "ok", console: Any = None) -> None:
    """Render a generic closing summary panel (S2 scaffold-summary surfaces).

    A thin, named wrapper over :func:`make_panel` for the closing-panel-only
    verbs (compute init, approval setup, project new/add) so their call sites
    read intentionally and share one closing style.
    """
    make_panel(body, title=title, kind=kind, console=console)
