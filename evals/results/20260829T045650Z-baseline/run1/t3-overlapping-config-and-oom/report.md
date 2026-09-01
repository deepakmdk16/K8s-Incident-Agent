## Root cause

**Deployment `orders/orders-api` cannot start any container because it injects a required environment variable from a ConfigMap key that does not exist.**

Each `orders-api` pod declares `DATABASE_URL` via `configMapKeyRef` → ConfigMap `orders/orders-config`, key `db_url`, with `Optional: false`. The key `db_url` is absent from that ConfigMap, so the kubelet fails at container-config assembly time, before the container is ever created, and the pod parks in `CreateContainerConfigError` and never becomes Ready. With 0/2 Ready pods, the `orders/orders-api` Service (`app=orders-api`) has no ready endpoints, so the gateway has no healthy upstream and `POST /api/orders` returns 502/503.

Verdict: **confirmed** — the kubelet event names the exact missing key and the exact ConfigMap, and the mechanism deterministically explains 0/2 Ready and the empty upstream pool.

## Evidence chain

- Paged workload is down, not merely degraded:
  - `kubectl get all -A`: `orders  deployment.apps/orders-api  0/2  2  0  21s` — 2 desired, 0 available.
  - `describe deployment.apps/orders-api -n orders`: `Replicas: 2 desired | 2 updated | 2 total | 0 available | 2 unavailable`, and `Available  False  MinimumReplicasUnavailable`.
- Both replicas fail identically at container-config time:
  - `kubectl get all -A`: `pod/orders-api-6c64874687-8c47z  0/1  CreateContainerConfigError` and `pod/orders-api-6c64874687-t6d22  0/1  CreateContainerConfigError`.
  - `describe pod/orders-api-6c64874687-8c47z -n orders`: container `api` → `State: Waiting`, `Reason: CreateContainerConfigError`, `Container ID: <empty>`, `Image ID: <empty>` (container never created).
- The exact cause, stated by the kubelet:
  - `describe pod/orders-api-6c64874687-8c47z -n orders`, Events: `Warning  Failed  3s (x6 over 51s)  kubelet  spec.containers{api}: Error: couldn't find key db_url in ConfigMap orders/orders-config`
  - Identical on the second replica — `describe pod/orders-api-6c64874687-t6d22 -n orders`, Events: `Warning  Failed  10s (x5 over 51s)  kubelet  spec.containers{api}: Error: couldn't find key db_url in ConfigMap orders/orders-config`
- The reference is hard-required (no fallback):
  - `describe deployment.apps/orders-api -n orders`, Pod Template: `Environment: DATABASE_URL: <set to the key 'db_url' of config map 'orders-config'>  Optional: false`. Because `Optional: false`, a missing key is fatal rather than skipped.
  - The same line appears in `describe replicaset.apps/orders-api-6c64874687 -n orders` and in both pod describes, confirming the defect lives in the Deployment's pod template and is inherited by every replica.
- Image/scheduling are healthy, isolating the failure to config:
  - `describe pod/orders-api-6c64874687-8c47z`: `Normal  Scheduled ... Successfully assigned orders/orders-api-6c64874687-8c47z to incident-lab-control-plane`; `Normal  Pulled  ... Container image "busybox:1.36" already present on machine and can be accessed by the pod`; Conditions `PodScheduled True`, `Initialized True`.
- Link to the customer-facing symptom:
  - `kubectl get all -A` services: `orders  service/orders-api  ClusterIP  10.96.40.243  80/TCP  SELECTOR app=orders-api`. Pod labels are `app=orders-api` (`describe pod ...`, Labels), but both pods are `Ready: False` / `ContainersReady: False`, so no endpoint is ready → the gateway sees no healthy upstream → 502/503 on `POST /api/orders`.

## Investigation ledger

- **`orders-report-worker` OOMKill is the cause of checkout 5xx** — ruled out. It is a real but separate fault: `describe pod/orders-report-worker-...`: `Reason: OOMKilled`, `Exit Code: 137`, `Limits: memory: 48Mi`, and `log line: "report-worker: loading order history into in-memory export buffer (~150MiB)"`. It is not on the order-submission path: it has `Port: <none>` and no Service selects `app=orders-report-worker` (no such entry in the Services list). The paged Service `orders-api` selects `app=orders-api` only. Fixing its memory limit would not restore a single `orders-api` endpoint.
- **Image pull failure / bad image tag** — ruled out. Events show `Normal  Pulled ... Container image "busybox:1.36" already present on machine and can be accessed by the pod`; status would be `ImagePullBackOff`/`ErrImagePull`, not `CreateContainerConfigError`.
- **Scheduling pressure / node NotReady / insufficient resources** — ruled out. `PodScheduled True`, both pods have node `incident-lab-control-plane` and IPs assigned; `orders-api` pods are `QoS Class: BestEffort` with no resource requests; ~24 other Deployments on the same node are `1/1 Running`.
- **Missing ConfigMap volume `orders-api-app` (the `/app/run.sh` script mount)** — ruled out as the paged cause. Volume mount failures surface as `FailedMount`; the `orders-api` pods show `PodReadyToStartContainers True`, `Initialized True`, and no `FailedMount` events. The only `FailedMount` in the whole capture is on the report-worker (`MountVolume.SetUp failed for volume "app" : failed to sync configmap cache: timed out waiting for the condition`), and that one recovered (`Container created` / `Container started` x4).
- **Crashing app code / bad `/app/run.sh` / failing readiness probe on `orders-api`** — ruled out. The container never started: `Container ID:` and `Image ID:` are empty, `Restart Count: 0`, and there are no `Created`/`Started`/`BackOff` events for container `api`. No probe is configured in the pod template.
- **Service selector mismatch / wrong port on `orders-api`** — ruled out. Service selector `app=orders-api` matches the pod label `app=orders-api`; the endpoint set is empty solely because no pod is Ready, not because of a label mismatch.
- **Bad rollout that could be rolled back** — ruled out as a remedy path. `deployment.kubernetes.io/revision: 1`, `OldReplicaSets: <none>` — there is no previous good revision; the config reference must be corrected forward.
- **Cluster/control-plane or DNS outage** — ruled out. All `kube-system` pods (`etcd`, `kube-apiserver`, `kube-controller-manager`, `kube-scheduler`, `kube-proxy`, `kindnet`, both `coredns`) are `1/1 Running` with `0` restarts.

**Remediation (fix forward, either direction — pick whichever matches the source of truth):**
1. Add the missing key to the ConfigMap: `kubectl -n orders patch configmap orders-config --type merge -p '{"data":{"db_url":"<the real DSN>"}}'`, then `kubectl -n orders rollout restart deploy/orders-api` (kubelet retries automatically, but a restart makes it deterministic); **or**
2. If the value already exists in `orders-config` under a different key name (e.g. `database_url`), correct the Deployment's `env[].valueFrom.configMapKeyRef.key` in `orders/orders-api` to that name.
3. Follow-up, non-paging: raise the `48Mi` memory limit on `orders/orders-report-worker` (or cap its export buffer) so it stops OOMKilling — track separately, it does not affect checkout.

## Verification recipe

```bash
# 1. Show the ConfigMap's actual keys — expect NO "db_url" key.
kubectl -n orders get configmap orders-config -o jsonpath='{range .data.*}{"\n"}{end}{"KEYS: "}{.data}' ; echo
kubectl -n orders describe configmap orders-config

# 2. Re-read the kubelet's verdict on the paged pods.
kubectl -n orders get events --field-selector reason=Failed | grep orders-api

# 3. Prove the Service has no ready endpoints (why the gateway 502/503s).
kubectl -n orders get endpoints orders-api -o wide
kubectl -n orders get endpointslice -l kubernetes.io/service-name=orders-api -o yaml | grep -A3 conditions
```

Expected: step 1 lists keys without `db_url`; step 2 prints `couldn't find key db_url in ConfigMap orders/orders-config`; step 3 shows `ENDPOINTS  <none>` (or `ready: false`). After patching the key, `kubectl -n orders rollout status deploy/orders-api` reaches `2/2` and endpoints populate.

```json
{
  "case_id": "t3-overlapping-config-and-oom",
  "failing_resource": {"kind": "Deployment", "namespace": "orders", "name": "orders-api"},
  "mechanism": "The orders-api pod template injects DATABASE_URL from configMapKeyRef orders-config key 'db_url' with Optional=false, but that key does not exist in the ConfigMap. The kubelet therefore fails container config assembly and both replicas stay in CreateContainerConfigError, never becoming Ready, so the orders-api Service has zero ready endpoints and the gateway returns 502/503 for POST /api/orders.",
  "verdict": "confirmed"
}
```