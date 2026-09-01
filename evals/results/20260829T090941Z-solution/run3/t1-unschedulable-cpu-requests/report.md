## Root cause

Deployment fraud/fraud-scoring asks each replica for 512 whole CPU cores. The unit suffix is missing: "512" means 512 cores, not the 512m (half a core) that was intended. The only node in the cluster, incident-lab-control-plane, has 6 allocatable CPU, so the scheduler can never place the pod and rejects it with "0/1 nodes are available: 1 Insufficient cpu". The pod fraud-scoring-596445859d-thcdh therefore stays Pending with PodScheduled=False and node <none>, the Deployment reports ready=0/1, and no fraud scores are produced. Fixing it requires editing the Deployment's pod template (the pod itself is disposable and would be recreated with the same request).

Remediation: edit Deployment fraud/fraud-scoring, field `spec.template.spec.containers[scorer].resources.requests.cpu`: `512` -> `512m`.

## Evidence chain

1. [symptom] The paged Deployment has no ready replicas and its only pod is Pending and unscheduled.
   source: namespace_overview(fraud) — verified
   > deployment/fraud-scoring ready=0/1 podLabels={app=fraud-scoring}
2. [symptom] The pod is Pending with no node assigned and PodScheduled False.
   source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
   > Status:           Pending
3. [link] The scheduler rejects the pod for insufficient CPU on the only node.
   source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
   > 0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 Preemption is not helpful for scheduling.
4. [link] The pod requests 512 CPU (whole cores, no milli suffix).
   source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
   > cpu:        512
5. [defect] The Deployment pod template is the source of the unsuffixed CPU request.
   source: get_object({"kind": "deployment", "name": "fraud-scoring", "namespace": "fraud"}) — verified
   > "requests": {
   >                 "cpu": "512"
   >               }
6. [link] The cluster's single node has only 6 allocatable CPU, far below the 512 cores requested.
   source: cluster_capacity({}) — verified
   > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)

## Investigation ledger

- The node is tainted, NotReady, or otherwise unavailable, blocking scheduling — ruled out: The single node reports Ready=True and no taints are recorded; the scheduler's own reason is Insufficient cpu, not a taint mismatch.
  source: cluster_capacity({}) — verified
  > condition Ready=True
- A nodeSelector or affinity rule in the pod spec prevents placement — ruled out: The pod has no node selector at all, so no placement constraint other than resources applies.
  source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
  > Node-Selectors:              <none>
- A ResourceQuota or LimitRange in the fraud namespace is rejecting or inflating the pod — ruled out: The namespace contains only a ConfigMap and a ServiceAccount besides the workload; no ResourceQuota or LimitRange exists, and the pod was admitted and created successfully.
  source: get_events({"namespace": "fraud", "warnings_only": false}) — verified
  > fraud Normal SuccessfulCreate replicaset/fraud-scoring-596445859d x1 Created pod: fraud-scoring-596445859d-thcdh
- The container image or command is broken (crash/image pull failure) — ruled out: The pod never reached a node, so no image pull or container start was ever attempted; the only condition present is PodScheduled False.
  source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
  > Conditions:
  >   Type           Status
  >   PodScheduled   False

## Verification recipe

1. `kubectl -n fraud get deployment fraud-scoring -o jsonpath='{.spec.template.spec.containers[0].resources.requests.cpu}'` — expect to see: "cpu": "512"  [PRESENT]
2. `kubectl -n fraud describe pod fraud-scoring-596445859d-thcdh` — expect to see: Insufficient cpu  [PRESENT]
3. `kubectl get nodes -o custom-columns=NAME:.metadata.name,CPU:.status.allocatable.cpu` — expect to see: cpu=6/6  [PRESENT]
(Each command was re-executed against the captured cluster state; PRESENT/ABSENT is the measured result, not a prediction.)

Verdict: confirmed. Every quote below was re-checked against the tool output it cites, a defect observation names the failing object, an alternative was ruled out with evidence, and every verification command was re-run and found present.

```json
{
  "case_id": "t1-unschedulable-cpu-requests",
  "failing_resource": {
    "kind": "Deployment",
    "namespace": "fraud",
    "name": "fraud-scoring"
  },
  "mechanism": "Deployment fraud/fraud-scoring sets .spec.template.spec.containers[scorer].resources.requests.cpu to \"512\" (512 whole cores) instead of \"512m\"; the only node has cpu=6/6 allocatable, so the default scheduler rejects the pod with \"0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 Preemption is not helpful for scheduling.\" and the replica stays Pending with PodScheduled False and no node assigned, keeping the Deployment at ready=0/1.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
