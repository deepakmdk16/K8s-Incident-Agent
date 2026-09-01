## Root cause

The scale-up of Deployment checkout/checkout-api from 2 to 6 replicas never happened. ResourceQuota checkout/checkout-quota caps the namespace at pods=2, and its status shows used 2 of hard 2. Every additional pod creation attempted by ReplicaSet checkout/checkout-api-5f66bf789c is rejected at admission with "is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2", so the Deployment stays at 2/6 with ReplicaFailure=True/FailedCreate and Available=False/MinimumReplicasUnavailable. The two surviving pods are Running and Ready with 0 restarts, so the traffic spike keeps being served by the original two replicas, which is why checkout p99 latency did not come down after the scale-up. The object a human must edit is the ResourceQuota, not the Deployment or its pods.

Remediation: edit ResourceQuota checkout/checkout-quota, field `spec.hard[pods]`: `2` -> `6 (or higher, at least the desired replica count of deployment/checkout-api)`.

## Evidence chain

1. [symptom] The paged deployment was scaled to 6 but only 2 replicas exist and are ready.
   source: namespace_overview(checkout) — verified
   > deployment/checkout-api ready=2/6 podLabels={app=checkout-api}
2. [symptom] The Deployment reports four unavailable replicas and a create failure condition.
   source: describe({"kind": "deployment", "name": "checkout-api", "namespace": "checkout"}) — verified
   > Replicas:               6 desired | 2 updated | 2 total | 2 available | 4 unavailable
3. [link] The Deployment's failure condition is FailedCreate, not a pod runtime failure.
   source: describe({"kind": "deployment", "name": "checkout-api", "namespace": "checkout"}) — verified
   > ReplicaFailure   True    FailedCreate
4. [link] The ReplicaSet's pod creations are rejected by the namespace quota.
   source: describe({"kind": "replicaset", "name": "checkout-api-5f66bf789c", "namespace": "checkout"}) — verified
   > Error creating: pods "checkout-api-5f66bf789c-wj8l8" is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2
5. [defect] The ResourceQuota hard-caps the namespace at 2 pods.
   source: get_object({"kind": "resourcequotas", "namespace": "checkout"}) — verified
   > "hard": {
   >         "pods": "2"
   >       }
6. [defect] The quota is fully consumed at 2 of 2 pods.
   source: describe({"kind": "resourcequota", "name": "checkout-quota", "namespace": "checkout"}) — verified
   > pods        2     2

## Investigation ledger

- The existing checkout-api pods are themselves unhealthy or crash-looping, causing latency. — ruled out: Both running pods are Ready with zero restarts, so the latency is capacity starvation rather than pod failure.
  source: namespace_overview(checkout) — verified
  > pod/checkout-api-5f66bf789c-gqzzb phase=Running labels={app=checkout-api, pod-template-hash=5f66bf789c} node=incident-lab-control-plane api(ready=True,restarts=0)
- The node lacks capacity or is unschedulable, so extra replicas cannot be placed. — ruled out: The only node is Ready with a pod capacity of 110, far above the 6 requested replicas, and the pods were never created at all (rejected at admission, not left Pending).
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
  >   condition Ready=True
- A broken reference in the pod template (the checkout-api-scripts ConfigMap volume) blocks new pods. — ruled out: The template is identical for the two pods that are Running and Ready, and the ReplicaSet's failures are quota admission rejections, not volume or mount errors.
  source: describe({"kind": "replicaset", "name": "checkout-api-5f66bf789c", "namespace": "checkout"}) — verified
  > Pods Status:    2 Running / 0 Waiting / 0 Succeeded / 0 Failed

## Verification recipe

1. `kubectl describe resourcequota checkout-quota -n checkout` — expect to see: pods        2     2  [PRESENT]
2. `kubectl describe rs checkout-api-5f66bf789c -n checkout` — expect to see: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2  [PRESENT]
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
  "mechanism": "ResourceQuota checkout/checkout-quota sets .spec.hard[pods] to \"2\" where it needs to allow at least the 6 replicas requested of Deployment checkout/checkout-api; its status already shows used 2 / hard 2. Consequently the admission of each further pod created by ReplicaSet checkout/checkout-api-5f66bf789c is rejected with \"Error creating: pods \\\"checkout-api-5f66bf789c-wj8l8\\\" is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2\", and the replicaset-controller keeps retrying and re-failing, leaving Deployment checkout/checkout-api at \"2 updated | 2 total | 2 available | 4 unavailable\" with ReplicaFailure=True (FailedCreate).",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
