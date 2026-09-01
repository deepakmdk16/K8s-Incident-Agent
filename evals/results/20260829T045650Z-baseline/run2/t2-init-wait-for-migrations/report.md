# SEV2 BillingReleaseStalled — billing/billing-api

**Verdict: confirmed**

## Root cause

The `billing-api` Deployment's pod template points its `wait-for-db` init container at a hostname that does not exist. The init container is configured with `DB_HOST: db-primary`, but the only database Service in the namespace is `billing/postgres-primary` (ClusterIP 10.96.16.233:5432, selector `app=postgres-primary`). There is no Service named `db-primary` in any namespace, so `db-primary:5432` never resolves/connects, the init container loops forever, `Initialized` stays `False`, the `api` container never starts, and the Deployment sits at 0/2 Ready — which is exactly the paged symptom (release stalled, invoices queuing). The database itself is genuinely healthy, matching the "database tier reports healthy" note in the page.

## Evidence chain

- **Symptom, `kubectl get all -A`:** `deployment.apps/billing-api  0/2  2  0` and both pods `pod/billing-api-ccb44c44c-89dn7` / `-m756m` in `0/1 Init:0/1`. The pods are stuck in the *init* phase, not crash-looping.
- **Blocked at the init container, describe of pod `billing-api-ccb44c44c-89dn7`:**
  - `wait-for-db: State: Running`, `Ready: False`, `Restart Count: 0`
  - `api: State: Waiting  Reason: PodInitializing`
  - Conditions: `Initialized  False`, `Ready  False`, `PodScheduled  True`
  Identical picture in describe of pod `billing-api-ccb44c44c-m756m`.
- **The hostname it is waiting on, describe of pod `.../89dn7` (and the deployment/replicaset pod template):** init container env `DB_HOST:  db-primary`.
- **It is waiting, not failing, log line (pod `.../89dn7`, container `wait-for-db`):**
  `wait-for-db: waiting for db-primary:5432 before starting billing-api`, then `waiting for db-primary:5432` repeating every ~5s at `02:12:44`, `02:12:49`, `02:12:54`, `02:12:59`, `02:13:04`. Same repeating loop in pod `.../m756m`'s log. No error, no exit — an infinite retry loop.
- **The target does not exist, Services section of `kubectl get all -A`:** the only Services present are `billing/postgres-primary` (`ClusterIP 10.96.16.233  5432/TCP  selector app=postgres-primary`), `default/kubernetes`, and `kube-system/kube-dns`. There is no `db-primary` Service anywhere, so `db-primary` resolves to nothing from the `billing` namespace.
- **The real DB is up, `kubectl get all -A`:** `pod/postgres-primary-65b8877bbf-9z42x  1/1  Running  0 restarts`, `deployment.apps/postgres-primary  1/1  1  1`. So the dependency is available under a *different name* — the wait is unsatisfiable purely because of the name.
- **Ownership of the bad value, describe of deployment `billing-api`:** the pod template itself carries `Environment: DB_HOST: db-primary`, and describe of replicaset `billing-api-ccb44c44c` shows the same. The defect is in the Deployment spec, not in one stray pod.
- **Nothing else is stalling the rollout, describe of deployment `billing-api`:** `Progressing True ReplicaSetUpdated`, `Available False MinimumReplicasUnavailable`, `NewReplicaSet: billing-api-ccb44c44c (2/2 replicas created)`, `OldReplicaSets: <none>` — pods were created fine; they just never initialize.

## Investigation ledger

- **Database tier down / not accepting connections** — ruled out: `postgres-primary-65b8877bbf-9z42x` is `1/1 Running` with `0` restarts and `deployment.apps/postgres-primary` is `1/1` available; Service `billing/postgres-primary` exists on `5432/TCP` with a matching selector (`app=postgres-primary`) and the pod carries label `app=postgres-primary`. Consistent with the page's "database tier reports healthy."
- **Image pull failure / bad image** — ruled out: describe of both pods shows `Normal Pulled ... Container image "busybox:1.36" already present on machine`, then `Created` and `Started`. No `ErrImagePull`/`ImagePullBackOff` anywhere.
- **Scheduling pressure, taints, or node capacity** — ruled out: `PodScheduled True` and `Normal Scheduled ... Successfully assigned billing/billing-api-ccb44c44c-{89dn7,m756m} to incident-lab-control-plane` in both describes. Both pods have IPs (`10.244.0.127`, `10.244.0.128`) and no `NodeSelectors`.
- **Missing ConfigMap `billing-api-scripts` (`Optional: false`)** — ruled out: if it were missing the pods would be stuck at `CreateContainerConfigError` with a `FailedMount` event; instead the init container reached `State: Running` and is emitting log output from `/app/wait-for-db.sh`, proving the volume mounted and the script is executing.
- **App crash / bad command in the `api` container** — ruled out: `api` has empty `Container ID` and `Reason: PodInitializing`; it has never been started, so it cannot be the cause.
- **Cluster DNS broken (CoreDNS outage) rather than a wrong name** — ruled out as the root cause: both `coredns-559f6c778d-9sqc8` and `-t9nfq` are `1/1 Running` with `0` restarts, `kube-dns` Service is present, and `kindnet`/`kube-proxy` DaemonSets are `1/1` ready. A DNS outage would also be a cluster-wide symptom, yet only this workload is affected — and even with perfect DNS, `db-primary` has no Service to resolve to.
- **Failed rollout of a previous revision / stuck old ReplicaSet** — ruled out: describe of deployment shows `OldReplicaSets: <none>` and `deployment.kubernetes.io/revision: 1`; there is nothing to roll back to. The stall is in the new (and only) revision's init gate.
- **Init container `wait-for-db.sh` itself is broken (e.g. exits nonzero)** — ruled out: `Restart Count: 0` and the log advances on a clean 5-second cadence with a coherent message; the script is working exactly as written, against the wrong target.
- **Note on timing:** the page says "over 20 minutes" while all `billing` resources show `AGE 20s` and the events are `21s` old. This snapshot was taken shortly after a re-create/re-apply of the namespace; it does not change the mechanism, since the wait loop is unsatisfiable and would repeat indefinitely on every attempt.

## Verification recipe

```bash
# 1. Confirm the Deployment tells the init container to wait on a name that has no Service.
kubectl -n billing get deploy billing-api \
  -o jsonpath='{.spec.template.spec.initContainers[0].env}{"\n"}'
kubectl -n billing get svc,endpoints

# 2. Confirm the name does not resolve, while the real Service does.
kubectl -n billing run dnscheck --rm -it --restart=Never --image=busybox:1.36 -- \
  sh -c 'nslookup db-primary; echo "---"; nslookup postgres-primary; \
         nc -zvw2 postgres-primary 5432'

# 3. Confirm the pods are still parked in the same wait loop (should keep ticking).
kubectl -n billing logs -l app=billing-api -c wait-for-db --tail=5
```

Expected: step 1 prints `"name":"DB_HOST","value":"db-primary"` while step 1's service list contains only `postgres-primary`; step 2 shows NXDOMAIN for `db-primary`, a successful lookup plus open `5432` for `postgres-primary`; step 3 keeps emitting `waiting for db-primary:5432`.

**Remediation:** patch the Deployment's init container env to the real Service name, e.g.
`kubectl -n billing set env deployment/billing-api --containers='wait-for-db' DB_HOST=postgres-primary`
(or `postgres-primary.billing.svc.cluster.local`). The rollout should complete on its own once the init probe can connect. Follow-ups: pin the `DB_HOST` value to the Service name in the source manifest/chart so the two cannot drift again, and give `wait-for-db` a bounded retry budget so an unresolvable dependency surfaces as a failing pod with a clear error instead of an indefinite silent stall.

```json
{
  "case_id": "t2-init-wait-for-migrations",
  "failing_resource": {"kind": "Deployment", "namespace": "billing", "name": "billing-api"},
  "mechanism": "The billing-api Deployment's pod template sets DB_HOST=db-primary for its wait-for-db init container, but no Service by that name exists in the cluster; the actual database Service in the namespace is postgres-primary:5432. The init container therefore loops forever on an unresolvable host, the pods never reach Initialized, the api container never starts, and the Deployment stays at 0/2 Ready so the release never goes live.",
  "verdict": "confirmed"
}
```