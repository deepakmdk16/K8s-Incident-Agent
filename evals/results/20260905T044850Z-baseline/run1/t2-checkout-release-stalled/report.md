## Root cause

**Verdict: confirmed.**

A cluster-scoped admission webhook — `validate.policy-guard.platform.internal` (the `ValidatingWebhookConfiguration` fronting the `policy-guard` service) — is orphaned: its backing `Service policy-guard` in namespace `platform-policy` does not exist. The webhook's failure policy is effectively `Fail`, so every `CREATE pod` call is rejected with an internal error at admission time.

Consequence for the page: the new ReplicaSet `checkout-api-64fb54b496` cannot create even its first pod (0 of 3 new replicas), while the deployment's `RollingUpdate` strategy (`maxUnavailable: 1`, `maxSurge: 0`) had already scaled the old ReplicaSet `checkout-api-8587489575` down from 3 to 2 to make room for the replacement that can never be admitted. The result is a permanently stalled rollout serving 2/3 replicas and elevated p95 latency. The checkout team's version bump is the innocent trigger, not the cause — it merely created a new pod template hash, and any pod creation in this cluster would now fail the same way.

## Evidence chain

- **The blocking error, verbatim** — `describe replicaset.apps/checkout-api-64fb54b496 -n checkout`, Events:
  `Warning FailedCreate 42s (x15 over 2m4s) replicaset-controller Error creating: Internal error occurred: failed calling webhook "validate.policy-guard.platform.internal": failed to call webhook: Post "https://policy-guard.platform-policy.svc:443/validate?timeout=10s": service "policy-guard" not found`
  This is a *create-time admission rejection*, not a scheduling, image, or runtime failure. The `x15 over 2m4s` shows the controller retrying continuously with no progress.
- **The webhook target genuinely does not exist** — `kubectl get all -A` lists no `platform-policy` namespace at all, and no `service/policy-guard` anywhere; the only Services in the cluster are `default/kubernetes` and `kube-system/kube-dns`. This corroborates the API server's `service "policy-guard" not found`.
- **No pod was ever produced by the new ReplicaSet** — `describe replicaset ... 64fb54b496`: `Replicas: 0 current / 1 desired` and `Pods Status: 0 Running / 0 Waiting / 0 Succeeded / 0 Failed`. Zero pods in *any* state means the failure is before pod object creation, which excludes every pod-level cause.
- **The deployment is stalled, not merely slow** — `describe deployment.apps/checkout-api -n checkout`, Conditions:
  `ReplicaFailure True FailedCreate` and `Progressing False ProgressDeadlineExceeded`. `ReplicaFailure/FailedCreate` propagated up from the ReplicaSet is the deployment controller telling us the same story.
- **Why capacity dropped to 2/3** — same describe: `RollingUpdateStrategy: 1 max unavailable, 0 max surge`, and Events:
  `Scaled down replica set checkout-api-8587489575 from 3 to 2` followed by `Scaled up replica set checkout-api-64fb54b496 from 0 to 1`.
  With `maxSurge: 0` the controller must evict before it creates; the create is blocked, so the surrendered replica never comes back. Hence `Replicas: 3 desired | 0 updated | 2 total | 2 available | 1 unavailable`.
- **The surviving old pods are healthy** — `kubectl get all -A`: `checkout-api-8587489575-46f42` and `-68xwq` are both `1/1 Running`, `RESTARTS 0`. The two remaining replicas serve fine; the deficit is purely the uncreatable third.
- **The change was cosmetic** — the old and new ReplicaSet pod templates in the two describes are byte-identical in image (`busybox:1.36`), command, probe, and even `APP_VERSION: 2026.09.04`; only the `pod-template-hash` differs. Consistent with "the checkout team changed only the version number."

## Investigation ledger

- **Image pull failure / bad tag (`ImagePullBackOff`)** — ruled out. Both ReplicaSets use the identical `busybox:1.36` image, the old one runs it fine (`1/1 Running`), and no pod object exists to pull an image for (`0 Running / 0 Waiting / 0 Succeeded / 0 Failed`).
- **Readiness probe failing on the new version** — ruled out. A failing probe produces a `Running 0/1` pod; here no pod was created. The probe spec (`http-get http://:8080/health`) is also identical between old and new templates.
- **Missing/renamed ConfigMap `checkout-content`** — ruled out as the paged cause. A missing non-optional ConfigMap volume yields a *created* pod stuck in `ContainerCreating` with `FailedMount` events, not `FailedCreate` at admission. The ConfigMap reference is also unchanged between revisions, and the old pods mount it successfully.
- **Unschedulable pod — node pressure, taints, node selector, insufficient CPU/memory** — ruled out. Scheduling happens after pod creation; no pod exists to schedule. Additionally requests are trivial (`cpu: 10m`, `memory: 16Mi`), `Node-Selectors: <none>`, `Tolerations: <none>`, and the single node happily runs 11 other pods.
- **ResourceQuota / LimitRange in `checkout` blocking creation** — plausible shape (also surfaces as `FailedCreate`), but ruled out by the error text itself, which names the webhook call and `service "policy-guard" not found` rather than a quota violation. No quota object appears in the cluster listing.
- **PodDisruptionBudget blocking eviction** — ruled out. The old ReplicaSet *successfully* scaled 3→2 (see deployment events); the stall is on the create side, not the delete side.
- **Bad rollout strategy alone (`maxSurge: 0`)** — considered and ruled out as *root* cause. It is a genuine aggravating factor: it converted "new pods can't start" into "we lost a serving replica," and it's why the symptom is 2/3 rather than 3/3-with-a-stuck-rollout. But with `maxSurge: 1` the new pod would still be rejected by the webhook and the rollout would still never complete. It is a hardening item, not the cause.
- **Another team's concurrent change** — consistent with the report that nobody else shipped: the webhook has no owning workload anywhere in `kubectl get all -A`, suggesting the `policy-guard` Deployment/Service was removed (or its namespace deleted) at some earlier point, leaving the `ValidatingWebhookConfiguration` behind as a latent cluster-wide landmine that only detonated on the next pod creation.

## Verification recipe

```bash
# 1. Show the orphaned webhook and its failurePolicy / scope — expect failurePolicy: Fail
#    and clientConfig.service pointing at platform-policy/policy-guard.
kubectl get validatingwebhookconfigurations -o custom-columns=\
'NAME:.metadata.name,WEBHOOK:.webhooks[*].name,SVC_NS:.webhooks[*].clientConfig.service.namespace,SVC:.webhooks[*].clientConfig.service.name,POLICY:.webhooks[*].failurePolicy'

# 2. Confirm the backing service/namespace is genuinely gone (expect NotFound on both).
kubectl get ns platform-policy; kubectl -n platform-policy get svc policy-guard endpoints policy-guard

# 3. Reproduce the admission rejection directly, with no side effects on checkout.
kubectl -n checkout run webhook-canary --image=busybox:1.36 --restart=Never \
  --dry-run=server --command -- sleep 1
#    Expect: Internal error occurred: failed calling webhook
#            "validate.policy-guard.platform.internal": ... service "policy-guard" not found
```

**Remediation (in order):**
1. *Immediate (restores checkout to 3/3):* remove the orphaned webhook — `kubectl delete validatingwebhookconfiguration <name-from-step-1>` — or, if policy-guard is meant to return, patch it to `failurePolicy: Ignore` and/or add a `namespaceSelector` excluding workload namespaces. Pod creation unblocks immediately and the ReplicaSet's retry loop completes the rollout with no further action.
2. *If policy-guard is supposed to exist:* restore the `platform-policy` namespace, Deployment, and Service, and verify `kubectl -n platform-policy get endpoints policy-guard` is non-empty before re-enabling `failurePolicy: Fail`.
3. *Hardening (prevents recurrence of the capacity loss):* set `maxSurge: 1` on `deployment/checkout-api` so a blocked or failing rollout never surrenders a serving replica, and add a cluster check alerting when any admission webhook's backing Service has zero endpoints.

```json
{
  "case_id": "t2-checkout-release-stalled",
  "failing_resource": {"kind": "ValidatingWebhookConfiguration", "namespace": "", "name": "policy-guard (webhook validate.policy-guard.platform.internal; confirm exact object name via kubectl get validatingwebhookconfigurations)"},
  "mechanism": "The ValidatingWebhookConfiguration validate.policy-guard.platform.internal intercepts pod CREATE calls but its backing Service platform-policy/policy-guard no longer exists, so the API server cannot reach it and, under a Fail failure policy, rejects every pod creation with 'Internal error occurred: failed calling webhook'. The checkout-api ReplicaSet controller therefore cannot create any replacement pod, leaving the release at 0 new replicas and the deployment serving 2 of 3 with elevated p95 latency. Deleting or scoping down this webhook configuration restores pod admission and the stalled rollout completes on its own.",
  "verdict": "confirmed"
}
```