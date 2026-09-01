#!/usr/bin/env bash
# wait.sh — t3-quiet-selector-loud-crashloop: block until BOTH sides of the
# composition have observably manifested:
#   quiet gold fault: Endpoints search/search has no addresses AND both
#     search pods are Ready (probes passing) AND the gateway logged a
#     failed search fetch;
#   loud decoy: analytics-batch/report-generator restartCount >= 3 with
#     waiting reason CrashLoopBackOff (the red herring must be fully lit
#     before capture, or the case loses its adversarial point).
# Exit 0 on manifest; exit 1 with a diagnostic after 300s.
set -u

CTX="${1:-kind-incident-lab}"
DEADLINE=$(( $(date +%s) + 300 ))

K() { kubectl --context "$CTX" --request-timeout=15s "$@"; }

while :; do
  ADDRS="$(K get endpoints -n search search \
    -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null || true)"
  READY="$(K get pods -n search -l app=search \
    -o jsonpath='{range .items[*]}{.status.containerStatuses[0].ready}{" "}{end}' 2>/dev/null || true)"
  GWLOG="$(K logs -n search deployment/web-gateway --tail=20 2>/dev/null \
    | grep 'search fetch FAILED' | head -n 1)"
  RESTARTS="$(K get pods -n analytics-batch -l app=report-generator \
    -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null || true)"
  REASON="$(K get pods -n analytics-batch -l app=report-generator \
    -o jsonpath='{.items[0].status.containerStatuses[0].state.waiting.reason}' 2>/dev/null || true)"
  if [ -z "$ADDRS" ] && [ "$READY" = "true true " ] && [ -n "$GWLOG" ] \
     && [ "${RESTARTS:-0}" -ge 3 ] 2>/dev/null && [ "$REASON" = "CrashLoopBackOff" ]; then
    echo "wait: fault manifested — Endpoints search empty; search pods Ready x2; gateway: $GWLOG; decoy restarts=$RESTARTS $REASON"
    exit 0
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    {
      echo "wait: TIMEOUT after 300s — endpoints='${ADDRS:-<empty>}' ready='${READY:-<unread>}' gwlog='${GWLOG:-<none>}' decoy_restarts='${RESTARTS:-<none>}' decoy_reason='${REASON:-<none>}'"
      K get endpoints,pods -n search -o wide || true
      K get pods -n analytics-batch -o wide || true
    } >&2
    exit 1
  fi
  sleep 5
done
