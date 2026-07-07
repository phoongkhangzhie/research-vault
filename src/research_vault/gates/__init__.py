"""gates — shared LLM-judged research-integrity gates for Research Vault (PR-M3).

★ SHAREABLE LOCATION (D-SV-0). The removed ``manuscript/support_matcher.py``
and ``manuscript/coldread.py`` (deleted in SR-RM-FIGMS) are re-instantiated
HERE — a top-level ``research_vault.gates`` package, sibling to ``manuscript/``,
``review/``, and ``experiment/`` — rather than back under ``manuscript/``.

Why shared, not siloed: the craft these modules embody (anti-anchoring,
disconfirm-first, verbatim-span-or-BLOCK, blind-judge canary, fail-closed
defaults — see ``data/doctrine/honesty-gates.md``) is NOT specific to the
manuscript loop. Any loop that needs "does this claim actually trace to this
source note?" (support_matcher) or "can a fresh reader follow this artifact
unaided?" (coldread) can call these directly. The manuscript loop is the
FIRST consumer, not the only one — see ``manuscript/fidelity_gates.py`` for
the thin manuscript-scoped adapter (batch-tally over a manuscript tree,
canary-gated) that wires these into ``rv manuscript check``.

Modules:
  support_matcher.py — 4-verdict claim -> source matcher
                        ([SUPPORTS|PARTIAL|ABSENT|CONTRADICTS]) (SR-MS-2)
  coldread.py         — 3-verdict self-containment judge
                        ([STANDS-ALONE|DANGLING|NEEDS-CONTEXT]) (SR-MS-COLDREAD)

Both are stdlib-only, judge_fn-injectable (mockable in tests, no live LLM
call required), and fail-closed by construction (a parse failure, a missing
field, or a judge exception never certifies — it BLOCKs).

sr: PR-M3
"""
from __future__ import annotations
