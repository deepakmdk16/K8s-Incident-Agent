# Incident report — t2-checkout-release-stalled

**Verdict: confirmed**

## Root cause

An orphaned cluster-scoped validating admission webhook — webhook `validate.policy-guard.platform.internal`, whose `clientConfig` points at `Service platform-policy/policy-guard`, a Service that does not exist — is rejecting every pod CREATE in the `checkout` namespace. Because the webhook's failure policy is effectively `Fail` (the call error is returned to the caller as an `Internal error` instead of being ignored), the new ReplicaSet `checkout-api-64fb54b496` cannot create even one pod. The deployment's rollout strategy (`maxUnavailable: 1, maxSurge: 0`) had already terminated one old pod to make room for the new one, so the service dropped from 3 to 2 endpoints and is stuck there: 0 new replicas up, 2 old replicas serving, p95 latency elevated.

The checkout team's change (`APP_VERSION: 2026.09.04`, image unchanged at `busybox:1.36`) is innocent — it merely generated a new pod-template hash and therefore a new ReplicaSet, which is what tripped over the broken admission webhook. Any pod creation in the cluster would fail the same way.

## Evidence chain

1. **The blocking mechanism, verbatim** — `describe replicaset.apps/checkout-api-64fb54b496 -n checkout`, Events:
   `Warning FailedCreate 42s (x15 over 2m4s) replicaset-controller Error creating: Internal error occurred: failed calling webhook "validate.policy-guard.platform.internal": failed to call webhook: Post "https://policy-guard.platform-policy.svc:443/validate?timeout=10s": service "policy-guard" not found`
   This is direct causal evidence: pod creation is being denied at admission, and the denial is surfaced (not ignored), i.e. failure policy is `Fail`.

2. **No pod was ever created for the new revision** — same describe: `Replicas: 0 current / 1 desired`, `Pods Status: 0 Running / 0 Waiting / 0 Succeeded / 0 Failed`. The failure is *pre-pod*; there is nothing to schedule, pull, or probe.

3. **The webhook backend genuinely does not exist** — `kubectl get all -A` lists no namespace `platform-policy`, no `policy-guard` pod, deployment, or service anywhere. The only Services in the cluster are `default/kubernetes` and `kube-system/kube-dns`. So the "service not found" error is literal, not a transient DNS blip.

4. **This is why the deployment is stuck, not the app** — `describe deployment.apps/checkout-api`, Conditions:
   `ReplicaFailure True FailedCreate` and `Progressing False ProgressDeadlineExceeded`, with `Available True MinimumReplicasAvailable`. The deployment is failing to *create*, not failing to *become ready*.

5. **Why 2 replicas instead of 3 (the latency symptom)** — `describe deployment`: `RollingUpdateStrategy: 1 max unavailable, 0 max surge`, and Events:
   `Scaled down replica set checkout-api-8587489575 from 3 to 2` then `Scaled up replica set checkout-api-64fb54b496 from 0 to 1`.
   With `maxSurge: 0`, the controller retired an old pod *before* the new one could be admitted; the new one never arrived, leaving `checkout-api 2/3 ... 0 up-to-date`.

6. **The team's change was only the version** — old RS `checkout-api-8587489575` and new RS `checkout-api-64fb54b496` both show `IMAGES busybox:1.36`, identical command, ports, probe, and volume. Consistent with the reported "changed only the version number" (`APP_VERSION: 2026.09.04` env).

7. **The surviving pods are healthy** — `kubectl get all -A`: `checkout-api-8587489575-46f42 1/1 Running 0`, `-68xwq 1/1 Running 0`. No restarts, both Ready; the app itself and its readiness probe are fine.

## Investigation ledger

- **Bad image / ImagePullBackOff on the new version** — ruled out. Both ReplicaSets carry the same image `busybox:1.36` (`kubectl get all -A`, RS list), and no pod object exists for the new RS to pull anything (`0 Running / 0 Waiting`). An image problem would produce a Pending/ImagePullBackOff pod.
- **Missing/renamed ConfigMap `checkout-content`** (volume is `Optional: false`, so a missing CM would block startup) — ruled out as the *paged* cause. That failure manifests as a pod stuck in `ContainerCreating` with `FailedMount` events; here no pod was created at all, and the error text names an admission webhook, not a volume. The old pods mount the same ConfigMap successfully and are `1/1 Running`.
- **Readiness probe failing on the new version (`/health` on :8080, busybox httpd serving `/www`)** — ruled out. Readiness only applies after a pod exists; the new RS has zero pods. Also the deployment condition is `ReplicaFailure/FailedCreate`, not a readiness-driven `Progressing` stall with unready pods.
- **Insufficient node capacity / scheduling constraint** — ruled out. Requests are trivial (`cpu: 10m, memory: 16Mi`), there are no node selectors or tolerations, and scheduling never enters the picture because no Pod object is created. A scheduling failure would show a Pending pod with `FailedScheduling`.
- **ResourceQuota or LimitRange in `checkout` blocking creation** — ruled out. Quota rejections surface as `Error creating: pods "..." is forbidden: exceeded quota: ...`, not `Internal error occurred: failed calling webhook`.
- **PodDisruptionBudget / eviction interference** — ruled out. The old pod was removed by the deployment controller's own rollout (`Scaled down replica set checkout-api-8587489575 from 3 to 2`), which is normal `maxUnavailable: 1` behavior, not a disruption event.
- **Another team's deploy broke something today** — consistent with "no other team shipped today": the webhook configuration is a pre-existing cluster object whose backing Service is absent; nothing in the checkout manifest references it. The trigger was simply the first pod creation attempted after the backend disappeared.
- **Rollout strategy itself is the root cause** — considered and rejected as *root* cause; `maxSurge: 0` is an aggravating factor that converted "rollout blocked" into "capacity loss and elevated latency", but even with `maxSurge: 1` the new pods would still be rejected by the webhook. It is a remediation target, not the cause.

## Remediation

**Immediate (restore capacity, ~seconds):**
1. Unblock admission. Either delete the orphaned webhook configuration, or flip it to non-blocking:
   `kubectl patch validatingwebhookconfiguration <name> --type=json -p '[{"op":"replace","path":"/webhooks/0/failurePolicy","value":"Ignore"}]'`
   (Preferred if the policy service is intentionally decommissioned: `kubectl delete validatingwebhookconfiguration <name>`. Capture the YAML first: `kubectl get validatingwebhookconfiguration <name> -o yaml > /tmp/policy-guard-backup.yaml`.)
   If instead `policy-guard` is supposed to exist, restore the `platform-policy` namespace/Deployment/Service.
2. The new ReplicaSet will then create its pod on the next controller retry; confirm `checkout-api` returns to `3/3`. If it does not retry promptly, `kubectl rollout restart deployment/checkout-api -n checkout`.

**Follow-up (prevent recurrence):**
- Scope the webhook so it cannot brick the cluster: add a `namespaceSelector`/`objectSelector` excluding `kube-system` and critical namespaces, and set `failurePolicy: Ignore` unless the policy service has an HA, monitored deployment.
- Alert on "webhook configuration references a Service that does not exist" and on `ReplicaFailure=True` on any Deployment.
- Set `maxSurge: 1` on `checkout-api` so a failed rollout does not cost serving capacity (with `maxSurge: 0, maxUnavailable: 1`, any blocked rollout immediately degrades the service).

## Verification recipe

```bash
# 1. Show the orphaned webhook and its dangling service reference (the smoking gun)
kubectl get validatingwebhookconfigurations -o custom-columns=\
'NAME:.metadata.name,WEBHOOK:.webhooks[*].name,FAILPOLICY:.webhooks[*].failurePolicy,SVC_NS:.webhooks[*].clientConfig.service.namespace,SVC:.webhooks[*].clientConfig.service.name'

# 2. Confirm the backing service really is absent
kubectl get svc -n platform-policy policy-guard        # expect: NotFound / no namespace

# 3. Reproduce the admission denial directly, independent of the rollout
kubectl run webhook-canary --image=busybox:1.36 -n checkout --restart=Never --dry-run=server -- true
# expect: Internal error occurred: failed calling webhook "validate.policy-guard.platform.internal"
```

Line 1 identifies the object whose spec must change; line 2 proves the endpoint is gone; line 3 shows any pod creation in `checkout` is rejected, proving the block is cluster-policy-wide and not specific to the checkout image or manifest.

```json
{
  "case_id": "t2-checkout-release-stalled",
  "failing_resource": {"kind": "ValidatingWebhookConfiguration", "namespace": "", "name": "policy-guard (cluster-scoped object containing webhook validate.policy-guard.platform.internal)"},
  "mechanism": "A cluster-scoped validating admission webhook, validate.policy-guard.platform.internal, points its clientConfig at Service platform-policy/policy-guard, which does not exist, and its failure policy is Fail, so the API server rejects every pod CREATE in the checkout namespace with 'Internal error occurred: failed calling webhook'. The new ReplicaSet checkout-api-64fb54b496 therefore reached 0 of 3 pods (0 Running / 0 Waiting), and because the rollout uses maxUnavailable:1 with maxSurge:0 an old pod was already retired to make room, leaving the deployment serving 2 of 3 replicas and driving p95 latency up. Removing the orphaned webhook configuration or setting its failurePolicy to Ignore restores pod admission and lets the rollout finish.",
  "verdict": "confirmed"
}
```