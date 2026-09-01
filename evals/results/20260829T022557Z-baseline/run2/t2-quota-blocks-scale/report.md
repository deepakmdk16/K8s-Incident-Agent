## Root cause

**Verdict: confirmed.**

The `ResourceQuota` named `checkout-quota` in namespace `checkout` caps the namespace at `pods=2`. The checkout-api deployment was scaled to 6 replicas, but the ReplicaSet controller was only able to create 2 pods; every subsequent pod creation was rejected by the quota admission plugin with `forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2`. So the intended 3x capacity increase never materialised — the service is still serving the spike on 2 pods, which is why p99 latency did not come down after the "scale up". The two pods that do exist are healthy and serving; the deficit is purely a missing-capacity problem caused by the quota ceiling.

The resource whose spec must change is the ResourceQuota `checkout/checkout-quota` (raise the `pods` hard limit), not the deployment or its pods.

## Evidence chain

- **Scale-up was requested and only partially fulfilled** — `describe deployment.apps/checkout-api -n checkout`: `Replicas: 6 desired | 2 updated | 2 total | 2 available | 4 unavailable`, and event `Scaled up replica set checkout-api-5f66bf789c from 0 to 6`.
- **The deployment itself reports the creation failure, not a scheduling or image failure** — same describe, Conditions: `ReplicaFailure   True    FailedCreate` and `Available   False   MinimumReplicasUnavailable`.
- **The exact blocking mechanism is quota admission** — `describe replicaset.apps/checkout-api-5f66bf789c -n checkout`, repeated events:
  `Warning FailedCreate ... Error creating: pods "checkout-api-5f66bf789c-wj8l8" is forbidden: exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2`
  (repeated for `7htmh`, `gql4n`, `fd87f`, `whgqr`, `f7kvq`, `k7lxt`, `ct5r6`, `9f5j4`, plus `(combined from similar events)` for more). The quota name, the dimension (`pods`), the used value (2) and the hard limit (2) are all stated verbatim.
- **Exactly the number of pods the quota allows exist** — `kubectl get all -A` shows only two checkout pods (`checkout-api-5f66bf789c-gqzzb`, `checkout-api-5f66bf789c-v28xx`); ReplicaSet describe: `Replicas: 2 current / 6 desired`, `Pods Status: 2 Running / 0 Waiting / 0 Succeeded / 0 Failed` — there are zero pending/failed pods, i.e. the missing 4 were never created at all.
- **The pods that do exist are healthy, so the latency is a capacity shortfall not a per-pod fault** — `describe pod/checkout-api-5f66bf789c-gqzzb`: `Status: Running`, `Ready: True`, `ContainersReady: True`, `Restart Count: 0`; log line: `checkout-api: serving on :8080`. Identical for `-v28xx` (`Ready: True`, `Restart Count: 0`, `checkout-api: serving on :8080`).

## Investigation ledger

- **Image pull / registry problem** — ruled out. Both pod describes show `Pulled ... Container image "busybox:1.36" already present on machine and can be accessed by the pod`, then `Created` and `Started`. The `ContainerCreating` status in `get all -A` is just the 0s-old snapshot; the describes show them Running.
- **Insufficient node resources / unschedulable pods** — ruled out. There are no Pending pods to schedule (`Pods Status: 2 Running / 0 Waiting / 0 Succeeded / 0 Failed`), and no `FailedScheduling` / `Insufficient cpu|memory` events anywhere. The missing replicas were rejected at API admission before a scheduler ever saw them.
- **CrashLoopBackOff / OOMKill / failing readiness probe on the running pods** — ruled out. Both pods: `Restart Count: 0`, `Ready: True`, `State: Running`, and their logs contain only the healthy startup line.
- **Node pressure / taints blocking placement** — ruled out. The single node `incident-lab-control-plane` is hosting all control-plane pods, coredns, kindnet, kube-proxy and the local-path-provisioner, all `1/1 Running`; the checkout pods scheduled onto it successfully (`Successfully assigned checkout/... to incident-lab-control-plane`).
- **Broken rollout / bad new revision (old ReplicaSet stuck)** — ruled out. `OldReplicaSets: <none>`, `deployment.kubernetes.io/revision: 1`, only one ReplicaSet exists. This is a first-and-only revision being scaled, not a rollout.
- **DNS / networking degradation causing payment-step timeouts** — ruled out as root cause. Both `coredns` pods are `1/1 Running` with 0 restarts, kube-proxy and kindnet daemonsets are `1/1 ready`. Nothing in the output points to a network fault; the slow payment step is explained by 2 pods absorbing traffic sized for 6.
- **Missing ConfigMap `checkout-api-scripts`** — ruled out. The volume is `Optional: false` and both pods mounted it and started successfully, and the container executed `/app/run.sh` (log output present).
- **A quota dimension other than `pods` (cpu/memory) being the binding constraint** — ruled out by the message itself: the rejection names `requested: pods=1, used: pods=2, limited: pods=2`. Also the pods are `QoS Class: BestEffort` with no requests/limits set, so cpu/memory quota dimensions are not what is being consumed here (and would have produced a different admission error, e.g. "must specify limits").

## Verification recipe

```bash
# 1. Show the quota that is blocking the scale-up: hard pods limit vs used
kubectl get resourcequota checkout-quota -n checkout -o yaml
#    (or: kubectl describe resourcequota -n checkout) -> expect pods hard: 2, used: 2

# 2. Confirm the deployment wants 6 but only 2 pods exist, and why
kubectl get deploy checkout-api -n checkout -o wide
kubectl get events -n checkout --field-selector reason=FailedCreate \
  --sort-by=.lastTimestamp | tail -5
#    -> "forbidden: exceeded quota: checkout-quota ... limited: pods=2"

# 3. Prove the fix direction (raise the ceiling, then watch pods appear)
kubectl patch resourcequota checkout-quota -n checkout \
  --type merge -p '{"spec":{"hard":{"pods":"8"}}}' \
  && kubectl rollout status deploy/checkout-api -n checkout --timeout=90s
```

**Remediation:** raise `spec.hard.pods` on `checkout/checkout-quota` to at least 6 (recommend 8 to match the deployment's `max-replicas: 8` surge annotation), then confirm the ReplicaSet reaches 6/6 and latency recovers. Follow-ups: if the quota also gains cpu/memory dimensions, give the `api` container explicit requests/limits (it is currently `BestEffort`); and add an alert on `ReplicaFailure=True` / `FailedCreate` so a quota-blocked scale-up pages immediately instead of being discovered via an SLO burn 25 minutes later. Longer term, keep the namespace quota and the HPA/max-replica ceiling in the same config source so they cannot drift apart.

```json
{
  "case_id": "t2-quota-blocks-scale",
  "failing_resource": {"kind": "ResourceQuota", "namespace": "checkout", "name": "checkout-quota"},
  "mechanism": "The ResourceQuota checkout-quota caps namespace checkout at pods=2, so when checkout-api was scaled to 6 replicas the ReplicaSet controller could only create 2 pods and all further creations were rejected at admission with 'exceeded quota: checkout-quota, requested: pods=1, used: pods=2, limited: pods=2'. The deployment is stuck at 2/6 available with ReplicaFailure/FailedCreate, so the spike is still being served by two pods and p99 latency never dropped. Raising the quota's pods hard limit is what unblocks the added capacity.",
  "verdict": "confirmed"
}
```