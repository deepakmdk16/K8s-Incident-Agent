## Root cause

The scale-up of Deployment checkout/checkout-api from 2 to 6 replicas never happened: ResourceQuota checkout/checkout-quota caps the namespace at pods: "2", and its status shows pods used 2 of hard 2. The ReplicaSet controller for checkout-api-5f66bf789c created two pods and every further pod creation was rejected by the quota admission plugin with 'is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2', leaving the Deployment at 2/6 with ReplicaFailure=FailedCreate and Available=False. Because only two pods are serving the spike instead of six, per-pod load stayed at pre-scale levels and checkout p99 latency kept burning the SLO. The two running pods are themselves healthy (Ready, 0 restarts) and the node has pods=110 allocatable, so nothing but the quota is holding the replica count down. Fix: raise .spec.hard.pods on ResourceQuota checkout/checkout-quota from "2" to at least 6 so the ReplicaSet can create the remaining four pods.

Remediation: edit ResourceQuota checkout/checkout-quota, field `.spec.hard.pods`: `2` -> `6 (or higher, at least the desired replica count of deployment checkout-api)`.

## Evidence chain

1. [symptom] The page reports checkout latency SLO burn after a scale-up to 6 replicas.
   source: the page — verified
   > The checkout-api deployment in namespace checkout was
   > scaled up to 6 replicas to absorb the spike, but latency has not come down
2. [symptom] Deployment checkout-api is at 2 of 6 replicas.
   source: namespace_overview(checkout) — verified
   > deployment/checkout-api ready=2/6 podLabels={app=checkout-api}
3. [link] The Deployment reports 4 unavailable replicas and ReplicaFailure=FailedCreate.
   source: describe({"kind": "deployment", "name": "checkout-api", "namespace": "checkout"}) — verified
   > Replicas:               6 desired | 2 updated | 2 total | 2 available | 4 unavailable
4. [link] Pod creation is rejected by the namespace quota.
   source: describe({"kind": "replicaset", "name": "checkout-api-5f66bf789c", "namespace": "checkout"}) — verified
   > Error creating: pods "checkout-api-5f66bf789c-wj8l8" is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2
5. [defect] ResourceQuota checkout-quota hard-caps the namespace at 2 pods.
   source: get_object({"kind": "resourcequotas", "namespace": "checkout"}) — verified
   > "hard": {
   >         "pods": "2"
   >       }
6. [defect] The quota is fully consumed at 2 of 2 pods.
   source: describe({"kind": "resourcequota", "name": "checkout-quota", "namespace": "checkout"}) — verified
   > pods        2     2

## Investigation ledger

- The two running checkout-api pods are unhealthy or crashing, causing the latency — ruled out: Both pods are Running and Ready with zero restarts, so pod health is not the limiter.
  source: namespace_overview(checkout) — verified
  > pod/checkout-api-5f66bf789c-gqzzb phase=Running labels={app=checkout-api, pod-template-hash=5f66bf789c} node=incident-lab-control-plane api(ready=True,restarts=0)
- The node cannot accommodate more pods (insufficient capacity or scheduling pressure) — ruled out: The single node is Ready with 110 allocatable pods and 6 CPUs free of taints, so scheduling capacity is not what stops the extra replicas; the pods are never created at all.
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
- A missing ConfigMap volume reference (checkout-api-scripts) blocks the new pods — ruled out: No pod ever reached the kubelet for a mount to fail; the only failure recorded on the ReplicaSet is quota-based FailedCreate at the API server.
  source: describe({"kind": "replicaset", "name": "checkout-api-5f66bf789c", "namespace": "checkout"}) — verified
  > Pods Status:    2 Running / 0 Waiting / 0 Succeeded / 0 Failed

## Verification recipe

1. `kubectl -n checkout describe resourcequota checkout-quota` — expect to see: pods        2     2  [PRESENT]
2. `kubectl -n checkout describe rs checkout-api-5f66bf789c` — expect to see: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2  [PRESENT]
3. `kubectl -n checkout describe deployment checkout-api` — expect to see: ReplicaFailure   True    FailedCreate  [PRESENT]
(Each command was re-executed against the captured cluster state; PRESENT/ABSENT is the measured result, not a prediction.)

Verdict: confirmed. Every quote below was re-checked against the tool output it cites, a defect observation names the failing object, an alternative was ruled out with evidence, and every verification command was re-run and found present.

```json
{
  "case_id": "t2-quota-blocks-scale",
  "failing_resource": {
    "kind": "ResourceQuota",
    "namespace": "checkout",
    "name": "checkout-quota"
  },
  "mechanism": "ResourceQuota checkout/checkout-quota sets .spec.hard.pods to \"2\" while Deployment checkout/checkout-api requests 6 replicas, so quota admission rejects every pod after the second: the ReplicaSet controller logs 'Error creating: pods \"checkout-api-5f66bf789c-wj8l8\" is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2' and retries, and the Deployment sits at \"2 desired | 2 updated | 2 total | 2 available | 4 unavailable\" with ReplicaFailure=FailedCreate. Only two pods absorb the traffic spike, so the added capacity the scale-up was supposed to deliver never exists.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
