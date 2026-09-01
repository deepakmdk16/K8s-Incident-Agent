#!/usr/bin/env bash
# wait.sh — t1-unschedulable-cpu-requests: block until the fault has
# observably manifested. Gates on the case's SPECIFIC decisive evidence,
# never a coarse Pending phase (which unbound PVCs, quota rejections, and
# slow pulls also reach):
#   1. the fraud-scoring pod's PodScheduled condition is False with reason
#      Unschedulable, AND
#   2. a FailedScheduling event for that pod mentions "Insufficient cpu".
# Exit 0 once both hold; exit 1 with a diagnostic after 300s.
set -u

CTX="${1:-kind-incident-lab}"
NS="fraud"
SELECTOR="app=fraud-scoring"
TIMEOUT=300
INTERVAL=5

K() { kubectl --context "$CTX" --request-timeout=30s "$@"; }

DEADLINE=$(( $(date +%s) + TIMEOUT ))
POD=""
SCHED=""
while :; do
  POD="$(K get pods -n "$NS" -l "$SELECTOR" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  if [ -n "$POD" ]; then
    SCHED="$(K get pod -n "$NS" "$POD" \
      -o jsonpath='{range .status.conditions[?(@.type=="PodScheduled")]}{.status}/{.reason}{end}' \
      2>/dev/null || true)"
    if [ "$SCHED" = "False/Unschedulable" ]; then
      if K get events -n "$NS" \
           --field-selector "reason=FailedScheduling,involvedObject.name=$POD" \
           -o jsonpath='{range .items[*]}{.message}{"\n"}{end}' 2>/dev/null \
         | grep -q "Insufficient cpu"; then
        echo "wait: fault manifested — pod $NS/$POD PodScheduled=False/Unschedulable, FailedScheduling event mentions 'Insufficient cpu'"
        exit 0
      fi
    fi
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    {
      echo "wait: timeout after ${TIMEOUT}s — fault did not manifest for $NS ($SELECTOR)"
      echo "wait: last pod seen: ${POD:-<none>}; PodScheduled status/reason: ${SCHED:-<unread>}"
      K get pods -n "$NS" -l "$SELECTOR" -o wide 2>&1 || true
      K get events -n "$NS" --field-selector reason=FailedScheduling 2>&1 || true
    } >&2
    exit 1
  fi
  sleep "$INTERVAL"
done
