## Root cause

**Probable.** The `inventory-sync` Deployment in namespace `inventory` runs under a ServiceAccount that has no RBAC grant for the API objects the sync loop reads/writes. The worker process starts cleanly and its container stays `Running` (the container's main process is a long-lived loop, not a one-shot job), but every API call in the sync loop is rejected by the API server with `403 Forbidden`. The loop swallows the error and sleeps until the next tick, so the pod never crashes, never restarts, and never fails a probe — it just stops publishing inventory updates. The result is exactly the paged symptom: a healthy-looking worker with a frozen feed and counts older than 30 minutes.

The resource whose spec must change is the owning workload, `deployment.apps/inventory-sync` (its `serviceAccountName` / the RBAC binding attached to that identity) — not the pod it produced.

## Evidence chain

- **Source: `kubectl get all -A`, inventory row** — `pod/inventory-sync-5cf949f7f9-czxsq 1/1 Running 0 5s`. The pod is `Ready 1/1` with `RESTARTS 0`. This rules out the entire family of "the worker is dead" causes: no CrashLoopBackOff, no OOMKill, no ImagePullBackOff, no probe flapping. Whatever is wrong is happening *inside* a process that the kubelet considers perfectly healthy — a silent, non-fatal failure mode.
- **Source: `kubectl get all -A`, inventory deployment row** — `deployment.apps/inventory-sync 1/1 1 1 5s`. Desired=Ready=Available=1. The Deployment controller is fully satisfied; there is no rollout stuck, no unavailable replica, no scheduling gap. Kubernetes-level health is green while the business-level data freshness monitor is red — the classic signature of an authorization/permission denial rather than a lifecycle failure.
- **Source: the page text itself** — "The inventory-sync worker in namespace inventory is running... sold-out items still show as in stock and merchandising reports the feed is frozen." Frozen (not erratic, not partial) output with a live process is what a uniformly-denied API verb produces: every tick fails identically, so the last-written value is pinned forever.
- **Source: `kubectl get all -A`, kube-system rows** — `coredns` 2/2 Running, `kindnet` 1/1, `kube-proxy` 1/1, `etcd`/`kube-apiserver`/`kube-controller-manager`/`kube-scheduler` all `1/1 Running 0 10h`. The control plane and cluster networking are intact, so the sync worker can reach the API server; the failure is at the authorization layer, not the connectivity layer.
- **Source: `kubectl get all -A`, services section** — only `default/kubernetes` (ClusterIP 10.96.0.1:443) and `kube-system/kube-dns` exist. The in-cluster API endpoint the worker would talk to is present and healthy; there is no separate inventory backend Service in this cluster that could be the frozen dependency.
- **Coverage limitation (stated plainly):** `kubectl get all` does not enumerate `ServiceAccount`, `Role`, `ClusterRole`, `RoleBinding`, or `ClusterRoleBinding`. The absence of any RBAC object in this output is therefore *expected* and is not itself proof — but it also means the provided output cannot exclude a missing binding, which is the only mechanism left standing after the eliminations below. This is why the verdict is **probable** rather than **confirmed**: the decisive artifact is a `403 Forbidden` line in the worker's logs or a `kubectl auth can-i --as=system:serviceaccount:inventory:<sa>` returning `no`.

## Investigation ledger

- **Worker crashed / is restarting** — ruled out. `RESTARTS 0` and `STATUS Running` on `pod/inventory-sync-5cf949f7f9-czxsq`.
- **Image pull failure or bad image tag** — ruled out. Image `busybox:1.36` is resolved and the container reached `Running 1/1`; a pull failure would show `ImagePullBackOff`/`ErrImagePull`.
- **Pod unschedulable / node pressure / resource starvation** — ruled out. The pod is bound to `incident-lab-control-plane` with IP `10.244.0.125` and is `Running`; there is no `Pending` pod anywhere in the output.
- **Deployment scaled to zero or rollout stuck** — ruled out. `deployment.apps/inventory-sync 1/1 1 1` — desired, up-to-date, and available all equal 1, and the single ReplicaSet `inventory-sync-5cf949f7f9` reports `1 1 1`.
- **Multiple competing ReplicaSets / orphaned old pods double-writing** — ruled out. Exactly one ReplicaSet exists for the deployment and it owns exactly one pod.
- **DNS resolution failure preventing the worker from reaching its dependency** — ruled out as the primary cause. Both `coredns` pods are `1/1 Running` with `0` restarts for `10h`, and `service/kube-dns` has a healthy ClusterIP `10.96.0.10` with the `k8s-app=kube-dns` selector matching the running pods.
- **CNI / kube-proxy breakage isolating the pod from the API server** — ruled out. `kindnet` and `kube-proxy` DaemonSets both report `DESIRED 1 / CURRENT 1 / READY 1 / AVAILABLE 1`, and the pod received a routable pod IP in the `10.244.0.0/16` range.
- **API server or etcd degraded, so writes silently fail cluster-wide** — ruled out. `etcd-...` and `kube-apiserver-...` are both `1/1 Running 0 10h`; a degraded control plane would also stall coredns and the local-path provisioner, which are healthy.
- **Missing Service/Endpoints for the inventory backend** — ruled out as evidenced here. No inventory Service is defined in the manifest set at all, so the worker is not depending on a broken in-cluster Service object; the only API endpoint in play is `default/kubernetes`.
- **Wrong/expired credentials in a Secret (authentication rather than authorization)** — not fully excludable from this output, but less consistent: a pod using its projected ServiceAccount token authenticates automatically, and a token-mount failure would typically surface as a container start error rather than a healthy `1/1 Running` pod. Named here as the residual alternative that the verification commands below also distinguish (`401 Unauthorized` in logs would point here instead of `403 Forbidden`).
- **The `5s` age of the deployment/pod** — considered as a signal that someone just restarted the worker to clear the stall (consistent with a 30-minute-stale feed being noticed and bounced). Note that a fresh pod cannot itself explain 30 minutes of staleness, and the restart evidently did *not* fix the feed, which argues for a persistent configuration-level cause rather than a transient one.

## Verification recipe

```bash
# 1. The decisive artifact: look for 403 Forbidden in the worker's own output.
kubectl -n inventory logs deploy/inventory-sync --tail=50

# 2. Identify the ServiceAccount the Deployment runs as, then test its rights directly.
kubectl -n inventory get deploy inventory-sync \
  -o jsonpath='{.spec.template.spec.serviceAccountName}{"\n"}'
kubectl -n inventory auth can-i --list \
  --as=system:serviceaccount:inventory:$(kubectl -n inventory get deploy inventory-sync \
  -o jsonpath='{.spec.template.spec.serviceAccountName}{"\n"}' | sed 's/^$/default/')

# 3. Confirm no Role/RoleBinding grants that identity anything in the namespace.
kubectl -n inventory get sa,role,rolebinding
kubectl get clusterrolebinding -o wide | grep -i inventory
```

Expected if the root cause holds: step 1 shows repeated `is forbidden: User "system:serviceaccount:inventory:<sa>" cannot list/watch/update <resource> in the namespace "inventory"`; step 2's `auth can-i --list` shows only the default `selfsubjectreviews`/`selfsubjectaccessreviews` entries; step 3 returns the ServiceAccount with `No resources found` for roles and bindings.

**Remediation:** create a `Role` in namespace `inventory` granting the verbs the sync loop needs (typically `get,list,watch` plus `update`/`patch` on the inventory-backing objects) and a `RoleBinding` tying it to the Deployment's ServiceAccount; if the Deployment currently runs as `default`, also set an explicit `spec.template.spec.serviceAccountName` on `deployment.apps/inventory-sync` so the grant is scoped to a dedicated identity. Roll the Deployment afterward and confirm the freshness monitor recovers.

```json
{
  "case_id": "t2-rbac-sync-forbidden",
  "failing_resource": {"kind": "Deployment", "namespace": "inventory", "name": "inventory-sync"},
  "mechanism": "The inventory-sync Deployment runs under a ServiceAccount that has no RBAC Role/RoleBinding for the inventory objects its sync loop reads and updates, so every API call in the loop is rejected with 403 Forbidden. The container's long-lived loop catches the error and sleeps instead of exiting, so the pod stays 1/1 Running with 0 restarts while publishing no updates, leaving storefront counts frozen past the 30-minute freshness threshold.",
  "verdict": "probable",
  "missing_evidence": "A 403 Forbidden line in `kubectl -n inventory logs deploy/inventory-sync`, or `kubectl auth can-i --list --as=system:serviceaccount:inventory:<sa>` showing no grants plus an empty `kubectl -n inventory get role,rolebinding`. `kubectl get all` does not list RBAC objects, so the provided output cannot show the missing binding directly."
}
```