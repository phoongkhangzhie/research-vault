"""compile.py — exec-guarded LaTeX compile loop for manuscripts.

Runs the standard academic compile sequence:
  pdflatex main.tex → bibtex main → pdflatex main.tex × 2

Then runs a bounded chktex fix-loop (max N iterations).

Exec-guard (§5J.5):
  ``pdflatex``, ``bibtex``, and ``chktex`` are SYSTEM PREREQUISITES
  (not pip-installable). If absent:
  - Print a friendly message: "install texlive-full (system package) to compile manuscripts"
  - Exit cleanly (return exit_code=1) — NEVER crash with a raw traceback.

This mirrors the wandb/asta prerequisite guards in research.py:59-66
and wait_for.py:671-677.

Stdlib only.
sr: SR-MS-1b
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

# Maximum iterations of the chktex fix-loop
CHKTEX_MAX_ITERS = 3


# ---------------------------------------------------------------------------
# Exec-guard helpers
# ---------------------------------------------------------------------------

def _find_tool(name: str) -> str | None:
    """Find a LaTeX tool on PATH. Checks /opt/homebrew/bin first (macOS)."""
    # Check if we have the tool on the existing PATH
    found = shutil.which(name)
    if found:
        return found
    # Also check /opt/homebrew/bin explicitly (common on macOS with Homebrew)
    homebrew_path = f"/opt/homebrew/bin/{name}"
    if os.path.isfile(homebrew_path) and os.access(homebrew_path, os.X_OK):
        return homebrew_path
    return None


def _texlive_absent_message(missing: list[str]) -> str:
    """Return the friendly absent-texlive message."""
    tools_str = ", ".join(missing)
    return (
        f"rv manuscript compile: missing LaTeX tool(s): {tools_str}\n"
        "  manuscript compile needs LaTeX — install texlive-full (system package):\n"
        "    macOS:   brew install --cask mactex   or   brew install basictex\n"
        "    Ubuntu:  sudo apt-get install texlive-full chktex\n"
        "    Other:   https://www.tug.org/texlive/\n"
        "  Note: LaTeX tools are NOT pip-installable — use your system package manager."
    )


# ---------------------------------------------------------------------------
# Hash helper (for updating manuscript_hash after compile)
# ---------------------------------------------------------------------------

def _sha256_file(path: Path) -> str:
    """Compute sha256:<hex> of a file (streaming)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


# ---------------------------------------------------------------------------
# Note field update helper
# ---------------------------------------------------------------------------

def _update_note_field(note_path: Path, field: str, value: str) -> None:
    """Update a single frontmatter field in an existing manuscript note.

    Uses ``[ \\t]*`` (NOT ``\\s*``) after the colon to avoid consuming the
    trailing newline — which would eat the next frontmatter field into group 1
    and silently delete it on substitution.
    """
    if not note_path.exists():
        return
    text = note_path.read_text(encoding="utf-8")
    # [ \t]* matches only horizontal whitespace — never eats the newline
    # or the next YAML key (which \s* would silently consume).
    pattern = re.compile(rf"^({re.escape(field)}:[ \t]*)(.*)$", re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(rf"\g<1>{value}", text, count=1)
        note_path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Compile step runner
# ---------------------------------------------------------------------------

def _run_cmd(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: int = 120,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run a command, return (returncode, stdout, stderr)."""
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env or {**os.environ, "PATH": _build_path()},
    )
    return result.returncode, result.stdout, result.stderr


def _build_path() -> str:
    """Return PATH with /opt/homebrew/bin prepended (macOS TeX Live compatibility)."""
    existing = os.environ.get("PATH", "")
    homebrew = "/opt/homebrew/bin"
    if homebrew not in existing:
        return f"{homebrew}:{existing}"
    return existing


# ---------------------------------------------------------------------------
# chktex fix-loop
# ---------------------------------------------------------------------------

def _run_chktex(
    chktex_bin: str,
    tex_path: Path,
    cwd: Path,
) -> tuple[int, str]:
    """Run chktex on tex_path, return (returncode, output)."""
    rc, out, err = _run_cmd(
        [chktex_bin, "-q", str(tex_path)],
        cwd=cwd,
    )
    return rc, (out + err)


def _chktex_error_count(output: str) -> int:
    """Count the number of chktex Error lines in output."""
    return len(re.findall(r"^Error ", output, re.MULTILINE))


def _chktex_fix_loop(
    chktex_bin: str | None,
    main_tex: Path,
    cwd: Path,
    max_iters: int = CHKTEX_MAX_ITERS,
) -> dict[str, Any]:
    """Run the bounded chktex fix-loop.

    The loop runs chktex up to max_iters times. Since we cannot actually
    auto-fix LaTeX errors in the loop (that would require an agent), the
    loop records the error count per iteration and returns a summary.

    In practice, this is a "report + bounded check" pattern:
      - If chktex is absent → skip (exec-guard already handled above).
      - Run chktex, report errors.
      - If errors > 0 on the first run, record as a warning (not a hard fail).

    Returns dict with "warnings", "chktex_errors", "iterations".
    """
    if chktex_bin is None:
        return {"warnings": [], "chktex_errors": 0, "iterations": 0}

    warnings: list[str] = []
    last_error_count = 0
    for i in range(max_iters):
        rc, output = _run_chktex(chktex_bin, main_tex, cwd)
        count = _chktex_error_count(output)
        last_error_count = count
        if count == 0:
            break
        if i < max_iters - 1:
            warnings.append(
                f"chktex iteration {i + 1}/{max_iters}: {count} error(s) "
                f"(auto-fix not available — review manually):\n{output[:500]}"
            )

    return {
        "warnings": warnings,
        "chktex_errors": last_error_count,
        "iterations": min(i + 1, max_iters),
    }


# ---------------------------------------------------------------------------
# Main compile entry point
# ---------------------------------------------------------------------------

def run_compile(
    manuscript_note_path: Path,
    tree_root: Path,
    *,
    chktex_max_iters: int = CHKTEX_MAX_ITERS,
    timeout: int = 120,
) -> dict[str, Any]:
    """Run the exec-guarded LaTeX compile loop for a manuscript.

    When to use: called by the ``compile`` DAG node and ``rv manuscript compile``.
    Runs: pdflatex → bibtex → pdflatex × 2 + chktex fix-loop.

    Exec-guard: if pdflatex or bibtex is absent, returns exit_code=1 with a
    friendly message (NEVER raises / crashes).

    On success:
      - Writes main.pdf to tree_root.
      - Updates manuscript_pdf + manuscript_hash in the manuscript note.

    Args:
        manuscript_note_path: path to the manuscript/<id>.md OKF note.
        tree_root: path to manuscripts/<id>/ (contains main.tex).
        chktex_max_iters: max iterations of the chktex fix-loop.
        timeout: subprocess timeout in seconds per command.

    Returns:
        dict with:
          "exit_code": int (0 = success, 1 = failure)
          "message": str (friendly error message on failure, or success summary)
          "log": str (combined pdflatex output)
          "chktex": dict (fix-loop summary)
          "pdf_path": str | None (path to main.pdf if produced)
    """
    main_tex = tree_root / "main.tex"
    if not main_tex.exists():
        return {
            "exit_code": 1,
            "message": f"rv manuscript compile: main.tex not found at {main_tex}",
            "log": "",
            "chktex": {},
            "pdf_path": None,
        }

    # ── Exec-guard ─────────────────────────────────────────────────────────
    missing_tools: list[str] = []
    pdflatex = _find_tool("pdflatex")
    bibtex = _find_tool("bibtex")
    chktex = _find_tool("chktex")  # optional — only warn if absent

    if pdflatex is None:
        missing_tools.append("pdflatex")
    if bibtex is None:
        missing_tools.append("bibtex")

    if missing_tools:
        return {
            "exit_code": 1,
            "message": _texlive_absent_message(missing_tools),
            "log": "",
            "chktex": {},
            "pdf_path": None,
        }

    # ── Compile sequence ───────────────────────────────────────────────────
    combined_log: list[str] = []
    cwd = tree_root
    base = "main"

    def _step(cmd: list[str], label: str) -> int:
        rc, out, err = _run_cmd(cmd, cwd=cwd, timeout=timeout)
        combined_log.append(f"=== {label} (rc={rc}) ===\n{out}\n{err}")
        return rc

    # Step 1: pdflatex (first pass — builds .aux)
    rc1 = _step([pdflatex, "-interaction=nonstopmode", f"{base}.tex"], "pdflatex pass 1")
    if rc1 != 0:
        # Recoverable — aux file may exist; continue to bibtex
        pass

    # Step 2: bibtex (resolves citations)
    rc2 = _step([bibtex, base], "bibtex")
    # bibtex returning non-zero with no .bib is expected for documents with no refs

    # Step 3: pdflatex (second pass — resolves refs)
    rc3 = _step([pdflatex, "-interaction=nonstopmode", f"{base}.tex"], "pdflatex pass 2")

    # Step 4: pdflatex (third pass — resolves any remaining cross-refs)
    rc4 = _step([pdflatex, "-interaction=nonstopmode", f"{base}.tex"], "pdflatex pass 3")

    full_log = "\n\n".join(combined_log)
    pdf_path = cwd / f"{base}.pdf"
    success = pdf_path.exists()

    # ── chktex fix-loop ────────────────────────────────────────────────────
    chktex_result = _chktex_fix_loop(
        chktex, main_tex, cwd, max_iters=chktex_max_iters
    )

    if not success:
        return {
            "exit_code": 1,
            "message": (
                f"rv manuscript compile: PDF not produced — check log for errors.\n"
                f"Last pdflatex exit code: {rc4}"
            ),
            "log": full_log,
            "chktex": chktex_result,
            "pdf_path": None,
        }

    # ── Update note fields ─────────────────────────────────────────────────
    pdf_hash = _sha256_file(pdf_path)
    _update_note_field(manuscript_note_path, "manuscript_pdf", str(pdf_path))
    _update_note_field(manuscript_note_path, "manuscript_hash", pdf_hash)

    return {
        "exit_code": 0,
        "message": (
            f"rv manuscript compile: OK — {pdf_path.name} produced "
            f"({pdf_path.stat().st_size:,} bytes).\n"
            f"manuscript_hash: {pdf_hash}"
        ),
        "log": full_log,
        "chktex": chktex_result,
        "pdf_path": str(pdf_path),
    }
