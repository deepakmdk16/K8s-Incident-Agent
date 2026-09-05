## Root cause

**Confirmed.** A cluster-scoped admission webhook — `validate.policy-guard.platform.internal`, served by `ValidatingWebhookConfiguration` "policy-guard" — is configured with `failurePolicy: Fail` and points at backing service `policy-guard.platform-policy.svc:443`, which does not exist. Every `CREATE pod` request in `checkout` is therefore rejected by the API server with an internal error before a pod object is ever admitted.

The consequence: the new ReplicaSet `checkout-api-64fb54b496` (revision 2) can create **zero** pods, so the rollout never progresses past `ProgressDeadlineExceeded`. Because the deployment's strategy is `maxUnavailable: 1, maxSurge: 0`, the deployment controller first scaled the old ReplicaSet `checkout-api-8587489575` down from 3 → 2 to make room for the (never-created) new pod. That surrendered replica is the missing capacity behind the elevated p95 — the deployment is stuck at 2/3 available and cannot recover on its own.

Nothing is wrong with the checkout-api spec itself (image, probe and volume are byte-identical to the healthy revision); the only edit was `APP_VERSION`. The resource that must change is the webhook configuration (delete it, set `failurePolicy: Ignore`, scope its `namespaceSelector`/`objectSelector` away from `checkout`, or restore the `policy-guard` Service). Immediate mitigation for the page: after unblocking admission, or as a stopgap, scale the old ReplicaSet back to 3 so capacity returns while the webhook is fixed.

## Evidence chain

1. **The pod creations are being rejected by an admission webhook, not failing after creation.**
   `describe replicaset.apps/checkout-api-64fb54b496 -n checkout`, Events:
   > `Warning FailedCreate 42s (x15 over 2m4s) replicaset-controller Error creating: Internal error occurred: failed calling webhook "validate.policy-guard.platform.internal": failed to call webhook: Post "https://policy-guard.platform-policy.svc:443/validate?timeout=10s": service "policy-guard" not found`

   This is a direct causal statement: the API server tried to call the webhook, the backing Service does not exist, and the webhook's failure policy turned that into a hard `Error creating`. The `x15 over 2m4s` shows it is repeating on every retry, not a one-off.

2. **No pod objects exist for the new revision at all.**
   Same describe: `Replicas: 0 current / 1 desired` and `Pods Status: 0 Running / 0 Waiting / 0 Succeeded / 0 Failed`. `kubectl get all -A` lists only two checkout pods, both `checkout-api-8587489575-*`. Zero-created (as opposed to created-and-unhealthy) is the signature of admission rejection.

3. **The webhook's backing service genuinely is absent.**
   `kubectl get all -A` Services section lists only `default/kubernetes` and `kube-system/kube-dns`. There is no `platform-policy` namespace, no `policy-guard` service, and no pod anywhere serving it — consistent with the webhook error text `service "policy-guard" not found`.

4. **This is what stalls the rollout.**
   `describe deployment.apps/checkout-api -n checkout`, Conditions:
   > `ReplicaFailure True FailedCreate`
   > `Progressing False ProgressDeadlineExceeded`
   and `Replicas: 3 desired | 0 updated | 2 total | 2 available | 1 unavailable`, `NewReplicaSet: checkout-api-64fb54b496 (0/1 replicas created)`.

5. **Why serving capacity dropped to 2 instead of holding at 3.**
   `describe deployment`: `RollingUpdateStrategy: 1 max unavailable, 0 max surge`. Matching events:
   > `2m4s ScalingReplicaSet Scaled down replica set checkout-api-8587489575 from 3 to 2`
   > `2m4s ScalingReplicaSet Scaled up replica set checkout-api-64fb54b496 from 0 to 1`

   With `maxSurge: 0` the controller must evict an old replica *before* the new one can exist. The new one never came into existence, so the deployment sits at `2/3 READY` (confirmed in `kubectl get all -A`: `deployment.apps/checkout-api 2/3 0 2`). That is the elevated-latency symptom.

6. **The checkout team's change is innocent.**
   Both ReplicaSets' pod templates are identical in the two describes: `Image: busybox:1.36`, same `Command: httpd -f -p 8080 -h /www`, same readiness probe, same `content` ConfigMap volume, same `APP_VERSION: 2026.09.04`. `kubectl get all -A` shows both RS with image `busybox:1.36`. Consistent with "they changed only the version number" — the template hash changed, which was enough to trigger a rollout into a broken admission path.

## Investigation ledger

- **Bad image / registry pull failure in the new version** — ruled out. Both ReplicaSets carry the identical image `busybox:1.36` (`get all -A` and both describes), and the old RS is running it fine. Moreover an image problem produces pods in `ImagePullBackOff`; here `Pods Status: 0 Running / 0 Waiting / 0 Succeeded / 0 Failed` — no pod was ever created.
- **Readiness probe failing on the new pods (`/health` on :8080 against a busybox httpd)** — ruled out as the *cause of this page*. A failing probe requires a running pod; there are none for revision 2, and the deployment condition is `ReplicaFailure/FailedCreate`, which is emitted at object-creation time, not `ProgressDeadlineExceeded` due to unready pods alone. (Worth a follow-up review, but it is not what is blocking the rollout.)
- **Missing/renamed ConfigMap `checkout-content`** — ruled out. The volume is `Optional: false`, so a missing ConfigMap would leave a pod in `ContainerCreating` with a `FailedMount` event on the *pod*. Instead the failure is on the ReplicaSet controller with an explicit webhook message, and no pod exists to mount anything. The two running old pods mount the same ConfigMap successfully.
- **Insufficient node capacity / scheduling constraints (single-node kind cluster)** — ruled out. Requests are `cpu: 10m, memory: 16Mi`; `Node-Selectors: <none>`, `Tolerations: <none>`. A scheduling failure would create the pod and leave it `Pending` with `FailedScheduling`; nothing was created, and the message names a webhook, not the scheduler.
- **ResourceQuota or LimitRange in `checkout` rejecting the pod** — ruled out. Those also surface as `FailedCreate`, but with `exceeded quota:` / `forbidden:` text. The actual text is `failed calling webhook ... service "policy-guard" not found`.
- **PodDisruptionBudget or `maxUnavailable` misconfiguration as root cause** — ruled out as root cause, retained as an amplifier. `maxSurge: 0` explains why capacity fell to 2/3 rather than staying at 3/3, but even with a surge the new pod could not be admitted. It converts a stalled rollout into a *capacity-losing* stalled rollout; it does not create the stall.
- **Another team shipping a conflicting change today** — consistent with the report that no one else shipped. The webhook is cluster-scoped platform infrastructure whose backend Service is simply absent (possibly deleted or never installed in this cluster); no other workload in `get all -A` mutated.
- **Control-plane or CNI degradation** — ruled out. All `kube-system` pods (`etcd`, `kube-apiserver`, `kube-controller-manager`, `kube-scheduler`, `kube-proxy`, `kindnet`, both `coredns`) are `1/1 Running` with `0` restarts and 3d1h uptime.

## Verification recipe

```bash
# 1. Find the offending webhook and prove its failurePolicy is Fail and it targets pod CREATE.
kubectl get validatingwebhookconfigurations -o yaml \
  | grep -A15 'validate.policy-guard.platform.internal'
#    expect: clientConfig.service {name: policy-guard, namespace: platform-policy}
#            failurePolicy: Fail, rules: [pods / CREATE]

# 2. Prove the webhook's backend does not exist.
kubectl get svc -n platform-policy policy-guard
kubectl get ns platform-policy
#    expect: NotFound for both -> every pod CREATE is hard-failed

# 3. Reproduce the rejection directly, independent of the deployment.
kubectl -n checkout run wh-probe --image=busybox:1.36 --restart=Never --command -- sleep 5
#    expect: Error from server (InternalError): failed calling webhook
#            "validate.policy-guard.platform.internal" ... service "policy-guard" not found
```

Remediation check after fixing (delete the webhook config / set `failurePolicy: Ignore` / restore the Service), then:

```bash
kubectl -n checkout rollout status deployment/checkout-api --timeout=120s
```

```json
{
  "case_id": "t2-checkout-release-stalled",
  "failing_resource": {"kind": "ValidatingWebhookConfiguration", "namespace": "", "name": "policy-guard"},
  "mechanism": "The ValidatingWebhookConfiguration webhook 'validate.policy-guard.platform.internal' intercepts pod CREATE with failurePolicy Fail, but its backing Service policy-guard.platform-policy.svc does not exist, so the API server hard-rejects every pod creation in the checkout namespace with 'failed calling webhook ... service \"policy-guard\" not found'. The new ReplicaSet therefore reports FailedCreate and reaches 0 of its desired replicas, leaving the rollout at ProgressDeadlineExceeded. Because the rollout strategy has maxSurge 0 and maxUnavailable 1, one old replica was terminated to make room for a pod that can never be admitted, dropping serving capacity to 2 of 3 and raising p95 latency.",
  "verdict": "confirmed"
}
```