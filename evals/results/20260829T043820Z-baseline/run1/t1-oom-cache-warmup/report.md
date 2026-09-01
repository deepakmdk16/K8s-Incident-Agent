## Root cause

**Deployment `recs/recommendations`** ships a container spec whose startup ("catalog cache warmup") allocates a ~200 MiB shell variable while the container's memory limit is 64 Mi. The cgroup limit is breached during warmup, the kernel OOM-kills the container before it ever reaches its serving loop, the pod never becomes Ready, and the Deployment stays at 0/1 Available — which is exactly the paged empty-recommendations-panel symptom. Verdict: **confirmed**.

## Evidence chain

- **The workload is the one paged and is unavailable.** `kubectl get all -A`: `recs deployment.apps/recommendations 0/1 1 0 15s` and `describe deployment.apps/recommendations` → `Replicas: 1 desired | ... | 0 available | 1 unavailable`, `Available False MinimumReplicasUnavailable`. No Service or Endpoints exist for it in the `get all -A` service list, so the only thing serving the panel is this pod, and it is not Ready.
- **The container is being killed for memory, repeatedly.** `kubectl get all -A`: `pod/recommendations-85fd7764f4-p9rw8 0/1 OOMKilled 2 (14s ago) 15s`. `describe pod`: `State: Terminated / Reason: OOMKilled / Exit Code: 137`, and `Last State: Terminated / Reason: OOMKilled / Exit Code: 137`, `Restart Count: 2`. Exit code 137 = SIGKILL, and the reason is explicitly OOMKilled (cgroup limit), not an application exit.
- **The allocation size is in the spec and exceeds the limit.** `describe deployment` / `describe pod` command:
  `CACHE="$(dd if=/dev/zero bs=1M count=200 2>/dev/null | tr '\0' x)"` — a 200 MiB buffer materialized in shell memory, against
  `Limits: memory: 64Mi` (`Requests: memory: 32Mi`). 200 Mi > 64 Mi, so the kill is deterministic on every start.
- **It dies during warmup, before serving.** `kubectl logs ... --tail=50` shows only `warming catalog cache` — the subsequent `catalog cache ready` and `serving recommendations` lines from the same command string never appear. `describe pod` timestamps confirm the container lives ~0–1 s: `Started: ... 06:55:42` / `Finished: ... 06:55:42`.
- **The failure loop is what keeps the Deployment down.** `describe pod` Events: `Started (x3 over 25s)` followed by `Warning BackOff ... Back-off restarting failed container server`. Every restart repeats the same 200 MiB allocation under the same 64 Mi limit.
- **Nothing else in the cluster is broken.** All `kube-system` and `local-path-storage` pods are `1/1 Running` with `0` restarts in `kubectl get all -A`.

## Investigation ledger

- **Scheduling / node pressure / eviction** — ruled out: `describe pod` shows `Successfully assigned recs/recommendations-85fd7764f4-p9rw8 to incident-lab-control-plane`, `PodScheduled True`, and the pod has an IP (`10.244.0.15`). Node-level eviction would surface as `Evicted` status with a `The node was low on resource` event; instead the kill is per-container `OOMKilled` while every other pod on the same node runs undisturbed with 0 restarts.
- **Image pull / registry failure** — ruled out: Events say `Container image "busybox:1.36" already present on machine and can be accessed by the pod` and `Container created` / `Container started` three times. No `ErrImagePull`/`ImagePullBackOff`.
- **Failing readiness/liveness probe killing a healthy container** — ruled out: `describe pod` lists no Liveness or Readiness probe at all, and there are no `Unhealthy` events. The container is `0/1` simply because it is Terminated, not probe-failed.
- **Application crash / bad config / missing dependency (e.g. a backend it calls)** — ruled out: the exit is `Exit Code: 137` with `Reason: OOMKilled`, i.e. SIGKILL from the cgroup OOM killer, not a nonzero application exit (would be 1/2) or a connection error in the logs. The only log line is `warming catalog cache`, emitted before any network work; `Environment: <none>` and `Volumes:` show no external config the container could be missing.
- **Missing/misconfigured Service or Endpoints causing the empty panel while the pod is fine** — ruled out as *root* cause: the pod itself is `0/1` and OOMKilled, so no Service configuration could have made the panel work. Fixing the Deployment's memory spec is the necessary change.
- **Memory *request* too low causing throttling** — ruled out: requests are not enforced as a kill threshold; the enforced ceiling is `Limits: memory: 64Mi`, and QoS is `Burstable`. The kill maps to the limit, not the request.

## Verification recipe

```bash
# 1. Confirm the kill reason and exit code are OOM, not an app exit.
kubectl get pod -n recs -l app=recommendations \
  -o jsonpath='{range .items[*].status.containerStatuses[*]}{.name}{" last="}{.lastState.terminated.reason}{"/"}{.lastState.terminated.exitCode}{" cur="}{.state.terminated.reason}{"\n"}{end}'
# expect: server last=OOMKilled/137 cur=OOMKilled/137

# 2. Show the allocation size in the spec side-by-side with the enforced limit.
kubectl get deploy recommendations -n recs \
  -o jsonpath='{.spec.template.spec.containers[0].command}{"\n"}LIMIT={.spec.template.spec.containers[0].resources.limits.memory}{"\n"}'
# expect: ...count=200... and LIMIT=64Mi  (200Mi allocation > 64Mi limit)

# 3. Prove the fix: raise the ceiling above the warmup footprint and watch it go Ready.
kubectl set resources deploy/recommendations -n recs --limits=memory=256Mi --requests=memory=128Mi
kubectl rollout status deploy/recommendations -n recs --timeout=90s
kubectl logs -n recs -l app=recommendations --tail=5   # expect "catalog cache ready" / "serving recommendations"
```

Longer-term remediation: either right-size the limit to the real warmup footprint (≥256 Mi, with request set to steady-state usage), or shrink the warmup buffer in the container command so the cache fits the intended 64 Mi budget — the spec change belongs on the Deployment, not the pod.

```json
{
  "case_id": "t1-oom-cache-warmup",
  "failing_resource": {"kind": "Deployment", "namespace": "recs", "name": "recommendations"},
  "mechanism": "The Deployment's container command allocates a ~200 MiB cache buffer at startup (dd bs=1M count=200) while its memory limit is only 64Mi, so the cgroup OOM killer SIGKILLs the container during warmup every time (Reason: OOMKilled, Exit Code: 137, logs stop after 'warming catalog cache'). The container never reaches its serving loop, so the pod never becomes Ready and the Deployment stays 0/1 Available in CrashLoopBackOff, leaving the recommendations panel empty.",
  "verdict": "confirmed"
}
```