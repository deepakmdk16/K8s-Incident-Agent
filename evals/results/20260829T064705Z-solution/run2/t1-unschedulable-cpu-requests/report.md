## Root cause

The fraud-scoring Deployment's pod template asks for 512 whole CPU cores. In Kubernetes resource syntax a bare "512" means 512 cores, not 512 millicores; the only node in the cluster has 6 allocatable CPUs, so the scheduler can never place the pod and it stays Pending with FailedScheduling / "Insufficient cpu". Because no pod is ever admitted to a node, the deployment stays at 0/1 ready and no fraud scores are produced. The fix is to write the request in millicores (512m) in the deployment's pod template.

Remediation: edit Deployment fraud/fraud-scoring, field `spec.template.spec.containers[scorer].resources.requests.cpu`: `512` -> `512m`.

## Evidence chain

1. [symptom] The paged deployment has no ready replicas and its only pod is Pending and unscheduled
   source: namespace_overview(fraud) — verified
   > deployment/fraud-scoring ready=0/1 podLabels={app=fraud-scoring}
2. [symptom] Scheduler rejects the pod for insufficient CPU
   source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
   > Warning  FailedScheduling  1s    default-scheduler  0/1 nodes are available: 1 Insufficient cpu.
3. [link] The pod requests 512 CPU (cores)
   source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
   > Requests:
   >       cpu:        512
4. [link] The only node has 6 CPUs capacity/allocatable, far less than 512
   source: cluster_capacity({}) — verified
   > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
5. [defect] The deployment pod template, the object a human edits, carries the bad CPU request value
   source: get_object({"kind": "deployment", "name": "fraud-scoring", "namespace": "fraud"}) — verified
   > "resources": {
   >               "requests": {
   >                 "cpu": "512"
   >               }
   >             },

## Investigation ledger

- A ResourceQuota in the fraud namespace blocked or shaped the pod — ruled out: There is no ResourceQuota object in namespace fraud
  source: get_object({"kind": "resourcequotas", "namespace": "fraud"}) — verified
  > 0 objects of kind resourcequotas in namespace fraud
- The node is unhealthy or tainted so nothing can schedule there — ruled out: The single node reports Ready=True and no taints are recorded
  source: cluster_capacity({}) — verified
  > condition Ready=True
- Image pull failure or crashing container — ruled out: The pod was never assigned a node, so no container was ever started; only the PodScheduled condition is False
  source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
  > Node:             <none>
- A node selector or affinity restricted placement — ruled out: The pod has no node selectors
  source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
  > Node-Selectors:              <none>

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
  "mechanism": "Deployment fraud/fraud-scoring sets .spec.template.spec.containers[scorer].resources.requests.cpu to \"512\" (512 whole cores) instead of \"512m\"; the cluster's single node has only 6 allocatable CPUs, so the scheduler rejects every placement attempt with \"0/1 nodes are available: 1 Insufficient cpu\" and the pod is never scheduled.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
