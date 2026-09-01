## Root cause

The checkout-api deployment was scaled to 6 replicas, but namespace checkout carries a ResourceQuota named checkout-quota whose .spec.hard["pods"] is 2, and 2 pods are already in use. The quota admission controller rejects every additional pod creation from ReplicaSet checkout-api-5f66bf789c with "exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2", so the deployment stays at 2/6 with ReplicaFailure=FailedCreate. The two surviving replicas are healthy and serving, so the extra capacity intended to absorb the traffic spike never exists and checkout p99 latency keeps burning SLO. The fix is to raise the pod quota in checkout-quota to at least the desired replica count.

Remediation: edit ResourceQuota checkout/checkout-quota, field `.spec.hard[pods]`: `2` -> `6 (at least; enough headroom for the 6 desired checkout-api replicas plus rollout surge)`.

## Evidence chain

1. [symptom] The paged deployment is stuck at 2 of 6 replicas.
   source: describe({"kind": "deployment", "name": "checkout-api", "namespace": "checkout"}) — verified
   > Replicas:               6 desired | 2 updated | 2 total | 2 available | 4 unavailable
2. [symptom] The deployment reports FailedCreate rather than a scheduling or image problem.
   source: describe({"kind": "deployment", "name": "checkout-api", "namespace": "checkout"}) — verified
   > ReplicaFailure   True    FailedCreate
3. [defect] ResourceQuota checkout-quota caps the namespace at 2 pods, and that ceiling is fully consumed.
   source: describe({"kind": "resourcequota", "name": "checkout-quota", "namespace": "checkout"}) — verified
   > pods        2     2
4. [defect] The ResourceQuota object named checkout-quota declares hard pods = 2 in its spec.
   source: get_object({"kind": "resourcequotas", "namespace": "checkout"}) — verified
   > "pods": "2"
5. [link] Pod creations for the checkout-api ReplicaSet are rejected by that quota.
   source: get_events({"namespace": "checkout"}) — verified
   > Error creating: pods "checkout-api-5f66bf789c-wj8l8" is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2

## Investigation ledger

- The running checkout-api pods are themselves broken or crash-looping, causing latency — ruled out: Both existing replicas are Running and Ready with zero restarts, so the deficit is missing capacity, not unhealthy pods.
  source: namespace_overview(checkout) — verified
  > pod/checkout-api-5f66bf789c-gqzzb phase=Running labels={app=checkout-api, pod-template-hash=5f66bf789c} node=incident-lab-control-plane api(ready=True,restarts=0)
- The application is failing at startup or erroring at runtime — ruled out: The container log shows the server started and is serving normally with no errors.
  source: get_logs({"namespace": "checkout", "pod": "checkout-api-5f66bf789c-gqzzb", "tail": 20}) — verified
  > checkout-api: serving on :8080
- The node lacks CPU/memory or pod slots for the extra 4 replicas (Unschedulable) — ruled out: The only node is Ready with 6 CPU, ~12Gi memory and 110 pod capacity, and no FailedScheduling events exist - the pods were rejected at admission and never created.
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)

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
  "mechanism": "ResourceQuota checkout-quota in namespace checkout sets .spec.hard[\"pods\"] to \"2\" while the checkout-api deployment asks for 6 replicas; with used pods already at 2, the quota admission controller rejects each further pod creation by ReplicaSet checkout-api-5f66bf789c with \"exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2\", so only 2 of the 6 replicas are ever created.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
