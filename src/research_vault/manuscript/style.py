# SPDX-License-Identifier: AGPL-3.0-or-later
"""manuscript/style.py — the manuscript-loop style seam (PR-M1, type-generic).

Mirrors ``review/style.py``'s tips-seam pattern (design §2's module layout: the
style seam = per-type section briefs + exemplar injection, §3.1/§8). For PR-M1
the payload is deliberately thin: a generic preamble + per-section tips sourced
from the active ``ManuscriptType``'s ``style_briefs`` dict, falling back to a
generic placeholder tip for any ``section_set`` entry with no brief authored
yet (honest — a type's real briefs land in PR-M3/M6; the seam itself is the
type-generic machinery built now).

Adopters override via ``[manuscript_style]`` in ``research_vault.toml`` —
``preamble`` + per-section-key overrides — exactly as ``[review_style]`` does
for the review loop.

Stdlib only.
sr: PR-M1
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from research_vault.manuscript.types import ManuscriptType

# ---------------------------------------------------------------------------
# Default style preamble
# ---------------------------------------------------------------------------

_DEFAULT_PREAMBLE: str = (
    "You are drafting a section of a manuscript in the Research Vault manuscript "
    "loop (type-generic core, PR-M1).\n"
    "Anti-fabrication spine: numbers, citations, table cells, and pivotal equations "
    "are DATA — injected by the machine (hermetic references.md build in PR-M2, results/equation "
    "injection in PR-M2/PR-M4), never typed by you. Claims and comparisons are "
    "PROSE you synthesize, gated against their source notes (the fidelity gates, "
    "PR-M3)."
)


def get_manuscript_style_preamble(config: Any = None) -> str:
    """Return the manuscript style preamble, merged with any adopter override.

    Args:
        config: a loaded Config instance (or None for the shipped default).
                If the config has ``[manuscript_style] preamble = "..."`` it is used.

    Returns:
        The preamble string injected before every node's spec.

    sr: PR-M1
    """
    if config is not None:
        raw = getattr(config, "_raw", {})
        override = raw.get("manuscript_style", {})
        if isinstance(override, dict):
            preamble = override.get("preamble")
            if isinstance(preamble, str) and preamble.strip():
                return preamble
    return _DEFAULT_PREAMBLE


def get_manuscript_section_tips(
    ms_type: "ManuscriptType",
    config: Any = None,
) -> dict[str, str]:
    """Return the section-key -> tip-string dict for a given ``ManuscriptType``.

    Defaults to the type descriptor's ``style_briefs``; falls back to a
    generic "write section <name>" placeholder for any ``section_set`` entry
    with no brief yet — an honest placeholder, never a fabricated brief
    (populated per-section in PR-M3/M6).

    Adopter override: ``[manuscript_style]`` section, keyed by section name
    (the ``preamble`` key is reserved and skipped here).

    Args:
        ms_type: the active ManuscriptType descriptor.
        config: optional loaded Config (or None for shipped defaults only).

    Returns:
        dict with a key for every ``ms_type.section_set`` entry, plus any
        extra keys already present in ``ms_type.style_briefs``.

    sr: PR-M1
    """
    tips: dict[str, str] = dict(ms_type.style_briefs or {})
    for section in ms_type.section_set:
        key = section.brief_key or section.name
        tips.setdefault(key, f"Write the {section.name} section.")

    if config is not None:
        raw = getattr(config, "_raw", {})
        override = raw.get("manuscript_style", {})
        if isinstance(override, dict):
            for key, value in override.items():
                if key == "preamble":
                    continue
                if isinstance(value, str):
                    tips[key] = value

    return tips
