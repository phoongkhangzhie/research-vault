"""dag/approval.py — Human-presence gate for rv dag approve / rv dag reject.

The load-bearing principle: security = stdin.isatty(), full stop.  A dispatched
subagent has no controlling TTY → it hits the non-interactive branch and is refused
regardless of any --yes flag or prompt weight.  The human prompt is a *courtesy*,
not a control — make it one keystroke.  All friction cuts live on the human-TTY side;
the crew path is untouched.

Two authorized paths:
  tty   — the operator is at their terminal (stdin.isatty()).  Accept a single
          y/enter/approve keystroke (or --yes to skip when TTY is present; --yes
          with no TTY is *ignored* — still fails closed).
  token — a pre-provisioned RV_APPROVER_TOKEN matched against the stored fingerprint
          in config (non-interactive scripts / CI the operator has explicitly blessed).

Fail-closed: any other path → refuse with a friendly nudge; state unchanged.

Slice 3 — enforce=false requires a valid HMAC signature keyed on the approver token:
  - A raw toml edit (enforce=false, no sig) is INERT when a token is provisioned.
  - Without a provisioned token the sig cannot be verified → "trust-me mode" (honored
    but rv doctor warns).  The signed path (rv approval setup → rv approval disable)
    is the robust one.
"""
from __future__ import annotations

import datetime
import hashlib
import hmac as _hmac
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import argparse
    from ..config import Config
    from ..adapters.base import SecretStore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Secret name resolved by EnvSecretStore:
#   env var : RV_APPROVER_TOKEN  (via _env_name: "rv-approver-token" → RV_APPROVER_TOKEN)
#   keyring : service="research-vault", username="rv-approver-token"
_SECRET_NAME = "rv-approver-token"

# Fixed prefix for the token fingerprint (prevents rainbow-table lookups on
# the stored hash; no full MAC needed — the prefix is the "salt").
_FINGERPRINT_PREFIX = b"rv-approver-token-v1:"

# HMAC message template for the enforce_sig (config_id substituted at runtime).
_ENFORCE_MSG_TMPL = "enforce=false|{config_id}"


# ---------------------------------------------------------------------------
# Fingerprint helpers
# ---------------------------------------------------------------------------

def compute_fingerprint(token: str) -> str:
    """Return the stored fingerprint for *token* (salted sha256 hex digest)."""
    return hashlib.sha256(_FINGERPRINT_PREFIX + token.encode("utf-8")).hexdigest()


def verify_fingerprint(token: str, stored_fingerprint: str) -> bool:
    """Timing-safe comparison of token fingerprint vs stored value."""
    expected = compute_fingerprint(token)
    return _hmac.compare_digest(
        expected.encode("utf-8"), stored_fingerprint.encode("utf-8")
    )


# ---------------------------------------------------------------------------
# Enforce-sig helpers (Slice 3)
# ---------------------------------------------------------------------------

def compute_enforce_sig(token: str, config_id: str) -> str:
    """Return the HMAC-SHA256 hex sig for 'enforce=false|{config_id}', keyed by token."""
    msg = _ENFORCE_MSG_TMPL.format(config_id=config_id).encode("utf-8")
    return _hmac.new(token.encode("utf-8"), msg=msg, digestmod=hashlib.sha256).hexdigest()


def verify_enforce_sig(token: str, config_id: str, sig: str) -> bool:
    """Timing-safe verification of an enforce_sig value."""
    if not sig:
        return False
    expected = compute_enforce_sig(token, config_id)
    return _hmac.compare_digest(
        expected.encode("utf-8"), sig.encode("utf-8")
    )


def get_config_id(cfg: "Config") -> str:
    """Return a stable identifier for this config instance (used in enforce_sig)."""
    config_file = getattr(cfg, "config_file", None)
    if config_file:
        return str(Path(config_file).resolve())
    return str(cfg.instance_root.resolve())


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

_FAIL_CLOSED_MSG = (
    "rv dag approve: this human-go gate needs you.\n"
    "  → At your terminal: rv dag approve {run_id} {node_id}\n"
    "  → For scripts/CI: rv approval setup (one-time approver token)\n"
    "Crew agents can't self-approve — by design. [crew-cannot-self-approve]"
)


def check_human_presence(
    args: "argparse.Namespace",
    cfg: "Config",
    secrets: "SecretStore",
) -> tuple[bool, str, str, str]:
    """Verify that a human authorized this approval/rejection.

    Returns (ok, method, approver, reason).

      ok       — True if the action is authorized.
      method   — "tty" | "token" | "" (on failure).
      approver — short label: "operator" for tty; "token:<last8>" for token path.
      reason   — human-readable explanation (for error messages on failure).

    Fail-closed: absent or mismatched credentials → (False, "", "", reason).
    """
    run_id: str = str(getattr(args, "run_id", "?"))
    node_id: str = str(getattr(args, "node_id", "?"))
    yes_flag: bool = bool(getattr(args, "yes", False))
    is_reject: bool = bool(getattr(args, "reject", False))

    # Read the approval block from raw config.
    approval_cfg: dict[str, Any] = cfg._raw.get("approval", {})
    enforce: bool = bool(approval_cfg.get("enforce", True))
    fingerprint: str = str(approval_cfg.get("token_fingerprint", "")).strip()
    enforce_sig: str = str(approval_cfg.get("enforce_sig", "")).strip()

    # Slice 3: if enforce=false, verify the enforce_sig before honoring it.
    if not enforce:
        if fingerprint:
            # A token is provisioned → MUST verify the sig.
            # A raw toml edit without a valid sig is INERT.
            try:
                token = secrets.get(_SECRET_NAME)
                config_id = get_config_id(cfg)
                if verify_enforce_sig(token, config_id, enforce_sig):
                    # Valid signed disable → gate is off (adopter's informed choice).
                    return (
                        True,
                        "token-gate-disabled",
                        "operator",
                        "approval gate disabled (signed)",
                    )
                else:
                    # Invalid or absent sig with a provisioned token → inert edit.
                    enforce = True
            except KeyError:
                # Can't resolve token to verify → treat as enforce=True.
                enforce = True
        else:
            # No token provisioned → "trust-me mode": honor the disable but warn.
            # rv doctor will flag this as unsigned.
            return (
                True,
                "tty-gate-disabled",
                "operator",
                "approval gate disabled (unsigned — trust-me mode)",
            )

    # --- enforce is True (default or because sig verification failed) ---

    if sys.stdin.isatty():
        # Human is at their terminal.
        if yes_flag:
            return (True, "tty", "operator", "--yes at terminal")
        # One-keystroke prompt.
        action = "reject" if is_reject else "approve"
        print(f"\nrv dag {action} — human-go gate requires your sign-off.")
        print(f"  run: {run_id!r}  node: {node_id!r}")
        try:
            answer = (
                input("  Confirm? [y/enter/approve to proceed, anything else aborts] ")
                .strip()
                .lower()
            )
        except (EOFError, KeyboardInterrupt):
            answer = "n"
        if answer in ("y", "yes", "approve", ""):
            return (True, "tty", "operator", f"{action} confirmed at terminal")
        # Abort — state unchanged, NOT a reject.
        return (
            False,
            "",
            "",
            f"approval aborted at terminal (state unchanged)",
        )

    # --- No TTY: try the token path (--yes is ignored when no TTY) ---
    # Fingerprint must be provisioned before we bother looking up the token.
    # Without a fingerprint there is nothing to verify against → fail closed.
    if not fingerprint:
        msg = _FAIL_CLOSED_MSG.format(run_id=run_id, node_id=node_id)
        return (False, "", "", msg)

    try:
        token = secrets.get(_SECRET_NAME)
    except KeyError:
        msg = _FAIL_CLOSED_MSG.format(run_id=run_id, node_id=node_id)
        return (False, "", "", msg)

    if not verify_fingerprint(token, fingerprint):
        # Token is provisioned but doesn't match the stored fingerprint.
        msg = (
            "rv dag approve: token fingerprint mismatch — not authorized.\n"
            "  Re-run `rv approval setup` to re-provision the token.\n"
            f"  Or at your terminal: rv dag approve {run_id} {node_id}"
        )
        return (False, "", "", msg)

    # Token matches.
    approver = f"token:{fingerprint[-8:]}"
    return (True, "token", approver, "approved via token")


# ---------------------------------------------------------------------------
# Doctor / status surface (Slice 4)
# ---------------------------------------------------------------------------

def approval_status_lines(cfg: "Config", secrets: "SecretStore") -> list[str]:
    """Return human-readable lines describing the current approval gate status.

    Called by rv doctor to surface the gate state and warn on anti-leak risk.
    """
    approval_cfg: dict[str, Any] = cfg._raw.get("approval", {})
    enforce: bool = bool(approval_cfg.get("enforce", True))
    fingerprint: str = str(approval_cfg.get("token_fingerprint", "")).strip()
    enforce_sig: str = str(approval_cfg.get("enforce_sig", "")).strip()

    lines: list[str] = []

    # Token presence check (never print the token value itself).
    token_present = False
    try:
        secrets.get(_SECRET_NAME)
        token_present = True
    except KeyError:
        pass
    token_label = "provisioned" if token_present else "absent"

    # Enforce state label.
    if enforce:
        enforce_label = "enforce=on"
    else:
        if fingerprint and enforce_sig:
            enforce_label = "enforce=off (signed)"
        else:
            enforce_label = "enforce=off (unsigned — trust-me mode)"

    lines.append(f"  approval: {enforce_label} · token={token_label}")

    # Anti-leak warning: is the token set as a plain env var?
    raw_env = os.environ.get("RV_APPROVER_TOKEN", "").strip()
    if raw_env:
        lines.append(
            "  WARNING [approval]: RV_APPROVER_TOKEN is set as a plain env var — "
            "it may propagate into crew dispatch env. "
            "Use `rv approval setup --keyring` to store in keyring instead."
        )

    return lines
