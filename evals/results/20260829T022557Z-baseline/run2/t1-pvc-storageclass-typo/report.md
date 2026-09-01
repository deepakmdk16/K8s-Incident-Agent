## Root cause

**Confirmed.** The StatefulSet `analytics/metrics-db` declares a `volumeClaimTemplate` bound to `StorageClass: fast-ssd`, but no such StorageClass exists in this cluster — the only storage provisioner present is kind's `local-path-provisioner`. The generated PVC `data-metrics-db-0` therefore can never be provisioned and stays unbound; the scheduler refuses to place `metrics-db-0` because it has an unbound *immediate* PVC, so the pod sits `Pending` forever and the StatefulSet reports `0/1 Ready`. That is exactly the paged symptom (empty dashboards, no report generation — the DB pod never starts).

## Evidence chain

1. **`describe statefulset.apps/metrics-db -n analytics`**, Volume Claims block:
   ```
   Volume Claims:
     Name:          data
     StorageClass:  fast-ssd
     Capacity:      1Gi
     Access Modes:  [ReadWriteOnce]
   ```
   The spec asks for a class named `fast-ssd`.

2. **`kubectl get all -A`** shows the only storage machinery in the cluster is `local-path-storage/deployment.apps/local-path-provisioner` (`kindest/local-path-provisioner`). There is no CSI driver, no vendor storage operator, nothing that would plausibly back a class named `fast-ssd`. (Note: `kubectl get all` does not list StorageClasses, so this is corroborating rather than direct — see Verification recipe.)

3. **`describe pod/metrics-db-0 -n analytics`**, Events:
   ```
   Warning  FailedScheduling  0s  default-scheduler  0/1 nodes are available: pod has unbound immediate PersistentVolumeClaims. not found
   ```
   The scheduler names the exact blocking condition — an unbound immediate PVC — and the trailing `not found` is the propagated provisioning error for the referenced class.

4. **`describe pod/metrics-db-0`**, Conditions / Volumes:
   ```
   Status:           Pending
   Node:             <none>
   PodScheduled      False
   data: PersistentVolumeClaim ... ClaimName: data-metrics-db-0
   ```
   The pod is unscheduled and its only workload volume is that PVC.

5. **`kubectl get all -A`**: `pod/metrics-db-0 0/1 Pending 0 restarts`, `statefulset.apps/metrics-db 0/1` — the readiness the alert fires on, with zero restarts (never started, as opposed to crashing).

6. **`kubectl logs metrics-db-0 -c db`** and `--previous` both return **empty output** — the container has never run, consistent with a scheduling-stage failure rather than an application-stage failure.

7. **`describe statefulset`** Events show `SuccessfulCreate ... Create Claim data-metrics-db-0` and `Create Pod metrics-db-0` — the controller did its job; the failure is downstream in provisioning/scheduling, i.e. in the claim template's spec.

## Investigation ledger

- **Application crash / bad image / bad command (`busybox:1.36` writing to `/data/heartbeat`)** — ruled out. `RESTARTS 0`, status is `Pending` not `CrashLoopBackOff`/`Error`, and both `kubectl logs` and `kubectl logs --previous` are empty. The container image was never even pulled; `Node: <none>` means no kubelet ever touched this pod.

- **Insufficient node resources / node pressure** — ruled out. The scheduler's message is specifically `pod has unbound immediate PersistentVolumeClaims`, not `Insufficient cpu/memory`. The pod is also `QoS Class: BestEffort` (no requests at all), so it cannot be rejected for resource fit.

- **Taints / nodeSelector / affinity mismatch** — ruled out. `Node-Selectors: <none>` and only the two default `not-ready`/`unreachable` NoExecute tolerations are present; the scheduler would have said `node(s) had untolerated taint` rather than citing PVCs.

- **Cluster / control-plane unhealthy, node NotReady** — ruled out. All `kube-system` pods (`etcd`, `kube-apiserver`, `kube-scheduler`, `kube-controller-manager`, `kube-proxy`, `kindnet`, both `coredns`) are `1/1 Running` with 0 restarts at 10h age, and the scheduler is actively emitting events for this pod.

- **Headless Service misconfiguration breaking discovery** — ruled out as the paged cause. `service/metrics-db` exists as `ClusterIP None` with selector `app=metrics-db`, matching the pod template labels (`app=metrics-db`). Even if it were wrong, it would not produce `Pending`/`PodScheduled=False`.

- **PVC exists but is bound to a PV that is already claimed / RWO conflict with an old pod** — ruled out for this incident. Only one pod (`metrics-db-0`) exists cluster-wide in `analytics`, and the StatefulSet event shows the claim was freshly created at this redeploy (`Create Claim data-metrics-db-0`). A multi-attach conflict would surface as `FailedAttachVolume`/`Multi-Attach error` at the kubelet stage, not `FailedScheduling`.

- **Correct StorageClass name but provisioner pod down** — ruled out. `local-path-provisioner-75f7fc7dc5-q6tkt` is `1/1 Running`, 0 restarts. A healthy provisioner still ignores claims for a class it does not own, which is precisely the `fast-ssd` situation.

- **`volumeBindingMode: WaitForFirstConsumer` chicken-and-egg** — ruled out. That mode produces `waiting for first consumer to be created` / `WaitForFirstConsumer`, and the scheduler treats such PVCs as *not* immediate. The message explicitly says `unbound **immediate** PersistentVolumeClaims`, meaning the referenced class resolution failed outright.

## Verification recipe

```bash
# 1. Is there any StorageClass named fast-ssd? (expect: NotFound; expect local-path to be the only/default class)
kubectl get storageclass

# 2. The PVC should be Pending with a provisioning error naming the missing class
kubectl get pvc -n analytics
kubectl describe pvc data-metrics-db-0 -n analytics   # look for: storageclass.storage.k8s.io "fast-ssd" not found

# 3. Confirm the StatefulSet spec is the thing pinning that class
kubectl get statefulset metrics-db -n analytics \
  -o jsonpath='{.spec.volumeClaimTemplates[*].spec.storageClassName}{"\n"}'
```

**Remediation:** point the claim template at a class that exists (`local-path`, or omit `storageClassName` to take the cluster default) — or create the `fast-ssd` StorageClass if a real fast-SSD backend is genuinely expected in this environment. Because `spec.volumeClaimTemplates` is immutable, the fix is: `kubectl delete statefulset metrics-db -n analytics --cascade=orphan`, delete the stuck `data-metrics-db-0` PVC, then re-apply the corrected StatefulSet manifest. Note `WhenDeleted: Retain`, so no existing data is at risk. Longer term, add an admission/CI check that every `storageClassName` referenced by a manifest exists in the target cluster, which would have caught this at redeploy time.

```json
{
  "case_id": "t1-pvc-storageclass-typo",
  "failing_resource": {"kind": "StatefulSet", "namespace": "analytics", "name": "metrics-db"},
  "mechanism": "The StatefulSet's volumeClaimTemplate requests StorageClass 'fast-ssd', which does not exist in this cluster, so the generated PVC data-metrics-db-0 is never provisioned and stays unbound. The scheduler then refuses to place metrics-db-0 ('pod has unbound immediate PersistentVolumeClaims. not found'), leaving it Pending with the container never started, so the StatefulSet reports 0/1 Ready.",
  "verdict": "confirmed"
}
```