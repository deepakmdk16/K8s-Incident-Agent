#!/usr/bin/env bash
# wait.sh — t2-rbac-sync-forbidden: block until the fault has observably
# manifested as its SPECIFIC decisive evidence in the worker's own logs:
#   1. 'discovery=ok' — the token header IS sent and accepted (an anonymous
#      or mis-built request would fail discovery too; capturing without this
#      would freeze a fixture whose 403 the gold remediation cannot fix), AND
#   2. a '403 Forbidden' line from the namespaced ConfigMap read.
# Exit 0 on manifest; exit 1 with a diagnostic after 300s.
set -u

CTX="${1:-kind-incident-lab}"
NS="inventory"
DEADLINE=$(( $(date +%s) + 300 ))

K() { kubectl --context "$CTX" --request-timeout=15s "$@"; }

while :; do
  LOGS="$(K logs -n "$NS" deployment/inventory-sync --tail=30 2>/dev/null || true)"
  if printf '%s\n' "$LOGS" | grep -q 'discovery=ok' \
     && printf '%s\n' "$LOGS" | grep -q '403 Forbidden'; then
    echo "wait: fault manifested — discovery=ok present AND 403 Forbidden present in inventory-sync logs"
    exit 0
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    {
      echo "wait: TIMEOUT after 300s — recent inventory-sync logs:"
      printf '%s\n' "$LOGS" | tail -n 10
      K get pods -n "$NS" -o wide || true
    } >&2
    exit 1
  fi
  sleep 5
done
