## Root cause

**Deployment `billing/billing-api`** is misconfigured: its init container `wait-for-db` is given `DB_HOST: db-primary`, but no Service (or any other endpoint) named `db-primary` exists in the `billing` namespace — the database Service is actually named `postgres-primary`. The init container therefore blocks forever in its TCP-wait loop against a hostname that never resolves/connects, `Initialized` stays `False`, the `api` container never starts, both pods stay `0/1 Init:0/1`, and the Deployment reports `0/2 Ready` — which is the paged symptom (release stalled, invoices queuing).

Verdict: **confirmed**.

## Evidence chain

- The stall is in init, not the app: `kubectl get all -A` shows `pod/billing-api-ccb44c44c-89dn7` and `-m756m` both `0/1  Init:0/1  0 RESTARTS`, `Status: Init:0/1`.
- Describe of pod `billing-api-ccb44c44c-89dn7` confirms the blocking stage:
  - Init container `wait-for-db`: `State: Running`, `Ready: False`, `Restart Count: 0`.
  - App container `api`: `State: Waiting`, `Reason: PodInitializing`.
  - Conditions: `Initialized  False`, `Ready  False`, `ContainersReady  False`, while `PodScheduled  True` and `PodReadyToStartContainers  True`.
- The target hostname comes from the pod spec: describe of pod `.../89dn7` → init container env `DB_HOST:  db-primary`. Same in describe of `.../m756m`, and it is baked into the workload spec — `describe deployment.apps/billing-api` Pod Template → `wait-for-db` → `Environment: DB_HOST:  db-primary` (and identically in `describe replicaset.apps/billing-api-ccb44c44c`).
- That hostname has no backing Service. `kubectl get all -A` Services section lists in namespace `billing` only:
  `service/postgres-primary   ClusterIP   10.96.16.233   5432/TCP   SELECTOR app=postgres-primary`. There is no `db-primary` Service anywhere in the cluster (the only other services are `default/kubernetes` and `kube-system/kube-dns`).
- The init script itself reports the loop it is stuck in — log line: `wait-for-db: waiting for db-primary:5432 before starting billing-api`, then repeating every ~5s: `waiting for db-primary:5432` (at `02:12:44`, `02:12:49`, `02:12:54`, `02:12:59`, `02:13:04`). Identical output from both replicas (`-89dn7` and `-m756m`), i.e. deterministic and config-driven, not a flaky pod.
- The database really is healthy, matching the page text ("database tier reports healthy"): `pod/postgres-primary-65b8877bbf-9z42x  1/1  Running`, `deployment.apps/postgres-primary  1/1  1  1`. So the dependency exists and is up — only the *name* the client is dialing is wrong.
- Deployment-level symptom follows directly: `describe deployment.apps/billing-api` → `Replicas: 2 desired | 2 updated | 2 total | 0 available | 2 unavailable`, `Available  False  MinimumReplicasUnavailable`, `Progressing  True  ReplicaSetUpdated`. No rollback path exists — `OldReplicaSets:  <none>`, `revision: 1` — so nothing serves traffic.

## Investigation ledger

- **DNS / CoreDNS broken cluster-wide** — ruled out: `kube-system` shows `coredns-559f6c778d-9sqc8` and `-t9nfq` both `1/1 Running` (10h), plus `kindnet` and `kube-proxy` DaemonSets `1/1` desired/ready. Networking is functional (pods got IPs `10.244.0.127/.128`, `PodReadyToStartContainers True`). A broken resolver would not selectively affect one hostname.
- **Postgres down / not accepting connections** — ruled out: `pod/postgres-primary-65b8877bbf-9z42x  1/1  Running  0 restarts` and `deployment.apps/postgres-primary  1/1  1  1  AVAILABLE`. The DB Service exists with a matching selector (`app=postgres-primary`) and a live pod carrying that label set. The page also states the database tier is healthy.
- **Service selector mismatch on the DB Service** — ruled out: `service/postgres-primary` selector is `app=postgres-primary`, and `deployment.apps/postgres-primary` selector is `app=postgres-primary`, so endpoints should populate. Regardless, the client is not dialing that Service name at all.
- **Image pull failure / bad image** — ruled out: events in both pod describes show `Pulled ... "busybox:1.36" already present on machine and can be accessed by the pod`, `Created`, `Started`. No `ErrImagePull`/`ImagePullBackOff`.
- **Missing ConfigMap `billing-api-scripts` (volume mount failure)** — ruled out: the volume is `Optional: false`, yet the pods mounted it and the init container executed `/app/wait-for-db.sh` successfully, emitting its own log lines. A missing ConfigMap would surface `FailedMount` events and the container would never start.
- **Scheduling / resource pressure (unschedulable, node pressure, taints)** — ruled out: `PodScheduled  True`, event `Successfully assigned billing/billing-api-... to incident-lab-control-plane`, QoS `BestEffort` with no requests, no `FailedScheduling` events.
- **Init container crash-looping on a script bug (e.g. bad shell syntax)** — ruled out: `Restart Count: 0`, `State: Running`, and the script is emitting a coherent, correctly-formatted wait loop. It is functioning as designed; the input value is wrong.
- **App container (`api`) itself broken / failing readiness probe** — ruled out: it has never started — `Container ID: <empty>`, `State: Waiting  Reason: PodInitializing`. There are no readiness probes defined on the pod spec at all.
- **Stalled rollout blocked by an old ReplicaSet / surge settings** — ruled out: `OldReplicaSets:  <none>`, `NewReplicaSet: billing-api-ccb44c44c (2/2 replicas created)`, `SuccessfulCreate` for both pods. The ReplicaSet did its job; the pods are the ones stuck.
- **Note on timeline (not an alternative, a caveat):** the page says "over 20 minutes" but all `billing` objects show `AGE 20s` and the logs span only ~20s. This is consistent with the snapshot being taken shortly after a re-create/re-apply (or a clock/label skew in the capture), and does not change the mechanism — the wait loop is unbounded and deterministic, so it will stall indefinitely at any age.

## Verification recipe

```bash
# 1. Prove the hostname the init container is dialing does not exist as a Service/Endpoints.
kubectl get svc,endpoints -n billing
kubectl get svc db-primary -n billing            # expect: Error from server (NotFound)

# 2. Prove the wrong value is in the workload spec (this is what must change).
kubectl get deploy billing-api -n billing \
  -o jsonpath='{.spec.template.spec.initContainers[0].env}{"\n"}'   # expect: DB_HOST=db-primary

# 3. Prove the real DB is reachable under its correct name (init would pass with it).
kubectl run dnscheck --rm -it --restart=Never -n billing --image=busybox:1.36 -- \
  sh -c 'nslookup db-primary; echo "---"; nc -zv postgres-primary 5432'
```

Expected: `db-primary` fails to resolve, `postgres-primary:5432` connects.

**Remediation:** patch the Deployment's init-container env to the real Service name, then watch the rollout complete.

```bash
kubectl set env deployment/billing-api -n billing --containers='wait-for-db' \
  DB_HOST=postgres-primary
# (or: DB_HOST=postgres-primary.billing.svc.cluster.local)
kubectl rollout status deployment/billing-api -n billing --timeout=120s
```

Longer term: make `wait-for-db.sh` fail fast with a bounded timeout and a non-zero exit so a bad hostname produces a crash-looping init container (a loud, obvious signal) instead of a silent indefinite `Init:0/1`; and source `DB_HOST` from the same manifest/values that name the Service so the two cannot drift.

```json
{
  "case_id": "t2-init-wait-for-migrations",
  "failing_resource": {"kind": "Deployment", "namespace": "billing", "name": "billing-api"},
  "mechanism": "The billing-api Deployment's pod template sets DB_HOST=db-primary for its wait-for-db init container, but no Service by that name exists in the billing namespace (the database Service is postgres-primary). The init container blocks forever in its TCP wait loop, so Initialized never becomes true, the api container never starts, and the Deployment stays at 0/2 Ready with the rollout stalled.",
  "verdict": "confirmed"
}
```