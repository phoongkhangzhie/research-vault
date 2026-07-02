"""figures — SR-FIG style seam and figure plumbing sub-package.

The `style` module exports `apply_style(preset, skin)` — the seam that Iris
replaces with the real aesthetic (BeautifulFigures adaptation + frontend-design skin).
The plumbing calls `apply_style` before plotting; Iris owns what it does.

Stdlib only (no matplotlib/pandas at import time — guarded behind the [figures] extra).
"""
