## Root cause

**Deployment `recs/recommendations`** is misconfigured: its container command allocates a ~200 MiB in-memory "catalog cache" (`dd if=/dev/zero bs=1M count=200 | tr '\0' x`) while the pod template caps the container at `memory: 64Mi`. The allocation exceeds the cgroup limit during startup, so the kernel OOM-kills the container before it ever finishes warming the cache and reaches its serving loop. The kubelet restarts it, it OOMs again, and the pod cycles OOMKilled/CrashLoopBackOff — the Deployment never gets a Ready replica, so the recommendations panel has no backend and renders empty. Verdict: **confirmed**.

## Evidence chain

- Paged workload is not available: `kubectl get all -A` → `recs deployment.apps/recommendations 0/1 1 0 15s`, and `describe deployment.apps/recommendations` → `Replicas: 1 desired | ... | 0 available | 1 unavailable`, `Available False MinimumReplicasUnavailable`. This is exactly the paged symptom (0/1 Ready).
- The single pod is being killed for memory, repeatedly: `kubectl get all -A` → `pod/recommendations-85fd7764f4-p9rw8 0/1 OOMKilled 2 (14s ago)`.
- Kill reason and exit code confirm cgroup OOM (not a normal crash): `describe pod/recommendations-85fd7764f4-p9rw8` → `State: Terminated / Reason: OOMKilled / Exit Code: 137`, and `Last State: Terminated / Reason: OOMKilled / Exit Code: 137`.
- The demanded memory is ~200 MiB, hard-coded in the container command: `describe pod` and `describe deployment` both show
  `sh -c echo "warming catalog cache"; CACHE="$(dd if=/dev/zero bs=1M count=200 2>/dev/null | tr '\0' x)"; ...`
  (`bs=1M count=200` = 200 MiB materialized into the shell variable `CACHE`).
- The allowed memory is 64 MiB: `describe deployment.apps/recommendations` pod template → `Limits: memory: 64Mi`, `Requests: memory: 32Mi`. 200 MiB ≫ 64 MiB.
- The kill lands *during* cache warm-up, before the serving loop: log line `warming catalog cache` is the only output, and the expected next line `catalog cache ready` / `serving recommendations` never appears — `kubectl logs ... --tail=50` → `2026-08-29T01:25:42.525660569Z warming catalog cache`.
- Death is near-instant, consistent with a fast bulk allocation rather than a slow leak: `Started: 06:55:42 / Finished: 06:55:42` (current), `Started: 06:55:28 / Finished: 06:55:29` (previous) in `describe pod`.
- The failure is a restart loop, not a one-off: `Restart Count: 2` and event `Warning BackOff ... Back-off restarting failed container server in pod recommendations-85fd7764f4-p9rw8_recs`.
- The defect lives in the Deployment spec (not just the pod): the identical command and `64Mi` limit appear in `describe deployment.apps/recommendations` and `describe replicaset.apps/recommendations-85fd7764f4`, so any replacement pod inherits it.

## Investigation ledger

- **Image pull / bad image (ImagePullBackOff)** — ruled out: `describe pod` event `Normal Pulled ... Container image "busybox:1.36" already present on machine and can be accessed by the pod`, and `Created`/`Started` events fire each cycle. The container runs, then dies.
- **Scheduling failure / insufficient node capacity / taints** — ruled out: `PodScheduled True`, event `Successfully assigned recs/recommendations-85fd7764f4 to incident-lab-control-plane`, and the pod has an IP `10.244.0.15`. Also the request is only `32Mi`, and no `FailedScheduling` events exist.
- **Node-level memory pressure / eviction by kubelet** — ruled out: an eviction would show `Status: Failed` with `Reason: Evicted`, not per-container `OOMKilled` with `Exit Code: 137`; and every other pod on `incident-lab-control-plane` (coredns ×2, etcd, apiserver, kindnet, kube-proxy, scheduler, controller-manager, local-path-provisioner) is `1/1 Running` with `RESTARTS 0` at 9h age. The OOM is scoped to this container's own 64Mi cgroup.
- **Application crash / bad command syntax (exit 1/2)** — ruled out: exit code is `137` (SIGKILL) with `Reason: OOMKilled`, not a nonzero application exit; and the first log line `warming catalog cache` proves the shell executed successfully.
- **Failing readiness/liveness probe keeping it 0/1** — ruled out: `describe pod` lists no `Liveness`/`Readiness` probe on container `server`, and there are no `Unhealthy` events. `0/1` is because the container is dead, not probe-failing.
- **Missing/misconfigured Service breaking the panel wiring** — ruled out as the *paged* cause: the alert is on `Deployment recs/recommendations has reported 0/1 Ready`, and that condition is fully explained above. (Note for the fix follow-up: `kubectl get all -A` shows no Service in `recs`, only `default/kubernetes` and `kube-system/kube-dns` — worth confirming separately, but it does not cause a 0/1 Ready Deployment.)
- **Bad rollout / stuck old ReplicaSet** — ruled out: `describe deployment` → `OldReplicaSets: <none>`, `NewReplicaSet: recommendations-85fd7764f4 (1/1 replicas created)`, `Progressing True NewReplicaSetAvailable`, revision 1. There is only one revision; nothing to roll back to.
- **Config/secret mount failure** — ruled out: only volume is `kube-api-access-v784x` (the projected SA token), `Initialized True`, `PodReadyToStartContainers True`, no `FailedMount` events.

**Remediation (proposed).** Either raise the ceiling or shrink the cache — pick per the real service's working-set:
- Raise the limit above the warm-up peak with headroom, e.g. `kubectl -n recs set resources deployment/recommendations --limits=memory=320Mi --requests=memory=256Mi` (keep request close to the true steady-state so the scheduler places it honestly), **or**
- Reduce the warm-up allocation in the Deployment's command (e.g. `count=200` → a size that fits in 64Mi), or stream/lazy-load the catalog cache instead of materializing it in one shell variable.

Whichever path, patch the **Deployment** spec (the ReplicaSet and pod are regenerated from it). Then watch for `Available True` and confirm the pod logs progress past `catalog cache ready` to `serving recommendations`.

## Verification recipe

```bash
# 1. Confirm the kill reason and exit code are a cgroup OOM (expect: OOMKilled / 137).
kubectl -n recs get pod -l app=recommendations \
  -o jsonpath='{range .items[*].status.containerStatuses[*]}{.name}{" last="}{.lastState.terminated.reason}{"/"}{.lastState.terminated.exitCode}{" cur="}{.state.terminated.reason}{"/"}{.state.terminated.exitCode}{"\n"}{end}'

# 2. Show the 200MiB allocation next to the 64Mi limit, straight from the Deployment spec.
kubectl -n recs get deploy recommendations \
  -o jsonpath='{.spec.template.spec.containers[0].command}{"\n"}LIMITS={.spec.template.spec.containers[0].resources.limits}{"\n"}'

# 3. Prove it dies during warm-up: logs stop at "warming catalog cache",
#    never reaching "catalog cache ready" / "serving recommendations".
kubectl -n recs logs deploy/recommendations -c server --tail=20
```

```json
{
  "case_id": "t1-oom-cache-warmup",
  "failing_resource": {"kind": "Deployment", "namespace": "recs", "name": "recommendations"},
  "mechanism": "The Deployment's container command allocates a ~200 MiB in-memory catalog cache (dd bs=1M count=200 piped into a shell variable) while its pod template sets a hard memory limit of 64Mi. The container exceeds its cgroup limit during warm-up and is OOM-killed (exit 137) before reaching its serving loop, so the kubelet restarts it into a CrashLoopBackOff and the Deployment never reports a Ready replica, leaving the recommendations panel with no backend.",
  "verdict": "confirmed"
}
```