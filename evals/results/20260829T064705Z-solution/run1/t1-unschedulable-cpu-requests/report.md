## Root cause

The fraud-scoring Deployment's pod template asks for 512 whole CPU cores. Kubernetes reads an unsuffixed "512" as 512 cores, not 512 millicores; the only node in this cluster has 6 allocatable CPUs, so the scheduler can never place the pod and it stays Pending with FailedScheduling / "Insufficient cpu". Because no pod is ever scheduled, the deployment stays at 0/1 ready and no fraud scores are produced. The fix is to change the request to 512m (or another value that fits within node allocatable CPU) in the Deployment pod template; the pod itself is disposable and will be recreated by the ReplicaSet.

Remediation: edit Deployment fraud/fraud-scoring, field `spec.template.spec.containers[scorer].resources.requests.cpu`: `512` -> `512m`.

## Evidence chain

1. [symptom] The paged deployment has no ready replicas and its only pod is Pending and unscheduled.
   source: namespace_overview(fraud) — verified
   > deployment/fraud-scoring ready=0/1 podLabels={app=fraud-scoring}
2. [symptom] The scheduler cannot place the pod due to insufficient CPU.
   source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
   > Warning  FailedScheduling  1s    default-scheduler  0/1 nodes are available: 1 Insufficient cpu.
3. [link] The pod requests 512 CPU (cores, not millicores).
   source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
   > Requests:
   >       cpu:        512
4. [link] The only node in the cluster has 6 allocatable CPUs, far below the 512 requested.
   source: cluster_capacity({}) — verified
   > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
5. [defect] The Deployment pod template itself carries the bad CPU request, so recreating the pod cannot help.
   source: get_object({"kind": "deployments", "name": "fraud-scoring", "namespace": "fraud"}) — verified
   > "resources": {
   >               "requests": {
   >                 "cpu": "512"
   >               }
   >             },

## Investigation ledger

- Node is unavailable, tainted or NotReady, preventing scheduling — ruled out: The single node is Ready and reports no taints, so the only scheduling obstacle is the CPU request size.
  source: cluster_capacity({}) — verified
  > condition Ready=True
- Pod is failing on a missing image, config, secret or service account reference — ruled out: The pod never got scheduled at all - its only condition is PodScheduled=False with no container-level waiting reason - so no reference resolution ever occurred.
  source: describe({"kind": "pod", "name": "fraud-scoring-596445859d-thcdh", "namespace": "fraud"}) — verified
  > Conditions:
  >   Type           Status
  >   PodScheduled   False
- A Service selector mismatch or missing endpoints is the cause of missing scores — ruled out: The fraud namespace contains no Service objects at all; only a configmap and serviceaccount accompany the deployment and its pod.
  source: namespace_overview(fraud) — verified
  > other objects: configmaps=1 serviceaccounts=1

## Verification recipe

1. `kubectl -n fraud get deploy fraud-scoring -o jsonpath='{.spec.template.spec.containers[0].resources.requests.cpu}'` — expect to see: "cpu": "512"  [PRESENT]
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
  "mechanism": "Deployment fraud/fraud-scoring sets .spec.template.spec.containers[scorer].resources.requests.cpu to \"512\", which Kubernetes interprets as 512 whole cores rather than the intended \"512m\"; the cluster's single node has cpu=6 allocatable, so the scheduler rejects the pod with FailedScheduling \"0/1 nodes are available: 1 Insufficient cpu\" and it stays Pending, unscheduled.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
