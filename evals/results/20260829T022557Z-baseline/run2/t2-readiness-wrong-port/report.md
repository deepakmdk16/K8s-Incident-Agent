## Root cause

**Verdict: confirmed.**

The `orders-api` Deployment (namespace `orders`) ships a readiness probe pointed at the wrong port. The container serves HTTP on **:8080** (the declared container port), but the readiness probe is `http-get http://:8081/`. Nothing listens on 8081, so every probe gets `connection refused`, both replicas stay `Ready: False` forever, and the `orders-api` Service therefore has **zero ready endpoints**. The checkout gateway's calls to `orders-api:8080` have nowhere to be routed, so they never complete and the gateway returns 5xx at checkout. The application itself is healthy — only the probe's port is wrong.

## Evidence chain

- **Probe targets 8081, app listens on 8080** — from `describe deployment.apps/orders-api -n orders`:
  - `Port: 8080/TCP (http)`
  - `Readiness: http-get http://:8081/ delay=0s timeout=1s period=5s successThreshold=1 failureThreshold=3`
  Same mismatch is reproduced verbatim in `describe replicaset.apps/orders-api-7cc5bcf4c7` and in both pod describes, so it comes from the Deployment's pod template, not from a hand-edited pod.
- **The app is actually up and serving on 8080** — log line from both pods: `orders-api: serving on :8080` (`kubectl logs orders-api-7cc5bcf4c7-lst42 -c api` and `...-pcspl -c api`). No crash, no error, no stack trace after that line.
- **Nothing is listening on 8081** — event in `describe pod/orders-api-7cc5bcf4c7-lst42`: `Warning Unhealthy ... Readiness probe failed: Get "http://10.244.0.122:8081/": dial tcp 10.244.0.122:8081: connect: connection refused`. Identical event on `...-pcspl` for `10.244.0.123:8081`. `connection refused` (not timeout) proves the port is closed, i.e. the probe is aimed at a port the process never bound.
- **Containers are running, just not ready** — both pod describes: `State: Running`, `Ready: False`, `Restart Count: 0`; conditions `Ready False` / `ContainersReady False`. Nothing is restarting or being killed, so this is a probe verdict, not an application failure.
- **This makes the Service endpoint-less** — `kubectl get all -A` shows `service/orders-api ClusterIP 10.96.113.141 ... 8080/TCP ... SELECTOR app=orders-api`, and both pods matching `app=orders-api` are `0/1` READY. kube-proxy only programs *ready* endpoints, so the ClusterIP has no backends.
- **Deployment-level blast radius matches the page** — `describe deployment.apps/orders-api`: `Replicas: 2 desired | 2 updated | 2 total | 0 available | 2 unavailable`, `Available False MinimumReplicasUnavailable`. All shoppers are affected because *all* backends are unready, matching "5xx for all shoppers".
- **Timing matches the release** — deployment, replicaset, service and pods all `AGE 5s` with `deployment.kubernetes.io/revision: 1` and `OldReplicaSets: <none>`; the page says the release was ~30 min ago. The symptom began with this rollout and there is no prior good ReplicaSet still serving traffic.

## Investigation ledger

- **Application crash / bad image / CrashLoopBackOff** — ruled out: both pods are `State: Running` with `Restart Count: 0`, image `busybox:1.36` was `already present on machine`, and the log shows a clean `orders-api: serving on :8080` startup line with no errors.
- **Image pull failure** — ruled out: `Normal Pulled ... Container image "busybox:1.36" already present on machine and can be accessed by the pod` on both pods; status is `Running`, not `ImagePullBackOff`.
- **Missing ConfigMap `orders-api-scripts` (volume mount failure)** — ruled out: volume is `Optional: false`, yet both pods reached `Initialized True` / `PodReadyToStartContainers True` and the container executed `/app/run.sh` successfully enough to log its startup banner. No `FailedMount` events.
- **Service selector mismatch (Service pointing at nothing)** — ruled out: `service/orders-api` selector is `app=orders-api` and both pods carry `Labels: app=orders-api`. The selector matches; the pods simply are not *ready*, which is the distinct failure. Service port `8080/TCP` also matches the container's serving port.
- **Checkout gateway itself broken** — ruled out as the cause: `pod/checkout-gateway-7b867bfc46-fgfqx 1/1 Running 0 restarts` and `deployment.apps/checkout-gateway 1/1 AVAILABLE`. The gateway is healthy and is the *reporter* of the failure ("its calls to the orders backend are not completing").
- **Cluster/infra fault (DNS, CNI, node pressure, scheduling)** — ruled out: `coredns` 2/2 Running, `kindnet` and `kube-proxy` daemonsets 1/1 ready, all control-plane pods Running with 0 restarts for 10h; both orders pods were `Successfully assigned` and got pod IPs (`10.244.0.122`, `10.244.0.123`). The probe reached the pod IP and got a TCP `connection refused` — the network path works, the port does not exist.
- **Probe too aggressive (timeout/initialDelay too short for a slow starter)** — ruled out: a slow start yields probe *timeouts* or `i/o timeout`, and would self-resolve as `Age 5s` grew; here it is a hard `connection refused` on every attempt (`x4 over 15s`) while the app has *already* logged that it is serving. Also `delay=0s` against an app that binds immediately is not the discriminator — the port number is.
- **Liveness probe killing the container** — ruled out: no liveness probe is defined in the pod template, and `Restart Count: 0`.

## Verification recipe

```bash
# 1. Confirm the Service has no ready endpoints -> gateway has nowhere to send traffic
kubectl get endpointslice -n orders -l kubernetes.io/service-name=orders-api -o wide
kubectl get endpoints orders-api -n orders          # expect ENDPOINTS: <none>

# 2. Confirm the probe port differs from the port the app actually binds
kubectl get deploy orders-api -n orders -o jsonpath='{.spec.template.spec.containers[0].readinessProbe.httpGet.port}{"  vs containerPort: "}{.spec.template.spec.containers[0].ports[0].containerPort}{"\n"}'

# 3. Prove 8080 answers and 8081 refuses, from inside the pod's netns
kubectl exec -n orders orders-api-7cc5bcf4c7-lst42 -c api -- wget -qO- --timeout=2 http://127.0.0.1:8080/ ; echo "8080 rc=$?"
kubectl exec -n orders orders-api-7cc5bcf4c7-lst42 -c api -- wget -qO- --timeout=2 http://127.0.0.1:8081/ ; echo "8081 rc=$?"
```

Expected: empty endpoint list; `8081 vs containerPort: 8080`; 8080 returns a response while 8081 fails to connect.

**Remediation:** point the readiness probe at the port the app serves on.

```bash
kubectl patch deploy orders-api -n orders --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/port","value":8080}]'
kubectl rollout status deploy/orders-api -n orders
```

Better: use the named port (`port: http`) so the probe can never drift from `containerPort` again, and add a rollout gate (`minReadySeconds` plus a CI check that every probe port matches a declared containerPort) so a release with zero ready replicas cannot reach production. Since revision 1 is the only revision, `kubectl rollout undo` is not available here — fix forward.

```json
{
  "case_id": "t2-readiness-wrong-port",
  "failing_resource": {"kind": "Deployment", "namespace": "orders", "name": "orders-api"},
  "mechanism": "The orders-api Deployment's pod template defines a readiness probe against http://:8081/ while the container actually binds only port 8080, so every probe returns 'connection refused' and both replicas remain Ready=False. Because no pod is ready, the orders-api Service has zero endpoints and inbound order-placement requests to it are never routed to a backend, surfacing as 5xx at checkout.",
  "verdict": "confirmed"
}
```