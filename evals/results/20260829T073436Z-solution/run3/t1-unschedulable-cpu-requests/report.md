## Root cause

The Deployment fraud/fraud-scoring asks for 512 whole CPU cores per pod. Its pod template sets .spec.template.spec.containers[scorer].resources.requests.cpu to "512", which Kubernetes reads as 512 cores, not the 512 millicores ("512m") that was clearly intended. The only node in the cluster, incident-lab-control-plane, has 6 allocatable CPUs, so no node can satisfy the request and the scheduler rejects the pod with FailedScheduling / "Insufficient cpu". The pod fraud-scoring-596445859d-thcdh therefore stays Pending with PodScheduled=False and never runs, so the Deployment reports 0/1 ready and no fraud scores are produced. Fixing the unit on the CPU request in the Deployment's pod template (512m) makes the pod fit on the existing node.

Remediation: edit Deployment fraud/fraud-scoring, field `spec.template.spec.containers[scorer].resources.requests.cpu`: `512` -> `512m`.

## Evidence chain

1. [symptom] The paged Deployment has no ready replicas and its only pod is Pending and unscheduled.
   source: namespace_overview(fraud) — verified
   > deployment/fraud-scoring ready=0/1 podLabels={app=fraud-scoring}
2. [symptom] The pod was never assigned a node; scheduling failed for lack of CPU.
   source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
   > Warning  FailedScheduling  1s    default-scheduler  0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 Preemption is not helpful for scheduling.
3. [link] The pod requests 512 CPU (cores), as rendered from the Deployment template.
   source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
   > Requests:
   >       cpu:        512
4. [defect] The Deployment pod template is the object carrying the wrong-unit CPU request.
   source: get_object({"kind": "deployment", "name": "fraud-scoring", "namespace": "fraud"}) — verified
   > "resources": {
   >               "requests": {
   >                 "cpu": "512"
   >               }
   >             },
5. [link] The only node in the cluster has 6 allocatable CPUs, far less than the 512 cores requested.
   source: cluster_capacity({}) — verified
   > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)

## Investigation ledger

- The pod is blocked by a node taint or an unschedulable/unhealthy node. — ruled out: The single node is Ready and the scheduler's rejection reason is CPU, not taints; the pod also carries only the default not-ready/unreachable tolerations.
  source: cluster_capacity({}) — verified
  > condition Ready=True
- A nodeSelector or affinity in the pod template excludes the node. — ruled out: The pod has no node selector at all, so no placement constraint other than resources applies.
  source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
  > Node-Selectors:              <none>
- A ResourceQuota or LimitRange in the fraud namespace is injecting or blocking the request. — ruled out: The namespace contains no ResourceQuota or LimitRange objects — only a ConfigMap and a ServiceAccount besides the workload.
  source: namespace_overview(fraud) — verified
  > other objects: configmaps=1 serviceaccounts=1
- Image pull failure or a crashing container is keeping the workload at 0/1. — ruled out: The pod never reached a node, so no image pull or container start was ever attempted; its only condition is PodScheduled=False.
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
  "mechanism": "The Deployment fraud/fraud-scoring sets .spec.template.spec.containers[scorer].resources.requests.cpu to \"512\" \u2014 512 whole cores \u2014 where \"512m\" (512 millicores) was intended, and the cluster's single node offers only 6 allocatable CPUs. The scheduler cannot fit the pod on any node, so the Deployment's replica is rejected at scheduling with \"Insufficient cpu\", stays Pending with PodScheduled=False, and the Deployment remains at 0/1 ready.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
