#!/usr/bin/env bash
# wait.sh — t2-yard-gate-release-blocked: block until the fault has observably
# manifested as its SPECIFIC decisive evidence, all at once:
#   1. a ReplicaSet FailedCreate event in yard-ops whose message says
#      'violates PodSecurity "restricted:latest"' (FailedCreate alone is not
#      enough — quota, a missing ServiceAccount and a failing webhook produce
#      it too);
#   2. deployment yard-ops/gate-checkin reports 0 updated replicas (the new
#      ReplicaSet never created a pod), 2 ready replicas (one old pod was
#      retired by maxUnavailable 1, the other two still serve), and
#      Progressing=False with reason ProgressDeadlineExceeded.
# Exit 0 on manifest; exit 1 with a diagnostic after 300s.
#
# Race guard: the Namespace update precedes the Deployment in fault.yaml and
# is synchronous, so a new pod should never be admitted; if one is,
# updatedReplicas >= 1 is reported as a distinct failure rather than waited
# out — re-inject cleanly instead of capturing a half-manifested fault.
set -u

CTX="${1:-kind-incident-lab}"
DEADLINE=$(( $(date +%s) + 300 ))
VIOLATION='violates PodSecurity "restricted:latest"'

K() { kubectl --context "$CTX" --request-timeout=15s "$@"; }

while :; do
  MSG="$(K get events -n yard-ops \
    --field-selector involvedObject.kind=ReplicaSet,reason=FailedCreate \
    -o jsonpath='{range .items[*]}{.message}{"\n"}{end}' 2>/dev/null \
    | grep -F "$VIOLATION" | head -n 1)"
  UPDATED="$(K get deployment gate-checkin -n yard-ops \
    -o jsonpath='{.status.updatedReplicas}' 2>/dev/null || true)"
  READY="$(K get deployment gate-checkin -n yard-ops \
    -o jsonpath='{.status.readyReplicas}' 2>/dev/null || true)"
  PROG="$(K get deployment gate-checkin -n yard-ops \
    -o jsonpath='{range .status.conditions[?(@.type=="Progressing")]}{.status}/{.reason}{end}' \
    2>/dev/null || true)"
  if [ -n "${UPDATED:-}" ] && [ "$UPDATED" -ge 1 ] 2>/dev/null; then
    echo "wait: RACE — $UPDATED updated replica(s) exist: a pod was admitted under the" \
         "new label set. Re-inject; do not capture." >&2
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
      K get deployment,replicasets,pods -n yard-ops -o wide || true
      K get events -n yard-ops --field-selector reason=FailedCreate || true
      K get namespace yard-ops --show-labels || true
    } >&2
    exit 1
  fi
  sleep 5
done
