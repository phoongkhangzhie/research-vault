"""lint.py — leakage gate and config linter for Research Vault.

When to use: ``rv lint [--strict]`` to run the project linter. Checks:
  1. Leakage scan: greps src/ for private codenames / paths that should not
     be hardcoded. The list of forbidden patterns is config-driven (from
     ``lint.forbidden_patterns`` in research_vault.toml) — no compiled-in names.
  2. Config schema validation: verifies all registered projects have required
     fields (source_dir, code).
  3. Zero-hardcoded-path rule: confirms no absolute paths to private home
     directories appear in the source tree.
  4. Vacuous-assertion rule (SR-LINT): flags ``assert True`` / ``or True``
     in test files — tautological assertions always pass, masking bugs.
  5. Unpinned-git-init rule (SR-LINT): flags ``git init`` WITHOUT
     ``--initial-branch`` in test files — an unpinned branch passes locally
     but fails on master-default CI runners.
  6. Redefined-in-same-scope rule (F811): flags ``def``/``async def``/``class``
     names that are shadowed in the same statement-list — a silent dead-code
     bug (duplicate ``check_manuscript`` shipped through SR-MS-2).
     Exempts ``@overload`` / ``@typing.overload`` chains, ``@property`` /
     ``@x.setter`` / ``@x.deleter`` / ``@x.getter`` pairs, and
     ``@singledispatch`` / ``@fn.register`` chains — all standard same-name
     idioms.  Recurses into control-flow block bodies (if/for/while/with/try)
     so in-branch duplicates are caught; try/except split-branch definitions
     remain naturally exempt.  Scans src/research_vault/ (production code only).
  7. Getsource-guard smell (SR-LINT): flags two forms in test files —
     (a) DIRECT: ``assert "X" in inspect.getsource(fn)`` (or bare ``getsource``);
     (b) INDIRECTED: ``src = inspect.getsource(fn); assert "X" in src``
     (intra-function taint: a name directly assigned from a getsource call,
     later used as the RHS of a positive ``in`` comparison inside an assert
     in the same function scope).
     Both forms are smells because getsource returns comments and docstrings
     as well as live code — the assertion may pass even when the live code is
     broken (the symbol survives in a comment).

All path resolution goes through Config — zero hardcoded paths or codenames.
Stdlib only.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Any

from .config import Config, load_config


# ---------------------------------------------------------------------------
# Test-hygiene rules — module-level state for monkeypatching in tests
# ---------------------------------------------------------------------------

# Repository root (two levels up from src/research_vault/lint.py).
_FRAMEWORK_ROOT: Path = Path(__file__).parent.parent.parent
# Default tests directory; monkeypatched by integration tests.
_TESTS_DIR: Path = _FRAMEWORK_ROOT / "tests"
# Default source directory for F811 scan; monkeypatched by integration tests.
_SRC_DIR: Path = _FRAMEWORK_ROOT / "src" / "research_vault"

# Vacuous-assertion patterns (rule 4 / SR-LINT).
# Each entry is (compiled_pattern, human_label).
_VACUOUS_ASSERT_PATS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bassert\s+True\b"), "assert True"),
    (re.compile(r"\bor\s+True\b"), "or True"),
]

# Unpinned-git-init pattern (rule 5 / SR-LINT).
# Matches ["git", "init" as a list literal with init as the immediate subcommand.
# The tight form `"git",\s*"init"` avoids matching commit messages like
# `["git", "-C", repo, "commit", "-m", "init"]` where "init" follows "-m".
_GIT_INIT_PAT: re.Pattern[str] = re.compile(r'"git",\s*"init"')
_INITIAL_BRANCH_PAT: re.Pattern[str] = re.compile(r"--initial-branch")


# ---------------------------------------------------------------------------
# Test-hygiene helpers (SR-LINT)
# ---------------------------------------------------------------------------

def _get_test_hygiene_skip_files(cfg: Config) -> frozenset[str]:
    """Return basenames of test files to skip in test-hygiene scans.

    Config key: ``lint.test_hygiene_skip_files`` (list of basename strings).
    Defaults include ``test_lint_rules.py`` (the self-test file that plants
    the very patterns the rules detect — analogous to test_leakage_scan.py
    being self-excluded from the leakage scan).
    """
    raw = cfg._raw.get("lint", {})
    if not isinstance(raw, dict):
        configured: list[str] = []
    else:
        configured = list(raw.get("test_hygiene_skip_files", []))
    # Always exclude the self-test file so its planted patterns don't
    # false-positive when rv lint runs repo-wide.
    defaults = ["test_lint_rules.py"]
    return frozenset(defaults + configured)


def _collect_test_files(
    tests_dir: Path,
    *,
    skip_files: frozenset[str] | None = None,
) -> list[Path]:
    """Return all .py files under tests_dir, excluding skip_files by basename."""
    if not tests_dir.exists():
        return []
    skip = skip_files or frozenset()
    return [
        f
        for f in tests_dir.rglob("*.py")
        if "__pycache__" not in f.parts and f.name not in skip
    ]


def check_vacuous_assertions(
    files: list[Path],
) -> list[tuple[str, int, str, str]]:
    """Scan *files* for vacuous assertions.

    Flags:
    - ``assert True`` — unconditionally passes; never catches a bug.
    - ``or True``     — short-circuits any expression to True;
                        ``assert expr or True`` always passes.

    Returns a list of ``(file_path, lineno, label, matching_line)`` tuples.
    Each matched line is reported at most once (the first matching pattern wins).
    """
    findings: list[tuple[str, int, str, str]] = []
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, start=1):
            for pat, label in _VACUOUS_ASSERT_PATS:
                if pat.search(line):
                    findings.append((str(f), lineno, label, line.rstrip()))
                    break  # report each line at most once
    return findings


def check_unpinned_git_init(
    files: list[Path],
) -> list[tuple[str, int, str]]:
    """Scan *files* for ``git init`` calls that omit ``--initial-branch``.

    An unpinned initial branch passes locally when ``init.defaultBranch=main``
    but fails on CI runners that default to ``master``.  The fix is always
    ``["git", "init", "--initial-branch=main", ...]`` (or the space-separated
    ``"--initial-branch", branch`` form).

    Matches the list-literal form ``"git",`` immediately followed by ``"init"``
    (with optional whitespace) to avoid false-positives on commit messages like
    ``["git", "-C", repo, "commit", "-m", "init"]``.

    Returns a list of ``(file_path, lineno, matching_line)`` tuples.
    """
    findings: list[tuple[str, int, str]] = []
    for f in files:
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, start=1):
            if _GIT_INIT_PAT.search(line) and not _INITIAL_BRANCH_PAT.search(line):
                findings.append((str(f), lineno, line.rstrip()))
    return findings


# ---------------------------------------------------------------------------
# Getsource-guard smell (AST-based, rule 7 / SR-LINT)
# ---------------------------------------------------------------------------

def _is_getsource_call(node: ast.expr) -> bool:
    """Return True if *node* is a call to ``inspect.getsource`` or bare ``getsource``."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id == "getsource":
        return True
    if isinstance(func, ast.Attribute) and func.attr == "getsource":
        return True
    return False


def _assert_contains_getsource_in(assert_node: ast.Assert) -> bool:
    """Return True if *assert_node* contains an ``X in getsource(...)`` comparison."""
    for node in ast.walk(assert_node.test):
        if not isinstance(node, ast.Compare):
            continue
        for op, comparator in zip(node.ops, node.comparators):
            if isinstance(op, ast.In) and _is_getsource_call(comparator):
                return True
    return False


def _assert_contains_tainted_in(
    assert_node: ast.Assert,
    tainted: set[str],
) -> bool:
    """Return True if *assert_node* has an ``X in <tainted_name>`` comparison.

    Scans the assert's test sub-tree for ``Compare`` nodes where the operator
    is ``In`` (positive containment, not ``NotIn``) and the comparator is a
    ``Name`` that belongs to *tainted*.
    """
    for node in ast.walk(assert_node.test):
        if not isinstance(node, ast.Compare):
            continue
        for op, comparator in zip(node.ops, node.comparators):
            if isinstance(op, ast.In) and isinstance(comparator, ast.Name):
                if comparator.id in tainted:
                    return True
    return False


def _collect_fn_scope_taint_and_asserts(
    stmts: list[ast.stmt],
    tainted: set[str],
    assert_nodes: list[ast.Assert],
) -> None:
    """Walk *stmts* collecting getsource-tainted names and ``Assert`` nodes.

    A name is tainted if it is **directly** assigned from a getsource call —
    i.e. ``name = inspect.getsource(fn)`` or ``name = getsource(fn)``.  Once
    tainted, the name remains tainted for the remainder of the scan (even if
    later reassigned).  This is conservative: well-written AST-based rewrites
    use a *different* variable for the final assertion, so no false positives.

    Does NOT recurse into nested ``FunctionDef`` / ``AsyncFunctionDef`` /
    ``ClassDef`` bodies — those are separate scopes with their own taint sets,
    handled by the outer ``ast.walk`` in ``check_getsource_guard``.

    Recurses into compound statement sub-bodies (if/for/while/with/try/match)
    via :func:`_get_compound_bodies` so in-body indirect assignments are caught.
    """
    for stmt in stmts:
        # Separate scopes — don't bleed taint into/from nested defs/classes.
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(stmt, ast.Assign) and _is_getsource_call(stmt.value):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    tainted.add(target.id)
        if isinstance(stmt, ast.Assert):
            assert_nodes.append(stmt)
        # Recurse into compound statement sub-bodies (forward ref to
        # _get_compound_bodies is fine: Python resolves at call time).
        for body_list in _get_compound_bodies(stmt):
            _collect_fn_scope_taint_and_asserts(body_list, tainted, assert_nodes)


def check_getsource_guard(
    files: list[Path],
) -> list[tuple[str, int, str]]:
    """Scan *files* for the getsource-guard smell (rule 7 / SR-LINT).

    Detects two forms:

    **Direct form** — ``assert "X" in inspect.getsource(fn)`` (or bare
    ``getsource``): the getsource call is the comparator inline in the assert.

    **Indirected form** — intra-function taint:
    ``src = inspect.getsource(fn); …; assert "X" in src``.  The name assigned
    directly from a getsource call is later used as the RHS of a positive ``in``
    comparison inside an assert in the *same function scope*.

    Both forms are smells: ``inspect.getsource`` returns comments and docstrings
    as well as live code, so the assertion may pass even when the guarded code
    path is dead — the asserted string may survive in a comment.

    This is a **smell flag**, not a proof of vacuity.  The rule reports the
    assert location and suggests the fix: assert the bad pattern is *absent*
    (``not in``), or use AST-based inspection (``ast.get_source_segment`` on
    a specific node) which is comment-free by construction.

    Returns a list of ``(file_path, lineno, matching_line)`` tuples.
    Files with SyntaxErrors are skipped gracefully.
    """
    findings: list[tuple[str, int, str]] = []
    for f in files:
        try:
            src = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            tree = ast.parse(src, filename=str(f))
        except SyntaxError:
            continue
        lines = src.splitlines()

        # ── Direct form: assert X in getsource(fn) ───────────────────────────
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert) and _assert_contains_getsource_in(node):
                lineno = node.lineno
                line_text = lines[lineno - 1].rstrip() if lineno <= len(lines) else ""
                findings.append((str(f), lineno, line_text))

        # ── Indirected form: src = getsource(fn); assert X in src ────────────
        # Walk each function scope independently so taint doesn't cross
        # function boundaries.
        for fn_node in ast.walk(tree):
            if not isinstance(fn_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            tainted: set[str] = set()
            assert_nodes: list[ast.Assert] = []
            _collect_fn_scope_taint_and_asserts(fn_node.body, tainted, assert_nodes)
            if not tainted:
                continue
            for assert_node in assert_nodes:
                if _assert_contains_tainted_in(assert_node, tainted):
                    lineno = assert_node.lineno
                    line_text = (
                        lines[lineno - 1].rstrip() if lineno <= len(lines) else ""
                    )
                    # Avoid duplicating a finding already reported by the direct form.
                    entry = (str(f), lineno, line_text)
                    if entry not in findings:
                        findings.append(entry)

    return findings


# ---------------------------------------------------------------------------
# F811 — redefined-in-same-scope (AST-based, rule 6)
# ---------------------------------------------------------------------------

def _is_exempt_decorated(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Return True if *node* carries an exemption decorator for same-name idioms.

    Exempted decorator patterns (none of these are bugs when names repeat):

    * ``@overload`` / ``@typing.overload`` — typing overload stubs.
    * ``@property`` — bare-name form (``ast.Name``).
    * ``@x.setter`` / ``@x.deleter`` / ``@x.getter`` — property descriptors
      (``ast.Attribute`` with attr in {setter, deleter, getter}).
    * ``@singledispatch`` — bare-name form (``ast.Name``).
    * ``@functools.singledispatch`` — attribute form (``ast.Attribute``).
    * ``@fn.register`` — singledispatch implementation registration
      (``ast.Attribute`` with attr == "register").

    Both the bare-name form and the dotted attribute form are recognised for
    each pattern.
    """
    _EXEMPT_NAMES = frozenset({"overload", "property", "singledispatch"})
    _EXEMPT_ATTRS = frozenset({"overload", "setter", "deleter", "getter",
                               "singledispatch", "register"})
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name) and dec.id in _EXEMPT_NAMES:
            return True
        if isinstance(dec, ast.Attribute) and dec.attr in _EXEMPT_ATTRS:
            return True
    return False


def _get_compound_bodies(node: ast.stmt) -> list[list[ast.stmt]]:
    """Return each immediate sub-statement-list of a compound statement.

    Each returned list is checked **independently** for within-list duplicates.
    This preserves the natural exemption for split-branch definitions (e.g.
    try/except, if/else, match/case): the two bodies are separate lists and
    are never compared against each other.

    Covered compound statements:
      - ``if`` / ``else`` — body and orelse are separate lists.
      - ``for`` / ``while`` / ``async for`` — body and (optional) orelse.
      - ``with`` / ``async with`` — body only.
      - ``try`` — body, each handler body, orelse, finalbody (all separate).
      - ``TryStar`` (Python 3.11+ ExceptGroup) — same as try.
      - ``match`` (Python 3.10+) — each ``case`` body is its own list, so
        duplicates within one case arm are flagged but the same name in two
        different case arms is NOT flagged (different statement-lists).

    ``FunctionDef`` and ``ClassDef`` bodies are NOT returned here — those are
    separate scopes already handled by the outer ``ast.walk`` in
    ``check_redefined_while_unused`` and ``_collect_fn_scope_taint_and_asserts``.
    """
    if isinstance(node, ast.If):
        result: list[list[ast.stmt]] = [node.body]
        if node.orelse:
            result.append(node.orelse)
        return result
    if isinstance(node, (ast.For, ast.While, ast.AsyncFor)):
        result = [node.body]
        if node.orelse:
            result.append(node.orelse)
        return result
    if isinstance(node, (ast.With, ast.AsyncWith)):
        return [node.body]
    if isinstance(node, ast.Try):
        result = [node.body]
        for handler in node.handlers:
            result.append(handler.body)
        if node.orelse:
            result.append(node.orelse)
        if node.finalbody:
            result.append(node.finalbody)
        return result
    # Python 3.11+ TryStar (ExceptGroup)
    if hasattr(ast, "TryStar") and isinstance(node, ast.TryStar):  # type: ignore[attr-defined]
        result = [node.body]
        for handler in node.handlers:
            result.append(handler.body)
        if node.orelse:
            result.append(node.orelse)
        if node.finalbody:
            result.append(node.finalbody)
        return result
    # Python 3.10+ match/case — each case body is a separate statement-list.
    # Duplicates within one case arm are flagged; the same name in two different
    # case arms is NOT flagged (different lists, like if/else branches).
    if hasattr(ast, "Match") and isinstance(node, ast.Match):  # type: ignore[attr-defined]
        return [case.body for case in node.cases]
    return []


def _check_scope_for_f811(
    stmts: list[ast.stmt],
    scope_name: str,
    filepath: str,
    findings: list[tuple[str, int, str, int, str]],
) -> None:
    """Walk one statement-list and append any F811 finding to *findings*.

    Tracks ``def`` / ``async def`` / ``class`` names in *stmts*.  When the same
    name appears a second time in the same statement-list, that is a
    redefined-in-same-scope violation — the first definition is dead code.

    Exemptions: if either the previous OR the current definition is decorated
    with any exemption recognised by ``_is_exempt_decorated`` (overload, property
    descriptors, singledispatch/register), the pair is skipped.

    Compound statement bodies (if/for/while/with/try) are recursed into so that
    in-branch duplicates are caught.  Each branch body is a separate list, so
    try/except split-branch definitions remain naturally exempt.
    """
    seen: dict[str, tuple[int, bool]] = {}  # name -> (first_lineno, is_exempt)
    for node in stmts:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name
            is_ex = _is_exempt_decorated(node)
            if name in seen:
                prev_lineno, prev_is_ex = seen[name]
                if not (is_ex or prev_is_ex):
                    findings.append(
                        (filepath, node.lineno, name, prev_lineno, scope_name)
                    )
            seen[name] = (node.lineno, is_ex)
        elif isinstance(node, ast.ClassDef):
            name = node.name
            if name in seen:
                prev_lineno, _ = seen[name]
                findings.append(
                    (filepath, node.lineno, name, prev_lineno, scope_name)
                )
            seen[name] = (node.lineno, False)
        else:
            # Recurse into each branch body of compound statements separately.
            # A fresh `seen` dict is used for each recursive call, so only
            # within-list duplicates are flagged — never cross-branch ones.
            for body in _get_compound_bodies(node):
                _check_scope_for_f811(body, scope_name, filepath, findings)


def check_redefined_while_unused(
    files: list[Path],
) -> list[tuple[str, int, str, int, str]]:
    """Scan *files* for F811 — redefined-in-same-scope ``def``/``class`` names.

    For each file, walks every scope (module body, function body, class body) and
    flags any ``def`` / ``async def`` / ``class`` name that is shadowed within the
    same statement-list.  A shadowed definition is dead code — the first definition
    is unreachable.  Note: the check is statement-list membership, not use-before-
    redefine; the rule name "F811" is used for compatibility with the Flake8 code.

    The motivating bug: a duplicate ``check_manuscript`` function shipped in
    SR-MS-2 because ``rv lint`` had no AST-level scope check, and ``CI green``
    was recorded without this gate.

    Exemptions (never flagged):
    - Functions decorated with ``@overload`` / ``@typing.overload``.
    - Functions decorated with ``@property`` / ``@x.setter`` / ``@x.deleter`` /
      ``@x.getter`` — standard property descriptor pattern.
    - Functions decorated with ``@singledispatch`` / ``@functools.singledispatch``
      / ``@fn.register`` — standard functools dispatch pattern.
    - Definitions in different branches of a ``try/except`` block (naturally
      excluded — they live in different statement-lists).
    - Definitions in different branches of an ``if/else`` block (same reason).

    Returns a list of ``(file_path, lineno, name, prev_lineno, scope_name)``
    tuples, one per violation.  ``lineno`` is the *second* (shadow) definition;
    ``prev_lineno`` is the first.
    """
    findings: list[tuple[str, int, str, int, str]] = []

    for f in files:
        try:
            src = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            tree = ast.parse(src, filename=str(f))
        except SyntaxError:
            continue

        # Module scope
        _check_scope_for_f811(tree.body, "<module>", str(f), findings)

        # Every nested scope (function bodies, class bodies, async functions)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _check_scope_for_f811(
                    node.body, f"{node.name}()", str(f), findings
                )
            elif isinstance(node, ast.ClassDef):
                _check_scope_for_f811(
                    node.body, node.name, str(f), findings
                )

    return findings


def _collect_src_files(
    src_dir: Path,
) -> list[Path]:
    """Return all .py files under *src_dir*, excluding __pycache__ trees."""
    if not src_dir.exists():
        return []
    return [
        f
        for f in src_dir.rglob("*.py")
        if "__pycache__" not in f.parts
    ]


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

    # 4. Test-hygiene rules (SR-LINT) — scoped to test files only
    skip_files = _get_test_hygiene_skip_files(cfg)
    test_files = _collect_test_files(_TESTS_DIR, skip_files=skip_files)

    # 4a. Vacuous-assertion rule
    va_findings = check_vacuous_assertions(test_files)
    if va_findings:
        print(f"\nVacuous-assertion rule: {len(va_findings)} finding(s) "
              f"(assert True / or True in test files):")
        for fpath, lineno, label, line in va_findings:
            print(f"  {fpath}:{lineno}: [{label}]")
            print(f"    {line}")
        issues_total += len(va_findings)
    else:
        n = len(test_files)
        print(f"Vacuous-assertion rule: OK ({n} test file(s) checked)")

    # 4b. Unpinned-git-init rule
    gi_findings = check_unpinned_git_init(test_files)
    if gi_findings:
        print(f"\nUnpinned-git-init rule: {len(gi_findings)} finding(s) "
              f"(git init without --initial-branch in test files):")
        for fpath, lineno, line in gi_findings:
            print(f"  {fpath}:{lineno}:")
            print(f"    {line}")
        issues_total += len(gi_findings)
    else:
        n = len(test_files)
        print(f"Unpinned-git-init rule: OK ({n} test file(s) checked)")

    # 4c. Getsource-guard smell (rule 7 / SR-LINT)
    gs_findings = check_getsource_guard(test_files)
    if gs_findings:
        print(
            f"\nGetsource-guard smell (rule 7): {len(gs_findings)} finding(s) "
            f"(assert X in getsource(fn) — passes even when live code is dead; "
            f"fix: assert the bad pattern is ABSENT, or strip comments via AST):"
        )
        for fpath, lineno, line in gs_findings:
            print(f"  {fpath}:{lineno}:")
            print(f"    {line}")
        issues_total += len(gs_findings)
    else:
        n = len(test_files)
        print(f"Getsource-guard smell (rule 7): OK ({n} test file(s) checked)")

    # 5. Redefined-in-same-scope rule (F811) — scoped to production src/
    src_files = _collect_src_files(_SRC_DIR)
    f811_findings = check_redefined_while_unused(src_files)
    if f811_findings:
        print(
            f"\nRedefined-in-same-scope rule (F811): {len(f811_findings)} finding(s) "
            f"(def/class name shadowed in same statement-list — first definition is dead code):"
        )
        for fpath, lineno, name, prev_lineno, scope in f811_findings:
            print(f"  {fpath}:{lineno}: [{scope}] {name!r} shadows definition at line {prev_lineno}")
        issues_total += len(f811_findings)
    else:
        n = len(src_files)
        print(f"Redefined-in-same-scope rule (F811): OK ({n} source file(s) checked)")

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
