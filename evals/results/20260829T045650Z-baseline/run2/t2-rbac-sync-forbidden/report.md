## Root cause

**Probable.** The `inventory-sync` worker Deployment in namespace `inventory` is running with a ServiceAccount that has no RBAC grant for the API objects it must read/write to publish inventory counts. Every sync iteration's API call is rejected with `Forbidden` (403), the worker's shell/loop container swallows the error and keeps looping, so the container stays `Running` and healthy while the storefront feed never advances. Because authorization — not the process — is broken, the pod looks perfectly healthy from Kubernetes' point of view, which is exactly why the only signal is the external data-freshness monitor.

The resource that must change is the `inventory-sync` Deployment (bind its ServiceAccount to a Role/ClusterRole carrying the required verbs, or point `spec.template.spec.serviceAccountName` at a ServiceAccount that already has them).

## Evidence chain

- **Symptom is data staleness, not process failure.** Alert text: *"Storefront inventory counts have not updated for over 30 minutes… The inventory-sync worker in namespace inventory is running."* Kubernetes health signals and the business signal disagree — the classic shape of a permission/authorization denial inside a loop that ignores errors.
- **The workload is healthy by every kubelet-visible measure.** From `kubectl get all -A`: `pod/inventory-sync-5cf949f7f9-czxsq   1/1   Running   0   5s`. No restarts, no CrashLoopBackOff, no `Error`, no `ImagePullBackOff`. So the container is not crashing, not OOM-killed, and not failing to start — the work is failing *inside* a running process.
- **Deployment reports fully satisfied.** `deployment.apps/inventory-sync   1/1   1   1   5s` and `replicaset.apps/inventory-sync-5cf949f7f9   1   1   1   5s` — desired == current == ready. There is no scheduling, capacity, or rollout problem to explain a frozen feed.
- **A restart did not fix it.** The Deployment, ReplicaSet and Pod are all `5s` old, yet the data has been stale for **over 30 minutes**. A freshly created/restarted worker that still produces no updates rules out a transient hang or a wedged process and points to a persistent, environmental precondition (authorization) that a restart cannot clear.
- **The container image is a bare `busybox:1.36`** (`CONTAINERS: sync   IMAGES: busybox:1.36`). A busybox-based sync loop is typically `while true; do kubectl/wget …; sleep N; done`, which does not exit on a non-zero API response — consistent with a 403 being retried forever with `RESTARTS 0`.
- **No supporting RBAC objects are visible for the namespace.** `kubectl get all -A` deliberately does not include ServiceAccounts, Roles, RoleBindings, ClusterRoles or ClusterRoleBindings, so the grant cannot be seen here — its absence is not proven by this output, which is why this is *probable* rather than *confirmed*. The verification commands below close that gap.
- **The rest of the cluster is healthy**, so no infrastructure dependency explains the freeze: `coredns` 2/2 `Running`, `etcd`, `kube-apiserver`, `kube-controller-manager`, `kube-scheduler`, `kube-proxy`, `kindnet` all `1/1 Running` with `0` restarts, and `service/kube-dns` present with `SELECTOR k8s-app=kube-dns` matching the running CoreDNS pods.

## Investigation ledger

- **Crash loop / bad image / OOM in the worker** — ruled out: `1/1 Running`, `RESTARTS 0`, status is not `CrashLoopBackOff`/`Error`/`ImagePullBackOff`.
- **Pod unschedulable or node pressure** — ruled out: the pod is bound to `incident-lab-control-plane` with IP `10.244.0.125`, `NOMINATED NODE <none>`, and the Deployment reports `AVAILABLE 1`.
- **Deployment scaled to zero / rollout stuck on an old ReplicaSet** — ruled out: exactly one ReplicaSet `inventory-sync-5cf949f7f9` exists with `DESIRED 1 / CURRENT 1 / READY 1`; there is no superseded ReplicaSet lingering with replicas.
- **DNS resolution failure preventing the worker from reaching the API server or upstream feed** — ruled out as the primary cause: both `coredns` pods are `1/1 Running` with `0` restarts, and `service/kube-dns` at `10.96.0.10` has a selector (`k8s-app=kube-dns`) that matches them. A DNS outage would also typically surface across other workloads, and none are impaired.
- **API server / etcd / control-plane outage** — ruled out: `kube-apiserver`, `etcd`, `kube-controller-manager`, `kube-scheduler` are each `1/1 Running`, `0` restarts, `10h` old; the Deployment/ReplicaSet/Pod created `5s` ago prove the control plane is currently accepting and acting on writes.
- **Missing/misrouted Service for the worker** — ruled out as relevant: the only Services are `default/kubernetes` and `kube-system/kube-dns`. `inventory-sync` is an outbound worker (no Service, no ports), so nothing needs to route *to* it; a missing Service cannot freeze an egress sync loop.
- **kube-proxy / CNI networking break** — ruled out: `kube-proxy-6ndq6` and `kindnet-88ckx` are `1/1 Running`, `0` restarts, and both DaemonSets show `DESIRED 1 / READY 1 / AVAILABLE 1`; the new pod received a pod IP from the `10.244.0.0/16` range.
- **Stale upstream/third-party inventory feed (problem outside the cluster)** — not fully excludable from this output alone, but it does not explain why the symptom persists identically across a brand-new pod while every cluster component is healthy; an RBAC denial explains both the silence and the persistence. This is the main reason the verdict is *probable*, not *confirmed*.

## Verification recipe

```bash
# 1. Identify the ServiceAccount the worker actually runs as.
kubectl -n inventory get deploy inventory-sync \
  -o jsonpath='{.spec.template.spec.serviceAccountName}{"\n"}'

# 2. Ask the API server directly whether that identity is authorized
#    (substitute the SA name from step 1; "default" if the field was empty).
kubectl -n inventory auth can-i --list \
  --as=system:serviceaccount:inventory:default
kubectl -n inventory auth can-i list pods \
  --as=system:serviceaccount:inventory:default      # expect: no

# 3. Confirm the denial is what the worker is hitting, and that no binding exists.
kubectl -n inventory logs deploy/inventory-sync --tail=50 | grep -iE 'forbidden|403|cannot|denied|RBAC'
kubectl -n inventory get rolebindings,roles
kubectl get clusterrolebindings -o wide | grep -i 'inventory'
```

A `Forbidden … cannot list resource … in API group … in the namespace "inventory"` line in the worker log plus an empty/irrelevant RoleBinding set turns this verdict to **confirmed**. Remediation: create a Role (or reuse a ClusterRole) with the verbs the sync loop needs, bind it to the worker's ServiceAccount, and set `spec.template.spec.serviceAccountName` on the `inventory-sync` Deployment to that ServiceAccount; the next sync tick then unfreezes the feed.

```json
{
  "case_id": "t2-rbac-sync-forbidden",
  "failing_resource": {"kind": "Deployment", "namespace": "inventory", "name": "inventory-sync"},
  "mechanism": "The inventory-sync Deployment runs under a ServiceAccount that holds no RBAC grant for the API resources its sync loop reads and updates, so every iteration's API request is rejected with 403 Forbidden. The busybox-based loop ignores the error and keeps retrying, so the container stays 1/1 Running with 0 restarts while inventory counts never advance, leaving only the external data-freshness monitor to fire.",
  "verdict": "probable",
  "missing_evidence": "Worker container logs showing 'Forbidden'/403 API responses, plus the output of 'kubectl -n inventory auth can-i --list --as=system:serviceaccount:inventory:<sa>' and the absence of a matching Role/RoleBinding, would make this confirmed."
}
```