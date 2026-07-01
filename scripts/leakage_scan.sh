#!/usr/bin/env bash
# leakage_scan.sh — full-teeth private-marker scan for the research-vault framework repo.
#
# Exit 0 = clean (safe to ship).  Exit 1 = private marker found (RED BUILD).
#
# Usage:
#   ./scripts/leakage_scan.sh [directory]   # default: doctrine/
#
# Marker classes:
#   1. Private project codenames  (cultural-social-sim, csb, dossier)
#   2. Private identity strings   (operator name + handles)
#   3. Private site / URLs        (operator's personal domain)
#   4. Private cluster paths      (/juice2/, /scr2/)
#   5. Secret-shaped strings      (known secret env-var names, API-key prefixes)
#   6. Versioned model IDs        (pinned claude-*-N strings belong in private config, not doctrine)
#   7. Placeholder-template lint  (memory.md files must not contain real private journal content)
#
# Self-exclusion: the scanner skips itself, ci.yml, and the test file
# (all three intentionally list the marker strings).

set -uo pipefail

TARGET="${1:-doctrine}"
FAIL=0

# ── core helpers ──────────────────────────────────────────────────────────────
# All helpers write any found lines to stdout, set FAIL=1, and return.
# NEVER use `set -e` around grep calls — grep exits 1 when no match (not an error here).

SKIP_PATTERN='(leakage_scan\.sh|\.github/workflows/ci\.yml|tests/test_leakage_scan\.py)'

_grep_literal() {
    # _grep_literal LABEL LITERAL
    local label="$1" lit="$2"
    local found
    found=$(grep -rn --include="*.md" --include="*.yml" --include="*.yaml" \
                 --include="*.toml" --include="*.json" --include="*.py" \
                 -F "$lit" "$TARGET" 2>/dev/null \
            | grep -Ev "$SKIP_PATTERN" || true)
    if [ -n "$found" ]; then
        echo "$found"
        echo "FAIL [$label]: literal '$lit' matched in $TARGET"
        FAIL=1
    fi
}

_grep_word() {
    # _grep_word LABEL WORD  (whole-word, case-insensitive)
    local label="$1" word="$2"
    local found
    found=$(grep -rn --include="*.md" --include="*.yml" --include="*.yaml" \
                 --include="*.toml" --include="*.json" --include="*.py" \
                 -wi "$word" "$TARGET" 2>/dev/null \
            | grep -Ev "$SKIP_PATTERN" || true)
    if [ -n "$found" ]; then
        echo "$found"
        echo "FAIL [$label]: whole-word '$word' (case-insensitive) matched in $TARGET"
        FAIL=1
    fi
}

_grep_re() {
    # _grep_re LABEL ERE_PATTERN
    local label="$1" pattern="$2"
    local found
    found=$(grep -rn --include="*.md" --include="*.yml" --include="*.yaml" \
                 --include="*.toml" --include="*.json" --include="*.py" \
                 -E "$pattern" "$TARGET" 2>/dev/null \
            | grep -Ev "$SKIP_PATTERN" || true)
    if [ -n "$found" ]; then
        echo "$found"
        echo "FAIL [$label]: regex '$pattern' matched in $TARGET"
        FAIL=1
    fi
}

echo "=== Leakage scan: $TARGET ==="

# ── Class 1: Private project codenames ───────────────────────────────────────
_grep_literal "codename/cultural-social-sim" "cultural-social-sim"
_grep_word    "codename/csb"                 "csb"
_grep_word    "codename/dossier"             "dossier"

# ── Class 2: Private identity strings ────────────────────────────────────────
_grep_word    "identity/khang"              "khang"
_grep_word    "identity/phoong"             "phoong"
_grep_literal "identity/phoongkz"           "phoongkz"
_grep_literal "identity/phoongkhangzhie"    "phoongkhangzhie"
# Institutional affiliation — operator's affiliation must not appear in portable doctrine.
_grep_word    "identity/stanford"           "stanford"

# ── Class 3: Private site / URLs ─────────────────────────────────────────────
_grep_literal "site/khangzhie.io"  "khangzhie.io"

# ── Class 4: Private cluster paths ───────────────────────────────────────────
_grep_literal "cluster//juice2"  "/juice2/"
_grep_literal "cluster//scr2"    "/scr2/"

# ── Class 5: Secret-shaped strings ───────────────────────────────────────────
# Known secret env-var names used in private bridge config
_grep_literal "secret/DRAIN_SECRET"    "DRAIN_SECRET"
_grep_literal "secret/WEBHOOK_SECRET"  "WEBHOOK_SECRET"
# Anthropic API-key prefix in plain text
_grep_re      "secret/sk-ant"          "sk-ant-[A-Za-z0-9_-]+"

# ── Class 6: Versioned model IDs (per-role model roster) ─────────────────────
# Doctrine states model tiers abstractly (Sonnet/Opus/Haiku — fine).
# Pinned version strings belong in private config, not portable doctrine.
# Catches: claude-sonnet-4-6, claude-3-5-sonnet-20241022, us.anthropic.claude-*
# Pattern covers: simple versioned (claude-<name>-N-N), date-versioned (claude-<...>-YYYYMMDD),
# and AWS Bedrock paths (us.anthropic.claude-*).
_grep_re      "model-roster/versioned-id" \
    "(claude-[a-z0-9-]+-[0-9]{8}|claude-[a-z]+-[0-9]+-[0-9]+|us\.anthropic\.claude)"

# ── Class 7: Placeholder-template lint ───────────────────────────────────────
# memory.md files inside doctrine/ must be template stubs, not real private entries.
PRIVATE_MEMORY_SLUGS=(
    "khang-qa"
    "khang-researcher"
    "khang-bio"
    "keeper-journal"
    "compounding-os"
    "chief-of-staff"
)
for slug in "${PRIVATE_MEMORY_SLUGS[@]}"; do
    if find "$TARGET" -name "memory.md" -exec grep -l "$slug" {} \; 2>/dev/null | grep -q .; then
        echo "FAIL [memory-template/$slug]: private memory slug '$slug' found in a doctrine/ memory.md"
        FAIL=1
    fi
done

# ── Result ────────────────────────────────────────────────────────────────────
echo ""
if [ "$FAIL" -eq 0 ]; then
    echo "OK: leakage scan clean — no private markers found in $TARGET"
    exit 0
else
    echo "ERROR: private markers found. The framework repo must be portable."
    echo "       Scrub all matches above before pushing."
    exit 1
fi
