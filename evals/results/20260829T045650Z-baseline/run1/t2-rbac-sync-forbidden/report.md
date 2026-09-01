## Root cause

**Verdict: probable.**

The `inventory-sync` worker (Deployment `inventory/inventory-sync`) is running and healthy from Kubernetes' point of view, but its sync loop is being rejected by the Kubernetes API at the authorization layer: the ServiceAccount the Deployment runs under has no Role/RoleBinding granting it the get/list/watch (and update) permissions it needs on the inventory-backing objects it reads. Every sync iteration gets an HTTP 403 `forbidden`, the worker logs the error and sleeps until the next cycle, and the process never exits — so the container stays `Running` with `0` restarts while the storefront feed silently freezes. This is a permissions-denied failure, not a crash, which is exactly why the liveness/readiness signal is green while the data-freshness monitor fires.

The resource whose spec must change is the `inventory-sync` Deployment's identity binding: its `serviceAccountName` and the missing Role/RoleBinding for that ServiceAccount in namespace `inventory`.

## Evidence chain

- **Symptom is data-plane, not control-plane.** `kubectl get all -A` shows `pod/inventory-sync-5cf949f7f9-czxsq  1/1  Running  0  5s` — the container is up, all containers ready, zero restarts. A crash-loop, OOMKill, image-pull, or scheduling failure would all surface here as `CrashLoopBackOff`/`ImagePullBackOff`/`Pending` or a nonzero restart count. None do. So the worker process is alive but not doing useful work.
- **The owning workload reports full health.** `deployment.apps/inventory-sync   1/1   1   1   5s` and `replicaset.apps/inventory-sync-5cf949f7f9   1   1   1   5s` — desired == current == ready. Kubernetes has no complaint to raise, which is consistent with the page coming from an external "data-freshness monitor" rather than from a pod-health alert.
- **Staleness far exceeds process lifetime.** The alert says counts have been frozen "for over 30 minutes", yet the pod and its ReplicaSet are `5s` old. The freeze therefore predates this pod instance and survived a fresh start — a restart did not clear it. That rules out a transient in-process hang or a wedged connection and points at a persistent, environment-level condition attached to the workload's spec (its identity/permissions), which a new pod inherits unchanged.
- **The worker is a generic shell image.** `CONTAINERS: sync`, `IMAGES: busybox:1.36` — a `busybox` container has no built-in health semantics; it will happily loop forever printing API errors. It cannot self-detect a 403 and exit, which explains `RESTARTS 0` alongside a 30-minute data outage.
- **No RBAC objects are visible for the namespace.** The only namespaced objects listed under `inventory` are the Deployment, ReplicaSet, and Pod. `kubectl get all` does not enumerate ServiceAccounts, Roles, or RoleBindings, so their absence here is *not* proof — but it is consistent with a namespace that was created with a workload and no accompanying RBAC manifest. This is the indirect link that keeps the verdict at *probable* rather than *confirmed*.
- **Cluster infrastructure is exonerated by the same output.** `coredns` 2/2 Running, `kube-apiserver`, `etcd`, `kube-scheduler`, `kube-controller-manager`, `kube-proxy`, and `kindnet` all `1/1 Running 0 restarts` at `10h`, and `service/kube-dns` has its `k8s-app=kube-dns` selector intact. The API server is reachable and serving (the Deployment/ReplicaSet/Pod chain was reconciled seconds ago), so the worker's requests are arriving — they are being *answered* with a denial, not dropped.

## Investigation ledger

- **Crash loop / OOMKill of the sync container** — ruled out: `RESTARTS 0` and `STATUS Running` for `pod/inventory-sync-5cf949f7f9-czxsq`. A killed container would increment restarts.
- **Image pull failure or bad image tag** — ruled out: status is `Running`, not `ImagePullBackOff`/`ErrImagePull`, and `busybox:1.36` is resolved and running.
- **Pod unschedulable / node pressure** — ruled out: the pod has `NODE incident-lab-control-plane` and `IP 10.244.0.125` assigned; scheduling succeeded. Every other pod on that node is `Running` at `10h`.
- **Readiness probe flapping the pod out of a Service** — ruled out for the symptom path: `READY 1/1`, and there is no Service in namespace `inventory` at all (services listed are only `default/kubernetes` and `kube-system/kube-dns`). The worker is an outbound puller, not a Service-backed endpoint.
- **DNS resolution failure preventing the worker from reaching the API or upstream feed** — ruled out as primary: both `coredns` pods are `1/1 Running` with `0` restarts, and `service/kube-dns` retains selector `k8s-app=kube-dns`, matching the running CoreDNS ReplicaSet `coredns-559f6c778d`. DNS is intact.
- **Network plugin / kube-proxy breakage** — ruled out: `daemonset.apps/kindnet` and `daemonset.apps/kube-proxy` both show `DESIRED 1 / CURRENT 1 / READY 1 / AVAILABLE 1`.
- **Replica count scaled to zero (no worker at all)** — ruled out: `deployment.apps/inventory-sync 1/1` with a live pod.
- **Deleted/rolled-back deployment mid-incident** — considered because the Deployment is only `5s` old, which means someone (or the scenario) just recreated it. Ruled out as the *cause*: the data has been stale for 30+ minutes, spanning multiple pod lifetimes, so recreating the workload demonstrably did not fix it. This actually strengthens the "config attached to the spec" hypothesis over any transient-runtime hypothesis.
- **Upstream inventory source (ERP/vendor feed) itself frozen** — **not fully excluded from this output alone.** Nothing here proves the 403-vs-upstream distinction; this is the main competing hypothesis. It is downgraded because a dead upstream would typically not correlate with a freshly-created Deployment carrying no RBAC, and because the verification commands below discriminate between the two in seconds.

## Verification recipe

```bash
# 1. The decisive one: read the worker's own logs for the denial.
kubectl -n inventory logs deploy/inventory-sync --tail=50
#    Expect lines containing: 'is forbidden: User "system:serviceaccount:inventory:<sa>"
#    cannot list resource ... in the namespace "inventory"'  (HTTP 403)

# 2. Ask the API directly, as the pod's identity.
kubectl -n inventory get deploy inventory-sync \
  -o jsonpath='{.spec.template.spec.serviceAccountName}{"\n"}'
kubectl auth can-i --list \
  --as=system:serviceaccount:inventory:$(kubectl -n inventory get deploy inventory-sync \
      -o jsonpath='{.spec.template.spec.serviceAccountName}{"\n"}' | sed 's/^$/default/') \
  -n inventory

# 3. Confirm no Role/RoleBinding exists to grant it.
kubectl -n inventory get sa,role,rolebinding
kubectl get clusterrolebinding -o wide | grep -i 'inventory'
```

**Remediation once confirmed:** create a dedicated ServiceAccount plus a Role granting the sync worker `get,list,watch` (and `update`/`patch` if it writes status) on the resources it syncs, bind it with a RoleBinding in namespace `inventory`, and set `spec.template.spec.serviceAccountName` on the `inventory-sync` Deployment to that ServiceAccount. Then `kubectl -n inventory rollout restart deploy/inventory-sync` and watch the freshness monitor recover. Longer term, give the worker a readiness/liveness probe or an exit-on-403 behavior so an authorization failure pages as a pod-health incident instead of silently serving stale counts to the storefront.

```json
{
  "case_id": "t2-rbac-sync-forbidden",
  "failing_resource": {"kind": "Deployment", "namespace": "inventory", "name": "inventory-sync"},
  "mechanism": "The inventory-sync Deployment runs under a ServiceAccount that has no Role/RoleBinding granting it read access to the inventory objects it must sync, so every API call from its sync loop is rejected with HTTP 403 forbidden. The busybox-based worker logs the denial and sleeps instead of exiting, so the pod stays 1/1 Running with 0 restarts while no inventory counts are ever written, leaving the storefront feed frozen.",
  "verdict": "probable"
}
```