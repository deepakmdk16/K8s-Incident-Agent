## Root cause

**Verdict: confirmed.**

The Deployment `recs/recommendations` ships a container spec whose startup command allocates a ~200 MiB in-memory "catalog cache" while the container's memory limit is only 64 Mi. The allocation exceeds the cgroup limit, the kernel OOM-kills the process (exit 137) before it ever reaches the serving loop, the kubelet restarts it, and it dies the same way every time — so the pod never becomes Ready, the Deployment stays at 0/1 Available, and no backend serves the "You may also like" panel.

## Evidence chain

- **Symptom, cluster-wide view** — `kubectl get all -A`: `recs pod/recommendations-85fd7764f4-p9rw8   0/1   OOMKilled   2 (14s ago)` and `recs deployment.apps/recommendations   0/1   1   0`. No other workload in the cluster is unhealthy (all kube-system, local-path-storage pods `1/1 Running`), so the outage is isolated to this Deployment.
- **Kill reason is memory, not a crash or bad exit** — describe of pod `recommendations-85fd7764f4-p9rw8`, container `server`:
  - `State: Terminated / Reason: OOMKilled / Exit Code: 137`
  - `Last State: Terminated / Reason: OOMKilled / Exit Code: 137`
  Two consecutive terminations, both OOMKilled — deterministic, not a transient spike.
- **The allocation that exceeds the limit** — the command in the pod, ReplicaSet and Deployment templates (identical in all three, i.e. it is the workload spec, not a mutated pod):
  `CACHE="$(dd if=/dev/zero bs=1M count=200 2>/dev/null | tr '\0' x)"` — a 200 MiB shell variable held in the process's resident memory.
- **The limit it exceeds** — describe of pod / deployment: `Limits: memory: 64Mi`, `Requests: memory: 32Mi`. 200 MiB requested against a 64 MiB cgroup ceiling.
- **It dies during warm-up, before serving** — `kubectl logs ... --tail=50` shows only `warming catalog cache`. The next echo in the command, `catalog cache ready`, and the loop's `serving recommendations` never appear. The kill lands inside the `dd | tr` allocation.
- **Timing confirms instant death, not gradual leak** — describe of pod: `Started: ... 06:55:42` / `Finished: ... 06:55:42` (same second); previous attempt `Started 06:55:28` / `Finished 06:55:29`. Lifetime ~1s, consistent with an allocation-time OOM rather than a runtime leak.
- **Restart loop is what keeps Ready false** — describe of pod events: `Warning BackOff ... Back-off restarting failed container server`; `Restart Count: 2` in ~25s. Conditions: `Ready: False`, `ContainersReady: False`.
- **Deployment-level consequence** — describe of deployment: `Available   False   MinimumReplicasUnavailable`, `1 desired | 1 updated | 1 total | 0 available | 1 unavailable`. This is exactly the "0/1 Ready" the page reports.
- **Owning spec is the thing to change** — describe of replicaset `recommendations-85fd7764f4` carries the same command and `Limits: memory: 64Mi`, inherited from the Deployment template; deleting the pod would only recreate the identical failing pod.

## Investigation ledger

- **Image pull / bad image (ImagePullBackOff)** — ruled out. Pod events: `Pulled ... Container image "busybox:1.36" already present on machine`, plus `Created` and `Started` ×3. The image resolves and the container runs.
- **Scheduling / node pressure / insufficient capacity** — ruled out. `PodScheduled True`, event `Successfully assigned recs/recommendations-85fd7764f4-p9rw8 to incident-lab-control-plane`, and the request is a modest `32Mi`. The container reaches `Running` before dying; a scheduling failure would leave it `Pending` with a `FailedScheduling` event. Node-level pressure would also have disturbed the co-located kube-system pods, which show `0` restarts.
- **Node-level (system) OOM evicting the pod** — ruled out. Container-level `OOMKilled` with `Exit Code: 137` on the container status, no `Evicted` pod status, no node-pressure eviction events, and every other pod on `incident-lab-control-plane` is `1/1 Running` with `RESTARTS 0`. The kill is scoped to this container's own cgroup limit.
- **Application bug / non-zero exit from the program logic** — ruled out. Exit 137 with `Reason: OOMKilled` is a SIGKILL from the OOM killer, not an application-chosen exit code; and the script has no failure path — its final statement is an infinite `while :` loop.
- **Failing liveness/readiness probe restarting the container** — ruled out. The pod spec in the describe output declares no probes (no `Liveness:`/`Readiness:` lines), and restarts are attributed to `OOMKilled`, not `Unhealthy` probe events.
- **Missing/misconfigured Service breaking the panel** — considered because no Service exists for `recommendations` in `kubectl get all -A` (only `default/kubernetes` and `kube-system/kube-dns`). Not the paged root cause: the alert is specifically "Deployment reports 0/1 Ready", which is fully explained by the OOM loop; a backend with zero Ready endpoints would blank the panel regardless of Service wiring. Worth a follow-up ticket, not the remediation for this page.
- **Dependency failure (CoreDNS/etcd/control plane)** — ruled out. All control-plane and DNS pods are `1/1 Running` with `0` restarts, and the container dies during a local `dd` allocation before any network call.

## Verification recipe

```bash
# 1. Confirm the container is being killed for memory, repeatedly, and see the limit next to the allocation.
kubectl get pod -n recs -l app=recommendations \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[0].lastState.terminated.reason}{"\t"}{.status.containerStatuses[0].lastState.terminated.exitCode}{"\t"}{.spec.containers[0].resources.limits.memory}{"\n"}{end}'
# expect: <pod>   OOMKilled   137   64Mi

# 2. Show that the workload spec itself asks for 200Mi against that 64Mi limit.
kubectl get deploy recommendations -n recs \
  -o jsonpath='{.spec.template.spec.containers[0].command}{"\n"}{.spec.template.spec.containers[0].resources}{"\n"}'
# expect: ...dd if=/dev/zero bs=1M count=200... with {"limits":{"memory":"64Mi"},"requests":{"memory":"32Mi"}}

# 3. Prove it never gets past warm-up (no "catalog cache ready" / "serving recommendations").
kubectl logs -n recs -l app=recommendations --tail=20
```

**Remediation (either direction, both change the Deployment spec):**

```bash
# A. Right-size the limit to fit the real working set (200Mi + headroom):
kubectl set resources deploy/recommendations -n recs \
  --containers=server --limits=memory=320Mi --requests=memory=256Mi

# B. Or, if 200Mi of warm cache is a bug/over-provision, shrink the cache in the command
#    (e.g. count=32) so it fits the 64Mi budget.
```

Then confirm recovery with `kubectl rollout status deploy/recommendations -n recs` and `kubectl get deploy -n recs` showing `1/1`. Separately, file a follow-up to confirm the Service/endpoint wiring that fronts this Deployment, since no `recs` Service appears in the provided cluster listing.

```json
{
  "case_id": "t1-oom-cache-warmup",
  "failing_resource": {"kind": "Deployment", "namespace": "recs", "name": "recommendations"},
  "mechanism": "The Deployment's container command allocates a ~200 MiB in-memory catalog cache (dd bs=1M count=200) at startup, but its pod template sets a memory limit of only 64Mi. The kernel OOM-kills the container during warm-up every time (Reason: OOMKilled, exit 137, twice within ~25s, logs stop at 'warming catalog cache'), so it never reaches its serving loop, never becomes Ready, and the Deployment stays 0/1 Available with MinimumReplicasUnavailable.",
  "verdict": "confirmed"
}
```