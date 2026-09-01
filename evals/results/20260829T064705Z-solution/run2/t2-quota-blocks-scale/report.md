## Root cause

The checkout namespace has a ResourceQuota named checkout-quota whose .spec.hard.pods is "2". checkout-api was scaled to 6 replicas, but the namespace quota admits only 2 pods and usage is already at 2/2. Every attempt by ReplicaSet checkout-api-5f66bf789c to create the 3rd through 6th pod is rejected at admission with "exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2", so the deployment stays at 2/6 with ReplicaFailure=True and the extra capacity intended to absorb the traffic spike never exists. The two pods that do exist are healthy and serving, so checkout traffic stays concentrated on them, which is why p99 latency has not come down. Fix: raise .spec.hard.pods on checkout-quota to at least 6.

Remediation: edit ResourceQuota checkout/checkout-quota, field `.spec.hard.pods`: `2` -> `6 (or higher, e.g. 8 to leave headroom for rolling-update surge)`.

## Evidence chain

1. [symptom] The deployment scaled to 6 is stuck at 2 ready pods.
   source: namespace_overview(checkout) — verified
   > deployment/checkout-api ready=2/6 podLabels={app=checkout-api}
2. [symptom] The deployment reports a replica creation failure rather than a scheduling or readiness failure.
   source: describe({"kind": "deployment", "name": "checkout-api", "namespace": "checkout"}) — verified
   > ReplicaFailure   True    FailedCreate
3. [defect] ResourceQuota checkout-quota hard-limits the namespace to 2 pods and is already fully consumed.
   source: describe({"kind": "resourcequota", "name": "checkout-quota", "namespace": "checkout"}) — verified
   > pods        2     2
4. [defect] The quota object's hard pods field is set to 2.
   source: get_object({"kind": "resourcequotas", "namespace": "checkout"}) — verified
   > "pods": "2"
5. [link] Every additional pod create for the checkout-api ReplicaSet is rejected by that quota.
   source: get_events({"namespace": "checkout"}) — verified
   > is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2

## Investigation ledger

- Node capacity or scheduling pressure prevented the extra replicas from being placed — ruled out: The only node is Ready with 110 pod capacity and full allocatable CPU/memory; the pods were never created at all, so scheduling was never reached.
  source: cluster_capacity({}) — verified
  > condition Ready=True
- The checkout-api container itself is broken (bad image, bad configmap mount, failing probes) so replicas never become ready — ruled out: The existing pods start and serve normally, and both are Ready with zero restarts.
  source: get_logs({"namespace": "checkout", "pod": "checkout-api-5f66bf789c-gqzzb", "tail": 20}) — verified
  > checkout-api: serving on :8080

## Verification recipe

1. `kubectl describe resourcequota checkout-quota -n checkout` — expect to see: pods        2     2  [PRESENT]
2. `kubectl get events -n checkout --field-selector reason=FailedCreate` — expect to see: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2  [PRESENT]
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
  "mechanism": "ResourceQuota checkout/checkout-quota sets .spec.hard.pods to \"2\" while deployment checkout-api requests 6 replicas; with usage already at 2/2, the API server rejects each pod create from ReplicaSet checkout-api-5f66bf789c with \"exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2\", so only 2 of the 6 pods are ever admitted and the deployment reports ReplicaFailure=True.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
