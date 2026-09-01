## Root cause

**Verdict: confirmed.**

The failing resource is `Deployment billing/billing-api`. Its init container `wait-for-db` is configured with `DB_HOST: db-primary`, but no Service named `db-primary` exists in the `billing` namespace (or anywhere in the cluster) — the database Service is named `postgres-primary`. The init container therefore blocks forever in its TCP-wait loop against an unresolvable/unreachable host, `Initialized` never flips to `True`, the `api` container never starts, and the Deployment stays at `0/2` Ready with `Available=False / MinimumReplicasUnavailable`. The database itself is fine; only the hostname the client is pointed at is wrong.

## Evidence chain

- **Symptom, from `kubectl get all -A`:** `deployment.apps/billing-api  0/2  2  0  20s` — two replicas created, zero available.
- **Both pods are stuck pre-start, from `kubectl get all -A`:** `pod/billing-api-ccb44c44c-89dn7  0/1  Init:0/1` and `pod/billing-api-ccb44c44c-m756m  0/1  Init:0/1` — status `Init:0/1` means the single init container has not completed.
- **The blocking container and its target, from `describe pod/billing-api-ccb44c44c-89dn7`:**
  - Init container `wait-for-db`, `State: Running`, `Ready: False`, `Restart Count: 0` — it is not crashing, it is *hanging*.
  - `Environment: DB_HOST:  db-primary`.
  - App container `api`: `State: Waiting`, `Reason: PodInitializing` — gated purely by the init container.
  - `Conditions: Initialized  False`, `Ready  False`, `PodScheduled  True`, `PodReadyToStartContainers  True`.
- **What it is waiting on, from the init-container logs (identical on both pods):**
  `wait-for-db: waiting for db-primary:5432 before starting billing-api`, then repeating `waiting for db-primary:5432` every ~5s at `02:12:44`, `02:12:49`, `02:12:54`, `02:12:59`, `02:13:04`. Steady 5-second retry cadence with no error/exit = a poll loop that never succeeds.
- **The name does not exist, from the Services list in `kubectl get all -A`:** the only Service in `billing` is `service/postgres-primary  ClusterIP 10.96.16.233  5432/TCP  SELECTOR app=postgres-primary`. There is no `db-primary` Service in `billing` or in any other namespace (only `default/kubernetes` and `kube-system/kube-dns` exist). So `db-primary` resolves to nothing.
- **The misconfiguration lives in the workload spec, not just the pods, from `describe deployment.apps/billing-api`:** Pod Template → Init Containers → `wait-for-db` → `Environment: DB_HOST:  db-primary`. Confirmed again in `describe replicaset.apps/billing-api-ccb44c44c` (same env in the template), so every replica the ReplicaSet creates inherits the wrong hostname — deleting pods will not help.
- **The database is genuinely healthy (page text corroborated), from `kubectl get all -A`:** `pod/postgres-primary-65b8877bbf-9z42x  1/1  Running  0 restarts` and `deployment.apps/postgres-primary  1/1  1  1`, with the `postgres-primary` Service selector `app=postgres-primary` matching that pod's Deployment/ReplicaSet labels. The dependency is up; the client is dialing the wrong name.

Causal chain: wrong `DB_HOST` value in the Deployment pod template → init container's TCP probe to `db-primary:5432` never succeeds → `Initialized=False` forever → `api` container stuck in `PodInitializing` → `0/2` Ready → `Available=False`, release stalled, invoices queue.

Note on timing: the alert says "over 20 minutes," but every `billing` object shows `AGE 20s` and pod events are `21s` old. The captured output is a snapshot taken shortly after a (re)apply/restart of the namespace objects; the failure mode is deterministic and identical across both replicas, so this does not change the diagnosis — it will hang indefinitely at any age.

## Investigation ledger

- **Database tier down / not accepting connections** — ruled out. `postgres-primary-65b8877bbf-9z42x` is `1/1 Running` with `0` restarts, `deployment.apps/postgres-primary` is `1/1` available, and `service/postgres-primary` has ClusterIP `10.96.16.233` on `5432/TCP` with a selector (`app=postgres-primary`) that matches the running pod's ReplicaSet labels. Also consistent with the page's own "database tier reports healthy."
- **Service selector mismatch leaving the DB Service with no endpoints** — ruled out on the available evidence: `service/postgres-primary` selector `app=postgres-primary` matches `replicaset.apps/postgres-primary-65b8877bbf` selector `app=postgres-primary,pod-template-hash=...`. Regardless, the client never reaches that Service — it is asking for `db-primary`.
- **Image pull failure / bad image** — ruled out. Events show `Pulled: Container image "busybox:1.36" already present on machine and can be accessed by the pod` and `Created` / `Started` for the init container; no `ErrImagePull`/`ImagePullBackOff` anywhere.
- **Scheduling problem (resources, taints, node pressure)** — ruled out. `PodScheduled True`, event `Successfully assigned billing/... to incident-lab-control-plane`, both pods have IPs (`10.244.0.127`, `10.244.0.128`), no `FailedScheduling` events, `QoS Class: BestEffort` with no resource requests to unsatisfy.
- **Missing/failed ConfigMap volume `billing-api-scripts`** — ruled out. The volume is `Optional: false`, yet the pods mounted it and the init container actually executed `/app/wait-for-db.sh` and emitted its own log banner. A missing ConfigMap would produce `FailedMount` events and a container that never starts.
- **Init container crashlooping (bad script, exit non-zero)** — ruled out. `State: Running`, `Restart Count: 0`, and logs show an orderly repeating 5-second poll — a hang, not a crash.
- **Readiness/liveness probe failing on the `api` container** — ruled out. The `api` container has never started (`State: Waiting`, `Reason: PodInitializing`, empty `Container ID`), and no probes are defined in the Deployment pod template.
- **Cluster DNS broken (which would also break `postgres-primary`)** — ruled out as the *root* cause. Both `coredns` pods are `1/1 Running` for `10h` with `0` restarts and `service/kube-dns` is present. Even a healthy DNS cannot resolve a Service name that does not exist. If DNS were broken cluster-wide, other workloads would be failing too; none are.
- **Rollout stuck behind an old ReplicaSet / bad rollout strategy** — ruled out. `OldReplicaSets: <none>`, `NewReplicaSet: billing-api-ccb44c44c (2/2 replicas created)`, `SuccessfulCreate` for both pods. The ReplicaSet created what it was told to; the pods themselves are blocked.
- **Wrong port rather than wrong host** — ruled out as the driver. The DB Service exposes `5432/TCP` and the init container probes `:5432`; the port matches. Only the hostname differs.
- **Cross-namespace Service (`db-primary` living elsewhere, needing an FQDN)** — ruled out from the full `-A` Service listing: no `db-primary` Service exists in any namespace.

## Verification recipe

```bash
# 1. Prove the name the init container waits on does not exist, while the real one does.
kubectl get svc -A | grep -E 'db-primary|postgres-primary'
#    Expect: only billing/postgres-primary appears; no db-primary anywhere.

# 2. Prove the bad hostname is baked into the Deployment's pod template (not just the pods).
kubectl get deploy billing-api -n billing \
  -o jsonpath='{.spec.template.spec.initContainers[0].env[?(@.name=="DB_HOST")].value}{"\n"}'
#    Expect: db-primary

# 3. Prove the DB is reachable under its real name from inside the namespace.
kubectl run dbcheck -n billing --rm -it --restart=Never --image=busybox:1.36 -- \
  sh -c 'nslookup db-primary; echo "---"; nc -zv -w3 postgres-primary 5432'
#    Expect: db-primary => NXDOMAIN / can't resolve; postgres-primary:5432 => open.
```

**Remediation:** point the init container (and any app-side DB config) at the real Service name.

```bash
kubectl set env deployment/billing-api -n billing --containers='' \
  --overwrite DB_HOST=postgres-primary   # if the flag form doesn't target init containers, patch directly:

kubectl patch deployment billing-api -n billing --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/initContainers/0/env/0/value","value":"postgres-primary.billing.svc.cluster.local"}]'

kubectl rollout status deployment/billing-api -n billing --timeout=120s
```

Fix this in the source manifest/Helm values too, or the next deploy reintroduces it. Follow-ups: give `wait-for-db` a bounded timeout so it fails loudly (`CrashLoopBackOff` with a clear error) instead of hanging silently, and add a release-gate check that every hostname referenced in a workload's env resolves to an existing Service.

```json
{
  "case_id": "t2-init-wait-for-migrations",
  "failing_resource": {"kind": "Deployment", "namespace": "billing", "name": "billing-api"},
  "mechanism": "The billing-api Deployment's pod template sets DB_HOST=db-primary for its wait-for-db init container, but no Service by that name exists in the cluster; the only database Service is billing/postgres-primary. The init container's TCP poll of db-primary:5432 can never succeed, so it runs forever, Initialized stays False, the api container is pinned in PodInitializing, and the Deployment reports 0/2 Ready with Available=False.",
  "verdict": "confirmed"
}
```