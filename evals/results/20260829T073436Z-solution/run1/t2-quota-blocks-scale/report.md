## Root cause

The namespace checkout carries a ResourceQuota named checkout-quota whose .spec.hard.pods is set to "2". The deployment/checkout-api was scaled to 6 replicas to absorb the traffic spike, but the quota admission controller rejects every pod creation beyond the second, so ReplicaSet checkout-api-5f66bf789c is stuck at 2 running pods out of 6 desired and the Deployment reports ReplicaFailure/FailedCreate with Available=False. Only two pod replicas are serving the spike traffic, which is why checkout p99 latency did not come down after the scale-up. The fix is to raise checkout-quota's .spec.hard.pods to at least the desired replica count (6); the two existing pods are healthy and the node has ample capacity (pods allocatable 110), so nothing else needs to change.

Remediation: edit ResourceQuota checkout/checkout-quota, field `spec.hard.pods`: `2` -> `6 (or higher, at least the desired replica count of deployment/checkout-api)`.

## Evidence chain

1. [symptom] The paged deployment is stuck at 2 of 6 replicas even though both running pods are ready.
   source: namespace_overview(checkout) — verified
   > deployment/checkout-api ready=2/6 podLabels={app=checkout-api}
2. [symptom] The Deployment reports the replica creation failure condition.
   source: describe({"kind": "deployment", "name": "checkout-api", "namespace": "checkout"}) — verified
   > ReplicaFailure   True    FailedCreate
3. [link] The ReplicaSet's pod creations are rejected by the namespace quota.
   source: describe({"kind": "replicaset", "name": "checkout-api-5f66bf789c", "namespace": "checkout"}) — verified
   > Error creating: pods "checkout-api-5f66bf789c-wj8l8" is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2
4. [defect] The ResourceQuota checkout-quota caps the namespace at 2 pods.
   source: get_object({"kind": "resourcequotas", "namespace": "checkout"}) — verified
   > "hard": {
   >         "pods": "2"
   >       }
5. [defect] The quota is fully consumed at its hard limit.
   source: describe({"kind": "resourcequota", "name": "checkout-quota", "namespace": "checkout"}) — verified
   > pods        2     2

## Investigation ledger

- Node capacity exhaustion / unschedulable pods prevented the scale-up — ruled out: The single node is Ready with a pod capacity of 110, far above the 6 requested replicas, and no scheduling failure events exist — the pods were never created at all.
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
- The running checkout-api pods are themselves unhealthy or crash-looping and serving slow responses — ruled out: Both existing pods are Running and Ready with zero restarts; capacity, not pod health, is the shortfall.
  source: namespace_overview(checkout) — verified
  > pod/checkout-api-5f66bf789c-gqzzb phase=Running labels={app=checkout-api, pod-template-hash=5f66bf789c} node=incident-lab-control-plane api(ready=True,restarts=0)
- A broken reference in the pod template (missing ConfigMap checkout-api-scripts) blocked the new pods — ruled out: The rejection message is a quota admission denial at pod creation, not a volume/ConfigMap mount failure, and the same template runs fine in the two admitted pods.
  source: describe({"kind": "replicaset", "name": "checkout-api-5f66bf789c", "namespace": "checkout"}) — verified
  > Warning  FailedCreate      1s               replicaset-controller  Error creating: pods "checkout-api-5f66bf789c-7htmh" is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2

## Verification recipe

1. `kubectl describe resourcequota checkout-quota -n checkout` — expect to see: pods        2     2  [PRESENT]
2. `kubectl describe rs checkout-api-5f66bf789c -n checkout` — expect to see: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2  [PRESENT]
3. `kubectl get deployment checkout-api -n checkout -o yaml` — expect to see: ReplicaFailure   True    FailedCreate  [PRESENT]
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
  "mechanism": "The ResourceQuota checkout-quota in namespace checkout has .spec.hard.pods = \"2\" while 2 pods are already used, so the quota admission controller forbids every further pod creation in the namespace with \"exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2\". The hard pod limit should be at least 6, the desired replica count, so the scale-up is admitted instead of being rejected on every retry.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
