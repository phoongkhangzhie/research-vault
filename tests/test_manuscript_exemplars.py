"""test_manuscript_exemplars.py — PR-M7 acceptance tests: the in-context
exemplar few-shot machinery (``manuscript/exemplars.py``, design §8).

Coverage:
  1. load_exemplar_bundle
     1a. real ``lit-review`` bundle (package data) -> 18 blocks, sorted by
         filename, every block carries ``category``/``source``/``verbatim``.
     1b. unknown/empty bundle_key -> [] (honest no-op, no error).
     1c. a synthetic temp bundle (importlib.resources override not needed —
         parse the raw text directly) round-trips the block-header schema.
     1d. a malformed exemplar file (missing the ``---`` separator) raises
         ValueError loudly, never silently drops a block.
  2. render_exemplar_block / build_principle_anchor_block
     2a. rendered block carries the verbatim passage + category + source.
     2b. principle blocks (kind=principle) are excluded from body rendering
         and instead render as RULE anchors via build_principle_anchor_block.
     2c. no principle blocks -> "" (no-op).
  3. inject_exemplar_briefs
     3a. empty blocks -> tips unchanged (no-op, no error).
     3b. non-empty bundle -> block appended ONLY to sections mapped in
         section_category_map, verbatim text present.
     3c. a section key absent from the tips dict is skipped, no KeyError.
     3d. principle-kind blocks are NOT injected into body tips (only through
         the preamble path).
  4. Teeth: a section brief shipped WITHOUT its matched exemplar block fails
     its own test — i.e. if the injector is bypassed/removed, the assertion
     that the exemplar text is present in the writer's brief FAILS. This
     directly proves the "ships without its exemplar block fails its test"
     acceptance criterion (design §8/PR-M7).
  5. __init__.py seam wiring: cmd_expand's Phase-2 manifest node spec for the
     lit-review type's mapped sections carries the injected exemplar text
     end-to-end, type-scoped (only lit-review's bundle loads for lit-review).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.manuscript import exemplars as ex
from research_vault.manuscript.types import get_type


# ---------------------------------------------------------------------------
# 1. load_exemplar_bundle
# ---------------------------------------------------------------------------

def test_lit_review_bundle_loads_18_blocks():
    blocks = ex.load_exemplar_bundle("lit-review")
    assert len(blocks) == 18
    # sorted by filename (e01-... .. e18-...) -> deterministic corpus order
    assert blocks[0]["id"] == "E1"
    assert blocks[-1]["id"] == "E18"
    for block in blocks:
        assert block.get("category")
        assert block.get("source")
        assert block.get("verbatim")
        assert block.get("verbatim-verified") == "yes" or block["verbatim-verified"].startswith("yes")


def test_unknown_or_empty_bundle_key_is_noop():
    assert ex.load_exemplar_bundle(None) == []
    assert ex.load_exemplar_bundle("") == []
    assert ex.load_exemplar_bundle("no-such-type") == []


def test_block_header_schema_round_trip():
    text = (
        "id: EX\n"
        "source: Test et al., Test Survey — arXiv:0000.00000\n"
        "category: synthesis\n"
        "technique: a test technique\n"
        "why: a test reason\n"
        "kind: exemplar\n"
        "verbatim-verified: yes\n"
        "---\n"
        "This is the verbatim passage.\n"
    )
    block = ex._parse_exemplar_file(text, filename="test.md")
    assert block["id"] == "EX"
    assert block["category"] == "synthesis"
    assert block["verbatim"] == "This is the verbatim passage."


def test_malformed_file_missing_separator_raises():
    text = "id: EX\nsource: X\ncategory: synthesis\n"
    with pytest.raises(ValueError, match="missing the '---'"):
        ex._parse_exemplar_file(text, filename="broken.md")


# ---------------------------------------------------------------------------
# 2. render_exemplar_block / build_principle_anchor_block
# ---------------------------------------------------------------------------

def test_render_exemplar_block_carries_verbatim_and_provenance():
    block = {
        "category": "synthesis",
        "source": "Test et al. — arXiv:0000.00000",
        "why": "a test reason",
        "verbatim": "the exact test passage",
    }
    rendered = ex.render_exemplar_block(block)
    assert "the exact test passage" in rendered
    assert "synthesis" in rendered
    assert "Test et al." in rendered
    assert "EXEMPLAR" in rendered


def test_principle_anchor_block_excludes_exemplars_includes_principles():
    blocks = ex.load_exemplar_bundle("lit-review")
    anchor = ex.build_principle_anchor_block(blocks)
    # E17/E18's verbatim passages must be present (principle anchors)
    e17 = next(b for b in blocks if b["id"] == "E17")
    e18 = next(b for b in blocks if b["id"] == "E18")
    assert e17["verbatim"] in anchor
    assert e18["verbatim"] in anchor
    # a body-exemplar's verbatim (e.g. E1, non-principle) must NOT appear here
    e1 = next(b for b in blocks if b["id"] == "E1")
    assert e1["verbatim"] not in anchor


def test_principle_anchor_block_empty_when_no_principles():
    body_only = [b for b in ex.load_exemplar_bundle("lit-review") if b.get("kind") != "principle"]
    assert ex.build_principle_anchor_block(body_only) == ""


# ---------------------------------------------------------------------------
# 3. inject_exemplar_briefs
# ---------------------------------------------------------------------------

def test_inject_noop_on_empty_blocks():
    # RD-4: the "framework" exemplar category folds into "introduction"
    # (the standalone framework body section is deleted).
    tips = {"introduction": "Write the introduction section."}
    result = ex.inject_exemplar_briefs(tips, [])
    assert result == tips
    assert result is not tips  # new dict returned (additive contract)


def test_inject_only_touches_mapped_sections_with_pointer_form():
    """NG-8: inject_exemplar_briefs appends a must-read POINTER (a `read
    <path>` line + MUST_READ_HEADER marker), NOT the verbatim excerpt text —
    supersedes PR-M7's verbatim-embed form."""
    tips = {
        "introduction": "Write the introduction section.",
        "conclusion": "Write the conclusion.",
    }
    blocks = ex.load_exemplar_bundle("lit-review")
    result = ex.inject_exemplar_briefs(tips, blocks)
    # introduction is mapped to ("framework", "figure-caption") categories (RD-4)
    e1 = next(b for b in blocks if b["id"] == "E1")
    assert ex.MUST_READ_HEADER in result["introduction"]
    assert "read " in result["introduction"]
    assert e1["id"] in result["introduction"] or e1["_file"] in result["introduction"]
    # the verbatim excerpt text itself is NOT embedded (NG-8's whole point —
    # bloat reduction; a section brief shrinks, it doesn't grow).
    assert e1["verbatim"] not in result["introduction"]
    # conclusion has no category mapping in the lit-review map -> untouched
    assert result["conclusion"] == "Write the conclusion."


def test_inject_skips_section_key_absent_from_tips_no_error():
    tips = {"conclusion": "Write the conclusion."}
    blocks = ex.load_exemplar_bundle("lit-review")
    result = ex.inject_exemplar_briefs(tips, blocks)  # "introduction" absent from tips
    assert result == {"conclusion": "Write the conclusion."}


def test_inject_never_embeds_principle_blocks_in_body_tips():
    tips = {"introduction": "Write the introduction section."}
    blocks = ex.load_exemplar_bundle("lit-review")
    result = ex.inject_exemplar_briefs(tips, blocks)
    e17 = next(b for b in blocks if b["id"] == "E17")
    e18 = next(b for b in blocks if b["id"] == "E18")
    assert e17["verbatim"] not in result["introduction"]
    assert e18["verbatim"] not in result["introduction"]


# ---------------------------------------------------------------------------
# 4. Teeth — a brief shipped WITHOUT its exemplar block fails its own test
# ---------------------------------------------------------------------------

def test_teeth_brief_without_injection_fails_the_assertion():
    """Simulates the "no-injection" bug directly: if a caller builds a
    section brief WITHOUT calling inject_exemplar_briefs, this exact
    assertion (which the real pipeline test below also runs) FAILS —
    proving the acceptance criterion "a section brief shipped without its
    exemplar block fails its test" (design §8/PR-M7; NG-8: now checking the
    pointer marker, not verbatim text).
    """
    tips = {"thematic-sections": "Draft the thematic sections."}
    blocks = ex.load_exemplar_bundle("lit-review")

    # RED: the un-injected brief does NOT carry the must-read pointer marker.
    assert ex.MUST_READ_HEADER not in tips["thematic-sections"]

    # GREEN: after injection, it does.
    injected = ex.inject_exemplar_briefs(tips, blocks)
    assert ex.MUST_READ_HEADER in injected["thematic-sections"]

    # And the standalone presence check agrees (NG-8's reusable teeth).
    ok, _ = ex.check_exemplar_pointer_presence(
        "thematic-sections", injected["thematic-sections"], blocks,
    )
    assert ok is True
    ok_missing, msg = ex.check_exemplar_pointer_presence(
        "thematic-sections", tips["thematic-sections"], blocks,
    )
    assert ok_missing is False
    assert "dropped pointer" in msg.lower() or "FAILED" in msg


# ---------------------------------------------------------------------------
# 5. __init__.py seam wiring — cmd_expand injects the exemplar bundle
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg(tmp_instance):
    from research_vault.config import load_config
    return load_config(reload=True)


def test_cmd_expand_wires_exemplar_bundle_into_mapped_section_spec(cfg):
    from research_vault.manuscript import cmd_new, cmd_expand

    cmd_new("demo-research", "survey-exemplar-wiring", ms_type_key="lit-review", config=cfg)
    manifest = cmd_expand("demo-research", "survey-exemplar-wiring", config=cfg)

    thematic_node = next(n for n in manifest["nodes"] if n["id"] == "thematic-sections")
    # RD-4: the standalone 'framework' body section is deleted — its
    # exemplar category folds into 'introduction' (the spine-at-a-glance
    # orientation table that replaced it).
    introduction_node = next(n for n in manifest["nodes"] if n["id"] == "introduction")

    blocks = ex.load_exemplar_bundle("lit-review")

    # NG-8: the section spec carries the must-read pointer marker + a real
    # `read <path>` line, not the verbatim excerpt text.
    assert ex.MUST_READ_HEADER in thematic_node["spec"]
    assert ex.MUST_READ_HEADER in introduction_node["spec"]
    assert "read " in thematic_node["spec"]
    assert "imitate the MOVE, not the words" in thematic_node["spec"]

    # The presence check agrees for both mapped sections.
    ok_t, _ = ex.check_exemplar_pointer_presence("thematic-sections", thematic_node["spec"], blocks)
    ok_i, _ = ex.check_exemplar_pointer_presence("introduction", introduction_node["spec"], blocks)
    assert ok_t is True
    assert ok_i is True

    # NG-8: the exemplar bundle's absolute dir is wired into `reads:` so the
    # harness's reads-grounding resolver surfaces the pointed-at files.
    assert any("exemplars" in r and "lit-review" in r for r in thematic_node["reads"])


def test_cmd_expand_wires_principle_anchors_into_every_node_via_preamble(cfg):
    from research_vault.manuscript import cmd_new, cmd_expand

    cmd_new("demo-research", "survey-principle-wiring", ms_type_key="lit-review", config=cfg)
    manifest = cmd_expand("demo-research", "survey-principle-wiring", config=cfg)

    blocks = ex.load_exemplar_bundle("lit-review")
    e17 = next(b for b in blocks if b["id"] == "E17")

    # the preamble is prepended to EVERY section's spec (_spec() closure) —
    # check a section with no direct exemplar match still carries the
    # principle anchor (proves it travels via the preamble, not per-section).
    intro_node = next(n for n in manifest["nodes"] if n["id"] == "introduction")
    assert e17["verbatim"] in intro_node["spec"]


def test_type_scoped_unmapped_section_key_gets_no_exemplar_injection(cfg):
    """A section key with no category mapping (e.g. `conclusion`) never gets
    a fabricated match — honest no-op, proving injection is matched, not
    blanket-applied.
    """
    from research_vault.manuscript import cmd_new, cmd_expand

    cmd_new("demo-research", "survey-unmapped-section", ms_type_key="lit-review", config=cfg)
    manifest = cmd_expand("demo-research", "survey-unmapped-section", config=cfg)

    conclusion_node = next(n for n in manifest["nodes"] if n["id"] == "conclusion")
    # no "[EXEMPLAR —" few-shot block appears for conclusion (no category
    # mapping) — only the principle-anchor preamble text (if any) is present.
    assert "[EXEMPLAR —" not in conclusion_node["spec"]


# ---------------------------------------------------------------------------
# 6. NG-8 — resolve_exemplar_bundle_path + the wired-in pre-dispatch assertion
# ---------------------------------------------------------------------------

def test_resolve_exemplar_bundle_path_returns_real_dir_for_lit_review():
    p = ex.resolve_exemplar_bundle_path("lit-review")
    assert p is not None
    assert p.is_dir()
    assert (p / "e01-framework.md").exists() or any(p.glob("e01-*.md"))


def test_resolve_exemplar_bundle_path_honest_none_for_unknown_bundle():
    assert ex.resolve_exemplar_bundle_path("no-such-bundle") is None
    assert ex.resolve_exemplar_bundle_path("") is None
    assert ex.resolve_exemplar_bundle_path(None) is None


def test_render_exemplar_pointer_carries_real_read_path_not_verbatim():
    blocks = ex.load_exemplar_bundle("lit-review")
    e1 = next(b for b in blocks if b["id"] == "E1")
    pointer = ex.render_exemplar_pointer(e1)
    assert pointer.startswith("- read ")
    assert e1["_file"] in pointer
    assert e1["verbatim"] not in pointer


def test_build_phase2_manifest_raises_on_dropped_pointer_regression(cfg):
    """NG-8 §3.3 acceptance: if inject_exemplar_briefs were bypassed for a
    section this bundle covers, the manifest build must fail LOUDLY — never
    silently ship a voiceless brief. Simulated by monkeypatching
    inject_exemplar_briefs to a no-op, mirroring the friction-log's
    hand-rolled-brief regression."""
    import research_vault.manuscript as ms_mod

    original = ms_mod._exemplars.inject_exemplar_briefs
    ms_mod._exemplars.inject_exemplar_briefs = lambda tips, blocks, *a, **kw: dict(tips)
    try:
        from research_vault.manuscript import cmd_new

        cmd_new("demo-research", "survey-dropped-pointer", ms_type_key="lit-review", config=cfg)
        with pytest.raises(ValueError, match="exemplar-pointer presence check FAILED"):
            ms_mod.cmd_expand("demo-research", "survey-dropped-pointer", config=cfg)
    finally:
        ms_mod._exemplars.inject_exemplar_briefs = original
