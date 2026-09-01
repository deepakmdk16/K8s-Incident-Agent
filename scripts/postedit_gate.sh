#!/usr/bin/env bash
# PostToolUse gate: after an edit under baseline/solution/tests/evals, run the
# fast toolchain check so a self-inflicted regression surfaces at the edit
# instead of at packaging time (the documented top failure mode).
#
# Reads the hook's JSON payload on stdin. Fails SAFE: anything unexpected exits
# 0 rather than blocking work. With no toolchain present it is a no-op.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

FILE="$(python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print((d.get("tool_input") or {}).get("file_path", ""))
except Exception:
    print("")
' 2>/dev/null)" || exit 0

[ -n "$FILE" ] || exit 0
case "$FILE" in
  */baseline/*|*/solution/*|*/common/*|*/tests/*|*/evals/*) ;;
  *) exit 0 ;;
esac
[ -f "$FILE" ] || exit 0   # deleted or foreign path — nothing to lint, fail safe

# Probe the linter first. A gate that fires because a tool is MISSING is noise,
# and noise gets ignored — only a real finding may fail this hook.
rc=0
if [ -f "$ROOT/pyproject.toml" ] && command -v uv >/dev/null 2>&1; then
  case "$FILE" in *.py) ;; *) exit 0 ;; esac   # ruff lints python only — a .sh here parses as noise
  (cd "$ROOT" && uv run ruff --version) >/dev/null 2>&1 || exit 0
  out="$(cd "$ROOT" && uv run ruff check "$FILE" 2>&1)" || rc=1
elif [ -f "$ROOT/package.json" ] && command -v npx >/dev/null 2>&1; then
  (cd "$ROOT" && npx --no-install eslint --version) >/dev/null 2>&1 || exit 0
  out="$(cd "$ROOT" && npx --no-install eslint "$FILE" 2>&1)" || rc=1
else
  exit 0   # no recognized toolchain — nothing to enforce
fi

[ "$rc" -eq 0 ] && exit 0
printf 'post-edit gate FAILED on %s\n%s\n' "$FILE" "${out:-}" >&2
exit 2
