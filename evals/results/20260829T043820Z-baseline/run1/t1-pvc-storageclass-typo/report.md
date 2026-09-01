## Root cause

The StatefulSet `analytics/metrics-db` declares a `volumeClaimTemplate` whose `StorageClass` is `fast-ssd`, a class that does not exist in this cluster (the only storage provisioner present is kind's `local-path-provisioner`). The generated PVC `data-metrics-db-0` therefore can never be dynamically provisioned and stays unbound, so the scheduler refuses to place `metrics-db-0` and the pod sits `Pending` forever — 0/1 Ready, no database, empty dashboards.

Verdict: **confirmed**.

## Evidence chain

- The paged symptom is visible directly: `kubectl get all -A` shows `analytics statefulset.apps/metrics-db 0/1` and `pod/metrics-db-0 0/1 Pending ... NODE <none>`.
- The pod is stuck *before* scheduling, not crashing: `describe pod/metrics-db-0` shows `Status: Pending`, `Node: <none>`, and `Conditions: PodScheduled False`.
- The scheduler names the blocking dependency and the reason it cannot be resolved — `describe pod/metrics-db-0` events:
  `Warning FailedScheduling ... 0/1 nodes are available: pod has unbound immediate PersistentVolumeClaims. not found`
  The `unbound immediate PersistentVolumeClaims` clause plus the trailing `not found` is the classic signature of a PVC whose referenced StorageClass object does not exist.
- The unbound claim belongs to this pod: `describe pod/metrics-db-0` → `Volumes: data: Type: PersistentVolumeClaim ... ClaimName: data-metrics-db-0`.
- The offending spec field lives on the StatefulSet, not the pod: `describe statefulset.apps/metrics-db` →
  `Volume Claims: Name: data / StorageClass: fast-ssd / Capacity: 1Gi / Access Modes: [ReadWriteOnce]`.
- The StatefulSet controller did its job — the failure is downstream of it: `describe statefulset` events `Create Claim data-metrics-db-0 Pod metrics-db-0 in StatefulSet metrics-db success` and `Create Pod metrics-db-0 ... successful`.
- No provisioner in the cluster serves `fast-ssd`: the only storage component in `kubectl get all -A` is `local-path-storage deployment.apps/local-path-provisioner 1/1`, which backs kind's default `standard`/`local-path` class. There is no CSI driver, cloud-provider, or SSD provisioner pod anywhere in the listing.
- Consistent with "this morning's redeploy": the StatefulSet, Service, and pod all show `AGE 0s`, i.e. freshly recreated objects carrying the bad `StorageClass` value.

## Investigation ledger

- **Container crash / bad image (`busybox:1.36`) or bad command** — ruled out. The pod never started: `describe pod` shows `PodScheduled False` and no container statuses; both `kubectl logs` invocations (current and `--previous`) returned empty, and `RESTARTS 0`. Image pull is never attempted for an unscheduled pod.
- **Node capacity / resource pressure** — ruled out. The container spec has no resource requests (`QoS Class: BestEffort`), and the scheduler message cites PVC binding, not `Insufficient cpu/memory`.
- **Taints, node selectors, or affinity blocking scheduling** — ruled out. `describe pod` shows `Node-Selectors: <none>` and only the two default not-ready/unreachable tolerations; the single node `incident-lab-control-plane` is healthy and hosting all kube-system pods plus `local-path-provisioner` (`1/1 Running`). The scheduler reason is PVC-specific, not `node(s) had untolerated taint`.
- **Cluster-wide control plane or scheduler outage** — ruled out. `kube-apiserver`, `kube-scheduler`, `kube-controller-manager`, `etcd`, `kindnet`, `kube-proxy`, and both `coredns` pods are all `1/1 Running` with `0` restarts; the scheduler is clearly alive since it emitted a `FailedScheduling` event.
- **Headless Service / DNS misconfiguration breaking readiness** — ruled out as the paged cause. `service/metrics-db` exists as `ClusterIP None` with selector `app=metrics-db`, matching the pod label `app=metrics-db`; and a Service problem could not produce a `PodScheduled False` condition anyway.
- **PVC exists but is `Pending` for capacity reasons (no PV of matching size / access mode)** — ruled out as the primary mechanism. With a named StorageClass, static matching is not attempted; the event's `not found` suffix points at a missing StorageClass object rather than a size/access-mode mismatch. (Confirmed by verification step 2 below.)
- **A previously-bound PVC left behind by an earlier release now conflicting** — considered; the retention policy is `WhenDeleted: Retain / WhenScaled: Retain`, so a stale PVC could persist. But the StatefulSet event `Create Claim data-metrics-db-0 ... success` shows the claim was newly created this deploy, so no pre-existing bound claim is being reused.

## Verification recipe

```bash
# 1. Does the StorageClass named in the volumeClaimTemplate exist? (expect: "fast-ssd" absent)
kubectl get storageclass

# 2. Confirm the PVC is Pending and read the provisioner's own reason
kubectl get pvc -n analytics
kubectl describe pvc data-metrics-db-0 -n analytics   # expect: storageclass.storage.k8s.io "fast-ssd" not found

# 3. Confirm the bad value is in the StatefulSet spec (the thing that must change)
kubectl get statefulset metrics-db -n analytics -o jsonpath='{.spec.volumeClaimTemplates[*].spec.storageClassName}{"\n"}'
```

**Remediation:** point the claim template at a StorageClass that actually exists (`kubectl get storageclass` → e.g. `standard` on kind), or create/install the `fast-ssd` class and its provisioner. Because `spec.volumeClaimTemplates` is immutable on an existing StatefulSet, apply with `kubectl delete statefulset metrics-db -n analytics --cascade=orphan`, delete the stuck `data-metrics-db-0` PVC, then re-apply the corrected manifest. If the analytics data must survive, ensure the replacement class/PV points at the retained volume before deleting anything.

```json
{
  "case_id": "t1-pvc-storageclass-typo",
  "failing_resource": {"kind": "StatefulSet", "namespace": "analytics", "name": "metrics-db"},
  "mechanism": "The StatefulSet's volumeClaimTemplate requests StorageClass 'fast-ssd', which does not exist in this cluster and has no provisioner, so the generated PVC data-metrics-db-0 can never be dynamically provisioned and stays unbound. The scheduler consequently cannot place metrics-db-0 ('pod has unbound immediate PersistentVolumeClaims. not found'), leaving the pod Pending and the StatefulSet permanently 0/1 Ready.",
  "verdict": "confirmed"
}
```