## Root cause

**Verdict: confirmed.**

The `ResourceQuota` named `checkout-quota` in namespace `checkout` caps the namespace at `pods=2`. When `checkout-api` was scaled to 6 replicas to absorb the traffic spike, the ReplicaSet controller was admission-denied on every pod creation past the second one, so the deployment is stuck at **2 running replicas serving the full spike traffic**. The scale-up that was supposed to relieve latency never actually happened — capacity is unchanged, so p99 keeps burning SLO. The two pods that do exist are healthy and serving; the failure is purely at the admission/quota layer, not in the application.

## Evidence chain

- **Scale-up did not take effect.** `kubectl get all -A`: `deployment.apps/checkout-api   0/6   2   0` — 6 desired, only 2 pods exist in the cluster listing (`checkout-api-5f66bf789c-gqzzb`, `checkout-api-5f66bf789c-v28xx`). No other checkout pods appear anywhere.
- **The deployment itself reports the shortfall as a create failure, not a scheduling or image failure.** `describe deployment checkout-api`: `Replicas: 6 desired | 2 updated | 2 total | 2 available | 4 unavailable`, and conditions `Available False MinimumReplicasUnavailable` plus **`ReplicaFailure True FailedCreate`**.
- **The exact blocking mechanism is the namespace quota.** `describe replicaset checkout-api-5f66bf789c` events, repeated ~11 times:
  > `Warning FailedCreate ... Error creating: pods "checkout-api-5f66bf789c-wj8l8" is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2`

  This is an API-server admission rejection (`is forbidden`), so the pod objects were never created — nothing for the scheduler or kubelet to act on.
- **Only 2 creates ever succeeded.** Same event list: `Normal SuccessfulCreate ... Created pod: checkout-api-5f66bf789c-v28xx` and `... -gqzzb`, then nothing but `FailedCreate`. RS status: `Replicas: 2 current / 6 desired`, `Pods Status: 2 Running / 0 Waiting / 0 Succeeded / 0 Failed`.
- **The surviving pods are healthy — the app is not the problem.** `describe pod checkout-api-5f66bf789c-gqzzb`: `Status: Running`, `Ready: True`, `ContainersReady True`, `Restart Count: 0`, events are clean (`Pulled` / `Created` / `Started`, no back-off). Same for `-v28xx`. Their logs are a single clean startup line: `checkout-api: serving on :8080` — no errors, no upstream timeouts.
- **Link to the paged symptom.** The page says the deployment "was scaled up to 6 replicas to absorb the spike, but latency has not come down." The output shows the scale-up was silently rejected: 2 of 6 pods exist, i.e. one third of the intended capacity is absorbing 100% of spike traffic. Serving capacity never increased, so p99 latency and payment-step timeouts persist. The `ScalingReplicaSet ... from 0 to 6` event on the deployment confirms the intent, and the quota events confirm the denial.

## Investigation ledger

- **Application slowness / bad code path in checkout-api** — ruled out as the cause of the *unremediated* scale-up. Both existing pods are `Ready: True` with `Restart Count: 0` and log only `checkout-api: serving on :8080`. Nothing indicates the container is failing; the shortfall is at pod creation, which the app cannot influence.
- **Node capacity exhaustion / unschedulable pods (Insufficient cpu/memory)** — ruled out. Insufficient-resource failures produce `Pending` pods with `FailedScheduling` events. Here the pods were never created at all: the error is `is forbidden: exceeded quota`, emitted by `replicaset-controller` at create time, and RS `Pods Status` shows `0 Waiting`. Also the pods are `QoS Class: BestEffort` with no resource requests, so scheduler resource math is not gating them.
- **Image pull failure / registry throttling** — ruled out. `Normal Pulled ... Container image "busybox:1.36" already present on machine`, no `ErrImagePull`/`ImagePullBackOff` anywhere.
- **Missing ConfigMap `checkout-api-scripts` (`Optional: false`) blocking startup** — ruled out. Both pods mounted it successfully and reached `Running`/`Ready` with `PodReadyToStartContainers True`; a missing ConfigMap would show `FailedMount` events and `ContainerCreating` hangs.
- **The `ContainerCreating` status in `kubectl get all -A` means the pods are stuck starting** — ruled out as a red herring. That listing is a snapshot at `AGE 0s`; the subsequent `describe` of both pods shows `Status: Running`, `Ready: True`, `Started`. Even if they were transiently creating, that would not explain 4 pods that do not exist as objects.
- **CPU throttling from a LimitRange-injected limit** — ruled out. Both pods show `QoS Class: BestEffort` and `Environment: <none>` with no limits rendered in the pod spec, so no CPU cap is being enforced on the running replicas.
- **A stale/competing ReplicaSet holding the quota (bad rollout)** — ruled out. `describe deployment` shows `OldReplicaSets: <none>` and `NewReplicaSet: checkout-api-5f66bf789c (2/6 replicas created)`; there is exactly one RS, and `used: pods=2` is entirely that RS's own two pods. Nothing else in namespace `checkout` appears in `get all -A`.
- **DNS / networking degradation causing latency** — ruled out as the paged root cause. Both `coredns` pods are `1/1 Running` for 9h with 0 restarts, and `kindnet`/`kube-proxy` DaemonSets are `1/1` ready. No connectivity errors in checkout logs.

## Verification recipe

```bash
# 1. Show the quota that is capping the namespace at 2 pods (used == hard).
kubectl get resourcequota checkout-quota -n checkout -o wide
kubectl describe resourcequota checkout-quota -n checkout

# 2. Show the deployment wants 6 but only has 2, with a FailedCreate ReplicaFailure.
kubectl get deploy checkout-api -n checkout \
  -o custom-columns='NAME:.metadata.name,DESIRED:.spec.replicas,READY:.status.readyReplicas,AVAIL:.status.availableReplicas'
kubectl get deploy checkout-api -n checkout -o jsonpath='{range .status.conditions[*]}{.type}={.status} {.reason}{"\n"}{end}'

# 3. Show the live admission denials naming the quota.
kubectl get events -n checkout --field-selector reason=FailedCreate --sort-by=.lastTimestamp | tail -5
```

Expect: quota `pods` used `2`, hard `2`; deployment `DESIRED=6 READY=2`; `ReplicaFailure=True FailedCreate`; events reading `exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2`.

**Remediation:** raise (or remove) the `pods` hard limit on `ResourceQuota/checkout-quota` to at least the intended replica count plus rollout surge headroom — for a 6-replica deployment with `25% maxSurge`, allow at least `8` pods (the RS already annotates `deployment.kubernetes.io/max-replicas: 8`), e.g. `kubectl patch resourcequota checkout-quota -n checkout --type=merge -p '{"spec":{"hard":{"pods":"8"}}}'`. The pending replicas will be created immediately by the ReplicaSet controller. Note the quota only constrains `pods`, not cpu/memory, and the pods are `BestEffort` — after the fix, confirm the node can actually host 6 replicas and follow up by adding resource requests/limits plus a matching cpu/memory quota so the pod-count cap is not the only guardrail. Longer term, make the HPA/scale ceiling and the namespace quota version-controlled together so a scale-up can never be silently rejected, and alert on `ReplicaFailure=True` / `FailedCreate` so this surfaces as a scaling failure rather than only as latency burn.

```json
{
  "case_id": "t2-quota-blocks-scale",
  "failing_resource": {"kind": "ResourceQuota", "namespace": "checkout", "name": "checkout-quota"},
  "mechanism": "ResourceQuota checkout-quota caps namespace checkout at pods=2, so when checkout-api was scaled to 6 replicas the API server admission-denied every pod create past the second one ('is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2'). The deployment is stuck at 2 available of 6 desired with ReplicaFailure/FailedCreate, so serving capacity never increased. The two existing pods absorb the entire traffic spike, keeping checkout p99 latency high and causing payment-step timeouts.",
  "verdict": "confirmed"
}
```