## Root cause

**Verdict: confirmed.**

The `ResourceQuota` **`checkout-quota`** in namespace `checkout` caps the namespace at `pods=2`. When `checkout-api` was scaled to 6 replicas to absorb the traffic spike, the ReplicaSet controller was able to create only 2 pods; every subsequent pod creation was rejected by the quota admission plugin with `403 Forbidden`. The deployment is therefore permanently stuck at 2/6 serving replicas, so the scale-up delivered zero additional capacity — the same 2 pods absorb the entire spike, which is exactly why p99 checkout latency never came down after the scale-out.

The pods themselves are healthy; the failure is at admission time, before pods ever exist. The spec that must change is the quota (raise `pods`), not the workload.

## Evidence chain

- **Quota is the rejecting mechanism** — `describe replicaset checkout-api-5f66bf789c`, repeated Warning events:
  `Error creating: pods "checkout-api-5f66bf789c-wj8l8" is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2`
  This names the quota object (`checkout-quota`), the limit (`pods=2`), and the current usage (`used: pods=2`). The message repeats for at least 9 distinct pod names plus a `(combined from similar events)` roll-up — i.e. the controller is hot-looping on rejections, not hitting a transient error.
- **Desired vs. actual capacity gap** — `describe deployment checkout-api`:
  `Replicas: 6 desired | 2 updated | 2 total | 2 available | 4 unavailable` and
  `NewReplicaSet: checkout-api-5f66bf789c (2/6 replicas created)`. Only one third of the intended capacity exists.
- **The gap is admission-side, not scheduling- or runtime-side** — same describe:
  `ReplicaFailure   True    FailedCreate` and `Available   False   MinimumReplicasUnavailable`. `ReplicaFailure/FailedCreate` is set specifically when the API rejects pod *creation*; there are no `Pending`/`FailedScheduling` pods anywhere in `kubectl get all -A`.
- **The 2 pods that do exist are perfectly healthy, so the latency is a capacity shortfall not a pod defect** — `describe pod checkout-api-5f66bf789c-gqzzb` and `...-v28xx`: both `Status: Running`, `Ready: True`, `ContainersReady: True`, `Restart Count: 0`, events end at `Started`. Logs from both: `checkout-api: serving on :8080` with no errors.
- **ReplicaSet accounting confirms the ceiling** — `describe replicaset`: `Replicas: 2 current / 6 desired`, `Pods Status: 2 Running / 0 Waiting / 0 Succeeded / 0 Failed`. Nothing is queued or crashing; the missing 4 simply were never admitted.
- **No competing consumer of the quota** — `kubectl get all -A` shows the `checkout` namespace contains exactly the two `checkout-api` pods and nothing else, so the `used: pods=2` is entirely this deployment. Raising the quota directly unblocks it.
- **Symptom/timing fit** — the page says latency "has not come down" *after* the scale-up; the deployment's own event `Scaled up replica set checkout-api-5f66bf789c from 0 to 6` is immediately followed by the FailedCreate storm, so the scale-up never materialized into serving capacity.

(Note: the `AGE 0s` / `ContainerCreating` in `kubectl get all -A` vs. `Running` in the describes is just the two commands being run seconds apart during the same reconcile; both views agree there are only 2 pods and 4 rejections.)

## Investigation ledger

- **Pods crashing / CrashLoopBackOff under load** — ruled out: both pods show `Restart Count: 0`, `State: Running`, `Ready: True`, and their logs contain only `checkout-api: serving on :8080` with no errors or restarts.
- **Image pull failure for the missing replicas** — ruled out: `Pulled ... "busybox:1.36" already present on machine and can be accessed by the pod` on both pods; and the missing replicas produced no pod objects at all, so no pull was ever attempted. The blocking error is `forbidden: exceeded quota`, not `ErrImagePull`.
- **Insufficient node CPU/memory → unschedulable pods** — ruled out: there are no `Pending` pods in `kubectl get all -A` and no `FailedScheduling` events. The two existing pods scheduled instantly (`Successfully assigned ... to incident-lab-control-plane`). Resource pressure would yield Pending pods, not `FailedCreate` at the ReplicaSet.
- **CPU throttling on the running pods (limits too low) causing latency** — ruled out as the paged cause: `QoS Class: BestEffort` on both pods and no `Limits`/`Requests` in the deployment pod template, so no CPU limit exists to throttle against. (Worth revisiting later as a hardening item, but it is not what blocked the scale-out.)
- **LimitRange forcing a rejection** — ruled out: a LimitRange violation surfaces as `forbidden: ... minimum/maximum ... constraint`, whereas the actual message explicitly names `exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2`. Also the quota here is on the `pods` count, not on compute resources, so no requests/limits are needed to satisfy it — only headroom.
- **Service/endpoint or DNS misrouting sending traffic to dead backends** — ruled out: no Service exists in the `checkout` namespace in `kubectl get all -A` (traffic is presumably ingressed elsewhere), and both CoreDNS pods are `1/1 Running` with 0 restarts. Regardless, a routing fault would not produce `FailedCreate` events, and the observed capacity shortfall alone explains sustained p99 burn.
- **A bad rollout / two competing ReplicaSets** — ruled out: `OldReplicaSets: <none>`, `deployment.kubernetes.io/revision: 1`, a single ReplicaSet exists, and `Progressing True ReplicaSetUpdated` — there is no second generation of pods competing for the quota.
- **Node NotReady or control-plane degradation** — ruled out: all `kube-system` pods (`etcd`, `kube-apiserver`, `kube-controller-manager`, `kube-scheduler`, `kube-proxy`, `kindnet`) are `1/1 Running` with 0 restarts and 9h uptime; the API server is healthy enough to be actively enforcing quota and emitting events.

## Verification recipe

```bash
# 1. Show the quota ceiling and that it is fully consumed (expect USED pods=2, HARD pods=2)
kubectl describe resourcequota checkout-quota -n checkout

# 2. Show the live rejection reason tying the missing replicas to that quota
kubectl get events -n checkout --field-selector reason=FailedCreate \
  --sort-by=.lastTimestamp | tail -5

# 3. Confirm the capacity gap the quota is causing
kubectl get deploy checkout-api -n checkout \
  -o custom-columns=DESIRED:.spec.replicas,READY:.status.readyReplicas,AVAIL:.status.availableReplicas
```

Confirmation looks like: quota `pods  2  2`, events reading `exceeded quota: checkout-quota ... limited: pods=2`, and `DESIRED 6 / READY 2`.

**Remediation** — immediate: raise the pod ceiling so the scale-out can land, e.g.
`kubectl patch resourcequota checkout-quota -n checkout --type=merge -p '{"spec":{"hard":{"pods":"10"}}}'`
(set the ceiling above the max HPA/target replica count, with headroom for rolling-update surge — a 6-replica deployment with 25% maxSurge needs at least 8). The 4 missing pods should be admitted within seconds and latency should recover as they become Ready. Follow-ups: alert on `ReplicaFailure=True` and on quota utilization crossing ~80% so a quota ceiling can never silently cap an emergency scale-up again; keep quota limits and HPA `maxReplicas` reconciled in the same manifest/review; and add CPU/memory requests to the pod template so these pods are not `BestEffort`.

```json
{
  "case_id": "t2-quota-blocks-scale",
  "failing_resource": {"kind": "ResourceQuota", "namespace": "checkout", "name": "checkout-quota"},
  "mechanism": "The checkout-quota ResourceQuota caps namespace checkout at pods=2, so when checkout-api was scaled to 6 replicas the API server rejected every pod creation past the second with 'forbidden: exceeded quota: checkout-quota ... limited: pods=2'. The deployment is stuck at 2/6 available with ReplicaFailure/FailedCreate, so the scale-up added no serving capacity and the same two pods absorb the full traffic spike, sustaining the p99 latency SLO burn.",
  "verdict": "confirmed"
}
```