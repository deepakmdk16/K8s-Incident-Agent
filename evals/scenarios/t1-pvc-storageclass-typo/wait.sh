#!/usr/bin/env bash
# wait.sh — t1-pvc-storageclass-typo: block until the fault has observably
# manifested as its SPECIFIC decisive evidence (never bare Pending, which
# quota rejections and unschedulable pods also reach):
#   1. PVC data-metrics-db-0 in analytics is phase Pending, AND
#   2. an event for that PVC names storageclass "fast-ssd" as not found, AND
#   3. the metrics-db-0 pod is Pending (held by the unbound claim).
# Exit 0 on manifest; exit 1 with a diagnostic after 300s.
set -u

CTX="${1:-kind-incident-lab}"
NS="analytics"
PVC="data-metrics-db-0"
DEADLINE=$(( $(date +%s) + 300 ))

K() { kubectl --context "$CTX" --request-timeout=15s "$@"; }

while :; do
  PHASE="$(K get pvc -n "$NS" "$PVC" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
  EV="$(K get events -n "$NS" \
    --field-selector "involvedObject.kind=PersistentVolumeClaim,involvedObject.name=$PVC" \
    -o jsonpath='{range .items[*]}{.message}{"\n"}{end}' 2>/dev/null \
    | grep 'fast-ssd' | grep -i 'not found' | head -n 1)"
  PODPHASE="$(K get pod -n "$NS" metrics-db-0 -o jsonpath='{.status.phase}' 2>/dev/null || true)"
  if [ "$PHASE" = "Pending" ] && [ -n "$EV" ] && [ "$PODPHASE" = "Pending" ]; then
    echo "wait: fault manifested — PVC $NS/$PVC Pending; event: $EV; pod metrics-db-0 Pending"
    exit 0
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    {
      echo "wait: TIMEOUT after 300s — pvc phase=${PHASE:-<none>} pod phase=${PODPHASE:-<none>}"
      K get pvc,pods -n "$NS" -o wide || true
      K get events -n "$NS" || true
    } >&2
    exit 1
  fi
  sleep 5
done
