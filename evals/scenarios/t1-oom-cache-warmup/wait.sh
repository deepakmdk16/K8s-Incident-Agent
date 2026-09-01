#!/usr/bin/env bash
# wait.sh — t1-oom-cache-warmup: block until the fault has observably
# manifested. Gates on the case's SPECIFIC decisive evidence, never a coarse
# state another failure could also reach: the recs/recommendations pod's
# container must show restartCount >= 2 AND
# lastState.terminated.reason == OOMKilled (or lastState exit code 137).
# Exit 0 on manifest; exit 1 with a diagnostic after 300s.
set -u

CTX="${1:-kind-incident-lab}"
NS="recs"
SELECTOR="app=recommendations"
DEADLINE=$(( $(date +%s) + 300 ))

K() { kubectl --context "$CTX" --request-timeout=15s "$@"; }

# Separate jsonpath reads: kubectl jsonpath errors out on a missing nested
# key (lastState.terminated is absent until the first kill), which would sink
# a combined query even while restartCount is readable.
while :; do
  RESTARTS="$(K get pods -n "$NS" -l "$SELECTOR" \
    -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null || true)"
  REASON="$(K get pods -n "$NS" -l "$SELECTOR" \
    -o jsonpath='{.items[0].status.containerStatuses[0].lastState.terminated.reason}' 2>/dev/null || true)"
  EXITCODE="$(K get pods -n "$NS" -l "$SELECTOR" \
    -o jsonpath='{.items[0].status.containerStatuses[0].lastState.terminated.exitCode}' 2>/dev/null || true)"
  if [ "${RESTARTS:-0}" -ge 2 ] 2>/dev/null \
     && { [ "$REASON" = "OOMKilled" ] || [ "$EXITCODE" = "137" ]; }; then
    echo "wait: fault manifested — restartCount=$RESTARTS lastState.terminated.reason=${REASON:-<none>} exitCode=${EXITCODE:-<none>}"
    exit 0
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    echo "wait: TIMEOUT after 300s — restartCount=${RESTARTS:-<none>} lastState.terminated.reason=${REASON:-<none>} exitCode=${EXITCODE:-<none>}" >&2
    K get pods -n "$NS" -l "$SELECTOR" -o wide >&2 || true
    exit 1
  fi
  sleep 5
done
