## Root cause

The StatefulSet analytics/metrics-db asks for a StorageClass that does not exist in this cluster. Its volumeClaimTemplate "data" sets .spec.storageClassName to "fast-ssd", but the only StorageClass in the cluster is "standard" (the default, provisioner rancher.io/local-path). The PersistentVolumeClaim data-metrics-db-0 generated from that template therefore stays Pending with ProvisioningFailed: storageclass.storage.k8s.io "fast-ssd" not found, and the scheduler refuses to place the StatefulSet's pod ("pod has unbound immediate PersistentVolumeClaims"), so the workload stays at 0/1 Ready and the headless Service metrics-db has no endpoint addresses, which is why dashboards are empty. Fix the storageClassName in the volumeClaimTemplate to "standard"; because volumeClaimTemplates are immutable on a live StatefulSet, apply it by recreating the StatefulSet (kubectl delete statefulset metrics-db --cascade=orphan) and deleting the stuck PersistentVolumeClaim data-metrics-db-0 so it is regenerated against the correct class.

Remediation: edit StatefulSet analytics/metrics-db, field `spec.volumeClaimTemplates[0].spec.storageClassName`: `fast-ssd` -> `standard`.

## Evidence chain

1. [symptom] StatefulSet metrics-db is 0/1 Ready and its pod is unscheduled and Pending.
   source: namespace_overview(analytics) — verified
   > statefulset/metrics-db ready=0/1 podLabels={app=metrics-db}
2. [symptom] The pod cannot be scheduled because its PVC is unbound.
   source: describe({"kind": "pod", "name": "metrics-db-0", "namespace": "analytics"}) — verified
   > 0/1 nodes are available: pod has unbound immediate PersistentVolumeClaims.
3. [defect] The StatefulSet's volumeClaimTemplate requests StorageClass fast-ssd.
   source: get_object({"kind": "statefulsets", "name": "metrics-db", "namespace": "analytics"}) — verified
   > "storageClassName": "fast-ssd",
4. [link] The generated PVC is Pending because the named StorageClass does not exist.
   source: describe({"kind": "persistentvolumeclaim", "name": "data-metrics-db-0", "namespace": "analytics"}) — verified
   > Warning  ProvisioningFailed  1s (x2 over 1s)  persistentvolume-controller  storageclass.storage.k8s.io "fast-ssd" not found
5. [link] The only StorageClass in the cluster is standard, which is the default.
   source: cluster_capacity({}) — verified
   > storageclass/standard provisioner=rancher.io/local-path (default)

## Investigation ledger

- The Service selector does not match the pod labels, so endpoints are empty for a labeling reason. — ruled out: The Service selector {app=metrics-db} matches the pod labels exactly, so empty endpoints follow from having no running pod rather than from a selector mismatch.
  source: namespace_overview(analytics) — verified
  > service/metrics-db selector={app=metrics-db} endpointAddresses=0
- The node is out of capacity or tainted, so nothing can be scheduled. — ruled out: The single node is Ready with full allocatable CPU, memory and 110 pod slots and no taints listed; scheduling failed on volume binding, not capacity.
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
  >   condition Ready=True
- A missing or unbound ServiceAccount / projected token reference blocked the pod from starting. — ruled out: The pod resolves the default ServiceAccount and its projected token volume without error; the only failing condition is PodScheduled=False.
  source: describe({"kind": "pod", "name": "metrics-db-0", "namespace": "analytics"}) — verified
  > Service Account:  default

## Verification recipe

1. `kubectl -n analytics describe pvc data-metrics-db-0` — expect to see: storageclass.storage.k8s.io "fast-ssd" not found  [PRESENT]
2. `kubectl -n analytics get statefulset metrics-db -o json` — expect to see: "storageClassName": "fast-ssd"  [PRESENT]
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
  "mechanism": "The StatefulSet analytics/metrics-db sets .spec.volumeClaimTemplates[0].spec.storageClassName to \"fast-ssd\", a StorageClass that does not exist in this cluster, so the PersistentVolumeClaim it generates, data-metrics-db-0, is rejected by the persistentvolume-controller with ProvisioningFailed \"storageclass.storage.k8s.io \\\"fast-ssd\\\" not found\" and stays Pending and unbound. With that volume never binding, the StatefulSet's replica cannot be scheduled onto any node and the StatefulSet reports 0/1 Ready.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
