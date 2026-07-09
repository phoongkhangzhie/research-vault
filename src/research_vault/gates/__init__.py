# SPDX-License-Identifier: AGPL-3.0-or-later
"""gates — shared LLM-judged research-integrity gates for Research Vault (PR-M3).

★ SHAREABLE LOCATION (D-SV-0). The removed ``manuscript/support_matcher.py``
(deleted in SR-RM-FIGMS) is re-instantiated HERE — a top-level
``research_vault.gates`` package, sibling to ``manuscript/``, ``review/``,
and ``experiment/`` — rather than back under ``manuscript/``.

Why shared, not siloed: the craft this module embodies (anti-anchoring,
disconfirm-first, verbatim-span-or-BLOCK, blind-judge canary, fail-closed
defaults — see ``data/doctrine/honesty-gates.md``) is NOT specific to the
manuscript loop. Any loop that needs "does this claim actually trace to this
source note?" (support_matcher) can call it directly. The manuscript loop is
the FIRST consumer, not the only one — see ``manuscript/fidelity_gates.py``
for the thin manuscript-scoped adapter (batch-tally over a manuscript tree,
canary-gated) that wires this into ``rv manuscript check``.

Modules:
  support_matcher.py — 4-verdict claim -> source matcher
                        ([SUPPORTS|PARTIAL|ABSENT|CONTRADICTS]) (SR-MS-2)

(The former ``coldread.py`` self-containment critic — 3-verdict
[STANDS-ALONE|DANGLING|NEEDS-CONTEXT] — was removed: it was SIGNAL-only,
non-actionable under hands-off autonomy, and redundant with the 2x3 review
board's coherence axis + RD-6's hard term-definition gate. The operator's call;
see DEVLOG.)

stdlib-only, judge_fn-injectable (mockable in tests, no live LLM call
required), and fail-closed by construction (a parse failure, a missing
field, or a judge exception never certifies — it BLOCKs).

sr: PR-M3
"""
from __future__ import annotations
