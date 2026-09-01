## Root cause

**Failing resource:** `StatefulSet analytics/metrics-db` (its `volumeClaimTemplates` spec).

The StatefulSet's volume claim template requests `StorageClass: fast-ssd`. No such StorageClass exists in this cluster — the only storage provisioner running is `local-path-storage/local-path-provisioner` (kind's `standard`/`local-path` class). The generated PVC `data-metrics-db-0` therefore can never be provisioned or bound, and the scheduler refuses to place `metrics-db-0` because it has an unbound immediate PVC. The pod sits in `Pending` forever, so the StatefulSet never reports a Ready replica, so the dashboards behind it have no database and stay empty.

Verdict: **confirmed** — the scheduler event names the exact mechanism ("unbound immediate PersistentVolumeClaims. not found") and the cluster inventory shows no provisioner that could satisfy `fast-ssd`.

## Evidence chain

- **Paged symptom, from `kubectl get all -A`:** `analytics   statefulset.apps/metrics-db   0/1   0s   db   busybox:1.36` and `pod/metrics-db-0   0/1   Pending   0   0s   <none>   <none>` — zero Ready replicas, pod not even assigned to a node (`NODE` = `<none>`).
- **Why the pod is not scheduled, from `describe pod/metrics-db-0`:**
  `Warning FailedScheduling ... 0/1 nodes are available: pod has unbound immediate PersistentVolumeClaims. not found`
  This is the direct causal link: scheduling is blocked on the PVC, and the trailing `not found` is the wrapped storage-class lookup failure surfaced into the scheduler message.
- **Which PVC, from `describe pod/metrics-db-0` → Volumes:**
  `data: Type: PersistentVolumeClaim ... ClaimName: data-metrics-db-0`.
- **Which storage class is requested, from `describe statefulset.apps/metrics-db` → Volume Claims:**
  `Name: data / StorageClass: fast-ssd / Capacity: 1Gi / Access Modes: [ReadWriteOnce]`.
- **That `fast-ssd` cannot be served, from `kubectl get all -A`:** the only storage-related workload in the cluster is `local-path-storage deployment.apps/local-path-provisioner 1/1 ... docker.io/kindest/local-path-provisioner`. There is no CSI driver, external provisioner, or any other storage controller pod running that could back a class named `fast-ssd`.
- **The StatefulSet did create the claim, from `describe statefulset.apps/metrics-db` events:**
  `Normal SuccessfulCreate ... Create Claim data-metrics-db-0 Pod metrics-db-0 in StatefulSet metrics-db success` — so the failure is in binding/provisioning, not in claim creation.
- **Pod never ran, from the log commands:** both `kubectl logs metrics-db-0 -c db` and `--previous` returned empty output — consistent with a container that has never started (`Pending`), so this is not an application-level or crash-loop failure.
- **Age correlation with the redeploy:** the StatefulSet, Service and pod all show `AGE 0s` (this object generation was just (re)created), matching "after this morning's redeploy"; `PVC retention WhenDeleted: Retain` means the old claim, if any, was not the blocker for creation.

## Investigation ledger

- **Image pull failure (bad tag / registry auth):** ruled out. The pod never reaches image pulling — status is `Pending` with `PodScheduled: False` and the only event is `FailedScheduling`; there is no `ErrImagePull`/`ImagePullBackOff` and no `Failed to pull image` event. `busybox:1.36` is also a valid public tag.
- **CrashLoopBackOff / application error (e.g. cannot write `/data/heartbeat`):** ruled out. `RESTARTS 0`, and both `kubectl logs ... --tail=50` and `--previous` are empty — the container has never executed.
- **Insufficient node resources / node pressure:** ruled out. The scheduler message is specifically `pod has unbound immediate PersistentVolumeClaims`, not `Insufficient cpu/memory`; the pod is `QoS Class: BestEffort` with no resource requests at all, so it cannot be evicted or rejected for resource reasons.
- **Node taints / nodeSelector / affinity mismatch:** ruled out. `describe pod` shows `Node-Selectors: <none>` and only the two default not-ready/unreachable tolerations; the single node `incident-lab-control-plane` is healthy and running all kube-system DaemonSet and control-plane pods. The scheduler did not report a taint predicate failure.
- **Node down / kubelet unreachable:** ruled out. `kindnet`, `kube-proxy` are `1/1 Running` on that node, CoreDNS and the provisioner are `Running` on it, and DaemonSets show `DESIRED 1 / READY 1`.
- **Service/DNS misconfiguration causing "empty dashboards":** ruled out as root cause. `service/metrics-db` is a correct headless service (`ClusterIP: None`, `PORT 5432/TCP`, `SELECTOR app=metrics-db`) matching the pod label `app=metrics-db`; it has no endpoints only because the pod is Pending — a downstream effect, not the cause. CoreDNS is `2/2` Running.
- **PVC exists but bound to a stale/released PV of the wrong size or access mode:** considered and rejected as the primary story. The scheduler says the PVC is *unbound* and appends `not found`; with `WhenDeleted: Retain` a leftover PV would produce a binding/affinity error, not a `not found`. (If `kubectl get pvc -n analytics` shows the claim `Pending` with a `ProvisioningFailed`/`storageclass ... not found` event, this alternative is fully closed — see verification.)
- **Namespace ResourceQuota blocking the PVC:** ruled out for the paged symptom. The claim was created successfully (`SuccessfulCreate ... Create Claim data-metrics-db-0 ... success`); a quota denial would have blocked claim creation and produced a `FailedCreate` event on the StatefulSet.

## Verification recipe

```bash
# 1. Does the requested StorageClass exist? (expect: no "fast-ssd" in the list)
kubectl get storageclass

# 2. Is the claim stuck Pending, and why? (expect: Pending, event
#    'storageclass.storage.k8s.io "fast-ssd" not found')
kubectl describe pvc data-metrics-db-0 -n analytics

# 3. Confirm the template that must change
kubectl get statefulset metrics-db -n analytics \
  -o jsonpath='{.spec.volumeClaimTemplates[*].spec.storageClassName}{"\n"}'
```

**Remediation (after confirming):** point the claim template at a class that exists (on this cluster, kind's `standard` backed by `local-path-provisioner`), or install/create the `fast-ssd` StorageClass if a real SSD provisioner is supposed to be present. Because `volumeClaimTemplates` is immutable on an existing StatefulSet, apply it as:

```bash
kubectl delete statefulset metrics-db -n analytics --cascade=orphan
kubectl delete pvc data-metrics-db-0 -n analytics      # it is Pending, holds no data
# re-apply the manifest with spec.volumeClaimTemplates[0].spec.storageClassName: standard
kubectl apply -f metrics-db.yaml
kubectl rollout status statefulset/metrics-db -n analytics
```

```json
{
  "case_id": "t1-pvc-storageclass-typo",
  "failing_resource": {"kind": "StatefulSet", "namespace": "analytics", "name": "metrics-db"},
  "mechanism": "The StatefulSet's volumeClaimTemplate requests StorageClass 'fast-ssd', which does not exist in this cluster (the only provisioner present is local-path). The generated PVC data-metrics-db-0 can never be provisioned or bound, so the scheduler rejects metrics-db-0 with 'pod has unbound immediate PersistentVolumeClaims. not found' and the pod stays Pending, leaving the StatefulSet permanently at 0/1 Ready.",
  "verdict": "confirmed"
}
```