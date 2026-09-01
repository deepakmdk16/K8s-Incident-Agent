## Root cause

**Confirmed.** The `billing/billing-api` Deployment's init container `wait-for-db` is configured with `DB_HOST=db-primary`, but no Service by that name exists in the `billing` namespace — the database Service is `billing/postgres-primary`. The init container's TCP probe against `db-primary:5432` therefore never succeeds (the hostname does not resolve to any endpoint), so `wait-for-db` loops forever, the pods stay `Init:0/1`/`PodInitializing`, and the Deployment reports `0/2` Ready. Invoices queue because the new `api` container is never started.

## Evidence chain

1. **The pods are stuck in init, not in the app container.**
   - `kubectl get all -A`: `pod/billing-api-ccb44c44c-89dn7  0/1  Init:0/1` and `pod/billing-api-ccb44c44c-m756m  0/1  Init:0/1`.
   - `describe of pod billing-api-ccb44c44c-89dn7`: init container `wait-for-db` → `State: Running`, `Ready: False`; app container `api` → `State: Waiting`, `Reason: PodInitializing`; condition `Initialized  False`.

2. **The init container is blocking on a hostname.**
   - `describe of pod billing-api-ccb44c44c-89dn7`, init container env: `DB_HOST:  db-primary`.
   - `log line: "wait-for-db: waiting for db-primary:5432 before starting billing-api"`, followed by repeated `waiting for db-primary:5432` at ~5s intervals on both pods — a steady retry loop with no progress and no error exit.

3. **`db-primary` does not exist; the real Service is `postgres-primary`.**
   - `kubectl get all -A` Services section lists in namespace `billing` only: `service/postgres-primary  ClusterIP  10.96.16.233  5432/TCP  selector app=postgres-primary`. There is no `db-primary` Service in `billing` or any other namespace (only `default/kubernetes` and `kube-system/kube-dns` exist besides it).

4. **The database itself is healthy — the dependency is up, only the name is wrong.**
   - `kubectl get all -A`: `pod/postgres-primary-65b8877bbf-9z42x  1/1  Running`; `deployment.apps/postgres-primary  1/1  1  1`. This matches the page text "The database tier reports healthy."
   - The `postgres-primary` Service selector `app=postgres-primary` matches the running pod's Deployment labels, so the Service has endpoints — the target the init container *should* be probing is reachable.

5. **The misconfiguration lives in the workload spec, not in a one-off pod.**
   - `describe of deployment billing-api`: Pod Template → init container `wait-for-db` → `Environment: DB_HOST:  db-primary`.
   - `describe of replicaset billing-api-ccb44c44c`: same `DB_HOST: db-primary` in the template — so every replica the ReplicaSet creates inherits the bad value (`Pods Status: 0 Running / 2 Waiting`).

6. **Nothing else is impeding the rollout.**
   - `describe of deployment billing-api`: `Progressing  True  ReplicaSetUpdated`, `Available False MinimumReplicasUnavailable`, `NewReplicaSet: billing-api-ccb44c44c (2/2 replicas created)` — the controller did its job; the pods just never initialize.
   - Pod events show `Scheduled`, `Pulled`, `Created`, `Started` with no warnings.

*Timeline note:* the page says "over 20 minutes" but the captured objects are `20s` old and the ReplicaSet has `Created pod:` events at `21s`. The snapshot appears to have been taken shortly after a re-create/re-apply of the objects; this does not change the mechanism, which is a static spec error that reproduces on every pod the ReplicaSet creates.

## Investigation ledger

- **Database tier down / not ready** — ruled out. `pod/postgres-primary-65b8877bbf-9z42x  1/1  Running`, `deployment.apps/postgres-primary  1/1`, and `service/postgres-primary` exists on `5432/TCP` with selector `app=postgres-primary` matching that pod. The dependency is serving; only the name used to reach it is wrong.
- **Cluster DNS broken** — ruled out. Both `kube-system` `coredns` pods are `1/1 Running` (10h), `deployment.apps/coredns 2/2`, and `service/kube-dns` exists. A DNS outage would also break other workloads; nothing else is failing. Furthermore, resolution failure here is expected and correct behavior for a name that has no Service object.
- **Image pull failure / bad image tag** — ruled out. Pod events: `Container image "busybox:1.36" already present on machine and can be accessed by the pod`, then `Created` and `Started`. No `ErrImagePull`/`ImagePullBackOff`.
- **Scheduling problem (resources, taints, node selectors)** — ruled out. `PodScheduled True`, event `Successfully assigned billing/billing-api-... to incident-lab-control-plane`, `Node-Selectors: <none>`, and both pods have node IPs assigned.
- **Missing/failed ConfigMap mount (`billing-api-scripts`, `Optional: false`)** — ruled out. A missing ConfigMap would leave the pod stuck at `ContainerCreating` with a `FailedMount` warning; instead the init container `Started` and is actively emitting log lines from `/app/wait-for-db.sh`, proving the volume mounted and the script is executing.
- **Crash/exit loop in the init container (e.g. script bug, OOM)** — ruled out. `Restart Count: 0` on both pods and `State: Running`; logs show a continuous retry loop, not repeated startup banners. The script is behaving as designed — it is the target hostname that is wrong.
- **Readiness/liveness probe failure on the `api` container** — ruled out. The `api` container has never started (`Container ID:` empty, `Reason: PodInitializing`), and no probes are defined in the template.
- **NetworkPolicy blocking egress to the DB** — considered, not the operative cause. No NetworkPolicy objects appear in the cluster inventory, and a policy block would not explain a target name that has no Service at all. Even if one existed, the spec would still need the `DB_HOST` fix.
- **Stale ReplicaSet / stuck rollout of an old revision** — ruled out. `OldReplicaSets: <none>`, `deployment.kubernetes.io/revision: 1`, and the single ReplicaSet `billing-api-ccb44c44c` has `2 current / 2 desired`.

## Verification recipe

```bash
# 1. Confirm no Service named db-primary exists, while postgres-primary does (with endpoints).
kubectl -n billing get svc,endpoints
kubectl -n billing get svc db-primary            # expect: Error from server (NotFound)

# 2. Confirm the Deployment spec is what injects the wrong hostname.
kubectl -n billing get deploy billing-api \
  -o jsonpath='{.spec.template.spec.initContainers[0].env}{"\n"}'   # expect DB_HOST=db-primary

# 3. Prove the name is the blocker: resolution fails for db-primary, succeeds for postgres-primary.
kubectl -n billing run dnscheck --rm -it --restart=Never --image=busybox:1.36 -- \
  sh -c 'nslookup db-primary; echo "---"; nslookup postgres-primary; nc -zv postgres-primary 5432'
```

**Remediation:** point the init container at the real Service name — `kubectl -n billing set env deployment/billing-api --containers=wait-for-db DB_HOST=postgres-primary` (or, better, fix `DB_HOST` to `postgres-primary` / `postgres-primary.billing.svc.cluster.local` in the source manifest and re-apply so the change is not lost on the next deploy). The rollout will then complete as the init container's TCP check succeeds and the `api` container starts. Follow-ups: add a bounded timeout/`failureThreshold` to `wait-for-db` so a wrong hostname fails loudly instead of hanging silently, and add a `progressDeadlineSeconds`-based alert so a stalled rollout pages on the init phase rather than 20 minutes later.

```json
{
  "case_id": "t2-init-wait-for-migrations",
  "failing_resource": {"kind": "Deployment", "namespace": "billing", "name": "billing-api"},
  "mechanism": "The billing-api Deployment's pod template sets DB_HOST=db-primary for its wait-for-db init container, but no Service by that name exists in the billing namespace (the database Service is postgres-primary). The init container's TCP check against db-primary:5432 can never succeed, so it loops indefinitely, every replica stays in Init:0/1 with the api container in PodInitializing, and the Deployment reports 0/2 Ready.",
  "verdict": "confirmed"
}
```