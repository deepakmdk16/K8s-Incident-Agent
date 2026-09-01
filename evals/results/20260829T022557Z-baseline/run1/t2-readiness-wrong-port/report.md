## Root cause

**Deployment `orders/orders-api`** ships a readiness probe pointed at the wrong port. The container serves HTTP on **:8080** (the declared container port and the port the app logs), but the readiness probe is `http-get http://:8081/`. Nothing listens on 8081, so every probe gets `connection refused`, both pods stay `Ready: False` forever, the `orders-api` Service therefore has **zero ready endpoints**, and the checkout gateway's calls to the orders backend never reach a backend — surfacing as 5xx at checkout. Verdict: **confirmed**.

## Evidence chain

- **Probe target vs. listening port (the mechanism).**
  - `describe deployment.apps/orders-api -n orders` pod template: `Port: 8080/TCP (http)` and `Readiness: http-get http://:8081/ delay=0s timeout=1s period=5s successThreshold=1 failureThreshold=3`. The probe port ≠ the served port.
  - `kubectl logs orders-api-7cc5bcf4c7-lst42 -c api`: log line `orders-api: serving on :8080` — the process binds 8080 only. Same for the second pod: `orders-api: serving on :8080`.
- **The probe is actually failing, with the exact refusal that "nothing is listening" produces.**
  - `describe pod/orders-api-7cc5bcf4c7-lst42`: `Warning Unhealthy 5s (x4 over 15s) ... Readiness probe failed: Get "http://10.244.0.122:8081/": dial tcp 10.244.0.122:8081: connect: connection refused`.
  - Identical on the other replica: `... Get "http://10.244.0.123:8081/": dial tcp 10.244.0.123:8081: connect: connection refused`.
- **The container itself is healthy — it is only the readiness gate that is red.**
  - Both pods: `Status: Running`, `State: Running`, `Restart Count: 0`, but `Ready: False`, `ContainersReady False`, `Ready False`. Image pull fine: `Container image "busybox:1.36" already present on machine`.
- **Result: no ready backends behind the Service.**
  - `kubectl get all -A`: `pod/orders-api-...-lst42 0/1 Running`, `pod/orders-api-...-pcspl 0/1 Running`; `deployment.apps/orders-api 0/2 ... 0 AVAILABLE`; `replicaset.apps/orders-api-7cc5bcf4c7 DESIRED 2 / CURRENT 2 / READY 0`.
  - `describe deployment.apps/orders-api`: `Replicas: 2 desired | 2 updated | 2 total | 0 available | 2 unavailable`, `Available False MinimumReplicasUnavailable`.
  - `service/orders-api ClusterIP 10.96.113.141 ... 8080/TCP ... SELECTOR app=orders-api` — the Service selects `app=orders-api`, and both matching pods carry `Labels: app=orders-api` but are not Ready, so kube-proxy programs no endpoints. Traffic to the ClusterIP has nowhere to go.
- **Timeline matches the page.** The Deployment/ReplicaSet/Service are `AGE 5s` in this snapshot with a single revision (`deployment.kubernetes.io/revision: 1`, `OldReplicaSets: <none>`), i.e. this is the freshly released orders-api the page references — the release itself introduced the state.
- **The caller is up and is the reporter, not the victim of its own bug.** `pod/checkout-gateway-7b867bfc46-fgfqx 1/1 Running`, `deployment.apps/checkout-gateway 1/1`.

## Investigation ledger

- **Application crash / CrashLoopBackOff in orders-api** — ruled out: both pods show `State: Running`, `Restart Count: 0`, and no `BackOff`/`Error` events; the app even logged a successful bind (`orders-api: serving on :8080`).
- **Image pull failure or bad image tag in the release** — ruled out: `Normal Pulled ... Container image "busybox:1.36" already present on machine`, `Created`, `Started` on both pods; no `ErrImagePull`/`ImagePullBackOff` anywhere in `get all -A`.
- **Scheduling / capacity / node pressure** — ruled out: `Normal Scheduled Successfully assigned orders/... to incident-lab-control-plane`, `PodScheduled True`, no `FailedScheduling`, and all `kube-system` pods are `1/1 Running` on the same node.
- **Missing ConfigMap `orders-api-scripts` blocking startup** — ruled out: volume is `Optional: false` yet `Initialized True`, `PodReadyToStartContainers True`, no `FailedMount` event, and the script clearly ran (it produced the serving log line).
- **Service selector / label mismatch (Service picks no pods at all)** — ruled out as the *cause*: `service/orders-api ... SELECTOR app=orders-api` exactly matches the pods' `Labels: app=orders-api`. The endpoint set is empty because of readiness, not selection.
- **Service port/targetPort misconfiguration** — ruled out as the cause: the Service exposes `8080/TCP`, which is the port the app actually serves and the port declared on the container. Even a correct targetPort yields no endpoints while pods are un-Ready.
- **Fault in the checkout-gateway deployment itself** — ruled out: it is `1/1 Running` with `READY 1`, no restarts; it is the alert source reporting an upstream that never completes.
- **DNS / cluster networking breakage** — ruled out: `coredns` `2/2` available, `kindnet` and `kube-proxy` DaemonSets `1/1` ready, control plane pods all `1/1 Running` for 10h. The failure is scoped to one workload released 30 minutes ago.
- **Liveness probe killing the container** — ruled out: no liveness probe is defined in the pod template; only `Readiness:` appears, and restart count is 0.

## Verification recipe

```bash
# 1. Confirm the Service has zero ready endpoints (why the gateway gets nothing back).
kubectl get endpointslice -n orders -l kubernetes.io/service-name=orders-api -o yaml
kubectl get endpoints orders-api -n orders

# 2. Confirm the app answers on 8080 and refuses 8081 from inside the pod.
kubectl exec -n orders orders-api-7cc5bcf4c7-lst42 -c api -- wget -qO- --timeout=2 http://127.0.0.1:8080/ ; echo "8080 rc=$?"
kubectl exec -n orders orders-api-7cc5bcf4c7-lst42 -c api -- wget -qO- --timeout=2 http://127.0.0.1:8081/ ; echo "8081 rc=$?"

# 3. Show the probe port in the workload spec that must change.
kubectl get deploy orders-api -n orders -o jsonpath='{.spec.template.spec.containers[0].readinessProbe.httpGet.port}{"\n"}{.spec.template.spec.containers[0].ports[*].containerPort}{"\n"}'
```

Expected: empty/`<none>` endpoints; `8080` returns a response while `8081` fails with connection refused; probe port prints `8081` against container port `8080`.

**Remediation:** patch the Deployment's readiness probe to the served port (`kubectl -n orders patch deploy orders-api --type=json -p '[{"op":"replace","path":"/spec/template/spec/containers/0/readinessProbe/httpGet/port","value":8080}]'`, or `"value":"http"` to use the named port). Pods become Ready, endpoints populate, checkout 5xx clears. If the 8081 target was intentional (a separate admin/health listener), the fix instead is to make the app bind 8081 — but the current image demonstrably does not. Immediate mitigation if a prior good revision existed: `kubectl -n orders rollout undo deploy/orders-api` — note this snapshot shows `revision: 1` with `OldReplicaSets: <none>`, so roll-forward with the patch is the actionable path. Follow-up: add a readiness gate to the release pipeline (`kubectl rollout status --timeout`) so a Deployment that never reaches Available fails the deploy instead of draining the Service.

```json
{
  "case_id": "t2-readiness-wrong-port",
  "failing_resource": {"kind": "Deployment", "namespace": "orders", "name": "orders-api"},
  "mechanism": "The orders-api Deployment defines its readiness probe as http-get on port 8081 while the container serves only on port 8080, so every probe is refused and both replicas remain not-Ready. Kubernetes therefore keeps them out of the orders-api Service endpoints, leaving the ClusterIP with no backends. Calls from the checkout gateway to the orders backend never complete, producing the 5xx at order submission.",
  "verdict": "confirmed"
}
```