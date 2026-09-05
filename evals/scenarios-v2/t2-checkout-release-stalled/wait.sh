#!/usr/bin/env bash
# wait.sh — t2-checkout-release-stalled: block until the fault has observably
# manifested as its SPECIFIC decisive evidence, all at once:
#   1. a ReplicaSet FailedCreate event in checkout whose message names the
#      webhook AND says 'service "policy-guard" not found' (FailedCreate alone
#      is not enough — quota and a missing ServiceAccount produce it too);
#   2. deployment checkout/checkout-api reports 0 updated replicas (the new
#      ReplicaSet never created a pod), 2 ready replicas (one old pod was
#      retired by maxUnavailable 1, the other two still serve), and
#      Progressing=False with reason ProgressDeadlineExceeded.
# Exit 0 on manifest; exit 1 with a diagnostic after 300s.
#
# Race guard: the configuration is applied before the Deployment, but the API
# server registers a webhook through an informer, so a pod could in principle
# slip through in the first fraction of a second. That shows up as
# updatedReplicas >= 1 and is reported as a distinct failure rather than waited
# out — re-inject cleanly instead of capturing a half-manifested fault.
set -u

CTX="${1:-kind-incident-lab}"
DEADLINE=$(( $(date +%s) + 300 ))
WEBHOOK='failed calling webhook "validate.policy-guard.platform.internal"'
MISSING='service "policy-guard" not found'

K() { kubectl --context "$CTX" --request-timeout=15s "$@"; }

while :; do
  MSG="$(K get events -n checkout \
    --field-selector involvedObject.kind=ReplicaSet,reason=FailedCreate \
    -o jsonpath='{range .items[*]}{.message}{"\n"}{end}' 2>/dev/null \
    | grep -F "$WEBHOOK" | grep -F "$MISSING" | head -n 1)"
  UPDATED="$(K get deployment checkout-api -n checkout \
    -o jsonpath='{.status.updatedReplicas}' 2>/dev/null || true)"
  READY="$(K get deployment checkout-api -n checkout \
    -o jsonpath='{.status.readyReplicas}' 2>/dev/null || true)"
  PROG="$(K get deployment checkout-api -n checkout \
    -o jsonpath='{range .status.conditions[?(@.type=="Progressing")]}{.status}/{.reason}{end}' \
    2>/dev/null || true)"
  if [ -n "${UPDATED:-}" ] && [ "$UPDATED" -ge 1 ] 2>/dev/null; then
    echo "wait: RACE — $UPDATED updated replica(s) exist: a pod was admitted before the" \
         "webhook configuration registered. Re-inject; do not capture." >&2
    exit 1
  fi
  if [ -n "$MSG" ] && [ "${READY:-0}" = "2" ] && [ "$PROG" = "False/ProgressDeadlineExceeded" ]; then
    echo "wait: fault manifested — updated=0 ready=$READY progressing=$PROG"
    echo "wait: ReplicaSet FailedCreate: $MSG"
    exit 0
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    {
      echo "wait: TIMEOUT after 300s — msg='${MSG:-<none>}' updated='${UPDATED:-0}'" \
           "ready='${READY:-<unread>}' progressing='${PROG:-<unread>}'"
      K get deployment,replicasets,pods -n checkout -o wide || true
      K get events -n checkout --field-selector reason=FailedCreate || true
      K get validatingwebhookconfigurations || true
    } >&2
    exit 1
  fi
  sleep 5
done
