## Root cause

StatefulSet analytics/metrics-db asks for a StorageClass that does not exist in this cluster. Its volumeClaimTemplate \"data\" sets .spec.volumeClaimTemplates[0].spec.storageClassName to \"fast-ssd\", while the only StorageClass present is standard (provisioner rancher.io/local-path), which is also the cluster default. The generated PersistentVolumeClaim analytics/data-metrics-db-0 therefore stays Pending with ProvisioningFailed: storageclass.storage.k8s.io \"fast-ssd\" not found, so no volume is ever bound and pod metrics-db-0 can never be scheduled. Downstream of that, StatefulSet analytics/metrics-db reports ready=0/1 and Service analytics/metrics-db has endpointAddresses=0, which is why the analytics dashboards are empty. Fix by pointing the claim template at the StorageClass that exists (or omitting storageClassName so the default is used); because volumeClaimTemplates are immutable, the StatefulSet has to be recreated (e.g. deleted with --cascade=orphan and reapplied) together with the Pending claim.

Remediation: edit StatefulSet analytics/metrics-db, field `spec.volumeClaimTemplates[0].spec.storageClassName`: `fast-ssd` -> `standard`.

## Evidence chain

1. [symptom] The paged StatefulSet is 0/1 ready and its only pod is Pending and unscheduled.
   source: namespace_overview(analytics) — verified
   > statefulset/metrics-db ready=0/1 podLabels={app=metrics-db}
2. [symptom] The pod cannot be scheduled because its PVC is unbound.
   source: describe({"kind": "pod", "name": "metrics-db-0", "namespace": "analytics"}) — verified
   > Warning  FailedScheduling  0s    default-scheduler  0/1 nodes are available: pod has unbound immediate PersistentVolumeClaims. not found
3. [link] The pod's data volume is the StatefulSet-generated claim data-metrics-db-0.
   source: describe({"kind": "pod", "name": "metrics-db-0", "namespace": "analytics"}) — verified
   > ClaimName:  data-metrics-db-0
4. [link] That PVC is Pending with no volume, because the named StorageClass does not exist.
   source: describe({"kind": "persistentvolumeclaim", "name": "data-metrics-db-0", "namespace": "analytics"}) — verified
   > Warning  ProvisioningFailed  1s (x2 over 1s)  persistentvolume-controller  storageclass.storage.k8s.io "fast-ssd" not found
5. [defect] The StatefulSet's volumeClaimTemplate names storageClassName fast-ssd.
   source: get_object({"kind": "statefulsets", "name": "metrics-db", "namespace": "analytics"}) — verified
   > "storageClassName": "fast-ssd",
6. [defect] The only StorageClass in the cluster is standard, which is the default.
   source: cluster_capacity({}) — verified
   > storageclass/standard provisioner=rancher.io/local-path (default)

## Investigation ledger

- Node capacity, readiness or taints prevented scheduling. — ruled out: The single node is Ready with full allocatable CPU/memory and 110 pod slots, so resources are not the constraint.
  source: cluster_capacity({}) — verified
  > node/incident-lab-control-plane cpu=6/6 memory=12220316Ki/12220316Ki pods=110 (capacity/allocatable)
  >   condition Ready=True
- A usable StorageClass under a different name (the cluster default) would have provisioned the volume anyway. — ruled out: The default class exists but is not what the claim template requests, and an explicit storageClassName suppresses default-class assignment, so provisioning was never attempted against it.
  source: describe({"kind": "persistentvolumeclaim", "name": "data-metrics-db-0", "namespace": "analytics"}) — verified
  > StorageClass:  fast-ssd
- A broken container image or command (crash loop) rather than storage. — ruled out: No container ever started: the pod has only PodScheduled=False and no node assigned.
  source: describe({"kind": "pod", "name": "metrics-db-0", "namespace": "analytics"}) — verified
  > Conditions:
  >   Type           Status
  >   PodScheduled   False

## Verification recipe

1. `kubectl -n analytics describe pvc data-metrics-db-0` — expect to see: storageclass.storage.k8s.io "fast-ssd" not found  [PRESENT]
2. `kubectl -n analytics get statefulset metrics-db -o jsonpath='{.spec.volumeClaimTemplates[0].spec.storageClassName}'` — expect to see: "storageClassName": "fast-ssd"  [PRESENT]
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
  "mechanism": "StatefulSet analytics/metrics-db sets .spec.volumeClaimTemplates[0].spec.storageClassName to \"fast-ssd\", a StorageClass that does not exist in this cluster, so the PersistentVolumeClaim it generates, analytics/data-metrics-db-0, is rejected by the persistentvolume-controller with `storageclass.storage.k8s.io \"fast-ssd\" not found` and stays Status: Pending with an empty Volume. Pod metrics-db-0 mounts that claim, so the scheduler refuses it with `0/1 nodes are available: pod has unbound immediate PersistentVolumeClaims`, and the StatefulSet stays at ready=0/1.",
  "verdict": "confirmed",
  "missing_evidence": ""
}
```
