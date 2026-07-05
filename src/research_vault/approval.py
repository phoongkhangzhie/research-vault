"""approval.py — `rv approval` verb for SR-APPROVE-GATE.

Subverbs:
  rv approval setup [--keyring]
      Provision a new RV_APPROVER_TOKEN and write the matching fingerprint to
      the config file.  Requires a TTY (the first-time provisioning is human-only;
      no chicken-and-egg bootstrap paradox because there is no pre-existing token
      to satisfy the gate).  Offers --keyring to store the token in the system
      keyring instead of echoing it for shell export.

  rv approval disable
      Disable the human-presence gate for this instance.  Requires the current
      approval ceremony (TTY keystroke or valid token).  Writes enforce=false +
      a HMAC enforce_sig into the config.  A raw toml edit (enforce=false, no sig)
      is INERT when a token is provisioned — only a signed disable is honored.

  rv approval enable
      Re-arm the gate.  Requires the current approval ceremony.  Writes
      enforce=true and clears enforce_sig.

  rv approval status
      Print the current gate state (same as the rv doctor approval section).

TOML write strategy: read the file, find/replace the [approval] section via
regex, write back.  Values are simple types (bool, str) — no new dep needed.
Stdlib only (re, os, secrets).
"""
from __future__ import annotations

import argparse
import os
import re
import secrets as _secrets_mod
import sys
from pathlib import Path
from typing import Any

from .config import load_config, Config


# ---------------------------------------------------------------------------
# TOML section writer (stdlib, no tomli_w dep)
# ---------------------------------------------------------------------------

def _toml_value(v: Any) -> str:
    """Render a Python value as a TOML inline value (bool/str/int only)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        # Escape backslashes and double-quotes.
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(v, int):
        return str(v)
    raise TypeError(f"Unsupported TOML value type {type(v)!r} for {v!r}")


def _build_section_text(section: str, data: dict[str, Any]) -> str:
    """Build the TOML text for a section from a dict."""
    lines = [f"[{section}]"]
    for k, v in data.items():
        lines.append(f"{k} = {_toml_value(v)}")
    return "\n".join(lines) + "\n"


def _update_toml_approval(config_path: Path, data: dict[str, Any]) -> None:
    """Write or replace the [approval] section in *config_path* in-place.

    Creates the section if absent; replaces if present.
    All other sections are untouched.
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    text = config_path.read_text(encoding="utf-8")
    section_text = _build_section_text("approval", data)

    # Match the existing [approval] section up to (but not including) the
    # next section header or end-of-file.
    pattern = re.compile(
        r"^\[approval\][^\n]*\n(?:(?!\[)[^\n]*\n)*",
        re.MULTILINE,
    )
    if pattern.search(text):
        new_text = pattern.sub(section_text, text)
    else:
        # Append — ensure file ends with a newline before the new section.
        if text and not text.endswith("\n"):
            text += "\n"
        new_text = text + "\n" + section_text

    config_path.write_text(new_text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Subverb: setup
# ---------------------------------------------------------------------------

def cmd_setup(cfg: Config, args: argparse.Namespace) -> int:
    """Provision a new RV_APPROVER_TOKEN and write the fingerprint to config.

    This is a TTY-only operation (first-time setup has no pre-existing token
    to satisfy the non-interactive gate — the human is physically present).
    """
    if not sys.stdin.isatty():
        print(
            "rv approval setup: this command must be run at an interactive terminal.\n"
            "  It provisions the token that non-interactive approve calls will use.\n"
            "  Run it once on your workstation, then export the printed token.",
            file=sys.stderr,
        )
        return 1

    use_keyring = bool(getattr(args, "keyring", False))

    # Generate a cryptographically random token (32 url-safe bytes = 43 chars).
    token = _secrets_mod.token_urlsafe(32)

    from .dag.approval import compute_fingerprint
    fingerprint = compute_fingerprint(token)

    # Write fingerprint to config.
    config_path = cfg.config_file
    if config_path is None:
        print(
            "rv approval setup: no config file found.\n"
            "  Run `rv init` first to create a research_vault.toml.",
            file=sys.stderr,
        )
        return 1

    # Read existing [approval] block to preserve enforce/enforce_sig.
    existing_approval: dict[str, Any] = dict(cfg._raw.get("approval", {}))
    existing_approval["token_fingerprint"] = fingerprint

    try:
        _update_toml_approval(config_path, existing_approval)
    except Exception as e:
        print(f"rv approval setup: could not write config: {e}", file=sys.stderr)
        return 1

    if use_keyring:
        try:
            import keyring  # type: ignore[import]
            keyring.set_password("research-vault", "rv-approver-token", token)
            print("Token stored in keyring (service=research-vault, username=rv-approver-token).")
            print(f"Fingerprint written to {config_path}")
            print(
                "  The token is in keyring — no env var needed on this machine.\n"
                "  For CI/scripts, export: RV_APPROVER_TOKEN=<token printed below>"
            )
        except ImportError:
            print(
                "rv approval setup: --keyring requested but keyring is not installed.\n"
                "  pip install keyring  OR  store the token in RV_APPROVER_TOKEN.",
                file=sys.stderr,
            )
            # Fall through to print the token for env-var use.
            use_keyring = False
        except Exception as e:
            print(f"rv approval setup: keyring write failed: {e}", file=sys.stderr)
            use_keyring = False

    if not use_keyring:
        print(f"Fingerprint written to {config_path}")
        print("\nToken (store securely — shown once):")
        print(f"  export RV_APPROVER_TOKEN={token}")
        print(
            "\nAdd this export to your shell profile or CI secret store.\n"
            "Do NOT commit the token to version control."
        )

    return 0


# ---------------------------------------------------------------------------
# Subverb: disable
# ---------------------------------------------------------------------------

def cmd_disable(cfg: Config, args: argparse.Namespace) -> int:
    """Disable the human-presence gate (presence-checked).

    Writes enforce=false + a HMAC-signed enforce_sig into the config.
    A raw toml edit without a valid sig is INERT when a token is provisioned.
    """
    config_path = cfg.config_file
    if config_path is None:
        print("rv approval disable: no config file found.", file=sys.stderr)
        return 1

    from .dag.approval import (
        check_human_presence,
        compute_enforce_sig,
        get_config_id,
        _SECRET_NAME,
    )
    from .adapters.base import EnvSecretStore
    _ss = EnvSecretStore()

    # Presence check (same gate as cmd_approve).
    ok, method, _, reason = check_human_presence(args, cfg, _ss)
    if not ok:
        print(reason, file=sys.stderr)
        return 1

    # Build enforce_sig if a token is provisioned (the signed path).
    fingerprint: str = str(cfg._raw.get("approval", {}).get("token_fingerprint", "")).strip()
    enforce_sig = ""
    if fingerprint:
        try:
            token = _ss.get(_SECRET_NAME)
            config_id = get_config_id(cfg)
            enforce_sig = compute_enforce_sig(token, config_id)
        except KeyError:
            # No token → unsigned disable (trust-me mode).
            pass

    existing_approval: dict[str, Any] = dict(cfg._raw.get("approval", {}))
    existing_approval["enforce"] = False
    existing_approval["enforce_sig"] = enforce_sig

    try:
        _update_toml_approval(config_path, existing_approval)
    except Exception as e:
        print(f"rv approval disable: could not write config: {e}", file=sys.stderr)
        return 1

    sig_label = "signed" if enforce_sig else "unsigned — trust-me mode"
    print(f"Approval gate DISABLED ({sig_label}).")
    if not enforce_sig:
        print(
            "  WARNING: no token provisioned — the disable is not cryptographically "
            "verified and is indistinguishable from an agent toml edit.\n"
            "  Run `rv approval setup` first to provision a token for a signed disable."
        )
    print(f"  Config updated: {config_path}")
    return 0


# ---------------------------------------------------------------------------
# Subverb: enable
# ---------------------------------------------------------------------------

def cmd_enable(cfg: Config, args: argparse.Namespace) -> int:
    """Re-arm the human-presence gate (presence-checked)."""
    config_path = cfg.config_file
    if config_path is None:
        print("rv approval enable: no config file found.", file=sys.stderr)
        return 1

    from .dag.approval import check_human_presence
    from .adapters.base import EnvSecretStore
    _ss = EnvSecretStore()

    ok, _, _, reason = check_human_presence(args, cfg, _ss)
    if not ok:
        print(reason, file=sys.stderr)
        return 1

    existing_approval: dict[str, Any] = dict(cfg._raw.get("approval", {}))
    existing_approval["enforce"] = True
    existing_approval["enforce_sig"] = ""  # clear the sig on re-arm

    try:
        _update_toml_approval(config_path, existing_approval)
    except Exception as e:
        print(f"rv approval enable: could not write config: {e}", file=sys.stderr)
        return 1

    print("Approval gate ENABLED (enforce=true).")
    print(f"  Config updated: {config_path}")
    return 0


# ---------------------------------------------------------------------------
# Subverb: status
# ---------------------------------------------------------------------------

def cmd_approval_status(cfg: Config) -> int:
    """Print the current approval gate status (same as rv doctor approval section)."""
    from .dag.approval import approval_status_lines
    from .adapters.base import EnvSecretStore
    lines = approval_status_lines(cfg, EnvSecretStore())
    for line in lines:
        print(line)
    return 0


# ---------------------------------------------------------------------------
# Parser + dispatcher
# ---------------------------------------------------------------------------

def build_parser(parent: argparse.ArgumentParser | None = None) -> argparse.ArgumentParser:
    """Build and return the `rv approval` argument parser."""
    desc = (
        "Manage the human-presence gate for rv dag approve (SR-APPROVE-GATE). "
        "setup: provision a token + fingerprint. "
        "disable: turn the gate off (signed when a token is provisioned). "
        "enable: re-arm the gate. "
        "status: show current gate state."
    )
    if parent is not None:
        p = parent.add_parser(
            "approval",
            help="Manage the rv dag approve human-presence gate (SR-APPROVE-GATE).",
            description=desc,
        )
    else:
        p = argparse.ArgumentParser(prog="rv approval", description=desc)

    sub = p.add_subparsers(dest="approval_cmd", required=True)

    # setup
    setup_p = sub.add_parser(
        "setup",
        help="Provision a token + write fingerprint to config (TTY required).",
    )
    setup_p.add_argument(
        "--keyring",
        action="store_true",
        default=False,
        help="Store the token in the system keyring instead of printing for shell export.",
    )

    # disable  (also accept --yes for the gate ceremony)
    dis_p = sub.add_parser(
        "disable",
        help="Disable the gate (presence-checked; signed when token is provisioned).",
    )
    dis_p.add_argument(
        "--yes",
        action="store_true",
        default=False,
        help="Skip the confirmation keystroke when a TTY is present.",
    )

    # enable
    ena_p = sub.add_parser(
        "enable",
        help="Re-arm the gate (presence-checked).",
    )
    ena_p.add_argument(
        "--yes",
        action="store_true",
        default=False,
        help="Skip the confirmation keystroke when a TTY is present.",
    )

    # status
    sub.add_parser("status", help="Print the current gate state.")

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch `rv approval` subcommands."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv approval: config error: {e}", file=sys.stderr)
        return 1

    approval_cmd = getattr(args, "approval_cmd", None)

    if approval_cmd == "setup":
        return cmd_setup(cfg, args)
    elif approval_cmd == "disable":
        return cmd_disable(cfg, args)
    elif approval_cmd == "enable":
        return cmd_enable(cfg, args)
    elif approval_cmd == "status":
        return cmd_approval_status(cfg)
    else:
        print(f"rv approval: unknown subcommand {approval_cmd!r}", file=sys.stderr)
        return 1
