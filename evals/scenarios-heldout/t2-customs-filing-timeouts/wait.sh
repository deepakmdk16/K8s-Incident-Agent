#!/usr/bin/env bash
# wait.sh — t2-customs-filing-timeouts: block until the fault has observably
# manifested as its SPECIFIC decisive evidence, all at once:
#   1. Endpoints customs-docs/manifest-renderer has NO addresses;
#   2. both manifest-renderer pods are Ready=True (the workload is healthy —
#      empty Endpoints with unready pods would be a readiness case);
#   3. manifest-submitter has logged the UNREACHABLE line.
# Exit 0 on manifest; exit 1 with a diagnostic after 300s.
set -u

CTX="${1:-kind-incident-lab}"
DEADLINE=$(( $(date +%s) + 300 ))

K() { kubectl --context "$CTX" --request-timeout=15s "$@"; }

while :; do
  ADDRS="$(K get endpoints -n customs-docs manifest-renderer \
    -o jsonpath='{.subsets[*].addresses[*].ip}' 2>/dev/null || true)"
  READY="$(K get pods -n customs-docs -l app=manifest-renderer \
    -o jsonpath='{range .items[*]}{.status.containerStatuses[0].ready}{" "}{end}' 2>/dev/null || true)"
  SUBLOG="$(K logs -n customs-docs deployment/manifest-submitter --tail=20 2>/dev/null \
    | grep -F 'UNREACHABLE' | head -n 1)"
  if [ -z "$ADDRS" ] && [ "$READY" = "true true " ] && [ -n "$SUBLOG" ]; then
    echo "wait: fault manifested — endpoints empty; renderer Ready x2"
    echo "wait: submitter: $SUBLOG"
    exit 0
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    {
      echo "wait: TIMEOUT after 300s — endpoints='${ADDRS:-<empty>}' ready='${READY:-<unread>}' sublog='${SUBLOG:-<none>}'"
      K get pods,endpoints -n customs-docs -o wide || true
    } >&2
    exit 1
  fi
  sleep 5
done
