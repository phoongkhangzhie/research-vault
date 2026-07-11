"""test_sr_lr_polish_s2.py — review-loop polish Slice 2: F14 coverage-gate arg-order fix.

Acceptance: a regression test greps every review-emitted label/help/note-body string
and asserts NONE contains 'review expand <project>' (the wrong-order signature).

Wrong:  rv review expand <project> <scope>
Right:  rv review <project> expand <scope>
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _all_review_strings(cfg) -> list[str]:
    """Collect all strings emitted by review (labels, note bodies, help text).

    Covers:
    1. Phase-1 manifest node labels (coverage-gate label)
    2. OKF review note body written by cmd_new
    3. Parser description strings in verbs.py (verbs.build_parser)
    """
    from research_vault.review import cmd_new
    from research_vault.review.verbs import build_parser

    note_path, review_dir, manifest = cmd_new(
        "demo-research",
        "scope-argorder",
        question="Test arg order",
        config=cfg,
    )

    strings: list[str] = []

    # 1. All manifest node labels and specs
    for node in manifest.get("nodes", []):
        strings.append(node.get("label", ""))
        strings.append(node.get("spec", ""))

    # 2. OKF note body
    strings.append(note_path.read_text(encoding="utf-8"))

    # 3. Parser description and subparser descriptions
    p = build_parser()
    strings.append(p.description or "")
    # Walk subparsers (only _SubParsersAction has _name_parser_map)
    for action in p._actions:
        if hasattr(action, "_name_parser_map"):
            for subname, subparser in action._name_parser_map.items():
                strings.append(subparser.description or "")
                for sub_action in subparser._actions:
                    strings.append(sub_action.help or "")

    return strings


# ---------------------------------------------------------------------------
# Regression: wrong-order pattern must be absent
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_instance):
    from research_vault.config import load_config
    return load_config(reload=True)


def test_no_wrong_order_review_expand_project(cfg):
    """No emitted string line may contain 'review expand <project>' (wrong arg order).

    Wrong form: rv review expand <project> <scope>
    Right form: rv review <project> expand <scope>

    Check is per-line: a single string with both "review expand" and "<project>"
    on DIFFERENT lines is valid (e.g. a note body with a 'rv review new <project>'
    line and a separate 'rv review expand ...' line).
    """
    strings = _all_review_strings(cfg)
    violations: list[str] = []
    for s in strings:
        for line in s.splitlines():
            # Detect the wrong pattern on a SINGLE line
            if "review expand <project>" in line:
                violations.append(repr(line[:120]))

    assert not violations, (
        "Found wrong arg-order 'rv review expand <project>' in emitted strings:\n"
        + "\n".join(violations)
    )


def test_no_stale_expand_instruction_in_coverage_gate_label(cfg):
    """Coverage-gate label must not instruct a hand-run 'expand' (D1: verb
    consolidation removed it — Phase-2 auto-emits on GO)."""
    from research_vault.review import cmd_new
    _, _, manifest = cmd_new(
        "demo-research",
        "scope-correct-order",
        question="Check correct order",
        config=cfg,
    )
    gate = next(n for n in manifest["nodes"] if n["id"] == "coverage-gate")
    label = gate.get("label", "")
    # Neither arg-order variant of a hand-run expand instruction may appear.
    assert "rv review expand <project>" not in label
    assert "rv review <project> expand <scope>" not in label
    # The label must instead say Phase-2 auto-emits.
    assert "auto-emit" in label.lower(), (
        f"coverage-gate label must state Phase-2 auto-emits; got: {label!r}"
    )


def test_no_stale_expand_instruction_in_note_body(cfg):
    """OKF note body must not instruct a hand-run 'expand' (D1: verb
    consolidation removed it — Phase-2 auto-emits on coverage-gate GO)."""
    from research_vault.review import cmd_new
    note_path, _, _ = cmd_new(
        "demo-research",
        "scope-note-order",
        question="Check note order",
        config=cfg,
    )
    body = note_path.read_text(encoding="utf-8")
    # Neither arg-order variant of a hand-run expand instruction may appear.
    assert "rv review expand <project>" not in body
    assert "rv review <project> expand <scope>" not in body
    # The note must instead say Phase-2 auto-emits.
    assert "auto-emit" in body.lower(), (
        f"OKF note body must state Phase-2 auto-emits; excerpt:\n{body[:500]}"
    )


def test_no_stale_expand_instruction_in_parser_description(cfg):
    """review verbs parser description must not instruct a hand-run 'expand'
    (D1: verb consolidation removed it — Phase-2 auto-emits on GO)."""
    from research_vault.review.verbs import build_parser
    p = build_parser()
    full_text = p.description or ""
    # Neither arg-order variant of a hand-run expand instruction may appear.
    assert "review expand <project>" not in full_text
    assert "review <project> expand <scope>" not in full_text
    # The description must instead say Phase-2 auto-emits.
    assert "auto-emit" in full_text.lower(), (
        f"Parser description must state Phase-2 auto-emits; got:\n{full_text[:300]}"
    )
