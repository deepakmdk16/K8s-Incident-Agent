#!/usr/bin/env bash
# Build a verified release zip + sha256 sidecar, then run checkpoints against
# the EXTRACTED zip (the deliverable is the bytes that ship, not the tree).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$ROOT/dist"
ZIP="$OUT/release_$STAMP.zip"

cd "$ROOT"

# The zip ships exactly what git tracks, so packaging is meaningless without git.
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "FAIL: not a git repo — packaging ships exactly what git tracks; run git init and commit first." >&2
  exit 2
fi
mkdir -p "$OUT"

# The zip ships exactly what git tracks — so a dirty tree means silent omissions.
if [ -n "$(git status --porcelain)" ]; then
  echo "FAIL: uncommitted/untracked changes — the zip would silently omit them:" >&2
  git status --porcelain >&2
  exit 1
fi
# Git-ignored files inside must-ship dirs would also be silently dropped.
#
# __pycache__ is excluded from this guard deliberately: it is build output, not a
# deliverable, so dropping it from the zip is CORRECT. pytest creates it under
# every source dir, and scripts/checkpoints.sh runs pytest — so the packaging
# step this guard protects is guaranteed to see it, and the guard fired on every
# run before this filter existed (the zip had never been built). Its remedy line
# below ("git add -f") would also be actively harmful here: force-adding .pyc
# would ship absolute build paths inside co_filename, where the privacy scan's
# grep -I (skip binaries) cannot see them.
IGNORED="$(git status --porcelain --ignored -- evals baseline solution tests common ablation \
  | grep '^!!' | grep -v '__pycache__/' || true)"
if [ -n "$IGNORED" ]; then
  echo "FAIL: git-ignored files inside must-ship dirs would be EXCLUDED from the zip:" >&2
  echo "$IGNORED" >&2
  echo "Fix .gitignore or 'git add -f' them before packaging." >&2
  exit 1
fi

git ls-files -z | xargs -0 zip -q "$ZIP"           # ship exactly what git tracks
shasum -a 256 "$ZIP" | awk '{print $1}' > "$ZIP.sha256"

EXTRACT="$OUT/verify_$STAMP"
mkdir -p "$EXTRACT"
unzip -q "$ZIP" -d "$EXTRACT"

echo "== verifying extracted package"
RC=0
bash "$ROOT/scripts/checkpoints.sh" --package "$EXTRACT" || RC=$?

echo "zip:    $ZIP"
echo "sha256: $(cat "$ZIP.sha256")"
[ $RC -eq 0 ] && echo "PACKAGE VERIFIED — distributing it is YOUR action, not the agent's." \
              || echo "PACKAGE FAILED VERIFICATION — do not ship it."
exit $RC
