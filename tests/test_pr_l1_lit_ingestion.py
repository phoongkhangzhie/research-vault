"""test_pr_l1_lit_ingestion.py — PR-L1: the lit-review ingestion enrichment (§7.5).

Tests the upstream half of the manuscript loop's equation machinery: the
`relate-<key>` node's tip string (`per_paper_relate_tips`) is extended to
extract `key_equations:` (a criticality ledger + `## Key equations` body
block), `repo:`, and `artifacts:` — and the `literature` OKF scaffold carries
the three fields as OPTIONAL (backward-compatible, doi/arxiv_id precedent).

All hermetic (tmp_instance). No ~/vault reads or writes. No LLM calls — this
tests the tip PROSE (what the agent is instructed to do) and the note.py
SCAFFOLD/PARSE shape, not a live extraction run.
"""
from __future__ import annotations

import re

import pytest

from research_vault.config import load_config
from research_vault import note as note_mod
from research_vault.review.style import get_review_tips, REVIEW_TIPS_KEYS


@pytest.fixture
def cfg(tmp_instance):
    return load_config(reload=True)


# ---------------------------------------------------------------------------
# 1. per_paper_relate_tips — the extraction-instruction prose (teeth)
# ---------------------------------------------------------------------------

def test_relate_tips_instructs_equation_extraction():
    """per_paper_relate_tips must instruct extracting pivotal equations into
    a `## Key equations` body block + a `key_equations:` frontmatter ledger."""
    tips = get_review_tips()
    relate_tip = tips["per_paper_relate_tips"]

    assert "key_equations" in relate_tip, (
        "per_paper_relate_tips must mention the key_equations: frontmatter field"
    )
    assert "## Key equations" in relate_tip, (
        "per_paper_relate_tips must instruct a '## Key equations' body block"
    )
    assert "critical" in relate_tip.lower(), (
        "per_paper_relate_tips must instruct marking critical: true|false"
    )


def test_relate_tips_instructs_criticality_discipline():
    """The tip must state the criticality marking discipline: true only when
    the paper's central claim turns on the equation; default false."""
    tips = get_review_tips()
    relate_tip = tips["per_paper_relate_tips"]
    assert "central claim" in relate_tip.lower(), (
        "per_paper_relate_tips must state the critical:true marking discipline "
        "(only when the paper's central claim turns on the equation)"
    )


def test_relate_tips_instructs_repo_field():
    """per_paper_relate_tips must instruct discovering + recording repo:."""
    tips = get_review_tips()
    relate_tip = tips["per_paper_relate_tips"]
    assert "repo:" in relate_tip or "`repo`" in relate_tip, (
        "per_paper_relate_tips must mention the repo: frontmatter field"
    )


def test_relate_tips_instructs_artifacts_field():
    """per_paper_relate_tips must instruct recording artifacts: as label: url pointers."""
    tips = get_review_tips()
    relate_tip = tips["per_paper_relate_tips"]
    assert "artifacts:" in relate_tip or "`artifacts`" in relate_tip, (
        "per_paper_relate_tips must mention the artifacts: frontmatter field"
    )
    assert "label" in relate_tip.lower() and "url" in relate_tip.lower(), (
        "per_paper_relate_tips must specify the label: url pointer shape for artifacts:"
    )


def test_relate_tips_scopes_lean_no_acquisition_subsystem():
    """The tip must EXPLICITLY forbid downloading/cloning/fetching artifacts
    (D-MS-6 LEAN scope) — record-what-you-see, never acquire."""
    tips = get_review_tips()
    relate_tip = tips["per_paper_relate_tips"].lower()
    assert "record-what-you-see" in relate_tip or "record what you see" in relate_tip, (
        "per_paper_relate_tips must name the LEAN record-what-you-see discipline (D-MS-6)"
    )
    # The prohibition must be an explicit negation, not a bare instruction to acquire.
    assert re.search(r"do not\s+(clone|download|fetch)", relate_tip), (
        "per_paper_relate_tips must explicitly forbid acquisition verbs "
        "(clone/download/fetch) — LEAN scope, D-MS-6"
    )


def test_review_tips_keys_unchanged():
    """PR-L1 extends an existing tip's PROSE — it does not add a new DAG node
    or a new tips key. REVIEW_TIPS_KEYS must be unchanged (6 keys)."""
    assert REVIEW_TIPS_KEYS == frozenset({
        "review_scope_tips",
        "review_search_tips",
        "review_snowball_tips",
        "per_paper_relate_tips",
        "review_synthesize_tips",
        "review_critic_tips",
    })


# ---------------------------------------------------------------------------
# 2. note.py literature scaffold — the three optional fields
# ---------------------------------------------------------------------------

def test_literature_scaffold_carries_key_equations_field(cfg):
    path = note_mod.cmd_new("demo-research", "literature", "A math paper", config=cfg)
    content = path.read_text()
    assert "key_equations:" in content


def test_literature_scaffold_carries_repo_field(cfg):
    path = note_mod.cmd_new("demo-research", "literature", "A paper", config=cfg)
    content = path.read_text()
    assert "repo:" in content


def test_literature_scaffold_carries_artifacts_field(cfg):
    path = note_mod.cmd_new("demo-research", "literature", "A paper", config=cfg)
    content = path.read_text()
    assert "artifacts:" in content


def test_literature_scaffold_new_fields_are_optional_empty(cfg):
    """New fields scaffold as empty (unset) — mirrors the doi/arxiv_id precedent.
    Parsing them back must yield empty string, not a violation."""
    path = note_mod.cmd_new("demo-research", "literature", "A paper", config=cfg)
    content = path.read_text()
    fields, _ = note_mod._parse_frontmatter(content)
    assert fields.get("key_equations", None) == ""
    assert fields.get("repo", None) == ""
    assert fields.get("artifacts", None) == ""


def test_literature_scaffold_passes_check(cfg):
    """A freshly scaffolded literature note (fields empty) passes cmd_check
    unchanged — absence of key_equations/repo/artifacts is never a violation."""
    note_mod.cmd_new("demo-research", "literature", "A paper", config=cfg)
    violations = note_mod.cmd_check("demo-research", config=cfg)
    assert violations == []


# ---------------------------------------------------------------------------
# 3. Backward compat — a PRE-enrichment literature note (no new fields at all)
# ---------------------------------------------------------------------------

def test_pre_enrichment_literature_note_parses_and_checks_unchanged(cfg, tmp_instance):
    """An existing literature/ note written BEFORE PR-L1 (no key_equations/
    repo/artifacts fields at all) must parse fine and cmd_check-pass unchanged."""
    project_dir = tmp_instance / "projects" / "demo-research" / "literature"
    project_dir.mkdir(parents=True, exist_ok=True)
    legacy_note = project_dir / "legacy2020.md"
    legacy_note.write_text(
        "---\n"
        "type: literature\n"
        "title: A legacy paper\n"
        "citekey: legacy2020\n"
        "doi: 10.1234/legacy\n"
        "arxiv_id: \n"
        "claim: some claim\n"
        "---\n"
        "\n<!-- legacy body, no Key equations section -->\n",
        encoding="utf-8",
    )
    fields, body = note_mod._parse_frontmatter(legacy_note.read_text(encoding="utf-8"))
    assert fields.get("type") == "literature"
    assert "key_equations" not in fields  # absent entirely — not even empty
    assert "repo" not in fields
    assert "artifacts" not in fields

    violations = note_mod.cmd_check("demo-research", config=cfg)
    assert violations == []


# ---------------------------------------------------------------------------
# 4. The body <-> frontmatter round-trip (the delicate spot, per Wren)
# ---------------------------------------------------------------------------

# A minimal stand-in for the future manuscript-loop extractor (equations.py,
# PR-M4, NOT built here — this proves shape-compatibility only): pull
# `### [label] Title` headers out of a `## Key equations` body section.
_EQ_HEADER_RE = re.compile(r"^###\s+\[([^\]]+)\]", re.MULTILINE)


def _extract_body_equation_labels(body: str) -> list[str]:
    """Extract equation labels from a '## Key equations' body section.

    Stand-in for the future manuscript equations.py extractor — proves the
    relate-produced body shape is minable, without building that module here
    (out of PR-L1 scope).
    """
    m = re.search(r"^##\s+Key equations\s*$(.*?)(?=^##\s|\Z)", body, re.MULTILINE | re.DOTALL)
    if not m:
        return []
    section = m.group(1)
    return _EQ_HEADER_RE.findall(section)


def test_body_ledger_roundtrip_marked_critical_equation():
    """A relate-produced note: a '## Key equations' body block with a labeled
    equation, joined to a frontmatter `key_equations:` criticality ledger
    marking that label critical: true. The body label and the frontmatter
    label must match — the join a downstream consumer (equations.py, PR-M4)
    performs."""
    note_text = (
        "---\n"
        "type: literature\n"
        "title: A math paper\n"
        "citekey: kingma2013\n"
        "key_equations:\n"
        "  - label: eq:elbo\n"
        "    critical: true\n"
        "  - label: eq:kl\n"
        "    critical: false\n"
        "---\n"
        "\n"
        "## Key equations\n\n"
        "### [eq:elbo] Evidence lower bound  *(critical)*\n"
        "$$ \\log p(x) \\ge \\mathbb{E}_{q}[\\log p(x,z) - \\log q(z)] $$\n\n"
        "### [eq:kl] KL regularizer\n"
        "$$ D_{KL}(q(z) \\| p(z)) $$\n\n"
        "## Discussion\n\n"
        "Some prose that must not be swept into the equations section.\n"
    )
    fields, body = note_mod._parse_frontmatter(note_text)

    ledger = fields["key_equations"]
    assert isinstance(ledger, list)
    assert {"label": "eq:elbo", "critical": "true"} in ledger
    assert {"label": "eq:kl", "critical": "false"} in ledger

    body_labels = _extract_body_equation_labels(body)
    assert body_labels == ["eq:elbo", "eq:kl"]

    # The join: every ledger label resolves to a body block (shape-compat
    # with the future equations.py extractor).
    ledger_labels = {entry["label"] for entry in ledger}
    assert ledger_labels == set(body_labels)

    # The specific join equations.py needs: which body-present equations are
    # marked critical.
    critical_labels = {
        entry["label"] for entry in ledger if entry.get("critical") == "true"
    }
    assert critical_labels == {"eq:elbo"}
    assert "eq:elbo" in body_labels  # the critical equation IS present in body


def test_body_ledger_roundtrip_no_equations_absent_field():
    """A paper with no pivotal equations: key_equations: absent entirely, no
    '## Key equations' body section — never an error, never a violation."""
    note_text = (
        "---\n"
        "type: literature\n"
        "title: A non-math paper\n"
        "citekey: qual2022\n"
        "---\n"
        "\n<!-- no equations in this paper -->\n"
    )
    fields, body = note_mod._parse_frontmatter(note_text)
    assert "key_equations" not in fields
    assert _extract_body_equation_labels(body) == []
