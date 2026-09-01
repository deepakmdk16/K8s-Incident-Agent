## Root cause

**Verdict: confirmed.**

The StatefulSet `analytics/metrics-db` declares a `volumeClaimTemplate` whose `StorageClass` is **`fast-ssd`**, a StorageClass that does not exist in this cluster (the only storage provisioner present is kind's `local-path-provisioner`). The generated PVC `data-metrics-db-0` therefore can never be provisioned or bound, and the scheduler refuses to place `metrics-db-0` because it has an unbound immediate PersistentVolumeClaim. The pod stays `Pending`, the StatefulSet stays `0/1 Ready`, and the analytics dashboards have no database to read from.

The spec that must change is the StatefulSet's `volumeClaimTemplates[0].spec.storageClassName` (the pod is a disposable product of it, and a StatefulSet's volumeClaimTemplate is immutable, so the fix requires delete + recreate of the StatefulSet).

## Evidence chain

- **Symptom, from `kubectl get all -A`:** `analytics pod/metrics-db-0 0/1 Pending 0 ... <none> <none>` — no IP, no node assigned. And `statefulset.apps/metrics-db  READY 0/1`, matching the page text exactly.
- **Scheduling is blocked on storage, not compute — from `describe pod/metrics-db-0`, Events:**
  `Warning FailedScheduling default-scheduler 0/1 nodes are available: pod has unbound immediate PersistentVolumeClaims. not found`
  The scheduler names the *only* reason: an unbound immediate PVC. The trailing `not found` is the provisioning-side error surfacing a missing object.
- **The unbound PVC is the volumeClaimTemplate's PVC — from `describe pod/metrics-db-0`, Volumes:**
  `data: Type: PersistentVolumeClaim ... ClaimName: data-metrics-db-0` and `Mounts: /data from data (rw)`.
- **The PVC's StorageClass is `fast-ssd` — from `describe statefulset.apps/metrics-db`, Volume Claims:**
  ```
  Name:          data
  StorageClass:  fast-ssd
  Capacity:      1Gi
  Access Modes:  [ReadWriteOnce]
  ```
- **`fast-ssd` has no provisioner in this cluster — from `kubectl get all -A`:** the only storage component running anywhere is `local-path-storage deployment.apps/local-path-provisioner 1/1` with image `kindest/local-path-provisioner`. This is a single-node kind cluster (`incident-lab-control-plane` is the only node referenced), whose stock StorageClass is `standard` backed by `rancher.io/local-path`. Nothing exists that would answer a claim for `fast-ssd`, so the claim sits Pending forever rather than being provisioned. (Consistent with the scheduler's `not found`.)
- **The claim was in fact created, so the failure is at bind/provision time, not creation time — from `describe statefulset`, Events:**
  `Normal SuccessfulCreate ... Create Claim data-metrics-db-0 Pod metrics-db-0 in StatefulSet metrics-db success`
- **Introduced by this morning's redeploy — from `kubectl get all -A`:** the StatefulSet, its pod, and `service/metrics-db` all show `AGE 0s`, while every cluster-infrastructure object shows `10h`. The workload was just recreated; the infrastructure is unchanged and healthy.

## Investigation ledger

- **Application crash / bad image / bad command.** Ruled out: the pod never reached a node. `Status: Pending`, `Node: <none>`, `PodScheduled False`, and both `kubectl logs` invocations (current and `--previous`) returned **empty output** — no container has ever started. `RESTARTS 0`. The `busybox:1.36` image was never even pulled.
- **Insufficient CPU/memory or node pressure.** Ruled out: the pod is `QoS Class: BestEffort` with no `resources` block at all, so it cannot fail a resource fit. The scheduler message cites only unbound PVCs, not `Insufficient cpu/memory`.
- **Taint/toleration or nodeSelector/affinity mismatch.** Ruled out: `Node-Selectors: <none>` and only the two default not-ready/unreachable tolerations in `describe pod`. The scheduler message would read `node(s) had untolerated taint ...`; it does not. The single node happily runs coredns, kindnet, kube-proxy, and local-path-provisioner, so it is schedulable.
- **Node down / kubelet unhealthy.** Ruled out: every control-plane and system pod on `incident-lab-control-plane` is `1/1 Running` with `0` restarts and 10h age, including `kube-scheduler` and `kube-controller-manager`.
- **Headless Service misconfiguration breaking readiness or DNS.** Ruled out as the *paged* cause: `service/metrics-db ClusterIP None ... SELECTOR app=metrics-db` correctly matches the pod label `app=metrics-db`, and `Service Name: metrics-db` on the StatefulSet matches. A service problem could not produce `FailedScheduling`, and it is not why the pod is `Pending`.
- **A pre-existing PVC/PV left over from a prior deploy conflicting with the new one (e.g. wrong size or already-bound PV).** Ruled out as the mechanism: the StatefulSet's retention policy is `WhenDeleted: Retain / WhenScaled: Retain`, so a leftover claim is plausible in principle — but the controller reports `Create Claim data-metrics-db-0 ... success` at age `0s`, meaning it created a fresh claim rather than reusing one, and the scheduler's complaint is that the claim is *unbound/not found*, not that it is bound to an incompatible volume. Even under the leftover-PVC scenario, the claim's StorageClass `fast-ssd` still has no provisioner.
- **StorageClass exists but its provisioner pod is crashed.** Ruled out by the `get all -A` inventory: there is no `fast-ssd`-related controller, CSI driver, DaemonSet, or Deployment anywhere in the cluster — no `csi-*` pods, no external provisioner. Nothing is crashed; nothing is there at all. (Note: `kubectl get storageclass` was not in the captured output; the confirming command is in the recipe below and the scheduler's own `not found` already points this way.)

## Verification recipe

```bash
# 1. Does 'fast-ssd' exist? Expect: not in the list; only kind's 'standard' (local-path).
kubectl get storageclass

# 2. Confirm the PVC is stuck Pending on that class, and read the provisioning event.
kubectl get pvc data-metrics-db-0 -n analytics -o wide
kubectl describe pvc data-metrics-db-0 -n analytics   # expect STATUS Pending, STORAGECLASS fast-ssd,
                                                      # event: storageclass.storage.k8s.io "fast-ssd" not found

# 3. Confirm the StatefulSet spec is the source of the bad class.
kubectl get statefulset metrics-db -n analytics \
  -o jsonpath='{.spec.volumeClaimTemplates[0].spec.storageClassName}{"\n"}'   # expect: fast-ssd
```

**Remediation (after confirmation):** repoint the volumeClaimTemplate at a StorageClass that exists (`standard` on this kind cluster), or create/install the `fast-ssd` StorageClass and its provisioner if this workload is meant to land on real SSD-backed storage. Because `volumeClaimTemplates` is immutable, apply with:

```bash
kubectl delete statefulset metrics-db -n analytics --cascade=orphan
kubectl delete pvc data-metrics-db-0 -n analytics        # the Pending, never-provisioned claim
kubectl delete pod metrics-db-0 -n analytics
# then re-apply the manifest with storageClassName: standard
```

No data loss risk here: the claim was never bound, so no volume was ever provisioned or written to.

```json
{
  "case_id": "t1-pvc-storageclass-typo",
  "failing_resource": {"kind": "StatefulSet", "namespace": "analytics", "name": "metrics-db"},
  "mechanism": "The StatefulSet's volumeClaimTemplate requests StorageClass 'fast-ssd', which does not exist in this cluster and has no provisioner, so the generated PVC data-metrics-db-0 can never be provisioned or bound. The scheduler consequently refuses to place metrics-db-0 ('pod has unbound immediate PersistentVolumeClaims'), leaving it Pending with no container ever started and the StatefulSet permanently 0/1 Ready.",
  "verdict": "confirmed"
}
```