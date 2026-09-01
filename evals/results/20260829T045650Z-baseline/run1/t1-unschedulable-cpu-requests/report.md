## Root cause

**Confirmed.** The `fraud/fraud-scoring` Deployment's pod template requests `cpu: 512` — i.e. 512 *whole CPU cores*, almost certainly a units mistake for `512m` (0.512 core). No node in this single-node kind cluster can satisfy that request, so the scheduler can never place the pod. The pod therefore sits `Pending` forever, the ReplicaSet reports `0 Running / 1 Waiting`, the Deployment stays `0/1 Available`, and no fraud scores are produced. The failing resource whose spec must change is the Deployment, not the pod (any pod it creates will be rejected identically).

## Evidence chain

- **Symptom, `kubectl get all -A`:** `fraud pod/fraud-scoring-596445859d-thcdh 0/1 Pending 0 ... <none> <none>` — the pod is `Pending` with **no node assigned** (`NODE = <none>`), i.e. it never got scheduled, as opposed to failing after scheduling.
- **Direct scheduler verdict, `describe pod/fraud-scoring-596445859d-thcdh`:**
  `Warning FailedScheduling default-scheduler 0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 Preemption is not helpful for scheduling.`
  This names the exact resource dimension that fails (`cpu`) and confirms it is a scheduling admission failure, not an image/runtime failure.
- **Causal quantity, same describe output:**
  ```
  Requests:
    cpu:        512
  ```
  A bare `512` in Kubernetes quantity syntax means 512 cores (`512m` would be 0.512 core). This is the value the scheduler is trying and failing to fit.
- **Cluster capacity context, `kubectl get all -A`:** exactly one node exists, `incident-lab-control-plane` (every kube-system pod is on it; `daemonset kindnet`/`kube-proxy` show `DESIRED 1`). A single kind node has on the order of a handful of cores — orders of magnitude below 512. This matches `0/1 nodes are available`.
- **The bad request originates in the workload spec, not the pod:** `describe deployment.apps/fraud-scoring` pod template shows the same `Requests: cpu: 512`, and `describe replicaset.apps/fraud-scoring-596445859d` shows it again. So the defect is templated and will be reproduced on every pod recreation — the Deployment is the resource to change.
- **Rollout state consistent with "never scheduled":** `describe deployment` → `Conditions: Available False MinimumReplicasUnavailable`, `Progressing True ReplicaSetUpdated`; `describe replicaset` → `Pods Status: 0 Running / 1 Waiting / 0 Succeeded / 0 Failed`. Waiting, never Failed — no crash occurred.
- **No container ever ran:** both `kubectl logs ... -c scorer` and `... --previous` returned empty output, and the pod `Conditions` list contains only `PodScheduled False` (no `Initialized`/`ContainersReady`/`Ready`). Consistent with a pod that never reached a kubelet.
- **Note on ages:** the Deployment/ReplicaSet/pod all show `AGE 0s` while the page says >20 minutes. The `FailedScheduling` event is regenerated on each scheduling attempt and the ReplicaSet controller recreates/retries continually, so this snapshot is a fresh attempt of a long-standing loop; it does not change the mechanism.

## Investigation ledger

- **Image pull failure (`busybox:1.36` unavailable / wrong tag / registry auth):** ruled out — status is `Pending` with `NODE <none>` and the only event is `FailedScheduling`. An image problem produces `ImagePullBackOff`/`ErrImagePull` *after* a node is assigned, plus `Failed`/`BackOff` pull events. None present.
- **CrashLoopBackOff / bad container command:** ruled out — `RESTARTS 0`, ReplicaSet reports `0 Running / 1 Waiting / 0 Succeeded / 0 Failed`, and both current and `--previous` logs are empty. The container never started, so the `sh -c` loop cannot be at fault.
- **Failing readiness/liveness probe (running but never Ready):** ruled out — no probes are defined in the pod template, and the pod is not Running; `PodScheduled False` is the only condition.
- **Node taints / nodeSelector / affinity mismatch:** ruled out — `Node-Selectors: <none>` and only the two default not-ready/unreachable tolerations are set; more decisively, the scheduler message says `Insufficient cpu`, not `node(s) had untolerated taint` or `didn't match Pod's node affinity/selector`.
- **Node down or NotReady (control-plane node unhealthy):** ruled out — every kube-system pod (`etcd`, `kube-apiserver`, `kube-scheduler`, `kube-controller-manager`, `kube-proxy`, `kindnet`, `coredns`) is `1/1 Running` with `0` restarts on that node, and the scheduler is actively evaluating and reporting on `0/1 nodes are available` (it counted 1 schedulable node).
- **Other workloads hogging CPU (noisy neighbor exhausting allocatable):** ruled out as the *cause* — the only other pods are kube-system/local-path components, whose combined requests on a kind node are a fraction of a core. Even on a completely idle node a 512-core request is unsatisfiable. Also note preemption was explored by the scheduler and reported "not helpful", i.e. evicting everything else would still not free enough CPU.
- **Memory pressure / insufficient memory:** ruled out — no memory request is set at all, and the scheduler explicitly names `Insufficient cpu`.
- **ResourceQuota or LimitRange in namespace `fraud` blocking creation:** ruled out — the pod object was created successfully (`SuccessfulCreate  Created pod: fraud-scoring-596445859d-thcdh`); quota rejection would fail at ReplicaSet admission with a `FailedCreate` / `exceeded quota` event and no pod would exist.
- **Missing volume/PVC/Secret/ConfigMap:** ruled out — the only volume is the default projected service-account token `kube-api-access-l7gvx`; no PVC, and there is no `FailedMount`/`FailedAttachVolume` event (those would also appear only post-scheduling).
- **Scheduler itself broken:** ruled out — `pod/kube-scheduler-incident-lab-control-plane 1/1 Running 0 9h`, and it is emitting fresh, specific scheduling decisions for this pod.

## Verification recipe

```bash
# 1. See the offending request verbatim (expect: 512, not 512m)
kubectl get deploy fraud-scoring -n fraud \
  -o jsonpath='{.spec.template.spec.containers[0].resources}{"\n"}'

# 2. Compare against actual node capacity/allocatable CPU (expect: single-digit cores)
kubectl get nodes -o custom-columns=\
NAME:.metadata.name,CPU_CAP:.status.capacity.cpu,CPU_ALLOC:.status.allocatable.cpu

# 3. Confirm the scheduler is rejecting on CPU, right now
kubectl get events -n fraud --field-selector reason=FailedScheduling \
  --sort-by=.lastTimestamp | tail -5
```

Remediation: patch the Deployment's request to the intended value (and give it a limit), which lets the ReplicaSet's next pod schedule:

```bash
kubectl set resources deployment/fraud-scoring -n fraud \
  -c=scorer --requests=cpu=512m --limits=cpu=1
kubectl rollout status deployment/fraud-scoring -n fraud --timeout=120s
```

Follow-up: add a `LimitRange` (max CPU request) or CI policy check in the `fraud` namespace so a bare-integer CPU quantity cannot reach the cluster again.

```json
{
  "case_id": "t1-unschedulable-cpu-requests",
  "failing_resource": {"kind": "Deployment", "namespace": "fraud", "name": "fraud-scoring"},
  "mechanism": "The fraud-scoring Deployment's pod template sets the scorer container's CPU request to `512` (512 whole cores, almost certainly a typo for `512m`), which exceeds the allocatable CPU of the cluster's only node. The scheduler cannot place the pod on any node and rejects it with `0/1 nodes are available: 1 Insufficient cpu`, so the pod stays Pending with no node assigned, the container never starts, and the Deployment remains 0/1 available, emitting no fraud scores.",
  "verdict": "confirmed"
}
```