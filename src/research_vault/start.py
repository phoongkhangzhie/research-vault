"""start.py — `rv start` — front door: launch Claude Code in the vault.

When to use: ``rv start [<vault_path>]`` to verify the vault and launch Claude
Code in it so the session boots as Alfred, the hub. This is the recommended way
to begin any Research Vault session — it verifies the instance is valid and the
runtime is present BEFORE handing off to Claude Code.

Preflight:
  - ``vault_path`` must contain ``research_vault.toml`` AND ``CLAUDE.md``.
  - ``claude`` must be on PATH (the agent runtime — the sole hard requirement).

On success, ``rv start`` does NOT return: it replaces the current process with
``claude`` running in the vault directory (``os.execvp``). Any extra args after
``rv start`` are forwarded verbatim to ``claude``.

Stdlib only. No external deps.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Core logic (importable + testable — no I/O side-effects at import time)
# ---------------------------------------------------------------------------

def _is_vault(path: Path) -> bool:
    """Return True iff ``path`` looks like an initialised Research Vault instance."""
    return (path / "research_vault.toml").exists() and (path / "CLAUDE.md").exists()


def cmd_start(
    vault_path: str | None,
    passthrough_args: list[str] | None = None,
    *,
    # Seams for testing — production code never passes these.
    _which_fn=shutil.which,
    _chdir_fn=os.chdir,
    _execvp_fn=os.execvp,
    _out=None,
    _err=None,
) -> int:
    """Launch Claude Code in the vault directory.

    Returns only on error (non-zero exit code). On success, the current process
    is replaced by ``claude`` and this function never returns.
    """
    out = _out if _out is not None else sys.stdout
    err = _err if _err is not None else sys.stderr

    # 1. Resolve vault path.
    if vault_path is not None:
        vault = Path(vault_path).expanduser().resolve()
    else:
        vault = Path.cwd()

    # 2. Preflight: vault check.
    if not _is_vault(vault):
        print(
            f"{vault} is not a Research Vault instance "
            "(no research_vault.toml + CLAUDE.md). "
            "Run 'rv init <name>' first, or cd into your vault.",
            file=err,
        )
        return 1

    # 3. Preflight: runtime check.
    if _which_fn("claude") is None:
        print(
            "Claude Code CLI ('claude') not found on PATH. "
            "rv start launches Claude Code in your vault so the session boots as Alfred. "
            "Install Claude Code, then re-run.",
            file=err,
        )
        return 1

    # 4. Launch: chdir → execvp (replaces this process).
    _chdir_fn(str(vault))
    args = passthrough_args or []
    _execvp_fn("claude", ["claude"] + args)

    # execvp never returns on success — reaching here means exec failed.
    print("rv start: os.execvp('claude', ...) failed unexpectedly.", file=err)
    return 1


# ---------------------------------------------------------------------------
# CLI verb
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``start`` verb.

    When to use: ``rv start [<vault_path>]`` — the front door. Launch Claude
    Code in your vault so the session becomes Alfred, the hub with the crew as
    subagents. Verifies vault validity and the runtime before exec-replacing
    with ``claude``. Extra args after the path are forwarded to ``claude``.
    """
    desc = (
        "Launch Claude Code in the vault directory. Verifies that the target "
        "is a Research Vault instance (research_vault.toml + CLAUDE.md) and "
        "that the Claude Code CLI is installed, then replaces this process with "
        "'claude' running in the vault dir so the session boots as Alfred. "
        "Pass any extra flags after <vault_path> to forward them to claude."
    )
    if parent is not None:
        p = parent.add_parser("start", help="Launch Claude Code in the vault (front door).", description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv start", description=desc)

    p.add_argument(
        "vault_path",
        nargs="?",
        default=None,
        help="Path to the vault instance (default: current directory).",
    )
    p.add_argument(
        "passthrough",
        nargs=argparse.REMAINDER,
        help="Extra args forwarded verbatim to claude.",
    )
    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch: rv start."""
    return cmd_start(
        vault_path=args.vault_path,
        passthrough_args=list(args.passthrough) if args.passthrough else [],
    )
