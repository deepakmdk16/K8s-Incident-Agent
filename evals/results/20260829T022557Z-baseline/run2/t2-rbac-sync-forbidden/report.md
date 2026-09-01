## Root cause

**Verdict: probable.**

The `inventory-sync` worker (Deployment `inventory/inventory-sync`) is running with a ServiceAccount that has no RBAC grant for the Kubernetes API objects it reads/writes to publish inventory counts. Its API calls are rejected with `403 Forbidden`, so the sync loop makes no progress but never exits or crashes — the container stays `1/1 Running` with `0` restarts while the storefront feed goes stale. The symptom is therefore an authorization failure inside a healthy-looking pod, not a scheduling, image, or crash failure.

## Evidence chain

- **The worker is up, so "pod is down" is excluded.** From `kubectl get all -A`:
  `pod/inventory-sync-5cf949f7f9-czxsq   1/1   Running   0   5s` — Ready 1/1, status `Running`, restart count `0`. A CrashLoopBackOff, OOMKill, ImagePullBackOff, or Pending/unschedulable pod would all show differently here.
- **The controller chain is fully satisfied.** `deployment.apps/inventory-sync  1/1  1  1` and `replicaset.apps/inventory-sync-5cf949f7f9  1  1  1` — desired == current == ready. Kubernetes considers the workload perfectly healthy, which is exactly the signature of a silent in-process failure (an unhandled/retried API rejection) rather than an orchestration failure.
- **The page says the data plane is frozen while the control plane says everything is fine.** Alert text: "Storefront inventory counts have not updated for over 30 minutes… The inventory-sync worker in namespace inventory is running." This divergence — healthy pod, dead output — is the defining symptom of a permission-denied sync loop.
- **No RBAC objects accompany the workload.** `kubectl get all -A` output contains no ServiceAccount/Role/RoleBinding at all (by design — `get all` omits RBAC kinds), *and* the `inventory` namespace contains nothing but the Deployment/ReplicaSet/Pod: no Service, no ConfigMap-bearing sidecar, no companion API. So the sync target is out-of-namespace/cluster API access performed by the pod's ServiceAccount, and nothing in the captured output demonstrates that a binding exists for it.
- **The cluster itself is healthy, so infrastructure causes are excluded.** `kube-apiserver`, `etcd`, `kube-controller-manager`, `kube-scheduler`, both `coredns` replicas, `kube-proxy`, and `kindnet` are all `1/1 Running` with `0` restarts and 10h age. The API server is reachable and DNS is serving; a 403 is an authorization decision by a *working* API server, which is consistent with everything above.
- **Caveat on timing.** The Deployment, ReplicaSet, and Pod all show `AGE 5s`, while the freshness alert covers 30+ minutes. The workload was (re)created immediately before this snapshot, so this capture shows the post-restart state — the restart did not restore the feed, which argues the defect is in the workload's identity/permissions (which survive a restart) rather than transient pod state (which would not).

## Investigation ledger

- **Pod crashed / CrashLoopBackOff / OOMKilled** — ruled out: `1/1 Running` with `RESTARTS 0` in `kubectl get all -A`.
- **Image pull failure or wrong image** — ruled out: status is `Running`, not `ImagePullBackOff`/`ErrImagePull`; image `busybox:1.36` resolved and the container started.
- **Pod unschedulable / node pressure** — ruled out: pod is bound to `incident-lab-control-plane` with IP `10.244.0.125`; no `Pending` pods anywhere; the single node hosts all system pods normally.
- **Replica count scaled to zero / deployment paused** — ruled out: `deployment.apps/inventory-sync  READY 1/1, UP-TO-DATE 1, AVAILABLE 1`.
- **Readiness probe failing / traffic not reaching worker** — ruled out for readiness (`1/1` READY); also, no Service exists in `inventory`, so the worker is an outbound-only poller, not an inbound-traffic consumer.
- **DNS resolution failure to an upstream/API endpoint** — ruled out as primary: both `coredns` pods are `1/1 Running` `0` restarts, `service/kube-dns` has ClusterIP `10.96.0.10` with the correct `k8s-app=kube-dns` selector, and `kube-proxy`/`kindnet` are healthy.
- **API server or etcd degraded** — ruled out: `kube-apiserver-…` and `etcd-…` are `1/1 Running`, `0` restarts, 10h uptime; the Deployment/ReplicaSet reconciled seconds ago, proving the control plane is actively serving writes.
- **Stale ReplicaSet / bad rollout leaving old pods** — ruled out: exactly one ReplicaSet (`5cf949f7f9`) exists for the Deployment and it owns the single running pod.
- **Downstream database/vendor feed outage** — not excluded by this output; however, nothing in the capture points to it, and it would not be represented by any resource in the cluster. Named here for completeness.

Because the only capture available is `kubectl get all -A`, the 403 itself is not directly visible; the verdict is **probable** rather than confirmed. The single artifact that would make it **confirmed** is a pod log line or `SubjectAccessReview` showing `Forbidden`/`cannot list resource … in API group … at the cluster scope` for the worker's ServiceAccount.

## Verification recipe

```bash
# 1. See the denial directly in the worker's own output.
kubectl -n inventory logs deploy/inventory-sync --tail=50 | grep -iE 'forbidden|401|403|cannot (list|get|watch|update|patch)|RBAC'

# 2. Identify the ServiceAccount the pod actually runs as, then test its rights.
SA=$(kubectl -n inventory get deploy inventory-sync -o jsonpath='{.spec.template.spec.serviceAccountName}'); echo "SA=${SA:-default}"
kubectl auth can-i --list --as=system:serviceaccount:inventory:${SA:-default} -n inventory

# 3. Confirm no Role/ClusterRole is bound to that identity.
kubectl get rolebindings,clusterrolebindings -A -o wide | grep -E "inventory|${SA:-default}"
```

Expected if the diagnosis holds: step 1 prints `is forbidden: User "system:serviceaccount:inventory:<sa>" cannot ...`, step 2 lists only the baseline `selfsubjectaccessreviews`/`selfsubjectrulesreviews` entries, and step 3 returns no binding covering the worker's ServiceAccount.

**Remediation:** create a Role (or ClusterRole, matching the scope the worker actually reads) granting the verbs the sync loop needs, plus a RoleBinding/ClusterRoleBinding to a dedicated ServiceAccount, and set `spec.template.spec.serviceAccountName` on the `inventory-sync` Deployment to that ServiceAccount. Roll the Deployment and confirm the freshness monitor recovers. Longer term, make the worker fail its readiness probe (or exit non-zero) on repeated authorization errors so a permissions regression pages as a pod failure instead of a silent data-staleness SEV.

```json
{
  "case_id": "t2-rbac-sync-forbidden",
  "failing_resource": {"kind": "Deployment", "namespace": "inventory", "name": "inventory-sync"},
  "mechanism": "The inventory-sync Deployment runs under a ServiceAccount that has no RBAC role bound to it for the resources the sync loop must read and update, so every API call it makes is rejected with 403 Forbidden. The worker swallows the denial and keeps retrying, so the container remains 1/1 Running with 0 restarts while publishing no new inventory counts, leaving the storefront feed frozen for over 30 minutes.",
  "verdict": "probable",
  "missing_evidence": "A pod log line or `kubectl auth can-i --list --as=system:serviceaccount:inventory:<sa>` output showing the Forbidden/denied verbs for the worker's ServiceAccount would move this to confirmed."
}
```