"""onboard.py — `rv onboard` — guided, idempotent first-run setup.

The adopter-facing front door for unlocking features.  Walks the ordered steps
runtime → provider key(s) → s2 → asta → wandb → zotero → compute.  For each step it
explains what the step unlocks, shows the request-form URL, and — at an interactive
TTY — offers to read a secret (``getpass``, never echoed) and store it in the system
keyring under the unified registry SSOT (so ``rv check`` and the runtime read it back).

Design guarantees:
  - **Idempotent** — state is re-derived from ``build_features()`` every run (NO
    state file).  A satisfied step is skipped with a masked confirmation.
  - **No-echo secrets** — values are read with ``getpass`` and NEVER printed or
    logged; only a masked prefix of the re-resolved value is shown to verify.
  - **No plaintext .env** — secrets go to the keyring, never to a file.
  - **Non-TTY fallback** — prints the exact remediation steps instead of prompting
    (``--print`` forces this even at a TTY).
  - **Exit 0** — only the runtime could ever block; missing feature keys never fail.
  - **Explicit lock messaging** — each locked step says the capability won't work
    until you add the key.

Stdlib only (``getpass`` + a lazy ``keyring`` via the registry).
"""
from __future__ import annotations

import argparse
import getpass as _getpass
import sys
from typing import Any, Callable

from .keys import (
    FEATURES,
    get_feature,
    resolve_key,
    store_key,
    mask,
)


# ---------------------------------------------------------------------------
# Small IO helpers (injected in tests)
# ---------------------------------------------------------------------------

def _prompt_yes(input_fn: Callable[[str], str], question: str, *, default_no: bool = True) -> bool:
    """Ask a yes/no question. Default is No (safe) unless default_no=False."""
    suffix = " [y/N] " if default_no else " [Y/n] "
    try:
        ans = input_fn(question + suffix).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if not ans:
        return not default_no
    return ans in ("y", "yes")


# ---------------------------------------------------------------------------
# Per-step handlers
# ---------------------------------------------------------------------------

def _step_runtime(result: dict[str, Any]) -> None:
    """Runtime step — the only hard requirement; cannot be fixed via keyring."""
    print("\n[1] Agent runtime (Claude Code) — REQUIRED (the only hard requirement)")
    if result.get("claude_cli"):
        print("    OK — the runtime is installed.")
    else:
        print("    NOT FOUND — Research Vault cannot dispatch agents without it.")
        print("    Install: https://docs.anthropic.com/en/docs/claude-code")
        print("    (This is the ONE thing that must be present; no API key is required.)")


def _remediation_lines(spec: Any) -> list[str]:
    """The exact copy-paste remediation for a keyring key (env var OR keyring CLI)."""
    from .keys import KEYRING_SERVICE
    return [
        f"      export {spec.env_var}=<value>            (session-scoped)",
        f"      keyring set {KEYRING_SERVICE} {spec.keyring_username}   (persists; or re-run `rv onboard` at a TTY)",
    ]


def _add_key_interactive(
    spec: Any,
    *,
    getpass_fn: Callable[[str], str],
) -> bool:
    """Read a secret via getpass and store it in the keyring. Returns True if stored+verified.

    The value is NEVER printed or logged — only a masked prefix of the re-resolved
    value is shown to confirm the write landed.
    """
    try:
        value = getpass_fn(f"      Paste {spec.label} (hidden, blank to skip): ")
    except (EOFError, KeyboardInterrupt):
        print("      (skipped)")
        return False
    value = (value or "").strip()
    if not value:
        print("      (skipped — no value entered)")
        return False
    try:
        store_key(spec, value)
    except ImportError:
        print("      keyring is not installed — cannot store. Use the env-var form above.")
        return False
    except Exception as exc:  # pragma: no cover - keyring backend errors
        print(f"      keyring write failed: {exc}. Use the env-var form above.")
        return False
    # Re-verify from the keyring (never trust the write — read it back).
    present, source, masked = resolve_key(spec)
    if present:
        print(f"      stored + verified via {source} ({masked}).")  # masked prefix only
        return True
    print("      WARNING: stored but could not re-resolve the key — check your keyring backend.")
    return False


def _step_key_feature(
    feat_status: dict[str, Any],
    feature: Any,
    *,
    interactive: bool,
    input_fn: Callable[[str], str],
    getpass_fn: Callable[[str], str],
    step_no: int,
) -> None:
    """A keyring-backed feature step (provider / s2 / wandb / zotero)."""
    print(f"\n[{step_no}] {feature.title} — unlocks {feature.unlocks}")
    if feat_status["status"] == "unlocked":
        print(f"    already configured — {feat_status['detail']} (skipping).")
        return

    print("    This feature won't work until you add the key.")
    if feature.note:
        print(f"    Note: {feature.note}")

    for spec in feature.keys:
        present, _src, _masked = resolve_key(spec)
        if present:
            continue  # idempotent: this provider key is already set
        print(f"    - {spec.label}: request a key at {spec.request_url}")
        if interactive:
            if _prompt_yes(input_fn, f"      Add {spec.label} now?"):
                _add_key_interactive(spec, getpass_fn=getpass_fn)
            else:
                print("      (skipped — you can re-run `rv onboard` any time)")
        else:
            print("      To add it (either):")
            for line in _remediation_lines(spec):
                print(line)


def _step_asta(feat_status: dict[str, Any], feature: Any, *, step_no: int) -> None:
    """asta is a package, not a keyring key — install + access-request instructions."""
    print(f"\n[{step_no}] {feature.title} — unlocks {feature.unlocks}")
    if feat_status["status"] == "unlocked":
        print("    already installed (skipping).")
        return
    print("    This feature won't work until asta is installed with access.")
    print(f"    Request access: {feature.request_url}")
    print(f"    Note: {feature.note}")
    print("    Then install asta per your project's instructions (it is not a pip dep).")


def _step_compute(
    feat_status: dict[str, Any],
    feature: Any,
    *,
    interactive: bool,
    input_fn: Callable[[str], str],
    cfg: Any,
    step_no: int,
) -> None:
    """Compute is a handoff to the guided `rv compute init` flow."""
    print(f"\n[{step_no}] {feature.title} — unlocks {feature.unlocks}")
    if feat_status["status"] == "unlocked":
        print("    already declared — compute_manifest.json present (skipping).")
        return
    print("    This feature won't work until you declare your compute environment.")
    print(f"    Hand off to: {feature.handoff_cmd}")
    if interactive and _prompt_yes(input_fn, "    Run `rv compute init` now?"):
        try:
            from .compute import cmd_init
            from .config import load_config as _load_config
            _cfg = cfg if cfg is not None else _load_config()
            cmd_init(_cfg)
        except Exception as exc:
            print(f"    could not run `rv compute init`: {exc}. Run it manually.")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def cmd_onboard(
    cfg: Any = None,
    *,
    print_only: bool = False,
    assume_tty: bool | None = None,
    input_fn: Callable[[str], str] | None = None,
    getpass_fn: Callable[[str], str] | None = None,
) -> int:
    """Run the guided, idempotent onboarding flow. Always returns 0.

    Args are injectable for tests: ``assume_tty`` overrides TTY detection;
    ``input_fn`` / ``getpass_fn`` override the prompts.
    """
    from .check import run_preflight
    from .richui import should_render_rich, render_onboard_header

    input_fn = input_fn or input
    getpass_fn = getpass_fn or _getpass.getpass

    tty = assume_tty if assume_tty is not None else _stdin_is_tty()
    interactive = tty and not print_only

    result = run_preflight(cfg)
    features_by_id = {f["id"]: f for f in result["features"]}

    # Header (rich panel at a TTY, plain otherwise).
    header = (
        "rv onboard — guided setup\n"
        "The agent runtime is the ONLY hard requirement. Everything below is a "
        "FEATURE you can unlock now or later. Secrets go to your system keyring "
        "(never a plaintext file, never echoed)."
    )
    if interactive and should_render_rich():
        try:
            # The panel title already says "rv onboard"; drop the redundant first
            # body line (kept in the plain path for the no-title case).
            body = header.split("\n", 1)[1] if "\n" in header else header
            render_onboard_header(body)
        except Exception:
            print(header)
    else:
        print(header)
        if not interactive:
            print(
                "\n(non-interactive: printing remediation steps instead of prompting — "
                "re-run at a TTY, or use `--print` to force this.)"
            )

    # Step 1: runtime.
    _step_runtime(result)

    # Steps 2..N: the feature catalog in order (provider → s2 → asta → wandb → zotero → compute).
    step_no = 2
    for feature in FEATURES:
        fs = features_by_id[feature.id]
        if feature.kind == "key":
            _step_key_feature(
                fs, feature,
                interactive=interactive,
                input_fn=input_fn,
                getpass_fn=getpass_fn,
                step_no=step_no,
            )
        elif feature.kind == "package":
            _step_asta(fs, feature, step_no=step_no)
        elif feature.kind == "handoff":
            _step_compute(
                fs, feature,
                interactive=interactive,
                input_fn=input_fn,
                cfg=cfg,
                step_no=step_no,
            )
        step_no += 1

    # Closing: re-derive locked set (idempotent truth) and summarise.
    post = run_preflight(cfg)
    locked = [f["title"] for f in post["features"] if f["status"] == "locked"]
    print("\n" + ("-" * 60))
    if locked:
        print(f"Done. Still locked (optional): {', '.join(locked)}.")
        print("Re-run `rv onboard` any time — satisfied steps are skipped.")
    else:
        print("Done. All features unlocked.")
    print("Verify any time with `rv check`.")
    print("Launch your vault session with `rv start`.")

    # Only the runtime could ever block; onboard itself always exits 0.
    return 0


def _stdin_is_tty() -> bool:
    try:
        return bool(sys.stdin.isatty())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# CLI verb
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``onboard`` verb.

    When to use: ``rv onboard`` for a guided, idempotent first-run setup that adds
    the keys unlocking each feature (provider models, s2, asta, W&B, Zotero, compute).
    """
    desc = (
        "Guided, idempotent first-run setup. Walks runtime → provider key(s) → s2 → "
        "asta → wandb → zotero → compute; explains what each unlocks, shows its "
        "request-form URL, and (at a TTY) reads secrets via getpass and stores them in "
        "your system keyring — never echoed, never written to a plaintext file. "
        "Re-run any time: satisfied steps are skipped (state is re-derived, no state "
        "file). Exit 0 (only the runtime could ever block). Use `--print` to print "
        "remediation steps instead of prompting."
    )
    if parent is not None:
        p = parent.add_parser(
            "onboard",
            help="Guided, idempotent setup — add the keys that unlock features.",
            description=desc,
        )
    else:
        p = argparse.ArgumentParser(prog="rv onboard", description=desc)

    p.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        default=False,
        help="Print remediation steps for every locked feature instead of prompting (no getpass).",
    )
    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch: rv onboard."""
    cfg = None
    try:
        from .config import load_config
        cfg = load_config()
    except Exception:
        cfg = None
    return cmd_onboard(cfg, print_only=getattr(args, "print_only", False))
