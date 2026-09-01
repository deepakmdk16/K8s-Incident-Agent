## Root cause

The StatefulSet analytics/metrics-db asks for a StorageClass that does not exist in this cluster. Its volumeClaimTemplate sets spec.storageClassName to \"fast-ssd\", but the only StorageClass present is \"standard\" (the default, provisioner rancher.io/local-path). The StatefulSet controller therefore created PVC data-metrics-db-0 with storageClassName fast-ssd, and the persistentvolume-controller rejected it with ProvisioningFailed: storageclass.storage.k8s.io \"fast-ssd\" not found, so the claim stays Pending and unbound. With no bound volume, the scheduler refuses the replica (\"pod has unbound immediate PersistentVolumeClaims\"), the pod stays Pending, the StatefulSet reports 0/1 Ready, and service/metrics-db has zero endpoint addresses, which is why dashboards are empty. Fix: set the volumeClaimTemplate's storageClassName to \"standard\"; because volumeClaimTemplates and a PVC's storageClassName are immutable, the StatefulSet must be deleted and reapplied with the corrected value, along with deleting the stale Pending PVC.

Remediation: edit StatefulSet analytics/metrics-db, field `spec.volumeClaimTemplates[0].spec.storageClassName`: `fast-ssd` -> `standard`.

## Evidence chain

1. [symptom] The paged StatefulSet reports 0/1 ready and its only replica is Pending and unscheduled.
   source: namespace_overview(analytics) — verified
   > statefulset/metrics-db ready=0/1 podLabels={app=metrics-db}
2. [symptom] Scheduling is blocked by an unbound PVC.
   source: describe({"kind": "pod", "name": "metrics-db-0", "namespace": "analytics"}) — verified
   > Warning  FailedScheduling  0s    default-scheduler  0/1 nodes are available: pod has unbound immediate PersistentVolumeClaims. not found
3. [link] The PVC created from the StatefulSet's volumeClaimTemplate is Pending because its StorageClass does not exist.
   source: describe({"kind": "persistentvolumeclaims", "name": "data-metrics-db-0", "namespace": "analytics"}) — verified
   > Warning  ProvisioningFailed  1s (x2 over 1s)  persistentvolume-controller  storageclass.storage.k8s.io "fast-ssd" not found
4. [defect] The StatefulSet's volumeClaimTemplate hard-codes storageClassName fast-ssd.
   source: get_object({"kind": "statefulsets", "name": "metrics-db", "namespace": "analytics"}) — verified
   > "storageClassName": "fast-ssd",
5. [defect] The only StorageClass in the cluster is standard, which is the default.
   source: cluster_capacity({}) — verified
   > storageclass/standard provisioner=rancher.io/local-path (default)

## Investigation ledger

- The replica is Pending because the node lacks capacity or carries a taint. — ruled out: The single node is Ready with full allocatable CPU/memory and no taints listed; the scheduler's only complaint is the unbound claim, not resources.
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
  >   condition Ready=True
- The Service selector does not match the pod labels, so endpoints are empty independently of storage. — ruled out: service/metrics-db selects app=metrics-db and the pod carries app=metrics-db; the endpoint count is zero only because no pod is running and ready.
  source: namespace_overview(analytics) — verified
  > service/metrics-db selector={app=metrics-db} endpointAddresses=0
- The container image or command is broken (CrashLoopBackOff / ImagePullBackOff). — ruled out: The replica never reaches a node at all - PodScheduled is False and no container status exists - so image or command failure cannot be the cause.
  source: describe({"kind": "pod", "name": "metrics-db-0", "namespace": "analytics"}) — verified
  > Conditions:
  >   Type           Status
  >   PodScheduled   False

## Verification recipe

1. `kubectl describe pvc data-metrics-db-0 -n analytics` — expect to see: storageclass.storage.k8s.io "fast-ssd" not found  [PRESENT]
2. `kubectl get statefulset metrics-db -n analytics -o jsonpath='{.spec.volumeClaimTemplates[0].spec.storageClassName}'` — expect to see: "storageClassName": "fast-ssd"  [PRESENT]
3. `kubectl get storageclass` — expect to see: storageclass/standard provisioner=rancher.io/local-path (default)  [PRESENT]
(Each command was re-executed against the captured cluster state; PRESENT/ABSENT is the measured result, not a prediction.)

Verdict: confirmed. Every quote below was re-checked against the tool output it cites, a defect observation names the failing object, an alternative was ruled out with evidence, and every verification command was re-run and found present.

```json
{
  "case_id": "t1-pvc-storageclass-typo",
  "failing_resource": {
    "kind": "StatefulSet",
    "namespace": "analytics",
    "name": "metrics-db"
  },
  "mechanism": "StatefulSet analytics/metrics-db sets .spec.volumeClaimTemplates[0].spec.storageClassName to \"fast-ssd\", a StorageClass that does not exist in this cluster (the only one is \"standard\"), so the claim generated from that template is rejected by the persistentvolume-controller with ProvisioningFailed \"storageclass.storage.k8s.io \\\"fast-ssd\\\" not found\" and never binds a volume.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
