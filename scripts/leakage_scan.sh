#!/usr/bin/env bash
# leakage_scan.sh — full-teeth private-marker scan for the research-vault framework repo.
#
# Exit 0 = clean (safe to ship).  Exit 1 = private marker found (RED BUILD).
#
# Usage:
#   ./scripts/leakage_scan.sh [directory]               # default: doctrine/ (CI mode)
#   ./scripts/leakage_scan.sh --staged                  # scan only git-staged files (pre-commit)
#   ./scripts/leakage_scan.sh --staged --secrets-only   # staged, class 5 only (project-repo profile)
#   ./scripts/leakage_scan.sh tests --codenames-only    # tests/ scan, class 1 only (see below)
#
# Flags (order-independent, may come before or after the directory argument):
#   --staged          Scan only files staged for commit (`git diff --cached --name-only`)
#                     instead of recursing over a directory. Fast; scans what's being committed.
#   --secrets-only    Run only class 5 (secret-shaped strings). Used for project-repo pre-commit
#                     profiles where private-marker classes 1-4,6-9 gate the researcher's own
#                     possibly-private content (which must NOT be gated by codename checks).
#   --codenames-only  Run ONLY class 1 (private project codenames). Used for scanning tests/
#                     in CI: tests/*.py legitimately contain fake secrets (test_keys_registry.py,
#                     test_onboard_verb.py — sk-ant-* fixtures for masking-behavior tests),
#                     casual operator-name comments (class 2), and versioned-model-id fixtures
#                     (class 6) that are NOT leaks — they're accepted test-fixture conventions.
#                     Running the FULL scan over tests/ would flood with those false positives;
#                     the actual root-cause bug (class-1 codenames like "cultural-social-sim"
#                     landing in tests/test_config.py etc.) needs only class 1's narrower net.
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
#  10. Crew narrative-names in .py (Ada/Wren/Mason/Argus/Iris/Atlas must not appear in Python
#      source). Scoped away from tests/ (in addition to src/-only CI wiring): these names are
#      ALSO the framework's own shipped git-identity + role-alias convention (doctrine/
#      git-discipline.md's `--as mason` example, doctrine/roles/*.md), which the test suite
#      legitimately exercises (git-identity tests, entry_id fixtures, role-doc cross-refs).
#      tests/ is never shipped in the wheel, so this cannot leak into the published package.
#  11. Private local dev-paths (~/vault, docs/superpowers/ internal-spec refs) — a shipped
#      doctrine/root .md file that cites an author-local path (the operator's hub instance
#      or its internal, unshipped spec directory) tells an adopter to go look at a path they
#      don't have. Scoped to NON-.py files (mirrors class 10's inverse: internal-spec-path /
#      boundary-safety comments in Python source are an established, accepted development-
#      history convention, out of scope here). No grandfather exemption (PR-C2: DEVLOG.md,
#      the only file that carried one, is now untracked and out of the scanned surface).
#  12. Internal dev-process references in Python source — "charter §N" (an internal
#      governance-doc citation), bare internal spec/decision labels (D-4e, K-D1, SR-XPB),
#      internal PR/task numbers, "design of record"/"internal design note", and dangling
#      pointers to unshipped design-doc filenames (YYYY-MM-DD-...-design.md). A public
#      wheel's Python source should never read like an internal changelog. Scoped .py-only
#      via _grep_re_py (mirrors class 10): shipped doctrine .md legitimately self-references
#      its OWN numbered sections (agent-charter.md's "§N", note-conventions.md's "#N" list
#      items) — those are real, shipped, non-dangling cross-references, out of scope here.
#
# Self-exclusion: the scanner skips itself, ci.yml, and the test file
# (all three intentionally list the marker strings). tests/test_git_discipline.py is also
# self-excluded — like tests/test_leakage_scan.py, its fixtures must contain the REAL class-1
# codename literals (cultural-social-sim etc.) to prove the framework-repo-vs-project-repo
# scanning-profile distinction actually fires; this is the detector's own test, not a leak.

set -uo pipefail

# ── Flag parsing ──────────────────────────────────────────────────────────────
STAGED=0
SECRETS_ONLY=0
CODENAMES_ONLY=0
TARGET=""

for arg in "$@"; do
    case "$arg" in
        --staged)          STAGED=1 ;;
        --secrets-only)    SECRETS_ONLY=1 ;;
        --codenames-only)  CODENAMES_ONLY=1 ;;
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

if [ "$SECRETS_ONLY" -eq 1 ] && [ "$CODENAMES_ONLY" -eq 1 ]; then
    echo "leakage_scan.sh: --secrets-only and --codenames-only are mutually exclusive" >&2
    exit 2
fi

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
    # Get staged paths; filter to tracked extensions.
    # Exclude manuscripts/** — LaTeX .tex/.bib files use \cite{} form (NOT Pandoc [@key])
    # and are intentionally not portable-doctrine artifacts. The .tex/.bib extensions
    # are already excluded by the .md/.yml/.toml/.json/.py/.sh filter, but manuscript/
    # notes (OKF metadata .md files) are also safe — they contain no [@key] patterns.
    # Exclusion documented (SR-MS-1b §5J.3): "LaTeX \cite{}+.bib does NOT match
    # Pandoc [@key] — safe-by-construction. Exclude manuscripts/** as belt-and-suspenders."
    STAGED_FILES=$(git diff --cached --name-only 2>/dev/null \
        | grep -E '\.(md|yml|yaml|toml|json|py|sh)$' \
        | grep -v '__pycache__' \
        | grep -v '^.*/manuscripts/' \
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

_grep_word_except() {
    # _grep_word_except LABEL WORD ALLOW_ERE [FILE_PAT]
    # Like _grep_word (whole-word, case-insensitive) but masks ALLOW_ERE from
    # each matching line's content (awk gsub) before re-checking.
    #
    # FILE_PAT (optional, awk ERE) scopes the masking to files whose grep-output
    # filename field ($1) matches FILE_PAT.  When FILE_PAT is provided, masking is
    # applied ONLY for matching files — all other files are checked strictly with
    # no masking.  When FILE_PAT is omitted (empty string), masking applies to
    # every file (backward-compatible behaviour).
    #
    # Scoping masking to the specific file that legitimately contains the identity
    # string (e.g. pyproject.toml for PyPI author metadata) prevents the allowlist
    # from silently suppressing detections in every other file type.
    #
    # -H is REQUIRED (not cosmetic): GNU grep on a single explicit non-directory
    # file argument (CI's per-file invocations, e.g. `leakage_scan.sh README.md`)
    # omits the filename prefix by default even with -r — this shifts $1 to the
    # line number and silently breaks FILE_PAT matching (and, worse, can silently
    # drop the check entirely via the NF>=3 guard on colon-less content). BSD grep
    # (macOS) always includes it, so this only surfaces on Linux CI. Found via
    # the class-11 CI failure on PR #195; -H makes the filename position
    # deterministic on both platforms.
    #
    # After masking, a case-insensitive substring check confirms the word survived.
    # This is equivalent to word-boundary matching in practice: if the word only
    # appeared inside the masked phrase, no substring remains after gsub.
    local label="$1" word="$2" allow_ere="$3" file_pat="${4:-}"
    local found
    local _MASK_AWK='NF>=3{
        content=$3; for(i=4;i<=NF;i++) content=content":"$i
        if(file_pat=="" || $1 ~ file_pat) { gsub(allow_ere, "", content) }
        if(index(tolower(content), tolower(word))>0) print $0
    }'
    if [ "$STAGED" -eq 1 ]; then
        found=$(echo "$STAGED_FILES" | xargs -I{} grep -nH -wi "$word" {} 2>/dev/null \
                | grep -Ev "$SKIP_PATTERN" \
                | awk -F: -v word="$word" -v allow_ere="$allow_ere" -v file_pat="$file_pat" "$_MASK_AWK" || true)
    else
        found=$(grep -rnH --include="*.md" --include="*.yml" --include="*.yaml" \
                     --include="*.toml" --include="*.json" --include="*.py" \
                     -wi "$word" "$TARGET" 2>/dev/null \
                | grep -Ev "$SKIP_PATTERN" \
                | awk -F: -v word="$word" -v allow_ere="$allow_ere" -v file_pat="$file_pat" "$_MASK_AWK" || true)
    fi
    if [ -n "$found" ]; then
        echo "$found"
        echo "FAIL [$label]: whole-word '$word' (case-insensitive) matched (outside allowlisted contexts)"
        FAIL=1
    fi
}

_grep_literal_except_re() {
    # _grep_literal_except_re LABEL LITERAL ALLOW_ERE [FILE_PAT]
    # Like _grep_literal but masks a caller-provided awk ERE from each matching
    # line's content before re-checking. More general than _grep_literal_except
    # (which hardcodes the canonical GitHub URL mask).
    #
    # FILE_PAT (optional, awk ERE): when provided, masking is applied ONLY for
    # files whose grep-output filename field ($1) matches FILE_PAT.  All other
    # files are checked strictly with no masking.  Omitting FILE_PAT (empty
    # string) applies masking to every file (backward-compatible).
    local label="$1" lit="$2" allow_ere="$3" file_pat="${4:-}"
    local found
    local _MASK_AWK='NF>=3{
        content=$3; for(i=4;i<=NF;i++) content=content":"$i
        if(file_pat=="" || $1 ~ file_pat) { gsub(allow_ere, "", content) }
        if(index(content, lit)>0) print $0
    }'
    if [ "$STAGED" -eq 1 ]; then
        found=$(echo "$STAGED_FILES" | xargs -I{} grep -nH -F "$lit" {} 2>/dev/null \
                | grep -Ev "$SKIP_PATTERN" \
                | awk -F: -v lit="$lit" -v allow_ere="$allow_ere" -v file_pat="$file_pat" "$_MASK_AWK" || true)
    else
        found=$(grep -rnH --include="*.md" --include="*.yml" --include="*.yaml" \
                     --include="*.toml" --include="*.json" --include="*.py" \
                     -F "$lit" "$TARGET" 2>/dev/null \
                | grep -Ev "$SKIP_PATTERN" \
                | awk -F: -v lit="$lit" -v allow_ere="$allow_ere" -v file_pat="$file_pat" "$_MASK_AWK" || true)
    fi
    if [ -n "$found" ]; then
        echo "$found"
        echo "FAIL [$label]: literal '$lit' matched (outside allowlisted contexts)"
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

_grep_py_word() {
    # _grep_py_word LABEL WORD  — whole-word, case-insensitive, .py files only.
    # Used for class 10 (crew narrative-names) so role docs (*.md) are not flagged.
    #
    # tests/ is ALSO excluded (in addition to SKIP_PATTERN) — mason/wren/ada/argus/
    # iris/atlas are the framework's own shipped git-identity + role-alias convention
    # (see doctrine/git-discipline.md's `--as mason` example, doctrine/roles/*.md),
    # not just session-narrative crew slang. The test suite legitimately exercises
    # that convention (git-identity tests, entry_id fixtures, role-doc cross-refs) —
    # and tests/ is never shipped in the wheel (see pyproject.toml build artifacts),
    # so a crew-name in a test file cannot leak into the published package. Class 10
    # stays scoped to real shipped Python source (src/), where CI already runs it.
    local label="$1" word="$2"
    local found
    local TESTS_EXCLUDE='(^|/)tests/'
    if [ "$STAGED" -eq 1 ]; then
        found=$(echo "$STAGED_FILES" | grep '\.py$' | xargs -I{} grep -nH -wi "$word" {} 2>/dev/null \
                | grep -Ev "$SKIP_PATTERN" | grep -Ev "$TESTS_EXCLUDE" || true)
    else
        found=$(grep -rn --include="*.py" -wi "$word" "$TARGET" 2>/dev/null \
                | grep -Ev "$SKIP_PATTERN" | grep -Ev "$TESTS_EXCLUDE" || true)
    fi
    if [ -n "$found" ]; then
        echo "$found"
        echo "FAIL [$label]: crew name '$word' (whole-word, .py) found in Python source"
        FAIL=1
    fi
}

_grep_re_py() {
    # _grep_re_py LABEL ERE_PATTERN [EXTRA_EXCLUDE_ERE]
    # Like _grep_re but .py files only — used for class 12 (internal
    # dev-process references in shipped Python source). Mirrors class 10's
    # _grep_py_word rationale: shipped *.md doctrine legitimately carries
    # these labels in its OWN self-consistent numbering (e.g. agent-charter.md
    # defines "charter §N"; note-conventions.md defines its own numbered
    # list, cross-referenced elsewhere in doctrine as "note-conventions #N").
    # A public wheel's PYTHON SOURCE should never read like an internal
    # changelog — that is the surface this class targets. tests/ is ALSO
    # excluded: it is never shipped (pyproject.toml build artifacts) and is
    # scanned separately, --codenames-only, for class 1 only.
    #
    # EXTRA_EXCLUDE_ERE (optional): an additional path regex to exclude,
    # for a file that legitimately documents the pattern itself (e.g.
    # lint.py's own rule-9 docstring names "SR-XPB" as an example of what
    # that rule catches — self-referential, not a leak, same shape as this
    # scanner's own SKIP_PATTERN self-exclusion).
    local label="$1" pattern="$2" extra_exclude="${3:-}"
    local found
    local TESTS_EXCLUDE='(^|/)tests/'
    if [ "$STAGED" -eq 1 ]; then
        found=$(echo "$STAGED_FILES" | grep '\.py$' | xargs -I{} grep -nH -E "$pattern" {} 2>/dev/null \
                | grep -Ev "$SKIP_PATTERN" | grep -Ev "$TESTS_EXCLUDE" || true)
    else
        found=$(grep -rn --include="*.py" -E "$pattern" "$TARGET" 2>/dev/null \
                | grep -Ev "$SKIP_PATTERN" | grep -Ev "$TESTS_EXCLUDE" || true)
    fi
    if [ -n "$extra_exclude" ] && [ -n "$found" ]; then
        found=$(echo "$found" | grep -Ev "$extra_exclude" || true)
    fi
    if [ -n "$found" ]; then
        echo "$found"
        echo "FAIL [$label]: internal dev-process reference matched (.py, regex '$pattern')"
        FAIL=1
    fi
}

_grep_literal_non_py() {
    # _grep_literal_non_py LABEL LITERAL
    # Like _grep_literal but EXCLUDES .py files entirely — internal dev-path
    # references in Python source comments/docstrings (design-of-record
    # citations, boundary-safety notes) are an established, accepted
    # convention across this codebase (mirrors class 10's inverse .py-only
    # scoping: that class is .py-ONLY, this one is .py-EXCLUDED). No
    # exemption/masking — every non-.py file is checked strictly (PR-C2:
    # DEVLOG.md's grandfather exemption was removed once DEVLOG.md was
    # untracked and stopped being part of the scanned/shipped surface).
    local label="$1" lit="$2"
    local found
    if [ "$STAGED" -eq 1 ]; then
        found=$(echo "$STAGED_FILES" | grep -v '\.py$' | xargs -I{} grep -nH -F "$lit" {} 2>/dev/null \
                | grep -Ev "$SKIP_PATTERN" || true)
    else
        found=$(grep -rnH --include="*.md" --include="*.yml" --include="*.yaml" \
                     --include="*.toml" --include="*.json" \
                     -F "$lit" "$TARGET" 2>/dev/null \
                | grep -Ev "$SKIP_PATTERN" || true)
    fi
    if [ -n "$found" ]; then
        echo "$found"
        echo "FAIL [$label]: literal '$lit' matched (non-.py)"
        FAIL=1
    fi
}

_grep_literal_except() {
    # _grep_literal_except LABEL LITERAL ALLOW_ERE
    # Like _grep_literal but instead of DROPPING entire lines that match ALLOW_ERE,
    # masks the canonical repo URL substring out of each matching line and then
    # re-checks whether the bare literal still survives on the remainder.
    #
    # The old per-line grep -Ev "$allow_ere" silently hid co-occurring leaks: a line
    # containing BOTH the canonical URL AND a real leak (e.g. a bare @handle or a
    # private /Users/... path) was dropped in its entirety, so the real leak was
    # never flagged.  Mask-then-recheck closes that hole:
    #   1. sed removes the canonical URL substring (and any sub-path like /issues).
    #   2. grep -F "$lit" re-tests the masked remainder.
    # If the literal still appears after masking → RED.  A line that contained ONLY
    # the canonical URL produces an empty/non-matching remainder → GREEN.
    #
    # Invariant: only github.com/phoongkhangzhie/research-vault (plus sub-paths) is
    # masked.  Any other occurrence of $lit on the same line is still caught.
    # Non-canonical URLs (github.com/phoongkhangzhie/other-repo) are NOT masked →
    # still RED, as intended.
    local label="$1" lit="$2" allow_ere="$3"
    local found
    # _MASK_AWK: splits each grep output line (filename:linenum:content) on `:`,
    # reconstructs the content portion (fields 3+), masks the canonical URL from
    # the content ONLY (not from the filename), then checks if the bare literal
    # still survives in the masked content.  Printing $0 (the original unmasked
    # line) preserves the full diagnostic output.
    #
    # Using awk for field-aware masking instead of sed-then-grep is critical:
    # sed would mask the URL from the ENTIRE line including the filename, but the
    # final grep -F would still match on "phoongkhangzhie" in the filename (e.g.
    # pytest temp dirs like /pytest-of-phoongkhangzhie/...) causing false positives.
    # PR #184 (README badges): img.shields.io/github/<kind>/<owner>/<repo>
    # badge URLs (stars/forks/watchers) reference the SAME canonical repo
    # identity as the github.com/ URL, just via a different badge CDN — they
    # do NOT start with "github.com/" so need their own explicit mask
    # (research-vault-specific, same as the github.com mask below).
    local _MASK_AWK='NF>=3{
        content=$3; for(i=4;i<=NF;i++) content=content":"$i
        gsub(/github[.]com\/phoongkhangzhie\/research-vault[A-Za-z0-9\/_.-]*/, "", content)
        gsub(/img[.]shields[.]io\/github\/(stars|forks|watchers)\/phoongkhangzhie\/research-vault[A-Za-z0-9\/_.?=&-]*/, "", content)
        if(index(content, lit)>0) print $0
    }'
    if [ "$STAGED" -eq 1 ]; then
        found=$(echo "$STAGED_FILES" | xargs -I{} grep -nH -F "$lit" {} 2>/dev/null \
                | grep -Ev "$SKIP_PATTERN" \
                | awk -F: -v lit="$lit" "$_MASK_AWK" || true)
    else
        found=$(grep -rnH --include="*.md" --include="*.yml" --include="*.yaml" \
                     --include="*.toml" --include="*.json" --include="*.py" \
                     -F "$lit" "$TARGET" 2>/dev/null \
                | grep -Ev "$SKIP_PATTERN" \
                | awk -F: -v lit="$lit" "$_MASK_AWK" || true)
    fi
    if [ -n "$found" ]; then
        echo "$found"
        echo "FAIL [$label]: literal '$lit' matched (outside allowlisted contexts)"
        FAIL=1
    fi
}

# ── Class 5 always runs (secrets scan applies everywhere) — EXCEPT --codenames-only ──
# Run this block in the two full-scan modes (plain + --secrets-only); skipped only in
# --codenames-only, which scans tests/ (see flag doc above) where sk-ant-* fake-secret
# test fixtures (test_keys_registry.py, test_onboard_verb.py, etc.) are legitimate and
# would otherwise flood the scan with false positives unrelated to the codename class.
if [ "$CODENAMES_ONLY" -eq 0 ]; then

# ── Class 5: Secret-shaped strings ───────────────────────────────────────────
# Known secret env-var names used in private bridge config
_grep_literal "secret/DRAIN_SECRET"    "DRAIN_SECRET"
_grep_literal "secret/WEBHOOK_SECRET"  "WEBHOOK_SECRET"
# Anthropic API-key prefix in plain text
_grep_re      "secret/sk-ant"          "sk-ant-[A-Za-z0-9_-]+"

fi  # end CODENAMES_ONLY-skips-class-5 block

# ── Private-marker classes (framework-repo-only; skipped in --secrets-only) ──
if [ "$SECRETS_ONLY" -eq 0 ]; then

# ── Class 1: Private project codenames ───────────────────────────────────────
# Runs in both the full scan AND --codenames-only mode.
_grep_literal "codename/cultural-social-sim" "cultural-social-sim"
_grep_word    "codename/csb"                 "csb"
_grep_word    "codename/dossier"             "dossier"

# ── Classes 2-11: skipped in --codenames-only (see flag doc above) ──────────
if [ "$CODENAMES_ONLY" -eq 0 ]; then

# ── Class 2: Private identity strings ────────────────────────────────────────
# The author/maintainer entry in pyproject.toml is public PyPI metadata and is
# the ONLY sanctioned location for "Khang Zhie Phoong" and "phoongkz@gmail.com".
# The FILE_PAT arg "(^|/)pyproject\.toml$" scopes masking to pyproject.toml ONLY —
# every other file is checked strictly with no masking, so the full name or
# author email appearing in a doctrine .md, a .py comment, a .yml, or any other
# file triggers a RED build exactly as it does on main.
_grep_word_except       "identity/khang"    "khang"    "Khang Zhie Phoong"                     "(^|/)pyproject\\.toml$"
_grep_word_except       "identity/phoong"   "phoong"   "(Khang Zhie Phoong|phoongkz@gmail[.]com)" "(^|/)pyproject\\.toml$"
_grep_literal_except_re "identity/phoongkz" "phoongkz" "phoongkz@gmail[.]com"                  "(^|/)pyproject\\.toml$"
# Allowlist: the canonical public repo URL github.com/phoongkhangzhie/research-vault
# (and its sub-paths, e.g. /issues) is legitimate publish-metadata in pyproject.toml
# and README.  A bare @phoongkhangzhie, any non-research-vault GitHub path, or any
# other context still triggers a RED build.
_grep_literal_except "identity/phoongkhangzhie" "phoongkhangzhie" \
    "github\.com/phoongkhangzhie/research-vault"
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

# ── Class 10: Crew narrative-names in Python source ──────────────────────────
# Session-narrative agent names must not appear in shipped Python source
# (docstrings, comments, inline annotations).  The product uses ROLE-BASED
# identifiers (researcher / engineer / reviewer / designer / manager / architect).
# Role docs (*.md in doctrine/roles/) are the ONLY legitimate home for these
# names — class 10 uses _grep_py_word to scan .py files only, leaving .md alone.
#
# Mapping: Ada→researcher · Wren→architect · Mason→engineer ·
#          Argus→reviewer · Iris→designer · Atlas→manager
_grep_py_word "crew-name/ada"    "ada"
_grep_py_word "crew-name/wren"   "wren"
_grep_py_word "crew-name/mason"  "mason"
_grep_py_word "crew-name/argus"  "argus"
_grep_py_word "crew-name/iris"   "iris"
_grep_py_word "crew-name/atlas"  "atlas"

# ── Class 11: Private local dev-paths ─────────────────────────────────────────
# ~/vault (the operator's own hub instance) and docs/superpowers/ (its internal,
# unshipped spec directory) are author-local paths — citing them in a shipped
# file tells an adopter to go look at a path they don't have.
#
# ~/vault is checked non-.py only: it legitimately appears in shipped .py
# comments/docstrings that document rv's OWN boundary ("state_dir, NOT ~/vault").
#
# docs/superpowers/ is checked in ALL shipped files INCLUDING .py: a
# design-of-record citation like "docs/superpowers/specs/foo.md" is a dangling
# pointer into the operator's private hub and must never ship, in any file type.
# (The pre-publish wheel audit caught 15 such refs the old non-.py exemption let
# through — this closes that hole.) Tests are scanned --codenames-only, so their
# design-of-record citations don't trip this and aren't shipped anyway.
_grep_literal_non_py "path/tilde-vault"       "~/vault"
_grep_literal         "path/docs-superpowers"  "docs/superpowers/"

# ── Class 12: Internal dev-process references in Python source ──────────────
# A pre-publish scrub (0.3.1) found systemic internal-process references in
# shipped .py comments/docstrings: an internal governance-doc citation
# ("charter §N"), bare internal spec/decision labels (D-4e, K-D1, SR-XPB),
# internal PR/task numbers, and dangling pointers to unshipped design-doc
# filenames. None of these are dangling in the sense of "the file doesn't
# ship" per se — some (like agent-charter.md) DO ship as doctrine — but a
# public wheel's PYTHON SOURCE should never read like an internal changelog:
# an adopter reading a docstring shouldn't have to cross-reference an
# internal numbering scheme to understand a comment. Scoped .py-only via
# _grep_re_py (mirrors class 10): shipped *.md doctrine legitimately
# self-references its OWN numbered sections (agent-charter.md's own "§N"
# values, note-conventions.md's own "#N" list items) — those are NOT
# dangling and are out of scope here.
#
# "charter §" — an internal governance-doc citation glued onto a plain
# comment as a parenthetical; the technical point should stand on its own.
_grep_re_py "devproc/charter-section" "charter §[0-9]"
# Bare "D-<digit>" decision/spec labels (D-4e, D-5a, D-7, ...) — an
# unshipped internal design doc's numbering scheme.
_grep_re_py "devproc/d-label" "\bD-[0-9]+[a-zA-Z]?\b"
# "K-D<digit>" decision labels (K-D1, K-D2, ...).
_grep_re_py "devproc/k-d-label" "\bK-D[0-9]+\b"
# "SR-" internal story-reference tags (SR-XPB, SR-CO-REMOTE, SR-1, ...).
# lint.py's own rule-9 docstring/comments NAME this exact pattern as an
# example of what that rule catches (self-referential, not a leak — the
# same shape as this scanner's own SKIP_PATTERN self-exclusion).
_grep_re_py "devproc/sr-tag" "\bSR-[A-Z0-9]+(-[A-Z0-9]+)*\b" "(^|/)lint\.py:"
# "PR #N" / "PR-N" / "PR delta" — internal pull-request references.
_grep_re_py "devproc/pr-number" "\bPR[ -]?#[0-9]+\b|PR delta"
# "acceptance item N" — an internal acceptance-checklist item number.
_grep_re_py "devproc/acceptance-item" "acceptance item [0-9]+"
# "Design of record" / "design note" — an internal design-doc genre label
# with no public referent (the doc itself never ships).
_grep_re_py "devproc/design-of-record" "[Dd]esign of record|internal design note"
# Internal design-doc filenames (YYYY-MM-DD-...-design[.md]) — a dangling
# pointer into an unshipped spec directory.
_grep_re_py "devproc/design-doc-filename" "[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9-]*-design(\.md)?"

fi  # end CODENAMES_ONLY-skips-classes-2-11 block

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
