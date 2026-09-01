#!/usr/bin/env bash
# wait.sh — t2-init-wait-for-migrations: block until the fault has
# observably manifested in its LOOP-FOREVER form (deliberate design: the
# init container stays state.running with restartCount 0 and no failure
# events — a restart-based gate would time out here):
#   1. both billing-api pods report initContainerStatuses[0].state.running,
#   2. the first pod's init log shows a PERSISTENT wait loop: >= 6
#      'waiting for db-primary:5432' lines (~30s of looping — proves a hung
#      wait, not a transient startup poll), AND
#   3. the healthy dependency is actually there: postgres-primary Available
#      (otherwise the counterfactual would be false and the capture invalid).
# Exit 0 on manifest; exit 1 with a diagnostic after 300s.
set -u

CTX="${1:-kind-incident-lab}"
NS="billing"
DEADLINE=$(( $(date +%s) + 300 ))

K() { kubectl --context "$CTX" --request-timeout=15s "$@"; }

while :; do
  RUNNING="$(K get pods -n "$NS" -l app=billing-api \
    -o jsonpath='{range .items[*]}{.status.initContainerStatuses[0].state.running.startedAt}{" "}{end}' 2>/dev/null || true)"
  N_RUNNING="$(printf '%s' "$RUNNING" | wc -w | tr -d ' ')"
  POD0="$(K get pods -n "$NS" -l app=billing-api \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  LOOPS=0
  if [ -n "$POD0" ]; then
    LOOPS="$(K logs -n "$NS" "$POD0" -c wait-for-db --tail=50 2>/dev/null \
      | grep -c 'waiting for db-primary:5432' || true)"
  fi
  DB_AVAIL="$(K get deployment -n "$NS" postgres-primary \
    -o jsonpath='{.status.availableReplicas}' 2>/dev/null || true)"
  if [ "$N_RUNNING" = "2" ] && [ "${LOOPS:-0}" -ge 6 ] && [ "${DB_AVAIL:-0}" = "1" ]; then
    echo "wait: fault manifested — 2 pods Init-running, $LOOPS wait-loop lines on $POD0, postgres-primary Available"
    exit 0
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    {
      echo "wait: TIMEOUT after 300s — init-running=$N_RUNNING loops=${LOOPS:-0} db_avail=${DB_AVAIL:-<none>}"
      K get pods -n "$NS" -o wide || true
    } >&2
    exit 1
  fi
  sleep 5
done
