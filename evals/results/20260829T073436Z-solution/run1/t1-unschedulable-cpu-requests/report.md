## Root cause

The Deployment fraud/fraud-scoring asks for a CPU request that no node in this cluster can satisfy. Its pod template sets spec.template.spec.containers[scorer].resources.requests.cpu to "512", which Kubernetes reads as 512 whole CPU cores, not 512 millicores. The only node in the cluster, incident-lab-control-plane, has cpu=6/6 capacity/allocatable, so the scheduler rejects the pod with "0/1 nodes are available: 1 Insufficient cpu" and the ReplicaSet's pod fraud-scoring-596445859d-thcdh stays Pending with PodScheduled=False and never gets a node, leaving the Deployment at ready=0/1 and producing no fraud scores. The pattern indicates a dropped "m" unit suffix: the intended value "512m" (0.512 cores) fits on the node.

Remediation: edit Deployment fraud/fraud-scoring, field `spec.template.spec.containers[scorer].resources.requests.cpu`: `"512" (512 whole CPU cores)` -> `"512m" (0.512 CPU cores), or any value that fits within the node's 6 allocatable CPUs`.

## Evidence chain

1. [symptom] The paged Deployment has no ready replicas and its only pod is unscheduled.
   source: namespace_overview(fraud) — verified
   > deployment/fraud-scoring ready=0/1 podLabels={app=fraud-scoring}
2. [symptom] The pod is Pending and has no node assigned.
   source: namespace_overview(fraud) — verified
   > pod/fraud-scoring-596445859d-thcdh phase=Pending labels={app=fraud-scoring, pod-template-hash=596445859d} node=<unscheduled>
3. [link] The scheduler rejects the pod for insufficient CPU, and preemption cannot help.
   source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
   > 0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 Preemption is not helpful for scheduling.
4. [link] The pod as admitted requests 512 CPU (cores).
   source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
   > Requests:
   >       cpu:        512
5. [defect] The Deployment pod template is the object carrying the bad request value.
   source: get_object({"kind": "deployments", "name": "fraud-scoring", "namespace": "fraud"}) — verified
   > "resources": {
   >               "requests": {
   >                 "cpu": "512"
   >               }
   >             },
6. [link] The only node in the cluster has 6 allocatable CPUs, far less than the 512 requested.
   source: cluster_capacity({}) — verified
   > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)

## Investigation ledger

- The pod is unschedulable because of node taints, NotReady node, or a nodeSelector mismatch rather than resources — ruled out: The single node reports Ready=True with no taints listed, and the pod specifies no node selector, so only the CPU request blocks scheduling.
  source: cluster_capacity({}) — verified
  > condition Ready=True
- A namespace ResourceQuota or LimitRange is rejecting or inflating the pod's resource request — ruled out: The fraud namespace contains only a ConfigMap and a ServiceAccount besides the workload; no ResourceQuota or LimitRange objects exist there.
  source: namespace_overview(fraud) — verified
  > other objects: configmaps=1 serviceaccounts=1
- The container image or command is broken (bad image pull, crash loop) — ruled out: The pod never reached a node, so no container was ever created or pulled; its only condition is PodScheduled=False.
  source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
  > Conditions:
  >   Type           Status
  >   PodScheduled   False

## Verification recipe

1. `kubectl -n fraud get deployment fraud-scoring -o jsonpath='{.spec.template.spec.containers[0].resources.requests.cpu}'` — expect to see: "cpu": "512"  [PRESENT]
2. `kubectl -n fraud describe pod fraud-scoring-596445859d-thcdh` — expect to see: 0/1 nodes are available: 1 Insufficient cpu.  [PRESENT]
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
  "mechanism": "The Deployment fraud/fraud-scoring sets .spec.template.spec.containers[scorer].resources.requests.cpu to \"512\", i.e. 512 whole cores, where \"512m\" (0.512 cores) was intended; the cluster's single node offers only cpu=6 allocatable, so the scheduler admits no node for the Deployment's pod, which is rejected with \"Insufficient cpu\" and held Pending with PodScheduled=False, keeping the Deployment at 0/1 ready replicas.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
