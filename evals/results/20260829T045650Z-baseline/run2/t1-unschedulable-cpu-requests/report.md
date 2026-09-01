## Root cause

**Confirmed.** The Deployment `fraud/fraud-scoring` requests `cpu: 512` for its `scorer` container. In Kubernetes resource notation `512` means 512 **cores** (the author almost certainly meant `512m` = 0.512 core). No node in this single-node kind cluster has anywhere near 512 allocatable CPUs, so the scheduler can never place the pod, it stays `Pending` forever, the ReplicaSet reports 0 ready, and the Deployment stays `0/1 available` — hence zero fraud scores.

The failing resource is the Deployment (its pod template spec must change); the `Pending` pod is only the symptom-carrier, and deleting/recreating it will reproduce the same failure indefinitely.

## Evidence chain

- **Symptom, `kubectl get all -A`:** `fraud pod/fraud-scoring-596445859d-thcdh 0/1 Pending 0 ... <none> <none>` — no node assigned, so the container never started.
- **Same output, workload level:** `deployment.apps/fraud-scoring 0/1 1 0` (READY 0/1, AVAILABLE 0) and `replicaset.apps/fraud-scoring-596445859d 1 1 0` (DESIRED 1, READY 0) — the unavailability propagates from the unscheduled pod.
- **Direct causal event, `describe pod/fraud-scoring-596445859d-thcdh`:**
  `Warning FailedScheduling ... 0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 Preemption is not helpful for scheduling.`
  The scheduler names CPU as the exact unsatisfiable resource, and states preemption cannot help (i.e. even evicting everything on the node would not free enough CPU — consistent with a request far exceeding node capacity, not mere contention).
- **The offending value, `describe pod`:**
  ```
  Requests:
    cpu:        512
  ```
  No unit suffix ⇒ 512 whole cores.
- **The value originates in the workload spec, not a mutation, `describe deployment.apps/fraud-scoring`:** the Pod Template shows the identical `Requests: cpu: 512`, and `describe replicaset.apps/fraud-scoring-596445859d` shows the same — so the bad request is baked into the Deployment's pod template and will be re-applied to every replacement pod.
- **Cluster is single-node and small, `kubectl get all -A`:** every running pod (`etcd`, `kube-apiserver`, `coredns`, `kindnet`, `kube-proxy`, `local-path-provisioner`) is on `incident-lab-control-plane`, and both DaemonSets show `DESIRED 1`. One kind node has on the order of single-digit CPUs — orders of magnitude below 512.
- **No competing pressure:** the only non-system workload in the entire cluster is `fraud-scoring` itself, so the "Insufficient cpu" is not caused by neighbours hogging the node.
- **Deployment conditions, `describe deployment`:** `Available False MinimumReplicasUnavailable` / `Progressing True ReplicaSetUpdated` — the rollout is stuck on replica availability, not on an image or config-object failure.
- **Note on ages:** the pod/RS/Deployment all show `AGE 0s` and the events are `1s`/`2s` old, while the page says 20+ minutes. This is consistent with the objects having been (re)created/observed just before capture; it does not change the mechanism, since the scheduling verdict is deterministic given the template.

## Investigation ledger

- **Image pull failure (bad tag / private registry) —** ruled out: status is `Pending` with `PodScheduled False`, not `ImagePullBackOff`/`ErrImagePull`, and there are no pull events. The image `busybox:1.36` is public and never even attempted, because scheduling precedes pulling.
- **CrashLoopBackOff / bad command —** ruled out: `RESTARTS 0`, and both `kubectl logs` (current and `--previous`) returned empty because no container has ever run on any node. The command itself (`echo` + `while true; sleep 10`) is a valid long-running loop anyway.
- **Missing ConfigMap/Secret/PVC blocking startup —** ruled out: the only volume is the default `kube-api-access-l7gvx` projected token with `ConfigMapName: kube-root-ca.crt`, and there are no `FailedMount`/`FailedAttachVolume` events; the sole event is `FailedScheduling`.
- **Taint / nodeSelector / affinity mismatch —** ruled out: `Node-Selectors: <none>`, no affinity block, and the scheduler message says `Insufficient cpu`, not `node(s) had untolerated taint`. The pod carries only the two default not-ready/unreachable tolerations.
- **Node NotReady or control plane down —** ruled out: all `kube-system` pods (`etcd`, `kube-apiserver`, `kube-scheduler`, `kube-controller-manager`, `kube-proxy`, `kindnet`) are `1/1 Running` with 0 restarts, DaemonSets show `1/1 ready`, and the scheduler is actively emitting events — so it is alive and deliberately refusing to place this pod.
- **Memory or ephemeral-storage pressure —** ruled out: only `cpu` appears under `Requests` (no memory request at all, QoS `Burstable`), and the scheduler explicitly names `Insufficient cpu`.
- **Genuine capacity exhaustion by other tenants (i.e. cluster needs a new node rather than a spec fix) —** ruled out as the primary cause: the cluster contains no other application workloads, and `Preemption is not helpful` indicates the request exceeds total node capacity rather than currently-allocated capacity. Adding a normal-sized node would still not satisfy a 512-core request.
- **ResourceQuota/LimitRange defaulting injecting the value —** considered; not supported: the same `cpu: 512` is present verbatim in the Deployment's own pod template and the ReplicaSet template, and no quota/limitrange objects appear anywhere in the cluster dump.

## Verification recipe

```bash
# 1. See the unit-less (whole-core) request as stored in the Deployment spec
kubectl get deployment fraud-scoring -n fraud \
  -o jsonpath='{.spec.template.spec.containers[0].resources}{"\n"}'
# expect: {"requests":{"cpu":"512"}}

# 2. Compare against what the single node can actually offer
kubectl get nodes -o custom-columns=\
NAME:.metadata.name,CPU_CAPACITY:.status.capacity.cpu,CPU_ALLOCATABLE:.status.allocatable.cpu
# expect: a handful of cores, i.e. << 512

# 3. Confirm the scheduler's own verdict on the pending pod
kubectl get events -n fraud --field-selector reason=FailedScheduling \
  -o custom-columns=OBJ:.involvedObject.name,MSG:.message
# expect: "0/1 nodes are available: 1 Insufficient cpu..."
```

**Remediation:** patch the Deployment's pod template to a sane request (add the milli-core suffix), e.g.

```bash
kubectl set resources deployment/fraud-scoring -n fraud \
  -c scorer --requests=cpu=512m
# optionally also set a limit, e.g. --limits=cpu=1
```

The bad ReplicaSet is replaced on the new revision and the pod schedules immediately. Follow-up: add a CI policy check (or a `LimitRange` with a max CPU request in the `fraud` namespace) to reject unit-less multi-core CPU requests before they reach the cluster.

```json
{
  "case_id": "t1-unschedulable-cpu-requests",
  "failing_resource": {"kind": "Deployment", "namespace": "fraud", "name": "fraud-scoring"},
  "mechanism": "The fraud-scoring Deployment's pod template requests cpu: 512 (unit-less, i.e. 512 whole cores, almost certainly a typo for 512m) for the scorer container, which exceeds the total CPU capacity of the cluster's only node. The scheduler therefore rejects the pod with 'Insufficient cpu' and notes preemption cannot help, so the pod stays Pending, never runs, and the Deployment remains 0/1 available, producing no fraud scores.",
  "verdict": "confirmed"
}
```