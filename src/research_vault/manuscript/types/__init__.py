# SPDX-License-Identifier: AGPL-3.0-or-later
"""manuscript/types — the ManuscriptType descriptor registry.

The type-generic manuscript-loop core (``manuscript/__init__.py``) is machinery
consuming a per-type descriptor: a type supplies everything that VARIES
(section-set, Phase-1 shape, source transform, equation sources, style briefs,
exemplar bundle, rubric, reviewer lenses, canaries); the core supplies
everything that DOESN'T (the two-phase scaffolder, the per-manuscript folder
convention, the review-revise loop, the hard fidelity gates, the hermetic
``.bib`` build). Mirrors how ``dag/catalog.py`` registers loops as ``LoopEntry``
descriptors — same "descriptor consumed by generic machinery" shape.


Only ``lit-review`` is registered — as an interface-conforming STUB
(``section_set`` carries one placeholder section so the scaffolder + Phase-2
fan-out are exercisable end-to-end today). The real 9-row survey section table,
the framework-selection ``phase1_builder``, the ``source_transform``,
the ``style_briefs``, the exemplar bundle, and the rubric +
reviewer lenses + canaries land. A future
``experiment-paper`` type is NOT built here — this registry is the contract it
will implement (table, last row).

Stdlib only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# SectionSpec — one row of a type's section-set
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SectionSpec:
    """One section in a ``ManuscriptType``'s ``section_set``.

    Attributes:
        name: section key — used as the Phase-2 DAG node id AND the
            ``sections/<name>.md`` stem.
        assembly_class: ``"M"`` (mechanical/deterministic) | ``"S"``
            (synthesized, LLM-scaffolded + gated) | ``"H"`` (human-led). See
            table header for the class legend.
        source_atoms: OKF type names this section reads (e.g.
            ``("literature", "concepts")``) — becomes the node's ``reads:``.
        brief_key: style-seam tip key for this section. Empty string means
            "use ``name`` as the tip key" (the style seam falls back to a
            generic placeholder tip when no brief has been authored yet —
            honest for a type whose briefs haven't landed).
    """

    name: str
    assembly_class: str = "S"
    source_atoms: tuple[str, ...] = ()
    brief_key: str = ""


# ---------------------------------------------------------------------------
# ManuscriptType — the type descriptor (table, as a dataclass)
# ---------------------------------------------------------------------------

@dataclass
class ManuscriptType:
    """A manuscript type descriptor — the type-generic seam.

    Attributes:
        key: stable slug (``"lit-review"``; future: ``"experiment-paper"``).
        section_set: ordered ``SectionSpec`` tuple — the type's section table
            Drives the Phase-2 fan-out generically.
        phase1_builder: optional callable building a CUSTOM Phase-1 manifest
            (e.g. lit-review's framework-selection sub-loop,
            ). Signature: ``(project, slug, project_notes_dir, tree_root,
            config) -> dict[str, Any]`` (a DAG manifest dict). ``None`` = the
            core's default PASS-THROUGH — the type has no framework/
            human-owned-shape step; ``rv manuscript new`` scaffolds the folder
            only and ``rv manuscript expand`` goes straight to Phase-2
            ("A `type` whose `phase1_builder` is the default
            pass-through … skips this entirely").
        source_transform: optional OKF-atoms -> section-inputs callable
            ``None`` — populated per-type when the
            transform is built (for lit-review).
        equation_sources: OKF type names the equation extractor mines
            Consumed starting; recorded here now so the
            type contract is complete.
        style_briefs: section-name -> brief string (
            structurally-binding contract). Empty — the style seam
            (``manuscript/style.py``) falls back to a generic placeholder tip
            per section until a type's briefs are authored.
        exemplar_bundle: key into ``data/exemplars/manuscript/<key>/``
            Recorded now; the loader ships.
        rubric: rubric identifier/string.
        reviewer_lenses: reviewer lens specs.
        canaries: canary probe identifiers.
        phase2_builder: optional callable building a CUSTOM Phase-2 manifest
            (the single-pass
            outline -> draft -> assemble design, replacing the type-generic
            per-section chain). Mirrors ``phase1_builder``'s already-
            established override shape exactly (reuse-over-create, charter
            ). Signature: ``(project, slug, project_notes_dir, tree_root,
            *, manuscript_fields, config) -> dict[str, Any]`` (a DAG manifest
            dict). ``None`` = the core's default section_set chain
            (``_build_phase2_manifest``) — a type with no Phase-2 override
            keeps the N-section chain unchanged (backward compatible).
    """

    key: str
    section_set: tuple[SectionSpec, ...] = ()
    phase1_builder: Callable[..., dict[str, Any]] | None = None
    source_transform: Callable[..., Any] | None = None
    equation_sources: tuple[str, ...] = ()
    style_briefs: dict[str, str] = field(default_factory=dict)
    exemplar_bundle: str | None = None
    rubric: str | None = None
    reviewer_lenses: tuple[Any, ...] = ()
    canaries: tuple[str, ...] = ()
    phase2_builder: Callable[..., dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, ManuscriptType] = {}


def register_type(ms_type: ManuscriptType) -> None:
    """Register a ``ManuscriptType`` in the module-level registry.

    Re-registering the same ``key`` overwrites the prior entry (idempotent —
    lets a type module be re-imported without raising).
    """
    _REGISTRY[ms_type.key] = ms_type


def get_type(key: str) -> ManuscriptType | None:
    """Return the registered ``ManuscriptType`` for ``key``, or ``None`` if unknown."""
    return _REGISTRY.get(key)


def all_type_keys() -> list[str]:
    """Return all registered type keys, sorted (stable for CLI/help display)."""
    return sorted(_REGISTRY)


# Populate the registry as a side-effect of importing this package. Call-time
# (bottom-of-file) import — ``lit_review`` imports ``ManuscriptType``/
# ``register_type`` from this module, so this must run AFTER those names are
# defined above; safe because Python has already bound them in this module's
# namespace by the time this line executes (same pattern as note.py's
# lazy-registration idiom).
from . import lit_review  # noqa: E402,F401  (population side-effect)
