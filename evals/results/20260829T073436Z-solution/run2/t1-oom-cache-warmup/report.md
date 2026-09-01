## Root cause

The Deployment recs/recommendations runs a container whose startup command builds a 200 MiB in-memory catalog cache (dd if=/dev/zero bs=1M count=200 piped into a shell variable), but its pod template caps the container at spec.template.spec.containers[server].resources.limits.memory = 64Mi. The cgroup limit is exceeded during warmup, so the kernel OOM-kills the process at exit code 137 before it ever prints "catalog cache ready" or enters its serving loop. The kubelet restarts it, it is OOM-killed again, and the container never reaches Ready, so the Deployment stays at 0/1 available and the recommendations panel has no backend. The fix is on the Deployment pod template, not on the pod: raise the memory limit (and request) above the ~200 MiB the warmup actually needs, e.g. limits.memory 256Mi.

Remediation: edit Deployment recs/recommendations, field `spec.template.spec.containers[server].resources.limits.memory`: `64Mi` -> `256Mi (any limit above the ~200Mi the cache-warmup step allocates; raise requests to match, e.g. 256Mi)`.

## Evidence chain

1. [symptom] The paged Deployment reports no available replicas.
   source: describe({"kind": "deployment", "name": "recommendations", "namespace": "recs"}) — verified
   > Replicas:               1 desired | 1 updated | 1 total | 0 available | 1 unavailable
2. [symptom] The single pod's container is not ready and has been OOMKilled repeatedly.
   source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
   > State:          Terminated
   >       Reason:       OOMKilled
   >       Exit Code:    137
3. [defect] The Deployment pod template allocates 200 MiB in the warmup command but caps container memory at 64Mi.
   source: describe({"kind": "deployment", "name": "recommendations", "namespace": "recs"}) — verified
   > echo "warming catalog cache"; CACHE="$(dd if=/dev/zero bs=1M count=200 2>/dev/null | tr '\0' x)"; echo "catalog cache ready"; while :; do echo "serving recommendations"; sleep 30; done
   >     Limits:
   >       memory:  64Mi
4. [link] The container dies during cache warmup, before it reaches the serving loop: only the first log line is ever emitted.
   source: get_logs({"namespace": "recs", "pod": "recommendations-85fd7764f4-p9rw8"}) — verified
   > warming catalog cache
5. [link] The kubelet keeps restarting the container, which is killed again each time.
   source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
   > Warning  BackOff    11s (x2 over 24s)  kubelet            spec.containers{server}: Back-off restarting failed container server in pod recommendations-85fd7764f4-p9rw8_recs

## Investigation ledger

- Node-level memory pressure or eviction caused the kill rather than the container's own limit — ruled out: The node has ~12 GiB allocatable memory and is Ready, and the pod was terminated with per-container OOMKilled, not Evicted.
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
  >   condition Ready=True
- A missing or wrong ConfigMap reference (e.g. cache sizing config) broke startup — ruled out: The container declares no environment or volume mounts other than the API-access token, and the only ConfigMap in the namespace is the standard CA bundle, so no config reference is involved.
  source: describe({"kind": "deployment", "name": "recommendations", "namespace": "recs"}) — verified
  > Environment:   <none>
  >     Mounts:        <none>
  >   Volumes:         <none>
- Image pull failure or scheduling failure kept the pod from running — ruled out: The image was present and the container was created and started three times; it was scheduled successfully.
  source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
  > Normal   Pulled     11s (x3 over 25s)  kubelet            spec.containers{server}: Container image "busybox:1.36" already present on machine and can be accessed by the pod

## Verification recipe

1. `kubectl -n recs get deploy recommendations -o jsonpath='{.spec.template.spec.containers[0].resources}{"\n"}{.spec.template.spec.containers[0].command}'` — expect to see: memory:  64Mi  [PRESENT]
2. `kubectl -n recs describe pod recommendations-85fd7764f4-p9rw8 | grep -A3 'State:'` — expect to see: OOMKilled  [PRESENT]
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
  "mechanism": "The Deployment recs/recommendations sets .spec.template.spec.containers[server].resources.limits.memory to 64Mi while that same container's startup command allocates a 200 MiB cache string (\"dd if=/dev/zero bs=1M count=200\"); the container's cgroup limit is breached during warmup, so the container is OOMKilled with exit code 137 after logging only \"warming catalog cache\", and the kubelet's restart/BackOff cycle repeats the kill, leaving the Deployment at 0/1 available.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
