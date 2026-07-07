"""manuscript/types/lit_review.py — the ``lit-review`` ManuscriptType (PR-M1 STUB).

Registers the survey/review-paper type as an interface-conforming placeholder:
ONE placeholder section (``draft``) so the type-generic core (the scaffolder,
the Phase-2 fan-out, ``assemble`` -> ``[HG:approve-manuscript]``) is exercisable
end-to-end today, without fabricating the real survey machinery ahead of its
own PRs.

What's real here vs. what's a placeholder (be honest about the gap, charter §1):
  - REAL now:  the type is registered; ``rv manuscript new --type lit-review``
    scaffolds a genuine per-manuscript folder + (pass-through) Phase-1;
    ``rv manuscript expand`` emits a genuine, schema-valid Phase-2 manifest
    with the one ``draft`` section -> ``assemble`` -> ``approve-manuscript``.
  - STUB (lands in a later PR):
    - the real 9-row survey section table (Abstract / Introduction / PRISMA /
      framework / N thematic sections / cross-cutting / open problems /
      conclusion / references) — design §3 — lands in PR-M6.
    - the framework-selection ``phase1_builder`` (design §5) — PR-M6.
    - the OKF -> survey ``source_transform`` (design §4) — PR-M6.
    - the §3.1 structurally-binding thematic-section briefs — PR-M3/M6.
    - the exemplar bundle loader (design §8) — PR-M8.
    - the rubric + reviewer lenses + canaries (design §11) — PR-M8.

sr: PR-M1
"""
from __future__ import annotations

from . import ManuscriptType, SectionSpec, register_type

# PR-M1 placeholder section-set: ONE section so the Phase-2 fan-out has
# something real to build (a schema-valid manifest, not a fabricated one).
# Replaced with the real 9-row table (design §3) in PR-M6.
_STUB_SECTION_SET: tuple[SectionSpec, ...] = (
    SectionSpec(
        name="draft",
        assembly_class="S",
        source_atoms=("literature", "concepts", "mocs"),
        brief_key="draft",
    ),
)

LIT_REVIEW = ManuscriptType(
    key="lit-review",
    section_set=_STUB_SECTION_SET,
    phase1_builder=None,       # PR-M6: framework-selection sub-loop (design §5)
    source_transform=None,     # PR-M6: OKF -> survey transformation (design §4)
    equation_sources=("concepts", "literature"),  # design §7 — consumed starting PR-M4
    style_briefs={},           # PR-M3/M6: the §3.1 thematic-section brief contract
    exemplar_bundle="lit-review",  # PR-M8: data/exemplars/manuscript/lit-review/
    rubric=None,               # PR-M8: DEFAULT_LIT_REVIEW_RUBRIC (design §11.1)
    reviewer_lenses=(),        # PR-M5: coverage / framework / synthesis lenses (§11.2)
    canaries=(),               # PR-M8: strong / weak / annotated-bib (§11.3)
)

register_type(LIT_REVIEW)
