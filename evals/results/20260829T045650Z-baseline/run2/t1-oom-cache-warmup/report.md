## Root cause

The Deployment `recs/recommendations` ships a container whose startup routine allocates a ~200 MiB in-memory "catalog cache" while the pod template caps memory at 64 Mi. The allocation exceeds the cgroup limit within a second of start, the kernel OOM-kills the container (exit 137), and the kubelet restarts it into the same deterministic failure — so the pod never reaches Ready, the Deployment stays 0/1 Available, and the "You may also like" panel has no backend serving recommendations. Verdict: **confirmed**.

## Evidence chain

- **Symptom / workload state** — `kubectl get all -A`: `recs deployment.apps/recommendations 0/1 1 0 15s` and `recs pod/recommendations-85fd7764f4-p9rw8 0/1 OOMKilled 2 (14s ago) 15s`. The deployment has zero available replicas and the single pod is in an OOM restart loop.
- **Kill reason is memory, not crash/exit-code bug** — describe of pod `recommendations-85fd7764f4-p9rw8`: `State: Terminated / Reason: OOMKilled / Exit Code: 137`, and `Last State: Terminated / Reason: OOMKilled / Exit Code: 137`. Both the current and prior container instances were OOM-killed, so the failure is repeatable, not a one-off.
- **The limit** — describe of pod, container `server`: `Limits: memory: 64Mi`, `Requests: memory: 32Mi` (QoS `Burstable`).
- **The demand that exceeds it** — describe of pod / describe of deployment, container command: `dd if=/dev/zero bs=1M count=200 2>/dev/null | tr '\0' x` assigned into shell variable `CACHE`. That is a single ~200 MiB resident string held by the shell process — roughly 3× the 64 Mi cgroup limit.
- **Kill happens during that allocation, before the service loop** — log line: `2026-08-29T01:25:42.525660569Z warming catalog cache` is the *only* log output. The next echo in the command, `catalog cache ready`, never appears, and neither does `serving recommendations`. The container dies inside the `dd | tr` allocation.
- **Timing corroborates an instant kill** — describe of pod: `Started: ...06:55:42` / `Finished: ...06:55:42` (same second); previous instance `Started: 06:55:28` / `Finished: 06:55:29`. Sub-second lifetimes are consistent with a hard memory-limit kill, not a slow leak.
- **The loop is now backoff-throttled** — describe of pod events: `Warning BackOff ... Back-off restarting failed container server in pod recommendations-85fd7764f4-p9rw8_recs`, with `Pulled/Created/Started (x3 over 25s)`.
- **Owning spec carries the defect** — describe of deployment `recommendations` Pod Template shows the identical `Limits: memory: 64Mi` and the same 200 MiB `dd` command, and describe of replicaset `recommendations-85fd7764f4` repeats it. The bad values live in the Deployment's pod template, so every replacement pod inherits them.
- **Deployment-level consequence** — describe of deployment: `Available False MinimumReplicasUnavailable`, `1 desired | ... | 0 available | 1 unavailable`.

## Investigation ledger

- **Image pull failure / bad image** — ruled out. Pod events: `Container image "busybox:1.36" already present on machine and can be accessed by the pod`, and the container reached `Running`/`Started` three times with a real Container ID.
- **Scheduling problem (insufficient node capacity, taints, node selector)** — ruled out. Pod events: `Successfully assigned recs/recommendations-85fd7764f4 to incident-lab-control-plane`; conditions show `PodScheduled True`. The pod runs; it dies after starting.
- **Node-level memory pressure / node evicting the pod** — ruled out. There are no `Evicted` pods and no eviction events; every other pod on `incident-lab-control-plane` (coredns ×2, etcd, apiserver, controller-manager, scheduler, kube-proxy, kindnet, local-path-provisioner) is `1/1 Running` with `0` restarts and 9h uptime. The kill is scoped to this one container's cgroup limit, and the pod is `Burstable` with a 64Mi cap.
- **Failing readiness/liveness probe** — ruled out. The pod and deployment describes list no `Liveness`/`Readiness`/`Startup` probe on container `server`; unready status is explained by `ContainersReady False` due to the terminated container, and restarts carry `Reason: OOMKilled` rather than probe-failure events.
- **Application logic error / non-zero exit from the script** — ruled out. Exit code is `137` (128+SIGKILL) with `Reason: OOMKilled` on both instances, not a shell/application error code, and the only log line is the benign `warming catalog cache`.
- **Missing Service / wrong selector breaking the panel's routing** — considered because no Service exists in namespace `recs` in `kubectl get all -A`. Not the paged cause: the alert is specifically `Deployment recs/recommendations has reported 0/1 Ready`, and the deployment describe explains that directly via `MinimumReplicasUnavailable`. Even a perfect Service would front zero ready endpoints. Worth flagging as a follow-up after the memory fix, but it is not the mechanism behind 0/1 Ready.
- **CrashLoop from an unrelated dependency (DNS, control plane)** — ruled out. `coredns` is `2/2` available, all `kube-system` components are `Running` with `0` restarts, and the container never gets far enough to make a network call (it dies during local memory allocation).
- **`--previous` log retrieval error indicating a node/runtime fault** — ruled out as a cause. `unable to retrieve container logs for containerd://24673d...` is expected: that earlier container's log was rotated away after being OOM-killed within one second and replaced by newer restarts. Current-container logs retrieve fine.

## Verification recipe

```bash
# 1. Confirm the kill reason and the limit that caused it (both instances OOMKilled at 64Mi).
kubectl get pod -n recs -l app=recommendations \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[0].lastState.terminated.reason}{"/"}{.status.containerStatuses[0].lastState.terminated.exitCode}{"\t"}{.spec.containers[0].resources.limits.memory}{"\n"}{end}'
# expect: ... OOMKilled/137   64Mi

# 2. Confirm the allocation size baked into the owning Deployment's pod template.
kubectl get deploy recommendations -n recs -o jsonpath='{.spec.template.spec.containers[0].command}{"\n"}{.spec.template.spec.containers[0].resources}{"\n"}'
# expect: dd ... bs=1M count=200   (~200MiB)  against limits {memory: 64Mi}

# 3. Prove causality by raising only the limit and watching it go Ready.
kubectl set resources deploy/recommendations -n recs --containers=server --limits=memory=320Mi --requests=memory=256Mi
kubectl rollout status deploy/recommendations -n recs --timeout=90s
kubectl logs -n recs -l app=recommendations --tail=5   # expect "catalog cache ready" then "serving recommendations"
```

**Remediation:** the Deployment's pod template must change. Either (a) raise the container's memory limit above the working set — `limits.memory: 320Mi` with `requests.memory: 256Mi` gives the ~200 MiB cache headroom — or (b) if 64 Mi is the intended budget, shrink the warm-up (`count=200` → a value comfortably under the limit) or move the catalog cache out of process memory. Option (a) is the correct rollback-safe hotfix for the page; option (b) is the capacity-planning conversation to have afterwards. Separately, note that namespace `recs` contains no Service — worth confirming how the storefront reaches this deployment once pods are Ready.

```json
{
  "case_id": "t1-oom-cache-warmup",
  "failing_resource": {"kind": "Deployment", "namespace": "recs", "name": "recommendations"},
  "mechanism": "The Deployment's pod template caps container memory at 64Mi while its startup command allocates a ~200MiB in-memory catalog cache via 'dd if=/dev/zero bs=1M count=200 | tr'. The container exceeds its cgroup limit within a second of starting and is OOM-killed (exit 137) before printing 'catalog cache ready', so the kubelet restarts it into the same deterministic failure and the Deployment never reaches 1/1 Ready.",
  "verdict": "confirmed"
}
```