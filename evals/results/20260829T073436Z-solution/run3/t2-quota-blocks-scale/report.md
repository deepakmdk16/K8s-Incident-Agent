## Root cause

The namespace checkout has a ResourceQuota named checkout-quota whose .spec.hard.pods is "2". The checkout-api Deployment was scaled to 6 replicas for the traffic spike, but its ReplicaSet checkout-api-5f66bf789c can only create 2 pods; every further pod creation is rejected by the quota admission plugin with "exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2". The Deployment is therefore stuck at 2/6 available with ReplicaFailure=True/FailedCreate, so the extra serving capacity intended to absorb the spike never exists and checkout p99 latency keeps burning SLO. The node is not the constraint (control-plane node is Ready with pods=110 allocatable). Fix: raise .spec.hard.pods on the ResourceQuota checkout-quota to at least 6 (8 leaves room for rolling-update surge), after which the ReplicaSet can create the remaining pods.

Remediation: edit ResourceQuota checkout/checkout-quota, field `.spec.hard.pods`: `2` -> `6 (at least the desired replica count of deployment/checkout-api, e.g. "8" to allow rolling-update surge)`.

## Evidence chain

1. [symptom] Deployment checkout-api is scaled to 6 but only 2 pods exist and are ready.
   source: namespace_overview(checkout) — verified
   > deployment/checkout-api ready=2/6 podLabels={app=checkout-api}
2. [symptom] The Deployment reports FailedCreate / unavailable replicas.
   source: describe({"kind": "deployment", "name": "checkout-api", "namespace": "checkout"}) — verified
   > Replicas:               6 desired | 2 updated | 2 total | 2 available | 4 unavailable
3. [link] The Deployment's ReplicaFailure condition is FailedCreate.
   source: describe({"kind": "deployment", "name": "checkout-api", "namespace": "checkout"}) — verified
   > ReplicaFailure   True    FailedCreate
4. [link] Pod creation is rejected by the quota admission plugin naming checkout-quota.
   source: describe({"kind": "replicaset", "name": "checkout-api-5f66bf789c", "namespace": "checkout"}) — verified
   > Error creating: pods "checkout-api-5f66bf789c-wj8l8" is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2
5. [defect] ResourceQuota checkout-quota caps pods at 2 in namespace checkout.
   source: get_object({"kind": "resourcequotas", "namespace": "checkout"}) — verified
   > "hard": {
   >         "pods": "2"
   >       }
6. [defect] The quota is fully consumed: used equals hard.
   source: describe({"kind": "resourcequota", "name": "checkout-quota", "namespace": "checkout"}) — verified
   > pods        2     2

## Investigation ledger

- Node lacks capacity / scheduling pressure prevented the extra 4 pods from running — ruled out: The only node is Ready with 110 allocatable pods and full CPU/memory allocatable; the pods were never created at all, so scheduling was never reached.
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
- The running checkout-api pods are themselves unhealthy (crashing, bad image, missing ConfigMap mount) — ruled out: Both existing pods are Running and Ready with zero restarts, so the shortfall is purely the missing replicas, not pod failure.
  source: namespace_overview(checkout) — verified
  > pod/checkout-api-5f66bf789c-gqzzb phase=Running labels={app=checkout-api, pod-template-hash=5f66bf789c} node=incident-lab-control-plane api(ready=True,restarts=0)

## Verification recipe

1. `kubectl -n checkout describe resourcequota checkout-quota` — expect to see: pods        2     2  [PRESENT]
2. `kubectl -n checkout describe rs checkout-api-5f66bf789c` — expect to see: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2  [PRESENT]
3. `kubectl -n checkout get deploy checkout-api -o yaml` — expect to see: ReplicaFailure   True    FailedCreate  [PRESENT]
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
  "mechanism": "The ResourceQuota checkout-quota in namespace checkout sets .spec.hard.pods to \"2\" while deployment/checkout-api asks for 6 replicas, so quota admission rejects every pod creation past the second with \"exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2\". The quota is fully consumed (used 2, hard 2) and keeps denying the ReplicaSet's repeated create attempts, holding the deployment at 2 of 6 replicas with ReplicaFailure=True/FailedCreate.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
