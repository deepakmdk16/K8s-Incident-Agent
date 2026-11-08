#!/usr/bin/env bash
# wait.sh — t3-driver-login-failures: the quiet fault, the loud in-namespace
# decoy, AND both decoys in the held-out noise pack, so the capture cannot
# race a decoy that has not broken yet. Waiting on the pack's decoys here is
# what lets evals/scenarios-heldout/_noise/namespaces.txt stay a healthy-only
# wait list.
#
# Decisive evidence, all at once:
#   1. the driver-auth log contains BOTH 'discovery=ok' (the token header is
#      sent) and '403 Forbidden' (the namespaced read is denied);
#   2. driver-auth's pod is Ready (the fault is quiet — a crashing auth pod
#      would be a different case);
#   3. badge-photo-resizer has restarted at least 3 times, is not Ready, its
#      container is either waiting in CrashLoopBackOff or terminated with
#      exit code 1, and its log carries the decisive 'still not set' line.
#      (This kubelet reports a short-lived container as terminated/Error
#      through its back-off and may never show CrashLoopBackOff — observed on
#      two injections, 2026-11-08 — so the status string is not the evidence;
#      the climbing restart count, the exit code and the log line are.)
#   4. trailer-pool/pool-balancer is Pending with reason Unschedulable;
#   5. fuel-tax has at least one Failed job.
# Exit 0 on manifest; exit 1 with a diagnostic after 300s.
set -u

CTX="${1:-kind-incident-lab}"
DEADLINE=$(( $(date +%s) + 300 ))

K() { kubectl --context "$CTX" --request-timeout=15s "$@"; }

while :; do
  AUTHLOG="$(K logs -n driver-portal deployment/driver-auth --tail=40 2>/dev/null || true)"
  DISC="$(printf '%s' "$AUTHLOG" | grep -F 'discovery=ok' | head -n 1)"
  DENY="$(printf '%s' "$AUTHLOG" | grep -F '403 Forbidden' | head -n 1)"
  AUTHREADY="$(K get pods -n driver-portal -l app=driver-auth \
    -o jsonpath='{.items[0].status.containerStatuses[0].ready}' 2>/dev/null || true)"
  RESTARTS="$(K get pods -n driver-portal -l app=badge-photo-resizer \
    -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null || true)"
  WAITING="$(K get pods -n driver-portal -l app=badge-photo-resizer \
    -o jsonpath='{.items[0].status.containerStatuses[0].state.waiting.reason}' 2>/dev/null || true)"
  EXITED="$(K get pods -n driver-portal -l app=badge-photo-resizer \
    -o jsonpath='{.items[0].status.containerStatuses[0].state.terminated.exitCode}' 2>/dev/null || true)"
  RESIZERREADY="$(K get pods -n driver-portal -l app=badge-photo-resizer \
    -o jsonpath='{.items[0].status.containerStatuses[0].ready}' 2>/dev/null || true)"
  RESIZERLOG="$(K logs -n driver-portal deployment/badge-photo-resizer --tail=5 2>/dev/null \
    | grep -F 'PHOTO_STORE_URL still not set' | head -n 1)"
  LOOPING=0
  if [ "$WAITING" = "CrashLoopBackOff" ] || [ "${EXITED:-}" = "1" ]; then LOOPING=1; fi
  PENDING="$(K get pods -n trailer-pool -l app=pool-balancer \
    -o jsonpath='{.items[0].status.conditions[?(@.reason=="Unschedulable")].reason}' 2>/dev/null || true)"
  JOBFAIL="$(K get jobs -n fuel-tax \
    -o jsonpath='{range .items[*]}{.status.failed}{" "}{end}' 2>/dev/null | tr -d ' ' || true)"
  if [ -n "$DISC" ] && [ -n "$DENY" ] && [ "$AUTHREADY" = "true" ] \
     && [ -n "$RESTARTS" ] && [ "${RESTARTS:-0}" -ge 3 ] && [ "$LOOPING" = "1" ] \
     && [ "$RESIZERREADY" = "false" ] && [ -n "$RESIZERLOG" ] \
     && [ "$PENDING" = "Unschedulable" ] && [ -n "$JOBFAIL" ]; then
    echo "wait: fault + decoys manifested — auth Ready; '$DISC'; '$DENY';" \
         "resizer restarts=$RESTARTS (waiting='$WAITING' exit='$EXITED'); pool-balancer Unschedulable;" \
         "fuel-tax failed jobs='$JOBFAIL'"
    exit 0
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    {
      echo "wait: TIMEOUT after 300s — disc='${DISC:-<none>}' deny='${DENY:-<none>}' authReady='${AUTHREADY:-<unread>}'"
      echo "  resizerRestarts='${RESTARTS:-<unread>}' waiting='${WAITING:-<none>}' exit='${EXITED:-<none>}' ready='${RESIZERREADY:-<unread>}' log='${RESIZERLOG:-<none>}'"
      echo "  pool='${PENDING:-<not-unschedulable>}' jobFailed='${JOBFAIL:-<none>}'"
      K get pods -A -o wide | grep -vE 'Running|Completed' || true
      K logs -n driver-portal deployment/driver-auth --tail=6 || true
    } >&2
    exit 1
  fi
  sleep 5
done
