## Root cause

The StatefulSet analytics/metrics-db asks for a StorageClass that does not exist in this cluster. Its .spec.volumeClaimTemplates[0].spec.storageClassName is "fast-ssd", but the only StorageClass present is "standard" (provisioner rancher.io/local-path, marked default). The StatefulSet controller created the PersistentVolumeClaim data-metrics-db-0 from that template, and the persistentvolume-controller rejects it with ProvisioningFailed: storageclass.storage.k8s.io \"fast-ssd\" not found, so the claim stays Pending with no volume bound. The pod metrics-db-0 mounts that claim, so the scheduler refuses it with \"pod has unbound immediate PersistentVolumeClaims\" and the pod stays Pending and unscheduled, leaving the StatefulSet at 0/1 Ready and the Service metrics-db with zero endpoint addresses, which is why dashboards are empty. Fix the template to name the existing StorageClass \"standard\" (or drop the field so the default class is inherited). Because volumeClaimTemplates are immutable on a live StatefulSet, apply the corrected spec after deleting the StatefulSet with --cascade=orphan and deleting the Pending PersistentVolumeClaim data-metrics-db-0, so the claim is recreated against the valid class.

Remediation: edit StatefulSet analytics/metrics-db, field `.spec.volumeClaimTemplates[0].spec.storageClassName`: `fast-ssd` -> `standard`.

## Evidence chain

1. [symptom] The paged StatefulSet is 0/1 Ready and its pod is Pending and unscheduled.
   source: namespace_overview(analytics) — verified
   > statefulset/metrics-db ready=0/1 podLabels={app=metrics-db}
2. [symptom] The Service has no endpoint addresses, matching the empty-dashboard symptom.
   source: namespace_overview(analytics) — verified
   > service/metrics-db selector={app=metrics-db} endpointAddresses=0
3. [defect] The StatefulSet's volume claim template names StorageClass fast-ssd.
   source: get_object({"kind": "statefulset", "name": "metrics-db", "namespace": "analytics"}) — verified
   > "storageClassName": "fast-ssd",
4. [defect] The generated PersistentVolumeClaim fails provisioning because that StorageClass does not exist.
   source: describe({"kind": "persistentvolumeclaim", "name": "data-metrics-db-0", "namespace": "analytics"}) — verified
   > storageclass.storage.k8s.io "fast-ssd" not found
5. [link] The only StorageClass in the cluster is standard, which is the default.
   source: cluster_capacity({}) — verified
   > storageclass/standard provisioner=rancher.io/local-path (default)
6. [link] The unbound claim is what keeps the replica from being scheduled.
   source: describe({"kind": "pod", "name": "metrics-db-0", "namespace": "analytics"}) — verified
   > 0/1 nodes are available: pod has unbound immediate PersistentVolumeClaims. not found
7. [link] The StatefulSet's volume claim template is what created data-metrics-db-0.
   source: describe({"kind": "statefulset", "name": "metrics-db", "namespace": "analytics"}) — verified
   > Create Claim data-metrics-db-0 Pod metrics-db-0 in StatefulSet metrics-db success

## Investigation ledger

- The node lacks capacity or is tainted/NotReady, so the pod cannot be placed. — ruled out: The single node is Ready with full allocatable CPU, memory and 110 pod slots and no taints listed; scheduling failed only on the unbound claim.
  source: cluster_capacity({}) — verified
  > condition Ready=True
- The Service selector does not match the pod labels, so endpoints are empty for a labelling reason. — ruled out: The Service selector app=metrics-db matches the pod's labels exactly; the endpoint list is empty only because the pod is Pending and unscheduled.
  source: namespace_overview(analytics) — verified
  > pod/metrics-db-0 phase=Pending labels={app=metrics-db, apps.kubernetes.io/pod-index=0, controller-revision-hash=metrics-db-579c7ff846, statefulset.kubernetes.io/pod-name=metrics-db-0} node=<unscheduled>
- The container image or command is broken (bad image pull or crash loop). — ruled out: The pod never reached a node, so no container was ever started; the only pod condition present is PodScheduled=False.
  source: describe({"kind": "pod", "name": "metrics-db-0", "namespace": "analytics"}) — verified
  > PodScheduled   False

## Verification recipe

1. `kubectl -n analytics get statefulset metrics-db -o jsonpath='{.spec.volumeClaimTemplates[0].spec.storageClassName}'` — expect to see: "storageClassName": "fast-ssd"  [PRESENT]
2. `kubectl -n analytics describe pvc data-metrics-db-0` — expect to see: storageclass.storage.k8s.io "fast-ssd" not found  [PRESENT]
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
  "mechanism": "The StatefulSet analytics/metrics-db sets .spec.volumeClaimTemplates[0].spec.storageClassName to \"fast-ssd\", a StorageClass that does not exist in this cluster, where the only class is \"standard\". The PersistentVolumeClaim data-metrics-db-0 generated from that template is therefore rejected by the persistentvolume-controller with ProvisioningFailed \"storageclass.storage.k8s.io \\\"fast-ssd\\\" not found\" and binds no volume, so the StatefulSet's ordinal-0 replica cannot be scheduled for an unbound immediate PersistentVolumeClaim and the StatefulSet is stuck reporting 0/1 Ready.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
