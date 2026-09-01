## Root cause

**StatefulSet `analytics/metrics-db`** — its `volumeClaimTemplates` entry `data` requests `StorageClass: fast-ssd`, which does not exist in this cluster (the only storage provisioner present is kind's `local-path-provisioner`). With no matching StorageClass, the generated PVC `data-metrics-db-0` is never provisioned and stays unbound; the scheduler refuses to place `metrics-db-0` because it has an unbound *immediate*-binding PVC, so the pod is stuck `Pending`, the StatefulSet never reaches 1/1 Ready, and no database process ever starts to serve the analytics dashboards.

Verdict: **confirmed**.

## Evidence chain

- The paged symptom is visible in `kubectl get all -A`: `statefulset.apps/metrics-db  0/1` and `pod/metrics-db-0  0/1  Pending  0 RESTARTS`, `NODE  <none>`, `IP  <none>` — the pod was never scheduled, so it never ran (0 restarts, not a crash loop).
- The scheduler names the blocker exactly, from *describe of pod metrics-db-0*: `Warning FailedScheduling ... 0/1 nodes are available: pod has unbound immediate PersistentVolumeClaims. not found`. The trailing `not found` is the volume-binding plugin reporting a missing referenced object (the StorageClass), not a full node/capacity or affinity rejection.
- The volume in question is the StatefulSet-managed claim, from *describe of pod metrics-db-0*: `Volumes: data: Type: PersistentVolumeClaim ... ClaimName: data-metrics-db-0`, and the only unsatisfied condition is `PodScheduled  False`.
- The offending spec field is in *describe of statefulset metrics-db*: `Volume Claims: Name: data / StorageClass: fast-ssd / Capacity: 1Gi / Access Modes: [ReadWriteOnce]`.
- No provisioner for `fast-ssd` exists anywhere in the cluster: `kubectl get all -A` lists exactly one storage component, `local-path-storage  deployment.apps/local-path-provisioner  1/1` (kind's default `local-path` / `standard` class). There is no CSI driver, no Ceph/EBS/vSphere controller, nothing that could serve a class named `fast-ssd`.
- The claim was in fact created (so this is not an RBAC/controller failure), from *describe of statefulset*: `Normal SuccessfulCreate ... Create Claim data-metrics-db-0 Pod metrics-db-0 in StatefulSet metrics-db success` — the claim exists but is unbound; note it also does not appear as bound anywhere.
- The container never executed, confirming no application-level fault: `kubectl logs metrics-db-0 -c db` and `--previous` both returned empty output.

## Investigation ledger

- **Application/container crash (bad image, bad command, DB init failure)** — ruled out: status is `Pending`, not `CrashLoopBackOff`/`Error`; `RESTARTS 0`; both `kubectl logs` and `logs --previous` are empty; the container was never started because the pod was never bound to a node.
- **Image pull failure (`busybox:1.36` unavailable)** — ruled out: an image problem produces `ImagePullBackOff`/`ErrImagePull` with a `Failed ... pull` event after scheduling. Here `Node: <none>` and the only event is `FailedScheduling`.
- **Insufficient node CPU/memory** — ruled out: the pod declares no resource requests (`QoS Class: BestEffort`, no `Requests` in *describe of pod*), and the scheduler's message cites unbound PVCs, not `Insufficient cpu/memory`.
- **Taints / nodeSelector / affinity mismatch** — ruled out: *describe of pod* shows `Node-Selectors: <none>` and only the two default not-ready/unreachable tolerations; the scheduler would have said `node(s) had untolerated taint` or `didn't match node selector`. It did not.
- **Node down / control plane broken** — ruled out: every `kube-system` pod (`etcd`, `kube-apiserver`, `kube-scheduler`, `kube-controller-manager`, `kube-proxy`, `kindnet`, both `coredns`) is `1/1 Running` on `incident-lab-control-plane`, and the scheduler is actively evaluating the pod (`0/1 nodes are available`).
- **Headless Service misconfiguration breaking dashboards** — ruled out as the *cause*: `service/metrics-db  ClusterIP  None ... 5432/TCP  SELECTOR app=metrics-db` correctly matches the pod label `app=metrics-db` in *describe of pod*. It has no endpoints only because the pod is Pending — a downstream effect, not the root cause.
- **PVC exists but bound to a stale/mismatched PV from before the redeploy** — ruled out as primary: retention policy is `WhenDeleted: Retain / WhenScaled: Retain`, so a leftover PV *could* linger, but a size/access-mode mismatch yields a different scheduler message and would not append `not found`; and no PersistentVolume-serving provisioner for `fast-ssd` exists at all. (Worth a glance during the fix — see verification step 1.)
- **DNS failure (dashboards can't resolve `metrics-db`)** — ruled out: both `coredns` replicas are `1/1 Running` and `kube-dns` Service is healthy; the dashboards are empty because the backend pod never started.

## Verification recipe

```bash
# 1. The claim is Pending and names a StorageClass that doesn't exist.
kubectl get pvc -n analytics data-metrics-db-0 -o wide
kubectl get storageclass          # expect 'standard' (local-path) only; no 'fast-ssd'

# 2. The claim's own events say the class is missing.
kubectl describe pvc -n analytics data-metrics-db-0 | tail -20
#    expect: "storageclass.storage.k8s.io \"fast-ssd\" not found"

# 3. Confirm the bad value lives in the StatefulSet spec (this is what must change).
kubectl get statefulset -n analytics metrics-db \
  -o jsonpath='{.spec.volumeClaimTemplates[*].spec.storageClassName}{"\n"}'
#    expect: fast-ssd
```

**Remediation:** point the volume claim template at a class that exists (`standard` on this kind cluster), or create/install a `fast-ssd` StorageClass backed by a real provisioner. Because `volumeClaimTemplates` is immutable on a live StatefulSet, apply it as:

```bash
kubectl delete statefulset -n analytics metrics-db --cascade=orphan
kubectl delete pvc -n analytics data-metrics-db-0        # unbound, no data to lose
# re-apply manifest with storageClassName: standard
kubectl rollout status statefulset/metrics-db -n analytics
```

```json
{
  "case_id": "t1-pvc-storageclass-typo",
  "failing_resource": {"kind": "StatefulSet", "namespace": "analytics", "name": "metrics-db"},
  "mechanism": "The StatefulSet's volumeClaimTemplate requests StorageClass 'fast-ssd', which does not exist in the cluster (only kind's local-path provisioner is present), so the generated PVC data-metrics-db-0 is never provisioned and stays unbound. The scheduler therefore rejects metrics-db-0 with 'pod has unbound immediate PersistentVolumeClaims. not found', leaving the pod Pending on no node. The database container never starts, so the StatefulSet reports 0/1 Ready indefinitely.",
  "verdict": "confirmed"
}
```