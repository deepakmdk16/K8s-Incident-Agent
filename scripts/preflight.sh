#!/usr/bin/env bash
# Session preflight: tools, versions, keys. Run once per working session.
# Conditional by design: warns on optional gaps, fails only on hard requirements.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FAIL=0; WARN=0
ok()   { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
warn() { printf '  \033[33mWARN\033[0m %s\n' "$1"; WARN=$((WARN+1)); }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }

echo "== preflight: $ROOT"

# --- .env (loaded if present; never printed) ---
if [ -f "$ROOT/.env" ]; then
  set -a; . "$ROOT/.env"; set +a
  ok ".env loaded"
else
  warn ".env missing — copy .env.example and fill keys"
fi

# --- required tools ---
for t in git curl zip shasum; do
  command -v "$t" >/dev/null 2>&1 && ok "tool: $t" || fail "tool missing: $t"
done

# --- optional toolchains (warn only) ---
# Version probes differ: most take --version, go takes "version", java takes
# -version (and the macOS /usr/bin/java stub exits non-zero without a JDK).
for t in python3 uv node npm go cargo java; do
  if command -v "$t" >/dev/null 2>&1; then
    v="$("$t" --version 2>/dev/null | head -1)" || v=""
    [ -n "$v" ] || { "$t" version >/dev/null 2>&1 && v="$("$t" version 2>/dev/null | head -1)"; }
    [ -n "$v" ] || { "$t" -version >/dev/null 2>&1 && v="$("$t" -version 2>&1 | head -1)"; }
    if [ -n "${v:-}" ]; then ok "toolchain: $t ($v)"
    else warn "toolchain absent: $t (not required by this project)"; fi
  else
    warn "toolchain absent: $t (not required by this project)"
  fi
done
# stack is fixed (pyproject.toml committed): uv is a hard requirement, not an option
[ -f "$ROOT/pyproject.toml" ] && ! command -v uv >/dev/null 2>&1 \
  && fail "uv is mandatory once pyproject.toml exists (README prerequisites: uv sync)"

# --- API keys (live eval runs call the Anthropic API) ---
have_key=0
# Anthropic is the only provider this project calls; warning about keys the
# project never reads only adds noise to a fresh-clone run.
for k in ANTHROPIC_API_KEY; do
  v="${!k:-}"
  if [ -n "$v" ]; then ok "key set: $k (${#v} chars)"; have_key=1
  else warn "key unset: $k"; fi
done
[ "$have_key" -eq 1 ] || warn "no provider key in env — offline tests and 'make verify' still work, but live eval runs (make eval) need a funded key"

# --- repo hygiene ---
if git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  ok "git repo"
  [ "$(git -C "$ROOT" config core.hooksPath 2>/dev/null)" = "githooks" ] \
    && ok "git hooks wired (githooks/)" \
    || warn "git hooks not wired — run: git config core.hooksPath githooks"
else
  warn "no git repo yet — run: git init && git config core.hooksPath githooks (hooks enforce the gates)"
fi
grep -q '^\.env$' "$ROOT/.gitignore" 2>/dev/null && ok ".env gitignored" || fail ".env not in .gitignore"

echo "== preflight: $FAIL fail, $WARN warn"
exit "$([ "$FAIL" -eq 0 ] && echo 0 || echo 1)"
