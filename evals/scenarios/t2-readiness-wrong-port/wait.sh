#!/usr/bin/env bash
# wait.sh — t2-readiness-wrong-port: block until the fault has observably
# manifested as its SPECIFIC decisive evidence:
#   1. both orders-api pods Running with Ready=False (app up, probe down),
#   2. a 'Readiness probe failed' event whose message names port 8081, AND
#   3. Endpoints orders-api has no addresses.
# Exit 0 on manifest; exit 1 with a diagnostic after 300s.
set -u

CTX="${1:-kind-incident-lab}"
NS="orders"
DEADLINE=$(( $(date +%s) + 300 ))

K() { kubectl --context "$CTX" --request-timeout=15s "$@"; }

while :; do
  STATE="$(K get pods -n "$NS" -l app=orders-api \
    -o jsonpath='{range .items[*]}{.status.phase}/{.status.containerStatuses[0].ready}{" "}{end}' 2>/dev/null || true)"
  EV="$(K get events -n "$NS" --field-selector reason=Unhealthy \
    -o jsonpath='{range .items[*]}{.message}{"\n"}{end}' 2>/dev/null \
    | grep 'Readiness probe failed' | grep '8081' | head -n 1)"
  ADDRS="$(K get endpoints -n "$NS" orders-api \
    -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null || true)"
  if [ "$STATE" = "Running/false Running/false " ] && [ -n "$EV" ] && [ -z "$ADDRS" ]; then
    echo "wait: fault manifested — orders-api Running/NotReady x2; event: $EV; Endpoints empty"
    exit 0
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    {
      echo "wait: TIMEOUT after 300s — state='${STATE:-<unread>}' event='${EV:-<none>}' endpoints='${ADDRS:-<empty>}'"
      K get pods,endpoints -n "$NS" -o wide || true
      K get events -n "$NS" --field-selector reason=Unhealthy || true
    } >&2
    exit 1
  fi
  sleep 5
done
