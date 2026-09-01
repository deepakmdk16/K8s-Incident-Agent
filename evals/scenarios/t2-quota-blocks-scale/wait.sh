#!/usr/bin/env bash
# wait.sh — t2-quota-blocks-scale: block until the fault has observably
# manifested as its SPECIFIC decisive evidence: a ReplicaSet FailedCreate
# event in namespace checkout whose message contains
# 'exceeded quota: checkout-quota'. Reason=FailedCreate alone is NOT enough
# (a missing ServiceAccount also produces FailedCreate); the message text is
# what distinguishes this fault. Deliberately no assertion on how many pods
# were admitted — that count varies with the quota-admission arming window.
set -u

CTX="${1:-kind-incident-lab}"
INTERVAL=5
TIMEOUT=300
DEADLINE=$(( $(date +%s) + TIMEOUT ))

while :; do
  MSG="$(kubectl --context "$CTX" get events -n checkout \
    --field-selector involvedObject.kind=ReplicaSet,reason=FailedCreate \
    -o jsonpath='{range .items[*]}{.message}{"\n"}{end}' 2>/dev/null \
    | grep 'exceeded quota: checkout-quota' | head -n 1)"
  if [ -n "$MSG" ]; then
    echo "wait: fault manifested — ReplicaSet FailedCreate: $MSG"
    exit 0
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    {
      echo "wait: TIMEOUT after ${TIMEOUT}s — no ReplicaSet FailedCreate event"
      echo "wait: containing 'exceeded quota: checkout-quota' in namespace checkout."
      echo "wait: FailedCreate events seen:"
      kubectl --context "$CTX" get events -n checkout \
        --field-selector reason=FailedCreate || true
      echo "wait: replicaset state:"
      kubectl --context "$CTX" get replicasets -n checkout -o wide || true
      echo "wait: resourcequota state:"
      kubectl --context "$CTX" get resourcequota -n checkout -o wide || true
    } >&2
    exit 1
  fi
  sleep "$INTERVAL"
done
