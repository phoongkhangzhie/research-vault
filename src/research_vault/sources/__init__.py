# SPDX-License-Identifier: AGPL-3.0-or-later
"""sources — the source-adapter abstraction (breadth-then-depth).

One narrow protocol (``SourceAdapter``) that every literature-search backend
implements; a normalized result record (``PaperHit``); a registry mapping
protocol-declared source names to adapter instances; and the composing layers
that make breadth safe and useful:

  - ``dedup``      — cross-source identity collapse (DOI > arXiv > OpenAlex >
                      normalized-title), union of external ids.
  - ``ranker``      — the 6-dim utility score + the saturation-paired
                      ≥3-source floor.
  - ``derivative``  — >60%-overlap ``derivative-of`` discounting so
                      saturation converges on INDEPENDENT sources, not a
                      deleted corpus.
  - ``sweep``       — the parallel (angle × source) width-sweep orchestrator
                      that ``rv research sweep`` drives.

Design authority: the lit-review loop redesign. Adapters are stdlib-HTTP or
subprocess-shelled (asta) — no forced third-party dependency (charter §6
reuse-over-create).
"""
from __future__ import annotations

from .base import NotSupported, PaperHit, SourceAdapter

__all__ = ["NotSupported", "PaperHit", "SourceAdapter"]
