# SPDX-License-Identifier: AGPL-3.0-or-later
"""plugins.py — `rv plugins` — show registered static adapters + config-selected ones.

When to use: ``rv plugins list`` to see which backends/notifiers/secret-stores
are registered in this Research Vault installation, and which are currently
selected by the instance config.

D-SR6-1 = THIN: this verb surfaces the *existing static registries*
(_NOTIFIER_REGISTRY / _BACKEND_REGISTRY / _SECRETS_REGISTRY in adapters/base.py)
plus the config-selected adapters (cfg.adapters, load_adapters()). There is NO
entry-points plugin seam in the merged code — this verb is a discovery tool,
not a plugin seam builder. A future SR may add importlib.metadata self-registration
if real adopter demand appears.

Stdlib only.
"""
from __future__ import annotations

import argparse
import sys
from typing import Any

from .config import Config, load_config


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def cmd_plugins_list(cfg: Config) -> dict[str, Any]:
    """Return a dict of registered adapters and the currently selected ones.

    Returns:
      {
        "notifiers": list[str],    — registered notifier adapter names
        "backends":  list[str],    — registered backend adapter names
        "secrets":   list[str],    — registered secret-store adapter names
        "active": {
          "notifier": str,
          "backend":  str,
          "secrets":  str,
        },
      }
    """
    from .adapters.base import (
        _NOTIFIER_REGISTRY,
        _BACKEND_REGISTRY,
        _SECRETS_REGISTRY,
    )
    adapter_cfg = cfg.adapters or {}
    return {
        "notifiers": sorted(_NOTIFIER_REGISTRY.keys()),
        "backends":  sorted(_BACKEND_REGISTRY.keys()),
        "secrets":   sorted(_SECRETS_REGISTRY.keys()),
        "active": {
            "notifier": adapter_cfg.get("notifier", "file"),
            "backend":  adapter_cfg.get("backend", "local"),
            "secrets":  adapter_cfg.get("secrets", "env"),
        },
    }


def _print_plugins(result: dict[str, Any]) -> None:
    """Print the plugins list in a human-readable format."""
    lines = [
        "=== rv plugins list — registered adapters ===",
        "",
        "Notifiers (adapters.notifier):",
    ]
    active_n = result["active"]["notifier"]
    for name in result["notifiers"]:
        marker = " [active]" if name == active_n else ""
        lines.append(f"  {name}{marker}")

    lines.append("")
    lines.append("Compute backends (adapters.backend):")
    active_b = result["active"]["backend"]
    for name in result["backends"]:
        marker = " [active]" if name == active_b else ""
        lines.append(f"  {name}{marker}")

    lines.append("")
    lines.append("Secret stores (adapters.secrets):")
    active_s = result["active"]["secrets"]
    for name in result["secrets"]:
        marker = " [active]" if name == active_s else ""
        lines.append(f"  {name}{marker}")

    lines.append("")
    lines.append(
        "Note: D-SR6-1=THIN — shows static registries only. "
        "No entry-points self-registration seam exists in this version."
    )
    lines.append("")
    print("\n".join(lines))


# ---------------------------------------------------------------------------
# CLI verb
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``plugins`` verb."""
    desc = (
        "Show registered adapter plugins (notifiers, compute backends, secret stores) "
        "and the currently selected (active) adapters from the instance config. "
        "D-SR6-1=THIN: surfaces static registries only — no entry-points discovery."
    )
    if parent is not None:
        p = parent.add_parser(
            "plugins",
            help="List registered adapter plugins + config-selected ones.",
            description=desc,
        )
    else:
        p = argparse.ArgumentParser(prog="rv plugins", description=desc)

    sub = p.add_subparsers(dest="plugins_cmd", required=True)
    sub.add_parser(
        "list",
        help="List all registered adapters and the currently active selection.",
    )

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch: rv plugins list."""
    cfg: Config = getattr(args, "_cfg", None) or load_config()

    plugins_cmd = getattr(args, "plugins_cmd", None)
    if plugins_cmd == "list":
        result = cmd_plugins_list(cfg)
        _print_plugins(result)
        return 0

    print(f"rv plugins: unknown subcommand {plugins_cmd!r}", file=sys.stderr)
    return 1
