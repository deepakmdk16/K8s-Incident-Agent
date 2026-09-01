## Root cause

The Deployment recs/recommendations ships a pod template whose container "server" warms a catalog cache by allocating a 200 MiB string (dd if=/dev/zero bs=1M count=200 piped into a shell variable) while spec.template.spec.containers[server].resources.limits.memory is set to 64Mi. The container's working set exceeds its cgroup memory limit during that warmup, so the kernel OOM-kills it with exit code 137 before it ever prints "catalog cache ready". The kubelet restarts it and it is killed again (restart count 2, now in CrashLoopBackOff), so the container never reaches Ready, the Deployment stays 0/1 available with Available=False MinimumReplicasUnavailable, and no recommendations backend serves the panel. Nothing else in the namespace is implicated: the only ConfigMap present is the default kube-root-ca.crt, the pod is scheduled and the node has ample free memory. Fix: raise the memory limit in the Deployment's pod template above the warmup footprint (e.g. limits.memory 256Mi, requests.memory 128Mi), or shrink the cache warmup to fit 64Mi.

Remediation: edit Deployment recs/recommendations, field `spec.template.spec.containers[server].resources.limits.memory`: `64Mi` -> `256Mi (any limit above the ~200Mi the cache-warmup step allocates, with requests raised accordingly)`.

## Evidence chain

1. [symptom] The paged Deployment has no available replica.
   source: describe({"kind": "deployment", "name": "recommendations", "namespace": "recs"}) — verified
   > Replicas:               1 desired | 1 updated | 1 total | 0 available | 1 unavailable
2. [symptom] The single pod's container is not ready and was OOMKilled repeatedly.
   source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
   > Reason:       OOMKilled
   >       Exit Code:    137
3. [defect] The Deployment pod template sets a 64Mi memory limit while the container command allocates 200 MiB.
   source: describe({"kind": "deployment", "name": "recommendations", "namespace": "recs"}) — verified
   > echo "warming catalog cache"; CACHE="$(dd if=/dev/zero bs=1M count=200 2>/dev/null | tr '\0' x)"; echo "catalog cache ready"; while :; do echo "serving recommendations"; sleep 30; done
   >     Limits:
   >       memory:  64Mi
4. [link] The container dies during the cache warmup allocation: it logs the warmup start and never logs completion.
   source: get_logs({"namespace": "recs", "pod": "recommendations-85fd7764f4-p9rw8"}) — verified
   > warming catalog cache
5. [link] Kubelet keeps restarting the container, which is why the Deployment stays 0/1.
   source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
   > Warning  BackOff    11s (x2 over 24s)  kubelet            spec.containers{server}: Back-off restarting failed container server in pod recommendations-85fd7764f4-p9rw8_recs

## Investigation ledger

- Node memory pressure / eviction on the node, rather than a per-container limit, killing the pod — ruled out: The single node is Ready with ~12 GiB allocatable memory, far more than the 200 MiB warmup needs, so the kill is a container cgroup limit, not node exhaustion.
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
- A missing or misreferenced ConfigMap/Secret/volume in the pod template — ruled out: The pod template declares no environment variables and no volumes beyond the default service-account projection, and the namespace's only ConfigMap is the default kube-root-ca.crt, so no config reference can be failing.
  source: describe({"kind": "deployment", "name": "recommendations", "namespace": "recs"}) — verified
  > Environment:   <none>
  >     Mounts:        <none>
  >   Volumes:         <none>
- Scheduling or image-pull failure preventing the container from starting — ruled out: The pod was scheduled and the image pulled and the container started three times; it starts fine and then dies, so admission, scheduling and image pull are not the fault.
  source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
  > Normal   Pulled     11s (x3 over 25s)  kubelet            spec.containers{server}: Container image "busybox:1.36" already present on machine and can be accessed by the pod

## Verification recipe

1. `kubectl -n recs get deployment recommendations -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}{"\n"}{.spec.template.spec.containers[0].command}'` — expect to see: memory:  64Mi  [PRESENT]
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
  "mechanism": "In the Deployment recs/recommendations, spec.template.spec.containers[server].resources.limits.memory is 64Mi while the container's own startup command allocates a ~200 MiB cache (\"dd if=/dev/zero bs=1M count=200\"), so the container exceeds its cgroup limit mid-warmup and is OOMKilled with exit code 137 before printing \"catalog cache ready\". The kubelet restarts and re-kills it (restart count 2, BackOff), so the Deployment's single replica never becomes Ready and it reports Available=False / MinimumReplicasUnavailable.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
