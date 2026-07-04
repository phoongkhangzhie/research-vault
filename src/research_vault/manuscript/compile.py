"""compile.py — exec-guarded LaTeX compile loop for manuscripts.

Runs the grounding-builders first, then the standard academic compile sequence:
  build_refs_bib → inject_results → inject_appendix
  → pdflatex main.tex → bibtex main → pdflatex main.tex × 2

Then runs a bounded chktex fix-loop (max N iterations).

Anti-fabrication contract (§5J.3/§5J.4):
  - build_refs_bib is ALWAYS called before pdflatex — never render without a grounded .bib.
  - inject_results is ALWAYS called — macros come from hash-verified artifacts only.
  - An unmatched \\cite hard-fails the compile (never silently produce an ungrounded PDF).
  - A results_hash mismatch hard-fails the compile (never silently proceed with wrong data).

Exec-guard (§5J.5):
  ``pdflatex``, ``bibtex``, and ``chktex`` are SYSTEM PREREQUISITES
  (not pip-installable). If absent:
  - Print a friendly message: "install texlive-full (system package) to compile manuscripts"
  - Exit cleanly (return exit_code=1, status="blocked-prereq") — NEVER crash.

Status key semantics (SR-DRAFT-RENDER):
  run_compile (and run_prep) always return a ``status`` key:
    "ok"            — compile succeeded, main.pdf produced.
    "blocked-prereq" — LaTeX toolchain (pdflatex/bibtex) absent; install texlive to unblock.
                       This is a RESUMABLE state — the draft is NOT broken.
    "failed"        — A real error: unmatched \\cite, results_hash mismatch, or pdflatex
                       hard-fail. The draft or its grounding requires attention.

  blocked-prereq is INTENTIONALLY DISTINCT from failed so DAG operators / the compile node
  can tell "install texlive to finish" from "the draft is broken."

This mirrors the wandb/asta prerequisite guards in research.py:59-66
and wait_for.py:671-677.

Stdlib only.
sr: SR-MS-1b, SR-DRAFT-RENDER
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..hashing import hash_file as _sha256_file  # canonical hasher — never duplicate

# Maximum iterations of the chktex fix-loop
CHKTEX_MAX_ITERS = 3


# ---------------------------------------------------------------------------
# Grounding-builder helpers
# ---------------------------------------------------------------------------

def _resolve_experiment_notes(manuscript_note_path: Path) -> list[Path]:
    """Resolve experiment note paths from the manuscript note's synthesized_okf field.

    The ``synthesized_okf`` frontmatter field lists the OKF note ids scoped
    to this manuscript (e.g. ``"experiments/exp-q1, findings/find-q1"``).
    This function extracts the ``experiments/`` items and returns their note
    paths relative to the project notes directory (parent.parent of the note).

    Returns an empty list when synthesized_okf is unset or the experiment notes
    do not exist — callers handle absence gracefully.
    """
    if not manuscript_note_path.exists():
        return []
    try:
        from research_vault.note import _parse_frontmatter
        text = manuscript_note_path.read_text(encoding="utf-8")
        fields, _ = _parse_frontmatter(text)
        scope_str = fields.get("synthesized_okf", "").strip()
        if not scope_str:
            return []
        # manuscript note lives at: project_notes_dir/manuscript/<id>.md
        # → parent.parent = project_notes_dir
        project_notes_dir = manuscript_note_path.parent.parent
        notes: list[Path] = []
        for item in scope_str.split(","):
            item = item.strip()
            if item.startswith("experiments/"):
                exp_name = item[len("experiments/"):]
                candidate = project_notes_dir / "experiments" / f"{exp_name}.md"
                if candidate.exists():
                    notes.append(candidate)
        return notes
    except Exception:
        return []


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
# Shared grounding-builder orchestration (anti-fabrication contract §5J.4)
# ---------------------------------------------------------------------------

def _run_grounding_builders(
    manuscript_note_path: Path,
    tree_root: Path,
    library_path: Path,
    experiment_notes: list[Path],
    *,
    label: str,
    extra_unmatched_msg: str = "",
) -> tuple[int, dict[str, Any] | None, list[str]]:
    """Run build_refs_bib → inject_results → inject_appendix.

    This is the SINGLE canonical anti-fabrication contract site (§5J.4).
    Called by BOTH ``run_prep`` and ``run_compile`` so the hard-fails live in
    exactly one place — a future divergence is structurally impossible.

    Hard-fails (never silently skip):
    - Any unmatched ``\\cite`` → exit_code=1 (never render an ungrounded PDF).
    - results_hash mismatch → exit_code=1 (never proceed with wrong data).

    Args:
        manuscript_note_path: path to the manuscript OKF note.
        tree_root: path to manuscripts/<id>/ (contains main.tex).
        library_path: resolved path to library.json (must be pre-resolved by caller).
        experiment_notes: resolved list of experiment note paths (pre-resolved by caller).
        label: caller label prefix for error messages, e.g.
            ``"rv manuscript compile --prep-only"`` or ``"rv manuscript compile"``.
        extra_unmatched_msg: optional extra line appended after "BLOCKED" in the
            unmatched-cite message. ``run_compile`` uses this to cite §5J.4;
            ``run_prep`` leaves it empty.

    Returns:
        ``(exit_code, failure_base | None, builder_warnings)``

        - ``exit_code`` — 0 on success, 1 on failure.
        - ``failure_base`` — on failure, a dict with ``"exit_code"``, ``"message"``,
          and ``"builder_warnings"``; the caller merges in any path-specific extra
          keys (``"log"``, ``"chktex"``, ``"pdf_path"``) before returning.
          ``None`` on success.
        - ``builder_warnings`` — non-fatal warning list; on success, caller extends
          its own response dict with this list.
    """
    builder_warnings: list[str] = []

    # ── Step 1: build_refs_bib ───────────────────────────────────────────────
    from research_vault.manuscript.bib import build_refs_bib
    bib_errors, _bib_path = build_refs_bib(
        tree_root,
        library_path=library_path,
        cite_tex_files=None,  # rglob all .tex under tree_root
    )
    if bib_errors:
        unmatched = [
            e for e in bib_errors
            if "unmatched" in e.lower() and "cite" in e.lower()
        ]
        if unmatched:
            return 1, {
                "exit_code": 1,
                "status": "failed",
                "message": (
                    f"{label}: BLOCKED — unmatched \\cite commands.\n"
                    + extra_unmatched_msg
                    + "Fix: run `rv cite add <doi>` then `rv cite sync` for each:\n  "
                    + "\n  ".join(unmatched)
                ),
                "builder_warnings": [],
            }, []
        # Non-fatal bib errors (library.json missing, malformed) → warn, continue
        builder_warnings.extend(bib_errors)

    # ── Step 2: inject_results ───────────────────────────────────────────────
    from research_vault.manuscript.results_inject import inject_results
    try:
        inj = inject_results(
            manuscript_note_path=manuscript_note_path,
            experiment_notes=experiment_notes,
            tree_root=tree_root,
        )
        builder_warnings.extend(inj.get("errors", []))
    except ValueError as exc:
        return 1, {
            "exit_code": 1,
            "status": "failed",
            "message": f"{label}: BLOCKED — results hash mismatch.\n{exc}",
            "builder_warnings": builder_warnings,
        }, builder_warnings

    # ── Step 3: inject_appendix ──────────────────────────────────────────────
    from research_vault.manuscript.appendix import inject_appendix
    inject_appendix(tree_root=tree_root, experiment_notes=experiment_notes)

    return 0, None, builder_warnings


# ---------------------------------------------------------------------------
# Prep-only entry point (draft-time macro-visibility seam — SR-MS-1c)
# ---------------------------------------------------------------------------

def run_prep(
    manuscript_note_path: Path,
    tree_root: Path,
    *,
    library_path: Path | None = None,
    experiment_notes: list[Path] | None = None,
) -> dict[str, Any]:
    """Run grounding-builders only — no pdflatex render.

    When to use: called by the ``compile`` DAG node's pre-draft step and
    ``rv manuscript compile --prep-only`` to populate refs.bib, results.tex, and
    sections/appendix-repro.tex BEFORE the drafting agent starts writing.

    This lets a ``results-discussion`` (or ``assemble``) node reference
    ``\\resultAcc`` and sibling macros while drafting, without waiting for the
    full compile at the end of the DAG.

    Delegates to ``_run_grounding_builders`` — the single anti-fabrication
    contract site (§5J.4). Does NOT require pdflatex/bibtex/chktex.

    Idempotent: builders overwrite their output files; running run_prep twice
    (or run_prep then run_compile, which re-runs the same builders) produces
    identical refs.bib / results.tex / appendix-repro.tex output.

    Args:
        manuscript_note_path: path to the manuscript/<id>.md OKF note.
        tree_root: path to manuscripts/<id>/ (contains main.tex).
        library_path: path to library.json. When None, defaults to
            manuscript_note_path.parent.parent / "library.json".
        experiment_notes: list of experiments/ note paths to read results from.
            When None, resolved automatically from the note's ``synthesized_okf``
            field.

    Returns:
        dict with:
          "exit_code": int (0 = success, 1 = failure)
          "message": str (friendly message — success summary or error)
          "pdf_path": None (never produced by prep-only)
          "builder_warnings": list[str] (non-fatal builder issues)

    sr: SR-MS-1c
    """
    main_tex = tree_root / "main.tex"
    if not main_tex.exists():
        return {
            "exit_code": 1,
            "status": "failed",
            "message": f"rv manuscript compile --prep-only: main.tex not found at {main_tex}",
            "pdf_path": None,
            "builder_warnings": [],
        }

    # Resolve library.json path (default: project_notes_dir/library.json)
    if library_path is None:
        library_path = manuscript_note_path.parent.parent / "library.json"

    # Resolve experiment notes from synthesized_okf if not provided
    if experiment_notes is None:
        experiment_notes = _resolve_experiment_notes(manuscript_note_path)

    exit_code, failure, builder_warnings = _run_grounding_builders(
        manuscript_note_path, tree_root, library_path, experiment_notes,
        label="rv manuscript compile --prep-only",
    )
    if failure is not None:
        return {"pdf_path": None, **failure}

    return {
        "exit_code": 0,
        "status": "ok",
        "message": (
            "rv manuscript compile --prep-only: OK — "
            "refs.bib, results.tex, and appendix-repro.tex populated.\n"
            "Macros are ready for drafting agents. "
            "Run `rv manuscript compile <id>` for the full PDF render."
        ),
        "pdf_path": None,
        "builder_warnings": builder_warnings,
    }


# ---------------------------------------------------------------------------
# Main compile entry point
# ---------------------------------------------------------------------------

def run_compile(
    manuscript_note_path: Path,
    tree_root: Path,
    *,
    library_path: Path | None = None,
    experiment_notes: list[Path] | None = None,
    chktex_max_iters: int = CHKTEX_MAX_ITERS,
    timeout: int = 120,
) -> dict[str, Any]:
    """Run the grounding-builders then the exec-guarded LaTeX compile loop.

    When to use: called by the ``compile`` DAG node and ``rv manuscript compile``.

    Execution order (anti-fabrication contract §5J.3/§5J.4):
      1. build_refs_bib — exports closed .bib from library.json.
         Hard-fails on any unmatched \\cite (never render an ungrounded PDF).
      2. inject_results — writes hash-verified \\newcommand macros into results.tex.
         Hard-fails on results_hash mismatch.
      3. inject_appendix — machine-populates sections/appendix-repro.tex.
      4. pdflatex → bibtex → pdflatex × 2 + chktex fix-loop.

    Exec-guard: if pdflatex or bibtex is absent, returns exit_code=1 with a
    friendly message (NEVER raises / crashes).

    On success:
      - Writes main.pdf to tree_root.
      - Updates manuscript_pdf + manuscript_hash in the manuscript note.

    Args:
        manuscript_note_path: path to the manuscript/<id>.md OKF note.
        tree_root: path to manuscripts/<id>/ (contains main.tex).
        library_path: path to library.json. When None, defaults to
            manuscript_note_path.parent.parent / "library.json" (the standard
            location set by ``rv project new``).
        experiment_notes: list of experiments/ note paths to read results from.
            When None, resolved automatically from the note's ``synthesized_okf``
            field (recommended path — lets compile be called without pre-resolution).
        chktex_max_iters: max iterations of the chktex fix-loop.
        timeout: subprocess timeout in seconds per command.

    Returns:
        dict with:
          "exit_code": int (0 = success, 1 = failure)
          "message": str (friendly error message on failure, or success summary)
          "log": str (combined pdflatex output)
          "chktex": dict (fix-loop summary)
          "pdf_path": str | None (path to main.pdf if produced)
          "builder_warnings": list[str] (non-fatal builder issues, e.g. missing library)
    """
    main_tex = tree_root / "main.tex"
    if not main_tex.exists():
        return {
            "exit_code": 1,
            "status": "failed",
            "message": f"rv manuscript compile: main.tex not found at {main_tex}",
            "log": "",
            "chktex": {},
            "pdf_path": None,
            "builder_warnings": [],
        }

    # ── Phase 1: Grounding builders ─────────────────────────────────────────
    # Delegates to _run_grounding_builders — the single anti-fabrication
    # contract site (§5J.4). Must run BEFORE pdflatex.

    # Resolve library.json path (default: project_notes_dir/library.json)
    if library_path is None:
        library_path = manuscript_note_path.parent.parent / "library.json"

    # Resolve experiment notes from synthesized_okf if not provided
    if experiment_notes is None:
        experiment_notes = _resolve_experiment_notes(manuscript_note_path)

    _gc_exit, _gc_failure, builder_warnings = _run_grounding_builders(
        manuscript_note_path, tree_root, library_path, experiment_notes,
        label="rv manuscript compile",
        extra_unmatched_msg="Rendering an ungrounded PDF is refused (§5J.4).\n",
    )
    if _gc_failure is not None:
        return {"log": "", "chktex": {}, "pdf_path": None, **_gc_failure}

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
            "status": "blocked-prereq",
            "message": _texlive_absent_message(missing_tools),
            "log": "",
            "chktex": {},
            "pdf_path": None,
            "builder_warnings": builder_warnings,
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
            "status": "failed",
            "message": (
                f"rv manuscript compile: PDF not produced — check log for errors.\n"
                f"Last pdflatex exit code: {rc4}"
            ),
            "log": full_log,
            "chktex": chktex_result,
            "pdf_path": None,
            "builder_warnings": builder_warnings,
        }

    # ── Update note fields ─────────────────────────────────────────────────
    pdf_hash = _sha256_file(pdf_path)
    _update_note_field(manuscript_note_path, "manuscript_pdf", str(pdf_path))
    _update_note_field(manuscript_note_path, "manuscript_hash", pdf_hash)

    return {
        "exit_code": 0,
        "status": "ok",
        "message": (
            f"rv manuscript compile: OK — PDF produced at {pdf_path} "
            f"({pdf_path.stat().st_size:,} bytes).\n"
            f"manuscript_hash: {pdf_hash}"
        ),
        "log": full_log,
        "chktex": chktex_result,
        "pdf_path": str(pdf_path),
        "builder_warnings": builder_warnings,
    }
