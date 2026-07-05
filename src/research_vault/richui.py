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
# Style knobs — the designer's follow-up pass edits HERE (structure stays put).
# ---------------------------------------------------------------------------
_STYLE: dict[str, str] = {
    "ok": "green",
    "locked": "yellow",
    "fail": "red",
    "info": "cyan",
    "header": "bold",
    "url": "blue underline",
    "panel_ok_border": "green",
    "panel_fail_border": "red",
    "panel_init_border": "cyan",
}


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

    Layout (Wren's spec):
      1. Header rule.
      2. Required panel — the runtime (the ONLY hard requirement).
      3. Toolkit tier-matrix Table (group · tier · coverage).
      4. Integrations Table — Capability | Unlocks | Class | Status.
      5. Result Panel (OK/FAIL, with locked-feature nudge).
    """
    from rich.panel import Panel
    from rich.table import Table

    con = console if console is not None else get_console()

    con.rule("[bold]rv check — Research Vault preflight[/bold]")

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
            title="Required",
            border_style=_STYLE["panel_ok_border"] if runtime_ok else _STYLE["panel_fail_border"],
        )
    )

    # ── 3. Toolkit tier-matrix ───────────────────────────────────────────────
    con.print(_tier_matrix_table(result, Table))

    # ── 4. Integrations table ────────────────────────────────────────────────
    con.print(_integrations_table(result, Table))

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
    con.print(Panel(body, title="Result", border_style=border))


def _tier_matrix_table(result: dict[str, Any], Table: Any) -> Any:
    """Build the toolkit tier-matrix Table (group coverage per tier)."""
    from collections import defaultdict

    table = Table(title="Toolkit tiers", show_lines=False)
    table.add_column("Tier", style=_STYLE["header"])
    table.add_column("Group")
    table.add_column("Coverage", justify="right")
    table.add_column("Status")

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
    """Build the Integrations Table — Capability | Unlocks | Class | Status."""
    table = Table(title="Integrations (each FEATURE-REQUIRED — locked until you add it)")
    table.add_column("Capability", style=_STYLE["header"])
    table.add_column("Unlocks")
    table.add_column("Class")
    table.add_column("Status")

    for feat in result.get("features", []):
        if feat["status"] == "unlocked":
            status = f"[{_STYLE['ok']}]unlocked[/]"
            if feat["detail"]:
                status += f" [dim]{feat['detail']}[/dim]"
        else:
            status = f"[{_STYLE['locked']}]locked[/]"
            hint = feat["urls"][0]["url"] if feat["urls"] else feat.get("handoff_cmd", "")
            if hint:
                status += f" [dim]→ {hint}[/dim]"
        table.add_row(feat["title"], feat["unlocks"], feat["class"], status)
    return table


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
            title="rv init",
            border_style=_STYLE["panel_init_border"],
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
    con.print(Panel(body, title="Done", border_style=_STYLE["panel_init_border"]))
