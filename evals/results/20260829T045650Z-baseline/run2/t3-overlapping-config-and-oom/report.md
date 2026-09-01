## Root cause

**Deployment `orders/orders-api` cannot start any container because its pod template injects `DATABASE_URL` from a ConfigMap key that does not exist.**

The container spec declares `DATABASE_URL` via `configMapKeyRef` → ConfigMap `orders/orders-config`, key `db_url`, with `Optional: false`. That key is absent from the ConfigMap, so kubelet refuses to create the container and both replicas sit in `CreateContainerConfigError` (a pre-start failure — the image is pulled, the container is never created). With zero Ready pods, Service `orders/orders-api` has no ready endpoints, and the gateway reports "no healthy upstream" and returns 502/503 for `POST /api/orders`.

Verdict: **confirmed** — kubelet states the exact missing key, and the failure mode (never-created containers) directly explains the permanently empty endpoint set behind the order-submission Service.

## Evidence chain

1. **The paged workload is down, deterministically, not flapping.**
   `kubectl get all -A`: `orders   pod/orders-api-6c64874687-8c47z   0/1   CreateContainerConfigError   0   21s` and `pod/orders-api-6c64874687-t6d22   0/1   CreateContainerConfigError   0   21s` — note `RESTARTS = 0` for both; the containers never ran.
   `deployment.apps/orders-api   0/2   2   0` — 0 available.

2. **Exact kubelet error names the missing key.**
   Describe of pod `orders-api-6c64874687-8c47z`, Events:
   `Warning  Failed  3s (x6 over 51s)  kubelet  spec.containers{api}: Error: couldn't find key db_url in ConfigMap orders/orders-config`
   Identical line in describe of pod `orders-api-6c64874687-t6d22`:
   `Error: couldn't find key db_url in ConfigMap orders/orders-config` — so this is a template-level defect, not a one-pod accident.

3. **The offending reference lives in the Deployment spec (so the Deployment/ConfigMap pair is what must change, not the pods).**
   Describe of `deployment.apps/orders-api`, Pod Template:
   `Environment: DATABASE_URL: <set to the key 'db_url' of config map 'orders-config'>  Optional: false`
   Same line reproduced in describe of `replicaset.apps/orders-api-6c64874687` and in both pod describes. `Optional: false` is what makes the missing key fatal rather than an empty env var.

4. **Image / node / scheduling are all fine — failure is strictly at container-config time.**
   Pod describe: `Normal Pulled ... Container image "busybox:1.36" already present on machine and can be accessed by the pod`, `PodScheduled: True`, `Initialized: True`, `Container ID: <empty>`, `State: Waiting / Reason: CreateContainerConfigError`.

5. **Link from "no Ready pods" to the paged 502/503.**
   `service/orders-api   ClusterIP   10.96.40.243   80/TCP   selector app=orders-api`; the only pods carrying `app=orders-api` are the two `0/1` pods (`Labels: app=orders-api` in both pod describes). Non-Ready pods are excluded from Service endpoints, so the order-submission upstream has no healthy backend — matching "gateway shows no healthy upstream."

6. **Deployment status corroborates a permanent, not transient, outage.**
   Describe of `deployment.apps/orders-api`: `Available False MinimumReplicasUnavailable`, `Progressing True ReplicaSetUpdated`, `NewReplicaSet: orders-api-6c64874687 (2/2 replicas created)` — the ReplicaSet created its pods successfully (`SuccessfulCreate` ×2); the block is entirely downstream of that, in kubelet.

## Investigation ledger

- **`orders-report-worker` OOMKill as the cause of checkout 5xx — ruled out.** It is a *different* Deployment (`deployment.apps/orders-report-worker`, label `app=orders-report-worker`) and is not selected by `service/orders-api` (selector `app=orders-api`). It has **no Service at all** in `kubectl get all -A`, and `Port: <none>` in its describe, so it serves no traffic on the checkout path. Its failure is independent: `Limits: memory: 48Mi` vs `log line: "report-worker: loading order history into in-memory export buffer (~150MiB)"` → `Reason: OOMKilled, Exit Code: 137`. Real but a separate, lower-severity issue (batch reporting), and fixing it would not restore `POST /api/orders`. Co-located in the `orders` namespace, which makes it an attractive decoy.
- **Image pull / registry failure — ruled out.** `Normal Pulled ... "busybox:1.36" already present on machine and can be accessed by the pod`; no `ErrImagePull`/`ImagePullBackOff` anywhere.
- **Scheduling / capacity / node pressure — ruled out.** Both pods show `PodScheduled True`, `Successfully assigned orders/... to incident-lab-control-plane`, IPs allocated (10.244.0.71/.72). Every other namespace's pod is `1/1 Running` on the same node, so the node is healthy and not resource-starved.
- **Crash loop in the app itself (bad `/app/run.sh`) — ruled out for orders-api.** `RESTARTS 0` and `Container ID:` empty; the container was never created, so `sh /app/run.sh` never executed. No `BackOff` event on the orders-api pods (contrast with the report-worker, which does show `BackOff`).
- **Missing/failed ConfigMap volume `orders-api-app` — ruled out as the blocker.** The pod condition `PodReadyToStartContainers: True` is set and there is **no** `FailedMount` event on either orders-api pod (unlike the report-worker, which logged `MountVolume.SetUp failed for volume "app" : failed to sync configmap cache`, a transient that then resolved). The only recurring error is the env-var key lookup.
- **Wrong Service selector / label mismatch — ruled out.** `service/orders-api` selector `app=orders-api` matches the pods' `Labels: app=orders-api`; endpoints are empty solely because the pods are not Ready.
- **Bad rollout needing a rollback — ruled out as a remedy path.** `deployment.kubernetes.io/revision: 1`, `OldReplicaSets: <none>` — there is no prior good revision to roll back to; this is revision 1, so the config defect shipped with the initial rollout.
- **Control-plane / DNS/CNI fault — ruled out.** All `kube-system` pods (apiserver, etcd, scheduler, controller-manager, both coredns, kube-proxy, kindnet) are `1/1 Running` with 0 restarts.

## Verification recipe

```bash
# 1. Show the ConfigMap's actual keys — expect 'db_url' to be absent.
kubectl get configmap orders-config -n orders -o jsonpath='{.data}' | tr ',' '\n'

# 2. Show the exact key the Deployment demands (and that it is non-optional).
kubectl get deploy orders-api -n orders \
  -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="DATABASE_URL")].valueFrom.configMapKeyRef}{"\n"}'

# 3. Confirm the Service has no ready endpoints -> gateway 502/503.
kubectl get endpointslice -n orders -l kubernetes.io/service-name=orders-api -o wide
```

Expected: (1) prints keys without `db_url`; (2) prints `{"key":"db_url","name":"orders-config"}` (no `optional:true`); (3) shows no ready addresses.

**Remediation (pick one, then confirm rollout):**
- If the ConfigMap is authoritative and the key was renamed (e.g. it now holds `database_url`), patch the Deployment to reference the correct key:
  `kubectl set env deploy/orders-api -n orders --from=configmap/orders-config --keys=<actual_key> ...` or edit `valueFrom.configMapKeyRef.key`.
- If the Deployment is authoritative and the key was dropped from the ConfigMap, restore it:
  `kubectl patch configmap orders-config -n orders --type merge -p '{"data":{"db_url":"<connection string>"}}'` then `kubectl rollout restart deploy/orders-api -n orders`.

Then: `kubectl rollout status deploy/orders-api -n orders` and re-check endpoints.

*Follow-up (separate, non-paging issue):* raise `orders-report-worker`'s memory limit above its ~150MiB working set or make the export streaming/chunked. Guardrail for both: make non-optional ConfigMap key references part of CI validation, and add a deploy gate on `Deployment.status.availableReplicas > 0` so a revision-1 config defect never reaches production behind the checkout gateway.

```json
{
  "case_id": "t3-overlapping-config-and-oom",
  "failing_resource": {"kind": "Deployment", "namespace": "orders", "name": "orders-api"},
  "mechanism": "The orders-api pod template injects DATABASE_URL from ConfigMap orders/orders-config key 'db_url' with Optional: false, but that key does not exist, so kubelet fails container creation with 'couldn't find key db_url in ConfigMap orders/orders-config' and both replicas stay in CreateContainerConfigError with 0 restarts. Because no pod ever becomes Ready, Service orders/orders-api has no ready endpoints, leaving the gateway with no healthy upstream and returning 502/503 for POST /api/orders.",
  "verdict": "confirmed"
}
```