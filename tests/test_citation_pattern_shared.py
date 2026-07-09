# SPDX-License-Identifier: AGPL-3.0-or-later
"""test_citation_pattern_shared.py — pre-publish hardening followup (#200).

``bib.py`` and ``fidelity_gates.py`` both used a byte-identical
``_WIKILINK_CITE_RE`` regex for the ``[[citekey]]`` wikilink citation
syntax. Hoisted to ``manuscript.citation_pattern.WIKILINK_CITE_RE`` — this
test pins both modules to the SAME compiled pattern object (no drift
possible) and exercises the pattern itself.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.manuscript import bib, citation_pattern, fidelity_gates

_SRC_MANUSCRIPT_DIR = Path(__file__).parent.parent / "src" / "research_vault" / "manuscript"

# The literal pattern string — a byte-identical ``re.compile(...)`` call
# appearing in more than ONE file is exactly the drift this hoist prevents.
# NOTE: ``re.compile`` caches compiled Pattern objects by (pattern, flags),
# so ``is``-identity between two independently-``re.compile``d instances of
# the SAME string is true regardless of whether the definition is actually
# shared — that check alone is vacuous and would pass even pre-hoist. This
# source-level count is the sound regression pin.
_PATTERN_LITERAL = r'r"\[\[([A-Za-z0-9_.\-]+)\]\]"'


def test_pattern_defined_in_exactly_one_source_file():
    defining_files = [
        p for p in _SRC_MANUSCRIPT_DIR.glob("*.py")
        if _PATTERN_LITERAL in p.read_text(encoding="utf-8")
    ]
    assert [p.name for p in defining_files] == ["citation_pattern.py"], (
        f"expected the wikilink regex literal to be defined in exactly one "
        f"file (citation_pattern.py); found it in: {[p.name for p in defining_files]}"
    )


def test_bib_and_fidelity_gates_import_the_shared_symbol():
    assert bib._WIKILINK_CITE_RE is citation_pattern.WIKILINK_CITE_RE
    assert fidelity_gates._WIKILINK_CITE_RE is citation_pattern.WIKILINK_CITE_RE


def test_wikilink_pattern_matches_citekey():
    m = list(citation_pattern.WIKILINK_CITE_RE.finditer("see [[smith2023]] and [[jones.2022-a]]"))
    assert [g.group(1) for g in m] == ["smith2023", "jones.2022-a"]
