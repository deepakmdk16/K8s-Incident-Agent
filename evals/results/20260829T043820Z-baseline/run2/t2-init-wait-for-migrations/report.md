## Root cause

**Verdict: confirmed.**

The `billing-api` Deployment's init container `wait-for-db` is configured with `DB_HOST=db-primary`, but no Service (or any other endpoint) named `db-primary` exists in the `billing` namespace — the database Service is actually named `postgres-primary`. The init container's TCP probe against `db-primary:5432` therefore never resolves/connects, `wait-for-db` loops forever, the `api` container is never started (`PodInitializing`), and the Deployment stays at `0/2` Ready, so the release never goes live and invoices queue. The database itself is fine; only the client-side hostname in the Deployment spec is wrong.

## Evidence chain

- **Symptom, from `kubectl get all -A`:** `deployment.apps/billing-api  0/2  2  0  20s` and both pods `0/1  Init:0/1` — the pods are stuck in the *init* phase, not in the app container.
- **The wait target, from `describe deployment.apps/billing-api`** (pod template, i.e. the spec that must change):
  ```
  Init Containers:
   wait-for-db:
    Command: sh /app/wait-for-db.sh
    Environment:
      DB_HOST:  db-primary
  ```
- **The actual database Service name, from `kubectl get all -A` (Services section):**
  ```
  billing  service/postgres-primary  ClusterIP  10.96.16.233  <none>  5432/TCP  20s  app=postgres-primary
  ```
  The only Services in the whole cluster are `billing/postgres-primary`, `default/kubernetes`, and `kube-system/kube-dns`. **There is no `db-primary` Service anywhere.** Its port (5432) and selector show `postgres-primary` is the intended target of the wait.
- **The mechanism firing, from `kubectl logs ... -c wait-for-db` on both pods:**
  ```
  wait-for-db: waiting for db-primary:5432 before starting billing-api
  waiting for db-primary:5432   (repeating every ~5s, both replicas)
  ```
  The name in the log matches the env var, confirming the script dials `$DB_HOST` and never succeeds.
- **The blocked handoff, from `describe pod/billing-api-ccb44c44c-89dn7` (identical for `-m756m`):**
  ```
  wait-for-db:  State: Running   Ready: False
  api:          State: Waiting   Reason: PodInitializing
  Conditions:   Initialized  False ; Ready  False
  ```
  Kubernetes will not start `api` until `wait-for-db` exits 0 — that is precisely why Ready is 0/2.
- **Database tier is healthy (rules out a real DB outage), from `kubectl get all -A`:** `pod/postgres-primary-65b8877bbf-9z42x  1/1  Running` and `deployment.apps/postgres-primary  1/1  1  1`. This matches the page text "database tier reports healthy".
- **Deployment-level consequence, from `describe deployment.apps/billing-api`:**
  ```
  Available    False  MinimumReplicasUnavailable
  Progressing  True   ReplicaSetUpdated
  ```

## Investigation ledger

- **Database is down / not accepting connections.** Ruled out: `pod/postgres-primary-65b8877bbf-9z42x` is `1/1 Running` with 0 restarts, its Deployment reports `1/1` available, and `service/postgres-primary` exists on 5432 with a selector (`app=postgres-primary`) matching that pod's Deployment labels.
- **Postgres Service has no endpoints (selector/label mismatch).** Ruled out as the cause of *this* page: even if true, the init container is not querying `postgres-primary` at all — it is querying `db-primary`, per the log line `waiting for db-primary:5432`. The selector `app=postgres-primary` also matches the `postgres-primary` Deployment's selector shown in the ReplicaSet/Deployment listing.
- **Cluster DNS broken.** Ruled out: both `coredns` pods are `1/1 Running` (10h, 0 restarts), `service/kube-dns` exists, and every other workload in the cluster is healthy. A DNS outage would not selectively break one hostname.
- **Image pull failure / bad image tag.** Ruled out: `Normal Pulled ... Container image "busybox:1.36" already present on machine`, plus `Created` and `Started` events for the init container — the image is fine and the container is running.
- **Scheduling / capacity / node pressure.** Ruled out: `PodScheduled True`, `Normal Scheduled ... Successfully assigned ... to incident-lab-control-plane`, and both pods have IPs (`10.244.0.127/.128`). No `FailedScheduling`, no taint/nodeSelector constraints in the pod template.
- **Missing ConfigMap `billing-api-scripts` (volume `Optional: false`).** Ruled out: a missing ConfigMap would produce `FailedMount`/`CreateContainerConfigError` events and the init container would never start; instead it started and is emitting its own log lines from `/app/wait-for-db.sh`.
- **NetworkPolicy blocking egress to the DB.** Ruled out as primary cause: no NetworkPolicy objects appear anywhere, and a blocked-but-resolvable host would still not explain a hostname that has no Service backing it. (Also `kindnet` and `kube-proxy` are healthy.)
- **App container crashlooping / failing readiness probe.** Ruled out: the `api` container has an empty `Container ID`, `State: Waiting / Reason: PodInitializing`, `Restart Count: 0` — it has never run. No readiness probe is defined in the pod template.
- **Old ReplicaSet stuck / rollout wedged by surge settings.** Ruled out: `OldReplicaSets: <none>`, `NewReplicaSet: billing-api-ccb44c44c (2/2 replicas created)`, and `SuccessfulCreate` events for both pods — the controller did its job; the pods themselves cannot initialize.

Note on the timeline: the page says "over 20 minutes" while all `billing` objects show `AGE 20s` and the init logs span ~20s. This is consistent with the snapshot being taken shortly after a re-apply/restart of the namespace; the failure mode is deterministic and identical on both replicas, so it does not change the diagnosis.

## Verification recipe

```bash
# 1. Confirm the hostname the init container waits on does not exist as a Service,
#    while the real DB Service is named postgres-primary.
kubectl get svc -n billing
kubectl get endpoints -n billing

# 2. Confirm the wrong hostname comes from the Deployment spec (the thing to change).
kubectl get deploy billing-api -n billing \
  -o jsonpath='{.spec.template.spec.initContainers[0].env}{"\n"}'
# expect: [{"name":"DB_HOST","value":"db-primary"}]

# 3. Prove the DB is reachable under its real name (and unreachable under db-primary).
kubectl run dbcheck --rm -it --restart=Never -n billing --image=busybox:1.36 -- \
  sh -c 'nc -z -w2 postgres-primary 5432 && echo POSTGRES-PRIMARY-OK; \
         nc -z -w2 db-primary 5432 || echo DB-PRIMARY-UNRESOLVABLE'
```

**Remediation:** point the init container at the real Service —
`kubectl set env deploy/billing-api -n billing --containers='wait-for-db' DB_HOST=postgres-primary`
(then fix it in the source manifest/Helm values so it survives the next release). Alternatively, if `db-primary` is the contractual name other clients depend on, create an alias Service `db-primary` in `billing` with selector `app=postgres-primary` on port 5432 (or an `ExternalName` to `postgres-primary.billing.svc.cluster.local`). Longer term, give the wait script a bounded timeout so it fails loudly instead of hanging a release.

```json
{
  "case_id": "t2-init-wait-for-migrations",
  "failing_resource": {"kind": "Deployment", "namespace": "billing", "name": "billing-api"},
  "mechanism": "The billing-api Deployment's pod template sets DB_HOST=db-primary for its wait-for-db init container, but no Service by that name exists in the billing namespace (the database Service is postgres-primary:5432). The init container's connect loop to db-primary:5432 never succeeds, so it never exits, the api container stays in PodInitializing, and the Deployment remains 0/2 Ready with Available=False/MinimumReplicasUnavailable.",
  "verdict": "confirmed"
}
```