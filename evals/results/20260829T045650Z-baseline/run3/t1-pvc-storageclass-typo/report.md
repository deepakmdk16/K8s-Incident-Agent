## Root cause

The **StatefulSet `analytics/metrics-db`** is the failing resource. Its `volumeClaimTemplates` entry requests `StorageClass: fast-ssd`, which does not resolve in this cluster. The statefulset controller therefore created PVC `data-metrics-db-0` bound to a storage class that has no matching StorageClass/provisioner, so the PVC never binds. Because `data` is an *immediate*-binding volume the scheduler refuses to place `metrics-db-0`, the pod stays `Pending` forever, no container ever starts, and the StatefulSet reports `0/1 Ready` — which is exactly the paged symptom (dashboards empty, no DB backend).

Verdict: **confirmed**.

## Evidence chain

- **Paged symptom present in output** — `kubectl get all -A`: `statefulset.apps/metrics-db  READY 0/1` and `pod/metrics-db-0  0/1  Pending  0 RESTARTS`. Zero restarts and `IP <none>`, `NODE <none>` mean the container never ran; this is a scheduling failure, not a crash.
- **Scheduler states the blocking condition** — describe of pod `metrics-db-0`, Events: `Warning FailedScheduling ... 0/1 nodes are available: pod has unbound immediate PersistentVolumeClaims. not found`. The `not found` tail is the storage-class resolution failure surfaced through the volume binder.
- **Pod condition confirms it never got scheduled** — describe of pod `metrics-db-0`: `Conditions: PodScheduled False`, `Node: <none>`, `Status: Pending`.
- **The unbound claim is the one the StatefulSet generated** — describe of pod: `Volumes: data: Type: PersistentVolumeClaim ... ClaimName: data-metrics-db-0`; describe of statefulset events: `Normal SuccessfulCreate ... Create Claim data-metrics-db-0 Pod metrics-db-0 in StatefulSet metrics-db success`.
- **The offending spec field** — describe of `statefulset.apps/metrics-db`: `Volume Claims: Name: data / StorageClass: fast-ssd / Capacity: 1Gi / Access Modes: [ReadWriteOnce]`. The class name lives in the StatefulSet's claim template, so the StatefulSet spec is what must change.
- **No such provisioner exists in the cluster** — `kubectl get all -A` shows the only storage provisioner running is `local-path-storage deployment.apps/local-path-provisioner ... kindest/local-path-provisioner`, on a single-node kind cluster (`incident-lab-control-plane`). A kind cluster ships the `standard`/local-path class; nothing in the output provides or backs a class named `fast-ssd`.
- **No application-level failure to blame** — `kubectl logs metrics-db-0 -c db` and `--previous` both return empty output, consistent with a container that was never created.

## Investigation ledger

- **Application crash / bad image / bad command (`busybox:1.36` writing `/data/heartbeat`)** — ruled out: pod status is `Pending` with `RESTARTS 0`, and both `kubectl logs` and `kubectl logs --previous` are empty. No container was ever created, so no application code ran.
- **Image pull failure (ImagePullBackOff)** — ruled out: the only scheduling/lifecycle event is `FailedScheduling`; there is no `Failed`/`ErrImagePull`/`BackOff` event, and pull is attempted only after scheduling, which never happened.
- **Insufficient node CPU/memory or resource-quota pressure** — ruled out: the pod is `QoS Class: BestEffort` (describe of pod shows no resource requests), and the scheduler's message names the specific reason `pod has unbound immediate PersistentVolumeClaims`, not `Insufficient cpu/memory`.
- **Taints / nodeSelector / affinity keeping it off the single node** — ruled out: describe of pod shows `Node-Selectors: <none>` and only the two default not-ready/unreachable tolerations; the scheduler's rejection reason is the volume binder, not a taint or predicate mismatch. Other pods (coredns, local-path-provisioner) schedule fine on that node.
- **Readiness probe failing / Service selector mismatch hiding a healthy pod** — ruled out: the StatefulSet template defines no probes, and `service/metrics-db` selector `app=metrics-db` matches the pod's label `app=metrics-db`. The pod is not Running at all, so readiness/endpoints are downstream, not causal.
- **Cluster-wide control-plane or CNI outage** — ruled out: `kube-apiserver`, `kube-scheduler`, `kube-controller-manager`, `etcd`, `kindnet`, `kube-proxy`, and both `coredns` pods are all `1/1 Running` with `0` restarts and 10h age; the scheduler is healthy enough to emit a precise rejection.
- **PVC exists but the underlying PV was deleted / RWO conflict with an old pod** — ruled out as the primary mechanism: retention policy is `WhenDeleted: Retain / WhenScaled: Retain`, the claim was freshly created this deploy (`SuccessfulCreate ... Create Claim data-metrics-db-0`, AGE `0s`), and no second pod exists anywhere in `kubectl get all -A` that could hold the volume. The failure is at class resolution (`not found`), before any PV is involved.

## Verification recipe

```bash
# 1. Is there a StorageClass named fast-ssd? (expect: "not found" / absent from list)
kubectl get storageclass

# 2. The PVC should be Pending with the class it can't resolve, plus a
#    "storageclass.storage.k8s.io \"fast-ssd\" not found" provisioning event.
kubectl describe pvc data-metrics-db-0 -n analytics

# 3. Confirm the class name comes from the StatefulSet's claim template.
kubectl get statefulset metrics-db -n analytics \
  -o jsonpath='{.spec.volumeClaimTemplates[*].spec.storageClassName}{"\n"}'
```

**Remediation (either one):**
- Point the claim template at a class that exists (e.g. the kind default `standard`). `volumeClaimTemplates` is immutable, so: `kubectl delete statefulset metrics-db -n analytics --cascade=orphan`, `kubectl delete pvc data-metrics-db-0 -n analytics` (the Pending claim has no data to lose), then re-apply the StatefulSet with the corrected `storageClassName`.
- Or, if `fast-ssd` is genuinely the intended tier, create the missing `StorageClass fast-ssd` backed by a real provisioner and let the existing PVC bind; then delete pod `metrics-db-0` so it reschedules.

```json
{
  "case_id": "t1-pvc-storageclass-typo",
  "failing_resource": {"kind": "StatefulSet", "namespace": "analytics", "name": "metrics-db"},
  "mechanism": "The StatefulSet's volumeClaimTemplate requests StorageClass 'fast-ssd', which does not exist in this cluster, so the generated PVC data-metrics-db-0 can never be provisioned or bound. Because that volume uses immediate binding, the scheduler rejects metrics-db-0 with 'pod has unbound immediate PersistentVolumeClaims. not found', leaving the pod Pending, the container never started, and the StatefulSet stuck at 0/1 Ready.",
  "verdict": "confirmed"
}
```