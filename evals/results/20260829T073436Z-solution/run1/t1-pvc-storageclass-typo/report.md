## Root cause

The StatefulSet analytics/metrics-db declares a volumeClaimTemplate named "data" whose storageClassName is "fast-ssd". No StorageClass by that name exists in this cluster; the only StorageClass present is "standard" (rancher.io/local-path), which is also the default. The PersistentVolumeClaim data-metrics-db-0 that the StatefulSet controller created from that template therefore stays Pending with "storageclass.storage.k8s.io \"fast-ssd\" not found", the pod metrics-db-0 cannot be scheduled because its claim is unbound ("pod has unbound immediate PersistentVolumeClaims"), and the StatefulSet stays at 0/1 Ready — which is why the Service metrics-db has no endpoint addresses and the dashboards are empty. Correct the volumeClaimTemplate to storageClassName "standard" (or omit it to take the default); because volumeClaimTemplates are immutable, the StatefulSet must be recreated (kubectl delete statefulset metrics-db --cascade=orphan) and the stale Pending PersistentVolumeClaim data-metrics-db-0 deleted so it is recreated against the valid class.

Remediation: edit StatefulSet analytics/metrics-db, field `.spec.volumeClaimTemplates[0].spec.storageClassName`: `fast-ssd` -> `standard`.

## Evidence chain

1. [symptom] The paged StatefulSet reports 0/1 ready and its only pod is Pending and unscheduled.
   source: namespace_overview(analytics) — verified
   > statefulset/metrics-db ready=0/1 podLabels={app=metrics-db}
2. [defect] The StatefulSet's volumeClaimTemplate asks for StorageClass fast-ssd.
   source: describe({"kind": "statefulset", "name": "metrics-db", "namespace": "analytics"}) — verified
   > Volume Claims:
   >   Name:          data
   >   StorageClass:  fast-ssd
3. [defect] The generated PVC carries storageClassName fast-ssd.
   source: get_object({"kind": "persistentvolumeclaims", "namespace": "analytics"}) — verified
   > "storageClassName": "fast-ssd"
4. [link] Provisioning of that PVC fails because the named StorageClass does not exist, leaving the PVC Pending.
   source: describe({"kind": "persistentvolumeclaim", "name": "data-metrics-db-0", "namespace": "analytics"}) — verified
   > Warning  ProvisioningFailed  1s (x2 over 1s)  persistentvolume-controller  storageclass.storage.k8s.io "fast-ssd" not found
5. [link] The only StorageClass in the cluster is standard, which is the default.
   source: cluster_capacity({}) — verified
   > storageclass/standard provisioner=rancher.io/local-path (default)
6. [link] The unbound claim is the reason the pod cannot be scheduled.
   source: describe({"kind": "pod", "name": "metrics-db-0", "namespace": "analytics"}) — verified
   > Warning  FailedScheduling  0s    default-scheduler  0/1 nodes are available: pod has unbound immediate PersistentVolumeClaims. not found

## Investigation ledger

- Node capacity, taints or node pressure prevented scheduling. — ruled out: The single node is Ready with full allocatable CPU, memory and 110 pod slots, and the scheduler's stated reason is the unbound claim, not resources or taints.
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
  >   condition Ready=True
- The Service selector does not match the pod labels, so endpoints are empty for a labelling reason. — ruled out: The Service selector app=metrics-db matches the pod template labels exactly; the endpoint list is empty only because no pod is running.
  source: namespace_overview(analytics) — verified
  > service/metrics-db selector={app=metrics-db} endpointAddresses=0
- The container image or command is broken (bad image pull, crash loop). — ruled out: The pod never reached a node at all - it has no node assigned and PodScheduled is False - so no container was ever started or pulled.
  source: describe({"kind": "pod", "name": "metrics-db-0", "namespace": "analytics"}) — verified
  > Conditions:
  >   Type           Status
  >   PodScheduled   False

## Verification recipe

1. `kubectl describe pvc data-metrics-db-0 -n analytics` — expect to see: storageclass.storage.k8s.io "fast-ssd" not found  [PRESENT]
2. `kubectl get storageclass` — expect to see: storageclass/standard provisioner=rancher.io/local-path (default)  [PRESENT]
3. `kubectl describe statefulset metrics-db -n analytics` — expect to see: StorageClass:  fast-ssd  [PRESENT]
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
  "mechanism": "The StatefulSet analytics/metrics-db sets .spec.volumeClaimTemplates[0].spec.storageClassName to \"fast-ssd\", a StorageClass that does not exist in this cluster (the only one is \"standard\"), so the PersistentVolumeClaim data-metrics-db-0 generated from that template is rejected by the persistentvolume-controller with \"storageclass.storage.k8s.io \\\"fast-ssd\\\" not found\" and remains Pending and unbound. With that claim never bound, the StatefulSet's replica cannot be placed on a node and the StatefulSet reports 0/1 Ready.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
