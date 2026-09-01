## Root cause

The recommendations Deployment's pod template runs a start-up "catalog cache warm-up" that materialises a 200 MiB string (dd if=/dev/zero bs=1M count=200 piped through tr) before it starts serving, but the same template caps the server container at memory limit 64Mi. The container's cgroup limit is exceeded during the warm-up, so the kernel OOM-kills the process each time it starts: the pod's server container is Terminated with Reason OOMKilled and Exit Code 137, has restarted twice, and is Ready=False. With its only replica never reaching Ready, Deployment recs/recommendations reports 0/1 available (Available=False, MinimumReplicasUnavailable), which is why the "You may also like" panel has no backend serving it. The fix is on the Deployment, not the pod: raise the container memory limit above the warm-up footprint (or shrink the warm-up), e.g. limits.memory 256Mi.

Remediation: edit Deployment recs/recommendations, field `spec.template.spec.containers[server].resources.limits.memory`: `64Mi` -> `at least 256Mi (the warm-up allocates a 200Mi cache string; e.g. limits.memory: 256Mi with requests.memory: 256Mi)`.

## Evidence chain

1. [symptom] The paged Deployment has no available replica.
   source: describe({"kind": "deployment", "name": "recommendations", "namespace": "recs"}) — verified
   > Replicas:               1 desired | 1 updated | 1 total | 0 available | 1 unavailable
2. [symptom] The single pod's server container is not ready and is being OOM-killed repeatedly.
   source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
   > Reason:       OOMKilled
   >       Exit Code:    137
3. [defect] The pod template caps memory at 64Mi while the start-up command allocates 200 MiB.
   source: describe({"kind": "deployment", "name": "recommendations", "namespace": "recs"}) — verified
   > echo "warming catalog cache"; CACHE="$(dd if=/dev/zero bs=1M count=200 2>/dev/null | tr '\0' x)"; echo "catalog cache ready"; while :; do echo "serving recommendations"; sleep 30; done
   >     Limits:
   >       memory:  64Mi
4. [link] The container dies within a second of starting, i.e. during the warm-up allocation, and keeps restarting.
   source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
   > Started:      Sat, 29 Aug 2026 06:55:28 +0530
   >       Finished:     Sat, 29 Aug 2026 06:55:29 +0530
   >     Ready:          False
   >     Restart Count:  2
5. [link] The kubelet is backing off restarting the failed server container, keeping the Deployment at 0/1 Ready.
   source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
   > Back-off restarting failed container server in pod recommendations-85fd7764f4-p9rw8_recs

## Investigation ledger

- Node-level memory pressure / eviction rather than a per-container cgroup limit — ruled out: The node has ~12 GiB allocatable memory and is Ready with no memory-pressure condition reported, so the kill came from the 64Mi container limit, not from node exhaustion.
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
  >   condition Ready=True
- Image pull failure or scheduling failure keeping the pod down — ruled out: The pod was scheduled and the image pulled and the container started three times; the only warning is the crash back-off, so admission, scheduling and image resolution all succeeded.
  source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
  > Normal   Pulled     11s (x3 over 25s)  kubelet            spec.containers{server}: Container image "busybox:1.36" already present on machine and can be accessed by the pod
- A missing ConfigMap/Secret/volume reference breaking the container — ruled out: The only ConfigMap in the namespace is the injected kube-root-ca.crt bundle, and the pod template declares no env, no volumes and no mounts, so no external reference can be unresolved.
  source: describe({"kind": "deployment", "name": "recommendations", "namespace": "recs"}) — verified
  > Environment:   <none>
  >     Mounts:        <none>
  >   Volumes:         <none>

## Verification recipe

1. `kubectl -n recs get deploy recommendations -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}{"\n"}{.spec.template.spec.containers[0].command}'` — expect to see: memory:  64Mi  [PRESENT]
2. `kubectl -n recs describe pod recommendations-85fd7764f4-p9rw8 | grep -A3 'Last State'` — expect to see: OOMKilled  [PRESENT]
3. `kubectl -n recs get deploy recommendations` — expect to see: 0 available  [PRESENT]
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
  "mechanism": "Deployment recs/recommendations sets .spec.template.spec.containers[server].resources.limits.memory to 64Mi while the same container's start-up command allocates a 200 MiB cache (\"dd if=/dev/zero bs=1M count=200 ... | tr\"); the allocation exceeds the container cgroup limit and the kernel OOM-kills the server container during warm-up (Reason: OOMKilled, Exit Code: 137), so the container never passes its warm-up phase.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
