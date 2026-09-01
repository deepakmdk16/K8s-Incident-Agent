#!/usr/bin/env bash
# wait.sh t1-imagepull-bogus-tag — poll until the fault has observably
# manifested as its SPECIFIC decisive evidence: a pod event in ns web whose
# message names the exact bogus image reference AND contains containerd's
# 'not found' phrasing. Gating on the ErrImagePull/ImagePullBackOff reason
# alone would be coarse (auth failures, DNS/network failures reach it too).
# Fail-fast: any event containing 'toomanyrequests' or 'rate limit' exits 1
# immediately — that text would contradict gold (registry.k8s.io has no
# anonymous rate limits; seeing it means the registry assumption broke) and
# must never be captured into a fixture.
set -u

CTX="${1:-kind-incident-lab}"
NS="web"
IMG="registry.k8s.io/retail/storefront:2.4.1"
TIMEOUT=300
INTERVAL=5

deadline=$(( $(date +%s) + TIMEOUT ))
while :; do
  events="$(kubectl --context "$CTX" get events -n "$NS" \
    -o jsonpath='{range .items[*]}{.reason}{"\t"}{.message}{"\n"}{end}' \
    2>/dev/null || true)"

  if printf '%s\n' "$events" | grep -qiE 'toomanyrequests|rate limit'; then
    echo "wait: FAIL-FAST — registry throttling text in events; this contradicts gold (registry.k8s.io has no anonymous rate limits) and the fixture would be invalid:" >&2
    printf '%s\n' "$events" | grep -iE 'toomanyrequests|rate limit' >&2
    exit 1
  fi

  if printf '%s\n' "$events" | grep -F "$IMG" | grep -q 'not found'; then
    echo "wait: manifested — pull-failure event contains 'not found' and names $IMG"
    exit 0
  fi

  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "wait: TIMEOUT after ${TIMEOUT}s — no event in ns/$NS with a message naming $IMG and containing 'not found'" >&2
    echo "wait: events seen at timeout:" >&2
    printf '%s\n' "$events" | tail -n 20 >&2
    exit 1
  fi
  sleep "$INTERVAL"
done
