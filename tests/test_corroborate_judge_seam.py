"""test_corroborate_judge_seam.py — Slice 6 judge-seam acceptance tests (SR-XPB).

Acceptance criteria:
  - The corroborate-judge-fragment.json is valid JSON with the required 4-node structure.
  - The judge node's static reads: field references the candidates artifact
    (structural check on the template JSON; not a live rv dag brief execution).
  - A findings note carrying corroborated_by: frontmatter validates against OKF_TYPES.
  - The assert node's reads: reference the judgment report, NOT the raw candidates
    (proves the assert-from-rank anti-pattern is structurally prevented).
  - The judge spec guidance requires a reason for each rejection.

Zero new walker/dispatch mechanism — reuses existing schema/brief machinery.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_vault.config import Config, reset_config_cache
from research_vault.note import OKF_TYPES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_cfg():
    reset_config_cache()
    yield
    reset_config_cache()


FRAGMENT_PATH = (
    Path(__file__).parent.parent
    / "src/research_vault/data/examples/demo-litreview/corroborate-judge-fragment.json"
)


# ---------------------------------------------------------------------------
# Fragment structural validity
# ---------------------------------------------------------------------------

def test_fragment_is_valid_json() -> None:
    """corroborate-judge-fragment.json is valid JSON."""
    assert FRAGMENT_PATH.exists(), f"Fragment not found at {FRAGMENT_PATH}"
    data = json.loads(FRAGMENT_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


def test_fragment_has_four_nodes() -> None:
    """Fragment has the four required nodes: corroborate, judge, human-go, assert."""
    data = json.loads(FRAGMENT_PATH.read_text(encoding="utf-8"))
    node_ids = {n["id"] for n in data.get("nodes", [])}
    assert "corroborate" in node_ids
    assert "judge-corroboration" in node_ids
    assert "human-go-corroboration" in node_ids
    assert "assert-corroboration" in node_ids


def test_fragment_judge_node_has_reads_pointing_to_candidates() -> None:
    """The judge node's reads: includes the candidates artifact."""
    data = json.loads(FRAGMENT_PATH.read_text(encoding="utf-8"))
    nodes_by_id = {n["id"]: n for n in data.get("nodes", [])}
    judge = nodes_by_id["judge-corroboration"]
    reads = judge.get("reads", [])
    # Must reference the candidates JSON
    reads_str = json.dumps(reads)
    assert "corroboration-candidates.json" in reads_str, (
        f"Judge node reads: must include candidates artifact. Got: {reads!r}"
    )


def test_fragment_assert_node_requires_human_go_upstream() -> None:
    """The assert node must depend on the human-go gate (crew-cannot-self-approve)."""
    data = json.loads(FRAGMENT_PATH.read_text(encoding="utf-8"))
    nodes_by_id = {n["id"]: n for n in data.get("nodes", [])}
    assert_node = nodes_by_id["assert-corroboration"]
    deps = {n["from"] for n in assert_node.get("needs", [])}
    assert "human-go-corroboration" in deps, (
        f"assert-corroboration must depend on the human-go gate. Got needs: {deps!r}"
    )


def test_fragment_corroborate_node_produces_candidates_artifact() -> None:
    """Corroborate node declares a produces: pointing to the candidates artifact."""
    data = json.loads(FRAGMENT_PATH.read_text(encoding="utf-8"))
    nodes_by_id = {n["id"]: n for n in data.get("nodes", [])}
    corr_node = nodes_by_id["corroborate"]
    produces_str = json.dumps(corr_node.get("produces", {}))
    assert "corroboration-candidates" in produces_str, (
        f"Corroborate node must declare a produces: for the candidates artifact. "
        f"Got: {corr_node.get('produces')!r}"
    )


def test_fragment_human_go_node_type() -> None:
    """The human-go-corroboration node has type 'human-go'."""
    data = json.loads(FRAGMENT_PATH.read_text(encoding="utf-8"))
    nodes_by_id = {n["id"]: n for n in data.get("nodes", [])}
    hg = nodes_by_id["human-go-corroboration"]
    assert hg["type"] == "human-go"


# ---------------------------------------------------------------------------
# Findings note with corroborated_by: frontmatter validates as OKF type
# ---------------------------------------------------------------------------

def test_findings_note_with_corroborated_by_is_valid_okf_type(tmp_path: Path) -> None:
    """A findings note carrying corroborated_by: frontmatter validates as OKF type."""
    from research_vault.note import _parse_frontmatter

    note_text = """\
---
type: findings
title: Cross-Project Corroborated Finding
corroborated_by:
  - "@peer-project:findings/their-finding.md:Key Finding"
---

## Finding

Language models exhibit scaling behavior consistent with our observations.
This finding is corroborated by peer-project:findings/their-finding.md.
"""
    fields, body = _parse_frontmatter(note_text)
    okf_type = fields.get("type", "")
    assert okf_type in OKF_TYPES, (
        f"findings note type {okf_type!r} must be in OKF_TYPES. "
        f"OKF_TYPES = {sorted(OKF_TYPES)}"
    )
    assert fields.get("title"), "findings note must have a title"


# ---------------------------------------------------------------------------
# Anti-pattern: assert-from-rank-alone proof
# ---------------------------------------------------------------------------

def test_assert_node_spec_references_judgment_not_candidates() -> None:
    """The assert node reads the judgment report, NOT the raw candidates directly.

    This proves the assert-from-rank-alone anti-pattern is structurally prevented:
    the assert node consumes the judge's verdict (judgment.md), not the raw
    ranked candidates.  Rank narrows; judge confirms; human reviews; assert.
    """
    data = json.loads(FRAGMENT_PATH.read_text(encoding="utf-8"))
    nodes_by_id = {n["id"]: n for n in data.get("nodes", [])}
    assert_node = nodes_by_id["assert-corroboration"]
    reads_str = json.dumps(assert_node.get("reads", []))

    # Must read the judgment output
    assert "judgment" in reads_str.lower(), (
        f"assert-corroboration must read the judgment report. Got reads: {reads_str!r}"
    )
    # Must NOT read the raw candidates directly (assert-from-rank prevention)
    assert "corroboration-candidates.json" not in reads_str, (
        f"assert-corroboration must NOT read raw candidates (assert-from-rank anti-pattern). "
        f"It must read the judge's judgment report. Got reads: {reads_str!r}"
    )


def test_judge_spec_guidance_requires_rejection_reason() -> None:
    """The judge node's spec guidance requires recording a reason for each rejection."""
    data = json.loads(FRAGMENT_PATH.read_text(encoding="utf-8"))
    nodes_by_id = {n["id"]: n for n in data.get("nodes", [])}
    judge = nodes_by_id["judge-corroboration"]
    guidance = json.dumps(judge.get("_spec_guidance", []))
    assert "reason" in guidance.lower(), (
        "Judge spec guidance must instruct the agent to record a reason for each rejection. "
        f"Got _spec_guidance: {judge.get('_spec_guidance')!r}"
    )
    assert "reject" in guidance.lower(), (
        "Judge spec guidance must mention rejection. "
        f"Got _spec_guidance: {judge.get('_spec_guidance')!r}"
    )
