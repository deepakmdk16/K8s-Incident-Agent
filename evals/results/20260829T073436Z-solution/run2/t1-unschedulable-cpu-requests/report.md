## Root cause

The Deployment fraud/fraud-scoring asks for 512 whole CPU cores per pod. Its pod template sets .spec.template.spec.containers[scorer].resources.requests.cpu to the value "512", which Kubernetes reads as 512 cores, where the intended value is "512m" (half a core). The only node in the cluster, incident-lab-control-plane, has six allocatable CPUs, so the scheduler cannot place the pod on any node and the pod fraud-scoring-596445859d-thcdh stays Pending with PodScheduled=False and a FailedScheduling event reading "Insufficient cpu". Because no pod is ever admitted, the Deployment reports ready=0/1 and no fraud scores are produced. There is no ResourceQuota or LimitRange in the namespace and the node is Ready and untainted, so nothing other than the oversized CPU request blocks scheduling.

Remediation: edit Deployment fraud/fraud-scoring, field `spec.template.spec.containers[scorer].resources.requests.cpu`: `512` -> `512m`.

## Evidence chain

1. [symptom] The Deployment has no ready replicas and its only pod is unscheduled.
   source: namespace_overview(fraud) — verified
   > deployment/fraud-scoring ready=0/1 podLabels={app=fraud-scoring}
2. [symptom] The pod is Pending and has never been assigned a node.
   source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
   > Status:           Pending
3. [link] The scheduler rejects the pod for lack of CPU on the single node.
   source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
   > 0/1 nodes are available: 1 Insufficient cpu.
4. [link] The pod requests 512 CPU units (cores).
   source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
   > cpu:        512
5. [defect] The Deployment pod template carries the oversized CPU request that a human must edit.
   source: get_object({"kind": "deployments", "name": "fraud-scoring", "namespace": "fraud"}) — verified
   > "requests": {
   >                 "cpu": "512"
   >               }
6. [link] The only node in the cluster has six CPUs allocatable, far below the request.
   source: cluster_capacity({}) — verified
   > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)

## Investigation ledger

- A ResourceQuota or LimitRange in the fraud namespace blocking or mutating the pod — ruled out: No ResourceQuota and no LimitRange objects exist in the namespace, so admission policy is not involved.
  source: get_object({"kind": "limitranges", "namespace": "fraud"}) — verified
  > 0 objects of kind limitranges in namespace fraud
- The node is NotReady or tainted so nothing can schedule there — ruled out: The single node reports Ready=True with no taints listed, so node health is not the blocker.
  source: cluster_capacity({}) — verified
  > condition Ready=True
- An image pull failure or crashing container inside the pod — ruled out: The pod was never scheduled to a node, so no container was ever started; the only condition is PodScheduled=False.
  source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
  > PodScheduled   False

## Verification recipe

1. `kubectl -n fraud get deployment fraud-scoring -o jsonpath='{.spec.template.spec.containers[0].resources.requests.cpu}'` — expect to see: "cpu": "512"  [PRESENT]
2. `kubectl -n fraud describe pod fraud-scoring-596445859d-thcdh` — expect to see: 0/1 nodes are available: 1 Insufficient cpu.  [PRESENT]
3. `kubectl describe nodes | grep -A3 Allocatable` — expect to see: cpu=6/6  [PRESENT]
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
  "mechanism": "The Deployment fraud/fraud-scoring sets .spec.template.spec.containers[scorer].resources.requests.cpu to \"512\" \u2014 512 whole cores instead of the intended \"512m\" \u2014 which exceeds the six allocatable CPUs of the cluster's only node, so its pod is rejected by the scheduler with FailedScheduling \"Insufficient cpu\" and stays Pending with PodScheduled=False, leaving the Deployment at zero of one replicas ready.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
