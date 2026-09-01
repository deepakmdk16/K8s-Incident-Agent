## Root cause

StatefulSet analytics/metrics-db asks for a StorageClass that does not exist in this cluster. Its volumeClaimTemplate sets .spec.volumeClaimTemplates[0].spec.storageClassName to "fast-ssd", but the only StorageClass present is "standard" (provisioner rancher.io/local-path, marked default). The generated PersistentVolumeClaim analytics/data-metrics-db-0 therefore stays Pending with ProvisioningFailed: storageclass.storage.k8s.io "fast-ssd" not found, so the pod cannot be scheduled, the StatefulSet reports 0/1 Ready, and Service analytics/metrics-db has 0 endpoint addresses, which is why the dashboards are empty. Fix: recreate the StatefulSet with storageClassName "standard" (volumeClaimTemplates are immutable in place, so delete with --cascade=orphan and re-apply, then delete the stuck PVC).

Remediation: edit StatefulSet analytics/metrics-db, field `spec.volumeClaimTemplates[0].spec.storageClassName`: `fast-ssd` -> `standard`.

## Evidence chain

1. [symptom] The paged StatefulSet is 0/1 Ready and its only pod is unscheduled.
   source: namespace_overview(analytics) — verified
   > statefulset/metrics-db ready=0/1 podLabels={app=metrics-db}
2. [symptom] The StatefulSet's pod is Pending and unscheduled because its PVC is unbound.
   source: describe({"kind": "pod", "name": "metrics-db-0", "namespace": "analytics"}) — verified
   > Warning  FailedScheduling  0s    default-scheduler  0/1 nodes are available: pod has unbound immediate PersistentVolumeClaims. not found
3. [link] The pod mounts PersistentVolumeClaim data-metrics-db-0.
   source: describe({"kind": "pod", "name": "metrics-db-0", "namespace": "analytics"}) — verified
   > ClaimName:  data-metrics-db-0
4. [link] That PVC is Pending on StorageClass fast-ssd, which the controller cannot find.
   source: describe({"kind": "persistentvolumeclaim", "name": "data-metrics-db-0", "namespace": "analytics"}) — verified
   > Warning  ProvisioningFailed  1s (x2 over 1s)  persistentvolume-controller  storageclass.storage.k8s.io "fast-ssd" not found
5. [defect] The StatefulSet's volumeClaimTemplate names the nonexistent class fast-ssd.
   source: get_object({"kind": "statefulsets", "name": "metrics-db", "namespace": "analytics"}) — verified
   > "storageClassName": "fast-ssd",
6. [defect] The only StorageClass in the cluster is standard, which is the default.
   source: cluster_capacity({}) — verified
   > storageclass/standard provisioner=rancher.io/local-path (default)

## Investigation ledger

- Node capacity or taints prevented scheduling — ruled out: The single node is Ready with full allocatable cpu/memory/pods and no taints listed; the scheduler's only complaint was the unbound PVC.
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
  >   condition Ready=True
- Service selector mismatch caused the empty endpoints — ruled out: The Service selector {app=metrics-db} matches the workload's pod labels exactly; it has zero addresses only because no pod ever runs.
  source: namespace_overview(analytics) — verified
  > service/metrics-db selector={app=metrics-db} endpointAddresses=0
- Container image or command failure (CrashLoopBackOff / ImagePullBackOff) — ruled out: The pod never reached a node at all - the only condition is PodScheduled=False and there is no container status, so no image pull or command ever ran.
  source: describe({"kind": "pod", "name": "metrics-db-0", "namespace": "analytics"}) — verified
  > Conditions:
  >   Type           Status
  >   PodScheduled   False

## Verification recipe

1. `kubectl describe pvc data-metrics-db-0 -n analytics` — expect to see: storageclass.storage.k8s.io "fast-ssd" not found  [PRESENT]
2. `kubectl get statefulset metrics-db -n analytics -o json | jq '.spec.volumeClaimTemplates[0].spec.storageClassName'` — expect to see: "storageClassName": "fast-ssd"  [PRESENT]
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
  "mechanism": "StatefulSet analytics/metrics-db sets .spec.volumeClaimTemplates[0].spec.storageClassName to \"fast-ssd\", a StorageClass that does not exist in this cluster (the only one is \"standard\"), so the PersistentVolumeClaim it generates, analytics/data-metrics-db-0, is rejected by the persistentvolume-controller with `storageclass.storage.k8s.io \"fast-ssd\" not found` and stays Status: Pending with no Volume bound. With that claim unbound the scheduler rejects the StatefulSet's replica with `0/1 nodes are available: pod has unbound immediate PersistentVolumeClaims`, leaving the StatefulSet at ready=0/1.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
