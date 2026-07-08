#!/usr/bin/env bash
# ci_dogfood_checks.sh — PR-CC-7: CI dogfood wiring for the code-conventions gate.
#
# Runs the note-plane gate (`rv note check`) over the packaged demo-research
# example (real OKF content — rv's own repo has no OKF notes of its own, so
# the shipped example is the meaningful non-vacuous fixture) and the
# repo-plane gate (`rv code check`) over rv's own repo tree (a real dogfood —
# CHECK-8b/c fire against rv's actual CITATION.cff/LICENSE; CHECK-3b/5/6a/7/8a
# are honest no-ops since rv's own repo has no code/data/results tree).
#
# Design: docs/superpowers/specs/2026-07-07-code-conventions-design.md §4
# (D-CC-4 gate placement — CI row) + §8 (PR-CC-7 acceptance).
#
# When to use: invoked by .github/workflows/ci.yml. Runnable locally the same
# way to reproduce a CI failure (`bash scripts/ci_dogfood_checks.sh`).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

CONFIG_FILE="$(mktemp)"
trap 'rm -f "$CONFIG_FILE"' EXIT

cat > "$CONFIG_FILE" <<EOF
instance_root = "$REPO_ROOT"

[projects.demo-research]
source_dir = "$REPO_ROOT/src/research_vault/data/examples/demo-research/notes"

[projects.research-vault]
source_dir = "$REPO_ROOT"
EOF

echo "=== rv note check — demo-research (packaged example, note-plane) ==="
RESEARCH_VAULT_CONFIG="$CONFIG_FILE" uv run rv note demo-research check

echo "=== rv code check — research-vault (repo-plane, local/WARN mode) ==="
RESEARCH_VAULT_CONFIG="$CONFIG_FILE" uv run rv code check research-vault

echo "=== rv code check --release — research-vault (release-blocking subset, CHECK-8b/c HARD) ==="
RESEARCH_VAULT_CONFIG="$CONFIG_FILE" uv run rv code check research-vault --release

echo "OK: all dogfood CI checks passed."
