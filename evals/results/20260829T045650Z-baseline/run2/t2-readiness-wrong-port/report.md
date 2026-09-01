## Root cause

The `orders-api` Deployment (`orders` namespace) ships a readiness probe pointed at the wrong port: the container listens on **8080** but the probe does an HTTP GET on **:8081**. The probe never succeeds, so both replicas stay `Ready: False`, the `orders-api` Service has zero ready endpoints, and the checkout gateway's calls to `orders-api:8080` fail — surfacing as 5xx at checkout. Verdict: **confirmed**.

## Root cause
The failing resource is `Deployment/orders-api` in namespace `orders`. Its pod template declares `Readiness: http-get http://:8081/` while the application process binds port 8080 (`Port: 8080/TCP (http)`, log line `orders-api: serving on :8080`). Nothing is listening on 8081, so every readiness probe gets `connection refused`. Kubernetes therefore never marks the pods Ready, never adds their IPs to the `orders-api` Service endpoints, and traffic from `checkout-gateway` to the Service has nowhere to land — the gateway's calls "do not complete" and it returns 5xx to shoppers. The application itself is healthy; only the probe's port is wrong.

## Evidence chain
- **Symptom in cluster state** — `kubectl get all -A`: `orders pod/orders-api-7cc5bcf4c7-lst42 0/1 Running` and `orders pod/orders-api-7cc5bcf4c7-pcspl 0/1 Running`; `deployment.apps/orders-api 0/2 ... AVAILABLE 0`. Both pods are Running (process alive) but not Ready.
- **The probe target** — describe of deployment `orders-api`: `Readiness: http-get http://:8081/ delay=0s timeout=1s period=5s successThreshold=1 failureThreshold=3`. The same line appears in describe of ReplicaSet `orders-api-7cc5bcf4c7` and both pod describes, confirming it comes from the Deployment's pod template, not a one-off pod edit.
- **The port actually served** — log line from both pods: `orders-api: serving on :8080`. Also the container spec: `Port: 8080/TCP (http)`.
- **Direct causal link probe→failure** — describe of pod `orders-api-7cc5bcf4c7-lst42`: `Warning Unhealthy ... Readiness probe failed: Get "http://10.244.0.122:8081/": dial tcp 10.244.0.122:8081: connect: connection refused` (x4 over 15s). Identical event on `-pcspl` at `10.244.0.123:8081`. Connection *refused* (not timeout) means the pod IP is reachable and the kernel actively rejected the port — i.e. nothing bound to 8081, while 8080 is open.
- **Link to the paged symptom** — `kubectl get all -A`: `service/orders-api ClusterIP 10.96.113.141 ... 8080/TCP ... SELECTOR app=orders-api`. The selector matches the pods' label `app=orders-api` (pod describe: `Labels: app=orders-api`), but non-Ready pods are excluded from the ready endpoint set, so the Service has no backends. `pod/checkout-gateway-7b867bfc46-fgfqx 1/1 Running` — the gateway is up and is the thing observing the failure, consistent with "gateway reports its calls to the orders backend are not completing."
- **Timing** — Deployment `Events: Normal ScalingReplicaSet ... Scaled up replica set orders-api-7cc5bcf4c7 from 0 to 2`, `deployment.kubernetes.io/revision: 1`, `AGE 5s` on the workload, matching "released about 30 minutes ago" — the outage began with this rollout.

## Investigation ledger
- **Application crash / bad image** — Ruled out: `State: Running`, `Restart Count: 0` on both pods, image `busybox:1.36` `already present on machine`, and the app logged `orders-api: serving on :8080`. A crashing or unpullable container would show CrashLoopBackOff/ImagePullBackOff and restarts.
- **App listening on the wrong port (i.e. the app is broken, not the probe)** — Ruled out as the fix target: the container port declaration and Service port both say 8080, and the app serves 8080. The only artifact referencing 8081 is the readiness probe, so the probe is the outlier.
- **Service selector / label mismatch** — Ruled out: Service selector `app=orders-api` exactly matches pod label `app=orders-api`. Endpoints are empty because of readiness, not selection.
- **Service port / targetPort misroute** — Ruled out as the paged cause: Service exposes `8080/TCP`, matching the port the app serves. Even if targetPort were suspect, no traffic could flow anyway because there are zero ready endpoints; the readiness failure is upstream and sufficient.
- **checkout-gateway itself being broken** — Ruled out: `checkout-gateway-7b867bfc46-fgfqx 1/1 Running`, `RESTARTS 0`, deployment `1/1` available, no warning events. It is the reporter of the failure, not the failure.
- **Cluster / networking / DNS / node problem** — Ruled out: all `kube-system` pods (`coredns` x2, `kube-proxy`, `kindnet`, apiserver, scheduler, controller-manager, etcd) are `1/1 Running` with 0 restarts and 10h age; only the freshly-released `orders-api` is unhealthy. The probe error is `connection refused` from the pod's own IP, which means kubelet reached the pod network fine.
- **Resource pressure / eviction / scheduling** — Ruled out: both pods `Successfully assigned` immediately, `PodScheduled True`, no `FailedScheduling`, `Evicted`, or OOM events.
- **Missing ConfigMap volume** — Ruled out: volume `scripts` from ConfigMap `orders-api-scripts` with `Optional: false`; the container started and executed `/app/run.sh` successfully (it logged its startup line), so the ConfigMap mounted fine.

## Verification recipe
```bash
# 1. Zero ready endpoints for the Service the gateway calls -> gateway 5xx
kubectl get endpointslice -n orders -l kubernetes.io/service-name=orders-api -o wide
kubectl get endpoints orders-api -n orders

# 2. The probe port vs the port actually served, side by side
kubectl get deploy orders-api -n orders \
  -o jsonpath='probe={.spec.template.spec.containers[0].readinessProbe.httpGet.port} containerPort={.spec.template.spec.containers[0].ports[0].containerPort}{"\n"}'

# 3. Prove 8080 answers and 8081 refuses from inside the pod's netns
kubectl exec -n orders orders-api-7cc5bcf4c7-lst42 -c api -- wget -qO- -T2 http://127.0.0.1:8080/ ; echo "8080 rc=$?"
kubectl exec -n orders orders-api-7cc5bcf4c7-lst42 -c api -- wget -qO- -T2 http://127.0.0.1:8081/ ; echo "8081 rc=$?"
```
Expected: step 1 shows `<none>`/no ready addresses; step 2 prints `probe=8081 containerPort=8080`; step 3 succeeds on 8080 and fails with connection refused on 8081.

**Remediation:** patch the Deployment's readiness probe to port 8080 (or the named port `http`), which is the canonical fix:
```bash
kubectl patch deploy orders-api -n orders --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/port","value":"http"}]'
kubectl rollout status deploy/orders-api -n orders
```
Pods become `1/1 Ready`, endpoints populate, and gateway 5xx clears. If an immediate rollback is preferred, note there is no prior good revision (`deployment.kubernetes.io/revision: 1`, `OldReplicaSets: <none>`), so `kubectl rollout undo` will not help — the forward patch is the only route. Follow-up: add a pre-merge check that the readiness probe port matches a declared `containerPort`, and alert on Deployment `Available=False`/empty Service endpoints so this is caught before the gateway 5xx monitor fires.

```json
{
  "case_id": "t2-readiness-wrong-port",
  "failing_resource": {"kind": "Deployment", "namespace": "orders", "name": "orders-api"},
  "mechanism": "The orders-api Deployment's pod template defines a readiness probe against port 8081 while the container actually listens on 8080, so every probe is refused and both replicas never become Ready. With no ready pods, the orders-api Service has zero endpoints, so requests to the orders backend cannot be routed and fail, producing the 5xx seen at checkout.",
  "verdict": "confirmed"
}
```