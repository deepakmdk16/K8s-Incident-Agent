## Root cause

**Verdict: confirmed.**

`Deployment orders/orders-api` cannot start any container because its pod template injects `DATABASE_URL` from a ConfigMap key that does not exist: `configMapKeyRef` → ConfigMap `orders/orders-config`, key `db_url`, with `Optional: false`. Kubelet resolves env vars *before* creating the container, so both replicas are stuck in `CreateContainerConfigError` in `Pending` state, never become Ready, and are therefore never added to the `orders/orders-api` Service endpoints. With zero healthy endpoints behind the Service, the gateway has no upstream for order submission and returns 502/503 for `POST /api/orders`.

This is a hard-fail config binding, not a crash loop: the image pulls fine and the process never runs at all (`Container ID:` is empty, `Restart Count: 0`).

## Evidence chain

- **Symptom → workload**: `kubectl get all -A`: `orders deployment.apps/orders-api 0/2 1 ... 0 available`; both pods `orders/orders-api-6c64874687-8c47z` and `-t6d22` show `STATUS CreateContainerConfigError`, `READY 0/1`.
- **Exact failure reason** — describe of pod `orders-api-6c64874687-8c47z`, Events:
  `Warning Failed 3s (x6 over 51s) kubelet spec.containers{api}: Error: couldn't find key db_url in ConfigMap orders/orders-config`
  Identical line in describe of pod `orders-api-6c64874687-t6d22` (`x5 over 51s`), so it affects **both** replicas, not one bad node/pod.
- **Where the bad reference lives (the spec that must change)** — describe of `deployment.apps/orders-api`, Pod Template:
  `Environment: DATABASE_URL: <set to the key 'db_url' of config map 'orders-config'>  Optional: false`
  The same line appears in describe of `replicaset.apps/orders-api-6c64874687` and in both pods, i.e. it is inherited from the Deployment template, and `Optional: false` makes the missing key fatal rather than skippable.
- **Container never ran**: describe of pod `-8c47z`: `Container ID:` (blank), `Image ID:` (blank), `State: Waiting / Reason: CreateContainerConfigError`, `Restart Count: 0`. Image is available: `Normal Pulled ... Container image "busybox:1.36" already present on machine`.
- **No healthy upstream mechanism**: `service/orders-api ClusterIP 10.96.40.243 80/TCP` with `SELECTOR app=orders-api`; the only pods carrying `app=orders-api` are the two non-Ready ones (`Ready: False`, `ContainersReady: False` in both describes). A Service only programs endpoints for Ready pods → zero endpoints → gateway 502/503.
- **Deployment-level confirmation of outage duration/state** — describe of `deployment.apps/orders-api`:
  `Replicas: 2 desired | 2 updated | 2 total | 0 available | 2 unavailable`, `Available False MinimumReplicasUnavailable`, `Progressing True ReplicaSetUpdated`. Only one revision exists (`deployment.kubernetes.io/revision: 1`, `OldReplicaSets: <none>`), so there is no previously-good ReplicaSet to fall back to.

## Investigation ledger

- **`orders-report-worker` OOMKilled (the loud co-tenant)** — ruled out as cause of the page. Describe of pod `orders-report-worker-5645b7fccf-zhjzb`: `Reason: OOMKilled`, `Exit Code: 137`, `Limits: memory: 48Mi`; log line: `report-worker: loading order history into in-memory export buffer (~150MiB)`. It is a separate Deployment with label `app=orders-report-worker`, which does **not** match `service/orders-api`'s selector `app=orders-api`, so it never served checkout traffic. Its container also *does* start (`Container ID: containerd://9a746155...`, `Restart Count: 3`), a different failure mode from the API pods. Real bug worth its own ticket (it needs ~150MiB against a 48Mi limit), but it cannot produce 502s on `POST /api/orders`.
- **Image pull / registry failure** — ruled out: `Normal Pulled ... "busybox:1.36" already present on machine and can be accessed by the pod` in both orders-api pod describes; no `ImagePullBackOff`/`ErrImagePull` anywhere in `kubectl get all -A`.
- **Scheduling / node capacity / taints** — ruled out: both pods have `PodScheduled True` and `Normal Scheduled ... Successfully assigned orders/orders-api-... to incident-lab-control-plane`; no `FailedScheduling` events, no `NOMINATED NODE`, `Node-Selectors: <none>`.
- **Missing/failed ConfigMap *volume*** (`orders-api-app` mounted at `/app`, supplying `run.sh`) — ruled out as the blocker: describe shows `PodReadyToStartContainers True` and `Initialized True` with no `FailedMount` event on the API pods; the only error is the env-key lookup. (Contrast: the report-worker pod *did* log `Warning FailedMount ... failed to sync configmap cache`, a transient that self-resolved.) If `orders-api-app` were absent we would see a mount error instead.
- **ConfigMap `orders-config` entirely absent** — not distinguishable as "absent" here, and it does not matter for the fix path: kubelet's message is `couldn't find key db_url in ConfigMap orders/orders-config`, which is emitted when the ConfigMap exists but lacks the key (a missing ConfigMap yields `configmap "orders-config" not found`). So the ConfigMap object exists; only the key is wrong/missing.
- **Application-level crash, bad liveness/readiness probe, or slow startup** — ruled out: no probes are defined in the Deployment template, and the container never reached `Created`/`Started` (blank `Container ID`, `Restart Count: 0`, no `BackOff` events on the API pods).
- **Cluster/control-plane or DNS degradation** — ruled out: all `kube-system` pods (`etcd`, `kube-apiserver`, `kube-scheduler`, `kube-controller-manager`, `coredns` x2, `kube-proxy`, `kindnet`) are `1/1 Running` with `0` restarts, and every other namespace's workload is `1/1 Running`. The failure is isolated to `orders-api`.
- **Bad rollout needing a rollback** — ruled out as a remedy: `deployment.kubernetes.io/revision: 1` and `OldReplicaSets: <none>`; there is no prior good revision, so the fix must be forward (correct the key or the reference).

**Remediation**

1. Immediate (pick whichever matches intent, then the ReplicaSet self-heals — no rollout needed if you fix the ConfigMap, since kubelet retries container creation):
   - If the connection string exists under a different key: inspect and correct the Deployment's reference —
     `kubectl -n orders patch deploy/orders-api --type=json -p='[{"op":"replace","path":"/spec/template/spec/containers/0/env/0/valueFrom/configMapKeyRef/key","value":"<actual-key>"}]'`
   - If the key is genuinely missing: add it —
     `kubectl -n orders patch configmap orders-config --type=merge -p '{"data":{"db_url":"<postgres://...>"}}'`
     (Secrets are the better home for a DSN with credentials; consider `secretKeyRef` instead.)
2. Confirm recovery: `kubectl -n orders rollout status deploy/orders-api` and `kubectl -n orders get endpoints orders-api` (must list two `:8080` addresses), then re-test `POST /api/orders` through the gateway.
3. Follow-ups: (a) file a separate ticket to raise `orders-report-worker`'s memory limit above its ~150MiB working set or make the export streaming/chunked — it is currently `OOMKilled` in a back-off loop and reports will silently not be produced; (b) add a CI/admission check that every `configMapKeyRef`/`secretKeyRef` with `optional: false` resolves against the target namespace before merge, since this class of typo bypasses image and schema validation entirely.

## Verification recipe

```bash
# 1. Show the ConfigMap's actual keys — expect db_url to be absent/misnamed
kubectl -n orders get configmap orders-config -o jsonpath='{range .data.*}{"\n"}{end}{"\n---keys---\n"}' ; \
kubectl -n orders get configmap orders-config -o go-template='{{range $k,$v := .data}}{{$k}}{{"\n"}}{{end}}'

# 2. Show the Deployment's env reference and the kubelet's rejection side by side
kubectl -n orders get deploy orders-api -o jsonpath='{.spec.template.spec.containers[0].env[*].valueFrom.configMapKeyRef}{"\n"}' ; \
kubectl -n orders get events --field-selector reason=Failed --sort-by=.lastTimestamp | tail -5

# 3. Prove the Service has no backends (the gateway's "no healthy upstream")
kubectl -n orders get endpoints orders-api -o wide
```

Expect: step 1 lists keys that do not include `db_url`; step 2 prints `{"key":"db_url","name":"orders-config"}` alongside `Error: couldn't find key db_url in ConfigMap orders/orders-config`; step 3 shows `ENDPOINTS <none>`.

```json
{
  "case_id": "t3-overlapping-config-and-oom",
  "failing_resource": {"kind": "Deployment", "namespace": "orders", "name": "orders-api"},
  "mechanism": "The orders-api pod template injects DATABASE_URL via configMapKeyRef to key 'db_url' in ConfigMap orders/orders-config with Optional: false, but that key does not exist, so kubelet fails env resolution before container creation and both replicas sit in CreateContainerConfigError, never starting. Because neither pod ever becomes Ready, the orders-api Service has zero endpoints, leaving the gateway with no healthy upstream and returning 502/503 for POST /api/orders.",
  "verdict": "confirmed"
}
```