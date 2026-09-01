#!/usr/bin/env bash
# capture.sh — snapshot a live (faulted) kind cluster into an offline text
# fixture under evals/fixtures/<id>/. The eval replays from fixtures;
# reproducing it needs no cluster (docs/decisions/problem-selection.md, eval design).
#
# Binding requirements implemented here (decision doc, "Design requirements"):
#   req 2 — secrets scrubbed BY CONSTRUCTION: Secret data/stringData values are
#           replaced with REDACTED-BY-CAPTURE, last-applied-configuration
#           annotations and managedFields are stripped, and a final pass
#           redacts JWTs and home paths across every captured file. The
#           kubeconfig is never captured. checkpoints.sh independently scans
#           evals/fixtures/ for anything construction missed.
#   req 4 — over-capture: all describes, BOTH log channels (current and
#           --previous, each tolerated on failure with per-channel status
#           recorded in scenario.yaml — see docs/failure-modes.md 2026-08-28),
#           events as JSON, per-kind manifests, node/PV/endpoints state.
#
# Fixture layout (the contract the replay tools consume):
#   scenario.yaml                 metadata + capture policy + log-channel ledger
#   page.txt                      the symptom-first page the agent starts from
#   cluster/                      cluster-scoped state (nodes, events, PV, RBAC)
#   ns/<namespace>/<kind>.json    scrubbed `kubectl get -o json` per kind
#   ns/<namespace>/describe/      per-object `kubectl describe`
#   ns/<namespace>/logs/          <pod>__<container>.log / .previous.log
#
# Usage: capture.sh --id <scenario-id> --page-file <file>
#                   [--context <kubectl-context>] [--out <dir>] [--force]
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REDACT="REDACTED-BY-CAPTURE"
WORKLOAD_TAIL=2000
SYSTEM_TAIL=300
SYS_NS="kube-system kube-public kube-node-lease local-path-storage"
NS_KINDS="pods deployments replicasets statefulsets daemonsets jobs cronjobs \
services endpoints endpointslices ingresses configmaps secrets serviceaccounts \
roles rolebindings persistentvolumeclaims resourcequotas limitranges \
networkpolicies horizontalpodautoscalers poddisruptionbudgets"

die() { echo "capture: $*" >&2; exit 1; }

ID=""; PAGE_FILE=""; CONTEXT="kind-incident-lab"
OUTBASE="$ROOT/evals/fixtures"; FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --id) ID="$2"; shift 2 ;;
    --page-file) PAGE_FILE="$2"; shift 2 ;;
    --context) CONTEXT="$2"; shift 2 ;;
    --out) OUTBASE="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    *) die "unknown arg: $1 (usage: --id X --page-file F [--context C] [--out D] [--force])" ;;
  esac
done
[ -n "$ID" ] || die "--id is required"
case "$ID" in *[!a-z0-9-]*) die "--id must be lowercase-kebab (got: $ID)" ;; esac
[ -s "${PAGE_FILE:-}" ] || die "--page-file is required and must be non-empty (symptom-first: no page, no scenario)"
command -v kubectl >/dev/null || die "kubectl not found"
command -v jq >/dev/null || die "jq not found"
command -v perl >/dev/null || die "perl not found"

K() { kubectl --context "$CONTEXT" --request-timeout=15s "$@"; }
K version >/dev/null 2>&1 || die "context '$CONTEXT' unreachable"

DEST="$OUTBASE/$ID"
if [ -e "$DEST" ] && [ "$FORCE" -ne 1 ]; then
  die "$DEST exists — captured fixtures are frozen; pass --force only for a disclosed re-capture"
fi
TMPD="$OUTBASE/.capture-$ID.$$"
ERRD="$(mktemp -d)"
mkdir -p "$TMPD/cluster" || die "cannot create $TMPD"

# Scrub applied to every captured JSON document (list or single object).
JQ_SCRUB='
def scrub_one:
  (if (.metadata? // null) != null then
     .metadata |= (del(.managedFields)
       | if (.annotations? // null) != null then
           .annotations |= del(."kubectl.kubernetes.io/last-applied-configuration")
         else . end)
   else . end)
  | (if .kind == "Secret" then
       .data = ((.data // {}) | with_entries(.value = "REDACTED-BY-CAPTURE"))
       | (if (.stringData? // null) != null then
            .stringData |= with_entries(.value = "REDACTED-BY-CAPTURE")
          else . end)
     else . end);
if (.items? // null) != null then .items |= map(scrub_one) else scrub_one end
'
# Belt AND braces for the secrets kind: redact every item regardless of its
# per-item TypeMeta, so a kubectl output-shape change can never leak values.
JQ_SCRUB_SECRETS="$JQ_SCRUB
| if (.items? // null) != null then
    .items |= map(.data = ((.data // {}) | with_entries(.value = \"$REDACT\")))
  else . end"

cap_json() { # outfile, kubectl get args... — failure is recorded IN BAND
  local out="$1"; shift
  local prog="$JQ_SCRUB"
  case "$*" in *" secrets"*|secrets*) prog="$JQ_SCRUB_SECRETS" ;; esac
  if ! K get "$@" -o json 2>"$ERRD/e" | jq "$prog" >"$out" 2>>"$ERRD/e" \
     || [ ! -s "$out" ]; then
    local why; why="$(head -c 160 "$ERRD/e" | tr '\n' ' ')"
    echo "capture: WARN get $* unavailable: $why" >&2
    jq -n --arg cmd "get $*" --arg err "$why" \
      '{capture_error: true, cmd: $cmd, error: $err}' >"$out"
  fi
}

echo "capture: $ID from context $CONTEXT"

# --- cluster-scoped state ---------------------------------------------------
K version -o json >"$TMPD/cluster/version.json" 2>/dev/null \
  || die "cannot read cluster version"
K api-resources >"$TMPD/cluster/api-resources.txt" 2>/dev/null
K get all -A -o wide >"$TMPD/cluster/get-all.txt" 2>/dev/null \
  || die "get all -A failed"
K describe nodes >"$TMPD/cluster/nodes.describe.txt" 2>/dev/null
cap_json "$TMPD/cluster/nodes.json" nodes
cap_json "$TMPD/cluster/events.json" events -A
cap_json "$TMPD/cluster/namespaces.json" namespaces
cap_json "$TMPD/cluster/pv.json" pv
cap_json "$TMPD/cluster/storageclasses.json" storageclasses
cap_json "$TMPD/cluster/clusterroles.json" clusterroles
cap_json "$TMPD/cluster/clusterrolebindings.json" clusterrolebindings

# --- namespaced state: per-kind JSON, per-object describe, per-container logs
NAMESPACES="$(K get namespaces -o jsonpath='{.items[*].metadata.name}')"
[ -n "$NAMESPACES" ] || die "no namespaces returned"
for NS in $NAMESPACES; do
  echo "capture:   namespace $NS"
  mkdir -p "$TMPD/ns/$NS/describe" "$TMPD/ns/$NS/logs"
  for KIND in $NS_KINDS; do
    cap_json "$TMPD/ns/$NS/$KIND.json" "$KIND" -n "$NS"
    K get "$KIND" -n "$NS" -o name 2>/dev/null | while read -r NAME; do
      [ -n "$NAME" ] || continue
      SAFE="$(printf '%s' "$NAME" | tr '/' '_')"
      K describe "$NAME" -n "$NS" >"$TMPD/ns/$NS/describe/$SAFE.txt" 2>/dev/null
    done
  done

  TAIL="$WORKLOAD_TAIL"
  case " $SYS_NS " in *" $NS "*) TAIL="$SYSTEM_TAIL" ;; esac
  jq -r '.items[] | .metadata.name as $p
         | ((.spec.initContainers // []) + (.spec.containers // []))[]
         | "\($p) \(.name)"' "$TMPD/ns/$NS/pods.json" 2>/dev/null \
  | while read -r POD CTR; do
      [ -n "$POD" ] || continue
      BASE="$TMPD/ns/$NS/logs/${POD}__${CTR}"
      CUR="ok"; CUR_WHY=""
      if ! K logs -n "$NS" "$POD" -c "$CTR" --tail="$TAIL" --timestamps \
           >"$BASE.log" 2>"$ERRD/e"; then
        CUR="unavailable"; CUR_WHY="$(head -c 160 "$ERRD/e" | tr '\n' ' ')"
        rm -f "$BASE.log"
      fi
      PREV="ok"; PREV_WHY=""
      if ! K logs -n "$NS" "$POD" -c "$CTR" --previous --tail="$TAIL" \
           --timestamps >"$BASE.previous.log" 2>"$ERRD/e"; then
        PREV="unavailable"; PREV_WHY="$(head -c 160 "$ERRD/e" | tr '\n' ' ')"
        rm -f "$BASE.previous.log"
      fi
      { echo "  - pod: $NS/$POD"
        echo "    container: $CTR"
        echo "    current: $CUR${CUR_WHY:+  # $CUR_WHY}"
        echo "    previous: $PREV${PREV_WHY:+  # $PREV_WHY}"
      } >>"$TMPD/.channels"
    done
done
[ -s "$TMPD/.channels" ] || die "no pod containers found — nothing to diagnose in this cluster"

# --- scenario manifest ------------------------------------------------------
cp "$PAGE_FILE" "$TMPD/page.txt"
SRVV="$(jq -r '.serverVersion.gitVersion // "unknown"' "$TMPD/cluster/version.json")"
CLIV="$(jq -r '.clientVersion.gitVersion // "unknown"' "$TMPD/cluster/version.json")"
NODEINFO="$(jq -r '.items[0].status.nodeInfo
  | "kubelet=\(.kubeletVersion) runtime=\(.containerRuntimeVersion) os=\(.osImage)"' \
  "$TMPD/cluster/nodes.json" 2>/dev/null || echo unknown)"
{ echo "schema: 1"
  echo "id: $ID"
  echo "mode: captured"
  echo "captured_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "context: $CONTEXT"
  echo "kubectl_client: $CLIV"
  echo "server: $SRVV"
  echo "node: $NODEINFO"
  echo "capture_policy:"
  echo "  workload_log_tail: $WORKLOAD_TAIL"
  echo "  system_log_tail: $SYSTEM_TAIL"
  echo "  system_namespaces: $SYS_NS"
  echo "log_channels:"
  cat "$TMPD/.channels"
} >"$TMPD/scenario.yaml"
rm -f "$TMPD/.channels"

# --- final scrub pass over every captured byte ------------------------------
# The kubeconfig-embed rule also catches kube-public/cluster-info, whose
# ConfigMap legitimately embeds the PUBLIC cluster CA — public or not, the
# checkpoints scan cannot tell base64'd PEM apart, so redact by construction
# rather than teaching the gate to allowlist (gates are fixed; code bends).
find "$TMPD" -type f -print0 | xargs -0 perl -pi -e '
  s{eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}(\.[A-Za-z0-9_-]+)?}{REDACTED-JWT}g;
  s{((?:certificate-authority|client-certificate|client-key)-data:\s*)[A-Za-z0-9+/=]{16,}}{${1}REDACTED-BY-CAPTURE}g;
  s{/(Users|home)/[A-Za-z0-9._-]+}{/REDACTED-HOME}g;
'

# --- self-check: an incomplete or leaky fixture must never land -------------
BAD=0
for req in scenario.yaml page.txt cluster/get-all.txt cluster/events.json \
           cluster/nodes.json cluster/version.json; do
  [ -s "$TMPD/$req" ] || { echo "capture: SELF-CHECK missing $req" >&2; BAD=1; }
done
ls "$TMPD"/ns/*/pods.json >/dev/null 2>&1 \
  || { echo "capture: SELF-CHECK no ns/*/pods.json" >&2; BAD=1; }
find "$TMPD" -path '*/describe/*' -name '*.txt' | grep -q . \
  || { echo "capture: SELF-CHECK no describes" >&2; BAD=1; }
# Same four pattern alternatives as the checkpoints.sh fixture scan — any
# drift here would install a "frozen" fixture that then fails the commit gate.
if grep -RqE '(eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}|LS0tLS1CRUdJTi|(certificate-authority|client-certificate|client-key)-data: *[A-Za-z0-9+/=]{16,}|token: *[A-Za-z0-9._-]{24,})' "$TMPD"; then
  echo "capture: SELF-CHECK credential-pattern hit survived scrubbing" >&2; BAD=1
fi
if [ "$BAD" -ne 0 ]; then
  die "self-check failed — partial capture left at $TMPD for inspection, NOT installed"
fi

[ -e "$DEST" ] && rm -rf "$DEST"   # only reachable with --force
mv "$TMPD" "$DEST"
rm -rf "$ERRD"
echo "capture: OK $DEST ($(find "$DEST" -type f | wc -l | tr -d ' ') files,\
 $(du -sh "$DEST" | cut -f1))"
echo "capture: log channels:"
sed -n '/^log_channels:/,$p' "$DEST/scenario.yaml" | sed 's/^/  /'
echo "capture: next — run scripts/checkpoints.sh (fixture scan + completeness gate)"
