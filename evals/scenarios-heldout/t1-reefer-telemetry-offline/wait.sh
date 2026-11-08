#!/usr/bin/env bash
# wait.sh — t1-reefer-telemetry-offline: block until the fault has observably
# manifested as its SPECIFIC decisive evidence, all at once:
#   1. both reefer-monitor pods are Pending with PodScheduled=False reason
#      Unschedulable (Pending alone is also what an image pull or a volume
#      wait looks like);
#   2. a FailedScheduling event in cold-chain whose message says
#      'Insufficient cpu' — the request, not some other scheduling constraint.
# Exit 0 on manifest; exit 1 with a diagnostic after 300s.
set -u

CTX="${1:-kind-incident-lab}"
DEADLINE=$(( $(date +%s) + 300 ))

K() { kubectl --context "$CTX" --request-timeout=15s "$@"; }

while :; do
  STATES="$(K get pods -n cold-chain -l app=reefer-monitor \
    -o jsonpath='{range .items[*]}{.status.phase}/{.status.conditions[?(@.type=="PodScheduled")].reason}{" "}{end}' \
    2>/dev/null || true)"
  MSG="$(K get events -n cold-chain --field-selector reason=FailedScheduling \
    -o jsonpath='{range .items[*]}{.message}{"\n"}{end}' 2>/dev/null \
    | grep -F 'Insufficient cpu' | head -n 1)"
  if [ "$STATES" = "Pending/Unschedulable Pending/Unschedulable " ] && [ -n "$MSG" ]; then
    echo "wait: fault manifested — pods: $STATES"
    echo "wait: FailedScheduling: $MSG"
    exit 0
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    {
      echo "wait: TIMEOUT after 300s — states='${STATES:-<none>}' msg='${MSG:-<none>}'"
      K get pods -n cold-chain -o wide || true
      K get events -n cold-chain --sort-by=.lastTimestamp | tail -n 10 || true
    } >&2
    exit 1
  fi
  sleep 5
done
