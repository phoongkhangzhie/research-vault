"""figures/style.py — the apply_style seam (STUB).

SEAM CONTRACT — DO NOT CHANGE THE SIGNATURE.
  apply_style(preset, skin) is the hook that render calls before plotting.
  Iris replaces this stub with the real aesthetic (BeautifulFigures adaptation +
  frontend-design typography/palette/skin). The plumbing calls it; Iris owns it.

  The two halves are INDEPENDENTLY MERGEABLE via this seam:
    - Engineer ships this stub (SR-FIG plumbing PR).
    - Iris ships the real implementation against the same signature.
  Keep the signature exactly `apply_style(preset, skin)`.

Presets:
  publication  — camera-ready paper figures (tight margins, serif fonts, print-safe palette)
  slide        — presentation slides (larger text, high-contrast, screen palette)
  poster       — conference poster (bold labels, large figures, accessible colors)

Skin:
  A per-project accent applied on top of the shared preset grammar.
  Example: skin="hfs" → project-specific palette accent (Iris's implementation).
  None or unknown → use the preset defaults.

This stub sets minimal sensible rcParams so renders are not raw matplotlib defaults.
The real Iris implementation replaces these with the BeautifulFigures-derived grammar.

Stdlib only in this stub (no matplotlib import at module level — guarded per [figures] extra).
"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# STUB — minimal default rcParams (Iris replaces this with the real aesthetic)
# ---------------------------------------------------------------------------

# Minimal preset rcParam overrides — enough to not look like raw matplotlib defaults.
# Iris's version will be far richer (BeautifulFigures + frontend-design skin).
_PRESET_RCPARAMS: dict[str, dict[str, Any]] = {
    "publication": {
        "figure.figsize": (8, 5),
        "font.size": 10,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
    },
    "slide": {
        "figure.figsize": (12, 7),
        "font.size": 14,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.2,
        "savefig.dpi": 150,
        "savefig.bbox": "tight",
    },
    "poster": {
        "figure.figsize": (14, 9),
        "font.size": 16,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.2,
        "savefig.dpi": 200,
        "savefig.bbox": "tight",
    },
}

_KNOWN_PRESETS = frozenset(_PRESET_RCPARAMS)


def apply_style(preset: str, skin: str | None) -> dict[str, Any] | None:
    """Apply the named preset + per-project skin to the current matplotlib rcParams.

    SEAM SIGNATURE — Iris replaces the body; the signature must not change.

    Args:
      preset: named style preset — "publication" | "slide" | "poster".
              Unknown presets fall back to "publication".
      skin:   per-project accent identifier (e.g. project slug). Currently a no-op
              in this stub — Iris's implementation maps this to palette/font accents.

    Returns:
      dict of rcParam keys set (the applied overrides), or None if matplotlib
      is not installed (the [figures] extra is absent). Callers may ignore the
      return value; the side-effect on matplotlib.rcParams is what matters.

    Stub behaviour (Iris replaces this):
      - Applies a minimal set of rcParams that strip raw matplotlib defaults.
      - Does NOT touch fonts, palettes, or anything requiring matplotlib internals.
      - If matplotlib is absent, returns None silently (no ImportError).
    """
    params = _PRESET_RCPARAMS.get(preset, _PRESET_RCPARAMS["publication"])

    try:
        import matplotlib as mpl
        mpl.rcParams.update(params)
    except ImportError:
        # [figures] extra not installed — stub returns None silently.
        # The verb's import-guard fires before render is called, so this
        # path only executes in test scenarios without matplotlib.
        return None

    # skin is intentionally unused in this stub — Iris's implementation applies
    # per-project palette/font accents here (e.g. colour from skin.<project>).
    _ = skin  # noqa: F841

    return dict(params)
