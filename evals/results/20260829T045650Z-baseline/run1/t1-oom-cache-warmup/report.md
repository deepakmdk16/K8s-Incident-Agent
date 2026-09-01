## Root cause

**Deployment `recs/recommendations`** ships a container whose startup routine allocates a ~200 MiB in-memory "catalog cache" while the pod template caps the container at `memory: 64Mi`. The allocation exceeds the cgroup limit within a second of start, the kernel OOM-kills the container (exit 137) before it ever finishes warm-up, and the kubelet restart loop drives the pod into `CrashLoopBackOff`. The container therefore never becomes Ready, the Deployment stays `0/1 available`, and the "You may also like" panel has no backend serving it.

Verdict: **confirmed** — the container command, the limit, and the OOMKilled/137 termination are all present in the same output and line up causally.

## Evidence chain

- **Paged symptom present in cluster state** — `kubectl get all -A`: `recs deployment.apps/recommendations 0/1 1 0 15s`, and its pod `pod/recommendations-85fd7764f4-p9rw8 0/1 OOMKilled 2 (14s ago)`.
- **The killer is memory, not a crash or bad exit** — `describe pod/recommendations-85fd7764f4-p9rw8`:
  - `State: Terminated / Reason: OOMKilled / Exit Code: 137`
  - `Last State: Terminated / Reason: OOMKilled / Exit Code: 137`
  Two consecutive terminations, both OOM.
- **Demand side (what allocates)** — same describe, container command:
  `CACHE="$(dd if=/dev/zero bs=1M count=200 2>/dev/null | tr '\0' x)"` → a single shell variable holding ~200 MiB (plus `dd`/`tr` pipeline overhead).
- **Supply side (what's allowed)** — same describe:
  `Limits: memory: 64Mi` / `Requests: memory: 32Mi`, `QoS Class: Burstable`. 200 MiB requested against a 64 MiB hard limit.
- **Death occurs during warm-up, before serving** — `kubectl logs ... --tail=50` shows only `warming catalog cache`; the next echo in the command, `catalog cache ready`, and the steady-state `serving recommendations` never appear. Timestamps in the describe confirm this: `Started: 06:55:42`, `Finished: 06:55:42` — killed in the same second it started.
- **The defect lives in the Deployment spec, not the pod** — the identical command and identical `Limits: memory: 64Mi` appear verbatim in `describe deployment.apps/recommendations` and `describe replicaset.apps/recommendations-85fd7764f4`. Every replacement pod inherits it; `Restart Count: 2` in 25 s shows the loop.
- **Deployment-level consequence** — `describe deployment.apps/recommendations`: `Available False MinimumReplicasUnavailable`, `1 desired | 1 total | 0 available | 1 unavailable`, matching the "0/1 Ready for over 15 minutes" page.

## Investigation ledger

- **Image pull / registry failure** — ruled out: events show `Pulled ... Container image "busybox:1.36" already present on machine`, plus `Created` and `Started` ×3. The container runs; it does not fail to start.
- **Application bug / non-zero exit from bad config** — ruled out: exit code is 137 with `Reason: OOMKilled`, the kernel-OOM signature, not an application-chosen exit status. The log line reached is the first echo, so no config parsing happened yet.
- **Node-level memory pressure / eviction (noisy-neighbour)** — ruled out: pod `Status: Running` with an assigned IP and no `Evicted` status or `Preempting`/eviction events; every other pod on `incident-lab-control-plane` (coredns ×2, etcd, apiserver, controller-manager, scheduler, kube-proxy, kindnet, local-path-provisioner) is `1/1 Running` with `0` restarts. This is a per-container cgroup limit hit, not node pressure.
- **Scheduling / capacity problem** — ruled out: `PodScheduled True`, `Successfully assigned recs/recommendations-85fd7764f4-p9rw8 to incident-lab-control-plane`; the 32Mi request fits fine.
- **Failing readiness/liveness probe** — ruled out: the pod template in the Deployment and ReplicaSet describes declares no probes; `Ready: False` follows from the container being in `Terminated`, not from a probe verdict.
- **Missing Service / selector mismatch breaking the panel's routing** — ruled out as *root cause* of the page: no Service exists in `recs` (`kubectl get all -A` lists only `default/kubernetes` and `kube-system/kube-dns`), but the alert fired on `Deployment 0/1 Ready`, and even a perfect Service would front zero Ready endpoints. Worth a follow-up ticket; it is not the paged mechanism.
- **Bad rollout / stuck old ReplicaSet** — ruled out: `OldReplicaSets: <none>`, single revision (`deployment.kubernetes.io/revision: 1`), `NewReplicaSetAvailable`. There is nothing to roll back to; revision 1 has always been broken.

## Verification recipe

```bash
# 1. Confirm the kill reason and exit code on both the current and previous run
kubectl get pod -n recs -l app=recommendations \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[0].state.terminated.reason}{"/"}{.status.containerStatuses[0].state.terminated.exitCode}{"\t"}{.status.containerStatuses[0].lastState.terminated.reason}{"\n"}{end}'
# expect: ... OOMKilled/137  OOMKilled

# 2. Show the limit and the allocation size side by side in the Deployment spec (the resource to fix)
kubectl get deploy recommendations -n recs \
  -o jsonpath='{.spec.template.spec.containers[0].resources}{"\n"}{.spec.template.spec.containers[0].command}{"\n"}'
# expect: {"limits":{"memory":"64Mi"},"requests":{"memory":"32Mi"}} and "... count=200 ..."

# 3. Prove the limit is the binding constraint: raise it and watch the pod go Ready
kubectl set resources deploy/recommendations -n recs -c server --limits=memory=320Mi --requests=memory=256Mi
kubectl rollout status deploy/recommendations -n recs --timeout=90s
kubectl logs -n recs -l app=recommendations --tail=5   # expect "catalog cache ready" / "serving recommendations"
```

**Remediation.** Immediate: raise the `server` container's memory limit above the warm-up footprint (~200 MiB of payload plus pipeline overhead — `320Mi` limit / `256Mi` request gives headroom) on the Deployment, so the change survives pod restarts. Durable alternative if the 64 MiB budget is intentional: shrink or stream the catalog cache (reduce `count=200`, or page it from disk/an external cache) rather than materialising it in a single shell variable. Either way, set request and limit from a measured steady-state footprint, and add a readiness probe plus a follow-up ticket to create the missing `recs` Service so the panel actually has a stable endpoint to call.

```json
{
  "case_id": "t1-oom-cache-warmup",
  "failing_resource": {"kind": "Deployment", "namespace": "recs", "name": "recommendations"},
  "mechanism": "The recommendations Deployment's pod template caps the 'server' container at memory 64Mi, but its startup command builds a ~200 MiB in-memory catalog cache via 'dd if=/dev/zero bs=1M count=200 | tr'. The allocation blows past the cgroup limit within a second, so the kernel OOM-kills the container (exit 137) during warm-up, before it can serve; the kubelet restart loop keeps every replacement pod non-Ready, leaving the Deployment at 0/1 available and the recommendations panel empty.",
  "verdict": "confirmed"
}
```