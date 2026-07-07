"""test_manuscript_equations.py — PR-M4 acceptance tests: the don't-drop-the-
math machinery (``manuscript/equations.py``, design §7).

Coverage:
  1. extract_equation_ledger
     1a. literature/ note: labeled body block joined to FM critical: true/false
     1b. literature/ note: no "## Key equations" section -> empty ledger, no error
     1c. concepts/ (non-literature) note: generic display-math scan, critical=None
     1d. type-agnostic: a stub experiment-paper-shaped source set (methods/,
         experiments/) mines the same way — no lit-review-specific assumption
     1e. missing source dir -> contributes zero entries, no error
     1f. label-join correctness: label uniqueness + exact-match (Ada's L1 catch)
  2. build_equation_ledger_brief_block / inject_equation_brief
     2a. empty ledger -> "" block, tips unchanged (no-op, no error)
     2b. non-empty ledger -> block appended ONLY to sections reading an
         equation_source
     2c. injected block carries the equation LaTeX verbatim (never re-typed)
  3. check_equation_fidelity — SIGNAL, never BLOCK (D-MS-2)
     3a. equation present in draft (deterministic normalized match) -> no finding
     3b. marked-critical equation ABSENT from draft -> SIGNAL finding (not raise,
         not BLOCK-classed)
     3c. unmarked equation ABSENT from draft -> also SIGNAL
     3d. judge_fn fallback: normalized mismatch but judge confirms present -> no finding
     3e. judge_fn raises -> fail-closed, treated as absent -> SIGNAL finding
     3f. empty ledger (no-equations paper) -> [] findings, no error
  4. Round-trip: extract -> ledger -> inject -> (assemble mock draft) -> gate
  5. __init__.py seam wiring: cmd_expand's Phase-2 manifest node spec carries
     the injected equation ledger for a project with a literature/ note.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.manuscript import equations as eq
from research_vault.manuscript.types import SectionSpec


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_LIT_NOTE_WITH_EQUATIONS = (
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

_LIT_NOTE_NO_EQUATIONS = (
    "---\n"
    "type: literature\n"
    "title: A non-math paper\n"
    "citekey: qual2022\n"
    "---\n"
    "\n<!-- no equations in this paper -->\n"
)

_CONCEPTS_NOTE_WITH_MATH = (
    "---\n"
    "type: concepts\n"
    "title: A framework concept\n"
    "---\n"
    "\n"
    "## Definition\n\n"
    "The core relation:\n\n"
    "$$ y = f(x) + \\epsilon $$\n\n"
    "and its normalized form:\n\n"
    "\\[ \\hat{y} = \\frac{y - \\mu}{\\sigma} \\]\n"
)


def _write_note(root: Path, subdir: str, filename: str, content: str) -> Path:
    d = root / subdir
    d.mkdir(parents=True, exist_ok=True)
    p = d / filename
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 1. extract_equation_ledger
# ---------------------------------------------------------------------------

def test_literature_labeled_block_joined_to_frontmatter_criticality(tmp_path):
    _write_note(tmp_path, "literature", "kingma2013.md", _LIT_NOTE_WITH_EQUATIONS)

    ledger = eq.extract_equation_ledger(tmp_path, ("literature",))

    by_label = {e["label"]: e for e in ledger}
    assert set(by_label) == {"eq:elbo", "eq:kl"}
    assert by_label["eq:elbo"]["critical"] is True
    assert by_label["eq:kl"]["critical"] is False
    assert "\\log p(x)" in by_label["eq:elbo"]["latex"]
    assert by_label["eq:elbo"]["title"] == "Evidence lower bound"


def test_literature_note_no_equations_section_empty_ledger_no_error(tmp_path):
    _write_note(tmp_path, "literature", "qual2022.md", _LIT_NOTE_NO_EQUATIONS)

    ledger = eq.extract_equation_ledger(tmp_path, ("literature",))

    assert ledger == []


def test_concepts_note_generic_scan_unmarked(tmp_path):
    _write_note(tmp_path, "concepts", "framework.md", _CONCEPTS_NOTE_WITH_MATH)

    ledger = eq.extract_equation_ledger(tmp_path, ("concepts",))

    assert len(ledger) == 2
    assert all(e["critical"] is None for e in ledger)
    assert any("y = f(x)" in e["latex"] for e in ledger)
    assert any("hat{y}" in e["latex"] for e in ledger)


def test_type_agnostic_stub_experiment_paper_sources(tmp_path):
    """Design §7c: a future experiment-paper type mines methods/experiments,
    not literature/concepts — the extractor makes no lit-review-specific
    assumption; it is driven entirely by the equation_sources tuple."""
    _write_note(
        tmp_path, "methods", "algo.md",
        "---\ntype: methods\ntitle: The algorithm\n---\n\n"
        "$$ \\theta_{t+1} = \\theta_t - \\eta \\nabla L(\\theta_t) $$\n",
    )
    _write_note(
        tmp_path, "experiments", "run1.md",
        "---\ntype: experiments\ntitle: Run 1\n---\n\nNo equations here.\n",
    )

    ledger = eq.extract_equation_ledger(tmp_path, ("methods", "experiments"))

    assert len(ledger) == 1
    assert "nabla L" in ledger[0]["latex"]
    assert ledger[0]["critical"] is None


def test_missing_source_dir_zero_entries_no_error(tmp_path):
    # No "literature" dir created at all under tmp_path.
    ledger = eq.extract_equation_ledger(tmp_path, ("literature", "concepts"))
    assert ledger == []


def test_label_join_exact_match_only(tmp_path):
    """A frontmatter label with no matching body block (or vice versa)
    contributes no joined row for the mismatched label — never a crash."""
    note = (
        "---\n"
        "type: literature\n"
        "title: Mismatched labels\n"
        "key_equations:\n"
        "  - label: eq:typo\n"
        "    critical: true\n"
        "---\n"
        "\n"
        "## Key equations\n\n"
        "### [eq:real] The actual equation\n"
        "$$ E = mc^2 $$\n"
    )
    _write_note(tmp_path, "literature", "mismatch.md", note)

    ledger = eq.extract_equation_ledger(tmp_path, ("literature",))

    assert len(ledger) == 1
    assert ledger[0]["label"] == "eq:real"
    assert ledger[0]["critical"] is None  # no FM entry for "eq:real" -> unmarked


# ---------------------------------------------------------------------------
# 2. build_equation_ledger_brief_block / inject_equation_brief
# ---------------------------------------------------------------------------

def test_empty_ledger_brief_block_is_empty_string():
    assert eq.build_equation_ledger_brief_block([]) == ""


def test_inject_equation_brief_noop_on_empty_ledger():
    tips = {"draft": "Write the draft section."}
    sections = (SectionSpec(name="draft", source_atoms=("literature",)),)

    result = eq.inject_equation_brief(tips, [], sections, ("literature",))

    assert result == tips
    assert result is not tips  # returns a copy, not the same object


def test_inject_equation_brief_only_touches_relevant_sections():
    ledger = [{"note": "literature/x.md", "label": "eq:1", "title": "T", "latex": "E=mc^2", "critical": True}]
    sections = (
        SectionSpec(name="draft", source_atoms=("literature", "concepts")),
        SectionSpec(name="prisma", source_atoms=("mocs",)),  # no equation_sources overlap
    )

    tips = {"draft": "Write draft.", "prisma": "Write PRISMA scope."}
    result = eq.inject_equation_brief(tips, ledger, sections, ("literature", "concepts"))

    assert "E=mc^2" in result["draft"]
    assert "REQUIRE" in result["draft"]
    assert result["prisma"] == "Write PRISMA scope."  # untouched


def test_injected_block_carries_latex_verbatim():
    ledger = [{"note": "n.md", "label": "eq:elbo", "title": "ELBO", "latex": "\\log p(x)", "critical": True}]
    block = eq.build_equation_ledger_brief_block(ledger)
    assert "\\log p(x)" in block
    assert "eq:elbo" in block


# ---------------------------------------------------------------------------
# 3. check_equation_fidelity — SIGNAL, never BLOCK (D-MS-2)
# ---------------------------------------------------------------------------

def _entry(label="eq:1", latex="E = mc^2", critical=True, note="literature/x.md", title="Mass-energy"):
    return {"note": note, "label": label, "title": title, "latex": latex, "critical": critical}


def test_equation_present_in_draft_no_finding():
    ledger = [_entry(latex="$$ E = mc^2 $$")]
    draft = "As shown, $$ E = mc^2 $$ is the relation."
    findings = eq.check_equation_fidelity(ledger, draft)
    assert findings == []


def test_marked_critical_absent_is_signal_not_block():
    ledger = [_entry(latex="$$ E = mc^2 $$", critical=True)]
    draft = "The draft never reproduces the equation at all."

    findings = eq.check_equation_fidelity(ledger, draft)

    assert len(findings) == 1
    f = findings[0]
    assert f["class"] == "SIGNAL"
    assert f["severity"] == "critical"
    assert f["critical"] is True
    # The gate must NEVER raise / never carry a build-failing class.
    assert f["class"] != "BLOCK"


def test_unmarked_absent_is_also_signal():
    ledger = [_entry(latex="$$ y = f(x) $$", critical=None)]
    draft = "No equations reproduced here."

    findings = eq.check_equation_fidelity(ledger, draft)

    assert len(findings) == 1
    assert findings[0]["class"] == "SIGNAL"
    assert findings[0]["severity"] == "unmarked"


def test_judge_fn_fallback_confirms_retypeset_equivalent():
    ledger = [_entry(latex="$$ E = mc^2 $$", critical=True)]
    # Draft has a DIFFERENT-looking (deterministic-mismatch) but semantically
    # equivalent retypeset form; only the judge can confirm it.
    draft = "$$ \\text{energy} = \\text{mass} \\times c^2 $$"

    def judge_says_present(entry, draft_text):
        return True

    findings = eq.check_equation_fidelity(ledger, draft, judge_fn=judge_says_present)
    assert findings == []


def test_judge_fn_exception_fails_closed_to_signal():
    ledger = [_entry(latex="$$ E = mc^2 $$", critical=True)]
    draft = "no reproduction of the equation"

    def judge_raises(entry, draft_text):
        raise RuntimeError("judge backend unavailable")

    findings = eq.check_equation_fidelity(ledger, draft, judge_fn=judge_raises)

    assert len(findings) == 1
    assert findings[0]["class"] == "SIGNAL"  # fail-closed: absent, still SIGNAL not raised


def test_empty_ledger_no_equations_paper_is_a_noop():
    findings = eq.check_equation_fidelity([], "any draft text at all")
    assert findings == []


# ---------------------------------------------------------------------------
# 4. Round-trip: extract -> ledger -> inject -> gate
# ---------------------------------------------------------------------------

def test_full_roundtrip_dropped_critical_equation_signals_not_blocks(tmp_path):
    _write_note(tmp_path, "literature", "kingma2013.md", _LIT_NOTE_WITH_EQUATIONS)

    # (c) extract
    ledger = eq.extract_equation_ledger(tmp_path, ("literature",))
    assert len(ledger) == 2

    # (a) inject into the writer brief
    sections = (SectionSpec(name="draft", source_atoms=("literature",), brief_key="draft"),)
    tips = eq.inject_equation_brief({"draft": "Write the draft."}, ledger, sections, ("literature",))
    assert "eq:elbo" in tips["draft"]
    assert "REQUIRE" in tips["draft"]

    # Simulate a writer draft that dropped the critical eq:elbo equation but
    # reproduced the non-critical eq:kl one.
    mock_draft = "Some synthesis prose. $$ D_{KL}(q(z) \\| p(z)) $$ more prose."

    # (b) the fidelity gate
    findings = eq.check_equation_fidelity(ledger, mock_draft)

    assert len(findings) == 1
    assert findings[0]["label"] == "eq:elbo"
    assert findings[0]["severity"] == "critical"
    assert findings[0]["class"] == "SIGNAL"  # build not failed


def test_full_roundtrip_no_equations_paper_is_a_complete_noop(tmp_path):
    _write_note(tmp_path, "literature", "qual2022.md", _LIT_NOTE_NO_EQUATIONS)

    ledger = eq.extract_equation_ledger(tmp_path, ("literature",))
    assert ledger == []

    sections = (SectionSpec(name="draft", source_atoms=("literature",), brief_key="draft"),)
    tips = eq.inject_equation_brief({"draft": "Write the draft."}, ledger, sections, ("literature",))
    assert tips["draft"] == "Write the draft."  # untouched

    findings = eq.check_equation_fidelity(ledger, "any draft, no equations expected")
    assert findings == []


# ---------------------------------------------------------------------------
# 5. __init__.py seam wiring — cmd_expand injects the ledger end-to-end
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_instance):
    from research_vault.config import load_config
    return load_config(reload=True)


def test_cmd_expand_wires_equation_ledger_into_section_spec(cfg):
    from research_vault.manuscript import cmd_new, cmd_expand
    from research_vault.config import Config

    project_notes_dir = cfg.project_notes_dir("demo-research")
    _write_note(project_notes_dir, "literature", "kingma2013.md", _LIT_NOTE_WITH_EQUATIONS)

    cmd_new("demo-research", "survey-eq-wiring", ms_type_key="lit-review", config=cfg)
    manifest = cmd_expand("demo-research", "survey-eq-wiring", config=cfg)

    draft_node = next(n for n in manifest["nodes"] if n["id"] == "thematic-sections")
    # The lit-review type's real section-set (types/lit_review.py SECTION_SET,
    # landed in PR-M6) has no "draft" section any more — "thematic-sections" is
    # the section whose source_atoms=("concepts", "literature") overlap
    # equation_sources=("concepts", "literature"), so the ledger must be
    # injected into its spec. NOTE: the thematic-sections brief itself (PR-M6,
    # §3.1) already contains a bare "REQUIRE" ("REQUIRE a theme-claim + AT
    # LEAST TWO papers..."), unrelated to equations — so the equation-ledger
    # marker must be the block's own distinctive header, not bare "REQUIRE".
    assert "eq:elbo" in draft_node["spec"]
    assert "★ REQUIRE — pivotal equations from your source notes" in draft_node["spec"]


def test_cmd_expand_noop_when_no_equations_present(cfg):
    from research_vault.manuscript import cmd_new, cmd_expand

    cmd_new("demo-research", "survey-no-eq", ms_type_key="lit-review", config=cfg)
    manifest = cmd_expand("demo-research", "survey-no-eq", config=cfg)

    draft_node = next(n for n in manifest["nodes"] if n["id"] == "thematic-sections")
    assert "eq:" not in draft_node["spec"]
    assert "★ REQUIRE — pivotal equations from your source notes" not in draft_node["spec"]
