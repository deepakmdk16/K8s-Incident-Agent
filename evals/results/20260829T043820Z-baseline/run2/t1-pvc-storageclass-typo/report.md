## Root cause

The StatefulSet `analytics/metrics-db` requests a storage class that does not exist in this cluster. Its `volumeClaimTemplates` entry `data` specifies `StorageClass: fast-ssd`, so the generated PVC `data-metrics-db-0` can never be provisioned or bound. Because the PVC has an immediate (non-`WaitForFirstConsumer`) binding requirement that cannot be satisfied, the scheduler refuses to place `metrics-db-0`, which stays `Pending` forever — hence 0/1 Ready and empty dashboards. The only storage provisioner present in the cluster is kind's `local-path-provisioner`, which serves the `standard` class, not `fast-ssd`.

Verdict: **probable** (mechanism of the Pending pod is directly evidenced; that the missing object is specifically the `fast-ssd` StorageClass is strongly implied by the scheduler's trailing `not found` but not shown by an explicit `kubectl get storageclass` listing).

## Evidence chain

- Paged symptom, from `kubectl get all -A`: `analytics pod/metrics-db-0 0/1 Pending 0 0s <none> <none>` — the pod has no node and no IP, and `statefulset.apps/metrics-db 0/1`.
- Scheduling is blocked by storage, from `describe pod/metrics-db-0 -n analytics` events:
  `Warning FailedScheduling ... 0/1 nodes are available: pod has unbound immediate PersistentVolumeClaims. not found`
  The trailing `not found` is the provisioning-side error surfaced through the scheduler predicate — the referenced class object cannot be resolved.
- The pod's only non-projected volume is the PVC, from `describe pod/metrics-db-0`:
  `data: Type: PersistentVolumeClaim ... ClaimName: data-metrics-db-0`.
- The unsatisfiable class name comes from the workload spec, from `describe statefulset.apps/metrics-db -n analytics`:
  ```
  Volume Claims:
    Name:          data
    StorageClass:  fast-ssd
    Capacity:      1Gi
    Access Modes:  [ReadWriteOnce]
  ```
- No `fast-ssd` provisioner exists. The only storage component in `kubectl get all -A` is
  `local-path-storage deployment.apps/local-path-provisioner 1/1 ... docker.io/kindest/local-path-provisioner`, i.e. a kind cluster whose provisioner backs the `standard` class. Cluster name in node column confirms kind: `incident-lab-control-plane`.
- The StatefulSet controller did its job — the failure is downstream of it, from `describe statefulset`:
  `Normal SuccessfulCreate ... Create Claim data-metrics-db-0 Pod metrics-db-0 in StatefulSet metrics-db success` and `Create Pod metrics-db-0 ... successful`. So the object is created; only binding fails.
- Pod condition confirms the pod never got past scheduling: `PodScheduled False`, and `Node: <none>`.

## Investigation ledger

- **Application crash / bad image / CrashLoopBackOff** — ruled out. Status is `Pending`, not `CrashLoopBackOff`/`Error`, `RESTARTS 0`, and both `kubectl logs metrics-db-0 -c db` and `--previous` returned empty: the container never started, so no application-level failure occurred.
- **Insufficient node CPU/memory (resource pressure)** — ruled out. The pod is `QoS Class: BestEffort` with no resource requests, and the scheduler message names the specific reason `pod has unbound immediate PersistentVolumeClaims`, not `Insufficient cpu/memory`.
- **Taints / nodeSelector / affinity mismatch (e.g. untolerated control-plane taint)** — ruled out. `describe pod` shows `Node-Selectors: <none>` and only the default not-ready/unreachable tolerations; the scheduler's single stated reason is the unbound PVC, and other pods (`kindnet`, `kube-proxy`, `coredns`, `local-path-provisioner`) schedule fine on the same lone node.
- **Node NotReady / control-plane outage** — ruled out. All `kube-system` components (`etcd`, `kube-apiserver`, `kube-scheduler`, `kube-controller-manager`, `kube-proxy`, `kindnet`) are `1/1 Running` with 0 restarts, and the scheduler is actively emitting `FailedScheduling` events.
- **Headless Service misconfiguration breaking the StatefulSet** — ruled out as the cause of the page. `service/metrics-db ClusterIP None ... SELECTOR app=metrics-db` matches `Pod Template Labels: app=metrics-db` and `Service Name: metrics-db`; a Service problem would not produce `PodScheduled False`.
- **Existing PV of wrong size/access mode blocking the bind** — considered, not the best fit. If a `fast-ssd` class existed with no matching PV, the message would typically stop at `pod has unbound immediate PersistentVolumeClaims`; the appended `not found` points at a missing referenced object (the class). A `kubectl get sc` would separate these definitively.
- **PVC left over from a prior revision with a stale class (retention policy)** — considered. `WhenDeleted: Retain / WhenScaled: Retain` means old PVCs survive, but the event `Create Claim data-metrics-db-0 ... success` shows this claim was freshly created from the current template, so the bad class comes from the live StatefulSet spec, not a leftover object. Either way the fix targets the same spec.

## Verification recipe

```bash
# 1. Is there a 'fast-ssd' StorageClass at all? (expect: not found; only 'standard' exists)
kubectl get storageclass

# 2. The PVC's state and the provisioning error verbatim
kubectl describe pvc data-metrics-db-0 -n analytics

# 3. The offending field in the workload spec
kubectl get statefulset metrics-db -n analytics \
  -o jsonpath='{.spec.volumeClaimTemplates[*].spec.storageClassName}{"\n"}'
```

Expected confirmation: command 1 lists no `fast-ssd`; command 2 shows `Status: Pending` with an event such as `storageclass.storage.k8s.io "fast-ssd" not found`; command 3 prints `fast-ssd`.

**Remediation (two options):**
- *Fix the workload spec (preferred if `fast-ssd` was a typo/copy-paste from another cluster):* `volumeClaimTemplates` is immutable, so delete the StatefulSet non-cascading and re-apply with the real class:
  `kubectl delete statefulset metrics-db -n analytics --cascade=orphan`, delete the stuck PVC `data-metrics-db-0`, then re-apply the manifest with `storageClassName: standard` (the class backed by the running `local-path-provisioner`).
- *Fix the environment (preferred if manifests are shared across clusters that really do have `fast-ssd`):* create a `fast-ssd` StorageClass in this cluster pointing at `rancher.io/local-path`, leaving the manifest untouched.

```json
{
  "case_id": "t1-pvc-storageclass-typo",
  "failing_resource": {"kind": "StatefulSet", "namespace": "analytics", "name": "metrics-db"},
  "mechanism": "The StatefulSet's volumeClaimTemplate requests StorageClass 'fast-ssd', which does not exist in this cluster (the only provisioner running is kind's local-path-provisioner serving 'standard'). The generated PVC data-metrics-db-0 therefore can never be provisioned or bound, and the scheduler rejects metrics-db-0 with 'pod has unbound immediate PersistentVolumeClaims. not found', leaving it Pending and the StatefulSet at 0/1 Ready.",
  "verdict": "probable"
}
```