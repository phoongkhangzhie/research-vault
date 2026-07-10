# SPDX-License-Identifier: AGPL-3.0-or-later
"""manuscript/exemplars.py — the few-shot exemplar loader/injector (PR-M7, design §8).

**The general rv writing-agent principle (design §8, generalize to §12):** voice comes
from few-shot REAL examples embedded in the prompt, not from a prose description of a
style. A writer told "write in a synthesis style" enumerates; a writer shown three real
synthesis paragraphs imitates the MOVE. This module is the type-generic mechanism; the
DATA (real, attributed, fair-use-short excerpts) lives at
``data/exemplars/manuscript/<ms_type.exemplar_bundle>/`` — package data, loaded via
``importlib.resources`` (zipimport-safe, mirrors the existing pattern for doctrine/examples/
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
        # NG-8 (next-gen lit-review design §3): each block self-describes its
        # bundle key so downstream pointer-rendering can resolve the
        # installed bundle's absolute path (``resolve_exemplar_bundle_path``)
        # without a second parameter threaded through every caller.
        block["_bundle_key"] = bundle_key
        blocks.append(block)
    return blocks


def resolve_exemplar_bundle_path(bundle_key: str | None) -> Any:
    """Return the installed exemplar bundle's absolute filesystem directory.

    NG-8 (next-gen lit-review design §3.1): "the bundle is package data, not
    a filesystem path a subagent can ``read``" — this resolver is the fix.
    Package-path resolver (the operator's build-time recommendation, design §9):
    NO copy is made — for a normal (non-zip) install, ``importlib.resources
    .files()`` already resolves to a real directory on disk; this just
    returns it as a ``pathlib.Path``.

    ``bundle_key`` empty/unknown, or an unresolvable (e.g. zip-packed)
    install -> ``None``, an honest no-op (callers degrade to embedding the
    exemplar id/category without a live ``read`` path — never a fabricated
    path, never an error).

    sr: NG-lit-review-waveB (NG-8)
    """
    if not bundle_key:
        return None
    base = (
        importlib.resources.files("research_vault")
        / "data"
        / "exemplars"
        / "manuscript"
        / bundle_key
    )
    try:
        if not base.is_dir():
            return None
    except (FileNotFoundError, NotADirectoryError, OSError):
        return None
    from pathlib import Path as _Path

    p = _Path(str(base))
    return p if p.is_dir() else None


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
# orientation table that replaced it. RD-3: ``prisma-scope`` was renamed
# ``appendix-methods`` (same "scope-method" category). PR-B (gold-settled
# `report.md`): ``appendix-methods`` is no longer a `report.md`/appendix
# body section at all — it is a DEVLOG/control-note record — but its
# exemplar category mapping is unchanged (the DEVLOG record is still
# prose describing scope/method; the "scope-method" moves still apply).
LIT_REVIEW_SECTION_CATEGORY_MAP: dict[str, tuple[str, ...]] = {
    "introduction": ("framework", "figure-caption"),
    "thematic-sections": ("synthesis", "comparison"),
    "cross-cutting-analysis": ("synthesis", "comparison"),
    "open-problems": ("gap",),
    "appendix-methods": ("scope-method",),
}


# NG-8 (next-gen lit-review design §3): the header marker every injected
# pointer block carries — the presence check (``check_exemplar_pointer_presence``)
# greps for this EXACT string, so it must never be paraphrased at the call site.
MUST_READ_HEADER = "Must-read before drafting this section — imitate the MOVE, not the words:"


def render_exemplar_pointer(block: dict[str, Any]) -> str:
    """Render one exemplar block as a must-read POINTER line (NG-8), not a
    verbatim embed.

    NG-8: ``inject_exemplar_briefs`` used to append the excerpt VERBATIM
    (design §8's original form — bloated the framework brief to ~6900
    chars). NG-8 replaces that with a ``read <path>`` pointer the drafter
    actively reads, resolved via ``resolve_exemplar_bundle_path``. When the
    bundle's real filesystem path can't be resolved (e.g. a zip-packed
    install), degrades to an honest id/category-only line — never a
    fabricated path.

    Args:
        block: a parsed block from ``load_exemplar_bundle`` (self-describes
            its ``_bundle_key``/``_file``).

    Returns:
        A single pointer line, e.g.
        ``- read /abs/path/e07-foo.md (synthesis) — why: <one line>``.

    sr: NG-lit-review-waveB (NG-8)
    """
    category = block.get("category", "")
    why = block.get("why", "")
    bundle_dir = resolve_exemplar_bundle_path(block.get("_bundle_key"))
    filename = block.get("_file", "")
    if bundle_dir is not None and filename:
        path = str(bundle_dir / filename)
        return f"- read {path} ({category}) — why: {why}"
    # Honest degrade — no fabricated path when the bundle can't be resolved.
    return f"- exemplar {block.get('id', '')} ({category}) — why: {why} [path unavailable]"


def inject_exemplar_briefs(
    tips: dict[str, str],
    blocks: list[dict[str, Any]],
    section_category_map: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, str]:
    """Append matched exemplar MUST-READ POINTERS to each mapped section's brief.

    NG-8 (next-gen lit-review design §3, supersedes the PR-M7 verbatim-embed
    form): rather than embedding the excerpt text, this appends a
    ``read <path>`` pointer block the drafter actively reads (enforced by
    the ``outline`` pre-pass citing the exemplar-move it imitates, NG-7).
    Keeps the header marker ``MUST_READ_HEADER`` — the presence check
    (``check_exemplar_pointer_presence``) greps for it, so a hand-rolled
    brief that drops this block is CATCHABLE, not silently invisible (design
    §3.3 process note: "a dropped pointer is invisible").

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

    sr: PR-M7 (verbatim form); NG-lit-review-waveB (NG-8: pointer form)
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
        pointer_lines = "\n".join(render_exemplar_pointer(b) for b in matched)
        result[section_key] = (
            result[section_key].rstrip() + "\n\n---\n\n" + MUST_READ_HEADER + "\n\n" + pointer_lines
        )
    return result


def check_exemplar_pointer_presence(
    section_key: str,
    node_spec: str,
    blocks: list[dict[str, Any]],
    section_category_map: dict[str, tuple[str, ...]] | None = None,
) -> tuple[bool, str]:
    """NG-8's presence check — the load-bearing teeth (design §3.3).

    A dropped VERBATIM excerpt was at least visible bloat; a dropped
    POINTER is invisible (the section still reads fine, just voiceless).
    This check makes the drop CATCHABLE: for any section this bundle has
    matching exemplar coverage for, the node's rendered ``spec`` MUST carry
    ``MUST_READ_HEADER`` — the exact marker ``inject_exemplar_briefs``
    stamps. A hand-rolled brief that bypassed injection (the friction-log
    process note: the hub hand-rolling a batched brief instead of emitting
    ``rv dag brief``) fails this check loudly, never silently.

    Args:
        section_key: the section this node drafts (e.g. "thematic-sections").
        node_spec: the ALREADY-BUILT node spec string to check.
        blocks: the parsed exemplar bundle.
        section_category_map: defaults to ``LIT_REVIEW_SECTION_CATEGORY_MAP``.

    Returns:
        (ok, message) — ok is True when either (a) this section has no
        exemplar coverage mapped/matched (a correct, honest no-op — never a
        forced match), or (b) the pointer marker is present. False + a loud
        message when coverage exists but the marker is missing.

    sr: NG-lit-review-waveB (NG-8)
    """
    mapping = (
        section_category_map
        if section_category_map is not None
        else LIT_REVIEW_SECTION_CATEGORY_MAP
    )
    categories = mapping.get(section_key)
    if not categories:
        return True, f"OK (no exemplar category mapped for {section_key!r})"

    body_blocks = [b for b in blocks if b.get("kind") != "principle"]
    matched = [b for b in body_blocks if b.get("category") in categories]
    if not matched:
        return True, f"OK (no matching exemplar blocks for {section_key!r} in this bundle)"

    if MUST_READ_HEADER not in node_spec:
        return False, (
            f"exemplar-pointer presence check FAILED for section {section_key!r}: "
            f"this bundle has {len(matched)} matching exemplar block(s) but the "
            f"drafted node spec carries no must-read pointer block. This is the "
            f"'dropped pointer is invisible' failure mode (design §3.3) — likely "
            f"a hand-rolled brief that bypassed inject_exemplar_briefs. Re-emit "
            f"via `rv dag brief`/`rv manuscript expand`, never hand-paraphrase."
        )
    return True, "OK"
