## Root cause

**Confirmed.** The `billing-api` Deployment's init container `wait-for-db` is pointed at a hostname that does not exist. Its spec sets `DB_HOST: db-primary`, but the only database Service in the `billing` namespace is named `postgres-primary` (ClusterIP `10.96.16.233:5432`). There is no Service, Endpoint, or DNS record for `db-primary` anywhere in the cluster, so the init container's TCP probe to `db-primary:5432` never succeeds. The init container loops forever, `Initialized` stays `False`, the `api` container is never started, and the Deployment sits at `0/2 Ready` — which is exactly the paged symptom (release stalled, invoices queueing).

The failing resource is the Deployment (its pod template env must change), not the pods it produced.

## Evidence chain

- **The workload is stuck in init, not in the app container.** `kubectl get all -A`: `pod/billing-api-ccb44c44c-89dn7  0/1  Init:0/1` and `pod/billing-api-ccb44c44c-m756m  0/1  Init:0/1`. Both replicas, same state.
- **The blocked step is `wait-for-db`.** Describe of pod `billing-api-ccb44c44c-89dn7`: init container `wait-for-db` → `State: Running`, `Ready: False`; app container `api` → `State: Waiting`, `Reason: PodInitializing`. Pod conditions: `Initialized  False`, `Ready  False`.
- **The target hostname is `db-primary`.** Describe of pod `.../89dn7`, init container env: `DB_HOST:  db-primary`. Identical in the describe of `.../m756m` and, decisively for the fix location, in `describe deployment.apps/billing-api -n billing` → Pod Template → Init Containers → `wait-for-db` → `Environment: DB_HOST: db-primary`.
- **That hostname resolves to nothing.** `kubectl get all -A` Services section lists only three Services cluster-wide: `billing/postgres-primary` (ClusterIP 10.96.16.233, `5432/TCP`, selector `app=postgres-primary`), `default/kubernetes`, and `kube-system/kube-dns`. There is **no** Service named `db-primary` in `billing` or any other namespace.
- **The probe is failing on connect, forever.** Log line from `.../89dn7 -c wait-for-db`: `wait-for-db: waiting for db-primary:5432 before starting billing-api`, then `waiting for db-primary:5432` repeated at `02:12:44`, `02:12:49`, `02:12:54`, `02:12:59`, `02:13:04` — a clean 5-second retry loop with zero progress messages. Same loop verbatim in `.../m756m` logs.
- **The database itself is fine, so the dependency is real but misnamed.** `pod/postgres-primary-65b8877bbf-9z42x  1/1  Running`, `deployment.apps/postgres-primary  1/1  1  1`, and its Service selector `app=postgres-primary` matches that pod's Deployment labels. This matches the page text "The database tier reports healthy."
- **This blocks the Deployment rollout, producing the alert.** `describe deployment.apps/billing-api`: `Replicas: 2 desired | 2 updated | 2 total | 0 available | 2 unavailable`, `Available  False  MinimumReplicasUnavailable`, `Progressing  True  ReplicaSetUpdated`.
- **The correct name is one character-class away from the configured one** — `postgres-primary` vs `db-primary`, both ending in `-primary` — consistent with a hand-edited/templated env value in the release manifest.

## Investigation ledger

- **Image pull failure / bad image** — ruled out. Describe events for both pods: `Pulled ... Container image "busybox:1.36" already present on machine and can be accessed by the pod`, followed by `Created` and `Started`. No `ErrImagePull`/`ImagePullBackOff` anywhere.
- **Scheduling / capacity / node pressure** — ruled out. Both pods show `PodScheduled True` and event `Successfully assigned billing/billing-api-... to incident-lab-control-plane`. No `FailedScheduling`, no `Pending`-unscheduled, no taints beyond the default not-ready/unreachable tolerations.
- **Missing or unmounted ConfigMap `billing-api-scripts`** — ruled out. The volume is `Optional: false`, so a missing ConfigMap would leave the pod at `ContainerCreating` with a `FailedMount` event; instead `PodReadyToStartContainers True`, the init container reached `Running` with a real `Container ID`, and it is emitting log output from `/app/wait-for-db.sh`. The script mounted and executed fine.
- **The database is actually down / not accepting connections** — ruled out. `postgres-primary` pod is `1/1 Running`, its Deployment is `1/1` available, and the Service `billing/postgres-primary` exposes `5432/TCP` with a selector matching the running pod. The page itself states the database tier reports healthy. The connection failure is to a *different*, nonexistent name.
- **Cluster DNS broken** — ruled out. Both `coredns` pods are `1/1 Running` (10h uptime), `deployment.apps/coredns` is `2/2`, and `service/kube-dns` exists. DNS resolution of a name that has no record is expected to fail regardless; nothing suggests CoreDNS itself is degraded.
- **Network plugin / kube-proxy failure** — ruled out. `kindnet` and `kube-proxy` DaemonSets are `1/1` desired/ready, all control-plane components `1/1 Running` for 10h, and pods received IPs (`10.244.0.127`, `10.244.0.128`).
- **Wrong-namespace lookup (Service exists elsewhere, needs FQDN)** — ruled out as the mechanism. A cross-namespace miss would still require a Service named `db-primary` to exist somewhere; the cluster-wide `kubectl get all -A` Services listing contains no such Service in any namespace. The name is wrong, not merely unqualified.
- **App container crashlooping / failing readiness probe** — ruled out. The `api` container has an empty `Container ID`, `Restart Count: 0`, and `Reason: PodInitializing`. It has never started, so no app-level bug can be responsible.
- **Stale ReplicaSet / bad rollout wedged behind an old revision** — ruled out. `describe deployment` shows `OldReplicaSets: <none>`, `NewReplicaSet: billing-api-ccb44c44c (2/2 replicas created)`, `revision: 1`. The ReplicaSet created both pods successfully (`SuccessfulCreate` ×2); the block is entirely inside the pods.
- **Timeline discrepancy (page says >20 min, objects show `AGE 20s`)** — noted, not a competing cause. This is consistent with the snapshot being taken shortly after a redeploy/restart of an already-stalled release, or with clock/collection skew between the watchdog and this capture. It does not change the mechanism: the same `DB_HOST: db-primary` in the Deployment template will reproduce the stall on every recreate, which is precisely why it has persisted for 20+ minutes across restarts.

## Verification recipe

```bash
# 1. Prove no 'db-primary' Service exists, but 'postgres-primary' does.
kubectl get svc -A | grep -E 'db-primary|postgres-primary'
#   expect: only billing/postgres-primary ... 5432/TCP  (no db-primary row)

# 2. Prove the resolution failure from inside the pod's own network namespace.
kubectl run dns-check --rm -it --restart=Never -n billing --image=busybox:1.36 -- \
  sh -c 'nslookup db-primary; echo "---"; nslookup postgres-primary; echo "---"; nc -zvw2 postgres-primary 5432'
#   expect: db-primary -> NXDOMAIN / server can't find;
#           postgres-primary -> 10.96.16.233; nc -> open

# 3. Prove the bad value lives in the Deployment spec (the thing to change).
kubectl get deploy billing-api -n billing \
  -o jsonpath='{.spec.template.spec.initContainers[0].env}{"\n"}'
#   expect: [{"name":"DB_HOST","value":"db-primary"}]
```

**Remediation:** patch the Deployment's init-container env to the real Service name, then let the rollout finish:

```bash
kubectl set env deployment/billing-api -n billing \
  --containers='wait-for-db' DB_HOST=postgres-primary.billing.svc.cluster.local
kubectl rollout status deployment/billing-api -n billing --timeout=120s
```

Follow-up: fix `DB_HOST` in the source manifest/Helm values so the next release does not reintroduce it, and add a bounded timeout (or `startupProbe` + explicit failure) to `wait-for-db.sh` so an unresolvable dependency fails loudly in minutes instead of hanging as an indefinite `Init:0/1`.

```json
{
  "case_id": "t2-init-wait-for-migrations",
  "failing_resource": {"kind": "Deployment", "namespace": "billing", "name": "billing-api"},
  "mechanism": "The billing-api Deployment's pod template sets DB_HOST=db-primary for its wait-for-db init container, but no Service by that name exists in the cluster; the only database Service is billing/postgres-primary on 5432/TCP. The init container's TCP probe to db-primary:5432 therefore never resolves and loops every 5 seconds forever, so Initialized stays False, the api container never starts, and the Deployment remains 0/2 Ready with Available=False/MinimumReplicasUnavailable.",
  "verdict": "confirmed"
}
```