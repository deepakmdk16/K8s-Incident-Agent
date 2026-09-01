#!/usr/bin/env bash
# wait.sh — t1-crashloop-missing-env: block until the fault has observably
# manifested as its SPECIFIC decisive evidence (never bare CrashLoopBackOff,
# which OOM kills and config errors also produce):
#   1. the checkout-worker pod has restartCount >= 3 with waiting reason
#      CrashLoopBackOff, AND
#   2. its current-channel logs contain 'FATAL: AMQP_URL not set'.
# Exit 0 on manifest; exit 1 with a diagnostic after 300s.
set -u

CTX="${1:-kind-incident-lab}"
NS="payments"
SELECTOR="app=checkout-worker"
DEADLINE=$(( $(date +%s) + 300 ))

K() { kubectl --context "$CTX" --request-timeout=15s "$@"; }

while :; do
  RESTARTS="$(K get pods -n "$NS" -l "$SELECTOR" \
    -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null || true)"
  REASON="$(K get pods -n "$NS" -l "$SELECTOR" \
    -o jsonpath='{.items[0].status.containerStatuses[0].state.waiting.reason}' 2>/dev/null || true)"
  POD="$(K get pods -n "$NS" -l "$SELECTOR" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  FATAL=""
  if [ -n "$POD" ]; then
    FATAL="$(K logs -n "$NS" "$POD" --tail=20 2>/dev/null \
      | grep 'FATAL: AMQP_URL not set' | head -n 1)"
  fi
  if [ "${RESTARTS:-0}" -ge 3 ] 2>/dev/null && [ "$REASON" = "CrashLoopBackOff" ] && [ -n "$FATAL" ]; then
    echo "wait: fault manifested — restarts=$RESTARTS reason=$REASON log: $FATAL"
    exit 0
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    {
      echo "wait: TIMEOUT after 300s — restarts=${RESTARTS:-<none>} reason=${REASON:-<none>} fatal='${FATAL:-<none>}'"
      K get pods -n "$NS" -l "$SELECTOR" -o wide || true
    } >&2
    exit 1
  fi
  sleep 5
done
