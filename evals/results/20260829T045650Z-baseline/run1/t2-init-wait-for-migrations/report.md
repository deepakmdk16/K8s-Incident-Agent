## Root cause

**Confirmed.** The failing resource is `Deployment billing/billing-api`. Its pod template hard-codes `DB_HOST: db-primary`, but the only database Service in the `billing` namespace is `postgres-primary` (ClusterIP 10.96.16.233:5432). There is no Service, and therefore no DNS record, named `db-primary` anywhere in the cluster. The `wait-for-db` init container blocks forever trying to reach `db-primary:5432`, so `Initialized` never becomes true, the `api` container never starts, and the Deployment stays at 0/2 Ready — which is exactly the paged symptom. The database itself is fine; the release is gated on a hostname that does not resolve.

## Evidence chain

1. **The gate is the init container, not the app.** `describe pod/billing-api-ccb44c44c-89dn7`: init container `wait-for-db` is `State: Running / Ready: False`, while the `api` container is `State: Waiting, Reason: PodInitializing`. Pod conditions: `Initialized False`, `Ready False`, `PodScheduled True`. Identical picture in `describe pod/billing-api-ccb44c44c-m756m`. So both replicas are stuck *before* the application ever runs.

2. **What the init container is waiting on.** `describe pod/...-89dn7`, init container env: `DB_HOST: db-primary`. Log line from that pod: `wait-for-db: waiting for db-primary:5432 before starting billing-api`, followed by repeated `waiting for db-primary:5432` every ~5s (02:12:44 → 02:13:04). Same lines in the `m756m` pod's log.

3. **`db-primary` does not exist.** The Services section of `kubectl get all -A` lists exactly three Services cluster-wide: `billing/postgres-primary` (ClusterIP 10.96.16.233, `5432/TCP`, selector `app=postgres-primary`), `default/kubernetes`, and `kube-system/kube-dns`. There is no `db-primary` Service in `billing` or any other namespace, so `db-primary` resolves to nothing and the TCP dial can never succeed.

4. **The actual database is up and serving on the expected port.** `pod/postgres-primary-65b8877bbf-9z42x` is `1/1 Running`; `deployment.apps/postgres-primary` is `1/1 … AVAILABLE 1`; its Service exposes `5432/TCP` with a matching selector. This corroborates the page's "database tier reports healthy" — the dependency is healthy, only the *name* used to reach it is wrong.

5. **The bad value lives in the Deployment spec, not in a pod-level override.** `describe deployment.apps/billing-api` Pod Template shows the same `Environment: DB_HOST: db-primary` under init container `wait-for-db`; `describe replicaset.apps/billing-api-ccb44c44c` repeats it. So the fix must change the Deployment's pod template — mutating a pod would be undone by the ReplicaSet.

6. **Rollout is stalled, not failed or degraded.** `describe deployment.apps/billing-api`: `Available False MinimumReplicasUnavailable`, `Progressing True ReplicaSetUpdated`, `0 available | 2 unavailable`, `NewReplicaSet: billing-api-ccb44c44c (2/2 replicas created)`, `OldReplicaSets: <none>`. `describe replicaset`: `0 Running / 2 Waiting / 0 Succeeded / 0 Failed`. Nothing is crashing; everything is blocked waiting.

Note on timeline: the page says >20 minutes, while the captured objects show `AGE 20s` and `Restart Count: 0`. The capture is from a fresh recreation of the same manifests; the stall is deterministic (the hostname is wrong in the spec), so it reproduces identically on every rollout — the wall-clock delta does not change the diagnosis.

## Investigation ledger

- **Database is down / not ready** — ruled out. `pod/postgres-primary-65b8877bbf-9z42x` is `1/1 Running` with 0 restarts and `deployment.apps/postgres-primary` reports `1/1 … AVAILABLE 1`. The dependency is healthy; matches the page's own "database tier reports healthy".
- **Service exists but has no endpoints (selector/label mismatch on the DB)** — ruled out as the cause. `service/postgres-primary` selector is `app=postgres-primary`, matching `deployment.apps/postgres-primary` selector `app=postgres-primary`, whose pod is Running. Even if endpoints were empty, it would be irrelevant: the client is dialing `db-primary`, a name with no Service object at all, so it never reaches endpoint resolution.
- **Wrong port** — ruled out. The init container targets `:5432` (log: `waiting for db-primary:5432`) and `service/postgres-primary` publishes `5432/TCP`. The port is correct; only the host is wrong.
- **Cluster DNS broken** — ruled out. Both `coredns-559f6c778d-*` pods are `1/1 Running` (10h, 0 restarts), `deployment.apps/coredns` is `2/2`, and `service/kube-dns` exists on 10.96.0.10. Cluster-wide DNS failure would also have disrupted long-running control-plane and `local-path-provisioner` workloads, which are all healthy. Failure to resolve is specific to a name that genuinely does not exist.
- **Image pull failure / bad image** — ruled out. Event: `Pulled … Container image "busybox:1.36" already present on machine and can be accessed by the pod`, followed by `Created` and `Started`. No `ErrImagePull`/`ImagePullBackOff` anywhere.
- **Scheduling pressure / unschedulable pods / node taints** — ruled out. Both pods show `PodScheduled True` and event `Successfully assigned billing/... to incident-lab-control-plane`; no `FailedScheduling` events; `NOMINATED NODE <none>`.
- **Missing ConfigMap blocking volume mount** — ruled out. Volume `scripts` is `ConfigMap billing-api-scripts, Optional: false`; a missing ConfigMap would leave the pod at `ContainerCreating` with a `FailedMount` event. Instead the container reached `Started`, `PodReadyToStartContainers True`, and the script produced log output — so `wait-for-db.sh` mounted and executed fine.
- **App container crashlooping / bad readiness probe on `api`** — ruled out. The `api` container has `Container ID: <empty>`, `Restart Count: 0`, and `Reason: PodInitializing` — it has never been started, so neither its code nor any probe can be responsible. No readiness probe is defined in the pod template.
- **Stuck rollout from a previous revision / surge blocking** — ruled out. `OldReplicaSets: <none>`, `deployment.kubernetes.io/revision: 1`, a single ReplicaSet with `2 current / 2 desired`. There is no old revision holding capacity.
- **NetworkPolicy blocking billing → postgres** — considered and ruled out. No NetworkPolicy objects appear in the namespace, and a policy-drop would still require the hostname to resolve first; the log shows the client is targeting a name (`db-primary`) with no corresponding Service, which fails before any packet is policed.

## Verification recipe

```bash
# 1. Confirm the name the init container waits on vs. the Services that actually exist.
kubectl get deploy billing-api -n billing \
  -o jsonpath='{.spec.template.spec.initContainers[0].env}{"\n"}'
kubectl get svc -n billing
# Expect: DB_HOST=db-primary, but only "postgres-primary" listed -> no such Service.

# 2. Prove db-primary does not resolve while postgres-primary does, from inside the namespace.
kubectl run dnscheck -n billing --rm -it --restart=Never --image=busybox:1.36 -- \
  sh -c 'nslookup db-primary; echo "---"; nslookup postgres-primary'
# Expect: NXDOMAIN / "can't resolve" for db-primary; a ClusterIP (10.96.16.233) for postgres-primary.

# 3. Confirm the DB is genuinely reachable at the correct name, i.e. the gate is the only blocker.
kubectl get endpoints postgres-primary -n billing
```

**Remediation:** point the init container at the real Service name.

```bash
kubectl set env deployment/billing-api -n billing --containers='wait-for-db' \
  DB_HOST=postgres-primary
kubectl rollout status deployment/billing-api -n billing --timeout=120s
```

Better: use the fully qualified `postgres-primary.billing.svc.cluster.local` and fix the value in the source manifest/Helm chart so the next release does not reintroduce it. Follow-ups: give `wait-for-db` a bounded timeout so it fails loudly (CrashLoopBackOff with a clear error) instead of hanging silently, and add a CI check that every `DB_HOST`-style value resolves to a Service that exists in the target namespace.

```json
{
  "case_id": "t2-init-wait-for-migrations",
  "failing_resource": {"kind": "Deployment", "namespace": "billing", "name": "billing-api"},
  "mechanism": "The billing-api Deployment's pod template sets DB_HOST=db-primary for its wait-for-db init container, but the database Service in the billing namespace is named postgres-primary, so db-primary resolves to nothing. The init container loops forever on 'waiting for db-primary:5432', the pods never reach Initialized, the api container is never started, and the Deployment reports 0/2 Ready with the rollout stalled.",
  "verdict": "confirmed"
}
```