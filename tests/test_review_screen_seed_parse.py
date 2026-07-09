# SPDX-License-Identifier: AGPL-3.0-or-later
"""test_review_screen_seed_parse.py — regression test for the
review-snowball tool op's ``_screen.md`` seed-id extraction.

Bug (v0.3.0 validation run, BLOCKING): ``_op_snowball`` extracted seed ids
with a naive "one id per non-empty, non-#, non-| line" scan over the WHOLE
``_screen.md`` file. But ``_screen.md`` is a real OKF-shaped note — YAML
frontmatter (``---`` delimiters) + a prose PRISMA exclusion audit trail +
the accepted ids — so the naive scan collected ``---`` and prose sentences
as "seed ids" and handed them to asta, which crashed
(``Error: No such option '---'``) because asta parses a leading ``-`` as a
CLI flag.

The fix (review-loop node-kind drift's screen/snowball contract, §5, this
fix): ``review_screen_tips`` now documents a fenced ```seeds``` block as the
canonical, unambiguous home for the accepted seed ids — the prose audit
trail lives freely above it. ``_extract_seed_ids_from_screen`` reads ONLY
that block when present, and additionally validates every token against
the paper-id shapes ``research.py`` already recognizes (reuse, charter
§6) — so even a malformed/legacy ``_screen.md`` can never hand a `---` or
a prose sentence to asta.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from research_vault.review.autonomy import _extract_seed_ids_from_screen  # noqa: E402
from research_vault.review.style import get_review_tips  # noqa: E402


_REALISTIC_SCREEN_MD = """---
run_id: r1
node_id: review-screen
---

# Screen: Does X generalize across Y?

## Exclusion audit trail

- [EXCLUDE] 10.1000/notrelevant — off-topic, does not address the RQ per protocol criterion C2.
- [EXCLUDE] 10.1000/wrongpop — wrong population (protocol criterion C1: only human-subject studies).
- A follow-up sentence continuing the audit trail prose without a leading dash.

## Accepted seeds

```seeds
2407.16891
10.48550/arxiv.2408.06929
10.1000/realseed1
```
"""


class TestFencedSeedBlockExtraction:
    """The canonical path: a realistic _screen.md with frontmatter + prose
    + a fenced ```seeds``` block. This is the regression test for the crash
    — before the fix, `---` and prose lines were collected as seed ids."""

    def test_extracts_exactly_the_fenced_ids(self):
        ids = _extract_seed_ids_from_screen(_REALISTIC_SCREEN_MD)
        assert ids == ["2407.16891", "10.48550/arxiv.2408.06929", "10.1000/realseed1"]

    def test_never_extracts_frontmatter_delimiter(self):
        ids = _extract_seed_ids_from_screen(_REALISTIC_SCREEN_MD)
        assert "---" not in ids

    def test_never_extracts_prose_audit_trail_lines(self):
        ids = _extract_seed_ids_from_screen(_REALISTIC_SCREEN_MD)
        for tok in ids:
            assert "EXCLUDE" not in tok
            assert "off-topic" not in tok
            assert "follow-up sentence" not in tok

    def test_never_extracts_frontmatter_keys(self):
        ids = _extract_seed_ids_from_screen(_REALISTIC_SCREEN_MD)
        assert "run_id: r1" not in ids
        assert "node_id: review-screen" not in ids


class TestNeverEmitsADashPrefixedToken:
    """Defensive guard (brief item 2): even a corrupted or hand-edited
    fenced block must never let a token beginning with '-' reach asta as a
    seed id — asta parses a leading '-' as a CLI flag and crashes."""

    def test_dash_prefixed_line_inside_fenced_block_is_dropped(self):
        text = (
            "```seeds\n"
            "---\n"
            "-a-flag-looking-token\n"
            "2407.16891\n"
            "```\n"
        )
        ids = _extract_seed_ids_from_screen(text)
        assert ids == ["2407.16891"]
        for tok in ids:
            assert not tok.startswith("-")

    def test_bullet_line_leaked_into_fenced_block_is_dropped(self):
        text = (
            "```seeds\n"
            "- 2407.16891\n"
            "10.1000/realseed1\n"
            "```\n"
        )
        ids = _extract_seed_ids_from_screen(text)
        # "- 2407.16891" doesn't match a bare paper-id pattern (leading
        # dash+space) so it's dropped rather than passed through raw.
        assert "10.1000/realseed1" in ids
        for tok in ids:
            assert not tok.startswith("-")


class TestLegacyBareIdFallback:
    """Backward compat (brief item 3): an old _screen.md with no fenced
    block at all — bare ids, one per line, no frontmatter — still works,
    filtered by the same id-pattern validation (never a blanket
    accept-everything scan)."""

    def test_bare_id_file_with_no_fenced_block_still_extracts(self):
        text = "10.1000/legacyseed1\n10.1000/legacyseed2\n"
        ids = _extract_seed_ids_from_screen(text)
        assert ids == ["10.1000/legacyseed1", "10.1000/legacyseed2"]

    def test_legacy_fallback_still_rejects_frontmatter_and_prose(self):
        # An old-shaped file that ALSO happens to carry frontmatter (no
        # fenced block) must not regress to the pre-fix crash — the
        # id-pattern validation is the actual gate now, not the fence.
        text = (
            "---\n"
            "run_id: r1\n"
            "---\n"
            "This is a free prose sentence, not a paper id.\n"
            "10.1000/legacyseed1\n"
        )
        ids = _extract_seed_ids_from_screen(text)
        assert ids == ["10.1000/legacyseed1"]

    def test_legacy_fallback_still_supports_comment_and_table_row_skip(self):
        text = (
            "# a heading, not an id\n"
            "| annotation | citekey |\n"
            "10.1000/legacyseed1\n"
        )
        ids = _extract_seed_ids_from_screen(text)
        assert ids == ["10.1000/legacyseed1"]


class TestEmptyAndMissingInput:
    def test_empty_text_returns_empty_list(self):
        assert _extract_seed_ids_from_screen("") == []

    def test_whitespace_only_text_returns_empty_list(self):
        assert _extract_seed_ids_from_screen("   \n\n  \n") == []


class TestScreenTipsDocumentsFencedSeedsBlock:
    """The contract (fenced ```seeds``` block) must be explicit in the
    agent-facing tips, not just enforced silently by the parser — an agent
    that never reads it would keep writing the old bare-id shape."""

    def test_tips_mention_the_seeds_fence(self):
        tips = get_review_tips(config=None)
        screen_tips = tips["review_screen_tips"]
        assert "```seeds" in screen_tips

    def test_tips_still_require_the_prose_audit_trail(self):
        # The prose PRISMA exclusion audit trail is load-bearing (§ WHY)
        # and must NOT be dropped by the fenced-block fix.
        tips = get_review_tips(config=None)
        screen_tips = tips["review_screen_tips"]
        assert "audit trail" in screen_tips.lower()
