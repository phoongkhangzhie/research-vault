# SPDX-License-Identifier: AGPL-3.0-or-later
"""citation_pattern.py — the single source of truth for the manuscript
loop's ``[[citekey]]`` wikilink citation syntax.

Pre-publish hardening followup: ``WIKILINK_CITE_RE`` used to be
byte-identically duplicated in ``manuscript/bib.py`` and
``manuscript/fidelity_gates.py`` (each with a comment explaining the
duplication was to dodge an import cycle). Neither module actually imports
the other, so there was no cycle to dodge — both are leaves imported by
``manuscript/check_gates.py``. Hoisted here so the two copies can never
drift apart (reuse, don't proliferate).

Stdlib only; this module has no other intra-package imports, so importing
it from anywhere in ``manuscript/`` is cycle-free by construction.
"""
from __future__ import annotations

import re

# The markdown render target's ONLY citation syntax — one citekey per
# wikilink (no multi-cite comma form): `Smith [[smith2023]] and Jones
# [[jones2022]]`, never a bundled `[[smith2023,jones2022]]`.
WIKILINK_CITE_RE = re.compile(r"\[\[([A-Za-z0-9_.\-]+)\]\]")
