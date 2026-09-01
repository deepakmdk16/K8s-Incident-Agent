## Root cause

The StatefulSet analytics/metrics-db requests a StorageClass that does not exist in this cluster. Its volumeClaimTemplate "data" sets spec.storageClassName to "fast-ssd", but the only StorageClass in the cluster is "standard" (the default, provisioner rancher.io/local-path). The generated PersistentVolumeClaim analytics/data-metrics-db-0 therefore stays Pending with ProvisioningFailed "storageclass.storage.k8s.io \"fast-ssd\" not found", and the scheduler refuses to place the pod ("pod has unbound immediate PersistentVolumeClaims"), so the StatefulSet stays at 0/1 Ready and Service analytics/metrics-db has no endpoint addresses, leaving dashboards empty. Fix: set the volumeClaimTemplate storageClassName to "standard" (volumeClaimTemplates are immutable, so the StatefulSet must be recreated — e.g. kubectl delete statefulset metrics-db --cascade=orphan, delete the pending PVC, then re-apply the corrected manifest).

Remediation: edit StatefulSet analytics/metrics-db, field `spec.volumeClaimTemplates[0].spec.storageClassName`: `fast-ssd` -> `standard`.

## Evidence chain

1. [symptom] The paged StatefulSet is 0/1 Ready and its only pod is unscheduled.
   source: namespace_overview(analytics) — verified
   > statefulset/metrics-db ready=0/1 podLabels={app=metrics-db}
2. [symptom] The pod cannot be scheduled because its PVC is unbound.
   source: describe({"kind": "pod", "name": "metrics-db-0", "namespace": "analytics"}) — verified
   > Warning  FailedScheduling  0s    default-scheduler  0/1 nodes are available: pod has unbound immediate PersistentVolumeClaims. not found
3. [link] The pod mounts the PVC data-metrics-db-0 generated from the StatefulSet volumeClaimTemplate.
   source: describe({"kind": "pod", "name": "metrics-db-0", "namespace": "analytics"}) — verified
   > ClaimName:  data-metrics-db-0
4. [link] That PVC is Pending and provisioning failed because the requested StorageClass does not exist.
   source: describe({"kind": "persistentvolumeclaims", "name": "data-metrics-db-0", "namespace": "analytics"}) — verified
   > Warning  ProvisioningFailed  1s (x2 over 1s)  persistentvolume-controller  storageclass.storage.k8s.io "fast-ssd" not found
5. [defect] The StatefulSet volumeClaimTemplate names the nonexistent StorageClass fast-ssd.
   source: get_object({"kind": "statefulsets", "name": "metrics-db", "namespace": "analytics"}) — verified
   > "storageClassName": "fast-ssd",
6. [defect] The only StorageClass present in the cluster is standard, which is the default.
   source: cluster_capacity({}) — verified
   > storageclass/standard provisioner=rancher.io/local-path (default)

## Investigation ledger

- Node capacity or taints prevented scheduling — ruled out: The single node is Ready with full allocatable capacity and no taints listed; the scheduler's only stated reason was the unbound PVC.
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
  >   condition Ready=True
- The Service selector does not match the pod labels, so endpoints are empty for a labelling reason — ruled out: The Service selector {app=metrics-db} matches the workload pod labels {app=metrics-db}; there are simply no running pods to become endpoints.
  source: namespace_overview(analytics) — verified
  > service/metrics-db selector={app=metrics-db} endpointAddresses=0
- The container image or command is broken (CrashLoopBackOff / ImagePullBackOff) — ruled out: No container was ever started: the pod is Pending with PodScheduled False, so no image pull or command failure is involved.
  source: describe({"kind": "pod", "name": "metrics-db-0", "namespace": "analytics"}) — verified
  > Status:           Pending

## Verification recipe

1. `kubectl -n analytics describe pvc data-metrics-db-0` — expect to see: storageclass.storage.k8s.io "fast-ssd" not found  [PRESENT]
2. `kubectl -n analytics get statefulset metrics-db -o jsonpath='{.spec.volumeClaimTemplates[0].spec.storageClassName}'` — expect to see: "storageClassName": "fast-ssd",  [PRESENT]
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
  "mechanism": "The StatefulSet analytics/metrics-db sets .spec.volumeClaimTemplates[0].spec.storageClassName to \"fast-ssd\", a StorageClass that does not exist in this cluster (the only one is \"standard\"), so the PersistentVolumeClaim it generates, analytics/data-metrics-db-0, is rejected by the provisioner with ProvisioningFailed \"storageclass.storage.k8s.io \\\"fast-ssd\\\" not found\" and never binds. With that claim unbound, the StatefulSet's replica cannot be admitted to a node and the controller holds at 0/1 Ready.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
