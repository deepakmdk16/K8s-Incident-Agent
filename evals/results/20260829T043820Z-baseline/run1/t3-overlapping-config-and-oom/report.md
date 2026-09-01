## Root cause

**Deployment `orders/orders-api` cannot start any container because its pod template injects `DATABASE_URL` from a ConfigMap key that does not exist.**

The container `api` declares `DATABASE_URL` as `valueFrom.configMapKeyRef` → ConfigMap `orders/orders-config`, key `db_url`, with `Optional: false`. That key is absent from the ConfigMap, so kubelet refuses to create the container and parks the pod in `CreateContainerConfigError`. Both replicas fail identically and forever (no restart backoff limit applies — the container is never created), so the Deployment stays `0/2 Available`, the `orders-api` Service has zero Ready endpoints, and the gateway reports "no healthy upstream" → `POST /api/orders` returns 502/503.

Verdict: **confirmed** — the kubelet event names the exact missing key, and the config-injection failure is the direct and only reason no `orders-api` container ever runs.

## Evidence chain

1. **The paged workload is down, and it is a config failure, not a crash loop.**
   - `kubectl get all -A`: `orders pod/orders-api-6c64874687-8c47z 0/1 CreateContainerConfigError 0 21s` and `orders pod/orders-api-6c64874687-t6d22 0/1 CreateContainerConfigError 0 21s`. Note `RESTARTS = 0` on both — the container was never created.
   - `kubectl get all -A`: `orders deployment.apps/orders-api 0/2 2 0 21s`.

2. **The causal mechanism, verbatim from kubelet.**
   - describe of pod `orders-api-6c64874687-8c47z`, Events:
     `Warning Failed 3s (x6 over 51s) kubelet spec.containers{api}: Error: couldn't find key db_url in ConfigMap orders/orders-config`
   - describe of pod `orders-api-6c64874687-t6d22`, Events: identical message —
     `Error: couldn't find key db_url in ConfigMap orders/orders-config`
   - Both pods, same error ⇒ not a per-pod/per-node fluke; it is inherent to the pod template.

3. **The bad reference lives in the Deployment's spec, which is why every replica inherits it.**
   - describe of `deployment.apps/orders-api`, Pod Template:
     `DATABASE_URL: <set to the key 'db_url' of config map 'orders-config'> Optional: false`
   - Same line reproduced in describe of `replicaset.apps/orders-api-6c64874687` and in both pod describes. `Optional: false` is what makes the missing key fatal rather than ignorable.

4. **Nothing else blocks startup for this workload.**
   - describe of pod `...-8c47z`: `Normal Pulled 3s (x6 over 51s) kubelet ... Container image "busybox:1.36" already present on machine` — image pull is fine.
   - Conditions: `PodScheduled True`, `Initialized True`, `PodReadyToStartContainers True` — scheduling, networking and volume setup all succeeded; only container creation fails.
   - The other volume, ConfigMap `orders-api-app`, mounts without error (no `FailedMount` event on either `orders-api` pod), so the missing item is specifically the `db_url` key in `orders-config`.

5. **Down-workload → dead Service endpoints → gateway 5xx.**
   - `service/orders-api ClusterIP 10.96.40.243 80/TCP selector app=orders-api`; the only pods carrying `app=orders-api` are the two `CreateContainerConfigError` pods (labels `app=orders-api` in both pod describes), and neither is Ready (`Ready: False`, `ContainersReady: False`). A Service with zero Ready endpoints is exactly the "no healthy upstream" the gateway reports.

## Investigation ledger

- **`orders-report-worker` OOMKilling is the root cause / is dragging orders down.** Ruled out. It is a separate Deployment with a separate Service-less workload: `get all -A` shows no Service selecting `app=orders-report-worker`, so it is not behind `POST /api/orders`. Its failure mode is also different — describe shows `State: Terminated / Reason: OOMKilled / Exit Code: 137`, `Limits: memory: 48Mi`, and log line `report-worker: loading order history into in-memory export buffer (~150MiB)` — a real but independent bug (a ~150MiB buffer under a 48Mi limit). Critically, the `orders-api` pods are `QoS Class: BestEffort` with **no** memory limit and **zero** restarts and were never OOMKilled; their kubelet error is a config-key error, not exit 137. Fix separately, not on this page.
- **Node pressure / eviction caused by the OOM neighbour.** Ruled out. All other pods on `incident-lab-control-plane` are `1/1 Running` with `0` restarts (e.g. `orders/orders-audit-ff8669574-z72nx 1/1 Running 0`), and both `orders-api` pods report `PodScheduled True` and are already assigned an IP. Container-level OOM kill of one container does not evict neighbours, and no `Evicted`/`SystemOOM` events appear.
- **Image pull failure / bad image tag.** Ruled out: `Container image "busybox:1.36" already present on machine and can be accessed by the pod` in both `orders-api` pod describes; status would be `ImagePullBackOff`, not `CreateContainerConfigError`.
- **Missing/failed `orders-api-app` ConfigMap volume (bad `/app/run.sh`).** Ruled out for `orders-api`: no `FailedMount` event on either pod and `PodReadyToStartContainers True`; a broken script would produce `CrashLoopBackOff` with restarts, not a container that is never created. (The transient `FailedMount ... failed to sync configmap cache` event appears only on the report-worker pod and resolved — it later reports `Container started`.)
- **Service selector / label mismatch causing empty endpoints.** Ruled out: `service/orders-api` selector `app=orders-api` matches the pod labels `app=orders-api` exactly. The endpoints are empty because the pods are not Ready, not because they are unselected.
- **The entire cluster or control plane is unhealthy.** Ruled out: `kube-system` etcd, apiserver, scheduler, controller-manager, coredns, kube-proxy and kindnet are all `1/1 Running` with `0` restarts, and ~20 unrelated app Deployments are `1/1` Available.
- **Whole ConfigMap `orders-config` is missing.** Ruled out by the wording of the kubelet error: `couldn't find key db_url in ConfigMap orders/orders-config` — a missing ConfigMap yields `configmap "orders-config" not found`. The object exists; only the key is wrong/absent (renamed key, e.g. `database_url`/`DATABASE_URL`, or a truncated config rollout).

## Verification recipe

```bash
# 1. Prove the key is absent from the ConfigMap the Deployment points at.
kubectl -n orders get configmap orders-config -o jsonpath='{.data}' | tr ',' '\n'
#    -> expect NO "db_url" entry (likely a similarly-named key instead)

# 2. Prove the Deployment spec is what demands that exact key.
kubectl -n orders get deploy orders-api \
  -o jsonpath='{.spec.template.spec.containers[0].env[*].valueFrom.configMapKeyRef}{"\n"}'
#    -> {"key":"db_url","name":"orders-config"}

# 3. Prove the symptom chain: no Ready endpoints behind the Service.
kubectl -n orders get endpoints orders-api -o wide
kubectl -n orders get events --field-selector reason=Failed | grep orders-api
#    -> endpoints <none>;  "couldn't find key db_url in ConfigMap orders/orders-config"
```

**Remediation (pick one, then confirm rollout):**
- *Fastest, if the value exists under a different key name:* correct the Deployment's `configMapKeyRef.key` to the real key —
  `kubectl -n orders patch deploy orders-api --type=json -p='[{"op":"replace","path":"/spec/template/spec/containers/0/env/0/valueFrom/configMapKeyRef/key","value":"<actual-key>"}]'`
- *If the key was genuinely dropped from the config rollout:* restore it —
  `kubectl -n orders patch configmap orders-config -p '{"data":{"db_url":"<connection-string>"}}'` then `kubectl -n orders rollout restart deploy/orders-api`.
- Confirm with `kubectl -n orders rollout status deploy/orders-api` and re-check `kubectl -n orders get endpoints orders-api`.
- **Follow-up (separate, non-paging ticket):** raise `orders-report-worker`'s memory limit above its ~150MiB working set (or stream the export instead of buffering) — it is broken but is not on the checkout path.

```json
{
  "case_id": "t3-overlapping-config-and-oom",
  "failing_resource": {"kind": "Deployment", "namespace": "orders", "name": "orders-api"},
  "mechanism": "The orders-api pod template injects DATABASE_URL via configMapKeyRef to key 'db_url' in ConfigMap orders/orders-config with Optional=false, but that key does not exist, so kubelet fails container creation with 'couldn't find key db_url in ConfigMap orders/orders-config' and both replicas sit in CreateContainerConfigError. With zero containers ever started, no pod becomes Ready, so Service orders-api has no endpoints and the gateway has no healthy upstream, returning 502/503 for POST /api/orders.",
  "verdict": "confirmed"
}
```