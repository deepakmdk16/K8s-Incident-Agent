#!/usr/bin/env bash
# wait.sh — t3-crossns-decoys: the t2-crossns-externalname-selector conditions
# PLUS every decoy in the v2 noise pack, so the capture cannot race a decoy
# that has not broken yet. Waiting on the decoys here is what lets
# evals/scenarios-v2/_noise/namespaces.txt stay a healthy-only wait list:
# a decoy is by construction never Available, so inject.sh must not block on it.
#
# Decisive evidence, all at once:
#   1. Endpoints payments/payments-gateway has NO addresses, AND
#   2. both payments-gateway-api pods are Ready=True, AND
#   3. storefront/checkout-api has logged the UNREACHABLE line, AND
#   4. release-canary/canary-runner has restarted at least twice, AND
#   5. report-exports has at least one Failed job, AND
#   6. batch-compute/model-trainer is Pending with FailedScheduling.
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
  CANARY="$(K get pods -n release-canary -l app=canary-runner \
    -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null || true)"
  JOBFAIL="$(K get jobs -n report-exports \
    -o jsonpath='{range .items[*]}{.status.failed}{" "}{end}' 2>/dev/null | tr -d ' ' || true)"
  PENDING="$(K get pods -n batch-compute -l app=model-trainer \
    -o jsonpath='{.items[0].status.conditions[?(@.reason=="Unschedulable")].reason}' 2>/dev/null || true)"
  if [ -z "$ADDRS" ] && [ "$READY" = "true true " ] && [ -n "$CHKLOG" ] \
     && [ -n "$CANARY" ] && [ "${CANARY:-0}" -ge 2 ] \
     && [ -n "$JOBFAIL" ] && [ "$PENDING" = "Unschedulable" ]; then
    echo "wait: fault + decoys manifested — endpoints empty; gateway Ready x2;" \
         "canary restarts=$CANARY; report-exports failed jobs='$JOBFAIL';" \
         "model-trainer Unschedulable"
    exit 0
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    {
      echo "wait: TIMEOUT after 300s — endpoints='${ADDRS:-<empty>}' ready='${READY:-<unread>}'"
      echo "  chklog='${CHKLOG:-<none>}' canaryRestarts='${CANARY:-<unread>}'"
      echo "  jobFailed='${JOBFAIL:-<none>}' trainer='${PENDING:-<not-unschedulable>}'"
      K get pods -A -o wide | grep -vE 'Running|Completed' || true
    } >&2
    exit 1
  fi
  sleep 5
done
