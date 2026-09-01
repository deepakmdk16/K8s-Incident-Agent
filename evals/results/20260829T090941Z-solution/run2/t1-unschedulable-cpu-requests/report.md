## Root cause

Deployment fraud/fraud-scoring asks for a CPU request of "512" in its pod template. In Kubernetes a bare number means whole CPU cores, so this is a request for 512 cores, almost certainly a typo for "512m" (half a core). The only node in the cluster, incident-lab-control-plane, reports cpu=6/6 capacity/allocatable, so no node can ever satisfy the request. The scheduler rejects the pod with "0/1 nodes are available: 1 Insufficient cpu", pod fraud/fraud-scoring-596445859d-thcdh stays Pending with PodScheduled False and Node: none, so the Deployment reports ready=0/1 and no fraud scores are produced. Changing the request to "512m" makes the pod schedulable on the existing node.

Remediation: edit Deployment fraud/fraud-scoring, field `spec.template.spec.containers[0].resources.requests.cpu`: `512` -> `512m`.

## Evidence chain

1. [symptom] The paged Deployment has no ready replicas.
   source: namespace_overview(fraud) — verified
   > deployment/fraud-scoring ready=0/1 podLabels={app=fraud-scoring}
2. [symptom] The pod is Pending with no node assigned.
   source: namespace_overview(fraud) — verified
   > pod/fraud-scoring-596445859d-thcdh phase=Pending labels={app=fraud-scoring, pod-template-hash=596445859d} node=<unscheduled>
3. [link] The scheduler rejects the pod for insufficient CPU.
   source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
   > 0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 Preemption is not helpful for scheduling.
4. [link] The pod requests 512 CPU cores.
   source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
   > Requests:
   >       cpu:        512
5. [link] The only node in the cluster has 6 allocatable CPU, far less than the request.
   source: cluster_capacity({}) — verified
   > cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
6. [defect] The Deployment pod template itself carries the oversized cpu request that a human must edit.
   source: get_object({"kind": "deployment", "name": "fraud-scoring", "namespace": "fraud"}) — verified
   > "resources": {
   >               "requests": {
   >                 "cpu": "512"
   >               }
   >             },

## Investigation ledger

- The pod is blocked by node taints or a nodeSelector rather than by its resource request — ruled out: The pod declares no node selector and only the default not-ready/unreachable tolerations, and the scheduler's stated reason is Insufficient cpu rather than an untolerated taint or selector mismatch.
  source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
  > Node-Selectors:              <none>
- A ResourceQuota or LimitRange in namespace fraud is blocking or rewriting the pod — ruled out: Besides the workload the namespace holds only a ConfigMap and a ServiceAccount; no ResourceQuota or LimitRange exists, and the pod was admitted and failed only at scheduling.
  source: namespace_overview(fraud) — verified
  > other objects: configmaps=1 serviceaccounts=1
- The container image or command is broken (ImagePullBackOff / CrashLoopBackOff) — ruled out: The pod never reached a node, so no image was pulled and no container started; its only condition is PodScheduled=False.
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
  "mechanism": "Deployment fraud/fraud-scoring sets .spec.template.spec.containers[0].resources.requests.cpu to \"512\", which is 512 whole cores rather than the intended \"512m\", exceeding the 6 allocatable CPU that exist in the cluster. The scheduler therefore cannot place the pod the Deployment creates and rejects it with \"0/1 nodes are available: 1 Insufficient cpu. preemption: 0/1 nodes are available: 1 Preemption is not helpful for scheduling.\", so pod fraud/fraud-scoring-596445859d-thcdh stays Pending with PodScheduled False and the Deployment stays at ready=0/1.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
