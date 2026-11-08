# t1-reefer-telemetry-offline

**Tier:** T1. **Status:** authored 2026-11-07; captured — see "Capture" below.
**Set:** `evals/scenarios-heldout/` (held-out root; scored by
`evals/scoring_v2.py`). Authoring cluster `kind-incident-lab`, node image
pinned to the frozen set's digest (server v1.37.0).

**Namespaces owned:** `cold-chain`. No cluster-scoped objects.

## Informal ground truth

The cold-chain team redeployed its reefer temperature monitor this morning
with a CPU request typed as `512` instead of `512m`. Five hundred and twelve
cores fit no node, so both pods stay Pending with `FailedScheduling:
Insufficient cpu`, nothing polls the trailers, and the telemetry-freshness
alert fires. The frozen set's atom for this is `t1-unschedulable-cpu-requests`
(fraud scoring); this case is the same atom in a company, namespace,
workload, container, ConfigMap and page the arms have never seen — the T1
transfer control of the held-out set.

## Wait condition

`wait.sh` gates on both reefer-monitor pods `Pending` with
`PodScheduled=False` reason `Unschedulable` **and** a `FailedScheduling`
event whose message contains `Insufficient cpu`. Pending alone is not
decisive (an image pull or a volume wait looks the same from the phase).

## Determinism

Single-node kind. `cpu: "512"` is unsatisfiable on any host, so the outcome
does not depend on the machine's core count (authoring contract rule 3). The
command is a plain echo-and-sleep on the node-cached `busybox:1.36`, so a
corrected request runs it as written (rule 1).

## Gold-side asymmetry

Only one object can defensibly change: the Deployment's pod template holds
the request. The ConfigMap it reads is healthy and referenced correctly, and
no Service or quota is involved (no ResourceQuota exists in the namespace, so
the quota class cannot be argued).

## Counterfactual verification (authoring contract rule 6)

Rehearsed live on 2026-11-08 (04:27–04:32 UTC) **before** the pristine
capture, on a separate `inject.sh --no-capture` injection:

| step | observed |
|---|---|
| fault live (wait.sh, within seconds of apply) | both pods `Pending`, `PodScheduled=False` reason `Unschedulable`; `FailedScheduling` message exactly `0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 Preemption is not helpful for scheduling.`; deployment `0/2` |
| applied `gold.json`'s `remediation_summary` verbatim | `kubectl set resources deployment/reefer-monitor -n cold-chain --requests=cpu=512m` → `resource requirements updated` |
| recovery | rollout completed within seconds (04:31:34 → 04:31:35 UTC): both new pods `1/1 Running` on the node, log `reefer-monitor: polling reefer units every 30s`, template requests now `{"cpu":"512m","memory":"64Mi"}` |

No other object was touched, which is what `remediation_summary` claims. The
cluster was then reset by `inject.sh` (namespace wipe) and re-injected clean
for the capture below.

A first injection attempt the same day failed at `kubectl apply` with
`cannot unmarshal object into Go struct field Container...command of type
string`: the inline command carried a `: ` inside a plain YAML scalar, which
YAML read as a mapping. Fixed by writing the command as a block scalar
before any rehearsal; no cluster state was involved.

## Capture

`evals/inject.sh --id t1-reefer-telemetry-offline --root evals/scenarios-heldout`,
2026-11-08T04:32:32Z, server v1.37.0, **capture schema 3** (the first fixture captured
by the schema-3 script: `cluster/customresourcedefinitions.json` is present
and lists no CRDs, so no `crs/` directory exists). 276 files, 1.6M. The
fault manifested on the first clean injection; the decisive text is in
`cluster/events.json` and in both pods' describes.
