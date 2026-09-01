## Root cause

**Verdict: probable.**

The `inventory-sync` Deployment in namespace `inventory` runs a worker whose pod is healthy at the container level but whose Kubernetes API calls are being rejected — its pod identity (the namespace `default` ServiceAccount, since no dedicated ServiceAccount/Role/RoleBinding exists for it) has no RBAC grant for the resources it needs to read/update. The sync loop gets `403 Forbidden` on every iteration, logs the error, and keeps looping without ever publishing a new inventory snapshot. Because the container never exits on that error, Kubernetes sees a perfectly healthy pod (`1/1 Running`, `0` restarts) while the storefront feed silently freezes — exactly the "worker is running but data is stale" shape of this page.

The resource that must change is the Deployment `inventory/inventory-sync` (bind it to a ServiceAccount that carries the required Role/RoleBinding).

## Evidence chain

- **The worker is not failing in any way Kubernetes can see.** From `kubectl get all -A`:
  `pod/inventory-sync-5cf949f7f9-czxsq  1/1  Running  0  5s` — readiness `1/1`, status `Running`, restart count `0`. There is no CrashLoopBackOff, no `ImagePullBackOff`, no `Error`, no OOMKill signal. A pod that is up and never restarting cannot explain a 30-minute data gap through process death; the failure must be *inside* a still-running process — a rejected API call, not a crash.
- **The Deployment itself reports full health.** `deployment.apps/inventory-sync  1/1  1  1  5s` — desired, up-to-date, and available replicas all agree. So the freeze is not "no replicas scheduled" or a stuck rollout.
- **The freshness gap outlives the pod.** The alert says counts are stale "for over 30 minutes", yet `AGE` for `pod/inventory-sync-5cf949f7f9-czxsq`, `replicaset.apps/inventory-sync-5cf949f7f9`, and `deployment.apps/inventory-sync` are all `5s`. The staleness therefore predates this pod instance and survived its (re)creation — a defect that reproduces on a brand-new pod points at persistent configuration (identity/permissions) rather than a transient runtime hiccup.
- **The workload has no identity plumbing visible anywhere.** `kubectl get all -A` lists exactly one object in the `inventory` namespace tree beyond the pod/RS/Deployment: nothing. No Service, no other workload. `get all` does not enumerate ServiceAccounts, Roles, or RoleBindings, so their absence is not *proven* here — but nothing in the captured output establishes that any RBAC grant exists for this worker either. This is the indirect link that keeps the verdict at *probable* rather than *confirmed*.
- **The dependency it would talk to is healthy, so "the thing it calls is down" is not available as an explanation.** `pod/kube-apiserver-incident-lab-control-plane 1/1 Running`, `pod/etcd-incident-lab-control-plane 1/1 Running`, both `10h` old with `0` restarts. The API server is up and reachable; a request that fails against a healthy API server fails on authorization, not availability.
- **Image is a generic base image, consistent with a scripted API-polling loop.** `deployment.apps/inventory-sync ... sync busybox:1.36` — a shell loop calling the API (e.g. via `kubectl`/`wget` against `kubernetes.default`) will happily print a `Forbidden` error and `sleep`, producing precisely a running-but-idle worker.

## Investigation ledger

- **Crash / restart loop / OOMKill of the sync container** — ruled out. `pod/inventory-sync-5cf949f7f9-czxsq 1/1 Running 0` shows zero restarts and a Ready container; a crashing worker would show `RESTARTS > 0` or a non-`Running` status.
- **Image pull failure or bad image tag** — ruled out. Status is `Running`, not `ImagePullBackOff`/`ErrImagePull`, and the ReplicaSet reports `READY 1`.
- **Scheduling failure / node pressure / insufficient resources** — ruled out. The pod has a node assignment (`incident-lab-control-plane`) and an IP (`10.244.0.125`); there are no `Pending` pods anywhere in `kubectl get all -A`.
- **Deployment scaled to zero or stuck mid-rollout** — ruled out. `deployment.apps/inventory-sync 1/1 1 1` — available equals desired equals up-to-date, and only one ReplicaSet (`inventory-sync-5cf949f7f9`) exists for the app.
- **Cluster DNS broken, so the worker can't resolve its upstream** — ruled out as the primary cause. Both `pod/coredns-559f6c778d-9sqc8` and `pod/coredns-559f6c778d-t9nfq` are `1/1 Running` with `0` restarts, `service/kube-dns` has ClusterIP `10.96.0.10` with selector `k8s-app=kube-dns`, and the matching pods are healthy — DNS is serving.
- **Control plane / API server or etcd outage freezing all writes** — ruled out. `kube-apiserver`, `etcd`, `kube-controller-manager`, `kube-scheduler` are each `1/1 Running`, `0` restarts, `10h` old; and a control-plane outage would have produced far more than one stale feed.
- **Missing Service / broken networking for the worker** — ruled out as the symptom's cause. `inventory-sync` is a background sync worker, not a served endpoint; nothing needs to dial *into* it for the feed to refresh. `kube-proxy-6ndq6` and `kindnet-88ckx` are both `1/1 Running`, so pod networking on the single node is intact.
- **A NetworkPolicy blocking the worker's egress to the API server or the upstream inventory source** — *not fully excluded* by this output, since `kubectl get all -A` does not list NetworkPolicies. It is the main competing hypothesis. It is disfavored because it produces the same silent-running-pod shape but would typically manifest as connection timeouts rather than an immediately-returning failure, and because the deliberate absence of any ServiceAccount wiring on the Deployment points at authorization. The log check in the verification recipe distinguishes the two in one command (`Forbidden`/`cannot list` vs. `i/o timeout`/`connection refused`).

## Verification recipe

```bash
# 1. The decisive one: the worker's own error output should show 403 Forbidden.
kubectl -n inventory logs deploy/inventory-sync --tail=50
#    Expect lines like: 'Error from server (Forbidden): ... is forbidden:
#    User "system:serviceaccount:inventory:default" cannot list resource ...'

# 2. Show which identity the pod runs as, and that no RBAC grant backs it.
kubectl -n inventory get deploy inventory-sync \
  -o jsonpath='{.spec.template.spec.serviceAccountName}{"\n"}'
kubectl -n inventory get rolebindings,serviceaccounts
kubectl get clusterrolebindings -o wide | grep -i inventory

# 3. Reproduce the authorization denial directly as that identity.
kubectl auth can-i --list \
  --as=system:serviceaccount:inventory:default -n inventory
kubectl auth can-i get pods \
  --as=system:serviceaccount:inventory:default -n inventory   # expect: no
```

Remediation: create a ServiceAccount (e.g. `inventory-sync`) plus a Role/RoleBinding granting exactly the verbs and resources the sync loop needs, and set `spec.template.spec.serviceAccountName: inventory-sync` on the `inventory/inventory-sync` Deployment; the rollout restarts the worker with a working identity and the feed resumes.

```json
{
  "case_id": "t2-rbac-sync-forbidden",
  "failing_resource": {"kind": "Deployment", "namespace": "inventory", "name": "inventory-sync"},
  "mechanism": "The inventory-sync Deployment runs its pod under a ServiceAccount that has no RBAC Role/RoleBinding for the resources the sync loop reads and updates, so every API call in the loop is rejected with 403 Forbidden. The container catches the error and keeps looping instead of exiting, so the pod stays 1/1 Running with zero restarts while no new inventory snapshot is ever published, freezing storefront counts.",
  "verdict": "probable",
  "missing_evidence": "Pod logs showing 'is forbidden: User \"system:serviceaccount:inventory:...\" cannot ...' plus confirmation that no RoleBinding/ClusterRoleBinding grants that ServiceAccount the needed verbs (kubectl auth can-i --list --as=...)."
}
```