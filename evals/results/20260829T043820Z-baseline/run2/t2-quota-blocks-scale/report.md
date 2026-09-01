## Root cause

**Confirmed.** The `ResourceQuota` named `checkout-quota` in namespace `checkout` caps the namespace at `pods=2`. When `checkout-api` was scaled to 6 replicas, the ReplicaSet controller was admission-denied on every pod creation past the second, so the deployment is stuck at 2 running replicas instead of 6. The scale-up that was supposed to absorb the traffic spike never actually happened — the same two pods are still serving all checkout traffic, which is why p99 latency never came down and shoppers time out at the payment step.

The failing resource whose spec must change is the quota (`ResourceQuota checkout/checkout-quota`), not the deployment: the deployment's spec already requests the correct replica count.

## Evidence chain

- **Scale-up was requested:** `describe deployment.apps/checkout-api -n checkout` → `Replicas: 6 desired | 2 updated | 2 total | 2 available | 4 unavailable`, and event `Normal ScalingReplicaSet ... Scaled up replica set checkout-api-5f66bf789c from 0 to 6`.
- **Scale-up did not happen:** `kubectl get all -A` → `deployment.apps/checkout-api 0/6` and `replicaset.apps/checkout-api-5f66bf789c 6 desired / 2 current`. Only two `checkout-api` pods exist cluster-wide (`-gqzzb`, `-v28xx`).
- **The blocking mechanism, named explicitly:** `describe replicaset.apps/checkout-api-5f66bf789c -n checkout` events, repeated many times:
  `Warning FailedCreate ... Error creating: pods "checkout-api-5f66bf789c-wj8l8" is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2`
  This is an admission-time rejection by the quota `checkout-quota` — `limited: pods=2`.
- **Both controllers surface it as a hard failure, not a transient one:** deployment condition `ReplicaFailure True FailedCreate` and `Available False MinimumReplicasUnavailable`; replicaset condition `ReplicaFailure True FailedCreate`.
- **The two pods that do exist are perfectly healthy** — so the capacity shortfall is purely a count problem, not a per-pod problem:
  - `describe pod/checkout-api-5f66bf789c-gqzzb` → `Status: Running`, `Ready: True`, `Restart Count: 0`, event `Started ... Container started`.
  - `describe pod/checkout-api-5f66bf789c-v28xx` → identical: `Ready: True`, `Restart Count: 0`.
  - Log lines: `checkout-api: serving on :8080` from both pods, with no errors afterwards.
- **Serving capacity is one third of intended:** 2 ready pods vs. 6 desired ⇒ each surviving pod absorbs ~3× its planned share of the spike, which is the direct latency mechanism behind the SLO burn.

## Investigation ledger

- **Node capacity / unschedulable pods (Pending, `FailedScheduling`, insufficient cpu/memory).** Ruled out: the rejections happen at *pod creation*, before scheduling — `Error creating: pods "..." is forbidden`, emitted by `replicaset-controller`, not `default-scheduler`. There are no Pending pods at all; the missing pods do not exist as API objects.
- **Image pull problems.** Ruled out: `describe pod` for both pods shows `Pulled ... Container image "busybox:1.36" already present on machine`, no `ErrImagePull`/`ImagePullBackOff` anywhere.
- **CrashLoopBackOff / OOMKill of checkout-api.** Ruled out: both pods show `Restart Count: 0` and `State: Running` with `Ready: True`; no restarts, no `Last State` termination block.
- **Readiness probe failures keeping pods out of the Service endpoints.** Ruled out: `ContainersReady True` and `Ready True` on both pods; no probes are even defined in the pod template shown in `describe deployment`.
- **A bad rollout / stuck new ReplicaSet (old and new RS fighting).** Ruled out: `describe deployment` shows `OldReplicaSets: <none>`, `deployment.kubernetes.io/revision: 1`, and a single ReplicaSet exists. This is a scale event, not a rollout.
- **CPU throttling of the running pods due to restrictive limits.** Ruled out as *the* cause: the pod template defines no resource requests/limits at all (`QoS Class: BestEffort` in both pod describes), so there is no CPU quota to throttle against. Note that BestEffort is a contributing fragility, but it is not what blocked the scale-up — the quota rejection message is explicit.
- **Container quota dimension (cpu/memory quota) rather than pod-count quota.** Ruled out: the rejection reads `requested: pods=1, used: pods=2, limited: pods=2` — the exhausted dimension is the `pods` object count.
- **DNS / networking failure between checkout and payments.** Ruled out from this output: both `coredns` pods are `1/1 Running` with no restarts, `kube-proxy` and `kindnet` are `1/1 Running`, and the checkout logs contain no resolution or connection errors — only `serving on :8080`.

## Verification recipe

```bash
# 1. Show the quota that is capping the namespace — expect  used 2 / hard 2  on "pods"
kubectl get resourcequota checkout-quota -n checkout -o yaml
kubectl describe resourcequota -n checkout

# 2. Show the admission rejections tied to that quota, newest first
kubectl get events -n checkout --field-selector reason=FailedCreate \
  --sort-by=.lastTimestamp -o wide | tail -20

# 3. Confirm the deployment is starved, not crashing
kubectl get deploy checkout-api -n checkout -o wide
kubectl get pods -n checkout -o wide   # expect exactly 2 Running/Ready pods, 0 restarts
```

**Remediation (for the on-call ticket):**

1. *Immediate:* raise the cap so the intended replicas can be created —
   `kubectl patch resourcequota checkout-quota -n checkout --type merge -p '{"spec":{"hard":{"pods":"8"}}}'`
   (8 leaves headroom for the deployment's 25% maxSurge, which would otherwise re-trigger `FailedCreate` on the next rollout). Then watch `kubectl rollout status deploy/checkout-api -n checkout` and confirm 6/6 Ready before declaring latency recovery.
2. *Follow-up:* the quota's `pods` hard limit must be sized against the workload's max HPA/scale target plus surge, and that relationship should be codified in the namespace's GitOps manifest so a quota of 2 can't silently shadow a deployment of 6 again.
3. *Follow-up:* alert on `ReplicaFailure=True` / `FailedCreate` for production deployments — the cluster knew the scale-up failed immediately, but the page only fired 25 minutes later via the latency SLO.
4. *Hygiene:* the pods are `QoS Class: BestEffort` (no requests/limits). Add requests so the quota can be expressed in cpu/memory terms and so checkout pods aren't first in line for eviction during a spike.

```json
{
  "case_id": "t2-quota-blocks-scale",
  "failing_resource": {"kind": "ResourceQuota", "namespace": "checkout", "name": "checkout-quota"},
  "mechanism": "The ResourceQuota checkout-quota limits namespace checkout to pods=2, so when checkout-api was scaled to 6 replicas the API server rejected every pod creation beyond the second with 'exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2'. The deployment is therefore stuck at 2 Running/Ready pods against 6 desired, and those two pods absorb roughly three times their intended share of the traffic spike. The intended capacity increase never materialized, so checkout p99 latency stayed elevated and burned the SLO.",
  "verdict": "confirmed"
}
```