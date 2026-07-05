"""bootstrap.py — `rv bootstrap` — best-effort venv + toolkit install.

When to use: ``rv bootstrap`` when Tier-1 toolkit packages are missing (e.g.
after a fresh clone on a new machine, or after `pip install research-vault --no-deps`).
Creates a `.venv` in the current directory and pip-installs the toolkit tiers.

Behaviour:
  - Tier-1 (portable pure-wheel defaults): installed as hard requirements.
    If Tier-1 fails the whole command exits non-zero with a clear error.
  - Tier-2 (GPU-fragile [local] extra): attempted best-effort.
    Failures are tolerated and logged as "skipped (reason)" — never crash.
  - Sidesteps PEP-668 (externally-managed envs) by using a local `.venv`.
  - Never modifies the system Python or the active environment.
  - Prints a per-package "installed / skipped (reason)" log.

Exit codes:
  0 — Tier-1 installed (Tier-2 may have partial failures)
  1 — Tier-1 failed (toolkit is unusable)

Stdlib only — no toolkit imports at module level (bare-import guard).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Install helpers
# ---------------------------------------------------------------------------

_TIER1_SPEC = "research-vault"              # pulls default deps (Tier-1)
_TIER2_SPEC = "research-vault[local]"       # adds Tier-2 on top
_SERVE_VLLM_SPEC = "research-vault[local,serve-vllm]"
_SERVE_SGLANG_SPEC = "research-vault[local,serve-sglang]"


def _find_pip(venv_dir: Path) -> Path:
    """Return path to the pip executable inside the venv."""
    # Unix / macOS
    pip = venv_dir / "bin" / "pip"
    if pip.exists():
        return pip
    # Windows
    pip_win = venv_dir / "Scripts" / "pip.exe"
    if pip_win.exists():
        return pip_win
    raise FileNotFoundError(f"pip not found inside venv at {venv_dir}")


def _find_python(venv_dir: Path) -> Path:
    """Return path to the python executable inside the venv."""
    py = venv_dir / "bin" / "python"
    if py.exists():
        return py
    py_win = venv_dir / "Scripts" / "python.exe"
    if py_win.exists():
        return py_win
    raise FileNotFoundError(f"python not found inside venv at {venv_dir}")


def _create_venv(venv_dir: Path) -> tuple[bool, str]:
    """Create a venv at venv_dir using stdlib venv. Returns (ok, message)."""
    if venv_dir.exists():
        return True, f"venv: already exists at {venv_dir}"
    try:
        result = subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True, f"venv: created at {venv_dir}"
        return False, (
            f"venv: FAILED to create at {venv_dir}\n"
            f"  {result.stderr.strip()}"
        )
    except Exception as exc:
        return False, f"venv: FAILED — {exc}"


def _pip_install(
    pip: Path,
    spec: str,
    *,
    upgrade: bool = True,
    extra_flags: list[str] | None = None,
) -> tuple[bool, str, str]:
    """Run pip install <spec>. Returns (ok, stdout, stderr)."""
    cmd = [str(pip), "install", spec]
    if upgrade:
        cmd.append("--upgrade")
    if extra_flags:
        cmd.extend(extra_flags)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as exc:
        return False, "", str(exc)


def _run_bootstrap(
    venv_dir: Path,
    *,
    tier2: bool = True,
    serve: str | None = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Core bootstrap logic. Returns a result dict with all outcome details.

    venv_dir: where to create / reuse the .venv
    tier2: whether to attempt Tier-2 install
    serve: None | "vllm" | "sglang" — serving sub-extra to attempt
    verbose: whether to print pip output
    """
    lines: list[str] = ["=== rv bootstrap — Research Vault toolkit install ===", ""]
    tier1_ok = False
    tier2_ok = False
    serve_ok = False
    tier2_reason = ""
    serve_reason = ""

    # 1. Create venv
    venv_ok, venv_msg = _create_venv(venv_dir)
    lines.append(venv_msg)
    if not venv_ok:
        lines.append("")
        lines.append("Result: FAIL — could not create .venv (see above).")
        return {
            "tier1_ok": False,
            "tier2_ok": False,
            "serve_ok": False,
            "tier2_reason": "venv creation failed",
            "serve_reason": "venv creation failed",
            "venv_dir": str(venv_dir),
            "report": "\n".join(lines),
        }

    # Locate pip
    try:
        pip = _find_pip(venv_dir)
    except FileNotFoundError as exc:
        msg = f"pip: NOT FOUND in venv — {exc}"
        lines.append(msg)
        lines.append("")
        lines.append("Result: FAIL — could not locate pip inside .venv.")
        return {
            "tier1_ok": False,
            "tier2_ok": False,
            "serve_ok": False,
            "tier2_reason": "pip not found",
            "serve_reason": "pip not found",
            "venv_dir": str(venv_dir),
            "report": "\n".join(lines),
        }

    # 2. Tier-1 hard install
    lines.append("")
    lines.append(f"Tier-1 install: {_TIER1_SPEC}")
    ok, stdout, stderr = _pip_install(pip, _TIER1_SPEC)
    if ok:
        tier1_ok = True
        lines.append("  [OK] Tier-1 installed")
    else:
        tier1_ok = False
        lines.append("  [FAIL] Tier-1 install failed")
        lines.append(f"  stderr: {stderr.strip()[:400]}")
    if verbose and stdout:
        for ln in stdout.strip().splitlines()[-10:]:
            lines.append(f"  | {ln}")

    if not tier1_ok:
        lines.append("")
        lines.append(
            "Result: FAIL — Tier-1 install failed. "
            "Fix the error above and re-run `rv bootstrap`."
        )
        return {
            "tier1_ok": False,
            "tier2_ok": False,
            "serve_ok": False,
            "tier2_reason": "tier1 failed",
            "serve_reason": "tier1 failed",
            "venv_dir": str(venv_dir),
            "report": "\n".join(lines),
        }

    # 3. Tier-2 best-effort
    if tier2:
        lines.append("")
        lines.append(f"Tier-2 install (best-effort): {_TIER2_SPEC}")
        ok, stdout, stderr = _pip_install(pip, _TIER2_SPEC)
        if ok:
            tier2_ok = True
            lines.append("  [OK] Tier-2 installed")
        else:
            tier2_ok = False
            tier2_reason = (stderr or "unknown error").strip()[:200]
            lines.append(
                "  [WARN] Tier-2 skipped — GPU-fragile packages may need a CUDA environment."
            )
            lines.append(f"  reason: {tier2_reason}")
            lines.append(
                "  Install on your GPU box: pip install research-vault[local]"
            )
        if verbose and stdout:
            for ln in stdout.strip().splitlines()[-10:]:
                lines.append(f"  | {ln}")
    else:
        lines.append("")
        lines.append("Tier-2 install: skipped (--no-tier2)")

    # 4. Serving stack sub-extra (optional)
    if serve:
        if serve == "vllm":
            spec = _SERVE_VLLM_SPEC
        elif serve == "sglang":
            spec = _SERVE_SGLANG_SPEC
        else:
            spec = None
            lines.append(f"  [WARN] Unknown serve target {serve!r} — skipping.")

        if spec:
            lines.append("")
            lines.append(f"Serve stack install (best-effort): {spec}")
            ok, stdout, stderr = _pip_install(pip, spec)
            if ok:
                serve_ok = True
                lines.append(f"  [OK] {serve} serving stack installed")
            else:
                serve_ok = False
                serve_reason = (stderr or "unknown error").strip()[:200]
                lines.append(
                    f"  [WARN] {serve} skipped — GPU/CUDA-specific install. "
                    "Try on your GPU node."
                )
                lines.append(f"  reason: {serve_reason}")
            if verbose and stdout:
                for ln in stdout.strip().splitlines()[-10:]:
                    lines.append(f"  | {ln}")

    # 5. Summary
    lines.append("")
    lines.append(
        f"Venv: {venv_dir}\n"
        "To activate: source .venv/bin/activate  (or .venv\\Scripts\\activate on Windows)\n"
        "Run `rv check` to verify the installed toolkit."
    )
    lines.append("")
    lines.append(
        "Result: OK — Tier-1 installed."
        + ("" if not tier2 else " Tier-2: " + ("installed." if tier2_ok else "skipped (see above)."))
    )

    return {
        "tier1_ok": tier1_ok,
        "tier2_ok": tier2_ok,
        "serve_ok": serve_ok,
        "tier2_reason": tier2_reason,
        "serve_reason": serve_reason,
        "venv_dir": str(venv_dir),
        "report": "\n".join(lines),
    }


# ---------------------------------------------------------------------------
# CLI verb
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``bootstrap`` verb.

    When to use: ``rv bootstrap`` when Tier-1 toolkit packages are missing.
    Creates a `.venv` and pip-installs the research toolkit (Tier-1 hard,
    Tier-2 best-effort). Sidesteps PEP-668 (externally-managed envs).
    Run `rv check` after to verify the installed stack.
    """
    desc = (
        "Best-effort toolkit bootstrap — create .venv and pip-install Research Vault tiers. "
        "Tier-1 (portable: model SDKs, data, stats, eval, multilingual, utilities) is "
        "installed as a hard requirement. "
        "Tier-2 (GPU-fragile: torch, transformers, accelerate, etc.) is attempted + tolerated. "
        "Never modifies your system Python. "
        "Run `rv check` after to verify the installed stack. "
        "Anti-pattern: do NOT pip-install Tier-2 on a CPU-only laptop — "
        "install it on your GPU box instead."
    )
    if parent is not None:
        p = parent.add_parser(
            "bootstrap",
            help="Auto-install toolkit tiers into .venv (Tier-1 hard, Tier-2 best-effort).",
            description=desc,
        )
    else:
        p = argparse.ArgumentParser(prog="rv bootstrap", description=desc)

    p.add_argument(
        "--venv",
        metavar="DIR",
        default=".venv",
        help="Directory for the venv (default: .venv in cwd).",
    )
    p.add_argument(
        "--no-tier2",
        action="store_true",
        default=False,
        help="Skip the Tier-2 [local] install attempt (useful on CPU-only machines).",
    )
    p.add_argument(
        "--serve",
        metavar="BACKEND",
        default=None,
        choices=["vllm", "sglang"],
        help="Also attempt to install a serving sub-extra: vllm (default docs) or sglang.",
    )
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Show pip install output (last 10 lines per step).",
    )

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch: rv bootstrap."""
    venv_dir = Path(args.venv).resolve()
    result = _run_bootstrap(
        venv_dir,
        tier2=not args.no_tier2,
        serve=args.serve,
        verbose=args.verbose,
    )
    print(result["report"])
    return 0 if result["tier1_ok"] else 1
