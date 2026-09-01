## Root cause

**Verdict: confirmed.**

Deployment `recs/recommendations` ships a container whose startup step allocates ~200 MiB of memory (`dd if=/dev/zero bs=1M count=200 | tr '\0' x` stored in the shell variable `CACHE`) while the pod template caps the container at `memory: 64Mi`. The cgroup limit is breached during the "catalog cache warm-up" before the serve loop is ever reached, so the kernel OOM-kills the container (exit code 137) every time. The kubelet restarts it, it dies again in the same place, and the pod never becomes Ready — hence the Deployment sits at 0/1 Available and the recommendations panel renders empty storewide. The defect is in the Deployment's pod spec (limit vs. allocation), not in the node or the image.

## Evidence chain

- Paged symptom reproduced in cluster state: `kubectl get all -A` shows `deployment.apps/recommendations` in namespace `recs` as `0/1` READY, `0` AVAILABLE, and its pod `recommendations-85fd7764f4-p9rw8` as `0/1 OOMKilled 2 (14s ago)`.
- Kill reason and exit code are explicit — describe of pod `recommendations-85fd7764f4-p9rw8`:
  - `State: Terminated / Reason: OOMKilled / Exit Code: 137`
  - `Last State: Terminated / Reason: OOMKilled / Exit Code: 137` (i.e. the previous incarnation died identically, not a one-off).
- The memory ceiling comes from the workload spec — describe of pod and describe of deployment both show:
  - `Limits: memory: 64Mi`, `Requests: memory: 32Mi`, `QoS Class: Burstable`.
- The demand that exceeds it is in the command line itself — describe of deployment `recommendations`, container `server`:
  - `CACHE="$(dd if=/dev/zero bs=1M count=200 2>/dev/null | tr '\0' x)"` → ~200 MiB held in a shell variable, roughly 3× the 64Mi limit.
- The kill happens *during* warm-up, before service start — `kubectl logs ... --tail=50` shows only `warming catalog cache`; the subsequent `echo "catalog cache ready"` and the `serving recommendations` loop lines never appear.
- Death is near-instant and repeating — describe of pod: `Started: ...06:55:42` / `Finished: ...06:55:42` (same second), previous incarnation `Started 06:55:28 / Finished 06:55:29`, `Restart Count: 2` in a 25s-old pod.
- The kubelet has entered crash-loop backoff, which is why it stays down rather than self-healing — pod events: `Warning BackOff ... Back-off restarting failed container server in pod recommendations-85fd7764f4-p9rw8_recs`.
- Ownership chain confirming which spec must change: pod `Controlled By: ReplicaSet/recommendations-85fd7764f4`; describe of that ReplicaSet shows `Controlled By: Deployment/recommendations`, and the identical `dd ... count=200` command plus `memory: 64Mi` appear in the Deployment's pod template — so the ReplicaSet and pod are merely faithful copies of the Deployment's spec.
- Deployment-level effect matching the alert text: describe of deployment shows `Available False MinimumReplicasUnavailable`, `1 desired | ... | 0 available | 1 unavailable`.

## Investigation ledger

- **Image pull failure / bad image ref** — ruled out. Pod events show `Normal Pulled ... Container image "busybox:1.36" already present on machine`, plus `Created` and `Started` (x3). The container runs; it does not fail to start.
- **Failing readiness/liveness probe restarting the container** — ruled out. The pod and Deployment specs list no probes at all (no `Liveness:`/`Readiness:` lines in describe output), and the termination reason is `OOMKilled`, not probe-driven. `Ready: False` is a consequence of the container being dead, not of a probe verdict.
- **Node-level memory pressure / eviction, or a noisy neighbour on the node** — ruled out. The pod is `Terminated / OOMKilled` in place with restarts, not `Evicted`/`Failed`; there are no node-pressure events, no `NodeHasMemoryPressure`, and every other pod on `incident-lab-control-plane` (coredns ×2, etcd, apiserver, controller-manager, scheduler, kube-proxy, kindnet, local-path-provisioner) is `1/1 Running` with `0` restarts. This is a cgroup limit breach scoped to one container.
- **Insufficient scheduling capacity / unschedulable pod** — ruled out. `PodScheduled True`, event `Successfully assigned recs/recommendations-85fd7764f4 to incident-lab-control-plane`, and the pod has an IP (`10.244.0.15`). It got a node fine; it dies after starting.
- **Application crash from a config/dependency error (e.g. missing env, unreachable backend)** — ruled out. `Environment: <none>` and no volumes/secrets beyond the default SA token; exit code is 137 (SIGKILL from the OOM killer), not a nonzero application error code, and the log stops exactly at the memory-allocating step.
- **Missing/misconfigured Service causing the empty panel independently of the pod** — considered and set aside as not the paged fault. There is indeed no Service in namespace `recs` in `kubectl get all -A`, but the alert is explicitly `Deployment recs/recommendations has reported 0/1 Ready`, and a Deployment with zero Ready pods would serve nothing regardless. Worth noting as a follow-up hygiene item; it does not explain or affect the 0/1 Ready condition.
- **Bad rollout / regression from a previous revision** — ruled out as a *separate* cause. `deployment.kubernetes.io/revision: 1`, `OldReplicaSets: <none>`, and the only Deployment event is `Scaled up replica set recommendations-85fd7764f4 from 0 to 1`. This workload has never had a healthy revision; the spec was wrong from creation.
- **Truncated/unavailable previous logs hiding a different failure** — noted, not blocking. `kubectl logs --previous` returned `unable to retrieve container logs for containerd://2467...` (that container's log file was already reaped), but the current incarnation's log plus the `OOMKilled` reason on *both* `State` and `Last State` establish the mechanism without it.

## Verification recipe

```bash
# 1. Confirm the kill reason + the limit that caused it, side by side.
kubectl get pod -n recs -l app=recommendations \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[0].lastState.terminated.reason}{"/"}{.status.containerStatuses[0].lastState.terminated.exitCode}{"\t"}{.spec.containers[0].resources.limits.memory}{"\n"}{end}'
# expect: <pod>  OOMKilled/137  64Mi

# 2. Confirm the allocation size baked into the Deployment spec (the thing that must change).
kubectl get deploy recommendations -n recs -o jsonpath='{.spec.template.spec.containers[0].command}{"\n"}{.spec.template.spec.containers[0].resources}{"\n"}'
# expect: ... dd if=/dev/zero bs=1M count=200 ...  and  {"limits":{"memory":"64Mi"},"requests":{"memory":"32Mi"}}

# 3. Prove the fix: raise the ceiling above the warm-up footprint and watch it go Ready.
kubectl set resources deploy/recommendations -n recs --limits=memory=320Mi --requests=memory=256Mi
kubectl rollout status deploy/recommendations -n recs --timeout=90s
kubectl logs -n recs -l app=recommendations --tail=5   # expect "catalog cache ready" then "serving recommendations"
```

**Remediation options (pick one):**
- *Fastest, low risk:* raise the container's memory limit to comfortably exceed the ~200 MiB warm-up plus shell overhead — e.g. `requests: 256Mi`, `limits: 320Mi` — on the Deployment pod template.
- *Better long term:* shrink the warm-up so it fits the intended footprint (reduce `count=200`, or stream/page the catalog cache instead of holding it whole in a shell variable), then keep the tight limit.
- Either way the edit must land on `Deployment recs/recommendations`; patching the pod or ReplicaSet will be reverted by the controllers.
- *Follow-up (not the page):* there is no Service fronting `app=recommendations` in namespace `recs` — verify how the storefront reaches this workload once pods are Ready.

```json
{
  "case_id": "t1-oom-cache-warmup",
  "failing_resource": {"kind": "Deployment", "namespace": "recs", "name": "recommendations"},
  "mechanism": "The Deployment's pod template caps the 'server' container at memory 64Mi, but its startup command allocates roughly 200 MiB of cache (dd if=/dev/zero bs=1M count=200 held in a shell variable). The container breaches its cgroup limit during warm-up and is OOM-killed with exit code 137 before it ever begins serving, so the kubelet crash-loops it and the Deployment never reports a Ready replica, leaving the recommendations panel empty.",
  "verdict": "confirmed"
}
```