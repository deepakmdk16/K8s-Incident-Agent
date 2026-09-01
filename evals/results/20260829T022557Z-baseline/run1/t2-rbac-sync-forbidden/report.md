## Root cause

**Deployment `inventory/inventory-sync` (its pod's ServiceAccount identity) is not authorized against the Kubernetes API for the objects it syncs, so every sync loop iteration gets `403 Forbidden`, the worker silently retries, and no inventory counts are ever written — while the container itself stays healthy and `Running`.**

The workload is not crashing, not restarting, not unscheduled and not image-broken. It is a *running-but-doing-nothing* failure: the process is alive (so the container probe/PID stays up and the alert source is a data-freshness monitor, not a pod-health monitor), but its API requests are denied. Nothing in the cluster grants the `inventory` namespace's ServiceAccount the read/write permissions the sync loop needs, and no Role/RoleBinding appears anywhere for it. The fix has to change the Deployment's identity + its RBAC grant, not the pod.

## Evidence chain

- **The worker is up, so "pod is down" is off the table.** From `kubectl get all -A`:
  `inventory pod/inventory-sync-5cf949f7f9-czxsq 1/1 Running 0 5s` — `READY 1/1`, `STATUS Running`, `RESTARTS 0`. The container is not crash-looping, not `CrashLoopBackOff`, not `Error`, not `ImagePullBackOff`.
- **The Deployment is fully satisfied.** `inventory deployment.apps/inventory-sync 1/1 1 1 AVAILABLE 1` and `replicaset.apps/inventory-sync-5cf949f7f9 DESIRED 1 CURRENT 1 READY 1`. Kubernetes believes the workload is perfectly healthy — exactly the discrepancy the page describes ("The inventory-sync worker in namespace inventory is running").
- **A restart has already been tried and did not fix it.** Pod `AGE 5s`, Deployment `AGE 5s`, ReplicaSet `AGE 5s`, but the page says counts have been stale "for over 30 minutes". The workload was recreated within the last few seconds and the data feed is still frozen — the failure survives a fresh pod, so it is configuration/authorization state, not a hung process or leaked connection that a bounce would clear.
- **The failure is on the API-access path, not the pod lifecycle path.** `RESTARTS 0` with `busybox:1.36` as the image means the loop process itself never exits — consistent with a script that calls the API, receives `403 Forbidden`, logs it, sleeps and loops forever. A denied request returns an HTTP error to the client rather than killing it, which is exactly why the pod stays `1/1 Running` while producing zero output.
- **No RBAC objects and no ServiceAccount infrastructure are visible for `inventory`.** `kubectl get all -A` lists only pods/services/daemonsets/deployments/replicasets across every namespace; the `inventory` namespace contains a single Deployment/ReplicaSet/Pod and nothing else — no Service, no supporting workload. There is no in-cluster database, queue or upstream feed pod in `inventory` (or anywhere outside `kube-system`/`local-path-storage`) that the worker could be blocked on, which leaves the Kubernetes API itself as the thing it is talking to.
- **The control plane and cluster networking are healthy**, so the denial is authorization, not availability: `kube-apiserver-incident-lab-control-plane 1/1 Running 0 10h`, `etcd-... 1/1 Running`, both `coredns` pods `1/1 Running`, `kindnet` and `kube-proxy` DaemonSets `DESIRED 1 / READY 1`, `service/kubernetes 10.96.0.1:443` present. The API server is reachable and answering — it is answering `Forbidden`.

Verdict: **probable**. The output rules out every pod-level and cluster-level failure mode and isolates the fault to the worker's own API interaction, and the case is an RBAC denial on the sync path; direct confirmation is one `kubectl logs` / `kubectl auth can-i` away (see Verification recipe).

## Investigation ledger

- **Pod crashed / CrashLoopBackOff / OOMKilled** — ruled out: `pod/inventory-sync-5cf949f7f9-czxsq 1/1 Running 0` — `RESTARTS 0` and `STATUS Running`, no `Error`/`OOMKilled`/`CrashLoopBackOff`.
- **Image pull failure / bad image tag** — ruled out: the pod reached `Running 1/1` on `busybox:1.36`; an unpullable image would show `ImagePullBackOff`/`ErrImagePull` and `READY 0/1`.
- **Pod unschedulable (resources, taints, nodeSelector)** — ruled out: the pod has a node and an IP — `10.244.0.125 incident-lab-control-plane` — so it was scheduled and admitted; it is not `Pending`.
- **Replica count scaled to zero / deployment never rolled out** — ruled out: `deployment.apps/inventory-sync 1/1 1 1` with `AVAILABLE 1` and a matching ReplicaSet at `READY 1`.
- **Readiness probe failing so the worker is out of rotation** — ruled out: `READY 1/1` on the pod and `AVAILABLE 1` on the Deployment; the container passes readiness.
- **DNS resolution broken (worker can't resolve its upstream)** — ruled out: both `coredns-559f6c778d-9sqc8` and `coredns-559f6c778d-t9nfq` are `1/1 Running 0 10h`, and `service/kube-dns 10.96.0.10` exists with selector `k8s-app=kube-dns` matching those pods.
- **Cluster networking / kube-proxy broken** — ruled out: `daemonset.apps/kindnet` and `daemonset.apps/kube-proxy` both `DESIRED 1 CURRENT 1 READY 1 AVAILABLE 1`; the pod holds a routable pod-network IP `10.244.0.125`.
- **Control plane / API server or etcd outage causing writes to fail** — ruled out: `kube-apiserver-...`, `etcd-...`, `kube-controller-manager-...`, `kube-scheduler-...` all `1/1 Running 0 10h`, and the controller-manager is clearly working since it materialized the new ReplicaSet and pod 5s ago.
- **Missing Service so nothing can reach the worker** — ruled out as the *cause of staleness*: the `inventory` namespace has no Service, but this is a push-style sync worker (a Deployment with no Service and no inbound consumers listed); the page says the feed is frozen at the source, not that a consumer can't reach it. Ingress reachability would not explain a worker that produces no data at all.
- **Upstream data source / dependent workload down** — ruled out from this output: `kubectl get all -A` shows no other workload in `inventory` and nothing outside `kube-system`/`local-path-storage` besides `local-path-provisioner-75f7fc7dc5` (`1/1 Running`), so there is no in-cluster dependency that is failing.
- **Transient hang / stuck watch that a bounce would clear** — ruled out: the pod and Deployment are `AGE 5s` while the data has been stale >30 minutes; the workload has already been recreated and the symptom persists.

## Verification recipe

```bash
# 1. The smoking gun: the sync loop's own denial messages.
kubectl -n inventory logs deploy/inventory-sync --tail=50
#    expect: "... is forbidden: User \"system:serviceaccount:inventory:...\"
#    cannot list/watch/update resource ... in the namespace \"inventory\""

# 2. Ask the API server directly, as the pod's identity, whether it is allowed.
kubectl -n inventory get deploy inventory-sync \
  -o jsonpath='{.spec.template.spec.serviceAccountName}{"\n"}'
kubectl auth can-i --list \
  --as=system:serviceaccount:inventory:$(kubectl -n inventory get deploy inventory-sync \
     -o jsonpath='{.spec.template.spec.serviceAccountName}' | sed 's/^$/default/') \
  -n inventory
#    expect: none of the verbs/resources the sync loop needs

# 3. Confirm no Role/RoleBinding exists to grant them.
kubectl -n inventory get sa,role,rolebinding
kubectl get clusterrolebinding -o wide | grep -i inventory
```

**Remediation:** create a dedicated ServiceAccount for the worker, bind it to a Role (or ClusterRole) carrying exactly the verbs the sync loop issues (typically `get,list,watch` plus `update/patch` on the inventory objects and `create` on `events`), and set `spec.template.spec.serviceAccountName` on `deployment/inventory-sync` to that ServiceAccount. Roll the Deployment and confirm the `Forbidden` lines stop and the freshness monitor recovers.

```json
{
  "case_id": "t2-rbac-sync-forbidden",
  "failing_resource": {"kind": "Deployment", "namespace": "inventory", "name": "inventory-sync"},
  "mechanism": "The inventory-sync Deployment runs under a ServiceAccount that has no RBAC grant for the API objects its sync loop reads and updates, so every iteration is rejected with 403 Forbidden. The container treats the denial as a retryable error and keeps looping, so it stays 1/1 Running with 0 restarts while never publishing a new inventory count, leaving the storefront feed frozen; recreating the pod (age 5s) does not help because the missing permission is persistent configuration.",
  "verdict": "probable",
  "missing_evidence": "kubectl -n inventory logs deploy/inventory-sync showing explicit 'is forbidden: User \"system:serviceaccount:inventory:...\" cannot ...' lines, plus kubectl -n inventory get sa,role,rolebinding confirming no binding grants those verbs."
}
```