#!/usr/bin/env bash
# inject.sh — apply one scenario's fault to the live kind cluster, wait for it
# to observably manifest, then snapshot via capture.sh. This is the only
# sanctioned path from fault.yaml to a fixture (evals/scenarios/README.md);
# exactly one scenario is live per capture.
#
# Flow: wipe non-system namespaces -> apply _noise (T3 ids only) and wait for
# it to settle -> if the scenario ships setup.yaml, apply it and wait for its
# deployments to be Available (pre-fault healthy state, e.g. the version a bad
# rollout replaces) -> apply fault.yaml -> run the scenario's wait.sh ->
# capture. Scenarios create only Namespaces and namespaced objects, so the
# wipe fully resets between scenarios (authoring contract rule 3).
#
# Usage: inject.sh --id <id> [--root <dir>] [--context <ctx>] [--force] [--no-capture]
#
# --root selects the scenario root (default evals/scenarios, the set frozen at
# case-set-freeze). New cases are authored in an additive root such as
# evals/scenarios-v2 so the frozen 12-case set keeps its identity; fixtures
# stay in the single evals/fixtures/ root, where every gate scan already
# reaches them.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KEEP_NS="default kube-system kube-public kube-node-lease local-path-storage"
WIPE_TIMEOUT=180
NOISE_TIMEOUT=180

die() { echo "inject: $*" >&2; exit 1; }

ID=""; CONTEXT="kind-incident-lab"; FORCE=0; NOCAP=0; SCEN_ROOT="evals/scenarios"
while [ $# -gt 0 ]; do
  case "$1" in
    --id) ID="$2"; shift 2 ;;
    --root) SCEN_ROOT="$2"; shift 2 ;;
    --context) CONTEXT="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    --no-capture) NOCAP=1; shift ;;
    *) die "unknown arg: $1 (usage: --id X [--root D] [--context C] [--force] [--no-capture])" ;;
  esac
done
[ -n "$ID" ] || die "--id is required"

SCEN="$ROOT/$SCEN_ROOT/$ID"
[ -d "$SCEN" ] || die "no scenario dir $SCEN"
[ -s "$SCEN/fault.yaml" ] || die "$ID: fault.yaml missing or empty"
[ -s "$SCEN/page.txt" ] || die "$ID: page.txt missing or empty (symptom-first: no page, no scenario)"
[ -x "$SCEN/wait.sh" ] || die "$ID: wait.sh missing or not executable"
if grep -qE '^kind: (ClusterRole|ClusterRoleBinding|PersistentVolume|StorageClass|MutatingWebhookConfiguration|ValidatingWebhookConfiguration)\b' "$SCEN/fault.yaml"; then
  die "$ID: fault.yaml creates cluster-scoped objects — scenarios must stay namespaced (README rule 3)"
fi
FIXTURE="$ROOT/evals/fixtures/$ID"
if [ -e "$FIXTURE" ] && [ "$FORCE" -ne 1 ]; then
  die "$FIXTURE exists — captured fixtures are frozen; --force only for a disclosed re-capture"
fi

K() { kubectl --context "$CONTEXT" --request-timeout=30s "$@"; }
K version >/dev/null 2>&1 || die "context '$CONTEXT' unreachable"

# Objects left in `default` (smoke tests, manual pokes) would silently enter
# every fixture as unowned noise — refuse rather than clean up blind.
STRAY="$(K get pods,deployments,replicasets,statefulsets,daemonsets,jobs,configmaps \
  -n default -o name 2>/dev/null | grep -v '^configmap/kube-root-ca.crt$' || true)"
[ -z "$STRAY" ] || die "namespace 'default' is not empty — remove stray objects first:$(printf '\n  %s' $STRAY)"

# --- reset: remove every namespace a previous scenario (or noise pack) owned
echo "inject: wiping non-system namespaces"
for NS in $(K get namespaces -o jsonpath='{.items[*].metadata.name}'); do
  case " $KEEP_NS " in *" $NS "*) continue ;; esac
  K delete namespace "$NS" --wait=false >/dev/null 2>&1 || true
done
DEADLINE=$(( $(date +%s) + WIPE_TIMEOUT ))
while :; do
  LEFT=""
  for NS in $(K get namespaces -o jsonpath='{.items[*].metadata.name}'); do
    case " $KEEP_NS " in *" $NS "*) continue ;; esac
    LEFT="$LEFT $NS"
  done
  [ -z "$LEFT" ] && break
  [ "$(date +%s)" -ge "$DEADLINE" ] && die "namespaces still terminating after ${WIPE_TIMEOUT}s:$LEFT"
  sleep 3
done

# --- T3 scenarios run against the noise pack (README: capture protocol)
case "$ID" in
  t3-*)
    # A scenario root brings its own noise pack if it ships one; the frozen
    # pack is the fallback, so t3-* cases under evals/scenarios/ are unaffected.
    NOISE="$ROOT/$SCEN_ROOT/_noise"
    [ -s "$NOISE/noise.yaml" ] || NOISE="$ROOT/evals/scenarios/_noise"
    echo "inject: noise pack $NOISE"
    [ -s "$NOISE/noise.yaml" ] || die "T3 scenario but $NOISE/noise.yaml missing"
    [ -s "$NOISE/namespaces.txt" ] || die "T3 scenario but $NOISE/namespaces.txt missing"
    echo "inject: applying noise pack"
    K apply -f "$NOISE/noise.yaml" >/dev/null || die "noise apply failed"
    while read -r NS; do
      [ -n "$NS" ] || continue
      K wait --for=condition=Available deployment --all -n "$NS" \
        --timeout="${NOISE_TIMEOUT}s" >/dev/null \
        || die "noise deployments in $NS not Available within ${NOISE_TIMEOUT}s"
    done <"$NOISE/namespaces.txt"
    ;;
esac

# --- optional pre-fault healthy state (two-phase scenarios, e.g. a rollout
# that was working before the faulty update)
if [ -s "$SCEN/setup.yaml" ]; then
  echo "inject: applying $ID/setup.yaml (pre-fault state)"
  K apply -f "$SCEN/setup.yaml" >/dev/null || die "setup apply failed"
  for NS in $(K get namespaces -o jsonpath='{.items[*].metadata.name}'); do
    case " $KEEP_NS " in *" $NS "*) continue ;; esac
    DEPLOYS="$(K get deployments -n "$NS" -o name 2>/dev/null)"
    [ -n "$DEPLOYS" ] || continue
    K wait --for=condition=Available deployment --all -n "$NS" \
      --timeout="${NOISE_TIMEOUT}s" >/dev/null \
      || die "setup deployments in $NS not Available within ${NOISE_TIMEOUT}s"
  done
fi

# --- fault in, wait for it to manifest, snapshot
echo "inject: applying $ID/fault.yaml"
K apply -f "$SCEN/fault.yaml" || die "fault apply failed"
echo "inject: waiting for fault to manifest ($SCEN/wait.sh)"
"$SCEN/wait.sh" "$CONTEXT" || die "$ID: wait condition not met — fault did not manifest"

if [ "$NOCAP" -eq 1 ]; then
  echo "inject: fault live, capture skipped (--no-capture)"
  exit 0
fi
FORCEARG=""; [ "$FORCE" -eq 1 ] && FORCEARG="--force"
bash "$ROOT/evals/capture.sh" --id "$ID" --page-file "$SCEN/page.txt" \
  --context "$CONTEXT" $FORCEARG
