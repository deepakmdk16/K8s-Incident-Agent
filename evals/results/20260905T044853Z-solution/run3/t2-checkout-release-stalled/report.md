## Root cause

The checkout-api rollout is not stalled by anything in the deployment's own spec. Every pod CREATE in this cluster is sent to the admission webhook "validate.policy-guard.platform.internal", carried by ValidatingWebhookConfiguration workload-standards, whose clientConfig points at Service policy-guard in namespace platform-policy. That backend does not exist - namespace platform-policy is not present in the cluster - so the API server call fails with service "policy-guard" not found and the create is denied with an Internal error rather than being allowed through. The new ReplicaSet checkout-api-64fb54b496 therefore holds ReplicaFailure/FailedCreate at 0 pods, while the old ReplicaSet checkout-api-8587489575 had already been scaled from 3 to 2 by the rolling update (1 max unavailable, 0 max surge), leaving Deployment checkout/checkout-api at 2 of 3 available with Progressing=False / ProgressDeadlineExceeded and elevated p95 latency. The version-number change the team made is incidental; any pod creation would be rejected the same way.

Remediation: edit ValidatingWebhookConfiguration cluster-scoped/workload-standards, field `webhooks[name=validate.policy-guard.platform.internal].failurePolicy (its clientConfig.service is platform-policy/policy-guard)`: `Fail, while clientConfig.service names Service policy-guard in namespace platform-policy, which does not exist, so every pod CREATE is rejected` -> `Ignore (or remove this webhook entry / restore Service policy-guard in namespace platform-policy) so pod CREATE admission succeeds again`.

## Evidence chain

1. [symptom] Deployment checkout/checkout-api is serving 2 of 3 replicas with no updated pods.
   source: describe({"kind": "deployment", "name": "checkout-api", "namespace": "checkout"}) — verified
   > Replicas:               3 desired | 0 updated | 2 total | 2 available | 1 unavailable
2. [symptom] The deployment reports a create failure and an exceeded progress deadline.
   source: describe({"kind": "deployment", "name": "checkout-api", "namespace": "checkout"}) — verified
   > ReplicaFailure   True    FailedCreate
3. [link] The new ReplicaSet for revision 2 has created no pods at all.
   source: describe({"kind": "replicaset", "name": "checkout-api-64fb54b496", "namespace": "checkout"}) — verified
   > Replicas:       0 current / 1 desired
4. [defect] Pod creation is rejected by the admission webhook validate.policy-guard.platform.internal because its backend service does not exist.
   source: describe({"kind": "replicaset", "name": "checkout-api-64fb54b496", "namespace": "checkout"}) — verified
   > Error creating: Internal error occurred: failed calling webhook "validate.policy-guard.platform.internal": failed to call webhook: Post "https://policy-guard.platform-policy.svc:443/validate?timeout=10s": service "policy-guard" not found
5. [link] The namespace hosting the webhook backend does not exist in this cluster; only six namespaces are present and platform-policy is not among them.
   source: list_namespaces({}) — verified
   > local-path-storage workloads=1 pods=1 notReady=0
6. [link] The rejection repeats on every retry, so no new pod ever reaches the scheduler.
   source: get_events({"namespace": "checkout", "warnings_only": false}) — verified
   > checkout Warning FailedCreate replicaset/checkout-api-64fb54b496 x15 Error creating: Internal error occurred: failed calling webhook

## Investigation ledger

- The new pod template is broken (bad image, bad command, failing readiness probe) so new pods crash or never become ready — ruled out: No pod of the new revision was ever created, so nothing could crash or fail a probe.
  source: describe({"kind": "replicaset", "name": "checkout-api-64fb54b496", "namespace": "checkout"}) — verified
  > Pods Status:    0 Running / 0 Waiting / 0 Succeeded / 0 Failed
- A ResourceQuota in the checkout namespace blocks creation of the third pod — ruled out: There is no ResourceQuota object in the namespace at all.
  source: get_object({"kind": "resourcequotas", "namespace": "checkout"}) — verified
  > 0 objects of kind resourcequotas in namespace checkout
- The mounted ConfigMap reference no longer resolves, blocking the new pods — ruled out: ConfigMap checkout-content exists in the namespace with the keys the template serves.
  source: find_consumers({"kind": "configmap", "name": "checkout-content", "namespace": "checkout"}) — verified
  > configmaps that exist in checkout: checkout-content, kube-root-ca.crt
- The version bump itself is what broke the release — ruled out: The only spec difference between the old and new ReplicaSet templates is the APP_VERSION value, and the rejection happens at admission before any container runs.
  source: get_object({"kind": "replicasets", "namespace": "checkout"}) — verified
  > "value": "2026.09.04"

## Verification recipe

1. `kubectl -n checkout describe rs checkout-api-64fb54b496` — expect to see: service "policy-guard" not found  [PRESENT]
2. `kubectl -n checkout describe deployment checkout-api` — expect to see: ReplicaFailure   True    FailedCreate  [PRESENT]
3. `kubectl get ns` — expect to see: kube-public workloads=0 pods=0 notReady=0  [PRESENT]
(Each command was re-executed against the captured cluster state; PRESENT/ABSENT is the measured result, not a prediction.)

Verdict: probable. Every quote below was re-checked against the tool output it cites, but the evidence is indirect: at least one condition for a confirmed verdict was not met.

```json
{
  "case_id": "t2-checkout-release-stalled",
  "failing_resource": {
    "kind": "ValidatingWebhookConfiguration",
    "namespace": "cluster-scoped",
    "name": "workload-standards"
  },
  "mechanism": "ValidatingWebhookConfiguration workload-standards intercepts pod CREATE through its webhook \"validate.policy-guard.platform.internal\", whose clientConfig.service names Service policy-guard in namespace platform-policy - a namespace that does not exist - so the API server call fails with `failed to call webhook: Post \"https://policy-guard.platform-policy.svc:443/validate?timeout=10s\": service \"policy-guard\" not found`. Because that webhook's failurePolicy is Fail rather than Ignore, the unreachable backend is turned into a rejection: each pod create attempt is denied with `Error creating: Internal error occurred`, repeated on every controller retry (x15 in two minutes), so no replacement pod is ever admitted.",
  "verdict": "probable",
  "missing_evidence": ""
}
```
