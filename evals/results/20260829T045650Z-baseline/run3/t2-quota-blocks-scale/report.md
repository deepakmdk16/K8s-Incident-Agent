## Root cause

**Confirmed.** The `checkout` namespace has a `ResourceQuota` named `checkout-quota` that caps the namespace at `pods=2`. The `checkout-api` deployment was scaled to 6 replicas, but the ReplicaSet controller is being rejected by the quota admission plugin on every pod create beyond the second one. Only 2 of 6 replicas exist, so the scale-up that was supposed to absorb the traffic spike never actually happened — the same 2 pods are still serving all checkout traffic, which is why p99 latency stayed pinned and the SLO keeps burning.

The two pods that do exist are healthy; nothing is crashing. The failure is at admission time, not at runtime — the capacity simply was never created.

## Evidence chain

- **`kubectl get all -A`**: `deployment.apps/checkout-api  0/6  2 (UP-TO-DATE)  0 (AVAILABLE)` and `replicaset.apps/checkout-api-5f66bf789c  6 DESIRED / 2 CURRENT`. Six replicas requested, two materialized.
- **`describe deployment.apps/checkout-api`**: `Replicas: 6 desired | 2 updated | 2 total | 2 available | 4 unavailable`, and conditions `Available False MinimumReplicasUnavailable` plus `ReplicaFailure True FailedCreate`. The deployment itself is reporting that pod *creation* is what failed.
- **`describe replicaset.apps/checkout-api-5f66bf789c`** — the direct causal line, repeated for every missing pod:
  `Warning FailedCreate ... Error creating: pods "checkout-api-5f66bf789c-wj8l8" is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2`
  This names the enforcing object (`checkout-quota`), the resource (`pods`), the ceiling (`limited: pods=2`), and the current usage (`used: pods=2`). Ten-plus such events, one per rejected replica.
- **Only two `SuccessfulCreate` events** in the same ReplicaSet event stream (`Created pod: ...-v28xx`, `Created pod: ...-gqzzb`) — matching exactly the quota's `pods=2` ceiling.
- **The two surviving pods are fine**, so the latency is not a per-pod defect: `describe pod/checkout-api-5f66bf789c-gqzzb` shows `Status: Running`, `Ready: True`, `Restart Count: 0`, and event `Started ... Container started`. Same for `-v28xx`. Logs from both are a clean single line: `checkout-api: serving on :8080` — no errors, no backend timeouts, no restarts.
- **No pending/unschedulable pods exist at all.** In `kubectl get all -A` the `checkout` namespace lists exactly two pods. The missing four were never admitted to the API server, so they never became `Pending` objects — consistent with admission-time rejection rather than scheduling failure.

Causal chain: quota ceiling `pods=2` → 4 of 6 pod creates rejected at admission → serving capacity stuck at 2 replicas during a traffic spike → per-pod load ~3x intended → checkout p99 latency stays above SLO and payment step times out.

## Investigation ledger

- **Insufficient node capacity / unschedulable pods** — ruled out. Scheduling failure would leave `Pending` pods with `FailedScheduling` events from `default-scheduler`. Instead the two existing pods show `Successfully assigned checkout/... to incident-lab-control-plane` and the other four have no pod objects at all; the errors come from `replicaset-controller` at create time, not the scheduler.
- **Image pull failure / bad image** — ruled out. `describe pod` shows `Pulled: Container image "busybox:1.36" already present on machine and can be accessed by the pod` and `Started`. No `ImagePullBackOff` anywhere.
- **Application crashlooping or failing readiness under load** — ruled out. Both pods report `Ready: True`, `ContainersReady True`, `Restart Count: 0`, and their logs contain only `checkout-api: serving on :8080` with no error output.
- **CPU throttling from tight resource limits on the pods** — ruled out as the blocker. The pod spec in `describe deployment` sets no requests or limits, and `QoS Class: BestEffort` confirms it. There are no CPU limits to throttle against. (Note: this also means the quota is counting raw pod count, not compute.)
- **A downstream dependency (payment service, DNS, database) being slow** — ruled out from this output. There is no payment or database workload in `kubectl get all -A` at all, and `coredns` is `2/2 Running` with 9h uptime and zero restarts. Nothing in the checkout logs shows a downstream call failing.
- **Bad rollout / stuck new ReplicaSet from a recent deploy** — ruled out. `OldReplicaSets: <none>` and `deployment.kubernetes.io/revision: 1`; there is only one ReplicaSet, so this is a pure scale event, not a version rollout.
- **Service selector mismatch sending traffic nowhere** — ruled out as the root cause. Latency is elevated but checkouts are completing slowly rather than failing outright, and the deployment/ReplicaSet selector `app=checkout-api` matches the pod labels `app=checkout-api`. Worth noting there is no `Service` object listed for `checkout` in the output, but that is a separate observation and does not explain the 4 missing replicas, which are explicitly accounted for by the quota errors.

## Verification recipe

```bash
# 1. Show the quota ceiling and that it is fully consumed — expect USED pods=2, HARD pods=2
kubectl describe resourcequota checkout-quota -n checkout

# 2. Show the admission rejections tying the quota to the missing replicas
kubectl get events -n checkout --field-selector reason=FailedCreate \
  --sort-by=.lastTimestamp | tail -20

# 3. Confirm the gap between desired and actual serving capacity
kubectl get deploy checkout-api -n checkout \
  -o custom-columns=DESIRED:.spec.replicas,READY:.status.readyReplicas,UNAVAIL:.status.unavailableReplicas
```

Expected: command 1 prints `pods  2  2`; command 2 prints `exceeded quota: checkout-quota ... limited: pods=2`; command 3 prints `6  2  4`.

**Remediation:** raise the `pods` hard limit in `ResourceQuota/checkout-quota` (e.g. to 8, above the deployment's `max-replicas: 8` surge ceiling) so the ReplicaSet can create the remaining four replicas. Immediately after, confirm `kubectl rollout status deploy/checkout-api -n checkout` reaches 6/6 and watch p99 recover. Follow-ups for the postmortem: add an alert on the `ReplicaFailure`/`FailedCreate` condition so quota-blocked scaling pages directly instead of surfacing as a latency SLO burn, size the quota against the HPA/max-surge ceiling rather than steady-state replica count, and set CPU/memory requests on `checkout-api` (currently `BestEffort`) so it is not first in line for eviction under node pressure.

```json
{
  "case_id": "t2-quota-blocks-scale",
  "failing_resource": {"kind": "ResourceQuota", "namespace": "checkout", "name": "checkout-quota"},
  "mechanism": "ResourceQuota checkout-quota caps the checkout namespace at pods=2, so when checkout-api was scaled to 6 replicas the API server rejected 4 of the 6 pod creations at admission time with 'exceeded quota: checkout-quota ... limited: pods=2'. Serving capacity therefore stayed at 2 replicas instead of 6 during the traffic spike, leaving each pod carrying roughly three times the intended load and keeping checkout p99 latency above its SLO.",
  "verdict": "confirmed"
}
```