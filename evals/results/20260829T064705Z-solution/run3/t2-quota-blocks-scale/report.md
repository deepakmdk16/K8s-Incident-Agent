## Root cause

The checkout namespace carries a ResourceQuota, checkout-quota, whose .spec.hard["pods"] is set to 2. The checkout-api deployment was scaled to 6 replicas, but its ReplicaSet can only create 2 pods; admission rejects every further pod creation with "exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2". The deployment therefore stays at 2/6 ready with ReplicaFailure=True/FailedCreate, so the scale-up added no serving capacity and the two surviving pods continue to absorb the whole traffic spike, which is what keeps checkout p99 latency burning the SLO.

Remediation: edit ResourceQuota checkout/checkout-quota, field `.spec.hard[pods]`: `2` -> `at least 6 (e.g. "6") so the 6 replicas of deployment/checkout-api can be admitted`.

## Evidence chain

1. [symptom] The deployment named in the page is stuck at 2 of 6 replicas.
   source: namespace_overview(checkout) — verified
   > deployment/checkout-api ready=2/6 podLabels={app=checkout-api}
2. [symptom] The deployment reports a replica creation failure, not a scheduling or readiness failure.
   source: describe({"kind": "deployment", "name": "checkout-api", "namespace": "checkout"}) — verified
   > Replicas:               6 desired | 2 updated | 2 total | 2 available | 4 unavailable
3. [link] The deployment's failure condition is FailedCreate.
   source: describe({"kind": "deployment", "name": "checkout-api", "namespace": "checkout"}) — verified
   > ReplicaFailure   True    FailedCreate
4. [link] Pod creation is being rejected by the namespace quota.
   source: get_events({"namespace": "checkout"}) — verified
   > Error creating: pods "checkout-api-5f66bf789c-wj8l8" is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2
5. [defect] The quota object caps the namespace at 2 pods.
   source: get_object({"kind": "resourcequotas", "namespace": "checkout"}) — verified
   > "hard": {
   >         "pods": "2"
   >       }
6. [defect] The quota is fully consumed at its hard limit of 2 pods.
   source: describe({"kind": "resourcequota", "name": "checkout-quota", "namespace": "checkout"}) — verified
   > pods        2     2

## Investigation ledger

- The existing checkout-api pods are unhealthy or crash-looping and that is what drives latency — ruled out: Both running pods are Ready with zero restarts, so the shortfall is missing replicas, not sick ones.
  source: namespace_overview(checkout) — verified
  > pod/checkout-api-5f66bf789c-gqzzb phase=Running labels={app=checkout-api, pod-template-hash=5f66bf789c} node=incident-lab-control-plane api(ready=True,restarts=0)
- The node cannot fit more pods (insufficient cluster capacity / scheduling pressure) — ruled out: The only node is Ready with a pod capacity of 110 and full CPU/memory allocatable, and the missing pods were never created at all (no Pending pods, no FailedScheduling events) — they were rejected at admission.
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
- A broken reference in the pod template (missing ConfigMap checkout-api-scripts) blocks the new pods — ruled out: The two pods created from the same template mount that ConfigMap and are Running and Ready, and the only warnings in the namespace are quota FailedCreate messages, none about volumes or configmaps.
  source: get_events({"namespace": "checkout"}) — verified
  > checkout Warning FailedCreate replicaset/checkout-api-5f66bf789c x1 Error creating: pods "checkout-api-5f66bf789c-f7kvq" is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2

## Verification recipe

1. `kubectl -n checkout describe resourcequota checkout-quota` — expect to see: pods        2     2  [PRESENT]
2. `kubectl -n checkout get events --field-selector reason=FailedCreate` — expect to see: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2  [PRESENT]
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
  "mechanism": "ResourceQuota checkout-quota in namespace checkout sets .spec.hard[\"pods\"] to \"2\" while deployment/checkout-api requests 6 replicas; the quota admission plugin rejects each additional pod creation by ReplicaSet checkout-api-5f66bf789c with \"forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2\", so only 2 of the 6 pods exist and the deployment reports ReplicaFailure/FailedCreate.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
