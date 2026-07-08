#!/usr/bin/env bash
# release_gate_canary.sh — PR-CC-7 rejects-only proof that `rv code check
# --release` flips RED on a missing/invalid CITATION.cff and stays GREEN when
# CITATION.cff + LICENSE are both present and valid.
#
# Builds an ephemeral fixture project (a real `scaffold_release_stubs` tree,
# NOT rv's own repo) under a temp dir, then drives `rv code check <project>
# --release` in three directions:
#   1. CITATION.cff + LICENSE both present+valid  -> expect GREEN (exit 0)
#   2. CITATION.cff removed                        -> expect RED   (exit 1)
#   3. CITATION.cff present but missing required keys -> expect RED (exit 1)
#
# A failure of ANY assertion below means the release gate is toothless or
# mis-wired (charter §10: a result that should have been red is a
# contamination flag, not a pass). This script self-verifies both directions —
# it is the "described CI-run" proof PR-CC-7's acceptance criterion asks for,
# and is designed to be re-runnable locally the same way CI runs it.
#
# Design: docs/superpowers/specs/2026-07-07-code-conventions-design.md
# §3 CHECK-8b/c + §8 PR-CC-7 acceptance ("CI red on missing/invalid
# CITATION.cff in the release path, green otherwise").
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE_DIR="$(mktemp -d)"
trap 'rm -rf "$FIXTURE_DIR"' EXIT

PROJECT_DIR="$FIXTURE_DIR/project"
mkdir -p "$PROJECT_DIR"

CONFIG_FILE="$FIXTURE_DIR/rv-canary.toml"
cat > "$CONFIG_FILE" <<EOF
instance_root = "$FIXTURE_DIR"

[projects.canary]
source_dir = "$PROJECT_DIR"
EOF

# A real (not the scaffolded placeholder) SPDX-recognizable LICENSE, so
# CHECK-8c is satisfied and only CHECK-8b (CITATION.cff) is under test.
cp "$REPO_ROOT/LICENSE" "$PROJECT_DIR/LICENSE"

cd "$REPO_ROOT"
uv run python -c "
from pathlib import Path
from research_vault import scaffold
scaffold.scaffold_release_stubs(Path('$PROJECT_DIR'), slug='canary')
"

echo "=== Canary 1: valid CITATION.cff + LICENSE -> expect GREEN (exit 0) ==="
if RESEARCH_VAULT_CONFIG="$CONFIG_FILE" uv run rv code check canary --release; then
  echo "OK: release gate GREEN with valid stubs"
else
  echo "FAIL: release gate unexpectedly RED with valid CITATION.cff + LICENSE"
  exit 1
fi

echo "=== Canary 2: CITATION.cff removed -> expect RED (exit 1) ==="
rm "$PROJECT_DIR/CITATION.cff"
if RESEARCH_VAULT_CONFIG="$CONFIG_FILE" uv run rv code check canary --release; then
  echo "FAIL: release gate stayed GREEN with CITATION.cff missing — gate is toothless"
  exit 1
else
  echo "OK: release gate correctly RED on missing CITATION.cff"
fi

echo "=== Canary 3: invalid CITATION.cff (missing required keys) -> expect RED (exit 1) ==="
cat > "$PROJECT_DIR/CITATION.cff" <<'EOF2'
cff-version: 1.2.0
title: "canary"
EOF2
if RESEARCH_VAULT_CONFIG="$CONFIG_FILE" uv run rv code check canary --release; then
  echo "FAIL: release gate stayed GREEN with an invalid CITATION.cff — gate is toothless"
  exit 1
else
  echo "OK: release gate correctly RED on invalid CITATION.cff (missing required keys)"
fi

echo "ALL CANARY CHECKS PASSED — the release gate flips correctly in both directions."
