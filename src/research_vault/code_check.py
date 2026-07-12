# SPDX-License-Identifier: AGPL-3.0-or-later
"""code_check.py — repo-plane conventions gate: `rv code check <project>`.

When to use: ``rv code check <project>`` validates the CODE TREE (not a note's
frontmatter) against the code-conventions doctrine
(`data/doctrine/code-conventions.md`). Distinct from the note-plane
`rv note <project> check` — this verb is about facts on the repo tree: no
notebook in the library import path, an environment pin, no data/results
duplication, science-critical tests, and releasability (secrets/paths,
CITATION.cff, LICENSE).

Severity split mirrors `note.py::run`'s hard/warn convention exactly: a
violation string starting with one of `_WARN_PREFIXES` degrades to a printed
warning that does NOT flip the exit code; everything else is HARD (exit 1).
CHECK-8a is always HARD (secrets/paths never degrade). CHECK-8b/c degrade to
WARN in local mode but drop their prefix (become HARD) in `--release` mode —
the release subset (CHECK-8b/c: "WARN locally / HARD at release").

Zero new walker: CHECK-8a COMPOSES the existing
`scripts/leakage_scan.sh --secrets-only <dir>` (dev-tree tooling, same
fail-open-when-absent posture as `git_discipline._run_leakage_scan`) — it does
NOT reimplement a secrets scanner. The absolute-personal-path regex class is a
genuinely new, generic class (leakage_scan.sh's class 4 only covers rv's own
hardcoded cluster mounts, not a project's arbitrary `/Users/…`/`/home/…`
paths) — this is the "extends... with two regex classes" the design calls for,
not a parallel scanner.

Stdlib only.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import Config, load_config
from .hashing import hash_file as _hash_file

# ---------------------------------------------------------------------------
# Severity — mirrors note.py::run's _WARN_PREFIXES convention exactly.
# A violation string starting with one of these degrades to WARN (exit 0);
# everything else is HARD (flips exit to 1).
# ---------------------------------------------------------------------------

_WARN_PREFIXES = ("[env-pin]", "[repo-policy]", "[science-path]", "[releasability]", "[leakage-scan]")

#: File extensions scanned for CHECK-8a — mirrors leakage_scan.sh's --include set.
_SCAN_EXTS = (".py", ".md", ".yml", ".yaml", ".toml", ".json", ".sh")

#: Filenames CHECK-6a's dup check ignores (scaffold housekeeping, not content).
_DUP_IGNORE_NAMES = frozenset({".gitkeep", "README.md"})


# ---------------------------------------------------------------------------
# CHECK-3b — no *.ipynb under code/src (repo-plane, HARD)
# ---------------------------------------------------------------------------

def check_notebook_in_src(code_dir: Path) -> list[str]:
    """No ``*.ipynb`` under ``code/src/`` — the library import path.

    HARD: a notebook in the import path could become (or already be) the sole
    source of a claimed number, defeating the note-plane CHECK-3a invariant
    upstream. See doctrine/code-conventions.md §2.3.
    """
    src_dir = code_dir / "src"
    if not src_dir.is_dir():
        return []
    violations = []
    for nb in sorted(src_dir.rglob("*.ipynb")):
        violations.append(f"notebook in code/src/ import path: {nb}")
    return violations


# ---------------------------------------------------------------------------
# CHECK-5 — environment pinned (repo-plane, WARN — soft, , researcher F1.2)
# ---------------------------------------------------------------------------

#: (filename, "how we know it's pinned") — checked at repo root, in order.
_LOCKFILE_CANDIDATES: list[str] = ["uv.lock", "requirements.lock", "environment.yml"]


def check_env_pinned(repo_root: Path) -> list[str]:
    """A lockfile-grade pin exists at repo root (WARN if absent/unpinned).

    ``uv.lock`` / ``requirements.lock`` are lockfiles by construction (any
    presence counts as pinned). ``environment.yml`` must carry at least one
    pinned dependency (``==`` or a conda-style ``=version`` spec) — a bare
    package-name list with no pins does not count.

    Scope note: this checks only the repo-tree half of CHECK-5. The note-plane
    half (`repro_env_python` is a concrete version, not a range) is out of
    scope for this repo-plane verb — see the return value for the deviation.
    """
    for name in ("uv.lock", "requirements.lock"):
        if (repo_root / name).is_file():
            return []
    env_yml = repo_root / "environment.yml"
    if env_yml.is_file():
        text = env_yml.read_text(encoding="utf-8", errors="replace")
        if re.search(r"[A-Za-z0-9_.\-]+\s*=[=]?\s*[0-9]", text):
            return []
        return [
            "[env-pin] environment.yml exists but has no pinned versions "
            "(expected e.g. 'numpy=1.26.0' or 'numpy==1.26.0')."
        ]
    return [
        "[env-pin] no lockfile-grade environment pin found at repo root "
        "(expected one of: " + ", ".join(_LOCKFILE_CANDIDATES) + ")."
    ]


# ---------------------------------------------------------------------------
# CHECK-6a — frozen-roots layout + SSOT integrity (repo-plane, mixed)
# ---------------------------------------------------------------------------

def _iter_files(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [p for p in root.rglob("*") if p.is_file() and p.name not in _DUP_IGNORE_NAMES]


def check_data_results_duplication(repo_root: Path) -> list[str]:
    """No tracked file under ``results/`` is also under ``data/`` (HARD).

    Duplication is detected by content hash (sha256), not filename — the
    duplicate-SSOT drift this check targets is the same artifact copied
    into both frozen roots, which a filename-only check would miss on a
    rename and false-positive on two unrelated same-named files.
    """
    data_dir = repo_root / "data"
    results_dir = repo_root / "results"
    data_hashes: dict[str, Path] = {}
    for p in _iter_files(data_dir):
        try:
            data_hashes[_hash_file(p)] = p
        except OSError:
            continue
    violations: list[str] = []
    for p in _iter_files(results_dir):
        try:
            h = _hash_file(p)
        except OSError:
            continue
        if h in data_hashes:
            violations.append(
                f"data/results duplication: {p} duplicates {data_hashes[h]} (same content hash)"
            )
    return violations


def check_runs_scores_git_policy(repo_root: Path) -> list[str]:
    """``results/runs/**`` gitignored, ``results/scores/**`` tracked (WARN on drift).

    Soft: a project may have deliberately customized its `.gitignore`; this
    flags drift from the shipped `FRAMEWORK_GITIGNORE` convention
    (`scaffold.py`) for a human to confirm, not to hard-block.
    """
    gitignore = repo_root / ".gitignore"
    if not gitignore.is_file():
        return ["[repo-policy] no .gitignore at repo root — results/runs/scores git policy cannot be verified."]
    text = gitignore.read_text(encoding="utf-8", errors="replace")
    violations = []
    if "results/runs/*" not in text and "results/runs/" not in text:
        violations.append(
            "[repo-policy] .gitignore missing the results/runs/* pattern "
            "(raw run outputs should be gitignored, not tracked)."
        )
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in ("results/scores/", "results/scores/*", "results/scores"):
            violations.append(
                "[repo-policy] .gitignore ignores results/scores/ — the tracked, "
                "citeable SSOT must never be gitignored."
            )
            break
    return violations


# ---------------------------------------------------------------------------
# CHECK-7 — science-critical path has tests (repo-plane, WARN — soft)
# ---------------------------------------------------------------------------

_SCIENCE_CRITICAL_MARKER = "# science-critical"


def _find_science_critical_markers(src_dir: Path) -> list[tuple[Path, str]]:
    """Return (file, symbol) pairs for every `# science-critical`-marked unit.

    Heuristic (R5, coarse module-level marker first): if the marker appears
    as a bare comment line preceding a `def `/`class ` line, the symbol is
    that function/class name (function-level). Otherwise (module-docstring
    line, or standalone), the symbol is the module stem (module-level).
    """
    out: list[tuple[Path, str]] = []
    for py in sorted(src_dir.rglob("*.py")):
        try:
            lines = py.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        module_level_hit = False
        for i, line in enumerate(lines):
            if _SCIENCE_CRITICAL_MARKER not in line:
                continue
            symbol = None
            for j in range(i + 1, min(i + 4, len(lines))):
                m = re.match(r"\s*(?:async\s+)?def\s+(\w+)|\s*class\s+(\w+)", lines[j])
                if m:
                    symbol = m.group(1) or m.group(2)
                    break
                if lines[j].strip():
                    break  # non-blank, non-def/class line — treat as module-level
            if symbol:
                out.append((py, symbol))
            elif not module_level_hit:
                out.append((py, py.stem))
                module_level_hit = True
    return out


def check_science_critical_tests(code_dir: Path) -> list[str]:
    """Every `# science-critical`-marked function/module has >=1 test (WARN, soft).

    Heuristic: a test file under `code/tests/` importing the marked module OR
    mentioning the marked symbol name. The oracle problem (Kanewala 2014) makes
    a hard global rule misfire — this pins the *named* load-bearing set, never
    global coverage%. Doctrine CHECK-7.
    """
    src_dir = code_dir / "src"
    tests_dir = code_dir / "tests"
    if not src_dir.is_dir():
        return []
    marked = _find_science_critical_markers(src_dir)
    if not marked:
        return []
    test_files = sorted(tests_dir.rglob("*.py")) if tests_dir.is_dir() else []
    test_blobs = []
    for tf in test_files:
        try:
            test_blobs.append(tf.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    combined = "\n".join(test_blobs)
    violations = []
    for py, symbol in marked:
        if symbol not in combined and py.stem not in combined:
            violations.append(
                f"[science-path] marked '{symbol}' in {py} has no corresponding test "
                f"under code/tests/ (no import/reference found)."
            )
    return violations


# ---------------------------------------------------------------------------
# CHECK-8a — secrets / absolute-personal paths (repo-plane, HARD)
# COMPOSES the existing leakage_scan.sh (--secrets-only) + one new generic
# absolute-personal-path regex class. Does NOT reimplement a scanner.
# ---------------------------------------------------------------------------

#: Generic absolute-personal-path regex — NOT rv's own hardcoded cluster
#: mounts (leakage_scan.sh class 4 is specific to rv's private paths); this
#: is the project-agnostic class the design calls for (researcher F4.1).
_ABS_PATH_RE = re.compile(r"(?<![\w/])(/Users/[^\s\"'\)]+|/home/[^\s\"'\)]+)")


def _find_leakage_scan_script() -> Path | None:
    """Locate `scripts/leakage_scan.sh` — dev-tree tooling, not wheel-packaged.

    Mirrors `git_discipline._run_leakage_scan`'s candidate search (same
    fail-open-when-absent posture: an adopting project without the rv dev
    tree simply can't run the composed half of CHECK-8a — surfaced as a WARN,
    never silently dropped).
    """
    candidates = [
        Path(__file__).parent.parent.parent / "scripts" / "leakage_scan.sh",
        Path.cwd() / "scripts" / "leakage_scan.sh",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _run_leakage_scan_secrets_only(code_dir: Path) -> tuple[int, str]:
    """Run `leakage_scan.sh --secrets-only <code_dir>` (directory mode, class 5 only)."""
    script = _find_leakage_scan_script()
    if script is None:
        return 0, "(leakage_scan.sh not found — secrets-scan half of CHECK-8a skipped)"
    r = subprocess.run(
        ["bash", str(script), "--secrets-only", str(code_dir)],
        capture_output=True, text=True,
    )
    return r.returncode, r.stdout + r.stderr


def check_secrets_and_paths(code_dir: Path) -> list[str]:
    """No secrets / absolute-personal paths under `code/` (HARD — CHECK-8a).

    Two halves: (1) composed `leakage_scan.sh --secrets-only` run over
    `code_dir` (credential-shaped strings — class 5); (2) a new generic
    absolute-personal-path regex scan (`/Users/…`, `/home/…`) over the same
    tree. Both HARD — this is the archetypal releasability gate.
    """
    if not code_dir.is_dir():
        return []
    violations: list[str] = []

    code, out = _run_leakage_scan_secrets_only(code_dir)
    if code != 0:
        violations.append(f"secrets scan FAILED (leakage_scan.sh --secrets-only):\n{out}")
    elif "(leakage_scan.sh not found" in out:
        violations.append(f"[leakage-scan] {out.strip()}")

    for p in sorted(code_dir.rglob("*")):
        if not p.is_file() or p.suffix not in _SCAN_EXTS:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            m = _ABS_PATH_RE.search(line)
            if m:
                violations.append(
                    f"absolute-personal path in {p}:{lineno}: {m.group(0)!r}"
                )
    return violations


# ---------------------------------------------------------------------------
# CHECK-8b/c — CITATION.cff / LICENSE (repo-plane; WARN local, HARD --release)
# ---------------------------------------------------------------------------

_CFF_REQUIRED_KEYS = ("cff-version:", "message:", "title:", "authors:")

#: (spdx-id, a signature substring — stdlib-minimal, no full SPDX matcher).
_SPDX_SIGNATURES: list[tuple[str, str]] = [
    ("MIT", "Permission is hereby granted, free of charge"),
    ("Apache-2.0", "Apache License"),
    ("BSD-3-Clause", "Redistribution and use in source and binary forms"),
    ("BSD-2-Clause", "Redistribution and use in source and binary forms"),
    # AGPL-3.0 checked before GPL-3.0: "GNU AFFERO GENERAL PUBLIC LICENSE" does
    # not contain "GNU GENERAL PUBLIC LICENSE" as a substring (AFFERO breaks
    # it), so order doesn't strictly matter here, but keep AGPL first for
    # readability since rv itself ships AGPL-3.0 (2026-07-08 relicense).
    ("AGPL-3.0", "GNU AFFERO GENERAL PUBLIC LICENSE"),
    ("GPL-3.0", "GNU GENERAL PUBLIC LICENSE"),
    ("GP.0", "GNU GENERAL PUBLIC LICENSE"),
    ("LGPL-3.0", "GNU LESSER GENERAL PUBLIC LICENSE"),
    ("MP.0", "Mozilla Public License"),
    ("Unlicense", "This is free and unencumbered software"),
]

#: Marker string in scaffold.py's own LICENSE placeholder — a scaffolded,
#: not-yet-chosen stub (never treated as a real SPDX-matched license).
_LICENSE_PLACEHOLDER_MARKER = "no license chosen yet"


def _degrade(msg: str, *, release: bool) -> str:
    """Prefix *msg* as WARN in local mode; HARD (no prefix) in --release mode."""
    return msg if release else f"[releasability] {msg}"


def check_citation_cff(repo_root: Path, *, release: bool = False) -> list[str]:
    """CITATION.cff present + minimally valid (stdlib-minimal, R2 — no pyyaml dep).

    Presence + the 4 required top-level keys (cff-version/message/title/authors)
    checked by line-prefix scan, not full CFF schema-validation. WARN in local
    mode; HARD when `release=True` (the release subset, CHECK-8b).
    """
    cff = repo_root / "CITATION.cff"
    if not cff.is_file():
        return [_degrade("CITATION.cff missing at repo root.", release=release)]
    text = cff.read_text(encoding="utf-8", errors="replace")
    lines = [ln.strip() for ln in text.splitlines()]
    missing = [k for k in _CFF_REQUIRED_KEYS if not any(ln.startswith(k) for ln in lines)]
    if missing:
        return [_degrade(
            f"CITATION.cff missing required key(s): {', '.join(missing)}.",
            release=release,
        )]
    return []


def check_license(repo_root: Path, *, release: bool = False) -> list[str]:
    """LICENSE present + matches a known SPDX signature (WARN local / HARD release).

    *Which* license is [DOC] (never guess/auto-pick); this only
    checks presence + that the content isn't the unfilled scaffold placeholder
    and matches a recognizable OSI license signature (CHECK-8c).
    """
    lic = repo_root / "LICENSE"
    if not lic.is_file():
        return [_degrade("LICENSE missing at repo root.", release=release)]
    text = lic.read_text(encoding="utf-8", errors="replace")
    if _LICENSE_PLACEHOLDER_MARKER in text:
        return [_degrade(
            "LICENSE is still the scaffolded placeholder — no SPDX license chosen yet.",
            release=release,
        )]
    for _spdx_id, sig in _SPDX_SIGNATURES:
        if sig in text:
            return []
    return [_degrade(
        "LICENSE present but does not match a known SPDX license signature.",
        release=release,
    )]


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------

def cmd_check(project: str, *, config: Config, release: bool = False) -> list[str]:
    """Run every repo-plane check for *project*. Returns the violation list.

    Each entry is either HARD (no recognized WARN prefix) or WARN (prefixed
    per `_WARN_PREFIXES`) — `run()` splits and applies the exit-code contract.
    """
    repo_root = config.project_repo_root(project)
    code_dir = repo_root / "code"

    violations: list[str] = []
    violations += check_notebook_in_src(code_dir)              # CHECK-3b HARD
    violations += check_env_pinned(repo_root)                  # CHECK-5 WARN
    violations += check_data_results_duplication(repo_root)    # CHECK-6a HARD
    violations += check_runs_scores_git_policy(repo_root)       # CHECK-6a WARN
    violations += check_science_critical_tests(code_dir)       # CHECK-7 WARN
    violations += check_secrets_and_paths(code_dir)             # CHECK-8a HARD
    violations += check_citation_cff(repo_root, release=release)   # CHECK-8b
    violations += check_license(repo_root, release=release)        # CHECK-8c
    return violations


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(sub: Any) -> None:
    p = sub.add_parser("code", help="Repo-plane code-conventions checks.")
    code_sub = p.add_subparsers(dest="code_cmd", required=True)

    check_p = code_sub.add_parser(
        "check", help="Run the repo-plane code-conventions gate for a project."
    )
    check_p.add_argument("project", help="Registered project slug.")
    check_p.add_argument(
        "--release", action="store_true",
        help="Run the release subset as HARD (CHECK-8b/c: CITATION.cff/LICENSE).",
    )

    return p


def run(args: argparse.Namespace) -> int:
    """Dispatch `rv code` subcommands. Returns exit code."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv code: config error: {e}", file=sys.stderr)
        return 1

    if args.code_cmd != "check":
        print(f"rv code: unknown subcommand {args.code_cmd!r}", file=sys.stderr)
        return 1

    try:
        violations = cmd_check(args.project, config=cfg, release=args.release)
    except (ValueError, KeyError) as e:
        print(f"rv code check: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"rv code check: unexpected error: {e}", file=sys.stderr)
        return 1

    if not violations:
        print(f"rv code check: OK — {args.project!r}")
        return 0

    # Hard/warn split — mirrors note.py::run's exact convention: a WARN-prefixed
    # violation is surfaced but does not flip the exit code.
    hard = [v for v in violations if not v.startswith(_WARN_PREFIXES)]
    warn = [v for v in violations if v.startswith(_WARN_PREFIXES)]
    for v in hard:
        print(f"  VIOLATION: {v}")
    for w in warn:
        print(f"  {w}")
    return 1 if hard else 0
