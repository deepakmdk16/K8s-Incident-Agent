#!/usr/bin/env bash
# wait.sh — t2-selector-drift-empty-endpoints: block until the fault has
# observably manifested as its SPECIFIC decisive evidence:
#   1. Endpoints shop/catalog has NO addresses (the selector drift), AND
#   2. both catalog pods are Ready=True (the quietness: probes pass), AND
#   3. the gateway has logged a failed catalog fetch (the paged symptom).
# Exit 0 on manifest; exit 1 with a diagnostic after 300s.
set -u

CTX="${1:-kind-incident-lab}"
NS="shop"
DEADLINE=$(( $(date +%s) + 300 ))

K() { kubectl --context "$CTX" --request-timeout=15s "$@"; }

while :; do
  ADDRS="$(K get endpoints -n "$NS" catalog \
    -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null || true)"
  READY="$(K get pods -n "$NS" -l app=catalog \
    -o jsonpath='{range .items[*]}{.status.containerStatuses[0].ready}{" "}{end}' 2>/dev/null || true)"
  GWLOG="$(K logs -n "$NS" deployment/storefront-gateway --tail=20 2>/dev/null \
    | grep 'catalog fetch FAILED' | head -n 1)"
  if [ -z "$ADDRS" ] && [ "$READY" = "true true " ] && [ -n "$GWLOG" ]; then
    echo "wait: fault manifested — Endpoints catalog empty; catalog pods Ready x2; gateway log: $GWLOG"
    exit 0
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    {
      echo "wait: TIMEOUT after 300s — endpoints='${ADDRS:-<empty>}' ready='${READY:-<unread>}' gwlog='${GWLOG:-<none>}'"
      K get endpoints,pods -n "$NS" -o wide || true
    } >&2
    exit 1
  fi
  sleep 5
done
