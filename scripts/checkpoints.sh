#!/usr/bin/env bash
# Deterministic pre-commit/pre-push gate. This is the loop's exit condition:
# a failure means the slice is not done. Conditional: language stages activate
# only when their project files exist.
# Usage: checkpoints.sh [--package DIR] [--secrets-only]
#   --package DIR    run the same gates against an extracted release zip
#   --secrets-only   stop after the secret/privacy scans (fast pre-commit hook)
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="$ROOT"; IS_PKG=0; SECRETS_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --package)
      # A nonexistent dir must be a loud exit 2, never an empty TARGET: greps
      # over "" error out with rc=2, which the scan blocks would read as
      # "no match" and print an all-green report against nothing
      # (docs/failure-modes.md 2026-08-29, error-as-green).
      [ -d "$2" ] || { echo "FAIL: --package dir does not exist: $2" >&2; exit 2; }
      TARGET="$(cd "$2" && pwd)"; IS_PKG=1; shift 2 ;;
    --secrets-only) SECRETS_ONLY=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

FAIL=0
ok()   { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAIL=$((FAIL+1)); }
skip() { printf '  \033[90mskip\033[0m %s\n' "$1"; }

echo "== checkpoints against: $TARGET"

# 1. Secret & privacy scan (whole tree). Keys and personal data never ship.
# .env is excluded from the grep: it legitimately exists in the working tree
# (gitignored); only its PRESENCE IN A PACKAGE is fatal, checked below.
grep -rInE '(sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,}|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35}|-----BEGIN [A-Z ]*PRIVATE KEY|api[_-]?key["'"'"']?\s*[:=]\s*["'"'"'][A-Za-z0-9_-]{16,})' \
   --exclude-dir=.git --exclude-dir=.work --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=dist \
   --exclude='*.sha256' --exclude='checkpoints.sh' --exclude='.env' "$TARGET" >/dev/null 2>&1
case $? in
  0) fail "secret-like strings found (run the grep above without >/dev/null to locate)" ;;
  1) ok "secret scan clean" ;;
  # rc>=2 = the scan itself errored. Treating that as "no match" printed an
  # all-green report against an unreadable target (error-as-green,
  # docs/failure-modes.md 2026-08-29) — a scan that did not run certifies nothing.
  *) fail "secret scan ERRORED (grep rc>=2) — cannot certify, do not ship" ;;
esac
if [ "$IS_PKG" -eq 1 ]; then
  find "$TARGET" -name '.env' -type f | grep -q . \
    && fail ".env shipped in package (any depth) — must never happen" || ok "no .env in package"
else
  git -C "$ROOT" ls-files --error-unmatch .env >/dev/null 2>&1 \
    && fail ".env is TRACKED by git — untrack it now (git rm --cached .env)" || ok ".env not tracked"
fi
# Personal data: home paths are never legitimate anywhere — they leak the
# operator's username and machine layout. checkpoints.sh and test_gates.sh are
# excluded: they contain the patterns and the deliberately-planted fixtures.
grep -rInE '/(Users|home)/[A-Za-z0-9._-]+' \
     --exclude-dir=.git --exclude-dir=.work --exclude-dir=node_modules --exclude-dir=.venv --exclude-dir=dist \
     --exclude='checkpoints.sh' --exclude='test_gates.sh' --exclude='settings.local.json' \
     --exclude='*.sha256' --exclude='.env' "$TARGET" >/dev/null 2>&1
case $? in
  0) fail "home path (/Users/... or /home/...) present — scrub before shipping" ;;
  1) ok "no home paths in target" ;;
  *) fail "home-path scan ERRORED (grep rc>=2) — cannot certify" ;;
esac

# k8s fixtures are captured kubectl output: the generic key patterns above miss
# service-account JWTs, base64'd PEM blocks (LS0tLS1CRUdJTi = "-----BEGI"), and
# kubeconfig data embeds. Secret values are redacted by construction in
# evals/capture.sh; this scan is the independent check that construction held.
if [ -d "$TARGET/evals/fixtures" ]; then
  grep -rInE '(eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}|LS0tLS1CRUdJTi|(certificate-authority|client-certificate|client-key)-data: *[A-Za-z0-9+/=]{16,}|token: *[A-Za-z0-9._-]{24,})' \
       "$TARGET/evals/fixtures" >/dev/null 2>&1
  case $? in
    0) fail "k8s credential material in evals/fixtures/ (JWT / base64 PEM / kubeconfig data / token) — run the grep above without >/dev/null" ;;
    1) ok "fixtures free of k8s credential material" ;;
    *) fail "fixture credential scan ERRORED (grep rc>=2) — cannot certify" ;;
  esac
else
  skip "no evals/fixtures/ — k8s fixture scan inactive"
fi

if [ "$SECRETS_ONLY" -eq 1 ]; then
  echo "== checkpoints (secrets-only): $FAIL failure(s)"
  exit "$([ "$FAIL" -eq 0 ] && echo 0 || echo 1)"
fi

# STATUS.md is open-items-only: a completed [x] item is history, and history
# is git log. Stale status is how a cold session wastes its first half hour.
if [ -f "$TARGET/STATUS.md" ]; then
  if grep -qE '^ *- \[[xX]\]' "$TARGET/STATUS.md" 2>/dev/null; then
    fail "STATUS.md contains completed [x] items — delete them (open-items-only; history is git log)"
  else
    ok "STATUS.md is open-items-only"
  fi
else
  skip "no STATUS.md — status hygiene gate inactive"
fi

# 2. Deliverable completeness (files a packaged release must carry)
for f in README.md CHANGELOG.md CLAUDE.md; do
  [ -f "$TARGET/$f" ] && ok "present: $f" || fail "missing: $f"
done
for d in baseline solution tests evals; do
  [ -d "$TARGET/$d" ] && ok "present: $d/" || fail "missing dir: $d/"
done

# 2b. Fixture schema completeness (design req 4: over-capture, verified
# structurally — an eval that replays offline is only as honest as its fixture).
# Contract: scenario.yaml (mode + log-channel ledger), page.txt (symptom-first),
# cluster state files, >=1 pods.json, >=1 describe. Written by evals/capture.sh;
# hand-authored fixtures must meet the same schema (mode: authored).
if [ -d "$TARGET/evals/fixtures" ]; then
  BADFIX=""; NFIX=0
  for FIX in "$TARGET"/evals/fixtures/*/; do
    [ -d "$FIX" ] || continue
    NFIX=$((NFIX+1)); MISS=""
    [ -s "$FIX/scenario.yaml" ] || MISS="$MISS scenario.yaml"
    grep -qE '^mode: (captured|authored)$' "$FIX/scenario.yaml" 2>/dev/null || MISS="$MISS mode:"
    # log-channel ledger: every entry records BOTH channel statuses
    # (docs/failure-modes.md 2026-08-28 — the schema is honest about evidence)
    LP="$(grep -cE '^  - pod: ' "$FIX/scenario.yaml" 2>/dev/null)"; LP="${LP:-0}"
    LC="$(grep -cE '^    current: ' "$FIX/scenario.yaml" 2>/dev/null)"; LC="${LC:-0}"
    LV="$(grep -cE '^    previous: ' "$FIX/scenario.yaml" 2>/dev/null)"; LV="${LV:-0}"
    { [ "$LP" -ge 1 ] && [ "$LC" -eq "$LP" ] && [ "$LV" -eq "$LP" ]; } \
      || MISS="$MISS log_channels(per-channel-status)"
    [ -s "$FIX/page.txt" ] || MISS="$MISS page.txt"
    # scoring spec (evals/scoring.md): a fixture without ground truth cannot
    # be scored — every fixture needs its agent-invisible gold.json
    [ -s "$TARGET/evals/scenarios/$(basename "$FIX")/gold.json" ] \
      || MISS="$MISS scenarios/$(basename "$FIX")/gold.json"
    for req in cluster/get-all.txt cluster/events.json cluster/nodes.json cluster/version.json; do
      [ -s "$FIX/$req" ] || MISS="$MISS $req"
    done
    find "$FIX" -name 'pods.json' -size +1c 2>/dev/null | grep -q . || MISS="$MISS ns/*/pods.json"
    find "$FIX" -path '*/describe/*' -name '*.txt' -size +1c 2>/dev/null | grep -q . || MISS="$MISS describes"
    [ -n "$MISS" ] && BADFIX="$BADFIX
  ${FIX#"$TARGET"/}:$MISS"
  done
  if [ "$NFIX" -eq 0 ]; then skip "evals/fixtures/ has no fixtures yet"
  elif [ -z "$BADFIX" ]; then ok "fixture schema complete ($NFIX fixture(s))"
  else fail "incomplete fixtures:$BADFIX"; fi
else
  skip "no evals/fixtures/ — completeness gate inactive"
fi

# 3. Claim completeness (README carries the figures a reader must reproduce)
if grep -nE '(^|[^A-Za-z])TBD([^A-Za-z]|$)' "$TARGET/README.md" >/dev/null 2>&1; then
  fail "README still has bare TBD markers (runtime / cost / open claims)"
else ok "README free of TBD markers"; fi
grep -qiE 'runtime[^A-Za-z]*[~0-9]' "$TARGET/README.md" && grep -qiE 'cost[^A-Za-z]*[~$0-9]' "$TARGET/README.md" \
  && ok "README states concrete runtime/cost" || fail "README missing concrete runtime/cost figures"
grep -qE '^## \[[1-9]' "$TARGET/CHANGELOG.md" 2>/dev/null && ok "CHANGELOG has numbered entries" \
  || fail "CHANGELOG has no entries beyond the [0] scaffold"

# 4. Language gates — run only for toolchains the project actually uses
if [ -f "$TARGET/pyproject.toml" ] || ls "$TARGET"/*/pyproject.toml >/dev/null 2>&1; then
  if command -v uv >/dev/null 2>&1; then
    (cd "$TARGET" && uv run pytest -q) && ok "pytest" || fail "pytest"
    (cd "$TARGET" && uv run ruff check .) && ok "ruff" || fail "ruff"
    (cd "$TARGET" && uv run ruff format --check .) && ok "ruff format" || fail "ruff format"
    (cd "$TARGET" && uv run pyright) && ok "pyright (strict)" || fail "pyright (strict)"
  else fail "pyproject present but uv missing"; fi
else skip "no pyproject.toml — python gate inactive"; fi

if [ -f "$TARGET/package.json" ]; then
  (cd "$TARGET" && npm test --silent) && ok "npm test" || fail "npm test"
else skip "no package.json — node gate inactive"; fi

if [ -f "$TARGET/go.mod" ]; then
  (cd "$TARGET" && go test ./...) && ok "go test" || fail "go test"
else skip "no go.mod — go gate inactive"; fi

if [ -f "$TARGET/Cargo.toml" ]; then
  (cd "$TARGET" && cargo test -q) && ok "cargo test" || fail "cargo test"
else skip "no Cargo.toml — rust gate inactive"; fi

# 5. Evals (activates when evals/run.sh exists)
if [ -x "$TARGET/evals/run.sh" ]; then
  "$TARGET/evals/run.sh" && ok "evals" || fail "evals"
elif [ -f "$TARGET/evals/run.sh" ]; then
  # Present but not executable would SILENTLY skip the only gate binding
  # improvement claims to evidence. Never let that read as green.
  fail "evals/run.sh exists but is not executable — chmod +x it (a red eval must block)"
else skip "evals/run.sh not present"; fi

# 6. Gate self-tests (the gates are load-bearing; they get regression tests too)
if [ "${GATES_UNDER_TEST:-0}" = "1" ]; then
  skip "gate self-tests (recursion guard)"
elif [ -x "$TARGET/tests/test_gates.sh" ]; then
  bash "$TARGET/tests/test_gates.sh" >/dev/null 2>&1 && ok "gate self-tests" \
    || fail "gate self-tests (debug: bash tests/test_gates.sh)"
else skip "tests/test_gates.sh not present"; fi

echo "== checkpoints: $FAIL failure(s)"
exit "$([ "$FAIL" -eq 0 ] && echo 0 || echo 1)"
