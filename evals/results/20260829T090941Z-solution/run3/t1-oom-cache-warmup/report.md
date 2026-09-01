## Root cause

Deployment recs/recommendations runs a startup command that builds a 200 MiB in-memory catalog cache (`dd if=/dev/zero bs=1M count=200 | tr '\0' x`) while its container memory limit is set to 64Mi. The cgroup limit is exceeded during warmup, so the kernel OOM-kills the container before it prints "catalog cache ready" and reaches its serving loop. The pod recs/recommendations-85fd7764f4-p9rw8 therefore reports State Terminated / Reason OOMKilled / Exit Code 137 and Ready: False, kubelet CrashLoopBackOffs it ("Back-off restarting failed container server"), and the Deployment stays at 0/1 available with condition Available=False MinimumReplicasUnavailable — which is what the workload health monitor paged on and why the "You may also like" panel has no backend serving it.

Remediation: edit Deployment recs/recommendations, field `.spec.template.spec.containers[server].resources.limits.memory`: `64Mi` -> `256Mi (at least above the ~200Mi the warmup allocates; raise requests to match, e.g. 256Mi)`.

## Evidence chain

1. [symptom] The paged Deployment has no available replica.
   source: describe({"kind": "deployment", "name": "recommendations", "namespace": "recs"}) — verified
   > Replicas:               1 desired | 1 updated | 1 total | 0 available | 1 unavailable
2. [symptom] The single pod's container is not ready and was OOM-killed twice.
   source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
   > Reason:       OOMKilled
   >       Exit Code:    137
3. [defect] The pod template asks for a 200 MiB allocation but caps memory at 64Mi.
   source: describe({"kind": "deployment", "name": "recommendations", "namespace": "recs"}) — verified
   > CACHE="$(dd if=/dev/zero bs=1M count=200 2>/dev/null | tr '\0' x)"
4. [defect] The container memory limit in the Deployment template is 64Mi.
   source: describe({"kind": "deployment", "name": "recommendations", "namespace": "recs"}) — verified
   > Limits:
   >       memory:  64Mi
5. [link] The container dies during cache warmup, before reaching the serving loop.
   source: get_logs({"namespace": "recs", "pod": "recommendations-85fd7764f4-p9rw8"}) — verified
   > warming catalog cache
6. [link] Kubelet keeps restarting the OOM-killed container, so the replica never becomes ready.
   source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
   > Back-off restarting failed container server in pod recommendations-85fd7764f4-p9rw8_recs

## Investigation ledger

- Node memory exhaustion / node pressure evicting the pod — ruled out: The node has ~12 GiB allocatable memory and is Ready, so the kill came from the container's own cgroup limit, not node pressure.
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
- A namespace LimitRange injecting or capping the memory limit — ruled out: No LimitRange exists in namespace recs, so the 64Mi limit comes from the Deployment's own template.
  source: get_object({"kind": "limitranges", "namespace": "recs"}) — verified
  > 0 objects of kind limitranges in namespace recs
- A ResourceQuota blocking or constraining the workload — ruled out: No ResourceQuota exists in namespace recs.
  source: get_object({"kind": "resourcequotas", "namespace": "recs"}) — verified
  > 0 objects of kind resourcequotas in namespace recs
- A missing ConfigMap/Secret/volume reference preventing startup — ruled out: The container declares no env, no mounts and no volumes beyond the default service account token, so no external reference can be failing to resolve.
  source: describe({"kind": "deployment", "name": "recommendations", "namespace": "recs"}) — verified
  > Environment:   <none>
  >     Mounts:        <none>
  >   Volumes:         <none>

## Verification recipe

1. `kubectl -n recs get deploy recommendations -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}{"\n"}{.spec.template.spec.containers[0].command}'` — expect to see: memory:  64Mi  [PRESENT]
2. `kubectl -n recs describe pod recommendations-85fd7764f4-p9rw8 | grep -A3 'Last State'` — expect to see: OOMKilled  [PRESENT]
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
  "mechanism": "Deployment recs/recommendations sets .spec.template.spec.containers[server].resources.limits.memory to 64Mi while the same template's command allocates a ~200Mi cache (`dd if=/dev/zero bs=1M count=200 2>/dev/null | tr '\\0' x`), so the container exceeds its cgroup limit during warmup and is killed with Reason: OOMKilled, Exit Code: 137 after logging only \"warming catalog cache\". Kubelet restarts it into \"Back-off restarting failed container server\", so the replica never passes Ready and the Deployment holds at \"0 available | 1 unavailable\".",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
