#!/usr/bin/env bash
# leakage_scan.sh — full-teeth private-marker scan for the research-vault framework repo.
#
# Exit 0 = clean (safe to ship).  Exit 1 = private marker found (RED BUILD).
#
# Usage:
#   ./scripts/leakage_scan.sh [directory]             # default: doctrine/ (CI mode)
#   ./scripts/leakage_scan.sh --staged                # scan only git-staged files (pre-commit)
#   ./scripts/leakage_scan.sh --staged --secrets-only # staged, class 5 only (project-repo profile)
#
# Flags (order-independent, may come before or after the directory argument):
#   --staged        Scan only files staged for commit (`git diff --cached --name-only`)
#                   instead of recursing over a directory. Fast; scans what's being committed.
#   --secrets-only  Run only class 5 (secret-shaped strings). Used for project-repo pre-commit
#                   profiles where private-marker classes 1-4,6-9 gate the researcher's own
#                   possibly-private content (which must NOT be gated by codename checks).
#
# Marker classes:
#   1. Private project codenames  (cultural-social-sim, csb, dossier)
#   2. Private identity strings   (operator name + handles)
#   3. Private site / URLs        (operator's personal domain)
#   4. Private cluster paths      (/juice2/, /scr2/)
#   5. Secret-shaped strings      (known secret env-var names, API-key prefixes)
#   6. Versioned model IDs        (pinned claude-*-N strings belong in private config, not doctrine)
#   7. Placeholder-template lint  (memory.md files must not contain real private journal content)
#   8. Real citekeys              (Pandoc [@key] citations reveal private bibliography)
#   9. Real projects.json entries (private project registry slugs/codes not in class 1)
#
# Self-exclusion: the scanner skips itself, ci.yml, and the test file
# (all three intentionally list the marker strings).

set -uo pipefail

# ── Flag parsing ──────────────────────────────────────────────────────────────
STAGED=0
SECRETS_ONLY=0
TARGET=""

for arg in "$@"; do
    case "$arg" in
        --staged)        STAGED=1 ;;
        --secrets-only)  SECRETS_ONLY=1 ;;
        -*)
            echo "leakage_scan.sh: unknown flag: $arg" >&2
            exit 2
            ;;
        *)
            if [ -z "$TARGET" ]; then
                TARGET="$arg"
            fi
            ;;
    esac
done

# Default target for directory mode
if [ -z "$TARGET" ] && [ "$STAGED" -eq 0 ]; then
    TARGET="doctrine"
fi

FAIL=0

SKIP_PATTERN='(leakage_scan\.sh|\.github/workflows/ci\.yml|tests/test_leakage_scan\.py|tests/test_git_discipline\.py)'

# ── Staged file list ──────────────────────────────────────────────────────────
# In staged mode, build a filtered list of staged files to scan.
STAGED_FILES=""
if [ "$STAGED" -eq 1 ]; then
    # Get staged paths; filter to tracked extensions
    STAGED_FILES=$(git diff --cached --name-only 2>/dev/null \
        | grep -E '\.(md|yml|yaml|toml|json|py|sh)$' \
        | grep -v '__pycache__' \
        || true)
    if [ -z "$STAGED_FILES" ]; then
        echo "=== Leakage scan (staged): no matching staged files — OK ==="
        exit 0
    fi
    echo "=== Leakage scan (staged: $(echo "$STAGED_FILES" | wc -l | tr -d ' ') file(s)) ==="
else
    echo "=== Leakage scan: $TARGET ==="
fi

# ── core helpers ──────────────────────────────────────────────────────────────
# All helpers write any found lines to stdout, set FAIL=1, and return.
# Two scan modes: directory-recursive (default) and file-list (--staged).

_grep_literal() {
    # _grep_literal LABEL LITERAL
    local label="$1" lit="$2"
    local found
    if [ "$STAGED" -eq 1 ]; then
        found=$(echo "$STAGED_FILES" | xargs -I{} grep -nH -F "$lit" {} 2>/dev/null \
                | grep -Ev "$SKIP_PATTERN" || true)
    else
        found=$(grep -rn --include="*.md" --include="*.yml" --include="*.yaml" \
                     --include="*.toml" --include="*.json" --include="*.py" \
                     -F "$lit" "$TARGET" 2>/dev/null \
                | grep -Ev "$SKIP_PATTERN" || true)
    fi
    if [ -n "$found" ]; then
        echo "$found"
        echo "FAIL [$label]: literal '$lit' matched"
        FAIL=1
    fi
}

_grep_word() {
    # _grep_word LABEL WORD  (whole-word, case-insensitive)
    local label="$1" word="$2"
    local found
    if [ "$STAGED" -eq 1 ]; then
        found=$(echo "$STAGED_FILES" | xargs -I{} grep -nH -wi "$word" {} 2>/dev/null \
                | grep -Ev "$SKIP_PATTERN" || true)
    else
        found=$(grep -rn --include="*.md" --include="*.yml" --include="*.yaml" \
                     --include="*.toml" --include="*.json" --include="*.py" \
                     -wi "$word" "$TARGET" 2>/dev/null \
                | grep -Ev "$SKIP_PATTERN" || true)
    fi
    if [ -n "$found" ]; then
        echo "$found"
        echo "FAIL [$label]: whole-word '$word' (case-insensitive) matched"
        FAIL=1
    fi
}

_grep_re() {
    # _grep_re LABEL ERE_PATTERN
    local label="$1" pattern="$2"
    local found
    if [ "$STAGED" -eq 1 ]; then
        found=$(echo "$STAGED_FILES" | xargs -I{} grep -nH -E "$pattern" {} 2>/dev/null \
                | grep -Ev "$SKIP_PATTERN" || true)
    else
        found=$(grep -rn --include="*.md" --include="*.yml" --include="*.yaml" \
                     --include="*.toml" --include="*.json" --include="*.py" \
                     -E "$pattern" "$TARGET" 2>/dev/null \
                | grep -Ev "$SKIP_PATTERN" || true)
    fi
    if [ -n "$found" ]; then
        echo "$found"
        echo "FAIL [$label]: regex '$pattern' matched"
        FAIL=1
    fi
}

# ── Class 5 always runs (secrets scan applies everywhere) ────────────────────
# Run this block always (both modes); the other classes are skipped in --secrets-only.

# ── Class 5: Secret-shaped strings ───────────────────────────────────────────
# Known secret env-var names used in private bridge config
_grep_literal "secret/DRAIN_SECRET"    "DRAIN_SECRET"
_grep_literal "secret/WEBHOOK_SECRET"  "WEBHOOK_SECRET"
# Anthropic API-key prefix in plain text
_grep_re      "secret/sk-ant"          "sk-ant-[A-Za-z0-9_-]+"

# ── Private-marker classes (framework-repo-only; skipped in --secrets-only) ──
if [ "$SECRETS_ONLY" -eq 0 ]; then

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
if [ "$STAGED" -eq 0 ]; then
    # Directory mode: use find
    for slug in "${PRIVATE_MEMORY_SLUGS[@]}"; do
        if find "$TARGET" -name "memory.md" -exec grep -l "$slug" {} \; 2>/dev/null | grep -q .; then
            echo "FAIL [memory-template/$slug]: private memory slug '$slug' found in a doctrine/ memory.md"
            FAIL=1
        fi
    done
else
    # Staged mode: check staged memory.md files
    staged_mem=$(echo "$STAGED_FILES" | grep 'memory\.md' || true)
    if [ -n "$staged_mem" ]; then
        for slug in "${PRIVATE_MEMORY_SLUGS[@]}"; do
            found_mem=$(echo "$staged_mem" | xargs -I{} grep -l "$slug" {} 2>/dev/null || true)
            if [ -n "$found_mem" ]; then
                echo "FAIL [memory-template/$slug]: private memory slug '$slug' in staged memory.md"
                FAIL=1
            fi
        done
    fi
fi

# ── Class 8: Real citekeys ───────────────────────────────────────────────────
# Pandoc inline-citation format: [@key] — private bibliography keys must not
# appear in portable doctrine. Any [@<letter>… form is a private citekey reference
# (citekeys identify specific papers in the operator's private Zotero library).
_grep_re "citekey/pandoc-citation" '\[@[A-Za-z][A-Za-z0-9_:-]+'

# ── Class 9: Real projects.json entries ──────────────────────────────────────
# The vault's project registry contains private slugs not fully covered by class 1.
# "_hub" is the hub-infrastructure registry key; "dsr" is the dossier project code.
_grep_literal "projects-json/_hub"     '"_hub"'
_grep_literal "projects-json/dsr-code" '"code": "dsr"'

fi  # end SECRETS_ONLY=0 block

# ── Result ────────────────────────────────────────────────────────────────────
echo ""
if [ "$FAIL" -eq 0 ]; then
    echo "OK: leakage scan clean — no private markers found"
    exit 0
else
    echo "ERROR: private markers found. The framework repo must be portable."
    echo "       Scrub all matches above before pushing."
    exit 1
fi
