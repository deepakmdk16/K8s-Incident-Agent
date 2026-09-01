## Root cause

The scale-up of Deployment checkout/checkout-api to 6 replicas never took effect because the namespace ResourceQuota checkout/checkout-quota caps the namespace at 2 pods. Every pod creation attempt by ReplicaSet checkout/checkout-api-5f66bf789c beyond the second is rejected by the quota admission plugin with 'exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2', so the deployment stays at 2/6 with ReplicaFailure/FailedCreate. Only two checkout-api pods are serving the traffic spike, which is why p99 latency stayed above the SLO and shoppers see timeouts at the payment step. The two existing pods themselves are healthy (Running, ready, 0 restarts, logging 'checkout-api: serving on :8080'), so the fix is to raise the pod count in the quota, not to change the deployment or its pods.

Remediation: edit ResourceQuota checkout/checkout-quota, field `.spec.hard.pods`: `2` -> `6 (or higher, to admit the 6 replicas checkout-api needs)`.

## Evidence chain

1. [symptom] The paged deployment was scaled to 6 but only 2 pods exist and are ready.
   source: namespace_overview(checkout) — verified
   > deployment/checkout-api ready=2/6 podLabels={app=checkout-api}
2. [symptom] The deployment reports 4 unavailable replicas and a ReplicaFailure/FailedCreate condition.
   source: describe({"kind": "deployment", "name": "checkout-api", "namespace": "checkout"}) — verified
   > Replicas:               6 desired | 2 updated | 2 total | 2 available | 4 unavailable
3. [link] The deployment's ReplicaSet cannot create the extra pods because the namespace quota forbids them.
   source: get_events({"namespace": "checkout"}) — verified
   > Error creating: pods "checkout-api-5f66bf789c-wj8l8" is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2
4. [defect] ResourceQuota checkout/checkout-quota hard-caps the namespace at 2 pods.
   source: get_object({"kind": "resourcequotas", "namespace": "checkout"}) — verified
   > "hard": {
   >         "pods": "2"
   >       }
5. [defect] The quota is fully consumed at its hard limit.
   source: describe({"kind": "resourcequota", "name": "checkout-quota", "namespace": "checkout"}) — verified
   > pods        2     2

## Investigation ledger

- Node capacity exhaustion / unschedulable pods blocking the scale-up — ruled out: The single node is Ready with 110 pod slots and full cpu/memory allocatable, and no FailedScheduling event exists; the extra pods never reached the scheduler because they were rejected at admission.
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
  >   condition Ready=True
- The checkout-api container itself is broken (bad image, missing ConfigMap volume, crash loop) so replicas cannot come up — ruled out: The running pods start cleanly and serve traffic, with 0 restarts and a healthy startup log line.
  source: get_logs({"namespace": "checkout", "pod": "checkout-api-5f66bf789c-gqzzb", "tail": 20}) — verified
  > checkout-api: serving on :8080

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
  "mechanism": "ResourceQuota checkout/checkout-quota sets .spec.hard.pods to \"2\" while Deployment checkout/checkout-api requests 6 replicas, so the quota admission plugin rejects every additional pod created by ReplicaSet checkout/checkout-api-5f66bf789c with `pods \"checkout-api-5f66bf789c-wj8l8\" is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2`. The deployment is therefore held at `2 desired | ... 2 available | 4 unavailable` with condition `ReplicaFailure True FailedCreate`, and the added capacity for the traffic spike is never created.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
