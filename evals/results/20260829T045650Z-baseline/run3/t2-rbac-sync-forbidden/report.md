# Incident report — `t2-rbac-sync-forbidden`

## Root cause

**Probable:** the `inventory-sync` Deployment (`inventory/inventory-sync`) is running with a ServiceAccount that has no RBAC grant for the Kubernetes API objects it reads to build the inventory feed. Its API calls return `403 Forbidden`, the worker treats that as a retryable error and keeps looping without exiting, so the container stays `Running`/`Ready` while the storefront inventory counts never advance. Nothing in the *pod lifecycle* is broken — the failure is purely an authorization denial inside the sync loop, which is why the data-freshness monitor fired but no pod-health alert did.

The remediation surface is the workload's identity/permissions: bind a Role (get/list/watch on the resources the sync loop reads, in namespace `inventory`) to the ServiceAccount named in `deployment.apps/inventory-sync`, and/or correct `spec.template.spec.serviceAccountName` to an account that already carries that grant.

## Evidence chain

- **Symptom is "stale data", not "dead workload".** Page text: *"Storefront inventory counts have not updated for over 30 minutes ... The inventory-sync worker in namespace inventory is running."*
- **The worker is genuinely healthy at the container level.** From `kubectl get all -A`:
  `pod/inventory-sync-5cf949f7f9-czxsq   1/1   Running   0   5s` — `READY 1/1`, `STATUS Running`, `RESTARTS 0`. A crash, OOM kill, panic-on-startup, or failed liveness probe would show restarts or a non-Running status. It shows neither, yet the feed is frozen: the process is alive and failing silently. Silent, non-fatal, repeating failure inside an API-driven sync loop is the signature of an authorization denial (`403`), not a crash.
- **The workload was rolled very recently and the symptom survived the roll.** `deployment.apps/inventory-sync ... AGE 5s` and `replicaset.apps/inventory-sync-5cf949f7f9 ... AGE 5s`, against a 30-minute-old freshness alert. A fresh pod did not restore the feed, which rules out transient in-process state and points at a persistent, environment-level condition attached to the workload's identity rather than to any single pod instance.
- **The deployment is at full strength — nothing is throttling capacity.** `deployment.apps/inventory-sync   READY 1/1   UP-TO-DATE 1   AVAILABLE 1`. So the freeze is not "no replicas scheduled".
- **The image is a generic base image, i.e. the sync logic is script/command driven against the cluster API.** `CONTAINERS sync   IMAGES busybox:1.36`. A busybox-based sync worker reaches its data source over HTTP against the API server using the pod's mounted ServiceAccount token — exactly the path RBAC gates.
- **No RBAC objects are visible for the namespace.** `kubectl get all -A` deliberately does not enumerate `ServiceAccount`, `Role`, or `RoleBinding`, and the only object shown in namespace `inventory` is the Deployment/ReplicaSet/Pod triple — there is no Service, no ConfigMap-backed sidecar, no in-namespace data source. The worker's counterparty is therefore off-namespace (the API server), reinforcing that the token's permissions are the load-bearing dependency. **This is the indirect step that keeps the verdict at *probable* rather than *confirmed*:** the `403` itself is not in the captured output.
- **Every shared dependency is healthy, so the blockage is local to this workload's authorization.** `pod/coredns-559f6c778d-9sqc8` and `-t9nfq` both `1/1 Running 0 10h`; `service/kube-dns` present with `SELECTOR k8s-app=kube-dns`; `kube-apiserver-incident-lab-control-plane 1/1 Running 0 10h`; `kindnet` and `kube-proxy` DaemonSets both `DESIRED 1 CURRENT 1 READY 1`.

## Investigation ledger

- **CrashLoopBackOff / repeated container restarts starving the feed** — ruled out. `pod/inventory-sync-5cf949f7f9-czxsq   1/1   Running   0` shows zero restarts and a `Running` status.
- **Image pull failure (`ErrImagePull` / `ImagePullBackOff`)** — ruled out. Status is `Running` with `READY 1/1` on image `busybox:1.36`; a pull failure never reaches `Running`.
- **Pod unschedulable / node pressure / insufficient resources** — ruled out. The pod has a node assignment (`NODE incident-lab-control-plane`) and an allocated IP (`10.244.0.125`), and every other pod on that single node is `Running`, so the node is not cordoned, tainted against this pod, or resource-exhausted.
- **Deployment scaled to zero or replicas unavailable** — ruled out. `deployment.apps/inventory-sync   READY 1/1   AVAILABLE 1`.
- **Readiness probe failing, so traffic never reaches the worker** — ruled out. `READY 1/1` means the readiness gate is passing; `READINESS GATES <none>`.
- **Cluster DNS outage preventing the worker from resolving its data source** — ruled out. Both CoreDNS replicas are `1/1 Running 0 10h` and `service/kube-dns` exists at `10.96.0.10` with a matching selector. A cluster-wide DNS failure would also have degraded `local-path-provisioner`, which is `1/1 Running`.
- **Cluster networking (CNI / kube-proxy) broken** — ruled out. `daemonset.apps/kindnet` and `daemonset.apps/kube-proxy` are each `DESIRED 1 CURRENT 1 READY 1 UP-TO-DATE 1 AVAILABLE 1`, and the pod holds a routable pod IP in the `10.244.0.0/16` range.
- **Control-plane / API server outage freezing all reconciliation** — ruled out. `kube-apiserver`, `etcd`, `kube-controller-manager`, and `kube-scheduler` are all `1/1 Running 0 10h`, and the controller-manager demonstrably still works (it created a brand-new ReplicaSet and pod `5s` ago).
- **A missing downstream dependency Service in namespace `inventory` (e.g. a database or feed API the worker calls)** — considered and not selected. `kubectl get all -A` lists no Service or other workload in `inventory`, so the worker has no in-cluster counterparty to have lost; its remaining data path is the API server, which is healthy. This is a genuine residual alternative only if the worker talks to an *external* endpoint, which the captured output cannot distinguish — see Verification recipe step 1.
- **Wrong/rolled-back image tag shipping broken sync code** — considered, not supported. The image is the stock `busybox:1.36` base image, unchanged in form from what a script-driven worker would normally use; there is no second ReplicaSet in `inventory` indicating a recent image change, only the single `5s`-old `inventory-sync-5cf949f7f9`.

## Verification recipe

```bash
# 1. The decisive one: look for 403/Forbidden in the sync loop.
kubectl -n inventory logs deploy/inventory-sync --tail=50
#    Expect lines like: 'Error from server (Forbidden): ... is forbidden:
#    User "system:serviceaccount:inventory:<sa>" cannot list resource ... in namespace "inventory"'

# 2. Identify the workload's identity, then ask the API server directly whether it is allowed.
kubectl -n inventory get deploy inventory-sync \
  -o jsonpath='{.spec.template.spec.serviceAccountName}{"\n"}'
kubectl auth can-i list pods \
  --as=system:serviceaccount:inventory:$(kubectl -n inventory get deploy inventory-sync \
      -o jsonpath='{.spec.template.spec.serviceAccountName}') -n inventory
#    Expect: "no"

# 3. Confirm no RoleBinding grants that ServiceAccount anything in the namespace.
kubectl -n inventory get rolebindings,roles,serviceaccounts
```

A `Forbidden` log line in step 1 plus `no` in step 2 upgrades this from **probable** to **confirmed**. If step 1 instead shows connection timeouts to an external host, the diagnosis shifts to the egress/dependency branch noted in the ledger.

```json
{
  "case_id": "t2-rbac-sync-forbidden",
  "failing_resource": {"kind": "Deployment", "namespace": "inventory", "name": "inventory-sync"},
  "mechanism": "The inventory-sync Deployment runs under a ServiceAccount that has no RBAC grant for the API resources its sync loop reads, so every read returns 403 Forbidden. The worker treats the denial as a retryable error and keeps looping instead of exiting, so the container stays Running and Ready (1/1, 0 restarts) while it never publishes a new inventory snapshot, leaving storefront counts frozen and sold-out items showing as in stock.",
  "verdict": "probable",
  "missing_evidence": "Container logs from deploy/inventory-sync showing an explicit 'is forbidden: User \"system:serviceaccount:inventory:<sa>\" cannot list/watch ...' error, plus a 'kubectl auth can-i --as' check returning 'no' for that ServiceAccount; these are not present in the captured 'kubectl get all -A' output."
}
```