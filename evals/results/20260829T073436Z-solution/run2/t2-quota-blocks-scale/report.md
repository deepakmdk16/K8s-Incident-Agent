## Root cause

The scale-up of deployment/checkout-api in namespace checkout from 2 to 6 replicas was never admitted. The namespace has a ResourceQuota named checkout-quota whose .spec.hard.pods is "2", and both existing checkout-api pods already consume that entire allowance (used 2 / hard 2). Every additional pod the ReplicaSet checkout-api-5f66bf789c tries to create is rejected by the quota admission controller with "exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2", so the deployment stays at 2/6 with the condition ReplicaFailure=True/FailedCreate. Serving capacity therefore never grew during the traffic spike and checkout p99 latency kept burning SLO. The fix is to raise .spec.hard.pods on ResourceQuota checkout-quota to at least 6 (the number of replicas requested); the node has ample room (pods allocatable 110, cpu 6, memory ~11.6Gi) and the two running pods are healthy.

Remediation: edit ResourceQuota checkout/checkout-quota, field `.spec.hard.pods`: `2` -> `6 (or higher, to cover the 6 replicas checkout-api requests)`.

## Evidence chain

1. [symptom] The paged deployment is stuck at 2 of 6 replicas even though both running pods are healthy.
   source: namespace_overview(checkout) — verified
   > deployment/checkout-api ready=2/6 podLabels={app=checkout-api}
2. [defect] ResourceQuota checkout-quota sets a hard pod limit of 2 for the namespace, and that allowance is already fully used.
   source: describe({"kind": "resourcequotas", "name": "checkout-quota", "namespace": "checkout"}) — verified
   > pods        2     2
3. [defect] The hard limit in the ResourceQuota checkout-quota spec is pods: 2.
   source: get_object({"kind": "resourcequotas", "namespace": "checkout"}) — verified
   > "name": "checkout-quota"
4. [link] Every extra pod the ReplicaSet tries to create is rejected by quota admission naming checkout-quota.
   source: get_events({"namespace": "checkout"}) — verified
   > Error creating: pods "checkout-api-5f66bf789c-wj8l8" is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2
5. [link] The deployment records the create failure as a ReplicaFailure condition and only 2 of 6 replicas were created.
   source: describe({"kind": "deployments", "name": "checkout-api", "namespace": "checkout"}) — verified
   > ReplicaFailure   True    FailedCreate
6. [symptom] The deployment spec does request 6 replicas.
   source: get_object({"kind": "deployments", "name": "checkout-api", "namespace": "checkout"}) — verified
   > "replicas": 6,

## Investigation ledger

- The cluster node lacks capacity to schedule the 4 extra pods — ruled out: The single node is Ready with 110 pod slots and full cpu/memory allocatable; the pods were never created at all, so scheduling was never reached.
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
- The running checkout-api pods are themselves unhealthy or crash-looping, causing the latency — ruled out: Both pods are Running and Ready with zero restarts, so the shortfall is missing replicas rather than failing ones.
  source: namespace_overview({"namespace": "checkout"}) — verified
  > pod/checkout-api-5f66bf789c-gqzzb phase=Running labels={app=checkout-api, pod-template-hash=5f66bf789c} node=incident-lab-control-plane api(ready=True,restarts=0)
- A broken reference in the pod template (image, ConfigMap volume) prevents the new pods from starting — ruled out: The only warnings in the namespace are quota FailedCreate events; there are no image-pull, mount or config-key errors, and pod objects were never created.
  source: get_events({"namespace": "checkout"}) — verified
  > checkout Warning FailedCreate replicaset/checkout-api-5f66bf789c x1 Error creating: pods "checkout-api-5f66bf789c-f7kvq" is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2

## Verification recipe

1. `kubectl describe resourcequota checkout-quota -n checkout` — expect to see: pods        2     2  [PRESENT]
2. `kubectl get events -n checkout --field-selector type=Warning` — expect to see: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2  [PRESENT]
3. `kubectl describe deployment checkout-api -n checkout` — expect to see: ReplicaFailure   True    FailedCreate  [PRESENT]
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
  "mechanism": "The ResourceQuota checkout-quota in namespace checkout caps .spec.hard.pods at \"2\" while the namespace needs at least 6 to hold the requested replica count, and its status shows pods used 2 of hard 2. With the allowance exhausted, quota admission rejects each new pod creation in the namespace with \"exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2\", and the rejections repeat on every create attempt so the namespace stays pinned at two pods.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
