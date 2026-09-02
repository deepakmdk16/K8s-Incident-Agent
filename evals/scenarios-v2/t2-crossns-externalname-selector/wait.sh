#!/usr/bin/env bash
# wait.sh — t2-crossns-externalname-selector: block until the fault has
# observably manifested as its SPECIFIC decisive evidence:
#   1. Endpoints payments/payments-gateway has NO addresses (the drift), AND
#   2. both payments-gateway-api pods are Ready=True (the workload is fine —
#      what is broken is the Service in front of it), AND
#   3. storefront/checkout-api has logged the UNREACHABLE line (the paged
#      symptom, one namespace away from the cause).
# Exit 0 on manifest; exit 1 with a diagnostic after 300s.
set -u

CTX="${1:-kind-incident-lab}"
DEADLINE=$(( $(date +%s) + 300 ))

K() { kubectl --context "$CTX" --request-timeout=15s "$@"; }

while :; do
  ADDRS="$(K get endpoints -n payments payments-gateway \
    -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null || true)"
  READY="$(K get pods -n payments -l app=payments-gateway-api \
    -o jsonpath='{range .items[*]}{.status.containerStatuses[0].ready}{" "}{end}' 2>/dev/null || true)"
  CHKLOG="$(K logs -n storefront deployment/checkout-api --tail=20 2>/dev/null \
    | grep 'payment gateway UNREACHABLE' | head -n 1)"
  if [ -z "$ADDRS" ] && [ "$READY" = "true true " ] && [ -n "$CHKLOG" ]; then
    echo "wait: fault manifested — Endpoints payments/payments-gateway empty;" \
         "gateway pods Ready x2; checkout log: $CHKLOG"
    exit 0
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    {
      echo "wait: TIMEOUT after 300s — endpoints='${ADDRS:-<empty>}' ready='${READY:-<unread>}' chklog='${CHKLOG:-<none>}'"
      K get endpoints,pods -n payments -o wide || true
      K get pods -n storefront -o wide || true
    } >&2
    exit 1
  fi
  sleep 5
done
