## Root cause

Deployment fraud/fraud-scoring asks for a CPU request of "512" in its pod template. In Kubernetes that value means 512 whole cores, not 512 millicores; the "m" suffix is missing. The only node in the cluster has cpu=6/6 capacity/allocatable, so no node can satisfy the request and the default scheduler rejects the pod with "0/1 nodes are available: 1 Insufficient cpu". The pod fraud/fraud-scoring-596445859d-thcdh therefore stays in phase Pending with PodScheduled False and never runs, so the Deployment reports ready=0/1 and the risk team receives no fraud scores. Setting the request to "512m" makes the pod schedulable.

Remediation: edit Deployment fraud/fraud-scoring, field `spec.template.spec.containers[scorer].resources.requests.cpu`: `512` -> `512m`.

## Evidence chain

1. [symptom] The paged Deployment has no ready replicas and its only pod is Pending and unscheduled.
   source: namespace_overview(fraud) — verified
   > deployment/fraud-scoring ready=0/1 podLabels={app=fraud-scoring}
2. [symptom] The pod is Pending with no node assigned.
   source: namespace_overview(fraud) — verified
   > pod/fraud-scoring-596445859d-thcdh phase=Pending labels={app=fraud-scoring, pod-template-hash=596445859d} node=<unscheduled>
3. [link] The scheduler rejects the pod for insufficient CPU.
   source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
   > Warning  FailedScheduling  1s    default-scheduler  0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 Preemption is not helpful for scheduling.
4. [link] The admitted CPU request on the running spec is 512 cores.
   source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
   > cpu:        512
5. [defect] The Deployment pod template requests cpu "512" (whole cores) rather than "512m".
   source: get_object({"kind": "deployments", "name": "fraud-scoring", "namespace": "fraud"}) — verified
   > "requests": {
   >                 "cpu": "512"
   >               }
6. [link] The cluster's only node has 6 allocatable CPUs, far less than the request.
   source: cluster_capacity({}) — verified
   > cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)

## Investigation ledger

- The node is unhealthy or tainted so nothing can schedule there — ruled out: The node reports Ready=True and no taints are listed, and the pod has no node selector; the scheduler's only complaint is CPU.
  source: cluster_capacity({}) — verified
  > condition Ready=True
- A ResourceQuota or LimitRange in the namespace blocks or inflates the pod — ruled out: The namespace contains only a ConfigMap and a ServiceAccount besides the workload; no quota or limitrange objects exist.
  source: namespace_overview(fraud) — verified
  > other objects: configmaps=1 serviceaccounts=1
- The container image or command is broken (crash loop / pull failure) — ruled out: The pod never got a node, so no container ever started; its only condition is PodScheduled False.
  source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
  > PodScheduled   False

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
  "mechanism": "Deployment fraud/fraud-scoring sets .spec.template.spec.containers[scorer].resources.requests.cpu to \"512\" \u2014 512 whole cores \u2014 where \"512m\" (0.512 core) was intended, which exceeds the cpu=6/6 capacity/allocatable of every node in the cluster. The default scheduler therefore cannot place the replica and keeps emitting FailedScheduling \"0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 Preemption is not helpful for scheduling.\", so the Deployment stays at ready=0/1 with its replica stuck in phase Pending and PodScheduled False.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
