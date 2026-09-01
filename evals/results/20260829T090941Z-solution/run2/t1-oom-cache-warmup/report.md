## Root cause

Deployment recs/recommendations runs a startup \"catalog cache warmup\" that materialises a 200 MiB string in the shell (dd if=/dev/zero bs=1M count=200 piped through tr into the CACHE variable) while its container memory limit is set to 64Mi. The allocation exceeds the cgroup limit, so the kernel OOM-kills the container within a second of every start: the server container reports Reason: OOMKilled, Exit Code: 137 with Restart Count: 2, and its log stops at \"warming catalog cache\" without ever reaching \"catalog cache ready\" or the \"serving recommendations\" loop. Because the container never gets past warmup, the pod never becomes Ready, the Deployment stays 0/1, and nothing serves the \"You may also like\" panel. The node is not short of memory (12220316Ki allocatable, Ready=True), so the defect is purely the limit written in the pod template.

Remediation: edit Deployment recs/recommendations, field `spec.template.spec.containers[server].resources.limits.memory`: `64Mi` -> `256Mi (covers the 200 MiB warmup cache plus shell overhead; raise requests.memory to match, e.g. 256Mi)`.

## Evidence chain

1. [symptom] The paged Deployment has no ready replicas.
   source: namespace_overview(recs) — verified
   > deployment/recommendations ready=0/1 podLabels={app=recommendations}
2. [symptom] The pod's server container is not ready and last exited OOMKilled after restarts.
   source: namespace_overview({"namespace": "recs"}) — verified
   > server(ready=False,restarts=2,lastExit=OOMKilled)
3. [defect] The container's startup command allocates a 200 MiB cache.
   source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
   > dd if=/dev/zero bs=1M count=200
4. [defect] The memory limit applied to that container is 64Mi.
   source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
   > Limits:
   >       memory:  64Mi
5. [link] The container is terminated by the OOM killer with exit code 137 immediately after start.
   source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
   > Reason:       OOMKilled
   >       Exit Code:    137
6. [link] The kubelet is in a restart back-off loop for this container.
   source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
   > Back-off restarting failed container server in pod recommendations-85fd7764f4-p9rw8_recs
7. [link] The container dies during warmup: the log stops after the warmup message and never reaches the serving loop.
   source: get_logs({"namespace": "recs", "pod": "recommendations-85fd7764f4-p9rw8"}) — verified
   > warming catalog cache

## Investigation ledger

- Node memory pressure or eviction on the node hosting the pod — ruled out: The node is Ready with ~12 GiB allocatable memory, so the kill came from the container's own cgroup limit, not node-level pressure.
  source: cluster_capacity({}) — verified
  > condition Ready=True
- A missing or misnamed ConfigMap / mount reference breaking startup — ruled out: The only ConfigMap in the namespace is the default CA bundle and the pod template references no configMap, secret or PVC; the sole mount is the service account token, which resolved.
  source: get_object({"kind": "configmaps", "namespace": "recs"}) — verified
  > "name": "kube-root-ca.crt",
- Scheduling, image pull or admission failure preventing the pod from running — ruled out: The pod was scheduled, the image was already present locally and the container was created and started three times, so startup itself succeeds and the process is then killed.
  source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
  > already present on machine and can be accessed by the pod

## Verification recipe

1. `kubectl -n recs get deployment recommendations -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}'` — expect to see: 64Mi  [PRESENT]
2. `kubectl -n recs describe pod recommendations-85fd7764f4-p9rw8` — expect to see: OOMKilled  [PRESENT]
3. `kubectl -n recs logs recommendations-85fd7764f4-p9rw8 -c server` — expect to see: warming catalog cache  [PRESENT]
(Each command was re-executed against the captured cluster state; PRESENT/ABSENT is the measured result, not a prediction.)

Verdict: confirmed. Every quote below was re-checked against the tool output it cites, a defect observation names the failing object, an alternative was ruled out with evidence, and every verification command was re-run and found present.

```json
{
  "case_id": "t1-oom-cache-warmup",
  "failing_resource": {
    "kind": "Deployment",
    "namespace": "recs",
    "name": "recommendations"
  },
  "mechanism": "Deployment recs/recommendations sets .spec.template.spec.containers[server].resources.limits.memory to 64Mi while the same container's startup command allocates a 200 MiB cache (\"dd if=/dev/zero bs=1M count=200 ... tr\"); the allocation crosses the cgroup limit, so the kernel kills the container during warmup with Reason: OOMKilled, Exit Code: 137, and the kubelet restarts and re-kills it in a BackOff loop, leaving the container Ready: False and the Deployment at 0/1.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
