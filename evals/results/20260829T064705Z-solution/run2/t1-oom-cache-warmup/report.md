## Root cause

The recommendations Deployment's own pod template is misconfigured: its container command warms a catalog cache by materialising a 200 MiB string (dd if=/dev/zero bs=1M count=200 ... tr), while the same template caps the container at resources.limits.memory: 64Mi. The cgroup limit is hit during cache warm-up, so the kernel OOM-kills the container at exit code 137 before it ever prints "catalog cache ready" or enters its serving loop. The pod therefore never reaches Ready (2 restarts, CrashLoop back-off), the Deployment stays 0/1 available, and the "You may also like" panel has no backend serving recommendations.

Remediation: edit Deployment recs/recommendations, field `spec.template.spec.containers[server].resources.limits.memory`: `64Mi` -> `256Mi (at least, to hold the ~200MiB cache the startup command allocates; raise requests.memory to match, e.g. 256Mi)`.

## Evidence chain

1. [symptom] The paged Deployment has no available replica and its only pod is not ready after repeated OOM kills.
   source: namespace_overview(recs) — verified
   > deployment/recommendations ready=0/1
2. [symptom] The pod's server container is terminated with reason OOMKilled and exit code 137, and has restarted twice.
   source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
   > Reason:       OOMKilled
   >       Exit Code:    137
3. [defect] The Deployment pod template caps memory at 64Mi while the same container command allocates a 200 MiB cache buffer.
   source: describe({"kind": "deployment", "name": "recommendations", "namespace": "recs"}) — verified
   > CACHE="$(dd if=/dev/zero bs=1M count=200 2>/dev/null | tr '\0' x)"
4. [defect] The memory limit written in the Deployment pod template is 64Mi.
   source: describe({"kind": "deployment", "name": "recommendations", "namespace": "recs"}) — verified
   > Limits:
   >       memory:  64Mi
5. [link] The container dies during cache warm-up: it logs the warm-up start but never logs completion of the cache.
   source: get_logs({"namespace": "recs", "pod": "recommendations-85fd7764f4-p9rw8"}) — verified
   > warming catalog cache
6. [link] Kubelet is restarting the container in back-off because it keeps failing, so the Deployment never gains a ready endpoint.
   source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
   > Back-off restarting failed container server in pod recommendations-85fd7764f4-p9rw8_recs

## Investigation ledger

- A LimitRange in the namespace injected the too-small 64Mi limit, meaning the namespace policy and not the Deployment would be the object to edit. — ruled out: There are no LimitRange objects in namespace recs, so the 64Mi limit comes from the Deployment's own pod template.
  source: get_object({"kind": "limitranges", "namespace": "recs"}) — verified
  > 0 objects of kind limitranges in namespace recs
- Node memory pressure / eviction: the node was out of memory and killed the pod. — ruled out: The node has ~12Gi allocatable memory and is Ready, so a 200 MiB allocation is not a node-level shortage; the kill is from the container's own 64Mi cgroup limit.
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
  >   condition Ready=True
- A broken image pull or missing referenced ConfigMap/Secret prevented the container from running. — ruled out: The image pulled fine and the container was created and started three times; the only namespace ConfigMap is the standard CA bundle and the template mounts no config.
  source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
  > Container image "busybox:1.36" already present on machine and can be accessed by the pod

## Verification recipe

1. `kubectl -n recs get deployment recommendations -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}'` — expect to see: memory:  64Mi  [PRESENT]
2. `kubectl -n recs describe pod recommendations-85fd7764f4-p9rw8 | grep -A2 'Last State'` — expect to see: OOMKilled  [PRESENT]
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
  "mechanism": "Deployment recs/recommendations sets .spec.template.spec.containers[server].resources.limits.memory to 64Mi, but the same container's startup command allocates a ~200 MiB cache buffer (\"dd if=/dev/zero bs=1M count=200 | tr '\\0' x\"); the container exceeds its memory cgroup during cache warm-up and is OOMKilled with exit code 137 before the warm-up completes. The limit must be at least the working-set of the warm-up (~256Mi).",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
