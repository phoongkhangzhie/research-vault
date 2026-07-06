"""test_sr_lr_polish_s2.py — SR-LR-POLISH Slice 2: F14 coverage-gate arg-order fix.

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


def test_correct_order_present_in_coverage_gate_label(cfg):
    """Coverage-gate label must contain the correct-order expand command."""
    from research_vault.review import cmd_new
    _, _, manifest = cmd_new(
        "demo-research",
        "scope-correct-order",
        question="Check correct order",
        config=cfg,
    )
    gate = next(n for n in manifest["nodes"] if n["id"] == "coverage-gate")
    label = gate.get("label", "")
    # The correct form must be present
    assert "rv review <project> expand <scope>" in label, (
        f"coverage-gate label must use correct arg order; got: {label!r}"
    )


def test_correct_order_in_note_body(cfg):
    """OKF note body must use the correct-order expand command."""
    from research_vault.review import cmd_new
    note_path, _, _ = cmd_new(
        "demo-research",
        "scope-note-order",
        question="Check note order",
        config=cfg,
    )
    body = note_path.read_text(encoding="utf-8")
    # Wrong form absent
    assert "rv review expand <project>" not in body, (
        "OKF note body must not contain wrong-order 'rv review expand <project>'"
    )
    # Correct form present
    assert "rv review <project> expand <scope>" in body, (
        f"OKF note body must contain correct-order expand command; excerpt:\n{body[:500]}"
    )


def test_correct_order_in_parser_description(cfg):
    """review verbs parser description must use correct arg order for expand."""
    from research_vault.review.verbs import build_parser
    p = build_parser()
    full_text = p.description or ""
    # Wrong form absent
    assert "review expand <project>" not in full_text, (
        "Parser description must not contain wrong-order 'review expand <project>'"
    )
    # Correct form present
    assert "rv review <project> expand <scope>" in full_text or \
           "review <project> expand" in full_text, (
        f"Parser description must use correct arg order; got:\n{full_text[:300]}"
    )
