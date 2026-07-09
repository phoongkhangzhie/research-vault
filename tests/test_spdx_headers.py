# SPDX-License-Identifier: AGPL-3.0-or-later
"""test_spdx_headers.py — pre-publish hardening: enforce the AGPL SPDX
header on every shipped source file.

research-vault is licensed AGPL-3.0-or-later (v0.3.0), and every existing
``.py`` file under ``src/research_vault`` carries
``# SPDX-License-Identifier: AGPL-3.0-or-later`` as its first line — but
until this test, nothing checked that mechanically. A future file could
ship header-less in the packaged wheel and nobody would notice (a green
gate that should be red). This test globs the real shipped source tree and
fails loudly on any file missing the exact header line.
"""
from __future__ import annotations

from pathlib import Path

_SPDX_LINE = "# SPDX-License-Identifier: AGPL-3.0-or-later"
_SRC_ROOT = Path(__file__).parent.parent / "src" / "research_vault"

# Files that are deliberately exempt (e.g. generated / vendored / non-code)
# — empty today; every real source file under src/research_vault carries
# the header. Keep this explicit and documented rather than a silent glob
# exclusion, so any future addition here is a conscious, reviewable choice.
_EXEMPT_RELATIVE_PATHS: frozenset[str] = frozenset()


def _all_py_files() -> list[Path]:
    return sorted(_SRC_ROOT.rglob("*.py"))


def test_every_source_file_carries_the_spdx_header():
    missing = []
    for path in _all_py_files():
        rel = str(path.relative_to(_SRC_ROOT))
        if rel in _EXEMPT_RELATIVE_PATHS:
            continue
        text = path.read_text(encoding="utf-8")
        first_line = text.splitlines()[0] if text.splitlines() else ""
        if first_line.strip() != _SPDX_LINE:
            missing.append(rel)
    assert missing == [], (
        f"{len(missing)} source file(s) missing the AGPL SPDX header as "
        f"their first line: {missing}"
    )


def test_source_tree_is_non_empty():
    # Guard against a vacuous pass (glob returning zero files silently
    # "succeeding" the assertion above).
    assert len(_all_py_files()) > 50
