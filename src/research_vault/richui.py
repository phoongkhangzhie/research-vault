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
    "header": "bold #C6D0E0",     # table column heads — lifted slate, bold
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
    """Construct a rich Console. Kept as a seam for tests / future config."""
    from rich.console import Console
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
    from rich.panel import Panel
    from rich.table import Table

    con = console if console is not None else get_console()

    con.rule(
        f"[{_STYLE['title']}]rv check[/] [dim]— Research Vault preflight[/dim]",
        style=_STYLE["border"],
    )

    # ── 2. Required (runtime only) ───────────────────────────────────────────
    runtime_ok = bool(result.get("claude_cli"))
    runtime_msg = str(result.get("claude_msg", "")).splitlines()[0] if result.get("claude_msg") else (
        "Claude CLI" + (" found" if runtime_ok else " NOT FOUND")
    )
    mark = f"[{_STYLE['ok']}]OK[/]" if runtime_ok else f"[{_STYLE['fail']}]FAIL[/]"
    con.print(
        Panel(
            f"{mark}  {runtime_msg}\n"
            "[dim]The agent runtime is the ONLY hard requirement — no API key is required.[/dim]",
            title=f"[{_STYLE['title']}]Required[/]",
            border_style=_STYLE["panel_ok_border"] if runtime_ok else _STYLE["panel_fail_border"],
            padding=(0, 1),
        )
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
        body = f"[{_STYLE['ok']}]Result: OK[/] — the agent runtime is present (the only hard requirement)."
        if locked:
            body += (
                f"\n[dim]{len(locked)} feature(s) locked: {', '.join(locked)}.[/dim]"
                "\nRun [bold]rv onboard[/bold] for a guided, idempotent setup."
            )
        border = _STYLE["panel_ok_border"]
    else:
        culprits = ", ".join(result.get("required_failed", [])) or "unknown"
        body = f"[{_STYLE['fail']}]Result: FAIL[/] — required prerequisite missing: {culprits}."
        border = _STYLE["panel_fail_border"]
    con.print(Panel(body, title=f"[{_STYLE['title']}]Result[/]", border_style=border, padding=(0, 1)))


def _tier_matrix_table(result: dict[str, Any], Table: Any) -> Any:
    """Build the toolkit tier-matrix Table (group coverage per tier)."""
    from collections import defaultdict

    table = Table(
        title=f"[{_STYLE['title']}]Toolkit tiers[/]",
        box=_table_box(),
        show_lines=False,
        header_style=_STYLE["header"],
        border_style=_STYLE["border"],
        title_justify="left",
        pad_edge=False,
        padding=(0, 1),
    )
    table.add_column("Tier", style=_STYLE["header"], no_wrap=True)
    table.add_column("Group")
    table.add_column("Coverage", justify="right", no_wrap=True)
    table.add_column("Status", no_wrap=True)

    for tier_label, key in (("Tier-1 (core)", "tier1"), ("Tier-2 (GPU/local)", "tier2")):
        groups: dict[str, list[bool]] = defaultdict(list)
        for item in result.get(key, []):
            groups[item["group"]].append(bool(item["ok"]))
        for group, oks in groups.items():
            ok_n, total = sum(oks), len(oks)
            if ok_n == total:
                status = f"[{_STYLE['ok']}]OK[/]"
            elif key == "tier2":
                status = f"[{_STYLE['info']}]optional[/]"
            else:
                status = f"[{_STYLE['locked']}]missing[/]"
            table.add_row(tier_label, group, f"{ok_n}/{total}", status)
            tier_label = ""  # only label the first row of each tier
    return table


def _integrations_table(result: dict[str, Any], Table: Any) -> Any:
    """Build the Integrations Table — Capability | Unlocks | Class | Status.

    The Status column carries ONLY the state word (+ masked detail when unlocked);
    the request link for a locked feature moves to the unlock-links footnote below
    (:func:`_print_unlock_links`) so a URL never has to wrap-and-truncate inside a
    narrow table cell — it stays whole and copy-pasteable.
    """
    table = Table(
        title=f"[{_STYLE['title']}]Integrations[/]  [dim]— each a feature you unlock, locked until you add it[/dim]",
        box=_table_box(),
        header_style=_STYLE["header"],
        border_style=_STYLE["border"],
        title_justify="left",
        pad_edge=False,
        padding=(0, 1),
    )
    table.add_column("Capability", style=_STYLE["header"], no_wrap=True)
    table.add_column("Unlocks")
    table.add_column("Class", style=_STYLE["muted"], no_wrap=True)
    table.add_column("Status", no_wrap=True)

    for feat in result.get("features", []):
        if feat["status"] == "unlocked":
            status = f"[{_STYLE['ok']}]● unlocked[/]"
            if feat["detail"]:
                status += f" [dim]{feat['detail']}[/dim]"
        else:
            status = f"[{_STYLE['locked']}]○ locked[/]"
        table.add_row(feat["title"], feat["unlocks"], feat["class"], status)
    return table


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
    from rich.panel import Panel
    con = console if console is not None else get_console()
    con.print(
        Panel(
            f"Initialising a Research Vault instance in:\n[bold]{target}[/bold]",
            title=f"[{_STYLE['title']}]rv init[/]",
            border_style=_STYLE["panel_init_border"],
            padding=(0, 1),
        )
    )


def render_onboard_header(header: str, console: Any = None) -> None:
    """Render the ``rv onboard`` header panel (identity border + brass title)."""
    from rich.panel import Panel
    con = console if console is not None else get_console()
    con.print(
        Panel(
            header,
            title=f"[{_STYLE['title']}]rv onboard[/]",
            border_style=_STYLE["border"],
            padding=(0, 1),
        )
    )


def render_init_closing(target: str, offer_onboard: bool, console: Any = None) -> None:
    """Render the ``rv init`` closing panel with next steps."""
    from rich.panel import Panel
    con = console if console is not None else get_console()
    body = (
        "[bold]Research Vault instance initialised.[/bold]\n\n"
        "Open [bold]claude[/bold] in this directory to start — you'll be Alfred, the hub.\n"
        "The crew is stood up as subagents in [dim].claude/agents/[/dim]\n\n"
        "Next steps:\n"
        "  1. [bold]rv onboard[/bold]   — guided setup: add the keys that unlock features\n"
        "  2. [bold]rv check[/bold]     — verify prerequisites\n"
        "  3. [bold]rv dag run examples/demo-research/research-loop.json[/bold] — the demo loop\n\n"
        "See [dim]QUICKSTART.md[/dim] for the full walkthrough."
    )
    con.print(Panel(body, title=f"[{_STYLE['title']}]Done[/]", border_style=_STYLE["panel_ok_border"], padding=(0, 1)))
