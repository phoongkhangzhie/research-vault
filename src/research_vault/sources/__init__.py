"""sources — the NG-1 source-adapter abstraction (breadth-then-depth, Wave A).

One narrow protocol (``SourceAdapter``) that every literature-search backend
implements; a normalized result record (``PaperHit``); a registry mapping
protocol-declared source names to adapter instances; and the composing layers
that make breadth safe and useful:

  - ``dedup``      — cross-source identity collapse (DOI > arXiv > OpenAlex >
                      normalized-title), union of external ids (NG-2).
  - ``ranker``      — the 6-dim utility score + the saturation-paired
                      ≥3-source floor (NG-3 / HR-craft rec 2).
  - ``derivative``  — >60%-overlap ``derivative-of`` discounting so
                      saturation converges on INDEPENDENT sources, not a
                      deleted corpus (NG-9 / HR-craft rec 3).
  - ``sweep``       — the parallel (angle × source) width-sweep orchestrator
                      that ``rv research sweep`` drives (NG-3).

Design authority: the next-gen lit-review loop design doc, §4 (breadth-then-
depth) + §7 (HR-craft folds). Adapters are stdlib-HTTP or subprocess-shelled
(asta) — no forced third-party dependency (charter §6 reuse-over-create).
"""
from __future__ import annotations

from .base import NotSupported, PaperHit, SourceAdapter

__all__ = ["NotSupported", "PaperHit", "SourceAdapter"]
