## Root cause

StatefulSet analytics/metrics-db asks for a StorageClass that does not exist in this cluster. Its volumeClaimTemplate "data" sets spec.storageClassName: "fast-ssd", but the only StorageClass in the cluster is "standard" (rancher.io/local-path, the default). The generated PVC analytics/data-metrics-db-0 therefore stays Pending with ProvisioningFailed: storageclass.storage.k8s.io "fast-ssd" not found, so no PersistentVolume is ever bound. The scheduler refuses to place metrics-db-0 because of the unbound immediate PVC, so the pod stays Pending, the StatefulSet reports 0/1 Ready, and Service analytics/metrics-db has zero endpoint addresses, which is why the dashboards are empty. Fixing it means editing the StatefulSet's volumeClaimTemplate to name "standard" (or creating a StorageClass named fast-ssd); because volumeClaimTemplates are immutable, the StatefulSet must be recreated (kubectl delete sts metrics-db --cascade=orphan, then apply the corrected spec) and the stale Pending PVC deleted.

Remediation: edit StatefulSet analytics/metrics-db, field `spec.volumeClaimTemplates[0].spec.storageClassName`: `fast-ssd` -> `standard`.

## Evidence chain

1. [symptom] The paged StatefulSet has no ready replicas and its pod is unscheduled.
   source: namespace_overview(analytics) — verified
   > statefulset/metrics-db ready=0/1 podLabels={app=metrics-db}
2. [symptom] The Service backing analytics has no endpoints because the pod never becomes ready.
   source: namespace_overview(analytics) — verified
   > service/metrics-db selector={app=metrics-db} endpointAddresses=0
3. [defect] The StatefulSet's volumeClaimTemplate names a StorageClass 'fast-ssd'.
   source: get_object({"kind": "statefulsets", "name": "metrics-db", "namespace": "analytics"}) — verified
   > "storageClassName": "fast-ssd",
4. [link] The only StorageClass in the cluster is 'standard', the default one; 'fast-ssd' does not exist.
   source: cluster_capacity({}) — verified
   > storageclass/standard provisioner=rancher.io/local-path (default)
5. [link] The generated PVC is Pending because the named StorageClass is missing.
   source: describe({"kind": "persistentvolumeclaim", "name": "data-metrics-db-0", "namespace": "analytics"}) — verified
   > storageclass.storage.k8s.io "fast-ssd" not found
6. [link] The unbound PVC is the reason the pod cannot be scheduled.
   source: describe({"kind": "pod", "name": "metrics-db-0", "namespace": "analytics"}) — verified
   > 0/1 nodes are available: pod has unbound immediate PersistentVolumeClaims.

## Investigation ledger

- The pod is Pending because the node lacks capacity or carries a taint. — ruled out: The single node is Ready with full allocatable CPU, memory and 110 pod slots, and the scheduler's stated reason is the unbound PVC, not resources or taints.
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
- The Service selector does not match the pod labels, so endpoints are empty for a labeling reason. — ruled out: The Service selector {app=metrics-db} matches the StatefulSet pod template labels {app=metrics-db}; endpoints are empty only because the pod is Pending and unscheduled.
  source: namespace_overview(analytics) — verified
  > pod/metrics-db-0 phase=Pending labels={app=metrics-db, apps.kubernetes.io/pod-index=0, controller-revision-hash=metrics-db-579c7ff846, statefulset.kubernetes.io/pod-name=metrics-db-0} node=<unscheduled>
- The container image or command is broken (crash loop / image pull failure). — ruled out: The pod never got as far as running a container: it has no node and only a PodScheduled=False condition.
  source: describe({"kind": "pod", "name": "metrics-db-0", "namespace": "analytics"}) — verified
  > PodScheduled   False

## Verification recipe

1. `kubectl get pvc data-metrics-db-0 -n analytics -o yaml` — expect to see: storageclass.storage.k8s.io "fast-ssd" not found  [PRESENT]
2. `kubectl get storageclass` — expect to see: storageclass/standard provisioner=rancher.io/local-path (default)  [PRESENT]
3. `kubectl get statefulset metrics-db -n analytics -o jsonpath='{.spec.volumeClaimTemplates[0].spec.storageClassName}'` — expect to see: "storageClassName": "fast-ssd"  [PRESENT]
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
  "mechanism": "StatefulSet analytics/metrics-db sets .spec.volumeClaimTemplates[0].spec.storageClassName to \"fast-ssd\", but the cluster's only StorageClass is \"standard\"; the resulting claim analytics/data-metrics-db-0 is rejected with ProvisioningFailed \"storageclass.storage.k8s.io \\\"fast-ssd\\\" not found\", so no volume is provisioned and the scheduler rejects metrics-db-0 with \"pod has unbound immediate PersistentVolumeClaims\".",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
