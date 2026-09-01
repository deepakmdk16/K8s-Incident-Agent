## Root cause

**Confirmed.** The Deployment `fraud/fraud-scoring` requests `cpu: 512` — that is 512 **whole CPU cores**, not 512 millicores (`512m`). No node in this single-node kind cluster can satisfy that request, so the scheduler can never place the pod, it stays `Pending` forever, and the Deployment reports `0/1` available. The failing spec is the Deployment's pod template (`spec.template.spec.containers[scorer].resources.requests.cpu`); the Pending pod is just the symptom it produces.

## Evidence chain

- The paged symptom, from `kubectl get all -A`: `fraud pod/fraud-scoring-596445859d-thcdh 0/1 Pending 0 ... <none> <none>` — no node assigned, and `deployment.apps/fraud-scoring 0/1 ... 0 AVAILABLE`.
- The scheduler states the reason directly, from the describe of pod `fraud-scoring-596445859d-thcdh`:
  `Warning FailedScheduling ... 0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 Preemption is not helpful for scheduling.`
- The resource ask is in the same describe:
  ```
  Requests:
    cpu:        512
  ```
  A bare `512` in Kubernetes quantity notation means 512 cores. Had the author meant half a core, the value would render as `512m`. This is the classic missing-`m` typo.
- The same value is in the *owning* spec, so recreating the pod cannot help — from the describe of `deployment.apps/fraud-scoring` and of `replicaset.apps/fraud-scoring-596445859d`, both pod templates show `Requests: cpu: 512`. This is why the alert has persisted 20+ minutes while the pod object itself shows `AGE 0s` (the pod keeps being recreated/re-listed into the same doomed state).
- The cluster genuinely has only one small node: every other pod in `kubectl get all -A` is on `incident-lab-control-plane`, and both DaemonSets show `DESIRED 1`, i.e. a single-node kind cluster. A kind node has a handful of allocatable cores at most — orders of magnitude below 512.
- `Node: <none>`, `IP:` empty, and `Conditions: PodScheduled False` in the pod describe confirm the pod never reached a node.
- Empty output from both `kubectl logs ... --tail=50` and `... --previous --tail=50` is consistent with a container that has never started — there is nothing to log because scheduling never happened.

## Investigation ledger

- **Image pull failure / bad image (`busybox:1.36`)** — ruled out. Status is `Pending` with `PodScheduled False` and reason `FailedScheduling`, not `ImagePullBackOff`/`ErrImagePull`; image pull cannot even be attempted before a node is assigned.
- **CrashLoopBackOff / bad container command** — ruled out. `RESTARTS 0`, `Pods Status: 0 Running / 1 Waiting`, and both current and `--previous` log fetches return nothing; the container never ran. The command itself (`while true; do ... sleep 10; done`) would run indefinitely anyway.
- **NodeSelector / affinity / taint mismatch** — ruled out. Pod describe shows `Node-Selectors: <none>` and only the two default `NoExecute` tolerations; the scheduler's message names `Insufficient cpu`, not `node(s) had untolerated taint` or `didn't match node selector`.
- **Node NotReady or scheduler down** — ruled out. `kube-scheduler`, `kube-apiserver`, `kube-controller-manager`, `etcd`, `kube-proxy`, `kindnet`, CoreDNS and local-path-provisioner are all `1/1 Running` on the node with 0 restarts, and the scheduler is actively emitting `FailedScheduling` events, so it is alive and evaluating this pod.
- **Other workloads hogging CPU on the node** — ruled out as the *cause*. Preemption is explicitly declared useless (`Preemption is not helpful for scheduling`), meaning even evicting every lower-priority pod would not free 512 cores; the request exceeds total node capacity, not merely current free capacity. The only other tenants are lightweight system pods.
- **Missing/failed PVC or ConfigMap blocking start** — ruled out. The pod's only volume is the default projected service-account token (`kube-api-access-l7gvx`); there are no PVCs anywhere in `kubectl get all -A`, and no `FailedMount`/`FailedScheduling ... persistentvolumeclaim` event.
- **ResourceQuota / LimitRange rejection in namespace `fraud`** — ruled out as the mechanism. The pod object was successfully created (`SuccessfulCreate ... Created pod`) and admitted; failure occurs at the scheduling stage, not at admission.

## Verification recipe

```bash
# 1. See the literal request value as stored in the Deployment spec (expect "512", not "512m")
kubectl get deploy fraud-scoring -n fraud \
  -o jsonpath='{.spec.template.spec.containers[0].resources.requests.cpu}{"\n"}'

# 2. Compare against what the cluster actually has to offer
kubectl get nodes -o custom-columns=NAME:.metadata.name,CPU_CAPACITY:.status.capacity.cpu,CPU_ALLOCATABLE:.status.allocatable.cpu

# 3. Re-read the scheduler's verdict on the pending pod
kubectl get events -n fraud --field-selector reason=FailedScheduling \
  -o custom-columns=OBJ:.involvedObject.name,MSG:.message
```

Expected: step 1 prints `512`, step 2 prints a single-digit allocatable core count, step 3 prints `Insufficient cpu` — 512 requested cores versus a few available proves the mechanism.

**Remediation:** patch the Deployment's request to the intended half-core value (and add a matching limit), which triggers a new ReplicaSet whose pod schedules immediately:

```bash
kubectl set resources deployment/fraud-scoring -n fraud \
  --containers=scorer --requests=cpu=500m --limits=cpu=1
kubectl rollout status deployment/fraud-scoring -n fraud --timeout=120s
```

Follow-up: add a CI/admission check (e.g. a policy rejecting CPU requests above node-allocatable, or a `LimitRange` with a `max` in namespace `fraud`) so a missing `m` suffix fails the pipeline instead of paging on-call.

```json
{
  "case_id": "t1-unschedulable-cpu-requests",
  "failing_resource": {"kind": "Deployment", "namespace": "fraud", "name": "fraud-scoring"},
  "mechanism": "The fraud-scoring Deployment's pod template requests 'cpu: 512', i.e. 512 whole cores, almost certainly a typo for '512m'. That request exceeds the allocatable CPU of every node in this single-node cluster, so the scheduler can never bind the pod and emits 'Insufficient cpu' with preemption declared unhelpful. The pod stays Pending with no node, leaving the Deployment permanently 0/1 available and producing no fraud scores.",
  "verdict": "confirmed"
}
```