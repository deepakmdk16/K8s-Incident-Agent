## Root cause

The StatefulSet analytics/metrics-db declares a volumeClaimTemplate "data" with storageClassName "fast-ssd", but the only StorageClass in this cluster is "standard" (provisioner rancher.io/local-path, marked default). The PVC created from that template, data-metrics-db-0, therefore carries storageClassName "fast-ssd" and the persistentvolume-controller rejects it with ProvisioningFailed: storageclass.storage.k8s.io "fast-ssd" not found. With the claim stuck Pending and unbound, the scheduler cannot place the StatefulSet's pod (FailedScheduling: pod has unbound immediate PersistentVolumeClaims), so it stays Pending, the StatefulSet stays 0/1 Ready, and service/metrics-db has zero endpoint addresses, which is why the analytics dashboards are empty. The fix is to correct the storage class name in the StatefulSet's volumeClaimTemplate to "standard"; because volumeClaimTemplates and a bound PVC's storageClassName are immutable, the StatefulSet must be recreated (e.g. kubectl delete sts metrics-db --cascade=orphan) and the stale Pending PVC data-metrics-db-0 deleted so it is recreated against "standard".

Remediation: edit StatefulSet analytics/metrics-db, field `spec.volumeClaimTemplates[0].spec.storageClassName`: `fast-ssd` -> `standard`.

## Evidence chain

1. [symptom] The paged StatefulSet has no ready replica and its only pod is unscheduled.
   source: namespace_overview(analytics) — verified
   > statefulset/metrics-db ready=0/1 podLabels={app=metrics-db}
2. [symptom] Service metrics-db has no endpoint addresses, so dashboards get nothing.
   source: namespace_overview(analytics) — verified
   > service/metrics-db selector={app=metrics-db} endpointAddresses=0
3. [defect] The StatefulSet's volumeClaimTemplate asks for StorageClass fast-ssd.
   source: describe({"kind": "statefulset", "name": "metrics-db", "namespace": "analytics"}) — verified
   > Volume Claims:
   >   Name:          data
   >   StorageClass:  fast-ssd
4. [link] The claim generated from that template carries storageClassName fast-ssd.
   source: get_object({"kind": "persistentvolumeclaims", "namespace": "analytics"}) — verified
   > "storageClassName": "fast-ssd"
5. [defect] Provisioning of the claim fails because that StorageClass does not exist.
   source: describe({"kind": "persistentvolumeclaim", "name": "data-metrics-db-0", "namespace": "analytics"}) — verified
   > Warning  ProvisioningFailed  1s (x2 over 1s)  persistentvolume-controller  storageclass.storage.k8s.io "fast-ssd" not found
6. [link] The only StorageClass present in the cluster is standard, the default.
   source: cluster_capacity({}) — verified
   > storageclass/standard provisioner=rancher.io/local-path (default)
7. [link] The unbound claim is what keeps the pod from being scheduled.
   source: describe({"kind": "pod", "name": "metrics-db-0", "namespace": "analytics"}) — verified
   > Warning  FailedScheduling  0s    default-scheduler  0/1 nodes are available: pod has unbound immediate PersistentVolumeClaims. not found

## Investigation ledger

- The node lacked capacity or carried a taint that blocked scheduling. — ruled out: The single node is Ready with full allocatable CPU, memory and 110 pod slots and no taints listed; scheduling failed on the volume, not on capacity.
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
  >   condition Ready=True
- The Service selector does not match the pod labels, so endpoints are empty for a labeling reason. — ruled out: The Service selector app=metrics-db exactly matches the pod labels; the replica simply never reaches Running/Ready because it is Pending and unscheduled.
  source: namespace_overview(analytics) — verified
  > service/metrics-db selector={app=metrics-db} endpointAddresses=0
- The container image or command is broken (bad image pull or crash loop). — ruled out: No container was ever started: the pod has no node and only a PodScheduled=False condition, so no image pull or command execution was attempted.
  source: describe({"kind": "pod", "name": "metrics-db-0", "namespace": "analytics"}) — verified
  > Conditions:
  >   Type           Status
  >   PodScheduled   False

## Verification recipe

1. `kubectl describe pvc data-metrics-db-0 -n analytics` — expect to see: storageclass.storage.k8s.io "fast-ssd" not found  [PRESENT]
2. `kubectl describe statefulset metrics-db -n analytics` — expect to see: StorageClass:  fast-ssd  [PRESENT]
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
  "mechanism": "StatefulSet analytics/metrics-db sets .spec.volumeClaimTemplates[0].spec.storageClassName to \"fast-ssd\", a StorageClass that does not exist in this cluster (the only one is \"standard\"), so the claim generated from that template, data-metrics-db-0, is rejected by the persistentvolume-controller with ProvisioningFailed \"storageclass.storage.k8s.io \\\"fast-ssd\\\" not found\" and never binds, leaving the StatefulSet's replica unschedulable for want of a bound volume.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
