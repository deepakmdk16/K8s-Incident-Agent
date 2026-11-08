#!/usr/bin/env bash
# wait.sh — t2-route-planning-degraded: block until the fault has observably
# manifested as its SPECIFIC decisive evidence, all at once:
#   1. both route-optimizer pods have restarted at least 3 times and at least
#      one is waiting in CrashLoopBackOff (a restart count alone could be a
#      single bad start);
#   2. the optimizer log names the typo'd endpoint as unreachable — the exact
#      line, not just an exit;
#   3. eta-publisher has logged the real solver as reachable (the asymmetry
#      that makes the profile, not the Service, the defect must be in the
#      fixture).
# Exit 0 on manifest; exit 1 with a diagnostic after 300s.
set -u

CTX="${1:-kind-incident-lab}"
DEADLINE=$(( $(date +%s) + 300 ))
BAD='solver route-solvr.dispatch.svc:7000 unreachable'
GOOD='solver route-solver.dispatch.svc:7000 reachable'

K() { kubectl --context "$CTX" --request-timeout=15s "$@"; }

while :; do
  RESTARTS="$(K get pods -n dispatch -l app=route-optimizer \
    -o jsonpath='{range .items[*]}{.status.containerStatuses[0].restartCount}{" "}{end}' 2>/dev/null || true)"
  WAITING="$(K get pods -n dispatch -l app=route-optimizer \
    -o jsonpath='{range .items[*]}{.status.containerStatuses[0].state.waiting.reason}{" "}{end}' 2>/dev/null || true)"
  BADLOG="$(K logs -n dispatch deployment/route-optimizer --tail=20 2>/dev/null | grep -F "$BAD" | head -n 1)"
  GOODLOG="$(K logs -n dispatch deployment/eta-publisher --tail=20 2>/dev/null | grep -F "$GOOD" | head -n 1)"
  MIN=999
  for R in $RESTARTS; do [ "$R" -lt "$MIN" ] 2>/dev/null && MIN="$R"; done
  COUNT="$(printf '%s' "$RESTARTS" | wc -w | tr -d ' ')"
  if [ "$COUNT" = "2" ] && [ "$MIN" -ge 3 ] && printf '%s' "$WAITING" | grep -q CrashLoopBackOff \
     && [ -n "$BADLOG" ] && [ -n "$GOODLOG" ]; then
    echo "wait: fault manifested — restarts=[$RESTARTS] waiting=[$WAITING]"
    echo "wait: optimizer: $BADLOG"
    echo "wait: eta-publisher: $GOODLOG"
    exit 0
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    {
      echo "wait: TIMEOUT after 300s — restarts='${RESTARTS:-<none>}' waiting='${WAITING:-<none>}'"
      echo "  badlog='${BADLOG:-<none>}' goodlog='${GOODLOG:-<none>}'"
      K get pods -n dispatch -o wide || true
      K logs -n dispatch deployment/route-optimizer --tail=10 || true
    } >&2
    exit 1
  fi
  sleep 5
done
