"""check.py — `rv check` — preflight prerequisite check.

When to use: ``rv check`` before starting any research loop. Verifies that
all prerequisites are available and reports missing items with clear install
instructions. Fail-fast: reports ALL failures, not just the first.

Checks:
  1. Claude CLI — ``claude --version`` must succeed (the agent runtime)
  2. ANTHROPIC_API_KEY — must be set in env or resolvable via keyring
  3. asta (optional) — the literature-search integration package
  4. Zotero / ZOTERO_KEY (optional) — for citation management
  5. figures extra (optional) — matplotlib/seaborn/pandas for rv figure preview/render

Exit codes:
  0 — all required prerequisites present (optional checks may warn)
  1 — one or more REQUIRED prerequisites missing

Stdlib only (plus optional keyring import for secret resolution).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from typing import Any


# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------

def _check_claude_cli() -> tuple[bool, str]:
    """Return (ok, message) for the Claude CLI check."""
    claude_path = shutil.which("claude")
    if claude_path:
        return True, f"Claude CLI: found at {claude_path}"
    return False, (
        "Claude CLI: NOT FOUND\n"
        "  Install: https://docs.anthropic.com/en/docs/claude-code\n"
        "  The Claude CLI is the agent runtime — Research Vault cannot dispatch\n"
        "  agents without it."
    )


def _check_api_key() -> tuple[bool, str]:
    """Return (ok, message) for the ANTHROPIC_API_KEY check."""
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        # Don't print the key; just confirm it's set
        prefix = key[:8] + "…" if len(key) > 8 else "***"
        return True, f"ANTHROPIC_API_KEY: set ({prefix})"

    # Try keyring (optional dependency)
    try:
        import keyring  # type: ignore[import]
        val = keyring.get_password("research_vault", "ANTHROPIC_API_KEY")
        if val:
            return True, "ANTHROPIC_API_KEY: found in system keyring"
    except ImportError:
        pass
    except Exception:
        pass

    return False, (
        "ANTHROPIC_API_KEY: NOT SET\n"
        "  Set via: export ANTHROPIC_API_KEY=sk-ant-…\n"
        "  Or store in keyring: keyring set research_vault ANTHROPIC_API_KEY\n"
        "  Get a key at: https://console.anthropic.com/"
    )


def _check_asta() -> tuple[bool, str, bool]:
    """Return (ok, message, required) for the asta check.

    asta is OPTIONAL — literature search degrades gracefully without it.
    """
    try:
        import asta  # type: ignore[import]
        return True, "asta: found", False
    except ImportError:
        return False, (
            "asta: NOT FOUND (optional)\n"
            "  Install: pip install asta  or  uv add asta\n"
            "  Required for `rv research` literature-search integration."
        ), False


def _check_wandb() -> tuple[bool, str, bool]:
    """Return (ok, message, required) for the W&B SDK + key check.

    W&B is a documented prerequisite for `rv wandb pull` (SR-WB) — not an optional
    enhancement. Check two things: SDK importable AND WANDB_API_KEY is set.
    If either fails, the W&B feature set is unavailable.
    Not blocking `all_required_ok` (W&B features degrade gracefully for non-W&B workflows).
    """
    # 1. SDK importable?
    try:
        import wandb  # type: ignore[import]
        wandb_ver = getattr(wandb, "__version__", "?")
        sdk_ok = True
        sdk_msg = f"wandb SDK: found (v{wandb_ver})"
    except ImportError:
        return False, (
            "wandb SDK: NOT INSTALLED (required for `rv wandb`)\n"
            "  Install: pip install wandb  or  uv add wandb\n"
            "  Get a free account at: https://wandb.ai"
        ), False

    # 2. WANDB_API_KEY resolvable?
    key = os.environ.get("WANDB_API_KEY", "").strip()
    if key:
        prefix = key[:8] + "…" if len(key) > 8 else "***"
        return True, f"wandb: SDK ok (v{wandb_ver}), WANDB_API_KEY set ({prefix})", False

    if not os.environ.get("VAULT_SKIP_KEYRING"):
        try:
            import keyring  # type: ignore[import]
            val = keyring.get_password("research-vault", "wandb-api-key")
            if val:
                return True, f"wandb: SDK ok (v{wandb_ver}), WANDB_API_KEY found in keyring", False
        except ImportError:
            pass
        except Exception:
            pass

    return False, (
        f"wandb: SDK ok (v{wandb_ver}) but WANDB_API_KEY not set\n"
        "  Set via: export WANDB_API_KEY=<your-wandb-api-key>\n"
        "  Get a key at: https://wandb.ai/settings"
    ), False


def _check_zotero() -> tuple[bool, str, bool]:
    """Return (ok, message, required) for the Zotero key check.

    Zotero is OPTIONAL — citation management is not required for the core loops.
    """
    key = os.environ.get("ZOTERO_KEY", "").strip()
    if key:
        return True, "ZOTERO_KEY: set", False

    try:
        import keyring  # type: ignore[import]
        val = keyring.get_password("research_vault", "ZOTERO_KEY")
        if val:
            return True, "ZOTERO_KEY: found in keyring", False
    except ImportError:
        pass
    except Exception:
        pass

    return False, (
        "ZOTERO_KEY: NOT SET (optional)\n"
        "  Set via: export ZOTERO_KEY=<your-zotero-api-key>\n"
        "  Get a key at: https://www.zotero.org/settings/keys\n"
        "  Required for `rv cite` and Zotero-backed literature management."
    ), False


def _check_figures() -> tuple[bool, str, bool]:
    """Return (ok, message, required) for the [figures] optional extra check.

    The figures extra (matplotlib/seaborn/pandas) is OPTIONAL — core loops
    run without it; `rv figure preview/render` requires it.
    SR-FIG: this is the first dependency-bearing capability in Research Vault.
    """
    try:
        import matplotlib  # type: ignore[import]
        import pandas  # type: ignore[import]
        return True, "figures extra (matplotlib+pandas): found", False
    except ImportError:
        return False, (
            "figures extra: NOT FOUND (optional)\n"
            "  Install: pip install research-vault[figures]\n"
            "  Required for `rv figure preview` and `rv figure render`.\n"
            "  Includes: matplotlib>=3.8, seaborn>=0.13, pandas>=2.2"
        ), False


# ---------------------------------------------------------------------------
# Main preflight runner
# ---------------------------------------------------------------------------

def run_preflight() -> dict[str, Any]:
    """Run all preflight checks and return a result dict.

    Returns:
      {
        "claude_cli": bool,
        "api_key": bool,
        "asta": bool,
        "zotero": bool,
        "figures": bool,
        "all_required_ok": bool,
        "report": str,        human-readable multi-line report
      }

    This is the programmatic entrypoint (used by tests and `rv check`).
    """
    lines: list[str] = ["=== rv check — Research Vault preflight ===", ""]

    # Required checks
    claude_ok, claude_msg = _check_claude_cli()
    apikey_ok, apikey_msg = _check_api_key()

    # Optional checks
    asta_ok, asta_msg, _ = _check_asta()
    zotero_ok, zotero_msg, _ = _check_zotero()
    wandb_ok, wandb_msg, _ = _check_wandb()
    figures_ok, figures_msg, _ = _check_figures()

    all_required = claude_ok and apikey_ok

    # Required section
    lines.append("Required:")
    status = "OK" if claude_ok else "FAIL"
    lines.append(f"  [{status}] {claude_msg}")
    status = "OK" if apikey_ok else "FAIL"
    lines.append(f"  [{status}] {apikey_msg}")

    # Optional section
    lines.append("")
    lines.append("Optional:")
    status = "OK" if asta_ok else "WARN"
    lines.append(f"  [{status}] {asta_msg}")
    status = "OK" if zotero_ok else "WARN"
    lines.append(f"  [{status}] {zotero_msg}")
    status = "OK" if wandb_ok else "WARN"
    lines.append(f"  [{status}] {wandb_msg}")
    status = "OK" if figures_ok else "WARN"
    lines.append(f"  [{status}] {figures_msg}")

    # Summary
    lines.append("")
    if all_required:
        lines.append("Result: OK — all required prerequisites present.")
        optional_missing = not asta_ok or not zotero_ok or not wandb_ok or not figures_ok
        if optional_missing:
            lines.append("  (optional tools not found — some features limited)")
    else:
        lines.append("Result: FAIL — required prerequisites missing (see FAIL items above).")

    report = "\n".join(lines)

    return {
        "claude_cli": claude_ok,
        "api_key": apikey_ok,
        "asta": asta_ok,
        "zotero": zotero_ok,
        "wandb_key": wandb_ok,
        "figures": figures_ok,
        "all_required_ok": all_required,
        "report": report,
    }


# ---------------------------------------------------------------------------
# CLI verb
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``check`` verb.

    When to use: ``rv check`` before running any research loop. Verifies that
    the Claude CLI, API key, and optional tools (asta, Zotero, figures extra)
    are available. Fail-fast: reports all failures with clear install instructions.
    """
    desc = (
        "Preflight check — verify Research Vault prerequisites. "
        "Checks: Claude CLI (required), ANTHROPIC_API_KEY (required), "
        "asta (optional, for literature search), Zotero/ZOTERO_KEY (optional, for citations), "
        "figures extra (optional, for rv figure preview/render). "
        "Exit 0 if all required prerequisites are present; exit 1 if any are missing."
    )
    if parent is not None:
        p = parent.add_parser(
            "check",
            help="Preflight check — verify prerequisites before running research loops.",
            description=desc,
        )
    else:
        p = argparse.ArgumentParser(prog="rv check", description=desc)

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch: rv check."""
    result = run_preflight()
    print(result["report"])
    return 0 if result["all_required_ok"] else 1
