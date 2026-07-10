# SPDX-License-Identifier: AGPL-3.0-or-later
"""tests/test_no_direct_api_judge.py — PR-F ★ D-F1 CI grep-guard.

Mechanically enforce Khang's rule going forward: EVERY judge/gate uses the
CC harness emit/ingest fan-out as its ONLY path. No module under
``gates/``, ``manuscript/``, or ``review/`` may reintroduce a hand-rolled
direct-API judge — a ``anthropic.Anthropic(`` client, the Anthropic Messages
endpoint URL, the deleted ``call_anthropic_messages`` helper, or a
judge-purpose ``RV_JUDGE_MODEL`` / ``ANTHROPIC_API_KEY`` env read.

This is the standing backstop against the exact class of drift this PR
deleted: a green-and-empty gate that silently reaches for a live model
instead of the cold fan-out. If this test goes RED, a direct-API judge path
crept back in — route it through ``gates.judge_seam`` / ``board_seam``
emit/ingest instead.

SCOPE (deliberately NARROW): the three judge/gate packages only. The
LEGITIMATE experiment-compute path (``adapters/model_client.py`` +
``experiment.py`` — ``ANTHROPIC_API_KEY`` resolved via the SecretStore for
keyed experiment inference) is OUTSIDE this scope and is NOT scanned — that
is the one place an Anthropic key legitimately flows (deliverable (d)).
"""
from __future__ import annotations

import re
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src" / "research_vault"
_SCANNED_DIRS = ("gates", "manuscript", "review")

# A judge is FORBIDDEN from being run via any of these in the scanned dirs.
# Each is a (compiled regex, human label).
_FORBIDDEN = [
    (re.compile(r"anthropic\.Anthropic\s*\("), "hand-rolled anthropic.Anthropic() client"),
    (re.compile(r"api\.anthropic\.com"), "the Anthropic Messages endpoint URL"),
    (re.compile(r"call_anthropic_messages"), "the deleted call_anthropic_messages helper"),
    (re.compile(r"litellm\.completion"), "a hand-rolled litellm.completion judge call"),
    # A judge-purpose env READ (os.environ / os.getenv / environ[...]) of the
    # judge-model or Anthropic key. The var name alone is not enough (a comment
    # could name it) — require an actual read expression.
    (
        re.compile(r"(?:os\.environ\.get|os\.getenv|os\.environ\[)\s*\(?\s*[\"'](?:RV_JUDGE_MODEL|ANTHROPIC_API_KEY)[\"']"),
        "a judge-purpose RV_JUDGE_MODEL/ANTHROPIC_API_KEY env read",
    ),
]


def _iter_scanned_py_files():
    for d in _SCANNED_DIRS:
        root = _SRC / d
        assert root.is_dir(), f"expected scan dir missing: {root}"
        yield from sorted(root.rglob("*.py"))


def test_no_direct_api_judge_path_in_gates_manuscript_review():
    """FAIL loudly if any judge/gate module reintroduces a direct-API judge."""
    violations: list[str] = []
    files = list(_iter_scanned_py_files())
    assert files, "grep-guard found no .py files to scan — wrong path?"

    for path in files:
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            for pat, label in _FORBIDDEN:
                if pat.search(line):
                    rel = path.relative_to(_SRC.parent.parent)
                    violations.append(f"{rel}:{lineno}: {label} -> {line.strip()}")

    assert not violations, (
        "PR-F D-F1: a direct-API judge path was reintroduced under "
        "gates/ | manuscript/ | review/. Every judge/gate MUST run via the "
        "harness emit/ingest cold fan-out (gates.judge_seam / board_seam), "
        "never a hand-rolled live-API call. Offenders:\n  "
        + "\n  ".join(violations)
    )


def test_gates_llm_module_stays_deleted():
    """The shared urllib judge-call module was deleted (PR-F); it must not
    return (a re-add would resurrect the whole direct-API judge path)."""
    assert not (_SRC / "gates" / "_llm.py").exists(), (
        "gates/_llm.py was deleted in PR-F (the shared direct-API judge call) "
        "— it must not come back. Route judges through the emit/ingest fan-out."
    )


def test_acceptance_b_literal_grep_is_empty():
    """Acceptance (b): the exact literal grep from the PR brief returns
    NOTHING over the three dirs (belt-and-suspenders over the regex guard —
    catches the literals even inside comments/docstrings)."""
    literals = ("api.anthropic.com", "call_anthropic_messages", "anthropic.Anthropic(")
    hits: list[str] = []
    for path in _iter_scanned_py_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for lit in literals:
                if lit in line:
                    hits.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not hits, "acceptance (b) literal grep is non-empty:\n  " + "\n  ".join(hits)
