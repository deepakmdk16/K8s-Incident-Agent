# Incident report — `t1-unschedulable-cpu-requests`

## Root cause
**Confirmed.** The Deployment `fraud/fraud-scoring` requests `cpu: 512` for its `scorer` container. That value has no unit suffix, so Kubernetes interprets it as **512 whole CPU cores**, not 512 millicores (`512m`). The cluster is a single-node kind cluster (`incident-lab-control-plane`) whose allocatable CPU is a small handful of cores, so no node can satisfy the request. The scheduler therefore rejects the pod with `Insufficient cpu`, the pod stays `Pending` forever, and the Deployment never reaches 1/1 — hence zero fraud scores. The spec that must change is the Deployment's pod template resource request.

## Evidence chain

- **Symptom, from `kubectl get all -A`:** `fraud pod/fraud-scoring-596445859d-thcdh 0/1 Pending 0 ... <none> <none>` — pod is Pending with **no node assigned** (`NODE = <none>`), i.e. it never got scheduled; it is not a crash/image/startup problem.
- **Deployment-level symptom, from `describe deployment.apps/fraud-scoring`:** `Replicas: 1 desired | 1 updated | 1 total | 0 available | 1 unavailable` and `Available False MinimumReplicasUnavailable`. This is the exact condition the availability monitor pages on.
- **Direct causal event, from `describe pod/fraud-scoring-596445859d-thcdh`:**
  `Warning FailedScheduling ... 0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 Preemption is not helpful for scheduling.`
  The scheduler names CPU as the unsatisfiable resource, and states preemption cannot help — i.e. the request exceeds node capacity outright, not merely current free CPU.
- **The offending spec value, from `describe pod` and `describe deployment` and `describe replicaset` (all three agree):**
  ```
  Requests:
    cpu:  512
  ```
  No `m` suffix ⇒ 512 cores. Note also `QoS Class: Burstable` (request set, no limit), confirming the request is the only resource knob in play.
- **Cluster capacity context, from `kubectl get all -A`:** exactly one node appears anywhere in the output — `incident-lab-control-plane` (it hosts etcd, apiserver, scheduler, controller-manager, kube-proxy, kindnet, coredns, local-path-provisioner). The scheduler's own `0/1 nodes are available` confirms the cluster has a single schedulable node. A single kind node does not have 512 cores.
- **Nothing else is wrong with the workload:** `Node-Selectors: <none>`, `Tolerations:` only the default not-ready/unreachable ones, `Volumes:` only the default service-account projection with `ConfigMapName: kube-root-ca.crt` — so no nodeSelector/affinity/taint/PVC blocker.
- **Logs are empty (`kubectl logs ... --tail=50` and `--previous` both return nothing)** — consistent with a container that has never been created on a node, reinforcing "never scheduled" rather than "started and failed".

Minor note on timestamps: the page says "over 20 minutes", but the captured objects show `AGE 0s` and `ScalingReplicaSet ... 1s`. The snapshot was taken right after a re-create/re-apply of the same spec (revision 1, `OldReplicaSets: <none>`). The mechanism is unchanged and reproduces immediately — a pod with a 512-core request will re-enter `Pending` every time.

## Investigation ledger

- **Image pull failure (bad tag / private registry):** ruled out. Status is `Pending` with `PodScheduled False` and the only event is `FailedScheduling`; there is no `ErrImagePull`/`ImagePullBackOff`, and `busybox:1.36` is a public image. Image pull only happens after scheduling, and no node was ever assigned (`Node: <none>`).
- **CrashLoopBackOff / bad container command:** ruled out. `RESTARTS 0`, both `kubectl logs` and `kubectl logs --previous` return empty, and the container never reached a node. The command itself is a valid infinite `sh -c` loop that would not exit.
- **Readiness/liveness probe failing:** ruled out. `describe pod` shows no probes configured, and the pod never entered Running.
- **nodeSelector / node affinity mismatch:** ruled out. `Node-Selectors: <none>` in the pod, ReplicaSet, and Deployment descriptions. The scheduler message would also read `didn't match Pod's node affinity/selector` rather than `Insufficient cpu`.
- **Control-plane taint blocking scheduling on the only node:** ruled out as the *paged* cause. The scheduler explicitly reported `Insufficient cpu`, not `node(s) had untolerated taint`. (Other pods like coredns and local-path-provisioner do schedule onto this node, so it is schedulable.)
- **Insufficient memory or ephemeral storage:** ruled out. The only request in the spec is `cpu`, and the scheduler names cpu specifically.
- **Genuine cluster-wide CPU exhaustion caused by noisy neighbours:** ruled out. The only other workloads on the node are kube-system control-plane/CNI pods and local-path-provisioner — a normal idle kind cluster. Decisively, the scheduler says `Preemption is not helpful`, meaning evicting every lower-priority pod would still not free 512 cores; the request exceeds node *capacity*, not just current availability.
- **ResourceQuota / LimitRange in namespace `fraud` injecting the value:** not ruled out as the *origin* of the number, but irrelevant to the failing mechanism — the pod was admitted (it exists) and failed at scheduling, not admission. Worth checking during the fix so the corrected value is not re-mutated.
- **Missing Service / networking preventing scores from reaching the risk team:** noted — there is indeed no `fraud` Service in `kubectl get all -A`. This is a separate gap, not the paged cause; even with a Service, a `Pending` pod would serve nothing.

## Verification recipe

```bash
# 1. Show the raw, unit-less request that the scheduler is being asked to satisfy.
kubectl get deploy fraud-scoring -n fraud \
  -o jsonpath='{.spec.template.spec.containers[0].resources.requests.cpu}{"\n"}'
# expect: 512   (i.e. 512 cores, not "512m")

# 2. Compare against what the only node actually has.
kubectl get nodes -o custom-columns=\
NAME:.metadata.name,CPU_CAPACITY:.status.capacity.cpu,CPU_ALLOCATABLE:.status.allocatable.cpu
# expect: a single node with a small core count (e.g. 8) << 512

# 3. Re-read the scheduler's verdict on the live pod.
kubectl get events -n fraud --field-selector reason=FailedScheduling \
  -o custom-columns=MSG:.message
# expect: "0/1 nodes are available: 1 Insufficient cpu. preemption: ... not helpful"
```

**Remediation:** patch the Deployment's request to millicores (and add a sane limit), which triggers a new ReplicaSet that schedules immediately:

```bash
kubectl -n fraud patch deploy fraud-scoring --type=json -p='[
  {"op":"replace","path":"/spec/template/spec/containers/0/resources/requests/cpu","value":"512m"},
  {"op":"add","path":"/spec/template/spec/containers/0/resources/limits","value":{"cpu":"1"}}
]'
kubectl -n fraud rollout status deploy/fraud-scoring --timeout=120s
```

Then fix the same typo in the source manifest/Helm values so the next deploy does not regress, and separately create the `fraud-scoring` Service that is absent from the cluster.

```json
{
  "case_id": "t1-unschedulable-cpu-requests",
  "failing_resource": {"kind": "Deployment", "namespace": "fraud", "name": "fraud-scoring"},
  "mechanism": "The Deployment's pod template requests 'cpu: 512' with no unit suffix, which Kubernetes parses as 512 whole CPU cores rather than 512 millicores. The single node in this cluster has nowhere near that allocatable CPU, so the scheduler rejects the pod with 'Insufficient cpu' and notes preemption cannot help; the pod stays Pending with no node assigned, the Deployment reports 0/1 available, and no fraud scores are produced.",
  "verdict": "confirmed"
}
```