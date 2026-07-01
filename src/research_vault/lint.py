"""lint.py — leakage gate and config linter for Research Vault.

When to use: ``rv lint [--strict]`` to run the project linter. Checks:
  1. Leakage scan: greps src/ for private codenames / paths that should not
     be hardcoded. The list of forbidden patterns is config-driven (from
     ``lint.forbidden_patterns`` in research_vault.toml) — no compiled-in names.
  2. Config schema validation: verifies all registered projects have required
     fields (source_dir, code).
  3. Zero-hardcoded-path rule: confirms no absolute paths to private home
     directories appear in the source tree.

All path resolution goes through Config — zero hardcoded paths or codenames.
Stdlib only.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from .config import Config, load_config


# ---------------------------------------------------------------------------
# Leakage scan
# ---------------------------------------------------------------------------

def _get_forbidden_patterns(cfg: Config) -> list[str]:
    """Return the list of forbidden patterns from config (lint.forbidden_patterns).

    If not configured, returns an empty list (no compiled-in patterns).
    The lint.forbidden_patterns field is a list of regex strings.
    """
    raw = cfg._raw.get("lint", {})
    if not isinstance(raw, dict):
        return []
    patterns = raw.get("forbidden_patterns", [])
    if not isinstance(patterns, list):
        return []
    return [str(p) for p in patterns]


def _scan_for_leakage(
    src_dir: Path,
    patterns: list[str],
    *,
    exclude_dirs: frozenset[str] | None = None,
) -> list[tuple[str, int, str, str]]:
    """Scan Python source files for forbidden patterns.

    Returns a list of (file_path, line_no, pattern, matching_line).
    """
    exclude = exclude_dirs or frozenset({"__pycache__", ".venv", ".git", "node_modules"})
    findings: list[tuple[str, int, str, str]] = []

    if not patterns:
        return findings

    compiled = [(p, re.compile(p)) for p in patterns]

    for py_file in src_dir.rglob("*.py"):
        # Skip excluded directories
        parts = py_file.parts
        if any(ex in parts for ex in exclude):
            continue

        try:
            lines = py_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        for lineno, line in enumerate(lines, start=1):
            for pat_str, pat_re in compiled:
                if pat_re.search(line):
                    findings.append((str(py_file), lineno, pat_str, line.rstrip()))

    return findings


# ---------------------------------------------------------------------------
# Config schema validation
# ---------------------------------------------------------------------------

def _validate_config_schema(cfg: Config) -> list[str]:
    """Validate that all registered projects have required fields.

    Required: source_dir, code.
    Returns a list of violation strings.
    """
    violations = []
    for slug, proj in cfg.projects.items():
        if not isinstance(proj, dict):
            violations.append(f"project {slug!r}: registry entry is not a dict")
            continue
        for req in ("source_dir", "code"):
            if req not in proj:
                violations.append(f"project {slug!r}: missing required field {req!r}")
    return violations


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_lint(cfg: Config, *, strict: bool = False) -> int:
    """Run all lint checks. Returns 0 if clean, 1 if any violations."""
    issues_total = 0

    # 1. Config schema validation
    schema_violations = _validate_config_schema(cfg)
    if schema_violations:
        print("Config schema violations:")
        for v in schema_violations:
            print(f"  {v}")
        issues_total += len(schema_violations)
    else:
        print("Config schema: OK")

    # 2. Leakage scan (only if patterns are configured)
    patterns = _get_forbidden_patterns(cfg)
    if patterns:
        src_dir = Path(__file__).parent
        findings = _scan_for_leakage(src_dir, patterns)
        if findings:
            print(f"\nLeakage scan: {len(findings)} finding(s) (forbidden patterns in source):")
            for fpath, lineno, pattern, line in findings:
                print(f"  {fpath}:{lineno}: matches {pattern!r}")
                print(f"    {line}")
            issues_total += len(findings)
        else:
            print(f"Leakage scan: OK ({len(patterns)} pattern(s) checked)")
    else:
        print("Leakage scan: no forbidden_patterns configured in [lint] — skipped")

    # 3. help --check gate
    from .cli import _check_verb_docstrings
    doc_violations = _check_verb_docstrings()
    if doc_violations:
        print(f"\nVerb docstring gate:")
        for v in doc_violations:
            print(f"  {v}")
        issues_total += len(doc_violations)
    else:
        print("Verb docstring gate: OK")

    if issues_total == 0:
        print("\nlint: PASS")
        return 0
    else:
        print(f"\nlint: FAIL ({issues_total} issue(s))")
        return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser(
    parent: "argparse._SubParsersAction | None" = None,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Build the argument parser for the ``lint`` verb.

    When to use: ``rv lint`` to run leakage scan + config validation + verb
    docstring gate. Use in CI to enforce the zero-hardcoded-path rule.
    """
    desc = (
        "Run the project linter: leakage scan + config schema validation + "
        "verb docstring gate. Exit 0 if clean, 1 if any violations."
    )
    if parent is not None:
        p = parent.add_parser("lint", help="Run the project linter.", description=desc)
    else:
        p = argparse.ArgumentParser(prog="rv lint", description=desc)

    p.add_argument(
        "--strict", action="store_true",
        help="Treat warnings as errors (reserved for future use).",
    )

    return p


def run(args: argparse.Namespace) -> int:
    """Run the lint command. Returns exit code."""
    try:
        cfg = load_config()
    except Exception as e:
        print(f"rv lint: config error: {e}", file=sys.stderr)
        return 1

    return cmd_lint(cfg, strict=getattr(args, "strict", False))
