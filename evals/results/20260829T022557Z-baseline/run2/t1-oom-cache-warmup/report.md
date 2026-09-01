## Root cause

**Verdict: confirmed.**

The Deployment `recs/recommendations` ships a container spec whose startup command allocates ~200 MiB of resident memory ("catalog cache warm-up") while the same spec caps the container at `memory: 64Mi`. The allocation exceeds the cgroup limit, the kernel OOM-kills the container during warm-up every time, the kubelet restarts it, and it dies again — so the pod never reaches Ready, the Deployment stays at 0/1 Available, and the "You may also like" panel has no backend serving it. The defect is in the workload spec (limit vs. cache size), not in the node, image, scheduling, or networking.

## Evidence chain

1. **The paged symptom is real and workload-local.** `kubectl get all -A`: `recs deployment.apps/recommendations 0/1 1 0 15s`, and `describe deployment.apps/recommendations` → `Replicas: 1 desired | 1 updated | 1 total | 0 available | 1 unavailable`, `Available False MinimumReplicasUnavailable`. Every other workload in the cluster is fully Ready (`coredns 2/2`, `local-path-provisioner 1/1`, `kindnet 1/1`, `kube-proxy 1/1`).

2. **The pod is being OOM-killed, repeatedly.** `kubectl get all -A`: `pod/recommendations-85fd7764f4-p9rw8 0/1 OOMKilled 2 (14s ago) 15s`. `describe pod`:
   - `State: Terminated / Reason: OOMKilled / Exit Code: 137`
   - `Last State: Terminated / Reason: OOMKilled / Exit Code: 137`
   - `Restart Count: 2`
   Exit code 137 = SIGKILL, and the kubelet attributes it to OOM, i.e. the cgroup memory limit was hit — not an application error exit.

3. **The memory demanded by the command exceeds the declared limit.** From `describe pod` (identical in `describe deployment` and `describe replicaset`, so it is the template, not pod drift):
   - Command: `... CACHE="$(dd if=/dev/zero bs=1M count=200 2>/dev/null | tr '\0' x)"; ...` → materialises a ~200 MiB shell variable in the container's address space.
   - `Limits: memory: 64Mi` (Requests: `32Mi`, QoS `Burstable`).
   200 MiB required vs. 64 MiB allowed — the kill is arithmetically inevitable.

4. **The kill happens precisely during warm-up, before serving.** `kubectl logs ... --tail=50` shows only `warming catalog cache` — the very next echo in the command, `catalog cache ready`, never appears, and `serving recommendations` never appears. So the process dies inside the `dd`/`tr` allocation. `describe pod` timestamps corroborate an instant death: `Started: 06:55:42`, `Finished: 06:55:42` (previous attempt: started 06:55:28, finished 06:55:29).

5. **The restart loop, not a one-off.** `describe pod` Events: `Pulled ... (x3 over 25s)`, `Created (x3)`, `Started (x3)`, and `Warning BackOff ... Back-off restarting failed container server`. The container has been created and started three times in 25 seconds and killed each time — CrashLoopBackOff driven by OOM.

6. **Chain closed to the symptom.** No container ever becomes Ready (`Ready: False`, `ContainersReady False`), so the ReplicaSet reports `0` ready, the Deployment reports `0 available`, and there is no healthy backend for the recommendations panel — matching "0/1 Ready for over 15 minutes" and the empty panel storewide.

## Investigation ledger

- **Image pull / bad tag (ImagePullBackOff):** ruled out — Events say `Container image "busybox:1.36" already present on machine and can be accessed by the pod`, and a concrete `Image ID: docker.io/library/busybox@sha256:73aaf0...` plus `Container ID: containerd://3543...` exist. The container runs; it does not fail to start.
- **Scheduling / insufficient node capacity / taints:** ruled out — `Successfully assigned recs/recommendations-85fd7764f4-p9rw8 to incident-lab-control-plane`, `PodScheduled True`, `Node: incident-lab-control-plane/172.18.0.2`. There is no `FailedScheduling` event and the request is only `32Mi`.
- **Node-level memory pressure / node-wide eviction:** ruled out as the driver — an evicted pod would show `Status: Evicted` with a node-pressure event, not `Reason: OOMKilled` with `Exit Code: 137` on the container. Every other pod on the same node (`coredns`, `etcd`, `kube-apiserver`, `kindnet`, etc.) has `RESTARTS 0` over 9h, so the node is not starved; only this container's own cgroup limit is being breached.
- **Application crash / bad config / missing dependency:** ruled out — exit 137 with `Reason: OOMKilled` is a kernel SIGKILL, not an application exit status, and the log shows a normal `warming catalog cache` with no error text before death.
- **Failing readiness/liveness probe killing the container:** ruled out — the pod spec in `describe pod` declares no probes at all (no Liveness/Readiness lines), and there are no `Unhealthy` events. Readiness is False simply because the container isn't running.
- **Service/DNS/networking breaking the panel independently:** ruled out as root cause — there is no Service in namespace `recs` at all in `kubectl get all -A`, but even a perfect Service would have no Ready endpoint while the pod OOM-loops; the pod also has an IP (`10.244.0.15`) and CNI (`kindnet`) plus `coredns` are healthy. (If a Service is genuinely missing from the manifest set, that is a separate follow-up, not the cause of `0/1 Ready`.)
- **Bad rollout / stuck old ReplicaSet:** ruled out — `OldReplicaSets: <none>`, `NewReplicaSet: recommendations-85fd7764f4 (1/1 replicas created)`, `revision: 1`. There is only one revision; nothing to roll back to.
- **Pod-level drift (someone edited the pod, template is fine):** ruled out — the `Limits: memory: 64Mi` and the 200 MiB `dd` command appear identically in `describe deployment`, `describe replicaset`, and `describe pod`, so the defect lives in the Deployment's pod template.

## Verification recipe

```bash
# 1. Confirm the kill reason and exit code are OOM, on every restart:
kubectl get pod -n recs -l app=recommendations \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[0].lastState.terminated.reason}{"\t"}{.status.containerStatuses[0].lastState.terminated.exitCode}{"\t"}{.status.containerStatuses[0].restartCount}{"\n"}{end}'
# expect: ... OOMKilled  137  <increasing count>

# 2. Show the contradiction inside the Deployment template (200 MiB alloc vs 64Mi cap):
kubectl get deploy recommendations -n recs \
  -o jsonpath='{.spec.template.spec.containers[0].command}{"\n---\n"}{.spec.template.spec.containers[0].resources}{"\n"}'
# expect: command containing "count=200" ; resources limits memory=64Mi

# 3. Prove the fix: raise the cap above the warm-up footprint and watch it go Ready.
kubectl set resources deploy/recommendations -n recs -c server --limits=memory=256Mi --requests=memory=128Mi
kubectl rollout status deploy/recommendations -n recs --timeout=90s
kubectl logs -n recs -l app=recommendations --tail=5   # expect "catalog cache ready" then "serving recommendations"
```

**Remediation.** Either raise the container's memory limit in `recs/recommendations` to comfortably exceed the warm-up footprint (≥256Mi limit, with a request around 128Mi so the scheduler reserves realistically), or shrink the warm-up allocation (`count=200` → a value that fits under 64Mi) if 64Mi is a deliberate budget. Whichever is chosen, keep request and limit consistent with actual steady-state usage, and add a readiness probe so a half-warmed cache never gets traffic. Rolling back is not an option here — `revision: 1` is the only revision, so the fix must be a forward change to the Deployment spec.

```json
{
  "case_id": "t1-oom-cache-warmup",
  "failing_resource": {"kind": "Deployment", "namespace": "recs", "name": "recommendations"},
  "mechanism": "The Deployment's pod template runs a startup command that allocates roughly 200 MiB for a catalog cache warm-up while capping the container at memory limit 64Mi, so the kernel OOM-kills the process mid-warm-up every time (Reason: OOMKilled, Exit Code: 137, Restart Count: 2, logs stop at 'warming catalog cache'). The container therefore never becomes Ready and the kubelet backs off restarting it, leaving the Deployment at 0/1 available with no backend to populate the recommendations panel.",
  "verdict": "confirmed"
}
```