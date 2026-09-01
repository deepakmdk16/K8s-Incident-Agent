## Root cause

**Verdict: confirmed.**

The namespace `checkout` has a `ResourceQuota` named `checkout-quota` with a hard cap of `pods=2`. The `checkout-api` deployment was scaled to 6 replicas, but the ReplicaSet controller can only create 2 pods — every subsequent pod creation is rejected by the quota admission plugin with `403 Forbidden`. So the "scale up to 6" that was supposed to absorb the traffic spike never actually happened: the service is still being served by 2 pods, which are saturated, which is why p99 latency keeps burning the SLO. The quota object (not the pods, which are healthy) is the thing whose spec must change.

## Evidence chain

1. **The quota is the thing rejecting pods.** From `describe replicaset.apps/checkout-api-5f66bf789c -n checkout`, repeated events:
   `Warning FailedCreate ... Error creating: pods "checkout-api-5f66bf789c-wj8l8" is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2`
   This names the quota (`checkout-quota`), the limit (`pods=2`), and the current usage (`used: pods=2`). At least 11 distinct pod names were rejected this way, plus a "(combined from similar events)" roll-up.

2. **The scale-up is stuck at 2, not 6.** `describe deployment.apps/checkout-api -n checkout`:
   `Replicas: 6 desired | 2 updated | 2 total | 2 available | 4 unavailable`
   and conditions `Available False MinimumReplicasUnavailable`, `ReplicaFailure True FailedCreate`.
   `kubectl get all -A` agrees: `deployment.apps/checkout-api 0/6`, `replicaset.apps/checkout-api-5f66bf789c 6 desired / 2 current / 0 ready` (readiness lagging only because the snapshot is 0s old).

3. **The RS confirms only 2 pods exist.** `describe replicaset.apps/checkout-api-5f66bf789c`:
   `Replicas: 2 current / 6 desired` and `Pods Status: 2 Running / 0 Waiting / 0 Succeeded / 0 Failed`. There are no pending/failed pods hiding somewhere — the other four were never created at all.

4. **The two pods that do exist are healthy, so capacity, not correctness, is the problem.** `describe pod/checkout-api-5f66bf789c-gqzzb` and `.../-v28xx` both show `Status: Running`, `Ready: True`, `ContainersReady: True`, `Restart Count: 0`, and `Started` events with no warnings. Logs from both are a clean single line: `checkout-api: serving on :8080`. No errors, no OOM, no crash loop.

5. **Symptom ties to mechanism.** The page says the deployment "was scaled up to 6 replicas to absorb the spike, but latency has not come down." The evidence shows the scale-up was silently only ⅓ effective — 2 of 6 pods serving spike-level traffic — which is exactly the shape of a latency/timeout SLO burn.

## Investigation ledger

- **Image pull failure / ContainerCreating stuck** — ruled out. `get all -A` shows both pods in `ContainerCreating`, but that is a stale 0s-old snapshot; the `describe` of both pods shows `State: Running`, `Started`, and event `Pulled ... "busybox:1.36" already present on machine`. No `ErrImagePull`/`ImagePullBackOff` anywhere.
- **Insufficient node resources / unschedulable pods** — ruled out. If it were scheduling pressure, we would see 4 pods in `Pending` with `FailedScheduling` events. Instead `Pods Status: 2 Running / 0 Waiting / 0 Succeeded / 0 Failed` — the missing pods were never admitted to the API server at all, and the rejection reason is explicitly `exceeded quota`, emitted by the replicaset-controller, not the scheduler.
- **CPU throttling / memory limits on the running pods** — ruled out as the primary cause. The pod spec has no resource requests or limits at all (`QoS Class: BestEffort`, no `Limits`/`Requests` in the deployment container spec), so there is no cgroup CPU quota throttling them. They are simply outnumbered.
- **Application crash / bad code in the checkout container** — ruled out. `Restart Count: 0` on both pods, and logs show only the healthy startup line `checkout-api: serving on :8080` with no errors.
- **Missing/failed ConfigMap mount (`checkout-api-scripts`)** — ruled out. The volume is `Optional: false`, so a missing ConfigMap would block startup; both pods mounted it and executed `sh /app/run.sh` successfully.
- **DNS / networking degradation (coredns)** — ruled out. Both `coredns` pods are `1/1 Running` with `0` restarts for 9h, `kube-dns` service is intact, and `kindnet`/`kube-proxy` DaemonSets are `1/1` ready. Nothing in the checkout pods indicates resolution failure.
- **Downstream payment dependency being slow** — considered, cannot be fully excluded from this output alone since no payment-service workload appears in `get all -A` (it may be off-cluster). However, it is not needed to explain the symptom: the quota block fully accounts for the failed capacity increase, and the page explicitly states latency did not improve *after* the scale-up — which is precisely what a blocked scale-up predicts. This is a secondary hypothesis, not the root cause.
- **Rollout/rollback in progress with a bad revision** — ruled out. `deployment.kubernetes.io/revision: 1`, `OldReplicaSets: <none>`, single ReplicaSet. There is no second revision.

## Verification recipe

```bash
# 1. Show the quota that is capping the namespace — expect pods hard=2, used=2
kubectl get resourcequota checkout-quota -n checkout -o wide
kubectl describe resourcequota checkout-quota -n checkout

# 2. Confirm the deployment wants 6 but only 2 pods exist, with the quota rejection recorded
kubectl get deploy checkout-api -n checkout -o jsonpath='{.spec.replicas} desired / {.status.availableReplicas} available{"\n"}'
kubectl get events -n checkout --field-selector reason=FailedCreate | grep -i quota

# 3. Remediation (raise the cap, then confirm the remaining 4 pods land)
kubectl patch resourcequota checkout-quota -n checkout --type merge -p '{"spec":{"hard":{"pods":"8"}}}'
kubectl rollout status deploy/checkout-api -n checkout --timeout=120s
```

Note on remediation: `pods=8` matches the deployment's `deployment.kubernetes.io/max-replicas: 8` annotation, so it leaves headroom for the 25% maxSurge during future rollouts. If the quota also constrains `cpu`/`memory` (the `describe` in step 1 will show this), those must be raised in the same edit or pod creation will still be rejected on a different dimension. Longer term, add resource requests to the `checkout-api` pod template and keep the quota's pod ceiling above the HPA/scale-up ceiling so a scale-up can never be silently truncated again — and alert on the `ReplicaFailure=True` deployment condition, which was true here and would have caught this in seconds.

```json
{
  "case_id": "t2-quota-blocks-scale",
  "failing_resource": {"kind": "ResourceQuota", "namespace": "checkout", "name": "checkout-quota"},
  "mechanism": "The ResourceQuota checkout-quota caps namespace checkout at pods=2, so when checkout-api was scaled to 6 replicas the API server rejected the 4 additional pod creations with 'exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2'. The intended capacity increase never materialized, leaving 2 healthy pods to absorb the full traffic spike. That saturation is what keeps checkout p99 latency high and causes payment-step timeouts.",
  "verdict": "confirmed"
}
```