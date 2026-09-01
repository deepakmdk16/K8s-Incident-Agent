## Root cause

The Deployment recs/recommendations sets a memory limit of 64Mi on its container "server", but that container's own startup command builds a 200 MiB in-memory catalog cache (dd if=/dev/zero bs=1M count=200 piped into a shell variable) before it starts serving. The allocation exceeds the cgroup limit, so the kernel OOM-kills the container during warm-up: the pod recommendations-85fd7764f4-p9rw8 shows State Terminated/Reason OOMKilled with exit code 137, restart count 2, and its log stops at "warming catalog cache" without ever reaching "catalog cache ready". The container therefore never becomes Ready, the Deployment stays 0/1 available (Available=False, MinimumReplicasUnavailable), and the recommendations panel has no backend. Nothing external constrains the limit — the recs namespace has no LimitRange and no ResourceQuota, and the node has ~12 GiB allocatable memory — so the wrong value is in the Deployment's own pod template and that is the object a human must edit.

Remediation: edit Deployment recs/recommendations, field `.spec.template.spec.containers[server].resources.limits.memory`: `64Mi` -> `256Mi (headroom above the ~200Mi cache-warmup allocation; raise requests accordingly, e.g. 256Mi)`.

## Evidence chain

1. [symptom] The paged Deployment reports 0/1 ready with an OOMKilled container.
   source: namespace_overview(recs) — verified
   > deployment/recommendations ready=0/1
2. [symptom] Deployment status shows no available replicas.
   source: describe({"kind": "deployment", "name": "recommendations", "namespace": "recs"}) — verified
   > Replicas:               1 desired | 1 updated | 1 total | 0 available | 1 unavailable
3. [defect] The pod template sets a 64Mi memory limit.
   source: describe({"kind": "deployment", "name": "recommendations", "namespace": "recs"}) — verified
   > Limits:
   >       memory:  64Mi
4. [defect] The same container's command allocates roughly 200 MiB into a shell variable during cache warm-up.
   source: describe({"kind": "deployment", "name": "recommendations", "namespace": "recs"}) — verified
   > echo "warming catalog cache"; CACHE="$(dd if=/dev/zero bs=1M count=200 2>/dev/null | tr '\0' x)"; echo "catalog cache ready";
5. [link] The container is killed by the OOM killer with exit code 137 and restarts.
   source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
   > State:          Terminated
   >       Reason:       OOMKilled
   >       Exit Code:    137
6. [link] The container dies during warm-up: the log stops after the warming message and never prints the cache-ready message.
   source: get_logs({"namespace": "recs", "pod": "recommendations-85fd7764f4-p9rw8"}) — verified
   > warming catalog cache
7. [link] Repeated kills put the container into restart back-off, keeping the pod not Ready.
   source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
   > Warning  BackOff    11s (x2 over 24s)  kubelet            spec.containers{server}: Back-off restarting failed container server in pod recommendations-85fd7764f4-p9rw8_recs

## Investigation ledger

- A namespace LimitRange or ResourceQuota imposed the 64Mi cap, meaning the policy object rather than the Deployment would need editing. — ruled out: The recs namespace contains no LimitRange and no ResourceQuota, so the 64Mi limit comes only from the Deployment's own pod template.
  source: get_object({"kind": "limitranges", "namespace": "recs"}) — verified
  > 0 objects of kind limitranges in namespace recs
- Node memory pressure or insufficient cluster capacity caused the kill. — ruled out: The single node is Ready with about 12 GiB allocatable memory, far more than the ~200 MiB the container needs, so this is a per-container cgroup limit kill, not node exhaustion.
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
  >   condition Ready=True
- A missing or misnamed ConfigMap/Secret/volume reference prevented the container from starting. — ruled out: The pod mounts only the default projected service-account token and the only ConfigMap in the namespace is kube-root-ca.crt; the container starts successfully and executes its command before being killed.
  source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
  > Mounts:
  >       /var/run/secrets/kubernetes.io/serviceaccount from kube-api-access-v784x (ro)
- An image pull failure kept the pod from running. — ruled out: The image was already present and the container was created and started three times.
  source: describe({"kind": "pod", "name": "recommendations-85fd7764f4-p9rw8", "namespace": "recs"}) — verified
  > Normal   Pulled     11s (x3 over 25s)  kubelet            spec.containers{server}: Container image "busybox:1.36" already present on machine and can be accessed by the pod

## Verification recipe

1. `kubectl -n recs get deployment recommendations -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}'` — expect to see: memory:  64Mi  [PRESENT]
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
  "mechanism": "The Deployment recs/recommendations declares .spec.template.spec.containers[server].resources.limits.memory = 64Mi while the same container's command allocates a ~200 MiB cache (\"dd if=/dev/zero bs=1M count=200\") at startup; the allocation breaches the cgroup limit and the container is OOMKilled (exit code 137) during warm-up, so the container never passes its cache-warming step, is repeatedly restarted into CrashLoopBackOff, and the Deployment reports 0/1 available. The limit must be at least the ~200 MiB the warm-up needs (e.g. 256Mi).",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
