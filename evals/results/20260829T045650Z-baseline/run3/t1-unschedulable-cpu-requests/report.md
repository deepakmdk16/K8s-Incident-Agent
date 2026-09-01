## Root cause

**Confirmed.** The Deployment `fraud/fraud-scoring` requests an absurd amount of CPU for its `scorer` container — `cpu: 512` (i.e. 512 full cores, not `512m`). No node in this single-node kind cluster can satisfy that request, so the scheduler leaves the pod `Pending` forever and the Deployment never reaches 1/1 available. The failing resource whose spec must change is the Deployment's pod template `resources.requests.cpu`.

## Evidence chain

- **`kubectl get all -A`**: `fraud deployment.apps/fraud-scoring 0/1 1 0` — the paged symptom (0 of 1 ready) and `pod/fraud-scoring-596445859d-thcdh 0/1 Pending 0 ... <none> <none>` — pod has no IP and no node.
- **`describe pod/fraud-scoring-596445859d-thcdh`**, event: `Warning FailedScheduling ... default-scheduler 0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 Preemption is not helpful for scheduling.` — direct statement from the scheduler that CPU is the binding constraint, and that no other pod can be evicted to make room (so the request exceeds node allocatable, not just current free capacity).
- **`describe pod`**, container `scorer`: `Requests: cpu: 512`. No unit suffix means 512 *cores*. This is confirmed to originate from the workload spec, not a mutating admission hack, because the same value appears in **`describe deployment.apps/fraud-scoring`** (`Requests: cpu: 512`) and **`describe replicaset.apps/fraud-scoring-596445859d`** (`Requests: cpu: 512`).
- **`describe pod`**, `Conditions: PodScheduled False`, `Node: <none>` — the pod never got past scheduling; it was never admitted to a kubelet.
- **`kubectl logs ... --tail=50`** and **`--previous`** both return empty output — consistent with a container that has never started, ruling out any in-container failure mode.
- **Cluster size** from `kubectl get all -A`: every other pod runs on the single node `incident-lab-control-plane` (`daemonset kindnet DESIRED 1`, `kube-proxy DESIRED 1` — one node total). A kind control-plane node has on the order of a handful of allocatable cores; 512 is not achievable by any tolerable scaling.
- **`describe deployment`**: `Conditions: Available False MinimumReplicasUnavailable` / `Progressing True ReplicaSetUpdated` — the Deployment-level manifestation of the same stall, matching the alert text.
- **`describe pod`**: `Node-Selectors: <none>`, `Tolerations:` only the two default not-ready/unreachable ones — no placement constraint other than capacity is in play.

## Investigation ledger

- **Image pull failure (bad tag / private registry)** — ruled out: status is `Pending` with `PodScheduled False` and no node assigned; image pulls only happen after scheduling, and there is no `ErrImagePull`/`ImagePullBackOff` and no `Failed to pull image` event in `describe pod`. `busybox:1.36` is a public image.
- **CrashLoopBackOff / bad container command** — ruled out: `RESTARTS 0`, no container statuses at all, and both `kubectl logs` and `kubectl logs --previous` returned nothing. The container never ran.
- **Node cordoned, NotReady, or tainted** — ruled out: the scheduler message is specifically `Insufficient cpu`, not `node(s) had untolerated taint` or `node(s) were unschedulable`. Also all kube-system pods and both DaemonSets are `1/1 Running`/`READY 1`, so the node is healthy and accepting pods.
- **nodeSelector / affinity mismatch** — ruled out: `Node-Selectors: <none>` in `describe pod` and `describe deployment`; the scheduler would have said `didn't match Pod's node affinity/selector`.
- **Memory pressure or ephemeral-storage exhaustion** — ruled out: the scheduler names `cpu` as the insufficient resource; only a CPU request is set (`Requests: cpu: 512`, no memory request listed).
- **Genuine cluster-capacity shortage caused by noisy neighbours** — ruled out as the *cause*: `preemption: 0/1 nodes are available: 1 Preemption is not helpful for scheduling` means evicting every lower-priority pod would still not free enough CPU, so the request itself is larger than node allocatable. Furthermore the only other workloads are coredns, kube-proxy, kindnet, etcd, apiserver and local-path-provisioner — trivial consumers.
- **ResourceQuota / LimitRange rejecting the pod** — ruled out: the pod object exists and was successfully created (`SuccessfulCreate ... Created pod: fraud-scoring-596445859d-thcdh`); quota rejection would fail at ReplicaSet create time with a `FailedCreate` event, not at scheduling.
- **PriorityClass starvation** — ruled out: `Priority: 0` with no priority class, and preemption is explicitly reported as unhelpful.
- **Note on ages:** the alert says >20 minutes, while the Deployment/pod show `AGE 0s`. This is consistent with the objects having been re-applied/recreated just before capture (revision 1, `OldReplicaSets: <none>`); it does not change the diagnosis, since the spec-level `cpu: 512` is deterministic and will reproduce the same `FailedScheduling` on every recreation.

## Verification recipe

```bash
# 1. Show the offending request straight from the Deployment spec (expect "512")
kubectl get deploy fraud-scoring -n fraud \
  -o jsonpath='{.spec.template.spec.containers[0].resources.requests.cpu}{"\n"}'

# 2. Compare against what the node can actually offer (expect a single-digit core count)
kubectl get nodes -o custom-columns=NAME:.metadata.name,ALLOC_CPU:.status.allocatable.cpu

# 3. Confirm the scheduler's own verdict on the pending pod
kubectl get events -n fraud --field-selector reason=FailedScheduling \
  -o custom-columns=OBJ:.involvedObject.name,MSG:.message
```

Remediation: patch the request to a sane value (e.g. `250m`) and the pod schedules immediately.

```bash
kubectl set resources deployment/fraud-scoring -n fraud \
  --containers=scorer --requests=cpu=250m
kubectl rollout status deployment/fraud-scoring -n fraud --timeout=120s
```

```json
{
  "case_id": "t1-unschedulable-cpu-requests",
  "failing_resource": {"kind": "Deployment", "namespace": "fraud", "name": "fraud-scoring"},
  "mechanism": "The fraud-scoring Deployment's pod template requests 'cpu: 512' — 512 whole cores, almost certainly a typo for 512m — which exceeds the allocatable CPU of every node in this single-node cluster. The scheduler therefore cannot place the pod and it stays Pending with 'Insufficient cpu' and 'Preemption is not helpful', so the Deployment never reaches an available replica and no fraud scores are produced.",
  "verdict": "confirmed"
}
```