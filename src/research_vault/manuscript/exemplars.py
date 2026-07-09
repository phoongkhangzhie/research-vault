"""manuscript/exemplars.py — the few-shot exemplar loader/injector (PR-M7, design §8).

**The general rv writing-agent principle (design §8, generalize to §12):** voice comes
from few-shot REAL examples embedded in the prompt, not from a prose description of a
style. A writer told "write in a synthesis style" enumerates; a writer shown three real
synthesis paragraphs imitates the MOVE. This module is the type-generic mechanism; the
DATA (real, attributed, fair-use-short excerpts) lives at
``data/exemplars/manuscript/<ms_type.exemplar_bundle>/`` — package data, loaded via
``importlib.resources`` (zipimport-safe, mirrors SR-PKG's pattern for doctrine/examples/
templates).

Each excerpt file is a labeled few-shot block (the "block-header schema", matching the researcher's
staging doc `docs/superpowers/specs/2026-07-07-survey-exemplar-corpus.md`):

    id: E1
    source: <author, venue, arXiv/DOI>
    category: <canonical injection bucket — framework|synthesis|figure-caption|
               comparison|gap|scope-method|principle>
    technique: <the specific move/technique, a finer-grained label>
    why: <one line — what the writer should learn from it>
    kind: exemplar | principle
    verbatim-verified: yes
    ---
    <the verbatim passage, unindented, wording never altered>

``kind: principle`` blocks (E17/E18 in the lit-review bundle) are NOT body-prose
exemplars — they are prompt-level RULE anchors for the writer's system preamble
(the researcher's placement, design §8: "for the writer's system prompt, not body prose").

**Not a description — the real text is in the prompt.** ``inject_exemplar_briefs``
embeds the excerpts VERBATIM as labeled few-shot blocks in the matching section's
brief. A section brief that ships without its exemplar block fails its own test
(teeth, design §8/PR-M7 acceptance) — see ``test_manuscript_exemplars.py``.

Stdlib only (``importlib.resources`` only).
sr: PR-M7
"""
from __future__ import annotations

import importlib.resources
from typing import Any

# ---------------------------------------------------------------------------
# Loading — data/exemplars/manuscript/<bundle_key>/*.md -> parsed blocks
# ---------------------------------------------------------------------------


def _parse_exemplar_file(text: str, *, filename: str) -> dict[str, Any]:
    """Parse one exemplar file: ``key: value`` header lines, a bare ``---``
    separator line, then the verbatim passage.

    Raises ValueError on a malformed file (missing the ``---`` separator) —
    a materialization bug should fail loudly at load time, never silently
    drop a block (charter §2).

    Args:
        text: the file's full text.
        filename: for error messages only.

    Returns:
        dict with every header key (str) plus ``"verbatim"`` (the passage,
        stripped of leading/trailing blank lines; internal wording untouched).

    sr: PR-M7
    """
    lines = text.splitlines()
    header: dict[str, str] = {}
    body_start: int | None = None
    for i, line in enumerate(lines):
        if line.strip() == "---":
            body_start = i + 1
            break
        if ":" in line:
            key, _, val = line.partition(":")
            header[key.strip()] = val.strip()
    if body_start is None:
        raise ValueError(
            f"manuscript/exemplars: {filename} is missing the '---' header/body "
            f"separator — malformed exemplar file (block-header schema requires it)."
        )
    verbatim = "\n".join(lines[body_start:]).strip("\n").strip()
    header["verbatim"] = verbatim
    return header


def load_exemplar_bundle(bundle_key: str | None) -> list[dict[str, Any]]:
    """Load every exemplar block for a ``ManuscriptType.exemplar_bundle`` key.

    When to use: called by the manuscript loop's Phase-2 scaffolder
    (``manuscript/__init__.py``'s ``_build_phase2_manifest``) right after
    ``get_manuscript_section_tips`` builds the per-section tips dict — mirrors
    ``equations.extract_equation_ledger``'s seam position.

    ``bundle_key`` is ``None``/empty, or the bundle directory doesn't exist
    (a future type with no exemplars authored yet) -> ``[]``, a correct,
    honest no-op — never an error and never a fabricated block.

    Args:
        bundle_key: ``ms_type.exemplar_bundle`` (e.g. ``"lit-review"``), or
            ``None``.

    Returns:
        Parsed blocks, sorted by filename (stable — the ``e01-...`` prefix
        convention keeps corpus order deterministic).

    sr: PR-M7
    """
    if not bundle_key:
        return []

    base = (
        importlib.resources.files("research_vault")
        / "data"
        / "exemplars"
        / "manuscript"
        / bundle_key
    )
    try:
        if not base.is_dir():
            return []
    except (FileNotFoundError, NotADirectoryError, OSError):
        return []

    blocks: list[dict[str, Any]] = []
    for entry in sorted(base.iterdir(), key=lambda p: p.name):
        if not entry.name.endswith(".md"):
            continue
        text = entry.read_text(encoding="utf-8")
        block = _parse_exemplar_file(text, filename=entry.name)
        block["_file"] = entry.name
        blocks.append(block)
    return blocks


# ---------------------------------------------------------------------------
# Rendering — a block -> the design §8 labeled few-shot form
# ---------------------------------------------------------------------------


def render_exemplar_block(block: dict[str, Any]) -> str:
    """Render one exemplar block as a labeled few-shot block (design §8's form):

        [EXEMPLAR — <category> — <source>]
        "<verbatim passage>"
          ↳ <why>

    Args:
        block: a parsed block from ``load_exemplar_bundle``.

    Returns:
        The rendered few-shot block, real excerpt text embedded verbatim.

    sr: PR-M7
    """
    category = block.get("category", "")
    source = block.get("source", "")
    why = block.get("why", "")
    verbatim = block.get("verbatim", "")
    return (
        f"[EXEMPLAR — {category} — {source}]\n"
        f'"{verbatim}"\n'
        f"  ↳ {why}"
    )


def build_principle_anchor_block(blocks: list[dict[str, Any]]) -> str:
    """Render ``kind: principle`` blocks as system-preamble RULE anchors.

    These (E17/E18 in the lit-review bundle) are prompt-level instructions,
    NOT body-prose exemplars to imitate verbatim (the researcher's placement, design
    §8's "for the writer's system prompt, not body prose") — rendered
    differently from ``render_exemplar_block`` (no "imitate the MOVE" framing;
    stated as a rule with attribution).

    Args:
        blocks: the full parsed bundle (both kinds); this function filters.

    Returns:
        "" if no ``kind: principle`` blocks are present (an honest no-op —
        a type whose bundle has no principle anchors gets no preamble
        addition, never a fabricated one).

    sr: PR-M7
    """
    principles = [b for b in blocks if b.get("kind") == "principle"]
    if not principles:
        return ""
    lines = ["Editorial principles (apply these as RULES governing your prose, not as text to imitate):"]
    for p in principles:
        source = p.get("source", "")
        verbatim = p.get("verbatim", "")
        lines.append(f'- "{verbatim}" ({source})')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Injection — section-key -> the moves that section's writer should see
# ---------------------------------------------------------------------------

# The lit-review section keys mapped to the exemplar ``category`` buckets
# relevant to what that section's writer must produce (design §8: "matched to
# the section being drafted"). A section key absent here (conclusion,
# references, abstract, assemble) has no exemplar coverage in the
# researcher-curated corpus (§ Coverage map) and is an honest no-op, never a
# forced match.
#
# RD-4 (next-gen lit-review design §6): the standalone ``framework`` body
# section is deleted — its exemplar category ("framework"/"figure-caption")
# now folds into ``introduction``, which carries the spine-at-a-glance
# orientation table that replaced it. RD-3: ``prisma-scope`` is renamed
# ``appendix-methods`` (relocated to the appendix, same category).
LIT_REVIEW_SECTION_CATEGORY_MAP: dict[str, tuple[str, ...]] = {
    "introduction": ("framework", "figure-caption"),
    "thematic-sections": ("synthesis", "comparison"),
    "cross-cutting-analysis": ("synthesis", "comparison"),
    "open-problems": ("gap",),
    "appendix-methods": ("scope-method",),
}


def inject_exemplar_briefs(
    tips: dict[str, str],
    blocks: list[dict[str, Any]],
    section_category_map: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, str]:
    """Append matched few-shot exemplar blocks to each mapped section's brief.

    When to use: called right after ``load_exemplar_bundle`` in the Phase-2
    scaffolder (mirrors ``equations.inject_equation_brief``'s seam position
    and additive-copy contract — the input ``tips`` dict is never mutated).

    Args:
        tips: the section-key -> tip-string dict (as built by
            ``style.get_manuscript_section_tips``, possibly already extended
            by the equation/source-transform injectors).
        blocks: the parsed exemplar bundle (``load_exemplar_bundle``'s
            return) — ``kind: principle`` blocks are excluded here (they go
            to the preamble via ``build_principle_anchor_block``, not body
            briefs).
        section_category_map: section-key -> category tuple; defaults to
            ``LIT_REVIEW_SECTION_CATEGORY_MAP``. A future type passes its own.

    Returns:
        A NEW dict (copy of ``tips``). A section key not present in ``tips``,
        a category with zero matching blocks, or an empty ``blocks`` list is
        an honest no-op for that section.

    sr: PR-M7
    """
    mapping = (
        section_category_map
        if section_category_map is not None
        else LIT_REVIEW_SECTION_CATEGORY_MAP
    )
    result = dict(tips)
    body_blocks = [b for b in blocks if b.get("kind") != "principle"]
    if not body_blocks:
        return result

    for section_key, categories in mapping.items():
        if section_key not in result:
            continue
        matched = [b for b in body_blocks if b.get("category") in categories]
        if not matched:
            continue
        rendered = "\n\n".join(render_exemplar_block(b) for b in matched)
        header = (
            "Here are excerpts from published surveys demonstrating the target "
            "voice. Imitate the MOVE, not the words:\n\n"
        )
        result[section_key] = (
            result[section_key].rstrip() + "\n\n---\n\n" + header + rendered
        )
    return result
