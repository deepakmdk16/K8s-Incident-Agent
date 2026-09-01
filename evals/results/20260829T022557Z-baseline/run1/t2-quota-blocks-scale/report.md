## Root cause

**Confirmed.** The `ResourceQuota` named `checkout-quota` in namespace `checkout` caps the namespace at `pods=2`. The `checkout-api` deployment was scaled to 6 replicas, but the ReplicaSet controller is rejected by the quota admission plugin on every pod creation past the second one. Only 2 checkout-api pods exist and serve traffic, so the scale-up intended to absorb the traffic spike never actually added capacity — the same 2 pods carry the full spike load, which is why p99 latency keeps burning the SLO despite the deployment "showing" 6 desired replicas.

The resource whose spec must change is the `ResourceQuota checkout/checkout-quota` (raise/remove the `pods` hard limit); the deployment spec itself is already correct at 6.

## Evidence chain

- **Quota is the blocker (direct, causal):** `describe replicaset.apps/checkout-api-5f66bf789c -n checkout` events —
  `Warning FailedCreate ... Error creating: pods "checkout-api-5f66bf789c-wj8l8" is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2`
  This exact message repeats for `7htmh`, `gql4n`, `fd87f`, `whgqr`, `f7kvq`, `k7lxt`, `ct5r6`, `9f5j4`, plus `(combined from similar events)` for `2dgbk`. Every replica beyond #2 is rejected at admission.
- **Only 2 of 6 replicas exist:** `describe deployment.apps/checkout-api -n checkout` — `Replicas: 6 desired | 2 updated | 2 total | 2 available | 4 unavailable`; and `NewReplicaSet: checkout-api-5f66bf789c (2/6 replicas created)`.
- **The deployment itself flags the admission failure, not an app failure:** deployment Conditions — `ReplicaFailure True FailedCreate` and `Available False MinimumReplicasUnavailable`. `ReplicaFailure/FailedCreate` is specifically the "could not create pods" (admission/quota) condition, not a scheduling or image condition.
- **The 2 pods that do exist are perfectly healthy — so the deficit is capacity, not brokenness:** `describe pod/checkout-api-5f66bf789c-gqzzb` — `Status: Running`, `Ready: True`, `ContainersReady: True`, `Restart Count: 0`, events `Pulled / Created / Started` with no warnings. Same for `v28xx` (`Ready: True`, `Restart Count: 0`).
- **Application is up and listening, not erroring:** `logs checkout-api-5f66bf789c-gqzzb` — `checkout-api: serving on :8080`; `logs ...-v28xx` — `checkout-api: serving on :8080`. No error, no backend timeout, no panic in either log.
- **Consistency with the top-level view:** `kubectl get all -A` shows only two `checkout-api` pods in the whole cluster, and `deployment.apps/checkout-api 0/6 ... 2` — the missing 4 pods are not Pending or ImagePullBackOff anywhere; they were never created as objects at all, which is the signature of admission rejection rather than a scheduling failure.

## Investigation ledger

- **Insufficient node capacity / unschedulable pods** — ruled out. Pods rejected by the scheduler would exist as objects in `Pending` with `FailedScheduling` events. `kubectl get all -A` lists no Pending checkout pods, and the ReplicaSet's failures come from `replicaset-controller` at *creation* time with `forbidden: exceeded quota`, not from `default-scheduler`. The two pods that were admitted scheduled instantly (`Successfully assigned ... to incident-lab-control-plane`).
- **Image pull problems** — ruled out. `describe pod` for both pods: `Container image "busybox:1.36" already present on machine and can be accessed by the pod`, then `Created` / `Started`, no `ErrImagePull`/`ImagePullBackOff`.
- **Application crashlooping or OOMKilled under load** — ruled out. Both pods show `Restart Count: 0`, `State: Running`, `Ready: True`. No OOMKilled/Error termination reason anywhere.
- **Readiness probe flapping pods out of the Service endpoints** — ruled out. Neither pod spec declares any probes in `describe deployment` / `describe pod`, and both report `Ready: True` / `ContainersReady: True`. So endpoint churn isn't shrinking the serving set.
- **Slow rollout throttled by RollingUpdate surge settings** — ruled out. `StrategyType: RollingUpdate, 25% max unavailable, 25% max surge` with `OldReplicaSets: <none>` and a single revision means this is a plain scale-up, not a constrained rollout; the deployment event is `Scaled up replica set checkout-api-5f66bf789c from 0 to 6`, and the block is the explicit quota rejection.
- **Downstream payment dependency being slow (matching the "timeouts at the payment step" symptom)** — ruled out as *root cause* from this output. There is no payment/backend workload in the cluster at all (`kubectl get all -A` shows only kube-system, local-path-storage, and checkout), and the checkout logs contain no upstream error lines. The payment-step timeouts are explained by the two overloaded replicas.
- **CPU/memory limit throttling of the running pods** — ruled out as the mechanism. `QoS Class: BestEffort` on both pods and no `Limits`/`Requests` in the deployment pod template, so there is no CPU quota throttling them; they are simply outnumbered by the load.

## Verification recipe

```bash
# 1. Show the quota that is capping the namespace at 2 pods
kubectl get resourcequota checkout-quota -n checkout -o wide
kubectl describe resourcequota checkout-quota -n checkout

# 2. Show the deployment wants 6 but only got 2, with the ReplicaFailure condition
kubectl get deploy checkout-api -n checkout -o jsonpath='{.spec.replicas}{" desired / "}{.status.availableReplicas}{" available"}{"\n"}{range .status.conditions[*]}{.type}={.status} {.reason}{"\n"}{end}'

# 3. Show the live admission rejections
kubectl get events -n checkout --field-selector reason=FailedCreate --sort-by=.lastTimestamp | tail -5
```

Expected: quota `hard: pods=2, used: pods=2`; deployment `6 desired / 2 available` with `ReplicaFailure=True FailedCreate`; events repeating `forbidden: exceeded quota: checkout-quota`. Remediation: raise `spec.hard.pods` on `checkout-quota` to at least 6 (plus headroom for surge — max-replicas is annotated at 8) and confirm the remaining 4 pods create and go Ready; then confirm p99 recovers.

```json
{
  "case_id": "t2-quota-blocks-scale",
  "failing_resource": {"kind": "ResourceQuota", "namespace": "checkout", "name": "checkout-quota"},
  "mechanism": "The checkout-quota ResourceQuota hard-limits namespace checkout to pods=2, so admission rejects every checkout-api pod creation past the second with 'exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2'. The scale-up to 6 replicas therefore added zero real capacity — only 2 pods exist and are Ready — leaving the same two replicas to absorb the full traffic spike, which drives the p99 latency SLO burn and payment-step timeouts.",
  "verdict": "confirmed"
}
```