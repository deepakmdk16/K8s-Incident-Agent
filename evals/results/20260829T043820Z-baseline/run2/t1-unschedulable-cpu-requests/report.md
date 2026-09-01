## Root cause

**Confirmed.** The `fraud/fraud-scoring` Deployment's pod template requests **`cpu: 512`** (i.e. 512 CPU *cores*, not 512 milli-cores — the value is missing the `m` suffix). No node in this single-node kind cluster can satisfy a 512-core request, so the scheduler rejects the pod with `Insufficient cpu` and it stays `Pending` forever. Nothing is ever admitted to a node, so no container starts, no scores are produced, and the Deployment reports `0/1` available.

This is a spec bug in the Deployment, not a node capacity or runtime failure: the resource unit typo makes the pod permanently unschedulable regardless of cluster size.

## Evidence chain

- **Symptom, `kubectl get all -A`:** `fraud pod/fraud-scoring-596445859d-thcdh 0/1 Pending 0 ... <none> <none>` — the pod is `Pending` with `NODE = <none>`, i.e. never scheduled, never started.
- **Deployment rollup, `kubectl get all -A`:** `deployment.apps/fraud-scoring 0/1 1 0` and from `describe deployment fraud-scoring`: `Replicas: 1 desired | 1 updated | 1 total | 0 available | 1 unavailable`, `Available False MinimumReplicasUnavailable` — matches the paged "0/1 running / no fraud scores".
- **Direct scheduler verdict, `describe pod/fraud-scoring-596445859d-thcdh` Events:**
  `Warning FailedScheduling ... default-scheduler 0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 Preemption is not helpful for scheduling.`
  This names CPU as the exact unsatisfiable predicate and states preemption cannot help (i.e. even evicting everything on the node would not free enough CPU).
- **Offending field, `describe pod` and `describe deployment` / `describe replicaset` (identical pod templates):**
  ```
  Requests:
    cpu:  512
  ```
  Rendered without an `m` suffix, this is 512 whole cores. A kind cluster's single node (`incident-lab-control-plane`) has on the order of a handful of cores.
- **Cluster has exactly one node, `describe pod`:** `0/1 nodes are available` and `kubectl get all -A` shows every system pod on `incident-lab-control-plane`, with `daemonset kindnet/kube-proxy DESIRED 1` — confirming a one-node cluster, so there is no other node that could fit the request.
- **Condition set, `describe pod`:** only `PodScheduled False` is present — there is no `Initialized`/`ContainersReady`/`Ready` progression, confirming the pod never reached the kubelet.
- **No container ever ran:** `kubectl logs ... -c scorer` and `... --previous` both return empty output, consistent with a pod that was never bound to a node (nothing to pull, run, or crash).
- **Ownership chain, `describe replicaset fraud-scoring-596445859d`:** `Controlled By: Deployment/fraud-scoring`, `Pods Status: 0 Running / 1 Waiting`, `SuccessfulCreate ... Created pod: fraud-scoring-596445859d-thcdh` — the ReplicaSet keeps creating the pod successfully; the failure is purely at scheduling, so the fix must be applied to the Deployment's pod template (the source of the request value).

## Investigation ledger

- **Image pull failure (bad image/registry/credentials)** — ruled out: status is `Pending` with `PodScheduled False` and the only event is `FailedScheduling`. No `ErrImagePull`/`ImagePullBackOff`, and image pulls only happen after a node is assigned; `Node: <none>`.
- **CrashLoopBackOff / bad command in the `sh -c` loop** — ruled out: `RESTARTS 0`, no container statuses, and both `kubectl logs` and `kubectl logs --previous` return empty. The container never executed.
- **Node down / NotReady / cordoned node, or taint without matching toleration** — ruled out: the scheduler message is specifically `Insufficient cpu`, not `node(s) had untolerated taint` or `node(s) were unschedulable`. All kube-system pods and both DaemonSets are `1/1 Running`/`READY 1` on that node, so the node is healthy and admitting pods.
- **`nodeSelector` / affinity mismatch** — ruled out: `describe pod` shows `Node-Selectors: <none>` and no affinity stanza; the scheduler would have said `didn't match Pod's node affinity/selector`.
- **Insufficient memory, ephemeral storage, or pod-count limit** — ruled out: the request block lists only `cpu`, and the scheduler names `Insufficient cpu` exclusively.
- **A ResourceQuota or LimitRange in namespace `fraud` blocking admission** — ruled out as the paged mechanism: quota/LimitRange violations fail at pod *creation* (ReplicaSet would emit `FailedCreate`), but `describe replicaset` shows `SuccessfulCreate ... Created pod`. The pod exists and reached the scheduler.
- **Genuine cluster capacity exhaustion caused by other noisy workloads** — ruled out: the only non-system workloads are `coredns`, `local-path-provisioner`, `kindnet`, `kube-proxy`, which reserve on the order of hundreds of millicores. Also `preemption: ... Preemption is not helpful` means freeing every preemptible pod still would not fit 512 cores — the request exceeds total node capacity, so this is a request-size bug, not contention.
- **Missing Service / networking preventing scores from reaching the risk team** — ruled out as root cause: there is no `fraud` Service at all in `kubectl get all -A`, but that is downstream noise; the pod is not even running, so the availability symptom is fully explained by scheduling. (Worth filing separately once the pod runs.)
- **Note on the `0s` ages:** the Deployment, ReplicaSet, and pod all show `AGE 0s` while the page says "over 20 minutes". This is consistent with the ReplicaSet controller having just recreated the pod object (or the resource having been re-applied) during the incident; it does not change the diagnosis, since the pod template's CPU request is the same in the Deployment, ReplicaSet, and pod, and would fail identically at every attempt.

## Verification recipe

```bash
# 1. Show the exact request value on the owning Deployment (expect: "512", not "512m")
kubectl get deploy fraud-scoring -n fraud \
  -o jsonpath='{.spec.template.spec.containers[0].resources}{"\n"}'

# 2. Compare against what the single node actually has allocatable (expect: a few cores)
kubectl get nodes -o custom-columns=\
NAME:.metadata.name,CPU_CAPACITY:.status.capacity.cpu,CPU_ALLOCATABLE:.status.allocatable.cpu

# 3. Re-read the scheduler's own verdict on the pending pod
kubectl get events -n fraud --field-selector reason=FailedScheduling \
  -o custom-columns=OBJ:.involvedObject.name,MSG:.message
```

Remediation: patch the Deployment's pod template to a sane request (e.g. `500m`) — `kubectl set resources deploy/fraud-scoring -n fraud -c scorer --requests=cpu=500m` — or fix the manifest in source control to `cpu: 500m` and re-apply. The pod should schedule within seconds; confirm with `kubectl rollout status deploy/fraud-scoring -n fraud`. Follow-ups: add a LimitRange with a `maxRequest` in the `fraud` namespace and a CI lint that rejects bare integer CPU requests above node capacity, so a missing `m` cannot ship again.

```json
{
  "case_id": "t1-unschedulable-cpu-requests",
  "failing_resource": {"kind": "Deployment", "namespace": "fraud", "name": "fraud-scoring"},
  "mechanism": "The fraud-scoring Deployment's pod template requests 'cpu: 512' — 512 whole cores rather than the intended 512m — which exceeds the allocatable CPU of every node in this single-node cluster. The scheduler therefore rejects the pod with 'Insufficient cpu' and notes preemption cannot help, leaving it permanently Pending with no node assigned, so the scorer container never starts and the Deployment stays 0/1 available with no fraud scores produced.",
  "verdict": "confirmed"
}
```