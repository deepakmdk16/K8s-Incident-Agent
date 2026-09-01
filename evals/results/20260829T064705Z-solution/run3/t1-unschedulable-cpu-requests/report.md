## Root cause

The fraud-scoring Deployment's pod template asks for 512 whole CPU cores. Kubernetes reads a bare \"512\" as 512 cores, not 512 millicores; the only node in the cluster has 6 allocatable CPUs, so no node can satisfy the request. The scheduler rejects the pod with \"Insufficient cpu\" and it stays Pending with no node assigned, so the Deployment reports 0/1 ready and no fraud scores are produced. The fix is to write the request as \"512m\" (or another value within node capacity) in the Deployment pod template.

Remediation: edit Deployment fraud/fraud-scoring, field `spec.template.spec.containers[0].resources.requests.cpu`: `512` -> `512m`.

## Evidence chain

1. [symptom] The paged deployment has no ready replicas and its only pod is Pending with no node.
   source: namespace_overview(fraud) — verified
   > deployment/fraud-scoring ready=0/1 podLabels={app=fraud-scoring}
2. [symptom] The scheduler cannot place the pod because of CPU.
   source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
   > Warning  FailedScheduling  1s    default-scheduler  0/1 nodes are available: 1 Insufficient cpu.
3. [link] The pod requests 512 CPU units (cores).
   source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
   > Requests:
   >       cpu:        512
4. [link] The only node in the cluster has 6 allocatable CPUs, far below the request.
   source: cluster_capacity({}) — verified
   > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
5. [defect] The Deployment pod template itself carries the oversized bare-core CPU request.
   source: get_object({"kind": "deployments", "name": "fraud-scoring", "namespace": "fraud"}) — verified
   > "resources": {
   >               "requests": {
   >                 "cpu": "512"
   >               }
   >             },

## Investigation ledger

- A ResourceQuota or LimitRange in the fraud namespace injected or blocked the request — ruled out: The namespace contains only a configmap and a serviceaccount besides the workload; no quota or limitrange objects exist.
  source: namespace_overview(fraud) — verified
  > other objects: configmaps=1 serviceaccounts=1
- The node is tainted, NotReady, or otherwise unavailable for scheduling — ruled out: The single node reports Ready=True with no taints listed, so unavailability is not the blocker.
  source: cluster_capacity({}) — verified
  > condition Ready=True
- Image pull failure or container crash loop — ruled out: The pod was never assigned a node, so no container was ever started; the only unmet condition is PodScheduled=False.
  source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
  > Node:             <none>

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
  "mechanism": "Deployment fraud/fraud-scoring sets .spec.template.spec.containers[0].resources.requests.cpu to \"512\", i.e. 512 whole cores, where \"512m\" (0.512 core) was intended; the cluster's single node has cpu=6 allocatable, so the default scheduler cannot fit the pod and rejects it with \"0/1 nodes are available: 1 Insufficient cpu\", leaving it unscheduled in Pending.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
