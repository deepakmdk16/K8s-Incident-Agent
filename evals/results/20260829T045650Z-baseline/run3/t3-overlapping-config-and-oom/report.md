# SEV1 OrderSubmit5xx — orders — Incident Report

**Case:** `t3-overlapping-config-and-oom`
**Verdict: confirmed**

## Root cause

`Deployment orders/orders-api` cannot start any container because its pod spec injects
`DATABASE_URL` from `configMapKeyRef{name: orders-config, key: db_url}` with `Optional: false`,
and the ConfigMap `orders/orders-config` does not contain a key named `db_url`. Kubelet
therefore fails container creation with `CreateContainerConfigError` on every attempt, both
replicas stay `0/1` and never pass `ContainersReady`, so the `orders/orders-api` Service has no
ready endpoints and the gateway reports "no healthy upstream" → `POST /api/orders` returns
502/503.

This is a pure configuration-contract mismatch (env key name vs. ConfigMap key), not a crash,
image, scheduling, or capacity problem — the image is already present on the node and the pods
are scheduled and have IPs.

**Remediation (either side of the contract, whichever matches the intended source of truth):**

- If the ConfigMap is authoritative and holds the URL under a different key (e.g. `database_url`,
  `DATABASE_URL`, `url`), patch the Deployment to reference that exact key:
  `kubectl -n orders patch deploy orders-api --type=json -p '[{"op":"replace","path":"/spec/template/spec/containers/0/env/0/valueFrom/configMapKeyRef/key","value":"<actual-key>"}]'`
- If the key was dropped/renamed by mistake in a config rollout, restore it:
  `kubectl -n orders patch configmap orders-config -p '{"data":{"db_url":"<connection-string>"}}'`
  (no rollout restart needed for `configMapKeyRef` — kubelet re-attempts container creation on the
  existing pods; if it does not converge quickly, `kubectl -n orders rollout restart deploy/orders-api`).

Then confirm `kubectl -n orders get endpoints orders-api` lists two addresses.
Separately (P2, non-blocking for checkout): `orders-report-worker` is OOMKill-looping and needs a
memory-limit/workload fix, but it does not serve the order-submission path.

## Evidence chain

1. **The paged workload is down and it is the one behind the order-submission Service.**
   - `kubectl get all -A`: `orders deployment.apps/orders-api 0/2 1 ... 0 available`, and both pods
     `pod/orders-api-6c64874687-8c47z` / `-t6d22` show `0/1 CreateContainerConfigError`.
   - `describe deployment.apps/orders-api`: `Replicas: 2 desired | 2 updated | 2 total | 0 available | 2 unavailable`,
     `Available False MinimumReplicasUnavailable`.
2. **The exact failure reason is the missing ConfigMap key.**
   - `describe pod/orders-api-6c64874687-8c47z` event:
     `Warning Failed 3s (x6 over 51s) kubelet spec.containers{api}: Error: couldn't find key db_url in ConfigMap orders/orders-config`
   - Identical event on the second replica, `describe pod/orders-api-6c64874687-t6d22`:
     `Error: couldn't find key db_url in ConfigMap orders/orders-config` (x5 over 51s).
     Both replicas fail the same way → not a one-off node/pod issue.
3. **The reference is mandatory, so there is no fallback path.**
   - `describe deployment.apps/orders-api` (and both pods):
     `DATABASE_URL: <set to the key 'db_url' of config map 'orders-config'> Optional: false`.
     With `Optional: false`, a missing key is a hard container-create failure.
4. **The container never ran (so it is not a runtime crash).**
   - `describe pod/...-8c47z`: `Container ID: <empty>`, `Image ID: <empty>`,
     `State: Waiting / Reason: CreateContainerConfigError`, `Restart Count: 0`.
5. **Service → no endpoints → gateway 5xx.**
   - `kubectl get all -A`: `orders service/orders-api ClusterIP 10.96.40.243 80/TCP SELECTOR app=orders-api`;
     the only pods carrying `app=orders-api` are the two non-ready ones
     (`describe pod`: `Ready: False`, `ContainersReady: False`). A ClusterIP Service only
     programs ready pods as endpoints, so the upstream pool is empty — matching
     "gateway shows no healthy upstream".
6. **Blast radius is scoped to this one Deployment, not the namespace or cluster.**
   - `orders pod/orders-audit-ff8669574-z72nx 1/1 Running` in the same namespace with the same
     `busybox:1.36` image → namespace, node, image pull, and RBAC/serviceaccount paths are healthy.

## Investigation ledger

- **`orders-report-worker` OOMKill loop (the loud decoy) — ruled out as cause of the page.**
  It is real (`describe pod/orders-report-worker-...`: `Reason: OOMKilled`, `Exit Code: 137`,
  `Restart Count: 3`, `Limits: memory: 48Mi`; log line:
  `report-worker: loading order history into in-memory export buffer (~150MiB)`), but:
  (a) it has **no Service and no ports** (`Port: <none>` in its pod/deployment spec; no
  `orders-report-worker` entry in the Services list), so it cannot be the gateway's upstream;
  (b) it is a separate Deployment with a separate ReplicaSet — it exerts no control over
  `orders-api` pod startup; (c) `orders-api` fails *before* container creation, so no amount of
  memory pressure from the worker explains `couldn't find key db_url`. Fixing the worker would
  leave `orders-api` at 0/2.
- **Node memory pressure / eviction cascading into orders-api — ruled out.**
  `orders-api` pods are `Pending` with `PodScheduled True`, `PodReadyToStartContainers True`, and
  zero restarts; their failure event is a config error, not `Evicted`/`OOMKilled`. Every other
  workload on `incident-lab-control-plane` (23 pods) is `1/1 Running` with 0 restarts.
- **Image pull / registry failure — ruled out.**
  `describe pod`: `Normal Pulled ... Container image "busybox:1.36" already present on machine and
  can be accessed by the pod` (x6). No `ErrImagePull`/`ImagePullBackOff`.
- **Scheduling / capacity / affinity — ruled out.**
  `Normal Scheduled ... Successfully assigned orders/orders-api-6c64874687-8c47z to
  incident-lab-control-plane`, `Node-Selectors: <none>`, no `FailedScheduling`, pods have IPs
  `10.244.0.71/.72`.
- **Missing ConfigMap object entirely (rather than a missing key) — ruled out.**
  The kubelet message is `couldn't find key db_url in ConfigMap orders/orders-config`, not
  `configmap "orders-config" not found`. Also the volume ConfigMap `orders-api-app` mounts without
  error (no `FailedMount` on the orders-api pods), so ConfigMap plumbing in the namespace works.
- **Service selector / label mismatch — ruled out.**
  Service selector `app=orders-api` matches the pod labels `app=orders-api` exactly
  (`describe pod` → `Labels: app=orders-api`). Endpoints are empty because the pods are not
  *ready*, not because they are not *selected*.
- **Failing readiness/liveness probe or bad `/app/run.sh` app logic — ruled out.**
  No probes appear in the Deployment spec, and the container has never started
  (`Container ID:` empty, `Restart Count: 0`) — application code has not executed yet.
- **Bad rollout / stuck old ReplicaSet — ruled out.**
  `describe deployment.apps/orders-api`: `deployment.kubernetes.io/revision: 1`,
  `OldReplicaSets: <none>`, `NewReplicaSet: orders-api-6c64874687 (2/2 replicas created)`. There is
  no previous good revision to roll back to; revision 1 was never healthy, so the fix must be a
  forward config change.
- **RBAC / ServiceAccount token problems — ruled out.**
  `kube-api-access-*` projected volume mounts fine on all orders pods, and `orders-audit` runs
  normally with the same `default` service account.

## Verification recipe

```bash
# 1. Show the keys that actually exist in the ConfigMap — expect NO "db_url".
kubectl -n orders get configmap orders-config -o jsonpath='{.data}' | tr ',' '\n'

# 2. Show the key the Deployment demands — expect "db_url" with optional unset/false.
kubectl -n orders get deploy orders-api \
  -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="DATABASE_URL")].valueFrom.configMapKeyRef}{"\n"}'

# 3. Confirm the symptom link: no endpoints for the order-submission Service + the kubelet error.
kubectl -n orders get endpoints orders-api
kubectl -n orders get events --field-selector reason=Failed | grep orders-api
```

Steps 1 and 2 disagreeing on the key name, plus an empty `ENDPOINTS` column in step 3, confirm the
root cause. After remediation, step 3 should list two `10.244.0.x:8080` addresses and
`kubectl -n orders get deploy orders-api` should read `2/2`.

```json
{
  "case_id": "t3-overlapping-config-and-oom",
  "failing_resource": {"kind": "Deployment", "namespace": "orders", "name": "orders-api"},
  "mechanism": "The orders-api pod spec injects DATABASE_URL via configMapKeyRef{name: orders-config, key: db_url} with Optional=false, but that key does not exist in ConfigMap orders/orders-config, so kubelet aborts container creation with CreateContainerConfigError on both replicas. The containers never start, so no pod becomes Ready, the orders-api Service has zero endpoints, and the gateway has no healthy upstream for POST /api/orders and returns 502/503.",
  "verdict": "confirmed"
}
```