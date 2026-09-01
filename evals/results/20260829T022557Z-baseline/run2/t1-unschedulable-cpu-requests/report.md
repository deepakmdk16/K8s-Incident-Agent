## Root cause

**Confirmed.** The Deployment `fraud/fraud-scoring` requests **512 CPU cores** (`Requests: cpu: 512` — cores, not millicores; `512m` was almost certainly intended). No node in this single-node kind cluster can satisfy that request, so the scheduler rejects the pod with `Insufficient cpu`, the pod stays `Pending` forever, is never bound to a node, and the container never starts. Hence `0/1` ready and zero fraud scores emitted.

The failing resource is the Deployment's pod template — the pod itself is a disposable symptom; deleting/restarting it produces another identical unschedulable pod.

## Evidence chain

- **Symptom**: `kubectl get all -A` → `fraud pod/fraud-scoring-596445859d-thcdh 0/1 Pending 0 ... <none> <none>` — status `Pending`, `NODE` column is `<none>`, i.e. never scheduled. Matching `deployment.apps/fraud-scoring 0/1 ... 0 AVAILABLE`.
- **Direct causal statement from the scheduler**: describe of pod `fraud-scoring-596445859d-thcdh`, Events:
  `Warning FailedScheduling ... default-scheduler 0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 Preemption is not helpful for scheduling.`
  This links mechanism (CPU request cannot be fit) to symptom (never scheduled) explicitly.
- **The offending spec value lives in the workload, not just the pod**: describe of deployment `fraud-scoring`, Pod Template → container `scorer` → `Requests: cpu: 512`. The same value appears in describe of replicaset `fraud-scoring-596445859d` (`Requests: cpu: 512`) and in describe of the pod (`Requests: cpu: 512`), proving it is propagated from the Deployment template.
- **Units**: Kubernetes reads a bare `512` as 512 whole cores. Any millicore intent would render as `512m`. Contrast: the pod has no memory request at all and `QoS Class: Burstable`, consistent with a single CPU-request field being the only resource constraint in play.
- **Cluster capacity is one node**: `kubectl get all -A` shows every system pod on `incident-lab-control-plane`, and both DaemonSets show `DESIRED 1 / CURRENT 1` — a one-node kind cluster. The scheduler message `0/1 nodes are available` corroborates a single schedulable node. A kind node has on the order of the host's core count, nowhere near 512.
- **Never started, so no runtime failure is possible**: `kubectl logs ... -c scorer` and `--previous` both return empty output, and the pod has `RESTARTS 0`, `IP: <none>`, and only `PodScheduled False` under Conditions (no `Initialized`/`ContainersReady` entries). The container was never created.
- **Preemption cannot rescue it**: pod `Priority: 0` and the scheduler's `Preemption is not helpful` — even evicting everything on the node would not free 512 cores.

## Investigation ledger

- **Image pull failure (bad tag / private registry)** — ruled out: status is `Pending` with `FailedScheduling`, not `ImagePullBackOff`/`ErrImagePull`; there is no `Failed to pull image` event. `busybox:1.36` is a valid public tag and the pod never reached a node where a pull would even be attempted.
- **CrashLoopBackOff / bad container command** — ruled out: `RESTARTS 0`, both `kubectl logs` and `kubectl logs --previous` return nothing, and the only pod condition is `PodScheduled False`. The shell loop in `Command` never executed.
- **Node taints / nodeSelector / affinity mismatch** — ruled out: describe of the pod shows `Node-Selectors: <none>` and only the two default NoExecute tolerations; the scheduler's reason is specifically `Insufficient cpu`, not `node(s) had untolerated taint` or `didn't match node selector`.
- **Node NotReady or control-plane failure** — ruled out: all `kube-system` pods (`etcd`, `kube-apiserver`, `kube-scheduler`, `kube-controller-manager`, `kube-proxy`, `kindnet`, `coredns`) are `1/1 Running` with 0 restarts, and the scheduler is actively emitting scheduling decisions for this pod (`0/1 nodes are available`, i.e. a node exists and is being evaluated).
- **Missing volume / ConfigMap / Secret mount** — ruled out: the only volume is the default projected `kube-api-access-l7gvx` service-account token; no user PVC/ConfigMap/Secret is referenced and there is no `FailedMount`/`Unschedulable due to unbound PVC` event.
- **ResourceQuota / LimitRange in namespace `fraud` blocking admission** — ruled out as the *paged* mechanism: quota rejection surfaces on the ReplicaSet as `FailedCreate ... exceeded quota`, but describe of the ReplicaSet shows `Normal SuccessfulCreate Created pod: fraud-scoring-596445859d-thcdh` and `1 current / 1 desired`. The pod object exists; the block is purely at scheduling.
- **Insufficient *memory* or other resource** — ruled out: the scheduler names `Insufficient cpu` specifically, and the pod template declares no memory request.
- **Missing Service (scores unreachable rather than unproduced)** — ruled out as root cause: no Service for `fraud-scoring` appears in `kubectl get all -A`, which would matter for reachability, but it cannot explain `0/1 READY` on the Deployment. The pod is not running at all, so the absence of a Service is downstream noise, not the paged symptom. (Worth fixing separately once the pod schedules.)
- **Age discrepancy (alert says "20 minutes", objects show `AGE 0s`)** — noted, not a competing explanation: the ReplicaSet event `SuccessfulCreate` at `2s` and Deployment event `ScalingReplicaSet ... from 0 to 1` at `1s` show this is a freshly (re)created generation of the same unschedulable object, consistent with the capture being taken after a redeploy/recreate. The `cpu: 512` request is identical across Deployment, ReplicaSet and Pod, so the mechanism is unchanged across generations.

## Verification recipe

```bash
# 1. Confirm the request is 512 whole cores in the Deployment template (the thing to change)
kubectl get deploy fraud-scoring -n fraud \
  -o jsonpath='{.spec.template.spec.containers[0].resources}{"\n"}'
# expect: {"requests":{"cpu":"512"}}   <- no "m" suffix = 512 cores

# 2. Confirm the node cannot possibly satisfy it
kubectl get nodes -o custom-columns=\
NAME:.metadata.name,CPU_CAPACITY:.status.capacity.cpu,CPU_ALLOCATABLE:.status.allocatable.cpu
# expect: allocatable single-digit / low double-digit cores, << 512

# 3. Confirm the scheduler's own verdict
kubectl get events -n fraud --field-selector reason=FailedScheduling \
  -o custom-columns=MSG:.message
# expect: "0/1 nodes are available: 1 Insufficient cpu. ..."
```

**Remediation:** patch the Deployment's CPU request to the intended millicore value (and add a memory request/limit while you're there):

```bash
kubectl set resources deployment/fraud-scoring -n fraud \
  --containers=scorer --requests=cpu=512m,memory=64Mi --limits=cpu=1,memory=128Mi
kubectl rollout status deployment/fraud-scoring -n fraud --timeout=120s
```

The rollout creates a new ReplicaSet whose pod is schedulable; the old `fraud-scoring-596445859d` and its Pending pod are scaled to 0 automatically. Follow-ups: fix the value at source in the manifest/Helm chart so the next deploy doesn't reintroduce it, add a LimitRange or admission policy in `fraud` capping per-container CPU requests, and create the Service/readiness probe the scoring clients need.

```json
{
  "case_id": "t1-unschedulable-cpu-requests",
  "failing_resource": {"kind": "Deployment", "namespace": "fraud", "name": "fraud-scoring"},
  "mechanism": "The Deployment's pod template requests 'cpu: 512' — 512 whole cores, not the intended 512m — which exceeds the allocatable CPU of the cluster's only node. The scheduler therefore cannot bind the pod and rejects it with 'Insufficient cpu', leaving it permanently Pending with the container never created, so the Deployment reports 0/1 available and emits no fraud scores.",
  "verdict": "confirmed"
}
```