#!/usr/bin/env bash
# inject.sh — apply one scenario's fault to the live kind cluster, wait for it
# to observably manifest, then snapshot via capture.sh. This is the only
# sanctioned path from fault.yaml to a fixture (evals/scenarios/README.md);
# exactly one scenario is live per capture.
#
# Flow: delete scenario-labelled admission webhook configurations -> wipe
# non-system namespaces -> apply _noise (T3 ids only) and wait for it to
# settle -> if the scenario ships setup.yaml, apply it and wait for its
# deployments to be Available (pre-fault healthy state, e.g. the version a bad
# rollout replaces) -> apply fault.yaml -> run the scenario's wait.sh ->
# capture. Frozen-root scenarios create only Namespaces and namespaced
# objects, so the namespace wipe fully resets between them (authoring contract
# rule 4). An additive root may also create admission webhook configurations
# (the only way to author an admission fault); those are cluster-scoped, so
# they carry the SCENARIO_LABEL and the reset deletes them by that label
# BEFORE anything tries to create a pod — a lingering failurePolicy=Fail
# webhook whose backend is gone would refuse every pod the next scenario needs.
#
# Usage: inject.sh --id <id> [--root <dir>] [--context <ctx>] [--force] [--no-capture]
#
# --root selects the scenario root (default evals/scenarios, the set frozen at
# case-set-freeze), relative to the repository. New cases are authored in an
# additive root such as evals/scenarios-v2 so the frozen 12-case set keeps its
# identity; fixtures stay in the single evals/fixtures/ root, where every gate
# scan already reaches them. INJECT_REPO_ROOT overrides the repository root so
# tests/test_gates.sh can exercise the refusals below against a scratch tree
# without touching the real scenario roots.
set -u

ROOT="${INJECT_REPO_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
KEEP_NS="default kube-system kube-public kube-node-lease local-path-storage"
WIPE_TIMEOUT=180
NOISE_TIMEOUT=180
FROZEN_ROOT="evals/scenarios"
SCENARIO_LABEL="incident-lab.dev/scenario"
WEBHOOK_KINDS="validatingwebhookconfigurations mutatingwebhookconfigurations"

die() { echo "inject: $*" >&2; exit 1; }

webhook_rules_ok() { # manifest — true iff every webhook document's rules are CREATE on pods only
  awk '
    /^---/ { if (webhook && bad) exit 1; webhook = 0; bad = 0; next }
    /^kind: (Mutating|Validating)WebhookConfiguration/ { webhook = 1 }
    /^ +operations:/ && $0 !~ /^ +operations: \["CREATE"\]$/ { bad = 1 }
    /^ +resources:/ && $0 !~ /^ +resources: \["pods"\]$/ { bad = 1 }
    END { if (webhook && bad) exit 1 }
  ' "$1"
}

ID=""; CONTEXT="kind-incident-lab"; FORCE=0; NOCAP=0; SCEN_ROOT="$FROZEN_ROOT"
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
# Cluster-scoped objects: refused outright for every kind the reset cannot
# undo. Admission webhook configurations are the one exception, and only in an
# additive root — the frozen root's contract (README rule 4) is namespaced
# objects only — and only when every such document carries the label the
# reset deletes by; an unlabelled one would outlive the scenario and refuse
# every later pod create. The checks run before the cluster is contacted.
for MANIFEST in "$SCEN/fault.yaml" "$SCEN/setup.yaml"; do
  [ -s "$MANIFEST" ] || continue
  if grep -qE '^kind: (ClusterRole|ClusterRoleBinding|PersistentVolume|StorageClass)\b' "$MANIFEST"; then
    die "$ID: $(basename "$MANIFEST") creates cluster-scoped objects — scenarios must stay namespaced (README rule 4)"
  fi
  N_WEBHOOKS="$(grep -cE '^kind: (MutatingWebhookConfiguration|ValidatingWebhookConfiguration)\b' "$MANIFEST")"
  [ "$N_WEBHOOKS" -gt 0 ] || continue
  [ "$SCEN_ROOT" != "$FROZEN_ROOT" ] \
    || die "$ID: webhook configurations are cluster-scoped — the frozen root stays namespaced (README rule 4); author admission cases in an additive root"
  N_LABELED="$(grep -cE "^ {2,}$SCENARIO_LABEL: $ID\$" "$MANIFEST")"
  [ "$N_LABELED" -eq "$N_WEBHOOKS" ] \
    || die "$ID: $(basename "$MANIFEST") has $N_WEBHOOKS webhook configuration(s) but $N_LABELED carry the label '$SCENARIO_LABEL: $ID' the reset deletes by"
  # A scenario webhook may intercept pod CREATE and nothing else: one that
  # matched namespace or webhook-configuration DELETE with failurePolicy Fail
  # would refuse the reset itself, and the cluster would need hand surgery.
  # Flow style is required so this lint can read the rules without a parser.
  # Scoped per YAML document: a Deployment's container `resources:` block in
  # the same file is not a webhook rule.
  webhook_rules_ok "$MANIFEST" \
    || die "$ID: $(basename "$MANIFEST") webhook rules must be exactly operations: [\"CREATE\"] on resources: [\"pods\"] (the reset must stay admissible)"
done
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

# --- reset, cluster-scoped half: a previous scenario's admission webhook
# configurations go first, because with failurePolicy=Fail and no backend
# they would refuse every pod the noise pack or setup.yaml creates below.
# Only scenario-labelled configurations are this pipeline's to delete; any
# other is a stray from outside it and is refused, like a non-empty default.
for KIND in $WEBHOOK_KINDS; do
  STRAY="$(K get "$KIND" -l "!$SCENARIO_LABEL" -o name 2>/dev/null || true)"
  [ -z "$STRAY" ] || die "unlabelled $KIND present — not created by inject.sh, refusing to touch it:$(printf '\n  %s' $STRAY)"
  OWNED="$(K get "$KIND" -l "$SCENARIO_LABEL" -o name 2>/dev/null || true)"
  [ -z "$OWNED" ] && continue
  echo "inject: deleting scenario-owned $KIND:$(printf ' %s' $OWNED)"
  K delete "$KIND" -l "$SCENARIO_LABEL" >/dev/null || die "could not delete scenario-owned $KIND"
done

# --- reset, namespaced half: remove every namespace a previous scenario (or
# noise pack) owned
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
