## Root cause

Deployment recs/recommendations runs a start-up cache warm-up that allocates about 200Mi ("dd if=/dev/zero bs=1M count=200 | tr '\0' x") into a shell variable, but its pod template caps the container at memory 64Mi. The kernel kills the container during warm-up, so pod recs/recommendations-85fd7764f4-p9rw8 terminates with Reason: OOMKilled / Exit Code: 137 before it ever prints "catalog cache ready", the container never reaches Ready, and the kubelet keeps restarting it into CrashLoopBackOff ("Back-off restarting failed container server"). With no ready replica the Deployment reports 0/1 available (Available False / MinimumReplicasUnavailable) and the storefront's recommendations panel has nothing serving it. The fix is to raise the container's memory limit (and request) above the warm-up working set, not to touch the pod.

Remediation: edit Deployment recs/recommendations, field `spec.template.spec.containers[server].resources.limits.memory`: `64Mi` -> `256Mi (headroom above the ~200Mi cache warm-up allocation; raise requests to match, e.g. 256Mi)`.

## Evidence chain

1. [symptom] The paged Deployment has no available replica.
   source: describe({"kind": "deployment", "name": "recommendations", "namespace": "recs"}) — verified
   > Replicas:               1 desired | 1 updated | 1 total | 0 available | 1 unavailable
2. [symptom] The single pod's container is not ready and was OOMKilled with exit code 137.
   source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
   > State:          Terminated
   >       Reason:       OOMKilled
   >       Exit Code:    137
3. [defect] The pod template caps memory at 64Mi while the start-up command allocates about 200Mi.
   source: describe({"kind": "deployment", "name": "recommendations", "namespace": "recs"}) — verified
   > echo "warming catalog cache"; CACHE="$(dd if=/dev/zero bs=1M count=200 2>/dev/null | tr '\0' x)"; echo "catalog cache ready"; while :; do echo "serving recommendations"; sleep 30; done
   >     Limits:
   >       memory:  64Mi
4. [link] The container dies during warm-up: it logs the warm-up start but never the completion line.
   source: get_logs({"namespace": "recs", "pod": "recommendations-85fd7764f4-p9rw8"}) — verified
   > warming catalog cache
5. [link] The kubelet keeps restarting the container, which never becomes ready.
   source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
   > Warning  BackOff    11s (x2 over 24s)  kubelet            spec.containers{server}: Back-off restarting failed container server in pod recommendations-85fd7764f4-p9rw8_recs

## Investigation ledger

- Node memory pressure / the node being unable to accommodate the pod — ruled out: The node has ~12Gi allocatable memory and is Ready, so the kill came from the container's own cgroup limit, not node-level exhaustion; the pod also scheduled successfully.
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
  >   condition Ready=True
- A namespace LimitRange imposing the 64Mi cap, which would make the LimitRange the object to edit — ruled out: There is no LimitRange in namespace recs, so the 64Mi limit comes from the Deployment's own pod template.
  source: get_object({"kind": "limitranges", "namespace": "recs"}) — verified
  > 0 objects of kind limitranges in namespace recs
- A broken reference (missing ConfigMap key, secret, or volume) preventing start-up — ruled out: The container declares no env, no volumes and no config references; the only mount is the default service account token, so nothing unresolved is involved.
  source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
  > Environment:  <none>
  >     Mounts:
  >       /var/run/secrets/kubernetes.io/serviceaccount from kube-api-access-v784x (ro)

## Verification recipe

1. `kubectl -n recs get deployment recommendations -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}'` — expect to see: memory:  64Mi  [PRESENT]
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
  "mechanism": "Deployment recs/recommendations sets .spec.template.spec.containers[server].resources.limits.memory to 64Mi while the container's start-up command allocates roughly 200Mi (\"dd if=/dev/zero bs=1M count=200 ... | tr '\\0' x\") into the CACHE variable, so the cgroup limit is exceeded during warm-up and the container is terminated with Reason: OOMKilled, Exit Code: 137 after logging only \"warming catalog cache\" and never \"catalog cache ready\". The kubelet restarts it and it is OOMKilled again, giving \"Back-off restarting failed container server\" and Ready: False, so the Deployment stays at 0 available replicas.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
