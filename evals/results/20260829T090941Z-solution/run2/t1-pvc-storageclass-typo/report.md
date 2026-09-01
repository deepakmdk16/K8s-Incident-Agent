## Root cause

StatefulSet analytics/metrics-db asks for a StorageClass that does not exist in this cluster. Its volumeClaimTemplate "data" sets spec.storageClassName to "fast-ssd", but the only StorageClass present is "standard" (provisioner rancher.io/local-path, marked default). The generated PersistentVolumeClaim analytics/data-metrics-db-0 therefore inherits storageClassName "fast-ssd" and the persistentvolume-controller rejects it with 'storageclass.storage.k8s.io "fast-ssd" not found', leaving the PVC Status: Pending with no bound volume. Because the pod mounts that unbound claim, the scheduler refuses to place it ("0/1 nodes are available: pod has unbound immediate PersistentVolumeClaims"), so the StatefulSet reports 0/1 Ready and Service analytics/metrics-db has 0 endpoint addresses, which is why the dashboards are empty. The StatefulSet's volumeClaimTemplate must name "standard"; volumeClaimTemplates are immutable, so the StatefulSet has to be re-created with the corrected value and the Pending PVC deleted.

Remediation: edit StatefulSet analytics/metrics-db, field `spec.volumeClaimTemplates[0].spec.storageClassName`: `fast-ssd` -> `standard`.

## Evidence chain

1. [symptom] The paged StatefulSet reports 0/1 Ready.
   source: namespace_overview(analytics) — verified
   > statefulset/metrics-db ready=0/1 podLabels={app=metrics-db}
2. [symptom] The Service has no endpoint addresses.
   source: namespace_overview(analytics) — verified
   > service/metrics-db selector={app=metrics-db} endpointAddresses=0
3. [link] The scheduler cannot place the pod because its PersistentVolumeClaim is unbound.
   source: describe({"kind": "pod", "name": "metrics-db-0", "namespace": "analytics"}) — verified
   > 0/1 nodes are available: pod has unbound immediate PersistentVolumeClaims
4. [link] The pod mounts PersistentVolumeClaim data-metrics-db-0.
   source: describe({"kind": "pod", "name": "metrics-db-0", "namespace": "analytics"}) — verified
   > ClaimName:  data-metrics-db-0
5. [defect] Provisioning of the PVC fails because the requested StorageClass does not exist.
   source: describe({"kind": "persistentvolumeclaim", "name": "data-metrics-db-0", "namespace": "analytics"}) — verified
   > storageclass.storage.k8s.io "fast-ssd" not found
6. [defect] The StatefulSet's volumeClaimTemplate is where the nonexistent class name is written.
   source: get_object({"kind": "statefulsets", "name": "metrics-db", "namespace": "analytics"}) — verified
   > "storageClassName": "fast-ssd",
7. [defect] The only StorageClass in the cluster is standard, which is the default.
   source: cluster_capacity({}) — verified
   > storageclass/standard provisioner=rancher.io/local-path (default)

## Investigation ledger

- The Service selector does not match the pod labels, so endpoints are empty for a labelling reason. — ruled out: The Service selector {app=metrics-db} matches the pod's labels exactly; there is no address only because the pod is Pending and unscheduled.
  source: namespace_overview(analytics) — verified
  > pod/metrics-db-0 phase=Pending labels={app=metrics-db, apps.kubernetes.io/pod-index=0, controller-revision-hash=metrics-db-579c7ff846, statefulset.kubernetes.io/pod-name=metrics-db-0} node=<unscheduled>
- The node lacks capacity or carries a taint that prevents scheduling. — ruled out: The single node is Ready with full allocatable cpu/memory/pods and no taints listed; the scheduling failure message concerns the unbound claim, not resources.
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
  >   condition Ready=True
- The container image or command is broken (CrashLoopBackOff / ImagePullBackOff). — ruled out: No container ever started: the only pod condition is PodScheduled False, so no image pull or command failure is in play.
  source: describe({"kind": "pod", "name": "metrics-db-0", "namespace": "analytics"}) — verified
  > Conditions:
  >   Type           Status
  >   PodScheduled   False

## Verification recipe

1. `kubectl -n analytics describe pvc data-metrics-db-0` — expect to see: storageclass.storage.k8s.io "fast-ssd" not found  [PRESENT]
2. `kubectl -n analytics get statefulset metrics-db -o json` — expect to see: "storageClassName": "fast-ssd"  [PRESENT]
3. `kubectl get storageclass` — expect to see: storageclass/standard  [PRESENT]
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
  "mechanism": "StatefulSet analytics/metrics-db sets .spec.volumeClaimTemplates[0].spec.storageClassName to \"fast-ssd\", a StorageClass that does not exist in the cluster; the only one present is \"standard\". The PersistentVolumeClaim it generates, analytics/data-metrics-db-0, is refused provisioning with 'storageclass.storage.k8s.io \"fast-ssd\" not found' and stays Status: Pending, so the scheduler rejects the StatefulSet's replica with \"0/1 nodes are available: pod has unbound immediate PersistentVolumeClaims\" and it is never placed on a node.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
