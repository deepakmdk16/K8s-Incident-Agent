#!/usr/bin/env bash
# wait.sh — t3-overlapping-config-and-oom: block until the fault has
# observably manifested with its SPECIFIC decisive evidence (never a coarse
# state another failure could also reach):
#   1. both orders-api pods waiting with reason CreateContainerConfigError and
#      a status message containing "couldn't find key db_url" (the exact
#      renamed-key evidence, not just any config error);
#   2. orders-audit Ready=True AND its log proves it resolved the NEW
#      database_url key (the asymmetry that makes gold's side canonical must
#      be in the fixture, not just implied);
#   3. orders-report-worker restartCount >= 2 with lastState OOMKilled (the
#      overlapping genuine decoy has fully manifested too).
# Exit 0 when all three hold; exit 1 with a diagnostic after 300s.
set -u

CTX="${1:-kind-incident-lab}"
NS="orders"
DEADLINE=$(( $(date +%s) + 300 ))

K() { kubectl --context "$CTX" --request-timeout=15s "$@"; }

while :; do
  # 1. renamed-key fault on both orders-api replicas, exact message text
  API_STATES="$(K get pods -n "$NS" -l app=orders-api -o jsonpath='{range .items[*]}{.status.containerStatuses[0].state.waiting.reason}{"\t"}{.status.containerStatuses[0].state.waiting.message}{"\n"}{end}' 2>/dev/null || true)"
  API_OK="$(printf '%s\n' "$API_STATES" | grep -c "^CreateContainerConfigError.*couldn't find key db_url" || true)"

  # 2. healthy new-key consumer: Ready AND logged the resolved value
  AUDIT_READY="$(K get pods -n "$NS" -l app=orders-audit -o jsonpath='{.items[*].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || true)"
  AUDIT_LOG=0
  if [ "$AUDIT_READY" = "True" ]; then
    if K logs -n "$NS" deploy/orders-audit --tail=50 2>/dev/null | grep -q "database_url resolved"; then
      AUDIT_LOG=1
    fi
  fi

  # 3. decoy fully manifested: >=2 restarts, last termination OOMKilled
  RW_RESTARTS="$(K get pods -n "$NS" -l app=orders-report-worker -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null || true)"
  RW_REASON="$(K get pods -n "$NS" -l app=orders-report-worker -o jsonpath='{.items[0].status.containerStatuses[0].lastState.terminated.reason}' 2>/dev/null || true)"

  if [ "${API_OK:-0}" -ge 2 ] && [ "$AUDIT_LOG" -eq 1 ] \
     && [ "${RW_RESTARTS:-0}" -ge 2 ] && [ "$RW_REASON" = "OOMKilled" ]; then
    echo "wait: fault manifested (orders-api CreateContainerConfigError x${API_OK}; orders-audit Ready with database_url log; report-worker restarts=${RW_RESTARTS} OOMKilled)"
    exit 0
  fi

  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    echo "wait: timeout after 300s — fault did not fully manifest" >&2
    echo "  orders-api waiting states: ${API_STATES:-<none>}" >&2
    echo "  orders-audit Ready=${AUDIT_READY:-<none>} database_url-logline=${AUDIT_LOG}" >&2
    echo "  report-worker restarts=${RW_RESTARTS:-<none>} lastState=${RW_REASON:-<none>}" >&2
    exit 1
  fi
  sleep 5
done
