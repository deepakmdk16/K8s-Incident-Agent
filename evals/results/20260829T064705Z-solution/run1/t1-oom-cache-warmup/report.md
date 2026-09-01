## Root cause

The recommendations Deployment's own pod template asks its container to build a 200 MiB in-memory catalog cache at startup (`dd if=/dev/zero bs=1M count=200 ... CACHE=...`) while capping the container at a 64Mi memory limit. The cgroup limit is hit part-way through the allocation, so the kernel OOM-kills the process during warm-up: the container logs stop at "warming catalog cache" and never reach "catalog cache ready". The kubelet restarts it, the same allocation happens again, and the container is OOMKilled every time (restart count 2, exit code 137, now in CrashLoopBackOff), so the pod never becomes Ready and the Deployment stays 0/1 available — which is why the "You may also like" panel is empty. Nothing else it references is broken: the namespace holds only the default service account and the auto-injected kube-root-ca.crt ConfigMap, and the node has ample free memory. The fix is to raise the container's memory limit (and request) above the cache size, e.g. limits.memory 256Mi, or shrink the warm-up buffer to fit 64Mi.

Remediation: edit Deployment recs/recommendations, field `spec.template.spec.containers[server].resources.limits.memory`: `64Mi` -> `256Mi (at least enough to hold the ~200Mi cache buffer the startup command allocates; raise requests accordingly, e.g. 256Mi)`.

## Evidence chain

1. [symptom] The paged Deployment reports no available replicas.
   source: describe({"kind": "deployment", "name": "recommendations", "namespace": "recs"}) — verified
   > Replicas:               1 desired | 1 updated | 1 total | 0 available | 1 unavailable
2. [symptom] Its only pod is not ready and has been OOMKilled repeatedly.
   source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
   > Reason:       OOMKilled
   >       Exit Code:    137
3. [defect] The Deployment pod template allocates a 200 MiB cache while limiting the container to 64Mi of memory.
   source: describe({"kind": "deployment", "name": "recommendations", "namespace": "recs"}) — verified
   > CACHE="$(dd if=/dev/zero bs=1M count=200 2>/dev/null | tr '\0' x)"
4. [defect] The memory limit in the Deployment pod template is 64Mi.
   source: describe({"kind": "deployment", "name": "recommendations", "namespace": "recs"}) — verified
   > Limits:
   >       memory:  64Mi
5. [link] The container is killed during the cache warm-up: the log stops after the warm-up banner and never prints the completion line.
   source: get_logs({"namespace": "recs", "pod": "recommendations-85fd7764f4-p9rw8"}) — verified
   > warming catalog cache
6. [link] The kubelet keeps restarting the container after each OOM kill, leaving it in back-off and never ready.
   source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
   > Warning  BackOff    11s (x2 over 24s)  kubelet            spec.containers{server}: Back-off restarting failed container server in pod recommendations-85fd7764f4-p9rw8_recs

## Investigation ledger

- Node-level memory pressure or eviction caused the kill rather than the container's own limit. — ruled out: The node has ~12 GiB allocatable memory and is Ready with no pressure taint, so the kill came from the container's 64Mi cgroup limit, not the node.
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
  >   condition Ready=True
- A missing or misnamed ConfigMap/Secret/volume reference kept the container from starting. — ruled out: The only ConfigMap in the namespace is the auto-injected CA bundle, and the pod template mounts no volumes and reads no config keys, so no reference fails to resolve.
  source: get_object({"kind": "configmaps", "namespace": "recs"}) — verified
  > "name": "kube-root-ca.crt"
- A failing readiness probe or a Service selector mismatch left the endpoint empty. — ruled out: The pod template defines no probes and no ports, and the container is Terminated/OOMKilled rather than running-but-unready; the namespace contains no Service at all.
  source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
  > Port:          <none>
  >     Host Port:     <none>

## Verification recipe

1. `kubectl -n recs describe pod recommendations-85fd7764f4-p9rw8` — expect to see: OOMKilled  [PRESENT]
2. `kubectl -n recs get deployment recommendations -o yaml` — expect to see: memory:  64Mi  [PRESENT]
3. `kubectl -n recs logs recommendations-85fd7764f4-p9rw8` — expect to see: warming catalog cache  [PRESENT]
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
  "mechanism": "Deployment recs/recommendations sets .spec.template.spec.containers[server].resources.limits.memory to 64Mi, but that same container's startup command allocates a ~200Mi cache buffer (`dd if=/dev/zero bs=1M count=200`), so the container exceeds its cgroup limit during warm-up and is OOMKilled with exit code 137 before it finishes. The limit needs to be at least the size of the cache (e.g. 256Mi) rather than 64Mi.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
